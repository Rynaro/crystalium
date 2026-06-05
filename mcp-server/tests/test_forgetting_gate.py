"""W4 — forgetting-gate long-session workload (evals/forgetting_gate.py).

Container-first:
  docker run --rm crystalium:dev python -m evals forgetting-gate

Asserts the gate is EVALUABLE (three axes defined in an arm) and returns a
structured verdict. The disposition (flip/stay-off) is data-dependent and
reported honestly by run().
"""

from __future__ import annotations

from pathlib import Path

import pytest

from evals.forgetting_gate import run, run_arm


def test_arm_axes_defined(tmp_path: Path) -> None:
    arm = run_arm(forgetting_fsrs=True, data_root=str(tmp_path))
    assert arm["memory_size_plateau"] is not None
    assert arm["high_value_retention"] is not None
    assert arm["recall_latency_ms"] is not None
    assert len(arm["counts"]) > 1


def test_run_returns_structured_verdict(tmp_path: Path) -> None:
    result = run(data_root=str(tmp_path))
    assert set(result["axes"]) == {"memory_size_plateau", "high_value_retention", "recall_latency_ms"}
    assert isinstance(result["gate_pass"], bool)
    assert "forgetting_fsrs" in result["verdict"]


@pytest.mark.slow
def test_fsrs_does_not_meaningfully_beat_lru_stays_off(tmp_path: Path) -> None:
    # T2 honest null: under the redesigned discriminating workload (60 ticks,
    # sustained noise, infrequent keystone recall, prune-every-tick) FSRS does NOT
    # meaningfully beat LRU — both arms retain the keystone (LRU's accumulated
    # access keeps it above the prune threshold) and the plateau gap is < 10%
    # (sign-unstable under noise). forgetting_fsrs stays OFF. See BENCH-NOTES §W4.
    r = run(data_root=str(tmp_path))
    assert r["axes"]["high_value_retention"]["on"] == r["axes"]["high_value_retention"]["off"]
    assert r["gate_pass"] is False


@pytest.mark.slow
def test_memory_metrics_are_reproducible(tmp_path: Path) -> None:
    # The deciding memory axes (plateau, retention) must be deterministic across
    # runs now that noise summaries are seeded by index, not uuid (latency is
    # wall-clock and excluded).
    a = run(data_root=str(tmp_path / "a"))
    b = run(data_root=str(tmp_path / "b"))
    assert a["axes"]["memory_size_plateau"] == b["axes"]["memory_size_plateau"]
    assert a["axes"]["high_value_retention"] == b["axes"]["high_value_retention"]
