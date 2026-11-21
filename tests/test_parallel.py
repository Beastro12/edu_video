"""P1-5: narration and visuals for independent scenes are generated concurrently (WORKERS
threads), in scene order, without breaking the budget cap (reserved under a lock), retries or
the Veo daily cap."""
import random
import re
import signal
import subprocess
import sys
import textwrap
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

import config
import ledger
import pipeline
import retries
from agents import google_client, voice

DELAY = 0.2  # each fake paid call takes this long


class FirstWave:
    """The first `n` calls into a provider must be in flight together (a barrier that times out), so a run that
    isn't concurrent fails by name rather than by a clock. `peak` is the most calls ever in flight at once."""

    def __init__(self, n):
        self.n, self.arrived, self.in_flight, self.peak = n, 0, 0, 0
        self.lock = threading.Lock()
        self.barrier = threading.Barrier(n)

    @contextmanager
    def call(self):
        with self.lock:
            self.in_flight += 1
            self.peak = max(self.peak, self.in_flight)
            first_wave = self.arrived < self.n
            self.arrived += first_wave
            k = self.arrived
        try:
            if first_wave:
                try:
                    self.barrier.wait(timeout=5)
                except threading.BrokenBarrierError:
                    raise AssertionError(f"only {k} of {self.n} calls arrived together") from None
            yield
        finally:
            with self.lock:
                self.in_flight -= 1


def scenes(n, visual_type="still"):
    return [{"id": i, "chapter": 1, "concept": f"c{i}", "narration": f"line number {i} " * 3,
             "visual_type": visual_type, "visual_description": f"picture {i}"} for i in range(1, n + 1)]


@pytest.fixture
def slow_providers(monkeypatch, stills_approved, media, tmp_path):
    """TTS and image model that each take DELAY; the audio is a real short mp3."""
    import shutil
    calls = {"tts": [], "image": [], "veo": 0, "wave": {"tts": None, "image": None}}  # wave: a FirstWave a test sets
    lock = threading.Lock()
    jitter = random.Random(5)  # delays vary (so calls finish out of order), the same way every run

    @contextmanager
    def flying(kind):
        wave = calls["wave"][kind]
        if wave is None:
            yield
        else:
            with wave.call():
                yield

    def post(url, headers, json, timeout):
        with flying("tts"):
            time.sleep(DELAY * jitter.uniform(0.5, 1.5))
        with lock:
            calls["tts"].append(json["text"])
        return SimpleNamespace(status_code=200, content=(media / "n_short.mp3").read_bytes(), text="", headers={})

    class Models:
        def generate_content(self, model, contents, config):
            with flying("image"):
                time.sleep(DELAY * jitter.uniform(0.5, 1.5))
            with lock:
                calls["image"].append(contents)
            data = (media / "still.png").read_bytes()
            return SimpleNamespace(parts=[SimpleNamespace(inline_data=SimpleNamespace(data=data, mime_type="image/png"),
                                                          thought=None)])

        def generate_videos(self, model, source, config):
            time.sleep(DELAY)
            with lock:
                calls["veo"] += 1
            video = SimpleNamespace(save=lambda path: shutil.copy(media / "ai.mp4", path))
            return SimpleNamespace(done=True, error=None,
                                   response=SimpleNamespace(generated_videos=[SimpleNamespace(video=video)]))

    monkeypatch.setattr(voice.requests, "post", post)
    monkeypatch.setattr(config, "GOOGLE_API_KEY", "offline-test")
    monkeypatch.setattr(google_client, "make", lambda: SimpleNamespace(
        models=Models(), files=SimpleNamespace(download=lambda file: None)))
    monkeypatch.setattr(config, "BUDGET_EUR", 100.0)
    return calls


@pytest.mark.parametrize("workers", [1, 4])
def test_generation_runs_in_parallel_and_keeps_scene_order(slow_providers, monkeypatch, tmp_path, workers):
    """The PEAK number of calls in flight is WORKERS in each phase ([1, 1] and [4, 4]): the first `workers`
    calls of a phase wait for each other at a barrier, which times out saying "only k of n calls arrived
    together". No clock is asserted: a loaded machine can't fail this one."""
    waves = {"tts": FirstWave(workers), "image": FirstWave(workers)}
    slow_providers["wave"].update(waves)
    monkeypatch.setattr(config, "WORKERS", workers)
    script = {"title": "T", "scenes": scenes(8)}
    assets = pipeline.generate_assets(script, tmp_path, allow_veo=False)
    assert [waves["tts"].peak, waves["image"].peak] == [workers, workers]
    assert [x["scene"]["id"] for x in assets] == list(range(1, 9)), "results in scene order"
    assert [x["kind"] for x in assets] == ["still"] * 8
    assert len(slow_providers["tts"]) == 8 and len(slow_providers["image"]) == 8


def test_a_serial_run_fails_the_barrier_by_name(slow_providers, monkeypatch, tmp_path):
    """The tool of the test above bites: four workers' worth of barrier in a one-worker run says what is wrong."""
    slow_providers["wave"]["tts"] = FirstWave(4)
    monkeypatch.setattr(config, "WORKERS", 1)
    with pytest.raises(AssertionError, match="only 1 of 4 calls arrived together"):
        pipeline.generate_assets({"title": "T", "scenes": scenes(8)}, tmp_path, allow_veo=False)


def test_concurrent_calls_cannot_overspend(slow_providers, monkeypatch, tmp_path):
    """Eight narrations at once with room for three: exactly three are bought."""
    per_call = ledger.tts_eur(len(scenes(1)[0]["narration"]))
    monkeypatch.setattr(config, "BUDGET_EUR", 3.5 * per_call)
    texts = [s["narration"] for s in scenes(8)]
    outcomes = []

    def buy(text):
        try:
            voice.narrate(text, tmp_path / "audio")
            outcomes.append("bought")
        except ledger.BudgetExceeded:
            outcomes.append("refused")

    with ThreadPoolExecutor(8) as pool:
        list(pool.map(buy, texts))
    assert outcomes.count("bought") == 3 and len(slow_providers["tts"]) == 3
    assert ledger.spent_eur() <= config.BUDGET_EUR


def test_budget_stop_ends_the_parallel_run(slow_providers, monkeypatch, tmp_path):
    per_call = ledger.tts_eur(len(scenes(1)[0]["narration"]))
    monkeypatch.setattr(config, "BUDGET_EUR", 2.5 * per_call)
    monkeypatch.setattr(config, "WORKERS", 4)
    with pytest.raises(ledger.BudgetExceeded):
        pipeline.generate_assets({"title": "T", "scenes": scenes(8)}, tmp_path, allow_veo=False)
    assert ledger.spent_eur() <= config.BUDGET_EUR


def test_retries_still_work_in_threads(slow_providers, monkeypatch, tmp_path, media):
    monkeypatch.setattr(retries.time, "sleep", lambda s: None)
    seen = {}
    lock = threading.Lock()

    def flaky(url, headers, json, timeout):
        with lock:
            seen[json["text"]] = seen.get(json["text"], 0) + 1
            first = seen[json["text"]] == 1
        return SimpleNamespace(status_code=503 if first else 200, text="busy", headers={},
                               content=(media / "n_short.mp3").read_bytes())

    monkeypatch.setattr(voice.requests, "post", flaky)
    monkeypatch.setattr(config, "WORKERS", 4)
    assets = pipeline.generate_assets({"title": "T", "scenes": scenes(6)}, tmp_path, allow_veo=False)
    assert len(assets) == 6 and set(seen.values()) == {2}


def test_the_veo_daily_cap_holds_under_concurrency(slow_providers, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "WORKERS", 4)
    assets = pipeline.generate_assets({"title": "T", "scenes": scenes(4, "ai_video")}, tmp_path, allow_veo=True)
    assert slow_providers["veo"] == config.VEO_MAX_PER_DAY
    assert sorted(x["kind"] for x in assets) == ["ai"] * config.VEO_MAX_PER_DAY + ["still"] * (4 - config.VEO_MAX_PER_DAY)


def test_scenes_sharing_a_line_or_a_picture_pay_once(slow_providers, monkeypatch, tmp_path):
    """Same narration and same image prompt in two scenes = same cache key: one purchase,
    the other scene waits and finds it cached (no double pay, no clash over temp files)."""
    monkeypatch.setattr(config, "WORKERS", 4)
    same = scenes(2)
    same[1] = {**same[1], "narration": same[0]["narration"], "visual_description": same[0]["visual_description"]}
    assets = pipeline.generate_assets({"title": "T", "scenes": same * 2}, tmp_path, allow_veo=False)
    assert len(slow_providers["tts"]) == 1 and len(slow_providers["image"]) == 1
    assert len({a["audio"] for a in assets}) == 1 and {a["kind"] for a in assets} == {"still"}


def test_a_failure_stops_new_work_at_once(monkeypatch):
    """A failure in a later item must not wait for an earlier slow one before stopping the rest. Events, not
    sleeps: item 0 is in flight until it has seen `stopping`; item 1 fails once item 0 is really running."""
    monkeypatch.setattr(config, "WORKERS", 2)
    started, saw_stopping, zero_running = [], [], threading.Event()

    def work(i):
        started.append(i)
        if i == 0:
            zero_running.set()
            saw_stopping.append(ledger.stopping.wait(5))
        elif i == 1:
            zero_running.wait(5)
            raise ledger.BudgetExceeded("over")
        else:
            ledger.stopping.wait(5)  # the freed worker's next item holds it until the main thread has stopped the queue
        return i

    with pytest.raises(ledger.BudgetExceeded):
        pipeline.in_parallel(work, list(range(20)))
    assert saw_stopping == [True], "item 0 was in flight when the failure set `stopping`"
    # item 1 failing frees its worker, which may take item 2 before the main thread cancels the queue
    assert sorted(started) in ([0, 1], [0, 1, 2]), f"started after the failure: {sorted(started)}"


def test_after_a_failure_calls_in_flight_cannot_reserve_new_spend(monkeypatch):
    """While one item has failed and another is still running, that other item's next paid
    call is refused; once nothing is in flight, the next run starts clean."""
    monkeypatch.setattr(config, "WORKERS", 2)
    outcome, running = [], threading.Event()

    def work(i):
        if i == 0:
            running.wait(5)  # fail only once item 1 is really in flight (not merely queued)
            raise ledger.BudgetExceeded("over")
        running.set()
        ledger.stopping.wait(5)  # still running when item 0 fails
        try:
            with ledger.reserve("elevenlabs", 0.001):
                outcome.append("reserved")
        except ledger.BudgetExceeded as e:
            outcome.append(str(e))

    with pytest.raises(ledger.BudgetExceeded):
        pipeline.in_parallel(work, [0, 1])
    assert len(outcome) == 1 and "stopping" in outcome[0]
    assert not ledger.stopping.is_set(), "cleared once nothing is in flight"
    with ledger.reserve("elevenlabs", 0.001):
        pass


def test_a_budget_stop_lets_a_started_veo_job_finish(slow_providers, monkeypatch, tmp_path):
    """Abandoning a started Veo job wastes a paid clip; only Ctrl-C may do that."""
    started = threading.Event()
    models = __import__("agents.google_client", fromlist=["make"]).make().models
    real = models.generate_videos

    def slow_veo(model, source, config):
        started.set()
        return SimpleNamespace(done=False, name="op")

    polls = []

    def get(op):
        polls.append(1)
        return real(model="m", source=None, config=None) if len(polls) > 2 else SimpleNamespace(done=False)

    fake = SimpleNamespace(models=SimpleNamespace(generate_videos=slow_veo), operations=SimpleNamespace(get=get),
                           files=SimpleNamespace(download=lambda file: None))
    monkeypatch.setattr(google_client, "make", lambda: fake)
    from agents import ai_video
    monkeypatch.setattr(ai_video.time, "sleep", lambda s: None)
    ledger.stopping.set()  # another scene has just run out of budget
    out = ai_video._buy({"visual_description": "sky"}, tmp_path / "v.mp4", 2.9, timeout_s=60)
    assert out.exists() and len(polls) == 3, "the job ran to the end and the clip was kept"


# --- P1-14: Ctrl-C never interrupts the pool's own machinery ------------------------------------------------------
# Every press here is a REAL signal: `signal.raise_signal` from the exact spot on the main thread, or
# `signal.pthread_kill(main, SIGINT)` from a worker. Never at a random time from a thread: a SIGINT sent from a
# thread just before the main thread blocks is seen only when the wait ends (a CPython race).

ROOT = Path(__file__).resolve().parents[1]


def press():
    """A terminal's Ctrl-C: the kernel hands SIGINT to the main thread."""
    signal.pthread_kill(threading.main_thread().ident, signal.SIGINT)


def press_and_wait(seconds=5):
    """press(), then wait until the handler has run (it sets the flags), and return when: a reservation that
    began after this time cannot have succeeded."""
    press()
    end = time.monotonic() + seconds
    while not ledger.interrupted.is_set():
        if time.monotonic() > end:
            raise AssertionError("the press never reached the handler")
        time.sleep(0.002)
    return time.monotonic()


def main_is_waiting(seconds=5):
    """Return once the main thread is inside concurrent.futures.wait (looked at from outside, by its stack)."""
    main = threading.main_thread().ident
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        frame = sys._current_frames()[main]
        while frame is not None:
            if frame.f_code.co_name == "wait" and frame.f_code.co_filename.endswith("_base.py"):  # concurrent.futures
                return
            frame = frame.f_back
        time.sleep(0.002)
    raise AssertionError("the main thread never reached the wait loop")


def pool_threads():
    return [t for t in threading.enumerate() if t.name.startswith("ThreadPoolExecutor")]


def paid(i, seconds=0.02):
    """A paid call: a reservation, some work, a ledger entry."""
    with ledger.reserve("elevenlabs", 0.001):
        time.sleep(seconds)
        ledger.record("elevenlabs", "m", {"chars": 1}, 0.001)
    return i


def veo_like(started, give_up_s=5, on_stopping=None, slow_exit=0.0):
    """A Veo job in its poll (agents/ai_video._buy): holds a reservation, gives up when `interrupted` is set and
    then records itself as billed. Never told to give up = AssertionError. `on_stopping` runs once, when the job
    first sees `stopping` (a budget stop lets a Veo job finish: it keeps polling)."""
    with ledger.reserve("google", 0.5):
        started.set()
        end = time.monotonic() + give_up_s
        while not ledger.interrupted.is_set():
            if on_stopping is not None and ledger.stopping.is_set():
                on_stopping, hook = None, on_stopping
                hook()
            if time.monotonic() > end:
                raise AssertionError("never told to give up")
            time.sleep(0.005)
        time.sleep(slow_exit)  # a poll that notices a press only at its next turn
        ledger.record("google", "veo", {"seconds": 8, "note": "lost track of job (RuntimeError); assumed billed"}, 0.5)
        raise RuntimeError("interrupted")


@pytest.fixture
def watch(monkeypatch):
    """What a press must leave behind: no pool thread of ours alive, flags and holds clear, SIGINT back as it was,
    and no reservation that began after `pressed_at`."""
    before = set(pool_threads())
    began = []
    real = ledger.reserve

    @contextmanager
    def reserve(provider, cost):
        t0 = time.monotonic()
        with real(provider, cost):
            began.append(t0)
            yield

    monkeypatch.setattr(ledger, "reserve", reserve)
    monkeypatch.setattr(config, "WORKERS", 2)
    w = SimpleNamespace(pressed_at=None, began=began)

    def check():
        stray = [t.name for t in pool_threads() if t not in before and t.is_alive()]
        assert not stray, f"pool threads still alive when in_parallel raised: {stray}"
        assert not ledger.stopping.is_set() and not ledger.interrupted.is_set(), "flags left set"
        assert abs(ledger._held) < 1e-9, "a reservation is still held"
        assert signal.getsignal(signal.SIGINT) is signal.default_int_handler, "SIGINT handler not restored"
        late = [t for t in began if w.pressed_at is not None and t > w.pressed_at]
        assert not late, f"{len(late)} reservation(s) succeeded after the press"

    w.check = check
    return w


def test_the_handler_is_installed_for_the_run_and_restored_after(watch):
    seen = []
    pipeline.in_parallel(lambda i: seen.append(signal.getsignal(signal.SIGINT)), [0, 1, 2])
    assert len(seen) == 3 and all(h is not signal.default_int_handler and callable(h) for h in seen)
    watch.check()


def test_a_sequential_run_installs_nothing(monkeypatch):
    monkeypatch.setattr(config, "WORKERS", 1)
    seen = []
    pipeline.in_parallel(lambda i: seen.append(signal.getsignal(signal.SIGINT)), [0, 1, 2])
    pipeline.in_parallel(lambda i: seen.append(signal.getsignal(signal.SIGINT)), [0])
    assert seen == [signal.default_int_handler] * 4


# --- the state table, one test per row ---

def test_a_press_pending_when_the_handler_is_installed_raises_from_the_install(watch, monkeypatch):
    """Row 1: KeyboardInterrupt from the install; no pool is made, nothing runs, nothing is left to clear."""
    real = signal.signal

    def install(num, handler):
        if num == signal.SIGINT and handler is not signal.default_int_handler:
            signal.raise_signal(signal.SIGINT)  # the old handler is still in place: raises right here
        return real(num, handler)

    made, ran = [], []
    monkeypatch.setattr(signal, "signal", install)
    monkeypatch.setattr(pipeline, "ThreadPoolExecutor", lambda *a: made.append(a) or ThreadPoolExecutor(*a))
    with pytest.raises(KeyboardInterrupt):
        pipeline.in_parallel(ran.append, [0, 1, 2])
    assert made == [] and ran == []
    monkeypatch.undo()
    watch.check()


def test_the_handler_records_the_press_before_it_sets_the_flags(watch, monkeypatch):
    """A job that sees `stopping` or `interrupted` set must be able to find the press already recorded (verdict's
    precedence reads the record): swap the two lines in the handler and the first `set` sees no press."""
    seen = []

    class Spy(threading.Event):
        def set(self):
            run = signal.getsignal(signal.SIGINT)
            seen.append(len(getattr(run, "presses", [])))
            super().set()

    monkeypatch.setattr(ledger, "stopping", Spy())

    def job(i):
        if i == 0:
            watch.pressed_at = press_and_wait()
        return i

    with pytest.raises(KeyboardInterrupt):
        pipeline.in_parallel(job, [0, 1])
    assert seen and seen[0] >= 1, f"flags were set with {seen[:1]} presses recorded"
    monkeypatch.undo()
    watch.check()


def test_a_press_inside_the_pool_constructor_submits_nothing(watch, monkeypatch):
    """Row 2."""
    submitted = []

    class Pool(ThreadPoolExecutor):
        def __init__(self, *a):
            super().__init__(*a)
            signal.raise_signal(signal.SIGINT)
            watch.pressed_at = time.monotonic()

        def submit(self, *a, **kw):
            submitted.append(a)
            return super().submit(*a, **kw)

    ran = []
    monkeypatch.setattr(pipeline, "ThreadPoolExecutor", Pool)
    with pytest.raises(KeyboardInterrupt):
        pipeline.in_parallel(ran.append, [0, 1, 2, 3])
    assert ran == [] and submitted == [], "not even a submit after the press"
    watch.check()


def test_a_press_inside_thread_start_leaves_no_orphan_thread(watch, monkeypatch):
    """Row 3 (C1; the reviewer's p1_orphan_thread probe). On CPython 3.11 `_adjust_thread_count` runs `t.start()` and
    only then `self._threads.add(t)`; a KeyboardInterrupt raised after the thread began left a worker that
    `shutdown(wait=True)` never joined, making paid calls with no attribution after the flags were cleared."""
    real_start, fired, started = threading.Thread.start, [], []

    def start(self):
        real_start(self)
        if self.name.startswith("ThreadPoolExecutor") and not fired:
            fired.append(self.name)
            signal.raise_signal(signal.SIGINT)  # right after `super().start()`, before `_threads.add`
            watch.pressed_at = time.monotonic()

    def job(i):
        started.append(i)
        return paid(i, seconds=0.3)

    submits = []
    real_submit = ThreadPoolExecutor.submit

    def submit(self, *a, **kw):
        submits.append(a)
        return real_submit(self, *a, **kw)

    monkeypatch.setattr(threading.Thread, "start", start)
    monkeypatch.setattr(ThreadPoolExecutor, "submit", submit)
    with ledger.attribute_to("film-x"):
        with pytest.raises(KeyboardInterrupt):
            pipeline.in_parallel(job, list(range(8)))
        entries = ledger.entries(film="film-x")
    assert fired and len(submits) == 1, "submitting stopped at the press"
    assert set(started) <= {0}, f"items started: {started}"
    assert all("run" in e for e in entries)
    watch.check()


def test_a_press_inside_the_second_thread_start_reaches_a_veo_job_already_polling(watch, monkeypatch):
    """Row 3's acceptance line (BACKLOG: "a test that interrupts during submission sees `ledger.interrupted` set and
    no further paid call started"; r2's C1: the Veo-like job's entry is present and attributed). From the
    reviewer's p1_submit_interrupted.py: item 0 is a Veo-like job polling `interrupted`; the press comes right after
    the 2nd `Thread.start()`. On a6e1177 the flags were cleared before item 0 could see them (it saw nothing) and
    the second thread was an orphan."""
    monkeypatch.setattr(config, "WORKERS", 4)
    real_start, starts, saw, started = threading.Thread.start, [], [], []
    polling = threading.Event()  # item 0 holds its reservation and is polling

    def start(self):
        real_start(self)
        if self.name.startswith("ThreadPoolExecutor"):
            starts.append(self.name)
            if len(starts) == 2:
                assert polling.wait(5), "item 0 never began"
                signal.raise_signal(signal.SIGINT)
                watch.pressed_at = time.monotonic()

    def job(i):
        started.append(i)
        if i == 0:
            try:
                return veo_like(polling)
            finally:
                saw.append(ledger.interrupted.is_set())
        return paid(i, seconds=0.2)

    monkeypatch.setattr(threading.Thread, "start", start)
    with ledger.attribute_to("film-x") as run_id:
        with pytest.raises(KeyboardInterrupt):
            pipeline.in_parallel(job, list(range(6)))
        entries = ledger.entries(film="film-x", run=run_id, provider="google")
    assert len(starts) == 2 and set(started) <= {0, 1}, f"no item beyond the two started threads: {started}"
    assert saw == [True], "item 0, polling, saw ledger.interrupted set"
    assert [e["units"]["note"] for e in entries] == ["lost track of job (RuntimeError); assumed billed"]
    watch.check()


def test_ctrl_c_while_waiting_stops_the_jobs_in_flight(watch, capsys):
    """Row 4 (E8; replaces a test whose `wait` stand-in had no `timeout=`). A press while the main thread waits:
    the Veo-like job in flight gives up and is recorded with its film and run; the queued items never start;
    one line says what is waited for."""
    started, ran = threading.Event(), []

    def job(i):
        ran.append(i)
        if i == 0:
            return veo_like(started, slow_exit=0.6)  # still in flight when the main thread next looks
        if i == 1:
            started.wait(5)
            watch.pressed_at = press_and_wait()
            return i
        raise AssertionError("a queued item started after the press")

    with ledger.attribute_to("film-x") as run_id:
        with pytest.raises(KeyboardInterrupt):
            pipeline.in_parallel(job, [0, 1, 2, 3])
        entries = ledger.entries(film="film-x", run=run_id)
    assert sorted(ran) == [0, 1]
    assert [e["units"]["note"] for e in entries if e["provider"] == "google"] == \
        ["lost track of job (RuntimeError); assumed billed"]
    out = capsys.readouterr().out
    assert out.count("• stopping:") == 1
    assert re.search(r"waiting for [12] calls in flight to finish \(a Veo job gives up and is counted as paid\)", out)
    watch.check()


def test_a_press_while_waiting_after_a_failure_records_the_veo_job_and_raises_the_failure(watch, capsys):
    """Row 5 (C2; the reviewer's p1_exit_loses_veo and p1_ctrl_c_in_shutdown probes): a job fails first, the main
    thread waits for the Veo job in flight, and Ctrl-C comes then. Whichever came first wins (E4): the worker's
    exception is re-raised, with a note, and the Veo job gave up and was recorded."""
    started = threading.Event()

    def press_now():
        watch.pressed_at = press_and_wait()

    def job(i):
        if i == 0:
            started.wait(5)
            raise ledger.BudgetExceeded("over budget")
        return veo_like(started, on_stopping=press_now)

    with ledger.attribute_to("film-x"):
        with pytest.raises(ledger.BudgetExceeded, match="over budget") as caught:
            pipeline.in_parallel(job, [0, 1])
        entries = ledger.entries(film="film-x", provider="google")
    assert [e["units"]["note"] for e in entries] == ["lost track of job (RuntimeError); assumed billed"]
    assert any("Ctrl-C pressed while waiting for 1 calls in flight" in n for n in caught.value.__notes__)
    out = capsys.readouterr().out
    assert "• stopping after a failure: waiting for 1 calls in flight" in out
    watch.check()


def test_a_press_during_the_pool_shutdown_is_never_swallowed(watch, monkeypatch):
    """The press lands inside `pool.shutdown`, after every job has finished: still no normal return."""
    class Pool(ThreadPoolExecutor):
        def shutdown(self, *a, **kw):
            signal.raise_signal(signal.SIGINT)
            watch.pressed_at = time.monotonic()
            return super().shutdown(*a, **kw)

    monkeypatch.setattr(pipeline, "ThreadPoolExecutor", Pool)
    with pytest.raises(KeyboardInterrupt):
        pipeline.in_parallel(paid, [0, 1, 2])
    watch.check()


def test_more_presses_while_waiting_for_calls_in_flight_are_harmless(watch):
    """Row 6: a second (and a third) press while waiting, after a press and after a failure. The Veo job is
    recorded once; nothing hangs, nothing is raised but the first cause."""
    started = threading.Event()

    def after_a_press(i):
        if i == 0:
            return veo_like(started)
        started.wait(5)
        watch.pressed_at = press_and_wait()
        for _ in range(2):
            time.sleep(0.05)
            press()
        time.sleep(0.3)  # still in flight while the main thread has seen all three
        return i

    with ledger.attribute_to("film-x"):
        with pytest.raises(KeyboardInterrupt):
            pipeline.in_parallel(after_a_press, [0, 1])
        assert len(ledger.entries(film="film-x", provider="google")) == 1
    watch.check()
    ledger.interrupted.clear()

    def after_a_failure(i):
        if i == 0:
            started.wait(5)
            raise ValueError("boom")
        with ledger.reserve("google", 0.5):
            ledger.stopping.wait(5)
            for _ in range(3):
                press()
                time.sleep(0.05)
            ledger.record("google", "veo", {"seconds": 8, "note": "assumed billed"}, 0.5)

    started.clear()
    started.set()
    with pytest.raises(ValueError, match="boom"):
        pipeline.in_parallel(after_a_failure, [0, 1])
    watch.check()


def test_a_press_as_the_last_future_finishes_is_not_a_normal_return(watch):
    """Row 7: the last job presses and returns normally. in_parallel must raise: a paid phase after it would start."""
    done_first = threading.Event()

    def job(i):
        if i == 0:
            done_first.set()
            return i
        done_first.wait(5)
        time.sleep(0.05)
        watch.pressed_at = press_and_wait()
        return i

    with pytest.raises(KeyboardInterrupt):
        pipeline.in_parallel(job, [0, 1])
    watch.check()


def test_a_press_during_the_handler_restore_is_not_swallowed(watch, monkeypatch):
    """Row 8: the press lands inside the `signal.signal` call that puts the old handler back."""
    real = signal.signal

    def restore(num, handler):
        if num == signal.SIGINT and handler is signal.default_int_handler:
            signal.raise_signal(signal.SIGINT)  # our handler is still the installed one: it records
            watch.pressed_at = time.monotonic()
        return real(num, handler)

    monkeypatch.setattr(signal, "signal", restore)
    with pytest.raises(KeyboardInterrupt):
        pipeline.in_parallel(paid, [0, 1, 2])
    monkeypatch.undo()
    watch.check()


def test_a_press_first_then_jobs_failing_because_of_it_raises_keyboard_interrupt(watch):
    """Row 9: whichever comes first wins. The jobs that fail BECAUSE of the press ("the run is stopping") are not
    what in_parallel raises."""
    refusals = []

    def job(i):
        if i == 0:
            watch.pressed_at = press_and_wait()
            try:
                paid(i)
            except ledger.BudgetExceeded as e:
                refusals.append(str(e))
                raise
        else:
            time.sleep(0.3)

    with pytest.raises(KeyboardInterrupt):
        pipeline.in_parallel(job, [0, 1])
    assert len(refusals) == 1 and "the run is stopping" in refusals[0]
    watch.check()


def test_a_run_after_an_interrupted_run_starts_clean(watch):
    """Flags and holds are cleared on the way out of an interrupted run, so the next run in the process works."""
    def job(i):
        if i == 0:
            watch.pressed_at = press_and_wait()
        return i

    with pytest.raises(KeyboardInterrupt):
        pipeline.in_parallel(job, [0, 1])
    watch.check()
    assert pipeline.in_parallel(paid, [0, 1, 2]) == [0, 1, 2]
    assert len(ledger.entries()) == 3


@pytest.mark.parametrize("kind", ["ignored", "custom"])
def test_a_sigint_that_is_not_python_default_is_left_alone(watch, monkeypatch, kind):
    """Row 10: SIG_IGN (nohup) or a handler someone installed: nothing of ours is installed, a press does what it
    did, and the handler is unchanged after."""
    pressed = []
    handler = signal.SIG_IGN if kind == "ignored" else (lambda num, frame: pressed.append(num))
    monkeypatch.setattr(signal, "getsignal", signal.getsignal)  # (restores nothing; documents that we only read)
    old = signal.signal(signal.SIGINT, handler)
    seen = []
    try:
        def job(i):
            seen.append(signal.getsignal(signal.SIGINT))
            if i == 0:
                press()
                time.sleep(0.1)
            return i

        assert pipeline.in_parallel(job, [0, 1, 2]) == [0, 1, 2]
        assert signal.getsignal(signal.SIGINT) is handler
    finally:
        signal.signal(signal.SIGINT, old)
    assert all(h is handler for h in seen)
    assert pressed == ([] if kind == "ignored" else [signal.SIGINT])
    watch.check()


def test_off_the_main_thread_nothing_is_installed(watch):
    """Row 11: signal.signal would raise ValueError there; the docstring says Ctrl-C is then what it was."""
    seen, out = [], []

    def run():
        out.append(pipeline.in_parallel(lambda i: seen.append(signal.getsignal(signal.SIGINT)) or i, [0, 1, 2]))

    t = threading.Thread(target=run)
    t.start()
    t.join(10)
    assert out == [[0, 1, 2]] and seen == [signal.default_int_handler] * 3
    watch.check()


def test_a_keyboard_interrupt_raised_as_an_exception_is_the_fallback(watch):
    """Row 12 (r1's fallback, kept): with a handler of someone else's that raises (nothing of ours is installed),
    the KeyboardInterrupt lands in in_parallel as an exception: both flags, cancel, wait, re-raise."""
    def raising(num, frame):
        raise KeyboardInterrupt

    old = signal.signal(signal.SIGINT, raising)
    started = threading.Event()

    def job(i):
        if i == 0:
            return veo_like(started)
        if i == 1:
            started.wait(5)
            main_is_waiting()  # without our handler a press inside `submit` can strand a thread: not this row
            press()
            time.sleep(0.5)
        return i

    try:
        with ledger.attribute_to("film-x"):
            with pytest.raises(KeyboardInterrupt):
                pipeline.in_parallel(job, [0, 1, 2, 3])
            assert len(ledger.entries(film="film-x", provider="google")) == 1
    finally:
        signal.signal(signal.SIGINT, old)
    watch.pressed_at = None
    watch.check()


# --- the handler never takes a lock the main thread may hold (E1) ---

def _child(code, tmp_path, timeout=30):
    """Run `code` in a fresh interpreter with the repo importable and its own build dir; (returncode, stdout)."""
    script = "import sys\nsys.path.insert(0, %r)\nimport config\nconfig.BUILD_DIR = sys.argv[1]\n" % str(ROOT)
    script += textwrap.dedent(code)
    try:
        done = subprocess.run([sys.executable, "-c", script, str(tmp_path / "build")], capture_output=True, text=True,
                              timeout=timeout, cwd=ROOT)
    except subprocess.TimeoutExpired as e:
        pytest.fail(f"the child hung for {timeout} s (a deadlock): {(e.stdout or b'')[-300:]!r}")
    return done.returncode, done.stdout + done.stderr


def test_a_press_inside_the_main_threads_event_calls_does_not_deadlock(tmp_path):
    """E1/E6. An Event whose `set` and `clear` take a real SIGINT while holding their own lock, exactly where a
    handler calling `.set()` would wait for itself. Child process, with a hang reported as a failure."""
    code = """
        import signal, threading
        import ledger, pipeline

        class Racy(threading.Event):
            def set(self):
                with self._cond:
                    self._flag = True
                    signal.raise_signal(signal.SIGINT)  # the press lands with the lock held
                    self._cond.notify_all()

            def clear(self):
                with self._cond:
                    signal.raise_signal(signal.SIGINT)
                    self._flag = False

        ledger.stopping, ledger.interrupted = Racy(), Racy()
        config.WORKERS = 2

        def job(i):
            if i == 0:
                raise ValueError("boom")
            ledger.stopping.wait(5)

        try:
            pipeline.in_parallel(job, [0, 1, 2])
        except ValueError as e:
            print("RAISED", type(e).__name__, getattr(e, "__notes__", []))
        print("FLAGS", ledger.stopping.is_set(), ledger.interrupted.is_set(), ledger._held)
    """
    rc, out = _child(code, tmp_path)
    assert rc == 0, out
    assert "RAISED ValueError ['Ctrl-C pressed while waiting for" in out and "FLAGS False False 0" in out, out


def test_a_press_after_a_failure_records_the_veo_job_before_the_process_can_exit(tmp_path):
    """The reviewer's p1_exit_loses_veo, in a child process that leaves through os._exit as a killed run does:
    on the base, a press in the wait after a failure left the Veo job unrecorded (the budget and VEO_MAX_PER_DAY
    then undercount a billed clip)."""
    code = """
        import os, signal, threading, time
        import ledger, pipeline
        config.WORKERS = 2
        started = threading.Event()

        def job(i):
            if i == 0:
                started.wait(5)
                raise ledger.BudgetExceeded("over budget")
            with ledger.reserve("google", 0.5):
                started.set()
                ledger.stopping.wait(5)
                signal.pthread_kill(threading.main_thread().ident, signal.SIGINT)
                time.sleep(0.5)  # a Veo poll: gives up only when told to (a press), checked every 10 s in real life
                end = time.monotonic() + 3
                while not ledger.interrupted.is_set() and time.monotonic() < end:
                    time.sleep(0.01)
                if ledger.interrupted.is_set():
                    ledger.record("google", "veo", {"seconds": 8, "note": "assumed billed"}, 0.5)

        try:
            with ledger.attribute_to("film-x"):
                pipeline.in_parallel(job, [0, 1])
        except BaseException as e:
            print("RAISED", type(e).__name__)
        sys.stdout.flush()
        os._exit(0)
    """
    rc, out = _child(code, tmp_path)
    assert rc == 0 and "RAISED BudgetExceeded" in out, out
    import json
    lines = (tmp_path / "build" / "ledger.jsonl").read_text().splitlines()
    entries = [json.loads(x) for x in lines]
    assert [(e["film"], e["units"]["note"]) for e in entries] == [("film-x", "assumed billed")], entries
