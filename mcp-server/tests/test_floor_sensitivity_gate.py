"""crystalium-residual-eight-plan (W-G-FLOOR / crystalium#48) -- tests for
`evals/floor_sensitivity_gate.py`.

Two measurements, kept structurally distinct (see that module's docstring):

  VP-M1 (`vp_m1_probe` / `vp_m1_pair` / `shipped_fixture_topology`) --
  FORGE D5's derived-membership probe. `TestVpM1DistinctPhantomControl`
  pins AC-357's positive control; `TestVpM1ShippedFixture` and
  `TestShippedFixtureTopology` pin AC-321/AC-374's finding that the
  SHIPPED `evals/fusion_gate.py` fixture's derived-membership channel is
  structurally absent post-#41.

  VP-M6 (`run_seed` / `aggregate_seeds` / `classify_disjoint`) -- AC-322's
  own measurement on this module's OWN tie-break-neutral, distinct-phantom
  fixture. `test_disjointness_classifier_both_branches` and
  `test_aggregate_uses_classifier` are AC-358's two normative node names.

Container-first:
  docker compose run --rm crystalium pytest mcp-server/tests/test_floor_sensitivity_gate.py -v
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from evals.floor_sensitivity_gate import (
    _GraphStoreSpy,
    aggregate_seeds,
    classify_disjoint,
    run,
    run_seed,
    shipped_fixture_topology,
    vp_m1_pair,
    vp_m1_probe,
)


# ---------------------------------------------------------------------------
# 1. The recording proxy -- pure delegation, no real store needed (fakes
#    stand in for GraphStore so this class stays fast).
# ---------------------------------------------------------------------------


class _FakeGraph:
    def __init__(self) -> None:
        self.neighbor_expand_calls: list[tuple[list[str], int, str | None]] = []
        self.decaying_walk_calls: list[tuple[list[str], int, float]] = []

    def neighbor_expand(self, seed_ids, depth=1, rel_filter=None):
        self.neighbor_expand_calls.append((list(seed_ids), depth, rel_filter))
        return {"b", "a"}

    def decaying_walk(self, seed_ids, max_hops=2, decay=0.5):
        self.decaying_walk_calls.append((list(seed_ids), max_hops, decay))
        return {"c": 0.5, "a": 0.5}

    def all_edges(self):
        return [("a", "b", "LINKS_TO")]

    def node_count(self) -> int:
        return 42


class TestGraphStoreSpy:
    def test_delegates_unknown_methods(self) -> None:
        fake = _FakeGraph()
        spy = _GraphStoreSpy(fake)
        assert spy.all_edges() == [("a", "b", "LINKS_TO")]
        assert spy.node_count() == 42

    def test_records_and_forwards_neighbor_expand(self) -> None:
        fake = _FakeGraph()
        spy = _GraphStoreSpy(fake)
        result = spy.neighbor_expand(["seed1", "seed2"], depth=1, rel_filter=None)
        assert result == {"a", "b"}  # forwarded unchanged
        assert fake.neighbor_expand_calls == [(["seed1", "seed2"], 1, None)]  # args forwarded unchanged
        assert spy.neighbor_expand_results == [{"a", "b"}]  # recorded

    def test_records_and_forwards_decaying_walk(self) -> None:
        fake = _FakeGraph()
        spy = _GraphStoreSpy(fake)
        result = spy.decaying_walk(["seed1"], max_hops=1, decay=0.5)
        assert result == {"c": 0.5, "a": 0.5}
        assert fake.decaying_walk_calls == [(["seed1"], 1, 0.5)]
        assert spy.decaying_walk_results == [{"c": 0.5, "a": 0.5}]

    def test_derived_union_combines_both_arms_and_dedupes(self) -> None:
        fake = _FakeGraph()
        spy = _GraphStoreSpy(fake)
        spy.neighbor_expand(["s"])
        spy.decaying_walk(["s"])
        # neighbor_expand returned {a, b}; decaying_walk returned keys {c, a}
        assert spy.derived_union == ["a", "b", "c"]

    def test_derived_union_empty_when_no_calls(self) -> None:
        spy = _GraphStoreSpy(_FakeGraph())
        assert spy.derived_union == []


# ---------------------------------------------------------------------------
# 2. classify_disjoint -- AC-358's pure classifier. `test_disjointness_
#    classifier_both_branches` is AC-358's normative node name.
# ---------------------------------------------------------------------------


class TestDisjointnessClassifier:
    def test_disjointness_classifier_both_branches(self) -> None:
        # disjoint branch
        assert classify_disjoint([0, 0], [2, 2]) is True
        # overlapping branch (1 shared between the two sets)
        assert classify_disjoint([0, 1], [1, 2]) is False
        # K-B3 vacuity guard: empty vs empty must NOT read as "disjoint"
        assert classify_disjoint([], []) is False

    def test_classify_disjoint_rejects_empty_low_even_if_high_nonempty(self) -> None:
        assert classify_disjoint([], [1, 2]) is False

    def test_classify_disjoint_rejects_empty_high_even_if_low_nonempty(self) -> None:
        assert classify_disjoint([1, 2], []) is False

    def test_classify_disjoint_is_pure(self) -> None:
        low, high = [0, 1], [2, 3]
        result_a = classify_disjoint(low, high)
        result_b = classify_disjoint(low, high)
        assert result_a == result_b is True
        assert low == [0, 1] and high == [2, 3]  # inputs untouched


# ---------------------------------------------------------------------------
# 3. aggregate_seeds -- AC-322's producing step. `test_aggregate_uses_
#    classifier` is AC-358's second normative node name (K-C5 binding).
# ---------------------------------------------------------------------------


class TestAggregateSeeds:
    def _write_seed_rows(self, tmp_path_marker: str, rows: list[dict]) -> list[str]:
        """Writes synthetic per-seed rows to the REAL `evals/results/`
        directory `aggregate_seeds` reads from (rule (g)'s canonical ART
        location; the function hardcodes this path, matching every other
        gate in this campaign) -- under uniquely-tagged labels so a
        concurrent real gate run can never collide with, or be polluted
        by, this test's fixtures."""
        results_dir = Path("evals/results")
        results_dir.mkdir(parents=True, exist_ok=True)
        labels = []
        for i, row in enumerate(rows):
            label = f"test-{tmp_path_marker}-{i}"
            (results_dir / f"floor-seed-{label}.json").write_text(json.dumps(row))
            labels.append(label)
        return labels

    def _cleanup(self, labels: list[str]) -> None:
        for label in labels:
            path = Path("evals/results") / f"floor-seed-{label}.json"
            if path.exists():
                path.unlink()

    def test_aggregate_uses_classifier(self) -> None:
        marker = uuid.uuid4().hex[:8]
        rows = [
            {"seed": "0", "floor10_target_rank": 0, "floor1000_target_rank": 1},
            {"seed": "1", "floor10_target_rank": 0, "floor1000_target_rank": 1},
        ]
        labels = self._write_seed_rows(marker, rows)
        try:
            agg = aggregate_seeds(labels)
        finally:
            self._cleanup(labels)

        low_ranks = [r["floor10_target_rank"] for r in rows]
        high_ranks = [r["floor1000_target_rank"] for r in rows]
        expected = classify_disjoint(low_ranks, high_ranks)

        assert agg["classifier"]["symbol"] == "classify_disjoint"
        assert agg["classifier"]["disjoint"] == expected
        assert expected is True  # sanity: this fixture IS disjoint by construction

    def test_aggregate_uses_classifier_on_non_disjoint_rows(self) -> None:
        """K-C5's binding must hold in BOTH directions -- an aggregate that
        only ever emits `True` would pass the first test vacuously."""
        marker = uuid.uuid4().hex[:8]
        rows = [
            {"seed": "0", "floor10_target_rank": 0, "floor1000_target_rank": 0},
            {"seed": "1", "floor10_target_rank": 1, "floor1000_target_rank": 0},
        ]
        labels = self._write_seed_rows(marker, rows)
        try:
            agg = aggregate_seeds(labels)
        finally:
            self._cleanup(labels)

        low_ranks = [r["floor10_target_rank"] for r in rows]
        high_ranks = [r["floor1000_target_rank"] for r in rows]
        expected = classify_disjoint(low_ranks, high_ranks)

        assert agg["classifier"]["disjoint"] == expected
        assert expected is False  # sanity: overlapping (rank 0 shared)

    def test_aggregate_seed_set_covers_divergent_point(self) -> None:
        """`seed_set_covers_divergent_point` reads the `"seed"` FIELD INSIDE
        each row's content -- never the artifact's filename/label -- so a
        row whose content omits "8" reads `False` even when the file it was
        read from happens to be seed-labelled "8", and vice versa."""
        marker = uuid.uuid4().hex[:8]
        results_dir = Path("evals/results")
        results_dir.mkdir(parents=True, exist_ok=True)

        without_8_label = f"test-{marker}-a"
        (results_dir / f"floor-seed-{without_8_label}.json").write_text(
            json.dumps({"seed": without_8_label, "floor10_target_rank": 0, "floor1000_target_rank": 1})
        )
        with_8_label = f"test-{marker}-b"
        (results_dir / f"floor-seed-{with_8_label}.json").write_text(
            json.dumps({"seed": "8", "floor10_target_rank": 0, "floor1000_target_rank": 1})
        )
        try:
            agg_without = aggregate_seeds([without_8_label])
            assert agg_without["seed_set_covers_divergent_point"] is False
            assert agg_without["divergent_point_known"] == "8"

            agg_with = aggregate_seeds([without_8_label, with_8_label])
            assert agg_with["seed_set_covers_divergent_point"] is True
            assert agg_with["seed_set"] == [without_8_label, "8"]
        finally:
            (results_dir / f"floor-seed-{without_8_label}.json").unlink()
            (results_dir / f"floor-seed-{with_8_label}.json").unlink()


# ---------------------------------------------------------------------------
# 4. VP-M1 -- real RelationalStore + real GraphStore (container-only; the
#    dense arm stays a stub, spec.md Sec 0.3 -- none of these are
#    @pytest.mark.slow and all run under `make test-ci`).
# ---------------------------------------------------------------------------


class TestVpM1DistinctPhantomControl:
    """AC-357: the positive control, on THIS module's own distinct-phantom
    fixture, floor=2 vs floor=1000."""

    def test_positive_control_matches_ac357(self, tmp_path) -> None:
        pair = vp_m1_pair(
            seed_label="0", floor_low=2, floor_high=1000,
            fixture="distinct_phantom", data_root=str(tmp_path / "ctl"),
        )
        assert pair["fixture"] == "distinct_phantom"
        assert pair["probe_symbol"] == "vp_m1_probe"
        assert pair["probe_calls"] == 2
        assert len(pair["fixture_phantom_targets"]) >= 3
        assert len(set(pair["fixture_phantom_targets"])) == len(pair["fixture_phantom_targets"])
        assert pair["low"]["derived"] != []
        assert len(pair["high"]["derived"]) > len(pair["low"]["derived"])
        assert set(pair["low"]["derived"]) <= set(pair["high"]["derived"])
        assert pair["low"]["derived"] != pair["high"]["derived"]
        assert pair["channel_live"] is True
        assert pair["low"]["self_check_ok"] is True
        assert pair["high"]["self_check_ok"] is True


class TestVpM1ShippedFixture:
    """AC-321's building block: the SHIPPED `evals/fusion_gate.py` fixture,
    imported read-only, self-checked against `run_floor_probe`."""

    def test_shipped_probe_self_check_passes(self, tmp_path) -> None:
        probe = vp_m1_probe(floor=10, fixture="shipped", data_root=str(tmp_path / "shipped"))
        assert probe["self_check_ok"] is True
        assert probe["fixture"] == "shipped"
        assert probe["floor_applied_readback"] == 10

    def test_shipped_fixture_channel_dead_floor10_vs_floor1000(self, tmp_path) -> None:
        """Pins the mechanism-note finding (`CHANGE/issue-48-mechanism-note.md`):
        on the SHIPPED fixture, the derived union is IDENTICAL at floor=10
        and floor=1000 -- this is the measurement AC-138/AC-139 were
        retired on."""
        pair = vp_m1_pair(
            seed_label="0", floor_low=10, floor_high=1000,
            fixture="shipped", data_root=str(tmp_path / "shipped-pair"),
        )
        assert pair["low"]["derived"] == pair["high"]["derived"]
        assert pair["channel_live"] is False
        assert pair["low"]["self_check_ok"] is True
        assert pair["high"]["self_check_ok"] is True


class TestShippedFixtureTopology:
    """AC-374: the mechanism note as a machine-checked assertion."""

    def test_topology_matches_ac374(self, tmp_path) -> None:
        topo = shipped_fixture_topology([2, 10, 1000], data_root=str(tmp_path / "topo"))
        assert topo["topology_source"] == "graph.all_edges"
        assert sorted(topo["edge_sources"]) == ["N1", "N2", "N3"]
        assert len(topo["phantom_targets"]) == 3
        assert len(set(topo["phantom_targets"])) == 1
        assert topo["distinct_phantom_count"] == 1

        unions = topo["derived_union_by_floor"]
        assert set(unions.keys()) == {"2", "10", "1000"}
        values = list(unions.values())
        assert all(v == values[0] for v in values)  # identical at every floor
        assert len(values[0]) > 0  # non-empty: not a dead walk masquerading as invariance


# ---------------------------------------------------------------------------
# 5. VP-M6 -- AC-322's own fixture and measurement.
# ---------------------------------------------------------------------------


class TestGateFixture:
    def test_run_seed_disjoint_ranks(self, tmp_path) -> None:
        row = run_seed(seed_label="0", data_root=str(tmp_path / "gate"))
        assert row["seed"] == "0"
        assert isinstance(row["floor10_target_rank"], int)
        assert isinstance(row["floor1000_target_rank"], int)
        assert row["floor10_target_rank"] >= 0
        assert row["floor1000_target_rank"] >= 0
        assert row["floor10_target_rank"] != row["floor1000_target_rank"]
        # Measured (module docstring): floor=10 keeps target at rank 0;
        # floor=1000 demotes it to rank 1, via the derived arm's two-vote
        # (graph + completion) phantom score exceeding target's single vote.
        assert row["floor10_target_rank"] == 0
        assert row["floor1000_target_rank"] == 1

    def test_run_seed_deterministic_across_two_runs(self, tmp_path) -> None:
        """No hash-order lottery: the union that changes between floors has
        exactly one member, so two independent runs (fresh data dirs, same
        process) must agree."""
        row_a = run_seed(seed_label="a", data_root=str(tmp_path / "gate-a"))
        row_b = run_seed(seed_label="b", data_root=str(tmp_path / "gate-b"))
        assert row_a["floor10_target_rank"] == row_b["floor10_target_rank"]
        assert row_a["floor1000_target_rank"] == row_b["floor1000_target_rank"]


class TestVpM1Pair:
    def test_vp_m1_pair_unknown_fixture_raises(self, tmp_path) -> None:
        with pytest.raises(ValueError):
            vp_m1_pair(
                seed_label="0", floor_low=10, floor_high=1000,
                fixture="not-a-real-fixture", data_root=str(tmp_path / "bad"),
            )


class TestRunCliSmoke:
    def test_run_returns_topology_shape(self, tmp_path) -> None:
        result = run(data_root=str(tmp_path / "cli"))
        assert result["fixture"] == "shipped"
        assert result["topology_source"] == "graph.all_edges"
        assert "derived_union_by_floor" in result
