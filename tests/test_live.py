"""P0-3: one live smoke run — a ~1-minute, 1-chapter film, stills + Manim, no Veo.

Costs real money (roughly €0.10-0.50). Run only via `make smoke`, once, under the budget rules
in CLAUDE.md. Spend goes to the real build/ledger.jsonl, so BUDGET_EUR still holds. Skipped when
a key is missing. Record the outcome of each check in docs/PROGRESS.md."""
import json
import shutil
from pathlib import Path

import pytest

import config
import ledger
import pipeline
from utils import duration, save_json

pytestmark = pytest.mark.live

NEEDED = ["ANTHROPIC_API_KEY", "ELEVENLABS_API_KEY", "ELEVENLABS_VOICE_ID", "GOOGLE_API_KEY"]
TOPIC = "Why the sky is dark at night"


@pytest.fixture(scope="module")
def film():
    missing = [k for k in NEEDED if not getattr(config, k)]
    if missing:
        pytest.skip(f"missing {', '.join(missing)} (see docs/NEEDS_PIETRO.md)")
    work = Path(config.BUILD_DIR) / "smoke-test"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    # One 1-minute chapter: seed the outline so the writer and critic still run for real.
    save_json(work / "outline.json", {"title": "Smoke test: " + TOPIC, "chapters": [{
        "number": 1, "title": "The dark sky", "minutes": 1,
        "summary": "If the universe is vast and full of stars, why is the night sky dark? "
                   "Light from distant galaxies has had only a finite time to reach us.",
        "key_ideas": ["Olbers' paradox", "finite age of the universe", "expansion redshifts light"]}]})
    before = len(ledger_entries())
    # Cap this run at €1 on top of the lifetime budget.
    config.BUDGET_EUR = min(config.BUDGET_EUR, ledger.spent_eur() + 1.0)
    try:
        script = pipeline.get_script(TOPIC, 1, work, skip_critic=False)
        final = pipeline.render_film(script, work, allow_veo=False, seed=TOPIC)
    except Exception as e:  # noqa: BLE001 - name what failed instead of a fixture error
        pytest.fail(f"live run failed: {type(e).__name__}: {str(e)[:500]}")
    return {"work": work, "script": script, "final": final, "calls": ledger_entries()[before:]}


def ledger_entries():
    path = ledger.ledger_path()
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def test_claude_model_name_is_accepted(film):
    assert any(e["provider"] == "anthropic" and e["model"] == config.CLAUDE_MODEL for e in film["calls"])


def test_elevenlabs_accepts_voice_settings_including_speed(film):
    # Proves the request with `speed` is accepted, not that speed is applied: listen to it.
    assert "speed" in config.VOICE_SETTINGS
    assert any(e["provider"] == "elevenlabs" for e in film["calls"])


def test_image_model_returns_an_image(film):
    manifest = json.loads((film["work"] / "manifest.json").read_text())["scenes"]
    stills = [m for m in manifest if m["kind"] == "still"]
    planned = [s for s in film["script"]["scenes"] if s["visual_type"] in ("still", "ai_video")]
    assert planned, "the writer planned no still scenes; rerun or adjust the seed outline"
    assert stills, f"every still fell back to Manim: {config.IMAGE_MODEL} failed (see run output)"
    assert any(e["provider"] == "google" and e["model"] == config.IMAGE_MODEL for e in film["calls"])


def test_film_is_about_a_minute(film):
    assert 30 < duration(film["final"]) < 150


@pytest.mark.live_veo
def test_veo_model_makes_one_clip():
    """Opt-in, NOT part of `make smoke`: costs about €3 and counts toward CLAUDE.md's 2 Veo
    clips per day. Run with `pytest -m live_veo tests/test_live.py` to verify VEO_MODEL."""
    if not config.GOOGLE_API_KEY:
        pytest.skip("missing GOOGLE_API_KEY")
    from agents import ai_video
    work = Path(config.BUILD_DIR) / "smoke-test-veo"
    config.BUDGET_EUR = min(config.BUDGET_EUR, ledger.spent_eur() + 4.0)
    scene = {"id": 1, "concept": "sky", "narration": "", "visual_type": "ai_video",
             "visual_description": "A slow drift over a calm night sky full of faint stars"}
    clip = ai_video.render_scene(scene, work)
    assert duration(clip) > 2
