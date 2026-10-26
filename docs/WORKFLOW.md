# How work is done here: the Opus → Sonnet → Haiku workflow

Pietro, 2026-10-14 and 2026-10-16:
- Opus plans; Sonnet implements; Haiku helps with search, tests and docs.
- Every task is achieved AND reviewed before it is accepted.
- When an agent needs fixing twice in a row, or succeeds three times in a row, find out why.

This file is the process. docs/AGENT_LEDGER.md is its record.

## Roles

| Role | Model | Definition | Does | Never |
|---|---|---|---|---|
| Orchestrator | Opus (main session) | this file | plans; writes specs; runs the design check; accepts or rejects; merges; commits; logs | implements large changes itself; skips the review |
| Design checker | Opus | `.claude/agents/reviewer.md` (design mode) | reads a spec against DECISIONS and the code before any code is written | edits |
| Implementer | Sonnet | `.claude/agents/implementer.md` | implements one spec in its own git worktree; tests; mutation checks | works in the shared tree; edits docs/ |
| Adversary (trial) | Haiku | `.claude/agents/adversary.md` | from the spec and the diff, writes 3 rerun/crash/edit tests expected to break an invariant | fixes code |
| Tests verifier | Haiku | `.claude/agents/tests.md` | runs `make check` (or a named subset) in the worktree, and reports the tail | edits anything |
| Reviewer | Opus | `.claude/agents/reviewer.md` | reviews the diff with the checklist, probe sequences and the severity rule | edits |
| Search / docs helpers | Haiku | `.claude/agents/search.md`, `docs.md` | extract facts with file:line citations; check quotes against sources | judge, or edit |
| Planner group | Opus + Sonnet (+ a Haiku fact sheet) | ad hoc | plans for a stage: one planner per lens, an Opus merge, an Opus plan review | — |

Sessions started inside `edu_video` load these agents by name. Sessions started elsewhere (as on
2026-10-16) run the same roles through the Agent tool, with each prompt pointing at the definition
file.

## One task, step by step

1. **Spec** (orchestrator), from the template below, saved in the session scratchpad.
2. **Design check** (Opus). Verdict GO, GO-with-changes or STOP. The spec is edited until GO.
   - Every spec gets one, content-only specs included (A5: P3-5 skipped it and contradicted D25).
   - A fix the orchestrator writes in answer to a finding is written as a state table: for each
     recorded file it touches, the rows none, same, different and malformed (A5: P3-7's fix
     covered one row of three).
   - Trial: before the design check, a Haiku spec linter quotes into the spec every DECISIONS
     entry whose subject the spec touches.
3. **Worktree:**
   `git worktree add -b wt/<task> <scratchpad>/wt/<task> auto/dev`.
   - One worktree per implementer, so no agent ever shares a working tree. This rule exists
     because of a 2026-10-14 `git stash` that took another agent's work.
4. **Implement** (Sonnet), in its worktree only.
   - Use the spec's fast test subset while working, and the full `make check` once at the end.
   - Every full suite, by any agent, runs as `flock <scratchpad>/makecheck.lock make check`, so
     only one runs at a time. Added on 2026-10-17: four suites at once on 4 cores (load average 23)
     failed the wall-clock test in tests/test_parallel.py and took 20 min each.
   - Commit on the worktree branch, one commit per task.
   - Scratch files go in the agent's own folder, `<scratchpad>/<task>-<id>/`, never the
     scratchpad root. Added on 2026-10-25: two agents' `mutate.py` collided, and one ran the
     other's script in its own worktree (caught and cleaned by the agent; nothing committed).
5. **Adversary** (Haiku), for every task that persists state; standard since 2026-10-25 (A6: two
   Criticals on its first run). It works in its own detached worktree. Its tests go into the
   implementer's worktree with the fix. A test that fails is a finding, and it goes back to the
   implementer.
6. **Verify** (Haiku). Run `make check` in the worktree independently, and report the tail. This
   checks the implementer's claim.
7. **Review** (Opus), on a fresh context with the spec, the diff and a revision map. The
   implementer's report must say which lines answer which finding.
8. **Accept or reject** (orchestrator).
   - Accept when Haiku's `make check` is green and the review has no Critical.
   - Warnings are fixed or recorded as follow-up tasks.
   - Then cherry-pick onto `auto/dev`, run `make check` on `auto/dev`, update the docs (BACKLOG,
     PROGRESS, DECISIONS), commit, and push.
9. **Log** the round in docs/AGENT_LEDGER.md ("Live log"), then check the triggers.

## Spec template
- **Goal and acceptance**, quoted from BACKLOG.
- **Direction:** the orchestrator's design. The implementer may challenge it with measurements.
- **Invariants:** every file the task persists or reads, with its writer, its reader and its
  rule. Cache keys: what must change, and what must NOT, especially paid files (D9).
- **Acceptance scenarios:** multi-run sequences, each becoming a test:
  - rerun with the same input;
  - changed input;
  - flag omitted;
  - a crash after each write, then a rerun;
  - an artifact deleted, then a rerun;
  - a hand edit, then a rerun;
  - for each blocking call or shared file: the same step while another process or thread acts
    on the build (A7).
- **DECISIONS touched,** with the lines the spec relies on quoted, including what must stay
  shared (e.g. D25's "what every style shares"). The design checker adds any that were missed.
- **Fast test subset.**
- **Rules:** worktree path; no live calls; no `.env`; no docs/ edits. A reversible gap you find
  yourself: fix it and note it under ASSUMED. Ask only before deleting user files or changing
  policy.
- **What to report:**
  - files changed and tests;
  - a mutation check;
  - measured numbers;
  - the `make check` tail;
  - the worktree commits;
  - the revision map on re-rounds.

## Severity (all reviewers)
- **Critical:**
  - an acceptance criterion is not met as written; or
  - a reachable path silently produces wrong content, spends money unasked, or loses user work,
    however unlikely that path is.
- **Warning:** a refusal, or output that clearly shows what happened; a quality or robustness
  issue.
- **Suggestion:** everything else.

## Outcome log and triggers
- Each round gets one row in AGENT_LEDGER.md's live log, in this format:
  `| date | agent id (5) | model | role | task | round | success / needs fix / rejected | why, in one line |`
- **Success:** the round was accepted with no Critical.
- **Needs fix:** a Critical, a failing adversary test, a red verify, or a gap that took another
  round.
- **Triggers** (per agent instance, and per role+model across instances):
  - **2 needs-fix rounds in a row:** failure analysis.
  - **3 successes in a row:** success analysis.
- **Analysis.** It answers four questions, and each gets an answer:
  1. Is it the task?
  2. Is it the instructions?
  3. Is it the agent's method?
  4. Does it need a different model, or a replacement?
  - Inputs: the round's spec, the reports and the reviewer's findings; transcript statistics (a
    Haiku extraction); and an **interview** of the agent itself.
  - Interview questions:
    - why it went as it did;
    - what in the instructions helped, or was missing;
    - what in its method to keep or drop;
    - what cost the most;
    - one idea worth exploring.
  - The result goes to AGENT_LEDGER.md "Analyses", with an action, and the action goes into this
    file, an agent definition or the spec template. An action is measured on the next tasks
    before it is kept.
- **Replacing an agent.** Replace one when two analyses in a row blame the agent's method or the
  model, not the task or the instructions. Instructions and specs come first: in the record so
  far, failures traced to the specs.

## Budget for this workflow
- Agents are paid in tokens, not euros, but contexts grow with every tool call. The ledger showed
  7–36M input tokens per implementer. So:
  - one task per implementer instance;
  - a fresh reviewer per review;
  - fast test subsets;
  - Haiku for the mechanical steps.
- Trial items (the adversary) stay only if the ledger shows they catch something a reviewer would
  otherwise have had to.
