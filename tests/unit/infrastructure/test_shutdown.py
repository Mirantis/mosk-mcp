"""Tests for graceful shutdown implementation.

Tests the GracefulShutdownManager class, shutdown hooks, and global functions.
"""

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mosk_mcp.infrastructure.shutdown import (
    GracefulShutdownManager,
    ShutdownEvent,
    ShutdownHook,
    ShutdownState,
    get_shutdown_manager,
    register_shutdown_hook,
    set_shutdown_manager,
)


# =============================================================================
# Enum Tests
# =============================================================================


class TestShutdownState:
    """Tests for ShutdownState enum."""

    def test_all_states_defined(self) -> None:
        """Test all shutdown states are defined."""
        assert ShutdownState.RUNNING == "running"
        assert ShutdownState.DRAINING == "draining"
        assert ShutdownState.SHUTTING_DOWN == "shutting_down"
        assert ShutdownState.STOPPED == "stopped"

    def test_state_count(self) -> None:
        """Test expected number of states."""
        assert len(ShutdownState) == 4


# =============================================================================
# ShutdownHook Tests
# =============================================================================


class TestShutdownHook:
    """Tests for ShutdownHook dataclass."""

    def test_required_fields(self) -> None:
        """Test creating hook with required fields."""
        callback = AsyncMock()
        hook = ShutdownHook(name="test", callback=callback)

        assert hook.name == "test"
        assert hook.callback == callback
        assert hook.priority == 100  # Default
        assert hook.timeout == 30.0  # Default

    def test_custom_fields(self) -> None:
        """Test creating hook with custom fields."""
        callback = AsyncMock()
        hook = ShutdownHook(
            name="test",
            callback=callback,
            priority=50,
            timeout=60.0,
        )

        assert hook.priority == 50
        assert hook.timeout == 60.0


# =============================================================================
# ShutdownEvent Tests
# =============================================================================


class TestShutdownEvent:
    """Tests for ShutdownEvent dataclass."""

    def test_with_signal(self) -> None:
        """Test creating event with signal."""
        event = ShutdownEvent(
            signal_name="SIGTERM",
            reason="Shutdown requested",
        )

        assert event.signal_name == "SIGTERM"
        assert event.reason == "Shutdown requested"
        assert isinstance(event.timestamp, datetime)

    def test_without_signal(self) -> None:
        """Test creating event without signal."""
        event = ShutdownEvent(
            signal_name=None,
            reason="Manual shutdown",
        )

        assert event.signal_name is None
        assert event.reason == "Manual shutdown"


# =============================================================================
# GracefulShutdownManager Initialization Tests
# =============================================================================


class TestGracefulShutdownManagerInitialization:
    """Tests for GracefulShutdownManager initialization."""

    def test_default_initialization(self) -> None:
        """Test default initialization."""
        manager = GracefulShutdownManager()

        assert manager.state == ShutdownState.RUNNING
        assert manager.is_running is True
        assert manager.is_draining is False
        assert manager.is_shutting_down is False
        assert manager.is_stopped is False
        assert manager.active_requests == 0
        assert manager.shutdown_event is None

    def test_custom_timeouts(self) -> None:
        """Test custom timeout configuration."""
        manager = GracefulShutdownManager(
            shutdown_timeout=120.0,
            drain_timeout=60.0,
        )

        assert manager._shutdown_timeout == 120.0
        assert manager._drain_timeout == 60.0

    def test_with_settings(self) -> None:
        """Test initialization with settings."""
        mock_settings = MagicMock()
        mock_settings.shutdown_timeout = 90.0
        mock_settings.drain_timeout = 45.0

        manager = GracefulShutdownManager(settings=mock_settings)

        assert manager._shutdown_timeout == 90.0
        assert manager._drain_timeout == 45.0


# =============================================================================
# Hook Registration Tests
# =============================================================================


class TestGracefulShutdownManagerHooks:
    """Tests for shutdown hook management."""

    def test_register_hook(self) -> None:
        """Test registering a shutdown hook."""
        manager = GracefulShutdownManager()
        callback = AsyncMock()

        manager.register_hook("test-hook", callback)

        assert len(manager._hooks) == 1
        assert manager._hooks[0].name == "test-hook"

    def test_register_multiple_hooks_sorted_by_priority(self) -> None:
        """Test hooks are sorted by priority."""
        manager = GracefulShutdownManager()

        manager.register_hook("low-priority", AsyncMock(), priority=200)
        manager.register_hook("high-priority", AsyncMock(), priority=50)
        manager.register_hook("medium-priority", AsyncMock(), priority=100)

        assert manager._hooks[0].name == "high-priority"
        assert manager._hooks[1].name == "medium-priority"
        assert manager._hooks[2].name == "low-priority"

    def test_unregister_hook_exists(self) -> None:
        """Test unregistering existing hook."""
        manager = GracefulShutdownManager()
        manager.register_hook("test-hook", AsyncMock())

        result = manager.unregister_hook("test-hook")

        assert result is True
        assert len(manager._hooks) == 0

    def test_unregister_hook_not_exists(self) -> None:
        """Test unregistering non-existent hook."""
        manager = GracefulShutdownManager()

        result = manager.unregister_hook("nonexistent")

        assert result is False


# =============================================================================
# Request Tracking Tests
# =============================================================================


class TestGracefulShutdownManagerRequestTracking:
    """Tests for request tracking."""

    def test_request_started(self) -> None:
        """Test tracking request start."""
        manager = GracefulShutdownManager()

        manager.request_started()

        assert manager.active_requests == 1

    def test_request_finished(self) -> None:
        """Test tracking request finish."""
        manager = GracefulShutdownManager()
        manager.request_started()

        manager.request_finished()

        assert manager.active_requests == 0

    def test_request_finished_never_negative(self) -> None:
        """Test request count never goes negative."""
        manager = GracefulShutdownManager()

        manager.request_finished()
        manager.request_finished()

        assert manager.active_requests == 0


# =============================================================================
# Shutdown Initiation Tests
# =============================================================================


class TestGracefulShutdownManagerInitiateShutdown:
    """Tests for initiating shutdown."""

    @pytest.mark.asyncio
    async def test_initiate_shutdown(self) -> None:
        """Test initiating shutdown."""
        manager = GracefulShutdownManager()

        await manager.initiate_shutdown(reason="Test shutdown")

        assert manager.state == ShutdownState.STOPPED
        assert manager.shutdown_event is not None
        assert manager.shutdown_event.reason == "Test shutdown"

    @pytest.mark.asyncio
    async def test_initiate_shutdown_with_signal(self) -> None:
        """Test initiating shutdown with signal."""
        manager = GracefulShutdownManager()

        await manager.initiate_shutdown(signal_name="SIGTERM", reason="Signal received")

        assert manager.shutdown_event is not None
        assert manager.shutdown_event.signal_name == "SIGTERM"

    @pytest.mark.asyncio
    async def test_initiate_shutdown_idempotent(self) -> None:
        """Test shutdown initiation is idempotent."""
        manager = GracefulShutdownManager()

        await manager.initiate_shutdown(reason="First")
        await manager.initiate_shutdown(reason="Second")

        assert manager.shutdown_event.reason == "First"

    @pytest.mark.asyncio
    async def test_initiate_shutdown_executes_hooks(self) -> None:
        """Test shutdown executes registered hooks."""
        manager = GracefulShutdownManager()
        hook_called = False

        async def test_hook() -> None:
            nonlocal hook_called
            hook_called = True

        manager.register_hook("test", test_hook)

        await manager.initiate_shutdown()

        assert hook_called is True


# =============================================================================
# Connection Draining Tests
# =============================================================================


class TestGracefulShutdownManagerDraining:
    """Tests for connection draining."""

    @pytest.mark.asyncio
    async def test_drain_no_active_requests(self) -> None:
        """Test draining with no active requests."""
        manager = GracefulShutdownManager()

        await manager._drain_connections()

        # Should complete immediately

    @pytest.mark.asyncio
    async def test_drain_waits_for_requests(self) -> None:
        """Test draining waits for active requests."""
        manager = GracefulShutdownManager(drain_timeout=1.0)
        manager.request_started()

        # Start draining in background
        drain_task = asyncio.create_task(manager._drain_connections())

        # Simulate request completion after small delay
        await asyncio.sleep(0.1)
        manager.request_finished()

        await drain_task

        assert manager.active_requests == 0

    @pytest.mark.asyncio
    async def test_drain_timeout(self) -> None:
        """Test draining times out with stuck requests."""
        manager = GracefulShutdownManager(drain_timeout=0.1)
        manager.request_started()

        await manager._drain_connections()

        # Should timeout but continue
        assert manager.active_requests == 1


# =============================================================================
# Hook Execution Tests
# =============================================================================


class TestGracefulShutdownManagerHookExecution:
    """Tests for hook execution."""

    @pytest.mark.asyncio
    async def test_execute_hooks_in_order(self) -> None:
        """Test hooks execute in priority order."""
        manager = GracefulShutdownManager()
        order: list[str] = []

        async def hook1() -> None:
            order.append("first")

        async def hook2() -> None:
            order.append("second")

        manager.register_hook("first", hook1, priority=10)
        manager.register_hook("second", hook2, priority=20)

        await manager._execute_hooks()

        assert order == ["first", "second"]

    @pytest.mark.asyncio
    async def test_execute_hook_timeout(self) -> None:
        """Test hook timeout handling."""
        manager = GracefulShutdownManager()

        async def slow_hook() -> None:
            await asyncio.sleep(10)  # Much longer than timeout

        manager.register_hook("slow", slow_hook, timeout=0.1)

        # Should complete without hanging
        await manager._execute_hooks()

    @pytest.mark.asyncio
    async def test_execute_hook_error_handling(self) -> None:
        """Test hook error handling."""
        manager = GracefulShutdownManager()

        async def failing_hook() -> None:
            raise RuntimeError("Hook failed")

        manager.register_hook("failing", failing_hook)

        # Should complete without raising
        await manager._execute_hooks()


# =============================================================================
# Wait for Shutdown Tests
# =============================================================================


class TestGracefulShutdownManagerWait:
    """Tests for waiting for shutdown."""

    @pytest.mark.asyncio
    async def test_wait_for_shutdown(self) -> None:
        """Test waiting for shutdown to complete."""
        manager = GracefulShutdownManager()

        # Start shutdown in background
        shutdown_task = asyncio.create_task(manager.initiate_shutdown())

        # Wait for shutdown
        await manager.wait_for_shutdown()

        await shutdown_task

        assert manager.is_stopped is True


# =============================================================================
# Health Status Tests
# =============================================================================


class TestGracefulShutdownManagerHealthStatus:
    """Tests for health status reporting."""

    def test_health_status_when_running(self) -> None:
        """Test health status when running."""
        manager = GracefulShutdownManager()

        status = manager.get_health_status()

        assert status["state"] == "running"
        assert status["is_healthy"] is True
        assert status["is_ready"] is True
        assert status["active_requests"] == 0
        assert status["shutdown_event"] is None

    @pytest.mark.asyncio
    async def test_health_status_after_shutdown(self) -> None:
        """Test health status after shutdown."""
        manager = GracefulShutdownManager()
        await manager.initiate_shutdown(signal_name="SIGTERM", reason="Test")

        status = manager.get_health_status()

        assert status["state"] == "stopped"
        assert status["is_healthy"] is False
        assert status["is_ready"] is False
        assert status["shutdown_event"] is not None
        assert status["shutdown_event"]["signal"] == "SIGTERM"


# =============================================================================
# Global Functions Tests
# =============================================================================


class TestGlobalFunctions:
    """Tests for global shutdown manager functions."""

    def test_get_shutdown_manager_creates_singleton(self) -> None:
        """Test get_shutdown_manager creates singleton."""
        # Reset global state
        from mosk_mcp.infrastructure import shutdown

        shutdown._shutdown_manager = None

        manager1 = get_shutdown_manager()
        manager2 = get_shutdown_manager()

        assert manager1 is manager2

        # Cleanup
        shutdown._shutdown_manager = None

    def test_set_shutdown_manager(self) -> None:
        """Test set_shutdown_manager sets the global manager."""
        from mosk_mcp.infrastructure import shutdown

        original = shutdown._shutdown_manager

        new_manager = GracefulShutdownManager()
        set_shutdown_manager(new_manager)

        assert get_shutdown_manager() is new_manager

        # Cleanup
        shutdown._shutdown_manager = original

    def test_register_shutdown_hook_global(self) -> None:
        """Test registering hook via global function."""
        from mosk_mcp.infrastructure import shutdown

        original = shutdown._shutdown_manager
        manager = GracefulShutdownManager()
        set_shutdown_manager(manager)

        callback = AsyncMock()
        register_shutdown_hook("global-test", callback)

        assert len(manager._hooks) == 1
        assert manager._hooks[0].name == "global-test"

        # Cleanup
        shutdown._shutdown_manager = original


# =============================================================================
# Signal Handler Tests
# =============================================================================


class TestSignalHandlers:
    """Tests for signal handler installation."""

    def test_install_signal_handlers_idempotent(self) -> None:
        """Test signal handler installation is idempotent."""
        manager = GracefulShutdownManager()

        # Note: We can't easily test actual signal handling,
        # but we can verify idempotency flag
        with patch("asyncio.get_event_loop") as mock_loop:
            mock_loop.return_value = MagicMock()

            manager.install_signal_handlers()
            assert manager._signal_handlers_installed is True

            # Second call should be no-op
            manager.install_signal_handlers()


# =============================================================================
# State Properties Tests
# =============================================================================


class TestStateProperties:
    """Tests for state property methods."""

    def test_is_running(self) -> None:
        """Test is_running property."""
        manager = GracefulShutdownManager()
        manager._state = ShutdownState.RUNNING
        assert manager.is_running is True

        manager._state = ShutdownState.DRAINING
        assert manager.is_running is False

    def test_is_draining(self) -> None:
        """Test is_draining property."""
        manager = GracefulShutdownManager()
        manager._state = ShutdownState.DRAINING
        assert manager.is_draining is True

        manager._state = ShutdownState.RUNNING
        assert manager.is_draining is False

    def test_is_shutting_down(self) -> None:
        """Test is_shutting_down property."""
        manager = GracefulShutdownManager()

        manager._state = ShutdownState.DRAINING
        assert manager.is_shutting_down is True

        manager._state = ShutdownState.SHUTTING_DOWN
        assert manager.is_shutting_down is True

        manager._state = ShutdownState.RUNNING
        assert manager.is_shutting_down is False

    def test_is_stopped(self) -> None:
        """Test is_stopped property."""
        manager = GracefulShutdownManager()
        manager._state = ShutdownState.STOPPED
        assert manager.is_stopped is True

        manager._state = ShutdownState.RUNNING
        assert manager.is_stopped is False
