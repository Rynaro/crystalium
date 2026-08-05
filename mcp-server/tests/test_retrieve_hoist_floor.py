"""crystalium#46 / crystalium#47 — `retrieve.py` micro-repairs.

#46: `vector_store.embed(query)` is loop-invariant (its sole argument is the
     bare query string, independent of `layer`) — it must be called exactly
     once per `recall()`, not once per layer. This file gates:
       - call count (AC-230)
       - the `embed_skipped` warning shape after the hoist (AC-231)
       - `dense_got_vector` (surfaced via `explain.arms.dense`) surviving the
         hoist unchanged (AC-232)

#47: `candidate_k` must track `FETCH_WIDTH_FLOOR` rather than a bare literal
     `10` that happens to equal it. This file gates:
       - the floor link is REAL (AC-233, RED-CHECKED against unmodified
         `retrieve.py` — see the maker's report)
       - the floor link is a NO-OP at the shipped default (AC-234)

Template: test_fusion_weighting.py's harness (real RelationalStore so BM25/
FTS5 is genuine; MagicMock vector + graph stores). Deliberately
self-contained (no import from test_fusion_weighting.py) so this file stays
independently ownable.

Container-first: run via
  docker compose run --rm crystalium pytest mcp-server/tests/test_retrieve_hoist_floor.py -v
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from crystalium.aetheryte import retrieve as retrieve_mod
from crystalium.aetheryte.redact import Redactor
from crystalium.aetheryte.retrieve import FETCH_WIDTH_FLOOR, Aetheryte
from crystalium.composer import Composer
from crystalium.config import Config
from crystalium.enforcement import Enforcement
from crystalium.importance import importance_score
from crystalium.schemas import Scope
from crystalium.storage.relational import RelationalStore
from crystalium.trust import Tier

_UTC = timezone.utc
_ALL_LAYERS = ["episodic", "semantic", "procedural", "execution"]
_PROJECT = "hoist-floor-proj"


# ---------------------------------------------------------------------------
# Shared harness (modelled on test_fusion_weighting.py:62-190; kept local so
# this file never imports from a file it does not own)
# ---------------------------------------------------------------------------


def _word_tokenizer(text: str) -> int:
    return len(text.split())


def _make_config(tmp_path: Path, **overrides: Any) -> Config:
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
    project: str = _PROJECT,
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
    embed_return_value: list[float] | None = None,
    embed_side_effect: BaseException | type[BaseException] | None = None,
    dense_hits: list[str] | None = None,
) -> Aetheryte:
    """Layer-2 harness: real RelationalStore (genuine BM25/FTS5) + MagicMock
    vector/graph stores, so `vector_store.embed` call count/args are exact."""
    cfg = _make_config(tmp_path)
    enforcement = Enforcement(cfg)
    redactor = Redactor(cfg)
    composer = Composer(config=cfg, tokenizer=_word_tokenizer, recall_relevance_primary=True)

    vector_store = MagicMock()
    # A bare MagicMock's attribute access returns a (truthy) child Mock, not
    # False — explicit here so `explain.arms.dense`'s `_is_null` branch
    # (retrieve.py:1049) reads the real stubbed VectorStore's contract,
    # never an accidental "null vector store" status.
    vector_store._is_null = False
    if embed_side_effect is not None:
        vector_store.embed.side_effect = embed_side_effect
    else:
        vector_store.embed.return_value = embed_return_value or [0.1, 0.2, 0.3]
    vector_store.dense_search.return_value = [{"id": cid} for cid in (dense_hits or [])]

    graph_store = MagicMock()
    graph_store._is_null = False
    graph_store.neighbor_expand.return_value = set()
    graph_store.decaying_walk.return_value = {}

    return Aetheryte(
        relational=relational,
        vector_store=vector_store,
        graph_store=graph_store,
        enforcement=enforcement,
        redactor=redactor,
        importance_fn=importance_score,
        composer=composer,
        completion=False,
        recall_active_only=False,
        recall_relevance_primary=True,
        recall_weighted_fusion=True,
        fusion_weight_dense=1.0,
        fusion_weight_derived=1.0,
        fusion_sparse_boost_alpha=1.0,
    )


# ---------------------------------------------------------------------------
# crystalium#46 — AC-230 / AC-231 / AC-232
# ---------------------------------------------------------------------------


class TestEmbedHoist:
    def test_embed_called_once_per_multi_layer_recall(
        self, tmp_path: Path, tmp_relational_store: RelationalStore
    ) -> None:
        """AC-230: `layers=None` searches all four layers (`_ALL_LAYERS`),
        so at `56c8510` (embed inside the per-layer loop) this call count
        would be 4. Post-hoist it must be exactly 1."""
        tmp_relational_store.insert_crystal(
            _crystal_dict(summary="wozzlefinch embed hoist notes")
        )
        aetheryte = _build_aetheryte(tmp_path, tmp_relational_store)

        aetheryte.recall(
            Scope(project=_PROJECT, agent_class_visibility="all"),
            "wozzlefinch embed hoist notes", 10, None, Tier.T1,
        )

        assert aetheryte.vector_store.embed.call_count == 1

    def test_embed_skipped_logged_once_with_layers(
        self,
        tmp_path: Path,
        tmp_relational_store: RelationalStore,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """AC-231: the CRYSTALIUM_SKIP_SLOW shape (`vector.py:88-92`, embed
        raises on every call) fires the `embed_skipped` warning exactly once
        per recall — never once per layer — and the per-layer `layer=`
        field (which has no hoisted equivalent) is replaced by `layers=`
        naming every layer that was in scope."""
        tmp_relational_store.insert_crystal(
            _crystal_dict(summary="fribbleton embed skip notes")
        )
        aetheryte = _build_aetheryte(
            tmp_path,
            tmp_relational_store,
            embed_side_effect=RuntimeError("model unavailable"),
        )
        mock_log = MagicMock()
        monkeypatch.setattr(retrieve_mod, "log", mock_log)

        aetheryte.recall(
            Scope(project=_PROJECT, agent_class_visibility="all"),
            "fribbleton embed skip notes", 10, None, Tier.T1,
        )

        embed_skipped_calls = [
            call
            for call in mock_log.warning.call_args_list
            if call.args and call.args[0] == "embed_skipped"
        ]
        assert len(embed_skipped_calls) == 1
        _, kwargs = embed_skipped_calls[0]
        assert "layer" not in kwargs, "the per-layer field must not survive the hoist"
        assert "layers" in kwargs
        assert kwargs["layers"] == _ALL_LAYERS

    def test_dense_got_vector_survives_hoist(
        self, tmp_path: Path, tmp_relational_store: RelationalStore
    ) -> None:
        """AC-232: `dense_got_vector` (surfaced via `explain.arms.dense`)
        reports the SAME value pre- and post-hoist for a successful embed —
        'active', not a status that regressed because the boolean is now
        assigned once instead of re-assigned identically on every layer."""
        tmp_relational_store.insert_crystal(
            _crystal_dict(summary="glimmering dense status notes")
        )
        aetheryte = _build_aetheryte(tmp_path, tmp_relational_store)

        result = aetheryte.recall(
            Scope(project=_PROJECT, agent_class_visibility="all"),
            "glimmering dense status notes", 10, None, Tier.T1, explain=True,
        )

        assert result.explain is not None
        assert result.explain["arms"]["dense"] == "active"

    def test_dense_got_vector_false_when_embed_unavailable(
        self,
        tmp_path: Path,
        tmp_relational_store: RelationalStore,
    ) -> None:
        """Companion negative case for AC-232: an embed that raises on the
        single hoisted call must still report 'inactive(embed_unavailable)'
        — not a stale True left over from a different code path."""
        tmp_relational_store.insert_crystal(
            _crystal_dict(summary="glimmering dense status notes")
        )
        aetheryte = _build_aetheryte(
            tmp_path,
            tmp_relational_store,
            embed_side_effect=RuntimeError("model unavailable"),
        )

        result = aetheryte.recall(
            Scope(project=_PROJECT, agent_class_visibility="all"),
            "glimmering dense status notes", 10, None, Tier.T1, explain=True,
        )

        assert result.explain is not None
        assert result.explain["arms"]["dense"] == "inactive(embed_unavailable)"


# ---------------------------------------------------------------------------
# crystalium#47 — AC-233 / AC-234
# ---------------------------------------------------------------------------


class TestFloorLink:
    def test_candidate_k_follows_fetch_width_floor(
        self,
        tmp_path: Path,
        tmp_relational_store: RelationalStore,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """AC-233 (RED-CHECK REQUIRED, per spec.criteria.md — demonstrated
        against unmodified `retrieve.py` before the fix landed; see the
        maker's report for the captured red output): with
        `FETCH_WIDTH_FLOOR` monkeypatched to 50 and `k=1`, `candidate_k`
        must equal 50, not `max(1*3, 10) == 10`. `monkeypatch.setattr` is
        used (never a direct module-attribute assignment) so the mutation
        auto-restores even on failure — the same global
        `evals/fusion_gate.py:227-229` mutates with an explicit `finally`
        restore at `:264`; a leaked mutation would poison later tests."""
        tmp_relational_store.insert_crystal(
            _crystal_dict(summary="floor probe notes")
        )
        monkeypatch.setattr(retrieve_mod, "FETCH_WIDTH_FLOOR", 50)
        aetheryte = _build_aetheryte(tmp_path, tmp_relational_store)

        result = aetheryte.recall(
            Scope(project=_PROJECT, agent_class_visibility="all"),
            "floor probe notes", 1, None, Tier.T1, explain=True,
        )

        assert result.explain is not None
        assert result.explain["fusion"]["candidate_k"] == 50

    @pytest.mark.parametrize("k", [1, 3, 10, 50])
    def test_candidate_k_unchanged_at_shipped_default(
        self, tmp_path: Path, tmp_relational_store: RelationalStore, k: int
    ) -> None:
        """AC-234: at the shipped `FETCH_WIDTH_FLOOR == 10` default, linking
        `candidate_k` to the constant must be a NO-OP — `candidate_k` stays
        exactly `max(k*3, 10)` for every k in {1, 3, 10, 50}."""
        assert FETCH_WIDTH_FLOOR == 10, "shipped default assumption changed"
        tmp_relational_store.insert_crystal(
            _crystal_dict(summary="floor default notes")
        )
        aetheryte = _build_aetheryte(tmp_path, tmp_relational_store)

        result = aetheryte.recall(
            Scope(project=_PROJECT, agent_class_visibility="all"),
            "floor default notes", k, None, Tier.T1, explain=True,
        )

        assert result.explain is not None
        assert result.explain["fusion"]["candidate_k"] == max(k * 3, 10)
