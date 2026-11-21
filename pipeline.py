"""Orchestrator. Every step caches to build/<slug>/, so a crash never re-bills finished work.

    python pipeline.py "What happens inside a neutron star" --minutes 15
    python pipeline.py "What happens inside a neutron star" --script-only   # review before spending
    python pipeline.py "A paper from last month" --brief brief.md           # verified facts for new research
"""
import argparse
import shutil
import signal
import sys
import threading
import time
from concurrent.futures import ALL_COMPLETED, FIRST_EXCEPTION, ThreadPoolExecutor, wait
from pathlib import Path

import config
import failures as failure_log
import ledger
import metadata
import qa
import run_report
import thumbnail
from agents import ai_video, critic, image_agent, manim_agent, music, script_agent, voice
from assembly import add_music, build_scene_clip, crossfade_concat, scene_tail
from failures import FailedEarlier, Failures
from utils import (
    atomic_output,
    brief_key,
    content_key,
    duration,
    kept_brief,
    load_json,
    log,
    save_json,
    slugify,
    video_duration,
)


def _chapter_sources(work: Path) -> str | None:
    """Hash of outline.json and every chapter file it lists; None if any is missing."""
    outline_path = work / "outline.json"
    if not outline_path.exists():
        return None
    outline = load_json(outline_path)
    parts = [outline]
    for ch in outline["chapters"]:
        ch_path = work / f"chapter_{ch['number']:02d}.json"
        if not ch_path.exists():
            return None
        parts.append(load_json(ch_path))
    return content_key("chapters", parts)


def _write_text(path: Path, text: str) -> None:
    with atomic_output(path) as tmp:
        tmp.write_text(text, encoding="utf-8")


def _script_started(work: Path) -> bool:
    return (work / "outline.json").exists() or (work / "script.json").exists() or any(work.glob("chapter_*.json"))


def _recorded_brief(work: Path) -> str:
    """The brief the build's script is stamped with. Builds from before briefs existed have no
    stamp and count as written without one."""
    stamp = work / config.BRIEF_KEY_FILE
    return stamp.read_text(encoding="utf-8").strip() if stamp.exists() else "none"


def _brief_conflict(work: Path, brief: str | None) -> str | None:
    """Why `brief` (None: no brief) can't go on with the script already in `work`, or None. A
    script written from another brief would not match the one whose Sources go into the
    description. Reads only."""
    if _recorded_brief(work) == brief_key(brief) or not _script_started(work):
        return None
    delete = "delete outline.json, chapter_*.json and script.json to write it again with this one"
    if _recorded_brief(work) == "none":
        return (f"The script in {work} was written without a brief; this run has one. "
                f"Rerun without --brief, or {delete}.")
    what = "written with a brief; this run has none" if brief is None else "written with a different brief"
    kept = f" (--brief {work / config.BRIEF_FILE})" if kept_brief(work) is not None else ""
    return f"The script in {work} was {what}. Rerun with the brief it was written from{kept}, or {delete}."


def _stamp_brief(work: Path, brief: str | None) -> None:
    """Stamp the build with the brief (or none) its script is written from, or refuse to go on
    with a script written from another."""
    conflict = _brief_conflict(work, brief)
    if conflict:
        raise RuntimeError(conflict)
    if not (work / config.BRIEF_KEY_FILE).exists() or _recorded_brief(work) != brief_key(brief):
        # The seed record (P3-4) belongs to the brief it was recorded under: a first or changed stamp ends it,
        # first, so a crash between the two leaves no record rather than one for another brief. Free: D9 is
        # untouched (nothing paid is keyed by it).
        (work / config.SEED_FILE).unlink(missing_ok=True)
        # before any generation, so a crash leaves a stamp that matches the files
        _write_text(work / config.BRIEF_KEY_FILE, brief_key(brief))


def _recorded_style(work: Path) -> str | None:
    """The style the build's pictures are made in (P3-2), or None for a build with no record
    (made before styles, or not yet past its script), which counts as no choice made."""
    path = work / config.STYLE_FILE
    if not path.exists():
        return None
    name = path.read_text(encoding="utf-8").strip()
    if name not in config.STYLES:
        raise ValueError(f"{path} says {name!r}, which is not a style ({', '.join(sorted(config.STYLES))}); "
                         "fix or delete the file")
    return name


def pick_style(flag: str | None, recorded: str | None, default: str) -> tuple[str, str]:
    """(style, where it came from): --style, else the build's record, else the default. The record
    outranks the default so that a rerun without the flag can't quietly switch a build and buy
    every picture again."""
    if flag is not None:
        return flag, "--style"
    if recorded is not None:
        return recorded, "recorded in the build"
    return default, "default"


def _record_style(work: Path) -> None:
    """Before any picture is made, so a run that stops partway leaves the style it was making."""
    if _recorded_style(work) != config.STYLE:
        _write_text(work / config.STYLE_FILE, config.STYLE)


def _seed_folder(brief_path: Path, work: Path) -> Path | None:
    """The folder of a brief that carries its reviewed script (P3-3): the outline.json next to
    it. Not the build folder itself, which `--brief <work>/brief.md` points at."""
    folder = brief_path.parent
    if not (folder / "outline.json").exists() or folder.resolve() == work.resolve():
        return None
    return folder


def _load_seed(seed: Path, brief: str) -> list[Path]:
    """The chapter files, of those the seed's outline lists, that exist in the seed. Raises
    ValueError saying what is wrong: the folder's script was reviewed with another version of its
    brief (its brief.key), or the outline or a chapter file isn't what get_script reads."""
    key_path = seed / config.BRIEF_KEY_FILE
    recorded = key_path.read_text(encoding="utf-8").strip() if key_path.exists() else None
    if recorded != brief_key(brief):
        raise ValueError(f"the script in {seed} was reviewed with a different version of its brief "
                         f"({key_path.name} {'is missing' if recorded is None else 'does not match'}): "
                         f"re-review the chapters, then update {key_path} (python -c \"from utils import "
                         f"brief_key; print(brief_key(open('{seed / config.BRIEF_FILE}', encoding='utf-8-sig')"
                         f".read()), end='')\" > {key_path})")
    outline_path = seed / "outline.json"
    try:
        chapters = load_json(outline_path)["chapters"]
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise ValueError(f"{outline_path} must be a JSON object with a 'chapters' list ({type(e).__name__})") from e
    if not (isinstance(chapters, list) and chapters and all(
            isinstance(c, dict) and isinstance(c.get("number"), int) and "title" in c and "minutes" in c
            for c in chapters)):
        raise ValueError(f"{outline_path}: 'chapters' must be a list of chapters with number, title and minutes")
    scene_keys = set(script_agent.SCENE["properties"])
    visual_types = script_agent.SCENE["properties"]["visual_type"]["enum"]
    files = []
    for ch in chapters:
        path = seed / f"chapter_{ch['number']:02d}.json"
        if path.exists():
            try:
                scenes = load_json(path)
            except (OSError, ValueError) as e:
                raise ValueError(f"{path} is not readable JSON ({type(e).__name__})") from e
            if not isinstance(scenes, list):
                raise ValueError(f"{path} must be a JSON list of scenes")
            for i, scene in enumerate(scenes, 1):
                if not (isinstance(scene, dict) and set(scene) == scene_keys and scene["visual_type"] in visual_types):
                    raise ValueError(f"{path}, scene {i}: needs exactly the keys {sorted(scene_keys)} "
                                     f"and a visual_type of {visual_types}")
            files.append(path)
    return files


def _outline_matches(seed: Path, work: Path) -> bool:
    """The build has no outline yet, or has the seed's: its chapters then belong to the seed. Reads only."""
    outline = seed / "outline.json"
    return not (work / outline.name).exists() or load_json(work / outline.name) == load_json(outline)


def _plan_seed(seed: Path, chapter_files: list[Path], work: Path,
               advice: bool = True) -> tuple[list[Path], str | None]:
    """What seeding would do to `work`, file by file (P3-3): the seed files it would copy (the
    outline first, then the chapters the build lacks), and a note when it leaves something because
    the build's copy differs. The build's outline must be the seed's, or none is: its chapters
    belong to it. `advice` False (P3-4: a run without --brief, seeding from the record) leaves out
    the "delete ..." hints. Reads only."""
    outline = seed / "outline.json"
    if not _outline_matches(seed, work):
        return [], (f"{outline.name} in {work} isn't the one in {seed}; not seeding"
                    + (" (to start from the seed's, delete the build's outline.json, chapter_*.json and "
                       "script.json)" if advice else ""))
    copy = [] if (work / outline.name).exists() else [outline]
    differ = []
    for path in chapter_files:
        if not (work / path.name).exists():
            copy.append(path)
        elif load_json(work / path.name) != load_json(path):
            differ.append(path.name)
    note = (f"{', '.join(differ)}: not the same in {work} as in {seed}; keeping the build's"
            + (" (to take the seed's, delete the build's copy and script.json)" if advice else "")) if differ else None
    return copy, note


def _shown_note(note: str | None, copy: list[Path], seed: Path, work: Path, recorded: bool) -> str | None:
    """`note`, unless it is chatter: on a run that seeds from the record (P3-4) the build says something
    only when it copies, or when it rejects the folder (an outline that isn't the build's)."""
    if note and recorded and not copy and _outline_matches(seed, work):
        return None
    return note


def _record_seed(work: Path, seed: Path) -> None:
    """Write the folder the build is seeded from (P3-4), an absolute path, atomically and only when it
    changes. Readers: `_recorded_seed`, on a rerun without --brief."""
    path, text = work / config.SEED_FILE, str(seed.resolve())
    try:
        current = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):  # none, or one nobody can read (hand-edited): replaced
        current = None
    if current != text:
        _write_text(path, text)


def _seed_needed(work: Path) -> bool:
    """The build lacks outline.json or a chapter file its outline lists (no outline.json counts as
    lacking one); an outline that can't be read does too, and get_script reports it."""
    try:
        chapters = load_json(work / "outline.json")["chapters"]
        return any(not (work / f"chapter_{ch['number']:02d}.json").exists() for ch in chapters)
    except (OSError, ValueError, KeyError, TypeError):
        return True


def _recorded_seed(work: Path, brief: str) -> tuple[Path | None, list[Path]]:
    """The folder recorded in `work` by an earlier seeding (P3-4), with the chapter files it holds, for a run
    that has the build's own kept brief (a rerun without --brief, or --brief <work>/brief.md). Reads only.
    - Nothing unless the build lacks outline.json or a chapter its outline lists: a build that is whole
      is not checked, so a topic folder that moved or was re-reviewed since is none of its business.
    - The folder is used only when it exists, has a brief.md, its brief.key matches that brief.md (reviewed
      with this version) and it is the build's brief (brief_key equal to the build's stamp), and _load_seed
      finds a valid script. Otherwise ONE line says why, and nothing is seeded (no error: the writer then
      fills the gap, with the kept brief)."""
    record = work / config.SEED_FILE
    if not record.exists() or not _seed_needed(work):
        return None, []

    def reject(why: str) -> tuple[None, list]:
        print(f"• script: not seeding from the folder recorded in {record}: {why}")
        return None, []

    try:
        text = record.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        return reject(f"cannot read it ({type(e).__name__})")
    try:  # a record is hand-editable: whatever is wrong with it is one line, never a traceback
        folder = Path(text)
        if not text or not folder.is_absolute():
            return reject(f"{text!r} is not an absolute path")
        if folder.resolve() == work.resolve():
            return reject("it names the build folder itself, which is not a seed")
        if not folder.exists():
            return reject(f"{text!r} does not exist")
        if not folder.is_dir():
            return reject(f"{text!r} is not a folder")
        try:
            topic_brief = (folder / config.BRIEF_FILE).read_text(encoding="utf-8-sig")
        except (OSError, UnicodeDecodeError) as e:
            return reject(f"{folder / config.BRIEF_FILE} cannot be read ({type(e).__name__})")
        try:
            chapters = _load_seed(folder, topic_brief)  # its brief.key against its own brief.md, and the script
        except ValueError as e:
            return reject(str(e))
        if brief_key(topic_brief) != brief_key(brief):
            return reject(f"the {config.BRIEF_FILE} in {folder} is not the brief this script was written with "
                          "(it was changed since)")
        return folder, chapters
    except (OSError, ValueError) as e:  # a NUL byte, a name too long, a brief.key that is a folder, a key that isn't UTF-8
        return reject(f"{text[:80]!r} cannot be used ({type(e).__name__})")


def seed_script(seed: Path, chapter_files: list[Path], work: Path, brief: str, recorded: bool = False) -> None:
    """Start or complete the build's script from the reviewed one in the brief's folder (P3-3):
    its outline and chapters, each copied only where the build has no file of that name. The
    writer does the rest, with the brief. Stamps first, after the brief check, so a crash
    mid-copy leaves a stamp that matches the files; a rerun finishes the job.

    P3-4: then the folder is recorded in the build (unless its outline isn't the build's) and the brief is
    kept, both before any copy, so a rerun WITHOUT --brief finds the kept brief and the folder and
    completes the job; `recorded` is that rerun (it prints only what it copies or why it rejects)."""
    _stamp_brief(work, brief)
    if _outline_matches(seed, work):
        _record_seed(work, seed)
    keep_brief(brief, work)
    copy, note = _plan_seed(seed, chapter_files, work, advice=not recorded)
    for path in copy:
        with atomic_output(work / path.name) as tmp:
            shutil.copyfile(path, tmp)
    if copy:
        print(f"• script: seeded {', '.join(p.name for p in copy)} from {seed}")
    note = _shown_note(note, copy, seed, work, recorded)
    if note:
        print(f"• script: {note}")


def get_script(topic: str, minutes: float, work: Path, skip_critic: bool, brief: str | None = None) -> dict:
    """script.json is assembled from outline.json + chapter_XX.json. Both can be edited:
    hand edits to script.json are kept; edits to a chapter file rebuild script.json;
    edits to both refuse to guess which one should win. `brief` (P3-1) is the research brief for
    the outline, writer and critic; None means none, even if `work` holds one from an earlier run.
    Once outline.json, a chapter or script.json exists, a different brief (or one where there was
    none, or none where there was one) is refused instead of mixing it into the script."""
    _stamp_brief(work, brief)
    final, meta_path = work / "script.json", work / "script.meta.json"
    if final.exists():
        script = load_json(final)
        sources = _chapter_sources(work)
        meta = load_json(meta_path) if meta_path.exists() else None
        if sources is None or meta is None or meta["chapters"] == sources:
            if meta is None:  # written before this check existed: adopt it as the baseline
                save_json(meta_path, {"chapters": sources, "script": content_key("script", script)})
            print("• script: using cached script.json (edit it freely)")
            return script
        if meta["script"] != content_key("script", script):
            raise RuntimeError(
                f"Both {final} and a chapter file were edited since the script was assembled. "
                "Keep one: delete script.json to rebuild it from the chapters, "
                "or undo the chapter edit to keep your script.json edits.")
        print("• script: chapter files changed; rebuilding script.json from them")

    outline_path = work / "outline.json"
    if outline_path.exists():
        outline = load_json(outline_path)
    else:
        print("• outline agent: planning chapters")
        outline = script_agent.plan_outline(topic, minutes, brief=brief)
        save_json(outline_path, outline)
    for ch in outline["chapters"]:
        print(f"    {ch['number']}. {ch['title']} ({ch['minutes']} min)")

    scenes, tail = [], []
    for ch in outline["chapters"]:
        ch_path = work / f"chapter_{ch['number']:02d}.json"
        if ch_path.exists():
            ch_scenes = load_json(ch_path)
        else:
            print(f"• writer: chapter {ch['number']}")
            ch_scenes = script_agent.write_chapter(outline, ch, tail, brief=brief)
            if not skip_critic:
                for _ in range(config.MAX_CRITIC_ROUNDS):
                    result = critic.review_chapter(outline, ch, ch_scenes, brief=brief)
                    for issue in result["issues"]:
                        print(f"    critic: {issue}")
                    ch_scenes = result["revised_scenes"]
                    if result["approved"]:
                        break
            save_json(ch_path, ch_scenes)
        for s in ch_scenes:
            s["chapter"] = ch["number"]
        scenes += ch_scenes
        tail = [s["narration"] for s in ch_scenes[-2:]]

    for i, s in enumerate(scenes, start=1):
        s["id"] = i
    script = {"title": outline["title"], "scenes": scenes}
    save_json(final, script)
    save_json(meta_path, {"chapters": _chapter_sources(work), "script": content_key("script", script)})
    words = sum(len(s["narration"].split()) for s in scenes)
    print(f"• script: {len(scenes)} scenes, {words} words (~{words / config.WORDS_PER_MIN:.0f} min)")
    return script


def make_visual(scene: dict, target_s: float, out_dir: Path, allow_veo: bool,
                failures: Failures | None = None) -> tuple[Path, str]:
    """Returns (path, kind). Falls back video -> still -> Manim, so one failed
    generation never stops the whole film. Failures are recorded in `failures` and, on
    later runs, skipped straight to the fallback (P1-7)."""
    vt = scene["visual_type"]
    if vt == "ai_video" and allow_veo:
        try:
            return ai_video.render_scene(scene, out_dir, failures=failures), "ai"
        except ledger.BudgetExceeded:
            raise  # out of money is not a failed generation: stop the run
        except ai_video.DailyCapReached as e:
            log(f"    scene {scene['id']}: Veo daily cap reached ({e}); using a still")
        except FailedEarlier as e:
            log(f"    scene {scene['id']}: Veo {e}; using a still")
        except Exception as e:  # noqa: BLE001
            log(f"    scene {scene['id']}: Veo failed ({str(e)[:100]}); trying a still")
    if vt in ("ai_video", "still") and image_agent.available():
        try:
            return image_agent.reviewed_still(scene, out_dir, failures), "still"
        except ledger.BudgetExceeded:
            raise
        except image_agent.StillRejected as e:
            log(f"    scene {scene['id']}: still failed review ({str(e)[:120]}); falling back to Manim")
        except FailedEarlier as e:
            log(f"    scene {scene['id']}: image model {e}; falling back to Manim")
        except Exception as e:  # noqa: BLE001
            log(f"    scene {scene['id']}: image model failed ({str(e)[:100]}); falling back to Manim")
    atmospheric = vt != "manim"
    return manim_agent.render_scene(scene, target_s, out_dir, atmospheric), "manim"


def _planned_scenes(minutes: float, work: Path, brief: str | None = None,
                    seed: Path | None = None) -> tuple[list[dict], list[tuple[str, int, int]]]:
    """Scenes this run will voice and illustrate, and the script-stage Claude calls still to
    make as (label, input_tokens, output_tokens). Real text where it exists; a synthetic
    scene mix following D3 where it doesn't. Every script-stage call carries the brief. Files the
    build lacks are planned from `seed` where seeding would copy them in (P3-3), without copying."""
    if (work / "script.json").exists():
        return load_json(work / "script.json")["scenes"], []
    own_outline = work / "outline.json"
    seeded = (seed is not None and (seed / "outline.json").exists()
              and (not own_outline.exists() or load_json(own_outline) == load_json(seed / "outline.json")))
    sources = [work, seed] if seeded else [work]  # per file: the build's own, else the one seeding would copy in

    def find(name: str) -> Path | None:
        return next((d / name for d in sources if (d / name).exists()), None)

    calls = []
    prompt_tokens = review_tokens = config.EST_PROMPT_TOKENS
    if brief is not None:
        block = script_agent.brief_block(brief)
        prompt_tokens += ledger.claude_input_estimate(block)
        review_tokens += ledger.claude_input_estimate(block, critic.BRIEF_CHECK)
    outline_path = find("outline.json")
    if outline_path:
        chapters = load_json(outline_path)["chapters"]
    else:
        chapters = [{"number": i + 1, "minutes": minutes / config.EST_CHAPTERS}
                    for i in range(config.EST_CHAPTERS)]
        calls.append(("outline", prompt_tokens, config.EST_OUTLINE_TOKENS_PER_CHAPTER * config.EST_CHAPTERS))
    scenes = []
    for ch in chapters:
        ch_path = find(f"chapter_{ch['number']:02d}.json")
        if ch_path:
            scenes += load_json(ch_path)
            continue
        words = ch["minutes"] * config.WORDS_PER_MIN
        out_tokens = int(words * config.EST_SCRIPT_TOKENS_PER_WORD)
        calls.append(("chapter", prompt_tokens, out_tokens))
        calls.append(("review", review_tokens + out_tokens, out_tokens))
        n = max(1, round(words / config.EST_WORDS_PER_SCENE))
        n_manim = round(config.EST_MANIM_SHARE * (n - 1))
        types = ["ai_video"] + ["manim"] * n_manim + ["still"] * (n - 1 - n_manim)
        chars = int(words * config.EST_CHARS_PER_WORD / n)
        scenes += [{"concept": "planned", "visual_type": vt, "narration": "x" * chars,
                    "visual_description": f"planned {ch['number']}.{i}"} for i, vt in enumerate(types)]
    return scenes, calls


def _still_rejected_for_good(scene: dict, visuals: Path) -> bool:
    return image_agent.is_decided(scene, visuals) and image_agent.review_log(scene, visuals)["accepted"] is None


def estimate_cost(minutes: float, work: Path, allow_veo: bool, skip_critic: bool,
                  worst_case: bool = False, retry_failed: bool = False,
                  brief: str | None = None, seed: Path | None = None) -> dict[str, float]:
    """Projected spend in EUR for what this run would still have to buy. Cached work costs
    nothing, and a generation that failed before costs its fallback (P1-7). Calls no paid API.
    Typical: one critic round, one Manim attempt; worst case: every critic round and every
    Manim retry."""
    scenes, calls = _planned_scenes(minutes, work, brief, seed)
    failed = Failures(failure_log.path(work), retry=retry_failed).earlier
    model = config.CLAUDE_MODEL
    reviews = 0 if skip_critic else (config.MAX_CRITIC_ROUNDS if worst_case else 1)
    manim_tries = config.MAX_MANIM_ATTEMPTS + 1 if worst_case else 1  # worst: every fix round, then a retime
    est = {"claude": sum(ledger.claude_eur(model, i, o) * (reviews if label == "review" else 1)
                         for label, i, o in calls),
           "elevenlabs": 0.0, "images": 0.0, "veo": 0.0, "thumbnail": 0.0}
    veo_left = max(0, config.VEO_MAX_PER_DAY - ledger.veo_clips_today())
    for scene in scenes:
        audio = voice.cache_path(scene["narration"], work / "audio")
        if not audio.exists():
            est["elevenlabs"] += ledger.tts_eur(len(scene["narration"]))
        vt = scene["visual_type"]
        veo = ai_video.cache_path(scene, work / "visuals")
        veo_cached = vt == "ai_video" and allow_veo and veo.exists()
        if vt == "ai_video" and allow_veo and (veo_cached or (veo_left > 0 and not failed(veo.stem))):
            if not veo_cached:
                est["veo"] += ledger.video_eur(config.VEO_CLIP_S)
                veo_left -= 1
        elif (vt in ("ai_video", "still") and image_agent.available()
              and not _still_rejected_for_good(scene, work / "visuals")
              and not failed(image_agent.next_still(scene, work / "visuals").stem)):
            if not image_agent.is_decided(scene, work / "visuals"):
                tries = 1 + config.STILL_REVIEW_RETRIES if worst_case else 1
                est["images"] += tries * ledger.image_eur()
                est["claude"] += tries * ledger.claude_eur(model, *config.EST_STILL_REVIEW_TOKENS)
                if worst_case:  # every attempt rejected: the scene then needs Manim as well
                    est["claude"] += manim_tries * ledger.claude_eur(model, *config.EST_MANIM_TOKENS)
        else:
            cached = audio.exists() and manim_agent.cache_path(
                scene, config.LEAD_IN_S + duration(audio) + config.TAIL_S, work / "visuals",
                atmospheric=vt != "manim").exists()
            if not cached:
                est["claude"] += manim_tries * ledger.claude_eur(model, *config.EST_MANIM_TOKENS)
    est["thumbnail"] = thumbnail.estimate(scenes, work, failed)  # P2-2
    return est


def print_estimate(topic: str, est: dict[str, float], worst: dict[str, float], style: str) -> None:
    total, worst_total, spent = sum(est.values()), sum(worst.values()), ledger.spent_eur()
    print(f'Projected cost for "{topic}" in the {style} style (estimate; prices marked "verify" in '
          "config.py; cached work excluded):")
    for name, eur in est.items():
        print(f"  {name:<11} €{eur:.2f}")
    print(f"  {'total':<11} €{total:.2f}   (worst case, every critic round and Manim retry: €{worst_total:.2f})")
    veo_left = max(0, config.VEO_MAX_PER_DAY - ledger.veo_clips_today())
    print(f"  Veo clips left today: {veo_left} of {config.VEO_MAX_PER_DAY}")
    verdict = "fits" if spent + worst_total <= config.BUDGET_EUR else "may EXCEED"
    print(f"Spent so far €{spent:.2f} of the €{config.BUDGET_EUR:.2f} budget: this run {verdict} it.")
    if not image_agent.available():
        print("  (no GOOGLE_API_KEY: stills and Veo scenes are counted as Manim)")

class _Run:
    """What `in_parallel`'s parallel path knows about one run (P1-14): when Ctrl-C was pressed, which jobs
    failed and when, and whether the SIGINT handler may touch `ledger`'s Events.

    The object is the SIGINT handler itself. It only records: a plain list append, and the two flags, and
    never while `guard` is up. The guard exists because a handler runs on the main thread between two
    bytecodes, possibly inside the main thread's own `Event.set()` / `.clear()` of those flags, which holds
    the Event's (non-reentrant) lock: a handler calling `.set()` there would wait for itself forever. The
    main thread raises the guard around every such call, so the handler then only records; when the guarded
    section ends and a press was recorded, the main thread sets the flags itself. Handlers can also nest
    (a second press while the first is in `.set()`), so the handler raises the guard around its own calls
    too. It prints nothing (a print could re-enter the console's own lock)."""

    def __init__(self):
        self.presses: list[float] = []  # time.monotonic() of each press, in order
        self.failures: list[tuple[float, BaseException]] = []  # (when, error) of each job that raised
        self.guard = False
        self.waiting = 0  # calls in flight when the main thread last looked

    def __call__(self, signum, frame):
        self.presses.append(time.monotonic())
        self.set_flags()

    def set_flags(self) -> None:
        """Stop everything, even a Veo job in flight (it is recorded as billed). Does nothing while the
        guard is up: the main thread then sets them when its guarded section ends."""
        if self.guard:
            return
        self.guard = True
        try:
            ledger.stopping.set()
            ledger.interrupted.set()
        finally:
            self.guard = False

    def guarded(self, *calls) -> None:
        """The main thread's own Event calls, with the handler barred from touching the Events; then
        whatever a press recorded meanwhile is paid."""
        self.guard = True
        try:
            for call in calls:
                call()
        finally:
            self.guard = False
        if self.presses:
            self.set_flags()

    def job(self, fn):
        def run_one(x):
            if self.presses:  # queued when the press came: not started (the run raises KeyboardInterrupt, so
                return None   # nothing reads this result)
            try:
                return fn(x)
            except BaseException as e:
                self.failures.append((time.monotonic(), e))
                raise
        return run_one

    def verdict(self, raised: KeyboardInterrupt | None = None) -> BaseException | None:
        """What `in_parallel` raises: whichever came first, a press or a failed job (a job that fails
        because of the press, say "the run is stopping", comes after it by construction). A press first
        is a KeyboardInterrupt; a failure first is that failure, noted if a press followed. A press that
        was recorded is never turned into a normal return."""
        failure = min(self.failures, key=lambda f: f[0], default=None)
        if self.presses and (failure is None or self.presses[0] <= failure[0]):
            return raised or KeyboardInterrupt()
        if failure is not None:
            if self.presses:
                failure[1].add_note(f"Ctrl-C pressed while waiting for {self.waiting} calls in flight")
            return failure[1]
        return None


def _stop(run: _Run, futures: list, interrupt: bool) -> None:
    """Cancel what is queued, then raise the flags (`interrupt`: a press, so a Veo job in flight gives up
    too), and say what is being waited for. Cancelling first: a queued job must not start in the gap."""
    for f in futures:
        f.cancel()
    run.guarded(ledger.stopping.set, *([ledger.interrupted.set] if interrupt else []))
    n = sum(1 for f in futures if not f.done())
    if interrupt:
        print(f"• stopping: waiting for {n} calls in flight to finish (a Veo job gives up and is counted as paid)"
              if n else "• stopping: no calls in flight")
    elif n:
        print(f"• stopping after a failure: waiting for {n} calls in flight to finish (Ctrl-C now makes a Veo "
              "job give up and counts it as paid)")


def _await(run: _Run, futures: list) -> None:
    """Return when every future is done or cancelled. Looks every PARALLEL_POLL_S: the handler only records
    a press, and a record-only handler doesn't end a timed wait early. The first press or failure stops the
    run, once."""
    stopped = False
    while True:
        pending = [f for f in futures if not f.done()]
        if not run.presses:  # what a press that comes next interrupted
            run.waiting = len(pending)
        if not stopped and (run.presses or run.failures):
            stopped = True
            _stop(run, futures, interrupt=bool(run.presses))
            continue
        if not pending:
            return
        wait(pending, timeout=config.PARALLEL_POLL_S,
             return_when=ALL_COMPLETED if stopped else FIRST_EXCEPTION)


def in_parallel(fn, items: list) -> list:
    """fn over items on WORKERS threads, results in the items' order. The first failure (say
    BudgetExceeded) stops the run at once: calls not yet started are cancelled and, until the
    calls in flight have finished, no new paid call may be reserved (ledger.stopping).

    Ctrl-C (P1-14, D17). An asynchronous KeyboardInterrupt raised inside concurrent.futures or
    threading can leave a started worker no one joins, or end the wait without recording a paid Veo
    job; no try/finally around those internals is safe. So the parallel path installs a SIGINT handler
    (`_Run`) that only records the press, and everything else is the main thread's loop:
    - it stops submitting at a press, cancels what is queued, raises both flags (a Veo job in flight
      gives up and is recorded as billed) and waits for the futures in flight, then raises
      KeyboardInterrupt;
    - after a failed job it does the same with `stopping` only, and a press during that wait also
      makes a Veo job give up. Whichever came first decides what is raised: a press first is a
      KeyboardInterrupt (jobs failing because of it are not re-raised); a failure first is
      re-raised, noted with the press;
    - a second press is harmless. Ctrl-\\ (SIGQUIT) kills at once, and calls in flight are then not
      recorded in the ledger;
    - the end order is fixed: shut the pool down (the handler still installed, so a press there only
      records), raise the guard for good, clear the flags, restore the handler, and only then raise.
      A press is never swallowed: this never returns normally after one.

    The handler is installed only on the main thread and only when SIGINT is still Python's default
    (asyncio.Runner's rule): under nohup (SIG_IGN), a custom or a C handler, or off the main thread,
    nothing is installed and Ctrl-C does what it did before: an exception inside the pool's machinery,
    caught here as a press (flags, cancel, wait, re-raise), with no guarantee about a thread that was
    being started.

    Two limits, both small. A press in the few bytecodes after the handler is restored (the default is back)
    surfaces as a bare KeyboardInterrupt, even where a failed job came first: everything is already cleaned up
    by then, but the failure's note is lost. And "no reservation after the press" means after the handler has
    run (it records the press, then sets the flags, in that order): a call that reserved in the instants
    between the signal arriving and the handler running is not a late one.

    The sequential path (WORKERS <= 1, or one item) installs nothing: the main thread makes the calls,
    and a Ctrl-C in a Veo poll is recorded by the poll itself (agents/ai_video.py)."""
    if config.WORKERS <= 1 or len(items) <= 1:
        return [fn(x) for x in items]
    run = _Run()
    installed = (threading.current_thread() is threading.main_thread()
                 and signal.getsignal(signal.SIGINT) is signal.default_int_handler)
    if installed:  # before the try: a press already pending raises from here, with nothing yet to undo
        signal.signal(signal.SIGINT, run)
    pool, futures, raised = None, [], None
    try:
        try:
            pool = ThreadPoolExecutor(config.WORKERS)
            for x in items:
                if run.presses:
                    break
                futures.append(pool.submit(run.job(fn), x))
            _await(run, futures)
        except KeyboardInterrupt as e:  # no handler (see above): an exception, wherever it landed
            raised = e
            run.presses.append(time.monotonic())
            _await(run, futures)
        except BaseException:  # our own error: like a failed job, the calls in flight finish, nothing new starts
            _stop(run, futures, interrupt=False)
            raise
    finally:
        try:
            if pool is not None:
                pool.shutdown(wait=True, cancel_futures=True)
        finally:
            run.guard = True  # for good: from here the handler only records
            try:
                ledger.stopping.clear()  # nothing is in flight any more: the next run starts clean
                ledger.interrupted.clear()
            finally:
                if installed:
                    signal.signal(signal.SIGINT, signal.default_int_handler)
    error = run.verdict(raised)
    if error is not None:
        raise error
    return [f.result() for f in futures]


def generate_assets(script: dict, work: Path, allow_veo: bool, retry_failed: bool = False) -> list[dict]:
    """Everything paid for, per scene: narration first (its length sets the scene's), then the
    visual. Independent scenes run concurrently (P1-5). Visuals that failed on an earlier run
    go straight to their fallback unless `retry_failed` (P1-7)."""
    scenes = script["scenes"]
    failures = Failures(failure_log.path(work), retry=retry_failed)
    run_report.stage("narration")
    print(f"• narration: {len(scenes)} scenes, {config.WORKERS} at a time")
    audio = in_parallel(lambda s: voice.narrate(s["narration"], work / "audio"), scenes)
    # Every picture, the last scene's too, is made for an ordinary tail: a Manim render is keyed by its
    # length (D9), and only the clip has the final tail (it holds the picture's last frame, P2-7).
    targets = [config.LEAD_IN_S + duration(a) + config.TAIL_S for a in audio]
    run_report.stage("visuals")
    print("• visuals")
    visuals = in_parallel(lambda i: make_visual(scenes[i], targets[i], work / "visuals", allow_veo, failures),
                          list(range(len(scenes))))
    return [{"scene": s, "audio": a, "visual": v, "kind": k}
            for s, a, (v, k) in zip(scenes, audio, visuals, strict=True)]


def render_film(script: dict, work: Path, allow_veo: bool, seed: str, retry_failed: bool = False) -> Path:
    """Per-scene files live in audio/, visuals/ and clips/, named by a hash of their inputs.
    manifest.json maps each scene id to the files it used."""
    clips, manifest = [], []
    assets = generate_assets(script, work, allow_veo, retry_failed)
    run_report.stage("scene clips")
    for i, asset in enumerate(assets):
        scene, narration, visual, kind = asset["scene"], asset["audio"], asset["visual"], asset["kind"]
        print(f"• scene {scene['id']}/{len(script['scenes'])}: {scene['concept']} [{kind}]")
        clip = build_scene_clip(visual, narration, work / "clips", kind, scene["id"],
                                scene_tail(i, len(script["scenes"])))
        clips.append(clip)
        review = image_agent.review_path(scene, work / "visuals")
        manifest.append({"id": scene["id"], "concept": scene["concept"], "kind": kind,
                         "audio": str(narration.relative_to(work)),
                         "visual": str(visual.relative_to(work)),
                         "clip": str(clip.relative_to(work)),
                         "still_review": str(review.relative_to(work)) if kind != "ai" and review.exists() else None})
    save_json(work / "manifest.json", {"title": script["title"], "style": config.STYLE, "scenes": manifest})

    run_report.stage("assembly")
    print("• assembling (crossfades)")
    narrated = crossfade_concat(clips, work / "narrated.mp4")
    total = video_duration(narrated)  # the container also counts AAC's end padding
    tracks = music.list_tracks()
    if tracks:
        bed = music.build_bed(tracks, total, work / "music_bed.wav", seed)
    else:
        print("    no files in music/ — using a synthetic placeholder pad (don't publish with it)")
        bed = music.build_bed([music.placeholder_pad(work / "placeholder_pad.wav")],
                              total, work / "music_bed.wav", seed)
    final = add_music(narrated, bed, work / f"{slugify(script['title'])}.mp4")
    print(f"Done: {final}  ({total / 60:.1f} min)")
    return final


def keep_brief(brief: str, work: Path) -> None:
    """Copy the brief into the build folder, so metadata (and a later run report) find it from
    `work` alone. Called once get_script has accepted it: the copy is the brief the script was
    written from; and at seeding too (P3-4), right after the stamp, so a crash mid-copy leaves a
    kept brief for the rerun without --brief."""
    dest = work / config.BRIEF_FILE
    if not dest.exists() or dest.read_text(encoding="utf-8") != brief:
        _write_text(dest, brief)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("topic")
    ap.add_argument("--minutes", type=float, default=config.DEFAULT_MINUTES)
    ap.add_argument("--script-only", action="store_true", help="stop after the reviewed script")
    ap.add_argument("--no-ai-video", action="store_true", help="skip Veo; use stills instead")
    ap.add_argument("--skip-critic", action="store_true")
    ap.add_argument("--estimate", action="store_true", help="print the projected cost and exit; calls nothing")
    ap.add_argument("--retry-failed", action="store_true",
                    help="ask Veo / the image model again for scenes whose generation failed on an earlier run")
    ap.add_argument("--brief", type=Path, metavar="FILE",
                    help="markdown research brief (verified facts, hedging rules, ## Sources) for films "
                         "about recent research; the script agents rely on it and its Sources go "
                         "into the YouTube description. Once the script exists, a different brief "
                         "(or none) is refused; a rerun may leave it out and uses the kept one. "
                         "An outline.json (and chapter_NN.json files) next to the brief is a "
                         "reviewed script: a run with --brief copies in whichever of its files "
                         "the build lacks. The build remembers that folder (build/<topic>/seed): a rerun "
                         "without --brief copies in a deleted chapter from it again, while the folder's "
                         "brief.key matches the build's brief")
    ap.add_argument("--style", choices=sorted(config.STYLES),
                    help="how the pictures look: calm = dark, soft, cinematic; flat = flat vector shapes in "
                         "rich colour on deep navy. Voice, pacing and music stay calm. Default: the style "
                         "the build was started in, else VISUAL_STYLE from .env, else calm. Each style makes "
                         "its own pictures, so switching a build's style reuses none")
    args = ap.parse_args()

    work = Path(config.BUILD_DIR) / slugify(args.topic)
    try:
        recorded = _recorded_style(work)
    except ValueError as e:
        ap.error(str(e))
    # Before anything makes a prompt or a cache path (the estimate included).
    config.STYLE, source = pick_style(args.style, recorded, config.STYLE)
    print(f"• style: {config.STYLE} ({source})")
    if recorded not in (None, config.STYLE):
        print(f"• style: this build is recorded as {recorded}; this run makes its pictures in {config.STYLE} "
              f"instead (any already made in {recorded} are kept)")
    brief, seed, seed_chapters, recorded_seed = None, None, [], False
    if args.brief is not None:
        try:
            brief = args.brief.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeDecodeError) as e:
            ap.error(f"--brief: cannot read {args.brief} ({type(e).__name__})")
        if not brief.strip():
            ap.error(f"--brief: {args.brief} is empty")
        seed = _seed_folder(args.brief, work)
        if seed is not None:
            try:
                seed_chapters = _load_seed(seed, brief)
            except ValueError as e:
                ap.error(f"--brief: {e}")
        elif args.brief.resolve() == (work / config.BRIEF_FILE).resolve() and kept_brief(work) is not None:
            # the kept copy (what a refusal suggests): the folder it was seeded from, as on a rerun without it
            seed, seed_chapters = _recorded_seed(work, brief)
            recorded_seed = seed is not None
        # (any other --brief is never mixed with the record: D23's refusal comes first)
    elif _script_started(work):
        brief = kept_brief(work)  # a rerun: the script goes on with the brief it was written with
        if brief is not None:
            print(f"• brief: using the one this script was written with ({work / config.BRIEF_FILE})")
            seed, seed_chapters = _recorded_seed(work, brief)  # P3-4: a deleted chapter comes back from its folder
            recorded_seed = seed is not None

    allow_veo = ai_video.available() and not args.no_ai_video
    if args.estimate:
        conflict = _brief_conflict(work, brief)
        if conflict:
            print(f"this run would be refused: {conflict}")
        if seed is not None:
            copy, note = _plan_seed(seed, seed_chapters, work, advice=not recorded_seed)
            if copy:
                print(f"• script: would seed {', '.join(p.name for p in copy)} from {seed}")
            note = _shown_note(note, copy, seed, work, recorded_seed)
            if note:
                print(f"• script: {note}")
        print_estimate(args.topic, estimate_cost(args.minutes, work, allow_veo, args.skip_critic,
                                                 retry_failed=args.retry_failed, brief=brief, seed=seed),
                       estimate_cost(args.minutes, work, allow_veo, args.skip_critic, worst_case=True,
                                     retry_failed=args.retry_failed, brief=brief, seed=seed), config.STYLE)
        return
    work.mkdir(parents=True, exist_ok=True)
    # P2-3: build/<slug>/run_report.md is written however this block ends
    with run_report.recording(work, args.topic, args.brief, brief, allow_veo) as run:
        run_report.stage("script")
        if _brief_conflict(work, brief) is None:  # a refused run changes nothing; get_script says why
            _record_style(work)  # first, like brief.key: a run that stops in the script stage keeps it
        if seed is not None:
            seed_script(seed, seed_chapters, work, brief, recorded=recorded_seed)

        script = run.script = get_script(args.topic, args.minutes, work, args.skip_critic, brief)
        if brief is not None:
            keep_brief(brief, work)
        if args.script_only:
            run.outcome = "script only"
            print(f"Script saved to {work / 'script.json'}. Edit it, then rerun without --script-only.")
            return

        final = render_film(script, work, allow_veo, args.topic, args.retry_failed)
        run_report.stage("metadata")
        metadata.write_metadata(work)  # metadata.json + subtitles.srt for YouTube
        print(f"• YouTube metadata: {work / 'metadata.json'}, {work / 'subtitles.srt'}")
        run_report.stage("thumbnail")  # after metadata: the title is its
        run.thumbnail = thumbnail.make(work, Failures(failure_log.path(work), retry=args.retry_failed))
        run_report.stage("QA")
        run.qa = qa.run_qa(final)  # writes qa.json next to the film
        qa.print_report(run.qa)
    if not run.qa["passed"]:  # outside the block: SystemExit would be written up as a stop
        sys.exit(1)


if __name__ == "__main__":
    main()
