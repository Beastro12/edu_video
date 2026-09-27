"""Still images for the slow-drift scenes, via Google's Gemini image model (same key as Veo).
Imagen 4 was retired from the Gemini API (P0-6, D13); images now come from generate_content."""
from pathlib import Path

import config
import ledger
from agents import google_client
from utils import atomic_output, content_key, run

STYLE = (" Calm, dark, low-contrast, cinematic, deep blues and soft warm highlights, "
         "lots of negative space, soft focus edges. No text, no letters, no watermark, "
         "no people's faces.")


def available() -> bool:
    return bool(config.GOOGLE_API_KEY)


def cache_path(scene: dict, out_dir: Path) -> Path:
    prompt = scene["visual_description"] + STYLE
    key = content_key("still", prompt, config.IMAGE_MODEL, config.ASPECT_RATIO, config.IMAGE_SIZE)
    return out_dir / f"still_{key}.png"


def render_still(scene: dict, out_dir: Path) -> Path:
    out = cache_path(scene, out_dir)
    if out.exists():
        return out
    ledger.check("google", ledger.image_eur())
    from google.genai import types

    resp = google_client.make().models.generate_content(
        model=config.IMAGE_MODEL,
        contents=scene["visual_description"] + STYLE,
        config=types.GenerateContentConfig(
            response_modalities=["IMAGE"],
            image_config=types.ImageConfig(aspect_ratio=config.ASPECT_RATIO, image_size=config.IMAGE_SIZE),
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),  # no tools; quiets the SDK
        ),
    )
    # The last finished image: skip text and any interim "thought" images.
    images = [p.inline_data for p in resp.parts or []
              if p.inline_data and p.inline_data.data and not p.thought]
    image = images[-1] if images else None
    if image is None:
        raise RuntimeError("The image model returned no image (often a safety filter on the prompt)")
    ledger.record("google", config.IMAGE_MODEL, {"images": 1}, ledger.image_eur())
    with atomic_output(out) as tmp:
        if image.mime_type == "image/png":
            tmp.write_bytes(image.data)
        else:  # e.g. JPEG: store as PNG so the file matches its name
            src = tmp.with_suffix(".src")
            src.write_bytes(image.data)
            try:
                run(["ffmpeg", "-y", "-v", "error", "-i", str(src), str(tmp)])
            finally:
                src.unlink(missing_ok=True)
    return out
