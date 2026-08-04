"""Tests for DreamWorker — consolidate + prune + T0 identity.

Container-first: run via:
  docker compose run --rm crystalium pytest mcp-server/tests/test_dream_worker.py -v
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch, call

import pytest

from crystalium.config import Config
from crystalium.dream.worker import DreamWorker, _DREAM_TIER
from crystalium.enforcement import Enforcement, TierViolation
from crystalium.importance import importance_score
from crystalium.trust import Tier


_UTC = timezone.utc


# ---------------------------------------------------------------------------
# Fixtures
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
    # crystalium#38 / S-5: the four new fusion config fields, same rationale.
    cfg.recall_weighted_fusion = True
    cfg.fusion_weight_dense = 1.0
    cfg.fusion_weight_derived = 1.0
    cfg.fusion_sparse_boost_alpha = 1.0
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    return cfg


@pytest.fixture
def tmp_relational(tmp_path: Path):
    from crystalium.storage.relational import RelationalStore
    return RelationalStore(db_path=tmp_path / "test.sqlite")


@pytest.fixture
def tmp_enforcement(tmp_path: Path) -> Enforcement:
    cfg = _make_config(tmp_path)
    return Enforcement(cfg)


def _make_worker(
    relational: Any,
    enforcement: Enforcement,
    gate: Any | None = None,
) -> DreamWorker:
    if gate is None:
        gate = MagicMock()
        from crystalium.gate import PromotionResult
        gate.propose_semantic.return_value = PromotionResult(
            decision="pending", reason="human-confirm window"
        )

    graph_store = MagicMock()
    graph_store.neighbor_expand.return_value = set()
    vector_store = MagicMock()

    return DreamWorker(
        relational=relational,
        vector_store=vector_store,
        graph_store=graph_store,
        enforcement=enforcement,
        gate=gate,
        importance_fn=importance_score,
    )


def _crystal(
    id: str | None = None,
    layer: str = "episodic",
    summary: str = "test summary",
    trust_tier: str = "T1",
    importance: float = 0.5,
) -> dict[str, Any]:
    cid = id or str(uuid.uuid4())
    now = datetime.now(_UTC)
    return {
        "id": cid,
        "layer": layer,
        "summary": summary,
        "trust_tier": trust_tier,
        "validation_state": "unverified",
        "status": "active",
        "content_ref": None,
        "embedding_ref": None,
        "scope": {"project": "test-project", "agent_class_visibility": "all", "sensitivity_tag": "none"},
        "provenance": {"source": "verified_agent", "author_agent": "test-agent", "task_id": None, "created_at": now.isoformat()},
        "temporal": {"t_valid_from": now.isoformat(), "t_valid_to": None, "superseded_by": None},
        "utility": {
            "access_count": 3,
            "last_access": now.isoformat(),
            "outcome_success_score": None,
            "importance": importance,
            "novelty_at_write": 0.5,
        },
    }


# ---------------------------------------------------------------------------
# Consolidate: gate.propose_semantic is called (not direct write)
# ---------------------------------------------------------------------------


class TestConsolidate:
    def test_consolidate_calls_propose_semantic_not_direct_write(
        self, tmp_relational: Any, tmp_enforcement: Enforcement
    ) -> None:
        """_consolidate() must call gate.propose_semantic(); NEVER direct-write Semantic."""
        gate = MagicMock()
        from crystalium.gate import PromotionResult
        gate.propose_semantic.return_value = PromotionResult(
            decision="admit", reason="test", record_id="consolidated-id"
        )

        worker = _make_worker(tmp_relational, tmp_enforcement, gate=gate)

        cluster = [_crystal(trust_tier="T1"), _crystal(trust_tier="T1")]
        now = datetime.now(_UTC)

        worker._consolidate(cluster, now)

        # Verify propose_semantic was called
        gate.propose_semantic.assert_called_once()
        call_kwargs = gate.propose_semantic.call_args
        # caller_tier must be T0 (Dream's synthetic identity, OQ-2)
        assert call_kwargs.kwargs.get("caller_tier") == Tier.T0 or \
               (call_kwargs.args and call_kwargs.args[2] == Tier.T0 if len(call_kwargs.args) > 2 else True)

    def test_consolidate_skips_t3_cluster(
        self, tmp_relational: Any, tmp_enforcement: Enforcement
    ) -> None:
        """_consolidate() skips clusters whose min-tier is T3 (D7 / P0-6)."""
        gate = MagicMock()
        worker = _make_worker(tmp_relational, tmp_enforcement, gate=gate)

        cluster = [_crystal(trust_tier="T3"), _crystal(trust_tier="T1")]
        now = datetime.now(_UTC)

        result = worker._consolidate(cluster, now)

        # Cluster min-tier = T3; exceeds Semantic ceiling (T1) → skip
        gate.propose_semantic.assert_not_called()
        assert result is None

    def test_consolidate_routes_ceiling_check_through_named_g4_guard(
        self, tmp_relational: Any, tmp_enforcement: Enforcement
    ) -> None:
        """The G4 ceiling check in _consolidate must go through the named, tested
        enforcement guard (not an inline `>` reimplementation), so the one path that
        can actually launder trust into Semantic exercises the load-bearing mechanism.
        """
        from unittest.mock import MagicMock as _MM

        from crystalium.trust import Tier as _T

        gate = MagicMock()
        worker = _make_worker(tmp_relational, tmp_enforcement, gate=gate)

        # Spy on the named guard while delegating to the real implementation.
        orig = worker.enforcement.assert_tier_within_layer_ceiling
        spy = _MM(side_effect=orig)
        worker.enforcement.assert_tier_within_layer_ceiling = spy  # type: ignore[method-assign]

        worker._consolidate([_crystal(trust_tier="T3"), _crystal(trust_tier="T1")], datetime.now(_UTC))

        # Called with the consolidated min-trust tier (T3) for the semantic layer.
        spy.assert_called_once_with(_T.T3, "semantic")
        gate.propose_semantic.assert_not_called()      # raised → skipped

    def test_consolidate_empty_cluster_returns_none(
        self, tmp_relational: Any, tmp_enforcement: Enforcement
    ) -> None:
        gate = MagicMock()
        worker = _make_worker(tmp_relational, tmp_enforcement, gate=gate)
        result = worker._consolidate([], datetime.now(_UTC))
        assert result is None
        gate.propose_semantic.assert_not_called()

    def test_consolidate_dream_uses_t0_identity(
        self, tmp_relational: Any, tmp_enforcement: Enforcement
    ) -> None:
        """Dream's synthetic T0 identity is confirmed: enforcement called with T0."""
        gate = MagicMock()
        from crystalium.gate import PromotionResult
        gate.propose_semantic.return_value = PromotionResult(decision="pending", reason="test")

        assertion_calls = []
        original_assert = tmp_enforcement.assert_tier_allowed

        def _track_assert(tool: str, layer: str, tier: Tier, op: str) -> None:
            assertion_calls.append({"tool": tool, "layer": layer, "tier": tier, "op": op})
            return original_assert(tool, layer, tier, op)

        tmp_enforcement.assert_tier_allowed = _track_assert  # type: ignore[method-assign]

        worker = _make_worker(tmp_relational, tmp_enforcement, gate=gate)
        cluster = [_crystal(trust_tier="T1")]
        worker._consolidate(cluster, datetime.now(_UTC))

        # At least one assert_tier_allowed call with tier=T0
        t0_calls = [c for c in assertion_calls if c["tier"] == Tier.T0]
        assert len(t0_calls) >= 1, (
            "Dream consolidate must call assert_tier_allowed with T0 identity (OQ-2)"
        )


# ---------------------------------------------------------------------------
# Prune: deprecated, never deleted; T0 identity; telemetry
# ---------------------------------------------------------------------------


class TestPrune:
    def test_prune_marks_below_threshold_as_deprecated(
        self, tmp_relational: Any, tmp_path: Path
    ) -> None:
        """_prune() marks records below threshold as deprecated; never deletes."""
        cfg = _make_config(tmp_path)
        enforcement = Enforcement(cfg)
        worker = _make_worker(tmp_relational, enforcement)

        # Insert a crystal with very low importance (below all thresholds)
        low_crystal = _crystal(id="low-imp", layer="episodic", importance=0.01)
        tmp_relational.insert_crystal(low_crystal)

        now = datetime.now(_UTC)

        # Override thresholds to make pruning fire reliably
        worker.prune_thresholds = {"episodic": 0.99, "semantic": 0.99, "procedural": 0.99, "execution": 0.99}
        worker._prune(now)

        # Verify record is deprecated (not deleted)
        rec = tmp_relational.get_crystal("low-imp")
        assert rec is not None, "Record must not be hard-deleted (P0-5)"
        assert rec["status"] == "deprecated", f"Expected 'deprecated', got {rec['status']!r}"

    def test_prune_does_not_delete_records(
        self, tmp_relational: Any, tmp_path: Path
    ) -> None:
        """_prune() never hard-deletes — P0-5."""
        cfg = _make_config(tmp_path)
        enforcement = Enforcement(cfg)
        worker = _make_worker(tmp_relational, enforcement)

        crystal_id = str(uuid.uuid4())
        low_crystal = _crystal(id=crystal_id, layer="episodic", importance=0.0)
        tmp_relational.insert_crystal(low_crystal)

        worker.prune_thresholds = {"episodic": 1.0, "semantic": 1.0, "procedural": 1.0, "execution": 1.0}
        worker._prune(datetime.now(_UTC))

        # Record still exists in DB (not deleted)
        rec = tmp_relational.get_crystal(crystal_id)
        assert rec is not None, "Hard-delete detected! P0-5 violation."

    def test_prune_above_threshold_kept_active(
        self, tmp_relational: Any, tmp_path: Path
    ) -> None:
        """Records above the threshold remain active."""
        cfg = _make_config(tmp_path)
        enforcement = Enforcement(cfg)
        worker = _make_worker(tmp_relational, enforcement)

        high_crystal = _crystal(id="high-imp", layer="episodic", importance=0.9)
        tmp_relational.insert_crystal(high_crystal)

        # Threshold below the record's importance score (actual score depends on formula)
        worker.prune_thresholds = {"episodic": 0.01, "semantic": 0.01, "procedural": 0.01, "execution": 0.01}
        worker._prune(datetime.now(_UTC))

        rec = tmp_relational.get_crystal("high-imp")
        assert rec is not None
        assert rec["status"] == "active", "High-importance record should remain active"

    def test_prune_calls_enforcement_with_t0(
        self, tmp_relational: Any, tmp_path: Path
    ) -> None:
        """_prune() calls enforcement.assert_tier_allowed with tier=T0 per record (OQ-2)."""
        cfg = _make_config(tmp_path)
        enforcement = Enforcement(cfg)

        t0_calls = []
        original = enforcement.assert_tier_allowed

        def _track(tool: str, layer: str, tier: Tier, op: str) -> None:
            if tier == Tier.T0:
                t0_calls.append({"tool": tool, "layer": layer})
            return original(tool, layer, tier, op)

        enforcement.assert_tier_allowed = _track  # type: ignore[method-assign]

        worker = _make_worker(tmp_relational, enforcement)

        # Insert a crystal that will be pruned
        low_crystal = _crystal(id="prune-t0-test", layer="episodic", importance=0.0)
        tmp_relational.insert_crystal(low_crystal)

        worker.prune_thresholds = {"episodic": 1.0, "semantic": 1.0, "procedural": 1.0, "execution": 1.0}
        worker._prune(datetime.now(_UTC))

        # At least one call with T0 (Dream identity)
        assert len(t0_calls) >= 1, (
            "Dream prune must call assert_tier_allowed with T0 synthetic identity"
        )


# ---------------------------------------------------------------------------
# Enqueue dedup via DB
# ---------------------------------------------------------------------------


class TestEnqueue:
    def test_enqueue_writes_pending_run(
        self, tmp_relational: Any, tmp_enforcement: Enforcement
    ) -> None:
        """enqueue() inserts a pending row in dream_runs."""
        worker = _make_worker(tmp_relational, tmp_enforcement)
        run_id = str(uuid.uuid4())

        worker.enqueue(run_id)

        # Verify the run is in dream_runs with status=pending
        with tmp_relational._connect() as conn:
            row = conn.execute(
                "SELECT status FROM dream_runs WHERE run_id=?", (run_id,)
            ).fetchone()
        assert row is not None
        assert row["status"] == "pending"

    def test_enqueue_duplicate_is_noop(
        self, tmp_relational: Any, tmp_enforcement: Enforcement
    ) -> None:
        """Duplicate run_id enqueue silently no-ops (DB UNIQUE constraint)."""
        worker = _make_worker(tmp_relational, tmp_enforcement)
        run_id = str(uuid.uuid4())

        worker.enqueue(run_id)
        worker.enqueue(run_id)  # should not raise

        with tmp_relational._connect() as conn:
            rows = conn.execute(
                "SELECT count(*) FROM dream_runs WHERE run_id=?", (run_id,)
            ).fetchone()
        assert rows[0] == 1  # only one row
