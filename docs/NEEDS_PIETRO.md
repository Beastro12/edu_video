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
**Recommend:** wait until P0-6 (Imagen → Gemini image model) is done: with google-genai
2.25 the current still code refuses API-key mode, so the image check would fail and waste
the one run. Then, on your machine: `cp .env.example .env`, fill it in, then `make smoke` once
(expected cost well under €1; `--estimate` shows it). If you want this cloud session to run it
instead: add the four as environment variables in the cloud environment's settings (environment
menu in the session title bar → Edit), and allow network access to `api.anthropic.com`,
`api.elevenlabs.io` and `generativelanguage.googleapis.com` (this environment's proxy
already refused `elevenlabs.io` and `ai.google.dev`). A new session picks the variables up.
