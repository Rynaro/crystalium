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

from evals.fusion_gate import run


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

    # AC-138/AC-139 moved to `evals/floor_sensitivity_gate.py` /
    # `mcp-server/tests/test_floor_sensitivity_gate.py` (crystalium#48,
    # W-G-FLOOR; AC-139's own "moved, not weakened" escape hatch). The
    # class that used to carry a strict-marked expected-red node here is
    # retired with a mechanism note, not kept as a permanently-red-pinned
    # test -- see `CHANGE/issue-48-mechanism-note.md` and AC-321/AC-374 in
    # `evals/floor_sensitivity_gate.py`.
