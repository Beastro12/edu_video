"""One place that builds the Google GenAI client (still images, Veo clips)."""
import config
import retries


def make(httpx_client=None):
    """The SDK retries 408/429/5xx, timeouts and failed connects itself, with the same
    jittered exponential backoff as retries.py; other errors raise at once."""
    from google import genai
    from google.genai import types

    retry = types.HttpRetryOptions(attempts=config.MAX_RETRIES + 1, initial_delay=config.RETRY_BASE_S,
                                   max_delay=config.RETRY_MAX_S, jitter=config.RETRY_JITTER_S,
                                   http_status_codes=sorted(retries.TRANSIENT_STATUS))
    return genai.Client(api_key=config.GOOGLE_API_KEY, http_options=types.HttpOptions(
        retry_options=retry, timeout=int(config.GOOGLE_TIMEOUT_S * 1000), httpx_client=httpx_client))


def start_job_options():
    """For a POST that starts a paid job (Veo): retry only a 429, which means the job was
    refused. A 5xx may arrive after Google accepted it, and a retry would pay twice (D11)."""
    from google.genai import types

    return types.HttpOptions(retry_options=types.HttpRetryOptions(
        attempts=config.MAX_RETRIES + 1, initial_delay=config.RETRY_BASE_S, max_delay=config.RETRY_MAX_S,
        jitter=config.RETRY_JITTER_S, http_status_codes=[429]))
