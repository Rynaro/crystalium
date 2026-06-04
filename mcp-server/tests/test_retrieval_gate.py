"""W5(i) — retrieval pattern-completion / context-match gate (evals/retrieval_gate.py).

Guards the T2 verdict: with the larger corpus (24 lexically-close distractors crowd
the graph-distant spokes out of flat dense recall), the decaying multi-hop walk
recovers them — completion lifts multi-hop recall/F1, so `recall_completion` is ON;
`recall_context_match` shows no rank lift, so it stays OFF. See BENCH-NOTES §W5(i).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from evals.retrieval_gate import run


@pytest.mark.slow
def test_completion_lifts_multihop_recall_and_f1(tmp_path: Path) -> None:
    r = run(data_root=str(tmp_path))
    assert r["graph_ok"] is True  # real kuzu graph, not the null stub
    f1 = r["axes"]["multihop_f1"]
    # The multi-hop walk recovers a graph-reachable relevant flat dense recall misses.
    assert f1["completion"] > f1["flat"]
    assert r["completion_pass"] is True


@pytest.mark.slow
def test_context_match_shows_no_rank_lift_stays_off(tmp_path: Path) -> None:
    r = run(data_root=str(tmp_path))
    # The context-matching crystal already ranks first in both arms — no lift.
    assert r["context_pass"] is False
