"""Provider setup and live model discovery.

Model lists are fetched from each provider's own API rather than hardcoded, so the
dropdown always shows what your key can actually call - no stale or invented model
IDs. Pricing still comes from pricing.json; anything without an entry there is
flagged rather than silently costed wrong.
"""

import os
import time

# Imported at module level on purpose. Importing these lazily inside functions
# meant two threadpool workers could import the same SDK concurrently and get
# "partially initialized module" errors.
from google import genai
from openai import OpenAI

import db

PROVIDERS = ["openai", "gemini"]

# Deliberately empty. Guessed model IDs are worse than none: picking one that
# doesn't exist fails at request time with a 404 from the provider. If we can't
# list models, say so instead of offering names we haven't verified.
FALLBACK_MODELS: dict[str, list[str]] = {"openai": [], "gemini": []}

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
    ids = [m.id for m in OpenAI(api_key=api_key).models.list()]
    # Chat-capable models only - skip embeddings, audio, image, moderation etc.
    skip = ("embedding", "whisper", "tts", "dall-e", "moderation", "image", "realtime",
            "transcribe", "audio", "codex", "search", "computer-use", "sora", "video",
            "guard", "babbage", "davinci", "instruct")
    chat = [i for i in ids if not any(s in i.lower() for s in skip)]
    return sorted(chat, reverse=True)


_clients: dict[str, object] = {}


def gemini_client(api_key: str):
    """One client per key, reused. Building a fresh one per call left short-lived
    clients (and their HTTP transports) being torn down mid-use, which surfaces as
    'Cannot send a request, as the client has been closed'."""
    if api_key not in _clients:
        _clients[api_key] = genai.Client(api_key=api_key)
    return _clients[api_key]


def _gemini_models(api_key: str) -> tuple[list[str], dict[str, bool]]:
    """Returns model IDs plus, per model, whether it's a "thinking" model - Gemini's
    own flag for whether it reasons before replying (slower, and billed for it) or
    answers directly. Surfaced in the admin dropdown so a fast/cheap pick is obvious
    without guessing from the name."""
    out, thinking = [], {}
    for m in gemini_client(api_key).models.list():
        actions = getattr(m, "supported_actions", None) or []
        if actions and "generateContent" not in actions:
            continue
        name = (getattr(m, "name", "") or "").removeprefix("models/")
        if name and "embedding" not in name and "aqa" not in name:
            out.append(name)
            thinking[name] = bool(getattr(m, "thinking", False))
    names = sorted(set(out), reverse=True)
    return names, thinking


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
    models, live, note, thinking = [], False, "", {}
    if not api_key:
        note = f"Add a {provider} key to load the full model list from their API."
    else:
        try:
            if provider == "openai":
                models = _openai_models(api_key)
            else:
                models, thinking = _gemini_models(api_key)
            live = bool(models)
        except Exception as e:
            note = f"Couldn't reach {provider} to list models: {e}"
            print(note)

    if not models:
        models = FALLBACK_MODELS.get(provider, [])
        note = note or f"Showing a fallback list - {provider} returned nothing."

    result = {"models": models, "live": live, "note": note, "thinking": thinking}
    _cache[provider] = (time.time(), result)
    return result
