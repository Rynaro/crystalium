"""crystalium#52 -- the `cross_layer` axis cannot fail (W-G-XL).

**What #52 found.** The axis that shipped under the name `cross_layer`
(`evals/fusion_gate.py::run_arm`) never called `Aetheryte.recall` with
`layers=None` -- it called `relational.bm25_search` PER LAYER, directly, and
both pinned values (`episodic`, `semantic`) were 0. It could not fail on the
defect it was named for (crystalium#45: cross-layer rank blocking). This
module is the gate that ACTUALLY measures the fused rank through
`Aetheryte.recall`, and it is expected to be RED on `b7f1a47` -- see "RED
expected by derivation" below.

**The fixture (FORGE D1; spec.amend-01.md Sec B.2, normative -- the original
plan's fixture was measured WRONG and superseded).** The plan originally
specified "N weakly-matching episodic fillers, one query term each". That is
FATAL: `relational.py::_fts5_query` quotes every token and joins with a
space, and FTS5 space-separated phrases are IMPLICIT AND -- a row carrying a
strict SUBSET of the query terms matches NOTHING at all (measured in the
pinned sqlite 3.46.1 build, `CHANGE/kb1-fts5-measurement.txt`). That fixture
yields an EMPTY episodic arm, `sem-target` alone at rank 0, the gate GREEN
for the wrong reason -- STOP S-3, critical path dead at link 2 (#45 gates on
this gate being demonstrably RED first).

D1's rebuilt fixture separates by TERM FREQUENCY and DOCUMENT-LENGTH
NORMALISATION instead -- the only two channels that exist under FTS5
implicit-AND, both pure deterministic functions of the corpus:

  `_XL_QUERY = "quorvex blenthar mizzletine korvath"` -- four fresh nonce
  terms, zero lexical overlap with `fusion_gate.py`'s own corpus (separate
  data root, separate project scope -- the two gates can never
  cross-pollinate).

  `episodic`: three fillers `ep1`/`ep2`/`ep3`. EACH contains all four query
  terms exactly once, padded with per-filler-unique non-query nonce tokens
  to STRICTLY DISTINCT lengths -- 24 / 32 / 40 tokens. Distinct lengths give
  a strict BM25 total order among the three (no ties).

  `semantic`: one `sem-target`, whose summary is each query term repeated
  THREE times and nothing else (12 tokens). TF 3-vs-1 and doc-length
  12-vs->=24 make it strictly the best BM25 match in the whole corpus --
  measured during the amendment pass (sqlite 3.46.1, ascending `bm25()`,
  best-first):

      sem-target  -7.1351351351351354e-06     <- global rank 0
      ep1         -4.1904761904761911e-06
      ep2         -3.7183098591549303e-06
      ep3         -3.3417721518987344e-06
      strict_total_order: True   sem_target_is_rank0: True

  `procedural`/`execution` stay empty -- they contribute nothing to
  `sparse_ranking`.

  `k = 5`, so `candidate_k = max(k*3, FETCH_WIDTH_FLOOR) = max(15, 10) = 15`.
  `corpus_per_layer + 1 (4) < candidate_k (15)` -- truncation is
  non-binding, so `candidate_k` cannot be the cause (the deconfound rule).
  `k (5) > corpus_per_layer (3)` -- the blocked target stays inside the
  `[:k]` response window; otherwise this gate would measure eviction
  (`retrieve.py:854`'s `filtered_ids[:k]`), not append order.

  Dense stub returns `[]` -- NOT a "neutral fixed list" (that variant is
  REVOKED, spec.amend-01.md Sec B.2.3: it contradicts AC-312's
  `dense_arm_size == 0`, resolved by FORGE in AC-312's favour). Graph is a
  real `GraphStore`, edgeless (`node_count() == 4`, `len(all_edges()) == 0`
  -- never `all_edges() == 0`, which is `[] == 0` and always `False` in
  Python, Sec A.3 / K-B16). Completion off. `recall_active_only=False` (the
  corpus is single-status; the status axis belongs to #44's own fixture).
  => the sparse arm is the ONLY voter, by construction AND by assertion.

**RED expected by derivation, established by measurement before W-45 may
start** (spec.amend-01.md Sec B.2.6 -- "RED by construction" is STRUCK from
the plan's vocabulary). With one live arm, `weighted_rrf_merge_scored`
(`retrieve.py:741-748`) reproduces the sparse order verbatim. Per-layer
append (`retrieve.py:521-530`, `_ALL_LAYERS` order
`["episodic","semantic","procedural","execution"]`, `retrieve.py:44`) gives,
at `b7f1a47`:

    sparse_ranking == ["ep1", "ep2", "ep3", "sem-target"]   target_rank == 3

`target_rank == expected_blocked_rank == corpus_per_layer == 3` is the
assertion this gate makes -- never `!= 0`, which passes on `null` and on the
repo's absent-sentinel `-1` (K-B4). `run()` writes
`evals/results/cross-layer-gate.json`; `CHANGE/vp-m2-gxl-red.json` is a copy
of that artifact, stamped with a run nonce and tree sha (rule (g)), and its
existence + green AC-310/AC-312 predicates is W-45's entry precondition --
not a claim in a spec document.

**Controls (C-XL-1/2/3), all three permanent test nodes** (see
`mcp-server/tests/test_cross_layer_gate.py`):

  - C-XL-1 (single-layer control, `test_single_layer_control_is_rank_zero`):
    the SAME fixture restricted to `layers=["semantic"]` puts `sem-target`
    at fused rank 0 TODAY. Red here means the fixture's BM25 assumption is
    wrong and the gate is red for the wrong reason -> STOP S-4.
  - C-XL-2 (arm liveness, folded into this module's `liveness` object and
    AC-312): `sparse_arm_size == corpus_per_layer + 1` -- the single
    assertion that catches a filler silently NOT matching under FTS5
    implicit-AND (K-B1's failure mode) -- `dense_arm_size == 0`,
    `edge_count == 0` AND `node_count == 4` together (never `edge_count`
    alone), `corpus_per_layer < candidate_k`, `k > corpus_per_layer`. Any
    deviation => `verdict: "confounded"`, never numbers (R-CONF).
  - C-XL-3 (global-premise probe, folded into `liveness.global_bm25_rank0`):
    a READ-ONLY call to `relational.bm25_search(_XL_QUERY, layer_filter=None,
    k=60)` -- the exact in-repo precedent at `fusion_gate.py:257-262` -- no
    fence contact (no new parameter, no status predicate, no new public
    method). It converts the fixture's BM25 assumption from prose into an
    in-gate assertion and is what makes the RED ATTRIBUTABLE: global rank 0
    + fused rank `corpus_per_layer` can ONLY be layer-append order.

`sparse_ranking`'s provenance is a RECORDING SPY on `relational.bm25_search`
(FORGE D5's recording-proxy pattern, reused) -- never a re-issued query
after the fact, which could diverge from what `Aetheryte.recall` actually
did. `liveness.sparse_arm_size` is read off `RecallResult.explain` (the
value `Aetheryte.recall` itself reports); `len(sparse_ranking) ==
liveness.sparse_arm_size` is the binding self-check that makes the spy
answerable to `explain` (K-C-N10).

This gate ships one strict-xfail test node
(`test_fused_rank_matches_global_bm25_once_layers_merge`, citing
crystalium#45) asserting the POST-FIX expectation (`target_rank == 0`).
Strict xfail is self-enforcing: landing #45 turns it XPASS, which fails the
suite until W-45 also removes the marker -- the repo's own precedent,
`test_fusion_gate.py:85-103`.

Container-first:
  docker compose run --rm crystalium /app/.venv/bin/python \
    -c "import evals.cross_layer_gate as m; m.emit(m.run(), '/app/evals/results/cross-layer-gate.json')"
  docker compose run --rm crystalium pytest mcp-server/tests/test_cross_layer_gate.py -v
"""

from __future__ import annotations

import os
import uuid
from typing import Any

from evals._corpus_rig import (
    assemble_result,
    build_aetheryte,
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

_PROJECT = "cross-layer-gate"
_AGENT_CLASS = "backend"

# Four fresh nonce terms -- zero lexical overlap with fusion_gate.py's own
# corpus (`plarnix threxil vandomere signature`), by construction: separate
# data root, separate project scope, disjoint token sets (spec.amend-01.md
# Sec B.2.1).
_XL_QUERY = "quorvex blenthar mizzletine korvath"
_XL_QUERY_TERMS = _XL_QUERY.split()

_EPISODIC_IDS: tuple[str, ...] = ("ep1", "ep2", "ep3")
_FILLER_LENGTHS: dict[str, int] = {"ep1": 24, "ep2": 32, "ep3": 40}
_TARGET_ID = "sem-target"

_K = 5


def _filler_summary(label: str, total_tokens: int) -> str:
    """All four `_XL_QUERY_TERMS` exactly once, padded with `label`-prefixed
    non-query nonce tokens to `total_tokens` (spec.amend-01.md Sec B.2.1 --
    "per-filler unique", so no two fillers ever share a padding token)."""
    pad_count = total_tokens - len(_XL_QUERY_TERMS)
    assert pad_count >= 0, (label, total_tokens)
    padding = [f"{label}pad{i:02d}" for i in range(pad_count)]
    return " ".join([*_XL_QUERY_TERMS, *padding])


def _sem_target_summary() -> str:
    """Each query term repeated 3x, nothing else -- 12 tokens. TF 3-vs-1 and
    doc-length 12-vs->=24 make this strictly the best BM25 match in the
    corpus (spec.amend-01.md Sec B.2.1's measured order)."""
    return " ".join(term for term in _XL_QUERY_TERMS for _ in range(3))


def build_fixture(relational: Any, graph: Any) -> dict[str, Any]:
    """Seeds the FORGE D1 fixture (spec.amend-01.md Sec B.2.1-B.2.3) into a
    real `RelationalStore` + real `GraphStore`: three episodic fillers
    carrying all four query terms at strictly distinct lengths (a strict
    BM25 total order, no ties) and one `semantic` `sem-target` whose term
    frequency and short length make it the strict global BM25 best. The
    graph gets one node per crystal (4 total) and ZERO edges -- edgeless
    liveness (Sec A.3), never assumed.

    Returns `{"episodic_ids": [...], "target_id": "sem-target"}` -- this is
    the fixture used identically by `run()` (the gate) and by
    `test_cross_layer_gate.py`'s C-XL-1/C-XL-3 controls, so all three read
    the exact same corpus.
    """
    stamps = stamp_sequence()
    fillers = [
        crystal(
            eid,
            "episodic",
            _filler_summary(eid, _FILLER_LENGTHS[eid]),
            project=_PROJECT,
            agent_class=_AGENT_CLASS,
            created_at=next(stamps),
        )
        for eid in _EPISODIC_IDS
    ]
    sem_target = crystal(
        _TARGET_ID,
        "semantic",
        _sem_target_summary(),
        project=_PROJECT,
        agent_class=_AGENT_CLASS,
        created_at=next(stamps),
    )

    seed_relational(relational, [*fillers, sem_target])
    seed_graph(
        graph,
        nodes=[(eid, "episodic") for eid in _EPISODIC_IDS] + [(_TARGET_ID, "semantic")],
        # no edges -- edgeless graph, asserted (never assumed) by graph_liveness below
    )
    return {"episodic_ids": list(_EPISODIC_IDS), "target_id": _TARGET_ID}


def _spy_bm25_search(relational: Any) -> list[dict[str, Any]]:
    """D5's recording-proxy pattern, reused (K-C-N10): wraps
    `relational.bm25_search` on THIS INSTANCE ONLY so every call
    `Aetheryte.recall` makes during the run is captured verbatim. Re-issuing
    `bm25_search` after the fact to reconstruct `sparse_ranking` is
    FORBIDDEN by the same amendment -- that is a re-implementation which can
    diverge from what `recall` actually did, the exact risk D5's self-check
    exists to close. Returns the list the spy appends
    `{"layer_filter": ..., "ids": [...]}` rows to, in call order; callers
    reconstruct the dedup'd first-seen union with `_union_first_seen` AFTER
    `recall()` returns, exactly mirroring `retrieve.py:526-530`'s own
    dedup discipline.

    No restore/`finally` is needed: `relational` is a fresh, single-use
    `RelationalStore` bound to this run's own `data_root`/tag (never shared
    across gates or across calls), so the instance-level override cannot
    leak into any other run.
    """
    calls: list[dict[str, Any]] = []
    original = relational.bm25_search

    def _spy(query: str, layer_filter: str | None = None, k: int = 10) -> list[dict[str, Any]]:
        hits = original(query, layer_filter=layer_filter, k=k)
        calls.append({"layer_filter": layer_filter, "ids": [h["id"] for h in hits]})
        return hits

    relational.bm25_search = _spy  # type: ignore[method-assign]
    return calls


def _union_first_seen(calls: list[dict[str, Any]]) -> list[str]:
    """Dedup'd first-seen union across the spy's captured calls -- the same
    reduction `retrieve.py:526-530` performs inline while building
    `sparse_ranking`, applied here to the spy's own record instead of a
    fresh query."""
    union: list[str] = []
    for call in calls:
        for cid in call["ids"]:
            if cid not in union:
                union.append(cid)
    return union


def run(*, data_root: str = "/tmp/crystalium-cross-layer-gate") -> dict[str, Any]:
    """crystalium#52 (W-G-XL) -- the cross-layer gate, THROUGH
    `Aetheryte.recall`. See the module docstring for the fixture, the
    pre-fix expected state, and the three controls this folds in
    (C-XL-2/C-XL-3 here; C-XL-1 is its own test node since it calls
    `recall` with a DIFFERENT `layers=` argument).
    """
    from crystalium.aetheryte.retrieve import FETCH_WIDTH_FLOOR
    from crystalium.schemas import Scope
    from crystalium.trust import Tier

    os.makedirs(data_root, exist_ok=True)
    tag = f"xl-{uuid.uuid4().hex[:8]}"
    stores = new_stores(data_root, tag)

    fixture = build_fixture(stores.relational, stores.graph)
    corpus_per_layer = len(fixture["episodic_ids"])
    target_id = fixture["target_id"]

    k = _K
    candidate_k = max(k * 3, FETCH_WIDTH_FLOOR)
    # Widths table (spec.amend-01.md Sec B.2.2) -- design invariants, not
    # runtime-measured liveness: a failure here is a fixture-construction
    # bug in THIS module, not a result to report gracefully.
    assert corpus_per_layer + 1 < candidate_k, (corpus_per_layer, candidate_k)  # truncation non-binding
    assert k > corpus_per_layer, (k, corpus_per_layer)  # blocked target stays inside [:k]

    # C-XL-3 -- global-premise probe: a READ-ONLY use of the shared method,
    # never issued again after the spied recall() call below.
    global_hits = stores.relational.bm25_search(_XL_QUERY, layer_filter=None, k=60)
    global_bm25_rank0 = global_hits[0]["id"] if global_hits else None

    graph_live = graph_liveness(stores.graph, expected_node_count=4, expected_edge_count=0)

    vector_store = stub_vector_store([])  # dense stub returns [] -- Sec B.2.3, "neutral list" REVOKED

    aetheryte = build_aetheryte(
        cfg=stores.cfg,
        relational=stores.relational,
        vector_store=vector_store,
        graph_store=stores.graph,
        completion=False,
        completion_max_hops=1,
        completion_decay=0.5,
        recall_active_only=False,
        recall_relevance_primary=True,
        recall_weighted_fusion=True,
        fusion_weight_dense=1.0,
        fusion_weight_derived=1.0,
        fusion_sparse_boost_alpha=1.0,
    )

    spy_calls = _spy_bm25_search(stores.relational)

    result = aetheryte.recall(
        Scope(project=_PROJECT, agent_class_visibility=_AGENT_CLASS),
        _XL_QUERY,
        k,
        None,  # layers=None -- ALL layers, the surface #52 found unmeasured
        Tier.T1,
        explain=True,
    )
    retrieved = [r.id for r in result.records]
    target_rank = retrieved.index(target_id) if target_id in retrieved else -1

    explain_fusion = (result.explain or {}).get("fusion", {})
    sparse_arm_size = explain_fusion.get("arm_sizes", {}).get("sparse")
    dense_arm_size = explain_fusion.get("arm_sizes", {}).get("dense")

    sparse_ranking = _union_first_seen(spy_calls)

    pinned = {
        "graph_edgeless": graph_live["verdict"] == "measured",
        "dense_arm_empty": dense_arm_size == 0,
        # K-C-N10's binding self-check: the spy's own union must equal the
        # size `explain` (Aetheryte's own report) attributes to the sparse arm.
        "sparse_spy_matches_explain": len(sparse_ranking) == sparse_arm_size,
        # C-XL-2 / K-B1's failure mode: a filler silently not matching.
        "sparse_arm_size_expected": sparse_arm_size == corpus_per_layer + 1,
        "corpus_per_layer_lt_candidate_k": corpus_per_layer < candidate_k,
        "k_gt_corpus_per_layer": k > corpus_per_layer,
        # C-XL-3.
        "global_bm25_rank0_is_target": global_bm25_rank0 == target_id,
    }
    verdict = resolve_verdict(pinned=pinned)

    liveness = {
        "corpus_per_layer": corpus_per_layer,
        "candidate_k": candidate_k,
        "k": k,
        "sparse_arm_size": sparse_arm_size,
        "dense_arm_size": dense_arm_size,
        "edge_count": graph_live["liveness"]["edge_count"],
        "node_count": graph_live["liveness"]["node_count"],
        "global_bm25_rank0": global_bm25_rank0,
    }

    return assemble_result(
        verdict=verdict,
        liveness=liveness,
        axes=None,
        target_rank=target_rank,
        expected_blocked_rank=corpus_per_layer,
        sparse_ranking=sparse_ranking,
        sparse_ranking_provenance="bm25_search_spy",
    )


__all__ = ["run", "emit", "build_fixture"]
