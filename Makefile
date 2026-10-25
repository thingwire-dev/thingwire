# ThingWire developer and agent entry points. `make verify` is the definition of done.
# STRICT_FILES: blocking ruff + mypy --strict. Override: make verify STRICT_FILES="a.py b.py"
STRICT_FILES ?= $(shell grep -hv '^\#' .agents/strict-files.txt | grep -v '^$$')
BASE ?= origin/release/robot-stack-v0
UV ?= uv run --extra dev

.PHONY: verify verify-fast lint types test dashes ownership firmware worktree worktree-clean help

help:
	@echo "verify | verify-fast | firmware | worktree NAME= BRANCH= BASE= | worktree-clean"

verify: lint types test dashes

# No full pytest: lint, types and dash check only, for quick loops.
verify-fast: lint types dashes

lint:
	$(UV) ruff check $(STRICT_FILES)

types:
	$(UV) mypy --strict $(STRICT_FILES)

test:
	$(UV) pytest -q

dashes:
	$(UV) python scripts/check_dashes.py --base $(BASE)

ownership:
	$(UV) python scripts/check_ownership.py --base $(BASE)

firmware:
	cd firmware && pio run

worktree:
	@test -n "$(NAME)" -a -n "$(BRANCH)" -a -n "$(BASE)" || \
		(echo "usage: make worktree NAME=w13 BRANCH=feat/x BASE=origin/release/robot-stack-v0"; exit 1)
	scripts/agent_worktree.sh create "$(NAME)" "$(BRANCH)" "$(BASE)"

worktree-clean:
	scripts/agent_worktree.sh prune-merged $(BASE)
