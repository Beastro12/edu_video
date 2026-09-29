"""Agent 4b: atmospheric clips via Google Veo. Any failure falls back to Manim upstream."""
import threading
import time
from pathlib import Path

import config
import ledger
import retries
from agents import google_client
from failures import Failures
from utils import atomic_output, content_key, output_lock

STYLE = (" Calm documentary footage, one continuous slow camera move, soft natural "
         "light, muted colours, shallow depth of field. No text, no captions, no logos, "
         "no people's faces.")


def available() -> bool:
    return bool(config.GOOGLE_API_KEY)


def cache_path(scene: dict, out_dir: Path) -> Path:
    prompt = scene["visual_description"] + STYLE
    return out_dir / f"ai_{content_key('ai', prompt, config.VEO_MODEL, config.ASPECT_RATIO, config.VEO_RESOLUTION)}.mp4"


_veo_lock = threading.Lock()


class DailyCapReached(RuntimeError):
    """Today's Veo clips are used up. The scene falls back to a still, as after a failed
    generation, but nothing is recorded as failed: tomorrow it can have its clip (P1-7)."""


def render_scene(scene: dict, out_dir: Path, timeout_s: int = 600, failures: Failures | None = None) -> Path:
    """A failed generation is recorded in `failures` (P1-7) and not attempted again unless
    it retries failures."""
    out = cache_path(scene, out_dir)
    with output_lock(out):
        if out.exists():
            return out
        if failures:
            failures.check(out.stem)
        try:
            made = _start(scene, out, timeout_s)
        except (ledger.BudgetExceeded, DailyCapReached):
            raise  # not a failed generation: nothing was asked of Veo
        except Exception as e:
            if failures:
                failures.record(out.stem, "veo", scene.get("id"), e)
            raise
        if failures:
            failures.clear(out.stem)
        return made


def _start(scene: dict, out: Path, timeout_s: int) -> Path:
    # One Veo job at a time: the daily cap is counted from the ledger, and two scenes checking
    # it at once could both see room for one more clip.
    with _veo_lock:
        if ledger.veo_clips_today() >= config.VEO_MAX_PER_DAY:
            raise DailyCapReached(f"{config.VEO_MAX_PER_DAY} Veo clips already made today (VEO_MAX_PER_DAY)")
        cost = ledger.video_eur(config.VEO_CLIP_S)
        with ledger.reserve("google", cost):
            return _buy(scene, out, cost, timeout_s)


def _buy(scene: dict, out: Path, cost: float, timeout_s: int) -> Path:
    import httpx
    from google.genai import errors, types

    client = google_client.make()
    try:
        op = client.models.generate_videos(
            model=config.VEO_MODEL,
            source=types.GenerateVideosSource(prompt=scene["visual_description"] + STYLE),
            # No generate_audio: the Gemini API refuses it (the SDK raises); the audio is discarded.
            config=types.GenerateVideosConfig(aspect_ratio=config.ASPECT_RATIO, resolution=config.VEO_RESOLUTION,
                                              http_options=google_client.start_job_options()),
        )
    except (errors.ServerError, httpx.TimeoutException) as e:
        # Google may have accepted the job before failing to answer: count it (D10, D11),
        # so neither the budget nor the daily cap can be walked around this way.
        ledger.record("google", config.VEO_MODEL,
                      {"seconds": config.VEO_CLIP_S, "note": f"start failed ({type(e).__name__}); may have started"},
                      cost)
        raise
    # From here the job is running on Google's side and is likely billed even if we lose it.
    start = time.time()
    try:
        while not op.done:
            if time.time() - start > timeout_s:
                raise RuntimeError("Veo timed out")
            if ledger.interrupted.is_set():  # only Ctrl-C abandons a paid job; a budget stop lets it finish
                raise RuntimeError("interrupted")
            time.sleep(10)
            op = client.operations.get(op)
    except Exception as e:
        ledger.record("google", config.VEO_MODEL,
                      {"seconds": config.VEO_CLIP_S, "note": f"lost track of job ({type(e).__name__}); assumed billed"},
                      cost)
        raise

    if getattr(op, "error", None):
        raise RuntimeError(f"Veo error: {op.error}")
    videos = getattr(op.response, "generated_videos", None) if op.response else None
    if not videos:
        raise RuntimeError("Veo returned no video (often a safety filter on the prompt)")
    ledger.record("google", config.VEO_MODEL, {"seconds": config.VEO_CLIP_S}, cost)
    # The SDK's own retry loop doesn't cover downloads; the clip is already paid for.
    retries.call(lambda: client.files.download(file=videos[0].video), "Veo download")
    with atomic_output(out) as tmp:
        videos[0].video.save(str(tmp))
    return out
