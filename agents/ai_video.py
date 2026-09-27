"""Agent 4b: atmospheric clips via Google Veo. Any failure falls back to Manim upstream."""
import time
from pathlib import Path

import config
import ledger
from utils import atomic_output, content_key

STYLE = (" Calm documentary footage, one continuous slow camera move, soft natural "
         "light, muted colours, shallow depth of field. No text, no captions, no logos, "
         "no people's faces.")


def available() -> bool:
    return bool(config.GOOGLE_API_KEY)


def cache_path(scene: dict, out_dir: Path) -> Path:
    prompt = scene["visual_description"] + STYLE
    return out_dir / f"ai_{content_key('ai', prompt, config.VEO_MODEL, config.ASPECT_RATIO)}.mp4"


def render_scene(scene: dict, out_dir: Path, timeout_s: int = 600) -> Path:
    out = cache_path(scene, out_dir)
    if out.exists():
        return out
    cost = ledger.video_eur(config.VEO_CLIP_S)
    ledger.check("google", cost)
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=config.GOOGLE_API_KEY)
    op = client.models.generate_videos(
        model=config.VEO_MODEL,
        prompt=scene["visual_description"] + STYLE,
        config=types.GenerateVideosConfig(aspect_ratio=config.ASPECT_RATIO),
    )
    start = time.time()
    while not op.done:
        if time.time() - start > timeout_s:
            # The job keeps running on Google's side and is likely billed: count it (D10).
            ledger.record("google", config.VEO_MODEL,
                          {"seconds": config.VEO_CLIP_S, "note": "timed out; assumed billed"}, cost)
            raise RuntimeError("Veo timed out")
        time.sleep(10)
        op = client.operations.get(op)

    if getattr(op, "error", None):
        raise RuntimeError(f"Veo error: {op.error}")
    videos = getattr(op.response, "generated_videos", None) if op.response else None
    if not videos:
        raise RuntimeError("Veo returned no video (often a safety filter on the prompt)")
    ledger.record("google", config.VEO_MODEL, {"seconds": config.VEO_CLIP_S}, cost)
    client.files.download(file=videos[0].video)
    with atomic_output(out) as tmp:
        videos[0].video.save(str(tmp))
    return out
