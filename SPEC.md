# CRYSTALIUM v0.1.0 Specification

The authoritative spec lives at `.spectra/crystalium-v0.1.0-spec.md` (narrative) and `.spectra/crystalium-v0.1.0-spec.yaml` (machine-readable). This file is the EIIS v1.4 install-target source of truth.

## Anchor list

- **8 validation gates G1–G8** — see `.spectra/crystalium-v0.1.0-spec.md` §3
  - G1: T3 cannot commit above Episodic
  - G2: T2 procedural commits land as candidate
  - G3: Procedural verifier-gated admission
  - G4: Trust-tier propagation blocks T3 laundering
  - G5: Human-confirm default window (30 days post-install)
  - G6: Working-set budget invariant (≤3,500 tokens, deterministic eviction)
  - G7: ECL envelope conformance per tool result
  - G8: Dream dedup on idle + event triggers

- **Tier × Layer × Operation matrix** — see §4 (12 rows × 4 columns; four operations: commit, propose_promote, force_promote, recall)

- **Tool surface contract** — see §5 (9 tools — the v0.1.0-era 7 plus `ingest`
  (v0.7/W7) and `graph_export` (v1.5.0/W-GE5) added in later releases; failure
  classes, enforcement order)

- **Working-set composer** — see §6 (slot allocations, eviction rule)

- **Build waves W1–W6** — see §8 (sequential; each wave's `container_test` runs inside `docker compose run --rm crystalium`)

- **EIIS v1.4 conformance plan** — see §11 (install-target whitelist + cleanup sweep)

- **ECL v2.0 conformance plan** — see §12 (11 required envelope fields + integrity helper)

- **Canary suite (10 missions)** — see §13 (memory-on/off A/B; headline metric ≥0.80 pass rate)

## P0 invariants

See `agent.md` §"P0 invariants". All 7 are mechanical, enforced at the chokepoint before any store mutation.

## Out-of-scope hooks

See `.spectra/crystalium-v0.1.0-spec.yaml` `out_of_scope_hooks:` block. V0.1 leaves doors open for polyglot skill abstraction, adaptive importance weights, belief-drift detection, server profile (Postgres/Qdrant/Neo4j), REM-style associative linkage, and others — without building them. Each hook is a config knob or frozen function signature that a future version can extend.
