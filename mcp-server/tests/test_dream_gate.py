"""W3 — deterministic Dream-intelligence gate workload (evals/dream_gate.py).

Container-first:
  docker run --rm crystalium:dev python -m evals dream-gate

Asserts the gate is EVALUABLE (metrics defined in both arms) and returns a
structured verdict. On the current fixture both gates tie (no strict win) → flags
stay OFF — an honest ablation-or-revert null.
"""

from __future__ import annotations

from pathlib import Path

from evals.dream_gate import run, run_arm


def test_arm_metrics_defined(tmp_path: Path) -> None:
    arm = run_arm(replay_evb=True, interleave=True, stc=True, data_root=str(tmp_path))
    assert arm["consolidation_gain"] is not None
    assert arm["semantic_drift"] is not None        # >=1 consolidation on the fixture
    assert arm["useful_context_retention"] is not None


def test_run_returns_structured_verdict(tmp_path: Path) -> None:
    result = run(data_root=str(tmp_path))
    assert set(result["gate_i_replay_interleave"]) == {"consolidation_gain", "semantic_drift"}
    assert "useful_context_retention" in result["gate_ii_stc"]
    assert isinstance(result["gate_i_pass"], bool)
    assert isinstance(result["gate_ii_pass"], bool)
    assert "dream_replay_evb+dream_interleave" in result["verdict"]
