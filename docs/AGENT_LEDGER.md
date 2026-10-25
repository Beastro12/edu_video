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

### A5. Needs fix three times in a row: the orchestrator (main, Opus), 2026-10-17
This is a self-analysis: the orchestrator can't be interviewed by anyone but itself. The evidence
is the live log and the specs in the scratchpad.

**The record:**
- **P3-7 spec.**
  - Design check a07d3 flagged that a film run without `--style` re-buys the pilot's pictures.
  - My binding fix refused a film build that records a *different* style. It missed a build with
    *no* record, which is the normal case for a new film.
  - Implementer acf40 found the gap and flagged it rather than deviating, because the spec forbade
    the fix.
- **Process.** I ran four full suites at once on 4 cores (load average 23). a1297's wall-clock
  test failed, and each suite took about 20 min instead of 12.
- **P3-5 spec.**
  - It said "each such cue is rewritten into flat-vector terms" and required "lens" in the cue
    list.
  - D25 says "a reviewed script serves any style", and "lens" was the subject of scene #31.
  - I had skipped the design check for this task ("content-only, no cache logic").
- **Earlier:**
  - P2-7's visual target (D9) and P1-14's flag invariant (D17);
  - constraints dropped in the plan merge.

**Why it happens:**
- **Every error is a rule from elsewhere that the spec didn't carry.** D9, D17 and D25 are
  standing decisions, and four cores is a fact about the machine. I write specs from the task's
  goal outward.
- **Both errors that reached code had gone round the design check,** the one step that reads a
  spec against everything else:
  - P3-5 skipped it;
  - P3-7's error was in my fix to a design-check finding, which nobody checked.
- **The P3-7 miss has a precise shape:** a recorded file with three states (none, same,
  different), and a fix that covered one of them. This is A1's cross-run-state cause, in the
  orchestrator.

**Why it fails:** the exception rule assumed only cache logic could conflict with a decision.
Content can too: D25 is about content.

**Why the work needs fixing:** the orchestrator writes specs, merges and runs agents in one
context. The check it saves time on is the one that fails.

**More efficient:**
1. No exceptions to the design check. P3-5's would have cost minutes of Opus. Skipping it cost
   an implementer round and a review round on W1 and W3.
2. A fix written in answer to a design-check finding is written as a state table: for each
   recorded file it touches, the rows none, same, different and malformed.
3. Each spec quotes the DECISIONS lines it relies on, not only their numbers, including what must
   stay shared (D25's "what every style shares").
4. Parallel work is planned against the machine: one full suite at a time. Done with `flock`, in
   WORKFLOW.md.

**Replace?**
- Not the model: these are steps skipped, not reasoning another model would do better.
- The design checker, an Opus instance with a separate context, already stands in for the
  orchestrator checking itself. The fix is to stop going round it.

**Idea worth trying:** a Haiku "spec linter" before the design check. It lists every DECISIONS
entry whose subject the spec touches (style, record, cache key, Veo, brief, seed) and pastes the
quoted lines into the spec. It is cheap, mechanical, and aimed at exactly this error class.

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

## Actions taken (2026-10-17)

| From | Change | Where |
|---|---|---|
| A5 | No exceptions to the design check, content-only specs included | WORKFLOW.md |
| A5 | A fix to a design-check finding is written as a state table (none / same / different / malformed) for each recorded file it touches | WORKFLOW.md |
| A5 | Specs quote the DECISIONS lines they rely on, including what must stay shared | WORKFLOW.md (spec template) |
| A5, process | One full test suite at a time (`flock`) | WORKFLOW.md, tests.md, implementer.md |
| A5 (trial) | Haiku spec linter before the design check: quotes the DECISIONS entries a spec touches | first used on P2-5's re-spec; measured here |

## Live log (from 2026-10-16)
Rows are appended per round; see docs/WORKFLOW.md for the format.

Note on the orchestrator rows: the orchestrator's own work (specs, merges) is logged too. Its two
known spec errors were P2-7's visual target (D9) and P1-14's flag invariant (D17); the plan merge
dropped constraints. Those are the reasons the design check and the plan review exist.

| Date | Agent | Model | Role | Task | Round | Outcome | Notes |
|---|---|---|---|---|---|---|---|
| 2026-10-16 | a9a77 | Haiku | search (facts) | planning | 1 | success | 244-line cited fact sheet; found PLAN/NEEDS inconsistency (opusplan entry missing) |
| 2026-10-16 | aa621 | Haiku | search (ledger) | analysis | 1 | success | 41 agents / 62 rounds extracted; flagged rows hand-checked |
| 2026-10-16 | a7fc4 | Opus | planner A | planning | 1 | success | goal outcomes G1–G5; G4 (live-script film) and STOP-authorship gaps |
| 2026-10-16 | afd67 | Sonnet | planner B | planning | 1 | success | 13-risk table; pilot gate; narration bought before stills; guesses labelled |
| 2026-10-16 | main | Opus | orchestrator (merge) | planning | 1 | needs fix | plan review: 3 Criticals in the merge (pilot files need copying; no voice-sample tool; reruns can buy Veo); guesses stated as fact |
| 2026-10-16 | af9ed | Opus | plan reviewer | planning | 1 | success | every code claim checked; ACCEPT-with-edits, all applied |
| 2026-10-17 | accdd | Opus | design checker | P2-8/9, P1-14/P3-4, P2-5 | 1 | success | before any code: 1 DECISIONS conflict (D17 in P1-14), missing no-paid-key rule, ~15 traps; measured the master fix in advance |
| 2026-10-17 | a07d3 | Opus | design checker | P3-6/7/8 | 1 | success | before any code: 3 spec Criticals (ffmpeg -h exits 0 for a missing filter; a film rerun without --style re-buys stills; estimate line vacuous) |
| 2026-10-17 | — | — | (exception) | P3-5 | — | — | no design check: content-only task with no cache logic; logged as a deliberate exception |
| 2026-10-17 | main | Opus | orchestrator (spec) | P3-7 | 1 | needs fix | my design-check change refused a film build recording a DIFFERENT style but missed one with NO record (a film run without --style re-buys the pilot's pictures); found and flagged by implementer acf40, who kept to the spec |
| 2026-10-17 | main | Opus | orchestrator (process) | wave 1–2 | — | needs fix | 4 full suites at once on 4 cores (load 23): a wall-clock test failed for a1297 and each suite took 20 min; fix: one full suite at a time (flock), in WORKFLOW.md, tests.md, implementer.md |
| 2026-10-17 | a8b73 | Haiku | tests verifier | P3-5 | 1 | success | 520 passed, 7 deselected, matches the claim; a first run hit its own 20-min background limit at 83% (not a failure), rerun with a longer limit |
| 2026-10-17 | a3288 | Sonnet | implementer | P3-5 | 1 | success | no Critical; 3 Warnings (lens subject lost to the banned word "lens"; inflections missing though the comment promised them; rewrites added flat-only medium words against D25) |
| 2026-10-17 | a3c18 | Opus | reviewer | P3-5 | 1 | success | no Critical; probes: base files fail the new check (18 hits / 11 scenes), invariants byte-checked, full suite 520; W1 root cause traced to the spec's own required cue ("lens") |
| 2026-10-17 | main | Opus | orchestrator (spec) | P3-5 | 1 | needs fix | spec asked for "flat-vector terms" (against D25: "a reviewed script serves any style") and required "lens", the subject of #31; the design check was skipped as an exception. Trigger: 3rd needs-fix in a row → A5 |
| 2026-10-17 | a3a83 | Haiku | tests verifier | P3-6 | 1 | success | 598 passed, 0 failed at load 8–11, against the implementer's 597 + 1 failed at load 23: consistent with the load diagnosis; it called the claim "wrong" where the counts are consistent (an over-reading, not a false result) |
| 2026-10-17 | — | — | (environment) | wave 1–2 | — | — | 2nd session-limit stop (reset 12:30 UTC) with 7 agents running; resumed 2026-10-25 after a container restart, which killed every background suite. Cost: each agent re-checks its tree and reruns its suite. Trial: at most 4 agents at once, measured on the next waves |
| 2026-10-25 | a3288 | Sonnet | implementer | P3-5 | 2 (fix) | pending re-review | W1–W3, S1, S2, S4, S7, S8 as decided; STYLE_MEDIUM_CUES added; 1.1/1.2 edited beyond the list under "and similar", flagged by itself |
| 2026-10-25 | acf40 | Sonnet | implementer | P3-7 | 1 | pending review | make + copy, 70 tests, 15 mutations killed; flagged the no-record style gap instead of deviating from the spec |
| 2026-10-25 | acf40 | Sonnet | implementer | P3-7 | 2 (spec change) | pending review | copy records the style (a)–(e) tested; 6 mutations of the write killed |
| 2026-10-25 | main | Opus | tests verifier (direct) | P3-5 r2 | 1 | success | flock'd suite on 2081b04: 523 passed, matches; run by the orchestrator as a background command instead of a Haiku agent (no agent tokens) |
| 2026-10-25 | main | Opus | tests verifier (direct) | P3-7 r2 | 1 | success | flock'd suite on 7f1f8b7: 597 passed, matches |
| 2026-10-25 | — | — | (environment) | all | — | — | 3rd session-limit stop (reset 17:50 UTC), ~50 min after resuming 6 agents. The 4-agent cap now applies: 4 resumed (P3-5 re-review, P3-6 review, P1-14/P3-4, P2-8/9); P3-7 review and the P3-9/10 design check wait for a slot |
| 2026-10-25 | a437a | Sonnet | implementer | P1-14, P3-4 | 1 | pending review | flake root-caused (CPython signal race: SIGINT from a thread just before a lock acquire is seen only when the wait ends) and fixed by raising the signal synchronously; timing tests now assert peak in-flight calls; 9 + 15 mutations killed; reported the scratch-name collision |
| 2026-10-25 | a3c18 | Opus | reviewer | P3-5 | 2 | success | re-review: W1–W3 closed, no Critical or Warning; kept 1.1/1.2 edits with reasons; 6 optional suggestions (S1 taken at merge) |
| 2026-10-25 | a3288 | Sonnet | implementer | P3-5 | 2 (fix) | success | re-review found no Critical or Warning |
| 2026-10-25 | a1297 | Sonnet | implementer | P3-6 | 1 | needs fix | Critical C1: inline `google_client.make().models.get` can never send (the temporary genai Client's `__del__` closes its httpx client); 4 Warnings (key prefix printed when cut before redact; tier match; missing_permissions shape; guard test can go live) |
| 2026-10-25 | a43d9 | Opus | reviewer | P3-6 | 1 | success | C1 reproduced by the orchestrator independently (genai_lifetime.py); found the same bug PRE-EXISTING in image_agent.py:53 (every still → Manim on a real run), missed since P0-6; method: real SDK, transport faked, socket tripwire |
| 2026-10-25 | a43d9 | Opus | design checker | P0-8 | 1 | success | GO-with-changes; prototyped the fix and the test on base (fails with the right error, passes fixed, no socket); AST guard resolved by import with a self-test. Trigger: design-checker role 3 successes in a row (accdd, a07d3, a43d9) |
| 2026-10-25 | ad515 | Haiku | adversary (trial) | P3-7 | 1 | success | 3 sequences, 2 FAIL, both real and both Critical, reproduced by the orchestrator: an unfinished review log copied mid-pilot makes the film pay for a second review; on a disk without hard links the copy fallback can replace a film file. First trial run: it caught what 77 implementer tests and 21 mutations missed |
| 2026-10-25 | acf40 | Sonnet | implementer | P3-7 | 1 (found after r2) | needs fix | the two adversary Criticals (above) are in round 1's copy code; both on paths that combine two runs or two processes, again A1's cross-run-state class |
| 2026-10-25 | acf40 | Sonnet | implementer | P3-7 | 2 (spec change) | success | the style record (a)–(e); the adversary's sequences found nothing in it |
