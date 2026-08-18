# Skill: recall — budgeting + working-set composer

Load this skill when planning or debugging a recall call.

## When to load

- Diagnosing why recall returned fewer results than expected.
- Tuning `k` for a given query scope.
- Understanding slot eviction order.

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
