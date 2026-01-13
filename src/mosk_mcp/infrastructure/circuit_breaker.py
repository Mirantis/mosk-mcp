"""Circuit Breaker pattern implementation.

This module provides a thread-safe circuit breaker for preventing cascading failures
in distributed systems. The circuit breaker tracks failures and opens the circuit
when threshold is exceeded, preventing further requests until recovery.

States:
- CLOSED: Normal operation, requests pass through
- OPEN: Too many failures, reject requests immediately
- HALF_OPEN: Testing recovery, allow limited requests
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any

from mosk_mcp.core.exceptions import MoskConnectionError
from mosk_mcp.observability.logging import get_logger


logger = get_logger(__name__)

# Enable debug mode for lock enforcement assertions (set by tests or development)
_DEBUG_LOCK_ENFORCEMENT = os.environ.get("MOSK_DEBUG_LOCK_ENFORCEMENT", "").lower() in (
    "1",
    "true",
    "yes",
)


class CircuitBreakerState(str, Enum):
    """Circuit breaker states."""

    CLOSED = "closed"  # Normal operation
    OPEN = "open"  # Failing, reject requests
    HALF_OPEN = "half_open"  # Testing recovery


@dataclass
class CircuitBreakerConfig:
    """Circuit breaker configuration.

    Attributes:
        failure_threshold: Failures before opening circuit.
        recovery_timeout: Seconds before testing recovery.
        half_open_max_calls: Max test calls in half-open state.
        success_threshold: Successes needed to close circuit.
    """

    failure_threshold: int = 5
    recovery_timeout: float = 30.0
    half_open_max_calls: int = 3
    success_threshold: int = 2


class CircuitBreaker:
    """Circuit breaker for preventing cascading failures.

    Thread-safe implementation that tracks failures and opens the circuit
    when threshold is exceeded, preventing further requests until recovery.

    States:
    - CLOSED: Normal operation, requests pass through
    - OPEN: Too many failures, reject requests immediately
    - HALF_OPEN: Testing recovery, allow limited requests
    """

    def __init__(self, name: str, config: CircuitBreakerConfig | None = None) -> None:
        """Initialize circuit breaker.

        Args:
            name: Identifier for this circuit breaker.
            config: Configuration settings.
        """
        self.name = name
        self.config = config or CircuitBreakerConfig()
        self._state = CircuitBreakerState.CLOSED
        self._failures = 0
        self._successes = 0
        self._last_failure_time = 0.0
        self._half_open_calls = 0
        self._lock = asyncio.Lock()
        self._total_failures = 0
        self._total_successes = 0
        self._state_changes = 0

    @property
    def state(self) -> CircuitBreakerState:
        """Current circuit breaker state."""
        return self._state

    @property
    def is_closed(self) -> bool:
        """Check if circuit is closed (normal operation)."""
        return self._state == CircuitBreakerState.CLOSED

    @property
    def is_open(self) -> bool:
        """Check if circuit is open (rejecting requests)."""
        return self._state == CircuitBreakerState.OPEN

    @property
    def metrics(self) -> dict[str, Any]:
        """Get circuit breaker metrics."""
        return {
            "name": self.name,
            "state": self._state.value,
            "failures": self._failures,
            "successes": self._successes,
            "total_failures": self._total_failures,
            "total_successes": self._total_successes,
            "state_changes": self._state_changes,
        }

    def _check_state_transition_unlocked(self) -> None:
        """Check if circuit should transition states.

        IMPORTANT: This method MUST only be called with self._lock held.
        The '_unlocked' suffix indicates this method does not acquire the lock
        itself - the caller is responsible for holding the lock.

        This is a private method that should only be called from within
        `async with self._lock:` blocks in this class.
        """
        # Note: We can't verify lock ownership in asyncio (Lock.locked() returns
        # True for ANY holder, not just the caller). Proper usage is enforced by:
        # 1. Naming convention (_unlocked suffix)
        # 2. Code review
        # 3. Debug assertion (helps catch issues during development/testing)
        import sys

        if __debug__ and sys.flags.dev_mode and not self._lock.locked():
            # In development mode, verify lock is held by someone
            # (not a guarantee it's us, but catches obvious bugs)
            raise AssertionError(
                "_check_state_transition_unlocked called without lock held. "
                "This is a bug - caller must hold self._lock."
            )

        now = time.monotonic()

        if (
            self._state == CircuitBreakerState.OPEN
            and now - self._last_failure_time >= self.config.recovery_timeout
        ):
            self._transition_to(CircuitBreakerState.HALF_OPEN)
            self._half_open_calls = 0
            self._successes = 0

    def _transition_to(self, new_state: CircuitBreakerState) -> None:
        """Transition to a new state with logging."""
        old_state = self._state
        self._state = new_state
        self._state_changes += 1
        logger.info(
            "circuit_breaker_state_change",
            name=self.name,
            old_state=old_state.value,
            new_state=new_state.value,
            failures=self._failures,
        )

    async def can_execute(self) -> bool:
        """Check if request can be executed.

        Returns:
            True if request is allowed.

        Raises:
            MoskConnectionError: If circuit is open.
        """
        async with self._lock:
            self._check_state_transition_unlocked()

            if self._state == CircuitBreakerState.CLOSED:
                return True

            if self._state == CircuitBreakerState.HALF_OPEN:
                if self._half_open_calls < self.config.half_open_max_calls:
                    self._half_open_calls += 1
                    return True
                return False

            # OPEN state
            remaining = self.config.recovery_timeout - (time.monotonic() - self._last_failure_time)
            raise MoskConnectionError(
                f"Circuit breaker '{self.name}' is open. Recovery in {remaining:.1f}s",
                service=self.name,
                details={"recovery_time": remaining, "state": self._state.value},
            )

    async def record_success(self) -> None:
        """Record a successful operation."""
        async with self._lock:
            self._successes += 1
            self._total_successes += 1

            if (
                self._state == CircuitBreakerState.HALF_OPEN
                and self._successes >= self.config.success_threshold
            ):
                self._transition_to(CircuitBreakerState.CLOSED)
                self._failures = 0
                self._successes = 0

    async def record_failure(self, error: Exception | None = None) -> None:
        """Record a failed operation.

        Args:
            error: The exception that caused the failure.
        """
        async with self._lock:
            self._failures += 1
            self._total_failures += 1
            self._last_failure_time = time.monotonic()

            if self._state == CircuitBreakerState.HALF_OPEN or (
                self._state == CircuitBreakerState.CLOSED
                and self._failures >= self.config.failure_threshold
            ):
                self._transition_to(CircuitBreakerState.OPEN)

            if error:
                logger.warning(
                    "circuit_breaker_failure",
                    name=self.name,
                    failures=self._failures,
                    error=str(error),
                )

    async def reset(self) -> None:
        """Reset circuit breaker to closed state."""
        async with self._lock:
            self._state = CircuitBreakerState.CLOSED
            self._failures = 0
            self._successes = 0
            self._half_open_calls = 0
            logger.info("circuit_breaker_reset", name=self.name)
