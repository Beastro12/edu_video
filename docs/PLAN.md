# Plan from here — Pietro and Claude (written 2026-09-27)

BACKLOG.md stays the list of tasks and their acceptance criteria. This file says in what order
they happen, what gates them, and who unblocks what. Claude updates it when a phase ends.

## Where we are (checked 2026-09-27, after P1-7)

Verified:
- P0-1…P0-7 (except P0-3) and P1-1…P1-7 are done, each with an offline test, a reviewer pass
  and a PROGRESS entry.
- `make check`: 134 tests pass, also from a fresh clone of the pushed branch.
- No key pattern and no `.env` anywhere in git history.
- Dependencies are consistent (`pip check`).
- The CLI runs with no keys, and `--estimate` calls nothing.
- PR #3's CI is green (the Next.js jobs).

Not verified, and why it matters:
1. **Nothing has run against the real APIs.** Model names, request shapes and prices come
   from documentation and secondary sources, checked only against mocks. This is the
   project's biggest risk: every feature added before the first live run is built on it.
2. **With a Google key, the first film may stop partway.** Veo is on by default. A 15-min
   film is estimated at €8.85 typical and €15.16 worst case (`--estimate`, Veo capped at 2
   clips a day), against a €10 budget for all runs together. Until you decide (NEEDS_PIETRO),
   check `--estimate` first, or run with `--no-ai-video` (€2.06 typical).
3. **Every film has a known defect:** the closing fade also dims the last narration (P1-8).
4. **Not publishable yet:** `music/` is empty (a synthetic placeholder bed is used), and the
   YouTube description is a placeholder.
5. **No CI runs the Python tests.** PR #3's CI runs only the Next.js jobs, so a regression
   is caught only if the agent runs `make check`.
6. **Dependencies are unpinned** (`>=` only). The installed versions are exactly the tested
   minimums, so a new SDK release could break the calls that the tests mock.

## Phase 1 — Claude, now (needs no keys)

In this order; each is a normal loop task (test, change, review, commit, PROGRESS):
1. **P1-8**: fade only the music at the end, not the voice (fixes a defect in every film).
2. **P1-9**: faster final film encode.
3. **P1-11**: pin dependency versions in a lock file (new; foundation).
4. **P2-3**: run report per film (costs, timings, QA, fallbacks). The first live run needs it.
5. **P2-5**: prune unused clips.
6. **P2-2**: thumbnail.
7. **P2-4**: README refresh.

P2-1 (batch mode) waits for Phase 3: producing many films makes sense only after one real
film has been verified. When only Pietro-blocked tasks remain, Claude stops and says so in
chat and PROGRESS. The `STOP` file is git-ignored, so it exists only on the machine it's made on.

## Phase 2 — Pietro (unblocks everything else)

Most useful first:
1. **Veo vs budget** (NEEDS_PIETRO, first entry): Veo on or off by default, and what
   `BUDGET_EUR` should be. About 5 minutes.
2. **Keys, then `make smoke` once** (NEEDS_PIETRO, keys entry): about €0.10–0.50, on your
   machine or in a cloud environment with the four variables and the three API hosts allowed.
   This is the gate for Phase 3.
3. **Harden the ledger** (NEEDS_PIETRO): two deny rules and one CLAUDE.md line.
4. **CI for the Python tests** (NEEDS_PIETRO): yes or no to an `edu_video` job in
   `.github/workflows/ci.yml`. Claude writes it if you say yes.
5. **PR #3**: merge it when you're happy with it. Work then continues on the same branch
   name, restarted from `main`, in smaller PRs. Also say whether Claude should watch PRs for
   review comments and CI failures.
6. **Taste, whenever:**
   - music you have rights to (NEEDS_PIETRO);
   - the YouTube description text;
   - whether the drift stutters visibly (P1-10: a 2-minute look);
   - the pause lengths (FYI entry);
   - two stale "Imagen" lines in CLAUDE.md (the stills now use the Gemini image model).

## Phase 3 — together, after `make smoke` passes

1. Claude fixes whatever the smoke run shows wrong (model names, response shapes, prices)
   and records it in PROGRESS.
2. **First real film**:
   - Claude runs `--estimate` first, then a 15-min film (without Veo unless you've decided
     otherwise), with its QA report and run report.
   - **You watch it.** This is the real acceptance test of the product.
3. Claude fixes what the first film shows. Then P2-1 (batch mode with the variety check) and,
   if you want Veo, the one-clip `live_veo` check.
4. Publishing stays yours: the pipeline makes the film, subtitles, metadata and thumbnail.
   Uploading to YouTube is not automated.

## How we work

- **Claude** runs the CLAUDE.md loop:
  - one task per commit, pushed to `claude/new-project-setup-b6eeid` (PR #3);
  - a reviewer pass before each commit;
  - each PROGRESS entry labels what was verified vs assumed;
  - anything that needs you goes to NEEDS_PIETRO, and its task is marked `[~]`.
- **Where this session departs from CLAUDE.md:** CLAUDE.md says to work on `auto/dev` with no
  `git push`. This cloud session must use and push its assigned branch instead. Update
  CLAUDE.md if you want the file to match.
- **Pietro** reads NEEDS_PIETRO (newest last) and the latest PROGRESS entries, and answers in
  chat or by editing an entry.
- **Money:** Claude makes no live call without keys and budget. `make smoke` runs once;
  Veo is capped at 2 clips a day; the ledger total is the cap.
