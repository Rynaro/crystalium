"""Tests for RelationalStore — SQLite + FTS5 index, BM25 search, promotions, telemetry.

Container-first: run via:
  docker compose run --rm crystalium pytest mcp-server/tests/test_storage_relational.py -v
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from crystalium.storage.relational import MAX_EXPORT_NODES, RelationalStore


class TestRelationalStoreBasics:
    def test_insert_and_get_round_trip(
        self, tmp_relational_store: RelationalStore, sample_crystal
    ) -> None:
        crystal = sample_crystal(layer="semantic")
        tmp_relational_store.insert_crystal(crystal)
        retrieved = tmp_relational_store.get_crystal(crystal["id"])
        assert retrieved is not None
        assert retrieved["id"] == crystal["id"]
        assert retrieved["layer"] == "semantic"
        assert retrieved["trust_tier"] == "T1"

    def test_get_returns_none_for_missing(
        self, tmp_relational_store: RelationalStore
    ) -> None:
        result = tmp_relational_store.get_crystal("nonexistent-id")
        assert result is None

    def test_scope_json_round_trip(
        self, tmp_relational_store: RelationalStore, sample_crystal
    ) -> None:
        crystal = sample_crystal(layer="semantic")
        tmp_relational_store.insert_crystal(crystal)
        retrieved = tmp_relational_store.get_crystal(crystal["id"])
        assert isinstance(retrieved["scope"], dict)
        assert retrieved["scope"]["project"] == "test-project"

    def test_utility_json_round_trip(
        self, tmp_relational_store: RelationalStore, sample_crystal
    ) -> None:
        crystal = sample_crystal(layer="episodic")
        tmp_relational_store.insert_crystal(crystal)
        retrieved = tmp_relational_store.get_crystal(crystal["id"])
        assert isinstance(retrieved["utility"], dict)
        assert retrieved["utility"]["access_count"] == 3


class TestBM25Search:
    def test_bm25_search_returns_hit(
        self, tmp_relational_store: RelationalStore, sample_crystal
    ) -> None:
        crystal = sample_crystal(
            layer="semantic",
            summary="Conventional Commits are required for all pull requests.",
        )
        tmp_relational_store.insert_crystal(crystal)
        results = tmp_relational_store.bm25_search("Conventional Commits", k=5)
        assert len(results) >= 1
        ids = [r["id"] for r in results]
        assert crystal["id"] in ids

    def test_bm25_search_with_layer_filter(
        self, tmp_relational_store: RelationalStore, sample_crystal
    ) -> None:
        sem = sample_crystal(layer="semantic", summary="project uses Python 3.12")
        epi = sample_crystal(layer="episodic", summary="project uses Python 3.12")
        tmp_relational_store.insert_crystal(sem)
        tmp_relational_store.insert_crystal(epi)
        results = tmp_relational_store.bm25_search("Python", layer_filter="semantic", k=5)
        ids = [r["id"] for r in results]
        # Battle-test fix (low): the old test only looped over results — it passed
        # vacuously if the filter over-filtered to []. Assert the filter actually
        # returns the semantic row AND excludes the episodic one, and that both are
        # present without the filter (so we know the exclusion is the filter's doing).
        assert len(results) >= 1
        assert sem["id"] in ids
        assert epi["id"] not in ids
        for r in results:
            assert r["layer"] == "semantic"
        unfiltered_ids = [r["id"] for r in tmp_relational_store.bm25_search("Python", k=5)]
        assert epi["id"] in unfiltered_ids

    def test_bm25_search_no_results_on_empty(
        self, tmp_relational_store: RelationalStore
    ) -> None:
        results = tmp_relational_store.bm25_search("completely nonexistent term xyz123", k=5)
        assert results == []

    def test_bm25_search_multiple_crystals_ordered(
        self, tmp_relational_store: RelationalStore, sample_crystal
    ) -> None:
        for i in range(5):
            c = sample_crystal(layer="semantic", summary=f"API endpoint /v{i} uses REST")
            tmp_relational_store.insert_crystal(c)
        results = tmp_relational_store.bm25_search("API REST endpoint", k=10)
        assert len(results) >= 1


class TestBiTemporalSupersession:
    def test_mark_superseded_sets_t_valid_to_and_superseded_by(
        self, tmp_relational_store: RelationalStore, sample_crystal
    ) -> None:
        old = sample_crystal(layer="semantic", crystal_id="old-001")
        new = sample_crystal(layer="semantic", crystal_id="new-001")
        tmp_relational_store.insert_crystal(old)
        tmp_relational_store.insert_crystal(new)

        t_valid_to = datetime(2026, 5, 28, 12, 0, 0, tzinfo=timezone.utc)
        tmp_relational_store.mark_superseded("old-001", "new-001", t_valid_to)

        retrieved = tmp_relational_store.get_crystal("old-001")
        assert retrieved["temporal"]["t_valid_to"] is not None
        assert "2026-05-28" in retrieved["temporal"]["t_valid_to"]
        assert retrieved["temporal"]["superseded_by"] == "new-001"

    def test_mark_superseded_raises_for_missing_crystal(
        self, tmp_relational_store: RelationalStore
    ) -> None:
        t = datetime.now(timezone.utc)
        with pytest.raises(KeyError, match="nonexistent"):
            tmp_relational_store.mark_superseded("nonexistent", "new-id", t)

    def test_new_crystal_not_affected_by_supersession(
        self, tmp_relational_store: RelationalStore, sample_crystal
    ) -> None:
        old = sample_crystal(layer="semantic", crystal_id="sup-old")
        new = sample_crystal(layer="semantic", crystal_id="sup-new")
        tmp_relational_store.insert_crystal(old)
        tmp_relational_store.insert_crystal(new)
        tmp_relational_store.mark_superseded("sup-old", "sup-new", datetime.now(timezone.utc))

        new_retrieved = tmp_relational_store.get_crystal("sup-new")
        assert new_retrieved["temporal"]["t_valid_to"] is None
        assert new_retrieved["temporal"]["superseded_by"] is None


class TestPendingPromotions:
    def test_insert_and_list_pending(self, tmp_relational_store: RelationalStore, sample_crystal) -> None:
        crystal = sample_crystal(layer="episodic")
        tmp_relational_store.insert_crystal(crystal)
        promo_id = str(uuid.uuid4())
        tmp_relational_store.insert_pending_promotion(
            promotion_id=promo_id,
            crystal_id=crystal["id"],
            target_layer="semantic",
            proposed_at=datetime.now(timezone.utc),
        )
        promotions = tmp_relational_store.list_pending_promotions()
        assert any(p["id"] == promo_id for p in promotions)

    def test_update_promotion_status(self, tmp_relational_store: RelationalStore, sample_crystal) -> None:
        crystal = sample_crystal(layer="episodic")
        tmp_relational_store.insert_crystal(crystal)
        promo_id = str(uuid.uuid4())
        tmp_relational_store.insert_pending_promotion(
            promo_id, crystal["id"], "semantic", datetime.now(timezone.utc)
        )
        tmp_relational_store.update_promotion_status(promo_id, "accepted")
        # Accepted promotions should no longer appear in list_pending_promotions
        pending = tmp_relational_store.list_pending_promotions()
        assert all(p["id"] != promo_id for p in pending)


class TestTelemetrySink:
    def test_record_tool_call_does_not_raise(self, tmp_relational_store: RelationalStore) -> None:
        # Should not raise
        tmp_relational_store.record_tool_call(
            tool="crystalium.recall",
            layer="semantic",
            tier="T1",
            op="recall",
            result="ok",
            latency_ms=12.5,
            overflow=False,
            error=None,
        )


# ---------------------------------------------------------------------------
# W-GE1: list_for_export / count_for_export enumerator tests
# ---------------------------------------------------------------------------


class TestListForExport:
    """Tests for RelationalStore.list_for_export and count_for_export (GAP-2, W-GE1)."""

    def _insert(self, store: RelationalStore, crystal: dict) -> dict:
        store.insert_crystal(crystal)
        return crystal

    def test_max_export_nodes_constant(self) -> None:
        """MAX_EXPORT_NODES must equal 10000 (FINDING-010)."""
        assert MAX_EXPORT_NODES == 10_000

    def test_list_returns_active_crystals(
        self, tmp_relational_store: RelationalStore, sample_crystal
    ) -> None:
        c = self._insert(tmp_relational_store, sample_crystal(layer="semantic"))
        results = tmp_relational_store.list_for_export(c["scope"]["project"])
        ids = [r["id"] for r in results]
        assert c["id"] in ids

    def test_list_excludes_deprecated_by_default(
        self, tmp_relational_store: RelationalStore, sample_crystal
    ) -> None:
        c = self._insert(tmp_relational_store, sample_crystal(layer="semantic"))
        tmp_relational_store.set_status(c["id"], "deprecated")
        results = tmp_relational_store.list_for_export(c["scope"]["project"])
        assert c["id"] not in [r["id"] for r in results]

    def test_list_includes_deprecated_with_flag(
        self, tmp_relational_store: RelationalStore, sample_crystal
    ) -> None:
        c = self._insert(tmp_relational_store, sample_crystal(layer="semantic"))
        tmp_relational_store.set_status(c["id"], "deprecated")
        results = tmp_relational_store.list_for_export(
            c["scope"]["project"], include_deprecated=True
        )
        assert c["id"] in [r["id"] for r in results]

    def test_list_excludes_quarantined_by_default(
        self, tmp_relational_store: RelationalStore, sample_crystal
    ) -> None:
        c = self._insert(tmp_relational_store, sample_crystal(layer="semantic", validation_state="quarantined"))
        results = tmp_relational_store.list_for_export(c["scope"]["project"])
        assert c["id"] not in [r["id"] for r in results]

    def test_list_includes_quarantined_with_flag(
        self, tmp_relational_store: RelationalStore, sample_crystal
    ) -> None:
        c = self._insert(tmp_relational_store, sample_crystal(layer="semantic", validation_state="quarantined"))
        results = tmp_relational_store.list_for_export(
            c["scope"]["project"], include_quarantined=True
        )
        assert c["id"] in [r["id"] for r in results]

    def test_list_excludes_superseded_by_default(
        self, tmp_relational_store: RelationalStore, sample_crystal
    ) -> None:
        from datetime import datetime, timezone
        c_old = self._insert(tmp_relational_store, sample_crystal(layer="semantic"))
        c_new = self._insert(tmp_relational_store, sample_crystal(layer="semantic"))
        tmp_relational_store.mark_superseded(c_old["id"], c_new["id"], datetime.now(timezone.utc))
        results = tmp_relational_store.list_for_export(c_old["scope"]["project"])
        ids = [r["id"] for r in results]
        assert c_old["id"] not in ids
        assert c_new["id"] in ids

    def test_list_includes_superseded_with_flag(
        self, tmp_relational_store: RelationalStore, sample_crystal
    ) -> None:
        from datetime import datetime, timezone
        c_old = self._insert(tmp_relational_store, sample_crystal(layer="semantic"))
        c_new = self._insert(tmp_relational_store, sample_crystal(layer="semantic"))
        tmp_relational_store.mark_superseded(c_old["id"], c_new["id"], datetime.now(timezone.utc))
        results = tmp_relational_store.list_for_export(
            c_old["scope"]["project"], include_superseded=True
        )
        ids = [r["id"] for r in results]
        assert c_old["id"] in ids

    def test_list_scoped_to_project(
        self, tmp_relational_store: RelationalStore, sample_crystal
    ) -> None:
        """list_for_export must not return crystals from a different project."""
        c_a = self._insert(tmp_relational_store, sample_crystal(layer="semantic"))
        # Manually insert a crystal in a different project
        import uuid as _uuid
        import json
        c_b_id = str(_uuid.uuid4())
        from crystalium.storage.relational import _to_json
        c_b = sample_crystal(layer="semantic", crystal_id=c_b_id)
        c_b["scope"] = {"project": "other-project", "agent_class_visibility": None, "sensitivity_tag": "none"}
        tmp_relational_store.insert_crystal(c_b)
        results = tmp_relational_store.list_for_export(c_a["scope"]["project"])
        ids = [r["id"] for r in results]
        assert c_a["id"] in ids
        assert c_b_id not in ids

    def test_limit_clamped_to_max_export_nodes(
        self, tmp_relational_store: RelationalStore, sample_crystal
    ) -> None:
        """Passing limit > MAX_EXPORT_NODES must be clamped to MAX_EXPORT_NODES."""
        c = self._insert(tmp_relational_store, sample_crystal(layer="semantic"))
        # Pass an absurdly large limit; should not error and should return the crystal
        results = tmp_relational_store.list_for_export(
            c["scope"]["project"], limit=99_999
        )
        assert len(results) <= MAX_EXPORT_NODES

    def test_pagination_via_offset(
        self, tmp_relational_store: RelationalStore, sample_crystal
    ) -> None:
        """Offset pagination returns the second page."""
        project = "page-test-project"
        inserted = []
        for _ in range(3):
            c = sample_crystal(layer="semantic")
            c["scope"] = {"project": project, "agent_class_visibility": None, "sensitivity_tag": "none"}
            tmp_relational_store.insert_crystal(c)
            inserted.append(c["id"])
        page1 = tmp_relational_store.list_for_export(project, limit=2, offset=0)
        page2 = tmp_relational_store.list_for_export(project, limit=2, offset=2)
        # Page1 has 2 results; page2 has 1
        assert len(page1) == 2
        assert len(page2) == 1
        # No overlap
        ids1 = {r["id"] for r in page1}
        ids2 = {r["id"] for r in page2}
        assert ids1.isdisjoint(ids2)

    def test_layer_filter(
        self, tmp_relational_store: RelationalStore, sample_crystal
    ) -> None:
        project = "layer-filter-project"
        sem = sample_crystal(layer="semantic")
        sem["scope"]["project"] = project
        tmp_relational_store.insert_crystal(sem)
        ep = sample_crystal(layer="episodic")
        ep["scope"]["project"] = project
        tmp_relational_store.insert_crystal(ep)
        results = tmp_relational_store.list_for_export(project, layers=["semantic"])
        assert all(r["layer"] == "semantic" for r in results)

    def test_visibility_filter(
        self, tmp_relational_store: RelationalStore, sample_crystal
    ) -> None:
        """Crystals with a different agent_class_visibility must be excluded."""
        project = "vis-filter-project"
        c_public = sample_crystal(layer="semantic")
        c_public["scope"]["project"] = project
        c_public["scope"]["agent_class_visibility"] = None
        tmp_relational_store.insert_crystal(c_public)
        c_forge = sample_crystal(layer="semantic")
        c_forge["scope"]["project"] = project
        c_forge["scope"]["agent_class_visibility"] = "forge"
        tmp_relational_store.insert_crystal(c_forge)
        # Query as "spectra" — should see public (null) but not forge-only
        results = tmp_relational_store.list_for_export(
            project, agent_class_visibility="spectra"
        )
        ids = [r["id"] for r in results]
        assert c_public["id"] in ids
        assert c_forge["id"] not in ids

    def test_count_for_export_matches_list(
        self, tmp_relational_store: RelationalStore, sample_crystal
    ) -> None:
        project = "count-test-project"
        for _ in range(3):
            c = sample_crystal(layer="semantic")
            c["scope"]["project"] = project
            tmp_relational_store.insert_crystal(c)
        count = tmp_relational_store.count_for_export(project)
        listed = tmp_relational_store.list_for_export(project, limit=MAX_EXPORT_NODES)
        assert count == len(listed)

    def test_count_respects_same_filters_as_list(
        self, tmp_relational_store: RelationalStore, sample_crystal
    ) -> None:
        """count_for_export with include_quarantined=True > without."""
        project = "count-filter-project"
        c_active = sample_crystal(layer="semantic")
        c_active["scope"]["project"] = project
        tmp_relational_store.insert_crystal(c_active)
        c_quar = sample_crystal(layer="semantic", validation_state="quarantined")
        c_quar["scope"]["project"] = project
        tmp_relational_store.insert_crystal(c_quar)
        count_default = tmp_relational_store.count_for_export(project)
        count_with_quar = tmp_relational_store.count_for_export(project, include_quarantined=True)
        assert count_with_quar > count_default
