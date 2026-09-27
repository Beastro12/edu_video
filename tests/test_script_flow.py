"""Offline test of the outline -> chapter -> critic flow with Claude mocked out."""
import json

import agents.critic as critic
import agents.script_agent as script_agent
import pipeline


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
