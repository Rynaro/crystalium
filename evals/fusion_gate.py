"""crystalium#38 -- deterministic fusion-quality eval gate (AC-125/126/138/139).

An A/B: weighted vs unweighted fusion over IDENTICAL corpora -- the only
variable is the flag (honest-ablation discipline, retrieval_gate.py:11-14).

Real `RelationalStore` (genuine BM25/FTS5) + real `GraphStore` (genuine Kuzu
graph, real `neighbor_expand`/`decaying_walk`) + a DETERMINISTIC STUB vector
arm (deliberation.md C-4: BGE-m3 puts the distinctive-token target at dense
rank 1, not 4, making the reverted-build RED unobtainable with a real
embedder -- the criteria require only the graph store to be real at this
Layer-3 gate, so the vector arm is pinned).

Topology and its history (vigil F-V4 remediation, 2026-08-03) -- this
fixture went through three shapes during remediation, and the failures are
kept in this docstring on purpose: each one taught something the final shape
depends on.

  1. ORIGINAL (3 competitors, no fillers). `dense_ranking` held only 3 ids,
     far below FETCH_WIDTH_FLOOR=10, so `[:10] == [:1000]` byte-identically
     -- the floor had NO tail to cut at all. AC-138/AC-139's INDETERMINATE
     verdict was correctly reached, but for the WRONG reason: the docstring
     blamed anomaly A ("the floor changes the tail, never the head") when
     the true blocker was fixture cardinality never giving the floor a live
     channel in the first place (vigil's finding).
  2. ENLARGED, still uppercase ids (this revision's first fix). 12 filler
     competitors added so `dense_ranking` holds 15 ids -- `[:10]` and
     `[:1000]` now genuinely differ in MEMBERSHIP, confirmed by intercepting
     the real `seed_ids`/`decaying_walk` arguments (see below). But the
     ORIGINAL naming (`N1`, `N2`, `N3`, `Z` -- all uppercase) meant `N1`
     ALONE, tied with `target` at `1/61` on the reverted build (both
     single-arm), already wins that tie via the id-ascending sort
     ("N1" < "target" alphabetically) with NO vote needed at all -- so the
     floor's now-real effect on vote count was invisible behind a
     tie-break confound this fixture didn't need to have.
  3. RENAMED so every competitor sorts AFTER "target" (tried and measured,
     then reverted). This closed the tie confound and DID let the floor's
     effect surface as rank variance -- but it also made the phantom node's
     win depend on genuinely EARNING 2 votes rather than winning-by-default,
     which regressed AC-125's own reliability (dropped from 7/7 to ~2/7
     unanimous across seeds, since AC-125 is IN AC-136's contingency six and
     must stay green). Measuring the renamed fixture across 14 seeds
     (0-12 + unset) is nonetheless the source of the corrected finding
     below, and is preserved in `red-evidence.txt`.

FINAL shape ships (2) — uppercase ids, 12 fillers — because AC-125 must stay
reliably green and is NOT allowed to trade that for AC-138/139 (which are
NOT in AC-136's contingency six). The corrected attribution for AC-138/139,
from measurement (3)'s 14-seed run, is recorded below rather than re-derived
from the (now reverted) renamed fixture.

  target   -- the SOLE BM25 hit (rank 1), NOT in the dense arm at all -- ONE
             base arm, `1/61 = 0.016393` on the REVERTED path (unweighted,
             no boost); boosted on the FIXED path (D3's selectivity boost:
             n_sparse=1 is maximally selective).
  N1/N2/N3 -- dense ranks 1-3, no lexical match, EACH with an edge to `Z`
             (the SAME destination from all three -- see below). N1 is
             deterministically `dense_ranking[0]`.
  F1..F12  -- filler competitors, dense ranks 4-15, no lexical match, no
             graph edges. Exist ONLY to push `dense_ranking`'s length past
             `FETCH_WIDTH_FLOOR` (10) -- vigil F-V4's fix. `[:10]` truncates
             at F7; `[:1000]` (or any floor >= 15) admits all of them.
  Z        -- a PHANTOM node: no sparse or dense presence, discoverable only
             via the graph.

  Reliability -- `target` is absent from the dense arm, and `N1` (dense rank
  1, uppercase id) is ALWAYS tied with `target` at `1/61` on the reverted
  build; the id-ascending tiebreak ("N1" < "target") means `target` never
  reaches rank 0 there regardless of anything the derived arms do -- this is
  what keeps AC-125 (`gate_pass`) unanimous across every sampled seed
  (0-5, unset -- C-2), independent of the anomaly-A lottery described next.

  Anomaly A and the floor, PRECISELY (not "the floor changes nothing"):
  intercepting the real `neighbor_expand`/`decaying_walk` calls (this
  remediation, `red-evidence.txt`) shows the floor DOES change what is
  passed to both -- at floor=10/seed=0 `decaying_walk` is given a 10-element
  seed set and returns `{"<phantom>": 0.5}` (its internal hash-order pick
  lands on an edge-bearing competitor first); at floor=1000/seed=0 it is
  given a 15-element seed set and returns `{}` (the pick lands on a
  edge-less filler first, and anomaly A's single-seed cap aborts the walk
  there). The floor's channel is LIVE and MEASURED, contradicting the
  original ("no tail") docstring. What remains true is narrower: WHICH
  element a given seed's hash-order pick lands on is a function of
  (PYTHONHASHSEED, the exact id strings) that does not correlate with slice
  length in a way that produces DISJOINT floor=10-vs-floor=1000 rank
  distributions -- measured over 14 sample points (PYTHONHASHSEED 0-12 and
  unset) on the id-renamed variant (3, above): exactly ONE point diverges
  (seed 8: floor=10 gives one rank, floor=1000 another -- proving the
  channel is live end-to-end), while all 13 other sampled points (0-7,
  9-12, unset) agree across both floors -- so the two floors' rank-outcome
  SETS overlap ({0,1} vs {0,1}) rather than partition. Per C-2, an
  overlapping distribution makes AC-139 INDETERMINATE regardless of
  whether individual seeds diverge.

  On the FIXED (weighted) path, `target` (boosted, its only arm) leads the
  base-arm `prelim`, so `target` -- not N1 -- is `seed_ids[0]`; target has no
  out-edges, so the ORDERED graph arm dies immediately there regardless of
  floor. `decaying_walk`'s hash-order pick might still land on N1/N2/N3 and
  find the phantom via completion alone -- but D2's derived-family MIN-RANK
  MERGE collapses graph+completion into ONE vote regardless of which
  channel(s) found it, so the phantom's fixed score is capped at
  `w_derived/61 = 1/61` either way -- below target's boosted score at every
  sampled seed and both floors (C-2, unanimous).

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


_FILLER_COUNT = 12  # > FETCH_WIDTH_FLOOR (10): gives the floor a live channel
# (vigil F-V4 remediation) — with only N1/N2/N3 (arity 3), `dense_ranking[:10]
# == dense_ranking[:1000]` byte-identically, so raising the floor changed
# NOTHING and the AC-138/AC-139 probe was unfalsifiable by construction, not
# by anomaly A. Filler count 12 makes `dense_ranking` hold 15 ids (N1-N3 +
# F1-F12): `[:10]` and `[:1000]` now genuinely differ in MEMBERSHIP.


def _build_fixture(relational: Any, graph: Any) -> tuple[list[str], str]:
    target = _crystal("target", "episodic", f"{_QUERY} episodic runbook notes")
    target_sem = _crystal("target-sem", "semantic", f"{_QUERY} semantic fact entry")
    n1 = _crystal("N1", "episodic", "dense competitor one filler content")
    n2 = _crystal("N2", "episodic", "dense competitor two filler content")
    n3 = _crystal("N3", "episodic", "dense competitor three filler content")
    z = _crystal("Z", "episodic", "phantom node, graph-only, no lexical or dense presence")
    fillers = [
        _crystal(f"F{i}", "episodic", f"unrelated dense filler item number {i}")
        for i in range(1, _FILLER_COUNT + 1)
    ]

    for c in (target, target_sem, n1, n2, n3, z, *fillers):
        relational.insert_crystal(c)

    for cid in ("N1", "N2", "N3", "Z"):
        graph.add_node(crystal_id=cid, layer="episodic")
    # All three competitors share the SAME edge target -- see module
    # docstring: this is what makes decaying_walk's hash-order frontier pick
    # robust (whichever of N1/N2/N3 it tries first still reaches Z).
    graph.add_edge("N1", "Z", "LINKS_TO")
    graph.add_edge("N2", "Z", "LINKS_TO")
    graph.add_edge("N3", "Z", "LINKS_TO")

    # target is NOT in the dense arm at all -- keeps target OUT of the
    # unweighted build's seed set entirely, so decaying_walk's hash-random
    # frontier pick is always among {N1, N2, N3, F1..F12}, never a coin flip
    # against target's own (edge-less) exhaustion aborting the walk. N1/N2/N3
    # stay at dense ranks 1-3 (always within ANY floor >= 3); the 12 fillers
    # fill ranks 4-15 (vigil F-V4's fix: dense_ranking now holds 15 ids, so
    # [:10] and [:1000] genuinely differ in membership -- see module
    # docstring for the measured consequences).
    dense_hits = ["N1", "N2", "N3"] + [f"F{i}" for i in range(1, _FILLER_COUNT + 1)]
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

    Corrected attribution (vigil F-V4 remediation; see the module docstring
    for the full measurement): the floor DOES have a live, measured channel
    on this fixture (`dense_ranking` holds 15 ids, so `[:10] != [:1000]`,
    confirmed by intercepting the real `neighbor_expand`/`decaying_walk`
    calls). It is NOT true that "anomaly A makes seed_ids[0] invariant to
    the floor" -- that was only true of the pre-remediation 3/5-id fixture.
    What remains true, and is now measured rather than assumed, is narrower:
    anomaly A's single-successful-seed cap makes the derived arms' outcome a
    function of (PYTHONHASHSEED, exact id strings) that does not correlate
    with slice length in a way that produces DISJOINT floor=10-vs-floor=1000
    rank distributions -- individual seeds CAN diverge (seed 8 does, on the
    id-renamed measurement variant), but the two floors' outcome SETS
    overlap across a 14-seed sample. A genuine rank change at any ONE seed
    is real and worth recording, not dismissed -- but AC-139 needs the
    *distributions* to be disjoint (C-2), and they are not."""
    os.makedirs(data_root, exist_ok=True)
    return run_arm(weighted=weighted, data_root=data_root, fetch_width_floor=floor)
