"""run_report.md for every pipeline run (P2-3): what it cost, how long each stage took, how each
scene ended up, what QA said and what to look at next. The first live film is where prices, model
names and response shapes meet reality; when it stops or a scene falls back, this is the one file
that says what happened.

Built only from files the run already wrote (ledger, manifest, failures.json, review logs, the
QA report it was handed) and from timings kept in memory: no network, no paid call. Written in a
`finally`, so a run that stops says where and why, and never lets a failure of its own hide the
error that stopped the run. Free text goes through utils.redact."""
import os
import shlex
import sys
import time
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

import config
import failures as failure_log
import ledger
from agents import ai_video, image_agent
from utils import atomic_output, brief_key, load_json, log, redact

FILE = "run_report.md"
PREVIOUS = "run_report.prev.md"  # the report this run replaced: one is kept
STAGES = ("script", "narration", "visuals", "scene clips", "assembly", "metadata", "QA")
PLAN_ORDER = ("ai_video", "still", "manim")  # best first: a scene fell back when it ended up later
USED = {"ai": "ai_video", "still": "still", "manim": "manim"}  # manifest kind -> script visual_type
FELL_BACK_TO = {"still": "a still", "manim": "Manim"}
RETRY, EDIT, AGAIN = ("`--retry-failed` asks again", "edit its visual_description in script.json for another picture",
                      "a rerun asks again")

_clock = time.perf_counter  # swapped in tests, for timings that can be compared
_current: "Run | None" = None


def _now() -> datetime:
    return datetime.now(timezone.utc)  # swapped in tests


def stage(name: str) -> None:
    """Mark the start of a pipeline stage; the previous one ends here. A no-op outside a run, so
    the functions that mark stages can be called on their own."""
    if _current is not None:
        _current.timeline.mark(name)


class _Timeline:
    """Wall-clock seconds per stage. The stages run one after another, so a stage lasts from its
    mark to the next mark, or to the end of the run: nothing has to be indented or wrapped, and the
    stage that was open when an error passed is the one the run stopped at."""

    def __init__(self):
        self.seconds: dict[str, float] = {}
        self.stopped_in: str | None = None
        self._open: str | None = None
        self._since = 0.0

    def mark(self, name: str) -> None:
        if name not in STAGES:
            raise ValueError(f"unknown stage {name!r}; the stages are {', '.join(STAGES)}")
        self.close()
        self._open, self._since = name, _clock()

    def close(self, stopped: bool = False) -> None:
        if self._open is not None:
            self.seconds[self._open] = self.seconds.get(self._open, 0.0) + _clock() - self._since
            if stopped:
                self.stopped_in = self._open
            self._open = None

    def finished(self, name: str) -> bool:
        return name in self.seconds and name != self.stopped_in


class Reason(NamedTuple):
    text: str
    hint: str | None = None
    veo_off: bool = False  # Veo was not allowed: a choice, not a problem


@dataclass
class SceneRow:
    id: int
    concept: str
    planned: str  # the script's visual_type, "?" if the script has no such scene
    kind: str  # the manifest's: ai / still / manim
    reasons: list[Reason] = field(default_factory=list)

    @property
    def used(self) -> str:
        return USED.get(self.kind, self.kind)

    @property
    def fell_back(self) -> bool:
        return self.planned in PLAN_ORDER and self.used in PLAN_ORDER and (
            PLAN_ORDER.index(self.used) > PLAN_ORDER.index(self.planned))


class Run:
    """What one run knows about itself. main() fills in `script`, `qa` and `outcome` as it goes;
    the rest of the report comes from the build folder."""

    def __init__(self, work: Path, run_id: str, topic: str, brief_path: Path | None, brief: str | None,
                 allow_veo: bool):
        self.work, self.run_id, self.topic = work, run_id, topic
        self.brief_path, self.brief, self.allow_veo = brief_path, brief, allow_veo
        self.argv = sys.argv[1:]
        self.script: dict | None = None
        self.qa: dict | None = None
        self.outcome: str | None = None  # "script only"; otherwise worked out from error and qa
        self.error: BaseException | None = None
        self.timeline = _Timeline()
        self.started, self._t0, self.total_s = _now(), _clock(), 0.0

    # --- writing it ---

    def write(self) -> None:
        """Never raises: a report that can't be written, or a console that has gone away (stdout
        piped to `head`), must not replace the error that stopped the run. The report this one
        replaces is kept as run_report.prev.md, so a refused or stopped rerun does not take the
        last finished run's QA and scene table with it."""
        try:
            text = self.render()  # first: if it fails, the old report stays where it is
            if (self.work / FILE).exists():
                os.replace(self.work / FILE, self.work / PREVIOUS)
            with atomic_output(self.work / FILE) as tmp:
                tmp.write_text(text, encoding="utf-8")
            message = f"• run report: {self.work / FILE}"
        except Exception as e:  # noqa: BLE001
            message = f"• run report: could not write the run report ({type(e).__name__}: {e})"
        try:
            log(message)
        except OSError:
            pass

    def render(self) -> str:
        scenes = self._scene_rows()
        parts = [self._header(), self._costs(), self._timings(), self._scenes(scenes), self._qa(),
                 self._next_steps(scenes)]
        return redact("\n".join(parts).rstrip() + "\n")

    # --- header ---

    def _stop(self) -> str | None:
        """Where and why the run stopped: 'visuals: ValueError: first line of the message'."""
        if self.error is None:
            return None
        message = redact(str(self.error)).strip().splitlines()
        first = message[0] if message else ""
        if len(first) > 300:
            first = first[:299] + "…"
        what = f"{type(self.error).__name__}: {first}" if first else type(self.error).__name__
        return f"{self.timeline.stopped_in or 'setup'}: {what}"

    def _outcome(self) -> str:
        if self.error is not None:
            return f"stopped at {self._stop()}"
        if self.outcome:
            return self.outcome
        return "finished, QA failed" if self.qa is not None and not self.qa.get("passed") else "finished"

    def _brief(self) -> str:
        if self.brief is None:
            return "none"
        where = f"`{self.brief_path}`" if self.brief_path is not None else f"kept copy `{self.work / config.BRIEF_FILE}`"
        return f"{where} (key {brief_key(self.brief)})"

    def _veo(self) -> str:
        if self.allow_veo:
            return "allowed"
        return f"not allowed: {self._veo_off()}"

    def _veo_off(self) -> str:
        return "--no-ai-video" if ai_video.available() else "no GOOGLE_API_KEY"

    def _header(self) -> str:
        title = (self.script or {}).get("title") or "not known yet"
        return "\n".join([
            f"# Run report: {self.topic}", "",
            f"- **Film:** {title}",
            f"- **Started:** {self.started:%Y-%m-%d %H:%M:%S} UTC",
            f"- **Outcome:** {self._outcome()}",
            *([f"- **Previous report:** `{PREVIOUS}` (the run before this one)"]
              if (self.work / FILE).exists() or (self.work / PREVIOUS).exists() else []),
            f"- **Command:** `python pipeline.py {shlex.join(self.argv)}`",
            f"- **Style:** {config.STYLE}",
            f"- **Brief:** {self._brief()}",
            f"- **Models:** Claude `{config.CLAUDE_MODEL}`, narration `{config.TTS_MODEL}`, "
            f"images `{config.IMAGE_MODEL}`, Veo `{config.VEO_MODEL}` ({self._veo()})", ""])

    # --- costs ---

    def _costs(self) -> str:
        mine, film = ledger.entries(run=self.run_id), ledger.entries(film=self.work.name)
        every = ledger.entries()
        lines = ["## Costs", "",
                 f"Estimates from the spend ledger (`{ledger.ledger_path()}`), not invoices. This run is "
                 f"`{self.run_id}`; its entries carry that id.", "",
                 "| Provider | Calls | This run | This film, all runs |", "|---|---:|---:|---:|"]
        for provider in sorted({e["provider"] for e in mine + film}):
            this = [e for e in mine if e["provider"] == provider]
            lines.append(f"| {provider} | {len(this)} | {_eur(this)} | "
                         f"{_eur([e for e in film if e['provider'] == provider])} |")
        lines.append(f"| Total | {len(mine)} | {_eur(mine)} | {_eur(film)} |")
        spent = ledger.spent_eur()
        lines += ["", f"Ledger total: €{spent:.3f} of €{config.BUDGET_EUR:.3f} (€{config.BUDGET_EUR - spent:.3f} left)."]
        unowned = [e for e in every if "film" not in e]
        if unowned:
            lines.append(f"Ledger entries with no film (made before P2-3 or outside a run): {len(unowned)} "
                         f"({_eur(unowned)}); they count in the ledger total only.")
        return "\n".join(lines) + "\n"

    # --- timings ---

    def _timings(self) -> str:
        lines = ["## Timings", "", "| Stage | Time |", "|---|---:|"]
        for name in STAGES:
            if name not in self.timeline.seconds:
                lines.append(f"| {name} | not reached |")
            else:
                here = " (stopped here)" if name == self.timeline.stopped_in else ""
                lines.append(f"| {name} | {self.timeline.seconds[name]:.1f} s{here} |")
        lines.append(f"| Total | {self.total_s:.1f} s |")
        return "\n".join(lines) + "\n"

    # --- scenes ---

    def _scene_rows(self) -> list[SceneRow] | None:
        """The scenes as built, or None. The manifest is read only when this run wrote it (the
        scene clips stage finished): after a stop, the one on disk is an earlier run's."""
        if not self.timeline.finished("scene clips"):
            return None
        manifest = _load(self.work / "manifest.json")
        if manifest is None:
            return None
        script = self.script or _load(self.work / "script.json") or {}
        planned = {s["id"]: s for s in script.get("scenes", [])}
        recorded = _load(failure_log.path(self.work)) or {}
        rows = []
        for m in manifest["scenes"]:
            scene = planned.get(m["id"])
            row = SceneRow(m["id"], m["concept"], scene["visual_type"] if scene else "?", m["kind"])
            if scene and row.fell_back:
                row.reasons = self._reasons(scene, row, recorded)
            rows.append(row)
        return rows

    def _reasons(self, scene: dict, row: SceneRow, recorded: dict) -> list[Reason]:
        """Why the scene did not get what the script planned, from what the pipeline left on disk.
        Transient errors, the Veo daily cap and a failed review call are not recorded (D19), so a
        scene with nothing on file says so instead of guessing."""
        visuals = self.work / "visuals"
        try:
            out = []
            if row.planned == "ai_video":
                failed = recorded.get(ai_video.cache_path(scene, visuals).stem)
                if not self.allow_veo:
                    out.append(Reason(f"Veo not allowed ({self._veo_off()})", veo_off=True))
                elif failed:
                    out.append(Reason(f"Veo failed: {failed['error']}", RETRY))
                else:
                    out.append(Reason("Veo: no failure on file (a transient error or the daily cap, which "
                                      "are not recorded; see the console log)", AGAIN))
            if row.used == "manim":
                failed = recorded.get(image_agent.next_still(scene, visuals).stem)
                review = image_agent.review_log(scene, visuals)
                if not image_agent.available():
                    out.append(Reason("no image model (no GOOGLE_API_KEY)"))
                elif failed:
                    out.append(Reason(f"image model failed: {failed['error']}", RETRY))
                elif review and review.get("done") and review["accepted"] is None and review["attempts"]:
                    problems = "; ".join(review["attempts"][-1]["problems"])
                    out.append(Reason(f"still rejected {len(review['attempts'])} times: {problems}", EDIT))
                else:
                    out.append(Reason("image model: no failure or rejection on file (a failed review call "
                                      "is not recorded; see the console log)", AGAIN))
            return out
        except (OSError, ValueError, KeyError, TypeError) as e:
            return [Reason(f"reason unreadable ({type(e).__name__})")]

    def _scenes(self, rows: list[SceneRow] | None) -> str:
        if rows is None:
            return "## Scenes\n\nNot reached: this run built no scene clips.\n"
        used = Counter(r.kind for r in rows)
        falls = Counter((r.planned, r.used) for r in rows if r.fell_back)
        by_plan = sorted(falls.items(), key=lambda kv: [PLAN_ORDER.index(x) for x in kv[0]])
        fallbacks = "; ".join(f"{a} → {b}: {n}" for (a, b), n in by_plan) or "none"
        lines = ["## Scenes", "",
                 f"Used: {', '.join(f'{used[k]} {k}' for k in USED if used[k])}. Fallbacks: {fallbacks}.",
                 "", "| Scene | Concept | Planned | Used | Note |", "|---:|---|---|---|---|"]
        for r in rows:
            note = "; ".join(x.text for x in r.reasons)
            lines.append(f"| {r.id} | {_cell(r.concept)} | {_cell(r.planned)} | {_cell(r.kind)} | {_cell(note, 240)} |")
        return "\n".join(lines) + "\n"

    # --- QA ---

    def _qa(self) -> str:
        if self.qa is None:
            return "## QA\n\nNot run.\n"
        lines = ["## QA", "", f"{'PASSED' if self.qa.get('passed') else 'FAILED'} `{self.qa.get('video', '')}`"]
        checks = self.qa.get("checks", {})
        if checks:
            lines += ["", "| Check | Result | Value | Target | Detail |", "|---|---|---|---|---|"]
            for name, c in checks.items():
                value = "n/a" if c.get("value") is None else c["value"]
                lines.append(f"| {_cell(name)} | {'ok' if c['passed'] else 'FAIL'} | {_cell(value)} | "
                             f"{_cell(c.get('target', ''))} | {_cell(c.get('detail', ''))} |")
        return "\n".join(lines) + "\n"

    # --- next steps ---

    def _next_steps(self, rows: list[SceneRow] | None) -> str:
        steps = []
        if isinstance(self.error, KeyboardInterrupt):
            steps.append(f"Stopped by Ctrl-C at {self.timeline.stopped_in or 'setup'}; rerun the same command "
                         "to resume (nothing finished is bought again)")
        elif self.error is not None:
            steps.append(f"Stopped at {self._stop()}; see the error above (finished work stays cached)")
        elif self.outcome == "script only":
            steps.append("Review and edit script.json, then rerun without --script-only.")
        for name, c in (self.qa or {}).get("checks", {}).items():
            if not c["passed"]:
                value = "n/a" if c.get("value") is None else c["value"]
                detail = f"; {c['detail']}" if c.get("detail") else ""
                steps.append(f"QA check `{name}` failed: {value} (target {c.get('target', '')}{detail})")
        for r in rows or []:
            why = [x for x in r.reasons if not x.veo_off]
            if r.fell_back and why:
                hints = list(dict.fromkeys(x.hint for x in why if x.hint))
                steps.append(f"Scene {r.id} fell back to {FELL_BACK_TO.get(r.used, r.used)}: "
                             + "; ".join([x.text for x in why] + hints))
        off = sum(1 for r in rows or [] if any(x.veo_off for x in r.reasons))
        if off:
            fix = ("rerun without it to use Veo (it costs money)" if ai_video.available()
                   else "add the key to .env to use Veo (it costs money)")
            steps.append(f"{off} ai_video scene{'s' if off != 1 else ''} did not get Veo because it was "
                         f"not allowed ({self._veo_off()}); {fix}")
        if not steps:
            return "## Next steps\n\nNone: the run finished, QA passed and no scene fell back.\n"
        return "\n".join(["## Next steps", ""] + [f"- {_line(s)}." for s in steps]) + "\n"


def _load(path: Path):
    """A JSON file, or None when it is missing or unreadable: the report is for the runs that
    went wrong, and a corrupt file may be what went wrong."""
    try:
        return load_json(path)
    except (OSError, ValueError):
        return None


def _eur(entries: list[dict]) -> str:
    return f"€{sum(e['est_cost_eur'] for e in entries):.3f}"


def _line(text, limit: int = 600) -> str:
    """Free text on one line, redacted before it is cut so a key can't be left half-visible."""
    text = " ".join(redact(str(text)).split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _cell(text, limit: int = 200) -> str:
    """One table cell: on one line, `|` escaped."""
    return _line(text, limit).replace("|", "\\|")


@contextmanager
def recording(work: Path, topic: str, brief_path: Path | None, brief: str | None,
              allow_veo: bool) -> Iterator[Run]:
    """Attribute the run's ledger entries to this film, time its stages and write the report when
    the block ends, however it ends (an error, Ctrl-C, a normal return)."""
    global _current
    with ledger.attribute_to(work.name) as run_id:
        run = _current = Run(work, run_id, topic, brief_path, brief, allow_veo)
        try:
            yield run
        except BaseException as e:
            run.error = e
            raise
        finally:
            _current = None
            run.timeline.close(stopped=run.error is not None)
            run.total_s = _clock() - run._t0
            run.write()
