"""P3-1: a research brief (--brief FILE) goes to the outline agent, the chapter writer and the
critic, is priced into the estimate, is kept in the build folder next to a stamp that stops a
different brief being mixed into a script already written, and its Sources reach the YouTube
description. Without a brief every prompt is exactly what it was before."""
import copy
import json
import shutil
import sys
from pathlib import Path

import pytest

import agents.critic as critic
import agents.script_agent as script_agent
import config
import ledger
import metadata
import pipeline
from utils import brief_key, content_key, save_json

BRIEF = """# Brief: the July 2026 result

## Facts
- The team reports a signal at 4 sigma. Say "reports", never "proves".

## Sources
- [Journal paper](https://example.org/paper)
"""
OTHER_BRIEF = BRIEF.replace("4 sigma", "5 sigma")


def block(brief):
    return ("Research brief (verified facts, hedging rules and sources for this film). Rely on it for\n"
            "anything about recent research; don't add facts about that research beyond it.\n"
            f"<<<BRIEF\n{brief}\nBRIEF>>>\n\n")


BRIEF_BLOCK = block(BRIEF)
CRITIC_CHECK = ("Check every claim about recent research against the brief. A claim the brief doesn't "
                "support: make it more general or remove it, and list it as an issue.\n\n")

OUTLINE = {"title": "T", "chapters": [
    {"number": 1, "title": "Ch1", "summary": "s", "key_ideas": ["k"], "minutes": 2},
    {"number": 2, "title": "Ch2", "summary": "s", "key_ideas": ["k"], "minutes": 2}]}
SCENES = [{"concept": "c", "narration": "a quiet line", "visual_type": "still", "visual_description": "d"}]
SYSTEMS = {"outline": script_agent.OUTLINE_SYSTEM, "chapter": script_agent.CHAPTER_SYSTEM,
           "review": critic.SYSTEM}


def fake_claude(calls):
    """Stands in for Claude: records (tool, system, prompt) and answers like a happy critic.
    Answers are copies: get_script adds keys to the scenes it receives."""
    def fake(system, prompt, tool, schema, max_tokens=8000):
        calls.append((tool, system, prompt))
        if tool == "outline":
            return copy.deepcopy(OUTLINE)
        if tool == "chapter":
            return {"scenes": copy.deepcopy(SCENES)}
        scenes = json.loads(prompt.split("scenes:\n", 1)[1])
        return {"approved": True, "issues": [], "revised_scenes": scenes}
    return fake


@pytest.fixture
def calls(monkeypatch):
    seen = []
    monkeypatch.setattr(script_agent, "structured", fake_claude(seen))
    monkeypatch.setattr(critic, "structured", fake_claude(seen))
    return seen


@pytest.fixture
def build():
    """The build folder `pipeline.main()` uses for the topic "topic"."""
    return Path(config.BUILD_DIR) / "topic"


# What the prompts were before briefs existed, written out the old way.
def old_outline_prompt(topic, minutes):
    return f"Topic: {topic}\nTotal length: {minutes} minutes."


def old_chapter_prompt(outline, chapter, previous_tail):
    words = int(chapter["minutes"] * config.WORDS_PER_MIN)
    return (
        f"Full outline (for context):\n{json.dumps(outline, ensure_ascii=False)}\n\n"
        f"Write chapter {chapter['number']}: {chapter['title']}\n"
        f"Target: about {words} words of narration.\n\n"
        + ("Previous chapter ended with:\n" + "\n".join(previous_tail) if previous_tail
           else "This is the opening chapter.")
    )


def old_review_prompt(outline, chapter, scenes):
    return (f"Outline: {json.dumps(outline, ensure_ascii=False)}\n\n"
            f"Chapter {chapter['number']} ({chapter['title']}) scenes:\n"
            f"{json.dumps(scenes, ensure_ascii=False)}")


# --- prompts -----------------------------------------------------------------

def test_the_brief_reaches_the_outline_every_chapter_and_every_critic_in_the_user_prompt(tmp_path, calls):
    pipeline.get_script("topic", 4, tmp_path, skip_critic=False, brief=BRIEF)

    ch1, ch2 = OUTLINE["chapters"]
    assert [(tool, prompt) for tool, _, prompt in calls] == [
        ("outline", BRIEF_BLOCK + old_outline_prompt("topic", 4)),
        ("chapter", BRIEF_BLOCK + old_chapter_prompt(OUTLINE, ch1, [])),
        ("review", BRIEF_BLOCK + CRITIC_CHECK + old_review_prompt(OUTLINE, ch1, SCENES)),
        ("chapter", BRIEF_BLOCK + old_chapter_prompt(OUTLINE, ch2, [SCENES[-1]["narration"]])),
        ("review", BRIEF_BLOCK + CRITIC_CHECK + old_review_prompt(OUTLINE, ch2, SCENES)),
    ], "the brief comes before everything else; the old text follows unchanged"
    for tool, system, _ in calls:
        assert system == SYSTEMS[tool], "system prompts do not change"
        assert "<<<BRIEF" not in system and BRIEF not in system


def test_the_critic_prompt_with_a_brief_still_ends_with_the_scenes_json(calls):
    critic.review_chapter(OUTLINE, OUTLINE["chapters"][0], SCENES, brief=BRIEF)

    prompt = calls[0][2]
    assert json.loads(prompt.split("scenes:\n", 1)[1]) == SCENES, "how test_script_flow's fake critic reads it"
    assert prompt.endswith(json.dumps(SCENES, ensure_ascii=False))
    assert prompt.index(CRITIC_CHECK) == len(BRIEF_BLOCK), "the instruction follows the brief block"


@pytest.mark.parametrize("tail", [[], ["It ended softly.", "And then it was quiet."]])
def test_without_a_brief_every_prompt_is_what_it_was_before(calls, tail):
    chapter = OUTLINE["chapters"][1]
    script_agent.plan_outline("A topic: with café", 7.5)
    script_agent.write_chapter(OUTLINE, chapter, tail)
    critic.review_chapter(OUTLINE, chapter, SCENES)

    (_, _, outline), (_, _, writer), (_, _, review) = calls
    assert outline == old_outline_prompt("A topic: with café", 7.5)
    assert writer == old_chapter_prompt(OUTLINE, chapter, tail)
    assert review == old_review_prompt(OUTLINE, chapter, SCENES)
    assert all("Research brief" not in p and "BRIEF" not in p for _, _, p in calls)
    assert [c[1] for c in calls] == [SYSTEMS["outline"], SYSTEMS["chapter"], SYSTEMS["review"]]


def test_get_script_without_a_brief_sends_none_even_if_the_build_folder_holds_one(tmp_path, calls):
    (tmp_path / "brief.md").write_text(BRIEF)

    pipeline.get_script("topic", 4, tmp_path, skip_critic=False)

    assert calls and all("Research brief" not in p for _, _, p in calls)
    assert (tmp_path / "brief.md").read_text() == BRIEF


# --- the brief and the script it was written from ----------------------------

def run_cli(monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["pipeline.py", *argv])
    pipeline.main()


def brief_file(tmp_path, text, name="brief.md"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_script_only_with_a_brief_keeps_it_and_its_stamp_in_the_build_folder_and_uses_it(
        tmp_path, monkeypatch, calls, build):
    run_cli(monkeypatch, "topic", "--brief", brief_file(tmp_path, BRIEF, "my brief.md"), "--script-only")

    assert (build / "brief.md").read_text(encoding="utf-8") == BRIEF
    assert (build / "brief.key").read_text() == content_key("brief", BRIEF)
    assert calls and all(p.startswith(BRIEF_BLOCK) for _, _, p in calls)
    assert (build / "script.json").exists()
    assert not list(build.glob("*.partial-*")), "files are made atomically"


def test_a_byte_order_mark_is_not_part_of_the_brief(tmp_path, monkeypatch, calls, build):
    path = tmp_path / "brief.md"
    path.write_bytes(b"\xef\xbb\xbf" + BRIEF.encode())

    run_cli(monkeypatch, "topic", "--brief", str(path), "--script-only")

    assert calls[0][2].startswith(BRIEF_BLOCK)
    assert (build / "brief.md").read_bytes() == BRIEF.encode()


def test_a_brief_change_before_any_script_file_exists_replaces_the_kept_copy(tmp_path, monkeypatch, calls, build):
    build.mkdir(parents=True)  # what a run that stopped before saving anything leaves behind
    (build / "brief.md").write_text(OTHER_BRIEF)
    (build / "brief.key").write_text(content_key("brief", OTHER_BRIEF))

    run_cli(monkeypatch, "topic", "--brief", brief_file(tmp_path, BRIEF), "--script-only")

    assert (build / "brief.md").read_text() == BRIEF
    assert (build / "brief.key").read_text() == content_key("brief", BRIEF)
    assert calls and all(p.startswith(BRIEF_BLOCK) for _, _, p in calls)


SCRIPT_FILES = ["outline.json", "chapter_01.json", "chapter_02.json", "script.json", "script.meta.json"]


@pytest.mark.parametrize("left", ["outline.json", "chapter_01.json", "script.json"])
def test_a_different_brief_is_refused_once_any_script_file_exists(tmp_path, monkeypatch, calls, build, left):
    run_cli(monkeypatch, "topic", "--brief", brief_file(tmp_path, BRIEF), "--script-only")
    for name in SCRIPT_FILES:
        if name != left:
            (build / name).unlink()
    n = len(calls)

    with pytest.raises(RuntimeError, match=r"different brief.*delete outline\.json, chapter_\*\.json and script\.json"):
        run_cli(monkeypatch, "topic", "--brief", brief_file(tmp_path, OTHER_BRIEF, "other.md"), "--script-only")

    assert len(calls) == n, "nothing was generated"
    assert (build / "brief.md").read_text() == BRIEF, "the kept copy is still the brief the script came from"
    assert (build / "brief.key").read_text() == content_key("brief", BRIEF)


def test_a_run_without_a_brief_is_refused_when_the_kept_copy_is_not_the_one_the_script_was_written_with(
        tmp_path, monkeypatch, calls, build):
    run_cli(monkeypatch, "topic", "--brief", brief_file(tmp_path, BRIEF), "--script-only")
    (build / "brief.md").write_text(OTHER_BRIEF)  # edited since: no longer what the stamp names
    n = len(calls)

    with pytest.raises(RuntimeError, match="written with a brief; this run has none") as refused:
        run_cli(monkeypatch, "topic", "--script-only")

    assert "(--brief" not in str(refused.value), "no matching copy to suggest"
    assert len(calls) == n
    assert (build / "brief.md").read_text() == OTHER_BRIEF
    assert (build / "brief.key").read_text() == content_key("brief", BRIEF)


def test_a_run_without_a_brief_is_refused_by_get_script_but_names_the_kept_copy(tmp_path, calls):
    pipeline.get_script("topic", 4, tmp_path, skip_critic=False, brief=BRIEF)
    (tmp_path / "brief.md").write_text(BRIEF)  # keep_brief's doing, in `main`

    with pytest.raises(RuntimeError, match="written with a brief; this run has none") as refused:
        pipeline.get_script("topic", 4, tmp_path, skip_critic=False)

    assert f"(--brief {tmp_path / 'brief.md'})" in str(refused.value)


def test_a_rerun_without_a_brief_goes_on_with_the_kept_one(tmp_path, monkeypatch, calls, build, capsys):
    run_cli(monkeypatch, "topic", "--brief", brief_file(tmp_path, BRIEF), "--script-only")
    for name in ("chapter_02.json", "script.json", "script.meta.json"):
        (build / name).unlink()  # chapter 2 is still to be written
    n = len(calls)
    capsys.readouterr()

    run_cli(monkeypatch, "topic", "--script-only")

    assert f"• brief: using the one this script was written with ({build / 'brief.md'})" in capsys.readouterr().out
    assert [c[0] for c in calls[n:]] == ["chapter", "review"]
    assert all(c[2].startswith(BRIEF_BLOCK) for c in calls[n:]), "the writer and critic still have the brief"
    assert (build / "brief.md").read_text() == BRIEF
    assert (build / "brief.key").read_text() == content_key("brief", BRIEF)


def test_the_estimate_of_a_rerun_uses_the_kept_brief(tmp_path, monkeypatch, calls, build):
    run_cli(monkeypatch, "topic", "--brief", brief_file(tmp_path, BRIEF), "--script-only")
    seen = []

    def estimate(*a, **k):
        seen.append((k.get("brief"), k.get("worst_case", False)))
        return {"claude": 0.0, "elevenlabs": 0.0, "images": 0.0, "veo": 0.0}

    monkeypatch.setattr(pipeline, "estimate_cost", estimate)
    run_cli(monkeypatch, "topic", "--estimate")

    assert seen == [(BRIEF, False), (BRIEF, True)]


def test_a_different_brief_is_refused_and_the_kept_copy_is_suggested(tmp_path, monkeypatch, calls, build):
    run_cli(monkeypatch, "topic", "--brief", brief_file(tmp_path, BRIEF), "--script-only")

    with pytest.raises(RuntimeError, match="written with a different brief") as refused:
        run_cli(monkeypatch, "topic", "--brief", brief_file(tmp_path, OTHER_BRIEF, "other.md"), "--script-only")

    assert f"Rerun with the brief it was written from (--brief {build / 'brief.md'})" in str(refused.value)


def test_the_estimate_says_when_the_run_would_be_refused_and_still_prints_it(tmp_path, monkeypatch, calls, build,
                                                                             capsys):
    monkeypatch.setattr(config, "GOOGLE_API_KEY", None)
    run_cli(monkeypatch, "topic", "--brief", brief_file(tmp_path, BRIEF), "--script-only")
    n = len(calls)
    capsys.readouterr()

    run_cli(monkeypatch, "topic", "--brief", brief_file(tmp_path, OTHER_BRIEF, "other.md"), "--estimate")

    out = capsys.readouterr().out
    assert "this run would be refused: The script in " in out and "written with a different brief" in out
    assert out.index("would be refused") < out.index("Projected cost"), "before the estimate, which is still there"
    assert len(calls) == n


def test_the_conflict_check_only_reads(tmp_path):
    assert pipeline._brief_conflict(tmp_path, BRIEF) is None
    save_json(tmp_path / "outline.json", OUTLINE)  # a build from before briefs existed

    assert "written without a brief; this run has one" in pipeline._brief_conflict(tmp_path, BRIEF)
    assert pipeline._brief_conflict(tmp_path, None) is None
    assert sorted(p.name for p in tmp_path.iterdir()) == ["outline.json"], "no stamp written"


def test_a_brief_is_refused_on_a_script_written_without_one(tmp_path, monkeypatch, calls, build):
    run_cli(monkeypatch, "topic", "--script-only")
    assert (build / "brief.key").read_text() == "none"
    n = len(calls)

    with pytest.raises(RuntimeError, match="written without a brief; this run has one"):
        run_cli(monkeypatch, "topic", "--brief", brief_file(tmp_path, BRIEF), "--script-only")

    assert len(calls) == n
    assert not (build / "brief.md").exists(), "the brief the script was not written from is not kept"
    assert (build / "brief.key").read_text() == "none"


def test_a_build_from_before_briefs_existed_counts_as_written_without_one(tmp_path, monkeypatch, calls, build):
    run_cli(monkeypatch, "topic", "--script-only")
    (build / "brief.key").unlink()  # such builds have no stamp
    n = len(calls)

    with pytest.raises(RuntimeError, match="written without a brief; this run has one"):
        run_cli(monkeypatch, "topic", "--brief", brief_file(tmp_path, BRIEF), "--script-only")
    assert not (build / "brief.key").exists()

    run_cli(monkeypatch, "topic", "--script-only")
    assert len(calls) == n, "still the same script, nothing regenerated"
    assert (build / "brief.key").read_text() == "none"


def test_the_same_brief_twice_is_fine_and_the_second_run_calls_nothing(tmp_path, monkeypatch, calls, build):
    path = brief_file(tmp_path, BRIEF)
    run_cli(monkeypatch, "topic", "--brief", path, "--script-only")
    n = len(calls)

    run_cli(monkeypatch, "topic", "--brief", path, "--script-only")

    assert n > 0 and len(calls) == n
    assert (build / "brief.md").read_text() == BRIEF


@pytest.mark.parametrize("brief, key", [(BRIEF, content_key("brief", BRIEF)), (None, "none")],
                         ids=["a brief", "no brief"])
def test_the_stamp_exists_before_the_outline_call(tmp_path, monkeypatch, brief, key):
    stamps = []
    inner = fake_claude([])

    def fake(system, prompt, tool, schema, max_tokens=8000):
        if tool == "outline":
            stamps.append((tmp_path / "brief.key").read_text())
        return inner(system, prompt, tool, schema, max_tokens)

    monkeypatch.setattr(script_agent, "structured", fake)
    monkeypatch.setattr(critic, "structured", fake)

    pipeline.get_script("topic", 4, tmp_path, skip_critic=False, brief=brief)

    assert stamps == [key]


def test_a_crash_midway_leaves_a_stamp_that_matches_the_partial_files(tmp_path, monkeypatch):
    chapters, inner = [], fake_claude([])

    def flaky(system, prompt, tool, schema, max_tokens=8000):
        if tool == "chapter":
            chapters.append(1)
            if len(chapters) == 2:
                raise RuntimeError("boom")
        return inner(system, prompt, tool, schema, max_tokens)

    monkeypatch.setattr(script_agent, "structured", flaky)
    monkeypatch.setattr(critic, "structured", flaky)
    with pytest.raises(RuntimeError, match="boom"):
        pipeline.get_script("topic", 4, tmp_path, skip_critic=False, brief=BRIEF)
    assert sorted(p.name for p in tmp_path.glob("*.json")) == ["chapter_01.json", "outline.json"]
    assert (tmp_path / "brief.key").read_text() == content_key("brief", BRIEF)

    with pytest.raises(RuntimeError, match="different brief"):
        pipeline.get_script("topic", 4, tmp_path, skip_critic=False, brief=OTHER_BRIEF)

    script = pipeline.get_script("topic", 4, tmp_path, skip_critic=False, brief=BRIEF)
    assert len(script["scenes"]) == 2


@pytest.mark.parametrize("content, message", [(None, "cannot read"), ("  \n", "is empty")])
def test_an_unreadable_or_empty_brief_is_a_usage_error(tmp_path, monkeypatch, capsys, build, content, message):
    brief = tmp_path / "brief.md"
    if content is not None:
        brief.write_text(content)

    with pytest.raises(SystemExit) as stop:
        run_cli(monkeypatch, "topic", "--brief", str(brief), "--script-only")

    assert stop.value.code == 2
    assert message in capsys.readouterr().err
    assert not build.exists(), "nothing was started"


# --- a brief's folder with its reviewed script (P3-3) ------------------------

SEEDED = {1: ["The first seeded line, spoken slowly. " * 3, "A second seeded line."],
          2: ["The second chapter opens with a seeded line. " * 4]}


def seeded_chapter(n):
    return [{"concept": f"seed {n}.{i}", "narration": text, "visual_type": "still", "visual_description": "d"}
            for i, text in enumerate(SEEDED[n], 1)]


@pytest.fixture
def folder(tmp_path):
    """A brief's folder carrying its reviewed script: the outline and both chapters, and the key
    of the brief they were reviewed with."""
    folder = tmp_path / "topics" / "demo"
    folder.mkdir(parents=True)
    (folder / "brief.md").write_text(BRIEF, encoding="utf-8")
    (folder / "brief.key").write_text(content_key("brief", BRIEF))
    save_json(folder / "outline.json", OUTLINE)
    for n in SEEDED:
        save_json(folder / f"chapter_{n:02d}.json", seeded_chapter(n))
    return folder


def seed_run(monkeypatch, folder, *flags):
    run_cli(monkeypatch, "topic", "--brief", str(folder / "brief.md"), *(flags or ("--script-only",)))


def script_narrations(build):
    return [s["narration"] for s in json.loads((build / "script.json").read_text())["scenes"]]


def test_a_seeded_script_only_builds_the_script_without_calling_claude(folder, monkeypatch, calls, build, capsys):
    seed_run(monkeypatch, folder)

    assert calls == []
    assert script_narrations(build) == SEEDED[1] + SEEDED[2]
    assert (build / "brief.key").read_text() == content_key("brief", BRIEF), "the folder's script goes with its brief"
    assert (build / "brief.md").read_text() == BRIEF
    assert f"• script: seeded outline.json, chapter_01.json, chapter_02.json from {folder}" in capsys.readouterr().out
    assert json.loads((build / "outline.json").read_text()) == OUTLINE
    assert not list(build.glob("*.partial-*"))


def test_a_rerun_of_a_seeded_build_goes_on_quietly_and_calls_nothing(folder, monkeypatch, calls, build, capsys):
    seed_run(monkeypatch, folder)
    capsys.readouterr()

    seed_run(monkeypatch, folder)

    assert calls == []
    out = capsys.readouterr().out
    assert out == ("• style: calm (recorded in the build)\n"  # P3-2: every run says its style
                   "• script: using cached script.json (edit it freely)\nScript saved to " + str(
                       build / "script.json") + ". Edit it, then rerun without --script-only.\n"
                   "• run report: " + str(build / "run_report.md") + "\n"), \
        "nothing to say about the seed"  # P2-3: and where the run's report went


def test_a_partial_seed_leaves_the_missing_chapter_to_the_writer_with_the_brief(folder, monkeypatch, calls, build,
                                                                                capsys):
    (folder / "chapter_02.json").unlink()

    seed_run(monkeypatch, folder)

    assert [c[0] for c in calls] == ["chapter", "review"]
    chapter, review = calls[0][2], calls[1][2]
    assert chapter.startswith(BRIEF_BLOCK) and "Write chapter 2: Ch2" in chapter
    assert SEEDED[1][-1] in chapter, "chapter 2 continues from the seeded chapter 1"
    assert review.startswith(BRIEF_BLOCK + CRITIC_CHECK)
    assert f"seeded outline.json, chapter_01.json from {folder}" in capsys.readouterr().out
    assert (build / "chapter_01.json").exists() and (build / "chapter_02.json").exists()


def test_an_interrupted_seed_is_completed_on_the_rerun_without_calling_claude(folder, monkeypatch, calls, build,
                                                                             capsys):
    real, copies = shutil.copyfile, []

    def dies_on_the_third_copy(src, dst, *args, **kwargs):
        if len(copies) == 2:
            raise OSError("disk full")
        copies.append(src)
        return real(src, dst, *args, **kwargs)

    monkeypatch.setattr(shutil, "copyfile", dies_on_the_third_copy)
    with pytest.raises(OSError, match="disk full"):
        seed_run(monkeypatch, folder)
    assert sorted(p.name for p in build.iterdir()) == [  # P2-3: the crash left its report too
        "brief.key", "chapter_01.json", "outline.json", "run_report.md", "style"]
    assert (build / "brief.key").read_text() == content_key("brief", BRIEF), "the stamp went in first"
    monkeypatch.setattr(shutil, "copyfile", real)
    capsys.readouterr()

    seed_run(monkeypatch, folder)

    assert calls == [], "the reviewed chapter is copied, not rewritten"
    assert script_narrations(build) == SEEDED[1] + SEEDED[2]
    assert f"• script: seeded chapter_02.json from {folder}" in capsys.readouterr().out


def test_a_chapter_deleted_from_a_seeded_build_is_seeded_again_not_rewritten(folder, monkeypatch, calls, build,
                                                                            capsys):
    seed_run(monkeypatch, folder)
    for name in ("chapter_02.json", "script.json", "script.meta.json"):
        (build / name).unlink()
    capsys.readouterr()

    seed_run(monkeypatch, folder)

    assert calls == []
    assert script_narrations(build) == SEEDED[1] + SEEDED[2]
    assert f"• script: seeded chapter_02.json from {folder}" in capsys.readouterr().out


def test_a_build_chapter_that_differs_from_the_seed_is_kept_and_named(folder, monkeypatch, calls, build, capsys):
    seed_run(monkeypatch, folder)
    edited = seeded_chapter(1)
    edited[0]["narration"] = "A line edited in the build."
    save_json(build / "chapter_01.json", edited)
    capsys.readouterr()

    seed_run(monkeypatch, folder)

    out = capsys.readouterr().out
    assert (f"• script: chapter_01.json: not the same in {build} as in {folder}; keeping the build's "
            "(to take the seed's, delete the build's copy and script.json)") in out
    assert "chapter_02.json" not in out.split("keeping")[0], "only the one that differs is named"
    assert calls == [] and json.loads((build / "chapter_01.json").read_text()) == edited


def test_a_build_whose_outline_is_not_the_seeds_is_not_seeded(folder, monkeypatch, calls, build, capsys):
    build.mkdir(parents=True)
    own = {"title": "Mine", "chapters": [OUTLINE["chapters"][0]]}
    save_json(build / "outline.json", own)
    (build / "brief.key").write_text(content_key("brief", BRIEF))

    seed_run(monkeypatch, folder)

    assert (f"• script: outline.json in {build} isn't the one in {folder}; not seeding (to start from the "
            "seed's, delete the build's outline.json, chapter_*.json and script.json)") in capsys.readouterr().out
    assert json.loads((build / "outline.json").read_text()) == own
    assert [c[0] for c in calls] == ["chapter", "review"], "the build's own outline is written out as usual"
    assert script_narrations(build) == [SCENES[0]["narration"]], "nothing of the folder's chapters"


def test_the_brief_kept_in_the_build_folder_is_not_a_seed_folder(tmp_path, monkeypatch, calls, build):
    run_cli(monkeypatch, "topic", "--brief", brief_file(tmp_path, BRIEF), "--script-only")
    n = len(calls)

    run_cli(monkeypatch, "topic", "--brief", str(build / "brief.md"), "--script-only")  # what a refusal suggests

    assert len(calls) == n


def damage_scene(folder, **changes):
    chapter = seeded_chapter(2)
    chapter[0].update(changes)
    save_json(folder / "chapter_02.json", chapter)


@pytest.mark.parametrize("damage", ["outline not json", "no chapters", "chapter without number",
                                    "chapter file not a list", "chapter file not json", "scene without a key",
                                    "scene with another key", "scene with an unknown visual type"])
def test_an_invalid_seed_copies_nothing(folder, monkeypatch, calls, build, capsys, damage):
    if damage == "outline not json":
        (folder / "outline.json").write_text("{not json")
    elif damage == "no chapters":
        save_json(folder / "outline.json", {"title": "T"})
    elif damage == "chapter without number":
        save_json(folder / "outline.json", {"title": "T", "chapters": [{"title": "a", "minutes": 1}]})
    elif damage == "chapter file not a list":
        save_json(folder / "chapter_02.json", {"scenes": []})
    elif damage == "chapter file not json":
        (folder / "chapter_01.json").write_text("[oops")
    elif damage == "scene without a key":
        chapter = seeded_chapter(2)
        del chapter[0]["visual_description"]
        save_json(folder / "chapter_02.json", chapter)
    elif damage == "scene with another key":
        damage_scene(folder, notes="x")
    else:
        damage_scene(folder, visual_type="gif")

    for flag in ("--script-only", "--estimate"):
        with pytest.raises(SystemExit) as stop:
            seed_run(monkeypatch, folder, flag)
        assert stop.value.code == 2

    err = capsys.readouterr().err
    assert err.count("--brief:") == 2 and str(folder) in err
    assert calls == [] and not build.exists(), "nothing copied, nothing stamped"


@pytest.mark.parametrize("key, problem", [(None, "brief.key is missing"), ("0123456789abcdef", "brief.key does not match")])
def test_a_seed_reviewed_with_another_version_of_its_brief_is_refused(folder, monkeypatch, calls, build, capsys,
                                                                    key, problem):
    if key is None:
        (folder / "brief.key").unlink()
    else:
        (folder / "brief.key").write_text(key)

    for flag in ("--script-only", "--estimate"):
        with pytest.raises(SystemExit) as stop:
            seed_run(monkeypatch, folder, flag)
        assert stop.value.code == 2

    err = capsys.readouterr().err
    assert err.count("was reviewed with a different version of its brief") == 2 and problem in err
    assert f"re-review the chapters, then update {folder / 'brief.key'}" in err
    assert calls == [] and not build.exists()


def test_editing_the_brief_in_a_seed_folder_refuses_the_seed_until_it_is_restamped(folder, monkeypatch, calls, build):
    (folder / "brief.md").write_text(OTHER_BRIEF)

    with pytest.raises(SystemExit):
        seed_run(monkeypatch, folder)

    (folder / "brief.key").write_text(content_key("brief", OTHER_BRIEF))
    seed_run(monkeypatch, folder)
    assert calls == [] and (build / "brief.key").read_text() == content_key("brief", OTHER_BRIEF)


@pytest.mark.parametrize("missing", [None, 2], ids=["whole seed", "chapter 2 missing"])
def test_the_estimate_prices_a_seed_as_if_it_were_already_in_the_build(folder, tmp_path, monkeypatch, missing):
    monkeypatch.setattr(config, "GOOGLE_API_KEY", None)
    if missing:
        (folder / f"chapter_{missing:02d}.json").unlink()
    copied = tmp_path / "copied"  # what seeding would leave behind
    copied.mkdir()
    for path in folder.glob("*.json"):
        (copied / path.name).write_text(path.read_text())
    fresh = tmp_path / "fresh"

    seeded = pipeline.estimate_cost(15, fresh, False, False, brief=BRIEF, seed=folder)
    worst = pipeline.estimate_cost(15, fresh, False, False, worst_case=True, brief=BRIEF, seed=folder)

    assert seeded == pipeline.estimate_cost(15, copied, False, False, brief=BRIEF)
    assert worst == pipeline.estimate_cost(15, copied, False, False, worst_case=True, brief=BRIEF)
    assert seeded["claude"] < pipeline.estimate_cost(15, fresh, False, False, brief=BRIEF)["claude"]
    assert not fresh.exists(), "nothing written"
    if not missing:
        narration = SEEDED[1] + SEEDED[2]
        assert seeded["elevenlabs"] == pytest.approx(sum(ledger.tts_eur(len(t)) for t in narration)), \
            "narration priced from the seeded text"
        no_script_calls = pipeline.estimate_cost(15, fresh, False, True, brief=BRIEF, seed=folder)
        assert no_script_calls["claude"] == seeded["claude"], "no outline, writer or critic call left"


def test_the_estimate_of_a_partly_seeded_build_prices_what_seeding_would_add(folder, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "GOOGLE_API_KEY", None)
    build = tmp_path / "build"
    build.mkdir()
    for name in ("outline.json", "chapter_01.json"):  # an interrupted seed
        (build / name).write_text((folder / name).read_text())
    complete = tmp_path / "complete"
    complete.mkdir()
    for path in folder.glob("*.json"):
        (complete / path.name).write_text(path.read_text())

    assert (pipeline.estimate_cost(15, build, False, False, brief=BRIEF, seed=folder)
            == pipeline.estimate_cost(15, complete, False, False, brief=BRIEF))


def test_the_estimate_ignores_a_seed_whose_outline_the_build_does_not_share(folder, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "GOOGLE_API_KEY", None)
    save_json(tmp_path / "outline.json", {"title": "Mine", "chapters": [OUTLINE["chapters"][0]]})

    with_seed = pipeline.estimate_cost(15, tmp_path, False, False, brief=BRIEF, seed=folder)

    assert with_seed == pipeline.estimate_cost(15, tmp_path, False, False, brief=BRIEF)


def test_estimate_of_a_seeded_brief_says_so_and_writes_nothing(folder, monkeypatch, calls, build, capsys):
    monkeypatch.setattr(config, "GOOGLE_API_KEY", None)

    seed_run(monkeypatch, folder, "--estimate")

    out = capsys.readouterr().out
    est = pipeline.estimate_cost(config.DEFAULT_MINUTES, build, False, False, brief=BRIEF, seed=folder)
    assert f"• script: would seed outline.json, chapter_01.json, chapter_02.json from {folder}" in out
    assert f"€{sum(est.values()):.2f}" in out
    assert calls == [] and not build.exists()


def test_estimate_says_why_a_seed_would_not_be_used(folder, monkeypatch, calls, build, capsys):
    build.mkdir(parents=True)
    save_json(build / "outline.json", {"title": "Mine", "chapters": [OUTLINE["chapters"][0]]})
    (build / "brief.key").write_text(content_key("brief", BRIEF))

    seed_run(monkeypatch, folder, "--estimate")

    out = capsys.readouterr().out
    assert f"outline.json in {build} isn't the one in {folder}; not seeding" in out and "would seed" not in out
    assert "Projected cost" in out


# --- the estimate ------------------------------------------------------------

def test_the_cli_passes_the_brief_to_both_estimates(tmp_path, monkeypatch):
    seen = []

    def estimate(*a, **k):
        seen.append((k.get("brief"), k.get("worst_case", False)))
        return {"claude": 0.0, "elevenlabs": 0.0, "images": 0.0, "veo": 0.0}

    monkeypatch.setattr(pipeline, "estimate_cost", estimate)
    run_cli(monkeypatch, "topic", "--brief", brief_file(tmp_path, BRIEF), "--estimate")
    assert seen == [(BRIEF, False), (BRIEF, True)], "typical and worst case"

    seen.clear()
    run_cli(monkeypatch, "topic", "--estimate")
    assert seen == [(None, False), (None, True)]


def test_estimate_with_a_brief_prints_the_projected_cost_and_writes_nothing(tmp_path, monkeypatch, capsys, build):
    monkeypatch.setattr(config, "GOOGLE_API_KEY", None)
    long_brief = BRIEF + "- Another verified fact, stated plainly. " * 4000

    run_cli(monkeypatch, "topic", "--estimate")
    plain = capsys.readouterr().out
    run_cli(monkeypatch, "topic", "--brief", brief_file(tmp_path, long_brief), "--estimate")
    out = capsys.readouterr().out

    est = pipeline.estimate_cost(config.DEFAULT_MINUTES, build, False, False, brief=long_brief)
    assert f"€{sum(est.values()):.2f}" in out and "Projected cost" in out
    assert out != plain, "the brief is priced in"
    assert not build.exists(), "--estimate creates nothing"


def brief_call_eur(brief, review) -> float:
    """What the brief adds to one script call: the whole block it is sent in, and for the critic
    the instruction after it."""
    texts = (script_agent.brief_block(brief), critic.BRIEF_CHECK) if review else (script_agent.brief_block(brief),)
    return ledger.claude_eur(config.CLAUDE_MODEL, ledger.claude_input_estimate(*texts), 0)


def extra_claude(brief, work, **kw) -> float:
    kw.setdefault("skip_critic", False)
    plain = pipeline.estimate_cost(15, work, allow_veo=False, **kw)
    briefed = pipeline.estimate_cost(15, work, allow_veo=False, brief=brief, **kw)
    assert {k: v for k, v in briefed.items() if k != "claude"} == {k: v for k, v in plain.items() if k != "claude"}
    return briefed["claude"] - plain["claude"]


@pytest.mark.parametrize("kw, reviews_per_chapter", [
    ({}, 1),
    ({"skip_critic": True}, 0),
    ({"worst_case": True}, config.MAX_CRITIC_ROUNDS),
])
def test_the_estimate_adds_the_brief_to_every_script_call(monkeypatch, tmp_path, kw, reviews_per_chapter):
    monkeypatch.setattr(config, "GOOGLE_API_KEY", None)
    brief = "A verified fact. " * 3000

    extra = extra_claude(brief, tmp_path / "fresh", **kw)

    writes = 1 + config.EST_CHAPTERS  # the outline and a call per chapter carry the brief block...
    reviews = config.EST_CHAPTERS * reviews_per_chapter  # ...and each review that block and the critic's instruction
    assert extra > 0
    assert extra == pytest.approx(writes * brief_call_eur(brief, False) + reviews * brief_call_eur(brief, True))


def test_the_estimate_has_no_outline_call_to_add_it_to_once_the_outline_exists(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "GOOGLE_API_KEY", None)
    save_json(tmp_path / "outline.json", {"title": "T", "chapters": [
        {"number": 1, "title": "a", "minutes": 2}, {"number": 2, "title": "b", "minutes": 2}]})
    save_json(tmp_path / "chapter_01.json", SCENES)  # chapter 1 is written: only chapter 2 is left
    brief = "A verified fact. " * 3000

    extra = extra_claude(brief, tmp_path)

    assert extra == pytest.approx(brief_call_eur(brief, False) + brief_call_eur(brief, True))


def test_the_estimate_is_unchanged_once_the_script_exists(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "GOOGLE_API_KEY", None)
    save_json(tmp_path / "script.json", {"title": "T", "scenes": [
        {"id": 1, "concept": "c", "narration": "x" * 200, "visual_type": "manim", "visual_description": "d"}]})

    for kw in ({}, {"worst_case": True}):
        plain = pipeline.estimate_cost(15, tmp_path, allow_veo=False, skip_critic=False, **kw)
        assert pipeline.estimate_cost(15, tmp_path, allow_veo=False, skip_critic=False, brief="x" * 90_000,
                                      **kw) == plain, "no script call is left to carry the brief"


# --- the YouTube description -------------------------------------------------

SOURCES_BRIEF = """# Brief

## Facts
- See [not a source](https://example.org/facts) for context.

## Sources
- [Journal paper](https://example.org/paper)

* Lab press release: https://example.org/press
- Plain text entry, accessed 2026-07-01
-   [Preprint](https://example.org/preprint?a=1&b=2) (v2)

## Notes
- [Not a source either](https://example.org/notes)
"""
SOURCES_BLOCK = """

Sources:
- Journal paper: https://example.org/paper
- Lab press release: https://example.org/press
- Plain text entry, accessed 2026-07-01
- Preprint: https://example.org/preprint?a=1&b=2 (v2)"""


def put_brief(work, text, stamp=None):
    """brief.md as the pipeline leaves it, with the stamp of the brief the script was written
    with: by default this one, read back the way `main` reads a brief file."""
    (work / "brief.md").write_text(text, encoding="utf-8")
    if stamp is None:
        stamp = brief_key((work / "brief.md").read_text(encoding="utf-8-sig"))
    (work / "brief.key").write_text(stamp)


def sources(tmp_path, text):
    put_brief(tmp_path, text)
    return metadata.brief_sources(tmp_path)


def test_sources_turn_links_into_plain_text_and_stop_at_the_next_section(tmp_path):
    assert sources(tmp_path, SOURCES_BRIEF) == SOURCES_BLOCK.split("Sources:\n", 1)[1]


@pytest.mark.parametrize("text, expected", [
    ("## Sources\n- [A](https://a.org)", "- A: https://a.org"),                 # section runs to the end of the file
    ("## Sources  \n* one\n* two\n", "- one\n- two"),                          # trailing spaces on the heading
    ("## Sources\r\n- [A](https://a.org)\r\n", "- A: https://a.org"),
    ("﻿## Sources\n- a\n", "- a"),                                         # a byte order mark
    ("## Sources and notes\n- x\n", ""),                                       # not that heading
    ("### Sources\n- x\n", ""),
    ("## Facts\n- [A](https://a.org)\n", ""),                                  # no sources section
    ("## Sources\n\n\n## Notes\n- x\n", ""),                                   # an empty one
    # the section ends at any heading of level 1 or 2...
    ("## Sources\n- a\n# Appendix\n- b\n", "- a"),
    ("## Sources\n- a\n## Notes\n- b\n", "- a"),
    # ...but not at deeper ones, which are skipped
    ("## Sources\n### Papers\n- a\n#### Press\n- b\n", "- a\n- b"),
    # HTML comments: a note, one after an entry, a multi-line one hiding entries and a heading
    ("## Sources\n<!-- add the preprint -->\n- a\n", "- a"),
    ("## Sources\n- a <!-- check the year -->\n", "- a"),
    ("## Sources\n- a\n<!--\n- hidden\n## Notes\n-->\n- b\n", "- a\n- b"),
    ("<!--\n## Sources\n- hidden\n-->\n", ""),
    # a wrapped entry is one entry; an indented bullet is its own
    ("## Sources\n- [Paper](https://a.org), Nature,\n  vol. 5, p. 1\n- b\n",
     "- Paper: https://a.org, Nature, vol. 5, p. 1\n- b"),
    ("## Sources\n- a\n    continued here\n    and here\n", "- a continued here and here"),
    ("## Sources\n- a\n  - nested\n", "- a\n- nested"),
    ("## Sources\n- a\ncontinued\n", "- a\n- continued"),            # not indented: its own entry
    # horizontal rules, code fences and table rows are not entries
    ("## Sources\n- a\n---\n- b\n***\n___\n- c\n- - -\n* * *\n", "- a\n- b\n- c"),
    ("## Sources\n```text\n- a\n```\n~~~\n- b\n", "- a\n- b"),
    ("## Sources\n| Title | URL |\n|---|---|\n| x | y |\n  | indented |\n- a\n", "- a"),
    ("## Sources\n- ---not a rule\n- **bold** entry\n", "- ---not a rule\n- **bold** entry"),
    # every list marker; a number or a year that is not one stays
    ("## Sources\n+ a\n1. b\n2) c\n10. d\n", "- a\n- b\n- c\n- d"),
    ("## Sources\n- 1.5 sigma result\n2026 survey\n", "- 1.5 sigma result\n- 2026 survey"),
])
def test_sources_section_rules(tmp_path, text, expected):
    assert sources(tmp_path, text) == expected


def test_no_brief_in_the_build_folder_means_no_sources(tmp_path, capsys):
    assert metadata.brief_sources(tmp_path) == ""
    assert capsys.readouterr().out == "", "nothing to say about a brief that isn't there"


NOT_THIS_BRIEF = "isn't the brief this script was written with; leaving Sources out of the description"


def test_sources_come_from_the_brief_the_stamp_names(tmp_path, capsys):
    put_brief(tmp_path, BRIEF)

    assert metadata.brief_sources(tmp_path) == "- Journal paper: https://example.org/paper"
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("stamp", [None, "none", brief_key(OTHER_BRIEF)], ids=["no stamp", "none", "another brief"])
def test_a_brief_the_stamp_does_not_name_gives_no_sources_and_says_so(tmp_path, capsys, stamp):
    put_brief(tmp_path, BRIEF)
    if stamp is None:
        (tmp_path / "brief.key").unlink()
    else:
        (tmp_path / "brief.key").write_text(stamp)

    assert metadata.brief_sources(tmp_path) == ""

    out = capsys.readouterr().out
    assert out.count(NOT_THIS_BRIEF) == 1 and out.count("\n") == 1, "one plain line"


@pytest.fixture
def timings_stubbed(monkeypatch):
    """Clip and audio timings stubbed (test_metadata.py measures the real ones): each scene
    starts 20 s after the last, so a chapter of two scenes is 40 s."""
    monkeypatch.setattr(metadata, "scene_starts",
                        lambda work, manifest: ([20.0 * i for i in range(len(manifest))], 20.0 * len(manifest)))
    monkeypatch.setattr(metadata, "subtitles", lambda *args: "")


@pytest.fixture
def film(tmp_path, timings_stubbed):
    """What write_metadata reads and nothing more: a script of six scenes in three chapters, its
    outline and manifest."""
    scenes = [{"id": i + 1, "chapter": i // 2 + 1, "concept": f"c{i}", "narration": "A calm line."}
              for i in range(6)]
    save_json(tmp_path / "script.json", {"title": "Starlight", "scenes": scenes})
    save_json(tmp_path / "manifest.json", {"title": "Starlight", "scenes": [{"id": s["id"]} for s in scenes]})
    save_json(tmp_path / "outline.json", {"title": "Starlight", "chapters": [
        {"number": n, "title": t, "summary": "s", "key_ideas": [], "minutes": 1}
        for n, t in enumerate(("Starlight", "Distance", "Gravity"), 1)]})
    return tmp_path


def test_sources_follow_the_chapter_list_in_the_description(film):
    plain = metadata.write_metadata(film)["description"]
    assert plain.splitlines()[-3:] == ["00:00 Starlight", "00:40 Distance", "01:20 Gravity"]
    put_brief(film, SOURCES_BRIEF)

    meta = metadata.write_metadata(film)

    assert meta["description"] == plain + SOURCES_BLOCK
    assert json.loads((film / "metadata.json").read_text())["description"] == meta["description"]


def test_a_brief_without_sources_or_no_brief_leaves_the_description_unchanged(film):
    plain = metadata.write_metadata(film)["description"]
    put_brief(film, "## Facts\n- [A](https://a.org)\n")
    assert metadata.write_metadata(film)["description"] == plain
    (film / "brief.md").unlink()
    assert metadata.write_metadata(film)["description"] == plain
    assert "Sources" not in plain


def test_sources_go_in_even_without_a_chapter_list_and_are_made_youtube_safe(film):
    script = json.loads((film / "script.json").read_text())
    for scene in script["scenes"]:
        scene["chapter"] = 1
    save_json(film / "script.json", script)
    put_brief(film, "## Sources\n- <b>Paper</b> [A](https://a.org)\n")

    description = metadata.write_metadata(film)["description"]

    assert description == config.YOUTUBE_DESCRIPTION + "\n\nSources:\n- bPaper/b A: https://a.org"
    assert "<" not in description and ">" not in description


def test_a_description_pushed_over_the_limit_by_the_sources_still_raises(film):
    metadata.write_metadata(film)  # fits without them
    entries = "".join(f"- [Paper {i}](https://example.org/papers/{i})\n" for i in range(200))
    put_brief(film, "## Sources\n" + entries)

    with pytest.raises(ValueError, match="5000 bytes"):
        metadata.write_metadata(film)


def test_a_brief_the_script_was_not_written_with_adds_no_sources_to_the_description(film, capsys):
    plain = metadata.write_metadata(film)["description"]
    put_brief(film, SOURCES_BRIEF, stamp="none")

    assert metadata.write_metadata(film)["description"] == plain
    assert NOT_THIS_BRIEF in capsys.readouterr().out


def test_an_abandoned_runs_brief_stays_out_of_the_description_of_a_film_written_without_one(
        tmp_path, monkeypatch, calls, build, capsys, timings_stubbed):
    run_cli(monkeypatch, "topic", "--brief", brief_file(tmp_path, BRIEF), "--script-only")
    for name in SCRIPT_FILES:  # the script is thrown away; the brief and its stamp are left behind
        (build / name).unlink()
    assert (build / "brief.md").exists() and (build / "brief.key").exists()

    n = len(calls)

    run_cli(monkeypatch, "topic", "--script-only")  # no refusal: no script file exists; written without a brief

    assert len(calls) > n and all("Research brief" not in p for _, _, p in calls[n:])
    assert (build / "brief.key").read_text() == "none"
    assert "## Sources" in (build / "brief.md").read_text(), "the old brief is still lying there"
    save_json(build / "manifest.json", {"title": "T", "scenes": [{"id": 1}, {"id": 2}]})
    capsys.readouterr()

    meta = metadata.write_metadata(build)

    assert meta["description"] == config.YOUTUBE_DESCRIPTION
    assert NOT_THIS_BRIEF in capsys.readouterr().out
