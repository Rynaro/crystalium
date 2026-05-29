"""W2 Objective 1 — evb.py parallel scorer (EVB = Gain × Need).

Container-first:
  docker compose run --rm crystalium pytest mcp-server/tests/test_evb.py -v

evb.py is PARALLEL to importance.py (D6 lock): these tests assert the proxy math,
the drop-in importance_fn arity, and that importance_score is untouched.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pytest

from crystalium import evb

_NOW = datetime(2026, 5, 29, 12, 0, 0, tzinfo=timezone.utc)


@dataclass
class _Rec:
    access_count: int = 0
    last_access: datetime = _NOW
    outcome_success: float | None = None
    novelty_at_write: float = 0.0


def test_evb_is_gain_times_need():
    r = _Rec(access_count=10, last_access=_NOW, outcome_success=0.9, novelty_at_write=0.6)
    g = evb.gain(r, now=_NOW)
    n = evb.need(r, now=_NOW)
    assert evb.evb_score(r, now=_NOW) == pytest.approx(g * n)


def test_gain_components_deterministic():
    r = _Rec(access_count=0, last_access=_NOW, outcome_success=1.0, novelty_at_write=1.0)
    # af(access_count=0)=0 → corroboration term 0; gain = 0.5*1.0 + 0.3*1.0 + 0.2*0
    assert evb.gain(r, now=_NOW, weights=(0.5, 0.3, 0.2)) == pytest.approx(0.8)


def test_outcome_none_is_neutral_half():
    r = _Rec(access_count=0, outcome_success=None, novelty_at_write=0.0)
    # gain = 0.5*0.5 + 0.3*0 + 0.2*0 = 0.25
    assert evb.gain(r, now=_NOW, weights=(0.5, 0.3, 0.2)) == pytest.approx(0.25)


def test_recency_decays_need():
    fresh = _Rec(last_access=_NOW)
    stale = _Rec(last_access=_NOW - timedelta(days=28))  # 2 half-lives
    assert evb.need(stale, now=_NOW) < evb.need(fresh, now=_NOW)


def test_need_uses_query_match_when_present():
    r = _Rec(last_access=_NOW)
    low = evb.need(r, now=_NOW, query_ctx={"match": 0.0})
    high = evb.need(r, now=_NOW, query_ctx={"match": 1.0})
    assert high > low
    # no query_ctx → neutral 0.5 prior, between the two
    neutral = evb.need(r, now=_NOW)
    assert low < neutral < high


def test_evb_in_unit_range_under_default_weights():
    # Max-ish record: all components saturate
    r = _Rec(access_count=10_000, last_access=_NOW, outcome_success=1.0, novelty_at_write=1.0)
    s = evb.evb_score(r, now=_NOW, query_ctx={"match": 1.0})
    assert 0.0 <= s <= 1.0


def test_make_evb_scorer_is_dropin_importance_fn():
    scorer = evb.make_evb_scorer((0.5, 0.3, 0.2), (0.5, 0.3, 0.2))
    r = _Rec(access_count=5, last_access=_NOW, outcome_success=0.8, novelty_at_write=0.4)
    # importance_fn call shape: fn(record, now=now)
    assert scorer(r, now=_NOW) == pytest.approx(evb.evb_score(r, now=_NOW))


def test_importance_lock_untouched():
    # evb.py must not have mutated the frozen importance constants.
    from crystalium import importance

    assert importance.WEIGHTS == (0.25, 0.30, 0.25, 0.20)
    assert importance.RECENCY_HALFLIFE_DAYS == 14.0
