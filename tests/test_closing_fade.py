"""P1-8: at the end of the film only the music bed fades out. The last narration keeps its level
to its last word (before, `afade` ran after `amix` and faded the voice as well).
P2-7: the picture's fade to black is as late: it starts after the last narration ends.

The test narrations are sine tones, one pitch per scene (conftest.build_film: 200 + 30*i Hz),
and the music is a 220 Hz tone, so a narrow band around a scene's pitch hears that scene's voice
alone in the finished film."""
import statistics
import subprocess

import pytest
from conftest import SPEECH_S, build_film, decoded_audio, ff, onset_s

import config
from utils import duration

LAST, EARLIER = len(SPEECH_S), len(SPEECH_S) - 1  # scene numbers
EDGE_S = 0.3  # skip each narration's attack and release
LUMA_TOL = 0.5  # of 255: the test picture is static after its first 1.5 s, so a steady frame repeats exactly


def pitch(scene: int) -> int:
    return 200 + 30 * scene


def band(film, hz: int, tmp_path) -> list[float]:
    """The film's audio through a narrow band-pass around one pitch: RMS dB per 10 ms."""
    out = tmp_path / f"band_{hz}.wav"
    narrow = f"bandpass=f={hz}:width_type=q:w=30"
    ff("-i", str(film), "-vn", "-af", f"{narrow},{narrow}", str(out))
    return decoded_audio(out)[1]


def level(levels: list[float], start_s: float, end_s: float) -> float:
    return statistics.median(levels[round(start_s * 100):round(end_s * 100)])


def narration(film, scene: int, tmp_path) -> tuple[float, float, list[float]]:
    """(start, end, band levels). The start is where the band first comes within 6 dB of its
    peak (a lower threshold catches the broadband click where another scene's tone starts); the
    end is the start plus the narration file's own length, which doesn't depend on its level
    (a fade would pull a level-based end earlier and hide itself)."""
    levels = band(film, pitch(scene), tmp_path)
    start = onset_s(levels, max(levels) - 6)
    return start, start + duration(film.parent / f"n{scene}.mp3"), levels


def test_the_last_narration_keeps_its_level_while_the_music_fades(media, small, tmp_path):
    """Like for like against the narration before it: each scene's voice levelling (single-pass
    loudnorm) plays every narration's first second ~1.7 dB louder than the rest (P2-6), before
    any mixing, so the last narration is compared with an earlier one over the same windows,
    not with its own start."""
    film = build_film(tmp_path / "film", media)
    windows = {}
    for scene in (EARLIER, LAST):
        start, end, voice = narration(film, scene, tmp_path)
        windows[scene] = {"whole": level(voice, start + EDGE_S, end - EDGE_S),
                          "last second": level(voice, end - 1.0, end - EDGE_S)}
    for part in ("whole", "last second"):
        gap = windows[LAST][part] - windows[EARLIER][part]
        assert abs(gap) <= 1.0, f"last narration ({part}) {gap:+.1f} dB against the one before"

    total_s, mix = decoded_audio(film)
    before = level(mix, start - 0.8, start - EDGE_S)  # the pause before the last narration: music only
    closing = level(mix, total_s - 0.4, total_s - 0.1)
    assert closing <= before - 10, f"music only {before - closing:.1f} dB down at the very end"


def luma(film) -> list[float]:
    """Mean luma (0-255) of every video frame of the film."""
    log = subprocess.run(["ffmpeg", "-v", "error", "-i", str(film), "-map", "0:v",
                          "-vf", "signalstats,metadata=print:key=lavfi.signalstats.YAVG:file=-",
                          "-f", "null", "-"], capture_output=True, text=True, check=True).stdout
    return [float(line.split("=")[1]) for line in log.splitlines() if "YAVG" in line]


def test_the_picture_does_not_darken_while_the_last_narration_sounds(media, small, tmp_path):
    """P2-7: the 2.5 s fade to black used to start 1.1 s before the last word ended (FADE_OUT_S
    longer than TAIL_S). Measured on a built film: the picture's brightness over the whole last
    narration equals its brightness when the voice starts, and the fade begins only after the voice
    has gone and ends the film in black."""
    film = build_film(tmp_path / "film", media)
    start, end, voice = narration(film, LAST, tmp_path)
    sounding = [i for i, db in enumerate(voice) if db > max(voice) - 6]
    assert (sounding[-1] + 1) / 100 == pytest.approx(end, abs=0.1), "the narration's end is where it is heard to stop"

    frames = luma(film)
    first, last = round(start * config.FPS), round(end * config.FPS)
    steady = frames[first]
    assert steady > 100, "a lit picture to start from"
    darkest = min(frames[first:last + 1])
    assert steady - darkest <= LUMA_TOL, f"picture {steady - darkest:.1f} (of 255) darker while the voice sounds"

    fade = next(i for i in range(last, len(frames)) if frames[i] < steady - LUMA_TOL) / config.FPS
    assert fade >= end, f"the fade starts {end - fade:.2f} s before the last narration ends"
    assert fade == pytest.approx(len(frames) / config.FPS - config.FADE_OUT_S, abs=0.2), "and it is the whole fade"
    assert frames[-1] < 20, f"the film ends in black (luma {frames[-1]:.1f}; black is 16)"
