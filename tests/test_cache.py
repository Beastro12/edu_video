"""P0-1: cached files are keyed by the inputs that produced them, not by scene number.

ElevenLabs and Imagen are faked at the HTTP/SDK boundary, so the agents' own cache
logic runs for real and every paid call is counted. FFmpeg runs for real at low
resolution to keep the test fast."""
import hashlib
from types import SimpleNamespace

import pytest
from conftest import ff

import assembly
import config
import pipeline
from agents import manim_agent, music, voice
from utils import duration, load_json

PAD = config.LEAD_IN_S + config.TAIL_S


def speech_s(text: str) -> float:
    """Fake narration length: unique per text length, so a stale mp3 shows up as a wrong duration."""
    return round(0.6 + len(text) / 50, 2)


@pytest.fixture
def film(tmp_path, monkeypatch):
    paid = {"tts": [], "image": []}
    made = {}  # prompt -> image bytes the fake Imagen returned for it
    spoken = {}  # text -> mp3 bytes the fake ElevenLabs returned for it
    renders = []  # output paths of every FFmpeg render in assembly and music

    def fake_post(url, headers, json, timeout):
        text = json["text"]
        paid["tts"].append(text)
        out = tmp_path / f"tts_{len(paid['tts'])}.mp3"
        pitch = 200 + int(hashlib.sha256(text.encode()).hexdigest()[:4], 16) % 400  # same length, other text: other audio
        ff("-f", "lavfi", "-i", f"sine=f={pitch}:d={speech_s(text)}:sample_rate=44100", str(out))
        spoken[text] = out.read_bytes()
        return SimpleNamespace(status_code=200, content=spoken[text], text="")

    class FakeModels:
        def generate_images(self, model, prompt, config):
            paid["image"].append(prompt)
            colour = hashlib.sha256(prompt.encode()).hexdigest()[:6]
            out = tmp_path / f"img_{len(paid['image'])}.png"
            ff("-f", "lavfi", "-i", f"color=c=0x{colour}:s=64x36", "-frames:v", "1", str(out))
            made[prompt] = out.read_bytes()
            image = SimpleNamespace(image_bytes=made[prompt])
            return SimpleNamespace(generated_images=[SimpleNamespace(image=image)])

    class FakeClient:
        def __init__(self, api_key):
            self.models = FakeModels()

    def spy(real):
        def wrapped(cmd, cwd=None):
            renders.append(cmd[-1])
            return real(cmd, cwd)
        return wrapped

    monkeypatch.setattr(voice.requests, "post", fake_post)
    monkeypatch.setattr("google.genai.Client", FakeClient)
    monkeypatch.setattr(config, "GOOGLE_API_KEY", "offline-test")
    monkeypatch.setattr(manim_agent, "text", lambda *a, **k: pytest.fail("unexpected Claude call"))
    monkeypatch.setattr(config, "MUSIC_DIR", str(tmp_path / "no_music"))
    monkeypatch.setattr(assembly, "W", 320)
    monkeypatch.setattr(assembly, "H", 180)
    monkeypatch.setattr(assembly, "run", spy(assembly.run))
    monkeypatch.setattr(music, "run", spy(music.run))

    work = tmp_path / "work"
    work.mkdir()

    def render(narrations, descriptions=None):
        descriptions = descriptions or [f"image for: {text}" for text in narrations]
        script = {"title": "Test film", "scenes": [
            {"id": i, "chapter": 1, "concept": f"c{i}", "narration": text,
             "visual_type": "still", "visual_description": desc}
            for i, (text, desc) in enumerate(zip(narrations, descriptions, strict=True), start=1)]}
        paid["tts"].clear()
        paid["image"].clear()
        renders.clear()
        final = pipeline.render_film(script, work, allow_veo=False, seed="seed")
        return final, script

    def rebuilt(pred):
        return [r for r in renders if pred(str(r))]

    def check_every_scene_uses_its_own_files(script):
        manifest = load_json(work / "manifest.json")["scenes"]
        assert [m["id"] for m in manifest] == [s["id"] for s in script["scenes"]]
        for m, s in zip(manifest, script["scenes"], strict=True):
            assert (work / m["audio"]).read_bytes() == spoken[s["narration"]]
            assert duration(work / m["clip"]) == pytest.approx(speech_s(s["narration"]) + PAD, abs=0.08)
            prompt = [p for p in made if p.startswith(s["visual_description"])][0]
            assert (work / m["visual"]).read_bytes() == made[prompt]

    return SimpleNamespace(render=render, paid=paid, rebuilt=rebuilt, work=work,
                           check=check_every_scene_uses_its_own_files)


A = "Stars form slowly."                                      # 18 chars
B = "Gas clouds collapse under their own gentle weight."       # 50 chars
C = "Light from far away arrives very late, long after it left."  # 59 chars


def test_unchanged_rerun_rebuilds_nothing(film):
    film.render([A, B, C])
    film.render([A, B, C])
    assert film.paid == {"tts": [], "image": []}
    assert film.rebuilt(lambda r: True) == []


def test_editing_one_narration_regenerates_only_that_scene_and_the_assembly(film):
    film.render([A, B, C])
    edited = "Gas clouds collapse."  # 20 chars: a different length, so a stale mp3 would be caught
    final, script = film.render([A, edited, C], [f"image for: {t}" for t in (A, B, C)])

    assert film.paid["tts"] == [edited]
    assert film.paid["image"] == [], "the still's inputs did not change, so it must not be re-bought"
    assert len(film.rebuilt(lambda r: "/clips/" in r)) == 1
    assert film.rebuilt(lambda r: "narrated" in r), "narrated.mp4 must be rebuilt when a clip changes"
    assert film.rebuilt(lambda r: r.endswith(".mp4") and "test-film" in r), "final video must be rebuilt"
    film.check(script)
    audio = [film.work / m["audio"] for m in load_json(film.work / "manifest.json")["scenes"]]
    expected = sum(duration(a) + PAD for a in audio) - 2 * config.XFADE_S
    assert duration(final) == pytest.approx(expected, abs=0.1)


def test_same_length_narration_edit_is_not_mistaken_for_the_old_one(film):
    film.render([A, B, C])
    edited = "Stars grow slowly."  # same length as A: only the content hash can tell them apart
    assert len(edited) == len(A)
    _, script = film.render([edited, B, C], [f"image for: {t}" for t in (A, B, C)])

    assert film.paid["tts"] == [edited]
    assert len(film.rebuilt(lambda r: "/clips/" in r)) == 1
    film.check(script)


def test_inserting_a_scene_does_not_shift_later_scenes_onto_wrong_files(film):
    film.render([A, B, C])
    new = "A new opening line, calm and slow, to begin."
    _, script = film.render([new, A, B, C])  # every old scene's id moves up by one

    assert film.paid["tts"] == [new], "only the new scene is paid for"
    assert len(film.paid["image"]) == 1
    film.check(script)


def test_manim_cache_ignores_scene_id_but_not_inputs(tmp_path, monkeypatch, media):
    briefs = []

    def fake_text(system, messages):
        briefs.append(messages[0]["content"])
        return "```python\nfrom manim import *\nclass X(Scene):\n    pass\n```"

    monkeypatch.setattr(manim_agent, "text", fake_text)
    monkeypatch.setattr(manim_agent, "_render", lambda py, cls, media_dir: media / "manim.mp4")
    scene = {"id": 3, "concept": "orbits", "narration": "The moon falls around us.",
             "visual_type": "manim", "visual_description": "a small circle orbits a larger one"}

    first = manim_agent.render_scene(scene, 5.0, tmp_path)
    assert manim_agent.render_scene({**scene, "id": 9}, 5.0, tmp_path) == first
    assert len(briefs) == 1, "same inputs under a new scene id must reuse the render"
    assert manim_agent.render_scene(scene, 7.0, tmp_path) != first
    assert manim_agent.render_scene({**scene, "narration": "Softly."}, 5.0, tmp_path) != first
    assert len(briefs) == 3
