"""FSRS/DSR forgetting curve — W4 forgetting faculty.

Free Spaced Repetition Scheduler (open-spaced-repetition; **verified**, runs fully
local). Each memory carries Difficulty, **Stability** (S, days), and
**Retrievability** (R). The forgetting curve is

    R = exp(ln(0.9) * elapsed_days / S)      # R = 0.9 exactly when elapsed == S

Successful recall **raises S** (reconsolidation — Nader 2000 / Schiller 2010
[CONTESTED: Chalkia 2020]; the implementation stands on engineering grounds: a
recalled memory is demonstrably worth keeping longer). A **lapse** (R has already
fallen below the floor by the time the memory is next touched) **resets S** to a
low value — the forgetting curve restarts.

Pure module (no I/O, no DB), mirroring evb.py: deterministic, fully unit-tested.
All tuning constants are supplied by the caller (from Config) so this module owns
the math, not the policy. Behind Config.forgetting_fsrs; nothing imports it when
the flag is off.
"""

from __future__ import annotations

import math
from datetime import datetime

_LN_09 = math.log(0.9)


def elapsed_days(last_access: datetime, now: datetime) -> float:
    """Non-negative days between last_access and now (tz-aware preferred)."""
    delta = (now - last_access).total_seconds() / 86400.0
    return max(0.0, delta)


def retrievability(stability: float, elapsed: float) -> float:
    """R = exp(ln(0.9) * elapsed / S), in (0, 1]. R=1 at elapsed=0; R=0.9 at elapsed=S.

    A non-positive stability is treated as already-forgotten (R -> 0.0).
    """
    if stability <= 0:
        return 0.0
    if elapsed <= 0:
        return 1.0
    return math.exp(_LN_09 * elapsed / stability)


def boost_stability(stability: float, *, factor: float, max_stability: float = 36500.0) -> float:
    """Reconsolidation: a successful recall multiplies stability (capped).

    factor > 1 grows S (more durable); bounded at max_stability (~100y) so it
    can't run away.
    """
    boosted = max(stability, 0.0) * max(1.0, factor)
    return min(boosted, max_stability)


def is_lapse(stability: float, elapsed: float, *, r_floor: float) -> bool:
    """True when the memory has already decayed below r_floor at access time."""
    return retrievability(stability, elapsed) < r_floor


def lapse_reset(lapse_stability: float) -> float:
    """Stability after a lapse — the forgetting curve restarts low."""
    return max(0.01, lapse_stability)


def on_recall(
    stability: float | None,
    elapsed: float,
    *,
    r_floor: float,
    boost_factor: float,
    initial_stability: float,
    lapse_stability: float,
) -> tuple[float, float]:
    """Update (stability, retrievability) on a recall touch.

    - First touch (stability is None) -> initialise to initial_stability.
    - Lapse (R < r_floor) -> reset stability low (forgetting restarts).
    - Otherwise successful recall -> boost stability (reconsolidation).
    Returns (new_stability, retrievability_after) where R-after is 1.0 (just
    accessed). Pure: caller persists the result.
    """
    if stability is None:
        return initial_stability, 1.0
    if is_lapse(stability, elapsed, r_floor=r_floor):
        return lapse_reset(lapse_stability), 1.0
    return boost_stability(stability, factor=boost_factor), 1.0
