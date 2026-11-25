# Why the line matters (Riemann hypothesis, ~3:17)

Made outside the pipeline (no provider keys in this session), on 2026-11-25:
- **Narration:** Kokoro TTS (local, voice `bm_george`, speed 0.92) from `script.json`, via `tts.py`.
- **Data:** `data.py` (mpmath, numpy). It computes:
  - the real prime counts and `li(x)`;
  - the first 2,000 zeta zeros;
  - the explicit formula for ψ(x) with 0–300 zeros;
  - a seeded million-flip coin walk;
  - the error band √x·ln x/(8π), Schoenfeld's bound under RH;
  - the unfolded zero spacings.
- **Pictures:** `scenes.py` (Manim, flat style), each scene timed to its narration
  (`durations.json`).
- **Assembly:** FFmpeg. The narration starts 0.3 s into each scene; a synthesized pad sits at
  −29 dB; the whole is normalized to −16 LUFS.

To rebuild:
1. `python tts.py bm_george 0.92` (needs `kokoro.onnx` and `voices.bin` from the kokoro-onnx v1.0
   GitHub release).
2. `python data.py`.
3. `manim -r 1920,1080 --fps 30 scenes.py S1 … S8`, then mux.
