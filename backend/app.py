import os
import secrets
import time

from dotenv import load_dotenv
from fastapi import APIRouter, BackgroundTasks, Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel
from twilio.request_validator import RequestValidator
from twilio.rest import Client as TwilioClient
from twilio.twiml.messaging_response import MessagingResponse

import crm
import db
import media
from agent import run_agent
from pricing import calculate_cost, load_pricing
from providers import PROVIDERS, ProviderError

load_dotenv()

app = FastAPI(title="WhatsApp concierge + multi-model token/cost tracker")

DEFAULT_MODELS = {
    "anthropic": os.environ.get("ANTHROPIC_DEFAULT_MODEL", "claude-opus-5"),
    "openai": os.environ.get("OPENAI_DEFAULT_MODEL", "gpt-5"),
    "gemini": os.environ.get("GEMINI_DEFAULT_MODEL", "gemini-3-flash"),
}
COMPANY_NAME = os.environ.get("COMPANY_NAME", "the company")
WHATSAPP_SYSTEM_PROMPT = (
    f"You're the WhatsApp concierge for {COMPANY_NAME}. You're warm, quick and genuinely "
    "useful - like a sharp colleague texting back, not a corporate FAQ bot.\n\n"
    "Style rules:\n"
    "- Keep it SHORT. One to three sentences typically. This is WhatsApp, not email.\n"
    "- Use emojis naturally to add warmth and structure (👋 ✅ 📦 💬 ⚡) - a few per message, "
    "never a wall of them, and never in a way that obscures the actual answer.\n"
    "- Use *bold* (single asterisks - WhatsApp formatting) for key facts like prices, order "
    "numbers and statuses.\n"
    "- For choices, use a short numbered list with number emojis (1️⃣ 2️⃣ 3️⃣) so they can just "
    "reply with a number.\n"
    "- No greetings on every message - only the first one. Don't sign off with your name.\n\n"
    "Tools:\n"
    "- Check who you're talking to with lookup_customer, and greet returning customers by name.\n"
    "- Use the CRM tools (list_my_orders, get_order_status, list_products, create_ticket) for "
    "anything about their account, orders or products - never invent order numbers, prices or statuses.\n"
    "- Use search_website for general questions about the company.\n"
    "- Use send_product_image when showing a product would genuinely help.\n"
    "- If someone reports a problem you can't solve, log it with create_ticket and give them the number.\n\n"
    "If a tool returns nothing useful, say so honestly and offer a next step - never make facts up."
)

TWILIO_WHATSAPP_NUMBER = os.environ.get("TWILIO_WHATSAPP_NUMBER", "")


@app.on_event("startup")
def _startup():
    first_provider = next(iter(DEFAULT_MODELS))
    db.init_db(first_provider, DEFAULT_MODELS[first_provider])
    crm.seed()


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

    cost = calculate_cost(req.provider, model, result.input_tokens, result.output_tokens)
    db.log_usage(
        channel="api", provider=req.provider, model=model,
        input_tokens=result.input_tokens, output_tokens=result.output_tokens,
        cost_usd=cost["total_usd"] if cost else None,
        tool_call_count=result.tool_call_count, turn_count=result.turn_count,
        latency_ms=latency_ms, user_message=req.message, reply_text=result.reply,
        media_kind="text",
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


def _send_whatsapp(to_number: str, body: str, media_files: list[str]) -> None:
    sid = os.environ.get("TWILIO_ACCOUNT_SID")
    token = os.environ.get("TWILIO_AUTH_TOKEN")
    missing = _missing_send_config()
    if missing:
        raise RuntimeError(f"Can't send reply - these env vars are not set: {', '.join(missing)}")
    client = TwilioClient(sid, token)
    urls = [u for u in (media.public_url(f) for f in media_files) if u]
    client.messages.create(
        from_=f"whatsapp:{TWILIO_WHATSAPP_NUMBER.removeprefix('whatsapp:')}",
        to=f"whatsapp:{to_number}",
        body=body or "",
        media_url=urls or None,
    )


def _handle_message(
    from_number: str, body: str, image: dict | None, media_kind: str, send: bool = True
) -> str:
    """Runs the agent, logs the exchange, and (when `send`) delivers the reply via
    Twilio's API. Normally called in the background so a slow exchange (transcription
    + tool calls + speech) can't hit Twilio's webhook timeout. Returns the reply text
    so the caller can deliver it inline instead when async sending isn't configured."""
    conversation_id = db.get_or_create_conversation(from_number)
    history = db.get_recent_messages(conversation_id, limit=10)
    settings = db.get_settings()
    provider, model = settings["active_provider"], settings["active_model"]

    start = time.time()
    result = None
    error = None
    try:
        result = run_agent(
            provider, body, model, WHATSAPP_SYSTEM_PROMPT, history,
            image=image, caller_phone=from_number,
        )
        reply_text = result.reply
    except Exception as e:
        reply_text = "Sorry, I'm temporarily unavailable. Please try again shortly. 🙏"
        error = str(e)

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
    if send:
        try:
            _send_whatsapp(from_number, reply_text, reply_media)
        except Exception as e:
            error = (error + " | " if error else "") + f"send failed: {e}"

    cost = calculate_cost(provider, model, result.input_tokens, result.output_tokens) if result else None
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
        )
    except Exception:
        pass  # a logging failure should never block the reply

    return reply_text


@app.post("/webhook/whatsapp")
async def whatsapp_webhook(request: Request, background: BackgroundTasks):
    form = await request.form()
    auth_token = os.environ.get("TWILIO_AUTH_TOKEN")
    public_base_url = os.environ.get("PUBLIC_BASE_URL")
    if auth_token and public_base_url:
        validator = RequestValidator(auth_token)
        url = public_base_url.rstrip("/") + "/webhook/whatsapp"
        signature = request.headers.get("X-Twilio-Signature", "")
        if not validator.validate(url, dict(form), signature):
            raise HTTPException(status_code=403, detail="Invalid Twilio signature")

    from_number = form.get("From", "").removeprefix("whatsapp:")
    body = form.get("Body", "") or ""
    if not from_number:
        raise HTTPException(status_code=400, detail="Missing From")

    image = None
    media_kind = "text"
    num_media = int(form.get("NumMedia", "0") or 0)
    if num_media:
        media_url = form.get("MediaUrl0", "")
        content_type = form.get("MediaContentType0", "")
        try:
            blob, ctype = media.download_twilio_media(media_url)
            if content_type.startswith("image/") or ctype.startswith("image/"):
                media_kind = "image"
                image = media.encode_image(blob, ctype)
                body = body or "(the customer sent this image)"
            elif content_type.startswith("audio/") or ctype.startswith("audio/"):
                media_kind = "audio"
                body = media.transcribe_audio(blob, ctype)
            else:
                media_kind = "unsupported"
                body = body or f"(the customer sent a {ctype} file, which you can't open)"
        except Exception as e:
            media_kind = "error"
            body = body or "(the customer sent an attachment that couldn't be processed)"
            print(f"media handling failed: {e}")

    if not body:
        raise HTTPException(status_code=400, detail="Nothing to reply to")

    missing = _missing_send_config()
    if missing:
        # Can't send via Twilio's API, so answer inline instead of generating a reply
        # nobody ever receives. Slower path (risks Twilio's ~15s timeout on long
        # exchanges) and can't attach media - set the missing vars to get those back.
        print(f"Replying inline; set {', '.join(missing)} for async replies with media.")
        reply_text = _handle_message(from_number, body, image, media_kind, send=False)
        twiml = MessagingResponse()
        twiml.message(reply_text)
        return Response(content=str(twiml), media_type="application/xml")

    # Ack Twilio immediately with empty TwiML; the real reply goes out via the API.
    background.add_task(_handle_message, from_number, body, image, media_kind)
    return Response(content=str(MessagingResponse()), media_type="application/xml")


# ---------------------------------------------------------------------------
# Admin UI (Basic Auth)
# ---------------------------------------------------------------------------

security = HTTPBasic()


def require_admin(credentials: HTTPBasicCredentials = Depends(security)):
    # Defaults to admin/admin if you don't set these - fine for a quick throwaway
    # test, but set your own ADMIN_USERNAME/ADMIN_PASSWORD before sharing the URL.
    username = os.environ.get("ADMIN_USERNAME", "admin")
    password = os.environ.get("ADMIN_PASSWORD", "admin")
    valid = secrets.compare_digest(credentials.username, username) & secrets.compare_digest(credentials.password, password)
    if not valid:
        raise HTTPException(status_code=401, detail="Invalid credentials", headers={"WWW-Authenticate": "Basic"})
    return True


admin_router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])


@admin_router.get("", response_class=HTMLResponse)
def admin_page():
    path = os.path.join(os.path.dirname(__file__), "admin", "index.html")
    with open(path) as f:
        return f.read()


@admin_router.get("/settings")
def admin_get_settings():
    pricing = load_pricing()
    return {
        **db.get_settings(),
        "providers": {
            provider: {
                **db.key_status(provider),
                "default_model": DEFAULT_MODELS[provider],
                "priced_models": list(pricing.get(provider, {}).keys()),
            }
            for provider in PROVIDERS
        },
    }


class SettingsUpdate(BaseModel):
    provider: str
    model: str


@admin_router.post("/settings")
def admin_update_settings(req: SettingsUpdate):
    if req.provider not in PROVIDERS:
        raise HTTPException(status_code=400, detail=f"Unknown provider '{req.provider}'")
    if not db.resolve_api_key(req.provider):
        raise HTTPException(status_code=400, detail=f"No API key configured for '{req.provider}' yet")
    db.update_settings(req.provider, req.model)
    return {"ok": True}


class KeyUpdate(BaseModel):
    provider: str
    api_key: str


@admin_router.post("/keys")
def admin_set_key(req: KeyUpdate):
    if req.provider not in PROVIDERS:
        raise HTTPException(status_code=400, detail=f"Unknown provider '{req.provider}'")
    if not req.api_key.strip():
        raise HTTPException(status_code=400, detail="api_key is empty")
    masked = db.set_provider_key(req.provider, req.api_key.strip())
    return {"provider": req.provider, "masked": masked, "source": "database"}


@admin_router.get("/usage")
def admin_usage(limit: int = 50, offset: int = 0):
    return db.get_usage(limit, offset)


app.include_router(admin_router)
