"""Deterministic W5 retrieval-faculty ablation gate (seeded multi-hop fixture).

Builds a small KNOWN-topology corpus in one project and measures multi-hop
recall F1 across arms — flat RRF (both faculties off) vs completion / context /
both on. The topology is a lexical *hub* that the query matches via BM25, plus
*spokes* that are ground-truth relevant but NOT lexically matched — reachable
only through seeded LINKS_TO edges (1 and 2 hops). A *context* pair both match
the query lexically; one carries an encoding_context matching the query scope.

Honest ablation (D6.4-i): completion flips on only if its arm lifts multi-hop F1
over flat; context_match flips on only if it lifts the context-relevant rank.

Deconfounded (crystalium#43): `Config.link_cooccurrence` is pinned OFF in
EVERY arm (decoupled from the `recall_completion` flag under test — see
`server.py::_build_components`'s `link_cooccurrence=` resolution), so the
only edges in the store in ANY arm are the two explicit `hub -> spoke1 ->
spoke2` edges seeded below. The recall walk / re-rank is now genuinely the
only variable between arms — the isolation claim this docstring makes is
falsifiable, not asserted: `resolve_verdict` re-measures each arm's
post-commit edge count and returns verdict "confounded" (never numbers) the
moment any arm drifts from the expected 2. Each commit also gets a
strictly-increasing `created_at` stamp, so `recent_crystal_ids`'s unindexed,
tiebreak-free `ORDER BY created_at DESC` is never a total tie here.

Falls back to verdict "inconclusive" — also never numbers — when the
sentence-transformers embedding backend is unavailable (crystalium#54, N-5:
under CRYSTALIUM_SKIP_SLOW the dense arm silently goes empty in every arm
while the gate keeps emitting confident F1/rank numbers) or to the legacy
"INCONCLUSIVE — graph store is the null stub" text when the graph store has
no real edges (faculties stay OFF either way). Template = forgetting_gate.py.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator

from evals.metrics import precision_recall_f1

_T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
_PROJECT = "retrieval-gate"
_QUERY = "acme login session token rotation"
_AGENT_CLASS = "backend"

# crystalium#43 isolation self-check: the only edges any arm should carry are
# the explicit hub -> spoke1 -> spoke2 chain seeded in run_arm(). Any arm
# whose post-commit edge count differs from this is a confound, not a faculty
# effect (measured pre-fix: flat/ctx 2, comp/both ~142 — see BENCH-NOTES).
_EXPECTED_EDGES = 2

# Lexically-close DISTRACTORS (share query words, NOT relevant, NOT graph-linked).
# With a small corpus + k=10 the spokes were already dense-recalled, so completion
# could add nothing (the ledger's "k >= |corpus|" confound). These distractors fill
# flat's top-k and push the graph-only-reachable spokes OUT of dense range, so the
# multi-hop walk has a real gap to recover. The spokes share NO query words, so they
# rank below every distractor under dense recall.
_DISTRACTORS = [
    "acme login page redesign mockups", "session token cookie consent banner",
    "acme login button hover animation", "token rotation marketing announcement",
    "login screen accessibility audit notes", "session timeout copy rewrite",
    "acme token branded swag inventory", "login analytics dashboard palette",
    "acme session lounge furniture order", "token vending machine restock",
    "login form placeholder text review", "session replay tooling comparison",
    "acme login onboarding email draft", "rotation roster for support shifts",
    "token economy whitepaper summary", "login latency status page wording",
    "acme session sponsorship contract", "token gating landing page hero",
    "login confetti animation timing", "session cookie GDPR FAQ entry",
    "acme login wallpaper design set", "token launch party guest list",
    "rotation of conference booth staff", "login splash gradient picker",
]


def _stamp_sequence(
    start: datetime = _T0, step: timedelta = timedelta(seconds=1)
) -> Iterator[str]:
    """Infinite strictly-increasing ISO-8601 `created_at` stamp generator.

    Kills the fixture's former `_T0` total tie (every one of the 31 commits
    shared one timestamp): `recent_crystal_ids` (relational.py) orders
    `created_at DESC LIMIT ?` with no tiebreak and no index, so "the 5 most
    recent" was an artefact of SQLite's scan order, not a real recency
    window (crystalium#43).
    """
    t = start
    while True:
        yield t.isoformat()
        t += step


def resolve_verdict(
    *, edge_counts: dict[str, int], expected_edges: int, embeddings_ok: bool
) -> str:
    """Pure verdict classifier (design constraint D-1, crystalium#43 / #54).

    No I/O, no model, no container state — every branch is reachable from
    plain in-memory inputs, which is what makes the isolation self-check and
    the N-5 honesty path actually falsifiable rather than merely asserted.

    Precedence is fixed and total:
      1. `embeddings_ok` is False       -> "inconclusive" (N-5, crystalium#54:
         never emit confident numbers off an empty dense arm).
      2. any arm's edge count differs from `expected_edges` -> "confounded"
         (crystalium#43 isolation self-check: a gate that cannot fail on the
         confound it names is not a gate).
      3. otherwise                      -> "isolated" (the honest
         classification; `run()` layers the descriptive F1/rank text for
         this arm on top of it).
    """
    if not embeddings_ok:
        return "inconclusive"
    if any(count != expected_edges for count in edge_counts.values()):
        return "confounded"
    return "isolated"


def _embeddings_available(data_root: str) -> bool:
    """Probe the dense-embedding backend BEFORE any arm commits its corpus.

    A single throwaway embed call, isolated from the four gate arms. A raise
    here (e.g. CRYSTALIUM_SKIP_SLOW=1 — `vector.py`'s `_load_model` guard)
    is caught up front so the gate can refuse to emit numbers at all
    (crystalium#54, N-5), rather than letting all 31 x 4 commits proceed
    with `if vec:` silently skipping every vector upsert and the gate then
    printing confident F1/rank numbers off a dense arm that was empty the
    whole time.
    """
    from pathlib import Path

    from crystalium.storage.vector import VectorStore

    probe_dir = Path(data_root) / f"rg-embed-probe-{uuid.uuid4().hex[:8]}" / "lance"
    try:
        VectorStore(lance_dir=probe_dir).embed("crystalium retrieval-gate embed probe")
        return True
    except Exception:
        return False


def _commit(
    episodic, summary: str, tier, *, created_at: str, enc_ctx: dict | None = None
) -> str:
    payload: dict[str, Any] = {"summary": summary, "scope": {"project": _PROJECT,
                                                             "agent_class_visibility": _AGENT_CLASS}}
    if enc_ctx is not None:
        payload["encoding_context"] = enc_ctx
    res = episodic.commit(
        payload=payload,
        provenance={"source": "verified_agent", "author_agent": "rg",
                    "created_at": created_at},
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
        # crystalium#43: pin graph writes OFF in EVERY arm, decoupled from the
        # `completion` flag under test. Without this, server.py's
        # `link_cooccurrence=config.recall_completion` wiring makes the arm
        # flag ALSO the commit-time graph-write flag, so "completion vs
        # flat" compared two different graphs (2 edges vs ~142) rather than
        # ablating one faculty on one fixed topology.
        link_cooccurrence=False,
    )
    (_enf, aetheryte, episodic, _sem, _proc, _exec, _gate, _sched, _rel) = _build_components(cfg)
    graph = aetheryte.graph_store
    relational = aetheryte.relational
    stamps = _stamp_sequence()

    # Hub matches the query lexically; spokes do NOT (reachable only via edges).
    hub = _commit(episodic, "acme login session token rotation runbook", Tier.T1,
                  created_at=next(stamps))
    spoke1 = _commit(episodic, "rollback procedure for credential store", Tier.T1,
                     created_at=next(stamps))
    spoke2 = _commit(episodic, "incident postmortem 2025 outage", Tier.T1,
                     created_at=next(stamps))
    noise1 = _commit(episodic, "unrelated billing invoice notes", Tier.T1,
                     created_at=next(stamps))
    noise2 = _commit(episodic, "frontend css grid layout tips", Tier.T1,
                     created_at=next(stamps))
    # Lexically-close distractors so k=10 dense recall fills up WITHOUT the spokes
    # (which share no query words) — the multi-hop walk then has a gap to recover.
    distractor_ids = [
        _commit(episodic, _d, Tier.T1, created_at=next(stamps)) for _d in _DISTRACTORS
    ]

    # Context pair: both lexically match the query; one matches the scope context.
    ctx_match = _commit(episodic, "acme login session token guide",
                        Tier.T1, created_at=next(stamps),
                        enc_ctx={"project": _PROJECT, "agent_class": _AGENT_CLASS})
    ctx_off = _commit(episodic, "acme login session token notes",
                     Tier.T1, created_at=next(stamps),
                     enc_ctx={"project": "other", "agent_class": "frontend"})

    all_ids = [hub, spoke1, spoke2, noise1, noise2, *distractor_ids, ctx_match, ctx_off]

    # Seed a known 2-hop chain hub -> spoke1 -> spoke2 — with link_cooccurrence
    # pinned off above, these are the ONLY edges in the store in ANY arm.
    graph_ok = True
    edge_count = 0
    try:
        for a, b in ((hub, spoke1), (spoke1, spoke2)):
            graph.add_node(crystal_id=a, layer="episodic")
            graph.add_node(crystal_id=b, layer="episodic")
            graph.add_edge(a, b, "LINKS_TO")
        graph_ok = bool(graph.decaying_walk([hub], max_hops=2, decay=0.5))
        edge_count = len(graph.all_edges())
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
    created_at_values = [relational.get_crystal(cid)["created_at"] for cid in all_ids]
    return {
        "f1": prf["f1"],
        "recall": prf["recall"],
        "precision": prf["precision"],
        "ctx_rank": ctx_rank,
        "graph_ok": graph_ok,
        "n_retrieved": len(retrieved),
        "edge_count": edge_count,
        "created_at_values": created_at_values,
    }


def run(*, data_root: str = "/tmp/crystalium-retrieval-gate") -> dict[str, Any]:
    import os

    os.makedirs(data_root, exist_ok=True)

    # N-5 (crystalium#54): probe embeddings BEFORE any arm runs. A raise here
    # (CRYSTALIUM_SKIP_SLOW=1) means the dense arm would be empty in every
    # gate arm — refuse to emit numbers rather than print a confident,
    # BM25+graph-only F1/rank that measures the wrong thing.
    embeddings_ok = _embeddings_available(data_root)
    if not embeddings_ok:
        verdict = resolve_verdict(
            edge_counts={}, expected_edges=_EXPECTED_EDGES, embeddings_ok=False
        )
        return {
            "axes": None,
            "graph_ok": None,
            "completion_pass": None,
            "context_pass": None,
            "gate_pass": None,
            "verdict": verdict,
            "reason": (
                "embeddings unavailable (CRYSTALIUM_SKIP_SLOW or model "
                "unreachable) — dense arm would be empty in every arm; "
                "refusing to emit numbers (crystalium#54, N-5)"
            ),
        }

    flat = run_arm(completion=False, context_match=False, data_root=data_root)
    comp = run_arm(completion=True, context_match=False, data_root=data_root)
    ctx = run_arm(completion=False, context_match=True, data_root=data_root)
    both = run_arm(completion=True, context_match=True, data_root=data_root)

    graph_ok = flat["graph_ok"] and comp["graph_ok"]
    edge_counts = {
        "flat": flat["edge_count"], "comp": comp["edge_count"],
        "ctx": ctx["edge_count"], "both": both["edge_count"],
    }

    # crystalium#43 isolation self-check. Only meaningful when the graph
    # backend is real (graph_ok) — the pre-existing null-stub INCONCLUSIVE
    # branch below already covers the "no real edges anywhere" case, and
    # must stay reachable rather than being shadowed by "confounded" (a
    # null stub trivially reports 0 edges in every arm, which is a
    # different failure mode from a topology confound).
    if graph_ok:
        isolation = resolve_verdict(
            edge_counts=edge_counts, expected_edges=_EXPECTED_EDGES, embeddings_ok=True,
        )
        if isolation == "confounded":
            return {
                "axes": None,
                "graph_ok": graph_ok,
                "completion_pass": None,
                "context_pass": None,
                "gate_pass": None,
                "verdict": "confounded",
                "edge_counts": edge_counts,
            }

    def _gt(a, b):
        return a is not None and b is not None and a > b

    def _lt(a, b):
        return a is not None and b is not None and a < b

    completion_ok = _gt(comp["f1"], flat["f1"])
    # context wins if it ranks the context-matching crystal strictly earlier.
    context_ok = _lt(ctx["ctx_rank"], flat["ctx_rank"])

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
        "edge_counts": edge_counts,
    }
