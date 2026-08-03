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
from crystalium.schemas import Budget, RecallResult, SlotBreakdown, CrystalSummary, Scope
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


def rrf_merge_scored(
    rankings: list[list[str]],
    k_rrf: int = 60,
) -> list[tuple[str, float]]:
    """Reciprocal Rank Fusion (RRF), returning IDs WITH their fused score.

    Same fusion as `rrf_merge` (see there for the formula), but exposes the
    `scores` dict that `rrf_merge` computed and discarded (crystalium#36 seam
    1) — the raw fusion value is what lets query relevance reach the composer
    (`_ComposerRecord.relevance_score`) and the client (`CrystalSummary.score`).

    Args:
        rankings: List of ranked lists (each list is already ordered best-first).
                  Duplicates within a single list are allowed but unusual.
        k_rrf:    RRF smoothing constant (default 60, as per Cormack et al. 2009).

    Returns:
        Deduplicated list of (id, score) sorted by descending RRF score. Ties
        broken by the natural dict-insertion order (deterministic for Python
        3.7+) — identical tie behaviour to `rrf_merge`.
    """
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank_0, record_id in enumerate(ranking):
            rank_1 = rank_0 + 1  # 1-based
            scores[record_id] = scores.get(record_id, 0.0) + 1.0 / (k_rrf + rank_1)

    return sorted(scores.items(), key=lambda kv: kv[1], reverse=True)


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
    return [cid for cid, _ in rrf_merge_scored(rankings, k_rrf)]


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
        recall_active_only: bool = False,
        recall_relevance_primary: bool = True,
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
        # W6: when True, recall excludes non-active (deprecated/superseded/expired)
        # crystals — the defense that makes operator reject=deprecate and bi-temporal
        # supersession actually bite. Off -> byte-identical to W5 (returns everything).
        self.recall_active_only = recall_active_only
        # crystalium#36 / FORGE DP-1+DP-2: gates seam 3 (top-k truncation, below)
        # as one unit with the Composer's own recall_relevance_primary flag
        # (seams 4+5 — relevance-primary eviction key + descending-relevance
        # ordering). Default ON: earned by correctness (a fresh crystal must be
        # retrievable), not an ablation win. Off -> seam 3 is skipped entirely;
        # `k` is not a cap (AC-009).
        self.recall_relevance_primary = recall_relevance_primary

    # ------------------------------------------------------------------
    # Public recall API
    # ------------------------------------------------------------------

    @staticmethod
    def _cache_ctx(scope: Any, k: int, layers: Any, caller_tier: Any) -> dict[str, Any]:
        """Recall-shaping dimensions that MUST participate in the cache key, so a
        result computed under one visibility/filter is never served to another."""
        return {
            "k": k,
            "layers": layers,
            "visibility": getattr(scope, "agent_class_visibility", None),
            "sensitivity": getattr(scope, "sensitivity_tag", None),
            "tier": str(caller_tier) if caller_tier is not None else None,
        }

    def recall(
        self,
        scope: Scope,
        query: str,
        k: int,
        layers: list[str] | None,
        caller_tier: Tier,
        explain: bool = False,
    ) -> RecallResult:
        """Hybrid recall — BM25 + dense + graph-expand, fused via RRF.

        Enforcement order (spec.yaml §tool_surface.crystalium.recall):
          1. assert_rate_limit
          2. assert_tier_allowed(op="recall") — universally allowed; runs for telemetry
          3. Hybrid retrieve per layer subset
          4. RRF fusion
          4b. k-gate (recall_relevance_primary only, crystalium#36 seam 3): truncate
              the scope/status-filtered, RRF-descending candidate list to the first
              `k` entries BEFORE composition — relevance is decisive over
              *membership*, not just fetch order, so `len(result.records) <= k` is a
              hard guarantee in this mode (AC-003). Off: `k` is not a cap, exactly
              as before v1.9.0 (AC-009).
          5. Optional reranker (stub, default DISABLED; see docstring below)
          6. Scope filter (project + agent_class_visibility)
          7. composer.compose(records) → slot-budgeted output. Eviction key and
             final ordering are relevance-primary in this mode (crystalium#36
             seams 4+5 — see composer.py); flag off restores the pre-1.9.0
             (importance, last_access, id) key and per-slot artefact order.
          8. redactor.redact(summary, sensitivity_tag) per record
          9. Return RecallResult

        Ordering contract (crystalium#36 / FORGE DP-6): in the default
        configuration (recall_relevance_primary=True), `result.records` is
        emitted in non-increasing `score` order, `id` ascending as a tiebreak.
        With the flag off, the order is the historical composer artefact (per-
        slot insertion order, or ascending-eviction-key order when the global
        token-cap pass fired) — an accident of implementation, not a contract.

        Reranker note:
            BGE-reranker-v2-m3 is stubbed behind config.reranker_enabled (default
            False) and only activates when len(candidates) > 20.
            [UNVERIFIED] reranker API shape — not validated against a pinned version.
            Full implementation deferred to v0.2 when heavy model stack is baselined.

        Args:
            scope:       Project + agent_class_visibility + sensitivity_tag triple.
            query:       Free-text recall query.
            k:           Target number of records to return. A HARD CAP on
                         len(result.records) when recall_relevance_primary is
                         True (the default); the caller is responsible for
                         clamping k to [1, 100] before calling (server._handle_recall
                         / the CLI verb do this — Aetheryte.recall() itself takes
                         k as given).
            layers:      Subset of layers to search (None = all four layers).
            caller_tier: Trust tier of the calling agent (universally allowed for recall).
            explain:     v1.6 diagnosability. When True, RecallResult.explain is
                         populated with a diagnostic object:
                           {candidates_prefilter, filtered_by_status, filtered_by_scope,
                            arms: {bm25: on|off, dense: active|inactive(reason), graph: on|off},
                            store: {total_crystals, active, embedded},
                            project_keys_present: [...],
                            k_applied, truncated_by_k}
                         so a zero-record recall against a non-empty store is
                         diagnosable from the result alone (the MOTIVATING INCIDENT
                         test, CHANGELOG v1.6.0). Bypasses the recall cache in both
                         directions (never read from, never written to) so a
                         diagnostic call is always freshly computed and never
                         contaminates a normal call's cached result with a stale
                         `explain` object.

        Returns:
            RecallResult with records (each carrying a non-null `score`,
            crystalium#36 / D3), slot_breakdown, total_tokens, evicted_count
            (token-budget evictions ONLY — never folds in k-truncation, DP-7),
            and budget (ALWAYS present: total_cap, slots, k_requested, k_applied,
            truncated_count — crystalium#36 / DP-3c).
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
            # v1.6: explain=True always bypasses the cache (see docstring).
            if self.recall_cache is not None and not explain:
                cached = self.recall_cache.get(
                    getattr(scope, "project", None), query, **self._cache_ctx(scope, k, layers, caller_tier)
                )
                if cached is not None:
                    return cached

            target_layers = layers if layers else _ALL_LAYERS

            # 3. Hybrid retrieve per layer
            all_candidates: dict[str, dict[str, Any]] = {}  # id → crystal dict
            sparse_ranking: list[str] = []
            dense_ranking: list[str] = []
            graph_ranking: list[str] = []
            # v1.6 explain: did ANY layer's embed() call return a usable vector?
            # Cheap to track unconditionally; only surfaced when explain=True.
            dense_got_vector = False

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
                    dense_got_vector = True
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
            # crystalium#36 seam 1: rrf_merge_scored exposes the raw fusion score
            # rrf_merge itself discarded. rrf_score_by_id is the SOURCE OF TRUTH
            # for _ComposerRecord.relevance_score / CrystalSummary.score below —
            # captured here, BEFORE the context_match re-rank, so `score` stays
            # the raw RRF fusion value (D3) regardless of that re-rank.
            fused_scored = rrf_merge_scored(rankings, k_rrf=60)
            fused_ids = [cid for cid, _ in fused_scored]
            rrf_score_by_id: dict[str, float] = dict(fused_scored)

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

            # W6 defense (recall_active_only): drop deprecated/superseded/expired
            # crystals so a rejected (deprecated) or superseded fact cannot resurface.
            # Active := status == "active" AND temporal.t_valid_to is unset. Off ->
            # always True (byte-identical to W5).
            def _is_active(crystal: dict[str, Any]) -> bool:
                if not self.recall_active_only:
                    return True
                if crystal.get("status", "active") != "active":
                    return False
                temporal = crystal.get("temporal") or {}
                if isinstance(temporal, str):
                    import json
                    try:
                        temporal = json.loads(temporal)
                    except Exception:
                        temporal = {}
                return temporal.get("t_valid_to") is None

            # v1.6 explain: waterfall the combined filter into its two stages so a
            # zero-record recall against a non-empty store is diagnosable — each
            # fused candidate is attributed to exactly ONE reason (scope checked
            # before status, matching evaluation order below). Counters are cheap
            # (pure Python, no extra I/O) and computed unconditionally; only
            # surfaced in the result when explain=True.
            filtered_ids: list[str] = []
            _filtered_by_scope = 0
            _filtered_by_status = 0
            for cid in fused_ids:
                candidate = all_candidates.get(cid)
                if candidate is None:
                    continue
                if not _scope_matches(candidate):
                    _filtered_by_scope += 1
                    continue
                if not _is_active(candidate):
                    _filtered_by_status += 1
                    continue
                filtered_ids.append(cid)

            # crystalium#36 seam 3 (gated by recall_relevance_primary): filtered_ids
            # is already in descending-RRF order, so truncating to the first k
            # entries here — BEFORE composer_records is built — makes relevance
            # decisive over *membership*, not just fetch order, and is the
            # mechanism by which `k` becomes a hard cap on len(result.records)
            # (DP-3a). `truncated_by_k_count` feeds budget.truncated_count /
            # explain.truncated_by_k (DP-7 — kept separate from evicted_count,
            # which stays token-budget-only). Off -> filtered_ids is untouched and
            # `k` is not a cap (AC-009), matching pre-1.9.0 behaviour exactly.
            truncated_by_k_count = 0
            if self.recall_relevance_primary:
                truncated_by_k_count = max(0, len(filtered_ids) - k)
                filtered_ids = filtered_ids[:k]

            # Build Crystal-like objects for the composer
            now = datetime.now(timezone.utc)

            def _to_composer_record(
                crystal: dict[str, Any],
                relevance_score: float = 0.0,
                relevance_rank: int = 0,
            ) -> "_ComposerRecord":
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
                    relevance_score=relevance_score,
                    relevance_rank=relevance_rank,
                )

            # crystalium#36 seam 2: relevance_score (raw RRF fusion value, D3) and
            # relevance_rank (0-based position in the post-filter fused order,
            # i.e. filtered_ids as of here — after the seam-3 gate above, so a
            # truncated run only ever assigns ranks 0..k-1) travel with the record
            # from here through the composer to CrystalSummary.score. Populated
            # in BOTH flag modes — only membership/eviction-key/ordering are gated.
            composer_records = [
                _to_composer_record(
                    all_candidates[cid],
                    relevance_score=rrf_score_by_id.get(cid, 0.0),
                    relevance_rank=rank,
                )
                for rank, cid in enumerate(filtered_ids)
                if cid in all_candidates
            ]

            # 7. Composer: slot-budgeted assembly. Defense-in-depth (issue #32): a
            # future tokenizer/composer fault (e.g. an unencodable summary) must
            # degrade recall to an empty, diagnostic working set with a logged
            # warning rather than crash the whole call — mirrors the
            # graph_expand_skipped / completion_walk_skipped pattern above.
            try:
                composed = self.composer.compose(composer_records)
            except Exception as exc:
                log.warning(
                    "composer_compose_skipped",
                    error=str(exc),
                    record_count=len(composer_records),
                )
                from crystalium.composer import ComposedSet

                composed = ComposedSet(
                    records=[],
                    slot_tokens={},
                    total_tokens=0,
                    evicted_count=len(composer_records),
                )

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
                        # crystalium#36 / DP-5: raw RRF fusion value, populated in
                        # BOTH flag modes — the fusion always runs; suppressing it
                        # under flag-off would remove diagnosability exactly where
                        # an operator on the legacy path needs it most.
                        score=rec.relevance_score,
                    )
                )

            # v1.6 item 3: build the `explain` diagnostic object (only when
            # requested — the store aggregate query is skipped otherwise).
            explain_obj: dict[str, Any] | None = None
            if explain:
                dense_null = bool(getattr(self.vector_store, "_is_null", False))
                if dense_null:
                    dense_status = "inactive(null_vector_store)"
                elif not dense_got_vector:
                    dense_status = "inactive(embed_unavailable)"
                else:
                    dense_status = "active"
                graph_status = "off" if getattr(self.graph_store, "_is_null", False) else "on"

                store_stats = self.relational.diagnostics_summary()
                explain_obj = {
                    "candidates_prefilter": len(all_candidates),
                    "filtered_by_status": _filtered_by_status,
                    "filtered_by_scope": _filtered_by_scope,
                    "arms": {
                        "bm25": "on",
                        "dense": dense_status,
                        "graph": graph_status,
                    },
                    "store": {
                        "total_crystals": store_stats["total_crystals"],
                        "active": store_stats["active"],
                        "embedded": store_stats["embedded"],
                    },
                    "project_keys_present": sorted(store_stats["by_project"].keys()),
                    # crystalium#36 / DP-3c: k-gate diagnosability, present in both
                    # flag modes (truncated_by_k_count is 0 when the flag is off).
                    "k_applied": len(result_records),
                    "truncated_by_k": truncated_by_k_count,
                }

            # crystalium#36 / DP-3c (D2.4): the working-set budget, ALWAYS present
            # (never explain-gated) — an explain-only budget would recreate the
            # exact discoverability failure issue #36 is about. total_cap/slots
            # come from Config (DP-3b: the budget itself never scales with k);
            # truncated_count is the k-gate-only drop count (DP-7 — evicted_count
            # keeps its existing, token-budget-only meaning, untouched below).
            budget = Budget(
                total_cap=self.composer.config.total_cap,
                slots=dict(self.composer.config.slots),
                k_requested=k,
                k_applied=len(result_records),
                truncated_count=truncated_by_k_count,
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
                budget=budget,
                explain=explain_obj,
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
            # v1.6: never cache an explain=True result (see docstring).
            if self.recall_cache is not None and not explain:
                self.recall_cache.put(
                    getattr(scope, "project", None), query, result,
                    **self._cache_ctx(scope, k, layers, caller_tier),
                )

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
        # crystalium#36 seam 2: query relevance carried from Aetheryte's RRF
        # fusion through to the Composer (_eviction_key, seam 4) and back out to
        # CrystalSummary.score (seam 5 / D3). relevance_score is the raw RRF
        # fusion value; relevance_rank is its 0-based position in the post-filter
        # fused order. __slots__ is declared, so both are ALSO in __init__ below
        # — editing one without the other raises AttributeError on assignment.
        "relevance_score",
        "relevance_rank",
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
        relevance_score: float = 0.0,
        relevance_rank: int = 0,
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
        # crystalium#36 seam 2 (see __slots__ note above).
        self.relevance_score = relevance_score
        self.relevance_rank = relevance_rank
