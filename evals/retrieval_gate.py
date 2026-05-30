"""Deterministic W5 retrieval-faculty ablation gate (seeded multi-hop fixture).

Builds a small KNOWN-topology corpus in one project and measures multi-hop
recall F1 across arms — flat RRF (both faculties off) vs completion / context /
both on. The topology is a lexical *hub* that the query matches via BM25, plus
*spokes* that are ground-truth relevant but NOT lexically matched — reachable
only through seeded LINKS_TO edges (1 and 2 hops). A *context* pair both match
the query lexically; one carries an encoding_context matching the query scope.

Honest ablation (D6.4-i): completion flips on only if its arm lifts multi-hop F1
over flat; context_match flips on only if it lifts the context-relevant rank.
Edges are seeded in EVERY arm, so the only variable is whether the recall walk /
re-rank runs — isolating the faculty, not the fixture. Falls back to an
"inconclusive" verdict when the graph store is the null stub (no real edges).
Template = forgetting_gate.py.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from evals.metrics import precision_recall_f1

_T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
_PROJECT = "retrieval-gate"
_QUERY = "acme login session token rotation"
_AGENT_CLASS = "backend"


def _commit(episodic, summary: str, tier, *, enc_ctx: dict | None = None) -> str:
    payload: dict[str, Any] = {"summary": summary, "scope": {"project": _PROJECT,
                                                             "agent_class_visibility": _AGENT_CLASS}}
    if enc_ctx is not None:
        payload["encoding_context"] = enc_ctx
    res = episodic.commit(
        payload=payload,
        provenance={"source": "verified_agent", "author_agent": "rg",
                    "created_at": _T0.isoformat()},
        caller_tier=tier,
    )
    return res.get("id", "")


def run_arm(*, completion: bool, context_match: bool, data_root: str) -> dict[str, Any]:
    from pathlib import Path

    from crystalium.config import Config
    from crystalium.schemas import Scope
    from crystalium.server import _build_components
    from crystalium.trust import Tier

    tag = f"{int(completion)}{int(context_match)}"
    cfg = Config(
        data_dir=Path(data_root) / f"rg-{tag}-{uuid.uuid4().hex[:8]}",
        recall_completion=completion,
        recall_context_match=context_match,
        completion_max_hops=2,
        completion_decay=0.5,
        rate_limit_per_minute=1_000_000,
    )
    (_enf, aetheryte, episodic, _sem, _proc, _exec, _gate, _sched, _rel) = _build_components(cfg)
    graph = aetheryte.graph_store

    # Hub matches the query lexically; spokes do NOT (reachable only via edges).
    hub = _commit(episodic, "acme login session token rotation runbook", Tier.T1)
    spoke1 = _commit(episodic, "rollback procedure for credential store", Tier.T1)
    spoke2 = _commit(episodic, "incident postmortem 2025 outage", Tier.T1)
    _noise1 = _commit(episodic, "unrelated billing invoice notes", Tier.T1)
    _noise2 = _commit(episodic, "frontend css grid layout tips", Tier.T1)

    # Context pair: both lexically match the query; one matches the scope context.
    ctx_match = _commit(episodic, "acme login session token guide",
                        Tier.T1, enc_ctx={"project": _PROJECT, "agent_class": _AGENT_CLASS})
    ctx_off = _commit(episodic, "acme login session token notes",
                     Tier.T1, enc_ctx={"project": "other", "agent_class": "frontend"})

    # Seed a known 2-hop chain hub -> spoke1 -> spoke2 in EVERY arm.
    graph_ok = True
    try:
        for a, b in ((hub, spoke1), (spoke1, spoke2)):
            graph.add_node(crystal_id=a, layer="episodic")
            graph.add_node(crystal_id=b, layer="episodic")
            graph.add_edge(a, b, "LINKS_TO")
        graph_ok = bool(graph.decaying_walk([hub], max_hops=2, decay=0.5))
    except Exception:
        graph_ok = False

    relevant = [hub, spoke1, spoke2]
    try:
        result = aetheryte.recall(
            Scope(project=_PROJECT, agent_class_visibility=_AGENT_CLASS),
            _QUERY, 10, None, Tier.T1,
        )
        retrieved = [r.id for r in result.records]
    except Exception:
        retrieved = []

    prf = precision_recall_f1(retrieved, relevant)
    # Context rank: 0-based position of the context-matching crystal (lower = better).
    ctx_rank = retrieved.index(ctx_match) if ctx_match in retrieved else None
    return {
        "f1": prf["f1"],
        "recall": prf["recall"],
        "precision": prf["precision"],
        "ctx_rank": ctx_rank,
        "graph_ok": graph_ok,
        "n_retrieved": len(retrieved),
    }


def run(*, data_root: str = "/tmp/crystalium-retrieval-gate") -> dict[str, Any]:
    import os

    os.makedirs(data_root, exist_ok=True)
    flat = run_arm(completion=False, context_match=False, data_root=data_root)
    comp = run_arm(completion=True, context_match=False, data_root=data_root)
    ctx = run_arm(completion=False, context_match=True, data_root=data_root)
    both = run_arm(completion=True, context_match=True, data_root=data_root)

    def _gt(a, b):
        return a is not None and b is not None and a > b

    def _lt(a, b):
        return a is not None and b is not None and a < b

    completion_ok = _gt(comp["f1"], flat["f1"])
    # context wins if it ranks the context-matching crystal strictly earlier.
    context_ok = _lt(ctx["ctx_rank"], flat["ctx_rank"])

    graph_ok = flat["graph_ok"] and comp["graph_ok"]
    axes = {
        "multihop_f1": {"flat": flat["f1"], "completion": comp["f1"], "both": both["f1"]},
        "context_rank": {"flat": flat["ctx_rank"], "context": ctx["ctx_rank"], "both": both["ctx_rank"]},
    }
    return {
        "axes": axes,
        "graph_ok": graph_ok,
        "completion_pass": completion_ok and graph_ok,
        "context_pass": context_ok,
        "gate_pass": (completion_ok and graph_ok) or context_ok,
        "verdict": (
            "INCONCLUSIVE — graph store is the null stub (no real edges); faculties stay OFF"
            if not graph_ok else
            f"completion {'lifts' if completion_ok else 'does NOT lift'} multi-hop F1; "
            f"context_match {'lifts' if context_ok else 'does NOT lift'} context rank — "
            "flip only the winning flag(s)"
        ),
    }
