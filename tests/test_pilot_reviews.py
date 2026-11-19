"""P3-7: what copy does with the pilot's review logs, and with a pilot that is still running.

A review log says a scene's picture is decided, so a film that finds the log buys nothing for that
scene. Carried over before its picture is made, or without it, a log would leave the film with a scene
that is decided and has no picture. So copy refuses a pilot that has not finished (render_film writes
manifest.json only after every picture), judges every log it carries once, as bytes, and puts the
picture in the film before the log. The races run at WORKERS=1 and at the default, each with its
precondition pinned (what exists on disk when the other side acts)."""
import json
import os
import time
from pathlib import Path

import pytest
from pilot_world import CHAPTERS, Pause, pilot_fixture, tree, world_fixture  # noqa: F401

import config
from agents import image_agent
from scripts import make_pilot
from utils import load_json, save_json

DEFAULT_WORKERS = config.WORKERS
WORKERS = pytest.mark.parametrize("workers", [1, DEFAULT_WORKERS], ids=["WORKERS=1", "WORKERS=default"])
GATE = ('the pilot has not finished; let it finish (or rerun its command: python pipeline.py "pilot demo" '
        '--style flat --no-ai-video), then copy')


def copy(*argv):
    return make_pilot.main(["copy", "pilot demo", "the film", *argv])


def plain(folder: Path) -> dict[str, bytes]:
    return {k: v for k, v in tree(folder).items() if v is not None}


def every_log_has_its_picture(film: Path) -> None:
    for path in (film / "visuals").glob("review_*.json"):
        accepted = load_json(path)["accepted"]
        assert accepted is None or (film / "visuals" / accepted).is_file(), f"{path.name} without {accepted}"


# --- review_problem: the per-file check, on bytes ------------------------------------------------------

@pytest.fixture
def visuals(tmp_path):
    folder = tmp_path / "visuals"
    folder.mkdir()
    (folder / "still_a.png").write_bytes(b"png")
    (tmp_path / "elsewhere.png").write_bytes(b"png")
    (folder / "linked.png").symlink_to(tmp_path / "elsewhere.png")
    (folder / "folder.png").mkdir()
    return folder


def log_bytes(**fields) -> bytes:
    return json.dumps({"accepted": "still_a.png", "done": True, "attempts": [], **fields}).encode()


@pytest.mark.parametrize("data,expected", [
    (log_bytes(), None),
    (log_bytes(accepted=None), None),  # rejected for good: the film falls back to Manim, as the pilot did
    (b"", "not readable JSON"), (b"{half", "not readable JSON"), (b"\xff\xfe", "not readable JSON"),
    (b"[]", "not a JSON object"), (b'"done"', "not a JSON object"),
    (log_bytes(done=False), "not finished"), (log_bytes(done="yes"), "not finished"), (log_bytes(done=1), "not finished"),
    (json.dumps({"accepted": "still_a.png"}).encode(), "not finished"),
    (log_bytes(accepted="missing.png"), "missing or not a plain file"),
    (log_bytes(accepted="linked.png"), "missing or not a plain file"),
    (log_bytes(accepted="folder.png"), "missing or not a plain file"),
    (log_bytes(accepted="../elsewhere.png"), "not a picture's file name"),
    (log_bytes(accepted="sub/x.png"), "not a picture's file name"),
    (log_bytes(accepted=""), "not a picture's file name"), (log_bytes(accepted=".."), "not a picture's file name"),
    (log_bytes(accepted=7), "not a picture's file name"), (log_bytes(accepted=["still_a.png"]), "not a picture's file name"),
])
def test_review_problem_judges_the_bytes_it_is_given(visuals, data, expected):
    problem = make_pilot.review_problem(data, visuals)
    assert (problem is None) if expected is None else (expected in problem)


# --- the pilot has not finished ----------------------------------------------------------------------------

def unlinked(path: Path):
    path.unlink()


def replaced_by_link(path: Path):
    target = path.with_name("target-" + path.name)
    target.write_bytes(path.read_bytes())
    path.unlink()
    path.symlink_to(target)


def replaced_by_folder(path: Path):
    path.unlink()
    path.mkdir()


CASES = {
    "no manifest": (lambda p: unlinked(p.work / "manifest.json"), "manifest.json is missing"),
    "manifest is not JSON": (lambda p: (p.work / "manifest.json").write_text("{half"), "manifest.json is not readable"),
    "manifest without scenes": (lambda p: p.set_manifest(scenes="x"), "manifest.json is not readable"),
    "manifest lacks the last scene": (lambda p: p.set_manifest(scenes=p.entries[:-1]), "does not list scene 3"),
    "an audio file is gone": (lambda p: unlinked(p.audio[2]), "scene 2: its audio file"),
    "a picture is gone": (lambda p: unlinked(p.visual[1]), "scene 1: its visual file"),
    "the manim render is gone": (lambda p: unlinked(p.visual[3]), "scene 3: its visual file"),
    "a picture is a symlink": (lambda p: replaced_by_link(p.visual[1]), "scene 1: its visual file"),
    "an audio file is a symlink": (lambda p: replaced_by_link(p.audio[1]), "scene 1: its audio file"),
    "a picture is a folder": (lambda p: replaced_by_folder(p.visual[2]), "scene 2: its visual file"),
    "a manifest path leaves the pilot": (lambda p: p.set_manifest(scenes=[{**e, "audio": "../x.mp3"} for e in p.entries]),
                                         "its audio file '../x.mp3'"),
    "a manifest path is absolute": (lambda p: p.set_manifest(scenes=[{**e, "visual": str(p.visual[e["id"]])}
                                                                     for e in p.entries]), "its visual file"),
    "script.json is gone": (lambda p: unlinked(p.work / "script.json"), "script.json is not readable"),
    "script.json is not JSON": (lambda p: (p.work / "script.json").write_text("{half"), "script.json is not readable"),
    "a current log is not finished": (lambda p: p.write_log(1, done=False), "it is not finished"),
    "a current log is half written": (lambda p: p.log[2].write_text('{"done": tr'), "it is not readable JSON"),
    "a current log has no done": (lambda p: p.log[1].write_text('{"accepted": null, "attempts": []}'),
                                  "it is not finished"),
    "a current log is a list": (lambda p: p.log[1].write_text("[]"), "it is not a JSON object"),
}


@pytest.mark.parametrize("case", list(CASES))
def test_copy_refuses_a_pilot_that_has_not_finished_and_writes_nothing(pilot, capsys, case):
    change, reason = CASES[case]
    change(pilot)
    before = tree(pilot.film.parent)
    assert copy() == 2
    err = capsys.readouterr().err
    assert err.startswith("make_pilot.py: ") and GATE in err, "the message names the rerun, with --no-ai-video"
    assert reason in err
    assert tree(pilot.film.parent) == before and not pilot.film.exists(), "no film folder, no style record"


def test_the_rerun_command_always_carries_no_ai_video_even_for_a_veo_pilot(pilot, capsys):
    """A rerun without it would buy Veo clips (a dead end the film command never reads); the film
    command carries --no-ai-video and reads exactly the still logs the rerun finishes."""
    (pilot.work / "visuals" / "ai_0123456789abcdef.mp4").write_bytes(b"veo")
    manifest = load_json(pilot.work / "manifest.json")
    manifest["scenes"][1]["kind"] = "ai"
    save_json(pilot.work / "manifest.json", manifest)
    pilot.log[2].write_text('{"done": false}')
    assert copy() == 2
    err = capsys.readouterr().err
    assert 'rerun its command: python pipeline.py "pilot demo" --style flat --no-ai-video), then copy' in err
    assert "--ai-video" not in err.replace("--no-ai-video", "")


def test_the_message_says_which_logs_and_files_and_lists_every_reason(pilot, capsys):
    pilot.write_log(1, done=False)
    pilot.log[2].write_text("{")
    pilot.audio[3].unlink()
    assert copy() == 2
    err = capsys.readouterr().err
    assert f"review log {pilot.log[1].name}: it is not finished" in err
    assert f"review log {pilot.log[2].name}: it is not readable JSON" in err
    assert "scene 3: its audio file" in err


def test_a_script_that_cannot_be_read_makes_every_log_count(pilot, capsys):
    (pilot.work / "script.json").write_text("{half")
    stray = pilot.work / "visuals" / "review_0000000000000000.json"
    stray.write_text('{"done": false}')  # a log no script of ours would name
    assert copy() == 2
    err = capsys.readouterr().err
    assert "script.json is not readable" in err and f"review log {stray.name}: it is not finished" in err


def test_logs_the_current_script_does_not_read_never_block_and_the_walk_says_the_film_decides_itself(pilot, capsys):
    """A3: a stale log (another style, an old description, junk) is no reason to refuse."""
    scene = pilot.scenes[0]
    calm = pilot.work / "visuals" / ("review_" + "0" * 16 + ".json")
    old_style = image_agent.review_path(scene, pilot.work / "visuals")  # computed under the pilot's flat...
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(config, "STYLE", "calm")
        stale = image_agent.review_path(scene, pilot.work / "visuals")  # ...and under calm
    assert stale != old_style, "the style is part of a log's name"
    stale.write_text('{"accepted": null, "done": false, "attempts": []}')
    calm.write_text("{half")
    (pilot.work / "visuals" / "review_ffffffffffffffff.json").write_text("[]")
    assert copy() == 0
    out = capsys.readouterr().out
    film_visuals = pilot.film / "visuals"
    for name in (stale.name, calm.name, "review_ffffffffffffffff.json"):
        assert f"visuals/{name}: not read by the pilot's current script: the film decides that scene itself" in out
        assert not (film_visuals / name).exists()
    assert (film_visuals / pilot.log[1].name).exists(), "the current logs came across"
    every_log_has_its_picture(pilot.film)


def test_a_finished_log_of_another_style_is_not_carried_either(pilot, capsys):
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(config, "STYLE", "calm")
        stale = image_agent.review_path(pilot.scenes[0], pilot.work / "visuals")
    stale.write_text(json.dumps({"accepted": pilot.visual[1].name, "done": True, "attempts": []}))
    assert copy() == 0
    assert not (pilot.film / "visuals" / stale.name).exists()
    assert (pilot.film / "visuals" / pilot.visual[1].name).exists(), "its picture is still a paid file"


def test_the_pilots_style_is_set_while_the_current_logs_are_named_and_restored_after(pilot, monkeypatch):
    seen = []
    real = image_agent.review_path

    def spy(scene, out_dir):
        seen.append(config.STYLE)
        return real(scene, out_dir)

    monkeypatch.setattr(image_agent, "review_path", spy)
    monkeypatch.setattr(config, "STYLE", "calm")  # this process' default; the pilot's record says flat
    assert copy() == 0
    assert seen and set(seen) == {"flat"}
    assert config.STYLE == "calm", "restored"
    pilot.write_log(1, done=False)
    assert copy() == 2
    assert config.STYLE == "calm", "restored after a refusal too"


def test_the_style_is_restored_even_when_naming_a_log_fails(monkeypatch):
    monkeypatch.setattr(config, "STYLE", "calm")
    assert make_pilot.current_logs(Path("."), [{"visual_type": "still"}], "flat") is None  # a scene without a description
    assert config.STYLE == "calm"


# --- the per-file check, on a finished pilot -----------------------------------------------------------------

def not_complete(capsys, *named):
    out, err = capsys.readouterr()
    assert "NOT COMPLETE" in err
    for text in named:
        assert text in err
    assert "python pipeline.py" not in out, "no film command is offered"
    return out, err


@pytest.mark.parametrize("breakage", ["picture missing", "picture is a link", "picture is a folder",
                                      "name leaves the folder", "name is not a string"])
def test_a_log_whose_picture_is_not_a_plain_file_is_not_carried_and_the_copy_says_not_complete(pilot, capsys, breakage):
    """The accepted picture of a second attempt, say: the manifest names the first, so the gate passes."""
    elsewhere = pilot.work / "visuals" / "still_second_attempt.png"
    if breakage == "picture is a link":
        (pilot.work / "visuals" / "real.png").write_bytes(b"x")
        elsewhere.symlink_to(pilot.work / "visuals" / "real.png")
    elif breakage == "picture is a folder":
        elsewhere.mkdir()
    pilot.write_log(1, accepted={"name leaves the folder": "../visuals/" + pilot.visual[1].name,
                                 "name is not a string": 7}.get(breakage, elsewhere.name))
    assert copy() == 1
    not_complete(capsys, pilot.log[1].name)
    assert not (pilot.film / "visuals" / pilot.log[1].name).exists(), "the log stays out"
    assert (pilot.film / "visuals" / pilot.log[2].name).exists(), "the others came across"
    every_log_has_its_picture(pilot.film)


def test_not_complete_is_finished_by_a_rerun_once_the_picture_is_there(pilot, capsys):
    pilot.write_log(1, accepted="still_second_attempt.png")
    assert copy() == 1
    not_complete(capsys)
    (pilot.work / "visuals" / "still_second_attempt.png").write_bytes(b"second")
    assert copy() == 0
    out = capsys.readouterr().out
    assert "python pipeline.py" in out
    assert (pilot.film / "visuals" / "still_second_attempt.png").read_bytes() == b"second"
    assert (pilot.film / "visuals" / pilot.log[1].name).read_bytes() == pilot.log[1].read_bytes()
    every_log_has_its_picture(pilot.film)


def test_the_picture_goes_in_before_its_log_and_the_logs_go_last_in_their_folder(pilot, monkeypatch):
    pilot.write_log(1, accepted="still_second_attempt.png")
    (pilot.work / "visuals" / "still_second_attempt.png").write_bytes(b"second")
    order = []
    real = make_pilot.put_new

    def spy(dest, source=None, data=None):
        order.append(Path(dest).name)
        return real(dest, source=source, data=data)

    monkeypatch.setattr(make_pilot, "put_new", spy)
    assert copy() == 0
    visuals = [n for n in order if n != "style"]
    logs = [n for n in visuals if n.startswith("review_")]
    assert len(logs) == 2
    first_log = visuals.index(logs[0])
    assert all(n.startswith("review_") for n in visuals[first_log:]), "every log after every other file"
    assert visuals.index("still_second_attempt.png") < first_log


def test_a_picture_the_film_has_as_a_folder_keeps_the_log_out(pilot, capsys):
    (pilot.film / "visuals" / pilot.visual[1].name).mkdir(parents=True)
    assert copy() == 1
    not_complete(capsys, "is not in the film as a plain file")
    assert not (pilot.film / "visuals" / pilot.log[1].name).exists()


def test_a_log_that_rejected_the_picture_for_good_is_carried_with_no_picture(pilot, capsys):
    pilot.write_log(1, accepted=None, attempts=[{"still": "x", "prompt": "p", "ok": False, "problems": ["text"]}])
    assert copy() == 0
    assert (pilot.film / "visuals" / pilot.log[1].name).read_bytes() == pilot.log[1].read_bytes()


def test_a_log_is_read_once_and_the_bytes_judged_are_the_bytes_written(pilot, monkeypatch):
    """The pilot rewrites the log (a rerun, say) right after copy read it: the film gets what was judged."""
    target = pilot.log[1]
    judged = target.read_bytes()
    real, reads = make_pilot.read_plain, []

    def read_plain(path):
        data = real(path)
        if Path(path) == target:
            reads.append(data)
            if len(reads) == 2:  # the gate was the first read; the walk's is the second
                target.write_text('{"done": false, "accepted": "gone.png"}')
        return data

    monkeypatch.setattr(make_pilot, "read_plain", read_plain)
    assert copy() == 0
    assert reads == [judged, judged]
    assert (pilot.film / "visuals" / target.name).read_bytes() == judged


def listing_hooks(monkeypatch, visuals: Path, hooks: dict):
    """Run hooks[n](entries) just after the n-th listing of the pilot's visuals/: 1 is the gate's, 2 the walk's."""
    real, seen = make_pilot.list_folder, []

    def list_folder(folder):
        entries = real(folder)
        if Path(folder) == visuals:
            seen.append(entries)
            if len(seen) in hooks:
                hooks[len(seen)](entries)
        return entries

    monkeypatch.setattr(make_pilot, "list_folder", list_folder)
    return seen


def test_a_picture_that_goes_missing_after_its_folder_was_listed_keeps_its_log_out(pilot, monkeypatch, capsys):
    pilot.write_log(1, accepted="still_second_attempt.png")
    picture = pilot.work / "visuals" / "still_second_attempt.png"
    picture.write_bytes(b"second")
    listing_hooks(monkeypatch, pilot.work / "visuals", {2: lambda entries: picture.unlink()})
    assert copy() == 1
    out, err = not_complete(capsys, pilot.log[1].name)
    assert "still_second_attempt.png: gone from the pilot after the folder was listed" in out
    assert not (pilot.film / "visuals" / pilot.log[1].name).exists()
    every_log_has_its_picture(pilot.film)


# --- a pilot that is still running -------------------------------------------------------------------------------

def wait_for(condition, what, seconds=30):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if condition():
            return
        time.sleep(0.01)
    raise AssertionError(f"never happened: {what}")


def film_buys_only_the_rest(world, pilot_paid):
    """The film, after the pilot is done and copied: it asks for chapter 2 and for nothing the pilot asked for."""
    world.forget()
    world.run(*world.film_argv())
    film = world.paid()
    assert sorted(film["tts"]) == sorted(s["narration"] for s in CHAPTERS[2])
    assert sorted(film["review"]) == ["fifth", "fourth"] and film["manim"] == []
    for kind in ("tts", "image", "review", "manim"):
        assert not set(film[kind]) & set(pilot_paid[kind]), f"the film bought again what the pilot bought ({kind})"


@WORKERS
def test_A1_copy_during_the_pilots_narration_stage_is_refused_and_then_the_film_buys_nothing_twice(
        world, monkeypatch, capsys, workers):
    monkeypatch.setattr(config, "WORKERS", workers)
    out = world.make("1")
    pilot_dir, film = world.build / "pilot-demo", world.build / "the-film"
    last = CHAPTERS[1][-1]["narration"]
    stop = world.hooks["tts"] = Pause(lambda text: text == last)
    running = world.start(*world.pilot_argv(out))
    stop.wait_reached()
    wait_for(lambda: len(list((pilot_dir / "audio").glob("*.mp3"))) == 2, "the first two narrations on disk")
    # preconditions: paid narration is on disk, the style is recorded, and the pilot is before its pictures
    assert (pilot_dir / "style").read_text() == "flat"
    assert not (pilot_dir / "visuals").exists() and not (pilot_dir / "manifest.json").exists()

    assert copy() == 2
    err = capsys.readouterr().err
    assert GATE in err and "manifest.json is missing" in err
    assert not film.exists(), "nothing written: not the style record, not the two paid lines"

    stop.go.set()
    running.join()
    assert copy() == 0
    pilot_paid = world.paid()
    assert len(pilot_paid["tts"]) == 3
    film_buys_only_the_rest(world, pilot_paid)


@WORKERS
def test_A1_copy_during_the_pilots_picture_stage_is_refused_too(world, monkeypatch, capsys, workers):
    monkeypatch.setattr(config, "WORKERS", workers)
    out = world.make("1")
    pilot_dir, film = world.build / "pilot-demo", world.build / "the-film"
    second = CHAPTERS[1][1]["visual_description"]
    stop = world.hooks["image"] = Pause(lambda prompt: prompt.startswith(second))
    running = world.start(*world.pilot_argv(out))
    stop.wait_reached()
    wait_for(lambda: len(list((pilot_dir / "visuals").glob("review_*.json"))) >= 1, "scene 1's picture decided")
    # preconditions: audio and a decided picture on disk, a picture still being made, and no manifest
    assert len(list((pilot_dir / "audio").glob("*.mp3"))) == 3 and (pilot_dir / "visuals").is_dir()
    assert not (pilot_dir / "manifest.json").exists()

    assert copy() == 2
    assert GATE in capsys.readouterr().err
    assert not film.exists()
    stop.go.set()
    running.join()
    assert copy() == 0
    film_buys_only_the_rest(world, world.paid())


def delete_scene_one_log(world, pilot_dir):
    """A finished pilot whose first still has to be decided again: its log is gone, its picture is not.
    The manifest of the first run stays, so a copy now would find a pilot that looks finished."""
    scene = load_json(pilot_dir / "script.json")["scenes"][0]
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(config, "STYLE", "flat")
        log = image_agent.review_path(scene, pilot_dir / "visuals")
    assert log.exists() and scene["concept"] == "first"
    first_bytes = load_json(log)
    log.unlink()
    return log, first_bytes["accepted"]


@WORKERS
def test_A5_a_log_that_finishes_after_its_folder_was_listed_is_carried_with_its_picture(
        world, monkeypatch, capsys, workers):
    monkeypatch.setattr(config, "WORKERS", workers)
    out = world.make("1")
    world.run(*world.pilot_argv(out))
    first_run = world.paid()
    pilot_dir, film = world.build / "pilot-demo", world.build / "the-film"
    visuals = pilot_dir / "visuals"
    log, first_picture = delete_scene_one_log(world, pilot_dir)
    world.forget()
    world.reject_first = {"first"}  # the review rejects attempt 1: the log is saved unfinished, attempt 2 follows
    review = Pause(lambda concept: concept == "first")
    second_attempt = Pause(lambda prompt: "Keep strictly" in prompt)
    world.hooks["review"], world.hooks["image"] = review, second_attempt
    running = world.start(*world.pilot_argv(out))
    review.wait_reached()
    # preconditions: visuals/ and the first run's manifest are on disk, and scene 1 has no log yet
    assert visuals.is_dir() and (pilot_dir / "manifest.json").exists() and not log.exists()

    def after_the_gate(entries):
        assert log.name not in [p.name for p in entries], "the gate listed visuals/ before the log existed"
        review.go.set()  # the rejection is saved as an unfinished log; the pilot then asks for attempt 2
        second_attempt.wait_reached()
        wait_for(log.exists, "the unfinished log saved")
        assert load_json(log)["done"] is False and len(load_json(log)["attempts"]) == 1

    state = {}

    def after_the_walk_listed(entries):
        assert log.name in [p.name for p in entries], "listed while unfinished"
        assert load_json(log)["done"] is False
        second_attempt.go.set()
        wait_for(lambda: load_json(log).get("done") is True, "the log finished after the listing")
        state["final"] = load_json(log)
        assert state["final"]["accepted"] not in [p.name for p in entries], "its picture is new, not in the listing"
        assert state["final"]["accepted"] != first_picture and (visuals / state["final"]["accepted"]).is_file()

    with monkeypatch.context() as mp:
        listing_hooks(mp, visuals, {1: after_the_gate, 2: after_the_walk_listed})
        assert copy() == 0, "the gate saw a finished-looking pilot; the per-file check did the rest"
    out_text = capsys.readouterr().out
    picture = state["final"]["accepted"]
    assert f"copied  visuals/{picture}" in out_text
    assert (film / "visuals" / picture).read_bytes() == (visuals / picture).read_bytes()
    assert (film / "visuals" / log.name).read_bytes() == log.read_bytes(), "the log as it finished, whole"
    every_log_has_its_picture(film)

    running.join()
    assert copy() == 0
    film_buys_only_the_rest(world, {k: first_run[k] + world.paid()[k] for k in first_run})


@WORKERS
def test_A5_a_log_that_appears_after_its_folder_was_listed_is_not_carried_and_a_rerun_carries_it(
        world, monkeypatch, capsys, workers):
    monkeypatch.setattr(config, "WORKERS", workers)
    out = world.make("1")
    world.run(*world.pilot_argv(out))
    first_run = world.paid()
    pilot_dir, film = world.build / "pilot-demo", world.build / "the-film"
    visuals = pilot_dir / "visuals"
    log, picture = delete_scene_one_log(world, pilot_dir)
    world.forget()
    review = Pause(lambda concept: concept == "first")
    world.hooks["review"] = review
    running = world.start(*world.pilot_argv(out))
    review.wait_reached()
    assert visuals.is_dir() and not log.exists() and (pilot_dir / "manifest.json").exists()

    def after_the_walk_listed(entries):
        assert log.name not in [p.name for p in entries]
        review.go.set()
        wait_for(lambda: log.exists() and load_json(log).get("done") is True, "the log finished after the listing")

    with monkeypatch.context() as mp:
        listing_hooks(mp, visuals, {2: after_the_walk_listed})
        assert copy() == 0
    assert not (film / "visuals" / log.name).exists(), "a log the walk never listed is not carried"
    every_log_has_its_picture(film)

    running.join()
    assert copy() == 0
    assert (film / "visuals" / log.name).read_bytes() == log.read_bytes()
    assert (film / "visuals" / picture).exists()
    # the first run's purchases plus the rerun's: the review of scene 1 was asked twice, the film asks for neither
    film_buys_only_the_rest(world, {k: first_run[k] + world.paid()[k] for k in first_run})


@WORKERS
def test_a_pilot_finished_with_one_scene_decided_by_a_second_attempt_is_carried_whole(world, monkeypatch, workers):
    """The ordinary case of the same: no race. The log names the second attempt's picture; the film
    ends up with both attempts' pictures and the log, and buys nothing of the pilot's."""
    monkeypatch.setattr(config, "WORKERS", workers)
    world.reject_first = {"first"}
    out = world.make("1")
    world.run(*world.pilot_argv(out))
    pilot_dir = world.build / "pilot-demo"
    [log] = [p for p in (pilot_dir / "visuals").glob("review_*.json") if len(load_json(p)["attempts"]) == 2]
    assert make_pilot.main(["copy", "pilot demo", "the film"]) == 0
    film = world.build / "the-film"
    assert (film / "visuals" / log.name).read_bytes() == log.read_bytes()
    for attempt in load_json(log)["attempts"]:
        assert (film / "visuals" / attempt["still"]).is_file()
    every_log_has_its_picture(film)
    film_buys_only_the_rest(world, world.paid())


def test_the_race_tests_pin_the_workers_they_run_at(monkeypatch):
    assert DEFAULT_WORKERS == config.WORKERS and DEFAULT_WORKERS > 1, "the default really is concurrent"
    assert os.environ.get("PYTEST_CURRENT_TEST")
