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
    file_hash,
    is_fresh,
    run,
    stamped_output,
    video_duration,
)

W, H, FPS = config.WIDTH, config.HEIGHT, config.FPS
RATE = config.AUDIO_RATE
BG = "0x0f1419"
VIDEO_ENC = ["-c:v", "libx264", "-preset", config.FILM_X264[0], "-crf", str(config.FILM_X264[1])]
AUDIO_ENC = ["-c:a", "aac", "-b:a", "192k", "-ar", str(RATE)]
# loudnorm's output timestamps skip at the end of its input; FFmpeg then drops samples
# under a duration limit. Re-stamping from the sample count after it fixes that (D12).
RESTAMP = "asetpts=N/SR/TB"


def scene_frames(narration_s: float) -> int:
    """Lead-in + narration + tail, rounded up to whole video frames."""
    return math.ceil(round((config.LEAD_IN_S + narration_s + config.TAIL_S) * FPS, 6))


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


def _moving_filter(target: float, v_len: float, is_ai: bool) -> str:
    slow = min(target / v_len, config.MAX_AI_SLOWDOWN) if (is_ai and v_len < target) else 1.0
    return (f"setpts={slow:.4f}*PTS,"
            f"scale={W}:{H}:force_original_aspect_ratio=decrease,"
            f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color={BG},setsar=1,fps={FPS},"
            f"tpad=stop_mode=clone:stop_duration={target:.2f},format=yuv420p")


def clip_encoder(kind: str) -> list[str]:
    """Scene clips are intermediates that the film re-encodes (D18). Stills and Manim get the
    fast CLIP_X264. Veo footage keeps FILM_X264: its grain loses detail through two fast
    encodes, and there are at most VEO_MAX_PER_DAY such clips, so their speed doesn't matter."""
    preset, crf = config.FILM_X264 if kind == "ai" else config.CLIP_X264
    return ["-c:v", "libx264", "-preset", preset, "-crf", str(crf)]


def build_scene_clip(visual: Path, narration: Path, out_dir: Path, kind: str, scene_id: int) -> Path:
    """Scene length is set by the voice: lead-in + narration + tail, in whole frames.
    kind: 'still' | 'manim' | 'ai'. The clip is named by a hash of its input files and
    the exact filters, so a changed narration or visual can never reuse an old clip."""
    n = scene_frames(duration(narration))
    target, samples = n / FPS, n * RATE // FPS
    vf = (_still_filter(target, scene_id) if kind == "still"
          else _moving_filter(target, duration(visual), kind == "ai"))
    # Level the voice: the ducking threshold is absolute, so it only behaves predictably if
    # every scene's narration arrives at the same loudness (D2). The lead-in is added before
    # loudnorm, and the result is re-stamped and cut to exactly `samples` (D12).
    af = (f"aresample={RATE},aformat=channel_layouts=stereo,"
          f"adelay=delays={int(config.LEAD_IN_S * 1000)}:all=1,"
          f"loudnorm=I={config.VOICE_LUFS}:TP=-2:LRA=7,aresample={RATE},{RESTAMP},"
          f"apad=whole_len={samples},atrim=end_sample={samples}")
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
            "[v][duck]amix=inputs=2:duration=first:normalize=0,"
            f"afade=t=out:st={max(total - config.MUSIC_FADE_S, 0):.2f}:d={config.MUSIC_FADE_S}"
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
