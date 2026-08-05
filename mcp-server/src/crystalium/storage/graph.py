"""Graph store — KuzuDB for crystal relationship graph.

Stores crystal nodes and typed edges (LINKS_TO, SUPERSEDES, CITES).
Used by the Aetheryte for context-aware neighbor expansion during recall.

FINDING-004 caution: log a WARN when node_count > 10_000. KuzuDB may
exhibit performance degradation at large scale; this is a v0.1 acceptance
criterion (ops team must plan migration if graph grows past this threshold).

Design:
  Nodes:  (:Crystal {id: string, layer: string})
  Edges:  (:Crystal)-[:LINKS_TO|SUPERSEDES|CITES]->(:Crystal)
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger("crystalium.storage.graph")

VALID_RELATIONS: frozenset[str] = frozenset({"LINKS_TO", "SUPERSEDES", "CITES"})

_NODE_WARN_THRESHOLD = 10_000

# Bound kuzu's per-database virtual reservation. kuzu's DEFAULT max_db_size is ~8 TB
# (it mmaps that much virtual address space up front); in a constrained container
# (memory cgroup / RLIMIT_AS) that mmap fails — "Buffer manager exception: Mmap for
# size 8796093022208 failed". It only surfaces once the graph is actually queried,
# which `recall_completion` (earned ON in T2) now does on every recall. A bounded,
# power-of-two cap (1 GiB default, generous past the 10k-node v0.1 threshold) and a
# bounded buffer pool keep kuzu allocatable everywhere; both are env-tunable up for
# large deployments.
_KUZU_MAX_DB_SIZE = int(os.environ.get("CRYSTALIUM_KUZU_MAX_DB_SIZE", str(1 << 30)))  # 1 GiB
_KUZU_BUFFER_POOL = int(os.environ.get("CRYSTALIUM_KUZU_BUFFER_POOL", str(1 << 28)))  # 256 MiB


class GraphStore:
    """KuzuDB-backed crystal relationship graph.

    Args:
        kuzu_dir: Path to the KuzuDB database directory. Created on first add_node().
    """

    def __init__(self, kuzu_dir: Path) -> None:
        self.kuzu_dir = kuzu_dir.resolve()
        self._db: Any = None
        self._conn: Any = None
        self._schema_initialized = False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_conn(self) -> Any:
        if self._conn is None:
            import kuzu  # type: ignore[import-untyped]

            # kuzu.Database manages its own directory creation.
            # We must NOT pre-create the path as a directory — KuzuDB raises
            # "Database path cannot be a directory" if the path already exists
            # as a directory when it expects to create it itself.
            # Only ensure the PARENT directory exists.
            self.kuzu_dir.parent.mkdir(parents=True, exist_ok=True)
            try:
                self._db = kuzu.Database(
                    str(self.kuzu_dir),
                    buffer_pool_size=_KUZU_BUFFER_POOL,
                    max_db_size=_KUZU_MAX_DB_SIZE,
                )
            except TypeError:
                # Older kuzu without the size kwargs — fall back to defaults.
                self._db = kuzu.Database(str(self.kuzu_dir))
            self._conn = kuzu.Connection(self._db)
            self._ensure_schema()
        return self._conn

    def _ensure_schema(self) -> None:
        if self._schema_initialized:
            return
        conn = self._conn
        # Create node table if not exists
        try:
            conn.execute(
                "CREATE NODE TABLE IF NOT EXISTS Crystal(id STRING, layer STRING, PRIMARY KEY(id))"
            )
        except Exception:
            pass  # table already exists
        # Create relationship tables
        for rel in VALID_RELATIONS:
            try:
                conn.execute(
                    f"CREATE REL TABLE IF NOT EXISTS {rel}(FROM Crystal TO Crystal)"
                )
            except Exception:
                pass  # already exists
        self._schema_initialized = True

    def _node_count(self) -> int:
        conn = self._get_conn()
        try:
            result = conn.execute("MATCH (c:Crystal) RETURN count(c)")
            row = result.get_next()
            return int(row[0]) if row else 0
        except Exception:
            return 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add_node(self, crystal_id: str, layer: str) -> None:
        """Add a Crystal node. Idempotent: no-op if node already exists.

        Also emits a WARN if the graph has grown past _NODE_WARN_THRESHOLD
        (FINDING-004 caution, MISSION.md §Stack: 'Watch >10K-node telemetry').

        Args:
            crystal_id: Crystal identifier.
            layer:      Memory layer string.
        """
        conn = self._get_conn()
        # Check for existence first to keep it idempotent
        try:
            result = conn.execute(
                "MATCH (c:Crystal {id: $id}) RETURN c.id", {"id": crystal_id}
            )
            if result.get_next() is not None:
                return  # already present
        except Exception:
            pass

        conn.execute(
            "CREATE (:Crystal {id: $id, layer: $layer})",
            {"id": crystal_id, "layer": layer},
        )

        # FINDING-004 telemetry warning
        count = self._node_count()
        if count > _NODE_WARN_THRESHOLD:
            log.warning(
                "graph_node_count_high",
                node_count=count,
                threshold=_NODE_WARN_THRESHOLD,
                advice=(
                    "FINDING-004: KuzuDB may degrade above 10K nodes. "
                    "Plan capacity review."
                ),
            )

    def delete_node(self, crystal_id: str) -> None:
        """Physically remove a Crystal node and all its edges (RTBF hard-delete).

        DETACH DELETE drops the node together with any attached relationships, so
        the right-to-be-forgotten erasure leaves no graph trace of the content.
        No-op if the node does not exist.
        """
        conn = self._get_conn()
        try:
            conn.execute("MATCH (c:Crystal {id: $id}) DETACH DELETE c", {"id": crystal_id})
        except Exception as exc:  # noqa: BLE001
            log.warning("graph_delete_node_error", crystal_id=crystal_id, error=str(exc))

    def add_edge(self, src: str, dst: str, rel: str) -> None:
        """Add a typed edge between two Crystal nodes.

        Args:
            src: Source crystal ID.
            dst: Destination crystal ID.
            rel: Relationship type — one of LINKS_TO, SUPERSEDES, CITES.

        Raises:
            ValueError: If rel is not in VALID_RELATIONS.
            KeyError:   If src or dst node does not exist.
        """
        if rel not in VALID_RELATIONS:
            raise ValueError(
                f"Invalid relationship type: {rel!r}. "
                f"Must be one of {sorted(VALID_RELATIONS)}."
            )
        conn = self._get_conn()
        # Verify both nodes exist; use has_next() before get_next() to avoid
        # "No more tuples" RuntimeError when the query returns 0 rows.
        for node_id in (src, dst):
            result = conn.execute(
                "MATCH (c:Crystal {id: $id}) RETURN c.id", {"id": node_id}
            )
            if not result.has_next():
                raise KeyError(f"Crystal node not found: {node_id!r}")

        conn.execute(
            f"MATCH (a:Crystal {{id: $src}}), (b:Crystal {{id: $dst}}) "
            f"CREATE (a)-[:{rel}]->(b)",
            {"src": src, "dst": dst},
        )

    def _neighbor_expand_one_hop(
        self, seed_ids: list[str], rel_filter: str | None
    ) -> set[str]:
        """Single depth-1 expansion over *seed_ids*, excluding *seed_ids* itself.

        Uses the repo's own has_next()-guarded idiom (graph.py:185-186, the
        add_edge path at :187-192) instead of the dead `is None` check: the
        kuzu driver RAISES RuntimeError at cursor exhaustion, it never
        returns None from get_next(). The try/except is per-seed so one
        failing seed cannot abort the remaining seeds in the caller's loop.
        """
        conn = self._get_conn()
        result_ids: set[str] = set()
        rel_clause = f"[r:{rel_filter}]" if rel_filter else "[r]"

        for seed_id in seed_ids:
            query = (
                f"MATCH (a:Crystal {{id: $seed}})-{rel_clause}->(b:Crystal) "
                f"RETURN DISTINCT b.id"
            )
            try:
                result = conn.execute(query, {"seed": seed_id})
                while result.has_next():
                    row = result.get_next()
                    neighbor_id = row[0]
                    if neighbor_id not in seed_ids:
                        result_ids.add(neighbor_id)
            except Exception as exc:
                log.warning("neighbor_expand_error", error=str(exc), seed_id=seed_id)

        return result_ids

    def neighbor_expand(
        self,
        seed_ids: list[str],
        depth: int = 1,
        rel_filter: str | None = None,
    ) -> set[str]:
        """Expand from *seed_ids* up to *depth* hops along *rel_filter* edges.

        Args:
            seed_ids:   Starting crystal IDs.
            depth:      Number of hops to traverse (default 1).
            rel_filter: Optional relationship type to traverse.
                        None = all three (LINKS_TO, SUPERSEDES, CITES).

        Returns:
            Set of crystal IDs reachable from seeds (excludes the seeds themselves).
        """
        if not seed_ids:
            return set()

        if rel_filter is not None and rel_filter not in VALID_RELATIONS:
            raise ValueError(f"Invalid rel_filter: {rel_filter!r}")

        original_seeds = set(seed_ids)

        # depth > 1: iterative depth-1 frontier expansion (crystalium#41 N-4).
        # The chained fixed-depth Cypher pattern used to bind every hop to the
        # same variable (pattern.replace('()', '(b:Crystal)') rewrote every
        # '()'), matching only self-loops. Walking the frontier one hop at a
        # time reuses the same has_next()-guarded query at every hop and
        # keeps the original seeds excluded at every hop, not just the first
        # (graph.py's public contract: "excludes the seeds themselves").
        result_ids: set[str] = set()
        frontier = list(dict.fromkeys(seed_ids))
        visited = set(frontier)

        for _hop in range(depth):
            if not frontier:
                break
            hop_ids = self._neighbor_expand_one_hop(frontier, rel_filter)
            hop_ids -= original_seeds
            result_ids |= hop_ids
            new_frontier = hop_ids - visited
            if not new_frontier:
                break
            visited |= new_frontier
            frontier = list(new_frontier)

        return result_ids

    def decaying_walk(
        self,
        seed_ids: list[str],
        max_hops: int = 2,
        decay: float = 0.5,
    ) -> dict[str, float]:
        """Bounded, decaying multi-hop walk (W5 pattern completion / CA3 analogue).

        Returns {crystal_id: decay**hop} for nodes reachable within *max_hops*,
        excluding the seeds; each node's score reflects its SHORTEST hop distance
        (first time seen in a BFS frontier). Empty dict if the graph is edgeless.
        Reuses the reliable depth-1 neighbor_expand per frontier so hop count is
        tracked honestly (unlike the fixed-depth pattern which collapses distance).
        The frontier (a set) is walked in sorted() order so the query sequence —
        and therefore the result — is deterministic across PYTHONHASHSEED values
        (crystalium#41 follow-up).
        """
        if not seed_ids or max_hops < 1:
            return {}
        scores: dict[str, float] = {}
        visited: set[str] = set(seed_ids)
        frontier: set[str] = set(seed_ids)
        for hop in range(1, max_hops + 1):
            nxt = self.neighbor_expand(sorted(frontier), depth=1)
            new = {n for n in nxt if n not in visited}
            if not new:
                break
            w = decay ** hop
            for n in new:
                scores[n] = w  # shortest-hop weight
            visited |= new
            frontier = new
        return scores

    def node_count(self) -> int:
        """Return the total number of Crystal nodes in the graph."""
        return self._node_count()

    def all_edges(
        self,
        *,
        rel_filter: str | None = None,
        limit: int = 50000,
        offset: int = 0,
    ) -> list[tuple[str, str, str]]:
        """Enumerate (from_id, to_id, rel_type) edges from KuzuDB (GAP-2, W-GE1).

        Cypher: MATCH (a:Crystal)-[r:REL_TYPE]->(b:Crystal) RETURN a.id, b.id, type(r)
        One query per rel type when rel_filter is None (kuzu REL tables are typed
        separately — graph.py:93-99). Paginated via SKIP/LIMIT. Returns [] on any
        kuzu error (mirrors neighbor_expand's defensive try/except). Uses the
        has_next()-guarded idiom (graph.py:185-186) so a fully healthy call
        emits no cursor-exhaustion warning (crystalium#41 N-1).

        Args:
            rel_filter: None = all VALID_RELATIONS; else one specific type.
            limit:      Max edges to return (bounded; edges can exceed node count).
            offset:     Pagination cursor (SKIP n rows).

        Returns:
            List of (from_id, to_id, rel_type) tuples. Empty on error.
        """
        _EDGE_WARN_THRESHOLD = 10 * _NODE_WARN_THRESHOLD  # 100_000

        if rel_filter is not None and rel_filter not in VALID_RELATIONS:
            raise ValueError(f"Invalid rel_filter: {rel_filter!r}. Must be one of {sorted(VALID_RELATIONS)}.")

        rels_to_query: list[str] = [rel_filter] if rel_filter else sorted(VALID_RELATIONS)
        results: list[tuple[str, str, str]] = []

        try:
            conn = self._get_conn()
            for rel in rels_to_query:
                try:
                    query = (
                        f"MATCH (a:Crystal)-[:{rel}]->(b:Crystal) "
                        f"RETURN a.id, b.id "
                        f"SKIP {offset} LIMIT {limit}"
                    )
                    result = conn.execute(query)
                    while result.has_next():
                        row = result.get_next()
                        results.append((str(row[0]), str(row[1]), rel))
                except Exception as exc:  # noqa: BLE001
                    log.warning("all_edges_rel_error", rel=rel, error=str(exc))
                    continue
        except Exception as exc:  # noqa: BLE001
            log.warning("all_edges_error", error=str(exc))
            return []

        total = len(results)
        if total > _EDGE_WARN_THRESHOLD:
            log.warning(
                "all_edges_count_high",
                edge_count=total,
                threshold=_EDGE_WARN_THRESHOLD,
                advice="Edge count exceeds 10×MAX_EXPORT_NODES; consider paginating.",
            )

        return results
