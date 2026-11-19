"""P2-8 adversary tests: the master's kept reading (master_level.json) against what a hostile sequence of
runs could make of it. A film is always what a clean build of its inputs makes (compared as decoded PCM,
SHA-256), and a reading is believed only for the inputs and settings it was made for."""
import json
import pathlib
import shutil
import threading
from types import SimpleNamespace

import pytest
from conftest import ff
from test_master_level import calls, counts, pcm_hash, reset  # noqa: F401  (calls is a fixture)

import assembly
import config
import qa
from agents.music import build_bed
from utils import is_fresh, video_duration


def make_folder(media, work: pathlib.Path, hz: int = 300, quiet_db: float = 0.0) -> SimpleNamespace:
    """A build folder: narrated.mp4 (a tone, `quiet_db` quieter) and its bed. No film yet."""
    work.mkdir(parents=True)
    narrated = work / "narrated.mp4"
    ff("-f", "lavfi", "-i", f"testsrc2=s=160x90:r={config.FPS}:d=12", "-f", "lavfi", "-i", f"sine=f={hz}:d=12",
       "-af", f"volume={quiet_db}dB", "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", str(config.AUDIO_RATE),
       "-shortest", str(narrated))
    bed = build_bed([media / "music.wav"], video_duration(narrated), work / "music_bed.wav", "s")
    return SimpleNamespace(work=work, narrated=narrated, bed=bed, out=work / "film.mp4",
                           level=work / "master_level.json")


def clean_build(media, work: pathlib.Path, **kwargs) -> SimpleNamespace:
    folder = make_folder(media, work, **kwargs)
    assembly.add_music(folder.narrated, folder.bed, folder.out)
    return folder


def test_a_changed_narration_of_the_same_length_is_remixed_and_read_like_a_clean_build(media, tmp_path, calls):  # noqa: F811
    """(a) The narration changes but its length does not (same frames, same samples): nothing about the
    film's size says it changed. It is read again and remixed, and both the reading and the film are
    those of a clean build."""
    old = clean_build(media, tmp_path / "old", hz=300)
    new = clean_build(media, tmp_path / "new", hz=450, quiet_db=-6.0)  # what the folder should become
    assert pcm_hash(old.out) != pcm_hash(new.out)
    shutil.copyfile(new.narrated, old.narrated)  # the narration is replaced in the old folder
    reset(calls)
    assembly.add_music(old.narrated, old.bed, old.out)
    assert counts(calls) == (1, 1)
    assert json.loads(old.level.read_text()) == json.loads(new.level.read_text())
    assert pcm_hash(old.out) == pcm_hash(new.out)


def test_a_crash_after_the_film_is_replaced_and_before_its_stamp_leaves_a_rerun_that_equals_a_clean_build(
        media, tmp_path, calls, monkeypatch):  # noqa: F811
    """(b) The new film is already in place when the process dies, and its stamp is not written: the old
    stamp is gone too (stamped_output drops it first), so the rerun mixes again, with the reading kept."""
    work = clean_build(media, tmp_path / "work", hz=300)
    clean = clean_build(media, tmp_path / "clean", hz=300)
    for folder in (work, clean):  # both beds change, the same way
        ff("-f", "lavfi", "-i", f"sine=f=330:d={video_duration(folder.narrated)}", "-ac", "2", str(folder.work / "o.wav"))
        (folder.work / "o.wav").replace(folder.bed)
    assembly.add_music(clean.narrated, clean.bed, clean.out)

    real = pathlib.Path.write_text

    def die_at_the_stamp(self, *args, **kwargs):
        if self.name == "film.mp4.key":
            raise KeyboardInterrupt("the container went away")
        return real(self, *args, **kwargs)

    with monkeypatch.context() as crashing:
        crashing.setattr(pathlib.Path, "write_text", die_at_the_stamp)
        with pytest.raises(KeyboardInterrupt):
            assembly.add_music(work.narrated, work.bed, work.out)
    assert work.out.exists() and not (work.work / "film.mp4.key").exists(), "a film in place, no stamp that vouches for it"
    reset(calls)
    assembly.add_music(work.narrated, work.bed, work.out)
    assert counts(calls) == (0, 1), "the reading was saved before the film was touched"
    assert pcm_hash(work.out) == pcm_hash(clean.out)
    assert is_fresh(work.out, assembly.mix_key(work.narrated, work.bed))


def test_another_films_reading_copied_in_is_never_applied(media, tmp_path, calls):  # noqa: F811
    """(c) A master_level.json of another film (the same file name, another narration's loudness) is not
    believed: with the film fresh it is read again and nothing is remixed (readings >= 1, remuxes == 0); with
    the film to be mixed again it is read first and the film is at TARGET_LUFS, not at the stranger's gain.
    The amended invariant: with its own reading kept, an unchanged rerun runs no FFmpeg at all."""
    mine = clean_build(media, tmp_path / "mine", hz=300)
    other = clean_build(media, tmp_path / "other", hz=300, quiet_db=-14.0)
    own = json.loads(mine.level.read_text())
    stranger = json.loads(other.level.read_text())
    assert abs(stranger["gain_db"] - own["gain_db"]) > 5, "the stranger's gain would be plainly wrong here"
    film = pcm_hash(mine.out)

    reset(calls)
    assembly.add_music(mine.narrated, mine.bed, mine.out)
    assert counts(calls) == (0, 0), "its own reading kept: no FFmpeg at all"

    shutil.copyfile(other.level, mine.level)
    reset(calls)
    assembly.add_music(mine.narrated, mine.bed, mine.out)
    readings, remuxes = counts(calls)
    assert readings >= 1 and remuxes == 0
    assert json.loads(mine.level.read_text()) == own and pcm_hash(mine.out) == film

    shutil.copyfile(other.level, mine.level)
    (mine.work / "film.mp4.key").unlink()
    reset(calls)
    assembly.add_music(mine.narrated, mine.bed, mine.out)
    assert counts(calls) == (1, 1)
    assert qa.loudness(mine.out)[0] == pytest.approx(config.TARGET_LUFS, abs=0.5)


def test_two_threads_mixing_one_build_while_a_third_scribbles_on_its_reading_leave_one_true_film(
        media, tmp_path, calls):  # noqa: F811
    """A second actor on the same build: two add_music calls at once and a thread overwriting
    master_level.json with garbage throughout. Every call returns, the film ends fresh and equal to a
    clean build's, and the scribbled reading is read again (never remixing) afterwards."""
    folder = make_folder(media, tmp_path / "shared")
    clean = clean_build(media, tmp_path / "clean")
    done, errors = threading.Event(), []

    def scribble():
        while not done.is_set():
            folder.level.write_text('{"key": "x", "gain_')

    def mix():
        try:
            assembly.add_music(folder.narrated, folder.bed, folder.out)
        except BaseException as err:  # noqa: BLE001 - the test reports it
            errors.append(err)

    scribbler = threading.Thread(target=scribble)
    mixers = [threading.Thread(target=mix) for _ in range(2)]
    scribbler.start()
    for t in mixers:
        t.start()
    for t in mixers:
        t.join()
    done.set()
    scribbler.join()
    assert not errors, errors
    assert is_fresh(folder.out, assembly.mix_key(folder.narrated, folder.bed))
    assert pcm_hash(folder.out) == pcm_hash(clean.out)
    reset(calls)
    assembly.add_music(folder.narrated, folder.bed, folder.out)
    assert calls.encodes == [], "a reading scribbled on is read again, the film is not remixed"
    assert json.loads(folder.level.read_text())["key"] != "x"
