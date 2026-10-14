"""FFmpeg assembly: fit each visual to its narration, crossfade scenes, duck music under the voice.

Timing is exact to the sample (D12): each scene is a whole number of video frames, its audio
is exactly that many samples, and the crossfade chain works from video lengths, so nothing
drifts however many scenes a film has."""
import math
from pathlib import Path

import config
from utils import (
    atomic_output,
    content_key,
    duration,
    ebur128_summary,
    file_hash,
    is_fresh,
    load_json,
    log,
    run,
    save_json,
    stamped_output,
    video_duration,
)

W, H, FPS = config.WIDTH, config.HEIGHT, config.FPS
RATE = config.AUDIO_RATE
VIDEO_ENC = ["-c:v", "libx264", "-preset", config.FILM_X264[0], "-crf", str(config.FILM_X264[1])]
AUDIO_ENC = ["-c:a", "aac", "-b:a", "192k", "-ar", str(RATE)]
# loudnorm's output timestamps skip at the end of its input; FFmpeg then drops samples under a
# duration limit. Re-stamping from the sample count fixes that (D12): the master mix needs it, and
# cutting the voice to exact samples is as safe with it.
RESTAMP = "asetpts=N/SR/TB"
SILENT_LUFS = -69.0     # ebur128 reads silence as -70.0: nothing to level
LEVEL_TOL_LU = 0.1      # a reading this close to VOICE_LUFS is accepted (it has one decimal)


def scene_tail(index: int, count: int) -> float:
    """Silence after scene `index`'s narration (0-based, of `count` scenes). The last scene's is
    longer: the closing fade must not start before the voice ends (P2-7)."""
    return config.FINAL_TAIL_S if index == count - 1 else config.TAIL_S


def scene_frames(narration_s: float, tail_s: float | None = None) -> int:
    """Lead-in + narration + tail (TAIL_S unless given), rounded up to whole video frames."""
    tail = config.TAIL_S if tail_s is None else tail_s
    return math.ceil(round((config.LEAD_IN_S + narration_s + tail) * FPS, 6))


def _still_filter(target: float, scene_id: int) -> str:
    """Slow Ken Burns drift. zoompan moves the crop in whole pixels (even ones: it runs in
    yuv420p), so the still is upscaled first (KEN_BURNS_UPSCALE) to make those steps small:
    0.73 px on screen at 3x. The drift is still stop-go at this speed (D18, P1-10)."""
    n = max(int(round(target * FPS)), 1)
    k = config.KEN_BURNS_UPSCALE
    z = config.KEN_BURNS_ZOOM
    centre_x, centre_y = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    motion = scene_id % 3
    if motion == 0:    # slow push in
        zexpr, x, y = f"1+{z}*on/{n}", centre_x, centre_y
    elif motion == 1:  # slow pull out
        zexpr, x, y = f"{1 + z}-{z}*on/{n}", centre_x, centre_y
    else:              # slow pan left to right
        zexpr, x, y = f"{1 + z}", f"(iw-iw/zoom)*on/{n}", centre_y
    return (f"scale={W * k}:{H * k}:force_original_aspect_ratio=increase,"
            f"crop={W * k}:{H * k},"
            f"zoompan=z='{zexpr}':x='{x}':y='{y}':d={n}:s={W}x{H}:fps={FPS},"
            f"setsar=1,format=yuv420p")


def pad_colour() -> str:
    """The letterbox bars match the active style's background, in FFmpeg's 0x form. Under calm
    this is the 0x0f1419 it always was, so no scene clip's cache key changes (D9)."""
    return "0x" + config.STYLES[config.STYLE]["manim_background"].lstrip("#")


def _moving_filter(target: float, v_len: float, is_ai: bool) -> str:
    slow = min(target / v_len, config.MAX_AI_SLOWDOWN) if (is_ai and v_len < target) else 1.0
    return (f"setpts={slow:.4f}*PTS,"
            f"scale={W}:{H}:force_original_aspect_ratio=decrease,"
            f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color={pad_colour()},setsar=1,fps={FPS},"
            f"tpad=stop_mode=clone:stop_duration={target:.2f},format=yuv420p")


def clip_encoder(kind: str) -> list[str]:
    """Scene clips are intermediates that the film re-encodes (D18). Stills and Manim get the
    fast CLIP_X264. Veo footage gets VEO_CLIP_X264, a slow encode: its grain loses detail
    through two fast encodes, and there are at most VEO_MAX_PER_DAY such clips, so their speed
    doesn't matter. Its own setting, so that a faster FILM_X264 doesn't also change it (D22)."""
    preset, crf = config.VEO_CLIP_X264 if kind == "ai" else config.CLIP_X264
    return ["-c:v", "libx264", "-preset", preset, "-crf", str(crf)]


def _limiter() -> str:
    """Peaks over the ceiling are limited in a LIMITER_RATE copy, where a sample peak is the true peak
    (at 48 kHz the burst peaks overshot by ~1.1 dB between samples), LIMITER_MARGIN_DB under the ceiling
    for rounding. `latency=1` hands every sample back where it came in (without it: 239 samples late, D12)."""
    limit = 10 ** ((config.VOICE_TP_DBTP - config.LIMITER_MARGIN_DB) / 20)
    return (f"aresample={config.LIMITER_RATE},alimiter=limit={limit:.4f}:attack={config.LIMITER_ATTACK_MS:g}"
            f":release={config.LIMITER_RELEASE_MS:g}:level=0:latency=1,aresample={RATE}")


def _voice_stage(gain_db: float, limited: bool) -> str:
    return f"volume={gain_db:.2f}dB" + (f",{_limiter()}" if limited else "")


def _read_level(narration: Path, graph: str) -> tuple[float, float]:
    """(integrated LUFS, true peak dBTP) of what `graph` makes of the narration."""
    return ebur128_summary(run(["ffmpeg", "-nostats", "-i", str(narration), "-af", f"{graph},ebur128=peak=true",
                                "-f", "null", "-"]).stderr)


def _miss(reading: float) -> float:
    """How far a reading is from VOICE_LUFS. Rounded: -18 - -18.1 is 0.10000000000000142 in floats,
    which is not within a tolerance of 0.1."""
    return round(abs(config.VOICE_LUFS - reading), 2)


def voice_gain(narration: Path, lead: str, fit: str, cache_dir: Path) -> tuple[float, bool]:
    """(gain in dB, whether a limiter is needed) that brings the scene's audio to VOICE_LUFS.

    One constant gain, because loudnorm in dynamic mode plays the first second ~1.7 dB louder (P2-6).
    The reading is of the scene's own audio (`lead`, then `fit`: lead-in, tail), so it is the loudness
    a test or QA measures. If that gain would push the true peak over VOICE_TP_DBTP the peaks are limited
    instead of the gain lowered (a lower gain misses VOICE_LUFS), and the loudness the limiter takes away
    is made up by re-reading the limited result. Kept in `cache_dir` by the narration's content, so a
    rerun or a rebuilt clip doesn't read it again."""
    key = content_key("voice level", file_hash(narration), lead, fit, _limiter(), config.VOICE_LUFS,
                      config.VOICE_TP_DBTP, config.LIMITER_ATTACK_MS, config.LIMITER_RELEASE_MS, config.LIMITER_RATE,
                      config.LIMITER_MARGIN_DB, config.LEVEL_ROUNDS, config.LEVEL_MAX_BOOST_DB,
                      config.LEVEL_MAX_ERR_LU, SILENT_LUFS, LEVEL_TOL_LU)  # everything the decision depends on
    saved = cache_dir / f"level_{key}.json"
    if saved.exists():
        try:
            found = load_json(saved)
            return float(found["gain_db"]), bool(found["limited"])
        except (ValueError, KeyError, TypeError):
            pass  # unreadable: read it again
    loudness, peak = _read_level(narration, f"{lead},{fit}")
    gain, limited, result = 0.0, False, loudness
    if loudness > SILENT_LUFS:
        linear = gain = round(config.VOICE_LUFS - loudness, 2)
        limited = peak + gain > config.VOICE_TP_DBTP
        result = config.VOICE_LUFS
        tried = []  # (miss in LU, gain, reading) of each limited result read
        for _ in range(config.LEVEL_ROUNDS if limited else 0):
            reading, _ = _read_level(narration, f"{lead},{_voice_stage(gain, True)},{fit}")
            tried.append((_miss(reading), gain, reading))
            if tried[-1][0] <= LEVEL_TOL_LU:
                break
            gain = round(min(gain + config.VOICE_LUFS - reading, linear + config.LEVEL_MAX_BOOST_DB), 2)
            if gain == tried[-1][1]:
                break  # at the cap already: reading it again would change nothing
        if tried:  # the best reading, not the last correction, which was never read
            _, gain, result = min(tried)
        if _miss(result) > config.LEVEL_MAX_ERR_LU:
            log(f"    {narration.name}: its loudness is in its peaks; limited to {config.VOICE_TP_DBTP} dBTP "
                f"it reaches {result} LUFS, not {config.VOICE_LUFS}")
    save_json(saved, {"measured_lufs": loudness, "true_peak_dbtp": peak, "gain_db": gain, "limited": limited,
                      "levelled_lufs": result})
    return gain, limited


def scene_audio_filter(narration: Path, cache_dir: Path, samples: int) -> str:
    """The narration as the scene's audio, exactly `samples` long: the lead-in silence, then one
    constant gain to VOICE_LUFS (D2: ducking is only predictable if every scene arrives at the same
    loudness), re-stamped and cut to the sample (D12)."""
    lead = (f"aresample={RATE},aformat=channel_layouts=stereo,"
            f"adelay=delays={int(config.LEAD_IN_S * 1000)}:all=1")
    fit = f"{RESTAMP},apad=whole_len={samples},atrim=end_sample={samples}"
    gain, limited = voice_gain(narration, lead, fit, cache_dir)
    return f"{lead},{_voice_stage(gain, limited)},{fit}"


def build_scene_clip(visual: Path, narration: Path, out_dir: Path, kind: str, scene_id: int,
                     tail_s: float | None = None) -> Path:
    """Scene length is set by the voice: lead-in + narration + tail, in whole frames.
    kind: 'still' | 'manim' | 'ai'. `tail_s` is the silence after the narration (TAIL_S unless
    given; the film's last scene gets FINAL_TAIL_S). The clip is named by a hash of its input
    files and the exact filters, so a changed narration or visual can never reuse an old clip."""
    n = scene_frames(duration(narration), tail_s)
    target, samples = n / FPS, n * RATE // FPS
    vf = (_still_filter(target, scene_id) if kind == "still"
          else _moving_filter(target, duration(visual), kind == "ai"))
    af = scene_audio_filter(narration, out_dir, samples)
    args = ["-filter_complex", f"[0:v]{vf}[v];[1:a]{af}[a]",
            "-map", "[v]", "-map", "[a]", "-frames:v", str(n),
            *clip_encoder(kind), "-r", str(FPS), *AUDIO_ENC]
    out = out_dir / f"{content_key('clip', file_hash(visual), file_hash(narration), args)}.mp4"
    if out.exists():
        return out
    with atomic_output(out) as tmp:
        run(["ffmpeg", "-y", "-i", str(visual), "-i", str(narration), *args, str(tmp)])
    return out


def xfade_offsets(durs: list[float]) -> list[str]:
    """Where each crossfade starts, as FFmpeg option text: previous scenes minus overlaps,
    counted in whole frames so rounding never starts a fade a frame late."""
    offsets, frames = [], 0
    for d in durs[:-1]:
        frames += round(d * FPS) - round(config.XFADE_S * FPS)
        offsets.append(f"{frames / FPS:.6f}")
    return offsets


def crossfade_concat(clips: list[Path], out: Path) -> Path:
    """Chain xfade/acrossfade. The overlap sits in each scene's silent tail, so
    narration never overlaps. Fades in from and out to black at the very ends.
    Offsets come from each clip's video length, and each clip's audio is trimmed to it
    first (dropping AAC's end padding), so audio and video stay locked. Rebuilt whenever
    any clip changes."""
    xf = config.XFADE_S
    durs = [video_duration(c) for c in clips]
    total = sum(durs) - xf * (len(clips) - 1)

    trims = [f"[{i}:a]atrim=end_sample={round(d * RATE)},asetpts=PTS-STARTPTS[s{i}]" for i, d in enumerate(durs)]
    parts, v, a = trims, "0:v", "s0"
    for i, offset in enumerate(xfade_offsets(durs), start=1):
        parts.append(f"[{v}][{i}:v]xfade=transition=fade:duration={xf}:offset={offset}[v{i}]")
        parts.append(f"[{a}][s{i}]acrossfade=d={xf}[a{i}]")
        v, a = f"v{i}", f"a{i}"
    graph = ";".join(parts) + (f";[{v}]fade=t=in:d={config.FADE_IN_S},"
                               f"fade=t=out:st={total - config.FADE_OUT_S:.2f}:d={config.FADE_OUT_S}[vout]")
    args = ["-filter_complex", graph, "-map", "[vout]", "-map", f"[{a}]",
            *VIDEO_ENC, "-r", str(FPS), *AUDIO_ENC]
    key = content_key("xfade", [file_hash(c) for c in clips], args)
    if is_fresh(out, key):
        return out
    inputs = sum((["-i", str(c)] for c in clips), [])
    with stamped_output(out, key) as tmp:
        run(["ffmpeg", "-y", *inputs, *args, str(tmp)])
    return out


def music_graph() -> str:
    """Input [1:a] music bed. Output [mus]: the bed at its level in the mix, before ducking."""
    return (f"[1:a]aresample={RATE},aformat=channel_layouts=stereo,"
            f"volume={config.MUSIC_VOLUME},afade=t=in:d={config.MUSIC_FADE_S}[mus]")


def duck_graph() -> str:
    """Inputs [0:a] narration, [1:a] music bed. Outputs [voice] and [duck], the bed pushed
    down by the voice. Shared with qa.py, which measures how far it ducks."""
    return ("[0:a]asplit=2[voice][key];" + music_graph() + ";"
            # sidechain: the voice pushes the music down while it speaks
            f"[mus][key]sidechaincompress=threshold=0.03:ratio={config.DUCK_RATIO}"
            ":attack=80:release=900[duck]")


def master_chain() -> str:
    return (f"loudnorm=I={config.TARGET_LUFS}:TP={config.MASTER_TP_DBTP}:LRA=11,"
            f"aresample={RATE},{RESTAMP}")


def mix_graph(total: float, voice_gain: float = 1.0, master: bool = True) -> str:
    """Inputs [0:a] narration, [1:a] bed; output [aout]. qa.py renders this same graph with
    voice_gain=0 (the voice still keys the sidechain, then is muted) to hear the music exactly
    as mixed, and with master=False to see what went into the final loudnorm."""
    return (duck_graph() + ";"
            f"[voice]volume={voice_gain}[v];"
            # only the music fades out: the last narration keeps its level to its last word (P1-8)
            f"[duck]afade=t=out:st={max(total - config.MUSIC_FADE_S, 0):.2f}:d={config.MUSIC_FADE_S}[bed];"
            "[v][bed]amix=inputs=2:duration=first:normalize=0"
            f"{',' + master_chain() if master else ''}[aout]")


def _mix_args(narrated: Path, bed: Path | None) -> list[str]:
    total = video_duration(narrated)
    if bed is None:
        return ["-map", "0:v", "-map", "0:a", "-c:v", "copy", "-af", master_chain(),
                *AUDIO_ENC, "-t", f"{total:.3f}"]
    return ["-filter_complex", mix_graph(total), "-map", "0:v", "-map", "[aout]",
            "-c:v", "copy", *AUDIO_ENC, "-t", f"{total:.3f}"]


def mix_key(narrated: Path, bed: Path | None) -> str:
    """What add_music would stamp on a film mixed now from these inputs (qa.py compares)."""
    return content_key("mix", file_hash(narrated), file_hash(bed) if bed else None, _mix_args(narrated, bed))


def add_music(narrated: Path, bed: Path | None, out: Path) -> Path:
    key = mix_key(narrated, bed)
    if is_fresh(out, key):
        return out
    inputs = ["-i", str(narrated)] + (["-i", str(bed)] if bed else [])
    with stamped_output(out, key) as tmp:
        run(["ffmpeg", "-y", *inputs, *_mix_args(narrated, bed), str(tmp)])
    return out
