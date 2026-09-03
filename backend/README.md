# Multi-model chat + cost tracker

A small FastAPI backend that sends your message to Claude, ChatGPT (OpenAI), or Gemini,
and returns the reply along with the exact token counts and USD cost for that one reply.

## Setup

```bash
cd backend
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then paste your API key(s) into .env
```

You only need to set the key(s) for the provider(s) you actually want to call.

## Run

```bash
uvicorn app:app --reload
```

## Use it

```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{
    "provider": "anthropic",
    "message": "Explain recursion in one sentence."
  }'
```

Response:

```json
{
  "provider": "anthropic",
  "model": "claude-opus-5",
  "reply": "Recursion is when a function calls itself...",
  "usage": { "input_tokens": 14, "output_tokens": 27, "total_tokens": 41 },
  "cost_usd": { "input_usd": 0.00007, "output_usd": 0.000675, "total_usd": 0.000745 },
  "pricing_note": "Cost calculated from backend/pricing.json - verify against the provider's current pricing page."
}
```

Swap `"provider"` for `"openai"` or `"gemini"` to hit those instead. Optionally pass
`"model"` (overrides the provider's default model) and `"system"` (a system prompt).

`GET /models` shows which providers currently have a key configured and which models
have known pricing.

## How the numbers work

- **Token counts** come straight from each provider's own API response
  (`usage.input_tokens`/`output_tokens` for Anthropic and OpenAI, `usage_metadata` for
  Gemini) - never estimated locally, so they're exact.
- **Cost** is `tokens * price_per_million` using the rates in `pricing.json`. Provider
  pricing changes fairly often (especially OpenAI and Gemini), so treat the shipped
  numbers as a starting point, not gospel - double check against the official pricing
  pages linked at the top of `pricing.json`, and edit that file when a rate changes or
  you want to add a new model.
- If you call a model that isn't in `pricing.json`, you still get exact token counts
  back, just with `cost_usd: null` and a note telling you to add pricing for it.

## Notes / what this deliberately doesn't do

- No conversation history - each call to `/chat` is a single independent turn. Add a
  `messages` list per-session if you want multi-turn chat.
- Keys live server-side in `.env`, not in the request body, so they never end up in
  logs or client code.
- No auth on the backend itself - it's meant to run locally. Add an API key / auth
  layer before exposing it beyond your own machine.
