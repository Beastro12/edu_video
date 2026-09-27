"""P1-3: a Manim render more than 25% shorter than its target (the scene would end on a long
frozen frame) gets one retiming request to Claude; the render closer to the target is kept."""
import re

import pytest
from conftest import ff

import config
from agents import manim_agent
from utils import duration

SCENE = {"id": 1, "concept": "orbits", "narration": "The moon falls around us, slowly.",
         "visual_type": "manim", "visual_description": "a small circle orbits a larger one"}
TARGET = 10.0


@pytest.fixture
def studio(monkeypatch, tmp_path):
    """Fake Claude answering with scripted code; fake renderer whose clip lasts as long as the
    code's `# lasts N` comment says (or fails if the code says `# broken`)."""
    replies, asked = [], []

    def fake_text(system, messages):
        asked.append(messages[-1]["content"])
        return f"```python\nfrom manim import *\nclass DocScene(Scene):\n    pass\n{replies.pop(0)}\n```"

    def fake_render(py_file, class_name, media_dir):
        code = py_file.read_text()
        if "# broken" in code:
            raise RuntimeError("render failed")
        secs = float(re.search(r"# lasts ([\d.]+)", code).group(1))
        out = media_dir / f"{py_file.stem}_{secs}.mp4"
        media_dir.mkdir(parents=True, exist_ok=True)
        ff("-f", "lavfi", "-i", f"testsrc2=s=160x90:r=30:d={secs}", "-pix_fmt", "yuv420p", str(out))
        return out

    monkeypatch.setattr(manim_agent, "text", fake_text)
    monkeypatch.setattr(manim_agent, "_render", fake_render)
    return replies, asked


def test_a_short_render_is_retimed_once(studio, tmp_path, monkeypatch):
    replies, asked = studio
    replies[:] = ["# lasts 4.0", "# lasts 9.5"]
    measured = []
    real = manim_agent.duration
    monkeypatch.setattr(manim_agent, "duration", lambda p: measured.append(real(p)) or measured[-1])
    out = manim_agent.render_scene(SCENE, TARGET, tmp_path)
    before, after = measured[0], duration(out)
    assert before == pytest.approx(4.0, abs=0.1)
    assert after == pytest.approx(9.5, abs=0.1), f"before {before} s, after {after:.2f} s, target {TARGET} s"
    assert len(asked) == 2
    assert "4.0" in asked[1] and f"{TARGET:.1f}" in asked[1], "Claude is told the measured and target lengths"


def test_a_render_close_enough_is_left_alone(studio, tmp_path):
    replies, asked = studio
    replies[:] = [f"# lasts {TARGET * (1 - config.MANIM_MAX_SHORTFALL) + 0.2}"]
    manim_agent.render_scene(SCENE, TARGET, tmp_path)
    assert len(asked) == 1


@pytest.mark.parametrize("retimed", ["# broken", "# lasts 3.0", "# lasts 15.0"])  # 15 s would lose its end
def test_a_retime_that_fails_or_is_worse_keeps_the_original(studio, tmp_path, retimed):
    replies, asked = studio
    replies[:] = ["# lasts 4.0", retimed]
    out = manim_agent.render_scene(SCENE, TARGET, tmp_path)
    assert duration(out) == pytest.approx(4.0, abs=0.1)
    assert len(asked) == 2, "asked exactly once, never again"


def test_the_retimed_render_is_cached(studio, tmp_path):
    replies, asked = studio
    replies[:] = ["# lasts 4.0", "# lasts 9.5"]
    first = manim_agent.render_scene(SCENE, TARGET, tmp_path)
    assert manim_agent.render_scene(SCENE, TARGET, tmp_path) == first
    assert len(asked) == 2


def test_a_problem_while_retiming_keeps_the_render_and_asks_nothing_more(studio, tmp_path, monkeypatch):
    """ffprobe failing on the retimed clip must not be taken for a render error of the original."""
    replies, asked = studio
    replies[:] = ["# lasts 4.0", "# lasts 9.5"]
    real = manim_agent.duration

    def flaky(path):
        if "retimed" in str(path):
            raise RuntimeError("ffprobe crashed")
        return real(path)

    monkeypatch.setattr(manim_agent, "duration", flaky)
    out = manim_agent.render_scene(SCENE, TARGET, tmp_path)
    assert duration(out) == pytest.approx(4.0, abs=0.1)
    assert len(asked) == 2, "no 'fix the render error' round for a render that worked"
