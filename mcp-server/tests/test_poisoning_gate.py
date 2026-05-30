"""W6 C9 — poisoning-resistance ASR gate smoke test (deterministic, BM25 recall).

Container-first:
  docker compose run --rm crystalium pytest mcp-server/tests/test_poisoning_gate.py -v
"""

from __future__ import annotations

from pathlib import Path

from evals.poisoning_gate import run, run_arm


def test_off_arm_resurfaces_deprecated_poison(tmp_path: Path) -> None:
    off = run_arm(defenses=False, data_root=str(tmp_path / "off"))
    assert off["n_attacks"] == 8
    assert off["asr"] == 1.0                       # no active-filter -> deprecated poison surfaces
    assert off["tier_wall_held"] == 8              # tier wall always on (chokepoint sacred)


def test_on_arm_blocks_poison(tmp_path: Path) -> None:
    on = run_arm(defenses=True, data_root=str(tmp_path / "on"))
    assert on["asr"] == 0.0                         # recall_active_only excludes rejected poison
    assert on["tier_wall_held"] == 8


def test_gate_passes_and_tier_wall_holds(tmp_path: Path) -> None:
    res = run(data_root=str(tmp_path / "gate"))
    assert res["gate_pass"] is True
    assert res["tier_wall_ok"] is True
    assert res["axes"]["asr"]["on"] <= res["axes"]["asr"]["bar"]
    assert res["axes"]["asr"]["on"] < res["axes"]["asr"]["off"]
