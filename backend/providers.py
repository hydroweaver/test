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


def clear_cache(provider: str | None = None) -> None:
    """Called when a key changes, so the model list refreshes immediately instead
    of staying stale for the cache window."""
    if provider:
        _cache.pop(provider, None)
    else:
        _cache.clear()


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
            "transcribe", "audio", "codex", "search", "computer-use", "sora", "video",
            "guard", "babbage", "davinci", "instruct")
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


def list_models(provider: str) -> dict:
    """Live model list from the provider, cached briefly.

    Returns {"models": [...], "live": bool, "note": str}. `live` is False when we
    had to fall back to a stub list - which is almost always because no key is
    configured, and is worth saying out loud rather than quietly showing 3 models.
    """
    cached = _cache.get(provider)
    if cached and time.time() - cached[0] < CACHE_SECONDS:
        return cached[1]

    api_key = db.resolve_api_key(provider)
    models, live, note = [], False, ""
    if not api_key:
        note = f"Add a {provider} key to load the full model list from their API."
    else:
        try:
            models = _openai_models(api_key) if provider == "openai" else _gemini_models(api_key)
            live = bool(models)
        except Exception as e:
            note = f"Couldn't reach {provider} to list models: {e}"
            print(note)

    if not models:
        models = FALLBACK_MODELS.get(provider, [])
        note = note or f"Showing a fallback list - {provider} returned nothing."

    result = {"models": models, "live": live, "note": note}
    _cache[provider] = (time.time(), result)
    return result
