.PHONY: check lint test smoke

# Offline gate: must pass before every commit. Costs nothing.
check: lint test

lint:
	ruff check .

test:
	pytest -q

# LIVE: calls paid APIs. Only run under the budget rules in CLAUDE.md.
smoke:
	pytest -q -m live
