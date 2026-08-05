"""CRYSTALIUM — portable memory harness for the Eidolons."""

from __future__ import annotations

#: Fallback literal — kept in sync with [project].version in pyproject.toml.
#: Used only when the package isn't installed with metadata (e.g. a raw
#: PYTHONPATH=src dev checkout that never ran `pip install`/`uv sync`).
_FALLBACK_VERSION = "1.12.0"

try:
    # v1.6 (item 6 housekeeping): single-source from installed package metadata
    # instead of a hand-maintained literal — this was stale at "1.0.0" for
    # several releases (including the version mcp.server.lowlevel.Server
    # reports as serverInfo to MCP clients; see server.py::_build_server).
    from importlib.metadata import version

    __version__ = version("crystalium")
except Exception:  # pragma: no cover — PackageNotFoundError or metadata absent
    __version__ = _FALLBACK_VERSION
