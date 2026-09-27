"""P0-2: every paid call is budget-checked before it is made and logged after it succeeds."""
import json
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest
from conftest import ROOT

import config
import ledger
import pipeline
from agents import ai_video, image_agent, llm, manim_agent, voice


def entries():
    path = ledger.ledger_path()
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


@pytest.fixture
def providers(monkeypatch):
    """Fakes for all four paid providers; `calls` counts what actually reached them."""
    calls = []

    def fake_create(**kwargs):
        calls.append("anthropic")
        return SimpleNamespace(content=[SimpleNamespace(type="tool_use", input={"ok": True})],
                               usage=SimpleNamespace(input_tokens=1200, output_tokens=300))

    def fake_post(url, headers, json, timeout):
        calls.append("elevenlabs")
        return SimpleNamespace(status_code=200, content=b"mp3", text="")

    class FakeVideo:
        def save(self, path):
            open(path, "wb").write(b"mp4")

    class FakeModels:
        def generate_images(self, model, prompt, config):
            calls.append("imagen")
            image = SimpleNamespace(image_bytes=b"png")
            return SimpleNamespace(generated_images=[SimpleNamespace(image=image)])

        def generate_videos(self, model, prompt, config):
            calls.append("veo")
            response = SimpleNamespace(generated_videos=[SimpleNamespace(video=FakeVideo())])
            return SimpleNamespace(done=True, error=None, response=response)

    class FakeClient:
        def __init__(self, api_key):
            self.models = FakeModels()
            self.files = SimpleNamespace(download=lambda file: None)

    monkeypatch.setattr(llm._client.messages, "create", fake_create)
    monkeypatch.setattr(voice.requests, "post", fake_post)
    monkeypatch.setattr("google.genai.Client", FakeClient)
    monkeypatch.setattr(config, "GOOGLE_API_KEY", "offline-test")
    monkeypatch.setattr(config, "BUDGET_EUR", 10.0)
    return calls


SCENE = {"id": 1, "concept": "c", "narration": "The sky darkens slowly.",
         "visual_type": "still", "visual_description": "a dark sky"}


def test_every_paid_call_is_logged_with_its_estimated_cost(providers, tmp_path):
    llm.structured("system", "prompt", "thing", {"type": "object"})
    voice.narrate(SCENE["narration"], tmp_path)
    image_agent.render_still(SCENE, tmp_path)
    ai_video.render_scene(SCENE, tmp_path)

    log = entries()
    assert [e["provider"] for e in log] == ["anthropic", "elevenlabs", "google", "google"]
    for e in log:
        assert set(e) == {"time", "provider", "model", "units", "est_cost_eur"}
    usd_in, usd_out = config.CLAUDE_USD_PER_MTOK[config.CLAUDE_MODEL]
    rate = config.USD_TO_EUR
    assert log[0]["units"] == {"input_tokens": 1200, "output_tokens": 300}
    assert log[0]["est_cost_eur"] == pytest.approx((1200 * usd_in + 300 * usd_out) / 1e6 * rate)
    assert log[1]["units"] == {"characters": len(SCENE["narration"])}
    assert log[1]["est_cost_eur"] == pytest.approx(
        len(SCENE["narration"]) / 1000 * config.TTS_USD_PER_1K_CHARS * rate)
    assert (log[2]["model"], log[2]["units"]) == (config.IMAGE_MODEL, {"images": 1})
    assert log[2]["est_cost_eur"] == pytest.approx(config.IMAGE_USD_PER_IMAGE * rate)
    assert (log[3]["model"], log[3]["units"]) == (config.VEO_MODEL, {"seconds": config.VEO_CLIP_S})
    assert log[3]["est_cost_eur"] == pytest.approx(config.VEO_CLIP_S * config.VEO_USD_PER_SECOND * rate)
    assert ledger.spent_eur() == pytest.approx(sum(e["est_cost_eur"] for e in log))

    voice.narrate(SCENE["narration"], tmp_path)  # cached: no call, no entry
    assert len(entries()) == 4


@pytest.mark.parametrize("call", [
    lambda d: llm.structured("system", "prompt", "thing", {"type": "object"}),
    lambda d: voice.narrate(SCENE["narration"], d),
    lambda d: image_agent.render_still(SCENE, d),
    lambda d: ai_video.render_scene(SCENE, d),
])
def test_call_that_would_exceed_the_budget_is_never_made(providers, monkeypatch, tmp_path, call):
    ledger.record("elevenlabs", "earlier run", {"characters": 1}, 9.999)
    with pytest.raises(ledger.BudgetExceeded):
        call(tmp_path)
    assert providers == [], "the provider must not be called once the budget is gone"
    assert len(entries()) == 1


def test_budget_stop_is_not_swallowed_by_the_visual_fallbacks(providers, monkeypatch, tmp_path):
    ledger.record("elevenlabs", "earlier run", {"characters": 1}, 9.999)
    monkeypatch.setattr(voice, "narrate", lambda text, out_dir: pytest.fail("audio is cached in this test"))
    monkeypatch.setattr(manim_agent, "text", lambda *a, **k: pytest.fail("fell back to Manim"))
    for vt in ("ai_video", "still"):
        with pytest.raises(ledger.BudgetExceeded):
            pipeline.make_visual({**SCENE, "visual_type": vt}, 5.0, tmp_path, allow_veo=True)
    assert providers == []


def test_veo_over_budget_stops_the_run_even_if_a_still_would_fit(providers, monkeypatch, tmp_path):
    ledger.record("elevenlabs", "earlier run", {"characters": 1}, 9.0)  # €1 left: a still fits, a clip doesn't
    assert ledger.image_eur() < 1 < ledger.video_eur(config.VEO_CLIP_S)
    with pytest.raises(ledger.BudgetExceeded):
        pipeline.make_visual({**SCENE, "visual_type": "ai_video"}, 5.0, tmp_path, allow_veo=True)
    assert providers == [], "must not quietly buy a still instead"


def test_veo_timeout_is_recorded_as_spent(providers, monkeypatch, tmp_path):
    class NeverDone:
        def generate_videos(self, model, prompt, config):
            providers.append("veo")
            return SimpleNamespace(done=False)

    monkeypatch.setattr("google.genai.Client", lambda api_key: SimpleNamespace(models=NeverDone()))
    with pytest.raises(RuntimeError, match="timed out"):
        ai_video.render_scene(SCENE, tmp_path, timeout_s=-1)
    assert [(e["provider"], e["units"]["seconds"]) for e in entries()] == [("google", config.VEO_CLIP_S)]


def test_budget_defaults_to_10_and_reads_the_env():
    # dotenv disabled: a BUDGET_EUR in Pietro's .env must not change what this test sees
    code = "import dotenv; dotenv.load_dotenv = lambda *a, **k: False; import config; print(config.BUDGET_EUR)"
    env = {k: v for k, v in os.environ.items() if k != "BUDGET_EUR"}
    run = lambda e: subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=e,  # noqa: E731
                                   capture_output=True, text=True, check=True).stdout.strip()
    assert run(env) == "10.0"
    assert run({**env, "BUDGET_EUR": "3.5"}) == "3.5"


def test_estimate_prices_uncached_work_without_calling_anything(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(config, "GOOGLE_API_KEY", "offline-test")  # no fakes: any call fails the test
    work = tmp_path / "build" / "topic"
    work.mkdir(parents=True)
    scenes = [
        {"id": 1, "concept": "a", "narration": "x" * 400, "visual_type": "still", "visual_description": "s1"},
        {"id": 2, "concept": "b", "narration": "y" * 300, "visual_type": "still", "visual_description": "s2"},
        {"id": 3, "concept": "c", "narration": "z" * 200, "visual_type": "ai_video", "visual_description": "v"},
    ]
    (work / "script.json").write_text(json.dumps({"title": "T", "scenes": scenes}))
    voice.cache_path(scenes[0]["narration"], work / "audio").parent.mkdir(parents=True)
    voice.cache_path(scenes[0]["narration"], work / "audio").write_bytes(b"cached")
    image_agent.cache_path(scenes[1], work / "visuals").parent.mkdir(parents=True)
    image_agent.cache_path(scenes[1], work / "visuals").write_bytes(b"cached")

    est = pipeline.estimate_cost(15, work, allow_veo=True, skip_critic=False)

    rate = config.USD_TO_EUR
    assert est["claude"] == 0  # script exists; no Manim scenes
    assert est["elevenlabs"] == pytest.approx(500 / 1000 * config.TTS_USD_PER_1K_CHARS * rate)
    assert est["images"] == pytest.approx(1 * config.IMAGE_USD_PER_IMAGE * rate)
    assert est["veo"] == pytest.approx(config.VEO_CLIP_S * config.VEO_USD_PER_SECOND * rate)

    monkeypatch.setattr(sys, "argv", ["pipeline.py", "topic", "--estimate"])
    monkeypatch.chdir(tmp_path)
    pipeline.main()
    out = capsys.readouterr().out
    assert f"€{sum(est.values()):.2f}" in out
    assert not ledger.ledger_path().exists()


def test_estimate_before_any_script_projects_all_stages(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "GOOGLE_API_KEY", "offline-test")
    est = pipeline.estimate_cost(15, tmp_path / "new", allow_veo=False, skip_critic=False)
    assert est["claude"] > 0 and est["elevenlabs"] > 0 and est["images"] > 0
    assert est["veo"] == 0
    chars = 15 * config.WORDS_PER_MIN * config.EST_CHARS_PER_WORD
    assert est["elevenlabs"] == pytest.approx(chars / 1000 * config.TTS_USD_PER_1K_CHARS * config.USD_TO_EUR,
                                              rel=0.05)


def test_estimate_without_google_counts_visuals_as_manim_and_skips_cached_ones(monkeypatch, tmp_path):
    from conftest import ff
    monkeypatch.setattr(config, "GOOGLE_API_KEY", None)
    work = tmp_path / "w"
    work.mkdir()
    scenes = [{"id": 1, "concept": "a", "narration": "n" * 100, "visual_type": "still", "visual_description": "s"},
              {"id": 2, "concept": "b", "narration": "m" * 100, "visual_type": "manim", "visual_description": "d"}]
    (work / "script.json").write_text(json.dumps({"title": "T", "scenes": scenes}))
    audio = voice.cache_path(scenes[0]["narration"], work / "audio")
    audio.parent.mkdir()
    ff("-f", "lavfi", "-i", "sine=d=1", str(audio))
    # the still scene falls back to an *atmospheric* Manim render; that one is already cached
    target = config.LEAD_IN_S + pipeline.duration(audio) + config.TAIL_S
    cached = manim_agent.cache_path(scenes[0], target, work / "visuals", atmospheric=True)
    cached.parent.mkdir()
    cached.write_bytes(b"mp4")

    est = pipeline.estimate_cost(15, work, allow_veo=False, skip_critic=False)
    worst = pipeline.estimate_cost(15, work, allow_veo=False, skip_critic=False, worst_case=True)

    one_manim = ledger.claude_eur(config.CLAUDE_MODEL, *config.EST_MANIM_TOKENS)
    assert est["images"] == est["veo"] == 0
    assert est["claude"] == pytest.approx(one_manim), "only the uncached Manim scene costs"
    assert worst["claude"] == pytest.approx(config.MAX_MANIM_ATTEMPTS * one_manim)


def test_estimate_skip_critic_drops_the_review_calls(monkeypatch, tmp_path):
    with_critic = pipeline.estimate_cost(15, tmp_path / "a", allow_veo=False, skip_critic=False)
    without = pipeline.estimate_cost(15, tmp_path / "a", allow_veo=False, skip_critic=True)
    worst = pipeline.estimate_cost(15, tmp_path / "a", allow_veo=False, skip_critic=False, worst_case=True)
    assert without["claude"] < with_critic["claude"] < worst["claude"]
