"""Paid generations that failed, by content key (P1-7). A Veo clip or a still whose request
failed for good is recorded in build/<slug>/failures.json, so a rerun goes straight to the
fallback instead of paying, or waiting up to 10 minutes, for the same failure again.
`--retry-failed` asks again.

Recorded: errors about the request itself (a safety filter, an invalid prompt, no image or
video returned, a Veo job that never finished). Not recorded (D19): transient errors (network,
rate limits, server errors; `retries.is_transient`), errors of this machine (disk, FFmpeg), a
budget stop, the Veo daily cap, Ctrl-C, and a still Claude rejected (P1-2's review log keeps
that decision)."""
import threading
from datetime import datetime, timezone
from pathlib import Path

import httpx

import ledger
import retries
from utils import CommandFailed, load_json, redact, save_json


class FailedEarlier(RuntimeError):
    """This generation failed on an earlier run; the caller falls back as if it just had."""


def path(work: Path) -> Path:
    return work / "failures.json"


def lasting(e: BaseException) -> bool:
    """True if the error is about the request itself, so asking again would fail again."""
    return not (retries.is_transient(e) or isinstance(e, OSError | CommandFailed | httpx.TransportError))


class Failures:
    def __init__(self, path: Path, retry: bool = False):
        self.path, self.retry = path, retry
        self._lock = threading.Lock()
        self._recorded: set[str] = set()  # failed during this run: not asked again even when retrying

    def _load(self) -> dict:
        return load_json(self.path) if self.path.exists() else {}

    def earlier(self, key: str) -> dict | None:
        """The recorded failure of this generation, unless this run retries failures (a
        failure recorded by this same run still counts: two scenes sharing it pay once)."""
        with self._lock:
            if self.retry and key not in self._recorded:
                return None
            return self._load().get(key)

    def check(self, key: str) -> None:
        """Raise FailedEarlier if this generation failed before."""
        entry = self.earlier(key)
        if entry:
            raise FailedEarlier(f"failed earlier ({entry['error'][:100]}); --retry-failed to try again")

    def record(self, key: str, what: str, scene_id, error: BaseException) -> None:
        if ledger.interrupted.is_set() or not lasting(error):  # Ctrl-C: nothing is known about the prompt
            return
        with self._lock:
            data = self._load()
            data[key] = {"what": what, "scene": scene_id,
                         "error": redact(f"{type(error).__name__}: {error}")[:500],  # redacted before cut
                         "time": datetime.now(timezone.utc).isoformat(timespec="seconds")}
            save_json(self.path, data)
            self._recorded.add(key)

    def clear(self, key: str) -> None:
        with self._lock:
            data = self._load()
            if data.pop(key, None) is not None:
                save_json(self.path, data)
