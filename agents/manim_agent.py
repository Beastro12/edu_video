"""Agent 4a: Claude writes Manim code per scene; errors are fed back for a fix."""
import re
import shutil
from pathlib import Path

import config
from agents.llm import text
from utils import atomic_output, content_key, run

HAS_LATEX = shutil.which("latex") is not None
CLASS_NAME = "DocScene"  # fixed, so the scene's position never leaks into the cache key
QUALITY = "-qh"

SYSTEM = f"""You write Manim Community Edition (v0.18+) code for calm science explainer videos.

Rules:
- Output ONE python code block. `from manim import *` then exactly one class named
  as instructed, subclassing Scene (or MovingCameraScene if you need camera moves).
- No external files, images, sounds, or network. Built-in shapes and Text only.
- {"MathTex/Tex are allowed." if HAS_LATEX else "LaTeX is NOT installed: never use MathTex, Tex, or Integer/DecimalNumber with LaTeX. Use Text for all labels and formulas."}
- Style: background "#0f1419". Soft palette: "#7fb3d5", "#a9dfbf", "#f5cba7",
  "#d7bde2", "#fdfefe". No flashing, no bright full-screen colour.
- This is a calm, sleep-friendly documentary: slow, gentle motion (run_time >= 2),
  at most 5 words of text on screen at once, font_size >= 32, nothing sudden.
- Timing: the sum of all run_time and wait() calls must be close to the target
  duration you are given. End with self.wait() of at least 1 second on a clean final frame.
- The visual must be scientifically correct. If a detail is uncertain, keep it schematic."""


def _extract_code(reply: str) -> str:
    m = re.search(r"```(?:python)?\n(.*?)```", reply, re.S)
    return (m.group(1) if m else reply).strip()


def _render(py_file: Path, class_name: str, media_dir: Path) -> Path:
    run(["manim", "render", QUALITY, "--fps", str(config.FPS), "--format", "mp4",
         "--media_dir", str(media_dir), str(py_file), class_name])
    hits = sorted(media_dir.glob(f"videos/{py_file.stem}/*/{class_name}.mp4"))
    if not hits:
        raise RuntimeError(f"Manim finished but no {class_name}.mp4 was found")
    return hits[-1]


def render_scene(scene: dict, target_s: float, out_dir: Path, atmospheric: bool = False) -> Path:
    brief = (
        f"Class name: {CLASS_NAME}\nTarget duration: {target_s:.1f} seconds\n"
        f"Concept: {scene['concept']}\nNarration (for timing and content):\n{scene['narration']}\n\n"
        f"Visual description:\n{scene['visual_description']}"
    )
    if atmospheric:
        brief += ("\n\nThis was planned as live-action footage. Instead make a slow, "
                  "abstract, atmospheric animation that evokes it (drifting particles, "
                  "soft shapes). No labels needed.")
    key = content_key("manim", SYSTEM, brief, config.CLAUDE_MODEL, QUALITY, config.FPS)
    out = out_dir / f"manim_{key}.mp4"
    if out.exists():
        return out
    py_file = out_dir / f"manim_{key}.py"
    out_dir.mkdir(parents=True, exist_ok=True)

    messages = [{"role": "user", "content": brief}]
    last_error = ""
    for attempt in range(1, config.MAX_MANIM_ATTEMPTS + 1):
        reply = text(SYSTEM, messages)
        py_file.write_text(_extract_code(reply))
        try:
            rendered = _render(py_file, CLASS_NAME, out_dir / "manim_media")
            with atomic_output(out) as tmp:
                shutil.copy(rendered, tmp)
            return out
        except RuntimeError as e:
            last_error = str(e)[-2500:]
            print(f"    manim attempt {attempt} failed; asking Claude to fix")
            messages += [
                {"role": "assistant", "content": reply},
                {"role": "user", "content": f"Rendering failed with this error. Fix the root cause and return the full corrected code.\n\n{last_error}"},
            ]
    raise RuntimeError(f"Scene {scene['id']} failed after {config.MAX_MANIM_ATTEMPTS} attempts:\n{last_error}")
