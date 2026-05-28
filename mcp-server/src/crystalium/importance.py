"""Importance function for CRYSTALIUM — FORGE D6 SIGNATURE LOCK.

DO NOT change the signature of importance_score() or the names/order of
WEIGHTS. D11 (adaptive learning, out of scope for v0.1) mutates ONLY the
WEIGHTS tuple. Every other change is a breaking spec deviation.

Usage:
    score = importance_score(record, now=datetime.now(timezone.utc))

The same function drives BOTH:
  - write-gate criterion (enforcement chokepoint)
  - forget-weight in Dream prune (forget_weight = 1 - importance_score)

Source: FORGE D6 + spec.yaml §config_defaults importance_weights.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Protocol


# ---------------------------------------------------------------------------
# D6 FROZEN SIGNATURE — do NOT change names or order
# WEIGHTS: (access_frequency, recency, outcome_success, novelty)
# ---------------------------------------------------------------------------

WEIGHTS: tuple[float, float, float, float] = (0.25, 0.30, 0.25, 0.20)

RECENCY_HALFLIFE_DAYS: float = 14.0  # OQ-4: operator-tunable? deferred.

_ACCESS_FREQ_CAP: float = 100.0  # log1p denominator — D6 config_defaults


# ---------------------------------------------------------------------------
# MemoryRecord Protocol
# ---------------------------------------------------------------------------


class MemoryRecord(Protocol):
    """Structural protocol for any record that can be scored by importance_score().

    Concrete implementations: Crystal (from storage), inline test stubs.
    """

    access_count: int
    """Total number of recall hits on this record."""

    last_access: datetime
    """Most recent recall timestamp (timezone-aware preferred)."""

    outcome_success: float | None
    """Outcome success score in [0, 1], or None if not yet scored."""

    novelty_at_write: float
    """Novelty score [0, 1] frozen at write time (OQ-9: recomputation deferred)."""


# ---------------------------------------------------------------------------
# D6 importance function
# ---------------------------------------------------------------------------


def importance_score(record: MemoryRecord, *, now: datetime) -> float:
    """Compute the importance score for *record* relative to *now*.

    Formula (FORGE D6):
        af  = log1p(access_count) / log1p(100)       # access frequency, capped at ~100 hits
        rc  = 0.5 ** (days_since_access / halflife)  # recency decay (exponential)
        os_ = outcome_success if not None else 0.5   # outcome success (default unscored = 0.5)
        nv  = novelty_at_write                        # frozen at write time

        raw = w_af*af + w_rc*rc + w_os*os_ + w_nv*nv
        return clamp(raw, 0.0, 1.0)

    Args:
        record: Any object satisfying the MemoryRecord protocol.
        now:    Reference datetime (use datetime.now(timezone.utc) in production).
                Must be timezone-aware; if naive, UTC is assumed.

    Returns:
        float in [0.0, 1.0]. Higher = more important / should be retained.
    """
    # Normalise timezone
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    last_access = record.last_access
    if last_access.tzinfo is None:
        last_access = last_access.replace(tzinfo=timezone.utc)

    # Component: access frequency (log-normalised, dampens heavy-hit dominance)
    af = math.log1p(max(0, record.access_count)) / math.log1p(_ACCESS_FREQ_CAP)

    # Component: recency decay (exponential halflife)
    days_elapsed = max(0.0, (now - last_access).total_seconds() / 86400.0)
    rc = 0.5 ** (days_elapsed / RECENCY_HALFLIFE_DAYS)

    # Component: outcome success (0.5 for unscored records = neutral)
    os_ = record.outcome_success if record.outcome_success is not None else 0.5

    # Component: novelty at write time (frozen per D6/OQ-9)
    nv = record.novelty_at_write

    w_af, w_rc, w_os, w_nv = WEIGHTS
    raw = w_af * af + w_rc * rc + w_os * os_ + w_nv * nv

    return max(0.0, min(1.0, raw))
