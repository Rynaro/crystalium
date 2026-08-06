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

**T2 re-examination (2026-06-04) — confirmed OFF, two paths identified.** Re-ran:
the baseline still ties exactly (consolidation_gain 1/1, drift 0/0, STC retention
1.0/1.0). Two routes to a discriminating Dream gate, BOTH beyond a fixture tweak:
(1) the `_gather` per-topic-cluster refinement below (a **production** Dream-worker
change); and (2) the ledger's preferred **interleaved-multi-task backward-transfer**
harness — the `forgetting()`/`backward_transfer()`/`forward_transfer()` R-matrix
functions already exist in `evals/metrics.py:45-72`, but no workload drives them
(it needs an A→B→A interference stream measuring whether Dream replay/interleave
reduces catastrophic forgetting of task A). Both are substantial builds with
genuinely uncertain outcomes (the retention dynamics resemble the W4/FSRS null —
the prune may not evict task A regardless of replay). Per ablation-as-arbiter we do
NOT manufacture a consolidation-count win on the coarse single-cluster fixture; the
W3 flags stay OFF until one of those harnesses is built and shows a confound-free
win. This is the deliberate honest stopping point, not a skip.

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

**[GAP CLOSED — follow-up run, still a null] (T2, 2026-06-04)** The longer/aggressive
pass was done: 60 ticks, 4 noise/tick, prune EVERY tick, and the keystone recalled
only every 8th tick (the value×recency discriminator — between accesses it "ages",
so a pure-recency LRU should drop it while FSRS's spaced-repetition stability keeps
it). Noise summaries are now seeded by index (not uuid), so the memory axes are
reproducible. Result — **FSRS still does NOT beat LRU:**

| axis | on (fsrs) | off (lru) | delta |
|---|---|---|---|
| memory_size_plateau (lower=flatter) | 1.379 | 1.379 | 0.0 (identical) |
| high_value_retention | 1.0 | 1.0 | 0.0 (both keep the keystone) |

Both arms **retain the keystone even at an 8-tick recall gap** — LRU's accumulated
access frequency keeps it above the prune threshold, so the value×recency contention
the ledger hoped for never bites — and the plateau is identical. The gate now requires
a **meaningful (≥10%) plateau margin** (no production default should flip on a
~2% synthetic, sign-unstable difference). **`forgetting_fsrs` stays OFF — an honest
null confirmed, not for lack of trying.** A win would require params engineered to
make LRU drop the keystone, which would be manufacturing the result. Guard tests:
`test_forgetting_gate.py`.

**Note:** the right-to-be-forgotten op is exercised by `test_rtbf.py`, not this
gate; it is an operator action, not an A/B axis.

## W5 retrieval-faculty gates (`retrieval-gate` / `dedup-gate` / `prefetch-gate`) — MIXED

`docker run --rm crystalium:dev python -m evals {retrieval-gate,dedup-gate,prefetch-gate}`
(real bge-m3 embedder; kuzu graph). Three independent two-arm ablations, one per
DoD axis. Result (updated T2): **two clean wins (dedup + completion, both ON),
three honest nulls (context_match no rank lift; prefetch cache-confounded; FSRS no
margin) stay OFF.**

### (i) completion EARNED ON; context_match stays OFF (T2, 2026-06-04)

The **[GAP]** below is closed: the fixture now adds **24 lexically-close distractors**
(share query words, NOT relevant, NOT graph-linked) so flat dense recall fills up
*without* the graph-distant spokes — creating the "missed-by-similarity but
reachable-by-graph" gap the faculty targets.

| axis | flat | completion | both |
|---|---|---|---|
| multihop_f1 | 0.12 | **0.18** | 0.18 |
| multihop_recall | 0.67 | **1.00** | 1.00 |
| context_rank (lower=better) | 1 | — | 1 (context arm: 1) |

**Verdict: completion LIFTS multi-hop recall/F1 ⇒ flip `recall_completion` ON; context
stays OFF.** Flat dense recall misses the 2-hop spoke (lexically distant, ranked below
the distractors → recall 0.67); the decaying multi-hop walk recovers it → recall 1.0,
F1 0.12→0.18. A genuine graph-reachability win. **`recall_context_match` shows no rank
lift (the context-matching crystal already ranks 1 in both arms) → stays OFF (honest
null on that faculty).** `recall_completion` default flipped ON; full suite green with
the flip (661 passed) — the graph walk runs on every recall without breakage. Guard
tests: `test_retrieval_gate.py`.

**crystalium#36 update (v1.9.0, 2026-08-02) — numbers shift, verdicts hold.**
`recall_relevance_primary` (default `true`) makes `k` a real cap and query
relevance the primary composition signal, mechanically shrinking the
`retrieval-gate` result set from "the whole filtered RRF list" to a genuine
top-`k=10`. Predicted in spec.md §Test Plan ("denominator ~30 → 10") and
confirmed by a live before/after run (af24493 vs this branch,
`python -m evals retrieval-gate`; full JSON in the ESL change dir
`crystalium-recall-starvation-36/eval-{before,after}.json`):

| axis | before (af24493) | after (v1.9.0) |
|---|---|---|
| multihop_f1.flat | 0.121 | 0.308 |
| multihop_f1.completion | 0.176 | 0.462 |
| context_rank.flat / .context | 2 / 2 | 2 / 2 |
| context_rank.both | 4 | **4 or 5 (run-varying — see note)** |
| `completion_pass` | **true** | **true** |
| `context_pass` | **false** | **false** |
| `gate_pass` | **true** | **true** |

**`context_rank.both` is run-varying, not a fixed 4** (vigil's independent
re-run measured 5, `verification.md` F-V6; two further maker re-runs on this
same code both measured 4). `flat`/`context`/`completion_pass`/`context_pass`/
`gate_pass` reproduce exactly across every run; only the `both`-arm rank
(completion AND context_match both on) fluctuates by one position — plausibly
real-embedder or tie-break non-determinism in that specific combined arm. It
feeds no pass predicate (`completion_ok` compares `comp` vs `flat`;
`context_ok` compares `ctx` vs `flat`; neither reads `both`), so the verdict
is unaffected either way — recorded here as run-varying rather than a single
number so a future re-run isn't mistaken for drift.

**crystalium#38 update (v1.10.0, 2026-08-03) — the F-V6 figure now has a named
mechanism, and it is retired only PARTIALLY.** #38's P3 investigation found
TWO separate causes behind the graph/completion arms' non-reproducibility,
and this release fixes only one of them:

1. **Consumer-side ordering (FIXED this release).** `neighbor_expand` returns
   a `set[str]` and `decaying_walk` scores a set comprehension; both were
   iterated in per-process hash-randomised order before being fused.
   `retrieve.py` now applies `sorted()` at both consumption points
   (deterministic, outside the `recall_weighted_fusion` flag). Measured
   directly (crystalium#38 `red-evidence.txt`): fixed fused order is
   byte-identical across `PYTHONHASHSEED` 0-4 on a >=4-distinct-UUID derived
   arm fixture.
2. **Store-side membership (OPEN, follow-up issue, NOT fixed here).**
   `GraphStore.neighbor_expand` (`storage/graph.py`, out of scope for #38)
   wraps its whole seed-loop in one `try`, and the underlying Kuzu driver
   RAISES at cursor exhaustion instead of returning `None` — so only the
   FIRST seed's neighbourhood is ever actually explored
   (`neighbor_expand(seeds) == neighbor_expand([seeds[0]])`). This is a
   **membership**, not merely ordering, nondeterminism that (1)'s `sorted()`
   fix cannot reach — WHICH seed happens to be tried first (and therefore
   which neighbours are found at all) still varies with the graph
   walk's frontier construction.

**Consequence for `context_rank.both`: it remains run-varying AFTER v1.10.0,
for the SECOND (still-open) reason above, not the first.** Re-measured on
the fusion-gate work (crystalium#38 measurement, real `GraphStore`):
`context_rank.both` observed values now include `{2, 4, 5}` (widening the
previously-recorded `{4, 5}` set), including a `4 -> 2` variation across two
runs at the *same* (unset) `PYTHONHASHSEED` — because crystal ids are
`uuid4`-fresh per run, so even a fixed hash seed does not pin the graph
walk's outcome. **A future reader must not treat a stable figure here as
evidence the fix is complete** — only the *ordering* half is; the
*membership* half is tracked as follow-up **F-A = #41** (deliberation.md §7,
opened before this change's tag per C-13) and will be re-annotated when it
lands.

**A THIRD mechanism bears on every number in this section, and it is a
confound in the FIXTURE, not a nondeterminism at all (crystalium#38
deliberation.md anomaly C / C-11, vigil F-V3 remediation).** This gate's own
docstring claims "Edges are seeded in EVERY arm, so the only variable is
whether the recall walk / re-rank runs — isolating the faculty, not the
fixture." That claim is FALSE, confirmed by measuring the actual edge
counts: **flat 2, context 2, completion 142, both 142.** Two causes:

1. `server.py:522,535` sets `link_cooccurrence = config.recall_completion`,
   so the flag under test ALSO changes the graph at commit time — the
   "completion vs flat" comparison is not an ablation of one faculty, it is
   a comparison between two DIFFERENT corpora (2 co-occurrence edges vs 142).
2. `recent_crystal_ids` (`relational.py`) does `ORDER BY created_at DESC
   LIMIT 5`, and this fixture stamps every crystal with the identical `_T0`
   — so "the 5 most recent" resolves, by tie order, to the 5
   **first-committed** crystals rather than a genuinely-recent window. The
   measured edge-target histogram is `{spoke1: 30, hub: 30, spoke2: 29,
   noise1: 27, noise2: 26}` — both ground-truth spokes are direct
   co-occurrence neighbours of nearly every other crystal in the corpus, so
   the completion arm's F1 lift is substantially an artifact of
   `created_at`-tie co-occurrence edges, not of the seeded 2-hop chain this
   gate is nominally testing.

**What this does and does not invalidate.** It does NOT invalidate AC-124's
reading of this gate as a non-inferiority tripwire (crystalium#38 C-5): the
confound is a property of the fixture, and the fixture (`evals/
retrieval_gate.py`, correctly OUT of #38's declared scope, C-1) is held
byte-identical between the before/after capture, so "the fusion change did
not lower `multihop_f1.completion`, on the identical (confounded) fixture"
remains a valid, narrow claim. It DOES invalidate any broader claim that
this gate isolates the completion faculty, that AC-124 shows multi-hop
retrieval quality improved in general, or that a green AC-124 shows the
derived-family merge preserves multi-hop *chains* specifically — none of
those claims are made in this file or in CHANGELOG `[1.10.0]`, and this
paragraph exists so a future reader does not manufacture one. Follow-up
**F-C = #43** (opened alongside F-A=#41 / F-B=#42 / F-D=#44 / D-1=#45 before
the crystalium#38 tag) owns
the actual fix: distinct `created_at` stamps per fixture crystal, edge
seeding decoupled from the arm under test, and the docstring corrected
either way. Until #43 lands, this gate is valid **only** as the
non-inferiority tripwire described above — severity is medium-high because
this is the gate that guards every retrieval-affecting change in the repo,
and it currently does not mean what its own docstring says.

Both F1 numbers roughly double (denominator shrinks from ~31 candidates to a
real `k=10`), exactly as predicted — this is precision rising mechanically,
not a faculty change. `context_rank.flat`/`.context` are unchanged (rank-
based, not count-based, so the k-cap doesn't move them). **No verdict
flipped** — per C-6
this is the check that matters; the absolute F1/recall numbers are expected to
drift with every future retrieval-relevant change and are not, by themselves,
a regression signal. `evb_gate`, `forgetting_gate`, `prefetch_gate` and
`dream_gate` were also re-run (C-6, uniform-importance-baseline-shift check);
all pass, unaffected — none of those four gates route through
`Aetheryte.recall()`'s composed record set in a way their DoD axes depend on
(forgetting_gate calls `recall()` but only measures latency; the others never
call it, and Dream's prune always recomputes importance fresh rather than
reading the stored `utility.importance` value D4 changed).

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

## crystalium#55 item 3 — weight-sensitivity sweeps: which gate is informative

**Pointer, not a restatement.** For any future `fusion_weight_derived` sweep: the
**retrieval gate** is the informative axis; the **FUSION gate cannot express weight
sensitivity** (it collapses to a target/Z comparison at k=2, degenerate across the
whole sub-1.0 band). The canonical statement — plus the C-9 fence and the sub-1.0
band's reopen condition — lives in `mcp-server/src/crystalium/config.py`, in the
`fusion_weight_derived` field's comment block. Read it there; this line exists only
so a sweep author who starts in `evals/` finds it.
