"""In-process recall cache — W5 predictive prefetch (protention).

A bounded LRU keyed by (project, query). plan_checkpoint pre-warms it with the
predicted next step; recall() reads it first. In-process only (no new service);
invalidated per-project on write to avoid staleness. hit/miss counters feed the
cache_hit_rate gate axis.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Any


class RecallCache:
    def __init__(self, max_size: int = 128) -> None:
        self._store: "OrderedDict[tuple[str, str], Any]" = OrderedDict()
        self._max = max_size
        self.hits = 0
        self.misses = 0

    @staticmethod
    def _key(project: str | None, query: str) -> tuple[str, str]:
        return (project or "", query)

    def peek(self, project: str | None, query: str) -> bool:
        """True if (project, query) is cached — WITHOUT counting a hit/miss."""
        return self._key(project, query) in self._store

    def get(self, project: str | None, query: str) -> Any | None:
        """Return the cached result (counting a hit) or None (counting a miss)."""
        k = self._key(project, query)
        if k in self._store:
            self._store.move_to_end(k)
            self.hits += 1
            return self._store[k]
        self.misses += 1
        return None

    def put(self, project: str | None, query: str, value: Any) -> None:
        k = self._key(project, query)
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
