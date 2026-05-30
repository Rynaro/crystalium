"""W6 C5 — belief-drift detection (A-MemGuard-style) Dream phase.

Container-first:
  docker compose run --rm crystalium pytest mcp-server/tests/test_drift_detect.py -v
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

from crystalium.dream.worker import DreamWorker
from crystalium.storage.relational import RelationalStore

_NOW = datetime(2026, 6, 1, tzinfo=timezone.utc)


def _semantic(cid: str, *, tier: str, summary: str, project="p") -> dict:
    return {
        "id": cid, "layer": "semantic", "trust_tier": tier,
        "validation_state": "validated", "status": "active", "summary": summary,
        "content_ref": "a" * 64, "scope": {"project": project},
        "provenance": {"source": "verified_agent", "author_agent": "x",
                       "created_at": _NOW.isoformat()},
        "utility": {"importance": 0.0}, "temporal": {"t_valid_from": _NOW.isoformat()},
    }


def _worker(tmp_path: Path, *, hits: list[dict], embed_ok: bool = True,
            tau_lo=0.80, tau_hi=0.97) -> tuple[DreamWorker, RelationalStore]:
    store = RelationalStore(db_path=tmp_path / "drift.sqlite")
    vec = MagicMock()
    vec.embed.return_value = [0.1, 0.2, 0.3] if embed_ok else []
    vec.dense_search.return_value = hits
    worker = DreamWorker(
        relational=store, vector_store=vec, graph_store=MagicMock(),
        enforcement=MagicMock(), gate=MagicMock(), importance_fn=lambda *a, **k: 0.0,
        drift_detect=True, drift_tau_lo=tau_lo, drift_tau_hi=tau_hi,
    )
    return worker, store


def test_flags_lower_trust_divergent_fact(tmp_path: Path) -> None:
    # sim = 1 - 0.12 = 0.88, inside [0.80, 0.97); C (T3) less trusted than P (T1).
    hits = [{"id": "P", "_distance": 0.12}, {"id": "C", "_distance": 0.12}]
    worker, store = _worker(tmp_path, hits=hits)
    store.insert_crystal(_semantic("P", tier="T1", summary="deploys use blue-green"))
    store.insert_crystal(_semantic("C", tier="T3", summary="deploys use red-black"))
    n = worker._check_drift(_NOW)
    assert n == 1
    rows = store.list_drift_audit()
    assert len(rows) == 1 and rows[0]["crystal_id"] == "C" and rows[0]["prior_id"] == "P"
    assert store.get_crystal("C")["memory_dynamics"]["drift_flagged"] is True
    # the higher-trust prior P is never flagged
    assert store.get_crystal("P").get("memory_dynamics") in (None, {}) or \
        "drift_flagged" not in (store.get_crystal("P").get("memory_dynamics") or {})


def test_near_duplicate_not_flagged(tmp_path: Path) -> None:
    # sim = 0.99 >= tau_hi -> a near-duplicate (dedup's job), NOT drift.
    hits = [{"id": "P", "_distance": 0.01}, {"id": "C", "_distance": 0.01}]
    worker, store = _worker(tmp_path, hits=hits)
    store.insert_crystal(_semantic("P", tier="T1", summary="same fact"))
    store.insert_crystal(_semantic("C", tier="T3", summary="same fact too"))
    assert worker._check_drift(_NOW) == 0
    assert store.list_drift_audit() == []


def test_equal_trust_not_flagged(tmp_path: Path) -> None:
    # Same trust tier on both -> no trust gap, so no drift flag (divergence among
    # equal-trust peers is not a higher-trust contradiction).
    hits = [{"id": "P", "_distance": 0.12}, {"id": "C", "_distance": 0.12}]
    worker, store = _worker(tmp_path, hits=hits)
    store.insert_crystal(_semantic("P", tier="T1", summary="deploys use blue-green"))
    store.insert_crystal(_semantic("C", tier="T1", summary="deploys use red-black"))
    assert worker._check_drift(_NOW) == 0


def test_different_project_not_flagged(tmp_path: Path) -> None:
    hits = [{"id": "P", "_distance": 0.12}, {"id": "C", "_distance": 0.12}]
    worker, store = _worker(tmp_path, hits=hits)
    store.insert_crystal(_semantic("P", tier="T1", summary="x", project="alpha"))
    store.insert_crystal(_semantic("C", tier="T3", summary="y", project="beta"))
    assert worker._check_drift(_NOW) == 0


def test_no_embedder_is_noop(tmp_path: Path) -> None:
    worker, store = _worker(tmp_path, hits=[], embed_ok=False)
    store.insert_crystal(_semantic("C", tier="T3", summary="anything"))
    assert worker._check_drift(_NOW) == 0
    assert store.list_drift_audit() == []


def test_idempotent_no_double_flag(tmp_path: Path) -> None:
    hits = [{"id": "P", "_distance": 0.12}, {"id": "C", "_distance": 0.12}]
    worker, store = _worker(tmp_path, hits=hits)
    store.insert_crystal(_semantic("P", tier="T1", summary="deploys use blue-green"))
    store.insert_crystal(_semantic("C", tier="T3", summary="deploys use red-black"))
    assert worker._check_drift(_NOW) == 1
    assert worker._check_drift(_NOW) == 0          # same (C,P) pair not re-flagged
    assert len(store.list_drift_audit()) == 1


def test_detect_only_no_mutation(tmp_path: Path) -> None:
    hits = [{"id": "P", "_distance": 0.12}, {"id": "C", "_distance": 0.12}]
    worker, store = _worker(tmp_path, hits=hits)
    store.insert_crystal(_semantic("P", tier="T1", summary="deploys use blue-green"))
    store.insert_crystal(_semantic("C", tier="T3", summary="deploys use red-black"))
    worker._check_drift(_NOW)
    # neither crystal superseded / deprecated / tier-changed
    for cid, tier in (("C", "T3"), ("P", "T1")):
        c = store.get_crystal(cid)
        assert c["status"] == "active"
        assert c["trust_tier"] == tier
        assert (c.get("temporal") or {}).get("t_valid_to") is None
