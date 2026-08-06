"""crystalium-residual-eight-plan (W-RIG) -- tests for the shared corpus rig.

`evals/_corpus_rig.py` is the mechanism the four Wave-1 gates (G-XL, G-CORPUS,
G-WD, G-FLOOR) all build on -- see that module's docstring and spec.md Sec 0.1
/ Sec 0.2. AC-305's three normative nodes live here:

  - test_confounded_axis_returns_no_numbers
  - test_liveness_measured_on_populated_edgeless_graph
  - test_liveness_confounded_on_empty_graph

`test_liveness_measured_on_populated_edgeless_graph` is the discriminating
node (K-C-N12 / K-B16): a rig written `all_edges() == 0` is `[] == 0` --
ALWAYS `False` in Python -- and can never conclude "edgeless" on a genuinely
edgeless, populated graph. This file exercises the rig against a REAL
`GraphStore` (genuine KuzuDB) and a REAL `RelationalStore` (genuine
SQLite/FTS5) throughout -- only the dense arm is ever a stub (spec.md
Sec 0.3), matching every downstream gate's own template choice.

Container-first:
  docker compose run --rm crystalium pytest mcp-server/tests/test_corpus_rig.py -v
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from evals._corpus_rig import (
    Stores,
    arm_liveness,
    assemble_result,
    build_aetheryte,
    crystal,
    emit,
    graph_liveness,
    new_stores,
    resolve_verdict,
    seed_graph,
    seed_relational,
    stamp_sequence,
    stub_vector_store,
)

_PROJECT = "corpus-rig-test"
_AGENT_CLASS = "backend"


# ---------------------------------------------------------------------------
# 1. Crystal minting
# ---------------------------------------------------------------------------


class TestCrystalMinting:
    def test_stamp_sequence_strictly_increasing(self) -> None:
        seq = stamp_sequence()
        values = [next(seq) for _ in range(5)]
        assert values == sorted(values)
        assert len(set(values)) == 5

    def test_stamp_sequence_independent_instances(self) -> None:
        """Two gates calling `stamp_sequence()` each get their OWN generator
        -- shared shape, never a shared mutable sequence object (Sec 0.1 vs
        Sec 0.2 discipline)."""
        a, b = stamp_sequence(), stamp_sequence()
        assert next(a) == next(b)
        next(a)
        assert next(a) != next(b)

    def test_crystal_shape_and_determinism(self) -> None:
        stamp = next(stamp_sequence())
        c1 = crystal(
            "id-1", "episodic", "some summary text",
            project=_PROJECT, agent_class=_AGENT_CLASS, created_at=stamp,
        )
        c2 = crystal(
            "id-1", "episodic", "some summary text",
            project=_PROJECT, agent_class=_AGENT_CLASS, created_at=stamp,
        )
        assert c1 == c2  # byte-identical given identical inputs -- no clock read
        assert c1["id"] == "id-1"
        assert c1["layer"] == "episodic"
        assert c1["summary"] == "some summary text"
        assert c1["provenance"]["created_at"] == stamp
        assert c1["scope"]["project"] == _PROJECT
        assert c1["scope"]["agent_class_visibility"] == _AGENT_CLASS
        assert c1["status"] == "active"
        assert c1["trust_tier"] == "T1"


# ---------------------------------------------------------------------------
# 2. Store seeding
# ---------------------------------------------------------------------------


class TestStoreSeeding:
    def test_new_stores_returns_wired_bundle(self, tmp_path: Path) -> None:
        stores = new_stores(str(tmp_path), "seed-demo")
        assert isinstance(stores, Stores)
        assert stores.cfg is not None
        assert stores.relational is not None
        assert stores.graph is not None

    def test_new_stores_distinct_tags_are_isolated(self, tmp_path: Path) -> None:
        a = new_stores(str(tmp_path), "tag-a")
        b = new_stores(str(tmp_path), "tag-b")
        stamps = stamp_sequence()
        seed_relational(
            a.relational,
            [crystal("only-in-a", "episodic", "quorvex blenthar filler",
                     project=_PROJECT, agent_class=_AGENT_CLASS, created_at=next(stamps))],
        )
        assert a.relational.get_crystal("only-in-a") is not None
        assert b.relational.get_crystal("only-in-a") is None

    def test_seed_relational_inserts_all(self, tmp_path: Path) -> None:
        stores = new_stores(str(tmp_path), "relational-demo")
        stamps = stamp_sequence()
        crystals = [
            crystal(f"c{i}", "episodic", "quorvex blenthar mizzletine korvath filler",
                    project=_PROJECT, agent_class=_AGENT_CLASS, created_at=next(stamps))
            for i in range(3)
        ]
        seed_relational(stores.relational, crystals)
        for c in crystals:
            assert stores.relational.get_crystal(c["id"]) is not None
        hits = stores.relational.bm25_search("quorvex", layer_filter="episodic", k=10)
        assert {h["id"] for h in hits} == {"c0", "c1", "c2"}

    def test_seed_graph_nodes_only_is_edgeless(self, tmp_path: Path) -> None:
        stores = new_stores(str(tmp_path), "graph-nodes-only")
        seed_graph(stores.graph, nodes=[("n1", "episodic"), ("n2", "episodic")])
        assert stores.graph.node_count() == 2
        assert stores.graph.all_edges() == []

    def test_seed_graph_nodes_and_edges(self, tmp_path: Path) -> None:
        stores = new_stores(str(tmp_path), "graph-with-edges")
        seed_graph(
            stores.graph,
            nodes=[("a", "episodic"), ("b", "episodic")],
            edges=[("a", "b", "LINKS_TO")],
        )
        assert stores.graph.node_count() == 2
        assert len(stores.graph.all_edges()) == 1


# ---------------------------------------------------------------------------
# 3. Stub dense arm
# ---------------------------------------------------------------------------


class TestStubDenseArm:
    def test_dense_search_returns_stated_ranking(self) -> None:
        vs = stub_vector_store(["x", "y", "z"])
        assert vs.dense_search.return_value == [{"id": "x"}, {"id": "y"}, {"id": "z"}]

    def test_embed_stays_truthy(self) -> None:
        vs = stub_vector_store([])
        assert vs.embed("anything") == [0.1, 0.2, 0.3]

    def test_empty_dense_hits_yields_empty_ranking(self) -> None:
        vs = stub_vector_store([])
        assert vs.dense_search.return_value == []


# ---------------------------------------------------------------------------
# 4. Aetheryte construction harness -- all flags explicit
# ---------------------------------------------------------------------------


class TestAetheryteHarness:
    def test_build_aetheryte_requires_every_flag(self, tmp_path: Path) -> None:
        """No flag this harness cares about may fall back to a default --
        omitting ANY of them is a TypeError, not a silently-inherited value."""
        stores = new_stores(str(tmp_path), "harness-typeerror")
        vs = stub_vector_store([])
        with pytest.raises(TypeError):
            build_aetheryte(  # type: ignore[call-arg]
                cfg=stores.cfg,
                relational=stores.relational,
                vector_store=vs,
                graph_store=stores.graph,
                completion=False,
                completion_max_hops=1,
                completion_decay=0.5,
                recall_active_only=False,
                recall_relevance_primary=True,
                recall_weighted_fusion=True,
                fusion_weight_dense=1.0,
                # fusion_weight_derived deliberately omitted
                fusion_sparse_boost_alpha=1.0,
            )

    def test_build_aetheryte_end_to_end_recall(self, tmp_path: Path) -> None:
        from crystalium.schemas import Scope
        from crystalium.trust import Tier

        stores = new_stores(str(tmp_path), "harness-e2e")
        stamps = stamp_sequence()
        seed_relational(
            stores.relational,
            [crystal("target", "episodic", "quorvex blenthar mizzletine korvath",
                     project=_PROJECT, agent_class=_AGENT_CLASS, created_at=next(stamps))],
        )
        vs = stub_vector_store([])
        aetheryte = build_aetheryte(
            cfg=stores.cfg,
            relational=stores.relational,
            vector_store=vs,
            graph_store=stores.graph,
            completion=False,
            completion_max_hops=1,
            completion_decay=0.5,
            recall_active_only=False,
            recall_relevance_primary=True,
            recall_weighted_fusion=True,
            fusion_weight_dense=1.0,
            fusion_weight_derived=1.0,
            fusion_sparse_boost_alpha=1.0,
        )
        result = aetheryte.recall(
            Scope(project=_PROJECT, agent_class_visibility=_AGENT_CLASS),
            "quorvex blenthar mizzletine korvath", 5, None, Tier.T1,
        )
        retrieved = [r.id for r in result.records]
        assert "target" in retrieved

    def test_build_aetheryte_flags_reach_the_instance(self, tmp_path: Path) -> None:
        """Read back OFF THE INSTANCE, not off the kwargs dict just written
        (VP-M4's tautology warning) -- this is the pin the four gates'
        `weight_readback`-style assertions depend on this rig providing
        correctly."""
        stores = new_stores(str(tmp_path), "harness-readback")
        vs = stub_vector_store([])
        aetheryte = build_aetheryte(
            cfg=stores.cfg,
            relational=stores.relational,
            vector_store=vs,
            graph_store=stores.graph,
            completion=False,
            completion_max_hops=1,
            completion_decay=0.5,
            recall_active_only=False,
            recall_relevance_primary=True,
            recall_weighted_fusion=True,
            fusion_weight_dense=1.0,
            fusion_weight_derived=0.93,
            fusion_sparse_boost_alpha=1.0,
        )
        assert aetheryte.fusion_weight_derived == 0.93
        assert aetheryte.recall_active_only is False
        assert aetheryte.completion is False


# ---------------------------------------------------------------------------
# 5. Arm-liveness self-checks (AC-305's three normative nodes live here)
# ---------------------------------------------------------------------------


class TestLiveness:
    def test_liveness_measured_on_populated_edgeless_graph(self, tmp_path: Path) -> None:
        """AC-305 (K-C-N12), the discriminating node. `all_edges() == 0` is
        `[] == 0` -- ALWAYS `False` in Python -- so a rig written that way
        can never conclude "edgeless" on a real, populated, genuinely
        edgeless graph. This node builds exactly that graph and requires
        the rig to say so."""
        stores = new_stores(str(tmp_path), "edgeless-populated")
        seed_graph(stores.graph, nodes=[(f"n{i}", "episodic") for i in range(4)])

        result = graph_liveness(stores.graph, expected_node_count=4, expected_edge_count=0)

        assert result["verdict"] == "measured"
        assert result["liveness"]["edge_count"] == 0
        assert result["liveness"]["node_count"] == 4

    def test_liveness_confounded_on_empty_graph(self, tmp_path: Path) -> None:
        """The other direction: `node_count == 0` (nothing seeded, or kuzu
        genuinely dead) must NEVER read as "measured" merely because
        `edge_count` is also 0 -- that is precisely the ambiguity
        `all_edges()`'s own docstring warns about ("Returns [] on any kuzu
        error", graph.py:332)."""
        stores = new_stores(str(tmp_path), "empty-graph")

        result = graph_liveness(stores.graph, expected_node_count=4, expected_edge_count=0)

        assert result["verdict"] == "confounded"
        assert result["liveness"]["node_count"] == 0
        assert result["liveness"]["edge_count"] == 0

        # AC-305's "no numeric axes" clause: even if a gate tried to attach
        # numbers on this branch, assemble_result strips them.
        assembled = assemble_result(
            verdict=result["verdict"], liveness=result["liveness"],
            axes={"target_rank": 0},
        )
        assert assembled["axes"] is None

    def test_liveness_confounded_on_wrong_node_count(self, tmp_path: Path) -> None:
        """A graph that IS live but was seeded to a DIFFERENT size than the
        gate expected is also a confound -- not a silent pass at the wrong
        N."""
        stores = new_stores(str(tmp_path), "wrong-node-count")
        seed_graph(stores.graph, nodes=[("only-one", "episodic")])

        result = graph_liveness(stores.graph, expected_node_count=4, expected_edge_count=0)

        assert result["verdict"] == "confounded"
        assert result["liveness"]["node_count"] == 1

    def test_liveness_confounded_when_edges_present_but_unexpected(
        self, tmp_path: Path
    ) -> None:
        """The general (non-edgeless) form: a gate that pins
        `expected_edge_count=0` and gets a real edge back is confounded --
        this is the deliberately-mis-pinned-axis case AC-305 is about."""
        stores = new_stores(str(tmp_path), "unexpected-edges")
        seed_graph(
            stores.graph,
            nodes=[("a", "episodic"), ("b", "episodic")],
            edges=[("a", "b", "LINKS_TO")],
        )

        result = graph_liveness(stores.graph, expected_node_count=2, expected_edge_count=0)

        assert result["verdict"] == "confounded"
        assert result["liveness"]["edge_count"] == 1

    def test_liveness_measured_with_expected_edges(self, tmp_path: Path) -> None:
        """The general form also reports "measured" for a NON-edgeless
        fixture whose edge count matches what was seeded -- `graph_liveness`
        is not restricted to the edgeless case."""
        stores = new_stores(str(tmp_path), "expected-edges")
        seed_graph(
            stores.graph,
            nodes=[("a", "episodic"), ("b", "episodic"), ("c", "episodic")],
            edges=[("a", "b", "LINKS_TO"), ("b", "c", "LINKS_TO")],
        )

        result = graph_liveness(stores.graph, expected_node_count=3, expected_edge_count=2)

        assert result["verdict"] == "measured"
        assert result["liveness"]["edge_count"] == 2
        assert result["liveness"]["node_count"] == 3

    def test_arm_liveness_measured(self) -> None:
        result = arm_liveness(
            dense_ranking=["a", "b", "c"], expected_dense=3,
            sparse_ranking=["x", "y"], expected_sparse=2,
        )
        assert result["verdict"] == "measured"

    def test_arm_liveness_confounded_on_truncated_dense(self) -> None:
        result = arm_liveness(
            dense_ranking=["a", "b"], expected_dense=3,
            sparse_ranking=["x", "y"], expected_sparse=2,
        )
        assert result["verdict"] == "confounded"
        assert result["liveness"]["dense_count"] == 2

    def test_arm_liveness_confounded_on_truncated_sparse(self) -> None:
        result = arm_liveness(
            dense_ranking=["a", "b", "c"], expected_dense=3,
            sparse_ranking=["x"], expected_sparse=2,
        )
        assert result["verdict"] == "confounded"
        assert result["liveness"]["sparse_count"] == 1


# ---------------------------------------------------------------------------
# 6. The pure verdict classifier (AC-305's third normative angle)
# ---------------------------------------------------------------------------


class TestVerdictClassifier:
    def test_resolve_verdict_all_true_is_measured(self) -> None:
        assert resolve_verdict(pinned={"a": True, "b": True}) == "measured"

    def test_resolve_verdict_any_false_is_confounded(self) -> None:
        assert resolve_verdict(pinned={"a": True, "b": False}) == "confounded"

    def test_resolve_verdict_single_false_is_confounded(self) -> None:
        assert resolve_verdict(pinned={"only_axis": False}) == "confounded"

    def test_resolve_verdict_rejects_empty(self) -> None:
        with pytest.raises(ValueError):
            resolve_verdict(pinned={})

    def test_resolve_verdict_is_pure(self) -> None:
        """No I/O, no shared state -- calling it twice with the same inputs
        gives the same answer, and it never touches a store or the
        filesystem (verified by simply not passing one)."""
        pinned = {"x": True, "y": True, "z": False}
        assert resolve_verdict(pinned=pinned) == resolve_verdict(pinned=dict(pinned))

    def test_confounded_axis_returns_no_numbers(self, tmp_path: Path) -> None:
        """AC-305: a gate fixture with ONE pinned axis deliberately set to a
        binding value must return verdict "confounded" and emit NO numeric
        axes (R-CONF; retrieval_gate.py:301-310 precedent).

        Demonstrated end-to-end against REAL stores: the fixture actually
        carries one edge, but the gate's OWN pinned axis asserts "the graph
        is edgeless" (`expected_edge_count=0`) -- deliberately mis-pinned,
        since this fixture is not edgeless at all. The axis is therefore
        BINDING (the precondition it names is false), `graph_liveness` must
        report "confounded", `resolve_verdict` must fold that into an
        overall "confounded" verdict, and `assemble_result` must strip any
        numeric axis a (hypothetical, buggy) gate tries to attach anyway.
        """
        stores = new_stores(str(tmp_path), "confounded-red-check")
        seed_graph(
            stores.graph,
            nodes=[("a", "episodic"), ("b", "episodic")],
            edges=[("a", "b", "LINKS_TO")],
        )

        graph_live = graph_liveness(stores.graph, expected_node_count=2, expected_edge_count=0)
        assert graph_live["verdict"] == "confounded"  # the pinned axis IS binding here

        verdict = resolve_verdict(pinned={"graph_edgeless": graph_live["verdict"] == "measured"})
        assert verdict == "confounded"

        result = assemble_result(
            verdict=verdict,
            liveness=graph_live["liveness"],
            axes={"target_rank": 0, "corpus_per_layer": 3, "candidate_k": 15},
        )
        assert result["verdict"] == "confounded"
        assert result["axes"] is None
        # belt-and-braces: no numeric axis leaks anywhere in the serialised form
        dumped = json.dumps(result)
        assert "target_rank" not in dumped
        assert "corpus_per_layer" not in dumped
        assert "candidate_k" not in dumped

    def test_measured_verdict_carries_axes_through(self) -> None:
        """The positive control for `assemble_result`: a "measured" verdict
        is NOT stripped -- axes pass through unchanged."""
        result = assemble_result(
            verdict="measured", liveness={"node_count": 4, "edge_count": 0},
            axes={"target_rank": 3},
        )
        assert result["axes"] == {"target_rank": 3}

    def test_assemble_result_rejects_unknown_verdict(self) -> None:
        with pytest.raises(ValueError):
            assemble_result(verdict="sort-of")


# ---------------------------------------------------------------------------
# 7. Artifact emission -- rule (g)
# ---------------------------------------------------------------------------


class TestEmit:
    def test_emit_stamps_nonce_and_tree_sha(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CRYSTALIUM_GATE_NONCE", "nonce-abc-123")
        monkeypatch.setenv("CRYSTALIUM_TREE_SHA", "deadbeef")
        out = tmp_path / "results" / "demo.json"
        emit({"verdict": "measured", "axes": {"x": 1}}, str(out))

        written = json.loads(out.read_text())
        assert written["run_nonce"] == "nonce-abc-123"
        assert written["tree_sha"] == "deadbeef"
        assert written["verdict"] == "measured"
        assert written["axes"] == {"x": 1}

    def test_emit_raises_keyerror_without_nonce(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("CRYSTALIUM_GATE_NONCE", raising=False)
        monkeypatch.setenv("CRYSTALIUM_TREE_SHA", "deadbeef")
        out = tmp_path / "demo.json"
        with pytest.raises(KeyError):
            emit({"verdict": "measured"}, str(out))
        assert not out.exists()

    def test_emit_raises_keyerror_without_tree_sha(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CRYSTALIUM_GATE_NONCE", "nonce-abc-123")
        monkeypatch.delenv("CRYSTALIUM_TREE_SHA", raising=False)
        out = tmp_path / "demo.json"
        with pytest.raises(KeyError):
            emit({"verdict": "measured"}, str(out))

    def test_emit_overwrites_stale_artifact(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CRYSTALIUM_GATE_NONCE", "run-2")
        monkeypatch.setenv("CRYSTALIUM_TREE_SHA", "sha-2")
        out = tmp_path / "demo.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"run_nonce": "run-1", "tree_sha": "sha-1", "stale": True}))

        emit({"verdict": "measured"}, str(out))

        written = json.loads(out.read_text())
        assert written["run_nonce"] == "run-2"
        assert written["tree_sha"] == "sha-2"
        assert "stale" not in written

    def test_emit_does_not_mutate_caller_dict(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CRYSTALIUM_GATE_NONCE", "nonce-x")
        monkeypatch.setenv("CRYSTALIUM_TREE_SHA", "sha-x")
        result = {"verdict": "measured"}
        emit(result, str(tmp_path / "demo.json"))
        assert "run_nonce" not in result
        assert "tree_sha" not in result

    def test_emit_creates_parent_directories(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CRYSTALIUM_GATE_NONCE", "n")
        monkeypatch.setenv("CRYSTALIUM_TREE_SHA", "t")
        out = tmp_path / "nested" / "dir" / "demo.json"
        assert not out.parent.exists()
        emit({"verdict": "measured"}, str(out))
        assert out.exists()
