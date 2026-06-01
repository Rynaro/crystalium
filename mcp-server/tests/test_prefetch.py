"""W5 Objective 4 — predictive prefetch (protention).

RecallCache LRU semantics + ExecutionLayer.checkpoint warming/prediction_error.

Container-first:
  docker compose run --rm crystalium pytest mcp-server/tests/test_prefetch.py -v
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

from crystalium.aetheryte.cache import RecallCache
from crystalium.config import Config
from crystalium.enforcement import Enforcement
from crystalium.importance import importance_score
from crystalium.layers.execution import ExecutionLayer
from crystalium.storage.relational import RelationalStore
from crystalium.trust import Tier

_NOW = datetime(2026, 5, 30, tzinfo=timezone.utc)


# ---- RecallCache unit semantics ------------------------------------------


def test_cache_get_counts_hits_and_misses() -> None:
    c = RecallCache()
    assert c.get("p", "q") is None          # miss
    c.put("p", "q", "RESULT")
    assert c.get("p", "q") == "RESULT"       # hit
    assert c.hits == 1 and c.misses == 1
    assert c.hit_rate() == 0.5


def test_cache_peek_does_not_count() -> None:
    c = RecallCache()
    assert c.peek("p", "q") is False
    c.put("p", "q", "R")
    assert c.peek("p", "q") is True
    assert c.hits == 0 and c.misses == 0     # peek is non-counting
    assert c.hit_rate() is None


def test_cache_lru_evicts_oldest() -> None:
    c = RecallCache(max_size=2)
    c.put("p", "a", 1)
    c.put("p", "b", 2)
    c.get("p", "a")                          # 'a' now most-recent
    c.put("p", "c", 3)                       # evicts least-recent ('b')
    assert c.peek("p", "a") is True
    assert c.peek("p", "b") is False
    assert c.peek("p", "c") is True


def test_cache_invalidate_project_is_scoped() -> None:
    c = RecallCache()
    c.put("p1", "q", 1)
    c.put("p2", "q", 2)
    c.invalidate_project("p1")
    assert c.peek("p1", "q") is False
    assert c.peek("p2", "q") is True


# ---- ExecutionLayer prefetch warming -------------------------------------


def _exec_layer(tmp_path: Path, *, prefetch: bool, cache: RecallCache | None, aetheryte):
    cfg = Config(data_dir=tmp_path / f"ex-{prefetch}", rate_limit_per_minute=10000)
    store = RelationalStore(db_path=cfg.sqlite_path)
    blob = MagicMock()
    blob.put.return_value = "b" * 64
    layer = ExecutionLayer(
        blob_store=blob, relational=store, enforcement=Enforcement(cfg),
        importance_fn=importance_score, aetheryte=aetheryte,
        recall_cache=cache, recall_prefetch=prefetch,
    )
    return layer, store


def test_checkpoint_warms_cache_and_records_prediction_error(tmp_path: Path) -> None:
    cache = RecallCache()
    aeth = MagicMock()
    aeth.recall.side_effect = lambda *a, **k: cache.put("proj", "next step", "WARM")
    layer, store = _exec_layer(tmp_path, prefetch=True, cache=cache, aetheryte=aeth)

    res = layer.checkpoint(
        state={"scope": {"project": "proj"}, "predicted_next_query": "next step",
               "summary": "cp1"},
        caller_tier=Tier.T1,
    )
    # cold prediction -> warmed via aetheryte, prediction_error = 1.0 on the crystal
    aeth.recall.assert_called_once()
    assert cache.peek("proj", "next step") is True
    dyn = store.get_crystal(res["id"]).get("memory_dynamics") or {}
    assert dyn.get("prediction_error") == 1.0


def test_checkpoint_warm_prediction_has_zero_error(tmp_path: Path) -> None:
    cache = RecallCache()
    # Pre-warm with the SAME key the checkpoint's warmth check uses (k=10, layers=None,
    # scope visibility/sensitivity None, tier=T1) — see ExecutionLayer.checkpoint.
    cache.put("proj", "next step", "ALREADY",
              k=10, layers=None, visibility=None, sensitivity=None, tier="T1")
    aeth = MagicMock()
    layer, store = _exec_layer(tmp_path, prefetch=True, cache=cache, aetheryte=aeth)

    res = layer.checkpoint(
        state={"scope": {"project": "proj"}, "predicted_next_query": "next step"},
        caller_tier=Tier.T1,
    )
    aeth.recall.assert_not_called()                  # already warm -> no warming
    dyn = store.get_crystal(res["id"]).get("memory_dynamics") or {}
    assert dyn.get("prediction_error") == 0.0


def test_checkpoint_off_does_not_touch_cache(tmp_path: Path) -> None:
    cache = RecallCache()
    aeth = MagicMock()
    layer, store = _exec_layer(tmp_path, prefetch=False, cache=cache, aetheryte=aeth)
    res = layer.checkpoint(
        state={"scope": {"project": "proj"}, "predicted_next_query": "next step"},
        caller_tier=Tier.T1,
    )
    aeth.recall.assert_not_called()
    assert cache.peek("proj", "next step") is False
    dyn = store.get_crystal(res["id"]).get("memory_dynamics")
    assert not dyn or "prediction_error" not in dyn


# --- Battle-test fix (MEDIUM): cache must key on all recall-shaping dimensions ---

def test_recall_cache_isolates_security_relevant_dimensions() -> None:
    """A result cached under one visibility / layer-subset / k must NOT be served to
    a different one — keying on (project, query) alone was a correctness + leak bug."""
    c = RecallCache()
    c.put("p", "q", "R_admin", k=10, layers=None, visibility="admin", sensitivity=None, tier="T0")

    # Same project+query but different visibility / k / layers -> MISS (no cross-serve).
    assert c.get("p", "q", k=10, layers=None, visibility="public", sensitivity=None, tier="T0") is None
    assert c.get("p", "q", k=5, layers=None, visibility="admin", sensitivity=None, tier="T0") is None
    assert c.get("p", "q", k=10, layers=["semantic"], visibility="admin", sensitivity=None, tier="T0") is None
    # Exact same context -> HIT.
    assert c.get("p", "q", k=10, layers=None, visibility="admin", sensitivity=None, tier="T0") == "R_admin"
    # layers order-insensitive (normalized to a sorted tuple).
    c.put("p", "q2", "R", k=3, layers=["b", "a"], visibility=None, sensitivity=None, tier="T1")
    assert c.get("p", "q2", k=3, layers=["a", "b"], visibility=None, sensitivity=None, tier="T1") == "R"


def test_recall_cache_invalidate_project_still_works_with_rich_key() -> None:
    c = RecallCache()
    c.put("p1", "q", "A", k=10, visibility="v")
    c.put("p2", "q", "B", k=10, visibility="v")
    c.invalidate_project("p1")
    assert c.get("p1", "q", k=10, visibility="v") is None
    assert c.get("p2", "q", k=10, visibility="v") == "B"
