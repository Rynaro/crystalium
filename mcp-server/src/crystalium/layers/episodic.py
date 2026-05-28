"""Episodic layer adapter — raw capture buffer, universally writable.

T3 callers land as quarantined (enforcement-side _MARK_QUARANTINE flag).
Bi-temporal updates: invalidate old → write new (never hard-delete, P0-5).

All writes funnel through enforcement.assert_tier_allowed() before any store call.
record() is called in finally blocks for telemetry (P0-7).

Source: spec.md §4 (tier matrix row: Episodic), FORGE D1.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Callable

import structlog

from crystalium.enforcement import Enforcement
from crystalium.aetheryte.redact import Redactor
from crystalium.schemas import Provenance
from crystalium.telemetry import now_ms
from crystalium.trust import Tier

log = structlog.get_logger("crystalium.layers.episodic")

# Type aliases
CommitResult = dict[str, Any]


class EpisodicLayer:
    """Episodic memory layer — the raw capture buffer.

    Args:
        blob_store:     BlobStore for full payload persistence.
        relational:     RelationalStore for metadata index.
        vector_store:   VectorStore for embedding index (optional; may be None in tests).
        graph_store:    GraphStore for relationship index (optional; may be None in tests).
        enforcement:    Enforcement instance (tier checks + telemetry).
        redactor:       Redactor for cross-agent handoff re-redaction.
        importance_fn:  Callable(record, now) -> float from importance.py.
    """

    def __init__(
        self,
        blob_store: Any,
        relational: Any,
        vector_store: Any,
        graph_store: Any,
        enforcement: Enforcement,
        redactor: Redactor,
        importance_fn: Callable[..., float],
    ) -> None:
        self.blob_store = blob_store
        self.relational = relational
        self.vector_store = vector_store
        self.graph_store = graph_store
        self.enforcement = enforcement
        self.redactor = redactor
        self.importance_fn = importance_fn

    # ------------------------------------------------------------------
    # commit (P0-1: raw capture; quarantine if T3)
    # ------------------------------------------------------------------

    def commit(
        self,
        payload: dict[str, Any],
        provenance: Provenance | dict[str, Any],
        caller_tier: Tier,
    ) -> CommitResult:
        """Commit a new crystal to the Episodic layer.

        Enforcement order (spec.md §5.2):
          1. assert_rate_limit
          2. assert_tier_allowed(layer='episodic', op='commit')
          3. quarantine flag consumed → validation_state set
          4. put blob
          5. insert relational + vector + graph
          6. record telemetry (finally)

        T3 callers land with validation_state='quarantined' (P0-1 / P0-2).
        T0/T1/T2 callers land with validation_state='unverified' (Episodic raw buffer).

        Returns:
            CommitResult dict with status, id, layer, validation_state, importance.
        """
        t0 = now_ms()
        outcome = "ok"
        error_code: str | None = None

        try:
            # 1. Rate limit
            self.enforcement.assert_rate_limit()

            # 2. Tier check
            self.enforcement.assert_tier_allowed(
                "crystalium.commit", "episodic", caller_tier, "commit"
            )

            # 3. Consume quarantine flag
            quarantined = self.enforcement.quarantine_active()
            self.enforcement.reset_quarantine_flag()

            validation_state = "quarantined" if quarantined else "unverified"

            # Build crystal record
            crystal_id = str(uuid.uuid4())
            now = datetime.now(timezone.utc)

            prov_dict = (
                provenance.model_dump()
                if hasattr(provenance, "model_dump")
                else dict(provenance)
            )

            # Serialize payload to bytes for blob storage
            import json
            payload_bytes = json.dumps(payload, default=str).encode()

            # 4. Put blob (P0-8: index→pointer→content)
            content_ref = self.blob_store.put(payload_bytes)

            # Build utility stub (importance scored from access baseline)
            utility = {
                "access_count": 0,
                "last_access": now.isoformat(),
                "outcome_success_score": None,
                "importance": 0.0,
                "novelty_at_write": payload.get("novelty_at_write", 0.5),
            }

            scope = payload.get("scope", {})
            summary = payload.get("summary", "")

            crystal_record: dict[str, Any] = {
                "id": crystal_id,
                "layer": "episodic",
                "content_ref": content_ref,
                "summary": summary or str(payload)[:256],
                "embedding_ref": None,
                "provenance": prov_dict,
                "trust_tier": str(caller_tier),
                "validation_state": validation_state,
                "scope": scope,
                "temporal": {
                    "t_valid_from": now.isoformat(),
                    "t_valid_to": None,
                    "superseded_by": None,
                },
                "utility": utility,
                "status": "active",
            }

            # 5. Insert into relational store
            self.relational.insert_crystal(crystal_record)

            # Vector + graph stores are optional (may not be available in tests).
            # VectorStore.upsert needs an embedded vector; embed at write time so
            # the dense recall arm has something to find.
            if self.vector_store is not None:
                try:
                    text = summary or str(payload)[:256]
                    vec = self.vector_store.embed(text)
                    if vec:  # skip if embedder is in SKIP_SLOW mode
                        self.vector_store.upsert(
                            crystal_id=crystal_id,
                            vector=vec,
                            metadata={"layer": "episodic"},
                        )
                except Exception as exc:  # noqa: BLE001
                    log.warning("vector_insert_skipped", error=str(exc))

            if self.graph_store is not None:
                try:
                    self.graph_store.add_node(crystal_id=crystal_id, layer="episodic")
                except Exception as exc:  # noqa: BLE001
                    log.warning("graph_insert_skipped", error=str(exc))

            log.info(
                "episodic_commit",
                crystal_id=crystal_id,
                validation_state=validation_state,
                caller_tier=str(caller_tier),
            )

            return {
                "status": "committed",
                "id": crystal_id,
                "layer": "episodic",
                "validation_state": validation_state,
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
                "episodic",
                caller_tier,
                "commit",
                outcome,
                now_ms() - t0,
                error=error_code,
            )

    # ------------------------------------------------------------------
    # recall (universally allowed, D1)
    # ------------------------------------------------------------------

    def recall(
        self,
        query: str,
        k: int = 10,
        scope_filter: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """BM25 recall from Episodic layer. No tier check (recall is universal).

        Used by Aetheryte (W3 full hybrid recall uses this as one branch).

        Args:
            query:        Free-text query string.
            k:            Number of results.
            scope_filter: Optional scope dict to filter by project.

        Returns:
            List of crystal dicts ordered by BM25 rank.
        """
        results = self.relational.bm25_search(query, layer_filter="episodic", k=k)

        if scope_filter and scope_filter.get("project"):
            project = scope_filter["project"]
            results = [
                r for r in results
                if (r.get("scope") or {}).get("project") == project
            ]

        return results
