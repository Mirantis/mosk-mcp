"""Infrastructure module for MOSK MCP Server.

This package contains infrastructure components:
- cache.py: Response caching with TTL and eviction
- circuit_breaker.py: Circuit breaker pattern for fault tolerance
- ratelimit.py: Rate limiting with token bucket
- shutdown.py: Graceful shutdown handling
- version_checker.py: MOSK version compatibility
"""

from __future__ import annotations

from mosk_mcp.infrastructure.cache import (
    CacheEntry,
    ResponseCache,
)
from mosk_mcp.infrastructure.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitBreakerState,
)
from mosk_mcp.infrastructure.ratelimit import (
    RateLimitConfig,
    RateLimiter,
    RateLimitExceeded,
    check_rate_limit,
    get_rate_limiter,
    set_rate_limiter,
)
from mosk_mcp.infrastructure.shutdown import (
    GracefulShutdownManager,
    ShutdownEvent,
    ShutdownHook,
    ShutdownState,
    get_shutdown_manager,
    register_shutdown_hook,
    set_shutdown_manager,
)
from mosk_mcp.infrastructure.version_checker import (
    MOSKVersionInfo,
    VersionCompatibility,
    get_cached_version_info,
    set_cached_version_info,
)


__all__ = [
    "CacheEntry",
    "CircuitBreaker",
    "CircuitBreakerConfig",
    "CircuitBreakerState",
    "GracefulShutdownManager",
    "MOSKVersionInfo",
    "RateLimitConfig",
    "RateLimitExceeded",
    "RateLimiter",
    "ResponseCache",
    "ShutdownEvent",
    "ShutdownHook",
    "ShutdownState",
    "VersionCompatibility",
    "check_rate_limit",
    "get_cached_version_info",
    "get_rate_limiter",
    "get_shutdown_manager",
    "register_shutdown_hook",
    "set_cached_version_info",
    "set_rate_limiter",
    "set_shutdown_manager",
]
