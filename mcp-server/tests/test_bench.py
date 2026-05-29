"""W1 Objectives 4 & 5 — ablation bench + SWE-Bench-CL axes (dep-free cores).

Container-first:
  docker compose run --rm crystalium pytest mcp-server/tests/test_bench.py -v

These exercise the pure cores without the heavy live store:
  - metrics.py axis math on a known accuracy matrix
  - fixture_repo loader determinism (byte-stable)
  - ablation.compute_axes on synthetic MissionResults
The full live A/B (run_all / ab with live handlers) needs sentence-transformers
and is run via `make bench` / the baked image.
"""

from __future__ import annotations

import json

import pytest

from evals import metrics
from evals.ablation import compute_axes
from evals.missions import MissionResult


# ---------------------------------------------------------------------------
# metrics.py — SWE-Bench-CL axes (deterministic on a known matrix)
# ---------------------------------------------------------------------------

# 3-task sequence. R[i][j] = accuracy on task j after learning up to task i.
_R = [
    [0.90, 0.00, 0.00],
    [0.80, 0.95, 0.00],
    [0.70, 0.90, 0.92],
]


def test_average_accuracy():
    # final row mean = (0.70 + 0.90 + 0.92)/3
    assert metrics.average_accuracy(_R) == pytest.approx((0.70 + 0.90 + 0.92) / 3)


def test_forgetting():
    # task0 peak among rows 0..1 = max(0.90,0.80)=0.90; final=0.70 -> 0.20
    # task1 peak among rows 0..1 = max(0.00,0.95)=0.95; final=0.90 -> 0.05
    assert metrics.forgetting(_R) == pytest.approx((0.20 + 0.05) / 2)


def test_backward_transfer():
    # (R[2][0]-R[0][0]) + (R[2][1]-R[1][1]) = (0.70-0.90)+(0.90-0.95) = -0.25 /2
    assert metrics.backward_transfer(_R) == pytest.approx((-0.20 + -0.05) / 2)


def test_forward_transfer_zero_baseline():
    # (R[0][1]-0)+(R[1][2]-0) = 0.00 + 0.00 = 0.0 /2
    assert metrics.forward_transfer(_R) == pytest.approx(0.0)


def test_tool_use_efficiency():
    assert metrics.tool_use_efficiency(8, 10) == pytest.approx(0.8)
    assert metrics.tool_use_efficiency(1, 0) is None


def test_single_task_axes_are_zero_safe():
    R1 = [[0.9]]
    assert metrics.forgetting(R1) == 0.0
    assert metrics.backward_transfer(R1) == 0.0
    assert metrics.forward_transfer(R1) == 0.0
    assert metrics.average_accuracy(R1) == pytest.approx(0.9)


def test_non_square_matrix_rejected():
    with pytest.raises(ValueError):
        metrics.average_accuracy([[0.1, 0.2], [0.3]])


def test_swe_bench_cl_axes_bundle():
    axes = metrics.swe_bench_cl_axes(_R, successes=8, tool_calls=10)
    assert set(axes) == {
        "average_accuracy", "forgetting", "backward_transfer",
        "forward_transfer", "tool_use_efficiency",
    }
    assert axes["tool_use_efficiency"] == pytest.approx(0.8)


# ---------------------------------------------------------------------------
# fixture_repo loader determinism
# ---------------------------------------------------------------------------


def test_fixture_repo_loads_sorted_and_stable():
    from evals.fixture_repo import load_fixture_repo

    a = load_fixture_repo()
    b = load_fixture_repo()
    ids = [c["id"] for c in a["crystals"]]
    assert ids == sorted(ids), "fixture crystals must load in sorted (deterministic) order"
    # byte-stable across loads
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    assert a["project"] == "fixture-acme"
    assert len(a["crystals"]) >= 5


# ---------------------------------------------------------------------------
# ablation.compute_axes — pure, synthetic MissionResults
# ---------------------------------------------------------------------------


def _mr(mid: str, passed: bool) -> MissionResult:
    return MissionResult(mission_id=mid, passed=passed, observed={}, expected={})


def test_compute_axes_delta():
    ids = ["CAN-1", "CAN-3", "CAN-4", "CAN-5"]
    on = {m: _mr(m, True) for m in ids}                       # 4/4
    off = {"CAN-1": _mr("CAN-1", True), "CAN-3": _mr("CAN-3", False),
           "CAN-4": _mr("CAN-4", False), "CAN-5": _mr("CAN-5", False)}  # 1/4
    axes = compute_axes(on, off, ids)
    assert axes["pass_rate"] == (1.0, 0.25, pytest.approx(0.75))
    assert axes["average_accuracy"][2] == pytest.approx(0.75)


def test_compute_axes_empty_missions_is_zero():
    axes = compute_axes({}, {}, [])
    assert axes["pass_rate"] == (0.0, 0.0, 0.0)
