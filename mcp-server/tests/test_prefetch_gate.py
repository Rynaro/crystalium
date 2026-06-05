"""W5 — predictive-prefetch ablation gate (evals/prefetch_gate.py).

Guards the honest T2 verdict: the imperfect-predictor confound is FIXED (the gate
no longer hands the checkpoint the verbatim future query → prediction accuracy is
< 1.0), but the p95 win is cache-vs-no-cache (the OFF arm has no recall cache at
all), so protention is NOT isolated from plain cache-warming — recall_prefetch
stays OFF. See BENCH-NOTES §W5.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from evals.prefetch_gate import run


@pytest.mark.slow
def test_predictor_is_imperfect_not_fabricated(tmp_path: Path) -> None:
    r = run(data_root=str(tmp_path))
    acc = r["axes"]["prediction_accuracy"]["on"]
    # The fabricated-perfect-prediction confound (verbatim future query) is gone.
    assert acc is not None and acc < 1.0


@pytest.mark.slow
def test_protention_not_isolated_so_flag_stays_off(tmp_path: Path) -> None:
    r = run(data_root=str(tmp_path))
    # OFF arm has no cache → on-vs-off is cache-vs-no-cache, not protention.
    assert r["protention_isolated"] is False
    assert r["gate_pass"] is False
