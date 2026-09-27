"""Thin Claude wrapper. Forced tool use gives us reliable structured JSON.
Every call is budget-checked (worst case: max_tokens of output) and logged to the ledger."""
import json

import anthropic

import config
import ledger

_client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)


def _create(**kwargs):
    est_in = ledger.claude_input_estimate(json.dumps(kwargs, default=str))
    ledger.check("anthropic", ledger.claude_eur(config.CLAUDE_MODEL, est_in, kwargs["max_tokens"]))
    resp = _client.messages.create(model=config.CLAUDE_MODEL, **kwargs)
    usage = resp.usage
    ledger.record("anthropic", config.CLAUDE_MODEL,
                  {"input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens},
                  ledger.claude_eur(config.CLAUDE_MODEL, usage.input_tokens, usage.output_tokens))
    return resp


def structured(system: str, prompt: str, tool_name: str, schema: dict,
               max_tokens: int = 8000) -> dict:
    resp = _create(
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
    resp = _create(max_tokens=max_tokens, system=system, messages=messages)
    return "".join(b.text for b in resp.content if b.type == "text")
