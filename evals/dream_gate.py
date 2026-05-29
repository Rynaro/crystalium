"""Deterministic W3 Dream-intelligence ablation gate (evals/dream_gate.py).

The canary can't evaluate the Dream augments (no consolidation, no eviction). This
runs a controlled, labeled-fact population through the real gather->consolidate
->prune pipeline in each arm and computes the W3 gate metrics over ground-truth
labels — fair in both arms. Template = evals/evb_gate.py (W2).

Population: 3 "fact groups" (each 3 episodes with DISTINCT author_agents so the
gate gets >=k=3 independent witnesses and admits fire) carrying a distinctive
ground-truth token; plus one STC group of 2 episodes (one salient high-EVB, one
within the window, distinct authors) which has only 2 witnesses — it admits ONLY
if STC lowers k to 2. A FakeGraph links each group's episodes so _gather forms
one cluster per group.

Two arm-pairs:
  (i)  chrono            vs  replay_evb+interleave  -> consolidation_gain, semantic_drift
  (ii) stc off           vs  stc on                 -> useful_context_retention, consolidation_gain
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from evals.metrics import (
    consolidation_gain,
    semantic_drift,
    useful_context_retention,
)

_T0 = datetime(2026, 2, 1, 12, 0, 0, tzinfo=timezone.utc)

# fact groups: (token, n_episodes, salient) — salient marks the STC group
_GROUPS = [
    ("argon2id", 3, False),
    ("blue-green", 3, False),
    ("pgbouncer", 3, False),
    ("stcsalient", 2, True),  # only 2 witnesses -> needs STC to admit
]


class _FakeGraph:
    """neighbor_expand returns the other episode ids in the same fact group."""

    def __init__(self, groups: dict[str, list[str]]):
        self._of = {cid: [x for x in ids if x != cid] for ids in groups.values() for cid in ids}

    def neighbor_expand(self, seed_ids, depth=1):
        out: list[str] = []
        for s in seed_ids:
            out.extend(self._of.get(s, []))
        return out


def _episode(cid: str, token: str, author: str, *, created_at: datetime,
             outcome: float, novelty: float, access: int) -> dict:
    return {
        "id": cid,
        "layer": "episodic",
        "trust_tier": "T1",
        "validation_state": "validated",
        "status": "active",
        "summary": f"fact about {token} system",
        "scope": {"project": "dream-gate"},
        "provenance": {"source": "verified_agent", "author_agent": author,
                       "created_at": created_at.isoformat()},
        "utility": {"access_count": access, "last_access": created_at.isoformat(),
                    "outcome_success_score": outcome, "importance": 0.0,
                    "novelty_at_write": novelty},
        "temporal": {"t_valid_from": created_at.isoformat()},
    }


def _seed_population(relational) -> tuple[dict[str, list[str]], list[dict], set[str]]:
    """Insert the labeled population. Returns (group->ids, consolidation labels, stc ids)."""
    groups: dict[str, list[str]] = {}
    labels: list[dict] = []   # {token, expected_tokens}
    stc_ids: set[str] = set()
    for gi, (token, n, salient) in enumerate(_GROUPS):
        ids: list[str] = []
        for ei in range(n):
            cid = f"{token}-{ei}"
            # salient group: first episode high-EVB, all within a tight window
            ca = _T0 + timedelta(seconds=gi * 10 + ei)
            outcome = 0.95 if (salient and ei == 0) else 0.6
            novelty = 0.9 if salient else 0.5
            access = 40 if (salient and ei == 0) else 5
            relational.insert_crystal(
                _episode(cid, token, author=f"agent-{token}-{ei}", created_at=ca,
                         outcome=outcome, novelty=novelty, access=access)
            )
            ids.append(cid)
        groups[token] = ids
        labels.append({"token": token, "expected_tokens": [token]})
        if salient:
            stc_ids.update(ids)
    return groups, labels, stc_ids


def _semantic_active(relational) -> int:
    return sum(
        1 for c in relational.list_crystals_with_dynamics()
        if c["layer"] == "semantic" and c["status"] == "active"
    )


def run_arm(*, replay_evb: bool, interleave: bool, stc: bool, data_root: str) -> dict[str, Any]:
    from pathlib import Path

    from crystalium.config import Config
    from crystalium.server import _build_components

    now = _T0 + timedelta(seconds=1)
    cfg = Config(
        data_dir=Path(data_root) / f"dg-{replay_evb}{interleave}{stc}-{uuid.uuid4().hex[:8]}",
        dream_replay_evb=replay_evb, dream_interleave=interleave,
        dream_stc=stc, stc_threshold=0.3, stc_window_s=3600,
        rate_limit_per_minute=100000,
    )
    (_enf, _aeth, _ep, _sem, _proc, _exec, _gate, scheduler, relational) = _build_components(cfg)
    worker = scheduler.worker

    groups, labels, stc_ids = _seed_population(relational)
    worker.graph_store = _FakeGraph(groups)  # link each group's episodes

    before = _semantic_active(relational)

    # Run the pipeline phase-by-phase (deterministic now). Capture consolidation
    # summaries for the drift metric.
    clusters = worker._gather(now)
    consolidations: list[dict] = []
    for cluster in clusters:
        proposed = worker._consolidate(cluster, now)
        if proposed is not None:
            # expected tokens = every group token whose episode is in this cluster
            toks = [lab["token"] for lab in labels
                    if any(cid.startswith(lab["token"]) for cid in
                           [r.get("id", "") for r in cluster])]
            consolidations.append({"summary": proposed.get("summary", ""),
                                   "expected_tokens": toks})
    worker._prune(now)

    after = _semantic_active(relational)
    snapshot = relational.list_crystals_with_dynamics()
    promotions = relational.list_promotions()

    return {
        # Admitted-consolidation count is the gain signal: the Dream PROPOSES (gate
        # admits + records a promotion) but does not insert the semantic crystal,
        # so semantic-row growth stays 0 — the promotions ledger / admitted count
        # is the honest measure. (semantic_row_growth kept as a secondary signal.)
        "consolidation_gain": len(consolidations),
        "promotions": len(promotions),
        "semantic_row_growth": consolidation_gain(before, after),
        "semantic_drift": semantic_drift(consolidations),
        "useful_context_retention": useful_context_retention(snapshot, stc_ids),
        "n_consolidations": len(consolidations),
    }


def run(*, data_root: str = "/tmp/crystalium-dream-gate") -> dict[str, Any]:
    import os

    os.makedirs(data_root, exist_ok=True)

    chrono = run_arm(replay_evb=False, interleave=False, stc=False, data_root=data_root)
    smart = run_arm(replay_evb=True, interleave=True, stc=False, data_root=data_root)
    stc_off = run_arm(replay_evb=False, interleave=False, stc=False, data_root=data_root)
    stc_on = run_arm(replay_evb=False, interleave=False, stc=True, data_root=data_root)

    def _delta(a, b):
        return (a - b) if (a is not None and b is not None) else None

    gate_i = {
        "consolidation_gain": {"on": smart["consolidation_gain"], "off": chrono["consolidation_gain"],
                               "delta": _delta(smart["consolidation_gain"], chrono["consolidation_gain"])},
        "semantic_drift": {"on": smart["semantic_drift"], "off": chrono["semantic_drift"],
                           "delta": _delta(smart["semantic_drift"], chrono["semantic_drift"])},
    }
    gate_ii = {
        "useful_context_retention": {"on": stc_on["useful_context_retention"],
                                     "off": stc_off["useful_context_retention"],
                                     "delta": _delta(stc_on["useful_context_retention"],
                                                     stc_off["useful_context_retention"])},
        "consolidation_gain": {"on": stc_on["consolidation_gain"], "off": stc_off["consolidation_gain"],
                               "delta": _delta(stc_on["consolidation_gain"], stc_off["consolidation_gain"])},
    }

    g1 = gate_i["consolidation_gain"]["delta"]
    d1 = gate_i["semantic_drift"]["delta"]
    gate_i_pass = (g1 is not None and g1 > 0) and (d1 is not None and d1 <= 0)
    r2 = gate_ii["useful_context_retention"]["delta"]
    gate_ii_pass = r2 is not None and r2 > 0

    return {
        "gate_i_replay_interleave": gate_i,
        "gate_ii_stc": gate_ii,
        "gate_i_pass": gate_i_pass,
        "gate_ii_pass": gate_ii_pass,
        "verdict": {
            "dream_replay_evb+dream_interleave": "FLIP ON" if gate_i_pass else "stays OFF (no strict win)",
            "dream_stc": "FLIP ON" if gate_ii_pass else "stays OFF (no strict win)",
        },
    }
