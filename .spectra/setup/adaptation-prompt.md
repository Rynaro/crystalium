# SPECTRA Adaptation Prompt — CRYSTALIUM

**Purpose:** A self-contained, re-runnable prompt that regenerates or refreshes
`.spectra/setup/spectra-conventions.md` from the current state of the CRYSTALIUM codebase. Run this
whenever the repo evolves (new layer, new tool, post-W6 install/CI landing, the v0.1 → v0.2 cut, or
when planned↔implemented filenames are reconciled).

**Mode:** READ-ONLY on the whole repo. Write ONLY `.spectra/setup/spectra-conventions.md`. Never
touch `.atlas/`, `.mcp.json`, `eidolons.*`, git config, or author email.

---

## Role

You are SPECTRA running its project-fit pass. CRYSTALIUM is a **portable memory harness for the
Eidolons** (Python, MCP, container-first) — it is NOT itself an agent. Your job is to map SPECTRA's
generic planning placeholders onto CRYSTALIUM's real vocabulary so future planning sessions speak the
project's language. You produce a vocabulary map, never code, never a plan.

---

## Inputs to read (in priority order)

1. **Source-of-truth docs:** `CRYSTALIUM.md` (methodology, four-layer model, Dream, trust),
   `MISSION.md` (frozen P0 set), `DESIGN-RATIONALE.md` (D1–D10), `README.md`, `AGENTS.md`
   (dev standard, commit scopes, project structure), `CLAUDE.md`, `CHANGELOG.md`.
2. **Build / config:** root `pyproject.toml`, `mcp-server/pyproject.toml`, `Makefile`, `Dockerfile`,
   `docker-compose.yml`, `docker-compose.dev.yml`, `EIIS_VERSION`, `ECL_VERSION`,
   `crystalium.yaml` (if present).
3. **Actual source tree:** glob `mcp-server/src/crystalium/**/*.py`, `mcp-server/tests/**/*.py`,
   `schemas/*.json`, `evals/**/*.py`. **The implemented tree is authoritative** when it conflicts
   with planned filenames in docs/specs.
4. **Prior SPECTRA output:** `.spectra/crystalium-v0.1.0-spec.md` + `.yaml` (mine for gate/wave/P0
   vocabulary), and the existing `.spectra/setup/project-profile.md`.

---

## Detection heuristics

- **Language/manager:** confirm Python `requires-python` and that the package manager is **uv**
  (running INSIDE Docker). Detect ruff (lint+format), mypy (typecheck), pytest (+asyncio_mode auto).
- **Container-first:** the host runs only `docker`/`git`/`make`. Every dev command is
  `docker compose run --rm crystalium <cmd>` or a `make` target. This is P0-13 — treat it as the
  primary architectural boundary; NEVER suggest host-side `python`/`pip`/`uv`/`pytest`.
- **Four-layer lattice:** find `layers/episodic.py`, `semantic.py`, `procedural.py`, `execution.py`.
  Confirm each layer's gate (Episodic=quarantine-by-default, Semantic=promotion-gated,
  Procedural=verifier-gated, Execution=TTL/ephemeral).
- **The chokepoint:** `enforcement.py` `Enforcement` class is THE single mutation sink. Locate the
  frozen `_MATRIX` (tier × layer × op), the `assert_*` guards, the exception hierarchy
  (`CrystaliumEnforcementError` + `reason_code`), and the `record()` telemetry call.
- **Trust tiers:** `trust.py` `Tier` enum (T0 operator > T1 verified > T2 unverified/default > T3
  environment) + `LAYER_CEILING`. Remember: **lower enum value = higher trust**.
- **Storage adapters:** map role → engine via the **actual** module names:
  `storage/relational.py`=SQLite+FTS5(BM25), `storage/vector.py`=LanceDB,
  `storage/graph.py`=KuzuDB, `storage/blob.py`=content-addressed SHA-256 filesystem.
- **Recall surface:** `aetheryte/retrieve.py` (the **Aetheryte**, hybrid BM25⊕vector⊕graph + RRF
  rerank) and `aetheryte/redact.py` (regex + small-LLM judge).
- **Composer:** `composer.py` — 6 typed slots, ≤3500-token hard cap (G6), deterministic importance
  eviction. Pull the slot caps from `CRYSTALIUM.md` / `config.py`.
- **Dream:** `dream/scheduler.py` + `dream/worker.py` — async, off hot path; orient→gather→
  consolidate→prune; PROPOSE-only.
- **MCP surface:** `server.py` `build_tool_manifest()` lists the 7 `crystalium.*` tools. Each result
  emits an **ECL v2.0** sidecar via `ecl.py`.
- **Gates G1–G8:** read `.spectra/crystalium-v0.1.0-spec.yaml` `gates:` for the canonical
  name/derivation/`test_anchor`. The **implemented tree is always authoritative** — if a
  `test_anchor` ever disagrees with the real test file, the real file wins and the doc is the
  drift to fix (as of the v0.1.0 reconciliation, spec.yaml + AGENTS.md already match the impl;
  G7 → `test_ecl_envelope.py`). Record the real path.
- **Conformance versions:** read `EIIS_VERSION` and `ECL_VERSION` verbatim.

---

## SPECTRA placeholder → CRYSTALIUM concept mapping (the core deliverable)

| SPECTRA generic | CRYSTALIUM real concept | Where |
|---|---|---|
| "FlowObject" / domain entity | **Crystal** (a memory record: payload + scope + provenance + temporal) | `schemas.py` (`Crystal`), `schemas/crystal.v1.json` |
| "Repository" / Data Access | **Storage adapter** (role-named) | `storage/relational.py`, `vector.py`, `graph.py`, `blob.py` |
| "Service" / Business Logic | **Layer adapter** (the four memory tiers) | `layers/{episodic,semantic,procedural,execution}.py` |
| "Validation" / guard | **Enforcement chokepoint** (`assert_*` guards + `_MATRIX`) | `enforcement.py` |
| "API Endpoint" / handler | **MCP tool** (`crystalium.*`, stdio JSON-RPC) | `server.py` `build_tool_manifest()` + `_handle_*` |
| "Controller" / orchestration | **Server runner** (`run_stdio`) | `server.py:run_stdio` |
| "Background job" | **Dream worker/scheduler** (async consolidation) | `dream/worker.py`, `dream/scheduler.py` |
| "Search/Query service" | **Aetheryte** (hybrid recall) | `aetheryte/retrieve.py` |
| "Config" | **Config dataclass** (env + `crystalium.yaml`) | `config.py` |
| "Test file" | `mcp-server/tests/test_<module>.py` (pytest, in-container) | `mcp-server/tests/` |
| "Acceptance/quality gate" | **G1–G8** validation gates + the 17 **P0** non-negotiables | spec.yaml `gates:`, `non_negotiables:` |
| "Deploy/release" | **Container build + EIIS install target** (v0.2 roster publish) | `Dockerfile`, `install.sh`, `AGENTS.md` §release |

---

## Output contract for `spectra-conventions.md`

Follow `.eidolons/spectra/templates/catalog.md` §"Convention Map" exactly:

1. Header: `**Project:** CRYSTALIUM`, `**Generated:** <today>`, `**Sources:** <files read>`.
2. **Quick Reference (Top 5 Conventions)** — the 5 highest-leverage rules a planner must internalize
   first (container-first is #1).
3. **Convention Mapping (SPECTRA → Project)** table — use the mapping above, with real path patterns
   + a concrete exemplar file for each row.
4. **Action Verb Mapping** (Create / Extend / Modify / Test) — phrased for an in-container Python+uv
   workflow (e.g. Test = `make test` / `docker compose run --rm crystalium pytest ...`).
5. **Validation Gates (Default)** — P0 = the relevant G-gate(s) + container test pass; P1 = full
   suite + ruff + mypy; P2 = ruff format / docstrings.
6. **Architectural Boundaries** — the P0-derived hard constraints every spec MUST respect
   (container-first, one chokepoint, bi-temporal never-delete, MIN-tier propagation, PROPOSE-only
   Dream, ≤3500 composer, scope+redaction, agent.md ≤1000 tokens).
7. **Sources** table — every convention traced to file+line or inference method, with HIGH/MEDIUM/LOW
   confidence.

---

## Guardrails

- **Evidence only.** Every asserted convention must trace to a real file. No invention. If a
  referenced artefact (e.g. `install.sh`, `.github/workflows/`, `crystalium.yaml`,
  `commit-result.v1.json`) is absent from the tree, mark it pending — do not assert it as present.
- **Implemented > planned.** When docs/spec name a file that the tree renamed, use the tree's name and
  note the divergence.
- **Commit-scope vocabulary** comes from `AGENTS.md` §Scopes: `schemas, enforcement, storage, layers,
  aetheryte, dream, composer, server, ecl, test, install, ci, docs`. Use these in action verbs/examples.
- **Keep it current toward 1.0.** On each re-run, reconcile any planned↔implemented drift, add new
  layers/tools/gates, and re-read `EIIS_VERSION`/`ECL_VERSION` (they will bump). Re-confirm the gate→
  test-anchor mapping against the live test tree.
- **Output discipline (P0):** write ONLY `.spectra/setup/spectra-conventions.md`. Never duplicate it
  into `.claude/`, `.cursor/`, `docs/`, or any vendor folder.
