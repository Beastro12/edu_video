"""P1-13 (D21): ElevenLabs' Eleven v4 (reportedly launched 2026-09-28) is ready as a one-line
switch, and `eleven_multilingual_v2` stays the default. v4 reportedly takes only stability and
similarity, no speed (D21, unverified), so it is sent only those. The default model's request
and cache key are unchanged, so no narration already paid for is bought again."""
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

import config
import ledger
from agents import voice
from utils import content_key

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEXT = "Far above, the stars keep their slow and quiet watch."


@pytest.fixture
def sent(monkeypatch, media):
    requests_made = []

    def post(url, headers, json, timeout):
        requests_made.append(json)
        return SimpleNamespace(status_code=200, content=(media / "n_short.mp3").read_bytes(), text="", headers={})

    monkeypatch.setattr(voice.requests, "post", post)
    monkeypatch.setattr(config, "BUDGET_EUR", 100.0)
    return requests_made


def test_eleven_v4_is_sent_only_the_settings_it_takes(sent, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "TTS_MODEL", "eleven_v4")
    voice.narrate(TEXT, tmp_path)
    [body] = sent
    assert body["model_id"] == "eleven_v4"
    assert set(body["voice_settings"]) == {"stability", "similarity_boost"}
    assert body["voice_settings"]["stability"] == config.VOICE_SETTINGS["stability"]


def test_the_default_model_request_and_cache_key_are_unchanged(sent, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "TTS_MODEL", "eleven_multilingual_v2")
    out = voice.narrate(TEXT, tmp_path)
    [body] = sent
    assert body["voice_settings"] == config.VOICE_SETTINGS, "speed and the rest still sent"
    # the key every narration so far was cached under (P0-1): a changed key would pay again
    before = content_key("tts", TEXT, config.ELEVENLABS_VOICE_ID, "eleven_multilingual_v2",
                         config.VOICE_SETTINGS, voice.URL)
    assert out.name == f"{before}.mp3"


def test_another_model_is_another_narration(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "TTS_MODEL", "eleven_multilingual_v2")
    first = voice.cache_path(TEXT, tmp_path)
    monkeypatch.setattr(config, "TTS_MODEL", "eleven_v4")
    assert voice.cache_path(TEXT, tmp_path) != first


def test_the_ledger_prices_narration_by_model(monkeypatch):
    rate = config.USD_TO_EUR
    for model, usd in config.TTS_USD_PER_1K_CHARS.items():
        monkeypatch.setattr(config, "TTS_MODEL", model)
        assert ledger.tts_eur(2000) == pytest.approx(2 * usd * rate)
    monkeypatch.setattr(config, "TTS_MODEL", "eleven_v9_not_listed")
    assert ledger.tts_eur(2000) == pytest.approx(2 * config.TTS_USD_PER_1K_CHARS_UNLISTED * rate)
    assert config.TTS_USD_PER_1K_CHARS_UNLISTED >= max(config.TTS_USD_PER_1K_CHARS.values())


def test_the_default_is_multilingual_v2_and_env_can_choose_v4():
    # dotenv disabled: a TTS_MODEL in Pietro's .env must not change what this test sees
    code = "import dotenv; dotenv.load_dotenv = lambda *a, **k: False; import config; print(config.TTS_MODEL)"
    env = {k: v for k, v in os.environ.items() if k != "TTS_MODEL"}
    run = lambda e: subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=e,  # noqa: E731
                                   capture_output=True, text=True, check=True).stdout.strip()
    assert run(env) == "eleven_multilingual_v2"
    assert run({**env, "TTS_MODEL": "eleven_v4"}) == "eleven_v4"
