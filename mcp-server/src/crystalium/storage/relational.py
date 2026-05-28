"""Relational store — SQLite + FTS5 for metadata, BM25 search, promotions, telemetry.

Chosen over an ORM for clarity and sqlite3 stdlib availability.
Uses aiosqlite for async I/O (install: aiosqlite>=0.19) with a synchronous
convenience wrapper for test use and non-async callers.

Tables:
  crystals            — one row per crystal (JSON columns for scope/provenance/utility/temporal)
  crystals_fts        — FTS5 virtual table on summary for BM25 recall
  pending_promotions  — promotion candidates awaiting human confirmation (G5, D8)
  tool_calls          — telemetry sink (P0-7)

Design notes:
- JSON columns for nested objects trade normalisation for flexibility at v0.1.
  The six flat columns (id, layer, trust_tier, validation_state, status, importance)
  are indexed for efficient gate checks.
- FTS5 content table mirrors 'crystals.summary' — kept in sync by triggers.
- aiosqlite is used for the async path; sqlite3 stdlib for the sync test path.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------

_DDL = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS crystals (
    id               TEXT PRIMARY KEY,
    layer            TEXT NOT NULL,
    trust_tier       TEXT NOT NULL,
    validation_state TEXT NOT NULL,
    status           TEXT NOT NULL,
    importance       REAL NOT NULL DEFAULT 0.0,
    summary          TEXT NOT NULL,
    content_ref      TEXT,
    embedding_ref    TEXT,
    scope            TEXT NOT NULL,    -- JSON
    provenance       TEXT NOT NULL,    -- JSON
    utility          TEXT NOT NULL,    -- JSON
    temporal         TEXT NOT NULL,    -- JSON
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS crystals_fts (
    content='crystals',
    content_rowid='rowid'
) USING fts5(summary);

CREATE TRIGGER IF NOT EXISTS crystals_ai AFTER INSERT ON crystals BEGIN
    INSERT INTO crystals_fts(rowid, summary) VALUES (new.rowid, new.summary);
END;

CREATE TRIGGER IF NOT EXISTS crystals_ad AFTER DELETE ON crystals BEGIN
    INSERT INTO crystals_fts(crystals_fts, rowid, summary) VALUES ('delete', old.rowid, old.summary);
END;

CREATE TRIGGER IF NOT EXISTS crystals_au AFTER UPDATE ON crystals BEGIN
    INSERT INTO crystals_fts(crystals_fts, rowid, summary) VALUES ('delete', old.rowid, old.summary);
    INSERT INTO crystals_fts(rowid, summary) VALUES (new.rowid, new.summary);
END;

CREATE INDEX IF NOT EXISTS idx_crystals_layer       ON crystals(layer);
CREATE INDEX IF NOT EXISTS idx_crystals_tier        ON crystals(trust_tier);
CREATE INDEX IF NOT EXISTS idx_crystals_status      ON crystals(status);
CREATE INDEX IF NOT EXISTS idx_crystals_importance  ON crystals(importance);

CREATE TABLE IF NOT EXISTS pending_promotions (
    id          TEXT PRIMARY KEY,
    crystal_id  TEXT NOT NULL REFERENCES crystals(id),
    target_layer TEXT NOT NULL,
    proposed_at TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending'  -- pending | accepted | rejected
);

CREATE TABLE IF NOT EXISTS tool_calls (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    tool        TEXT NOT NULL,
    layer       TEXT,
    tier        TEXT,
    op          TEXT,
    result      TEXT NOT NULL,
    latency_ms  REAL NOT NULL,
    overflow    INTEGER NOT NULL DEFAULT 0,
    error       TEXT,
    ts          TEXT NOT NULL
);
"""


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_json(obj: Any) -> str:
    return json.dumps(obj, default=str)


def _from_json(s: str) -> Any:
    return json.loads(s)


# ---------------------------------------------------------------------------
# RelationalStore (synchronous — uses stdlib sqlite3)
# ---------------------------------------------------------------------------


class RelationalStore:
    """Synchronous SQLite + FTS5 store.

    Async path is intentionally deferred for v0.1: the server uses
    asyncio but storage I/O is fast enough for local single-user operation.
    Wrap calls in asyncio.to_thread() for non-blocking behaviour if needed.

    Args:
        db_path: Path to the SQLite database file. Created if absent.
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            # Execute DDL statement by statement to avoid multi-statement issues
            for stmt in _DDL.strip().split(";"):
                stmt = stmt.strip()
                if stmt:
                    try:
                        conn.execute(stmt)
                    except sqlite3.OperationalError:
                        pass  # Ignore errors from already-existing objects
            conn.commit()

    # ------------------------------------------------------------------
    # Crystal CRUD
    # ------------------------------------------------------------------

    def insert_crystal(self, crystal: dict[str, Any]) -> None:
        """Insert a new crystal row. crystal must have an 'id' key."""
        now = _now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO crystals
                    (id, layer, trust_tier, validation_state, status, importance,
                     summary, content_ref, embedding_ref,
                     scope, provenance, utility, temporal, created_at, updated_at)
                VALUES
                    (:id, :layer, :trust_tier, :validation_state, :status, :importance,
                     :summary, :content_ref, :embedding_ref,
                     :scope, :provenance, :utility, :temporal, :created_at, :updated_at)
                """,
                {
                    "id": crystal["id"],
                    "layer": crystal["layer"],
                    "trust_tier": crystal["trust_tier"],
                    "validation_state": crystal.get("validation_state", "unverified"),
                    "status": crystal.get("status", "candidate"),
                    "importance": crystal.get("utility", {}).get("importance", 0.0),
                    "summary": crystal.get("summary", ""),
                    "content_ref": crystal.get("content_ref"),
                    "embedding_ref": crystal.get("embedding_ref"),
                    "scope": _to_json(crystal.get("scope", {})),
                    "provenance": _to_json(crystal.get("provenance", {})),
                    "utility": _to_json(crystal.get("utility", {})),
                    "temporal": _to_json(crystal.get("temporal", {})),
                    "created_at": crystal.get("provenance", {}).get("created_at", now),
                    "updated_at": now,
                },
            )
            conn.commit()

    def get_crystal(self, crystal_id: str) -> dict[str, Any] | None:
        """Retrieve a crystal by ID. Returns None if not found."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM crystals WHERE id = ?", (crystal_id,)
            ).fetchone()
        if row is None:
            return None
        return self._row_to_dict(row)

    def _row_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        for json_col in ("scope", "provenance", "utility", "temporal"):
            if d.get(json_col):
                d[json_col] = _from_json(d[json_col])
        return d

    # ------------------------------------------------------------------
    # BM25 / FTS5 search
    # ------------------------------------------------------------------

    def bm25_search(
        self,
        query: str,
        layer_filter: str | None = None,
        k: int = 10,
    ) -> list[dict[str, Any]]:
        """Full-text BM25 search over crystal summaries.

        Args:
            query:        FTS5 query string (e.g. 'project conventions').
            layer_filter: Optional layer to restrict results.
            k:            Maximum number of results.

        Returns:
            List of crystal dicts ordered by BM25 rank (best first).
        """
        with self._connect() as conn:
            if layer_filter:
                rows = conn.execute(
                    """
                    SELECT c.*
                    FROM crystals c
                    JOIN crystals_fts fts ON c.rowid = fts.rowid
                    WHERE crystals_fts MATCH ?
                      AND c.layer = ?
                    ORDER BY bm25(crystals_fts)
                    LIMIT ?
                    """,
                    (query, layer_filter, k),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT c.*
                    FROM crystals c
                    JOIN crystals_fts fts ON c.rowid = fts.rowid
                    WHERE crystals_fts MATCH ?
                    ORDER BY bm25(crystals_fts)
                    LIMIT ?
                    """,
                    (query, k),
                ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Bi-temporal supersession (P0-5)
    # ------------------------------------------------------------------

    def mark_superseded(
        self,
        old_id: str,
        new_id: str,
        t_valid_to: datetime,
    ) -> None:
        """Invalidate *old_id* by setting t_valid_to and superseded_by.

        This is the ONLY valid way to "remove" a crystal (P0: never hard-delete).

        Args:
            old_id:      Crystal being superseded.
            new_id:      Crystal that replaces it.
            t_valid_to:  Effective invalidation time (usually now).
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT temporal FROM crystals WHERE id = ?", (old_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"Crystal not found: {old_id!r}")

            temporal = _from_json(row["temporal"])
            temporal["t_valid_to"] = t_valid_to.isoformat()
            temporal["superseded_by"] = new_id

            conn.execute(
                "UPDATE crystals SET temporal = ?, updated_at = ? WHERE id = ?",
                (_to_json(temporal), _now_iso(), old_id),
            )
            conn.commit()

    # ------------------------------------------------------------------
    # Pending promotions (G5)
    # ------------------------------------------------------------------

    def insert_pending_promotion(
        self,
        promotion_id: str,
        crystal_id: str,
        target_layer: str,
        proposed_at: datetime,
    ) -> None:
        """Add a promotion to the pending queue."""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO pending_promotions (id, crystal_id, target_layer, proposed_at, status)
                VALUES (?, ?, ?, ?, 'pending')
                """,
                (promotion_id, crystal_id, target_layer, proposed_at.isoformat()),
            )
            conn.commit()

    def list_pending_promotions(
        self,
        layer_filter: str | None = None,
    ) -> list[dict[str, Any]]:
        """List all pending promotions, optionally filtered by target_layer.

        Args:
            layer_filter: If provided, return only promotions targeting this
                          layer (e.g. "semantic", "procedural"). Resolves the
                          client-side fallback TODO noted in the W4 stub.
        """
        with self._connect() as conn:
            if layer_filter:
                rows = conn.execute(
                    "SELECT * FROM pending_promotions WHERE status = 'pending' AND target_layer = ?",
                    (layer_filter,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM pending_promotions WHERE status = 'pending'"
                ).fetchall()
        return [dict(r) for r in rows]

    def update_promotion_status(self, promotion_id: str, status: str) -> None:
        """Update a promotion to 'accepted' or 'rejected'."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE pending_promotions SET status = ? WHERE id = ?",
                (status, promotion_id),
            )
            conn.commit()

    # ------------------------------------------------------------------
    # Telemetry sink
    # ------------------------------------------------------------------

    def record_tool_call(
        self,
        *,
        tool: str,
        layer: str | None,
        tier: str | None,
        op: str | None,
        result: str,
        latency_ms: float,
        overflow: bool,
        error: str | None,
    ) -> None:
        """Write one telemetry record to tool_calls table."""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO tool_calls (tool, layer, tier, op, result, latency_ms, overflow, error, ts)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (tool, layer, tier, op, result, latency_ms, int(overflow), error, _now_iso()),
            )
            conn.commit()
