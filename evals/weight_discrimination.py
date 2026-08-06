"""crystalium#55 (W-G-WD) -- weight-discriminating fixture: the #42 DP-1(b)
re-check oracle, NOT band characterisation.

This module's ONLY declared purpose is to give the #42 derived-family-merge
re-check (spec.md Sec 3.4: "#42 IS F-B" -- DP-1(b)'s recorded reversal
condition) an instrument that can actually SEE derived-arm arithmetic move.
It is NOT, and must never be cited as, a characterisation of the sub-1.0
`fusion_weight_derived` band -- that question is ruled formally unsupported
(spec.md Sec 5.2; FORGE D4): no shipped gate validates or falsifies any
sub-1.0 value, and this fixture does not change that. A future reader who
finds a weight-sweep fixture here and cites it as evidence for what a
sub-1.0 weight "does" to retrieval quality is repeating the exact failure
#52 documents (an axis that presents as evidence for something it does not
measure), one axis over. State it plainly: this module's purpose is the
DP-1(b) re-check, NOT band characterisation.

C-9 (spec.md Sec 3.3): no test, docstring, result field or comment in this
module may present a sub-1.0 `fusion_weight_derived` as a supported
precision dial. The weights swept below ({0.90, 0.95, 1.00} by default, and
the AC-375 wide-band control {0.5, 1.0, 100.0}) are TEST INPUTS to a
discriminability check -- "does the gate's answer change at all as this
axis moves" -- never a validated or recommended operating point. The only
value `config.py` documents as supported is 1.0.

Fixture shape (spec.md Sec 4, #55; AC-317/AC-375/AC-376), on the shared
corpus rig (`evals/_corpus_rig.py`), through `Aetheryte.recall` end to end:

  - single layer, `corpus < candidate_k` -- both ASSERTED, the two pinned
    non-binding axes (R-CONF; liveness self-checks below via the rig).
  - real `GraphStore`, stub dense arm (empty -- neither A nor B ever reaches
    the dense arm, so "base-arm vote" below can only mean the sparse arm).
  - `A` (`wgwd-record-a`) -- a record whose ONLY support is the derived arm:
    no lexical match to the query, absent from the (empty) dense arm,
    reachable ONLY as the sole graph-neighbour of `B` (a graph-only
    phantom, derived-arm rank 1 by construction -- it is the only node
    `neighbor_expand` can ever find).
  - `B` (`wgwd-record-b`) -- a record with EXACTLY one base-arm vote, at a
    KNOWN rank: it is the sparse arm's rank-2 hit (a `wgwd-leader` crystal
    is seeded purely to occupy rank 1 with a stronger lexical match, so
    B's rank is fixed at 2, not incidental).
  - the ordering of A vs B is then decided by pure, stated, checkable
    arithmetic -- `w_derived / (60 + 1)` vs `1.0 / (60 + 2)` (RRF, k=60,
    matching `retrieve.py::weighted_rrf_merge_scored`'s own formula) -- the
    same mechanism `dp2-control-note.md` reconstructed for the retrieval
    gate. `_run_cell` reads BOTH the fused score `Aetheryte.recall` actually
    produced (`record.score`) AND this module's own hand-computed
    `expected_score_*`, so the two can be compared rather than one merely
    asserted.

AC-317: at `fusion_weight_derived in {0.90, 0.95, 1.00}` the outcome of A
vs B must differ at least twice (>= 2 distinct outcomes) -- a gate that
answers identically at every sampled weight is not a weight gate, it is
exactly the degeneracy #55 reports. AC-375 is the same check over a wide
band ({0.5, 1.0, 100.0}) as the positive-capability control: without it, a
`< 2` result on AC-317's narrow band cannot be told apart from "this
fixture cannot discriminate at all" (K-C-N2).

Injection discipline (VP-M4): `fusion_weight_derived` is threaded through
`build_aetheryte` (this module's own factory over the rig's harness, itself
wrapping `Aetheryte.__init__`) and every reader of it -- AC-317's
`weight_readback`, AC-376's own node -- reads it back OFF THE `Aetheryte`
INSTANCE the factory returned, never off the kwargs dict this module just
wrote (that comparison is a tautology and cannot fail).

R-CONF (Liveness): `_run_cell` asserts the two pinned axes (single layer,
`corpus < candidate_k`) and the rig's own `graph_liveness` / `arm_liveness`
self-checks BEFORE ever reporting a numeric outcome; any one of them going
false collapses the cell's verdict to `"confounded"` with `outcome: None`
-- never a silently-wrong number. This is what makes the red-check
("sever A's graph edge") observable: with no derived-arm support left,
`graph_liveness`'s `expected_edge_count=1` check fails, every cell in the
run goes `"confounded"`, and AC-317's own `.outcome | type == "string"`
conjunct (a `None` is not a string) reddens the gate -- exactly the
behaviour a gate that can fail is supposed to have.

Container-first, like every other Wave-1 gate in this campaign:
  docker compose run --rm crystalium pytest mcp-server/tests/test_weight_discrimination.py -v
  docker compose run --rm crystalium /app/.venv/bin/python -c \
      "import evals.weight_discrimination as m; \
       m.emit(m.run(seed_label='0'), '/app/evals/results/wd-seed-0.json')"

Out of scope for the W-G-WD unit that built everything ABOVE this line
(forge-rulings.md D4; W-G-WD's scope was the fixture + AC-317/AC-318/
AC-375/AC-376 only): this module's own docstring used to record the #42
`run_dp1_recheck` oracle (AC-352) as a FORWARD OBLIGATION on #42's own
unit (W-42), needing seed exclusion relaxed on the retrieval path before
the check is meaningful. **W-42 has now discharged that obligation** --
`run_dp1_recheck` / `_run_dp1_cell` / `_build_dp1_fixture` below are W-42's
own grant into this file, on a fixture disjoint from the A/B/leader corpus
above (own project scope, own ids -- spec.md Sec 0.2). Nothing here builds
the struck Sec-D2 bitwise identity harness (AC-319/AC-320, dropped by
FORGE D4: no runnable harness ever existed to "re-run").
"""

from __future__ import annotations

import uuid
from typing import Any

from evals._corpus_rig import (  # noqa: F401 -- `emit` is re-exported, see module docstring
    arm_liveness,
    crystal,
    emit,
    graph_liveness,
    new_stores,
    resolve_verdict,
    seed_graph,
    seed_relational,
    stamp_sequence,
    stub_vector_store,
)
from evals._corpus_rig import (
    build_aetheryte as _rig_build_aetheryte,
)

# ---------------------------------------------------------------------------
# Fixture constants -- disjoint project scope from every other Wave-1 gate
# (spec.md Sec 0.1's crystal() convention); an unusual, invented-word query
# so lexical match is unambiguous and never accidentally collides with real
# content (fusion_gate.py precedent).
# ---------------------------------------------------------------------------

_PROJECT = "weight-discrimination-gate"
_AGENT_CLASS = "backend"
_LAYER = "episodic"
_LAYERS = [_LAYER]  # single layer -- pinned, asserted below (R-CONF)
_QUERY = "vandomerite skarn"

_ID_LEADER = "wgwd-leader"     # sparse rank 1 -- exists ONLY to fix B's rank at 2
_ID_B = "wgwd-record-b"        # exactly one base-arm (sparse) vote, KNOWN rank 2
_ID_A = "wgwd-record-a"        # graph-only phantom -- derived-arm rank 1, ONLY support

# RRF constant, restated here as a CHECKABLE value (matches
# retrieve.py::weighted_rrf_merge_scored's own `k_rrf: int = 60` default,
# which every production call site uses unmodified) so `expected_score_*`
# below is arithmetic this module states and can be wrong about, not a
# number quietly assumed.
_K_RRF = 60
_RANK_A = 1  # A is the sole neighbour `neighbor_expand` can ever return
_RANK_B = 2  # B is the sparse arm's second hit, behind `_ID_LEADER`

# AC-317's three sampled weights. C-9: these are TEST INPUTS to a
# discriminability check, not a supported/recommended operating range --
# see the module docstring's second paragraph.
_DEFAULT_WEIGHTS: tuple[float, ...] = (0.90, 0.95, 1.00)


def _build_fixture(relational: Any, graph: Any) -> list[dict[str, Any]]:
    """Seed the L / B / A corpus (module docstring) into one gate arm's
    stores. Returns the minted crystal list so callers can assert
    `corpus < candidate_k` off the REAL corpus size, never a hardcoded
    constant that could drift out of sync with this function."""
    stamps = stamp_sequence()
    leader = crystal(
        _ID_LEADER,
        _LAYER,
        f"{_QUERY} {_QUERY} {_QUERY} engineering runbook notes",
        project=_PROJECT,
        agent_class=_AGENT_CLASS,
        created_at=next(stamps),
    )
    base = crystal(
        _ID_B,
        _LAYER,
        f"{_QUERY} single mention entry record",
        project=_PROJECT,
        agent_class=_AGENT_CLASS,
        created_at=next(stamps),
    )
    phantom = crystal(
        _ID_A,
        _LAYER,
        "graph-only phantom node, no lexical or dense presence whatsoever",
        project=_PROJECT,
        agent_class=_AGENT_CLASS,
        created_at=next(stamps),
    )
    corpus = [leader, base, phantom]
    seed_relational(relational, corpus)
    # ONE edge, B -> A: A is then the sole neighbour of ANY seed (B is
    # always within fetch_width on this tiny corpus), so A lands at
    # derived-arm rank 1 regardless of seed order or PYTHONHASHSEED
    # (`neighbor_expand`'s result is walked via `sorted()`, retrieve.py --
    # deterministic across hash seeds by construction, not by luck).
    seed_graph(graph, nodes=[(_ID_B, _LAYER), (_ID_A, _LAYER)], edges=[(_ID_B, _ID_A, "LINKS_TO")])
    return corpus


def build_aetheryte(
    *,
    w_derived: float,
    relational: Any | None = None,
    graph_store: Any | None = None,
    vector_store: Any | None = None,
    cfg: Any | None = None,
    data_root: str = "/tmp/crystalium-weight-discrimination-build",
    tag: str | None = None,
) -> Any:
    """W-G-WD's own factory, wrapping the rig's `build_aetheryte` harness
    (AC-376's normative symbol: `evals.weight_discrimination.build_aetheryte`).

    `w_derived` is the ONLY axis a caller varies -- every other flag is
    pinned here, explicitly, matching the rig's own "never inherit a
    default" discipline:
      - `completion=False` -- the derived arm is graph-only (matches the
        module docstring's "single voter" claim; with completion off,
        `derived_family_merge([graph_ranking, []])` is the identity
        transform on `graph_ranking`, AC-108).
      - `recall_active_only=True`, `recall_relevance_primary=True`,
        `recall_weighted_fusion=True` -- the weighted path (`_weighted`)
        must be live for `fusion_weight_derived` to do anything at all.
      - `fusion_sparse_boost_alpha=0.0` -- pins `w_sparse == 1.0` exactly
        regardless of query selectivity, so B's fused score is EXACTLY
        `1.0 / (60 + rank)`, matching the module docstring's stated
        formula with no D3 selectivity-boost cross-term to account for.

    When `relational`/`graph_store`/`cfg` are omitted, a throwaway, empty
    (unseeded) fixture is built under `data_root`/`tag` -- this is what
    lets AC-376 call `build_aetheryte(w_derived=w)` with no other argument
    and still get a real `Aetheryte` instance to read `.fusion_weight_derived`
    off of. `run()` below always supplies its own seeded stores instead, so
    the corpus a caller actually measures against is never this throwaway
    one.
    """
    if relational is None or graph_store is None or cfg is None:
        stores = new_stores(data_root, tag or f"build-{uuid.uuid4().hex[:8]}")
        cfg = stores.cfg if cfg is None else cfg
        relational = stores.relational if relational is None else relational
        graph_store = stores.graph if graph_store is None else graph_store
    if vector_store is None:
        vector_store = stub_vector_store([])  # no dense hits -- A/B never reach the dense arm
    return _rig_build_aetheryte(
        cfg=cfg,
        relational=relational,
        vector_store=vector_store,
        graph_store=graph_store,
        completion=False,
        completion_max_hops=2,
        completion_decay=0.5,
        recall_active_only=True,
        recall_relevance_primary=True,
        recall_weighted_fusion=True,
        fusion_weight_dense=1.0,
        fusion_weight_derived=w_derived,
        fusion_sparse_boost_alpha=0.0,
    )


def _run_cell(*, w_derived: float, seed_label: str, data_root: str, k: int) -> dict[str, Any]:
    """One (weight, seed) sample: build a fresh corpus, build an `Aetheryte`
    at `w_derived` via `build_aetheryte`, recall through it, and classify
    A vs B's ordering. R-CONF: never reports a numeric `outcome` unless
    every pinned/liveness axis below is confirmed non-binding."""
    from crystalium.aetheryte.retrieve import FETCH_WIDTH_FLOOR
    from crystalium.schemas import Scope
    from crystalium.trust import Tier

    assert len(_LAYERS) == 1, "G-WD is a single-layer fixture by construction"

    tag = f"wgwd-{seed_label}-{w_derived}-{uuid.uuid4().hex[:8]}"
    stores = new_stores(data_root, tag)
    relational, graph, cfg = stores.relational, stores.graph, stores.cfg

    corpus = _build_fixture(relational, graph)
    vector_store = stub_vector_store([])  # empty dense arm -- see module docstring

    aetheryte = build_aetheryte(
        w_derived=w_derived,
        relational=relational,
        graph_store=graph,
        vector_store=vector_store,
        cfg=cfg,
    )

    candidate_k = max(k * 3, FETCH_WIDTH_FLOOR)
    corpus_size = len(corpus)
    assert corpus_size < candidate_k, (corpus_size, candidate_k)  # pinned non-binding axis

    scope = Scope(project=_PROJECT, agent_class_visibility=_AGENT_CLASS, sensitivity_tag="none")
    result = aetheryte.recall(scope, _QUERY, k, _LAYERS, Tier.T1, explain=True)

    explain_obj = result.explain or {}
    fusion = explain_obj.get("fusion", {})
    arm_sizes = fusion.get("arm_sizes", {})

    g_live = graph_liveness(graph, expected_node_count=2, expected_edge_count=1)
    sparse_hits = relational.bm25_search(_QUERY, layer_filter=_LAYER, k=candidate_k)
    sparse_ids = [h["id"] for h in sparse_hits]
    a_live = arm_liveness(
        dense_ranking=[],
        expected_dense=0,
        sparse_ranking=sparse_ids,
        expected_sparse=2,
    )

    pinned = {
        "corpus_lt_candidate_k": corpus_size < candidate_k,
        "graph_liveness": g_live["verdict"] == "measured",
        "arm_liveness": a_live["verdict"] == "measured",
        # A must be reachable ONLY via the derived arm -- never a lexical hit.
        "phantom_derived_only": _ID_A not in sparse_ids,
        # B's base-arm rank must be the KNOWN, stated one -- leader first, B second.
        "base_known_rank": sparse_ids[:2] == [_ID_LEADER, _ID_B],
    }
    verdict = resolve_verdict(pinned=pinned)

    cell: dict[str, Any] = {
        "weight": w_derived,
        "weight_readback": aetheryte.fusion_weight_derived,  # off the INSTANCE (VP-M4)
        "verdict": verdict,
        "liveness": {
            "graph": g_live["liveness"],
            "arms": a_live["liveness"],
            "pinned": pinned,
        },
    }

    if verdict != "measured":
        cell["outcome"] = None
        return cell

    scores = {r.id: r.score for r in result.records}
    ids = [r.id for r in result.records]
    score_a = scores.get(_ID_A)
    score_b = scores.get(_ID_B)

    if score_a is not None and score_b is not None:
        if score_a > score_b:
            outcome = "A_outranks_B"
        elif score_b > score_a:
            outcome = "B_outranks_A"
        else:
            outcome = "TIE"
    elif score_a is not None:
        outcome = "A_outranks_B"  # B fell out of the response entirely
    elif score_b is not None:
        outcome = "B_outranks_A"  # A fell out of the response entirely
    else:
        outcome = "NEITHER_PRESENT"

    cell.update(
        {
            "outcome": outcome,
            "score_a": score_a,
            "score_b": score_b,
            "rank_a": ids.index(_ID_A) if _ID_A in ids else None,
            "rank_b": ids.index(_ID_B) if _ID_B in ids else None,
            # The stated, checkable arithmetic (module docstring) -- what the
            # fused score SHOULD be if the formula this fixture is built to
            # demonstrate is what actually ran.
            "expected_score_a": w_derived / (_K_RRF + _RANK_A),
            "expected_score_b": 1.0 / (_K_RRF + _RANK_B),
            "w_sparse": fusion.get("w_sparse"),
            "arm_sizes": arm_sizes,
        }
    )
    return cell


def run(
    *,
    seed_label: str = "0",
    weights: list[float] | None = None,
    data_root: str = "/tmp/crystalium-weight-discrimination",
    k: int = 3,
) -> dict[str, Any]:
    """AC-317 / AC-375: sweep `fusion_weight_derived` over `weights`
    (default `{0.90, 0.95, 1.00}`; AC-375 passes the wide-band control
    `[0.5, 1.0, 100.0]`) and report each cell's A-vs-B outcome.

    `seed_label` is carried through to the result for traceability across
    the C-2 multi-process protocol (`PYTHONHASHSEED` 0-5 + unset, one
    process per seed -- see the module docstring); this fixture's ranks are
    fixed by construction (BM25 term-frequency gap for B, a single graph
    edge for A, both walked via `sorted()`), so the outcome set is not
    expected to vary BY seed -- what AC-317 checks is that it varies BY
    weight, at every seed.

    Returns `{"seed_label", "cells": [...], "distinct_outcome_count",
    "weights"}` -- UNSTAMPED (no `run_nonce`/`tree_sha`); pass the result to
    `emit()` (re-exported from `evals._corpus_rig`, rule (g)) to stamp and
    write it.
    """
    weight_list = list(weights) if weights is not None else list(_DEFAULT_WEIGHTS)
    cells = [
        _run_cell(w_derived=w, seed_label=seed_label, data_root=data_root, k=k) for w in weight_list
    ]
    distinct = {c["outcome"] for c in cells if c.get("outcome") is not None}
    return {
        "seed_label": seed_label,
        "weights": weight_list,
        "cells": cells,
        "distinct_outcome_count": len(distinct),
    }


# ---------------------------------------------------------------------------
# crystalium#42 (W-42) -- DP-1(b) re-check oracle (AC-352). This is the
# FORWARD OBLIGATION this module's own docstring names as out of scope for
# W-G-WD: "the #42 run_dp1_recheck oracle (AC-352) is a FORWARD OBLIGATION
# on #42's own unit (W-42), which needs recall_active_only/seed-exclusion
# relaxed on the retrieval path before that check is meaningful". Landing
# #42 is itself the evidence deliberation.md Sec 8's DP-1(b) reversal
# fires on (spec.md Sec 3.4: "#42 IS F-B") -- this is that re-check.
#
# A SEPARATE, disjoint fixture from the AC-317/AC-375 A/B/leader corpus
# above (own project scope, own ids -- spec.md Sec 0.2 discipline applied
# to this module's own two fixtures, never sharing a data root):
#   - `_ID_DP1_SUP` -- a support record with exactly ONE base-arm (sparse)
#     vote, and the sole edge to the phantom below. Reachable ONLY through
#     it does `_ID_DP1_D` earn derived-arm rank 0.
#   - `_ID_DP1_D`   -- graph-only phantom: no lexical or dense presence
#     whatsoever (AC-352's "derived-only record").
#   - `_ID_DP1_TB`  -- "two base arms" (AC-352's own term): ONE sparse vote
#     (weaker TF than SUP, so sparse order is [SUP, TB] deterministically)
#     AND the sole dense hit -- a record backed by two base arms, no graph
#     edge, so its score is base-arm-only by construction.
# ---------------------------------------------------------------------------

_DP1_PROJECT = "dp1-recheck-gate"
_DP1_QUERY = "florzenweit brackamyr"

_ID_DP1_SUP = "dp1-record-sup"  # one sparse vote, KNOWN rank 0; sole edge to D
_ID_DP1_D = "dp1-record-d"      # graph-only phantom -- derived-arm rank 0, ONLY support
_ID_DP1_TB = "dp1-record-tb"    # two base arms: sparse rank 1 AND the sole dense hit


def _build_dp1_fixture(relational: Any, graph: Any) -> list[dict[str, Any]]:
    """Seed the SUP / D / TB corpus (module docstring, AC-352 section) into
    a fresh pair of stores."""
    stamps = stamp_sequence()
    sup = crystal(
        _ID_DP1_SUP,
        _LAYER,
        f"{_DP1_QUERY} {_DP1_QUERY} support record entry",
        project=_DP1_PROJECT,
        agent_class=_AGENT_CLASS,
        created_at=next(stamps),
    )
    tb = crystal(
        _ID_DP1_TB,
        _LAYER,
        f"{_DP1_QUERY} single mention two-base-arm record",
        project=_DP1_PROJECT,
        agent_class=_AGENT_CLASS,
        created_at=next(stamps),
    )
    phantom = crystal(
        _ID_DP1_D,
        _LAYER,
        "graph-only phantom node, no lexical or dense presence whatsoever",
        project=_DP1_PROJECT,
        agent_class=_AGENT_CLASS,
        created_at=next(stamps),
    )
    corpus = [sup, tb, phantom]
    seed_relational(relational, corpus)
    # ONE edge, SUP -> D: D is then the sole neighbour of ANY seed (SUP is
    # always within fetch_width on this tiny corpus), so D lands at
    # derived-arm rank 0 regardless of seed order or PYTHONHASHSEED. TB
    # carries NO edge -- its score is base-arm-only, by construction.
    seed_graph(
        graph,
        nodes=[(_ID_DP1_SUP, _LAYER), (_ID_DP1_D, _LAYER), (_ID_DP1_TB, _LAYER)],
        edges=[(_ID_DP1_SUP, _ID_DP1_D, "LINKS_TO")],
    )
    return corpus


def _run_dp1_cell(*, w_derived: float, data_root: str, k: int = 3) -> dict[str, Any]:
    """One `w_derived` sample of the DP-1(b) re-check (AC-352): build a
    fresh corpus, an `Aetheryte` with `recall_seed_derived_credit=True`
    (seed exclusion RELAXED -- the configuration this check exists to
    re-verify), recall, and read the derived-only phantom's and the
    two-base-arm record's ranks off the ACTUAL fused `result.records`
    (never off hand-computed arithmetic alone), so a combiner regression
    the stated RRF formula does not predict would still be caught.

    `recall_seed_derived_credit` predates `evals/_corpus_rig.py::build_aetheryte`
    (crystalium#42 postdates W-RIG) and is not one of that factory's stated
    required flags, so it is set on the returned INSTANCE post-construction
    -- `retrieve.py`'s two call sites read `self.recall_seed_derived_credit`
    at call time, so this is behaviourally identical to constructor
    injection and leaves `_corpus_rig.py` -- shared by three OTHER,
    already-shipped Wave-1 gates -- byte-untouched.
    """
    from crystalium.aetheryte.retrieve import FETCH_WIDTH_FLOOR
    from crystalium.schemas import Scope
    from crystalium.trust import Tier

    tag = f"dp1-{w_derived}-{uuid.uuid4().hex[:8]}"
    stores = new_stores(data_root, tag)
    relational, graph, cfg = stores.relational, stores.graph, stores.cfg

    corpus = _build_dp1_fixture(relational, graph)
    vector_store = stub_vector_store([_ID_DP1_TB])  # TB is the sole dense hit

    aetheryte = _rig_build_aetheryte(
        cfg=cfg,
        relational=relational,
        vector_store=vector_store,
        graph_store=graph,
        completion=False,
        completion_max_hops=2,
        completion_decay=0.5,
        recall_active_only=True,
        recall_relevance_primary=True,
        recall_weighted_fusion=True,
        fusion_weight_dense=1.0,
        fusion_weight_derived=w_derived,
        fusion_sparse_boost_alpha=0.0,
    )
    # crystalium#42 -- the flag this re-check exists to exercise. Set on the
    # instance (see docstring above); `_rig_build_aetheryte` predates it.
    aetheryte.recall_seed_derived_credit = True

    candidate_k = max(k * 3, FETCH_WIDTH_FLOOR)
    corpus_size = len(corpus)
    assert corpus_size < candidate_k, (corpus_size, candidate_k)  # pinned non-binding axis

    scope = Scope(
        project=_DP1_PROJECT, agent_class_visibility=_AGENT_CLASS, sensitivity_tag="none"
    )
    result = aetheryte.recall(scope, _DP1_QUERY, k, _LAYERS, Tier.T1, explain=True)

    g_live = graph_liveness(graph, expected_node_count=3, expected_edge_count=1)
    sparse_hits = relational.bm25_search(_DP1_QUERY, layer_filter=_LAYER, k=candidate_k)
    sparse_ids = [h["id"] for h in sparse_hits]
    dense_ids = [h["id"] for h in vector_store.dense_search.return_value]
    a_live = arm_liveness(
        dense_ranking=dense_ids, expected_dense=1, sparse_ranking=sparse_ids, expected_sparse=2,
    )
    # R-CONF: never report a numeric outcome unless every pinned/liveness
    # axis is confirmed non-binding -- loud AssertionError, not a silent
    # "confounded" this narrow, single-purpose oracle has no verdict field
    # for.
    assert g_live["verdict"] == "measured", g_live
    assert a_live["verdict"] == "measured", a_live
    assert _ID_DP1_D not in sparse_ids and _ID_DP1_D not in dense_ids, "D must be derived-only"
    assert sparse_ids[:2] == [_ID_DP1_SUP, _ID_DP1_TB], sparse_ids  # KNOWN sparse ranks
    assert dense_ids == [_ID_DP1_TB], dense_ids  # KNOWN dense rank

    ids = [r.id for r in result.records]
    assert _ID_DP1_D in ids, ("D fell out of the response entirely", ids)
    assert _ID_DP1_TB in ids, ("TB fell out of the response entirely", ids)
    derived_only_rank = ids.index(_ID_DP1_D)
    two_base_arm_rank = ids.index(_ID_DP1_TB)

    return {
        "w_derived": aetheryte.fusion_weight_derived,  # off the INSTANCE (VP-M4)
        "p1_recreated": derived_only_rank < two_base_arm_rank,
        "derived_only_rank": derived_only_rank,
        "two_base_arm_rank": two_base_arm_rank,
        "recall_seed_derived_credit": aetheryte.recall_seed_derived_credit,
        "liveness": {"graph": g_live["liveness"], "arms": a_live["liveness"]},
    }


def run_dp1_recheck(
    *, w_derived: float = 1.0, data_root: str = "/tmp/crystalium-dp1-recheck"
) -> dict[str, Any]:
    """crystalium#42's DP-1(b) re-check (AC-352): does a derived-only
    record outrank a record backed by two base arms, WITH seed exclusion
    relaxed on the retrieval path (`recall_seed_derived_credit=True`)?
    Returns `_run_dp1_cell`'s dict UNSTAMPED (no `run_nonce`/`tree_sha`) --
    pass to `emit()` (re-exported from `evals._corpus_rig`, rule (g)) to
    stamp and write it.

    `w_derived=1.0` (the default, and the only value `config.py` documents
    as supported) is the actual re-check; `w_derived=100.0` is AC-352's
    MANDATORY positive control (`config.py:296-298`'s stated ceiling) --
    the caller MUST run it too and confirm `p1_recreated: true` before
    part (i)'s `false` can be read as evidence rather than as a fixture
    that cannot say `true` under any input (global rule (f) / S-14).
    """
    return _run_dp1_cell(w_derived=w_derived, data_root=data_root)
