"""Issue 74 acceptance: executable pairing, isolation, controls and clustered CIs."""

from __future__ import annotations

import asyncio
import copy
import json
import subprocess
from pathlib import Path

import pytest
from evals.coding_ab.report import bootstrap, check_claims, claim_status, emit, summarize
from evals.coding_ab.runner import ARMS, ROOT, check_source, experiment, token_match
from evals.coding_ab.solver import solve


@pytest.fixture(scope="module")
def calibration() -> dict:
    """Run all arms through actual first-party MCP and executable fixture tests."""
    from crystalium import telemetry

    previous_store = telemetry._relational_store
    result = asyncio.run(experiment(resamples=1000))
    assert telemetry._relational_store is previous_store
    return result


def test_paired_fixture_runs_and_clean_stores(calibration: dict) -> None:
    """Every arm receives exactly six tasks x four seeds with distinct stores."""
    rows = calibration["runs"]
    assert len(rows) == 6 * 4 * len(ARMS)
    expected = {(task, seed) for task in calibration["headroom"] for seed in range(4)}
    for arm in ARMS:
        selected = [row for row in rows if row["arm"] == arm]
        assert {(r["task"], r["seed"]) for r in selected} == expected
        assert all(r["clean_store"] and r["solver"] == "fixture-v1" for r in selected)
    assert all(r["injected_tokens"] == 0 for r in rows if r["arm"] == "off")
    assert all(r["records"] == 10 for r in rows if r["arm"] == "always_on_k10")
    assert all(r["records"] == 3 for r in rows if r["arm"] == "always_on_k3")
    assert all(
        r["injected_tokens"] == 0
        for r in rows
        if r["arm"] == "gated_k3" and r["task"].startswith("absolute")
    )


def test_control_tokens_and_planted_harm(calibration: dict) -> None:
    """Independent unrelated memory costs exactly k10 and produces executable losses."""
    rows = {(r["task"], r["seed"], r["arm"]): r for r in calibration["runs"]}
    for task in calibration["headroom"]:
        for seed in range(4):
            relevant = rows[(task, seed, "always_on_k10")]
            unrelated = rows[(task, seed, "irrelevant")]
            assert relevant["injected_tokens"] == unrelated["injected_tokens"] > 0
            assert relevant["memory_sha256"] != unrelated["memory_sha256"]
    arms = calibration["strata"]["all"]
    assert arms["irrelevant"]["metrics"]["distraction_rate"] > 0
    assert arms["irrelevant"]["metrics"]["resolve_rate"] < arms["off"]["metrics"]["resolve_rate"]
    assert arms["fix_span"]["metrics"]["resolve_rate"] == 1


def test_headroom_and_anchor_strata(calibration: dict) -> None:
    """Baseline execution, rather than task labels, determines 0/4, 2/4 and 4/4."""
    for headroom in ("0/4", "1-3/4", "4/4"):
        assert calibration["strata"][headroom]["off"]["task_count"] == 2
        for anchor in ("anchor", "no_anchor"):
            group = calibration["strata"][headroom + ":" + anchor]
            assert group["off"]["task_count"] == 1
            assert set(group) == set(ARMS)
    for arms in calibration["strata"].values():
        for arm in arms.values():
            assert {
                "resolve_rate",
                "injected_tokens",
                "distraction_rate",
                "steps",
                "total_tokens",
            } <= set(arm["metrics"])
            assert len(arm["delta_resolve_rate_ci95"]) == 2


def test_bootstrap_tasks_not_independent_seeds() -> None:
    """Duplicating paired seeds must not falsely narrow task-clustered intervals."""
    tasks = [{"id": "a", "anchor": True}, {"id": "b", "anchor": False}]

    def rows(n: int) -> list[dict]:
        return [
            {
                "task": task,
                "seed": seed,
                "arm": arm,
                "resolved": arm != "off" and task == "a",
                "steps": 1,
                "injected_tokens": int(arm != "off"),
                "total_tokens": None,
            }
            for task in ("a", "b")
            for seed in range(n)
            for arm in ("off", "raw")
        ]

    short = summarize(rows(1), tasks, 1000, 74)["strata"]["all"]["raw"]
    long = summarize(rows(20), tasks, 1000, 74)["strata"]["all"]["raw"]
    assert short["delta_resolve_rate_ci95"] == long["delta_resolve_rate_ci95"] == [0, 1]
    assert bootstrap([0, 1, -1], 1000, 74) == bootstrap([0, 1, -1], 1000, 74)
    with pytest.raises(ValueError, match="1000"):
        bootstrap([1], 999, 74)


def test_no_claim_without_ci(calibration: dict, tmp_path: Path) -> None:
    """P0-1 consumers can reject unsupported claims mechanically."""
    assert claim_status(None) == "no claim"
    assert claim_status([float("nan"), 1]) == "no claim"
    assert claim_status([1, -1]) == "no claim"
    check_claims(calibration)
    bad = copy.deepcopy(calibration)
    bad["strata"]["all"]["raw"]["delta_resolve_rate_ci95"] = None
    with pytest.raises(ValueError, match="unsupported claim"):
        check_claims(bad)
    bad["strata"]["all"]["raw"]["claims"]["resolve_rate"] = "no claim"
    check_claims(bad)
    digest = emit(calibration, tmp_path)
    assert digest == emit(calibration, tmp_path)
    assert json.loads((tmp_path / "report.json").read_text()) == calibration
    assert "no claim" in (tmp_path / "report.md").read_text()


def test_solver_consumes_memory_and_real_oracle(tmp_path: Path) -> None:
    """Removing/swapping the actual retrieved directive changes executable outcomes."""
    fixture = ROOT / "fixtures" / "absolute"
    source = (fixture / "implementation.py").read_text()
    request = {"source": source, "seed": 0, "exploration": 0, "memory": ""}
    assert solve(request)["source"] == source
    request["memory"] = "PATCH[return x + 1 => return abs(x)]"
    repaired = solve(request)["source"]
    oracle = (fixture / "check.py").read_text()
    assert check_source(repaired, oracle, tmp_path / "good")
    assert not check_source(source, oracle, tmp_path / "base")
    request["memory"] = "PATCH[return x + 1 => return x * 0]"
    assert not check_source(solve(request)["source"], oracle, tmp_path / "harmful")
    assert len(token_match(request["memory"], 50).split()) == 50


def test_oracle_requires_completion_and_timeout_is_not_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A premature zero exit cannot masquerade as completed assertions."""
    assert not check_source(
        "import os; os._exit(0)",
        "from implementation import transform\nassert False",
        tmp_path / "early",
    )

    def timeout(*args: object, **kwargs: object) -> None:
        raise subprocess.TimeoutExpired("oracle", 10)

    monkeypatch.setattr(subprocess, "run", timeout)
    with pytest.raises(subprocess.TimeoutExpired):
        check_source("", "assert True", tmp_path / "timeout")


def test_reject_unpaired_and_ungated_live(
    calibration: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Configuration mistakes and infrastructure errors must not become solver losses."""
    monkeypatch.delenv("CRYSTALIUM_CODING_AB_LIVE", raising=False)
    with pytest.raises(ValueError, match="CRYSTALIUM_CODING_AB_LIVE"):
        asyncio.run(experiment(live_command=["no-such-executable"]))
    with pytest.raises(ValueError, match="unique"):
        asyncio.run(experiment(seeds=(0, 0)))
    tasks = json.loads((ROOT / "tasks.json").read_text())["tasks"]
    with pytest.raises(ValueError, match="unpaired"):
        summarize(calibration["runs"][1:], tasks, 1000, 74)
