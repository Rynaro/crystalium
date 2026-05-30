"""W6 C1+C2 — security ledgers, validation-state filter, status edits.

Container-first:
  docker compose run --rm crystalium pytest mcp-server/tests/test_security_ledgers.py -v
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from crystalium.storage.relational import RelationalStore

_NOW = datetime(2026, 6, 1, tzinfo=timezone.utc)


def _crystal(cid: str, *, layer="episodic", vstate="unverified", status="active",
             tier="T1", summary="a fact", project="p") -> dict:
    return {
        "id": cid, "layer": layer, "trust_tier": tier,
        "validation_state": vstate, "status": status, "summary": summary,
        "content_ref": "a" * 64, "scope": {"project": project},
        "provenance": {"source": "verified_agent", "author_agent": "x",
                       "created_at": _NOW.isoformat()},
        "utility": {"importance": 0.0}, "temporal": {"t_valid_from": _NOW.isoformat()},
    }


def test_migration_idempotent_creates_w6_tables(tmp_path: Path) -> None:
    db = tmp_path / "m.sqlite"
    RelationalStore(db_path=db)
    RelationalStore(db_path=db)  # second init must be a no-op (CREATE IF NOT EXISTS)
    with sqlite3.connect(str(db)) as conn:
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert {"drift_audit", "quarantine_audit", "conflicts"} <= names


def test_list_by_validation_state_filters(tmp_path: Path) -> None:
    store = RelationalStore(db_path=tmp_path / "v.sqlite")
    store.insert_crystal(_crystal("q1", vstate="quarantined"))
    store.insert_crystal(_crystal("q2", vstate="quarantined"))
    store.insert_crystal(_crystal("u1", vstate="unverified"))
    store.insert_crystal(_crystal("qd", vstate="quarantined", status="deprecated"))
    got = {c["id"] for c in store.list_by_validation_state("quarantined")}
    assert got == {"q1", "q2"}                    # excludes unverified + deprecated


def test_set_validation_state_and_status(tmp_path: Path) -> None:
    store = RelationalStore(db_path=tmp_path / "s.sqlite")
    store.insert_crystal(_crystal("c1", vstate="quarantined"))
    assert store.set_validation_state("c1", "unverified") is True
    assert store.get_crystal("c1")["validation_state"] == "unverified"
    assert store.set_status("c1", "deprecated") is True
    assert store.get_crystal("c1")["status"] == "deprecated"
    assert store.get_crystal("c1") is not None      # soft-delete: row still exists
    assert store.set_status("missing", "deprecated") is False


def test_drift_ledger(tmp_path: Path) -> None:
    store = RelationalStore(db_path=tmp_path / "d.sqlite")
    store.record_drift("cand", "prior", 0.88, "T3", "T1", now=_NOW)
    rows = store.list_drift_audit()
    assert len(rows) == 1
    assert rows[0]["crystal_id"] == "cand" and rows[0]["prior_id"] == "prior"
    assert rows[0]["similarity"] == 0.88 and rows[0]["prior_tier"] == "T1"


def test_quarantine_ledger(tmp_path: Path) -> None:
    store = RelationalStore(db_path=tmp_path / "qa.sqlite")
    store.record_quarantine_review("c1", "accept", "looks legit", actor_tier="T0", now=_NOW)
    store.record_quarantine_review("c2", "reject", "poison", actor_tier="T0", now=_NOW)
    rows = store.list_quarantine_audit()
    assert [r["action"] for r in rows] == ["accept", "reject"]
    assert rows[1]["reason"] == "poison"


def test_conflicts_ledger_records_both_lineages_and_tiers(tmp_path: Path) -> None:
    store = RelationalStore(db_path=tmp_path / "cf.sqlite")
    store.record_conflict("new", "old", winner_summary="X is true", loser_summary="X is false",
                          winner_tier="T2", loser_tier="T1", similarity=0.85,
                          scope={"project": "p"}, now=_NOW)
    rows = store.list_conflicts()
    assert len(rows) == 1
    r = rows[0]
    assert r["winner_id"] == "new" and r["loser_id"] == "old"
    assert r["winner_summary"] == "X is true" and r["loser_summary"] == "X is false"
    # tier inversion (winner T2 less trusted than loser T1) is recorded, auditable
    assert r["winner_tier"] == "T2" and r["loser_tier"] == "T1"
    assert r["scope"] == {"project": "p"}
