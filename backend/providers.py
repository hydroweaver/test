"""Thin adapters around each provider's SDK.

Each function sends one message and returns (reply_text, input_tokens, output_tokens),
reading the token counts straight from the provider's own response - never estimated.
"""

import os


class ProviderError(Exception):
    pass


def _require_key(env_var: str, provider: str) -> str:
    key = os.environ.get(env_var)
    if not key:
        raise ProviderError(
            f"{env_var} is not set. Add it to backend/.env to use the '{provider}' provider."
        )
    return key


def call_anthropic(message: str, model: str, system: str | None) -> tuple[str, int, int]:
    import anthropic

    client = anthropic.Anthropic(api_key=_require_key("ANTHROPIC_API_KEY", "anthropic"))
    response = client.messages.create(
        model=model,
        max_tokens=4096,
        system=system or anthropic.NOT_GIVEN,
        messages=[{"role": "user", "content": message}],
    )
    reply = "".join(block.text for block in response.content if block.type == "text")
    return reply, response.usage.input_tokens, response.usage.output_tokens


def call_openai(message: str, model: str, system: str | None) -> tuple[str, int, int]:
    from openai import OpenAI

    client = OpenAI(api_key=_require_key("OPENAI_API_KEY", "openai"))
    kwargs = {}
    if system:
        kwargs["instructions"] = system
    response = client.responses.create(
        model=model,
        input=message,
        **kwargs,
    )
    return response.output_text, response.usage.input_tokens, response.usage.output_tokens


def call_gemini(message: str, model: str, system: str | None) -> tuple[str, int, int]:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=_require_key("GEMINI_API_KEY", "gemini"))
    config = types.GenerateContentConfig(system_instruction=system) if system else None
    response = client.models.generate_content(
        model=model,
        contents=message,
        config=config,
    )
    usage = response.usage_metadata
    return response.text, usage.prompt_token_count, usage.candidates_token_count


PROVIDERS = {
    "anthropic": call_anthropic,
    "openai": call_openai,
    "gemini": call_gemini,
}
