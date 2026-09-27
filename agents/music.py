"""Music: builds one continuous bed from the tracks in music/, crossfading between
tracks (or between repeats of a single track) so a 20-minute video has no loop seam.
Use royalty-free tracks you have the rights to."""
import random
from pathlib import Path

import config
from utils import content_key, duration, file_hash, is_fresh, run, stamped_output

EXTS = {".mp3", ".wav", ".m4a", ".flac", ".ogg"}


def list_tracks() -> list[Path]:
    return sorted(p for p in Path(config.MUSIC_DIR).glob("*") if p.suffix.lower() in EXTS)


def build_bed(tracks: list[Path], total_s: float, out: Path, seed: str) -> Path:
    xf = config.MUSIC_XFADE_S
    usable = [(t, duration(t)) for t in tracks]
    usable = [(t, d) for t, d in usable if d > 3 * xf]
    if not usable:
        raise RuntimeError("No music track is long enough to crossfade")

    rng = random.Random(seed)
    order, length = [], 0.0
    while length < total_s + 2 * xf:
        batch = usable[:]
        rng.shuffle(batch)
        for t, d in batch:
            length += d if not order else d - xf
            order.append(t)
            if length >= total_s + 2 * xf:
                break

    norm = "".join(f"[{i}:a]aresample=48000,aformat=channel_layouts=stereo[m{i}];"
                   for i in range(len(order)))
    if len(order) == 1:
        chain, last = "", "m0"
    else:
        steps, last = [], "m0"
        for i in range(1, len(order)):
            steps.append(f"[{last}][m{i}]acrossfade=d={xf}:c1=qsin:c2=qsin[x{i}]")
            last = f"x{i}"
        chain = ";".join(steps) + ";"
    args = ["-filter_complex", f"{norm}{chain}[{last}]anull[out]",
            "-map", "[out]", "-t", f"{total_s:.2f}", "-c:a", "pcm_s16le"]
    key = content_key("bed", [file_hash(t) for t in order], args)
    if is_fresh(out, key):
        return out
    inputs = sum((["-i", str(t)] for t in order), [])
    with stamped_output(out, key) as tmp:
        run(["ffmpeg", "-y", *inputs, *args, str(tmp)])
    return out


def placeholder_pad(out: Path, seconds: float = 180) -> Path:
    """Soft synthetic chord for testing the pipeline. Not meant for publishing."""
    chord = "+".join(f"0.18*sin(2*PI*{f}*t)" for f in (110, 164.81, 220, 277.18))
    args = ["-f", "lavfi", "-i", f"aevalsrc='{chord}':s=48000:d={seconds}",
            "-af", "tremolo=f=0.15:d=0.4,aecho=0.8:0.7:600:0.3,lowpass=f=1200", "-ac", "2"]
    key = content_key("pad", args)
    if is_fresh(out, key):
        return out
    with stamped_output(out, key) as tmp:
        run(["ffmpeg", "-y", *args, str(tmp)])
    return out
