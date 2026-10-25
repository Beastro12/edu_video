"""Content rules for the films prepared under topics/<name>/ (a brief with its reviewed script):
what the prompts ask of a script, checked on the files, so a hand edit can't quietly break them.
Runs on every topic folder that has an outline.json; costs nothing."""
import copy
import json
import re
from pathlib import Path

import pytest
from conftest import PHOTO_CUE_TYPES, medium_cue_problems, photo_cue_problems

import metadata
from agents.script_agent import SCENE
from utils import brief_key

TOPICS = sorted(p.parent for p in (Path(__file__).resolve().parents[1] / "topics").glob("*/outline.json"))
SCENE_KEYS = set(SCENE["properties"])
VISUAL_TYPES = SCENE["properties"]["visual_type"]["enum"]
AI_VIDEO_MAX_WORDS = 25  # CHAPTER_SYSTEM: narration for these scenes stays under this

pytestmark = pytest.mark.parametrize("topic", TOPICS, ids=[t.name for t in TOPICS])


def outline_of(topic):
    return json.loads((topic / "outline.json").read_text(encoding="utf-8"))["chapters"]


def scenes_of(topic):
    """(chapter number, scene number from 1, scene) for every scene of every listed chapter."""
    return [(ch["number"], i, scene)
            for ch in outline_of(topic)
            for i, scene in enumerate(json.loads((topic / f"chapter_{ch['number']:02d}.json").read_text(
                encoding="utf-8")), 1)]


def test_the_outline_adds_up_and_is_numbered_from_one(topic):
    chapters = outline_of(topic)
    total = sum(ch["minutes"] for ch in chapters)
    assert abs(total - round(total)) <= 0.01, f"chapter minutes add up to {total}, not a whole number"
    assert [ch["number"] for ch in chapters] == list(range(1, len(chapters) + 1))


def test_every_listed_chapter_exists_and_its_scenes_follow_the_schema(topic):
    problems = []
    for ch in outline_of(topic):
        path = topic / f"chapter_{ch['number']:02d}.json"
        if not path.exists():
            problems.append(f"{path.name} is listed in the outline but missing")
    assert not problems, "; ".join(problems)
    for number, i, scene in scenes_of(topic):
        where = f"chapter {number} scene {i}"
        assert set(scene) == SCENE_KEYS, f"{where}: keys {sorted(scene)} instead of {sorted(SCENE_KEYS)}"
        assert scene["visual_type"] in VISUAL_TYPES, f"{where}: visual_type {scene['visual_type']!r}"
        assert all(isinstance(scene[k], str) and scene[k].strip() for k in SCENE_KEYS), f"{where}: an empty field"


def test_the_narration_stays_calm(topic):
    problems = []
    for number, i, scene in scenes_of(topic):
        narration = scene["narration"]
        if "!" in narration:
            problems.append(f"chapter {number} scene {i}: an exclamation mark")
        if re.search(r"in this video", narration, re.IGNORECASE):
            problems.append(f"chapter {number} scene {i}: 'in this video'")
    assert not problems, "; ".join(problems)


def test_ai_video_scenes_are_short_and_at_most_one_per_chapter(topic):
    problems, per_chapter = [], {}
    for number, i, scene in scenes_of(topic):
        if scene["visual_type"] != "ai_video":
            continue
        per_chapter[number] = per_chapter.get(number, 0) + 1
        words = len(scene["narration"].split())
        if words >= AI_VIDEO_MAX_WORDS:
            problems.append(f"chapter {number} scene {i}: ai_video narration has {words} words, "
                            f"not under {AI_VIDEO_MAX_WORDS}")
    problems += [f"chapter {n}: {count} ai_video scenes, at most one" for n, count in per_chapter.items() if count > 1]
    assert not problems, "; ".join(problems)


def test_still_and_ai_video_descriptions_use_no_photographic_cue(topic):
    """Under --style flat the description comes before the flat suffix (D25), so blur, mist, depth of
    field and the like can pull the picture toward a photograph. Say it with shapes, gradients and
    glows instead (config.FLAT_PHOTO_CUES lists the words). Changing a description re-keys its picture
    (D9), so do it before the picture is bought."""
    problems = photo_cue_problems(scenes_of(topic))
    assert not problems, "; ".join(problems)


def test_still_and_ai_video_descriptions_name_no_medium(topic):
    """The medium (vector, painting, cartoon...) belongs to the style suffix (D25), so one reviewed
    script serves every style and `calm` stays the default. config.STYLE_MEDIUM_CUES lists the words."""
    problems = medium_cue_problems(scenes_of(topic))
    assert not problems, "; ".join(problems)


@pytest.mark.parametrize("check, text", [
    (photo_cue_problems, "A pale disc on a dark ground. A thin Mist, soft-focus edges."),
    (medium_cue_problems, "A pale disc on a dark ground. A Flat Vector illustration."),
], ids=["photographic", "medium"])
def test_the_cue_checks_see_a_cue_in_a_still_or_a_clip_but_not_in_a_diagram(topic, check, text):
    scenes = scenes_of(topic)
    dirty = copy.deepcopy(scenes)
    for _, _, scene in dirty:  # whatever the folder says now, each scene gets one clean sentence and two cues
        scene["visual_description"] = text
    found = check(dirty)
    wanted = [(n, i) for n, i, s in scenes if s["visual_type"] in PHOTO_CUE_TYPES]
    assert len(found) == 2 * len(wanted), found
    assert all(any(f"chapter {n} scene {i}:" in f for f in found) for n, i in wanted)
    assert not any("manim" in f for f in found)
    assert all(("style suffix (D25)" in f) == (check is medium_cue_problems) for f in found), found


def test_the_brief_exists_and_has_sources_for_the_description(topic):
    path = topic / "brief.md"
    assert path.exists(), "a topic folder carries its brief"
    sources = metadata.parse_sources(path.read_text(encoding="utf-8-sig"))
    assert sources, "no entries under '## Sources' in brief.md"


def test_the_folder_records_the_version_of_the_brief_its_script_was_reviewed_with(topic):
    """Edit the brief and this fails until the chapters are re-reviewed and brief.key is updated
    (python -c "from utils import brief_key; print(brief_key(open('brief.md', encoding='utf-8-sig').read()))")."""
    brief = (topic / "brief.md").read_text(encoding="utf-8-sig")
    key_path = topic / "brief.key"
    assert key_path.exists(), "brief.key is missing: it records the brief the chapters were reviewed with"
    assert key_path.read_text(encoding="utf-8").strip() == brief_key(brief), (
        "brief.md changed since the chapters were reviewed: re-review them, then update brief.key")


SCENE_HEADER = re.compile(r"\*\*(\d+)\. (.+)\*\* \[(\w+)\]")
CHAPTER_HEADER = re.compile(r"## Chapter (\d+): (.+?) \([^()]*\)")
CLAIMS_HEADING = "## Where each scene's claims come from"


def read_script_md(text):
    """script.md as data: its title, its chapter headings, each scene's number, concept,
    visual type, narration and visual description, and the numbers in the claims table. Its
    blocks are separated by blank lines: a heading, a scene's header, its narration, its visual."""
    blocks = [b.strip() for b in re.split(r"\n\s*\n", text) if b.strip()]
    title, chapters, scenes, table, i = None, [], [], None, 0
    while i < len(blocks):
        block = blocks[i]
        if block.startswith("# "):
            title = block[2:].strip()
        elif m := CHAPTER_HEADER.fullmatch(block):
            chapters.append((int(m[1]), m[2]))
        elif m := SCENE_HEADER.fullmatch(block):
            narration, visual = blocks[i + 1], blocks[i + 2]
            assert visual.startswith("*Visual: ") and visual.endswith("*"), f"scene {m[1]}: no '*Visual: ...*' block"
            scenes.append({"n": int(m[1]), "concept": m[2], "visual_type": m[3], "narration": narration,
                           "visual_description": visual[len("*Visual: "):-1],
                           "chapter": chapters[-1][0] if chapters else None})
            i += 2
        elif block == CLAIMS_HEADING:
            table = []
        elif table is not None:
            table += [int(m[1]) for line in block.splitlines() if (m := re.match(r"\|\s*(\d+)\s*\|", line))]
        i += 1
    return title, chapters, scenes, table


def test_script_md_is_the_chapter_files_in_reading_form(topic):
    title, chapters, scenes, table = read_script_md((topic / "script.md").read_text(encoding="utf-8"))
    outline = json.loads((topic / "outline.json").read_text(encoding="utf-8"))
    problems = []
    if title != outline["title"]:
        problems.append(f"title {title!r} is not the outline's {outline['title']!r}")
    if chapters != [(ch["number"], ch["title"]) for ch in outline["chapters"]]:
        problems.append(f"chapter headings {chapters} are not the outline's")
    expected = scenes_of(topic)
    if len(scenes) != len(expected):
        problems.append(f"{len(scenes)} scenes in script.md, {len(expected)} in the chapter files")
    for n, (md, (chapter, i, scene)) in enumerate(zip(scenes, expected, strict=False), 1):
        where = f"scene {n} (chapter {chapter} scene {i})"
        if md["n"] != n:
            problems.append(f"{where}: numbered {md['n']} in script.md")
        if md["chapter"] != chapter:
            problems.append(f"{where}: under chapter {md['chapter']}'s heading in script.md")
        for key in ("concept", "visual_type", "narration", "visual_description"):
            if md[key] != scene[key].strip():
                problems.append(f"{where}: {key} differs between script.md and chapter_{chapter:02d}.json")
    assert not problems, "; ".join(problems)
    assert table == list(range(1, len(expected) + 1)), (
        f"the claims table has rows {table}, expected one per scene, 1..{len(expected)}")
