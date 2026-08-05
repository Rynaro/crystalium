"""Episodic layer adapter — raw capture buffer, universally writable.

T3 callers land as quarantined (enforcement-side _MARK_QUARANTINE flag).
Bi-temporal updates: invalidate old → write new (never hard-delete, P0-5).

All writes funnel through enforcement.assert_tier_allowed() before any store call.
Telemetry (P0-7) is recorded once, by server.py's `_call_tool` dispatcher —
commit() no longer duplicates that write in its own finally block
(crystalium#35 fix-forward, v2.0.1: removed a stale-keyed double-write).

Source: spec.md §4 (tier matrix row: Episodic), FORGE D1.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Callable

import structlog

from crystalium.enforcement import Enforcement
from crystalium.aetheryte.redact import Redactor
from crystalium.importance import initial_importance
from crystalium.schemas import Provenance
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
        dedup_merge: bool = False,
        sep_threshold: float = 0.92,
        link_cooccurrence: bool = False,
        cooccurrence_limit: int = 5,
    ) -> None:
        self.blob_store = blob_store
        self.relational = relational
        self.vector_store = vector_store
        self.graph_store = graph_store
        self.enforcement = enforcement
        self.redactor = redactor
        self.importance_fn = importance_fn
        # W5 retrieval (default off): write-time dedup-merge + co-occurrence edges.
        self.dedup_merge = dedup_merge
        self.sep_threshold = sep_threshold
        self.link_cooccurrence = link_cooccurrence
        self.cooccurrence_limit = cooccurrence_limit

    def _dedup_target(self, text: str, layer: str) -> str | None:
        """W5 pattern separation: id of an existing near-duplicate (cosine >
        sep_threshold) in *layer*, or None. Cosine is pinned via dense_search
        (cosine_sim = 1 - _distance). Best-effort; None on any miss/error."""
        if not self.dedup_merge or self.vector_store is None or not text:
            return None
        try:
            vec = self.vector_store.embed(text)
            if not vec:
                return None
            hits = self.vector_store.dense_search(vec, layer_filter=layer, k=1)
            if hits:
                dist = hits[0].get("_distance")
                if dist is not None and (1.0 - float(dist)) > self.sep_threshold:
                    return hits[0].get("id")
        except Exception as exc:  # noqa: BLE001
            log.debug("dedup_check_skipped", error=str(exc))
        return None

    def _link_cooccurrence(self, crystal_id: str, scope: dict) -> None:
        """W5 D1: link this crystal to recent same-project crystals via LINKS_TO,
        so the pattern-completion walk has edges. Bounded; best-effort."""
        if not self.link_cooccurrence or self.graph_store is None:
            return
        project = scope.get("project") if isinstance(scope, dict) else None
        if not project:
            return
        try:
            for other in self.relational.recent_crystal_ids(
                project, exclude_id=crystal_id, limit=self.cooccurrence_limit
            ):
                self.graph_store.add_edge(crystal_id, other, "LINKS_TO")
        except Exception as exc:  # noqa: BLE001
            log.debug("cooccurrence_link_skipped", error=str(exc))

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

            # W5 pattern separation: if a near-duplicate already exists, merge
            # provenance in place (no new row/blob) instead of a blind append.
            # Runs AFTER the chokepoint (rate-limit + tier) above, so the gate is
            # preserved. Off / null vector store -> normal append.
            dup_id = self._dedup_target(payload.get("summary", "") or str(payload)[:256], "episodic")
            if dup_id is not None:
                self.relational.merge_provenance(dup_id, prov_dict)
                log.info("episodic_dedup_merged", merged_into=dup_id)
                # crystalium#36 (critique F2): this is a merge echo, not a fresh
                # commit — the merged-into crystal already has its own stored
                # importance. Echoing the literal 0.0 misreported it whenever the
                # absorbing crystal had earned a non-zero score; report the
                # ALREADY-STORED value instead. Out of AC-010's scope (that AC
                # covers only the non-dedup commit path) — best-effort: a lookup
                # failure falls back to 0.0 rather than raising.
                merged_crystal = self.relational.get_crystal(dup_id)
                merged_importance = (
                    float((merged_crystal.get("utility") or {}).get("importance", 0.0))
                    if merged_crystal is not None
                    else 0.0
                )
                return {
                    "status": "merged",
                    "id": dup_id,
                    "layer": "episodic",
                    "merged_into": dup_id,
                    "validation_state": validation_state,
                    "importance": merged_importance,
                }

            # Serialize payload to bytes for blob storage
            import json
            payload_bytes = json.dumps(payload, default=str).encode()

            # 4. Put blob (P0-8: index→pointer→content)
            content_ref = self.blob_store.put(payload_bytes)

            # Build utility stub (importance scored from access baseline).
            # crystalium#36 / DP-4=C: cold-start importance from the injected
            # importance_fn, clamped to COLD_START_IMPORTANCE_CEILING — no longer
            # a hardcoded 0.0 that pins a fresh crystal below every accumulated
            # record regardless of topic.
            utility = {
                "access_count": 0,
                "last_access": now.isoformat(),
                "outcome_success_score": None,
                "importance": 0.0,
                "novelty_at_write": payload.get("novelty_at_write", 0.5),
            }
            utility["importance"] = initial_importance(self.importance_fn, utility, now)

            scope = payload.get("scope", {})
            summary = payload.get("summary", "")

            from crystalium.protection import resolve_encoding_context, resolve_protection
            protected, tags = resolve_protection(payload, prov_dict.get("source"))
            enc_ctx = resolve_encoding_context(payload, prov_dict, scope)

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
                "protected": protected,
                "tags": tags,
                "encoding_context": enc_ctx,
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
                self._link_cooccurrence(crystal_id, scope)

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
                # DP-4c: echo the computed cold-start value (no longer a bare 0.0).
                "importance": utility["importance"],
                "content_ref": content_ref,
            }

        except Exception:
            # crystalium#35 fix-forward (v2.0.1): this used to record its own
            # telemetry row here (stale dotted "crystalium.commit") in a
            # `finally:` block on every call. server.py's `_call_tool`
            # dispatcher already records the SAME call under the canonical
            # name ("commit") — including layer, which the dispatcher reads
            # from the caller-supplied (schema-required) `layer` argument that
            # routed here in the first place, so it is never out of sync with
            # the "episodic" literal this block used to hardcode. op="commit"
            # carried no information the outer write lacks (the enforcement
            # matrix's op key IS the tool name here). Bare re-raise preserves
            # commit()'s exception semantics unchanged.
            raise

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
