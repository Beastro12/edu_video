"""Retries for transient API errors: jittered exponential backoff, at most MAX_RETRIES.
The Anthropic and Google SDKs run their own retry loops, configured from the same values
(agents/llm.py, agents/google_client.py); this helper covers plain `requests` calls
(ElevenLabs)."""
import random
import time
from collections.abc import Callable
from typing import TypeVar

import requests

import config

TRANSIENT_STATUS = {408, 429, 500, 502, 503, 504}
T = TypeVar("T")


class TransientError(RuntimeError):
    """A response worth retrying (rate limit, server error). Carries Retry-After if given."""

    def __init__(self, message: str, retry_after_s: float | None = None):
        super().__init__(message)
        self.retry_after_s = retry_after_s


def backoff_s(retry: int) -> float:
    """Wait before retry number `retry` (0-based)."""
    wait = config.RETRY_BASE_S * 2 ** retry + random.uniform(0, config.RETRY_JITTER_S)
    return min(wait, config.RETRY_MAX_S)


def retry_after_s(headers) -> float | None:
    try:
        return float(headers.get("Retry-After"))
    except (TypeError, ValueError):
        return None


def is_transient(e: BaseException) -> bool:
    if isinstance(e, TransientError | requests.Timeout | requests.ConnectionError):
        return True
    try:  # Google SDK calls that bypass its own retry loop (file downloads)
        import httpx
        from google.genai import errors
    except ImportError:
        return False
    return (isinstance(e, errors.APIError) and e.code in TRANSIENT_STATUS) or \
        isinstance(e, httpx.TimeoutException | httpx.ConnectError)


def call(fn: Callable[[], T], what: str) -> T:
    """Run fn(); on a transient failure wait and try again, up to MAX_RETRIES times.
    Any other exception propagates at once."""
    for retry in range(config.MAX_RETRIES + 1):
        try:
            return fn()
        except Exception as e:
            if not is_transient(e) or retry == config.MAX_RETRIES:
                raise
            wait = min(max(getattr(e, "retry_after_s", None) or backoff_s(retry), 0), config.RETRY_MAX_S)
            print(f"    {what}: {str(e)[:120]}; retry {retry + 1}/{config.MAX_RETRIES} in {wait:.1f}s")
            time.sleep(wait)
    raise AssertionError("unreachable")
