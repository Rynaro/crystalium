# Skill: dream-cycle — orient → gather → consolidate → prune

Load this skill when debugging Dream scheduling or understanding consolidation.

## When to load

- Dream is not running after a long idle period.
- Two Dream runs were enqueued instead of one (G8 dedup issue).
- Consolidated semantic records are lower quality than expected.

## Four phases

### 1. Orient
- Reads the `tool_calls` telemetry table for the last N minutes.
- Identifies high-activity projects and layers.
- Determines which crystals are candidates for consolidation.

### 2. Gather
- Fetches top-k episodic crystals by importance for the active projects.
- Loads linked semantic crystals via KuzuDB graph traversal.
- Trust tiers preserved: gather step records `min_tier = min(input_tiers)`.

### 3. Consolidate
- Groups semantically related crystals via embedding similarity (LanceDB ANN).
- For each cluster: proposes a new Semantic crystal via `gate.propose_semantic()`.
- Trust propagation: `proposed_crystal.trust_tier = min_tier` (P0-6, G4).
- Does NOT write directly — all consolidations land as `pending_promotions`.

### 4. Prune
- Marks low-importance / expired crystals `status="deprecated"`.
- Execution layer TTL sweep (all plans past 24h).
- Writes audit telemetry per-prune event.

## Trigger conditions (FORGE D3)

| Trigger | Condition |
|---|---|
| Idle poll | `now - last_activity > 300s` AND `now - last_dream > 1800s` |
| Session end | `crystalium.session_end()` called |
| Event count | `pending_events > event_threshold` (default: 100) |
| Nightly cron | APScheduler `CronTrigger(hour=3)` |

## Dedup (G8)

All trigger paths share `resolve_dream_run_id(now, last_dream)` — a
deterministic function producing the same `run_id` for inputs within the
same 30-minute window. Concurrent triggers collapse to one execution.
