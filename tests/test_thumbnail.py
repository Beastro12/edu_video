"""P2-2: a finished film gets build/<slug>/thumbnail.jpg (1280x720, under YouTube's 2 MB) next to
metadata.json, which names it. The picture is one image from the image model (image_agent's own
request, cache, ledger entry and failure record), never asked for text; the title is drawn by
us. Without a key, or when the model can't be used, it is a frame of the film. A thumbnail never
fails the run. The image model is faked at the HTTP boundary, as in test_images.py."""
import io
import random
import shutil
import signal
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest
from conftest import build_film
from PIL import Image, ImageChops, ImageStat, features
from test_images import gemini_via, image_response
from test_run_report import make_film

import assembly
import config
import failures
import ledger
import metadata
import pipeline
import qa
import run_report
import thumbnail
from agents import image_agent
from utils import load_json, save_json

REAL_WRITE_METADATA, REAL_MAKE = metadata.write_metadata, thumbnail.make  # make_film stands them in for
TITLE = "A Small Lit Stage: Global Workspace Theory and Language Models"
BLUE, GREEN = (0x44, 0x66, 0xAA), (0x33, 0xAA, 0x66)
FLAT_STILL = config.STYLES["flat"]["still"]
CALM_STILL = config.STYLES["calm"]["still"]
SIZE = config.THUMBNAIL_SIZE
RIGHT = (1100, 360)  # the right two thirds hold the picture, untouched by the title and its gradient


def png(colour=BLUE) -> bytes:
    data = io.BytesIO()
    Image.new("RGB", (640, 360), colour).save(data, "PNG")
    return data.getvalue()


def answer(colour=BLUE) -> dict:
    return image_response(png(colour), "image/png")


def pixel(path, at=RIGHT):
    return Image.open(path).convert("RGB").getpixel(at)


def near(got, want, tol=8) -> bool:
    return all(abs(a - b) <= tol for a, b in zip(got, want, strict=True))


def spent() -> list[dict]:
    return ledger.entries()


@pytest.fixture(scope="module")
def built(media, tmp_path_factory):
    """A real two-scene film (320x180, sine tones for the voice) in the pipeline's layout."""
    size = assembly.W, assembly.H
    assembly.W, assembly.H = 320, 180
    try:
        final = build_film(tmp_path_factory.mktemp("film") / "film", media,
                           narrations=["calm " * 6, "slow " * 7])
    finally:
        assembly.W, assembly.H = size
    return final.parent


@pytest.fixture
def work(built, tmp_path):
    """A private copy whose first scene is a still, and its real metadata.json."""
    copy = tmp_path / "work"
    shutil.copytree(built, copy)
    script = load_json(copy / "script.json")
    script["title"] = TITLE
    script["scenes"][0].update(visual_type="still", visual_description="a dark sky")
    save_json(copy / "script.json", script)
    metadata.write_metadata(copy)
    return copy


@pytest.fixture
def image_model(real_google, monkeypatch):
    """The image model on a mock transport: queue what it answers with, in order."""
    outcomes = []
    sent = gemini_via(monkeypatch, outcomes)
    return outcomes, sent


def outcome(work, retry=False):
    """What thumbnail.make returns."""
    return thumbnail.make(work, failures.Failures(failures.path(work), retry=retry))


def make(work, retry=False):
    """The thumbnail's path, None if there is none."""
    return outcome(work, retry).path


@contextmanager
def within(seconds):
    """Fail, rather than hang, if the block runs longer (pytest-timeout isn't installed)."""
    def expired(signum, frame):
        raise TimeoutError(f"still running after {seconds} s: a hang")
    previous = signal.signal(signal.SIGALRM, expired)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


def prompt(request) -> str:
    return request["contents"][0]["parts"][0]["text"]


# --- the picture: one image request, reusing image_agent's ---------------------------------------

def test_with_a_key_the_picture_is_one_request_for_a_wordless_image_in_the_active_style(work, image_model):
    outcomes, sent = image_model
    outcomes.append(answer())
    assert "thumbnail" not in load_json(work / "metadata.json")

    result = outcome(work)
    out = result.path

    assert out == work / "thumbnail.jpg" and result == thumbnail.Outcome("picture", None, None, out)
    [request] = sent
    text = prompt(request)
    assert text.startswith("a dark sky"), "the first still's description is the subject"
    assert text.endswith(CALM_STILL), "then the active style's suffix, as for every still"
    assert "no text" in text.lower() and "left 45%" in text, "the note matches the title column (46%)"
    config_sent = request["generationConfig"]
    assert config_sent["imageConfig"]["aspectRatio"] == "16:9"
    assert config_sent["responseModalities"] == ["IMAGE"]
    assert f"/models/{config.IMAGE_MODEL}:generateContent" in request["url"]

    picture = Image.open(out)
    assert picture.format == "JPEG" and picture.size == SIZE
    assert out.stat().st_size < 2_000_000
    assert near(pixel(out), BLUE), "the picture is the model's, not a frame of the film"
    assert load_json(work / "metadata.json")["thumbnail"] == "thumbnail.jpg"
    [entry] = spent()
    assert (entry["provider"], entry["model"], entry["units"]) == ("google", config.IMAGE_MODEL, {"images": 1})
    assert entry["est_cost_eur"] == pytest.approx(ledger.image_eur())


def test_the_images_cost_is_attributed_to_the_film_and_the_run(work, image_model):
    outcomes, sent = image_model
    outcomes.append(answer())
    with ledger.attribute_to("a-film") as run:
        make(work)
    [entry] = spent()
    assert (entry["film"], entry["run"]) == ("a-film", run)


def test_a_rerun_asks_for_nothing_and_leaves_a_fresh_thumbnail_alone(work, image_model):
    outcomes, sent = image_model
    outcomes.append(answer())
    out = make(work)
    out.write_bytes(b"made by hand")  # an up-to-date thumbnail is not rebuilt, so this is kept

    assert make(work) == out
    assert len(sent) == 1 and len(spent()) == 1
    assert out.read_bytes() == b"made by hand"


def test_a_thumbnail_made_in_one_style_is_not_reused_in_another(work, image_model, monkeypatch):
    outcomes, sent = image_model
    outcomes += [answer(BLUE), answer(GREEN)]
    calm = make(work)
    assert near(pixel(calm), BLUE)

    monkeypatch.setattr(config, "STYLE", "flat")
    flat = make(work)
    assert len(sent) == 2 and prompt(sent[1]).endswith(FLAT_STILL) and CALM_STILL not in prompt(sent[1])
    assert near(pixel(flat), GREEN), "the flat thumbnail is a new picture, not the calm one"

    monkeypatch.setattr(config, "STYLE", "calm")
    assert near(pixel(make(work)), BLUE), "back in calm: its own picture again"
    assert len(sent) == 2 and len(spent()) == 2, "and nothing is bought a third time"


def test_a_new_title_is_drawn_again_without_buying_the_picture_again(work, image_model):
    outcomes, sent = image_model
    outcomes.append(answer())
    out = make(work)
    first = out.read_bytes()

    script = load_json(work / "script.json")
    script["title"] = "Another Title"
    save_json(work / "script.json", script)
    metadata.write_metadata(work)
    assert load_json(work / "metadata.json")["thumbnail"] == "thumbnail.jpg", "metadata keeps naming it"
    make(work)

    assert out.read_bytes() != first
    assert len(sent) == 1 and len(spent()) == 1


def test_a_current_thumbnail_asks_for_nothing_even_when_its_picture_was_deleted(work, image_model):
    outcomes, sent = image_model
    outcomes += [answer(BLUE), answer(GREEN)]
    out = make(work)
    first = out.read_bytes()
    picture = image_agent.cache_path(thumbnail.picture_scene(
        load_json(work / "script.json")["scenes"], work / "visuals"), work / "visuals")
    picture.unlink()

    assert pipeline.estimate_cost(15, work, allow_veo=False, skip_critic=True)["thumbnail"] == 0, \
        "up to date: the estimate buys nothing"
    result = outcome(work)

    assert (result.source, result.path) == ("picture", out)
    assert len(sent) == 1 and len(spent()) == 1 and out.read_bytes() == first and not picture.exists()

    script = load_json(work / "script.json")  # a stale thumbnail does need the picture again
    script["title"] = "Another Title"
    save_json(work / "script.json", script)
    metadata.write_metadata(work)
    assert pipeline.estimate_cost(15, work, allow_veo=False, skip_critic=True)["thumbnail"] == pytest.approx(
        ledger.image_eur())
    assert near(pixel(make(work)), GREEN) and len(sent) == 2 and len(spent()) == 2


# --- the fallback: a frame of the film -------------------------------------------------------------

def test_without_a_key_the_picture_is_a_frame_of_the_film_and_nothing_is_asked(work, monkeypatch, capsys):
    monkeypatch.setattr(config, "GOOGLE_API_KEY", None)  # a request would trip the offline guard

    out = make(work)

    assert Image.open(out).format == "JPEG" and Image.open(out).size == SIZE
    assert ImageStat.Stat(Image.open(out).convert("L").crop((700, 0, 1280, 720))).stddev[0] > 20, \
        "a test pattern, not a flat picture"
    assert spent() == []
    assert load_json(work / "metadata.json")["thumbnail"] == "thumbnail.jpg"
    assert "using a frame of the film instead (no GOOGLE_API_KEY)" in capsys.readouterr().out


def test_the_frame_comes_from_the_first_still_or_manim_scene_not_a_veo_clip(tmp_path):
    save_json(tmp_path / "manifest.json", {"scenes": [
        {"kind": "ai", "clip": "clips/a.mp4"}, {"kind": "still", "clip": "clips/b.mp4"},
        {"kind": "manim", "clip": "clips/c.mp4"}]})
    assert thumbnail._frame_clip(tmp_path) == tmp_path / "clips/b.mp4"
    save_json(tmp_path / "manifest.json", {"scenes": [{"kind": "ai", "clip": "clips/a.mp4"}]})
    assert thumbnail._frame_clip(tmp_path) == tmp_path / "clips/a.mp4", "a film of Veo clips only"


def test_a_lasting_failure_falls_back_is_recorded_and_not_asked_again(work, image_model, capsys):
    outcomes, sent = image_model
    outcomes.append(400)  # a request the model refuses for good

    out = make(work)

    assert out.exists() and Image.open(out).size == SIZE and spent() == []
    [(key, entry)] = load_json(work / "failures.json").items()
    assert key == image_agent.cache_path(thumbnail.picture_scene(
        load_json(work / "script.json")["scenes"], work / "visuals"), work / "visuals").stem
    assert entry["what"] == "still" and entry["scene"] == "thumbnail" and "400" in entry["error"]
    assert "using a frame of the film instead (image model failed" in capsys.readouterr().out

    capsys.readouterr()
    make(work)
    shown = capsys.readouterr().out
    assert len(sent) == 1, "the rerun does not ask again"
    assert "failed earlier" in shown and "--retry-failed" in shown

    outcomes.append(answer())
    retried = make(work, retry=True)
    assert len(sent) == 2 and near(pixel(retried), BLUE), "--retry-failed asks again"
    assert load_json(work / "failures.json") == {} and len(spent()) == 1


def test_a_transient_failure_is_not_recorded_so_the_next_run_asks_again(work, image_model):
    outcomes, sent = image_model
    outcomes += [503] * (config.MAX_RETRIES + 1) + [answer()]
    make(work)
    assert len(sent) == config.MAX_RETRIES + 1 and spent() == []
    assert not (work / "failures.json").exists() or load_json(work / "failures.json") == {}

    assert near(pixel(make(work)), BLUE)
    assert len(sent) == config.MAX_RETRIES + 2


def test_a_spent_budget_gives_a_frame_and_stops_nothing(work, monkeypatch, capsys):
    monkeypatch.setattr(config, "GOOGLE_API_KEY", "offline-test")
    monkeypatch.setattr(config, "BUDGET_EUR", 0.0)  # a request would trip the offline guard

    out = make(work)

    assert out.exists() and spent() == []
    assert not (work / "failures.json").exists(), "nothing was asked of the model: not a failed generation"
    assert "using a frame of the film instead (budget spent" in capsys.readouterr().out


def test_a_film_of_diagrams_has_no_subject_so_it_gets_a_frame_without_a_request(work, monkeypatch):
    monkeypatch.setattr(config, "GOOGLE_API_KEY", "offline-test")  # a request would trip the offline guard
    script = load_json(work / "script.json")
    script["scenes"][0]["visual_type"] = "manim"
    save_json(work / "script.json", script)
    assert make(work).exists() and spent() == []


def a_scene(kind, text, concept="c"):
    return {"visual_type": kind, "visual_description": text, "concept": concept}


def test_the_subject_is_the_first_still_else_the_first_veo_scene_never_a_diagram(tmp_path):
    scenes = [a_scene("manim", "diagram"), a_scene("ai_video", "waves"), a_scene("still", "a dark sky "),
              a_scene("still", "later")]
    picked = thumbnail.picture_scene(scenes, tmp_path)
    assert picked["visual_description"] == "a dark sky" + thumbnail._composition()
    assert picked["id"] == "thumbnail" and scenes[2]["visual_description"] == "a dark sky ", "the script is untouched"
    assert thumbnail.picture_scene(scenes[:2], tmp_path)["visual_description"].startswith("waves")
    assert thumbnail.picture_scene(scenes[:1], tmp_path) is None and thumbnail.picture_scene([], tmp_path) is None


def test_a_still_the_review_turned_down_for_good_is_not_the_subject(tmp_path):
    scenes = [a_scene("still", "a page of strokes", "one"), a_scene("still", "a dark sky", "two"),
              a_scene("ai_video", "waves", "three")]
    assert thumbnail.picture_scene(scenes, tmp_path)["visual_description"].startswith("a page of strokes")

    def decided(scene, log):
        path = image_agent.review_path(scene, tmp_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        save_json(path, log)

    decided(scenes[0], {"accepted": None, "done": True, "attempts": []})  # rejected for good
    assert thumbnail.picture_scene(scenes, tmp_path)["visual_description"].startswith("a dark sky")
    decided(scenes[1], {"accepted": None, "done": True, "attempts": []})
    assert thumbnail.picture_scene(scenes, tmp_path)["visual_description"].startswith("waves"), "a Veo scene has no review"

    decided(scenes[0], {"accepted": None, "done": False, "attempts": []})  # still being decided: not for good
    (tmp_path / "ok.png").write_bytes(b"png")
    decided(scenes[1], {"accepted": "ok.png", "done": True, "attempts": []})
    assert thumbnail.picture_scene(scenes, tmp_path)["visual_description"].startswith("a page of strokes")


def test_the_composition_note_asks_for_what_the_title_covers(tmp_path, monkeypatch):
    scenes = [a_scene("still", "a dark sky")]
    assert "across about the left 45% of the frame" in thumbnail.picture_scene(scenes, tmp_path)["visual_description"]
    monkeypatch.setattr(config, "THUMBNAIL_COLUMN", 0.31)
    assert "the left 30% of the frame" in thumbnail.picture_scene(scenes, tmp_path)["visual_description"]


# --- the title -------------------------------------------------------------------------------------

def bright(title):
    """The title composed over black, as a mask of the pixels bright enough to be letters."""
    image = thumbnail.compose(Image.new("RGB", SIZE, "black"), title)
    return image.convert("L").point(lambda v: 255 if v > 128 else 0)


def boxes(title, monkeypatch):
    """(headline box, subtitle box) of a split title's letters: the subtitle's pixels are what drawing
    it adds to drawing it invisible."""
    everything = bright(title)
    monkeypatch.setattr(config, "THUMBNAIL_SUBTITLE_OPACITY", 0)
    headline = bright(title)
    return headline.getbbox(), ImageChops.subtract(everything, headline).getbbox()


def in_the_left_part(box) -> bool:
    left, top, right, bottom = box
    return (config.THUMBNAIL_MARGIN_PX - 4 <= left and right <= config.THUMBNAIL_COLUMN * SIZE[0]
            and 0 <= top and bottom <= SIZE[1])


@pytest.mark.parametrize("title", [
    "Black Holes",
    " ".join(["slowly"] * 14),  # 99 characters
    "Supercalifragilisticexpialidocious " * 3,
    "x" * 130,  # one word that is too long for any size
], ids=["short", "long", "long words", "one word"])
def test_a_title_without_a_colon_wraps_to_three_lines_at_most_and_stays_in_the_left_part_of_the_frame(title):
    [block] = thumbnail.fit_title(title)
    low, high = config.THUMBNAIL_FONT_PX[1], config.THUMBNAIL_FONT_PX[0]
    assert 1 <= len(block.lines) <= config.THUMBNAIL_MAX_LINES and low <= block.font.size <= high
    box = bright(title).getbbox()
    assert in_the_left_part(box), "inside the left part, as the pixels are drawn"
    assert box[3] - box[1] <= len(block.lines) * thumbnail._line_height(block.font.size), "no more rows than lines"
    if " ".join(title.split()) == " ".join(block.lines):
        assert not block.lines[-1].endswith("…")
    else:
        assert block.lines[-1].endswith("…"), "a title that can't fit even at the smallest size is cut with an ellipsis"


PINNED_FREETYPE = "2.14.3"  # the build the pins below were made with (Pillow 12.3.0, requirements.lock)


@pytest.mark.skipif(features.version("freetype2") != PINNED_FREETYPE,
                    reason=f"layouts pinned with FreeType {PINNED_FREETYPE}: another build sets text a little "
                           "differently (the margins are a few pixels); the property tests below still hold")
@pytest.mark.parametrize("title, size, lines", [
    ("Black Holes", 110, ["Black", "Holes"]),
    (" ".join(["slowly"] * 14), 34, ["slowly slowly slowly slowly slowly", "slowly slowly slowly slowly slowly",
                                     "slowly slowly slowly slowly"]),
    ("A Small Lit Stage, Global Workspace Theory and Language Models", 47,  # 46 when sizes went down by 2
     ["A Small Lit Stage, Global", "Workspace Theory and", "Language Models"]),
    ("Why the Night Sky Is Dark", 110, ["Why the", "Night Sky", "Is Dark"]),
    ("A Small Lit Stage", 110, ["A Small Lit", "Stage"]),  # greedy, as ever: only a headline is balanced
    ("Why Stars Twinkle at Night", 110, ["Why Stars", "Twinkle at", "Night"]),
    ("Ratio 2:1 of Light", 110, ["Ratio 2:1", "of Light"]),  # a colon without a space is not a split
    ("Note:", 110, ["Note:"]),
    (": leading", 110, [": leading"]),  # nor is one with no headline before it
], ids=["short", "long", "commas", "plain", "greedy", "greedy 3", "ratio", "trailing colon", "leading colon"])
def test_a_title_without_a_split_colon_is_drawn_as_it_was_before_titles_could_be_split(title, size, lines):
    """Pinned from before the split existed. They depend on Pillow's bundled font, which
    requirements.lock pins: a new font that sets them differently is a change to make here."""
    [block] = thumbnail.fit_title(title)
    assert (block.font.size, block.lines, block.opacity, block.above) == (size, lines, 1.0, 0)


def test_the_first_films_title_is_a_headline_and_a_smaller_subtitle_in_the_left_part(monkeypatch):
    headline, subtitle = thumbnail.fit_title(TITLE)
    assert " ".join(headline.lines) == "A Small Lit Stage", "split at the first colon, which is not drawn"
    assert " ".join(subtitle.lines) == "Global Workspace Theory and Language Models"
    assert headline.font.size > 46 * 1.5, "much larger than the whole title was (46 px)"
    assert subtitle.font.size == max(config.THUMBNAIL_FONT_PX[1], round(headline.font.size * config.THUMBNAIL_SUBTITLE_RATIO))
    assert len(headline.lines) <= 2 and len(subtitle.lines) <= 2
    assert (headline.above, subtitle.above) == (0, round(headline.font.size * config.THUMBNAIL_SUBTITLE_GAP))
    assert subtitle.opacity == config.THUMBNAIL_SUBTITLE_OPACITY < headline.opacity == 1.0
    widths = [headline.font.getbbox(line)[2] for line in headline.lines]
    assert min(widths) > 0.6 * max(widths), "no word left alone under a long line"

    headline_box, subtitle_box = boxes(TITLE, monkeypatch)
    assert in_the_left_part(headline_box) and in_the_left_part(subtitle_box)
    assert subtitle_box[1] > headline_box[3], "the subtitle sits below the headline, with a gap"
    assert subtitle_box[3] - subtitle_box[1] <= 2 * thumbnail._line_height(subtitle.font.size)
    assert headline_box[3] - headline_box[1] <= 2 * thumbnail._line_height(headline.font.size)


def test_the_subtitle_is_a_little_dimmer_than_the_headline(monkeypatch):
    image = thumbnail.compose(Image.new("RGB", SIZE, "black"), TITLE)
    dimmer = config.THUMBNAIL_SUBTITLE_OPACITY * config.THUMBNAIL_TITLE_RGB[0]  # (boxes() draws it invisible)
    headline_box, subtitle_box = boxes(TITLE, monkeypatch)
    brightest = lambda box: image.crop(box).getextrema()[0][1]  # the red channel  # noqa: E731
    assert brightest(headline_box) == config.THUMBNAIL_TITLE_RGB[0]
    assert brightest(subtitle_box) == pytest.approx(dimmer, abs=2) and dimmer < config.THUMBNAIL_TITLE_RGB[0]


@pytest.mark.parametrize("title", [
    "A short headline: " + "a subtitle that goes on " * 6,
    "word " * 40 + ": Short sub",
    "Head: " + "word " * 80,
    "word " * 40 + ": " + "more " * 60,
    "x" * 130 + ": " + "y" * 130,
    "Part 1: The Sky: A Story",  # only the first colon splits
], ids=["long subtitle", "long headline", "very long subtitle", "both long", "one word each", "two colons"])
def test_a_headline_and_subtitle_that_are_too_long_are_cut_with_an_ellipsis_and_still_fit(title, monkeypatch):
    headline, subtitle = thumbnail.fit_title(title)
    head, _, sub = " ".join(title.split()).partition(": ")
    for block, text in ((headline, head), (subtitle, sub)):
        assert 1 <= len(block.lines) <= 2 and block.font.size >= config.THUMBNAIL_FONT_PX[1]
        assert (" ".join(block.lines) == text) != block.lines[-1].endswith("…"), "cut exactly when it didn't fit"
    assert subtitle.font.size <= headline.font.size
    head_box, sub_box = boxes(title, monkeypatch)
    assert in_the_left_part(head_box) and in_the_left_part(sub_box) and sub_box[1] > head_box[3]
    if title.startswith("Part 1"):
        assert " ".join(subtitle.lines) == "The Sky: A Story"


def test_the_split_layout_is_part_of_the_stamp_so_a_changed_layout_redraws_for_free(work, monkeypatch):
    monkeypatch.setattr(config, "GOOGLE_API_KEY", None)
    out = make(work)
    first = out.read_bytes()
    assert make(work).read_bytes() == first
    for name, value in (("THUMBNAIL_SUBTITLE_RATIO", 0.6), ("THUMBNAIL_SUBTITLE_GAP", 0.5),
                        ("THUMBNAIL_SUBTITLE_OPACITY", 0.5), ("THUMBNAIL_SPLIT_LINES", (2, 1))):
        with monkeypatch.context() as m:
            m.setattr(config, name, value)
            assert make(work).read_bytes() != first, name
        assert make(work).read_bytes() == first, "and back again"
    assert spent() == []


def test_characters_the_bundled_font_lacks_are_drawn_as_plain_stand_ins_not_as_empty_boxes():
    [block] = thumbnail.fit_title("Schrödinger’s Cat — a story × 2…")
    assert " ".join(block.lines) == "Schrodinger’s Cat - a story x 2…", "the curly apostrophe and the ellipsis it does have"
    headline, subtitle = thumbnail.fit_title("Schrödinger: A Cat — in a box")
    assert (" ".join(headline.lines), " ".join(subtitle.lines)) == ("Schrodinger", "A Cat - in a box")
    box = bytes(block.font.getmask("\ue000"))
    assert all(ch.isspace() or bytes(block.font.getmask(ch)) != box for ch in "".join(block.lines))


@pytest.mark.parametrize("title, drawn", [
    ("Reaching −273.15 Degrees", "Reaching -273.15 Degrees"),  # the minus sign keeps its sign
    ("The Meaning of π", "The Meaning of pi"),
    ("Δ and Ω and Σ and λ", "Delta and Omega and Sigma and lambda"),
    ("Half: ½ and 1⁄2", "Half: 1/2 and 1⁄2"),
    ("H₂O and x² and 10⁻³", "H2O and x2 and 10-3"),
    ("µ meson", "mu meson"),
    ("Ångström Scales", "Angstrom Scales"),
    ("ħ ≈ 1 → ∞", "hbar ~ 1 -> infinity"),
], ids=["minus", "pi", "capitals", "fractions", "sub and superscripts", "micro", "accents", "symbols"])
def test_the_common_physics_characters_the_font_lacks_have_stand_ins(title, drawn):
    text = [" ".join(b.lines) for b in thumbnail.fit_title(title)]
    assert ": ".join(text) == drawn  # a split title's parts are what the colon joined


def test_a_greek_headline_keeps_its_split_and_what_has_no_stand_in_is_left_out_and_logged(capsys):
    headline, subtitle = thumbnail.fit_title("Ψ: The Wave Function")
    assert (" ".join(headline.lines), " ".join(subtitle.lines)) == ("Psi", "The Wave Function")
    assert capsys.readouterr().out == "", "nothing was lost"

    [block] = thumbnail.fit_title("Moon 🌙 and 🌟 light")
    assert " ".join(block.lines) == "Moon and light"
    shown = capsys.readouterr().out
    assert "no way to draw 🌙 🌟 in the title; left out of the thumbnail" in shown


def test_the_stand_ins_and_the_pillow_version_are_part_of_the_stamp(work, monkeypatch, capsys):
    monkeypatch.setattr(config, "GOOGLE_API_KEY", None)
    make(work)
    capsys.readouterr()
    make(work)
    assert "up to date" in capsys.readouterr().out
    with monkeypatch.context() as m:
        m.setitem(thumbnail.STAND_INS, "π", "PI")
        make(work)
        assert "up to date" not in capsys.readouterr().out, "a changed stand-in redraws"
    make(work)
    capsys.readouterr()
    with monkeypatch.context() as m:
        m.setattr(thumbnail.PIL, "__version__", "99.0.0")  # another Pillow, another bundled font
        make(work)
        assert "up to date" not in capsys.readouterr().out


def test_the_font_shrinks_for_a_longer_title_and_is_large_for_a_short_one():
    sizes = [thumbnail.fit_title(t)[0].font.size for t in ("Black Holes", "A Small Lit Stage, Global Workspace "
                                                            "Theory and Language Models", " ".join(["slowly"] * 14))]
    assert sizes[0] > sizes[1] > sizes[2]


def test_the_title_is_off_white_with_a_dark_gradient_behind_it_and_the_right_of_the_picture_is_untouched():
    grey = (120, 130, 140)
    image = thumbnail.compose(Image.new("RGB", SIZE, grey), TITLE)
    light = [sum(image.getpixel((x, 30))) / 3 for x in (2, 500, 580, 700, 1000)]
    assert light[0] < 60 and light[0] == pytest.approx(light[1], abs=1) == pytest.approx(light[2], abs=1), \
        "as dark at the title column's right edge (589 px) as at the left edge"
    assert light[2] < light[3] < light[4] == sum(grey) / 3, "then it fades out"
    assert image.getpixel((1000, 100)) == image.getpixel((1270, 700)) == grey
    colours = {rgb for _, rgb in image.crop((0, 200, 600, 520)).getcolors(maxcolors=600 * 320)}
    assert config.THUMBNAIL_TITLE_RGB in colours, "the title's own colour"


def test_the_shadows_drop_is_a_setting(monkeypatch):
    grey = Image.new("RGB", SIZE, (120, 130, 140))
    assert len(config.THUMBNAIL_SHADOW) == 3
    drawn = {}
    for drop in (0.0, 0.3):
        with monkeypatch.context() as m:
            m.setattr(config, "THUMBNAIL_SHADOW", (*config.THUMBNAIL_SHADOW[:2], drop))
            drawn[drop] = thumbnail.compose(grey, TITLE)
    assert drawn[0.0].tobytes() != drawn[0.3].tobytes()


TITLES = [
    "Black Holes", TITLE, " ".join(["slowly"] * 14), "Supercalifragilisticexpialidocious " * 3, "x" * 130,
    "A short headline: " + "a subtitle that goes on " * 6, "word " * 40 + ": Short sub", "Head: " + "word " * 80,
    "word " * 40 + ": " + "more " * 60, "x" * 130 + ": " + "y" * 130, "Part 1: The Sky: A Story",
    "Why the Night Sky Is Dark: A Slow Look at Light", "Note:", ": leading", "Ψ: The Wave Function",
]


@pytest.mark.parametrize("font_px", [(110, 30), (100, 25), (111, 30), (64, 31), (40, 40)])
def test_any_font_range_even_or_odd_is_searched_to_its_smallest_size_and_never_hangs(font_px, monkeypatch):
    monkeypatch.setattr(config, "THUMBNAIL_FONT_PX", font_px)
    largest, smallest = font_px
    with within(60):
        for title in TITLES:
            blocks = thumbnail.fit_title(title)
            assert all(smallest <= b.font.size <= largest for b in blocks), title
    if smallest == 30:  # a text that fits only at the smallest size is drawn at it, whether or not the range is odd
        [block] = thumbnail.fit_title("x" * 130)
        monkeypatch.setattr(config, "THUMBNAIL_FONT_PX", (110, 30))
        assert block.font.size == 30 and thumbnail.fit_title("x" * 130)[0].lines == block.lines


def tall_of(blocks) -> int:
    return sum(b.above + len(b.lines) * thumbnail._line_height(b.font.size) for b in blocks)


def test_a_frame_too_short_for_the_whole_title_cuts_the_tallest_part_until_it_fits(monkeypatch):
    with within(60):
        monkeypatch.setattr(config, "THUMBNAIL_SIZE", (1280, 200))  # 56 px inside the margins: one line, at best
        [block] = thumbnail.fit_title("Why the Night Sky Is Dark and Why Stars Twinkle at Night")
        assert len(block.lines) == 1 and block.lines[0].endswith("…") and tall_of([block]) <= 56

        monkeypatch.setattr(config, "THUMBNAIL_SIZE", (1280, 230))  # 86 px: a headline line and one of subtitle
        headline, subtitle = thumbnail.fit_title(TITLE)
        assert " ".join(headline.lines) == "A Small Lit Stage", "the headline is as it was"
        assert len(subtitle.lines) == 1 and subtitle.lines[0].endswith("…"), "the subtitle, the tallest, is cut"
        assert tall_of([headline, subtitle]) <= 86


def test_a_frame_with_no_room_for_a_title_is_an_error_not_a_hang(monkeypatch):
    monkeypatch.setattr(config, "THUMBNAIL_SIZE", (1280, 150))  # 6 px inside the margins
    with within(60):
        for title in ("Black Holes", TITLE):
            with pytest.raises(ValueError, match="can't be made to fit the frame"):
                thumbnail.fit_title(title)


@pytest.mark.parametrize("title", TITLES)
def test_the_title_is_set_at_the_largest_size_that_fits_and_every_line_fits_the_column(title):
    blocks = thumbnail.fit_title(title)
    width = thumbnail._column()
    assert all(b.font.getbbox(line)[2] <= width for b in blocks for line in b.lines)
    parts = thumbnail._parts(title)
    uncut = all(" ".join(b.lines) == part[0] for b, part in zip(blocks, parts, strict=True))
    if uncut:  # (what is cut is checked with the ellipsis)
        size = blocks[0].font.size
        assert thumbnail._blocks(parts, size, width) is not None
        assert size == config.THUMBNAIL_FONT_PX[0] or thumbnail._blocks(parts, size + 1, width) is None, \
            "one pixel larger would not fit"


def test_a_picture_that_is_too_big_is_compressed_harder_until_it_fits(monkeypatch):
    noise = Image.frombytes("RGB", SIZE, random.Random(1).randbytes(SIZE[0] * SIZE[1] * 3))
    composed = thumbnail.compose(noise, TITLE)
    at_first_quality = io.BytesIO()
    composed.save(at_first_quality, "JPEG", quality=config.THUMBNAIL_JPEG_QUALITY[0], optimize=True)
    limit = at_first_quality.tell() - 100_000
    monkeypatch.setattr(config, "THUMBNAIL_MAX_BYTES", limit)
    data = thumbnail.encode(composed)
    assert len(data) <= limit and Image.open(io.BytesIO(data)).size == SIZE
    monkeypatch.setattr(config, "THUMBNAIL_MAX_BYTES", 1000)
    with pytest.raises(ValueError, match="over YouTube's 1000 bytes"):
        thumbnail.encode(composed)


# --- never fails the run ---------------------------------------------------------------------------

def test_a_thumbnail_that_cannot_be_made_is_logged_and_leaves_no_file(work, monkeypatch, capsys):
    monkeypatch.setattr(config, "GOOGLE_API_KEY", None)
    monkeypatch.setattr(config, "THUMBNAIL_MAX_BYTES", 1000)  # nothing fits
    assert make(work) is None
    assert "thumbnail: skipped (ValueError: over YouTube's 1000 bytes" in capsys.readouterr().out
    assert not (work / "thumbnail.jpg").exists() and not list(work.glob("thumbnail*"))
    assert "thumbnail" not in load_json(work / "metadata.json")

    (work / "metadata.json").unlink()
    monkeypatch.setattr(config, "THUMBNAIL_MAX_BYTES", 2_000_000)
    assert make(work) is None
    assert "thumbnail: skipped (FileNotFoundError" in capsys.readouterr().out


def test_a_thumbnail_that_cannot_be_redrawn_is_removed_and_no_longer_named(work, image_model, monkeypatch, capsys):
    outcomes, sent = image_model
    outcomes.append(answer())
    out = make(work)
    script = load_json(work / "script.json")
    script["title"] = "A Completely Different Title"
    save_json(work / "script.json", script)
    metadata.write_metadata(work)
    assert load_json(work / "metadata.json")["thumbnail"] == "thumbnail.jpg", "named, with the old title on disk"
    real_encode = thumbnail.encode
    monkeypatch.setattr(thumbnail, "encode", lambda image: (_ for _ in ()).throw(OSError("disk full")))
    capsys.readouterr()

    result = outcome(work)

    assert (result.source, result.path) == ("none", None) and result.reason == "OSError: disk full"
    assert not out.exists() and not (work / "thumbnail.jpg.key").exists(), "the old title can't be uploaded"
    assert "thumbnail" not in load_json(work / "metadata.json")
    shown = capsys.readouterr().out
    assert "skipped (OSError: disk full)" in shown and "removed the old thumbnail.jpg" in shown

    monkeypatch.setattr(thumbnail, "encode", real_encode)  # and the next run makes it again, buying nothing
    assert outcome(work).source == "picture" and len(sent) == 1
    assert load_json(work / "metadata.json")["thumbnail"] == "thumbnail.jpg"


def test_a_failure_after_the_thumbnail_is_current_keeps_it(work, monkeypatch):
    monkeypatch.setattr(config, "GOOGLE_API_KEY", None)
    out = make(work)
    first = out.read_bytes()
    monkeypatch.setattr(metadata, "add_thumbnail", lambda work: (_ for _ in ()).throw(OSError("disk full")))
    assert outcome(work).source == "none"
    assert out.read_bytes() == first and (work / "thumbnail.jpg.key").exists(), "it is for this title: not stale"


@pytest.mark.parametrize("flag", ["stopping", "interrupted"])
def test_a_run_that_is_stopping_is_not_absorbed_by_the_thumbnail(work, image_model, flag):
    outcomes, sent = image_model
    outcomes.append(400)
    getattr(ledger, flag).set()
    try:
        with pytest.raises(Exception):  # noqa: B017  (the budget stop, or the model's refusal: either way it propagates)
            make(work)
    finally:
        getattr(ledger, flag).clear()
    assert not (work / "thumbnail.jpg").exists()
    assert not (work / "failures.json").exists() or load_json(work / "failures.json") == {}


def test_a_key_in_an_error_is_redacted_before_the_message_is_cut(work, monkeypatch, capsys):
    secret = "AIza-secret-offline"
    monkeypatch.setattr(config, "GOOGLE_API_KEY", secret)
    leaky = RuntimeError("x" * 90 + f" key={secret}")  # a cut at 100 characters would leave "AIza-"
    monkeypatch.setattr(image_agent, "_buy", lambda scene, out: (_ for _ in ()).throw(leaky))
    result = outcome(work)
    shown = capsys.readouterr().out + str(result)
    assert "AIza" not in shown and "***" in shown

    monkeypatch.setattr(thumbnail, "encode", lambda image: (_ for _ in ()).throw(ValueError("x" * 190 + secret)))
    (work / "failures.json").unlink()  # let it go on to the encode
    shown = capsys.readouterr().out + str(outcome(work))
    assert "AIza" not in shown


def test_the_outcome_says_where_the_picture_came_from_and_why_not_the_models(work, image_model, monkeypatch, capsys):
    outcomes, sent = image_model
    out = work / "thumbnail.jpg"
    with monkeypatch.context() as m:  # no key
        m.setattr(config, "GOOGLE_API_KEY", None)
        assert outcome(work) == thumbnail.Outcome("frame", "no GOOGLE_API_KEY", None, out)

    outcomes.append(400)  # a lasting failure: recorded, and the hint says how to ask again
    first = outcome(work)
    assert (first.source, first.hint, first.path) == ("frame", thumbnail.RETRY, out)
    assert first.reason.startswith("image model failed (") and "400" in first.reason
    again = outcome(work)  # ...and the rerun knows it
    assert (again.source, again.hint) == ("frame", thumbnail.RETRY)
    assert again.reason.startswith("image model failed earlier (") and "400" in again.reason

    (work / "failures.json").unlink()
    outcomes += [503] * (config.MAX_RETRIES + 1)  # a transient one: nothing recorded, a rerun asks again
    transient = outcome(work)
    assert (transient.source, transient.hint) == ("frame", thumbnail.AGAIN)

    with monkeypatch.context() as m:  # budget spent: nothing was asked
        m.setattr(config, "BUDGET_EUR", 0.0)
        broke = outcome(work)
    assert (broke.source, broke.path) == ("frame", out) and broke.reason.startswith("budget spent (")
    assert "budget" in broke.hint

    with monkeypatch.context() as m:  # a film with no subject
        script = load_json(work / "script.json")
        script["scenes"][0]["visual_type"] = "manim"
        save_json(work / "script.json", script)
        m.setattr(config, "GOOGLE_API_KEY", "offline-test")
        assert outcome(work).reason == "the film has no still or Veo scene to take a subject from"


def test_ctrl_c_is_not_swallowed(work, monkeypatch):
    monkeypatch.setattr(thumbnail, "compose", lambda *a: (_ for _ in ()).throw(KeyboardInterrupt()))
    monkeypatch.setattr(config, "GOOGLE_API_KEY", None)
    with pytest.raises(KeyboardInterrupt):
        make(work)


# --- the estimate ----------------------------------------------------------------------------------

def planned(work, *kinds):
    """A script in `work` whose scenes have these visual types; returns their scenes."""
    scenes = [{"id": i, "concept": f"c{i}", "narration": "n" * 50, "visual_type": kind,
               "visual_description": f"scene {i}"} for i, kind in enumerate(kinds, 1)]
    work.mkdir(parents=True, exist_ok=True)
    save_json(work / "script.json", {"title": "T", "scenes": scenes})
    return scenes


def thumbnail_estimate(work, **kw):
    return pipeline.estimate_cost(15, work, allow_veo=False, skip_critic=True, **kw)["thumbnail"]


def test_the_estimate_counts_the_thumbnail_once_with_a_key_and_no_picture_cached(tmp_path, monkeypatch):
    work = tmp_path / "w"
    scenes = planned(work, "still", "manim")
    monkeypatch.setattr(config, "GOOGLE_API_KEY", "offline-test")
    assert thumbnail_estimate(work) == pytest.approx(ledger.image_eur())
    worst = pipeline.estimate_cost(15, work, allow_veo=False, skip_critic=True, worst_case=True)["thumbnail"]
    assert worst == pytest.approx(ledger.image_eur()), "one image, with no review to repeat"

    cached = image_agent.cache_path(thumbnail.picture_scene(scenes, work / "visuals"), work / "visuals")
    cached.parent.mkdir(parents=True)
    cached.write_bytes(png())
    assert thumbnail_estimate(work) == 0, "already made: a rerun buys nothing"


def test_the_estimate_has_no_thumbnail_without_a_key_or_a_subject(tmp_path, monkeypatch):
    work = tmp_path / "w"
    planned(work, "still", "manim")
    monkeypatch.setattr(config, "GOOGLE_API_KEY", None)
    assert thumbnail_estimate(work) == 0, "a frame is free"
    monkeypatch.setattr(config, "GOOGLE_API_KEY", "offline-test")
    planned(work, "manim", "manim")
    assert thumbnail_estimate(work) == 0, "a film of diagrams has no subject for a picture"


def test_the_estimate_knows_a_failed_thumbnail_until_failures_are_retried(tmp_path, monkeypatch):
    work = tmp_path / "w"
    scenes = planned(work, "still")
    monkeypatch.setattr(config, "GOOGLE_API_KEY", "offline-test")
    stem = image_agent.cache_path(thumbnail.picture_scene(scenes, work / "visuals"), work / "visuals").stem
    failures.Failures(failures.path(work)).record(stem, "still", "thumbnail", RuntimeError("no image"))
    assert thumbnail_estimate(work) == 0, "it failed before: the frame is used, which is free"
    assert thumbnail_estimate(work, retry_failed=True) == pytest.approx(ledger.image_eur())


def test_the_estimate_is_printed_with_the_thumbnail_in_the_total(tmp_path, monkeypatch, capsys):
    work = Path(config.BUILD_DIR) / "topic"
    planned(work, "still", "manim")
    monkeypatch.setattr(config, "GOOGLE_API_KEY", "offline-test")
    est = pipeline.estimate_cost(15, work, allow_veo=False, skip_critic=False)
    monkeypatch.setattr(sys, "argv", ["pipeline.py", "topic", "--estimate"])
    pipeline.main()
    out = capsys.readouterr().out
    assert f"  thumbnail   €{est['thumbnail']:.2f}" in out and est["thumbnail"] > 0
    assert f"€{sum(est.values()):.2f}" in out


# --- in the pipeline -------------------------------------------------------------------------------

def run_main(monkeypatch, calls, *argv):
    """pipeline.main() with the film, its metadata and QA stood in for (each appends its name to
    `calls`), so that only the thumbnail stage is the test's: patch thumbnail.make or leave it real."""
    work = Path(config.BUILD_DIR) / "topic"

    def stage(name, result=None):
        return lambda *args, **kwargs: calls.append(name) or result

    monkeypatch.setattr(pipeline, "get_script", lambda *a, **k: {"title": "T", "scenes": []})
    monkeypatch.setattr(pipeline, "render_film", lambda *a, **k: work / "film.mp4")
    monkeypatch.setattr(pipeline.metadata, "write_metadata", stage("metadata"))
    monkeypatch.setattr(qa, "run_qa", lambda path: calls.append("qa") or {"video": "film.mp4", "passed": True,
                                                                           "checks": {}})
    monkeypatch.setattr(qa, "print_report", lambda report: None)
    monkeypatch.setattr(sys, "argv", ["pipeline.py", "topic", *argv])
    pipeline.main()
    return work


def test_the_thumbnail_is_made_after_the_metadata_and_before_qa_and_is_a_stage_of_the_report(monkeypatch):
    calls, seen = [], []
    monkeypatch.setattr(pipeline.thumbnail, "make", lambda work, log: calls.append("thumbnail") or seen.append(
        (work, log.retry, log.path)))
    work = run_main(monkeypatch, calls, "--retry-failed")
    assert calls == ["metadata", "thumbnail", "qa"]
    assert seen == [(work, True, failures.path(work))], "it records failures where the stills do, and retries with them"
    assert run_report.STAGES.index("thumbnail") == run_report.STAGES.index("metadata") + 1
    timings = (work / "run_report.md").read_text(encoding="utf-8").split("## Timings")[1].split("## Scenes")[0]
    assert "| thumbnail | not reached |" not in timings and "| thumbnail |" in timings


def test_a_thumbnail_that_goes_wrong_does_not_stop_the_run_before_qa(monkeypatch, capsys):
    """The real stage, with the stand-in metadata step leaving no metadata.json to take the title from."""
    calls = []
    work = run_main(monkeypatch, calls)
    assert calls == ["metadata", "qa"]
    assert "thumbnail: skipped (FileNotFoundError" in capsys.readouterr().out
    assert "- **Outcome:** finished" in (work / "run_report.md").read_text(encoding="utf-8")


def test_a_finished_run_writes_the_thumbnail_next_to_the_metadata_and_a_rerun_buys_nothing(
        monkeypatch, tmp_path, media, small):
    """pipeline.main() on a real small film (as in test_run_report.py), with the real metadata and
    thumbnail steps and the image model faked."""
    film = make_film(monkeypatch, tmp_path, media)
    monkeypatch.setattr(pipeline.metadata, "write_metadata", REAL_WRITE_METADATA)
    monkeypatch.setattr(pipeline.thumbnail, "make", REAL_MAKE)

    film.run()

    out = film.work / "thumbnail.jpg"
    assert Image.open(out).format == "JPEG" and Image.open(out).size == SIZE and out.stat().st_size < 2_000_000
    meta = load_json(film.work / "metadata.json")
    assert meta["title"] == "Test film" and meta["thumbnail"] == "thumbnail.jpg"
    picture = image_agent.cache_path(thumbnail.picture_scene(film.script["scenes"], film.work / "visuals"),
                                     film.work / "visuals")
    assert picture.exists(), "the first still's description, asked for once and kept with the other pictures"
    bought = spent()
    last = bought[-1]
    assert (last["provider"], last["units"], last["film"]) == ("google", {"images": 1}, "topic"), \
        "the picture is the run's last purchase, on the film's account"
    first = out.read_bytes()
    assert "- **Thumbnail:** the image model's picture with the title drawn on it (`thumbnail.jpg`)" in \
        (film.work / "run_report.md").read_text(encoding="utf-8")

    film.run()
    assert spent() == bought and out.read_bytes() == first
