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

from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger("crystalium.storage.graph")

VALID_RELATIONS: frozenset[str] = frozenset({"LINKS_TO", "SUPERSEDES", "CITES"})

_NODE_WARN_THRESHOLD = 10_000


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

        conn = self._get_conn()
        result_ids: set[str] = set()

        if rel_filter is not None and rel_filter not in VALID_RELATIONS:
            raise ValueError(f"Invalid rel_filter: {rel_filter!r}")

        # Build Cypher query — KuzuDB supports fixed-depth patterns
        rel_pattern = f":{rel_filter}" if rel_filter else ""
        # For depth > 1, chain multiple hops (simple fixed-depth expansion)
        hops = "-[{rel}]->()".format(rel=rel_pattern)
        pattern = "".join([hops] * depth)

        try:
            for seed_id in seed_ids:
                query = (
                    f"MATCH (a:Crystal {{id: $seed}}){pattern.replace('()', '(b:Crystal)')}"
                    f" RETURN DISTINCT b.id"
                )
                # Simple depth-1 shorthand (covers 90% of use cases)
                if depth == 1:
                    rel_clause = f"[r:{rel_filter}]" if rel_filter else "[r]"
                    query = (
                        f"MATCH (a:Crystal {{id: $seed}})-{rel_clause}->(b:Crystal) "
                        f"RETURN DISTINCT b.id"
                    )
                result = conn.execute(query, {"seed": seed_id})
                while True:
                    row = result.get_next()
                    if row is None:
                        break
                    neighbor_id = row[0]
                    if neighbor_id not in seed_ids:
                        result_ids.add(neighbor_id)
        except Exception as exc:
            log.warning("neighbor_expand_error", error=str(exc), seed_ids=seed_ids)

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
        """
        if not seed_ids or max_hops < 1:
            return {}
        scores: dict[str, float] = {}
        visited: set[str] = set(seed_ids)
        frontier: set[str] = set(seed_ids)
        for hop in range(1, max_hops + 1):
            nxt = self.neighbor_expand(list(frontier), depth=1)
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
