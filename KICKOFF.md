# Handing this to Claude Code

## 1. One-time setup (≈15 min)

```bash
unzip edu_video.zip && cd edu_video
git init && git add -A && git commit -m "baseline from chat"

# system deps (macOS shown; Ubuntu: sudo apt install ffmpeg libcairo2-dev libpango1.0-dev pkg-config)
brew install ffmpeg cairo pango pkg-config

python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
make check          # expect: ruff clean, 6 passed
```

Then `cp .env.example .env` and fill in your keys and a calm ElevenLabs voice ID.
Without keys Claude Code still works on everything offline and lists what it needs
from you in `docs/NEEDS_PIETRO.md`.

Optional: drop a few royalty-free instrumental tracks into `music/`.

## 2. First session: watch one iteration

```bash
source .venv/bin/activate
claude --permission-mode auto
```

Paste:

> Read CLAUDE.md, docs/DECISIONS.md, docs/BACKLOG.md and the last entry in
> docs/PROGRESS.md. Run `make check` to confirm the baseline is green, then create and
> switch to branch auto/dev. Start the iteration loop from CLAUDE.md with P0-1 and keep
> going task after task until a stop condition in CLAUDE.md applies. Don't ask me
> questions mid-task: decide, record it under ASSUMED, and put anything that truly needs
> me in docs/NEEDS_PIETRO.md.

Watching the first task tells you whether the permissions and the loop behave.

## 3. Unattended: run it without you

```bash
source .venv/bin/activate
MAX_ITER=12 ./scripts/run_autonomous.sh
```

Each iteration is a fresh headless session (`claude -p`) doing one backlog task, so
context never fills up; memory lives in git and the docs. It stops when the backlog
is done, when Claude writes a `STOP` file, or after `MAX_ITER` iterations.

- Stop it any time: `touch STOP` (delete the file to allow runs again)
- Logs: `logs/`

## 4. Checking in

- `docs/PROGRESS.md` — what was done, verified, assumed
- `docs/NEEDS_PIETRO.md` — decisions and keys it's waiting on
- `git log --oneline auto/dev` — one commit per task
- `STOP` — why it stopped, if it did

When happy, merge `auto/dev` into your main branch yourself. Claude Code is not
allowed to push.
