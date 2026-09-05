"""Incoming and outgoing WhatsApp media.

Incoming: Twilio hands us authenticated URLs - images go to the model as vision
input, voice notes get transcribed. Outgoing: files are written to a public media
directory the app serves, since Twilio fetches reply media over HTTP.

Audio always goes through OpenAI (Whisper in, TTS out) regardless of which chat
provider is active, because Claude can't take audio input at all.
"""

import base64
import os
import uuid

import requests

import db

MEDIA_DIR = os.path.join(os.path.dirname(db.DB_PATH) or ".", "media")
TRANSCRIBE_MODEL = os.environ.get("TRANSCRIBE_MODEL", "whisper-1")
TTS_MODEL = os.environ.get("TTS_MODEL", "gpt-4o-mini-tts")
TTS_VOICE = os.environ.get("TTS_VOICE", "alloy")


# Learned from the last incoming webhook, so reply media keeps working after the app
# is renamed (which changes the domain) even if PUBLIC_BASE_URL still points at the old one.
_observed_base_url: str | None = None


def remember_base_url(url: str) -> None:
    global _observed_base_url
    _observed_base_url = url.rstrip("/")


def public_url(filename: str) -> str | None:
    base = os.environ.get("PUBLIC_BASE_URL") or _observed_base_url
    return f"{base.rstrip('/')}/media/{filename}" if base else None


def download_twilio_media(url: str) -> tuple[bytes, str]:
    """Twilio media URLs need account auth. Returns (bytes, content_type)."""
    sid = os.environ.get("TWILIO_ACCOUNT_SID")
    token = os.environ.get("TWILIO_AUTH_TOKEN")
    resp = requests.get(url, auth=(sid, token) if sid and token else None, timeout=30)
    resp.raise_for_status()
    return resp.content, resp.headers.get("Content-Type", "application/octet-stream")


def transcribe_audio(audio_bytes: bytes, content_type: str) -> str:
    """Voice note -> text, via OpenAI Whisper."""
    from openai import OpenAI

    key = db.resolve_api_key("openai")
    if not key:
        raise RuntimeError(
            "Voice notes need an OpenAI key (used for transcription even when another "
            "provider answers). Add one on the admin page."
        )
    ext = {"audio/ogg": "ogg", "audio/mpeg": "mp3", "audio/mp4": "mp4", "audio/wav": "wav"}.get(
        content_type.split(";")[0], "ogg"
    )
    path = os.path.join(MEDIA_DIR, f"in_{uuid.uuid4().hex}.{ext}")
    os.makedirs(MEDIA_DIR, exist_ok=True)
    with open(path, "wb") as f:
        f.write(audio_bytes)
    try:
        client = OpenAI(api_key=key)
        with open(path, "rb") as f:
            result = client.audio.transcriptions.create(model=TRANSCRIBE_MODEL, file=f)
        return result.text
    finally:
        os.remove(path)


def synthesize_voice_note(text: str) -> str | None:
    """Text -> an .mp3 in the public media dir. Returns the filename, or None if unavailable."""
    from openai import OpenAI

    key = db.resolve_api_key("openai")
    if not key:
        return None
    os.makedirs(MEDIA_DIR, exist_ok=True)
    filename = f"reply_{uuid.uuid4().hex}.mp3"
    path = os.path.join(MEDIA_DIR, filename)
    client = OpenAI(api_key=key)
    with client.audio.speech.with_streaming_response.create(
        model=TTS_MODEL, voice=TTS_VOICE, input=text[:4000]
    ) as response:
        response.stream_to_file(path)
    return filename


def encode_image(image_bytes: bytes, content_type: str) -> dict:
    """Normalized image payload the per-provider agents convert to their own format."""
    return {
        "media_type": content_type.split(";")[0] or "image/jpeg",
        "b64": base64.standard_b64encode(image_bytes).decode(),
    }
