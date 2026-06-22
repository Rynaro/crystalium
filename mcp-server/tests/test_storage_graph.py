"""Tests for GraphStore — KuzuDB crystal relationship graph.

Container-first: run via:
  docker compose run --rm crystalium pytest mcp-server/tests/test_storage_graph.py -v
"""

from __future__ import annotations

import pytest

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
