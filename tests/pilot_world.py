"""A world for the pilot tool's tests (P3-7): a small reviewed script in tmp_path/topics/demo, and
pipeline.main() run end to end on it with every provider faked and every paid request counted.

The pictures go through the real google-genai SDK on an httpx mock transport (so the request the
pilot sends is the one the SDK would send, under a socket guard, D30); narration, the still review and
Manim's Claude call are stand-ins, as in test_style. Each run() is a new process: it starts from the
default style, and `hold` lets a test stop a run inside a provider call while it does something else."""
import base64
import functools
import json
import socket
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

import config
import pipeline
import qa
from agents import google_client, manim_agent, still_critic, voice
from scripts import make_pilot
from utils import brief_key, load_json, save_json

BRIEF = """# Brief: a demo result

## Facts
- The team reports a signal. Say "reports", never "proves".

## Sources
- [Paper](https://example.org/paper)
"""
OUTLINE = {"title": "The demo film", "chapters": [
    {"number": 1, "title": "One", "minutes": 1.0}, {"number": 2, "title": "Two", "minutes": 1.0}]}


def scene(concept, narration, visual_type, description):
    return {"concept": concept, "narration": narration, "visual_type": visual_type, "visual_description": description}


CHAPTERS = {
    1: [scene("first", "The first line is spoken slowly and with care.", "still", "a quiet field of stars"),
        scene("second", "The second line follows it, a little more softly.", "ai_video", "a slow river of light"),
        scene("third", "The third line draws a simple diagram.", "manim", "two circles that orbit each other")],
    2: [scene("fourth", "The fourth line opens the second chapter.", "still", "a distant pale moon"),
        scene("fifth", "The fifth line closes the film quietly.", "still", "a long calm horizon")],
}


def make_source(root: Path, name="demo", chapters=(1, 2), brief=BRIEF) -> Path:
    """A reviewed script, as topics/<name> holds one."""
    src = root / "topics" / name
    src.mkdir(parents=True, exist_ok=True)
    (src / "brief.md").write_text(brief, encoding="utf-8")
    (src / "brief.key").write_text(brief_key(brief))
    save_json(src / "outline.json", {**OUTLINE, "chapters": [c for c in OUTLINE["chapters"] if c["number"] in chapters]})
    for n in chapters:
        save_json(src / f"chapter_{n:02d}.json", CHAPTERS[n])
    return src


class Pause:
    """A provider hook that stops the run inside a request, once, for the first item `when` accepts, until
    the test lets it go: the run's own thread waits here while the test acts on the same build."""

    def __init__(self, when=lambda item: True):
        self.when, self.reached, self.go = when, threading.Event(), threading.Event()

    def __call__(self, item):
        if self.when(item) and not self.reached.is_set():
            self.reached.set()
            assert self.go.wait(60), "the test never let the run go on"

    def wait_reached(self):
        assert self.reached.wait(60), "the run never got there"


class Running:
    """pipeline.main() on its own thread; join() re-raises what stopped it."""

    def __init__(self):
        self.error = None
        self.thread = threading.Thread(target=self._main, daemon=True)
        self.thread.start()

    def _main(self):
        try:
            pipeline.main()
        except BaseException as e:  # noqa: BLE001 - handed to join()
            self.error = e

    def join(self):
        self.thread.join(60)
        assert not self.thread.is_alive(), "the run did not finish"
        if self.error is not None:
            raise self.error


class World:
    def __init__(self, monkeypatch, tmp_path, media):
        self.mp, self.tmp, self.media = monkeypatch, tmp_path, media
        self.lock = threading.Lock()
        self.asked = {"tts": [], "image": [], "review": [], "manim": []}
        self.hooks = {}  # kind -> fn(item) called before the request is answered (may block)
        self.reject_first = set()  # review once rejects the first picture of a scene whose concept is here
        self.requests = []  # every request that reached the mock transport
        self.sockets = []
        self.png = (media / "still.png").read_bytes()
        self.mp3 = (media / "n_short.mp3").read_bytes()
        self.mp4 = (media / "manim.mp4").read_bytes()
        self.src = make_source(tmp_path)
        self.install()

    @property
    def build(self) -> Path:
        return Path(config.BUILD_DIR)

    def count(self, kind, item):
        with self.lock:
            self.asked[kind].append(item)
        if kind in self.hooks:
            self.hooks[kind](item)

    def install(self):
        mp, world = self.mp, self

        def image_handler(request):
            body = json.loads(request.content)
            prompt = body["contents"][0]["parts"][0]["text"]
            world.requests.append(prompt)
            world.count("image", prompt)
            png = base64.b64encode(world.png).decode()
            return httpx.Response(200, content=json.dumps({"candidates": [{"content": {"role": "model", "parts": [
                {"inlineData": {"mimeType": "image/png", "data": png}}]}, "finishReason": "STOP"}]}))

        def no_connect(sock, address):
            world.sockets.append(address)
            raise OSError("the test opened a network connection")

        mp.setattr(socket.socket, "connect", no_connect)
        mp.setattr(socket.socket, "connect_ex", lambda sock, address: no_connect(sock, address))
        client = httpx.Client(transport=httpx.MockTransport(image_handler))
        mp.setattr(google_client, "make", functools.partial(google_client.make, httpx_client=client))
        mp.setattr(config, "GOOGLE_API_KEY", "offline-test")
        mp.setattr(config, "BUDGET_EUR", 100.0)
        mp.setattr(config, "MUSIC_DIR", str(self.tmp / "no_music"))

        def post(url, headers, json, timeout):
            world.count("tts", json["text"])
            return SimpleNamespace(status_code=200, content=world.mp3, text="", headers={})

        mp.setattr(voice.requests, "post", post)

        def review(scn, still):
            world.count("review", scn["concept"])
            if scn["concept"] in world.reject_first:
                world.reject_first.discard(scn["concept"])
                return {"ok": False, "problems": ["too bright"]}
            return {"ok": True, "problems": []}

        mp.setattr(still_critic, "review", review)

        def manim_text(system, messages):
            world.count("manim", messages[0]["content"][:40])
            return "```python\nfrom manim import *\n```"

        def manim_render(py_file, class_name, media_dir):
            out = media_dir / "videos" / py_file.stem / "480p" / f"{class_name}.mp4"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(world.mp4)
            return out

        mp.setattr(manim_agent, "text", manim_text)
        mp.setattr(manim_agent, "_render", manim_render)
        mp.setattr(manim_agent, "_fit_timing", lambda rendered, *a, **k: rendered)
        # FFmpeg assembly is not what these tests are about: each stand-in writes a short real mp4.
        def write_clip(out):
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(world.mp4)
            return out

        mp.setattr(pipeline, "build_scene_clip", lambda visual, narration, clips, kind, sid, tail: write_clip(
            clips / f"clip_{sid}_{visual.stem}.mp4"))
        mp.setattr(pipeline, "crossfade_concat", lambda clips, out: write_clip(out))
        mp.setattr(pipeline.music, "placeholder_pad", lambda out, *a, **k: write_clip(out))
        mp.setattr(pipeline.music, "build_bed", lambda tracks, total, out, seed: write_clip(out))
        mp.setattr(pipeline, "add_music", lambda narrated, bed, out: write_clip(out))
        mp.setattr(qa, "run_qa", lambda path: {"passed": True})
        mp.setattr(qa, "print_report", lambda report: None)

    def paid(self) -> dict:
        """What has been bought so far (copies, so a later run doesn't change what a test holds)."""
        return {k: list(v) for k, v in self.asked.items()}

    def forget(self):
        for v in self.asked.values():
            v.clear()

    def run(self, *argv, default="calm"):
        self.mp.setattr(config, "STYLE", default)
        self.mp.setattr(sys, "argv", ["pipeline.py", *argv])
        pipeline.main()

    def start(self, *argv) -> "Running":
        """A run in a second thread: `run`'s setup is done here, in the test's own thread."""
        self.mp.setattr(config, "STYLE", "calm")
        self.mp.setattr(sys, "argv", ["pipeline.py", *argv])
        return Running()

    def pilot_argv(self, out: Path, topic="pilot demo", *flags):
        return (topic, "--brief", str(out / "brief.md"), "--style", "flat", "--no-ai-video", *flags)

    def film_argv(self, topic="the film", *flags):
        return (topic, "--brief", str(self.src / "brief.md"), "--style", "flat", "--no-ai-video", *flags)

    def make(self, chapters="1", name="pilot-seed", topic="pilot demo") -> Path:
        out = self.tmp / name
        assert make_pilot.main(["make", str(out), "--from", str(self.src), "--chapters", chapters,
                                "--topic", topic]) == 0
        return out


@pytest.fixture(name="world")
def world_fixture(monkeypatch, tmp_path, media, small, real_google):
    monkeypatch.setattr(make_pilot, "TOPICS", tmp_path / "topics")
    return World(monkeypatch, tmp_path, media)


def tree(root: Path) -> dict[str, bytes | None]:
    """Every file and folder under root (folders as None), for 'nothing was written' checks."""
    if not root.exists() and not root.is_symlink():
        return {}
    return {str(p.relative_to(root)): (p.read_bytes() if p.is_file() else None) for p in sorted(root.rglob("*"))}


def manifest_of(work: Path) -> dict:
    return load_json(work / "manifest.json")


# --- a pilot build made by hand: no run, so a test can break any one thing in it ----------------

def scenes_of(chapters=(1,)) -> list[dict]:
    out = []
    for n in chapters:
        for sc in CHAPTERS[n]:
            out.append({**sc, "id": len(out) + 1, "chapter": n})
    return out


class FakePilot:
    """A finished pilot build, written the way a run writes it (the real cache and review paths, the
    manifest after every picture), with a tweakable part for each file the tool judges."""

    def __init__(self, monkeypatch, work: Path, style="flat", scenes=None):
        from agents import image_agent, manim_agent
        self.work, self.style = work, style
        self.scenes = scenes or scenes_of()
        monkeypatch.setattr(config, "STYLE", style)  # the names of pictures and logs hold the style
        (work / "audio").mkdir(parents=True)
        (work / "visuals").mkdir()
        (work / "clips").mkdir()
        (work / config.STYLE_FILE).write_text(style)
        save_json(work / "script.json", {"title": "The demo film", "scenes": self.scenes})
        self.audio, self.visual, self.log, entries = {}, {}, {}, []
        for s in self.scenes:
            self.audio[s["id"]] = voice.cache_path(s["narration"], work / "audio")
            self.audio[s["id"]].write_bytes(b"mp3 " + s["narration"].encode())
            if s["visual_type"] == "manim":
                self.visual[s["id"]] = manim_agent.cache_path(s, 5.0, work / "visuals", False)
                self.visual[s["id"]].write_bytes(b"manim " + s["concept"].encode())
                kind = "manim"
            else:
                self.visual[s["id"]] = image_agent.cache_path(s, work / "visuals")
                self.visual[s["id"]].write_bytes(b"png " + s["concept"].encode())
                self.log[s["id"]] = image_agent.review_path(s, work / "visuals")
                self.write_log(s["id"])
                kind = "still"
            clip = work / "clips" / f"clip_{s['id']}.mp4"
            clip.write_bytes(b"clip")
            entries.append({"id": s["id"], "concept": s["concept"], "kind": kind,
                            "audio": str(self.audio[s["id"]].relative_to(work)),
                            "visual": str(self.visual[s["id"]].relative_to(work)),
                            "clip": str(clip.relative_to(work)),
                            "still_review": str(self.log[s["id"]].relative_to(work)) if s["id"] in self.log else None})
        save_json(work / "manifest.json", {"title": "The demo film", "style": style, "scenes": entries})
        self.entries = entries
        # what a run leaves that is never carried over
        (work / "failures.json").write_text("{}")
        (work / "narrated.mp4").write_bytes(b"narrated")
        (work / "clips" / "level_0.json").write_text("{}")
        (work / "script.meta.json").write_text("{}")
        (work / "outline.json").write_text("{}")
        (work / "metadata.json").write_text("{}")
        (work / "visuals" / "manim_media" / "videos" / "x" / "480p").mkdir(parents=True)
        (work / "visuals" / "manim_media" / "videos" / "x" / "480p" / "DocScene.mp4").write_bytes(b"scratch")

    def write_log(self, scene_id, **changes):
        log = {"accepted": self.visual[scene_id].name, "done": True,
               "attempts": [{"still": self.visual[scene_id].name, "prompt": "p", "ok": True, "problems": []}]}
        log.update(changes)
        self.log[scene_id].write_text(json.dumps(log))

    def set_manifest(self, **changes):
        manifest = load_json(self.work / "manifest.json")
        manifest.update(changes)
        save_json(self.work / "manifest.json", manifest)

    def files(self) -> dict[str, bytes]:
        """Every plain file of audio/ and visuals/ (not under manim_media), by path."""
        return {str(p.relative_to(self.work)): p.read_bytes()
                for sub in ("audio", "visuals") for p in sorted((self.work / sub).rglob("*"))
                if p.is_file() and not p.is_symlink() and "manim_media" not in p.parts}


@pytest.fixture(name="pilot")
def pilot_fixture(monkeypatch, tmp_path):
    """A finished hand-made pilot in tmp_path/build/pilot-demo and the film's (absent) folder."""
    monkeypatch.setattr(make_pilot, "TOPICS", tmp_path / "topics")
    (tmp_path / "topics").mkdir()
    fake = FakePilot(monkeypatch, tmp_path / "build" / "pilot-demo")
    fake.film = tmp_path / "build" / "the-film"
    fake.src = make_source(tmp_path)  # topics/demo, whose brief the pilot kept: copy finds it by content
    (fake.work / "brief.md").write_text(BRIEF)
    (fake.work / "brief.key").write_text(brief_key(BRIEF))
    return fake
