# Skill: forget — Dream prune rules + audit

Load this skill when diagnosing stale crystal retention or configuring prune
thresholds.

## When to load

- A crystal should have been pruned but is still appearing in recall.
- Tuning importance weights to promote faster forgetting.
- Understanding which crystals are eligible for pruning.

## What Dream prune does (P0-11, FORGE D3)

Dream prune runs in the `prune` phase of the orient→gather→consolidate→prune
cycle. It:

1. Scores all `status="active"` crystals by `importance_score()`.
2. Marks low-importance crystals `status="deprecated"` (never deletes rows).
3. Updates `temporal.t_valid_to = now` on deprecated records.

**Never hard-deletes.** All records remain queryable in time-travel mode.

## Prune eligibility rules

- `validation_state = "quarantined"` AND `importance < 0.10` AND age > 7 days.
- `status = "active"` AND `importance < 0.05` AND `last_access` > 90 days ago.
- Execution layer: any crystal past its TTL (default 24h).

These thresholds are configurable via `crystalium.yaml`; defaults are frozen at
v0.1.

## Audit trail

Every prune event writes a telemetry record:
  `{tool: "dream.prune", layer, crystal_id, importance, reason}`.

Prune decisions are reviewable via the `crystalium promote review` CLI
(`status=deprecated` filter).

## Importance decay

`recency_score(t) = exp(-λ * Δt)` where `λ = ln(2) / RECENCY_HALFLIFE_DAYS`.
Default `RECENCY_HALFLIFE_DAYS = 14`. OQ-4: may be too aggressive for
long-lived workspaces; operator-tunable post-v0.1.
