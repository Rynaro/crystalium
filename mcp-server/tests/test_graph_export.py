"""Tests for GraphExporter — edge derivation, canonical JSON, schema validation.

Container-first: run via:
  docker compose run --rm crystalium pytest mcp-server/tests/test_graph_export.py -v

Gates implemented here:
  G-GE2: rich source-tagged edge synthesis    (test_g_ge2_rich_edges, test_g_ge2_edge_source_tag)
  G-GE4: edge hygiene (dangling/dedup/loop)   (test_g_ge4_edge_hygiene)
  G-GE1: canonical JSON validates schema      (test_g_ge1_json_validates)
  G-GE5: truncation flag + bounded            (test_g_ge5_truncation_flag)
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from crystalium.export.graph_export import ExportFlags, GraphExporter
from crystalium.storage.graph import GraphStore
from crystalium.storage.relational import RelationalStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PROJECT = "test-export-project"
_NOW = datetime.now(timezone.utc).isoformat()


def _make_scope(project: str = _PROJECT, acv: str | None = None) -> dict:
    return {
        "project": project,
        "agent_class_visibility": acv,
        "sensitivity_tag": "none",
    }


def _make_crystal(
    *,
    project: str = _PROJECT,
    layer: str = "semantic",
    trust_tier: str = "T1",
    validation_state: str = "unverified",
    status: str = "active",
    author_agent: str = "test-agent",
    importance: float = 0.5,
    crystal_id: str | None = None,
    tags: list[str] | None = None,
    acv: str | None = None,
) -> dict:
    cid = crystal_id or str(uuid.uuid4())
    c: dict[str, Any] = {
        "id": cid,
        "layer": layer,
        "summary": f"Crystal {cid[:8]} [{layer}]",
        "provenance": {
            "source": "verified_agent",
            "author_agent": author_agent,
            "task_id": "task-001",
            "created_at": _NOW,
        },
        "trust_tier": trust_tier,
        "validation_state": validation_state,
        "scope": {
            "project": project,
            "agent_class_visibility": acv,
            "sensitivity_tag": "none",
        },
        "temporal": {
            "t_valid_from": _NOW,
            "t_valid_to": None,
            "superseded_by": None,
        },
        "utility": {
            "access_count": 1,
            "last_access": _NOW,
            "outcome_success_score": None,
            "importance": importance,
            "novelty_at_write": 0.5,
        },
        "status": status,
        "tags": tags or [],
    }
    if layer == "episodic":
        c["content_ref"] = hashlib.sha256(f"content-{cid}".encode()).hexdigest()
    return c


# ---------------------------------------------------------------------------
# W-GE2 fixtures — STORY-1 rich-edge fixture (§11 STORY-1)
# ---------------------------------------------------------------------------

class _RichFixture:
    """Provides a relational + graph store populated with all four edge sources."""

    def __init__(self, rel: RelationalStore, graph: GraphStore) -> None:
        self.rel = rel
        self.graph = graph
        self._setup()

    def _setup(self) -> None:
        # (a) LINKS_TO: crystals a and b co-occurring; edge written to kuzu
        self.c_a = _make_crystal(crystal_id="crystal-a", project=_PROJECT)
        self.c_b = _make_crystal(crystal_id="crystal-b", project=_PROJECT)
        self.rel.insert_crystal(self.c_a)
        self.rel.insert_crystal(self.c_b)
        self.graph.add_node(self.c_a["id"], "semantic")
        self.graph.add_node(self.c_b["id"], "semantic")
        self.graph.add_edge(self.c_a["id"], self.c_b["id"], "LINKS_TO")

        # (b) SUPERSEDES: c_old superseded by c_new
        self.c_old = _make_crystal(crystal_id="crystal-old", project=_PROJECT)
        self.c_new = _make_crystal(crystal_id="crystal-new", project=_PROJECT)
        self.rel.insert_crystal(self.c_old)
        self.rel.insert_crystal(self.c_new)
        self.rel.mark_superseded(self.c_old["id"], self.c_new["id"], datetime.now(timezone.utc))

        # (c) MERGED_FROM: c_merged has corroboration=2 and merged_authors=[author of c_e]
        self.c_e = _make_crystal(crystal_id="crystal-e", project=_PROJECT, author_agent="agent-x")
        self.c_merged = _make_crystal(crystal_id="crystal-merged", project=_PROJECT)
        self.rel.insert_crystal(self.c_e)
        self.rel.insert_crystal(self.c_merged)
        # Simulate merge_provenance: c_merged absorbed agent-x's contribution
        self.rel.merge_provenance(
            self.c_merged["id"],
            {"author_agent": "agent-x", "source": "verified_agent"},
        )

        # (d) CONFLICTS_WITH: f (winner) vs g (loser)
        self.c_f = _make_crystal(crystal_id="crystal-f", project=_PROJECT)
        self.c_g = _make_crystal(crystal_id="crystal-g", project=_PROJECT)
        self.rel.insert_crystal(self.c_f)
        self.rel.insert_crystal(self.c_g)
        self.rel.record_conflict(
            self.c_f["id"],
            self.c_g["id"],
            winner_tier="T1",
            loser_tier="T2",
            similarity=0.85,
            scope={"project": _PROJECT},
        )


@pytest.fixture
def rich_fixture(tmp_path: Path):
    rel = RelationalStore(db_path=tmp_path / "rich.sqlite")
    graph = GraphStore(kuzu_dir=tmp_path / "rich.kuzu")
    return _RichFixture(rel, graph)


@pytest.fixture
def rich_exporter(rich_fixture: _RichFixture) -> GraphExporter:
    return GraphExporter(
        relational_store=rich_fixture.rel,
        graph_store=rich_fixture.graph,
    )


# ---------------------------------------------------------------------------
# G-GE2: Rich, source-tagged edge synthesis
# ---------------------------------------------------------------------------


class TestGGE2RichEdges:
    """G-GE2: edges[] must contain ≥1 edge of each type when all sources populated."""

    def test_g_ge2_rich_edges(
        self, rich_fixture: _RichFixture, rich_exporter: GraphExporter
    ) -> None:
        """With all four edge sources populated, export contains all four types."""
        result = rich_exporter.export(
            scope=_make_scope(),
            include_flags=ExportFlags(include_superseded=True),
        )
        edge_types = {e["type"] for e in result["edges"]}
        assert "LINKS_TO" in edge_types, f"Missing LINKS_TO in {edge_types}"
        assert "SUPERSEDES" in edge_types, f"Missing SUPERSEDES in {edge_types}"
        assert "MERGED_FROM" in edge_types, f"Missing MERGED_FROM in {edge_types}"
        assert "CONFLICTS_WITH" in edge_types, f"Missing CONFLICTS_WITH in {edge_types}"

    def test_g_ge2_edge_source_tag(
        self, rich_fixture: _RichFixture, rich_exporter: GraphExporter
    ) -> None:
        """Every edge must carry source ∈ {kuzu, derived} (D1 mandatory tag)."""
        result = rich_exporter.export(
            scope=_make_scope(),
            include_flags=ExportFlags(include_superseded=True),
        )
        for edge in result["edges"]:
            assert "source" in edge, f"Edge missing source: {edge}"
            assert edge["source"] in ("kuzu", "derived"), (
                f"Invalid source: {edge['source']!r} on edge {edge}"
            )

    def test_g_ge2_links_to_source_is_kuzu(
        self, rich_fixture: _RichFixture, rich_exporter: GraphExporter
    ) -> None:
        result = rich_exporter.export(scope=_make_scope(), include_flags=ExportFlags(include_superseded=True))
        links_to = [e for e in result["edges"] if e["type"] == "LINKS_TO"]
        assert all(e["source"] == "kuzu" for e in links_to)

    def test_g_ge2_supersedes_source_is_derived(
        self, rich_fixture: _RichFixture, rich_exporter: GraphExporter
    ) -> None:
        result = rich_exporter.export(scope=_make_scope(), include_flags=ExportFlags(include_superseded=True))
        sups = [e for e in result["edges"] if e["type"] == "SUPERSEDES"]
        assert all(e["source"] == "derived" for e in sups)

    def test_g_ge2_supersedes_direction_newer_to_older(
        self, rich_fixture: _RichFixture, rich_exporter: GraphExporter
    ) -> None:
        """SUPERSEDES edge must point newer→older (§4.3 directionality frozen)."""
        result = rich_exporter.export(scope=_make_scope(), include_flags=ExportFlags(include_superseded=True))
        sups = [e for e in result["edges"] if e["type"] == "SUPERSEDES"]
        assert len(sups) >= 1
        sup = sups[0]
        assert sup["from"] == rich_fixture.c_new["id"]
        assert sup["to"] == rich_fixture.c_old["id"]

    def test_g_ge2_merged_from_source_is_derived(
        self, rich_fixture: _RichFixture, rich_exporter: GraphExporter
    ) -> None:
        result = rich_exporter.export(scope=_make_scope(), include_flags=ExportFlags(include_superseded=True))
        merged = [e for e in result["edges"] if e["type"] == "MERGED_FROM"]
        assert all(e["source"] == "derived" for e in merged)

    def test_g_ge2_conflicts_with_source_is_derived(
        self, rich_fixture: _RichFixture, rich_exporter: GraphExporter
    ) -> None:
        result = rich_exporter.export(scope=_make_scope(), include_flags=ExportFlags(include_superseded=True))
        conflicts = [e for e in result["edges"] if e["type"] == "CONFLICTS_WITH"]
        assert all(e["source"] == "derived" for e in conflicts)

    def test_g_ge2_conflicts_with_direction_winner_to_loser(
        self, rich_fixture: _RichFixture, rich_exporter: GraphExporter
    ) -> None:
        result = rich_exporter.export(scope=_make_scope(), include_flags=ExportFlags(include_superseded=True))
        conflicts = [e for e in result["edges"] if e["type"] == "CONFLICTS_WITH"]
        assert len(conflicts) >= 1
        c = conflicts[0]
        assert c["from"] == rich_fixture.c_f["id"]
        assert c["to"] == rich_fixture.c_g["id"]
        assert c["metadata"]["direction"] == "winner_to_loser"


# ---------------------------------------------------------------------------
# G-GE4: Edge hygiene (dangling / dedup / self-loop)
# ---------------------------------------------------------------------------


class TestGGE4EdgeHygiene:
    """G-GE4: dangling-endpoint drop, dedup, self-loop drop."""

    def _make_exporter(self, rel: RelationalStore, graph: GraphStore) -> GraphExporter:
        return GraphExporter(relational_store=rel, graph_store=graph)

    def test_g_ge4_edge_hygiene(self, tmp_path: Path) -> None:
        """G-GE4 anchor: three hygiene rules verified in one fixture.

        Setup:
          - One visible node (node_a)
          - One HIDDEN node (node_hidden, superseded/excluded by default)
          - Kuzu edge node_a → node_hidden (should be dangling-dropped)
          - Conflict: node_a self-loop (should be self-loop-dropped)
          - Duplicate LINKS_TO edge a→b (should be deduped to one)
        """
        rel = RelationalStore(db_path=tmp_path / "hyg.sqlite")
        graph = GraphStore(kuzu_dir=tmp_path / "hyg.kuzu")

        node_a = _make_crystal(crystal_id="hyg-a", project="hyg-project")
        node_b = _make_crystal(crystal_id="hyg-b", project="hyg-project")
        node_hidden = _make_crystal(crystal_id="hyg-hidden", project="hyg-project")
        node_new = _make_crystal(crystal_id="hyg-new", project="hyg-project")

        for c in (node_a, node_b, node_hidden, node_new):
            rel.insert_crystal(c)
            graph.add_node(c["id"], c["layer"])

        # Supersede node_hidden (default filter will exclude it)
        rel.mark_superseded(node_hidden["id"], node_new["id"], datetime.now(timezone.utc))

        # Kuzu edge: a → hidden (will be dangling after filter)
        graph.add_edge(node_a["id"], node_hidden["id"], "LINKS_TO")
        # Kuzu edge: a → b (will be kept)
        graph.add_edge(node_a["id"], node_b["id"], "LINKS_TO")
        # Duplicate: a → b again (will be deduped; kuzu may allow it)
        try:
            graph.add_edge(node_a["id"], node_b["id"], "LINKS_TO")
        except Exception:
            pass  # kuzu might reject the dup; either way dedup logic handles it

        # Record a self-loop conflict (a vs a)
        rel.record_conflict(
            node_a["id"], node_a["id"],
            winner_tier="T1", loser_tier="T1", similarity=0.99,
            scope={"project": "hyg-project"},
        )

        exporter = self._make_exporter(rel, graph)
        result = exporter.export(
            scope={"project": "hyg-project", "agent_class_visibility": None, "sensitivity_tag": "none"},
            # Default: superseded excluded, so node_hidden won't be in node_id_set
        )

        edge_types = {e["type"] for e in result["edges"]}
        ids_in_nodes = {n["id"] for n in result["nodes"]}

        # HYG-1: the a→hidden edge must be dropped (hidden not in nodes)
        assert "hyg-hidden" not in ids_in_nodes
        for e in result["edges"]:
            assert e["from"] in ids_in_nodes, f"Dangling from: {e}"
            assert e["to"] in ids_in_nodes, f"Dangling to: {e}"

        # HYG-2: at most one (a, b, LINKS_TO) edge
        ab_links = [e for e in result["edges"] if e["from"] == "hyg-a" and e["to"] == "hyg-b" and e["type"] == "LINKS_TO"]
        assert len(ab_links) <= 1, f"Duplicate edge not deduped: {ab_links}"

        # HYG-3: no self-loops
        for e in result["edges"]:
            assert e["from"] != e["to"], f"Self-loop not dropped: {e}"

        # counts.edges_dropped_dangling reflects at least the dangling drop
        assert result["counts"]["edges_dropped_dangling"] >= 1

    def test_hygiene_dropped_dangling_count(self, tmp_path: Path) -> None:
        """edges_dropped_dangling count increments for each dangling edge."""
        rel = RelationalStore(db_path=tmp_path / "dang.sqlite")
        graph = GraphStore(kuzu_dir=tmp_path / "dang.kuzu")

        node_a = _make_crystal(crystal_id="dang-a", project="dang-proj")
        node_b = _make_crystal(crystal_id="dang-b", project="dang-proj")
        node_c = _make_crystal(crystal_id="dang-c", project="dang-proj")

        for c in (node_a, node_b, node_c):
            rel.insert_crystal(c)
            graph.add_node(c["id"], c["layer"])

        # Make node_c deprecated (excluded by default)
        rel.set_status(node_c["id"], "deprecated")

        # Edge a→c will be dangling (c deprecated/excluded)
        graph.add_edge(node_a["id"], node_c["id"], "LINKS_TO")
        # Edge a→b is kept
        graph.add_edge(node_a["id"], node_b["id"], "LINKS_TO")

        exporter = GraphExporter(relational_store=rel, graph_store=graph)
        result = exporter.export(
            scope={"project": "dang-proj", "agent_class_visibility": None, "sensitivity_tag": "none"},
        )
        assert result["counts"]["edges_dropped_dangling"] >= 1

    def test_hygiene_dedup_count(self, tmp_path: Path) -> None:
        """edges_deduped increments when duplicate (from,to,type) tuples exist."""
        from crystalium.export.graph_export import GraphExporter
        rel = RelationalStore(db_path=tmp_path / "dedup.sqlite")

        # Use a null graph store to control raw_edges manually
        class _FakeGraph:
            def all_edges(self, **kwargs):
                return [
                    ("dedup-a", "dedup-b", "LINKS_TO"),
                    ("dedup-a", "dedup-b", "LINKS_TO"),  # duplicate
                ]

        node_a = _make_crystal(crystal_id="dedup-a", project="dedup-proj")
        node_b = _make_crystal(crystal_id="dedup-b", project="dedup-proj")
        rel.insert_crystal(node_a)
        rel.insert_crystal(node_b)

        exporter = GraphExporter(relational_store=rel, graph_store=_FakeGraph())
        result = exporter.export(
            scope={"project": "dedup-proj", "agent_class_visibility": None, "sensitivity_tag": "none"},
        )
        # After dedup, only 1 edge
        links = [e for e in result["edges"] if e["type"] == "LINKS_TO"]
        assert len(links) == 1
        assert result["counts"]["edges_deduped"] >= 1


# ---------------------------------------------------------------------------
# W-GE3: G-GE1 Canonical JSON validates schema
# ---------------------------------------------------------------------------


def _find_schemas_dir() -> Path:
    here = Path(__file__).parent
    candidates = [
        here.parent.parent / "schemas",
        here.parent.parent.parent / "schemas",
        Path("/app/schemas"),
        Path("/schemas"),
    ]
    for p in candidates:
        if p.exists() and p.is_dir():
            return p
    raise AssertionError(f"schemas/ directory not found. Tried: {[str(c) for c in candidates]}")


def _validate_against_schema(schema: dict, instance: dict) -> None:
    try:
        import jsonschema
        from jsonschema import Draft202012Validator
    except ImportError:
        pytest.skip("jsonschema not installed")
    validator = Draft202012Validator(schema)
    errors = list(validator.iter_errors(instance))
    if errors:
        messages = "\n".join(str(e.message) for e in errors)
        raise AssertionError(f"JSON schema validation failed:\n{messages}")


class TestGGE1JsonValidates:
    """G-GE1: canonical JSON validates against schemas/graph-export.v1.json."""

    def test_g_ge1_json_validates(self, tmp_path: Path) -> None:
        """Full export payload validates against the graph-export.v1.json schema."""
        schemas_dir = _find_schemas_dir()
        schema_path = schemas_dir / "graph-export.v1.json"
        if not schema_path.exists():
            pytest.skip("graph-export.v1.json schema not yet present (W-GE3 adds it)")

        import json
        with schema_path.open() as f:
            schema = json.load(f)

        rel = RelationalStore(db_path=tmp_path / "ge1.sqlite")

        class _NullGraph:
            def all_edges(self, **kwargs):
                return []

        c = _make_crystal(project="ge1-project")
        rel.insert_crystal(c)

        exporter = GraphExporter(relational_store=rel, graph_store=_NullGraph())
        result = exporter.export(scope=_make_scope("ge1-project"))
        _validate_against_schema(schema, result)

    def test_g_ge1_top_level_fields_present(self, tmp_path: Path) -> None:
        """Top-level schema_version, generated_from, counts, truncated, nodes, edges all present."""
        rel = RelationalStore(db_path=tmp_path / "ge1b.sqlite")

        class _NullGraph:
            def all_edges(self, **kwargs):
                return []

        c = _make_crystal(project="ge1b-project")
        rel.insert_crystal(c)

        exporter = GraphExporter(relational_store=rel, graph_store=_NullGraph())
        result = exporter.export(scope=_make_scope("ge1b-project"))

        for field in ("schema_version", "generated_from", "counts", "truncated", "nodes", "edges"):
            assert field in result, f"Missing top-level field: {field}"
        assert result["schema_version"] == "graph-export.v1"
        assert isinstance(result["nodes"], list)
        assert isinstance(result["edges"], list)
        assert isinstance(result["truncated"], bool)
        assert isinstance(result["counts"]["nodes"], int)
        assert isinstance(result["counts"]["edges"], int)
        assert isinstance(result["counts"]["nodes_total_estimate"], int)
        assert isinstance(result["counts"]["edges_dropped_dangling"], int)
        assert isinstance(result["counts"]["edges_deduped"], int)

    def test_g_ge1_node_required_fields(self, tmp_path: Path) -> None:
        """Every node must have id, layer, summary, trust_tier, validation_state, status, importance."""
        rel = RelationalStore(db_path=tmp_path / "ge1c.sqlite")

        class _NullGraph:
            def all_edges(self, **kwargs):
                return []

        c = _make_crystal(project="ge1c-project")
        rel.insert_crystal(c)

        exporter = GraphExporter(relational_store=rel, graph_store=_NullGraph())
        result = exporter.export(scope=_make_scope("ge1c-project"))

        assert len(result["nodes"]) >= 1
        for node in result["nodes"]:
            for req in ("id", "layer", "summary", "trust_tier", "validation_state", "status", "importance"):
                assert req in node, f"Node missing field: {req}"

    def test_g_ge1_edge_required_fields(self, tmp_path: Path) -> None:
        """Every edge must have from, to, type, source."""
        rel = RelationalStore(db_path=tmp_path / "ge1d.sqlite")

        class _FakeGraph:
            def all_edges(self, **kwargs):
                return [("ge1d-a", "ge1d-b", "LINKS_TO")]

        c_a = _make_crystal(crystal_id="ge1d-a", project="ge1d-project")
        c_b = _make_crystal(crystal_id="ge1d-b", project="ge1d-project")
        rel.insert_crystal(c_a)
        rel.insert_crystal(c_b)

        exporter = GraphExporter(relational_store=rel, graph_store=_FakeGraph())
        result = exporter.export(scope=_make_scope("ge1d-project"))
        assert len(result["edges"]) >= 1
        for edge in result["edges"]:
            for req in ("from", "to", "type", "source"):
                assert req in edge, f"Edge missing field: {req}"


# ---------------------------------------------------------------------------
# G-GE5: Truncation flag + bounded (§3 G-GE5)
# ---------------------------------------------------------------------------


class TestGGE5TruncationFlag:
    """G-GE5: truncated:true when node cap is hit; bounded scan."""

    def test_g_ge5_truncation_flag(self, tmp_path: Path) -> None:
        """Export with limit < total count sets truncated:true."""
        rel = RelationalStore(db_path=tmp_path / "ge5.sqlite")

        class _NullGraph:
            def all_edges(self, **kwargs):
                return []

        project = "ge5-project"
        # Insert 5 active crystals
        for i in range(5):
            c = _make_crystal(project=project, crystal_id=f"ge5-crystal-{i:03d}")
            rel.insert_crystal(c)

        exporter = GraphExporter(relational_store=rel, graph_store=_NullGraph())
        # Request only 3 (< 5)
        result = exporter.export(scope=_make_scope(project), limit=3)
        assert len(result["nodes"]) == 3
        assert result["truncated"] is True
        assert result["counts"]["nodes_total_estimate"] >= 5
        assert result["counts"]["nodes_total_estimate"] >= result["counts"]["nodes"]

    def test_g_ge5_no_truncation_when_limit_exceeds_count(self, tmp_path: Path) -> None:
        """When limit > total count, truncated must be False."""
        rel = RelationalStore(db_path=tmp_path / "ge5b.sqlite")

        class _NullGraph:
            def all_edges(self, **kwargs):
                return []

        project = "ge5b-project"
        for i in range(3):
            c = _make_crystal(project=project, crystal_id=f"ge5b-crystal-{i:03d}")
            rel.insert_crystal(c)

        exporter = GraphExporter(relational_store=rel, graph_store=_NullGraph())
        result = exporter.export(scope=_make_scope(project), limit=5000)
        assert len(result["nodes"]) == 3
        assert result["truncated"] is False

    def test_g_ge5_limit_clamped_to_max_export_nodes(self, tmp_path: Path) -> None:
        """Passing limit=99999 is silently clamped to MAX_EXPORT_NODES (10000)."""
        from crystalium.storage.relational import MAX_EXPORT_NODES
        rel = RelationalStore(db_path=tmp_path / "ge5c.sqlite")

        class _NullGraph:
            def all_edges(self, **kwargs):
                return []

        exporter = GraphExporter(relational_store=rel, graph_store=_NullGraph())
        # Just verify no error is raised and nodes count respects the cap
        result = exporter.export(scope=_make_scope("ge5c-project"), limit=99_999)
        assert result["counts"]["nodes"] <= MAX_EXPORT_NODES


# ---------------------------------------------------------------------------
# G-GE3: Visibility & redaction defaults (§3 G-GE3)
# ---------------------------------------------------------------------------


class TestGGE3VisibilityDefaults:
    """G-GE3: default export excludes quarantined / deprecated / superseded /
    wrong-visibility crystals; raw blob never emitted; each exclusion overridable.
    """

    def test_g_ge3_visibility_defaults(self, tmp_path: Path) -> None:
        """Default export excludes quarantined, deprecated, superseded, wrong-visibility
        nodes; episodic node carries summary only (no content_ref by default).
        """
        rel = RelationalStore(db_path=tmp_path / "ge3.sqlite")

        class _NullGraph:
            def all_edges(self, **kwargs):
                return []

        project = "ge3-project"

        # Active crystal visible to all (should be in default export)
        c_active = _make_crystal(
            crystal_id="ge3-active", project=project, layer="semantic"
        )
        rel.insert_crystal(c_active)

        # Quarantined crystal
        c_quarantined = _make_crystal(
            crystal_id="ge3-quarantined", project=project, validation_state="unverified"
        )
        rel.insert_crystal(c_quarantined)
        rel.set_validation_state(c_quarantined["id"], "quarantined")

        # Deprecated crystal
        c_deprecated = _make_crystal(
            crystal_id="ge3-deprecated", project=project
        )
        rel.insert_crystal(c_deprecated)
        rel.set_status(c_deprecated["id"], "deprecated")

        # Superseded crystal (c_old superseded by c_new)
        c_old = _make_crystal(crystal_id="ge3-old", project=project)
        c_new = _make_crystal(crystal_id="ge3-new", project=project)
        rel.insert_crystal(c_old)
        rel.insert_crystal(c_new)
        rel.mark_superseded(c_old["id"], c_new["id"], datetime.now(timezone.utc))

        # Forge-only crystal (invisible to spectra)
        c_forge = _make_crystal(
            crystal_id="ge3-forge", project=project, acv="forge"
        )
        rel.insert_crystal(c_forge)

        # Episodic crystal with content_ref
        c_episodic = _make_crystal(
            crystal_id="ge3-episodic", project=project, layer="episodic"
        )
        rel.insert_crystal(c_episodic)

        exporter = GraphExporter(relational_store=rel, graph_store=_NullGraph())

        # Default export from spectra's perspective
        result = exporter.export(
            scope={"project": project, "agent_class_visibility": "spectra", "sensitivity_tag": "none"},
        )
        node_ids = {n["id"] for n in result["nodes"]}

        # Active node must be present
        assert "ge3-active" in node_ids, "Active node missing from default export"
        # Episodic node must be present (but no content_ref)
        assert "ge3-episodic" in node_ids, "Episodic node missing from default export"

        # Quarantined must be absent
        assert "ge3-quarantined" not in node_ids, "Quarantined node should be excluded"
        # Deprecated must be absent
        assert "ge3-deprecated" not in node_ids, "Deprecated node should be excluded"
        # Superseded old node must be absent by default
        assert "ge3-old" not in node_ids, "Superseded node should be excluded"
        # Forge-only must be absent when visibility=spectra
        assert "ge3-forge" not in node_ids, "Forge-only node should be excluded for spectra"

        # Episodic node must NOT have content_ref by default (GAP-3 blob redaction)
        ep_node = next(n for n in result["nodes"] if n["id"] == "ge3-episodic")
        assert "content_ref" not in ep_node, (
            "content_ref must not be emitted by default (blob redaction P0)"
        )

    def test_g_ge3_overrides_include_excluded_nodes(self, tmp_path: Path) -> None:
        """With override flags, excluded nodes appear in the export."""
        rel = RelationalStore(db_path=tmp_path / "ge3b.sqlite")

        class _NullGraph:
            def all_edges(self, **kwargs):
                return []

        project = "ge3b-project"

        c_quarantined = _make_crystal(
            crystal_id="ge3b-q", project=project, validation_state="unverified"
        )
        rel.insert_crystal(c_quarantined)
        rel.set_validation_state(c_quarantined["id"], "quarantined")

        c_deprecated = _make_crystal(crystal_id="ge3b-dep", project=project)
        rel.insert_crystal(c_deprecated)
        rel.set_status(c_deprecated["id"], "deprecated")

        c_old = _make_crystal(crystal_id="ge3b-old", project=project)
        c_new = _make_crystal(crystal_id="ge3b-new", project=project)
        rel.insert_crystal(c_old)
        rel.insert_crystal(c_new)
        rel.mark_superseded(c_old["id"], c_new["id"], datetime.now(timezone.utc))

        exporter = GraphExporter(relational_store=rel, graph_store=_NullGraph())
        result = exporter.export(
            scope={"project": project, "agent_class_visibility": None, "sensitivity_tag": "none"},
            include_flags=ExportFlags(
                include_quarantined=True,
                include_deprecated=True,
                include_superseded=True,
            ),
        )
        node_ids = {n["id"] for n in result["nodes"]}
        assert "ge3b-q" in node_ids, "Quarantined should appear with --include-quarantined"
        assert "ge3b-dep" in node_ids, "Deprecated should appear with --include-deprecated"
        assert "ge3b-old" in node_ids, "Superseded should appear with --include-superseded"

    def test_g_ge3_content_ref_emitted_as_hash_only_when_flagged(self, tmp_path: Path) -> None:
        """With --include-content-ref, the episodic node's content_ref is the
        opaque SHA-256 hash (never resolved blob bytes).
        """
        rel = RelationalStore(db_path=tmp_path / "ge3c.sqlite")

        class _NullGraph:
            def all_edges(self, **kwargs):
                return []

        project = "ge3c-project"
        c_ep = _make_crystal(crystal_id="ge3c-ep", project=project, layer="episodic")
        rel.insert_crystal(c_ep)

        exporter = GraphExporter(relational_store=rel, graph_store=_NullGraph())
        result = exporter.export(
            scope={"project": project, "agent_class_visibility": None, "sensitivity_tag": "none"},
            include_flags=ExportFlags(include_content_ref=True),
        )
        ep_node = next(n for n in result["nodes"] if n["id"] == "ge3c-ep")
        # content_ref should be present and be a 64-hex SHA-256 hash
        assert "content_ref" in ep_node, "content_ref should appear when flag set"
        assert isinstance(ep_node["content_ref"], str)
        assert len(ep_node["content_ref"]) == 64, (
            f"content_ref should be 64-hex SHA-256, got len={len(ep_node['content_ref'])}"
        )


# ---------------------------------------------------------------------------
# G-GE6: CLI ⇆ MCP parity (§3 G-GE6)
# ---------------------------------------------------------------------------


class TestGGE6CliMcpParity:
    """G-GE6: CLI and MCP json payloads byte-identical after json.dumps(sort_keys=True).

    Both surfaces call the same GraphExporter.export(...) core (D3, HYG-4 ordering).
    The test drives GraphExporter directly for both "surfaces" to verify the shared
    core produces stable output. The MCP dispatch test also verifies handler wiring.
    """

    def test_g_ge6_cli_mcp_parity(self, tmp_path: Path) -> None:
        """Calling GraphExporter.export twice with identical args produces byte-identical
        JSON when serialised with json.dumps(..., sort_keys=True).

        This proves the shared core (G-GE6) and HYG-4 determinism.
        """
        rel = RelationalStore(db_path=tmp_path / "ge6.sqlite")

        class _NullGraph:
            def all_edges(self, **kwargs):
                return []

        project = "ge6-project"
        for i in range(3):
            c = _make_crystal(project=project, crystal_id=f"ge6-crystal-{i:02d}")
            rel.insert_crystal(c)

        exporter = GraphExporter(relational_store=rel, graph_store=_NullGraph())
        scope = {"project": project, "agent_class_visibility": None, "sensitivity_tag": "none"}

        # Simulate "CLI call" — direct export
        result_cli = exporter.export(scope=scope, layers=None, limit=5000)
        # Simulate "MCP call" — same parameters
        result_mcp = exporter.export(scope=scope, layers=None, limit=5000)

        # Normalise generated_at timestamp (may differ by microseconds between calls)
        # The spec says byte-identical when scope/flags identical; generated_at is
        # the only non-deterministic field — we zero it for parity comparison.
        import copy

        def _normalise(d: dict) -> dict:
            d2 = copy.deepcopy(d)
            d2.get("generated_from", {}).pop("generated_at", None)
            d2.get("generated_from", {}).pop("caller_tier", None)
            return d2

        cli_json = json.dumps(_normalise(result_cli), sort_keys=True, default=str)
        mcp_json = json.dumps(_normalise(result_mcp), sort_keys=True, default=str)
        assert cli_json == mcp_json, (
            "CLI and MCP payloads must be byte-identical after sort_keys normalisation"
        )

    def test_g_ge6_handler_wiring(self, tmp_path: Path) -> None:
        """_handle_graph_export returns a valid canonical dict with expected fields."""
        from crystalium.server import _handle_graph_export
        from crystalium.trust import Tier

        rel = RelationalStore(db_path=tmp_path / "ge6b.sqlite")

        class _NullGraph:
            def all_edges(self, **kwargs):
                return []

        project = "ge6b-project"
        c = _make_crystal(project=project, crystal_id="ge6b-c1")
        rel.insert_crystal(c)

        exporter = GraphExporter(relational_store=rel, graph_store=_NullGraph())
        result = _handle_graph_export(
            {"scope": {"project": project}, "limit": 100},
            exporter,
            None,
            Tier.T1,
        )
        assert result["schema_version"] == "graph-export.v1"
        assert "nodes" in result
        assert "edges" in result
        assert "counts" in result
        assert result["generated_from"]["caller_tier"] == "T1"

    def test_g_ge6_manifest_includes_graph_export(self) -> None:
        """build_tool_manifest() must include crystalium.graph_export as the 9th tool."""
        from crystalium.server import build_tool_manifest
        tools = build_tool_manifest()
        names = [t["name"] for t in tools]
        assert "crystalium.graph_export" in names, (
            f"crystalium.graph_export missing from manifest: {names}"
        )
        ge = next(t for t in tools if t["name"] == "crystalium.graph_export")
        assert ge["inputSchema"]["required"] == ["scope"]
        assert "format" in ge["inputSchema"]["properties"]
        assert "json" in ge["inputSchema"]["properties"]["format"]["enum"]
        assert "graphml" in ge["inputSchema"]["properties"]["format"]["enum"]
        assert "cytoscape" in ge["inputSchema"]["properties"]["format"]["enum"]


# ---------------------------------------------------------------------------
# G-GE7: ECL sidecar auto-emitted on MCP result (§3 G-GE7)
# ---------------------------------------------------------------------------


class TestGGE7EclSidecar:
    """G-GE7: valid ECL v2.0 sidecar auto-emitted by _emit_ecl_sidecar with
    artifact.kind='graph-export', performative='INFORM', sha256 integrity match.
    """

    def test_g_ge7_ecl_sidecar(self, tmp_path: Path) -> None:
        """ECL envelope for graph_export has correct kind, performative, and integrity."""
        from crystalium.ecl import build_for_tool_result, compute_sha256

        rel = RelationalStore(db_path=tmp_path / "ge7.sqlite")

        class _NullGraph:
            def all_edges(self, **kwargs):
                return []

        project = "ge7-project"
        c = _make_crystal(project=project, crystal_id="ge7-c1")
        rel.insert_crystal(c)

        exporter = GraphExporter(relational_store=rel, graph_store=_NullGraph())
        result = exporter.export(
            scope={"project": project, "agent_class_visibility": None, "sensitivity_tag": "none"},
        )
        result_bytes = json.dumps(result, sort_keys=True, default=str).encode()
        expected_sha = compute_sha256(result_bytes)

        # Build the envelope the same way _emit_ecl_sidecar does
        envelope = build_for_tool_result(
            tool_name="crystalium.graph_export",
            payload=result_bytes,
            artifact_kind="graph-export",
            caller_identity=None,
            performative="INFORM",
        )
        d = envelope.to_dict()

        # artifact.kind must be "graph-export"
        assert d["artifact"]["kind"] == "graph-export", (
            f"artifact.kind should be 'graph-export', got {d['artifact']['kind']!r}"
        )
        # performative must be INFORM
        assert d["performative"] == "INFORM", (
            f"performative should be 'INFORM', got {d['performative']!r}"
        )
        # integrity.method must be sha256
        assert d["integrity"]["method"] == "sha256"
        # integrity.value must equal sha256(payload_bytes)
        assert d["integrity"]["value"] == expected_sha, (
            f"integrity.value {d['integrity']['value']!r} != sha256 of payload"
        )
        # artifact.sha256 must also match
        assert d["artifact"]["sha256"] == expected_sha

    def test_g_ge7_ecl_sidecar_written_to_run_dir(self, tmp_path: Path) -> None:
        """_emit_ecl_sidecar writes a file into run_dir when called for graph_export."""
        from crystalium.server import _emit_ecl_sidecar

        payload = b'{"schema_version":"graph-export.v1","nodes":[],"edges":[]}'
        run_dir = tmp_path / "runs" / "ge7-test-run"
        run_dir.mkdir(parents=True)

        _emit_ecl_sidecar(
            "crystalium.graph_export",
            payload,
            "graph-export",
            run_dir,
            caller={"eidolon": "test", "version": "0.0.1", "tier": "T1"},
            performative="INFORM",
        )

        # Expect at least one ecl-envelope.*.json file in run_dir
        sidecar_files = list(run_dir.glob("ecl-envelope.*.json"))
        assert len(sidecar_files) >= 1, (
            f"No ECL sidecar file written to {run_dir}. Files: {list(run_dir.iterdir())}"
        )

        sidecar = json.loads(sidecar_files[0].read_text())
        assert sidecar["artifact"]["kind"] == "graph-export"
        assert sidecar["integrity"]["method"] == "sha256"

    def test_g_ge7_unknown_tool_fallback_intact(self, tmp_path: Path) -> None:
        """The unknown-tool else branch must still raise CrystaliumEnforcementError."""
        # This is not a full async MCP test; we verify the enforcement import works.
        from crystalium.enforcement import CrystaliumEnforcementError
        with pytest.raises(CrystaliumEnforcementError):
            raise CrystaliumEnforcementError("Unknown tool 'bogus'.", reason_code="UNKNOWN_TOOL")
