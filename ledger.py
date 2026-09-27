"""Spend ledger and hard budget cap.

Every paid call is checked against BUDGET_EUR before it is made and appended to
build/ledger.jsonl after it succeeds. The ledger is shared by all videos, so the cap is a
total across runs: raise BUDGET_EUR (or archive the ledger) to allow more spend."""
import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import config

_lock = threading.RLock()  # re-entrant: reserve() holds it while check() reads the ledger
stopping = threading.Event()  # set when a parallel run fails: no new paid call may start (P1-5)
interrupted = threading.Event()  # set on Ctrl-C: jobs in flight give up too (a Veo poll)


class BudgetExceeded(Exception):
    """A paid call would take total spend past BUDGET_EUR. The fallback chain must never
    swallow this: the run stops."""


def ledger_path() -> Path:
    return Path(config.BUILD_DIR) / "ledger.jsonl"


def _entries() -> list[dict]:
    with _lock:  # never read a line another thread is halfway through writing
        path = ledger_path()
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []


def spent_eur() -> float:
    return sum(e["est_cost_eur"] for e in _entries())


def veo_clips_today() -> int:
    """Veo clips recorded today (UTC), for CLAUDE.md's daily limit. A Veo entry is the only
    one billed in seconds."""
    today = datetime.now(timezone.utc).date().isoformat()
    return sum(1 for e in _entries() if e["provider"] == "google" and "seconds" in e["units"]
               and e["time"].startswith(today))


_held = 0.0  # cost reserved by calls in flight (P1-5: several can run at once)


def check(provider: str, est_cost_eur: float, held: float = 0.0) -> None:
    spent = spent_eur()
    if spent + held + est_cost_eur > config.BUDGET_EUR:
        raise BudgetExceeded(
            f"{provider} call (~€{est_cost_eur:.3f}) would take spend to €{spent + held + est_cost_eur:.2f}, "
            f"over BUDGET_EUR €{config.BUDGET_EUR:.2f} ({ledger_path()}). Stopping.")


@contextmanager
def reserve(provider: str, est_cost_eur: float) -> Iterator[None]:
    """Hold this call's estimated cost for as long as it runs, checked against what is spent
    plus what other calls in flight already hold, in one step under the lock. The call
    records its actual cost before the hold is released, so the cap holds under concurrency."""
    global _held
    with _lock:
        if stopping.is_set():
            raise BudgetExceeded(f"{provider} call refused: the run is stopping after an earlier failure")
        check(provider, est_cost_eur, _held)
        _held += est_cost_eur
    try:
        yield
    finally:
        with _lock:
            _held -= est_cost_eur


def record(provider: str, model: str, units: dict, est_cost_eur: float) -> None:
    entry = {"time": datetime.now(timezone.utc).isoformat(timespec="seconds"), "provider": provider,
             "model": model, "units": units, "est_cost_eur": round(est_cost_eur, 6)}
    with _lock:
        ledger_path().parent.mkdir(parents=True, exist_ok=True)
        with ledger_path().open("a") as f:
            f.write(json.dumps(entry) + "\n")


# --- Prices (all estimates; config.py holds the numbers, marked "verify") ------

def claude_eur(model: str, input_tokens: int, output_tokens: int) -> float:
    usd_in, usd_out = config.CLAUDE_USD_PER_MTOK.get(model, config.CLAUDE_USD_PER_MTOK_UNLISTED)
    return (input_tokens * usd_in + output_tokens * usd_out) / 1e6 * config.USD_TO_EUR


def tts_eur(chars: int) -> float:
    return chars / 1000 * config.TTS_USD_PER_1K_CHARS * config.USD_TO_EUR


def image_eur() -> float:
    return config.IMAGE_USD_PER_IMAGE[config.IMAGE_SIZE] * config.USD_TO_EUR


def video_eur(seconds: float) -> float:
    return seconds * config.VEO_USD_PER_SECOND * config.USD_TO_EUR


def claude_input_estimate(*texts: str) -> int:
    """Tokens a request will likely use, from its text; errs high."""
    return sum(len(t) for t in texts) // config.EST_CHARS_PER_TOKEN + 1
