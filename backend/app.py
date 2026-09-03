import os

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from pricing import calculate_cost, load_pricing
from providers import PROVIDERS, ProviderError

load_dotenv()

app = FastAPI(title="Multi-model chat + cost tracker")

DEFAULT_MODELS = {
    "anthropic": os.environ.get("ANTHROPIC_DEFAULT_MODEL", "claude-opus-5"),
    "openai": os.environ.get("OPENAI_DEFAULT_MODEL", "gpt-5"),
    "gemini": os.environ.get("GEMINI_DEFAULT_MODEL", "gemini-3-flash"),
}


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
    pricing_note: str


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/models")
def models():
    """Which providers have a key configured, default model, and known-priced models."""
    pricing = load_pricing()
    return {
        provider: {
            "configured": bool(os.environ.get(f"{provider.upper()}_API_KEY")),
            "default_model": DEFAULT_MODELS[provider],
            "priced_models": list(pricing.get(provider, {}).keys()),
        }
        for provider in PROVIDERS
    }


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest):
    if req.provider not in PROVIDERS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown provider '{req.provider}'. Choose one of: {list(PROVIDERS)}",
        )

    model = req.model or DEFAULT_MODELS[req.provider]

    try:
        reply, input_tokens, output_tokens = PROVIDERS[req.provider](req.message, model, req.system)
    except ProviderError as e:
        raise HTTPException(status_code=400, detail=str(e))

    cost = calculate_cost(req.provider, model, input_tokens, output_tokens)
    pricing_note = (
        "Cost calculated from backend/pricing.json - verify against the provider's current pricing page."
        if cost is not None
        else f"No pricing entry for '{model}' in pricing.json - token counts are exact, cost could not be computed. Add this model's rates to pricing.json."
    )

    return ChatResponse(
        provider=req.provider,
        model=model,
        reply=reply,
        usage=TokenUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
        ),
        cost_usd=cost,
        pricing_note=pricing_note,
    )
