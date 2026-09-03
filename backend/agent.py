"""One tool-calling loop per provider, all logging total tokens across every turn.

The point of running a real tool loop (vs. stuffing website text into the prompt) is
to measure honest token/cost for a tool-using agent - so every turn's input/output
tokens get summed here, not just the final one.
"""

import json
import os
from typing import NamedTuple

import db
from providers import ProviderError
from tools import SEARCH_WEBSITE_SCHEMA, search_website

MAX_TOOL_TURNS = int(os.environ.get("MAX_TOOL_TURNS", "3"))


class AgentResult(NamedTuple):
    reply: str
    input_tokens: int
    output_tokens: int
    tool_call_count: int
    turn_count: int


def _key(provider: str) -> str:
    key = db.resolve_api_key(provider)
    if not key:
        raise ProviderError(
            f"No API key configured for '{provider}'. Set it via the admin page or "
            f"the {provider.upper()}_API_KEY env var."
        )
    return key


def _run_anthropic_agent(message: str, model: str, system: str | None, history: list[dict]) -> AgentResult:
    import anthropic

    client = anthropic.Anthropic(api_key=_key("anthropic"))
    tools = [{
        "name": SEARCH_WEBSITE_SCHEMA["name"],
        "description": SEARCH_WEBSITE_SCHEMA["description"],
        "input_schema": SEARCH_WEBSITE_SCHEMA["parameters"],
    }]
    messages = [{"role": h["role"], "content": h["content"]} for h in history]
    messages.append({"role": "user", "content": message})

    input_tokens = output_tokens = tool_calls = 0
    for turn in range(MAX_TOOL_TURNS + 1):
        force_text = turn == MAX_TOOL_TURNS
        response = client.messages.create(
            model=model,
            max_tokens=4096,
            system=system or anthropic.NOT_GIVEN,
            messages=messages,
            tools=[] if force_text else tools,
            tool_choice={"type": "none"} if force_text else anthropic.NOT_GIVEN,
        )
        input_tokens += response.usage.input_tokens
        output_tokens += response.usage.output_tokens

        tool_uses = [b for b in response.content if b.type == "tool_use"]
        if not tool_uses or force_text:
            reply = "".join(b.text for b in response.content if b.type == "text")
            return AgentResult(reply, input_tokens, output_tokens, tool_calls, turn + 1)

        messages.append({"role": "assistant", "content": response.content})
        results = []
        for tu in tool_uses:
            result_text = search_website(tu.input.get("query", ""))
            tool_calls += 1
            results.append({"type": "tool_result", "tool_use_id": tu.id, "content": result_text})
        messages.append({"role": "user", "content": results})

    return AgentResult("(no response)", input_tokens, output_tokens, tool_calls, MAX_TOOL_TURNS + 1)


def _run_openai_agent(message: str, model: str, system: str | None, history: list[dict]) -> AgentResult:
    from openai import OpenAI

    client = OpenAI(api_key=_key("openai"))
    tools = [{
        "type": "function",
        "name": SEARCH_WEBSITE_SCHEMA["name"],
        "description": SEARCH_WEBSITE_SCHEMA["description"],
        "parameters": SEARCH_WEBSITE_SCHEMA["parameters"],
    }]
    input_items = [{"role": h["role"], "content": h["content"]} for h in history]
    input_items.append({"role": "user", "content": message})

    input_tokens = output_tokens = tool_calls = 0
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
        input_tokens += response.usage.input_tokens
        output_tokens += response.usage.output_tokens

        function_calls = [item for item in response.output if item.type == "function_call"]
        if not function_calls or force_text:
            return AgentResult(response.output_text, input_tokens, output_tokens, tool_calls, turn + 1)

        input_items += response.output
        for call in function_calls:
            args = json.loads(call.arguments or "{}")
            result_text = search_website(args.get("query", ""))
            tool_calls += 1
            input_items.append({
                "type": "function_call_output",
                "call_id": call.call_id,
                "output": result_text,
            })

    return AgentResult("(no response)", input_tokens, output_tokens, tool_calls, MAX_TOOL_TURNS + 1)


def _run_gemini_agent(message: str, model: str, system: str | None, history: list[dict]) -> AgentResult:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=_key("gemini"))
    tool = types.Tool(function_declarations=[types.FunctionDeclaration(
        name=SEARCH_WEBSITE_SCHEMA["name"],
        description=SEARCH_WEBSITE_SCHEMA["description"],
        parameters=SEARCH_WEBSITE_SCHEMA["parameters"],
    )])

    contents = []
    for h in history:
        role = "model" if h["role"] == "assistant" else "user"
        contents.append(types.Content(role=role, parts=[types.Part(text=h["content"])]))
    contents.append(types.Content(role="user", parts=[types.Part(text=message)]))

    input_tokens = output_tokens = tool_calls = 0
    for turn in range(MAX_TOOL_TURNS + 1):
        force_text = turn == MAX_TOOL_TURNS
        config = types.GenerateContentConfig(
            system_instruction=system,
            tools=None if force_text else [tool],
        )
        response = client.models.generate_content(model=model, contents=contents, config=config)
        usage = response.usage_metadata
        input_tokens += usage.prompt_token_count or 0
        output_tokens += usage.candidates_token_count or 0

        parts = response.candidates[0].content.parts or []
        function_calls = [p for p in parts if p.function_call]
        if not function_calls or force_text:
            return AgentResult(response.text or "", input_tokens, output_tokens, tool_calls, turn + 1)

        contents.append(response.candidates[0].content)
        response_parts = []
        for p in function_calls:
            query = dict(p.function_call.args or {}).get("query", "")
            result_text = search_website(query)
            tool_calls += 1
            response_parts.append(types.Part.from_function_response(
                name=p.function_call.name, response={"result": result_text}
            ))
        contents.append(types.Content(role="user", parts=response_parts))

    return AgentResult("(no response)", input_tokens, output_tokens, tool_calls, MAX_TOOL_TURNS + 1)


AGENTS = {
    "anthropic": _run_anthropic_agent,
    "openai": _run_openai_agent,
    "gemini": _run_gemini_agent,
}


def run_agent(provider: str, message: str, model: str, system: str | None, history: list[dict]) -> AgentResult:
    return AGENTS[provider](message, model, system, history)
