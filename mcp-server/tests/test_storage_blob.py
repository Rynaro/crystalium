"""Tests for BlobStore — content-addressed immutable filesystem tier.

Container-first: run via:
  docker compose run --rm crystalium pytest mcp-server/tests/test_storage_blob.py -v
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from crystalium.storage.blob import BlobStore


class TestBlobStoreBasics:
    def test_put_returns_sha256(self, tmp_blob_store: BlobStore) -> None:
        data = b"hello, crystalium"
        blob_id = tmp_blob_store.put(data)
        expected = hashlib.sha256(data).hexdigest()
        assert blob_id == expected

    def test_get_round_trip(self, tmp_blob_store: BlobStore) -> None:
        data = b"some memory payload"
        blob_id = tmp_blob_store.put(data)
        retrieved = tmp_blob_store.get(blob_id)
        assert retrieved == data

    def test_same_bytes_same_hash(self, tmp_blob_store: BlobStore) -> None:
        """Idempotent: putting the same bytes twice returns the same ID."""
        data = b"deterministic content"
        id1 = tmp_blob_store.put(data)
        id2 = tmp_blob_store.put(data)
        assert id1 == id2

    def test_different_bytes_different_hash(self, tmp_blob_store: BlobStore) -> None:
        id1 = tmp_blob_store.put(b"content A")
        id2 = tmp_blob_store.put(b"content B")
        assert id1 != id2

    def test_exists_returns_true_after_put(self, tmp_blob_store: BlobStore) -> None:
        data = b"exists check"
        blob_id = tmp_blob_store.put(data)
        assert tmp_blob_store.exists(blob_id) is True

    def test_exists_returns_false_for_missing(self, tmp_blob_store: BlobStore) -> None:
        fake_id = hashlib.sha256(b"not stored").hexdigest()
        assert tmp_blob_store.exists(fake_id) is False

    def test_get_raises_for_missing_blob(self, tmp_blob_store: BlobStore) -> None:
        fake_id = hashlib.sha256(b"missing").hexdigest()
        with pytest.raises(FileNotFoundError, match=fake_id):
            tmp_blob_store.get(fake_id)

    def test_delete_raises_not_implemented(self, tmp_blob_store: BlobStore) -> None:
        """P0: never hard-delete. delete() must raise NotImplementedError."""
        blob_id = tmp_blob_store.put(b"should not be deleted")
        with pytest.raises(NotImplementedError, match="never hard-delete"):
            tmp_blob_store.delete(blob_id)

    def test_blob_stored_with_prefix_sharding(self, tmp_blob_store: BlobStore) -> None:
        """On-disk layout: <root>/<2-char prefix>/<64-char hash>."""
        data = b"layout test"
        blob_id = tmp_blob_store.put(data)
        expected_path = tmp_blob_store.root / blob_id[:2] / blob_id
        assert expected_path.exists()

    def test_empty_bytes_stored(self, tmp_blob_store: BlobStore) -> None:
        blob_id = tmp_blob_store.put(b"")
        assert tmp_blob_store.exists(blob_id)
        assert tmp_blob_store.get(blob_id) == b""

    def test_large_payload(self, tmp_blob_store: BlobStore) -> None:
        data = b"x" * (1024 * 1024)  # 1 MiB
        blob_id = tmp_blob_store.put(data)
        retrieved = tmp_blob_store.get(blob_id)
        assert retrieved == data
        assert len(retrieved) == 1024 * 1024
