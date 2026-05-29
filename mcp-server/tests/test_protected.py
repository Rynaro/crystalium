"""W4 Objective 4 — protected/tags persistence + auto-protect rule.

Container-first:
  docker compose run --rm crystalium pytest mcp-server/tests/test_protected.py -v
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from crystalium.protection import resolve_protection
from crystalium.storage.relational import RelationalStore

_NOW = datetime(2026, 5, 29, tzinfo=timezone.utc)


# --- auto-protect rule (pure) ----------------------------------------------

def test_resolve_protection_explicit():
    p, tags = resolve_protection({"protected": True}, "verified_agent")
    assert p is True


def test_resolve_protection_human_provenance():
    p, _ = resolve_protection({}, "human")
    assert p is True


def test_resolve_protection_decision_tag():
    p, tags = resolve_protection({"tags": ["[DECISION]", "auth"]}, "verified_agent")
    assert p is True
    assert tags == ["[DECISION]", "auth"]


def test_resolve_protection_default_false():
    p, tags = resolve_protection({"summary": "routine fact"}, "verified_agent")
    assert p is False
    assert tags == []


# --- storage round-trip -----------------------------------------------------

def _crystal(cid: str, **extra) -> dict:
    c = {
        "id": cid, "layer": "semantic", "trust_tier": "T1",
        "validation_state": "validated", "status": "active", "summary": f"s {cid}",
        "scope": {"project": "p"},
        "provenance": {"source": "verified_agent", "created_at": _NOW.isoformat()},
        "utility": {"access_count": 0, "last_access": _NOW.isoformat(), "importance": 0.0,
                    "novelty_at_write": 0.5},
        "temporal": {"t_valid_from": _NOW.isoformat()},
    }
    c.update(extra)
    return c


def test_protected_tags_roundtrip(tmp_path: Path) -> None:
    store = RelationalStore(db_path=tmp_path / "p.sqlite")
    store.insert_crystal(_crystal("c1", protected=True, tags=["[DECISION]"]))
    got = store.get_crystal("c1")
    assert got["protected"] is True
    assert got["tags"] == ["[DECISION]"]

    store.insert_crystal(_crystal("c2"))
    g2 = store.get_crystal("c2")
    assert g2["protected"] is False
    assert g2["tags"] is None


def test_migration_adds_protected_tags_idempotent(tmp_path: Path) -> None:
    db = tmp_path / "legacy.sqlite"
    # Pre-W4 table: has memory_dynamics (W2) but NOT protected/tags.
    with sqlite3.connect(str(db)) as conn:
        conn.execute(
            """CREATE TABLE crystals (
                id TEXT PRIMARY KEY, layer TEXT NOT NULL, trust_tier TEXT NOT NULL,
                validation_state TEXT NOT NULL, status TEXT NOT NULL,
                importance REAL NOT NULL DEFAULT 0.0, summary TEXT NOT NULL,
                content_ref TEXT, embedding_ref TEXT, scope TEXT NOT NULL,
                provenance TEXT NOT NULL, utility TEXT NOT NULL, temporal TEXT NOT NULL,
                memory_dynamics TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"""
        )
        conn.execute(
            "INSERT INTO crystals VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("old", "semantic", "T1", "validated", "active", 0.0, "legacy", None, None,
             "{}", "{}", "{}", "{}", None, _NOW.isoformat(), _NOW.isoformat()),
        )
        conn.commit()

    store = RelationalStore(db_path=db)
    cols = {r[1] for r in sqlite3.connect(str(db)).execute("PRAGMA table_info(crystals)").fetchall()}
    assert {"protected", "tags"} <= cols
    old = store.get_crystal("old")
    assert old["protected"] is False and old["tags"] is None  # legacy default

    # second open = no-op (idempotent)
    RelationalStore(db_path=db)
    cols2 = {r[1] for r in sqlite3.connect(str(db)).execute("PRAGMA table_info(crystals)").fetchall()}
    assert cols2 == cols
