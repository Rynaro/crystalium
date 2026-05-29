"""W2 Objective 2 — memory_dynamics column + forward migration + store methods.

Container-first:
  docker compose run --rm crystalium pytest mcp-server/tests/test_migration_memory_dynamics.py -v
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from crystalium.storage.relational import RelationalStore

_NOW = datetime(2026, 5, 29, 12, 0, 0, tzinfo=timezone.utc)


def _crystal(cid: str, **md) -> dict:
    c = {
        "id": cid,
        "layer": "semantic",
        "trust_tier": "T1",
        "validation_state": "validated",
        "status": "active",
        "summary": f"summary {cid}",
        "scope": {"project": "p"},
        "provenance": {"source": "verified_agent", "created_at": _NOW.isoformat()},
        "utility": {"access_count": 0, "last_access": _NOW.isoformat(), "importance": 0.0,
                    "novelty_at_write": 0.5},
        "temporal": {"t_valid_from": _NOW.isoformat()},
    }
    if md:
        c["memory_dynamics"] = md
    return c


def test_fresh_db_has_memory_dynamics_column(tmp_path: Path) -> None:
    store = RelationalStore(db_path=tmp_path / "fresh.sqlite")
    with sqlite3.connect(str(store.db_path)) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(crystals)").fetchall()}
    assert "memory_dynamics" in cols


def test_insert_and_read_memory_dynamics_roundtrip(tmp_path: Path) -> None:
    store = RelationalStore(db_path=tmp_path / "rt.sqlite")
    store.insert_crystal(_crystal("c1", evb=0.42, stability=None))
    got = store.get_crystal("c1")
    assert got is not None
    assert got["memory_dynamics"]["evb"] == 0.42

    # crystal without memory_dynamics → NULL → reads as None
    store.insert_crystal(_crystal("c2"))
    assert store.get_crystal("c2")["memory_dynamics"] is None


def test_update_dynamics_merges(tmp_path: Path) -> None:
    store = RelationalStore(db_path=tmp_path / "upd.sqlite")
    store.insert_crystal(_crystal("c1", evb=0.1, stability=3.0))
    assert store.update_dynamics("c1", {"evb": 0.9}) is True
    md = store.get_crystal("c1")["memory_dynamics"]
    assert md["evb"] == 0.9        # overwritten
    assert md["stability"] == 3.0  # preserved
    assert store.update_dynamics("missing", {"evb": 1.0}) is False


def test_record_access_bumps_count_and_last_access(tmp_path: Path) -> None:
    store = RelationalStore(db_path=tmp_path / "acc.sqlite")
    store.insert_crystal(_crystal("c1"))
    later = datetime(2026, 6, 1, tzinfo=timezone.utc)
    assert store.record_access("c1", now=later) is True
    util = store.get_crystal("c1")["utility"]
    assert util["access_count"] == 1
    assert util["last_access"] == later.isoformat()
    assert store.record_access("missing", now=later) is False


def test_forward_migration_idempotent_on_pre_w2_db(tmp_path: Path) -> None:
    """A DB created WITHOUT memory_dynamics is migrated additively; re-running is a no-op."""
    db = tmp_path / "legacy.sqlite"
    # Simulate a pre-W2 crystals table (no memory_dynamics column) + a legacy row.
    with sqlite3.connect(str(db)) as conn:
        conn.execute(
            """
            CREATE TABLE crystals (
                id TEXT PRIMARY KEY, layer TEXT NOT NULL, trust_tier TEXT NOT NULL,
                validation_state TEXT NOT NULL, status TEXT NOT NULL,
                importance REAL NOT NULL DEFAULT 0.0, summary TEXT NOT NULL,
                content_ref TEXT, embedding_ref TEXT, scope TEXT NOT NULL,
                provenance TEXT NOT NULL, utility TEXT NOT NULL, temporal TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "INSERT INTO crystals VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("old1", "semantic", "T1", "validated", "active", 0.0, "legacy",
             None, None, "{}", "{}", "{}", "{}", _NOW.isoformat(), _NOW.isoformat()),
        )
        conn.commit()

    # First open runs the migration.
    store = RelationalStore(db_path=db)
    with sqlite3.connect(str(db)) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(crystals)").fetchall()}
    assert "memory_dynamics" in cols
    # Legacy row survives, reads memory_dynamics as None.
    got = store.get_crystal("old1")
    assert got is not None and got["memory_dynamics"] is None

    # Second open (migration runs again) must not raise and column count is stable.
    RelationalStore(db_path=db)
    with sqlite3.connect(str(db)) as conn:
        cols2 = {r[1] for r in conn.execute("PRAGMA table_info(crystals)").fetchall()}
    assert cols2 == cols  # idempotent: no duplicate/extra column
