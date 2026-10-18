"""Bounded repair facts and confined historical metadata anchors (fix_span.v1)."""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class Anchor(BaseModel):
    """A historical file, directory, or qualified symbol reference."""

    model_config = ConfigDict(extra="forbid")
    kind: Literal["file", "dir", "symbol"]
    value: str = Field(min_length=1, pattern=r"\S")


class Locator(BaseModel):
    """An inert origin reference, never fetched or executed."""

    model_config = ConfigDict(extra="forbid")
    kind: Literal["commit", "pr", "handoff"]
    value: str = Field(min_length=1, pattern=r"\S")


class FixSpan(BaseModel):
    """Complete repair facts; trust and provenance belong to the outer crystal."""

    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["1.0"] = "1.0"
    symptom: str = Field(min_length=1, max_length=200, pattern=r"\S")
    root_cause: str = Field(min_length=1, max_length=300, pattern=r"\S")
    fix_pattern: str = Field(min_length=1, max_length=400, pattern=r"\S")
    anchors: list[Anchor] = Field(min_length=1)
    constraints: list[str]
    locator: Locator


def is_structured(payload: dict[str, Any]) -> bool:
    """Recognize declared repairs without reinterpreting legacy opaque kind metadata."""
    return payload.get("kind") == "fix_span" or "fix_span" in payload


def normalize_anchor(anchor: Anchor, repo_root: Path | None) -> Anchor:
    """Confine metadata while permitting nonexistent historical suffixes.

    resolve(strict=False) resolves existing symlink prefixes. Runtime sandbox path
    checks retain their separate strict semantics.
    """
    value = anchor.value
    windows = PureWindowsPath(value)
    if "\x00" in value or "\\" in value or windows.drive or value.startswith("/"):
        raise ValueError("path")
    if anchor.kind == "symbol":
        if "/" in value or ".." in value or not re.fullmatch(r"[\w$]+(?:(?:\.|::)[\w$]+)*", value):
            raise ValueError("path")
        return anchor
    path = PurePosixPath(value)
    if ".." in path.parts or not path.parts or str(path) == "." or repo_root is None:
        raise ValueError("path")
    try:
        root = repo_root.resolve(strict=True)
        if not root.is_dir():
            raise ValueError("path")
        (root / str(path)).resolve(strict=False).relative_to(root)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ValueError("path") from exc
    return anchor.model_copy(update={"value": str(path)})


def render_fix_span(payload: dict[str, Any]) -> str:
    """Render every fact in fixed order, before redaction and token composition."""
    if not is_structured(payload):
        return payload.get("summary", "")
    span = FixSpan.model_validate(payload["fix_span"])
    anchors = ", ".join(a.kind + ":" + a.value for a in span.anchors)
    constraints = "; ".join(span.constraints) if span.constraints else "none"
    return (
        "symptom: "
        + span.symptom
        + "\nroot cause: "
        + span.root_cause
        + "\nfix: "
        + span.fix_pattern
        + "\nanchors: "
        + anchors
        + "\nconstraints: "
        + constraints
        + "\nlocator: "
        + span.locator.kind
        + ":"
        + span.locator.value
    )
