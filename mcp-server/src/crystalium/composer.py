"""Working-set slot eviction composer — G6 anchor (FORGE D9).

P0-9 (MISSION.md §67-74): the composer assembles at most 3 500 tokens from
typed slots with hard per-slot caps and fully deterministic eviction.

Eviction rule (D9, superseded by crystalium#36 / FORGE DP-1 when
Config.recall_relevance_primary is True — the default since v1.9.0):
  Pop the record with the LOWEST tuple
  (relevance_score, importance, last_access, record_id) where "lowest" means:
    - relevance_score ascending (evict least query-relevant first — PRIMARY
      signal since v1.9.0; read via getattr(rec, "relevance_score", 0.0) so a
      record with no relevance_score attribute ties at 0.0, see C-2)
    - importance  ascending  (evict least important first — the secondary/
      tiebreak signal since v1.9.0; was the sole/primary key pre-1.9.0)
    - last_access ascending  (evict oldest accessed first, i.e. smaller timestamp)
    - record_id   ascending  (lexicographic tiebreak for reproducibility)

  `recall_relevance_primary=False` restores the pre-1.9.0 rule exactly:
  (importance, last_access, record_id), and the composer no longer re-sorts
  its output by relevance either (see `compose()` below) — the emitted order
  reverts to the historical per-slot / eviction-key artefact order.

Slot allocation (spec.yaml §slots):
  executive  300
  procedural 600
  semantic   800
  episodic   800
  execution  1000
  buffer     300
  total_cap  3500

Tokenizer preference (spec.yaml §eviction_rule.tokenizer_preference):
  1. tiktoken cl100k_base — if importable
  2. word_count × 1.3 fallback — deterministic, no deps
     [UNVERIFIED] The ×1.3 multiplier approximates real BPE counts for
     English prose but is NOT validated against a golden token count.

Layer → slot routing:
  episodic   → episodic slot
  semantic   → semantic slot
  procedural → procedural slot
  execution  → execution slot
  records with scope.slot == "executive" → executive slot  (W4 server sets this)
  anything else → buffer slot

Source: spec.yaml §P0-9, §eviction_rule, §slots; FORGE D9.
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, TYPE_CHECKING

import structlog

from crystalium.config import Config

log = structlog.get_logger("crystalium.composer")

if TYPE_CHECKING:
    pass


# ---------------------------------------------------------------------------
# Tokenizer (tiktoken preferred; word-count×1.3 fallback)
# ---------------------------------------------------------------------------


def _build_tokenizer() -> Callable[[str], int]:
    """Build the best available tokenizer.

    Returns a callable: text → int (token count).

    Priority:
      1. tiktoken cl100k_base (accurate BPE count)
      2. len(text.split()) * 1.3, rounded up (deterministic fallback)
         [UNVERIFIED] ×1.3 is a heuristic; accurate within ~10% for typical prose.
    """
    try:
        import tiktoken  # type: ignore[import-untyped]

        enc = tiktoken.get_encoding("cl100k_base")

        def _tiktoken(text: str) -> int:
            # disallowed_special=() → count any special-token STRING embedded in stored
            # content (e.g. '<|endoftext|>') as ordinary text instead of raising and
            # taking down the whole recall. tiktoken defaults disallowed_special="all".
            return len(enc.encode(text, disallowed_special=()))

        log.debug("tokenizer_backend", backend="tiktoken_cl100k_base")
        return _tiktoken

    except (ImportError, Exception):
        # Fallback: word-count × 1.3, minimum 1 per non-empty string
        import math

        def _word_count(text: str) -> int:
            if not text:
                return 0
            return max(1, math.ceil(len(text.split()) * 1.3))

        log.debug("tokenizer_backend", backend="word_count_x1.3_fallback_UNVERIFIED")
        return _word_count


# Module-level tokenizer — built once per process
_TOKENIZER: Callable[[str], int] = _build_tokenizer()


# ---------------------------------------------------------------------------
# Output types
# ---------------------------------------------------------------------------


@dataclass
class ComposedSet:
    """Result from Composer.compose()."""

    records: list[Any]          # list of _ComposerRecord (typed as Any to avoid circular)
    slot_tokens: dict[str, int]  # slot_name → token count after eviction
    total_tokens: int
    evicted_count: int


# ---------------------------------------------------------------------------
# Internal: eviction key for a record
# ---------------------------------------------------------------------------


def _eviction_key(
    rec: Any, relevance_primary: bool = True
) -> tuple[float, float, datetime, str] | tuple[float, datetime, str]:
    """Return the ascending eviction sort key for *rec*.

    Lowest key = evicted first.

    When *relevance_primary* is True (crystalium#36 / FORGE DP-1, the default
    since v1.9.0 via Config.recall_relevance_primary):
      (relevance_score↑, importance↑, last_access↑, record_id↑)

    `getattr(rec, "relevance_score", 0.0)` is load-bearing, not defensive style
    (C-2): a record with no relevance_score attribute — e.g. every hand-built
    stub in the pre-1.9.0 test suite — ties at relevance 0.0, so the legacy
    (importance, last_access, id) ordering survives intact as the equal-
    relevance case.

    When *relevance_primary* is False (pre-1.9.0 behaviour, restored by
    Config.recall_relevance_primary=False):
      (importance↑, last_access↑, record_id↑)
    """
    last_access = rec.last_access
    if last_access.tzinfo is None:
        last_access = last_access.replace(tzinfo=timezone.utc)
    # importance: lower importance → evicted sooner → ascending order → use raw value
    # last_access: older access → evicted sooner → ascending order → use raw datetime
    # record_id: lexicographic tiebreak
    if relevance_primary:
        relevance = getattr(rec, "relevance_score", 0.0)
        return (relevance, rec.importance, last_access, rec.id)
    return (rec.importance, last_access, rec.id)


# ---------------------------------------------------------------------------
# Composer
# ---------------------------------------------------------------------------


class Composer:
    """Slot-budget composer with D9 deterministic eviction.

    Args:
        config:    Server-wide config (provides slot caps + total_cap).
        tokenizer: Optional tokenizer override (default: module-level _TOKENIZER).
                   Useful in tests to inject a deterministic stub.
    """

    # Layer → default slot mapping
    _LAYER_SLOT: dict[str, str] = {
        "episodic": "episodic",
        "semantic": "semantic",
        "procedural": "procedural",
        "execution": "execution",
    }

    def __init__(
        self,
        config: Config,
        tokenizer: Callable[[str], int] | None = None,
        recall_relevance_primary: bool | None = None,
    ) -> None:
        self.config = config
        self._tokenize: Callable[[str], int] = tokenizer if tokenizer is not None else _TOKENIZER
        # crystalium#36 / FORGE DP-2: gates the eviction key (seam 4) and the
        # final response ordering (seam 5) as one unit with Aetheryte's own
        # recall_relevance_primary flag (seam 3+3b). Wired from
        # Config.recall_relevance_primary at server.py's composer construction
        # site; explicit kwarg (not a bare `self.config` read) so a bare
        # `Composer(config)` in a test predating this change still gets the
        # new behaviour without threading the flag through every call site.
        #
        # C-16 / DP-R4(iii) (post-verification, D3 sentinel): defaults to
        # `None`, which falls back to `config.recall_relevance_primary`,
        # rather than a bare `True`. vigil's D3 finding: a hardcoded `True`
        # default meant a FUTURE third construction site that forgot the
        # kwarg would silently run seams 4+5 ON while Aetheryte's seam 3(b)
        # stayed OFF (a half-gated mode no criterion covers, in which
        # `Config.recall_relevance_primary=False` is silently ignored). Both
        # existing production call sites (server.py, __main__.py) keep their
        # explicit `recall_relevance_primary=config.recall_relevance_primary`
        # kwarg, so behaviour at each is byte-identical to before this sentinel.
        self.recall_relevance_primary = (
            config.recall_relevance_primary if recall_relevance_primary is None else recall_relevance_primary
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def tokenize(self, text: str) -> int:
        """Count tokens in *text* using the configured tokenizer.

        Uses tiktoken cl100k_base if importable; falls back to word_count × 1.3.
        The fallback is marked [UNVERIFIED] — see module docstring.
        """
        return self._tokenize(text)

    def compose(self, records: list[Any]) -> ComposedSet:
        """Assemble a slot-budgeted working set from *records*.

        Algorithm:
          1. Route each record to a slot (layer mapping + executive override).
          2. Count tokens per record using the configured tokenizer.
          3. For each slot: while slot.tokens > slot.cap, evict the record
             with the lowest eviction key (D9; crystalium#36 seam 4 — see
             `_eviction_key` for the relevance-primary vs legacy rule).
          4. Assert total_tokens <= total_cap (G6 invariant).
          5. crystalium#36 seam 5 (`self.recall_relevance_primary` only): sort
             the surviving records by descending `relevance_score`, `id`
             ascending as tiebreak — the fused query relevance is now decisive
             in what ordering the caller sees, not an artefact of per-slot
             insertion order. Flag off: emit in the pre-1.9.0 artefact order
             (Pass-1 per-slot append order, or Pass-2's ascending-eviction-key
             order when the global cap pass fired) — byte-identical to D9.
          6. Return ComposedSet.

        Args:
            records: List of _ComposerRecord (or any object with .id, .layer,
                     .summary, .importance, .last_access, .slot_override fields).

        Returns:
            ComposedSet with post-eviction records, slot token breakdown, and
            the count of evicted records.

        Raises:
            AssertionError: If total_tokens > total_cap after eviction (should never
                            fire unless slot caps sum > total_cap; included per G6).
        """
        slot_caps = self.config.slots  # e.g. {"executive": 300, ...}
        total_cap = self.config.total_cap

        # Route records to slots
        # slot_records: slot_name → list of (record, token_count)
        slot_records: dict[str, list[tuple[Any, int]]] = {
            slot: [] for slot in slot_caps
        }

        for rec in records:
            slot = self._route(rec)
            tokens = self._tokenize(rec.summary)
            slot_records[slot].append((rec, tokens))

        # Pass 1 — per-slot eviction: trim each slot to its own (soft) cap.
        evicted_count = 0
        # Keep (rec, tokens, slot_name) so Pass 2 can evict globally and keep the
        # per-slot totals accurate.
        kept: list[tuple[Any, int, str]] = []
        slot_token_totals: dict[str, int] = {slot: 0 for slot in slot_caps}

        for slot_name, cap in slot_caps.items():
            items = slot_records[slot_name]
            slot_total = sum(t for _, t in items)

            if slot_total <= cap:
                for rec, tok in items:
                    kept.append((rec, tok, slot_name))
                slot_token_totals[slot_name] = slot_total
                continue

            # Sort ascending by eviction key (lowest-key = evicted first), trim front.
            remaining = sorted(
                items,
                key=lambda pair: _eviction_key(pair[0], self.recall_relevance_primary),
            )
            running_total = slot_total
            while running_total > cap and remaining:
                evicted_rec, evicted_tokens = remaining.pop(0)
                running_total -= evicted_tokens
                evicted_count += 1
                log.debug("evicted", slot=slot_name, record_id=evicted_rec.id,
                          importance=evicted_rec.importance, tokens=evicted_tokens)

            for rec, tok in remaining:
                kept.append((rec, tok, slot_name))
            slot_token_totals[slot_name] = running_total

        total_tokens = sum(slot_token_totals.values())

        # Pass 2 — global cap reconciliation (G6 invariant). The slot caps are soft
        # reservations whose sum (3800) intentionally over-subscribes the hard 3500
        # total_cap; when enough slots fill, the working set must still be trimmed to
        # total_cap. Per-slot eviction alone can never guarantee this (battle-test
        # HIGH: the old code asserted instead of evicting and crashed recall). Evict
        # the globally lowest-eviction-key records across ALL slots until within cap.
        if total_tokens > total_cap:
            kept.sort(key=lambda triple: _eviction_key(triple[0], self.recall_relevance_primary))
            while total_tokens > total_cap and kept:
                evicted_rec, evicted_tokens, evicted_slot = kept.pop(0)
                total_tokens -= evicted_tokens
                slot_token_totals[evicted_slot] -= evicted_tokens
                evicted_count += 1
                log.debug("evicted_global", slot=evicted_slot, record_id=evicted_rec.id,
                          importance=evicted_rec.importance, tokens=evicted_tokens)

        kept_records = [rec for rec, _, _ in kept]

        # crystalium#36 seam 5 (DP-6, gated): descending relevance_score, id
        # ascending tiebreak. `getattr(..., 0.0)` mirrors the eviction key's
        # degrade-safe read (C-2). Flag off: kept_records keeps whatever order
        # the two eviction passes above produced (byte-identical to pre-1.9.0).
        if self.recall_relevance_primary:
            kept_records.sort(
                key=lambda rec: (-getattr(rec, "relevance_score", 0.0), rec.id)
            )

        # G6 post-condition: the two-pass eviction guarantees this holds by
        # construction; kept as a hard invariant tripwire.
        assert total_tokens <= total_cap, (
            f"WorkingSetOverflow: total_tokens={total_tokens} > total_cap={total_cap}. "
            f"Slot breakdown: {slot_token_totals}."
        )

        return ComposedSet(
            records=kept_records,
            slot_tokens=slot_token_totals,
            total_tokens=total_tokens,
            evicted_count=evicted_count,
        )

    # ------------------------------------------------------------------
    # Internal: slot routing
    # ------------------------------------------------------------------

    def _route(self, rec: Any) -> str:
        """Route *rec* to a slot name.

        Executive override: if rec.slot_override == "executive", route there.
        Otherwise use layer → slot mapping. Unknown layers → buffer.
        """
        override = getattr(rec, "slot_override", None)
        if override == "executive":
            return "executive"

        layer = getattr(rec, "layer", None) or "episodic"
        return self._LAYER_SLOT.get(layer, "buffer")
