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
