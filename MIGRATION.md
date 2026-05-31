# Migration notes — CRYSTALIUM v0.1.0 → v1.0.0

This document covers the operator-facing changes across the v0.1.0 → v1.0.0 wave
series (W2–W8). **There are no breaking storage changes** and only **one runtime
behavior change** (two flags now default ON). Most additions are config flags
defaulted OFF (ablation-or-revert; see `DESIGN-RATIONALE.md` §D6.7 for the full
ablation table and `evals/BENCH-NOTES.md` for the A/B verdicts).

## Schema: no break

`schemas/crystal.v1.json` (and every other schema) **stays at v1** — no `crystal.v2`.
Fields added across the waves (`tags`, `encoding_context`, `memory_dynamics`,
`protected`) were declared in v1 from the start (nullable / additive) and are
populated by later waves. A v0.1 store opens unchanged; new columns are added by the
idempotent SQLite migration in `RelationalStore._migrate` (guarded `ALTER TABLE …`).

## The one behavior change

Two ablation winners now default **ON** (they had recorded A/B wins):

| flag | default | why | wave / verdict |
|---|---|---|---|
| `write_dedup_merge` | **ON** | near-duplicate writes merge (write amp 1.0→0.667, precision held) | W5 PASS |
| `recall_active_only` | **ON** | recall excludes deprecated/superseded crystals (poisoning ASR 1.00→0.00; also a correctness fix) | W6 PASS |

Operator impact: a commit of a near-duplicate fact may return `status:"merged"` (with
an existing id) instead of a new crystal; recall no longer returns deprecated or
bi-temporally-superseded crystals. To restore v0.1 behavior, set
`CRYSTALIUM_WRITE_DEDUP_MERGE=false` and `CRYSTALIUM_RECALL_ACTIVE_ONLY=false`.

> v1.0 reconciled a latent bug where `Config.from_env()` defaulted these two to
> `false` while the dataclass defaulted `true` — env-built configs silently reverted
> the flips. Both constructors now agree (guarded by a default-parity test).

## Config keys added per wave (all default OFF unless noted)

| wave | env var → default |
|---|---|
| W2 (EVB) | `CRYSTALIUM_EVB_ENABLED`=false · `CRYSTALIUM_EVB_PERCENTILE`=0.5 · `CRYSTALIUM_DREAM_REPLAY_EVB`=false |
| W3 (Dream) | `CRYSTALIUM_DREAM_INTERLEAVE`=false · `CRYSTALIUM_DREAM_INTERLEAVE_RATIO`=0.5 · `CRYSTALIUM_DREAM_STC`=false · `CRYSTALIUM_STC_THRESHOLD`=0.5 · `CRYSTALIUM_STC_WINDOW_S`=3600 |
| W4 (Forgetting) | `CRYSTALIUM_FORGETTING_FSRS`=false · `CRYSTALIUM_R_FLOOR`=0.7 · `CRYSTALIUM_RESURFACE_FLOOR`=0.85 |
| W5 (Retrieval) | `CRYSTALIUM_RECALL_COMPLETION`=false · `CRYSTALIUM_COMPLETION_MAX_HOPS`=2 · `CRYSTALIUM_COMPLETION_DECAY`=0.5 · `CRYSTALIUM_RECALL_CONTEXT_MATCH`=false · `CRYSTALIUM_WRITE_DEDUP_MERGE`=**true** · `CRYSTALIUM_SEP_THRESHOLD`=0.92 · `CRYSTALIUM_RECALL_PREFETCH`=false |
| W6 (Security) | `CRYSTALIUM_DRIFT_DETECT`=false · `CRYSTALIUM_DRIFT_TAU_LO`=0.80 · `CRYSTALIUM_DRIFT_TAU_HI`=0.97 · `CRYSTALIUM_WRITE_CONFLICT_DETECT`=false · `CRYSTALIUM_CONFLICT_TAU_LO`=0.80 · `CRYSTALIUM_RECALL_ACTIVE_ONLY`=**true** |
| W7 (Integration) | _(no flags — adds the `crystalium.ingest` tool; install flags `--version`/`--manifest-only`/`--hosts`/`--members`)_ |

## Tool surface

The MCP surface grew from **7 → 8** tools: W7 added **`crystalium.ingest`** (ingest a
roster ECL handoff envelope, v1.x/v2.x, mapping it to a crystal while preserving
provenance + MIN trust tier). The original 7 tools are unchanged.

## Operator CLI additions

- `crystalium forget <id> --reason …` (W4) — the one sanctioned, audited hard-delete (RTBF, T0).
- `crystalium quarantine list` / `review <id> --accept|--reject --reason …` (W6) — triage queue over quarantined crystals (T0, audited; reject soft-deprecates).
- `install.sh --version` / `--manifest-only` / `--hosts auto` / `--members` (W7).

## Known limitations carried into 1.0

- **Canary:** memory-on beats memory-off (+0.75) but is below the 0.80 bar by one
  mission — a recall-after-bi-temporal-update re-index `[GAP]` (see BENCH-NOTES).
- **Availability SLO:** availability 100% ✓; recall p95 ~205 ms (embedder-bound,
  marginally over the 200 ms target). Both `[PROXY]` (synthetic harness).
- Six of eight ablation augments are honest nulls, shipped OFF, fully tested.
