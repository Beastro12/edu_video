# Backlog

Format: `- [ ] ID — title` then acceptance criteria. `[x]` done, `[~]` blocked, `[-]` dropped.
Work top to bottom within a priority.

## P0 — correctness and safety

- [x] P0-1 — Content-hash cache keys (known bug)
  Cache files are keyed by scene number. Editing a narration after a render reuses
  the stale mp3/clip, and changing a chapter's scene count shifts every later ID onto
  the wrong cached files. Also: once `script.json` exists, edits to `chapter_XX.json`
  are ignored.
  Accept: cache keys include a hash of the inputs that produced them (narration text
  + voice settings for audio; visual_description + type + target length for visuals;
  clip hashes for assembly). Editing one scene's narration regenerates only that
  scene's audio, clip, and the final assembly. A test proves both cases. `narrated.mp4`
  and the final video are rebuilt when any clip changes.

- [x] P0-2 — Spend ledger and hard cap
  Accept: every paid call appends `{time, provider, model, units, est_cost_eur}` to
  `build/ledger.jsonl`. Prices live in `config.py`, marked "verify". `BUDGET_EUR` from
  env (default 10) is checked before each paid call; exceeding it raises and stops
  the run. `python pipeline.py "<topic>" --estimate` prints the projected cost without
  calling anything. Offline tests with mocked providers.

- [~] P0-3 — Live smoke test (needs keys; run once) [blocked: no API keys in this environment; tests/test_live.py written, see NEEDS_PIETRO.md]
  Accept: `tests/test_live.py` marked `live` renders a ~1-minute, 1-chapter film with
  `--no-ai-video`. Verifies and records in PROGRESS: Claude model name accepted,
  ElevenLabs accepts `speed` in voice_settings, Imagen model name and response shape
  (`generated_images[0].image.image_bytes`). Fix config/code for anything that fails.
  If keys are missing, log it in NEEDS_PIETRO.md, mark `[~]`, and continue with other tasks.

- [x] P0-4 — Retry with backoff on transient errors
  Accept: 429/5xx/timeouts from Anthropic, ElevenLabs and Google retry with jittered
  exponential backoff (max 4). Non-transient errors fail fast. Tested with mocks.

- [x] P0-5 — Scene audio loses its lead-in; narration drifts ahead of the visuals
  Found during P0-1 (verified, FFmpeg 6.1.1): in `build_scene_clip`, `loudnorm` followed
  by `adelay` emits the lead-in silence frames without valid timestamps (`ashowinfo`
  shows `pts:NOPTS`); with `-t` on the AAC encode those samples are lost, so each clip's
  audio holds ~0.55 s fewer samples than its container claims (3.39 s reported, 2.84 s
  decoded). `crossfade_concat` works on decoded samples, so the loss accumulates: 3 scenes
  gave narrated.mp4 audio 6.99 s vs video 8.67 s. In a 15-min film narration would drift
  about a minute ahead of its visuals and run into crossfades (breaks D1 and D5). The
  existing duration tests pass because they read container duration (= video).
  Accept: for every scene clip, decoded audio duration equals the video duration
  (±1 AAC frame) and narration onset is at `LEAD_IN_S` (±20 ms); narrated.mp4 decoded
  audio matches its video (±0.05 s). Tests measure decoded samples, not container duration.

- [ ] P0-6 — Google model retirements: stills and Veo defaults no longer exist
  Found during P0-2 research (NOT verified at Google's own pages, which the sandbox can't
  reach; several independent secondary sources agree): the Gemini API shut down
  `imagen-4.0-*-generate-001` on 2026-08-17 (successor `gemini-3.1-flash-image`, served
  through `generate_content`, response parts carry `inline_data` instead of
  `generated_images[0].image.image_bytes`) and `veo-3.0-generate-001` on 2026-06-30
  (successor `veo-3.1-generate-preview`). With today's defaults every still would fall
  back to Manim, so D3's ~60% stills silently becomes 0%.
  Verified locally during P0-4 (google-genai 2.25.0): `generate_images` raises "only supported
  in Gemini Enterprise Agent Platform mode, not in Gemini Developer API mode" before sending
  any request, so with a `GOOGLE_API_KEY` today's code never produces a still. Also
  `generate_videos(prompt=...)` is deprecated in favour of `source=`.
  Accept: `image_agent` generates stills with the Gemini image model via the SDK's
  documented `generate_content` path (16:9, image-only output), mocked offline test of the
  new response shape including "no image part" → fallback; `IMAGE_MODEL`/`VEO_MODEL`
  defaults updated, marked "verify"; prices updated; DECISIONS entry; P0-3's live check
  covers the new names. If the SDK installed doesn't expose the needed types, log it.

- [ ] P0-7 — Enforce CLAUDE.md's "Veo: max 2 clips per day" in code
  Found in the P0-2 review: a run with a Google key asks Veo for one clip per chapter (4-6),
  and nothing enforces the daily limit.
  Accept: `VEO_MAX_PER_DAY` in config (default 2) counts today's (UTC) Veo entries in the
  ledger; past it, Veo is skipped with a log line and the scene falls back to a still
  (not a run stop). `--estimate` counts only the Veo clips still allowed today. Mocked test.

## P1 — quality

- [ ] P1-1 — Automated video QA report
  Accept: `qa.py build/<slug>/<final>.mp4` writes `qa.json` and exits non-zero on
  failure. Checks: duration vs script estimate, integrated loudness within ±1 LU of
  target, true peak ≤ −1 dBTP, ducking depth ≥ 6 dB (music level during speech vs
  pauses), no black frames except the opening/closing fades (`blackdetect`), no
  narration overlapping a crossfade. Pipeline runs it at the end.

- [ ] P1-2 — Visual review of stills
  Accept: after generating a still, a Claude vision call checks it against the scene
  (text/letters in the image, physically wrong depictions, unsettling imagery for
  sleep viewing). On failure, regenerate with the critique appended (max 2), then fall
  back to Manim. Result logged per scene.

- [ ] P1-3 — Manim timing fit
  Accept: if a render is >25% shorter than the target, ask Claude once to retime the
  scene. Measured before/after in a mocked test.

- [ ] P1-4 — YouTube metadata
  Accept: `build/<slug>/metadata.json` + `subtitles.srt`: title, description,
  chapter timestamps taken from real clip boundaries (YouTube needs 00:00 first and
  ≥10 s chapters), SRT from narration timing per scene.

- [ ] P1-5 — Parallel generation
  Accept: TTS and image generation for independent scenes run concurrently
  (configurable workers, default 4), respecting P0-4 retries and P0-2 budget checks.
  Wall-clock time before/after recorded.
  (Added after the P0-2 review:) the budget check must reserve each call's cost under
  a lock before the call and settle it after, so concurrent calls can't overshoot.

- [ ] P1-6 — Faster still rendering
  Accept: still-scene render time drops ≥40% without visible jitter. Measure the
  current 3× upscale approach vs alternatives; record numbers in DECISIONS.

- [ ] P1-7 — Don't re-attempt failed paid generations on every rerun
  Found during P0-1: `make_visual` falls back Veo → still → Manim, but only successes are
  cached, so every rerun of a film asks Veo (and Imagen) again for scenes that already
  failed, paying or waiting up to 10 min each time.
  Accept: a failed generation is recorded per content key in `build/<slug>/failures.json`
  with the error; reruns go straight to the fallback unless `--retry-failed` is passed.
  Mocked test: second run makes no provider call for a previously failed scene.

- [ ] P1-8 — The closing fade dims the last narration
  Found in the P0-5 review (measured by the reviewer): in `add_music`, `afade=t=out` runs
  after `amix`, so it fades the voice as well as the music; with a steady tone the voice is
  ~10 dB down by the end of the last scene.
  Accept: only the music bed fades out (the voice isn't touched); a test measures the last
  narration's level against an earlier one (within 1 dB) and the bed's level falling.

## P2 — scale and polish

- [ ] P2-1 — Batch mode from `topics.txt`, with a variety check against past titles
  and outlines (avoid near-duplicate videos; YouTube demonetises mass-produced content).
- [ ] P2-2 — Thumbnail generation (1280×720, no text baked in by the image model; title
  text added by FFmpeg/Pillow).
- [ ] P2-3 — Structured run log and a `run_report.md` per video (costs, timings, QA,
  fallbacks used).
- [ ] P2-4 — README refresh reflecting everything above.
