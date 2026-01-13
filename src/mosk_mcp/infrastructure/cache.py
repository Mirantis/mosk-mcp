"""Response caching with TTL and eviction.

This module provides a thread-safe response cache with time-based expiration,
size-based eviction, and automatic cleanup for use in MCP server contexts.

Features:
- Time-based expiration (TTL)
- Size-based eviction (LRU)
- Cache statistics
- Automatic background cleanup
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from mosk_mcp.observability.logging import get_logger


logger = get_logger(__name__)

T = TypeVar("T")


@dataclass
class CacheEntry(Generic[T]):
    """Cache entry with TTL tracking.

    Attributes:
        value: Cached value.
        created_at: When entry was created.
        ttl_seconds: Time-to-live in seconds.
        hits: Number of cache hits.
    """

    value: T
    created_at: float
    ttl_seconds: float
    hits: int = 0

    @property
    def is_expired(self) -> bool:
        """Check if entry has expired."""
        return time.monotonic() - self.created_at > self.ttl_seconds

    @property
    def age_seconds(self) -> float:
        """Get age of entry in seconds."""
        return time.monotonic() - self.created_at


class ResponseCache:
    """Thread-safe response cache with TTL and eviction.

    Features:
    - Time-based expiration (TTL)
    - Size-based eviction (LRU)
    - Cache statistics
    - Automatic cleanup
    """

    def __init__(
        self,
        default_ttl_seconds: float = 30.0,
        max_entries: int = 1000,
        cleanup_interval_seconds: float = 60.0,
    ) -> None:
        """Initialize response cache.

        Args:
            default_ttl_seconds: Default TTL for entries.
            max_entries: Maximum cache entries before eviction.
            cleanup_interval_seconds: Interval for cleanup task.
        """
        self.default_ttl_seconds = default_ttl_seconds
        self.max_entries = max_entries
        self.cleanup_interval_seconds = cleanup_interval_seconds

        self._cache: dict[str, CacheEntry[Any]] = {}
        self._lock = asyncio.Lock()
        self._hits = 0
        self._misses = 0
        self._evictions = 0
        self._cleanup_task: asyncio.Task[None] | None = None

    @property
    def metrics(self) -> dict[str, Any]:
        """Get cache metrics."""
        total = self._hits + self._misses
        hit_rate = self._hits / total if total > 0 else 0.0
        return {
            "entries": len(self._cache),
            "max_entries": self.max_entries,
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": hit_rate,
            "evictions": self._evictions,
        }

    def _generate_key(self, *args: Any, **kwargs: Any) -> str:
        """Generate cache key from arguments."""
        key_parts = [str(arg) for arg in args]
        key_parts.extend(f"{k}={v}" for k, v in sorted(kwargs.items()))
        return ":".join(key_parts)

    async def get(self, key: str) -> tuple[bool, Any]:
        """Get value from cache.

        Args:
            key: Cache key.

        Returns:
            Tuple of (hit, value). If hit is False, value is None.
        """
        # Auto-restart cleanup task if it stopped due to errors
        # This ensures read-only workloads also trigger cleanup restart
        if self._cleanup_task is None or self._cleanup_task.done():
            await self.start_cleanup_task()

        async with self._lock:
            entry = self._cache.get(key)

            if entry is None:
                self._misses += 1
                return False, None

            if entry.is_expired:
                del self._cache[key]
                self._misses += 1
                return False, None

            entry.hits += 1
            self._hits += 1
            return True, entry.value

    async def set(
        self,
        key: str,
        value: Any,
        ttl_seconds: float | None = None,
    ) -> None:
        """Set value in cache.

        Args:
            key: Cache key.
            value: Value to cache.
            ttl_seconds: Optional TTL override.
        """
        async with self._lock:
            # Evict if at capacity
            if len(self._cache) >= self.max_entries:
                await self._evict_oldest()

            self._cache[key] = CacheEntry(
                value=value,
                created_at=time.monotonic(),
                ttl_seconds=ttl_seconds or self.default_ttl_seconds,
            )

        # Auto-restart cleanup task if it stopped due to errors
        if self._cleanup_task is None or self._cleanup_task.done():
            await self.start_cleanup_task()

    async def invalidate(self, key: str) -> bool:
        """Invalidate a cache entry.

        Args:
            key: Cache key to invalidate.

        Returns:
            True if entry was found and removed.
        """
        async with self._lock:
            if key in self._cache:
                del self._cache[key]
                return True
            return False

    async def invalidate_pattern(self, pattern: str) -> int:
        """Invalidate entries matching a pattern.

        Args:
            pattern: Pattern to match (simple prefix matching).

        Returns:
            Number of entries invalidated.
        """
        async with self._lock:
            keys_to_remove = [k for k in self._cache if k.startswith(pattern)]
            for key in keys_to_remove:
                del self._cache[key]
            return len(keys_to_remove)

    async def clear(self) -> int:
        """Clear all cache entries.

        Returns:
            Number of entries cleared.
        """
        async with self._lock:
            count = len(self._cache)
            self._cache.clear()
            logger.info("cache_cleared", entries=count)
            return count

    async def _evict_oldest(self) -> None:
        """Evict oldest entry (must be called with lock held)."""
        if not self._cache:
            return

        # Find oldest entry
        oldest_key = min(self._cache.keys(), key=lambda k: self._cache[k].created_at)
        del self._cache[oldest_key]
        self._evictions += 1

    async def _cleanup_expired(self) -> int:
        """Remove expired entries.

        Returns:
            Number of entries removed.
        """
        async with self._lock:
            expired_keys = [k for k, v in self._cache.items() if v.is_expired]
            for key in expired_keys:
                del self._cache[key]
            if expired_keys:
                logger.debug("cache_cleanup", removed=len(expired_keys))
            return len(expired_keys)

    async def start_cleanup_task(self) -> None:
        """Start background cleanup task."""
        if self._cleanup_task is None or self._cleanup_task.done():
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())

    async def stop_cleanup_task(self) -> None:
        """Stop background cleanup task."""
        if self._cleanup_task and not self._cleanup_task.done():
            self._cleanup_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._cleanup_task
            self._cleanup_task = None

    async def _cleanup_loop(self) -> None:
        """Background cleanup loop with exponential backoff on errors."""
        consecutive_errors = 0
        max_backoff = 300  # Max 5 minutes between retries
        max_consecutive_errors = 10  # Stop after 10 consecutive errors
        base_interval = self.cleanup_interval_seconds

        while True:
            try:
                # Use backoff interval if we've had errors
                if consecutive_errors > 0:
                    backoff = min(base_interval * (2**consecutive_errors), max_backoff)
                    logger.warning(
                        "cache_cleanup_backoff",
                        consecutive_errors=consecutive_errors,
                        backoff_seconds=backoff,
                    )
                    await asyncio.sleep(backoff)
                else:
                    await asyncio.sleep(base_interval)

                await self._cleanup_expired()
                consecutive_errors = 0  # Reset on success
            except asyncio.CancelledError:
                break
            except Exception as e:
                consecutive_errors += 1
                logger.error(
                    "cache_cleanup_error",
                    error=str(e),
                    consecutive_errors=consecutive_errors,
                )
                # Stop loop after too many consecutive errors
                if consecutive_errors >= max_consecutive_errors:
                    logger.critical(
                        "cache_cleanup_loop_stopped",
                        reason="max_consecutive_errors_exceeded",
                        max_errors=max_consecutive_errors,
                        message="Cleanup loop stopped. Will restart on next cache operation.",
                    )
                    break
