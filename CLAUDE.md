# CLAUDE.md — edu_video

You are the sole engineer on this project and work unattended. Pietro (the owner) is
usually not available. Read this file fully at the start of every session.

## What this is

A Python pipeline where several AI agents produce a calm, sleep-friendly science
documentary (10–20 min, English). Reference style: the YouTube channel SleepOnPhysics.
Soft narration, slow visuals, low instrumental music ducked under the voice.

Flow (`pipeline.py`): outline agent → chapter writer → critic (per chapter) → ElevenLabs
narration per scene → visual per scene (Imagen still with slow drift / Manim diagram /
Veo clip, with fallbacks) → FFmpeg assembly (crossfades, music bed, ducking, −16 LUFS).

| File | Role |
|---|---|
| `pipeline.py` | Orchestrator, caching in `build/<slug>/` |
| `agents/script_agent.py`, `agents/critic.py` | Outline, chapters, review (Claude, forced tool use via `agents/llm.py`) |
| `agents/voice.py` | ElevenLabs REST |
| `agents/image_agent.py`, `agents/ai_video.py` | Google Imagen / Veo |
| `agents/manim_agent.py` | Claude writes Manim code; render errors fed back, up to 3 tries |
| `agents/music.py` | Seamless music bed from `music/` |
| `assembly.py` | All FFmpeg work |
| `config.py` | Every tunable |
| `docs/DECISIONS.md` | Why things are the way they are. Read before changing design |

## Commands

- `make check` — lint + offline tests. Free. **Must pass before every commit.**
- `make smoke` — live tests (`-m live`). Costs money. Only under the budget rules below.
- `python pipeline.py "<topic>" --minutes 15 [--script-only] [--no-ai-video]`

## The iteration loop (do this every time)

1. Read the last entry of `docs/PROGRESS.md`, then `docs/BACKLOG.md`.
2. Pick the highest-priority unchecked task that isn't marked blocked.
3. Write or extend an offline test that fails for the right reason. Mock paid APIs.
4. Implement the smallest change that makes it pass.
5. Run `make check`. If it fails, **diagnose the root cause before changing code**.
   Don't patch symptoms, loosen assertions, or skip tests to get green.
   After 3 failed attempts on one task: `git restore` your changes, change its
   `- [ ]` to `- [~]`, append `[blocked: reason]`, log it, and move on.
6. Ask the `reviewer` subagent to review the diff. Fix everything it marks Critical.
7. Commit on branch `auto/dev`: `git commit -m "<task-id>: <what changed>"`.
8. Tick the task in BACKLOG. Append a PROGRESS entry (format below).
9. New work you discover goes to the bottom of the right priority in BACKLOG, with
   acceptance criteria. Never delete tasks; mark them `- [-] ... [dropped: reason]`.
10. Continue with the next task.

### PROGRESS entry format

```
## <date> <task-id> <title>
CHANGED — files and what changed
CHECKED — how it was verified (test names, commands, measured numbers)
ASSUMED — anything inferred that Pietro didn't state, or "none"
DIVERGED — anything departing from DECISIONS.md or earlier choices, and why, or "none"
NEXT — the next task and any risk you see
```

Label claims by source. A guess never appears as a fact: say "verified", "assumed",
or "not verified".

## Hard rules

- **Secrets:** never read, print, log, or commit `.env` or any key. The code loads keys
  via `config.py`; you never need to see them.
- **Git:** work on `auto/dev`. No `git push`, no force, no rebase of existing history,
  no deleting branches.
- **Money:** live API calls only through `make smoke` or a pipeline run, and only if
  the spend ledger (task P0-2) shows budget left. Until P0-2 is done, the only live
  run allowed is the P0-3 smoke test, once. Veo: max 2 clips per day.
- **Design:** don't reverse anything in `docs/DECISIONS.md` silently. If evidence says
  a decision is wrong, add a new decision entry with the evidence, then change it.
- **Scope:** don't touch files outside this repo. Don't delete anything in `music/`.
- **Content:** scripts must stay scientifically accurate and calm. When unsure about a
  fact, the critic should make it more general rather than more confident.

## When you need Pietro, and when to stop

Don't ask questions mid-task; decide, and record the choice under ASSUMED.

If a task truly needs Pietro (missing or rejected key, a taste/money/publishing/licensing
decision), append it to `docs/NEEDS_PIETRO.md` (what, why, what you tried, what you'd
recommend), mark the task `- [~]`, and **continue** with tasks that don't depend on it.

Stop the whole loop by creating a file named `STOP` in the repo root containing one
line with the reason, when:
- the budget is exhausted,
- 3 tasks in a row ended blocked,
- no unchecked, unblocked tasks remain,
- continuing would risk breaking a rule above.

If `STOP` already exists when you start, do nothing and exit; Pietro put it there.
Before stopping, make sure `make check` passes on the committed state.
