"""W2 — deterministic EVB ablation-gate workload (evals/evb_gate.py).

Container-first:
  docker run --rm crystalium:dev python -m evals evb-gate

Asserts the gate is now EVALUABLE (defined metrics, both arms) and that EVB's
multiplicative scorer evicts the single-axis distractors that the legacy additive
blend keeps — the measurable EVB effect, deterministic on the fixed population.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from evals.evb_gate import run, run_arm


def test_gate_metrics_are_defined_both_arms(tmp_path: Path) -> None:
    on = run_arm(True, data_root=str(tmp_path))
    off = run_arm(False, data_root=str(tmp_path))
    for arm in (on, off):
        assert arm["promotion_precision"] is not None
        assert arm["high_value_retention"] is not None
        assert arm["retention_precision"] is not None
        assert arm["distractor_eviction"] is not None


def test_evb_evicts_distractors_legacy_keeps_them(tmp_path: Path) -> None:
    on = run_arm(True, data_root=str(tmp_path))
    off = run_arm(False, data_root=str(tmp_path))
    # EVB (Gain x Need) devalues high-need/low-gain distractors → evicts them all.
    assert on["distractor_eviction"] == pytest.approx(1.0)
    # Legacy additive blend keeps them → evicts none.
    assert off["distractor_eviction"] == pytest.approx(0.0)


def test_high_value_retained_in_both_arms(tmp_path: Path) -> None:
    # Both scorers keep genuine high-value memories — the DoD metrics tie at 1.0.
    on = run_arm(True, data_root=str(tmp_path))
    off = run_arm(False, data_root=str(tmp_path))
    assert on["high_value_retention"] == pytest.approx(1.0)
    assert off["high_value_retention"] == pytest.approx(1.0)


def test_run_returns_evb_wins_on_retention_purity(tmp_path: Path) -> None:
    result = run(data_root=str(tmp_path))
    # The DISCRIMINATING gate (W2 earned, T2): EVB strictly improves retained-set
    # PURITY (retention_precision) with no high-value-retention regression. The
    # earlier promotion/high-value criterion saturated at 1.0 in BOTH arms (legacy
    # keeps every hv too), so it could not discriminate — see DESIGN-RATIONALE §D6.1.
    assert result["gate_pass"] is True
    rp = result["axes"]["retention_precision"]
    assert rp["on"] == pytest.approx(1.0)        # EVB retains only genuine value
    assert rp["off"] < rp["on"]                  # legacy retains distractors → impure
    assert result["axes"]["high_value_retention"]["delta"] >= 0  # no regression
