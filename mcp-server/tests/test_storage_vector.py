"""Tests for VectorStore — LanceDB + sentence-transformers BGE-m3.

Container-first: run via:
  docker compose run --rm crystalium pytest mcp-server/tests/test_storage_vector.py -v

Slow tests (model download required) are marked @pytest.mark.slow.
Set CRYSTALIUM_SKIP_SLOW=1 to skip them during fast CI passes.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from crystalium.storage.vector import VectorStore


# ---------------------------------------------------------------------------
# Helper: build a fake embedding without loading the model
# ---------------------------------------------------------------------------


def _fake_vec(dim: int = 8) -> list[float]:
    """Tiny deterministic fake embedding for unit tests."""
    return [float(i) / dim for i in range(dim)]


# ---------------------------------------------------------------------------
# Tests that do NOT require model download
# ---------------------------------------------------------------------------


class TestVectorStoreUnit:
    """Tests using fake vectors — no sentence-transformers model required."""

    def test_upsert_and_dense_search(self, tmp_vector_store: VectorStore) -> None:
        vec = _fake_vec()
        tmp_vector_store.upsert("crystal-1", vec, {"layer": "semantic", "importance": 0.8})
        tmp_vector_store.upsert("crystal-2", _fake_vec(8), {"layer": "semantic"})

        results = tmp_vector_store.dense_search(vec, k=5)
        assert len(results) >= 1
        ids = [r["id"] for r in results]
        assert "crystal-1" in ids

    def test_upsert_is_idempotent(self, tmp_vector_store: VectorStore) -> None:
        """Upserting the same crystal_id twice should not duplicate."""
        vec = _fake_vec()
        tmp_vector_store.upsert("cryst-idem", vec, {"layer": "episodic"})
        tmp_vector_store.upsert("cryst-idem", vec, {"layer": "episodic"})
        results = tmp_vector_store.dense_search(vec, k=10)
        ids = [r["id"] for r in results]
        assert ids.count("cryst-idem") == 1

    def test_dense_search_returns_empty_on_empty_store(
        self, tmp_vector_store: VectorStore
    ) -> None:
        results = tmp_vector_store.dense_search(_fake_vec(), k=5)
        assert results == []

    def test_layer_filter_in_dense_search(self, tmp_vector_store: VectorStore) -> None:
        vec = _fake_vec()
        tmp_vector_store.upsert("sem-1", vec, {"layer": "semantic"})
        tmp_vector_store.upsert("epi-1", vec, {"layer": "episodic"})
        results = tmp_vector_store.dense_search(vec, layer_filter="semantic", k=10)
        for r in results:
            assert r.get("layer") == "semantic"


class TestVectorStoreEmbedCache:
    @pytest.mark.slow
    def test_embed_cache_hits_on_repeat(self, tmp_vector_store: VectorStore) -> None:
        """embed() should cache results so calling it twice returns the same object."""
        if os.environ.get("CRYSTALIUM_SKIP_SLOW"):
            pytest.skip("CRYSTALIUM_SKIP_SLOW is set; skipping model-download test")
        text = "project uses Conventional Commits"
        vec1 = tmp_vector_store.embed(text)
        vec2 = tmp_vector_store.embed(text)
        assert vec1 is vec2  # same object from cache

    @pytest.mark.slow
    def test_embed_returns_non_empty_vector(self, tmp_vector_store: VectorStore) -> None:
        if os.environ.get("CRYSTALIUM_SKIP_SLOW"):
            pytest.skip("CRYSTALIUM_SKIP_SLOW is set; skipping model-download test")
        vec = tmp_vector_store.embed("test sentence")
        assert len(vec) > 0
        assert all(isinstance(v, float) for v in vec)


class TestOllamaBackendNotImplemented:
    def test_ollama_raises_not_implemented(self, tmp_path: Path) -> None:
        store = VectorStore(lance_dir=tmp_path / "v", embed_backend="ollama")
        with pytest.raises(NotImplementedError, match="v0.2"):
            store.embed("test")
