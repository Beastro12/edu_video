# Needs Pietro

Decisions and access only you can give. Newest at the bottom. Each: what, why, what was
tried, what I'd recommend.

## 2026-09-27 — Veo alone costs more than the default budget (money decision)
**What:** decide whether films use Veo by default, and what `BUDGET_EUR` should be.
**Why:** `python pipeline.py "<topic>" --minutes 15 --estimate` (P0-2) projects ~€2.06
without Google keys and ~€16.87 with them. Veo accounts for €14.72 of that: 5 chapters × one
8 s clip × $0.40/s. The default `BUDGET_EUR` is €10 *in total across runs*, so one full film
with Veo would stop partway. Prices are estimates from secondary sources, not verified.
**Tried:** nothing to try; this is a cost/taste call.
**Recommend:** make `--no-ai-video` the default until you've seen whether Veo shots are worth
~€3 each for sleep content (stills already drift slowly), and keep €10 as the cap for the
first live test runs. The cap is cumulative, so if you want Veo, raise `BUDGET_EUR` by
about €20 for each Veo film you plan.
**Update (foundation check, same day):** the numbers above predate the Veo daily cap (P0-7).
With it, `--estimate` for a 15-min film with Google now says €8.85 typical (Veo €5.89 for
2 clips; the other Veo scenes get stills) and €15.16 worst case; without Veo, €2.06. So one
film fits the €10 budget in the typical case but may not in the worst, and a second film
would not. The decision is unchanged; the "about €20 per Veo film" becomes about €9–15.
**Update 2026-09-29 (D20):** see the entry "Veo 3.1 or Veo 3.1 Lite" at the end: Lite would
bring the same film to €4.14 typical and €10.45 worst case.

## 2026-09-27 — Lock the spend ledger against the agent (permissions: yours to change)
**What:** two optional hardening changes that only you should make, because they change
the agent's own rules and permissions.
**Why:** the P0-2 review pointed out that `.claude/settings.json` allows `Edit`/`Write` on
every file, including `build/ledger.jsonl` and `.env`. An unattended agent could (by mistake)
reset its own budget. The code now anchors the ledger to the project folder and the README
says only you raise the budget, but nothing technically stops a file edit.
**Tried:** nothing; I don't edit CLAUDE.md or permission settings myself.
**Recommend:** (1) add to `.claude/settings.json` → `permissions.deny`:
`"Edit(./build/ledger.jsonl)"`, `"Write(./build/ledger.jsonl)"`, `"Edit(./.env)"`,
`"Write(./.env)"`; (2) add to CLAUDE.md's hard rules: "Never edit, move or delete
`build/ledger.jsonl`, and never set or raise `BUDGET_EUR`."

## 2026-09-27 — API keys (and network access) for the live smoke test, P0-3
**What:** `ANTHROPIC_API_KEY`, `ELEVENLABS_API_KEY`, `ELEVENLABS_VOICE_ID` (a calm voice from
the ElevenLabs Voice Library) and `GOOGLE_API_KEY` (Gemini API).
**Why:** P0-3 must confirm live, once: the Claude model name is accepted, ElevenLabs accepts
`speed` in voice_settings, and the image model works (P0-6: Imagen 4 appears to be retired;
see BACKLOG). None of the four variables is set here and there is no `.env`.
**Tried:** checked presence only (never printed). `tests/test_live.py` is written and skips
until the keys exist.
**Recommend:** P0-6 is done (stills now use the Gemini image model; the old Imagen call
could never succeed with google-genai 2.25), so the one smoke run is now worth doing. It
also checks the new default model names, which are unverified. On your machine: `cp .env.example .env`, fill it in, then `make smoke` once
(expected cost well under €1; `--estimate` shows it). If you want this cloud session to run it
instead: add the four as environment variables in the cloud environment's settings (environment
menu in the session title bar → Edit), and allow network access to `api.anthropic.com`,
`api.elevenlabs.io` and `generativelanguage.googleapis.com` (this environment's proxy
already refused `elevenlabs.io` and `ai.google.dev`). A new session picks the variables up.

## 2026-09-27 — Is the new Veo model name right? (money: ~€3 to find out)
**What:** whether to spend one Veo clip (~€3, one of the 2 allowed per day) to verify
`VEO_MODEL` (`veo-3.1-generate-preview`, from secondary sources).
**Why:** `make smoke` runs without Veo, so a wrong Veo name would only show as every Veo
scene quietly becoming a still.
**Tried:** wrote `test_veo_model_makes_one_clip` in `tests/test_live.py`, marked `live_veo`;
it never runs by default (not in `make check` or `make smoke`).
**Recommend:** only if you want Veo in films at all (see the first entry): after `make smoke`
passes, run `pytest -m live_veo tests/test_live.py` once.
**Update 2026-09-29 (D20):** the same check verifies whichever `VEO_MODEL` is set: about €3
with Veo 3.1, about €0.60 with Lite. It now also checks that the clip comes back at 1080p.

## 2026-09-27 — FYI: pauses between narrations are longer (taste; change if you like)
**What:** `LEAD_IN_S` 0.6 → 1.4 s and `TAIL_S` 1.8 → 1.4 s in `config.py` (P1-1, D14).
**Why:** the new QA check found every narration started halfway through a crossfade, so its
first words were faded in while the picture was still dissolving. The crossfade now sits in
silence on both sides. Side effects: 1.6 s between narrations instead of 1.2 s, and each
scene 0.4 s longer (a 15-min film has ~31 scenes, so about +13 s). Changing these values later
re-renders every Manim scene (their target length is part of the cache key: a Claude call each).
**Tried:** kept the gentle 1.2 s crossfade and split the extra silence evenly.
**Recommend:** keep it unless the pauses feel too long. Other values work as long as
`XFADE_S` stays below both, and the pause between narrations (`LEAD_IN_S + TAIL_S − XFADE_S`)
stays at least 1.2 s so the ducked music audibly comes back (config asserts both); a shorter
crossfade (e.g. 0.6 s) would keep the old length but dissolve faster.

## 2026-09-27 — FYI: YouTube description text (taste; change if you like)
**What:** every `metadata.json` description starts with `YOUTUBE_DESCRIPTION` in `config.py`,
currently "A calm, slow science documentary to relax or fall asleep to.", followed by the
chapter timestamps (P1-4).
**Why:** a placeholder I chose; the channel's voice is yours.
**Recommend:** replace it with your channel's standard text (and any music credits your
licences require). Keep it under YouTube's 5,000 bytes; the pipeline refuses longer.

## 2026-09-27 — Look: does the still drift stutter? (P1-10; taste vs render time)
**What:** a still's slow drift doesn't glide. zoompan places the picture on the chroma grid of
its 3× upscale (steps of 2 px there), so on a pan it stands still on 57% of frames and jumps
0.73 px on the rest, and a push-in moves back and forth (measured frame by frame, D18). This has been
so since the start; P1-6 didn't change it. FFmpeg's `perspective` filter glides (jitter
0.02–0.05 px vs 0.47–0.68) and is at least as sharp, but costs render time: its clip is about
twice as slow and the film encodes it more slowly, together +0.6–0.8 s per second of still
scene, roughly +6–8 min per 15-min film (estimate, assuming ~10 min of still scenes). Two cheaper
middle ways each give something up: `perspective` with linear interpolation (+4–5 min) glides
but is measurably softer (detail −10 to −22%); zoompan in yuv444p (+3–5 min) only halves the jumps (jitter 0.20–0.29).
**Why it needs you:** the jumps are under a pixel; whether they show on a real screen needs
eyes. I can measure motion but not watch it.
**Tried:** measured them (D18). `.venv/bin/python scripts/bench_stills.py --compare IMAGE`
renders a pan and a push-in of your image both ways in about two minutes and prints the four
files (their sound is a test tone: mute it).
**Recommend:** run it on one of your stills (e.g. `build/<slug>/visuals/still_*.png`) and watch
the films in pairs, full-screen, on your largest display. If you see a difference, say so and I'll do
P1-10; if not, nothing to do.

## 2026-09-27 — Music you have the rights to (licensing; blocks publishing)
**What:** soft instrumental tracks for `music/`, which is empty.
**Why:** with no tracks, every film gets a synthetic placeholder pad ("don't publish with it").
Which music the channel may use is a licensing decision.
**Tried:** checked `music/` (only `.gitkeep`). The bed builder loops and crossfades whatever is
there, so a few tracks of a few minutes each are enough.
**Recommend:** 3–6 calm instrumental tracks you hold a licence for (wav/mp3/flac/ogg/m4a),
with their credits added to the YouTube description text if the licence asks for it.

## 2026-09-27 — Run the Python tests in CI? (repo settings: yours to change)
**What:** add an `edu_video` job to `.github/workflows/ci.yml`: install ffmpeg and
`requirements-dev.txt`, then run `make check` (about 4 minutes).
**Why:** PR #3's CI runs only the Next.js jobs, so none of edu_video's 134 tests run outside
the agent's own `make check`. A regression pushed by mistake would go unnoticed.
**Tried:** nothing; the workflow is shared with the Next.js app, so I haven't touched it.
**Recommend:** yes, and I'll write it if you say so (with the lock file from P1-11, so CI
installs the tested versions).

## 2026-09-29 — Veo 3.1 or Veo 3.1 Lite? (taste + money; you asked about Veo 4)
**What:** which Veo model films use by default.
**Why:** no Veo 4 was found: not in Google's latest SDK (2.25.0, released a week ago), and not
announced according to several secondary sources (Google's pages are blocked from this
sandbox). The options on the Gemini API are Veo 3.1 (the default, the full model) and Veo 3.1
Lite (the cheapest tier), with prices from secondary sources:

| | per 8 s clip | 15-min film, typical / worst (`--estimate`) |
|---|---|---|
| Veo 3.1 (default) | ~€2.94 | €8.85 / €15.16 |
| Veo 3.1 Lite | ~€0.59 | €4.14 / €10.45 |

Both are over the €10 budget in the worst case. Nobody has seen a Lite clip.
**Tried:** checked the SDK's code and tests for model names and options (D20), and made Lite
a one-line switch (priced, and 1080p is requested explicitly). Veo's audio can't be turned
off on the Gemini API, so both prices include audio we throw away.
**Recommend:** after `make smoke`, run the one-clip Veo check twice, once per model
(`VEO_MODEL=... pytest -m live_veo tests/test_live.py`; about €3.50 and both of the day's 2
clips), and compare the clips. If Lite looks good enough for calm B-roll, add
`VEO_MODEL=veo-3.1-lite-generate-preview` to `.env`. If your `.env` already sets `VEO_MODEL`,
that setting wins over the default.
