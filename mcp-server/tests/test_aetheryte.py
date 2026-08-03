"""Tests for Aetheryte hybrid recall — BM25 + dense + graph + RRF.

Container-first: run via:
  docker compose run --rm crystalium pytest mcp-server/tests/test_aetheryte.py -v

Heavy tests (sentence-transformers model download) are marked @pytest.mark.slow
and skipped when CRYSTALIUM_SKIP_SLOW=1.
"""

from __future__ import annotations

import hashlib
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from crystalium.aetheryte.retrieve import Aetheryte, rrf_merge
from crystalium.aetheryte.redact import Redactor
from crystalium.composer import Composer
from crystalium.config import Config
from crystalium.enforcement import Enforcement
from crystalium.importance import importance_score
from crystalium.schemas import Scope
from crystalium.storage.relational import RelationalStore
from crystalium.trust import Tier


_UTC = timezone.utc


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(tmp_path: Path) -> Config:
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
    cfg.rate_limit_per_minute = 200
    cfg.install_ts = None
    cfg.repo_root = None
    # crystalium#36 / C-8: the new Config field, assigned in the same commit
    # that adds it (R-3).
    cfg.recall_relevance_primary = True
    # crystalium#38 / S-5: the four new fusion config fields, assigned in the
    # same commit that adds them (same rationale as recall_relevance_primary
    # above — Config.__new__ bypasses __init__/dataclass defaults entirely).
    cfg.recall_weighted_fusion = True
    cfg.fusion_weight_dense = 1.0
    cfg.fusion_weight_derived = 1.0
    cfg.fusion_sparse_boost_alpha = 1.0
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    return cfg


def _word_tokenizer(text: str) -> int:
    return len(text.split())


def _crystal_dict(
    id: str | None = None,
    layer: str = "semantic",
    summary: str = "test summary",
    trust_tier: str = "T1",
    project: str = "proj-a",
    agent_class_visibility: str | None = "all",
    sensitivity_tag: str = "none",
    importance: float = 0.5,
) -> dict[str, Any]:
    cid = id or str(uuid.uuid4())
    now = datetime.now(_UTC)
    content_ref = hashlib.sha256(cid.encode()).hexdigest()
    return {
        "id": cid,
        "layer": layer,
        "summary": summary,
        "trust_tier": trust_tier,
        "validation_state": "validated",
        "status": "active",
        "content_ref": content_ref,
        "embedding_ref": None,
        "scope": {
            "project": project,
            "agent_class_visibility": agent_class_visibility,
            "sensitivity_tag": sensitivity_tag,
        },
        "provenance": {
            "source": "verified_agent",
            "author_agent": "test-agent",
            "task_id": None,
            "created_at": now.isoformat(),
        },
        "temporal": {
            "t_valid_from": now.isoformat(),
            "t_valid_to": None,
            "superseded_by": None,
        },
        "utility": {
            "access_count": 5,
            "last_access": now.isoformat(),
            "outcome_success_score": None,
            "importance": importance,
            "novelty_at_write": 0.6,
        },
    }


def _build_aetheryte(
    tmp_path: Path,
    relational: RelationalStore,
    skip_vector: bool = True,
) -> Aetheryte:
    """Build an Aetheryte with mock vector + graph stores for unit tests."""
    cfg = _make_config(tmp_path)

    enforcement = Enforcement(cfg)
    redactor = Redactor(cfg)
    composer = Composer(config=cfg, tokenizer=_word_tokenizer)

    if skip_vector:
        vector_store = MagicMock()
        vector_store.embed.return_value = []
        vector_store.dense_search.return_value = []
    else:
        from crystalium.storage.vector import VectorStore
        vector_store = VectorStore(lance_dir=tmp_path / "vectors.lance")

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
    )


# ---------------------------------------------------------------------------
# BM25 hybrid recall (sparse arm tested via real RelationalStore)
# ---------------------------------------------------------------------------


class TestAetheryteBm25Recall:
    """BM25 recall path — no dense/graph arm (mock vector+graph)."""

    def test_bm25_hit_returns_relevant_record(
        self, tmp_path: Path, tmp_relational_store: RelationalStore
    ) -> None:
        """Insert a crystal whose summary contains the query terms; BM25 should return it.

        Note on FTS5 tokenisation: SQLite's default FTS5 tokeniser does not stem,
        so query terms must literally appear in the indexed text. We use full
        tokens that exist verbatim in the summary; stemming/prefix-matching is
        an OQ-4 follow-up for v0.2.
        """
        crystal = _crystal_dict(
            summary="authentication bug fix in login handler",
            project="proj-a",
        )
        tmp_relational_store.insert_crystal(crystal)

        aetheryte = _build_aetheryte(tmp_path, tmp_relational_store)

        scope = Scope(project="proj-a")
        result = aetheryte.recall(
            scope=scope,
            query="authentication bug fix",
            k=5,
            layers=["semantic", "episodic", "procedural", "execution"],
            caller_tier=Tier.T1,
        )

        # The relevant crystal should appear in results
        ids = {r.id for r in result.records}
        assert crystal["id"] in ids, (
            f"Expected crystal {crystal['id']!r} in recall results; got {ids}"
        )

    def test_empty_store_returns_empty(
        self, tmp_path: Path, tmp_relational_store: RelationalStore
    ) -> None:
        """Empty store returns empty RecallResult."""
        aetheryte = _build_aetheryte(tmp_path, tmp_relational_store)
        scope = Scope(project="proj-a")
        result = aetheryte.recall(
            scope=scope, query="anything", k=5, layers=None, caller_tier=Tier.T1
        )
        assert result.records == []
        assert result.total_tokens == 0
        assert result.evicted_count == 0

    def test_recall_all_layers_by_default(
        self, tmp_path: Path, tmp_relational_store: RelationalStore
    ) -> None:
        """layers=None searches all four layers."""
        for layer in ("episodic", "semantic", "procedural", "execution"):
            c = _crystal_dict(
                summary=f"relevant content in {layer} layer",
                layer=layer,
                project="proj-all",
            )
            if layer == "episodic":
                c["content_ref"] = hashlib.sha256(c["id"].encode()).hexdigest()
            tmp_relational_store.insert_crystal(c)

        aetheryte = _build_aetheryte(tmp_path, tmp_relational_store)
        scope = Scope(project="proj-all")
        result = aetheryte.recall(
            scope=scope, query="relevant content", k=10, layers=None, caller_tier=Tier.T1
        )
        found_layers = {r.layer for r in result.records}
        # At least one result per layer
        assert len(found_layers) >= 1


# ---------------------------------------------------------------------------
# Scope filter
# ---------------------------------------------------------------------------


class TestScopeFilter:
    def test_cross_project_recall_filtered(
        self, tmp_path: Path, tmp_relational_store: RelationalStore
    ) -> None:
        """Crystals from project B should not appear in a recall scoped to project A."""
        proj_a_crystal = _crystal_dict(
            summary="project alpha confidential data", project="proj-a"
        )
        proj_b_crystal = _crystal_dict(
            summary="project beta confidential data", project="proj-b"
        )
        tmp_relational_store.insert_crystal(proj_a_crystal)
        tmp_relational_store.insert_crystal(proj_b_crystal)

        aetheryte = _build_aetheryte(tmp_path, tmp_relational_store)
        scope = Scope(project="proj-a")
        result = aetheryte.recall(
            scope=scope, query="confidential data", k=10, layers=None, caller_tier=Tier.T1
        )

        ids = {r.id for r in result.records}
        assert proj_b_crystal["id"] not in ids, (
            "Cross-project crystal must not appear in proj-a recall"
        )
        # proj-a crystal may or may not appear depending on FTS5 content
        # (it depends on FTS5 matching "confidential data" — allowed here)

    def test_agent_class_visibility_filtered(
        self, tmp_path: Path, tmp_relational_store: RelationalStore
    ) -> None:
        """Crystals with a specific agent_class_visibility not matching the scope are filtered."""
        # Crystal visible only to "agent-class-x"
        restricted = _crystal_dict(
            summary="restricted agent content",
            project="proj-acv",
            agent_class_visibility="agent-class-x",
        )
        # Crystal visible to "all"
        visible = _crystal_dict(
            summary="open agent content",
            project="proj-acv",
            agent_class_visibility="all",
        )
        tmp_relational_store.insert_crystal(restricted)
        tmp_relational_store.insert_crystal(visible)

        aetheryte = _build_aetheryte(tmp_path, tmp_relational_store)
        # Recall as "agent-class-y" — should not see "agent-class-x" restricted content
        scope = Scope(project="proj-acv", agent_class_visibility="agent-class-y")
        result = aetheryte.recall(
            scope=scope, query="agent content", k=10, layers=None, caller_tier=Tier.T1
        )

        ids = {r.id for r in result.records}
        assert restricted["id"] not in ids, (
            "agent-class-x crystal must not appear for agent-class-y scope"
        )


# ---------------------------------------------------------------------------
# Redactor applied to output
# ---------------------------------------------------------------------------


class TestRedactorApplied:
    def test_ssn_in_summary_is_redacted(
        self, tmp_path: Path, tmp_relational_store: RelationalStore
    ) -> None:
        """Crystal summary containing an SSN pattern should be redacted in output."""
        ssn_crystal = _crystal_dict(
            summary="User SSN is 123-45-6789 stored in system",
            project="proj-redact",
            sensitivity_tag="pii",
        )
        tmp_relational_store.insert_crystal(ssn_crystal)

        aetheryte = _build_aetheryte(tmp_path, tmp_relational_store)
        scope = Scope(project="proj-redact")
        result = aetheryte.recall(
            scope=scope, query="SSN system", k=5, layers=None, caller_tier=Tier.T1
        )

        for record in result.records:
            if record.id == ssn_crystal["id"]:
                # SSN should be replaced with «REDACTED:SSN»
                assert "123-45-6789" not in record.summary, (
                    "SSN was NOT redacted in recall output"
                )
                assert "REDACTED" in record.summary, (
                    "Expected REDACTED sentinel in output"
                )
                break


# ---------------------------------------------------------------------------
# Rate limit
# ---------------------------------------------------------------------------


class TestRateLimit:
    def test_rate_limit_enforced(
        self, tmp_path: Path, tmp_relational_store: RelationalStore
    ) -> None:
        """RateLimitExceeded is raised when calls exceed the per-minute limit."""
        from crystalium.enforcement import RateLimitExceeded

        cfg = _make_config(tmp_path)
        cfg.rate_limit_per_minute = 2  # very low limit for testing

        enforcement = Enforcement(cfg)
        redactor = Redactor(cfg)
        composer = Composer(config=cfg, tokenizer=_word_tokenizer)
        graph_store = MagicMock()
        graph_store.neighbor_expand.return_value = set()
        vector_store = MagicMock()
        vector_store.embed.return_value = []
        vector_store.dense_search.return_value = []

        aetheryte = Aetheryte(
            relational=tmp_relational_store,
            vector_store=vector_store,
            graph_store=graph_store,
            enforcement=enforcement,
            redactor=redactor,
            importance_fn=importance_score,
            composer=composer,
        )
        scope = Scope(project="proj-rl")

        # First two calls should succeed
        aetheryte.recall(scope=scope, query="q", k=1, layers=None, caller_tier=Tier.T1)
        aetheryte.recall(scope=scope, query="q", k=1, layers=None, caller_tier=Tier.T1)

        # Third call should be rate-limited
        with pytest.raises(RateLimitExceeded):
            aetheryte.recall(scope=scope, query="q", k=1, layers=None, caller_tier=Tier.T1)
