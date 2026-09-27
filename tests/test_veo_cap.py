"""P0-7: CLAUDE.md allows at most VEO_MAX_PER_DAY Veo clips per (UTC) day. Past it, a Veo
scene falls back to a still instead of stopping the run."""
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import config
import ledger
import pipeline
from agents import ai_video

SCENE = {"id": 1, "concept": "c", "narration": "n" * 50, "visual_type": "ai_video", "visual_description": "sky"}


def veo_entry(when: datetime) -> None:
    path = ledger.ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps({"time": when.isoformat(timespec="seconds"), "provider": "google",
                            "model": config.VEO_MODEL, "units": {"seconds": 8}, "est_cost_eur": 2.94}) + "\n")


@pytest.fixture
def google(monkeypatch):
    calls = []

    class FakeModels:
        def generate_videos(self, model, source, config):
            calls.append("veo")
            video = SimpleNamespace(save=lambda path: open(path, "wb").write(b"mp4"))
            return SimpleNamespace(done=True, error=None,
                                   response=SimpleNamespace(generated_videos=[SimpleNamespace(video=video)]))

        def generate_content(self, model, contents, config):
            calls.append("image")
            return SimpleNamespace(parts=[SimpleNamespace(inline_data=SimpleNamespace(data=b"png", mime_type="image/png"), thought=None)])

    fake = SimpleNamespace(models=FakeModels(), files=SimpleNamespace(download=lambda file: None))
    monkeypatch.setattr(config, "GOOGLE_API_KEY", "offline-test")
    monkeypatch.setattr(config, "BUDGET_EUR", 100.0)
    monkeypatch.setattr("agents.google_client.make", lambda: fake)
    return calls


def test_past_the_daily_cap_veo_is_not_called_and_the_scene_gets_a_still(google, tmp_path, capsys):
    now = datetime.now(timezone.utc)
    for _ in range(config.VEO_MAX_PER_DAY):
        veo_entry(now)
    visual, kind = pipeline.make_visual(SCENE, 5.0, tmp_path, allow_veo=True)
    assert kind == "still"
    assert google == ["image"]
    assert "Veo daily cap reached" in capsys.readouterr().out


def test_a_cached_clip_is_still_used_past_the_cap(google, tmp_path):
    ai_video.render_scene(SCENE, tmp_path)  # made (and counted) today
    for _ in range(config.VEO_MAX_PER_DAY):
        veo_entry(datetime.now(timezone.utc))
    visual, kind = pipeline.make_visual(SCENE, 5.0, tmp_path, allow_veo=True)
    assert kind == "ai" and google == ["veo"]


def test_clips_from_yesterday_and_todays_images_do_not_count(google, tmp_path):
    yesterday = datetime.now(timezone.utc) - timedelta(days=1)
    for _ in range(config.VEO_MAX_PER_DAY):
        veo_entry(yesterday)
        ledger.record("google", config.IMAGE_MODEL, {"images": 1}, 0.06)  # today, but not Veo
    ai_video.render_scene(SCENE, tmp_path)
    assert google == ["veo"]


def test_estimate_counts_only_the_veo_clips_still_allowed_today(google, tmp_path):
    veo_entry(datetime.now(timezone.utc))  # one of today's clips used
    work = tmp_path / "w"
    work.mkdir()
    scenes = [{**SCENE, "id": i, "visual_description": f"sky {i}"} for i in range(3)]
    (work / "script.json").write_text(json.dumps({"title": "T", "scenes": scenes}))
    est = pipeline.estimate_cost(15, work, allow_veo=True, skip_critic=False)
    allowed = config.VEO_MAX_PER_DAY - 1
    assert est["veo"] == pytest.approx(allowed * ledger.video_eur(config.VEO_CLIP_S))
    assert est["images"] == pytest.approx((3 - allowed) * ledger.image_eur())
