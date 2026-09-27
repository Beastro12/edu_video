#!/usr/bin/env bash
# Runs Claude Code headless in a loop: one backlog task per iteration, fresh context each
# time. State carries over through git, docs/BACKLOG.md and docs/PROGRESS.md.
# Stop at any time:  touch STOP
set -uo pipefail
cd "$(dirname "$0")/.."

MAX_ITER="${MAX_ITER:-12}"
# dontAsk: anything not allowed in .claude/settings.json is denied instead of waiting
# for approval, so an unattended run can't hang on a prompt.
MODE="${MODE:-dontAsk}"

mkdir -p logs
git rev-parse --verify auto/dev >/dev/null 2>&1 || git branch auto/dev
git switch auto/dev || exit 1

PROMPT='Run exactly one iteration of the loop in CLAUDE.md: pick the top unblocked task in docs/BACKLOG.md, write the failing test, implement, make check, get the reviewer subagent to review, commit, update BACKLOG and PROGRESS. Then end your turn. If a stop condition in CLAUDE.md applies, create the STOP file with the reason and end.'

for i in $(seq 1 "$MAX_ITER"); do
  if [ -f STOP ]; then echo "STOP: $(cat STOP)"; break; fi
  if ! grep -q '^- \[ \] ' docs/BACKLOG.md; then echo "No open tasks left."; break; fi
  log="logs/$(date +%Y%m%d_%H%M%S)_iter$i.log"
  echo "=== iteration $i/$MAX_ITER -> $log"
  claude -p "$PROMPT" --permission-mode "$MODE" > "$log" 2>&1
  code=$?
  if [ $code -ne 0 ]; then echo "claude exited with $code, see $log"; break; fi
  git log -1 --oneline
done

echo "--- final check"
if make check >/dev/null 2>&1; then echo "make check: green"; else echo "make check: FAILING, see PROGRESS.md"; fi
[ -f docs/NEEDS_PIETRO.md ] && echo "Items waiting for you: docs/NEEDS_PIETRO.md"
