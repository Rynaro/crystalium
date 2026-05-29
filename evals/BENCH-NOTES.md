# Ablation bench — notes & tracked discrepancies (W1, v0.2.0)

## What the bench is

The bench is the **arbiter** for the ablation-or-revert rule. Every later wave's
algorithm ships behind a Config flag defaulted **off**, and only flips on if the
bench shows it beating the prior version on the wave's named metric.

- `python -m evals canary --mode both` — legacy memory-on/off A/B headline.
- `python -m evals ab --flag <name> --missions CAN-1,CAN-3,...` — generalized
  ablation: `ab(flag, missions) -> {axis: (on, off, delta)}`. `--flag memory`
  reproduces the legacy null-arm; any Config boolean field toggles live-on/off.
- `python -m evals axes [--demo|--matrix JSON]` — SWE-Bench-CL axes
  (average_accuracy, forgetting, backward/forward transfer, tool_use_efficiency).
- `python -m evals forget` — selective-forgetting probe (reuses
  `selective_forgetting.py`; also drives CAN-4).
- Container-first runner (CI-proven): `docker run --rm crystalium:dev python -m evals ...`
  or `make bench`. (`docker compose run` is unusable for the venv — see below.)

The deterministic seeded fixture repo is `evals/fixtures/fixture_repo.json`,
loaded by `evals/fixture_repo.py` (byte-stable across loads); both A/B arms can
seed it for a shared baseline.

## Reproduced v0.1 parity (DoD G-A) — PASS

`ab(flag="memory", missions={CAN-1,CAN-3,CAN-4,CAN-5})` reproduces the v0.1
headline **exactly**: `pass_on=0.25, pass_off=1.0, delta=-0.75` — byte-identical
to the committed baseline `evals/results/2026-05-28T20-09-30…json`.

### [UNVERIFIED] Honest null result (not massaged)
The headline delta is **negative** (memory-off "wins"). This is a **pre-existing
property of the canary suite**, not a v0.2.0 regression: the null arm's mission
oracles are lenient (an empty store trivially satisfies "no-leakage"-style
criteria), so the A/B does not yet show memory *helping*. Reported plainly per
the ablation-or-revert rule. Strengthening the missions with genuinely
memory-dependent oracles is later-wave work; W1 only builds the measurement
spine and reproduces the baseline.

## DECISION-2 — A/B arm-set drift — RECONCILED (spec → code) in v0.2.0

The A/B arm set previously disagreed between code and the spec, and the two also
numbered missions CAN-3…CAN-7 differently. Reconciled **spec → code** (code is
the running reality; preserves the v0.1 parity baseline):

- **Single source of truth:** `evals/missions.py` `AB_ARM_MISSION_IDS = {CAN-1,
  CAN-3, CAN-4, CAN-5}` (the four `ab_arm` missions: recall-across-sessions,
  poisoning-resistance, selective-forget, multi-agent-isolation).
- `.spectra/crystalium-v0.1.0-spec.md` §13 and `.spectra/crystalium-v0.1.0-spec.yaml`
  `canary_missions` + `headline_ab_metric` were rewritten so their CAN-3…CAN-7
  identities and `ab_arm` flags match the code, and the headline now reads
  `{CAN-1, CAN-3, CAN-4, CAN-5}`.

Code↔(old spec) mission-numbering map, for anyone reading pre-v0.2.0 history:

| CAN-N | code identity (canonical) | old spec identity (pre-reconcile) |
|---|---|---|
| CAN-3 | poisoning_resistance_t3_episodic_only | promote_gate_T2_procedural_candidate |
| CAN-4 | selective_forget_superseded | poisoning_resistance_T3_summarization |
| CAN-5 | multi_agent_isolation | selective_forget_bi_temporal |
| CAN-6 | procedural_verifier_required | multi_agent_isolation |
| CAN-7 | bitemporal_correctness | procedural_verifier_pass |

CAN-1/2/8/9/10 already agreed. The reconciliation is documentation-only (no code
or `AB_ARM_MISSION_IDS` change), so the reproduced v0.1 headline (delta −0.75) is
unchanged.

## Toolchain caveat — `docker compose run` vs baked image

`docker-compose.yml` bind-mounts `.:/app`, which shadows the image's baked
`/app/.venv`; `uv run` then falls back to the dependency-less root
`pyproject.toml` and cannot find pytest/the deps. CI and these notes therefore
use `docker run --rm crystalium:dev <cmd>` against the **baked** image (see
`.github/workflows/ci.yml` rationale). `make bench`/`make test` use the compose
path for convention parity and share this caveat. **[GAP]** A future wave could
relocate the venv (e.g. `UV_PROJECT_ENVIRONMENT=/opt/venv`) so the compose path
works with live edits; out of scope for W1.

## W2 EVB ablation gate (v0.3.0) — INCONCLUSIVE, flag stays OFF

`docker run --rm crystalium:dev python -m evals ab --flag evb_enabled` over the
A/B arm set, evb_threshold=0.5:

| axis | on (evb) | off (legacy) | delta |
|---|---|---|---|
| pass_rate | 0.25 | 0.25 | 0.0 |
| promotion_precision | null | null | null |
| high_value_retention | null | null | null |

**Verdict: EVB does NOT beat legacy ⇒ `evb_enabled` stays OFF (default).** This is
an honest ablation-or-revert null, not a regression:

- **Both gate metrics are undefined (null)** on the current canary. The suite
  triggers **no promotions** (semantic auto-admit needs k independent witnesses;
  the missions don't commit corroborating witnesses → empty ledger →
  promotion_precision undefined) and produces **no high-EVB persisted crystals**
  in a single synchronous run (the full Dream prune write-back doesn't run inline;
  recall-persisted evb values stay below 0.5 for the few recalled crystals →
  high_value_retention undefined).
- **pass_rate is unchanged** (0.25 both arms): EVB reshapes eviction/composer
  *ordering*, not mission pass/fail on this suite — expected.

The EVB machinery (evb.py, persistence, routing, recompute-on-event,
instrumentation, axes) all ship **behind the OFF flag** and are fully tested. The
keystone is in place; the canary simply cannot yet *demonstrate* it beats legacy.

**[GAP — later wave]** Strengthen the canary to exercise continual-learning
dynamics that EVB targets: missions that (a) commit k corroborating witnesses so
promotions actually fire, (b) run a full Dream prune cycle so high-EVB survival
is observable, and (c) attach outcomes to recalled crystals. Only then can the
gate give EVB a fair vs-legacy comparison. Until then `evb_enabled` remains OFF.
