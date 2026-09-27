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
CHANGED — New `ledger.py`: `check()` before every paid call (raises `BudgetExceeded` when
spent + this call > `BUDGET_EUR`), `record()` appends `{time, provider, model, units,
est_cost_eur}` to `build/ledger.jsonl` after success; price helpers. `config.py`: `BUDGET_EUR`
from env (default 10, must be finite ≥ 0), prices marked "verify", `EST_*` sizes; `BUILD_DIR`
and `MUSIC_DIR` anchored to the project folder. `agents/llm.py`: one `_create()` that checks
(estimated input + full `max_tokens`) and records actual token usage. `voice.py`,
`image_agent.py`, `ai_video.py`: check + record; `cache_path()` helpers (also Manim). A Veo
timeout is recorded as spent. `pipeline.py`: `make_visual` re-raises `BudgetExceeded` instead
of falling back; `estimate_cost()` (cache-aware, typical and worst case) and `--estimate`.
`tests/conftest.py`: autouse guard: per-test build dir, and Anthropic (class level), requests,
google-genai blocked unless a test mocks them. `tests/test_budget.py` (13 tests). README
"Money" section, `.env.example`, DECISIONS D10, NEEDS_PIETRO.md (new).
CHECKED — verified: `make check` ruff clean, 27 passed (60 s). Mutations each fail a test:
no re-raise in the Veo or still branch; no TTS pre-check; `atmospheric=False` in the estimate;
`skip_critic` ignored; the old env test body with a fake `.env` `BUDGET_EUR=20` (the fixed
one passes). `--estimate` for 15 min: €2.06 typical / €3.94 worst without a Google key;
€16.87 with one (Veo €14.72). Run from another cwd it creates no `build/` there. Reviewer
subagent: 1 Critical (env test broke once `.env` sets `BUDGET_EUR`) fixed; warnings fixed
or logged (P0-7 Veo daily cap; P1-5 must reserve under a lock).
ASSUMED — The cap is cumulative across all runs (CLAUDE.md: "only if the spend ledger shows
budget left"). Failed calls aren't billed (not recorded), except Veo timeouts. Prices from
secondary sources (Claude: Anthropic's own model table): not verified. USD→EUR 0.92.
DIVERGED — Git branch as in P0-1. The reviewer's suggestions to add a CLAUDE.md rule and
deny rules to `.claude/settings.json` were not applied: those are Pietro's to change, and
are written up in NEEDS_PIETRO.md.
NEXT — P0-3 (live smoke): keys are missing in this environment, so it will be logged and
marked blocked. Then P0-4 (retries: Anthropic SDK `max_retries`, google-genai
`HttpRetryOptions`, own helper for ElevenLabs).

## 2026-09-27 P0-3 Live smoke test — BLOCKED (no keys)
CHANGED — `tests/test_live.py` (marked `live`): seeds a 1-chapter, 1-minute outline, runs the
real writer + critic + render with `allow_veo=False` into `build/smoke-test/` (spend goes to
the real ledger), then checks: Claude model in the ledger, an ElevenLabs call succeeded with
`speed` in voice_settings, at least one still came from `IMAGE_MODEL` (not all fell back to
Manim), film length 30-150 s. NEEDS_PIETRO.md: keys + network hosts. BACKLOG: P0-3 `[~]`.
CHECKED — verified: `pytest -m live tests/test_live.py` → 4 skipped ("missing ..."); `make
check` deselects it (27 passed). Key presence checked with `test -n`, values never printed.
Not verified: anything live.
ASSUMED — Seeding outline.json is how to get "1 chapter": the outline agent is told to plan
4-6 chapters, so `--minutes 1` alone wouldn't give one.
DIVERGED — The reviewer subagent was not run for this blocked task; `tests/test_live.py` is
included in the P0-4 review instead (it adds no offline code path).
NEXT — P0-4. One blocked task so far (stop rule: 3 in a row).

## 2026-09-27 P0-4 Retry with backoff on transient errors
CHANGED — `config.py`: MAX_RETRIES=4, RETRY_BASE_S/RETRY_JITTER_S/RETRY_MAX_S, GOOGLE_TIMEOUT_S.
`agents/llm.py`: `make_client()` with the SDK's `max_retries=4`. New `agents/google_client.py`:
one client with `HttpRetryOptions` + request timeout; `start_job_options()` retries only 429
for the Veo POST. New `retries.py`: `call()` with jittered exponential backoff, honours
`Retry-After` (clamped to 0..RETRY_MAX_S), `is_transient()` (TransientError, requests timeouts
and connection errors, google-genai 408/429/5xx, httpx timeouts/connect errors).
`agents/voice.py`: ElevenLabs POST through `retries.call`. `agents/image_agent.py`,
`ai_video.py`: client from `google_client`; Veo uses `source=GenerateVideosSource` (the
`prompt=` form is deprecated), records a started job it loses while polling as spent, and
retries the download (the SDK's retry loop skips downloads). `tests/test_retries.py` (19),
conftest `real_anthropic` / `real_google` fixtures (lift one guard each, for mock-transport
tests), fakes accept the new kwargs. `requirements*.txt`: floors raised to the tested versions
(anthropic 1.8, google-genai 2.25); httpx/httpx2 declared for tests. D11. test_live: €1 cap
per run and named failures. Doc backticks unescaped (a quoted-heredoc slip in earlier entries).
CHECKED — verified: red run first: Claude stopped after 3 attempts (SDK default), ElevenLabs
raised on the first 429. The two Google tests failed for another reason at first:
google-genai 2.25.0 refuses `generate_images` in API-key mode before sending anything (now in
P0-6); they were retargeted at `generate_content`/Veo. `make check`: ruff clean, 46 passed, no
warnings. Reviewer subagent: no Critical; its mutations (no retry options, default retries,
400 treated as transient, Retry-After ignored, no jitter/growth, no timeout retry) each fail a
test; `real_*` fixtures made 0 socket connects. My mutations after its warnings, each caught:
Veo POST retried on 5xx, lost job unrecorded, download unretried, no timeout, negative
Retry-After.
ASSUMED — A Veo 5xx on start may mean the job was accepted (hence no retry); a job lost while
polling is billed. GOOGLE_TIMEOUT_S=120 is enough for one image or a clip download.
DIVERGED — Anthropic retries use the SDK's own backoff curve (0.5 s → 8 s), not RETRY_BASE_S/
RETRY_MAX_S: reimplementing the SDK's loop would duplicate it (D11).
NEXT — P0-5 (A/V drift): root cause and fix already measured (adelay before loudnorm).
Reviewer note kept for later: a Google-image success-after-retry ledger test belongs in P0-6.

## 2026-09-27 P0-5 Scene audio loses its lead-in; narration drifts ahead of the visuals
CHANGED — `assembly.py`: scenes are whole video frames (`scene_frames`), audio exactly
frames × 1600 samples (`adelay` → `loudnorm` → `asetpts=N/SR/TB` → `apad`/`atrim`), video
capped with `-frames:v`; `crossfade_concat` counts offsets in whole frames
(`xfade_offsets`) and trims each clip's audio to its video before `acrossfade`; `add_music` re-stamps after `loudnorm` and cuts
to the video length. `utils.video_duration`. `config.AUDIO_RATE` (48000, divisible by FPS),
used by assembly and music. Tests: `decoded_audio`/`onset_s` helpers in conftest; per-clip
timing over 4 narration lengths × still/manim; a 12-scene chain; final mix at two lengths.
DECISIONS D12.
CHECKED — verified: red first (clip decoded 3.883 s vs 4.44 s target; narrated audio 10.26 s
vs video 11.93 s). My first fix (only moving `adelay` before `loudnorm`) passed the fixture
lengths; the reviewer subagent marked it Critical with evidence that `loudnorm` itself skips
timestamps (19 of 43 lengths short by up to 87 ms; 1.06 s drift over 40 scenes). I reproduced
it (3.06 / 3.344 s narrations lost 27 / 40 ms) before changing code, then fixed the rest.
Mutations, each caught: no re-stamp in the scene chain; no audio trim in the crossfade; no
re-stamp in the final mix (lost 39-75 ms at most lengths, measured); HEAD's assembly.py.
Re-review: no Critical; 44 lengths within one AAC frame, onset drift ≤ 7 ms over 40 scenes
(was −1.06 s). Its one Warning (offsets printed to 3 decimals start some fades a frame late)
fixed with a red-first test. `make check`: 54 passed. Voice level −18.2 LUFS vs −18.6 before (target −18; test tone).
ASSUMED — Up to one extra frame (33 ms) per scene is acceptable pacing-wise.
DIVERGED — Acceptance said "decoded audio equals the video duration (±1 AAC frame)"; met
literally now (it wasn't with the first fix, which compared to the `-t` target instead).
NEXT — P0-6 (Google model retirements). Then P0-7 (Veo daily cap), then P1. New: P1-8
(closing fade also dims the last narration, from the review). `pipeline.py` now builds the
bed to the video length, not the container's.

## 2026-09-27 P0-6 Google model retirements: stills and Veo defaults
CHANGED — `agents/image_agent.py`: stills via `generate_content` on `IMAGE_MODEL`
(`response_modalities=["IMAGE"]`, `ImageConfig(aspect_ratio, image_size)`); last finished (non-"thought") inline image is used; non-PNG answers converted to PNG with FFmpeg inside `atomic_output`; no image →
RuntimeError → Manim fallback (D7); cache key adds `IMAGE_SIZE`. `config.py`: `IMAGE_MODEL`
`gemini-3.1-flash-image`, `IMAGE_SIZE` 1K, `VEO_MODEL` `veo-3.1-generate-preview`,
`IMAGE_USD_PER_IMAGE` per size (1K 0.067) — all "verify"; the AFC log noise is off. `.env.example`, README, D13, NEEDS_PIETRO (smoke run
now worthwhile). New `tests/test_images.py` (5, real SDK on a mock transport); opt-in `live_veo` test; fakes in
test_cache/test_budget moved to `generate_content`. (Veo's `source=` call landed in P0-4.)
CHECKED — verified: red first: both success-path tests failed with google-genai 2.25.0's
"only supported in Gemini Enterprise Agent Platform mode" for `generate_images`; the
no-image → Manim test passed before too (kept as a guard). Request body asserted:
`responseModalities ["IMAGE"]`, `imageConfig.aspectRatio 16:9`, prompt text; a 503 then success
= 2 requests and 1 ledger entry. Reviewer subagent (first attempt cut off by a usage limit, rerun): no Critical; it ran five
response shapes (text+image, WebP, blocked prompt, IMAGE_SAFETY, thought image) through the real
SDK. Its Warning (the Veo name isn't live-checked) → opt-in `live_veo` test + NEEDS_PIETRO; its
suggestions applied (thought filter proven by a test that fails without it, price per size,
imageSize/model URL asserted). Fakes in older tests lacked the SDK's `thought` field; fixed
there, not in the code. `make check`: 59 passed.
Not verified: the retirement dates, the new model names and prices (secondary sources; Google's
pages unreachable here). P0-3's live run checks the image model; the Veo name needs the opt-in test.
ASSUMED — `gemini-3.1-flash-image` (not `-preview`) is the GA name Google's deprecation table
maps Imagen 4 to; 1K images are sharp enough for a 10% slow drift at 1080p.
DIVERGED — none from DECISIONS (D3 is restored, not changed). CLAUDE.md still says "Imagen" in
its flow line and file table; left for Pietro since it's his instruction file.
NEXT — P0-7 (Veo daily cap from the ledger).
