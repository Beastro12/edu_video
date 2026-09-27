import json
import re
import subprocess
from pathlib import Path


def run(cmd: list[str], cwd: str | None = None) -> subprocess.CompletedProcess:
    """Run a command; raise with stderr attached so failures are diagnosable."""
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"Command failed: {' '.join(cmd)}\n--- stderr (tail) ---\n{result.stderr[-3000:]}"
        )
    return result


def duration(path: str | Path) -> float:
    out = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
               "-of", "json", str(path)]).stdout
    return float(json.loads(out)["format"]["duration"])


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60]


def save_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False))


def load_json(path: Path):
    return json.loads(path.read_text())
