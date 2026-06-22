"""CRYSTALIUM graph-export core — edge-derivation + canonical JSON assembly.

Implements D1 (edge-derivation strategy) and D2 (canonical JSON output).
The four edge types (§5.1):
  LINKS_TO    — read from KuzuDB via GraphStore.all_edges; source:"kuzu"
  CITES       — pass-through from KuzuDB; source:"kuzu"
  SUPERSEDES  — derived from temporal.superseded_by; source:"derived"
  MERGED_FROM — derived from provenance.corroboration/merged_authors/merged_sources;
               source:"derived"
  CONFLICTS_WITH — derived from conflicts ledger; source:"derived"

Global hygiene (§5.5):
  HYG-1 dangling endpoint drop (default)
  HYG-2 dedup by (from, to, type)
  HYG-3 self-loop drop
  HYG-4 deterministic ordering (nodes by id, edges by (type, from, to))
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import structlog

log = structlog.get_logger("crystalium.export.graph_export")


@dataclass(frozen=True)
class ExportFlags:
    """Frozen flag object controlling visibility / edge derivation (§6.4)."""

    include_quarantined: bool = False
    include_deprecated: bool = False
    include_superseded: bool = False
    all_visibility: bool = False          # ignore agent_class_visibility filter
    include_content_ref: bool = False     # emit opaque sha256 hash (never blob)
    include_drift: bool = False           # CONFLICT-2 drift_audit edges
    synthesize_links: bool = False        # §5.4(b) read-only co-occurrence fallback
    backfill_links: bool = False          # §5.4(a) WRITE path; CLI-only, operator-gated
    dangling_policy: str = "drop"         # "drop" | "keep"


class GraphExporter:
    """Derives a canonical graph dict (§4) from the relational + graph stores.

    Args:
        relational_store: RelationalStore instance.
        graph_store:      GraphStore (or _NullGraphStore) instance.
    """

    def __init__(self, relational_store: Any, graph_store: Any) -> None:
        self._rel = relational_store
        self._graph = graph_store

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def export(
        self,
        *,
        scope: dict[str, Any],
        layers: list[str] | None = None,
        limit: int = 5000,
        include_flags: ExportFlags | None = None,
        redactor: Any | None = None,
    ) -> dict[str, Any]:
        """Produce the canonical graph dict (validates against graph-export.v1.json).

        Steps (§6.3):
          1. list_for_export + count_for_export → nodes (redact each summary)
          2. Build node-id set
          3. all_edges → LINKS_TO/CITES
          4. Derive SUPERSEDES/MERGED_FROM/CONFLICTS_WITH from relational
          5. Apply §5.5 hygiene against the node-id set
          6. Assemble top-level + counts + truncated

        Args:
            scope:         Dict with at least {"project": str}; optionally
                           {"agent_class_visibility": str, "sensitivity_tag": str}.
            layers:        Optional layer subset filter.
            limit:         Node cap (default 5000, clamped to MAX_EXPORT_NODES).
            include_flags: ExportFlags instance (defaults to ExportFlags()).
            redactor:      Redactor instance from aetheryte/redact.py; if None,
                           summaries are passed through unmodified.

        Returns:
            Canonical graph dict ready for JSON serialisation.
        """
        from crystalium.storage.relational import MAX_EXPORT_NODES

        flags = include_flags if include_flags is not None else ExportFlags()
        project = scope.get("project", "")
        agent_class_visibility = (
            None if flags.all_visibility
            else scope.get("agent_class_visibility")
        )
        sensitivity_tag = scope.get("sensitivity_tag", "none") or "none"

        # Clamp limit
        limit = min(max(0, limit), MAX_EXPORT_NODES)

        # ── Step 1: enumerate nodes ──────────────────────────────────────
        node_rows = self._rel.list_for_export(
            project,
            agent_class_visibility=agent_class_visibility,
            layers=layers,
            include_quarantined=flags.include_quarantined,
            include_deprecated=flags.include_deprecated,
            include_superseded=flags.include_superseded,
            limit=limit,
            offset=0,
        )
        total_estimate = self._rel.count_for_export(
            project,
            agent_class_visibility=agent_class_visibility,
            layers=layers,
            include_quarantined=flags.include_quarantined,
            include_deprecated=flags.include_deprecated,
            include_superseded=flags.include_superseded,
        )
        truncated = len(node_rows) >= limit and total_estimate > limit
        if truncated:
            log.warning(
                "graph_export_truncated",
                project=project,
                limit=limit,
                total_estimate=total_estimate,
                advice="Node limit hit. Set a higher --limit (max 10000) or paginate.",
            )

        # ── Step 2: build node-id set + hydrated node list ───────────────
        node_id_set: set[str] = set()
        nodes: list[dict[str, Any]] = []
        for row in node_rows:
            node_id_set.add(row["id"])
            node = self._project_node(
                row, flags=flags, sensitivity_tag=sensitivity_tag, redactor=redactor
            )
            nodes.append(node)

        # Sort by id ascending (HYG-4)
        nodes.sort(key=lambda n: n["id"])

        # ── Step 3: read kuzu edges (LINKS_TO + CITES) ───────────────────
        raw_edges: list[dict[str, Any]] = []
        kuzu_tuples = self._graph.all_edges()
        for from_id, to_id, rel_type in kuzu_tuples:
            if rel_type in ("LINKS_TO", "CITES"):
                raw_edges.append({
                    "from": from_id,
                    "to": to_id,
                    "type": rel_type,
                    "source": "kuzu",
                    "weight": 1.0,
                    "metadata": {},
                })

        # ── Step 4: derive relational edges ──────────────────────────────
        raw_edges.extend(self._derive_supersedes(project, flags=flags))
        raw_edges.extend(self._derive_merged_from(project, node_id_set=node_id_set))
        raw_edges.extend(self._derive_conflicts_with(project, node_id_set=node_id_set, flags=flags))

        # ── Step 5: apply hygiene ─────────────────────────────────────────
        edges, edges_dropped_dangling, edges_deduped = self._apply_hygiene(
            raw_edges, node_id_set=node_id_set, dangling_policy=flags.dangling_policy
        )

        # ── Step 6: assemble top-level ────────────────────────────────────
        return {
            "schema_version": "graph-export.v1",
            "generated_from": {
                "project": project,
                "agent_class_visibility": scope.get("agent_class_visibility"),
                "layers": layers,
                "generated_at": datetime.now(UTC).isoformat(),
                "caller_tier": None,
            },
            "counts": {
                "nodes": len(nodes),
                "edges": len(edges),
                "nodes_total_estimate": total_estimate,
                "edges_dropped_dangling": edges_dropped_dangling,
                "edges_deduped": edges_deduped,
            },
            "truncated": truncated,
            "nodes": nodes,
            "edges": edges,
        }

    # ------------------------------------------------------------------
    # Node projection (§4.2)
    # ------------------------------------------------------------------

    def _project_node(
        self,
        row: dict[str, Any],
        *,
        flags: ExportFlags,
        sensitivity_tag: str,
        redactor: Any | None,
    ) -> dict[str, Any]:
        """Project a _row_to_dict() row to a Node object (§4.2).

        Applies Redactor to summary; omits content_ref by default (GAP-3).
        """
        summary = row.get("summary", "")
        if redactor is not None:
            summary = redactor.redact(summary, sensitivity_tag)

        utility = row.get("utility") or {}
        provenance = row.get("provenance") or {}

        node: dict[str, Any] = {
            "id": row["id"],
            "layer": row["layer"],
            "summary": summary,
            "trust_tier": row["trust_tier"],
            "validation_state": row["validation_state"],
            "status": row["status"],
            "importance": float(utility.get("importance", row.get("importance", 0.0))),
        }

        # Optional fields
        created_at = provenance.get("created_at") or row.get("created_at")
        if created_at:
            node["created_at"] = created_at
        last_access = utility.get("last_access")
        if last_access:
            node["last_access"] = last_access

        tags = row.get("tags")
        if tags:
            node["tags"] = tags if isinstance(tags, list) else []
        else:
            node["tags"] = []

        node["protected"] = bool(row.get("protected", False))

        # content_ref: only emit the opaque hash when flag set (GAP-3; never blob)
        if flags.include_content_ref and row.get("content_ref"):
            node["content_ref"] = row["content_ref"]

        node["scope_project"] = row.get("scope", {}).get("project", "")

        return node

    # ------------------------------------------------------------------
    # Edge derivation rules (§5.1)
    # ------------------------------------------------------------------

    def _derive_supersedes(
        self, project: str, *, flags: ExportFlags
    ) -> list[dict[str, Any]]:
        """SUP-1: derive SUPERSEDES edges from temporal.superseded_by (§5.1).

        For every crystal row where temporal.superseded_by IS NOT NULL, emit
        {from: newer_id, to: old_id, type:SUPERSEDES, source:derived}.
        Note: default filter excludes superseded nodes, so these edges will
        normally be dangling and dropped. With --include-superseded, both
        endpoints survive and the edge is emitted (G-GE2 fixture documented).
        """
        edges: list[dict[str, Any]] = []
        # We need all crystals including superseded ones to find the lineage
        rows = self._rel.list_for_export(
            project,
            include_quarantined=True,
            include_deprecated=True,
            include_superseded=True,
            limit=10000,
        )
        for row in rows:
            temporal = row.get("temporal") or {}
            newer_id = temporal.get("superseded_by")
            if newer_id:
                edges.append({
                    "from": newer_id,
                    "to": row["id"],
                    "type": "SUPERSEDES",
                    "source": "derived",
                    "weight": 1.0,
                    "metadata": {
                        "t_valid_to": temporal.get("t_valid_to"),
                    },
                })
        return edges

    def _derive_merged_from(
        self, project: str, *, node_id_set: set[str]
    ) -> list[dict[str, Any]]:
        """MERGED-1: derive MERGED_FROM edges from provenance fields (§5.1).

        For every crystal c where corroboration > 1 or merged_authors/merged_sources
        non-empty, emit one edge per contributing author that resolves to an
        in-scope crystal id.

        [GAP-MERGE-RESOLUTION]: merged_authors are agent/source names, not ids.
        We resolve them by looking for in-scope crystals whose provenance.author_agent
        matches. Unresolvable contributors are dropped (dangling).
        """
        edges: list[dict[str, Any]] = []
        # Load all in-scope rows to build an author→crystal_id index
        all_rows = self._rel.list_for_export(
            project,
            include_quarantined=True,
            include_deprecated=True,
            include_superseded=True,
            limit=10000,
        )
        # author_agent → list of crystal ids
        author_to_ids: dict[str, list[str]] = {}
        for row in all_rows:
            prov = row.get("provenance") or {}
            author = prov.get("author_agent")
            if author:
                author_to_ids.setdefault(author, []).append(row["id"])

        for row in all_rows:
            if row["id"] not in node_id_set:
                continue
            prov = row.get("provenance") or {}
            corroboration = int(prov.get("corroboration", 1))
            merged_authors: list[str] = prov.get("merged_authors") or []
            merged_sources: list[str] = prov.get("merged_sources") or []

            if corroboration <= 1 and not merged_authors and not merged_sources:
                continue

            # Emit one edge per contributing author that resolves to an in-scope id
            seen_contributors: set[str] = set()
            for author in merged_authors:
                for contributor_id in author_to_ids.get(author, []):
                    if contributor_id == row["id"]:
                        continue  # skip self
                    if contributor_id in seen_contributors:
                        continue
                    seen_contributors.add(contributor_id)
                    edges.append({
                        "from": row["id"],
                        "to": contributor_id,
                        "type": "MERGED_FROM",
                        "source": "derived",
                        "weight": float(corroboration),
                        "metadata": {
                            "corroboration": corroboration,
                            "merged_authors": merged_authors,
                            "merged_sources": merged_sources,
                        },
                    })

        return edges

    def _derive_conflicts_with(
        self,
        project: str,
        *,
        node_id_set: set[str],
        flags: ExportFlags,
    ) -> list[dict[str, Any]]:
        """CONFLICT-1/2: derive CONFLICTS_WITH edges from ledgers (§5.1)."""
        edges: list[dict[str, Any]] = []

        for conflict in self._rel.list_conflicts():
            winner_id = conflict.get("winner_id", "")
            loser_id = conflict.get("loser_id", "")
            if not winner_id or not loser_id:
                continue
            # Scope check: if ledger scope has a project, it must match;
            # if null, fall back to node-set membership check (GAP-CONFLICT-SCOPE)
            conflict_scope = conflict.get("scope")
            if conflict_scope and isinstance(conflict_scope, dict):
                if conflict_scope.get("project") != project:
                    continue
            else:
                # Null-scope fallback: both endpoints must be in the node set
                if winner_id not in node_id_set or loser_id not in node_id_set:
                    continue

            similarity = conflict.get("similarity")
            edges.append({
                "from": winner_id,
                "to": loser_id,
                "type": "CONFLICTS_WITH",
                "source": "derived",
                "weight": float(similarity) if similarity is not None else 1.0,
                "metadata": {
                    "winner_tier": conflict.get("winner_tier"),
                    "loser_tier": conflict.get("loser_tier"),
                    "similarity": similarity,
                    "direction": "winner_to_loser",
                    "origin": "conflicts",
                },
            })

        # CONFLICT-2: drift_audit rows (opt-in via include_drift flag)
        if flags.include_drift:
            for drift in self._rel.list_drift_audit():
                crystal_id = drift.get("crystal_id", "")
                prior_id = drift.get("prior_id", "")
                if not crystal_id or not prior_id:
                    continue
                similarity = drift.get("similarity")
                edges.append({
                    "from": crystal_id,
                    "to": prior_id,
                    "type": "CONFLICTS_WITH",
                    "source": "derived",
                    "weight": float(similarity) if similarity is not None else 1.0,
                    "metadata": {
                        "similarity": similarity,
                        "candidate_tier": drift.get("candidate_tier"),
                        "prior_tier": drift.get("prior_tier"),
                        "direction": "winner_to_loser",
                        "origin": "drift_audit",
                    },
                })

        return edges

    # ------------------------------------------------------------------
    # Global hygiene (§5.5)
    # ------------------------------------------------------------------

    def _apply_hygiene(
        self,
        raw_edges: list[dict[str, Any]],
        *,
        node_id_set: set[str],
        dangling_policy: str = "drop",
    ) -> tuple[list[dict[str, Any]], int, int]:
        """Apply HYG-1 through HYG-4 to raw_edges.

        Returns:
            (clean_edges, edges_dropped_dangling, edges_deduped)
        """
        edges_dropped_dangling = 0
        edges_deduped = 0

        # HYG-3: drop self-loops
        no_self = [e for e in raw_edges if e["from"] != e["to"]]

        # HYG-1: dangling endpoint check
        if dangling_policy == "drop":
            kept = []
            for e in no_self:
                if e["from"] in node_id_set and e["to"] in node_id_set:
                    kept.append(e)
                else:
                    edges_dropped_dangling += 1
        else:
            kept = no_self

        # HYG-2: deduplicate by (from, to, type); keep max weight, union metadata
        dedup_map: dict[tuple[str, str, str], dict[str, Any]] = {}
        for e in kept:
            key = (e["from"], e["to"], e["type"])
            if key not in dedup_map:
                dedup_map[key] = dict(e)
                dedup_map[key]["metadata"] = dict(e.get("metadata") or {})
            else:
                existing = dedup_map[key]
                # Keep max weight
                existing["weight"] = max(existing.get("weight", 1.0), e.get("weight", 1.0))
                # Union metadata (non-destructive merge; existing values take precedence)
                meta = dict(e.get("metadata") or {})
                meta.update(existing["metadata"])
                existing["metadata"] = meta
                edges_deduped += 1

        # HYG-4: sort by (type, from, to) ascending
        clean_edges = sorted(
            dedup_map.values(),
            key=lambda e: (e["type"], e["from"], e["to"]),
        )

        return clean_edges, edges_dropped_dangling, edges_deduped
