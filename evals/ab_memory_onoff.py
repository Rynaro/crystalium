"""A/B memory-on/off harness for the CRYSTALIUM canary suite.

Runs all 10 missions in two arms: memory_on=True (live CRYSTALIUM store) and
memory_on=False (null-store). Computes the headline A/B metric:

  Δ = pass_rate(on) - pass_rate(off)  over A/B-eligible missions.

Usage:
  python -m crystalium canary --mode on_only|off_only|both

The CLI entry point in __main__.py calls run_all().
"""

from __future__ import annotations

import datetime
import json
import os
import uuid
from pathlib import Path
from typing import Any, Optional

from evals.missions import (
    AB_ARM_MISSION_IDS,
    MISSIONS,
    CanaryEnv,
    MissionResult,
)

# Path for result artefacts (relative to repo root)
RESULTS_DIR = Path(__file__).parent / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Null-store factory
# ---------------------------------------------------------------------------


def _build_null_handlers() -> dict[str, Any]:
    """Handlers that return empty/vacuous results (memory_on=False arm)."""
    return {
        "recall": lambda args: {
            "records": [],
            "total_tokens": 0,
            "slot_breakdown": {},
            "evicted_count": 0,
        },
        "commit": lambda args: {
            "status": "committed",
            "id": f"null-{uuid.uuid4()}",
            "layer": args.get("layer", "episodic"),
            "validation_state": "unverified",
            "importance": 0.0,
        },
        "update": lambda args: {
            "status": "updated",
            "id": args.get("id", ""),
            "patch_keys": list(args.get("patch", {}).keys()),
            "reason": args.get("reason", ""),
            "note": "null-arm",
        },
        "skill_invoke": lambda args: {
            "skill_id": args.get("skill_id", ""),
            "exit_code": 0,
            "stdout": "",
            "stderr": "",
            "overflow_flag": False,
            "passed": True,
        },
        "session_end": lambda args: {"enqueued": False, "dream_run_id": None},
        "get_crystal": lambda crystal_id: None,
        "row_count": lambda layer: 0,
        "dream_queue_len": lambda: 0,
        "trigger_idle_poll": lambda: None,
    }


# ---------------------------------------------------------------------------
# Live-store factory (wires to actual CRYSTALIUM handlers)
# ---------------------------------------------------------------------------


def _build_live_handlers(config_override: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Handlers wired to live CRYSTALIUM components.

    Imports are deferred to this function so that importing this module does not
    pull in heavy deps (sentence-transformers, etc.) unless explicitly requested.
    """
    from crystalium.config import Config
    from crystalium.server import (
        _build_components,
        _handle_commit,
        _handle_recall,
        _handle_session_end,
        _handle_update,
    )
    from crystalium.trust import Tier

    cfg_kwargs: dict[str, Any] = {}
    if config_override:
        cfg_kwargs.update(config_override)
    # project is a Scope-level concept, not a Config field. Track it for the
    # CanaryEnv scope filter; do NOT pass it into Config(**...).
    run_id = str(uuid.uuid4())[:8]
    project = cfg_kwargs.pop("project", f"canary-{run_id}")

    config = Config(**cfg_kwargs)
    (
        enforcement,
        aetheryte,
        episodic_layer,
        semantic_layer,
        procedural_layer,
        execution_layer,
        gate,
        scheduler,
        relational,
    ) = _build_components(config)

    default_tier = Tier.T1

    def _recall(args: dict[str, Any]) -> dict[str, Any]:
        result = _handle_recall(args, aetheryte, scheduler, default_tier)
        if hasattr(result, "model_dump"):
            return result.model_dump()
        return result

    def _commit(args: dict[str, Any]) -> dict[str, Any]:
        tier_str = args.get("payload", {}).get("trust_tier", "T1")
        try:
            from crystalium.trust import Tier as T
            tier = T.from_str(tier_str)
        except Exception:
            tier = Tier.T1
        return _handle_commit(
            args, episodic_layer, semantic_layer, procedural_layer, execution_layer, tier
        )

    def _update(args: dict[str, Any]) -> dict[str, Any]:
        return _handle_update(
            args, episodic_layer, semantic_layer, procedural_layer,
            execution_layer, enforcement, relational, default_tier
        )

    def _session_end(args: dict[str, Any]) -> dict[str, Any]:
        return _handle_session_end(args, scheduler)

    def _get_crystal(crystal_id: str) -> Optional[dict[str, Any]]:
        return enforcement._store.get_crystal(crystal_id) if hasattr(enforcement, "_store") else None

    def _row_count(layer: str) -> int:
        try:
            from crystalium.storage.relational import RelationalStore
            store: RelationalStore = enforcement._store  # type: ignore[attr-defined]
            rows = store.bm25_search(query="*", layer_filter=layer, k=10000)
            return len(rows)
        except Exception:
            return 0

    def _dream_queue_len() -> int:
        try:
            return len(scheduler._pending_runs)  # type: ignore[attr-defined]
        except Exception:
            return 0

    def _trigger_idle_poll() -> Optional[str]:
        try:
            return scheduler._poll_idle()  # type: ignore[attr-defined]
        except Exception:
            return None

    return {
        "recall": _recall,
        "commit": _commit,
        "update": _update,
        "session_end": _session_end,
        "get_crystal": _get_crystal,
        "row_count": _row_count,
        "dream_queue_len": _dream_queue_len,
        "trigger_idle_poll": _trigger_idle_poll,
    }


# ---------------------------------------------------------------------------
# Single-arm runner
# ---------------------------------------------------------------------------


def _run_arm(
    memory_on: bool,
    project_prefix: str = "canary",
    config_override: Optional[dict[str, Any]] = None,
    seed_fixture: bool = False,
) -> dict[str, MissionResult]:
    """Run all 10 missions in one arm. Returns mission_id -> MissionResult.

    config_override is merged into the live-arm Config (previously it was
    dropped); the per-run project scope is always injected. When seed_fixture is
    True the deterministic fixture repo is committed before missions run, giving
    both arms the same known baseline.
    """
    run_id = str(uuid.uuid4())[:8]
    project = f"{project_prefix}-{'on' if memory_on else 'off'}-{run_id}"

    if memory_on:
        try:
            live_override = dict(config_override or {})
            live_override["project"] = project
            handlers = _build_live_handlers(config_override=live_override)
        except Exception as exc:
            # If live handlers fail to build (e.g. missing deps), return all failures
            results: dict[str, MissionResult] = {}
            for mission in MISSIONS:
                mid = mission.__name__.split("_")[1].upper().replace("_", "-")[:5]
                results[mid] = MissionResult(
                    mission_id=mid, passed=False,
                    observed={}, expected={},
                    error=f"live_handler_build_failed: {exc}",
                )
            return results
    else:
        handlers = _build_null_handlers()

    env = CanaryEnv(memory_on=memory_on, project=project, handlers=handlers)

    if seed_fixture:
        from evals.fixture_repo import seed_fixture_repo
        seed_fixture_repo(env)

    results = {}
    for mission_fn in MISSIONS:
        try:
            result = mission_fn(env)
        except Exception as exc:
            # Unexpected mission crash — treat as fail
            # Derive CAN-N ID from function name (e.g. can_1_ -> CAN-1)
            parts = mission_fn.__name__.split("_")
            mid = f"CAN-{parts[1]}" if len(parts) > 1 and parts[1].isdigit() else mission_fn.__name__
            result = MissionResult(
                mission_id=mid, passed=False,
                observed={}, expected={},
                error=str(exc),
            )
        results[result.mission_id] = result

    return results


# ---------------------------------------------------------------------------
# Aggregate + headline metric
# ---------------------------------------------------------------------------


def _compute_headline(
    on_results: dict[str, MissionResult],
    off_results: dict[str, MissionResult],
) -> dict[str, Any]:
    """Compute per-mission and aggregate A/B metric."""
    ab_pass_on = sum(
        1 for mid, r in on_results.items() if mid in AB_ARM_MISSION_IDS and r.passed
    )
    ab_total = len(AB_ARM_MISSION_IDS)
    ab_pass_off = sum(
        1 for mid, r in off_results.items() if mid in AB_ARM_MISSION_IDS and r.passed
    )

    pass_rate_on = ab_pass_on / ab_total if ab_total > 0 else 0.0
    pass_rate_off = ab_pass_off / ab_total if ab_total > 0 else 0.0
    delta = pass_rate_on - pass_rate_off

    return {
        "ab_missions": list(AB_ARM_MISSION_IDS),
        "ab_pass_on": ab_pass_on,
        "ab_pass_off": ab_pass_off,
        "ab_total": ab_total,
        "pass_rate_on": pass_rate_on,
        "pass_rate_off": pass_rate_off,
        "delta": delta,
        "headline_pass": delta >= 0.80,
        "note": (
            "[UNVERIFIED — memory does not measurably help on this canary set]"
            if delta < 0.80 else "PASS — memory_on beats memory_off by >={:.0%}".format(delta)
        ),
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_all(
    memory_on: bool = True,
    mode: str = "both",
    write_results: bool = True,
    config_override: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Run canary missions and return the aggregate result dict.

    Args:
        memory_on:   Passed to on-arm only when mode="on_only". Ignored for "both".
        mode:        "both" | "on_only" | "off_only"
        write_results: If True, writes a timestamped JSON to evals/results/.
        config_override: Optional config overrides for the live arm.

    Returns:
        Dict with "on_results", "off_results" (may be empty for single-arm modes),
        "headline", and "timestamp".
    """
    timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()

    on_results: dict[str, dict[str, Any]] = {}
    off_results: dict[str, dict[str, Any]] = {}

    if mode in ("both", "on_only"):
        raw = _run_arm(memory_on=True, config_override=config_override)
        on_results = {
            mid: {
                "passed": r.passed,
                "observed": r.observed,
                "expected": r.expected,
                "notes": r.notes,
                "error": r.error,
            }
            for mid, r in raw.items()
        }

    if mode in ("both", "off_only"):
        raw = _run_arm(memory_on=False, config_override=config_override)
        off_results = {
            mid: {
                "passed": r.passed,
                "observed": r.observed,
                "expected": r.expected,
                "notes": r.notes,
                "error": r.error,
            }
            for mid, r in raw.items()
        }

    # For headline metric we need both arms; degrade gracefully for single-arm runs
    if mode == "both":
        on_mr = _run_arm(memory_on=True, config_override=config_override)
        off_mr = _run_arm(memory_on=False, config_override=config_override)
        headline = _compute_headline(on_mr, off_mr)
    else:
        headline = {
            "note": f"single-arm mode={mode!r}; headline requires both arms",
            "headline_pass": None,
            "delta": None,
        }

    aggregate = {
        "timestamp": timestamp,
        "mode": mode,
        "on_results": on_results,
        "off_results": off_results,
        "headline": headline,
        "mission_count": len(MISSIONS),
        "ab_arm_missions": list(AB_ARM_MISSION_IDS),
    }

    if write_results:
        ts_safe = timestamp.replace(":", "-").replace("+", "Z")
        result_path = RESULTS_DIR / f"{ts_safe}.json"
        result_path.write_text(json.dumps(aggregate, indent=2, default=str))

    return aggregate
