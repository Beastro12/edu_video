"""P1-4: metadata.json (title, description, chapter timestamps from the real clip boundaries,
following YouTube's rules) and subtitles.srt timed from each scene's narration. Timings are
checked against voice onsets measured in the decoded film (D12), not against the same
arithmetic the code uses."""
import json
import re
import shutil

import pytest
from conftest import build_film, decoded_audio

import assembly
import config
import metadata
from utils import load_json, save_json

TEXTS = [
    "The night is quiet. Far away, a star is burning.",
    "Its light travels outward, in every direction, for a very long time.",
    "Some of that light reaches us. We see the star as it was, long ago.",
    "Gravity shapes everything the light passes on its way.",
    "Near a massive object, the path of light bends, gently.",
    "And so the sky we see is a quiet record of all these journeys.",
]
TITLES = ("Starlight", "Distance", "Gravity")


@pytest.fixture(scope="module")
def built(media, tmp_path_factory):
    """One film for the module (six scenes in three chapters), with an outline."""
    size = assembly.W, assembly.H
    assembly.W, assembly.H = 320, 180
    try:
        final = build_film(tmp_path_factory.mktemp("film") / "film", media, narrations=TEXTS,
                           chapters=[1, 1, 2, 2, 3, 3])
    finally:
        assembly.W, assembly.H = size
    save_json(final.parent / "outline.json", {"title": "Starlight", "chapters": [
        {"number": n, "title": t, "summary": "s", "key_ideas": [], "minutes": 1} for n, t in enumerate(TITLES, 1)]})
    return final.parent


@pytest.fixture
def work(built, tmp_path):
    """A private copy, so a test can rewrite the script without affecting the others."""
    copy = tmp_path / "work"
    shutil.copytree(built, copy)
    return copy


def voices(work):
    """(onset, end) of each narration, measured in the decoded narrated.mp4."""
    _, levels = decoded_audio(work / "narrated.mp4")
    loud = [db > -40 for db in levels]
    spans, i = [], 0
    while i < len(loud):
        if loud[i]:
            j = i
            while j < len(loud) and loud[j]:
                j += 1
            spans.append((i / 100, j / 100))
            i = j
        else:
            i += 1
    return spans


def srt_cues(path):
    t = r"(\d\d):(\d\d):(\d\d),(\d\d\d)"
    cues = []
    for block in path.read_text().strip().split("\n\n"):
        lines = block.split("\n")
        g = [int(x) for x in re.fullmatch(f"{t} --> {t}", lines[1]).groups()]
        cues.append((int(lines[0]), g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000,
                     g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000, lines[2:]))
    return cues


def test_chapters_start_where_their_first_scene_starts_in_the_film(work):
    meta = metadata.write_metadata(work)
    spans = voices(work)
    assert len(spans) == len(TEXTS)
    assert [c["title"] for c in meta["chapters"]] == list(TITLES)
    assert meta["chapters"][0]["start"] == "00:00"
    for chapter, first_scene in zip(meta["chapters"], [0, 2, 4], strict=True):
        assert chapter["seconds"] + config.LEAD_IN_S == pytest.approx(spans[first_scene][0], abs=0.03)
    lines = meta["description"].splitlines()
    assert lines[0] == config.YOUTUBE_DESCRIPTION
    assert lines.index("00:00 Starlight") < lines.index(f"{meta['chapters'][1]['start']} Distance")
    assert meta["title"] == load_json(work / "script.json")["title"], "the title you'd edit is script.json's"
    assert json.loads((work / "metadata.json").read_text()) == meta


def test_subtitles_follow_the_narration_heard_in_the_film(work):
    metadata.write_metadata(work)
    cues = srt_cues(work / "subtitles.srt")
    assert [c[0] for c in cues] == list(range(1, len(cues) + 1))
    assert " ".join(" ".join(c[3]) for c in cues).split() == " ".join(TEXTS).split(), "every word, in order"
    assert all(len(" ".join(c[3])) <= metadata.CUE_CHARS and len(c[3]) <= 2 for c in cues)
    assert all(a[2] <= b[1] for a, b in zip(cues, cues[1:], strict=False)), "no overlapping cues"
    for (onset, end), text in zip(voices(work), TEXTS, strict=True):
        mine = [c for c in cues if onset - 0.05 <= c[1] < end]
        assert " ".join(" ".join(c[3]) for c in mine) == text
        assert mine[0][1] == pytest.approx(onset, abs=0.03)
        assert mine[-1][2] == pytest.approx(end, abs=0.08)  # mp3 padding makes the file a little longer


def test_fewer_than_three_chapters_are_left_out_of_the_description(work):
    script = load_json(work / "script.json")
    for scene, n in zip(script["scenes"], [1, 1, 1, 2, 2, 2], strict=True):
        scene["chapter"] = n
    save_json(work / "script.json", script)
    meta = metadata.write_metadata(work)
    assert len(meta["chapters"]) == 2
    assert "00:00" not in meta["description"], "YouTube ignores fewer than 3 chapters"


def marks(chapters, starts, total):
    scenes = [{"chapter": n} for n in chapters]
    titles = {1: "A", 2: "B", 3: "C", 4: "D"}
    return [(c["seconds"], c["title"]) for c in metadata.chapter_marks(scenes, starts, titles, total)]


@pytest.mark.parametrize("chapters, starts, total, expected", [
    ([1, 2, 3], [0, 20, 40], 60, [(0, "A"), (20, "B"), (40, "C")]),          # all long enough
    ([1, 2, 3, 4], [0, 6, 30, 50], 70, [(0, "B"), (30, "C"), (50, "D")]),    # short opening takes the next title
    ([1, 2, 3, 4], [0, 20, 25, 50], 70, [(0, "A"), (25, "C"), (50, "D")]),   # short middle goes to the one before
    ([1, 2, 3, 4], [0, 20, 40, 65], 70, [(0, "A"), (20, "B"), (40, "C")]),   # short last, measured to the end
    ([1, 2, 3], [0, 4, 8], 12, [(0, "C")]),                                   # all short: one chapter
])
def test_chapter_rules(chapters, starts, total, expected):
    result = marks(chapters, starts, total)
    assert result == expected
    ends = [s for s, _ in result[1:]] + [total]
    if len(result) > 1:
        assert all(e - s >= metadata.MIN_CHAPTER_S for (s, _), e in zip(result, ends, strict=True))


def test_hour_long_timestamps():
    assert metadata.timestamp(3725) == "1:02:05" and metadata.timestamp(65) == "01:05"


def test_cues_pack_sentences_and_break_into_two_lines():
    cues = metadata.cue_texts("Dr. Smith looked up. Wait... what? It was late.")
    assert cues == ["Dr. Smith looked up. Wait... what? It was late."], "no flash cues from abbreviations"
    long = " ".join(["slowly"] * 30)
    assert all(len(c) <= metadata.CUE_CHARS for c in metadata.cue_texts(long))
    two = metadata.two_lines("The light that reaches us tonight left its star long ago.")
    assert two.count("\n") == 1 and all(len(line) <= metadata.LINE_CHARS for line in two.split("\n"))


def test_youtube_limits(work, monkeypatch):
    script = load_json(work / "script.json")
    script["title"] = "<b>Why</b> " + "the night sky is dark " * 10
    save_json(work / "script.json", script)
    meta = metadata.write_metadata(work)
    assert len(meta["title"]) <= metadata.TITLE_MAX and "<" not in meta["title"] and ">" not in meta["title"]
    monkeypatch.setattr(config, "YOUTUBE_DESCRIPTION", "x" * 6000)
    with pytest.raises(ValueError, match="5000 bytes"):
        metadata.write_metadata(work)
