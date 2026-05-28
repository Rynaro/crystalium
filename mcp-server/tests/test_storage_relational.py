"""Tests for RelationalStore — SQLite + FTS5 index, BM25 search, promotions, telemetry.

Container-first: run via:
  docker compose run --rm crystalium pytest mcp-server/tests/test_storage_relational.py -v
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from crystalium.storage.relational import RelationalStore


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
        for r in results:
            assert r["layer"] == "semantic"

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
