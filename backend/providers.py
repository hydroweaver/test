"""Provider setup and live model discovery.

Model lists are fetched from each provider's own API rather than hardcoded, so the
dropdown always shows what your key can actually call - no stale or invented model
IDs. Pricing still comes from pricing.json; anything without an entry there is
flagged rather than silently costed wrong.
"""

import os
import time

import db

PROVIDERS = ["openai", "gemini"]

# Shown if the provider's API can't be reached (no key, network, etc.)
FALLBACK_MODELS = {
    "openai": ["gpt-5", "gpt-5-mini", "gpt-5-nano"],
    "gemini": ["gemini-3-pro", "gemini-3-flash"],
}

_cache: dict[str, tuple[float, list[str]]] = {}
CACHE_SECONDS = 300


class ProviderError(Exception):
    pass


def _require_key(env_var: str, provider: str) -> str:
    key = os.environ.get(env_var)
    if not key:
        raise ProviderError(
            f"{env_var} is not set. Add it to backend/.env to use the '{provider}' provider."
        )
    return key


def _openai_models(api_key: str) -> list[str]:
    from openai import OpenAI

    ids = [m.id for m in OpenAI(api_key=api_key).models.list()]
    # Chat-capable models only - skip embeddings, audio, image, moderation etc.
    skip = ("embedding", "whisper", "tts", "dall-e", "moderation", "image", "realtime",
            "transcribe", "audio", "codex", "search", "computer-use")
    chat = [i for i in ids if not any(s in i.lower() for s in skip)]
    return sorted(chat, reverse=True)


def _gemini_models(api_key: str) -> list[str]:
    from google import genai

    out = []
    for m in genai.Client(api_key=api_key).models.list():
        actions = getattr(m, "supported_actions", None) or []
        if actions and "generateContent" not in actions:
            continue
        name = (getattr(m, "name", "") or "").removeprefix("models/")
        if name and "embedding" not in name and "aqa" not in name:
            out.append(name)
    return sorted(set(out), reverse=True)


def list_models(provider: str) -> list[str]:
    """Live model list from the provider, cached briefly. Falls back to a small
    known-good list if the provider can't be reached."""
    cached = _cache.get(provider)
    if cached and time.time() - cached[0] < CACHE_SECONDS:
        return cached[1]

    api_key = db.resolve_api_key(provider)
    models = []
    if api_key:
        try:
            models = _openai_models(api_key) if provider == "openai" else _gemini_models(api_key)
        except Exception as e:
            print(f"could not list {provider} models: {e}")

    models = models or FALLBACK_MODELS.get(provider, [])
    _cache[provider] = (time.time(), models)
    return models
