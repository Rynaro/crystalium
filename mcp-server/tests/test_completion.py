"""W5 Objective 1/5 — decaying multi-hop walk + co-occurrence linking substrate.

Container-first:
  docker compose run --rm crystalium pytest mcp-server/tests/test_completion.py -v
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from crystalium.storage.graph import GraphStore
from crystalium.storage.relational import RelationalStore

_NOW = datetime(2026, 5, 30, tzinfo=timezone.utc)


def _chain(g: GraphStore, ids: list[str]) -> None:
    for n in ids:
        g.add_node(n, "semantic")
    for a, b in zip(ids, ids[1:]):
        g.add_edge(a, b, "LINKS_TO")


def test_decaying_walk_hop_decay(tmp_graph_store: GraphStore) -> None:
    _chain(tmp_graph_store, ["a", "b", "c", "d"])  # a->b->c->d
    walked = tmp_graph_store.decaying_walk(["a"], max_hops=2, decay=0.5)
    assert walked.get("b") == pytest.approx(0.5)   # hop 1
    assert walked.get("c") == pytest.approx(0.25)  # hop 2
    assert "d" not in walked                       # hop 3 > max_hops
    assert "a" not in walked                       # seed excluded


def test_decaying_walk_edgeless(tmp_graph_store: GraphStore) -> None:
    tmp_graph_store.add_node("solo", "semantic")
    assert tmp_graph_store.decaying_walk(["solo"], max_hops=3, decay=0.5) == {}


def test_decaying_walk_empty_seeds(tmp_graph_store: GraphStore) -> None:
    assert tmp_graph_store.decaying_walk([], max_hops=2, decay=0.5) == {}


def test_null_graph_decaying_walk_noop() -> None:
    from crystalium.server import _NullGraphStore

    assert _NullGraphStore().decaying_walk(["x"], max_hops=2, decay=0.5) == {}


def _crystal(cid: str, project: str) -> dict:
    return {
        "id": cid, "layer": "episodic", "trust_tier": "T1",
        "validation_state": "validated", "status": "active", "summary": f"s {cid}",
        "content_ref": "a" * 64,
        "scope": {"project": project},
        "provenance": {"source": "verified_agent", "created_at": _NOW.isoformat()},
        "utility": {"access_count": 0, "last_access": _NOW.isoformat(), "importance": 0.0,
                    "novelty_at_write": 0.5},
        "temporal": {"t_valid_from": _NOW.isoformat()},
    }


def test_recent_crystal_ids_bounded_and_scoped(tmp_path: Path) -> None:
    store = RelationalStore(db_path=tmp_path / "r.sqlite")
    for i in range(4):
        store.insert_crystal(_crystal(f"p-{i}", "proj"))
    store.insert_crystal(_crystal("other", "elsewhere"))
    ids = store.recent_crystal_ids("proj", exclude_id="p-3", limit=2)
    assert len(ids) == 2                 # bounded
    assert "p-3" not in ids              # self excluded
    assert "other" not in ids            # other project excluded
    assert all(i.startswith("p-") for i in ids)
