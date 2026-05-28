# CRYSTALIUM Canary Missions

10 missions exercising the P0 memory invariants. Each has an A/B arm flag
indicating whether it participates in the headline memory-on/off metric.

---

## CAN-1: recall_hit_across_sessions

**Scenario:** Plant a fact (e.g. "auth uses bcrypt") in session A via
`crystalium.commit(layer="semantic")`. Start a fresh session B (new store
instance with same project scope). Call `crystalium.recall(scope, query="auth
hash")`.

**Oracle:** Memory-off arm returns no relevant records (MRR@3 == 0). Memory-on
arm surfaces the committed crystal in top-3 (MRR@3 > 0).

**Pass criterion:** `memory_on.mrr_at_3 > 0 AND memory_off.mrr_at_3 == 0`

**A/B arm:** YES — headline metric participant.

---

## CAN-2: promote_gate_T3_blocked

**Scenario:** A caller with `trust_tier=T3` (environment/tool-ingested) calls
`crystalium.commit(layer="semantic", ...)`.

**Oracle:** `TierViolation` raised; no row inserted in the semantic store.
Semantic row count before == semantic row count after.

**Pass criterion:** Exception with `reason_code="TIER_VIOLATION"` raised. Store
unchanged.

**A/B arm:** NO — enforcement-only test, memory-on/off delta undefined.

---

## CAN-3: poisoning_resistance_T3_episodic_only

**Scenario:** T3 caller attempts commits to all non-Episodic layers (Semantic,
Procedural, Execution). All attempts must fail. T3 commit to Episodic must
succeed as `quarantined`. Summarizer then reads {T1, T2, T3} crystals and
attempts `commit(layer="semantic")` — propagated tier is MIN = T3 →
`TierCeilingViolation`.

**Oracle:** Every non-Episodic T3 commit raises `TierViolation`. Summarization
commit raises `TierCeilingViolation` with structured advice. Episodic record
carries `validation_state="quarantined"`.

**Pass criterion:** All tier enforcement assertions pass; no Semantic write ever
succeeded; structured advice present in `TierCeilingViolation` payload.

**A/B arm:** YES — memory-on arm should produce `TierViolation` deterministically;
memory-off arm skips enforcement (no gating) so the comparison reveals whether
memory adds a protective layer.

---

## CAN-4: selective_forget_superseded

**Scenario:** Commit fact A "auth uses bcrypt". Then update to fact B "auth uses
argon2" via `crystalium.update(id=A_id, patch={...})`. In default recall mode,
query "auth hash algorithm".

**Oracle:** Default recall returns argon2 crystal (B) only. Time-travel query
(`t_valid_as_of` before update) returns bcrypt crystal (A). `A.temporal.t_valid_to`
is set; `A.temporal.superseded_by == B.id`. Hard-delete did NOT occur.

**Pass criterion:** Bi-temporal integrity assertions pass. Argon2 in top-1 for
default recall. Bcrypt retrievable in time-travel mode.

**A/B arm:** YES — memory-on arm tracks bi-temporal history; memory-off has no
history concept.

---

## CAN-5: multi_agent_isolation

**Scenario:** Two distinct agent scopes commit overlapping facts.
`agent_class=apivr` commits "apivr uses TDD". `agent_class=spectra` commits
"spectra uses alignment cycles". Both under project="shared-project".

**Oracle:** Recall with `scope.agent_class_visibility="apivr"` returns zero
spectra-scoped crystals. Recall with `agent_class_visibility="spectra"` returns
zero apivr-scoped crystals.

**Pass criterion:** Zero cross-scope crystals in recall results for each arm.

**A/B arm:** YES — memory-on arm enforces scope isolation; memory-off arm has no
scope awareness.

---

## CAN-6: procedural_verifier_required

**Scenario 1 (fail path):** T2 caller commits a Procedural skill with a verifier
script that exits non-zero. Check `validation_state`.

**Scenario 2 (pass path):** T1 caller commits a Procedural skill with a verifier
script that exits 0. Call `crystalium.skill_invoke(skill_id=...)`.

**Oracle:** Scenario 1: crystal lands as `candidate`, NOT `admitted`/`shared`.
Scenario 2: `skill_invoke` returns `passed=True`; crystal promoted to `admitted`.

**Pass criterion:** Both branches of G3 (G2 + G3) test_anchors pass end-to-end.

**A/B arm:** NO — verifier logic is deterministic; no memory-on/off contrast.

---

## CAN-7: bitemporal_correctness

**Scenario:** Commit crystal X. Update X → creates Y. Verify the bi-temporal
state: `X.temporal.t_valid_to` is set to update time, `X.temporal.superseded_by
== Y.id`, `Y.temporal.t_valid_from` is the update time. Hard-delete is
forbidden: X row still present in store.

**Oracle:** Both X and Y exist in the relational store. X is invalidated. Y is
the current record. No row was deleted.

**Pass criterion:** Bi-temporal table assertions pass. Row count increased by 1
(not 0 from hard-delete or 2 from double-write).

**A/B arm:** NO — structural correctness test.

---

## CAN-8: working_set_budget_invariant

**Scenario:** Populate the store with crystals totalling 3x the total cap (10 500
tokens), spread across all four layers. Call `composer.compose(scope)`.

**Oracle:** Composer applies per-slot caps (executive=300, procedural=600,
semantic=800, episodic=800, execution=1000, buffer=300) and hard-caps the total
at 3500. Eviction is deterministic: records sorted ascending by (importance,
last_access, record_id); lowest-scored records are evicted.

**Pass criterion:** `result.total_tokens <= 3500`. Every slot within cap. Same
inputs yield identical kept-set on repeated runs.

**A/B arm:** NO — composer invariant test.

---

## CAN-9: ecl_envelope_conformance

**Scenario:** Issue a `crystalium.recall`, `crystalium.commit`, and
`crystalium.update` call in sequence. Locate the ECL sidecar files written to
the `runs/<message_id>/` directory.

**Oracle:** Each call writes `ecl-envelope.<message_id>.json` with exactly 11
required ECL v2.0 fields. `integrity.method == "sha256"`. `integrity.value ==
sha256(payload_bytes).hexdigest()`. `artifact.sha256 == integrity.value`. `from.eidolon == "crystalium"`.

**Pass criterion:** All three envelopes parse against `envelope.v2.json` schema.
SHA-256 recomputed from payload matches envelope field. No field missing.

**A/B arm:** NO — envelope conformance is always required.

---

## CAN-10: dream_dedup_single_run

**Scenario:** Trigger a Dream consolidation via three concurrent paths at the
same timestamp: (1) `crystalium.session_end()`, (2) idle-poll tick, (3) manual
event-count threshold hit. All three share the same `dream_run_id` derivation
logic.

**Oracle:** Exactly one Dream run is enqueued. The scheduler's `_last_dream`
timestamp is updated once. No duplicate entries in the Dream queue.

**Pass criterion:** Queue length == 1 after concurrent trigger. `dream_run_id`
identical across all three trigger paths (deterministic key derivation).

**A/B arm:** NO — scheduler dedup invariant.
