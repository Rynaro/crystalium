"""W5 — encoding_context persistence + capture rule.

Container-first:
  docker compose run --rm crystalium pytest mcp-server/tests/test_encoding_context.py -v
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from crystalium.protection import resolve_encoding_context
from crystalium.storage.relational import RelationalStore

_NOW = datetime(2026, 5, 30, tzinfo=timezone.utc)


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


def test_resolve_explicit_context():
    ctx = resolve_encoding_context({"encoding_context": {"task": "auth"}}, {}, {})
    assert ctx == {"task": "auth"}


def test_resolve_derived_context():
    ctx = resolve_encoding_context(
        {}, {"author_agent": "atlas"}, {"project": "acme", "agent_class_visibility": "all"}
    )
    assert ctx == {"project": "acme", "agent_class": "all", "author_agent": "atlas"}


def test_encoding_context_roundtrip(tmp_path: Path) -> None:
    store = RelationalStore(db_path=tmp_path / "ec.sqlite")
    store.insert_crystal(_crystal("c1", encoding_context={"task": "refactor", "branch": "main"}))
    got = store.get_crystal("c1")
    assert got["encoding_context"] == {"task": "refactor", "branch": "main"}
    store.insert_crystal(_crystal("c2"))
    assert store.get_crystal("c2")["encoding_context"] is None


def test_encoding_context_migration_idempotent(tmp_path: Path) -> None:
    db = tmp_path / "legacy.sqlite"
    # Pre-W5 table: has W4 protected/tags but NOT encoding_context.
    with sqlite3.connect(str(db)) as conn:
        conn.execute(
            """CREATE TABLE crystals (
                id TEXT PRIMARY KEY, layer TEXT NOT NULL, trust_tier TEXT NOT NULL,
                validation_state TEXT NOT NULL, status TEXT NOT NULL,
                importance REAL NOT NULL DEFAULT 0.0, summary TEXT NOT NULL,
                content_ref TEXT, embedding_ref TEXT, scope TEXT NOT NULL,
                provenance TEXT NOT NULL, utility TEXT NOT NULL, temporal TEXT NOT NULL,
                memory_dynamics TEXT, protected INTEGER NOT NULL DEFAULT 0, tags TEXT,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"""
        )
        conn.commit()
    RelationalStore(db_path=db)
    cols = {r[1] for r in sqlite3.connect(str(db)).execute("PRAGMA table_info(crystals)").fetchall()}
    assert "encoding_context" in cols
    RelationalStore(db_path=db)  # second open = no-op
    cols2 = {r[1] for r in sqlite3.connect(str(db)).execute("PRAGMA table_info(crystals)").fetchall()}
    assert cols2 == cols
