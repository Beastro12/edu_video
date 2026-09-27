"""Critic: checks each chapter for scientific errors and sleep-suitability before anything costs money."""
import json

from agents.llm import structured
from agents.script_agent import CHAPTER_SCHEMA

REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "approved": {"type": "boolean"},
        "issues": {"type": "array", "items": {"type": "string"}},
        "revised_scenes": CHAPTER_SCHEMA["properties"]["scenes"],
    },
    "required": ["approved", "issues", "revised_scenes"],
}

SYSTEM = """You are a strict science editor for calm, sleep-friendly documentaries.

Check for: factual errors, misleading simplifications, gaps in the logic, jargon used
before it is explained, and visuals that would teach something wrong.
Also check the calm tone: no cliffhangers, exclamations, alarming phrasing or energy spikes.
And the visual mix: mostly "still", "manim" only where a diagram truly helps, at most one
"ai_video" per chapter with narration under 25 words.

If all is sound: approved=true, issues=[], scenes returned unchanged.
Otherwise list each issue plainly and return corrected revised_scenes.
Do not lengthen the chapter unless a gap requires it."""


def review_chapter(outline: dict, chapter: dict, scenes: list[dict]) -> dict:
    prompt = (f"Outline: {json.dumps(outline, ensure_ascii=False)}\n\n"
              f"Chapter {chapter['number']} ({chapter['title']}) scenes:\n"
              f"{json.dumps(scenes, ensure_ascii=False)}")
    return structured(SYSTEM, prompt, "review", REVIEW_SCHEMA)
