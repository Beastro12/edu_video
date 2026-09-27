"""Benchmark for scene clips (P1-6, D18): render time, motion smoothness and picture quality of
the still drift, Manim and Veo clips, for different upscale factors, drift filters and x264
settings. Every number in D18 comes from this script. It times the real pipeline steps:
`assembly.build_scene_clip` (the scene clip) and `assembly.crossfade_concat` (the film's
final encode, which re-encodes every clip). Writes into build/bench_stills/ (~4 GB, mostly
lossless references); takes ~50 min on 4 cores.

    .venv/bin/python scripts/bench_stills.py
    .venv/bin/python scripts/bench_stills.py --compare [IMAGE]   # ~2 min: the drifts to watch

--compare renders a pan and a push-in of IMAGE (e.g. a still from build/<slug>/visuals/, or
the test image) with today's zoompan drift and with the sub-pixel `perspective` drift, as
films, to watch full-screen side by side (P1-10). Their sound is a test tone.

Columns (SCENE_S seconds at the film's size; times are the fastest of REPEATS runs):
- clip s / film s / total s: the scene clip, then the film's encode of it
- jitter px, step px, still, jump px, back: motion (see `jitter`)
- detail: variance of the Laplacian of the middle frame (higher = sharper)
- SSIM clip / film: against a lossless render of the same filters, over the frames between
  the film's fades. Only rows that share their reference's geometry have one.
- MB: the scene clip's size"""
import hashlib
import re
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import assembly  # noqa: E402
import config  # noqa: E402

D = Path(config.BUILD_DIR) / "bench_stills"
SCENE_S = 20.0
REPEATS = 3
W, H, FPS = config.WIDTH, config.HEIGHT, config.FPS
LOSSLESS = ("ultrafast", None)  # x264 -qp 0
WAS = ("medium", 18)            # every scene clip before D18
SOURCE = ["-f", "lavfi", "-i", "cellauto=s=1344x768:rule=110:seed=1,format=gray", "-f", "lavfi",
          "-i", "gradients=s=1344x768:c0=0x102040:c1=0x405060:seed=1", "-filter_complex",
          "[0][1]blend=all_mode=overlay,gblur=sigma=1.2", "-frames:v", "1"]  # textured: a hard case
MANIM_SCENE = '''from manim import *
class Orbit(Scene):
    def construct(self):
        self.camera.background_color = "#0f1419"
        sun = Circle(radius=0.6, color=YELLOW, fill_opacity=0.8)
        path = Circle(radius=2.6, color=GREY_B, stroke_opacity=0.5)
        planet = Dot(color=BLUE_C, radius=0.14).move_to(path.point_from_proportion(0))
        label = Text("gravity keeps it falling", font_size=30, color=GREY_A).to_edge(DOWN)
        self.play(FadeIn(sun), Create(path), FadeIn(planet), run_time=2)
        self.play(MoveAlongPath(planet, path), Write(label), run_time=8, rate_func=linear)
        self.wait(2)
'''


def ff(*a):
    subprocess.run(["ffmpeg", "-y", "-v", "error", *a], check=True)


def x264(preset, crf):
    return ["-c:v", "libx264", "-preset", preset, *(["-qp", "0"] if crf is None else ["-crf", str(crf)])]


def cached(name: str, recipe: list[str], make=None) -> Path:
    """A generated input, named by a hash of its recipe so a changed recipe makes a new one."""
    stem, suffix = name.rsplit(".", 1)
    out = D / f"{stem}_{hashlib.sha256(' '.join(recipe).encode()).hexdigest()[:10]}.{suffix}"
    D.mkdir(parents=True, exist_ok=True)
    if not out.exists():
        if make:
            make(out)
        else:
            ff(*recipe, str(out))
    return out


def still():
    return cached("still.png", SOURCE)  # Gemini's 1K 16:9 size


def veo_like():
    """8 s of 1280x720 at 24 fps: a slow move over the still, with film grain, delivered as a
    good-quality H.264 file, like a Veo download."""
    return cached("veo.mp4", ["-loop", "1", "-i", str(still()), "-vf",
                              "scale=2560:1440,zoompan=z='1.2-0.1*on/192':x='iw/2-(iw/zoom/2)':"
                              "y='ih/2-(ih/zoom/2)':d=192:s=1280x720:fps=24,noise=alls=10:allf=t:all_seed=1,format=yuv420p",
                              "-frames:v", "192", *x264("medium", 16)])


def manim_clip():
    """A real Manim render, at the quality and frame rate the pipeline uses."""
    def make(out: Path):
        work = D / "manim_src"
        work.mkdir(exist_ok=True)
        (work / "orbit.py").write_text(MANIM_SCENE)
        subprocess.run([str(Path(sys.executable).parent / "manim"), "render", "-qh", "--fps", str(FPS),
                        "--format", "mp4", "--media_dir", str(work), str(work / "orbit.py"), "Orbit"],
                       check=True, capture_output=True)
        next(work.glob("videos/orbit/*/Orbit.mp4")).rename(out)
    return cached("manim.mp4", [MANIM_SCENE, str(FPS)], make)


def narration(seconds: float) -> Path:
    return cached(f"voice_{seconds:.2f}.mp3", ["-f", "lavfi", "-i", f"sine=f=220:d={seconds}:sample_rate=44100"])


ZOOMPAN = assembly._still_filter  # the pipeline's drift


def zoompan_yuv444(target: float, scene_id: int) -> str:
    """The pipeline's drift with zoompan run in yuv444p: zoompan keeps its crop on the chroma
    grid, so in yuv420p it moves in steps of 2 px of the upscale; in yuv444p, steps of 1 px."""
    vf = ZOOMPAN(target, scene_id)
    assert ",zoompan=" in vf
    return vf.replace(",zoompan=", ",format=yuv444p,zoompan=")


def perspective_linear(target: float, scene_id: int) -> str:
    return perspective_filter(target, scene_id, "linear")


def perspective_filter(target: float, scene_id: int, interpolation: str = "cubic") -> str:
    """The alternative measured in D18: the same drift, positioned to 1/256 px by `perspective`
    on the still at the film's size, instead of zoompan on an upscale."""
    n = max(int(round(target * FPS)), 1)
    z = config.KEN_BURNS_ZOOM
    zoom = {0: f"(1+{z}*on/{n})", 1: f"({1 + z}-{z}*on/{n})", 2: f"{1 + z}"}[scene_id % 3]
    w, h = f"(W/{zoom})", f"(H/{zoom})"
    x = f"((W-{w})*on/{n})" if scene_id % 3 == 2 else f"((W-{w})/2)"
    y = f"((H-{h})/2)"
    return (f"scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos,crop={W}:{H},format=yuv420p,"
            f"loop=loop={n - 1}:size=1:start=0,setpts=N/({FPS}*TB),"
            f"perspective=x0='{x}':y0='{y}':x1='{x}+{w}':y1='{y}':x2='{x}':y2='{y}+{h}':x3='{x}+{w}':y3='{y}+{h}'"
            f":interpolation={interpolation}:eval=frame,setsar=1,format=yuv420p")


def timed(fn, out_dir: Path) -> tuple[float, Path]:
    """Fastest of REPEATS runs of a cached step, each from an empty folder."""
    best = float("inf")
    for _ in range(REPEATS):
        for old in out_dir.glob("*"):
            old.unlink()
        t = time.perf_counter()
        path = fn()
        best = min(best, time.perf_counter() - t)
    return best, path


def build(visual: Path, kind: str, scene_id: int, enc: tuple, name: str, factor: int = 3, drift=None) -> dict:
    """The scene clip and the film made from it, with this clip encoder, upscale and drift filter."""
    voice = narration(SCENE_S - config.LEAD_IN_S - config.TAIL_S)
    saved = config.KEN_BURNS_UPSCALE, assembly.clip_encoder, assembly._still_filter
    config.KEN_BURNS_UPSCALE, assembly.clip_encoder = factor, lambda kind: x264(*enc)
    assembly._still_filter = drift or saved[2]
    try:
        clip_s, clip = timed(lambda: assembly.build_scene_clip(visual, voice, D / name / "clip", kind, scene_id),
                             D / name / "clip")
    finally:
        config.KEN_BURNS_UPSCALE, assembly.clip_encoder, assembly._still_filter = saved
    film_s, film = timed(lambda: assembly.crossfade_concat([clip], D / name / "film" / "film.mp4"), D / name / "film")
    return {"clip": clip, "film": film, "clip_s": clip_s, "film_s": film_s, "mb": clip.stat().st_size / 1e6}


def ssim(path: Path, ref: Path) -> float:
    """Over the frames between the film's fade-in and fade-out, which the lossless clip lacks."""
    a, b = config.FADE_IN_S + 0.5, SCENE_S - config.FADE_OUT_S - 0.5
    cut = f"trim=start={a}:end={b},setpts=PTS-STARTPTS"
    err = subprocess.run(["ffmpeg", "-i", str(path), "-i", str(ref), "-lavfi",
                          f"[0:v]{cut}[a];[1:v]{cut}[b];[a][b]ssim", "-f", "null", "-"],
                         capture_output=True, text=True, check=True).stderr
    return float(re.search(r"All:([0-9.]+)", err).group(1))


def detail(path: Path) -> float:
    """Variance of the Laplacian of the middle frame: higher = sharper."""
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(SCENE_S / 2), "-i", str(path), "-frames:v", "1",
                          "-vf", "format=gray", "-f", "rawvideo", "-"], capture_output=True, check=True).stdout
    f = np.frombuffer(raw, np.uint8).reshape(H, W).astype(np.float64)
    return float((4 * f[1:-1, 1:-1] - f[:-2, 1:-1] - f[2:, 1:-1] - f[1:-1, :-2] - f[1:-1, 2:]).var())


def profiles(path: Path) -> np.ndarray:
    """Per frame, the column means of the middle third of the picture, at full width."""
    band = H // 3
    proc = subprocess.Popen(["ffmpeg", "-v", "error", "-i", str(path), "-vf", f"crop={W}:{band}:0:{band},format=gray",
                             "-f", "rawvideo", "-"], stdout=subprocess.PIPE)
    rows = []
    while chunk := proc.stdout.read(W * band):
        rows.append(np.frombuffer(chunk, np.uint8).reshape(band, W).mean(axis=0, dtype=np.float64))
    proc.wait()
    return np.array(rows)


def shift_x(a: np.ndarray, b: np.ndarray) -> float:
    """How far profile b is moved right of a, in px: the slope of the cross-spectrum's phase
    over the lower frequencies (a shift by s turns phase by -2*pi*f*s), Hann-windowed.
    Interpolation-based estimators (a parabola on the correlation peak; gradient steps with
    linear interpolation) are pulled towards whole pixels on textured pictures, by up to
    0.06 px here; this one is off by < 0.001 px (`check_shift_x`)."""
    w = np.hanning(len(a))
    cross = np.fft.rfft((b - b.mean()) * w) * np.conj(np.fft.rfft((a - a.mean()) * w))
    f = np.fft.rfftfreq(len(a))[1:len(a) // 8]
    c = cross[1:len(a) // 8]
    phase, weight = np.unwrap(np.angle(c)), np.abs(c)
    return float(-np.sum(weight * f * phase) / np.sum(weight * f * f) / (2 * np.pi))


WINDOW = 240     # px: narrow enough that a push-in moves nearly uniformly within it
MAX_STEP = 3.0   # px per frame: the drift moves < 1; an estimate beyond this is a failed one


def jitter(path: Path) -> dict:
    """Motion smoothness in px per frame, at full resolution. A smooth drift moves by the same
    sub-pixel amount every frame; `jitter` is the std of the frame-to-frame change of that step.
    Measured in eight WINDOW-wide strips (in a push-in, content moves at different speeds
    across the picture; a wider strip mixes them, and its estimate then moves with wherever the
    encoder leaves detail) and the median taken over strips. Also: `step`, the mean movement;
    `still`, the share of frames that don't move (< 0.05 px; median over strips); `jump`, the
    mean size of the moves that happen (median); `back`, the share of moves against the drift
    (the worst strip). Estimates past MAX_STEP (the phase unwrap failing, rare) are dropped and
    counted in `failed`."""
    p = profiles(path)
    per_strip, failed = [], 0
    for x in range(0, W - WINDOW + 1, WINDOW):
        s = np.array([shift_x(p[i, x:x + WINDOW], p[i + 1, x:x + WINDOW]) for i in range(len(p) - 1)])
        ok = np.abs(s) <= MAX_STEP
        failed += int((~ok).sum())
        good = s[ok]
        moves = good[np.abs(good) >= 0.05]
        per_strip.append({"jitter": np.std(np.diff(s)[ok[1:] & ok[:-1]]), "step": np.mean(np.abs(good)),
                          "still": 1 - len(moves) / len(good),
                          "jump": np.mean(np.abs(moves)) if len(moves) else 0.0,
                          "back": np.mean(np.sign(moves) == -np.sign(good.sum())) if len(moves) else 0.0})
    out = {k: float(np.median([m[k] for m in per_strip])) for k in ("jitter", "step", "still", "jump")}
    return {**out, "back": float(max(m["back"] for m in per_strip)), "failed": failed}


def check_shift_x():
    """The estimator itself, on known sub-pixel shifts made independently of it: whole-pixel
    shifts at 4x the width, then area-averaged down to W."""
    wide = np.frombuffer(subprocess.run(["ffmpeg", "-v", "error", "-i", str(still()), "-vf",
                                         f"scale={4 * W}:{H},format=gray", "-f", "rawvideo", "-"],
                                        capture_output=True, check=True).stdout, np.uint8).reshape(H, 4 * W)
    row = wide[H // 3: 2 * H // 3].mean(axis=0, dtype=np.float64)
    down = lambda k: row[64 - k: 64 - k + 4 * (W - 32)].reshape(-1, 4).mean(axis=1)  # noqa: E731
    for n, where in ((W - 32, "full width"), (WINDOW, f"{WINDOW} px strip")):
        errors = [abs(shift_x(down(0)[:n], down(k)[:n]) - k / 4) for k in (1, 2, 3, 5, 7)]
        print(f"shift_x error on known shifts of 0.25-1.75 px, {where}: max {max(errors):.4f} px")


HEAD = (f"{'variant':<38}{'motion':<9}{'clip s':>7}{'film s':>7}{'total s':>8}{'jitter px':>10}{'step px':>8}"
        f"{'still':>7}{'jump px':>8}{'back':>6}{'detail':>8}{'SSIM clip':>10}{'SSIM film':>10}{'MB':>7}")


def report(label: str, motion: str, r: dict, ref: Path | None, moves: bool = True) -> None:
    m = jitter(r["clip"]) if moves else None
    motion_cols = (f"{m['jitter']:>10.4f}{m['step']:>8.4f}{m['still']:>7.0%}{m['jump']:>8.3f}{m['back']:>6.0%}" if m
                   else f"{'-':>10}{'-':>8}{'-':>7}{'-':>8}{'-':>6}")
    q = (f"{ssim(r['clip'], ref):>10.5f}{ssim(r['film'], ref):>10.5f}" if ref and ref != r["clip"]
         else f"{'-':>10}{'-':>10}")
    print(f"{label:<38}{motion:<9}{r['clip_s']:>7.2f}{r['film_s']:>7.2f}{r['clip_s'] + r['film_s']:>8.2f}"
          f"{motion_cols}{detail(r['clip']):>8.0f}{q}{r['mb']:>7.1f}"
          + (f"   ({m['failed']} failed step estimates dropped)" if m and m["failed"] else ""),
          flush=True)


def slug(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")


def tags(enc: tuple, now: tuple) -> str:
    return "".join(t for t, hit in ((" (was)", enc == WAS), (" (now)", enc == now)) if hit)


def compare(image: Path) -> None:
    global REPEATS
    REPEATS = 1
    for motion, scene_id in (("pan", 2), ("push-in", 0)):
        for label, drift in (("zoompan", None), ("perspective", perspective_filter)):
            r = build(image, "still", scene_id, config.CLIP_X264, f"compare_{motion}_{label}", drift=drift)
            print(f"{motion:<8} {label:<12} {r['film']}")
    print("(the sound is a test tone: mute it)")


if __name__ == "__main__":
    D.mkdir(parents=True, exist_ok=True)
    if "--compare" in sys.argv:
        args = sys.argv[sys.argv.index("--compare") + 1:]
        compare(Path(args[0]).resolve() if args else still())
        sys.exit()
    check_shift_x()
    now_still = config.CLIP_X264
    variants = [("3x lossless (reference)", 3, LOSSLESS, None), ("3x medium crf 18", 3, WAS, None),
                ("2x medium crf 18", 2, WAS, None), ("1x medium crf 18", 1, WAS, None),
                ("3x veryfast crf 18", 3, ("veryfast", 18), None), ("3x veryfast crf 16", 3, ("veryfast", 16), None),
                (f"3x {now_still[0]} crf {now_still[1]}", 3, now_still, None),
                ("3x zoompan in yuv444p, lossless (reference)", 3, LOSSLESS, zoompan_yuv444),
                (f"3x zoompan in yuv444p, {now_still[0]} crf {now_still[1]}", 3, now_still, zoompan_yuv444),
                ("perspective lossless (reference)", 1, LOSSLESS, perspective_filter),
                (f"perspective {now_still[0]} crf {now_still[1]}", 1, now_still, perspective_filter),
                (f"perspective linear, {now_still[0]} crf {now_still[1]}", 1, now_still, perspective_linear)]
    print(f"\nStill scene: {SCENE_S:.0f} s, {W}x{H} at {FPS} fps; film encode {config.FILM_X264}")
    print(HEAD)
    nows = {}
    for motion, scene_id in (("pan", 2), ("push-in", 0)):
        refs = {}
        for label, factor, enc, drift in variants:
            r = build(still(), "still", scene_id, enc, f"{motion}_{slug(label)}", factor, drift)
            if enc == LOSSLESS:
                refs[drift] = r["clip"]
            zoompan_now = drift is None and factor == config.KEN_BURNS_UPSCALE
            report(label + (tags(enc, now_still) if zoompan_now else ""), motion, r,
                   refs.get(drift) if factor == 3 or drift else None)
            if zoompan_now and enc == now_still:
                nows[motion] = (r["clip"], refs[drift])

    for kind, source, title in (("manim", manim_clip, "Manim clip (a real -qh render)"),
                                ("ai", veo_like, "Veo-like clip (1280x720 24 fps, 8 s, grain), stretched to the scene")):
        now = tuple(assembly.clip_encoder(kind)[3:6:2])
        now = (now[0], int(now[1]))
        print(f"\n{title}")
        print(HEAD)
        ref = None
        for enc in (LOSSLESS, WAS, config.CLIP_X264):
            label = "lossless (reference)" if enc == LOSSLESS else f"{enc[0]} crf {enc[1]}{tags(enc, now)}"
            r = build(source(), kind, 1, enc, f"{kind}_{slug(label)}")
            ref = ref or r["clip"]
            report(label, kind, r, ref, moves=False)
            if enc == now:
                nows[kind] = (r["clip"], ref)

    print("\nFilm encoder options, on each kind's current clip (for a later task; D18 keeps FILM_X264)")
    print(f"{'film encoder':<22}{'kind':<9}{'film s':>7}{'SSIM film':>10}{'MB/min':>8}")
    for kind, (clip, ref) in nows.items():
        for enc in (config.FILM_X264, ("fast", 17), ("faster", 17), ("veryfast", 16), ("veryfast", 15)):
            saved, assembly.VIDEO_ENC = assembly.VIDEO_ENC, x264(*enc)
            out = D / "film_enc" / f"{kind}_{slug(str(enc))}"
            try:
                t, film = timed(lambda c=clip, o=out: assembly.crossfade_concat([c], o / "film.mp4"), out)
            finally:
                assembly.VIDEO_ENC = saved
            mb = film.stat().st_size / 1e6 * 60 / SCENE_S
            print(f"{enc[0] + ' crf ' + str(enc[1]):<22}{kind:<9}{t:>7.2f}{ssim(film, ref):>10.5f}{mb:>8.1f}", flush=True)
