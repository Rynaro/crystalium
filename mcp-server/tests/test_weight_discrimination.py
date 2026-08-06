"""crystalium#55 (W-G-WD) -- pytest wrapper for the weight-discriminating
fixture (AC-317, AC-318, AC-375, AC-376).

`evals/weight_discrimination.py` is runnable standalone via
`python -c "import evals.weight_discrimination as m; ..."` (see its module
docstring for the exact rule-(g) invocation this campaign's criteria use);
this file pins its behaviour as pytest nodes so `make test` / CI catches a
regression the same way `test_fusion_gate.py` / `test_corpus_rig.py` do for
their own gates.

`test_weight_injection_reaches_instance` is a bare module-level function
(not a class method) -- its name is NORMATIVE (AC-376's VERIFY line names
this exact node path: `test_weight_discrimination.py::test_weight_injection_reaches_instance`).

Container-first:
  docker compose run --rm crystalium pytest mcp-server/tests/test_weight_discrimination.py -v
"""

from __future__ import annotations

from pathlib import Path

import pytest
from evals.weight_discrimination import (
    _K_RRF,
    _RANK_A,
    _RANK_B,
    build_aetheryte,
    emit,
    run,
    run_dp1_recheck,
)

# ---------------------------------------------------------------------------
# AC-376 (K-C-N6) -- the weight readback becomes a real pin. Bare
# module-level function; node name and the `build_aetheryte` symbol are
# both normative (spec.criteria.amend-03.md).
# ---------------------------------------------------------------------------


def test_weight_injection_reaches_instance(tmp_path: Path) -> None:
    """For each weight the campaign cares about (AC-317's three, AC-375's
    wide band, and the 1.0 control point), `build_aetheryte(w_derived=w)`
    must return an `Aetheryte` whose OWN `.fusion_weight_derived` equals
    `w` -- read off the INSTANCE the test itself built, never off the
    kwargs dict `build_aetheryte` was called with (that comparison is a
    tautology and cannot fail -- VP-M4)."""
    for w in (0.5, 0.90, 0.95, 1.00, 100.0):
        aetheryte = build_aetheryte(w_derived=w, data_root=str(tmp_path / f"readback-{w}"))
        assert aetheryte.fusion_weight_derived == w, (w, aetheryte.fusion_weight_derived)


# ---------------------------------------------------------------------------
# AC-318 (K-N1) -- literal sentinel in the docstring's first paragraph.
# Mirrors the exact check the criterion's own VERIFY command runs, as a
# permanent regression node rather than a one-off shell invocation.
# ---------------------------------------------------------------------------


class TestDocstringPurposeSentinel:
    def test_first_paragraph_states_dp1_recheck_not_characterisation(self) -> None:
        import evals.weight_discrimination as m

        first_paragraph = (m.__doc__ or "").split("\n\n")[0]
        assert "DP-1" in first_paragraph, first_paragraph
        assert "NOT band characterisation" in first_paragraph, first_paragraph


# ---------------------------------------------------------------------------
# AC-317 -- >= 2 distinct outcomes across {0.90, 0.95, 1.00}.
# AC-375 -- the wide-band positive-capability control (K-C-N2).
# ---------------------------------------------------------------------------


class TestWeightDiscriminationOutcomes:
    def test_ac317_distinct_outcomes_across_default_band(self, tmp_path: Path) -> None:
        result = run(seed_label="0", data_root=str(tmp_path / "wd-ac317"))
        cells = result["cells"]
        assert len(cells) == 3, cells
        assert [c["weight"] for c in cells] == [0.90, 0.95, 1.00]
        for cell in cells:
            assert cell["verdict"] == "measured", cell
            assert isinstance(cell["weight_readback"], float), cell
            assert cell["weight_readback"] == cell["weight"], cell
            assert isinstance(cell["outcome"], str), cell
        outcomes = {c["outcome"] for c in cells}
        assert len(outcomes) >= 2, (
            "fewer than 2 distinct outcomes across {0.90, 0.95, 1.00} -- "
            "this is exactly the #55 degeneracy, not a passing gate"
        )
        assert result["distinct_outcome_count"] == len(outcomes)

    def test_ac375_wideband_positive_capability_control(self, tmp_path: Path) -> None:
        """K-C-N2: without this, AC-317's negative cannot be told apart
        from "my fixture cannot discriminate at all" -- a 200x weight swing
        (0.5 -> 100.0) that still produced one outcome would indict the
        fixture, not the weights."""
        result = run(
            seed_label="0", weights=[0.5, 1.0, 100.0], data_root=str(tmp_path / "wd-ac375")
        )
        cells = result["cells"]
        assert [c["weight"] for c in cells] == [0.5, 1.0, 100.0]
        for cell in cells:
            assert cell["verdict"] == "measured", cell
            assert cell["weight_readback"] == cell["weight"], cell
            assert isinstance(cell["outcome"], str), cell
        outcomes = {c["outcome"] for c in cells}
        assert len(outcomes) >= 2, cells

    def test_outcome_crosses_over_from_b_to_a_as_weight_rises(self, tmp_path: Path) -> None:
        """Directional sanity check, not just cardinality: B (the known,
        rank-2 base-arm vote) leads at the low end of the band and A (the
        derived-only phantom) leads at the high end -- never the reverse --
        matching the stated crossover `w* = (60+1)/(60+2) ~= 0.968`."""
        result = run(seed_label="0", weights=[0.90, 1.00], data_root=str(tmp_path / "wd-crossover"))
        low, high = result["cells"]
        assert low["outcome"] == "B_outranks_A", low
        assert high["outcome"] == "A_outranks_B", high

    def test_scores_match_the_stated_rrf_arithmetic(self, tmp_path: Path) -> None:
        """The ordering is not just qualitatively right -- the fused score
        `Aetheryte.recall` actually produced must equal the module's own
        hand-stated formula, `w_derived / (60 + 1)` for A and
        `1.0 / (60 + 2)` for B, within float tolerance."""
        result = run(seed_label="0", weights=[0.90, 1.00], data_root=str(tmp_path / "wd-arith"))
        for cell in result["cells"]:
            assert cell["score_a"] == pytest.approx(cell["expected_score_a"], abs=1e-12), cell
            assert cell["score_b"] == pytest.approx(cell["expected_score_b"], abs=1e-12), cell
            assert cell["expected_score_a"] == pytest.approx(
                cell["weight"] / (_K_RRF + _RANK_A), abs=1e-15
            )
            assert cell["expected_score_b"] == pytest.approx(1.0 / (_K_RRF + _RANK_B), abs=1e-15)
            # w_sparse pinned to exactly 1.0 (fusion_sparse_boost_alpha=0.0)
            # -- no D3 selectivity cross-term muddying B's score.
            assert cell["w_sparse"] == 1.0, cell

    def test_a_is_derived_only_and_b_is_the_known_sparse_rank(self, tmp_path: Path) -> None:
        """Fixture self-check: A has no sparse presence at all (arm_sizes.sparse
        counts only the leader + B), and the derived arm holds exactly A."""
        result = run(seed_label="0", weights=[1.00], data_root=str(tmp_path / "wd-armcheck"))
        cell = result["cells"][0]
        assert cell["arm_sizes"]["sparse"] == 2, cell  # leader + B, never A
        assert cell["arm_sizes"]["dense"] == 0, cell
        assert cell["arm_sizes"]["derived"] == 1, cell  # A alone
        assert cell["liveness"]["pinned"]["phantom_derived_only"] is True
        assert cell["liveness"]["pinned"]["base_known_rank"] is True


# ---------------------------------------------------------------------------
# Emission (rule (g)) round trip -- `emit` is re-exported from the rig, but
# this module's own `run()` result must be a valid input to it.
# ---------------------------------------------------------------------------


class TestEmit:
    def test_emit_writes_stamped_artifact(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CRYSTALIUM_GATE_NONCE", "wgwd-test-nonce")
        monkeypatch.setenv("CRYSTALIUM_TREE_SHA", "wgwd-test-sha")
        result = run(seed_label="0", data_root=str(tmp_path / "wd-emit"))
        out = tmp_path / "wd-seed-0.json"
        emit(result, str(out))

        import json

        written = json.loads(out.read_text())
        assert written["run_nonce"] == "wgwd-test-nonce"
        assert written["tree_sha"] == "wgwd-test-sha"
        assert written["seed_label"] == "0"
        assert len(written["cells"]) == 3


# ---------------------------------------------------------------------------
# R-CONF regression coverage: a severed graph edge must collapse every
# cell's verdict to "confounded" with outcome=None -- never a silently
# wrong number. This is the permanent-suite twin of the red-check this
# unit records externally (CHANGE/red-evidence-wgwd.json) by literally
# perturbing the shipped fixture the same way, container-side.
# ---------------------------------------------------------------------------


class TestRConfConfound:
    def test_severed_derived_support_confounds_every_cell(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import evals.weight_discrimination as m

        # Sever A's only support: `_build_fixture` calls the module-level
        # `seed_graph` it imported from the rig -- patch THAT symbol (not
        # `GraphStore` itself, which has no public "remove_edge") so every
        # node still gets added but no edge ever does. This is the same
        # perturbation the red-check performs container-side, expressed as
        # a permanent regression node.
        original_seed_graph = m.seed_graph

        def _seed_graph_no_edge(graph: object, *, nodes=(), edges=()) -> None:
            original_seed_graph(graph, nodes=nodes, edges=())  # drop every edge

        monkeypatch.setattr(m, "seed_graph", _seed_graph_no_edge)

        result = run(
            seed_label="0", weights=[0.90, 0.95, 1.00], data_root=str(tmp_path / "wd-redcheck")
        )
        cells = result["cells"]
        assert len(cells) == 3
        for cell in cells:
            assert cell["verdict"] == "confounded", cell
            assert cell["outcome"] is None, cell
        outcomes = {c["outcome"] for c in cells}
        assert outcomes == {None}
        # AC-317's own type-guard (`.outcome | type == "string"`) would now
        # fail on every cell -- this IS the red-check's mechanism, not a
        # separate one.


# ---------------------------------------------------------------------------
# crystalium#42 (W-42) -- AC-352 DP-1(b) re-check regression coverage. Pins
# `run_dp1_recheck`'s behaviour as a pytest node so `make test` / CI catches
# a regression the same way the rest of this file does for AC-317/AC-375 --
# the container-side commands in spec.criteria.amend-03.md remain the
# criterion's own VERIFY, this is the permanent-suite twin.
# ---------------------------------------------------------------------------


class TestDp1Recheck:
    def test_no_p1_recreation_at_supported_weight(self, tmp_path: Path) -> None:
        """AC-352 part (i): at `w_derived=1.0` (the only supported value,
        C-9), a derived-only record must NOT outrank a record backed by two
        base arms, WITH seed exclusion relaxed on the retrieval path."""
        result = run_dp1_recheck(w_derived=1.0, data_root=str(tmp_path / "dp1-recheck"))
        assert result["p1_recreated"] is False
        assert result["w_derived"] == 1.0
        assert result["derived_only_rank"] > result["two_base_arm_rank"]
        assert result["recall_seed_derived_credit"] is True

    def test_positive_control_can_recreate_p1(self, tmp_path: Path) -> None:
        """AC-352 part (ii), the MANDATORY positive control: the same
        fixture must be able to say `true` at `w_derived=100.0`
        (`config.py:296-298`'s stated ceiling) -- otherwise part (i)'s
        `false` is not evidence (global rule (f) / S-14)."""
        result = run_dp1_recheck(w_derived=100.0, data_root=str(tmp_path / "dp1-control"))
        assert result["p1_recreated"] is True
        assert result["w_derived"] == 100.0
        assert result["derived_only_rank"] < result["two_base_arm_rank"]
