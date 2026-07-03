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
from crystalium.quality import POOR_SUMMARY_ADVICE, is_poor_summary
from crystalium.schemas import Provenance
from crystalium.scope import canonical_project_key, normalize_write_scope
from crystalium.telemetry import now_ms
from crystalium.trust import Tier

log = structlog.get_logger("crystalium.layers.execution")

CommitResult = dict[str, Any]

# Default TTL for execution-layer entries (config will expose this in W4/W6)
_DEFAULT_TTL_HOURS = 24

#: Sentinel distinguishing "state has no 'summary' key" from any other value.
#: dict.get(key, default) applies the default ONLY on key absence — the
#: pre-v1.6 fallback (f"plan_checkpoint:{crystal_id[:8]}") relied on that, and
#: the v1.6 enrichment preserves the same semantic: an explicit caller-supplied
#: summary (even a poor one) is flagged via summary_quality but never rewritten;
#: only a fully-omitted summary is auto-composed.
_MISSING = object()


def _default_checkpoint_summary(
    crystal_id: str, state: dict[str, Any], scope: dict[str, Any]
) -> str:
    """Compose a diagnosable default checkpoint summary (v1.6 item 2).

    MOTIVATING INCIDENT (CHANGELOG v1.6.0): the bare 'plan_checkpoint:<id>'
    label was the ONLY FTS-indexed text on the sole surviving checkpoint of a
    9-crystal store, so BM25/FTS5 recall could never surface it by any query a
    human would actually type. This composes plan name + scope + phase words
    instead — only used when the caller omitted 'summary' entirely (an
    explicit caller-supplied summary, however poor, is never rewritten; see
    quality.is_poor_summary / the summary_quality advisory).
    """
    plan_name = state.get("plan_name") or state.get("plan") or scope.get("plan")
    phase = state.get("phase") or state.get("current_phase") or state.get("step")
    project = scope.get("project")

    parts = ["plan checkpoint"]
    if plan_name:
        parts.append(f"for {plan_name}")
    if project:
        parts.append(f"(project={project})")
    if phase:
        parts.append(f"phase={phase}")
    parts.append(f"[{crystal_id[:8]}]")
    return " ".join(str(p) for p in parts)


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
        aetheryte: Any = None,
        recall_cache: Any = None,
        recall_prefetch: bool = False,
    ) -> None:
        self.blob_store = blob_store
        self.relational = relational
        self.enforcement = enforcement
        self.importance_fn = importance_fn
        self.ttl_hours = ttl_hours
        # W5 predictive prefetch (protention; default off). When on, a checkpoint
        # whose state declares a predicted next query pre-warms the recall cache;
        # the per-checkpoint prediction_error records whether that need was
        # already anticipated (cache warm) at prediction time.
        self.aetheryte = aetheryte
        self.recall_cache = recall_cache
        self.recall_prefetch = recall_prefetch

    def _prefetch(
        self,
        checkpoint_id: str,
        query: str,
        scope: dict[str, Any],
        caller_tier: Tier,
    ) -> None:
        """W5 protention: pre-warm the recall cache for the predicted next query
        and record prediction_error on the checkpoint crystal. prediction_error
        is 1.0 when the predicted need was cold (not anticipated) at this point,
        0.0 when it was already warm. Best-effort; never breaks the checkpoint."""
        if self.aetheryte is None or self.recall_cache is None or not query:
            return
        try:
            project = scope.get("project") if isinstance(scope, dict) else None
            if not project:
                return
            # Mirror the pre-warm recall's keying (k=10, layers=None, scope + tier),
            # so the warmth check reads the same cache entry the warm-up writes.
            _ctx = {
                "k": 10, "layers": None,
                "visibility": scope.get("agent_class_visibility") if isinstance(scope, dict) else None,
                "sensitivity": scope.get("sensitivity_tag") if isinstance(scope, dict) else None,
                "tier": str(caller_tier) if caller_tier is not None else None,
            }
            warm = self.recall_cache.peek(project, query, **_ctx)
            prediction_error = 0.0 if warm else 1.0
            if not warm:
                # Warm the cache so the actual recall lands as a hit.
                from crystalium.schemas import Scope

                self.aetheryte.recall(
                    Scope(
                        project=project,
                        agent_class_visibility=scope.get("agent_class_visibility"),
                        sensitivity_tag=scope.get("sensitivity_tag"),
                    ),
                    query,
                    10,
                    None,
                    caller_tier,
                )
            try:
                self.relational.update_dynamics(
                    checkpoint_id, {"prediction_error": prediction_error}
                )
            except Exception as exc:  # noqa: BLE001
                log.debug("prediction_error_write_skipped", error=str(exc))
        except Exception as exc:  # noqa: BLE001
            log.debug("prefetch_skipped", error=str(exc))

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

            # v1.6 item 1: canonical project-key normalization (write path). Also
            # reached indirectly via crystalium.commit(layer='execution') ->
            # server._handle_commit, which already normalized — this pass is then
            # a no-op (idempotent) and scope_normalized reflects the earlier one.
            canonical_project = canonical_project_key(self.enforcement.config.data_dir)
            scope, scope_normalized = normalize_write_scope(
                state.get("scope"), canonical_project
            )

            # v1.6 item 2: auto-enrich ONLY the server-composed default label (the
            # caller omitted 'summary' entirely) — an explicit caller summary is
            # never rewritten, however poor (find-where-composed target: the old
            # f"plan_checkpoint:{crystal_id[:8]}" fallback below).
            raw_summary = state.get("summary", _MISSING)
            if raw_summary is _MISSING:
                summary = _default_checkpoint_summary(crystal_id, state, scope)
            else:
                summary = raw_summary

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

            # W5 prefetch (default off): pre-warm the recall cache for the plan's
            # predicted next query + record prediction_error on this checkpoint.
            if self.recall_prefetch:
                predicted = state.get("predicted_next_query") or state.get("next_query")
                if predicted:
                    self._prefetch(crystal_id, str(predicted), scope, caller_tier)

            log.info(
                "execution_checkpoint",
                crystal_id=crystal_id,
                ttl_expires=ttl_expires.isoformat(),
                caller_tier=str(caller_tier),
            )

            result: CommitResult = {
                "status": "committed",
                "id": crystal_id,
                "layer": "execution",
                "validation_state": "validated",
                "expires_at": ttl_expires.isoformat(),
                "content_ref": content_ref,
            }
            # v1.6 items 1 + 2: additive advisories, byte-identical result on the
            # clean path (no scope rewrite, non-poor summary).
            if scope_normalized:
                result["scope_normalized"] = True
            if is_poor_summary(crystal_record["summary"]):
                result["summary_quality"] = "poor"
                result["advisory"] = POOR_SUMMARY_ADVICE
            return result

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

            # v1.6 item 1: canonical project-key normalization (write path).
            canonical_project = canonical_project_key(self.enforcement.config.data_dir)
            scope, scope_normalized = normalize_write_scope(diff.get("scope"), canonical_project)
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

            result: CommitResult = {
                "status": "committed",
                "id": crystal_id,
                "layer": "execution",
                "validation_state": "validated",
                "expires_at": ttl_expires.isoformat(),
                "supersedes": supersedes_id,
                "content_ref": content_ref,
            }
            # v1.6 item 1: additive advisory, byte-identical result on the clean path.
            if scope_normalized:
                result["scope_normalized"] = True
            return result

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
