# Spec — P3-7 REDO: the pilot tool (`scripts/make_pilot.py`)

**Why a redo.**
- Four rounds (37fc849 → 1e1c356) were lost unpushed when the container was replaced.
- This spec is the design after all four rounds and the round-4 review. The round-4 Critical is
  folded in as decided.
- BACKLOG P3-7 is the acceptance, plus the PLAN resume steps 4–5.

## `make OUT --from SRC --chapters N[,N] [--topic T] [--film-topic F] [--style S]`
- **Refuse:**
  - OUT under `topics/` or `build/`, a symlink into either, or a dangling link;
  - OUT existing and non-empty;
  - a chapter not in the outline, or whose file is missing.
- **Check the source** with `pipeline._load_seed(src, brief)`, so the refusal text is the
  pipeline's own.
- **Write**, in this order: the chapter files, then `outline.json` (only the chosen chapters, all
  other keys kept, in outline order), then `brief.md` (a byte copy), then `brief.key`
  (`utils.brief_key` of brief.md read as utf-8-sig) LAST. A crash therefore leaves a folder the
  pipeline refuses. Afterwards, re-check the output with `_load_seed`.
- **Print:**
  - the pilot command: `python pipeline.py "<pilot topic>" --brief <OUT resolved>/brief.md --style
    flat --no-ai-video`, where the default topic is "pilot <source folder name>";
  - the copy command. Without `--film-topic`, the literal `"<film topic>"` placeholder.
- Every printed command is a double-quoted shell word, with a round-trip test.
- Refusals go to stderr as `make_pilot.py: <message>`, exit 2.

## `copy PILOT FILM [--from SRC]`: hands the pilot's paid audio/ and visuals/ to the film build
**Order:** every refusal, then the style record, then the files. Nothing is written on a refusal.

1. **Refusals (all before any write):**
   - the pilot build is missing, or pilot == film, or a slug is empty;
   - the pilot's `style` record is missing or invalid: non-UTF-8, a directory, a broken link,
     empty, or not a style name. The message names the file and says "rerun the pilot once with
     its --style (it buys nothing already made), or write the style name into <path>";
   - the pilot's manifest.json `style` differs from its record. The message also says "or finish
     the pilot in <record> (rerun with --style <record>)";
   - the film records a different style: "to make the film in <style>, delete <film>/style (its
     <other> pictures stay on disk) and copy again";
   - the film folder, or its audio/ or visuals/, is a file, or resolves inside `topics/` (use the
     existing inside() helper). That includes a symlink;
   - **THE PILOT HAS NOT FINISHED** (the round-4 review's Critical, option (i), orchestrator
     decision):
     - the pilot's `manifest.json` must exist and list every scene of its `script.json`, and
       every audio and visual file the manifest names must be a plain file
       (`render_film` writes the manifest only after every visual is made);
     - and no review log of the pilot's CURRENT script may be unfinished or unreadable. Compute the
       current logs with `image_agent.review_path(scene, visuals)` for the still and ai_video
       scenes of script.json, with `config.STYLE` set to the pilot's recorded style and restored
       in a finally. If script.json is unreadable, every log counts.
     - Otherwise refuse: "the pilot has not finished; let it finish (or rerun its command: python
       pipeline.py "<pilot topic>" --style <style> --no-ai-video), then copy".
     - The rerun command ALWAYS carries `--no-ai-video`. The round-4 review showed that leaving it
       out for a Veo pilot creates a dead end and buys Veo; the film command reads exactly those
       still logs.
     - Logs NOT read by the current script never block. The walk skips them, saying "the film
       decides that scene itself".
   - the disk cannot make hard links. Probe once in the film's build folder, or the nearest
     existing parent: a run-unique name (pid plus `secrets.token_hex`), `open(..., "xb")`, then
     `os.link`. Remove only what this run made.
2. **The style record:** if the film has none, write the pilot's style into `build/<film>/style`
   via `put_new`, which never replaces. A same-style record is left untouched. A record appearing
   mid-copy: a different style is refused, the same style goes on.
3. **The walk:**
   - copy every regular file under audio/ and visuals/ recursively;
   - skip `.partial-*`, symlinks (and don't follow symlinked folders) and `visuals/manim_media/`
     (Manim's scratch, not paid output);
   - never copy clips/, level files, failures.json, manifests or script files;
   - every file is made by `put_new`: a `.partial-` name, then `os.link` into place. That fails if
     the name is taken, so nothing is EVER replaced. There is no fallback;
   - review logs are copied LAST in their folder. Each is read ONCE as bytes and judged by
     `review_problem(data, visuals)`, which checks: valid JSON dict, `done` true, and the accepted
     name is a plain non-symlink file in the pilot's visuals.
     - Copy the accepted picture FIRST.
     - Write the log (those exact bytes) only if the picture is now in the film.
     - Otherwise skip the log, print NOT COMPLETE, exit 1, and offer no film command.
4. **Print:**
   - copied and skipped files, with the reason for each skip;
   - "failures.json is not carried over (a pilot failure is asked again)";
   - a VEO_NOTE if the pilot has `ai_*.mp4` or manifest scenes of kind "ai": the film command,
     with `--no-ai-video`, will not use them;
   - the film command: `python pipeline.py "<film>" --brief topics/<src>/brief.md --style <style>
     --no-ai-video`. `<src>` comes from `--from`, or from matching the pilot's kept brief.md
     against `topics/*/brief.md` by content. A non-UTF-8 topic brief is refused by name; say to
     use --from.

## Tests (tests/test_pilot.py, tests/test_pilot_reviews.py; providers faked as in test_brief/test_style; FFmpeg assembly may be stubbed)
- **Scenario 1:** `make` on the real topic → `--estimate` on the pilot with no keys works, and
  writes nothing.
- **Scenario 2:** a faked pilot run, then `copy`, then a faked film run.
  - The film's paid requests exclude every scene the pilot made (narration, image prompts, still
    reviews, Manim, thumbnail).
  - The film's `--estimate` prices only the rest.
  - A control: without copy, everything is re-bought.
- `copy` twice → everything skipped. Each refusal → nothing written, and no style record.
- **A crash at each `make` write, and at each `copy` point** → no half file, and the rerun
  finishes.
- **A file appearing mid-copy is not replaced**: hard links only, and a no-hard-link disk is
  refused.
- **A film run without `--style` after copy** keeps the pilot's style and buys only the rest. An
  explicit other `--style` still wins.
- **Running-pilot races** (the round-4 review): run them at WORKERS=1 AND at default, and pin
  each precondition (assert visuals/ exists when the sequence requires it):
  - A1: copy during the pilot's narration stage → refused (the pilot has not finished).
  - A5: a log finished after its folder was listed → never carried without its picture.
  - The film then buys nothing the pilot bought.
- **A stale log of another style** does not block (A3).
- **Mutation check:** the finished gate removed; the per-file check removed; manim_media copied;
  a fallback that replaces; `--no-ai-video` dropped from the rerun command.

## Rules
- Read `.claude/agents/implementer.md`.
- Worktree only. Scratch files in your own folder.
- Full suite once, under flock.
- **DURABILITY:** run `git push -u origin wip/p3-7` after EVERY commit.
