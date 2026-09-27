import hashlib
import json
import os
import re
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
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


def video_duration(path: str | Path) -> float:
    """Length of the video stream alone. The container duration also counts the audio,
    which AAC pads to a whole 1024-sample frame."""
    out = run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=duration",
               "-of", "json", str(path)]).stdout
    return float(json.loads(out)["streams"][0]["duration"])


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60]


def save_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False))


def load_json(path: Path):
    return json.loads(path.read_text())


# --- Caching -----------------------------------------------------------------
# A cached file is valid only for the exact inputs that produced it. Per-scene files
# are named by that key (so renumbered scenes still find their own files); fixed-name
# outputs such as narrated.mp4 carry the key in a "<name>.key" stamp next to them.

def content_key(*parts) -> str:
    """Short stable hash of JSON-serialisable inputs."""
    blob = json.dumps(parts, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def file_hash(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


@contextmanager
def atomic_output(out: Path) -> Iterator[Path]:
    """Yield a temporary path next to `out` and move it into place only if the block
    succeeds, so a crash never leaves a half-written file that later looks cached."""
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(f"{out.stem}.partial{out.suffix}")
    try:
        yield tmp
        os.replace(tmp, out)
    finally:
        tmp.unlink(missing_ok=True)


def _stamp(out: Path) -> Path:
    return out.with_name(out.name + ".key")


def is_fresh(out: Path, key: str) -> bool:
    """True if `out` exists and was built from inputs with this key."""
    stamp = _stamp(out)
    return out.exists() and stamp.exists() and stamp.read_text().strip() == key


@contextmanager
def stamped_output(out: Path, key: str) -> Iterator[Path]:
    """Rebuild a fixed-name output: drop the old stamp first, write atomically,
    then record the new key. A crash at any point leaves no stamp that lies."""
    _stamp(out).unlink(missing_ok=True)
    with atomic_output(out) as tmp:
        yield tmp
    _stamp(out).write_text(key)
