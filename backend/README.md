# WhatsApp concierge + multi-model token/cost tracker

Answers WhatsApp messages (via Twilio) as a local kirana (grocery) store's assistant,
using ChatGPT or Gemini with a tool-calling loop over a toy CRM (products, orders,
khata/credit tab, delivery areas) plus an optional one-time crawl of the shop's own
website or FAQ page. Every reply's exact token usage and cost land in a built-in
`/admin` page, where you can switch the active provider/model. This is a throwaway
test rig for comparing token/cost across models, not a hardened product.

**What the bot can do**
- Replies in a snappy, emoji-friendly WhatsApp voice (short messages, `*bold*` facts,
  numbered options).
- **Receives** text, images (sent to the model as vision input), and voice notes
  (transcribed with OpenAI Whisper regardless of which chat model is active, since
  Gemini's chat models don't take audio input directly).
- **Sends** text, product images, and voice notes back (voice replies when you sent a
  voice note; OpenAI TTS).
- Calls a **toy CRM** (`crm.py`): looks the caller up by WhatsApp number, lists their
  orders, checks an order by number, lists products and prices, sends a product image,
  checks delivery areas/fees, looks up the khata (credit tab), takes new orders, and
  opens complaint tickets. All dummy seeded data - edit the lists at the top of
  `crm.py`.
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
3. Once deployed, visit `https://<your-app>.up.railway.app/admin` (login `admin`/
   `admin` unless you set `ADMIN_USERNAME`/`ADMIN_PASSWORD`), confirm a provider shows
   configured, and pick the active provider/model.
4. Crawl the target site once so the bot has something to answer from:
   `railway run python crawl.py https://www.example.com`
5. For WhatsApp, set these env vars - **all four are required**: `TWILIO_ACCOUNT_SID`,
   `TWILIO_AUTH_TOKEN`, `TWILIO_WHATSAPP_NUMBER` (your live WhatsApp sender, or
   `+14155238886` if you're on the sandbox) and `PUBLIC_BASE_URL` (your Railway URL).
   Then in the Twilio console point the WhatsApp number's incoming-message webhook at
   `<your-railway-url>/webhook/whatsapp`.

   **The 24-hour window applies to a live number.** WhatsApp only allows free-form
   replies within 24 hours of the customer's own last message; outside that, only an
   approved template goes through, and this app doesn't send templates. So the bot
   answers people who message it, but a reply sent out of the blue (including the
   admin page's *Test WhatsApp* button) is accepted by Twilio and then dropped by
   WhatsApp with error 63016. The usage log now records that verdict.

   There is one delivery path: the webhook is acked instantly and the reply is sent
   from a background task via Twilio's API, which has no time limit - so a model that
   takes 30s still gets its answer delivered, and every row records the Twilio message
   SID as proof. (Replying inline in the webhook response would need no credentials,
   but Twilio drops any webhook over ~15s, which silently loses slow models' replies -
   so that path is gone.) Without the credentials nothing can be sent: the app warns at
   startup and every row logs `send failed`. They are also what lets the bot fetch
   incoming images and voice notes.

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
- `GET /admin` - pick active provider/model, see the usage log.
- `POST /webhook/whatsapp` - what Twilio calls (use `ngrok http 8000` to test this
  locally, same as step 5 above but with the ngrok URL as `PUBLIC_BASE_URL`).

```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"provider": "openai", "message": "What does this site offer?"}'
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
if you don't set your own credentials, and the WhatsApp webhook only checks Twilio's
signature when you opt in with `VERIFY_TWILIO_SIGNATURE=1`. Fine for a throwaway test; set real credentials before leaving this running
anywhere someone else could find the URL.

`/admin` logs in with a form and a signed session cookie (HttpOnly, SameSite=Lax,
`Secure` over HTTPS), valid 7 days. The cookie is signed with a secret derived from
`ADMIN_PASSWORD`, so changing the password logs every session out; set
`ADMIN_SESSION_SECRET` if you'd rather it were independent. Note this only changes
*how* the password is transmitted and stored - the protection is still one shared
password, so the thing that actually matters is setting a strong `ADMIN_PASSWORD`.
