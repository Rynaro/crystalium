"""Battle-test regression guards — the main-workflow bugs found by the TRANCE scatter.

Container-first:
  docker compose run --rm crystalium pytest mcp-server/tests/test_battle_fixes.py -v
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from crystalium.config import Config
from crystalium.enforcement import CrystaliumEnforcementError
from crystalium.schemas import Scope
from crystalium.storage.relational import RelationalStore
from crystalium.trust import Tier

_NOW = datetime(2026, 6, 1, tzinfo=timezone.utc)


def _crystal(cid: str, summary: str, *, project="p") -> dict:
    return {
        "id": cid, "layer": "episodic", "trust_tier": "T1",
        "validation_state": "unverified", "status": "active", "summary": summary,
        "content_ref": "a" * 64, "scope": {"project": project},
        "provenance": {"source": "verified_agent", "created_at": _NOW.isoformat()},
        "utility": {"importance": 0.0}, "temporal": {"t_valid_from": _NOW.isoformat()},
    }


# --- Fix #1: FTS5 query sanitization (recall no longer crashes on special chars) ---

@pytest.mark.parametrize("query", [
    "what is us-east-1: region?",      # ':' and '-'
    "auth: bcrypt",                    # ':'
    "path/to/file.py",                 # '/' '.'
    'a "quoted" term',                 # '"'
    "wildcard*",                       # '*'
    "x AND y OR z NOT w",              # bareword operators
    "(grouped)",                       # parens
])
def test_bm25_special_chars_no_crash(tmp_path: Path, query: str) -> None:
    store = RelationalStore(db_path=tmp_path / "fts.sqlite")
    store.insert_crystal(_crystal("c1", "us-east-1 deploy region auth bcrypt path file"))
    # must not raise sqlite3.OperationalError
    rows = store.bm25_search(query, layer_filter="episodic", k=10)
    assert isinstance(rows, list)


def test_bm25_no_layer_filter_binding(tmp_path: Path) -> None:
    # the no-layer branch previously passed 3 bindings for 2 placeholders -> error
    store = RelationalStore(db_path=tmp_path / "nl.sqlite")
    store.insert_crystal(_crystal("c1", "deploy region notes"))
    rows = store.bm25_search("deploy region", layer_filter=None, k=10)
    assert any(r["id"] == "c1" for r in rows)


def test_bm25_all_punctuation_returns_empty(tmp_path: Path) -> None:
    store = RelationalStore(db_path=tmp_path / "p.sqlite")
    store.insert_crystal(_crystal("c1", "real content"))
    assert store.bm25_search(":-*\"()", layer_filter="episodic", k=10) == []


# --- Fix #2: recall-after-update re-indexes the new revision into the vector store ---

def test_update_reembeds_new_revision(tmp_path: Path) -> None:
    from crystalium.server import _build_components, _handle_update
    cfg = Config(data_dir=tmp_path / "u", rate_limit_per_minute=10**9)
    (_e, _a, ep, se, pr, ex, _g, _s, relational) = _build_components(cfg)
    # commit a real episodic crystal
    res = ep.commit(payload={"summary": "deploy region is us-east-1", "scope": {"project": "p"}},
                    provenance={"source": "verified_agent", "author_agent": "t",
                                "created_at": _NOW.isoformat()}, caller_tier=Tier.T1)
    cid = res["id"]
    # spy the SHARED vector store so we can assert the update re-indexes the new id
    spy = MagicMock()
    spy.embed.return_value = [0.1, 0.2, 0.3]
    ep.vector_store = spy
    out = _handle_update(
        {"id": cid, "layer": "episodic",
         "patch": {"summary": "deploy region is eu-west-1", "content": "migrated"},
         "reason": "migrate"},
        ep, se, pr, ex, _e, relational, Tier.T1,
    )
    assert out["status"] == "updated"
    new_id = out["id"]
    # the new revision was upserted into the vector store (the bug: it never was)
    assert spy.upsert.called
    assert spy.upsert.call_args.kwargs.get("crystal_id") == new_id


# --- Fix #3: semantic.update on a missing id -> structured CRYSTAL_NOT_FOUND ---

def test_semantic_update_missing_id_structured(tmp_path: Path) -> None:
    from crystalium.server import _build_components
    cfg = Config(data_dir=tmp_path / "s", rate_limit_per_minute=10**9)
    (_e, _a, _ep, semantic, _pr, _ex, _g, _s, _r) = _build_components(cfg)
    with pytest.raises(CrystaliumEnforcementError) as exc:
        semantic.update(record_id="does-not-exist", patch={"summary": "x"},
                        reason="r", caller_tier=Tier.T1)
    assert exc.value.reason_code == "CRYSTAL_NOT_FOUND"


# --- Fix #4: the enforcement matrix is immutable at both levels ---

def test_matrix_immutable_both_levels() -> None:
    from crystalium import enforcement
    with pytest.raises(TypeError):
        enforcement._MATRIX[("semantic", "commit")][Tier.T3] = "allow"   # inner frozen
    with pytest.raises(TypeError):
        enforcement._MATRIX[("bogus", "op")] = {Tier.T0: "allow"}        # outer frozen


# --- Fix #5: negative/garbage recall k is clamped, not crashed ---

def test_recall_negative_k_clamped(tmp_path: Path) -> None:
    from crystalium.server import _build_components, _handle_recall
    cfg = Config(data_dir=tmp_path / "k", rate_limit_per_minute=10**9)
    (_e, aetheryte, ep, _se, _pr, _ex, _g, scheduler, _r) = _build_components(cfg)
    ep.commit(payload={"summary": "findable fact alpha", "scope": {"project": "p"}},
              provenance={"source": "verified_agent", "created_at": _NOW.isoformat()},
              caller_tier=Tier.T1)
    # negative k must not crash or tail-slice
    res = _handle_recall({"scope": {"project": "p"}, "query": "findable fact", "k": -5},
                         aetheryte, scheduler, Tier.T1)
    assert res is not None
    res2 = _handle_recall({"scope": {"project": "p"}, "query": "findable fact", "k": "garbage"},
                          aetheryte, scheduler, Tier.T1)
    assert res2 is not None
