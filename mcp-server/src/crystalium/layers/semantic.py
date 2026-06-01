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
        dedup_merge: bool = False,
        sep_threshold: float = 0.92,
        link_cooccurrence: bool = False,
        cooccurrence_limit: int = 5,
        write_conflict_detect: bool = False,
        conflict_tau_lo: float = 0.80,
    ) -> None:
        self.blob_store = blob_store
        self.relational = relational
        self.vector_store = vector_store
        self.graph_store = graph_store
        self.enforcement = enforcement
        self.gate = gate
        self.redactor = redactor
        self.importance_fn = importance_fn
        # W5 retrieval (default off): write-time dedup-merge + co-occurrence edges.
        self.dedup_merge = dedup_merge
        self.sep_threshold = sep_threshold
        self.link_cooccurrence = link_cooccurrence
        self.cooccurrence_limit = cooccurrence_limit
        # W6 multi-agent write-conflict detection (default off). A near-duplicate
        # that is same-subject (cosine in [conflict_tau_lo, sep_threshold)) but
        # DIVERGENT in content is a conflict, not a corroboration -> last-write-wins.
        self.write_conflict_detect = write_conflict_detect
        self.conflict_tau_lo = conflict_tau_lo

    def _conflict_target(self, text: str, scope: dict) -> dict | None:
        """W6 write-conflict: a same-project, same-subject prior whose content
        DIVERGES from *text* — cosine in [conflict_tau_lo, sep_threshold) (similar
        enough to be the same subject, below the dedup threshold so it isn't a
        near-duplicate) and a different summary. Returns {id, similarity, summary,
        tier} of the loser, or None. Best-effort; None on any miss/error."""
        if not self.write_conflict_detect or self.vector_store is None or not text:
            return None
        project = scope.get("project") if isinstance(scope, dict) else None
        if not project:
            return None
        try:
            vec = self.vector_store.embed(text)
            if not vec:
                return None
            hits = self.vector_store.dense_search(vec, layer_filter="semantic", k=5)
            for hit in hits:
                dist = hit.get("_distance")
                if dist is None:
                    continue
                sim = 1.0 - float(dist)
                if not (self.conflict_tau_lo <= sim < self.sep_threshold):
                    continue
                prior = self.relational.get_crystal(hit.get("id"))
                if not prior or prior.get("status") != "active":
                    continue
                pscope = prior.get("scope") or {}
                if (pscope.get("project") if isinstance(pscope, dict) else None) != project:
                    continue
                if (prior.get("summary") or "") == text:
                    continue  # identical summary -> not a divergence
                return {
                    "id": prior["id"],
                    "similarity": sim,
                    "summary": prior.get("summary"),
                    "tier": prior.get("trust_tier"),
                }
        except Exception as exc:  # noqa: BLE001
            log.debug("conflict_check_skipped", error=str(exc))
        return None

    def _dedup_target(self, text: str, layer: str) -> str | None:
        """W5 pattern separation: id of an existing near-duplicate (cosine >
        sep_threshold) in *layer*, or None (cosine_sim = 1 - _distance)."""
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
        """W5 D1: link this crystal to recent same-project crystals via LINKS_TO."""
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

            # 3. D7/G4 ceiling guard — defense-in-depth. On a DIRECT commit the tier
            # matrix above (step 2) already denies T2/T3 for Semantic, so caller_tier
            # is necessarily <= the T1 ceiling here and this never raises. It is kept
            # as a cheap belt-and-suspenders re-check; the load-bearing G4 enforcement
            # for MULTI-SOURCE consolidation (where a min-trust tier can exceed the
            # ceiling) lives in DreamWorker._consolidate, which calls the same guard.
            self.enforcement.assert_tier_within_layer_ceiling(caller_tier, "semantic")

            prov_dict = (
                provenance.model_dump()
                if hasattr(provenance, "model_dump")
                else dict(provenance)
            )

            # W5 pattern separation: a near-duplicate semantic fact merges its
            # provenance (corroboration bump) into the existing crystal instead of
            # a blind append. After the chokepoint (rate-limit + tier + ceiling),
            # before the gate/insert. Off / null vector store -> normal flow.
            dup_id = self._dedup_target(payload.get("summary", "") or str(payload)[:256], "semantic")
            if dup_id is not None:
                self.relational.merge_provenance(dup_id, prov_dict)
                log.info("semantic_dedup_merged", merged_into=dup_id)
                return {
                    "status": "merged",
                    "id": dup_id,
                    "layer": "semantic",
                    "merged_into": dup_id,
                    "validation_state": "validated",
                    "importance": 0.0,
                }

            # W6 write-conflict detection: a same-subject-but-divergent prior is a
            # conflict (not a corroboration). Detected here in the dedup slot (after
            # the chokepoint); resolved last-write-wins AFTER the new crystal is
            # admitted + inserted below. Off / null vector store -> None.
            conflict_loser = self._conflict_target(
                payload.get("summary", "") or str(payload)[:256],
                payload.get("scope", {}),
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

            from crystalium.protection import resolve_encoding_context, resolve_protection
            protected, tags = resolve_protection(payload, prov_dict.get("source"))
            enc_ctx = resolve_encoding_context(payload, prov_dict, scope)

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
                "encoding_context": enc_ctx,
            }

            # 5. Insert stores
            self.relational.insert_crystal(crystal_record)

            # W6 conflict resolution: last-write-wins. The new (just-inserted)
            # crystal wins; bi-temporally supersede the divergent prior and record
            # BOTH lineages (+ both tiers) so the conflict is surfaced, never
            # silently absorbed as corroboration. Pure LWW ignores trust ordering —
            # the recorded tiers make any inversion auditable.
            conflict_recorded = False
            if conflict_loser is not None:
                try:
                    self.relational.mark_superseded(conflict_loser["id"], crystal_id, now)
                    self.relational.record_conflict(
                        crystal_id, conflict_loser["id"],
                        winner_summary=summary or str(payload)[:256],
                        loser_summary=conflict_loser.get("summary"),
                        winner_tier=str(caller_tier),
                        loser_tier=conflict_loser.get("tier"),
                        similarity=conflict_loser.get("similarity"),
                        scope=scope if isinstance(scope, dict) else None,
                        now=now,
                    )
                    conflict_recorded = True
                    log.info(
                        "semantic_write_conflict",
                        winner=crystal_id, loser=conflict_loser["id"],
                        similarity=conflict_loser.get("similarity"),
                    )
                except Exception as exc:  # noqa: BLE001
                    log.warning("conflict_resolution_skipped", error=str(exc))

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
                self._link_cooccurrence(crystal_id, scope)

            log.info(
                "semantic_commit",
                crystal_id=crystal_id,
                caller_tier=str(caller_tier),
            )

            result: dict[str, Any] = {
                "status": "committed",
                "id": crystal_id,
                "layer": "semantic",
                "validation_state": "validated",
                "importance": 0.0,
                "content_ref": content_ref,
            }
            if conflict_recorded:
                result["conflict_superseded"] = conflict_loser["id"]
            return result

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
                from crystalium.enforcement import CrystaliumEnforcementError
                raise CrystaliumEnforcementError(
                    f"Crystal not found: {record_id!r}", reason_code="CRYSTAL_NOT_FOUND"
                )

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
