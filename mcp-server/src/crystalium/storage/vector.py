"""Vector store — LanceDB + sentence-transformers BGE-m3 for dense ANN search.

Implements the index portion of the index→pointer→content split for semantic
similarity recall. Only embeddings + minimal metadata are stored here;
full crystal payloads live in the blob tier.

Backends:
  sentence-transformers  — default; downloads BGE-m3 on first use (inside container).
  ollama                 — raises NotImplementedError for v0.1 (FORGE D2 precedent;
                           Ollama backend deferred unless trivial).

FINDING-004 caution: watch >10K-node (vector) telemetry; KuzuDB note also applies here.
[UNVERIFIED] LanceDB async API shape — verified against lancedb 0.6.x docs but
subject to minor version changes.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger("crystalium.storage.vector")

# LanceDB and numpy are heavy; only import when the store is instantiated.
# This keeps test discovery fast when these deps are unavailable.


class VectorStore:
    """LanceDB-backed vector store for crystal embeddings.

    Args:
        lance_dir:    Path to the LanceDB database directory. Created on first upsert.
        embed_backend: 'sentence-transformers' (default) or 'ollama' (NotImplementedError).
        model_name:   Embedding model name (default: 'BAAI/bge-m3').
    """

    _TABLE_NAME = "crystal_embeddings"
    _DEFAULT_MODEL = "BAAI/bge-m3"

    def __init__(
        self,
        lance_dir: Path,
        embed_backend: str = "sentence-transformers",
        model_name: str | None = None,
    ) -> None:
        self.lance_dir = lance_dir.resolve()
        self.embed_backend = embed_backend
        self.model_name = model_name or self._DEFAULT_MODEL
        self._model: Any = None  # lazy-loaded
        self._db: Any = None     # lazy-loaded lancedb connection
        self._table: Any = None  # lazy-loaded table
        self._embed_cache: dict[str, list[float]] = {}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_db(self) -> Any:
        if self._db is None:
            import lancedb  # type: ignore[import-untyped]

            self.lance_dir.mkdir(parents=True, exist_ok=True)
            self._db = lancedb.connect(str(self.lance_dir))
        return self._db

    def _get_table(self) -> Any:
        if self._table is None:
            db = self._get_db()
            try:
                self._table = db.open_table(self._TABLE_NAME)
            except Exception:
                # Table doesn't exist yet; will be created on first upsert.
                self._table = None
        return self._table

    def _load_model(self) -> Any:
        if self.embed_backend == "ollama":
            raise NotImplementedError(
                "Ollama embedding backend is not implemented in v0.1. "
                "Set embed_backend='sentence-transformers' (default). "
                "Ollama support is deferred to v0.2."
            )
        if self._model is None:
            # Skip heavy model download in CI/test environments if flagged
            if os.environ.get("CRYSTALIUM_SKIP_SLOW"):
                raise RuntimeError(
                    "CRYSTALIUM_SKIP_SLOW is set; skipping sentence-transformers model load. "
                    "Mark the calling test with @pytest.mark.slow."
                )
            from sentence_transformers import SentenceTransformer  # type: ignore[import-untyped]

            log.info("loading_embed_model", model=self.model_name)
            self._model = SentenceTransformer(self.model_name)
        return self._model

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def embed(self, text: str) -> list[float]:
        """Compute the embedding vector for *text*.

        Results are cached in-process (key = text) to avoid re-computing
        the same embedding on repeated commits.

        Args:
            text: Text to embed (crystal summary or query).

        Returns:
            List of floats (embedding dimension depends on model).
        """
        if text in self._embed_cache:
            return self._embed_cache[text]

        model = self._load_model()
        import numpy as np  # type: ignore[import-untyped]

        vector = model.encode(text, normalize_embeddings=True)
        if isinstance(vector, np.ndarray):
            vector = vector.tolist()
        self._embed_cache[text] = vector
        return vector

    def upsert(
        self,
        crystal_id: str,
        vector: list[float],
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Insert or update the embedding for *crystal_id*.

        Args:
            crystal_id: Crystal identifier (primary key in this table).
            vector:     Embedding vector.
            metadata:   Additional fields to store alongside the vector
                        (e.g. layer, trust_tier, importance).
        """
        import pyarrow as pa  # type: ignore[import-untyped]

        db = self._get_db()
        row: dict[str, Any] = {"id": crystal_id, "vector": vector}
        if metadata:
            row.update(metadata)

        table = self._get_table()
        if table is None:
            # First insert — create table with schema inferred from first row
            self._table = db.create_table(self._TABLE_NAME, data=[row])
        else:
            # Upsert (delete existing + insert new)
            try:
                table.delete(f"id = '{crystal_id}'")
            except Exception:
                pass
            table.add([row])

    def delete(self, crystal_id: str) -> None:
        """Physically remove the embedding for *crystal_id* (RTBF hard-delete).

        The right-to-be-forgotten erasure must leave no dense representation of the
        content on disk. No-op if the table or the row does not exist.
        """
        table = self._get_table()
        if table is None:
            return
        try:
            table.delete(f"id = '{crystal_id}'")
        except Exception as exc:  # noqa: BLE001
            log.warning("vector_delete_error", crystal_id=crystal_id, error=str(exc))

    def dense_search(
        self,
        query_vec: list[float],
        layer_filter: str | None = None,
        k: int = 10,
    ) -> list[dict[str, Any]]:
        """ANN search for the *k* nearest vectors to *query_vec*.

        Args:
            query_vec:    Query embedding (same model as upsert).
            layer_filter: Optional layer to restrict results.
            k:            Number of results to return.

        Returns:
            List of metadata dicts with '_distance' appended. The metric is pinned
            to COSINE (embeddings are L2-normalized at embed time), so for any row
            cosine_similarity == 1.0 - row['_distance'] (W5 dedup relies on this).
            Empty list if table does not exist yet.
        """
        table = self._get_table()
        if table is None:
            return []

        try:
            query = table.search(query_vec).metric("cosine").limit(k)
            if layer_filter:
                query = query.where(f"layer = '{layer_filter}'", prefilter=True)
            results = query.to_list()
        except Exception as exc:
            log.warning("dense_search_error", error=str(exc))
            return []

        return results
