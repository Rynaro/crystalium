"""W2 Objectives 5+6 — promotion_precision + high_value_retention axes.

Container-first:
  docker compose run --rm crystalium pytest mcp-server/tests/test_new_axes.py -v

Pure axis math on synthetic snapshots (no live store) + the promotions ledger /
dynamics-snapshot store methods.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from crystalium.storage.relational import RelationalStore
from evals.metrics import high_value_retention, promotion_precision

_NOW = datetime(2026, 5, 29, tzinfo=timezone.utc)


# --- promotion_precision ----------------------------------------------------

def test_promotion_precision_basic():
    promotions = [{"crystal_id": "a"}, {"crystal_id": "b"}, {"crystal_id": "c"}, {"crystal_id": "d"}]
    crystals = {
        "a": {"access_count": 2, "outcome_success_score": 0.9},   # useful
        "b": {"access_count": 1, "outcome_success_score": 0.6},   # useful
        "c": {"access_count": 0, "outcome_success_score": 0.9},   # not recalled
        "d": {"access_count": 3, "outcome_success_score": 0.2},   # bad outcome
    }
    assert promotion_precision(promotions, crystals) == pytest.approx(2 / 4)


def test_promotion_precision_none_when_no_promotions():
    assert promotion_precision([], {}) is None


def test_promotion_precision_unscored_outcome_excluded():
    promotions = [{"crystal_id": "a"}]
    crystals = {"a": {"access_count": 5, "outcome_success_score": None}}
    assert promotion_precision(promotions, crystals) == 0.0


# --- high_value_retention ---------------------------------------------------

def test_high_value_retention_basic():
    crystals = [
        {"evb": 0.9, "status": "active"},      # high, survived
        {"evb": 0.8, "status": "deprecated"},  # high, evicted
        {"evb": 0.7, "status": "active"},      # high, survived
        {"evb": 0.1, "status": "deprecated"},  # low — ignored
        {"evb": None, "status": "active"},     # uncomputed — ignored
    ]
    assert high_value_retention(crystals, evb_threshold=0.5) == pytest.approx(2 / 3)


def test_high_value_retention_none_when_no_high_evb():
    crystals = [{"evb": 0.1, "status": "active"}, {"evb": None, "status": "active"}]
    assert high_value_retention(crystals, evb_threshold=0.5) is None


# --- instrumentation store methods ------------------------------------------

def test_promotions_ledger_and_dynamics_snapshot(tmp_path: Path) -> None:
    store = RelationalStore(db_path=tmp_path / "instr.sqlite")
    crystal = {
        "id": "x1", "layer": "semantic", "trust_tier": "T1",
        "validation_state": "validated", "status": "active", "summary": "s",
        "scope": {"project": "p"},
        "provenance": {"source": "verified_agent", "created_at": _NOW.isoformat()},
        "utility": {"access_count": 2, "last_access": _NOW.isoformat(),
                    "importance": 0.0, "novelty_at_write": 0.5,
                    "outcome_success_score": 0.8},
        "temporal": {"t_valid_from": _NOW.isoformat()},
        "memory_dynamics": {"evb": 0.7},
    }
    store.insert_crystal(crystal)
    store.record_promotion("x1", "semantic", now=_NOW)

    promotions = store.list_promotions()
    assert len(promotions) == 1 and promotions[0]["crystal_id"] == "x1"

    snap = store.list_crystals_with_dynamics()
    assert len(snap) == 1
    row = snap[0]
    assert row["evb"] == 0.7
    assert row["access_count"] == 2
    assert row["outcome_success_score"] == 0.8
    assert row["status"] == "active"

    # End-to-end: the two axes computed from these store snapshots.
    by_id = {r["id"]: r for r in snap}
    assert promotion_precision(promotions, by_id) == pytest.approx(1.0)
    assert high_value_retention(snap, evb_threshold=0.5) == pytest.approx(1.0)
