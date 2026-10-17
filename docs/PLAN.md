# Plan from here — Pietro and Claude (rewritten 2026-10-14; goal stages 2026-10-16)

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
5. **Known, small, open:** P2-8, P2-9, P1-14 and P3-4. Their order is in Stage 0 below.

## The goal, as checkable outcomes (planning group, 2026-10-16)

Made by a planning group: a Haiku fact sheet; Planner A (Opus, the path to the goal); Planner B
(Sonnet, live-run risk); an Opus merge; and an Opus plan review, accepted with its edits. The
sources are in the session scratchpad.

1. **G1, live APIs work.** Evidence: `scripts/preflight.py` all PASS, then `make smoke` 5 of 5,
   with the ledger delta at or under €0.6.
2. **G2, the first film is built.** Evidence: `run_report.md` outcome "finished", QA passes,
   spend within the step ceilings.
3. **G3, Pietro's verdict on the first film,** recorded word for word in PROGRESS. This is the MVP.
4. **G4, a second film from a new headline,** with the outline, writer and critic run live by the
   pipeline. The first film's script was written in-session, so its script stage never ran live
   (PROGRESS P3-3).
5. **G5, unattended production:** batch mode (P2-1) with a per-film spending cap, plus CI for the
   Python tests.

## Stage 0 — Claude, offline, before the stop (docs/WORKFLOW.md)

In this order. The new tasks come first: N4 must happen before any still is bought, and N1, N3,
N5 and N6 must exist before keys arrive.
1. **N4 (P3-5): flat-safe prompts.** Remove photographic cues (blur, mist, depth of field) from
   the first film's still and Veo descriptions. The narration stays as it is, and script.md is
   re-checked against the JSON.
2. **N1 (P3-6): `scripts/preflight.py`.**
   - Free probes:
     - each host;
     - model retrieve, plus a 16-token forced-tool call (a 200 with stop_reason max_tokens
       passes);
     - ElevenLabs models, voice and subscription;
     - Google `models.get` for image and Veo;
     - local FFmpeg filters (loudnorm, alimiter, sidechaincompress, xfade);
     - `manim --version` and disk space.
   - `--image` buys one 1K image (≈ €0.06), which catches a free-tier key.
   - `--tts-sample` buys one line (≤ €0.02), saves the mp3 and prints the words per minute.
   - It prints PASS/FAIL with redacted errors. Paid probes go through the ledger, and the total is
     at most €0.10.
3. **N3 (P3-7), N5 (P3-8), N6 (P3-9):**
   - **N3:** `scripts/make_pilot.py --chapters 1 build/pilot-seed` writes a one-chapter seed folder
     outside `topics/` (brief.md, brief.key, outline, chapter_01). It prints the pilot command and
     the copy command for the full film. Reuse is by copying the paid files: the cache keys depend
     on content, but the files live under the topic's build folder.
   - **N5:** run-report lines:
     - narrations limited out of the total, and the largest boost;
     - image attempts per still;
     - each QA check's margin to its limit;
     - estimate vs ledger.
   - **N6:** the build remembers `--no-ai-video`, like its style. Until then, every film command
     carries `--no-ai-video`: Veo is on whenever a Google key is set, so a plain rerun could buy
     Veo clips.
4. **P1-14:** Ctrl-C is the protocol's stop button.
5. **P2-8, then P2-9:** the master's first second and the true-peak margin. Mix only: the fix must
   not change any paid cache key. Planner A would do P2-9 only if the real true-peak check fails;
   it is cheap enough to do now.
6. **P3-4:** protects the reviewed script on reruns.
7. **N2 (P3-10):** record redacted live responses as contract fixtures. It must exist before the
   keys arrive, so smoke records them.
8. **After the first film:** P2-5 (prune; disk only), and P2-1's per-film cap.

## The next stop (what Claude does when only Pietro-blocked work remains)
1. `make check` is green.
2. NEEDS_PIETRO gets one **"Start here"** entry: the decisions below, numbered, each answerable in
   one line.
3. PLAN and PROGRESS are updated. README steps 4 and 6, and NEEDS entry 15, are rewritten for the
   pilot flow.
4. Commit and push `auto/dev`.
5. A message to Pietro of at most 6 lines, with the decisions and links.
6. In an unattended `run_autonomous.sh` run only: a `STOP` file whose line says "written by
   Claude: <reason>; delete to resume". STOP is git-ignored, so it is local to that machine.
   CLAUDE.md treats a STOP present at start as Pietro's; changing that rule is his call
   (decision 8).

## Pietro's decisions, most unblocking first
1. **Keys.** The four keys, all set on the ONE machine where every paid run happens. The ledger is
   per machine, because `build/` is git-ignored.
   - Use a Google key from a billing-enabled project, and an ElevenLabs plan that allows library
     voices through the API (Planner B's guess that this matters).
   - Here `api.elevenlabs.io` was refused; the other two hosts are not verified. Preflight checks
     all three.
   - If the runs happen in the cloud, how should the sample, pilot and film reach you? `build/`
     isn't pushed.
2. **`BUDGET_EUR`,** plus a spending limit at each provider.
   - The first-film sequence (sample, smoke, pilot, film, one rerun) is about €6.5 typical and
     about €21 in the worst case. About €9.7 is a realistic bad case, a guess assuming 30% of
     stills rejected.
   - Planner B suggests 12; Planner A and NEEDS_PIETRO suggest 15.
   - Each step also runs under an inline `BUDGET_EUR=<spent + step>` that never exceeds yours. The
     shell environment overrides `.env` (config.py uses `load_dotenv()` without override).
3. **Veo off for film 1.** It is untested; a clip costs €2.94, or €0.59 with Lite.
4. **May Claude judge the pilot's stills against a stated test** (flat look, no letters, no faces)?
   A cloud run has nobody watching. Otherwise you get a contact sheet.
5. **Settings:**
   - the opusplan and agent settings (NEEDS_PIETRO);
   - the ledger and `.env` deny rules (NEEDS #2);
   - the CLAUDE.md STOP-authorship rule.
6. **A second headline** for film 2 (G4).
7. **Later:** repository visibility, CI, a per-film cap for batch mode.

These are reactions to artefacts, not upfront decisions: the voice (from the sample), the look
(from the pilot), and music before publishing.

## Resume protocol (when keys arrive), with stop/go at each step
Each step runs under an inline `BUDGET_EUR` ceiling; hitting it means "stop and ask Pietro".
1. **Free.**
   - `make check`; `python scripts/preflight.py`. Go only if everything passes.
   - `--estimate` with the real keys: go if typical is €2.5–4.5 and the style reads flat; stop if
     over €5.
   - `--script-only`: expect zero new ledger lines. A new line means the writer ran.
2. **Voice.** `preflight.py --tts-sample`, or until it exists,
   `pytest -s -m live_voice tests/test_live_optin.py` (about €0.12; it also answers Eleven v4).
   Decide the voice BEFORE the pilot: voice, model and settings are in the narration cache key.
3. **`make smoke`** (ceiling +€1.0, built in).
   - Go: 5 of 5, ledger delta ≤ €0.6, all three providers called, 100–160 words per minute.
   - A smoke rerun re-buys everything, so ask Pietro before a second one.
   - Then compare the provider dashboards with the ledger, and recalibrate `EST_*` from the
     ledger's units.
4. **Pilot = chapter 1** (5 scenes, about 2 min; ceiling +€1.5; `--no-ai-video`). Go:
   - outcome "finished", cost ≤ €0.8;
   - at most 1 fallback to Manim (a planned Veo scene becoming a still doesn't count);
   - at most 6 still attempts for its 4 stills (the thumbnail not counted);
   - QA margins: loudness ± 0.7 LU, true peak ≤ -1.5, ducking ≥ 7 dB, duration ratio 0.85–1.15;
   - the limiter engaged on at most half the scenes.
5. **Full film** (ceiling +€5; `--no-ai-video`).
   - First copy the pilot's paid files (the command comes from N3):
     `P=build/pilot-ai-global-workspace F=build/do-ais-already-process-information-in-a-way-that-gives-rise-; mkdir -p $F/audio $F/visuals && cp -rn $P/audio/. $F/audio/ && cp -rn $P/visuals/. $F/visuals/`.
     Copy nothing else: no outline, chapters, script files, brief.key, style or failures.json.
   - Ctrl-C if spend passes 70% of the ceiling before the visuals finish.
   - Go: at most 4 of 39 fallbacks to Manim, cost ≤ €5, QA passes. A mix-only failure gets a free
     remix: the same command, with `--no-ai-video`.
6. **Pietro watches → G3.**

**Always stop and ask** on:
- a hit ceiling, or cost above 1.5× the estimate;
- 402/403 plan errors;
- any taste call;
- any Veo;
- a mid-run change to style, prompts or text (re-buys about €2.2 of stills or about €1 of
  narration);
- a second smoke.

## After the first film
- **Feedback:** each of Pietro's comments becomes a BACKLOG task with acceptance criteria, worked
  through docs/WORKFLOW.md.
- **Film 2 (G4).**
  - Claude writes the brief from primary sources; a Haiku agent checks that every [A] line quotes
    its source.
  - The pipeline writes the script live (`--brief`, no seed), and the critic runs.
  - Claude fact-checks the script before any narration is bought.
- **Batch mode (P2-1)** only after G4, with a per-film cap and the variety check. Then CI (G5).

## How we work

- **Claude Code** runs the CLAUDE.md loop: one task per commit, pushed to `auto/dev` (not
  `main`), a reviewer pass before each commit, a PROGRESS entry labelling what was verified vs
  assumed, and NEEDS_PIETRO for what needs you.
- **Operating model (Pietro, 2026-10-14 and 2026-10-16):** docs/WORKFLOW.md.
  - Opus plans, writes the specs, design-checks and reviews.
  - Sonnet implements in its own git worktree.
  - Haiku extracts facts, verifies `make check` and runs mechanical checks.
  - Every round is logged in docs/AGENT_LEDGER.md, with streak analyses.
  - The `opusplan` model setting itself waits for Pietro's yes.
- **Where this departs from CLAUDE.md:** CLAUDE.md says "No `git push`". A cloud session's work
  is lost unless pushed, so Claude pushes `auto/dev` only.
- **Money:** no live call without keys and budget. `make smoke` runs once; Veo is capped at 2
  clips a day; the ledger total is the cap. Each run's costs are in its `run_report.md`.
