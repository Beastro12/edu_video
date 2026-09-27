"""Automated QA of a finished film. Measures it against the targets in config.py, writes
qa.json next to it, and exits non-zero if any check fails.

    python qa.py build/<slug>/<final>.mp4

Needs the build folder the pipeline left: script.json, manifest.json with its clips,
narrated.mp4 and (for the ducking check) music_bed.wav."""
import array
import json
import math
import re
import statistics
import sys
import tempfile
from pathlib import Path

import config
from assembly import mix_graph, mix_key, xfade_offsets
from utils import is_fresh, load_json, run, video_duration

WINDOW_S = 0.1


def levels_db(inputs: list[Path], graph: str | None = None) -> list[float]:
    """RMS level (dBFS) per WINDOW_S of the first input's audio, or of a graph's [out]."""
    with tempfile.TemporaryDirectory() as d:
        raw = Path(d) / "a.raw"
        args = sum((["-i", str(p)] for p in inputs), [])
        args += ["-filter_complex", graph, "-map", "[out]"] if graph else ["-map", "0:a"]
        run(["ffmpeg", "-v", "error", "-y", *args, "-ac", "1", "-ar", "8000", "-f", "s16le", str(raw)])
        samples = array.array("h", raw.read_bytes())
    step = int(8000 * WINDOW_S)
    out = []
    for i in range(0, len(samples), step):
        window = samples[i:i + step]
        rms = math.sqrt(sum(x * x for x in window) / len(window)) / 32768
        out.append(20 * math.log10(max(rms, 1e-9)))
    return out


def loudness(path: Path) -> tuple[float, float]:
    """(integrated loudness LUFS, true peak dBTP), EBU R128."""
    log = run(["ffmpeg", "-nostats", "-i", str(path), "-map", "0:a", "-af", "ebur128=peak=true",
               "-f", "null", "-"]).stderr
    summary = log[log.rfind("Summary:"):]
    integrated = float(re.search(r"I:\s+(-?[\d.]+) LUFS", summary).group(1))
    peak = float(re.search(r"Peak:\s+(-?[\d.]+|-inf) dBFS", summary).group(1))
    return integrated, peak


def black_segments(path: Path) -> list[tuple[float, float]]:
    log = run(["ffmpeg", "-nostats", "-i", str(path), "-map", "0:v",
               "-vf", f"blackdetect=d={config.QA_BLACK_MIN_S}:pix_th={config.QA_BLACK_PIX_TH}",
               "-f", "null", "-"]).stderr
    return [(float(a), float(b)) for a, b in re.findall(r"black_start:([\d.]+) black_end:([\d.]+)", log)]


def check(passed: bool, value, target: str, detail: str = "") -> dict:
    return {"passed": passed, "value": value, "target": target, **({"detail": detail} if detail else {})}


def check_duration(final: Path, script: dict) -> dict:
    words = sum(len(s["narration"].split()) for s in script["scenes"])
    n = len(script["scenes"])
    expected = (words / config.WORDS_PER_MIN * 60 + n * (config.LEAD_IN_S + config.TAIL_S)
                - (n - 1) * config.XFADE_S)
    actual = video_duration(final)
    ratio = actual / expected
    return check(abs(ratio - 1) <= config.QA_DURATION_TOL, round(actual, 2),
                 f"{expected:.1f} s ±{config.QA_DURATION_TOL:.0%} (script: {words} words at {config.WORDS_PER_MIN} wpm)")


def check_scene_count(work: Path, script: dict) -> dict:
    """The duration check can't see one missing scene (a few % of a film); this can."""
    made, planned = len(load_json(work / "manifest.json")["scenes"]), len(script["scenes"])
    return check(made == planned, made, f"{planned} scenes, as in script.json")


def check_loudness(final: Path) -> tuple[dict, dict]:
    integrated, peak = loudness(final)
    return (check(abs(integrated - config.TARGET_LUFS) <= config.QA_LOUDNESS_TOL_LU, integrated,
                  f"{config.TARGET_LUFS} LUFS ±{config.QA_LOUDNESS_TOL_LU} LU"),
            check(peak <= config.QA_MAX_TRUE_PEAK_DBTP, peak, f"≤ {config.QA_MAX_TRUE_PEAK_DBTP} dBTP"))


def check_ducking(final: Path) -> dict:
    """How much quieter the music is under the voice than in the pauses, in the film as mixed.

    1. The film's stamp must match what add_music would make now from this folder's narration
       and bed; otherwise it was mixed some other way and nothing below would describe it.
    2. The same mix graph is rendered before the master twice: as is, and with the voice muted
       after it has keyed the sidechain. The second is the music exactly as it was mixed.
    3. The master's gain per window is the film's level minus the pre-master mix; adding it to
       the music gives the music as heard in the film, pumping of the final loudnorm included.
    4. Compare it while the voice speaks (compressor settled) with real pauses (compressor
       released: QA_PAUSE_S of silence)."""
    work = final.parent
    narrated, bed = work / "narrated.mp4", work / "music_bed.wav"
    target = f"≥ {config.QA_MIN_DUCKING_DB} dB"
    if not bed.exists():
        return check(True, None, target, "skipped: no music bed")
    if not is_fresh(final, mix_key(narrated, bed)):
        return check(False, None, target, "the film wasn't mixed from this folder's narration and bed "
                                          "with the current settings; rebuild it")
    total = video_duration(narrated)
    voice = levels_db([narrated])
    premaster = levels_db([narrated, bed], mix_graph(total, master=False) + ";[aout]anull[out]")
    music = levels_db([narrated, bed], mix_graph(total, voice_gain=0, master=False) + ";[aout]anull[out]")
    heard = [m + (f - p) for m, f, p in zip(music, levels_db([final]), premaster, strict=False)]
    edge = int(config.MUSIC_FADE_S / WINDOW_S)  # the bed's own fade-in / fade-out
    settled, pause = round(config.QA_ATTACK_S / WINDOW_S), round(config.QA_PAUSE_S / WINDOW_S)
    speaking, pausing = [], []
    for i in range(max(edge, pause), min(len(voice), len(heard)) - edge):
        if all(v > config.QA_SPEECH_DB for v in voice[i - settled:i + 1]):
            speaking.append(heard[i])
        elif all(v < config.QA_SILENCE_DB for v in voice[i - pause:i + 1]):
            pausing.append(heard[i])
    if not speaking or not pausing:
        return check(False, None, target, f"no {'speech' if not speaking else 'pause'} long enough to "
                                          "compare: the music never gets to come back")
    depth = statistics.median(pausing) - statistics.median(speaking)
    return check(depth >= config.QA_MIN_DUCKING_DB, round(depth, 1), target,
                 f"{len(speaking)} speech / {len(pausing)} pause windows")


def check_black(final: Path) -> dict:
    total = video_duration(final)
    allowed = config.FADE_IN_S + WINDOW_S, total - config.FADE_OUT_S - WINDOW_S
    bad = [(round(a, 2), round(b, 2)) for a, b in black_segments(final)
           if not (b <= allowed[0] or a >= allowed[1])]
    return check(not bad, bad, "no black except the opening/closing fades")


def check_crossfades(work: Path) -> dict:
    """No narration inside any crossfade: those overlaps must sit in scenes' silent tails (D5)."""
    manifest = load_json(work / "manifest.json")["scenes"]
    durs = [video_duration(work / m["clip"]) for m in manifest]
    voice = levels_db([work / "narrated.mp4"])
    loud = []
    for offset in map(float, xfade_offsets(durs)):
        window = voice[int(offset / WINDOW_S):int((offset + config.XFADE_S) / WINDOW_S)]
        if window and max(window) > config.QA_SILENCE_DB:
            loud.append(round(offset, 2))
    return check(not loud, loud, f"voice below {config.QA_SILENCE_DB} dBFS in every crossfade")


def run_qa(final: Path) -> dict:
    work = final.parent
    script = load_json(work / "script.json")
    loud, peak = check_loudness(final)
    checks = {"duration": check_duration(final, script), "scene_count": check_scene_count(work, script),
              "loudness": loud, "true_peak": peak,
              "ducking": check_ducking(final), "black_frames": check_black(final),
              "narration_in_crossfade": check_crossfades(work)}
    report = {"video": final.name, "passed": all(c["passed"] for c in checks.values()), "checks": checks}
    (work / "qa.json").write_text(json.dumps(report, indent=2, ensure_ascii=False))
    return report


def print_report(report: dict) -> None:
    print(f"QA {'PASSED' if report['passed'] else 'FAILED'}: {report['video']}")
    for name, c in report["checks"].items():
        mark = "ok  " if c["passed"] else "FAIL"
        print(f"  {mark} {name:<24} {c['value']}  (target {c['target']}){'  ' + c['detail'] if 'detail' in c else ''}")


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    report = run_qa(Path(argv[1]))
    print_report(report)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
