"""crystalium#38 -- deterministic fusion-quality eval gate (AC-125/126/138/139).

An A/B: weighted vs unweighted fusion over IDENTICAL corpora -- the only
variable is the flag (honest-ablation discipline, retrieval_gate.py:11-14).

Real `RelationalStore` (genuine BM25/FTS5) + real `GraphStore` (genuine Kuzu
graph, real `neighbor_expand`/`decaying_walk`) + a DETERMINISTIC STUB vector
arm (deliberation.md C-4: BGE-m3 puts the distinctive-token target at dense
rank 1, not 4, making the reverted-build RED unobtainable with a real
embedder -- the criteria require only the graph store to be real at this
Layer-3 gate, so the vector arm is pinned).

Topology -- designed AROUND anomaly A (deliberation.md section 3-A:
`neighbor_expand` wraps its whole `for seed_id in seed_ids` loop in ONE try,
and Kuzu RAISES at cursor exhaustion instead of returning None, so the first
seed to exhaust aborts the ENTIRE call -- in practice `neighbor_expand(seeds)
== neighbor_expand([seeds[0]])`. This is a REAL, measured, pre-existing
defect, out of scope (`storage/graph.py` is not a declared glob, C-1)):

  target   -- the SOLE BM25 hit (rank 1), NOT in the dense arm at all -- ONE
             base arm, `1/61 = 0.016393` on the REVERTED path (unweighted,
             no boost); boosted on the FIXED path (D3's selectivity boost:
             n_sparse=1 is maximally selective, so `w_sparse` is well above
             1.0 with only 5 crystals in the store).
  N1/N2/N3 -- dense ranks 1-3, no lexical match, EACH with an edge to `Z`
             (the SAME destination from all three, not just one -- see
             below). N1 is deterministically `dense_ranking[0]`.
  Z        -- a PHANTOM node: no sparse or dense presence, discoverable only
             via the graph.

  Reliability, not luck -- `target` is DELIBERATELY absent from the dense
  arm, so on the REVERTED path `seed_ids` (the ORDERED LIST
  `dense_ranking[:fetch_width]`) is `[N1, N2, N3]` only. `neighbor_expand`
  walks that list in order, so `N1` (index 0) is reliably queried and its
  edge to `Z` reliably fires (graph vote, `1/61`). `decaying_walk`
  (completion) instead converts `seed_ids` to a **set** before picking which
  seed to expand first (hash-order, not list-order) -- but since ALL THREE
  of N1/N2/N3 carry the SAME edge and `target` is not even a candidate for
  that pick, the hash choice is a lottery with NO losing ticket: whichever
  of the three it lands on, it still reaches `Z` (completion vote, another
  `1/61`). Two votes, `2/61 = 0.032787`, beats target's ONE-arm,
  unboosted `1/61 = 0.016393` with a wide, non-tied margin, on every run
  (verified across `PYTHONHASHSEED` 0-5 and unset, C-2).

  On the FIXED (weighted) path, `target` (boosted, its only arm) leads the
  base-arm `prelim` against N1/N2/N3's unboosted single dense arm each --
  `target` is `seed_ids[0]` there, not N1; target has no out-edges, so the
  ORDERED graph arm dies immediately (exhausts on target's own empty query
  before ever reaching N1). `decaying_walk`'s hash-order pick might still
  land on N1/N2/N3 and find `Z` via completion alone -- but D2's
  derived-family MIN-RANK MERGE collapses graph+completion into ONE vote
  regardless of which channel(s) found it, so `Z`'s fixed score is capped at
  `w_derived/61 = 1/61 = 0.016393` either way -- safely below target's
  boosted score (>= 1.0/61, and materially higher whenever the selectivity
  boost is live). This is D2's OWN mechanism supplying the fixed arm's
  robustness, not a fixture coincidence.

  Consequence, reported honestly rather than engineered away: given anomaly
  A, `seed_ids[0]` is INVARIANT to FETCH_WIDTH_FLOOR on both paths (the
  floor changes the TAIL of the seed slice, never its head), so this gate's
  graph-arm mechanism cannot, by construction, discriminate floor=10 from
  floor=1000 -- `run_floor_probe` is provided for AC-138/AC-139 but its
  result on THIS fixture is expected to be INDETERMINATE on the graph-arm
  channel (not a false green) until anomaly A (follow-up F-A) is fixed.
  This is the exact `[RISK]` deliberation.md DP-8 pre-registered under C-2:
  "an overlap makes AC-139 INDETERMINATE, which is not green, which
  triggers its own escape hatch: AC-138 must be moved, not weakened."

Multi-layer fixture (AC-126): the query also matches a `target-sem` crystal
committed to `semantic`, so the cross-layer sparse-arm rank is reported per
layer even though DP-5 defers the cross-layer fusion fix itself.
"""

from __future__ import annotations

import hashlib
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

_PROJECT = "fusion-gate"
_AGENT_CLASS = "backend"
_QUERY = "plarnix threxil vandomere signature"
_ALL_LAYERS = ["episodic", "semantic", "procedural", "execution"]


def _crystal(id: str, layer: str, summary: str) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    return {
        "id": id,
        "layer": layer,
        "summary": summary,
        "trust_tier": "T1",
        "validation_state": "validated",
        "status": "active",
        "content_ref": hashlib.sha256(id.encode()).hexdigest(),
        "embedding_ref": None,
        "scope": {
            "project": _PROJECT, "agent_class_visibility": _AGENT_CLASS,
            "sensitivity_tag": "none",
        },
        "provenance": {
            "source": "verified_agent", "author_agent": "fusion-gate",
            "task_id": None, "created_at": now.isoformat(),
        },
        "temporal": {"t_valid_from": now.isoformat(), "t_valid_to": None, "superseded_by": None},
        "utility": {
            "access_count": 0, "last_access": now.isoformat(),
            "outcome_success_score": None, "importance": 0.5, "novelty_at_write": 0.5,
        },
    }


def _build_fixture(relational: Any, graph: Any) -> tuple[list[str], str]:
    target = _crystal("target", "episodic", f"{_QUERY} episodic runbook notes")
    target_sem = _crystal("target-sem", "semantic", f"{_QUERY} semantic fact entry")
    n1 = _crystal("N1", "episodic", "dense competitor one filler content")
    n2 = _crystal("N2", "episodic", "dense competitor two filler content")
    n3 = _crystal("N3", "episodic", "dense competitor three filler content")
    z = _crystal("Z", "episodic", "phantom node, graph-only, no lexical or dense presence")

    for c in (target, target_sem, n1, n2, n3, z):
        relational.insert_crystal(c)

    for cid in ("N1", "N2", "N3", "Z"):
        graph.add_node(crystal_id=cid, layer="episodic")
    # All three competitors share the SAME edge target -- see module
    # docstring: this is what makes decaying_walk's hash-order frontier pick
    # robust (whichever of N1/N2/N3 it tries first still reaches Z).
    graph.add_edge("N1", "Z", "LINKS_TO")
    graph.add_edge("N2", "Z", "LINKS_TO")
    graph.add_edge("N3", "Z", "LINKS_TO")

    dense_hits = ["N1", "N2", "N3"]  # target is NOT in the dense arm at all --
    # see module docstring: this keeps target OUT of the unweighted build's
    # seed set entirely, so decaying_walk's hash-random frontier pick is
    # ALWAYS among {N1, N2, N3} (all three edge-bearing), never a coin flip
    # against `target`'s own (edge-less) exhaustion aborting the walk.
    return dense_hits, "target"


def run_arm(
    *, weighted: bool, data_root: str, fetch_width_floor: int | None = None
) -> dict[str, Any]:
    from crystalium.aetheryte import retrieve as retrieve_mod
    from crystalium.aetheryte.redact import Redactor
    from crystalium.aetheryte.retrieve import Aetheryte
    from crystalium.composer import Composer
    from crystalium.config import Config
    from crystalium.enforcement import Enforcement
    from crystalium.importance import importance_score
    from crystalium.schemas import Scope
    from crystalium.storage.graph import GraphStore
    from crystalium.storage.relational import RelationalStore
    from crystalium.trust import Tier

    tag = f"{int(weighted)}-{fetch_width_floor or 'd'}-{uuid.uuid4().hex[:8]}"
    data_dir = Path(data_root) / f"fg-{tag}"
    cfg = Config(data_dir=data_dir, recall_weighted_fusion=weighted, rate_limit_per_minute=1_000_000)

    relational = RelationalStore(db_path=cfg.sqlite_path)
    graph = GraphStore(kuzu_dir=cfg.kuzu_path)
    enforcement = Enforcement(cfg)
    redactor = Redactor(cfg)
    composer = Composer(cfg, recall_relevance_primary=cfg.recall_relevance_primary)

    dense_hits, target_id = _build_fixture(relational, graph)

    vector_store = MagicMock()
    vector_store.embed.return_value = [0.1, 0.2, 0.3]
    vector_store.dense_search.return_value = [{"id": cid} for cid in dense_hits]

    original_floor = retrieve_mod.FETCH_WIDTH_FLOOR
    if fetch_width_floor is not None:
        retrieve_mod.FETCH_WIDTH_FLOOR = fetch_width_floor

    try:
        aetheryte = Aetheryte(
            relational=relational,
            vector_store=vector_store,
            graph_store=graph,
            enforcement=enforcement,
            redactor=redactor,
            importance_fn=importance_score,
            composer=composer,
            completion=True,
            completion_max_hops=1,
            completion_decay=0.5,
            recall_active_only=False,
            recall_relevance_primary=True,
            recall_weighted_fusion=weighted,
            fusion_weight_dense=cfg.fusion_weight_dense,
            fusion_weight_derived=cfg.fusion_weight_derived,
            fusion_sparse_boost_alpha=cfg.fusion_sparse_boost_alpha,
        )
        result = aetheryte.recall(
            Scope(project=_PROJECT, agent_class_visibility=_AGENT_CLASS),
            _QUERY, 2, None, Tier.T1,
        )
        retrieved = [r.id for r in result.records]
        target_rank = retrieved.index(target_id) if target_id in retrieved else -1

        cross_layer: dict[str, int | None] = {}
        for layer in ("episodic", "semantic"):
            hits = relational.bm25_search(_QUERY, layer_filter=layer, k=30)
            ids = [h["id"] for h in hits]
            layer_target = "target" if layer == "episodic" else "target-sem"
            cross_layer[layer] = ids.index(layer_target) if layer_target in ids else None
    finally:
        retrieve_mod.FETCH_WIDTH_FLOOR = original_floor

    return {"target_rank": target_rank, "retrieved": retrieved, "cross_layer": cross_layer}


def run(*, data_root: str = "/tmp/crystalium-fusion-gate") -> dict[str, Any]:
    """AC-125/126: the default A/B (FETCH_WIDTH_FLOOR unmodified)."""
    os.makedirs(data_root, exist_ok=True)
    weighted = run_arm(weighted=True, data_root=data_root)
    unweighted = run_arm(weighted=False, data_root=data_root)

    gate_pass = weighted["target_rank"] == 0 and unweighted["target_rank"] != 0

    return {
        "weighted": weighted,
        "unweighted": unweighted,
        "gate_pass": gate_pass,
        "cross_layer": weighted["cross_layer"],
        "verdict": (
            "weighted fusion holds target at fused rank 0; unweighted does not"
            if gate_pass else
            "GATE FAILED or INCONCLUSIVE -- see weighted/unweighted target_rank"
        ),
    }


def run_floor_probe(
    *, floor: int, weighted: bool, data_root: str = "/tmp/crystalium-fusion-gate"
) -> dict[str, Any]:
    """AC-138/AC-139: re-run a single arm with FETCH_WIDTH_FLOOR overridden.
    Compare against `floor=FETCH_WIDTH_FLOOR` (10, the shipped default) to
    determine whether raising the floor changed the target's fused rank.
    See the module docstring: on THIS fixture, anomaly A makes `seed_ids[0]`
    (and therefore the graph arm's contents) invariant to the floor, so a
    genuine rank change here would be a genuine finding, not the expected
    outcome."""
    os.makedirs(data_root, exist_ok=True)
    return run_arm(weighted=weighted, data_root=data_root, fetch_width_floor=floor)
