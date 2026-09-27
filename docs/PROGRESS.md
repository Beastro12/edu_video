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

## 2026-09-27 P0-7 Enforce "Veo: max 2 clips per day" in code
CHANGED — `config.VEO_MAX_PER_DAY` (2). `ledger.veo_clips_today()` counts today's (UTC) Veo
entries (the only ones billed in seconds). `agents/ai_video.py`: `DailyCapReached` raised before
the budget check (a cached clip is still served); a start POST failing with 5xx/timeout is now
recorded as "may have started". `pipeline.py`: "Veo daily cap reached …; using a still" log
line; `--estimate` prices only the clips still allowed today and prints how many are left.
New `tests/test_veo_cap.py` (4); one P0-4 test updated (see DIVERGED). README, D10 note.
CHECKED — verified: red first (cap test got kind 'ai'; estimate priced 3 clips instead of 1;
the yesterday test passed before too, now also proves image entries don't count). Mutation:
without the start-failure record, the Veo start test fails. Reviewer subagent: no Critical;
its Warning (a started-but-unanswered job escaped the cap) fixed; suggestions applied.
`make check`: 63 passed.
ASSUMED — Past the cap a Veo scene should become a still (not stop the run), per the task text.
DIVERGED — P0-4's test asserted "no ledger entry" after a 503 on the Veo start; it now expects
one "may have started" entry. Deliberate: over-counting only makes the budget and cap stricter,
and D10 already treats a lost job as billed.
NEXT — P1-1 (automated QA report). All P0 tasks are done except P0-3 (blocked on keys).

## 2026-09-27 P1-1 Automated video QA report
CHANGED — New `qa.py` (`python qa.py build/<slug>/<film>.mp4` → `qa.json`, exit 1 on failure):
duration vs the script's estimate (words at WORDS_PER_MIN + scene pads − crossfades, ±30%),
scene count (manifest vs script), integrated loudness (±1 LU of −16), true peak (≤ −1 dBTP), ducking depth (≥ 6 dB), black
segments outside the opening/closing fades, narration inside any crossfade. `pipeline.main`
runs it after rendering and exits 1 if it fails. `config.py`: QA_* thresholds, FADE_IN_S /
FADE_OUT_S, MASTER_TP_DBTP −2.0 (was −1.5), LEAD_IN_S 0.6→1.4 and TAIL_S 1.8→1.4 with asserts
(crossfade inside silence both sides; ≥ 1.2 s between narrations) (D14). `assembly.py`: one
shared `mix_graph()` (+ `music_graph`, `duck_graph`, `mix_key`) used by `add_music` and qa.py;
the mix graph gained a `[voice]volume=1.0` stage, so existing films re-mix once (FFmpeg only).
New `tests/test_qa.py` (13); `tests/test_live.py` checks QA; `tests/test_assembly.py` derives its onset search from
config. README "Quality check", D14, D15, NEEDS_PIETRO (pacing FYI).
CHECKED — verified: a healthy 6-scene test film (real assembly code, 320×180, one scene on
the dark Manim background) passes all 7 checks: 39.2 s vs 38.8 s estimate, −16.3 LUFS,
−11.1 dBTP, ducking 10.8 dB in the film (D2's hand measurement ~10 dB), no stray black, silent
crossfades. Faults, each failing its own check: film turned down 8 dB after mixing → loudness
only; voice at −34 LUFS (under the sidechain's absolute threshold) → ducking; film mixed with
DUCK_RATIO 1 → ducking (stale stamp) and, with QA on the same settings, ducking measured ~0;
mix wired to the raw bed → ducking; lead-in 0.6 s → narration_in_crossfade + ducking (no
pause long enough); black scene → black_frames; script 3× longer → duration; ~0 dBFS tone →
true_peak; CLI exit codes; pipeline exits 1 on a failed report. Master true peak on bursty
pink noise: −1.2 dBTP at TP −1.5, −1.8 at −2.0. Reviewer subagent: first pass Critical — my
first ducking check measured the settings, not the film (films with no ducking passed at
10.5 dB); fixed and re-reviewed: no Critical (its probes: stale → fail, mis-wired → 0.2 dB).
It also found the pix_th 0.10 false positive on our own background (verified; now 0.05).
`make check`: 76 passed (3 min 27 s; the QA tests build nine small films).
ASSUMED — ±30% for the duration check (voice speed is unknown before TTS). The new lead-in
and tail values (1.4 / 1.4 s) are a pacing choice, flagged in NEEDS_PIETRO.
DIVERGED — D5's rule extended by D14 (evidence in D14); LEAD_IN_S/TAIL_S changed. (An earlier
draft of this entry claimed a with/without-sidechain comparison was equivalent to "speech vs
pauses"; the reviewer showed it measured config, not the film. The shipped check measures the
film, speech vs pauses, as the acceptance says.)
NEXT — P1-2 (vision review of stills). Risks: `make check` is getting slow (~3.5 min); the
ducking margin (4.8 dB) is untested on real music — confirm on the first live run.

## 2026-09-27 P1-2 Visual review of stills
CHANGED — New `agents/still_critic.py` (Claude vision verdict `{ok, problems}` on a JPEG
preview + the scene's concept and description). `agents/image_agent.py`: `reviewed_still`
(generate → review → regenerate with a "what to show" prompt plus earlier problems,
STILL_REVIEW_RETRIES=2 → `StillRejected`), per-scene `review_<key>.json` written after every
attempt, `is_decided`. `agents/llm.py`: `structured(images=...)` with a text + fixed per-image
token estimate; a tool call cut off at max_tokens raises. `utils.preview_jpeg` (long edge ≤
PREVIEW_PX). `pipeline.py`: `make_visual` uses the review ("still failed review …; falling back
to Manim"); manifest links each scene's review; `--estimate` prices undecided stills (image +
review), rejected ones as Manim, and a worst case with every retry plus Manim. `config.py`:
STILL_REVIEW_RETRIES, STILL_REVIEW_MAX_TOKENS, PREVIEW_PX, EST_IMAGE_TOKENS,
EST_STILL_REVIEW_TOKENS. Tests: `tests/test_still_review.py` (12); conftest `stills_approved`
for tests not about the review; the paid-call guard is now a BaseException so the fallbacks'
`except Exception` can't hide it. README, D16.
CHECKED — verified: red first (7 of 8 original tests failed: no review happened). Reviewer
subagent: no Critical; its warnings fixed with tests that fail without the fix (mutations):
concept not in the review key; base64 counted as text (a real 1376×768 preview is >20k base64
chars; estimates for tiny and real images now equal and < €0.02); log saved only at the end
(a crash re-reviewed attempt 1 and accepted the rejected image); guard as AssertionError
(swallowed into Manim). `--estimate` for rejected / accepted-but-missing / accepted states
tested. `make check`: 88 passed (3 min 22 s).
ASSUMED — 2 retries then Manim (from the task text); the reviewer's standard ("artistic
simplification is fine when it doesn't teach something wrong").
DIVERGED — test_budget's estimate fixture now marks scene 2 as decided with an accepted still
on disk (a bare still file no longer means "nothing to buy": its review would still be owed).
NEXT — P1-3 (Manim timing fit). Not verified live: how strict the reviewer is on real images
(too strict → many Manim fallbacks and extra spend; watch the review logs on the first run).

## 2026-09-27 P1-3 Manim timing fit
CHANGED — `agents/manim_agent.py`: `_fit_timing` runs after a successful render (outside the
error-fix loop): if the render is more than `MANIM_MAX_SHORTFALL` (25%) shorter than its scene,
Claude is asked once to retime it (measured and target lengths given, "not longer"); the render
with the smaller timing error is kept, an overshoot counting double (the assembler cuts the end
off). Fail-soft: any problem while retiming keeps the original; only BudgetExceeded stops the
run. `config.MANIM_MAX_SHORTFALL`. `--estimate` worst case counts the retime call. New
`tests/test_manim_timing.py` (7); a cache test isolates itself from retiming; two estimate
expectations include the retime.
CHECKED — verified: red first (a 4.0 s render for a 10 s scene stayed 4.0 s; Claude asked once
instead of twice). Now measured before 4.0 s → after 9.5 s (mocked renderer whose clip length
comes from the code). Retime that fails, is shorter (3 s) or overshoots (15 s) keeps the
original; exactly one extra Claude call. Reviewer subagent: no Critical; its warnings fixed
(retime inside the render try could discard a good render and trigger a fix round; estimate
missed the call). Mutations caught: symmetric timing error; a retiming error inside the render
try. `make check`: 95 passed (3 min 21 s).
ASSUMED — Overshoot counts double; renders cached before this change are not retimed (the
threshold isn't in the cache key, which is fine: only new renders are affected).
DIVERGED — none.
NEXT — P1-4 (YouTube metadata + SRT). Not verified live: whether Claude's retimes land close
to the target on real Manim code.

## 2026-09-27 P1-4 YouTube metadata
CHANGED — New `metadata.py`: `write_metadata(work)` → `metadata.json` (title, description =
`YOUTUBE_DESCRIPTION` + chapter list, chapters, duration) and `subtitles.srt`. Chapter starts are
each chapter's first scene start in the film (the crossfade arithmetic over the clips' video
lengths); chapters under 10 s merge (a short opening keeps 00:00 and takes the next title,
others go to the chapter before, the last is measured to the film's end); fewer than 3 →
no list in the description. SRT: each scene's narration spans its measured audio after the
lead-in, split into cues of whole sentences (≤ 84 chars, two lines near 42) timed by length.
YouTube limits: `<`/`>` removed, title clipped to 100 chars, description > 5,000 bytes refused.
`pipeline.main` writes it after rendering. The test film builder moved to `tests/conftest.py`
(`build_film`, with narrations/chapters; `small` fixture). README, `config.YOUTUBE_DESCRIPTION`,
NEEDS_PIETRO (FYI on the description text).
CHECKED — verified: chapter starts and each scene's first/last cue match voice onsets/ends
measured in the decoded narrated.mp4 (±30 ms / ±80 ms incl. mp3 padding); chapter rules unit-
tested (short opening / middle / last / all short); hour timestamps; cue packing (no flash
cues from "Dr." or "…"); YouTube limits. Reviewer subagent: no Critical; it measured onsets
itself (all first cues within 10 ms of the voice, in narrated.mp4 and the final film). Its
warnings fixed (tests re-derived the code's arithmetic → now measure the film; merge cases
untested → unit tests; YouTube limits). Mutations caught: subtitles ignoring the lead-in; wrong
crossfade arithmetic (a "container duration" mutant was equivalent: container = video length
since P0-5). `make check`: 106 passed (3 min 49 s; metadata tests now build one film).
ASSUMED — The title comes from script.json (what you'd edit), chapter titles from outline.json.
The description opening line is a placeholder (NEEDS_PIETRO).
DIVERGED — none.
NEXT — P1-5 (parallel TTS and image generation, with budget reservation under a lock).

## 2026-09-27 P1-5 Parallel generation
CHANGED — `pipeline.py`: `generate_assets` (narration for all scenes, then visuals) on
`config.WORKERS` (4) threads via `in_parallel` (ordered results; the first failure cancels what
hasn't started, sets `ledger.stopping` until in-flight calls finish, then clears it); clips are
still built sequentially. `ledger.py`: `reserve()` holds each call's estimated cost under an
RLock (spent + held + this ≤ budget) until the call has recorded; reads under the lock;
`stopping` / `interrupted` events. `utils.py`: `output_lock` per output (two scenes with the
same cache key pay once), unique temp names, atomic `save_json`, `log()` single-write lines.
Voice / image / Veo / Manim / Claude call sites: lock → re-check cache → reserve → buy. Veo jobs
run one at a time (daily cap can't be raced) and are abandoned only on Ctrl-C. Threaded
messages carry the scene id. New `tests/test_parallel.py` (9); conftest resets the stop events
and holds per test; test_cache fakes name files by hash (thread-safe). README, D17.
CHECKED — verified: red first: 8 concurrent narrations with budget for 3 bought 8 (now 3);
duplicate line/picture across scenes bought 4× (now 1×); a failure let 20/20 items start (now
< 6); no refusal after a stop. Wall-clock, 8 scenes with 0.2 s fake latency per TTS and image
call (seeded jitter): ~3.5 s sequential → ~1.35 s on 4 workers (3 runs: 3.52/1.29, 3.87/1.35,
3.45/1.41); the ceiling below 4× is each wave waiting for its slowest call plus ~0.3 s of
sequential local work. Reviewer subagent: first pass 1 Critical (duplicate keys paid twice and
crashed on the shared temp file — I had flagged it) + 3 warnings; fixed and re-reviewed (its
repros: 60 → 30 paid calls, 20 → 5 started); second pass 2 warnings (stop flag leaked across runs;
a budget stop abandoned a paid Veo job) fixed, each with a test that fails without the fix.
`make check`: 115 passed (3 min 51 s).
ASSUMED — 4 workers; clip rendering stays sequential (FFmpeg is already multi-threaded).
DIVERGED — D10's "check-then-call is not atomic" limit is now closed within one process (D17);
two processes sharing the ledger still aren't coordinated.
NEXT — P1-6 (faster still rendering). Risk: Manim renders may now run 4 at a time (CPU heavy).

## 2026-09-27 P1-6 Faster still rendering
CHANGED — `assembly.py`: `clip_encoder(kind)`: still and Manim scene clips are encoded with
`config.CLIP_X264` (x264 veryfast crf 14); Veo clips and the film keep `FILM_X264` (medium crf 18,
as before); `_still_filter` takes its upscale from `config.KEN_BURNS_UPSCALE` (3, unchanged).
New `scripts/bench_stills.py`: the benchmark every D18 number comes from (times the real
`build_scene_clip` / `crossfade_concat`; SSIM against lossless renders; motion per frame;
`--compare IMAGE` renders the drifts to watch). `tests/test_assembly.py`: which encoder each clip
kind and the film get, and that the upscale comes from config. D18 written; README render times;
numpy in requirements-dev (the benchmark); BACKLOG P1-9, P1-10 (`[~]`), P2-5; NEEDS_PIETRO: look at
the drift.
CHECKED — verified with the benchmark (D18, one run, 4 cores, 20 s scenes): still clip step
15.06 → 8.42 s (pan), 18.01 → 9.31 s (push-in), −44% / −48% (43–49% in all five runs made);
film SSIM −0.00007 / −0.00034; motion unchanged (jitter 0.685 → 0.683, 0.470 → 0.465 px); Manim
clip −13%, SSIM −0.00003. NOT met end to end: a still scene including the film's encode is
−26% / −24% (P1-9). Go/no-go gates fixed before the last runs (clip ≥ −40%, film SSIM within
0.0005, jitter within 0.01 px) passed. The test fails when every kind gets the fast encoder and
when the upscale is hard-coded (both mutations run). `--compare` run: 2 min 10 s. Reviewer:
pass 1, one Critical (D18's numbers could not be reproduced from the script) plus warnings; while
fixing it I found three mistakes of my own, each diagnosed before changing anything: unseeded test
sources (numbers moved between runs; seeds now fixed and checked by hash), a shift estimator pulled
to whole pixels (0.06 px error; the phase-slope one is off by ≤ 0.0022 px), and a push-in jitter
measure that mixed speeds (0.54 → 0.65 for identical motion; now 240 px strips). Pass 2: no
Critical; 5 warnings, all fixed (the stop-go is on even pixels because zoompan runs in yuv420p —
checked with showinfo — and the yuv444p variant it suggested is now measured; step statistics
printed by the script; 3 repeats and the observed ±8% timing spread stated; scope of the tick;
film/clip ratio) and 4 suggestions taken. Those fixes were checked by me against the benchmark
output, not re-reviewed. `make check`: 116 passed (4 min 1 s) on the committed state.
ASSUMED — "still-scene render time" in the criterion means the still's clip step (the README had
named the drift as the heaviest step); by that reading ≥40% is met, end to end it isn't.
"Without visible jitter": the motion is unchanged; whether the drift's existing stop-go is visible
is not verified (P1-10, needs Pietro). Veo clips keep the old encoder (grain: −0.0067 SSIM).
DIVERGED — nothing in DECISIONS reversed (no entry fixed the encoder). From my own earlier output:
`_still_filter`'s docstring claim that 3× keeps zoompan's positioning "from producing visible
jitter" is only partly true (D18); reworded. From my own gate: the push-in jitter gate I set first
failed (0.540 → 0.649) on a 480 px strip; I diagnosed the instrument (identical motion in narrow
strips), replaced it, and re-measured everything with the replacement.
NEXT — P1-7 (don't re-attempt failed paid generations). Risk: none known from P1-6; existing
builds re-render their clips once (free), and old clips stay on disk until P2-5.

## 2026-09-27 P1-7 Don't re-attempt failed paid generations
CHANGED — new `failures.py`: `Failures` (per film `build/<slug>/failures.json`: what, scene,
error, time, by the generation's cache key), `FailedEarlier`, `lasting()` (which errors are worth
remembering). `ai_video.render_scene` and `image_agent.render_still` check it before paying,
record a lasting failure, clear the entry on success. `pipeline.py`: `make_visual` passes it and
falls back on `FailedEarlier`; `generate_assets` / `render_film` take `retry_failed`; CLI
`--retry-failed`; `--estimate` counts the fallback for failed scenes (`image_agent.next_still`
finds the next review attempt's file). `utils.py`: `CommandFailed` (FFmpeg/Manim errors, a
RuntimeError subclass), `redact()`, applied in `log()`. README, D19. New `tests/test_failures.py`
(18).
CHECKED — verified: red first (no module; the estimate counted €2.94 for a Veo clip that would
not be bought). The mocked second run makes no provider call for a failed Veo scene, a failed
still, or a failure on a later review attempt; `--retry-failed` asks again and a success clears
the entry; budget stop, daily cap, Ctrl-C, a network error and a full disk are not recorded;
two scenes sharing a failed key ask once even when retrying; 32 concurrent records all kept; no
key in failures.json or the output. 16 mutants, each killed (the lock one 5 of 5 times; the
concurrent test passes 10 of 10 with the lock). Reviewer: no Critical; 4 warnings, all fixed
— the main one showed my D19 claim false (I wrote transient errors "cannot be told apart", but
`retries.is_transient` does exactly that; they were being recorded, so a network blip would have
pushed a film's remaining scenes to Manim for good) — plus the retry double-pay, 5 untested
behaviours, and unredacted log lines; 3 suggestions taken. Fixes checked by the mutants, not
re-reviewed. `make check`: 134 passed (3 min 48 s).
ASSUMED — a failure is kept until `--retry-failed` (no expiry); a changed timeout or key doesn't
clear it (README says to use `--retry-failed`). A still rejected for good by Claude stays P1-2's
decision; `--retry-failed` doesn't reopen it (delete its `review_*.json` to).
DIVERGED — none from DECISIONS.
NEXT — P1-8 (the closing fade dims the last narration).
