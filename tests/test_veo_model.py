"""P1-12 (D20): no Veo 4 was found. Veo scenes keep Veo 3.1 by default, at the film's
resolution; Veo 3.1 Lite is one line in .env. The request names the model and resolution and
never `generate_audio` (the Gemini API refuses it), and the ledger prices a clip by model and
resolution."""
import functools
import json
import os
import subprocess
import sys

import httpx
import pytest

import config
import ledger
from agents import ai_video, google_client

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCENE = {"visual_description": "a dark sky"}


def test_veo_request_names_the_model_and_resolution_and_no_audio_flag(real_google, monkeypatch, tmp_path):
    """Through the real SDK on a mock transport: what actually goes over the wire."""
    sent = []

    def handler(request):
        sent.append((request.url.path, json.loads(request.content)))
        return httpx.Response(400, content=json.dumps({"error": {"code": 400, "message": "seen; stop"}}))

    monkeypatch.setattr(config, "GOOGLE_API_KEY", "offline-test")
    mock = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(google_client, "make", functools.partial(google_client.make, httpx_client=mock))
    with pytest.raises(Exception, match="400"):
        ai_video.render_scene(SCENE, tmp_path)
    [(path, body)] = sent
    assert path.endswith(f"/models/{config.VEO_MODEL}:predictLongRunning")
    assert body["parameters"]["resolution"] == config.VEO_RESOLUTION
    assert body["parameters"]["aspectRatio"] == config.ASPECT_RATIO
    assert "generateAudio" not in body["parameters"]


def test_the_default_is_veo_3_1_at_the_film_size():
    # dotenv disabled: a VEO_MODEL in Pietro's .env must not change what this test sees
    code = ("import dotenv; dotenv.load_dotenv = lambda *a, **k: False; import config; "
            "print(config.VEO_MODEL, config.VEO_RESOLUTION)")
    env = {k: v for k, v in os.environ.items() if k != "VEO_MODEL"}
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True,
                         check=True).stdout.split()
    assert out == ["veo-3.1-generate-preview", f"{config.HEIGHT}p"]
    assert "veo-3.1-lite-generate-preview" in config.VEO_USD_PER_SECOND, "Lite is priced, ready to choose"


def test_the_ledger_prices_a_clip_by_model_and_resolution(monkeypatch):
    rate = config.USD_TO_EUR
    for model, prices in config.VEO_USD_PER_SECOND.items():
        for resolution, usd in prices.items():
            monkeypatch.setattr(config, "VEO_MODEL", model)
            monkeypatch.setattr(config, "VEO_RESOLUTION", resolution)
            assert ledger.video_eur(8) == pytest.approx(8 * usd * rate)
    monkeypatch.setattr(config, "VEO_MODEL", "veo-9-not-listed")
    assert ledger.video_eur(8) == pytest.approx(8 * config.VEO_USD_PER_SECOND_UNLISTED * rate)
    priciest = max(p for prices in config.VEO_USD_PER_SECOND.values() for p in prices.values())
    assert config.VEO_USD_PER_SECOND_UNLISTED >= priciest, "an unknown model is never priced low"


def test_another_resolution_is_another_clip(monkeypatch, tmp_path):
    first = ai_video.cache_path(SCENE, tmp_path)
    monkeypatch.setattr(config, "VEO_RESOLUTION", "720p")
    assert ai_video.cache_path(SCENE, tmp_path) != first
