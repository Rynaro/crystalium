"""Ricoeur-protected class resolution — W4 forgetting faculty.

A protected crystal is exempt from decay/eviction/re-surfacing (the "duty of
memory", Ricoeur 2000). The protected-class definition is FINAL for W4 — do not
widen it to dodge the eviction gate. A crystal is protected when ANY of:

  - the operator explicitly set payload.protected == True;
  - provenance.source == "human" (operator-authored facts are never auto-forgotten);
  - it carries the [DECISION] tag (a recorded decision must not silently fade).

Audit-log records (right-to-be-forgotten ledger) are protected by construction.
"""

from __future__ import annotations

from typing import Any

DECISION_MARKER = "[DECISION]"


def _has_decision_tag(tags: list[Any]) -> bool:
    for t in tags:
        if DECISION_MARKER.lower() in str(t).lower():
            return True
    return False


def resolve_protection(payload: dict[str, Any], provenance_source: str | None) -> tuple[bool, list[str]]:
    """Return (protected, tags) for a commit.

    Pure + deterministic. tags is normalised to a list[str]; protected applies the
    FINAL auto-protect rule (explicit OR human provenance OR [DECISION] tag).
    """
    raw_tags = payload.get("tags") or []
    tags = [str(t) for t in raw_tags] if isinstance(raw_tags, (list, tuple)) else [str(raw_tags)]
    protected = (
        bool(payload.get("protected"))
        or provenance_source == "human"
        or _has_decision_tag(tags)
    )
    return protected, tags
