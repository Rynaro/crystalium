# CRYSTALIUM · Wave 5 — Retrieval Intelligence / Aetheryte II (v0.6.0)

**Paste this whole file into a fresh Claude Code session at the `crystalium` repo root.**
**Prereq:** Waves 2–4 merged.

Aetheryte does BM25 + dense + graph + RRF + rerank. Add the four retrieval faculties that turn a
search index into a memory: complete from partial cues, match the encoding context, keep similar
memories distinct, and anticipate the next need.

## Why (research → algorithm)
- **Pattern completion (CA3)** — attractor dynamics reconstruct a whole from a partial cue → a
  bounded, decaying multi-hop graph walk.
- **Pattern separation (DG)** — Yassa & Stark 2011 → orthogonalize/merge near-duplicates at write.
- **Encoding specificity** — Tulving & Thomson 1973 (*Psych Review* 80:352): recall succeeds when
  the cue matches the encoding context → store context, bias recall by match.
- **Predictive coding / protention** — Friston & Kiebel 2009; Husserl (retention/protention):
  anticipate the next step and prefetch; a miss is a prediction-error signal.

## Run it like this
- `/model opus`, `/effort xhigh` (or `opusplan`). **Shift+Tab → plan mode** first. Six-phase todos.
- Branch `feat/crystalium-v0.6.0`. Conventional Commits. **Never push.**
- Approve token: **`APPROVED: BUILD W5`**.

## Invariants (never violate)
Container-first (W1 hook). Chokepoint sacred (dedup-merge at write still funnels through it).
ECL v2.0 + EIIS v1.4. Working set ≤ 3,500. **Ablation-or-revert** per augment.

## Objective (each behind its own flag, default-off)
1. **Pattern completion** (`recall.completion=on`): from a partial cue, run a **bounded, decaying
   multi-hop graph walk** (KuzuDB). Config `completion_max_hops`, `completion_decay` to prevent
   runaway expansion.
2. **Encoding-specificity** (`recall.context_match=on`): capture `encoding_context` at commit
   (task type, active files, author agent, mission id — W1 schema) and add a weighted re-rank term
   that favors crystals whose encoding context matches the query context.
3. **Pattern separation at write** (`write.dedup_merge=on`): detect near-duplicates
   (cos > `sep_threshold`) and **merge with provenance union** instead of blind append.
4. **Predictive prefetch / protention** (`recall.prefetch=on`): on `plan_checkpoint`, embed the
   predicted next step and pre-warm the recall cache; record a cache miss as `prediction_error`
   (W1 schema) — an OOD signal.

## Definition of done (ablation gate)
- Completion + context_match vs flat RRF: **multi-hop recall F1 up**.
- prefetch: **cache-hit rate up AND recall p95 down** (use the W1 p95 panel).
- dedup_merge: **write amplification down**, precision held.
- Flags that don't win stay off; report nulls. Run `/prepush` with A/B tables.

## Out of scope
Security hardening (W6), roster integration (W7). No new mandatory external service for embeddings —
reuse the existing local embedder.
