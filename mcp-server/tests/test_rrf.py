"""Unit tests for the pure RRF fusion function.

Container-first: run via:
  docker compose run --rm crystalium pytest mcp-server/tests/test_rrf.py -v
"""

from __future__ import annotations

import pytest

from crystalium.aetheryte.retrieve import (
    derived_family_merge,
    resolve_sparse_weight,
    rrf_merge,
    rrf_merge_scored,
    weighted_rrf_merge_scored,
)


class TestRrfMerge:
    """Test rrf_merge() as a pure function — no storage deps."""

    def test_empty_rankings(self) -> None:
        """Empty input returns empty output."""
        assert rrf_merge([]) == []

    def test_single_ranking_preserves_order(self) -> None:
        """Single ranking: RRF score = 1/(60+rank); order should match input."""
        ranking = ["a", "b", "c"]
        result = rrf_merge([ranking])
        assert result == ["a", "b", "c"]

    def test_three_rankings_fused(self) -> None:
        """Three rankings where 'c' appears first in two out of three lists.

        Expected: 'c' should rank highest due to two strong positions.
        """
        r1 = ["a", "b", "c"]  # c at position 3 → 1/63
        r2 = ["c", "a", "b"]  # c at position 1 → 1/61
        r3 = ["b", "c", "a"]  # c at position 2 → 1/62

        # c score = 1/61 + 1/62 + 1/63 ≈ 0.0490
        # a score = 1/61 + 1/62 + 1/63 ... let's calculate precisely
        # a: r1 pos=1 → 1/61; r2 pos=2 → 1/62; r3 pos=3 → 1/63
        # b: r1 pos=2 → 1/62; r2 pos=3 → 1/63; r3 pos=1 → 1/61
        # c: r1 pos=3 → 1/63; r2 pos=1 → 1/61; r3 pos=2 → 1/62
        # a == b == c (all appear at position 1, 2, 3 across the lists)
        # They should all have the same score; order deterministic by dict insertion

        result = rrf_merge([r1, r2, r3])
        assert set(result) == {"a", "b", "c"}
        assert len(result) == 3

    def test_k_rrf_60_sensitivity(self) -> None:
        """k_rrf=60 vs k_rrf=1: verify score differences.

        With k_rrf=60: 1/(60+1) vs 1/(60+2) — small difference.
        With k_rrf=1:  1/(1+1)  vs 1/(1+2)  — large difference.
        In both cases, rank-1 item should win over rank-2.
        """
        r1 = ["best", "second"]
        r2 = ["best", "second"]

        result_60 = rrf_merge([r1, r2], k_rrf=60)
        result_1 = rrf_merge([r1, r2], k_rrf=1)

        assert result_60[0] == "best"
        assert result_1[0] == "best"

    def test_item_in_only_one_list(self) -> None:
        """Items appearing in fewer lists score lower than items in more lists."""
        # "shared" appears in both; "exclusive_a" and "exclusive_b" in one each
        r1 = ["shared", "exclusive_a"]
        r2 = ["shared", "exclusive_b"]

        result = rrf_merge([r1, r2])
        # "shared" should be first (appears in both at rank 1)
        assert result[0] == "shared"

    def test_deduplication(self) -> None:
        """Duplicate IDs across different rankings are merged, not repeated."""
        r1 = ["a", "b"]
        r2 = ["a", "c"]
        r3 = ["b", "a"]

        result = rrf_merge([r1, r2, r3])
        # All unique; no duplicates
        assert len(result) == len(set(result))
        assert set(result) == {"a", "b", "c"}

    def test_five_rankings_top_result_stable(self) -> None:
        """Top result is stable across repeated calls (determinism)."""
        rankings = [
            ["alpha", "beta", "gamma", "delta"],
            ["beta", "alpha", "delta", "gamma"],
            ["alpha", "gamma", "delta", "beta"],
            ["gamma", "alpha", "beta", "delta"],
            ["delta", "beta", "alpha", "gamma"],
        ]
        result_a = rrf_merge(rankings)
        result_b = rrf_merge(rankings)
        assert result_a == result_b  # deterministic

    def test_expected_rank_order(self) -> None:
        """Explicit score calculation to pin the expected rank order.

        r1 = ["x", "y", "z"]  — only list
        Scores: x=1/61, y=1/62, z=1/63
        Expected order: x > y > z
        """
        result = rrf_merge([["x", "y", "z"]], k_rrf=60)
        assert result == ["x", "y", "z"]

    def test_rrf_merge_scored_matches_rrf_merge(self) -> None:
        """crystalium#36 seam 1: rrf_merge_scored is the ID-with-score sibling of
        rrf_merge — same fusion, same tie-break, over the existing fixtures.
        rrf_merge is reimplemented as [cid for cid, _ in rrf_merge_scored(...)],
        so this pins that rrf_merge's own output contract is unaffected
        (test_rrf.py stays byte-identical, per S-1's output contract)."""
        fixtures: list[list[list[str]]] = [
            [],
            [["a", "b", "c"]],
            [["a", "b", "c"], ["c", "a", "b"], ["b", "c", "a"]],
            [["shared", "exclusive_a"], ["shared", "exclusive_b"]],
            [
                ["alpha", "beta", "gamma", "delta"],
                ["beta", "alpha", "delta", "gamma"],
                ["alpha", "gamma", "delta", "beta"],
                ["gamma", "alpha", "beta", "delta"],
                ["delta", "beta", "alpha", "gamma"],
            ],
        ]
        for rankings in fixtures:
            for k_rrf in (1, 60):
                assert [cid for cid, _ in rrf_merge_scored(rankings, k_rrf=k_rrf)] == rrf_merge(
                    rankings, k_rrf=k_rrf
                )


# ---------------------------------------------------------------------------
# crystalium#38 (FORGE deliberation.md DP-1(b)/D1..D3) — weighted RRF fusion,
# the derived-family min-rank merge, and the sparse-arm weight resolution.
# All classes below are ADDITIVE — TestRrfMerge above is byte-identical to
# ef42967 (AC-106).
# ---------------------------------------------------------------------------


class TestWeightedRrf:
    """weighted_rrf_merge_scored — the D1 pure function."""

    # Same fixture corpus as test_rrf_merge_scored_matches_rrf_merge above,
    # PLUS the two fixtures vigil's F4/B-4 findings required: a discordant
    # tie (legacy insertion order != id-ascending) and an intra-list
    # duplicate (rrf_merge_scored accumulates one term PER OCCURRENCE —
    # retrieve.py:82-86 — and weighted_rrf_merge_scored must do the same).
    _LEGACY_FIXTURES: list[list[list[str]]] = [
        [],
        [["a", "b", "c"]],
        [["a", "b", "c"], ["c", "a", "b"], ["b", "c", "a"]],
        [["shared", "exclusive_a"], ["shared", "exclusive_b"]],
        [
            ["alpha", "beta", "gamma", "delta"],
            ["beta", "alpha", "delta", "gamma"],
            ["alpha", "gamma", "delta", "beta"],
            ["gamma", "alpha", "beta", "delta"],
            ["delta", "beta", "alpha", "gamma"],
        ],
    ]

    def test_unit_weights_match_legacy_scores(self) -> None:
        """AC-104: at every arm weight 1.0, weighted_rrf_merge_scored produces
        the SAME (id, score) multiset as rrf_merge_scored for the same input
        — exact under IEEE-754 (`w * x` with `w == 1.0` is identity, and the
        caller-supplied arm order preserves summation order), not merely
        equal up to a tolerance.

        Order is deliberately NOT asserted here — AC-132 owns order-equality,
        scoped to the (larger) domain where the two functions' tiebreak rules
        provably cannot diverge. The discordant fixture below
        (`[["b"], ["a"]]`) is exactly the counterexample where they DO
        diverge: legacy's insertion-order tiebreak gives `['b', 'a']`,
        weighted's id-ascending tiebreak (AC-105) gives `['a', 'b']` — same
        score multiset, different order. Revision 1.0.0 asserted order
        universally and was wrong (vigil F4)."""
        fixtures = self._LEGACY_FIXTURES + [
            [["b"], ["a"]],       # discordant tie (vigil F4)
            [["a", "b", "a"]],    # intra-list duplicate (vigil B-4)
        ]
        for rankings in fixtures:
            for k_rrf in (1, 60):
                legacy = dict(rrf_merge_scored(rankings, k_rrf=k_rrf))
                weighted = dict(
                    weighted_rrf_merge_scored(
                        [(r, 1.0) for r in rankings], k_rrf=k_rrf
                    )
                )
                assert weighted == legacy, (rankings, k_rrf, weighted, legacy)

    def test_unit_weights_match_legacy_order_when_tie_free(self) -> None:
        """AC-132: on the domain where NO two candidates hold an exactly
        equal fused score, weighted_rrf_merge_scored's id-ascending tiebreak
        and rrf_merge_scored's insertion-order tiebreak cannot diverge —
        order-equality holds too, not just score-equality. Tie-freeness is
        asserted MECHANICALLY per fixture (skipped, not assumed, on any
        fixture that turns out to carry a tie)."""
        exercised_a_fixture = False
        for rankings in self._LEGACY_FIXTURES:
            for k_rrf in (1, 60):
                legacy_scored = rrf_merge_scored(rankings, k_rrf=k_rrf)
                scores = [s for _, s in legacy_scored]
                if len(scores) != len(set(scores)):
                    continue  # this fixture carries a tie; outside AC-132's domain
                exercised_a_fixture = True
                legacy_order = [cid for cid, _ in legacy_scored]
                weighted_order = [
                    cid
                    for cid, _ in weighted_rrf_merge_scored(
                        [(r, 1.0) for r in rankings], k_rrf=k_rrf
                    )
                ]
                assert weighted_order == legacy_order
        assert exercised_a_fixture  # the tie-free domain must not be vacuous

    def test_exact_tie_breaks_by_id_ascending(self) -> None:
        """AC-105: candidates whose weighted fused scores are EXACTLY equal
        are ordered by id ascending, regardless of which order the arms are
        supplied in — an insertion-order tiebreak is not stable under P3
        (graph/completion rank order was previously hash-seed-dependent)."""
        order_a = weighted_rrf_merge_scored([(["b"], 1.0), (["a"], 1.0)])
        order_b = weighted_rrf_merge_scored([(["a"], 1.0), (["b"], 1.0)])
        assert [cid for cid, _ in order_a] == ["a", "b"]
        assert [cid for cid, _ in order_b] == ["a", "b"]
        assert order_a[0][1] == order_a[1][1]  # confirm it really is an exact tie


class TestDerivedFamily:
    """derived_family_merge — the D2 correlated-arm min-rank merge."""

    def test_candidate_in_both_arms_appears_once(self) -> None:
        """AC-107: a candidate present in both the graph ranking and the
        completion ranking is emitted EXACTLY ONCE, at its MINIMUM (best)
        rank across the two arms."""
        graph = ["x", "shared", "y"]     # shared at rank 2 here
        completion = ["shared", "z"]     # shared at rank 1 here (better)
        merged = derived_family_merge([graph, completion])
        assert merged.count("shared") == 1
        assert merged.index("shared") == 0  # rank-1 (best) position wins

    def test_single_derived_arm_is_identity(self) -> None:
        """AC-108: with the completion ranking empty (leaving exactly one
        derived arm), weighted_rrf_merge_scored at every weight 1.0
        reproduces the unweighted THREE-arm fusion (sparse, dense, graph) to
        within 1e-15 — re-ranking a single arm by its own min-rank is that
        arm (§D2's identity property; measured bitwise on the real stack,
        deliberation.md §DP-2)."""
        sparse = ["s1", "s2"]
        dense = ["d1", "s1", "d2"]
        graph = ["g1", "s2", "g2"]
        completion: list[str] = []

        derived = derived_family_merge([graph, completion])
        assert derived == graph  # identity at the merge-function boundary too

        legacy = dict(rrf_merge_scored([sparse, dense, graph], k_rrf=60))
        weighted = dict(
            weighted_rrf_merge_scored(
                [(sparse, 1.0), (dense, 1.0), (derived, 1.0)], k_rrf=60
            )
        )
        assert set(weighted) == set(legacy)
        for cid in legacy:
            assert abs(weighted[cid] - legacy[cid]) < 1e-15


class TestSparseWeight:
    """resolve_sparse_weight — the D3 query-conditional sparse-arm weight."""

    def test_selective_query_boosts_sparse_arm(self) -> None:
        """AC-109: n_sparse far below both the fetch cap and the searched-
        layer count (same status population as the numerator, AC-142) ->
        w_sparse strictly greater than 1.0."""
        w_sparse, selectivity = resolve_sparse_weight(
            raw_n_sparse=1, resolved_n_sparse=1, cap=120, n_scoped=31, alpha=1.0
        )
        assert w_sparse > 1.0
        assert selectivity > 0.0

    def test_censored_sparse_arm_is_neutral(self) -> None:
        """AC-110: the sparse ranking LENGTH reaches the fetch cap -> exactly
        1.0 — a censored count cannot evidence selectivity (the true match
        count is unknown and >= cap)."""
        w_sparse, selectivity = resolve_sparse_weight(
            raw_n_sparse=120, resolved_n_sparse=120, cap=120, n_scoped=10_000, alpha=1.0
        )
        assert w_sparse == 1.0
        assert selectivity == 0.0

    @pytest.mark.parametrize("n_scoped", [0, 1])
    def test_empty_sparse_arm_does_not_raise(self, n_scoped: int) -> None:
        """AC-111: the sparse arm returns no candidates -> resolution
        completes without raising, parameterised over searched-layer sizes 0
        and 1 — guards the zero-division and negative-selectivity edges."""
        w_sparse, selectivity = resolve_sparse_weight(
            raw_n_sparse=0, resolved_n_sparse=0, cap=120, n_scoped=n_scoped, alpha=1.0
        )
        assert w_sparse == 1.0
        assert selectivity == 0.0

    def test_weight_is_bounded(self) -> None:
        """AC-112: over a parameterised grid including degenerate inputs, the
        resolved weight always lies in the closed interval [1.0, 1.0+alpha].
        A BOUND, not a correctness oracle — vigil F5 demonstrated an inverted
        weight sitting comfortably inside it; AC-134 is the oracle that
        catches that."""
        alpha = 1.0
        for cap in (1, 10, 120):
            for n_scoped in (0, 1, 5, 31, 10_000):
                for raw_n_sparse in (0, 1, 5, 30, 120, 9_999):
                    for resolved_n_sparse in (0, raw_n_sparse, max(0, raw_n_sparse - 3)):
                        w_sparse, selectivity = resolve_sparse_weight(
                            raw_n_sparse, resolved_n_sparse, cap, n_scoped, alpha
                        )
                        assert 1.0 <= w_sparse <= 1.0 + alpha, (
                            raw_n_sparse, resolved_n_sparse, cap, n_scoped, w_sparse
                        )
                        assert 0.0 <= selectivity <= 1.0

    def test_layer_saturating_query_gets_no_boost(self) -> None:
        """AC-134: the BM25 conjunction matches EVERY crystal in the searched
        layer (n_sparse == n_scoped, maximally NON-selective) -> w_sparse
        resolves to exactly 1.0. MUST be demonstrated RED against a
        global-N denominator (vigil F5): 5 procedural crystals matched, all
        5, inside a 10,005-crystal STORE — a global reading resolves
        w_sparse=1.9995 (near-maximal boost for the least selective possible
        query) while the search-space-local reading correctly resolves 1.0.
        See TestExplain (test_fusion_weighting.py) for the real-stack
        `layers=['procedural']` companion case this criterion also names."""
        w_scoped, _ = resolve_sparse_weight(
            raw_n_sparse=5, resolved_n_sparse=5, cap=30, n_scoped=5, alpha=1.0
        )
        assert w_scoped == 1.0

        # RED-first: a GLOBAL (store-wide) denominator — the defect this
        # criterion forecloses — resolves a near-maximal boost instead.
        w_global, _ = resolve_sparse_weight(
            raw_n_sparse=5, resolved_n_sparse=5, cap=30, n_scoped=10_005, alpha=1.0
        )
        assert w_global > 1.9
        assert w_global != w_scoped
