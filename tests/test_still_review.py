"""P1-2: every generated still is checked by Claude (vision) against its scene. A rejected
still is regenerated with the critique appended (at most STILL_REVIEW_RETRIES times), then the
scene falls back to Manim. The outcome is cached per scene and logged in the manifest."""
import hashlib
import json
from types import SimpleNamespace

import pytest
from conftest import ff

import config
import ledger
import pipeline
from agents import google_client, image_agent, llm, manim_agent

SCENE = {"id": 1, "concept": "Light bends near a star", "narration": "n", "visual_type": "still",
         "visual_description": "starlight curving gently around a dark sun"}


@pytest.fixture
def world(monkeypatch, tmp_path, media):
    """Fake image model (a distinct PNG per prompt) and a fake Claude that answers reviews
    from a script of verdicts. Records every paid call."""
    calls = {"image": [], "review": [], "manim": 0}
    verdicts = []

    class Models:
        def generate_content(self, model, contents, config):
            calls["image"].append(contents)
            out = tmp_path / f"img{len(calls['image'])}.png"
            colour = hashlib.sha256(contents.encode()).hexdigest()[:6]
            ff("-f", "lavfi", "-i", f"color=c=0x{colour}:s=64x36", "-frames:v", "1", str(out))
            part = SimpleNamespace(inline_data=SimpleNamespace(data=out.read_bytes(), mime_type="image/png"),
                                   thought=None)
            return SimpleNamespace(parts=[part])

    def create(**kwargs):
        calls["review"].append(kwargs)
        ok, problems = verdicts.pop(0)
        block = SimpleNamespace(type="tool_use", input={"ok": ok, "problems": problems})
        return SimpleNamespace(content=[block], usage=SimpleNamespace(input_tokens=1200, output_tokens=60))

    def manim(*args, **kwargs):
        calls["manim"] += 1
        return media / "manim.mp4"

    monkeypatch.setattr(config, "GOOGLE_API_KEY", "offline-test")
    monkeypatch.setattr(google_client, "make", lambda: SimpleNamespace(models=Models()))
    monkeypatch.setattr(llm._client.messages, "create", create)
    monkeypatch.setattr(manim_agent, "render_scene", manim)
    return SimpleNamespace(calls=calls, verdicts=verdicts, out=tmp_path / "visuals")


def visual(world, scene=SCENE):
    return pipeline.make_visual(scene, 5.0, world.out, allow_veo=False)


def test_an_approved_still_is_used(world):
    world.verdicts[:] = [(True, [])]
    path, kind = visual(world)
    assert kind == "still" and len(world.calls["image"]) == 1 and len(world.calls["review"]) == 1


def test_the_review_sees_the_image_and_the_scene(world):
    world.verdicts[:] = [(True, [])]
    visual(world)
    request = world.calls["review"][0]
    content = request["messages"][0]["content"]
    image = next(c for c in content if c["type"] == "image")
    assert image["source"]["media_type"] == "image/jpeg" and image["source"]["data"]
    text = " ".join(c["text"] for c in content if c["type"] == "text")
    assert SCENE["concept"] in text and SCENE["visual_description"] in text
    assert "text" in request["system"].lower() and "sleep" in request["system"].lower()


def test_a_rejected_still_is_regenerated_with_the_critique(world):
    world.verdicts[:] = [(False, ["letters in the sky"]), (True, [])]
    path, kind = visual(world)
    assert kind == "still"
    first, second = world.calls["image"]
    assert "letters in the sky" not in first and "letters in the sky" in second
    assert path != image_agent.cache_path(SCENE, world.out), "not the rejected first still"
    assert len(world.calls["review"]) == 2


def test_after_the_retries_the_scene_falls_back_to_manim(world):
    world.verdicts[:] = [(False, ["a face"])] * (1 + config.STILL_REVIEW_RETRIES)
    path, kind = visual(world)
    assert kind == "manim" and world.calls["manim"] == 1
    assert len(world.calls["image"]) == len(world.calls["review"]) == 1 + config.STILL_REVIEW_RETRIES


@pytest.mark.parametrize("verdicts, kind", [([(False, ["text"]), (True, [])], "still"),
                                            ([(False, ["text"])] * 3, "manim")])
def test_a_rerun_pays_for_nothing(world, verdicts, kind):
    world.verdicts[:] = list(verdicts)
    first = visual(world)
    paid = (len(world.calls["image"]), len(world.calls["review"]))
    assert visual(world) == first and first[1] == kind
    assert (len(world.calls["image"]), len(world.calls["review"])) == paid


def test_the_outcome_is_logged_per_scene(world):
    world.verdicts[:] = [(False, ["harsh contrast"]), (True, [])]
    visual(world)
    log = image_agent.review_log(SCENE, world.out)
    assert [a["ok"] for a in log["attempts"]] == [False, True]
    assert log["attempts"][0]["problems"] == ["harsh contrast"]
    assert log["accepted"] == log["attempts"][1]["still"]


def test_review_cost_estimate_ignores_the_image_bytes(world, monkeypatch, tmp_path):
    """A real 1376x768 preview is ~85 KB of base64: counted as text it would look like
    tens of thousands of tokens. The estimate must be the same as for a tiny image."""
    from agents import still_critic
    small, large = tmp_path / "small.png", tmp_path / "large.png"
    ff("-f", "lavfi", "-i", "color=c=0x223344:s=64x36", "-frames:v", "1", str(small))
    ff("-f", "lavfi", "-i", "cellauto=s=1376x768", "-frames:v", "1", str(large))  # busy: big JPEG
    checked = []
    monkeypatch.setattr(ledger, "check", lambda provider, eur, held=0.0: checked.append(eur))
    world.verdicts[:] = [(True, []), (True, [])]
    still_critic.review(SCENE, small)
    still_critic.review(SCENE, large)
    assert checked[0] == checked[1] < 0.02
    sent = world.calls["review"][1]["messages"][0]["content"][0]["source"]["data"]
    assert len(sent) > 20_000, "the large preview really is large"


def test_editing_the_concept_asks_for_a_new_review(world):
    world.verdicts[:] = [(True, []), (True, [])]
    visual(world)
    visual(world, {**SCENE, "concept": "Gravity bends the path of light"})
    assert len(world.calls["review"]) == 2, "the verdict was about the old concept"


def test_a_crash_mid_review_does_not_pay_twice(world, monkeypatch):
    world.verdicts[:] = [(False, ["a face"]), (True, [])]
    models = image_agent.google_client.make().models
    real = models.generate_content
    crash = {"now": False}

    def flaky(model, contents, config):
        if len(world.calls["image"]) == 1 and not crash["now"]:
            crash["now"] = True
            raise ConnectionError("network dropped")
        return real(model, contents, config)

    monkeypatch.setattr(image_agent.google_client, "make", lambda: SimpleNamespace(
        models=SimpleNamespace(generate_content=flaky)))
    assert visual(world)[1] == "manim"  # this run lost its second image
    path, kind = visual(world)  # the rerun carries on from attempt 2
    assert kind == "still" and len(world.calls["review"]) == 2, "attempt 1's review was kept, not bought again"
    assert path != image_agent.cache_path(SCENE, world.out), "not the image rejected in attempt 1"
    assert [a["ok"] for a in image_agent.review_log(SCENE, world.out)["attempts"]] == [False, True]


def test_a_forgotten_stub_fails_loudly(monkeypatch, tmp_path, media):
    """The paid-call guard must not be swallowed by the fallbacks into a quiet Manim scene."""
    from conftest import PaidCallInOfflineTest

    class Models:
        def generate_content(self, model, contents, config):
            ff("-f", "lavfi", "-i", "color=c=0x223344:s=64x36", "-frames:v", "1", str(tmp_path / "i.png"))
            part = SimpleNamespace(inline_data=SimpleNamespace(data=(tmp_path / "i.png").read_bytes(),
                                                               mime_type="image/png"), thought=None)
            return SimpleNamespace(parts=[part])

    monkeypatch.setattr(config, "GOOGLE_API_KEY", "offline-test")
    monkeypatch.setattr(google_client, "make", lambda: SimpleNamespace(models=Models()))
    with pytest.raises(PaidCallInOfflineTest):
        pipeline.make_visual(SCENE, 5.0, tmp_path / "v", allow_veo=False)


def estimate_for(tmp_path, scene, **kw):
    work = tmp_path / "w"
    (work / "visuals").mkdir(parents=True, exist_ok=True)
    (work / "script.json").write_text(json.dumps({"title": "T", "scenes": [scene]}))
    return pipeline.estimate_cost(15, work, allow_veo=False, skip_critic=False, **kw), work / "visuals"


def test_estimate_prices_a_still_by_what_is_left_to_decide(world, tmp_path):
    rate_review = ledger.claude_eur(config.CLAUDE_MODEL, *config.EST_STILL_REVIEW_TOKENS)
    manim = ledger.claude_eur(config.CLAUDE_MODEL, *config.EST_MANIM_TOKENS)
    est, visuals = estimate_for(tmp_path, SCENE)
    assert (est["images"], est["claude"]) == pytest.approx((ledger.image_eur(), rate_review))
    worst, _ = estimate_for(tmp_path, SCENE, worst_case=True)
    tries = 1 + config.STILL_REVIEW_RETRIES
    assert worst["claude"] == pytest.approx(tries * rate_review + (config.MAX_MANIM_ATTEMPTS + 1) * manim)

    world.verdicts[:] = [(True, [])]
    image_agent.reviewed_still(SCENE, visuals)
    assert estimate_for(tmp_path, SCENE)[0] == {"claude": 0, "elevenlabs": pytest.approx(est["elevenlabs"]),
                                               "images": 0, "veo": 0}
    log = image_agent.review_log(SCENE, visuals)
    (visuals / log["accepted"]).unlink()  # accepted still lost: it will be made again
    assert estimate_for(tmp_path, SCENE)[0]["images"] == pytest.approx(ledger.image_eur())

    image_agent.review_path(SCENE, visuals).write_text(json.dumps(
        {"accepted": None, "done": True, "attempts": []}))  # rejected for good: Manim instead
    est = estimate_for(tmp_path, SCENE)[0]
    assert est["images"] == 0 and est["claude"] == pytest.approx(manim)
