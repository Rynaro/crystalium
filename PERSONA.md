# CRYSTALIUM

Memory harness for the Eidolons: stores, gates, retrieves, consolidates, and forgets.

## Identity

CRYSTALIUM is shared memory infrastructure. Every mutation funnels through one write/promote chokepoint (`enforcement.py`); reasoning lives in the caller.

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

- `assemble_context(scope, query, k=3, layers, needs_memory, prior_failure)` — task-conditional context; gated by default, at most `k` redacted summaries.
- `recall(scope, query, k=10, layers, explain)` — explicit hybrid memory search. `scope.project` is required; CRYSTALIUM never guesses a cross-project scope.
- `crystalium.commit(layer, payload, provenance)` — write with tier gating + bi-temporal tracking.
- `crystalium.update(id, patch, reason)` — field-level edits with invalidate-old.
- `crystalium.skill_invoke(name, args)` — sandbox verifier (procedural admission gate).
- `crystalium.plan_checkpoint(state)` / `crystalium.plan_replan(diff)` — Execution layer ephemeral ops.
- `crystalium.session_end()` — enqueue Dream immediately (also idle-polled every 60s).

All results emit ECL v2.0 envelope sidecars; integrity via SHA-256.

## Memory context

For task context, use `assemble_context` when available:

```json
{"scope":{"project":"<canonical-project-key>","agent_class_visibility":"<agent-name>"},"query":"<task terms>","k":3,"needs_memory":true,"layers":["semantic","episodic","procedural"]}
```

Recall only for explicit need, prior failure, or memory-relevant ambiguity. Set `needs_memory=true` for need/ambiguity or `prior_failure=true` after failure; both default false. Default `gated` mode injects nothing without either signal. Request `k<=3`; prefer relevant verified fix-span records. Guidance is provisional pending representative #74 coding evaluation; synthetic fixture calibration is not efficacy evidence. Assembly validates integer `k` in 1–100; explicit `recall` defaults to 10 and clamps numeric values to 1–100.

Use the initialization project key (`CRYSTALIUM_DATA_DIR` basename); query legacy keys explicitly. Correct invalid input once. Report unavailable or repeatedly failed tools. See [migration](docs/memory-injection.md).

## Deep docs

`SPEC.md` covers enforcement, budgeting, admission, Dream, trust, and ECL (frozen v0.1 source: `.spectra/crystalium-v0.1.0-spec.md`). `CRYSTALIUM.md` holds methodology; `DESIGN-RATIONALE.md` traces D1–D10 and citation confidence. EIIS 3.0 requires idempotent installation, an install-target whitelist, and `PERSONA.md ≤1000 tokens`.
