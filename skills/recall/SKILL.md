# Skill: recall — budgeting + working-set composer

Load this skill when planning or debugging a recall call.

## When to load

- Diagnosing why recall returned fewer results than expected.
- Tuning `k` for a given query scope.
- Understanding slot eviction order.
- Choosing conditional task context versus explicit memory search.

## Calling contract

For task context, use `assemble_context`; default `gated` mode retrieves only when
`needs_memory` or `prior_failure` is true (both default false). Set signals from
the task rather than making every pre-flight unconditional. Recommend `k<=3`;
assembly defaults to 3 and rejects non-integers or values outside 1–100.

```json
{"scope":{"project":"<canonical-project-key>","agent_class_visibility":"<agent-name>"},"query":"<task terms>","k":3,"needs_memory":true,"layers":["semantic","episodic","procedural"]}
```

For explicit search, use `recall`: `k` defaults to 10 and numeric values are clamped
to 1–100. Its search behavior is unchanged. Both tools require `scope.project` and
`query`; neither guesses cross-project scope. Use the canonical key advertised by
server initialization (the `CRYSTALIUM_DATA_DIR` basename).

Correct invalid input once. If the tool is unavailable or the corrected call fails,
report memory context unavailable; never claim success. The server enforces calls
it receives, not calls an unwired host never makes. See
[operator modes and selection limits](../../docs/memory-injection.md).

## Working-set slot caps (P0-9, FORGE D9)

| Slot | Cap (tokens) |
|---|---|
| executive | 300 |
| procedural | 600 |
| semantic | 800 |
| episodic | 800 |
| execution | 1000 |
| buffer | 300 |
| **total** | **3500** |

## RRF (Reciprocal Rank Fusion)

Aetheryte combines three ranked lists: BM25 (SQLite FTS5), dense vector
(LanceDB), and graph-neighbor expansion (KuzuDB). Fusion formula:

  `rrf_score(d) = sum_i( 1 / (k + rank_i(d)) )`  where `k=60` (Cormack 2009).

Re-ranking activates when `k > 20` (sentence-transformers cross-encoder if
available; otherwise falls back to BM25-only rank).

## Eviction rule

Records sorted ascending by `(importance, last_access, record_id)`. Lowest
tuple evicted first. Deterministic: same inputs → same kept set.

Importance `f(access_freq, recency, outcome_success, novelty)` with frozen
weights `(0.25, 0.30, 0.25, 0.20)`. Recency half-life = 14 days.

## Trust filtering on recall

Trust tier is NOT filtered at recall time. Filtering happens at the downstream
commit via MIN-trust propagation (G4). Redactor runs between retrieval and LLM.
