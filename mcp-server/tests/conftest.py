"""Shared pytest fixtures for CRYSTALIUM Wave 1 tests.

Container-first: these tests are run via:
  docker compose run --rm crystalium pytest mcp-server/tests/ -v
NOT via host Python/pytest directly.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import pytest

from crystalium.storage.blob import BlobStore
from crystalium.storage.relational import RelationalStore


# ---------------------------------------------------------------------------
# W8 conformance registry — the test files whose green == "mechanically
# conformant" (G1-G8 + path-escape/rate-limit/output-cap/never-hard-delete +
# RTBF exception/working-set cap). `pytest -m conformance` runs exactly these.
# Adding/removing a gate file here is the single source of truth; test_conformance.py
# cross-checks that every G-gate id is represented.
# ---------------------------------------------------------------------------

CONFORMANCE_FILES = frozenset({
    "test_enforcement.py",       # G1, G2 + path-escape, rate-limit, output-cap, tier-ceiling, review-op
    "test_skill_invoke.py",      # G3 sandbox
    "test_trust_propagation.py", # G4 MIN-tier propagation
    "test_promotion_gate.py",    # G5 promotion / k-corroboration / human-confirm
    "test_composer.py",          # G6 working-set cap
    "test_ecl_envelope.py",      # G7 ECL 11-field + SHA-256 integrity
    "test_dream_scheduler.py",   # G8 dream dedup
    "test_bitemporal.py",        # never-hard-delete (P0-5)
    "test_rtbf.py",              # the ONE sanctioned hard-delete (RTBF exception)
})


def pytest_collection_modifyitems(config, items):  # noqa: ARG001
    """Auto-apply the `conformance` marker to every test in a gate file, so the
    mechanical-invariant suite is a single runnable target without per-test churn."""
    mark = pytest.mark.conformance
    for item in items:
        if Path(str(item.fspath)).name in CONFORMANCE_FILES:
            item.add_marker(mark)


# ---------------------------------------------------------------------------
# Storage fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_blob_store(tmp_path: Path) -> BlobStore:
    """BlobStore backed by a temporary directory."""
    return BlobStore(root=tmp_path / "blobs")


@pytest.fixture
def tmp_relational_store(tmp_path: Path) -> RelationalStore:
    """RelationalStore backed by a temporary SQLite database."""
    return RelationalStore(db_path=tmp_path / "test.sqlite")


@pytest.fixture
def tmp_vector_store(tmp_path: Path):
    """VectorStore backed by a temporary LanceDB directory.

    Importing sentence-transformers is slow; mark tests that use this
    with @pytest.mark.slow and set CRYSTALIUM_SKIP_SLOW=1 to skip.
    """
    from crystalium.storage.vector import VectorStore

    return VectorStore(lance_dir=tmp_path / "vectors.lance")


@pytest.fixture
def tmp_graph_store(tmp_path: Path):
    """GraphStore backed by a temporary KuzuDB directory."""
    from crystalium.storage.graph import GraphStore

    return GraphStore(kuzu_dir=tmp_path / "graph.kuzu")


# ---------------------------------------------------------------------------
# Time fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def frozen_now() -> datetime:
    """A fixed reference datetime (UTC) for deterministic time-based tests."""
    return datetime(2026, 5, 28, 12, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Crystal factory
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_crystal() -> Callable[..., dict]:
    """Factory fixture: sample_crystal(layer='semantic') → dict.

    Returns a dict that validates against crystal.v1.json for the given layer.
    """

    def _make(
        layer: str = "semantic",
        trust_tier: str = "T1",
        validation_state: str = "validated",
        crystal_id: str | None = None,
        summary: str | None = None,
        importance: float = 0.5,
        novelty_at_write: float = 0.7,
        access_count: int = 3,
        now: datetime | None = None,
    ) -> dict:
        if now is None:
            now = datetime.now(timezone.utc)
        cid = crystal_id or str(uuid.uuid4())
        base: dict = {
            "id": cid,
            "layer": layer,
            "summary": summary or f"Test crystal [{layer}] {cid[:8]}",
            "provenance": {
                "source": "verified_agent",
                "author_agent": "test-agent",
                "task_id": "test-task-001",
                "created_at": now.isoformat(),
            },
            "trust_tier": trust_tier,
            "validation_state": validation_state,
            "scope": {
                "project": "test-project",
                "agent_class_visibility": "all",
                "sensitivity_tag": "none",
            },
            "temporal": {
                "t_valid_from": now.isoformat(),
                "t_valid_to": None,
                "superseded_by": None,
            },
            "utility": {
                "access_count": access_count,
                "last_access": now.isoformat(),
                "outcome_success_score": None,
                "importance": importance,
                "novelty_at_write": novelty_at_write,
            },
            "status": "active",
        }
        # Episodic requires content_ref (P0 index→pointer→content)
        if layer == "episodic":
            # Generate a plausible fake SHA-256 (64 hex chars)
            import hashlib

            fake_content = f"test content for {cid}".encode()
            base["content_ref"] = hashlib.sha256(fake_content).hexdigest()

        return base

    return _make
