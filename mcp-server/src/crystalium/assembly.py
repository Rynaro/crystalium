"""Task-conditional context assembly, separate from explicit memory search.

Only protected, redacted recall summaries enter selection. Anchored 10% relevance
bands avoid a non-transitive comparator. Within a band, a short (<=128 summary
tokens) fix/outcome cue wins, then summary length, original hybrid rank and id.
Cues are bounded text heuristics, not verified fixes or structured fix spans.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import TYPE_CHECKING

from crystalium.schemas import AssembleContextRequest, AssembleContextResult, CrystalSummary, Scope

if TYPE_CHECKING:
    from crystalium.aetheryte.retrieve import Aetheryte
    from crystalium.config import Config
    from crystalium.trust import Tier

_FIX_CUE = re.compile(r"\b(fix(?:ed|es)?|resolv(?:ed|e)|outcome|tests? pass(?:ed)?)\b", re.I)


def select_records(
    records: list[CrystalSummary],
    tokenize: Callable[[str], int] | None = None,
) -> list[CrystalSummary]:
    """Order comparable redacted summaries deterministically; never read payloads."""
    # The whitespace fallback is for standalone policy inspection only; assembly
    # always supplies the existing composer's tokenizer.
    count = tokenize if callable(tokenize) else lambda text: len(text.split())
    ranked = sorted(
        enumerate(records), key=lambda pair: (-(pair[1].score or 0), pair[0], pair[1].id)
    )
    ordered: list[CrystalSummary] = []
    while ranked:
        anchor = ranked[0][1].score or 0
        boundary = 1
        while boundary < len(ranked):
            score = ranked[boundary][1].score or 0
            if not (score == anchor if anchor <= 0 else score >= anchor * 0.9):
                break
            boundary += 1
        band, ranked = ranked[:boundary], ranked[boundary:]

        def preference(pair: tuple[int, CrystalSummary]) -> tuple[bool, int, int, str]:
            rank, record = pair
            length = count(record.summary)
            short_fix = length <= 128 and bool(_FIX_CUE.search(record.summary[:4096]))
            return (not short_fix, length, rank, record.id)

        ordered.extend(record for _, record in sorted(band, key=preference))
    return ordered


def assemble_context(
    request: AssembleContextRequest,
    aetheryte: Aetheryte,
    config: Config,
    caller_tier: Tier,
) -> AssembleContextResult:
    """Gate before recall/composition; retain protected retrieval and cap last."""
    mode = config.memory_injection_mode
    if mode == "off":
        reason = "off"
    elif mode == "always_on":
        reason = "always_on"
    elif request.needs_memory:
        reason = "needs_memory"
    elif request.prior_failure:
        reason = "prior_failure"
    else:
        reason = "no_signal"
    enabled = reason not in ("off", "no_signal")
    records: list[CrystalSummary] = []
    tokens = 0
    if enabled:
        recalled = aetheryte.recall(
            scope=Scope(**request.scope.model_dump()),
            query=request.query,
            k=min(100, max(10, request.k)),
            layers=request.layers,
            caller_tier=caller_tier,
        )
        records = select_records(recalled.records, aetheryte.composer.tokenize)[: request.k]
        tokens = sum(aetheryte.composer.tokenize(record.summary) for record in records)
    return AssembleContextResult(
        records=records,
        gate_open=enabled,
        gate_reason=reason,
        mode=mode,
        k_requested=request.k,
        injected_token_count=tokens,
    )
