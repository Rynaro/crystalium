# Changelog

All notable changes to CRYSTALIUM are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versioning follows
[SemVer](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.2.0] — 2026-06-01

### Added

- **Env-var caller identity (`CRYSTALIUM_CALLER_EIDOLON` / `CRYSTALIUM_CALLER_TIER`).** All six
  Eidolons share one MCP server process; identity is correctly a process-level env var. Setting
  `CRYSTALIUM_CALLER_EIDOLON=atlas` (or any roster member) resolves to tier T1, enabling writes
  to the semantic/execution layers and `plan_checkpoint`/`plan_replan` that were previously
  blocked under the T2 default. `CRYSTALIUM_CALLER_TIER` allows an explicit tier override.
  Both follow the MIN-trust rule: `final = max(declared_tier, identity_tier)` so a low-trust
  identity cannot be self-elevated via an explicit override.
  The two env vars are documented in `config.py` alongside the other `CRYSTALIUM_*` vars.
  The ingest path (`crystalium.ingest`) is unaffected — it calls `resolve_caller_tier(envelope)`
  independently from `ingest_adapter` and never consults the process env, so a T3-origin
  envelope cannot be laundered upward by the process identity.
  Falls back to T2 when neither env var is set (D4 backward-compatible default preserved).

## [1.1.0] — 2026-06-01

### Added

- **CPU/GPU build variants.** `ARG TORCH_VARIANT` (`make build VARIANT=gpu`) selects the
  torch wheel. CPU is the default and the only published image; GPU (CUDA cu121, amd64-only)
  is buildable-only, for hosts that do bulk re-embedding.

### Changed

- **Container image slimmed ~4.5×: 8.9 GB → 1.97 GB** (published runtime) / 2.13 GB (dev).
  `torch` is now a direct dependency pinned to PyTorch's CPU index, dropping the ~4.4 GB
  NVIDIA CUDA stack (`nvidia/*` + `triton`) that the single-text `sentence-transformers`
  embedding workload never used. The published `ghcr.io/rynaro/crystalium:latest` is
  CPU-only and runtime-only — the dev toolchain (pytest/mypy/ruff/jsonschema) is split into
  the `dev` image stage and no longer shipped to consumers.
- Runtime entrypoint is `uv run --no-sync`: the container runs the venv baked at build time
  instead of re-resolving (and re-pulling the CUDA torch wheel) on every start. Dependency
  changes now require an explicit `uv sync`.

### Fixed

- `docker compose run` / `make test` work without manual flags: the compose file declares an
  anonymous `/app/.venv` volume (un-shadows the baked venv under the source bind-mount) and
  sets `PYTHONPATH=/app/mcp-server/src:/app` (so the `evals`-importing tests collect).

## [1.0.0] — 2026-05-31

### Added

- Conformance suite: a `conformance` pytest marker over all 8 G-gates + mechanical
  invariants (`pytest -m conformance` == "green is conformant") + a blocking CI job +
  a gate-registry self-check; working-set cap pinned to the literal 3500.
- Availability SLO: recall availability (success/attempts) metric + the W1 latency
  panel now reports it (target ≥99% availability, recall p95 <200 ms).
- `MIGRATION.md` (per-wave config-key delta, schema-v1-stable, the one behavior change)
  and `docs/roster-pr.md` (drafted nexus roster entry, operator-opened).
- DESIGN-RATIONALE D6.6 (W7 Extended Mind) + D6.7 consolidated 8-result ablation table
  + marker legend.

### Changed

- **Default ON (recorded A/B wins):** `write_dedup_merge` (W5) and `recall_active_only`
  (W6). All other augment flags stay OFF (honest nulls).
- Canary honestly repaired (de-vacuumed off-arm, episodic + isolated missions,
  single-run headline, restated gate): memory-on beats memory-off **+0.75** (was −0.75).
- Version 0.8.0 → 1.0.0.

### Fixed

- `Config.from_env()` defaulted `write_dedup_merge` / `recall_active_only` to False,
  contradicting the dataclass True — env-built configs silently reverted the flips.
  Reconciled (both default True; guarded by a default-parity test).
- Canary harness bit-rot (`_get_crystal`/`_row_count` read a non-existent
  `enforcement._store`) and the `run_all` double-run (headline computed from a
  different execution than the displayed results).
- install manifest now validates against `install.manifest.v1.json` (`ecl_version`,
  role `schema`, schema extended for `profile`/`roster`/`scope`).

### Known limitations

- Canary below the 0.80 bar by one mission (recall-after-bi-temporal-update re-index
  `[GAP]`); recall p95 ~205 ms marginally over the 200 ms embedder-bound target. Both
  `[PROXY]` (synthetic harness). See `evals/BENCH-NOTES.md`.

## [0.8.0] — 2026-05-31 — Wave 7: Eidolons Integration

### Added

- `crystalium.ingest` (8th MCP tool): ingest a roster ECL handoff envelope (v1.x/v2.x)
  → `crystal.v1` via a generic adapter, preserving the native artifact verbatim in
  `encoding_context` and committing through the chokepoint (MIN trust tier preserved;
  T3 → episodic-quarantined, never laundered).
- EIIS v1.4 finalization: install `--version`/`--manifest-only`/`--hosts`/`--members`;
  AGENTS.md YAML frontmatter (`version`, `handoffs.upstream/downstream`, ECL/EIIS pins);
  host `serve` wiring + repo `.mcp.json` self-wire; standalone + 2-member verified.

## [0.7.0] — 2026-05-30 — Wave 6: Security & Integrity Hardening

### Added

- Belief-drift detection (`drift_detect`, OFF), quarantine triage CLI (T0, audited,
  reject = soft-deprecate), write-conflict detection (`write_conflict_detect`, OFF),
  and `recall_active_only` (**ON** — excludes deprecated/superseded from recall;
  poisoning ASR 1.00→0.00). Three append-only audit ledgers.

## [0.6.0] — 2026-05-29 — Wave 5: Retrieval Intelligence (Aetheryte II)

### Added

- Pattern completion (`recall_completion`, OFF), encoding-specificity re-rank
  (`recall_context_match`, OFF), pattern-separation dedup-merge (`write_dedup_merge`,
  **ON** — write amp 1.0→0.667), predictive prefetch (`recall_prefetch`, OFF).

## [0.5.0] — 2026-05-29 — Wave 4: Forgetting as a Faculty

### Added

- FSRS/DSR forgetting (`forgetting_fsrs`, OFF), value-aware eviction, spaced
  re-surfacing, Ricoeur-protected class, and the right-to-be-forgotten operator op
  (`crystalium forget`, T0, audited — the one sanctioned hard-delete).

## [0.4.0] — 2026-05-28 — Wave 3: The Dream Becomes Intelligent

### Added

- Prioritized replay (`dream_replay_evb`, OFF), CLS interleaving (`dream_interleave`,
  OFF), synaptic-tagging consolidation (`dream_stc`, OFF).

## [0.3.0] — 2026-05-28 — Wave 2: Importance as Expected Value of Backup

### Added

- EVB importance scorer (`evb_enabled`, OFF; Gain×Need, Mattar & Daw 2018) + the
  `memory_dynamics` persistence column.

## [0.2.0] — 2026-05-28 — Wave 1: Foundations & Eval Spine

### Added

- Container-first PreToolUse hook, the `/prepush` command, the evals/canary spine, and
  the `memory_dynamics` schema field.

## [0.1.0] — 2026-05-28

### Added

- Initial implementation of the four-layer memory harness (Episodic, Semantic,
  Procedural, Execution) with one mechanical write-gate chokepoint
  (`enforcement.py`).
- MCP server (stdio JSON-RPC 2.0) exposing 7 tools: `recall`, `commit`,
  `update`, `skill_invoke`, `plan_checkpoint`, `plan_replan`, `session_end`.
- Storage adapters: SQLite + FTS5 (relational + sparse indexing), LanceDB
  (vector), KuzuDB (graph), content-addressed filesystem blob tier.
- Aetheryte hybrid recall surface: BM25 ⊕ vector ⊕ graph retrieval with
  optional reranking when k > 20.
- ECL v2.0 envelope sidecar emission on every tool result (11 required fields,
  SHA-256 integrity via `hashlib`).
- EIIS v1.4-conformant `install.sh` with canonical inventory whitelist sweep
  (Appendix A reference implementation).
- Dream consolidation worker (async, `apscheduler`-backed) with dual-trigger
  (idle-poll every 60s + explicit `session_end` tool call).
- Bounded slotted working-set composer: enforces ≤3,500 tokens across six
  typed slots (executive 300, procedural 600, semantic 800, episodic 800,
  execution 1000, buffer 300). Deterministic eviction by importance score.
- Tier × Layer × Operation matrix (§4): 12 rows × 4 tiers; guards admission
  per trust tier and target layer. Prevents T3 pollution, blocks multi-agent
  poisoning via MIN-tier propagation rule.
- 10-mission canary suite with memory-on/off A/B harness. Headline metric:
  memory-on beats memory-off on ≥80% of canaries.
- 8 P0 conformance gates (G1–G8) with `test_anchor` paths in
  `test_enforcement.py`, `test_skill_invoke.py`, `test_composer.py`,
  `test_dream_scheduler.py`, `test_ecl_envelope.py`, `test_trust_propagation.py`,
  `test_promotion_gate.py`.
- Container-first architecture: all Python toolchain (uv, pytest, embeddings,
  storage engines) runs inside `docker compose service crystalium`. Host runs
  only `docker compose`, `git`, `make`.
- Redaction layer: regex pre-pass + small-LLM judge for sensitivity-tagged
  content. Re-applied at every cross-agent handoff (ECL envelope).
- Operator CLI: `crystalium promote list` / `crystalium promote review <id>
  [--accept|--reject]` for Semantic promotion inbox.
- Importance function (D6): `importance_score(record, *, now) -> float` with
  frozen signature; weights tuple externally tunable (entry point for v0.2
  adaptive learning, D11).
- Bi-temporal update primitive: `crystalium.update(id, patch, reason)`
  invalidates-old, writes-new with `superseded_by` link. Never hard-delete.

### Out of scope (v0.1.0 — hooks left, not built)

- Polyglot skill abstraction (`language` + `capability_class` fields
  reserved in `skill.v1.json`; raised by v0.2).
- Adaptive/learned importance weights (`WEIGHTS` tuple is swap point; D11
  deferred).
- Belief-drift detection (`provenance` field on every crystal + audit log
  populated; analysis layer deferred).
- Quarantine review UI (`validation_state: quarantined` field reserved;
  `crystalium promote` CLI can enumerate, v0.2 adds UI).
- Server profile (Postgres/Qdrant/Neo4j) and LangGraph adapter (`config.profile`
  field raises `NotImplementedError("v0.2")` on `"server"`; local-only in v0.1).
- In-weights consolidation / LoRA fine-tuning (eviction is highest-importance-first
  only; no gradient-based consolidation).
- Multi-agent CRDT/consensus (append-only + content-addressed + last-write-wins
  with `superseded_by` sufficient for v0.1; CRDT complexity deferred).
- REM-style associative linkage (graph tier exists for fact retrieval; optimal
  link learning deferred).
- Streamable-HTTP transport (`CRYSTALIUM_TRANSPORT=http` raises
  `NotImplementedError("v0.2")`; stdio only).
- Nexus roster entry (blocked by `capability_class` enum closure;
  standalone repo in v0.1, roster integration in v0.2+).

### Quality metrics

- `agent.md`: ≤1,000 tokens (verified by CI).
- Composer: ≤3,500 tokens (G6 invariant).
- Test coverage: G1–G8 all passing; canary suite ≥0.80 A/B pass rate.
- `install.sh` idempotency: second-run produces identical install target
  (CI job enforces).
- DESIGN-RATIONALE.md: ≥10 citations (anchor list from MISSION.md), [UNVERIFIED]
  markers on unverifiable claims.
- EIIS v1.4 conformance: source-repo has all 6 required files; install target
  whitelist enforced; `agent.md` + `SPEC.md` dual-write recorded in
  `install.manifest.json`.
- ECL v2.0 conformance: every tool result emits envelope with 11 required
  fields; `integrity.value` matches `sha256(payload_bytes)`.

### Known limitations

- Verifier sandbox is soft (subprocess inside container, not DinD or microVM).
  OS-level isolation (DevContainer, Firecracker) is operator's responsibility.
- Offline consolidation (Dream) cannot perform gradient-based learning; it proposes
  (via clarifying LLM call), never auto-learns. Weights remain static.
- Importance `novelty_at_write` is frozen at write time; not recomputed as
  neighbourhood shifts (OQ-9).
- k=3 corroboration may be hard to achieve in single-operator or single-Eidolon
  workflows (OQ-5).
- `force_promote` (T0 only) writes straight through (no inbox); audit lives in
  telemetry (OQ-1).

---

**Starting from v0.1.0, CRYSTALIUM is versioned according to
[SemVer 2.0.0](https://semver.org/spec/v2.0.0.html). Breaking changes will bump
MAJOR; new backwards-compatible features will bump MINOR; bugfixes will bump
PATCH.**
