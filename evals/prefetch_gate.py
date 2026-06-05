"""Deterministic W5 predictive-prefetch (protention) ablation gate.

Simulates a session where each plan checkpoint declares the query it expects to
need next; the agent then issues exactly that recall. Two arms — no cache
(recall_prefetch off) vs prefetch (on). Measures:

  cache_hit_rate — hits / (hits + misses) on the shared RecallCache (on arm only)
  recall_p95_ms  — 95th-percentile recall latency (warm cache should be faster)

Honest ablation (D6.4-ii): prefetch flips on only if the cache hit rate is
positive AND recall p95 does not regress vs the no-cache arm. The off arm has no
cache, so its hit rate is undefined (documented). Template = forgetting_gate.py.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from crystalium import telemetry

_T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
_PROJECT = "prefetch-gate"
_STEPS = [
    "deploy rollback runbook",        # 0
    "database migration checklist",   # 1
    "incident escalation contacts",   # 2
    "feature flag kill switch",       # 3
]

# A deterministic query stream over the 4 steps. The agent does NOT know the
# future query (the old gate handed it the exact verbatim next query — a
# fabricated perfect predictor). Instead it predicts the next query with an
# IMPERFECT first-order rotation model (predict (current+1) % 4). The stream
# mostly follows that rotation but deviates on 3 of 11 transitions, so the
# realistic hit rate is ~0.7 — protention with real uncertainty, not prepaid cost.
_STREAM = [0, 1, 2, 3, 0, 1, 2, 3, 0, 2, 1, 3]


def _predict_next(current_idx: int) -> int:
    """Imperfect protention: assume a fixed A→B→C→D→A rotation (right on the
    stream's dominant transitions, wrong on its deviations)."""
    return (current_idx + 1) % len(_STEPS)


def _commit(episodic, summary: str, tier) -> None:
    episodic.commit(
        payload={"summary": summary, "scope": {"project": _PROJECT}},
        provenance={"source": "verified_agent", "author_agent": "pg",
                    "created_at": _T0.isoformat()},
        caller_tier=tier,
    )


def _p95(samples: list[float]) -> float | None:
    if not samples:
        return None
    s = sorted(samples)
    idx = min(len(s) - 1, int(round(0.95 * (len(s) - 1))))
    return s[idx]


def run_arm(*, recall_prefetch: bool, data_root: str) -> dict[str, Any]:
    from pathlib import Path

    from crystalium.config import Config
    from crystalium.schemas import Scope
    from crystalium.server import _build_components
    from crystalium.trust import Tier

    cfg = Config(
        data_dir=Path(data_root) / f"pg-{int(recall_prefetch)}-{uuid.uuid4().hex[:8]}",
        recall_prefetch=recall_prefetch,
        rate_limit_per_minute=1_000_000,
    )
    (_enf, aetheryte, episodic, _sem, _proc, execution, _gate, _sched, _rel) = _build_components(cfg)

    # Seed one crystal per step so each recall has something to return.
    for s in _STEPS:
        _commit(episodic, f"{s} details and procedure", Tier.T1)

    scope = Scope(project=_PROJECT)
    latencies: list[float] = []
    pred_hits = 0
    pred_total = 0
    # Walk the stream. Each tick: (1) recall the ACTUAL query — which may have been
    # prefetched by the PREVIOUS tick's IMPERFECT prediction — then (2) checkpoint
    # an imperfect prediction of the NEXT query, warming the cache for it. The agent
    # never sees the future query; the cache only hits when protention was right.
    for i, idx in enumerate(_STREAM):
        query = _STEPS[idx]
        t0 = telemetry.now_ms()
        try:
            aetheryte.recall(scope, query, 10, None, Tier.T1)
        except Exception:
            pass
        latencies.append(telemetry.now_ms() - t0)

        if i + 1 < len(_STREAM):
            pred_idx = _predict_next(idx)
            pred_total += 1
            if pred_idx == _STREAM[i + 1]:
                pred_hits += 1
            execution.checkpoint(
                state={"scope": {"project": _PROJECT},
                       "predicted_next_query": _STEPS[pred_idx],
                       "summary": f"cp:{i}"},
                caller_tier=Tier.T1,
            )

    cache = execution.recall_cache
    return {
        "cache_hit_rate": cache.hit_rate() if cache is not None else None,
        "hits": getattr(cache, "hits", None),
        "misses": getattr(cache, "misses", None),
        "recall_p95_ms": _p95(latencies),
        # Sanity: a REALISTIC (imperfect) predictor — must be < 1.0, else the
        # fabricated-perfect-prediction confound has returned.
        "prediction_accuracy": (pred_hits / pred_total) if pred_total else None,
    }


def run(*, data_root: str = "/tmp/crystalium-prefetch-gate") -> dict[str, Any]:
    import os

    os.makedirs(data_root, exist_ok=True)
    off = run_arm(recall_prefetch=False, data_root=data_root)
    on = run_arm(recall_prefetch=True, data_root=data_root)

    hr = on["cache_hit_rate"]
    p95_on = on["recall_p95_ms"]
    p95_off = off["recall_p95_ms"]
    acc = on["prediction_accuracy"]
    hit_ok = hr is not None and hr > 0.0
    # p95 must not regress (allow 20% slack for warm-up noise on tiny samples).
    p95_ok = p95_on is not None and p95_off is not None and p95_on <= p95_off * 1.2
    # Confound guard: the predictor MUST be imperfect (the old gate handed over the
    # verbatim future query → accuracy 1.0). A realistic hit must be earned, not prepaid.
    realistic = acc is not None and acc < 1.0
    # PROTENTION ISOLATION (the confound this gate still cannot clear): the OFF arm
    # (recall_prefetch=False) has NO recall cache at all, so on-vs-off measures
    # cache-vs-no-cache — the huge p95 win is ordinary cache-warming of REPEATED
    # queries (the cache holds every recall; repeats hit regardless of prediction),
    # not predictive prefetch. Crediting protention needs a cache-on/prefetch-off
    # baseline, which the bundled `recall_prefetch` flag does not expose. Until then
    # the protention-specific win is unprovable and the flag stays OFF (honest null).
    protention_isolated = off["cache_hit_rate"] is not None
    gate_pass = hit_ok and p95_ok and realistic and protention_isolated
    return {
        "axes": {
            "cache_hit_rate": {"on": hr, "off": off["cache_hit_rate"]},
            "recall_p95_ms": {"on": p95_on, "off": p95_off},
            "prediction_accuracy": {"on": acc, "off": off["prediction_accuracy"]},
        },
        "gate_pass": gate_pass,
        "protention_isolated": protention_isolated,
        "verdict": (
            f"prefetch earns cache hits under imperfect prediction (acc={acc}) without "
            "p95 regression — flip recall_prefetch ON"
            if gate_pass else
            "imperfect-predictor confound FIXED (acc<1.0), but the p95 win is cache-vs-"
            "no-cache (the OFF arm has no cache) — protention is NOT isolated from plain "
            "cache-warming of repeated queries. recall_prefetch stays OFF until a "
            "cache-on/prefetch-off baseline can credit protention specifically."
        ),
    }
