# CRYSTALIUM v0.1.0 — Makefile
# Host-visible commands only. All Python tooling runs INSIDE Docker.
# Host requires: docker, git (make is optional wrapper).
#
# Source: crystalium-v0.1.0-spec.md §10 Container-first build protocol.

COMPOSE = docker compose
SERVICE = crystalium
RUN     = $(COMPOSE) run --rm $(SERVICE)

# Torch build variant: cpu (default, ~2 GB image) or gpu (~6 GB, amd64-only).
# Usage: make build VARIANT=gpu
VARIANT ?= cpu

# ---------------------------------------------------------------------------
# Primary targets
# ---------------------------------------------------------------------------

.PHONY: build test test-fast test-ci test-schemas test-storage test-w1 bench bench-axes lint typecheck clean shell help

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
