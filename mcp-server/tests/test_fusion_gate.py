"""crystalium#38 -- pytest wrapper for the fusion-quality eval gate (AC-125).

Template: test_retrieval_gate.py. The eval itself (`evals/fusion_gate.py`)
is runnable standalone via `python -m evals fusion-gate`; this file pins its
verdict as a pytest node so `make test` / CI catches a regression the same
way `test_retrieval_gate.py` pins `evals/retrieval_gate.py`.

Container-first: run via
  docker compose run --rm crystalium pytest mcp-server/tests/test_fusion_gate.py -v
"""

from __future__ import annotations

from pathlib import Path

import pytest

from evals.fusion_gate import run, run_floor_probe


class TestFusionGate:
    def test_weighted_vs_unweighted_ab(self, tmp_path: Path) -> None:
        """AC-125: the weighted arm holds the target at fused rank 0; the
        unweighted arm does not, over an identical real-RelationalStore +
        real-GraphStore corpus (the only variable is the flag)."""
        result = run(data_root=str(tmp_path / "fusion-gate"))
        assert result["weighted"]["target_rank"] == 0, result
        assert result["unweighted"]["target_rank"] != 0, result
        assert result["gate_pass"] is True, result

    def test_cross_layer_axis_present(self, tmp_path: Path) -> None:
        """AC-126: the multi-layer fixture reports the target's sparse-arm
        rank for each searched layer (episodic AND semantic)."""
        result = run(data_root=str(tmp_path / "fusion-gate-cl"))
        cross_layer = result["cross_layer"]
        assert "episodic" in cross_layer
        assert "semantic" in cross_layer
        assert cross_layer["episodic"] == 0  # sole sparse hit in that layer
        assert cross_layer["semantic"] == 0  # sole sparse hit in that layer


class TestFetchWidthFloorInflation:
    """AC-138/AC-139 -- BLOCKED/INDETERMINATE on this eval, not weakened.

    Root cause: anomaly A (deliberation.md section 3-A, follow-up F-A, out
    of scope for this change) caps `neighbor_expand`'s effective behaviour
    at `neighbor_expand(seeds) == neighbor_expand([seeds[0]])`, and
    `seeds[0]` is invariant to FETCH_WIDTH_FLOOR on this fixture (the floor
    only changes the seed-set TAIL, never its head). Measured via the C-2
    multi-run protocol (7 runs, PYTHONHASHSEED 0-5 and unset, see
    red-evidence.txt in the ESL change dir): floor=10 and floor=1000 give
    IDENTICAL target-rank distributions on BOTH the fixed and reverted
    builds -- not merely noisy, but deterministically invariant. Per
    AC-139's own escape-hatch text ("If AC-139 cannot go green, the fixture
    is seed-insensitive and AC-138 must be moved, not weakened") this test
    class records the finding rather than asserting a fabricated pass.
    C-14: this is a deviation report for the checker/FORGE, not an
    implementer's judgement call.
    """

    @pytest.mark.xfail(
        reason=(
            "AC-139 INDETERMINATE on this fixture: FETCH_WIDTH_FLOOR has no "
            "observable effect on the reverted build's target rank (anomaly "
            "A caps neighbor_expand at seeds[0], which the floor cannot "
            "move) -- confirmed 7/7 unanimous across PYTHONHASHSEED 0-5 and "
            "unset. AC-138 is therefore unfalsifiable here per its own "
            "precondition and must be moved (not weakened) once F-A lands. "
            "See red-evidence.txt."
        ),
        strict=True,
    )
    def test_reverted_build_rank_changes_with_floor(self, tmp_path: Path) -> None:
        """The literal AC-139 assertion, left in its RED (xfail) state
        rather than silently dropped -- a future F-A fix should flip this
        to strict=False and then to a real pass."""
        floor10 = run_floor_probe(
            floor=10, weighted=False, data_root=str(tmp_path / "fp10")
        )
        floor1000 = run_floor_probe(
            floor=1000, weighted=False, data_root=str(tmp_path / "fp1000")
        )
        assert floor10["target_rank"] != floor1000["target_rank"]
