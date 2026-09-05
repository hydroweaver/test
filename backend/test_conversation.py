"""Drive a realistic customer conversation at the deployed bot, then report the cost.

Posts to /webhook/whatsapp exactly the way Twilio does, so it exercises the real
path (agent loop, CRM tools, vision, logging) without needing a phone. Run it once
per model to compare like for like.

    python test_conversation.py https://your-app.up.railway.app
    python test_conversation.py https://your-app.up.railway.app --reset

Options:
    --from   +32477874767   caller to simulate (default is the seeded CRM customer)
    --audio  <url>          public audio URL to send as a voice note
    --wait   12             seconds to wait after each message
    --admin  admin:admin    admin credentials for reading the usage log
"""

import argparse
import sys
import time

import requests

# A real support conversation: greeting, order chase, follow-up needing context,
# an image, pricing, a fault report, usage question, and two write actions.
SCRIPT = [
    ("hi", None),
    ("where's my order?", None),
    ("when exactly will it arrive?", None),                      # needs prior context
    ("is this the product I ordered?", "IMAGE"),                 # vision
    ("how much does it cost to send to India?", None),           # rate card
    ("my OTPs keep failing for Nigerian numbers since yesterday", None),  # incident + ticket
    ("how many messages did I send last month?", None),          # usage stats
    ("ok please order another 500000 of the whatsapp one", None),  # write: place_order
    ("can you add a template for delivery delays?", None),       # write: catalogue
    ("what was the first thing I asked you today?", None),       # conversation memory
]


def send(base, caller, body, media_url=None):
    data = {"From": f"whatsapp:{caller}", "Body": body, "NumMedia": "0"}
    if media_url:
        data.update({"NumMedia": "1", "MediaUrl0": media_url,
                     "MediaContentType0": "image/png" if ".png" in media_url else "audio/ogg"})
    r = requests.post(f"{base}/webhook/whatsapp", data=data, timeout=90)
    r.raise_for_status()
    return r.text


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("base_url")
    ap.add_argument("--from", dest="caller", default="+32477874767")
    ap.add_argument("--audio", default=None, help="public URL of an audio file to send")
    ap.add_argument("--wait", type=float, default=12)
    ap.add_argument("--admin", default="admin:admin")
    ap.add_argument("--reset", action="store_true", help="only note where to clear history")
    args = ap.parse_args()

    base = args.base_url.rstrip("/")
    user, _, pw = args.admin.partition(":")

    try:
        requests.get(f"{base}/health", timeout=20).raise_for_status()
    except Exception as e:
        sys.exit(f"Can't reach {base}: {e}")

    # The app serves its own product images publicly - use one as the test image.
    image_url = f"{base}/media/product_wa-api.png"
    before = requests.get(f"{base}/admin/usage?limit=1", auth=(user, pw), timeout=20).json()
    start_count = before["totals"]["count"]

    steps = list(SCRIPT)
    if args.audio:
        steps.insert(4, ("(voice note)", "AUDIO"))

    print(f"Sending {len(steps)} messages as {args.caller}\n")
    for i, (text, kind) in enumerate(steps, 1):
        media = image_url if kind == "IMAGE" else (args.audio if kind == "AUDIO" else None)
        label = f" [{kind.lower()}]" if kind else ""
        print(f"  {i:>2}. {text[:60]}{label}")
        try:
            send(base, args.caller, text, media)
        except Exception as e:
            print(f"      !! failed: {e}")
        time.sleep(args.wait)

    print("\nWaiting for the last reply to finish…")
    time.sleep(args.wait)

    usage = requests.get(f"{base}/admin/usage?limit=100", auth=(user, pw), timeout=30).json()
    rows = [r for r in reversed(usage["rows"])][-(len(steps)):]

    print(f"\n{'#':>2}  {'model':<16} {'in':>7} {'cached':>7} {'out':>7} {'cost':>9} {'tools':>5} "
          f"{'turns':>5} {'secs':>6}  status")
    print("-" * 100)
    tot_in = tot_cached = tot_out = 0
    tot_cost = 0.0
    for i, r in enumerate(rows, 1):
        tot_in += r["input_tokens"]; tot_cached += r.get("cache_read_tokens") or 0
        tot_out += r["output_tokens"]; tot_cost += r["cost_usd"] or 0
        status = (r.get("error") or "")[:34]
        print(f"{i:>2}  {r['model'][:16]:<16} {r['input_tokens']:>7} {r.get('cache_read_tokens') or 0:>7} "
              f"{r['output_tokens']:>7} {(('$%.5f' % r['cost_usd']) if r['cost_usd'] is not None else '-'):>9} "
              f"{r['tool_call_count']:>5} {r['turn_count']:>5} "
              f"{(r['latency_ms'] or 0)/1000:>6.1f}  {status}")
    print("-" * 100)
    n = len(rows) or 1
    print(f"    {'TOTAL':<16} {tot_in:>7} {tot_cached:>7} {tot_out:>7} {'$%.5f' % tot_cost:>9}")
    print(f"\n  replies: {n}   avg cost/reply: ${tot_cost / n:.5f}   "
          f"avg tokens/reply: {(tot_in + tot_cached + tot_out) // n}")
    print(f"  new rows this run: {usage['totals']['count'] - start_count}")
    print("\nFull conversation and replies: %s/admin" % base)


if __name__ == "__main__":
    main()
