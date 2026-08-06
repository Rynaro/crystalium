"""Tests for GraphStore — KuzuDB crystal relationship graph.

Container-first: run via:
  docker compose run --rm crystalium pytest mcp-server/tests/test_storage_graph.py -v
"""

from __future__ import annotations

import pytest
from structlog.testing import capture_logs

from crystalium.storage.graph import GraphStore


class TestGraphStoreBasics:
    def test_add_node_does_not_raise(self, tmp_graph_store: GraphStore) -> None:
        tmp_graph_store.add_node("crystal-001", "semantic")

    def test_add_node_idempotent(self, tmp_graph_store: GraphStore) -> None:
        """Adding the same node twice should not raise."""
        tmp_graph_store.add_node("idem-001", "semantic")
        tmp_graph_store.add_node("idem-001", "semantic")

    def test_node_count_increments(self, tmp_graph_store: GraphStore) -> None:
        initial = tmp_graph_store.node_count()
        tmp_graph_store.add_node("count-001", "episodic")
        assert tmp_graph_store.node_count() == initial + 1

    def test_add_edge_links_to(self, tmp_graph_store: GraphStore) -> None:
        tmp_graph_store.add_node("edge-src", "semantic")
        tmp_graph_store.add_node("edge-dst", "semantic")
        tmp_graph_store.add_edge("edge-src", "edge-dst", "LINKS_TO")

    def test_add_edge_supersedes(self, tmp_graph_store: GraphStore) -> None:
        tmp_graph_store.add_node("sup-old", "semantic")
        tmp_graph_store.add_node("sup-new", "semantic")
        tmp_graph_store.add_edge("sup-old", "sup-new", "SUPERSEDES")

    def test_add_edge_cites(self, tmp_graph_store: GraphStore) -> None:
        tmp_graph_store.add_node("cite-a", "procedural")
        tmp_graph_store.add_node("cite-b", "semantic")
        tmp_graph_store.add_edge("cite-a", "cite-b", "CITES")

    def test_add_edge_invalid_relation_raises(self, tmp_graph_store: GraphStore) -> None:
        tmp_graph_store.add_node("inv-a", "semantic")
        tmp_graph_store.add_node("inv-b", "semantic")
        with pytest.raises(ValueError, match="Invalid relationship type"):
            tmp_graph_store.add_edge("inv-a", "inv-b", "INVENTED_REL")

    def test_add_edge_missing_src_raises(self, tmp_graph_store: GraphStore) -> None:
        tmp_graph_store.add_node("edge-exists", "semantic")
        with pytest.raises(KeyError, match="missing-src"):
            tmp_graph_store.add_edge("missing-src", "edge-exists", "LINKS_TO")

    def test_add_edge_missing_dst_raises(self, tmp_graph_store: GraphStore) -> None:
        tmp_graph_store.add_node("has-src", "semantic")
        with pytest.raises(KeyError, match="missing-dst"):
            tmp_graph_store.add_edge("has-src", "missing-dst", "LINKS_TO")


class TestNeighborExpand:
    def test_neighbor_expand_depth_1(self, tmp_graph_store: GraphStore) -> None:
        """Depth-1 expansion returns direct neighbors."""
        tmp_graph_store.add_node("n-seed", "semantic")
        tmp_graph_store.add_node("n-nbr1", "semantic")
        tmp_graph_store.add_node("n-nbr2", "semantic")
        tmp_graph_store.add_node("n-unconnected", "semantic")
        tmp_graph_store.add_edge("n-seed", "n-nbr1", "LINKS_TO")
        tmp_graph_store.add_edge("n-seed", "n-nbr2", "LINKS_TO")

        neighbors = tmp_graph_store.neighbor_expand(["n-seed"], depth=1)
        assert "n-nbr1" in neighbors
        assert "n-nbr2" in neighbors
        assert "n-seed" not in neighbors  # seed excluded
        assert "n-unconnected" not in neighbors

    def test_neighbor_expand_rel_filter(self, tmp_graph_store: GraphStore) -> None:
        """rel_filter restricts expansion to only that edge type."""
        tmp_graph_store.add_node("rf-a", "semantic")
        tmp_graph_store.add_node("rf-b", "episodic")
        tmp_graph_store.add_node("rf-c", "procedural")
        tmp_graph_store.add_edge("rf-a", "rf-b", "LINKS_TO")
        tmp_graph_store.add_edge("rf-a", "rf-c", "CITES")

        via_links = tmp_graph_store.neighbor_expand(["rf-a"], depth=1, rel_filter="LINKS_TO")
        assert "rf-b" in via_links
        assert "rf-c" not in via_links

    def test_neighbor_expand_empty_seeds(self, tmp_graph_store: GraphStore) -> None:
        result = tmp_graph_store.neighbor_expand([])
        assert result == set()

    def test_neighbor_expand_no_edges(self, tmp_graph_store: GraphStore) -> None:
        tmp_graph_store.add_node("isolated", "execution")
        result = tmp_graph_store.neighbor_expand(["isolated"])
        assert result == set()

    def test_neighbor_expand_invalid_rel_filter_raises(
        self, tmp_graph_store: GraphStore
    ) -> None:
        tmp_graph_store.add_node("rf-test", "semantic")
        with pytest.raises(ValueError, match="Invalid rel_filter"):
            tmp_graph_store.neighbor_expand(["rf-test"], rel_filter="INVENTED")


# ---------------------------------------------------------------------------
# crystalium#41 (+N-1, +N-4): neighbor_expand/all_edges cursor-exhaustion +
# depth>1 pattern repair. These tests are the campaign's red-check (AC-210):
# the kuzu driver RAISES RuntimeError at cursor exhaustion instead of
# returning None from get_next(), and the pre-fix exception boundary sits
# OUTSIDE the per-seed loop, so the first seed's exhaustion unwinds the
# whole loop and seeds 2..N are never queried.
# ---------------------------------------------------------------------------


class TestNeighborExpandMultiSeed:
    """Real-GraphStore, >=2-seed coverage. AC-211..214, AC-217.

    Every existing neighbor_expand test in this file passes a single seed
    (see TestNeighborExpand above) and every other call site in the repo is
    a MagicMock, so the suite was structurally blind to the first-seed-abort
    defect (scout-measured: 27 passed on the buggy code). These tests are
    the ones that must go red on unmodified graph.py before the fix lands.
    """

    def test_multi_seed_expansion_is_union_minus_seeds(
        self, tmp_graph_store: GraphStore
    ) -> None:
        """expand(S) == (union of expand([s]) for s in S) - set(S).

        Not the naive union: graph.py:251's seed-exclusion filter stands
        (#42 is REPORT, out of scope here), so the correct identity
        subtracts the seed set. No seed-to-seed edge in this fixture.
        """
        tmp_graph_store.add_node("ms-a", "semantic")
        tmp_graph_store.add_node("ms-b", "semantic")
        tmp_graph_store.add_node("ms-x", "semantic")
        tmp_graph_store.add_node("ms-y", "semantic")
        tmp_graph_store.add_node("ms-shared", "semantic")
        tmp_graph_store.add_edge("ms-a", "ms-x", "LINKS_TO")
        tmp_graph_store.add_edge("ms-a", "ms-shared", "LINKS_TO")
        tmp_graph_store.add_edge("ms-b", "ms-y", "LINKS_TO")
        tmp_graph_store.add_edge("ms-b", "ms-shared", "LINKS_TO")

        seeds = ["ms-a", "ms-b"]
        multi = tmp_graph_store.neighbor_expand(seeds, depth=1)

        per_seed_union: set[str] = set()
        for seed in seeds:
            per_seed_union |= tmp_graph_store.neighbor_expand([seed], depth=1)
        expected = per_seed_union - set(seeds)

        assert multi == expected
        assert multi == {"ms-x", "ms-y", "ms-shared"}

    def test_edgeless_first_seed_does_not_abort_later_seeds(
        self, tmp_graph_store: GraphStore
    ) -> None:
        """An edge-less FIRST seed must not zero out later seeds' neighbours.

        Scout-measured at 56c8510: expand([e0, s1, s2]) == [] even though s1
        and s2 have real out-edges — worse than "explores one seed", it is
        "aborts at the first cursor exhaustion, whatever that seed yielded".
        """
        tmp_graph_store.add_node("efs-e0", "semantic")  # no out-edges
        tmp_graph_store.add_node("efs-s1", "semantic")
        tmp_graph_store.add_node("efs-s2", "semantic")
        tmp_graph_store.add_node("efs-n1", "semantic")
        tmp_graph_store.add_node("efs-n2", "semantic")
        tmp_graph_store.add_edge("efs-s1", "efs-n1", "LINKS_TO")
        tmp_graph_store.add_edge("efs-s2", "efs-n2", "LINKS_TO")

        result = tmp_graph_store.neighbor_expand(
            ["efs-e0", "efs-s1", "efs-s2"], depth=1
        )

        assert result != set()
        assert result == {"efs-n1", "efs-n2"}

    def test_multi_seed_differs_from_first_seed_alone(
        self, tmp_graph_store: GraphStore
    ) -> None:
        """expand(seeds) != expand([seeds[0]]) on a discriminating fixture.

        Directly negates CHANGELOG.md's recorded symptom
        neighbor_expand(seeds) == neighbor_expand([seeds[0]]).
        """
        tmp_graph_store.add_node("df-a", "semantic")
        tmp_graph_store.add_node("df-b", "semantic")
        tmp_graph_store.add_node("df-only-a", "semantic")
        tmp_graph_store.add_node("df-only-b", "semantic")
        tmp_graph_store.add_edge("df-a", "df-only-a", "LINKS_TO")
        tmp_graph_store.add_edge("df-b", "df-only-b", "LINKS_TO")

        seeds = ["df-a", "df-b"]
        multi = tmp_graph_store.neighbor_expand(seeds, depth=1)
        first_only = tmp_graph_store.neighbor_expand([seeds[0]], depth=1)

        assert multi != first_only
        assert "df-only-b" in multi
        assert "df-only-b" not in first_only

    def test_depth_2_chain_returns_both_hops(
        self, tmp_graph_store: GraphStore
    ) -> None:
        """A plain chain a -> b -> c, no self-loops: depth=2 from [a] is {b, c}.

        N-4: at 56c8510 the chained-pattern query binds every hop to the
        same Cypher variable `b` (pattern.replace('()', '(b:Crystal)')
        rewrites every '()'), matching only self-loops -- this fixture has
        none, so pre-fix this returns set() (or a caught binder error),
        never {"dc-b", "dc-c"}.
        """
        tmp_graph_store.add_node("dc-a", "semantic")
        tmp_graph_store.add_node("dc-b", "semantic")
        tmp_graph_store.add_node("dc-c", "semantic")
        tmp_graph_store.add_edge("dc-a", "dc-b", "LINKS_TO")
        tmp_graph_store.add_edge("dc-b", "dc-c", "LINKS_TO")

        result = tmp_graph_store.neighbor_expand(["dc-a"], depth=2)

        assert result == {"dc-b", "dc-c"}

    def test_decaying_walk_is_hashseed_invariant(
        self, tmp_graph_store: GraphStore
    ) -> None:
        """decaying_walk must be identical under PYTHONHASHSEED 0 and 5.

        graph.py:278 passes list(frontier) from a `set`, whose iteration
        order is per-process hash-randomised. The hop-2 frontier below has
        two members (dw-s1, dw-s2), each with a distinct hop-2 neighbour --
        exactly the shape that exposes both the hash-random order AND the
        first-seed-abort defect: pre-fix, only one branch is ever explored,
        regardless of which PYTHONHASHSEED landed it first. Invoked twice
        (PYTHONHASHSEED=0 and =5) by AC-217's VERIFY line; this single
        assertion must hold under both.
        """
        tmp_graph_store.add_node("dw-seed", "semantic")
        tmp_graph_store.add_node("dw-s1", "semantic")
        tmp_graph_store.add_node("dw-s2", "semantic")
        tmp_graph_store.add_node("dw-n1", "semantic")
        tmp_graph_store.add_node("dw-n2", "semantic")
        tmp_graph_store.add_edge("dw-seed", "dw-s1", "LINKS_TO")
        tmp_graph_store.add_edge("dw-seed", "dw-s2", "LINKS_TO")
        tmp_graph_store.add_edge("dw-s1", "dw-n1", "LINKS_TO")
        tmp_graph_store.add_edge("dw-s2", "dw-n2", "LINKS_TO")

        scores = tmp_graph_store.decaying_walk(["dw-seed"], max_hops=2, decay=0.5)

        assert scores == {
            "dw-s1": 0.5,
            "dw-s2": 0.5,
            "dw-n1": 0.25,
            "dw-n2": 0.25,
        }


# ---------------------------------------------------------------------------
# W-GE1: GraphStore.all_edges tests
# ---------------------------------------------------------------------------


class TestAllEdges:
    """Tests for GraphStore.all_edges (GAP-2, W-GE1)."""

    def test_all_edges_empty_graph(self, tmp_graph_store: GraphStore) -> None:
        """Empty graph returns empty list."""
        edges = tmp_graph_store.all_edges()
        assert edges == []

    def test_all_edges_returns_links_to(self, tmp_graph_store: GraphStore) -> None:
        tmp_graph_store.add_node("ae-a", "semantic")
        tmp_graph_store.add_node("ae-b", "semantic")
        tmp_graph_store.add_edge("ae-a", "ae-b", "LINKS_TO")
        edges = tmp_graph_store.all_edges()
        assert ("ae-a", "ae-b", "LINKS_TO") in edges

    def test_all_edges_rel_filter_links_to(self, tmp_graph_store: GraphStore) -> None:
        tmp_graph_store.add_node("rf-ae-a", "semantic")
        tmp_graph_store.add_node("rf-ae-b", "semantic")
        tmp_graph_store.add_node("rf-ae-c", "semantic")
        tmp_graph_store.add_edge("rf-ae-a", "rf-ae-b", "LINKS_TO")
        tmp_graph_store.add_edge("rf-ae-a", "rf-ae-c", "CITES")
        links = tmp_graph_store.all_edges(rel_filter="LINKS_TO")
        assert all(e[2] == "LINKS_TO" for e in links)
        assert ("rf-ae-a", "rf-ae-b", "LINKS_TO") in links
        assert ("rf-ae-a", "rf-ae-c", "CITES") not in links

    def test_all_edges_rel_filter_invalid_raises(self, tmp_graph_store: GraphStore) -> None:
        with pytest.raises(ValueError, match="Invalid rel_filter"):
            tmp_graph_store.all_edges(rel_filter="INVENTED_REL")

    def test_all_edges_tuple_shape(self, tmp_graph_store: GraphStore) -> None:
        tmp_graph_store.add_node("shape-a", "episodic")
        tmp_graph_store.add_node("shape-b", "episodic")
        tmp_graph_store.add_edge("shape-a", "shape-b", "LINKS_TO")
        edges = tmp_graph_store.all_edges(rel_filter="LINKS_TO")
        assert len(edges) >= 1
        from_id, to_id, rel_type = edges[0]
        assert isinstance(from_id, str)
        assert isinstance(to_id, str)
        assert rel_type in {"LINKS_TO", "SUPERSEDES", "CITES"}

    def test_all_edges_limit_bounds(self, tmp_graph_store: GraphStore) -> None:
        """all_edges with limit=1 returns at most 1 edge."""
        tmp_graph_store.add_node("lim-a", "semantic")
        tmp_graph_store.add_node("lim-b", "semantic")
        tmp_graph_store.add_node("lim-c", "semantic")
        tmp_graph_store.add_edge("lim-a", "lim-b", "LINKS_TO")
        tmp_graph_store.add_edge("lim-a", "lim-c", "LINKS_TO")
        edges = tmp_graph_store.all_edges(rel_filter="LINKS_TO", limit=1)
        assert len(edges) <= 1

    def test_all_edges_pagination(self, tmp_graph_store: GraphStore) -> None:
        """Offset pagination returns disjoint batches."""
        tmp_graph_store.add_node("pag-a", "semantic")
        tmp_graph_store.add_node("pag-b", "semantic")
        tmp_graph_store.add_node("pag-c", "semantic")
        tmp_graph_store.add_edge("pag-a", "pag-b", "LINKS_TO")
        tmp_graph_store.add_edge("pag-a", "pag-c", "LINKS_TO")
        page1 = tmp_graph_store.all_edges(rel_filter="LINKS_TO", limit=1, offset=0)
        page2 = tmp_graph_store.all_edges(rel_filter="LINKS_TO", limit=1, offset=1)
        assert len(page1) <= 1
        assert len(page2) <= 1
        if page1 and page2:
            assert page1[0] != page2[0]

    def test_null_graph_store_all_edges_returns_empty(self) -> None:
        """_NullGraphStore.all_edges must return []."""
        from crystalium.server import _NullGraphStore
        null_store = _NullGraphStore()
        assert null_store.all_edges() == []
        assert null_store.all_edges(rel_filter="LINKS_TO") == []
        assert null_store.all_edges(rel_filter="LINKS_TO", limit=100) == []


# ---------------------------------------------------------------------------
# crystalium#41 N-1: all_edges cursor-exhaustion idiom. AC-215, AC-216.
# ---------------------------------------------------------------------------


class TestAllEdgesCursor:
    """all_edges must use the has_next() idiom, not the dead `is None` check.

    N-1: at 56c8510 all_edges survives only by accident -- its try is
    per-rel-type *inside* the outer loop, so each rel's rows are fully
    collected before that rel's own cursor-exhaustion raise. Correctness
    is one refactor away from breaking, and every healthy call logs one
    spurious `all_edges_rel_error` warning per rel type queried.
    """

    def test_all_edges_complete_across_rel_types(
        self, tmp_graph_store: GraphStore
    ) -> None:
        """>=2 distinct rel types: every edge of every queried type comes back."""
        tmp_graph_store.add_node("aec-a", "semantic")
        tmp_graph_store.add_node("aec-b", "semantic")
        tmp_graph_store.add_node("aec-c", "semantic")
        tmp_graph_store.add_edge("aec-a", "aec-b", "LINKS_TO")
        tmp_graph_store.add_edge("aec-a", "aec-c", "CITES")
        tmp_graph_store.add_edge("aec-b", "aec-c", "SUPERSEDES")

        edges = tmp_graph_store.all_edges()
        edge_set = set(edges)

        assert len(edges) == 3
        assert ("aec-a", "aec-b", "LINKS_TO") in edge_set
        assert ("aec-a", "aec-c", "CITES") in edge_set
        assert ("aec-b", "aec-c", "SUPERSEDES") in edge_set

    def test_all_edges_emits_no_rel_error_on_healthy_path(
        self, tmp_graph_store: GraphStore
    ) -> None:
        """A fully healthy all_edges() call must log no all_edges_rel_error.

        At 56c8510, `graph.py:333-339`'s `is None` check never fires (kuzu
        raises instead of returning None), so exhaustion always raises into
        the per-rel `except`, which logs `all_edges_rel_error` on *every*
        rel type queried -- even when every sub-query succeeded.
        """
        tmp_graph_store.add_node("aen-a", "semantic")
        tmp_graph_store.add_node("aen-b", "semantic")
        tmp_graph_store.add_edge("aen-a", "aen-b", "LINKS_TO")
        tmp_graph_store.add_edge("aen-a", "aen-b", "CITES")

        with capture_logs() as logs:
            edges = tmp_graph_store.all_edges()

        assert len(edges) == 2
        rel_error_events = [e for e in logs if e.get("event") == "all_edges_rel_error"]
        assert rel_error_events == []


# ---------------------------------------------------------------------------
# crystalium#42 (W-42, FORGE D2) -- exclude_seeds oracle.
#
# Three topologies, all mandatory (spec.amend-01.md Sec B.5.2): a single
# fixture cannot attribute across the three sites (graph.py:225/:271/:272,
# :302, :305) that each bind on a different shape.
#
#   T1 -- depth 1, frontier-mates. Seeds {S1,S2}; S1->S2, S1->N1. This is the
#         topology on which the ORIGINAL (2-site) spec's exclude_seeds=False
#         was a no-op -- with the full 5-site threading it now differs, so
#         AC-351's default-flip red-check genuinely goes red HERE.
#   T2 -- depth 2. Seeds {S1,S2}; S1->M, M->S2, M->N2. Exercises :272 at hop
#         2 and DISCHARGES the graph.py:266 (`visited = set(frontier)`)
#         proof obligation: S2 is a hop-2-discovered seed that must appear
#         in result_ids with :266 left unconditional.
#   T3 -- walk. Same graph as T2, via decaying_walk. Exercises :302/:305.
#   T3-variant -- seeds {S1,S2}, single edge S1->S2. decaying_walk's
#         True-branch value is EMPTY ({}), which is exactly what a dead
#         store would also return -- so its node carries a mandatory
#         liveness guard (node_count()==2, len(all_edges())==1) rather than
#         trusting an empty expectation on its own (K-B16 form).
#
# These builders are named `_build_t*` and are module-private to this
# oracle -- normative node names below (AC-350, AC-354) are module-level
# functions, NOT class-nested, per spec.criteria.amend-01/03.md.
# ---------------------------------------------------------------------------


def _build_t1(graph: GraphStore) -> list[str]:
    graph.add_node("t1-s1", "semantic")
    graph.add_node("t1-s2", "semantic")
    graph.add_node("t1-n1", "semantic")
    graph.add_edge("t1-s1", "t1-s2", "LINKS_TO")
    graph.add_edge("t1-s1", "t1-n1", "LINKS_TO")
    return ["t1-s1", "t1-s2"]


def _build_t2(graph: GraphStore) -> list[str]:
    graph.add_node("t2-s1", "semantic")
    graph.add_node("t2-s2", "semantic")
    graph.add_node("t2-m", "semantic")
    graph.add_node("t2-n2", "semantic")
    graph.add_edge("t2-s1", "t2-m", "LINKS_TO")
    graph.add_edge("t2-m", "t2-s2", "LINKS_TO")
    graph.add_edge("t2-m", "t2-n2", "LINKS_TO")
    return ["t2-s1", "t2-s2"]


def _build_t3_variant(graph: GraphStore) -> list[str]:
    graph.add_node("t3v-s1", "semantic")
    graph.add_node("t3v-s2", "semantic")
    graph.add_edge("t3v-s1", "t3v-s2", "LINKS_TO")
    return ["t3v-s1", "t3v-s2"]


@pytest.mark.parametrize(
    "topology",
    ["T1", "T2", "T3", "T3-variant"],
)
def test_exclude_seeds_default_is_byte_identical(
    topology: str, tmp_graph_store: GraphStore
) -> None:
    """AC-350: exclude_seeds=True (the default) is byte-identical to `b7f1a47`
    on all four topologies (FORGE D2)."""
    if topology == "T1":
        seeds = _build_t1(tmp_graph_store)
        result = tmp_graph_store.neighbor_expand(seeds, depth=1)
        assert result == {"t1-n1"}
    elif topology == "T2":
        seeds = _build_t2(tmp_graph_store)
        result = tmp_graph_store.neighbor_expand(seeds, depth=2)
        assert result == {"t2-m", "t2-n2"}
    elif topology == "T3":
        seeds = _build_t2(tmp_graph_store)
        scores = tmp_graph_store.decaying_walk(seeds, max_hops=2, decay=0.5)
        assert scores == {"t2-m": 0.5, "t2-n2": 0.25}
    else:  # T3-variant
        seeds = _build_t3_variant(tmp_graph_store)
        scores = tmp_graph_store.decaying_walk(seeds, max_hops=2, decay=0.5)
        # Mandatory liveness guard (K-B16 form): the expected value here is
        # EMPTY, exactly what a dead/never-came-up store would also return.
        # Without this the case would pass on a kuzu error.
        assert tmp_graph_store.node_count() == 2
        assert len(tmp_graph_store.all_edges()) == 1
        assert scores == {}


@pytest.mark.parametrize(
    "topology",
    ["T1", "T2", "T3", "T3-variant"],
)
def test_exclude_seeds_false_expected_sets(
    topology: str, tmp_graph_store: GraphStore
) -> None:
    """AC-354: exclude_seeds=False (the opt-in relaxation) produces exactly
    the enumerated False-branch sets/weights, including a seed credited at
    its true shortest-hop distance (FORGE D2).

    T2 additionally DISCHARGES the graph.py:266 (`visited = set(frontier)`)
    proof obligation: S2 is a hop-2-discovered seed and appears in
    result_ids here with :266 deliberately left unconditional -- if it were
    absent, :266 (or the frontier arithmetic) would be load-bearing for
    membership after all and the D2 ruling's :266-unchanged clause would be
    overturned.
    """
    if topology == "T1":
        seeds = _build_t1(tmp_graph_store)
        result = tmp_graph_store.neighbor_expand(seeds, depth=1, exclude_seeds=False)
        assert result == {"t1-n1", "t1-s2"}
    elif topology == "T2":
        seeds = _build_t2(tmp_graph_store)
        result = tmp_graph_store.neighbor_expand(seeds, depth=2, exclude_seeds=False)
        assert result == {"t2-m", "t2-n2", "t2-s2"}
    elif topology == "T3":
        seeds = _build_t2(tmp_graph_store)
        scores = tmp_graph_store.decaying_walk(
            seeds, max_hops=2, decay=0.5, exclude_seeds=False
        )
        assert scores == {"t2-m": 0.5, "t2-n2": 0.25, "t2-s2": 0.25}
    else:  # T3-variant -- hop-1 seed credit
        seeds = _build_t3_variant(tmp_graph_store)
        scores = tmp_graph_store.decaying_walk(
            seeds, max_hops=2, decay=0.5, exclude_seeds=False
        )
        assert scores == {"t3v-s2": 0.5}
