"""The pilot tool (P3-7): make one chapter of a reviewed script into a small film of its own, then
hand what it paid for to the full film.

    python scripts/make_pilot.py make OUT --from topics/NAME --chapters 1 [--topic T] [--film-topic F] [--style S]
    python scripts/make_pilot.py copy PILOT FILM [--from topics/NAME]

`make` writes a seed folder (the chapters chosen, a one-chapter outline, brief.md and its brief.key)
outside topics/ and build/, and prints the pilot's command and the copy command. The pilot then
makes its pictures and narration under build/<pilot>/ like any film.

`copy` puts the pilot's paid audio/ and visuals/ into build/<film>/ so the full film buys only the
rest (D9: every file is named by the hash of what made it; only the folder differs). Nothing is ever
replaced: a file is written under a .partial- name and hard-linked into place, which fails if the
name is taken. It refuses a pilot that has not finished, because the review logs say a picture is
decided, and a log copied without its picture, or before it was done, would stop the film from
making that picture. Nothing is written on a refusal. No paid call is made by either command."""
import argparse
import json
import os
import re
import secrets
import shutil
import stat
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import config  # noqa: E402
import pipeline  # noqa: E402
from agents import image_agent  # noqa: E402
from utils import atomic_output, brief_key, kept_brief, load_json, slugify  # noqa: E402

TOPICS = ROOT / "topics"  # the reviewed scripts; module-level so a test can point it elsewhere
PARTIAL = ".partial-"     # the name of a file being made (utils.atomic_output uses it too)
LOG_KINDS = ("still", "ai_video")  # the scenes whose pictures have a review log


class Refusal(Exception):
    """Something the tool will not do; the message says what and what to do about it."""


class SourceGone(Exception):
    """A file to copy vanished, or is no longer a plain file, between listing it and reading it."""


def q(text: str) -> str:
    """A double-quoted shell word: the characters that stay special inside double quotes are escaped."""
    return '"' + re.sub(r'(["\\$`])', r"\\\1", text) + '"'


# --- paths -------------------------------------------------------------------

def inside(path: Path, folder: Path) -> bool:
    """True if `path`, with every symlink followed (a dangling one too), is `folder` or below it."""
    real, base = Path(os.path.realpath(path)), Path(os.path.realpath(folder))
    return real == base or base in real.parents


def is_plain(path: Path) -> bool:
    """A regular file that is not a symlink."""
    try:
        return stat.S_ISREG(os.lstat(path).st_mode)
    except OSError:
        return False


def not_a_folder(path: Path) -> bool:
    """Something is at `path` (a dangling link included) that is not a folder."""
    return os.path.lexists(path) and not path.is_dir()


def list_folder(folder: Path) -> list[Path]:
    """The folder's entries as they are now. The one place the walk lists a folder."""
    try:
        return sorted(folder.iterdir())
    except FileNotFoundError:
        return []


def read_plain(path: Path) -> bytes:
    """The bytes of a plain file, read once, never through a symlink."""
    with open_plain(path) as f:
        return f.read()


def open_plain(path: Path):
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as e:
        raise SourceGone(f"{path.name}: {type(e).__name__}") from e
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise SourceGone(f"{path.name}: no longer a plain file")
    return os.fdopen(fd, "rb")


def put_new(dest: Path, source: Path | None = None, data: bytes | None = None) -> bool:
    """Make `dest` from `source`'s bytes (or `data`) unless the name is taken: a .partial- file is
    written, then hard-linked into place. os.link fails if `dest` exists, so nothing is ever
    replaced, and `dest` never holds half a file. True if made, False if the name was taken."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f"{dest.stem}{PARTIAL}{os.getpid()}-{secrets.token_hex(4)}{dest.suffix}")
    try:
        with open(tmp, "xb") as out:
            if data is not None:
                out.write(data)
            else:
                with open_plain(source) as f:
                    shutil.copyfileobj(f, out)
        try:
            os.link(tmp, dest)
        except FileExistsError:
            return False
        return True
    finally:
        tmp.unlink(missing_ok=True)


def hard_link_problem(folder: Path) -> str | None:
    """Why this disk can't make hard links where `folder` is (or would be), or None. Probes in the
    folder, or its nearest existing parent, with names unique to this run, and removes only those."""
    where = folder
    while not where.is_dir():
        if where.parent == where:
            break
        where = where.parent
    name = f".link-probe-{os.getpid()}-{secrets.token_hex(4)}"
    first, second = where / name, where / f"{name}.link"
    made = []
    try:
        with open(first, "xb"):
            made.append(first)
        os.link(first, second)
        made.append(second)
        return None
    except OSError as e:
        return f"{type(e).__name__}: {e} (probed in {where})"
    finally:
        for path in made:
            path.unlink(missing_ok=True)


def read_text_file(path: Path) -> str | None:
    """A UTF-8 file's text, or None if it is missing, a folder, a broken link or not UTF-8."""
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def source_label(src: Path) -> str:
    """How a command names the source: topics/<name> when it is one (the commands run from the project
    root), else its full path."""
    src = Path(os.path.abspath(src))
    return f"topics/{src.name}" if src.parent == Path(os.path.abspath(TOPICS)) else str(src)


# --- make --------------------------------------------------------------------

def parse_chapters(text: str) -> list[int]:
    try:
        numbers = [int(part) for part in text.split(",")]
    except ValueError:
        raise Refusal(f"--chapters {text!r}: give chapter numbers like 1 or 1,2") from None
    if not numbers:
        raise Refusal("--chapters: give at least one chapter")
    return sorted(set(numbers))


def check_out(out: Path) -> Path:
    """OUT's resolved path, if the tool may write there."""
    where = Path(os.path.abspath(out))
    if os.path.islink(where) and not where.exists():
        raise Refusal(f"{out} is a dangling link; remove it or pick another folder")
    for name, folder in (("topics/", TOPICS), ("build/", Path(config.BUILD_DIR))):
        if inside(where, folder) or where.is_relative_to(Path(os.path.abspath(folder))):  # through links, or as typed
            raise Refusal(f"{out} is inside {name} ({folder}, or a link into it): a pilot's seed folder belongs "
                          "outside topics/ and build/, so that it is never taken for a topic or a build")
    real = Path(os.path.realpath(where))
    if real.exists():
        if not real.is_dir():
            raise Refusal(f"{out} is a file; pick a folder that doesn't exist or is empty")
        if any(real.iterdir()):
            raise Refusal(f"{out} is not empty; pick another folder, or empty it if an earlier make_pilot.py "
                          "run stopped partway (a folder without brief.key is refused by the pipeline)")
    return real


def make(out_arg: str, src_arg: str, chapters_arg: str, topic: str | None, film_topic: str | None,
         style: str) -> None:
    out = check_out(Path(out_arg))
    chosen = parse_chapters(chapters_arg)
    src = Path(src_arg)
    try:
        brief_bytes = (src / config.BRIEF_FILE).read_bytes()
        brief = brief_bytes.decode("utf-8-sig")
    except (OSError, UnicodeDecodeError) as e:
        raise Refusal(f"{src / config.BRIEF_FILE} cannot be read as UTF-8 text ({type(e).__name__})") from None
    if not brief.strip():
        raise Refusal(f"{src / config.BRIEF_FILE} is empty")
    try:
        files = pipeline._load_seed(src, brief)  # the pipeline's own check, so its refusals are ours
    except ValueError as e:
        raise Refusal(str(e)) from None
    outline = load_json(src / "outline.json")
    listed = [c["number"] for c in outline["chapters"]]
    for n in chosen:
        if n not in listed:
            raise Refusal(f"chapter {n} is not in {src / 'outline.json'} (it lists {', '.join(map(str, listed))})")
        if src / f"chapter_{n:02d}.json" not in files:
            raise Refusal(f"chapter {n} is in the outline but {src / f'chapter_{n:02d}.json'} is missing")

    out.mkdir(parents=True, exist_ok=True)
    # The order matters: brief.key goes last, and the pipeline refuses a folder without it, so a stop
    # anywhere leaves a folder that cannot be mistaken for a finished seed.
    for n in chosen:
        name = f"chapter_{n:02d}.json"
        with atomic_output(out / name) as tmp:
            tmp.write_bytes((src / name).read_bytes())
    kept_outline = {**outline, "chapters": [c for c in outline["chapters"] if c["number"] in chosen]}
    with atomic_output(out / "outline.json") as tmp:
        tmp.write_text(json.dumps(kept_outline, indent=2, ensure_ascii=False), encoding="utf-8")  # as save_json
    with atomic_output(out / config.BRIEF_FILE) as tmp:
        tmp.write_bytes(brief_bytes)
    kept = (out / config.BRIEF_FILE).read_text(encoding="utf-8-sig")
    with atomic_output(out / config.BRIEF_KEY_FILE) as tmp:
        tmp.write_text(brief_key(kept), encoding="utf-8")
    try:
        made = pipeline._load_seed(out, kept)
    except ValueError as e:
        raise Refusal(f"the folder just written does not pass the pipeline's own check: {e}") from None
    if len(made) != len(chosen):
        raise Refusal(f"wrote {len(made)} chapter files in {out}, expected {len(chosen)}")

    pilot_topic = topic or f"pilot {Path(os.path.abspath(src)).name}"
    print(f"Wrote {out}: chapter{'s' if len(chosen) > 1 else ''} {', '.join(map(str, chosen))} of "
          f"{source_label(src)} ({len(made)} chapter file{'s' if len(made) > 1 else ''}, outline.json, "
          f"{config.BRIEF_FILE}, {config.BRIEF_KEY_FILE}).")
    print(f"Run these from {ROOT}.\n")
    print("1. The pilot (the estimate first: add --estimate; it calls nothing):")
    print(f"   python pipeline.py {q(pilot_topic)} --brief {q(str(out / config.BRIEF_FILE))} "
          f"--style {style} --no-ai-video\n")
    print("2. When the pilot has finished, hand its paid audio and pictures to the film:")
    print(f"   python scripts/make_pilot.py copy {q(pilot_topic)} {q(film_topic or '<film topic>')} "
          f"--from {q(source_label(src))}")


# --- copy: the checks ---------------------------------------------------------

def pilot_style(pilot: Path) -> str:
    """The style the pilot's pictures are made in, from its record; refuses if there is none to trust."""
    path = pilot / config.STYLE_FILE
    text = read_text_file(path)
    name = text.strip() if text is not None else None
    if name in config.STYLES:
        return name
    why = ("is missing, a folder, a broken link or not UTF-8 text" if text is None
           else "is empty" if not name else f"says {name!r}, which is not a style ({', '.join(sorted(config.STYLES))})")
    raise Refusal(f"{path} {why}, so the pilot's pictures can't be matched to a style; rerun the pilot once with its "
                  f"--style (it buys nothing already made), or write the style name into {path}")


def read_json(path: Path):
    """A JSON file's content, or None if it is missing or unreadable."""
    try:
        return json.loads(path.read_bytes())
    except (OSError, ValueError):
        return None


def current_logs(visuals: Path, scenes, style: str) -> set[str] | None:
    """The names of the review logs the pilot's script reads now, in the pilot's recorded style
    (config.STYLE is part of every log's name); None when script.json can't say, and then every log
    counts. The global is restored whatever happens."""
    if not isinstance(scenes, list):
        return None
    saved = config.STYLE
    try:
        config.STYLE = style
        return {image_agent.review_path(s, visuals).name for s in scenes if s["visual_type"] in LOG_KINDS}
    except (KeyError, TypeError):
        return None
    finally:
        config.STYLE = saved


def parse_log(data: bytes) -> tuple[dict | None, str | None]:
    """(the log, None) if the bytes are a review log that is done, else (None, why not)."""
    try:
        log = json.loads(data)
    except (ValueError, UnicodeDecodeError):
        return None, "it is not readable JSON"
    if not isinstance(log, dict):
        return None, "it is not a JSON object"
    if log.get("done") is not True:
        return None, "it is not finished"
    return log, None


def review_problem(data: bytes, visuals: Path) -> str | None:
    """Why this review log, as read, may not be carried over, or None: it must be a JSON object that is
    done, and the picture it accepted must be a plain file (not a link) in the pilot's visuals."""
    log, why = parse_log(data)
    if log is None:
        return why
    accepted = log.get("accepted")
    if accepted is None:
        return None  # rejected for good: the film falls back to Manim, as the pilot did
    if not isinstance(accepted, str) or not accepted or accepted != Path(accepted).name or accepted in (".", ".."):
        return f"it accepts {accepted!r}, which is not a picture's file name"
    if not is_plain(visuals / accepted):
        return f"its picture {accepted} is missing or not a plain file in the pilot's visuals"
    return None


def unfinished(pilot: Path, scenes, mine: set[str] | None) -> list[str]:
    """Why the pilot has not finished, or []. render_film writes manifest.json only after every
    picture is made, so it must list every scene of script.json with its files in place; and no
    review log the current script reads may be unfinished."""
    problems = []
    manifest = read_json(pilot / "manifest.json")
    entries = manifest.get("scenes") if isinstance(manifest, dict) else None
    if not is_plain(pilot / "manifest.json"):
        problems.append("manifest.json is missing")
    elif not isinstance(entries, list):
        problems.append("manifest.json is not readable")
    elif not isinstance(scenes, list):
        problems.append("script.json is not readable, so manifest.json can't be checked against it")
    else:
        by_id = {e.get("id"): e for e in entries if isinstance(e, dict)}
        wanted = [s.get("id") for s in scenes if isinstance(s, dict)]
        absent = [i for i in wanted if i not in by_id]
        if absent:
            problems.append(f"manifest.json does not list scene {', '.join(map(str, absent))} of script.json")
        for i in wanted:
            for key in ("audio", "visual"):
                name = by_id.get(i, {}).get(key)
                if i in by_id and not (isinstance(name, str) and name and not os.path.isabs(name)
                                       and is_plain(pilot / name) and inside(pilot / name, pilot)):
                    problems.append(f"scene {i}: its {key} file {name!r} is not a plain file in the pilot")
    for path in list_folder(pilot / "visuals"):
        if not (path.name.startswith("review_") and path.name.endswith(".json")) or PARTIAL in path.name:
            continue
        if mine is not None and path.name not in mine:
            continue  # not read by the current script: the film decides that scene itself
        try:
            data = read_plain(path)
        except SourceGone:
            problems.append(f"{path.name} is not a plain file")
            continue
        why = parse_log(data)[1]  # its picture is the per-file check's, at the moment of the copy
        if why:
            problems.append(f"review log {path.name}: {why}")
    return problems


def find_source(src_arg: str | None, pilot: Path) -> Path:
    """The folder of the reviewed script the film is made from: --from, else the one topics/*/brief.md
    that is the pilot's kept brief, by content."""
    if src_arg:
        folder = TOPICS / src_arg if os.sep not in src_arg and (TOPICS / src_arg / config.BRIEF_FILE).exists() \
            else Path(src_arg)
        if not (folder / config.BRIEF_FILE).is_file():
            raise Refusal(f"--from {src_arg}: there is no {config.BRIEF_FILE} in {folder}")
        return folder
    kept = kept_brief(pilot)
    if kept is None:
        raise Refusal(f"{pilot} holds no brief the pilot's script was made with, so the film's source can't be "
                      "found by content: say it with --from topics/NAME")
    matches = []
    for brief in sorted(TOPICS.glob(f"*/{config.BRIEF_FILE}")):
        try:
            text = brief.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeDecodeError):
            raise Refusal(f"{brief} is not UTF-8 text, so it can't be compared with the pilot's brief: "
                          "say the source with --from topics/NAME") from None
        if text == kept:
            matches.append(brief.parent)
    if len(matches) != 1:
        raise Refusal((f"no brief in {TOPICS}/*/ is the pilot's brief" if not matches else
                       f"{len(matches)} topics hold the pilot's brief ({', '.join(m.name for m in matches)})")
                      + ": say the source with --from topics/NAME")
    return matches[0]


# --- copy: the walk ------------------------------------------------------------

@dataclass
class Walk:
    copied: list[str] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)
    incomplete: list[tuple[str, str]] = field(default_factory=list)


def is_review_log(name: str) -> bool:
    return name.startswith("review_") and name.endswith(".json")


def walk(pilot: Path, film: Path, mine: set[str] | None, result: Walk) -> None:
    for sub in ("audio", "visuals"):
        src = pilot / sub
        if os.path.islink(src):
            result.skipped.append((sub, "a symlinked folder is not followed"))
        elif src.is_dir():
            walk_folder(src, film / sub, sub, pilot / "visuals", mine, result)


def walk_folder(src: Path, dst: Path, rel: str, visuals: Path, mine: set[str] | None, result: Walk) -> None:
    logs = []
    for path in list_folder(src):
        name, shown = path.name, f"{rel}/{path.name}"
        try:
            mode = os.lstat(path).st_mode
        except FileNotFoundError:
            result.skipped.append((shown, "gone from the pilot after the folder was listed"))
            continue
        if PARTIAL in name:
            result.skipped.append((shown, "a file still being made (.partial-)"))
        elif stat.S_ISLNK(mode):
            result.skipped.append((shown, "a symlink"))
        elif stat.S_ISDIR(mode):
            if shown == "visuals/manim_media":
                result.skipped.append((shown, "Manim's scratch folder, not paid output"))
            else:
                walk_folder(path, dst / name, shown, visuals, mine, result)
        elif not stat.S_ISREG(mode):
            result.skipped.append((shown, "not a regular file"))
        elif rel == "visuals" and is_review_log(name):
            logs.append(path)
        else:
            copy_file(path, dst / name, shown, result)
    for path in logs:  # last in their folder: a log vouches for a picture that must be there first
        copy_log(path, dst / path.name, f"{rel}/{path.name}", visuals, mine, result)


def copy_file(src: Path, dst: Path, shown: str, result: Walk) -> None:
    """Copy one file unless the name is taken, and say which happened."""
    if os.path.lexists(dst):
        result.skipped.append((shown, "already in the film"))
        return
    try:
        made = put_new(dst, source=src)
    except SourceGone as e:
        result.skipped.append((shown, f"gone from the pilot while copying ({e})"))
        return
    if made:
        result.copied.append(shown)
    else:
        result.skipped.append((shown, "already in the film"))


def copy_log(src: Path, dst: Path, shown: str, visuals: Path, mine: set[str] | None, result: Walk) -> None:
    """A review log goes in only with its picture. It is read once, as bytes; those bytes are what is
    judged and what is written, so a log that finishes while we look is either all seen or not at all."""
    if mine is not None and src.name not in mine:
        result.skipped.append((shown, "not read by the pilot's current script: the film decides that scene itself"))
        return
    if os.path.lexists(dst):
        result.skipped.append((shown, "already in the film"))
        return
    try:
        data = read_plain(src)
    except SourceGone as e:
        result.skipped.append((shown, f"gone from the pilot while copying ({e})"))
        return
    why = review_problem(data, visuals)
    if why is None:
        accepted = json.loads(data).get("accepted")
        if accepted is not None:
            picture = dst.parent / accepted
            if not os.path.lexists(picture):  # not made by the walk (it finished after the folder was listed)
                copy_file(visuals / accepted, picture, f"visuals/{accepted}", result)
            if not is_plain(picture):
                why = f"its picture {accepted} is not in the film as a plain file"
    if why:
        result.incomplete.append((shown, why))
        return
    if put_new(dst, data=data):
        result.copied.append(shown)
    else:
        result.skipped.append((shown, "already in the film"))


# --- copy ---------------------------------------------------------------------

def copy(pilot_arg: str, film_arg: str, src_arg: str | None) -> int:
    pilot_slug, film_slug = slugify(pilot_arg), slugify(film_arg)
    if not pilot_slug or not film_slug:
        raise Refusal(f"{'PILOT' if not pilot_slug else 'FILM'} gives an empty build name: give the topic the "
                      "pilot (or film) was run with")
    if pilot_slug == film_slug:
        raise Refusal(f"the pilot and the film are the same build ({pilot_slug}): copying a build into itself "
                      "does nothing; give the film's own topic")
    pilot, film = Path(config.BUILD_DIR) / pilot_slug, Path(config.BUILD_DIR) / film_slug
    if not pilot.is_dir():
        raise Refusal(f"there is no pilot build at {pilot}: run the pilot first (python scripts/make_pilot.py make "
                      "prints its command)")

    style = pilot_style(pilot)
    manifest = read_json(pilot / "manifest.json")
    if isinstance(manifest, dict) and isinstance(manifest.get("style"), str) and manifest["style"] != style:
        raise Refusal(f"{pilot / 'manifest.json'} says the pilot was finished in {manifest['style']}, but "
                      f"{pilot / config.STYLE_FILE} says {style}; rerun the pilot once with its --style (it buys nothing "
                      f"already made), or write the style name into {pilot / config.STYLE_FILE}, or finish the pilot "
                      f"in {style} (rerun with --style {style})")

    film_style = film / config.STYLE_FILE
    if os.path.lexists(film_style):
        recorded = read_text_file(film_style)
        recorded = recorded.strip() if recorded is not None else None
        if recorded not in config.STYLES:
            raise Refusal(f"{film_style} is not a style record ({'unreadable' if recorded is None else repr(recorded)}): "
                          "fix it or delete it, then copy again")
        if recorded != style:
            raise Refusal(f"the film is recorded in {recorded} and the pilot in {style}: to make the film in "
                          f"{style}, delete {film_style} (its {recorded} pictures stay on disk) and copy again")
    for folder in (film, film / "audio", film / "visuals"):
        if not_a_folder(folder):
            raise Refusal(f"{folder} is not a folder (a file or a broken link is in its place)")
        if inside(folder, TOPICS):
            raise Refusal(f"{folder} is inside topics/ (or a link into it); a build's files never go there")

    script = read_json(pilot / "script.json")
    scenes = script.get("scenes") if isinstance(script, dict) else None
    mine = current_logs(pilot / "visuals", scenes, style)
    problems = unfinished(pilot, scenes, mine)
    if problems:
        raise Refusal(f"the pilot has not finished; let it finish (or rerun its command: python pipeline.py "
                      f"{q(pilot_arg)} --style {style} --no-ai-video), then copy\n"
                      + "\n".join(f"  - {p}" for p in problems))
    source = find_source(src_arg, pilot)
    made_with = kept_brief(pilot)
    if src_arg and made_with is not None and read_text_file(source / config.BRIEF_FILE) != made_with:
        print(f"note: {source / config.BRIEF_FILE} is not the brief the pilot was made with; the film's script "
              "may differ from the pilot's")
    problem = hard_link_problem(film)
    if problem:
        raise Refusal(f"this disk cannot make hard links ({problem}); copy needs them so that no file is ever "
                      "replaced, and has no fallback that could")

    # Every refusal is behind us. The style record first: a copy that stops later has still said which
    # style these pictures are in. put_new never replaces, so a record that appeared meanwhile wins.
    film.mkdir(parents=True, exist_ok=True)
    if put_new(film_style, data=style.encode("utf-8")):
        print(f"• style: recorded {style} in {film_style}")
    else:
        now = (read_text_file(film_style) or "").strip()
        if now != style:
            raise Refusal(f"{film_style} appeared meanwhile and says {now!r}, not {style}: to make the film in "
                          f"{style}, delete it and copy again (nothing else was written)")
        print(f"• style: {film_style} already says {style}")

    result = Walk()
    walk(pilot, film, mine, result)
    for shown in result.copied:
        print(f"  copied  {shown}")
    for shown, why in result.skipped:
        print(f"  skipped {shown}: {why}")
    print(f"{len(result.copied)} file{'s' if len(result.copied) != 1 else ''} copied, "
          f"{len(result.skipped)} skipped.")
    print("failures.json is not carried over (a pilot failure is asked again).")
    veo = any(p.name.startswith("ai_") and p.suffix == ".mp4" for p in list_folder(pilot / "visuals")) or (
        isinstance(manifest, dict) and any(isinstance(e, dict) and e.get("kind") == "ai"
                                           for e in manifest.get("scenes") or []))
    if veo:
        print("VEO_NOTE: the pilot has Veo clips (ai_*.mp4 / scenes of kind ai). The film command below carries "
              "--no-ai-video, so it will not use them; they stay in the pilot's build and cost nothing more.")
    if result.incomplete:
        print("NOT COMPLETE: these review logs were not carried over, because the pilot's picture for each is not "
              "finished or not there (a log copied without its picture would make the film skip that scene):",
              file=sys.stderr)
        for shown, why in result.incomplete:
            print(f"  {shown}: {why}", file=sys.stderr)
        print("Let the pilot finish, then run the same copy command again; it only adds what is missing.",
              file=sys.stderr)
        return 1
    print("\nNow the film (the estimate first: add --estimate):")
    print(f"   python pipeline.py {q(film_arg)} --brief {q(source_label(source) + '/' + config.BRIEF_FILE)} "
          f"--style {style} --no-ai-video")
    return 0


# --- command line ---------------------------------------------------------------

def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="make_pilot.py", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="command", required=True)
    m = sub.add_parser("make", help="write a seed folder for a pilot: chosen chapters of a reviewed script")
    m.add_argument("out", metavar="OUT", help="the new folder, outside topics/ and build/; missing or empty")
    m.add_argument("--from", dest="src", required=True, metavar="SRC",
                   help="the reviewed script's folder, e.g. topics/ai-global-workspace")
    m.add_argument("--chapters", required=True, metavar="N[,N]", help="chapter numbers to keep, e.g. 1 or 1,2")
    m.add_argument("--topic", help="the pilot's topic string (its build folder's name); default: pilot <SRC's name>")
    m.add_argument("--film-topic", help="the full film's topic string, for the copy command")
    m.add_argument("--style", choices=sorted(config.STYLES), default="flat", help="the pilot's style (default: flat)")
    c = sub.add_parser("copy", help="give the film the pilot's paid audio/ and visuals/ (never replaces a file)")
    c.add_argument("pilot", metavar="PILOT", help="the pilot's topic string, as run")
    c.add_argument("film", metavar="FILM", help="the full film's topic string, as it will be run")
    c.add_argument("--from", dest="src", metavar="SRC",
                   help="the reviewed script's folder (default: the topics/ folder whose brief.md is the pilot's)")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "make":
            make(args.out, args.src, args.chapters, args.topic, args.film_topic, args.style)
            return 0
        return copy(args.pilot, args.film, args.src)
    except Refusal as e:
        print(f"make_pilot.py: {e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("make_pilot.py: interrupted; no file is half-written; run the same command again to finish",
              file=sys.stderr)
        return 130
    except OSError as e:
        print(f"make_pilot.py: stopped by {type(e).__name__}: {e}. Every file already made is whole; fix this and "
              "run the same command again", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
