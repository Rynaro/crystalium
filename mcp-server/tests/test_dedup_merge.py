"""W5 Objective 3 — pattern separation at write (dedup-merge).

Container-first:
  docker compose run --rm crystalium pytest mcp-server/tests/test_dedup_merge.py -v
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

from crystalium.aetheryte.redact import Redactor
from crystalium.config import Config
from crystalium.enforcement import Enforcement
from crystalium.importance import importance_score
from crystalium.layers.episodic import EpisodicLayer
from crystalium.storage.relational import RelationalStore
from crystalium.trust import Tier

_NOW = datetime(2026, 5, 30, tzinfo=timezone.utc)


def _existing(cid: str) -> dict:
    return {
        "id": cid, "layer": "episodic", "trust_tier": "T1",
        "validation_state": "unverified", "status": "active", "summary": "auth uses argon2",
        "content_ref": "a" * 64, "scope": {"project": "p"},
        "provenance": {"source": "verified_agent", "author_agent": "agent-a",
                       "created_at": _NOW.isoformat()},
        "utility": {"access_count": 0, "last_access": _NOW.isoformat(), "importance": 0.0,
                    "novelty_at_write": 0.5},
        "temporal": {"t_valid_from": _NOW.isoformat()},
    }


def test_merge_provenance_unions_and_bumps(tmp_path: Path) -> None:
    store = RelationalStore(db_path=tmp_path / "m.sqlite")
    store.insert_crystal(_existing("c1"))
    assert store.merge_provenance("c1", {"author_agent": "agent-b", "source": "human"}) is True
    prov = store.get_crystal("c1")["provenance"]
    assert set(prov["merged_authors"]) == {"agent-a", "agent-b"}
    assert set(prov["merged_sources"]) == {"verified_agent", "human"}
    assert prov["corroboration"] == 2
    assert store.merge_provenance("missing", {"source": "human"}) is False


def _layer(tmp_path: Path, *, dedup_merge: bool, vec_hit_distance: float | None):
    cfg = Config(data_dir=tmp_path / f"d-{dedup_merge}-{vec_hit_distance}", rate_limit_per_minute=10000)
    store = RelationalStore(db_path=cfg.sqlite_path)
    store.insert_crystal(_existing("existing"))
    vec = MagicMock()
    vec.embed.return_value = [0.1, 0.2, 0.3]
    vec.dense_search.return_value = (
        [] if vec_hit_distance is None else [{"id": "existing", "_distance": vec_hit_distance}]
    )
    blob = MagicMock()
    blob.put.return_value = "b" * 64  # content_ref must be a real string for insert
    layer = EpisodicLayer(
        blob_store=blob, relational=store, vector_store=vec, graph_store=None,
        enforcement=Enforcement(cfg), redactor=Redactor(config=cfg),
        importance_fn=importance_score, dedup_merge=dedup_merge, sep_threshold=0.92,
    )
    return layer, store


def _rowcount(store: RelationalStore) -> int:
    import sqlite3
    with sqlite3.connect(str(store.db_path)) as conn:
        return conn.execute("SELECT COUNT(*) FROM crystals").fetchone()[0]


def test_dedup_merge_merges_near_duplicate(tmp_path: Path) -> None:
    # cosine = 1 - 0.05 = 0.95 > 0.92 -> merge, no new row.
    layer, store = _layer(tmp_path, dedup_merge=True, vec_hit_distance=0.05)
    before = _rowcount(store)
    res = layer.commit(payload={"summary": "auth uses argon2", "scope": {"project": "p"}},
                       provenance={"source": "verified_agent", "author_agent": "agent-b"},
                       caller_tier=Tier.T1)
    assert res["status"] == "merged"
    assert res["id"] == "existing"
    assert _rowcount(store) == before                       # no new row
    assert store.get_crystal("existing")["provenance"]["corroboration"] == 2


def test_dedup_below_threshold_inserts(tmp_path: Path) -> None:
    # cosine = 1 - 0.5 = 0.5 < 0.92 -> normal insert.
    layer, store = _layer(tmp_path, dedup_merge=True, vec_hit_distance=0.5)
    before = _rowcount(store)
    res = layer.commit(payload={"summary": "totally different note", "scope": {"project": "p"}},
                       provenance={"source": "verified_agent"}, caller_tier=Tier.T1)
    assert res["status"] == "committed"
    assert _rowcount(store) == before + 1                   # new row


def test_dedup_off_blind_appends(tmp_path: Path) -> None:
    layer, store = _layer(tmp_path, dedup_merge=False, vec_hit_distance=0.01)
    before = _rowcount(store)
    res = layer.commit(payload={"summary": "auth uses argon2", "scope": {"project": "p"}},
                       provenance={"source": "verified_agent"}, caller_tier=Tier.T1)
    assert res["status"] == "committed"
    assert _rowcount(store) == before + 1                   # blind append despite near-dup
