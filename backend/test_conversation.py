"""Benchmark one or more models against the deployed bot, then compare them.

Posts to /webhook/whatsapp exactly the way Twilio does, so it exercises the real
path - agent loop, CRM tools, vision, per-exchange logging - without needing a
phone or a browser. With several models it switches the active model between runs
and prints a side-by-side table, which is the whole point of this rig.

    python test_conversation.py https://your-app.up.railway.app \
        --models "openai|gpt-5,gemini|gemini-3.6-flash" \
        --admin admin:yourpassword

NOTE: replies are delivered for real, so the caller's phone will buzz once per
message per model. Use --caller with a number that isn't yours to avoid that.
"""

import argparse
import sys
import time

import requests

# A realistic kirana-store conversation: greeting, order chase, a follow-up that
# only works with context, an image, prices, delivery, khata, and two writes.
SCRIPT = [
    ("hi", None),
    ("where's my order?", None),
    ("when will it reach?", None),                          # needs prior context
    ("is this what I ordered?", "IMAGE"),                   # vision
    ("what's the rate for basmati rice?", None),            # list_products
    ("do you deliver to Koramangala?", None),               # check_delivery_area
    ("how much khata is pending?", None),                   # khata balance
    ("send 2 packets of toor dal", None),                   # write: place_order
    ("can you start keeping organic jaggery?", None),        # write: stock request
    ("what did I ask you first today?", None),              # conversation memory
]


def login(base, admin):
    """Session-cookie login - the admin pages stopped using Basic Auth."""
    user, _, pw = admin.partition(":")
    s = requests.Session()
    r = s.post(f"{base}/admin/login", data={"username": user, "password": pw},
               timeout=20, allow_redirects=False)
    if r.status_code not in (200, 302, 303):
        sys.exit(f"Admin login failed ({r.status_code}). Check --admin user:password.")
    if s.get(f"{base}/admin/usage?limit=1", timeout=20).status_code != 200:
        sys.exit("Admin login rejected - wrong password?")
    return s


def send(base, caller, body, media_url=None):
    data = {"From": f"whatsapp:{caller}", "Body": body, "NumMedia": "0"}
    if media_url:
        data.update({"NumMedia": "1", "MediaUrl0": media_url,
                     "MediaContentType0": "image/png" if ".png" in media_url else "audio/ogg"})
    r = requests.post(f"{base}/webhook/whatsapp", data=data, timeout=120)
    r.raise_for_status()
    return r.text


def run_model(s, base, provider, model, caller, image_url, wait):
    r = s.post(f"{base}/admin/settings", json={"provider": provider, "model": model}, timeout=60)
    if r.status_code != 200:
        print(f"  ! can't select {provider}/{model}: {r.text[:200]}")
        return None
    # Row ids only go up, so anything above this watermark belongs to this run.
    watermark = s.get(f"{base}/admin/usage?limit=1", timeout=30).json()["rows"]
    watermark = watermark[0]["id"] if watermark else 0

    for i, (body, kind) in enumerate(SCRIPT, 1):
        media = image_url if kind == "IMAGE" else None
        print(f"  {i:2}/{len(SCRIPT)}  {body[:48]}")
        try:
            send(base, caller, body, media)
        except Exception as e:
            print(f"      send failed: {e}")
        time.sleep(wait)

    rows = s.get(f"{base}/admin/usage?limit=100", timeout=30).json()["rows"]
    return [r for r in rows if r["id"] > watermark]


def summarise(model, rows):
    if not rows:
        return None
    n = len(rows)
    num = lambda k: sum(r.get(k) or 0 for r in rows)
    delivered = sum(1 for r in rows if "delivery: delivered" in (r.get("error") or ""))
    failed = sum(1 for r in rows if "failed" in (r.get("error") or "").lower())
    return {
        "model": model, "replies": n,
        "in": num("input_tokens") // n, "out": num("output_tokens") // n,
        "cached": num("cache_read_tokens") // n,
        "cost": num("cost_usd"), "cost_each": num("cost_usd") / n,
        "latency": num("latency_ms") / n / 1000,
        "tools": num("tool_call_count") / n, "turns": num("turn_count") / n,
        "delivered": delivered, "errors": failed,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("base_url")
    ap.add_argument("--models", required=True,
                    help='comma-separated "provider|model" pairs, e.g. "openai|gpt-5,gemini|gemini-3.6-flash"')
    ap.add_argument("--caller", default="+32477874767")
    ap.add_argument("--admin", default="admin:admin")
    ap.add_argument("--wait", type=float, default=12,
                    help="seconds between messages - must exceed the model's reply time")
    args = ap.parse_args()

    base = args.base_url.rstrip("/")
    try:
        health = requests.get(f"{base}/health", timeout=20).json()
    except Exception as e:
        sys.exit(f"Can't reach {base}: {e}")
    print(f"{base}  commit={health.get('commit')}  twilio_ready={health.get('twilio_ready')}")
    if not health.get("twilio_ready"):
        print(f"  ! replies won't be delivered: {health.get('twilio_missing')}")

    s = login(base, args.admin)
    image_url = f"{base}/media/product_rice-basmati-5kg.png"

    results = []
    for pair in args.models.split(","):
        provider, _, model = pair.strip().partition("|")
        if not model:
            sys.exit(f"--models needs 'provider|model' pairs, got '{pair}'")
        print(f"\n=== {provider} / {model} ===")
        rows = run_model(s, base, provider, model, args.caller, image_url, args.wait)
        summary = summarise(f"{provider}/{model}", rows or [])
        if summary:
            results.append(summary)
        else:
            print("  no rows logged - model rejected, or replies slower than --wait")

    if not results:
        sys.exit("\nNothing to compare.")

    print(f"\n{'model':<28}{'reply':>6}{'in':>8}{'out':>7}{'cache':>7}"
          f"{'$/reply':>10}{'total $':>10}{'secs':>7}{'tools':>7}{'sent':>6}")
    print("-" * 96)
    for r in results:
        print(f"{r['model']:<28}{r['replies']:>6}{r['in']:>8}{r['out']:>7}{r['cached']:>7}"
              f"{r['cost_each']:>10.4f}{r['cost']:>10.4f}{r['latency']:>7.1f}"
              f"{r['tools']:>7.1f}{r['delivered']:>6}")
    print("\nPer-reply averages. 'sent' counts replies Twilio confirmed delivered.")
    cheapest = min(results, key=lambda r: r["cost_each"])
    fastest = min(results, key=lambda r: r["latency"])
    print(f"Cheapest: {cheapest['model']} (${cheapest['cost_each']:.4f}/reply) · "
          f"Fastest: {fastest['model']} ({fastest['latency']:.1f}s)")


if __name__ == "__main__":
    main()
