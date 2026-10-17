"""Check every provider before money is spent (P3-6): a blocked host, a wrong model name, a bad
key, a free-tier key, a missing FFmpeg filter or a full disk, for (almost) nothing.

    python scripts/preflight.py                 # free, plus one 16-token Claude call (~EUR 0.0005)
    python scripts/preflight.py --image         # ...and one 1K picture (~EUR 0.06): a free-tier key is refused here
    python scripts/preflight.py --tts-sample    # ...and one narrated line (~EUR 0.015): prints its pace

One line per check, `PASS|WARN|SKIP|FAIL <check>: <detail>`, then a summary line; the exit code is 0
only if no check FAILed. Nothing here retries for minutes: a probe has a short timeout and one attempt.
Errors are redacted (utils.redact), so no key is printed. The only files written are the ledger
(every paid call is reserved and recorded as in a run) and build/preflight/ (the sample picture and
narration)."""
import argparse
import re
import shutil
import subprocess
import sys
from collections import Counter
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import config  # noqa: E402
import ledger  # noqa: E402
from agents import google_client, image_agent, llm, voice  # noqa: E402
from utils import duration, log, redact  # noqa: E402

HOSTS = ("api.anthropic.com", "api.elevenlabs.io", "generativelanguage.googleapis.com")
ELEVENLABS = "https://api.elevenlabs.io"
FILTERS = ("loudnorm", "alimiter", "sidechaincompress", "xfade", "acrossfade", "ebur128")
ENCODER = "libx264"
SAMPLE_PICTURE = "A calm, dark night sky with a few faint stars over a quiet horizon"
Result = tuple[str, str]  # (status, detail)


def describe(e: BaseException) -> str:
    """One redacted line for an error: its type and text."""
    return redact(f"{type(e).__name__}: {' '.join(str(e).split())}")[:300]


class Report:
    """Prints each check's line as it finishes and counts the outcomes."""

    def __init__(self):
        self.counts: Counter[str] = Counter()

    def add(self, status: str, name: str, detail: str) -> None:
        self.counts[status] += 1
        log(f"{status} {name}: {detail}")

    def run(self, name: str, fn: Callable[[], Result], needs: tuple[str, ...] = ()) -> str:
        """A check: FAIL if a key it needs is missing, else whatever `fn` returns. An error is a
        FAIL, never a crash. Exception only: Ctrl-C (and the offline tests' guard) propagate."""
        missing = [k for k in needs if not getattr(config, k)]
        try:
            status, detail = ("FAIL", f"missing {', '.join(missing)} (set it in .env)") if missing else fn()
        except ledger.BudgetExceeded as e:
            status, detail = "FAIL", f"budget: {e}"
        except Exception as e:
            status, detail = "FAIL", describe(e)
        self.add(status, name, detail)
        return status


# --- Hosts ----------------------------------------------------------------------

def _host(host: str) -> Result:
    """Reachable with the environment's proxy settings: any HTTP status counts (the host answered).
    A refused tunnel (ProxyError), a connection error or a timeout raises, and is a FAIL."""
    url = f"https://{host}/"
    r = requests.get(url, timeout=config.PREFLIGHT_TIMEOUT_S, allow_redirects=False)
    proxies = requests.utils.get_environ_proxies(url)
    return "PASS", f"HTTP {r.status_code} ({'via proxy' if proxies.get('https') or proxies.get('all') else 'direct'})"


def check_hosts(report: Report) -> None:
    for host in HOSTS:
        report.run(f"host {host}", lambda host=host: _host(host))


# --- Claude ---------------------------------------------------------------------

def _anthropic_client():
    """No retries and a short timeout: a probe that fails should fail now (D11)."""
    import anthropic
    return anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY, max_retries=0, timeout=config.PREFLIGHT_TIMEOUT_S)


def check_claude(report: Report) -> None:
    client, model_missing = None, False

    def model() -> Result:
        nonlocal client, model_missing
        client = _anthropic_client()
        try:
            info = client.models.retrieve(config.CLAUDE_MODEL)
        except Exception as e:
            if getattr(e, "status_code", None) == 404:
                model_missing = True
                return "FAIL", f"model {config.CLAUDE_MODEL} not found (404); fix CLAUDE_MODEL in .env"
            raise
        name = getattr(info, "display_name", None)
        return "PASS", f"{config.CLAUDE_MODEL} exists" + (f" ({name})" if name else "")

    def call() -> Result:
        # llm._create, not llm.structured: structured raises on max_tokens, which is the expected
        # stop here. It reserves and records in the ledger like every Claude call (D10).
        resp = llm._create(
            client=_anthropic_client() if client is None else client, max_tokens=16,
            system="Preflight check. Answer by calling the tool.",
            messages=[{"role": "user", "content": "Call the tool."}],
            tools=[{"name": "ping", "description": "Return ok.", "input_schema": {"type": "object", "properties": {}}}],
            tool_choice={"type": "tool", "name": "ping"})
        usage = resp.usage
        return "PASS", (f"200, stop_reason {resp.stop_reason}, {usage.input_tokens} in / {usage.output_tokens} out "
                        f"(forced tool call, max_tokens 16)")

    needs = ("ANTHROPIC_API_KEY",)
    report.run("claude model", model, needs)
    if model_missing:
        report.add("SKIP", "claude call", "the model was not found")
    else:
        report.run("claude call", call, needs)


# --- ElevenLabs -----------------------------------------------------------------

def _eleven_get(path: str):
    return requests.get(ELEVENLABS + path, headers={"xi-api-key": config.ELEVENLABS_API_KEY},
                        timeout=config.PREFLIGHT_TIMEOUT_S)


def _eleven_detail(r) -> tuple[str, str]:
    """(status code word, message) of an ElevenLabs error body: {"detail": {"status", "message"}},
    {"detail": "text"} or anything else."""
    try:
        detail = r.json()["detail"]
    except Exception:
        return "", " ".join(str(getattr(r, "text", "")).split())[:200]
    if isinstance(detail, dict):
        return str(detail.get("status", "")), " ".join(str(detail.get("message", "")).split())[:200]
    return "", " ".join(str(detail).split())[:200]


def _eleven_error(r) -> str:
    word, message = _eleven_detail(r)
    return f"HTTP {r.status_code}" + (f" {word}" if word else "") + (f": {message}" if message else "")


def check_elevenlabs(report: Report) -> None:
    def models() -> Result:
        r = _eleven_get("/v1/models")
        if r.status_code != 200:
            return "FAIL", _eleven_error(r)
        body = r.json()
        if not isinstance(body, list):
            return "FAIL", f"unexpected answer: {str(body)[:100]}"
        ids = [m.get("model_id") for m in body]
        if config.TTS_MODEL not in ids:
            return "FAIL", f"model {config.TTS_MODEL} is not listed (fix TTS_MODEL in .env); listed: {', '.join(map(str, ids))}"
        return "PASS", f"{config.TTS_MODEL} is listed"

    def voice_() -> Result:
        r = _eleven_get(f"/v1/voices/{config.ELEVENLABS_VOICE_ID}")
        if r.status_code == 200:
            return "PASS", f"voice {config.ELEVENLABS_VOICE_ID} is {r.json().get('name', '?')!r}"
        if r.status_code == 404 or _eleven_detail(r)[0] == "voice_not_found":
            return "FAIL", f"voice {config.ELEVENLABS_VOICE_ID} not found ({_eleven_error(r)}); fix ELEVENLABS_VOICE_ID"
        return "FAIL", _eleven_error(r)

    def subscription() -> Result:
        r = _eleven_get("/v1/user/subscription")
        if r.status_code == 401 and _eleven_detail(r)[0] == "missing_permissions":
            return "FAIL", "key lacks user_read; TTS may still work"
        if r.status_code != 200:
            return "FAIL", _eleven_error(r)
        body = r.json()
        tier, limit, used = body.get("tier", "?"), body.get("character_limit"), body.get("character_count")
        left = f"{limit - used:,} of {limit:,} characters left" if isinstance(limit, int) and isinstance(used, int) \
            else "characters left unknown"
        if tier == "free":
            return "WARN", f"plan free, {left}; the free plan may refuse some voices over the API"
        return "PASS", f"plan {tier}, {left}"

    key = ("ELEVENLABS_API_KEY",)
    report.run("elevenlabs models", models, key)
    report.run("elevenlabs voice", voice_, key + ("ELEVENLABS_VOICE_ID",))
    report.run("elevenlabs subscription", subscription, key)


# --- Google ---------------------------------------------------------------------

def check_google(report: Report) -> None:
    def model(name: str, setting: str) -> Result:
        from google.genai import types

        # Per request: one attempt and a short timeout, whatever the shared client is set to (D11).
        probe = types.GetModelConfig(http_options=types.HttpOptions(
            timeout=int(config.PREFLIGHT_TIMEOUT_S * 1000), retry_options=types.HttpRetryOptions(attempts=1)))
        try:
            info = google_client.make().models.get(model=name, config=probe)
        except Exception as e:
            if getattr(e, "code", None) == 404:
                return "FAIL", f"model {name} not found (404); fix {setting} in .env"
            raise
        shown = getattr(info, "display_name", None)
        return "PASS", f"{name} exists" + (f" ({shown})" if shown else "")

    report.run("google image model", lambda: model(config.IMAGE_MODEL, "IMAGE_MODEL"), ("GOOGLE_API_KEY",))
    report.run("google veo model", lambda: model(config.VEO_MODEL, "VEO_MODEL"), ("GOOGLE_API_KEY",))


# --- Local ----------------------------------------------------------------------

def _tool(cmd: list[str]) -> tuple[int, str, str]:
    """(exit code, stdout, stderr) of a local command."""
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except FileNotFoundError:
        raise RuntimeError(f"{cmd[0]} is not installed (not on PATH)") from None
    return done.returncode, done.stdout, done.stderr


_OPTION = r"^\s+{name}\s+<"


def _option_block(help_text: str, name: str) -> list[str] | None:
    """The help lines of one AVOption and of its named values (the lines under it, up to the next
    option), or None if the filter has no such option. An option line looks like
    `   peak   <flags>   ..F.A...... set peak mode`; a value line has no `<type>`."""
    lines = help_text.splitlines()
    for i, line in enumerate(lines):
        if re.match(_OPTION.format(name=re.escape(name)), line):
            block = []
            for nxt in lines[i + 1:]:
                if re.match(r"^\s+\S+\s+<", nxt) or not nxt.strip():
                    break
                block.append(nxt)
            return block
    return None


def _filter(name: str) -> Result:
    # `ffmpeg -h filter=X` exits 0 even for a filter that doesn't exist: only its text says (FFmpeg 6.1.1
    # prints "Unknown filter 'X'." and a header `Filter X` for a real one).
    _, out, _ = _tool(["ffmpeg", "-hide_banner", "-h", f"filter={name}"])
    if not re.search(rf"^Filter {re.escape(name)}\s*$", out, re.M):
        return "FAIL", f"this FFmpeg has no filter {name} ({(out.strip().splitlines() or ['no output'])[0][:100]})"
    if name == "alimiter" and _option_block(out, "latency") is None:
        return "FAIL", "alimiter has no `latency` option (assembly.py needs it): FFmpeg is too old"
    if name == "ebur128":
        values = _option_block(out, "peak")
        if values is None:
            return "FAIL", "ebur128 has no `peak` option: FFmpeg is too old"
        if not any(line.split()[:1] == ["true"] for line in values):
            return "FAIL", "ebur128's `peak` option has no `true` value (true-peak metering)"
    return "PASS", "present" + (" with latency" if name == "alimiter" else " with peak=true" if name == "ebur128" else "")


def _encoder(name: str) -> Result:
    _, out, _ = _tool(["ffmpeg", "-hide_banner", "-h", f"encoder={name}"])
    if not re.search(rf"^Encoder {re.escape(name)} \[", out, re.M):
        return "FAIL", f"this FFmpeg has no encoder {name} ({(out.strip().splitlines() or ['no output'])[0][:100]})"
    return "PASS", "present"


def _manim() -> Result:
    code, out, err = _tool(["manim", "--version"])
    first = next((line.strip() for line in (out + "\n" + err).splitlines() if line.strip()), "no output")
    return ("PASS", first) if code == 0 else ("FAIL", f"manim --version exited {code}: {first[:150]}")


def _disk() -> Result:
    path = Path(config.BUILD_DIR)
    while not path.exists() and path != path.parent:  # build/ may not exist yet: measure the disk it will be on
        path = path.parent
    free_gb = shutil.disk_usage(path).free / 1e9
    if free_gb < config.PREFLIGHT_MIN_FREE_GB:
        return "FAIL", f"only {free_gb:.1f} GB free at {path}; a film needs {config.PREFLIGHT_MIN_FREE_GB} GB (PREFLIGHT_MIN_FREE_GB)"
    return "PASS", f"{free_gb:.1f} GB free at {path} (need {config.PREFLIGHT_MIN_FREE_GB})"


def check_local(report: Report) -> None:
    for name in FILTERS:
        report.run(f"ffmpeg filter {name}", lambda name=name: _filter(name))
    report.run(f"ffmpeg encoder {ENCODER}", lambda: _encoder(ENCODER))
    report.run("manim", _manim)
    report.run("disk", _disk)


# --- Paid probes ----------------------------------------------------------------

@contextmanager
def _no_retries():
    """A paid probe makes one attempt: the helpers read MAX_RETRIES per call (retries.call, the
    Google client's retry options), so a 429 or a 5xx reports at once instead of waiting minutes."""
    saved = config.MAX_RETRIES
    config.MAX_RETRIES = 0
    try:
        yield
    finally:
        config.MAX_RETRIES = saved


def out_dir() -> Path:
    return Path(config.BUILD_DIR) / "preflight"


def _spent() -> float | None:
    try:
        return ledger.spent_eur()
    except Exception:  # an unreadable ledger is the paid probes' FAIL to report, not a crash here
        return None


def _recorded(before: float | None) -> str:
    after = _spent()
    return f"€{after - before:.4f} recorded" if before is not None and after is not None else "recorded"


def probe_image() -> Result:
    """One 1K picture through image_agent's request path. Never cached: each run buys one. A free-tier
    or unbilled key is refused here and nowhere in the free probes."""
    out = out_dir() / "image.png"
    scene = {"id": 0, "concept": "preflight", "visual_description": SAMPLE_PICTURE}
    before = _spent()
    with _no_retries(), ledger.reserve("google", ledger.image_eur()):
        image_agent._buy(scene, out)  # records its own ledger entry
    return "PASS", f"{out} ({out.stat().st_size / 1024:.0f} KB); {_recorded(before)}"


def probe_tts(pace: dict) -> Result:
    """One fixed calm line through voice.narrate. Cached by its text, so a rerun is free. Leaves
    the words per minute in `pace`."""
    folder = out_dir()
    cached = voice.cache_path(voice.SAMPLE_LINE, folder).exists()
    before = _spent()
    with _no_retries():
        mp3 = voice.narrate(voice.SAMPLE_LINE, folder)  # reserves and records
    try:
        seconds = duration(mp3)
    except Exception as e:
        return "FAIL", f"{mp3} was made but its length could not be read: {describe(e)}"
    words = len(voice.SAMPLE_LINE.split())
    pace["wpm"] = wpm = round(words / (seconds / 60))  # the number shown is the number judged
    bought = "cached, nothing bought" if cached else _recorded(before)
    return "PASS", f"{mp3}, {seconds:.1f} s, {words} words, {wpm:.0f} words per minute; {bought}"


# --- Main -----------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Check every provider before money is spent. Free, except one "
                                 "16-token Claude call (~EUR 0.0005) and the options below.")
    ap.add_argument("--image", action="store_true",
                    help="also buy one 1K picture (~EUR 0.06) to build/preflight/image.png: catches a free-tier or "
                         "unbilled Google key. Buys a new one every time")
    ap.add_argument("--tts-sample", action="store_true",
                    help="also narrate one fixed calm line (<= EUR 0.02) to build/preflight/: prints its length and "
                         "words per minute (WARN outside 100-160). Cached, so a rerun is free")
    args = ap.parse_args(argv)

    report = Report()
    started = _spent()
    check_hosts(report)
    check_claude(report)
    check_elevenlabs(report)
    check_google(report)
    check_local(report)
    if args.image:
        report.run("image", probe_image, ("GOOGLE_API_KEY",))
    if args.tts_sample:
        pace: dict = {}
        report.run("tts sample", lambda: probe_tts(pace), ("ELEVENLABS_API_KEY", "ELEVENLABS_VOICE_ID"))
        low, high = config.PREFLIGHT_WPM_OK
        if "wpm" in pace and not low <= pace["wpm"] <= high:
            report.add("WARN", "tts pace", f"{pace['wpm']:.0f} words per minute is outside {low}-{high}; scripts are "
                       f"sized at WORDS_PER_MIN={config.WORDS_PER_MIN} (D21): listen to the file")

    n = report.counts
    ended = _spent()
    spent = f"; spent €{ended - started:.4f} this run" if started is not None and ended is not None else ""
    log(f"preflight: {n['PASS']} PASS, {n['WARN']} WARN, {n['SKIP']} SKIP, {n['FAIL']} FAIL{spent}"
        + (" -- fix the FAIL lines before a paid run" if n["FAIL"] else " -- ready"))
    return 1 if n["FAIL"] else 0


if __name__ == "__main__":
    sys.exit(main())
