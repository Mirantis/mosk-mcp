"""Graceful shutdown management for MOSK MCP Server.

This module provides graceful shutdown functionality to ensure
clean termination of the server and all its components.

Features:
- Signal handling (SIGTERM, SIGINT, SIGHUP)
- Shutdown hooks for cleanup callbacks
- Graceful connection draining
- Health endpoint integration during shutdown
- Configurable shutdown timeout
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import TYPE_CHECKING, Any

from mosk_mcp.observability.logging import get_logger


if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from mosk_mcp.core.config import Settings

logger = get_logger(__name__)


class ShutdownState(str, Enum):
    """Server shutdown state.

    Attributes:
        RUNNING: Server is running normally.
        DRAINING: Server is draining connections (not accepting new ones).
        SHUTTING_DOWN: Server is executing shutdown hooks.
        STOPPED: Server has stopped.
    """

    RUNNING = "running"
    DRAINING = "draining"
    SHUTTING_DOWN = "shutting_down"
    STOPPED = "stopped"


@dataclass
class ShutdownHook:
    """A shutdown hook callback.

    Attributes:
        name: Descriptive name for the hook.
        callback: Async function to call during shutdown.
        priority: Lower numbers run first (default 100).
        timeout: Maximum time to wait for this hook (seconds).
    """

    name: str
    callback: Callable[[], Awaitable[None]]
    priority: int = 100
    timeout: float = 30.0


@dataclass
class ShutdownEvent:
    """Information about a shutdown event.

    Attributes:
        signal_name: Name of the signal received (if any).
        reason: Reason for shutdown.
        timestamp: When shutdown was initiated.
    """

    signal_name: str | None
    reason: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))


class GracefulShutdownManager:
    """Manages graceful shutdown of the server.

    This class provides:
    - Signal handler registration for SIGTERM, SIGINT
    - Shutdown hook management with priorities
    - Connection draining support
    - Health check integration during shutdown

    Attributes:
        state: Current shutdown state.
        shutdown_timeout: Maximum time for entire shutdown process.
        drain_timeout: Time to drain connections before forcing shutdown.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        shutdown_timeout: float = 60.0,
        drain_timeout: float = 30.0,
    ) -> None:
        """Initialize the shutdown manager.

        Args:
            settings: Application settings.
            shutdown_timeout: Maximum shutdown time in seconds.
            drain_timeout: Connection drain timeout in seconds.
        """
        self._state = ShutdownState.RUNNING
        self._shutdown_timeout = shutdown_timeout
        self._drain_timeout = drain_timeout
        self._hooks: list[ShutdownHook] = []
        self._shutdown_event: ShutdownEvent | None = None
        self._shutdown_complete = asyncio.Event()
        self._lock = asyncio.Lock()
        self._active_requests = 0
        self._signal_handlers_installed = False
        # Track background tasks to prevent GC and allow proper cleanup
        self._background_tasks: set[asyncio.Task[None]] = set()

        if settings:
            self._shutdown_timeout = getattr(settings, "shutdown_timeout", shutdown_timeout)
            self._drain_timeout = getattr(settings, "drain_timeout", drain_timeout)

    @property
    def state(self) -> ShutdownState:
        """Get current shutdown state."""
        return self._state

    @property
    def is_running(self) -> bool:
        """Check if server is running normally."""
        return self._state == ShutdownState.RUNNING

    @property
    def is_draining(self) -> bool:
        """Check if server is draining connections."""
        return self._state == ShutdownState.DRAINING

    @property
    def is_shutting_down(self) -> bool:
        """Check if server is shutting down."""
        return self._state in (ShutdownState.DRAINING, ShutdownState.SHUTTING_DOWN)

    @property
    def is_stopped(self) -> bool:
        """Check if server has stopped."""
        return self._state == ShutdownState.STOPPED

    @property
    def active_requests(self) -> int:
        """Get number of active requests."""
        return self._active_requests

    @property
    def shutdown_event(self) -> ShutdownEvent | None:
        """Get the shutdown event if shutdown has been initiated."""
        return self._shutdown_event

    def register_hook(
        self,
        name: str,
        callback: Callable[[], Awaitable[None]],
        priority: int = 100,
        timeout: float = 30.0,
    ) -> None:
        """Register a shutdown hook.

        Args:
            name: Descriptive name for the hook.
            callback: Async function to call during shutdown.
            priority: Lower numbers run first.
            timeout: Maximum time to wait for this hook.
        """
        hook = ShutdownHook(
            name=name,
            callback=callback,
            priority=priority,
            timeout=timeout,
        )
        self._hooks.append(hook)
        # Keep sorted by priority
        self._hooks.sort(key=lambda h: h.priority)
        logger.debug("shutdown_hook_registered", name=name, priority=priority)

    def unregister_hook(self, name: str) -> bool:
        """Unregister a shutdown hook by name.

        Args:
            name: Name of the hook to remove.

        Returns:
            True if hook was found and removed.
        """
        for i, hook in enumerate(self._hooks):
            if hook.name == name:
                del self._hooks[i]
                logger.debug("shutdown_hook_unregistered", name=name)
                return True
        return False

    def request_started(self) -> None:
        """Track that a request has started."""
        self._active_requests += 1

    def request_finished(self) -> None:
        """Track that a request has finished."""
        self._active_requests = max(0, self._active_requests - 1)

    def install_signal_handlers(self) -> None:
        """Install signal handlers for graceful shutdown.

        Installs handlers for:
        - SIGTERM: Graceful shutdown (from kubernetes/docker)
        - SIGINT: Graceful shutdown (Ctrl+C)
        - SIGHUP: Reload configuration (future use)
        """
        if self._signal_handlers_installed:
            return

        loop = asyncio.get_event_loop()

        # Define signal handler (uses instance variable for task tracking)
        def handle_signal(sig: signal.Signals) -> None:
            sig_name = sig.name
            logger.info("signal_received", signal=sig_name)
            task = asyncio.create_task(self.initiate_shutdown(signal_name=sig_name))
            self._background_tasks.add(task)
            task.add_done_callback(self._background_tasks.discard)

        # Install handlers
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, handle_signal, sig)
            except (NotImplementedError, RuntimeError):
                # Windows doesn't support add_signal_handler
                # Fall back to signal.signal
                signal.signal(sig, lambda s, _f: handle_signal(signal.Signals(s)))

        # SIGHUP for config reload (Unix only)
        if sys.platform != "win32":
            with contextlib.suppress(NotImplementedError, RuntimeError):
                loop.add_signal_handler(
                    signal.SIGHUP,
                    lambda: logger.info("sighup_received", message="Config reload not implemented"),
                )

        self._signal_handlers_installed = True
        logger.debug("signal_handlers_installed")

    async def initiate_shutdown(
        self,
        signal_name: str | None = None,
        reason: str = "Shutdown requested",
    ) -> None:
        """Initiate graceful shutdown.

        Args:
            signal_name: Name of the signal that triggered shutdown.
            reason: Reason for shutdown.
        """
        async with self._lock:
            if self._state != ShutdownState.RUNNING:
                logger.warning(
                    "shutdown_already_in_progress",
                    current_state=self._state.value,
                )
                return

            self._shutdown_event = ShutdownEvent(
                signal_name=signal_name,
                reason=reason,
            )

            logger.info(
                "shutdown_initiated",
                signal=signal_name,
                reason=reason,
                active_requests=self._active_requests,
            )

            # Start draining
            self._state = ShutdownState.DRAINING

        # Wait for active requests to complete
        await self._drain_connections()

        # Execute shutdown hooks
        self._state = ShutdownState.SHUTTING_DOWN
        await self._execute_hooks()

        # Mark as stopped
        self._state = ShutdownState.STOPPED
        self._shutdown_complete.set()

        logger.info("shutdown_complete")

    async def _drain_connections(self) -> None:
        """Wait for active connections to drain."""
        if self._active_requests == 0:
            logger.debug("no_connections_to_drain")
            return

        logger.info(
            "draining_connections",
            active_requests=self._active_requests,
            timeout=self._drain_timeout,
        )

        # Wait for requests to complete or timeout
        start = asyncio.get_event_loop().time()
        while self._active_requests > 0:
            elapsed = asyncio.get_event_loop().time() - start
            if elapsed >= self._drain_timeout:
                logger.warning(
                    "drain_timeout_exceeded",
                    remaining_requests=self._active_requests,
                )
                break

            await asyncio.sleep(0.1)

        if self._active_requests == 0:
            logger.info("connections_drained")
        else:
            logger.warning(
                "forced_connection_closure",
                remaining=self._active_requests,
            )

    async def _execute_hooks(self) -> None:
        """Execute all registered shutdown hooks."""
        logger.info("executing_shutdown_hooks", hook_count=len(self._hooks))

        for hook in self._hooks:
            logger.debug("executing_hook", name=hook.name, priority=hook.priority)
            try:
                await asyncio.wait_for(
                    hook.callback(),
                    timeout=hook.timeout,
                )
                logger.debug("hook_completed", name=hook.name)
            except TimeoutError:
                logger.error(
                    "hook_timeout",
                    name=hook.name,
                    timeout=hook.timeout,
                )
            except Exception as e:
                logger.error(
                    "hook_error",
                    name=hook.name,
                    error=str(e),
                    error_type=type(e).__name__,
                )

    async def wait_for_shutdown(self) -> None:
        """Wait for shutdown to complete."""
        await self._shutdown_complete.wait()

    def get_health_status(self) -> dict[str, Any]:
        """Get health status for health check endpoints.

        Returns:
            Dictionary with shutdown-related health information.
        """
        return {
            "state": self._state.value,
            "is_healthy": self._state == ShutdownState.RUNNING,
            "is_ready": self._state == ShutdownState.RUNNING,
            "active_requests": self._active_requests,
            "shutdown_event": (
                {
                    "signal": self._shutdown_event.signal_name,
                    "reason": self._shutdown_event.reason,
                    "timestamp": self._shutdown_event.timestamp.isoformat(),
                }
                if self._shutdown_event
                else None
            ),
        }


# Global shutdown manager instance
_shutdown_manager: GracefulShutdownManager | None = None


def get_shutdown_manager() -> GracefulShutdownManager:
    """Get the global shutdown manager instance.

    Returns:
        The global GracefulShutdownManager instance.
    """
    global _shutdown_manager
    if _shutdown_manager is None:
        _shutdown_manager = GracefulShutdownManager()
    return _shutdown_manager


def set_shutdown_manager(manager: GracefulShutdownManager) -> None:
    """Set the global shutdown manager instance.

    Args:
        manager: GracefulShutdownManager to use globally.
    """
    global _shutdown_manager
    _shutdown_manager = manager


def register_shutdown_hook(
    name: str,
    callback: Callable[[], Awaitable[None]],
    priority: int = 100,
    timeout: float = 30.0,
) -> None:
    """Register a shutdown hook using the global manager.

    Args:
        name: Descriptive name for the hook.
        callback: Async function to call during shutdown.
        priority: Lower numbers run first.
        timeout: Maximum time to wait for this hook.
    """
    manager = get_shutdown_manager()
    manager.register_hook(name, callback, priority, timeout)
