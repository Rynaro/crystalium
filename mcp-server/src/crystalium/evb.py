"""Expected Value of Backup (EVB) importance — W2 keystone reformulation.

EVB = Gain x Need (Mattar & Daw 2018, Nat Neurosci 21:1609 -- verified). Optimal
memory access/replay is ordered by this product: Gain = how much accessing this
memory improves future choices; Need = how likely/soon it will be relevant. One
product unifies what to keep (eviction), what to surface (composer), and what to
replay (later waves).

DELIBERATE SFMA-STYLE PROXY (document + ablate):
  True Mattar-Daw Gain requires counterfactual policy evaluation -- how much
  re-experiencing this memory would change future action-values. CRYSTALIUM has no
  policy to evaluate, so we use a successor-feature-like proxy over the fields the
  MemoryRecord protocol already exposes:

    gain ≈ g(outcome_success, novelty_at_write, corroboration_potential)
    need ≈ n(recency, access_frequency, predicted_next_task_match)
    evb  = gain x need

  The proxy is itself flagged and ablatable: evb_score only becomes the importance
  function when Config.evb_enabled is True, and the ablation bench — not this
  docstring — decides whether it ships (ablation-as-arbiter; neuroscience is the
  hypothesis). [GAP] corroboration_potential and predicted_next_task_match have no
  canonical schema ground truth; their proxy formulas + assumptions are pinned
  below and may be revisited in later waves.

This module is PARALLEL to importance.py (D6 lock): it never imports anything
mutable from it beyond the read-only MemoryRecord protocol, and importance_score's
frozen signature/weights are untouched. evb_score shares importance_score's
arity — (record, *, now) — so a scorer built here is a drop-in importance_fn.

Under the default proxy weights each factor lies in [0, 1], so evb ∈ [0, 1] today
(threshold- and composer-compatible). It is persisted to the unbounded
memory_dynamics.evb field so future, non-normalised formulas need no schema bump.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from crystalium.importance import (
    _ACCESS_FREQ_CAP,
    RECENCY_HALFLIFE_DAYS,
    MemoryRecord,
)

# Default hand-tuned proxy weights (mirror Config.evb_gain_weights / evb_need_weights).
# gain:  (outcome_success, novelty, corroboration_potential)
# need:  (recency, access_frequency, predicted_next_task_match)
DEFAULT_GAIN_WEIGHTS: tuple[float, float, float] = (0.5, 0.3, 0.2)
DEFAULT_NEED_WEIGHTS: tuple[float, float, float] = (0.5, 0.3, 0.2)

# Neutral prior for predicted_next_task_match when no query context is supplied
# (the eviction + Dream paths call the scorer as fn(record, now=now), no query).
_NEUTRAL_TASK_MATCH: float = 0.5


def _access_frequency(record: MemoryRecord) -> float:
    """log-normalised access frequency, saturated at 1.0 (mirrors importance.py af).

    importance.py lets af exceed 1.0 for access_count > cap and clamps the final
    sum; here gain/need are multiplied, so we saturate the component at 1.0 to keep
    each factor — and thus the proxy evb — in [0, 1] (drop-in for the composer's
    [0,1] importance + the [0,1] eviction thresholds). The persisted field stays
    unbounded for future, non-normalised formulas.
    """
    af = math.log1p(max(0, record.access_count)) / math.log1p(_ACCESS_FREQ_CAP)
    return min(1.0, af)


def _recency(record: MemoryRecord, *, now: datetime) -> float:
    """Exponential recency decay in (0, 1] (mirrors importance.py rc)."""
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    last = record.last_access
    if last.tzinfo is None:
        last = last.replace(tzinfo=UTC)
    days = max(0.0, (now - last).total_seconds() / 86400.0)
    return 0.5 ** (days / RECENCY_HALFLIFE_DAYS)


def gain(
    record: MemoryRecord,
    *,
    now: datetime,
    weights: tuple[float, float, float] = DEFAULT_GAIN_WEIGHTS,
) -> float:
    """Gain proxy: value of backing this memory up for future choices.

    g = w0*outcome_success + w1*novelty_at_write + w2*corroboration_potential
      - outcome_success: realised usefulness (0.5 neutral when unscored).
      - novelty_at_write: surprising/novel memories carry more information.
      - corroboration_potential [GAP-D]: proxied by access frequency — repeated
        independent access is evidence the fact recurs and is worth consolidating.
    """
    w_os, w_nv, w_cp = weights
    outcome = record.outcome_success if record.outcome_success is not None else 0.5
    novelty = record.novelty_at_write
    corroboration_potential = _access_frequency(record)
    return w_os * outcome + w_nv * novelty + w_cp * corroboration_potential


def need(
    record: MemoryRecord,
    *,
    now: datetime,
    weights: tuple[float, float, float] = DEFAULT_NEED_WEIGHTS,
    query_ctx: Any | None = None,
) -> float:
    """Need proxy: probability this memory is required soon.

    n = w0*recency + w1*access_frequency + w2*predicted_next_task_match
      - predicted_next_task_match [GAP-D]: when query_ctx carries a numeric
        'match' in [0,1] it is used directly; otherwise a neutral 0.5 prior (the
        eviction/Dream paths score without a query).
    """
    w_rc, w_af, w_tm = weights
    rc = _recency(record, now=now)
    af = _access_frequency(record)
    tm = _predicted_task_match(query_ctx)
    return w_rc * rc + w_af * af + w_tm * tm


def _predicted_task_match(query_ctx: Any | None) -> float:
    """Extract a [0,1] task-match from query_ctx, else the neutral prior."""
    if query_ctx is None:
        return _NEUTRAL_TASK_MATCH
    if isinstance(query_ctx, (int, float)):
        return max(0.0, min(1.0, float(query_ctx)))
    if isinstance(query_ctx, dict) and "match" in query_ctx:
        try:
            return max(0.0, min(1.0, float(query_ctx["match"])))
        except (TypeError, ValueError):
            return _NEUTRAL_TASK_MATCH
    return _NEUTRAL_TASK_MATCH


def evb_score(
    record: MemoryRecord,
    *,
    now: datetime,
    gain_weights: tuple[float, float, float] = DEFAULT_GAIN_WEIGHTS,
    need_weights: tuple[float, float, float] = DEFAULT_NEED_WEIGHTS,
    query_ctx: Any | None = None,
) -> float:
    """EVB = gain x need. Drop-in importance_fn arity: evb_score(record, now=now)."""
    g = gain(record, now=now, weights=gain_weights)
    n = need(record, now=now, weights=need_weights, query_ctx=query_ctx)
    return g * n


def make_evb_scorer(
    gain_weights: tuple[float, float, float] = DEFAULT_GAIN_WEIGHTS,
    need_weights: tuple[float, float, float] = DEFAULT_NEED_WEIGHTS,
) -> Callable[..., float]:
    """Build an importance_fn-compatible scorer closing over Config EVB weights.

    Returned callable has signature (record, *, now, query_ctx=None) -> float, so
    it substitutes for importance_score wherever importance_fn is injected
    (_build_components selects it when config.evb_enabled is True).
    """

    def _scorer(record: MemoryRecord, *, now: datetime, query_ctx: Any | None = None) -> float:
        return evb_score(
            record,
            now=now,
            gain_weights=gain_weights,
            need_weights=need_weights,
            query_ctx=query_ctx,
        )

    return _scorer
