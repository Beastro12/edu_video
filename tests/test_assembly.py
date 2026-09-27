"""Offline tests for FFmpeg assembly. These encode the behaviour verified by hand:
voice-first timing, crossfade arithmetic, seamless music bed, final loudness."""
import json
import subprocess

import pytest
from conftest import decoded_audio, onset_s

import config
from agents.music import build_bed
from assembly import add_music, build_scene_clip, crossfade_concat, xfade_offsets
from utils import duration, video_duration

PAD = config.LEAD_IN_S + config.TAIL_S
AAC_FRAME = 1024 / config.AUDIO_RATE


def probe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                          "stream=codec_type,width,height,r_frame_rate", "-of", "json", str(path)],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)["streams"]


@pytest.fixture(scope="module")
def clips(media, tmp_path_factory):
    d = tmp_path_factory.mktemp("clips")
    return [
        build_scene_clip(media / "still.png", media / "n_short.mp3", d, "still", 1),
        build_scene_clip(media / "manim.mp4", media / "n_long.mp3", d, "manim", 2),
        build_scene_clip(media / "ai.mp4", media / "n_short.mp3", d, "ai", 3),
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


@pytest.fixture
def small(monkeypatch):
    """Low resolution: these tests are about timing, not pictures."""
    import assembly
    monkeypatch.setattr(assembly, "W", 320)
    monkeypatch.setattr(assembly, "H", 180)


@pytest.mark.parametrize("speech_s", [2.0, 2.69, 3.06, 3.344])  # 3.06 / 3.344 lost 27 / 40 ms before D12
def test_scene_audio_is_real_samples_that_end_with_the_video(small, media, tmp_path, speech_s):
    """P0-5: the lead-in must be silence *samples*, not a timestamp gap, and the audio must
    end with the video: the crossfade chain works on samples, so any gap or shortfall moves
    every later scene's voice."""
    from conftest import ff
    narration = tmp_path / "n.mp3"
    ff("-f", "lavfi", "-i", f"sine=f=300:d={speech_s}:sample_rate=44100", str(narration))
    for visual, kind in [(media / "still.png", "still"), (media / "manim.mp4", "manim")]:
        clip = build_scene_clip(visual, narration, tmp_path / "clips", kind, 1)
        video = video_duration(clip)
        assert round(video * config.FPS, 3) == round(video * config.FPS), "whole video frames"
        secs, levels = decoded_audio(clip)
        assert secs == pytest.approx(video, abs=AAC_FRAME)
        assert onset_s(levels) == pytest.approx(config.LEAD_IN_S, abs=0.02)


def test_long_chain_keeps_every_voice_in_place(small, media, tmp_path):
    """Twelve scenes of different lengths: the last scene's voice starts where its picture
    does, plus the lead-in, and narrated/final audio end with the video."""
    from conftest import ff
    clips = []
    for i in range(12):
        narration = tmp_path / f"n{i}.mp3"
        ff("-f", "lavfi", "-i", f"sine=f={250 + 20 * i}:d={2.0 + 0.137 * i:.3f}:sample_rate=44100", str(narration))
        clips.append(build_scene_clip(media / "manim.mp4", narration, tmp_path / "clips", "manim", i))
    narrated = crossfade_concat(clips, tmp_path / "narrated.mp4")

    durs = [video_duration(c) for c in clips]
    last_start = sum(durs[:-1]) - config.XFADE_S * (len(clips) - 1)
    # the film is exactly the sum of its scenes minus the overlaps, to the frame
    assert video_duration(narrated) == pytest.approx(last_start + durs[-1], abs=1e-3)
    secs, levels = decoded_audio(narrated)
    # sine-tone narrations: the last voice is the first sound after the previous voice ends,
    # which is where the previous scene's silent tail begins
    window = int((last_start + config.XFADE_S - config.TAIL_S + 0.05) * 100)
    assert window / 100 + onset_s(levels[window:]) == pytest.approx(last_start + config.LEAD_IN_S, abs=0.03)
    assert secs == pytest.approx(video_duration(narrated), abs=AAC_FRAME)

    bed = build_bed([media / "music.wav"], video_duration(narrated), tmp_path / "bed.wav", "s")
    final = add_music(narrated, bed, tmp_path / "final.mp4")
    assert decoded_audio(final)[0] == pytest.approx(video_duration(final), abs=AAC_FRAME)


@pytest.mark.parametrize("secs", [11.9333, 20.0])  # lost 39 / 75 ms before D12
def test_final_mix_audio_ends_with_the_video(media, tmp_path, secs):
    from conftest import ff
    narrated = tmp_path / "narrated.mp4"
    ff("-f", "lavfi", "-i", f"testsrc2=s=160x90:r={config.FPS}:d={secs}", "-f", "lavfi", "-i", f"sine=f=300:d={secs}",
       "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", str(config.AUDIO_RATE), "-shortest", str(narrated))
    bed = build_bed([media / "music.wav"], video_duration(narrated), tmp_path / "bed.wav", "s")
    final = add_music(narrated, bed, tmp_path / "final.mp4")
    assert decoded_audio(final)[0] == pytest.approx(video_duration(final), abs=AAC_FRAME)


def test_every_crossfade_starts_on_a_frame():
    """An offset written as 2.067 instead of 2.0667 starts the fade a frame late (the
    reviewer counted 123 vs 122 frames); a late last fade makes the film a frame longer
    than its audio."""
    frames = [62, 65, 71, 80, 59, 133, 134]
    offsets = xfade_offsets([n / config.FPS for n in frames])
    xf = round(config.XFADE_S * config.FPS)
    expected = [sum(frames[:i]) - xf * i for i in range(1, len(frames))]
    assert [round(float(o) * config.FPS, 4) for o in offsets] == expected


def test_scene_clips_are_fast_intermediates_and_the_film_gets_the_final_encode(media, tmp_path, monkeypatch):
    """P1-6 / D18: the scene clip encode dominated still-scene render time; clips are
    re-encoded by the crossfade anyway, so still and Manim clips use CLIP_X264. Veo clips
    (grainy, few) and the film use FILM_X264."""
    import assembly
    commands = []
    real = assembly.run
    monkeypatch.setattr(assembly, "run", lambda cmd, cwd=None: commands.append(cmd) or real(cmd, cwd))
    monkeypatch.setattr(assembly, "W", 320)
    monkeypatch.setattr(assembly, "H", 180)
    monkeypatch.setattr(config, "KEN_BURNS_UPSCALE", 2)  # not the default, so a hard-coded factor fails
    clips = [build_scene_clip(media / visual, media / "n_short.mp3", tmp_path, kind, 1)
             for visual, kind in (("still.png", "still"), ("manim.mp4", "manim"), ("ai.mp4", "ai"))]
    crossfade_concat(clips, tmp_path / "n.mp4")
    encodes = [c for c in commands if "-preset" in c]
    preset = lambda cmd: (cmd[cmd.index("-preset") + 1], cmd[cmd.index("-crf") + 1])  # noqa: E731
    fast, film = (config.CLIP_X264[0], str(config.CLIP_X264[1])), (config.FILM_X264[0], str(config.FILM_X264[1]))
    assert [preset(c) for c in encodes] == [fast, fast, film, film], "still, manim, ai clip; then the film"
    assert "scale=640:360" in " ".join(encodes[0]), "the still is upscaled by KEN_BURNS_UPSCALE before zoompan"
