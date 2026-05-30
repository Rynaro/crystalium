"""Blob store — content-addressed, immutable, cheap filesystem tier.

Implements the index→pointer→content split described in MISSION.md
§Index→pointer→content (citing Teyler & DiScenna 1986).

Key design decisions:
- SHA-256 content-addressed: same bytes → same ID, deduplication automatic.
- Two-character prefix sharding: <root>/<2-char prefix>/<64-char hash>
  (matches git's object store layout for familiarity + avoids large directories).
- delete() raises NotImplementedError — P0: never hard-delete (MISSION.md §Updates
  are bi-temporal / Never hard-delete). Supersession via bi-temporal write is the
  only valid "removal" path.
- Sync-only I/O: blobs are written once and read many times; async is not needed
  and avoids aiofiles dependency.
"""

from __future__ import annotations

import hashlib
from pathlib import Path


class BlobStore:
    """Content-addressed immutable blob storage.

    Args:
        root: Root directory for blobs. Will be created on first put().
    """

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _blob_path(self, blob_id: str) -> Path:
        """Derive on-disk path from *blob_id* (64-char hex SHA-256).

        Layout: <root>/<2 char prefix>/<blob_id>
        """
        if len(blob_id) != 64:
            raise ValueError(f"blob_id must be a 64-char hex SHA-256 digest, got len={len(blob_id)}")
        prefix = blob_id[:2]
        return self.root / prefix / blob_id

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def put(self, data: bytes) -> str:
        """Write *data* to the blob store.

        Returns the SHA-256 hex digest (the blob ID).
        Idempotent: writing the same bytes twice returns the same ID and
        does not modify the stored content.
        """
        blob_id = hashlib.sha256(data).hexdigest()
        path = self._blob_path(blob_id)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        return blob_id

    def get(self, blob_id: str) -> bytes:
        """Read and return the blob identified by *blob_id*.

        Raises:
            FileNotFoundError: If the blob does not exist.
            ValueError: If blob_id is not a valid 64-char hex digest.
        """
        path = self._blob_path(blob_id)
        if not path.exists():
            raise FileNotFoundError(f"Blob not found: {blob_id!r}")
        return path.read_bytes()

    def exists(self, blob_id: str) -> bool:
        """Return True if a blob with *blob_id* exists in the store."""
        try:
            return self._blob_path(blob_id).exists()
        except ValueError:
            return False

    def delete(self, blob_id: str) -> None:  # noqa: ARG002
        """Raises NotImplementedError — blobs are immutable and never hard-deleted.

        P0 (MISSION.md §Never hard-delete): supersession via bi-temporal write is
        the only valid "removal" path.
        """
        raise NotImplementedError(
            "BlobStore.delete() is intentionally unimplemented. "
            "CRYSTALIUM never hard-deletes memory records (P0). "
            "Use bi-temporal supersession instead: "
            "set temporal.t_valid_to + temporal.superseded_by on the old crystal, "
            "then write the new crystal."
        )

    def tombstone(self, blob_id: str) -> bool:
        """Physically drop a blob — the W4 right-to-be-forgotten exception (P0).

        DISTINCT from delete() (which stays blocked): tombstone is reached ONLY
        via the operator-gated `forget` op after a forget_audit row is written.
        Returns True if a blob was removed, False if it was absent. Never raises
        on a missing/invalid id.
        """
        try:
            path = self._blob_path(blob_id)
        except ValueError:
            return False
        if path.exists():
            path.unlink()
            return True
        return False
