"""P2-7: the film's last scene has a longer tail, so the closing fade to black starts after the last
word (the fade is FADE_OUT_S long, the ordinary tail TAIL_S shorter). Only the last scene's clip
changes; every other scene keeps its clip, and with it its cache key (D9). The test of the picture
on a built film is in test_closing_fade.py."""
import itertools
import math
from pathlib import Path

import pytest
from conftest import ROOT

import assembly
import config


def frames(narration_s: float, tail_s: float) -> int:
    return math.ceil(round((config.LEAD_IN_S + narration_s + tail_s) * config.FPS, 6))


def test_config_refuses_a_final_tail_shorter_than_the_fade(monkeypatch):
    """The assertion is in config.py itself: run its text as it is, then with a final tail 0.1 s
    shorter than the fade."""
    import dotenv
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: False)  # a .env must not change this
    source = (ROOT / "config.py").read_text()
    short = source.replace("FINAL_TAIL_S = FADE_OUT_S + 1.0", "FINAL_TAIL_S = FADE_OUT_S - 0.1")
    assert short != source, "the line this test edits has moved"

    def load(text):
        exec(compile(text, "config.py", "exec"), {"__file__": str(ROOT / "config.py")})

    load(source)
    with pytest.raises(AssertionError, match="closing fade would start before the last narration ends"):
        load(short)


def test_the_closing_fade_starts_after_the_last_word_and_the_crossfades_are_still_in_silence():
    assert config.FINAL_TAIL_S >= config.FADE_OUT_S
    assert config.XFADE_S < config.TAIL_S and config.XFADE_S < config.LEAD_IN_S  # D5, D14


def test_only_the_last_scene_gets_the_final_tail():
    assert [assembly.scene_tail(i, 4) for i in range(4)] == [config.TAIL_S] * 3 + [config.FINAL_TAIL_S]
    assert assembly.scene_tail(0, 1) == config.FINAL_TAIL_S, "a one-scene film's only scene is its last"


def test_scene_frames_default_tail_is_unchanged_and_a_given_tail_adds_its_frames():
    assert assembly.scene_frames(2.0) == frames(2.0, config.TAIL_S) == assembly.scene_frames(2.0, config.TAIL_S)
    extra = round((config.FINAL_TAIL_S - config.TAIL_S) * config.FPS)
    assert assembly.scene_frames(2.0, config.FINAL_TAIL_S) == assembly.scene_frames(2.0) + extra


@pytest.fixture
def commands(monkeypatch):
    """The FFmpeg commands that scene clips would run, without running them."""
    seen = []
    monkeypatch.setattr(assembly, "run", lambda cmd, cwd=None: seen.append(cmd) or Path(cmd[-1]).write_bytes(b"clip"))
    return seen


fresh_folder = itertools.count()  # every clip in its own folder, so none is found cached


def clip(media, tmp_path, kind="manim", tail_s=None) -> Path:
    return assembly.build_scene_clip(media / "manim.mp4", media / "n_short.mp3",
                                     tmp_path / f"clips{next(fresh_folder)}", kind, 1, tail_s)


@pytest.mark.parametrize("kind", ["still", "manim", "ai"])
def test_a_clip_is_as_long_as_its_tail_says_in_picture_and_sound(media, tmp_path, commands, kind):
    from utils import duration
    narration_s = duration(media / "n_short.mp3")
    for tail_s, want in [(None, frames(narration_s, config.TAIL_S)),
                         (config.FINAL_TAIL_S, frames(narration_s, config.FINAL_TAIL_S))]:
        clip(media, tmp_path, kind, tail_s)
        cmd = commands[-1]
        assert cmd[cmd.index("-frames:v") + 1] == str(want)
        samples = want * config.AUDIO_RATE // config.FPS
        assert f"apad=whole_len={samples},atrim=end_sample={samples}" in " ".join(cmd)


@pytest.mark.parametrize("kind", ["still", "manim", "ai"])
def test_other_scenes_keep_their_clips_and_only_the_last_one_follows_the_final_tail(media, tmp_path, commands,
                                                                                    monkeypatch, kind):
    """Cache keys (D9): an ordinary scene's clip is the same file whatever FINAL_TAIL_S is, and the
    last scene's clip is another one (a rebuilt clip costs nothing: clips aren't paid)."""
    ordinary, last = clip(media, tmp_path, kind).name, clip(media, tmp_path, kind, config.FINAL_TAIL_S).name
    assert ordinary != last
    assert clip(media, tmp_path, kind, config.TAIL_S).name == ordinary, "no tail given means TAIL_S"
    monkeypatch.setattr(config, "FINAL_TAIL_S", config.FINAL_TAIL_S + 1.0)
    assert clip(media, tmp_path, kind).name == ordinary
    assert clip(media, tmp_path, kind, config.FINAL_TAIL_S).name != last
