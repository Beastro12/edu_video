"""YouTube metadata for a finished film, timed from what was actually rendered:
metadata.json (title, description with chapter timestamps) and subtitles.srt.

Chapters follow YouTube's rules: the first at 00:00, each at least 10 s long, and at least
three of them or YouTube shows none (the list is then left out of the description)."""
import re
from pathlib import Path

import config
from assembly import xfade_offsets
from utils import duration, kept_brief, load_json, save_json, video_duration

MIN_CHAPTER_S = 10
MIN_CHAPTERS = 3
CUE_CHARS = 84        # one cue: at most two lines...
LINE_CHARS = 42       # ...of about this length
TITLE_MAX = 100       # YouTube's limits
DESCRIPTION_MAX_BYTES = 5000


def scene_starts(work: Path, manifest: list[dict]) -> tuple[list[float], float]:
    """When each scene starts in the film, and the film's length: the same arithmetic the
    crossfade chain used on the same clips."""
    durs = [video_duration(work / m["clip"]) for m in manifest]
    starts = [0.0] + [float(o) for o in xfade_offsets(durs)]
    return starts, starts[-1] + durs[-1]


def timestamp(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60:02d}:{s % 60:02d}"


def chapter_marks(scenes: list[dict], starts: list[float], titles: dict[int, str], total: float) -> list[dict]:
    marks = []
    for scene, start in zip(scenes, starts, strict=True):
        n = scene.get("chapter", 1)
        if not marks or marks[-1]["chapter"] != n:
            marks.append({"chapter": n, "seconds": start, "title": titles.get(n, f"Part {n}")})
    marks[0]["seconds"] = 0.0
    while len(marks) > 1:  # merge any chapter under MIN_CHAPTER_S into its neighbour
        ends = [m["seconds"] for m in marks[1:]] + [total]
        short = next((i for i, m in enumerate(marks) if ends[i] - m["seconds"] < MIN_CHAPTER_S), None)
        if short is None:
            break
        if short == 0:  # the opening is too short: it keeps 00:00 and takes the next chapter's title
            marks[0]["title"] = marks[1]["title"]
            del marks[1]
        else:  # its time goes to the chapter before
            del marks[short]
    return [{"start": timestamp(m["seconds"]), "seconds": round(m["seconds"], 3), "title": m["title"]}
            for m in marks]


def cue_texts(narration: str) -> list[str]:
    """Whole sentences packed into cues of at most CUE_CHARS (so "Dr." or "…" can't make a
    flash of a cue); a sentence longer than that is cut at word boundaries."""
    pieces = []
    for sentence in re.split(r"(?<=[.!?])\s+", narration.strip()):
        line = ""
        for word in sentence.split():
            if line and len(line) + 1 + len(word) > CUE_CHARS:
                pieces.append(line)
                line = word
            else:
                line = f"{line} {word}".strip()
        if line:
            pieces.append(line)
    cues = []
    for piece in pieces:
        if cues and len(cues[-1]) + 1 + len(piece) <= CUE_CHARS:
            cues[-1] += " " + piece
        else:
            cues.append(piece)
    return cues


def two_lines(text: str) -> str:
    """Break a cue at the space nearest its middle when it's longer than one line."""
    if len(text) <= LINE_CHARS or " " not in text:
        return text
    spaces = [i for i, ch in enumerate(text) if ch == " "]
    cut = min(spaces, key=lambda i: abs(i - len(text) / 2))
    return text[:cut] + "\n" + text[cut + 1:]


def youtube_safe(text: str) -> str:
    return text.replace("<", "").replace(">", "")


def brief_sources(work: Path) -> str:
    """The Sources of the research brief kept in `work` (P3-1), as parse_sources gives them.
    Only the brief the script was written with counts: brief.md must match the stamp
    (brief.key) the script stage left, or a leftover from an abandoned run would put sources
    in the description of a film that never used them. "" when there is no brief, no sources
    or no match."""
    if not (work / config.BRIEF_FILE).exists():
        return ""
    brief = kept_brief(work)
    if brief is None:
        print(f"    {config.BRIEF_FILE} isn't the brief this script was written with; "
              "leaving Sources out of the description")
        return ""
    return parse_sources(brief)


NOT_AN_ENTRY = re.compile(r"#{3,}(\s|$)"            # a deeper heading
                          r"|(-\s*){3,}$|(\*\s*){3,}$|(_\s*){3,}$"   # a horizontal rule
                          r"|```|~~~"                # a code fence
                          r"|\|")                    # a table row


def parse_sources(text: str) -> str:
    """The `## Sources` section of a brief, one "- text" line per entry. A description is plain
    text, so markdown links become "text: url" and list markers go; the section ends at the next
    `#` or `##` heading; deeper headings, horizontal rules, code fences, table rows and HTML
    comments are skipped; an indented line that isn't a bullet continues the entry before it.
    "" without a Sources section."""
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    entries, in_sources = [], False
    for line in text.splitlines():
        if not in_sources:
            in_sources = line.rstrip() == "## Sources"
        elif re.match(r"#{1,2}(\s|$)", line):
            break
        elif line.strip() and not NOT_AN_ENTRY.match(line.strip()):
            item = re.sub(r"^(?:[-*+]|\d{1,3}[.)])\s+", "", line.strip())
            if line[0].isspace() and item == line.strip() and entries:
                entries[-1] += " " + item
            else:
                entries.append(item)
    return "\n".join("- " + re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", r"\1: \2", e) for e in entries)


def srt_time(seconds: float) -> str:
    ms = round(seconds * 1000)
    return f"{ms // 3_600_000:02d}:{ms // 60_000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def subtitles(work: Path, scenes: list[dict], manifest: list[dict], starts: list[float]) -> str:
    """Each scene's narration spans its measured audio after the lead-in; cues share that span
    in proportion to their length."""
    blocks = []
    for scene, m, start in zip(scenes, manifest, starts, strict=True):
        begin = start + config.LEAD_IN_S
        span = duration(work / m["audio"])
        texts = cue_texts(scene["narration"])
        total_chars = sum(len(t) for t in texts)
        t = begin
        for text in texts:
            end = t + span * len(text) / total_chars
            blocks.append(f"{len(blocks) + 1}\n{srt_time(t)} --> {srt_time(end)}\n{two_lines(text)}\n")
            t = end
    return "\n".join(blocks)


def write_metadata(work: Path) -> dict:
    """Writes metadata.json and subtitles.srt into the build folder; returns the metadata."""
    script = load_json(work / "script.json")
    manifest = load_json(work / "manifest.json")["scenes"]
    outline_path = work / "outline.json"
    titles = {c["number"]: c["title"] for c in load_json(outline_path)["chapters"]} if outline_path.exists() else {}
    starts, total = scene_starts(work, manifest)
    chapters = [{**c, "title": youtube_safe(c["title"])}
                for c in chapter_marks(script["scenes"], starts, titles, total)]
    title = youtube_safe(script["title"])
    if len(title) > TITLE_MAX:
        print(f"    title longer than YouTube's {TITLE_MAX} characters; shortened")
        title = title[:TITLE_MAX - 1].rsplit(" ", 1)[0] + "…"
    description = youtube_safe(config.YOUTUBE_DESCRIPTION)
    if len(chapters) >= MIN_CHAPTERS:
        description += "\n\n" + "\n".join(f"{c['start']} {c['title']}" for c in chapters)
    sources = brief_sources(work)
    if sources:
        description += "\n\nSources:\n" + youtube_safe(sources)
    if len(description.encode()) > DESCRIPTION_MAX_BYTES:
        raise ValueError(f"description is over YouTube's {DESCRIPTION_MAX_BYTES} bytes; "
                         "shorten YOUTUBE_DESCRIPTION or the brief's Sources")
    meta = {"title": title, "description": description, "chapters": chapters, "duration_s": round(total, 3)}
    save_json(work / "metadata.json", meta)
    (work / "subtitles.srt").write_text(subtitles(work, script["scenes"], manifest, starts))
    return meta
