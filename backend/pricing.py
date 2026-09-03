import json
import os

_PRICING_PATH = os.path.join(os.path.dirname(__file__), "pricing.json")


def load_pricing() -> dict:
    with open(_PRICING_PATH) as f:
        data = json.load(f)
    data.pop("_comment", None)
    return data


def calculate_cost(provider: str, model: str, input_tokens: int, output_tokens: int) -> dict | None:
    """Returns per-reply cost in USD, or None if this model isn't in pricing.json."""
    pricing = load_pricing()
    rates = pricing.get(provider, {}).get(model)
    if rates is None:
        return None

    input_cost = input_tokens * rates["input"] / 1_000_000
    output_cost = output_tokens * rates["output"] / 1_000_000
    return {
        "input_usd": round(input_cost, 8),
        "output_usd": round(output_cost, 8),
        "total_usd": round(input_cost + output_cost, 8),
    }
