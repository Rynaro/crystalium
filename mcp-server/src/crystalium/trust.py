"""Trust tier enumeration and pure-function trust combinators.

The tier system is the foundation of CRYSTALIUM's write-gating (MISSION.md §Keystone,
FORGE D1). Trust tier carries through cross-agent reads and summarization:
consolidated.tier = min(inputs.tier) — never resets to T1 (P0-6).

Layer ceiling map enforces which tiers may write to which layers (D1 tier matrix).
"""

from __future__ import annotations

from enum import IntEnum
from typing import Iterable


# ---------------------------------------------------------------------------
# Tier enum — ordered lowest-trust-first so min() is natural
# ---------------------------------------------------------------------------


class Tier(IntEnum):
    """Trust tier for callers and crystals.

    T0 = human operator (highest trust)
    T1 = verified agent (e.g. Eidolon with T1 identity)
    T2 = unverified agent (output admitted as 'candidate')
    T3 = environment / tool-ingested (lowest trust; Episodic-quarantine only)
    """

    T0 = 0  # human operator
    T1 = 1  # verified agent
    T2 = 2  # unverified agent
    T3 = 3  # environment / tool-ingested

    def __str__(self) -> str:  # noqa: D105
        return self.name

    @classmethod
    def from_str(cls, s: str) -> "Tier":
        """Parse 'T0', 'T1', 'T2', 'T3' (case-insensitive)."""
        upper = s.upper()
        try:
            return cls[upper]
        except KeyError as exc:
            raise ValueError(f"Unknown trust tier: {s!r}. Expected T0, T1, T2, or T3.") from exc


# ---------------------------------------------------------------------------
# Layer type alias (string enum used in schemas)
# ---------------------------------------------------------------------------

VALID_LAYERS: frozenset[str] = frozenset({"episodic", "semantic", "procedural", "execution"})


# ---------------------------------------------------------------------------
# Layer ceiling map (D1)
#
# A commit to a layer is only allowed if the caller's tier <= the layer's ceiling.
# Note: lower Tier value = higher trust (T0=0 is highest).
#
# Ceilings:
#   episodic   — T3 (all tiers; T3 commits land as quarantined via allow_quarantine)
#   semantic   — T1 (T0 and T1 only)
#   procedural — T2 (T0, T1, T2; T2 lands as candidate)
#   execution  — T1 (T0 and T1 only)
# ---------------------------------------------------------------------------

LAYER_CEILING: dict[str, Tier] = {
    "episodic": Tier.T3,    # universally writable; T3 quarantine is enforcement-side
    "semantic": Tier.T1,    # T0+T1 only
    "procedural": Tier.T2,  # T0+T1+T2; T2 candidate-only
    "execution": Tier.T1,   # T0+T1 only
}


# ---------------------------------------------------------------------------
# Pure-function combinators
# ---------------------------------------------------------------------------


def min_tier(tiers: Iterable[Tier]) -> Tier:
    """Return the LOWEST-TRUST tier (highest integer value) from *tiers*.

    Used for trust propagation through summarization (FORGE D7, P0-6):
        consolidated.tier = min_tier(inputs) — never resets to T1.

    Raises ValueError on empty input.
    """
    result: Tier | None = None
    for t in tiers:
        if result is None or t > result:
            result = t
    if result is None:
        raise ValueError("min_tier() called with empty iterable.")
    return result


def tier_within_ceiling(tier: Tier, layer: str) -> bool:
    """Return True if *tier* is allowed to commit to *layer* (i.e. tier <= ceiling).

    T3 is allowed for episodic (it lands as quarantined) but denied for all others.
    Does NOT check the quarantine / candidate nuance — that is enforcement.py's job.
    """
    ceiling = LAYER_CEILING.get(layer)
    if ceiling is None:
        raise ValueError(f"Unknown layer: {layer!r}. Expected one of {sorted(VALID_LAYERS)}.")
    return tier <= ceiling
