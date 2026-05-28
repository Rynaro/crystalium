# CRYSTALIUM

Portable memory harness for the Eidolons. Stores, gates, retrieves, consolidates, and forgets memory. Does not reason, plan, or write code.

## Identity

CRYSTALIUM is infrastructure for a team of AI coding agents sharing a personal memory substrate. It implements one mechanical write/promote chokepoint (enforcement.py) through which every memory mutation funnels. The thesis: constrained interfaces beat raw autonomy (SWE-agent ACI pattern).

**Vocabulary (frozen):** Crystal = an admitted memory record. Aetheryte = the recall/index network. Dream = the async consolidation worker.

## Four memory layers

| Layer | Holds | Write path | Lifetime |
|---|---|---|---|
| **Episodic** | Past missions/sessions/PRs as pointers | Fast, ungated → `quarantined` | Long-term, Dream-pruned |
| **Semantic** | Project conventions, sigs, API facts | **Gated promotion only** | Indefinite, bi-temporal |
| **Procedural** | Verified, executable, reusable skills | **Verifier-gated admission only** | Indefinite, utility-scored |
| **Execution** | In-flight plan, state, replan history | Every step, ephemeral | TTL, expires at task end |

## P0 invariants (mechanical, not advisory)

1. **Capture is ungated** → quarantined into raw Episodic. Gate promotion, not capture.
2. **T3 (environment/tool-ingested) writes ONLY Episodic-quarantined.** Never Semantic/Procedural directly.
3. **Procedural admission requires verifier-pass in sandbox** (subprocess + 30s timeout + 8 KiB output cap).
4. **Semantic promotion requires ≥k corroboration OR human-confirm** (k=3; human-confirm default ON for first 30 days post-install).
5. **Updates are bi-temporal** — invalidate-old, write-new. Never hard-delete. Rollback precondition.
6. **Trust tier carries through consolidation** — MIN of inputs. Blocks multi-agent poison laundering.
7. **Path-traversal guard + per-process rate limit + telemetry on every call** — exactly as atlas-aci does.

## Tool surface

- `crystalium.recall(scope, query, k, layers)` — hybrid (BM25 ⊕ vector ⊕ graph) with reranking if k>20.
- `crystalium.commit(layer, payload, provenance)` — write with tier gating + bi-temporal tracking.
- `crystalium.update(id, patch, reason)` — field-level edits with invalidate-old.
- `crystalium.skill_invoke(name, args)` — sandbox verifier (procedural admission gate).
- `crystalium.plan_checkpoint(state)` / `crystalium.plan_replan(diff)` — Execution layer ephemeral ops.
- `crystalium.session_end()` — enqueue Dream immediately (also idle-polled every 60s).

All results emit ECL v2.0 envelope sidecars; integrity via SHA-256.

## When to load deeper docs

- **Enforcement design:** load `specs/crystalium-v0.1.0-spec.md` §2 (architecture), §4 (tier matrix).
- **Recall budgeting + working-set composer:** load spec §6 (slot allocations, eviction rule).
- **Skill admission gate:** load spec §3 G3 + §5.4 (`skill_invoke` contract).
- **Dream cadence + forgetting:** load spec §3 G8 + §15 (out-of-scope hooks for adaptive weights).
- **Trust propagation rule:** load spec §3 G4 + §4 note 1.
- **ECL conformance:** load spec §12 (11 required envelope fields).

## SPEC + Methodology

Full decision-ready spec: `.spectra/crystalium-v0.1.0-spec.md` (70 sections; test_anchors + gates + waves). The spec is frozen for v0.1 and shipped to install target as `SPEC.md`.

Full methodology: `CRYSTALIUM.md` (research anchors + constrained-interfaces thesis).

Design rationale: `DESIGN-RATIONALE.md` (every D1–D10 decision traced to source + [UNVERIFIED] markers for external citations).

EIIS v1.4 conformance: `install.sh` idempotent, install-target whitelist enforced, `agent.md ≤ 1000 tokens` verified at CI.
