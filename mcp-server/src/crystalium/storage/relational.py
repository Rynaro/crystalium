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
    memory_dynamics  TEXT,             -- JSON, nullable (W2: stability/retrievability/difficulty/evb/...)
    protected        INTEGER NOT NULL DEFAULT 0,  -- W4 Ricoeur-protected: exempt from decay/eviction
    tags             TEXT,             -- JSON array, nullable (W4)
    encoding_context TEXT,             -- JSON, nullable (W5 encoding-specificity context)
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS crystals_fts USING fts5(summary, content='crystals', content_rowid='rowid');

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
    crystal_id  TEXT NOT NULL,
    target_layer TEXT NOT NULL,
    proposed_at TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending'  -- pending | accepted | rejected
);

CREATE TABLE IF NOT EXISTS promotions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    crystal_id  TEXT NOT NULL,
    gate        TEXT NOT NULL,    -- semantic | procedural
    ts          TEXT NOT NULL
);

-- W4 right-to-be-forgotten audit ledger (append-only). Every hard-tombstone
-- writes a row here BEFORE the delete; this table is itself protected from decay.
CREATE TABLE IF NOT EXISTS forget_audit (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    crystal_id  TEXT NOT NULL,
    actor_tier  TEXT NOT NULL,
    reason      TEXT NOT NULL,
    layer       TEXT,
    ts          TEXT NOT NULL
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
        # executescript handles multi-statement DDL including CREATE TRIGGER ... BEGIN...END
        # blocks which contain internal semicolons that would break a naive split(";") approach.
        # It issues an implicit COMMIT before running, which is fine for DDL-only setup.
        conn = self._connect()
        try:
            conn.executescript(_DDL)
            self._migrate(conn)
        finally:
            conn.close()

    def _migrate(self, conn: sqlite3.Connection) -> None:
        """Forward, idempotent migrations for pre-W2 databases.

        CREATE TABLE IF NOT EXISTS does not alter an existing table, so the
        memory_dynamics column (W2) is added here for DBs created before it.
        ALTER TABLE ADD COLUMN errors if the column exists, so we guard on
        PRAGMA table_info — making a second run a no-op (additive, NULL default).
        """
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(crystals)").fetchall()}
        if "memory_dynamics" not in cols:
            conn.execute("ALTER TABLE crystals ADD COLUMN memory_dynamics TEXT")
            conn.commit()
        # W4: protected/tags (added to crystal.v1.json in W1, persisted now).
        if "protected" not in cols:
            conn.execute("ALTER TABLE crystals ADD COLUMN protected INTEGER NOT NULL DEFAULT 0")
            conn.commit()
        if "tags" not in cols:
            conn.execute("ALTER TABLE crystals ADD COLUMN tags TEXT")
            conn.commit()
        # W5: encoding_context (added to crystal.v1.json in W1, persisted now).
        if "encoding_context" not in cols:
            conn.execute("ALTER TABLE crystals ADD COLUMN encoding_context TEXT")
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
                     scope, provenance, utility, temporal, memory_dynamics,
                     protected, tags, encoding_context, created_at, updated_at)
                VALUES
                    (:id, :layer, :trust_tier, :validation_state, :status, :importance,
                     :summary, :content_ref, :embedding_ref,
                     :scope, :provenance, :utility, :temporal, :memory_dynamics,
                     :protected, :tags, :encoding_context, :created_at, :updated_at)
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
                    "memory_dynamics": (
                        _to_json(crystal["memory_dynamics"])
                        if crystal.get("memory_dynamics") is not None
                        else None
                    ),
                    "protected": 1 if crystal.get("protected") else 0,
                    "tags": _to_json(crystal["tags"]) if crystal.get("tags") else None,
                    "encoding_context": (
                        _to_json(crystal["encoding_context"])
                        if crystal.get("encoding_context") is not None
                        else None
                    ),
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
        for json_col in ("scope", "provenance", "utility", "temporal", "memory_dynamics",
                         "tags", "encoding_context"):
            if d.get(json_col):
                d[json_col] = _from_json(d[json_col])
        if "protected" in d:
            d["protected"] = bool(d["protected"])
        return d

    def update_dynamics(self, crystal_id: str, dynamics: dict[str, Any]) -> bool:
        """Merge *dynamics* into the crystal's memory_dynamics JSON (W2 evb write-back).

        Returns False if the crystal does not exist. Existing keys are overwritten;
        other dynamics keys (W4 FSRS fields) are preserved.
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT memory_dynamics FROM crystals WHERE id = ?", (crystal_id,)
            ).fetchone()
            if row is None:
                return False
            current = _from_json(row["memory_dynamics"]) if row["memory_dynamics"] else {}
            current.update(dynamics)
            conn.execute(
                "UPDATE crystals SET memory_dynamics = ?, updated_at = ? WHERE id = ?",
                (_to_json(current), _now_iso(), crystal_id),
            )
            conn.commit()
        return True

    def record_access(self, crystal_id: str, *, now: datetime) -> bool:
        """Bump access_count and set last_access in the crystal's utility JSON.

        The access event for EVB's Need term (W2). Returns False if not found.
        Importance/evb recompute is the caller's responsibility (kept out of the
        store so storage stays free of scoring logic).
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT utility FROM crystals WHERE id = ?", (crystal_id,)
            ).fetchone()
            if row is None:
                return False
            util = _from_json(row["utility"]) if row["utility"] else {}
            util["access_count"] = int(util.get("access_count", 0)) + 1
            util["last_access"] = now.isoformat()
            conn.execute(
                "UPDATE crystals SET utility = ?, updated_at = ? WHERE id = ?",
                (_to_json(util), _now_iso(), crystal_id),
            )
            conn.commit()
        return True

    def record_outcome(self, crystal_id: str, score: float, *, now: datetime) -> bool:
        """Set utility.outcome_success_score in-place (W2 outcome event).

        An outcome is an in-place fact about an existing crystal — NOT a
        bi-temporal supersession. Feeds EVB's Gain term + promotion precision.
        EVB is refreshed on the next recall/Dream recompute. Returns False if
        the crystal does not exist.
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT utility FROM crystals WHERE id = ?", (crystal_id,)
            ).fetchone()
            if row is None:
                return False
            util = _from_json(row["utility"]) if row["utility"] else {}
            util["outcome_success_score"] = float(score)
            util["last_access"] = now.isoformat()
            conn.execute(
                "UPDATE crystals SET utility = ?, updated_at = ? WHERE id = ?",
                (_to_json(util), _now_iso(), crystal_id),
            )
            conn.commit()
        return True

    # ------------------------------------------------------------------
    # W2 instrumentation — promotions ledger + dynamics snapshots
    # ------------------------------------------------------------------

    def record_promotion(self, crystal_id: str, gate: str, *, now: datetime) -> None:
        """Append a promotion-ledger row (every auto-admit). Net-new in W2."""
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO promotions (crystal_id, gate, ts) VALUES (?, ?, ?)",
                (crystal_id, gate, now.isoformat()),
            )
            conn.commit()

    def list_promotions(self) -> list[dict[str, Any]]:
        """All promotion-ledger rows (crystal_id, gate, ts)."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT crystal_id, gate, ts FROM promotions ORDER BY id"
            ).fetchall()
        return [dict(r) for r in rows]

    def list_crystals_with_dynamics(self) -> list[dict[str, Any]]:
        """Per-crystal snapshot for the W2 bench axes: id, layer, status,
        access_count, outcome_success_score, and persisted evb."""
        out: list[dict[str, Any]] = []
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, layer, status, utility, memory_dynamics FROM crystals"
            ).fetchall()
        for r in rows:
            util = _from_json(r["utility"]) if r["utility"] else {}
            md = _from_json(r["memory_dynamics"]) if r["memory_dynamics"] else {}
            out.append({
                "id": r["id"],
                "layer": r["layer"],
                "status": r["status"],
                "access_count": int(util.get("access_count", 0)),
                "outcome_success_score": util.get("outcome_success_score"),
                "evb": md.get("evb") if isinstance(md, dict) else None,
            })
        return out

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
    # W4 right-to-be-forgotten — the ONE sanctioned hard-delete
    # ------------------------------------------------------------------

    def tombstone(self, crystal_id: str, reason: str, *, actor_tier: str, now: datetime) -> bool:
        """Hard-delete a crystal — the SOLE exception to never-hard-delete (P0-5).

        Writes the forget_audit row BEFORE the delete, in the same transaction, so
        an erased crystal always leaves an audit trail. Returns False if the
        crystal does not exist (no audit row written). This is the only
        `DELETE FROM crystals` in the codebase; it is reached only via the
        operator-gated `forget` enforcement op (see __main__ `forget` CLI).
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT layer FROM crystals WHERE id = ?", (crystal_id,)
            ).fetchone()
            if row is None:
                return False
            conn.execute(
                "INSERT INTO forget_audit (crystal_id, actor_tier, reason, layer, ts) "
                "VALUES (?, ?, ?, ?, ?)",
                (crystal_id, actor_tier, reason, row["layer"], now.isoformat()),
            )
            conn.execute("DELETE FROM crystals WHERE id = ?", (crystal_id,))
            conn.commit()
        return True

    def recent_crystal_ids(self, project: str, *, exclude_id: str, limit: int = 5) -> list[str]:
        """Most-recent active crystal ids in *project* (W5 co-occurrence linking).

        Bounded to *limit* to avoid combinatorial edge blow-up; excludes
        exclude_id. Returns [] on any error / no project.
        """
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT id FROM crystals "
                    "WHERE json_extract(scope, '$.project') = ? AND status='active' AND id != ? "
                    "ORDER BY created_at DESC LIMIT ?",
                    (project, exclude_id, limit),
                ).fetchall()
            return [r[0] for r in rows]
        except Exception:
            return []

    def list_forget_audit(self) -> list[dict[str, Any]]:
        """All right-to-be-forgotten audit rows (append-only)."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT crystal_id, actor_tier, reason, layer, ts FROM forget_audit ORDER BY id"
            ).fetchall()
        return [dict(r) for r in rows]

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
