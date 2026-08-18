# CRYSTALIUM v0.1.0 — Container-first build
# Host runs only: docker compose + git
# Do NOT run uv/pip/python/pytest on the host.
#
# Source: MISSION.md §Container-first + crystalium-v0.1.0-spec.md §10

FROM python:3.12-slim AS base

# Torch variant selector. cpu (default) installs the CPU-only torch wheel
# (no nvidia/* , no triton — venv ~1 GB). gpu installs the CUDA cu121 wheel
# (amd64-only — venv ~5 GB). Override at build time:
#   docker compose build                         # cpu (default)
#   make build VARIANT=gpu                        # gpu
#   docker build --build-arg TORCH_VARIANT=gpu .  # gpu, plain docker
ARG TORCH_VARIANT=cpu

# Install curl for uv and jq for the EIIS v3 package installer.
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates jq \
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

# Install RUNTIME dependencies only (no dev toolchain — that lands in the `dev`
# stage below). torch resolves CPU-only by default (see pyproject [tool.uv.sources]);
# this is the layer the published GHCR image ships, so it must stay slim.
# The gpu branch replaces the CPU wheel with the CUDA cu121 build (amd64-only);
# it is a no-op for the default cpu variant, so the CPU image never pulls CUDA.
# uv sync runs INSIDE the container — never on the host (P0-13)
RUN uv sync --no-cache \
    && if [ "$TORCH_VARIANT" = "gpu" ]; then \
         uv pip install --reinstall torch --index-url https://download.pytorch.org/whl/cu121; \
       fi

# Copy source tree
COPY mcp-server/src ./src
COPY schemas ./schemas
# Copy the evals package so the ablation/canary bench runs in-container + CI.
COPY evals ./evals

# Set PYTHONPATH so crystalium (in src/) AND the evals package (at /app) are
# importable without install.
ENV PYTHONPATH="/app/src:/app"

# Default entry point — override in docker-compose for specific commands.
# --no-sync: run against the venv baked at build time WITHOUT re-resolving.
# Without it, `uv run` re-syncs on every container start and re-pulls torch from
# PyPI (the full CUDA stack), defeating the slim CPU image. The baked venv is
# already the source of truth; dependency changes require an explicit `uv sync`.
ENTRYPOINT ["uv", "run", "--no-sync"]
CMD ["python", "-m", "crystalium"]

# ---------------------------------------------------------------------------
# Dev image — used by docker-compose.dev.yml
# ---------------------------------------------------------------------------
FROM base AS dev

# ARG does not cross FROM boundaries — re-declare so the dev sync preserves the
# variant resolved in `base`.
ARG TORCH_VARIANT=cpu

# Layer the dev toolchain (pytest, ruff, mypy, jsonschema) on top of the runtime
# venv. Only the dev image carries this — the published runtime image does not.
# Re-apply the gpu override after sync (uv sync would otherwise restore the
# CPU-default torch); no-op for the cpu variant.
RUN uv sync --extra dev --no-cache \
    && if [ "$TORCH_VARIANT" = "gpu" ]; then \
         uv pip install --reinstall torch --index-url https://download.pytorch.org/whl/cu121; \
       fi

# Copy tests into the image
COPY mcp-server/tests ./tests

# Bake the canonical EIIS v3 package into the dev image for package smoke tests.
COPY install.sh PERSONA.md SPEC.md ECL_VERSION manifest.json ./
COPY skills ./skills

# Data-dir mountpoint for the compose dev volume (crystalium#66).
#
# docker-compose.yml runs this image as the HOST uid (see its `user:` key) so writes
# into the bind-mounted source tree land owned by the developer rather than by root.
# That makes the previous data dir unusable: it lived on a named volume at
# /root/.crystalium, and /root is mode 0700 owned by root, so a non-root uid cannot
# open SQLite/LanceDB/Kuzu there.
#
# Docker seeds a fresh named volume from the image's directory at the mount point,
# mode included — so pre-creating /data as 1777 (sticky, like /tmp) makes the volume
# writable by ANY host uid. Hardcoding a uid here would break every developer whose
# `id -u` is not 1000.
#
# DEV STAGE ONLY, deliberately. The published image is built from `base`
# (release.yml `target: base`) and is left untouched: it still runs as root and still
# resolves its data dir under $HOME, so existing MCP wiring that bind-mounts a host
# directory onto /root/.crystalium/<project> keeps working. A `--user` pin belongs on
# that `docker run` invocation — where the bind mount already carries host ownership —
# not baked into the image.
RUN mkdir -p /data && chmod 1777 /data

# For pytest runs via docker compose
CMD ["pytest", "tests/", "-v"]
