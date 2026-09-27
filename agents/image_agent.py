"""Still images for the slow-drift scenes, via Google Imagen (same key as Veo)."""
from pathlib import Path

import config

STYLE = (" Calm, dark, low-contrast, cinematic, deep blues and soft warm highlights, "
         "lots of negative space, soft focus edges. No text, no letters, no watermark, "
         "no people's faces.")


def available() -> bool:
    return bool(config.GOOGLE_API_KEY)


def render_still(scene: dict, work_dir: Path) -> Path:
    out = work_dir / f"scene_{scene['id']:02d}_still.png"
    if out.exists():
        return out
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=config.GOOGLE_API_KEY)
    resp = client.models.generate_images(
        model=config.IMAGE_MODEL,
        prompt=scene["visual_description"] + STYLE,
        config=types.GenerateImagesConfig(number_of_images=1, aspect_ratio="16:9"),
    )
    if not resp.generated_images:
        raise RuntimeError("Imagen returned no image (often a safety filter on the prompt)")
    out.write_bytes(resp.generated_images[0].image.image_bytes)
    return out
