# Backlog

Format: `- [ ] ID — title` then acceptance criteria. `[x]` done, `[~]` blocked, `[-]` dropped.
Work top to bottom within a priority.

## P0 — correctness and safety

- [ ] P0-1 — Content-hash cache keys (known bug)
  Cache files are keyed by scene number. Editing a narration after a render reuses
  the stale mp3/clip, and changing a chapter's scene count shifts every later ID onto
  the wrong cached files. Also: once `script.json` exists, edits to `chapter_XX.json`
  are ignored.
  Accept: cache keys include a hash of the inputs that produced them (narration text
  + voice settings for audio; visual_description + type + target length for visuals;
  clip hashes for assembly). Editing one scene's narration regenerates only that
  scene's audio, clip, and the final assembly. A test proves both cases. `narrated.mp4`
  and the final video are rebuilt when any clip changes.

- [ ] P0-2 — Spend ledger and hard cap
  Accept: every paid call appends `{time, provider, model, units, est_cost_eur}` to
  `build/ledger.jsonl`. Prices live in `config.py`, marked "verify". `BUDGET_EUR` from
  env (default 10) is checked before each paid call; exceeding it raises and stops
  the run. `python pipeline.py "<topic>" --estimate` prints the projected cost without
  calling anything. Offline tests with mocked providers.

- [ ] P0-3 — Live smoke test (needs keys; run once)
  Accept: `tests/test_live.py` marked `live` renders a ~1-minute, 1-chapter film with
  `--no-ai-video`. Verifies and records in PROGRESS: Claude model name accepted,
  ElevenLabs accepts `speed` in voice_settings, Imagen model name and response shape
  (`generated_images[0].image.image_bytes`). Fix config/code for anything that fails.
  If keys are missing, log it in NEEDS_PIETRO.md, mark `[~]`, and continue with other tasks.

- [ ] P0-4 — Retry with backoff on transient errors
  Accept: 429/5xx/timeouts from Anthropic, ElevenLabs and Google retry with jittered
  exponential backoff (max 4). Non-transient errors fail fast. Tested with mocks.

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

- [ ] P1-6 — Faster still rendering
  Accept: still-scene render time drops ≥40% without visible jitter. Measure the
  current 3× upscale approach vs alternatives; record numbers in DECISIONS.

## P2 — scale and polish

- [ ] P2-1 — Batch mode from `topics.txt`, with a variety check against past titles
  and outlines (avoid near-duplicate videos; YouTube demonetises mass-produced content).
- [ ] P2-2 — Thumbnail generation (1280×720, no text baked in by the image model; title
  text added by FFmpeg/Pillow).
- [ ] P2-3 — Structured run log and a `run_report.md` per video (costs, timings, QA,
  fallbacks used).
- [ ] P2-4 — README refresh reflecting everything above.
