"""P1-7: a paid generation that failed (Veo, the image model) is recorded per content key in
build/<slug>/failures.json; a rerun goes straight to the fallback instead of paying or waiting
again, unless --retry-failed. A budget stop, the Veo daily cap and Ctrl-C are not failures."""
import sys
import threading
from types import SimpleNamespace

import httpx
import pytest

import config
import failures
import ledger
import pipeline
from agents import ai_video, manim_agent, still_critic
from utils import load_json, save_json

SCENE = {"id": 3, "concept": "c", "narration": "n" * 50, "visual_type": "ai_video", "visual_description": "sky"}


@pytest.fixture
def google(monkeypatch, stills_approved, media):
    """Veo and the image model; `google.veo` / `google.image` say what each does next."""
    calls = []

    def no_video():
        return SimpleNamespace(done=True, error=None, response=SimpleNamespace(generated_videos=[]))

    def a_video():
        video = SimpleNamespace(save=lambda path: open(path, "wb").write(b"mp4"))
        return SimpleNamespace(done=True, error=None, response=SimpleNamespace(generated_videos=[SimpleNamespace(video=video)]))

    class Models:
        def generate_videos(self, model, source, config):
            calls.append("veo")
            return state.veo()

        def generate_content(self, model, contents, config):
            calls.append("image")
            return state.image()

    def an_image():
        data = (media / "still.png").read_bytes()
        return SimpleNamespace(parts=[SimpleNamespace(inline_data=SimpleNamespace(data=data, mime_type="image/png"), thought=None)])

    def no_image():
        return SimpleNamespace(parts=[])

    state = SimpleNamespace(calls=calls, veo=no_video, image=an_image, a_video=a_video, an_image=an_image,
                            no_image=no_image)
    fake = SimpleNamespace(models=Models(), files=SimpleNamespace(download=lambda file: None))
    monkeypatch.setattr(config, "GOOGLE_API_KEY", "offline-test")
    monkeypatch.setattr(config, "BUDGET_EUR", 100.0)
    monkeypatch.setattr("agents.google_client.make", lambda: fake)
    monkeypatch.setattr(manim_agent, "render_scene", lambda *a, **k: media / "manim.mp4")
    return state


def visual(work, retry=False, scene=SCENE):
    log = failures.Failures(work / "failures.json", retry=retry)
    return pipeline.make_visual(scene, 5.0, work / "visuals", allow_veo=True, failures=log)


def test_a_failed_veo_scene_is_not_asked_again_on_rerun(google, tmp_path, capsys):
    path, kind = visual(tmp_path)
    assert kind == "still" and google.calls == ["veo", "image"]
    entry = load_json(tmp_path / "failures.json")[ai_video.cache_path(SCENE, tmp_path / "visuals").stem]
    assert entry["scene"] == 3 and entry["what"] == "veo" and "no video" in entry["error"]

    capsys.readouterr()
    again, kind = visual(tmp_path)
    assert (again, kind) == (path, "still")
    assert google.calls == ["veo", "image"], "no provider call for the scene that failed"
    assert "--retry-failed" in capsys.readouterr().out


def test_retry_failed_asks_again_and_a_success_clears_the_entry(google, tmp_path):
    visual(tmp_path)
    google.veo = google.a_video
    path, kind = visual(tmp_path, retry=True)
    assert kind == "ai" and google.calls == ["veo", "image", "veo"]
    assert load_json(tmp_path / "failures.json") == {}
    assert visual(tmp_path) == (path, "ai"), "and later runs use the clip"


def test_a_failed_still_goes_straight_to_manim_on_rerun(google, tmp_path):
    google.image = google.no_image
    still_scene = {**SCENE, "visual_type": "still"}
    assert visual(tmp_path, scene=still_scene)[1] == "manim"
    assert google.calls == ["image"]
    [entry] = load_json(tmp_path / "failures.json").values()
    assert entry["what"] == "still" and "no image" in entry["error"]
    assert visual(tmp_path, scene=still_scene)[1] == "manim"
    assert google.calls == ["image"]


def test_a_changed_description_is_a_new_generation(google, tmp_path):
    visual(tmp_path)
    visual(tmp_path, scene={**SCENE, "visual_description": "a different sky"})
    assert google.calls.count("veo") == 2


@pytest.mark.parametrize("stop", ["cap", "budget", "interrupt"])
def test_stops_that_are_not_failed_generations_are_not_recorded(google, tmp_path, monkeypatch, stop):
    if stop == "cap":
        monkeypatch.setattr(config, "VEO_MAX_PER_DAY", 0)
        visual(tmp_path)
    elif stop == "budget":
        monkeypatch.setattr(config, "BUDGET_EUR", 0.0)
        with pytest.raises(ledger.BudgetExceeded):
            visual(tmp_path)
    else:
        google.veo = lambda: SimpleNamespace(done=False, name="op")
        monkeypatch.setattr(ai_video.time, "sleep", lambda s: None)
        ledger.interrupted.set()
        visual(tmp_path)
    log = tmp_path / "failures.json"
    assert not log.exists() or load_json(log) == {}
    ledger.interrupted.clear()
    google.veo = google.a_video
    monkeypatch.setattr(config, "BUDGET_EUR", 100.0)
    monkeypatch.setattr(config, "VEO_MAX_PER_DAY", 2)
    assert visual(tmp_path)[1] == "ai", "the next run tries Veo again"


def test_keys_never_reach_the_failure_log_or_the_output(google, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(config, "GOOGLE_API_KEY", "AIza-secret-offline")

    def leaky():
        raise RuntimeError("400 Bad Request for url https://x/?key=AIza-secret-offline")

    google.veo = leaky
    visual(tmp_path)
    visual(tmp_path)  # and the "failed earlier" message
    text = (tmp_path / "failures.json").read_text()
    assert "AIza-secret-offline" not in text and "key=" in text
    out = capsys.readouterr().out
    assert "AIza-secret-offline" not in out and "key=***" in out


def test_the_cli_passes_retry_failed(monkeypatch, tmp_path):
    class Rendered(Exception):
        pass

    seen = []

    def render_film(script, work, allow_veo, seed, retry_failed):
        seen.append(retry_failed)
        raise Rendered

    monkeypatch.setattr(pipeline, "get_script", lambda *a: {"title": "T", "scenes": []})
    monkeypatch.setattr(pipeline, "render_film", render_film)
    for argv in (["topic"], ["topic", "--retry-failed"]):
        monkeypatch.setattr(sys, "argv", ["pipeline.py", *argv])
        with pytest.raises(Rendered):
            pipeline.main()
    assert seen == [False, True]


def test_the_estimate_counts_the_fallback_for_a_failed_scene(google, tmp_path):
    visual(tmp_path)  # Veo fails; the still is made and accepted
    save_json(tmp_path / "script.json", {"title": "T", "scenes": [SCENE]})
    est = pipeline.estimate_cost(1, tmp_path, allow_veo=True, skip_critic=True)
    assert est["veo"] == 0 and est["images"] == 0, "the rerun buys nothing for this scene"
    retry = pipeline.estimate_cost(1, tmp_path, allow_veo=True, skip_critic=True, retry_failed=True)
    assert retry["veo"] == pytest.approx(ledger.video_eur(config.VEO_CLIP_S))


@pytest.mark.parametrize("error", [httpx.ConnectError("network down"), OSError("No space left on device")])
def test_errors_that_say_nothing_about_the_request_are_not_recorded(google, tmp_path, error):
    def fails():
        raise error

    google.veo = fails
    assert visual(tmp_path)[1] == "still"
    log = tmp_path / "failures.json"
    assert not log.exists() or load_json(log) == {}
    visual(tmp_path)
    assert google.calls.count("veo") == 2, "the next run asks Veo again"


def test_with_retry_failed_two_scenes_sharing_a_failure_ask_once(google, tmp_path):
    log = failures.Failures(tmp_path / "failures.json", retry=True)
    for _ in range(2):  # the same picture in two scenes: one cache key
        pipeline.make_visual(SCENE, 5.0, tmp_path / "visuals", allow_veo=True, failures=log)
    assert google.calls.count("veo") == 1


def test_a_still_that_works_on_retry_clears_its_entry(google, tmp_path):
    google.image = google.no_image
    still_scene = {**SCENE, "visual_type": "still"}
    visual(tmp_path, scene=still_scene)
    google.image = google.an_image
    assert visual(tmp_path, retry=True, scene=still_scene)[1] == "still"
    assert load_json(tmp_path / "failures.json") == {}


def test_the_estimate_counts_manim_for_a_failed_still(google, tmp_path):
    google.image = google.no_image
    still_scene = {**SCENE, "visual_type": "still"}
    visual(tmp_path, scene=still_scene)
    save_json(tmp_path / "script.json", {"title": "T", "scenes": [still_scene]})
    assert pipeline.estimate_cost(1, tmp_path, allow_veo=True, skip_critic=True)["images"] == 0
    retry = pipeline.estimate_cost(1, tmp_path, allow_veo=True, skip_critic=True, retry_failed=True)
    assert retry["images"] == pytest.approx(ledger.image_eur())


def test_a_failure_on_a_later_review_attempt_is_remembered(google, tmp_path, monkeypatch):
    """Attempt 0 is rejected by the review, attempt 1's image fails: the rerun resumes at
    attempt 1, finds it failed, and goes to Manim without asking the image model."""
    monkeypatch.setattr(still_critic, "review", lambda scene, still: {"ok": False, "problems": ["letters"]})
    images = iter([google.an_image, google.no_image])
    google.image = lambda: next(images)()
    still_scene = {**SCENE, "visual_type": "still"}
    assert visual(tmp_path, scene=still_scene)[1] == "manim"
    assert google.calls == ["image", "image"]
    assert visual(tmp_path, scene=still_scene)[1] == "manim"
    assert google.calls == ["image", "image"]
    save_json(tmp_path / "script.json", {"title": "T", "scenes": [still_scene]})
    est = pipeline.estimate_cost(1, tmp_path, allow_veo=True, skip_critic=True)
    assert est["images"] == 0, "the estimate knows attempt 1 failed"


def test_concurrent_failures_are_all_kept(tmp_path):
    log = failures.Failures(tmp_path / "failures.json")
    start = threading.Barrier(32)

    def fail(i):
        start.wait()
        log.record(f"key{i}", "still", i, RuntimeError("no image"))

    threads = [threading.Thread(target=fail, args=(i,)) for i in range(32)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(load_json(tmp_path / "failures.json")) == 32


def test_the_cli_passes_retry_failed_to_the_estimate(monkeypatch):
    seen = []

    def estimate(*a, **k):
        seen.append(k.get("retry_failed"))
        return {"claude": 0.0, "elevenlabs": 0.0, "images": 0.0, "veo": 0.0}

    monkeypatch.setattr(pipeline, "estimate_cost", estimate)
    monkeypatch.setattr(sys, "argv", ["pipeline.py", "topic", "--estimate", "--retry-failed"])
    pipeline.main()
    assert seen == [True, True]
