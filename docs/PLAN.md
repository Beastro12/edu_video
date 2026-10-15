# Plan from here — Pietro and Claude (rewritten 2026-10-14)

BACKLOG.md stays the list of tasks and their acceptance criteria. This file says in what order
they happen, what gates them, and who unblocks what. Claude updates it when a phase ends.

## Where we are (checked 2026-10-14)

**Goal (Pietro, 2026-10-14):** "finish this project to MVP", with a first film on a New Scientist
headline, "Do AIs already process information in a way that gives rise to consciousness?",
"in kurzgesagt style + others I said I like". MVP = one real film made end to end by the
pipeline and watched by Pietro (Phase 3 below).

Verified:
- **Offline MVP work, done:** P3-1 research briefs, P3-3 the first film's brief and script,
  P3-2 the flat style, P1-11 the lock file, P2-3 the run report, P2-7 the fade after the last
  word, P2-6 even narration levels, P2-2 the thumbnail, P2-4 the README and runbook.
  - Each has offline tests, an independent reviewer pass (every Critical fixed before commit)
    and a PROGRESS entry.
  - Each is pushed to `auto/dev` in Beastro12/edu_video.
- **`make check`:** 517 tests pass (2026-10-15, rerun after a container restart).
- **The first film's script** is in `topics/ai-global-workspace/`: 39 scenes, about 16 min of
  narration.
  - Every fact is traced to a brief line labelled by source: Anthropic's own 6 July 2026 post,
    or established science.
  - It was critic-reviewed and fact-checked. It never says or implies an AI is, or isn't,
    conscious.
  - `script.md` is the readable version.
- **Without keys**, `--script-only` seeds and builds the script with zero calls.
- **`--estimate` for the first film** (run 2026-10-15 with a dummy Google key, so the stills are
  priced; nothing was called):
  - without Veo: **€3.31 typical, €10.96 worst case**;
  - with Veo: €9.06 typical, €16.24 worst case;
  - against the €10 budget (NEEDS_PIETRO, 2026-10-15).

Not verified, and why it matters:
1. **Nothing has run against the real APIs** (no keys in this environment, and the network
   policy blocks `api.elevenlabs.io`). Model names, request shapes and prices are checked only
   against mocks. This is still the biggest risk.
2. **The flat look is unseen.** 12 of the script's 29 still and Veo descriptions use photographic
   words, which may pull pictures away from flat. Look at the first few stills (NEEDS_PIETRO).
3. **Levelling is tested on synthetic voices only.** Real ElevenLabs speech is not. The run
   report and `clips/level_*.json` will show how often the limiter engages.
4. **Not publishable yet:** `music/` is empty (a placeholder pad is used), and the YouTube
   description text is a placeholder.
5. **Known, small, open:**
   - P2-8: the film's first second is about 1.8 dB louder, from the master `loudnorm`, once per
     film.
   - P2-9: true-peak margin on dense bursts.
   - P1-14: a millisecond Ctrl-C window.
   - P3-4: a rerun without `--brief` doesn't re-seed a deleted chapter.

## Phase 1 — Claude, now (needs no keys)

Done 2026-10-15: P2-2 (thumbnail) and P2-4 (README with the first-film runbook). Offline work
left, none of it blocking the first film: P2-8, P3-4, P1-14, P2-9, P2-5. P2-1 (batch mode) waits
for one real film.

## Phase 2 — Pietro (unblocks the film)

Most useful first:
1. **Keys and hosts.** Either:
   - in this cloud environment's settings (title bar → environment → Edit), add
     `ANTHROPIC_API_KEY`, `ELEVENLABS_API_KEY`, `ELEVENLABS_VOICE_ID` and `GOOGLE_API_KEY` as
     environment variables, and add `api.elevenlabs.io` to Allowed domains; or
   - run on your machine with `.env`.
   Never paste keys into chat.
2. **The look** (NEEDS_PIETRO, 2026-10-14): is "Kurzgesagt-inspired pictures, calm
   SleepOnPhysics voice" what you meant, and which "others"?
3. **Veo** on or off for this film (default off), and `BUDGET_EUR` (€10 now): the no-Veo
   worst case is €10.96 (NEEDS_PIETRO, 2026-10-15).
4. **Music** you hold rights to (otherwise the placeholder pad).
5. **Earlier open items** (NEEDS_PIETRO): CI, repository visibility, Eleven v4, pacing, the
   film encode trade-off, ledger hardening, and the opusplan settings diffs (still waiting
   for your yes).

## Phase 3 — the first film, together

1. `make smoke` once (about €0.10–0.50). Claude fixes whatever it shows wrong.
2. The film. README's "Make the first film" section has the exact commands:
   - `--estimate` first;
   - then the run with `--brief topics/ai-global-workspace/brief.md --style flat --no-ai-video`;
   - look at the first stills;
   - read `run_report.md` and `qa.json`.
3. **You watch it.** That is the acceptance test of the MVP.
4. Claude fixes what the film shows. Then P2-1 (batch mode).
5. Publishing stays yours. The pipeline makes the film, subtitles, metadata (with the brief's
   Sources) and thumbnail.

## How we work

- **Claude Code** runs the CLAUDE.md loop: one task per commit, pushed to `auto/dev` (not
  `main`), a reviewer pass before each commit, a PROGRESS entry labelling what was verified vs
  assumed, and NEEDS_PIETRO for what needs you.
- **Operating model (Pietro, 2026-10-14):**
  - The main session plans, researches, fact-checks and reviews diffs.
  - Sonnet subagents implement each task from a written spec.
  - An independent reviewer subagent checks each diff.
  - The `opusplan` / Haiku-helper settings themselves are not applied yet: they wait for
    Pietro's yes.
- **Where this departs from CLAUDE.md:** CLAUDE.md says "No `git push`". A cloud session's work
  is lost unless pushed, so Claude pushes `auto/dev` only.
- **Money:** no live call without keys and budget. `make smoke` runs once; Veo is capped at 2
  clips a day; the ledger total is the cap. Each run's costs are in its `run_report.md`.
