# Spec — P2-8 + P2-9 REDO: the master is one constant gain, with a true-peak margin

**Why a redo.**
- These were accepted on 2026-10-26: commits 95c51fc, 1f3c539 and d0c657f, 605 passed, review a9d94
  with no Critical.
- They were lost unpushed when the container was replaced.
- This spec is the ACCEPTED design, review fixes included.
- The accepted docs text (D12, D15, D28 amendments, D32) is in AGENT_LEDGER / chat. The orchestrator
  writes the docs, not you.

## Goal (BACKLOG P2-8, P2-9)
- **P2-8:** the master's single-pass `loudnorm` played the film's first narration about +1.8 dB
  louder in its first second. Replace it with one constant gain, read first.
- **P2-9:** dense bursts must pass QA's true-peak check (−1.0 dBTP) without lowering
  `TARGET_LUFS`. Ordinary films keep their level (±0.2 LU).
- **Mix only:** no paid cache key may change (D9). Pin clip names and level-file names against the
  base.

## Design (accepted)
- **`assembly.py`.**
  - Factor the voice levelling into a shared `_level_decision`: a reading, then a constant gain,
    plus a limiter only if the gain would pass the ceiling, plus the capped make-up loop (D28).
    `voice_gain` uses it; its key parts, the `level_*.json` content and its name are unchanged.
  - `METER = "ebur128=peak=true"`, used by `_read_level` and the master's reading.
  - `_limiter(ceiling_dbtp=None)` reads the ceiling at call time. The default text is
    byte-identical.
  - `master_chain(total, gain_db="<gain>", limited=True)`:
    `volume=<gain>dB[,aresample=192000,alimiter=...latency=1,aresample=48000],asetpts=N/SR/TB,apad=whole_len=N,atrim=end_sample=N`.
    - The cut to the film's exact sample count is REQUIRED. Without it, `amix` followed by AAC with
      the video copied ends 60–110 ms early, and three test_assembly tests catch that.
  - `_master_ceiling() = MASTER_TP_DBTP - MASTER_AAC_MARGIN_DB`, used both by the limiter and by
    the decision on whether the gain needs a limiter.
  - `master_level(narrated, bed, hashes)` reads the pre-master mix (the same graph qa renders with
    `master=False`). It keeps `master_level.json` in the build root, ONE fixed file holding the
    reading and its own key:
    - the key is `content_key("master level", narrated hash, bed hash, mix_graph(master=False) if
      bed, master_chain(total), METER, _master_settings())`;
    - a missing, truncated, empty, non-JSON, wrong-type, no-gain, NaN or other-key file is
      ignored and read again;
    - writes use `save_json` (atomic).
  - `mix_graph(total, voice_scale=1.0, master=True|str)`: rename the parameter so it doesn't
    shadow `voice_gain`. qa's keyword follows. qa's two `master=False` graph strings stay
    byte-identical to base (pin them).
  - `mix_key` covers inputs and settings, not the gain.
  - `add_music(narrated, bed, out)`:
    - it calls `master_level` BEFORE `is_fresh`. A fresh film with a missing, damaged or foreign
      reading reads once, for free, never remuxes, and fails if that reading's FFmpeg fails. The
      docstring says so.
    - The film stamp (`stamped_output`) is written after the reading is saved.
- **`config.py`:** `MASTER_AAC_MARGIN_DB = 2.0`, plus an assert that the limiter's limit lies
  between −24 and 0 dBFS. The comment carries the measured numbers:
  - at 2.0, the worst encoded true peak is −1.7 dBTP ('peakier' WITHOUT music, the worst path)
    and −2.1 with music;
  - at 1.5, two films reach −1.0; at 1.0, one reaches −0.2;
  - AAC adds 0.0–2.9 dB: speech-like 0.1–0.5, dense bursts 1.2–2.9, erratically.
- **`qa.py`:** wording only (the master is one constant gain).
- **`tests/conftest.py`:** `build_film(voices=..., music=False)`. Defaults behave as before.

## Tests (tests/test_master_level.py; real FFmpeg films)
1. The first narration holds its level from its first second, with and without music. On base it
   fails at +1.8 dB.
2. The master lands on `TARGET_LUFS` ±0.5 LU, with and without music.
3. Burst films (`peaky` and `peakier` from test_voice_level) × with or without music pass QA, with
   `TARGET_LUFS == -16`.
   - `test_without_its_margin_the_burst_film_overshoots`, with music and without, is skipped
     unless `ffmpeg -version` is 6.1.x.
4. An unchanged film makes no new reading and no remux: 0 FFmpeg calls (spy on `assembly.run`).
5. A changed bed → read again and remixed.
6. A changed cut or meter → read again (mutation check: a key of `_master_limiter()` only must
   fail).
7. QA passes, and ducking is measured on the unmastered mix. Pin both `master=False` strings.
8. A deleted, damaged (9 variants) or foreign `master_level.json` → read once, no remux. A
   hand-edited gain with its key intact is believed at the next remux.
9. A crash after the reading and before the stamp → the rerun mixes without reading.
10. No music, then music, written to the SAME output path: each step reads once and remuxes
    once.
11. The film's audio ends with its picture, within one AAC frame, with and without music.
12. Pin two clip names and two `level_*.json` names against the base.
13. A fresh film whose reading cannot be made fails and leaves the film as it was.

Also add the adversary's tests as tests/test_adversary_p2_8.py:
- (a) a changed narration of the same length → remixed, and the reading equals a clean build's
  (compare decoded PCM SHA-256);
- (b) a crash after the film is replaced and before its stamp → the rerun equals a clean build;
- (c) another film's `master_level.json` copied in → never applied. The amended invariant: with
  the reading kept, an unchanged rerun runs no FFmpeg; with a foreign reading, readings >= 1 and
  remuxes == 0.

## Measured on the accepted version (your numbers should match closely)
- First second: +1.82 → −0.01 dB with music, +1.83 → 0.00 without.
- Sync within ±1 sample; the limiter adds 0 delay.
- A 15-minute worst case: up to 4 passes of 17–28 s, on input change only.

## Rules
- Read `.claude/agents/implementer.md`.
- Worktree only. Scratch files in your own scratch folder.
- Full suite once, under flock.
- **DURABILITY (new):** after EVERY commit, run `git push -u origin wip/p2-8`, the branch name in
  your prompt. Containers get replaced; unpushed work is lost.
