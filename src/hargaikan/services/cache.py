"""Small thread-safe time-based cache for public catalog / band / prediction data (layer 3).

Never used for user data (profiles, preferences, allergies, budgets). Failed loads are not cached.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Hashable
from typing import Any, TypeVar

T = TypeVar("T")


class TTLCache:
    """``get(key, loader)`` returns a cached value younger than ``ttl_s`` or calls ``loader``.

    ``ttl_s=None`` caches forever (memory backend). At most ``max_entries`` keys are kept; the
    least recently stored key is evicted first.
    """

    def __init__(self, ttl_s: float | None, *, max_entries: int = 2048, clock: Callable[[], float] = time.monotonic) -> None:
        self.ttl_s = ttl_s
        self.max_entries = max_entries
        self._clock = clock
        self._data: OrderedDict[Hashable, tuple[float, Any]] = OrderedDict()
        self._lock = threading.Lock()

    def _fresh(self, stored_at: float) -> bool:
        return self.ttl_s is None or self._clock() - stored_at < self.ttl_s

    def get(self, key: Hashable, loader: Callable[[], T]) -> T:
        with self._lock:
            hit = self._data.get(key)
            if hit is not None and self._fresh(hit[0]):
                return hit[1]
        value = loader()  # exceptions propagate; nothing is cached
        self.put(key, value)
        return value

    def put(self, key: Hashable, value: Any) -> None:
        with self._lock:
            self._data[key] = (self._clock(), value)
            self._data.move_to_end(key)
            while len(self._data) > self.max_entries:
                self._data.popitem(last=False)

    def stale(self, key: Hashable) -> Any | None:
        """The last stored value for ``key`` regardless of age (None if never stored)."""
        with self._lock:
            hit = self._data.get(key)
            return hit[1] if hit is not None else None

    def clear(self) -> None:
        with self._lock:
            self._data.clear()
