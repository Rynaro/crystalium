# CRYSTALIUM v0.1.0 — Makefile
# Host-visible commands only. All Python tooling runs INSIDE Docker.
# Host requires: docker, git (make is optional wrapper).
#
# Source: crystalium-v0.1.0-spec.md §10 Container-first build protocol.

# Run containers as the invoking user so writes into the bind-mounted source tree
# land owned by the developer, not root (crystalium#66). docker-compose.yml reads
# these; the defaults there only apply to a bare `docker compose` invocation.
export DOCKER_UID := $(shell id -u)
export DOCKER_GID := $(shell id -g)

COMPOSE = docker compose
SERVICE = crystalium
RUN     = $(COMPOSE) run --rm $(SERVICE)

# Torch build variant: cpu (default, ~2 GB image) or gpu (~6 GB, amd64-only).
# Usage: make build VARIANT=gpu
VARIANT ?= cpu

# ---------------------------------------------------------------------------
# Primary targets
# ---------------------------------------------------------------------------

.PHONY: build test test-fast test-ci test-schemas test-storage test-w1 bench bench-axes lint typecheck clean shell check-ownership fix-ownership help

## build: Build the crystalium container image (VARIANT=cpu|gpu, default cpu)
build:
	TORCH_VARIANT=$(VARIANT) $(COMPOSE) build $(SERVICE)

## test: Run the full test suite inside the container
test:
	$(RUN) pytest mcp-server/tests/ -v

## test-fast: Run test suite skipping slow (model download) tests
test-fast:
	$(RUN) env CRYSTALIUM_SKIP_SLOW=1 pytest mcp-server/tests/ -v -m "not slow"

## test-ci: Reproduce CI exactly (SKIP_SLOW=1 with slow tests still SELECTED)
test-ci:
	$(RUN) env CRYSTALIUM_SKIP_SLOW=1 pytest mcp-server/tests/ -v

## test-schemas: Run only schema + Pydantic tests (W1 fast subset)
test-schemas:
	$(RUN) pytest mcp-server/tests/test_schemas.py -v

## test-storage: Run storage adapter tests (W1 scope)
test-storage:
	$(RUN) pytest \
		mcp-server/tests/test_storage_blob.py \
		mcp-server/tests/test_storage_relational.py \
		mcp-server/tests/test_storage_vector.py \
		mcp-server/tests/test_storage_graph.py \
		-v

## test-w1: Run the full Wave 1 container test as specified in spec.yaml
test-w1:
	$(RUN) pytest \
		mcp-server/tests/test_schemas.py \
		mcp-server/tests/test_storage_relational.py \
		mcp-server/tests/test_storage_vector.py \
		mcp-server/tests/test_storage_graph.py \
		mcp-server/tests/test_storage_blob.py \
		mcp-server/tests/test_importance.py \
		mcp-server/tests/test_config.py \
		-v

## bench: Run the ablation/canary bench (memory-on/off A/B headline) in-container
bench:
	$(RUN) python -m evals canary --mode both

## bench-axes: Print SWE-Bench-CL axes from the demo accuracy matrix (dep-free smoke)
bench-axes:
	$(RUN) python -m evals axes --demo

## lint: Run ruff linter inside the container
lint:
	$(RUN) ruff check mcp-server/src mcp-server/tests

## typecheck: Run mypy inside the container
typecheck:
	$(RUN) mypy mcp-server/src/crystalium

## shell: Open a bash shell inside the container (for debugging)
shell:
	$(COMPOSE) run --rm --entrypoint bash $(SERVICE)

## check-ownership: Assert the container left nothing the host user cannot delete (crystalium#66)
#
# Gates on REMOVABILITY, not on raw ownership — the two differ, and the difference
# is not cosmetic. Docker creates the `/app/.venv` anonymous-volume mountpoint as
# root no matter what `user:` says (the daemon does it, not the container process),
# but it is an EMPTY directory in a developer-owned parent, so `rmdir` clears it
# without sudo. An ownership-only gate would flag it and invite an exception list,
# which is how a gate rots into a formality.
#
# The rule itself lives in scripts/check-ownership.sh, which documents why the
# obvious one-line form has a silent hole for mode-0700 directories.
check-ownership:
	@echo "==> forcing the container to write into the bind-mounted tree"
	@$(RUN) python -c "import compileall; compileall.compile_dir('/app/evals', quiet=2)" >/dev/null || true
	@echo "==> scanning for paths the host user (uid $$(id -u)) cannot delete"
	@bash scripts/check-ownership.sh

## fix-ownership: Reclaim root-owned files left by containers from before crystalium#66
#
# One-time migration for a checkout that predates the `user:` key. Containers used to
# run as root, so caches they wrote (__pycache__/, .pytest_cache/, .ruff_cache/, and
# the .venv mountpoint) are owned by uid 0 and cannot be chown'd or deleted by the
# developer — `sudo` is one answer, but not everyone has it on a work machine.
#
# Instead we use the tool that created the mess: a throwaway root container with the
# tree mounted. chown, not rm, so existing caches survive the migration intact.
# --entrypoint sh, NOT the default: the image's entrypoint is `uv run --no-sync`, and
# a plain `docker run` has no anonymous volume shadowing /app/.venv — so uv helpfully
# creates a fresh venv straight into the bind-mounted tree. Measured, the first draft
# of this target did exactly that.
#
# chown -h so symlinks are retargeted rather than followed: .venv/bin/python and
# .venv/lib64 are links, and a plain chown would silently fix their targets and leave
# the links themselves root-owned.
fix-ownership:
	@echo "==> reclaiming paths not owned by uid $$(id -u)"
	@docker run --rm -u 0:0 -v "$$PWD":/app -w /app --entrypoint sh crystalium:dev \
		-c "find /app -not -user $$(id -u) -exec chown -h $$(id -u):$$(id -g) {} +" || true
	@echo "==> remaining foreign-owned paths: $$(find . -path ./.git -prune -o ! -uid $$(id -u) -print 2>/dev/null | wc -l)"

## clean: Remove the crystalium_data volume (DESTRUCTIVE — deletes all stored crystals)
clean:
	@echo "WARNING: This will delete all stored crystal data."
	@read -p "Are you sure? [y/N] " ans && [ "$$ans" = "y" ]
	$(COMPOSE) down -v

# ---------------------------------------------------------------------------
# Help
# ---------------------------------------------------------------------------

.DEFAULT_GOAL := help

help:
	@grep -E '^## ' Makefile | sed 's/^## /  /'
