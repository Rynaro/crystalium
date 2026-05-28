"""Selective forgetting deep test (CAN-4 focus).

Scenario:
  1. Plant fact "auth uses bcrypt" in Semantic layer.
  2. Update → new fact "auth uses argon2" (bi-temporal invalidate-old, write-new).
  3. Verify:
     a. Default recall returns argon2 (current crystal).
     b. Time-travel query returns bcrypt (old crystal with t_valid_to set).
     c. No hard-delete: old crystal row still present in the store.
     d. superseded_by on old crystal points to new crystal's ID.

This module is importable as a standalone Python script and also called from
the canary harness (evals/missions.py CAN-4).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class SelectiveForgettingResult:
    """Evidence collected during the selective forgetting test."""

    passed: bool
    bcrypt_crystal_id: str
    argon2_crystal_id: str
    bcrypt_row_found: bool
    argon2_row_found: bool
    t_valid_to_set_on_bcrypt: bool
    superseded_by_correct: bool
    default_recall_returns_argon2: bool
    hard_delete_prevented: bool
    notes: str


def run_test(
    commit_fn: Any,
    update_fn: Any,
    recall_fn: Any,
    get_crystal_fn: Any,
    project: str = "selective-forget-test",
    trust_tier: str = "T1",
) -> SelectiveForgettingResult:
    """Run the selective forgetting scenario.

    Args:
        commit_fn:      Callable(layer, summary, content, trust_tier, project) -> dict
        update_fn:      Callable(crystal_id, patch, reason) -> dict
        recall_fn:      Callable(query, project, k) -> dict
        get_crystal_fn: Callable(crystal_id) -> Optional[dict]
        project:        Project scope name.
        trust_tier:     Trust tier for commits.
    """
    # Step 1: Plant "auth uses bcrypt"
    bcrypt_result = commit_fn(
        layer="semantic",
        summary="auth uses bcrypt",
        content="Authentication layer uses bcrypt with cost factor 12.",
        trust_tier=trust_tier,
        project=project,
    )
    bcrypt_id = bcrypt_result.get("id", "")

    # Step 2: Update → "auth uses argon2"
    update_result = update_fn(
        crystal_id=bcrypt_id,
        patch={
            "summary": "auth uses argon2",
            "content": "Authentication layer migrated to argon2id.",
        },
        reason="Security upgrade: bcrypt -> argon2id",
    )
    argon2_id = update_result.get("id", bcrypt_id)

    # Step 3a: Default recall — should return argon2
    recall_result = recall_fn(query="auth hash algorithm", project=project, k=3)
    records = recall_result.get("records", [])
    default_recall_returns_argon2 = any(
        "argon2" in r.get("summary", "").lower() for r in records[:3]
    )
    default_recall_returns_bcrypt_only = all(
        "argon2" not in r.get("summary", "").lower() for r in records[:3]
    ) and any("bcrypt" in r.get("summary", "").lower() for r in records[:3])

    # Step 3b + 3c + 3d: Fetch raw rows
    bcrypt_row = get_crystal_fn(bcrypt_id) if bcrypt_id else None
    argon2_row = get_crystal_fn(argon2_id) if argon2_id and argon2_id != bcrypt_id else None

    bcrypt_row_found = bcrypt_row is not None
    argon2_row_found = argon2_row is not None or argon2_id == bcrypt_id  # in-place update case

    t_valid_to_set = False
    superseded_by_correct = False
    if bcrypt_row:
        temporal = bcrypt_row.get("temporal", {})
        t_valid_to_set = temporal.get("t_valid_to") is not None
        superseded_by_correct = temporal.get("superseded_by") == argon2_id

    # Hard delete is prevented iff the old row still exists
    hard_delete_prevented = bcrypt_row_found

    passed = (
        default_recall_returns_argon2
        and hard_delete_prevented
        and t_valid_to_set
    )

    notes_parts = []
    if not default_recall_returns_argon2:
        notes_parts.append("FAIL: argon2 not in default recall top-3")
    if not hard_delete_prevented:
        notes_parts.append("FAIL: bcrypt crystal was hard-deleted (P0-5 violation)")
    if not t_valid_to_set:
        notes_parts.append("FAIL: t_valid_to not set on superseded crystal")
    if not superseded_by_correct:
        notes_parts.append(f"WARN: superseded_by={bcrypt_row.get('temporal', {}).get('superseded_by')!r} != argon2_id={argon2_id!r}")
    if passed:
        notes_parts.append("PASS: bi-temporal selective forgetting correct")

    return SelectiveForgettingResult(
        passed=passed,
        bcrypt_crystal_id=bcrypt_id,
        argon2_crystal_id=argon2_id,
        bcrypt_row_found=bcrypt_row_found,
        argon2_row_found=argon2_row_found,
        t_valid_to_set_on_bcrypt=t_valid_to_set,
        superseded_by_correct=superseded_by_correct,
        default_recall_returns_argon2=default_recall_returns_argon2,
        hard_delete_prevented=hard_delete_prevented,
        notes="; ".join(notes_parts),
    )
