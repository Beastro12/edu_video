# Calm science documentary generator (10–20 min, sleep-friendly)

> Handing this to Claude Code? Start with **KICKOFF.md**.

Several AI agents make one narrated, slow-paced science documentary, optionally from a
research brief of verified facts (for films about recent research) and in one of two looks:

| Step | Agent | Job |
|---|---|---|
| 1 | Outline agent (Claude) | Plans 4–6 chapters that build on each other |
| 2 | Writer (Claude) | Writes one chapter at a time, continuing from the last lines of the previous one |
| 3 | Critic (Claude) | Reviews each chapter for scientific accuracy and calm tone, up to 2 rounds |
| 4 | Voice (ElevenLabs) | Soft, slow narration per scene. **Its length sets each scene's length** |
| 5a | Stills (Google Gemini image model) | ~60% of scenes: one calm image in the film's style (`calm`: dark, cinematic; `flat`: Kurzgesagt-inspired flat vector) with a slow drift |
| 5b | Manim (Claude writes code) | ~30%: gentle diagrams; render errors are fed back to Claude for up to 3 fixes |
| 5c | Veo (Google) | At most one moving shot per chapter |
| 6 | Assembly (FFmpeg) | Each narration levelled to one loudness, crossfades, music ducked under the voice, −16 LUFS |
| 7 | Wrap-up | QA report, YouTube metadata (chapters, the brief's Sources), subtitles, thumbnail, `run_report.md` |

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

python3.11 -m venv .venv && source .venv/bin/activate   # the lock is made for Python 3.11
pip install -r requirements.lock    # the exact versions the tests ran with (dev tools included)
cp .env.example .env    # add keys and a calm ElevenLabs voice ID
```

`requirements.txt` and `requirements-dev.txt` keep the version ranges, for upgrading on purpose; after an upgrade, run
`make check` and regenerate the lock (the command is in the header of `requirements.lock`).

Put soft instrumental tracks you have the rights to in `music/`. Several tracks are
shuffled and crossfaded; a single track is repeated with crossfades so there's no loop
seam. With no tracks, a synthetic placeholder pad is used for testing.

## Make the first film

The first film's script is already written and reviewed. It is "A Small Lit Stage: Global
Workspace Theory and Language Models", from the headline "Do AIs already process information in a
way that gives rise to consciousness?" (New Scientist, 12 Oct 2026). Its research brief, outline,
chapters and a readable `script.md` are in `topics/ai-global-workspace/`.

1. **Keys.** Put four values in `.env`: `ANTHROPIC_API_KEY`, `ELEVENLABS_API_KEY`,
   `ELEVENLABS_VOICE_ID` (a calm voice) and `GOOGLE_API_KEY`. In a Claude Code cloud environment,
   set them as environment variables instead, and allow `api.elevenlabs.io`.
2. **Check the keys and model names once, for about €0.10–0.50:**
   ```bash
   make smoke
   ```
3. **See what the film will cost.** This calls nothing and writes nothing:
   ```bash
   python pipeline.py "Do AIs already process information in a way that gives rise to consciousness?" \
     --brief topics/ai-global-workspace/brief.md --style flat --no-ai-video --estimate
   ```
4. **Make it.** Run the same command without `--estimate`. Meanwhile:
   - Look at the first stills as they land in `build/<topic>/visuals/`. If they don't look like
     the flat style you want, stop with Ctrl-C. That's safe: a rerun resumes and buys nothing
     twice.
   - Reruns can leave out `--brief` and `--style`; the build remembers both.
   - Drop `--no-ai-video` to allow Veo, at most 2 clips a day; `--estimate` shows the cost.
5. **Read what came out.** All of it is in `build/<topic>/`:
   - `run_report.md`: outcome, costs, timings, fallbacks, QA and next steps;
   - the film `<title>.mp4`;
   - `metadata.json`: title, description with chapters and the brief's Sources;
   - `subtitles.srt`;
   - `thumbnail.jpg`.
6. **Before publishing**, put music you hold rights to in `music/` and rerun. Only the mix is
   redone; nothing paid is bought again. Without music, the film has a placeholder pad.

### Another research film

1. **Write a brief.** Create `topics/<name>/brief.md`, using `topics/ai-global-workspace/brief.md`
   as the model:
   - what the film is;
   - binding hedging rules;
   - facts labelled by source;
   - a `## Sources` list, one entry per line.
2. **Write and review the script.** Run `python pipeline.py "<topic>" --brief topics/<name>/brief.md
   --script-only`, then read `build/<topic>/script.json`.
3. **Optionally, keep the reviewed script with its brief:** copy `outline.json` and
   `chapter_NN.json` into the folder, and write `brief.key` (the command is in the error a
   missing one gives). `tests/test_topics.py` then checks the folder in `make check`.

## Run

```bash
# 1. Script only: read/edit build/<topic>/script.json before spending on voice & images
python pipeline.py "What happens inside a neutron star" --minutes 15 --script-only

# 2. Full render (reuses the edited script)
python pipeline.py "What happens inside a neutron star" --minutes 15

# Skip Veo entirely (stills + Manim only, cheaper)
python pipeline.py "What happens inside a neutron star" --no-ai-video

# Ask Veo / the image model again for scenes whose generation failed on an earlier run
python pipeline.py "What happens inside a neutron star" --retry-failed

# Skip the critic's review of each chapter (cheaper; not for films you publish)
python pipeline.py "What happens inside a neutron star" --skip-critic

# A film about recent research, from a brief of verified facts (see "Research briefs" below)
python pipeline.py "A paper from last month" --brief topics/<name>/brief.md

# Kurzgesagt-inspired flat vector pictures, same calm voice. The build remembers the style;
# VISUAL_STYLE in .env sets the default (calm).
python pipeline.py "What happens inside a neutron star" --style flat
```

### Research briefs

A brief is a markdown file: what the film is, binding hedging rules, facts labelled by source,
and a `## Sources` heading with one entry per line (they go into the YouTube description).

- The outline agent, the writer and the critic all get it. The critic generalises or drops any
  claim the brief doesn't support.
- A brief change after the script exists is refused; a rerun may leave `--brief` out and uses
  the kept copy.
- **A reviewed script can sit beside the brief** (see `topics/`): an `outline.json`, the
  `chapter_NN.json` files, and a `brief.key` recording the brief version they were reviewed
  with.
  - A run with `--brief` copies in whichever of those files the build lacks, so only chapters
    the folder doesn't have go to the writer.
  - After editing the brief, re-review the chapters and update `brief.key` (the error says how);
    until then seeding is refused.
  - A rerun without `--brief` doesn't seed: a deleted chapter would be written afresh.

A Veo clip or a still that fails for good (a safety filter, an invalid prompt, a Veo job
that never finishes) is recorded in `build/<topic>/failures.json`, and later runs go straight to that
scene's fallback (still, then Manim) instead of paying or waiting for the same failure again;
the run says so for each such scene. `--retry-failed` asks again, and a success clears the
entry; use it too after a change that could fix a failure without changing the prompt (a new
key, a longer timeout). Errors that say nothing about the request (network, rate limits,
server errors, a full disk), budget stops, the Veo daily cap and Ctrl-C are not recorded.

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
price page. A Veo clip is about €3 (Veo 3.1 at 1080p), up to one clip per chapter; with
`VEO_MODEL=veo-3.1-lite-generate-preview` in `.env`, about €0.60 (the cheapest tier, DECISIONS
D20). Veo is also
capped at `VEO_MAX_PER_DAY` clips per UTC day (2, counted from the ledger); past that, Veo
scenes get a still instead.

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

## Run report

Every run (not `--estimate`) writes `build/<topic>/run_report.md`, also when it stops partway:
- the outcome, flags, style, brief and models;
- what this run and this film cost (from the ledger);
- time per stage;
- each scene's planned and used visual, with the recorded reason for any fallback;
- QA check by check;
- the thumbnail's source;
- next steps.
The previous report is kept as `run_report.prev.md`. Keys never appear in it.

## YouTube files

Each run also writes `metadata.json` (title, description, chapter timestamps) and
`subtitles.srt` into the build folder, timed from the clips and narration actually used.
Chapters follow YouTube's rules: the first at 00:00, each at least 10 s, and at least three
(otherwise the list is left out of the description). The description's opening line is
`YOUTUBE_DESCRIPTION` in `config.py`.

It also writes `thumbnail.jpg` (1280×720 JPEG, under YouTube's 2 MB) and names it in
`metadata.json`. The picture is one image from the image model, drawn from the first still
scene's description in the build's style and asked for no text (about the cost of a still; a
rerun doesn't buy it again); the title is drawn on the left with Pillow, and a title with a
": " (colon and space, with text on both sides) is split there into a large headline and a
smaller subtitle. Without a
`GOOGLE_API_KEY`, or when the image can't be had, it is a frame of the film instead. A thumbnail
that can't be made is logged and never fails the run.

## Tuning

Scenes are narrated and illustrated `WORKERS` at a time (see `config.py`). The budget cap
still holds with calls in flight: each paid call reserves its estimated cost before it starts
(Claude's actual tokens can differ a little from the estimate).

Everything is in `config.py`: voice calmness and speed, pauses, crossfade length, how
far stills drift, music level and ducking. Model names can be overridden in `.env`
(`CLAUDE_MODEL`, `IMAGE_MODEL`, `VEO_MODEL`, `TTS_MODEL`), since providers rename them often.
`TTS_MODEL=eleven_v4` switches the narration to ElevenLabs' Eleven v4 (DECISIONS D21): it
reportedly has no speed setting, so compare it first with
`pytest -s -m live_voice tests/test_live_optin.py` (about €0.12, four short files to listen to).

Render time (4 cores; DECISIONS D18, measured with `scripts/bench_stills.py`): each second
of a still scene costs about 1.1–1.3 s of FFmpeg in all (its clip plus its share of the film's
final encode), of a Manim scene about 0.4 s (plus Manim's own render), of a Veo scene about
2.3 s. A 15-minute film therefore spends roughly 15–20 minutes in FFmpeg (an estimate for a
typical scene mix, not a measured run).
