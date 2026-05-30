"""W6 C4 — recall_active_only excludes deprecated/superseded crystals.

Integration via live components (BM25 path works without an embedder, so this
runs under CRYSTALIUM_SKIP_SLOW).

Container-first:
  docker compose run --rm crystalium pytest mcp-server/tests/test_recall_active_only.py -v
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from crystalium.config import Config
from crystalium.schemas import Scope
from crystalium.server import _build_components
from crystalium.trust import Tier

_NOW = datetime(2026, 6, 1, tzinfo=timezone.utc)


def _build(tmp_path: Path, *, active_only: bool):
    cfg = Config(
        data_dir=tmp_path / f"rao-{active_only}",
        recall_active_only=active_only,
        rate_limit_per_minute=1_000_000,
    )
    return _build_components(cfg)


def _commit(episodic, summary: str) -> str:
    res = episodic.commit(
        payload={"summary": summary, "scope": {"project": "rao"}},
        provenance={"source": "verified_agent", "author_agent": "t",
                    "created_at": _NOW.isoformat()},
        caller_tier=Tier.T1,
    )
    return res["id"]


def _ids(aetheryte) -> list[str]:
    res = aetheryte.recall(Scope(project="rao"), "alpha bravo charlie token", 10, None, Tier.T1)
    return [r.id for r in res.records]


def test_off_returns_deprecated(tmp_path: Path) -> None:
    (_e, aetheryte, episodic, _s, _p, _x, _g, _sc, relational) = _build(tmp_path, active_only=False)
    keep = _commit(episodic, "alpha bravo charlie token good")
    gone = _commit(episodic, "alpha bravo charlie token rejected")
    relational.set_status(gone, "deprecated")
    got = _ids(aetheryte)
    assert keep in got and gone in got        # OFF: deprecated still surfaces (W5 behavior)


def test_on_excludes_deprecated_and_superseded(tmp_path: Path) -> None:
    (_e, aetheryte, episodic, _s, _p, _x, _g, _sc, relational) = _build(tmp_path, active_only=True)
    keep = _commit(episodic, "alpha bravo charlie token good")
    dep = _commit(episodic, "alpha bravo charlie token rejected")
    sup = _commit(episodic, "alpha bravo charlie token old")
    relational.set_status(dep, "deprecated")
    relational.mark_superseded(sup, keep, _NOW)   # sets temporal.t_valid_to
    got = _ids(aetheryte)
    assert keep in got
    assert dep not in got                          # deprecated excluded
    assert sup not in got                          # superseded excluded
