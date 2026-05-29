# CRYSTALIUM — Project Profile (SPECTRA stack/convention detection)

**Generated:** 2026-05-29
**Detection method:** Read-only inspection of source-of-truth docs, build config, and the
actual implemented source tree.
**Repo:** `Rynaro/crystalium` — portable memory harness for the Eidolons (NOT an agent itself).

> This is a factual inventory. Every claim cites a file path. Where the prior spec's planned
> filenames diverge from what was actually implemented, the **implemented** name is authoritative
> and the divergence is flagged in "Known ambiguities."

---

## 1. Identity & status

| Field | Value | Source |
|---|---|---|
| Project | CRYSTALIUM | `CRYSTALIUM.md:1`, `pyproject.toml` (root) |
| Version | `0.1.0` | `mcp-server/pyproject.toml:8`, `CHANGELOG.md:9` |
| Status | v0.1.0 / Pre-roster / Standalone Eidolon | `README.md:5-7` |
| License | Apache-2.0 | `mcp-server/pyproject.toml:12`, `README.md:195` |
| What it is | Memory substrate: stores, gates, retrieves, consolidates, forgets | `CRYSTALIUM.md:7`, `README.md:3` |
| What it is NOT | Not an agent, not a reasoner, not a vector DB, not a training system | `CRYSTALIUM.md:117-125` |
| Reference impl | `Rynaro/atlas-aci` (`enforcement.py` chokepoint pattern) | `CRYSTALIUM.md:175`, `enforcement.py:24` |

---

## 2. Language & toolchain

| Field | Value | Source |
|---|---|---|
| Language | Python ≥3.11 | `mcp-server/pyproject.toml:11`, `AGENTS.md:175` |
| Container base image | `python:3.12-slim` | `Dockerfile:7` |
| Package/dependency manager | **uv** (lock generated inside container; `uv.lock` gitignored) | `Dockerfile:13-18,32`, `mcp-server/pyproject.toml:2-4` |
| Build backend | hatchling (`packages = ["src/crystalium"]`) | `mcp-server/pyproject.toml:50-55` |
| Linter + formatter | **ruff** (line-length 100, target py311, rules `E,F,W,I,B,UP,SIM,RUF`) | `mcp-server/pyproject.toml:57-63`, root `pyproject.toml:7-13` |
| Type checker | **mypy** (`strict = false`, `ignore_missing_imports = true`) | `mcp-server/pyproject.toml:72-75`, `Makefile:59-60` |
| CLI entry point | `crystalium = "crystalium.__main__:cli"` | `mcp-server/pyproject.toml:43-44` |
| Type hints | Mandatory on all signatures; absolute imports; snake_case fn/var, PascalCase class | `AGENTS.md:175-181` |
| Pydantic | v2 models, `model_config = ConfigDict(extra="forbid")`, never `.json()` | `AGENTS.md:184-189` |

### Runtime dependencies (`mcp-server/pyproject.toml:17-31`)
`mcp>=1.2.0`, `pydantic>=2.6`, `lancedb>=0.6`, `kuzu>=0.4`, `sentence-transformers>=2.7`,
`apscheduler>=3.10`, `structlog>=24.1`, `click>=8.1`, `sqlalchemy>=2.0`, `aiosqlite>=0.19`,
`opentelemetry-sdk>=1.20`, `tiktoken>=0.6`, `pyarrow>=14`.

### Dev dependencies (`mcp-server/pyproject.toml:33-41`)
`pytest>=8.0`, `pytest-asyncio>=0.23`, `pytest-cov>=5.0`, `ruff>=0.4`, `mypy>=1.10`,
`jsonschema>=4.21`.

---

## 3. Container-first protocol (NON-NEGOTIABLE — P0-13)

All Python toolchain runs **inside** the docker compose service `crystalium`. The host runs only
`docker`, `git`, and `make`. No host-side `python`, `pip`, `uv`, `pytest`, or model downloads.
Source: `AGENTS.md:7-15`, `CLAUDE.md:5-21`, `CRYSTALIUM.md:129`, spec.yaml P0-13.

| Concern | Detail | Source |
|---|---|---|
| Compose service | `crystalium` | `docker-compose.yml:11-12` |
| Run prefix | `docker compose run --rm crystalium <cmd>` | `Makefile:7-9`, `README.md:79` |
| Image (dev) | `crystalium:dev`, Dockerfile target `dev` | `docker-compose.yml:13-17`, `Dockerfile:48-54` |
| Source mount | `.:/app` (read-write) | `docker-compose.yml:18-19` |
| Data volume | named `crystalium_data` → `/root/.crystalium` | `docker-compose.yml:20-23,34-36` |
| Data dir env | `CRYSTALIUM_DATA_DIR=/root/.crystalium/default` | `docker-compose.yml:25` |
| PYTHONPATH | `/app/mcp-server/src` (compose) / `/app/src` (Dockerfile) | `docker-compose.yml:26`, `Dockerfile:39` |
| Slow-test skip | `CRYSTALIUM_SKIP_SLOW=1` (skips sentence-transformers downloads) | `docker-compose.yml:27`, `Makefile:26-27`, `mcp-server/pyproject.toml:68-69` |
| Transport mode | stdio JSON-RPC 2.0; `stdin_open: true`, `tty: false` | `docker-compose.yml:30-32`, `server.py:418-428` |

---

## 4. Test framework & how tests run

| Field | Value | Source |
|---|---|---|
| Framework | **pytest** + pytest-asyncio (`asyncio_mode = "auto"`) | `mcp-server/pyproject.toml:65-70` |
| Test root | `mcp-server/tests/` (`testpaths = ["tests"]`) | `mcp-server/pyproject.toml:66`, `Makefile:23` |
| Slow marker | `@pytest.mark.slow` (deselect `-m "not slow"`) | `mcp-server/pyproject.toml:67-70` |
| Full suite | `make test` → `docker compose run --rm crystalium pytest mcp-server/tests/ -v` | `Makefile:22-23`, `AGENTS.md:36-38` |
| Fast suite | `make test-fast` (env `CRYSTALIUM_SKIP_SLOW=1`, `-m "not slow"`) | `Makefile:26-27` |
| Single file | `make test-file F=mcp-server/tests/test_enforcement.py` | `AGENTS.md:44-52` |
| Single by pattern | `make test-file F=... P="test_g1"` (pytest `-k`) | `AGENTS.md:56-60` |
| Schema subset | `make test-schemas` / `make schema` | `Makefile:30-31`, `AGENTS.md:88-91` |
| Storage subset | `make test-storage` / `make test-w1` | `Makefile:34-52` |
| Lint | `make lint` → `ruff check mcp-server/src mcp-server/tests` (+ `ruff format --check`) | `Makefile:55-56`, `AGENTS.md:66-82` |
| Typecheck | `make typecheck` → `mypy mcp-server/src/crystalium` | `Makefile:59-60` |

### Actual test files present (`mcp-server/tests/`)
`conftest.py`, `test_enforcement.py`, `test_trust_propagation.py`, `test_promotion_gate.py`,
`test_skill_invoke.py`, `test_composer.py`, `test_dream_scheduler.py`, `test_dream_worker.py`,
`test_ecl_envelope.py`, `test_schemas.py`, `test_config.py`, `test_importance.py`,
`test_bitemporal.py`, `test_redaction.py`, `test_aetheryte.py`, `test_rrf.py`, `test_server.py`,
`test_cli.py`, `test_storage_blob.py`, `test_storage_relational.py`, `test_storage_vector.py`,
`test_storage_graph.py`.

> Note: storage tests are named by **role** (`relational`/`vector`/`graph`/`blob`), matching the
> implemented adapters — not by the engine names (`sqlite`/`lance`/`kuzu`) the spec.yaml planned.
> The ECL test is `test_ecl_envelope.py` (impl), not `test_ecl_conformance.py` (planned).

### Eval / canary harness (`evals/`)
`evals/missions.py`, `evals/ab_memory_onoff.py`, `evals/poisoning_resistance.py`,
`evals/selective_forgetting.py`, `evals/__init__.py`. The headline metric is the **memory-on/off
A/B** across 10 canary missions (pass rate ≥ 0.80). Source: `README.md:221`, `CHANGELOG.md:34-39`,
spec.yaml P0-17 + W5.

> Note: canary missions live under `evals/` in the implemented tree, not
> `mcp-server/tests/canary/test_canary_*.py` as the spec.yaml W5 planned.

---

## 5. Validation gates (G1–G8) — the project's P0 quality contract

All 8 gates must pass in pytest before commit (`AGENTS.md:40`, `CLAUDE.md:54-61`). Each gate has a
canonical `test_anchor`. Source: `.spectra/crystalium-v0.1.0-spec.yaml:87-163`.

| Gate | Name | Derived from | Test anchor (per spec.yaml) |
|---|---|---|---|
| G1 | T3 cannot commit above Episodic | D1 | `test_enforcement.py::test_g1_t3_cannot_commit_above_episodic` |
| G2 | T2 procedural commits land as candidate | D1 | `test_enforcement.py::test_g2_t2_procedural_candidate_only` |
| G3 | Procedural verifier-gated admission (sandbox) | D5 | `test_skill_invoke.py::test_g3_skill_invoke_sandbox_contract` |
| G4 | Trust-tier propagation blocks T3 laundering | D7 | `test_trust_propagation.py::test_g4_min_tier_blocks_semantic_laundering` |
| G5 | Human-confirm default window (promotion gate) | D8 | `test_promotion_gate.py::test_g5_human_confirm_default_window` |
| G6 | Working-set budget invariant (≤3500 tokens) | D9 | `test_composer.py::test_g6_working_set_budget_invariant` |
| G7 | Every tool result emits valid ECL envelope | D4 | `test_ecl_conformance.py::test_g7_every_tool_result_emits_valid_envelope` |
| G8 | Dream dedup on concurrent triggers | D3 | `test_dream_scheduler.py::test_g8_dream_dedup_on_concurrent_triggers` |

Additional CI gates: `agent.md ≤1000 tokens` (tiktoken), `install.sh idempotent` (second-run-no-diff),
DESIGN-RATIONALE ≥10 citations. Source: `CLAUDE.md:57-61`.

---

## 6. P0 non-negotiables (P0-1 … P0-17)

The frozen MISSION P0 set. Every SPECTRA spec on this repo MUST respect these.
Source: `.spectra/crystalium-v0.1.0-spec.yaml:31-82`, `MISSION.md`.

- **P0-1/2:** Capture is ungated into Episodic, always `validation_state=quarantined`; T3 content writes ONLY Episodic-quarantined.
- **P0-3:** Procedural admission requires the skill's verifier to pass in a sandbox.
- **P0-4:** Semantic promotion requires ≥k corroborations OR human confirm.
- **P0-5:** Updates are **bi-temporal** — invalidate-old (`t_valid_to=now`, `superseded_by`), write-new. **Never hard-delete.**
- **P0-6:** Trust tier propagates through reads and summarization; consolidated crystal takes **MIN** trust tier.
- **P0-7:** Path-traversal guard + per-process rate limit + telemetry on every call (atlas-aci pattern).
- **P0-8:** Index → pointer → content (cheap content-addressed blob tier holds payloads).
- **P0-9:** Bounded slotted working set ≤3500 tokens, hard caps + deterministic eviction.
- **P0-10:** Dream is async, off hot path; may only PROPOSE Semantic upserts.
- **P0-11:** One importance function `f(access_frequency, recency, outcome_success, novelty)` for both write-gate and forget-weight.
- **P0-12:** Every crystal carries `scope=(project, agent_class_visibility, sensitivity_tag)`; redactor between retrieval and LLM.
- **P0-13:** Container-first (see §3).
- **P0-14:** `agent.md` ≤1000 tokens; composer-proven ≤3500 tokens.
- **P0-15:** Every P0 invariant has a passing test in `test_enforcement.py` (or equivalent).
- **P0-16:** `install.sh` idempotent.
- **P0-17:** Memory-on/off A/B is the headline metric.

---

## 7. The four-layer crystal lattice + the chokepoint

CRYSTALIUM separates memory by **age** (Episodic←recent, Semantic←consolidated) and **trust**
(Episodic/Semantic are facts; Procedural is executable). Source: `CRYSTALIUM.md:14-24`,
`AGENTS.md:135-145`.

| Layer | Module | Indexing | Gate | Persistence | Source |
|---|---|---|---|---|---|
| **Episodic** | `layers/episodic.py` | pointer-indexed | quarantine-by-default (T3) | forgotten by importance | `CRYSTALIUM.md:17` |
| **Semantic** | `layers/semantic.py` | vector+graph | promotion-gated (≥k corrob OR human) | indefinite, bi-temporal | `CRYSTALIUM.md:19` |
| **Procedural** | `layers/procedural.py` | bytecode/skill | verifier-gated (sandbox subprocess) | runnable once admitted | `CRYSTALIUM.md:21` |
| **Execution** | `layers/execution.py` | transient | TTL-bound | NOT persisted across sessions | `CRYSTALIUM.md:23` |

### Trust tiers (`mcp-server/src/crystalium/trust.py`)
`Tier.T0` (operator) > `T1` (verified agent) > `T2` (unverified, default caller) > `T3`
(environment/tool). **Lower enum value = higher trust.** `LAYER_CEILING` maps layer→max admission
tier. Source: `enforcement.py:43,114-131,438`, `server.py:63-67,82-89`.

### The keystone — `enforcement.py` (one chokepoint)
Every write/promote/skill-invoke funnels through the `Enforcement` class before any store code.
Three pre-checks + telemetry. Modelled on `atlas-aci/.../enforcement.py`. Source: `CRYSTALIUM.md:27-39`,
`enforcement.py:351-609`.

| Guard | Method | Raises |
|---|---|---|
| Tier × Layer × Op matrix | `assert_tier_allowed(tool, layer, tier, op)` | `TierViolation` |
| MIN-tier ceiling (D7) | `assert_tier_within_layer_ceiling(consolidated_tier, layer)` | `TierCeilingViolation` |
| Path-traversal | `assert_no_path_escape(target, root)` | `PathEscape` |
| Rate limit (200/min, 60s window) | `assert_rate_limit()` | `RateLimitExceeded` |
| Output cap | `cap_output(payload, max_bytes)` → `(bytes, overflow)` | `OutputCapExceeded` |
| Telemetry | `record(...)` (call in `finally`) → `telemetry.record_call` | — |

The frozen `_MATRIX` dict (`enforcement.py:239-340`) encodes the full 12-row tier×layer×op table.
Exception base: `CrystaliumEnforcementError` (carries `reason_code` + `advice`).

---

## 8. Retrieval / storage tech

| Concern | Engine | Module | Source |
|---|---|---|---|
| Sparse / BM25 / FTS | SQLite + FTS5 (+ SQLAlchemy + aiosqlite) | `storage/relational.py` | `CRYSTALIUM.md:45`, `CHANGELOG.md:18-19` |
| Dense / vector | LanceDB (+ sentence-transformers, pyarrow) | `storage/vector.py` | `CRYSTALIUM.md:45`, `pyproject.toml:21,24` |
| Graph / structured facts | KuzuDB | `storage/graph.py` | `CRYSTALIUM.md:45`, `pyproject.toml:22` |
| Episodic payloads | Content-addressed (SHA-256) filesystem blob tier | `storage/blob.py` | `CRYSTALIUM.md:45-49`, `README.md:43-45` |
| Hybrid recall surface | **Aetheryte** — BM25 ⊕ vector ⊕ graph + RRF rerank (when k>20) | `aetheryte/retrieve.py` | `README.md:33-36,105`, `CHANGELOG.md:20-21`, `test_rrf.py` |
| Redaction | regex pre-filter + small-LLM judge (Ollama optional) | `aetheryte/redact.py` | `CRYSTALIUM.md:100-105` |
| Working-set assembly | **Composer** — 6 typed slots, ≤3500 token cap, importance eviction | `composer.py` | `CRYSTALIUM.md:56-69`, `README.md:88` |
| Consolidation | **Dream** — orient→gather→consolidate→prune, async (apscheduler) | `dream/scheduler.py`, `dream/worker.py` | `CRYSTALIUM.md:76-95` |
| Importance fn | `importance_score(access_frequency, recency, outcome_success, novelty)` FROZEN sig | `importance.py` | `CRYSTALIUM.md:69-71`, spec.yaml D6 |
| Telemetry | structlog JSONL + OpenTelemetry | `telemetry.py` | `CRYSTALIUM.md:37`, `README.md:23` |

All data lives under `~/.crystalium/<project>/` (portable; `tar.gz` = full snapshot). Source:
`CRYSTALIUM.md:135`, `README.md:45`.

### Composer slot budget (`CRYSTALIUM.md:59-67`, `README.md:128`)
executive 300 | procedural 600 | semantic 800 | episodic 800 | execution 1000 | buffer 300 |
**total ≤ 3500** (hard limit, G6).

---

## 9. MCP server surface (7 tools)

stdio JSON-RPC 2.0 server (`server.py:418` `run_stdio`; tool manifest `server.py:96-257`). HTTP
raises `NotImplementedError("v0.2")`. Default caller identity is the D4 conservative
`{eidolon: "unknown", version: "n/a", tier: T2}` (`server.py:63-67`).

| Tool (`crystalium.*`) | Purpose | Gated by | Source |
|---|---|---|---|
| `recall(scope, query, k, layers)` | Hybrid BM25+vector+graph retrieval, redacted, slot-budgeted | rate limit only | `server.py:102-131`, `README.md:105` |
| `commit(layer, payload, provenance)` | Write with tier enforcement + bi-temporal | tier matrix (G1–G4) | `server.py:132-159` |
| `update(id, patch, reason)` | Bi-temporal edit; invalidate-old, never hard-delete | tier matrix (G1, G4) | `server.py:160-179`, `server.py:626-717` |
| `skill_invoke(skill_id, args, timeout_s≤30, output_cap_bytes≤8192, workdir)` | Sandbox verifier subprocess | sandbox contract (G3) | `server.py:180-202`, `server.py:720-770` |
| `plan_checkpoint(state)` | Execution-layer checkpoint (TTL-bound, T0/T1 only) | tier matrix (G1) | `server.py:203-220` |
| `plan_replan(diff)` | Execution-layer replan diff (T0/T1 only) | tier matrix (G1) | `server.py:221-238` |
| `session_end(reason)` | Enqueue Dream immediately (else 60s idle-poll) | rate limit only | `server.py:239-257`, `server.py:773-787` |

Every tool result emits an **ECL v2.0 envelope sidecar** (`ecl.py` `build_for_tool_result` +
`emit_sidecar`) into `data_dir/runs/<message_id>/`. Sidecar failure is non-fatal (logged, not
propagated). Source: `server.py:42,388-410,456`.

---

## 10. EIIS / ECL conformance

| Contract | Version | Source |
|---|---|---|
| EIIS (install) | **1.4** | `EIIS_VERSION`, `CLAUDE.md:47-52`, `README.md:7` |
| ECL (runtime comms) | **2.0** | `ECL_VERSION`, `CLAUDE.md:50`, `CRYSTALIUM.md:133` |

- ECL envelopes carry **11 required fields** + SHA-256 integrity (`hashlib`). Source: `README.md:113`, `CHANGELOG.md:22-23`.
- Install target whitelist `./.eidolons/crystalium/`: only `agent.md`, `SPEC.md`,
  `install.manifest.json`, `ECL_VERSION`, optional `skills/*.md` + `schemas/*.json`. Source: `CLAUDE.md:51`.
- `install.sh` idempotent (CI second-run-no-diff). EIIS source-repo 6 required files:
  `agent.md`, `AGENTS.md`, `CLAUDE.md`, `README.md`, `install.sh`, `EIIS_VERSION`. Source: `CLAUDE.md:48-52`.

> `install.sh` is referenced throughout but was not present in the working tree at profile time
> (it is a W6 deliverable). Treat its existence as planned/pending. See "Known ambiguities."

---

## 11. JSON Schemas (`schemas/*.json`, Draft 2020-12)

Present: `crystal.v1.json`, `skill.v1.json`, `recall-request.v1.json`, `recall-result.v1.json`,
`commit-request.v1.json`, `install.manifest.v1.json`. Pydantic mirrors live in
`mcp-server/src/crystalium/schemas.py` (`Crystal`, `Skill`, `Scope`, `Provenance`, etc.).
Source: `AGENTS.md:116-123`, `schemas.py`, `server.py:50`.

> `commit-result.v1.json` is referenced by spec.yaml W1 and AGENTS.md but was NOT found among the
> present schema files. See "Known ambiguities."

---

## 12. CI / GitHub Actions

Planned workflows (`AGENTS.md:256-265`, spec.yaml W6): `test.yml`, `lint.yml`, `schema.yml`,
`eiis.yml` / `eiis-conformance.yml`. All run via `docker compose` (no host python/pip).

> No `.github/workflows/` directory was present in the working tree at profile time. CI workflows
> are a W6 deliverable, not yet landed. See "Known ambiguities."

---

## 13. Implementation waves (W1–W6, from prior spec)

The v0.1.0 build was decomposed into 6 waves. Source: `.spectra/crystalium-v0.1.0-spec.yaml:495-614`.

| Wave | Scope | Gates required |
|---|---|---|
| W1 | schemas + Pydantic mirrors + storage adapters | — |
| W2 | `enforcement.py` chokepoint + tier matrix + importance + trust + telemetry + redaction | G1, G2, G4 |
| W3 | `layers/*` + `gate.py` + Aetheryte recall + Dream scheduler/worker + composer | G3, G5, G6, G8 |
| W4 | `server.py` MCP wiring + `__main__.py` CLI + ECL envelope helper | G1–G8 |
| W5 | full test suite + canary suite (10 missions + memory-on/off A/B) | G1–G8 + A/B≥0.80 |
| W6 | `install.sh` + Docker/compose hardening + CI + DESIGN-RATIONALE + CHANGELOG | G1–G8 + EIIS v1.4 |

---

## 14. Directory map (implemented tree)

```
crystalium/
├── agent.md                  # always-loaded entry point (≤1000 tokens, P0-14)
├── SPEC.md                   # EIIS v1.4 install-target spec
├── MISSION.md                # frozen P0 bootstrap (immutable until v0.2)
├── DESIGN-RATIONALE.md       # D1–D10 decisions + citations
├── CRYSTALIUM.md             # methodology + research anchors
├── README.md  AGENTS.md  CLAUDE.md  CHANGELOG.md  LICENSE
├── EIIS_VERSION (=1.4)       ECL_VERSION (=2.0)
├── Dockerfile                # python:3.12-slim + uv; targets base, dev
├── docker-compose.yml        docker-compose.dev.yml
├── Makefile                  # host-visible wrappers (build/test/lint/schema/typecheck)
├── pyproject.toml            # root: ruff config only (real pkg in mcp-server/)
├── schemas/                  # JSON Schema Draft 2020-12 (crystal, skill, recall*, commit-request, install.manifest)
├── mcp-server/
│   ├── pyproject.toml        # the real package (name="crystalium", uv-managed)
│   ├── .python-version
│   └── src/crystalium/
│       ├── __init__.py (__version__="0.1.0")  __main__.py (CLI)
│       ├── server.py         # MCP stdio server + 7 tool handlers
│       ├── enforcement.py    # THE chokepoint (Enforcement class, _MATRIX)
│       ├── trust.py          # Tier enum + LAYER_CEILING
│       ├── config.py         # Config dataclass (env + crystalium.yaml)
│       ├── schemas.py        # Pydantic mirrors (Crystal, Skill, Scope, Provenance)
│       ├── importance.py     # importance_score (FROZEN signature)
│       ├── composer.py       # working-set composer (≤3500, G6)
│       ├── gate.py           # PromotionGate (G5)
│       ├── ecl.py            # ECL v2.0 envelope sidecar helper (G7)
│       ├── telemetry.py      # structlog JSONL + OTel
│       ├── layers/{episodic,semantic,procedural,execution}.py
│       ├── aetheryte/{retrieve.py (recall), redact.py}
│       └── storage/{relational.py (SQLite+FTS5), vector.py (LanceDB), graph.py (KuzuDB), blob.py}
│   └── tests/                # pytest (test_<module>.py + gate-anchored tests)
└── evals/                    # canary missions + memory-on/off A/B harness
    ├── missions.py  ab_memory_onoff.py  poisoning_resistance.py  selective_forgetting.py
```

---

## 15. Known ambiguities (for human curation)

1. **Spec.yaml planned filenames ≠ implemented filenames.** The prior spec (W1/W3/W4) and AGENTS.md
   §project-structure name modules `storage/sqlite.py`, `storage/lance.py`, `storage/kuzu.py`,
   `aetheryte/recall.py`, `ecl_envelope.py`, and tests `test_storage_sqlite.py` /
   `test_ecl_conformance.py` / `mcp-server/tests/canary/`. The **actual** tree implements
   `storage/relational.py`, `storage/vector.py`, `storage/graph.py`, `aetheryte/retrieve.py`,
   `ecl.py`, `test_storage_relational.py`, `test_ecl_envelope.py`, and `evals/`.
   → `spectra-conventions.md` uses the **implemented** names. Confirm this is the intended final
   layout (and consider updating spec.yaml/AGENTS.md to match) or correct the conventions if the
   tree is mid-rename.
2. **`commit-result.v1.json` missing.** Referenced by spec.yaml W1 + AGENTS.md but absent from
   `schemas/`. Confirm whether it should exist or the reference is stale.
3. **`install.sh`, `.github/workflows/`, `crystalium.yaml` not in working tree.** All are referenced
   (EIIS conformance, CI, config loader) but are W6 deliverables / runtime-side files not yet landed.
   Re-run detection after W6 closes.
4. **Test-anchor names in spec.yaml** (e.g. `test_ecl_conformance.py::test_g7_...`) may not match the
   actual `test_ecl_envelope.py`. Verify the canonical gate→test mapping when SPECTRA writes specs
   that reference gates.
