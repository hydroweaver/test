import os
import secrets
import time

from dotenv import load_dotenv
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel
from twilio.request_validator import RequestValidator
from twilio.twiml.messaging_response import MessagingResponse

import db
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
    f"You are a helpful WhatsApp concierge for {COMPANY_NAME}'s website. "
    "Use the search_website tool to find accurate information on the site before "
    "answering questions about the company, its products, services, pricing, or "
    "policies. Keep replies concise and friendly, suitable for a WhatsApp message."
)


@app.on_event("startup")
def _startup():
    first_provider = next(iter(DEFAULT_MODELS))
    db.init_db(first_provider, DEFAULT_MODELS[first_provider])


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
        latency_ms=latency_ms,
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

@app.post("/webhook/whatsapp")
async def whatsapp_webhook(request: Request):
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
    body = form.get("Body", "")
    if not from_number or not body:
        raise HTTPException(status_code=400, detail="Missing From/Body")

    conversation_id = db.get_or_create_conversation(from_number)
    history = db.get_recent_messages(conversation_id, limit=10)
    settings = db.get_settings()
    provider, model = settings["active_provider"], settings["active_model"]

    start = time.time()
    try:
        result = run_agent(provider, body, model, WHATSAPP_SYSTEM_PROMPT, history)
        reply_text = result.reply
    except Exception as e:
        # Covers a missing key (ProviderError) as well as any provider SDK failure
        # (bad key, rate limit, network, etc.) - the webhook must always reply, never 500.
        result = None
        reply_text = "Sorry, I'm temporarily unavailable. Please try again shortly."
        error = str(e)
    else:
        error = None
    latency_ms = int((time.time() - start) * 1000)

    db.add_message(conversation_id, "user", body)
    if result is not None:
        db.add_message(conversation_id, "assistant", reply_text)
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
        )
    except Exception:
        pass  # a logging failure should never block the WhatsApp reply

    twiml = MessagingResponse()
    twiml.message(reply_text)
    return Response(content=str(twiml), media_type="application/xml")


# ---------------------------------------------------------------------------
# Admin UI (Basic Auth)
# ---------------------------------------------------------------------------

security = HTTPBasic()


def require_admin(credentials: HTTPBasicCredentials = Depends(security)):
    username = os.environ.get("ADMIN_USERNAME")
    password = os.environ.get("ADMIN_PASSWORD")
    if not username or not password:
        raise HTTPException(status_code=500, detail="ADMIN_USERNAME/ADMIN_PASSWORD not configured on the server")
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
