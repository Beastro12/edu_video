"""Still images for the slow-drift scenes, via Google Imagen (same key as Veo)."""
from pathlib import Path

import config
import ledger
from agents import google_client
from utils import atomic_output, content_key

STYLE = (" Calm, dark, low-contrast, cinematic, deep blues and soft warm highlights, "
         "lots of negative space, soft focus edges. No text, no letters, no watermark, "
         "no people's faces.")


def available() -> bool:
    return bool(config.GOOGLE_API_KEY)


def cache_path(scene: dict, out_dir: Path) -> Path:
    prompt = scene["visual_description"] + STYLE
    return out_dir / f"still_{content_key('still', prompt, config.IMAGE_MODEL, config.ASPECT_RATIO)}.png"


def render_still(scene: dict, out_dir: Path) -> Path:
    out = cache_path(scene, out_dir)
    if out.exists():
        return out
    ledger.check("google", ledger.image_eur())
    from google.genai import types

    client = google_client.make()
    resp = client.models.generate_images(
        model=config.IMAGE_MODEL,
        prompt=scene["visual_description"] + STYLE,
        config=types.GenerateImagesConfig(number_of_images=1, aspect_ratio=config.ASPECT_RATIO),
    )
    if not resp.generated_images:
        raise RuntimeError("Imagen returned no image (often a safety filter on the prompt)")
    ledger.record("google", config.IMAGE_MODEL, {"images": 1}, ledger.image_eur())
    with atomic_output(out) as tmp:
        tmp.write_bytes(resp.generated_images[0].image.image_bytes)
    return out
