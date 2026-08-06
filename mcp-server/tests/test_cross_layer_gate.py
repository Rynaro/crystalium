"""crystalium#52 -- pytest wrapper for the cross-layer gate (W-G-XL).

AC-310/AC-311/AC-312/AC-313/AC-359 (`spec.criteria.amend-03.md`); C-XL-1/2/3
(`spec.amend-01.md` Sec B.2.5, re-designated in `spec.amend-03.md` Sec 8.1).
`evals/cross_layer_gate.py`'s module docstring carries the full mechanism and
the measured pre-fix numbers; this file pins the verdict as pytest nodes so
`make test` / CI catches a regression, mirroring `test_fusion_gate.py`'s
relationship to `evals/fusion_gate.py`.

Container-first: run via
  docker compose run --rm crystalium pytest mcp-server/tests/test_cross_layer_gate.py -v
"""

from __future__ import annotations

from pathlib import Path

from evals._corpus_rig import (
    build_aetheryte,
    crystal,
    new_stores,
    seed_graph,
    seed_relational,
    stamp_sequence,
    stub_vector_store,
)
from evals.cross_layer_gate import (
    _EPISODIC_IDS,
    _FILLER_LENGTHS,
    _TARGET_ID,
    _XL_QUERY,
    _filler_summary,
    _sem_target_summary,
    build_fixture,
    run,
)

_PROJECT = "cross-layer-gate"
_AGENT_CLASS = "backend"


def _build_aetheryte_for(stores, *, dense_hits: list[str] | None = None):
    return build_aetheryte(
        cfg=stores.cfg,
        relational=stores.relational,
        vector_store=stub_vector_store(dense_hits or []),
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


class TestCrossLayerGateRedness:
    """crystalium#45 FIXED (W-45, Option A). Pre-fix, `b7f1a47`, this class
    asserted the fused rank was NOT 0 -- blocked at the per-layer append
    position (`AC-310`). That pre-fix characterisation is preserved
    PERMANENTLY in git history (`release/v2.0.2` commit `a16c072`, W-G-XL)
    and in `CHANGE/vp-m2-gxl-red.json`; it is not re-asserted here on every
    future tree, exactly as AC-345's D7 two-commit shape preserves a
    pre-fix characterisation via a pinned SHA rather than a permanently-red
    node on HEAD. Post-fix, the SAME fixture's `Aetheryte.recall` call now
    goes through the Option A global fetch shape (`retrieve.py`'s
    `_is_no_exclusion` case): `sem-target` is the strict global BM25 best
    (C-XL-3 / `TestGlobalPremiseProbe`, unaffected by this change), so the
    fused rank is now 0 -- AC-340's oracle."""

    def test_target_not_blocked_at_layer_append_rank(self, tmp_path: Path) -> None:
        result = run(data_root=str(tmp_path / "xl-gate"))
        assert result["verdict"] == "measured", result
        assert isinstance(result["target_rank"], int), result
        assert isinstance(result["expected_blocked_rank"], int), result
        assert result["expected_blocked_rank"] == result["liveness"]["corpus_per_layer"], result
        assert result["expected_blocked_rank"] == 3, result
        # AC-340's oracle: the target is no longer blocked at the per-layer
        # append position -- it is the global BM25 best, fused rank 0.
        assert result["target_rank"] != result["expected_blocked_rank"], result
        assert result["target_rank"] == 0, result

    def test_sparse_ranking_is_global_bm25_order(self, tmp_path: Path) -> None:
        """The mechanism, not just the number: post-#45, `sparse_ranking` is
        the ONE global call's score-space order (`relational.py:531-541`
        orders by `bm25(crystals_fts)` with no layer filter) -- `sem-target`
        first (the strict global BM25 best, C-XL-3), the three episodic
        fillers after it in their own strict BM25 order."""
        result = run(data_root=str(tmp_path / "xl-gate-order"))
        assert result["sparse_ranking"][0] == "sem-target", result
        assert result["sparse_ranking"].index("sem-target") == 0, result
        assert set(result["sparse_ranking"][1:]) == {"ep1", "ep2", "ep3"}, result

    def test_sparse_ranking_provenance_is_spy(self, tmp_path: Path) -> None:
        """K-C-N10: `sparse_ranking` is bound to a recording spy on
        `relational.bm25_search` -- never re-issued after the fact -- and its
        length is bound to `explain.fusion.arm_sizes.sparse` (the value
        `Aetheryte.recall` itself reports)."""
        result = run(data_root=str(tmp_path / "xl-gate-provenance"))
        assert result["sparse_ranking_provenance"] == "bm25_search_spy", result
        assert len(result["sparse_ranking"]) == result["liveness"]["sparse_arm_size"], result


class TestLiveness:
    """AC-312 / C-XL-2: every pinned axis must be non-binding BEFORE any
    numeric axis is trusted. `sparse_arm_size == corpus_per_layer + 1` is the
    single assertion that catches K-B1's failure mode -- a filler silently
    not matching under FTS5 implicit-AND (this is the node the maker's
    red-check perturbs: dropping one query term from `ep1` flips this RED
    and the verdict to "confounded")."""

    def test_liveness_all_conjuncts_green(self, tmp_path: Path) -> None:
        result = run(data_root=str(tmp_path / "xl-gate-liveness"))
        liveness = result["liveness"]
        assert liveness["edge_count"] == 0, liveness
        assert liveness["node_count"] == 4, liveness
        assert liveness["dense_arm_size"] == 0, liveness
        assert liveness["sparse_arm_size"] == liveness["corpus_per_layer"] + 1, liveness
        assert liveness["corpus_per_layer"] < liveness["candidate_k"], liveness
        assert liveness["k"] > liveness["corpus_per_layer"], liveness
        assert liveness["global_bm25_rank0"] == "sem-target", liveness


class TestSingleLayerControl:
    def test_single_layer_control_is_rank_zero(self, tmp_path: Path) -> None:
        """C-XL-1 (AC-311): the SAME fixture restricted to `layers=["semantic"]`
        puts `sem-target` at fused rank 0 TODAY. If this is red, the fixture's
        BM25 assumption is wrong and the main gate is red for the wrong
        reason -> STOP S-4."""
        from crystalium.schemas import Scope
        from crystalium.trust import Tier

        stores = new_stores(str(tmp_path / "xl-gate-c1"), "single-layer")
        fixture = build_fixture(stores.relational, stores.graph)
        aetheryte = _build_aetheryte_for(stores)

        result = aetheryte.recall(
            Scope(project=_PROJECT, agent_class_visibility=_AGENT_CLASS),
            _XL_QUERY, 5, ["semantic"], Tier.T1,
        )
        retrieved = [r.id for r in result.records]
        assert retrieved.index(fixture["target_id"]) == 0, retrieved


class TestGlobalPremiseProbe:
    def test_global_premise_probe_ranks_target_first(self, tmp_path: Path) -> None:
        """C-XL-3: `relational.bm25_search(query, layer_filter=None, k=60)`
        puts `sem-target` at global rank 0 -- converts the fixture's BM25
        assumption from prose into an in-gate assertion. This is what makes
        the main gate's RED attributable: global rank 0 + fused rank
        `corpus_per_layer` can ONLY be layer-append order. Read-only use of
        the shared method (the `fusion_gate.py:257-262` precedent) -- no
        fence contact."""
        stores = new_stores(str(tmp_path / "xl-gate-c3"), "global-premise")
        fixture = build_fixture(stores.relational, stores.graph)

        hits = stores.relational.bm25_search(_XL_QUERY, layer_filter=None, k=60)
        ids = [h["id"] for h in hits]
        assert ids[0] == fixture["target_id"], ids


class TestFutureLayerMerge:
    """crystalium#45 -- cross-layer rank blocking. FIXED (W-45, Option A).

    Shipped as a `strict=True` xfail (the repo's own precedent,
    `test_fusion_gate.py:85-103`): strict xfail is self-enforcing -- landing
    #45 turns this XPASS, which fails the suite until W-45 also removes the
    marker (AC-341), coupling the fix to the gate's own retirement
    (spec.amend-01.md Sec B.2.6). This commit is that removal, landed in the
    SAME commit as the `retrieve.py` fetch-shape change (AC-341's
    `git show --name-only` predicate)."""

    def test_fused_rank_matches_global_bm25_once_layers_merge(self, tmp_path: Path) -> None:
        """The literal post-#45 assertion (AC-340's oracle)."""
        result = run(data_root=str(tmp_path / "xl-gate-xfail"))
        assert result["target_rank"] == 0, result


def test_relocated_target_control(tmp_path: Path) -> None:
    """AC-343: relocate the semantic target INTO the `episodic` layer (the
    same layer as the fillers it used to be blocked behind) on the FIXED
    build, and confirm the gate still measures fused rank through
    `Aetheryte.recall` -- never record IDENTITY. If the gate secretly keyed
    off `sem-target`'s id/layer rather than its measured BM25 score, this
    would go undetected by every other node in this module (all of which use
    the unmodified fixture). Moving the target's LAYER while keeping its
    TF/length recipe (still the strict corpus-wide BM25 best) must still put
    it at fused rank 0 -- the same global merge, on a target now indistinguishable
    from the OTHER episodic layer members except by its BM25 score."""
    from crystalium.schemas import Scope
    from crystalium.trust import Tier

    stores = new_stores(str(tmp_path / "xl-gate-relocated"), "relocated-target")

    stamps = stamp_sequence()
    fillers = [
        crystal(
            eid, "episodic", _filler_summary(eid, _FILLER_LENGTHS[eid]),
            project=_PROJECT, agent_class=_AGENT_CLASS, created_at=next(stamps),
        )
        for eid in _EPISODIC_IDS
    ]
    # The target's recipe is UNCHANGED (still the strict global BM25 best,
    # spec.amend-01.md Sec B.2.1's measured TF=3/12-token construction) --
    # only its LAYER moves, from `semantic` to `episodic`.
    relocated_target = crystal(
        _TARGET_ID, "episodic", _sem_target_summary(),
        project=_PROJECT, agent_class=_AGENT_CLASS, created_at=next(stamps),
    )
    seed_relational(stores.relational, [*fillers, relocated_target])
    seed_graph(
        stores.graph,
        nodes=[(eid, "episodic") for eid in _EPISODIC_IDS] + [(_TARGET_ID, "episodic")],
    )

    aetheryte = _build_aetheryte_for(stores)
    result = aetheryte.recall(
        Scope(project=_PROJECT, agent_class_visibility=_AGENT_CLASS),
        _XL_QUERY, 5, None, Tier.T1,
    )
    retrieved = [r.id for r in result.records]
    assert retrieved.index(_TARGET_ID) == 0, retrieved
