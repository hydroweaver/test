import hashlib
import hmac
import os
import secrets
import time

from dotenv import load_dotenv
from fastapi import APIRouter, BackgroundTasks, Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel
from twilio.request_validator import RequestValidator
from twilio.rest import Client as TwilioClient
from twilio.twiml.messaging_response import MessagingResponse

import crm
import db
import media
from agent import run_agent
from pricing import calculate_cost, load_pricing, lookup_rates
from providers import PROVIDERS, ProviderError, clear_cache, list_models

load_dotenv()

app = FastAPI(title="WhatsApp concierge + multi-model token/cost tracker")

DEFAULT_MODELS = {
    "openai": os.environ.get("OPENAI_DEFAULT_MODEL", "gpt-5"),
    "gemini": os.environ.get("GEMINI_DEFAULT_MODEL", "gemini-3-flash"),
}
COMPANY_NAME = os.environ.get("COMPANY_NAME", "the company")


def _load_system_prompt() -> str:
    """The bot's instructions live in system_prompt.txt so they can be edited (and
    resized, to see the cost impact) without touching code. Lines starting with
    '> ' are editor notes and are stripped before sending."""
    path = os.path.join(os.path.dirname(__file__), "system_prompt.txt")
    try:
        with open(path) as f:
            raw = f.read()
    except FileNotFoundError:
        return f"You are the WhatsApp concierge for {COMPANY_NAME}. Be brief and helpful."
    body = "\n".join(l for l in raw.splitlines() if not l.startswith("> ") and l.strip() != ">")
    return body.strip().replace("{company}", COMPANY_NAME)


WHATSAPP_SYSTEM_PROMPT = _load_system_prompt()

TWILIO_WHATSAPP_NUMBER = os.environ.get("TWILIO_WHATSAPP_NUMBER", "")


@app.on_event("startup")
def _startup():
    first_provider = next(iter(DEFAULT_MODELS))
    db.init_db(first_provider, DEFAULT_MODELS[first_provider])
    crm.seed()
    # Replies go out over Twilio's API, so these are required. Say it at boot rather
    # than letting every exchange fail one at a time in the usage log.
    missing = _missing_send_config()
    if missing:
        print(f"WARNING: WhatsApp replies cannot be sent - {'; '.join(missing)}")


# ---------------------------------------------------------------------------
# Chat / status endpoints
# ---------------------------------------------------------------------------

class ChatRequest(BaseModel):
    provider: str
    message: str
    model: str | None = None
    system: str | None = None


class TokenUsage(BaseModel):
    input_tokens: int
    output_tokens: int
    total_tokens: int


class ChatResponse(BaseModel):
    provider: str
    model: str
    reply: str
    usage: TokenUsage
    cost_usd: dict | None
    tool_call_count: int
    turn_count: int
    pricing_note: str


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/models")
def models():
    pricing = load_pricing()
    return {
        provider: {
            **db.key_status(provider),
            "default_model": DEFAULT_MODELS[provider],
            **list_models(provider),
            "priced_models": list(pricing.get(provider, {}).keys()),
        }
        for provider in PROVIDERS
    }


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest):
    if req.provider not in PROVIDERS:
        raise HTTPException(status_code=400, detail=f"Unknown provider '{req.provider}'. Choose one of: {list(PROVIDERS)}")

    model = req.model or DEFAULT_MODELS[req.provider]
    start = time.time()
    try:
        result = run_agent(req.provider, req.message, model, req.system, history=[])
    except ProviderError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        # Any failure from the underlying provider SDK (bad key, rate limit, network, etc.)
        raise HTTPException(status_code=502, detail=f"{req.provider} request failed: {e}")
    latency_ms = int((time.time() - start) * 1000)

    cost = calculate_cost(
        req.provider, model, result.input_tokens, result.output_tokens,
        result.cache_read_tokens, result.cache_write_tokens,
    )
    db.log_usage(
        channel="api", provider=req.provider, model=model,
        input_tokens=result.input_tokens, output_tokens=result.output_tokens,
        cost_usd=cost["total_usd"] if cost else None,
        tool_call_count=result.tool_call_count, turn_count=result.turn_count,
        latency_ms=latency_ms, user_message=req.message, reply_text=result.reply,
        media_kind="text",
        cache_read_tokens=result.cache_read_tokens, cache_write_tokens=result.cache_write_tokens,
    )

    pricing_note = (
        "Cost calculated from backend/pricing.json - verify against the provider's current pricing page."
        if cost is not None
        else f"No pricing entry for '{model}' in pricing.json - token counts are exact, cost could not be computed."
    )
    return ChatResponse(
        provider=req.provider, model=model, reply=result.reply,
        usage=TokenUsage(
            input_tokens=result.input_tokens, output_tokens=result.output_tokens,
            total_tokens=result.input_tokens + result.output_tokens,
        ),
        cost_usd=cost, tool_call_count=result.tool_call_count, turn_count=result.turn_count,
        pricing_note=pricing_note,
    )


# ---------------------------------------------------------------------------
# Twilio WhatsApp webhook
# ---------------------------------------------------------------------------

@app.get("/media/{filename}")
def serve_media(filename: str):
    """Public (unauthenticated) - Twilio fetches reply media over plain HTTP."""
    if "/" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="Bad filename")
    path = os.path.join(media.MEDIA_DIR, filename)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(path)


def twilio_auth() -> tuple[str, str, str] | None:
    """The (username, password, account_sid) to authenticate to Twilio with.

    Twilio issues two kinds of credentials and they are NOT interchangeable:
      - Account SID (AC...) + Auth Token, the account-level pair;
      - API Key SID (SK...) + Secret, which is what the console hands you when you
        create an API key - and which still needs the AC... account SID alongside it,
        because that's what identifies the account in the request URL.
    Supporting only the first meant an API key could never work.
    """
    account_sid = os.environ.get("TWILIO_ACCOUNT_SID", "")
    key_sid = os.environ.get("TWILIO_API_KEY_SID", "")
    key_secret = os.environ.get("TWILIO_API_KEY_SECRET", "")
    auth_token = os.environ.get("TWILIO_AUTH_TOKEN", "")

    if key_sid and key_secret and account_sid:
        return key_sid, key_secret, account_sid
    if account_sid and auth_token:
        return account_sid, auth_token, account_sid
    return None


def twilio_client() -> TwilioClient:
    auth = twilio_auth()
    if not auth:
        raise RuntimeError(f"Twilio credentials incomplete: {', '.join(_missing_send_config())}")
    username, password, account_sid = auth
    return TwilioClient(username, password, account_sid)


def _missing_send_config() -> list[str]:
    """What's missing before replies can be sent through Twilio's API."""
    account_sid = os.environ.get("TWILIO_ACCOUNT_SID", "")
    key_sid = os.environ.get("TWILIO_API_KEY_SID", "")
    key_secret = os.environ.get("TWILIO_API_KEY_SECRET", "")
    auth_token = os.environ.get("TWILIO_AUTH_TOKEN", "")

    missing = []
    if not account_sid:
        missing.append("TWILIO_ACCOUNT_SID (the AC... one)")
    elif not account_sid.startswith("AC"):
        # An API key SID pasted in here silently addresses a non-existent account.
        missing.append(f"TWILIO_ACCOUNT_SID must be the AC... account SID, not '{account_sid[:4]}...'")
    if not ((key_sid and key_secret) or auth_token):
        missing.append("TWILIO_AUTH_TOKEN, or TWILIO_API_KEY_SID + TWILIO_API_KEY_SECRET")
    elif key_sid and not key_secret:
        missing.append("TWILIO_API_KEY_SECRET")
    elif key_secret and not key_sid:
        missing.append("TWILIO_API_KEY_SID")
    if not TWILIO_WHATSAPP_NUMBER:
        missing.append("TWILIO_WHATSAPP_NUMBER")
    return missing


WHATSAPP_MAX_CHARS = 1500  # Twilio rejects WhatsApp bodies over 1600

# The handful of Twilio error codes that actually explain a silently undelivered
# WhatsApp reply, in plain English.
TWILIO_ERROR_HINTS = {
    20003: "Authentication failed - check TWILIO_AUTH_TOKEN, or the API key pair "
           "(TWILIO_API_KEY_SID + TWILIO_API_KEY_SECRET) against TWILIO_ACCOUNT_SID.",
    21606: "TWILIO_WHATSAPP_NUMBER isn't a WhatsApp-enabled sender on this account.",
    21608: "Number not permitted to receive from this sender (a trial account, or an "
           "unjoined sandbox).",
    21610: "That number unsubscribed (replied STOP).",
    63007: "No WhatsApp sender found for TWILIO_WHATSAPP_NUMBER on this account.",
    # The big one on a live WhatsApp Business number. A bot can only reply freely
    # inside 24h of the customer's own last message; outside it, only an approved
    # template goes through - which this app deliberately doesn't send.
    63016: "Outside the 24-hour customer service window. A live WhatsApp Business "
           "number can only send free-form messages within 24h of the customer's last "
           "message - outside that, only an approved template is allowed. Message the "
           "bot from that phone first, then reply within 24h.",
    131047: "Outside the 24-hour window (Meta's code for the same thing) - the customer "
            "must message you first, then you have 24h to reply freely.",
    131026: "WhatsApp couldn't deliver it - the number isn't on WhatsApp, or can't "
            "receive from this business.",
    63015: "WhatsApp couldn't deliver it - the number may not be on WhatsApp.",
    63024: "Twilio rejected the message body (empty, too long, or bad media URL).",
    63003: "Twilio couldn't find that recipient on WhatsApp.",
    63018: "Rate limited by WhatsApp - too many messages too quickly.",
}


def _send_whatsapp(to_number: str, body: str, media_files: list[str]) -> str:
    """Sends the reply, returning the Twilio message SIDs so a successful send is
    provable. Long replies are split - Twilio rejects the whole message otherwise,
    which is how a reply ends up in the log but never on the phone."""
    missing = _missing_send_config()
    if missing:
        raise RuntimeError(f"Can't send reply - these env vars are not set: {', '.join(missing)}")

    body = (body or "").strip()
    if not body and not media_files:
        # An empty body is rejected by Twilio, so the customer would get silence.
        body = "Sorry, I couldn't put together an answer for that. Try rephrasing? 🙏"

    chunks = [body[i:i + WHATSAPP_MAX_CHARS] for i in range(0, len(body), WHATSAPP_MAX_CHARS)] or [""]
    client = twilio_client()
    urls = [u for u in (media.public_url(f) for f in media_files) if u]
    # Twilio accepting a WhatsApp message only means it queued. Delivery can still
    # fail seconds later (expired sandbox join, outside the 24h window) and that
    # verdict only ever arrives here, on a status callback.
    # Falls back to the host the last webhook came in on, so delivery verdicts work
    # even when PUBLIC_BASE_URL was never set.
    base = media.base_url()
    status_callback = f"{base}/webhook/twilio-status" if base else None
    sids = []
    for i, chunk in enumerate(chunks):
        msg = client.messages.create(
            from_=f"whatsapp:{TWILIO_WHATSAPP_NUMBER.removeprefix('whatsapp:')}",
            to=f"whatsapp:{to_number}",
            body=chunk,
            media_url=(urls or None) if i == len(chunks) - 1 else None,  # media on the last part
            **({"status_callback": status_callback} if status_callback else {}),
        )
        sids.append(msg.sid)
    return ",".join(sids)


@app.post("/webhook/twilio-status")
async def twilio_status_webhook(request: Request):
    """Twilio reports each message's final state here (delivered / undelivered /
    failed, with an error code). Recorded against the row that sent it, so the log
    shows what actually happened rather than only what we attempted."""
    form = await request.form()
    sid = form.get("MessageSid", "")
    status = form.get("MessageStatus", "")
    code = form.get("ErrorCode", "")
    if not sid:
        return Response(status_code=204)

    note = f"delivery: {status}"
    if code:
        try:
            hint = TWILIO_ERROR_HINTS.get(int(code), "")
        except ValueError:
            hint = ""
        note += f" (Twilio error {code}{' - ' + hint if hint else ''})"
    if status in ("failed", "undelivered"):
        print(f"WhatsApp NOT delivered [{sid}]: {note}")
    await run_in_threadpool(db.note_delivery, sid, note)
    return Response(status_code=204)


def _handle_message(
    from_number: str, body: str, image: dict | None, media_kind: str,
    note: str | None = None,
) -> str:
    """Runs the agent, logs the exchange, and sends the reply via Twilio's API.
    Always called in the background, so a slow exchange (transcription + tool calls +
    speech) can take as long as it needs - the webhook has already been answered."""
    conversation_id = db.get_or_create_conversation(from_number)
    history = db.get_recent_messages(conversation_id, limit=6)
    settings = db.get_settings()
    provider, model = settings["active_provider"], settings["active_model"]

    start = time.time()
    result = None
    error = note
    try:
        result = run_agent(
            provider, body, model, WHATSAPP_SYSTEM_PROMPT, history,
            image=image, caller_phone=from_number,
        )
        reply_text = result.reply
    except Exception as e:
        reply_text = "Sorry, I'm temporarily unavailable. Please try again shortly. 🙏"
        # Append, don't replace: `note` carries how the reply is being delivered and
        # any media failure, which is often the more useful half of the diagnosis.
        error = f"{error} | {e}" if error else str(e)

    reply_media = list(result.media_files) if result else []
    # Mirror the medium: a voice note gets a voice note back.
    if media_kind == "audio" and result and reply_text:
        try:
            voice_file = media.synthesize_voice_note(reply_text)
            if voice_file:
                reply_media.append(voice_file)
        except Exception as e:
            error = (error + " | " if error else "") + f"TTS failed: {e}"

    latency_ms = int((time.time() - start) * 1000)

    db.add_message(conversation_id, "user", body)
    if result is not None:
        db.add_message(conversation_id, "assistant", reply_text)

    # Send first, then write exactly one usage row for this exchange - one incoming
    # message, its one reply, and every token spent producing it.
    try:
        sids = _send_whatsapp(from_number, reply_text, reply_media)
        # Record the Twilio SIDs so "logged but never arrived" is diagnosable:
        # no SID here means we never handed it to Twilio at all.
        error = (error + " | " if error else "") + f"sent ✓ {sids}"
    except Exception as e:
        error = (error + " | " if error else "") + f"send failed: {e}"
        print(f"send failed for {from_number}: {e}")

    cost = calculate_cost(
        provider, model, result.input_tokens, result.output_tokens,
        result.cache_read_tokens, result.cache_write_tokens,
    ) if result else None
    try:
        db.log_usage(
            channel="whatsapp", provider=provider, model=model,
            input_tokens=result.input_tokens if result else 0,
            output_tokens=result.output_tokens if result else 0,
            cost_usd=cost["total_usd"] if cost else None,
            tool_call_count=result.tool_call_count if result else 0,
            turn_count=result.turn_count if result else 0,
            phone_number=from_number, latency_ms=latency_ms, error=error,
            user_message=body, reply_text=reply_text, media_kind=media_kind,
            cache_read_tokens=result.cache_read_tokens if result else 0,
            cache_write_tokens=result.cache_write_tokens if result else 0,
        )
    except Exception:
        pass  # a logging failure should never block the reply

    return reply_text


@app.post("/webhook/whatsapp")
async def whatsapp_webhook(request: Request, background: BackgroundTasks):
    form = await request.form()
    auth_token = os.environ.get("TWILIO_AUTH_TOKEN")
    public_base_url = os.environ.get("PUBLIC_BASE_URL")
    # Signature checking is OFF by default - it depends on the exact URL, so renaming
    # the app silently 403s everything. Set VERIFY_TWILIO_SIGNATURE=1 to turn it on.
    if os.environ.get("VERIFY_TWILIO_SIGNATURE") == "1" and auth_token and public_base_url:
        validator = RequestValidator(auth_token)
        url = public_base_url.rstrip("/") + "/webhook/whatsapp"
        if not validator.validate(url, dict(form), request.headers.get("X-Twilio-Signature", "")):
            raise HTTPException(status_code=403, detail=f"Bad Twilio signature (checked against {url})")

    host = request.headers.get("x-forwarded-host") or request.headers.get("host")
    if host:
        media.remember_base_url(f"{request.headers.get('x-forwarded-proto', 'https')}://{host}")

    from_number = form.get("From", "").removeprefix("whatsapp:")
    body = form.get("Body", "") or ""
    if not from_number:
        raise HTTPException(status_code=400, detail="Missing From")

    image = None
    media_kind = "text"
    media_error = None
    num_media = int(form.get("NumMedia", "0") or 0)
    if num_media:
        media_url = form.get("MediaUrl0", "")
        content_type = form.get("MediaContentType0", "")
        try:
            # Downloading and transcribing are blocking network calls; running them
            # directly in this async handler would freeze the event loop (and with it
            # every other request) until they finish.
            blob, ctype = await run_in_threadpool(media.download_twilio_media, media_url)
            if content_type.startswith("image/") or ctype.startswith("image/"):
                media_kind = "image"
                image = media.encode_image(blob, ctype)
                body = body or "(the customer sent this image)"
            elif content_type.startswith("audio/") or ctype.startswith("audio/"):
                media_kind = "audio"
                body = await run_in_threadpool(media.transcribe_audio, blob, ctype)
            else:
                media_kind = "unsupported"
                body = body or f"(the customer sent a {ctype} file, which you can't open)"
        except Exception as e:
            media_kind = "error"
            media_error = f"media download failed: {e}"
            body = body or "(the customer sent an attachment that couldn't be processed)"
            print(media_error)

    if not body:
        raise HTTPException(status_code=400, detail="Nothing to reply to")

    # Ack Twilio instantly with an empty response, then answer from a background task
    # via Twilio's API, which has no webhook time limit.
    background.add_task(_handle_message, from_number, body, image, media_kind, media_error)
    return Response(content=str(MessagingResponse()), media_type="application/xml")


# ---------------------------------------------------------------------------
# Admin UI (cookie session)
# ---------------------------------------------------------------------------
# A signed cookie rather than HTTP Basic: Basic makes the browser throw its own
# password dialog, which can't be styled or dismissed, and resends the password on
# every single request. Here the password is sent once, to the login form.

SESSION_COOKIE = "admin_session"
SESSION_TTL_S = 7 * 24 * 3600


def _admin_credentials() -> tuple[str, str]:
    # Defaults to admin/admin if you don't set these - fine for a quick throwaway
    # test, but set your own ADMIN_USERNAME/ADMIN_PASSWORD before sharing the URL.
    return os.environ.get("ADMIN_USERNAME", "admin"), os.environ.get("ADMIN_PASSWORD", "admin")


def _session_secret() -> bytes:
    """Derived from the password unless ADMIN_SESSION_SECRET is set, so changing the
    password automatically invalidates every session already handed out."""
    user, password = _admin_credentials()
    raw = os.environ.get("ADMIN_SESSION_SECRET") or f"{user}:{password}"
    return hashlib.sha256(raw.encode()).digest()


def _sign(expires_at: int) -> str:
    return hmac.new(_session_secret(), str(expires_at).encode(), hashlib.sha256).hexdigest()


def _issue_session() -> str:
    expires_at = int(time.time()) + SESSION_TTL_S
    return f"{expires_at}.{_sign(expires_at)}"


def _session_valid(token: str | None) -> bool:
    if not token or "." not in token:
        return False
    expires_at, _, signature = token.partition(".")
    if not expires_at.isdigit() or not hmac.compare_digest(signature, _sign(int(expires_at))):
        return False
    return int(expires_at) > time.time()


def _logged_in(request: Request) -> bool:
    return _session_valid(request.cookies.get(SESSION_COOKIE))


def require_admin(request: Request):
    """API guard. Returns a plain 401 with NO WWW-Authenticate header - that header is
    the entire reason a browser pops up its own login box."""
    if not _logged_in(request):
        raise HTTPException(status_code=401, detail="Not logged in")
    return True


LOGIN_REDIRECT = "/admin/login"


@app.get("/admin", response_class=HTMLResponse)
def admin_page(request: Request):
    if not _logged_in(request):
        return RedirectResponse(LOGIN_REDIRECT, status_code=303)
    path = os.path.join(os.path.dirname(__file__), "admin", "index.html")
    with open(path) as f:
        return HTMLResponse(f.read())


@app.get("/admin/login", response_class=HTMLResponse)
def admin_login_page(request: Request, error: int = 0):
    if _logged_in(request):
        return RedirectResponse("/admin", status_code=303)
    path = os.path.join(os.path.dirname(__file__), "admin", "login.html")
    with open(path) as f:
        html = f.read()
    return HTMLResponse(html.replace("{{ERROR}}", "Wrong username or password." if error else ""))


@app.post("/admin/login")
async def admin_login(request: Request):
    form = await request.form()
    user, password = _admin_credentials()
    ok = secrets.compare_digest(str(form.get("username", "")), user) & \
        secrets.compare_digest(str(form.get("password", "")), password)
    if not ok:
        return RedirectResponse(f"{LOGIN_REDIRECT}?error=1", status_code=303)

    response = RedirectResponse("/admin", status_code=303)
    response.set_cookie(
        SESSION_COOKIE, _issue_session(), max_age=SESSION_TTL_S, httponly=True, samesite="lax",
        # Railway terminates TLS upstream, so trust the proxy header for this.
        secure=request.headers.get("x-forwarded-proto", request.url.scheme) == "https",
    )
    return response


@app.post("/admin/logout")
def admin_logout():
    response = RedirectResponse(LOGIN_REDIRECT, status_code=303)
    response.delete_cookie(SESSION_COOKIE)
    return response


admin_router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])


@admin_router.get("/settings")
def admin_get_settings():
    pricing = load_pricing()
    return {
        **db.get_settings(),
        "providers": {
            provider: {
                **db.key_status(provider),
                "default_model": DEFAULT_MODELS[provider],
                **list_models(provider),
            "priced_models": list(pricing.get(provider, {}).keys()),
            }
            for provider in PROVIDERS
        },
    }


class SettingsUpdate(BaseModel):
    provider: str
    model: str


@admin_router.get("/models")
def admin_models():
    """Live model lists - separate from /settings because this makes a network call
    to each provider and must never block the page or the key-save button."""
    out = {}
    for provider in PROVIDERS:
        info = list_models(provider)
        # Which of these we can actually cost, accounting for snapshot fallback.
        info["unpriced"] = [m for m in info["models"] if lookup_rates(provider, m) is None]
        out[provider] = info
    return out


@admin_router.post("/settings")
def admin_update_settings(req: SettingsUpdate):
    if req.provider not in PROVIDERS:
        raise HTTPException(status_code=400, detail=f"Unknown provider '{req.provider}'")
    if not db.resolve_api_key(req.provider):
        raise HTTPException(status_code=400, detail=f"No API key configured for '{req.provider}' yet")

    # Reject a model the provider doesn't actually serve, rather than letting every
    # message fail later with a 404 from their API.
    info = list_models(req.provider)
    if info["live"] and req.model not in info["models"]:
        raise HTTPException(
            status_code=400,
            detail=f"'{req.model}' isn't a model {req.provider} offers on this key. "
                   f"Pick one from the list (e.g. {', '.join(info['models'][:3])}).",
        )

    db.update_settings(req.provider, req.model)
    return {"ok": True}


@admin_router.get("/usage")
def admin_usage(limit: int = 50, offset: int = 0):
    return db.get_usage(limit, offset)


class WhatsAppTest(BaseModel):
    to: str


@admin_router.post("/twilio-test")
def admin_twilio_test(req: WhatsAppTest):
    """Sends one test WhatsApp message and reports exactly what Twilio said.

    Delivery failures are otherwise invisible from the outside: the reply is
    generated and logged here while Twilio rejects it for a reason only their API
    response carries. This surfaces that reason (and their error code, which is the
    actual diagnosis) instead of leaving you guessing.
    """
    missing = _missing_send_config()
    if missing:
        return {"ok": False, "stage": "config",
                "error": f"Not set in the environment: {', '.join(missing)}",
                "hint": "Add them in Railway, then redeploy - env changes need a new deploy."}

    to = req.to.strip()
    if not to.startswith("+"):
        return {"ok": False, "stage": "input",
                "error": f"'{to}' needs to be in full international format, e.g. +32477874767."}

    try:
        sid = _send_whatsapp(to, "Test message from your admin page ✅", [])
    except Exception as e:
        code = getattr(e, "code", None)
        return {"ok": False, "stage": "twilio", "error": str(e),
                "twilio_code": code, "hint": TWILIO_ERROR_HINTS.get(code, "")}

    # Accepting is not delivering. WhatsApp messages routinely queue fine and then
    # fail a few seconds later, so wait for the verdict instead of reporting the
    # send as a success and leaving you to wonder why nothing arrived.
    client = twilio_client()
    status, code = "queued", None
    for _ in range(6):
        time.sleep(1.5)
        msg = client.messages(sid.split(",")[0]).fetch()
        status, code = msg.status, msg.error_code
        if status in ("delivered", "read", "failed", "undelivered"):
            break

    delivered = status in ("delivered", "read")
    hint = TWILIO_ERROR_HINTS.get(code, "") if code else ""
    if not delivered and not hint:
        hint = ("Twilio took it but WhatsApp hasn't confirmed delivery yet. The usual "
                "cause is the 24-hour window: message the bot from that phone, then "
                "test again within 24h."
                if status in ("queued", "sent", "accepted")
                else "Check the message in Twilio's console for the full reason.")
    return {"ok": delivered, "stage": "delivery", "message_sid": sid,
            "status": status, "twilio_code": code, "hint": hint}


app.include_router(admin_router)
