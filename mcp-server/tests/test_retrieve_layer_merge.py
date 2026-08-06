"""crystalium#45 -- cross-layer rank blocking, Option A (W-45).

FORGE D3 (`CHANGE/forge-rulings.md` §D3) pins the three-case fetch shape in
`retrieve.py` (default/`layers=None` -> one global `bm25_search` call,
score-space by construction; single-layer -> today's one filtered call,
byte-preserving; strict subset `len(target_layers) >= 2` -> global head +
post-filter + a starvation backstop). This module is the K-N12 regression
suite for the THIRD case -- the one case a naive "just merge globally and
post-filter" implementation gets wrong (spec.amend-01.md Sec B.3, K-N12;
spec.amend-03.md Sec 10/15.4, K-C-N13).

**The starved-subset fixture (shared by every test below).** `E = 4 *
candidate_k` episodic rows, TF=6 (24 tokens, no padding) each -- all four
query terms repeated six times, matching all terms under FTS5's implicit-AND
(D1's mechanism, `CHANGE/kb1-fts5-measurement.txt`). `S = candidate_k`
semantic rows: 14 fillers at TF=1 (4 tokens) + 16 padding tokens (20 tokens
total), plus one `sem-target` at TF=3 (12 tokens, no padding). Measured in
the pinned build (sqlite 3.46.1, `docker compose run --rm crystalium`) before
this fixture was written into a test: the 60 episodic rows occupy global BM25
ranks 0-59 (ALL strictly better than every semantic row), and `sem-target`
is rank 0 within the semantic-only order (best of the 15). This is the exact
K-N12 regime: on `layers=["semantic", "procedural"]` (a 2-layer strict
subset), the global head (`k = candidate_k * len(_ALL_LAYERS)`) is entirely
episodic, so a naive global-call + post-filter implementation recovers ZERO
semantic rows -- `sem-target` is lost, not merely re-ranked. Only the
starvation backstop (one `bm25_search(layer_filter=<layer>, k=candidate_k)`
call per target layer, fired because the post-filtered head is starved AND
the head was censored) recovers it.

`k = 5`, `candidate_k = max(k*3, FETCH_WIDTH_FLOOR) = 15` -- same widths as
the G-XL fixture (`evals/cross_layer_gate.py`), so `sem-target`'s rank-0
position within the semantic-only order survives the `[:k]` response window.

Dense arm is pinned EMPTY in the sparse-axis tests (`dense_search.return_value
= []`) -- a `MagicMock` dense stub ignores `layer_filter`, so letting it
answer would post-filter its fixed list and confound the sparse backstop
measurement with dense-stub semantics (spec.amend-01.md Sec B.3.3's own
rationale, Sec 0.2 discipline: one axis per node). AC-378 is the dense
mirror's OWN node, with the sparse arm pinned empty by the symmetric
argument -- and a `layer_filter`-honouring `side_effect` stub (never
`return_value`), because that IS the property under test.

Container-first:
  docker compose run --rm crystalium pytest mcp-server/tests/test_retrieve_layer_merge.py -v
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from evals._corpus_rig import (
    build_aetheryte,
    crystal,
    new_stores,
    seed_relational,
    stamp_sequence,
    stub_vector_store,
)

from crystalium.schemas import Scope
from crystalium.trust import Tier

_PROJECT = "retrieve-layer-merge"
_AGENT_CLASS = "backend"

# Four fresh nonce terms -- zero lexical overlap with fusion_gate.py's or
# cross_layer_gate.py's own corpora (separate data root, separate project
# scope in any case).
_QUERY_TERMS = ["vorlanth", "kestrune", "bellonyx", "thrymnar"]
_QUERY = " ".join(_QUERY_TERMS)

_K = 5
_CANDIDATE_K = max(_K * 3, 10)  # 15, mirrors retrieve.py's FETCH_WIDTH_FLOOR=10
_ALL_LAYERS = ["episodic", "semantic", "procedural", "execution"]

_N_EPISODIC = 4 * _CANDIDATE_K  # 60
_N_SEMANTIC_FILLERS = _CANDIDATE_K - 1  # 14 (+ 1 target == candidate_k == 15)
_TARGET_ID = "sem-target"


def _summary(label: str, tf: int, pad: int) -> str:
    """All four `_QUERY_TERMS` repeated `tf` times, padded with `label`-prefixed
    non-query nonce tokens to add `pad` extra tokens -- the same TF/document-
    length separation mechanism D1 measured for the G-XL fixture."""
    terms = _QUERY_TERMS * tf
    padding = [f"{label}pad{i:03d}" for i in range(pad)]
    return " ".join([*terms, *padding])


def _build_starved_subset_fixture(relational: Any) -> dict[str, Any]:
    """K-N12 fixture (spec.amend-01.md Sec B.3.3 / AC-355): `E = 4*candidate_k`
    episodic rows, all strictly better in BM25 than `S = candidate_k` semantic
    rows including one planted target. Measured (not derived) before use:

        top 60 global BM25 ranks == all 60 episodic rows (0 semantic present)
        sem-target rank within the semantic-only order == 0 (best of 15)

    On `layers=["semantic", "procedural"]` the global head is therefore
    entirely excluded-layer noise -- the K-N12 regime.
    """
    stamps = stamp_sequence()
    crystals: list[dict[str, Any]] = []
    episodic_ids: list[str] = []
    for i in range(_N_EPISODIC):
        cid = f"ep{i:02d}"
        episodic_ids.append(cid)
        crystals.append(
            crystal(
                cid, "episodic", _summary(cid, 6, 0),
                project=_PROJECT, agent_class=_AGENT_CLASS, created_at=next(stamps),
            )
        )
    filler_ids: list[str] = []
    for i in range(_N_SEMANTIC_FILLERS):
        cid = f"semf{i:02d}"
        filler_ids.append(cid)
        crystals.append(
            crystal(
                cid, "semantic", _summary(cid, 1, 16),
                project=_PROJECT, agent_class=_AGENT_CLASS, created_at=next(stamps),
            )
        )
    crystals.append(
        crystal(
            _TARGET_ID, "semantic", _summary("target", 3, 0),
            project=_PROJECT, agent_class=_AGENT_CLASS, created_at=next(stamps),
        )
    )
    seed_relational(relational, crystals)
    return {"episodic_ids": episodic_ids, "filler_ids": filler_ids, "target_id": _TARGET_ID}


def _build_uncensored_subset_fixture(relational: Any) -> dict[str, Any]:
    """AC-377 case (ii): a corpus SMALLER than the global request
    (`candidate_k * len(_ALL_LAYERS) == 60`) on the SAME 2-layer subset path,
    so the global head is NOT censored. Three decoy rows (semantic x2,
    procedural x1) carry NONE of the query terms -- under FTS5's implicit-AND
    they never match the query (`CHANGE/kb1-fts5-measurement.txt`), so they
    are absent from `sparse_ranking` but still counted by `count_for_export`
    (a store-population aggregate, not query-conditional) -- this is what
    makes `n_scoped > resolved_n_sparse` and `selectivity` measurably
    positive rather than a vacuous 0/0.
    """
    stamps = stamp_sequence()
    crystals = [
        crystal("eu0", "episodic", _summary("eu0", 2, 0), project=_PROJECT, agent_class=_AGENT_CLASS, created_at=next(stamps)),
        crystal("eu1", "episodic", _summary("eu1", 2, 4), project=_PROJECT, agent_class=_AGENT_CLASS, created_at=next(stamps)),
        crystal("eu2", "episodic", _summary("eu2", 2, 8), project=_PROJECT, agent_class=_AGENT_CLASS, created_at=next(stamps)),
        crystal("semu-filler", "semantic", _summary("semuf", 1, 16), project=_PROJECT, agent_class=_AGENT_CLASS, created_at=next(stamps)),
        crystal(_TARGET_ID, "semantic", _summary("targetu", 3, 0), project=_PROJECT, agent_class=_AGENT_CLASS, created_at=next(stamps)),
        crystal("semu-decoy0", "semantic", "unrelated filler content alpha bravo", project=_PROJECT, agent_class=_AGENT_CLASS, created_at=next(stamps)),
        crystal("semu-decoy1", "semantic", "unrelated filler content charlie delta", project=_PROJECT, agent_class=_AGENT_CLASS, created_at=next(stamps)),
        crystal("procu-decoy0", "procedural", "unrelated filler content echo foxtrot", project=_PROJECT, agent_class=_AGENT_CLASS, created_at=next(stamps)),
    ]
    seed_relational(relational, crystals)
    return {"target_id": _TARGET_ID}


def _dense_global_order(fixture: dict[str, Any]) -> list[dict[str, str]]:
    """The dense arm's GLOBAL ranking (score order), dominated by the excluded
    `episodic` layer -- mirrors `_build_starved_subset_fixture`'s shape
    exactly so the AC-378 dense node exercises the identical K-N12 regime on
    the other arm: `sem-target` at rank 0 within the semantic-only slice, but
    buried past the global head's `k` window by 60 excluded-layer rows."""
    return (
        [{"id": cid, "layer": "episodic"} for cid in fixture["episodic_ids"]]
        + [{"id": fixture["target_id"], "layer": "semantic"}]
        + [{"id": cid, "layer": "semantic"} for cid in fixture["filler_ids"]]
    )


def _layer_honouring_dense_stub(global_order: list[dict[str, str]]):
    """AC-378's `side_effect` stub: returns only rows of the requested layer
    (mirroring `vector.py:199-200`'s `where(layer = ...)`), or the full
    global ordering when `layer_filter is None` (`vector.py:174-199` supports
    it) -- the real `dense_search` contract, never invented. A `return_value`
    `MagicMock` (the rig's `stub_vector_store`) CANNOT express this: it
    ignores `layer_filter` entirely, which is exactly the property this node
    must exercise (spec.amend-01.md Sec B.3.3's own maker-note; AC-378)."""

    def _stub(query_vec: Any, layer_filter: str | None = None, k: int = 10) -> list[dict[str, str]]:
        if layer_filter is None:
            return list(global_order)[:k]
        return [row for row in global_order if row["layer"] == layer_filter][:k]

    return _stub


def _spy_bm25_calls(relational: Any) -> list[str | None]:
    """Call-count spy (AC-348 baseline): records the `layer_filter` of every
    `bm25_search` call `Aetheryte.recall` issues, in call order, without
    altering behaviour (delegates to the real bound method)."""
    calls: list[str | None] = []
    original = relational.bm25_search

    def _spy(query: str, layer_filter: str | None = None, k: int = 10) -> list[dict[str, Any]]:
        calls.append(layer_filter)
        return original(query, layer_filter=layer_filter, k=k)

    relational.bm25_search = _spy  # type: ignore[method-assign]
    return calls


def _build_aetheryte_for(stores: Any, vector_store: Any) -> Any:
    return build_aetheryte(
        cfg=stores.cfg,
        relational=stores.relational,
        vector_store=vector_store,
        graph_store=MagicMock(**{"neighbor_expand.return_value": set()}),
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


# ---------------------------------------------------------------------------
# AC-355 -- K-N12, the mandatory subset-starvation node (node name NORMATIVE).
# Bare module-level function, deliberately NOT class-nested: the criteria
# and amend-04's declared PENDING manifest both name the 2-segment selector
# `test_retrieve_layer_merge.py::test_subset_layer_recall_no_regression` --
# class-nesting it would reproduce the exact K-N21 defect amend-04 exists to
# fix elsewhere (`release-blocker-node-selectors.md`: `file.py::func` cannot
# collect a class-qualified node).
# ---------------------------------------------------------------------------


def test_subset_layer_recall_no_regression(tmp_path: Path) -> None:
    """RED on a naive global+post-filter implementation (the global head
    is 100% excluded-layer noise, so post-filtering it alone recovers
    ZERO semantic rows); GREEN only when the starvation backstop exists."""
    stores = new_stores(str(tmp_path / "kn12-sparse"), "subset-recall")
    fixture = _build_starved_subset_fixture(stores.relational)
    aetheryte = _build_aetheryte_for(stores, stub_vector_store([]))

    result = aetheryte.recall(
        Scope(project=_PROJECT, agent_class_visibility=_AGENT_CLASS),
        _QUERY, _K, ["semantic", "procedural"], Tier.T1,
        explain=True,
    )
    retrieved = [r.id for r in result.records]
    assert fixture["target_id"] in retrieved, retrieved

    fusion = (result.explain or {}).get("fusion", {})
    sparse_size = fusion.get("arm_sizes", {}).get("sparse")
    assert sparse_size >= min(_CANDIDATE_K, _CANDIDATE_K), (sparse_size, fusion)


# ---------------------------------------------------------------------------
# AC-378 -- the dense mirror (node name NORMATIVE; bare, same reason as above)
# ---------------------------------------------------------------------------


def test_subset_layer_dense_mirror_no_regression(tmp_path: Path) -> None:
    """The dense half of D3's "sparse shown; dense mirrors it" mandate --
    AC-355 pins the dense arm EMPTY (single-axis discipline), so without
    this node the dense mirror shipped completely ungated. RED on a naive
    global+post-filter dense implementation; GREEN only with the dense
    backstop."""
    stores = new_stores(str(tmp_path / "kn12-dense"), "subset-dense")
    fixture = _build_starved_subset_fixture(stores.relational)
    # Sparse pinned empty -- one axis (symmetric to AC-355's dense pin).
    stores.relational.bm25_search = lambda *a, **kw: []  # type: ignore[method-assign]

    global_order = _dense_global_order(fixture)
    vector_store = MagicMock()
    vector_store.embed.return_value = [0.1, 0.2, 0.3]
    vector_store.dense_search.side_effect = _layer_honouring_dense_stub(global_order)

    aetheryte = _build_aetheryte_for(stores, vector_store)

    result = aetheryte.recall(
        Scope(project=_PROJECT, agent_class_visibility=_AGENT_CLASS),
        _QUERY, _K, ["semantic", "procedural"], Tier.T1,
        explain=True,
    )
    retrieved = [r.id for r in result.records]
    assert fixture["target_id"] in retrieved, retrieved

    fusion = (result.explain or {}).get("fusion", {})
    dense_size = fusion.get("arm_sizes", {}).get("dense")
    assert dense_size >= min(_CANDIDATE_K, _CANDIDATE_K), (dense_size, fusion)


# ---------------------------------------------------------------------------
# AC-377 -- the censoring signal is the fetch that produced the HEAD
# (node name NORMATIVE, bare; two parametrised cases, both required)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "case",
    ["censored", "uncensored"],
)
def test_subset_censoring_signal_is_the_global_fetch(tmp_path: Path, case: str) -> None:
    """(i) censored: the global head IS censored (returns exactly
    `candidate_k * len(_ALL_LAYERS)` rows) -- `selectivity` must stay
    0.0, sourced from the HEAD's raw count, never from
    `len(sparse_ranking)` (which on this path is post-filtered head +
    backstop tail and is NOT the fetch that produced the signal, K-C-N13).
    (ii) uncensored: a smaller corpus on the SAME 2-layer subset path --
    the rule-(f) Form-1 positive control -- `selectivity` must be
    measurably > 0."""
    stores = new_stores(str(tmp_path / f"kn12-censor-{case}"), f"censor-{case}")
    if case == "censored":
        _build_starved_subset_fixture(stores.relational)
    else:
        _build_uncensored_subset_fixture(stores.relational)

    aetheryte = _build_aetheryte_for(stores, stub_vector_store([]))

    result = aetheryte.recall(
        Scope(project=_PROJECT, agent_class_visibility=_AGENT_CLASS),
        _QUERY, _K, ["semantic", "procedural"], Tier.T1,
        explain=True,
    )
    fusion = (result.explain or {}).get("fusion", {})

    if case == "censored":
        assert fusion.get("selectivity") == 0.0, fusion
        assert fusion.get("w_sparse") == 1.0, fusion
        assert fusion.get("n_sparse_cap") == _CANDIDATE_K * 4, fusion
        assert fusion.get("raw_n_sparse") == _CANDIDATE_K * 4, fusion
        assert fusion.get("sparse_fetch_shape") == "global+backstop", fusion
    else:
        assert fusion.get("selectivity", 0.0) > 0.0, fusion
        assert fusion.get("w_sparse", 1.0) > 1.0, fusion


# ---------------------------------------------------------------------------
# AC-348 baseline -- the exact per-path bm25_search call-count budget BEFORE
# #44's top-up exists (W-44 composes its own <=1-per-fetch increment on top
# of this baseline; see spec.criteria.amend-03.md Sec 15.2). Not itself one
# of W-45's mandated node names, but the per-path shape D3's PLAN CONSEQUENCE
# calls out explicitly -- kept here, in W-45's own file, so the exact
# fetch-shape call count this campaign's later links depend on is pinned and
# regression-tested from the first commit that establishes it.
# ---------------------------------------------------------------------------


class TestCallBudgetPerPath:
    @pytest.mark.parametrize(
        "layers_arg,expected_calls",
        [
            pytest.param(None, 1, id="default"),
            pytest.param(["semantic"], 1, id="single-layer"),
            pytest.param(["semantic", "procedural"], 3, id="subset"),
        ],
    )
    def test_call_budget_per_path(
        self, tmp_path: Path, layers_arg: list[str] | None, expected_calls: int
    ) -> None:
        stores = new_stores(str(tmp_path / f"kn12-budget-{expected_calls}"), f"budget-{expected_calls}")
        # The starved fixture is used for all three paths -- for
        # default/single-layer the call count is path-shape-determined
        # (never data-dependent); for the subset path this specific fixture
        # is what makes the backstop actually fire (2 backstop calls, one
        # per target layer), so the budget's upper edge is exercised, not
        # just its shape.
        _build_starved_subset_fixture(stores.relational)
        calls = _spy_bm25_calls(stores.relational)
        aetheryte = _build_aetheryte_for(stores, stub_vector_store([]))

        aetheryte.recall(
            Scope(project=_PROJECT, agent_class_visibility=_AGENT_CLASS),
            _QUERY, _K, layers_arg, Tier.T1,
        )
        assert len(calls) == expected_calls, calls
