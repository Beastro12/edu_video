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
folder so a different cwd can't start a fresh ledger.
