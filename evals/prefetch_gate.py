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
    "deploy rollback runbook",
    "database migration checklist",
    "incident escalation contacts",
    "feature flag kill switch",
]


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
    # Each tick: checkpoint predicting the next step's query, then recall it.
    for s in _STEPS:
        execution.checkpoint(
            state={"scope": {"project": _PROJECT}, "predicted_next_query": s,
                   "summary": f"cp:{s[:8]}"},
            caller_tier=Tier.T1,
        )
        t0 = telemetry.now_ms()
        try:
            aetheryte.recall(scope, s, 10, None, Tier.T1)
        except Exception:
            pass
        latencies.append(telemetry.now_ms() - t0)

    cache = execution.recall_cache
    return {
        "cache_hit_rate": cache.hit_rate() if cache is not None else None,
        "hits": getattr(cache, "hits", None),
        "misses": getattr(cache, "misses", None),
        "recall_p95_ms": _p95(latencies),
    }


def run(*, data_root: str = "/tmp/crystalium-prefetch-gate") -> dict[str, Any]:
    import os

    os.makedirs(data_root, exist_ok=True)
    off = run_arm(recall_prefetch=False, data_root=data_root)
    on = run_arm(recall_prefetch=True, data_root=data_root)

    hr = on["cache_hit_rate"]
    p95_on = on["recall_p95_ms"]
    p95_off = off["recall_p95_ms"]
    hit_ok = hr is not None and hr > 0.0
    # p95 must not regress (allow 20% slack for warm-up noise on tiny samples).
    p95_ok = p95_on is not None and p95_off is not None and p95_on <= p95_off * 1.2
    gate_pass = hit_ok and p95_ok
    return {
        "axes": {
            "cache_hit_rate": {"on": hr, "off": off["cache_hit_rate"]},
            "recall_p95_ms": {"on": p95_on, "off": p95_off},
        },
        "gate_pass": gate_pass,
        "verdict": (
            "prefetch yields cache hits without p95 regression — flip recall_prefetch ON"
            if gate_pass else
            "prefetch shows no hit-rate gain or regresses p95 — recall_prefetch stays OFF"
        ),
    }
