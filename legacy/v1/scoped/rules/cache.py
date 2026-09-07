"""TTL-based in-memory cache for rule lookups."""

from __future__ import annotations

import threading
import time
from typing import Any


class RuleCache:
    """Thread-safe cache for rule query results with TTL expiry.

    Used by RuleEngine to avoid re-querying the database on every
    evaluate() call. Invalidated by RuleStore on mutations.

    Args:
        ttl_seconds: Time-to-live for cache entries. After this many
            seconds, entries are considered stale and re-fetched.
    """

    def __init__(self, ttl_seconds: float = 60.0) -> None:
        self._ttl = ttl_seconds
        self._store: dict[str, tuple[float, Any]] = {}  # key -> (expires_at, value)
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0

    def get(self, key: str) -> Any | None:
        """Return cached value or None if expired/missing."""
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                self._misses += 1
                return None
            expires_at, value = entry
            if time.monotonic() > expires_at:
                del self._store[key]
                self._misses += 1
                return None
            self._hits += 1
            return value

    def put(self, key: str, value: Any) -> None:
        """Store a value with TTL expiry."""
        with self._lock:
            self._store[key] = (time.monotonic() + self._ttl, value)

    def invalidate(self, key: str | None = None) -> None:
        """Invalidate a specific key, or all keys if None."""
        with self._lock:
            if key is None:
                self._store.clear()
            else:
                self._store.pop(key, None)

    def stats(self) -> dict[str, Any]:
        """Return cache hit/miss statistics."""
        with self._lock:
            total = self._hits + self._misses
            return {
                "hits": self._hits,
                "misses": self._misses,
                "hit_rate": self._hits / total if total > 0 else 0.0,
                "size": len(self._store),
                "ttl_seconds": self._ttl,
            }
