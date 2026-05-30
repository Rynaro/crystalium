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

**[GAP — addressed below]** Strengthen the canary to exercise continual-learning
dynamics that EVB targets: missions that (a) commit k corroborating witnesses so
promotions actually fire, (b) run a full Dream prune cycle so high-EVB survival
is observable, and (c) attach outcomes to recalled crystals.

## W2 EVB gate — strengthened workload (`evals/evb_gate.py`)

`docker run --rm crystalium:dev python -m evals evb-gate`. A deterministic
population (6 high-value, 6 distractor, 6 low-value) runs through a real Dream
prune in each arm; metrics use a **ground-truth value label (id prefix)** —
scorer-independent, so the A/B is fair in BOTH arms (it no longer keys on
persisted evb, which only the EVB arm has).

| axis | on (evb) | off (legacy) | delta |
|---|---|---|---|
| promotion_precision | 1.0 | 1.0 | 0.0 (tie) |
| high_value_retention | 1.0 | 1.0 | 0.0 (tie) |
| distractor_eviction *(diagnostic)* | 1.0 | 0.0 | **+1.0** |

**Verdict: EVB does NOT strictly beat legacy on the two DoD metrics ⇒
`evb_enabled` stays OFF.** Both scorers perfectly retain genuine high-value
memories and recall promoted ones, so the recall-oriented DoD metrics tie.

**Key finding (the real EVB effect):** the diagnostic shows EVB evicts **100% of
single-axis distractors** ("high need, low gain" — frequently accessed but
useless) while legacy keeps **0%**. EVB's multiplicative Gain×Need correctly
devalues junk the additive blend over-rewards. EVB's advantage is **precision**
(evicting low-value), which the DoD's **recall** metrics (high-value retention /
promotion precision) structurally cannot detect.

**Operator decision surfaced:** the W2 DoD named the wrong axes to detect EVB's
benefit. Options: (i) keep `evb_enabled` OFF under the strict literal DoD (ties →
no flip — the conservative default taken here); or (ii) adopt distractor-eviction
/ retention-precision as the W2 gate metric (a DoD refinement) and flip ON.
Flipping was NOT done unilaterally — redefining the gate to manufacture a win
would violate "never massage a metric". Also note EVB's product is systematically
lower-scaled than the additive sum, so eviction thresholds likely want
recalibration for the EVB scale (a W4 forgetting-faculty concern).

## W3 Dream-intelligence gate (`evals/dream_gate.py`) — INCONCLUSIVE, all flags OFF

`docker run --rm crystalium:dev python -m evals dream-gate`. A deterministic
labeled-fact population (3 fact groups with ≥k=3 independent witnesses + a
2-witness STC group, a FakeGraph linking each group) runs through the real
gather→consolidate→prune pipeline in two arm-pairs.

| Gate | axis | on | off | delta |
|---|---|---|---|---|
| (i) replay+interleave vs chrono | consolidation_gain | 1 | 1 | 0 |
| | semantic_drift | 0.0 | 0.0 | 0.0 |
| (ii) STC off vs on | useful_context_retention | 1.0 | 1.0 | 0.0 |
| | consolidation_gain | 1 | 1 | 0 |

**Verdict: no augment strictly beats baseline ⇒ `dream_replay_evb`,
`dream_interleave`, `dream_stc` all stay OFF (default).** Honest null, not a
regression — every augment ships behind its off flag, fully tested; the gate is
now *evaluable* (metrics defined, no longer null).

**Why the tie (root cause + [GAP] for a later wave):** v0.1 `_gather`
(`worker.py:252`) collapses seeds + graph-neighbours into a *single mixed
cluster*, so consolidation count is ~1 regardless of replay ordering; the mixed
cluster already carries ≥k witnesses, so STC's lowered `k_override` changes
nothing; summaries fit in the 512-char join so drift is 0; and nothing is pruned
so context retention is 1.0. The augments are correct and gated, but the coarse
single-cluster `_gather` can't *exercise* per-fact consolidation dynamics. A
later wave should refine `_gather` to emit **per-topic clusters** (so replay
ordering, interleave, and STC each have a measurable surface) and re-run this
gate. Until then all W3 flags remain OFF.

**Measurement note:** consolidation gain is measured as the *admitted-
consolidation count*, NOT semantic-row growth — the Dream **proposes** (the gate
admits + records a promotion) but `_consolidate` never inserts the semantic
crystal, so row-count growth is structurally 0.

## W4 forgetting-faculty gate (`evals/forgetting_gate.py`) — INCONCLUSIVE, flag OFF

`docker run --rm crystalium:dev python -m evals forgetting-gate`. A 24-tick
synthetic session in two arms (LRU vs FSRS) over a recalled high-value set + a
never-recalled noise stream.

| axis | on (fsrs) | off (lru) | delta |
|---|---|---|---|
| memory_size_plateau (lower=flatter) | 1.0 | 1.0 | 0.0 |
| high_value_retention | 1.0 | 1.0 | 0.0 |
| recall_latency_ms | ~0.0047 | ~0.0071 | −0.0024 |

**Verdict: FSRS does NOT strictly beat LRU on all three DoD axes ⇒
`forgetting_fsrs` stays OFF.** High-value retention ties at 1.0 (both keep the
recalled keystones) and FSRS latency is marginally lower, but **neither arm
plateaus** at default params (both grow ~+1 active/tick in the latter half) — so
the plateau axis ties and the gate fails. Honest null; the faculty (FSRS decay,
value-aware eviction, re-surfacing, protected class, RTBF) ships fully tested
behind the off flag.

**[GAP — the noisiest-wave tuning follow-up]** To separate the arms, a later pass
needs a **longer session** and/or **more aggressive eviction** (higher r_floor,
more frequent prune, larger noise:signal ratio) so FSRS's value-aware eviction
visibly plateaus memory while LRU grows. This is parameter/iteration tuning
(roadmap budgeted "several A/B iterations"), not a redesign. Until then the flag
stays OFF.

**Note:** the right-to-be-forgotten op is exercised by `test_rtbf.py`, not this
gate; it is an operator action, not an A/B axis.
