"""W4 Objective 5 — right-to-be-forgotten (the ONE sanctioned hard-delete).

Container-first:
  docker compose run --rm crystalium pytest mcp-server/tests/test_rtbf.py -v

Asserts: tombstone deletes + audits, missing-id is a no-op, the forget op is
T0-only, the CLI erases through the chokepoint, and soft-delete paths are
untouched (TestHardDeleteForbidden / CAN-4 remain green elsewhere).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from click.testing import CliRunner

from crystalium.config import Config
from crystalium.enforcement import Enforcement
from crystalium.storage.relational import RelationalStore
from crystalium.trust import Tier

_NOW = datetime(2026, 6, 1, tzinfo=timezone.utc)


def _crystal(cid: str) -> dict:
    return {
        "id": cid, "layer": "semantic", "trust_tier": "T1",
        "validation_state": "validated", "status": "active", "summary": f"s {cid}",
        "scope": {"project": "p"},
        "provenance": {"source": "verified_agent", "created_at": _NOW.isoformat()},
        "utility": {"access_count": 0, "last_access": _NOW.isoformat(), "importance": 0.0,
                    "novelty_at_write": 0.5},
        "temporal": {"t_valid_from": _NOW.isoformat()},
    }


def test_tombstone_deletes_and_audits(tmp_path: Path) -> None:
    store = RelationalStore(db_path=tmp_path / "t.sqlite")
    store.insert_crystal(_crystal("c1"))
    assert store.tombstone("c1", "gdpr erasure", actor_tier="T0", now=_NOW) is True
    assert store.get_crystal("c1") is None                 # hard-deleted
    audit = store.list_forget_audit()
    assert len(audit) == 1
    assert audit[0]["crystal_id"] == "c1"
    assert audit[0]["reason"] == "gdpr erasure"
    assert audit[0]["actor_tier"] == "T0"


def test_tombstone_missing_is_noop(tmp_path: Path) -> None:
    store = RelationalStore(db_path=tmp_path / "t2.sqlite")
    assert store.tombstone("nope", "x", actor_tier="T0", now=_NOW) is False
    assert store.list_forget_audit() == []                 # no audit row for a no-op


def test_blob_tombstone(tmp_path: Path) -> None:
    from crystalium.storage.blob import BlobStore

    bs = BlobStore(root=tmp_path / "blobs")
    ref = bs.put(b"secret bytes")
    assert bs.exists(ref) is True
    assert bs.tombstone(ref) is True
    assert bs.exists(ref) is False
    assert bs.tombstone(ref) is False                      # already gone
    # delete() stays hard-blocked (P0) — tombstone is the only exception
    with pytest.raises(NotImplementedError):
        bs.delete(ref)


def test_forget_op_is_t0_only(tmp_path: Path) -> None:
    enf = Enforcement(Config(data_dir=tmp_path / "d"))
    enf.assert_tier_allowed("crystalium.forget", "semantic", Tier.T0, "forget")  # allowed
    for tier in (Tier.T1, Tier.T2, Tier.T3):
        with pytest.raises(Exception):
            enf.assert_tier_allowed("crystalium.forget", "semantic", tier, "forget")


def test_cli_forget_erases_through_chokepoint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CRYSTALIUM_DATA_DIR", str(tmp_path / "data"))
    cfg = Config.from_env()
    store = RelationalStore(db_path=cfg.sqlite_path)
    store.insert_crystal(_crystal("victim"))

    from crystalium.__main__ import cli

    result = CliRunner().invoke(
        cli, ["forget", "victim", "--reason", "operator request", "--yes"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    assert RelationalStore(db_path=cfg.sqlite_path).get_crystal("victim") is None
    assert RelationalStore(db_path=cfg.sqlite_path).list_forget_audit()[0]["reason"] == "operator request"


# --- Battle-test fix (HIGH): RTBF must erase the dense embedding + graph node too ---

def test_vector_delete_removes_embedding(tmp_path: Path) -> None:
    from crystalium.storage.vector import VectorStore

    vs = VectorStore(lance_dir=tmp_path / "vectors.lance")
    vs.upsert("keep", [0.1, 0.2, 0.3], metadata={"layer": "semantic"})
    vs.upsert("victim", [0.1, 0.2, 0.31], metadata={"layer": "semantic"})
    vs.delete("victim")
    ids = {r["id"] for r in vs.dense_search([0.1, 0.2, 0.3], k=10)}
    assert "victim" not in ids                              # embedding physically gone
    assert "keep" in ids
    vs.delete("victim")                                     # idempotent no-op


def test_graph_delete_node_removes_node(tmp_path: Path) -> None:
    from crystalium.storage.graph import GraphStore

    gs = GraphStore(kuzu_dir=tmp_path / "graph.kuzu")
    gs.add_node("victim", "semantic")
    gs.add_node("keep", "semantic")
    assert gs.node_count() == 2
    gs.delete_node("victim")
    assert gs.node_count() == 1                             # node physically gone
    gs.delete_node("victim")                                # idempotent no-op
    assert gs.node_count() == 1
