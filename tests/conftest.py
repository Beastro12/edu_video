import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def ff(*args):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *args], check=True)


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
