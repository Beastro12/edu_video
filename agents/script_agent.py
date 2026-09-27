"""Script agents: an outline planner, then a writer that goes chapter by chapter.

A 10-20 minute script is ~1,200-2,400 words of narration plus a visual description
per scene. Writing it in one call risks truncation and drift; chapters keep each call
small and let the critic look closely at each part."""
import json

import config
from agents.llm import structured

OUTLINE_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "chapters": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "number": {"type": "integer"},
                "title": {"type": "string"},
                "summary": {"type": "string", "description": "What this chapter explains, 2-3 sentences"},
                "key_ideas": {"type": "array", "items": {"type": "string"}},
                "minutes": {"type": "number"},
            },
            "required": ["number", "title", "summary", "key_ideas", "minutes"],
        }},
    },
    "required": ["title", "chapters"],
}

SCENE = {
    "type": "object",
    "properties": {
        "concept": {"type": "string", "description": "The single idea this scene carries"},
        "narration": {"type": "string"},
        "visual_type": {"type": "string", "enum": ["still", "manim", "ai_video"]},
        "visual_description": {"type": "string"},
    },
    "required": ["concept", "narration", "visual_type", "visual_description"],
}
CHAPTER_SCHEMA = {"type": "object", "properties": {"scenes": {"type": "array", "items": SCENE}},
                  "required": ["scenes"]}

TONE = """This is a calm, long-form science documentary people may watch while falling asleep.
Narration: a soft, patient voice. Even emotional register, gentle wonder. Plain words,
unhurried sentences, room to breathe. Explain real mechanisms, not just vibes.
Never: cliffhangers, "but wait", stacked questions to the viewer, exclamation marks,
sudden shifts in energy, violent or alarming phrasing, "in this video"."""

VISUALS = """Each scene gets a visual_type:
- "still" (default, about 60% of scenes): one AI-generated image that drifts slowly on
  screen. Write visual_description as an image prompt: subject, composition, lighting,
  colours. Dark, low contrast, lots of negative space. No text.
- "manim" (about 30%): only where a simple diagram truly helps understanding (orbits,
  fields, waves, graphs, particles). Slow motion, at most 5 words on screen at once.
- "ai_video" (at most one per chapter): a moving shot worth the cost. One continuous
  slow camera move. Narration for these scenes stays under 25 words."""

OUTLINE_SYSTEM = f"""You plan calm long-form science documentaries.
{TONE}

Plan 4-6 chapters that build on each other: a quiet opening image, foundations,
deepening, the strange consequences, and a gentle closing reflection.
Chapter minutes must add up to the requested total."""

CHAPTER_SYSTEM = f"""You write one chapter of a calm long-form science documentary.
{TONE}

{VISUALS}

Scenes carry roughly 40-70 words of narration each. Continue smoothly from the
previous chapter's last lines; don't recap what was already explained."""


def plan_outline(topic: str, minutes: float) -> dict:
    return structured(OUTLINE_SYSTEM, f"Topic: {topic}\nTotal length: {minutes} minutes.",
                      "outline", OUTLINE_SCHEMA, max_tokens=4000)


def write_chapter(outline: dict, chapter: dict, previous_tail: list[str]) -> list[dict]:
    words = int(chapter["minutes"] * config.WORDS_PER_MIN)
    prompt = (
        f"Full outline (for context):\n{json.dumps(outline, ensure_ascii=False)}\n\n"
        f"Write chapter {chapter['number']}: {chapter['title']}\n"
        f"Target: about {words} words of narration.\n\n"
        + ("Previous chapter ended with:\n" + "\n".join(previous_tail) if previous_tail
           else "This is the opening chapter.")
    )
    return structured(CHAPTER_SYSTEM, prompt, "chapter", CHAPTER_SCHEMA)["scenes"]
