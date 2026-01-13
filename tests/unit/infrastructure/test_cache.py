"""Tests for response cache implementation.

Tests the ResponseCache class for TTL expiration, size-based eviction,
and cleanup behavior.
"""

import asyncio
import time

import pytest

from mosk_mcp.infrastructure.cache import CacheEntry, ResponseCache


class TestCacheEntry:
    """Tests for CacheEntry dataclass."""

    def test_is_expired_false_when_fresh(self) -> None:
        """Test entry is not expired when just created."""
        entry = CacheEntry(
            value="test",
            created_at=time.monotonic(),
            ttl_seconds=30.0,
        )
        assert entry.is_expired is False

    def test_is_expired_true_after_ttl(self) -> None:
        """Test entry is expired after TTL."""
        entry = CacheEntry(
            value="test",
            created_at=time.monotonic() - 31,  # 31 seconds ago
            ttl_seconds=30.0,
        )
        assert entry.is_expired is True

    def test_age_seconds(self) -> None:
        """Test age_seconds calculation."""
        past = time.monotonic() - 10
        entry = CacheEntry(value="test", created_at=past, ttl_seconds=30.0)
        assert entry.age_seconds >= 10


class TestResponseCacheInitialization:
    """Tests for cache initialization."""

    def test_default_config(self) -> None:
        """Test cache with default configuration."""
        cache = ResponseCache()
        assert cache.default_ttl_seconds == 30.0
        assert cache.max_entries == 1000
        assert cache.cleanup_interval_seconds == 60.0

    def test_custom_config(self) -> None:
        """Test cache with custom configuration."""
        cache = ResponseCache(
            default_ttl_seconds=60.0,
            max_entries=500,
            cleanup_interval_seconds=120.0,
        )
        assert cache.default_ttl_seconds == 60.0
        assert cache.max_entries == 500
        assert cache.cleanup_interval_seconds == 120.0

    def test_initial_metrics(self) -> None:
        """Test initial metrics are zero."""
        cache = ResponseCache()
        metrics = cache.metrics
        assert metrics["entries"] == 0
        assert metrics["hits"] == 0
        assert metrics["misses"] == 0
        assert metrics["hit_rate"] == 0.0
        assert metrics["evictions"] == 0


class TestResponseCacheGetSet:
    """Tests for get and set operations."""

    @pytest.mark.asyncio
    async def test_set_and_get(self) -> None:
        """Test basic set and get."""
        cache = ResponseCache()
        await cache.set("key1", "value1")

        hit, value = await cache.get("key1")
        assert hit is True
        assert value == "value1"

    @pytest.mark.asyncio
    async def test_get_miss(self) -> None:
        """Test cache miss returns False."""
        cache = ResponseCache()
        hit, value = await cache.get("nonexistent")
        assert hit is False
        assert value is None

    @pytest.mark.asyncio
    async def test_get_increments_hits(self) -> None:
        """Test that get increments hit counter."""
        cache = ResponseCache()
        await cache.set("key1", "value1")

        await cache.get("key1")
        await cache.get("key1")

        assert cache.metrics["hits"] == 2

    @pytest.mark.asyncio
    async def test_get_miss_increments_misses(self) -> None:
        """Test that cache miss increments miss counter."""
        cache = ResponseCache()
        await cache.get("nonexistent")
        await cache.get("other")

        assert cache.metrics["misses"] == 2

    @pytest.mark.asyncio
    async def test_set_with_custom_ttl(self) -> None:
        """Test set with custom TTL."""
        cache = ResponseCache(default_ttl_seconds=30.0)
        await cache.set("key1", "value1", ttl_seconds=5.0)

        # Entry should exist with custom TTL
        hit, _value = await cache.get("key1")
        assert hit is True

    @pytest.mark.asyncio
    async def test_hit_rate_calculation(self) -> None:
        """Test hit rate is calculated correctly."""
        cache = ResponseCache()
        await cache.set("key1", "value1")

        # 2 hits, 1 miss
        await cache.get("key1")  # hit
        await cache.get("key1")  # hit
        await cache.get("miss")  # miss

        assert cache.metrics["hits"] == 2
        assert cache.metrics["misses"] == 1
        # 2 / 3 = 0.666...
        assert 0.66 < cache.metrics["hit_rate"] < 0.67


class TestResponseCacheTTL:
    """Tests for TTL expiration."""

    @pytest.mark.asyncio
    async def test_expired_entry_returns_miss(self) -> None:
        """Test that expired entries return cache miss."""
        cache = ResponseCache(default_ttl_seconds=0.1)  # 100ms TTL
        await cache.set("key1", "value1")

        # Wait for expiration
        await asyncio.sleep(0.15)

        hit, value = await cache.get("key1")
        assert hit is False
        assert value is None

    @pytest.mark.asyncio
    async def test_expired_entry_is_removed(self) -> None:
        """Test that expired entries are removed on access."""
        cache = ResponseCache(default_ttl_seconds=0.1)
        await cache.set("key1", "value1")

        # Entry exists initially
        assert cache.metrics["entries"] == 1

        # Wait for expiration
        await asyncio.sleep(0.15)

        # Access triggers removal
        await cache.get("key1")
        assert cache.metrics["entries"] == 0


class TestResponseCacheEviction:
    """Tests for size-based eviction."""

    @pytest.mark.asyncio
    async def test_eviction_at_capacity(self) -> None:
        """Test that oldest entry is evicted at capacity."""
        cache = ResponseCache(max_entries=3)

        await cache.set("key1", "value1")
        await asyncio.sleep(0.01)
        await cache.set("key2", "value2")
        await asyncio.sleep(0.01)
        await cache.set("key3", "value3")

        # At capacity, adding new entry should evict oldest (key1)
        await cache.set("key4", "value4")

        assert cache.metrics["entries"] == 3
        assert cache.metrics["evictions"] == 1

        # key1 should be evicted
        hit, _ = await cache.get("key1")
        assert hit is False

        # key4 should exist
        hit, _ = await cache.get("key4")
        assert hit is True


class TestResponseCacheInvalidation:
    """Tests for cache invalidation."""

    @pytest.mark.asyncio
    async def test_invalidate_existing_key(self) -> None:
        """Test invalidating an existing key."""
        cache = ResponseCache()
        await cache.set("key1", "value1")

        result = await cache.invalidate("key1")
        assert result is True
        assert cache.metrics["entries"] == 0

    @pytest.mark.asyncio
    async def test_invalidate_nonexistent_key(self) -> None:
        """Test invalidating a nonexistent key."""
        cache = ResponseCache()
        result = await cache.invalidate("nonexistent")
        assert result is False

    @pytest.mark.asyncio
    async def test_invalidate_pattern(self) -> None:
        """Test invalidating entries by pattern."""
        cache = ResponseCache()
        await cache.set("user:1:profile", "data1")
        await cache.set("user:1:settings", "data2")
        await cache.set("user:2:profile", "data3")
        await cache.set("other:key", "data4")

        # Invalidate all user:1: entries
        count = await cache.invalidate_pattern("user:1:")
        assert count == 2
        assert cache.metrics["entries"] == 2

        # Verify correct entries remain
        hit, _ = await cache.get("user:2:profile")
        assert hit is True
        hit, _ = await cache.get("other:key")
        assert hit is True

    @pytest.mark.asyncio
    async def test_clear(self) -> None:
        """Test clearing all entries."""
        cache = ResponseCache()
        await cache.set("key1", "value1")
        await cache.set("key2", "value2")
        await cache.set("key3", "value3")

        count = await cache.clear()
        assert count == 3
        assert cache.metrics["entries"] == 0


class TestResponseCacheKeyGeneration:
    """Tests for cache key generation."""

    def test_generate_key_from_args(self) -> None:
        """Test key generation from positional args."""
        cache = ResponseCache()
        key = cache._generate_key("a", "b", "c")
        assert key == "a:b:c"

    def test_generate_key_from_kwargs(self) -> None:
        """Test key generation from keyword args."""
        cache = ResponseCache()
        key = cache._generate_key(x=1, y=2)
        assert key == "x=1:y=2"

    def test_generate_key_mixed(self) -> None:
        """Test key generation from mixed args."""
        cache = ResponseCache()
        key = cache._generate_key("prefix", id=123, type="user")
        assert key == "prefix:id=123:type=user"


class TestResponseCacheCleanup:
    """Tests for cleanup task."""

    @pytest.mark.asyncio
    async def test_start_and_stop_cleanup_task(self) -> None:
        """Test starting and stopping cleanup task."""
        cache = ResponseCache(cleanup_interval_seconds=0.1)

        await cache.start_cleanup_task()
        assert cache._cleanup_task is not None
        assert not cache._cleanup_task.done()

        await cache.stop_cleanup_task()
        assert cache._cleanup_task is None

    @pytest.mark.asyncio
    async def test_cleanup_removes_expired(self) -> None:
        """Test that cleanup removes expired entries."""
        cache = ResponseCache(
            default_ttl_seconds=0.05,  # 50ms TTL
            cleanup_interval_seconds=0.1,  # 100ms cleanup interval
        )

        await cache.set("key1", "value1")
        assert cache.metrics["entries"] == 1

        # Start cleanup and wait for it to run
        await cache.start_cleanup_task()
        await asyncio.sleep(0.2)  # Wait for cleanup to run

        # Entry should be expired and cleaned up
        assert cache.metrics["entries"] == 0

        await cache.stop_cleanup_task()

    @pytest.mark.asyncio
    async def test_cleanup_handles_cancellation(self) -> None:
        """Test that cleanup task handles cancellation gracefully."""
        cache = ResponseCache(cleanup_interval_seconds=1.0)

        await cache.start_cleanup_task()
        await asyncio.sleep(0.01)  # Let task start

        # Should not raise
        await cache.stop_cleanup_task()

    @pytest.mark.asyncio
    async def test_set_restarts_stopped_cleanup(self) -> None:
        """Test that set() restarts cleanup task if it stopped."""
        cache = ResponseCache(cleanup_interval_seconds=60.0)

        # Ensure no cleanup task
        assert cache._cleanup_task is None

        # Set should start cleanup task
        await cache.set("key1", "value1")
        assert cache._cleanup_task is not None

        await cache.stop_cleanup_task()


class TestResponseCacheConcurrency:
    """Tests for concurrent access."""

    @pytest.mark.asyncio
    async def test_concurrent_sets(self) -> None:
        """Test concurrent set operations are thread-safe."""
        cache = ResponseCache(max_entries=1000)

        async def set_values(start: int, count: int) -> None:
            for i in range(start, start + count):
                await cache.set(f"key{i}", f"value{i}")

        # Set 100 values concurrently from 10 coroutines
        tasks = [set_values(i * 10, 10) for i in range(10)]
        await asyncio.gather(*tasks)

        assert cache.metrics["entries"] == 100

        await cache.stop_cleanup_task()

    @pytest.mark.asyncio
    async def test_concurrent_get_set(self) -> None:
        """Test concurrent get and set operations."""
        cache = ResponseCache()

        async def operations() -> None:
            await cache.set("shared", "value")
            await cache.get("shared")
            await cache.get("missing")

        tasks = [operations() for _ in range(20)]
        await asyncio.gather(*tasks)

        # Should have some hits and misses
        assert cache.metrics["hits"] > 0
        assert cache.metrics["misses"] > 0

        await cache.stop_cleanup_task()
