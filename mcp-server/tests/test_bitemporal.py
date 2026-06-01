"""Wave 2 bi-temporal integrity tests — P0-5.

Container-first: run via:
  docker compose run --rm crystalium pytest mcp-server/tests/test_bitemporal.py -v

Coverage:
  - Update flow: old crystal t_valid_to set + superseded_by populated
  - New crystal t_valid_from set correctly
  - Hard-delete raises (BlobStore.delete + Crystal hard-delete forbidden)
  - RelationalStore.mark_superseded preserves old record (P0-5)
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from crystalium.config import Config
from crystalium.enforcement import Enforcement
from crystalium.storage.blob import BlobStore
from crystalium.storage.relational import RelationalStore
from crystalium.trust import Tier


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_relational(tmp_path: Path) -> RelationalStore:
    return RelationalStore(db_path=tmp_path / "bitemporal.sqlite")


@pytest.fixture
def tmp_blob(tmp_path: Path) -> BlobStore:
    return BlobStore(root=tmp_path / "blobs")


@pytest.fixture
def enforcement() -> Enforcement:
    return Enforcement(Config(rate_limit_per_minute=200))


def _insert_crystal(relational: RelationalStore, crystal_id: str, layer: str = "semantic") -> dict:
    """Helper: insert a minimal crystal and return it."""
    now = datetime.now(timezone.utc)
    crystal = {
        "id": crystal_id,
        "layer": layer,
        "content_ref": "a" * 64,
        "summary": f"test crystal {crystal_id}",
        "embedding_ref": None,
        "provenance": {
            "source": "verified_agent",
            "author_agent": "test-agent",
            "task_id": None,
            "created_at": now.isoformat(),
        },
        "trust_tier": "T1",
        "validation_state": "validated",
        "scope": {"project": "test", "agent_class_visibility": None, "sensitivity_tag": "none"},
        "temporal": {
            "t_valid_from": now.isoformat(),
            "t_valid_to": None,
            "superseded_by": None,
        },
        "utility": {
            "access_count": 0,
            "last_access": now.isoformat(),
            "outcome_success_score": None,
            "importance": 0.5,
            "novelty_at_write": 0.5,
        },
        "status": "active",
    }
    relational.insert_crystal(crystal)
    return crystal


# ---------------------------------------------------------------------------
# Bi-temporal supersession (P0-5)
# ---------------------------------------------------------------------------


class TestBiTemporalSupersession:
    """Verify the invalidate-old + write-new pattern (P0-5)."""

    def test_mark_superseded_sets_t_valid_to(
        self, tmp_relational: RelationalStore
    ) -> None:
        """mark_superseded sets t_valid_to and superseded_by on old crystal."""
        _insert_crystal(tmp_relational, "old-001")
        now = datetime.now(timezone.utc)

        tmp_relational.mark_superseded("old-001", "new-001", now)

        old = tmp_relational.get_crystal("old-001")
        assert old is not None

        temporal = old["temporal"]
        assert temporal["t_valid_to"] is not None, "t_valid_to must be set after supersession"
        assert temporal["superseded_by"] == "new-001", "superseded_by must point to new crystal"

    def test_mark_superseded_preserves_old_record(
        self, tmp_relational: RelationalStore
    ) -> None:
        """Old crystal still exists after supersession (never hard-deleted)."""
        _insert_crystal(tmp_relational, "old-002")
        now = datetime.now(timezone.utc)

        tmp_relational.mark_superseded("old-002", "new-002", now)

        # Old record MUST still exist
        old = tmp_relational.get_crystal("old-002")
        assert old is not None, "Old crystal must NOT be hard-deleted after supersession (P0-5)"

    def test_superseded_crystal_has_valid_to_set(
        self, tmp_relational: RelationalStore
    ) -> None:
        """After supersession, t_valid_to on old crystal matches the invalidation time."""
        _insert_crystal(tmp_relational, "old-003")
        invalidation_time = datetime(2026, 5, 28, 12, 0, 0, tzinfo=timezone.utc)

        tmp_relational.mark_superseded("old-003", "new-003", invalidation_time)

        old = tmp_relational.get_crystal("old-003")
        temporal = old["temporal"]
        t_valid_to = datetime.fromisoformat(temporal["t_valid_to"])
        # Should be close to invalidation_time (within a few seconds)
        assert abs((t_valid_to - invalidation_time).total_seconds()) < 5

    def test_update_via_semantic_layer(
        self,
        tmp_relational: RelationalStore,
        tmp_blob: BlobStore,
        enforcement: Enforcement,
    ) -> None:
        """SemanticLayer.update: old crystal gets t_valid_to + superseded_by; new
        crystal gets t_valid_from, status=active, and trust_tier re-pinned to the
        caller (battle-test: this test was previously abandoned with zero assertions)."""
        from datetime import datetime, timezone

        from crystalium.aetheryte.redact import Redactor
        from crystalium.gate import PromotionGate
        from crystalium.importance import importance_score
        from crystalium.layers.semantic import SemanticLayer

        config = Config(rate_limit_per_minute=200)
        gate = PromotionGate(config=config, relational=tmp_relational, enforcement=enforcement)
        layer = SemanticLayer(
            blob_store=tmp_blob, relational=tmp_relational, vector_store=None,
            graph_store=None, enforcement=enforcement, gate=gate,
            redactor=Redactor(config), importance_fn=importance_score,
        )

        # Seed an existing active semantic crystal directly (bypass the promotion dance —
        # this test is about the UPDATE bi-temporal mechanics, not admission).
        now_iso = datetime(2026, 5, 28, 12, 0, tzinfo=timezone.utc).isoformat()
        tmp_relational.insert_crystal({
            "id": "sem-1", "layer": "semantic", "trust_tier": "T1",
            "validation_state": "validated", "status": "active", "summary": "original fact",
            "scope": {"project": "test"},
            "provenance": {"source": "verified_agent", "created_at": now_iso},
            "utility": {"importance": 0.0}, "temporal": {"t_valid_from": now_iso},
        })

        res = layer.update(record_id="sem-1", patch={"summary": "revised fact"},
                           reason="correction", caller_tier=Tier.T1)
        new_id = res["id"]
        assert new_id != "sem-1"

        old = tmp_relational.get_crystal("sem-1")
        new = tmp_relational.get_crystal(new_id)
        old_temporal = old["temporal"] if isinstance(old["temporal"], dict) else __import__("json").loads(old["temporal"])
        new_temporal = new["temporal"] if isinstance(new["temporal"], dict) else __import__("json").loads(new["temporal"])

        assert old_temporal.get("t_valid_to") is not None          # old closed
        assert old_temporal.get("superseded_by") == new_id
        assert new_temporal.get("t_valid_from") is not None        # new opened
        assert new_temporal.get("t_valid_to") is None
        assert new["status"] == "active"
        assert new["summary"] == "revised fact"
        assert new["trust_tier"] == "T1"                           # re-pinned to caller

    def test_update_via_relational_directly(
        self,
        tmp_relational: RelationalStore,
    ) -> None:
        """Direct relational mark_superseded + insert_new flow matches bi-temporal contract."""
        # Insert old
        _insert_crystal(tmp_relational, "crystal-A", "semantic")
        now = datetime.now(timezone.utc)

        # Insert new
        new_crystal = _insert_crystal.__wrapped__({}) if False else None
        new_crystal = {
            "id": "crystal-B",
            "layer": "semantic",
            "content_ref": "b" * 64,
            "summary": "updated fact",
            "embedding_ref": None,
            "provenance": {
                "source": "verified_agent",
                "author_agent": "test-agent",
                "task_id": None,
                "created_at": now.isoformat(),
            },
            "trust_tier": "T1",
            "validation_state": "validated",
            "scope": {"project": "test", "agent_class_visibility": None, "sensitivity_tag": "none"},
            "temporal": {
                "t_valid_from": now.isoformat(),
                "t_valid_to": None,
                "superseded_by": None,
            },
            "utility": {
                "access_count": 0,
                "last_access": now.isoformat(),
                "outcome_success_score": None,
                "importance": 0.5,
                "novelty_at_write": 0.5,
            },
            "status": "active",
        }

        # Bi-temporal flow
        tmp_relational.mark_superseded("crystal-A", "crystal-B", now)
        tmp_relational.insert_crystal(new_crystal)

        # Verify: old has t_valid_to
        old = tmp_relational.get_crystal("crystal-A")
        assert old["temporal"]["t_valid_to"] is not None
        assert old["temporal"]["superseded_by"] == "crystal-B"

        # Verify: new has t_valid_from and no t_valid_to
        new = tmp_relational.get_crystal("crystal-B")
        assert new is not None
        assert new["temporal"]["t_valid_to"] is None
        assert new["temporal"]["superseded_by"] is None

    def test_mark_superseded_unknown_id_raises(
        self, tmp_relational: RelationalStore
    ) -> None:
        """mark_superseded on unknown crystal_id raises KeyError."""
        with pytest.raises(KeyError):
            tmp_relational.mark_superseded(
                "does-not-exist", "new-id", datetime.now(timezone.utc)
            )


# ---------------------------------------------------------------------------
# Hard-delete is forbidden (P0-5)
# ---------------------------------------------------------------------------


class TestHardDeleteForbidden:
    def test_blob_store_delete_raises_not_implemented(
        self, tmp_blob: BlobStore
    ) -> None:
        """BlobStore.delete() raises NotImplementedError (P0: never hard-delete)."""
        # Put something first so blob_id is valid
        blob_id = tmp_blob.put(b"test content")

        with pytest.raises(NotImplementedError) as exc_info:
            tmp_blob.delete(blob_id)

        assert "hard-delete" in str(exc_info.value).lower() or "P0" in str(exc_info.value) or "bi-temporal" in str(exc_info.value).lower()

    def test_blob_store_delete_raises_on_nonexistent(
        self, tmp_blob: BlobStore
    ) -> None:
        """BlobStore.delete() raises NotImplementedError even for non-existent blobs."""
        with pytest.raises(NotImplementedError):
            tmp_blob.delete("a" * 64)


# ---------------------------------------------------------------------------
# t_valid_from/t_valid_to semantics
# ---------------------------------------------------------------------------


class TestBiTemporalSemantics:
    def test_new_crystal_has_no_t_valid_to(
        self, tmp_relational: RelationalStore
    ) -> None:
        """Newly inserted crystal has t_valid_to=None (currently valid)."""
        _insert_crystal(tmp_relational, "fresh-001")
        crystal = tmp_relational.get_crystal("fresh-001")
        assert crystal["temporal"]["t_valid_to"] is None

    def test_superseded_crystal_is_no_longer_valid(
        self, tmp_relational: RelationalStore
    ) -> None:
        """A superseded crystal has t_valid_to set; it is no longer the current version."""
        _insert_crystal(tmp_relational, "live-001")
        now = datetime.now(timezone.utc)
        tmp_relational.mark_superseded("live-001", "live-002", now)

        crystal = tmp_relational.get_crystal("live-001")
        assert crystal["temporal"]["t_valid_to"] is not None
        # It should not be None — the record is invalidated
        t_valid_to = datetime.fromisoformat(crystal["temporal"]["t_valid_to"])
        assert t_valid_to <= datetime.now(timezone.utc)

    def test_superseded_by_chain(
        self, tmp_relational: RelationalStore
    ) -> None:
        """Chain A → B → C: each step sets superseded_by correctly."""
        now = datetime.now(timezone.utc)
        _insert_crystal(tmp_relational, "chain-A")
        _insert_crystal(tmp_relational, "chain-B")
        _insert_crystal(tmp_relational, "chain-C")

        tmp_relational.mark_superseded("chain-A", "chain-B", now)
        tmp_relational.mark_superseded("chain-B", "chain-C", now)

        a = tmp_relational.get_crystal("chain-A")
        b = tmp_relational.get_crystal("chain-B")
        c = tmp_relational.get_crystal("chain-C")

        assert a["temporal"]["superseded_by"] == "chain-B"
        assert b["temporal"]["superseded_by"] == "chain-C"
        assert c["temporal"]["superseded_by"] is None  # C is current
