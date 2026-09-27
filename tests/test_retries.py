"""P0-4: 429 / 5xx / timeouts retry with jittered exponential backoff (at most 4 retries);
anything else fails on the first attempt. The real Anthropic and Google SDKs run against a
mock HTTP transport, so their own retry code is what's being tested."""
import functools
import json
from types import SimpleNamespace

import anthropic
import httpx
import httpx2
import pytest
import requests
from google.genai import errors

import config
import ledger
import retries
from agents import ai_video, google_client, llm, voice

ATTEMPTS = config.MAX_RETRIES + 1


@pytest.fixture
def fast(monkeypatch):
    """Keep backoff real but tiny, and remember every wait."""
    waits = []
    monkeypatch.setattr(config, "RETRY_BASE_S", 0.001)
    monkeypatch.setattr(config, "RETRY_JITTER_S", 0.001)
    monkeypatch.setattr(retries.time, "sleep", waits.append)
    return waits


# --- ElevenLabs (requests + our helper) ----------------------------------------------------

def scripted_post(outcomes, seen):
    """Fake requests.post answering with the given status codes / exceptions in order."""
    def post(url, headers, json, timeout):
        seen.append(json["text"])
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return SimpleNamespace(status_code=outcome, content=b"mp3", text=f"status {outcome}",
                               headers={"Retry-After": "7"} if outcome == 429 else {})
    return post


def test_elevenlabs_retries_rate_limits_server_errors_and_timeouts(fast, monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(voice.requests, "post", scripted_post(
        [429, 503, requests.Timeout("slow"), requests.ConnectionError("reset"), 200], seen))
    out = voice.narrate("A calm line.", tmp_path)
    assert out.read_bytes() == b"mp3"
    assert len(seen) == 5
    assert fast[0] == 7, "Retry-After from a 429 is honoured"
    assert fast[1] < fast[2] < fast[3], "backoff grows"


def test_elevenlabs_gives_up_after_four_retries(fast, monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(voice.requests, "post", scripted_post([502] * 10, seen))
    with pytest.raises(RuntimeError, match="502"):
        voice.narrate("A calm line.", tmp_path)
    assert len(seen) == ATTEMPTS


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
def test_elevenlabs_non_transient_errors_fail_fast(fast, monkeypatch, tmp_path, status):
    seen = []
    monkeypatch.setattr(voice.requests, "post", scripted_post([status, 200], seen))
    with pytest.raises(RuntimeError, match=str(status)):
        voice.narrate("A calm line.", tmp_path)
    assert len(seen) == 1 and fast == []


def test_elevenlabs_ledger_counts_one_call_however_many_attempts(fast, monkeypatch, tmp_path):
    monkeypatch.setattr(voice.requests, "post", scripted_post([503, 503, 200], []))
    voice.narrate("A calm line.", tmp_path)
    assert len(ledger_entries()) == 1
    monkeypatch.setattr(voice.requests, "post", scripted_post([503] * 10, []))
    with pytest.raises(RuntimeError):
        voice.narrate("Another line.", tmp_path)
    monkeypatch.setattr(voice.requests, "post", scripted_post([400], []))
    with pytest.raises(RuntimeError):
        voice.narrate("A third line.", tmp_path)
    assert len(ledger_entries()) == 1, "failed calls are not recorded"


def test_negative_retry_after_does_not_crash(fast, monkeypatch):
    attempts = []

    def flaky():
        attempts.append(1)
        if len(attempts) == 1:
            raise retries.TransientError("429", retry_after_s=-5)
        return "ok"

    assert retries.call(flaky, "test") == "ok"
    assert fast[0] >= 0


def ledger_entries():
    path = ledger.ledger_path()
    return path.read_text().splitlines() if path.exists() else []


def test_backoff_is_jittered_and_capped(monkeypatch):
    monkeypatch.setattr(config, "RETRY_BASE_S", 1.0)
    monkeypatch.setattr(config, "RETRY_JITTER_S", 1.0)
    monkeypatch.setattr(config, "RETRY_MAX_S", 5.0)
    waits = [retries.backoff_s(n) for n in range(6) for _ in range(20)]
    assert min(waits[:20]) >= 1.0 and max(waits[:20]) <= 2.0
    assert len(set(waits[:20])) > 1, "jittered"
    assert max(waits) <= 5.0


# --- Anthropic (SDK retries) ------------------------------------------------------------------

MESSAGE = {"id": "msg_1", "type": "message", "role": "assistant", "model": "m",
           "content": [{"type": "tool_use", "id": "tu_1", "name": "thing", "input": {"ok": True}}],
           "stop_reason": "tool_use", "stop_sequence": None,
           "usage": {"input_tokens": 10, "output_tokens": 5}}


def claude_via(monkeypatch, outcomes):
    """Point llm at a real SDK client whose HTTP goes to a scripted mock transport."""
    seen = []

    def handler(request):
        seen.append(request.url.path)
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        body = MESSAGE if outcome == 200 else {"type": "error", "error": {"type": "x", "message": "x"}}
        return httpx2.Response(outcome, json=body, headers={"retry-after-ms": "1"})

    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "offline-test")
    client = llm.make_client(http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(handler)))
    monkeypatch.setattr(llm, "_client", client)
    return seen


def test_claude_retries_transient_errors(real_anthropic, monkeypatch):
    seen = claude_via(monkeypatch, [httpx2.ReadTimeout("slow"), 429, 500, 529, 200])  # timeout first: SDK backoff is short then
    assert llm.structured("s", "p", "thing", {"type": "object"}) == {"ok": True}
    assert len(seen) == 5
    assert len(ledger_entries()) == 1, "one call recorded, not one per attempt"


def test_claude_gives_up_after_four_retries(real_anthropic, monkeypatch):
    seen = claude_via(monkeypatch, [503] * 10)
    with pytest.raises(anthropic.InternalServerError):
        llm.structured("s", "p", "thing", {"type": "object"})
    assert len(seen) == ATTEMPTS
    assert ledger_entries() == []


def test_claude_bad_request_fails_fast(real_anthropic, monkeypatch):
    seen = claude_via(monkeypatch, [400, 200])
    with pytest.raises(anthropic.BadRequestError):
        llm.structured("s", "p", "thing", {"type": "object"})
    assert len(seen) == 1


# --- Google (SDK retries) ---------------------------------------------------------------------

def google_via(monkeypatch, outcomes):
    """A real google-genai client (retry config from google_client.make) on a mock transport."""
    seen = []

    def handler(request):
        seen.append(request.url.path)
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return httpx.Response(outcome, content=json.dumps({"error": {"code": outcome, "message": "x"}}))

    monkeypatch.setattr(config, "GOOGLE_API_KEY", "offline-test")
    mock = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(google_client, "make", functools.partial(google_client.make, httpx_client=mock))
    return seen


def test_google_retries_transient_errors_then_fails_fast_on_a_client_error(real_google, fast, monkeypatch):
    seen = google_via(monkeypatch, [503, 429, httpx.ReadTimeout("slow"), 400, 200])
    with pytest.raises(errors.ClientError, match="400"):
        google_client.make().models.generate_content(model="m", contents="p")
    assert len(seen) == 4, "three transient failures retried, the 400 not retried"


def test_google_gives_up_after_four_retries(real_google, fast, monkeypatch):
    seen = google_via(monkeypatch, [500] * 10)
    with pytest.raises(errors.ServerError, match="500"):
        google_client.make().models.generate_content(model="m", contents="p")
    assert len(seen) == ATTEMPTS


def test_google_client_has_a_request_timeout(real_google, monkeypatch):
    monkeypatch.setattr(config, "GOOGLE_API_KEY", "offline-test")
    assert google_client.make()._api_client._http_options.timeout == config.GOOGLE_TIMEOUT_S * 1000


def test_veo_start_is_retried_only_when_refused(real_google, fast, monkeypatch, tmp_path):
    """A 5xx may come after Google accepted the job: retrying could start (and bill) a second one."""
    seen = google_via(monkeypatch, [503, 200])
    with pytest.raises(errors.ServerError):
        ai_video.render_scene({"visual_description": "a dark sky"}, tmp_path)
    assert len(seen) == 1
    seen = google_via(monkeypatch, [429, 429, 400])
    with pytest.raises(errors.ClientError):
        ai_video.render_scene({"visual_description": "a dark sky"}, tmp_path)
    assert len(seen) == 3
    assert ledger_entries() == [], "a job that never started costs nothing"


class FakeVeo:
    """Veo at the SDK level: the job starts, then polling or downloading misbehaves."""

    def __init__(self, polls, downloads):
        self.polls, self.downloads, self.download_calls = polls, downloads, 0
        video = SimpleNamespace(save=lambda path: open(path, "wb").write(b"mp4"))
        self.done = SimpleNamespace(done=True, error=None, response=SimpleNamespace(generated_videos=[
            SimpleNamespace(video=video)]))
        self.models = SimpleNamespace(generate_videos=lambda **kw: SimpleNamespace(done=False))
        self.operations = SimpleNamespace(get=self.get)
        self.files = SimpleNamespace(download=self.download)

    def get(self, op):
        outcome = self.polls.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return self.done

    def download(self, file):
        self.download_calls += 1
        outcome = self.downloads.pop(0)
        if isinstance(outcome, Exception):
            raise outcome


def veo_with(monkeypatch, fake):
    monkeypatch.setattr(config, "GOOGLE_API_KEY", "offline-test")
    monkeypatch.setattr(google_client, "make", lambda: fake)
    monkeypatch.setattr(ai_video.time, "sleep", lambda s: None)


def test_veo_job_lost_while_polling_is_recorded_as_spent(fast, monkeypatch, tmp_path):
    veo_with(monkeypatch, FakeVeo(polls=[errors.ServerError(503, {"error": {"message": "x"}})], downloads=[]))
    with pytest.raises(errors.ServerError):
        ai_video.render_scene({"visual_description": "a dark sky"}, tmp_path)
    assert len(ledger_entries()) == 1


def test_veo_download_is_retried_so_a_paid_clip_is_not_lost(fast, monkeypatch, tmp_path):
    fake = FakeVeo(polls=["done"], downloads=[errors.ServerError(503, {"error": {"message": "x"}}),
                                              httpx.ReadTimeout("slow"), None])
    veo_with(monkeypatch, fake)
    out = ai_video.render_scene({"visual_description": "a dark sky"}, tmp_path)
    assert out.read_bytes() == b"mp4"
    assert fake.download_calls == 3
    assert len(ledger_entries()) == 1
