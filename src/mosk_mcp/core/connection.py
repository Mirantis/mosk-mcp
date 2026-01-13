"""Connection management for Kubernetes clusters.

This module provides connection management with resilience features:
- Lazy connection initialization
- Automatic reconnection with exponential backoff
- Circuit breaker integration
- Health monitoring
- Connection metrics

Extracted from server_context.py for better modularity.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import TYPE_CHECKING, Any

from mosk_mcp.core.exceptions import MoskConnectionError
from mosk_mcp.infrastructure.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerConfig,
)
from mosk_mcp.observability.logging import get_logger


if TYPE_CHECKING:
    from collections.abc import Callable

    from mosk_mcp.adapters.kubernetes import KubernetesAdapter


logger = get_logger(__name__)


class ConnectionState(str, Enum):
    """Connection lifecycle states."""

    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    FAILED = "failed"
    CLOSED = "closed"


class ClusterType(str, Enum):
    """Kubernetes cluster types for MOSK architecture."""

    MCC = "mcc"  # Management cluster (LCM operations)
    MOSK = "mosk"  # Workload cluster (OpenStack operations)


class HealthStatus(str, Enum):
    """Health check status values."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


@dataclass
class ConnectionMetrics:
    """Metrics for a connection.

    Attributes:
        connect_count: Total connection attempts.
        disconnect_count: Total disconnections.
        reconnect_count: Total reconnection attempts.
        last_connected_at: Last successful connection time.
        last_error: Last error message.
        total_requests: Total requests processed.
        failed_requests: Total failed requests.
    """

    connect_count: int = 0
    disconnect_count: int = 0
    reconnect_count: int = 0
    last_connected_at: datetime | None = None
    last_disconnected_at: datetime | None = None
    last_error: str | None = None
    total_requests: int = 0
    failed_requests: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "connect_count": self.connect_count,
            "disconnect_count": self.disconnect_count,
            "reconnect_count": self.reconnect_count,
            "last_connected_at": (
                self.last_connected_at.isoformat() if self.last_connected_at else None
            ),
            "last_disconnected_at": (
                self.last_disconnected_at.isoformat() if self.last_disconnected_at else None
            ),
            "last_error": self.last_error,
            "total_requests": self.total_requests,
            "failed_requests": self.failed_requests,
            "success_rate": (
                (self.total_requests - self.failed_requests) / self.total_requests
                if self.total_requests > 0
                else None  # None indicates "no data" rather than "perfect success"
            ),
        }


class ConnectionManager:
    """Manages a Kubernetes cluster connection with resilience features.

    Features:
    - Lazy connection initialization
    - Automatic reconnection with exponential backoff
    - Circuit breaker integration
    - Health monitoring
    - Connection metrics
    """

    def __init__(
        self,
        cluster_type: ClusterType,
        adapter_factory: Callable[[], KubernetesAdapter],
        circuit_breaker_config: CircuitBreakerConfig | None = None,
        max_reconnect_attempts: int = 5,
        reconnect_base_delay: float = 1.0,
        reconnect_max_delay: float = 60.0,
        health_check_interval: float = 60.0,
    ) -> None:
        """Initialize connection manager.

        Args:
            cluster_type: Type of cluster (MCC or MOSK).
            adapter_factory: Factory function to create adapter.
            circuit_breaker_config: Circuit breaker configuration.
            max_reconnect_attempts: Max reconnection attempts.
            reconnect_base_delay: Base delay for reconnection backoff.
            reconnect_max_delay: Max delay for reconnection backoff.
            health_check_interval: Interval for health checks.
        """
        self.cluster_type = cluster_type
        self._adapter_factory = adapter_factory
        self._adapter: KubernetesAdapter | None = None
        self._state = ConnectionState.DISCONNECTED
        self._lock = asyncio.Lock()

        # Resilience
        self.circuit_breaker = CircuitBreaker(
            name=f"{cluster_type.value}_connection",
            config=circuit_breaker_config,
        )
        self.max_reconnect_attempts = max_reconnect_attempts
        self.reconnect_base_delay = reconnect_base_delay
        self.reconnect_max_delay = reconnect_max_delay

        # Health monitoring
        self.health_check_interval = health_check_interval
        self._health_check_task: asyncio.Task[None] | None = None
        self._health_status = HealthStatus.UNKNOWN

        # Metrics
        self.metrics = ConnectionMetrics()

    @property
    def state(self) -> ConnectionState:
        """Get current connection state."""
        return self._state

    @property
    def is_connected(self) -> bool:
        """Check if connected."""
        return self._state == ConnectionState.CONNECTED

    @property
    def health_status(self) -> HealthStatus:
        """Get current health status."""
        return self._health_status

    def _set_state(self, new_state: ConnectionState) -> None:
        """Set connection state with logging."""
        old_state = self._state
        self._state = new_state
        if old_state != new_state:
            logger.info(
                "connection_state_change",
                cluster=self.cluster_type.value,
                old_state=old_state.value,
                new_state=new_state.value,
            )

    async def get_adapter(self) -> KubernetesAdapter:
        """Get connected adapter, connecting if necessary.

        Returns:
            Connected KubernetesAdapter.

        Raises:
            MoskConnectionError: If connection fails.
        """
        # Fast path: already connected
        if self._state == ConnectionState.CONNECTED and self._adapter is not None:
            # Check circuit breaker
            await self.circuit_breaker.can_execute()
            return self._adapter

        # Slow path: need to connect
        async with self._lock:
            # Double-check after acquiring lock
            if self._state == ConnectionState.CONNECTED and self._adapter is not None:
                await self.circuit_breaker.can_execute()
                return self._adapter

            return await self._connect()

    async def _connect(self) -> KubernetesAdapter:
        """Establish connection (must be called with lock held).

        Returns:
            Connected KubernetesAdapter.

        Raises:
            MoskConnectionError: If connection fails.
        """
        self._set_state(ConnectionState.CONNECTING)
        self.metrics.connect_count += 1

        try:
            if self._adapter is None:
                self._adapter = self._adapter_factory()

            await self._adapter.connect()

            self._set_state(ConnectionState.CONNECTED)
            self._health_status = HealthStatus.HEALTHY
            self.metrics.last_connected_at = datetime.now(UTC)
            await self.circuit_breaker.reset()

            logger.info(
                "connection_established",
                cluster=self.cluster_type.value,
            )

            return self._adapter

        except Exception as e:
            self._set_state(ConnectionState.FAILED)
            self._health_status = HealthStatus.UNHEALTHY
            self.metrics.last_error = str(e)
            await self.circuit_breaker.record_failure(e)

            logger.error(
                "connection_failed",
                cluster=self.cluster_type.value,
                error=str(e),
            )

            raise MoskConnectionError(
                f"Failed to connect to {self.cluster_type.value} cluster: {e}",
                service=self.cluster_type.value,
            ) from e

    async def reconnect(self) -> KubernetesAdapter:
        """Attempt to reconnect with exponential backoff.

        Returns:
            Connected KubernetesAdapter.

        Raises:
            MoskConnectionError: If all reconnection attempts fail.
        """
        async with self._lock:
            self._set_state(ConnectionState.RECONNECTING)
            self.metrics.reconnect_count += 1

            last_error: Exception | None = None

            for attempt in range(self.max_reconnect_attempts):
                try:
                    # Calculate backoff delay
                    delay = min(
                        self.reconnect_base_delay * (2**attempt),
                        self.reconnect_max_delay,
                    )

                    if attempt > 0:
                        logger.info(
                            "reconnect_attempt",
                            cluster=self.cluster_type.value,
                            attempt=attempt + 1,
                            max_attempts=self.max_reconnect_attempts,
                            delay=delay,
                        )
                        await asyncio.sleep(delay)

                    # Reset adapter for fresh connection
                    if self._adapter:
                        with contextlib.suppress(Exception):
                            await self._adapter.disconnect()
                        self._adapter = None

                    return await self._connect()

                except Exception as e:
                    last_error = e
                    logger.warning(
                        "reconnect_failed",
                        cluster=self.cluster_type.value,
                        attempt=attempt + 1,
                        error=str(e),
                    )

            self._set_state(ConnectionState.FAILED)
            raise MoskConnectionError(
                f"Failed to reconnect to {self.cluster_type.value} after "
                f"{self.max_reconnect_attempts} attempts",
                service=self.cluster_type.value,
                details={"last_error": str(last_error) if last_error else None},
            )

    async def disconnect(self) -> None:
        """Disconnect from cluster."""
        async with self._lock:
            if self._adapter and self._state in (
                ConnectionState.CONNECTED,
                ConnectionState.RECONNECTING,
            ):
                try:
                    await self._adapter.disconnect()
                except Exception as e:
                    logger.warning(
                        "disconnect_error",
                        cluster=self.cluster_type.value,
                        error=str(e),
                    )

            self._set_state(ConnectionState.DISCONNECTED)
            self._health_status = HealthStatus.UNKNOWN
            self.metrics.disconnect_count += 1
            self.metrics.last_disconnected_at = datetime.now(UTC)

            logger.info("disconnected", cluster=self.cluster_type.value)

    async def close(self) -> None:
        """Close connection permanently."""
        await self.stop_health_monitoring()
        await self.disconnect()
        self._set_state(ConnectionState.CLOSED)
        self._adapter = None

    async def health_check(self) -> HealthStatus:
        """Perform health check on connection.

        Returns:
            Current health status.
        """
        if not self.is_connected or self._adapter is None:
            self._health_status = HealthStatus.UNHEALTHY
            return self._health_status

        try:
            # Check circuit breaker state
            if self.circuit_breaker.is_open:
                self._health_status = HealthStatus.UNHEALTHY
                return self._health_status

            # Simple connectivity check using adapter's health check method
            is_healthy = await asyncio.wait_for(
                self._adapter.check_api_health(),
                timeout=10.0,
            )

            if is_healthy:
                await self.circuit_breaker.record_success()
                self._health_status = HealthStatus.HEALTHY
            else:
                self._health_status = HealthStatus.DEGRADED

        except TimeoutError:
            self._health_status = HealthStatus.DEGRADED
            await self.circuit_breaker.record_failure(TimeoutError("Health check timeout"))

        except Exception as e:
            self._health_status = HealthStatus.UNHEALTHY
            await self.circuit_breaker.record_failure(e)
            logger.warning(
                "health_check_failed",
                cluster=self.cluster_type.value,
                error=str(e),
            )

        return self._health_status

    async def start_health_monitoring(self) -> None:
        """Start background health monitoring."""
        if self._health_check_task is None or self._health_check_task.done():
            self._health_check_task = asyncio.create_task(self._health_check_loop())

    async def stop_health_monitoring(self) -> None:
        """Stop background health monitoring."""
        if self._health_check_task and not self._health_check_task.done():
            self._health_check_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._health_check_task
            self._health_check_task = None

    async def _health_check_loop(self) -> None:
        """Background health check loop."""
        while True:
            try:
                await asyncio.sleep(self.health_check_interval)
                if self.is_connected:
                    await self.health_check()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(
                    "health_check_loop_error",
                    cluster=self.cluster_type.value,
                    error=str(e),
                )

    def get_status(self) -> dict[str, Any]:
        """Get connection status summary."""
        return {
            "cluster_type": self.cluster_type.value,
            "state": self._state.value,
            "health_status": self._health_status.value,
            "circuit_breaker": self.circuit_breaker.metrics,
            "metrics": self.metrics.to_dict(),
        }
