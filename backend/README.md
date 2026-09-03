# WhatsApp concierge + multi-model token/cost tracker

A FastAPI backend that answers WhatsApp messages (via Twilio) as a website concierge,
using Claude, ChatGPT (OpenAI), or Gemini with a tool-calling loop over a one-time
crawl of a target site. Every reply's exact token usage (summed across all tool-call
turns) and USD cost land in SQLite, viewable on a built-in `/admin` page where you can
also paste API keys and switch the active provider/model. Also usable directly via
`POST /chat`, no WhatsApp required. This is a throwaway test rig for comparing
token/cost across models, not a hardened product - see the security note below.

## Setup

```bash
cd backend
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Generate an encryption key for API keys pasted into the admin page, and set
`ADMIN_USERNAME`/`ADMIN_PASSWORD` (required - `/admin/*` won't work without them):

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Put that in `SETTINGS_ENCRYPTION_KEY` in `.env`. Provider API keys can either go in
`.env` (`ANTHROPIC_API_KEY` etc.) or be pasted later through the admin page - either
works, the admin page takes precedence if both are set.

## Crawl the target site once

```bash
python crawl.py https://www.routemobile.com --max-pages 30
```

This is what `search_website` (the tool the model calls) searches over. Re-run it any
time the site content changes - it replaces old chunks for URLs it re-visits.

## Run

```bash
uvicorn app:app --reload
```

- `POST /chat` - direct testing, no WhatsApp needed (see below).
- `GET /admin` - paste keys, pick active provider/model, see the usage log.
- `POST /webhook/whatsapp` - what Twilio calls.

## Try it via `/chat`

```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"provider": "anthropic", "message": "What does Route Mobile do?"}'
```

Returns the reply plus `usage` (exact tokens from the provider's own response,
summed across every tool-call turn), `cost_usd`, `tool_call_count`, and `turn_count`.

## Wiring up WhatsApp (Twilio)

1. Deploy somewhere with a public HTTPS URL (this repo ships a `Procfile` for
   Railway - attach a **Volume mounted at `/data`** and set `DB_PATH=/data/app.db`,
   otherwise the SQLite file resets on every redeploy).
2. Set `PUBLIC_BASE_URL` to that URL, plus `TWILIO_ACCOUNT_SID`/`TWILIO_AUTH_TOKEN`.
3. In the Twilio console (WhatsApp Sandbox or a real WhatsApp sender), set the
   incoming-message webhook to `<your-url>/webhook/whatsapp`.
4. Message the Twilio WhatsApp number - replies use whichever provider/model is set
   as "active" on the `/admin` page.

To test locally before deploying: `ngrok http 8000`, set `PUBLIC_BASE_URL` to the
ngrok HTTPS URL, point Twilio's sandbox webhook at `<ngrok-url>/webhook/whatsapp`.

## How the numbers work

- **Token counts** come straight from each provider's own API response, summed across
  every turn of the tool-calling loop (the model may call `search_website` more than
  once before answering) - never estimated locally.
- **Cost** is `tokens * price_per_million` from `pricing.json`. Provider pricing
  drifts, especially OpenAI/Gemini - treat it as a starting point and edit that file
  if a rate is off.
- `tool_call_count` / `turn_count` on every logged row let you compare how much a
  tool-using agent loop actually costs per reply, across providers.

## Security note (read before deploying anywhere reachable)

This is built for quick personal testing, not production:
- `/admin` is gated only by HTTP Basic Auth (`ADMIN_USERNAME`/`ADMIN_PASSWORD`) - fine
  for a throwaway test, not for anything you'd keep running with real traffic.
- Pasted provider API keys are encrypted at rest (Fernet) and never echoed back to the
  browser, but if `SETTINGS_ENCRYPTION_KEY` leaks, so do the keys.
- The WhatsApp webhook checks Twilio's request signature only when both
  `TWILIO_AUTH_TOKEN` and `PUBLIC_BASE_URL` are set - if you skip those, anyone can
  POST to `/webhook/whatsapp` and spend your API budget.
- No rate limiting anywhere.

## Notes

- Conversation history is per WhatsApp phone number (last ~10 messages); `/chat` stays
  single-turn (no history) since it's meant for quick one-off testing.
- `providers.py`'s single-turn functions (no tools/history) still work for quick manual
  testing; the live paths (`/chat`, the webhook) go through `agent.py`'s tool-loop
  instead.
