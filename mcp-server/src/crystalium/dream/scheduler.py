"""Dream scheduler — dual-trigger idle-poll + session_end with dedup (G8 anchor).

Implements FORGE D3 emit: two complementary trigger paths share one enqueue
function, deduped by dream_run_id so concurrent fires produce exactly one run.

Design (spec.yaml §G8, §config_defaults):
  - BackgroundScheduler (APScheduler) polls every dream_tick_s (default 60 s).
  - Poll tick fires if:
      now - last_activity >= idle_threshold_s (default 300 s)
      AND now - last_dream  >= min_dream_gap_s (default 1 800 s)
  - session_end() fast-path: called by crystalium.session_end MCP tool (W4).
  - Both paths call enqueue(dream_run_id) — no direct worker.run_pending() call.
  - Dedup: _pending_run_ids set (in-memory) + dream_runs table UNIQUE(run_id).
    Duplicate enqueue silently no-ops.

G8 test anchor:
  test_g8_dream_dedup_single_enqueue — simultaneous idle-tick + session_end
  yield exactly one enqueued run.

FINDING-004 (spec source_of_truth_attribution):
  Scheduler MUST run outside MCP request context. BackgroundScheduler runs in a
  daemon thread; never call enqueue() from inside an MCP tool handler directly.
  The MCP tool handler for session_end calls scheduler.on_session_end() which
  posts to the scheduler's job queue rather than running inline.

Source: .forge/reasoning-report.md §D3; spec.yaml §G8; config_defaults §dream_*.
"""

from __future__ import annotations

import threading
import time
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

import structlog

from crystalium.config import Config

if TYPE_CHECKING:
    from crystalium.dream.worker import DreamWorker
    from crystalium.storage.relational import RelationalStore

log = structlog.get_logger("crystalium.dream.scheduler")


class DreamScheduler:
    """Async-safe Dream scheduler with dual-trigger and dedup.

    Args:
        config:   Server-wide config (idle_threshold_s, min_dream_gap_s, dream_tick_s).
        worker:   DreamWorker instance (enqueue target).
        store:    RelationalStore (reads last_activity / last_dream from dream_state).

    Notes:
        _pending_run_ids is the in-memory dedup set; the DB dream_runs table
        carries the durable UNIQUE(run_id) constraint (written by worker.enqueue).
        Both layers are required: the in-memory set catches double-fires before
        a DB write, while the DB prevents re-runs across process restarts.
    """

    def __init__(
        self,
        config: Config,
        worker: "DreamWorker",
        store: "RelationalStore",
    ) -> None:
        self.config = config
        self.worker = worker
        self.store = store

        # In-memory dedup set — keyed by dream_run_id
        self._pending_run_ids: set[str] = set()
        self._lock = threading.Lock()

        # APScheduler background scheduler (optional dep)
        self._scheduler: Any = None
        self._started = False

        # Timestamps tracked by the scheduler (updated by W4 activity hooks)
        self._last_activity: datetime = datetime.now(timezone.utc)
        self._last_dream: datetime = datetime(2000, 1, 1, tzinfo=timezone.utc)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the background APScheduler.

        Falls back to a no-op if apscheduler is not installed (test environments
        that only need the enqueue/dedup logic can skip scheduler start).

        The APScheduler daemon thread is set non-blocking so it doesn't prevent
        process exit (daemon=True).
        """
        if self._started:
            return
        try:
            from apscheduler.schedulers.background import BackgroundScheduler  # type: ignore[import-untyped]

            self._scheduler = BackgroundScheduler(daemon=True)
            self._scheduler.add_job(
                self._tick,
                "interval",
                seconds=self.config.dream_tick_s,
                id="dream_idle_poll",
                replace_existing=True,
            )
            self._scheduler.start()
            self._started = True
            log.info(
                "dream_scheduler_started",
                tick_s=self.config.dream_tick_s,
                idle_threshold_s=self.config.idle_threshold_s,
                min_dream_gap_s=self.config.min_dream_gap_s,
            )
        except ImportError:
            log.warning(
                "apscheduler_missing",
                message=(
                    "apscheduler not installed — background Dream polling disabled. "
                    "session_end trigger and manual on_session_end() still work."
                ),
            )

    def stop(self) -> None:
        """Gracefully stop the APScheduler if running."""
        if self._scheduler is not None and self._started:
            try:
                self._scheduler.shutdown(wait=False)
            except Exception as exc:
                log.warning("scheduler_shutdown_error", error=str(exc))
            self._started = False

    # ------------------------------------------------------------------
    # Activity hooks (called by W4 tool handlers)
    # ------------------------------------------------------------------

    def record_activity(self) -> None:
        """Update last_activity timestamp (called after any commit/recall)."""
        with self._lock:
            self._last_activity = datetime.now(timezone.utc)

    def record_dream_completed(self, run_id: str) -> None:
        """Called by DreamWorker when a run finishes; updates last_dream timestamp."""
        with self._lock:
            self._last_dream = datetime.now(timezone.utc)
            self._pending_run_ids.discard(run_id)

    # ------------------------------------------------------------------
    # Trigger paths
    # ------------------------------------------------------------------

    def on_session_end(self) -> str | None:
        """Fast-path trigger: called by the crystalium.session_end MCP tool (W4).

        Immediately evaluates the dream gap condition and enqueues a run.
        Returns the dream_run_id if enqueued, None if skipped (gap not met or dedup).

        This method is intentionally synchronous and lightweight — it just enqueues;
        the actual Dream work happens in worker.run_pending() off the MCP hot path.
        """
        now = datetime.now(timezone.utc)
        with self._lock:
            last_dream = self._last_dream

        gap_ok = (now - last_dream).total_seconds() >= self.config.min_dream_gap_s
        if not gap_ok:
            log.debug(
                "session_end_skipped",
                reason="min_dream_gap not met",
                seconds_since_dream=(now - last_dream).total_seconds(),
                min_gap=self.config.min_dream_gap_s,
            )
            return None

        run_id = _make_run_id()
        return self.enqueue(run_id)

    def enqueue(self, dream_run_id: str) -> str | None:
        """Enqueue a Dream run by *dream_run_id* (shared dedup path).

        Dedup logic (G8):
          1. Check _pending_run_ids in-memory set under lock.
          2. If already present: no-op, return None.
          3. If new: add to set, call worker.enqueue(dream_run_id), return run_id.

        The DB-level UNIQUE constraint on dream_runs.run_id provides a second
        dedup layer for concurrent processes / post-restart scenarios.

        Args:
            dream_run_id: Unique identifier for this Dream run.

        Returns:
            dream_run_id if newly enqueued; None if deduplicated away.
        """
        with self._lock:
            if dream_run_id in self._pending_run_ids:
                log.debug("dream_enqueue_dedup", run_id=dream_run_id)
                return None
            self._pending_run_ids.add(dream_run_id)

        # Delegate to worker (outside lock to avoid deadlock)
        try:
            self.worker.enqueue(dream_run_id)
            log.info("dream_enqueued", run_id=dream_run_id)
        except Exception as exc:
            # If worker.enqueue fails (e.g. DB conflict), remove from in-memory set
            with self._lock:
                self._pending_run_ids.discard(dream_run_id)
            log.warning("dream_enqueue_failed", run_id=dream_run_id, error=str(exc))
            return None

        return dream_run_id

    # ------------------------------------------------------------------
    # Internal: idle-poll tick
    # ------------------------------------------------------------------

    def _tick(self) -> None:
        """APScheduler job: fire if idle threshold AND dream gap are met.

        Called every dream_tick_s seconds by the background scheduler.
        Both conditions must hold simultaneously (G8 GIVEN clause).
        """
        now = datetime.now(timezone.utc)
        with self._lock:
            last_activity = self._last_activity
            last_dream = self._last_dream

        idle_s = (now - last_activity).total_seconds()
        gap_s = (now - last_dream).total_seconds()

        if idle_s < self.config.idle_threshold_s:
            log.debug(
                "dream_tick_skip",
                reason="not_idle",
                idle_s=idle_s,
                threshold=self.config.idle_threshold_s,
            )
            return

        if gap_s < self.config.min_dream_gap_s:
            log.debug(
                "dream_tick_skip",
                reason="dream_gap_not_met",
                gap_s=gap_s,
                min_gap=self.config.min_dream_gap_s,
            )
            return

        run_id = _make_run_id()
        log.info(
            "dream_tick_fire",
            run_id=run_id,
            idle_s=idle_s,
            gap_s=gap_s,
        )
        self.enqueue(run_id)

    # ------------------------------------------------------------------
    # Test helpers
    # ------------------------------------------------------------------

    def _inject_timestamps(
        self,
        last_activity: datetime,
        last_dream: datetime,
    ) -> None:
        """Override internal timestamps for deterministic unit tests.

        Not part of the public API — used only in test_dream_scheduler.py.
        """
        with self._lock:
            self._last_activity = last_activity
            self._last_dream = last_dream


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _make_run_id() -> str:
    """Generate a unique dream run ID (UUID4).

    [UNVERIFIED] UUID7 (time-ordered) is preferred per D4 guidance but requires
    an external library not in Python 3.11 stdlib. UUID4 is used here for
    compatibility; revisit when Python 3.12+ uuid7 lands or a dep is added.
    """
    return str(uuid.uuid4())
