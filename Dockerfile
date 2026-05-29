# CRYSTALIUM v0.1.0 — Container-first build
# Host runs only: docker compose + git
# Do NOT run uv/pip/python/pytest on the host.
#
# Source: MISSION.md §Container-first + crystalium-v0.1.0-spec.md §10

FROM python:3.12-slim AS base

# Install curl for uv installer (and as a useful tool for the doctor command)
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install uv (fast Python package manager).
# Pinned to a stable installer URL — ghcr.io/astral-sh/uv image registry pulls
# proved flaky during initial bootstrap (DeadlineExceeded). The installer
# script is self-contained and idempotent.
ENV UV_INSTALL_DIR=/usr/local/bin
RUN curl -LsSf https://astral.sh/uv/install.sh | sh \
    && uv --version

WORKDIR /app

# Copy pyproject.toml + README first for layer caching.
# README.md is at the repo root (not under mcp-server/); hatchling reads it
# via [project.readme] during 'uv sync', so it MUST be in the build context
# before sync runs.
COPY mcp-server/pyproject.toml mcp-server/.python-version ./
COPY README.md ./

# Install all dependencies including dev group (needed for pytest)
# uv sync runs INSIDE the container — never on the host (P0-13)
RUN uv sync --extra dev --no-cache

# Copy source tree
COPY mcp-server/src ./src
COPY schemas ./schemas
# Copy the evals package so the ablation/canary bench runs in-container + CI.
COPY evals ./evals

# Set PYTHONPATH so crystalium (in src/) AND the evals package (at /app) are
# importable without install.
ENV PYTHONPATH="/app/src:/app"

# Default entry point — override in docker-compose for specific commands
ENTRYPOINT ["uv", "run"]
CMD ["python", "-m", "crystalium"]

# ---------------------------------------------------------------------------
# Dev image — used by docker-compose.dev.yml
# ---------------------------------------------------------------------------
FROM base AS dev

# Copy tests into the image
COPY mcp-server/tests ./tests

# For pytest runs via docker compose
CMD ["pytest", "tests/", "-v"]
