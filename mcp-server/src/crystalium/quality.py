"""Summary-quality gate — mechanical, advisory-only (soft in v1.6).

MOTIVATING INCIDENT (CHANGELOG v1.6.0): the only FTS-indexed text on a live
project's crystals was terse machine labels (e.g. "plan_checkpoint:08234787"),
which no human-typed BM25/FTS5 query could ever match. `summary` is the sole
FTS-indexed field (crystal.v1.json), so a low-signal summary silently degrades
recall no differently than dropping the row from the index.

This is a mechanical (non-LLM), cheap heuristic — never a hard gate in a minor
version: a failing summary is still ACCEPTED (writers never break), but the
tool result carries `summary_quality: "poor"` plus a one-line advisory so the
caller can see and fix it. `plan_checkpoint` additionally auto-enriches its
own server-composed default label (see layers/execution.py) — callers'
explicit summaries are never silently rewritten.
"""

from __future__ import annotations

import re

#: Minimum summary length (chars, after stripping) to pass the gate.
MIN_SUMMARY_LENGTH = 24

#: Minimum count of alphabetic "words" (maximal runs of A-Za-z) to pass.
MIN_ALPHA_WORDS = 3

_ALPHA_WORD_RE = re.compile(r"[A-Za-z]+")

#: The bare machine-label shape this gate exists to catch, e.g.
#: "plan_checkpoint:08234787" or "commit:3fa2b1c0-...".
_MACHINE_LABEL_RE = re.compile(r"^[a-z_]+:[0-9a-f-]+$")

POOR_SUMMARY_ADVICE = (
    "summary is short, word-sparse, or shaped like a bare machine label — the "
    "ONLY FTS-indexed text for this crystal. Write a human-readable summary "
    "(what + project + why) so BM25/FTS5 recall can find it."
)


def is_poor_summary(summary: str | None) -> bool:
    """Return True when *summary* fails the mechanical quality gate.

    Fails when the (stripped) text is shorter than MIN_SUMMARY_LENGTH chars,
    OR has fewer than MIN_ALPHA_WORDS alphabetic words, OR matches the bare
    `^[a-z_]+:[0-9a-f-]+$` machine-label pattern.
    """
    text = (summary or "").strip()
    if len(text) < MIN_SUMMARY_LENGTH:
        return True
    if len(_ALPHA_WORD_RE.findall(text)) < MIN_ALPHA_WORDS:
        return True
    return bool(_MACHINE_LABEL_RE.match(text))
