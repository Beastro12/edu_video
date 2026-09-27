# Decisions

Each entry: what, why, evidence. Add new entries; don't rewrite old ones.

**D1 — Audio first.** Narration is generated before visuals; its measured length sets
each scene's length. Why: visuals and voice drifting out of sync is the most common
failure in generated video.

**D2 — Level the voice before ducking.** Each scene's narration is loudness-normalised
to `VOICE_LUFS` before mixing. Evidence: without it, ducking measured only ~3 dB on a
quiet voice, because `sidechaincompress` uses an absolute threshold. With it: ~10 dB.

**D3 — Stills are the default visual (~60%).** Slow-drifting AI images, Manim ~30% for
diagrams with ≤5 words on screen, Veo ≤1 per chapter. Why: sleep viewing is broken by
busy labelled diagrams, and Veo is priced per second. Risk: the critic reads text, not
images, so a still can show wrong physics (see P1-5).

**D4 — Chapters, not one-shot scripts.** 10–20 min is ~1,200–2,400 words plus visual
descriptions; one call risks truncation and drift. Writer receives the previous
chapter's last two narrations for continuity.

**D5 — Crossfades sit in silence.** `XFADE_S < TAIL_S` is asserted in config, so the
overlap never covers narration.

**D6 — Equal-power music crossfades (`qsin`).** Evidence: a linear crossfade dipped
~3 dB at the loop point; `qsin` measured flat (−18.7 / −18.9 / −18.2 dB).

**D7 — Fallback chain Veo → still → Manim.** One failed generation never stops a film.

**D8 — Forced tool use for structured output** from Claude, not "reply in JSON".

**D9 — Cache by content, not by scene number.** Per-scene files live in `audio/`,
`visuals/` and `clips/`, named by a hash of the inputs that produced them (narration text
+ voice settings; prompt + model; input file bytes + exact FFmpeg filters). Fixed-name
outputs (`narrated.mp4`, `music_bed.wav`, the final video) carry a `<name>.key` stamp and
are rebuilt when it doesn't match. `script.json` records a hash of the chapter files it
came from. Why: scene-number keys reused a stale mp3 after a narration edit, and a
chapter gaining a scene shifted every later scene onto another scene's files (P0-1).
Evidence: `tests/test_cache.py` — inserting a scene in front buys only that scene's audio
and image; editing one narration rebuilds one clip, `narrated.mp4` and the final.
Still/Veo keys leave out the scene type and target length because those generators never
see them, so a narration-only edit doesn't re-buy the image. Manim keys include the whole
brief (narration and target length), so editing a Manim scene's narration also re-runs its
Claude call and render. Two scenes with identical `visual_description` share one still or
Veo clip. Cost: old versions stay on disk until `build/<slug>/` is cleaned.

**D10 — One spend ledger, checked before every paid call.** `build/ledger.jsonl` is shared
by all videos and `BUDGET_EUR` caps its total, so the cap holds across reruns and topics.
A call is checked with its worst case (Claude: estimated input + full `max_tokens` output)
and recorded only after it succeeds, with actual Claude token usage. `BudgetExceeded` is
not a failed generation: the Veo → still → Manim fallback re-raises it (D7 unchanged for
real failures). Prices (2026-09-27, secondary sources; verify): Claude Sonnet 5 $2/$10 per
MTok (Anthropic's model table); ElevenLabs Multilingual v2 $0.10/1k chars; Imagen 4
Standard $0.04/image; Veo $0.40/s; USD→EUR 0.92. Evidence: `--estimate` for a 15-min film
gives ~€2 without Google, ~€17 with Veo (5 × 8 s clips ≈ €14.7), so Veo is most of the cost.
Known limits: check-then-call is not atomic (across threads or processes), so parallel
work could overshoot by the calls in flight; P1-5 must reserve the cost under a lock. Failed
calls are assumed unbilled and not recorded, except a Veo job that times out: it keeps
running on Google's side, so it is recorded as spent. `build/` is anchored to the project
folder so a different cwd can't start a fresh ledger. The Veo daily cap (P0-7,
`VEO_MAX_PER_DAY`) is counted from the same ledger and shares its weakness: a clip counts
once its job ends (up to 10 min), so two runs at once could each make 2. A Veo start that
fails with a 5xx or timeout is counted as possibly started (it may have been accepted).

**D11 — Use each SDK's own retry loop; one small helper for the rest.** Transient errors
are retried up to `MAX_RETRIES` (4) with jittered exponential backoff; anything else
(400/401/403/404/422, safety refusals) fails on the first try.
- Anthropic: the SDK's built-in retries with `max_retries=4` (408/409/429/5xx, timeouts and
  connection errors; its own 0.5 s → 8 s curve; honours `retry-after`).
- Google: google-genai's `HttpRetryOptions` (off unless configured — verified in 2.25.0) from
  the same config values: 408/429/5xx, timeouts and failed connects (not other dropped
  connections). Requests time out after `GOOGLE_TIMEOUT_S`; the SDK has no timeout otherwise.
- ElevenLabs (`requests`) and Veo file downloads (which bypass the SDK's retry loop):
  `retries.py`, honouring `Retry-After`, capped at `RETRY_MAX_S`.
- The POST that starts a Veo job is retried only on 429 (job refused). A 5xx can arrive after
  Google accepted the job, and a retry would start and bill a second one (~€2.94). Once a job
  has started, losing it (poll failure, timeout) records it as spent (D10).
Why not one wrapper around everything: the SDKs already classify errors and retry inside the
call; wrapping them again would multiply attempts. Evidence: `tests/test_retries.py` drives
the real Anthropic and Google SDKs through mock HTTP transports and counts requests, and
checks that one call is recorded once however many attempts it took. Residual risk: a
Claude, ElevenLabs or image POST that times out after the provider did the work is retried
and may be billed twice; the ledger records it once.

**D12 — Sample-exact scene timing; test audio as decoded samples.** Each scene is a whole
number of video frames (`scene_frames`: lead-in + narration + tail, rounded up) and its audio
exactly that many samples (48 kHz / 30 fps = 1600 per frame): `adelay` → `loudnorm` →
`asetpts=N/SR/TB` → `apad`/`atrim` to the sample, video capped with `-frames:v`.
`crossfade_concat` trims each clip's audio to its video length (dropping AAC's end padding)
and counts offsets in whole frames (an offset printed as "2.067" for 2.0667 s started the
fade a frame late). The final mix re-stamps after `loudnorm` too.
Evidence (FFmpeg 6.1.1): `loudnorm`'s output timestamps are not continuous: after a short
final frame the next timestamp jumps a whole 100 ms block (`ashowinfo`), and with `adelay`
after it the lead-in silence had no timestamps at all. FFmpeg drops samples when cutting by
time, so clip audio decoded short (a 3.39 s clip to 2.84 s, voice at 0.04 s instead of
0.60 s; with only the reorder, 3.06 / 3.344 s narrations still lost 27 / 40 ms) and the final
mix lost 39-75 ms. Players honour the timestamps, so one clip sounds right; the crossfade
chain works on samples, so every scene moved the next one's voice: 3 scenes gave narrated.mp4
10.26 s of audio under 11.93 s of video, and the reviewer measured 1.06 s over 40 scenes.
Even with exact clips, AAC end padding (≤ 21 ms per clip) added up to +0.22 s over 40 scenes
until the crossfade trimmed it. Now: every clip's decoded audio equals its video within one
AAC frame, onset 0.60 s, and in a 12-scene chain the last voice starts within 30 ms of where
it should (tests measure decoded samples via `decoded_audio`, never container durations).
Voice level is kept (−18.2 vs −18.6 LUFS on a test tone; D2 holds). Side effect: `loudnorm`
after a lead-in lifts each scene's first ~1.5 s by ~1 dB (reviewer, steady noise); harmless
to ducking. Scenes are now up to one frame (33 ms) longer than lead-in + narration + tail.

**D13 — Stills from the Gemini image model; Veo 3.1.** Stills are generated with
`generate_content` (`response_modalities=["IMAGE"]`, 16:9, `IMAGE_SIZE` 1K) on
`IMAGE_MODEL` (default `gemini-3.1-flash-image`); a non-PNG answer is converted to PNG; an
answer without an image falls back to Manim (D7). Veo defaults to `veo-3.1-generate-preview`
and is called with `source=GenerateVideosSource(...)`. Evidence: verified locally,
google-genai 2.25.0 refuses `generate_images` (Imagen) in Gemini-API-key mode before sending
anything, so with the old code every still silently became Manim, undoing D3. Not verified
(Google's pages are unreachable from the sandbox; several secondary sources agree): the Gemini
API shut down Imagen 4 on 2026-08-17 and Veo 3.0 on 2026-06-30; model names and prices
(~$0.067 per 1K image, Veo $0.40/s) — all marked "verify" in config.py, checked by P0-3.
Veo's clip length is left at the model default (8 s assumed in the ledger) rather than
sending `duration_seconds`, which isn't verified for Veo 3.1 and isn't covered by the smoke
test (it runs without Veo).

**D14 — The crossfade sits in silence on both sides.** `XFADE_S` must be shorter than
`LEAD_IN_S` as well as `TAIL_S` (both asserted). D5 only guarded the tail: with lead-in 0.6 s
and a 1.2 s crossfade, every narration began halfway through the crossfade, so its first
0.6 s was faded in by `acrossfade` (measured by qa.py: silence for 0.6 s, then the voice at
−30 → −19 dBFS across the rest of the fade, in all 5 crossfades of a test film) while the
picture was still dissolving. Now lead-in 1.4 s and tail 1.4 s (were 0.6 / 1.8): 0.2 s of
silence on each side of every crossfade, 1.6 s between narrations (was 1.2), each scene 0.4 s
longer. The values are a pacing choice (NEEDS_PIETRO); the constraint is the decision.

**D15 — QA measures the film, not the settings; master ceiling −2 dBTP.** qa.py's ducking
check first requires the film's mix stamp to match what `add_music` would make now from the
folder's narration and bed (else: fail, rebuild). It then renders the one shared `mix_graph`
before the master with the voice muted after it keys the sidechain (the music exactly as
wired), adds the master's per-window gain (film level − pre-master level), and compares the
music under speech with real pauses (≥ `QA_PAUSE_S` of silence). Evidence: a first version
compared the bed with vs without the sidechain from config; the reviewer showed a film mixed
with no ducking and one mixed with the raw bed both passed at 10.5 dB. Now both fail; the
healthy test film reads 10.8 dB (D2: ~10 dB by hand). `blackdetect` uses `pix_th` 0.05: at
0.10 the Manim background (#0f1419) alone counted as black. Master true-peak ceiling −2.0
(was −1.5): bursty pink noise through the master measured −1.2 dBTP at −1.5 (QA limit −1.0)
and −1.8 at −2.0, loudness unchanged (−16.6 / −16.7 LUFS). Limits: the check trusts the
stamp (a film replaced after mixing, with other music under the speech, would not be seen,
since the voice dominates both renders there), and it needs pauses of `QA_PAUSE_S` + 0.2 s
between narrations (asserted in config). Real music is untested until the first live run.

**D16 — Every still is reviewed by Claude (vision) before it is used.** Answers D3's risk
("the critic reads text, not images, so a still can show wrong physics"). The reviewer sees a
≤1024 px JPEG preview plus the scene's concept and description, and rejects text/letters,
physically wrong depictions and anything unsettling for sleep viewing. A rejected still is
regenerated with a prompt that says what to show (naming "letters" can make the model draw
some) plus the earlier problems, up to `STILL_REVIEW_RETRIES` (2) times; then the scene falls
back to Manim (D7 extended). The decision is cached per scene in `visuals/review_<key>.json`,
keyed by everything both models are given (prompt, concept, models, reviewer prompt/schema),
written after every attempt so a crash or rerun never pays twice; `manifest.json` links it.
Cost: one extra image (~€0.06) and one review (~€0.01) per rejected attempt; the image cost
estimate uses a fixed `EST_IMAGE_TOKENS` because base64 counted as text would look like
tens of thousands of tokens. Not verified live: reviewer strictness on real images.

**D17 — Generate scenes concurrently; reserve budget, serialise Veo.** Narration for all
scenes, then visuals for all scenes, run on `WORKERS` threads (`pipeline.in_parallel`,
results in scene order). The first failure stops the run at once: calls not yet started are
cancelled and, until the calls in flight have finished, `ledger.stopping` refuses any new
reservation (then it clears, so a later run in the same process starts clean). A started Veo
job is only abandoned on Ctrl-C (`ledger.interrupted`); otherwise it finishes and is cached,
since abandoning it would waste a paid clip. Two scenes with the same cache key are made once (`utils.output_lock`; the other waits
and finds it cached); temp files carry the thread id; JSON writes are atomic. Clip rendering stays
sequential (FFmpeg already uses the cores). Every paid call now *reserves* its estimated
cost under the ledger lock (spent + held by calls in flight + this call ≤ budget) and records
its actual cost before releasing the hold, which closes D10's check-then-call gap within a
process. Veo jobs run one at a time so the daily cap can't be raced. Evidence: 8 concurrent
narrations with room for 3 bought 8 before, exactly 3 now; 8 scenes (TTS + image, 0.2 s fake
latency) took ~3.5 s sequentially and ~1.35 s on 4 workers. Still open: two separate
processes share the ledger file but not the lock.

**D18 — Scene clips are fast intermediates; the drift keeps zoompan on a 3× upscale.**
The numbers here come from one run of `scripts/bench_stills.py` (4 cores; a 20 s scene at
1080p30; the still is a seeded, textured 1344×768 test image, a hard case). Times are the
fastest of 3 runs and vary up to ~8% between runs; SSIM, sizes and motion repeat exactly. "Clip"
is `build_scene_clip`, "film" the film's own encode of that clip in `crossfade_concat` (medium
crf 18, unchanged). Film SSIM is against a lossless render of the same drift, on the frames
between the film's fades; "detail" is the variance of the Laplacian of the middle frame
(higher = sharper). Cells are pan / push-in.

| drift, clip encoder | clip s | film s | jitter px | film SSIM | detail | clip MB |
|---|---|---|---|---|---|---|
| 3× zoompan, lossless (reference) | 8.13 / 8.10 | 15.10 / 19.56 | 0.682 / 0.457 | – | 1746 / 1995 | 368 / 785 |
| 3× zoompan, medium crf 18 (was) | 15.06 / 18.01 | 14.30 / 17.35 | 0.685 / 0.470 | 0.99752 / 0.99670 | 1684 / 1848 | 15.7 / 25.0 |
| 2× zoompan, medium crf 18 | 17.51 / 16.86 | 16.36 / 17.27 | 0.833 / 0.530 | – | 1702 / 1892 | 20.8 / 29.0 |
| 1× zoompan, medium crf 18 | 12.17 / 15.16 | 13.12 / 16.17 | 1.181 / 0.733 | – | 1688 / 1922 | 16.1 / 27.1 |
| 3× zoompan, veryfast crf 18 | 8.37 / 9.10 | 14.63 / 17.20 | 0.686 / 0.456 | 0.99585 / 0.99480 | 1694 / 1769 | 11.8 / 20.9 |
| 3× zoompan, veryfast crf 16 | 8.47 / 8.88 | 13.59 / 16.76 | 0.684 / 0.463 | 0.99679 / 0.99582 | 1688 / 1799 | 14.5 / 28.5 |
| **3× zoompan, veryfast crf 14 (now)** | **8.42 / 9.31** | 13.18 / 17.46 | 0.683 / 0.465 | 0.99745 / 0.99636 | 1680 / 1821 | 18.1 / 39.8 |
| 3× zoompan in yuv444p, veryfast crf 14 | 15.95 / 15.99 | 14.62 / 17.65 | 0.199 / 0.289 | 0.99761 / 0.99605 | 1784 / 1923 | 21.1 / 40.4 |
| `perspective` cubic, veryfast crf 14 | 17.81 / 17.88 | 16.55 / 24.36 | 0.050 / 0.020 | 0.99721 / 0.99690 | 1716 / 2231 | 38.3 / 123 |
| `perspective` linear, veryfast crf 14 | 13.72 / 12.77 | 16.15 / 23.83 | 0.059 / 0.021 | – | 1313 / 1647 | 41.5 / 116 |

- **Speed.** The clip's x264 encode, not zoompan, was most of the clip step (lossless
  ultrafast: 8.1 s). Chosen: still and Manim clips use `CLIP_X264` = veryfast crf 14: clip step
  −44% / −48% (43–49% in each of the five runs made, two of them with unseeded images); Manim
  −13% (3.61 → 3.15 s). The film's encode is unchanged and now takes 1.6–1.9× as long as the
  clip, so a still scene end to end (clip + film) is −26% / −24% (29.4 → 21.6 s, 35.4 → 26.8 s).
  A faster film encode is P1-9.
- **Quality.** Film SSIM −0.00007 / −0.00034 (Manim 0.99987 → 0.99984). veryfast crf 16 and 18
  were as fast but lost 0.0007–0.0019. Veo-like footage (grainy 720p24, stretched) loses 0.0067
  through two fast encodes (0.96858 → 0.96191), so Veo clips keep `FILM_X264`: at most
  `VEO_MAX_PER_DAY` per film, their speed doesn't matter (`assembly.clip_encoder`).
- **Motion.** Jitter is set by the drift filter, not the encoder (was and now within 0.01 px of
  each other, and within 0.013 px of the lossless render). zoompan keeps its crop on the
  chroma grid of its input, which the pipeline's `format=yuv420p` makes yuv420p, so even at 3×
  the drift moves in steps of 2 px of the upscale: on the pan the picture stands still on 57%
  of frames and jumps 0.733 px on the rest (mean 0.318 px/frame); in the push-in it moves back
  and forth (in the worst of the 8 strips, 75% of its moves go against the drift). A smaller
  upscale saves at most 20% (measured at the old encoder) and is jerkier (2×: +22% / +13%;
  1×: +72% / +56%), so 3× stays (`KEN_BURNS_UPSCALE`). Alternatives, each at 1.4–2.1× the
  clip time: zoompan in yuv444p halves the jumps (0.366 px; jitter 0.20 / 0.29); FFmpeg's
  `perspective` (positions to 1/256 px) glides (moves on every frame of the pan, never
  backwards; jitter 0.05 / 0.02), with cubic interpolation as sharp as today and with linear
  cheaper but softer (detail −22% / −10%). Whether today's stop-go is visible needs eyes on a
  screen: P1-10, NEEDS_PIETRO.
- **Disk.** Intermediate clips +15% / +59%. The clip cache key includes the encoder, so an
  existing build re-renders its clips once (no paid call) and the old ones stay until pruned
  (P2-5).
- **How motion is measured**, and three mistakes not to repeat. Per frame, the column means of
  the middle third; per 240 px strip, the shift between consecutive frames from the slope of the
  cross-spectrum's phase (checked on each run against independently made known shifts: off by
  ≤ 0.0001 px full width, ≤ 0.0022 px on a strip); jitter = std of the frame-to-frame change of
  that shift, median over 8 strips; shifts past 3 px are failed estimates (< 1%), dropped and
  counted. The first benchmark (i) fitted a parabola to the correlation peak at 480 px width,
  which pulls towards whole pixels, (ii) used unseeded test sources, so its numbers could not
  be reproduced (the P1-6 review's Critical), and (iii) an intermediate version measured the
  push-in on a 480 px strip, where motion isn't uniform, so the estimate moved with wherever the
  encoder left detail (0.54 → 0.65 px for identical motion).

**D19 — A failed paid generation is recorded, not re-attempted (P1-7).** Before, only
successes were cached, so every rerun asked Veo (and the image model) again for scenes that
had already failed, paying or waiting up to 10 minutes each time. Now `ai_video.render_scene`
and `image_agent.render_still` record a failure under the cache key of the generation itself
(`failures.json`, per film) and later runs raise `FailedEarlier` at once, which `make_visual`
treats like the failure (still, then Manim). Recorded at the generation call, not in
`make_visual`: a still's review is a separate Claude call, and a Claude outage must not make
later runs throw away a still that was bought. Not recorded: `BudgetExceeded`,
`DailyCapReached` (nothing was asked of the provider) and anything while Ctrl-C is stopping the
run; a still Claude rejects is already kept by P1-2's review log. Only errors about the request
itself are recorded (`failures.lasting`: a safety filter, an invalid prompt, no image or video,
a Veo job that never finished). Transient errors (`retries.is_transient`, D11, and any other
httpx transport error) and this machine's errors (OSError, a failed FFmpeg command) are not:
asking again may well work, and a network outage must not push a whole film to Manim. Every
skip is logged with the error and the `--retry-failed` hint. Under `--retry-failed`, a key that
fails again in that run isn't asked twice (two scenes sharing a picture pay once). Error text
is redacted of API keys before it is saved or logged (`utils.redact`, applied in `log()`).
`--estimate` counts the fallback for such scenes, including a failure on a later review
attempt.
