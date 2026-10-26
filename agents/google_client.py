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
    """For the POST that starts a paid job (Veo): the SDK makes exactly one attempt. Its retry
    predicate (google-genai 2.25.0, _api_client.py:575) retries a transport timeout or connect error
    WHATEVER `http_status_codes` says, and a timeout can arrive after Google accepted the job: a retry
    would start and bill a second one (D11). The one retry that is safe, a 429 (the job was refused),
    is made by ai_video through retries.call. `retry_options` must be given: an HttpOptions without
    it falls back to the client's own loop, which retries 5xx."""
    from google.genai import types

    return types.HttpOptions(retry_options=types.HttpRetryOptions(attempts=1))
