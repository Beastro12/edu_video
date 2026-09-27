# Calm science documentary generator (10–20 min, sleep-friendly)

> Handing this to Claude Code? Start with **KICKOFF.md**.

Several AI agents make one narrated, slow-paced science documentary:

| Step | Agent | Job |
|---|---|---|
| 1 | Outline agent (Claude) | Plans 4–6 chapters that build on each other |
| 2 | Writer (Claude) | Writes one chapter at a time, continuing from the last lines of the previous one |
| 3 | Critic (Claude) | Reviews each chapter for scientific accuracy and calm tone, up to 2 rounds |
| 4 | Voice (ElevenLabs) | Soft, slow narration per scene. **Its length sets each scene's length** |
| 5a | Stills (Google Gemini image model) | ~60% of scenes: one dark, calm image with a slow drift (zoom or pan) |
| 5b | Manim (Claude writes code) | ~30%: gentle diagrams; render errors are fed back to Claude for up to 3 fixes |
| 5c | Veo (Google) | At most one moving shot per chapter |
| 6 | Assembly (FFmpeg) | Crossfades between scenes, seamless music bed ducked under the voice, −16 LUFS |

Every still is checked by Claude (vision) for text, wrong physics and anything unsettling;
a rejected one is regenerated with the critique (twice at most). Failures fall back instead
of stopping: Veo → still → Manim. Without a Google key, every scene is rendered with Manim.

Everything caches in `build/<topic>/` (outline, each chapter, audio, visuals). If a run
crashes, rerun the same command and it resumes without paying again. Cached files are
named by a hash of what produced them, so an edited scene is regenerated and nothing
else is.

## Setup

```bash
# System: ffmpeg + Manim's libraries
#   macOS:  brew install ffmpeg cairo pango pkg-config
#   Ubuntu: sudo apt install ffmpeg libcairo2-dev libpango1.0-dev pkg-config
# Optional: LaTeX lets Manim render proper equations (auto-detected)

python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env    # add keys and a calm ElevenLabs voice ID
```

Put soft instrumental tracks you have the rights to in `music/`. Several tracks are
shuffled and crossfaded; a single track is repeated with crossfades so there's no loop
seam. With no tracks, a synthetic placeholder pad is used for testing.

## Run

```bash
# 1. Script only: read/edit build/<topic>/script.json before spending on voice & images
python pipeline.py "What happens inside a neutron star" --minutes 15 --script-only

# 2. Full render (reuses the edited script)
python pipeline.py "What happens inside a neutron star" --minutes 15

# Skip Veo entirely (stills + Manim only, cheaper)
python pipeline.py "What happens inside a neutron star" --no-ai-video
```

## Money

Every paid call (Claude, ElevenLabs, Google images, Veo) is checked against `BUDGET_EUR` (env,
default 10) before it's made and logged to `build/ledger.jsonl` after it succeeds, with
its estimated cost. The budget is the total across all runs: when the next call would
pass it, the run stops with `BudgetExceeded` (it never falls back to a cheaper visual).
Only Pietro raises `BUDGET_EUR` in `.env` or archives the ledger; the unattended agent
never touches either. `build/` and `music/` are resolved from the project folder, so the
ledger is the same whatever directory you run from.

```bash
# What would this cost? Counts only work that isn't cached yet, prints a typical and a
# worst-case total, calls nothing.
python pipeline.py "What happens inside a neutron star" --minutes 15 --estimate
```

Prices are estimates in `config.py`, marked "verify": check them against each provider's
price page. Veo dominates: about €3 per clip, up to one clip per chapter. Veo is also capped at
`VEO_MAX_PER_DAY` clips per UTC day (2, counted from the ledger); past that, Veo scenes get a
still instead.

Edit the script in `script.json`, or in a `chapter_XX.json` (script.json is then
rebuilt from the chapters). If both were edited, the run stops and asks you to keep one.
Editing a scene regenerates only that scene's narration, visual and clip, then the final
assembly. `manifest.json` lists the files each scene used: to re-roll one with unchanged
text (say, a still you don't like), delete its file under `visuals/` and rerun. To have Claude
rewrite a chapter, delete its `chapter_XX.json` and `script.json`.

## Quality check

Every run ends with `qa.py`, which writes `qa.json` next to the film and fails the run if
the film misses a target: length vs the script, loudness (−16 LUFS ±1 LU), true peak
(≤ −1 dBTP), music ducking under the voice (≥ 6 dB), black frames outside the opening and
closing fades, and narration inside a crossfade. Run it on its own with
`python qa.py build/<topic>/<film>.mp4` (exit code 1 on failure).

## YouTube files

Each run also writes `metadata.json` (title, description, chapter timestamps) and
`subtitles.srt` into the build folder, timed from the clips and narration actually used.
Chapters follow YouTube's rules: the first at 00:00, each at least 10 s, and at least three
(otherwise the list is left out of the description). The description's opening line is
`YOUTUBE_DESCRIPTION` in `config.py`.

## Tuning

Everything is in `config.py`: voice calmness and speed, pauses, crossfade length, how
far stills drift, music level and ducking. Model names can be overridden in `.env`
(`CLAUDE_MODEL`, `IMAGE_MODEL`, `VEO_MODEL`), since Google renames them often.

Render time: the slow drift on stills is the heaviest step, roughly 1–3× real time per
still scene depending on your CPU, so a 15-minute video takes a while to assemble.
