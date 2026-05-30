"""W4 Objectives 2+3 — value-aware eviction + spaced re-surfacing in the Dream.

Container-first:
  docker compose run --rm crystalium pytest mcp-server/tests/test_forgetting.py -v
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from crystalium.dream.worker import DreamWorker
from crystalium.evb import evb_score
from crystalium.storage.relational import RelationalStore

_NOW = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)


def _enf_stub():
    return SimpleNamespace(assert_tier_allowed=lambda *a, **k: None)


def _worker(store, **flags):
    return DreamWorker(
        relational=store, vector_store=None, graph_store=None,
        enforcement=_enf_stub(), gate=SimpleNamespace(config=SimpleNamespace(k_corroboration=3)),
        importance_fn=evb_score, **flags,
    )


def _crystal(store, cid, *, last_access, stability, evb, protected=False):
    store.insert_crystal({
        "id": cid, "layer": "semantic", "trust_tier": "T1",
        "validation_state": "validated", "status": "active", "summary": f"s {cid}",
        "scope": {"project": "p"},
        "provenance": {"source": "verified_agent", "created_at": _NOW.isoformat()},
        "utility": {"access_count": 1, "last_access": last_access.isoformat(),
                    "importance": 0.0, "novelty_at_write": 0.5},
        "temporal": {"t_valid_from": _NOW.isoformat()},
        "memory_dynamics": {"stability": stability, "evb": evb},
        "protected": protected,
    })


def test_value_aware_eviction(tmp_path: Path) -> None:
    store = RelationalStore(db_path=tmp_path / "ev.sqlite")
    old = _NOW - timedelta(days=40)        # very low R at stability=2
    # evbs: 0.1, 0.9, 0.1(protected), 0.5(fresh) -> cutoff at percentile 0.5 = 0.5
    _crystal(store, "old_lowval", last_access=old, stability=2.0, evb=0.1)
    _crystal(store, "old_highval", last_access=old, stability=2.0, evb=0.9)
    _crystal(store, "prot_old", last_access=old, stability=2.0, evb=0.1, protected=True)
    _crystal(store, "fresh", last_access=_NOW, stability=2.0, evb=0.5)

    w = _worker(store, forgetting_fsrs=True, r_floor=0.7, evb_percentile=0.5)
    w._prune(_NOW)

    assert store.get_crystal("old_lowval")["status"] == "deprecated"   # low R AND low value
    assert store.get_crystal("old_highval")["status"] == "active"      # low R but high value
    assert store.get_crystal("prot_old")["status"] == "active"         # protected: never evicted
    assert store.get_crystal("fresh")["status"] == "active"            # high R (recent)


def test_eviction_never_on_age_alone(tmp_path: Path) -> None:
    # An old, low-R crystal that is HIGH value must survive (value gate).
    store = RelationalStore(db_path=tmp_path / "age.sqlite")
    old = _NOW - timedelta(days=60)
    _crystal(store, "a", last_access=old, stability=1.0, evb=0.9)
    _crystal(store, "b", last_access=old, stability=1.0, evb=0.1)
    w = _worker(store, forgetting_fsrs=True, r_floor=0.7, evb_percentile=0.5)
    w._prune(_NOW)
    assert store.get_crystal("a")["status"] == "active"      # high value survives despite age
    assert store.get_crystal("b")["status"] == "deprecated"


def test_resurface_boosts_aging_high_value(tmp_path: Path) -> None:
    store = RelationalStore(db_path=tmp_path / "rs.sqlite")
    # stability=10, ~21 days -> R~0.8 (between r_floor 0.7 and resurface_floor 0.85)
    aging = _NOW - timedelta(days=21)
    _crystal(store, "hot", last_access=aging, stability=10.0, evb=0.9)
    _crystal(store, "cold", last_access=aging, stability=10.0, evb=0.1)  # low value: skip
    w = _worker(store, forgetting_fsrs=True, r_floor=0.7, resurface_floor=0.85,
                evb_percentile=0.5, fsrs_boost_factor=1.5)
    boosted = w._resurface(_NOW)
    assert boosted == 1
    assert store.get_crystal("hot")["memory_dynamics"]["stability"] == 15.0  # 10 * 1.5
    assert store.get_crystal("cold")["memory_dynamics"]["stability"] == 10.0  # unchanged


def test_flag_off_uses_legacy_threshold(tmp_path: Path) -> None:
    # forgetting_fsrs off -> legacy importance-threshold prune (evb_score < threshold).
    store = RelationalStore(db_path=tmp_path / "legacy.sqlite")
    old = _NOW - timedelta(days=40)
    _crystal(store, "x", last_access=old, stability=2.0, evb=0.9)  # high value but legacy ignores it
    w = _worker(store, forgetting_fsrs=False)
    w._prune(_NOW)
    # legacy: low evb_score (old, low access) < semantic threshold 0.10 -> deprecated
    assert store.get_crystal("x")["status"] == "deprecated"
