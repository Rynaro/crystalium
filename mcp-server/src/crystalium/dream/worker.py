"""Dream worker — orient → gather → consolidate → prune cycle.

Implements the async Dream consolidation loop described in MISSION §78-83
and FORGE D3 / D9 decisions.

P0 anchors:
  P0-5  : never hard-delete; prune sets status='deprecated'.
  P0-6  : min-tier propagation — consolidated crystal takes MIN(input tiers).
  P0-10 : Dream is async, off hot path. Worker runs only via enqueue/run_pending,
           never inline in an MCP request context.

Dream's identity:
  Dream runs as synthetic T0 (service identity, per OQ-2 acknowledgment,
  spec.yaml §OQ-2). This grants Dream the right to call gate.propose_semantic()
  and mark records deprecated via _prune(). Every such call passes
  enforcement.assert_tier_allowed(..., tier=T0, ...) so the chokepoint is
  exercised and telemetry is emitted even for internal operations.

Phases:
  orient     — survey recent activity (episodic commits, recall patterns).
  gather     — fetch candidate clusters: same-topic, same-author, superseded.
  consolidate— propose Semantic upserts via gate.propose_semantic() (NEVER direct write).
  prune      — mark below-threshold records as deprecated; audit every prune.

Per-layer prune thresholds (configurable via Config; defaults from spec):
  episodic   0.15
  semantic   0.10
  procedural 0.05
  execution  0.20

Source: FORGE D3, D9; spec.yaml §dream/worker.py, §P0-5/P0-6/P0-10; OQ-2.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, TYPE_CHECKING

import structlog

from crystalium.enforcement import Enforcement
from crystalium.telemetry import now_ms, record_call
from crystalium.trust import Tier

if TYPE_CHECKING:
    from crystalium.gate import PromotionGate
    from crystalium.storage.relational import RelationalStore
    from crystalium.storage.vector import VectorStore
    from crystalium.storage.graph import GraphStore

log = structlog.get_logger("crystalium.dream.worker")

# Synthetic T0 identity for Dream (OQ-2 acknowledged, ON by default)
_DREAM_TIER: Tier = Tier.T0

# Default per-layer prune importance thresholds (spec.yaml §dream/worker.py)
_DEFAULT_PRUNE_THRESHOLDS: dict[str, float] = {
    "episodic": 0.15,
    "semantic": 0.10,
    "procedural": 0.05,
    "execution": 0.20,
}

# Maximum tokens per phase summary written to dream_runs.summary
_PHASE_TOKEN_CAP = 500


class DreamWorker:
    """Dream consolidation worker.

    Args:
        relational:    RelationalStore (reads + marks deprecated; dream_runs table).
        vector_store:  VectorStore (dense recall for cluster formation).
        graph_store:   GraphStore (neighbor_expand for cluster formation).
        enforcement:   Enforcement instance (tier checks for T0 Dream identity).
        gate:          PromotionGate (propose_semantic path; NEVER direct write).
        importance_fn: Callable(record, now) → float from importance.py.
        prune_thresholds: Optional override dict for per-layer thresholds.
    """

    def __init__(
        self,
        relational: "RelationalStore",
        vector_store: "VectorStore",
        graph_store: "GraphStore",
        enforcement: Enforcement,
        gate: "PromotionGate",
        importance_fn: Callable[..., float],
        prune_thresholds: dict[str, float] | None = None,
        persist_dynamics: bool = False,
        replay_evb: bool = False,
        interleave: bool = False,
        interleave_ratio: float = 0.5,
        stc: bool = False,
        stc_threshold: float = 0.5,
        stc_window_s: int = 3600,
        forgetting_fsrs: bool = False,
        r_floor: float = 0.7,
        resurface_floor: float = 0.85,
        evb_percentile: float = 0.5,
        fsrs_initial_stability: float = 2.0,
        fsrs_boost_factor: float = 1.5,
        fsrs_lapse_stability: float = 0.5,
        drift_detect: bool = False,
        drift_tau_lo: float = 0.80,
        drift_tau_hi: float = 0.97,
        on_run_complete: Callable[[str], None] | None = None,
    ) -> None:
        self.relational = relational
        # Battle-test fix (CRITICAL): completion callback used to release the
        # scheduler's in-memory pending slot after each run. Without it, in server
        # mode the slot is never freed and the G8 coalescing gate silently swallows
        # every trigger after the first. Wired in server._build_components to
        # DreamScheduler.record_dream_completed. None in the one-shot CLI / unit tests.
        self.on_run_complete = on_run_complete
        self.vector_store = vector_store
        self.graph_store = graph_store
        self.enforcement = enforcement
        self.gate = gate
        self.importance_fn = importance_fn
        self.prune_thresholds = prune_thresholds or dict(_DEFAULT_PRUNE_THRESHOLDS)
        # W2 (D2): when True, importance_fn is evb_score and the prune loop writes
        # the recomputed value back to memory_dynamics.evb (persist-only — no
        # net-new replay ranking). Set from config.evb_enabled at build time.
        self.persist_dynamics = persist_dynamics
        # W3 Dream-intelligence flags (each default OFF -> byte-identical chronological
        # behaviour; the test helper _make_worker omits them). Set from Config in
        # server._build_components.
        self.replay_evb = replay_evb
        self.interleave = interleave
        self.interleave_ratio = interleave_ratio
        self.stc = stc
        self.stc_threshold = stc_threshold
        self.stc_window_s = stc_window_s
        # W4 forgetting faculty (default OFF -> legacy importance-threshold prune).
        self.forgetting_fsrs = forgetting_fsrs
        self.r_floor = r_floor
        self.resurface_floor = resurface_floor
        self.evb_percentile = evb_percentile
        self.fsrs_initial_stability = fsrs_initial_stability
        self.fsrs_boost_factor = fsrs_boost_factor
        self.fsrs_lapse_stability = fsrs_lapse_stability
        # W6 belief-drift detection (A-MemGuard-style; default OFF). A flag-gated
        # phase between consolidate and prune flags a lower-trust semantic fact that
        # is same-subject-but-divergent (cosine in [tau_lo, tau_hi)) from a
        # higher-trust prior. Detect-and-flag only (no remediation).
        self.drift_detect = drift_detect
        self.drift_tau_lo = drift_tau_lo
        self.drift_tau_hi = drift_tau_hi

        # Battle-test fix (HIGH): value-aware forgetting needs the EVB signal. With
        # forgetting_fsrs ON but persist_dynamics OFF (evb_enabled=False), evb is
        # never written, so the value gate can never fire and the prune phase evicts
        # nothing. Surface this incoherent config loudly instead of degrading silently.
        if self.forgetting_fsrs and not self.persist_dynamics:
            log.warning(
                "forgetting_value_signal_unavailable",
                advice=(
                    "forgetting_fsrs is enabled but evb_enabled is off, so EVB is "
                    "never persisted. Value-aware eviction will keep all crystals "
                    "(fail-safe: never evicts on age alone). Enable evb_enabled to "
                    "activate value-aware forgetting."
                ),
            )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def enqueue(self, dream_run_id: str) -> None:
        """Write a pending dream run to the dream_runs table.

        The UNIQUE constraint on run_id provides DB-level dedup (G8 second layer).
        Silently no-ops on IntegrityError (duplicate run_id).

        Args:
            dream_run_id: Unique identifier for this dream run (UUID4).
        """
        import sqlite3

        try:
            with self.relational._connect() as conn:
                # Ensure dream_runs table exists (created here lazily)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS dream_runs (
                        run_id      TEXT PRIMARY KEY,
                        status      TEXT NOT NULL DEFAULT 'pending',
                        enqueued_at TEXT NOT NULL,
                        started_at  TEXT,
                        finished_at TEXT,
                        summary     TEXT
                    )
                """)
                conn.execute(
                    "INSERT INTO dream_runs (run_id, status, enqueued_at) VALUES (?, 'pending', ?)",
                    (dream_run_id, datetime.now(timezone.utc).isoformat()),
                )
                conn.commit()
            log.info("dream_run_enqueued", run_id=dream_run_id)
        except sqlite3.IntegrityError:
            log.debug("dream_run_duplicate_skipped", run_id=dream_run_id)
        except Exception as exc:
            log.warning("dream_run_enqueue_error", run_id=dream_run_id, error=str(exc))
            raise

    def run_pending(self) -> None:
        """Drain the pending dream_runs queue and execute each run.

        For each pending run: mark started → orient → gather → consolidate →
        prune → write summary → mark finished.

        Token budget: each phase produces a summary capped at _PHASE_TOKEN_CAP
        words (heuristic; not tiktoken — Dream summaries are operator-facing text,
        not fed back into the LLM context).
        """
        pending = self._fetch_pending_runs()
        if not pending:
            log.debug("dream_no_pending_runs")
            return

        for run_id in pending:
            self._execute_run(run_id)

    # ------------------------------------------------------------------
    # Run execution
    # ------------------------------------------------------------------

    def _execute_run(self, run_id: str) -> None:
        """Execute one Dream run (orient → gather → consolidate → prune)."""
        t0 = now_ms()
        now = datetime.now(timezone.utc)

        try:
            self._mark_run_started(run_id, now)

            # Phase 1: Orient
            orient_summary = self._orient(now)

            # Phase 2: Gather clusters
            clusters = self._gather(now)

            # Phase 3: Consolidate (propose Semantic upserts via gate)
            consolidate_summary = ""
            proposals = 0
            for cluster in clusters:
                result = self._consolidate(cluster, now)
                if result:
                    proposals += 1
            consolidate_summary = f"Proposed {proposals} Semantic upsert(s)."

            # Phase 3b: Spaced re-surfacing (W4) — boost stability of valuable,
            # aging crystals BEFORE retrievability crosses the floor. Runs only
            # under forgetting_fsrs, after consolidate and before prune.
            if self.forgetting_fsrs:
                self._resurface(now)

            # Phase 3c: Belief-drift detection (W6) — scan active semantic facts for
            # silent divergence from a higher-trust prior. Flag-gated; runs after
            # consolidate, before prune; behind the chokepoint (read + ledger-append
            # only, never a mutation tool).
            if self.drift_detect:
                self._check_drift(now)

            # Phase 4: Prune
            prune_summary = self._prune(now)

            # Build run summary (≤ _PHASE_TOKEN_CAP words total)
            summary_parts = [
                f"orient: {_truncate(orient_summary, 100)}",
                f"consolidate: {_truncate(consolidate_summary, 100)}",
                f"prune: {_truncate(prune_summary, 100)}",
            ]
            full_summary = " | ".join(summary_parts)

            self._mark_run_finished(run_id, now, summary=full_summary)
            log.info(
                "dream_run_finished",
                run_id=run_id,
                proposals=proposals,
                latency_ms=now_ms() - t0,
            )

        except Exception as exc:
            log.warning(
                "dream_run_error",
                run_id=run_id,
                error=str(exc),
                latency_ms=now_ms() - t0,
            )
            self._mark_run_finished(run_id, datetime.now(timezone.utc), summary=f"ERROR: {exc}")

        finally:
            # Release the scheduler's in-memory pending slot regardless of outcome,
            # so the next trigger can enqueue. Best-effort: a callback failure must
            # not mask the run result.
            if self.on_run_complete is not None:
                try:
                    self.on_run_complete(run_id)
                except Exception as cb_exc:  # noqa: BLE001
                    log.warning("dream_on_run_complete_error", run_id=run_id, error=str(cb_exc))

    # ------------------------------------------------------------------
    # Phase: orient
    # ------------------------------------------------------------------

    def _orient(self, now: datetime) -> str:
        """Survey recent activity — episodic commits + recall frequencies.

        Returns a short summary string for the run log.
        """
        try:
            with self.relational._connect() as conn:
                # Recent episodic commits (last 24 h). Battle-test fix: the prior
                # `cutoff.replace(hour=cutoff.hour - min(cutoff.hour,24))` always
                # collapsed to 00:00 today (hour-hour=0); use timedelta so it is a
                # true rolling 24h window that also crosses the date boundary.
                cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
                rows = conn.execute(
                    "SELECT count(*) FROM crystals WHERE layer='episodic' AND updated_at > ?",
                    (cutoff.isoformat(),),
                ).fetchone()
                recent_episodic = rows[0] if rows else 0

                # Total recall calls from telemetry
                recall_rows = conn.execute(
                    "SELECT count(*) FROM tool_calls WHERE tool='crystalium.recall'"
                ).fetchone()
                total_recalls = recall_rows[0] if recall_rows else 0

            return f"recent_episodic={recent_episodic}, total_recalls={total_recalls}"
        except Exception as exc:
            log.warning("dream_orient_error", error=str(exc))
            return f"orient_error={exc}"

    # ------------------------------------------------------------------
    # Phase: gather
    # ------------------------------------------------------------------

    def _interleave_semantic(self, cluster: list[dict[str, Any]], now: datetime) -> None:
        """W3 CLS interleave: mix a sampled ratio of existing semantic facts into
        *cluster* in place. Deterministic top-N by (created_at, id) — no RNG — so
        the bench A/B is reproducible. No-op when the flag is off or no semantics.
        """
        if not self.interleave or not cluster:
            return
        n = round(self.interleave_ratio * len(cluster))
        if n <= 0:
            return
        try:
            with self.relational._connect() as conn:
                rows = conn.execute(
                    "SELECT id FROM crystals WHERE layer='semantic' AND status='active' "
                    "ORDER BY created_at, id LIMIT ?",
                    (n,),
                ).fetchall()
            for r in rows:
                rec = self.relational.get_crystal(r[0])
                if rec:
                    cluster.append(rec)
        except Exception as exc:
            log.debug("interleave_skipped", error=str(exc))

    @staticmethod
    def _parse_ts(raw: Any) -> datetime | None:
        if isinstance(raw, str):
            try:
                dt = datetime.fromisoformat(raw)
                return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                return None
        return None

    def _stc_k_override(self, cluster: list[dict[str, Any]], now: datetime) -> int | None:
        """W3 STC: if a salient episode (EVB >= stc_threshold) has another episode
        within stc_window_s, lower the corroboration bar by 1 (floored at 1) for
        this consolidation. Returns None (baseline) when off / no capture.
        """
        if not self.stc or not cluster:
            return None
        episodes = [
            (rec.get("id"), self._parse_ts(rec.get("created_at")), self._evb(rec, now))
            for rec in cluster
            if rec.get("layer") == "episodic"
        ]
        episodes = [(i, t, e) for (i, t, e) in episodes if t is not None]
        salient = [(i, t) for (i, t, e) in episodes if e >= self.stc_threshold]
        for si, st in salient:
            for ei, et, _ in episodes:
                if ei != si and abs((et - st).total_seconds()) <= self.stc_window_s:
                    base_k = getattr(getattr(self.gate, "config", None), "k_corroboration", 3)
                    return max(1, int(base_k) - 1)
        return None

    def _evb(self, rec: dict[str, Any], now: datetime) -> float:
        """EVB score for a gathered crystal row (W3 replay/STC ordering).

        Uses evb_score directly (default proxy weights) so 'dream.replay=evb'
        orders by EVB regardless of evb_enabled (which only swaps the eviction
        scorer). Malformed utility -> 0.0 so any sort stays total/deterministic.
        """
        from crystalium.evb import evb_score

        util = rec.get("utility") or {}
        if isinstance(util, str):
            try:
                util = json.loads(util)
            except (ValueError, TypeError):
                util = {}
        try:
            return float(evb_score(_UtilityStub(util, now), now=now))
        except Exception:
            return 0.0

    def _resurface(self, now: datetime) -> int:
        """W4 spaced re-surfacing: boost stability of valuable, aging crystals
        (r_floor < R < resurface_floor AND EVB above the percentile) before they
        fade. Never deprecates; protected/un-aged/low-value are skipped. Returns
        the number boosted.
        """
        from crystalium import fsrs as _fsrs

        cutoff = self._evb_percentile_cutoff()
        boosted = 0
        try:
            with self.relational._connect() as conn:
                rows = conn.execute(
                    "SELECT id, utility, memory_dynamics, protected FROM crystals "
                    "WHERE status='active'"
                ).fetchall()
        except Exception as exc:
            log.debug("resurface_skipped", error=str(exc))
            return 0
        for row in rows:
            cid = row[0]
            if bool(row[3]):  # protected — no need to re-surface; never fades
                continue
            utility = json.loads(row[1]) if isinstance(row[1], str) else (row[1] or {})
            md = json.loads(row[2]) if isinstance(row[2], str) else (row[2] or {})
            evb = md.get("evb")
            # high-value only: above the percentile cutoff (skip if no value signal)
            if cutoff is None or evb is None or evb < cutoff:
                continue
            eff_s = md.get("stability") or self.fsrs_initial_stability
            elapsed = _fsrs.elapsed_days(_UtilityStub(utility, now).last_access, now)
            r = _fsrs.retrievability(eff_s, elapsed)
            if self.r_floor < r < self.resurface_floor:
                self.relational.update_dynamics(cid, {
                    "stability": _fsrs.boost_stability(eff_s, factor=self.fsrs_boost_factor),
                    "retrievability": r,
                })
                boosted += 1
        if boosted:
            log.info("dream_resurface", boosted=boosted)
        return boosted

    def _check_drift(self, now: datetime) -> int:
        """W6 belief-drift: flag active semantic facts that silently diverge from a
        higher-trust prior on the same subject.

        Predicate (similarity-band + trust-gap): for a candidate C, find a prior P
        (same project) with cosine(C,P) in [drift_tau_lo, drift_tau_hi) — same
        subject but NOT a near-duplicate — where C is strictly LESS trusted than P
        (tier int greater). Flag C: append a drift_audit row + set
        memory_dynamics.drift_flagged=true. Detect-and-flag ONLY; no supersede /
        deprecate / tier change. Null/SKIP_SLOW embedder -> graceful no-op.
        Returns the number of crystals newly flagged.
        """
        from crystalium.trust import Tier

        # Embedder availability guard (null stub / SKIP_SLOW -> empty vectors).
        try:
            if not self.vector_store.embed("probe"):
                log.info("drift_skipped", reason="no_embedder")
                return 0
        except Exception:
            log.info("drift_skipped", reason="no_embedder")
            return 0

        # Active semantic crystals to scan.
        try:
            with self.relational._connect() as conn:
                rows = conn.execute(
                    "SELECT id, summary, trust_tier, scope FROM crystals "
                    "WHERE layer='semantic' AND status='active'"
                ).fetchall()
        except Exception as exc:
            log.debug("drift_skipped", error=str(exc))
            return 0

        # Don't re-flag the same (candidate, prior) pair across runs.
        seen_pairs = {
            (r["crystal_id"], r["prior_id"]) for r in self.relational.list_drift_audit()
        }

        def _tier_int(t: str | None) -> int:
            try:
                return int(Tier.from_str(t))
            except Exception:
                return int(Tier.T3)  # fail-closed: unparseable = least trusted

        flagged = 0
        for row in rows:
            cid, summary = row[0], row[1] or ""
            cand_tier = row[2]
            cand_scope = json.loads(row[3]) if isinstance(row[3], str) else (row[3] or {})
            cand_project = cand_scope.get("project") if isinstance(cand_scope, dict) else None
            if not summary or not cand_project:
                continue
            try:
                vec = self.vector_store.embed(summary)
                if not vec:
                    continue
                hits = self.vector_store.dense_search(query_vec=vec, layer_filter="semantic", k=5)
            except Exception:
                continue
            for hit in hits:
                pid = hit.get("id")
                if not pid or pid == cid or (cid, pid) in seen_pairs:
                    continue
                dist = hit.get("_distance")
                if dist is None:
                    continue
                sim = 1.0 - float(dist)
                if not (self.drift_tau_lo <= sim < self.drift_tau_hi):
                    continue
                prior = self.relational.get_crystal(pid)
                if not prior:
                    continue
                prior_scope = prior.get("scope") or {}
                if (prior_scope.get("project") if isinstance(prior_scope, dict) else None) != cand_project:
                    continue
                prior_tier = prior.get("trust_tier")
                # candidate must be STRICTLY less trusted than the prior (higher int)
                if _tier_int(cand_tier) <= _tier_int(prior_tier):
                    continue
                self.relational.record_drift(cid, pid, sim, cand_tier, prior_tier, now=now)
                self.relational.update_dynamics(cid, {"drift_flagged": True})
                seen_pairs.add((cid, pid))
                flagged += 1
                break  # one flag per candidate per run
        if flagged:
            log.info("dream_drift_flagged", count=flagged)
        return flagged

    def _evb_percentile_cutoff(self) -> float | None:
        """EVB at self.evb_percentile over the active set (None if no evb data)."""
        try:
            evbs = sorted(
                c["evb"]
                for c in self.relational.list_crystals_with_dynamics()
                if c.get("status") == "active" and c.get("evb") is not None
            )
        except Exception:
            return None
        if not evbs:
            return None
        idx = min(len(evbs) - 1, int(self.evb_percentile * len(evbs)))
        return evbs[idx]

    def _gather(self, now: datetime) -> list[list[dict[str, Any]]]:
        """Fetch candidate clusters for consolidation.

        Cluster types:
          1. Same-topic episodic records (graph-expand on shared neighbours).
          2. Superseded records (flagged via temporal.superseded_by != NULL).

        Returns list of clusters (each cluster = list of crystal dicts).
        Token-bounded: fetch at most 50 records total across all clusters.
        """
        clusters: list[list[dict[str, Any]]] = []

        try:
            # Cluster type 1: Episodic records with shared graph neighbours
            if self.replay_evb:
                # W3 prioritized replay: score a larger candidate pool by EVB and
                # take the highest (reverse replay of high-Gain episodes). Ties
                # broken by (created_at, id) for a total, deterministic order.
                with self.relational._connect() as conn:
                    pool = conn.execute(
                        "SELECT id, utility, created_at FROM crystals "
                        "WHERE layer='episodic' AND status='active' LIMIT 50"
                    ).fetchall()
                scored = [
                    (-self._evb({"utility": r[1]}, now), r[2] or "", r[0])
                    for r in pool
                ]
                scored.sort()
                seed_ids = [t[2] for t in scored[:20]]
            else:
                with self.relational._connect() as conn:
                    rows = conn.execute(
                        "SELECT id FROM crystals WHERE layer='episodic' AND status='active' LIMIT 20"
                    ).fetchall()
                seed_ids = [r[0] for r in rows]

            if seed_ids:
                try:
                    neighbour_ids = self.graph_store.neighbor_expand(seed_ids, depth=1)
                    # Group seeds + neighbours as one cluster if non-empty
                    if neighbour_ids:
                        cluster_ids = list(set(seed_ids[:5]) | set(list(neighbour_ids)[:5]))
                        cluster = []
                        for cid in cluster_ids[:10]:
                            rec = self.relational.get_crystal(cid)
                            if rec:
                                cluster.append(rec)
                        if cluster:
                            self._interleave_semantic(cluster, now)
                            clusters.append(cluster)
                except Exception as exc:
                    log.debug("gather_graph_expand_skipped", error=str(exc))

            # Cluster type 2: Records with superseded_by set (contradictions/updates)
            with self.relational._connect() as conn:
                rows = conn.execute(
                    """SELECT id FROM crystals
                       WHERE json_extract(temporal, '$.superseded_by') IS NOT NULL
                         AND status = 'active'
                       LIMIT 10"""
                ).fetchall()
            if rows:
                superseded_cluster = []
                for row in rows:
                    rec = self.relational.get_crystal(row[0])
                    if rec:
                        superseded_cluster.append(rec)
                if superseded_cluster:
                    clusters.append(superseded_cluster)

        except Exception as exc:
            log.warning("dream_gather_error", error=str(exc))

        return clusters

    # ------------------------------------------------------------------
    # Phase: consolidate
    # ------------------------------------------------------------------

    def _consolidate(
        self, cluster: list[dict[str, Any]], now: datetime
    ) -> dict[str, Any] | None:
        """Propose a Semantic upsert for *cluster* via gate.propose_semantic().

        Critical invariants:
          1. Dream NEVER writes directly to Semantic — always via gate.propose_semantic().
          2. Min-tier propagation (D7, P0-6): consolidated crystal takes MIN trust tier.
          3. Dream runs as synthetic T0 (OQ-2 acknowledged, ON by default).
          4. enforcement.assert_tier_allowed is called with tier=T0 before the gate call.

        Args:
            cluster: List of crystal dicts forming a related group.
            now:     Reference time for the consolidation run.

        Returns:
            Proposed crystal dict if proposal was made; None if cluster was empty
            or all records were too low-trust for Semantic admission.
        """
        if not cluster:
            return None

        # 1. Compute consolidated trust tier: MIN of all input tiers (D7 / P0-6)
        from crystalium.trust import Tier as TierEnum, min_tier, LAYER_CEILING

        input_tiers = []
        for rec in cluster:
            tier_str = rec.get("trust_tier", "T1")
            try:
                input_tiers.append(TierEnum.from_str(tier_str))
            except ValueError:
                input_tiers.append(TierEnum.T3)

        if not input_tiers:
            return None

        consolidated_tier = min_tier(input_tiers)

        # G4 anti-laundering: a cluster whose min-trust consolidated tier exceeds the
        # Semantic ceiling (T1) must NOT become a Semantic fact. Route through the
        # named, tested enforcement guard (single source of truth) rather than an
        # inline `>` reimplementation. Dream's policy on violation is to SKIP the
        # cluster (not crash the run), so the raise is converted to a skip here.
        from crystalium.enforcement import TierCeilingViolation

        try:
            self.enforcement.assert_tier_within_layer_ceiling(consolidated_tier, "semantic")
        except TierCeilingViolation:
            log.debug(
                "consolidate_skip_tier",
                consolidated_tier=str(consolidated_tier),
                ceiling=str(LAYER_CEILING.get("semantic", TierEnum.T1)),
            )
            return None

        # 2. Assert Dream's T0 identity via enforcement chokepoint (OQ-2)
        self.enforcement.assert_tier_allowed(
            "dream.consolidate", "semantic", _DREAM_TIER, "commit"
        )

        # 3. Build consolidated crystal representation
        summaries = [rec.get("summary", "") for rec in cluster]
        consolidated_summary = "; ".join(s for s in summaries if s)[:512]

        # W3 OBJ-4 abstraction metric (log-only): a promotion that doesn't compress
        # isn't generalization. Ratio ~1 -> flag. Summary-length proxy (no blob dep).
        source_len = sum(len(s) for s in summaries)
        compression_ratio = (len(consolidated_summary) / source_len) if source_len else None
        log.info(
            "dream_compression",
            compression_ratio=compression_ratio,
            no_generalization=(compression_ratio is not None and compression_ratio >= 0.95),
            cluster_size=len(cluster),
        )

        consolidated_crystal: dict[str, Any] = {
            "id": _make_crystal_id(),
            "layer": "semantic",
            "summary": consolidated_summary,
            "provenance": {
                "source": "verified_agent",
                "author_agent": "dream.consolidator",
                "task_id": None,
                "created_at": now.isoformat(),
            },
            "trust_tier": str(consolidated_tier),
            "validation_state": "unverified",
            "scope": cluster[0].get("scope", {"project": "default"}),
            "temporal": {
                "t_valid_from": now.isoformat(),
                "t_valid_to": None,
                "superseded_by": None,
            },
            "utility": {
                "access_count": 0,
                "last_access": now.isoformat(),
                "outcome_success_score": None,
                "importance": 0.0,
                "novelty_at_write": 0.3,
            },
            "status": "active",
        }

        # 4. Propose via gate (NEVER write directly)
        from crystalium.gate import CrystalRef

        witnesses = [
            CrystalRef(
                id=rec["id"],
                author_agent=rec.get("provenance", {}).get("author_agent") if isinstance(rec.get("provenance"), dict) else None,
                source=(rec.get("provenance") or {}).get("source", "unverified_agent") if isinstance(rec.get("provenance"), dict) else "unverified_agent",
                trust_tier=TierEnum.from_str(rec.get("trust_tier", "T1")),
            )
            for rec in cluster
        ]

        # W3 STC: a salient nearby episode lowers the corroboration bar (k_override).
        # Chokepoint-preserving — the gate still counts witnesses + human-confirm.
        k_override = self._stc_k_override(cluster, now)

        try:
            result = self.gate.propose_semantic(
                crystal=consolidated_crystal,
                witnesses=witnesses,
                caller_tier=_DREAM_TIER,
                force=False,
                k_override=k_override,
            )
            log.info(
                "dream_consolidate_proposed",
                decision=result.decision,
                cluster_size=len(cluster),
                consolidated_tier=str(consolidated_tier),
                stc_k_override=k_override,
            )
            return consolidated_crystal if result.decision == "admit" else None

        except Exception as exc:
            log.warning("dream_consolidate_gate_error", error=str(exc))
            return None

    # ------------------------------------------------------------------
    # Phase: prune
    # ------------------------------------------------------------------

    def _prune(self, now: datetime) -> str:
        """Mark below-threshold records as deprecated.

        Rules:
          - For each layer, compute importance_score(record, now).
          - Records with score < per-layer threshold → status = 'deprecated'.
          - NEVER hard-delete (P0-5).
          - Every prune is audited via telemetry.record_call(tool="dream.prune", ...).
          - assert_tier_allowed with tier=T0 before each deprecation (OQ-2).

        Returns:
            Short summary string for the run log.
        """
        deprecated_count = 0
        layers = list(_DEFAULT_PRUNE_THRESHOLDS.keys())

        # W4: when forgetting_fsrs, eviction is value-aware — compute the EVB
        # percentile cutoff once over the whole active set (never age alone).
        evb_cutoff = self._evb_percentile_cutoff() if self.forgetting_fsrs else None

        for layer in layers:
            threshold = self.prune_thresholds.get(layer, _DEFAULT_PRUNE_THRESHOLDS[layer])
            t0_prune = now_ms()

            try:
                # Fetch active records for this layer (W4 also reads memory_dynamics
                # + protected so eviction can see R/stability and the protected flag).
                with self.relational._connect() as conn:
                    rows = conn.execute(
                        "SELECT id, utility, status, memory_dynamics, protected "
                        "FROM crystals WHERE layer=? AND status='active'",
                        (layer,),
                    ).fetchall()

                to_deprecate = []
                for row in rows:
                    cid = row[0]
                    utility_raw = row[1]
                    utility = json.loads(utility_raw) if isinstance(utility_raw, str) else utility_raw or {}
                    md_raw = row[3]
                    md = json.loads(md_raw) if isinstance(md_raw, str) else (md_raw or {})
                    protected = bool(row[4])

                    # W4 Ricoeur-protected class: never evicted (any mode).
                    if protected:
                        continue

                    rec_stub = _UtilityStub(utility, now)
                    score = self.importance_fn(rec_stub, now=now)

                    # W2 (D2): when EVB is the scorer, persist the recomputed value
                    # to memory_dynamics.evb so the composer + later waves read a
                    # fresh, single-source-of-truth value (persist-only).
                    if self.persist_dynamics:
                        self.relational.update_dynamics(cid, {"evb": score})

                    if self.forgetting_fsrs:
                        # Value-aware: evict ONLY when retrievability has decayed
                        # below r_floor AND value (EVB) is below the percentile —
                        # never on age/threshold alone.
                        from crystalium import fsrs as _fsrs

                        eff_s = md.get("stability") or self.fsrs_initial_stability
                        elapsed = _fsrs.elapsed_days(rec_stub.last_access, now)
                        r = _fsrs.retrievability(eff_s, elapsed)
                        evb = md.get("evb")
                        # Battle-test fix (HIGH): the value gate requires a real value
                        # signal. When evb is unavailable (None) or no percentile cutoff
                        # exists — which is exactly the state when evb_enabled=False, so
                        # evb is never persisted — the old code treated that as
                        # "below value", silently collapsing eviction to R<r_floor ALONE
                        # (age-only), violating this phase's "never on age alone"
                        # guarantee. Fail safe: without a value signal we do NOT evict.
                        value_below = (
                            evb is not None and evb_cutoff is not None and evb < evb_cutoff
                        )
                        if r < self.r_floor and value_below:
                            to_deprecate.append(cid)
                    elif score < threshold:
                        to_deprecate.append(cid)

                for cid in to_deprecate:
                    # Assert Dream's T0 identity before each deprecation
                    self.enforcement.assert_tier_allowed(
                        "dream.prune", layer, _DREAM_TIER, "commit"
                    )

                    with self.relational._connect() as conn:
                        conn.execute(
                            "UPDATE crystals SET status='deprecated', updated_at=? WHERE id=?",
                            (datetime.now(timezone.utc).isoformat(), cid),
                        )
                        conn.commit()

                    deprecated_count += 1
                    log.info("dream_prune_deprecated", crystal_id=cid, layer=layer, score_below=threshold)

                    # Audit telemetry per prune (P0-7 extension for Dream)
                    record_call(
                        tool="dream.prune",
                        layer=layer,
                        tier=str(_DREAM_TIER),
                        op="prune",
                        result="ok",
                        latency_ms=now_ms() - t0_prune,
                        extra={"crystal_id": cid, "threshold": threshold},
                    )

            except Exception as exc:
                log.warning("dream_prune_error", layer=layer, error=str(exc))

        return f"deprecated={deprecated_count} records across {len(layers)} layers"

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _fetch_pending_runs(self) -> list[str]:
        """Fetch run_ids with status='pending' from dream_runs table."""
        try:
            with self.relational._connect() as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS dream_runs (
                        run_id      TEXT PRIMARY KEY,
                        status      TEXT NOT NULL DEFAULT 'pending',
                        enqueued_at TEXT NOT NULL,
                        started_at  TEXT,
                        finished_at TEXT,
                        summary     TEXT
                    )
                """)
                conn.commit()
                rows = conn.execute(
                    "SELECT run_id FROM dream_runs WHERE status='pending'"
                ).fetchall()
            return [r[0] for r in rows]
        except Exception as exc:
            log.warning("dream_fetch_pending_error", error=str(exc))
            return []

    def _mark_run_started(self, run_id: str, now: datetime) -> None:
        try:
            with self.relational._connect() as conn:
                conn.execute(
                    "UPDATE dream_runs SET status='running', started_at=? WHERE run_id=?",
                    (now.isoformat(), run_id),
                )
                conn.commit()
        except Exception as exc:
            log.warning("dream_mark_started_error", run_id=run_id, error=str(exc))

    def _mark_run_finished(
        self, run_id: str, now: datetime, summary: str = ""
    ) -> None:
        try:
            with self.relational._connect() as conn:
                conn.execute(
                    "UPDATE dream_runs SET status='finished', finished_at=?, summary=? WHERE run_id=?",
                    (now.isoformat(), summary[:_PHASE_TOKEN_CAP * 5], run_id),
                )
                conn.commit()
        except Exception as exc:
            log.warning("dream_mark_finished_error", run_id=run_id, error=str(exc))


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


class _UtilityStub:
    """Minimal MemoryRecord protocol implementation from a utility dict.

    Used by _prune() to call importance_fn without loading full Crystal models.
    """

    __slots__ = ("access_count", "last_access", "outcome_success", "novelty_at_write")

    def __init__(self, utility: dict[str, Any], now: datetime) -> None:
        self.access_count: int = int(utility.get("access_count", 0))

        last_access_raw = utility.get("last_access")
        if isinstance(last_access_raw, str):
            try:
                la = datetime.fromisoformat(last_access_raw)
                if la.tzinfo is None:
                    la = la.replace(tzinfo=timezone.utc)
                self.last_access = la
            except (ValueError, TypeError):
                self.last_access = now
        else:
            self.last_access = now

        outcome_raw = utility.get("outcome_success_score")
        self.outcome_success: float | None = (
            float(outcome_raw) if outcome_raw is not None else None
        )
        self.novelty_at_write: float = float(utility.get("novelty_at_write", 0.5))


def _make_crystal_id() -> str:
    import uuid
    return str(uuid.uuid4())


def _truncate(text: str, max_words: int) -> str:
    """Truncate *text* to *max_words* words (heuristic, no tiktoken)."""
    words = text.split()
    if len(words) <= max_words:
        return text
    return " ".join(words[:max_words]) + "…"
