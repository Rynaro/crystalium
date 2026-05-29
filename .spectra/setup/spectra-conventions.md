## 🗺️ CONVENTION MAP

**Project:** CRYSTALIUM — portable memory harness for the Eidolons (Python, MCP, container-first; NOT an agent itself)
**Generated:** 2026-05-29
**Sources:** `CRYSTALIUM.md`, `MISSION.md`, `README.md`, `AGENTS.md`, `CLAUDE.md`, `CHANGELOG.md`, root + `mcp-server/pyproject.toml`, `Makefile`, `Dockerfile`, `docker-compose.yml`, `EIIS_VERSION`, `ECL_VERSION`, the implemented `mcp-server/src/crystalium/**` + `mcp-server/tests/**` + `schemas/*.json` + `evals/**` tree, and `.spectra/crystalium-v0.1.0-spec.{md,yaml}`.

> When this file is loaded, its vocabulary supersedes SPECTRA's generic placeholders ("FlowObject",
> "Repository", "Service") in every downstream phase. It maps **implemented** module names — where the
> prior spec planned different filenames, the implemented name wins (see Sources notes).

---

### Quick Reference (Top 5 Conventions)

1. **Container-first is absolute (P0-13).** All Python toolchain runs INSIDE the docker compose
   service `crystalium`. The host runs only `docker`, `git`, `make`. Every command is
   `docker compose run --rm crystalium <cmd>` or a `make` target. NEVER plan a host-side `python`,
   `pip`, `uv`, or `pytest` step. (`AGENTS.md:7-15`, `CLAUDE.md:5-21`)
2. **One chokepoint — `enforcement.py`.** Every write/promote/skill-invoke funnels through the
   `Enforcement` class (frozen `_MATRIX` tier×layer×op + `assert_*` guards + telemetry `record()`)
   before any storage code. No distributed guards. Modelled on atlas-aci. (`enforcement.py`,
   `CRYSTALIUM.md:27-39`)
3. **Four-layer crystal lattice.** Memory = `layers/{episodic, semantic, procedural, execution}.py`,
   separated by age (Episodic←recent, Semantic←consolidated) and trust. Each has its own gate.
   A memory record is a **Crystal**. (`CRYSTALIUM.md:14-24`, `schemas.py`)
4. **Bi-temporal, never hard-delete (P0-5).** Updates invalidate-old (`t_valid_to=now`,
   `superseded_by`) and write-new. Trust tiers propagate via **MIN** (P0-6) — consolidation can never
   launder T3 into Semantic. (`server.py:626-717`, spec.yaml P0-5/6)
5. **Quality = G1–G8 gates + 17 P0 non-negotiables, proven by pytest in-container.** All 8 gates and
   the canary memory-on/off A/B (≥0.80) must pass before commit. `agent.md ≤1000 tokens`; composer
   ≤3500 tokens. (`CLAUDE.md:54-61`, spec.yaml `gates:`/`non_negotiables:`)

---

### Convention Mapping (SPECTRA → Project)

| SPECTRA Concept | This Project | Path Pattern | Exemplar |
|----------------|-------------|--------------|----------|
| Domain entity ("FlowObject") | **Crystal** (payload+scope+provenance+temporal) / **Skill** (procedural) | `schemas/<name>.v1.json` + Pydantic mirror | `schemas/crystal.v1.json`, `mcp-server/src/crystalium/schemas.py` |
| Service / Business Logic | **Layer adapter** (the four memory tiers) | `mcp-server/src/crystalium/layers/*.py` | `layers/semantic.py`, `layers/procedural.py` |
| Data Access ("Repository") | **Storage adapter** (role-named, not engine-named) | `mcp-server/src/crystalium/storage/*.py` | `storage/relational.py` (SQLite+FTS5), `storage/vector.py` (LanceDB), `storage/graph.py` (KuzuDB), `storage/blob.py` (SHA-256 blob) |
| Validation / guard | **Enforcement chokepoint** (`assert_*` + `_MATRIX`) | `mcp-server/src/crystalium/enforcement.py` | `Enforcement.assert_tier_allowed`, `assert_no_path_escape`, `assert_rate_limit` |
| Search / Query service | **Aetheryte** (hybrid BM25⊕vector⊕graph + RRF rerank) | `mcp-server/src/crystalium/aetheryte/*.py` | `aetheryte/retrieve.py`, `aetheryte/redact.py` |
| Working-set assembly | **Composer** (6 typed slots, ≤3500 tok, importance eviction, G6) | `mcp-server/src/crystalium/composer.py` | `composer.py` |
| Background job | **Dream** (async consolidation: orient→gather→consolidate→prune; PROPOSE-only) | `mcp-server/src/crystalium/dream/*.py` | `dream/scheduler.py`, `dream/worker.py` |
| API Endpoint / handler | **MCP tool** (`crystalium.*`, stdio JSON-RPC 2.0) | `server.py` `build_tool_manifest()` + `_handle_*` | `crystalium.recall`, `crystalium.commit`, `crystalium.skill_invoke` |
| Controller / orchestration | **Server runner** | `mcp-server/src/crystalium/server.py` | `run_stdio()` |
| Cross-cutting (auth/trust) | **Trust tiers** (`Tier` enum + `LAYER_CEILING`; lower value = higher trust) | `mcp-server/src/crystalium/trust.py` | `Tier.T0..T3`, `LAYER_CEILING` |
| Config | **Config dataclass** (env-var + `crystalium.yaml`) | `mcp-server/src/crystalium/config.py` | `Config` |
| Wire/handoff format | **ECL v2.0 envelope sidecar** (11 fields, SHA-256) | `mcp-server/src/crystalium/ecl.py` | `build_for_tool_result`, `emit_sidecar` |
| Test File | `mcp-server/tests/test_<module>.py` (pytest, in-container) | `mcp-server/tests/test_*.py` | `test_enforcement.py`, `test_composer.py`, `test_ecl_envelope.py` |
| Eval / canary | **Eval harness** (10 missions, memory-on/off A/B ≥0.80) | `evals/*.py` | `evals/ab_memory_onoff.py`, `evals/missions.py` |

---

### Action Verb Mapping

| SPECTRA Verb | In This Project | Example |
|-------------|----------------|---------|
| Create | New module under `mcp-server/src/crystalium/<area>/`, JSON Schema in `schemas/`, Pydantic mirror in `schemas.py`, paired test in `mcp-server/tests/`; type hints mandatory, ruff/mypy clean | "Create `layers/semantic.py` + `schemas/semantic-record.v1.json` + `test_semantic.py`" |
| Extend | Add to the existing chokepoint/layer/tool without new modules; e.g. add a row to `_MATRIX`, a slot to the composer, a tool to `build_tool_manifest()` | "Extend `enforcement._MATRIX` with the new (layer, op) row + cover in `test_enforcement.py`" |
| Modify | Edit existing logic preserving the chokepoint path + bi-temporal/MIN-tier invariants; re-prove the affected G-gate | "Modify `server._handle_update` keeping invalidate-old/write-new ordering (P0-5)" |
| Test | Always in-container pytest: `make test` (full) / `make test-fast` (skip slow) / `make test-file F=mcp-server/tests/test_X.py [P="pattern"]`; lint `make lint`; types `make typecheck`; schemas `make schema` | "Test: `make test-file F=mcp-server/tests/test_composer.py P=test_g6`" |
| Configure | Add a default to the `Config` dataclass (`config.py`) traced to a FORGE decision (D1–D9) or P0 anchor; expose env override + `crystalium.yaml` key | "Configure `idle_threshold_s` (D3) with `CRYSTALIUM_IDLE_THRESHOLD_S` override" |

Commit scope vocabulary (`AGENTS.md:215-229`): `schemas, enforcement, storage, layers, aetheryte,
dream, composer, server, ecl, test, install, ci, docs`. Use Conventional Commits
(`feat(enforcement): ...`).

---

### Validation Gates (Default)

- **P0 (blocks release):** The relevant **G-gate** for the touched area passes AND its container test
  passes — `docker compose run --rm crystalium pytest <anchor>`. Plus any P0 invariant the change
  touches (T3-quarantine, bi-temporal never-delete, MIN-tier, PROPOSE-only Dream, ≤3500 composer,
  container-first). Gate→area: G1/G2/G4 → `enforcement`; G3 → `skill_invoke`/`procedural`; G5 →
  `gate.py`/promotion; G6 → `composer`; G7 → `ecl`; G8 → `dream`.
- **P1 (degrades experience):** Full suite green (`make test`), ruff clean (`make lint`), mypy clean
  (`make typecheck`), schemas valid (`make schema`); canary A/B ≥ 0.80 for memory-affecting changes.
- **P2 (cosmetic):** `ruff format` clean, Google-style docstrings on public functions (summarize the
  chokepoint path for complex functions), `agent.md` token count still ≤1000.

---

### Architectural Boundaries

Every CRYSTALIUM spec MUST respect these (from the frozen MISSION P0 set + DESIGN decisions):

1. **Container-first (P0-13).** No host-side `python`/`pip`/`uv`/`pytest`/model-downloads — ever. Plan
   all execution as `docker compose run --rm crystalium ...` or `make` targets.
2. **One chokepoint (P0-7).** All mutation flows through `enforcement.py`. Do not add storage writes
   that bypass `assert_*` + `record()`. New write paths add to `_MATRIX`, they don't side-step it.
3. **T3 quarantine ceiling (P0-1/2).** Environment/tool (T3) input writes ONLY Episodic-quarantined.
   Never Semantic/Procedural directly.
4. **Bi-temporal, never hard-delete (P0-5).** Updates invalidate-old + write-new; deletes are
   `t_valid_to`/`superseded_by` marks.
5. **MIN-tier propagation (P0-6).** Consolidated crystals take the minimum trust tier of inputs;
   summarization cannot launder T3 into Semantic (G4).
6. **Promotion is gated (P0-3/4).** Procedural admission requires verifier-pass in sandbox (G3);
   Semantic promotion requires ≥k corroborations OR human-confirm (G5). Dream only PROPOSES (P0-10).
7. **Index → pointer → content (P0-8).** Indices/metadata in SQLite/LanceDB/KuzuDB; payloads in the
   content-addressed blob tier under `~/.crystalium/<project>/`.
8. **Bounded working set (P0-9/14).** Composer ≤3500 tokens across 6 typed slots, deterministic
   importance eviction; `agent.md` ≤1000 tokens.
9. **Scope + redaction (P0-12).** Every crystal carries `scope=(project, agent_class_visibility,
   sensitivity_tag)`; redactor runs between retrieval and the LLM and re-runs at every cross-agent
   handoff.
10. **Single importance function (P0-11).** `importance_score(access_frequency, recency,
    outcome_success, novelty)` — FROZEN signature — drives both write-gate and forget-weight.
11. **Conformance:** EIIS **v1.4** install contract + ECL **v2.0** envelope on every tool result
    (11 fields, SHA-256). `install.sh` idempotent (P0-16).
12. **CRYSTALIUM is infrastructure, not an agent.** No reasoning loop, no planning, no goal-seeking —
    do not spec it to "decide" or "infer"; it stores, gates, retrieves, consolidates, forgets.

---

### Sources

| Convention | Source | Confidence |
|-----------|--------|------------|
| Container-first (host = docker/git/make only) | `AGENTS.md:7-15`, `CLAUDE.md:5-21`, `CRYSTALIUM.md:129`, spec.yaml P0-13 | HIGH |
| Python ≥3.11, uv, ruff, mypy, pytest | `mcp-server/pyproject.toml:11,17-41,57-75`, `Dockerfile:7,13-18` | HIGH |
| One chokepoint `enforcement.py` + `_MATRIX` + `assert_*` | `mcp-server/src/crystalium/enforcement.py:239-340,351-609` | HIGH |
| Four layers (episodic/semantic/procedural/execution) | `mcp-server/src/crystalium/layers/*.py`, `CRYSTALIUM.md:14-24` | HIGH |
| Trust tiers + LAYER_CEILING (lower = higher trust) | `trust.py`, `enforcement.py:43,438`, `server.py:63-89` | HIGH |
| Storage adapters role-named (relational/vector/graph/blob) | `mcp-server/src/crystalium/storage/*.py`, `server.py:51-54` | HIGH |
| Aetheryte hybrid recall + redactor | `aetheryte/retrieve.py`, `aetheryte/redact.py`, `README.md:33-36,105`, `test_rrf.py` | HIGH |
| Composer 6 slots ≤3500 (G6) | `composer.py`, `CRYSTALIUM.md:59-67`, `README.md:128` | HIGH |
| Dream async PROPOSE-only | `dream/scheduler.py`, `dream/worker.py`, `CRYSTALIUM.md:76-95` | HIGH |
| 7 MCP tools + ECL v2.0 sidecar (G7) | `server.py:96-257,388-410`, `ecl.py`, `README.md:101-113` | HIGH |
| G1–G8 gates + test anchors | `.spectra/crystalium-v0.1.0-spec.yaml:87-163` | HIGH |
| 17 P0 non-negotiables | `.spectra/crystalium-v0.1.0-spec.yaml:31-82`, `MISSION.md` | HIGH |
| EIIS v1.4 / ECL v2.0 | `EIIS_VERSION`, `ECL_VERSION`, `CLAUDE.md:47-52` | HIGH |
| Commit scopes + Conventional Commits | `AGENTS.md:195-252` | HIGH |
| Implemented filenames are CANONICAL (relational/vector/graph not sqlite/lance/kuzu; retrieve not recall; ecl not ecl_envelope; top-level `evals/` not `tests/canary/`) | implemented tree glob; all docs reconciled (`AGENTS.md`, `spec.{md,yaml}`, `CLAUDE.md`, `CHANGELOG.md`) | HIGH (resolved — use implemented names, never the planned ones) |
| Gate test-anchors use implemented names (G7 → `test_ecl_envelope.py`) | `spec.yaml:153`, `mcp-server/tests/test_ecl_envelope.py` | HIGH (resolved) |
| `install.sh`, `.github/workflows/{ci,conformance,release}.yml`, `schemas/commit-result.v1.json` all EXIST; only `crystalium.yaml` (runtime config loader) is absent/deferred | repo tree | HIGH (corrected — earlier "absent" flags were wrong) |
