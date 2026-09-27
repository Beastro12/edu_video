"""Thin Claude wrapper. Forced tool use gives us reliable structured JSON."""
import anthropic

import config

_client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)


def structured(system: str, prompt: str, tool_name: str, schema: dict,
               max_tokens: int = 8000) -> dict:
    resp = _client.messages.create(
        model=config.CLAUDE_MODEL,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": prompt}],
        tools=[{"name": tool_name, "description": f"Return the {tool_name}.",
                "input_schema": schema}],
        tool_choice={"type": "tool", "name": tool_name},
    )
    for block in resp.content:
        if block.type == "tool_use":
            return block.input
    raise RuntimeError(f"Claude returned no {tool_name} tool call")


def text(system: str, messages: list[dict], max_tokens: int = 8000) -> str:
    resp = _client.messages.create(
        model=config.CLAUDE_MODEL, max_tokens=max_tokens,
        system=system, messages=messages,
    )
    return "".join(b.text for b in resp.content if b.type == "text")
