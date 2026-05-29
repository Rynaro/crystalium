"""Deterministic EVB ablation-gate workload (W2 DoD).

The generic canary can't evaluate EVB: it triggers no promotions and no Dream
eviction, so both gate metrics come back undefined. This module runs a small,
controlled, deterministic population through a real Dream prune in each arm
(evb_enabled on/off) and computes the two gate metrics over a GROUND-TRUTH value
label (id prefix) — scorer-independent, so the A/B is fair in both arms.

Population (semantic layer, prune threshold 0.10):
  hv-*  high value : high outcome + high novelty + frequent + recent  (high gain AND need)
  ds-*  distractor : frequent + recent but zero outcome/novelty       (high need, low gain)
  lv-*  low value  : sparse + stale + unscored                        (low everything)

Promotions: the hv-* set is recorded in the ledger (the memories worth keeping).
Metrics (both arms, ground-truth labels):
  promotion_precision  = promoted ∩ (recalled & positive outcome) / promoted
  high_value_retention = hv-* still active after prune / hv-*
Diagnostic (not gate-deciding):
  distractor_eviction  = ds-* deprecated after prune / ds-*   (EVB's precision)

This file is bench-only; it never runs in production paths.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from evals.metrics import high_value_retention, promotion_precision

_T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def _utility(access: int, outcome: float | None, novelty: float, last_access: datetime) -> dict:
    return {
        "access_count": access,
        "last_access": last_access.isoformat(),
        "outcome_success_score": outcome,
        "importance": 0.0,
        "novelty_at_write": novelty,
    }


def _crystal(cid: str, util: dict) -> dict:
    return {
        "id": cid,
        "layer": "semantic",
        "trust_tier": "T1",
        "validation_state": "validated",
        "status": "active",
        "summary": f"fixture fact {cid}",
        "scope": {"project": "evb-gate"},
        "provenance": {"source": "verified_agent", "created_at": _T0.isoformat()},
        "utility": util,
        "temporal": {"t_valid_from": _T0.isoformat()},
    }


def _population(now: datetime) -> tuple[list[dict], set[str]]:
    """Deterministic crystals + the ground-truth high-value id set."""
    crystals: list[dict] = []
    hv_ids: set[str] = set()
    for i in range(6):
        cid = f"hv-{i}"
        hv_ids.add(cid)
        crystals.append(_crystal(cid, _utility(20, 0.9, 0.8, now)))            # high gain & need
    for i in range(6):
        crystals.append(_crystal(f"ds-{i}", _utility(5, 0.0, 0.0, now)))        # high need, low gain
    for i in range(6):
        old = now - timedelta(days=120)
        crystals.append(_crystal(f"lv-{i}", _utility(0, None, 0.05, old)))      # low everything
    return crystals, hv_ids


def run_arm(evb_enabled: bool, *, data_root: str) -> dict[str, Any]:
    """Run the workload in one arm; return the per-arm metric values + snapshot."""
    from pathlib import Path

    from crystalium.config import Config
    from crystalium.server import _build_components

    now = _T0 + timedelta(days=1)
    cfg = Config(
        data_dir=Path(data_root) / f"evb-gate-{'on' if evb_enabled else 'off'}-{uuid.uuid4().hex[:8]}",
        evb_enabled=evb_enabled,
        rate_limit_per_minute=100000,
    )
    (_enf, _aeth, _ep, _sem, _proc, _exec, _gate, scheduler, relational) = _build_components(cfg)
    worker = scheduler.worker

    crystals, hv_ids = _population(now)
    for c in crystals:
        relational.insert_crystal(c)
        # When evb is on, persist a fresh evb so the composer/diagnostics see it.
        if evb_enabled:
            from crystalium.evb import evb_score
            from crystalium.aetheryte.retrieve import _AccessStub

            relational.update_dynamics(c["id"], {"evb": evb_score(_AccessStub(c["utility"], now), now=now)})

    # The high-value memories are the ones worth promoting.
    for cid in sorted(hv_ids):
        relational.record_promotion(cid, "semantic", now=now)

    # Real Dream prune in this arm (uses the arm's importance_fn: evb or legacy).
    worker._prune(now)

    snapshot = relational.list_crystals_with_dynamics()
    by_id = {c["id"]: c for c in snapshot}
    promotions = relational.list_promotions()

    ds_ids = {c["id"] for c in snapshot if c["id"].startswith("ds-")}
    ds_evicted = sum(1 for cid in ds_ids if by_id[cid]["status"] == "deprecated")

    return {
        "promotion_precision": promotion_precision(promotions, by_id),
        "high_value_retention": high_value_retention(snapshot, high_value_ids=hv_ids),
        "distractor_eviction": (ds_evicted / len(ds_ids)) if ds_ids else None,
    }


def run(*, data_root: str = "/tmp/crystalium-evb-gate") -> dict[str, Any]:
    """Run both arms and return {axis: {on, off, delta}} + the gate verdict."""
    import os

    os.makedirs(data_root, exist_ok=True)
    on = run_arm(True, data_root=data_root)
    off = run_arm(False, data_root=data_root)

    axes: dict[str, Any] = {}
    for name in ("promotion_precision", "high_value_retention", "distractor_eviction"):
        o, f = on[name], off[name]
        delta = (o - f) if (o is not None and f is not None) else None
        axes[name] = {"on": o, "off": f, "delta": delta}

    pp = axes["promotion_precision"]["delta"]
    hv = axes["high_value_retention"]["delta"]
    beats = pp is not None and hv is not None and pp > 0 and hv > 0
    return {
        "axes": axes,
        "gate_pass": beats,
        "verdict": (
            "EVB beats legacy on BOTH gate metrics — flip evb_enabled ON"
            if beats
            else "EVB does NOT strictly beat legacy on both gate metrics — evb_enabled stays OFF"
        ),
    }
