"""Opt-in paid checks, each run only by its own marker, never by `make smoke`: this module has
no module-wide `live` mark (P1-13 review: when these sat in test_live.py, `-m live` ran them).

    pytest -m live_veo tests/test_live_optin.py        # one Veo clip, ~€3 (Veo 3.1) or ~€0.60 (Lite)
    pytest -s -m live_voice tests/test_live_optin.py   # four short narrations, ~€0.12
"""
from pathlib import Path

import pytest

import config
import ledger
import retries
from utils import duration, run


@pytest.mark.live_veo
def test_veo_model_makes_one_clip(monkeypatch):
    """Counts toward CLAUDE.md's 2 Veo clips per day. Verifies VEO_MODEL and VEO_RESOLUTION (D20)."""
    if not config.GOOGLE_API_KEY:
        pytest.skip("missing GOOGLE_API_KEY")
    from agents import ai_video
    work = Path(config.BUILD_DIR) / "smoke-test-veo"
    monkeypatch.setattr(config, "BUDGET_EUR", min(config.BUDGET_EUR, ledger.spent_eur() + 4.0))
    scene = {"id": 1, "concept": "sky", "narration": "", "visual_type": "ai_video",
             "visual_description": "A slow drift over a calm night sky full of faint stars"}
    clip = ai_video.render_scene(scene, work)
    assert duration(clip) > 2
    height = run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=height",
                  "-of", "csv=p=0", str(clip)]).stdout.strip()
    assert f"{height}p" == config.VEO_RESOLUTION, "the model honoured the requested resolution"


VOICE_LINE = ("Far above us, the stars keep their slow and quiet watch. Their light set out long ago, "
              "and it is only now, tonight, that it reaches your eyes.")


@pytest.mark.live_voice
def test_voice_models_side_by_side(monkeypatch):
    """A/B for P1-13 (D21): the same calm line from the current model; from Eleven v4 as the
    pipeline would send it (stability and similarity only); from v4 with a `[slowly]` tag
    (reportedly how v4 sets pace); and from v4 sent every VOICE_SETTING, speed included, which
    tests the report that v4 has no speed setting (a refusal, or the same pace as plain v4, means
    it has none). Listen to the files it prints; words per minute compare with WORDS_PER_MIN."""
    missing = [k for k in ("ELEVENLABS_API_KEY", "ELEVENLABS_VOICE_ID") if not getattr(config, k)]
    if missing:
        pytest.skip(f"missing {', '.join(missing)}")
    from agents import voice
    monkeypatch.setattr(config, "BUDGET_EUR", min(config.BUDGET_EUR, ledger.spent_eur() + 0.5))
    words = len(VOICE_LINE.split())
    everything = {m: s for m, s in config.TTS_SETTINGS_ACCEPTED.items() if m != "eleven_v4"}
    for label, model, text, accepted in (
            ("current", "eleven_multilingual_v2", VOICE_LINE, config.TTS_SETTINGS_ACCEPTED),
            ("v4", "eleven_v4", VOICE_LINE, config.TTS_SETTINGS_ACCEPTED),
            ("v4-slowly", "eleven_v4", "[slowly] " + VOICE_LINE, config.TTS_SETTINGS_ACCEPTED),
            ("v4-speed", "eleven_v4", VOICE_LINE, everything)):
        monkeypatch.setattr(config, "TTS_MODEL", model)
        monkeypatch.setattr(config, "TTS_SETTINGS_ACCEPTED", accepted)
        try:
            path = voice.narrate(text, Path(config.BUILD_DIR) / "voice-compare" / label)
        except retries.TransientError:
            raise  # an outage past the retries says nothing about the model: fail, don't report it
        except RuntimeError as e:  # e.g. v4 refusing `speed`: that answers the question too
            print(f"\n{label:<10} refused: {str(e)[:160]}")
            continue
        secs = duration(path)
        print(f"\n{label:<10} {model:<24} {secs:5.1f} s  {words * 60 / secs:5.0f} words/min  {path}")
        assert secs > 2
