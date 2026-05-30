"""W4 Objective 1 — pure FSRS/DSR forgetting-curve math.

Container-first:
  docker compose run --rm crystalium pytest mcp-server/tests/test_fsrs.py -v
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import pytest

from crystalium import fsrs

_NOW = datetime(2026, 5, 29, tzinfo=timezone.utc)


def test_retrievability_curve():
    assert fsrs.retrievability(10.0, 0.0) == pytest.approx(1.0)
    assert fsrs.retrievability(10.0, 10.0) == pytest.approx(0.9)   # R=0.9 at elapsed=S
    assert fsrs.retrievability(10.0, 20.0) == pytest.approx(0.81)  # 0.9^2
    assert fsrs.retrievability(0.0, 5.0) == 0.0                    # no stability -> forgotten


def test_retrievability_monotonic_decreasing():
    prev = 1.1
    for t in range(0, 40, 2):
        r = fsrs.retrievability(8.0, float(t))
        assert r <= prev
        prev = r


def test_boost_grows_and_caps():
    assert fsrs.boost_stability(10.0, factor=1.5) == pytest.approx(15.0)
    assert fsrs.boost_stability(10.0, factor=0.5) == pytest.approx(10.0)  # factor floored at 1
    assert fsrs.boost_stability(1e9, factor=2.0) == 36500.0               # capped


def test_is_lapse():
    # S=10: R(10,30)=0.9^3=0.729 < 0.8 floor -> lapse; R(10,5)~0.95 -> not
    assert fsrs.is_lapse(10.0, 30.0, r_floor=0.8) is True
    assert fsrs.is_lapse(10.0, 5.0, r_floor=0.8) is False


def test_elapsed_days():
    assert fsrs.elapsed_days(_NOW, _NOW + timedelta(days=3)) == pytest.approx(3.0)
    assert fsrs.elapsed_days(_NOW, _NOW - timedelta(days=3)) == 0.0  # clamped


def test_on_recall_first_touch_initialises():
    s, r = fsrs.on_recall(None, 0.0, r_floor=0.7, boost_factor=1.5,
                          initial_stability=2.0, lapse_stability=0.5)
    assert s == 2.0 and r == 1.0


def test_on_recall_success_boosts():
    s, r = fsrs.on_recall(10.0, 1.0, r_floor=0.7, boost_factor=1.5,
                          initial_stability=2.0, lapse_stability=0.5)
    assert s == pytest.approx(15.0) and r == 1.0


def test_on_recall_lapse_resets():
    # elapsed=40 on S=10 -> R=0.9^4=0.656 < 0.7 -> lapse reset
    s, r = fsrs.on_recall(10.0, 40.0, r_floor=0.7, boost_factor=1.5,
                          initial_stability=2.0, lapse_stability=0.5)
    assert s == 0.5 and r == 1.0
