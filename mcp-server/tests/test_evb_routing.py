"""W2 Objective 3 — routing importance_fn through EVB behind evb_enabled.

Container-first:
  docker compose run --rm crystalium pytest mcp-server/tests/test_evb_routing.py -v

Asserts _build_components selects the EVB scorer + enables Dream write-back when
evb_enabled, and is byte-identical to legacy (importance_score, no write-back)
when off.
"""

from __future__ import annotations

from pathlib import Path

from crystalium.config import Config
from crystalium.importance import importance_score
from crystalium.server import _build_components


def _components(tmp_path: Path, *, evb: bool):
    cfg = Config(data_dir=tmp_path / ("on" if evb else "off"), evb_enabled=evb)
    (
        enforcement, aetheryte, episodic, semantic, procedural,
        execution, gate, scheduler, relational,
    ) = _build_components(cfg)
    return episodic, aetheryte, scheduler


def test_legacy_selects_importance_score(tmp_path: Path) -> None:
    episodic, aetheryte, scheduler = _components(tmp_path, evb=False)
    assert episodic.importance_fn is importance_score
    assert aetheryte.importance_fn is importance_score
    assert scheduler.worker.persist_dynamics is False


def test_evb_enabled_selects_evb_scorer_and_writeback(tmp_path: Path) -> None:
    episodic, aetheryte, scheduler = _components(tmp_path, evb=True)
    # No longer the legacy function — it's the evb closure.
    assert episodic.importance_fn is not importance_score
    assert aetheryte.importance_fn is not importance_score
    # Same selected callable injected everywhere (single source of truth).
    assert episodic.importance_fn is aetheryte.importance_fn
    # Dream prune write-back enabled.
    assert scheduler.worker.persist_dynamics is True


def test_evb_scorer_is_callable_with_importance_arity(tmp_path: Path) -> None:
    from datetime import datetime, timezone

    episodic, _, _ = _components(tmp_path, evb=True)
    fn = episodic.importance_fn

    class _Rec:
        access_count = 3
        last_access = datetime(2026, 5, 29, tzinfo=timezone.utc)
        outcome_success = 0.8
        novelty_at_write = 0.5

    val = fn(_Rec(), now=datetime(2026, 5, 30, tzinfo=timezone.utc))
    assert isinstance(val, float)
    assert 0.0 <= val <= 1.0
