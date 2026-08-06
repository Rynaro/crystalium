"""crystalium#44 -- status-blind sparse candidate-set top-up (W-44).

D6 (`CHANGE/forge-rulings.md`): this is a REAL production defect, not a
fixture artefact. `_is_active` (`retrieve.py`) returns `True` unconditionally
when `Config.recall_active_only` is `False`, and the `Aetheryte` CONSTRUCTOR
default is `False` -- so a fixture copied blind from the eval template would
exercise dead code. Production wires it `True`: `config.py:347` defaults
`recall_active_only=True` and `server.py:600` / `__main__.py:351` pass it
through unchanged. The `False` at the constructor binds only for DIRECT
construction (evals/tests). So deprecated rows really do consume fetch slots
and starve active hits at default deployment, silently -- every fixture
below therefore pins `recall_active_only=True` and asserts the pin by
READ-BACK off the `Aetheryte` instance (`aetheryte.recall_active_only is
True`), never off the kwargs dict a fixture just wrote (that assertion is a
tautology and cannot fail -- spec.amend-01.md Sec B.4.3 item 2).

D7 (two-commit shape, `forge-rulings.md`): this file lands in two commits.
Commit 1 (this state) adds ONLY `test_prefix_baseline_starves_active_hits`
(AC-345) and `test_topup_recovers_active_hits` (AC-346), no xfail markers,
on the PRE-#44 tree (`retrieve.py` untouched) -- AC-345 is GREEN here
(starvation reproduces) and AC-346 is RED (no top-up exists yet to recover
it). Commit 2 lands the `retrieve.py` fix AND the
`@pytest.mark.xfail(strict=True, ...)` marker on AC-345's node in the SAME
commit -- post-fix AC-345 must report XFAIL (not PASS: XPASS would mean the
starvation regressed and must fail the suite; not FAIL: the characterisation
itself is unchanged, only its relevance is).

Fence: `retrieve.py:605-615` / `:241-242` (`CHANGE/fence-amend.md`, verdict
ALLOW) -- the top-up is caller-side only, reuses the ONE existing
`_is_active` predicate, and never adds a parameter, a new storage method, or
a status predicate to `bm25_search`. This file, plus `retrieve.py`, are the
ONLY files W-44 may touch (`fence-amend.json.authorised_changes`).

Container-first:
  docker compose run --rm crystalium pytest mcp-server/tests/test_sparse_status_topup.py -v
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

from crystalium.aetheryte.retrieve import HARD_TOPUP_CEILING
from crystalium.schemas import Scope
from crystalium.trust import Tier

_PROJECT = "sparse-status-topup"
_AGENT_CLASS = "backend"

# Four fresh nonce terms -- zero lexical overlap with any other gate/test
# module's own corpus in this campaign (separate data root, separate project
# scope in any case; see the grep sweep this file's own maker ran across
# evals/*.py and mcp-server/tests/*.py before picking these).
_QUERY_TERMS = ["obrenthal", "kyzmerith", "duskvane", "ferrolance"]
_QUERY = " ".join(_QUERY_TERMS)

_K = 5
_CANDIDATE_K = max(_K * 3, 10)  # 15, mirrors retrieve.py's FETCH_WIDTH_FLOOR=10
_LAYER = "semantic"
_TARGET_ID = "active-target"


def _summary(label: str, tf: int, pad: int) -> str:
    """All four `_QUERY_TERMS` repeated `tf` times, padded with `label`-
    prefixed non-query nonce tokens to add `pad` extra tokens -- the same
    TF/document-length BM25-rank-separation mechanism D1 measured
    (`CHANGE/kb1-fts5-measurement.txt`) and W-45 already relies on
    (`test_retrieve_layer_merge.py::_summary`)."""
    terms = _QUERY_TERMS * tf
    padding = [f"{label}pad{i:03d}" for i in range(pad)]
    return " ".join([*terms, *padding])


def _build_starved_status_fixture(relational: Any) -> dict[str, Any]:
    """AC-345/346's shared corpus (single-layer path, the simplest fetch
    shape D6's regime needs): `candidate_k` DEPRECATED rows at `tf=6` (no
    padding, the same shape that dominated the K-N12 fixture's episodic
    rows), all strictly better in BM25 than ONE active target at `tf=1`
    (padded to 20 tokens, the same shape as that fixture's semantic
    fillers). `candidate_k + 1` total matching rows -> a plain `k=candidate_k`
    fetch is CENSORED (`len(hits) == candidate_k`) and returns the
    deprecated rows exclusively: the target is starved OUT of the fetch
    entirely, not merely re-ranked."""
    stamps = stamp_sequence()
    crystals: list[dict[str, Any]] = []
    deprecated_ids: list[str] = []
    for i in range(_CANDIDATE_K):
        cid = f"dep{i:02d}"
        deprecated_ids.append(cid)
        crystals.append(
            crystal(
                cid, _LAYER, _summary(cid, 6, 0),
                project=_PROJECT, agent_class=_AGENT_CLASS, created_at=next(stamps),
                status="deprecated",
            )
        )
    crystals.append(
        crystal(
            _TARGET_ID, _LAYER, _summary("target", 1, 16),
            project=_PROJECT, agent_class=_AGENT_CLASS, created_at=next(stamps),
            status="active",
        )
    )
    seed_relational(relational, crystals)
    return {"deprecated_ids": deprecated_ids, "target_id": _TARGET_ID}


def _build_aetheryte_for(stores: Any, *, recall_active_only: bool) -> Any:
    """Every retrieval flag stated explicitly (the rig's own `build_aetheryte`
    contract) -- dense arm pinned EMPTY (`stub_vector_store([])`) so the
    sparse-fetch starvation is the ONLY channel by which the target could
    reach `result.records`, never a dense-arm confound (single-axis
    discipline, spec.md Sec 0.2)."""
    return build_aetheryte(
        cfg=stores.cfg,
        relational=stores.relational,
        vector_store=stub_vector_store([]),
        graph_store=MagicMock(**{"neighbor_expand.return_value": set()}),
        completion=False,
        completion_max_hops=1,
        completion_decay=0.5,
        recall_active_only=recall_active_only,
        recall_relevance_primary=True,
        recall_weighted_fusion=True,
        fusion_weight_dense=1.0,
        fusion_weight_derived=1.0,
        fusion_sparse_boost_alpha=1.0,
    )


# ---------------------------------------------------------------------------
# AC-345 -- REPLACED (K-B5/K-C-N4/K-C-N7/K-C-N8; FORGE D7). Node name
# NORMATIVE (module-level, deliberately NOT class-nested -- see
# `CHANGE/spec.criteria.amend-04.md`'s node-selector sweep).
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason="pre-#44 starvation characterisation; XPASS = starvation regression (#44)",
)
def test_prefix_baseline_starves_active_hits(tmp_path: Path) -> None:
    """Commit-1 characterisation: on the PRE-#44 tree, the top
    `candidate_k` BM25 hits are entirely deprecated and the active target
    NEVER reaches `result.records` -- silent starvation, D6's production
    defect. GREEN here (commit 1); XFAIL-marked in commit 2 once the fix
    lands, so this node's own continued PASS becomes the self-enforcing
    regression signal (XPASS -> strict -> suite RED)."""
    stores = new_stores(str(tmp_path / "topup-baseline"), "baseline")
    fixture = _build_starved_status_fixture(stores.relational)
    aetheryte = _build_aetheryte_for(stores, recall_active_only=True)
    # VP-M4's rule (spec.amend-01.md Sec B.4.3 item 2): read the pin back off
    # the INSTANCE, never off the kwargs dict just written above.
    assert aetheryte.recall_active_only is True

    result = aetheryte.recall(
        Scope(project=_PROJECT, agent_class_visibility=_AGENT_CLASS),
        _QUERY, _K, [_LAYER], Tier.T1,
        explain=True,
    )
    retrieved = [r.id for r in result.records]
    assert fixture["target_id"] not in retrieved, retrieved

    # Positive-capability check (rule (f) discipline): prove the fetch was
    # genuinely censored, not merely absent-by-accident -- LIMIT candidate_k
    # over candidate_k+1 total matches returns EXACTLY candidate_k rows.
    fusion = (result.explain or {}).get("fusion", {})
    assert fusion.get("raw_n_sparse") == _CANDIDATE_K, fusion
    assert fusion.get("n_sparse_cap") == _CANDIDATE_K, fusion


# ---------------------------------------------------------------------------
# AC-346 -- UNCHANGED-BUT-RE-ANCHORED (FORGE D6). Node name NORMATIVE.
# ---------------------------------------------------------------------------


def test_topup_recovers_active_hits(tmp_path: Path) -> None:
    """Post-#44, the SAME corpus recovers the active target into
    `result.records`. RED on the pre-fix tree (this commit); GREEN once the
    `retrieve.py` top-up lands in commit 2."""
    stores = new_stores(str(tmp_path / "topup-recover"), "recover")
    fixture = _build_starved_status_fixture(stores.relational)
    aetheryte = _build_aetheryte_for(stores, recall_active_only=True)
    assert aetheryte.recall_active_only is True

    result = aetheryte.recall(
        Scope(project=_PROJECT, agent_class_visibility=_AGENT_CLASS),
        _QUERY, _K, [_LAYER], Tier.T1,
        explain=True,
    )
    retrieved = [r.id for r in result.records]
    assert fixture["target_id"] in retrieved, retrieved


# ---------------------------------------------------------------------------
# Shared fixtures + spy for AC-348 / AC-356 / AC-379 (below) -- all landed in
# commit 2, alongside the `retrieve.py` fix (D7).
# ---------------------------------------------------------------------------


def _spy_bm25_calls(relational: Any) -> list[tuple[str | None, int]]:
    """Call-count/shape spy (AC-348 / AC-356 / AC-379 part 2's own
    instrument): records `(layer_filter, k)` of every `bm25_search` call
    `Aetheryte.recall` issues, in call order, delegating to the real bound
    method (never altering behaviour) -- every one of those criteria's own
    VERIFY text is explicit: asserted BY CALL COUNT ON A SPY, never by
    reading the source (mirrors `test_retrieve_layer_merge.py::_spy_bm25_calls`,
    extended to also capture `k`, which AC-379 part 2 needs)."""
    calls: list[tuple[str | None, int]] = []
    original = relational.bm25_search

    def _spy(query: str, layer_filter: str | None = None, k: int = 10) -> list[dict[str, Any]]:
        calls.append((layer_filter, k))
        return original(query, layer_filter=layer_filter, k=k)

    relational.bm25_search = _spy  # type: ignore[method-assign]
    return calls


def _build_default_dirty_fixture(relational: Any) -> dict[str, Any]:
    """AC-348's default-path case: `candidate_k * 4` (the global head's own
    width) deprecated rows plus one active target, all in ONE layer -- the
    default (`layers=None`) path issues a single global `bm25_search` call
    regardless of how many DISTINCT layers actually carry content, so a
    single-layer corpus exercises the global-head shape exactly as a
    multi-layer one would."""
    global_k = _CANDIDATE_K * 4
    stamps = stamp_sequence()
    crystals: list[dict[str, Any]] = []
    for i in range(global_k):
        crystals.append(
            crystal(
                f"gdep{i:03d}", _LAYER, _summary(f"gdep{i:03d}", 6, 0),
                project=_PROJECT, agent_class=_AGENT_CLASS, created_at=next(stamps),
                status="deprecated",
            )
        )
    target_id = "global-active-target"
    crystals.append(
        crystal(
            target_id, _LAYER, _summary("gtarget", 1, 16),
            project=_PROJECT, agent_class=_AGENT_CLASS, created_at=next(stamps),
            status="active",
        )
    )
    seed_relational(relational, crystals)
    return {"target_id": target_id, "global_k": global_k}


def _build_subset_status_fixture(relational: Any) -> dict[str, Any]:
    """AC-348's subset case AND AC-379's FORGE-mandated K-N12 regime
    (fence-amend.md Ruling 2 / `composition_ruling`): an excluded-layer-
    dominated CENSORED head (`episodic`, `tf=6`, `candidate_k*4` rows --
    fills the ENTIRE global window, the same domination shape measured for
    `test_retrieve_layer_merge.py`'s own K-N12 fixture) with a target-layer
    (`semantic`) BACKSTOP call that is deprecated-censored (`candidate_k`
    rows at `tf=3` -- strictly below episodic's `tf=6`, strictly above the
    target's `tf=1`+pad16, the SAME proven tf=6>tf=3>tf=1+pad16 chain that
    fixture already measured) above ONE planted active target. The other
    target layer (`procedural`) carries NO matching content at all, so its
    backstop call returns empty and never widens -- singularly focused
    (K-C-N13 / spec.md Sec 0.2 discipline), matching FORGE's fence-amend
    wording verbatim: "a target-layer backstop call ... above A planted
    active target" (singular).

    By construction: the head's post-filtered contribution (`sparse_head`)
    is EMPTY (100% episodic), so the head itself never widens -- this is
    exactly the regime in which a head-only top-up is near-inert, and only
    the backstop-locus widen recovers the target."""
    stamps = stamp_sequence()
    crystals: list[dict[str, Any]] = []
    global_k = _CANDIDATE_K * 4  # 60
    for i in range(global_k):
        crystals.append(
            crystal(
                f"epi{i:03d}", "episodic", _summary(f"epi{i:03d}", 6, 0),
                project=_PROJECT, agent_class=_AGENT_CLASS, created_at=next(stamps),
                status="active",
            )
        )
    for i in range(_CANDIDATE_K):
        crystals.append(
            crystal(
                f"semdep{i:03d}", "semantic", _summary(f"semdep{i:03d}", 3, 0),
                project=_PROJECT, agent_class=_AGENT_CLASS, created_at=next(stamps),
                status="deprecated",
            )
        )
    target_id = "subset-active-target"
    crystals.append(
        crystal(
            target_id, "semantic", _summary("subtarget", 1, 16),
            project=_PROJECT, agent_class=_AGENT_CLASS, created_at=next(stamps),
            status="active",
        )
    )
    seed_relational(relational, crystals)
    return {"target_id": target_id, "global_k": global_k}


# ---------------------------------------------------------------------------
# AC-348 -- REPLACED (FORGE fence-amend `composition_ruling`; openly amends
# D3). Node name NORMATIVE.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tag,layers_arg,build_fixture,expected_calls,budget",
    [
        pytest.param("default", None, _build_default_dirty_fixture, 2, 2, id="default"),
        pytest.param(
            "single-layer", [_LAYER], _build_starved_status_fixture, 2, 2, id="single-layer"
        ),
        pytest.param(
            "subset", ["semantic", "procedural"], _build_subset_status_fixture, 4, 6, id="subset"
        ),
    ],
)
def test_at_most_one_extra_query(
    tmp_path: Path,
    tag: str,
    layers_arg: list[str] | None,
    build_fixture: Any,
    expected_calls: int,
    budget: int,
) -> None:
    """AC-348 -- path-scoped call-count budget WITH the top-up ACTUALLY
    FIRING (never a vacuous "<=" that just happens not to fire): <= 1 extra
    call on default/single-layer, <= 1 + len(target_layers) on strict
    subsets (fence-amend `composition_ruling`; spec.amend-03.md Sec 15.2's
    per-path table: total calls <= 2 on default/single-layer, <= 2 + 2*L on
    strict subsets). Asserted BY CALL COUNT ON A SPY -- never by reading
    the source."""
    stores = new_stores(str(tmp_path / f"budget-{tag}"), tag)
    build_fixture(stores.relational)
    calls = _spy_bm25_calls(stores.relational)
    aetheryte = _build_aetheryte_for(stores, recall_active_only=True)
    assert aetheryte.recall_active_only is True

    aetheryte.recall(
        Scope(project=_PROJECT, agent_class_visibility=_AGENT_CLASS),
        _QUERY, _K, layers_arg, Tier.T1,
    )
    assert len(calls) == expected_calls, calls
    assert len(calls) <= budget, calls


# ---------------------------------------------------------------------------
# AC-356 -- AMENDED (fence breach condition 8). Node name/command unchanged
# from `spec.criteria.amend-01.md`; this is ALSO the mechanical detector for
# breach 8 ("the top-up issuing any additional bm25_search call when
# recall_active_only is False").
# ---------------------------------------------------------------------------


def test_topup_inert_when_active_only_off(tmp_path: Path) -> None:
    """Negative control: `_is_active` returns `True` UNCONDITIONALLY when
    the flag is off, so `n_inactive_observed` is always 0 and no fetch is
    ever dirty -- inertness is STRUCTURAL, not conditional. Same corpus as
    AC-345/346; `recall_active_only=False`. Zero additional `bm25_search`
    calls; `explain.fusion.sparse_topup.fired == False`."""
    stores = new_stores(str(tmp_path / "topup-inert"), "inert")
    _build_starved_status_fixture(stores.relational)
    calls = _spy_bm25_calls(stores.relational)
    aetheryte = _build_aetheryte_for(stores, recall_active_only=False)
    assert aetheryte.recall_active_only is False

    result = aetheryte.recall(
        Scope(project=_PROJECT, agent_class_visibility=_AGENT_CLASS),
        _QUERY, _K, [_LAYER], Tier.T1,
        explain=True,
    )
    # Exactly the baseline single-layer call, zero additional (top-up) calls.
    assert len(calls) == 1, calls

    fusion = (result.explain or {}).get("fusion", {})
    topup = fusion.get("sparse_topup", {})
    assert topup.get("fired") is False, topup
    assert topup.get("widened_fetches") == [], topup


# ---------------------------------------------------------------------------
# AC-379 -- ADDED (FORGE fence-amend: the MANDATED subset-composition node).
# Both node names below are NORMATIVE (spec.amend-03.md Sec 15.3).
# ---------------------------------------------------------------------------


def test_subset_status_topup_recovers_active_hits(tmp_path: Path) -> None:
    """Part 1 -- the discriminating gate. RED under head-only (a widened
    GLOBAL head returns mostly excluded-layer rows the post-filter
    discards; the deprecated-censored per-layer BACKSTOP call, the actual
    starvation locus, is never widened); GREEN only with the
    backstop-locus widen (fence-amend.md Ruling 2 / `composition_ruling`,
    "H-C"). Before this node existed the strict-subset composition had NO
    criterion that could fail on it: AC-346/AC-347 are default-path,
    AC-355 measures layer coverage, AC-348 counts calls only."""
    stores = new_stores(str(tmp_path / "topup-subset"), "subset")
    fixture = _build_subset_status_fixture(stores.relational)
    aetheryte = _build_aetheryte_for(stores, recall_active_only=True)
    assert aetheryte.recall_active_only is True

    result = aetheryte.recall(
        Scope(project=_PROJECT, agent_class_visibility=_AGENT_CLASS),
        _QUERY, _K, ["semantic", "procedural"], Tier.T1,
        explain=True,
    )
    retrieved = [r.id for r in result.records]
    assert fixture["target_id"] in retrieved, retrieved


def test_topup_counter_matches_observed_calls(tmp_path: Path) -> None:
    """Part 2 -- F-V3 counter honesty, using ONLY the fields the ruling
    authorises: `explain.fusion.sparse_topup.widened_fetches` must match
    the SPY's OBSERVED `bm25_search` call sequence exactly -- one entry per
    observed widened call (the Nth, N>=2, call issued for a given fetch
    role), same locus, same `k_final`, with `k_final == min(k_initial +
    n_inactive_observed, HARD_TOPUP_CEILING)`. Also the mechanical detector
    for fence breach condition 7 (a second widen of one fetch, or a loop,
    would surface as an extra or unmatched entry)."""
    stores = new_stores(str(tmp_path / "topup-counter"), "counter")
    _build_subset_status_fixture(stores.relational)
    calls = _spy_bm25_calls(stores.relational)
    aetheryte = _build_aetheryte_for(stores, recall_active_only=True)
    assert aetheryte.recall_active_only is True

    result = aetheryte.recall(
        Scope(project=_PROJECT, agent_class_visibility=_AGENT_CLASS),
        _QUERY, _K, ["semantic", "procedural"], Tier.T1,
        explain=True,
    )

    fusion = (result.explain or {}).get("fusion", {})
    topup = fusion.get("sparse_topup", {})
    widened_fetches = topup.get("widened_fetches", [])

    # Derive the "widened" calls straight from the spy: the Nth call
    # (N >= 2) for a given layer_filter is a second, wider call for that
    # SAME fetch role -- the only shape a bounded, per-fetch, at-most-once
    # widen can take.
    seen_layers: dict[str | None, int] = {}
    observed_widens: list[tuple[str | None, int]] = []
    for layer_filter, k in calls:
        seen_layers[layer_filter] = seen_layers.get(layer_filter, 0) + 1
        if seen_layers[layer_filter] >= 2:
            observed_widens.append((layer_filter, k))

    assert len(widened_fetches) == len(observed_widens), (widened_fetches, calls)
    assert topup.get("fired") == (len(widened_fetches) > 0), topup
    # Positive-capability (rule (f)): prove the derivation isn't vacuous --
    # at least one widen actually happened on this fixture.
    assert widened_fetches, (topup, calls)

    for entry, (layer_filter, k) in zip(widened_fetches, observed_widens):
        assert entry["k_final"] == k, (entry, calls)
        assert entry["k_final"] == min(
            entry["k_initial"] + entry["n_inactive_observed"], HARD_TOPUP_CEILING
        ), entry
        expected_label = "head" if layer_filter is None else f"backstop:{layer_filter}"
        assert entry["fetch"] == expected_label, (entry, calls)
