# Spec — P1-14 + P3-4 REDO

**Why a redo.**
- Round 1 (2ab8998, 84a1642; 553 passed) and the partly done round 2 were lost unpushed when the
  container was replaced.
- Implement P1-14 directly as specified in **docs/specs/P1-14_r2.md**, whose "Design-check changes"
  are binding. It supersedes round 1's design: a SIGINT handler that only records the press, a
  guard on the Event calls, a submit loop and a poll-wait loop, a fixed end order, and precedence.
- Implement P3-4 as below, including its round-2 fixes.
- Two commits on wip/p1-14: "P1-14: …" then "P3-4: …".

## P1-14 (see P1-14_r2.md for the design, the state table, E1–E8 and the tests)
**What round 1 learned (keep):**
- The original flaky test was a CPython race: a SIGINT sent from a thread just before the main
  thread blocks is seen only when the wait ends. Raise signals synchronously
  (`signal.raise_signal`) from the exact spot, or use `signal.pthread_kill(main_ident, SIGINT)`
  from a worker. Never send them at a random time from a thread.
- Timing tests: `test_generation_runs_in_parallel_and_keeps_scene_order` must assert the PEAK
  number of calls in flight ([1,1] at 1 worker, [4,4] at 4) using a barrier that times out with
  the message "only k of n calls arrived together". Don't assert elapsed time.
  `test_a_failure_stops_new_work_at_once` uses events: item 0 sees `stopping` while in flight,
  and `sorted(started)` is [0,1] or [0,1,2].
- `ai_video`'s poll: `except BaseException` → record "lost track of job; assumed billed", then
  re-raise (W4).
  - P0-9 is already on auto/dev and changed the START block and the line before the poll. Touch
    only the poll's `except`.
  - The review probes are lost. Re-create a small orphan-thread test: a `Thread.start` wrapper
    that raises SIGINT after `super().start()`. Assert no pool thread is alive when `in_parallel`
    raises, and no reservation is made after the press.

## P3-4 — A rerun without `--brief` restores a deleted chapter from the topic folder
**From BACKLOG.** Seeding (D23, P3-3) happens only on a run with `--brief`. A rerun that leaves it
out uses the kept brief, but a chapter deleted from such a build goes to the writer and replaces
reviewed scenes (a paid Claude call).

**Accept:**
- Seeding records its folder in `build/<slug>/seed` (absolute path, `config.SEED_FILE = "seed"`).
- A rerun without `--brief` seeds from it while that folder's `brief.key` equals the build's
  stamp.
- Test: a chapter deleted from a seeded build comes back on a rerun without `--brief`, with 0
  calls.

**Design (round 1 plus round 2, accepted):**
- **Writing the record.**
  - `seed_script` writes the record via `_record_seed` (`_write_text`, atomic), after the brief
    stamp and BEFORE any copy, and only when it changes.
  - The same brief seeded from another folder moves the record.
  - A folder whose outline is not the build's is not recorded.
- **Keeping the brief early.** `seed_script` calls `keep_brief` right after the stamp and the
  record, so a crash mid-copy is completed by a rerun without `--brief`. Without this, that rerun
  is refused with "written with a brief; this run has none". Update keep_brief's docstring:
  "called at seeding too".
- **Reading the record.** It is read only:
  - on a rerun WITHOUT `--brief`, inside the existing `elif _script_started(work)` branch and
    only when `kept_brief` returned a brief;
  - or with `--brief <work>/brief.md`, the kept copy.

  An explicit `--brief` from another folder meets the D23 refusal first, and is never mixed with
  the record.
- **Using the folder.** Only when all of these hold:
  - the folder exists and has brief.md;
  - `brief_key(folder/brief.md) == folder/brief.key == build's brief.key`;
  - `_load_seed` finds a valid script.

  Otherwise print ONE line saying why, with no seeding and no `ap.error`.
- **Round-2 fix W3:**
  - validate the recorded seed, and print, ONLY when the build lacks outline.json or a chapter
    file the build's outline lists. No outline.json in the build counts as missing. An
    unreadable outline: validate as now;
  - remove the `seed` record BEFORE writing a changed brief stamp (a first stamp included);
    `_record_seed` then writes the new one.
  - Tests: (c3) a start-over with another brief, and (c4) the topic's brief re-reviewed after the
    film, each print NOTHING on an intact rerun.
- **Round-2 fix S3:**
  - a record with trailing spaces, or naming a missing path: "does not exist";
  - a path that exists and is not a directory: "is not a folder";
  - a record naming the build folder itself: rejected with one line.
- **Printing on the recorded path:** only when copying, or when the folder is rejected (with the
  reason). No "delete outline.json…" advice.
- **`--estimate` without `--brief`** plans from the record in the same way and writes nothing.
  Assert the build tree is identical before and after.
- **Update** the `--help` text and README's rerun line ("a rerun without --brief doesn't seed"
  becomes untrue).

**Acceptance scenarios (tests/test_brief.py, with a fake Claude counting calls):**
1. Seeded build; delete chapter_02.json and script.json; rerun with no `--brief` → it is seeded
   again, 0 calls, and script.json is rebuilt. The same holds with all chapters deleted, or the
   outline.
2. The topic folder moved away → one line; the writer is called for that chapter with the kept
   brief.
3. The topic's brief.md edited since: (a) brief.key unchanged, i.e. a re-review in progress;
   (b) edited and restamped → one line, no seeding.
4. `--estimate` with no `--brief` on scenario 1's state → no Claude script cost, nothing written.
5. A build seeded before P3-4 (no `seed` file) → today's behaviour.
6. An explicit `--brief` from another folder on a build with a record → D23's refusal applies
   first.
7. A crash mid-copy, then a rerun without `--brief` → completes with 0 calls. A crash while
   writing the record → the old record is whole, or there is none, and no partial file.
8. With the whole script deleted, a rerun has no brief, and seeds nothing.
9. Update the exact build-root listing at test_brief.py (around :487); it gains `seed` and
   `brief.md`.

**DECISIONS:** propose a D23 amendment (P3-3's seeding plus this record) and a D17 note in your
report.

## Rules
- Read `.claude/agents/implementer.md`.
- Worktree only. Scratch files in your own folder.
- Full suite once, under flock.
- **DURABILITY:** run `git push -u origin wip/p1-14` after EVERY commit.
