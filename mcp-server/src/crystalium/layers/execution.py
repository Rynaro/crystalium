"""Execution layer adapter — ephemeral TTL-bound plan state.

T0/T1 only (T2/T3 blocked by D1 matrix). No propose_promote or force_promote
(execution is ephemeral, TTL-bound, expires at task end).

checkpoint(): write new plan state, TTL-bound.
replan(): append-only history of plan diffs (bi-temporal: superseded_by).

Source: spec.md §4 (Execution row), FORGE D1; gate G1.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import structlog

from crystalium.enforcement import Enforcement
from crystalium.schemas import Provenance
from crystalium.telemetry import now_ms
from crystalium.trust import Tier

log = structlog.get_logger("crystalium.layers.execution")

CommitResult = dict[str, Any]

# Default TTL for execution-layer entries (config will expose this in W4/W6)
_DEFAULT_TTL_HOURS = 24


class ExecutionLayer:
    """Execution memory layer — ephemeral TTL-bound plan state.

    Args:
        blob_store:    BlobStore for checkpoint/replan payloads.
        relational:    RelationalStore for index.
        enforcement:   Enforcement instance.
        importance_fn: Callable from importance.py.
        ttl_hours:     Time-to-live for execution entries (default: 24h).
    """

    def __init__(
        self,
        blob_store: Any,
        relational: Any,
        enforcement: Enforcement,
        importance_fn: Callable[..., float],
        ttl_hours: int = _DEFAULT_TTL_HOURS,
    ) -> None:
        self.blob_store = blob_store
        self.relational = relational
        self.enforcement = enforcement
        self.importance_fn = importance_fn
        self.ttl_hours = ttl_hours

    # ------------------------------------------------------------------
    # checkpoint (spec.md §5.5)
    # ------------------------------------------------------------------

    def checkpoint(
        self,
        state: dict[str, Any],
        caller_tier: Tier,
    ) -> CommitResult:
        """Write a plan checkpoint with TTL.

        Enforcement order:
          1. assert_rate_limit
          2. assert_tier_allowed(layer='execution', op='commit')  [G1: T2/T3 blocked]
          3. put blob
          4. insert relational with TTL-derived t_valid_to
          5. record (finally)

        The TTL is baked into temporal.t_valid_to at write time. Dream prune
        and recall both filter on t_valid_to to enforce expiry.

        Returns:
            CommitResult with status, id, layer, validation_state.
        """
        t0 = now_ms()
        outcome = "ok"
        error_code: str | None = None

        try:
            # 1. Rate limit
            self.enforcement.assert_rate_limit()

            # 2. Tier check (G1: T2/T3 denied)
            self.enforcement.assert_tier_allowed(
                "crystalium.plan_checkpoint", "execution", caller_tier, "commit"
            )

            now = datetime.now(timezone.utc)
            ttl_expires = now + timedelta(hours=self.ttl_hours)
            crystal_id = str(uuid.uuid4())

            payload_bytes = json.dumps(state, default=str).encode()
            content_ref = self.blob_store.put(payload_bytes)

            scope = state.get("scope", {})
            summary = state.get("summary", f"plan_checkpoint:{crystal_id[:8]}")

            utility = {
                "access_count": 0,
                "last_access": now.isoformat(),
                "outcome_success_score": None,
                "importance": 0.5,  # execution entries are currently active work
                "novelty_at_write": 0.5,
            }

            crystal_record: dict[str, Any] = {
                "id": crystal_id,
                "layer": "execution",
                "content_ref": content_ref,
                "summary": summary if isinstance(summary, str) else str(summary)[:256],
                "embedding_ref": None,
                "provenance": {
                    "source": "verified_agent" if caller_tier <= Tier.T1 else "unverified_agent",
                    "author_agent": None,
                    "task_id": state.get("task_id"),
                    "created_at": now.isoformat(),
                },
                "trust_tier": str(caller_tier),
                "validation_state": "validated",
                "scope": scope,
                "temporal": {
                    "t_valid_from": now.isoformat(),
                    "t_valid_to": ttl_expires.isoformat(),  # TTL bound
                    "superseded_by": None,
                },
                "utility": utility,
                "status": "active",
            }

            self.relational.insert_crystal(crystal_record)

            log.info(
                "execution_checkpoint",
                crystal_id=crystal_id,
                ttl_expires=ttl_expires.isoformat(),
                caller_tier=str(caller_tier),
            )

            return {
                "status": "committed",
                "id": crystal_id,
                "layer": "execution",
                "validation_state": "validated",
                "expires_at": ttl_expires.isoformat(),
                "content_ref": content_ref,
            }

        except Exception as exc:
            outcome = "error" if not hasattr(exc, "reason_code") else "rejected"
            error_code = getattr(exc, "reason_code", type(exc).__name__)
            raise

        finally:
            self.enforcement.record(
                "crystalium.plan_checkpoint",
                "execution",
                caller_tier,
                "commit",
                outcome,
                now_ms() - t0,
                error=error_code,
            )

    # ------------------------------------------------------------------
    # replan — append-only history (spec.md §5.6)
    # ------------------------------------------------------------------

    def replan(
        self,
        diff: dict[str, Any],
        caller_tier: Tier,
    ) -> CommitResult:
        """Append a plan replan diff.

        Bi-temporal: if diff references an existing plan_id, invalidate
        the old entry (t_valid_to=now, superseded_by=new_id) and write new.
        Always append-only — no hard deletes (P0-5).

        Enforcement order:
          1. assert_rate_limit
          2. assert_tier_allowed(layer='execution', op='commit')  [G1]
          3. If diff.supersedes_id: mark_superseded(old, new, now)
          4. put blob + insert relational
          5. record (finally)

        Returns:
            CommitResult for the new plan node.
        """
        t0 = now_ms()
        outcome = "ok"
        error_code: str | None = None

        try:
            # 1. Rate limit
            self.enforcement.assert_rate_limit()

            # 2. Tier check (G1: T2/T3 denied)
            self.enforcement.assert_tier_allowed(
                "crystalium.plan_replan", "execution", caller_tier, "commit"
            )

            now = datetime.now(timezone.utc)
            ttl_expires = now + timedelta(hours=self.ttl_hours)
            crystal_id = str(uuid.uuid4())

            # 3. Bi-temporal supersession if diff references old plan
            supersedes_id = diff.get("supersedes_id")
            if supersedes_id:
                try:
                    self.relational.mark_superseded(supersedes_id, crystal_id, now)
                except KeyError:
                    log.warning(
                        "replan_supersedes_not_found",
                        supersedes_id=supersedes_id,
                    )

            payload_bytes = json.dumps(diff, default=str).encode()
            content_ref = self.blob_store.put(payload_bytes)

            scope = diff.get("scope", {})
            summary = diff.get("summary", f"plan_replan:{crystal_id[:8]}")

            utility = {
                "access_count": 0,
                "last_access": now.isoformat(),
                "outcome_success_score": None,
                "importance": 0.5,
                "novelty_at_write": 0.5,
            }

            crystal_record: dict[str, Any] = {
                "id": crystal_id,
                "layer": "execution",
                "content_ref": content_ref,
                "summary": summary if isinstance(summary, str) else str(summary)[:256],
                "embedding_ref": None,
                "provenance": {
                    "source": "verified_agent" if caller_tier <= Tier.T1 else "unverified_agent",
                    "author_agent": None,
                    "task_id": diff.get("task_id"),
                    "created_at": now.isoformat(),
                },
                "trust_tier": str(caller_tier),
                "validation_state": "validated",
                "scope": scope,
                "temporal": {
                    "t_valid_from": now.isoformat(),
                    "t_valid_to": ttl_expires.isoformat(),
                    "superseded_by": None,
                },
                "utility": utility,
                "status": "active",
            }

            self.relational.insert_crystal(crystal_record)

            log.info(
                "execution_replan",
                crystal_id=crystal_id,
                supersedes_id=supersedes_id,
                caller_tier=str(caller_tier),
            )

            return {
                "status": "committed",
                "id": crystal_id,
                "layer": "execution",
                "validation_state": "validated",
                "expires_at": ttl_expires.isoformat(),
                "supersedes": supersedes_id,
                "content_ref": content_ref,
            }

        except Exception as exc:
            outcome = "error" if not hasattr(exc, "reason_code") else "rejected"
            error_code = getattr(exc, "reason_code", type(exc).__name__)
            raise

        finally:
            self.enforcement.record(
                "crystalium.plan_replan",
                "execution",
                caller_tier,
                "commit",
                outcome,
                now_ms() - t0,
                error=error_code,
            )
