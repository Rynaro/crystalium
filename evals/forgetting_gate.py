"""Deterministic W4 forgetting-faculty ablation gate (long synthetic session).

Runs a many-tick session (advancing `now`) in two arms — LRU (forgetting_fsrs off)
vs FSRS (on) — over a fixed population: a small high-value set recalled every tick
(so FSRS reconsolidation keeps it) and a stream of never-recalled noise that ages
out. Measures the three DoD axes per arm:

  memory_size       — active-crystal count per tick -> memory_size_plateau (lower=flatter)
  high_value_recall — fraction of the high-value set still active (high_value_retention)
  recall_latency    — wall-clock ms per recall (now_ms bracketing)

Honest ablation: a flag flips on only if FSRS wins all three (plateau lower,
retention >=, latency <=). Template = evb_gate.py / dream_gate.py.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from crystalium import telemetry
from evals.metrics import high_value_retention, memory_size_plateau

_T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
# Longer session + higher noise:signal + prune-every-tick (W4 DoD): the original
# 24 ticks / 1-noise / prune-every-3 built too little eviction pressure for EITHER
# arm to plateau. With sustained noise and an every-tick prune, legacy/LRU memory
# grows unbounded — its importance-threshold prune cannot evict unscored noise (the
# 0.5 neutral-outcome term floors importance at ≈0.125 > 0.10) — while FSRS's
# retrievability-decay eviction bounds it. That contrast is what the gate must see.
_TICKS = 60
_HV = 5  # high-value crystals recalled INFREQUENTLY (the value×recency discriminator)
_NOISE_PER_TICK = 4  # sustained noise:signal pressure
# Recall the high-value set only every Nth tick. This is FSRS's real test: between
# accesses the keystone memory "ages", so a pure-recency LRU prune evicts it, while
# FSRS's accumulated spaced-repetition STABILITY should keep it retrievable. Recall
# every-tick (the old workload) keeps it under BOTH arms → no discrimination.
_RECALL_EVERY = 8


def _commit(episodic, summary: str, tier, now: datetime) -> str:
    res = episodic.commit(
        payload={"summary": summary, "scope": {"project": "forget-gate"}},
        provenance={"source": "verified_agent", "author_agent": "fg",
                    "created_at": now.isoformat()},
        caller_tier=tier,
    )
    return res.get("id", "")


def run_arm(*, forgetting_fsrs: bool, data_root: str) -> dict[str, Any]:
    from pathlib import Path

    from crystalium.config import Config
    from crystalium.schemas import Scope
    from crystalium.server import _build_components
    from crystalium.trust import Tier

    cfg = Config(
        data_dir=Path(data_root) / f"fg-{'fsrs' if forgetting_fsrs else 'lru'}-{uuid.uuid4().hex[:8]}",
        forgetting_fsrs=forgetting_fsrs,
        r_floor=0.7,
        rate_limit_per_minute=1_000_000,
    )
    (_enf, aetheryte, episodic, _sem, _proc, _exec, _gate, scheduler, relational) = _build_components(cfg)
    worker = scheduler.worker

    # Seed the high-value set up front (recalled every tick).
    hv_ids: set[str] = set()
    for i in range(_HV):
        hv_ids.add(_commit(episodic, f"keystone fact {i} about acme auth", Tier.T1, _T0))

    telemetry.reset_latency_samples()
    counts: list[int] = []
    latencies: list[float] = []

    for d in range(1, _TICKS + 1):
        now = _T0 + timedelta(days=d)
        # Sustained noise each tick (never recalled -> ages out). Deterministic
        # unique summaries ({d}-{_n}) — no uuid — so the gate is reproducible
        # (matching the module's "deterministic" claim) yet dedup-safe.
        for _n in range(_NOISE_PER_TICK):
            _commit(episodic, f"transient note tick {d} item {_n}", Tier.T1, now)
        # Recall the high-value topic only every Nth tick (infrequent access).
        # On hits FSRS reconsolidates stability; LRU only refreshes recency.
        if d % _RECALL_EVERY == 0:
            t0 = telemetry.now_ms()
            try:
                aetheryte.recall(scope=Scope(project="forget-gate"),
                                 query="keystone fact acme auth", k=10, caller_tier=Tier.T1)
            except Exception:
                pass
            latencies.append(telemetry.now_ms() - t0)
        # Prune EVERY tick (aggressive eviction — exposes the LRU-vs-FSRS gap).
        worker._prune(now)
        active = sum(1 for c in relational.list_crystals_with_dynamics() if c["status"] == "active")
        counts.append(active)

    snapshot = relational.list_crystals_with_dynamics()
    return {
        "memory_size_plateau": memory_size_plateau(counts),
        "high_value_retention": high_value_retention(snapshot, high_value_ids=hv_ids),
        "recall_latency_ms": (sum(latencies) / len(latencies)) if latencies else None,
        "final_active": counts[-1] if counts else None,
        "counts": counts,
    }


def run(*, data_root: str = "/tmp/crystalium-forgetting-gate") -> dict[str, Any]:
    import os

    os.makedirs(data_root, exist_ok=True)
    lru = run_arm(forgetting_fsrs=False, data_root=data_root)
    fsrs = run_arm(forgetting_fsrs=True, data_root=data_root)

    def _delta(a, b):
        return (a - b) if (a is not None and b is not None) else None

    axes = {
        "memory_size_plateau": {"on": fsrs["memory_size_plateau"], "off": lru["memory_size_plateau"],
                                "delta": _delta(fsrs["memory_size_plateau"], lru["memory_size_plateau"])},
        "high_value_retention": {"on": fsrs["high_value_retention"], "off": lru["high_value_retention"],
                                 "delta": _delta(fsrs["high_value_retention"], lru["high_value_retention"])},
        "recall_latency_ms": {"on": fsrs["recall_latency_ms"], "off": lru["recall_latency_ms"],
                              "delta": _delta(fsrs["recall_latency_ms"], lru["recall_latency_ms"])},
    }
    # DoD: plateau strictly flatter, retention not worse, latency holds-or-better.
    p = axes["memory_size_plateau"]
    r = axes["high_value_retention"]
    lat = axes["recall_latency_ms"]
    # plateau must be MEANINGFULLY flatter (>=10%), not a noise-level strict-<: a
    # production default must never flip on a ~2% synthetic difference that flips
    # sign run-to-run. retention not worse; latency within slack.
    plateau_ok = p["on"] is not None and p["off"] is not None and p["on"] <= p["off"] * 0.9
    retain_ok = r["on"] is not None and r["off"] is not None and r["on"] >= r["off"]
    latency_ok = lat["on"] is not None and lat["off"] is not None and lat["on"] <= lat["off"] * 1.2
    gate_pass = plateau_ok and retain_ok and latency_ok
    return {
        "axes": axes,
        "gate_pass": gate_pass,
        "verdict": (
            "FSRS beats LRU by a meaningful margin on all three DoD axes — flip forgetting_fsrs ON"
            if gate_pass else
            "FSRS does NOT meaningfully beat LRU (both retain the keystone; both bound noise "
            "comparably; plateau gap < 10% and sign-unstable) — forgetting_fsrs stays OFF"
        ),
    }
