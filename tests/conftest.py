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


class PaidCallInOfflineTest(BaseException):
    """Not an Exception: the pipeline's fallbacks catch Exception, and must not hide this."""


@pytest.fixture(autouse=True)
def offline(request, monkeypatch, tmp_path):
    """Every offline test gets its own build dir, so the spend ledger never touches build/,
    and fails loudly if it reaches a paid API it didn't mock."""
    if request.node.get_closest_marker("live") or request.node.get_closest_marker("live_veo"):
        return
    import requests

    import config
    import ledger

    ledger.stopping.clear()  # no test inherits another's stopped run
    ledger.interrupted.clear()
    monkeypatch.setattr(ledger, "_held", 0.0)

    def blocked(*args, **kwargs):
        raise PaidCallInOfflineTest("offline test reached a paid API without mocking it")

    monkeypatch.setattr(config, "BUILD_DIR", str(tmp_path / "build"))
    monkeypatch.setattr(requests.Session, "request", blocked)  # requests.post and friends
    monkeypatch.setattr(requests, "post", blocked)
    monkeypatch.setattr(anthropic.resources.messages.Messages, "create", blocked)  # any client
    monkeypatch.setattr(google.genai, "Client", blocked)


@pytest.fixture
def stills_approved(monkeypatch):
    """For tests that make stills but aren't about the vision review (P1-2): every still passes."""
    from agents import still_critic
    monkeypatch.setattr(still_critic, "review", lambda scene, still: {"ok": True, "problems": []})


@pytest.fixture
def real_anthropic(monkeypatch):
    """Lift the guard for the Anthropic SDK only. For tests whose client is wired to a mock
    HTTP transport, so nothing leaves the machine."""
    monkeypatch.setattr(anthropic.resources.messages.Messages, "create", _REAL_ANTHROPIC_CREATE)


@pytest.fixture
def real_google(monkeypatch):
    """Same, for the Google GenAI SDK only."""
    monkeypatch.setattr(google.genai, "Client", _REAL_GENAI_CLIENT)


SPEECH_S = [4.0, 5.5, 3.0, 6.0, 4.5, 5.0]  # six scenes: long enough to see ducking past the fades


def build_film(work, media, visuals=None, narrations=None, chapters=None, **overrides):
    """A small film in the pipeline's build-folder layout, made with the real assembly code
    (use with the `small` fixture). Sine tones stand in for the voice, as long as their text
    at WORDS_PER_MIN. `overrides` patch config for the build only (to inject a fault);
    `visuals` swaps a scene's picture; `narrations` / `chapters` set the script."""
    import assembly
    import config
    from agents.music import build_bed
    from utils import save_json, video_duration
    saved = {k: getattr(config, k) for k in overrides}
    for k, v in overrides.items():
        setattr(config, k, v)
    try:
        work.mkdir(parents=True, exist_ok=True)
        scenes, manifest, clips = [], [], []
        texts = narrations or [" ".join(["calm"] * round(secs * config.WORDS_PER_MIN / 60)) for secs in SPEECH_S]
        for i, text in enumerate(texts, start=1):
            secs = round(len(text.split()) * 60 / config.WORDS_PER_MIN, 2)
            narration = work / f"n{i}.mp3"
            ff("-f", "lavfi", "-i", f"sine=f={200 + 30 * i}:d={secs}:sample_rate=44100", str(narration))
            visual, kind = (visuals or {}).get(i, (media / "manim.mp4", "manim"))
            clip = assembly.build_scene_clip(visual, narration, work / "clips", kind, i)
            scenes.append({"id": i, "chapter": (chapters or [1] * len(texts))[i - 1], "concept": f"c{i}",
                           "narration": text, "visual_type": "manim", "visual_description": "d"})
            manifest.append({"id": i, "concept": f"c{i}", "kind": kind, "audio": narration.name,
                             "visual": str(visual), "clip": str(clip.relative_to(work))})
            clips.append(clip)
        save_json(work / "script.json", {"title": "QA test", "scenes": scenes})
        save_json(work / "manifest.json", {"title": "QA test", "scenes": manifest})
        narrated = assembly.crossfade_concat(clips, work / "narrated.mp4")
        bed = build_bed([media / "music.wav"], video_duration(narrated), work / "music_bed.wav", "s")
        return assembly.add_music(narrated, bed, work / "qa-test.mp4")
    finally:
        for k, v in saved.items():
            setattr(config, k, v)


@pytest.fixture
def small(monkeypatch):
    """Low resolution: tests about timing and sound, not pictures."""
    import assembly
    monkeypatch.setattr(assembly, "W", 320)
    monkeypatch.setattr(assembly, "H", 180)


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
