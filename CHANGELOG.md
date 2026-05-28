# Changelog

All notable changes to CRYSTALIUM are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versioning follows
[SemVer](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
  `test_dream_scheduler.py`, `test_ecl_conformance.py`, `test_trust_propagation.py`,
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
