"""W2 Objective 4 — recompute-on-event (access + outcome).

Container-first:
  docker compose run --rm crystalium pytest mcp-server/tests/test_recompute_on_event.py -v

Access bump is a layer fact (record_access, tested in the migration suite). Here
we cover the outcome event: an outcome-only update records outcome_success_score
IN PLACE (no bi-temporal supersession), while a content patch still supersedes.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from crystalium.config import Config
from crystalium.enforcement import Enforcement
from crystalium.server import _handle_update
from crystalium.storage.relational import RelationalStore
from crystalium.trust import Tier

_NOW = datetime(2026, 5, 29, 12, 0, 0, tzinfo=timezone.utc)


def _store(tmp_path: Path) -> RelationalStore:
    return RelationalStore(db_path=tmp_path / "ev.sqlite")


def _crystal(cid: str, layer: str = "episodic") -> dict:
    c = {
        "id": cid,
        "layer": layer,
        "trust_tier": "T1",
        "validation_state": "validated",
        "status": "active",
        "summary": f"summary {cid}",
        "content_ref": "a" * 64 if layer == "episodic" else None,
        "scope": {"project": "p"},
        "provenance": {"source": "verified_agent", "created_at": _NOW.isoformat()},
        "utility": {"access_count": 0, "last_access": _NOW.isoformat(), "importance": 0.0,
                    "novelty_at_write": 0.5},
        "temporal": {"t_valid_from": _NOW.isoformat()},
    }
    return c


def _row_count(store: RelationalStore) -> int:
    import sqlite3
    with sqlite3.connect(str(store.db_path)) as conn:
        return conn.execute("SELECT COUNT(*) FROM crystals").fetchone()[0]


def test_record_outcome_store_method(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.insert_crystal(_crystal("c1"))
    assert store.record_outcome("c1", 0.9, now=_NOW) is True
    assert store.get_crystal("c1")["utility"]["outcome_success_score"] == 0.9
    assert store.record_outcome("missing", 0.5, now=_NOW) is False


def test_outcome_only_update_records_in_place_no_supersession(tmp_path: Path) -> None:
    store = _store(tmp_path)
    cfg = Config(data_dir=tmp_path / "d", rate_limit_per_minute=1000)
    enf = Enforcement(cfg)
    store.insert_crystal(_crystal("c1"))
    before = _row_count(store)

    result = _handle_update(
        {"id": "c1", "patch": {"outcome_success_score": 0.8}, "reason": "task ok"},
        None, None, None, None, enf, store, Tier.T1,
    )
    assert result["status"] == "outcome_recorded"
    assert result["outcome_success_score"] == 0.8
    # In place: no new revision, score persisted on the SAME crystal.
    assert _row_count(store) == before
    got = store.get_crystal("c1")
    assert got["utility"]["outcome_success_score"] == 0.8
    assert got["temporal"].get("superseded_by") is None


def test_content_update_still_supersedes(tmp_path: Path) -> None:
    store = _store(tmp_path)
    cfg = Config(data_dir=tmp_path / "d", rate_limit_per_minute=1000)
    enf = Enforcement(cfg)
    store.insert_crystal(_crystal("c1"))
    before = _row_count(store)

    result = _handle_update(
        {"id": "c1", "patch": {"summary": "new summary"}, "reason": "edit"},
        None, None, None, None, enf, store, Tier.T1,
    )
    assert result["status"] == "updated"
    assert result["supersedes"] == "c1"
    assert _row_count(store) == before + 1  # new revision written
    assert store.get_crystal("c1")["temporal"]["superseded_by"] == result["id"]
