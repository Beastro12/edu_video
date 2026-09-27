"""Checks a generated still against its scene before it goes into the film (Claude vision)."""
from pathlib import Path

import config
from agents.llm import structured
from utils import preview_jpeg

SCHEMA = {
    "type": "object",
    "properties": {
        "ok": {"type": "boolean"},
        "problems": {"type": "array", "items": {"type": "string"},
                     "description": "Each problem, specific enough to fix in a new image prompt"},
    },
    "required": ["ok", "problems"],
}

SYSTEM = """You check AI-generated still images for a calm science documentary that people
watch while falling asleep. Each image stays on screen with a slow drift behind narration.

Reject the image (ok=false) if it has any of these:
- text, letters, numbers, symbols or a watermark anywhere;
- a depiction that is physically or scientifically wrong for the scene's concept (wrong
  shapes, impossible physics, a misleading picture of the mechanism);
- anything unsettling for sleep viewing: faces or eyes, people, creatures, bright flashes,
  harsh contrast, alarming or violent imagery.

Otherwise ok=true and problems=[]. Judge only what is visible; artistic simplification is fine
when it doesn't teach something wrong. Name each problem plainly so it can be avoided."""


PROMPT = """Scene concept: {concept}
What the image was meant to show: {visual_description}

Does this image pass?"""


def review(scene: dict, still: Path) -> dict:
    verdict = structured(SYSTEM, PROMPT.format(**scene), "verdict", SCHEMA,
                         max_tokens=config.STILL_REVIEW_MAX_TOKENS,
                         images=[preview_jpeg(still, config.PREVIEW_PX)])
    if not isinstance(verdict.get("ok"), bool) or not isinstance(verdict.get("problems"), list):
        raise RuntimeError(f"unusable verdict from the still review: {verdict}")
    return verdict
