"""P2-8: the master is one constant gain, read first.

The master used to be a single-pass `loudnorm` (dynamic mode), which plays the film's first narration
about +1.8 dB louder in its first second (the start-up P2-6 removed from each scene clip, found again
in P1-8 / P2-7). Now `assembly.master_level` reads the pre-master mix (EBU R128 with true peak) and
`master_chain` applies one `volume`, plus a limiter only if that gain would pass the ceiling. The reading
is kept in the build folder's `master_level.json` with its own key.

The films are real FFmpeg films; sine tones stand in for the voice (one pitch far from the 220 Hz
music, so a narrow band hears the voice alone). Everything is measured on decoded samples (D12)."""
import json
import shutil
import signal
import statistics
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import build_film, decoded_audio, fake_ffmpeg, ff, onset_s

import assembly
import config
import qa
from agents.music import build_bed
from utils import CommandFailed, duration, file_hash, video_duration

AAC_FRAME = 1024 / config.AUDIO_RATE
EDGE_S = 0.3        # skip a narration's attack and release
FIRST_S = 4.0       # the first narration of the tone films
HZ = [600, 700, 800]  # far from the 220 Hz music: a narrow band hears one voice alone

# What the base (a6e1177) made of these, byte for byte: nothing paid and no clip may re-key (D9).
BASE_UNMASTERED = ("[0:a]asplit=2[voice][key];[1:a]aresample=48000,aformat=channel_layouts=stereo,volume=0.22,"
                   "afade=t=in:d=6.0[mus];[mus][key]sidechaincompress=threshold=0.03:ratio=6:attack=80:release=900"
                   "[duck];[voice]volume=1.0[v];[duck]afade=t=out:st=24.00:d=6.0[bed];"
                   "[v][bed]amix=inputs=2:duration=first:normalize=0[aout]")
BASE_MUSIC_ONLY = BASE_UNMASTERED.replace("[voice]volume=1.0[v]", "[voice]volume=0[v]")
BASE_CLIPS = {"still": "533fa6e4ddab797f.mp4", "manim": "42d528ee539ef32c.mp4"}
BASE_LEVELS = {"level_1d739448d2df76f5.json", "level_51840e08cdb36961.json"}


# --- builders ----------------------------------------------------------------------------------------

def tone(path: Path, hz: int, secs: float) -> Path:
    ff("-f", "lavfi", "-i", f"sine=f={hz}:d={secs}:sample_rate=44100", "-ar", "44100", "-ac", "1", str(path))
    return path


@pytest.fixture(scope="module")
def small_pictures():
    """Low resolution for the module's builds (the function-scoped `small` is not available to module fixtures)."""
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(assembly, "W", 320)
        mp.setattr(assembly, "H", 180)
        yield


@pytest.fixture(scope="module")
def tones(tmp_path_factory):
    d = tmp_path_factory.mktemp("tones")
    return [tone(d / f"t{i}.mp3", hz, FIRST_S + i) for i, hz in enumerate(HZ)]


@pytest.fixture(scope="module", params=[True, False], ids=["with music", "no music"])
def tone_film(request, media, tones, small_pictures, tmp_path_factory):
    return build_film(tmp_path_factory.mktemp("tone_film"), media, voices=tones, music=request.param)


def band(film: Path, hz: int, tmp_path: Path) -> list[float]:
    """The film's audio through a narrow band-pass around one pitch (twice): RMS dB per 10 ms."""
    out = tmp_path / f"band_{hz}.wav"
    narrow = f"bandpass=f={hz}:width_type=q:w=30"
    ff("-i", str(film), "-vn", "-af", f"{narrow},{narrow}", str(out))
    return decoded_audio(out)[1]


def level(levels: list[float], start_s: float, end_s: float) -> float:
    return statistics.median(levels[round(start_s * 100):round(end_s * 100)])


def simple_narrated(path: Path, secs: float = 12.0, hz: int = 300) -> Path:
    """A film's narrated.mp4 without the scene machinery: picture and a tone, AAC, exact frames."""
    ff("-f", "lavfi", "-i", f"testsrc2=s=160x90:r={config.FPS}:d={secs}", "-f", "lavfi", "-i", f"sine=f={hz}:d={secs}",
       "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", str(config.AUDIO_RATE), "-shortest", str(path))
    return path


@pytest.fixture(scope="module")
def parts(media, tmp_path_factory):
    """A build folder with a narrated.mp4 and its bed, and no film yet."""
    work = tmp_path_factory.mktemp("parts")
    narrated = simple_narrated(work / "narrated.mp4")
    bed = build_bed([media / "music.wav"], video_duration(narrated), work / "music_bed.wav", "s")
    return SimpleNamespace(work=work, narrated=narrated, bed=bed)


@pytest.fixture
def folder(parts, tmp_path):
    """A private copy of `parts`: (narrated, bed, the film's path)."""
    work = tmp_path / "work"
    shutil.copytree(parts.work, work)
    return SimpleNamespace(work=work, narrated=work / "narrated.mp4", bed=work / "music_bed.wav",
                           out=work / "film.mp4", level=work / "master_level.json")


@pytest.fixture
def calls(monkeypatch):
    """Every FFmpeg command assembly runs: loudness readings (`-f null -`) and encodes."""
    seen = SimpleNamespace(readings=[], encodes=[])
    real = assembly.run

    def spy(cmd, cwd=None):
        (seen.readings if cmd[-3:] == ["-f", "null", "-"] else seen.encodes).append(cmd)
        return real(cmd, cwd)

    monkeypatch.setattr(assembly, "run", spy)
    return seen


def counts(seen) -> tuple[int, int]:
    return len(seen.readings), len(seen.encodes)


def reset(seen):
    seen.readings.clear()
    seen.encodes.clear()


def pcm_hash(path: Path) -> str:
    """SHA-256 of the film's decoded audio."""
    import hashlib
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:a", "-f", "s16le", "-"],
                         capture_output=True, check=True).stdout
    return hashlib.sha256(raw).hexdigest()


# --- 1, 2: the first narration holds its level; the master lands on target ------------------------------

def test_the_first_narration_holds_its_level_from_its_first_second(tone_film, tmp_path):
    """The acceptance test of P2-8, in the finished film, with and without music: the first narration's first
    second is as loud as the rest of it (base: +1.8 dB, from the master's loudnorm start-up)."""
    voice = band(tone_film, HZ[0], tmp_path)
    start = onset_s(voice, max(voice) - 6)
    end = start + duration(tone_film.parent / "n1.mp3")
    first, rest = level(voice, start + 0.1, start + 1.0), level(voice, start + 1.0, end - EDGE_S)
    assert first - rest == pytest.approx(0, abs=0.5), f"first second {first - rest:+.2f} dB against the rest"


def test_the_master_lands_on_the_target(tone_film):
    integrated, peak = qa.loudness(tone_film)
    assert integrated == pytest.approx(config.TARGET_LUFS, abs=0.5)
    assert peak <= config.QA_MAX_TRUE_PEAK_DBTP


# --- 4, 5, 6: what is read again, and what is remixed ----------------------------------------------------

def test_an_unchanged_film_makes_no_new_reading_and_no_remux(folder, calls):
    assembly.add_music(folder.narrated, folder.bed, folder.out)
    assert counts(calls) == (1, 1)  # a tone is not limited: one reading, one mux
    reset(calls)
    assert assembly.add_music(folder.narrated, folder.bed, folder.out) == folder.out
    assert counts(calls) == (0, 0), "an unchanged film is looked at, not read or mixed"


def test_a_changed_bed_is_read_again_and_remixed(folder, calls, media):
    assembly.add_music(folder.narrated, folder.bed, folder.out)
    before = pcm_hash(folder.out)
    reset(calls)
    ff("-f", "lavfi", "-i", f"sine=f=330:d={duration(folder.narrated)}", "-ac", "2", str(folder.bed.with_name("other.wav")))
    folder.bed.with_name("other.wav").replace(folder.bed)
    assembly.add_music(folder.narrated, folder.bed, folder.out)
    assert counts(calls) == (1, 1)
    assert pcm_hash(folder.out) != before


@pytest.mark.parametrize("what, other", [("RESTAMP", "asetpts=PTS-STARTPTS"), ("METER", "ebur128=peak=true:dualmono=true")],
                         ids=["the cut", "the meter"])
def test_a_changed_cut_or_meter_is_read_again(folder, calls, monkeypatch, what, other):
    """The reading is of one thing: this mix, through this meter, to be cut this way. Any of them changed and
    the kept reading is not believed (a key of the limiter alone would believe it)."""
    assembly.add_music(folder.narrated, folder.bed, folder.out)
    reset(calls)
    monkeypatch.setattr(assembly, what, other)
    assembly.add_music(folder.narrated, folder.bed, folder.out)
    assert len(calls.readings) == 1


@pytest.mark.parametrize("name, other", [("TARGET_LUFS", -17), ("MASTER_TP_DBTP", -3.0),
                                         ("LEVEL_ROUNDS", 2), ("LIMITER_RELEASE_MS", 40)])
def test_a_changed_setting_is_read_again_and_remixed(folder, calls, monkeypatch, name, other):
    """A film mixed with other settings is not the film these settings make (qa.py checks the stamp for it)."""
    assembly.add_music(folder.narrated, folder.bed, folder.out)
    reset(calls)
    monkeypatch.setattr(config, name, other)
    assembly.add_music(folder.narrated, folder.bed, folder.out)
    assert counts(calls) == (1, 1)


# --- 7: QA ------------------------------------------------------------------------------------------------

def test_qa_hears_the_unmastered_mix_and_the_graphs_are_those_of_the_base(media, small_pictures, tmp_path, monkeypatch):
    """qa.py renders the mix BEFORE the master (twice: as is, and with the voice muted after it keys the
    sidechain); its two graph strings are the base's, byte for byte. A film with music passes QA."""
    assert assembly.mix_graph(30.0, master=False) == BASE_UNMASTERED
    assert assembly.mix_graph(30.0, voice_scale=0, master=False) == BASE_MUSIC_ONLY
    film = build_film(tmp_path / "film", media)
    graphs = []
    real = qa.levels_db
    monkeypatch.setattr(qa, "levels_db", lambda inputs, graph=None: graphs.append(graph) or real(inputs, graph))
    report = qa.run_qa(film)
    assert all(c["passed"] for c in report["checks"].values()), json.dumps(report["checks"], indent=1)
    total = video_duration(film)
    rendered = [g for g in graphs if g]
    assert rendered == [assembly.mix_graph(total, master=False) + ";[aout]anull[out]",
                        assembly.mix_graph(total, voice_scale=0, master=False) + ";[aout]anull[out]"]
    assert not any("<gain>" in g or "alimiter" in g or "atrim" in g for g in rendered), "no master in what QA renders"


def test_the_master_chain_is_one_gain_cut_to_the_films_samples():
    chain = assembly.master_chain(30.0, 1.234, False)
    assert chain == "volume=1.23dB,asetpts=N/SR/TB,apad=whole_len=1440000,atrim=end_sample=1440000"
    assert "loudnorm" not in assembly.master_chain(30.0)
    limited = assembly.master_chain(30.0, 1.234, True)
    assert "alimiter=limit=0.7852:" in limited and limited.endswith("aresample=48000," + chain.split(",", 1)[1])


# --- 8: the kept reading ---------------------------------------------------------------------------------

DAMAGE = {
    "empty": "", "truncated": '{"key": "abc', "not JSON": "gain = 3", "a list": "[1, 2]", "a bare number": "7",
    "no gain": lambda f: {k: v for k, v in f.items() if k != "gain_db"},
    "NaN gain": lambda f: f | {"gain_db": float("nan")},
    "a text gain": lambda f: f | {"gain_db": "loud"},
    "no limiter flag": lambda f: {k: v for k, v in f.items() if k != "limited"},
    "another film's key": lambda f: f | {"key": "0123456789abcdef"},
}


def damage(path: Path, how):
    if callable(how):
        how = json.dumps(how(json.loads(path.read_text())))
    path.write_text(how)


def test_a_deleted_reading_is_made_again_without_a_remux(folder, calls):
    out = assembly.add_music(folder.narrated, folder.bed, folder.out)
    first, film = json.loads(folder.level.read_text()), file_hash(out)
    reset(calls)
    folder.level.unlink()
    assembly.add_music(folder.narrated, folder.bed, folder.out)
    assert counts(calls) == (1, 0) and file_hash(out) == film
    assert json.loads(folder.level.read_text()) == first


@pytest.mark.parametrize("how", list(DAMAGE), ids=list(DAMAGE))
def test_a_damaged_or_foreign_reading_is_made_again_without_a_remux(folder, calls, how):
    out = assembly.add_music(folder.narrated, folder.bed, folder.out)
    first, film = json.loads(folder.level.read_text()), file_hash(out)
    damage(folder.level, DAMAGE[how])
    reset(calls)
    assembly.add_music(folder.narrated, folder.bed, folder.out)
    assert counts(calls) == (1, 0), "read once for free, the fresh film is not remuxed"
    assert file_hash(out) == film and json.loads(folder.level.read_text()) == first


def test_a_hand_edited_gain_with_its_key_intact_is_believed_at_the_next_remux(folder, calls):
    out = assembly.add_music(folder.narrated, folder.bed, folder.out)
    kept = json.loads(folder.level.read_text())
    folder.level.write_text(json.dumps(kept | {"gain_db": kept["gain_db"] + 3.0}))
    reset(calls)
    assembly.add_music(folder.narrated, folder.bed, folder.out)
    assert counts(calls) == (0, 0), "a fresh film is not touched"
    Path(str(out) + ".key").unlink()  # now it has to be mixed again
    assembly.add_music(folder.narrated, folder.bed, folder.out)
    assert counts(calls) == (0, 1), "mixed with the edited gain, without reading"
    assert qa.loudness(out)[0] == pytest.approx(config.TARGET_LUFS + 3.0, abs=0.5)


# --- 9: crashes --------------------------------------------------------------------------------------------

def test_a_crash_after_the_reading_and_before_the_stamp_reruns_without_reading(folder, calls, monkeypatch):
    real = assembly.run

    def crash(cmd, cwd=None):
        if cmd[-3:] != ["-f", "null", "-"]:
            raise RuntimeError("the container went away")
        return real(cmd, cwd)

    with monkeypatch.context() as crashing:
        crashing.setattr(assembly, "run", crash)
        with pytest.raises(RuntimeError):
            assembly.add_music(folder.narrated, folder.bed, folder.out)
    assert folder.level.exists() and not folder.out.exists()
    reset(calls)
    assembly.add_music(folder.narrated, folder.bed, folder.out)
    assert counts(calls) == (0, 1), "the reading was kept: the rerun only mixes"


@pytest.fixture
def sigint_raises():
    """Python's own SIGINT handler (a process started in the background inherits SIGINT ignored)."""
    old = signal.signal(signal.SIGINT, signal.default_int_handler)
    yield
    signal.signal(signal.SIGINT, old)


@pytest.mark.parametrize("where", ["the reading", "the mux"])
def test_a_real_interrupt_at_each_blocking_point_leaves_nothing_that_lies(folder, monkeypatch, sigint_raises, where):
    """Ctrl-C (a real SIGINT, raised in the main thread by the handler Python installs) at the master's
    reading and at the mux: no half file, no stamp for a film that is not there, and the rerun finishes."""
    real = assembly.run
    reading = lambda cmd: cmd[-3:] == ["-f", "null", "-"]  # noqa: E731

    def interrupt(cmd, cwd=None):
        if reading(cmd) == (where == "the reading"):
            signal.raise_signal(signal.SIGINT)
        return real(cmd, cwd)

    monkeypatch.setattr(assembly, "run", interrupt)
    with pytest.raises(KeyboardInterrupt):
        assembly.add_music(folder.narrated, folder.bed, folder.out)
    assert not folder.out.exists() and not Path(str(folder.out) + ".key").exists()
    assert not list(folder.work.glob("*.partial*"))
    assert folder.level.exists() == (where == "the mux"), "the reading is kept once it is made, and only then"
    monkeypatch.setattr(assembly, "run", real)
    assembly.add_music(folder.narrated, folder.bed, folder.out)
    assert qa.loudness(folder.out)[0] == pytest.approx(config.TARGET_LUFS, abs=0.5)


# --- 10: music added to the film written before ------------------------------------------------------------

def test_no_music_then_music_to_the_same_path_reads_and_remixes_each_time(folder, calls):
    assembly.add_music(folder.narrated, None, folder.out)
    assert counts(calls) == (1, 1)
    plain = pcm_hash(folder.out)
    reset(calls)
    assembly.add_music(folder.narrated, folder.bed, folder.out)
    assert counts(calls) == (1, 1), "the bed is part of what was read and of what was mixed"
    assert pcm_hash(folder.out) != plain
    reset(calls)
    assembly.add_music(folder.narrated, folder.bed, folder.out)
    assert counts(calls) == (0, 0)


# --- 11: sync ---------------------------------------------------------------------------------------------

def test_the_films_audio_ends_with_its_picture(tone_film):
    secs, _ = decoded_audio(tone_film)
    assert secs == pytest.approx(video_duration(tone_film), abs=AAC_FRAME)


# --- 12: nothing paid, no clip and no level file re-keys -------------------------------------------------------

def test_clip_and_level_file_names_are_those_of_the_base(tmp_path, monkeypatch):
    """D9: this change is the mix only. Two clips (one still, one Manim) and the level files of their
    narrations are named as the base named them, by a hash of the inputs and the exact filters."""
    monkeypatch.setattr(assembly, "run", fake_ffmpeg)
    monkeypatch.setattr(assembly, "duration", lambda p: 3.0)
    monkeypatch.setattr(assembly, "W", config.WIDTH)  # the module's small pictures are in the filter text
    monkeypatch.setattr(assembly, "H", config.HEIGHT)
    names = {}
    for i, kind in enumerate(["still", "manim"]):
        visual, narr = tmp_path / f"v{i}.bin", tmp_path / f"n{i}.bin"
        visual.write_bytes(f"picture {i}".encode())
        narr.write_bytes(f"narration {i}".encode())
        names[kind] = assembly.build_scene_clip(visual, narr, tmp_path / "clips", kind, i + 1).name
    assert names == BASE_CLIPS
    assert {p.name for p in (tmp_path / "clips").glob("level_*")} == BASE_LEVELS


# --- 13: a reading that cannot be made -----------------------------------------------------------------------

def test_a_fresh_film_whose_reading_cannot_be_made_fails_and_stays_as_it_was(folder, monkeypatch):
    out = assembly.add_music(folder.narrated, folder.bed, folder.out)
    before = (file_hash(out), Path(str(out) + ".key").read_text())
    folder.level.unlink()
    real = assembly.run

    def refuse(cmd, cwd=None):
        if cmd[-3:] == ["-f", "null", "-"]:
            raise CommandFailed("ffmpeg went away")
        return real(cmd, cwd)

    monkeypatch.setattr(assembly, "run", refuse)
    with pytest.raises(CommandFailed):
        assembly.add_music(folder.narrated, folder.bed, folder.out)
    assert (file_hash(out), Path(str(out) + ".key").read_text()) == before
    assert not folder.level.exists()


def test_the_master_is_not_a_loudnorm_any_more(folder):
    for bed in (folder.bed, None):
        assert "loudnorm" not in " ".join(assembly._mix_args(folder.narrated, bed, (1.0, False)))
        assert "loudnorm" not in " ".join(assembly._mix_args(folder.narrated, bed))
