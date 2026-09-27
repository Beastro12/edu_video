"""P0-6: stills come from the Gemini image model through generate_content (Imagen 4 is
retired from the Gemini API). The real google-genai SDK runs against a mock transport."""
import base64
import functools
import json

import httpx
import pytest
from conftest import ff

import config
import ledger
import pipeline
from agents import google_client, image_agent, manim_agent

SCENE = {"id": 1, "concept": "c", "narration": "n", "visual_type": "still", "visual_description": "a dark sky"}


@pytest.fixture
def image_bytes(tmp_path):
    def make(fmt):
        out = tmp_path / f"img.{fmt}"
        ff("-f", "lavfi", "-i", "color=c=0x223344:s=64x36", "-frames:v", "1", str(out))
        return out.read_bytes()
    return make


def gemini_via(monkeypatch, outcomes):
    """Real google-genai client on a mock transport; outcomes are status codes or response bodies."""
    requests = []

    def handler(request):
        requests.append({"url": str(request.url), **json.loads(request.content)})
        outcome = outcomes.pop(0)
        if isinstance(outcome, int):
            return httpx.Response(outcome, content=json.dumps({"error": {"code": outcome, "message": "x"}}))
        return httpx.Response(200, content=json.dumps(outcome))

    monkeypatch.setattr(config, "GOOGLE_API_KEY", "offline-test")
    monkeypatch.setattr(config, "RETRY_BASE_S", 0.001)
    monkeypatch.setattr(config, "RETRY_JITTER_S", 0.001)
    mock = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(google_client, "make", functools.partial(google_client.make, httpx_client=mock))
    return requests


def image_response(data: bytes, mime: str) -> dict:
    return {"candidates": [{"content": {"role": "model", "parts": [
        {"inlineData": {"mimeType": mime, "data": base64.b64encode(data).decode()}}]}, "finishReason": "STOP"}]}


def entries():
    path = ledger.ledger_path()
    return path.read_text().splitlines() if path.exists() else []


def test_still_comes_from_the_gemini_image_model(real_google, monkeypatch, tmp_path, image_bytes):
    png = image_bytes("png")
    sent = gemini_via(monkeypatch, [503, image_response(png, "image/png")])
    out = image_agent.render_still(SCENE, tmp_path)

    assert out.read_bytes() == png
    assert len(sent) == 2, "a transient error is retried"
    body = sent[-1]
    assert body["generationConfig"]["responseModalities"] == ["IMAGE"]
    assert body["generationConfig"]["imageConfig"]["aspectRatio"] == config.ASPECT_RATIO
    assert body["generationConfig"]["imageConfig"]["imageSize"] == config.IMAGE_SIZE
    assert f"/models/{config.IMAGE_MODEL}:generateContent" in body["url"]
    assert body["contents"][0]["parts"][0]["text"].startswith("a dark sky")
    assert len(entries()) == 1, "one image recorded, not one per attempt"


def test_jpeg_answer_is_stored_as_png(real_google, monkeypatch, tmp_path, image_bytes):
    gemini_via(monkeypatch, [image_response(image_bytes("jpg"), "image/jpeg")])
    out = image_agent.render_still(SCENE, tmp_path)
    assert out.suffix == ".png"
    assert out.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_no_image_in_the_answer_falls_back_to_manim(real_google, monkeypatch, tmp_path, media):
    refused = {"candidates": [{"content": {"role": "model", "parts": [{"text": "I can't draw that."}]},
                               "finishReason": "STOP"}]}
    gemini_via(monkeypatch, [refused])
    monkeypatch.setattr(manim_agent, "render_scene", lambda *a, **k: media / "manim.mp4")
    visual, kind = pipeline.make_visual(SCENE, 5.0, tmp_path, allow_veo=False)
    assert kind == "manim"
    assert entries() == [], "no image, nothing recorded"


@pytest.mark.parametrize("where", [0, 1])  # before or after the finished image
def test_interim_thought_images_are_skipped(real_google, monkeypatch, tmp_path, image_bytes, where):
    png = image_bytes("png")
    answer = image_response(png, "image/png")
    answer["candidates"][0]["content"]["parts"].insert(where, {
        "thought": True, "inlineData": {"mimeType": "image/png", "data": base64.b64encode(b"draft").decode()}})
    gemini_via(monkeypatch, [answer])
    assert image_agent.render_still(SCENE, tmp_path).read_bytes() == png
