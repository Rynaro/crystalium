"""Semantic layer adapter — curated facts, conventions, conventions.

Gate-guarded admission: commits routed through PromotionGate.propose_semantic()
which applies k-corroboration (D8) and human-confirm window (G5).

Bi-temporal updates (P0-5): invalidate-old (t_valid_to=now, superseded_by=new_id)
then write-new. NEVER hard-delete.

All writes funnel through enforcement.assert_tier_allowed() before any store call.

Source: spec.md §4 (Semantic row), FORGE D1, D7, D8; gates G4, G5.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

import structlog

from crystalium.enforcement import Enforcement
from crystalium.gate import PromotionGate, CrystalRef
from crystalium.aetheryte.redact import Redactor
from crystalium.schemas import Provenance
from crystalium.telemetry import now_ms
from crystalium.trust import Tier

log = structlog.get_logger("crystalium.layers.semantic")

CommitResult = dict[str, Any]


class SemanticLayer:
    """Semantic memory layer — curated facts and conventions.

    Args:
        blob_store:   BlobStore for payload storage.
        relational:   RelationalStore for index + pending_promotions.
        vector_store: VectorStore for semantic similarity index.
        graph_store:  GraphStore for relationship index.
        enforcement:  Enforcement instance.
        gate:         PromotionGate instance.
        redactor:     Redactor for handoff re-redaction.
        importance_fn: Callable from importance.py.
    """

    def __init__(
        self,
        blob_store: Any,
        relational: Any,
        vector_store: Any,
        graph_store: Any,
        enforcement: Enforcement,
        gate: PromotionGate,
        redactor: Redactor,
        importance_fn: Callable[..., float],
    ) -> None:
        self.blob_store = blob_store
        self.relational = relational
        self.vector_store = vector_store
        self.graph_store = graph_store
        self.enforcement = enforcement
        self.gate = gate
        self.redactor = redactor
        self.importance_fn = importance_fn

    # ------------------------------------------------------------------
    # commit (G1 / G4 / G5)
    # ------------------------------------------------------------------

    def commit(
        self,
        payload: dict[str, Any],
        provenance: Provenance | dict[str, Any],
        caller_tier: Tier,
        witnesses: list[CrystalRef] | None = None,
        force_promote: bool = False,
    ) -> CommitResult:
        """Commit to the Semantic layer.

        Enforcement order (spec.md §5.2):
          1. assert_rate_limit
          2. assert_tier_allowed(layer='semantic', op='commit')   [G1]
          3. assert_tier_within_layer_ceiling(consolidated_tier)  [G4]
          4. gate.propose_semantic() → admit | pending | reject   [G5]
          5. If admit: put blob + insert relational + vector + graph
          6. If pending: record in pending_promotions; return pending result
          7. record telemetry (finally)

        Args:
            payload:      Crystal payload dict (must include 'summary', 'scope').
            provenance:   Provenance of the commit.
            caller_tier:  Caller's trust tier.
            witnesses:    Corroborating CrystalRef list for k-corroboration (D8).
            force_promote: T0-only bypass of k-gate.

        Returns:
            CommitResult dict. status in {"committed", "pending", "rejected"}.
        """
        t0 = now_ms()
        outcome = "ok"
        error_code: str | None = None

        try:
            # 1. Rate limit
            self.enforcement.assert_rate_limit()

            # 2. Tier check (G1)
            self.enforcement.assert_tier_allowed(
                "crystalium.commit", "semantic", caller_tier, "commit"
            )

            # 3. D7 ceiling check: consolidated_tier must be <= T1 for Semantic
            # The caller supplies the consolidated tier as caller_tier
            # (the server or summarizer is responsible for computing min(inputs))
            self.enforcement.assert_tier_within_layer_ceiling(caller_tier, "semantic")

            prov_dict = (
                provenance.model_dump()
                if hasattr(provenance, "model_dump")
                else dict(provenance)
            )

            # Build a stub crystal for the gate
            crystal_id = str(uuid.uuid4())
            stub_crystal = {"id": crystal_id, **payload}

            # 4. Promotion gate (G5 / D8)
            promotion_result = self.gate.propose_semantic(
                crystal=stub_crystal,
                witnesses=witnesses or [],
                caller_tier=caller_tier,
                force=force_promote,
            )

            if promotion_result.decision == "pending":
                outcome = "pending"
                return {
                    "status": "pending",
                    "id": crystal_id,
                    "layer": "semantic",
                    "promotion_id": promotion_result.promotion_id,
                    "reason": promotion_result.reason,
                }

            if promotion_result.decision == "reject":
                outcome = "rejected"
                return {
                    "status": "rejected",
                    "reason_code": "PROMOTION_GATE",
                    "detail": promotion_result.reason,
                }

            # decision == "admit" — proceed with storage
            now = datetime.now(timezone.utc)
            payload_bytes = json.dumps(payload, default=str).encode()
            content_ref = self.blob_store.put(payload_bytes)

            scope = payload.get("scope", {})
            summary = payload.get("summary", "")

            utility = {
                "access_count": 0,
                "last_access": now.isoformat(),
                "outcome_success_score": None,
                "importance": 0.0,
                "novelty_at_write": payload.get("novelty_at_write", 0.5),
            }

            from crystalium.protection import resolve_protection
            protected, tags = resolve_protection(payload, prov_dict.get("source"))

            crystal_record: dict[str, Any] = {
                "id": crystal_id,
                "layer": "semantic",
                "content_ref": content_ref,
                "summary": summary or str(payload)[:256],
                "embedding_ref": None,
                "provenance": prov_dict,
                "trust_tier": str(caller_tier),
                "validation_state": "validated",
                "scope": scope,
                "temporal": {
                    "t_valid_from": now.isoformat(),
                    "t_valid_to": None,
                    "superseded_by": None,
                },
                "utility": utility,
                "status": "active",
                "protected": protected,
                "tags": tags,
            }

            # 5. Insert stores
            self.relational.insert_crystal(crystal_record)

            # VectorStore.upsert needs an embedded vector; embed at write time.
            if self.vector_store is not None:
                try:
                    text = summary or str(payload)[:256]
                    vec = self.vector_store.embed(text)
                    if vec:  # skip if embedder is in SKIP_SLOW mode
                        self.vector_store.upsert(
                            crystal_id=crystal_id,
                            vector=vec,
                            metadata={"layer": "semantic"},
                        )
                except Exception as exc:  # noqa: BLE001
                    log.warning("vector_insert_skipped", error=str(exc))

            if self.graph_store is not None:
                try:
                    self.graph_store.add_node(crystal_id=crystal_id, layer="semantic")
                except Exception as exc:  # noqa: BLE001
                    log.warning("graph_insert_skipped", error=str(exc))

            log.info(
                "semantic_commit",
                crystal_id=crystal_id,
                caller_tier=str(caller_tier),
            )

            return {
                "status": "committed",
                "id": crystal_id,
                "layer": "semantic",
                "validation_state": "validated",
                "importance": 0.0,
                "content_ref": content_ref,
            }

        except Exception as exc:
            outcome = "error" if not hasattr(exc, "reason_code") else "rejected"
            error_code = getattr(exc, "reason_code", type(exc).__name__)
            raise

        finally:
            self.enforcement.record(
                "crystalium.commit",
                "semantic",
                caller_tier,
                "commit",
                outcome,
                now_ms() - t0,
                error=error_code,
            )

    # ------------------------------------------------------------------
    # update — bi-temporal (P0-5, spec.md §5.3)
    # ------------------------------------------------------------------

    def update(
        self,
        record_id: str,
        patch: dict[str, Any],
        reason: str,
        caller_tier: Tier,
    ) -> CommitResult:
        """Bi-temporal update: invalidate old + write new revision.

        NEVER hard-deletes. Raises if record not found.

        Enforcement order (spec.md §5.3):
          1. assert_rate_limit
          2. assert_tier_allowed(op='commit')  [treated as commit-of-new-revision]
          3. fetch existing
          4. mark_superseded(old_id, new_id, t_valid_to=now)
          5. insert new revision with t_valid_from=now
          6. record (finally)

        Returns:
            CommitResult for the new revision.

        Raises:
            KeyError: If record_id does not exist.
        """
        t0 = now_ms()
        outcome = "ok"
        error_code: str | None = None

        try:
            # 1. Rate limit
            self.enforcement.assert_rate_limit()

            # 2. Tier check
            self.enforcement.assert_tier_allowed(
                "crystalium.update", "semantic", caller_tier, "commit"
            )

            # 3. Fetch existing
            existing = self.relational.get_crystal(record_id)
            if existing is None:
                raise KeyError(f"Crystal not found: {record_id!r}")

            now = datetime.now(timezone.utc)
            new_id = str(uuid.uuid4())

            # 4. Invalidate old (bi-temporal: set t_valid_to + superseded_by)
            self.relational.mark_superseded(record_id, new_id, now)

            # 5. Build and insert new revision
            new_record = dict(existing)
            new_record.update(patch)
            new_record["id"] = new_id
            new_record["status"] = "active"

            temporal = new_record.get("temporal") or {}
            if isinstance(temporal, str):
                import json as _json
                temporal = _json.loads(temporal)
            temporal["t_valid_from"] = now.isoformat()
            temporal["t_valid_to"] = None
            temporal["superseded_by"] = None
            new_record["temporal"] = temporal
            new_record["trust_tier"] = str(caller_tier)
            new_record["validation_state"] = "validated"

            # Update utility timestamp
            utility = new_record.get("utility") or {}
            if isinstance(utility, str):
                import json as _json
                utility = _json.loads(utility)
            utility["last_access"] = now.isoformat()
            new_record["utility"] = utility

            # Persist updated payload
            import json as _json
            payload_bytes = _json.dumps(
                {k: v for k, v in new_record.items() if k != "content_ref"},
                default=str,
            ).encode()
            content_ref = self.blob_store.put(payload_bytes)
            new_record["content_ref"] = content_ref

            self.relational.insert_crystal(new_record)

            log.info(
                "semantic_update",
                old_id=record_id,
                new_id=new_id,
                caller_tier=str(caller_tier),
                reason=reason,
            )

            return {
                "status": "committed",
                "id": new_id,
                "layer": "semantic",
                "validation_state": "validated",
                "supersedes": record_id,
                "importance": (utility.get("importance") or 0.0),
                "content_ref": content_ref,
            }

        except Exception as exc:
            outcome = "error" if not hasattr(exc, "reason_code") else "rejected"
            error_code = getattr(exc, "reason_code", type(exc).__name__)
            raise

        finally:
            self.enforcement.record(
                "crystalium.update",
                "semantic",
                caller_tier,
                "update",
                outcome,
                now_ms() - t0,
                error=error_code,
            )
