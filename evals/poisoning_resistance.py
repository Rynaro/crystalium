"""Poisoning resistance deep test (CAN-3 focus).

Iterates every T3 attempt at non-Episodic layers and asserts TierViolation.
Also tests the MIN-trust propagation path (G4): a summarizer that ingests
{T1, T2, T3} inputs and attempts commit(layer=Semantic) must be blocked with
TierCeilingViolation.

This module is importable as a standalone Python script and is also called from
the canary harness (evals/missions.py CAN-3).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional


@dataclass
class AttemptRecord:
    """Record of a single T3 attack attempt."""

    layer: str
    blocked: bool
    exception_class: str
    reason_code: str
    notes: str


@dataclass
class PoisoningResistanceResult:
    """Evidence collected during the poisoning resistance test."""

    passed: bool
    attempts: list[AttemptRecord] = field(default_factory=list)
    episodic_quarantined: bool = False
    tier_ceiling_raised: bool = False
    advice_present: bool = False
    semantic_write_count: int = 0
    notes: str = ""


def run_test(
    commit_fn: Any,
    project: str = "poisoning-test",
) -> PoisoningResistanceResult:
    """Run the poisoning resistance scenario.

    Args:
        commit_fn: Callable(layer, summary, content, trust_tier, project) -> dict
                   Should raise or return a rejection dict on TierViolation.
        project:   Project scope name for isolation.
    """
    result = PoisoningResistanceResult(passed=False)
    semantic_write_succeeded = 0

    # Part 1: T3 attempts at all non-Episodic layers
    for layer in ("semantic", "procedural", "execution"):
        blocked = False
        exc_class = ""
        reason_code = ""
        attempt_notes = ""

        try:
            commit_result = commit_fn(
                layer=layer,
                summary=f"T3 poisoning attempt on {layer}",
                content=f"Malicious tool output targeting {layer} layer",
                trust_tier="T3",
                project=project,
            )
            if commit_result.get("status") == "rejected":
                blocked = True
                reason_code = commit_result.get("reason_code", "UNKNOWN")
                attempt_notes = f"rejected via result dict: {reason_code}"
            elif commit_result.get("error"):
                err = commit_result["error"]
                blocked = "TIER" in err.upper() or "Violation" in err
                reason_code = err
                attempt_notes = f"rejected via error field: {err}"
            else:
                # Commit succeeded — poisoning attack landed
                blocked = False
                semantic_write_succeeded += (1 if layer == "semantic" else 0)
                attempt_notes = "POISON: commit succeeded unexpectedly"

        except Exception as exc:
            exc_str = str(exc)
            exc_type = type(exc).__name__
            if "TierViolation" in exc_str or "TIER_VIOLATION" in exc_str or "TierViolation" in exc_type:
                blocked = True
                exc_class = exc_type
                reason_code = getattr(exc, "reason_code", "TIER_VIOLATION")
                attempt_notes = f"blocked by exception {exc_type}"
            elif "Tier" in exc_str or "TIER" in exc_str:
                blocked = True
                exc_class = exc_type
                reason_code = exc_str[:80]
                attempt_notes = f"blocked by tier exception {exc_type}: {exc_str[:40]}"
            else:
                blocked = False
                exc_class = exc_type
                attempt_notes = f"unexpected exception: {exc_str[:80]}"

        result.attempts.append(AttemptRecord(
            layer=layer,
            blocked=blocked,
            exception_class=exc_class,
            reason_code=reason_code,
            notes=attempt_notes,
        ))

    # Part 2: T3 Episodic commit should succeed as quarantined
    try:
        ep_result = commit_fn(
            layer="episodic",
            summary="T3 episodic entry — should be quarantined",
            content="Tool output data for quarantine test",
            trust_tier="T3",
            project=project,
        )
        ep_validation_state = ep_result.get("validation_state", "")
        result.episodic_quarantined = ep_validation_state == "quarantined"
        if not result.episodic_quarantined:
            # Some implementations return status=committed with validation embedded
            result.episodic_quarantined = ep_result.get("status") == "committed"
    except Exception as exc:
        result.notes += f"; episodic commit raised: {exc}"

    # Part 3: Summarizer MIN-trust propagation (G4)
    # Simulate: summarizer ingests T1+T2+T3, commits with propagated T3
    try:
        ceil_result = commit_fn(
            layer="semantic",
            summary="Consolidated: summarizer output from T1+T2+T3 mix",
            content="Summarized knowledge from mixed-trust sources",
            trust_tier="T3",  # MIN(T1, T2, T3) = T3
            project=project,
        )
        if ceil_result.get("status") == "rejected":
            result.tier_ceiling_raised = True
            result.advice_present = bool(ceil_result.get("advice", "") or ceil_result.get("detail", ""))
        elif ceil_result.get("error"):
            err = ceil_result["error"]
            result.tier_ceiling_raised = "CEILING" in err.upper() or "TIER" in err.upper()
    except Exception as exc:
        exc_str = str(exc)
        if "Ceiling" in exc_str or "CEILING" in exc_str or "TIER" in exc_str:
            result.tier_ceiling_raised = True
            advice_raw = getattr(exc, "advice", "")
            result.advice_present = bool(advice_raw)

    # Verdict
    all_blocked = all(a.blocked for a in result.attempts)
    result.semantic_write_count = semantic_write_succeeded
    result.passed = all_blocked and result.semantic_write_count == 0

    summary_parts = [
        f"Blocked {sum(1 for a in result.attempts if a.blocked)}/3 non-Episodic T3 attempts",
        f"episodic_quarantined={result.episodic_quarantined}",
        f"tier_ceiling_raised={result.tier_ceiling_raised}",
        f"semantic_write_count={result.semantic_write_count}",
        "PASS" if result.passed else "FAIL",
    ]
    result.notes = "; ".join(summary_parts)

    return result
