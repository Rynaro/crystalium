# Skill: commit — tier matrix + bi-temporal flow

Load this skill when writing to any memory layer.

## When to load

- Choosing the correct `layer` for a commit call.
- Verifying a caller has the right trust tier for the operation.
- Understanding bi-temporal bookkeeping rules.

## Tier × layer × operation matrix (FORGE D1)

| Layer | T0 | T1 | T2 | T3 |
|---|---|---|---|---|
| Episodic commit | allow | allow | allow | allow_quarantine |
| Semantic commit | allow | allow | deny | deny |
| Procedural commit_candidate | allow | allow | allow | deny |
| Execution commit | allow | allow | deny | deny |
| Any recall | allow | allow | allow | allow |

`allow_quarantine` = commit succeeds but `validation_state="quarantined"`.
T3 callers NEVER write Semantic/Procedural/Execution directly (G1, P0-2).

## Bi-temporal write flow (P0-5)

1. Assign new `crystal_id` (UUID4).
2. `t_valid_from = now`.
3. Write new record.
4. If replacing an existing record: `old.t_valid_to = now`, `old.superseded_by = new_id`.
5. NEVER delete any row.

## Promotion gate (G5)

Within the first 30 days post-install (`install.ts` + 30d window) every
promotion proposal lands in `pending_promotions` table. Set
`CRYSTALIUM_AUTO_CONFIRM=1` to bypass (test-only).

## Trust propagation at consolidation (G4)

When a summarizer reads crystals from multiple trust tiers:

  `effective_tier = min(inputs.trust_tier)`

If `effective_tier = T3`, any downstream commit to Semantic/Procedural is
rejected with `TierCeilingViolation` + structured advice.
