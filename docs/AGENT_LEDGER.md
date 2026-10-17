# Agent ledger and streak analyses

Pietro, 2026-10-16: when a subagent returns work that needs fixing twice in a row, or succeeds
three times in a row, find out why. Was it the task, the instructions, or its method? Should it be
replaced? And what does it think is worth exploring?

This file is the record. docs/WORKFLOW.md says how rounds are run and logged from now on.

## Definitions
- **Round:** one hand-back from an agent: its first run, or a SendMessage that resumes it.
- **Implementer round:**
  - *success* = the next review found no Critical. Warnings are allowed. Accepted rounds that no
    reviewer saw are marked (unreviewed).
  - *needs fix* = the review found a Critical, or the round left a gap that took another
    implementer round.
- **Reviewer round:** *success* = its Critical calls held up when they were fixed, and it raised
  no false Critical.
- **Critical** (adopted 2026-10-16, from reviewer a84d's proposal):
  - an acceptance criterion isn't met as written; or
  - a reachable path silently produces wrong content, spends money unasked, or loses user work,
    however unlikely the path.
  - A refusal, or output that clearly shows what happened, is a Warning.
- **Triggers:** 2 needs-fix rounds in a row, or 3 successes in a row, for one agent instance; or
  the same pattern for one role and model across instances.

**Sources (verified):**
- the subagent transcripts (41 agents, 62 rounds);
- the main session transcript;
- a Haiku-built extraction:
  `/tmp/claude-0/-home-user-Mise/d564ebe5-c1cf-5ad9-a562-a240bb058770/scratchpad/ledger/` (session
  scratch: `agents.json`, `ledger.md`).
- Reviewer verdicts are parsed from the reports; flagged rows were checked by hand.

## Implementer rounds

**Era 1** (2026-09-27…30). The main session (Opus) implemented; an Opus reviewer subagent reviewed.
First review per task:

| Task | P0-1 | P0-2 | P0-4 | P0-5 | P0-6 | P0-7 | P1-1 | P1-2 | P1-3 | P1-4 | P1-5 | P1-6 | P1-7 | P1-12 | P1-8 | P1-13 | P1-9 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Critical | 0 | 1 | 0 | 1 | 0 | 0 | 1 | 0 | 0 | 0 | 1 | 1 | 0 | 0 | 0 | 1 | 1 |

- 7 of 17 tasks needed a fix on first review. Every fix passed re-review at the first attempt, so
  there was no instance of two needs-fix rounds in a row.
- Success streaks of 3 or more: P1-1's fix → P1-2 → P1-3 → P1-4, and P1-7 → P1-12 → P1-8.

**Era 2** (2026-10-14). Sonnet subagents implemented, the orchestrator (Opus) wrote the specs, and an
Opus reviewer subagent reviewed.

| Agent | Task | Rounds, in order |
|---|---|---|
| ae885 | P3-1, P3-3 | P3-1 r1 **needs fix** (Critical: changed brief reuses stale script) → r2 **needs fix** (self-flagged gap: stale brief.md) → r3 success → P3-3 r4 **needs fix** (Critical: all-or-nothing seeding) → r5 success |
| ad8d9 | P1-11 | r1 **needs fix** (Critical: KICKOFF.md still installed from ranges) |
| a758f | P3-2 | r1 success (W1: style not recorded) → r2 success (2 warnings, fixed by orchestrator) |
| a42cf | P2-3 | r1 success (no Critical, no Warning) → r2 success (unreviewed) |
| a664e | P2-7, P2-6 | P2-7 success → P2-6 success → fix round success (unreviewed) |
| a17ec | P2-2 | r1 taste change requested (not counted) → r2 **needs fix** (6 warnings incl. a hang and a paid re-buy; the Critical was the orchestrator's docs) → r3 success (unreviewed) |
| ad0eb (writer) | film script | r1 **needs fix** (critic: 21 issues; fact-check: 6) |
| ab1eb (critic) | film script | r1 success |

- First-round outcomes of Sonnet implementers, in time order: fix, fix, fix, success, success,
  success, success, fix. That is 4 of 8 needing a fix, against Era 1's 7 of 17. The samples are
  small: there is no evidence that Sonnet implements worse than the main Opus session did.

## Analyses

### A1. Needs fix twice in a row: ae885, P3-1 rounds 1–2 (and the same class in P3-3 round 4)

**What failed:** state across reruns. A changed brief reused a script written from another one; a
stale brief.md from an abandoned run was cited; an interrupted seed made the writer replace
reviewed chapters.

**Why:**
- **Instructions (the main cause; the orchestrator's).** The spec described one run. It never
  stated the invariant ("build/brief.md is the brief the script was written from"), nor what a
  rerun does when `--brief` changes or is omitted. Two of its lines combined into the bug ("without
  --brief, brief is None" and "overwrite if content differs"). Its "stop and explain rather than
  guess" made the agent flag a gap it could have closed (agent's account).
- **Method.** It tested each behaviour in one run, plus at most one rerun, and never ran crash,
  edit or change sequences ("the same blind spot twice", agent's account).
- **Task.** Cross-run cache and state invariants are the hardest class in this codebase. The
  reviewer's checklist line "can a changed input reuse a stale cached file?" led straight to
  both Criticals.
- **Model fit.** The same agent's rounds 3 and 5 succeeded once the invariants were spelled out.
  So the gap is inventing adversarial sequences unprompted, not the mechanics (agent's account,
  consistent with the record).

**Replace it?** No. Change the spec and the method instead (see Actions). The same class recurred
in P2-2 r2: buying a picture before checking freshness, and a stale thumbnail named after a
failed redraw. That shows a class problem, not an agent problem.

### A2. Three successes in a row: a664e, P2-7 → P2-6 → fixes

**Why:**
- **Task.** Small and local (mostly assembly.py), with outcomes measurable offline in dB, LUFS,
  samples and luma. P1-8 had left helpers and a baseline, so it reproduced the bug first.
- **Instructions.** The spec fixed the design and order, named the traps (loudnorm's silent
  fallback, limiter latency), stated a pin rule ("a clip-arg pin may change, a paid-file pin may
  not"), and said what to report.
- **Method.**
  - It measured before choosing an option; option A's silent fallback decided P2-6.
  - Its mutation checks found real gaps: a 48 kHz limiter, a bare-narration reading, a hard-coded
    rate.
  - It compared against HEAD (18 identical clip names) and built separate patches.

**But the streak hid two things:**
- **An orchestrator spec error.** "The last scene's visual target uses the final tail" re-keyed a
  paid Manim render against D9. The agent built it as written and reported it as a cost, not a
  conflict. The reviewer caught it.
- **A process violation.** A `git stash` in the shared tree took another agent's uncommitted
  work ("a lapse, not ignorance").

So a success streak isn't proof the spec was right.

**What to keep:** measure first; mutation checks; HEAD comparisons; patches built as it goes.

### A3. Success streak (4): reviewer a84d (P3-1 ×2, P3-3 ×2)

**Why:**
- **Method.** It turned every suspicion into a short sequence of real CLI runs on a scratch copy
  with Claude faked, recording what each run printed, called and wrote. It loaded HEAD's modules
  beside the new ones to settle byte-identity, and checked [A] lines sentence by sentence against
  the saved source.
- **Instructions.** The checklist's stale-cache line; concrete "extra points" per task; scope
  limits.
- **Its own correction.** One finding was a Warning, not a Critical. It asked for a written
  severity rule, now adopted above.
- **Across 28 reviewers in both eras,** every Critical in the record was accepted and fixed, and
  none was rejected as wrong (the orchestrator's record; nobody re-checked them independently).
  The Opus reviewer is the step that stopped every known Critical before commit.

### A4. Role-level pattern: Sonnet implementers went fix ×3, then success ×4, then fix

**The record.**
- The three needs-fix rounds: P3-1 and P3-3 (cross-run state) and P1-11 (scope).
  - P1-11's Critical came from the orchestrator's own instruction ("keep README edits minimal").
  - The agent had flagged KICKOFF.md as left alone.
- The four successes (P3-2, P2-3, P2-7, P2-6) followed specs that stated invariants explicitly:
  - "calm is byte-identical; pin it with old literals";
  - "the reading's key covers the narration; paid files keep their keys";
  - "a stopped run still writes the report".
- The next failure (P2-2) was again cross-run state (freshness before purchase), on a spec whose
  invariants section was thin.

**Cause.** Spec quality on state invariants, rather than the model.

**Efficiency (tokens, from the transcripts):**
- Implementers read 7M–36M input tokens each, mostly cache reads, against 0.6M–13M for reviewers.
  Cost grows with tool calls × context: a664e made 162 tool calls, and ae885 kept one context
  across 7 rounds.
- Full `make check` runs (7–10 min) were repeated 4+ times per task where a subset would have done.
- Rebuilding context at each re-review was the reviewer's biggest cost.

## Actions taken (2026-10-16)

| From | Change | Where |
|---|---|---|
| A1, A4, a84d, ae885 | Specs state invariants (each persisted file's writer and reader) and **acceptance scenarios as multi-run sequences** (rerun same, changed, omitted; crash mid-write; delete; hand-edit) that become tests | spec template, WORKFLOW.md |
| A2, a664e | **Design check** (Opus) before coding: the spec is read against DECISIONS; it lists cache keys, pins and paid files touched | WORKFLOW.md |
| A2, a664e | **One git worktree per implementer**; the orchestrator merges; no shared tree | WORKFLOW.md |
| A1, ae885 | Implementers close reversible, non-destructive gaps themselves and log them under ASSUMED; they ask only for deletions or policy | implementer.md |
| A1, ae885 | Trial: a **Haiku adversary** writes 3 rerun/crash/edit sequences from the spec and the diff, as tests | WORKFLOW.md (trial, measured) |
| A3, a84d | Written **severity rule**; a probe-sequence method for reviewers; a revision map ("which lines answer which finding") on re-reviews | reviewer.md, WORKFLOW.md |
| A2, efficiency | Each spec names a **fast test subset**; full `make check` once at the end; a Haiku agent verifies the implementer's `make check` claim | spec template, tests.md |
| efficiency | A fresh reviewer context per review, with the revision map, instead of one reviewer carrying 5 rounds | WORKFLOW.md |

**Ideas from the agents, not yet adopted:**
- a randomized operation-sequence test for cache state (ae885);
- Haiku checks [A] quotes against the source mechanically (a84d);
- probe harnesses kept as reusable files (a84d).
Each will be tried on the first task it fits, and logged here.

## Live log (from 2026-10-16)
Rows are appended per round; see docs/WORKFLOW.md for the format.

| Date | Agent | Model | Role | Task | Round | Outcome | Notes |
|---|---|---|---|---|---|---|---|
| 2026-10-16 | a9a77 | Haiku | search (facts) | planning | 1 | success | 244-line cited fact sheet; found PLAN/NEEDS inconsistency (opusplan entry missing) |
| 2026-10-16 | aa621 | Haiku | search (ledger) | analysis | 1 | success | 41 agents / 62 rounds extracted; flagged rows hand-checked |
| 2026-10-16 | a7fc4 | Opus | planner A | planning | 1 | success | goal outcomes G1–G5; G4 (live-script film) and STOP-authorship gaps |
| 2026-10-16 | afd67 | Sonnet | planner B | planning | 1 | success | 13-risk table; pilot gate; narration bought before stills; guesses labelled |
| 2026-10-16 | main | Opus | orchestrator (merge) | planning | 1 | needs fix | plan review: 3 Criticals in the merge (pilot files need copying; no voice-sample tool; reruns can buy Veo); guesses stated as fact |
| 2026-10-16 | af9ed | Opus | plan reviewer | planning | 1 | success | every code claim checked; ACCEPT-with-edits, all applied |
| 2026-10-17 | accdd | Opus | design checker | P2-8/9, P1-14/P3-4, P2-5 | 1 | success | before any code: 1 DECISIONS conflict (D17 in P1-14), missing no-paid-key rule, ~15 traps; measured the master fix in advance |

Note on the orchestrator rows: the orchestrator's own work (specs, merges) is logged too. Its two
known spec errors were P2-7's visual target (D9) and P1-14's flag invariant (D17); the plan merge
dropped constraints. Those are the reasons the design check and the plan review exist.
