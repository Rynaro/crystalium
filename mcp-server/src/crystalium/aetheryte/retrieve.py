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
from crystalium.trust import Tier

if TYPE_CHECKING:
    from crystalium.composer import Composer
    from crystalium.storage.relational import RelationalStore
    from crystalium.storage.vector import VectorStore
    from crystalium.storage.graph import GraphStore

log = structlog.get_logger("crystalium.aetheryte.retrieve")

# All four valid layer names (from trust.py VALID_LAYERS)
_ALL_LAYERS: list[str] = ["episodic", "semantic", "procedural", "execution"]

# crystalium#36 seam 3b (DP-R1 / C-12, post-verification): the minimum
# ARM-SEEDING fetch width once k becomes a real response cap (seam 3).
# Matches the existing candidate_k floor (max(k*3, 10), below) and the
# shipped default k (both the MCP manifest and the CLI default to 10) — not
# a new magic number. Read only under Config.recall_relevance_primary; flag
# off keeps every arm-seeding slice as bare `[:k]`, byte-identical to 323229f.
FETCH_WIDTH_FLOOR: int = 10


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
# crystalium#38 — weighted RRF fusion (FORGE deliberation.md DP-1(b)/D1).
#
# `rrf_merge` / `rrf_merge_scored` above are NOT modified by this change — they
# keep their documented insertion-order tie-break and remain the flag-off path
# (test_rrf.py's pre-existing classes stay byte-identical, AC-106). Everything
# below is new, additive surface gated behind Config.recall_weighted_fusion
# (subsumed under recall_relevance_primary, D7).
# ---------------------------------------------------------------------------


def weighted_rrf_merge_scored(
    weighted_rankings: list[tuple[list[str], float]],
    k_rrf: int = 60,
) -> list[tuple[str, float]]:
    """Weighted Reciprocal Rank Fusion — the D1 pure function.

        score(id) = sum over arms of  w_arm / (k_rrf + rank_arm(id))   (rank 1-based)

    Arms in which `id` does not appear contribute nothing to its score. Every
    arm at `w_arm = 1.0` reduces this to exactly `rrf_merge_scored`'s formula
    (AC-104) — weighting is a strict generalisation of the unweighted fusion.

    Duplicates within a single ranking accumulate one term PER OCCURRENCE,
    mirroring `rrf_merge_scored` (`retrieve.py:82-86`) — its own docstring
    "blesses" the case ("allowed but unusual"), so this function must not
    silently de-duplicate a caller's ranking (vigil B-4 / AC-104).

    Args:
        weighted_rankings: List of (ranking, weight) pairs. Iterate the CALLER-
            SUPPLIED order (the recall pipeline fixes it to sparse, dense,
            derived — D1) so float summation stays bit-reproducible across
            runs; this function performs no reordering of its own.
        k_rrf: RRF smoothing constant (default 60, matching `rrf_merge_scored`).

    Returns:
        List of (id, score) sorted by `(-score, id)` — descending score,
        id-ascending as a DETERMINISTIC tiebreak (AC-105). This deliberately
        differs from `rrf_merge_scored`'s insertion-order tiebreak: with float
        weights, exact ties are rarer but more dangerous, and P3 (graph/
        completion arm order was previously hash-seed-dependent) shows
        insertion order is not a stable tiebreak to inherit. AC-104's
        multiset-of-`(id, score)` equality against `rrf_merge_scored` holds
        regardless of this difference; AC-132 pins order-equality on the
        (larger) domain where no tie exists, where the two tiebreak rules
        provably cannot diverge.
    """
    scores: dict[str, float] = {}
    for ranking, weight in weighted_rankings:
        for rank_0, record_id in enumerate(ranking):
            rank_1 = rank_0 + 1  # 1-based
            scores[record_id] = scores.get(record_id, 0.0) + weight / (k_rrf + rank_1)

    return sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))


def weighted_rrf_merge(
    weighted_rankings: list[tuple[list[str], float]],
    k_rrf: int = 60,
) -> list[str]:
    """IDs-only sibling of `weighted_rrf_merge_scored` (D4's `prelim` helper)."""
    return [cid for cid, _ in weighted_rrf_merge_scored(weighted_rankings, k_rrf)]


def derived_family_merge(arms: list[list[str]]) -> list[str]:
    """D2 — collapse the correlated derived arms (graph, completion) into ONE
    voter by MIN-RANK, before fusion.

    `seed_ids = prelim[:fetch_width]` seeds BOTH the graph walk and the
    completion walk (D4) — they are not independent evidence, they are the
    base arms' own neighbourhood explored twice. Summing them under RRF lets
    a single opinion vote three times (P2); this merges them into the single
    voter D1's fusion actually sums.

    A candidate present in both source arms is emitted ONCE, at its BEST
    (minimum, i.e. best-ranked) position across the two (AC-107). With one
    of the two arms empty (or absent), this is the IDENTITY transform on the
    other — re-ranking one arm by its own min-rank does not change it
    (AC-108's `weighted_rrf_merge_scored`-level identity property; measured
    bitwise on the real stack, deliberation.md §DP-2).

    Args:
        arms: The derived-family source rankings, e.g. `[graph_ranking,
            completion_ranking]`. Order of the input list does not matter —
            output is by (min-rank, id), not by arm precedence.

    Returns:
        Merged ranking, ordered by ascending (min_rank, id) — the id-ascending
        half of the tiebreak matters only for candidates that are NOT present
        in either source arm at all, which cannot occur; it exists so ties in
        `min_rank` (an arm's own duplicate defence aside) resolve
        deterministically rather than by dict-insertion order (P3).
    """
    minrank: dict[str, int] = {}
    for arm in arms:
        for i, cid in enumerate(arm):
            rank = i + 1
            if cid not in minrank or rank < minrank[cid]:
                minrank[cid] = rank
    return [cid for cid, _ in sorted(minrank.items(), key=lambda kv: (kv[1], kv[0]))]


def resolve_sparse_weight(
    raw_n_sparse: int,
    resolved_n_sparse: int,
    cap: int,
    n_scoped: int,
    alpha: float,
) -> tuple[float, float]:
    """D3 — the query-conditional sparse-arm weight.

        selectivity = 0.0                                              if raw_n_sparse == 0 or raw_n_sparse >= cap
                      clamp(1 - resolved_n_sparse / max(1, n_scoped), 0, 1)   otherwise
        w_sparse    = 1.0 + alpha * selectivity        # in [1.0, 1.0 + alpha]

    Two distinct counts are deliberately taken (C-7, FORGE deliberation.md
    DP-9): the CENSORING test ("was this fetch truncated by the cap?") is a
    property of the FETCH, not of any status-filtered subset of it, so it
    MUST read `raw_n_sparse` — before any population filtering — never the
    population-resolved count. The selectivity RATIO, once past censoring,
    uses `resolved_n_sparse` (the population-resolved numerator, DP-9)
    against `n_scoped` (drawn from the SAME population, DP-3/DP-9/AC-142) —
    using the raw numerator there would mix populations (the exact defect
    AC-142 exists to forbid).

    Args:
        raw_n_sparse:      The raw row count of the fetch that PRODUCES the
                           censoring signal (crystalium#45 / spec.amend-03.md
                           §10, K-C-N13): the global call's row count on the
                           default and strict-subset paths, or the single
                           filtered call's row count on the single-layer
                           path — NEVER `len(sparse_ranking)` directly, which
                           on the strict-subset path also holds starvation-
                           backstop rows from OTHER fetches (§B.3.1) and
                           would conflate the signal. Unconditional, the CENSORING
                           signal. `bm25_search` applies no status predicate
                           (shared method, never filtered).
        resolved_n_sparse: The population-resolved numerator (DP-9): equals
                           `raw_n_sparse` under an all-statuses population,
                           or the active-only count within `sparse_ranking`
                           under an active-only population.
        cap:               The requested `k` of the SAME fetch `raw_n_sparse`
                           was read from: `candidate_k * len(_ALL_LAYERS)` on
                           the default/strict-subset (global) paths,
                           `candidate_k` on the single-layer path
                           (crystalium#45 / spec.amend-03.md §10).
        n_scoped:          Crystal count over `target_layers`, in the SAME
                           status population as `resolved_n_sparse`.
        alpha:             `fusion_sparse_boost_alpha`, >= 0.

    Returns:
        (w_sparse, selectivity).
    """
    if raw_n_sparse == 0 or raw_n_sparse >= cap:
        selectivity = 0.0
    else:
        selectivity = max(0.0, min(1.0, 1.0 - (resolved_n_sparse / max(1, n_scoped))))
    w_sparse = 1.0 + alpha * selectivity
    return w_sparse, selectivity


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
        recall_weighted_fusion: bool = True,
        fusion_weight_dense: float = 1.0,
        fusion_weight_derived: float = 1.0,
        fusion_sparse_boost_alpha: float = 1.0,
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
        # crystalium#38 (FORGE deliberation.md DP-1..DP-9): weighted RRF fusion
        # + the derived-family min-rank merge (D1/D2), replacing unweighted
        # summation on the gated path. Concrete defaults (not a Config-object
        # sentinel — Aetheryte holds no `self.config` reference, unlike
        # Composer) matching D6 exactly, mirroring the existing
        # `recall_relevance_primary` / `recall_active_only` parameters above;
        # both production construction sites (server.py, __main__.py) pass
        # these explicitly from `config.recall_weighted_fusion` etc., so a
        # bare `Aetheryte(...)` at a hypothetical future third site still
        # gets the correct, non-foot-gun default (the same protection
        # DP-R4(iii) introduced for Composer, applied here without a config
        # object to fall back to since none exists at this layer).
        self.recall_weighted_fusion = recall_weighted_fusion
        # D6 — per-arm weights, default 1.0. Deliberately NO
        # `fusion_weight_sparse`: RRF ordering is invariant to a global
        # positive scale factor (score values scale, the sort does not), so
        # pinning the sparse base at 1.0 removes a redundant degree of
        # freedom (`w_sparse` below is instead the D3 query-conditional
        # boost, resolved per-recall).
        self.fusion_weight_dense = fusion_weight_dense
        self.fusion_weight_derived = fusion_weight_derived
        self.fusion_sparse_boost_alpha = fusion_sparse_boost_alpha
        # D7 — subsumption: the weighted path activates ONLY when both flags
        # are True. `recall_relevance_primary=False` is contractually
        # byte-identical to pre-1.9.0 (#36 AC-008/AC-009, frozen); an
        # independent weighting flag would alter the fused order on that
        # path. Resolved once here (not per-call) since neither flag varies
        # within a recall. Pinned by AC-120 so the half-gated mode #36
        # DP-R4(iii) closed cannot reappear as an emergent property here.
        self._weighted = bool(recall_weighted_fusion and recall_relevance_primary)

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

            candidate_k = max(k * 3, FETCH_WIDTH_FLOOR)

            # crystalium#46: embed(query) is loop-invariant (its sole argument
            # is the bare query string — no per-layer parameter), so it is
            # hoisted out of the per-layer loop below to a single call.
            # dense_got_vector is assigned once from this call (previously
            # re-assigned identically on every truthy iteration); the
            # embed_skipped warning likewise fires once, carrying `layers`
            # instead of the no-longer-meaningful per-layer `layer` field.
            query_vec: list[float] = []
            try:
                query_vec = self.vector_store.embed(query)
            except Exception as exc:
                log.warning("embed_skipped", layers=list(target_layers), error=str(exc))

            if query_vec:
                dense_got_vector = True

            # crystalium#45 (FORGE D3; spec.amend-01.md §B.3, spec.amend-03.md
            # §10/§15.4): the three-case fetch shape, sparse AND dense (dense
            # mirrors sparse — `vector.py:174-199` supports `layer_filter=None`
            # exactly as `bm25_search` does). Path classification is by SET
            # membership — "nothing excluded" is the operative semantics of
            # the default (`layers=None`) path, never literal list-order
            # equality, so an explicit `layers=[...]` naming all four layers
            # (in any order) still takes the score-space global path.
            _all_layers_set = set(_ALL_LAYERS)
            _target_layer_set = set(target_layers)
            _is_no_exclusion = _target_layer_set == _all_layers_set
            _is_single_layer = len(target_layers) == 1

            # Per-fetch raw-count capture (K-C-N13 / AC-377 / spec.amend-03.md
            # §15.4 — W-45 owns this so #44's top-up, W-44, can read, AT THE
            # CALL SITE, the row count each `bm25_search` call actually
            # returned and the `k` it requested. `len(sparse_ranking)` is NOT
            # this signal on the strict-subset path (head + backstop tail).
            sparse_fetches: list[dict[str, Any]] = []

            def _record_sparse_fetch(
                label: str, k_requested: int, hits: list[dict[str, Any]]
            ) -> None:
                sparse_fetches.append(
                    {"fetch": label, "k_requested": k_requested, "n_returned": len(hits)}
                )

            def _ingest_sparse_hits(hits: list[dict[str, Any]]) -> None:
                for hit in hits:
                    cid = hit["id"]
                    all_candidates[cid] = hit
                    if cid not in sparse_ranking:
                        sparse_ranking.append(cid)

            def _ingest_dense_hits(hits: list[dict[str, Any]]) -> None:
                # Dense hits from LanceDB carry 'id' (or metadata id) and '_distance'
                for hit in hits:
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

            if _is_no_exclusion:
                # Default path (layers=None, or an explicit all-four subset):
                # ONE global call per arm. Score-space by construction --
                # relational.py:531-541 orders globally by bm25(crystals_fts)
                # with no layer filter -- so no post-filter is needed: nothing
                # is excluded (spec.amend-01.md §B.3.1).
                global_k = candidate_k * len(_ALL_LAYERS)
                bm25_hits = self.relational.bm25_search(query, layer_filter=None, k=global_k)
                _record_sparse_fetch("head", global_k, bm25_hits)
                _ingest_sparse_hits(bm25_hits)
                raw_n_sparse_signal = len(bm25_hits)
                cap_signal = global_k
                sparse_fetch_shape = "global"

                if query_vec:
                    dense_hits = self.vector_store.dense_search(
                        query_vec=query_vec, layer_filter=None, k=global_k
                    )
                    _ingest_dense_hits(dense_hits)

            elif _is_single_layer:
                # Single-layer path -- today's per-layer call, already
                # score-space within the layer. Byte-preserving.
                layer = target_layers[0]
                bm25_hits = self.relational.bm25_search(query, layer_filter=layer, k=candidate_k)
                _record_sparse_fetch("head", candidate_k, bm25_hits)
                _ingest_sparse_hits(bm25_hits)
                raw_n_sparse_signal = len(bm25_hits)
                cap_signal = candidate_k
                sparse_fetch_shape = "single-layer"

                if query_vec:
                    dense_hits = self.vector_store.dense_search(
                        query_vec=query_vec, layer_filter=layer, k=candidate_k
                    )
                    _ingest_dense_hits(dense_hits)

            else:
                # Strict subset, len(target_layers) >= 2: global call
                # (score-space) + post-filter to target_layers, plus a
                # starvation backstop (K-N12) -- global dominance by EXCLUDED
                # layers is noise for an explicit subset, never signal. The
                # censoring signal (raw_n_sparse_signal/cap_signal) is the
                # HEAD's alone (K-C-N13, spec.amend-03.md §10/§15.1) --
                # backstop rows never touch it ("coverage appendages").
                global_k = candidate_k * len(_ALL_LAYERS)
                bm25_hits = self.relational.bm25_search(query, layer_filter=None, k=global_k)
                _record_sparse_fetch("head", global_k, bm25_hits)
                raw_n_sparse_signal = len(bm25_hits)
                cap_signal = global_k
                sparse_fetch_shape = "global+backstop"
                head_censored = raw_n_sparse_signal >= global_k

                sparse_head = [hit for hit in bm25_hits if hit.get("layer") in _target_layer_set]
                _ingest_sparse_hits(sparse_head)

                needed = candidate_k * len(target_layers)
                if len(sparse_head) < needed and head_censored:
                    for layer in target_layers:
                        layer_hits = self.relational.bm25_search(
                            query, layer_filter=layer, k=candidate_k
                        )
                        _record_sparse_fetch(f"backstop:{layer}", candidate_k, layer_hits)
                        # Layer-major, appended AFTER the globally-ordered
                        # head; dedup against rows already present handled by
                        # _ingest_sparse_hits' own "if cid not in" guard.
                        _ingest_sparse_hits(layer_hits)

                if query_vec:
                    dense_hits_global = self.vector_store.dense_search(
                        query_vec=query_vec, layer_filter=None, k=global_k
                    )
                    dense_head = [
                        hit for hit in dense_hits_global if hit.get("layer") in _target_layer_set
                    ]
                    _ingest_dense_hits(dense_head)

                    dense_needed = candidate_k * len(target_layers)
                    dense_head_censored = len(dense_hits_global) >= global_k
                    if len(dense_head) < dense_needed and dense_head_censored:
                        for layer in target_layers:
                            layer_dense_hits = self.vector_store.dense_search(
                                query_vec=query_vec, layer_filter=layer, k=candidate_k
                            )
                            _ingest_dense_hits(layer_dense_hits)

            # crystalium#36 seam 3b (gated; DP-R1/C-12): once k means "response
            # cap" (seam 3), the RANKING UNIVERSE must not shrink with it, or a
            # small k silently changes which arms vote — seed_ids/completion_seeds
            # sliced by bare `k` let a narrow window concentrate the graph/
            # completion walk onto a handful of seeds, handing their
            # topically-unrelated neighbours enough extra arms to outrank a
            # fresh, exactly-matching crystal and truncate it out of the k-gate
            # entirely (vigil's F-V1, verification.md). fetch_width is used for
            # EVERY arm-seeding slice below; the `[:k]` response slice (seam 3,
            # already applied earlier) remains the ONLY consumer of caller k.
            # Flag off: fetch_width == k exactly, so this is a no-op — every
            # arm-seeding slice stays bare `[:k]`, byte-identical to 323229f.
            fetch_width = max(k, FETCH_WIDTH_FLOOR) if self.recall_relevance_primary else k

            # W6 defense (recall_active_only): drop deprecated/superseded/expired
            # crystals so a rejected (deprecated) or superseded fact cannot resurface.
            # Active := status == "active" AND temporal.t_valid_to is unset. Off ->
            # always True (byte-identical to W5). Defined here (moved up from its
            # v1.9.0 position at the scope/status filter, below) so crystalium#38's
            # DP-9(b) sparse-weight numerator can reuse the SAME predicate the
            # response applies — population parity by construction, never a second
            # divergent definition.
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

            # crystalium#38 (FORGE DP-1..DP-9, D0 pipeline order — load-bearing,
            # no feedback loop): weights are resolved ONCE per recall, from the
            # BASE arms only, before any seeding or derived-arm construction.
            # Unweighted (flag off, either half of the D7 subsumption): every
            # value below stays at its v1.9.0-equivalent neutral default, and
            # `seed_ids` is `dense_ranking[:fetch_width]` exactly as ef42967 —
            # AC-119's byte-identical flag-off contract.
            w_sparse = 1.0
            w_dense = self.fusion_weight_dense
            w_derived = self.fusion_weight_derived
            selectivity = 0.0
            # crystalium#45 / K-C-N13 (spec.amend-03.md §10): `cap`/`raw_n_sparse`
            # are the requested-k/raw-row-count of the FETCH THAT PRODUCED THE
            # HEAD (cap_signal/raw_n_sparse_signal, captured at the call site
            # above) — never `len(sparse_ranking)` directly, which on the
            # strict-subset path also holds starvation-backstop rows from
            # OTHER fetches and would conflate the censoring signal with
            # coverage. Single-layer path: unchanged, byte-preserving
            # (cap_signal == candidate_k, raw_n_sparse_signal == that call's
            # row count).
            cap = cap_signal
            raw_n_sparse = raw_n_sparse_signal
            n_sparse_resolved = raw_n_sparse
            n_scoped = 0
            n_scoped_status = "n/a"

            if self._weighted:
                # D3 — the query-conditional sparse weight, resolved from the
                # search-space-local selectivity. DP-9(b): the numerator and
                # denominator are drawn from the SAME status population,
                # tracking `recall_active_only` (all-statuses when it is
                # False, matching what the response itself then returns).
                # C-8(ii)/(iii): the population is resolved ONCE here, as a
                # PURE-PYTHON filter over the crystal dicts already in hand
                # (`bm25_search` returns full rows) — never a status predicate
                # on the shared `bm25_search`, and no extra I/O beyond the one
                # bounded aggregate below (DP-3, "the existing bounded
                # aggregate", `count_for_export`; no new public storage
                # method, per FORGE's ruling).
                if self.recall_active_only:
                    n_scoped = self.relational.count_for_export(
                        getattr(scope, "project", None), layers=target_layers
                    )
                    n_scoped_status = "active_only"
                else:
                    n_scoped = self.relational.count_for_export(
                        getattr(scope, "project", None),
                        layers=target_layers,
                        include_deprecated=True,
                        include_superseded=True,
                        include_quarantined=True,
                    )
                    n_scoped_status = "all_statuses"
                # `_is_active` already returns True unconditionally when
                # `recall_active_only` is False, so this one expression is
                # correct under BOTH population choices without a second
                # helper (it degenerates to `raw_n_sparse` in that branch).
                n_sparse_resolved = sum(
                    1 for cid in sparse_ranking if _is_active(all_candidates.get(cid, {}))
                )
                # C-7: the CENSORING test below reads `raw_n_sparse` — the
                # UNFILTERED fetch length — never `n_sparse_resolved`. A fetch
                # truncated by the cap has an unknown true match count, a
                # property of the fetch itself, independent of how many of
                # those raw hits are active.
                w_sparse, selectivity = resolve_sparse_weight(
                    raw_n_sparse, n_sparse_resolved, cap, n_scoped,
                    self.fusion_sparse_boost_alpha,
                )

                # D4 — seed from a BASE-ARM (sparse + dense) preliminary
                # fusion, not `dense_ranking` alone. Invariant I-1: `prelim`
                # reads ONLY sparse_ranking/dense_ranking — never
                # graph_ranking or completion_ranking, which would create a
                # feedback loop in which the derived arms influence their own
                # seeds. `fetch_width` (the seed COUNT) is unchanged; only
                # seed COMPOSITION differs from v1.9.0 (issue item 2).
                prelim = weighted_rrf_merge(
                    [(sparse_ranking, w_sparse), (dense_ranking, w_dense)]
                )
                seed_ids = prelim[:fetch_width]
            else:
                seed_ids = dense_ranking[:fetch_width]

            # Graph-expand: neighbours of top fetch_width seed hits (depth=1).
            if seed_ids:
                try:
                    neighbour_ids = self.graph_store.neighbor_expand(
                        seed_ids=seed_ids, depth=1
                    )
                    # D5 (P3 determinism fix; deliberately OUTSIDE the
                    # recall_weighted_fusion flag — see class docstring /
                    # Risks+Rollback: gating an ORDER-stabilising fix behind a
                    # flag would leave the flag-off path irreproducible too).
                    # `neighbor_expand` returns a `set[str]` whose iteration
                    # order is per-process hash-randomised (P3); `sorted()`
                    # replaces that with a deterministic total order. This is
                    # ordering determinism, not relevance ordering —
                    # `neighbor_expand` has no relevance signal to offer.
                    for nid in sorted(neighbour_ids):
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
                # `seed_ids or sparse_ranking[:fetch_width]` — this fallback is
                # near-dead under the weighted path (`prelim` is non-empty
                # whenever either base arm is non-empty) but kept, free and
                # defensive, for the unweighted path where `seed_ids` really
                # can be empty (D4 comment).
                completion_seeds = seed_ids or sparse_ranking[:fetch_width]
                if completion_seeds:
                    try:
                        walked = self.graph_store.decaying_walk(
                            seed_ids=completion_seeds,
                            max_hops=self.completion_max_hops,
                            decay=self.completion_decay,
                        )
                        # D5: id tiebreak added (score-primary, id-ascending
                        # within a hop) — keeps the within-hop order stable
                        # under P3 (a `dict` of scores has no source-order
                        # guarantee of its own once ties are possible).
                        for cid, _score in sorted(
                            walked.items(), key=lambda kv: (-kv[1], kv[0])
                        ):
                            if cid not in all_candidates:
                                full = self.relational.get_crystal(cid)
                                if full:
                                    all_candidates[cid] = full
                            if cid in all_candidates and cid not in completion_ranking:
                                completion_ranking.append(cid)
                    except Exception as exc:
                        log.warning("completion_walk_skipped", error=str(exc))

            # D2 — collapse the correlated derived arms into ONE voter by
            # min-rank. Computed unconditionally (cheap, pure Python, no I/O)
            # so `explain.fusion.arm_sizes.derived` is available regardless of
            # flag state; only the WEIGHTED fusion below actually consumes it.
            derived_ranking = derived_family_merge([graph_ranking, completion_ranking])

            # 4. RRF fusion.
            # crystalium#36 seam 1: rrf_merge_scored / weighted_rrf_merge_scored
            # exposes the raw fusion score rrf_merge itself discarded.
            # rrf_score_by_id is the SOURCE OF TRUTH for
            # _ComposerRecord.relevance_score / CrystalSummary.score below —
            # captured here, BEFORE the context_match re-rank, so `score` stays
            # the raw (D3: now weighted, on the gated path) fusion value
            # regardless of that re-rank.
            if self._weighted:
                # D1: fixed arm iteration order (sparse, dense, derived) so
                # float summation is bit-reproducible. An empty derived_ranking
                # (no graph/completion candidates) contributes nothing — same
                # as the legacy path's `if completion_ranking:` guard, just
                # expressed structurally rather than conditionally, since D2
                # already collapsed graph+completion into one possibly-empty
                # arm.
                fused_scored = weighted_rrf_merge_scored(
                    [
                        (sparse_ranking, w_sparse),
                        (dense_ranking, w_dense),
                        (derived_ranking, w_derived),
                    ],
                    k_rrf=60,
                )
            else:
                # Unweighted (flag off): byte-identical to ef42967 — the
                # legacy 3/4-arm rrf_merge_scored, completion appended only
                # when non-empty.
                rankings = [sparse_ranking, dense_ranking, graph_ranking]
                if completion_ranking:
                    rankings.append(completion_ranking)
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

            # _is_active is defined earlier in this method (crystalium#38 /
            # DP-9(b)), before the weighted-fusion block, so its sparse-weight
            # numerator and this scope/status filter share the SAME predicate.

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
            # is already in descending-RRF order — UNLESS `context_match` is on,
            # in which case it was re-sorted by context-overlap-then-RRF at
            # step 4b (`fused_ids = sorted(fused_ids, key=lambda cid: -_ctx_overlap(cid))`,
            # above) before this scope/status filter built filtered_ids from it;
            # the k-gate below then selects the top-k by THAT order, giving
            # `context_match` membership power it did not have pre-1.9.0
            # (vigil's F-V4, verification.md). Harmless as shipped:
            # `recall_context_match` defaults OFF and is gate-recommended to
            # stay off (context_pass: false), and seam 5 re-sorts the survivors
            # by raw RRF regardless (AC-003/AC-007 hold either way). Truncating
            # to the first k entries here — BEFORE composer_records is built —
            # makes relevance (or, when context_match is on, context-then-
            # relevance) decisive over *membership*, not just fetch order, and
            # is the mechanism by which `k` becomes a hard cap on
            # len(result.records) (DP-3a). Off -> filtered_ids is untouched and
            # `k` is not a cap (AC-009), matching pre-1.9.0 behaviour exactly.
            _filtered_ids_before_k = len(filtered_ids)
            if self.recall_relevance_primary:
                filtered_ids = filtered_ids[:k]

            # crystalium#36 / DP-R3 / C-14: truncated_count and k_applied are
            # derived from the slice ACTUALLY PERFORMED above, never from
            # pre-slice intent. Attack D (delete the `filtered_ids[:k]` line
            # above, keep a counter computed independently of it) shipped
            # `k_requested=15, k_applied=20, truncated_count=5` — five drops
            # that never happened — while an arithmetic-only oracle stayed
            # green (verification.md F-V3). Comparing the real before/after
            # lengths of the block above makes both fields true by
            # construction: delete that slice and `len(filtered_ids)` no
            # longer shrinks, so `truncated_by_k_count` collapses to 0 here
            # too — the counter cannot diverge from the code it describes.
            # `k_applied_count` is capped at `k` by `min(...)`, so
            # `k_applied <= k_requested` holds in EVERY configuration
            # (including flag-off, where no slice runs and the real result
            # set may exceed k — AC-009; DP-7 keeps `evicted_count` — the
            # token-budget counter — entirely separate, AC-032).
            truncated_by_k_count = _filtered_ids_before_k - len(filtered_ids)
            k_applied_count = min(k, _filtered_ids_before_k)

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
                    # Same slice-derived values as `budget` below (DP-R3/C-14) —
                    # explain and budget never disagree.
                    "k_applied": k_applied_count,
                    "truncated_by_k": truncated_by_k_count,
                    # crystalium#38 / D8 (FORGE deliberation.md DP-7/C-6): the
                    # weights and the query signal that produced them.
                    # `n_sparse` is the POPULATION-RESOLVED numerator (DP-9);
                    # `arm_sizes.sparse` below is the RAW ranking length and
                    # may differ from it under an active-only population on a
                    # mixed-status store (AC-142) — the two are deliberately
                    # NOT the same field. Present (with `weighted: false` and
                    # neutral values) even on the unweighted path, so a caller
                    # comparing explain objects across the flag never sees the
                    # key appear/disappear.
                    "fusion": {
                        "weighted": self._weighted,
                        "w_sparse": w_sparse,
                        "w_dense": w_dense,
                        "w_derived": w_derived,
                        "selectivity": selectivity,
                        "n_sparse": n_sparse_resolved,
                        "n_sparse_cap": cap,
                        # crystalium#45 / K-C-N13 (spec.amend-03.md §10, §15.4):
                        # additive fields making the censoring signal and the
                        # fetch shape observable from outside (previously
                        # unobservable — `n_sparse` above is population-
                        # resolved, never the raw pre-population-filter count).
                        # `raw_n_sparse` is the SAME value passed to
                        # `resolve_sparse_weight` as `raw_n_sparse` (== `cap`'s
                        # own fetch); `fetches` is the per-fetch capture (one
                        # entry per `bm25_search` call the recall path issued)
                        # W-44's top-up reads at the call site rather than
                        # re-deriving from `sparse_ranking` (K-C-N10 risk).
                        "raw_n_sparse": raw_n_sparse,
                        "sparse_fetch_shape": sparse_fetch_shape,
                        "fetches": list(sparse_fetches),
                        "n_scoped": n_scoped,
                        "n_scoped_layers": list(target_layers),
                        "n_scoped_status": n_scoped_status,
                        "fetch_width": fetch_width,
                        "candidate_k": candidate_k,
                        "arm_sizes": {
                            "sparse": len(sparse_ranking),
                            "dense": len(dense_ranking),
                            "graph": len(graph_ranking),
                            "completion": len(completion_ranking),
                            "derived": len(derived_ranking),
                        },
                    },
                }

            # crystalium#36 / DP-3c (D2.4): the working-set budget, ALWAYS present
            # (never explain-gated) — an explain-only budget would recreate the
            # exact discoverability failure issue #36 is about. total_cap/slots
            # come from Config (DP-3b: the budget itself never scales with k);
            # truncated_count is the k-gate-only drop count (DP-7 — evicted_count
            # keeps its existing, token-budget-only meaning, untouched below).
            # k_applied/truncated_count are slice-derived (DP-R3/C-14), NOT
            # `len(result_records)` — the composer's own token-budget eviction
            # (evicted_count) can further shrink the final result set below
            # k_applied; that is a SEPARATE, already-counted signal (AC-032).
            budget = Budget(
                total_cap=self.composer.config.total_cap,
                slots=dict(self.composer.config.slots),
                k_requested=k,
                k_applied=k_applied_count,
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

        except Exception:
            # crystalium#35 fix-forward (v2.0.1): this method used to record its
            # own telemetry row here (tool="crystalium.recall", the pre-#35
            # dotted name) in a `finally:` block that fired on every call —
            # success AND failure. server.py's `_call_tool` dispatcher already
            # records the SAME call under the canonical name ("recall", matching
            # what tools/call actually received) on both its success and
            # exception paths, so every recall was double-written to
            # `tool_calls`: once under the stale dotted key from here, once
            # under the canonical key from the dispatcher. op="recall" carried
            # no information the outer write lacks (the enforcement matrix's op
            # key for this tool IS the tool name), so removing this write drops
            # a redundant duplicate, not real signal. Bare re-raise preserves
            # recall()'s exception semantics unchanged (no swallow, no wrap).
            raise


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
