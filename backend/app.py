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
    # Replies go out over Twilio's API only. Say so at boot rather than letting every
    # exchange fail one at a time in the usage log.
    missing = _missing_send_config()
    if missing:
        print(f"WARNING: WhatsApp replies cannot be sent - not set: {', '.join(missing)}")


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


def _missing_send_config() -> list[str]:
    """Which env vars are missing for sending replies via Twilio's API."""
    return [
        name for name, value in (
            ("TWILIO_ACCOUNT_SID", os.environ.get("TWILIO_ACCOUNT_SID")),
            ("TWILIO_AUTH_TOKEN", os.environ.get("TWILIO_AUTH_TOKEN")),
            ("TWILIO_WHATSAPP_NUMBER", TWILIO_WHATSAPP_NUMBER),
        ) if not value
    ]


WHATSAPP_MAX_CHARS = 1500  # Twilio rejects WhatsApp bodies over 1600


def _send_whatsapp(to_number: str, body: str, media_files: list[str]) -> str:
    """Sends the reply, returning the Twilio message SIDs so a successful send is
    provable. Long replies are split - Twilio rejects the whole message otherwise,
    which is how a reply ends up in the log but never on the phone."""
    sid = os.environ.get("TWILIO_ACCOUNT_SID")
    token = os.environ.get("TWILIO_AUTH_TOKEN")
    missing = _missing_send_config()
    if missing:
        raise RuntimeError(f"Can't send reply - these env vars are not set: {', '.join(missing)}")

    body = (body or "").strip()
    if not body and not media_files:
        # An empty body is rejected by Twilio, so the customer would get silence.
        body = "Sorry, I couldn't put together an answer for that. Try rephrasing? 🙏"

    chunks = [body[i:i + WHATSAPP_MAX_CHARS] for i in range(0, len(body), WHATSAPP_MAX_CHARS)] or [""]
    client = TwilioClient(sid, token)
    urls = [u for u in (media.public_url(f) for f in media_files) if u]
    sids = []
    for i, chunk in enumerate(chunks):
        msg = client.messages.create(
            from_=f"whatsapp:{TWILIO_WHATSAPP_NUMBER.removeprefix('whatsapp:')}",
            to=f"whatsapp:{to_number}",
            body=chunk,
            media_url=(urls or None) if i == len(chunks) - 1 else None,  # media on the last part
        )
        sids.append(msg.sid)
    return ",".join(sids)


def _handle_message(
    from_number: str, body: str, image: dict | None, media_kind: str,
    note: str | None = None,
) -> str:
    """Runs the agent, logs the exchange, and delivers the reply via Twilio's API.
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

    # One delivery path: ack Twilio instantly with an empty response, then answer via
    # Twilio's API from a background task, which has no time limit. (Replying inline
    # in this response would need no credentials, but Twilio drops any webhook that
    # takes over ~15s - so a slow model's reply would be billed and never delivered.)
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


app.include_router(admin_router)
