# CRYSTALIUM v0.1.0 — Container-first build
# Host runs only: docker compose + git
# Do NOT run uv/pip/python/pytest on the host.
#
# Source: MISSION.md §Container-first + crystalium-v0.1.0-spec.md §10

FROM python:3.12-slim AS base

# Install uv (fast Python package manager)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app

# Copy pyproject.toml first for layer caching
COPY mcp-server/pyproject.toml mcp-server/.python-version ./

# Install all dependencies including dev group (needed for pytest)
# uv sync runs INSIDE the container — never on the host (P0-13)
RUN uv sync --extra dev --no-cache

# Copy source tree
COPY mcp-server/src ./src
COPY schemas ./schemas

# Set PYTHONPATH so crystalium package is importable without install
ENV PYTHONPATH="/app/src"

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
