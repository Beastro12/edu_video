"""FFmpeg assembly: fit each visual to its narration, crossfade scenes, duck music under the voice."""
from pathlib import Path

import config
from utils import duration, run

W, H, FPS = config.WIDTH, config.HEIGHT, config.FPS
BG = "0x0f1419"


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


def build_scene_clip(visual: Path, narration: Path, out: Path, kind: str, scene_id: int) -> Path:
    """Scene length is set by the voice: lead-in + narration + tail.
    kind: 'still' | 'manim' | 'ai'."""
    if out.exists():
        return out
    target = config.LEAD_IN_S + duration(narration) + config.TAIL_S
    vf = (_still_filter(target, scene_id) if kind == "still"
          else _moving_filter(target, duration(visual), kind == "ai"))
    # Level the voice first: the ducking threshold is absolute, so it only behaves
    # predictably if every scene's narration arrives at the same loudness.
    af = (f"loudnorm=I={config.VOICE_LUFS}:TP=-2:LRA=7,"
          f"aresample=48000,aformat=channel_layouts=stereo,"
          f"adelay=delays={int(config.LEAD_IN_S * 1000)}:all=1,"
          f"apad=whole_dur={target:.2f}")
    run(["ffmpeg", "-y", "-i", str(visual), "-i", str(narration),
         "-filter_complex", f"[0:v]{vf}[v];[1:a]{af}[a]",
         "-map", "[v]", "-map", "[a]", "-t", f"{target:.2f}",
         "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-r", str(FPS),
         "-c:a", "aac", "-b:a", "192k", "-ar", "48000", str(out)])
    return out


def crossfade_concat(clips: list[Path], out: Path) -> Path:
    """Chain xfade/acrossfade. The overlap sits in each scene's silent tail, so
    narration never overlaps. Fades in from and out to black at the very ends."""
    if out.exists():
        return out
    xf = config.XFADE_S
    durs = [duration(c) for c in clips]
    total = sum(durs) - xf * (len(clips) - 1)
    inputs = sum((["-i", str(c)] for c in clips), [])

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
    run(["ffmpeg", "-y", *inputs, "-filter_complex", graph,
         "-map", "[vout]", "-map", f"[{a}]" if len(clips) > 1 else a,
         "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-r", str(FPS),
         "-c:a", "aac", "-b:a", "192k", "-ar", "48000", str(out)])
    return out


def add_music(narrated: Path, bed: Path | None, out: Path) -> Path:
    total = duration(narrated)
    if bed is None:
        run(["ffmpeg", "-y", "-i", str(narrated), "-c:v", "copy",
             "-af", f"loudnorm=I={config.TARGET_LUFS}:TP=-1.5:LRA=11",
             "-c:a", "aac", "-b:a", "192k", str(out)])
        return out
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
    run(["ffmpeg", "-y", "-i", str(narrated), "-i", str(bed),
         "-filter_complex", fc, "-map", "0:v", "-map", "[aout]",
         "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
         "-t", f"{total:.2f}", str(out)])
    return out
