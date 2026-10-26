"""P0-9: the POST that starts a Veo job is never retried by the SDK, and only a 429 is retried by us.

google-genai 2.25.0 retries a transport timeout or connect error WHATEVER its status-code list says
(`_api_client.py:575-578`), so `start_job_options()` with `http_status_codes=[429]` still sent the start
again after a ReadTimeout, and Google had maybe accepted the first: two jobs, one billed in the ledger (D11).

Every test here drives the REAL SDK with its OWN httpx client (D30), faked below it at
`httpx.HTTPTransport.handle_request`, under a socket guard. POSTs are counted by the
`:predictLongRunning` path."""
import signal
import socket
import threading
import time
from types import SimpleNamespace

import httpx
import pytest
from google.genai import errors as genai_errors
from google.genai import types

import config
import ledger
import pipeline
import retries
from agents import ai_video, google_client

REAL_SLEEP = time.sleep
SCENE = {"id": 3, "concept": "stars", "narration": "n", "visual_type": "ai_video", "visual_description": "a dark sky"}
OPERATION = {"name": "models/veo/operations/op1", "done": True, "response": {"generateVideoResponse": {
    "generatedSamples": [{"video": {"uri": "https://generativelanguage.googleapis.com/v1beta/files/abc:download?alt=media"}}]}}}
PENDING = {"name": "models/veo/operations/op1", "done": False}


class SocketOpened(BaseException):
    """Not an Exception: no retry loop or fallback may swallow it."""


def refusal(status, headers=None):
    return httpx.Response(status, json={"error": {"code": status, "message": "x", "status": "X"}}, headers=headers or {})


@pytest.fixture
def veo(monkeypatch, tmp_path):
    """Control of the fake Google: `.starts` is a list of what each start POST answers (a status, an
    exception to raise, or an httpx.Response; 200 starts a job), `.polls` the same for operations.get
    (default: the finished job), `.posts` / `.gets` count what reached the transport, `.sleeps` what was waited."""
    ctl = SimpleNamespace(starts=[], polls=[], downloads_fail=[], posts=0, gets=0, downloads=0, sleeps=[], sockets=[],
                          on_post=None)

    def no_connect(self, address):
        if self.family in (socket.AF_INET, socket.AF_INET6):
            ctl.sockets.append(address)
            raise SocketOpened(address)

    def handle_request(self, request):
        url = str(request.url)
        if ":predictLongRunning" in url:
            ctl.posts += 1
            if ctl.on_post:
                ctl.on_post(ctl.posts)
            outcome = ctl.starts.pop(0)
        elif "/operations/op1" in url:
            ctl.gets += 1
            outcome = ctl.polls.pop(0) if ctl.polls else None
            if outcome is None:
                return httpx.Response(200, json=OPERATION)
            if isinstance(outcome, dict):
                return httpx.Response(200, json=outcome)
        elif ":download" in url:
            ctl.downloads += 1
            if ctl.downloads_fail:
                return refusal(ctl.downloads_fail.pop(0))
            return httpx.Response(200, content=b"mp4bytes")
        else:
            return httpx.Response(404, json={"error": {"code": 404, "message": "?", "status": "NOT_FOUND"}})
        if isinstance(outcome, BaseException):
            raise outcome
        if isinstance(outcome, httpx.Response):
            return outcome
        if outcome == 200:
            return httpx.Response(200, json=PENDING)
        if outcome == "garbage":
            return httpx.Response(200, content=b"<html>not json</html>")
        return refusal(outcome)

    def sleep(seconds):
        ctl.sleeps.append(seconds)

    monkeypatch.setattr(socket.socket, "connect", no_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", lambda self, address: no_connect(self, address))
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", handle_request)
    monkeypatch.setattr(time, "sleep", sleep)  # the 10 s poll, our backoff and tenacity's
    monkeypatch.setattr(config, "GOOGLE_API_KEY", "offline-test")
    monkeypatch.setattr(config, "BUDGET_EUR", 100.0)
    monkeypatch.setattr(config, "RETRY_BASE_S", 0.001)
    monkeypatch.setattr(config, "RETRY_JITTER_S", 0.001)
    monkeypatch.setattr(config, "RETRY_MAX_S", 0.01)
    ctl.out_dir = tmp_path / "visuals"
    yield ctl
    assert ctl.sockets == [], "the test opened a network connection"


def render(veo):
    return ai_video.render_scene(SCENE, veo.out_dir)


def notes():
    """The ledger's Veo rows: the note of each, or "clip" for a normal one."""
    return [e["units"].get("note", "clip") for e in ledger.entries() if "seconds" in e["units"]]


@pytest.fixture(autouse=True)
def real_sdk(real_google):
    """The real google-genai client in every test of this module."""


# --- the start POST is one attempt --------------------------------------------------------------------

def test_a_read_timeout_on_the_start_is_one_post_one_may_have_started_entry(veo):
    """On the base, the SDK sent the start again after the timeout: 2 POSTs, a clip made, one ledger row."""
    veo.starts = [httpx.ReadTimeout("slow"), 200]
    with pytest.raises(httpx.ReadTimeout):
        render(veo)
    assert veo.posts == 1
    assert notes() == ["start failed (ReadTimeout); may have started"]
    assert not any(veo.out_dir.glob("ai_*.mp4"))


@pytest.mark.parametrize("error", [
    httpx.ReadTimeout("slow"), httpx.WriteTimeout("slow"), httpx.ReadError("reset"), httpx.WriteError("reset"),
    httpx.RemoteProtocolError("server disconnected"),
    httpx.ConnectTimeout("slow"),  # pre-send in fact, recorded on purpose (see ai_video._provably_not_started)
], ids=lambda e: type(e).__name__)
def test_every_error_after_the_request_may_have_left_is_recorded_once_and_not_repeated(veo, error):
    veo.starts = [error, 200]
    with pytest.raises(type(error)):
        render(veo)
    assert veo.posts == 1
    assert notes() == [f"start failed ({type(error).__name__}); may have started"]


def test_a_503_on_the_start_is_one_post_and_a_may_have_started_entry(veo):
    veo.starts = [503, 200]
    with pytest.raises(genai_errors.ServerError):
        render(veo)
    assert veo.posts == 1 and notes() == ["start failed (ServerError); may have started"]


def test_a_200_that_is_not_json_is_recorded_too(veo):
    """Google answered, so the job may exist; the SDK cannot read the answer."""
    veo.starts = ["garbage", 200]
    with pytest.raises(Exception) as caught:
        render(veo)
    assert veo.posts == 1
    assert notes() == [f"start failed ({type(caught.value).__name__}); may have started"]


@pytest.mark.parametrize("error", [httpx.ConnectError("refused"), httpx.ProxyError("tunnel refused"),
                                   httpx.PoolTimeout("no connection free")], ids=lambda e: type(e).__name__)
def test_an_error_before_any_byte_was_written_is_one_attempt_and_no_record(veo, error):
    veo.starts = [error, 200]
    with pytest.raises(type(error)):
        render(veo)
    assert veo.posts == 1 and notes() == []


def test_a_400_is_refused_not_retried_and_not_recorded(veo):
    veo.starts = [400, 200]
    with pytest.raises(genai_errors.ClientError) as caught:
        render(veo)
    assert caught.value.code == 400 and veo.posts == 1 and notes() == []


# --- a 429 is retried by our own code ----------------------------------------------------------------------

def test_a_429_then_a_200_is_two_posts_one_job_one_normal_ledger_entry(veo):
    veo.starts = [429, 200]
    out = render(veo)
    assert out.read_bytes() == b"mp4bytes"
    assert veo.posts == 2 and notes() == ["clip"]
    assert len(veo.sleeps) == 2, "one backoff before the retry (ours) and the 10 s poll"
    assert veo.sleeps[0] <= config.RETRY_MAX_S


def test_the_wait_follows_retry_after(veo, monkeypatch):
    monkeypatch.setattr(config, "RETRY_MAX_S", 30.0)
    veo.starts = [refusal(429, {"Retry-After": "3"}), 200]
    render(veo)
    assert veo.sleeps[0] == 3.0


def test_429_five_times_is_five_posts_no_record_and_the_client_error(veo):
    veo.starts = [429] * 10
    with pytest.raises(genai_errors.ClientError) as caught:
        render(veo)
    assert caught.value.code == 429
    assert veo.posts == config.MAX_RETRIES + 1 == 5
    assert notes() == []
    assert len(veo.sleeps) == config.MAX_RETRIES


def test_a_429_then_a_timeout_is_recorded_and_not_asked_again(veo):
    veo.starts = [429, httpx.ReadTimeout("slow"), 200]
    with pytest.raises(httpx.ReadTimeout):
        render(veo)
    assert veo.posts == 2 and notes() == ["start failed (ReadTimeout); may have started"]


def test_a_429_then_a_503_is_recorded_and_not_asked_again(veo):
    veo.starts = [429, 503, 200]
    with pytest.raises(genai_errors.ServerError):
        render(veo)
    assert veo.posts == 2 and notes() == ["start failed (ServerError); may have started"]


# --- an interrupt during the 429 wait --------------------------------------------------------------------------

@pytest.mark.parametrize("flag", ["interrupted", "stopping"])
def test_a_press_during_the_429_backoff_starts_no_new_job(veo, monkeypatch, flag):
    veo.starts = [429, 200]
    monkeypatch.setattr(time, "sleep", lambda s: getattr(ledger, flag).set())  # the press comes while waiting
    with pytest.raises(ai_video.StartAbandoned):
        render(veo)
    assert veo.posts == 1 and notes() == []


def test_a_real_sigint_in_the_main_thread_during_the_429_backoff(veo, monkeypatch):
    veo.starts = [429, 200]
    monkeypatch.setattr(time, "sleep", lambda s: signal.raise_signal(signal.SIGINT))
    with pytest.raises(KeyboardInterrupt):
        render(veo)
    assert veo.posts == 1 and notes() == [], "refused, nothing in flight: nothing to count"


def test_a_real_sigint_while_the_start_is_in_flight_is_recorded(veo):
    """Ctrl-C with the POST on the wire (the run is not parallel: the main thread makes the call): Google
    may have accepted the job, so it is counted. On the base nothing was recorded."""
    veo.on_post = lambda n: signal.raise_signal(signal.SIGINT)
    veo.starts = [200]
    with pytest.raises(KeyboardInterrupt):
        render(veo)
    assert veo.posts == 1 and notes() == ["start failed (KeyboardInterrupt); may have started"]


def test_a_real_ctrl_c_in_a_parallel_run_stops_a_worker_waiting_out_a_429(veo, monkeypatch):
    """pipeline.in_parallel: the main thread gets the SIGINT, sets ledger.interrupted, and the worker that is
    waiting to ask Veo again wakes to find it set and starts nothing."""
    veo.starts = [429, 200]

    def backoff(seconds):  # the worker's wait; the press comes while it waits
        REAL_SLEEP(0.3)  # the main thread is in wait() by now
        signal.pthread_kill(threading.main_thread().ident, signal.SIGINT)  # a terminal's Ctrl-C reaches the main thread
        deadline = time.monotonic() + 10
        while not ledger.interrupted.is_set() and time.monotonic() < deadline:
            REAL_SLEEP(0.01)

    monkeypatch.setattr(time, "sleep", backoff)
    monkeypatch.setattr(config, "WORKERS", 2)
    results = []

    def work(i):
        if i == 0:
            results.append(render(veo))

    with pytest.raises(KeyboardInterrupt):
        pipeline.in_parallel(work, [0, 1])
    assert results == []
    assert veo.posts == 1, "no new job after the press"
    assert notes() == []
    assert not ledger.interrupted.is_set(), "in_parallel clears it on the way out"


# --- the reads keep their retries -------------------------------------------------------------------------------------

def test_a_503_while_polling_is_retried_and_the_clip_is_recorded_once(veo):
    veo.starts = [200]
    veo.polls = [503]
    out = render(veo)
    assert out.read_bytes() == b"mp4bytes"
    assert veo.posts == 1 and veo.gets == 2, "the poll was repeated"
    assert notes() == ["clip"]


def test_a_flaky_download_is_still_retried(veo):
    veo.starts = [200]
    veo.downloads_fail = [503]
    out = render(veo)
    assert out.read_bytes() == b"mp4bytes" and veo.downloads == 2
    assert notes() == ["clip"]


# --- the options and the SDK pin ---------------------------------------------------------------------------------------

def test_the_start_options_are_one_attempt_with_retry_options_given():
    options = google_client.start_job_options()
    assert options.retry_options is not None and options.retry_options.attempts == 1


def test_an_http_options_without_retry_options_falls_back_to_the_clients_loop(veo, monkeypatch):
    """Why start_job_options must give retry_options: without them the client's own loop retries a 5xx."""
    monkeypatch.setattr(google_client, "start_job_options", lambda: types.HttpOptions())
    veo.starts = [503, 200]
    render(veo)
    assert veo.posts == 2, "the SDK's client-level retry ran: a second job would have been billed"


def test_sdk_pin_with_a_status_code_list_the_sdk_still_retries_a_timeout(veo, monkeypatch):
    """The finding behind P0-9, measured on google-genai 2.25.0: the old options (attempts > 1, codes [429])
    still retry a ReadTimeout, because the predicate ORs in transport timeouts. If an SDK upgrade stops
    doing that, this fails: then the workaround (one attempt, our own 429 loop) can be reconsidered."""
    monkeypatch.setattr(google_client, "start_job_options", lambda: types.HttpOptions(
        retry_options=types.HttpRetryOptions(attempts=config.MAX_RETRIES + 1, http_status_codes=[429])))
    veo.starts = [httpx.ReadTimeout("slow"), 200]
    render(veo)
    assert veo.posts == 2


def test_the_same_pin_for_a_connect_error(veo, monkeypatch):
    monkeypatch.setattr(google_client, "start_job_options", lambda: types.HttpOptions(
        retry_options=types.HttpRetryOptions(attempts=config.MAX_RETRIES + 1, http_status_codes=[429])))
    veo.starts = [httpx.ConnectError("refused"), 200]
    render(veo)
    assert veo.posts == 2


# --- retries.call ------------------------------------------------------------------------------------------------------------

def test_retries_call_keeps_its_default_predicate_and_takes_another(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    tries = []

    def flaky():
        tries.append(1)
        if len(tries) < 3:
            raise retries.TransientError("busy")
        return "ok"

    assert retries.call(flaky, "x") == "ok" and len(tries) == 3
    tries.clear()
    with pytest.raises(retries.TransientError):
        retries.call(flaky, "x", transient=lambda e: False)
    assert len(tries) == 1
    tries.clear()

    def odd():
        tries.append(1)
        if len(tries) < 3:
            raise ValueError("not transient by default")
        return "ok"

    assert retries.call(odd, "x", transient=lambda e: isinstance(e, ValueError)) == "ok" and len(tries) == 3


def test_the_ledger_rows_of_a_start_that_fails_count_toward_the_daily_cap(veo):
    veo.starts = [httpx.ReadTimeout("slow"), httpx.ReadTimeout("slow"), 200]
    for _ in range(config.VEO_MAX_PER_DAY):
        with pytest.raises(httpx.ReadTimeout):
            ai_video.render_scene({**SCENE, "visual_description": f"sky {_}"}, veo.out_dir)
    assert ledger.veo_clips_today() == config.VEO_MAX_PER_DAY
    with pytest.raises(ai_video.DailyCapReached):
        ai_video.render_scene({**SCENE, "visual_description": "sky 9"}, veo.out_dir)
    assert veo.posts == config.VEO_MAX_PER_DAY, "the cap saw both possibly-started jobs"
