"""P2-6: a narration is levelled by one constant gain, so it holds its level from its first second.

The scene clip used to level the voice with a single-pass `loudnorm` (dynamic mode), which plays a
steady narration's first second ~1.7 dB louder than the rest (loudnorm's start-up; found in P1-8). Now
the gain is measured first (EBU R128 integrated loudness of the scene's own audio) and applied as a
`volume`; a limiter joins only when that gain would push the true peak over VOICE_TP_DBTP. Narrations
are tones, noise and a peaky synthetic voice; measurements are of decoded samples (D12)."""
import itertools
import json
import math
import re
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import decoded_audio, fake_ffmpeg, ff, onset_s

import assembly
import config
import qa
from utils import duration

SECONDS = 6.0
EDGE_S = 0.3  # skip each narration's attack and release
folders = itertools.count()


PEAKY = {  # kind: (pink-noise body amplitude, burst length in ms, seconds between bursts)
    "peaky": (0.2, 1, 1.0),        # -29 LUFS, peaks 23 dB above it: the +11 dB to VOICE_LUFS puts them 7 dB over the ceiling
    "peakier": (0.15, 2, 0.25),    # the limiter eats 2.1 LU of the gain unless it is made up
    "all peaks": (0.0001, 1, 1.0),  # the body is inaudible: the loudness is all in the bursts
}


def narration(tmp_path, kind: str) -> Path:
    """A 6 s mono 44.1 kHz mp3 like ElevenLabs returns: a 300 Hz tone, steady pink noise, or one of
    the PEAKY noises, whose bursts (white noise, amplitude 0.5: broadband like a plosive, so the
    peak between two samples is higher than either) start half a period in."""
    out = tmp_path / f"{kind}.mp3"
    if kind in PEAKY:
        amplitude, ms, every = PEAKY[kind]
        sources = ["-f", "lavfi", "-i", f"anoisesrc=color=pink:amplitude={amplitude}:seed=5:d={SECONDS}:sample_rate=44100",
                   "-f", "lavfi", "-i", f"aevalsrc=0.5*lt(mod(t+{every / 2}\\,{every})\\,{ms / 1000})*(2*random(0)-1)"
                                        f":d={SECONDS}:s=44100", "-filter_complex", "amix=inputs=2:normalize=0"]
    else:
        sources = ["-f", "lavfi", "-i", {"tone": f"sine=f=300:d={SECONDS}:sample_rate=44100",
                                         "noise": f"anoisesrc=color=pink:amplitude=0.1:seed=3:d={SECONDS}:sample_rate=44100"}[kind]]
    ff(*sources, "-ar", "44100", "-ac", "1", str(out))
    return out


def scene_wav(narr: Path, tmp_path: Path) -> Path:
    """The scene's audio exactly as build_scene_clip filters it, before AAC: 32-bit float samples."""
    n = assembly.scene_frames(duration(narr))
    samples = n * config.AUDIO_RATE // config.FPS
    af = assembly.scene_audio_filter(narr, tmp_path / f"level{next(folders)}", samples)
    out = tmp_path / f"{narr.stem}.wav"
    ff("-i", str(narr), "-af", af, "-c:a", "pcm_f32le", str(out))
    return out


def power_db(levels: list[float], start_s: float, end_s: float) -> float:
    """Mean power (dB) of the 10 ms levels between two times."""
    window = levels[round(start_s * 100):round(end_s * 100)]
    return 10 * math.log10(sum(10 ** (db / 10) for db in window) / len(window))


def clip_audio(media, narr: Path, tmp_path: Path) -> tuple[Path, list[float]]:
    clip = assembly.build_scene_clip(media / "manim.mp4", narr, tmp_path / "clips", "manim", 1)
    return clip, decoded_audio(clip)[1]


# --- the voice-only track ------------------------------------------------------------------------

def gain_per_second(narr: Path, levels_after: list[float]) -> list[float]:
    """Mean dB the levelling added to the narration in each second of it (the clip's 10 ms levels
    against the narration's own, shifted by the lead-in): a steady noise varies from second to second
    by itself, so its level alone says nothing; the gain does. Skips the first 50 ms (attack)."""
    before = decoded_audio(narr)[1]
    shift = round(config.LEAD_IN_S * 100)
    seconds = []
    for second in range(int(SECONDS)):
        window = range(5 if second == 0 else second * 100, min((second + 1) * 100, len(before)))
        gains = [levels_after[w + shift] - before[w] for w in window if before[w] > -60]
        seconds.append(sum(gains) / len(gains))
    return seconds


@pytest.mark.parametrize("kind", ["tone", "noise"])
def test_a_steady_narration_holds_its_level_from_its_first_second(small, media, tmp_path, kind):
    """The acceptance test of P2-6, on the clip's audio (the voice alone, before any mixing): the
    gain is the same in every second of a steady narration, the first one included (it was
    +1.85 dB more in the first second, then flat). A steady tone also holds its own level."""
    narr = narration(tmp_path, kind)
    clip, levels = clip_audio(media, narr, tmp_path)
    assert onset_s(levels) == pytest.approx(config.LEAD_IN_S, abs=0.02)
    seconds = gain_per_second(narr, levels)
    for second, gain in enumerate(seconds[:-1]):  # the last second ends on the release
        assert gain == pytest.approx(seconds[2], abs=0.5), f"second {second + 1}: {gain:+.2f} dB, the rest {seconds[2]:+.2f}"
    if kind == "tone":
        start = onset_s(levels)
        rest = power_db(levels, start + 1.0, start + SECONDS - EDGE_S)
        assert power_db(levels, start + 0.1, start + 1.0) == pytest.approx(rest, abs=0.5)


@pytest.mark.parametrize("kind", ["tone", "noise", "peaky", "peakier"])
def test_every_narration_is_at_the_voice_level(small, media, tmp_path, kind):
    """VOICE_LUFS +- 0.5 LU (integrated loudness of the scene's audio, EBU R128), however the
    narration was recorded; the peaky ones need the limiter and still get there."""
    clip, _ = clip_audio(media, narration(tmp_path, kind), tmp_path)
    integrated, _ = qa.loudness(clip)
    assert integrated == pytest.approx(config.VOICE_LUFS, abs=0.5)


@pytest.mark.parametrize("kind", ["tone", "noise"])
def test_the_gain_is_exact_to_the_resolution_of_the_reading(tmp_path, kind):
    """Before AAC the scene's audio is at VOICE_LUFS to 0.1 LU, the resolution of an EBU R128
    reading: the gain was read from the scene's own audio, lead-in and tail included (read from
    the bare narration, a 6 s tone comes out 0.2 LU low, the short edge blocks counting for more)."""
    integrated, _ = qa.loudness(scene_wav(narration(tmp_path, kind), tmp_path))
    assert integrated == pytest.approx(config.VOICE_LUFS, abs=0.1)


@pytest.mark.parametrize("kind", ["peaky", "peakier"])
def test_a_peaky_narration_gets_a_limiter_not_a_smaller_gain(tmp_path, kind):
    """Hard case: a linear gain to VOICE_LUFS would put the true peak 6-7 dB over the ceiling. The
    peaks are limited, the true peak stays under it, and the loudness still reaches VOICE_LUFS (a
    gain kept small enough to clear the ceiling would be that far short)."""
    narr = narration(tmp_path, kind)
    untouched = tmp_path / "untouched.wav"
    ff("-i", str(narr), "-af", "aresample=48000,aformat=channel_layouts=stereo", str(untouched))
    loudness, peak = qa.loudness(untouched)
    over = peak + config.VOICE_LUFS - loudness - config.VOICE_TP_DBTP
    assert over > 3, f"the test narration isn't a hard case: {over:+.1f} dB over the ceiling"

    integrated, true_peak = qa.loudness(scene_wav(narr, tmp_path))
    assert true_peak <= config.VOICE_TP_DBTP
    assert integrated == pytest.approx(config.VOICE_LUFS, abs=0.5)


def test_the_limiter_leaves_the_body_of_a_narration_at_one_level(tmp_path):
    """Away from the bursts the gain is one constant from the first second on (a two-pass
    `loudnorm` falls back to its dynamic mode here, and its start-up comes back)."""
    narr = narration(tmp_path, "peaky")
    before, after = decoded_audio(narr)[1], decoded_audio(scene_wav(narr, tmp_path))[1]
    shift = round(config.LEAD_IN_S * 100)
    gains = [(w / 100, after[w + shift] - before[w]) for w in range(5, len(before) - 30)
             if 0.2 <= ((w / 100) - 0.5) % 1.0 <= 0.8]  # 0.2 s after a burst (the release) to 0.2 s before the next
    first = [g for t, g in gains if t < 1.0]
    rest = [g for t, g in gains if t >= 1.0]
    assert first and rest
    assert max(rest) - min(rest) < 0.2 and abs(sum(first) / len(first) - sum(rest) / len(rest)) < 0.2


def test_a_narration_whose_loudness_is_all_in_its_peaks_is_capped_and_reported(tmp_path, capsys):
    """The loop that makes up the limiter's loss can't ask for the impossible: here every dB of gain
    is eaten by the limiter, so it stops at +3 dB over the linear gain, keeps the best reading, and
    says so, with the true peak still under the ceiling."""
    narr = narration(tmp_path, "all peaks")
    wav = scene_wav(narr, tmp_path)
    assert "its loudness is in its peaks" in capsys.readouterr().out
    saved = next(tmp_path.glob("level*/level_*.json"))
    found = json.loads(saved.read_text())
    assert found["limited"] and found["gain_db"] <= config.VOICE_LUFS - found["measured_lufs"] + config.LEVEL_MAX_BOOST_DB
    assert qa.loudness(wav)[1] <= config.VOICE_TP_DBTP


# --- sample-exact timing (D12) -----------------------------------------------------------------------

def samples_of(path: Path) -> list[float]:
    """The left channel of a stereo wav of 32-bit float samples (the wave module reads only PCM)."""
    import struct
    raw = path.read_bytes()
    start = raw.index(b"data") + 8
    count = (len(raw) - start) // 8
    return list(struct.unpack(f"<{count * 2}f", raw[start:start + count * 8]))[0::2]


@pytest.mark.parametrize("peaky", [False, True], ids=["gain only", "with the limiter"])
def test_levelling_moves_nothing_by_a_single_sample(tmp_path, peaky):
    """Gain, even a changing one, never flips a sample's sign, so the signs of the levelled scene
    are those of the narration behind its lead-in, sample for sample (measured: 99.994-100%). A
    delay of one sample would disagree on 13% of them, the limiter's uncompensated lookahead
    (239 samples) on 35%. The scene is exactly its whole frames long."""
    narr = narration(tmp_path, "peaky" if peaky else "noise")
    wav = scene_wav(narr, tmp_path)
    samples = assembly.scene_frames(duration(narr)) * config.AUDIO_RATE // config.FPS
    delayed = tmp_path / "delayed.wav"
    ff("-i", str(narr), "-af", f"aresample={config.AUDIO_RATE},aformat=channel_layouts=stereo,"
       f"adelay=delays={int(config.LEAD_IN_S * 1000)}:all=1", "-c:a", "pcm_f32le", str(delayed))
    out, ref = samples_of(wav), samples_of(delayed)
    assert len(out) == samples
    same = sum((a > 0) == (b > 0) for a, b in zip(out, ref, strict=False))
    # a few samples sit within the resampler's rounding of zero (-104 dB)
    assert same / len(ref) > 0.9995, f"signs agree on {same / len(ref):.4%}"


# --- the measurement is cached, and clips rebuild from it ---------------------------------------------

@pytest.fixture
def ffmpeg_calls(monkeypatch):
    """Every FFmpeg command assembly runs, split into loudness readings and encodes."""
    calls = SimpleNamespace(readings=[], encodes=[])
    real = assembly.run

    def spy(cmd, cwd=None):
        (calls.readings if cmd[-3:] == ["-f", "null", "-"] else calls.encodes).append(cmd)
        return real(cmd, cwd)

    monkeypatch.setattr(assembly, "run", spy)
    return calls


def test_a_narration_is_measured_once_and_a_cached_clip_is_found_without_measuring(small, media, ffmpeg_calls, tmp_path):
    narr = narration(tmp_path, "noise")
    clip = assembly.build_scene_clip(media / "manim.mp4", narr, tmp_path / "clips", "manim", 1)
    assert (len(ffmpeg_calls.readings), len(ffmpeg_calls.encodes)) == (1, 1)

    assert assembly.build_scene_clip(media / "manim.mp4", narr, tmp_path / "clips", "manim", 1) == clip
    assert (len(ffmpeg_calls.readings), len(ffmpeg_calls.encodes)) == (1, 1), "nothing run for a cached clip"

    clip.unlink()  # the reading is kept: a lost clip is only re-encoded
    assert assembly.build_scene_clip(media / "manim.mp4", narr, tmp_path / "clips", "manim", 1) == clip
    assert (len(ffmpeg_calls.readings), len(ffmpeg_calls.encodes)) == (1, 2)


def test_a_changed_narration_is_measured_again_and_gets_another_clip(small, media, ffmpeg_calls, tmp_path):
    first = assembly.build_scene_clip(media / "manim.mp4", narration(tmp_path, "tone"), tmp_path / "clips", "manim", 1)
    other = assembly.build_scene_clip(media / "manim.mp4", narration(tmp_path, "noise"), tmp_path / "clips", "manim", 1)
    assert other != first and len(ffmpeg_calls.readings) == 2


def test_the_clip_carries_the_measured_gain_in_its_key(small, media, ffmpeg_calls, tmp_path, monkeypatch):
    """The gain is part of the clip's ffmpeg arguments, so a different VOICE_LUFS (or measurement)
    can never be served by an old clip."""
    narr = narration(tmp_path, "noise")
    quiet = assembly.build_scene_clip(media / "manim.mp4", narr, tmp_path / "clips", "manim", 1)
    monkeypatch.setattr(config, "VOICE_LUFS", -24)
    assert assembly.build_scene_clip(media / "manim.mp4", narr, tmp_path / "clips", "manim", 1) != quiet


@pytest.mark.parametrize("source", ["anullsrc=r=44100:cl=mono", "anoisesrc=amplitude=0.00003:seed=1:sample_rate=44100"],
                         ids=["silence", "hiss at -90 dBFS"])
def test_a_narration_with_nothing_to_level_is_left_alone(small, media, tmp_path, source):
    """EBU R128 reads anything under -70 LUFS as -70: a gain to -18 would be +52 dB, silence's
    hiss turned into a roar."""
    quiet = tmp_path / "quiet.mp3"
    ff("-f", "lavfi", "-i", source, "-t", "2", str(quiet))
    clip = assembly.build_scene_clip(media / "manim.mp4", quiet, tmp_path / "clips", "manim", 1)
    assert max(decoded_audio(clip)[1]) < -70


def test_ffmpeg_has_what_the_levelling_needs():
    """The levelling is checked here, not on the first limited scene of a live film: the filters, and
    every option it passes (an FFmpeg without alimiter's `latency` would delay the voice by 239 samples
    or refuse the graph)."""
    assert shutil.which("ffmpeg")

    def options(name: str) -> str:
        return subprocess.run(["ffmpeg", "-hide_banner", "-h", f"filter={name}"], capture_output=True, text=True).stdout

    limiter, reader = options("alimiter"), options("ebur128")
    passed = assembly._limiter().split("alimiter=")[1].split(",")[0]  # limit=...:attack=...:latency=1
    assert {"limit", "attack", "release", "level", "latency"} <= set(re.findall(r"(\w+)=", passed))
    for option in re.findall(r"(\w+)=", passed):
        assert re.search(rf"^\s+{option}\s", limiter, re.M), f"alimiter has no option {option!r}"
    assert re.search(r"^\s+latency\s", limiter, re.M) and re.search(r"^\s+peak\s", reader, re.M)


def test_the_limiter_filter_is_what_was_measured():
    """The text the tests above measured (0.7852 = -2.1 dBFS). A change to these settings is a change
    to every limited scene's clip key: update this line on purpose."""
    assert assembly._limiter() == ("aresample=192000,alimiter=limit=0.7852:attack=5:release=50:level=0:latency=1,"
                                   "aresample=48000")


@pytest.mark.parametrize("name, other, expected", [
    ("LIMITER_ATTACK_MS", 4, ":attack=4:"), ("LIMITER_RELEASE_MS", 40, ":release=40:"),
    ("LIMITER_RATE", 96000, "aresample=96000,"), ("LIMITER_MARGIN_DB", 0.2, "limit=0.7762:"),
    ("VOICE_TP_DBTP", -3.0, "limit=0.6998:"),
])
def test_the_limiter_filter_follows_its_settings(monkeypatch, name, other, expected):
    monkeypatch.setattr(config, name, other)
    assert expected in assembly._limiter()


# --- the decision's cache key and its tolerance ------------------------------------------------------

@pytest.fixture
def level_of(monkeypatch, tmp_path):
    """voice_gain on a stand-in narration with a fixed, quiet reading, and the level files it saved."""
    monkeypatch.setattr(assembly, "run", fake_ffmpeg)
    narr = tmp_path / "n.mp3"
    narr.write_bytes(b"not audio: the reading is faked")
    saved = tmp_path / "clips"

    def level():
        assembly.voice_gain(narr, "lead", "fit", saved)
        return sorted(saved.glob("level_*.json"))
    return level


@pytest.mark.parametrize("owner, name, other", [
    (config, "VOICE_LUFS", -20), (config, "VOICE_TP_DBTP", -3.0), (config, "LEVEL_ROUNDS", 2),
    (config, "LEVEL_MAX_BOOST_DB", 2.0), (config, "LEVEL_MAX_ERR_LU", 0.4), (config, "LIMITER_ATTACK_MS", 4),
    (config, "LIMITER_RELEASE_MS", 40), (config, "LIMITER_RATE", 96000), (config, "LIMITER_MARGIN_DB", 0.2),
    (assembly, "SILENT_LUFS", -60.0), (assembly, "LEVEL_TOL_LU", 0.2),
], ids=lambda v: v if isinstance(v, str) else "")
def test_every_setting_the_decision_depends_on_is_in_its_cache_key(level_of, monkeypatch, owner, name, other):
    """A level file is found again only for the settings that made it: with any of them changed, the
    narration is read afresh (a stale file would level it as the old settings did)."""
    assert len(level_of()) == 1 and len(level_of()) == 1, "the same settings find the same file"
    assert getattr(owner, name) != other
    monkeypatch.setattr(owner, name, other)
    assert len(level_of()) == 2, f"{name} is not in the key"


@pytest.mark.parametrize("second, reads, gain", [(-18.1, 2, 11.0), (-17.9, 2, 11.0), (-18.2, 3, 11.2)],
                         ids=["a tenth low", "a tenth high", "two tenths low"])
def test_a_reading_a_tenth_off_is_accepted_without_another_reading(level_of, monkeypatch, tmp_path, second, reads, gain):
    """-18 - -18.1 is 0.10000000000000142 in floats, which is not within a tolerance of 0.1: compared
    unrounded, every narration a tenth off cost another reading. Two tenths off still does."""
    readings = iter([(-29.0, -8.0), (second, -2.1), (-18.0, -2.1)])  # untouched, limited, limited again
    seen = []

    def read(narration, graph):
        seen.append(graph)
        return next(readings)

    monkeypatch.setattr(assembly, "_read_level", read)
    found = json.loads(level_of()[0].read_text())
    assert len(seen) == reads and found["gain_db"] == gain and found["limited"]
