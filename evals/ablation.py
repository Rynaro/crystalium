"""Generalized ablation bench (W1 Objective 4).

    ab(flag, missions) -> {axis: (on, off, delta)}

Runs the canary missions twice — feature `flag` ON vs OFF — against the
deterministic seeded fixture repo, and reports the delta per axis. This is the
arbiter every later wave's augment must beat (ablation-or-revert): a wave's
algorithm ships behind a Config flag defaulted OFF and only flips on if ab()
shows it beating the prior version on the wave's named metric.

Two flag modes:
  - flag="memory" (sentinel): legacy memory-on/off — OFF arm uses null handlers
    (no store at all). Reproduces the v0.1 headline for parity.
  - flag=<Config bool field> (e.g. "reranker_enabled"): both arms run live; the
    named Config flag is toggled True vs False via config_override.

compute_axes() is pure (takes two {mission_id: MissionResult} dicts) so the axis
math is unit-tested without the heavy live store.
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path
from typing import Any

from evals.ab_memory_onoff import RESULTS_DIR, _run_arm
from evals.metrics import high_value_retention, promotion_precision
from evals.missions import AB_ARM_MISSION_IDS, MissionResult

MEMORY_FLAG = "memory"


def _pass_rate(results: dict[str, MissionResult], mission_ids: list[str]) -> float:
    if not mission_ids:
        return 0.0
    hits = sum(1 for mid in mission_ids if results.get(mid) and results[mid].passed)
    return hits / len(mission_ids)


def compute_axes(
    on_results: dict[str, MissionResult],
    off_results: dict[str, MissionResult],
    mission_ids: list[str],
) -> dict[str, tuple[float, float, float]]:
    """Pure axis computation. Returns {axis: (on, off, delta)} over mission_ids."""
    pr_on = _pass_rate(on_results, mission_ids)
    pr_off = _pass_rate(off_results, mission_ids)
    # average_accuracy over the scored set == pass_rate here (single observation
    # per task); kept as a distinct named axis for parity with metrics.py.
    return {
        "pass_rate": (pr_on, pr_off, pr_on - pr_off),
        "average_accuracy": (pr_on, pr_off, pr_on - pr_off),
    }


def _snapshot_axes(handlers: dict, evb_threshold: float) -> dict[str, float | None]:
    """W2 store-snapshot axes for one arm, read from the live handlers."""
    if not handlers:
        return {"promotion_precision": None, "high_value_retention": None}
    promotions = handlers.get("list_promotions", lambda: [])()
    crystals = handlers.get("list_crystals_with_dynamics", lambda: [])()
    by_id = {c["id"]: c for c in crystals}
    return {
        "promotion_precision": promotion_precision(promotions, by_id),
        "high_value_retention": high_value_retention(crystals, evb_threshold=evb_threshold),
    }


def ab(
    flag: str,
    missions: list[str] | None = None,
    *,
    config_override: dict[str, Any] | None = None,
    seed_fixture: bool = True,
    write_results: bool = False,
    evb_threshold: float = 0.5,
) -> dict[str, Any]:
    """Run an A/B ablation for `flag` over `missions`; return {axis:(on,off,delta)}.

    Args:
        flag:        "memory" (sentinel) or a Config boolean field name.
        missions:    CAN-N ids to score (default: the A/B arm set).
        config_override: extra Config kwargs applied to both arms.
        seed_fixture: seed the deterministic fixture repo before each arm.
        write_results: persist a JSON artefact to evals/results/.
        evb_threshold: high-EVB cut for high_value_retention (recorded in output).

    Axes include pass_rate/average_accuracy (mission pass) and the W2 gate axes
    promotion_precision + high_value_retention (store snapshots). A snapshot axis
    delta is None when either arm is undefined (e.g. no promotions on the canary).
    """
    mission_ids = list(missions) if missions else sorted(AB_ARM_MISSION_IDS)
    base = dict(config_override or {})

    if flag == MEMORY_FLAG:
        on, on_h = _run_arm(memory_on=True, config_override=base or None,
                            seed_fixture=seed_fixture, return_handlers=True)
        off, off_h = _run_arm(memory_on=False, config_override=base or None,
                             seed_fixture=seed_fixture, return_handlers=True)
    else:
        on, on_h = _run_arm(memory_on=True, config_override={**base, flag: True},
                            seed_fixture=seed_fixture, return_handlers=True)
        off, off_h = _run_arm(memory_on=True, config_override={**base, flag: False},
                             seed_fixture=seed_fixture, return_handlers=True)

    axes = compute_axes(on, off, mission_ids)

    # W2 store-snapshot gate axes (promotion_precision, high_value_retention).
    on_snap = _snapshot_axes(on_h, evb_threshold)
    off_snap = _snapshot_axes(off_h, evb_threshold)
    for name in ("promotion_precision", "high_value_retention"):
        o, f = on_snap[name], off_snap[name]
        delta = (o - f) if (o is not None and f is not None) else None
        axes[name] = (o, f, delta)

    result: dict[str, Any] = {
        "timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
        "flag": flag,
        "missions": mission_ids,
        "evb_threshold": evb_threshold,
        "axes": {k: {"on": v[0], "off": v[1], "delta": v[2]} for k, v in axes.items()},
        "per_mission_on": {mid: on[mid].passed for mid in on},
        "per_mission_off": {mid: off[mid].passed for mid in off},
    }

    if write_results:
        ts_safe = result["timestamp"].replace(":", "-").replace("+", "Z")
        path: Path = RESULTS_DIR / f"ablation-{flag}-{ts_safe}.json"
        path.write_text(json.dumps(result, indent=2, default=str))

    return result
