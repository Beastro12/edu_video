"""Offline tests for FFmpeg assembly. These encode the behaviour verified by hand:
voice-first timing, crossfade arithmetic, seamless music bed, final loudness."""
import json
import subprocess

import pytest

import config
from agents.music import build_bed
from assembly import add_music, build_scene_clip, crossfade_concat
from utils import duration

PAD = config.LEAD_IN_S + config.TAIL_S


def probe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                          "stream=codec_type,width,height,r_frame_rate", "-of", "json", str(path)],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)["streams"]


@pytest.fixture(scope="module")
def clips(media, tmp_path_factory):
    d = tmp_path_factory.mktemp("clips")
    return [
        build_scene_clip(media / "still.png", media / "n_short.mp3", d / "a.mp4", "still", 1),
        build_scene_clip(media / "manim.mp4", media / "n_long.mp3", d / "b.mp4", "manim", 2),
        build_scene_clip(media / "ai.mp4", media / "n_short.mp3", d / "c.mp4", "ai", 3),
    ]


def test_scene_length_is_set_by_narration(media, clips):
    for clip, narr in zip(clips, ["n_short", "n_long", "n_short"], strict=True):
        assert duration(clip) == pytest.approx(duration(media / f"{narr}.mp3") + PAD, abs=0.08)


def test_scene_format_is_normalised(clips):
    for clip in clips:
        v = [s for s in probe(clip) if s["codec_type"] == "video"][0]
        assert (v["width"], v["height"], v["r_frame_rate"]) == (config.WIDTH, config.HEIGHT, f"{config.FPS}/1")


def test_crossfade_total_length(clips, tmp_path):
    out = crossfade_concat(clips, tmp_path / "x.mp4")
    expected = sum(duration(c) for c in clips) - config.XFADE_S * (len(clips) - 1)
    assert duration(out) == pytest.approx(expected, abs=0.1)


def test_music_bed_repeats_short_track_to_exact_length(media, tmp_path):
    # 20 s track, 50 s video: the bed must crossfade repeats and land on the exact length
    bed = build_bed([media / "music.wav"], 50.0, tmp_path / "bed.wav", "seed")
    assert duration(bed) == pytest.approx(50.0, abs=0.05)


def test_final_mix_length_and_loudness(clips, media, tmp_path):
    narrated = crossfade_concat(clips, tmp_path / "n.mp4")
    bed = tmp_path / "bed.wav"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-stream_loop", "-1", "-i", str(media / "music.wav"),
                    "-t", str(duration(narrated)), str(bed)], check=True)
    final = add_music(narrated, bed, tmp_path / "final.mp4")
    assert duration(final) == pytest.approx(duration(narrated), abs=0.1)
    log = subprocess.run(["ffmpeg", "-i", str(final), "-af", "ebur128", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    integrated = float(log.split("Integrated loudness:")[1].split("I:")[1].split("LUFS")[0])
    assert integrated == pytest.approx(config.TARGET_LUFS, abs=2.0)
