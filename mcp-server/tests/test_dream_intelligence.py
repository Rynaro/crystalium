"""W3 — Dream intelligence augments (replay / interleave / STC / abstraction).

Container-first:
  docker compose run --rm crystalium pytest mcp-server/tests/test_dream_intelligence.py -v

Unit-level: STC k_override decision, interleave mixing, EVB scoring of rows. End
-to-end replay ordering is exercised by the dream-gate workload (set-union in
_gather makes final cluster order non-deterministic to assert here).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from crystalium.dream.worker import DreamWorker
from crystalium.evb import evb_score
from crystalium.storage.relational import RelationalStore

_NOW = datetime(2026, 5, 29, 12, 0, 0, tzinfo=timezone.utc)


def _gate_stub(k: int = 3):
    return SimpleNamespace(config=SimpleNamespace(k_corroboration=k))


def _worker(relational=None, **flags) -> DreamWorker:
    return DreamWorker(
        relational=relational,
        vector_store=None,
        graph_store=None,
        enforcement=None,
        gate=_gate_stub(),
        importance_fn=evb_score,
        **flags,
    )


def _episode(cid: str, *, created_at: datetime, outcome=0.9, novelty=0.8, access=20) -> dict:
    return {
        "id": cid,
        "layer": "episodic",
        "created_at": created_at.isoformat(),
        "utility": {
            "access_count": access,
            "last_access": created_at.isoformat(),
            "outcome_success_score": outcome,
            "novelty_at_write": novelty,
        },
    }


# --- STC k_override ---------------------------------------------------------

def test_stc_off_returns_none():
    w = _worker(stc=False)
    cluster = [_episode("e1", created_at=_NOW), _episode("e2", created_at=_NOW)]
    assert w._stc_k_override(cluster, _NOW) is None


def test_stc_capture_lowers_k():
    # Two high-EVB episodes within the window → salient + neighbour → k=3-1=2.
    w = _worker(stc=True, stc_threshold=0.3, stc_window_s=3600)
    cluster = [
        _episode("e1", created_at=_NOW),
        _episode("e2", created_at=_NOW + timedelta(seconds=600)),
    ]
    assert w._stc_k_override(cluster, _NOW) == 2


def test_stc_no_neighbour_in_window_returns_none():
    # Salient episode but the only other is far outside the window.
    w = _worker(stc=True, stc_threshold=0.3, stc_window_s=60)
    cluster = [
        _episode("e1", created_at=_NOW),
        _episode("e2", created_at=_NOW + timedelta(days=2)),
    ]
    assert w._stc_k_override(cluster, _NOW) is None


def test_stc_no_salient_returns_none():
    # All low-EVB (no outcome, no novelty, no access) → none salient.
    w = _worker(stc=True, stc_threshold=0.9, stc_window_s=3600)
    cluster = [
        _episode("e1", created_at=_NOW, outcome=0.0, novelty=0.0, access=0),
        _episode("e2", created_at=_NOW, outcome=0.0, novelty=0.0, access=0),
    ]
    assert w._stc_k_override(cluster, _NOW) is None


def test_stc_k_override_floored_at_one():
    w = _worker(stc=True, stc_threshold=0.3, stc_window_s=3600)
    w.gate = _gate_stub(k=1)  # base k=1 → k-1=0 → floored to 1
    cluster = [
        _episode("e1", created_at=_NOW),
        _episode("e2", created_at=_NOW + timedelta(seconds=10)),
    ]
    assert w._stc_k_override(cluster, _NOW) == 1


# --- interleave -------------------------------------------------------------

def _sem(store: RelationalStore, cid: str) -> None:
    store.insert_crystal({
        "id": cid, "layer": "semantic", "trust_tier": "T1",
        "validation_state": "validated", "status": "active", "summary": f"sem {cid}",
        "scope": {"project": "p"},
        "provenance": {"source": "verified_agent", "created_at": _NOW.isoformat()},
        "utility": {"access_count": 0, "last_access": _NOW.isoformat(),
                    "importance": 0.0, "novelty_at_write": 0.5},
        "temporal": {"t_valid_from": _NOW.isoformat()},
    })


def test_interleave_off_is_noop(tmp_path: Path):
    store = RelationalStore(db_path=tmp_path / "i0.sqlite")
    _sem(store, "s1")
    w = _worker(relational=store, interleave=False)
    cluster = [_episode("e1", created_at=_NOW)]
    w._interleave_semantic(cluster, _NOW)
    assert len(cluster) == 1  # untouched


def test_interleave_appends_semantic(tmp_path: Path):
    store = RelationalStore(db_path=tmp_path / "i1.sqlite")
    _sem(store, "s1")
    _sem(store, "s2")
    w = _worker(relational=store, interleave=True, interleave_ratio=1.0)
    cluster = [_episode("e1", created_at=_NOW)]  # 1 episodic → round(1.0*1)=1 semantic
    w._interleave_semantic(cluster, _NOW)
    assert len(cluster) == 2
    assert any(c["layer"] == "semantic" for c in cluster)


# --- EVB scoring of rows ----------------------------------------------------

def test_evb_high_signal_scores_higher():
    w = _worker()
    hi = _episode("hi", created_at=_NOW, outcome=0.9, novelty=0.9, access=50)
    lo = _episode("lo", created_at=_NOW - timedelta(days=120), outcome=0.0, novelty=0.0, access=0)
    assert w._evb(hi, _NOW) > w._evb(lo, _NOW)
