# WhatsApp concierge + multi-model token/cost tracker

Answers WhatsApp messages (via Twilio) as a website concierge, using Claude, ChatGPT,
or Gemini with a tool-calling loop over a one-time crawl of a target site plus a toy
CRM. Every reply's exact token usage and cost land in a built-in `/admin` page, where
you can also paste API keys and switch the active provider/model. This is a throwaway
test rig for comparing token/cost across models, not a hardened product.

**What the bot can do**
- Replies in a snappy, emoji-friendly WhatsApp voice (short messages, `*bold*` facts,
  numbered options).
- **Receives** text, images (sent to the model as vision input), and voice notes
  (transcribed with OpenAI Whisper - needed even when Claude/Gemini is the chat model,
  since Claude can't take audio).
- **Sends** text, product images, and voice notes back (voice replies when you sent a
  voice note; OpenAI TTS).
- Calls a **toy CRM** (`crm.py`): looks the caller up by WhatsApp number, lists their
  orders, checks an order by number, lists products, sends a product image, and opens
  support tickets. All dummy seeded data - edit the lists at the top of `crm.py`.
- No WhatsApp **templates** needed: the bot only ever replies inside the 24-hour
  customer service window, where free-form text and media are allowed. (Carousels and
  quick-reply buttons *would* need approved Content Templates - not built.)

**One row per exchange:** each usage-log row is exactly one incoming message and the
one reply it produced, with every token spent in between (including all tool-call
turns) counted against it - so per-reply cost is directly comparable.

## Fastest path: deploy to Railway

1. Deploy this repo to Railway, with **root directory set to `backend`** (Railway
   project → Settings → Root Directory) - that's where `requirements.txt` and the
   `Procfile` live.
2. Set at least one provider key as a Railway env var (`OPENAI_API_KEY`
   or `GEMINI_API_KEY`). These are the only way keys are supplied.
   after deploying. Everything else has a working default; nothing else is required
   to get the app up.
3. Once deployed, visit `https://<your-app>.up.railway.app/admin` (login `admin`/
   `admin` unless you set `ADMIN_USERNAME`/`ADMIN_PASSWORD`), confirm a provider shows
   configured, and pick the active provider/model.
4. Crawl the target site once so the bot has something to answer from:
   `railway run python crawl.py https://www.example.com`
5. For WhatsApp, set these env vars: `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`,
   `TWILIO_WHATSAPP_NUMBER` (the sandbox number is `+14155238886`) and
   `PUBLIC_BASE_URL` (your Railway URL). Then in the Twilio console point the WhatsApp
   number's incoming-message webhook at `<your-railway-url>/webhook/whatsapp`.
   All four matter: replies are sent asynchronously through Twilio's API (so slow
   exchanges can't hit the webhook timeout), and images/voice notes are fetched by
   Twilio from `PUBLIC_BASE_URL/media/...`.

That's it - message the Twilio WhatsApp number and watch replies + token/cost show up
in `/admin`. (Optional: attach a Railway Volume mounted at `/data` and set
`DB_PATH=/data/app.db` if you want the usage log/crawled content to survive a
redeploy - skip it for a quick test, nothing breaks either way.)

## Running it locally instead

```bash
cd backend
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # add at least one provider key
python crawl.py https://www.example.com --max-pages 30
uvicorn app:app --reload
```

- `POST /chat` - direct testing, no WhatsApp needed.
- `GET /admin` - paste keys, pick active provider/model, see the usage log.
- `POST /webhook/whatsapp` - what Twilio calls (use `ngrok http 8000` to test this
  locally, same as step 5 above but with the ngrok URL as `PUBLIC_BASE_URL`).

```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"provider": "anthropic", "message": "What does this site offer?"}'
```

Returns the reply plus `usage` (exact tokens, summed across every tool-call turn),
`cost_usd`, `tool_call_count`, and `turn_count`.

## How the numbers work

- **Token counts** come straight from each provider's own API response, summed across
  every turn of the tool-calling loop (the model may call `search_website` more than
  once before answering) - never estimated locally.
- **Cost** is `tokens * price_per_million` from `pricing.json`. Provider pricing
  drifts, especially OpenAI/Gemini - treat it as a starting point and edit that file
  if a rate is off.
- `tool_call_count` / `turn_count` on every logged row let you compare how much a
  tool-using agent loop actually costs per reply, across providers.

## Security note

Built for quick personal testing, not production: `/admin` defaults to `admin`/`admin`
if you don't set your own credentials, pasted keys are encrypted at rest but the
encryption key auto-generates itself if you don't set one, and the WhatsApp webhook
only checks Twilio's signature when `TWILIO_AUTH_TOKEN`+`PUBLIC_BASE_URL` are both
set. Fine for a throwaway test; set real credentials before leaving this running
anywhere someone else could find the URL.
