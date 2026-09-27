"""Offline test of the outline -> chapter -> critic flow with Claude mocked out."""
import json

import pytest

import agents.critic as critic
import agents.script_agent as script_agent
import pipeline
from utils import load_json, save_json


def make_fake(calls):
    def fake(system, prompt, tool, schema, max_tokens=8000):
        calls.append((tool, prompt))
        if tool == "outline":
            return {"title": "T", "chapters": [
                {"number": i, "title": f"Ch{i}", "summary": "s", "key_ideas": ["k"], "minutes": 2} for i in (1, 2)]}
        if tool == "chapter":
            n = sum(1 for c in calls if c[0] == "chapter")
            return {"scenes": [{"concept": f"c{n}.{j}", "narration": f"chapter {n} line {j}",
                                "visual_type": "still", "visual_description": "d"} for j in (1, 2, 3)]}
        if tool == "review":
            scenes = json.loads(prompt.split("scenes:\n", 1)[1])
            return {"approved": True, "issues": [], "revised_scenes": scenes}
        raise AssertionError(tool)
    return fake


def test_chapters_numbered_continuous_and_cached(tmp_path, monkeypatch):
    calls = []
    fake = make_fake(calls)
    monkeypatch.setattr(script_agent, "structured", fake)
    monkeypatch.setattr(critic, "structured", fake)

    script = pipeline.get_script("topic", 4, tmp_path, skip_critic=False)

    assert [s["id"] for s in script["scenes"]] == list(range(1, 7))
    assert [s["chapter"] for s in script["scenes"]] == [1, 1, 1, 2, 2, 2]
    chapter2_prompt = [p for t, p in calls if t == "chapter"][1]
    assert "chapter 1 line 3" in chapter2_prompt, "writer must receive the previous chapter's ending"

    n = len(calls)
    pipeline.get_script("topic", 4, tmp_path, skip_critic=False)
    assert len(calls) == n, "rerun must not call Claude again"


def _built_script(tmp_path, monkeypatch):
    calls = []
    fake = make_fake(calls)
    monkeypatch.setattr(script_agent, "structured", fake)
    monkeypatch.setattr(critic, "structured", fake)
    pipeline.get_script("topic", 4, tmp_path, skip_critic=False)
    return calls


def test_chapter_edits_reach_script_json(tmp_path, monkeypatch):
    calls = _built_script(tmp_path, monkeypatch)
    n = len(calls)
    ch2 = load_json(tmp_path / "chapter_02.json")
    ch2[0]["narration"] = "an edited line"
    del ch2[2]  # a chapter losing a scene renumbers everything after it
    save_json(tmp_path / "chapter_02.json", ch2)

    script = pipeline.get_script("topic", 4, tmp_path, skip_critic=False)

    assert script["scenes"][3]["narration"] == "an edited line"
    assert [s["id"] for s in script["scenes"]] == [1, 2, 3, 4, 5]
    assert load_json(tmp_path / "script.json") == script
    assert len(calls) == n, "rebuilding from edited chapters must not call Claude"


def test_hand_edits_to_script_json_are_kept(tmp_path, monkeypatch):
    _built_script(tmp_path, monkeypatch)
    script = load_json(tmp_path / "script.json")
    script["scenes"][0]["narration"] = "edited in script.json"
    save_json(tmp_path / "script.json", script)

    again = pipeline.get_script("topic", 4, tmp_path, skip_critic=False)
    assert again["scenes"][0]["narration"] == "edited in script.json"


def test_editing_both_script_and_chapter_refuses_to_guess(tmp_path, monkeypatch):
    _built_script(tmp_path, monkeypatch)
    script = load_json(tmp_path / "script.json")
    script["scenes"][0]["narration"] = "edited in script.json"
    save_json(tmp_path / "script.json", script)
    ch1 = load_json(tmp_path / "chapter_01.json")
    ch1[1]["narration"] = "edited in the chapter"
    save_json(tmp_path / "chapter_01.json", ch1)

    with pytest.raises(RuntimeError, match=r"Both .* and a chapter file were edited"):
        pipeline.get_script("topic", 4, tmp_path, skip_critic=False)
