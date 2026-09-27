"""FFmpeg assembly: fit each visual to its narration, crossfade scenes, duck music under the voice."""
from pathlib import Path

import config
from utils import atomic_output, content_key, duration, file_hash, is_fresh, run, stamped_output

W, H, FPS = config.WIDTH, config.HEIGHT, config.FPS
BG = "0x0f1419"
VIDEO_ENC = ["-c:v", "libx264", "-preset", "medium", "-crf", "18"]
AUDIO_ENC = ["-c:a", "aac", "-b:a", "192k", "-ar", "48000"]


def _still_filter(target: float, scene_id: int) -> str:
    """Slow Ken Burns drift. Upscaling 3x first keeps zoompan's integer
    positioning from producing visible jitter at this slow speed."""
    n = max(int(round(target * FPS)), 1)
    z = config.KEN_BURNS_ZOOM
    centre_x, centre_y = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    motion = scene_id % 3
    if motion == 0:    # slow push in
        zexpr, x, y = f"1+{z}*on/{n}", centre_x, centre_y
    elif motion == 1:  # slow pull out
        zexpr, x, y = f"{1 + z}-{z}*on/{n}", centre_x, centre_y
    else:              # slow pan left to right
        zexpr, x, y = f"{1 + z}", f"(iw-iw/zoom)*on/{n}", centre_y
    return (f"scale={W * 3}:{H * 3}:force_original_aspect_ratio=increase,"
            f"crop={W * 3}:{H * 3},"
            f"zoompan=z='{zexpr}':x='{x}':y='{y}':d={n}:s={W}x{H}:fps={FPS},"
            f"setsar=1,format=yuv420p")


def _moving_filter(target: float, v_len: float, is_ai: bool) -> str:
    slow = min(target / v_len, config.MAX_AI_SLOWDOWN) if (is_ai and v_len < target) else 1.0
    return (f"setpts={slow:.4f}*PTS,"
            f"scale={W}:{H}:force_original_aspect_ratio=decrease,"
            f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color={BG},setsar=1,fps={FPS},"
            f"tpad=stop_mode=clone:stop_duration={target:.2f},format=yuv420p")


def build_scene_clip(visual: Path, narration: Path, out_dir: Path, kind: str, scene_id: int) -> Path:
    """Scene length is set by the voice: lead-in + narration + tail.
    kind: 'still' | 'manim' | 'ai'. The clip is named by a hash of its input files and
    the exact filters, so a changed narration or visual can never reuse an old clip."""
    target = config.LEAD_IN_S + duration(narration) + config.TAIL_S
    vf = (_still_filter(target, scene_id) if kind == "still"
          else _moving_filter(target, duration(visual), kind == "ai"))
    # Level the voice first: the ducking threshold is absolute, so it only behaves
    # predictably if every scene's narration arrives at the same loudness.
    af = (f"loudnorm=I={config.VOICE_LUFS}:TP=-2:LRA=7,"
          f"aresample=48000,aformat=channel_layouts=stereo,"
          f"adelay=delays={int(config.LEAD_IN_S * 1000)}:all=1,"
          f"apad=whole_dur={target:.2f}")
    args = ["-filter_complex", f"[0:v]{vf}[v];[1:a]{af}[a]",
            "-map", "[v]", "-map", "[a]", "-t", f"{target:.2f}",
            *VIDEO_ENC, "-r", str(FPS), *AUDIO_ENC]
    out = out_dir / f"{content_key('clip', file_hash(visual), file_hash(narration), args)}.mp4"
    if out.exists():
        return out
    with atomic_output(out) as tmp:
        run(["ffmpeg", "-y", "-i", str(visual), "-i", str(narration), *args, str(tmp)])
    return out


def crossfade_concat(clips: list[Path], out: Path) -> Path:
    """Chain xfade/acrossfade. The overlap sits in each scene's silent tail, so
    narration never overlaps. Fades in from and out to black at the very ends.
    Rebuilt whenever any clip changes."""
    xf = config.XFADE_S
    durs = [duration(c) for c in clips]
    total = sum(durs) - xf * (len(clips) - 1)

    if len(clips) == 1:
        graph, v, a = "", "0:v", "0:a"
    else:
        parts, v, a, offset = [], "0:v", "0:a", 0.0
        for i in range(1, len(clips)):
            offset += durs[i - 1] - xf
            parts.append(f"[{v}][{i}:v]xfade=transition=fade:duration={xf}:offset={offset:.3f}[v{i}]")
            parts.append(f"[{a}][{i}:a]acrossfade=d={xf}[a{i}]")
            v, a = f"v{i}", f"a{i}"
        graph = ";".join(parts) + ";"
    graph += f"[{v}]fade=t=in:d=1.5,fade=t=out:st={total - 2.5:.2f}:d=2.5[vout]"
    args = ["-filter_complex", graph,
            "-map", "[vout]", "-map", f"[{a}]" if len(clips) > 1 else a,
            *VIDEO_ENC, "-r", str(FPS), *AUDIO_ENC]
    key = content_key("xfade", [file_hash(c) for c in clips], args)
    if is_fresh(out, key):
        return out
    inputs = sum((["-i", str(c)] for c in clips), [])
    with stamped_output(out, key) as tmp:
        run(["ffmpeg", "-y", *inputs, *args, str(tmp)])
    return out


def add_music(narrated: Path, bed: Path | None, out: Path) -> Path:
    total = duration(narrated)
    if bed is None:
        args = ["-c:v", "copy", "-af", f"loudnorm=I={config.TARGET_LUFS}:TP=-1.5:LRA=11",
                "-c:a", "aac", "-b:a", "192k"]
    else:
        fc = (
            "[0:a]asplit=2[voice][key];"
            f"[1:a]aresample=48000,aformat=channel_layouts=stereo,"
            f"volume={config.MUSIC_VOLUME},afade=t=in:d={config.MUSIC_FADE_S}[mus];"
            # sidechain: the voice pushes the music down while it speaks
            f"[mus][key]sidechaincompress=threshold=0.03:ratio={config.DUCK_RATIO}"
            ":attack=80:release=900[duck];"
            "[voice][duck]amix=inputs=2:duration=first:normalize=0,"
            f"afade=t=out:st={max(total - config.MUSIC_FADE_S, 0):.2f}:d={config.MUSIC_FADE_S},"
            f"loudnorm=I={config.TARGET_LUFS}:TP=-1.5:LRA=11[aout]"
        )
        args = ["-filter_complex", fc, "-map", "0:v", "-map", "[aout]",
                "-c:v", "copy", *AUDIO_ENC, "-t", f"{total:.2f}"]
    key = content_key("mix", file_hash(narrated), file_hash(bed) if bed else None, args)
    if is_fresh(out, key):
        return out
    inputs = ["-i", str(narrated)] + (["-i", str(bed)] if bed else [])
    with stamped_output(out, key) as tmp:
        run(["ffmpeg", "-y", *inputs, *args, str(tmp)])
    return out
