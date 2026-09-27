import math
import shutil
import struct
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import anthropic.resources.messages  # noqa: E402
import google.genai  # noqa: E402

# Saved before the guard below replaces them, for tests that run the real SDKs against a
# mock HTTP transport (see `real_anthropic`, `real_google`).
_REAL_ANTHROPIC_CREATE = anthropic.resources.messages.Messages.create
_REAL_GENAI_CLIENT = google.genai.Client


def ff(*args):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *args], check=True)


def decoded_audio(path) -> tuple[float, list[float]]:
    """The audio as real samples, not as the container claims: (seconds, RMS dBFS per 10 ms).
    A timestamp gap in the stream plays as silence in a player but vanishes here, exactly as it
    does inside FFmpeg filter graphs such as the crossfade chain."""
    with tempfile.TemporaryDirectory() as d:
        wav = Path(d) / "a.wav"
        ff("-i", str(path), "-map", "0:a", "-ac", "1", "-ar", "8000", "-c:a", "pcm_s16le", str(wav))
        with wave.open(str(wav)) as w:
            n, rate = w.getnframes(), w.getframerate()
            samples = struct.unpack(f"<{n}h", w.readframes(n))
    step = rate // 100
    levels = []
    for i in range(0, n, step):
        window = samples[i:i + step]
        rms = math.sqrt(sum(x * x for x in window) / len(window)) / 32768
        levels.append(20 * math.log10(max(rms, 1e-9)))
    return n / rate, levels


def onset_s(levels: list[float], threshold_db: float = -40) -> float:
    """When sound first rises above the threshold (10 ms resolution)."""
    return next(i for i, db in enumerate(levels) if db > threshold_db) / 100


@pytest.fixture(autouse=True)
def offline(request, monkeypatch, tmp_path):
    """Every offline test gets its own build dir, so the spend ledger never touches build/,
    and fails loudly if it reaches a paid API it didn't mock."""
    if request.node.get_closest_marker("live") or request.node.get_closest_marker("live_veo"):
        return
    import requests

    import config

    def blocked(*args, **kwargs):
        raise AssertionError("offline test reached a paid API without mocking it")

    monkeypatch.setattr(config, "BUILD_DIR", str(tmp_path / "build"))
    monkeypatch.setattr(requests.Session, "request", blocked)  # requests.post and friends
    monkeypatch.setattr(requests, "post", blocked)
    monkeypatch.setattr(anthropic.resources.messages.Messages, "create", blocked)  # any client
    monkeypatch.setattr(google.genai, "Client", blocked)


@pytest.fixture
def real_anthropic(monkeypatch):
    """Lift the guard for the Anthropic SDK only. For tests whose client is wired to a mock
    HTTP transport, so nothing leaves the machine."""
    monkeypatch.setattr(anthropic.resources.messages.Messages, "create", _REAL_ANTHROPIC_CREATE)


@pytest.fixture
def real_google(monkeypatch):
    """Same, for the Google GenAI SDK only."""
    monkeypatch.setattr(google.genai, "Client", _REAL_GENAI_CLIENT)


@pytest.fixture(scope="session")
def media(tmp_path_factory):
    """Synthetic stand-ins for paid API outputs: narration, Manim clip, Veo clip, still, music."""
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    d = tmp_path_factory.mktemp("media")
    # 'narration': ElevenLabs returns mono 44.1k mp3; the tone is deliberately quiet so voice levelling is exercised
    ff("-f", "lavfi", "-i", "sine=f=300:d=2:sample_rate=44100", str(d / "n_short.mp3"))
    ff("-f", "lavfi", "-i", "sine=f=350:d=3:sample_rate=44100", str(d / "n_long.mp3"))
    ff("-f", "lavfi", "-i", "testsrc2=s=1920x1080:r=30:d=1.5", "-pix_fmt", "yuv420p", str(d / "manim.mp4"))
    # Veo-like: different size/fps and its own audio track, which must be discarded
    ff("-f", "lavfi", "-i", "testsrc=s=1280x720:r=24:d=2", "-f", "lavfi", "-i", "sine=f=1000:d=2",
       "-pix_fmt", "yuv420p", "-shortest", str(d / "ai.mp4"))
    ff("-f", "lavfi", "-i", "testsrc2=s=1024x576", "-frames:v", "1", str(d / "still.png"))
    ff("-f", "lavfi", "-i", "sine=f=220:d=20", "-ac", "2", str(d / "music.wav"))
    return d
