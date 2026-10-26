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
  (Since P0-6 the stills come from the Gemini image model through `generate_content`, so
  read "Imagen" as `IMAGE_MODEL` and its `parts[].inline_data` answer; `test_live.py` runs
  the current code.)
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

- [x] P0-6 — Google model retirements: stills and Veo defaults no longer exist
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

- [x] P0-7 — Enforce CLAUDE.md's "Veo: max 2 clips per day" in code
  Found in the P0-2 review: a run with a Google key asks Veo for one clip per chapter (4-6),
  and nothing enforces the daily limit.
  Accept: `VEO_MAX_PER_DAY` in config (default 2) counts today's (UTC) Veo entries in the
  ledger; past it, Veo is skipped with a log line and the scene falls back to a still
  (not a run stop). `--estimate` counts only the Veo clips still allowed today. Mocked test.
- [x] P0-8 — A temporary google-genai Client closes its own HTTP client: every still fails
  Found in P3-6's review (2026-10-25), reproduced by the orchestrator with the real SDK and the
  transport faked.
  - `image_agent.py:53` calls `google_client.make().models.generate_content(...)` inline. The
    Client is collected at once, and its `__del__` closes its own httpx client.
  - So the request never goes out: "Cannot send a request, as the client has been closed".
  - On a real run every still falls back to Manim and the thumbnail to a frame, as in D13.
  - Offline tests replace `google_client.make()` wholesale, so they never saw it.
  Accept:
  - every genai call keeps its Client referenced for the whole call;
  - a real-SDK regression test, with the transport faked, fails on the base and passes fixed;
  - an import-resolving AST guard flags any attribute read on a temporary Client, with a
    self-test.
- [ ] P0-9 — The Veo start POST is retried after a timeout, against D11 (found 2026-10-26)
  google-genai 2.25.0 retries transport timeouts and connect errors whatever
  `HttpRetryOptions.http_status_codes` says (`_api_client.py:575-578`). So
  `start_job_options()`'s "429 only" doesn't hold. A start that times out after Google
  accepted it is sent again and billed twice (about €2.94). The SDK swallowed the first
  timeout, so the ledger records one job: the budget and the daily cap undercount.
  Reproduced offline: a ReadTimeout and then a 200 gave 2 POSTs.
  Accept: one SDK attempt on the start; our code retries a 429 only; a timeout or 5xx makes
  exactly 1 POST and 1 "may have started" entry. Real-SDK tests with the transport faked
  (D30).

## P1 — quality

- [x] P1-1 — Automated video QA report
  Accept: `qa.py build/<slug>/<final>.mp4` writes `qa.json` and exits non-zero on
  failure. Checks: duration vs script estimate, integrated loudness within ±1 LU of
  target, true peak ≤ −1 dBTP, ducking depth ≥ 6 dB (music level during speech vs
  pauses), no black frames except the opening/closing fades (`blackdetect`), no
  narration overlapping a crossfade. Pipeline runs it at the end.

- [x] P1-2 — Visual review of stills
  Accept: after generating a still, a Claude vision call checks it against the scene
  (text/letters in the image, physically wrong depictions, unsettling imagery for
  sleep viewing). On failure, regenerate with the critique appended (max 2), then fall
  back to Manim. Result logged per scene.

- [x] P1-3 — Manim timing fit
  Accept: if a render is >25% shorter than the target, ask Claude once to retime the
  scene. Measured before/after in a mocked test.

- [x] P1-4 — YouTube metadata
  Accept: `build/<slug>/metadata.json` + `subtitles.srt`: title, description,
  chapter timestamps taken from real clip boundaries (YouTube needs 00:00 first and
  ≥10 s chapters), SRT from narration timing per scene.

- [x] P1-5 — Parallel generation
  Accept: TTS and image generation for independent scenes run concurrently
  (configurable workers, default 4), respecting P0-4 retries and P0-2 budget checks.
  Wall-clock time before/after recorded.
  (Added after the P0-2 review:) the budget check must reserve each call's cost under
  a lock before the call and settle it after, so concurrent calls can't overshoot.

- [x] P1-6 — Faster still rendering (done for the still scene clip, −44% / −48%, with jitter
  unchanged (within 0.01 px); a still scene end to end is −26% / −24% because the film's own
  encode is unchanged: P1-9; whether the drift's existing stop-go is visible: P1-10. D18)
  Accept: still-scene render time drops ≥40% without visible jitter. Measure the
  current 3× upscale approach vs alternatives; record numbers in DECISIONS.

- [x] P1-7 — Don't re-attempt failed paid generations on every rerun
  Found during P0-1: `make_visual` falls back Veo → still → Manim, but only successes are
  cached, so every rerun of a film asks Veo (and Imagen) again for scenes that already
  failed, paying or waiting up to 10 min each time.
  Accept: a failed generation is recorded per content key in `build/<slug>/failures.json`
  with the error; reruns go straight to the fallback unless `--retry-failed` is passed.
  Mocked test: second run makes no provider call for a previously failed scene.

- [x] P1-8 — The closing fade dims the last narration
  Found in the P0-5 review (measured by the reviewer): in `add_music`, `afade=t=out` runs
  after `amix`, so it fades the voice as well as the music; with a steady tone the voice is
  ~10 dB down by the end of the last scene.
  Accept: only the music bed fades out (the voice isn't touched); a test measures the last
  narration's level against an earlier one (within 1 dB) and the bed's level falling.

- [~] P1-9 — Faster final film encode [blocked: no x264 setting meets all the criteria (D22);
  the size-or-quality trade-off is Pietro's, see NEEDS_PIETRO]
  Found in P1-6 (D18): once scene clips are fast, the film's own encode (`FILM_X264`, x264
  medium crf 18 in `crossfade_concat`) is 61–65% of a still scene's render time, and every
  scene pays it.
  Accept: with `scripts/bench_stills.py`, pick film x264 settings that cut the film encode
  ≥40% with SSIM (against the lossless reference) within 0.0005 of today's for stills, Manim
  and Veo clips, and file size up ≤25%; record the numbers in a new DECISIONS entry (this
  changes the file that is uploaded). Test: the film is encoded with the chosen settings.

- [~] P1-10 — Smooth sub-pixel drift on stills [blocked: needs Pietro's eyes, see NEEDS_PIETRO]
  Found in P1-6 (D18): zoompan keeps the crop on the yuv420p chroma grid, so even on the 3×
  upscale the slow drift moves in 0.73 px jumps with the picture standing still in between
  (stop-go), and a push-in moves back and forth. FFmpeg's `perspective` filter positions to
  1/256 px and removes it, but makes the still clip step about twice as slow (cubic; linear is
  cheaper but softer); zoompan in yuv444p costs nearly as much per clip and only halves the
  jumps (D18).
  Accept (once Pietro says the difference is visible): stills drift with jitter ≤ 0.1 px in
  the benchmark, same framing and motion as today; D18 updated with the new render time.

- [x] P1-11 — Pin dependency versions
  Found in the foundation check (PLAN.md): requirements use `>=` only, and the installed
  versions are exactly the tested minimums (anthropic 1.8.0, google-genai 2.25.0), so a new
  SDK release could silently break calls the tests mock.
  Accept: a `requirements.lock` (exact versions of the working environment) that README's
  install uses; `requirements.txt` keeps the ranges; `make check` passes in a fresh venv
  installed from the lock; a test fails if an installed version differs from the lock.

- [x] P1-12 — Veo 4 as a substitute for Veo 3 (Pietro, 2026-09-29)
  Accept: find out whether Veo 4 exists and what it costs; if something newer or cheaper than
  `veo-3.1-generate-preview` is available on the Gemini API with our request shape, switch the
  default with a DECISIONS entry, prices per model, and tests on the request actually sent.
  Outcome (D20): no Veo 4 found; Veo 3.1 stays the default (nothing better is available);
  Veo 3.1 Lite (~1/5 the price, the cheapest tier) is priced and one line in `.env` away;
  choosing it is Pietro's call (NEEDS_PIETRO).

- [x] P1-13 — ElevenLabs Eleven v4 as a substitute (Pietro, 2026-09-29: "ElevenLabs Veo 4")
  Accept: find out what Eleven v4 is, what it costs and what it accepts; make it selectable
  without breaking the current voice or its paid cache; decide the default (a taste call:
  Pietro's), with a DECISIONS entry and tests on the request actually sent.
  Outcome (D21): ready as `TTS_MODEL=eleven_v4`; the default stays Multilingual v2 because v4
  reportedly has no speed control (unverified); the choice and a €0.12 listening check are in
  NEEDS_PIETRO.

## P3 — the first film (MVP; Pietro, 2026-10-14)

Pietro: "finish this project to MVP", with a research film on a New Scientist headline (12 Oct
2026): "Do AIs already process information in a way that gives rise to consciousness?", "in
kurzgesagt style + others I said I like". Plan approved 2026-10-14 (PLAN.md). These come before
the remaining P1/P2 tasks; then P1-11, P2-3, P2-7, P2-6, P2-2, P2-4 in that order.

- [x] P3-1 — Research brief for a film (`--brief FILE`)
  A film about recent research needs facts the script model may not know.
  Accept: `--brief` passes the file's text to the outline agent, the writer and the critic (in
  the user prompt; system prompts unchanged; with no brief every prompt is byte-identical to
  before); the critic is told to generalise or drop claims the brief doesn't support;
  `--estimate` counts the brief's tokens; the brief is copied to `build/<slug>/brief.md` and
  its `## Sources` list goes into the YouTube description.
- [x] P3-2 — Visual style presets (`--style calm|flat`)
  Kurzgesagt-inspired pictures (flat vector shapes, rich colour on deep navy) with the calm
  SleepOnPhysics voice and pacing. The studio's name never goes into a prompt.
  Accept: `config.STYLES` holds each preset's still-prompt suffix and Manim palette; `calm`
  (the default) leaves every prompt and cache key byte-identical; `flat` changes the still
  prompt and the Manim palette; the flat background stays above the QA black threshold.
- [x] P3-3 — The film's brief and script (`topics/ai-global-workspace/`)
  Accept: `brief.md` labels every fact by source (primary verified vs secondary) with binding
  hedging rules; the outline and chapters are written with the pipeline's own prompts and
  reviewed with the critic's; every factual sentence maps to a brief line (table in
  `script.md`); `get_script` seeds missing outline/chapter files from the brief's folder;
  `--script-only` builds `script.json` with no keys, and `--estimate` runs.

- [ ] P3-4 — A rerun without `--brief` restores a deleted chapter from the topic folder
  Found in P3-3's review: seeding happens only on a run with `--brief`. On a rerun that leaves
  it out (allowed, since the kept brief is used), a deleted chapter goes to the writer and
  replaces reviewed scenes (a paid call). README and `--help` now say so.
  Accept: seeding records its folder in the build (e.g. `seed`, its path); a rerun without
  `--brief` seeds from it while that folder's `brief.key` equals the build's stamp; test: a
  chapter deleted from a seeded build comes back on a rerun without `--brief`, zero calls.

- [ ] P1-14 — Ctrl-C during the submission of parallel work doesn't stop the workers
  Found in P2-3's review (pre-existing, P1-5): `pipeline.in_parallel` creates the pool and
  submits futures before its `try`, so a Ctrl-C landing during submission skips the handler:
  `ledger.interrupted`/`stopping` are never set and the scenes already submitted keep making
  paid calls until the interpreter joins the threads (their ledger entries then lack the
  run's attribution). The window is milliseconds.
  Accept: pool creation and submission inside the `try`; a test that interrupts during
  submission sees `ledger.interrupted` set and no further paid call started.

- [x] P3-5 — Flat-safe prompts for the first film (planning group N4, 2026-10-16)
  12 of the 29 still and Veo descriptions in `topics/ai-global-workspace/` carry photographic
  cues (blur, mist, depth of field, dew). Under `--style flat`, they may pull pictures away from
  flat vector.
  Accept:
  - those cues are rewritten into flat-vector terms, with the composition kept;
  - no narration changes;
  - `script.md` and its fact table are regenerated and `tests/test_topics.py` passes;
  - a new test_topics check fails if a still or Veo description uses a word from a
    photographic-cue list in config;
  - `brief.key` unchanged (the brief isn't edited).
  Done 2026-10-25. **Amended acceptance (orchestrator, with the reason):**
  - The cues were rewritten in style-neutral terms, not "flat-vector terms". The line above
    contradicted D25, "a reviewed script serves any style". The style suffix supplies the
    medium, and calm is still the default. Review W3; analysis A5.
  - 11 descriptions carried cues, not 12. The reviewer searched for a 12th too.
  - A second list, `STYLE_MEDIUM_CUES`, keeps medium words out of descriptions.
  - The fact table is unchanged, because no narration changed.
- [x] P3-6 — `scripts/preflight.py`: check every provider before money is spent (N1)
  Done 2026-10-25 (D31). It must land with or after P0-8, because `--image` goes through
  `image_agent._buy`.
  Accept:
  - Free checks:
    - host reachability for api.anthropic.com, api.elevenlabs.io and
      generativelanguage.googleapis.com;
    - Claude model retrieve, plus a 16-token forced-tool call (a 200 with stop_reason
      max_tokens passes);
    - ElevenLabs models, voice and subscription;
    - Google `models.get` for IMAGE_MODEL and VEO_MODEL;
    - local FFmpeg filters (loudnorm, alimiter, sidechaincompress, xfade, ebur128);
    - `manim --version`;
    - free disk space.
  - `--image` buys one 1K image (≈ €0.06); `--tts-sample` buys one line (≤ €0.02), saves the mp3
    and prints the words per minute.
  - Every paid probe goes through the ledger; the total is at most €0.10.
  - Output is PASS/FAIL per line, with errors redacted.
  - The exit code is non-zero on any FAIL.
  - Offline tests mock every provider.
- [ ] P3-7 — Pilot tool: one chapter as a seed folder (N3)
  Accept:
  - `scripts/make_pilot.py --chapters 1 OUT` writes into OUT (outside `topics/`): brief.md, a
    correct brief.key, a one-chapter outline.json and chapter_01.json.
  - It prints the pilot command (its own topic string, `--brief OUT/brief.md --style flat
    --no-ai-video`) and the command that copies the pilot's `audio/` and `visuals/` into the
    full film's build without overwriting.
  - `--estimate` on the pilot works with no keys.
  - Test: a faked pilot then a faked full film; the full film buys nothing the pilot bought.
- [ ] P3-8 — Run report lines for the first live runs (N5)
  Accept: `run_report.md` adds:
  - narrations limited out of the total, and the largest boost (from `clips/level_*.json`);
  - image attempts per still, from the review logs;
  - each QA check's margin to its limit;
  - this run's estimate vs its ledger total, when an estimate was made in the same run.
  Tests on faked runs.
- [ ] P3-9 — The build remembers `--no-ai-video` (N6)
  Veo is on whenever a Google key is set (ai_video.available). A rerun without the flag could
  therefore buy Veo clips (about €2.94 each) unasked.
  Accept:
  - like the style (D25), `build/<slug>/` records whether Veo is allowed;
  - a rerun without `--no-ai-video` keeps the recorded choice and prints it;
  - an explicit `--ai-video` (new) or `--no-ai-video` switches, with a line saying so;
  - `--estimate` honours the record.
  - Tests: a rerun after `--no-ai-video` requests no Veo; an explicit `--ai-video` does.
- [ ] P3-10 — Record redacted live responses as contract fixtures (N2)
  Accept:
  - with `RECORD_LIVE=1`, every provider response is saved under `build/recorded/`, redacted
    (keys, headers, binary bodies stored as hash, length and head);
  - `tests/test_contract.py` replays fixtures dropped into `tests/fixtures/live/` through the
    real parsing code, and skips when there are none;
  - a test proves no key string appears in any saved file.
  `make smoke` with `RECORD_LIVE=1` then pays once for real fixtures.

- [ ] P3-12 — A fake-provider full run in `make check` (AGENT_LEDGER A6)
  One short film goes through the real SDKs and the real `requests` stack, every provider faked
  at the transport level, under P1-15's tripwire. It catches SDK lifetime bugs (P0-8),
  SDK-upgrade breaks, and any path that unit tests fake away. It comes after P3-10, whose
  recorded responses it can serve.
  Accept: a film made end to end offline, with no socket opened, and the run report naming every
  provider request it made.
- [ ] P1-19 — A probe kit: the reviewers' harnesses as reusable files (AGENT_LEDGER A7)
  Reviewers rebuilt the same harnesses for every task. They are a socket tripwire (connect and
  getaddrinfo), a real-signal driver (SIGINT at random points, statistics), a slowed-write
  SIGKILL harness with a concurrent-writer race, and transport-level fakes per provider.
  Accept: `tests/probes/` holds them (not collected by pytest), each with a docstring and one
  example run; implementer.md and reviewer.md point to it.
- [ ] P3-11 — Feed the description word lists into the writer and critic (P3-5 review S5)
  The cue checks only cover hand-made `topics/` folders. A script the pipeline writes (G4) goes
  to `build/<slug>/` and is never checked.
  Accept:
  - `photo_cue_problems` and `medium_cue_problems` move from tests/conftest.py into a module;
  - the writer's visuals prompt names the two lists' intent;
  - the critic flags a description that uses either list;
  - `--estimate` lists such scenes before anything is bought.
- [ ] P1-15 — A socket-level backstop for offline tests (P3-6 review)
  The conftest guard blocks known SDK entry points; a new path could still open a socket.
  Accept:
  - an autouse fixture refuses `socket.connect` to any non-loopback address in offline tests,
    and `socket.getaddrinfo` for any non-local name. The P0-8 review found a DNS lookup that a
    connect-only guard lets through.
  - It has a self-test. Live tests (`-m live`) are exempt.
  - The per-test socket guards in test_genai_lifetime.py and test_preflight.py then use it.
- [ ] P1-16 — Redact before cutting, everywhere (P3-6 review)
  pipeline.py:294, :301 and :305, and retries.py:62, cut `str(e)` before `log()` redacts it. A key
  that straddles the cut is no longer whole, so its first characters print.
  Accept:
  - `redact(text)[:N]` at each site;
  - a test puts a long key across each cut and finds no 8-character run of it in the output;
  - an AST check flags a slice of `str(...)` or `.text` that is not wrapped in `redact`.
- [ ] P1-17 — Test-order leak: test_qa's `healthy` fixture sets `assembly.W`/`H` directly (P2-8)
  `test_assembly.py::test_scene_format_is_normalised` fails when test_qa.py runs first in the
  same process. Accept: the fixture uses monkeypatch, and a test runs the pair in both orders.
- [ ] P1-18 — Widen the genai AST guard (P0-8 review W1)
  The guard misses forms that do break: `from . import google_client`, an alias of the factory
  (`f = google_client.make`), `getattr(make(), "models")`, `google.genai.client.Client`. None
  is in the code today. Accept: each is added to the self-test's flagged list, and the scanner
  catches it.

## P2 — scale and polish

Order set by docs/PLAN.md (2026-09-27): the run report first (the first live run needs
it); batch mode waits for one verified live film.

- [x] P2-3 — Structured run log and a `run_report.md` per video (costs, timings, QA,
  fallbacks used).
  Accept (2026-10-14): every run past argument parsing writes `build/<slug>/run_report.md`,
  also a stopped run (in a `finally`) and `--script-only`, never `--estimate`. It holds: header
  (outcome `finished` / `finished, QA failed` / `script only` / `stopped at <stage>: …`, flags,
  style, brief, models); this run's and this film's costs from the ledger (entries carry `film`
  and `run`); stage timings; scenes with fallbacks and their recorded reasons; QA check by
  check; next steps. Keys are redacted; the previous report is kept as `run_report.prev.md`.
- [ ] P2-5 — Prune per-scene files no film uses any more
  Found in the P1-6 review: files are named by a hash of their inputs, so any change of
  settings (e.g. D18's clip encoder) leaves the old clips behind; `clips/` roughly doubles.
  Accept: `python pipeline.py "<topic>" --prune` deletes the free intermediates (`clips/`)
  that the current `manifest.json` doesn't reference and prints the space freed. Paid files
  (`audio/`, `visuals/`) no manifest references are only listed, with what they cost; they
  are deleted only with `--prune-paid` (a reverted script edit would otherwise pay again).
  Since P3-2 a build can hold pictures of two styles (D25); `--prune-paid` keeps those the
  last manifest of each style used, or a switch back would buy them all again. `clips/level_*.json`
  (P2-6) are free but save a reading per scene: keep the ones the kept clips used.
  The thumbnail's picture (P2-2) is a paid file in `visuals/` that no manifest names: keep it
  (its path is `image_agent.cache_path(thumbnail.picture_scene(...))`).
  Nothing in `music/` is touched. Test: after a settings change and rebuild, `--prune` leaves
  exactly the referenced clips and every paid file.
- [x] P2-2 — Thumbnail generation (1280×720, no text baked in by the image model; title
  text added by FFmpeg/Pillow). Done 2026-10-14 (D29).
- [~] P2-1 — Batch mode from `topics.txt`, with a variety check against past titles
  and outlines (avoid near-duplicate videos; YouTube demonetises mass-produced content).
  [blocked: needs one verified live film first, PLAN.md phase 3]
- [x] P2-6 — Each narration's first second is louder than the rest
  Found in P1-8 (measured on the voice-only track, before mixing; confirmed by the reviewer):
  each scene's single-pass `loudnorm` (dynamic mode) plays a steady narration's first second
  ~1.7 dB louder, then holds it level (scene 5: −18.3 dB in the first second, −20.0 after);
  the same in every scene. It's loudnorm's start-up, not a slow drift.
  Accept: per-scene levelling that holds a steady tone within 0.5 dB from its first second on
  (for example two-pass `loudnorm`: measure, then a linear gain), with every narration still at
  `VOICE_LUFS` ± 0.5 LU, sample-exact timing (D12) unchanged, and QA passing; test on the
  voice-only track.
- [x] P2-7 — The picture starts fading out before the last word
  Found in the P1-8 review: `FADE_OUT_S` (2.5 s) is longer than `TAIL_S` (1.4 s), so the film's
  closing fade to black starts ~1.1 s before the last narration ends. Nothing checks it; it
  predates P1-8.
  Accept: the fade to black starts no earlier than the last narration's end (e.g. `FADE_OUT_S`
  ≤ `TAIL_S`, or a longer final tail), asserted in `config.py`; a test on a built film finds no
  darkening while the last narration sounds. If a fade under the last words is wanted
  instead, record that in DECISIONS and close this.
- [ ] P2-8 — The master `loudnorm` plays the film's first narration louder
  Found in P2-6 (implementer and reviewer): after per-scene levelling, scenes 2–6 of a built
  film are within 0.1 dB from their first second, but scene 1's first second is +1.8 dB: the
  final mix's single-pass `loudnorm` (dynamic mode) starting up.
  Accept: the finished film's first narration holds its level within 0.5 dB from its first
  second on; the master still lands at `TARGET_LUFS` ± 1 LU with QA passing; test on a built
  film.
- [ ] P2-9 — True-peak margin on dense bursts
  Found in P2-6's review: on a film of dense percussive bursts the final true peak reaches
  -0.3 dBTP (QA's ceiling is -1.0) before and after P2-6: the master `loudnorm` plus AAC (D15).
  Real narration is far less peaky; not seen on speech-like tests.
  Accept: a film made from the test's burst narrations passes QA's true-peak check, without
  lowering `TARGET_LUFS`; ordinary films unchanged.
- [x] P2-4 — README refresh reflecting everything above. Done 2026-10-15, with a "Make the
  first film" runbook.
