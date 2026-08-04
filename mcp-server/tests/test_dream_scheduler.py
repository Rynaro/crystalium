"""Tests for DreamScheduler — G8 anchor + idle threshold + dream gap.

Container-first: run via:
  docker compose run --rm crystalium pytest mcp-server/tests/test_dream_scheduler.py -v

G8 anchor:
  test_g8_dream_dedup_single_enqueue — concurrent idle-trigger + session_end
  yields exactly one enqueued run.
"""

from __future__ import annotations

import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch, call
from typing import Any

import pytest

from crystalium.config import Config
from crystalium.dream.scheduler import DreamScheduler, _make_run_id


_UTC = timezone.utc


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_config(
    idle_threshold_s: int = 300,
    min_dream_gap_s: int = 1800,
    dream_tick_s: int = 60,
) -> Config:
    """Build a minimal Config for scheduler tests."""
    cfg = Config.__new__(Config)
    cfg.transport = "stdio"
    cfg.idle_threshold_s = idle_threshold_s
    cfg.min_dream_gap_s = min_dream_gap_s
    cfg.dream_tick_s = dream_tick_s
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
    cfg.data_dir = Path("/tmp/crystalium_dream_test")
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


def _make_scheduler(
    enqueued: list[str] | None = None,
    idle_threshold_s: int = 300,
    min_dream_gap_s: int = 1800,
) -> DreamScheduler:
    """Build a DreamScheduler with a mock worker that records enqueue calls."""
    cfg = _make_config(
        idle_threshold_s=idle_threshold_s,
        min_dream_gap_s=min_dream_gap_s,
    )
    worker = MagicMock()
    store = MagicMock()

    scheduler = DreamScheduler(config=cfg, worker=worker, store=store)

    if enqueued is not None:
        # Capture enqueue calls by patching worker.enqueue to append to the list
        original_enqueue = scheduler.enqueue

        def _track_enqueue(run_id: str) -> str | None:
            result = original_enqueue(run_id)
            if result is not None:
                enqueued.append(result)
            return result

        scheduler.enqueue = _track_enqueue  # type: ignore[method-assign]

    return scheduler


# ---------------------------------------------------------------------------
# G8: Dream dedup on concurrent triggers
# ---------------------------------------------------------------------------


class TestG8DreamDedup:
    """G8 anchor: simultaneous idle-tick + session_end → exactly one enqueue."""

    def test_g8_dream_dedup_single_enqueue(self) -> None:
        """G8: Simulate idle-tick + session_end firing concurrently.

        Both triggers share the same enqueue path. Only one run_id should be
        recorded in the worker (in-memory dedup + DB UNIQUE constraint).

        This test uses the same run_id for both triggers to simulate the
        worst-case: both paths generate the same ID simultaneously.
        """
        enqueued: list[str] = []
        cfg = _make_config(idle_threshold_s=1, min_dream_gap_s=1)
        worker = MagicMock()

        # Track actual worker.enqueue calls
        actual_worker_calls: list[str] = []
        worker.enqueue.side_effect = lambda run_id: actual_worker_calls.append(run_id)

        store = MagicMock()
        scheduler = DreamScheduler(config=cfg, worker=worker, store=store)

        # Inject timestamps so both triggers fire
        old_time = datetime(2000, 1, 1, tzinfo=_UTC)
        scheduler._inject_timestamps(last_activity=old_time, last_dream=old_time)

        # Use a fixed run_id to force the collision
        fixed_run_id = "test-run-" + str(uuid.uuid4())

        # Simulate concurrent calls with the SAME run_id
        barrier = threading.Barrier(2)
        results = []

        def _call_enqueue():
            barrier.wait()  # synchronize both threads
            r = scheduler.enqueue(fixed_run_id)
            results.append(r)

        t1 = threading.Thread(target=_call_enqueue)
        t2 = threading.Thread(target=_call_enqueue)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # Exactly one enqueue should have succeeded (the other was deduplicated)
        non_none = [r for r in results if r is not None]
        assert len(non_none) == 1, (
            f"Expected exactly 1 enqueue, got {len(non_none)}: {results}"
        )
        assert worker.enqueue.call_count == 1, (
            f"worker.enqueue should be called exactly once, got {worker.enqueue.call_count}"
        )

    def test_g8_session_end_and_idle_tick_share_path(self) -> None:
        """Idle-tick and session_end mint DISTINCT run_ids; G8 coalesces the second.

        Regression for fix/dream-enqueue-race: the real trigger paths each call
        _make_run_id() independently, so the two concurrent IDs never collide.
        The G8 invariant ("at most one pending Dream run") therefore must hold on
        *any pending run*, not on run_id identity — otherwise both distinct IDs
        enqueue and two Dreams run. Until the slot is released by
        record_dream_completed(), the second trigger must coalesce away.
        """
        cfg = _make_config(idle_threshold_s=1, min_dream_gap_s=1)
        worker = MagicMock()
        call_count = [0]

        def _counting_enqueue(run_id: str) -> None:
            call_count[0] += 1

        worker.enqueue.side_effect = _counting_enqueue

        store = MagicMock()
        scheduler = DreamScheduler(config=cfg, worker=worker, store=store)

        # Inject old timestamps so conditions are met
        old_time = datetime(2000, 1, 1, tzinfo=_UTC)
        scheduler._inject_timestamps(last_activity=old_time, last_dream=old_time)

        # Fire two different run_ids (the production concurrent scenario)
        r1 = scheduler.enqueue("run-id-001")
        r2 = scheduler.enqueue("run-id-002")

        # First wins; second coalesces — exactly one pending Dream (G8)
        assert r1 == "run-id-001"
        assert r2 is None
        assert call_count[0] == 1  # only one run reaches the worker

        # Slot releases on completion → next trigger may enqueue again
        scheduler.record_dream_completed("run-id-001")
        r3 = scheduler.enqueue("run-id-003")
        assert r3 == "run-id-003"
        assert call_count[0] == 2

    def test_same_run_id_deduplicated_in_memory(self) -> None:
        """Same run_id submitted twice → only first enqueue reaches worker."""
        cfg = _make_config()
        worker = MagicMock()
        store = MagicMock()
        scheduler = DreamScheduler(config=cfg, worker=worker, store=store)

        r1 = scheduler.enqueue("dedup-test-id")
        r2 = scheduler.enqueue("dedup-test-id")

        assert r1 == "dedup-test-id"
        assert r2 is None  # deduplicated
        assert worker.enqueue.call_count == 1


# ---------------------------------------------------------------------------
# Idle threshold
# ---------------------------------------------------------------------------


class TestIdleThreshold:
    def test_idle_threshold_not_met_no_fire(self) -> None:
        """If idle time < idle_threshold_s, _tick() must not enqueue."""
        cfg = _make_config(idle_threshold_s=300, min_dream_gap_s=1800)
        worker = MagicMock()
        store = MagicMock()
        scheduler = DreamScheduler(config=cfg, worker=worker, store=store)

        # last_activity just now → not idle
        scheduler._inject_timestamps(
            last_activity=datetime.now(_UTC),
            last_dream=datetime(2000, 1, 1, tzinfo=_UTC),
        )

        scheduler._tick()
        worker.enqueue.assert_not_called()

    def test_idle_threshold_met_fires(self) -> None:
        """If idle time ≥ idle_threshold_s AND gap met, _tick() enqueues."""
        cfg = _make_config(idle_threshold_s=10, min_dream_gap_s=10)
        worker = MagicMock()
        store = MagicMock()
        scheduler = DreamScheduler(config=cfg, worker=worker, store=store)

        # last_activity long ago → idle threshold exceeded
        old_time = datetime(2000, 1, 1, tzinfo=_UTC)
        scheduler._inject_timestamps(last_activity=old_time, last_dream=old_time)

        scheduler._tick()
        assert worker.enqueue.call_count == 1


# ---------------------------------------------------------------------------
# Dream gap
# ---------------------------------------------------------------------------


class TestMinDreamGap:
    def test_min_dream_gap_not_met_no_fire(self) -> None:
        """If now - last_dream < min_dream_gap_s, _tick() must not enqueue."""
        cfg = _make_config(idle_threshold_s=10, min_dream_gap_s=3600)
        worker = MagicMock()
        store = MagicMock()
        scheduler = DreamScheduler(config=cfg, worker=worker, store=store)

        # last_dream just now → gap not met
        scheduler._inject_timestamps(
            last_activity=datetime(2000, 1, 1, tzinfo=_UTC),
            last_dream=datetime.now(_UTC),
        )

        scheduler._tick()
        worker.enqueue.assert_not_called()

    def test_session_end_skipped_when_gap_not_met(self) -> None:
        """on_session_end() returns None if min_dream_gap_s not met."""
        cfg = _make_config(min_dream_gap_s=3600)
        worker = MagicMock()
        store = MagicMock()
        scheduler = DreamScheduler(config=cfg, worker=worker, store=store)

        # last_dream just now
        scheduler._inject_timestamps(
            last_activity=datetime(2000, 1, 1, tzinfo=_UTC),
            last_dream=datetime.now(_UTC),
        )

        result = scheduler.on_session_end()
        assert result is None
        worker.enqueue.assert_not_called()

    def test_session_end_enqueues_when_gap_met(self) -> None:
        """on_session_end() enqueues when gap is met."""
        cfg = _make_config(min_dream_gap_s=10)
        worker = MagicMock()
        store = MagicMock()
        scheduler = DreamScheduler(config=cfg, worker=worker, store=store)

        # last_dream long ago
        scheduler._inject_timestamps(
            last_activity=datetime.now(_UTC),
            last_dream=datetime(2000, 1, 1, tzinfo=_UTC),
        )

        result = scheduler.on_session_end()
        assert result is not None
        worker.enqueue.assert_called_once()
