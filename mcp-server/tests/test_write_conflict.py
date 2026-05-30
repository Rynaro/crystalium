"""W6 C7 — multi-agent write-conflict detection (LWW + conflicts ledger).

Container-first:
  docker compose run --rm crystalium pytest mcp-server/tests/test_write_conflict.py -v
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from crystalium.aetheryte.redact import Redactor
from crystalium.config import Config
from crystalium.enforcement import Enforcement
from crystalium.importance import importance_score
from crystalium.layers.semantic import SemanticLayer
from crystalium.storage.relational import RelationalStore
from crystalium.trust import Tier

_NOW = datetime(2026, 6, 1, tzinfo=timezone.utc)


def _prior(cid: str, summary: str, *, tier="T1", project="p") -> dict:
    return {
        "id": cid, "layer": "semantic", "trust_tier": tier,
        "validation_state": "validated", "status": "active", "summary": summary,
        "content_ref": "a" * 64, "scope": {"project": project},
        "provenance": {"source": "verified_agent", "created_at": _NOW.isoformat()},
        "utility": {"importance": 0.0}, "temporal": {"t_valid_from": _NOW.isoformat()},
    }


def _layer(tmp_path: Path, *, conflict: bool, dist: float):
    cfg = Config(data_dir=tmp_path / f"wc-{conflict}", rate_limit_per_minute=100000)
    store = RelationalStore(db_path=cfg.sqlite_path)
    store.insert_crystal(_prior("old", "deploys use blue-green"))
    vec = MagicMock()
    vec.embed.return_value = [0.1, 0.2, 0.3]
    vec.dense_search.return_value = [{"id": "old", "_distance": dist}]
    blob = MagicMock(); blob.put.return_value = "b" * 64
    gate = MagicMock()
    gate.propose_semantic.return_value = SimpleNamespace(
        decision="admit", promotion_id=None, reason="ok")
    layer = SemanticLayer(
        blob_store=blob, relational=store, vector_store=vec, graph_store=None,
        enforcement=Enforcement(cfg), gate=gate, redactor=Redactor(config=cfg),
        importance_fn=importance_score, dedup_merge=True, sep_threshold=0.92,
        write_conflict_detect=conflict, conflict_tau_lo=0.80,
    )
    return layer, store


def _commit_divergent(layer):
    return layer.commit(
        payload={"summary": "deploys use red-black", "scope": {"project": "p"}},
        provenance={"source": "verified_agent", "author_agent": "agent-b"},
        caller_tier=Tier.T1,
    )


def test_conflict_lww_supersedes_and_records(tmp_path: Path) -> None:
    # sim = 1 - 0.15 = 0.85, in [0.80, 0.92): same-subject, divergent -> conflict.
    layer, store = _layer(tmp_path, conflict=True, dist=0.15)
    res = _commit_divergent(layer)
    assert res["status"] == "committed"
    assert res["conflict_superseded"] == "old"
    # prior bi-temporally superseded (last-write-wins), not deleted
    old = store.get_crystal("old")
    assert old is not None
    assert old["temporal"]["t_valid_to"] is not None
    assert old["temporal"]["superseded_by"] == res["id"]
    # both lineages + both tiers recorded
    conflicts = store.list_conflicts()
    assert len(conflicts) == 1
    cf = conflicts[0]
    assert cf["winner_id"] == res["id"] and cf["loser_id"] == "old"
    assert cf["winner_summary"] == "deploys use red-black"
    assert cf["loser_summary"] == "deploys use blue-green"
    assert cf["winner_tier"] == "T1" and cf["loser_tier"] == "T1"


def test_off_is_byte_identical_no_conflict(tmp_path: Path) -> None:
    layer, store = _layer(tmp_path, conflict=False, dist=0.15)
    res = _commit_divergent(layer)
    assert res["status"] == "committed"
    assert "conflict_superseded" not in res
    assert store.get_crystal("old")["temporal"].get("t_valid_to") is None  # prior untouched
    assert store.list_conflicts() == []


def test_near_duplicate_not_a_conflict(tmp_path: Path) -> None:
    # sim = 0.97 >= sep_threshold -> dedup territory (corroboration), not a conflict.
    layer, store = _layer(tmp_path, conflict=True, dist=0.03)
    res = _commit_divergent(layer)
    # dedup-merge absorbs it as corroboration (W5 behavior); no conflict recorded
    assert res["status"] == "merged"
    assert store.list_conflicts() == []
