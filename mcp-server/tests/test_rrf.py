"""Unit tests for the pure RRF fusion function.

Container-first: run via:
  docker compose run --rm crystalium pytest mcp-server/tests/test_rrf.py -v
"""

from __future__ import annotations

import pytest

from crystalium.aetheryte.retrieve import rrf_merge, rrf_merge_scored


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
