"""P0-3: one live smoke run — a ~1-minute, 1-chapter film, stills + Manim, no Veo.

Costs real money (roughly €0.10-0.50). Run only via `make smoke`, once, under the budget rules
in CLAUDE.md. Spend goes to the real build/ledger.jsonl, so BUDGET_EUR still holds. Skipped when
a key is missing. Record the outcome of each check in docs/PROGRESS.md.
The opt-in paid checks (one Veo clip, the voice A/B) live in test_live_optin.py, so that
`make smoke` (`-m live`) can never select them."""
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
    # Proves the request is accepted with the settings this model is sent (with `speed` where
    # the model takes it), not that they're applied: listen to it.
    from agents import voice
    if config.TTS_MODEL not in config.TTS_SETTINGS_ACCEPTED:  # a model that takes every setting
        assert "speed" in voice.voice_settings()
    assert any(e["provider"] == "elevenlabs" and e["model"] == config.TTS_MODEL for e in film["calls"])


def test_image_model_returns_an_image(film):
    manifest = json.loads((film["work"] / "manifest.json").read_text())["scenes"]
    stills = [m for m in manifest if m["kind"] == "still"]
    planned = [s for s in film["script"]["scenes"] if s["visual_type"] in ("still", "ai_video")]
    assert planned, "the writer planned no still scenes; rerun or adjust the seed outline"
    assert stills, f"every still fell back to Manim: {config.IMAGE_MODEL} failed (see run output)"
    assert any(e["provider"] == "google" and e["model"] == config.IMAGE_MODEL for e in film["calls"])


def test_film_is_about_a_minute(film):
    assert 30 < duration(film["final"]) < 150


def test_film_passes_qa(film):
    import qa
    report = qa.run_qa(film["final"])
    assert report["passed"], {k: v for k, v in report["checks"].items() if not v["passed"]}
