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
