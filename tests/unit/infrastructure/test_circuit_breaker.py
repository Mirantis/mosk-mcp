"""Tests for circuit breaker implementation.

Tests the CircuitBreaker class state transitions, failure tracking,
and recovery behavior.
"""

import asyncio

import pytest

from mosk_mcp.core.exceptions import MoskConnectionError
from mosk_mcp.infrastructure.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitBreakerState,
)


class TestCircuitBreakerInitialization:
    """Tests for circuit breaker initialization."""

    def test_default_config(self) -> None:
        """Test circuit breaker with default configuration."""
        cb = CircuitBreaker("test")
        assert cb.name == "test"
        assert cb.state == CircuitBreakerState.CLOSED
        assert cb.is_closed is True
        assert cb.is_open is False
        assert cb.config.failure_threshold == 5
        assert cb.config.recovery_timeout == 30.0

    def test_custom_config(self) -> None:
        """Test circuit breaker with custom configuration."""
        config = CircuitBreakerConfig(
            failure_threshold=3,
            recovery_timeout=10.0,
            half_open_max_calls=2,
            success_threshold=1,
        )
        cb = CircuitBreaker("custom", config)
        assert cb.config.failure_threshold == 3
        assert cb.config.recovery_timeout == 10.0
        assert cb.config.half_open_max_calls == 2
        assert cb.config.success_threshold == 1

    def test_initial_metrics(self) -> None:
        """Test initial metrics are zero."""
        cb = CircuitBreaker("test")
        metrics = cb.metrics
        assert metrics["name"] == "test"
        assert metrics["state"] == "closed"
        assert metrics["failures"] == 0
        assert metrics["successes"] == 0
        assert metrics["total_failures"] == 0
        assert metrics["total_successes"] == 0
        assert metrics["state_changes"] == 0


class TestCircuitBreakerClosedState:
    """Tests for circuit breaker in CLOSED state."""

    @pytest.mark.asyncio
    async def test_can_execute_when_closed(self) -> None:
        """Test that requests are allowed when circuit is closed."""
        cb = CircuitBreaker("test")
        assert await cb.can_execute() is True

    @pytest.mark.asyncio
    async def test_record_success_increments_counters(self) -> None:
        """Test that recording success increments counters."""
        cb = CircuitBreaker("test")
        await cb.record_success()
        assert cb.metrics["successes"] == 1
        assert cb.metrics["total_successes"] == 1

    @pytest.mark.asyncio
    async def test_record_failure_increments_counters(self) -> None:
        """Test that recording failure increments counters."""
        cb = CircuitBreaker("test")
        await cb.record_failure()
        assert cb.metrics["failures"] == 1
        assert cb.metrics["total_failures"] == 1

    @pytest.mark.asyncio
    async def test_stays_closed_below_threshold(self) -> None:
        """Test circuit stays closed when failures are below threshold."""
        config = CircuitBreakerConfig(failure_threshold=5)
        cb = CircuitBreaker("test", config)

        # Record 4 failures (below threshold of 5)
        for _ in range(4):
            await cb.record_failure()

        assert cb.state == CircuitBreakerState.CLOSED
        assert cb.is_closed is True


class TestCircuitBreakerStateTransitions:
    """Tests for circuit breaker state transitions."""

    @pytest.mark.asyncio
    async def test_closed_to_open_on_threshold(self) -> None:
        """Test circuit opens when failure threshold is reached."""
        config = CircuitBreakerConfig(failure_threshold=3)
        cb = CircuitBreaker("test", config)

        # Record failures up to threshold
        for _ in range(3):
            await cb.record_failure()

        assert cb.state == CircuitBreakerState.OPEN
        assert cb.is_open is True
        assert cb.metrics["state_changes"] == 1

    @pytest.mark.asyncio
    async def test_open_to_half_open_after_timeout(self) -> None:
        """Test circuit transitions to half-open after recovery timeout."""
        config = CircuitBreakerConfig(
            failure_threshold=1,
            recovery_timeout=0.1,  # 100ms for fast test
        )
        cb = CircuitBreaker("test", config)

        # Open the circuit
        await cb.record_failure()
        assert cb.state == CircuitBreakerState.OPEN

        # Wait for recovery timeout
        await asyncio.sleep(0.15)

        # Trigger state check by calling can_execute
        assert await cb.can_execute() is True
        assert cb.state == CircuitBreakerState.HALF_OPEN

    @pytest.mark.asyncio
    async def test_half_open_to_closed_on_success(self) -> None:
        """Test circuit closes after success threshold in half-open."""
        config = CircuitBreakerConfig(
            failure_threshold=1,
            recovery_timeout=0.1,
            success_threshold=2,
        )
        cb = CircuitBreaker("test", config)

        # Open the circuit
        await cb.record_failure()

        # Wait for half-open
        await asyncio.sleep(0.15)
        await cb.can_execute()

        # Record successes to meet threshold
        await cb.record_success()
        assert cb.state == CircuitBreakerState.HALF_OPEN

        await cb.record_success()
        assert cb.state == CircuitBreakerState.CLOSED

    @pytest.mark.asyncio
    async def test_half_open_to_open_on_failure(self) -> None:
        """Test circuit reopens on any failure in half-open state."""
        config = CircuitBreakerConfig(
            failure_threshold=1,
            recovery_timeout=0.1,
        )
        cb = CircuitBreaker("test", config)

        # Open the circuit
        await cb.record_failure()

        # Wait for half-open
        await asyncio.sleep(0.15)
        await cb.can_execute()
        assert cb.state == CircuitBreakerState.HALF_OPEN

        # Single failure should reopen
        await cb.record_failure()
        assert cb.state == CircuitBreakerState.OPEN


class TestCircuitBreakerOpenState:
    """Tests for circuit breaker in OPEN state."""

    @pytest.mark.asyncio
    async def test_can_execute_raises_when_open(self) -> None:
        """Test that can_execute raises MoskConnectionError when open."""
        config = CircuitBreakerConfig(
            failure_threshold=1,
            recovery_timeout=60.0,  # Long timeout
        )
        cb = CircuitBreaker("test", config)

        # Open the circuit
        await cb.record_failure()

        # Should raise error
        with pytest.raises(MoskConnectionError) as exc_info:
            await cb.can_execute()

        assert "Circuit breaker 'test' is open" in str(exc_info.value)
        assert exc_info.value.details is not None
        assert exc_info.value.details["state"] == "open"


class TestCircuitBreakerHalfOpenState:
    """Tests for circuit breaker in HALF_OPEN state."""

    @pytest.mark.asyncio
    async def test_limited_calls_in_half_open(self) -> None:
        """Test that only limited calls are allowed in half-open state."""
        config = CircuitBreakerConfig(
            failure_threshold=1,
            recovery_timeout=0.1,
            half_open_max_calls=2,
        )
        cb = CircuitBreaker("test", config)

        # Open and wait for half-open
        await cb.record_failure()
        await asyncio.sleep(0.15)

        # First two calls should succeed
        assert await cb.can_execute() is True
        assert await cb.can_execute() is True

        # Third call should fail (max calls reached)
        assert await cb.can_execute() is False


class TestCircuitBreakerReset:
    """Tests for circuit breaker reset functionality."""

    @pytest.mark.asyncio
    async def test_reset_from_open(self) -> None:
        """Test resetting circuit from open state."""
        config = CircuitBreakerConfig(failure_threshold=1)
        cb = CircuitBreaker("test", config)

        # Open the circuit
        await cb.record_failure()
        assert cb.is_open is True

        # Reset
        await cb.reset()
        assert cb.state == CircuitBreakerState.CLOSED
        assert cb.metrics["failures"] == 0
        assert cb.metrics["successes"] == 0

    @pytest.mark.asyncio
    async def test_reset_preserves_total_counters(self) -> None:
        """Test that reset doesn't clear total counters."""
        config = CircuitBreakerConfig(failure_threshold=5)
        cb = CircuitBreaker("test", config)

        # Record some operations
        await cb.record_failure()
        await cb.record_success()

        total_failures = cb.metrics["total_failures"]
        total_successes = cb.metrics["total_successes"]

        await cb.reset()

        # Current counters are reset
        assert cb.metrics["failures"] == 0
        assert cb.metrics["successes"] == 0

        # Total counters preserved
        assert cb.metrics["total_failures"] == total_failures
        assert cb.metrics["total_successes"] == total_successes


class TestCircuitBreakerConcurrency:
    """Tests for concurrent access to circuit breaker."""

    @pytest.mark.asyncio
    async def test_concurrent_record_failures(self) -> None:
        """Test concurrent failure recording is thread-safe."""
        config = CircuitBreakerConfig(failure_threshold=100)
        cb = CircuitBreaker("test", config)

        # Record many failures concurrently
        tasks = [cb.record_failure() for _ in range(50)]
        await asyncio.gather(*tasks)

        assert cb.metrics["failures"] == 50
        assert cb.metrics["total_failures"] == 50

    @pytest.mark.asyncio
    async def test_concurrent_mixed_operations(self) -> None:
        """Test concurrent mixed operations are thread-safe."""
        config = CircuitBreakerConfig(failure_threshold=100)
        cb = CircuitBreaker("test", config)

        async def mixed_ops() -> None:
            await cb.record_failure()
            await cb.record_success()
            await cb.can_execute()

        tasks = [mixed_ops() for _ in range(20)]
        await asyncio.gather(*tasks)

        assert cb.metrics["total_failures"] == 20
        assert cb.metrics["total_successes"] == 20


class TestCircuitBreakerLockAssertion:
    """Tests for lock convention in _check_state_transition_unlocked.

    Note: The previous tests checked that _check_state_transition raised when
    called without the lock. However, asyncio.Lock.locked() returns True if
    ANY coroutine holds the lock, not necessarily the calling coroutine.
    This created a TOCTOU race condition.

    The method is now named _check_state_transition_unlocked (sync, not async)
    to indicate via naming convention that it must be called with the lock held.
    Proper usage is enforced by naming convention and code review.
    """

    def test_method_is_named_unlocked(self) -> None:
        """Test that the method follows the _unlocked naming convention."""
        cb = CircuitBreaker("test")

        # The method should exist with the _unlocked suffix
        assert hasattr(cb, "_check_state_transition_unlocked")

        # It should be a regular method, not async (can be called sync)
        import inspect

        assert not inspect.iscoroutinefunction(cb._check_state_transition_unlocked)

    def test_check_state_transition_unlocked_is_sync(self) -> None:
        """Test that _check_state_transition_unlocked works as a sync method."""
        cb = CircuitBreaker("test")

        # Should be callable without await
        cb._check_state_transition_unlocked()  # Should not raise
