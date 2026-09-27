"""Still images for the slow-drift scenes, via Google Imagen (same key as Veo)."""
from pathlib import Path

import config
from utils import atomic_output, content_key

STYLE = (" Calm, dark, low-contrast, cinematic, deep blues and soft warm highlights, "
         "lots of negative space, soft focus edges. No text, no letters, no watermark, "
         "no people's faces.")


def available() -> bool:
    return bool(config.GOOGLE_API_KEY)


def render_still(scene: dict, out_dir: Path) -> Path:
    prompt = scene["visual_description"] + STYLE
    out = out_dir / f"still_{content_key('still', prompt, config.IMAGE_MODEL, config.ASPECT_RATIO)}.png"
    if out.exists():
        return out
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=config.GOOGLE_API_KEY)
    resp = client.models.generate_images(
        model=config.IMAGE_MODEL,
        prompt=prompt,
        config=types.GenerateImagesConfig(number_of_images=1, aspect_ratio=config.ASPECT_RATIO),
    )
    if not resp.generated_images:
        raise RuntimeError("Imagen returned no image (often a safety filter on the prompt)")
    with atomic_output(out) as tmp:
        tmp.write_bytes(resp.generated_images[0].image.image_bytes)
    return out
