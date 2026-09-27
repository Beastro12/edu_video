"""Agent 4b: atmospheric clips via Google Veo. Any failure falls back to Manim upstream."""
import time
from pathlib import Path

import config

STYLE = (" Calm documentary footage, one continuous slow camera move, soft natural "
         "light, muted colours, shallow depth of field. No text, no captions, no logos, "
         "no people's faces.")


def available() -> bool:
    return bool(config.GOOGLE_API_KEY)


def render_scene(scene: dict, work_dir: Path, timeout_s: int = 600) -> Path:
    out = work_dir / f"scene_{scene['id']:02d}_ai.mp4"
    if out.exists():
        return out
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=config.GOOGLE_API_KEY)
    op = client.models.generate_videos(
        model=config.VEO_MODEL,
        prompt=scene["visual_description"] + STYLE,
        config=types.GenerateVideosConfig(aspect_ratio="16:9"),
    )
    start = time.time()
    while not op.done:
        if time.time() - start > timeout_s:
            raise RuntimeError("Veo timed out")
        time.sleep(10)
        op = client.operations.get(op)

    if getattr(op, "error", None):
        raise RuntimeError(f"Veo error: {op.error}")
    videos = getattr(op.response, "generated_videos", None) if op.response else None
    if not videos:
        raise RuntimeError("Veo returned no video (often a safety filter on the prompt)")
    client.files.download(file=videos[0].video)
    videos[0].video.save(str(out))
    return out
