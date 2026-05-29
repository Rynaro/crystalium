# Claude Code integration

This repo follows the AGENTS.md open standard. The authoritative entry point is `agent.md` (always-loaded by the host LLM). Full methodology in `CRYSTALIUM.md`. Architectural decisions traced in `DESIGN-RATIONALE.md`. The decision-ready spec lives at `.spectra/crystalium-v0.1.0-spec.md` and is the contract APIVR-Δ implements wave-by-wave.

## Container-first (mandatory)

This project's toolchain (uv, pytest, mcp SDK, lancedb, kuzu, sentence-transformers) lives entirely inside the docker compose service `crystalium`. Run any dev command via `docker compose run --rm crystalium <cmd>` or the equivalent `make` target.

**The host runs only `docker compose`, `git`, and `make`.** Do not invoke `python`, `pip`, `uv`, or `pytest` on the host.

Examples:

```bash
# Bad — will fail
python -m pytest mcp-server/tests/

# Good
make test
# or
docker compose run --rm crystalium pytest mcp-server/tests/ -v
```

## CRYSTALIUM is infrastructure, not an agent

Do not invoke CRYSTALIUM tools expecting LLM-style reasoning. It stores, gates, retrieves, consolidates, and forgets. Reasoning lives in the consuming agent (the Eidolon that calls `crystalium.recall`, `crystalium.commit`, etc.).

The MCP tools are the public surface:

- `recall(scope, query, k, layers)` — retrieve hybrid (BM25+vector+graph)
- `commit(layer, payload, provenance)` — write with tier gating
- `update(id, patch, reason)` — edit with bi-temporal tracking
- `skill_invoke(name, args)` — run verifier in sandbox
- `plan_checkpoint(state)` / `plan_replan(diff)` — Execution layer ops
- `session_end()` — enqueue Dream immediately

Every result carries an ECL v2.0 envelope sidecar with SHA-256 integrity.

## Spec and methodology

- **Specification:** `.spectra/crystalium-v0.1.0-spec.md` (70 sections; 8 gates, 6 waves, canary suite)
- **Methodology:** `CRYSTALIUM.md` (research anchors, four-layer model, Dream consolidation, trust propagation)
- **Design rationale:** `DESIGN-RATIONALE.md` (D1–D10 decisions, [UNVERIFIED] markers for unverifiable claims)
- **Agent profile:** `agent.md` (≤1000 tokens, entry point)

The spec is frozen for v0.1 and shipped to the install target as `./.eidolons/crystalium/SPEC.md`.

## EIIS v1.4 conformance

- Source repo has 6 required files: `agent.md`, `AGENTS.md`, `CLAUDE.md`, `README.md`, `install.sh`, `EIIS_VERSION = 1.4`.
- ECL v2.0 declared: source includes `ECL_VERSION = 2.0` (triggers EIIS §3.7.1 verbatim-copy to install target).
- Install target whitelisted: `./.eidolons/crystalium/` contains only `agent.md`, `SPEC.md`, `install.manifest.json`, `ECL_VERSION`, optional `skills/*.md` and `schemas/*.json`.
- Idempotent `install.sh`: second run produces identical output (CI enforces).

## Quality gates (all must pass before commit)

- **G1–G8:** All 8 conformance gates passing in pytest (test_enforcement.py, test_skill_invoke.py, test_composer.py, test_dream_scheduler.py, test_ecl_envelope.py, test_trust_propagation.py, test_promotion_gate.py).
- **agent.md ≤1000 tokens:** CI verifies token count via tiktoken.
- **Composer ≤3500 tokens:** G6 invariant enforced.
- **Canary suite ≥0.80 A/B pass rate:** memory-on beats memory-off on ≥80% of 10 missions.
- **install.sh idempotent:** CI "second-run-no-diff" job.
- **DESIGN-RATIONALE.md citations:** ≥10 citations from anchor list (MISSION.md §Quality bars); [UNVERIFIED] markers on unverifiable claims.

## Development workflow

```bash
# Build image
docker compose build

# Run tests (all gates)
make test

# Run single test
make test-file F=mcp-server/tests/test_enforcement.py

# Lint
make lint

# Validate schemas
make schema

# Commit (Conventional Commits)
git commit -m "feat(enforcement): add assert_tier_allowed with tier matrix"

# Work on the spec branch (no push, no PR for v0.1)
git checkout feat/crystalium-v0.1.0
```

See `AGENTS.md` for the full developer standard.

<!-- eidolon:dispatch-pointer start -->
## Eidolons

This project uses [Eidolons](https://github.com/Rynaro/eidolons). The canonical agent dispatch table, methodology references, and per-Eidolon hand-off contracts live at [`./EIDOLONS.md`](./EIDOLONS.md). Read that file first before responding to any prompt that mentions an Eidolon or matches a TRANCE complexity signal.
<!-- eidolon:dispatch-pointer end -->
