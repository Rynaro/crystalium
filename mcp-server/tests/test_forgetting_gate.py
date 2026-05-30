"""W4 — forgetting-gate long-session workload (evals/forgetting_gate.py).

Container-first:
  docker run --rm crystalium:dev python -m evals forgetting-gate

Asserts the gate is EVALUABLE (three axes defined in an arm) and returns a
structured verdict. The disposition (flip/stay-off) is data-dependent and
reported honestly by run().
"""

from __future__ import annotations

from pathlib import Path

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
