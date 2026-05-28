"""Storage adapters for CRYSTALIUM.

Four adapters implement the index→pointer→content pattern (MISSION.md §Index→pointer→content):

  BlobStore    — content-addressed immutable byte payloads (SHA-256 keyed filesystem)
  RelationalStore — SQLite + FTS5 (index metadata, BM25 search, pending promotions, telemetry)
  VectorStore  — LanceDB (vector embeddings, dense ANN search)
  GraphStore   — KuzuDB (crystal relationship graph, neighbor expansion)

All heavy reads/writes go through these; layers/* call into adapters only,
never directly to the filesystem (except BlobStore by design).
"""

from crystalium.storage.blob import BlobStore
from crystalium.storage.graph import GraphStore
from crystalium.storage.relational import RelationalStore
from crystalium.storage.vector import VectorStore

__all__ = ["BlobStore", "RelationalStore", "VectorStore", "GraphStore"]
