"""Self-contained demo of crystalium.graph_export — all four edge types.

Seeds a small "uv-migration" project (~8 crystals) in a temp directory,
exports via GraphExporter, and prints three sections to stdout:

  === CANONICAL JSON ===
  === CYTOSCAPE ===
  === GRAPHML (head) ===

Run in-container (print-only, default):
  docker compose run --rm crystalium python examples/graph_export_demo.py

Save all three artifacts to a directory (creates it if needed):
  docker compose run --rm crystalium python examples/graph_export_demo.py --out-dir /tmp/demo
  # then open /tmp/demo/graph-export-demo.graphml in Gephi,
  # or paste the .cytoscape.json into js.cytoscape.org

Save individual artifacts:
  docker compose run --rm crystalium python examples/graph_export_demo.py --save-json /tmp/g.json
  docker compose run --rm crystalium python examples/graph_export_demo.py --save-graphml /tmp/g.graphml
  docker compose run --rm crystalium python examples/graph_export_demo.py --save-cytoscape /tmp/g.cytoscape.json

Flags are additive; combine freely:
  docker compose run --rm crystalium python examples/graph_export_demo.py \\
      --save-json /tmp/g.json --save-graphml /tmp/g.graphml

With no save flags the current behaviour is preserved exactly (three sections
printed to stdout). When save flags are present the same stdout sections are
still printed PLUS each artifact is written and a one-line confirmation is
emitted to stderr so that stdout remains machine-parseable.

Add --quiet to suppress stdout when saving (optional convenience; the no-flag
default is always unchanged).
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Crystal factory — mirrors _make_crystal from test_graph_export.py exactly
# ---------------------------------------------------------------------------

_PROJECT = "uv-migration"
_NOW = datetime(2026, 6, 22, 9, 0, 0, tzinfo=UTC).isoformat()


def _crystal(
    crystal_id: str,
    summary: str,
    *,
    layer: str = "semantic",
    trust_tier: str = "T1",
    validation_state: str = "unverified",
    status: str = "active",
    author_agent: str = "spectra",
    importance: float = 0.7,
    tags: list[str] | None = None,
) -> dict:
    return {
        "id": crystal_id,
        "layer": layer,
        "summary": summary,
        "provenance": {
            "source": "verified_agent",
            "author_agent": author_agent,
            "task_id": "demo-task-001",
            "created_at": _NOW,
        },
        "trust_tier": trust_tier,
        "validation_state": validation_state,
        "scope": {
            "project": _PROJECT,
            "agent_class_visibility": None,
            "sensitivity_tag": "none",
        },
        "temporal": {
            "t_valid_from": _NOW,
            "t_valid_to": None,
            "superseded_by": None,
        },
        "utility": {
            "access_count": 1,
            "last_access": _NOW,
            "outcome_success_score": None,
            "importance": importance,
            "novelty_at_write": 0.6,
        },
        "status": status,
        "tags": tags or [],
    }


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------

def seed_store(tmp: Path):
    """Seed RelationalStore + GraphStore with a human-readable uv-migration project."""
    from crystalium.storage.graph import GraphStore
    from crystalium.storage.relational import RelationalStore

    rel = RelationalStore(db_path=tmp / "demo.sqlite")
    graph = GraphStore(kuzu_dir=tmp / "demo.kuzu")

    # ── 8 crystals ──────────────────────────────────────────────────────────

    # A: pip is the current dep manager  (will be SUPERSEDED)
    c_pip = _crystal(
        "use-pip",
        "Use pip for dependency management",
        author_agent="forge",
        importance=0.4,
        tags=["deps", "tooling"],
    )
    # B: uv replaces pip  (supersedes A)
    c_uv = _crystal(
        "use-uv",
        "Use uv for dependency management — 10–100x faster than pip",
        author_agent="forge",
        importance=0.9,
        tags=["deps", "tooling", "uv"],
    )
    # C: lockfile policy  (co-occurs / LINKS_TO with B via kuzu)
    c_lock = _crystal(
        "uv-lockfile-committed",
        "uv.lock committed to version control; reproducible builds guaranteed",
        author_agent="spectra",
        importance=0.8,
        tags=["uv", "reproducibility"],
    )
    # D: workspace layout  (LINKS_TO B via kuzu)
    c_workspace = _crystal(
        "uv-workspace-layout",
        "Monorepo uses uv workspaces; sub-packages declared in pyproject.toml",
        author_agent="spectra",
        importance=0.75,
        tags=["uv", "monorepo"],
    )
    # E: authored by a different agent — will be the MERGED_FROM source crystal
    c_ci_note = _crystal(
        "ci-uses-uv",
        "CI pipeline switched to uv; cold-cache build now 18 s vs 4 min with pip",
        author_agent="forge-ci",
        importance=0.85,
        tags=["ci", "uv"],
    )
    # F: the MERGED crystal that absorbed E's knowledge
    c_migration_summary = _crystal(
        "migration-summary",
        "Full pip→uv migration complete: lockfile, CI, workspace, pre-commit updated",
        author_agent="spectra",
        importance=0.95,
        tags=["uv", "migration"],
    )
    # G: winner in a conflict (uv vs poetry)
    c_uv_policy = _crystal(
        "dep-tool-uv",
        "Selected uv as the project dependency manager (over Poetry/PDM)",
        author_agent="spectra",
        importance=0.9,
        tags=["decision", "uv"],
    )
    # H: loser in the conflict
    c_poetry = _crystal(
        "dep-tool-poetry",
        "Evaluated Poetry as dep manager — rejected: no workspace support in v1",
        author_agent="spectra",
        importance=0.5,
        tags=["decision", "poetry"],
        validation_state="unverified",
    )

    # Insert all into relational store
    for c in (c_pip, c_uv, c_lock, c_workspace, c_ci_note,
              c_migration_summary, c_uv_policy, c_poetry):
        rel.insert_crystal(c)

    # Add nodes to kuzu graph
    for c in (c_pip, c_uv, c_lock, c_workspace, c_ci_note,
              c_migration_summary, c_uv_policy, c_poetry):
        graph.add_node(c["id"], c["layer"])

    # ── LINKS_TO (kuzu) ─────────────────────────────────────────────────────
    # "use-uv" co-occurs with "uv-lockfile-committed"
    graph.add_edge("use-uv", "uv-lockfile-committed", "LINKS_TO")
    # "use-uv" co-occurs with "uv-workspace-layout"
    graph.add_edge("use-uv", "uv-workspace-layout", "LINKS_TO")
    # "migration-summary" links to "ci-uses-uv"
    graph.add_edge("migration-summary", "ci-uses-uv", "LINKS_TO")

    # ── SUPERSEDES (derived from temporal.superseded_by) ────────────────────
    # "use-uv" supersedes "use-pip"  →  export will emit: use-uv —SUPERSEDES→ use-pip
    rel.mark_superseded("use-pip", "use-uv", datetime(2026, 6, 1, tzinfo=UTC))

    # ── MERGED_FROM (derived from provenance.merged_authors) ────────────────
    # "migration-summary" absorbed the knowledge authored by "forge-ci"
    # (i.e., the crystal ci-uses-uv whose author_agent == "forge-ci")
    rel.merge_provenance(
        "migration-summary",
        {"author_agent": "forge-ci", "source": "verified_agent"},
    )

    # ── CONFLICTS_WITH (derived from conflicts ledger) ───────────────────────
    # uv won over poetry; winner=dep-tool-uv, loser=dep-tool-poetry
    rel.record_conflict(
        "dep-tool-uv",
        "dep-tool-poetry",
        winner_tier="T1",
        loser_tier="T1",
        similarity=0.82,
        scope={"project": _PROJECT},
    )

    return rel, graph


# ---------------------------------------------------------------------------
# File-save helpers
# ---------------------------------------------------------------------------

def _save_file(path: Path, content: str, label: str) -> None:
    """Write *content* to *path*, creating parent dirs as needed, then log to stderr."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    print(f"wrote {label} -> {path}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="graph_export_demo.py",
        description=(
            "Demo of crystalium.graph_export — seeds 8 crystals (uv-migration project), "
            "exports via GraphExporter, and prints/saves the canonical JSON, "
            "Cytoscape elements, and GraphML representations.\n\n"
            "With no save flags the three sections are printed to stdout only.\n"
            "When save flags are given, artifacts are written to the given paths "
            "AND the same stdout sections are still printed (add --quiet to suppress stdout)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  # Print only (default)\n"
            "  docker compose run --rm crystalium python examples/graph_export_demo.py\n\n"
            "  # Save all three to a directory\n"
            "  docker compose run --rm crystalium python examples/graph_export_demo.py --out-dir /tmp/demo\n\n"
            "  # Save individual files\n"
            "  docker compose run --rm crystalium python examples/graph_export_demo.py \\\n"
            "      --save-json /tmp/g.json --save-graphml /tmp/g.graphml\n\n"
            "  # Save silently (no stdout sections)\n"
            "  docker compose run --rm crystalium python examples/graph_export_demo.py \\\n"
            "      --out-dir /tmp/demo --quiet"
        ),
    )
    parser.add_argument(
        "--save-json",
        metavar="PATH",
        type=Path,
        default=None,
        help="Write the canonical JSON ({nodes[], edges[]}) to PATH.",
    )
    parser.add_argument(
        "--save-graphml",
        metavar="PATH",
        type=Path,
        default=None,
        help="Write the GraphML XML string to PATH.",
    )
    parser.add_argument(
        "--save-cytoscape",
        metavar="PATH",
        type=Path,
        default=None,
        help="Write the Cytoscape elements JSON to PATH.",
    )
    parser.add_argument(
        "--out-dir",
        metavar="DIR",
        type=Path,
        default=None,
        help=(
            "Convenience: write all three artifacts to DIR as "
            "graph-export-demo.json / .graphml / .cytoscape.json "
            "(creates DIR if it does not exist)."
        ),
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        default=False,
        help="Suppress the stdout sections when any save flag is active.",
    )
    return parser


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    args = _build_parser().parse_args()

    # Resolve --out-dir into the individual save paths (lower precedence than
    # explicit --save-* flags — explicit path wins if both given).
    if args.out_dir is not None:
        args.out_dir.mkdir(parents=True, exist_ok=True)
        if args.save_json is None:
            args.save_json = args.out_dir / "graph-export-demo.json"
        if args.save_graphml is None:
            args.save_graphml = args.out_dir / "graph-export-demo.graphml"
        if args.save_cytoscape is None:
            args.save_cytoscape = args.out_dir / "graph-export-demo.cytoscape.json"

    any_save = any(
        p is not None for p in (args.save_json, args.save_graphml, args.save_cytoscape)
    )
    # suppress stdout only when --quiet is explicitly requested AND saving
    suppress_stdout = args.quiet and any_save

    from crystalium.export.adapters import to_cytoscape, to_graphml
    from crystalium.export.graph_export import ExportFlags, GraphExporter

    with tempfile.TemporaryDirectory(prefix="crystalium-demo-") as _tmp:
        tmp = Path(_tmp)
        rel, graph = seed_store(tmp)

        exporter = GraphExporter(relational_store=rel, graph_store=graph)

        scope = {
            "project": _PROJECT,
            "agent_class_visibility": None,
            "sensitivity_tag": "none",
        }

        # include_superseded=True so the supersession edge isn't dangling-dropped
        flags = ExportFlags(include_superseded=True)
        canonical = exporter.export(scope=scope, include_flags=flags)

        # Scrub the non-deterministic generated_at timestamp for readability
        canonical_display = json.loads(json.dumps(canonical))
        canonical_display["generated_from"]["generated_at"] = "2026-06-22T09:00:00+00:00"

        cy = to_cytoscape(canonical)
        gml_str = to_graphml(canonical)

        # ── Save artifacts (before printing so stderr confirmations appear first) ──

        if args.save_json is not None:
            _save_file(
                args.save_json,
                json.dumps(canonical_display, indent=2, sort_keys=True),
                "canonical JSON",
            )

        if args.save_cytoscape is not None:
            _save_file(
                args.save_cytoscape,
                json.dumps(cy, indent=2),
                "Cytoscape JSON",
            )

        if args.save_graphml is not None:
            _save_file(args.save_graphml, gml_str, "GraphML")

        # ── Stdout sections (always on unless --quiet + saving) ─────────────

        if not suppress_stdout:
            # ── Section 1: Canonical JSON ────────────────────────────────────
            print("=" * 60)
            print("=== CANONICAL JSON ===")
            print("=" * 60)
            print(json.dumps(canonical_display, indent=2, sort_keys=True))

            # ── Section 2: Cytoscape ─────────────────────────────────────────
            print()
            print("=" * 60)
            print("=== CYTOSCAPE ===")
            print("=" * 60)
            print(json.dumps(cy, indent=2, sort_keys=True))

            # ── Section 3: GraphML head ───────────────────────────────────────
            print()
            print("=" * 60)
            print("=== GRAPHML (head — first 35 lines) ===")
            print("=" * 60)
            import xml.dom.minidom
            try:
                pretty = xml.dom.minidom.parseString(gml_str).toprettyxml(indent="  ")
                # strip the XML declaration line minidom adds
                lines = [ln for ln in pretty.splitlines() if not ln.startswith("<?xml")]
                head_lines = lines[:35]
            except Exception:
                head_lines = gml_str[:3000].splitlines()[:35]
            print("\n".join(head_lines))

            # ── Edge summary (plain-English) ──────────────────────────────────
            print()
            print("=" * 60)
            print("=== EDGE SUMMARY ===")
            print("=" * 60)
            node_summary = {n["id"]: n["summary"][:55] for n in canonical["nodes"]}
            for e in canonical["edges"]:
                frm = e["from"]
                to = e["to"]
                etype = e["type"]
                src = e["source"]
                weight = e.get("weight", 1.0)
                frm_label = node_summary.get(frm, frm)
                to_label = node_summary.get(to, to)
                print(f"  {frm_label!r}")
                print(f"    --[{etype} source={src} w={weight}]-->")
                print(f"    {to_label!r}")
                print()

            # ── Counts ────────────────────────────────────────────────────────
            counts = canonical["counts"]
            print("=" * 60)
            print("=== COUNTS ===")
            print("=" * 60)
            print(f"  nodes                : {counts['nodes']}")
            print(f"  edges                : {counts['edges']}")
            print(f"  nodes_total_estimate : {counts['nodes_total_estimate']}")
            print(f"  edges_dropped_dangling: {counts['edges_dropped_dangling']}")
            print(f"  edges_deduped        : {counts['edges_deduped']}")
            print(f"  truncated            : {canonical['truncated']}")
            print()

        # Verify all four types present (always checked, regardless of --quiet)
        edge_types = {e["type"] for e in canonical["edges"]}
        expected = {"LINKS_TO", "SUPERSEDES", "MERGED_FROM", "CONFLICTS_WITH"}
        missing = expected - edge_types
        if missing:
            print(f"DEMO ERROR: missing edge types: {missing}", file=sys.stderr)
            sys.exit(1)

        if not suppress_stdout:
            print(f"  All four edge types present: {sorted(edge_types)}")
            print()
            print("Demo complete. Temp store cleaned up automatically.")


if __name__ == "__main__":
    main()
