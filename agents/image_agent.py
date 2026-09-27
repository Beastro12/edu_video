"""Still images for the slow-drift scenes, via Google's Gemini image model (same key as Veo).
Imagen 4 was retired from the Gemini API (P0-6, D13); images now come from generate_content."""
from pathlib import Path

import config
import ledger
from agents import google_client, still_critic
from failures import Failures
from utils import atomic_output, content_key, load_json, output_lock, run, save_json

STYLE = (" Calm, dark, low-contrast, cinematic, deep blues and soft warm highlights, "
         "lots of negative space, soft focus edges. No text, no letters, no watermark, "
         "no people's faces.")


def available() -> bool:
    return bool(config.GOOGLE_API_KEY)


def cache_path(scene: dict, out_dir: Path) -> Path:
    prompt = scene["visual_description"] + STYLE
    key = content_key("still", prompt, config.IMAGE_MODEL, config.ASPECT_RATIO, config.IMAGE_SIZE)
    return out_dir / f"still_{key}.png"


def render_still(scene: dict, out_dir: Path, failures: Failures | None = None) -> Path:
    """A failed generation is recorded in `failures` (P1-7) and not attempted again unless
    it retries failures."""
    out = cache_path(scene, out_dir)
    with output_lock(out):
        if out.exists():
            return out
        if failures:
            failures.check(out.stem)
        try:
            with ledger.reserve("google", ledger.image_eur()):
                made = _buy(scene, out)
        except ledger.BudgetExceeded:
            raise  # not a failed generation: nothing was asked of the image model
        except Exception as e:
            if failures:
                failures.record(out.stem, "still", scene.get("id"), e)
            raise
        if failures:
            failures.clear(out.stem)
        return made


def _buy(scene: dict, out: Path) -> Path:
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


class StillRejected(RuntimeError):
    """Every attempt failed review; the scene falls back to Manim."""


def review_path(scene: dict, out_dir: Path) -> Path:
    """The per-scene decision: keyed by everything the image model and the reviewer are given."""
    key = content_key("review", scene["visual_description"] + STYLE, scene["concept"], config.IMAGE_MODEL,
                      config.ASPECT_RATIO, config.IMAGE_SIZE, config.CLAUDE_MODEL, still_critic.SYSTEM,
                      still_critic.PROMPT, still_critic.SCHEMA, config.STILL_REVIEW_RETRIES, config.PREVIEW_PX)
    return out_dir / f"review_{key}.json"


def review_log(scene: dict, out_dir: Path) -> dict | None:
    path = review_path(scene, out_dir)
    return load_json(path) if path.exists() else None


def is_decided(scene: dict, out_dir: Path) -> bool:
    """True if a rerun would buy nothing: rejected for good, or accepted and still on disk."""
    log = review_log(scene, out_dir)
    return log is not None and log.get("done", False) and (
        log["accepted"] is None or (out_dir / log["accepted"]).exists())


def next_still(scene: dict, out_dir: Path) -> Path:
    """The file the scene's next review attempt would make (for --estimate: did it fail before?)."""
    attempts = (review_log(scene, out_dir) or {"attempts": []})["attempts"]
    return cache_path({**scene, "visual_description": _retry_prompt(scene, attempts)}, out_dir)


def _retry_prompt(scene: dict, attempts: list[dict]) -> str:
    """Say what to show, not what went wrong: naming 'letters' can make the model draw some."""
    if not attempts:
        return scene["visual_description"]
    fixes = "; ".join(p for a in attempts for p in a["problems"])
    return (scene["visual_description"] + " Keep strictly to this subject, as a wordless image with no "
            "writing, symbols, people or faces, soft light and gentle contrast, and physically faithful "
            f"to the concept ({scene['concept']}). Earlier versions were turned down: {fixes}.")


def reviewed_still(scene: dict, out_dir: Path, failures: Failures | None = None) -> Path:
    """A still that passed Claude's review. A rejected one is regenerated with the critique
    folded into the prompt (STILL_REVIEW_RETRIES times), then StillRejected. Each attempt is
    written to review_<key>.json as it happens, so neither a rerun nor a crash pays twice."""
    path = review_path(scene, out_dir)
    with output_lock(path):  # a scene with the same picture waits for this decision
        return _decide(scene, out_dir, path, failures)


def _decide(scene: dict, out_dir: Path, path: Path, failures: Failures | None) -> Path:
    log = review_log(scene, out_dir) or {"accepted": None, "done": False, "attempts": []}
    if log.get("done"):
        if log["accepted"] and (out_dir / log["accepted"]).exists():
            return out_dir / log["accepted"]
        if not log["accepted"]:
            problems = log["attempts"][-1]["problems"] if log["attempts"] else []
            raise StillRejected(f"rejected earlier: {problems}")
        log = {"accepted": None, "done": False, "attempts": []}  # accepted file is gone: decide again
    while len(log["attempts"]) < 1 + config.STILL_REVIEW_RETRIES:
        prompt = _retry_prompt(scene, log["attempts"])
        still = render_still({**scene, "visual_description": prompt}, out_dir, failures)
        verdict = still_critic.review(scene, still)
        log["attempts"].append({"still": still.name, "prompt": prompt, "ok": verdict["ok"],
                                "problems": verdict["problems"]})
        if verdict["ok"]:
            log.update(accepted=still.name, done=True)
        save_json(path, log)
        if verdict["ok"]:
            return still
    log["done"] = True
    save_json(path, log)
    raise StillRejected(f"rejected {len(log['attempts'])} times: {log['attempts'][-1]['problems']}")
