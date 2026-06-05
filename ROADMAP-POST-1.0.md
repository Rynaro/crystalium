# CRYSTALIUM — Post-1.0 Technical Roadmap (gap ledger)

Every gap surfaced across waves W1–W8, with root cause, proposed approach, and an
explicit exit criterion. Sourced from `evals/BENCH-NOTES.md`, `DESIGN-RATIONALE.md`
(§D6.7 ablation table), and the per-wave `[GAP]`/`[PROXY]` markers. Ordered by tier:
**T1 = correctness** (affects real behavior), **T2 = earn the OFF flags** (re-run the
six inconclusive/confounded ablations on discriminating fixtures), **T3 = integration
& hardening completeness**, **T4 = ops/CI hygiene**.

The standing discipline holds: neuroscience-as-hypothesis, **ablation-as-arbiter**; a
flag flips ON only on a confound-free A/B win; nulls stay OFF and documented. Nothing
here is shipped green by lowering a bar.

---

## T1 — Correctness (do first; these are real behavior gaps)

### G1.1 — Recall-after-bi-temporal-update re-index gap  ★ blocks the canary 0.80
- **Symptom:** after `update`, the new revision is committed but not resurfaced by
  recall (CAN-4 fails; memory-on canary lands at 0.75 instead of clearing 0.80).
- **Root cause:** `_handle_update` (episodic/procedural/execution path) inserts the new
  revision via `relational.insert_crystal` directly and **does not re-embed it into the
  vector store**, so it's absent from the dense recall arm (and the superseded original
  is correctly excluded by `recall_active_only`). BM25 alone under-ranks it.
- **Approach:** route the update's new-revision insert through the same embed+upsert the
  layer `commit` uses (or add a `vector_store.upsert` call in `_handle_update`). Keep
  bi-temporal supersession unchanged.
- **Exit:** CAN-4 passes on-arm; canary memory-on ≥ 0.80 AND beats off; a regression
  test asserts an updated crystal is dense-recallable.
- **RESOLVED (T1 pass) — diagnosis corrected.** The production re-index gap was
  already closed: `_handle_update`'s fallback path re-embeds (and `episodic.update`
  upserts), so on a **clean store** the canary scores `pass_rate_on=1.0` (CAN-4
  passes, beats off `0.0`) on **both `main` and the fix branch**. The `0.25`
  observed under the canary was a **test-harness confound, not a production gap**:
  `_build_live_handlers` shared the persistent `~/.crystalium/default` store across
  runs, so cross-run `write_dedup_merge` polluted the scope filter. Fix shipped:
  (a) `semantic.update()` now re-embeds too (a genuine *layer-completeness* gap —
  the semantic direct-update path lacked the upsert the others had); (b) the canary
  now isolates each run in a fresh ephemeral `data_dir`, making `make bench`
  deterministic without a manual volume wipe. Regression tests:
  `test_semantic_update_reembeds_new_revision`, `test_canary_run_uses_fresh_ephemeral_data_dir`.
  *Latent follow-up:* `procedural.update()` / `execution.update()` layer methods
  still lack the upsert (not exercised by the canary, which routes through
  `_handle_update`'s fallback) — close for full layer symmetry.

### G1.2 — `bm25_search` passes raw queries to FTS5 (syntax injection / crash)
- **Symptom:** a recall query containing `:`/`-`/`*` etc. raises `OperationalError: no
  such column` (hit during the W7 round-trip; worked around by sanitizing test queries).
- **Root cause:** the query string is handed to FTS5 `MATCH` verbatim; FTS5 treats
  several characters as query syntax.
- **Approach:** sanitize/quote the query for FTS5 (wrap bare terms as phrases or strip
  operators) before `MATCH`; this is also a minor injection-surface hardening.
- **Exit:** a recall with arbitrary punctuation returns results (or empty) without error;
  a fuzz test over special-char queries passes.

### G1.3 — `tool_calls` audit table is DDL-only / unpopulated
- **Symptom:** `record_tool_call` has zero callers; `DreamWorker._orient` queries a table
  nothing writes; the live audit substrate is the crystal/forget/promotion ledgers only.
- **Approach:** either wire `enforcement.record` → `record_tool_call` (a queryable audit
  trail, useful for the drift detector and ops) **or** drop the dead table + the
  `_orient` query. Decide based on whether a queryable call-audit is wanted at 1.x.
- **Exit:** no DDL table without a writer; `_orient` reads only populated tables.

---

## T2 — Earn the OFF flags (re-run the six inconclusive/confounded ablations)

Each augment ships fully tested behind an OFF flag; the synthetic v0.1 fixtures could
not discriminate it. Each needs a **discriminating workload** + a re-run; flip ON only
on a confound-free win.

### G2.1 — EVB (`evb_enabled`, W2) — INCONCLUSIVE
- **Gap:** the canary set ties EVB vs recency; EVB's value-ordering never gets a case
  where high-value-but-old beats low-value-but-recent.
- **Workload:** a session with explicit high-utility-old vs low-utility-new contention +
  a budgeted eviction; measure high-value retention under pressure.

### G2.2 — Dream replay / interleave / STC (`dream_*`, W3) — INCONCLUSIVE
- **Gap:** no arm beats baseline; the fixture lacks genuine catastrophic-forgetting
  pressure.
- **Workload:** interleaved multi-task stream with measured backward-transfer / forgetting
  (SWE-Bench-CL-style axes already exist in `evals/metrics.py`).

### G2.3 — FSRS forgetting (`forgetting_fsrs`, W4) — INCONCLUSIVE
- **Gap:** neither LRU nor FSRS plateaus at default params over 24 ticks.
- **Workload:** longer sessions + higher noise:signal + more aggressive eviction
  (higher `r_floor`, frequent prune) so FSRS's value-aware eviction visibly plateaus
  memory while LRU grows.

### G2.4 — Pattern completion + context-match (`recall_completion`/`recall_context_match`, W5) — INCONCLUSIVE
- **Gap:** the 7-crystal fixture + k=10 means dense recall already returns everything —
  no similarity-missed-but-graph-reachable gap to fill.
- **Workload:** a large corpus where `k ≪ |relevant|` and some relevants are reachable
  only via graph edges; measure multi-hop F1 lift over flat RRF.

### G2.5 — Predictive prefetch (`recall_prefetch`, W5) — PASS-BUT-CONFOUNDED
- **Gap:** the gate fed the checkpoint the *exact verbatim* future query (fabricated
  perfect prediction); p95 win is checkpoint-prepaid cost.
- **Workload:** an **imperfect predictor** — next query drawn from a realistic
  distribution, not handed over — so the cache hit-rate reflects real protention.

### Exit for all T2
A discriminating, confound-free gate per flag in `evals/`; flip ON only those that
genuinely beat the prior version; update `DESIGN-RATIONALE.md` §D6.7 + BENCH-NOTES.

---

## T3 — Integration & security hardening completeness

### G3.1 — Belief-drift contradiction detection (`drift_detect`, W6) — OFF
- **Gap:** cosine similarity is a `[PROXY]` for contradiction — a genuine contradiction
  ("backups nightly" vs "backups disabled") scored 0.696, **below** the 0.80 band floor;
  contradictions read LOW-similarity, paraphrases ~0.95.
- **Approach:** add a contradiction signal beyond cosine — a small NLI/entailment model
  or a negation/antonym lexical check — and a contradiction-labeled bench; or expose the
  band as clearly operator-tuned with documented precision/recall.
- **Exit:** drift catches a labeled contradiction set at a stated precision; band/method
  documented; flip ON only if it adds value without flooding false positives.

### G3.2 — Write-conflict trust-aware resolution (`write_conflict_detect`, W6) — OFF
- **Gap:** pure last-write-wins lets a **less-trusted newcomer supersede a higher-trust
  prior** (a trust inversion — currently only recorded in the conflicts ledger's tiers).
  The gate also doesn't isolate a conflict-specific ASR win.
- **Approach:** trust-aware policy (don't let a lower-trust write supersede a higher-trust
  prior; surface for review instead) + a conflict-isolating ASR gate.
- **Exit:** no trust inversion possible under the ON policy; an isolating gate shows the
  win; flip ON on a confound-free result.

### G3.3 — Project-scoped dedup/conflict detection (W5/W6)
- **Gap:** `_dedup_target` / `_conflict_target` filter by **layer only, not project**, so
  cross-project near-duplicates can collide/merge.
- **Approach:** add a project scope filter to the dense-search dedup/conflict probes.
- **Exit:** a cross-project near-dup does not merge/conflict; test covers it.

### G3.4 — ECL v2.0 schema + per-schema validation (W7)
- **Gap:** vendored envelopes are v1.0; the v2.0 envelope schema is not vendored; the
  generic ingest mapping validates the crystal.v1 *output* but not the artifact's *inner*
  native schema.
- **Approach:** vendor `eidolons-ecl/schemas/envelope.v2.json`; add an optional
  per-`kind` validator (the native payload is already preserved verbatim in
  `encoding_context` for re-validation).
- **Exit:** v2.0 envelopes validate against the vendored schema; per-kind validation is
  opt-in and tested for the kinds that have local schemas.

### G3.5 — Missing roster artifact schemas (W7)
- **Gap:** `spec-bundle.v1`, `verdict.v1`, `document-bundle.v1`, OPUS
  `mission.v1`/`composition.v1`/`run-report.v1` are named by the roadmap but absent
  locally; they ride the generic ingest path as kind-aliases.
- **Approach:** pull canonical schemas from the source repos as they publish, or
  synthesize + contribute them upstream; then wire per-kind adapters.
- **Exit:** each named kind has a vendored schema + a round-trip fixture.

### G3.6 — Canonical trust→`provenance.source` mapping (W7)
- **Gap:** the tier→source table is a documented `[PROXY]`, not a canonical mapping.
- **Approach:** align with the nexus/EIIS canonical mapping once published; replace the
  local table.
- **Exit:** the mapping references a single canonical source; the `[PROXY]` marker is
  removed.

---

## T4 — Ops / CI / SLO hygiene

### G4.1 — Recall p95 SLO vs embedder latency
- **Gap:** recall p95 ~205ms is **embedder-bound** (one bge-m3 forward pass on CPU),
  marginally over the 200ms target; the target was set before measuring.
- **Approach:** either (a) reconcile the target to the embedder reality (e.g., split
  "cold dense" vs "BM25-only" p95, or set 250ms with the embedder caveat), or (b) cache
  query embeddings / offer a lighter embedder; record availability from production, not
  just the synthetic harness.
- **Exit:** a target that reflects the deployed embedder, met with headroom; availability
  recorded from real traffic.

### G4.2 — Make the legacy conformance jobs blocking
- **Gap:** `conformance.yml` (`eiis-1-4`, `ecl-2-0`) are still `continue-on-error: true`
  (advisory), pending the published `eidolons-eiis`/`eidolons-ecl` checkers. (W8 added a
  *blocking* `conformance` job in `ci.yml`; the EIIS/ECL jobs remain advisory.)
- **Approach:** flip `continue-on-error: false` once the external checkers publish + the
  manifest/envelope validate against them.
- **Exit:** EIIS + ECL conformance are blocking gates.

### G4.3 — Root `EIDOLONS.md` referenced but absent
- **Gap:** `CLAUDE.md` points to `./EIDOLONS.md`; the real file is at
  `.eidolons/cortex/EIDOLONS.md`.
- **Approach:** add a root pointer/symlink or correct the reference.
- **Exit:** the referenced path resolves.

---

## Sequencing

1. **T1 first** (G1.1 unblocks the canary 0.80; G1.2/G1.3 are small correctness wins).
2. **T2** as a dedicated "earn the flags" pass — better fixtures, honest re-runs; expect
   some to stay OFF (that's the arbiter working).
3. **T3** alongside the next roster integration milestone (the external schemas/mappings
   land as the nexus publishes them).
4. **T4** is continuous hygiene; G4.2 gates on upstream checker availability.

None of this is required for the v1.0.0 freeze — 1.0 ships with these documented. They
are the honest backlog the ablation-as-arbiter discipline produced.
