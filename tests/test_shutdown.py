"""Tests for the graceful shutdown module.

This module tests the GracefulShutdownManager class and related functionality.
"""

import asyncio

import pytest

from mosk_mcp.core.config import Environment, LogFormat, Settings
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
# Fixtures
# =============================================================================


@pytest.fixture
def shutdown_manager():
    """Create a shutdown manager for testing."""
    return GracefulShutdownManager(
        shutdown_timeout=5.0,
        drain_timeout=2.0,
    )


@pytest.fixture
def settings():
    """Create test settings."""
    return Settings(
        shutdown_timeout=10,
        drain_timeout=5,
        auth_enabled=False,
        otel_enabled=False,
        log_format=LogFormat.CONSOLE,
        environment=Environment.DEVELOPMENT,
    )


@pytest.fixture(autouse=True)
def reset_global_manager():
    """Reset global shutdown manager between tests."""
    set_shutdown_manager(GracefulShutdownManager())
    yield


# =============================================================================
# ShutdownState Tests
# =============================================================================


class TestShutdownState:
    """Tests for ShutdownState enum."""

    def test_state_values(self):
        """Test shutdown state values."""
        assert ShutdownState.RUNNING.value == "running"
        assert ShutdownState.DRAINING.value == "draining"
        assert ShutdownState.SHUTTING_DOWN.value == "shutting_down"
        assert ShutdownState.STOPPED.value == "stopped"


# =============================================================================
# ShutdownHook Tests
# =============================================================================


class TestShutdownHook:
    """Tests for ShutdownHook dataclass."""

    def test_hook_creation(self):
        """Test creating a shutdown hook."""

        async def callback():
            pass

        hook = ShutdownHook(
            name="test_hook",
            callback=callback,
            priority=50,
            timeout=15.0,
        )

        assert hook.name == "test_hook"
        assert hook.priority == 50
        assert hook.timeout == 15.0

    def test_hook_defaults(self):
        """Test hook default values."""

        async def callback():
            pass

        hook = ShutdownHook(name="test", callback=callback)

        assert hook.priority == 100
        assert hook.timeout == 30.0


# =============================================================================
# ShutdownEvent Tests
# =============================================================================


class TestShutdownEvent:
    """Tests for ShutdownEvent dataclass."""

    def test_event_creation(self):
        """Test creating a shutdown event."""
        event = ShutdownEvent(
            signal_name="SIGTERM",
            reason="Test shutdown",
        )

        assert event.signal_name == "SIGTERM"
        assert event.reason == "Test shutdown"
        assert event.timestamp is not None


# =============================================================================
# GracefulShutdownManager Tests
# =============================================================================


class TestGracefulShutdownManager:
    """Tests for GracefulShutdownManager class."""

    def test_initialization(self, shutdown_manager):
        """Test shutdown manager initialization."""
        assert shutdown_manager.state == ShutdownState.RUNNING
        assert shutdown_manager.is_running is True
        assert shutdown_manager.is_shutting_down is False
        assert shutdown_manager.active_requests == 0

    def test_initialization_from_settings(self, settings):
        """Test initialization from settings."""
        manager = GracefulShutdownManager(settings=settings)

        assert manager._shutdown_timeout == 10
        assert manager._drain_timeout == 5

    def test_register_hook(self, shutdown_manager):
        """Test registering a shutdown hook."""

        async def callback():
            pass

        shutdown_manager.register_hook(
            name="test_hook",
            callback=callback,
            priority=50,
        )

        assert len(shutdown_manager._hooks) == 1
        assert shutdown_manager._hooks[0].name == "test_hook"

    def test_hooks_sorted_by_priority(self, shutdown_manager):
        """Test that hooks are sorted by priority."""

        async def callback():
            pass

        shutdown_manager.register_hook("high_priority", callback, priority=10)
        shutdown_manager.register_hook("medium_priority", callback, priority=50)
        shutdown_manager.register_hook("low_priority", callback, priority=100)

        assert shutdown_manager._hooks[0].name == "high_priority"
        assert shutdown_manager._hooks[1].name == "medium_priority"
        assert shutdown_manager._hooks[2].name == "low_priority"

    def test_unregister_hook(self, shutdown_manager):
        """Test unregistering a shutdown hook."""

        async def callback():
            pass

        shutdown_manager.register_hook("test_hook", callback)
        assert len(shutdown_manager._hooks) == 1

        result = shutdown_manager.unregister_hook("test_hook")
        assert result is True
        assert len(shutdown_manager._hooks) == 0

    def test_unregister_nonexistent_hook(self, shutdown_manager):
        """Test unregistering a hook that doesn't exist."""
        result = shutdown_manager.unregister_hook("nonexistent")
        assert result is False

    def test_request_tracking(self, shutdown_manager):
        """Test request tracking."""
        assert shutdown_manager.active_requests == 0

        shutdown_manager.request_started()
        assert shutdown_manager.active_requests == 1

        shutdown_manager.request_started()
        assert shutdown_manager.active_requests == 2

        shutdown_manager.request_finished()
        assert shutdown_manager.active_requests == 1

        shutdown_manager.request_finished()
        assert shutdown_manager.active_requests == 0

    def test_request_finished_not_negative(self, shutdown_manager):
        """Test that request count doesn't go negative."""
        shutdown_manager.request_finished()
        assert shutdown_manager.active_requests == 0

    @pytest.mark.asyncio
    async def test_initiate_shutdown(self, shutdown_manager):
        """Test initiating shutdown."""
        await shutdown_manager.initiate_shutdown(
            signal_name="SIGTERM",
            reason="Test shutdown",
        )

        assert shutdown_manager.state == ShutdownState.STOPPED
        assert shutdown_manager.is_stopped is True
        assert shutdown_manager.shutdown_event is not None
        assert shutdown_manager.shutdown_event.signal_name == "SIGTERM"

    @pytest.mark.asyncio
    async def test_shutdown_only_once(self, shutdown_manager):
        """Test that shutdown can only be initiated once."""
        await shutdown_manager.initiate_shutdown(reason="First")

        # Second call should be ignored
        await shutdown_manager.initiate_shutdown(reason="Second")

        assert shutdown_manager.shutdown_event.reason == "First"

    @pytest.mark.asyncio
    async def test_shutdown_executes_hooks(self, shutdown_manager):
        """Test that shutdown executes registered hooks."""
        hook_executed = []

        async def callback():
            hook_executed.append(True)

        shutdown_manager.register_hook("test", callback)

        await shutdown_manager.initiate_shutdown()

        assert hook_executed == [True]

    @pytest.mark.asyncio
    async def test_shutdown_hook_order(self, shutdown_manager):
        """Test that hooks execute in priority order."""
        execution_order = []

        async def callback1():
            execution_order.append(1)

        async def callback2():
            execution_order.append(2)

        async def callback3():
            execution_order.append(3)

        shutdown_manager.register_hook("third", callback3, priority=100)
        shutdown_manager.register_hook("first", callback1, priority=10)
        shutdown_manager.register_hook("second", callback2, priority=50)

        await shutdown_manager.initiate_shutdown()

        assert execution_order == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_shutdown_hook_timeout(self, shutdown_manager):
        """Test that slow hooks are timed out."""

        async def slow_callback():
            await asyncio.sleep(10)  # Will timeout

        shutdown_manager.register_hook(
            "slow",
            slow_callback,
            timeout=0.1,  # 100ms timeout
        )

        # Should complete despite slow hook
        await asyncio.wait_for(
            shutdown_manager.initiate_shutdown(),
            timeout=2.0,
        )

        assert shutdown_manager.is_stopped

    @pytest.mark.asyncio
    async def test_shutdown_hook_error(self, shutdown_manager):
        """Test that hook errors don't stop shutdown."""

        async def failing_callback():
            raise ValueError("Hook error")

        async def succeeding_callback():
            pass

        shutdown_manager.register_hook("failing", failing_callback, priority=10)
        shutdown_manager.register_hook("succeeding", succeeding_callback, priority=20)

        # Should complete despite hook error
        await shutdown_manager.initiate_shutdown()

        assert shutdown_manager.is_stopped

    @pytest.mark.asyncio
    async def test_drain_connections(self, shutdown_manager):
        """Test connection draining."""
        # Start some requests
        shutdown_manager.request_started()
        shutdown_manager.request_started()

        # Start shutdown in background
        shutdown_task = asyncio.create_task(shutdown_manager.initiate_shutdown())

        # Wait a bit then finish requests
        await asyncio.sleep(0.1)
        shutdown_manager.request_finished()
        shutdown_manager.request_finished()

        # Shutdown should complete
        await asyncio.wait_for(shutdown_task, timeout=5.0)

        assert shutdown_manager.is_stopped

    def test_get_health_status_running(self, shutdown_manager):
        """Test health status when running."""
        status = shutdown_manager.get_health_status()

        assert status["state"] == "running"
        assert status["is_healthy"] is True
        assert status["is_ready"] is True
        assert status["shutdown_event"] is None

    @pytest.mark.asyncio
    async def test_get_health_status_stopped(self, shutdown_manager):
        """Test health status when stopped."""
        await shutdown_manager.initiate_shutdown(reason="Test")

        status = shutdown_manager.get_health_status()

        assert status["state"] == "stopped"
        assert status["is_healthy"] is False
        assert status["is_ready"] is False
        assert status["shutdown_event"] is not None

    @pytest.mark.asyncio
    async def test_wait_for_shutdown(self, shutdown_manager):
        """Test waiting for shutdown completion."""
        # Start shutdown in background
        asyncio.create_task(shutdown_manager.initiate_shutdown())

        # Wait should complete
        await asyncio.wait_for(
            shutdown_manager.wait_for_shutdown(),
            timeout=5.0,
        )

        assert shutdown_manager.is_stopped

    def test_state_properties(self, shutdown_manager):
        """Test state property methods."""
        assert shutdown_manager.is_running is True
        assert shutdown_manager.is_draining is False
        assert shutdown_manager.is_shutting_down is False
        assert shutdown_manager.is_stopped is False


# =============================================================================
# Global Functions Tests
# =============================================================================


class TestGlobalFunctions:
    """Tests for module-level convenience functions."""

    def test_get_shutdown_manager(self):
        """Test getting global shutdown manager."""
        manager = get_shutdown_manager()
        assert isinstance(manager, GracefulShutdownManager)

    def test_set_shutdown_manager(self):
        """Test setting global shutdown manager."""
        custom_manager = GracefulShutdownManager(shutdown_timeout=120.0)
        set_shutdown_manager(custom_manager)

        assert get_shutdown_manager() is custom_manager

    def test_register_shutdown_hook(self):
        """Test registering hook via global function."""

        async def callback():
            pass

        register_shutdown_hook("global_test", callback, priority=50)

        manager = get_shutdown_manager()
        assert any(h.name == "global_test" for h in manager._hooks)


# =============================================================================
# Integration Tests
# =============================================================================


class TestShutdownIntegration:
    """Integration tests for graceful shutdown."""

    @pytest.mark.asyncio
    async def test_full_shutdown_flow(self):
        """Test complete shutdown flow."""
        manager = GracefulShutdownManager(
            shutdown_timeout=10.0,
            drain_timeout=2.0,
        )

        # Track hook executions
        hooks_executed = []

        async def cleanup_connections():
            hooks_executed.append("connections")

        async def cleanup_resources():
            hooks_executed.append("resources")

        async def final_cleanup():
            hooks_executed.append("final")

        manager.register_hook("connections", cleanup_connections, priority=10)
        manager.register_hook("resources", cleanup_resources, priority=50)
        manager.register_hook("final", final_cleanup, priority=100)

        # Simulate some active requests
        manager.request_started()
        manager.request_started()

        # Start shutdown
        shutdown_task = asyncio.create_task(
            manager.initiate_shutdown(signal_name="SIGTERM", reason="Test")
        )

        # Finish requests after short delay
        await asyncio.sleep(0.1)
        manager.request_finished()
        manager.request_finished()

        # Wait for shutdown
        await asyncio.wait_for(shutdown_task, timeout=10.0)

        # Verify
        assert manager.is_stopped
        assert hooks_executed == ["connections", "resources", "final"]
        assert manager.shutdown_event.signal_name == "SIGTERM"

    @pytest.mark.asyncio
    async def test_shutdown_with_settings(self):
        """Test shutdown with custom settings."""
        settings = Settings(
            shutdown_timeout=15,
            drain_timeout=5,
            auth_enabled=False,
            otel_enabled=False,
            log_format=LogFormat.CONSOLE,
            environment=Environment.DEVELOPMENT,
        )

        manager = GracefulShutdownManager(settings=settings)

        assert manager._shutdown_timeout == 15
        assert manager._drain_timeout == 5

        await manager.initiate_shutdown()
        assert manager.is_stopped
