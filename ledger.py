"""Spend ledger and hard budget cap.

Every paid call is checked against BUDGET_EUR before it is made and appended to
build/ledger.jsonl after it succeeds. The ledger is shared by all videos, so the cap is a
total across runs: raise BUDGET_EUR (or archive the ledger) to allow more spend."""
import json
import threading
from datetime import datetime, timezone
from pathlib import Path

import config

_lock = threading.Lock()


class BudgetExceeded(Exception):
    """A paid call would take total spend past BUDGET_EUR. The fallback chain must never
    swallow this: the run stops."""


def ledger_path() -> Path:
    return Path(config.BUILD_DIR) / "ledger.jsonl"


def spent_eur() -> float:
    path = ledger_path()
    if not path.exists():
        return 0.0
    return sum(json.loads(line)["est_cost_eur"] for line in path.read_text().splitlines() if line.strip())


def check(provider: str, est_cost_eur: float) -> None:
    spent = spent_eur()
    if spent + est_cost_eur > config.BUDGET_EUR:
        raise BudgetExceeded(
            f"{provider} call (~€{est_cost_eur:.3f}) would take spend to €{spent + est_cost_eur:.2f}, "
            f"over BUDGET_EUR €{config.BUDGET_EUR:.2f} ({ledger_path()}). Stopping.")


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
