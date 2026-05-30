"""W5 Objective 2 — encoding-specificity post-RRF re-rank.

Container-first:
  docker compose run --rm crystalium pytest mcp-server/tests/test_context_match.py -v

Integration over a real recall pipeline with null vector/graph (bm25 arm only):
the crystal whose encoding_context better matches the scope-derived query context
ranks first when context_match is ON, and order is unchanged when OFF.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from crystalium.aetheryte.redact import Redactor
from crystalium.aetheryte.retrieve import Aetheryte
from crystalium.composer import Composer
from crystalium.config import Config
from crystalium.enforcement import Enforcement
from crystalium.importance import importance_score
from crystalium.schemas import Scope
from crystalium.server import _NullGraphStore, _NullVectorStore
from crystalium.storage.relational import RelationalStore
from crystalium.trust import Tier

_NOW = datetime(2026, 5, 30, tzinfo=timezone.utc)


def _crystal(cid: str, agent_class_ctx: str) -> dict:
    return {
        "id": cid, "layer": "semantic", "trust_tier": "T1",
        "validation_state": "validated", "status": "active",
        "summary": f"alpha shared fact {cid}",
        "scope": {"project": "p", "agent_class_visibility": None},
        "provenance": {"source": "verified_agent", "created_at": _NOW.isoformat()},
        "utility": {"access_count": 0, "last_access": _NOW.isoformat(), "importance": 0.0,
                    "novelty_at_write": 0.5},
        "temporal": {"t_valid_from": _NOW.isoformat()},
        "encoding_context": {"project": "p", "agent_class": agent_class_ctx},
    }


def _aetheryte(tmp_path: Path, *, context_match: bool) -> tuple[Aetheryte, RelationalStore]:
    cfg = Config(data_dir=tmp_path / f"cm-{context_match}")
    store = RelationalStore(db_path=cfg.sqlite_path)
    aeth = Aetheryte(
        relational=store, vector_store=_NullVectorStore(), graph_store=_NullGraphStore(),
        enforcement=Enforcement(cfg), redactor=Redactor(config=cfg),
        importance_fn=importance_score, composer=Composer(cfg),
        context_match=context_match,
    )
    store.insert_crystal(_crystal("match-X", "X"))
    store.insert_crystal(_crystal("other-Y", "Y"))
    return aeth, store


def test_context_match_reranks_matching_first(tmp_path: Path) -> None:
    aeth, _ = _aetheryte(tmp_path, context_match=True)
    res = aeth.recall(
        scope=Scope(project="p", agent_class_visibility="X"),
        query="alpha", k=10, layers=None, caller_tier=Tier.T1,
    )
    ids = [r.id for r in res.records]
    assert ids, "expected bm25 candidates"
    # The X-context crystal (overlap=2: project+agent_class) outranks Y (overlap=1).
    assert ids[0] == "match-X"


def test_context_match_off_does_not_force_order(tmp_path: Path) -> None:
    aeth, _ = _aetheryte(tmp_path, context_match=False)
    res = aeth.recall(
        scope=Scope(project="p", agent_class_visibility="X"),
        query="alpha", k=10, layers=None, caller_tier=Tier.T1,
    )
    ids = [r.id for r in res.records]
    assert set(ids) == {"match-X", "other-Y"}  # both present; no context re-rank applied
