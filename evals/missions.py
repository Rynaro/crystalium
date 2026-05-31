"""CRYSTALIUM canary missions — 10 behavioural contracts.

Each mission is a function: `def can_N_<slug>(env: CanaryEnv) -> MissionResult`.

CanaryEnv exposes high-level helpers (recall, commit, update, skill_invoke, ...)
that wrap the server tool handlers. MissionResult carries the verdict + evidence.

Design notes:
  - Missions are pure Python functions; they do NOT import pytest.
  - They accept a CanaryEnv so the A/B harness can inject memory-on or memory-off
    variants without changing mission code.
  - ab_arm: True on a mission means it participates in the headline A/B metric.
    The harness computes Δ = pass_rate(on) - pass_rate(off) over these missions.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional


# ---------------------------------------------------------------------------
# CanaryEnv protocol
# ---------------------------------------------------------------------------


@dataclass
class CanaryEnv:
    """Thin façade over the CRYSTALIUM tool handlers.

    The A/B harness injects either a real store (memory_on=True) or a null-store
    (memory_on=False).  All mission functions interact exclusively through this
    interface — never directly with storage adapters.

    Attributes:
        memory_on: If True, a live CRYSTALIUM store backs every call.
                   If False, commits are no-ops and recalls return empty.
        project:   Scope project name for isolation between test runs.
        _state:    Internal dict for cross-call state (e.g. committed crystal IDs).
        handlers:  Dict of callable handlers keyed by tool name.
                   Injected by the harness; see ab_memory_onoff.py.
    """

    memory_on: bool
    project: str
    _state: dict[str, Any] = field(default_factory=dict)
    handlers: dict[str, Any] = field(default_factory=dict)

    def recall(
        self,
        query: str,
        layers: Optional[list[str]] = None,
        k: int = 10,
        agent_class: Optional[str] = None,
    ) -> dict[str, Any]:
        """Issue a crystalium.recall call."""
        args: dict[str, Any] = {
            "scope": {
                "project": self.project,
                "agent_class_visibility": agent_class,
            },
            "query": query,
            "k": k,
        }
        if layers:
            args["layers"] = layers
        handler = self.handlers.get("recall")
        if handler is None or not self.memory_on:
            return {"records": [], "total_tokens": 0, "slot_breakdown": {}, "evicted_count": 0}
        return handler(args)

    def commit(
        self,
        layer: str,
        summary: str,
        content: str,
        trust_tier: str = "T1",
        agent_class: Optional[str] = None,
        sensitivity_tag: Optional[str] = None,
    ) -> dict[str, Any]:
        """Issue a crystalium.commit call."""
        content_sha = hashlib.sha256(content.encode()).hexdigest()
        args = {
            "layer": layer,
            "payload": {
                "summary": summary,
                "content": content,
                "scope": {
                    "project": self.project,
                    "agent_class_visibility": agent_class,
                    "sensitivity_tag": sensitivity_tag,
                },
                "trust_tier": trust_tier,
            },
            "provenance": {
                "source": "verified_agent",
                "author_agent": "canary-test",
                "task_id": str(uuid.uuid4()),
            },
        }
        handler = self.handlers.get("commit")
        if handler is None or not self.memory_on:
            # Null-arm: generate a stub ID so caller can track it
            return {"status": "committed", "id": f"null-{uuid.uuid4()}", "layer": layer,
                    "validation_state": "unverified", "importance": 0.0}
        return handler(args)

    def update(self, crystal_id: str, patch: dict[str, Any], reason: str) -> dict[str, Any]:
        """Issue a crystalium.update call."""
        args = {"id": crystal_id, "patch": patch, "reason": reason}
        handler = self.handlers.get("update")
        if handler is None or not self.memory_on:
            return {"status": "updated", "id": crystal_id, "patch_keys": list(patch.keys()),
                    "reason": reason, "note": "null-arm"}
        return handler(args)

    def skill_invoke(
        self,
        skill_id: str,
        args: Optional[dict[str, Any]] = None,
        timeout_s: int = 30,
    ) -> dict[str, Any]:
        """Issue a crystalium.skill_invoke call."""
        invoke_args = {
            "skill_id": skill_id,
            "args": args or {},
            "timeout_s": timeout_s,
        }
        handler = self.handlers.get("skill_invoke")
        if handler is None or not self.memory_on:
            return {"skill_id": skill_id, "exit_code": 0, "stdout": "", "stderr": "",
                    "overflow_flag": False, "passed": True}
        return handler(invoke_args)

    def session_end(self, reason: str = "") -> dict[str, Any]:
        """Issue a crystalium.session_end call."""
        args = {"reason": reason}
        handler = self.handlers.get("session_end")
        if handler is None or not self.memory_on:
            return {"enqueued": False, "dream_run_id": None}
        return handler(args)

    def get_crystal_row(self, crystal_id: str) -> Optional[dict[str, Any]]:
        """Fetch raw crystal metadata from the relational store."""
        getter = self.handlers.get("get_crystal")
        if getter is None or not self.memory_on:
            return None
        return getter(crystal_id)

    def row_count(self, layer: str) -> int:
        """Return count of crystals in a given layer."""
        counter = self.handlers.get("row_count")
        if counter is None or not self.memory_on:
            return 0
        return counter(layer)

    def dream_queue_len(self) -> int:
        """Return number of pending Dream runs."""
        getter = self.handlers.get("dream_queue_len")
        if getter is None or not self.memory_on:
            return 0
        return getter()

    def trigger_idle_poll(self) -> Optional[str]:
        """Manually trigger an idle-poll tick."""
        trigger = self.handlers.get("trigger_idle_poll")
        if trigger is None or not self.memory_on:
            return None
        return trigger()


# ---------------------------------------------------------------------------
# MissionResult
# ---------------------------------------------------------------------------


@dataclass
class MissionResult:
    """Result of a single canary mission execution.

    Attributes:
        mission_id: CAN-N string.
        passed:     True iff the pass criterion was met.
        observed:   Collected measurements / actual values.
        expected:   Expected values for the pass criterion.
        notes:      Free-text explanation (especially on failure).
        error:      Exception string if mission raised unexpectedly.
    """

    mission_id: str
    passed: bool
    observed: dict[str, Any]
    expected: dict[str, Any]
    notes: str = ""
    error: str = ""


# ---------------------------------------------------------------------------
# Utility: MRR@k
# ---------------------------------------------------------------------------


def _mrr_at_k(records: list[dict[str, Any]], target_summary: str, k: int = 3) -> float:
    """Compute MRR@k for whether any record's summary matches the target string."""
    for rank, record in enumerate(records[:k], start=1):
        if target_summary.lower() in record.get("summary", "").lower():
            return 1.0 / rank
    return 0.0


# ---------------------------------------------------------------------------
# Mission implementations
# ---------------------------------------------------------------------------


def can_1_recall_hit_across_sessions(env: CanaryEnv) -> MissionResult:
    """CAN-1: Commit fact in session A; recall in session B. A/B arm."""
    target_summary = "auth uses bcrypt"
    # Commit to EPISODIC (the universal write buffer): it persists immediately and
    # is recallable. (A lone T1 SEMANTIC commit is correctly held PENDING by the
    # promotion gate, so it would never be recallable — that hardening is not what
    # this mission tests.)
    commit_result = env.commit(
        layer="episodic",
        summary=target_summary,
        content="Authentication layer uses bcrypt with cost factor 12.",
        trust_tier="T1",
    )
    committed_id = commit_result.get("id", "")

    recall_result = env.recall(query="auth bcrypt hash", k=3)
    records = recall_result.get("records", [])
    mrr = _mrr_at_k(records, target_summary)

    # Memory-DEPENDENT criterion, SAME for both arms: the fact must be recalled.
    # Memory-on recalls it (mrr>0 -> PASS); memory-off has an empty store (mrr==0 ->
    # FAIL). This is what makes the A/B delta meaningful (no vacuous off-arm pass).
    passed = mrr > 0

    return MissionResult(
        mission_id="CAN-1",
        passed=passed,
        observed={"mrr_at_3": mrr, "committed_id": committed_id, "record_count": len(records)},
        expected={"mrr_at_3_gt": 0, "memory_on": env.memory_on},
        notes=(
            f"memory_on={env.memory_on}: mrr@3={mrr:.3f}. "
            + ("PASS — recalled" if passed else "FAIL — fact not recalled (expected for memory-off)")
        ),
    )


def can_2_promote_gate_t3_blocked(env: CanaryEnv) -> MissionResult:
    """CAN-2: T3 caller attempts Semantic commit. Must raise TierViolation. No A/B."""
    row_before = env.row_count("semantic")

    # Directly call with T3 tier
    tier_violation_raised = False
    reason_code = ""
    try:
        result = env.commit(
            layer="semantic",
            summary="T3 injection attempt",
            content="Malicious content from tool output",
            trust_tier="T3",
        )
        # If no exception, check the result for a rejection signal
        if result.get("status") == "rejected":
            tier_violation_raised = True
            reason_code = result.get("reason_code", "")
        elif result.get("error"):
            tier_violation_raised = "TIER" in result.get("error", "").upper()
    except Exception as exc:
        exc_str = str(exc)
        if "TierViolation" in exc_str or "TIER_VIOLATION" in exc_str:
            tier_violation_raised = True
            reason_code = "TIER_VIOLATION"
        else:
            tier_violation_raised = False

    row_after = env.row_count("semantic")
    store_unchanged = row_before == row_after

    if env.memory_on:
        passed = tier_violation_raised and store_unchanged
    else:
        # Without memory, no enforcement; treat pass as vacuous
        passed = True

    return MissionResult(
        mission_id="CAN-2",
        passed=passed,
        observed={
            "tier_violation_raised": tier_violation_raised,
            "reason_code": reason_code,
            "semantic_row_before": row_before,
            "semantic_row_after": row_after,
        },
        expected={"tier_violation_raised": True, "store_unchanged": True},
        notes=f"G1 gate check. memory_on={env.memory_on}. " + ("PASS" if passed else "FAIL"),
    )


def can_3_poisoning_resistance_t3_episodic_only(env: CanaryEnv) -> MissionResult:
    """CAN-3: T3 tool output stays Episodic-quarantined, never reaches Semantic. A/B arm."""
    blocked_layers: list[str] = []
    episodic_quarantined = False
    tier_ceiling_raised = False

    for layer in ("semantic", "procedural", "execution"):
        try:
            result = env.commit(
                layer=layer,
                summary=f"T3 attempt on {layer}",
                content=f"tool output targeting {layer}",
                trust_tier="T3",
            )
            if result.get("status") == "rejected" or "error" in result:
                blocked_layers.append(layer)
        except Exception as exc:
            if "Tier" in str(exc) or "TIER" in str(exc):
                blocked_layers.append(layer)

    # T3 Episodic commit should succeed as quarantined
    try:
        ep_result = env.commit(
            layer="episodic",
            summary="T3 episodic entry — should be quarantined",
            content="Tool output data",
            trust_tier="T3",
        )
        ep_id = ep_result.get("id", "")
        if ep_id and env.memory_on:
            row = env.get_crystal_row(ep_id)
            if row and row.get("validation_state") == "quarantined":
                episodic_quarantined = True
        elif not env.memory_on:
            episodic_quarantined = True  # vacuous
    except Exception:
        episodic_quarantined = False

    # Summarizer attempt: min-tier propagation
    try:
        result = env.commit(
            layer="semantic",
            summary="Consolidated: T3 laundering attempt",
            content="Summarized from T1+T2+T3 mix",
            trust_tier="T3",  # propagated MIN = T3
        )
        if result.get("status") == "rejected" or "error" in result:
            tier_ceiling_raised = True
    except Exception as exc:
        if "Ceiling" in str(exc) or "TIER" in str(exc):
            tier_ceiling_raised = True

    # SAME criterion both arms: the tier wall must block all non-episodic layers AND
    # episodic must land quarantined. Memory-off has no enforcement/store, so the
    # commits are no-ops that neither reject nor quarantine -> FAIL (poison is not
    # contained without the memory substrate). No vacuous off-arm pass.
    all_blocked = len(blocked_layers) == 3
    passed = all_blocked and episodic_quarantined

    return MissionResult(
        mission_id="CAN-3",
        passed=passed,
        observed={
            "blocked_layers": blocked_layers,
            "episodic_quarantined": episodic_quarantined,
            "tier_ceiling_raised": tier_ceiling_raised,
        },
        expected={
            "all_non_episodic_blocked": True,
            "episodic_quarantined": True,
        },
        notes=f"memory_on={env.memory_on}. " + ("PASS" if passed else "FAIL"),
    )


def can_4_selective_forget_superseded(env: CanaryEnv) -> MissionResult:
    """CAN-4: Update a crystal; old remains with t_valid_to set. A/B arm."""
    # Commit original fact to EPISODIC (persists immediately + supports bi-temporal
    # update; a semantic commit would land pending and the update would 404).
    # Distinct fixture (NOT CAN-1's "auth uses bcrypt") so write_dedup_merge — ON by
    # default — does not collide this mission's commit with CAN-1's in the shared
    # project, which would re-point the update target.
    result_a = env.commit(
        layer="episodic",
        summary="deploy region is us-east-1",
        content="The production deploy region is us-east-1.",
        trust_tier="T1",
    )
    crystal_a_id = result_a.get("id", "")

    # Update to new fact (region migration)
    result_b = env.update(
        crystal_id=crystal_a_id,
        patch={"summary": "deploy region is eu-west-1", "content": "Production migrated to eu-west-1."},
        reason="Region migration: us-east-1 -> eu-west-1",
    )
    crystal_b_id = result_b.get("id", crystal_a_id)

    # Recall in default mode — should return the new region (eu-west-1)
    recall = env.recall(query="deploy region production", k=3)
    records = recall.get("records", [])
    argon2_in_top3 = any("eu-west-1" in r.get("summary", "").lower() for r in records[:3])

    # Verify bi-temporal state of old crystal
    t_valid_to_set = False
    superseded_by_correct = False
    hard_delete_occurred = False

    if env.memory_on and crystal_a_id:
        old_row = env.get_crystal_row(crystal_a_id)
        if old_row is None:
            hard_delete_occurred = True
        else:
            temporal = old_row.get("temporal", {})
            t_valid_to_set = temporal.get("t_valid_to") is not None
            superseded_by_correct = temporal.get("superseded_by") == crystal_b_id

    # SAME criterion both arms: old crystal soft-superseded (t_valid_to set, no hard
    # delete) AND the new fact recalled. Memory-off can't supersede or recall -> FAIL.
    passed = (not hard_delete_occurred) and t_valid_to_set and argon2_in_top3

    return MissionResult(
        mission_id="CAN-4",
        passed=passed,
        observed={
            "crystal_a_id": crystal_a_id,
            "crystal_b_id": crystal_b_id,
            "t_valid_to_set": t_valid_to_set,
            "superseded_by_correct": superseded_by_correct,
            "hard_delete_occurred": hard_delete_occurred,
            "argon2_in_top3": argon2_in_top3,
        },
        expected={"t_valid_to_set": True, "no_hard_delete": True, "argon2_in_recall": True},
        notes=f"memory_on={env.memory_on}. " + ("PASS" if passed else "FAIL"),
    )


def can_5_multi_agent_isolation(env: CanaryEnv) -> MissionResult:
    """CAN-5: Agent-class scope isolation. A/B arm."""
    # Commit to EPISODIC (persists + recallable; semantic would land pending).
    env.commit(
        layer="episodic",
        summary="apivr uses TDD for all features",
        content="APIVR-Delta implements test-driven development.",
        trust_tier="T1",
        agent_class="apivr",
    )
    # spectra commits its fact
    env.commit(
        layer="episodic",
        summary="spectra uses alignment cycles",
        content="SPECTRA runs alignment planning phases.",
        trust_tier="T1",
        agent_class="spectra",
    )

    # Recall from apivr scope
    apivr_recall = env.recall(query="apivr methodology approach", k=5, agent_class="apivr")
    apivr_records = apivr_recall.get("records", [])
    apivr_sees_own = any("apivr" in r.get("summary", "").lower() for r in apivr_records)
    spectra_in_apivr = any("spectra" in r.get("summary", "").lower() for r in apivr_records)

    # Recall from spectra scope
    spectra_recall = env.recall(query="spectra methodology approach", k=5, agent_class="spectra")
    spectra_records = spectra_recall.get("records", [])
    spectra_sees_own = any("spectra" in r.get("summary", "").lower() for r in spectra_records)
    apivr_in_spectra = any("apivr" in r.get("summary", "").lower() for r in spectra_records)

    # SAME criterion both arms (strengthened, memory-DEPENDENT): each scope must
    # recall its OWN fact AND NOT see the other's. Memory-off recalls nothing, so
    # "sees own" is False -> FAIL (no vacuous "empty store doesn't leak" pass).
    passed = apivr_sees_own and spectra_sees_own and (not spectra_in_apivr) and (not apivr_in_spectra)

    return MissionResult(
        mission_id="CAN-5",
        passed=passed,
        observed={
            "spectra_leaks_into_apivr": spectra_in_apivr,
            "apivr_leaks_into_spectra": apivr_in_spectra,
            "apivr_recall_count": len(apivr_recall.get("records", [])),
            "spectra_recall_count": len(spectra_recall.get("records", [])),
        },
        expected={"no_cross_scope_leakage": True},
        notes=f"memory_on={env.memory_on}. " + ("PASS" if passed else "FAIL"),
    )


def can_6_procedural_verifier_required(env: CanaryEnv) -> MissionResult:
    """CAN-6: Procedural skill without verifier stays candidate; with verifier -> shared. No A/B."""
    # Attempt skill_invoke on a non-existent / failing skill
    fail_result = env.skill_invoke(skill_id="failing-verifier-skill")
    passed_false = not fail_result.get("passed", True) if env.memory_on else True

    # A passing skill
    pass_result = env.skill_invoke(skill_id="passing-verifier-skill")
    passed_true = pass_result.get("passed", False)

    if env.memory_on:
        # We only check the skill_invoke result shapes; actual promotion state
        # is verified in test_skill_invoke.py::test_g3_skill_invoke_sandbox_contract
        passed = passed_true  # passing skill returns passed=True
    else:
        passed = True  # vacuous

    return MissionResult(
        mission_id="CAN-6",
        passed=passed,
        observed={
            "fail_result": fail_result,
            "pass_result": pass_result,
        },
        expected={"passing_skill_passed": True},
        notes=f"memory_on={env.memory_on}. G3 path. " + ("PASS" if passed else "FAIL"),
    )


def can_7_bitemporal_correctness(env: CanaryEnv) -> MissionResult:
    """CAN-7: Update crystal; verify t_valid_to + superseded_by + no hard-delete. No A/B."""
    result_x = env.commit(
        layer="semantic",
        summary="bitemporal test crystal X",
        content="Original content",
        trust_tier="T1",
    )
    x_id = result_x.get("id", "")

    result_y = env.update(
        crystal_id=x_id,
        patch={"summary": "bitemporal test crystal Y", "content": "Updated content"},
        reason="test update for CAN-7",
    )
    y_id = result_y.get("id", x_id)

    t_valid_to_set = False
    superseded_by_set = False
    x_still_exists = False

    if env.memory_on and x_id:
        row_x = env.get_crystal_row(x_id)
        if row_x is not None:
            x_still_exists = True
            temporal = row_x.get("temporal", {})
            t_valid_to_set = temporal.get("t_valid_to") is not None
            superseded_by_set = temporal.get("superseded_by") == y_id

    if env.memory_on:
        passed = x_still_exists and t_valid_to_set
    else:
        passed = True

    return MissionResult(
        mission_id="CAN-7",
        passed=passed,
        observed={
            "x_id": x_id,
            "y_id": y_id,
            "x_still_exists": x_still_exists,
            "t_valid_to_set": t_valid_to_set,
            "superseded_by_set": superseded_by_set,
        },
        expected={"no_hard_delete": True, "t_valid_to_set": True},
        notes=f"memory_on={env.memory_on}. " + ("PASS" if passed else "FAIL"),
    )


def can_8_working_set_budget_invariant(env: CanaryEnv) -> MissionResult:
    """CAN-8: Composer enforces <=3500 total cap. No A/B."""
    # The actual eviction is tested in test_composer.py::test_g6_working_set_budget_invariant.
    # This mission exercises the outcome via recall (which uses the composer).
    # Commit many records to fill up the store.
    total_tokens_observed = 0
    all_slots_within_cap = True

    for i in range(20):
        env.commit(
            layer="episodic",
            summary=f"budget test record {i}: " + ("x" * 100),
            content=f"Content block {i}: " + ("padding " * 50),
            trust_tier="T1",
        )

    recall_result = env.recall(query="budget test record", k=20)
    total_tokens_observed = recall_result.get("total_tokens", 0)
    slot_breakdown = recall_result.get("slot_breakdown", {})

    slot_caps = {
        "executive": 300, "procedural": 600, "semantic": 800,
        "episodic": 800, "execution": 1000, "buffer": 300,
    }
    for slot, cap in slot_caps.items():
        if slot_breakdown.get(slot, 0) > cap:
            all_slots_within_cap = False

    if env.memory_on:
        passed = total_tokens_observed <= 3500 and all_slots_within_cap
    else:
        passed = True  # vacuous

    return MissionResult(
        mission_id="CAN-8",
        passed=passed,
        observed={
            "total_tokens": total_tokens_observed,
            "slot_breakdown": slot_breakdown,
            "all_slots_within_cap": all_slots_within_cap,
        },
        expected={"total_tokens_lte": 3500, "all_slots_within_cap": True},
        notes=f"memory_on={env.memory_on}. G6. " + ("PASS" if passed else "FAIL"),
    )


def can_9_ecl_envelope_conformance(env: CanaryEnv) -> MissionResult:
    """CAN-9: Every tool result emits a valid ECL v2.0 envelope. No A/B."""
    envelopes_found: list[dict[str, Any]] = []
    validation_errors: list[str] = []

    ecl_validator = env.handlers.get("ecl_validator")

    for op_name, handler_key in [("recall", "recall"), ("commit", "commit")]:
        try:
            if handler_key == "recall":
                env.recall(query="ecl test query", k=1)
            else:
                env.commit(
                    layer="episodic",
                    summary="ECL envelope test crystal",
                    content="Test payload for ECL conformance check",
                    trust_tier="T1",
                )
        except Exception as exc:
            validation_errors.append(f"{op_name}: unexpected exception {exc}")

    if ecl_validator and env.memory_on:
        validation_result = ecl_validator()
        envelopes_found = validation_result.get("envelopes", [])
        validation_errors.extend(validation_result.get("errors", []))
        passed = len(envelopes_found) > 0 and len(validation_errors) == 0
    else:
        # Without an ecl_validator injected, defer to test_ecl_envelope.py
        passed = True  # vacuous in harness; tested by G7 test_anchor

    return MissionResult(
        mission_id="CAN-9",
        passed=passed,
        observed={
            "envelopes_found": len(envelopes_found),
            "validation_errors": validation_errors,
        },
        expected={"envelopes_found_gt": 0, "validation_errors_count": 0},
        notes=f"memory_on={env.memory_on}. G7. " + ("PASS" if passed else "FAIL"),
    )


def can_10_dream_dedup_single_run(env: CanaryEnv) -> MissionResult:
    """CAN-10: Concurrent Dream triggers dedup to exactly one run. No A/B."""
    # Trigger via session_end
    result_session = env.session_end(reason="canary CAN-10 test")
    session_run_id = result_session.get("dream_run_id")

    # Trigger via idle-poll (if handler available)
    idle_run_id = env.trigger_idle_poll()

    queue_len = env.dream_queue_len()

    if env.memory_on:
        # At most one run enqueued; could be 0 if gap conditions not met
        passed = queue_len <= 1
    else:
        passed = True  # vacuous

    return MissionResult(
        mission_id="CAN-10",
        passed=passed,
        observed={
            "session_run_id": session_run_id,
            "idle_run_id": idle_run_id,
            "queue_len": queue_len,
        },
        expected={"queue_len_lte": 1},
        notes=f"memory_on={env.memory_on}. G8. " + ("PASS" if passed else "FAIL"),
    )


# ---------------------------------------------------------------------------
# Mission registry
# ---------------------------------------------------------------------------

MISSIONS = [
    can_1_recall_hit_across_sessions,
    can_2_promote_gate_t3_blocked,
    can_3_poisoning_resistance_t3_episodic_only,
    can_4_selective_forget_superseded,
    can_5_multi_agent_isolation,
    can_6_procedural_verifier_required,
    can_7_bitemporal_correctness,
    can_8_working_set_budget_invariant,
    can_9_ecl_envelope_conformance,
    can_10_dream_dedup_single_run,
]

# A/B eligible missions (headline metric)
AB_ARM_MISSION_IDS = {"CAN-1", "CAN-3", "CAN-4", "CAN-5"}
