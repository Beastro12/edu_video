"""P1-1: qa.py measures a finished film against config targets. A healthy film built with the
real assembly code passes; each injected fault fails exactly its own check."""
import json
import subprocess
import sys

import pytest
from conftest import ROOT, ff

import assembly
import config
import qa
from agents.music import build_bed
from utils import save_json, video_duration

SPEECH_S = [4.0, 5.5, 3.0, 6.0, 4.5, 5.0]  # six scenes: long enough to see ducking past the fades


def build_film(work, media, visuals=None, **overrides):
    """A small film in the pipeline's build-folder layout. `overrides` patch config for the build
    only (to inject a fault); `visuals` swaps a scene's picture."""
    saved = {k: getattr(config, k) for k in overrides}
    for k, v in overrides.items():
        setattr(config, k, v)
    try:
        work.mkdir(parents=True, exist_ok=True)
        scenes, manifest, clips = [], [], []
        for i, secs in enumerate(SPEECH_S, start=1):
            narration = work / f"n{i}.mp3"
            ff("-f", "lavfi", "-i", f"sine=f={200 + 30 * i}:d={secs}:sample_rate=44100", str(narration))
            visual, kind = (visuals or {}).get(i, (media / "manim.mp4", "manim"))
            clip = assembly.build_scene_clip(visual, narration, work / "clips", kind, i)
            words = round(secs * config.WORDS_PER_MIN / 60)  # narration text as long as the tone
            scenes.append({"id": i, "concept": f"c{i}", "narration": " ".join(["calm"] * words),
                           "visual_type": "manim", "visual_description": "d"})
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


@pytest.fixture(autouse=True)
def small(monkeypatch):
    monkeypatch.setattr(assembly, "W", 320)
    monkeypatch.setattr(assembly, "H", 180)


@pytest.fixture(scope="module")
def dark_scene(tmp_path_factory):
    """A healthy but dark scene: the Manim background with one small shape (not 'black')."""
    out = tmp_path_factory.mktemp("dark") / "dark.mp4"
    ff("-f", "lavfi", "-i", "color=c=0x0f1419:s=320x180:r=30:d=3",
       "-vf", "drawbox=x=150:y=80:w=20:h=20:color=0x7fb3d5:t=fill", "-pix_fmt", "yuv420p", str(out))
    return out


@pytest.fixture(scope="module")
def healthy(media, tmp_path_factory, dark_scene):
    assembly.W, assembly.H = 320, 180
    return build_film(tmp_path_factory.mktemp("healthy"), media, visuals={2: (dark_scene, "manim")})


def failing(report):
    return sorted(name for name, c in report["checks"].items() if not c["passed"])


CHECKS = ["duration", "scene_count", "loudness", "true_peak", "ducking", "black_frames", "narration_in_crossfade"]


def test_healthy_film_passes_and_writes_qa_json(healthy):
    report = qa.run_qa(healthy)
    assert list(report["checks"]) == CHECKS
    assert failing(report) == [], json.dumps(report, indent=1)
    assert json.loads((healthy.parent / "qa.json").read_text()) == report


def test_cli_exit_code(healthy):
    ok = subprocess.run([sys.executable, "qa.py", str(healthy)], cwd=ROOT, capture_output=True, text=True)
    assert ok.returncode == 0, ok.stdout + ok.stderr
    assert "QA PASSED" in ok.stdout


@pytest.mark.parametrize("fault, overrides, expected", [
    ("voice too quiet to trigger the sidechain (why D2 levels it)", {"VOICE_LUFS": -34}, ["ducking"]),
    # gaps of 0.8 s leave no pause long enough for the music to come back, so ducking can't be heard either
    ("lead-in shorter than the crossfade (D14)", {"LEAD_IN_S": 0.6}, ["ducking", "narration_in_crossfade"]),
    ("mixed with no ducking, checked with today's settings", {"DUCK_RATIO": 1}, ["ducking"]),
])
def test_each_fault_fails_its_own_check(media, tmp_path, fault, overrides, expected):
    final = build_film(tmp_path / "film", media, **overrides)
    assert failing(qa.run_qa(final)) == expected, fault


def test_black_scene_is_caught(media, tmp_path):
    black = tmp_path / "black.mp4"
    ff("-f", "lavfi", "-i", "color=c=black:s=320x180:r=30:d=2", "-pix_fmt", "yuv420p", str(black))
    final = build_film(tmp_path / "film", media, visuals={3: (black, "manim")})
    report = qa.run_qa(final)
    assert failing(report) == ["black_frames"]
    (start, end), = report["checks"]["black_frames"]["value"]
    assert config.FADE_IN_S < start < end < video_duration(final) - config.FADE_OUT_S


def copy_film(healthy, tmp_path):
    import shutil
    work = tmp_path / "copy"
    shutil.copytree(healthy.parent, work)
    return work


def test_film_turned_down_after_mixing_fails_loudness_only(healthy, tmp_path):
    work = copy_film(healthy, tmp_path)
    quiet = work / "quiet.mp4"
    ff("-i", str(work / healthy.name), "-map", "0", "-c:v", "copy", "-af", "volume=-8dB", "-c:a", "aac", str(quiet))
    quiet.replace(work / healthy.name)  # the mix stamp still matches: only the level changed
    report = qa.run_qa(work / healthy.name)
    assert failing(report) == ["loudness"]
    assert report["checks"]["ducking"]["passed"], "a uniform level change doesn't change ducking"


def test_film_far_shorter_than_its_script_is_caught(healthy, tmp_path):
    work = copy_film(healthy, tmp_path)
    script = json.loads((work / "script.json").read_text())
    for s in script["scenes"]:
        s["narration"] = " ".join([s["narration"]] * 3)  # the script promised three times as much
    save_json(work / "script.json", script)
    assert failing(qa.run_qa(work / healthy.name)) == ["duration"]


def test_true_peak_over_the_limit_is_caught(tmp_path):
    loud = tmp_path / "loud.mp4"
    ff("-f", "lavfi", "-i", "aevalsrc=0.99*sin(2*PI*997*t):s=48000:d=3", "-c:a", "aac", str(loud))  # ~0 dBFS
    _, peak = qa.check_loudness(loud)
    assert not peak["passed"] and peak["value"] > config.QA_MAX_TRUE_PEAK_DBTP


def test_cli_fails_a_bad_film(media, tmp_path):
    final = build_film(tmp_path / "film", media, TARGET_LUFS=-24)
    bad = subprocess.run([sys.executable, "qa.py", str(final)], cwd=ROOT, capture_output=True, text=True)
    assert bad.returncode == 1 and "FAIL loudness" in bad.stdout
    assert "FAIL ducking" in bad.stdout, "mixed with other settings: ducking can't be vouched for"


def test_pipeline_runs_qa_at_the_end_and_fails_the_run_on_a_bad_film(monkeypatch, tmp_path, capsys):
    import pipeline
    final = tmp_path / "film.mp4"
    monkeypatch.setattr(pipeline, "get_script", lambda *a: {"title": "T", "scenes": []})
    monkeypatch.setattr(pipeline, "render_film", lambda *a: final)
    checked = []
    report = {"video": final.name, "passed": False,
              "checks": {"loudness": {"passed": False, "value": -24.0, "target": "-16 LUFS"}}}
    monkeypatch.setattr(qa, "run_qa", lambda path: checked.append(path) or report)
    monkeypatch.setattr(sys, "argv", ["pipeline.py", "topic"])
    with pytest.raises(SystemExit) as stop:
        pipeline.main()
    assert stop.value.code == 1 and checked == [final]
    assert "QA FAILED" in capsys.readouterr().out


def test_no_ducking_is_caught_even_when_the_settings_agree(media, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DUCK_RATIO", 1)
    final = build_film(tmp_path / "film", media)
    report = qa.run_qa(final)
    assert failing(report) == ["ducking"]
    assert report["checks"]["ducking"]["value"] < 2


def test_a_mix_wired_to_the_unducked_bed_is_caught(media, tmp_path, monkeypatch):
    """A wiring bug in the mix itself: the voice keys the sidechain but the bed goes in raw."""
    monkeypatch.setattr(assembly, "duck_graph", lambda: (
        "[0:a]asplit=2[voice][key];" + assembly.music_graph() + ";[key]anullsink;[mus]anull[duck]"))
    final = build_film(tmp_path / "film", media)
    assert failing(qa.run_qa(final)) == ["ducking"]
