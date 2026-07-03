"""v1.6 Wave 4 — memory diagnosability + guards.

Covers the six v1.6 changes:
  1. Canonical project-key derivation + write-time scope normalization (scope.py)
  2. Summary-quality gate (quality.py) + plan_checkpoint auto-enrichment
  3. `recall --explain` (aetheryte.retrieve.Aetheryte.recall(explain=True))
  4. (doctor upgrades are covered by CLI smoke in test_cli.py-style tools; the
     underlying RelationalStore.diagnostics_summary() is exercised directly here)
  5. Never-deprecate-last-checkpoint guard (dream/worker.py::DreamWorker._prune)
  6. FTS5 injection regression through the full explain-enabled recall path

MOTIVATING INCIDENT (CHANGELOG v1.6.0): a live project store held 9 crystals yet
answered every recall with 0 records. Root causes: (1) the only plan checkpoint
was status=deprecated and recall_active_only (default ON) filtered it; (2)
writers used 3 different free-typed scope.project keys for the same project;
(3) summaries were terse machine labels (the only FTS-indexed text); (4)
embedding_ref was null on every crystal (deps absent -> Null vector store).
TestRecallExplain::test_explain_diagnoses_zero_records_against_nonempty_store
reproduces (1)+(2) directly; TestFts5InjectionRegression exercises the arm that
protects (3)'s indexed text from a hostile query.

Container-first:
  docker compose run --rm crystalium pytest mcp-server/tests/test_diagnosability.py -v
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from crystalium.config import Config
from crystalium.ecl import compute_sha256
from crystalium.quality import is_poor_summary
from crystalium.schemas import Scope
from crystalium.scope import canonical_project_key, default_recall_project, normalize_write_scope
from crystalium.server import _build_components, _handle_commit, _handle_ingest
from crystalium.trust import Tier

_NOW = datetime(2026, 7, 1, tzinfo=timezone.utc)


def _components(tmp_path: Path, name: str = "eidolons"):
    cfg = Config(data_dir=tmp_path / name, rate_limit_per_minute=1_000_000)
    return cfg, _build_components(cfg)


def _crystal(
    cid: str,
    *,
    layer: str = "episodic",
    status: str = "active",
    project: str = "eidolons",
    plan: str | None = None,
    summary: str = "a real crystal with a genuinely descriptive summary",
    importance: float = 0.5,
) -> dict:
    scope = {"project": project}
    if plan is not None:
        scope["plan"] = plan
    d = {
        "id": cid,
        "layer": layer,
        "trust_tier": "T1",
        "validation_state": "validated" if layer == "execution" else "unverified",
        "status": status,
        "summary": summary,
        "embedding_ref": None,
        "scope": scope,
        "provenance": {"source": "verified_agent", "created_at": _NOW.isoformat()},
        "utility": {
            "access_count": 0,
            "last_access": _NOW.isoformat(),
            "outcome_success_score": None,
            "importance": importance,
            "novelty_at_write": 0.5,
        },
        "temporal": {"t_valid_from": _NOW.isoformat(), "t_valid_to": None, "superseded_by": None},
    }
    if layer == "episodic":
        import hashlib

        d["content_ref"] = hashlib.sha256(cid.encode()).hexdigest()
    return d


# ---------------------------------------------------------------------------
# Item 1 — canonical project-key derivation (pure functions)
# ---------------------------------------------------------------------------


class TestCanonicalProjectKey:
    def test_canonical_project_key_is_data_dir_basename(self, tmp_path: Path) -> None:
        assert canonical_project_key(tmp_path / "eidolons-v2") == "eidolons-v2"

    def test_canonical_project_key_empty_basename_falls_back(self) -> None:
        assert canonical_project_key(Path("/")) == "default"

    def test_normalize_write_scope_passthrough_when_already_canonical(self) -> None:
        scope, normalized = normalize_write_scope({"project": "eidolons"}, "eidolons")
        assert normalized is False
        assert scope == {"project": "eidolons"}
        assert "project_raw" not in scope

    def test_normalize_write_scope_preserves_raw_and_rewrites(self) -> None:
        scope, normalized = normalize_write_scope(
            {"project": "riverdale-migration"}, "eidolons"
        )
        assert normalized is True
        assert scope["project"] == "eidolons"
        assert scope["project_raw"] == "riverdale-migration"

    def test_normalize_write_scope_missing_project_defaults_silently(self) -> None:
        scope, normalized = normalize_write_scope({}, "eidolons")
        assert normalized is True
        assert scope["project"] == "eidolons"
        assert "project_raw" not in scope  # nothing to preserve

    def test_normalize_write_scope_none_scope(self) -> None:
        scope, normalized = normalize_write_scope(None, "eidolons")
        assert normalized is True
        assert scope == {"project": "eidolons"}

    def test_normalize_write_scope_does_not_mutate_input(self) -> None:
        original = {"project": "riverdale-migration"}
        normalize_write_scope(original, "eidolons")
        assert original == {"project": "riverdale-migration"}  # caller's dict untouched

    def test_default_recall_project_explicit_passthrough(self) -> None:
        # Recall never rewrites an explicit project — legacy keys must stay queryable.
        assert default_recall_project({"project": "legacy-key"}, "eidolons") == "legacy-key"

    def test_default_recall_project_defaults_when_omitted(self) -> None:
        assert default_recall_project({}, "eidolons") == "eidolons"
        assert default_recall_project(None, "eidolons") == "eidolons"


# ---------------------------------------------------------------------------
# Item 1 — write-path integration (commit / ingest / checkpoint / replan)
# ---------------------------------------------------------------------------


class TestWriteScopeNormalization:
    def test_commit_normalizes_fragmented_project_key(self, tmp_path: Path) -> None:
        cfg, (enforcement, _a, ep, se, pr, ex, _g, _s, relational) = _components(tmp_path)
        result = _handle_commit(
            {
                "layer": "episodic",
                "payload": {
                    "summary": "a genuinely descriptive summary about caching policy",
                    "scope": {"project": "eidolons-v2-go-migration-2026-06-24"},
                },
                "provenance": {"source": "verified_agent"},
            },
            ep, se, pr, ex, Tier.T1, cfg,
        )
        assert result["scope_normalized"] is True
        crystal = relational.get_crystal(result["id"])
        assert crystal["scope"]["project"] == "eidolons"
        assert crystal["scope"]["project_raw"] == "eidolons-v2-go-migration-2026-06-24"

    def test_commit_no_advisory_when_project_already_canonical(self, tmp_path: Path) -> None:
        cfg, (enforcement, _a, ep, se, pr, ex, _g, _s, relational) = _components(tmp_path)
        result = _handle_commit(
            {
                "layer": "episodic",
                "payload": {
                    "summary": "a genuinely descriptive summary about caching policy",
                    "scope": {"project": "eidolons"},
                },
                "provenance": {"source": "verified_agent"},
            },
            ep, se, pr, ex, Tier.T1, cfg,
        )
        assert "scope_normalized" not in result
        crystal = relational.get_crystal(result["id"])
        assert "project_raw" not in crystal["scope"]

    def test_ingest_normalizes_thread_derived_project(self, tmp_path: Path) -> None:
        cfg, (enforcement, _a, ep, se, pr, ex, _g, _s, relational) = _components(tmp_path)
        payload = '{"note": "self-authored fact about caching"}'
        sha = compute_sha256(payload.encode())
        envelope = {
            "envelope_version": "2.0", "message_id": "m1",
            "thread_id": "riverdale-migration", "parent_id": None,
            "from": {"eidolon": "atlas", "version": "1.0.0"},
            "to": {"eidolon": "crystalium", "version": "1.0.0"},
            "performative": "INFORM", "objective": "caching policy notes",
            "artifact": {"kind": "note", "schema_version": "1.0", "path": "stdin",
                         "sha256": sha, "size_bytes": len(payload)},
            "integrity": {"method": "sha256", "value": sha},
            "trace": {"ts": "2026-01-01T00:00:00Z", "host": "h", "model": "m", "tier": "T1"},
        }
        receipt = _handle_ingest(
            {"envelope": envelope, "payload": payload, "payload_encoding": "json"},
            ep, se, pr, ex, cfg,
        )
        assert receipt["scope_normalized"] is True
        crystal = relational.get_crystal(receipt["id"])
        assert crystal["scope"]["project"] == "eidolons"
        assert crystal["scope"]["project_raw"] == "riverdale-migration"

    def test_checkpoint_normalizes_project_key(self, tmp_path: Path) -> None:
        cfg, (enforcement, _a, _ep, _se, _pr, ex, _g, _s, relational) = _components(tmp_path)
        res = ex.checkpoint(
            state={
                "scope": {"project": "riverdale-migration"},
                "summary": "checkpoint for the wave 4 rollout plan before verification",
            },
            caller_tier=Tier.T1,
        )
        assert res["scope_normalized"] is True
        crystal = relational.get_crystal(res["id"])
        assert crystal["scope"]["project"] == "eidolons"
        assert crystal["scope"]["project_raw"] == "riverdale-migration"

    def test_checkpoint_via_commit_tool_normalizes_once(self, tmp_path: Path) -> None:
        """crystalium.commit(layer='execution') dispatches through _handle_commit,
        which normalizes BEFORE execution.checkpoint() runs its own (idempotent)
        pass — the advisory must still surface exactly once, not be lost."""
        cfg, (enforcement, _a, ep, se, pr, ex, _g, _s, relational) = _components(tmp_path)
        result = _handle_commit(
            {
                "layer": "execution",
                "payload": {
                    "scope": {"project": "riverdale-migration"},
                    "summary": "checkpoint for the wave 4 rollout plan before verification",
                },
                "provenance": {"source": "verified_agent"},
            },
            ep, se, pr, ex, Tier.T1, cfg,
        )
        assert result["scope_normalized"] is True
        crystal = relational.get_crystal(result["id"])
        assert crystal["scope"]["project"] == "eidolons"
        assert crystal["scope"]["project_raw"] == "riverdale-migration"

    def test_replan_normalizes_project_key(self, tmp_path: Path) -> None:
        cfg, (enforcement, _a, _ep, _se, _pr, ex, _g, _s, relational) = _components(tmp_path)
        res = ex.replan(
            diff={"scope": {"project": "riverdale-migration"}, "summary": "replan diff"},
            caller_tier=Tier.T1,
        )
        assert res["scope_normalized"] is True
        crystal = relational.get_crystal(res["id"])
        assert crystal["scope"]["project"] == "eidolons"
        assert crystal["scope"]["project_raw"] == "riverdale-migration"

    def test_recall_defaults_omitted_project_to_canonical(self, tmp_path: Path) -> None:
        cfg, (enforcement, aetheryte, ep, se, pr, ex, _g, _s, relational) = _components(tmp_path)
        ep.commit(
            payload={"summary": "findable fact about deploy regions", "scope": {"project": "eidolons"}},
            provenance={"source": "verified_agent", "created_at": _NOW.isoformat()},
            caller_tier=Tier.T1,
        )
        # Omitted scope.project -> defaults to canonical, not the literal "default".
        out = aetheryte.recall(Scope(project=canonical_project_key(cfg.data_dir)),
                               "findable fact deploy", 10, None, Tier.T1)
        assert len(out.records) == 1

    def test_recall_explicit_legacy_project_still_queryable(self, tmp_path: Path) -> None:
        """Recall never rewrites an explicit scope.project — a legacy/fragmented
        key from before v1.6 (or from a row v1.6 never migrated) must stay
        queryable so it can be found and fixed, not silently hidden."""
        cfg, (enforcement, aetheryte, _ep, _se, _pr, _ex, _g, _s, relational) = _components(tmp_path)
        relational.insert_crystal(_crystal("legacy-1", project="riverdale-migration",
                                            summary="a legacy fact about the caching layer"))
        out = aetheryte.recall(Scope(project="riverdale-migration"), "legacy fact caching", 10, None, Tier.T1)
        assert any(r.id == "legacy-1" for r in out.records)


# ---------------------------------------------------------------------------
# Item 2 — summary-quality gate + plan_checkpoint auto-enrichment
# ---------------------------------------------------------------------------


class TestSummaryQualityGate:
    def test_is_poor_summary_too_short(self) -> None:
        assert is_poor_summary("short") is True

    def test_is_poor_summary_machine_label(self) -> None:
        assert is_poor_summary("plan_checkpoint:08234787") is True

    def test_is_poor_summary_too_few_words(self) -> None:
        # long enough, but one unbroken alphabetic run -> only 1 "word"
        assert is_poor_summary("asdkfjhasdkjfhaskvbjkasdvbnasdxyz") is True

    def test_is_poor_summary_none_or_empty(self) -> None:
        assert is_poor_summary(None) is True
        assert is_poor_summary("") is True

    def test_is_poor_summary_passes_with_real_sentence(self) -> None:
        assert is_poor_summary(
            "Deploy region migrated from us-east-1 to eu-west-1 for latency"
        ) is False

    def test_commit_poor_summary_gets_advisory(self, tmp_path: Path) -> None:
        cfg, (enforcement, _a, ep, se, pr, ex, _g, _s, relational) = _components(tmp_path)
        result = _handle_commit(
            {
                "layer": "episodic",
                "payload": {"summary": "short", "scope": {"project": "eidolons"}},
                "provenance": {"source": "verified_agent"},
            },
            ep, se, pr, ex, Tier.T1, cfg,
        )
        assert result["status"] == "committed"  # NEVER blocks the write (soft in 1.6)
        assert result["summary_quality"] == "poor"
        assert "advisory" in result

    def test_commit_good_summary_no_advisory(self, tmp_path: Path) -> None:
        cfg, (enforcement, _a, ep, se, pr, ex, _g, _s, relational) = _components(tmp_path)
        result = _handle_commit(
            {
                "layer": "episodic",
                "payload": {
                    "summary": "a genuinely descriptive summary about caching policy decisions",
                    "scope": {"project": "eidolons"},
                },
                "provenance": {"source": "verified_agent"},
            },
            ep, se, pr, ex, Tier.T1, cfg,
        )
        assert "summary_quality" not in result

    def test_checkpoint_omitted_summary_auto_enriched(self, tmp_path: Path) -> None:
        cfg, (enforcement, _a, _ep, _se, _pr, ex, _g, _s, relational) = _components(tmp_path)
        res = ex.checkpoint(
            state={
                "scope": {"project": "eidolons", "plan": "wave-4"},
                "plan_name": "Wave 4 rollout",
                "phase": "verify",
            },
            caller_tier=Tier.T1,
        )
        crystal = relational.get_crystal(res["id"])
        assert "plan_checkpoint:" not in crystal["summary"]
        assert "Wave 4 rollout" in crystal["summary"]
        assert "verify" in crystal["summary"]
        assert is_poor_summary(crystal["summary"]) is False
        assert "summary_quality" not in res

    def test_checkpoint_explicit_poor_summary_not_rewritten_but_flagged(
        self, tmp_path: Path
    ) -> None:
        cfg, (enforcement, _a, _ep, _se, _pr, ex, _g, _s, relational) = _components(tmp_path)
        res = ex.checkpoint(
            state={"scope": {"project": "eidolons"}, "summary": "cp1"},
            caller_tier=Tier.T1,
        )
        crystal = relational.get_crystal(res["id"])
        assert crystal["summary"] == "cp1"          # caller's summary is NEVER rewritten
        assert res["summary_quality"] == "poor"
        assert "advisory" in res

    def test_checkpoint_bare_default_label_would_have_been_poor(self) -> None:
        """Sanity anchor: the OLD f'plan_checkpoint:{id[:8]}' default (pre-v1.6)
        is exactly the machine-label shape the gate exists to catch."""
        assert is_poor_summary("plan_checkpoint:08234787") is True


# ---------------------------------------------------------------------------
# Item 3 — recall --explain
# ---------------------------------------------------------------------------


class TestRecallExplain:
    def test_explain_diagnoses_zero_records_against_nonempty_store(self, tmp_path: Path) -> None:
        """The MOTIVATING INCIDENT, reproduced: a store with real crystals answers
        a scoped recall with 0 records because (1) the only relevant checkpoint
        is deprecated and (2) a sibling crystal lives under a fragmented project
        key. explain must make BOTH visible from the result alone."""
        cfg, (enforcement, aetheryte, _ep, _se, _pr, _ex, _g, _s, relational) = _components(tmp_path)

        relational.insert_crystal(_crystal(
            "cp-1", layer="execution", status="deprecated", project="eidolons", plan="wave-4",
            summary="plan checkpoint for wave rollout project eidolons phase verify",
        ))
        relational.insert_crystal(_crystal(
            "ep-1", project="riverdale-migration",
            # Shares the query terms with cp-1 so it becomes a BM25 candidate too
            # (otherwise it would never reach the scope filter at all — this test
            # needs it to be FOUND and then EXCLUDED by scope, to exercise
            # filtered_by_scope).
            summary="riverdale migration notes about the wave rollout caching layer",
        ))

        result = aetheryte.recall(
            Scope(project="eidolons"), "wave rollout", 10, None, Tier.T1, explain=True
        )

        assert result.records == []  # the incident: zero records despite a non-empty store
        explain = result.explain
        assert explain is not None
        assert explain["store"]["total_crystals"] == 2
        assert explain["store"]["active"] == 1
        assert explain["filtered_by_status"] >= 1   # the deprecated checkpoint, diagnosed
        assert explain["filtered_by_scope"] >= 1     # the fragmented-project sibling, diagnosed
        assert set(explain["project_keys_present"]) == {"eidolons", "riverdale-migration"}
        assert explain["arms"]["bm25"] == "on"
        assert explain["arms"]["dense"].startswith("inactive") or explain["arms"]["dense"] == "active"
        assert explain["candidates_prefilter"] >= 1

    def test_explain_absent_by_default(self, tmp_path: Path) -> None:
        cfg, (enforcement, aetheryte, ep, se, pr, ex, _g, _s, relational) = _components(tmp_path)
        result = aetheryte.recall(Scope(project="eidolons"), "anything", 5, None, Tier.T1)
        assert result.explain is None
        # exclude_none: a non-explain call's dumped JSON shape omits the key entirely.
        assert "explain" not in result.model_dump(exclude_none=True)

    def test_explain_dense_arm_inactive_with_reason_on_null_vector_store(
        self, tmp_path: Path
    ) -> None:
        """Root cause #4 of the MOTIVATING INCIDENT: heavy deps absent -> Null
        vector store -> embedding_ref never populated -> dense arm silently
        inactive. explain must name the reason, not just go quiet."""
        from crystalium.aetheryte.redact import Redactor
        from crystalium.aetheryte.retrieve import Aetheryte
        from crystalium.composer import Composer
        from crystalium.enforcement import Enforcement
        from crystalium.importance import importance_score
        from crystalium.server import _NullGraphStore, _NullVectorStore
        from crystalium.storage.relational import RelationalStore

        cfg = Config(data_dir=tmp_path / "null-arms", rate_limit_per_minute=1_000_000)
        relational = RelationalStore(db_path=cfg.sqlite_path)
        relational.insert_crystal(_crystal("c1", summary="a real crystal about caching notes"))
        aetheryte = Aetheryte(
            relational=relational,
            vector_store=_NullVectorStore(),
            graph_store=_NullGraphStore(),
            enforcement=Enforcement(cfg),
            redactor=Redactor(config=cfg),
            importance_fn=importance_score,
            composer=Composer(config=cfg),
        )
        result = aetheryte.recall(
            Scope(project="eidolons"), "caching notes", 10, None, Tier.T1, explain=True
        )
        assert result.explain["arms"]["dense"] == "inactive(null_vector_store)"
        assert result.explain["arms"]["graph"] == "off"
        assert result.explain["store"]["embedded"] == 0

    def test_explain_bypasses_recall_cache(self, tmp_path: Path) -> None:
        """explain=True never reads a cached result and never writes one, so a
        diagnostic call is always fresh and never leaks into a normal caller's
        cached response."""
        from crystalium.aetheryte.cache import RecallCache

        cfg = Config(data_dir=tmp_path / "cache-explain", rate_limit_per_minute=1_000_000,
                     recall_prefetch=True)
        (enforcement, _a, ep, se, pr, ex, gate, sched, relational) = _build_components(cfg)
        cache = RecallCache()
        from crystalium.aetheryte.redact import Redactor
        from crystalium.aetheryte.retrieve import Aetheryte
        from crystalium.composer import Composer
        from crystalium.enforcement import Enforcement
        from crystalium.importance import importance_score
        from crystalium.server import _NullGraphStore, _NullVectorStore

        aetheryte = Aetheryte(
            relational=relational, vector_store=_NullVectorStore(), graph_store=_NullGraphStore(),
            enforcement=Enforcement(cfg), redactor=Redactor(config=cfg),
            importance_fn=importance_score, composer=Composer(config=cfg),
            recall_cache=cache,
        )
        ep.commit(payload={"summary": "cache bypass probe content", "scope": {"project": "eidolons"}},
                  provenance={"source": "verified_agent", "created_at": _NOW.isoformat()},
                  caller_tier=Tier.T1)
        scope = Scope(project="eidolons")
        aetheryte.recall(scope, "cache bypass probe", 10, None, Tier.T1, explain=True)
        # explain=True must not have populated the cache.
        assert cache.get("eidolons", "cache bypass probe", k=10, layers=None,
                         visibility=None, sensitivity=None, tier="T1") is None


# ---------------------------------------------------------------------------
# Item 4 — RelationalStore.diagnostics_summary() (backs doctor + explain)
# ---------------------------------------------------------------------------


class TestDiagnosticsSummary:
    def test_diagnostics_summary_counts_and_groups(self, tmp_path: Path) -> None:
        cfg, (enforcement, _a, _ep, _se, _pr, _ex, _g, _s, relational) = _components(tmp_path)
        relational.insert_crystal(_crystal("a1", project="eidolons"))
        relational.insert_crystal(_crystal("a2", project="eidolons", status="deprecated"))
        relational.insert_crystal(_crystal("a3", project="riverdale-migration"))

        stats = relational.diagnostics_summary()
        assert stats["total_crystals"] == 3
        assert stats["active"] == 2
        assert stats["embedded"] == 0
        assert stats["by_status"] == {"active": 2, "deprecated": 1}
        assert stats["by_project"] == {"eidolons": 2, "riverdale-migration": 1}


# ---------------------------------------------------------------------------
# Item 5 — never-deprecate-last-checkpoint guard
# ---------------------------------------------------------------------------


class TestNeverDeprecateLastCheckpoint:
    def _worker(self, enforcement, gate, relational, *, importance=0.0):
        from crystalium.dream.worker import DreamWorker

        return DreamWorker(
            relational=relational, vector_store=None, graph_store=None,
            enforcement=enforcement, gate=gate,
            importance_fn=lambda rec, now: importance,
        )

    def test_sole_active_checkpoint_for_plan_survives_prune(self, tmp_path: Path) -> None:
        cfg, (enforcement, _a, _ep, _se, _pr, ex, gate, _s, relational) = _components(tmp_path)
        res = ex.checkpoint(
            state={"scope": {"project": "eidolons", "plan": "wave-4"},
                   "summary": "wave 4 rollout checkpoint before verification phase"},
            caller_tier=Tier.T1,
        )
        cid = res["id"]

        worker = self._worker(enforcement, gate, relational)
        worker._prune(datetime.now(timezone.utc))

        assert relational.get_crystal(cid)["status"] == "active"

    def test_sole_active_checkpoint_falls_back_to_project_when_plan_absent(
        self, tmp_path: Path
    ) -> None:
        cfg, (enforcement, _a, _ep, _se, _pr, ex, gate, _s, relational) = _components(tmp_path)
        res = ex.checkpoint(
            state={"scope": {"project": "eidolons"},
                   "summary": "checkpoint with no explicit plan key at all here"},
            caller_tier=Tier.T1,
        )
        cid = res["id"]

        worker = self._worker(enforcement, gate, relational)
        worker._prune(datetime.now(timezone.utc))

        assert relational.get_crystal(cid)["status"] == "active"

    def test_at_least_one_checkpoint_per_plan_survives_prune(self, tmp_path: Path) -> None:
        """Two low-scoring checkpoints for the SAME plan: pruning may deprecate
        one (a genuine replacement exists), but a plan's checkpoint count must
        never hit zero."""
        cfg, (enforcement, _a, _ep, _se, _pr, ex, gate, _s, relational) = _components(tmp_path)
        cid_a = ex.checkpoint(
            state={"scope": {"project": "eidolons", "plan": "wave-4"},
                   "summary": "wave 4 rollout checkpoint number one before review"},
            caller_tier=Tier.T1,
        )["id"]
        cid_b = ex.checkpoint(
            state={"scope": {"project": "eidolons", "plan": "wave-4"},
                   "summary": "wave 4 rollout checkpoint number two after review"},
            caller_tier=Tier.T1,
        )["id"]

        worker = self._worker(enforcement, gate, relational)
        worker._prune(datetime.now(timezone.utc))

        statuses = [relational.get_crystal(cid_a)["status"], relational.get_crystal(cid_b)["status"]]
        assert statuses.count("active") >= 1, "a plan must never end a prune pass with zero checkpoints"

    def test_checkpoints_in_different_plans_are_each_protected(self, tmp_path: Path) -> None:
        cfg, (enforcement, _a, _ep, _se, _pr, ex, gate, _s, relational) = _components(tmp_path)
        cid_a = ex.checkpoint(
            state={"scope": {"project": "eidolons", "plan": "wave-4"},
                   "summary": "wave 4 rollout checkpoint before verification phase"},
            caller_tier=Tier.T1,
        )["id"]
        cid_b = ex.checkpoint(
            state={"scope": {"project": "eidolons", "plan": "wave-5"},
                   "summary": "wave 5 planning checkpoint before kickoff meeting"},
            caller_tier=Tier.T1,
        )["id"]

        worker = self._worker(enforcement, gate, relational)
        worker._prune(datetime.now(timezone.utc))

        # each is the SOLE checkpoint of its own distinct plan -> both protected
        assert relational.get_crystal(cid_a)["status"] == "active"
        assert relational.get_crystal(cid_b)["status"] == "active"

    def test_non_execution_layers_are_not_exempted_by_the_guard(self, tmp_path: Path) -> None:
        """The guard is execution-layer-only; a lone low-scoring episodic crystal
        is pruned normally (never-deprecate-last-checkpoint is not a blanket
        never-deprecate-anything-alone rule)."""
        cfg, (enforcement, _a, ep, _se, _pr, _ex, gate, _s, relational) = _components(tmp_path)
        res = ep.commit(
            payload={"summary": "a lone episodic crystal with nothing else nearby",
                     "scope": {"project": "eidolons"}},
            provenance={"source": "verified_agent", "created_at": _NOW.isoformat()},
            caller_tier=Tier.T1,
        )
        worker = self._worker(enforcement, gate, relational)
        worker._prune(datetime.now(timezone.utc))
        assert relational.get_crystal(res["id"])["status"] == "deprecated"


# ---------------------------------------------------------------------------
# G1.2 regression — FTS5 injection survives the full explain-enabled recall path
# (bm25_search's own sanitizer already existed pre-1.6; this exercises it
# through the diagnosability-enhanced pipeline end-to-end.)
# ---------------------------------------------------------------------------


class TestFts5InjectionRegression:
    @pytest.mark.parametrize("query", [
        'a "quoted" term',
        "wildcard*",
        "x AND y OR z NOT w",
        "path/to/file.py",
        "what is us-east-1: region?",
        "(grouped)",
    ])
    def test_recall_explain_survives_hostile_query(self, tmp_path: Path, query: str) -> None:
        cfg, (enforcement, aetheryte, ep, se, pr, ex, _g, _s, relational) = _components(tmp_path)
        ep.commit(
            payload={"summary": "some real content living in the store", "scope": {"project": "eidolons"}},
            provenance={"source": "verified_agent", "created_at": _NOW.isoformat()},
            caller_tier=Tier.T1,
        )
        result = aetheryte.recall(
            Scope(project="eidolons"), query, 10, None, Tier.T1, explain=True
        )
        assert result.explain is not None
        assert result.explain["store"]["total_crystals"] == 1
