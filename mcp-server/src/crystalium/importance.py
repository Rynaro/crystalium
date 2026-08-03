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


# ---------------------------------------------------------------------------
# Cold-start importance (crystalium#36 / FORGE DP-4=C, v1.9.0)
#
# A freshly committed crystal was hardcoded to utility.importance=0.0 on every
# episodic/procedural/semantic commit, even though each layer is handed an
# importance_fn it never called. Calling importance_fn bare would score a fresh
# EVB record at ~0.24 (fine — see below) but a fresh LEGACY-scorer record at
# ~0.525, which is *durable* under evb_enabled=False (persist_dynamics is off
# there, so Dream never overwrites it) — a permanent starvation inversion above
# a genuinely proven-useful record (measured 0.386 for 5 accesses / 0.9 outcome
# / 7 days old at af24493). The ceiling below makes that mechanically
# impossible rather than merely documented.
# ---------------------------------------------------------------------------

#: Cold-start importance ceiling (FORGE DP-4=C). Derived from the measured
#: band at af24493: keeps a fresh crystal below a proven-useful record (0.386)
#: and inside the reporter's live-observed band (0.15-0.42) for issue #36.
#: Under the default EVB scorer this never binds (fresh EVB ~= 0.24-0.26); it
#: only clamps the legacy scorer's much higher bare cold-start value (~0.525).
COLD_START_IMPORTANCE_CEILING: float = 0.30


class _ColdStartStub:
    """Minimal MemoryRecord stub for scoring a crystal at commit time (write path).

    Mirrors aetheryte/retrieve.py::_AccessStub (the read-path equivalent) but is
    built directly from a commit's `utility` dict instead of a stored crystal.
    """

    __slots__ = ("access_count", "last_access", "outcome_success", "novelty_at_write")

    def __init__(self, utility: dict, now: datetime) -> None:
        self.access_count = int(utility.get("access_count", 0))
        self.last_access = now
        self.outcome_success = utility.get("outcome_success_score")
        self.novelty_at_write = float(utility.get("novelty_at_write", 0.5))


def initial_importance(
    importance_fn: "object",
    utility: dict,
    now: datetime,
) -> float:
    """Cold-start importance for a crystal at the moment of commit (FORGE DP-4=C).

    Scores a MemoryRecord-shaped stub built from *utility* (access_count=0,
    last_access=now, outcome_success=None, novelty_at_write=payload-supplied or
    0.5 default — the fields every layer's utility dict already carries at
    commit time) through *importance_fn* — the same swap point recall/Dream use
    (evb_score under evb_enabled=True, legacy importance_score otherwise) — and
    clamps the result to COLD_START_IMPORTANCE_CEILING.

    Sub-rulings (DP-4a/b/c, binding):
      - utility.importance ONLY. Never writes memory_dynamics (that is Dream's
        writer, dream/worker.py).
      - No backfill: only called at commit time for a NEW row; pre-existing
        0.0-importance rows are untouched and rescued by relevance (crystalium#36
        seams 1-5), not by this helper.

    Args:
        importance_fn: Callable(record, *, now) -> float (importance_score or
                        an evb_score closure — the same value injected into the
                        layer's constructor).
        utility:        The utility dict already built by the caller (before
                         this call sets its "importance" key).
        now:             Reference datetime (must match utility["last_access"]).

    Returns:
        float in [0.0, COLD_START_IMPORTANCE_CEILING].
    """
    stub = _ColdStartStub(utility, now)
    raw = importance_fn(stub, now=now)  # type: ignore[call-arg]
    return min(float(raw), COLD_START_IMPORTANCE_CEILING)
