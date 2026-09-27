"""P1-5: narration and visuals for independent scenes are generated concurrently (WORKERS
threads), in scene order, without breaking the budget cap (reserved under a lock), retries or
the Veo daily cap."""
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

import config
import ledger
import pipeline
import retries
from agents import google_client, voice

DELAY = 0.2  # each fake paid call takes this long


def scenes(n, visual_type="still"):
    return [{"id": i, "chapter": 1, "concept": f"c{i}", "narration": f"line number {i} " * 3,
             "visual_type": visual_type, "visual_description": f"picture {i}"} for i in range(1, n + 1)]


@pytest.fixture
def slow_providers(monkeypatch, stills_approved, media, tmp_path):
    """TTS and image model that each take DELAY; the audio is a real short mp3."""
    import shutil
    calls = {"tts": [], "image": [], "veo": 0}
    lock = threading.Lock()
    jitter = random.Random(5)  # delays vary (so calls finish out of order), the same way every run

    def post(url, headers, json, timeout):
        time.sleep(DELAY * jitter.uniform(0.5, 1.5))
        with lock:
            calls["tts"].append(json["text"])
        return SimpleNamespace(status_code=200, content=(media / "n_short.mp3").read_bytes(), text="", headers={})

    class Models:
        def generate_content(self, model, contents, config):
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


def timed(work, script, workers, monkeypatch, allow_veo=False):
    monkeypatch.setattr(config, "WORKERS", workers)
    start = time.perf_counter()
    assets = pipeline.generate_assets(script, work, allow_veo)
    return time.perf_counter() - start, assets


def test_generation_runs_in_parallel_and_keeps_scene_order(slow_providers, monkeypatch, tmp_path):
    script = {"title": "T", "scenes": scenes(8)}
    sequential, a = timed(tmp_path / "one", script, 1, monkeypatch)
    parallel, b = timed(tmp_path / "four", script, 4, monkeypatch)
    print(f"\n8 scenes (TTS + image, {DELAY:.1f} s each): 1 worker {sequential:.2f} s, 4 workers {parallel:.2f} s")
    # 4 workers can't reach 4x: each wave of 4 waits for its slowest (random delays, ~1.3x the
    # mean) and ~0.3 s of local work (probing audio, writing images) stays sequential. Measured
    # 3.08 s -> 1.37 s. Under half the sequential time can only come from running concurrently.
    assert parallel < sequential / 2
    assert [x["audio"].read_bytes() for x in a] == [x["audio"].read_bytes() for x in b]
    assert [x["scene"]["id"] for x in b] == list(range(1, 9)), "results in scene order"
    assert [x["kind"] for x in b] == ["still"] * 8


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
    """A failure in a later item must not wait for an earlier slow one before stopping the rest."""
    monkeypatch.setattr(config, "WORKERS", 2)
    started = []

    def work(i):
        started.append(i)
        if i == 0:
            time.sleep(1.0)
        elif i == 1:
            raise ledger.BudgetExceeded("over")
        else:
            time.sleep(0.05)
        return i

    with pytest.raises(ledger.BudgetExceeded):
        pipeline.in_parallel(work, list(range(20)))
    assert len(started) < 6, f"{len(started)} of 20 items started after the failure"


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
