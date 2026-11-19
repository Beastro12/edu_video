"""P3-7: scripts/make_pilot.py. `make` turns chapters of a reviewed script into a seed folder for a pilot
run; `copy` hands the pilot's paid audio/ and visuals/ to the full film's build without ever replacing
a file. Nothing here calls a paid API: runs are pipeline.main() end to end on the fakes of
pilot_world.py (pictures through the real SDK on a mock transport), or a pilot build made by hand.

The review-log side of copy (the finished-pilot gate, the per-file check, the races with a running
pilot) is in test_pilot_reviews.py."""
import os
import shlex
import signal
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest
from pilot_world import (  # noqa: F401
    BRIEF,
    CHAPTERS,
    OUTLINE,
    make_source,
    pilot_fixture,
    tree,
    world_fixture,
)

import config
import ledger
import pipeline
from scripts import make_pilot
from utils import brief_key, load_json, save_json


def run(*argv):
    return make_pilot.main(list(argv))


def copy(*argv):
    return run("copy", "pilot demo", "the film", *argv)


class Crash(BaseException):
    """A process stopped between two writes (not an Exception: nothing may swallow it)."""


# --- make ------------------------------------------------------------------------------------------

def test_make_writes_the_chapters_then_the_outline_then_the_brief_then_its_key_last(tmp_path, monkeypatch):
    src = make_source(tmp_path)
    monkeypatch.setattr(make_pilot, "TOPICS", tmp_path / "topics")
    order = []
    real = make_pilot.atomic_output

    @contextmanager
    def spy(path):
        with real(path) as tmp:
            yield tmp
        order.append(Path(path).name)

    monkeypatch.setattr(make_pilot, "atomic_output", spy)
    out = tmp_path / "seed"
    assert run("make", str(out), "--from", str(src), "--chapters", "2,1") == 0
    assert order == ["chapter_01.json", "chapter_02.json", "outline.json", "brief.md", "brief.key"]
    assert sorted(p.name for p in out.iterdir()) == sorted(order), "no other file, no .partial- leftover"


def test_make_keeps_the_chosen_chapters_in_outline_order_and_every_other_key(tmp_path):
    src = make_source(tmp_path)
    outline = {**OUTLINE, "note": "kept", "chapters": [*OUTLINE["chapters"], {"number": 3, "title": "Three", "minutes": 2,
                                                                              "extra": "kept too"}]}
    save_json(src / "outline.json", outline)
    save_json(src / "chapter_03.json", CHAPTERS[1])
    out = tmp_path / "seed"
    assert run("make", str(out), "--from", str(src), "--chapters", "3,1") == 0
    kept = load_json(out / "outline.json")
    assert [c["number"] for c in kept["chapters"]] == [1, 3], "in the outline's order, not the order given"
    assert kept["chapters"][1] == outline["chapters"][2] and kept["note"] == "kept" and kept["title"] == "The demo film"
    assert sorted(p.name for p in out.glob("chapter_*")) == ["chapter_01.json", "chapter_03.json"]
    assert (out / "chapter_03.json").read_bytes() == (src / "chapter_03.json").read_bytes()


def test_the_brief_is_a_byte_copy_and_its_key_is_the_one_the_pipeline_makes(tmp_path):
    bom = "﻿# Brief with a byte order mark\r\n\r\n## Sources\r\n- [x](https://example.org)\r\n"
    src = make_source(tmp_path, brief="x")
    (src / "brief.md").write_bytes(bom.encode("utf-8"))
    (src / "brief.key").write_text(brief_key(bom.encode("utf-8").decode("utf-8-sig")))
    out = tmp_path / "seed"
    assert run("make", str(out), "--from", str(src), "--chapters", "1") == 0
    assert (out / "brief.md").read_bytes() == (src / "brief.md").read_bytes(), "bytes, BOM and line ends included"
    text = (out / "brief.md").read_text(encoding="utf-8-sig")
    assert (out / "brief.key").read_text() == brief_key(text)
    assert pipeline._load_seed(out, text), "the pipeline accepts what was written"


@pytest.mark.parametrize("make_out", ["topics_child", "build_child", "topics_itself"])
def test_make_refuses_an_out_under_topics_or_build(tmp_path, monkeypatch, capsys, make_out):
    src = make_source(tmp_path)
    monkeypatch.setattr(make_pilot, "TOPICS", tmp_path / "topics")
    out = {"topics_child": tmp_path / "topics" / "pilot", "build_child": Path(config.BUILD_DIR) / "pilot",
           "topics_itself": tmp_path / "topics"}[make_out]
    before = tree(tmp_path)
    assert run("make", str(out), "--from", str(src), "--chapters", "1") == 2
    err = capsys.readouterr().err
    assert err.startswith("make_pilot.py: ") and "outside topics/ and build/" in err
    assert tree(tmp_path) == before, "nothing written, no folder made"


@pytest.mark.parametrize("target", ["topics", "build"])
def test_make_refuses_a_symlink_into_topics_or_build_and_a_dangling_link(tmp_path, monkeypatch, capsys, target):
    src = make_source(tmp_path)
    monkeypatch.setattr(make_pilot, "TOPICS", tmp_path / "topics")
    inside = tmp_path / target / "somewhere"
    inside.mkdir(parents=True)
    link = tmp_path / "link"
    link.symlink_to(inside)
    assert run("make", str(link), "--from", str(src), "--chapters", "1") == 2
    assert "inside" in capsys.readouterr().err
    assert list(inside.iterdir()) == [], "nothing was written through the link"
    dangling = tmp_path / "dangling"
    dangling.symlink_to(tmp_path / "nowhere" / "x")
    assert run("make", str(dangling), "--from", str(src), "--chapters", "1") == 2
    assert "dangling" in capsys.readouterr().err
    assert not (tmp_path / "nowhere").exists(), "the link was not followed to make its target"
    dangling_into = tmp_path / "into"
    dangling_into.symlink_to(Path(config.BUILD_DIR) / "later")
    assert run("make", str(dangling_into), "--from", str(src), "--chapters", "1") == 2
    assert not (Path(config.BUILD_DIR) / "later").exists()


def test_make_refuses_an_out_that_is_not_empty_or_is_a_file_and_says_what_to_do(tmp_path, capsys):
    src = make_source(tmp_path)
    full = tmp_path / "full"
    full.mkdir()
    (full / "mine.txt").write_text("keep")
    assert run("make", str(full), "--from", str(src), "--chapters", "1") == 2
    assert "not empty" in capsys.readouterr().err
    assert (full / "mine.txt").read_text() == "keep" and sorted(p.name for p in full.iterdir()) == ["mine.txt"]
    (tmp_path / "afile").write_text("x")
    assert run("make", str(tmp_path / "afile"), "--from", str(src), "--chapters", "1") == 2
    assert (tmp_path / "afile").read_text() == "x"
    empty = tmp_path / "empty"
    empty.mkdir()
    assert run("make", str(empty), "--from", str(src), "--chapters", "1") == 0, "an existing empty folder is fine"


@pytest.mark.parametrize("chapters,message", [
    ("3", "chapter 3 is not in"), ("1,9", "chapter 9 is not in"), ("x", "give chapter numbers"),
    ("1,,2", "give chapter numbers"), ("", "give chapter numbers")])
def test_make_refuses_a_chapter_the_outline_lacks_and_writes_nothing(tmp_path, capsys, chapters, message):
    src = make_source(tmp_path)
    before = tree(tmp_path)
    assert run("make", str(tmp_path / "seed"), "--from", str(src), "--chapters", chapters) == 2
    assert message in capsys.readouterr().err
    assert tree(tmp_path) == before


def test_make_refuses_a_chapter_whose_file_is_missing(tmp_path, capsys):
    src = make_source(tmp_path)
    (src / "chapter_02.json").unlink()
    assert run("make", str(tmp_path / "seed"), "--from", str(src), "--chapters", "2") == 2
    assert "chapter_02.json is missing" in capsys.readouterr().err
    assert not (tmp_path / "seed").exists()
    assert run("make", str(tmp_path / "seed"), "--from", str(src), "--chapters", "1") == 0, "the other chapter is fine"


def test_make_checks_the_source_with_the_pipelines_own_check_and_says_its_words(tmp_path, capsys):
    src = make_source(tmp_path)
    (src / "brief.key").write_text("not the key")
    with pytest.raises(ValueError) as expected:
        pipeline._load_seed(src, BRIEF)
    assert run("make", str(tmp_path / "seed"), "--from", str(src), "--chapters", "1") == 2
    assert capsys.readouterr().err.strip() == f"make_pilot.py: {expected.value}"
    assert not (tmp_path / "seed").exists()
    (src / "brief.key").write_text(brief_key(BRIEF))
    chapter = load_json(src / "chapter_01.json")
    chapter[0]["visual_type"] = "hologram"
    save_json(src / "chapter_01.json", chapter)
    assert run("make", str(tmp_path / "seed"), "--from", str(src), "--chapters", "2") == 2
    assert "chapter_01.json, scene 1" in capsys.readouterr().err, "the pipeline refuses the source's other chapter too"


@pytest.mark.parametrize("brief", [None, b"\xff\xfe\x00bad", b"  \n"])
def test_make_refuses_a_source_brief_the_pipeline_would_not_read(tmp_path, capsys, brief):
    src = make_source(tmp_path)
    if brief is None:
        (src / "brief.md").unlink()
    else:
        (src / "brief.md").write_bytes(brief)
    assert run("make", str(tmp_path / "seed"), "--from", str(src), "--chapters", "1") == 2
    assert "brief.md" in capsys.readouterr().err and not (tmp_path / "seed").exists()


def test_a_stop_at_each_make_write_leaves_a_folder_the_pipeline_refuses_and_a_rerun_finishes(tmp_path, monkeypatch, capsys):
    src = make_source(tmp_path)
    real = make_pilot.atomic_output
    writes = []

    @contextmanager
    def counting(path):
        with real(path) as tmp:
            yield tmp
        writes.append(Path(path).name)

    monkeypatch.setattr(make_pilot, "atomic_output", counting)
    assert run("make", str(tmp_path / "reference"), "--from", str(src), "--chapters", "1,2") == 0
    expected = tree(tmp_path / "reference")
    assert len(writes) == 5

    for stop_at in range(1, len(writes) + 1):
        out = tmp_path / f"seed{stop_at}"
        seen = []

        @contextmanager
        def stopping(path, seen=seen, stop_at=stop_at):
            with real(path) as tmp:
                yield tmp
                seen.append(path)
                if len(seen) == stop_at:
                    raise Crash  # after the file was written, before it was moved into place
        monkeypatch.setattr(make_pilot, "atomic_output", stopping)
        with pytest.raises(Crash):
            run("make", str(out), "--from", str(src), "--chapters", "1,2")
        left = sorted(p.name for p in out.iterdir())
        assert not [n for n in left if ".partial-" in n], f"stop {stop_at}: no half file"
        assert left == sorted(writes[:stop_at - 1]), f"stop {stop_at}: exactly the writes before it"
        with pytest.raises(ValueError):  # brief.key is the last write, so the pipeline refuses every earlier state
            pipeline._load_seed(out, BRIEF)
        monkeypatch.setattr(make_pilot, "atomic_output", counting)
        if left:
            assert run("make", str(out), "--from", str(src), "--chapters", "1,2") == 2, "a leftover folder is refused"
            assert "not empty" in capsys.readouterr().err
        for p in out.iterdir():
            p.unlink()  # the owner empties it, as the message says
        assert run("make", str(out), "--from", str(src), "--chapters", "1,2") == 0
        assert tree(out) == expected, f"stop {stop_at}: the rerun made the same folder"


def test_make_checks_what_it_wrote_with_the_pipelines_check(tmp_path, monkeypatch, capsys):
    src = make_source(tmp_path)
    monkeypatch.setattr(make_pilot, "brief_key", lambda text: "wrong key")  # a bug in what make stamps
    assert run("make", str(tmp_path / "seed"), "--from", str(src), "--chapters", "1") == 2
    assert "does not pass the pipeline's own check" in capsys.readouterr().err


# --- the commands make prints ------------------------------------------------------------------------

def printed(capsys) -> list[str]:
    return [line.strip() for line in capsys.readouterr().out.splitlines() if line.strip().startswith("python ")]


def test_make_prints_the_pilot_command_and_the_copy_command_with_a_placeholder(tmp_path, monkeypatch, capsys):
    src = make_source(tmp_path)
    monkeypatch.setattr(make_pilot, "TOPICS", tmp_path / "topics")
    out = tmp_path / "seed"
    assert run("make", str(out), "--from", str(src), "--chapters", "1") == 0
    pilot_cmd, copy_cmd = printed(capsys)
    assert pilot_cmd == f'python pipeline.py "pilot demo" --brief "{out.resolve()}/brief.md" --style flat --no-ai-video'
    assert copy_cmd == 'python scripts/make_pilot.py copy "pilot demo" "<film topic>" --from "topics/demo"'


def test_make_prints_the_given_topics_and_style(tmp_path, monkeypatch, capsys):
    src = make_source(tmp_path)
    monkeypatch.setattr(make_pilot, "TOPICS", tmp_path / "topics")
    assert run("make", str(tmp_path / "seed"), "--from", str(src), "--chapters", "1", "--topic", "my pilot",
               "--film-topic", "My Film", "--style", "calm") == 0
    pilot_cmd, copy_cmd = printed(capsys)
    assert pilot_cmd.startswith('python pipeline.py "my pilot" --brief ') and pilot_cmd.endswith("--style calm --no-ai-video")
    assert copy_cmd == 'python scripts/make_pilot.py copy "my pilot" "My Film" --from "topics/demo"'


def test_a_source_outside_topics_is_named_by_its_full_path(tmp_path, capsys):
    src = make_source(tmp_path / "elsewhere", name="my topic")
    assert run("make", str(tmp_path / "seed"), "--from", str(src), "--chapters", "1") == 0
    pilot_cmd, copy_cmd = printed(capsys)
    assert pilot_cmd.startswith('python pipeline.py "pilot my topic" ')
    assert copy_cmd.endswith(f'--from "{src}"')


AWKWARD = ['plain', 'two words', 'a "quoted" one', 'dollar $HOME and $(echo hi)', 'back`tick`', 'back\\slash\\n',
           'mix "a" $b `c` \\d', "it's", 'ünïcode — ok', 'two\nlines', '!bang', '*star* [x] {a,b} ~tilde']


@pytest.mark.parametrize("word", AWKWARD)
def test_every_printed_command_is_double_quoted_shell_words_that_round_trip(word):
    """Through the shell itself: what the user pastes is read back as exactly the words we meant."""
    quoted = make_pilot.q(word)
    assert quoted[0] == quoted[-1] == '"'
    done = subprocess.run(["bash", "-c", f"printf '%s\\0' {quoted}"], capture_output=True, check=True)
    assert done.stdout.decode().split("\0")[:-1] == [word]
    assert shlex.split(quoted, posix=True) or word == ""  # and a parser that knows only quotes still finds one word


@pytest.mark.parametrize("name", ['a "b" $c', "back`tick", "x\\y z"])
def test_the_whole_printed_commands_round_trip_with_awkward_names(tmp_path, monkeypatch, capsys, name):
    src = make_source(tmp_path / "e", name=name)
    out = tmp_path / ('out "1" $x')
    assert run("make", str(out), "--from", str(src), "--chapters", "1", "--film-topic", name + " film") == 0
    pilot_cmd, copy_cmd = printed(capsys)
    for command, expected in ((pilot_cmd, ["python", "pipeline.py", f"pilot {name}", "--brief",
                                           str(out.resolve() / "brief.md"), "--style", "flat", "--no-ai-video"]),
                              (copy_cmd, ["python", "scripts/make_pilot.py", "copy", f"pilot {name}", name + " film",
                                          "--from", str(src)])):
        done = subprocess.run(["bash", "-c", f"printf '%s\\0' {command}"], capture_output=True, check=True)
        assert done.stdout.decode().split("\0")[:-1] == expected


# --- scenario 1: the real topic ----------------------------------------------------------------------

def test_make_on_the_real_topic_then_the_estimate_with_no_keys_prices_the_pilot_and_writes_nothing(
        tmp_path, monkeypatch, capsys):
    real_topic = Path(__file__).resolve().parents[1] / "topics" / "ai-global-workspace"
    out = tmp_path / "pilot-seed"
    assert run("make", str(out), "--from", str(real_topic), "--chapters", "1") == 0
    pilot_cmd, _ = printed(capsys)
    for key in ("ANTHROPIC_API_KEY", "ELEVENLABS_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.setattr(config, key, "")
    argv = shlex.split(pilot_cmd)[1:]  # pipeline.py "topic" --brief ... --style flat --no-ai-video
    assert argv[1] == "pilot ai-global-workspace"
    seen = []
    monkeypatch.setattr(pipeline, "print_estimate", lambda topic, est, worst, style: seen.append((topic, est, style)))
    monkeypatch.setattr(sys, "argv", [*argv, "--estimate"])
    before_seed = tree(out)
    pipeline.main()
    [(topic, est, style)] = seen
    assert topic == "pilot ai-global-workspace" and style == "flat"
    scenes = load_json(real_topic / "chapter_01.json")
    # no Google key: stills and Veo scenes count as Manim renders. No outline or writer call is priced.
    assert est["claude"] == pytest.approx(len(scenes) * ledger.claude_eur(config.CLAUDE_MODEL, *config.EST_MANIM_TOKENS))
    assert est["elevenlabs"] == pytest.approx(ledger.tts_eur(sum(len(sc["narration"]) for sc in scenes)))
    assert est["veo"] == est["images"] == est["thumbnail"] == 0.0
    assert tree(out) == before_seed, "the seed folder is untouched"
    assert not Path(config.BUILD_DIR).exists(), "an estimate writes no build folder, no ledger"
    assert ledger.spent_eur() == 0


# --- scenario 2: a faked pilot, copy, a faked film ---------------------------------------------------

def test_the_film_buys_nothing_the_pilot_bought_and_its_estimate_prices_only_the_rest(world, monkeypatch, capsys):
    out = world.make("1")
    world.run(*world.pilot_argv(out))
    bought = world.paid()
    pilot_dir = world.build / "pilot-demo"
    assert len(bought["tts"]) == 3 and len(bought["review"]) == 2 and len(bought["manim"]) == 1
    assert len(bought["image"]) == 3, "two stills and the thumbnail's picture"
    assert (pilot_dir / "manifest.json").exists()

    assert run("copy", "pilot demo", "the film") == 0
    assert (world.build / "the-film" / "style").read_text() == "flat"
    world.forget()
    estimates = []
    real_print = pipeline.print_estimate
    monkeypatch.setattr(pipeline, "print_estimate", lambda topic, est, worst, style: (
        estimates.append(est), real_print(topic, est, worst, style)))
    world.run(*world.film_argv(), "--estimate")
    assert world.paid() == {"tts": [], "image": [], "review": [], "manim": []}
    rest_chars = sum(len(s["narration"]) for s in CHAPTERS[2])
    est = estimates[0]
    assert est["elevenlabs"] == pytest.approx(ledger.tts_eur(rest_chars)), "only chapter 2's narration"
    assert est["images"] == pytest.approx(2 * ledger.image_eur()), "two pictures; the thumbnail's is the pilot's"
    assert est["thumbnail"] == 0.0
    still_review = 2 * ledger.claude_eur(config.CLAUDE_MODEL, *config.EST_STILL_REVIEW_TOKENS)
    assert est["claude"] == pytest.approx(still_review), "no Manim render, no script call"

    world.run(*world.film_argv())
    film = world.paid()
    assert sorted(film["tts"]) == sorted(s["narration"] for s in CHAPTERS[2]), "exactly chapter 2's lines, none of the pilot's"
    assert sorted(film["review"]) == ["fifth", "fourth"] and film["manim"] == []
    assert len(film["image"]) == 2 and not set(film["image"]) & set(bought["image"]), "no prompt the pilot sent"
    assert any("Composition:" in p for p in bought["image"]), "(the thumbnail's picture was among the pilot's)"
    assert (world.build / "the-film" / "thumbnail.jpg").exists(), "and the film still has its thumbnail"
    assert (world.build / "the-film" / "manifest.json").exists()


def test_without_copy_the_film_buys_everything_again(world):
    out = world.make("1")
    world.run(*world.pilot_argv(out))
    world.forget()
    world.run(*world.film_argv())
    film = world.paid()
    assert len(film["tts"]) == 5 and len(film["review"]) == 4 and len(film["manim"]) == 1
    assert len(film["image"]) == 5, "four stills and the thumbnail's picture, all again"


def test_a_film_run_without_style_after_copy_keeps_the_pilots_style_and_buys_only_the_rest(world, capsys):
    out = world.make("1")
    world.run(*world.pilot_argv(out))
    assert run("copy", "pilot demo", "the film") == 0
    world.forget()
    capsys.readouterr()
    world.run("the film", "--brief", str(world.src / "brief.md"), "--no-ai-video")  # no --style
    assert "• style: flat (recorded in the build)" in capsys.readouterr().out
    assert config.STYLE == "flat"
    assert sorted(world.paid()["tts"]) == sorted(s["narration"] for s in CHAPTERS[2])
    assert world.paid()["manim"] == [] and len(world.paid()["image"]) == 2


def test_an_explicit_other_style_still_wins_on_the_film(world, capsys):
    out = world.make("1")
    world.run(*world.pilot_argv(out))
    assert run("copy", "pilot demo", "the film") == 0
    world.forget()
    capsys.readouterr()
    world.run("the film", "--brief", str(world.src / "brief.md"), "--style", "calm", "--no-ai-video")
    text = capsys.readouterr().out
    assert "• style: calm (--style)" in text and "this build is recorded as flat" in text
    assert (world.build / "the-film" / "style").read_text() == "calm"
    assert len(world.paid()["tts"]) == 2 and len(world.paid()["manim"]) == 1, "the pictures are bought in calm"


def test_the_paid_files_keep_their_names_in_the_film(world):
    """D9: copy moves files between folders; no cache key changes. Every audio and visual file the film's
    manifest names for a pilot scene is a file of the same name the pilot's manifest names."""
    out = world.make("1")
    world.run(*world.pilot_argv(out))
    world.run(*world.film_argv("pilot demo again"))  # control: the film made at the same style without copy
    assert run("copy", "pilot demo", "the film") == 0
    world.run(*world.film_argv())
    pilot_scenes = load_json(world.build / "pilot-demo" / "manifest.json")["scenes"]
    film_scenes = load_json(world.build / "the-film" / "manifest.json")["scenes"]
    for p, f in zip(pilot_scenes, film_scenes, strict=False):
        assert (p["audio"], p["visual"]) == (f["audio"], f["visual"])
    for p in pilot_scenes:
        for key in ("audio", "visual"):
            assert (world.build / "the-film" / p[key]).read_bytes() == (world.build / "pilot-demo" / p[key]).read_bytes()


# --- copy: refusals write nothing --------------------------------------------------------------------

def no_style_record(pilot_):
    return not (pilot_.film / "style").exists()


@pytest.mark.parametrize("args,message", [
    (["", "the film"], "empty build name"),
    (["pilot demo", "!!!"], "empty build name"),
    (["pilot demo", "Pilot  DEMO"], "same build"),
    (["pilot-demo", "pilot demo"], "same build"),
    (["no such pilot", "the film"], "no pilot build"),
])
def test_copy_refuses_a_missing_pilot_the_same_build_and_an_empty_slug(pilot, capsys, args, message):
    before = tree(pilot.film.parent)
    assert run("copy", *args) == 2
    err = capsys.readouterr().err
    assert err.startswith("make_pilot.py: ") and message in err
    assert tree(pilot.film.parent) == before and not pilot.film.exists()


def style_record_cases():
    return {"missing": None, "empty": "", "blank": " \n", "not a style": "vector", "non-utf8": b"fl\xffat",
            "directory": "dir", "broken link": "link"}


@pytest.mark.parametrize("case", list(style_record_cases()))
def test_copy_refuses_a_pilot_whose_style_record_is_missing_or_invalid_and_names_the_way_out(pilot, capsys, case):
    record = pilot.work / "style"
    record.unlink()
    value = style_record_cases()[case]
    if value == "dir":
        record.mkdir()
    elif value == "link":
        record.symlink_to(pilot.work / "nowhere")
    elif isinstance(value, bytes):
        record.write_bytes(value)
    elif value is not None:
        record.write_text(value)
    before = tree(pilot.film.parent)
    assert run("copy", "pilot demo", "the film") == 2
    err = capsys.readouterr().err
    assert str(record) in err
    assert "rerun the pilot once with its --style (it buys nothing already made), or write the style name into " \
           f"{record}" in err
    assert tree(pilot.film.parent) == before and not pilot.film.exists()


def test_copy_refuses_a_manifest_whose_style_differs_from_the_record_and_offers_both_ways(pilot, capsys):
    pilot.set_manifest(style="calm")
    assert run("copy", "pilot demo", "the film") == 2
    err = capsys.readouterr().err
    assert "manifest.json says the pilot was finished in calm" in err and "style says flat" in err
    assert "or finish the pilot in flat (rerun with --style flat)" in err
    assert "rerun the pilot once with its --style (it buys nothing already made), or write the style name into" in err
    assert not pilot.film.exists()


def test_a_manifest_with_no_style_field_is_not_a_mismatch(pilot):
    manifest = load_json(pilot.work / "manifest.json")
    del manifest["style"]
    save_json(pilot.work / "manifest.json", manifest)
    assert run("copy", "pilot demo", "the film") == 0


def test_copy_refuses_a_film_recorded_in_another_style_and_says_how_to_change_it(pilot, capsys):
    pilot.film.mkdir()
    (pilot.film / "style").write_text("calm")
    before = tree(pilot.film)
    assert run("copy", "pilot demo", "the film") == 2
    err = capsys.readouterr().err
    assert f"to make the film in flat, delete {pilot.film / 'style'} (its calm pictures stay on disk) and copy again" in err
    assert tree(pilot.film) == before, "nothing written"


@pytest.mark.parametrize("bad", ["not a style", b"\xff\xfe", "dir"])
def test_copy_refuses_a_film_style_record_it_cannot_read(pilot, capsys, bad):
    pilot.film.mkdir()
    record = pilot.film / "style"
    if bad == "dir":
        record.mkdir()
    elif isinstance(bad, bytes):
        record.write_bytes(bad)
    else:
        record.write_text(bad)
    assert run("copy", "pilot demo", "the film") == 2
    assert "is not a style record" in capsys.readouterr().err
    assert not (pilot.film / "audio").exists()


def test_a_film_with_the_same_style_record_is_left_untouched_and_copy_goes_on(pilot, capsys):
    pilot.film.mkdir()
    (pilot.film / "style").write_text("flat\n")
    assert run("copy", "pilot demo", "the film") == 0
    assert (pilot.film / "style").read_text() == "flat\n", "not rewritten"
    assert "already says flat" in capsys.readouterr().out


@pytest.mark.parametrize("where", ["film", "audio", "visuals"])
@pytest.mark.parametrize("kind", ["file", "broken link", "link into topics"])
def test_copy_refuses_a_film_folder_that_is_a_file_or_resolves_into_topics(pilot, tmp_path, capsys, where, kind):
    target = pilot.film if where == "film" else pilot.film / where
    target.parent.mkdir(parents=True, exist_ok=True)
    topic = tmp_path / "topics" / "linked"
    topic.mkdir()
    if kind == "file":
        target.write_text("x")
    elif kind == "broken link":
        target.symlink_to(tmp_path / "nowhere")
    else:
        target.symlink_to(topic)
    before = tree(tmp_path / "topics"), tree(pilot.film.parent), os.path.lexists(pilot.film / "style")
    assert run("copy", "pilot demo", "the film") == 2
    assert ("is not a folder" if kind != "link into topics" else "is inside topics/") in capsys.readouterr().err
    assert (tree(tmp_path / "topics"), tree(pilot.film.parent), os.path.lexists(pilot.film / "style")) == before
    assert list(topic.iterdir()) == [], "nothing was written into topics/"


def test_a_film_whose_audio_is_a_link_elsewhere_is_allowed(pilot, tmp_path):
    """A build folder on another disk is a normal setup; only topics/ is off limits."""
    elsewhere = tmp_path / "other-disk"
    elsewhere.mkdir()
    pilot.film.mkdir()
    (pilot.film / "audio").symlink_to(elsewhere)
    assert run("copy", "pilot demo", "the film") == 0
    assert sorted(p.name for p in elsewhere.iterdir()) == sorted(p.name for p in (pilot.work / "audio").iterdir())


def test_copy_refuses_a_disk_without_hard_links_before_writing_anything(pilot, monkeypatch, capsys):
    def no_links(src, dst, **kw):
        raise OSError(1, "Operation not permitted")

    monkeypatch.setattr(os, "link", no_links)
    assert run("copy", "pilot demo", "the film") == 2
    err = capsys.readouterr().err
    assert "cannot make hard links" in err and "no fallback" in err
    assert not pilot.film.exists(), "not even the style record"
    assert not list(pilot.film.parent.glob(".link-probe-*")), "the probe removed what it made"


def test_the_hard_link_probe_runs_in_the_nearest_existing_parent_and_removes_only_its_own_files(pilot, tmp_path):
    made = []
    real_link = os.link

    def watching(src, dst, **kw):
        made.append((Path(src), Path(dst)))
        real_link(src, dst, **kw)

    other = pilot.film.parent / ".link-probe-someone-else"
    other.write_text("not mine")
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(os, "link", watching)
        assert make_pilot.hard_link_problem(pilot.film / "audio") is None
    [(a, b)] = made
    assert a.parent == pilot.film.parent, "the nearest existing parent, the film not being there"
    assert a.name.startswith(f".link-probe-{os.getpid()}-") and not a.exists() and not b.exists()
    assert other.read_text() == "not mine", "only what this run made is removed"


# --- copy: the walk ----------------------------------------------------------------------------------

def test_copy_carries_every_regular_file_of_audio_and_visuals_and_nothing_else(pilot, capsys):
    (pilot.work / "visuals" / "sub").mkdir()
    (pilot.work / "visuals" / "sub" / "deep.png").write_bytes(b"deep")
    assert run("copy", "pilot demo", "the film") == 0
    expected = pilot.files()
    assert expected and "visuals/sub/deep.png" in expected
    assert {k: v for k, v in tree(pilot.film).items() if v is not None and k != "style"} == expected
    for never in ("clips", "failures.json", "manifest.json", "script.json", "script.meta.json", "outline.json",
                  "metadata.json", "narrated.mp4"):
        assert not (pilot.film / never).exists(), never
    out = capsys.readouterr().out
    assert "failures.json is not carried over (a pilot failure is asked again)." in out
    assert "visuals/manim_media: Manim's scratch folder, not paid output" in out
    assert not (pilot.film / "visuals" / "manim_media").exists()


def test_copy_skips_partial_files_symlinks_and_symlinked_folders_and_says_why(pilot, tmp_path, capsys):
    visuals, audio = pilot.work / "visuals", pilot.work / "audio"
    (audio / "x.partial-1-2.mp3").write_bytes(b"half")
    (visuals / "still_aa.partial-9-9.png").write_bytes(b"half")
    (audio / "link.mp3").symlink_to(next(iter(pilot.audio.values())))
    (visuals / "broken.png").symlink_to(tmp_path / "nowhere")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.png").write_bytes(b"secret")
    (visuals / "folder-link").symlink_to(outside)
    assert run("copy", "pilot demo", "the film") == 0
    out = capsys.readouterr().out
    present = {p.name for p in pilot.film.rglob("*")}
    assert present.isdisjoint({"x.partial-1-2.mp3", "still_aa.partial-9-9.png", "link.mp3", "broken.png",
                               "folder-link", "secret.png"})
    for line in ("audio/x.partial-1-2.mp3: a file still being made", "visuals/still_aa.partial-9-9.png: a file still being made",
                 "audio/link.mp3: a symlink", "visuals/broken.png: a symlink", "visuals/folder-link: a symlink"):
        assert line in out
    assert "skipped visuals/manim_media" in out


def test_a_symlinked_audio_or_visuals_folder_of_the_pilot_is_not_followed(pilot, tmp_path, capsys):
    real = tmp_path / "real-audio"
    (pilot.work / "audio").rename(real)
    (pilot.work / "audio").symlink_to(real)
    assert run("copy", "pilot demo", "the film") == 2, "the manifest's audio files are not plain files then"
    assert "has not finished" in capsys.readouterr().err
    assert not pilot.film.exists()


def test_a_veo_pilot_says_the_film_command_will_not_use_its_clips(pilot, capsys):
    (pilot.work / "visuals" / "ai_0123456789abcdef.mp4").write_bytes(b"veo")
    assert run("copy", "pilot demo", "the film") == 0
    out = capsys.readouterr().out
    assert "VEO_NOTE:" in out and "--no-ai-video, so it will not use them" in out
    assert (pilot.film / "visuals" / "ai_0123456789abcdef.mp4").exists(), "copied all the same: it was paid for"


def test_a_pilot_with_no_veo_prints_no_veo_note(pilot, capsys):
    assert run("copy", "pilot demo", "the film") == 0
    assert "VEO_NOTE" not in capsys.readouterr().out


def test_a_manifest_scene_of_kind_ai_also_brings_the_veo_note(pilot, capsys):
    manifest = load_json(pilot.work / "manifest.json")
    manifest["scenes"][1]["kind"] = "ai"
    save_json(pilot.work / "manifest.json", manifest)
    assert run("copy", "pilot demo", "the film") == 0
    assert "VEO_NOTE:" in capsys.readouterr().out


def test_copy_prints_the_film_command_with_the_source_the_style_and_no_ai_video(pilot, capsys):
    assert run("copy", "pilot demo", 'the "big" film') == 0
    assert 'python pipeline.py "the \\"big\\" film" --brief "topics/demo/brief.md" --style flat --no-ai-video' in \
        capsys.readouterr().out, "the source found by the content of the pilot's kept brief"


def test_from_names_the_source_and_overrides_the_search(pilot, tmp_path, capsys):
    (tmp_path / "topics" / "other").mkdir()
    (tmp_path / "topics" / "other" / "brief.md").write_text(BRIEF)  # identical to demo's: a search is ambiguous
    assert run("copy", "pilot demo", "the film") == 2
    assert "2 topics hold the pilot's brief (demo, other)" in capsys.readouterr().err
    assert not pilot.film.exists()
    assert run("copy", "pilot demo", "the film", "--from", "other") == 0, "a bare name is a folder of topics/"
    assert '--brief "topics/other/brief.md"' in capsys.readouterr().out
    assert run("copy", "pilot demo", "the film", "--from", str(tmp_path / "topics" / "demo")) == 0


def test_no_matching_topic_and_a_topic_brief_that_is_not_utf8_are_refused_with_the_way_out(pilot, tmp_path, capsys):
    (tmp_path / "topics" / "demo" / "brief.md").write_text("some other brief")
    assert run("copy", "pilot demo", "the film") == 2
    assert "is the pilot's brief: say the source with --from" in capsys.readouterr().err
    (tmp_path / "topics" / "bad").mkdir()
    (tmp_path / "topics" / "bad" / "brief.md").write_bytes(b"\xff\xfe\x00")
    assert run("copy", "pilot demo", "the film") == 2
    err = capsys.readouterr().err
    assert str(tmp_path / "topics" / "bad" / "brief.md") in err and "--from" in err and "UTF-8" in err
    assert not pilot.film.exists()
    assert run("copy", "pilot demo", "the film", "--from", "demo") == 0, "--from skips the search"


def test_a_pilot_with_no_kept_brief_needs_from(pilot, capsys):
    (pilot.work / "brief.md").unlink()
    assert run("copy", "pilot demo", "the film") == 2
    assert "say it with --from" in capsys.readouterr().err
    assert not pilot.film.exists()


# --- copy: never replaces ------------------------------------------------------------------------------

def test_copying_twice_skips_everything_the_second_time(pilot, capsys):
    assert run("copy", "pilot demo", "the film") == 0
    first = tree(pilot.film)
    capsys.readouterr()
    assert run("copy", "pilot demo", "the film") == 0
    out = capsys.readouterr().out
    assert "0 files copied" in out and "copied  " not in out
    assert tree(pilot.film) == first
    assert out.count("already in the film") == len(pilot.files())


def test_a_file_the_film_already_has_is_never_replaced_even_with_other_bytes(pilot):
    pilot.film.mkdir()
    (pilot.film / "audio").mkdir()
    name = next(iter(pilot.audio.values())).name
    (pilot.film / "audio" / name).write_bytes(b"the film's own")
    assert run("copy", "pilot demo", "the film") == 0
    assert (pilot.film / "audio" / name).read_bytes() == b"the film's own"


def appears_before_the_link(monkeypatch, name, content=b"someone else's"):
    """The race: just as copy links `name` into place, another process has made it."""
    real = os.link

    def link(src, dst, **kw):
        if Path(dst).name == name:
            Path(dst).write_bytes(content)
        return real(src, dst, **kw)

    monkeypatch.setattr(os, "link", link)


@pytest.mark.parametrize("kind", ["audio", "picture", "log"])
def test_a_file_appearing_mid_copy_is_not_replaced(pilot, monkeypatch, capsys, kind):
    name = {"audio": pilot.audio[1].name, "picture": pilot.visual[1].name, "log": pilot.log[1].name}[kind]
    appears_before_the_link(monkeypatch, name)
    assert run("copy", "pilot demo", "the film") == 0
    sub = "audio" if kind == "audio" else "visuals"
    assert (pilot.film / sub / name).read_bytes() == b"someone else's"
    assert f"{sub}/{name}: already in the film" in capsys.readouterr().out
    assert not [p for p in pilot.film.rglob("*") if ".partial-" in p.name], "and no temporary file is left"


def test_a_dangling_link_where_a_file_goes_is_not_replaced_either(pilot, tmp_path):
    (pilot.film / "audio").mkdir(parents=True)
    name = pilot.audio[1].name
    (pilot.film / "audio" / name).symlink_to(tmp_path / "nowhere")
    assert run("copy", "pilot demo", "the film") == 0
    assert (pilot.film / "audio" / name).is_symlink() and not (tmp_path / "nowhere").exists()


def test_a_style_record_appearing_mid_copy_is_refused_when_it_differs_and_goes_on_when_it_is_the_same(
        pilot, monkeypatch, capsys):
    other = pilot.film / "style"
    with monkeypatch.context() as mp:
        appears_before_the_link(mp, "style", b"calm")
        assert run("copy", "pilot demo", "the film") == 2
    assert "appeared meanwhile and says 'calm'" in capsys.readouterr().err
    assert other.read_text() == "calm" and not (pilot.film / "audio").exists(), "nothing else was written"
    other.unlink()
    with monkeypatch.context() as mp:
        appears_before_the_link(mp, "style", b"flat")
        assert run("copy", "pilot demo", "the film") == 0
    assert other.read_bytes() == b"flat" and (pilot.film / "audio").exists()


def test_a_fallback_that_replaces_is_not_there_to_find(pilot, monkeypatch):
    """The tool has no code path that replaces a file: os.replace, os.rename and shutil.move are never
    called on a film file, whatever else fails."""
    calls = []
    for owner, name in ((os, "replace"), (os, "rename")):
        monkeypatch.setattr(owner, name, lambda *a, _n=name, **k: calls.append(_n))
    monkeypatch.setattr("shutil.move", lambda *a, **k: calls.append("move"))
    appears_before_the_link(monkeypatch, pilot.audio[1].name)
    assert run("copy", "pilot demo", "the film") == 0
    assert calls == []


# --- copy: a stop at every point -----------------------------------------------------------------------

def plain_files(film: Path) -> dict[str, bytes]:
    return {k: v for k, v in tree(film).items() if v is not None}


def partials(film: Path) -> list[str]:
    return [str(p) for p in film.rglob("*") if ".partial-" in p.name]


@pytest.fixture
def reference(pilot, tmp_path):
    """What a clean copy leaves, and how many files it makes."""
    assert run("copy", "pilot demo", "reference film") == 0
    ref = tmp_path / "build" / "reference-film"
    return {"files": plain_files(ref), "n": len(plain_files(ref)) - 1}  # minus the style record


def stop_nth_link(monkeypatch, n, action):
    """`action` instead of the n-th hard link of a .partial- file into the film (the style record counts)."""
    real, seen = os.link, []

    def link(src, dst, **kw):
        if ".partial-" in Path(src).name:
            seen.append(dst)
            if len(seen) == n:
                action()
        return real(src, dst, **kw)

    monkeypatch.setattr(os, "link", link)


def stop_nth_put(monkeypatch, n):
    real, seen = make_pilot.put_new, []

    def put_new(*a, **k):
        seen.append(a)
        if len(seen) == n:
            raise Crash
        return real(*a, **k)

    monkeypatch.setattr(make_pilot, "put_new", put_new)


@pytest.mark.parametrize("where", ["before the file is made", "between the temporary file and its link"])
def test_a_stop_at_each_copy_point_leaves_no_half_file_and_the_rerun_finishes(pilot, monkeypatch, reference, where):
    for n in range(1, reference["n"] + 2):  # the style record is the first write
        film = pilot.film
        if film.exists():
            import shutil
            shutil.rmtree(film)
        with monkeypatch.context() as mp:
            if where.startswith("before"):
                stop_nth_put(mp, n)
            else:
                stop_nth_link(mp, n, lambda: (_ for _ in ()).throw(Crash()))
            with pytest.raises(Crash):
                run("copy", "pilot demo", "the film")
        assert not partials(film), f"stop {n}: no .partial- file left behind"
        for rel, data in plain_files(film).items():
            assert data == reference["files"][rel.replace("the-film", "reference-film")], f"stop {n}: {rel} is whole"
        assert run("copy", "pilot demo", "the film") == 0, f"stop {n}: the rerun finishes"
        assert plain_files(film) == reference["files"], f"stop {n}: and leaves what a clean copy leaves"


def test_a_stop_with_a_hard_kill_leaves_a_partial_file_that_the_rerun_ignores_and_never_carries(pilot, reference):
    (pilot.film / "audio").mkdir(parents=True)
    (pilot.film / "audio" / "x.partial-1-1.mp3").write_bytes(b"half, from a kill -9")
    assert run("copy", "pilot demo", "the film") == 0
    assert plain_files(pilot.film).keys() - {"audio/x.partial-1-1.mp3"} == reference["files"].keys()


@pytest.mark.parametrize("n", [1, 4, 9])
def test_a_real_ctrl_c_during_the_copy_stops_cleanly_and_the_rerun_finishes(pilot, monkeypatch, reference, capsys, n):
    """signal.raise_signal(SIGINT): Python's own handler raises KeyboardInterrupt, as at the keyboard."""
    handler = signal.getsignal(signal.SIGINT)
    assert handler is signal.default_int_handler
    with monkeypatch.context() as mp:
        stop_nth_link(mp, n, lambda: signal.raise_signal(signal.SIGINT))
        assert run("copy", "pilot demo", "the film") == 130
    err = capsys.readouterr().err
    assert "interrupted; no file is half-written" in err
    assert not partials(pilot.film)
    assert run("copy", "pilot demo", "the film") == 0
    assert plain_files(pilot.film) == {k.replace("reference-film", "the-film"): v for k, v in reference["files"].items()}


def test_a_full_disk_stops_copy_with_the_files_made_so_far_whole(pilot, monkeypatch, capsys, reference):
    def action():
        raise OSError(28, "No space left on device")

    with monkeypatch.context() as mp:
        stop_nth_link(mp, 4, action)
        assert run("copy", "pilot demo", "the film") == 1
    assert "No space left on device" in capsys.readouterr().err
    assert not partials(pilot.film)
    assert run("copy", "pilot demo", "the film") == 0
    assert plain_files(pilot.film) == reference["files"]


# --- the helpers --------------------------------------------------------------------------------------

def test_inside_follows_links_and_dangling_links(tmp_path):
    base = tmp_path / "topics"
    (base / "a").mkdir(parents=True)
    (tmp_path / "x").symlink_to(base / "a")
    (tmp_path / "gone").symlink_to(base / "later" / "deep")
    assert make_pilot.inside(base, base) and make_pilot.inside(base / "a" / "b", base)
    assert make_pilot.inside(tmp_path / "x", base) and make_pilot.inside(tmp_path / "gone", base)
    assert not make_pilot.inside(tmp_path / "other", base) and not make_pilot.inside(tmp_path, base)
    assert not make_pilot.inside(tmp_path / "topics-extra", base), "a name that merely starts alike"


def test_put_new_makes_a_file_whole_or_not_at_all(tmp_path):
    dest = tmp_path / "d" / "a.png"
    assert make_pilot.put_new(dest, data=b"one") and dest.read_bytes() == b"one"
    assert not make_pilot.put_new(dest, data=b"two") and dest.read_bytes() == b"one"
    assert sorted(p.name for p in dest.parent.iterdir()) == ["a.png"]
    src = tmp_path / "src.png"
    src.write_bytes(b"src")
    link = tmp_path / "link.png"
    link.symlink_to(src)
    with pytest.raises(make_pilot.SourceGone):
        make_pilot.put_new(tmp_path / "e.png", source=link)
    with pytest.raises(make_pilot.SourceGone):
        make_pilot.put_new(tmp_path / "e.png", source=tmp_path / "missing")
    assert not (tmp_path / "e.png").exists() and not list(tmp_path.glob("*.partial-*"))


def test_the_module_imports_what_it_uses_and_prints_its_usage():
    done = subprocess.run([sys.executable, str(Path(make_pilot.__file__)), "--help"], capture_output=True, text=True)
    assert done.returncode == 0 and "make" in done.stdout and "copy" in done.stdout
    done = subprocess.run([sys.executable, str(Path(make_pilot.__file__)), "copy", "a"], capture_output=True, text=True)
    assert done.returncode == 2 and "usage" in done.stderr

