"""Issue #36 regression suite — query relevance never reaches the composer.

crystalium#36: `crystalium.recall` returned records ordered and selected
entirely without reference to the query. This suite pins the fix bound at
spec.md revision 2.0.0 (FORGE deliberation.md): relevance is now the primary
composition signal, `k` is a real cap, `score`/`budget` are populated, cold-
start importance is no longer a hardcoded 0.0, and the published schema
matches the emitted result.

C-5 (RED-first): AC-001, AC-002, AC-004, AC-005, AC-010 are asserted to fail
at af24493 BEFORE the fix lands; see red-evidence.txt in the ESL change dir.

Harness note (critique F1): AC-001 and AC-004 wire the mock vector store with
a NON-EMPTY `embed.return_value` and a `dense_search` return that surfaces the
non-query-matching competitor crystals — dense is the only path into
`all_candidates` for them (retrieve.py's `if query_vec:` gate kills the dense
arm entirely on an empty embed return). The target/relevant crystal is given
multi-arm presence (BM25 + dense) so its RRF score strictly exceeds every
single-arm-only competitor's — two single-arm rank-1 entries would otherwise
tie at exactly 1/61 and the seam-4 eviction key would fall through to
importance, evicting the fresh/relevant crystal even post-fix.

Container-first: run via
  docker compose run --rm crystalium pytest mcp-server/tests/test_recall_starvation.py -v
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

# C-7 (AC-028/AC-029 hazard): jsonschema>=4.21 is a declared dev dependency
# (mcp-server/pyproject.toml). Import it UNCONDITIONALLY here — never behind a
# try/except ImportError -> pytest.skip (test_schemas.py:63-77's pattern) —
# or the schema round-trip test could never fail on the defect it names.
import jsonschema  # noqa: F401
from jsonschema import Draft202012Validator

from crystalium.aetheryte.redact import Redactor
from crystalium.aetheryte.retrieve import Aetheryte
from crystalium.composer import Composer
from crystalium.config import Config
from crystalium.enforcement import Enforcement, TierViolation
from crystalium.evb import evb_score, make_evb_scorer
from crystalium.importance import COLD_START_IMPORTANCE_CEILING, importance_score
from crystalium.layers.episodic import EpisodicLayer
from crystalium.layers.execution import ExecutionLayer
from crystalium.layers.procedural import ProceduralLayer
from crystalium.layers.semantic import SemanticLayer
from crystalium.schemas import Scope
from crystalium.server import (
    _VALID_PROVENANCE_SOURCES,
    _handle_commit,
    _handle_recall,
    build_tool_manifest,
    normalize_k,
)
from crystalium.storage.relational import RelationalStore
from crystalium.trust import Tier

_UTC = timezone.utc


# ---------------------------------------------------------------------------
# Shared harness (modelled on test_aetheryte.py:41-151)
# ---------------------------------------------------------------------------


def _word_tokenizer(text: str) -> int:
    """Deterministic 1-token-per-word tokenizer (test_aetheryte.py:68-69)."""
    return len(text.split())


def _make_config(tmp_path: Path, **overrides: Any) -> Config:
    """Config.__new__ manual helper (C-8: recall_relevance_primary assigned here)."""
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
    cfg.evb_enabled = True
    # crystalium#36 / C-8: assigned here (Composer's own default kwarg already
    # covers Composer-only construction, but this keeps Config itself complete
    # for any code path that reads it directly).
    cfg.recall_relevance_primary = True
    # crystalium#38 / S-5: the four new fusion config fields, same rationale.
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
    project: str = "proj-a",
    importance: float = 0.5,
    last_access: datetime | None = None,
) -> dict[str, Any]:
    cid = id or str(uuid.uuid4())
    now = last_access or datetime.now(_UTC)
    content_ref = hashlib.sha256(cid.encode()).hexdigest()
    return {
        "id": cid,
        "layer": layer,
        "summary": summary,
        "trust_tier": "T1",
        "validation_state": "validated",
        "status": "active",
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
    recall_relevance_primary: bool = True,
    episodic_cap: int = 800,
) -> Aetheryte:
    """BM25-only harness (dense/graph inactive) — matches test_aetheryte.py's
    default fast harness. Sufficient whenever the fixture's candidates are all
    reachable via BM25 alone (i.e. every test EXCEPT the AC-001/AC-004 eviction-
    vs-importance tests, which need F1's dense-arm fix below)."""
    cfg = _make_config(tmp_path, recall_relevance_primary=recall_relevance_primary)
    cfg.slots["episodic"] = episodic_cap

    enforcement = Enforcement(cfg)
    redactor = Redactor(cfg)
    composer = Composer(config=cfg, tokenizer=_word_tokenizer,
                         recall_relevance_primary=recall_relevance_primary)

    vector_store = MagicMock()
    vector_store.embed.return_value = []
    vector_store.dense_search.return_value = []
    graph_store = MagicMock()
    graph_store.neighbor_expand.return_value = set()

    return Aetheryte(
        relational=relational,
        vector_store=vector_store,
        graph_store=graph_store,
        enforcement=enforcement,
        redactor=redactor,
        importance_fn=importance_score,
        composer=composer,
        recall_relevance_primary=recall_relevance_primary,
    )


def _build_aetheryte_with_dense(
    tmp_path: Path,
    relational: RelationalStore,
    *,
    dense_hit_ids: list[str],
    episodic_cap: int,
    recall_relevance_primary: bool = True,
) -> Aetheryte:
    """F1-fixed harness for AC-001/AC-004: the dense arm is ALIVE (non-empty
    `embed.return_value`) and `dense_search` surfaces *dense_hit_ids* in order
    — the only path into `all_candidates` for a crystal that does not match
    the query via BM25 (retrieve.py:278's `if query_vec:` gate)."""
    cfg = _make_config(tmp_path, recall_relevance_primary=recall_relevance_primary)
    cfg.slots["episodic"] = episodic_cap

    enforcement = Enforcement(cfg)
    redactor = Redactor(cfg)
    composer = Composer(config=cfg, tokenizer=_word_tokenizer,
                         recall_relevance_primary=recall_relevance_primary)

    vector_store = MagicMock()
    vector_store.embed.return_value = [0.1, 0.2, 0.3]  # non-empty -> dense arm alive
    vector_store.dense_search.return_value = [{"id": cid} for cid in dense_hit_ids]
    graph_store = MagicMock()
    graph_store.neighbor_expand.return_value = set()

    return Aetheryte(
        relational=relational,
        vector_store=vector_store,
        graph_store=graph_store,
        enforcement=enforcement,
        redactor=redactor,
        importance_fn=importance_score,
        composer=composer,
        recall_relevance_primary=recall_relevance_primary,
    )


def _insert_matching_crystals(
    relational: RelationalStore, n: int, query: str, *, project: str = "proj-a"
) -> list[str]:
    """Insert *n* crystals whose summary is exactly *query* (all match BM25),
    short enough that a large slot cap never evicts any of them."""
    ids = []
    for i in range(n):
        cid = f"match-{i}"
        relational.insert_crystal(_crystal_dict(id=cid, summary=query, project=project))
        ids.append(cid)
    return ids


# ---------------------------------------------------------------------------
# AC-001 — the headline regression: a fresh, relevant, zero-importance crystal
# survives eviction against high-importance topically-unrelated competitors.
# ---------------------------------------------------------------------------


class TestIssue36Regression:
    def test_fresh_distinctive_crystal_is_returned(
        self, tmp_path: Path, tmp_relational_store: RelationalStore
    ) -> None:
        query = "zephyrion quaggle brindlewisp"  # 3 distinctive low-frequency tokens

        target = _crystal_dict(
            id="target", summary=f"{query} runbook notes here",
            importance=0.0,
        )
        competitors = [
            _crystal_dict(
                id=f"comp-{i}", summary=f"unrelated topic filler words number {i}",
                importance=0.9,
            )
            for i in range(4)
        ]
        tmp_relational_store.insert_crystal(target)
        for c in competitors:
            tmp_relational_store.insert_crystal(c)

        # F1: dense arm surfaces ALL five (competitors have no BM25 route in);
        # target is ALSO dense-present (multi-arm) so it strictly outranks any
        # single-arm-only competitor (avoids the RRF rank-1 tie trap).
        dense_ids = ["target"] + [c["id"] for c in competitors]
        aetheryte = _build_aetheryte_with_dense(
            tmp_path, tmp_relational_store, dense_hit_ids=dense_ids, episodic_cap=10,
        )

        result = aetheryte.recall(
            scope=Scope(project="proj-a"), query=query, k=10,
            layers=["episodic"], caller_tier=Tier.T1,
        )

        ids = {r.id for r in result.records}
        assert "target" in ids, (
            f"fresh distinctive crystal must survive composition; got {ids}"
        )


# ---------------------------------------------------------------------------
# AC-002 / AC-003 — k is a real cap
# ---------------------------------------------------------------------------


class TestKIsACap:
    def test_k5_returns_five_when_five_fit(
        self, tmp_path: Path, tmp_relational_store: RelationalStore
    ) -> None:
        query = "kilroy pentacle octarine"
        _insert_matching_crystals(tmp_relational_store, 8, query)
        aetheryte = _build_aetheryte(tmp_path, tmp_relational_store)

        result = aetheryte.recall(
            scope=Scope(project="proj-a"), query=query, k=5,
            layers=["episodic"], caller_tier=Tier.T1,
        )
        assert len(result.records) == 5

    @pytest.mark.parametrize("k", [1, 3, 5, 10, 15])
    def test_never_exceeds_k(
        self, tmp_path: Path, tmp_relational_store: RelationalStore, k: int
    ) -> None:
        query = "marzipan velveteen quorum"
        _insert_matching_crystals(tmp_relational_store, 20, query)
        aetheryte = _build_aetheryte(tmp_path, tmp_relational_store)

        result = aetheryte.recall(
            scope=Scope(project="proj-a"), query=query, k=k,
            layers=["episodic"], caller_tier=Tier.T1,
        )
        assert len(result.records) <= k


# ---------------------------------------------------------------------------
# AC-004 — relevance beats importance (provable only under DP-1 O1)
# ---------------------------------------------------------------------------


class TestRelevanceBeatsImportance:
    def test_relevant_zero_importance_survives_eviction(
        self, tmp_path: Path, tmp_relational_store: RelationalStore
    ) -> None:
        query = "umbracite fenwright torvane"

        relevant = _crystal_dict(id="relevant", summary=f"{query} incident notes", importance=0.0)
        irrelevant = _crystal_dict(id="irrelevant", summary="totally different filler content", importance=0.9)
        tmp_relational_store.insert_crystal(relevant)
        tmp_relational_store.insert_crystal(irrelevant)

        # F1: worst-case dense ordering for `relevant` (irrelevant ranks dense-1,
        # relevant dense-2) — relevant STILL wins because it also has the BM25
        # arm (irrelevant has none): 1/61+1/62 > 1/61.
        aetheryte = _build_aetheryte_with_dense(
            tmp_path, tmp_relational_store,
            dense_hit_ids=["irrelevant", "relevant"], episodic_cap=8,
        )

        result = aetheryte.recall(
            scope=Scope(project="proj-a"), query=query, k=10,
            layers=["episodic"], caller_tier=Tier.T1,
        )
        ids = {r.id for r in result.records}
        assert "relevant" in ids, f"query-relevant crystal must survive; got {ids}"
        assert "irrelevant" not in ids, f"irrelevant high-importance crystal should be evicted; got {ids}"


# ---------------------------------------------------------------------------
# AC-005 / AC-006 — score is populated in BOTH flag modes
# ---------------------------------------------------------------------------


class TestScorePopulated:
    def test_score_is_not_none(
        self, tmp_path: Path, tmp_relational_store: RelationalStore
    ) -> None:
        query = "obsidian latchkey ferrous"
        _insert_matching_crystals(tmp_relational_store, 1, query)
        aetheryte = _build_aetheryte(tmp_path, tmp_relational_store)

        result = aetheryte.recall(
            scope=Scope(project="proj-a"), query=query, k=5,
            layers=["episodic"], caller_tier=Tier.T1,
        )
        assert result.records, "expected a non-empty result"
        for rec in result.records:
            assert rec.score is not None

    def test_score_populated_when_flag_off(
        self, tmp_path: Path, tmp_relational_store: RelationalStore
    ) -> None:
        query = "tessellate hollowmere quintant"
        _insert_matching_crystals(tmp_relational_store, 1, query)
        aetheryte = _build_aetheryte(tmp_path, tmp_relational_store, recall_relevance_primary=False)

        result = aetheryte.recall(
            scope=Scope(project="proj-a"), query=query, k=5,
            layers=["episodic"], caller_tier=Tier.T1,
        )
        assert result.records
        for rec in result.records:
            assert rec.score is not None


# ---------------------------------------------------------------------------
# AC-007 — descending score ordering (default configuration), STRENGTHENED
# per DP-R2/C-13 (revision 2.1.0 amendment)
# ---------------------------------------------------------------------------


class TestOrdering:
    def test_evicting_slot_emits_descending_score(
        self, tmp_path: Path, tmp_relational_store: RelationalStore
    ) -> None:
        """AC-007, strengthened (was test_records_ordered_by_descending_score).

        vigil's F-V2: the previous fixture (3 short records, 800-token slot)
        never evicted, so Pass-1 never touched `remaining` (sorted ASCENDING
        by eviction key) and insertion order was already descending —
        deleting seam 5's output sort (attack E) left the old node GREEN even
        though seam 5 is the ONLY code guaranteeing descending order. THIS
        fixture forces >=1 eviction: four candidates (differing BM25 rank via
        document-length normalisation — FTS5 MATCH is implicit-AND, so every
        summary must contain all three query terms, per
        relational.py:_fts5_query) routed to one 30-token episodic slot.
        Total = 3+10+17+24 = 54 tokens > 30 -> Pass 1 evicts exactly the
        lowest-relevance survivor (c4, 24 tokens; 54-24=30, not > cap) and
        reassembles the slot from `remaining` (POST-pop order [c3, c2, c1] —
        ASCENDING relevance, the worst-first order F-V2 warned about). Only
        seam 5's final sort turns that into [c1, c2, c3] (descending).
        Gate proof: deleting the seam-5 sort must turn this RED (verified
        manually — see the maker's attack-sanity notes).
        """
        query = "alpha bravo charlie"
        # FTS5 MATCH is implicit-AND across terms — every crystal must contain
        # all three query terms to become a candidate at all. Differing
        # document LENGTH (same term set, more filler words) differentiates
        # BM25 rank via length normalisation: the shortest exact match ranks
        # best, the longest padded one ranks worst.
        tmp_relational_store.insert_crystal(
            _crystal_dict(id="c1", summary="alpha bravo charlie", importance=0.1)
        )
        tmp_relational_store.insert_crystal(
            _crystal_dict(
                id="c2",
                summary="alpha bravo charlie one two three four five six seven",
                importance=0.9,
            )
        )
        tmp_relational_store.insert_crystal(
            _crystal_dict(
                id="c3",
                summary=(
                    "alpha bravo charlie one two three four five six seven eight "
                    "nine ten eleven twelve thirteen fourteen"
                ),
                importance=0.9,
            )
        )
        tmp_relational_store.insert_crystal(
            _crystal_dict(
                id="c4",
                summary=(
                    "alpha bravo charlie one two three four five six seven eight "
                    "nine ten eleven twelve thirteen fourteen fifteen sixteen "
                    "seventeen eighteen nineteen twenty twentyone"
                ),
                importance=0.9,
            )
        )
        aetheryte = _build_aetheryte(tmp_path, tmp_relational_store, episodic_cap=30)

        result = aetheryte.recall(
            scope=Scope(project="proj-a"), query=query, k=10,
            layers=["episodic"], caller_tier=Tier.T1,
        )
        ids = [r.id for r in result.records]
        assert "c4" not in ids, f"c4 (lowest relevance) should have been evicted; got {ids}"
        assert len(result.records) >= 2, "fixture must survive with >=2 records to prove ordering"
        scores = [r.score for r in result.records]
        assert scores == sorted(scores, reverse=True), f"expected non-increasing score order, got {scores}"


# ---------------------------------------------------------------------------
# AC-008 / AC-009 — legacy path (flag off)
#
# F4: fixture importance is pinned by DIRECT relational.insert_crystal (never
# via the commit API — D4's cold-start clamp is ungated, so a captured
# expectation from a fresh commit would be tautological, not "pre-fix").
# ---------------------------------------------------------------------------


class TestLegacyPath:
    def test_flag_off_restores_prefix_behaviour(
        self, tmp_path: Path, tmp_relational_store: RelationalStore
    ) -> None:
        query = "alpha bravo charlie delta echo"
        t0 = datetime(2026, 1, 1, tzinfo=_UTC)
        low = _crystal_dict(id="low", summary=query, importance=0.1, last_access=t0)
        high = _crystal_dict(id="high", summary=query, importance=0.9, last_access=t0)
        tmp_relational_store.insert_crystal(low)
        tmp_relational_store.insert_crystal(high)

        # episodic_cap=8: 10 words total (5+5) overflow -> exactly one eviction.
        aetheryte = _build_aetheryte(
            tmp_path, tmp_relational_store, recall_relevance_primary=False, episodic_cap=8,
        )
        result = aetheryte.recall(
            scope=Scope(project="proj-a"), query=query, k=10,
            layers=["episodic"], caller_tier=Tier.T1,
        )
        # Legacy key (importance asc, last_access asc, id asc): lowest importance
        # (low=0.1) is evicted first; high (0.9) survives — byte-identical to
        # the pre-1.9.0 D9 rule.
        ids = {r.id for r in result.records}
        assert "high" in ids
        assert "low" not in ids

    def test_flag_off_does_not_cap_at_k(
        self, tmp_path: Path, tmp_relational_store: RelationalStore
    ) -> None:
        query = "narwhal grommet aspidistra"
        _insert_matching_crystals(tmp_relational_store, 3, query)
        aetheryte = _build_aetheryte(tmp_path, tmp_relational_store, recall_relevance_primary=False)

        result = aetheryte.recall(
            scope=Scope(project="proj-a"), query=query, k=1,
            layers=["episodic"], caller_tier=Tier.T1,
        )
        assert len(result.records) > 1, "flag-off must NOT truncate to k (AC-009 / C-1 boundary)"


# ---------------------------------------------------------------------------
# AC-010..014 — cold-start importance
# ---------------------------------------------------------------------------


def _episodic_layer(cfg: Config, relational: RelationalStore, importance_fn: Any) -> EpisodicLayer:
    from crystalium.storage.blob import BlobStore
    return EpisodicLayer(
        blob_store=BlobStore(root=cfg.data_dir / "blobs"),
        relational=relational, vector_store=None, graph_store=None,
        enforcement=Enforcement(cfg), redactor=Redactor(cfg), importance_fn=importance_fn,
    )


def _procedural_layer(cfg: Config, relational: RelationalStore, importance_fn: Any) -> ProceduralLayer:
    from crystalium.storage.blob import BlobStore
    from crystalium.gate import PromotionGate
    enforcement = Enforcement(cfg)
    return ProceduralLayer(
        blob_store=BlobStore(root=cfg.data_dir / "blobs"),
        relational=relational, enforcement=enforcement,
        gate=PromotionGate(cfg, relational, enforcement),
        redactor=Redactor(cfg), importance_fn=importance_fn, data_dir=cfg.data_dir,
    )


def _semantic_layer(cfg: Config, relational: RelationalStore, importance_fn: Any) -> SemanticLayer:
    from crystalium.storage.blob import BlobStore
    from crystalium.gate import PromotionGate
    enforcement = Enforcement(cfg)
    return SemanticLayer(
        blob_store=BlobStore(root=cfg.data_dir / "blobs"),
        relational=relational, vector_store=None, graph_store=None,
        enforcement=enforcement, gate=PromotionGate(cfg, relational, enforcement),
        redactor=Redactor(cfg), importance_fn=importance_fn,
    )


class TestColdStartImportance:
    @pytest.mark.parametrize("layer_name", ["episodic", "procedural", "semantic"])
    def test_commit_importance_nonzero(
        self, tmp_path: Path, tmp_relational_store: RelationalStore, layer_name: str
    ) -> None:
        """C-5 RED-first at af24493; DP-4c. evb_enabled=True (default)."""
        cfg = Config(data_dir=tmp_path / f"ci-{layer_name}", rate_limit_per_minute=1_000_000)
        importance_fn = make_evb_scorer(cfg.evb_gain_weights, cfg.evb_need_weights)
        payload = {
            "summary": "a genuinely descriptive commit-importance test payload",
            "scope": {"project": "proj-a"},
        }
        provenance = {"source": "verified_agent", "author_agent": "tester"}

        if layer_name == "episodic":
            layer = _episodic_layer(cfg, tmp_relational_store, importance_fn)
            result = layer.commit(payload=payload, provenance=provenance, caller_tier=Tier.T1)
        elif layer_name == "procedural":
            layer = _procedural_layer(cfg, tmp_relational_store, importance_fn)
            result = layer.commit(payload=payload, provenance=provenance, caller_tier=Tier.T1)
        else:
            layer = _semantic_layer(cfg, tmp_relational_store, importance_fn)
            result = layer.commit(
                payload=payload, provenance=provenance, caller_tier=Tier.T0, force_promote=True,
            )

        assert result["status"] == "committed"
        assert result["importance"] > 0.0, f"{layer_name}: cold-start importance must be nonzero"

    def test_legacy_scorer_clamped_to_ceiling(
        self, tmp_path: Path, tmp_relational_store: RelationalStore
    ) -> None:
        """C-3: evb_enabled=False -> the bare legacy score (~0.525) is clamped."""
        cfg = Config(data_dir=tmp_path / "ci-legacy", rate_limit_per_minute=1_000_000, evb_enabled=False)
        layer = _episodic_layer(cfg, tmp_relational_store, importance_score)
        result = layer.commit(
            payload={"summary": "legacy scorer cold-start ceiling test payload", "scope": {"project": "proj-a"}},
            provenance={"source": "verified_agent", "author_agent": "tester"},
            caller_tier=Tier.T1,
        )
        assert result["status"] == "committed"
        assert result["importance"] <= COLD_START_IMPORTANCE_CEILING
        assert result["importance"] > 0.0

    def test_evb_path_unclamped(
        self, tmp_path: Path, tmp_relational_store: RelationalStore
    ) -> None:
        """C-3: evb_enabled=True -> the ceiling never binds at novelty=0.5."""
        cfg = Config(data_dir=tmp_path / "ci-evb", rate_limit_per_minute=1_000_000, evb_enabled=True)
        importance_fn = make_evb_scorer(cfg.evb_gain_weights, cfg.evb_need_weights)
        layer = _episodic_layer(cfg, tmp_relational_store, importance_fn)
        result = layer.commit(
            payload={
                "summary": "evb path unclamped cold-start test payload",
                "scope": {"project": "proj-a"}, "novelty_at_write": 0.5,
            },
            provenance={"source": "verified_agent", "author_agent": "tester"},
            caller_tier=Tier.T1,
        )
        assert result["status"] == "committed"

        # Independent reference: recency normalises to 1.0 whenever last_access
        # == now, which is always true for a fresh commit's utility stub — so
        # this reproduces deterministically regardless of wall-clock time.
        class _FreshStub:
            access_count = 0
            outcome_success = None
            novelty_at_write = 0.5
            last_access = datetime.now(_UTC)

        expected = evb_score(_FreshStub(), now=_FreshStub.last_access)
        assert result["importance"] == pytest.approx(expected, abs=1e-9)
        assert result["importance"] < COLD_START_IMPORTANCE_CEILING, "the clamp must not bind on the default path"

    def test_execution_layer_importance_unchanged(
        self, tmp_path: Path, tmp_relational_store: RelationalStore
    ) -> None:
        """DP-4d: execution.py's 0.5 'currently active work' privilege stays."""
        from crystalium.storage.blob import BlobStore
        cfg = Config(data_dir=tmp_path / "ci-exec", rate_limit_per_minute=1_000_000)
        layer = ExecutionLayer(
            blob_store=BlobStore(root=cfg.data_dir / "blobs"),
            relational=tmp_relational_store, enforcement=Enforcement(cfg),
            importance_fn=importance_score,
        )
        result = layer.checkpoint(
            state={"scope": {"project": "proj-a"}, "summary": "execution checkpoint importance test"},
            caller_tier=Tier.T1,
        )
        stored = tmp_relational_store.get_crystal(result["id"])
        assert stored["utility"]["importance"] == 0.5

    def test_in_session_reinforcement_closes_the_loop(
        self, tmp_path: Path, tmp_relational_store: RelationalStore
    ) -> None:
        """F5: the oracle is store inspection (utility.access_count), not a
        recall-response field — CrystalSummary carries no access_count."""
        cfg = Config(data_dir=tmp_path / "ci-reinforce", rate_limit_per_minute=1_000_000)
        importance_fn = make_evb_scorer(cfg.evb_gain_weights, cfg.evb_need_weights)
        layer = _episodic_layer(cfg, tmp_relational_store, importance_fn)
        commit_result = layer.commit(
            payload={"summary": "reinforcement loop closure test payload alpha", "scope": {"project": "proj-a"}},
            provenance={"source": "verified_agent", "author_agent": "tester"},
            caller_tier=Tier.T1,
        )
        crystal_id = commit_result["id"]

        aetheryte = _build_aetheryte(tmp_path, tmp_relational_store)
        query = "reinforcement loop closure test payload alpha"
        aetheryte.recall(scope=Scope(project="proj-a"), query=query, k=5,
                          layers=["episodic"], caller_tier=Tier.T1)
        aetheryte.recall(scope=Scope(project="proj-a"), query=query, k=5,
                          layers=["episodic"], caller_tier=Tier.T1)

        stored = tmp_relational_store.get_crystal(crystal_id)
        assert stored["utility"]["access_count"] >= 1


# ---------------------------------------------------------------------------
# AC-015..019, AC-032 — budget surfaced (AC-016/AC-017 strengthened, AC-032
# new-by-relocation, per DP-R3/C-14 — revision 2.1.0 amendment)
# ---------------------------------------------------------------------------


class TestBudgetSurfaced:
    def test_budget_object_present_with_five_fields(
        self, tmp_path: Path, tmp_relational_store: RelationalStore
    ) -> None:
        aetheryte = _build_aetheryte(tmp_path, tmp_relational_store)
        result = aetheryte.recall(
            scope=Scope(project="proj-a"), query="anything at all", k=5,
            layers=["episodic"], caller_tier=Tier.T1,
        )
        assert result.budget is not None
        assert result.budget.total_cap == 3500
        assert isinstance(result.budget.slots, dict)
        assert result.budget.k_requested == 5
        assert result.budget.k_applied == len(result.records)
        assert result.budget.truncated_count >= 0

    def test_truncated_count_derived_from_real_slice(
        self, tmp_path: Path, tmp_relational_store: RelationalStore
    ) -> None:
        """AC-016, strengthened per DP-R3/C-14 (was test_truncated_count_reports_k_drops).

        The oracle asserts the SLICE IDENTITY: `len(records) + truncated_count
        == candidates-before-the-slice`, not bare arithmetic (`8 - 3`) — a
        counter computed from pre-slice INTENT rather than the performed act
        can satisfy `8 - 3 == 5` while being completely disconnected from
        reality (verification.md F-V3: attack D — delete `filtered_ids =
        filtered_ids[:k]`, keep the old counter — shipped `truncated_count=5`
        for zero actual drops, and the old bare-arithmetic oracle stayed
        green). The identity catches that shape of defect: it only holds when
        `truncated_count` is genuinely `before - after`.
        """
        query = "cardamom lissome vantablack"
        _insert_matching_crystals(tmp_relational_store, 8, query)
        aetheryte = _build_aetheryte(tmp_path, tmp_relational_store)

        # Independent baseline: candidates-before-the-slice, observed via a
        # k large enough that seam 3 never truncates (8 candidates < k=100).
        baseline = aetheryte.recall(
            scope=Scope(project="proj-a"), query=query, k=100,
            layers=["episodic"], caller_tier=Tier.T1,
        )
        candidates_before_slice = len(baseline.records)
        assert candidates_before_slice == 8  # sanity: the fixture's own count

        result = aetheryte.recall(
            scope=Scope(project="proj-a"), query=query, k=3,
            layers=["episodic"], caller_tier=Tier.T1,
        )
        assert result.budget.truncated_count == 8 - 3
        assert len(result.records) + result.budget.truncated_count == candidates_before_slice

    def test_k_applied_never_exceeds_k_requested(
        self, tmp_path: Path, tmp_relational_store: RelationalStore
    ) -> None:
        """AC-017, strengthened / re-subjected per DP-R3/C-14 (new node — the
        `evicted_count == 0` pin this ID previously carried relocates
        verbatim to AC-032 / test_evicted_count_excludes_k_truncation below).

        `k_applied = min(k_requested, len(before))` makes `k_applied <=
        k_requested` true BY CONSTRUCTION, in every configuration. Under
        attack D the shipped object reported `k_requested=15, k_applied=20`
        (`k_applied > k_requested`) while every existing oracle stayed green
        (verification.md F-V3).
        """
        query = "starwise glockenfen tarrow"
        _insert_matching_crystals(tmp_relational_store, 8, query)
        aetheryte = _build_aetheryte(tmp_path, tmp_relational_store)
        for k in (1, 3, 5, 8, 15, 100):
            result = aetheryte.recall(
                scope=Scope(project="proj-a"), query=query, k=k,
                layers=["episodic"], caller_tier=Tier.T1,
            )
            assert result.budget.k_applied <= result.budget.k_requested, (
                f"k_applied={result.budget.k_applied} > k_requested={result.budget.k_requested} at k={k}"
            )

    def test_evicted_count_excludes_k_truncation(
        self, tmp_path: Path, tmp_relational_store: RelationalStore
    ) -> None:
        """AC-032 (relocated verbatim from revision 2.0.0's AC-017 when that ID
        took the k_applied invariant above; text and body unchanged)."""
        query = "hexalith prismwatch dunnage"
        _insert_matching_crystals(tmp_relational_store, 8, query)
        aetheryte = _build_aetheryte(tmp_path, tmp_relational_store)
        result = aetheryte.recall(
            scope=Scope(project="proj-a"), query=query, k=3,
            layers=["episodic"], caller_tier=Tier.T1,
        )
        assert result.budget.truncated_count > 0
        assert result.evicted_count == 0

    def test_explain_carries_k_keys(
        self, tmp_path: Path, tmp_relational_store: RelationalStore
    ) -> None:
        query = "isoclast wrenfell quandary"
        _insert_matching_crystals(tmp_relational_store, 8, query)
        aetheryte = _build_aetheryte(tmp_path, tmp_relational_store)
        result = aetheryte.recall(
            scope=Scope(project="proj-a"), query=query, k=3,
            layers=["episodic"], caller_tier=Tier.T1, explain=True,
        )
        assert result.explain is not None
        assert "k_applied" in result.explain
        assert "truncated_by_k" in result.explain
        assert result.explain["k_applied"] == result.budget.k_applied
        assert result.explain["truncated_by_k"] == result.budget.truncated_count

    def test_total_cap_invariant_holds(
        self, tmp_path: Path, tmp_relational_store: RelationalStore
    ) -> None:
        query = "totalcap invariant probe query"
        _insert_matching_crystals(tmp_relational_store, 5, query)
        aetheryte = _build_aetheryte(tmp_path, tmp_relational_store)
        result = aetheryte.recall(
            scope=Scope(project="proj-a"), query=query, k=10,
            layers=["episodic"], caller_tier=Tier.T1,
        )
        assert result.total_tokens <= 3500


# ---------------------------------------------------------------------------
# AC-020 / AC-021 — k clamp + non-coercible fallback (DP-3d / C-11)
# ---------------------------------------------------------------------------


class TestKClamp:
    @pytest.mark.parametrize("raw_k", [0, -3, 500])
    def test_numeric_k_clamped(self, tmp_path: Path, raw_k: int) -> None:
        # server._handle_recall
        aetheryte = MagicMock()
        aetheryte.recall.return_value = MagicMock(model_dump=lambda **kw: {})
        cfg = Config(data_dir=tmp_path / f"kc-{raw_k}", rate_limit_per_minute=1_000_000)
        _handle_recall(
            {"scope": {"project": "p"}, "query": "q", "k": raw_k},
            aetheryte, MagicMock(), Tier.T1, cfg,
        )
        applied_k = aetheryte.recall.call_args.kwargs["k"]
        assert 1 <= applied_k <= 100

        # CLI verb (DP-3d: "at both entry points")
        from unittest.mock import patch
        from click.testing import CliRunner
        from crystalium.__main__ import cli

        fake_result = MagicMock()
        fake_result.model_dump.return_value = {
            "records": [], "slot_breakdown": {}, "total_tokens": 0, "evicted_count": 0,
        }
        with patch("crystalium.aetheryte.retrieve.Aetheryte") as MockAetheryte:
            mock_instance = MagicMock()
            mock_instance.recall.return_value = fake_result
            MockAetheryte.return_value = mock_instance

            runner = CliRunner()
            result = runner.invoke(
                cli,
                ["recall", "--query", "q", "--scope-project", "p", "--k", str(raw_k)],
                env={"CRYSTALIUM_DATA_DIR": str(tmp_path / f"kc-cli-{raw_k}")},
                catch_exceptions=False,
            )
            assert result.exit_code == 0, result.output
            cli_applied_k = mock_instance.recall.call_args.kwargs["k"]
            assert 1 <= cli_applied_k <= 100

    def test_non_coercible_k_falls_back_to_default(self, tmp_path: Path) -> None:
        aetheryte = MagicMock()
        aetheryte.recall.return_value = MagicMock(model_dump=lambda **kw: {})
        cfg = Config(data_dir=tmp_path / "kc-garbage", rate_limit_per_minute=1_000_000)
        _handle_recall(
            {"scope": {"project": "p"}, "query": "q", "k": "garbage"},
            aetheryte, MagicMock(), Tier.T1, cfg,
        )
        applied_k = aetheryte.recall.call_args.kwargs["k"]
        assert applied_k == 10

    def test_normalize_k_never_raises(self) -> None:
        for garbage in ["garbage", None, [], {}, object()]:
            assert normalize_k(garbage) == 10


# ---------------------------------------------------------------------------
# AC-022..024 — oversized-summary advisory (DP-8, C-4)
# ---------------------------------------------------------------------------


class TestOversizedSummary:
    def _components(self, tmp_path: Path, *, episodic_cap: int = 800):
        from crystalium.server import _build_components
        cfg = Config(
            data_dir=tmp_path,
            rate_limit_per_minute=1_000_000,
            slots={
                "executive": 300, "procedural": 600, "semantic": 800,
                "episodic": episodic_cap, "execution": 1000, "buffer": 300,
            },
        )
        return cfg, _build_components(cfg)

    def test_oversized_summary_advisory(self, tmp_path: Path) -> None:
        cfg, (_e, aetheryte, ep, se, pr, ex, _g, _s, _r) = self._components(tmp_path, episodic_cap=5)
        long_summary = "a genuinely descriptive summary with plenty of real words in it today"
        result = _handle_commit(
            {"layer": "episodic", "payload": {"summary": long_summary, "scope": {"project": "p"}},
             "provenance": {"source": "verified_agent"}},
            ep, se, pr, ex, Tier.T1, cfg, composer=aetheryte.composer,
        )
        assert result["status"] == "committed"
        assert result["summary_size"] == "oversized"
        assert result["summary_tokens"] > result["slot_cap"]
        assert result["slot_cap"] == 5
        assert "advisory" in result

    def test_fitting_summary_has_no_advisory(self, tmp_path: Path) -> None:
        cfg, (_e, aetheryte, ep, se, pr, ex, _g, _s, _r) = self._components(tmp_path, episodic_cap=800)
        result = _handle_commit(
            {"layer": "episodic",
             "payload": {"summary": "a genuinely descriptive summary about caching policy decisions",
                         "scope": {"project": "p"}},
             "provenance": {"source": "verified_agent"}},
            ep, se, pr, ex, Tier.T1, cfg, composer=aetheryte.composer,
        )
        assert result["status"] == "committed"
        assert "summary_size" not in result

    def test_tokenizer_exception_skips_advisory(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        cfg, (_e, aetheryte, ep, se, pr, ex, _g, _s, _r) = self._components(tmp_path, episodic_cap=5)

        def _raise(*_a: Any, **_kw: Any) -> int:
            raise RuntimeError("tokenizer exploded")

        monkeypatch.setattr(aetheryte.composer, "tokenize", _raise)
        result = _handle_commit(
            {"layer": "episodic",
             "payload": {"summary": "a genuinely descriptive summary about caching policy decisions",
                         "scope": {"project": "p"}},
             "provenance": {"source": "verified_agent"}},
            ep, se, pr, ex, Tier.T1, cfg, composer=aetheryte.composer,
        )
        assert result["status"] == "committed"
        assert "summary_size" not in result


# ---------------------------------------------------------------------------
# AC-025..027 — DX items
# ---------------------------------------------------------------------------


class TestDX:
    def test_commit_description_lists_provenance_sources(self) -> None:
        tools = {t["name"]: t for t in build_tool_manifest()}
        prov_desc = tools["crystalium.commit"]["inputSchema"]["properties"]["provenance"]["description"]
        for source in _VALID_PROVENANCE_SOURCES:
            assert source in prov_desc, f"{source!r} missing from provenance description"

    def test_tier_violation_advice_names_procedural_fallback(self) -> None:
        exc = TierViolation("crystalium.commit", "semantic", Tier.T2, "commit")
        assert "procedural" in exc.advice

    def test_advice_makes_no_promotion_promise(self) -> None:
        exc = TierViolation("crystalium.commit", "semantic", Tier.T2, "commit")
        assert "promoted to semantic" not in exc.advice
        assert "auto-promot" not in exc.advice


# ---------------------------------------------------------------------------
# AC-031 — seam 3b: fetch width decoupled from response cap k (DP-R1 / C-12)
#
# Models vigil's F-V1 mechanism (verification.md): `seed_ids = dense_ranking[:k]`
# (pre seam-3b) makes graph/completion arm MEMBERSHIP a function of the
# caller's response cap `k`, not just how many records come back. At a small
# k the walk's seed window narrows, concentrating attention and letting a
# cluster of topically-unrelated "neighbours" accumulate a third arm
# (graph + completion) that outranks a fresh, exactly-matching crystal's own
# two-arm (sparse+dense) presence — so the fresh crystal is not merely
# ranked worse, it is TRUNCATED OUT by seam 3's k-gate entirely.
#
# Modelled here via a seed-window-length-conditioned mock on
# neighbor_expand/decaying_walk: len(seed_ids) <= 3 (the pre-fix k=3 window,
# since dense_ranking[:3] has exactly 3 entries) injects a 4-strong noise
# cluster; a wider window (post seam-3b, fetch_width = max(k,
# FETCH_WIDTH_FLOOR) = 10 >= len(dense_ranking) = 5) does not. `fresh` is
# ALSO given a real dense hit (rank 1) so the post-fix comparison is a clean
# two-arm-vs-one-arm win, not a rank-1/rank-1 RRF tie (the exact trap
# critique F1 warned about for AC-001/AC-004).
# ---------------------------------------------------------------------------


class TestSmallKFetchWidth:
    def test_fresh_crystal_returned_at_k3(
        self, tmp_path: Path, tmp_relational_store: RelationalStore
    ) -> None:
        query = "glimmerfen paltrix bewock"  # 3 distinctive low-frequency tokens

        fresh = _crystal_dict(id="fresh", summary=f"{query} runbook notes here")
        tmp_relational_store.insert_crystal(fresh)
        for i in range(1, 5):
            tmp_relational_store.insert_crystal(
                _crystal_dict(id=f"n{i}", summary=f"unrelated topic filler content number {i}")
            )

        cfg = _make_config(tmp_path, recall_relevance_primary=True)
        enforcement = Enforcement(cfg)
        redactor = Redactor(cfg)
        composer = Composer(config=cfg, tokenizer=_word_tokenizer, recall_relevance_primary=True)

        vector_store = MagicMock()
        vector_store.embed.return_value = [0.1, 0.2, 0.3]
        # fresh is ALSO dense rank-1 (two-arm baseline: sparse + dense).
        vector_store.dense_search.return_value = [
            {"id": "fresh"}, {"id": "n1"}, {"id": "n2"}, {"id": "n3"}, {"id": "n4"},
        ]

        def _neighbor_expand(seed_ids: list[str], depth: int) -> list[str]:
            if len(seed_ids) <= 3:
                return ["n1", "n2", "n3", "n4"]
            return []

        def _decaying_walk(seed_ids: list[str], max_hops: int, decay: float) -> dict[str, float]:
            if len(seed_ids) <= 3:
                return {"n1": 0.9, "n2": 0.8, "n3": 0.7, "n4": 0.6}
            return {}

        graph_store = MagicMock()
        graph_store.neighbor_expand.side_effect = _neighbor_expand
        graph_store.decaying_walk.side_effect = _decaying_walk

        aetheryte = Aetheryte(
            relational=tmp_relational_store,
            vector_store=vector_store,
            graph_store=graph_store,
            enforcement=enforcement,
            redactor=redactor,
            importance_fn=importance_score,
            composer=composer,
            completion=True,
            recall_relevance_primary=True,
        )

        result = aetheryte.recall(
            scope=Scope(project="proj-a"), query=query, k=3,
            layers=["episodic"], caller_tier=Tier.T1,
        )
        ids = {r.id for r in result.records}
        assert "fresh" in ids, f"fresh crystal must be returned at k=3; got {ids}"


# ---------------------------------------------------------------------------
# AC-028 / AC-029 — schema round-trip (DP-X, C-7)
#
# C-7 hazard: must NOT use the skip-on-ImportError helper at test_schemas.py:
# 63-77 — jsonschema>=4.21 is a declared dependency, so import it unconditionally
# (done once, at module top — see the `import jsonschema` block above).
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
    import json
    with path.open() as fh:
        return json.load(fh)


class TestSchemaRoundTrip:
    def test_plain_result_validates(
        self, tmp_path: Path, tmp_relational_store: RelationalStore
    ) -> None:
        query = "schema roundtrip plain probe"
        _insert_matching_crystals(tmp_relational_store, 2, query)
        aetheryte = _build_aetheryte(tmp_path, tmp_relational_store)
        result = aetheryte.recall(
            scope=Scope(project="proj-a"), query=query, k=5,
            layers=["episodic"], caller_tier=Tier.T1,
        )
        schema = _load_recall_result_schema()
        instance = result.model_dump(exclude_none=True, mode="json")
        Draft202012Validator(schema).validate(instance)

    def test_explain_result_validates(
        self, tmp_path: Path, tmp_relational_store: RelationalStore
    ) -> None:
        query = "schema roundtrip explain probe"
        _insert_matching_crystals(tmp_relational_store, 2, query)
        aetheryte = _build_aetheryte(tmp_path, tmp_relational_store)
        result = aetheryte.recall(
            scope=Scope(project="proj-a"), query=query, k=5,
            layers=["episodic"], caller_tier=Tier.T1, explain=True,
        )
        schema = _load_recall_result_schema()
        instance = result.model_dump(exclude_none=True, mode="json")
        Draft202012Validator(schema).validate(instance)
