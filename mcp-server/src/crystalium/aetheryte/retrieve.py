"""Aetheryte — hybrid BM25 + dense + graph recall with RRF fusion.

Implements the crystalium.recall tool surface (spec.yaml §tool_surface, §6).

Architecture:
  Sparse arm  — BM25 over RelationalStore.bm25_search (FTS5 / SQLite)
  Dense arm   — ANN over VectorStore.dense_search (LanceDB / BGE-m3)
  Graph arm   — neighbour expansion from top dense hits via GraphStore.neighbor_expand
  Fusion      — Reciprocal Rank Fusion (RRF, k_rrf=60) merges all three arms
  Reranker    — BGE-reranker-v2-m3 stub, default DISABLED (spec §reranker_enabled_when_k_gt)
  Scope       — project + agent_class_visibility filter applied after fusion
  Composer    — slot-budgeted output (composer.compose returns ComposedSet)
  Redactor    — per-record summary redaction (P0-12)

P0 anchors:
  P0-7  : assert_rate_limit + assert_no_path_escape + telemetry on every call
  P0-9  : composer enforces ≤3500 token working set
  P0-12 : redactor applied at retrieval output

Source: spec.yaml §tool_surface crystalium.recall, §P0-7/P0-9/P0-12.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, TYPE_CHECKING

import structlog

from crystalium.enforcement import Enforcement
from crystalium.aetheryte.redact import Redactor
from crystalium.schemas import RecallResult, SlotBreakdown, CrystalSummary, Scope
from crystalium.telemetry import now_ms, record_call
from crystalium.trust import Tier

if TYPE_CHECKING:
    from crystalium.composer import Composer
    from crystalium.storage.relational import RelationalStore
    from crystalium.storage.vector import VectorStore
    from crystalium.storage.graph import GraphStore

log = structlog.get_logger("crystalium.aetheryte.retrieve")

# All four valid layer names (from trust.py VALID_LAYERS)
_ALL_LAYERS: list[str] = ["episodic", "semantic", "procedural", "execution"]


# ---------------------------------------------------------------------------
# Pure RRF fusion function (tested independently in test_rrf.py)
# ---------------------------------------------------------------------------


def rrf_merge(
    rankings: list[list[str]],
    k_rrf: int = 60,
) -> list[str]:
    """Reciprocal Rank Fusion (RRF) over multiple ranked lists of record IDs.

    For each candidate ID across all ranking lists:
        score(id) = sum( 1 / (k_rrf + rank_i(id)) )   for each list i where id appears
    where rank_i is 1-based.

    Returns IDs sorted by descending RRF score (best first).
    Ties broken by the natural dict-insertion order (deterministic for Python 3.7+).

    Args:
        rankings: List of ranked lists (each list is already ordered best-first).
                  Duplicates within a single list are allowed but unusual.
        k_rrf:    RRF smoothing constant (default 60, as per Cormack et al. 2009).

    Returns:
        Deduplicated list of IDs sorted by descending RRF score.
    """
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank_0, record_id in enumerate(ranking):
            rank_1 = rank_0 + 1  # 1-based
            scores[record_id] = scores.get(record_id, 0.0) + 1.0 / (k_rrf + rank_1)

    return sorted(scores, key=lambda x: scores[x], reverse=True)


# ---------------------------------------------------------------------------
# Aetheryte
# ---------------------------------------------------------------------------


class Aetheryte:
    """Hybrid retrieval layer for CRYSTALIUM.

    Orchestrates BM25 (sparse) + dense ANN + graph-expand arms, fuses them via
    RRF, optionally reranks, then routes through the Composer for slot eviction
    and the Redactor for PII/secrets masking.

    Args:
        relational:    RelationalStore (bm25_search + crystal metadata).
        vector_store:  VectorStore (dense_search + embed).
        graph_store:   GraphStore (neighbor_expand).
        enforcement:   Enforcement instance (rate limit + tier check + telemetry).
        redactor:      Redactor (regex pre-pass per record).
        importance_fn: Callable(record, now) → float from importance.py.
        composer:      Composer for slot-budget assembly.
    """

    def __init__(
        self,
        relational: "RelationalStore",
        vector_store: "VectorStore",
        graph_store: "GraphStore",
        enforcement: Enforcement,
        redactor: Redactor,
        importance_fn: Callable[..., float],
        composer: "Composer",
        persist_dynamics: bool = False,
        forgetting_fsrs: bool = False,
        r_floor: float = 0.7,
        fsrs_boost_factor: float = 1.5,
        fsrs_initial_stability: float = 2.0,
        fsrs_initial_difficulty: float = 0.3,
        fsrs_lapse_stability: float = 0.5,
        completion: bool = False,
        completion_max_hops: int = 2,
        completion_decay: float = 0.5,
        context_match: bool = False,
        recall_cache: Any = None,
    ) -> None:
        self.relational = relational
        self.vector_store = vector_store
        self.graph_store = graph_store
        self.enforcement = enforcement
        self.redactor = redactor
        self.importance_fn = importance_fn
        self.composer = composer
        # W2: when True (evb_enabled), recall recomputes + persists EVB for each
        # returned crystal. The access-count bump itself is unconditional (a layer
        # fact applied to BOTH A/B arms so the only difference is the scorer).
        self.persist_dynamics = persist_dynamics
        # W4: when True (forgetting_fsrs), recall boosts FSRS stability / resets on lapse.
        self.forgetting_fsrs = forgetting_fsrs
        self.r_floor = r_floor
        self.fsrs_boost_factor = fsrs_boost_factor
        self.fsrs_initial_stability = fsrs_initial_stability
        self.fsrs_initial_difficulty = fsrs_initial_difficulty
        self.fsrs_lapse_stability = fsrs_lapse_stability
        # W5 retrieval faculties (default off).
        self.completion = completion
        self.completion_max_hops = completion_max_hops
        self.completion_decay = completion_decay
        self.context_match = context_match
        self.recall_cache = recall_cache  # shared RecallCache (W5 prefetch); None = off

    # ------------------------------------------------------------------
    # Public recall API
    # ------------------------------------------------------------------

    def recall(
        self,
        scope: Scope,
        query: str,
        k: int,
        layers: list[str] | None,
        caller_tier: Tier,
    ) -> RecallResult:
        """Hybrid recall — BM25 + dense + graph-expand, fused via RRF.

        Enforcement order (spec.yaml §tool_surface.crystalium.recall):
          1. assert_rate_limit
          2. assert_tier_allowed(op="recall") — universally allowed; runs for telemetry
          3. Hybrid retrieve per layer subset
          4. RRF fusion
          5. Optional reranker (stub, default DISABLED; see docstring below)
          6. Scope filter (project + agent_class_visibility)
          7. composer.compose(records) → slot-budgeted output
          8. redactor.redact(summary, sensitivity_tag) per record
          9. Return RecallResult

        Reranker note:
            BGE-reranker-v2-m3 is stubbed behind config.reranker_enabled (default
            False) and only activates when len(candidates) > 20.
            [UNVERIFIED] reranker API shape — not validated against a pinned version.
            Full implementation deferred to v0.2 when heavy model stack is baselined.

        Args:
            scope:       Project + agent_class_visibility + sensitivity_tag triple.
            query:       Free-text recall query.
            k:           Target number of records to return.
            layers:      Subset of layers to search (None = all four layers).
            caller_tier: Trust tier of the calling agent (universally allowed for recall).

        Returns:
            RecallResult with records, slot_breakdown, total_tokens, evicted_count.
        """
        t0 = now_ms()
        outcome = "ok"
        error_code: str | None = None

        try:
            # 1. Rate limit
            self.enforcement.assert_rate_limit()

            # 2. Tier check (universally allowed; runs for telemetry only)
            self.enforcement.assert_tier_allowed(
                "crystalium.recall", "episodic", caller_tier, "recall"
            )

            # 2b. W5 prefetch: serve a pre-warmed result from the recall cache.
            # The chokepoint above still ran. Off (recall_cache None) -> no cache.
            if self.recall_cache is not None:
                cached = self.recall_cache.get(getattr(scope, "project", None), query)
                if cached is not None:
                    return cached

            target_layers = layers if layers else _ALL_LAYERS

            # 3. Hybrid retrieve per layer
            all_candidates: dict[str, dict[str, Any]] = {}  # id → crystal dict
            sparse_ranking: list[str] = []
            dense_ranking: list[str] = []
            graph_ranking: list[str] = []

            candidate_k = max(k * 3, 10)

            for layer in target_layers:
                # Sparse arm: BM25
                bm25_hits = self.relational.bm25_search(
                    query, layer_filter=layer, k=candidate_k
                )
                for hit in bm25_hits:
                    cid = hit["id"]
                    all_candidates[cid] = hit
                    if cid not in sparse_ranking:
                        sparse_ranking.append(cid)

                # Dense arm: ANN
                query_vec: list[float] = []
                try:
                    query_vec = self.vector_store.embed(query)
                except Exception as exc:
                    log.warning("embed_skipped", layer=layer, error=str(exc))

                if query_vec:
                    dense_hits = self.vector_store.dense_search(
                        query_vec=query_vec, layer_filter=layer, k=candidate_k
                    )
                    # Dense hits from LanceDB carry 'id' (or metadata id) and '_distance'
                    for hit in dense_hits:
                        cid = hit.get("id", "")
                        if not cid:
                            continue
                        if cid not in all_candidates:
                            # Fetch full record from relational if not already loaded
                            full = self.relational.get_crystal(cid)
                            if full:
                                all_candidates[cid] = full
                        if cid not in dense_ranking:
                            dense_ranking.append(cid)

            # Graph-expand: neighbours of top-k dense hits (depth=1)
            seed_ids = dense_ranking[:k]
            if seed_ids:
                try:
                    neighbour_ids = self.graph_store.neighbor_expand(
                        seed_ids=seed_ids, depth=1
                    )
                    for nid in neighbour_ids:
                        if nid not in all_candidates:
                            full = self.relational.get_crystal(nid)
                            if full:
                                all_candidates[nid] = full
                        if nid not in graph_ranking:
                            graph_ranking.append(nid)
                except Exception as exc:
                    log.warning("graph_expand_skipped", error=str(exc))

            # 3b. W5 pattern completion: bounded, decaying multi-hop graph walk from
            # the top seeds (CA3-analogue). Off -> no 4th arm (byte-identical RRF).
            completion_ranking: list[str] = []
            if self.completion:
                completion_seeds = seed_ids or sparse_ranking[:k]
                if completion_seeds:
                    try:
                        walked = self.graph_store.decaying_walk(
                            seed_ids=completion_seeds,
                            max_hops=self.completion_max_hops,
                            decay=self.completion_decay,
                        )
                        for cid, _score in sorted(walked.items(), key=lambda kv: -kv[1]):
                            if cid not in all_candidates:
                                full = self.relational.get_crystal(cid)
                                if full:
                                    all_candidates[cid] = full
                            if cid in all_candidates and cid not in completion_ranking:
                                completion_ranking.append(cid)
                    except Exception as exc:
                        log.warning("completion_walk_skipped", error=str(exc))

            # 4. RRF fusion (completion is a 4th ranked list only when non-empty)
            rankings = [sparse_ranking, dense_ranking, graph_ranking]
            if completion_ranking:
                rankings.append(completion_ranking)
            fused_ids = rrf_merge(rankings, k_rrf=60)

            # 4b. W5 encoding-specificity: post-RRF re-rank that boosts crystals
            # whose stored encoding_context overlaps the scope-derived query context
            # (Tulving & Thomson 1973). Stable sort -> RRF order preserved within
            # equal context-match. Off -> fused order unchanged (byte-identical).
            if self.context_match:
                q_ctx = {
                    "project": getattr(scope, "project", None),
                    "agent_class": getattr(scope, "agent_class_visibility", None),
                }

                def _ctx_overlap(cid: str) -> int:
                    ec = all_candidates.get(cid, {}).get("encoding_context")
                    if not isinstance(ec, dict):
                        return 0
                    return sum(
                        1 for key in ("project", "agent_class")
                        if q_ctx.get(key) is not None and ec.get(key) == q_ctx.get(key)
                    )

                fused_ids = sorted(fused_ids, key=lambda cid: -_ctx_overlap(cid))

            # 5. Optional reranker stub (default DISABLED, never executes in v0.1)
            # Reranker is BGE-reranker-v2-m3; activated when:
            #   len(candidates) > 20 AND config.reranker_enabled is True
            # [UNVERIFIED] reranker API shape — deferred to v0.2.
            # if len(fused_ids) > 20 and getattr(self.enforcement.config, "reranker_enabled", False):
            #     fused_ids = self._rerank(query, fused_ids, all_candidates)  # NotImplemented v0.2

            # 6. Scope filter: project + agent_class_visibility
            def _scope_matches(crystal: dict[str, Any]) -> bool:
                raw_scope = crystal.get("scope")
                if not raw_scope:
                    return False
                if isinstance(raw_scope, str):
                    import json
                    try:
                        raw_scope = json.loads(raw_scope)
                    except Exception:
                        return False
                # Project must match
                if raw_scope.get("project") != scope.project:
                    return False
                # agent_class_visibility: if caller scope restricts, filter by it
                if scope.agent_class_visibility is not None:
                    crystal_acv = raw_scope.get("agent_class_visibility")
                    # "all" means visible to all agent classes
                    if crystal_acv is not None and crystal_acv not in ("all", scope.agent_class_visibility):
                        return False
                return True

            filtered_ids = [
                cid for cid in fused_ids
                if cid in all_candidates and _scope_matches(all_candidates[cid])
            ]

            # Build Crystal-like objects for the composer
            now = datetime.now(timezone.utc)

            def _to_composer_record(crystal: dict[str, Any]) -> "_ComposerRecord":
                utility = crystal.get("utility", {})
                if isinstance(utility, str):
                    import json
                    utility = json.loads(utility)
                last_access_raw = utility.get("last_access", now.isoformat())
                if isinstance(last_access_raw, str):
                    try:
                        last_access_dt = datetime.fromisoformat(last_access_raw)
                        if last_access_dt.tzinfo is None:
                            last_access_dt = last_access_dt.replace(tzinfo=timezone.utc)
                    except (ValueError, TypeError):
                        last_access_dt = now
                else:
                    last_access_dt = now

                raw_scope = crystal.get("scope", {})
                if isinstance(raw_scope, str):
                    import json
                    try:
                        raw_scope = json.loads(raw_scope)
                    except Exception:
                        raw_scope = {}

                # W2: when a persisted EVB value exists (written by Dream/recall
                # under evb_enabled), it is the composer's single source of truth
                # for ranking. Legacy path is byte-identical: with no evb, fall back
                # to utility.importance exactly as before.
                md = crystal.get("memory_dynamics")
                if isinstance(md, str):
                    import json
                    try:
                        md = json.loads(md)
                    except Exception:
                        md = None
                evb_cached = md.get("evb") if isinstance(md, dict) else None
                importance_val = (
                    float(evb_cached)
                    if evb_cached is not None
                    else float(utility.get("importance", 0.0))
                )

                return _ComposerRecord(
                    id=crystal["id"],
                    layer=crystal.get("layer", "episodic"),
                    summary=crystal.get("summary", ""),
                    trust_tier=crystal.get("trust_tier", "T1"),
                    validation_state=crystal.get("validation_state", "unverified"),
                    importance=importance_val,
                    last_access=last_access_dt,
                    content_ref=crystal.get("content_ref"),
                    scope_sensitivity_tag=(raw_scope.get("sensitivity_tag") or "none"),
                    slot_override=raw_scope.get("slot"),
                )

            composer_records = [
                _to_composer_record(all_candidates[cid])
                for cid in filtered_ids
                if cid in all_candidates
            ]

            # 7. Composer: slot-budgeted assembly
            composed = self.composer.compose(composer_records)

            # 7b. W2 access event: bump access_count/last_access for every surfaced
            # crystal (a layer fact recorded in BOTH A/B arms). Under evb_enabled,
            # recompute + persist EVB so Need's access-frequency term and the
            # composer's evb cache stay fresh. Never let this break a recall.
            for rec in composed.records:
                try:
                    # W4: successful recall boosts FSRS stability (reconsolidation),
                    # or resets it on a lapse. Computed from the PRE-recall last_access
                    # (rec.last_access) BEFORE record_access bumps it.
                    if self.forgetting_fsrs:
                        from crystalium import fsrs as _fsrs

                        full = self.relational.get_crystal(rec.id)
                        md = (full or {}).get("memory_dynamics") or {}
                        elapsed = _fsrs.elapsed_days(rec.last_access, now)
                        new_s, r = _fsrs.on_recall(
                            md.get("stability"),
                            elapsed,
                            r_floor=self.r_floor,
                            boost_factor=self.fsrs_boost_factor,
                            initial_stability=self.fsrs_initial_stability,
                            lapse_stability=self.fsrs_lapse_stability,
                        )
                        self.relational.update_dynamics(rec.id, {
                            "stability": new_s,
                            "retrievability": r,
                            "difficulty": md.get("difficulty", self.fsrs_initial_difficulty),
                        })
                    self.relational.record_access(rec.id, now=now)
                    if self.persist_dynamics:
                        full = self.relational.get_crystal(rec.id)
                        if full is not None:
                            stub = _AccessStub(full.get("utility", {}), now)
                            self.relational.update_dynamics(
                                rec.id, {"evb": self.importance_fn(stub, now=now)}
                            )
                except Exception:
                    log.warning("recall_access_event_failed", crystal_id=rec.id)

            # 8. Redactor: per-record summary redaction (P0-12)
            result_records: list[CrystalSummary] = []
            for rec in composed.records:
                redacted_summary = self.redactor.redact(
                    rec.summary, rec.scope_sensitivity_tag
                )
                result_records.append(
                    CrystalSummary(
                        id=rec.id,
                        layer=rec.layer,  # type: ignore[arg-type]
                        summary=redacted_summary,
                        trust_tier=rec.trust_tier,  # type: ignore[arg-type]
                        validation_state=rec.validation_state,  # type: ignore[arg-type]
                        importance=rec.importance,
                        last_access=rec.last_access,
                        content_ref=rec.content_ref,
                    )
                )

            result = RecallResult(
                records=result_records,
                slot_breakdown=SlotBreakdown(
                    executive=composed.slot_tokens.get("executive", 0),
                    procedural=composed.slot_tokens.get("procedural", 0),
                    semantic=composed.slot_tokens.get("semantic", 0),
                    episodic=composed.slot_tokens.get("episodic", 0),
                    execution=composed.slot_tokens.get("execution", 0),
                    buffer=composed.slot_tokens.get("buffer", 0),
                ),
                total_tokens=composed.total_tokens,
                evicted_count=composed.evicted_count,
            )

            log.info(
                "recall_ok",
                query_len=len(query),
                k=k,
                layers=target_layers,
                candidates=len(all_candidates),
                filtered=len(filtered_ids),
                after_compose=len(result_records),
                evicted=composed.evicted_count,
                caller_tier=str(caller_tier),
            )

            # W5 prefetch: cache this (cold) result so a pre-warmed read hits later.
            if self.recall_cache is not None:
                self.recall_cache.put(getattr(scope, "project", None), query, result)

            return result

        except Exception as exc:
            outcome = "error" if not hasattr(exc, "reason_code") else "rejected"
            error_code = getattr(exc, "reason_code", type(exc).__name__)
            raise

        finally:
            record_call(
                tool="crystalium.recall",
                layer=None,
                tier=str(caller_tier),
                op="recall",
                result=outcome,
                latency_ms=now_ms() - t0,
                error=error_code,
            )


class _AccessStub:
    """Minimal MemoryRecord built from a crystal's utility JSON for EVB recompute."""

    __slots__ = ("access_count", "last_access", "outcome_success", "novelty_at_write")

    def __init__(self, util: dict[str, Any], now: datetime) -> None:
        self.access_count = int(util.get("access_count", 0))
        la = util.get("last_access")
        if isinstance(la, str):
            try:
                self.last_access = datetime.fromisoformat(la)
            except ValueError:
                self.last_access = now
        else:
            self.last_access = now
        self.outcome_success = util.get("outcome_success_score")
        self.novelty_at_write = float(util.get("novelty_at_write", 0.0))


# ---------------------------------------------------------------------------
# Internal record type for the recall → composer pipeline
# ---------------------------------------------------------------------------


class _ComposerRecord:
    """Lightweight record passed from Aetheryte to Composer.

    Holds only the fields needed for slot assignment, importance scoring,
    and redaction. Not a Pydantic model — avoids schema validation overhead
    in the hot retrieval path.
    """

    __slots__ = (
        "id",
        "layer",
        "summary",
        "trust_tier",
        "validation_state",
        "importance",
        "last_access",
        "content_ref",
        "scope_sensitivity_tag",
        "slot_override",
        # MemoryRecord protocol fields (for importance_score compatibility)
        "access_count",
        "outcome_success",
        "novelty_at_write",
    )

    def __init__(
        self,
        id: str,
        layer: str,
        summary: str,
        trust_tier: str,
        validation_state: str,
        importance: float,
        last_access: datetime,
        content_ref: str | None,
        scope_sensitivity_tag: str,
        slot_override: str | None = None,
    ) -> None:
        self.id = id
        self.layer = layer
        self.summary = summary
        self.trust_tier = trust_tier
        self.validation_state = validation_state
        self.importance = importance
        self.last_access = last_access
        self.content_ref = content_ref
        self.scope_sensitivity_tag = scope_sensitivity_tag
        self.slot_override = slot_override
        # MemoryRecord protocol stubs (used by importance_score)
        self.access_count = 0
        self.outcome_success: float | None = None
        self.novelty_at_write: float = 0.5
