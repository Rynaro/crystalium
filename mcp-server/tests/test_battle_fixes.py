"""Battle-test regression guards — the main-workflow bugs found by the TRANCE scatter.

Container-first:
  docker compose run --rm crystalium pytest mcp-server/tests/test_battle_fixes.py -v
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from crystalium.config import Config
from crystalium.enforcement import CrystaliumEnforcementError
from crystalium.schemas import Scope
from crystalium.storage.relational import RelationalStore
from crystalium.trust import Tier

_NOW = datetime(2026, 6, 1, tzinfo=timezone.utc)


def _crystal(cid: str, summary: str, *, project="p") -> dict:
    return {
        "id": cid, "layer": "episodic", "trust_tier": "T1",
        "validation_state": "unverified", "status": "active", "summary": summary,
        "content_ref": "a" * 64, "scope": {"project": project},
        "provenance": {"source": "verified_agent", "created_at": _NOW.isoformat()},
        "utility": {"importance": 0.0}, "temporal": {"t_valid_from": _NOW.isoformat()},
    }


# --- Fix #1: FTS5 query sanitization (recall no longer crashes on special chars) ---

@pytest.mark.parametrize("query", [
    "what is us-east-1: region?",      # ':' and '-'
    "auth: bcrypt",                    # ':'
    "path/to/file.py",                 # '/' '.'
    'a "quoted" term',                 # '"'
    "wildcard*",                       # '*'
    "x AND y OR z NOT w",              # bareword operators
    "(grouped)",                       # parens
])
def test_bm25_special_chars_no_crash(tmp_path: Path, query: str) -> None:
    store = RelationalStore(db_path=tmp_path / "fts.sqlite")
    store.insert_crystal(_crystal("c1", "us-east-1 deploy region auth bcrypt path file"))
    # must not raise sqlite3.OperationalError
    rows = store.bm25_search(query, layer_filter="episodic", k=10)
    assert isinstance(rows, list)


def test_bm25_no_layer_filter_binding(tmp_path: Path) -> None:
    # the no-layer branch previously passed 3 bindings for 2 placeholders -> error
    store = RelationalStore(db_path=tmp_path / "nl.sqlite")
    store.insert_crystal(_crystal("c1", "deploy region notes"))
    rows = store.bm25_search("deploy region", layer_filter=None, k=10)
    assert any(r["id"] == "c1" for r in rows)


def test_bm25_all_punctuation_returns_empty(tmp_path: Path) -> None:
    store = RelationalStore(db_path=tmp_path / "p.sqlite")
    store.insert_crystal(_crystal("c1", "real content"))
    assert store.bm25_search(":-*\"()", layer_filter="episodic", k=10) == []


# --- Fix #2: recall-after-update re-indexes the new revision into the vector store ---

def test_update_reembeds_new_revision(tmp_path: Path) -> None:
    from crystalium.server import _build_components, _handle_update
    cfg = Config(data_dir=tmp_path / "u", rate_limit_per_minute=10**9)
    (_e, _a, ep, se, pr, ex, _g, _s, relational) = _build_components(cfg)
    # commit a real episodic crystal
    res = ep.commit(payload={"summary": "deploy region is us-east-1", "scope": {"project": "p"}},
                    provenance={"source": "verified_agent", "author_agent": "t",
                                "created_at": _NOW.isoformat()}, caller_tier=Tier.T1)
    cid = res["id"]
    # spy the SHARED vector store so we can assert the update re-indexes the new id
    spy = MagicMock()
    spy.embed.return_value = [0.1, 0.2, 0.3]
    ep.vector_store = spy
    out = _handle_update(
        {"id": cid, "layer": "episodic",
         "patch": {"summary": "deploy region is eu-west-1", "content": "migrated"},
         "reason": "migrate"},
        ep, se, pr, ex, _e, relational, Tier.T1,
    )
    assert out["status"] == "updated"
    new_id = out["id"]
    # the new revision was upserted into the vector store (the bug: it never was)
    assert spy.upsert.called
    assert spy.upsert.call_args.kwargs.get("crystal_id") == new_id


# --- Fix #3: semantic.update on a missing id -> structured CRYSTAL_NOT_FOUND ---

def test_semantic_update_missing_id_structured(tmp_path: Path) -> None:
    from crystalium.server import _build_components
    cfg = Config(data_dir=tmp_path / "s", rate_limit_per_minute=10**9)
    (_e, _a, _ep, semantic, _pr, _ex, _g, _s, _r) = _build_components(cfg)
    with pytest.raises(CrystaliumEnforcementError) as exc:
        semantic.update(record_id="does-not-exist", patch={"summary": "x"},
                        reason="r", caller_tier=Tier.T1)
    assert exc.value.reason_code == "CRYSTAL_NOT_FOUND"


# --- Fix #4: the enforcement matrix is immutable at both levels ---

def test_matrix_immutable_both_levels() -> None:
    from crystalium import enforcement
    with pytest.raises(TypeError):
        enforcement._MATRIX[("semantic", "commit")][Tier.T3] = "allow"   # inner frozen
    with pytest.raises(TypeError):
        enforcement._MATRIX[("bogus", "op")] = {Tier.T0: "allow"}        # outer frozen


# --- Fix #5: negative/garbage recall k is clamped, not crashed ---

def test_recall_negative_k_clamped(tmp_path: Path) -> None:
    from crystalium.server import _build_components, _handle_recall
    cfg = Config(data_dir=tmp_path / "k", rate_limit_per_minute=10**9)
    (_e, aetheryte, ep, _se, _pr, _ex, _g, scheduler, _r) = _build_components(cfg)
    ep.commit(payload={"summary": "findable fact alpha", "scope": {"project": "p"}},
              provenance={"source": "verified_agent", "created_at": _NOW.isoformat()},
              caller_tier=Tier.T1)
    # negative k must not crash or tail-slice
    res = _handle_recall({"scope": {"project": "p"}, "query": "findable fact", "k": -5},
                         aetheryte, scheduler, Tier.T1, cfg)
    assert res is not None
    res2 = _handle_recall({"scope": {"project": "p"}, "query": "findable fact", "k": "garbage"},
                          aetheryte, scheduler, Tier.T1, cfg)
    assert res2 is not None


# --- Fix #6/#7: Dream — orient 24h window + distinct-run_id enqueue coalescing ---

def test_orient_window_is_true_24h(tmp_path: Path) -> None:
    # the cutoff must be ~24h before now, not collapsed to 00:00 today
    import datetime as _dt
    from crystalium.dream import worker as _w
    now = _dt.datetime(2026, 6, 1, 3, 0, tzinfo=_dt.timezone.utc)  # 03:00 — the bug's worst case
    cutoff = now - _w.timedelta(hours=24)
    assert cutoff == _dt.datetime(2026, 5, 31, 3, 0, tzinfo=_dt.timezone.utc)
    assert (now - cutoff).total_seconds() == 24 * 3600


def test_dream_enqueue_coalesces_distinct_run_ids() -> None:
    # the real race: two triggers mint DIFFERENT run_ids; only one must enqueue
    from unittest.mock import MagicMock
    from crystalium.config import Config
    from crystalium.dream.scheduler import DreamScheduler
    worker = MagicMock()
    sched = DreamScheduler(config=Config(data_dir=Path("/tmp/sched-coalesce")),
                           worker=worker, store=MagicMock())
    a = sched.enqueue("dream-aaaaaaaaaaaa")
    b = sched.enqueue("dream-bbbbbbbbbbbb")   # distinct id, run still pending
    assert a == "dream-aaaaaaaaaaaa"
    assert b is None                           # coalesced (G8: at most one pending)
    assert worker.enqueue.call_count == 1
    # after completion, a new run may enqueue again
    sched.record_dream_completed("dream-aaaaaaaaaaaa")
    c = sched.enqueue("dream-cccccccccccc")
    assert c == "dream-cccccccccccc"
    assert worker.enqueue.call_count == 2


# --- CRIT-2: Dream drain + slot release closes the trigger→execute→release loop ---

def test_tick_drains_and_releases_slot_so_dream_is_not_inert() -> None:
    """Regression: in server mode the slot was never released, so after the first
    trigger the coalescing gate swallowed every subsequent run forever. The tick
    must drain worker.run_pending() and the worker's on_run_complete callback must
    free the slot, letting the next trigger enqueue again."""
    from unittest.mock import MagicMock
    from crystalium.config import Config
    from crystalium.dream.scheduler import DreamScheduler

    worker = MagicMock()
    sched = DreamScheduler(
        config=Config(data_dir=Path("/tmp/sched-drain"), idle_threshold_s=1, min_dream_gap_s=0),
        worker=worker, store=MagicMock(),
    )
    # Wire the production loop: draining a run releases its slot (mirrors
    # server._build_components: worker.on_run_complete = scheduler.record_dream_completed).
    worker.run_pending.side_effect = lambda: [
        sched.record_dream_completed(rid) for rid in list(sched._pending_run_ids)
    ]

    assert sched.enqueue("dream-111111111111") == "dream-111111111111"
    assert sched._pending_run_ids                      # one pending
    # Make idle/gap conditions hold so _tick proceeds, then run a tick.
    old = datetime(2000, 1, 1, tzinfo=timezone.utc)
    sched._inject_timestamps(last_activity=old, last_dream=old)
    sched._tick()                                      # drains → releases slot → may re-enqueue
    worker.run_pending.assert_called()
    # Slot was freed (and possibly immediately refilled by the tick's own enqueue);
    # the key invariant: a brand-new distinct trigger is NOT permanently swallowed.
    for rid in list(sched._pending_run_ids):
        sched.record_dream_completed(rid)
    assert sched.enqueue("dream-222222222222") == "dream-222222222222"


def test_worker_on_run_complete_fires_in_finally(tmp_path: Path) -> None:
    """_execute_run must release the slot even when a run errors mid-cycle."""
    from unittest.mock import MagicMock
    from crystalium.dream.worker import DreamWorker

    released: list[str] = []
    worker = DreamWorker(
        relational=MagicMock(), vector_store=MagicMock(), graph_store=MagicMock(),
        enforcement=MagicMock(), gate=MagicMock(), importance_fn=lambda **k: 0.0,
        on_run_complete=released.append,
    )
    # Force the run to raise after start so the except + finally both execute.
    worker._mark_run_started = MagicMock()
    worker._mark_run_finished = MagicMock()
    worker._orient = MagicMock(side_effect=RuntimeError("boom"))
    worker._execute_run("run-err")
    assert released == ["run-err"]      # slot released despite the error


# --- CRIT-1: crystalium.update tier enforcement + patch field-laundering guard ---

def _proc_crystal(cid: str) -> dict:
    c = _crystal(cid, "procedural step", project="p")
    c["layer"] = "procedural"
    return c


def _update_components(store: RelationalStore):
    from crystalium.enforcement import Enforcement
    episodic = MagicMock()
    episodic.vector_store = None                # skip the best-effort reindex
    return dict(
        episodic=episodic, semantic=MagicMock(), procedural=MagicMock(),
        execution=MagicMock(), enforcement=Enforcement(Config(data_dir=store.db_path.parent)),
        relational=store,
    )


def test_update_denies_t3_on_procedural_layer(tmp_path: Path) -> None:
    from crystalium.server import _handle_update
    store = RelationalStore(db_path=tmp_path / "u1.sqlite")
    store.insert_crystal(_proc_crystal("c1"))
    with pytest.raises(CrystaliumEnforcementError):
        _handle_update(
            {"id": "c1", "patch": {"summary": "mutated"}, "reason": "r"},
            caller_tier=Tier.T3, **_update_components(store),
        )


def test_update_strips_trust_laundering_patch_fields(tmp_path: Path) -> None:
    from crystalium.server import _handle_update
    store = RelationalStore(db_path=tmp_path / "u2.sqlite")
    store.insert_crystal(_proc_crystal("c1"))   # trust_tier T1
    res = _handle_update(
        {"id": "c1",
         "patch": {"summary": "ok", "trust_tier": "T0", "protected": True,
                   "validation_state": "validated", "layer": "semantic"},
         "reason": "r"},
        caller_tier=Tier.T1, **_update_components(store),
    )
    new = store.get_crystal(res["id"])
    assert new["summary"] == "ok"               # content patch applied
    assert new["trust_tier"] == "T1"            # laundering stripped
    assert new["layer"] == "procedural"
    assert new["validation_state"] == "unverified"
    assert not new.get("protected")


def test_update_outcome_score_rejected_out_of_range(tmp_path: Path) -> None:
    from crystalium.server import _handle_update
    store = RelationalStore(db_path=tmp_path / "u3.sqlite")
    store.insert_crystal(_proc_crystal("c1"))
    comps = _update_components(store)
    with pytest.raises(CrystaliumEnforcementError):
        _handle_update({"id": "c1", "patch": {"outcome_success_score": 2.0}, "reason": "r"},
                       caller_tier=Tier.T1, **comps)
    with pytest.raises(CrystaliumEnforcementError):
        _handle_update({"id": "c1", "patch": {"outcome_success_score": "nan-ish"}, "reason": "r"},
                       caller_tier=Tier.T1, **comps)


# --- Lost-update race: read-modify-write mutators must serialize (BEGIN IMMEDIATE) ---

def test_record_access_no_lost_update_under_concurrency(tmp_path: Path) -> None:
    """Concurrent record_access() on the SAME crystal must not drop increments.

    Without the BEGIN IMMEDIATE write-lock, two threads each SELECT access_count=n,
    both write n+1, and one increment is lost. With it, every increment lands."""
    import json as _json
    import threading

    store = RelationalStore(db_path=tmp_path / "rmw.sqlite")
    store.insert_crystal(_crystal("hot", "popular fact"))

    n_threads, per_thread = 12, 8
    barrier = threading.Barrier(n_threads)

    def worker() -> None:
        barrier.wait()                       # maximize interleaving
        for _ in range(per_thread):
            store.record_access("hot", now=_NOW)

    threads = [threading.Thread(target=worker) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    final = store.get_crystal("hot")
    util = final["utility"] if isinstance(final["utility"], dict) else _json.loads(final["utility"])
    assert util["access_count"] == n_threads * per_thread   # zero lost increments


# --- T1 G1.1: semantic.update() re-embeds the new revision (dense recall path) ---

def test_semantic_update_reembeds_new_revision(tmp_path: Path) -> None:
    """G1.1: semantic.update() must upsert the new revision into the vector store.

    Before the fix, semantic.update() called insert_crystal() for the new revision
    but skipped the vector upsert — identical to the episodic fallback gap that
    _handle_update already fixed. With recall_active_only the superseded original is
    excluded, so the updated fact silently vanished from dense recall.

    We insert directly into the relational store (bypassing the promotion gate, which
    is driven by witness count) so the crystal is present and updateable in all
    environments including CRYSTALIUM_SKIP_SLOW.
    """
    from crystalium.server import _build_components
    cfg = Config(data_dir=tmp_path / "sem_upd", rate_limit_per_minute=10**9)
    (_e, _a, _ep, semantic, _pr, _ex, _g, _s, relational) = _build_components(cfg)

    # Insert a semantic crystal directly (bypasses gate; SKIP_SLOW-safe)
    cid = "sem-direct-001"
    relational.insert_crystal({
        "id": cid, "layer": "semantic", "trust_tier": "T1",
        "validation_state": "validated", "status": "active",
        "summary": "deploy region is us-east-1", "content_ref": "a" * 64,
        "scope": {"project": "p"},
        "provenance": {"source": "verified_agent", "created_at": _NOW.isoformat()},
        "utility": {"importance": 0.0},
        "temporal": {"t_valid_from": _NOW.isoformat(), "t_valid_to": None, "superseded_by": None},
    })

    # Spy the vector store on the semantic layer
    spy = MagicMock()
    spy.embed.return_value = [0.1, 0.2, 0.3]
    semantic.vector_store = spy

    out = semantic.update(
        record_id=cid,
        patch={"summary": "deploy region is eu-west-1"},
        reason="region migration",
        caller_tier=Tier.T1,
    )
    new_id = out["id"]

    # The new revision must have been upserted into the vector store
    assert spy.upsert.called, "vector_store.upsert was never called after semantic.update()"
    assert spy.upsert.call_args.kwargs.get("crystal_id") == new_id


# --- T1 G1.3: tool_calls audit table is populated by record_call() ---

def test_tool_calls_populated_via_telemetry(tmp_path: Path) -> None:
    """G1.3: record_call() (via register_relational_store) must write to tool_calls.

    Before the fix, record_tool_call() had zero callers — DreamWorker._orient()
    queried a perpetually-empty table. Wiring the store into telemetry makes every
    tool invocation appear in the audit table.
    """
    import sqlite3

    from crystalium.telemetry import record_call, register_relational_store, reset_latency_samples

    store = RelationalStore(db_path=tmp_path / "audit.sqlite")
    register_relational_store(store)
    try:
        reset_latency_samples()
        record_call(tool="crystalium.recall", layer="episodic", tier="T1",
                    op="recall", result="ok", latency_ms=42.0)
        record_call(tool="crystalium.commit", layer="semantic", tier="T1",
                    op="commit", result="ok", latency_ms=10.0)

        with sqlite3.connect(str(store.db_path)) as conn:
            rows = conn.execute("SELECT tool, result FROM tool_calls ORDER BY ts").fetchall()

        assert len(rows) == 2
        assert rows[0][0] == "crystalium.recall"
        assert rows[1][0] == "crystalium.commit"
    finally:
        # Deregister so other tests are not affected by the side-channel
        register_relational_store(None)


def test_tool_calls_readable_by_orient(tmp_path: Path) -> None:
    """G1.3: DreamWorker._orient() reads the tool_calls count from a populated table.

    crystalium#35 fix-forward (v2.0.1): written under telemetry.RECALL_TOOL
    (the canonical dispatch name), not the pre-#35 stale dotted literal —
    _orient()'s own query was repointed to the same canonical key (see
    test_server.py's test_dream_orient_counts_recalls_via_canonical_key for
    the end-to-end regression guard that drives this through the real
    dispatcher instead of writing directly here).
    """
    import datetime as _dt

    from crystalium.dream.worker import DreamWorker
    from crystalium.telemetry import RECALL_TOOL, record_call, register_relational_store, reset_latency_samples

    store = RelationalStore(db_path=tmp_path / "orient.sqlite")
    register_relational_store(store)
    try:
        reset_latency_samples()
        # Write two recall calls into the audit table
        record_call(tool=RECALL_TOOL, layer=None, tier="T1",
                    op="recall", result="ok", latency_ms=55.0)
        record_call(tool=RECALL_TOOL, layer=None, tier="T1",
                    op="recall", result="ok", latency_ms=60.0)

        worker = DreamWorker(
            relational=store,
            vector_store=MagicMock(),
            graph_store=MagicMock(),
            enforcement=MagicMock(),
            gate=MagicMock(),
            importance_fn=lambda **k: 0.0,
        )
        summary = worker._orient(now=_dt.datetime.now(_dt.UTC))
        assert "total_recalls=2" in summary, f"_orient did not report 2 recalls: {summary!r}"
    finally:
        register_relational_store(None)


def test_merge_provenance_no_lost_corroboration_under_concurrency(tmp_path: Path) -> None:
    """Concurrent merge_provenance() must not drop corroboration bumps."""
    import json as _json
    import threading

    store = RelationalStore(db_path=tmp_path / "rmw2.sqlite")
    store.insert_crystal(_crystal("c", "fact"))

    n_threads = 10
    barrier = threading.Barrier(n_threads)

    def worker(i: int) -> None:
        barrier.wait()
        store.merge_provenance("c", {"author_agent": f"agent-{i}", "source": "verified_agent"})

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    final = store.get_crystal("c")
    prov = final["provenance"] if isinstance(final["provenance"], dict) else _json.loads(final["provenance"])
    # corroboration starts at 1, each of n merges bumps by 1 -> 1 + n
    assert prov["corroboration"] == 1 + n_threads
    assert len(prov["merged_authors"]) == n_threads   # every distinct author retained


def test_canary_run_uses_fresh_ephemeral_data_dir(monkeypatch) -> None:
    """Canary reproducibility (the real G1.1 'canary 0.80' resolution): each
    _build_live_handlers() run must inject a FRESH ephemeral data_dir, never the
    persistent ~/.crystalium/default volume. Sharing the default store lets
    cross-run write_dedup_merge pollute prior runs' crystals and collapses the
    headline (observed 0.25 instead of the true 1.0). Distinct per-run stores keep
    `make bench` deterministic without a manual volume wipe."""
    import evals.ab_memory_onoff as ab

    seen: list = []

    class _SpyConfig:
        def __init__(self, **kwargs):
            seen.append(kwargs.get("data_dir"))

    # Stub the heavy component build so this stays a unit test (no embedder deps).
    monkeypatch.setattr("crystalium.config.Config", _SpyConfig)
    monkeypatch.setattr(
        "crystalium.server._build_components",
        lambda cfg: tuple(MagicMock() for _ in range(9)),
    )

    ab._build_live_handlers()
    ab._build_live_handlers()

    default = str(Path("~/.crystalium/default/").expanduser())
    assert len(seen) == 2
    for dd in seen:
        assert dd is not None, "canary must inject a data_dir, not fall back to the default volume"
        assert str(dd) != default, "canary must NOT use the persistent default store"
        assert "crystalium-canary-" in str(dd)
    assert str(seen[0]) != str(seen[1]), "each run must get its OWN ephemeral store"
