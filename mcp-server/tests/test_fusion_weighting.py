"""crystalium#38 (FORGE deliberation.md) — weighted RRF fusion, Layer 2/3.

Template: test_recall_starvation.py's harness (real RelationalStore so BM25/
FTS5 is genuine; MagicMock vector + graph stores so arm memberships are
exact — the Terminology block's "fixture-shape hazard" (vigil F2): the graph/
completion mocks' return value must be INDEPENDENT of the `seed_ids` argument
they are called with, or FETCH_WIDTH_FLOOR criteria (AC-138/AC-139) become a
no-op by construction).

Container-first: run via
  docker compose run --rm crystalium pytest mcp-server/tests/test_fusion_weighting.py -v
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import jsonschema  # noqa: F401 — see test_recall_starvation.py C-7 note: import unconditionally
import pytest
from jsonschema import Draft202012Validator

from crystalium.aetheryte.redact import Redactor
from crystalium.aetheryte.retrieve import (
    FETCH_WIDTH_FLOOR,
    Aetheryte,
    derived_family_merge,
    resolve_sparse_weight,
    rrf_merge_scored,
    weighted_rrf_merge,
    weighted_rrf_merge_scored,
)
from crystalium.composer import Composer
from crystalium.config import Config
from crystalium.enforcement import Enforcement
from crystalium.importance import importance_score
from crystalium.schemas import Scope
from crystalium.server import build_tool_manifest
from crystalium.storage.relational import RelationalStore
from crystalium.trust import Tier

_UTC = timezone.utc
_ALL_LAYERS = ["episodic", "semantic", "procedural", "execution"]


# ---------------------------------------------------------------------------
# Shared harness (modelled on test_recall_starvation.py:79-198)
# ---------------------------------------------------------------------------


def _word_tokenizer(text: str) -> int:
    return len(text.split())


def _make_config(tmp_path: Path, **overrides: Any) -> Config:
    """Config.__new__ manual helper — same pattern as test_recall_starvation.py."""
    cfg = Config.__new__(Config)
    cfg.transport = "stdio"
    cfg.idle_threshold_s = 300
    cfg.min_dream_gap_s = 1800
    cfg.dream_tick_s = 60
    cfg.ecl_version = "2.0"
    cfg.skill_invoke_timeout_s = 30
    cfg.skill_invoke_output_cap_bytes = 8192
    cfg.importance_weights = (0.25, 0.30, 0.25, 0.20)
    cfg.importance_recency_halflife_days = 14.0
    cfg.k_corroboration = 3
    cfg.human_confirm_default_window_days = 30
    cfg.slots = {
        "executive": 300, "procedural": 600, "semantic": 800,
        "episodic": 800, "execution": 1000, "buffer": 300,
    }
    cfg.total_cap = 3500
    cfg.data_dir = tmp_path / "data"
    cfg.embed_backend = "sentence-transformers"
    cfg.rate_limit_per_minute = 1_000_000
    cfg.install_ts = None
    cfg.repo_root = None
    cfg.evb_enabled = False
    cfg.recall_relevance_primary = True
    cfg.recall_weighted_fusion = True
    cfg.fusion_weight_dense = 1.0
    cfg.fusion_weight_derived = 1.0
    cfg.fusion_sparse_boost_alpha = 1.0
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    for key, value in overrides.items():
        setattr(cfg, key, value)
    return cfg


def _crystal_dict(
    *,
    id: str | None = None,
    layer: str = "episodic",
    summary: str = "test summary",
    project: str = "fusion-proj",
    importance: float = 0.5,
    status: str = "active",
) -> dict[str, Any]:
    cid = id or str(uuid.uuid4())
    now = datetime.now(_UTC)
    content_ref = hashlib.sha256(cid.encode()).hexdigest()
    return {
        "id": cid,
        "layer": layer,
        "summary": summary,
        "trust_tier": "T1",
        "validation_state": "validated",
        "status": status,
        "content_ref": content_ref,
        "embedding_ref": None,
        "scope": {"project": project, "agent_class_visibility": "all", "sensitivity_tag": "none"},
        "provenance": {
            "source": "verified_agent", "author_agent": "test-agent",
            "task_id": None, "created_at": now.isoformat(),
        },
        "temporal": {"t_valid_from": now.isoformat(), "t_valid_to": None, "superseded_by": None},
        "utility": {
            "access_count": 0, "last_access": now.isoformat(),
            "outcome_success_score": None, "importance": importance,
            "novelty_at_write": 0.5,
        },
    }


def _build_aetheryte(
    tmp_path: Path,
    relational: RelationalStore,
    *,
    dense_hits: list[str] | None = None,
    graph_neighbors: set[str] | None = None,
    completion_walk: dict[str, float] | None = None,
    recall_completion: bool = True,
    recall_active_only: bool = False,
    recall_relevance_primary: bool = True,
    recall_weighted_fusion: bool = True,
    fusion_weight_dense: float = 1.0,
    fusion_weight_derived: float = 1.0,
    fusion_sparse_boost_alpha: float = 1.0,
) -> Aetheryte:
    """Layer-2 harness: real RelationalStore (genuine BM25/FTS5) + MagicMock
    vector/graph stores whose return values are FIXED (independent of the
    `seed_ids` argument — the Terminology block's fixture-shape mandate)."""
    cfg = _make_config(
        tmp_path,
        recall_relevance_primary=recall_relevance_primary,
        recall_weighted_fusion=recall_weighted_fusion,
        fusion_weight_dense=fusion_weight_dense,
        fusion_weight_derived=fusion_weight_derived,
        fusion_sparse_boost_alpha=fusion_sparse_boost_alpha,
    )
    enforcement = Enforcement(cfg)
    redactor = Redactor(cfg)
    composer = Composer(config=cfg, tokenizer=_word_tokenizer,
                         recall_relevance_primary=recall_relevance_primary)

    vector_store = MagicMock()
    vector_store.embed.return_value = [0.1, 0.2, 0.3]
    vector_store.dense_search.return_value = [{"id": cid} for cid in (dense_hits or [])]

    graph_store = MagicMock()
    # C-3: mocks return the REAL types (set[str] / dict[str, float]) so the
    # mandatory RED-first demonstrations (and D5's sort) are exercised for
    # real, not made vacuous by a list that iterates deterministically anyway.
    graph_store.neighbor_expand.return_value = set(graph_neighbors or set())
    graph_store.decaying_walk.return_value = dict(completion_walk or {})

    return Aetheryte(
        relational=relational,
        vector_store=vector_store,
        graph_store=graph_store,
        enforcement=enforcement,
        redactor=redactor,
        importance_fn=importance_score,
        composer=composer,
        completion=recall_completion,
        recall_active_only=recall_active_only,
        recall_relevance_primary=recall_relevance_primary,
        recall_weighted_fusion=recall_weighted_fusion,
        fusion_weight_dense=fusion_weight_dense,
        fusion_weight_derived=fusion_weight_derived,
        fusion_sparse_boost_alpha=fusion_sparse_boost_alpha,
    )


def _recompute_fusion(
    sparse: list[str], dense: list[str], graph: list[str], completion: list[str],
    *, w_sparse: float, w_dense: float, w_derived: float,
) -> dict[str, float]:
    """Independent recomputation via the SAME public pure functions the
    implementation calls — the oracle AC-116/AC-118 ask for ('the pure
    function recomputed on the same arms')."""
    derived = derived_family_merge([graph, completion])
    return dict(
        weighted_rrf_merge_scored(
            [(sparse, w_sparse), (dense, w_dense), (derived, w_derived)], k_rrf=60
        )
    )


# ---------------------------------------------------------------------------
# Sketch fixture (Terminology block, spec.criteria.md)
# ---------------------------------------------------------------------------

_SKETCH_QUERY = "zephyrion quaggle brindlewisp"


def _build_sketch_fixture(
    tmp_path: Path,
    relational: RelationalStore,
    *,
    project: str = "sketch-proj",
    recall_weighted_fusion: bool = True,
    recall_relevance_primary: bool = True,
) -> tuple[Aetheryte, str, dict[str, Any]]:
    """The issue's acceptance sketch, built per the frozen Terminology block:
    a target crystal carrying three distinctive low-frequency tokens, BM25
    rank 1; a dense ranking placing three unrelated competitors (N1..N3) at
    ranks 1-3 and the target at rank 4; graph AND completion arms NON-EMPTY
    at the fetch width actually used, returning those same three
    competitors — via a mock whose return value ignores its `seed_ids` arg
    (vigil F2's fixture-shape hazard).

    Returns (aetheryte, target_id, meta) where meta carries every raw arm +
    the independently-resolvable weight inputs, so callers can recompute an
    expected fusion value via `_recompute_fusion` / `resolve_sparse_weight`
    without re-deriving the fixture's arithmetic by hand.
    """
    target = _crystal_dict(id="target", project=project, summary=f"{_SKETCH_QUERY} runbook notes")
    n1 = _crystal_dict(id="N1", project=project, summary="unrelated filler alpha content")
    n2 = _crystal_dict(id="N2", project=project, summary="unrelated filler beta content")
    n3 = _crystal_dict(id="N3", project=project, summary="unrelated filler gamma content")
    fillers = [
        _crystal_dict(id=f"D{i}", project=project, summary=f"unrelated filler item number {i}")
        for i in range(4, 11)
    ]
    all_crystals = [target, n1, n2, n3] + fillers
    for c in all_crystals:
        relational.insert_crystal(c)

    sparse_ranking = ["target"]  # target's 3 distinctive tokens are the ONLY BM25 hit
    dense_ranking = ["N1", "N2", "N3", "target"] + [f"D{i}" for i in range(4, 11)]
    graph_ranking = ["N1", "N2", "N3"]
    completion_ranking = ["N1", "N2", "N3"]

    aetheryte = _build_aetheryte(
        tmp_path, relational,
        dense_hits=dense_ranking,
        graph_neighbors={"N1", "N2", "N3"},
        completion_walk={"N1": 0.5, "N2": 0.25, "N3": 0.125},  # sorts to N1,N2,N3
        recall_weighted_fusion=recall_weighted_fusion,
        recall_relevance_primary=recall_relevance_primary,
    )
    meta = {
        "sparse": sparse_ranking,
        "dense": dense_ranking,
        "graph": graph_ranking,
        "completion": completion_ranking,
        "n_scoped": len(all_crystals),
        "project": project,
    }
    return aetheryte, "target", meta


def _sketch_weights(meta: dict[str, Any], *, alpha: float = 1.0, k: int = 10) -> tuple[float, float]:
    candidate_k = max(k * 3, 10)
    cap = candidate_k * len(_ALL_LAYERS)
    raw_n_sparse = len(meta["sparse"])
    w_sparse, selectivity = resolve_sparse_weight(
        raw_n_sparse, raw_n_sparse, cap, meta["n_scoped"], alpha
    )
    return w_sparse, selectivity


# ---------------------------------------------------------------------------
# AC-101 / AC-102 / AC-103 — the issue's own acceptance sketch
# ---------------------------------------------------------------------------


class TestIssue38Sketch:
    def test_target_is_fusion_rank_1(
        self, tmp_path: Path, tmp_relational_store: RelationalStore
    ) -> None:
        """AC-101: with the weighted fusion path active, the target wins
        FUSION rank 0 (not mere membership — #36's AC-031 fixture already
        proves membership at fetch_width=10)."""
        aetheryte, target_id, _meta = _build_sketch_fixture(tmp_path, tmp_relational_store)
        result = aetheryte.recall(
            Scope(project="sketch-proj", agent_class_visibility="all"),
            _SKETCH_QUERY, 10, None, Tier.T1,
        )
        assert result.records[0].id == target_id

    @pytest.mark.parametrize("k", [1, 3, 5, 10, 25])
    def test_rank_1_is_k_independent(
        self, tmp_path: Path, k: int
    ) -> None:
        """AC-102: the target wins fusion rank 0 at ANY k — a fresh store
        per k (interleaving invalid, #36 F-V1 note)."""
        relational = RelationalStore(db_path=tmp_path / f"k{k}.sqlite")
        aetheryte, target_id, _meta = _build_sketch_fixture(
            tmp_path / f"k{k}", relational
        )
        result = aetheryte.recall(
            Scope(project="sketch-proj", agent_class_visibility="all"),
            _SKETCH_QUERY, k, None, Tier.T1,
        )
        assert result.records[0].id == target_id

    def test_unweighted_path_ranks_target_below_first(
        self, tmp_path: Path, tmp_relational_store: RelationalStore
    ) -> None:
        """AC-103: the gate's own gate — the SAME fixture with weighting
        disabled must place the target BELOW rank 0. If this cannot go red,
        AC-101 is passing for a reason other than the fix."""
        aetheryte, target_id, _meta = _build_sketch_fixture(
            tmp_path, tmp_relational_store, recall_weighted_fusion=False
        )
        result = aetheryte.recall(
            Scope(project="sketch-proj", agent_class_visibility="all"),
            _SKETCH_QUERY, 10, None, Tier.T1,
        )
        assert result.records[0].id != target_id


# ---------------------------------------------------------------------------
# AC-113 / AC-114 / AC-115 — D4 base-arm seeding
# ---------------------------------------------------------------------------


class TestSeeding:
    def test_sparse_only_record_becomes_a_seed(
        self, tmp_path: Path, tmp_relational_store: RelationalStore
    ) -> None:
        """AC-113: a record present in sparse but outside dense's first
        fetch_width entries becomes a graph-expansion seed (issue item 2)."""
        s = _crystal_dict(id="S", summary="wozzlefinch quantrix ombledoop notes")
        fillers = [_crystal_dict(id=f"D{i}", summary=f"filler dense item {i}") for i in range(1, 13)]
        for c in [s] + fillers:
            tmp_relational_store.insert_crystal(c)

        aetheryte = _build_aetheryte(
            tmp_path, tmp_relational_store,
            dense_hits=[f"D{i}" for i in range(1, 13)],  # S absent from dense entirely
            graph_neighbors=set(), completion_walk={},
        )
        aetheryte.recall(
            Scope(project="fusion-proj", agent_class_visibility="all"),
            "wozzlefinch quantrix ombledoop", 10, None, Tier.T1,
        )
        called_seed_ids = aetheryte.graph_store.neighbor_expand.call_args.kwargs["seed_ids"]
        assert "S" in called_seed_ids

    def test_prelim_reads_base_arms_only(
        self, tmp_path: Path, tmp_relational_store: RelationalStore
    ) -> None:
        """AC-114: invariant I-1 — the preliminary fused order used for
        seeding is computed from sparse+dense ONLY. Verified by recomputing
        the expected seed set independently (via the same public pure
        function) and asserting the graph-expansion call received EXACTLY
        that set — a derived arm feeding its own seeds would diverge from
        this, since derived_ranking is not part of the computation at all."""
        x = _crystal_dict(id="X", summary="fribbleton yarnwick glostrum entry")
        fillers = [_crystal_dict(id=f"Y{i}", summary=f"filler dense item {i}") for i in range(1, 10)]
        for c in [x] + fillers:
            tmp_relational_store.insert_crystal(c)

        dense_hits = [f"Y{i}" for i in range(1, 10)]
        aetheryte = _build_aetheryte(
            tmp_path, tmp_relational_store,
            dense_hits=dense_hits,
            graph_neighbors={"BAIT"}, completion_walk={"BAIT": 1.0},
        )
        aetheryte.recall(
            Scope(project="fusion-proj", agent_class_visibility="all"),
            "fribbleton yarnwick glostrum", 10, None, Tier.T1,
        )
        called_seed_ids = aetheryte.graph_store.neighbor_expand.call_args.kwargs["seed_ids"]

        # Independent recomputation: n_scoped = 10 crystals inserted (all active).
        w_sparse, _ = resolve_sparse_weight(1, 1, max(10 * 3, 10) * len(_ALL_LAYERS), 10, 1.0)
        expected = weighted_rrf_merge([(["X"], w_sparse), (dense_hits, 1.0)])[:FETCH_WIDTH_FLOOR]
        assert called_seed_ids == expected
        assert "BAIT" not in called_seed_ids  # the derived arm never reaches prelim

    @pytest.mark.parametrize("k", [1, 3, 10, 50])
    def test_seed_count_bounded_by_fetch_width(
        self, tmp_path: Path, k: int
    ) -> None:
        """AC-115: D4 changed seed COMPOSITION, never seed COUNT — the seed
        set passed to graph expansion never exceeds fetch_width."""
        relational = RelationalStore(db_path=tmp_path / f"seedcount{k}.sqlite")
        fillers = [_crystal_dict(id=f"F{i}", summary=f"filler dense item {i}") for i in range(1, 61)]
        for c in fillers:
            relational.insert_crystal(c)

        aetheryte = _build_aetheryte(
            tmp_path / f"seedcount{k}", relational,
            dense_hits=[f"F{i}" for i in range(1, 61)],
            graph_neighbors=set(), completion_walk={},
        )
        aetheryte.recall(
            Scope(project="fusion-proj", agent_class_visibility="all"),
            "no matching tokens at all here", k, None, Tier.T1,
        )
        called_seed_ids = aetheryte.graph_store.neighbor_expand.call_args.kwargs["seed_ids"]
        fetch_width = max(k, FETCH_WIDTH_FLOOR)
        assert len(called_seed_ids) <= fetch_width


# ---------------------------------------------------------------------------
# AC-116 / AC-118 — score semantics
# ---------------------------------------------------------------------------


class TestScoreSemantics:
    def test_score_is_the_weighted_fusion_value(
        self, tmp_path: Path, tmp_relational_store: RelationalStore
    ) -> None:
        """AC-116: on the weighted path, a returned CrystalSummary.score
        equals that record's weighted fused value, recomputed via the pure
        function on the same arms (rrf_score_by_id stays the single source
        of truth, #36 seam 1)."""
        aetheryte, target_id, meta = _build_sketch_fixture(tmp_path, tmp_relational_store)
        result = aetheryte.recall(
            Scope(project="sketch-proj", agent_class_visibility="all"),
            _SKETCH_QUERY, 10, None, Tier.T1,
        )
        w_sparse, _ = _sketch_weights(meta)
        expected = _recompute_fusion(
            meta["sparse"], meta["dense"], meta["graph"], meta["completion"],
            w_sparse=w_sparse, w_dense=1.0, w_derived=1.0,
        )
        by_id = {r.id: r.score for r in result.records}
        assert by_id[target_id] == pytest.approx(expected[target_id], abs=1e-12)


class TestOrdering:
    def test_weighted_scores_emit_descending(
        self, tmp_path: Path, tmp_relational_store: RelationalStore
    ) -> None:
        """AC-118: re-asserts #36 AC-007 under weighted scores — records
        emit in non-increasing `score` order, with an eviction-forcing
        fixture (#36 F-V2: a fixture that never evicts cannot exercise the
        final composer sort)."""
        many = [
            _crystal_dict(id=f"E{i}", summary=f"acme login session token rotation entry {i}")
            for i in range(6)
        ]
        for c in many:
            tmp_relational_store.insert_crystal(c)

        aetheryte = _build_aetheryte(
            tmp_path, tmp_relational_store,
            dense_hits=[c["id"] for c in many],
            graph_neighbors=set(), completion_walk={},
        )
        # Force at least one eviction: a tiny episodic slot cap.
        aetheryte.composer.config.slots["episodic"] = 20
        result = aetheryte.recall(
            Scope(project="fusion-proj", agent_class_visibility="all"),
            "acme login session token rotation", 10, None, Tier.T1,
        )
        assert len(result.records) >= 2
        assert result.evicted_count > 0
        scores = [r.score for r in result.records]
        assert scores == sorted(scores, reverse=True)


# ---------------------------------------------------------------------------
# AC-117 — explain.fusion
# ---------------------------------------------------------------------------


class TestExplain:
    def test_explain_carries_fusion_object(
        self, tmp_path: Path, tmp_relational_store: RelationalStore
    ) -> None:
        """AC-117: explain=true on the weighted path carries a `fusion`
        object naming the three arm weights, the selectivity inputs, AND the
        status population the denominator was drawn from (AC-134/AC-142's
        diagnostic surface, vigil G-2)."""
        aetheryte, target_id, meta = _build_sketch_fixture(tmp_path, tmp_relational_store)
        result = aetheryte.recall(
            Scope(project="sketch-proj", agent_class_visibility="all"),
            _SKETCH_QUERY, 10, None, Tier.T1, explain=True,
        )
        fusion = result.explain["fusion"]
        for key in (
            "weighted", "w_sparse", "w_dense", "w_derived", "selectivity",
            "n_sparse", "n_sparse_cap", "n_scoped", "n_scoped_layers",
            "n_scoped_status", "fetch_width", "candidate_k", "arm_sizes",
        ):
            assert key in fusion, key
        assert fusion["weighted"] is True
        assert fusion["n_scoped"] == meta["n_scoped"]
        assert fusion["n_scoped_layers"] == _ALL_LAYERS
        assert fusion["n_scoped_status"] == "all_statuses"  # recall_active_only=False here
        w_sparse, selectivity = _sketch_weights(meta)
        assert fusion["w_sparse"] == pytest.approx(w_sparse, abs=1e-12)
        assert fusion["selectivity"] == pytest.approx(selectivity, abs=1e-12)
        # explain and score must never disagree (#36 DP-3c).
        by_id = {r.id: r.score for r in result.records}
        assert by_id[target_id] == pytest.approx(
            _recompute_fusion(
                meta["sparse"], meta["dense"], meta["graph"], meta["completion"],
                w_sparse=w_sparse, w_dense=1.0, w_derived=1.0,
            )[target_id],
            abs=1e-12,
        )

    def test_layer_saturating_query_real_stack(
        self, tmp_path: Path, tmp_relational_store: RelationalStore
    ) -> None:
        """AC-134 real-stack companion: layers=['procedural'] where the BM25
        conjunction matches every crystal in that layer -> w_sparse == 1.0
        exactly on the live explain surface."""
        matches = [
            _crystal_dict(id=f"P{i}", layer="procedural", summary="satisfy this exact query text")
            for i in range(5)
        ]
        for c in matches:
            tmp_relational_store.insert_crystal(c)

        aetheryte = _build_aetheryte(
            tmp_path, tmp_relational_store,
            dense_hits=[c["id"] for c in matches],
            graph_neighbors=set(), completion_walk={},
        )
        result = aetheryte.recall(
            Scope(project="fusion-proj", agent_class_visibility="all"),
            "satisfy this exact query text", 10, ["procedural"], Tier.T1, explain=True,
        )
        assert result.explain["fusion"]["w_sparse"] == 1.0


# ---------------------------------------------------------------------------
# AC-119 / AC-120 — flag-off contracts
# ---------------------------------------------------------------------------


class TestFlagOff:
    def test_weighted_off_reproduces_v190_fusion(
        self, tmp_path: Path, tmp_relational_store: RelationalStore
    ) -> None:
        """AC-119: with recall_weighted_fusion=False, the fused id order
        matches ef42967's unweighted rrf_merge_scored — id-ascending arms
        with pairwise-distinct completion scores make the captured order
        well-defined under BOTH ef42967's unsorted set iteration and D5's
        sorted() (which stays OUTSIDE the flag)."""
        ids = ["a1", "a2", "a3", "a4"]
        crystals = [_crystal_dict(id=cid, summary="acme login session token rotation") for cid in ids]
        for c in crystals:
            tmp_relational_store.insert_crystal(c)

        aetheryte = _build_aetheryte(
            tmp_path, tmp_relational_store,
            dense_hits=ids,
            graph_neighbors={"a2", "a3"},
            completion_walk={"a3": 0.9, "a4": 0.1},  # pairwise-distinct
            recall_weighted_fusion=False,
        )
        result = aetheryte.recall(
            Scope(project="fusion-proj", agent_class_visibility="all"),
            "acme login session token rotation", 10, None, Tier.T1,
        )
        observed = [r.id for r in result.records]

        expected = [
            cid for cid, _ in rrf_merge_scored(
                [ids, ids, sorted({"a2", "a3"}), [c for c, _ in sorted({"a3": 0.9, "a4": 0.1}.items(), key=lambda kv: (-kv[1], kv[0]))]],
                k_rrf=60,
            )
        ]
        assert observed == expected

    def test_relevance_primary_off_subsumes_weighting(
        self, tmp_path: Path, tmp_relational_store: RelationalStore
    ) -> None:
        """AC-120: recall_relevance_primary=False with recall_weighted_fusion
        left True still produces the UNWEIGHTED fused order — D7's
        subsumption. Also (vigil A-10): fetch_width narrows to bare k on
        this path, so the fixture must hold both differences fixed."""
        aetheryte, target_id, meta = _build_sketch_fixture(
            tmp_path, tmp_relational_store, recall_relevance_primary=False,
        )
        result = aetheryte.recall(
            Scope(project="sketch-proj", agent_class_visibility="all"),
            _SKETCH_QUERY, 10, None, Tier.T1,
        )
        expected = [
            cid for cid, _ in rrf_merge_scored(
                [meta["sparse"], meta["dense"], meta["graph"], meta["completion"]],
                k_rrf=60,
            )
        ]
        # recall_relevance_primary=False also skips seam 3 (#36 AC-009): `k`
        # is not a cap on this path, so the full unweighted fused order is
        # expected, not a top-10 slice.
        assert [r.id for r in result.records] == expected
        # fetch_width == k (bare), not max(k, FETCH_WIDTH_FLOOR) — assert the
        # seed call received no more than k ids.
        called_seed_ids = aetheryte.graph_store.neighbor_expand.call_args.kwargs["seed_ids"]
        assert len(called_seed_ids) <= 10


# ---------------------------------------------------------------------------
# AC-131 — hash-seed determinism
# ---------------------------------------------------------------------------

_DETERMINISM_SCRIPT = """
import json, sys
sys.path.insert(0, "/app/mcp-server/tests")
from test_fusion_weighting import _build_aetheryte, _crystal_dict
from crystalium.schemas import Scope
from crystalium.storage.relational import RelationalStore
from crystalium.trust import Tier
import tempfile, pathlib

tmp = pathlib.Path(tempfile.mkdtemp())
relational = RelationalStore(db_path=tmp / "d.sqlite")
ids = ["11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222",
       "33333333-3333-4333-8333-333333333333", "44444444-4444-4444-8444-444444444444"]
crystals = [_crystal_dict(id=cid, summary="acme login session token rotation") for cid in ids]
for c in crystals:
    relational.insert_crystal(c)

aetheryte = _build_aetheryte(
    tmp, relational,
    dense_hits=ids,
    graph_neighbors=set(ids),
    completion_walk={ids[0]: 0.5, ids[1]: 0.5, ids[2]: 0.25, ids[3]: 0.1},
)
result = aetheryte.recall(
    Scope(project="fusion-proj", agent_class_visibility="all"),
    "acme login session token rotation", 10, None, Tier.T1,
)
print(json.dumps([r.id for r in result.records]))
"""


class TestDeterminism:
    def test_fused_order_is_hash_seed_independent(self, tmp_path: Path) -> None:
        """AC-131: the same recall, executed in separate processes started
        with different PYTHONHASHSEED values, over a derived-arm fixture
        holding >= 4 distinct UUID-shaped ids, produces BYTE-IDENTICAL fused
        id orders — D5's two sorted() calls neutralise P3 at the consumer.
        The mocks return set[str]/dict[str,float] (C-3), mirroring
        GraphStore's real return types, so this is not vacuous.

        `verification.md` note for the checker (per spec.md's mandate): the
        literal fixture ids used are the four UUID-shaped strings above;
        `red-evidence.txt` (this change's ESL dir) records the observed
        disagreeing seed pair at ef42967 for these exact ids."""
        outputs = {}
        for seed in ("0", "1", "2", "3", "4"):
            import os
            env = dict(os.environ)
            env["PYTHONHASHSEED"] = seed
            env["PYTHONPATH"] = "/app/mcp-server/src:/app"
            proc = subprocess.run(
                [sys.executable, "-c", _DETERMINISM_SCRIPT],
                env=env, capture_output=True, text=True, timeout=60,
            )
            assert proc.returncode == 0, proc.stderr
            outputs[seed] = json.loads(proc.stdout.strip().splitlines()[-1])

        first = outputs["0"]
        for seed, order in outputs.items():
            assert order == first, f"seed {seed} diverged: {order} != {first}"


# ---------------------------------------------------------------------------
# AC-135 — path-level identity with completion off
# ---------------------------------------------------------------------------


class TestIdentity:
    def test_path_level_identity_with_completion_off(
        self, tmp_path: Path, tmp_relational_store: RelationalStore
    ) -> None:
        """AC-135: WHERE recall_completion=False, fusion_sparse_boost_alpha=0.0
        and every arm weight left at 1.0, Aetheryte.recall() reproduces the
        fused id order the ef42967-equivalent (flag-off) path produces for
        the SAME arms — §D2's identity property at the PATH level (AC-108
        pins only the pure function).

        Reseeding precondition (vigil B-3), ASSERTED not assumed: neutralising
        alpha/weights does not neutralise D4 — `prelim` is still not
        byte-equal to `dense_ranking` (order differs). The identity holds
        here only because the sketch fixture's prelim[:fetch_width] is
        SET-EQUAL to dense_ranking[:fetch_width] (membership does not
        differ, so a real neighbor_expand would return the same
        neighbours either way)."""
        _throwaway, _tid, meta = _build_sketch_fixture(tmp_path, tmp_relational_store)

        # --- Precondition, asserted ---
        w_sparse_neutral, _ = resolve_sparse_weight(
            len(meta["sparse"]), len(meta["sparse"]),
            max(10 * 3, 10) * len(_ALL_LAYERS), meta["n_scoped"], 0.0,  # alpha=0.0
        )
        assert w_sparse_neutral == 1.0
        prelim = weighted_rrf_merge([(meta["sparse"], 1.0), (meta["dense"], 1.0)])
        assert set(prelim[:FETCH_WIDTH_FLOOR]) == set(meta["dense"][:FETCH_WIDTH_FLOOR])
        assert prelim[:FETCH_WIDTH_FLOOR] != meta["dense"][:FETCH_WIDTH_FLOOR], (
            "D4 reseeding must NOT be neutralised by alpha=0/weights=1.0 — "
            "if this fails the fixture no longer exercises the precondition"
        )

        # --- Path-level comparison ---
        scope = Scope(project=meta["project"], agent_class_visibility="all")
        weighted_neutral = _build_aetheryte(
            tmp_path, tmp_relational_store,
            dense_hits=meta["dense"], graph_neighbors=set(meta["graph"]),
            completion_walk={}, recall_completion=False,
            fusion_sparse_boost_alpha=0.0, recall_weighted_fusion=True,
        )
        unweighted = _build_aetheryte(
            tmp_path, tmp_relational_store,
            dense_hits=meta["dense"], graph_neighbors=set(meta["graph"]),
            completion_walk={}, recall_completion=False,
            recall_weighted_fusion=False,
        )
        r1 = weighted_neutral.recall(scope, _SKETCH_QUERY, 10, None, Tier.T1)
        r2 = unweighted.recall(scope, _SKETCH_QUERY, 10, None, Tier.T1)
        assert [r.id for r in r1.records] == [r.id for r in r2.records]


# ---------------------------------------------------------------------------
# AC-140 / AC-141 — guard-vs-cure, Layer 3 (real GraphStore; C-4: the vector
# arm is a deterministic stub, since a real embedder cannot be reliably
# steered to a specific dense rank — measurement.md §7).
# ---------------------------------------------------------------------------

_GUARD_QUERY = "plarnix threxil vandomere"


def _build_aetheryte_real_graph(
    tmp_path: Path,
    relational: RelationalStore,
    graph_store: Any,
    *,
    dense_hits: list[str],
    recall_completion: bool = False,
    recall_weighted_fusion: bool = True,
    recall_relevance_primary: bool = True,
) -> Aetheryte:
    cfg = _make_config(
        tmp_path,
        recall_relevance_primary=recall_relevance_primary,
        recall_weighted_fusion=recall_weighted_fusion,
    )
    enforcement = Enforcement(cfg)
    redactor = Redactor(cfg)
    composer = Composer(config=cfg, tokenizer=_word_tokenizer,
                         recall_relevance_primary=recall_relevance_primary)
    vector_store = MagicMock()
    vector_store.embed.return_value = [0.1, 0.2, 0.3]
    vector_store.dense_search.return_value = [{"id": cid} for cid in dense_hits]
    return Aetheryte(
        relational=relational,
        vector_store=vector_store,
        graph_store=graph_store,
        enforcement=enforcement,
        redactor=redactor,
        importance_fn=importance_score,
        composer=composer,
        completion=recall_completion,
        recall_active_only=False,
        recall_relevance_primary=recall_relevance_primary,
        recall_weighted_fusion=recall_weighted_fusion,
    )


def _build_guard_fixture(
    relational: RelationalStore, graph_store: Any
) -> tuple[list[str], str]:
    """Target: 3 distinctive BM25 tokens (sole sparse hit), dense rank 4.
    N1/N2/N3: dense ranks 1-3, no lexical match. ONE real graph edge,
    N1 -> N2 — this is what makes AC-141 (the falsifiability precondition)
    observable: at FETCH_WIDTH_FLOOR=1, the reverted build seeds ONLY the
    top dense competitor (N1), and N1's real out-edge promotes N2 to a
    second arm, which is enough to outrank the target's own two (sparse +
    dense) arms. On the fixed build the target itself leads the base-arm
    `prelim`, so it is what gets seeded, and it has no out-edges."""
    target = _crystal_dict(id="target", summary=f"{_GUARD_QUERY} runbook notes")
    n1 = _crystal_dict(id="N1", summary="dense competitor one filler content")
    n2 = _crystal_dict(id="N2", summary="dense competitor two filler content")
    n3 = _crystal_dict(id="N3", summary="dense competitor three filler content")
    for c in (target, n1, n2, n3):
        relational.insert_crystal(c)
    graph_store.add_node(crystal_id="N1", layer="episodic")
    graph_store.add_node(crystal_id="N2", layer="episodic")
    graph_store.add_edge("N1", "N2", "LINKS_TO")
    dense_hits = ["N1", "N2", "N3", "target"]  # target at dense rank 4
    return dense_hits, "target"


class TestGuardVsCure:
    @pytest.mark.parametrize("k", [1, 3, 5])
    def test_target_survives_floor_removal_at_small_k(
        self, tmp_path: Path, k: int, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """AC-140 — the change's thesis test and the issue's literal
        acceptance bar ("without relying on the fetch-width floor").
        FETCH_WIDTH_FLOOR monkeypatched to 1: on the FIXED build the target
        holds fused rank 0 at k in {1, 3, 5}. Predicted mechanism (D4
        reseeding, not the weighting) is pre-registered so it can be wrong
        — a RED result here is a finding about the change's nature
        (deliberation.md DP-4(ii)/C-12: routes to FORGE, does NOT flip the
        shipped default or block the tag on its own), not a defect to
        silently patch away."""
        monkeypatch.setattr("crystalium.aetheryte.retrieve.FETCH_WIDTH_FLOOR", 1)
        from crystalium.storage.graph import GraphStore

        relational = RelationalStore(db_path=tmp_path / f"guard140-{k}.sqlite")
        graph_store = GraphStore(kuzu_dir=tmp_path / f"guard140-{k}-graph.kuzu")
        dense_hits, target_id = _build_guard_fixture(relational, graph_store)
        aetheryte = _build_aetheryte_real_graph(
            tmp_path, relational, graph_store, dense_hits=dense_hits,
        )
        result = aetheryte.recall(
            Scope(project="fusion-proj", agent_class_visibility="all"),
            _GUARD_QUERY, k, None, Tier.T1,
        )
        assert result.records, "empty result set"
        observed = result.records[0].id
        assert observed == target_id, (
            f"AC-140 RED at k={k}: fused rank 0 is {observed!r}, not the "
            f"target — record the per-k table in red-evidence.txt for the checker"
        )

    def test_reverted_build_needs_the_floor_at_k1(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """AC-141 — the falsifiability precondition for AC-140, standing to
        it exactly as AC-139 stands to AC-138. With the fusion fix REVERTED
        (recall_weighted_fusion=False) and FETCH_WIDTH_FLOOR patched to 1,
        the reverted build must NOT return the target at rank 0 — #36's
        F-V1 k=1 cell reproduced. Proves the floor's channel is live at
        small k in this fixture, so AC-140's green carries information."""
        monkeypatch.setattr("crystalium.aetheryte.retrieve.FETCH_WIDTH_FLOOR", 1)
        from crystalium.storage.graph import GraphStore

        relational = RelationalStore(db_path=tmp_path / "guard141.sqlite")
        graph_store = GraphStore(kuzu_dir=tmp_path / "guard141-graph.kuzu")
        dense_hits, target_id = _build_guard_fixture(relational, graph_store)
        aetheryte = _build_aetheryte_real_graph(
            tmp_path, relational, graph_store, dense_hits=dense_hits,
            recall_weighted_fusion=False,
        )
        result = aetheryte.recall(
            Scope(project="fusion-proj", agent_class_visibility="all"),
            _GUARD_QUERY, 1, None, Tier.T1,
        )
        assert result.records
        assert result.records[0].id != target_id, (
            "AC-141 cannot go green: the fixture is not floor-sensitive at "
            "k=1 — AC-140 must be MOVED, not weakened (vigil G-1 convention)"
        )


# ---------------------------------------------------------------------------
# AC-142 — the selectivity denominator's status population (real store;
# companion to test_rrf.py's pure-function TestSparseWeight, which cannot
# construct a mixed active/deprecated population on its own).
# ---------------------------------------------------------------------------


class TestSparseWeight:
    def test_mixed_status_population_agrees(
        self, tmp_path: Path, tmp_relational_store: RelationalStore
    ) -> None:
        """AC-142: a searched layer holding active AND deprecated crystals,
        matched by the query REGARDLESS of status -> the population-
        agreement invariant holds (n_sparse <= n_scoped, DP-9(b): both ends
        active-only when recall_active_only=True) AND w_sparse == 1.0. MUST
        be demonstrated RED against a mixed implementation (all-statuses
        numerator against an active-only denominator) — see
        red-evidence.txt."""
        actives = [
            _crystal_dict(id=f"act-{i}", layer="semantic",
                          summary="glimmerfax rotunda protocol document", status="active")
            for i in range(5)
        ]
        deprecated = [
            _crystal_dict(id=f"dep-{i}", layer="semantic",
                          summary="glimmerfax rotunda protocol document", status="deprecated")
            for i in range(3)
        ]
        for c in actives + deprecated:
            tmp_relational_store.insert_crystal(c)

        aetheryte = _build_aetheryte(
            tmp_path, tmp_relational_store,
            dense_hits=[c["id"] for c in actives + deprecated],
            graph_neighbors=set(), completion_walk={},
            recall_active_only=True,
        )
        result = aetheryte.recall(
            Scope(project="fusion-proj", agent_class_visibility="all"),
            "glimmerfax rotunda protocol", 10, ["semantic"], Tier.T1, explain=True,
        )
        fusion = result.explain["fusion"]
        assert fusion["n_sparse"] <= fusion["n_scoped"], (
            "population-agreement invariant VIOLATED — numerator/denominator "
            "drawn from different status populations"
        )
        assert fusion["w_sparse"] == 1.0
        assert fusion["n_scoped_status"] == "active_only"
        assert fusion["arm_sizes"]["sparse"] == 8   # RAW length, C-7
        assert fusion["n_sparse"] == 5               # population-resolved numerator
        assert fusion["n_scoped"] == 5


# ---------------------------------------------------------------------------
# AC-128 — manifest describes the weighted score semantics
# ---------------------------------------------------------------------------


class TestDX:
    def test_manifest_describes_weighted_score(self) -> None:
        manifest = build_tool_manifest()
        recall_tool = next(t for t in manifest if t["name"] == "crystalium.recall")
        desc = recall_tool["description"]
        assert "raw hybrid-retrieval RRF value" not in desc, (
            "the v1.9.0 unqualified phrase must not survive unqualified into v1.10.0"
        )
        assert "weighted hybrid-retrieval" in desc.lower()


# ---------------------------------------------------------------------------
# AC-129 — schema round-trip with the fusion object populated
# ---------------------------------------------------------------------------


def _find_schemas_dir() -> Path:
    here = Path(__file__).parent
    for candidate in (
        here.parent.parent / "schemas",
        here.parent.parent.parent / "schemas",
        Path("/app/schemas"),
        Path("/schemas"),
    ):
        if candidate.exists() and candidate.is_dir():
            return candidate
    raise AssertionError("schemas/ directory not found")


def _load_recall_result_schema() -> dict:
    path = _find_schemas_dir() / "recall-result.v1.json"
    with path.open() as fh:
        return json.load(fh)


class TestSchemaRoundTrip:
    def test_explain_with_fusion_validates(
        self, tmp_path: Path, tmp_relational_store: RelationalStore
    ) -> None:
        """AC-129: a live RecallResult with explain=true on the weighted
        path validates against schemas/recall-result.v1.json — importing
        jsonschema unconditionally (module top). The top level declares
        `additionalProperties: false`, but `explain` is deliberately loose,
        so this must pass with NO schema edit (S-6 / G-5)."""
        aetheryte, _target_id, _meta = _build_sketch_fixture(tmp_path, tmp_relational_store)
        result = aetheryte.recall(
            Scope(project="sketch-proj", agent_class_visibility="all"),
            _SKETCH_QUERY, 10, None, Tier.T1, explain=True,
        )
        schema = _load_recall_result_schema()
        instance = result.model_dump(exclude_none=True, mode="json")
        Draft202012Validator(schema).validate(instance)
