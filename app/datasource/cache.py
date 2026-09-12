"""Tiny in-memory TTL cache, used to avoid refetching news/options every scan cycle."""

from __future__ import annotations

import threading
import time
from typing import Any, Callable


class TTLCache:
    def __init__(self, ttl_seconds: float):
        self.ttl_seconds = ttl_seconds
        self._store: dict[Any, tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def get_or_set(self, key: Any, factory: Callable[[], Any]) -> Any:
        now = time.monotonic()
        with self._lock:
            cached = self._store.get(key)
            if cached is not None and now - cached[0] < self.ttl_seconds:
                return cached[1]
        value = factory()
        with self._lock:
            self._store[key] = (now, value)
        return value
