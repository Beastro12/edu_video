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


def style() -> str:
    """The active preset's suffix, read per call: main sets config.STYLE after this module loads."""
    return config.STYLES[config.STYLE]["ai_video"]


def available() -> bool:
    return bool(config.GOOGLE_API_KEY)


def cache_path(scene: dict, out_dir: Path) -> Path:
    prompt = scene["visual_description"] + style()
    return out_dir / f"ai_{content_key('ai', prompt, config.VEO_MODEL, config.ASPECT_RATIO, config.VEO_RESOLUTION)}.mp4"


_veo_lock = threading.Lock()


class DailyCapReached(RuntimeError):
    """Today's Veo clips are used up. The scene falls back to a still, as after a failed
    generation, but nothing is recorded as failed: tomorrow it can have its clip (P1-7)."""


class StartAbandoned(RuntimeError):
    """The run is stopping (Ctrl-C, or an earlier scene failed) while the start waited out a 429. Google
    refused, so no job exists, and nothing was sent after it. Like DailyCapReached, nothing about the prompt
    failed: not recorded as a failure (D19), and the scene falls back as after a failed generation."""


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
        except (ledger.BudgetExceeded, DailyCapReached, StartAbandoned):
            raise  # not a failed generation: nothing was asked of Veo (or Google refused and we stopped)
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


def _provably_not_started() -> tuple[type[BaseException], ...]:
    """The errors that prove a start POST made no job:
    - errors.ClientError: a 4xx, Google refused (a 429 is retried; see _buy);
    - httpx ConnectError, ProxyError and PoolTimeout (and httpx2's twins): raised before any byte of the
      request is written (httpcore 1.0.9, _backends/sync.py:150-153 and :202-205, _sync/http_proxy.py:297 and
      :315-316).
    ConnectTimeout is deliberately NOT here, although it is in fact pre-send as well: a false "may have
    started" record costs budget headroom and one daily clip, never money, while a missed one can cost
    a clip nobody counted (orchestrator decision, P0-9)."""
    import httpx
    from google.genai import errors

    kinds: list[type[BaseException]] = [errors.ClientError, httpx.ConnectError, httpx.ProxyError, httpx.PoolTimeout]
    try:  # the SDK accepts either library's client
        import httpx2
        kinds += [httpx2.ConnectError, httpx2.ProxyError, httpx2.PoolTimeout]
    except ImportError:
        pass
    return tuple(kinds)


def _buy(scene: dict, out: Path, cost: float, timeout_s: int) -> Path:
    from google.genai import errors, types

    client = google_client.make()
    never_started = _provably_not_started()
    posts, unknown = 0, False  # `unknown`: a start POST is in flight, or failed in a way that proves nothing

    def post():
        nonlocal posts, unknown
        if posts and (ledger.interrupted.is_set() or ledger.stopping.is_set()):
            # Checked after the backoff, before the POST: a Ctrl-C then must not start a new (paid) job.
            raise StartAbandoned("the run is stopping (Ctrl-C or an earlier failure); no new job was started")
        # Built before `unknown` is set: an error in our own arguments sent nothing, so it is not a possible job.
        source = types.GenerateVideosSource(prompt=scene["visual_description"] + style())
        # No generate_audio: the Gemini API refuses it (the SDK raises); the audio is discarded.
        gen_config = types.GenerateVideosConfig(aspect_ratio=config.ASPECT_RATIO, resolution=config.VEO_RESOLUTION,
                                                http_options=google_client.start_job_options())
        posts += 1
        unknown = True
        try:
            op = client.models.generate_videos(model=config.VEO_MODEL, source=source, config=gen_config)
        except never_started as e:
            unknown = False
            if isinstance(e, errors.ClientError) and e.code == 429:  # retries.call waits Retry-After if given
                e.retry_after_s = retries.retry_after_s(getattr(getattr(e, "response", None), "headers", None) or {})
            raise
        return op  # `unknown` stays set until the start's handler below is left: a press before then counts

    def refused(e: BaseException) -> bool:
        return isinstance(e, errors.ClientError) and e.code == 429

    try:
        # The SDK makes one attempt (google_client.start_job_options); the only retry is ours, and only for a
        # 429: Google refused the job, so asking again cannot bill twice. A timeout or a 5xx may have come after
        # Google accepted it (D11).
        op = retries.call(post, "Veo start", transient=refused)
        start = time.time()  # inside this try: a press anywhere from the POST to here still counts the job
    except BaseException as e:  # BaseException: a Ctrl-C while the POST is in flight may also have started the job
        if unknown:
            # Google may have accepted the job before the answer failed: count it (D10, D11), so neither
            # the budget nor the daily cap can be walked around this way.
            ledger.record("google", config.VEO_MODEL,
                          {"seconds": config.VEO_CLIP_S, "note": f"start failed ({type(e).__name__}); may have started"},
                          cost)
        raise
    # From here the job is running on Google's side and is likely billed even if we lose it.
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
