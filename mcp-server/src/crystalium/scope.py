"""Canonical project-key derivation + write-time scope normalization.

v1.6 Wave 4 (memory diagnosability). MOTIVATING INCIDENT (CHANGELOG v1.6.0): a
live project store held 9 crystals yet answered every recall with 0 records.
One root cause: writers used three different free-typed `scope.project`
strings for the SAME logical project ("eidolons",
"eidolons-v2-go-migration-2026-06-24", "riverdale-migration"), so scoped
recall silently partitioned the store into islands no query could bridge.

The fix: the data directory IS the project. `CRYSTALIUM_DATA_DIR` already
names one dedicated store per project (MISSION.md §Storage paths); its
basename is therefore the single canonical `scope.project` key, never a
caller-supplied free-typed string.

Write paths (commit, ingest, plan_checkpoint, plan_replan) normalize
scope.project to the canonical key, preserving any differing caller-supplied
value in scope.project_raw (new optional field) so provenance of the original
label is never lost. Recall does NOT rewrite an explicit scope.project (it is
a read filter, not a write of record — legacy/fragmented keys must stay
queryable for diagnosis, see `recall --explain`); it only defaults an omitted
scope.project to the canonical key.

No migration of existing rows — out of scope for v1.6. `doctor` and
`recall --explain` surface pre-existing fragmentation (distinct project keys
already in the store) so it is visible rather than silently corrosive.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def canonical_project_key(data_dir: Path) -> str:
    """Derive the canonical `scope.project` key from a CRYSTALIUM_DATA_DIR.

    The key is the basename of *data_dir* — the store IS the project.
    Falls back to "default" for a degenerate data_dir (e.g. filesystem root)
    whose basename is empty.
    """
    name = Path(data_dir).name
    return name or "default"


def normalize_write_scope(
    scope: dict[str, Any] | None, canonical: str
) -> tuple[dict[str, Any], bool]:
    """Normalize `scope["project"]` to *canonical* for a WRITE path.

    Returns (normalized_scope, was_normalized). *scope* is never mutated in
    place — a shallow copy is returned.

    - scope.project == canonical: passthrough, unchanged, was_normalized=False.
    - scope.project differs (including missing/empty): scope.project is
      rewritten to canonical; if the caller supplied a non-empty differing
      value it is preserved verbatim in scope.project_raw (never overwritten
      if already present from an earlier normalization pass), was_normalized=True.
    """
    normalized: dict[str, Any] = dict(scope) if isinstance(scope, dict) else {}
    raw_project = normalized.get("project")

    if raw_project == canonical:
        return normalized, False

    if raw_project and "project_raw" not in normalized:
        normalized["project_raw"] = raw_project
    normalized["project"] = canonical
    return normalized, True


def default_recall_project(scope: dict[str, Any] | None, canonical: str) -> str:
    """Recall-side default: an explicit scope.project passes through verbatim
    (recall is a read filter — rewriting it would make legacy/fragmented
    project keys unqueryable, defeating diagnosability). Only an omitted or
    empty scope.project defaults to the canonical key.
    """
    if isinstance(scope, dict):
        project = scope.get("project")
        if project:
            return project
    return canonical
