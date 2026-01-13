"""Tests for the rate limiting module.

This module tests the RateLimiter class and related functionality.
"""

import asyncio
from datetime import UTC, datetime

import pytest

from mosk_mcp.auth.types import Permission, Role, UserContext
from mosk_mcp.infrastructure.ratelimit import (
    RateLimitConfig,
    RateLimiter,
    RateLimitExceeded,
    SlidingWindowCounter,
    TokenBucket,
    check_rate_limit,
    get_rate_limiter,
    set_rate_limiter,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def rate_limiter():
    """Create a rate limiter for testing."""
    return RateLimiter(enabled=True)


@pytest.fixture
def disabled_limiter():
    """Create a disabled rate limiter."""
    return RateLimiter(enabled=False)


@pytest.fixture
def user_context():
    """Create a test user context."""
    return UserContext(
        user_id="test-user-123",
        username="testuser",
        role=Role.OPERATOR,
        permissions=frozenset([Permission.READ_MACHINES, Permission.WRITE_MACHINES]),
        authenticated_at=datetime.now(UTC),
        auth_method="test",
    )


@pytest.fixture
def admin_context():
    """Create an admin user context."""
    return UserContext(
        user_id="admin-user-456",
        username="admin",
        role=Role.ADMINISTRATOR,
        permissions=frozenset(Permission),
        authenticated_at=datetime.now(UTC),
        auth_method="test",
    )


@pytest.fixture
def viewer_context():
    """Create a viewer user context."""
    return UserContext(
        user_id="viewer-user-789",
        username="viewer",
        role=Role.VIEWER,
        permissions=frozenset([Permission.READ_MACHINES]),
        authenticated_at=datetime.now(UTC),
        auth_method="test",
    )


@pytest.fixture(autouse=True)
def reset_global_limiter():
    """Reset global rate limiter between tests."""
    set_rate_limiter(RateLimiter(enabled=True))
    yield


# =============================================================================
# TokenBucket Tests
# =============================================================================


class TestTokenBucket:
    """Tests for TokenBucket class."""

    def test_initialization(self):
        """Test token bucket initialization."""
        bucket = TokenBucket(capacity=10, refill_rate=1.0)

        assert bucket.capacity == 10
        assert bucket.tokens == 10.0
        assert bucket.refill_rate == 1.0

    def test_consume_success(self):
        """Test successful token consumption."""
        bucket = TokenBucket(capacity=10, refill_rate=1.0)

        assert bucket.consume(5) is True
        assert bucket.tokens == 5.0

    def test_consume_failure(self):
        """Test failed token consumption when not enough tokens."""
        bucket = TokenBucket(capacity=5, refill_rate=1.0)

        assert bucket.consume(3) is True  # 2 left
        assert bucket.consume(3) is False  # Not enough

    def test_refill(self):
        """Test token refill over time."""
        bucket = TokenBucket(capacity=10, refill_rate=10.0)  # 10 tokens/sec

        # Consume all tokens
        bucket.consume(10)
        assert bucket.tokens == 0.0

        # Simulate time passing (manually update last_update)
        import time

        bucket.last_update = time.monotonic() - 0.5  # 0.5 seconds ago
        bucket._refill()

        # Should have refilled 5 tokens (0.5s * 10 tokens/s)
        assert bucket.tokens >= 4.5  # Allow some timing variance

    def test_refill_capped_at_capacity(self):
        """Test that refill doesn't exceed capacity."""
        bucket = TokenBucket(capacity=10, refill_rate=100.0)  # Very fast refill

        bucket.consume(5)
        import time

        bucket.last_update = time.monotonic() - 10  # Long time ago
        bucket._refill()

        assert bucket.tokens == 10.0  # Capped at capacity

    def test_time_until_available(self):
        """Test calculating time until tokens available."""
        bucket = TokenBucket(capacity=10, refill_rate=2.0)  # 2 tokens/sec

        bucket.consume(10)  # Consume all

        # Need 5 tokens, at 2/sec = 2.5 seconds
        wait_time = bucket.time_until_available(5)
        assert 2.0 <= wait_time <= 3.0  # Allow some variance

    def test_time_until_available_when_available(self):
        """Test time_until_available returns 0 when tokens available."""
        bucket = TokenBucket(capacity=10, refill_rate=1.0)

        assert bucket.time_until_available(5) == 0.0


# =============================================================================
# SlidingWindowCounter Tests
# =============================================================================


class TestSlidingWindowCounter:
    """Tests for SlidingWindowCounter class."""

    @pytest.mark.asyncio
    async def test_try_acquire_success(self):
        """Test successful request acquisition."""
        counter = SlidingWindowCounter(window_size=60.0, max_requests=10)

        result = await counter.try_acquire()
        assert result is True
        assert counter.current_count == 1

    @pytest.mark.asyncio
    async def test_try_acquire_limit_exceeded(self):
        """Test acquisition fails when limit exceeded."""
        counter = SlidingWindowCounter(window_size=60.0, max_requests=3)

        # Acquire up to limit
        for _ in range(3):
            await counter.try_acquire()

        # Next should fail
        result = await counter.try_acquire()
        assert result is False

    @pytest.mark.asyncio
    async def test_get_retry_after_when_available(self):
        """Test retry_after is 0 when requests available."""
        counter = SlidingWindowCounter(window_size=60.0, max_requests=10)

        retry_after = await counter.get_retry_after()
        assert retry_after == 0.0

    @pytest.mark.asyncio
    async def test_get_retry_after_when_exceeded(self):
        """Test retry_after calculation when limit exceeded."""
        counter = SlidingWindowCounter(window_size=1.0, max_requests=2)  # 1 second window

        await counter.try_acquire()
        await counter.try_acquire()

        retry_after = await counter.get_retry_after()
        assert 0.0 <= retry_after <= 1.0


# =============================================================================
# RateLimiter Tests
# =============================================================================


class TestRateLimiter:
    """Tests for RateLimiter class."""

    def test_initialization(self, rate_limiter):
        """Test rate limiter initialization."""
        assert rate_limiter.enabled is True

    def test_disabled_limiter(self, disabled_limiter):
        """Test disabled rate limiter."""
        assert disabled_limiter.enabled is False

    @pytest.mark.asyncio
    async def test_check_rate_limit_disabled(self, disabled_limiter, user_context):
        """Test that disabled limiter allows all requests."""
        # Should not raise
        for _ in range(100):
            await disabled_limiter.check_rate_limit(
                user_id=user_context.user_id,
                role_name=user_context.role.value,
            )

    @pytest.mark.asyncio
    async def test_check_rate_limit_success(self, rate_limiter, user_context):
        """Test successful rate limit check."""
        # Should not raise
        await rate_limiter.check_rate_limit(
            user_id=user_context.user_id,
            role_name=user_context.role.value,
        )

    @pytest.mark.asyncio
    async def test_check_rate_limit_exceeded(self, user_context):
        """Test rate limit exceeded."""
        # Create limiter with very low limits for testing
        limiter = RateLimiter(enabled=True)

        # Override global limit
        limiter._global_counter = SlidingWindowCounter(
            window_size=60.0,
            max_requests=1000,  # High global limit
        )

        # Override client bucket with tiny limit
        client_key = limiter._get_client_key(user_id=user_context.user_id)
        limiter._client_limiters[client_key] = TokenBucket(
            capacity=2,
            refill_rate=0.01,  # Very slow refill
        )

        # First two should succeed
        await limiter.check_rate_limit(
            user_id=user_context.user_id,
            role_name=user_context.role.value,
        )
        await limiter.check_rate_limit(
            user_id=user_context.user_id,
            role_name=user_context.role.value,
        )

        # Third should fail
        with pytest.raises(RateLimitExceeded) as exc_info:
            await limiter.check_rate_limit(
                user_id=user_context.user_id,
                role_name=user_context.role.value,
            )

        assert exc_info.value.retry_after > 0

    @pytest.mark.asyncio
    async def test_different_clients_independent(self, rate_limiter, user_context, admin_context):
        """Test that different clients have independent limits."""
        # Both should succeed
        await rate_limiter.check_rate_limit(
            user_id=user_context.user_id,
            role_name=user_context.role.value,
        )
        await rate_limiter.check_rate_limit(
            user_id=admin_context.user_id,
            role_name=admin_context.role.value,
        )

    @pytest.mark.asyncio
    async def test_admin_has_higher_limits(self, admin_context):
        """Test that admin has higher rate limits."""
        limiter = RateLimiter(enabled=True)
        config = limiter._get_rate_config(role_name=admin_context.role.value)

        # Admin should have higher limits than default
        assert config.requests_per_minute >= 120

    @pytest.mark.asyncio
    async def test_viewer_has_lower_limits(self, viewer_context):
        """Test that viewer has lower rate limits."""
        limiter = RateLimiter(enabled=True)
        config = limiter._get_rate_config(role_name=viewer_context.role.value)

        # Viewer should have lower limits
        assert config.requests_per_minute <= 60

    @pytest.mark.asyncio
    async def test_anonymous_gets_viewer_limits(self):
        """Test that anonymous users get viewer-level limits."""
        limiter = RateLimiter(enabled=True)
        config = limiter._get_rate_config(None)  # No user context

        # Should get viewer limits
        assert config.requests_per_minute == 30

    @pytest.mark.asyncio
    async def test_get_rate_limit_info(self, rate_limiter, user_context):
        """Test getting rate limit information."""
        info = await rate_limiter.get_rate_limit_info(
            user_id=user_context.user_id,
            role_name=user_context.role.value,
        )

        assert "limit" in info
        assert "remaining" in info
        assert "reset" in info
        assert "burst_size" in info

    def test_reset_client(self, rate_limiter, user_context):
        """Test resetting client rate limit."""
        # Create a bucket for the client
        client_key = rate_limiter._get_client_key(user_id=user_context.user_id)
        rate_limiter._get_or_create_bucket(
            client_key,
            RateLimitConfig(),
        )

        assert client_key in rate_limiter._client_limiters

        # Reset
        rate_limiter.reset_client(user_id=user_context.user_id)

        assert client_key not in rate_limiter._client_limiters

    @pytest.mark.asyncio
    async def test_ip_based_limiting(self, rate_limiter):
        """Test rate limiting by IP address."""
        ip1 = "192.168.1.1"
        ip2 = "192.168.1.2"

        # Different IPs should have independent limits
        await rate_limiter.check_rate_limit(ip_address=ip1)
        await rate_limiter.check_rate_limit(ip_address=ip2)


# =============================================================================
# Global Functions Tests
# =============================================================================


class TestGlobalFunctions:
    """Tests for module-level convenience functions."""

    def test_get_rate_limiter(self):
        """Test getting global rate limiter."""
        limiter = get_rate_limiter()
        assert isinstance(limiter, RateLimiter)

    def test_set_rate_limiter(self):
        """Test setting global rate limiter."""
        custom_limiter = RateLimiter(enabled=False)
        set_rate_limiter(custom_limiter)

        assert get_rate_limiter() is custom_limiter
        assert get_rate_limiter().enabled is False

    @pytest.mark.asyncio
    async def test_check_rate_limit_function(self, user_context):
        """Test module-level check_rate_limit function."""
        # Should not raise with fresh limiter
        await check_rate_limit(user_context)


# =============================================================================
# RateLimitExceeded Tests
# =============================================================================


class TestRateLimitExceeded:
    """Tests for RateLimitExceeded exception."""

    def test_exception_attributes(self):
        """Test exception has correct attributes."""
        exc = RateLimitExceeded(
            message="Rate limit exceeded",
            retry_after=5.0,
            limit=60,
            current=65,
        )

        assert str(exc) == "Rate limit exceeded"
        assert exc.retry_after == 5.0
        assert exc.limit == 60
        assert exc.current == 65


# =============================================================================
# Integration Tests
# =============================================================================


class TestRateLimiterIntegration:
    """Integration tests for rate limiting."""

    @pytest.mark.asyncio
    async def test_concurrent_requests(self, user_context):
        """Test rate limiting under concurrent load."""
        limiter = RateLimiter(enabled=True)

        # Override with higher global limit for testing
        limiter._global_counter = SlidingWindowCounter(
            window_size=60.0,
            max_requests=1000,
        )

        # Run multiple concurrent requests
        async def make_request():
            try:
                await limiter.check_rate_limit(user_context)
                return True
            except RateLimitExceeded:
                return False

        # Run 5 concurrent requests
        results = await asyncio.gather(*[make_request() for _ in range(5)])

        # At least some should succeed
        assert any(results)

    @pytest.mark.asyncio
    async def test_rate_limit_recovery(self):
        """Test that rate limits recover over time."""
        limiter = RateLimiter(enabled=True)

        # Create a bucket with fast refill for testing
        client_key = "test-recovery"
        limiter._client_limiters[client_key] = TokenBucket(
            capacity=2,
            refill_rate=100.0,  # 100 tokens/second for quick recovery
        )

        bucket = limiter._client_limiters[client_key]

        # Consume all tokens
        bucket.consume(2)
        assert bucket.tokens == 0.0

        # Wait a tiny bit
        await asyncio.sleep(0.05)  # 50ms

        # Should have recovered some tokens
        bucket._refill()
        assert bucket.tokens > 0
