"""In-process recall cache — W5 predictive prefetch (protention).

A bounded LRU. plan_checkpoint pre-warms it with the predicted next step; recall()
reads it first. In-process only (no new service); invalidated per-project on write
to avoid staleness. hit/miss counters feed the cache_hit_rate gate axis.

Battle-test fix (MEDIUM): the key MUST include every dimension that changes which
records a recall returns — agent_class_visibility, sensitivity_tag, the layer
subset, k, and caller_tier — not just (project, query). Keying on (project, query)
alone served a result computed for one visibility/filter to a different caller: a
correctness bug and a cross-visibility leak. The key still leads with project so
invalidate_project() stays O(n) over keys with key[0] == project.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Any

# Full cache key: project first (invalidation pivot), then every recall-shaping
# dimension. Tuples are hashable; layers is normalized to a sorted tuple.
_Key = tuple[str, str, int | None, tuple[str, ...] | None, str | None, str | None, str | None]


class RecallCache:
    def __init__(self, max_size: int = 128) -> None:
        self._store: "OrderedDict[_Key, Any]" = OrderedDict()
        self._max = max_size
        self.hits = 0
        self.misses = 0

    @staticmethod
    def _key(
        project: str | None,
        query: str,
        *,
        k: int | None = None,
        layers: Any = None,
        visibility: str | None = None,
        sensitivity: str | None = None,
        tier: str | None = None,
    ) -> _Key:
        layers_norm = tuple(sorted(layers)) if layers else None
        return (project or "", query, k, layers_norm, visibility, sensitivity, tier)

    def peek(self, project: str | None, query: str, **ctx: Any) -> bool:
        """True if the keyed entry is cached — WITHOUT counting a hit/miss."""
        return self._key(project, query, **ctx) in self._store

    def get(self, project: str | None, query: str, **ctx: Any) -> Any | None:
        """Return the cached result (counting a hit) or None (counting a miss)."""
        k = self._key(project, query, **ctx)
        if k in self._store:
            self._store.move_to_end(k)
            self.hits += 1
            return self._store[k]
        self.misses += 1
        return None

    def put(self, project: str | None, query: str, value: Any, **ctx: Any) -> None:
        k = self._key(project, query, **ctx)
        self._store[k] = value
        self._store.move_to_end(k)
        while len(self._store) > self._max:
            self._store.popitem(last=False)

    def invalidate_project(self, project: str | None) -> None:
        """Drop cached entries for *project* (called on a write into that scope)."""
        target = project or ""
        for k in [k for k in self._store if k[0] == target]:
            del self._store[k]

    def hit_rate(self) -> float | None:
        total = self.hits + self.misses
        return self.hits / total if total else None
