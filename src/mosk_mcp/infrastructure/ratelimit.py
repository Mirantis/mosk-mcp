"""Rate limiting for MOSK MCP Server.

This module provides rate limiting functionality to protect the server
from abuse and ensure fair resource allocation among clients.

Features:
- Token bucket algorithm for smooth rate limiting
- Per-client and global rate limits
- Configurable limits by user role
- Sliding window for request counting
- Async-safe implementation
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

from mosk_mcp.observability.logging import get_logger


if TYPE_CHECKING:
    from mosk_mcp.core.config import Settings

logger = get_logger(__name__)


class RateLimitExceeded(Exception):
    """Raised when rate limit is exceeded.

    Attributes:
        retry_after: Seconds until the client can retry.
        limit: The rate limit that was exceeded.
        current: Current request count in the window.
    """

    def __init__(
        self,
        message: str,
        retry_after: float,
        limit: int,
        current: int,
    ) -> None:
        """Initialize rate limit exceeded exception.

        Args:
            message: Error message.
            retry_after: Seconds until retry is allowed.
            limit: The limit that was exceeded.
            current: Current count in window.
        """
        super().__init__(message)
        self.retry_after = retry_after
        self.limit = limit
        self.current = current


class RateLimitStrategy(str, Enum):
    """Rate limiting strategies.

    Attributes:
        FIXED_WINDOW: Count requests in fixed time windows.
        SLIDING_WINDOW: Count requests in a sliding time window.
        TOKEN_BUCKET: Token bucket algorithm for smooth limiting.
    """

    FIXED_WINDOW = "fixed_window"
    SLIDING_WINDOW = "sliding_window"
    TOKEN_BUCKET = "token_bucket"


@dataclass
class RateLimitConfig:
    """Configuration for rate limiting.

    Attributes:
        requests_per_minute: Maximum requests per minute.
        requests_per_hour: Maximum requests per hour.
        burst_size: Maximum burst size for token bucket.
        strategy: Rate limiting strategy to use.
    """

    requests_per_minute: int = 60
    requests_per_hour: int = 1000
    burst_size: int = 10
    strategy: RateLimitStrategy = RateLimitStrategy.TOKEN_BUCKET


# Default rate limits by role
ROLE_RATE_LIMITS: dict[str, RateLimitConfig] = {
    "viewer": RateLimitConfig(
        requests_per_minute=30,
        requests_per_hour=500,
        burst_size=5,
    ),
    "operator": RateLimitConfig(
        requests_per_minute=60,
        requests_per_hour=1000,
        burst_size=10,
    ),
    "administrator": RateLimitConfig(
        requests_per_minute=120,
        requests_per_hour=2000,
        burst_size=20,
    ),
}

# Global rate limit (across all clients)
GLOBAL_RATE_LIMIT = RateLimitConfig(
    requests_per_minute=500,
    requests_per_hour=10000,
    burst_size=50,
)


@dataclass
class TokenBucket:
    """Token bucket for rate limiting.

    The token bucket algorithm allows for bursty traffic while
    enforcing an average rate limit. Tokens are added at a fixed
    rate and consumed for each request.

    Attributes:
        capacity: Maximum tokens in the bucket.
        tokens: Current tokens available.
        refill_rate: Tokens added per second.
        last_update: Timestamp of last token update.
        consumed_count: Total tokens consumed (for reporting).
    """

    capacity: int
    tokens: float = field(init=False)
    refill_rate: float  # tokens per second
    last_update: float = field(default_factory=time.monotonic)
    consumed_count: int = field(init=False, default=0)

    def __post_init__(self) -> None:
        """Initialize tokens to capacity."""
        self.tokens = float(self.capacity)
        self.consumed_count = 0

    def _refill(self) -> None:
        """Refill tokens based on elapsed time."""
        now = time.monotonic()
        elapsed = now - self.last_update
        self.tokens = min(
            self.capacity,
            self.tokens + elapsed * self.refill_rate,
        )
        self.last_update = now

    def consume(self, tokens: int = 1) -> bool:
        """Try to consume tokens from the bucket.

        Args:
            tokens: Number of tokens to consume.

        Returns:
            True if tokens were consumed, False if not enough available.
        """
        self._refill()
        if self.tokens >= tokens:
            self.tokens -= tokens
            self.consumed_count += tokens
            return True
        return False

    def time_until_available(self, tokens: int = 1) -> float:
        """Calculate time until tokens are available.

        Args:
            tokens: Number of tokens needed.

        Returns:
            Seconds until tokens will be available.
        """
        self._refill()
        if self.tokens >= tokens:
            return 0.0
        needed = tokens - self.tokens
        return needed / self.refill_rate


@dataclass
class SlidingWindowCounter:
    """Sliding window counter for rate limiting.

    Uses a sliding window to count requests, providing smoother
    rate limiting than fixed windows.

    Attributes:
        window_size: Size of the window in seconds.
        max_requests: Maximum requests allowed in the window.
        requests: List of request timestamps.
    """

    window_size: float
    max_requests: int
    requests: list[float] = field(default_factory=list)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def _cleanup(self) -> None:
        """Remove expired requests from the window."""
        now = time.monotonic()
        cutoff = now - self.window_size
        self.requests = [t for t in self.requests if t > cutoff]

    async def try_acquire(self) -> bool:
        """Try to acquire a slot in the rate limit window.

        Returns:
            True if request is allowed, False if limit exceeded.
        """
        async with self._lock:
            self._cleanup()
            if len(self.requests) < self.max_requests:
                self.requests.append(time.monotonic())
                return True
            return False

    async def get_retry_after(self) -> float:
        """Get time until a slot is available.

        Returns:
            Seconds until a request will be allowed.
        """
        async with self._lock:
            self._cleanup()
            if len(self.requests) < self.max_requests:
                return 0.0
            # Time until oldest request expires
            oldest = min(self.requests)
            return max(0.0, oldest + self.window_size - time.monotonic())

    @property
    def current_count(self) -> int:
        """Get current request count in the window."""
        self._cleanup()
        return len(self.requests)


class RateLimiter:
    """Rate limiter with per-client and global limits.

    This class manages rate limiting for the MCP server, supporting:
    - Per-client limits based on user ID or IP
    - Role-based limits (viewer, operator, administrator)
    - Global limits to protect server resources
    - Multiple rate limiting strategies

    Attributes:
        _client_limiters: Per-client rate limiters.
        _global_limiter: Global rate limiter.
        _config: Rate limit configuration.
        _enabled: Whether rate limiting is enabled.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        enabled: bool = True,
    ) -> None:
        """Initialize the rate limiter.

        Args:
            settings: Application settings.
            enabled: Whether rate limiting is enabled.
        """
        self._enabled = enabled
        self._client_limiters: dict[str, TokenBucket] = {}
        self._client_counters: dict[str, SlidingWindowCounter] = {}
        self._global_counter = SlidingWindowCounter(
            window_size=60.0,  # 1 minute
            max_requests=GLOBAL_RATE_LIMIT.requests_per_minute,
        )
        self._cleanup_lock = asyncio.Lock()
        self._last_cleanup = time.monotonic()
        self._cleanup_interval = 300.0  # 5 minutes

        if settings:
            self._enabled = getattr(settings, "rate_limit_enabled", True)

    @property
    def enabled(self) -> bool:
        """Check if rate limiting is enabled."""
        return self._enabled

    def _get_client_key(
        self,
        user_id: str | None = None,
        ip_address: str | None = None,
    ) -> str:
        """Generate a unique key for the client.

        Args:
            user_id: User ID if authenticated.
            ip_address: Client IP address.

        Returns:
            Unique client identifier.
        """
        if user_id:
            return f"user:{user_id}"
        if ip_address:
            return f"ip:{ip_address}"
        return "anonymous"

    def _get_rate_config(self, role_name: str | None = None) -> RateLimitConfig:
        """Get rate limit config for the client.

        Args:
            role_name: Role name for the user (e.g., "viewer", "operator", "administrator").

        Returns:
            Rate limit configuration for the client.
        """
        if role_name:
            return ROLE_RATE_LIMITS.get(role_name.lower(), ROLE_RATE_LIMITS["viewer"])
        return ROLE_RATE_LIMITS["viewer"]

    def _get_or_create_bucket(
        self,
        client_key: str,
        config: RateLimitConfig,
    ) -> TokenBucket:
        """Get or create a token bucket for the client.

        Args:
            client_key: Client identifier.
            config: Rate limit configuration.

        Returns:
            Token bucket for the client.
        """
        if client_key not in self._client_limiters:
            # Create token bucket with refill rate based on per-minute limit
            self._client_limiters[client_key] = TokenBucket(
                capacity=config.burst_size,
                refill_rate=config.requests_per_minute / 60.0,
            )
        return self._client_limiters[client_key]

    def _get_or_create_counter(
        self,
        client_key: str,
        config: RateLimitConfig,
    ) -> SlidingWindowCounter:
        """Get or create a sliding window counter for the client.

        Args:
            client_key: Client identifier.
            config: Rate limit configuration.

        Returns:
            Sliding window counter for the client.
        """
        if client_key not in self._client_counters:
            self._client_counters[client_key] = SlidingWindowCounter(
                window_size=60.0,  # 1 minute window
                max_requests=config.requests_per_minute,
            )
        return self._client_counters[client_key]

    async def _cleanup_old_clients(self) -> None:
        """Remove stale client limiters to prevent memory leaks.

        Note:
            Uses cleanup_lock AND takes a snapshot of keys to prevent
            race conditions with concurrent check_rate_limit calls.
        """
        async with self._cleanup_lock:
            now = time.monotonic()
            if now - self._last_cleanup < self._cleanup_interval:
                return

            # Take a snapshot of keys to avoid "dictionary changed size during iteration"
            # This is safe because we only read values, not modify during iteration
            limiter_keys = list(self._client_limiters.keys())
            stale_keys = []

            for key in limiter_keys:
                bucket = self._client_limiters.get(key)
                if bucket is not None and now - bucket.last_update > 3600:  # 1 hour
                    stale_keys.append(key)

            # Remove stale entries - use pop() for thread-safety
            for key in stale_keys:
                self._client_limiters.pop(key, None)
                self._client_counters.pop(key, None)

            if stale_keys:
                logger.debug(
                    "rate_limit_cleanup",
                    removed_clients=len(stale_keys),
                )

            self._last_cleanup = now

    async def check_rate_limit(
        self,
        user_id: str | None = None,
        role_name: str | None = None,
        ip_address: str | None = None,
        cost: int = 1,
    ) -> None:
        """Check if request is within rate limits.

        Args:
            user_id: User ID if authenticated.
            role_name: Role name for rate limit config (viewer, operator, administrator).
            ip_address: Client IP address.
            cost: Cost of this request (default 1).

        Raises:
            RateLimitExceeded: If rate limit is exceeded.
        """
        if not self._enabled:
            return

        # Periodic cleanup
        await self._cleanup_old_clients()

        client_key = self._get_client_key(user_id, ip_address)
        config = self._get_rate_config(role_name)

        # Check global limit first
        if not await self._global_counter.try_acquire():
            retry_after = await self._global_counter.get_retry_after()
            logger.warning(
                "global_rate_limit_exceeded",
                client=client_key,
                retry_after=retry_after,
            )
            raise RateLimitExceeded(
                message="Global rate limit exceeded. Please try again later.",
                retry_after=retry_after,
                limit=GLOBAL_RATE_LIMIT.requests_per_minute,
                current=self._global_counter.current_count,
            )

        # Check client limit using token bucket
        bucket = self._get_or_create_bucket(client_key, config)
        if not bucket.consume(cost):
            retry_after = bucket.time_until_available(cost)
            logger.warning(
                "client_rate_limit_exceeded",
                client=client_key,
                retry_after=retry_after,
                limit=config.requests_per_minute,
            )
            raise RateLimitExceeded(
                message="Rate limit exceeded. Please slow down your requests.",
                retry_after=retry_after,
                limit=config.requests_per_minute,
                current=bucket.consumed_count,
            )

        logger.debug(
            "rate_limit_check_passed",
            client=client_key,
            tokens_remaining=bucket.tokens,
        )

    async def get_rate_limit_info(
        self,
        user_id: str | None = None,
        role_name: str | None = None,
        ip_address: str | None = None,
    ) -> dict[str, Any]:
        """Get current rate limit status for a client.

        Args:
            user_id: User ID if authenticated.
            role_name: Role name for rate limit config.
            ip_address: Client IP address.

        Returns:
            Dictionary with rate limit information.
        """
        client_key = self._get_client_key(user_id, ip_address)
        config = self._get_rate_config(role_name)
        bucket = self._get_or_create_bucket(client_key, config)
        bucket._refill()

        return {
            "limit": config.requests_per_minute,
            "remaining": int(bucket.tokens),
            "reset": bucket.time_until_available(config.burst_size),
            "burst_size": config.burst_size,
        }

    def reset_client(
        self,
        user_id: str | None = None,
        ip_address: str | None = None,
    ) -> None:
        """Reset rate limit for a specific client.

        Args:
            user_id: User ID if authenticated.
            ip_address: Client IP address.
        """
        client_key = self._get_client_key(user_id, ip_address)
        self._client_limiters.pop(client_key, None)
        self._client_counters.pop(client_key, None)
        logger.info("rate_limit_reset", client=client_key)


# Global rate limiter instance
_rate_limiter: RateLimiter | None = None


def get_rate_limiter() -> RateLimiter:
    """Get the global rate limiter instance.

    Returns:
        The global RateLimiter instance.
    """
    global _rate_limiter
    if _rate_limiter is None:
        _rate_limiter = RateLimiter()
    return _rate_limiter


def set_rate_limiter(limiter: RateLimiter) -> None:
    """Set the global rate limiter instance.

    Args:
        limiter: RateLimiter to use globally.
    """
    global _rate_limiter
    _rate_limiter = limiter


async def check_rate_limit(
    user_id: str | None = None,
    role_name: str | None = None,
    ip_address: str | None = None,
    cost: int = 1,
) -> None:
    """Check rate limit using the global limiter.

    Convenience function to check rate limits without getting
    the limiter instance directly.

    Args:
        user_id: User ID if authenticated.
        role_name: Role name for rate limit config.
        ip_address: Client IP address.
        cost: Cost of this request.

    Raises:
        RateLimitExceeded: If rate limit is exceeded.
    """
    limiter = get_rate_limiter()
    await limiter.check_rate_limit(user_id, role_name, ip_address, cost)
