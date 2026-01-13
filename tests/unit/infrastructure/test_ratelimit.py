"""Tests for rate limiting implementation.

Tests the RateLimiter class, token bucket, and sliding window counter.
"""

import asyncio

import pytest

from mosk_mcp.infrastructure.ratelimit import (
    GLOBAL_RATE_LIMIT,
    ROLE_RATE_LIMITS,
    RateLimitConfig,
    RateLimiter,
    RateLimitExceeded,
    SlidingWindowCounter,
    TokenBucket,
)


class TestTokenBucket:
    """Tests for TokenBucket class."""

    def test_initialization(self) -> None:
        """Test token bucket is initialized with full tokens."""
        bucket = TokenBucket(capacity=10, refill_rate=1.0)
        assert bucket.tokens == 10
        assert bucket.capacity == 10

    def test_consume_success(self) -> None:
        """Test consuming tokens when available."""
        bucket = TokenBucket(capacity=10, refill_rate=1.0)
        assert bucket.consume(5) is True
        assert bucket.tokens == 5

    def test_consume_failure(self) -> None:
        """Test consuming fails when not enough tokens."""
        bucket = TokenBucket(capacity=5, refill_rate=1.0)
        assert bucket.consume(10) is False
        assert bucket.tokens == 5  # Unchanged

    def test_consume_all_tokens(self) -> None:
        """Test consuming all tokens."""
        bucket = TokenBucket(capacity=5, refill_rate=1.0)
        assert bucket.consume(5) is True
        assert bucket.tokens == 0
        assert bucket.consume(1) is False

    def test_refill_over_time(self) -> None:
        """Test tokens refill over time."""
        bucket = TokenBucket(capacity=10, refill_rate=10.0)  # 10 tokens/sec
        bucket.consume(10)  # Empty the bucket
        assert bucket.tokens == 0

        # Simulate time passing
        bucket.last_update -= 0.5  # 500ms ago
        bucket._refill()

        assert bucket.tokens == pytest.approx(5.0, rel=0.1)

    def test_refill_capped_at_capacity(self) -> None:
        """Test refill doesn't exceed capacity."""
        bucket = TokenBucket(capacity=10, refill_rate=100.0)  # Fast refill
        bucket.consume(5)
        bucket.last_update -= 10  # Long time ago
        bucket._refill()

        assert bucket.tokens == 10  # Capped at capacity

    def test_time_until_available(self) -> None:
        """Test calculating time until tokens available."""
        bucket = TokenBucket(capacity=10, refill_rate=2.0)  # 2 tokens/sec
        bucket.consume(10)  # Empty

        # Need 4 tokens, should take 2 seconds
        wait_time = bucket.time_until_available(4)
        assert wait_time == pytest.approx(2.0, rel=0.1)

    def test_time_until_available_zero_when_available(self) -> None:
        """Test time is zero when tokens are already available."""
        bucket = TokenBucket(capacity=10, refill_rate=1.0)
        assert bucket.time_until_available(5) == 0.0


class TestSlidingWindowCounter:
    """Tests for SlidingWindowCounter class."""

    @pytest.mark.asyncio
    async def test_try_acquire_success(self) -> None:
        """Test acquiring slots when below limit."""
        counter = SlidingWindowCounter(window_size=60.0, max_requests=10)

        assert await counter.try_acquire() is True
        assert counter.current_count == 1

    @pytest.mark.asyncio
    async def test_try_acquire_at_limit(self) -> None:
        """Test acquiring fails at limit."""
        counter = SlidingWindowCounter(window_size=60.0, max_requests=3)

        assert await counter.try_acquire() is True
        assert await counter.try_acquire() is True
        assert await counter.try_acquire() is True
        assert await counter.try_acquire() is False  # At limit

    @pytest.mark.asyncio
    async def test_window_expiration(self) -> None:
        """Test requests expire after window."""
        counter = SlidingWindowCounter(window_size=0.1, max_requests=1)

        assert await counter.try_acquire() is True
        assert await counter.try_acquire() is False

        # Wait for window to expire
        await asyncio.sleep(0.15)

        assert await counter.try_acquire() is True

    @pytest.mark.asyncio
    async def test_get_retry_after(self) -> None:
        """Test retry after calculation."""
        counter = SlidingWindowCounter(window_size=60.0, max_requests=1)

        await counter.try_acquire()
        retry_after = await counter.get_retry_after()

        # Should be close to 60 seconds
        assert 59 < retry_after <= 60

    @pytest.mark.asyncio
    async def test_get_retry_after_zero_when_available(self) -> None:
        """Test retry after is zero when slots available."""
        counter = SlidingWindowCounter(window_size=60.0, max_requests=10)

        retry_after = await counter.get_retry_after()
        assert retry_after == 0.0


class TestRateLimitConfig:
    """Tests for RateLimitConfig dataclass."""

    def test_default_config(self) -> None:
        """Test default configuration values."""
        config = RateLimitConfig()
        assert config.requests_per_minute == 60
        assert config.requests_per_hour == 1000
        assert config.burst_size == 10

    def test_role_configs_exist(self) -> None:
        """Test that role-based configs are defined."""
        assert "viewer" in ROLE_RATE_LIMITS
        assert "operator" in ROLE_RATE_LIMITS
        assert "administrator" in ROLE_RATE_LIMITS

    def test_role_hierarchy(self) -> None:
        """Test that higher roles have higher limits."""
        viewer = ROLE_RATE_LIMITS["viewer"]
        operator = ROLE_RATE_LIMITS["operator"]
        admin = ROLE_RATE_LIMITS["administrator"]

        assert viewer.requests_per_minute < operator.requests_per_minute
        assert operator.requests_per_minute < admin.requests_per_minute


class TestRateLimiter:
    """Tests for RateLimiter class."""

    @pytest.mark.asyncio
    async def test_disabled_limiter_allows_all(self) -> None:
        """Test that disabled limiter allows all requests."""
        limiter = RateLimiter(enabled=False)

        # Should not raise even with many requests
        for _ in range(100):
            await limiter.check_rate_limit(user_id="test")

    @pytest.mark.asyncio
    async def test_enabled_limiter_enforces_limits(self) -> None:
        """Test that enabled limiter enforces limits."""
        limiter = RateLimiter(enabled=True)

        # Exhaust the bucket
        bucket = limiter._get_or_create_bucket(
            "user:test",
            ROLE_RATE_LIMITS["viewer"],
        )
        bucket.tokens = 0  # Manually exhaust

        with pytest.raises(RateLimitExceeded) as exc_info:
            await limiter.check_rate_limit(user_id="test", role_name="viewer")

        assert exc_info.value.retry_after > 0

    @pytest.mark.asyncio
    async def test_client_key_generation(self) -> None:
        """Test client key generation."""
        limiter = RateLimiter()

        assert limiter._get_client_key(user_id="user1") == "user:user1"
        assert limiter._get_client_key(ip_address="1.2.3.4") == "ip:1.2.3.4"
        assert limiter._get_client_key() == "anonymous"

    @pytest.mark.asyncio
    async def test_role_based_limits(self) -> None:
        """Test different limits for different roles."""
        limiter = RateLimiter()

        viewer_config = limiter._get_rate_config("viewer")
        admin_config = limiter._get_rate_config("administrator")

        assert viewer_config.requests_per_minute == 30
        assert admin_config.requests_per_minute == 120

    @pytest.mark.asyncio
    async def test_unknown_role_defaults_to_viewer(self) -> None:
        """Test unknown role gets viewer limits."""
        limiter = RateLimiter()

        unknown_config = limiter._get_rate_config("unknown_role")
        viewer_config = limiter._get_rate_config("viewer")

        assert unknown_config.requests_per_minute == viewer_config.requests_per_minute

    @pytest.mark.asyncio
    async def test_get_rate_limit_info(self) -> None:
        """Test getting rate limit info for a client."""
        limiter = RateLimiter()

        info = await limiter.get_rate_limit_info(user_id="test", role_name="viewer")

        assert "limit" in info
        assert "remaining" in info
        assert "reset" in info
        assert "burst_size" in info
        assert info["limit"] == 30  # Viewer limit

    @pytest.mark.asyncio
    async def test_reset_client(self) -> None:
        """Test resetting a client's rate limit."""
        limiter = RateLimiter()

        # Make some requests to create state
        await limiter.check_rate_limit(user_id="test", role_name="viewer")
        assert "user:test" in limiter._client_limiters

        # Reset
        limiter.reset_client(user_id="test")
        assert "user:test" not in limiter._client_limiters


class TestRateLimitExceeded:
    """Tests for RateLimitExceeded exception."""

    def test_exception_attributes(self) -> None:
        """Test exception has required attributes."""
        exc = RateLimitExceeded(
            message="Rate limited",
            retry_after=30.0,
            limit=100,
            current=150,
        )

        assert str(exc) == "Rate limited"
        assert exc.retry_after == 30.0
        assert exc.limit == 100
        assert exc.current == 150


class TestRateLimiterConcurrency:
    """Tests for concurrent access to rate limiter."""

    @pytest.mark.asyncio
    async def test_concurrent_requests(self) -> None:
        """Test concurrent requests are handled safely."""
        limiter = RateLimiter(enabled=True)

        async def make_request(user: str) -> bool:
            try:
                await limiter.check_rate_limit(user_id=user, role_name="administrator")
                return True
            except RateLimitExceeded:
                return False

        # Make concurrent requests from same user
        results = await asyncio.gather(*[make_request("user1") for _ in range(10)])

        # Some should succeed, some may fail
        assert any(results)  # At least one should succeed

    @pytest.mark.asyncio
    async def test_concurrent_different_users(self) -> None:
        """Test concurrent requests from different users."""
        limiter = RateLimiter(enabled=True)

        async def make_request(user: str) -> bool:
            try:
                await limiter.check_rate_limit(user_id=user, role_name="administrator")
                return True
            except RateLimitExceeded:
                return False

        # Make requests from different users
        results = await asyncio.gather(*[make_request(f"user{i}") for i in range(10)])

        # All should succeed (different users, different buckets)
        assert all(results)


class TestGlobalRateLimit:
    """Tests for global rate limiting."""

    @pytest.mark.asyncio
    async def test_global_limit_enforcement(self) -> None:
        """Test that global limit is enforced."""
        limiter = RateLimiter(enabled=True)

        # Exhaust global counter
        for _ in range(GLOBAL_RATE_LIMIT.requests_per_minute):
            await limiter._global_counter.try_acquire()

        # Next request should fail on global limit
        with pytest.raises(RateLimitExceeded) as exc_info:
            await limiter.check_rate_limit(user_id="any_user", role_name="administrator")

        assert "Global rate limit" in str(exc_info.value)
