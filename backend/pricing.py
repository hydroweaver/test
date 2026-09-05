import json
import os

_PRICING_PATH = os.path.join(os.path.dirname(__file__), "pricing.json")


def load_pricing() -> dict:
    with open(_PRICING_PATH) as f:
        data = json.load(f)
    data.pop("_comment", None)
    return data


def calculate_cost(
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> dict | None:
    """Returns per-reply cost in USD, or None if this model isn't in pricing.json.

    Cached tokens are billed at their own rates, so they're priced separately -
    counting them at the full input rate would overstate the cost of a cached run.
    `input_tokens` must be the UNCACHED input only.
    """
    pricing = load_pricing()
    rates = pricing.get(provider, {}).get(model)
    if rates is None:
        return None

    input_cost = input_tokens * rates["input"] / 1_000_000
    output_cost = output_tokens * rates["output"] / 1_000_000
    read_cost = cache_read_tokens * rates.get("cache_read", rates["input"]) / 1_000_000
    write_cost = cache_write_tokens * rates.get("cache_write", rates["input"]) / 1_000_000

    return {
        "input_usd": round(input_cost, 8),
        "output_usd": round(output_cost, 8),
        "cache_read_usd": round(read_cost, 8),
        "cache_write_usd": round(write_cost, 8),
        "total_usd": round(input_cost + output_cost + read_cost + write_cost, 8),
    }
