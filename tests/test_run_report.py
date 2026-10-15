"""P2-3: every run that gets past argument parsing leaves build/<slug>/run_report.md: what it
cost (from the ledger, attributed to this film and this run), how long each stage took, how each
scene ended up, what QA said and what to look at next. It is written in a `finally`, so a run that
stops says where and why. Nothing is called to build it, and no key reaches it. Providers are
faked at the HTTP boundary or by stand-ins, as in test_style.py."""
import json
import re
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import config
import ledger
import pipeline
import qa
import run_report
import thumbnail
from agents import ai_video, google_client, image_agent, manim_agent, still_critic, voice
from utils import brief_key, load_json, save_json

REAL_GET_SCRIPT = pipeline.get_script  # the film fixture fakes it
REAL_RUN_QA = qa.run_qa  # the fixtures replace it on the module
STAGES = ["script", "narration", "visuals", "scene clips", "assembly", "metadata", "thumbnail", "QA"]
OLD_KEYS = {"time", "provider", "model", "units", "est_cost_eur"}  # what an entry had before P2-3


# --- reading a report -------------------------------------------------------------------------

def sections(text: str) -> dict[str, str]:
    parts = re.split(r"^## (.+)$", text, flags=re.M)
    return {"header": parts[0], **dict(zip(parts[1::2], parts[2::2], strict=True))}


def rows(section: str) -> list[list[str]]:
    """The cells of each row of the section's table, without its header and separator."""
    lines = [line for line in section.splitlines() if line.startswith("|")]
    return [[c.strip() for c in re.split(r"(?<!\\)\|", line.strip())[1:-1]] for line in lines[2:]]


def field(text: str, name: str) -> str:
    return re.search(rf"^- \*\*{name}:\*\* (.*)$", text, flags=re.M).group(1)


def euros(cell: str) -> float:
    return float(cell.removeprefix("€"))


def ledger_lines() -> list[dict]:
    path = ledger.ledger_path()
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def raising(error):
    def fail(*args, **kwargs):
        raise error
    return fail


def seed_ledger(*entries: dict) -> None:
    """Entries as they were written before (no film) or by other films and runs."""
    ledger.ledger_path().parent.mkdir(parents=True, exist_ok=True)
    with ledger.ledger_path().open("a") as f:
        for e in entries:
            f.write(json.dumps({"time": "2026-10-01T09:00:00+00:00", "provider": "google", "model": "m",
                                "units": {"images": 1}, **e}) + "\n")


# --- the ledger knows which film and which run an entry belongs to ------------------------------

def test_entries_made_during_a_run_carry_its_film_and_a_run_id():
    ledger.record("google", "m", {"images": 1}, 0.1)
    with ledger.attribute_to("film-a") as first:
        ledger.record("google", "m", {"images": 1}, 0.2)
    with ledger.attribute_to("film-a") as second:
        ledger.record("google", "m", {"images": 1}, 0.3)
    ledger.record("google", "m", {"images": 1}, 0.4)
    before, one, two, after = ledger_lines()
    assert set(before) == OLD_KEYS == set(after), "outside a run an entry is exactly what it was"
    assert (one["film"], one["run"]) == ("film-a", first)
    assert (two["film"], two["run"]) == ("film-a", second)
    assert first != second, "two runs of one film are told apart even within the same second"


def test_worker_threads_record_under_the_runs_film():
    with ledger.attribute_to("film-a") as run, ThreadPoolExecutor(8) as pool:
        list(pool.map(lambda i: ledger.record("elevenlabs", "m", {"characters": i}, 0.01), range(16)))
    assert [(e["film"], e["run"]) for e in ledger_lines()] == [("film-a", run)] * 16


def test_the_attribution_ends_with_the_run_even_when_it_raises():
    with pytest.raises(RuntimeError), ledger.attribute_to("film-a"):
        raise RuntimeError("boom")
    ledger.record("google", "m", {"images": 1}, 0.1)
    assert set(ledger_lines()[0]) == OLD_KEYS


def test_old_entries_without_a_film_are_still_read_by_every_reader():
    today = datetime.now(timezone.utc).isoformat(timespec="seconds")
    seed_ledger({"est_cost_eur": 1.5, "time": today, "units": {"seconds": 8}},
                {"est_cost_eur": 0.5, "film": "a", "run": "r1", "time": today, "units": {"seconds": 8}},
                {"est_cost_eur": 0.25, "film": "b", "run": "r2"})
    assert ledger.spent_eur() == pytest.approx(2.25)
    assert ledger.veo_clips_today() == 2
    assert [e["est_cost_eur"] for e in ledger.entries(film="a")] == [0.5], "an old entry matches no film"
    assert [e["est_cost_eur"] for e in ledger.entries(run="r2")] == [0.25]
    assert len(ledger.entries()) == 3


# --- a whole run, with the providers faked ------------------------------------------------------

def a_scene(i, visual_type, description):
    return {"id": i, "chapter": 1, "concept": f"c{i}", "narration": f"Line number {i}.",
            "visual_type": visual_type, "visual_description": description}


def touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"stand-in")
    return path


def stub_assembly(monkeypatch):
    """FFmpeg's part stood in for, as these tests are about what the report says and a real
    assembly costs ~10 s a run; `real_film` keeps the real one for the timings."""
    monkeypatch.setattr(pipeline, "build_scene_clip",
                        lambda visual, narration, out_dir, kind, scene_id, *rest: touch(out_dir / f"clip_{scene_id}.mp4"))
    monkeypatch.setattr(pipeline, "crossfade_concat", lambda clips, out: touch(out))
    monkeypatch.setattr(pipeline, "video_duration", lambda path: 90.0)
    monkeypatch.setattr(pipeline.music, "placeholder_pad", lambda out: touch(out))
    monkeypatch.setattr(pipeline.music, "build_bed", lambda tracks, total, out, seed: touch(out))
    monkeypatch.setattr(pipeline, "add_music", lambda narrated, bed, out: touch(out))


@pytest.fixture
def film(monkeypatch, tmp_path, media):
    stub_assembly(monkeypatch)
    return make_film(monkeypatch, tmp_path, media)


@pytest.fixture
def real_film(monkeypatch, tmp_path, media, small):
    return make_film(monkeypatch, tmp_path, media)


def make_film(monkeypatch, tmp_path, media):
    """pipeline.main() end to end on five scenes, one ending each way: a Veo clip, a Veo scene the
    model refuses (so a still), a still, a still the reviewer rejects (so Manim) and a diagram.
    Claude's script call is a stand-in that records its cost the way agents/llm.py does."""
    still = (media / "still.png").read_bytes()
    narration = (media / "n_short.mp3").read_bytes()
    script = {"title": "Test film", "scenes": [
        a_scene(1, "ai_video", "picture one"), a_scene(2, "ai_video", "veo refuses this"),
        a_scene(3, "still", "picture three"), a_scene(4, "still", "rejected picture"),
        a_scene(5, "manim", "diagram five")]}

    class Models:
        def generate_content(self, model, contents, config):
            if "image refuses" in contents:
                return SimpleNamespace(parts=[])
            part = SimpleNamespace(inline_data=SimpleNamespace(data=still, mime_type="image/png"), thought=None)
            return SimpleNamespace(parts=[part])

        def generate_videos(self, model, source, config):
            videos = [] if "veo refuses" in source.prompt else [SimpleNamespace(video=SimpleNamespace(
                save=lambda path: shutil.copy(media / "ai.mp4", path)))]
            return SimpleNamespace(done=True, error=None, response=SimpleNamespace(generated_videos=videos))

    def get_script(topic, minutes, work, skip_critic, brief=None):
        if (work / "script.json").exists():
            return load_json(work / "script.json")
        ledger.record("anthropic", config.CLAUDE_MODEL, {"input_tokens": 1200, "output_tokens": 800},
                      ledger.claude_eur(config.CLAUDE_MODEL, 1200, 800))
        save_json(work / "script.json", script)
        return script

    def render_manim(scene, target_s, out_dir, atmospheric):
        out_dir.mkdir(parents=True, exist_ok=True)
        return Path(shutil.copy(media / "manim.mp4", out_dir / f"manim_{scene['id']}.mp4"))

    def review(scene, still):
        ok = "rejected" not in scene["visual_description"]
        return {"ok": ok, "problems": [] if ok else ["it shows a ruler with letters on it"]}

    monkeypatch.setattr(config, "GOOGLE_API_KEY", "offline-test")
    monkeypatch.setattr(config, "BUDGET_EUR", 100.0)
    monkeypatch.setattr(config, "STILL_REVIEW_RETRIES", 1)
    monkeypatch.setattr(config, "MUSIC_DIR", str(tmp_path / "no_music"))
    monkeypatch.setattr(google_client, "make", lambda: SimpleNamespace(
        models=Models(), files=SimpleNamespace(download=lambda file: None)))
    monkeypatch.setattr(voice.requests, "post", lambda url, headers, json, timeout: SimpleNamespace(
        status_code=200, content=narration, text="", headers={}))
    monkeypatch.setattr(still_critic, "review", review)
    monkeypatch.setattr(manim_agent, "render_scene", render_manim)
    monkeypatch.setattr(pipeline, "get_script", get_script)
    monkeypatch.setattr(pipeline.metadata, "write_metadata", lambda work: None)
    monkeypatch.setattr(pipeline.thumbnail, "make", lambda work, failures: None)  # test_thumbnail.py's
    monkeypatch.setattr(pipeline.qa, "print_report", lambda report: None)
    state = SimpleNamespace(
        script=script, work=Path(config.BUILD_DIR) / "topic",
        # the shape qa.run_qa returns, made with its own constructor: a change there breaks these tests
        qa={"video": "test-film.mp4", "passed": True, "checks": {
            "duration": qa.check(True, 31.2, "30.0 s ±30% (script: 60 words at 120 wpm)"),
            "loudness": qa.check(True, -16.1, "-16 LUFS ±1.0 LU"),
            "ducking": qa.check(True, 8.4, "≥ 6.0 dB", "12 speech / 9 pause windows")}})
    monkeypatch.setattr(qa, "run_qa", lambda path: state.qa)

    def run(*argv):
        monkeypatch.setattr(sys, "argv", ["pipeline.py", "topic", *argv])
        pipeline.main()

    state.run = run
    state.report = lambda: (state.work / "run_report.md").read_text(encoding="utf-8")
    return state


def test_a_finished_run_reports_what_it_cost_per_provider_as_the_ledger_recorded_it(film):
    film.run()
    costs = sections(film.report())["Costs"]
    made = ledger_lines()
    assert {e["film"] for e in made} == {"topic"} and len({e["run"] for e in made}) == 1
    table = {r[0]: r[1:] for r in rows(costs)}
    assert set(table) == {"anthropic", "elevenlabs", "google", "Total"}
    for provider in ("anthropic", "elevenlabs", "google"):
        entries = [e for e in made if e["provider"] == provider]
        calls, this_run, this_film = table[provider]
        assert int(calls) == len(entries) > 0
        assert euros(this_run) == pytest.approx(sum(e["est_cost_eur"] for e in entries), abs=5e-4)
        assert euros(this_film) == euros(this_run), "a film's first run is all there is of the film"
    assert euros(table["Total"][1]) == pytest.approx(sum(e["est_cost_eur"] for e in made), abs=5e-4)
    assert f"€{ledger.spent_eur():.3f} of €{config.BUDGET_EUR:.3f}" in costs
    assert f"€{config.BUDGET_EUR - ledger.spent_eur():.3f} left" in costs


def test_a_rerun_costs_nothing_but_the_film_remembers_what_it_cost(film):
    film.run()
    first = {r[0]: r[1:] for r in rows(sections(film.report())["Costs"])}
    film.run()
    costs = sections(film.report())["Costs"]
    again = {r[0]: r[1:] for r in rows(costs)}
    assert again["Total"][1] == "€0.000" and again["Total"][0] == "0"
    assert again["Total"][2] == first["Total"][2], "the film still costs what its first run cost"
    assert len(ledger_lines()) == int(first["Total"][0]), "and the rerun bought nothing"


def test_costs_of_other_films_and_of_entries_without_a_film_are_not_this_films(film):
    seed_ledger({"est_cost_eur": 1.0, "film": "other-film", "run": "r1"},
                {"est_cost_eur": 2.0, "provider": "anthropic"},
                {"est_cost_eur": 0.5, "provider": "elevenlabs", "film": "topic", "run": "an-earlier-run"})
    film.run()
    costs = sections(film.report())["Costs"]
    ours = [e for e in ledger_lines() if e.get("run") not in (None, "r1", "an-earlier-run")]
    total = next(r for r in rows(costs) if r[0] == "Total")
    assert euros(total[2]) == pytest.approx(sum(e["est_cost_eur"] for e in ours), abs=5e-4)
    assert euros(total[3]) == pytest.approx(sum(e["est_cost_eur"] for e in ours) + 0.5, abs=5e-4), \
        "this film's earlier run counts in the film's total and not in this run's"
    assert f"€{ledger.spent_eur():.3f} of" in costs, "every entry counts in the ledger total"
    assert "Ledger entries with no film (made before P2-3 or outside a run): 1 (€2.000)" in costs


def test_fallbacks_show_in_the_scene_table_with_the_reason_on_file(film):
    film.run()
    text = film.report()
    scenes = sections(text)["Scenes"]
    table = rows(scenes)
    assert [r[:4] for r in table] == [["1", "c1", "ai_video", "ai"], ["2", "c2", "ai_video", "still"],
                                      ["3", "c3", "still", "still"], ["4", "c4", "still", "manim"],
                                      ["5", "c5", "manim", "manim"]]
    notes = [r[4] for r in table]
    assert notes[0] == notes[2] == notes[4] == "", "scenes that went as planned carry no note"
    assert notes[1].startswith("Veo failed: RuntimeError: Veo returned no video")
    assert "still rejected 2 times" in notes[3] and "it shows a ruler with letters on it" in notes[3]
    assert "Used: 1 ai, 2 still, 2 manim." in scenes
    assert "Fallbacks: ai_video → still: 1; still → manim: 1." in scenes


def test_each_problem_is_a_line_in_next_steps_with_the_way_to_ask_again(film):
    film.run()
    steps = [line for line in sections(film.report())["Next steps"].splitlines() if line.startswith("- ")]
    assert len(steps) == 2
    assert steps[0].startswith("- Scene 2 fell back to a still: Veo failed: ") and "`--retry-failed`" in steps[0]
    assert steps[1].startswith("- Scene 4 fell back to Manim: still rejected 2 times")
    assert steps[1].endswith("edit its visual_description in script.json for another picture.")


def test_a_picture_the_image_model_refused_is_a_fallback_with_its_recorded_error(film):
    film.script["scenes"] = [a_scene(1, "still", "image refuses this"), a_scene(2, "manim", "diagram two")]
    film.run()
    row = rows(sections(film.report())["Scenes"])[0]
    assert row[:4] == ["1", "c1", "still", "manim"]
    assert row[4].startswith("image model failed: RuntimeError: The image model returned no image")
    assert "`--retry-failed` asks again" in sections(film.report())["Next steps"]


def test_a_still_whose_review_call_failed_says_nothing_is_on_file_instead_of_guessing(film, monkeypatch):
    monkeypatch.setattr(still_critic, "review", raising(RuntimeError("Claude is overloaded")))
    film.script["scenes"] = [a_scene(1, "still", "picture one"), a_scene(2, "manim", "diagram two")]
    film.run()
    row = rows(sections(film.report())["Scenes"])[0]
    assert row[:4] == ["1", "c1", "still", "manim"]
    assert row[4] == ("image model: no failure or rejection on file (a failed review call is not recorded; "
                      "see the console log)")


def test_a_veo_scene_that_fell_back_without_a_failure_on_file_says_what_that_means(film, monkeypatch):
    """The daily cap and transient errors are not recorded (D19): the report must not invent a reason."""
    monkeypatch.setattr(config, "VEO_MAX_PER_DAY", 0)
    film.run()
    table = rows(sections(film.report())["Scenes"])
    assert [r[3] for r in table[:2]] == ["still", "still"]
    assert table[0][4] == ("Veo: no failure on file (a transient error or the daily cap, which are not "
                           "recorded; see the console log)")
    steps = sections(film.report())["Next steps"]
    assert "- Scene 1 fell back to a still: Veo: no failure on file" in steps and "a rerun asks again" in steps


def test_without_a_google_key_the_report_says_why_nothing_was_drawn(film, monkeypatch):
    monkeypatch.setattr(config, "GOOGLE_API_KEY", None)
    film.run()
    sec = sections(film.report())
    assert field(sec["header"], "Models").endswith(f"Veo `{config.VEO_MODEL}` (not allowed: no GOOGLE_API_KEY)")
    assert "google" not in {r[0] for r in rows(sec["Costs"])}
    assert [r[3] for r in rows(sec["Scenes"])] == ["manim"] * 5
    steps = sec["Next steps"]
    assert "- Scene 3 fell back to Manim: no image model (no GOOGLE_API_KEY)." in steps
    assert ("- 2 ai_video scenes did not get Veo because it was not allowed (no GOOGLE_API_KEY); "
            "add the key to .env to use Veo (it costs money).") in steps


def test_a_clean_run_has_nothing_to_do(film):
    film.script["scenes"] = [a_scene(1, "still", "picture one"), a_scene(2, "manim", "diagram two")]
    film.run()
    sec = sections(film.report())
    assert "Fallbacks: none." in sec["Scenes"]
    assert sec["Next steps"].strip() == "None: the run finished, QA passed and no scene fell back."


THUMB = Path("thumbnail.jpg")


def thumbnail_made(monkeypatch, outcome):
    monkeypatch.setattr(pipeline.thumbnail, "make", lambda work, failures: outcome)


def test_the_report_says_where_the_thumbnail_came_from_and_a_good_one_is_no_next_step(film, monkeypatch):
    film.script["scenes"] = [a_scene(1, "still", "picture one"), a_scene(2, "manim", "diagram two")]
    thumbnail_made(monkeypatch, thumbnail.Outcome("picture", None, None, THUMB))
    film.run()
    sec = sections(film.report())
    assert field(sec["header"], "Thumbnail") == "the image model's picture with the title drawn on it (`thumbnail.jpg`)"
    assert sec["Next steps"].strip() == "None: the run finished, QA passed and no scene fell back."


@pytest.mark.parametrize("outcome, header, step", [
    (thumbnail.Outcome("frame", "no GOOGLE_API_KEY", None, THUMB),
     "a frame of the film (`thumbnail.jpg`): no GOOGLE_API_KEY",
     "- Thumbnail is a frame of the film: no GOOGLE_API_KEY."),
    (thumbnail.Outcome("frame", "image model failed (RuntimeError: no image)", thumbnail.RETRY, THUMB),
     "a frame of the film (`thumbnail.jpg`): image model failed (RuntimeError: no image)",
     "- Thumbnail is a frame of the film: image model failed (RuntimeError: no image); "
     "`--retry-failed` asks again."),
    (thumbnail.Outcome("frame", "budget spent (over BUDGET_EUR)", "a rerun with budget left draws it", THUMB),
     "a frame of the film (`thumbnail.jpg`): budget spent (over BUDGET_EUR)",
     "- Thumbnail is a frame of the film: budget spent (over BUDGET_EUR); a rerun with budget left draws it."),
    (thumbnail.Outcome("none", "ValueError: boom", "see the console log", None),
     "none: ValueError: boom", "- Thumbnail is not made: ValueError: boom; see the console log."),
], ids=["no key", "failure recorded", "budget spent", "none"])
def test_a_thumbnail_that_is_not_the_models_picture_is_a_line_in_the_header_and_a_next_step(
        film, monkeypatch, outcome, header, step):
    film.script["scenes"] = [a_scene(1, "still", "picture one"), a_scene(2, "manim", "diagram two")]
    thumbnail_made(monkeypatch, outcome)
    film.run()
    sec = sections(film.report())
    assert field(sec["header"], "Thumbnail") == header
    assert [line for line in sec["Next steps"].splitlines() if line.startswith("- ")] == [step]


def test_the_thumbnail_line_says_not_made_when_the_stage_was_not_reached_and_no_key_reaches_it(film, monkeypatch):
    film.run("--script-only")
    assert field(sections(film.report())["header"], "Thumbnail") == "not made"
    assert "Thumbnail" not in sections(film.report())["Next steps"]

    monkeypatch.setattr(config, "GOOGLE_API_KEY", "AIza-offline-secret-456")
    thumbnail_made(monkeypatch, thumbnail.Outcome("frame", "image model failed (key=AIza-offline-secret-456)", None,
                                                  THUMB))
    film.run()
    assert "AIza" not in film.report() and "key=***" in film.report()


def test_veo_switched_off_is_one_line_not_one_per_scene(film):
    film.run("--no-ai-video")
    sec = sections(film.report())
    assert field(sec["header"], "Models").endswith(f"Veo `{config.VEO_MODEL}` (not allowed: --no-ai-video)")
    notes = [r[4] for r in rows(sec["Scenes"])]
    assert "Veo not allowed (--no-ai-video)" in notes[0] and "Veo not allowed (--no-ai-video)" in notes[1]
    steps = [line for line in sec["Next steps"].splitlines() if line.startswith("- ")]
    assert [s for s in steps if "not allowed" in s] == [
        "- 2 ai_video scenes did not get Veo because it was not allowed (--no-ai-video); "
        "rerun without it to use Veo (it costs money)."]
    assert not [s for s in steps if s.startswith("- Scene 1 ") or s.startswith("- Scene 2 ")]


def test_a_finished_run_times_every_stage_and_reports_what_the_real_qa_returned(real_film, monkeypatch):
    returned = []
    monkeypatch.setattr(qa, "run_qa", lambda path: returned.append(REAL_RUN_QA(path)) or returned[-1])
    try:
        real_film.run()
    except SystemExit as e:  # a failed check is a result here, not a fault
        assert e.code == 1 and not returned[0]["passed"]
    sec = sections(real_film.report())
    assert sec["QA"].lstrip().startswith(f"{'PASSED' if returned[0]['passed'] else 'FAILED'} `{returned[0]['video']}`")
    assert rows(sec["QA"]) == [
        [name, "ok" if c["passed"] else "FAIL", "n/a" if c["value"] is None else str(c["value"]), c["target"],
         c.get("detail", "")] for name, c in returned[0]["checks"].items()]
    timings = rows(sec["Timings"])
    assert [r[0] for r in timings] == [*STAGES, "Total"]
    for stage, seconds in timings:
        assert re.fullmatch(r"\d+\.\d s", seconds), f"{stage}: {seconds!r}"
    assert float(timings[-1][1].split()[0]) >= max(float(s.split()[0]) for _, s in timings[:-1]), \
        "the total is the whole run's wall-clock time"


def test_the_report_lists_qa_check_by_check(film):
    film.run()
    sec = sections(film.report())
    assert sec["QA"].lstrip().startswith("PASSED `test-film.mp4`")
    table = rows(sec["QA"])
    assert [(r[0], r[1]) for r in table] == [("duration", "ok"), ("loudness", "ok"), ("ducking", "ok")]
    assert table[2] == ["ducking", "ok", "8.4", "≥ 6.0 dB", "12 speech / 9 pause windows"]


def test_a_failed_qa_check_is_the_outcome_and_a_next_step(film):
    film.qa["passed"] = False
    film.qa["checks"]["ducking"] = qa.check(False, 4.2, "≥ 6.0 dB", "12 speech / 9 pause windows")
    with pytest.raises(SystemExit) as stopped:
        film.run()
    assert stopped.value.code == 1, "the exit code is what it was"
    sec = sections(film.report())
    assert field(sec["header"], "Outcome") == "finished, QA failed"
    assert sec["QA"].lstrip().startswith("FAILED `test-film.mp4`")
    assert ["ducking", "FAIL", "4.2", "≥ 6.0 dB", "12 speech / 9 pause windows"] in rows(sec["QA"])
    assert "- QA check `ducking` failed: 4.2 (target ≥ 6.0 dB; 12 speech / 9 pause windows)" in sec["Next steps"]


def test_a_run_that_raises_in_the_visuals_stage_still_writes_its_report(film, monkeypatch):
    monkeypatch.setattr(pipeline, "make_visual", raising(
        RuntimeError("the picture stage went down\nand this second line is not for the header")))
    with pytest.raises(RuntimeError, match="went down"):
        film.run()
    sec = sections(film.report())
    assert field(sec["header"], "Outcome") == "stopped at visuals: RuntimeError: the picture stage went down"
    assert field(sec["header"], "Film") == "Test film"
    assert sec["QA"].strip() == "Not run."
    timings = dict(rows(sec["Timings"]))
    assert re.fullmatch(r"\d+\.\d s", timings["script"]) and re.fullmatch(r"\d+\.\d s", timings["narration"])
    assert re.fullmatch(r"\d+\.\d s \(stopped here\)", timings["visuals"])
    assert [timings[s] for s in ("scene clips", "assembly", "metadata", "thumbnail", "QA")] == ["not reached"] * 5
    costs = {r[0]: r[1:] for r in rows(sec["Costs"])}
    assert set(costs) == {"anthropic", "elevenlabs", "Total"}, "what was bought before it stopped, and nothing else"
    assert costs["elevenlabs"][0] == "5" and euros(costs["Total"][1]) > 0
    assert "not reached" in sec["Scenes"].lower()
    assert "- Stopped at visuals: RuntimeError: the picture stage went down; see the error above" in sec["Next steps"]
    assert "same command" not in sec["Next steps"], "a stop says nothing about what to type next"


def test_a_stop_after_the_clips_still_shows_the_scenes_that_were_built(film, monkeypatch):
    monkeypatch.setattr(pipeline, "crossfade_concat", raising(RuntimeError("ffmpeg went down")))
    with pytest.raises(RuntimeError, match="ffmpeg"):
        film.run()
    sec = sections(film.report())
    assert field(sec["header"], "Outcome") == "stopped at assembly: RuntimeError: ffmpeg went down"
    assert [r[3] for r in rows(sec["Scenes"])] == ["ai", "still", "still", "manim", "manim"]
    timings = dict(rows(sec["Timings"]))
    assert timings["scene clips"] != "not reached" and timings["assembly"].endswith("(stopped here)")
    assert timings["metadata"] == timings["thumbnail"] == timings["QA"] == "not reached"
    assert sec["QA"].strip() == "Not run."


def test_a_stopped_rerun_does_not_show_the_scenes_of_the_run_before(film, monkeypatch):
    film.run()
    assert len(rows(sections(film.report())["Scenes"])) == 5
    assert (film.work / "manifest.json").exists()

    monkeypatch.setattr(pipeline, "make_visual", raising(RuntimeError("down")))
    with pytest.raises(RuntimeError):
        film.run()
    sec = sections(film.report())
    assert rows(sec["Scenes"]) == [] and "not reached" in sec["Scenes"].lower()
    assert sec["QA"].strip() == "Not run.", "and not the earlier run's QA either"


@pytest.mark.parametrize("error, outcome", [
    (ledger.BudgetExceeded("google call (~€0.060) would take spend over BUDGET_EUR €100.00"),
     "BudgetExceeded: google call (~€0.060) would take spend over BUDGET_EUR €100.00"),
    (KeyboardInterrupt(), "KeyboardInterrupt"),
    (ValueError("x" * 400), "ValueError: " + "x" * 299 + "…"),
])
def test_the_outcome_names_the_error_type_and_the_start_of_its_message(film, monkeypatch, error, outcome):
    monkeypatch.setattr(pipeline, "make_visual", raising(error))
    with pytest.raises(type(error)):
        film.run()
    assert field(sections(film.report())["header"], "Outcome") == f"stopped at visuals: {outcome}"


def test_ctrl_c_is_a_stop_that_the_same_command_resumes(film, monkeypatch):
    monkeypatch.setattr(pipeline, "make_visual", raising(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        film.run()
    steps = sections(film.report())["Next steps"]
    assert ("- Stopped by Ctrl-C at visuals; rerun the same command to resume "
            "(nothing finished is bought again).") in steps
    assert "see the error" not in steps


def test_a_refused_brief_does_not_tell_you_to_rerun_the_same_command(film, monkeypatch, tmp_path):
    """The fix for a refused run is a different command (the brief it was written with)."""
    monkeypatch.setattr(pipeline, "get_script", REAL_GET_SCRIPT)  # the film fixture fakes it
    film.work.mkdir(parents=True)
    (film.work / "outline.json").write_text("{}")  # a script written without a brief...
    brief = tmp_path / "brief.md"
    brief.write_text("# Facts\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="without a brief; this run has one"):
        film.run("--brief", str(brief))  # ...and this run has one
    sec = sections(film.report())
    assert field(sec["header"], "Outcome").startswith("stopped at script: RuntimeError: The script in ")
    assert "see the error above" in sec["Next steps"] and "same command" not in sec["Next steps"]


def test_a_stop_before_the_first_stage_is_a_stop_at_setup(tmp_path):
    work = tmp_path / "build" / "a-film"
    with pytest.raises(OSError), run_report.recording(work, "A film", None, None, allow_veo=True):
        raise OSError("disk full")
    sec = sections((work / "run_report.md").read_text(encoding="utf-8"))
    assert field(sec["header"], "Outcome") == "stopped at setup: OSError: disk full"
    assert [dict(rows(sec["Timings"]))[s] for s in STAGES] == ["not reached"] * len(STAGES)


def test_a_review_log_that_cannot_be_read_does_not_cost_the_report(tmp_path):
    work = tmp_path / "build" / "a-film"
    scene = a_scene(1, "still", "a picture")
    review = image_agent.review_path(scene, work / "visuals")
    review.parent.mkdir(parents=True)
    review.write_text("{not json")
    with run_report.recording(work, "A film", None, None, allow_veo=True) as run:
        row = run_report.SceneRow(1, "c1", "still", "manim")
        assert [r.text for r in run._reasons(scene, row, {})] == ["reason unreadable (JSONDecodeError)"]


def test_a_script_only_run_writes_a_report_that_says_so(film):
    film.run("--script-only")
    sec = sections(film.report())
    assert field(sec["header"], "Outcome") == "script only"
    assert field(sec["header"], "Film") == "Test film"
    timings = dict(rows(sec["Timings"]))
    assert re.fullmatch(r"\d+\.\d s", timings["script"])
    assert [timings[s] for s in STAGES[1:]] == ["not reached"] * (len(STAGES) - 1)
    assert sec["QA"].strip() == "Not run."
    assert "rerun without --script-only" in sec["Next steps"]
    assert {r[0] for r in rows(sec["Costs"])} == {"anthropic", "Total"}


def test_a_key_in_an_error_message_never_reaches_the_report(film, monkeypatch):
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "sk-ant-offline-secret-123")
    monkeypatch.setattr(config, "GOOGLE_API_KEY", "AIza-offline-secret-456")

    monkeypatch.setattr(pipeline, "make_visual", raising(RuntimeError(
        "403 for https://example.test/v1?key=AIza-offline-secret-456 (sk-ant-offline-secret-123)")))
    with pytest.raises(RuntimeError):
        film.run()
    text = film.report()
    assert "secret" not in text
    assert field(sections(text)["header"], "Outcome") == (
        "stopped at visuals: RuntimeError: 403 for https://example.test/v1?key=*** (***)")


def test_a_key_in_a_failure_on_file_or_in_a_concept_never_reaches_the_report(film, monkeypatch):
    """The failure log is redacted when it is written; the report does not rely on that."""
    monkeypatch.setattr(config, "GOOGLE_API_KEY", "AIza-offline-secret-456")
    film.script["scenes"][0]["concept"] = "AIza-offline-secret-456"
    veo_key = ai_video.cache_path(film.script["scenes"][1], film.work / "visuals").stem
    save_json(film.work / "failures.json", {veo_key: {
        "what": "veo", "scene": 2, "time": "2026-10-13T10:00:00+00:00",
        "error": "RuntimeError: 403 for https://example.test/v1?key=AIza-offline-secret-456"}})
    film.run()
    text = film.report()
    assert "secret" not in text
    assert "Veo failed: RuntimeError: 403 for https://example.test/v1?key=***" in text


def test_a_key_that_the_outcome_line_would_cut_in_two_is_redacted_before_the_cut(film, monkeypatch):
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "sk-ant-offline-secret-123")
    monkeypatch.setattr(pipeline, "make_visual", raising(RuntimeError("y" * 290 + "sk-ant-offline-secret-123")))
    with pytest.raises(RuntimeError):
        film.run()
    text = film.report()
    assert "sk-ant" not in text and "secret" not in text
    assert field(sections(text)["header"], "Outcome").endswith("RuntimeError: " + "y" * 290 + "***")


def test_a_key_in_the_film_title_is_caught_by_the_last_pass_over_the_text(film, monkeypatch):
    monkeypatch.setattr(config, "ELEVENLABS_API_KEY", "xi-offline-secret-789")
    film.script["title"] = "Film xi-offline-secret-789"
    film.run()
    text = film.report()
    assert "secret" not in text and field(sections(text)["header"], "Film") == "Film ***"


def test_a_key_that_a_table_limit_would_cut_in_two_is_redacted_before_the_cut(film, monkeypatch):
    monkeypatch.setattr(config, "GOOGLE_API_KEY", "AIza-offline-secret-456")
    film.script["scenes"][0]["concept"] = "x" * 190 + "AIza-offline-secret-456"  # a cell holds 200
    film.run()
    text = film.report()
    assert "AIza" not in text and "secret" not in text
    assert rows(sections(text)["Scenes"])[0][1] == "x" * 190 + "***"


def test_cells_stay_on_one_line_and_pipes_are_escaped(film):
    film.script["scenes"][0]["concept"] = "ratio | of\nthings"
    film.run()
    row = rows(sections(film.report())["Scenes"])[0]
    assert row[1] == r"ratio \| of things" and len(row) == 5


def test_the_header_names_the_command_style_brief_and_models(film, tmp_path):
    brief = tmp_path / "brief.md"
    brief.write_text("# Facts\n\n## Sources\n- [A paper](https://example.org/a)\n", encoding="utf-8")
    film.run("--minutes", "12", "--style", "flat", "--brief", str(brief))
    header = sections(film.report())["header"]
    assert header.startswith("# Run report: topic")
    assert re.fullmatch(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d UTC", field(header, "Started"))
    assert field(header, "Command") == f"`python pipeline.py topic --minutes 12 --style flat --brief {brief}`"
    assert field(header, "Style") == "flat"
    assert field(header, "Brief") == f"`{brief}` (key {brief_key(brief.read_text(encoding='utf-8'))})"
    assert field(header, "Models") == (
        f"Claude `{config.CLAUDE_MODEL}`, narration `{config.TTS_MODEL}`, images `{config.IMAGE_MODEL}`, "
        f"Veo `{config.VEO_MODEL}` (allowed)")
    assert field(header, "Outcome") == "finished"


def test_a_rerun_says_it_used_the_kept_brief_and_a_run_without_one_says_none(film, tmp_path):
    film.run()
    assert field(sections(film.report())["header"], "Brief") == "none"

    text = "# Facts\n"
    film.work.joinpath("brief.md").write_text(text, encoding="utf-8")
    film.work.joinpath("brief.key").write_text(brief_key(text), encoding="utf-8")
    film.run()  # the script exists, so the kept brief is used
    assert field(sections(film.report())["header"], "Brief") == (
        f"kept copy `{film.work / 'brief.md'}` (key {brief_key(text)})")


def test_estimate_writes_no_report(film, capsys):
    film.run("--estimate")
    assert "Projected cost" in capsys.readouterr().out
    assert not (film.work / "run_report.md").exists() and not film.work.exists(), "it writes nothing"
    assert ledger_lines() == []


def test_a_usage_error_writes_no_report(film, tmp_path):
    with pytest.raises(SystemExit) as stopped:
        film.run("--brief", str(tmp_path / "missing.md"))
    assert stopped.value.code == 2 and not film.work.exists()


def no_disk(monkeypatch):
    """Writing the report fails, as on a full disk."""
    @contextmanager
    def full(out):
        raise OSError("disk full")
        yield

    monkeypatch.setattr(run_report, "atomic_output", full)


def test_a_report_that_cannot_be_written_does_not_hide_the_error_that_stopped_the_run(film, monkeypatch, capsys):
    no_disk(monkeypatch)
    monkeypatch.setattr(pipeline, "make_visual", raising(RuntimeError("the real cause")))
    with pytest.raises(RuntimeError, match="the real cause"):
        film.run()
    assert "could not write the run report (OSError: disk full)" in capsys.readouterr().out


def test_a_finished_run_whose_report_cannot_be_written_still_finishes(film, monkeypatch, capsys):
    no_disk(monkeypatch)
    film.run()
    assert "could not write the run report" in capsys.readouterr().out
    assert (film.work / "test-film.mp4").exists()


def test_a_console_that_has_gone_away_does_not_hide_the_error_that_stopped_the_run(film, monkeypatch):
    """stdout piped to `head`: the line saying where the report went raises BrokenPipeError."""
    def gone(message):
        raise BrokenPipeError(32, "Broken pipe")

    monkeypatch.setattr(run_report, "log", gone)
    monkeypatch.setattr(pipeline, "make_visual", raising(RuntimeError("the real cause")))
    with pytest.raises(RuntimeError, match="the real cause"):
        film.run()
    assert field(sections(film.report())["header"], "Outcome").startswith("stopped at visuals: RuntimeError")


def test_a_console_that_has_gone_away_does_not_stop_a_finished_run_either(film, monkeypatch):
    def gone(message):
        raise BrokenPipeError(32, "Broken pipe")

    monkeypatch.setattr(run_report, "log", gone)
    no_disk(monkeypatch)  # and the "could not write" line is lost the same way
    film.run()
    assert (film.work / "test-film.mp4").exists()


def test_a_rerun_keeps_the_report_it_replaces_as_the_previous_one(film, monkeypatch):
    film.run()
    first = film.report()
    assert "Previous report" not in first and not (film.work / "run_report.prev.md").exists()

    monkeypatch.setattr(pipeline, "make_visual", raising(RuntimeError("down")))
    with pytest.raises(RuntimeError):
        film.run()
    second = film.report()
    assert (film.work / "run_report.prev.md").read_text(encoding="utf-8") == first, \
        "the stopped rerun did not take the finished run's scenes and QA with it"
    assert field(sections(second)["header"], "Previous report") == "`run_report.prev.md` (the run before this one)"
    assert field(sections(second)["header"], "Outcome").startswith("stopped at visuals")

    with pytest.raises(RuntimeError):
        film.run()
    assert (film.work / "run_report.prev.md").read_text(encoding="utf-8") == second, "only one is kept"
    assert not list(film.work.glob("run_report*.partial*")), "nothing half-written is left"


def test_a_report_that_fails_to_render_leaves_the_old_one_in_place(film, monkeypatch):
    film.run()
    first = film.report()
    monkeypatch.setattr(run_report.Run, "render", raising(KeyError("a field went missing")))
    film.run()
    assert film.report() == first and not (film.work / "run_report.prev.md").exists()


def test_the_run_report_is_named_in_the_output(film, capsys):
    film.run()
    assert f"• run report: {film.work / 'run_report.md'}" in capsys.readouterr().out


def test_stage_marks_outside_a_run_are_ignored():
    run_report.stage("narration")  # generate_assets called directly, as the other tests do
    assert run_report._current is None


def test_an_unknown_stage_is_a_programming_error_not_a_silent_gap(film, monkeypatch):
    monkeypatch.setattr(pipeline, "make_visual", lambda *a, **k: run_report.stage("scene clip"))
    with pytest.raises(ValueError, match="scene clip"):
        film.run()


# --- layout, with a fixed clock ----------------------------------------------------------------

def test_the_layout_and_the_numbers_with_a_fixed_clock(tmp_path, monkeypatch):
    now = [0.0]
    monkeypatch.setattr(run_report, "_clock", lambda: now[0])
    monkeypatch.setattr(run_report, "_now", lambda: datetime(2026, 10, 14, 9, 30, 5, tzinfo=timezone.utc))
    work = tmp_path / "build" / "a-film"
    with run_report.recording(work, "A film", None, None, allow_veo=True) as run:
        now[0] = 2.0
        run_report.stage("script")
        now[0] = 5.0
        run_report.stage("narration")
        now[0] = 9.5
        run.script = {"title": "A Title", "scenes": []}
        run.outcome = "script only"
    text = (work / "run_report.md").read_text(encoding="utf-8")
    assert list(sections(text)) == ["header", "Costs", "Timings", "Scenes", "QA", "Next steps"]
    assert field(text, "Started") == "2026-10-14 09:30:05 UTC"
    assert dict(rows(sections(text)["Timings"])) == {
        "script": "3.0 s", "narration": "4.5 s", "visuals": "not reached", "scene clips": "not reached",
        "assembly": "not reached", "metadata": "not reached", "thumbnail": "not reached", "QA": "not reached",
        "Total": "9.5 s"}
