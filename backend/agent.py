"""One tool-calling loop per provider, all logging total tokens across every turn.

Every turn's tokens are summed so one logged row = one incoming message and the
single reply it produced, including all the tool round trips in between.
"""

import json
import os
from typing import NamedTuple

import db
from providers import ProviderError
from tools import TOOL_SCHEMAS, ToolBox

MAX_TOOL_TURNS = int(os.environ.get("MAX_TOOL_TURNS", "2"))

# Prompt caching is on by default: the system prompt + tool schemas are a ~2k token
# static prefix resent on every turn, which is exactly what caching is for. Set
# PROMPT_CACHING=0 to measure the uncached cost for comparison.
PROMPT_CACHING = os.environ.get("PROMPT_CACHING", "1") != "0"


class AgentResult(NamedTuple):
    reply: str
    input_tokens: int          # uncached input only
    output_tokens: int
    tool_call_count: int
    turn_count: int
    media_files: list[str]
    cache_read_tokens: int = 0   # served from cache (cheap)
    cache_write_tokens: int = 0  # written into cache (Anthropic charges a premium)


def _key(provider: str) -> str:
    key = db.resolve_api_key(provider)
    if not key:
        raise ProviderError(
            f"No API key configured for '{provider}'. Set it via the admin page or "
            f"the {provider.upper()}_API_KEY env var."
        )
    return key


def _run_anthropic_agent(message, model, system, history, image, toolbox) -> AgentResult:
    import anthropic

    client = anthropic.Anthropic(api_key=_key("anthropic"))
    tools = [
        {"name": t["name"], "description": t["description"], "input_schema": t["parameters"]}
        for t in TOOL_SCHEMAS
    ]
    messages = [{"role": h["role"], "content": h["content"]} for h in history]
    if image:
        messages.append({"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": image["media_type"], "data": image["b64"]}},
            {"type": "text", "text": message},
        ]})
    else:
        messages.append({"role": "user", "content": message})

    # A cache breakpoint on the system block caches everything before it in the
    # render order (tools -> system), i.e. the whole static prefix.
    if system and PROMPT_CACHING:
        system_param = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
    else:
        system_param = system or anthropic.NOT_GIVEN

    input_tokens = output_tokens = tool_calls = cache_read = cache_write = 0
    for turn in range(MAX_TOOL_TURNS + 1):
        force_text = turn == MAX_TOOL_TURNS
        response = client.messages.create(
            model=model,
            max_tokens=600,
            system=system_param,
            messages=messages,
            tools=[] if force_text else tools,
            tool_choice={"type": "none"} if force_text else anthropic.NOT_GIVEN,
        )
        input_tokens += response.usage.input_tokens
        output_tokens += response.usage.output_tokens
        cache_read += getattr(response.usage, "cache_read_input_tokens", 0) or 0
        cache_write += getattr(response.usage, "cache_creation_input_tokens", 0) or 0

        tool_uses = [b for b in response.content if b.type == "tool_use"]
        if not tool_uses or force_text:
            reply = "".join(b.text for b in response.content if b.type == "text")
            return AgentResult(reply, input_tokens, output_tokens, tool_calls, turn + 1,
                               toolbox.pending_media, cache_read, cache_write)

        messages.append({"role": "assistant", "content": response.content})
        results = []
        for tu in tool_uses:
            results.append({
                "type": "tool_result", "tool_use_id": tu.id,
                "content": toolbox.call(tu.name, dict(tu.input or {})),
            })
            tool_calls += 1
        messages.append({"role": "user", "content": results})

    return AgentResult("", input_tokens, output_tokens, tool_calls, MAX_TOOL_TURNS + 1,
                       toolbox.pending_media, cache_read, cache_write)


def _run_openai_agent(message, model, system, history, image, toolbox) -> AgentResult:
    from openai import OpenAI

    client = OpenAI(api_key=_key("openai"))
    tools = [
        {"type": "function", "name": t["name"], "description": t["description"], "parameters": t["parameters"]}
        for t in TOOL_SCHEMAS
    ]
    input_items = [{"role": h["role"], "content": h["content"]} for h in history]
    if image:
        input_items.append({"role": "user", "content": [
            {"type": "input_text", "text": message},
            {"type": "input_image", "image_url": f"data:{image['media_type']};base64,{image['b64']}"},
        ]})
    else:
        input_items.append({"role": "user", "content": message})

    # OpenAI caches automatically (no opt-in) for prompts over ~1k tokens.
    input_tokens = output_tokens = tool_calls = cache_read = 0
    for turn in range(MAX_TOOL_TURNS + 1):
        force_text = turn == MAX_TOOL_TURNS
        kwargs = {"instructions": system} if system else {}
        response = client.responses.create(
            model=model,
            input=input_items,
            tools=[] if force_text else tools,
            tool_choice="none" if force_text else "auto",
            **kwargs,
        )
        details = getattr(response.usage, "input_tokens_details", None)
        cached = getattr(details, "cached_tokens", 0) or 0
        # OpenAI's input_tokens INCLUDES the cached ones - subtract so they aren't
        # billed twice, once at full rate and once at the cache rate.
        input_tokens += max(response.usage.input_tokens - cached, 0)
        cache_read += cached
        output_tokens += response.usage.output_tokens

        function_calls = [item for item in response.output if item.type == "function_call"]
        if not function_calls or force_text:
            return AgentResult(
                response.output_text, input_tokens, output_tokens, tool_calls, turn + 1,
                toolbox.pending_media, cache_read, 0,
            )

        input_items += response.output
        for call in function_calls:
            args = json.loads(call.arguments or "{}")
            input_items.append({
                "type": "function_call_output", "call_id": call.call_id,
                "output": toolbox.call(call.name, args),
            })
            tool_calls += 1

    return AgentResult("", input_tokens, output_tokens, tool_calls, MAX_TOOL_TURNS + 1,
                       toolbox.pending_media, cache_read, 0)


def _run_gemini_agent(message, model, system, history, image, toolbox) -> AgentResult:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=_key("gemini"))
    tool = types.Tool(function_declarations=[
        types.FunctionDeclaration(name=t["name"], description=t["description"], parameters=t["parameters"])
        for t in TOOL_SCHEMAS
    ])

    contents = []
    for h in history:
        role = "model" if h["role"] == "assistant" else "user"
        contents.append(types.Content(role=role, parts=[types.Part(text=h["content"])]))
    parts = [types.Part(text=message)]
    if image:
        import base64 as _b64
        parts.insert(0, types.Part.from_bytes(
            data=_b64.b64decode(image["b64"]), mime_type=image["media_type"]
        ))
    contents.append(types.Content(role="user", parts=parts))

    # Gemini caches implicitly on current models - nothing to opt into.
    input_tokens = output_tokens = tool_calls = cache_read = 0
    for turn in range(MAX_TOOL_TURNS + 1):
        force_text = turn == MAX_TOOL_TURNS
        config = types.GenerateContentConfig(
            system_instruction=system,
            tools=None if force_text else [tool],
        )
        response = client.models.generate_content(model=model, contents=contents, config=config)
        usage = response.usage_metadata
        cached = getattr(usage, "cached_content_token_count", 0) or 0
        # prompt_token_count includes cached tokens - subtract so they're priced once.
        input_tokens += max((usage.prompt_token_count or 0) - cached, 0)
        cache_read += cached
        output_tokens += usage.candidates_token_count or 0

        response_parts = response.candidates[0].content.parts or []
        function_calls = [p for p in response_parts if p.function_call]
        if not function_calls or force_text:
            return AgentResult(
                response.text or "", input_tokens, output_tokens, tool_calls, turn + 1,
                toolbox.pending_media, cache_read, 0,
            )

        contents.append(response.candidates[0].content)
        reply_parts = []
        for p in function_calls:
            result = toolbox.call(p.function_call.name, dict(p.function_call.args or {}))
            tool_calls += 1
            reply_parts.append(types.Part.from_function_response(
                name=p.function_call.name, response={"result": result}
            ))
        contents.append(types.Content(role="user", parts=reply_parts))

    return AgentResult("", input_tokens, output_tokens, tool_calls, MAX_TOOL_TURNS + 1, toolbox.pending_media)


AGENTS = {
    "anthropic": _run_anthropic_agent,
    "openai": _run_openai_agent,
    "gemini": _run_gemini_agent,
}


def run_agent(
    provider: str,
    message: str,
    model: str,
    system: str | None,
    history: list[dict],
    image: dict | None = None,
    caller_phone: str | None = None,
) -> AgentResult:
    toolbox = ToolBox(caller_phone=caller_phone)
    return AGENTS[provider](message, model, system, history, image, toolbox)
