"""P3-2: visual style presets (`--style calm|flat`). Only the pictures' look changes.

Under `calm` (the default) every prompt and cache path is what it was before presets existed, so
no paid still, clip or Manim render is bought again (D9): the old strings are written out here and
the paths are rebuilt from them. `flat` has its own prompts and therefore its own cache keys.
A build records its style, so a rerun can't quietly switch and buy every picture again.
Providers are faked at the HTTP boundary or by stand-ins, and nothing leaves the machine."""
import base64
import functools
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from conftest import fake_ffmpeg, ff, medium_cue_problems, photo_cue_problems

import assembly
import config
import ledger
import pipeline
import qa
from agents import ai_video, google_client, image_agent, manim_agent, still_critic, voice
from utils import content_key, load_json

REAL_GET_SCRIPT = pipeline.get_script

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DESCRIPTION = "a dark sky"
SCENE = {"id": 1, "concept": "orbits", "narration": "The moon falls around us, slowly.",
         "visual_type": "still", "visual_description": DESCRIPTION}
TARGET_S = 10.0

# What the prompts were before presets existed; every file bought so far is keyed by these.
OLD_STILL = (" Calm, dark, low-contrast, cinematic, deep blues and soft warm highlights, "
             "lots of negative space, soft focus edges. No text, no letters, no watermark, "
             "no people's faces.")
OLD_AI_VIDEO = (" Calm documentary footage, one continuous slow camera move, soft natural "
                "light, muted colours, shallow depth of field. No text, no captions, no logos, "
                "no people's faces.")
OLD_MANIM_STYLE = ('- Style: background "#0f1419". Soft palette: "#7fb3d5", "#a9dfbf", "#f5cba7",\n'
                   '  "#d7bde2", "#fdfefe". No flashing, no bright full-screen colour.')


def old_manim_system(has_latex: bool) -> str:
    """manim_agent.SYSTEM as it was before presets."""
    return f"""You write Manim Community Edition (v0.18+) code for calm science explainer videos.

Rules:
- Output ONE python code block. `from manim import *` then exactly one class named
  as instructed, subclassing Scene (or MovingCameraScene if you need camera moves).
- No external files, images, sounds, or network. Built-in shapes and Text only.
- {"MathTex/Tex are allowed." if has_latex else "LaTeX is NOT installed: never use MathTex, Tex, or Integer/DecimalNumber with LaTeX. Use Text for all labels and formulas."}
- Style: background "#0f1419". Soft palette: "#7fb3d5", "#a9dfbf", "#f5cba7",
  "#d7bde2", "#fdfefe". No flashing, no bright full-screen colour.
- This is a calm, sleep-friendly documentary: slow, gentle motion (run_time >= 2),
  at most 5 words of text on screen at once, font_size >= 32, nothing sudden.
- Timing: the sum of all run_time and wait() calls must be close to the target
  duration you are given. End with self.wait() of at least 1 second on a clean final frame.
- The visual must be scientifically correct. If a detail is uncertain, keep it schematic."""


# The flat preset as specified, split where the spec breaks its lines (not where config.py does).
FLAT_STILL = (" Flat vector illustration: clean geometric shapes with rounded corners, smooth flat colour "
              "fields with soft gradients and a gentle rim light, a few small soft stars or specks of light. "
              "Deep navy and indigo background with warm amber, coral and teal accents. Calm and uncluttered, "
              "generous negative space, low contrast, no harsh edges. No text, no letters, no watermark, no "
              "faces, no characters.")
FLAT_AI_VIDEO = (" Smooth 2D flat vector animation: clean geometric shapes with soft gradients, one continuous "
                 "slow camera move, deep navy and indigo with warm amber, coral and teal accents, calm and "
                 "uncluttered. No text, no captions, no logos, no faces, no characters.")
FLAT_MANIM_STYLE = (
    '- Style: background "#162044" (deep navy). Flat vector look: filled shapes (fill_opacity 1,\n'
    '  thin or no stroke), rounded forms (Circle, RoundedRectangle), soft glows made by stacking 3-4\n'
    '  concentric circles of falling opacity, a few small dim dots as distant stars.\n'
    '  Palette: "#f4a261" amber, "#e76f51" coral, "#2a9d8f" teal, "#8ecae6" sky, "#e9c46a" gold,\n'
    '  "#f1faee" off-white for text. No flashing, no bright full-screen colour.')

OLD = {"still": OLD_STILL, "ai_video": OLD_AI_VIDEO}
FLAT = {"still": FLAT_STILL, "ai_video": FLAT_AI_VIDEO}


@pytest.fixture
def style(monkeypatch):
    def use(name):
        monkeypatch.setattr(config, "STYLE", name)  # undone after the test, wherever main() set it
    return use


def paths(out_dir, atmospheric=False) -> dict:
    """The four cache paths a scene gets, as the agents now make them."""
    return {"still": image_agent.cache_path(SCENE, out_dir),
            "review": image_agent.review_path(SCENE, out_dir),
            "ai": ai_video.cache_path(SCENE, out_dir),
            "manim": manim_agent.cache_path(SCENE, TARGET_S, out_dir, atmospheric)}


def old_paths(out_dir, has_latex, atmospheric=False) -> dict:
    """The same four paths, rebuilt from the old literal strings."""
    d = DESCRIPTION
    return {
        "still": out_dir / f"still_{content_key('still', d + OLD_STILL, config.IMAGE_MODEL, config.ASPECT_RATIO, config.IMAGE_SIZE)}.png",
        "review": out_dir / ("review_" + content_key(
            "review", d + OLD_STILL, SCENE["concept"], config.IMAGE_MODEL, config.ASPECT_RATIO, config.IMAGE_SIZE,
            config.CLAUDE_MODEL, still_critic.SYSTEM, still_critic.PROMPT, still_critic.SCHEMA,
            config.STILL_REVIEW_RETRIES, config.PREVIEW_PX) + ".json"),
        "ai": out_dir / ("ai_" + content_key("ai", d + OLD_AI_VIDEO, config.VEO_MODEL, config.ASPECT_RATIO,
                                             config.VEO_RESOLUTION) + ".mp4"),
        "manim": out_dir / ("manim_" + content_key(
            "manim", old_manim_system(has_latex), manim_agent._brief(SCENE, TARGET_S, atmospheric),
            config.CLAUDE_MODEL, "-qh", config.FPS) + ".mp4"),
    }


# --- the providers, faked at the HTTP boundary ----------------------------------------------

def sent_still_prompt(monkeypatch, out_dir, media) -> str:
    """The text the image model is sent for SCENE, through the real SDK on a mock transport."""
    sent = []
    png = base64.b64encode((media / "still.png").read_bytes()).decode()
    answer = {"candidates": [{"content": {"role": "model", "parts": [
        {"inlineData": {"mimeType": "image/png", "data": png}}]}, "finishReason": "STOP"}]}

    def handler(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, content=json.dumps(answer))

    through_mock(monkeypatch, handler)
    image_agent.render_still(SCENE, out_dir)
    [body] = sent
    return body["contents"][0]["parts"][0]["text"]


def sent_veo_prompt(monkeypatch, out_dir) -> str:
    """The prompt Veo is sent; the mock refuses the job, so nothing is bought or waited for."""
    sent = []

    def handler(request):
        sent.append(json.loads(request.content))
        return httpx.Response(400, content=json.dumps({"error": {"code": 400, "message": "seen; stop"}}))

    through_mock(monkeypatch, handler)
    with pytest.raises(Exception, match="400"):
        ai_video.render_scene(SCENE, out_dir)
    [body] = sent
    return body["instances"][0]["prompt"]


def through_mock(monkeypatch, handler):
    monkeypatch.setattr(config, "GOOGLE_API_KEY", "offline-test")
    mock = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(google_client, "make", functools.partial(google_client.make, httpx_client=mock))


# --- calm: nothing changes ------------------------------------------------------------------

def test_calm_strings_are_the_old_ones():
    calm = config.STYLES["calm"]
    assert calm["still"] == OLD_STILL
    assert calm["ai_video"] == OLD_AI_VIDEO
    assert calm["manim"] == OLD_MANIM_STYLE
    assert calm["manim_background"] == "#0f1419"


@pytest.mark.parametrize("has_latex", [True, False])
def test_calm_manim_system_prompt_is_byte_identical(style, monkeypatch, has_latex):
    style("calm")
    monkeypatch.setattr(manim_agent, "HAS_LATEX", has_latex)
    assert manim_agent.system() == old_manim_system(has_latex)


@pytest.mark.parametrize("has_latex", [True, False])
@pytest.mark.parametrize("atmospheric", [False, True])
def test_calm_cache_paths_are_the_ones_every_paid_file_was_bought_under(
        style, monkeypatch, tmp_path, has_latex, atmospheric):
    style("calm")
    monkeypatch.setattr(manim_agent, "HAS_LATEX", has_latex)
    assert paths(tmp_path, atmospheric) == old_paths(tmp_path, has_latex, atmospheric)


def test_calm_image_and_veo_requests_carry_the_old_prompts(style, real_google, monkeypatch, tmp_path, media):
    style("calm")
    assert sent_still_prompt(monkeypatch, tmp_path / "s", media) == DESCRIPTION + OLD_STILL
    assert sent_veo_prompt(monkeypatch, tmp_path / "v") == DESCRIPTION + OLD_AI_VIDEO


# --- flat: its own look, its own cache keys -----------------------------------------------

def test_flat_strings_are_the_specified_ones():
    flat = config.STYLES["flat"]
    assert flat["still"] == FLAT_STILL
    assert flat["ai_video"] == FLAT_AI_VIDEO
    assert flat["manim"] == FLAT_MANIM_STYLE
    assert flat["manim_background"] == "#162044"
    assert "\n" not in flat["still"] + flat["ai_video"], "a prompt suffix is one line"


def test_flat_prompts_end_with_the_flat_suffixes(style):
    style("flat")
    assert image_agent.style() == FLAT_STILL
    assert ai_video.style() == FLAT_AI_VIDEO
    style("calm")
    assert image_agent.style() == OLD_STILL, "read at call time: changing the style changes the suffix"
    assert ai_video.style() == OLD_AI_VIDEO


@pytest.mark.parametrize("has_latex", [True, False])
def test_flat_manim_system_prompt_swaps_only_the_style_lines(style, monkeypatch, has_latex):
    monkeypatch.setattr(manim_agent, "HAS_LATEX", has_latex)
    style("flat")
    system = manim_agent.system()
    assert system == old_manim_system(has_latex).replace(OLD_MANIM_STYLE, FLAT_MANIM_STYLE)
    assert '"#162044"' in system
    for colour in ("#f4a261", "#e76f51", "#2a9d8f", "#8ecae6", "#e9c46a", "#f1faee"):
        assert f'"{colour}"' in system
    for colour in ("#0f1419", "#7fb3d5", "#a9dfbf", "#f5cba7", "#d7bde2", "#fdfefe"):
        assert colour not in system, "no calm colour is left to pull the picture back"


@pytest.mark.parametrize("atmospheric", [False, True])
def test_a_style_change_never_reuses_a_calm_file(style, tmp_path, atmospheric):
    style("calm")
    calm = paths(tmp_path, atmospheric)
    style("flat")
    flat = paths(tmp_path, atmospheric)
    for kind in calm:
        assert flat[kind] != calm[kind], f"the {kind} path ignores the style"
    style("calm")
    assert paths(tmp_path, atmospheric) == calm, "and going back finds the calm files again"


def test_flat_image_and_veo_requests_carry_the_flat_prompts(style, real_google, monkeypatch, tmp_path, media):
    style("flat")
    assert sent_still_prompt(monkeypatch, tmp_path / "s", media) == DESCRIPTION + FLAT_STILL
    assert sent_veo_prompt(monkeypatch, tmp_path / "v") == DESCRIPTION + FLAT_AI_VIDEO


@pytest.mark.parametrize("name", sorted(config.STYLES))
def test_every_manim_request_gets_the_active_style(style, monkeypatch, tmp_path, name):
    """The write and the retime request both go out with system(), so a retimed render keeps its look."""
    style(name)
    systems = []

    def fake_text(system, messages):
        systems.append(system)
        return "```python\nfrom manim import *\nclass DocScene(Scene):\n    pass\n```"

    def fake_render(py_file, class_name, media_dir):
        media_dir.mkdir(parents=True, exist_ok=True)
        out = media_dir / f"{py_file.stem}.mp4"
        ff("-f", "lavfi", "-i", "testsrc2=s=160x90:r=30:d=4", "-pix_fmt", "yuv420p", str(out))  # short: retimed
        return out

    monkeypatch.setattr(manim_agent, "text", fake_text)
    monkeypatch.setattr(manim_agent, "_render", fake_render)
    manim_agent.render_scene(SCENE, TARGET_S, tmp_path)
    assert len(systems) == 2, "one request to write, one to retime"
    assert systems == [manim_agent.system()] * 2
    assert config.STYLES[name]["manim"] in systems[0]


# --- the presets themselves ------------------------------------------------------------------

def luma(hex_colour: str) -> float:
    """BT.709 luma of an sRGB hex colour, 0-255."""
    r, g, b = (int(hex_colour[i:i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def test_every_preset_has_the_same_keys_and_names_its_background_in_its_manim_lines():
    for name, preset in config.STYLES.items():
        assert set(preset) == {"still", "ai_video", "manim", "manim_background"}, name
        assert preset["still"].startswith(" ") and preset["ai_video"].startswith(" "), \
            f"{name}: a suffix is appended to a description, so it starts with a space"
        assert f'background "{preset["manim_background"]}"' in preset["manim"], name


def test_no_preset_is_darker_than_calm_and_none_is_flagged_black_by_qa(tmp_path):
    calm = luma(config.STYLES["calm"]["manim_background"])
    for name, preset in config.STYLES.items():
        assert luma(preset["manim_background"]) >= calm, f"{name} is darker than calm"

    def black_in(background: str) -> list:
        clip = tmp_path / f"{background.lstrip('#')}.mp4"
        ff("-f", "lavfi", "-i", f"color=c=0x{background.lstrip('#')}:s=160x90:r=30:d=2", "-pix_fmt", "yuv420p", str(clip))
        return qa.black_segments(clip)

    for name, preset in config.STYLES.items():
        assert black_in(preset["manim_background"]) == [], f"QA would flag {name}'s background as black"
    assert black_in("#050505") != [], "the check can fail: near-black is flagged"


def test_the_flat_background_is_brighter_than_calm_and_the_spec_numbers_hold():
    assert luma("#0f1419") == pytest.approx(19.3, abs=0.05)
    assert luma("#162044") == pytest.approx(32.5, abs=0.05)
    assert luma(config.STYLES["flat"]["manim_background"]) > luma(config.STYLES["calm"]["manim_background"])
    assert luma(config.STYLES["flat"]["manim_background"]) / 255 > config.QA_BLACK_PIX_TH


def test_no_preset_text_names_a_studio(style, monkeypatch):
    """Only a style's traits go into a prompt (never a studio's name, characters or mascots)."""
    def strings(value):
        return [value] if isinstance(value, str) else [s for v in value.values() for s in strings(v)]

    seen = strings(config.STYLES)
    assert len(seen) == 4 * len(config.STYLES), "four strings per preset"
    for name in config.STYLES:
        style(name)
        seen += [image_agent.style(), ai_video.style(), manim_agent.system()]
    assert not [s for s in seen if "kurzgesagt" in s.lower()]


# --- the command line and the build's record ---------------------------------------------------

def run_cli(monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["pipeline.py", "topic", *argv])
    pipeline.main()


def spy_on_estimates(monkeypatch):
    """Every estimate_cost result, with the style in force when it was made."""
    seen = []
    real = pipeline.estimate_cost

    def spy(*args, **kwargs):
        seen.append((config.STYLE, real(*args, **kwargs)))
        return seen[-1][1]

    monkeypatch.setattr(pipeline, "estimate_cost", spy)
    return seen


def decided_build(tmp_path, decided=(2,), record=None):
    """A script of two stills; those in `decided` are already made and accepted, under the style
    in force now. `record` stamps the build with a style."""
    work = tmp_path / "build" / "topic"
    (work / "visuals").mkdir(parents=True)
    scenes = [{"id": i, "concept": "c", "narration": "x" * 100, "visual_type": "still", "visual_description": f"s{i}"}
              for i in (1, 2)]
    (work / "script.json").write_text(json.dumps({"title": "T", "scenes": scenes}))
    (work / "visuals" / "accepted.png").write_bytes(b"png")
    for i in decided:
        image_agent.review_path(scenes[i - 1], work / "visuals").write_text(
            '{"accepted": "accepted.png", "done": true, "attempts": []}')
    if record:
        (work / config.STYLE_FILE).write_text(record)
    return work


def test_style_flat_sets_the_style_before_the_estimate(style, monkeypatch, tmp_path):
    style("calm")
    monkeypatch.setattr(config, "GOOGLE_API_KEY", "offline-test")
    decided_build(tmp_path)
    seen = spy_on_estimates(monkeypatch)

    run_cli(monkeypatch, "--estimate")
    calm = [est for name, est in seen if name == "calm"]
    assert [name for name, _ in seen] == ["calm", "calm"], "typical and worst case"
    assert calm[0]["images"] > 0, "scene 1 has no still yet"

    seen.clear()
    run_cli(monkeypatch, "--style", "flat", "--estimate")
    assert config.STYLE == "flat"
    assert [name for name, _ in seen] == ["flat", "flat"], "the estimate saw the style"
    # scene 2's calm still and decision are not the flat ones: both scenes are still to buy
    assert seen[0][1]["images"] == pytest.approx(2 * calm[0]["images"])


def test_without_the_flag_or_a_record_the_style_is_the_default(style, monkeypatch, capsys):
    style("flat")  # as if VISUAL_STYLE=flat were in the environment
    seen = spy_on_estimates(monkeypatch)
    run_cli(monkeypatch, "--estimate")
    assert [name for name, _ in seen] == ["flat", "flat"]
    assert config.STYLE == "flat"
    assert "• style: flat (default)" in capsys.readouterr().out


def test_an_unknown_style_is_a_usage_error_that_lists_the_choices(style, monkeypatch, capsys):
    style("calm")
    with pytest.raises(SystemExit) as exit_:
        run_cli(monkeypatch, "--style", "sepia", "--estimate")
    assert exit_.value.code == 2
    err = capsys.readouterr().err
    assert "invalid choice" in err and "'sepia'" in err
    assert all(f"'{name}'" in err for name in config.STYLES)
    assert config.STYLE == "calm"


@pytest.mark.parametrize("flag, recorded, default, expected", [
    ("flat", "calm", "calm", ("flat", "--style")),
    ("calm", None, "flat", ("calm", "--style")),
    ("calm", "calm", "flat", ("calm", "--style")),
    (None, "flat", "calm", ("flat", "recorded in the build")),
    (None, "calm", "flat", ("calm", "recorded in the build")),  # the environment's default never overrides a record
    (None, None, "flat", ("flat", "default")),
    (None, None, "calm", ("calm", "default")),
])
def test_the_flag_beats_the_record_which_beats_the_default(flag, recorded, default, expected):
    assert pipeline.pick_style(flag, recorded, default) == expected


def test_a_record_that_names_no_style_is_a_usage_error_naming_the_file(style, monkeypatch, tmp_path, capsys):
    style("calm")
    work = decided_build(tmp_path)
    (work / config.STYLE_FILE).write_text("sepia\n")
    with pytest.raises(SystemExit) as exit_:
        run_cli(monkeypatch, "--estimate")
    assert exit_.value.code == 2
    err = capsys.readouterr().err
    assert str(work / config.STYLE_FILE) in err and "'sepia'" in err
    assert all(name in err for name in config.STYLES)


def test_the_estimate_shows_the_style_and_prices_the_one_it_would_use(style, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(config, "GOOGLE_API_KEY", "offline-test")
    style("flat")
    work = decided_build(tmp_path, decided=(1, 2), record="flat")  # both pictures made, in flat
    seen = spy_on_estimates(monkeypatch)

    style("calm")  # a new process: the default
    run_cli(monkeypatch, "--estimate")
    out = capsys.readouterr().out
    assert "• style: flat (recorded in the build)" in out
    assert "in the flat style" in out, "print_estimate says which style it priced"
    assert "recorded as" not in out
    assert [name for name, _ in seen] == ["flat", "flat"]
    assert seen[0][1]["images"] == 0, "every picture is already made in flat"

    seen.clear()
    style("calm")
    run_cli(monkeypatch, "--style", "calm", "--estimate")
    out = capsys.readouterr().out
    assert "• style: calm (--style)" in out
    assert "this build is recorded as flat; this run makes its pictures in calm instead" in out
    assert "in the calm style" in out
    assert [name for name, _ in seen] == ["calm", "calm"]
    assert seen[0][1]["images"] == pytest.approx(2 * ledger.image_eur()), "the calm pictures are all to buy"
    assert (work / config.STYLE_FILE).read_text() == "flat", "an estimate records nothing"


def test_naming_the_recorded_style_is_not_a_switch(style, monkeypatch, tmp_path, capsys):
    style("calm")
    decided_build(tmp_path, record="flat")
    run_cli(monkeypatch, "--style", "flat", "--estimate")
    out = capsys.readouterr().out
    assert "• style: flat (--style)" in out and "recorded as" not in out


# --- a whole run: the build remembers its style ----------------------------------------------

@pytest.fixture
def film(monkeypatch, tmp_path, media, small, stills_approved):
    """pipeline.main() end to end on a film of two stills, with the providers faked and the
    image requests counted. Each run() is a new process: it starts from the default style."""
    asked = []
    still = (media / "still.png").read_bytes()
    narration = (media / "n_short.mp3").read_bytes()

    class Models:
        def generate_content(self, model, contents, config):
            asked.append(contents)
            part = SimpleNamespace(inline_data=SimpleNamespace(data=still, mime_type="image/png"), thought=None)
            return SimpleNamespace(parts=[part])

    script = {"title": "Test film", "scenes": [
        {"id": i, "chapter": 1, "concept": f"c{i}", "narration": f"Line {i}.", "visual_type": "still",
         "visual_description": f"picture {i}"} for i in (1, 2)]}
    monkeypatch.setattr(config, "GOOGLE_API_KEY", "offline-test")
    monkeypatch.setattr(config, "BUDGET_EUR", 100.0)
    monkeypatch.setattr(config, "MUSIC_DIR", str(tmp_path / "no_music"))
    monkeypatch.setattr(google_client, "make", lambda: SimpleNamespace(models=Models()))
    monkeypatch.setattr(voice.requests, "post", lambda url, headers, json, timeout: SimpleNamespace(
        status_code=200, content=narration, text="", headers={}))
    monkeypatch.setattr(pipeline, "get_script", lambda *a: script)
    monkeypatch.setattr(pipeline.metadata, "write_metadata", lambda work: None)
    monkeypatch.setattr(qa, "run_qa", lambda path: {"passed": True})
    monkeypatch.setattr(qa, "print_report", lambda report: None)

    def run(*argv, default="calm"):
        monkeypatch.setattr(config, "STYLE", default)
        run_cli(monkeypatch, "--no-ai-video", *argv)

    return SimpleNamespace(run=run, asked=asked, work=Path(config.BUILD_DIR) / "topic")


def test_a_rerun_without_the_flag_keeps_the_recorded_style_and_buys_no_picture(film, capsys):
    film.run("--style", "flat")
    assert len(film.asked) == 2 and all(prompt.endswith(FLAT_STILL) for prompt in film.asked)
    assert (film.work / config.STYLE_FILE).read_text() == "flat"
    assert "• style: flat (--style)" in capsys.readouterr().out
    made = sorted(p.name for p in (film.work / "visuals").iterdir())
    film.asked.clear()

    film.run()  # the default is calm again; the build says flat
    out = capsys.readouterr().out
    assert "• style: flat (recorded in the build)" in out and "recorded as" not in out
    assert config.STYLE == "flat"
    assert film.asked == [], "the flat pictures were found, not bought again"
    assert sorted(p.name for p in (film.work / "visuals").iterdir()) == made, "and no other file was made"


def test_an_explicit_other_style_says_so_before_buying_and_is_used(film, capsys):
    film.run("--style", "flat")
    film.asked.clear()
    capsys.readouterr()

    film.run("--style", "calm")
    out = capsys.readouterr().out
    assert "• style: calm (--style)" in out
    assert "this build is recorded as flat; this run makes its pictures in calm instead" in out
    assert out.index("recorded as flat") < out.index("• narration"), "said before any spending"
    assert len(film.asked) == 2 and all(prompt.endswith(OLD_STILL) for prompt in film.asked)
    assert (film.work / config.STYLE_FILE).read_text() == "calm"

    film.asked.clear()
    film.run()  # the switch stuck, and the new pictures are cached
    assert "• style: calm (recorded in the build)" in capsys.readouterr().out
    assert film.asked == []
    film.run("--style", "flat")  # and the flat ones were kept: going back buys nothing either
    assert film.asked == []


def test_manifest_json_records_the_style(film):
    film.run()
    assert load_json(film.work / "manifest.json")["style"] == "calm"
    film.run("--style", "flat")
    manifest = load_json(film.work / "manifest.json")
    assert manifest["style"] == "flat"
    assert len(manifest["scenes"]) == 2, "the scenes are still there for metadata and QA"


def test_the_style_is_recorded_before_any_picture_is_made(film, monkeypatch, capsys):
    real = pipeline.make_visual
    crash = [True]

    def make_visual(*args, **kwargs):
        if crash:
            raise RuntimeError("the visual stage went down")
        return real(*args, **kwargs)

    monkeypatch.setattr(pipeline, "make_visual", make_visual)
    with pytest.raises(RuntimeError, match="went down"):
        film.run("--style", "flat")
    assert (film.work / config.STYLE_FILE).read_text() == "flat", "the crash left the record"
    assert not (film.work / "manifest.json").exists(), "nothing was assembled"
    assert film.asked == []

    crash.clear()
    capsys.readouterr()
    film.run()  # picks up where it stopped, in the style it was making
    assert "• style: flat (recorded in the build)" in capsys.readouterr().out
    assert len(film.asked) == 2 and all(prompt.endswith(FLAT_STILL) for prompt in film.asked)


def test_a_run_that_stops_in_the_script_stage_keeps_the_style_it_was_given(film, monkeypatch, capsys):
    def stops(*args):
        raise ledger.BudgetExceeded("out of budget in the outline call")

    monkeypatch.setattr(pipeline, "get_script", stops)
    with pytest.raises(ledger.BudgetExceeded):
        film.run("--style", "flat")
    assert (film.work / config.STYLE_FILE).read_text() == "flat", "recorded before the script stage"


def test_a_refused_run_records_no_style(film, monkeypatch):
    film.work.mkdir(parents=True)
    (film.work / "outline.json").write_text("{}")  # a script written without a brief...
    brief = film.work.parent / "other_brief.md"
    brief.write_text("# another brief\n")
    monkeypatch.setattr(pipeline, "get_script", REAL_GET_SCRIPT)  # the film fixture fakes it
    with pytest.raises(RuntimeError, match="without a brief; this run has one"):
        film.run("--style", "flat", "--brief", str(brief))  # ...and this run has one: refused
    assert not (film.work / config.STYLE_FILE).exists(), "a refused run changes nothing"


def test_a_script_only_run_records_the_style_too(film, capsys):
    film.run("--style", "flat", "--script-only")
    assert (film.work / config.STYLE_FILE).read_text() == "flat"
    assert film.asked == [] and not (film.work / "manifest.json").exists()
    capsys.readouterr()
    film.run()
    assert "• style: flat (recorded in the build)" in capsys.readouterr().out
    assert len(film.asked) == 2 and all(prompt.endswith(FLAT_STILL) for prompt in film.asked)


# --- the letterbox pad follows the style -------------------------------------------------------

def old_moving_filter(target, v_len, is_ai):
    """assembly._moving_filter as it was before presets, when the pad colour was a constant."""
    slow = min(target / v_len, config.MAX_AI_SLOWDOWN) if (is_ai and v_len < target) else 1.0
    return (f"setpts={slow:.4f}*PTS,"
            f"scale={assembly.W}:{assembly.H}:force_original_aspect_ratio=decrease,"
            f"pad={assembly.W}:{assembly.H}:(ow-iw)/2:(oh-ih)/2:color=0x0f1419,setsar=1,fps={config.FPS},"
            f"tpad=stop_mode=clone:stop_duration={target:.2f},format=yuv420p")


@pytest.fixture
def clip_name(monkeypatch, small, media, tmp_path):
    """The name a scene clip gets (a hash of its files and exact filters, D9), without encoding it."""
    monkeypatch.setattr(assembly, "run", fake_ffmpeg)  # answers P2-6's loudness reading, writes a stub clip
    made = iter(range(100))

    def name(kind):
        return assembly.build_scene_clip(media / "manim.mp4", media / "n_short.mp3",
                                         tmp_path / f"clips{next(made)}", kind, 1).name
    return name


@pytest.mark.parametrize("target, v_len, is_ai", [(5.0, 3.0, False), (5.0, 3.0, True), (5.0, 9.0, True)])
def test_calm_letterbox_filter_is_byte_identical(style, small, target, v_len, is_ai):
    style("calm")
    assert assembly.pad_colour() == "0x0f1419"
    assert assembly._moving_filter(target, v_len, is_ai) == old_moving_filter(target, v_len, is_ai)


@pytest.mark.parametrize("kind", ["manim", "ai"])
def test_calm_scene_clips_keep_the_names_they_were_cached_under(style, monkeypatch, clip_name, kind):
    style("calm")
    now = clip_name(kind)
    with monkeypatch.context() as before:
        before.setattr(assembly, "_moving_filter", old_moving_filter)
        assert clip_name(kind) == now


def test_flat_scene_clips_are_other_clips_with_the_flat_pad(style, clip_name, small):
    style("calm")
    calm = clip_name("manim")
    style("flat")
    assert "color=0x162044," in assembly._moving_filter(5.0, 3.0, False)
    assert "0x0f1419" not in assembly._moving_filter(5.0, 3.0, False)
    assert clip_name("manim") != calm, "a flat film never reuses a calm clip"


@pytest.mark.parametrize("name", sorted(config.STYLES))
def test_the_letterbox_bars_are_the_styles_background(style, small, media, tmp_path, name):
    """A real 4:3 picture in the 16:9 frame: the bar pixels are the preset's background."""
    style(name)
    src = tmp_path / "four_three.mp4"
    ff("-f", "lavfi", "-i", "color=c=white:s=160x120:r=30:d=2", "-pix_fmt", "yuv420p", str(src))
    clip = assembly.build_scene_clip(src, media / "n_short.mp3", tmp_path / "clips", "manim", 1)
    pixel = subprocess.run(["ffmpeg", "-v", "error", "-ss", "1", "-i", str(clip), "-vf", "crop=8:8:4:90",
                            "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                           capture_output=True, check=True).stdout[:3]  # the first pixel of an 8x8 patch
    hex_ = config.STYLES[name]["manim_background"]
    want = tuple(int(hex_[i:i + 2], 16) for i in (1, 3, 5))
    assert len(pixel) == 3 and all(abs(a - b) <= 3 for a, b in zip(pixel, want, strict=True)), (tuple(pixel), want)


# --- the environment ----------------------------------------------------------------------------

def import_config(**env):
    """Import config in a fresh interpreter. dotenv is disabled: a VISUAL_STYLE in Pietro's .env
    must not change what this test sees."""
    code = "import dotenv; dotenv.load_dotenv = lambda *a, **k: False; import config; print(config.STYLE)"
    clean = {k: v for k, v in os.environ.items() if k not in ("STYLE", "VISUAL_STYLE")}
    return subprocess.run([sys.executable, "-c", code], cwd=ROOT, env={**clean, **env}, capture_output=True, text=True)


def test_the_default_is_calm_and_the_environment_can_choose_flat():
    assert import_config().stdout.strip() == "calm"
    chosen = import_config(VISUAL_STYLE="flat")
    assert chosen.returncode == 0 and chosen.stdout.strip() == "flat"


def test_a_stray_style_variable_in_the_environment_is_ignored():
    """Why the variable is VISUAL_STYLE: a generic STYLE in a shell must not stop every import."""
    stray = import_config(STYLE="sepia")
    assert stray.returncode == 0 and stray.stdout.strip() == "calm"


def test_an_unknown_visual_style_in_the_environment_fails_at_import_with_the_choices_listed():
    refused = import_config(VISUAL_STYLE="sepia")
    assert refused.returncode != 0
    assert "AssertionError" in refused.stderr
    assert "VISUAL_STYLE must be one of" in refused.stderr and "'sepia'" in refused.stderr
    assert all(name in refused.stderr for name in config.STYLES), refused.stderr


def _a_cue_list_matches_whole_words_in_any_case_in_stills_and_clips_only(cues, problems):
    assert all(cue and cue == cue.lower().strip() for cue in cues)
    assert len(set(cues)) == len(cues)

    def caught(text, visual_type="still"):
        return problems([(1, 1, {"visual_type": visual_type, "visual_description": text})])

    for cue in cues:
        for text in (f"A calm {cue} here.", f"A calm {cue.upper()}.", f"{cue.title()}, then dark.",
                     f"(a {cue.replace(' ', chr(10))})", f"a {cue.replace(' ', '-')}-like shape"):
            assert caught(text), f"{cue!r} not caught in {text!r}"
            assert caught(text, "ai_video"), f"{cue!r} not caught in an ai_video description {text!r}"
            assert not caught(text, "manim"), "a manim description is exempt"
    return caught


def test_the_photographic_cue_list_has_the_required_words_and_matches_whole_words_in_any_case():
    """FLAT_PHOTO_CUES (P3-5): the words test_topics keeps out of still and ai_video descriptions."""
    required = {"blur", "blurred", "soft focus", "out of focus", "depth of field", "shallow depth", "bokeh",
                "mist", "haze", "fog", "dew", "photograph", "photographic", "cinematic", "lens"}
    # Whole-word matching misses every inflection, so each is listed (a reviewer's list of misses).
    inflections = {"focused", "unfocused", "defocused", "blurring", "blurs", "mists", "misted", "dewdrop",
                   "dewdrops", "photography", "photographed", "reflect", "reflecting", "sharper",
                   "sharpness", "foggy", "foggier", "hazy", "hazier", "misty"}
    strong = {"footage", "photorealistic", "hyperrealistic", "film grain", "grainy", "exposure",
              "long exposure", "aperture", "macro", "telephoto", "volumetric", "god rays"}
    assert required | inflections | strong <= set(config.FLAT_PHOTO_CUES)
    caught = _a_cue_list_matches_whole_words_in_any_case_in_stills_and_clips_only(
        config.FLAT_PHOTO_CUES, photo_cue_problems)
    for word in sorted(inflections | strong):
        assert caught(f"A quiet scene, {word}, in the dark."), word
    assert not caught("A mistake, a dewar, a blurb, a hazel tree, a sharpener, a photographer, lensing, a smist,"
                      " a macrophage, an unexposed edge. One continuous slow camera move; a glow; soft"
                      " gradients; a thin pale band; a flat calm sea.")


def test_the_style_medium_list_has_the_required_words_and_matches_whole_words_in_any_case():
    """STYLE_MEDIUM_CUES (P3-5): the medium belongs to the style suffix (D25), not to a description."""
    required = {"vector", "illustration", "illustrated", "drawn as", "flat shapes", "flat colour", "flat color",
                "cartoon", "painting", "painterly", "watercolour", "watercolor", "oil painting",
                "brushstroke", "brushstrokes"}
    assert required <= set(config.STYLE_MEDIUM_CUES)
    assert not set(config.STYLE_MEDIUM_CUES) & set(config.FLAT_PHOTO_CUES), "one word, one list"
    caught = _a_cue_list_matches_whole_words_in_any_case_in_stills_and_clips_only(
        config.STYLE_MEDIUM_CUES, medium_cue_problems)
    assert "medium words belong to the style suffix (D25)" in caught("A vector disc.")[0]
    assert not caught("Flat ground under a flat calm sea; a vectorless dark, a paintbrush-free night, drawn"
                      " in pale lines, a drawn curtain, an oil lamp, a flat disc on a flat table.")
    assert "an ai_video" in caught("A vector disc.", "ai_video")[0] and "a still" in caught("A vector disc.")[0]
