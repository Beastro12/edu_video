# Progress log

## 2026-09-27 BASELINE Handoff from claude.ai chat
CHANGED — Initial codebase, offline tests (`tests/`), Makefile gate, Claude Code setup.
CHECKED — `make check`: ruff clean, 6 tests pass (assembly timing, formats, crossfade
arithmetic, music bed length, final loudness, chapter flow + caching with Claude mocked).
Manim render path verified with a real render (1080p30). Voice ducking ~10 dB and
seamless music loop measured by hand (see DECISIONS D2, D6).
ASSUMED — Budget default €10 for live calls; Imagen for stills; ~120 wpm pacing.
DIVERGED — none
NEXT — P0-1 (cache bug). Not verified live: Imagen model name/response, ElevenLabs
`speed`, Veo model name, current Claude model name.

## 2026-09-27 P0-1 Content-hash cache keys
CHANGED — `utils.py`: `content_key`, `file_hash`, `atomic_output` (write to `.partial`, then
rename), `is_fresh`/`stamped_output` (`<name>.key` stamps). `agents/voice.py`, `image_agent.py`,
`ai_video.py`, `manim_agent.py`: outputs named by a hash of their inputs in `audio/` and
`visuals/`; Manim class name fixed (`DocScene`) so the scene id isn't in the brief.
`assembly.py`: clips named by hash of visual bytes + narration bytes + exact filters/encode args;
`narrated.mp4` and the final video rebuilt when their stamp doesn't match (the final was
previously re-mixed on every run). `agents/music.py`: bed and placeholder pad stamped.
`pipeline.py`: `render_film()` split out of `main()`; writes `manifest.json` (scene → files);
`get_script` tracks a hash of outline + chapters in `script.meta.json`: chapter edits
rebuild script.json, hand edits to script.json are kept, edits to both raise.
`config.ASPECT_RATIO` added. Tests: `tests/test_cache.py` (5), 3 new in
`tests/test_script_flow.py`; `test_assembly.py` passes a directory to `build_scene_clip`.
README cache section, DECISIONS D9, `.claude/agents/video-qa.md` path updated.
CHECKED — verified: red run first (after only extracting `render_film`): the 4 original
test_cache tests failed on their assertions (stale mp3 reused after an edit; after inserting
a scene the old code paid for the wrong text; final re-mixed on an unchanged rerun; Manim keyed
by id) and 2 of 3 script-flow tests failed (chapter edits ignored; no conflict detection).
Now `make check`: ruff clean, 14 passed (60 s; was 6 passed, 34 s). Reviewer subagent: no
Critical; its mutation runs (TTS key without text, narrated/final rebuilt only when missing,
still key with scene id) each broke a test. Its 6 applicable warnings fixed; a same-length
narration edit test was added and verified to fail when the narration hash is dropped from
the clip key.
ASSUMED — A narration edit should not re-buy an unchanged still/Veo clip, so their keys leave
out type and target length (the generators never see them). Manim keys include the whole
brief, so a Manim scene's visual IS regenerated when its narration changes (paid Claude call).
When script.json exists without a meta file (older build), it is adopted as the baseline.
DIVERGED — (1) BACKLOG listed "visual_description + type + target length" for visual keys;
still/Veo keys use prompt + model + aspect only, for the reason above (recorded in D9).
(2) Git: CLAUDE.md says commit on `auto/dev` and never push. This runs in a cloud session
whose container is discarded, set up to develop and push on `claude/new-project-setup-b6eeid`
in the `Beastro12/Mise` repo (project in `edu_video/`). Commits go there and are pushed to
that branch only (no PR, nothing merged); without the push the work would be lost.
NEXT — P0-2 (spend ledger). Risks found: P0-5 (verified A/V drift bug, pre-existing) and P0-6
(Imagen 4 and Veo 3.0 retired per secondary sources, not verified) added to BACKLOG. Reviewer
suggestion not taken: keying `narrated.mp4` by clip names instead of hashing ~1 GB of clips per
run; kept byte hashes for robustness (measure the cost at the first long render).

## 2026-09-27 P0-2 Spend ledger and hard cap
CHANGED — New \`ledger.py\`: \`check()\` before every paid call (raises \`BudgetExceeded\` when
spent + this call > \`BUDGET_EUR\`), \`record()\` appends \`{time, provider, model, units,
est_cost_eur}\` to \`build/ledger.jsonl\` after success; price helpers. \`config.py\`: \`BUDGET_EUR\`
from env (default 10, must be finite ≥ 0), prices marked "verify", \`EST_*\` sizes; \`BUILD_DIR\`
and \`MUSIC_DIR\` anchored to the project folder. \`agents/llm.py\`: one \`_create()\` that checks
(estimated input + full \`max_tokens\`) and records actual token usage. \`voice.py\`,
\`image_agent.py\`, \`ai_video.py\`: check + record; \`cache_path()\` helpers (also Manim). A Veo
timeout is recorded as spent. \`pipeline.py\`: \`make_visual\` re-raises \`BudgetExceeded\` instead
of falling back; \`estimate_cost()\` (cache-aware, typical and worst case) and \`--estimate\`.
\`tests/conftest.py\`: autouse guard: per-test build dir, and Anthropic (class level), requests,
google-genai blocked unless a test mocks them. \`tests/test_budget.py\` (13 tests). README
"Money" section, \`.env.example\`, DECISIONS D10, NEEDS_PIETRO.md (new).
CHECKED — verified: \`make check\` ruff clean, 27 passed (60 s). Mutations each fail a test:
no re-raise in the Veo or still branch; no TTS pre-check; \`atmospheric=False\` in the estimate;
\`skip_critic\` ignored; the old env test body with a fake \`.env\` \`BUDGET_EUR=20\` (the fixed
one passes). \`--estimate\` for 15 min: €2.06 typical / €3.94 worst without a Google key;
€16.87 with one (Veo €14.72). Run from another cwd it creates no \`build/\` there. Reviewer
subagent: 1 Critical (env test broke once \`.env\` sets \`BUDGET_EUR\`) fixed; warnings fixed
or logged (P0-7 Veo daily cap; P1-5 must reserve under a lock).
ASSUMED — The cap is cumulative across all runs (CLAUDE.md: "only if the spend ledger shows
budget left"). Failed calls aren't billed (not recorded), except Veo timeouts. Prices from
secondary sources (Claude: Anthropic's own model table): not verified. USD→EUR 0.92.
DIVERGED — Git branch as in P0-1. The reviewer's suggestions to add a CLAUDE.md rule and
deny rules to \`.claude/settings.json\` were not applied: those are Pietro's to change, and
are written up in NEEDS_PIETRO.md.
NEXT — P0-3 (live smoke): keys are missing in this environment, so it will be logged and
marked blocked. Then P0-4 (retries: Anthropic SDK \`max_retries\`, google-genai
\`HttpRetryOptions\`, own helper for ElevenLabs).
