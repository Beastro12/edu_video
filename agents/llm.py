"""Thin Claude wrapper. Forced tool use gives us reliable structured JSON.
Every call is budget-checked (worst case: max_tokens of output) and logged to the ledger."""
import base64
import json

import anthropic

import config
import ledger


def make_client(**kwargs) -> anthropic.Anthropic:
    # The SDK retries 408/409/429/5xx and timeouts with jittered exponential backoff and
    # honours retry-after; other errors raise at once.
    return anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY, max_retries=config.MAX_RETRIES, **kwargs)


_client = make_client()


def _create(est_in: int | None = None, **kwargs):
    if est_in is None:
        est_in = ledger.claude_input_estimate(json.dumps(kwargs, default=str))
    with ledger.reserve("anthropic", ledger.claude_eur(config.CLAUDE_MODEL, est_in, kwargs["max_tokens"])):
        resp = _client.messages.create(model=config.CLAUDE_MODEL, **kwargs)
        usage = resp.usage
        ledger.record("anthropic", config.CLAUDE_MODEL,
                      {"input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens},
                      ledger.claude_eur(config.CLAUDE_MODEL, usage.input_tokens, usage.output_tokens))
    return resp


def structured(system: str, prompt: str, tool_name: str, schema: dict,
               max_tokens: int = 8000, images: list[bytes] = ()) -> dict:
    """images: JPEG bytes, shown to Claude before the prompt."""
    content = [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                            "data": base64.standard_b64encode(img).decode()}}
               for img in images] + [{"type": "text", "text": prompt}]
    # Estimate from the text, plus a fixed size per image: base64 read as text would look like
    # a hundred thousand tokens and trip the budget check for nothing.
    est_in = ledger.claude_input_estimate(system, prompt, json.dumps(schema)) + len(images) * config.EST_IMAGE_TOKENS
    resp = _create(
        est_in=est_in,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": content if images else prompt}],
        tools=[{"name": tool_name, "description": f"Return the {tool_name}.",
                "input_schema": schema}],
        tool_choice={"type": "tool", "name": tool_name},
    )
    if getattr(resp, "stop_reason", None) == "max_tokens":
        raise RuntimeError(f"Claude's {tool_name} was cut off at max_tokens={max_tokens}")
    for block in resp.content:
        if block.type == "tool_use":
            return block.input
    raise RuntimeError(f"Claude returned no {tool_name} tool call")


def text(system: str, messages: list[dict], max_tokens: int = 8000) -> str:
    resp = _create(max_tokens=max_tokens, system=system, messages=messages)
    return "".join(b.text for b in resp.content if b.type == "text")
