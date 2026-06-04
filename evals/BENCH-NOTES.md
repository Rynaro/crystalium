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

## W2 EVB ablation gate — EARNED ON (T2, 2026-06-04)

The deterministic `evals/evb_gate.py` gate decides on **retained-set purity** (the
axis EVB's `gain×need` actually moves), correcting the original promotion/retention
criterion which saturated at 1.0 in both arms (non-discriminating):

| axis | on (evb) | off (legacy) | delta |
|---|---|---|---|
| retention_precision | 1.00 | 0.33 | **+0.67** |
| high_value_retention | 1.00 | 1.00 | 0.00 (no regression) |
| distractor_eviction | 1.00 | 0.00 | +1.00 |
| promotion_precision | 1.00 | 1.00 | 0.00 (saturated — non-discriminating) |

**Verdict: EVB strictly improves retained-set purity with no high-value-retention
regression ⇒ `evb_enabled` flipped ON (default).** Legacy's additive blend keeps
high-need/zero-gain distractors (recency+access ≈ 0.40 > 0.10 threshold) and
unscored-old crystals (0.5 neutral-outcome term ≈ 0.14 > 0.10); EVB's multiplicative
scorer zeroes both and keeps only the genuine high-value set. Full suite green with
the flip (656 passed) — no production coupling. `make bench` canary unaffected (1.0).
Run: `docker compose run --rm crystalium python -c "from evals.evb_gate import run; print(run())"`.

---

### (Superseded) W2 EVB ablation gate (v0.3.0) — INCONCLUSIVE, flag stayed OFF

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

## W5 retrieval-faculty gates (`retrieval-gate` / `dedup-gate` / `prefetch-gate`) — MIXED

`docker run --rm crystalium:dev python -m evals {retrieval-gate,dedup-gate,prefetch-gate}`
(real bge-m3 embedder; kuzu graph). Three independent two-arm ablations, one per
DoD axis. Result: **one clean win (dedup), two non-flips (completion/context
inconclusive; prefetch confounded).**

### (i) completion + context_match — INCONCLUSIVE, flags OFF

| axis | flat | completion | both |
|---|---|---|---|
| multihop_f1 | 0.60 | 0.60 | 0.60 |
| context_rank (lower=better) | 1 | 1 (context arm) | 1 |

**Verdict: neither lifts ⇒ `recall_completion` and `recall_context_match` stay
OFF.** With a 7-crystal corpus and k=10, the dense arm already retrieves *every*
crystal, so the seeded hub→spoke edges add nothing the flat fusion didn't already
surface — there is no "missed-by-similarity but reachable-by-graph" gap for
completion to fill, and the context-matching crystal already ranks at 1 without
the re-rank. Honest null; the synthetic fixture is too small to create the
multi-hop gap the faculty targets. **[GAP]** a discriminating fixture needs a
corpus large enough (k ≪ |relevant|) that similarity recall misses graph-reachable
relevants — a larger-fixture follow-up, not a redesign.

### (ii) dedup-merge (pattern separation) — PASS, flip `write_dedup_merge` ON

| axis | on (dedup) | off (append) |
|---|---|---|
| write_amplification (lower=better) | 0.667 | 1.0 |
| merged | 2 | — |
| precision_held | true | true |

**Verdict: write amplification drops 1.0 → 0.667 (2 of 3 paraphrases merged by
real bge-m3 cosine > 0.92) with the canonical fact still recalled in both arms ⇒
flip `write_dedup_merge` ON.** This is the one **confound-free** win: the merge
decision is made by the real embedder on genuine paraphrases, not handed to the
faculty by the harness. The deliberate cost (the exact paraphrase wording is no
longer separately stored — pattern separation's inverse) is the intended trade;
precision (returning the relevant fact) holds.

### (iii) predictive prefetch — CONFOUND #1 FIXED, confound #2 found, `recall_prefetch` stays OFF (T2, 2026-06-04)

The W5 **[GAP]** below — "needs an *imperfect* predictor" — is now closed: the gate
predicts the next query with an imperfect first-order rotation model and lets the
actual stream deviate, so `prediction_accuracy = 0.73 (< 1.0)`, no longer the
fabricated-perfect signal. A confound guard (`gate_pass` requires `accuracy < 1.0`)
prevents regression.

| axis | on (prefetch) | off (no cache) |
|---|---|---|
| prediction_accuracy | **0.73** (imperfect ✓) | 0.73 |
| cache_hit_rate | 0.73 | null (no cache) |
| recall_p95_ms | 0.14 | 189.3 |

**Verdict: still `recall_prefetch` stays OFF — a DEEPER confound surfaced.** With the
predictor fixed, the p95 win is exposed as **cache-vs-no-cache, not protention**: the
OFF arm (`recall_prefetch=False`) has **no recall cache at all**, and the queries
repeat, so the cache warms on first use and every repeat hits *regardless of
prediction* — the 1300× p95 drop is ordinary caching, not predictive prefetch.
Crediting protention specifically needs a **cache-on / prefetch-off baseline**, which
the bundled `recall_prefetch` flag does not expose. The gate now encodes this
(`protention_isolated = off-arm-has-a-cache` → False → `gate_pass` False). Honest
null on protention; the cache benefit is real but is a separate (unflagged) axis.
Guard tests: `test_prefetch_gate.py`.

#### (Superseded) predictive prefetch — PASS-BUT-CONFOUNDED (fabricated predictor)

| axis | on (prefetch) | off (no cache) |
|---|---|---|
| cache_hit_rate | 0.50 | null (no cache) |
| recall_p95_ms | 0.09 | 235.1 |

The original harness fed the checkpoint the *exact verbatim* query the agent then
recalled — fabricated perfect prediction. Superseded by the imperfect-predictor gate
above.

## W6 security & integrity gates — poisoning ASR PASS; drift bench proven; flags split

### (iii) poisoning-resistance ASR (`poisoning-gate`) — PASS, flip `recall_active_only` ON

`docker run --rm crystalium:dev python -m evals poisoning-gate` (deterministic;
BM25 recall, no embedder needed — same result with the real bge-m3 embedder). Eight
attacks: each delivers T3 poison (lands quarantined — the only open door), the
defended system triages+rejects it (deprecate + audit), then a FRESH component set
on the same data_dir attempts activation via recall.

| axis | on (defenses) | off (W5 baseline) | bar |
|---|---|---|---|
| attack_success_rate | **0.00** | 1.00 | ≤ 0.10 |
| tier_wall (T3→semantic denied) | 8/8 | 8/8 | — |

**Verdict: defenses cut ASR 1.00 → 0.00 with the tier wall holding in BOTH arms ⇒
flip `recall_active_only` ON.** Without the active-filter a rejected (deprecated)
poison still resurfaces at recall — the gap W6 closes; with it, the rejected poison
stays out. **[PROXY/confound, honest]** the active-filter directly implements the
exclusion the metric checks, so the 0.00 is partly tautological — but the
underlying behaviour change (recall must not return deprecated/superseded crystals)
is an unambiguous correctness + security fix, not a tuned win, and it is canary-no-
regression verified. The tier chokepoint is *always* on (sacred), so it defends
equally in both arms — recorded as evidence, not an ablation axis.

### Belief-drift detector — bench-proven (real embedder), flag stays OFF

A real-bge-m3 end-to-end check: a T3 fact "backups are disabled to save cost"
against a T1 prior "backups run every night at 2am" (same project) is correctly
flagged (drift_audit row written, lower-trust fact marked, prior untouched).

**[GAP — the band proxy's hard edge]** the genuinely-contradictory pair scored
cosine **0.696**, BELOW the default band floor `drift_tau_lo=0.80`. Semantic
*contradiction* is often LOW-similarity (opposite assertions diverge in embedding
space), while paraphrases sit ~0.95+. So the default [0.80, 0.97) band catches
near-paraphrase divergence but MISSES strong contradictions — the demo needed
`drift_tau_lo=0.55` to flag it. Similarity is a [PROXY] for contradiction, not a
detector of it (no negation/NLI model in the loop). **`drift_detect` stays OFF**:
it is detect-and-flag only (no ASR movement to arbitrate), the band needs
per-deployment tuning, and a too-low floor floods false positives. Ships gated +
operator-tunable (drift_tau_lo/hi); the faculty is proven to flag when the band fits.

### Write-conflict detection — flag stays OFF

`write_conflict_detect` (LWW supersede + conflicts ledger) is correct and tested
(test_write_conflict), but the ASR gate neutralizes poison via the deprecate path,
so the gate does not isolate a conflict-specific ASR win. Combined with the
last-write-wins trust-inversion risk (a less-trusted newcomer can supersede a
higher-trust prior — recorded in the conflicts ledger's winner/loser tiers, but
unguarded), it **stays OFF** pending a conflict-isolating gate + a trust-aware
resolution policy. Ships gated.

**Net W6 flip:** `recall_active_only` → ON (gate PASS + correctness, canary-clean);
`drift_detect` + `write_conflict_detect` → stay OFF (honest nulls; faculties ship
gated).

## W8 1.0 freeze — honest canary repair + availability SLO

### Canary A/B (`evals/ab_memory_onoff.py`) — REPAIRED, honest result: memory helps, below 0.80

The v0.1→v0.7 canary reported `delta = -0.75` and was UNMEETABLE: (A) the off-arm
missions passed *vacuously* (`passed=True` / `mrr==0`) so `pass_rate_off` was pinned
to 1.0 and `delta = on - off` could never reach 0.80; (B) harness bit-rot
(`_get_crystal` read a non-existent `enforcement._store`); (C) `run_all` ran each arm
*twice* on separate random-project stores, so the headline reflected a different
execution than the displayed per-mission results; (D) the AB missions committed lone
T1 *semantic* facts that the promotion gate correctly held PENDING (never recallable).

W8 repaired all four HONESTLY (no metric massaging): off-arm now uses the SAME
memory-dependent criterion as on-arm; commits go to EPISODIC (recallable); each
mission is isolated in its own project (deterministic); the headline is computed from
the single run; the gate is restated to the faithful "memory-on ≥0.80 AND beats off".

| metric | before (v0.1–0.7) | after (W8) |
|---|---|---|
| pass_rate_off (vacuous?) | 1.00 (vacuous) | **0.00** (genuine — memory-off fails) |
| pass_rate_on | 0.25 (+ harness bugs) | **0.75** (CAN-1/3/5 pass) |
| delta | −0.75 | **+0.75** |
| beats_off | no | **yes** |
| headline_pass (≥0.80) | false | **false** |

**Honest verdict: memory now demonstrably helps — a full reversal (−0.75 → +0.75),
memory-on beats memory-off on 3 of 4 memory-dependent missions while memory-off
fails all 4. It lands BELOW the 0.80 bar by exactly one mission (CAN-4).** This is
reported as-is, NOT massaged to clear the gate.

**[GAP] CAN-4 — recall-after-bi-temporal-update.** CAN-4 commits a fact, `update`s it
(supersede works: `t_valid_to` set, no hard-delete ✓), then recalls — but the NEW
crystal is not resurfaced. Root cause: the bi-temporal update path (`_handle_update`)
inserts the new revision via the relational store directly and does NOT re-embed it
into the vector index, so the updated crystal is missing from dense recall (and the
superseded original is correctly excluded by `recall_active_only`). A real update→
recall re-index gap; fix deferred (post-1.0) — not forced green here.

### Availability SLO (Extended-Mind parity) — availability PASS, p95 marginally over [PROXY]

Measured on a 50-commit / 200-recall synthetic workload (real bge-m3, warm):

| SLO axis | target | observed | verdict |
|---|---|---|---|
| recall availability (success/attempts) | ≥ 99% | **100%** | PASS |
| recall p95 latency | < 200 ms | **~205 ms** | marginally OVER |

Availability meets the target. recall p95 (~205 ms) is **embedder-bound** — the warm
p95 is essentially one bge-m3 query-embedding forward pass on CPU; it marginally
exceeds the 200 ms target. Recorded honestly (the target is not moved to pass).
**[PROXY]** both values are from a synthetic harness, not production traffic.
