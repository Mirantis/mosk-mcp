"""Kubernetes adapter using kr8s for async operations.

This module provides an async-first Kubernetes client wrapper that:
- Uses kr8s for all Kubernetes operations
- Supports standard and custom resources (CRDs)
- Provides proper error handling with KubernetesError
- Manages connections with kubeconfig or in-cluster config
- Implements context manager for proper cleanup
- Provides automatic retry with exponential backoff
- Supports connection pooling for high-throughput scenarios
"""

from __future__ import annotations

import asyncio
import contextlib
import random
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, ParamSpec, TypeVar, cast

import kr8s
from kr8s.asyncio.objects import APIObject

from mosk_mcp.core.exceptions import (
    ConfigurationError,
    KubernetesError,
    MoskConnectionError,
    ResourceNotFoundError,
)
from mosk_mcp.observability.logging import get_logger


if TYPE_CHECKING:
    import builtins
    from collections.abc import AsyncGenerator, Callable, Coroutine

    from mosk_mcp.adapters.crd.base import KubernetesResource
    from mosk_mcp.core.config import Settings


logger = get_logger(__name__)

T = TypeVar("T")
P = ParamSpec("P")


# =============================================================================
# Retry Configuration
# =============================================================================


@dataclass
class RetryConfig:
    """Configuration for retry behavior.

    Attributes:
        max_retries: Maximum number of retry attempts.
        base_delay: Base delay in seconds between retries.
        max_delay: Maximum delay in seconds between retries.
        exponential_base: Base for exponential backoff calculation.
        jitter: Whether to add random jitter to delays.
        retryable_exceptions: Tuple of exception types that should trigger retry.
    """

    max_retries: int = 3
    base_delay: float = 0.5
    max_delay: float = 30.0
    exponential_base: float = 2.0
    jitter: bool = True
    retryable_exceptions: tuple[type[Exception], ...] = field(
        default_factory=lambda: (
            ConnectionError,
            TimeoutError,
            OSError,
        )
    )

    def calculate_delay(self, attempt: int) -> float:
        """Calculate delay for a given retry attempt.

        Args:
            attempt: Current retry attempt number (0-indexed).

        Returns:
            Delay in seconds before next retry.
        """
        delay = min(
            self.base_delay * (self.exponential_base**attempt),
            self.max_delay,
        )
        if self.jitter:
            # S311: random.random() is fine for jitter - not cryptographic
            delay = delay * (0.5 + random.random())
        return delay


# Default retry configuration
DEFAULT_RETRY_CONFIG = RetryConfig()


# =============================================================================
# Error Detection Utilities
# =============================================================================


def _is_not_found_error(exc: Exception) -> bool:
    """Check if an exception represents a 'not found' error.

    This provides consistent error detection across all Kubernetes operations,
    avoiding fragile string-based checks scattered throughout the code.

    Args:
        exc: The exception to check.

    Returns:
        True if the exception indicates a resource was not found.
    """
    # Check for kr8s-specific NotFoundError first (most reliable)
    exc_type_name = type(exc).__name__
    if exc_type_name in ("NotFoundError", "ServerError"):
        # kr8s raises ServerError for 404s sometimes
        if "404" in str(exc) or "NotFound" in str(exc):
            return True
        if exc_type_name == "NotFoundError":
            return True

    # Check exception message as fallback
    error_str = str(exc).lower()
    return any(
        pattern in error_str for pattern in ("not found", "notfound", "404", "does not exist")
    )


def _validate_custom_resource_params(
    group: str,
    version: str,
    plural: str,
) -> None:
    """Validate custom resource API parameters.

    Args:
        group: API group (e.g., 'kaas.mirantis.com').
        version: API version (e.g., 'v1alpha1').
        plural: Resource plural name (e.g., 'machines').

    Raises:
        ValueError: If any parameter is invalid.
    """
    # Validate group format (should contain at least one dot for non-core resources,
    # or be empty string for core API group)
    if group and not group.replace("-", "").replace(".", "").replace("_", "").isalnum():
        raise ValueError(
            f"Invalid API group '{group}': must contain only alphanumeric "
            "characters, dots, dashes, and underscores"
        )

    # Validate version format (v1, v1alpha1, v1beta1, v2, etc.)
    if not version:
        raise ValueError("API version cannot be empty")
    valid_version_prefixes = ("v1", "v2", "v3")
    if not any(version.startswith(prefix) for prefix in valid_version_prefixes):
        raise ValueError(
            f"Invalid API version '{version}': must start with v1, v2, or v3 "
            "(e.g., 'v1', 'v1alpha1', 'v1beta1', 'v2')"
        )
    # Version should be alphanumeric after the 'v'
    version_suffix = version[1:]  # Remove leading 'v'
    if not version_suffix.replace("alpha", "").replace("beta", "").isdigit() and version_suffix:
        # Allow patterns like "1", "1alpha1", "1beta2", "2"
        import re

        if not re.match(r"^\d+(alpha\d+|beta\d+)?$", version_suffix):
            raise ValueError(
                f"Invalid API version format '{version}': expected format like "
                "'v1', 'v1alpha1', 'v1beta1', 'v2'"
            )

    # Validate plural (should be lowercase alphanumeric with possible dashes)
    if not plural:
        raise ValueError("Resource plural name cannot be empty")
    if not plural.islower():
        raise ValueError(f"Invalid plural '{plural}': must be lowercase (Kubernetes convention)")
    if not plural.replace("-", "").isalnum():
        raise ValueError(
            f"Invalid plural '{plural}': must contain only lowercase "
            "alphanumeric characters and dashes"
        )


async def with_retry(  # type: ignore[valid-type]
    func: Callable[P, Coroutine[Any, Any, T]],
    *args: P.args,
    config: RetryConfig | None = None,
    operation_name: str = "operation",
    **kwargs: P.kwargs,
) -> T:
    """Execute an async function with retry logic.

    Args:
        func: Async function to execute.
        *args: Positional arguments for the function.
        config: Retry configuration. Uses default if None.
        operation_name: Name of the operation for logging.
        **kwargs: Keyword arguments for the function.

    Returns:
        Result of the function call.

    Raises:
        The last exception if all retries fail.
    """
    config = config or DEFAULT_RETRY_CONFIG
    last_exception: Exception | None = None

    for attempt in range(config.max_retries + 1):
        try:
            return await func(*args, **kwargs)
        except config.retryable_exceptions as e:
            last_exception = e
            if attempt < config.max_retries:
                delay = config.calculate_delay(attempt)
                logger.warning(
                    "kubernetes_operation_retry",
                    operation=operation_name,
                    attempt=attempt + 1,
                    max_retries=config.max_retries,
                    delay=delay,
                    error=str(e),
                    error_type=type(e).__name__,
                )
                await asyncio.sleep(delay)
            else:
                logger.error(
                    "kubernetes_operation_failed_after_retries",
                    operation=operation_name,
                    attempts=attempt + 1,
                    error=str(e),
                    error_type=type(e).__name__,
                )
        except Exception:
            # Non-retryable exception, re-raise immediately
            raise

    # Should not reach here, but raise the last exception if we do
    if last_exception:
        raise last_exception
    raise RuntimeError(f"Unexpected state in retry logic for {operation_name}")


# =============================================================================
# Connection Pool
# =============================================================================


class ConnectionPoolExhausted(Exception):
    """Raised when connection pool cannot provide a connection within timeout.

    Attributes:
        timeout: The timeout value that was exceeded.
        pool_size: Maximum size of the connection pool.
        in_use_count: Number of connections in use when exhaustion occurred.
    """

    def __init__(
        self,
        message: str,
        timeout: float = 0.0,
        pool_size: int = 0,
        in_use_count: int = 0,
    ) -> None:
        super().__init__(message)
        self.timeout = timeout
        self.pool_size = pool_size
        self.in_use_count = in_use_count


class ConnectionPool:
    """Pool of Kubernetes API connections for high-throughput scenarios.

    This class manages a pool of kr8s API connections to support
    concurrent operations without overwhelming the API server.

    Features:
    - Configurable pool size and acquire timeout
    - Connection health checking before returning to caller
    - Fair async waiting using Condition for FIFO scheduling
    - Graceful shutdown with connection cleanup

    Attributes:
        _pool: List of available connections.
        _in_use: Set of connections currently in use.
        _max_size: Maximum number of connections in the pool.
        _kubeconfig_path: Path to kubeconfig file.
        _condition: Condition for fair scheduling and thread-safe operations.
        _acquire_timeout: Timeout for acquiring connections.
    """

    def __init__(
        self,
        max_size: int = 10,
        kubeconfig_path: Path | None = None,
        acquire_timeout: float = 30.0,
        health_check_interval: float = 60.0,
    ) -> None:
        """Initialize the connection pool.

        Args:
            max_size: Maximum number of connections.
            kubeconfig_path: Path to kubeconfig file.
            acquire_timeout: Timeout in seconds for acquiring a connection.
            health_check_interval: Interval for connection health checks.
        """
        self._pool: list[kr8s.asyncio.Api] = []
        self._in_use: set[kr8s.asyncio.Api] = set()
        self._max_size = max_size
        self._kubeconfig_path = kubeconfig_path
        self._closed = False
        self._acquire_timeout = acquire_timeout
        self._health_check_interval = health_check_interval
        # Use Condition for fair scheduling - notify() wakes one waiter at a time
        self._condition = asyncio.Condition()
        self._last_health_check: dict[int, float] = {}  # conn id -> timestamp

    @property
    def size(self) -> int:
        """Get current pool size."""
        return len(self._pool) + len(self._in_use)

    @property
    def available(self) -> int:
        """Get number of available connections."""
        return len(self._pool)

    @property
    def in_use_count(self) -> int:
        """Get number of connections in use."""
        return len(self._in_use)

    async def _create_connection(self) -> kr8s.asyncio.Api:
        """Create a new API connection.

        Returns:
            New kr8s API connection.
        """
        if self._kubeconfig_path:
            return await kr8s.asyncio.api(kubeconfig=str(self._kubeconfig_path))
        return await kr8s.asyncio.api()

    async def _check_connection_health(self, conn: kr8s.asyncio.Api) -> bool:
        """Check if a connection is healthy.

        Args:
            conn: Connection to check.

        Returns:
            True if connection is healthy, False otherwise.

        Note:
            This method has a strict 5-second timeout to prevent deadlock
            when called while holding the connection pool lock.
        """
        conn_id = id(conn)
        now = asyncio.get_event_loop().time()

        # Skip health check if recently checked
        last_check = self._last_health_check.get(conn_id, 0)
        if now - last_check < self._health_check_interval:
            return True

        try:
            # P1 FIX: Reduced timeout from 5s to 2s to prevent blocking
            # A healthy connection should respond in <1s; 2s is generous fallback
            await asyncio.wait_for(conn.version(), timeout=2.0)
            self._last_health_check[conn_id] = now
            return True
        except TimeoutError:
            logger.warning(
                "connection_health_check_timeout",
                connection_id=conn_id,
                timeout_seconds=2.0,
            )
            self._last_health_check.pop(conn_id, None)
            return False
        except Exception as e:
            logger.warning(
                "connection_health_check_failed",
                connection_id=conn_id,
                error=str(e),
            )
            # Remove from health check cache
            self._last_health_check.pop(conn_id, None)
            return False

    async def acquire(self, timeout: float | None = None) -> kr8s.asyncio.Api:
        """Acquire a connection from the pool.

        Args:
            timeout: Override default acquire timeout. None uses default.

        Returns:
            An API connection.

        Raises:
            RuntimeError: If pool is closed.
            ConnectionPoolExhausted: If no connection available within timeout.
        """
        if self._closed:
            raise RuntimeError("Connection pool is closed")

        timeout = timeout if timeout is not None else self._acquire_timeout
        start_time = asyncio.get_event_loop().time()

        async with self._condition:
            while True:
                # Try to get a healthy existing connection
                while self._pool:
                    conn = self._pool.pop()
                    if await self._check_connection_health(conn):
                        self._in_use.add(conn)
                        logger.debug(
                            "connection_pool_acquired",
                            available=len(self._pool),
                            in_use=len(self._in_use),
                        )
                        return conn
                    else:
                        # Discard unhealthy connection
                        logger.info(
                            "connection_pool_discarded_unhealthy",
                            available=len(self._pool),
                        )

                # Create a new connection if under max size
                if self.size < self._max_size:
                    conn = await self._create_connection()
                    self._in_use.add(conn)
                    self._last_health_check[id(conn)] = asyncio.get_event_loop().time()
                    logger.debug(
                        "connection_pool_created",
                        available=len(self._pool),
                        in_use=len(self._in_use),
                    )
                    return conn

                # Check timeout
                elapsed = asyncio.get_event_loop().time() - start_time
                remaining = timeout - elapsed
                if remaining <= 0:
                    logger.error(
                        "connection_pool_exhausted",
                        timeout=timeout,
                        pool_size=self.size,
                        in_use=self.in_use_count,
                    )
                    raise ConnectionPoolExhausted(
                        f"Could not acquire connection within {timeout}s. "
                        f"Pool size: {self.size}, in use: {self.in_use_count}",
                        timeout=timeout,
                        pool_size=self.size,
                        in_use_count=self.in_use_count,
                    )

                # Wait for a connection to become available (fair scheduling)
                # Condition.wait() releases lock, waits for notify, reacquires lock
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(
                        self._condition.wait(),
                        timeout=min(remaining, 1.0),  # Check every 1s max
                    )

    async def release(self, conn: kr8s.asyncio.Api) -> None:
        """Release a connection back to the pool.

        Args:
            conn: Connection to release.
        """
        async with self._condition:
            if conn in self._in_use:
                self._in_use.discard(conn)
                if not self._closed:
                    self._pool.append(conn)
                    # Notify ONE waiting acquirer (fair scheduling)
                    self._condition.notify()
                logger.debug(
                    "connection_pool_released",
                    available=len(self._pool),
                    in_use=len(self._in_use),
                )

    async def close(self) -> None:
        """Close all connections in the pool properly.

        This method ensures all connections (both available and in-use) are
        properly closed to prevent resource leaks.
        """
        async with self._condition:
            self._closed = True

            # Collect all connections to close
            all_connections = list(self._pool) + list(self._in_use)
            close_count = len(all_connections)

            # Close each connection properly (kr8s >= 0.17 provides close())
            for conn in all_connections:
                try:
                    await conn.close()
                except Exception as e:
                    # Log but don't fail - we want to close all connections
                    logger.warning(
                        "connection_close_error",
                        error=str(e),
                        error_type=type(e).__name__,
                    )

            # Clear pool state
            self._pool.clear()
            self._in_use.clear()
            self._last_health_check.clear()

            # Wake up ALL waiting acquirers so they can fail fast
            self._condition.notify_all()

            logger.debug(
                "connection_pool_closed",
                connections_closed=close_count,
            )

    @asynccontextmanager
    async def connection(
        self, timeout: float | None = None
    ) -> AsyncGenerator[kr8s.asyncio.Api, None]:
        """Context manager for acquiring and releasing connections.

        Args:
            timeout: Override default acquire timeout.

        Yields:
            An API connection.
        """
        conn = await self.acquire(timeout=timeout)
        try:
            yield conn
        finally:
            await self.release(conn)

    # Add async context manager support for proper cleanup
    async def __aenter__(self) -> ConnectionPool:
        """Async context manager entry."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        """Async context manager exit - ensures connections are closed."""
        await self.close()

    def __del__(self) -> None:
        """Destructor - warn if pool wasn't properly closed.

        Log warning if connections weren't cleaned up properly.
        This helps detect resource leaks in development.
        """
        if not self._closed and (self._pool or self._in_use):
            # Use print since logger may not be available during shutdown
            import sys

            with contextlib.suppress(Exception):
                print(
                    f"WARNING: ConnectionPool garbage collected with "
                    f"{len(self._pool)} available and {len(self._in_use)} in-use "
                    f"connections. Call close() explicitly to avoid resource leaks.",
                    file=sys.stderr,
                )


class KubernetesAdapter:
    """Async Kubernetes client adapter using kr8s.

    This adapter provides a high-level async interface for Kubernetes operations,
    supporting both standard resources and MOSK custom resources.

    Attributes:
        _api: The kr8s API client.
        _kubeconfig_path: Path to kubeconfig file.
        _namespace: Default namespace for operations.
        _connected: Whether the client is connected.

    Example:
        async with KubernetesAdapter.from_settings(settings) as k8s:
            machines = await k8s.list_custom_resources(
                group="kaas.mirantis.com",
                version="v1alpha1",
                plural="machines",
            )
    """

    # CRD mappings for MOSK resources
    CRD_MAPPINGS: ClassVar[dict[str, dict[str, str]]] = {
        # Machine and Node Management
        "machines": {
            "group": "cluster.k8s.io",
            "version": "v1alpha1",
            "plural": "machines",
        },
        "baremetalhostinventories": {
            "group": "kaas.mirantis.com",
            "version": "v1alpha1",
            "plural": "baremetalhostinventories",
        },
        "baremetalhostprofiles": {
            "group": "metal3.io",
            "version": "v1alpha1",
            "plural": "baremetalhostprofiles",
        },
        # Networking
        "ipamhosts": {
            "group": "ipam.mirantis.com",
            "version": "v1alpha1",
            "plural": "ipamhosts",
        },
        "l2templates": {
            "group": "ipam.mirantis.com",
            "version": "v1alpha1",
            "plural": "l2templates",
        },
        # OpenStack
        "openstackdeployments": {
            "group": "lcm.mirantis.com",
            "version": "v1alpha1",
            "plural": "openstackdeployments",
        },
        "openstackdeploymentstatus": {
            "group": "lcm.mirantis.com",
            "version": "v1alpha1",
            "plural": "openstackdeploymentstatus",
        },
        # Ceph Storage
        "kaascephoperationrequests": {
            "group": "kaas.mirantis.com",
            "version": "v1alpha1",
            "plural": "kaascephoperationrequests",
        },
        "miracephs": {
            "group": "lcm.mirantis.com",
            "version": "v1alpha1",
            "plural": "miracephs",
        },
        "kaascephclusters": {
            "group": "kaas.mirantis.com",
            "version": "v1alpha1",
            "plural": "kaascephclusters",
        },
        # Maintenance and Lifecycle
        "nodemaintenancerequests": {
            "group": "lcm.mirantis.com",
            "version": "v1alpha1",
            "plural": "nodemaintenancerequests",
        },
        "clustermaintenancerequests": {
            "group": "lcm.mirantis.com",
            "version": "v1alpha1",
            "plural": "clustermaintenancerequests",
        },
        "gracefulrebootrequests": {
            "group": "kaas.mirantis.com",
            "version": "v1alpha1",
            "plural": "gracefulrebootrequests",
        },
        "clusterupdateplans": {
            "group": "kaas.mirantis.com",
            "version": "v1alpha1",
            "plural": "clusterupdateplans",
        },
        # Cluster resources (for discovering MOSK cluster namespace)
        "clusters": {
            "group": "cluster.k8s.io",
            "version": "v1alpha1",
            "plural": "clusters",
        },
        # Cluster releases (available MOSK platform releases)
        "clusterreleases": {
            "group": "kaas.mirantis.com",
            "version": "v1alpha1",
            "plural": "clusterreleases",
        },
        # Cluster upgrade status tracking
        "clusterupgradestatuses": {
            "group": "kaas.mirantis.com",
            "version": "v1alpha1",
            "plural": "clusterupgradestatuses",
        },
        # Machine upgrade status tracking
        "machineupgradestatuses": {
            "group": "kaas.mirantis.com",
            "version": "v1alpha1",
            "plural": "machineupgradestatuses",
        },
        # LCM Machine CRs (for tracking machine LCM state)
        "lcmmachines": {
            "group": "lcm.mirantis.com",
            "version": "v1alpha1",
            "plural": "lcmmachines",
        },
        # Helm Bundle CRs (for tracking helm chart upgrades)
        "helmbundles": {
            "group": "lcm.mirantis.com",
            "version": "v1alpha1",
            "plural": "helmbundles",
        },
        # LCM Cluster Upgrade Status (for detailed LCM tracking)
        "lcmclusterupgradestatuses": {
            "group": "lcm.mirantis.com",
            "version": "v1alpha1",
            "plural": "lcmclusterupgradestatuses",
        },
        # KaasRelease (MCC platform release with supported cluster releases)
        "kaasreleases": {
            "group": "kaas.mirantis.com",
            "version": "v1alpha1",
            "plural": "kaasreleases",
        },
    }

    # Cache for discovered MOSK cluster info with TTL support.
    # Each entry contains 'data' (the cached result) and 'timestamp' (when cached).
    _mosk_cluster_cache: ClassVar[dict[str, dict[str, Any]]] = {}

    # Cache TTL in seconds (5 minutes default)
    _CACHE_TTL_SECONDS: ClassVar[int] = 300

    def __init__(
        self,
        kubeconfig_path: Path | str | None = None,
        namespace: str = "default",
        retry_config: RetryConfig | None = None,
        enable_retry: bool = True,
    ) -> None:
        """Initialize the Kubernetes adapter.

        Args:
            kubeconfig_path: Path to kubeconfig file. If None, uses in-cluster config.
            namespace: Default namespace for operations.
            retry_config: Configuration for retry behavior.
            enable_retry: Whether to enable automatic retry for transient failures.
        """
        self._kubeconfig_path = Path(kubeconfig_path) if kubeconfig_path else None
        self._namespace = namespace
        self._api: kr8s.asyncio.Api | None = None
        self._connected = False
        self._retry_config = retry_config or DEFAULT_RETRY_CONFIG
        self._enable_retry = enable_retry

    @classmethod
    def from_settings(cls, settings: Settings) -> KubernetesAdapter:
        """Create a KubernetesAdapter from application settings.

        This is a convenience factory method that extracts relevant
        configuration from the Settings object.

        Args:
            settings: Application settings containing Kubernetes configuration.

        Returns:
            Configured KubernetesAdapter instance (not yet connected).

        Example:
            adapter = KubernetesAdapter.from_settings(settings)
            await adapter.connect()
        """
        return cls(
            namespace=settings.kubernetes_namespace,
            retry_config=RetryConfig(
                max_retries=settings.max_retries,
            ),
        )

    async def connect(self) -> None:
        """Establish connection to Kubernetes cluster.

        Raises:
            ConnectionError: If connection fails.
            ConfigurationError: If kubeconfig is invalid.
        """
        if self._connected:
            return

        logger.debug(
            "connecting_to_kubernetes",
            kubeconfig=str(self._kubeconfig_path) if self._kubeconfig_path else "in-cluster",
        )

        try:
            if self._kubeconfig_path:
                if not self._kubeconfig_path.exists():
                    raise ConfigurationError(
                        f"Kubeconfig file not found: {self._kubeconfig_path}",
                        config_key="kubeconfig_path",
                    )
                self._api = await kr8s.asyncio.api(
                    kubeconfig=str(self._kubeconfig_path),
                )
            else:
                # Use in-cluster config or default kubeconfig
                self._api = await kr8s.asyncio.api()

            # Verify connection by attempting to get server version
            await self._api.version()
            self._connected = True
            logger.info("kubernetes_connected")

        except FileNotFoundError as e:
            raise ConfigurationError(
                f"Kubeconfig file not found: {e}",
                config_key="kubeconfig_path",
            ) from e
        except Exception as e:
            raise MoskConnectionError(
                f"Failed to connect to Kubernetes cluster: {e}",
                service="kubernetes",
            ) from e

    async def disconnect(self) -> None:
        """Close connection to Kubernetes cluster.

        Performs cleanup of the kr8s API client. While kr8s doesn't have
        an explicit close method, we ensure proper state reset and attempt
        to close any underlying httpx sessions if available.
        """
        if self._api:
            try:
                # kr8s >= 0.17 provides async_close() for proper connection cleanup.
                # This prevents resource leaks for long-running processes.
                await self._api.async_close()
            except Exception as e:
                logger.debug(
                    "kr8s_async_close_failed",
                    error=str(e),
                    error_type=type(e).__name__,
                )
            finally:
                self._api = None
        self._connected = False
        logger.debug("kubernetes_disconnected")

    async def __aenter__(self) -> KubernetesAdapter:
        """Async context manager entry."""
        await self.connect()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        """Async context manager exit."""
        await self.disconnect()

    def _ensure_connected(self) -> None:
        """Ensure the adapter is connected.

        Raises:
            MoskConnectionError: If not connected.
        """
        if not self._connected or self._api is None:
            raise MoskConnectionError(
                "Kubernetes adapter not connected. Call connect() first.",
                service="kubernetes",
            )

    # Mapping of known plural names to their kind (CamelCase)
    # kr8s requires the kind, not the plural, for new_class()
    _PLURAL_TO_KIND_MAP: ClassVar[dict[str, str]] = {
        # MOSK/OpenStack CRDs
        "openstackdeployments": "OpenStackDeployment",
        "openstackdeploymentstatus": "OpenStackDeploymentStatus",
        # Ceph CRDs - Mira (lcm.mirantis.com)
        "miracephs": "MiraCeph",
        "miracephhealths": "MiraCephHealth",
        "miracephlogs": "MiraCephLog",
        "miracephmaintenances": "MiraCephMaintenance",
        "miracephnodedisables": "MiraCephNodeDisable",
        "miracephsecrets": "MiraCephSecret",
        "cephosdremoverequests": "CephOsdRemoveRequest",
        "cephperftestrequests": "CephPerfTestRequest",
        "cephsharerequests": "CephShareRequest",
        # Ceph CRDs - Rook (ceph.rook.io)
        "cephclusters": "CephCluster",
        "cephblockpools": "CephBlockPool",
        "cephfilesystems": "CephFilesystem",
        "cephobjectstores": "CephObjectStore",
        "cephobjectstoreusers": "CephObjectStoreUser",
        "cephnfses": "CephNFS",
        "cephclients": "CephClient",
        # KAAS/MCC CRDs
        "machines": "Machine",
        "clusters": "Cluster",
        "baremetalhostinventories": "BareMetalHostInventory",
        "baremetalhostprofiles": "BareMetalHostProfile",
        "baremetalhosts": "BareMetalHost",
        "baremetalhostcredentials": "BareMetalHostCredential",
        "l2templates": "L2Template",
        "ipamhosts": "IpamHost",
        "subnets": "Subnet",
        "subnetpools": "SubnetPool",
        "ipaddrs": "IPaddr",
        "ipamclusters": "IpamCluster",
        "clusterreleases": "ClusterRelease",
        "clusterdeploymentstatuses": "ClusterDeploymentStatus",
        "clusteroidcconfigurations": "ClusterOIDCConfiguration",
        "clusterpollstatuses": "ClusterPollStatus",
        "clusterupgradestatuses": "ClusterUpgradeStatus",
        "machinedeploymentstatuses": "MachineDeploymentStatus",
        "machinepollstatuses": "MachinePollStatus",
        # Maintenance CRDs
        "nodemaintenancerequests": "NodeMaintenanceRequest",
        "clustermaintenancerequests": "ClusterMaintenanceRequest",
        "clusterworkloadlocks": "ClusterWorkloadLock",
        "gracefulrebootrequests": "GracefulRebootRequest",
        # Update CRDs
        "clusterupdateplans": "ClusterUpdatePlan",
        "clusterupdates": "ClusterUpdate",
        # KaaS Release CRDs
        "kaasreleases": "KaaSRelease",
    }

    def _plural_to_kind(self, plural: str) -> str:
        """Convert a plural resource name to its kind (CamelCase).

        Args:
            plural: Resource plural name (e.g., 'machines', 'openstackdeployments').

        Returns:
            The kind in CamelCase (e.g., 'Machine', 'OpenStackDeployment').
            If not in the mapping, returns a basic conversion (title case).
        """
        # Check known mappings first
        if plural.lower() in self._PLURAL_TO_KIND_MAP:
            return self._PLURAL_TO_KIND_MAP[plural.lower()]

        # Basic fallback: remove trailing 's' and title case
        # This is a naive approach but covers simple cases
        kind = plural.rstrip("s")
        # Handle 'ies' -> 'y' (e.g., 'policies' -> 'policy')
        if kind.endswith("ie"):
            kind = kind[:-2] + "y"
        return kind.title()

    @property
    def api(self) -> kr8s.asyncio.Api:
        """Get the kr8s API client.

        Returns:
            The kr8s API client.

        Raises:
            MoskConnectionError: If not connected.
        """
        self._ensure_connected()
        # _ensure_connected guarantees _api is not None when connected
        # Using cast for type safety instead of assertion (which can be disabled with -O)
        if self._api is None:
            raise MoskConnectionError(
                "Kubernetes API client is None after connection check",
                service="kubernetes",
            )
        return self._api

    def _resolve_namespace(self, namespace: str | None) -> str:
        """Resolve the namespace to use for an operation.

        This helper consolidates the common pattern of falling back to the
        default namespace when none is specified.

        Args:
            namespace: Explicit namespace, or None to use default.

        Returns:
            The namespace to use (either provided or default).
        """
        return namespace or self._namespace

    @property
    def namespace(self) -> str:
        """Get the default namespace.

        Returns:
            Default namespace.
        """
        return self._namespace

    @property
    def is_connected(self) -> bool:
        """Check if connected to cluster.

        Returns:
            True if connected.
        """
        return self._connected

    # =========================================================================
    # Health and Info Operations
    # =========================================================================

    async def check_health(self) -> dict[str, Any]:
        """Check Kubernetes cluster health.

        Returns:
            Dictionary with health status and version info.

        Raises:
            KubernetesError: If health check fails.
        """
        self._ensure_connected()

        try:
            version_info = await self.api.version()
            return {
                "status": "healthy",
                "server_version": version_info.get("gitVersion", "unknown"),
                "platform": version_info.get("platform", "unknown"),
                "go_version": version_info.get("goVersion", "unknown"),
            }
        except Exception as e:
            logger.error("kubernetes_health_check_failed", error=str(e))
            return {
                "status": "unhealthy",
                "error": str(e),
            }

    async def get_server_version(self) -> str:
        """Get Kubernetes server version.

        Returns:
            Server version string.

        Raises:
            KubernetesError: If operation fails.
        """
        self._ensure_connected()

        try:
            version_info = await self.api.version()
            return cast("str", version_info.get("gitVersion", "unknown"))
        except Exception as e:
            raise KubernetesError(
                f"Failed to get server version: {e}",
                operation="get_version",
            ) from e

    async def check_connectivity(self) -> bool:
        """Check if the adapter can connect to Kubernetes API.

        This method attempts to connect if not already connected,
        and verifies connectivity by checking the server version.

        Returns:
            True if connected and API is responsive, False otherwise.
        """
        try:
            if not self._connected:
                await self.connect()

            # Verify connectivity by getting server version
            await self.api.version()
            return True

        except Exception as e:
            logger.debug(
                "kubernetes_connectivity_check_failed",
                error=str(e),
                error_type=type(e).__name__,
            )
            return False

    async def check_api_health(self) -> bool:
        """Check if the Kubernetes API server is healthy.

        Performs a health check by verifying:
        1. API server is reachable
        2. Can retrieve server version

        Returns:
            True if API is healthy, False otherwise.

        Raises:
            KubernetesError: If health check fails.
        """
        self._ensure_connected()

        try:
            # Verify API health by getting server version
            await self.api.version()
            return True

        except Exception as e:
            raise KubernetesError(
                f"API server health check failed: {e}",
                operation="health_check",
            ) from e

    # =========================================================================
    # Generic Resource Operations
    # =========================================================================

    # Cluster-scoped resource kinds that don't use namespaces
    CLUSTER_SCOPED_KINDS = frozenset(
        {
            "Node",
            "Namespace",
            "PersistentVolume",
            "ClusterRole",
            "ClusterRoleBinding",
            "StorageClass",
            "IngressClass",
            "PriorityClass",
            "RuntimeClass",
            "VolumeAttachment",
            "CSIDriver",
            "CSINode",
            "CustomResourceDefinition",
            "APIService",
            "MutatingWebhookConfiguration",
            "ValidatingWebhookConfiguration",
        }
    )

    async def get(
        self,
        kind: str,
        name: str,
        namespace: str | None = None,
    ) -> dict[str, Any]:
        """Get a Kubernetes resource.

        Args:
            kind: Resource kind (e.g., 'Pod', 'ConfigMap', 'Node').
            name: Resource name.
            namespace: Resource namespace. Uses default if None.
                For cluster-scoped resources (Node, Namespace, etc.),
                this parameter is ignored.

        Returns:
            Resource as dictionary.

        Raises:
            ResourceNotFoundError: If resource doesn't exist.
            KubernetesError: If operation fails.
        """
        self._ensure_connected()

        # Check if this is a cluster-scoped resource
        is_cluster_scoped = kind in self.CLUSTER_SCOPED_KINDS

        ns = kr8s.ALL if is_cluster_scoped else namespace or self._namespace

        logger.debug(
            "kubernetes_get",
            kind=kind,
            name=name,
            namespace=str(ns) if not is_cluster_scoped else "cluster-scoped",
        )

        try:
            # kr8s.asyncio.get returns an async generator even for single resources
            resources_gen = kr8s.asyncio.get(
                kind,
                name,
                namespace=ns,
                api=self.api,
            )

            # Collect the first result from the generator
            resource = None
            async for r in resources_gen:
                resource = r
                break  # We only want the first (and should be only) result

            if resource is None:
                resource_id = name if is_cluster_scoped else f"{ns}/{name}"
                raise ResourceNotFoundError(
                    f"{kind} '{name}' not found"
                    + (f" in namespace '{ns}'" if not is_cluster_scoped else ""),
                    resource_type=kind,
                    resource_id=resource_id,
                )

            return cast("dict[str, Any]", resource.raw)

        except ResourceNotFoundError:
            raise
        except kr8s.NotFoundError as e:
            resource_id = name if is_cluster_scoped else f"{ns}/{name}"
            raise ResourceNotFoundError(
                f"{kind} '{name}' not found"
                + (f" in namespace '{ns}'" if not is_cluster_scoped else ""),
                resource_type=kind,
                resource_id=resource_id,
            ) from e
        except Exception as e:
            raise KubernetesError(
                f"Failed to get {kind} '{name}': {e}",
                operation="get",
                resource_kind=kind,
                resource_name=name,
                namespace=str(ns) if not is_cluster_scoped else None,
            ) from e

    async def list(
        self,
        kind: str,
        namespace: str | None = None,
        label_selector: str | None = None,
        field_selector: str | None = None,
    ) -> builtins.list[dict[str, Any]]:
        """List Kubernetes resources.

        Args:
            kind: Resource kind (e.g., 'Pod', 'ConfigMap', 'Node').
            namespace: Resource namespace. Uses default if None. Use '*' for all namespaces.
                For cluster-scoped resources (Node, Namespace, etc.),
                this parameter is ignored and all resources are listed.
            label_selector: Label selector string (e.g., 'app=nginx').
            field_selector: Field selector string (e.g., 'status.phase=Running').

        Returns:
            List of resources as dictionaries.

        Raises:
            KubernetesError: If operation fails.
        """
        self._ensure_connected()

        # Check if this is a cluster-scoped resource
        is_cluster_scoped = kind in self.CLUSTER_SCOPED_KINDS

        ns = kr8s.ALL if is_cluster_scoped or namespace == "*" else namespace or self._namespace

        logger.debug(
            "kubernetes_list",
            kind=kind,
            namespace=str(ns) if not is_cluster_scoped else "cluster-scoped",
            label_selector=label_selector,
            field_selector=field_selector,
        )

        try:
            # kr8s.asyncio.get returns an async generator when listing resources
            resources_gen = kr8s.asyncio.get(
                kind,
                namespace=ns,
                label_selector=label_selector,
                field_selector=field_selector,
                api=self.api,
            )

            # Collect all resources from the async generator
            resources = []
            async for r in resources_gen:
                resources.append(r.raw)

            return resources

        except Exception as e:
            raise KubernetesError(
                f"Failed to list {kind}: {e}",
                operation="list",
                resource_kind=kind,
                namespace=str(ns) if not is_cluster_scoped else None,
            ) from e

    async def create(
        self,
        resource: dict[str, Any],
        namespace: str | None = None,
    ) -> dict[str, Any]:
        """Create a Kubernetes resource.

        Args:
            resource: Resource definition as dictionary.
            namespace: Override namespace. Uses resource metadata or default.

        Returns:
            Created resource as dictionary.

        Raises:
            KubernetesError: If operation fails.
        """
        self._ensure_connected()

        kind = resource.get("kind", "Unknown")
        name = resource.get("metadata", {}).get("name", "unknown")
        ns = namespace or resource.get("metadata", {}).get("namespace") or self._namespace

        logger.debug(
            "kubernetes_create",
            kind=kind,
            name=name,
            namespace=ns,
        )

        try:
            # Create API object from dict
            obj = APIObject(resource, api=self.api)
            await obj.create()

            logger.info(
                "kubernetes_resource_created",
                kind=kind,
                name=name,
                namespace=ns,
            )
            return obj.raw

        except Exception as e:
            error_msg = str(e)
            status_code = None

            if hasattr(e, "status"):
                status_code = getattr(e, "status", None)

            raise KubernetesError(
                f"Failed to create {kind} '{name}': {error_msg}",
                operation="create",
                resource_kind=kind,
                resource_name=name,
                namespace=ns,
                status_code=status_code,
            ) from e

    async def patch(
        self,
        kind: str,
        name: str,
        patch: dict[str, Any],
        namespace: str | None = None,
        patch_type: str = "merge",
    ) -> dict[str, Any]:
        """Patch a Kubernetes resource.

        Args:
            kind: Resource kind.
            name: Resource name.
            patch: Patch data.
            namespace: Resource namespace. Uses default if None.
            patch_type: Type of patch ('merge', 'strategic', 'json').

        Returns:
            Patched resource as dictionary.

        Raises:
            ResourceNotFoundError: If resource doesn't exist.
            KubernetesError: If operation fails.
        """
        self._ensure_connected()
        ns = namespace or self._namespace

        logger.debug(
            "kubernetes_patch",
            kind=kind,
            name=name,
            namespace=ns,
            patch_type=patch_type,
        )

        try:
            resource = await kr8s.asyncio.get(
                kind,
                name,
                namespace=ns,
                api=self.api,
            )

            if resource is None:
                raise ResourceNotFoundError(
                    f"{kind} '{name}' not found in namespace '{ns}'",
                    resource_type=kind,
                    resource_id=f"{ns}/{name}",
                )

            await resource.patch(patch, type=patch_type)

            logger.info(
                "kubernetes_resource_patched",
                kind=kind,
                name=name,
                namespace=ns,
            )
            return cast("dict[str, Any]", resource.raw)

        except ResourceNotFoundError:
            raise
        except kr8s.NotFoundError as e:
            raise ResourceNotFoundError(
                f"{kind} '{name}' not found in namespace '{ns}'",
                resource_type=kind,
                resource_id=f"{ns}/{name}",
            ) from e
        except Exception as e:
            raise KubernetesError(
                f"Failed to patch {kind} '{name}': {e}",
                operation="patch",
                resource_kind=kind,
                resource_name=name,
                namespace=ns,
            ) from e

    async def delete(
        self,
        kind: str,
        name: str,
        namespace: str | None = None,
        grace_period_seconds: int | None = None,
    ) -> None:
        """Delete a Kubernetes resource.

        Args:
            kind: Resource kind.
            name: Resource name.
            namespace: Resource namespace. Uses default if None.
            grace_period_seconds: Deletion grace period.

        Raises:
            ResourceNotFoundError: If resource doesn't exist.
            KubernetesError: If operation fails.
        """
        self._ensure_connected()
        ns = self._resolve_namespace(namespace)

        logger.debug(
            "kubernetes_delete",
            kind=kind,
            name=name,
            namespace=ns,
        )

        try:
            resource = await kr8s.asyncio.get(
                kind,
                name,
                namespace=ns,
                api=self.api,
            )

            if resource is None:
                raise ResourceNotFoundError(
                    f"{kind} '{name}' not found in namespace '{ns}'",
                    resource_type=kind,
                    resource_id=f"{ns}/{name}",
                )

            delete_options = {}
            if grace_period_seconds is not None:
                delete_options["grace_period_seconds"] = grace_period_seconds

            await resource.delete(**delete_options)

            logger.info(
                "kubernetes_resource_deleted",
                kind=kind,
                name=name,
                namespace=ns,
            )

        except ResourceNotFoundError:
            raise
        except kr8s.NotFoundError as e:
            raise ResourceNotFoundError(
                f"{kind} '{name}' not found in namespace '{ns}'",
                resource_type=kind,
                resource_id=f"{ns}/{name}",
            ) from e
        except Exception as e:
            raise KubernetesError(
                f"Failed to delete {kind} '{name}': {e}",
                operation="delete",
                resource_kind=kind,
                resource_name=name,
                namespace=ns,
            ) from e

    # =========================================================================
    # Custom Resource (CRD) Operations
    # =========================================================================

    async def get_custom_resource(
        self,
        group: str,
        version: str,
        plural: str,
        name: str,
        namespace: str | None = None,
        kind: str | None = None,
        namespaced: bool = True,
    ) -> dict[str, Any]:
        """Get a custom resource.

        Args:
            group: API group (e.g., 'kaas.mirantis.com').
            version: API version (e.g., 'v1alpha1').
            plural: Resource plural name (e.g., 'machines').
            name: Resource name.
            namespace: Resource namespace. Uses default if None.
                       Ignored for cluster-scoped resources (namespaced=False).
            kind: Resource kind (CamelCase, e.g., 'Machine'). If not provided,
                  will be derived from plural (e.g., 'machines' -> 'Machine').
            namespaced: Whether the resource is namespaced (True) or cluster-scoped (False).

        Returns:
            Resource as dictionary.

        Raises:
            ResourceNotFoundError: If resource doesn't exist.
            ValueError: If group, version, or plural are invalid.
            KubernetesError: If operation fails.
        """
        # Validate input parameters
        _validate_custom_resource_params(group, version, plural)

        self._ensure_connected()
        # For cluster-scoped resources, namespace is ignored
        ns = None if not namespaced else namespace or self._namespace
        api_version = f"{group}/{version}"

        # Derive kind from plural if not provided
        # e.g., 'machines' -> 'Machine', 'openstackdeployments' -> 'OpenStackDeployment'
        if kind is None:
            kind = self._plural_to_kind(plural)

        logger.debug(
            "kubernetes_get_custom_resource",
            api_version=api_version,
            kind=kind,
            plural=plural,
            name=name,
            namespace=ns if ns else "(cluster-scoped)",
            namespaced=namespaced,
        )

        try:
            # Create a custom resource class dynamically
            resource_class = kr8s.asyncio.objects.new_class(
                kind=kind,
                version=api_version,
                namespaced=namespaced,
                plural=plural,  # Pass the plural to avoid kr8s naive pluralization
            )

            # For cluster-scoped resources, don't pass namespace
            get_kwargs: dict[str, Any] = {"api": self.api}
            if namespaced and ns is not None:
                get_kwargs["namespace"] = ns

            resources = await resource_class.get(name, **get_kwargs)

            if resources is None:
                resource_id = f"{ns}/{name}" if ns else name
                raise ResourceNotFoundError(
                    f"{kind} '{name}' not found" + (f" in namespace '{ns}'" if ns else ""),
                    resource_type=kind,
                    resource_id=resource_id,
                )

            return resources.raw

        except ResourceNotFoundError:
            raise
        except Exception as e:
            resource_id = f"{ns}/{name}" if ns else name
            if _is_not_found_error(e):
                raise ResourceNotFoundError(
                    f"{kind} '{name}' not found" + (f" in namespace '{ns}'" if ns else ""),
                    resource_type=kind,
                    resource_id=resource_id,
                ) from e
            raise KubernetesError(
                f"Failed to get {kind} '{name}': {e}",
                operation="get",
                resource_kind=kind,
                resource_name=name,
                namespace=ns if ns else "(cluster-scoped)",
            ) from e

    async def list_custom_resources(
        self,
        group: str,
        version: str,
        plural: str,
        namespace: str | None = None,
        label_selector: str | None = None,
        kind: str | None = None,
        namespaced: bool = True,
    ) -> builtins.list[dict[str, Any]]:
        """List custom resources.

        Args:
            group: API group (e.g., 'kaas.mirantis.com').
            version: API version (e.g., 'v1alpha1').
            plural: Resource plural name (e.g., 'machines').
            namespace: Resource namespace. Uses default if None. Use '*' for all namespaces.
                       Ignored for cluster-scoped resources (namespaced=False).
            label_selector: Label selector string.
            kind: Resource kind (CamelCase, e.g., 'Machine'). If not provided,
                  will be derived from plural.
            namespaced: Whether the resource is namespaced (True) or cluster-scoped (False).

        Returns:
            List of resources as dictionaries.

        Raises:
            ValueError: If group, version, or plural are invalid.
            KubernetesError: If operation fails.
        """
        # Validate input parameters
        _validate_custom_resource_params(group, version, plural)

        self._ensure_connected()
        api_version = f"{group}/{version}"

        # For cluster-scoped resources, namespace is ignored
        if not namespaced:
            ns = None
        elif namespace == "*":
            ns = kr8s.ALL
        else:
            ns = namespace or self._namespace

        # Derive kind from plural if not provided
        if kind is None:
            kind = self._plural_to_kind(plural)

        logger.debug(
            "kubernetes_list_custom_resources",
            api_version=api_version,
            kind=kind,
            plural=plural,
            namespace=str(ns) if ns else "(cluster-scoped)",
            label_selector=label_selector,
            namespaced=namespaced,
        )

        try:
            resource_class = kr8s.asyncio.objects.new_class(
                kind=kind,
                version=api_version,
                namespaced=namespaced,
                plural=plural,  # Pass the plural to avoid kr8s naive pluralization
            )

            # kr8s list() returns an async generator, not a coroutine
            # Collect all resources from the async generator
            resources: list[dict[str, Any]] = []
            # For cluster-scoped resources, don't pass namespace
            list_kwargs: dict[str, Any] = {
                "label_selector": label_selector,
                "api": self.api,
            }
            if namespaced and ns is not None:
                list_kwargs["namespace"] = ns

            async for r in resource_class.list(**list_kwargs):
                resources.append(r.raw)

            return resources

        except Exception as e:
            raise KubernetesError(
                f"Failed to list {kind}: {e}",
                operation="list",
                resource_kind=kind,
                namespace=str(ns) if ns else "(cluster-scoped)",
            ) from e

    async def create_custom_resource(
        self,
        group: str,
        version: str,
        plural: str,
        resource: dict[str, Any],
        namespace: str | None = None,
    ) -> dict[str, Any]:
        """Create a custom resource.

        Args:
            group: API group.
            version: API version.
            plural: Resource plural name.
            resource: Resource definition as dictionary.
            namespace: Resource namespace. Uses resource metadata or default.

        Returns:
            Created resource as dictionary.

        Raises:
            KubernetesError: If operation fails.
        """
        self._ensure_connected()
        api_version = f"{group}/{version}"
        name = resource.get("metadata", {}).get("name", "unknown")
        ns = namespace or resource.get("metadata", {}).get("namespace") or self._namespace

        logger.debug(
            "kubernetes_create_custom_resource",
            api_version=api_version,
            plural=plural,
            name=name,
            namespace=ns,
        )

        try:
            # Ensure apiVersion is set
            if "apiVersion" not in resource:
                resource["apiVersion"] = api_version

            resource_class = kr8s.asyncio.objects.new_class(
                kind=plural,
                version=api_version,
                namespaced=True,
            )

            obj = resource_class(resource, api=self.api)
            await obj.create()

            logger.info(
                "kubernetes_custom_resource_created",
                api_version=api_version,
                plural=plural,
                name=name,
                namespace=ns,
            )
            return obj.raw

        except Exception as e:
            raise KubernetesError(
                f"Failed to create {plural} '{name}': {e}",
                operation="create",
                resource_kind=plural,
                resource_name=name,
                namespace=ns,
            ) from e

    async def patch_custom_resource(
        self,
        group: str,
        version: str,
        plural: str,
        name: str,
        patch: dict[str, Any] | builtins.list[dict[str, Any]],
        namespace: str | None = None,
        patch_type: str = "merge",
        kind: str | None = None,
    ) -> dict[str, Any]:
        """Patch a custom resource.

        Args:
            group: API group.
            version: API version.
            plural: Resource plural name.
            name: Resource name.
            patch: Patch data (dict for merge/strategic, list for json patch).
            namespace: Resource namespace. Uses default if None.
            patch_type: Type of patch ('merge', 'strategic', 'json').
            kind: Resource kind (CamelCase, e.g., 'Machine'). If not provided,
                  will be derived from plural (e.g., 'machines' -> 'Machine').

        Returns:
            Patched resource as dictionary.

        Raises:
            ResourceNotFoundError: If resource doesn't exist.
            KubernetesError: If operation fails.
        """
        self._ensure_connected()
        api_version = f"{group}/{version}"
        ns = namespace or self._namespace

        # Derive kind from plural if not provided
        # e.g., 'machines' -> 'Machine', 'openstackdeployments' -> 'OpenStackDeployment'
        if kind is None:
            kind = self._plural_to_kind(plural)

        logger.debug(
            "kubernetes_patch_custom_resource",
            api_version=api_version,
            kind=kind,
            plural=plural,
            name=name,
            namespace=ns,
            patch_type=patch_type,
        )

        try:
            resource_class = kr8s.asyncio.objects.new_class(
                kind=kind,
                version=api_version,
                namespaced=True,
                plural=plural,  # Pass the plural to avoid kr8s naive pluralization
            )

            resource = await resource_class.get(
                name,
                namespace=ns,
                api=self.api,
            )

            if resource is None:
                raise ResourceNotFoundError(
                    f"{plural} '{name}' not found in namespace '{ns}'",
                    resource_type=plural,
                    resource_id=f"{ns}/{name}",
                )

            await resource.patch(patch, type=patch_type)

            logger.info(
                "kubernetes_custom_resource_patched",
                api_version=api_version,
                plural=plural,
                name=name,
                namespace=ns,
            )
            return resource.raw

        except ResourceNotFoundError:
            raise
        except Exception as e:
            if _is_not_found_error(e):
                raise ResourceNotFoundError(
                    f"{plural} '{name}' not found in namespace '{ns}'",
                    resource_type=plural,
                    resource_id=f"{ns}/{name}",
                ) from e
            raise KubernetesError(
                f"Failed to patch {plural} '{name}': {e}",
                operation="patch",
                resource_kind=plural,
                resource_name=name,
                namespace=ns,
            ) from e

    async def delete_custom_resource(
        self,
        group: str,
        version: str,
        plural: str,
        name: str,
        namespace: str | None = None,
    ) -> None:
        """Delete a custom resource.

        Args:
            group: API group.
            version: API version.
            plural: Resource plural name.
            name: Resource name.
            namespace: Resource namespace. Uses default if None.

        Raises:
            ResourceNotFoundError: If resource doesn't exist.
            KubernetesError: If operation fails.
        """
        self._ensure_connected()
        api_version = f"{group}/{version}"
        ns = namespace or self._namespace

        logger.debug(
            "kubernetes_delete_custom_resource",
            api_version=api_version,
            plural=plural,
            name=name,
            namespace=ns,
        )

        try:
            resource_class = kr8s.asyncio.objects.new_class(
                kind=plural,
                version=api_version,
                namespaced=True,
            )

            resource = await resource_class.get(
                name,
                namespace=ns,
                api=self.api,
            )

            if resource is None:
                raise ResourceNotFoundError(
                    f"{plural} '{name}' not found in namespace '{ns}'",
                    resource_type=plural,
                    resource_id=f"{ns}/{name}",
                )

            await resource.delete()

            logger.info(
                "kubernetes_custom_resource_deleted",
                api_version=api_version,
                plural=plural,
                name=name,
                namespace=ns,
            )

        except ResourceNotFoundError:
            raise
        except Exception as e:
            if _is_not_found_error(e):
                raise ResourceNotFoundError(
                    f"{plural} '{name}' not found in namespace '{ns}'",
                    resource_type=plural,
                    resource_id=f"{ns}/{name}",
                ) from e
            raise KubernetesError(
                f"Failed to delete {plural} '{name}': {e}",
                operation="delete",
                resource_kind=plural,
                resource_name=name,
                namespace=ns,
            ) from e

    # =========================================================================
    # Core Kubernetes Resource Methods
    # =========================================================================

    async def list_nodes(
        self,
        label_selector: str | None = None,
    ) -> builtins.list[dict[str, Any]]:
        """List Kubernetes Node resources.

        Args:
            label_selector: Label selector string.

        Returns:
            List of Node resources.
        """
        return await self.list(
            kind="Node",
            label_selector=label_selector,
        )

    async def list_pods(
        self,
        namespace: str | None = None,
        label_selector: str | None = None,
        field_selector: str | None = None,
    ) -> builtins.list[dict[str, Any]]:
        """List Pod resources.

        Args:
            namespace: Namespace to list from. Uses '*' for all namespaces.
            label_selector: Label selector string.
            field_selector: Field selector string.

        Returns:
            List of Pod resources.
        """
        return await self.list(
            kind="Pod",
            namespace=namespace,
            label_selector=label_selector,
            field_selector=field_selector,
        )

    async def get_pod_logs(
        self,
        pod_name: str | None = None,
        namespace: str | None = None,
        label_selector: str | None = None,
        container: str | None = None,
        tail_lines: int | None = None,
        since_seconds: int | None = None,
        previous: bool = False,
        timestamps: bool = False,
        limit_bytes: int | None = None,
    ) -> builtins.list[dict[str, Any]]:
        """Get logs from pods for RCA and troubleshooting.

        Supports multiple ways to identify pods:
        - By exact pod name
        - By label selector (returns logs from all matching pods)

        Args:
            pod_name: Exact pod name. If provided, gets logs from this specific pod.
            namespace: Namespace to search in. Uses default if None.
            label_selector: Label selector to find pods (e.g., 'app=nova-api').
                If pod_name is provided, this is ignored.
            container: Container name. Required if pod has multiple containers.
                If None and pod has multiple containers, returns logs from first container.
            tail_lines: Number of lines from end of logs to return.
                If None, returns all available logs.
            since_seconds: Return logs newer than this many seconds.
                Useful for getting recent logs only.
            previous: If True, return logs from previous terminated container instance.
                Useful for debugging crashed pods.
            timestamps: If True, add RFC3339 timestamp at beginning of each log line.
            limit_bytes: Maximum bytes of logs to return. Useful for large log files.

        Returns:
            List of dictionaries, each containing:
            - pod_name: Name of the pod
            - namespace: Pod namespace
            - container: Container name
            - logs: Log content as string
            - log_lines: Number of lines returned
            - truncated: Whether logs were truncated
            - error: Error message if log retrieval failed for this pod

        Raises:
            KubernetesError: If operation fails completely.
            ValueError: If neither pod_name nor label_selector is provided.

        Example:
            # Get logs from specific pod
            logs = await adapter.get_pod_logs(
                pod_name="nova-api-5d4b8c9f7-x2k3m",
                namespace="openstack",
                tail_lines=100,
            )

            # Get logs from all pods matching label
            logs = await adapter.get_pod_logs(
                label_selector="app=nova-api",
                namespace="openstack",
                since_seconds=3600,  # Last hour
            )

            # Get previous container logs (crashed pod)
            logs = await adapter.get_pod_logs(
                pod_name="nova-api-5d4b8c9f7-x2k3m",
                previous=True,
            )
        """
        self._ensure_connected()

        if not pod_name and not label_selector:
            raise ValueError("Either pod_name or label_selector must be provided")

        ns = namespace or self._namespace
        results: list[dict[str, Any]] = []

        logger.debug(
            "get_pod_logs_started",
            pod_name=pod_name,
            namespace=ns,
            label_selector=label_selector,
            container=container,
            tail_lines=tail_lines,
            since_seconds=since_seconds,
            previous=previous,
        )

        try:
            # Get pods to fetch logs from
            if pod_name:
                # Get specific pod
                pods_gen = kr8s.asyncio.get(
                    "Pod",
                    pod_name,
                    namespace=ns,
                    api=self.api,
                )
                pods = [p async for p in pods_gen]
            else:
                # Get pods by label selector
                pods_gen = kr8s.asyncio.get(
                    "Pod",
                    namespace=ns,
                    label_selector=label_selector,
                    api=self.api,
                )
                pods = [p async for p in pods_gen]

            if not pods:
                logger.warning(
                    "no_pods_found_for_logs",
                    pod_name=pod_name,
                    label_selector=label_selector,
                    namespace=ns,
                )
                return []

            # Fetch logs from each pod
            for pod in pods:
                pod_result = await self._get_single_pod_logs(
                    pod=pod,
                    container=container,
                    tail_lines=tail_lines,
                    since_seconds=since_seconds,
                    previous=previous,
                    timestamps=timestamps,
                    limit_bytes=limit_bytes,
                )
                results.append(pod_result)

            logger.info(
                "get_pod_logs_completed",
                pods_count=len(results),
                successful=sum(1 for r in results if not r.get("error")),
            )

            return results

        except Exception as e:
            raise KubernetesError(
                f"Failed to get pod logs: {e}",
                operation="get_pod_logs",
                resource_kind="Pod",
                namespace=ns,
            ) from e

    async def _get_single_pod_logs(
        self,
        pod: APIObject,
        container: str | None = None,
        tail_lines: int | None = None,
        since_seconds: int | None = None,
        previous: bool = False,
        timestamps: bool = False,
        limit_bytes: int | None = None,
    ) -> dict[str, Any]:
        """Get logs from a single pod.

        Args:
            pod: kr8s Pod object.
            container: Container name.
            tail_lines: Number of lines from end.
            since_seconds: Logs newer than this.
            previous: Get previous container logs.
            timestamps: Add timestamps to logs.
            limit_bytes: Maximum bytes.

        Returns:
            Dictionary with pod logs and metadata.
        """
        pod_name = pod.metadata.name
        pod_namespace = pod.metadata.namespace

        # Determine container to use
        containers = pod.spec.get("containers", [])  # type: ignore[no-untyped-call]
        init_containers = pod.spec.get("initContainers", [])  # type: ignore[no-untyped-call]
        all_containers = containers + init_containers

        target_container = container
        if not target_container and len(containers) == 1:
            target_container = containers[0].get("name")
        elif not target_container and len(containers) > 1:
            # Default to first container, but note there are multiple
            target_container = containers[0].get("name")
            logger.debug(
                "multiple_containers_defaulting_to_first",
                pod=pod_name,
                container=target_container,
                available=[c.get("name") for c in all_containers],
            )

        result: dict[str, Any] = {
            "pod_name": pod_name,
            "namespace": pod_namespace,
            "container": target_container,
            "available_containers": [c.get("name") for c in all_containers],
            "logs": "",
            "log_lines": 0,
            "truncated": False,
            "error": None,
        }

        try:
            # Build log kwargs
            log_kwargs: dict[str, Any] = {}
            if target_container:
                log_kwargs["container"] = target_container
            if tail_lines is not None:
                log_kwargs["tail_lines"] = tail_lines
            if since_seconds is not None:
                log_kwargs["since_seconds"] = since_seconds
            if previous:
                log_kwargs["previous"] = True
            if timestamps:
                log_kwargs["timestamps"] = True
            if limit_bytes is not None:
                log_kwargs["limit_bytes"] = limit_bytes

            # Get logs using kr8s pod.logs() async iterator
            log_lines = []
            async for line in pod.logs(**log_kwargs):  # type: ignore[attr-defined]
                log_lines.append(line)

            result["logs"] = "\n".join(log_lines)
            result["log_lines"] = len(log_lines)

            # Check if truncated (limit_bytes was hit)
            if limit_bytes and len(result["logs"].encode()) >= limit_bytes:
                result["truncated"] = True

        except Exception as e:
            error_msg = str(e)
            result["error"] = error_msg
            logger.warning(
                "pod_log_retrieval_failed",
                pod=pod_name,
                container=target_container,
                error=error_msg,
            )

        return result

    # =========================================================================
    # MOSK-Specific Convenience Methods
    # =========================================================================

    async def list_machines(
        self,
        namespace: str | None = None,
        label_selector: str | None = None,
    ) -> builtins.list[dict[str, Any]]:
        """List Machine CRs.

        Args:
            namespace: Namespace to list from. Uses default if None.
            label_selector: Label selector string.

        Returns:
            List of Machine resources.
        """
        mapping = self.CRD_MAPPINGS["machines"]
        return await self.list_custom_resources(
            group=mapping["group"],
            version=mapping["version"],
            plural=mapping["plural"],
            namespace=namespace,
            label_selector=label_selector,
        )

    async def get_machine(
        self,
        name: str,
        namespace: str | None = None,
    ) -> dict[str, Any]:
        """Get a Machine CR.

        Args:
            name: Machine name.
            namespace: Namespace. Uses default if None.

        Returns:
            Machine resource.
        """
        mapping = self.CRD_MAPPINGS["machines"]
        return await self.get_custom_resource(
            group=mapping["group"],
            version=mapping["version"],
            plural=mapping["plural"],
            name=name,
            namespace=namespace,
        )

    async def discover_mosk_cluster_namespace(
        self,
        exclude_cluster: str = "kaas-mgmt",
        search_namespaces: builtins.list[str] | None = None,
    ) -> tuple[str | None, str | None]:
        """Discover the MOSK child cluster name and namespace.

        This method finds the non-management cluster (the MOSK child cluster)
        by searching specific namespaces first (to work with namespace-scoped
        permissions), then falling back to cluster-wide search if needed.

        Results are cached with a TTL to avoid repeated API calls while still
        picking up cluster changes within a reasonable time.

        Args:
            exclude_cluster: Name of the management cluster to exclude.
            search_namespaces: List of namespaces to search. Defaults to
                common MOSK namespaces: ["lab", "default", "mosk"].

        Returns:
            Tuple of (cluster_name, namespace) or (None, None) if not found.
        """
        cache_key = f"mosk_cluster_{exclude_cluster}"
        current_time = time.monotonic()

        # Check cache with TTL validation
        if cache_key in self._mosk_cluster_cache:
            cache_entry = self._mosk_cluster_cache[cache_key]
            cache_age = current_time - cache_entry.get("timestamp", 0)

            if cache_age < self._CACHE_TTL_SECONDS:
                cached_data = cache_entry.get("data")
                if cached_data:
                    logger.debug(
                        "using_cached_mosk_cluster",
                        cache_age_seconds=round(cache_age, 1),
                        ttl_seconds=self._CACHE_TTL_SECONDS,
                    )
                    return cached_data.get("name"), cached_data.get("namespace")
                return None, None
            else:
                # Cache expired, remove stale entry
                logger.debug(
                    "mosk_cluster_cache_expired",
                    cache_age_seconds=round(cache_age, 1),
                    ttl_seconds=self._CACHE_TTL_SECONDS,
                )
                del self._mosk_cluster_cache[cache_key]

        # Default namespaces to search (in priority order)
        if search_namespaces is None:
            search_namespaces = ["lab", "default", "mosk", "openstack"]

        mapping = self.CRD_MAPPINGS["clusters"]

        # First, try namespace-scoped search (works with limited permissions)
        for ns in search_namespaces:
            try:
                clusters = await self.list_custom_resources(
                    group=mapping["group"],
                    version=mapping["version"],
                    plural=mapping["plural"],
                    namespace=ns,
                )

                # Log the search results for debugging
                cluster_names = [c.get("metadata", {}).get("name", "?") for c in clusters]
                logger.info(
                    "namespace_cluster_search_result",
                    namespace=ns,
                    cluster_count=len(clusters),
                    cluster_names=cluster_names,
                )

                for cluster in clusters:
                    metadata = cluster.get("metadata", {})
                    name = metadata.get("name", "")
                    namespace = metadata.get("namespace", ns)

                    # Skip the management cluster
                    if name == exclude_cluster:
                        continue

                    # Found the MOSK child cluster - cache with timestamp
                    self._mosk_cluster_cache[cache_key] = {
                        "data": {"name": name, "namespace": namespace},
                        "timestamp": current_time,
                    }
                    logger.info(
                        "discovered_mosk_cluster",
                        cluster_name=name,
                        namespace=namespace,
                        search_method="namespace_scoped",
                    )
                    return name, namespace

            except Exception as e:
                # Log at INFO level for better visibility during debugging
                # Include error type to distinguish permission errors from other failures
                error_type = type(e).__name__
                error_msg = str(e)
                # Check for common permission-related errors
                is_permission_error = any(
                    x in error_msg.lower()
                    for x in ["forbidden", "403", "unauthorized", "401"]
                )
                logger.info(
                    "namespace_search_failed",
                    namespace=ns,
                    error=error_msg,
                    error_type=error_type,
                    is_permission_error=is_permission_error,
                )
                continue

        # Fall back to cluster-wide search if namespace search didn't find anything
        try:
            logger.debug("trying_cluster_wide_search")
            clusters = await self.list_custom_resources(
                group=mapping["group"],
                version=mapping["version"],
                plural=mapping["plural"],
                namespace="*",  # All namespaces
            )

            for cluster in clusters:
                metadata = cluster.get("metadata", {})
                name = metadata.get("name", "")
                namespace = metadata.get("namespace", "")

                # Skip the management cluster
                if name == exclude_cluster:
                    continue

                # Found the MOSK child cluster - cache with timestamp
                self._mosk_cluster_cache[cache_key] = {
                    "data": {"name": name, "namespace": namespace},
                    "timestamp": current_time,
                }
                logger.info(
                    "discovered_mosk_cluster",
                    cluster_name=name,
                    namespace=namespace,
                    search_method="cluster_wide",
                )
                return name, namespace

        except Exception as e:
            error_type = type(e).__name__
            error_msg = str(e)
            is_permission_error = any(
                x in error_msg.lower()
                for x in ["forbidden", "403", "unauthorized", "401"]
            )
            logger.info(
                "cluster_wide_search_failed",
                error=error_msg,
                error_type=error_type,
                is_permission_error=is_permission_error,
            )

        # No MOSK cluster found - cache negative result with timestamp
        self._mosk_cluster_cache[cache_key] = {
            "data": None,
            "timestamp": current_time,
        }
        logger.warning(
            "failed_to_discover_mosk_cluster",
            searched_namespaces=search_namespaces,
            message="Could not find MOSK cluster. Try specifying mosk_cluster_name and mosk_namespace explicitly.",
        )
        return None, None

    @classmethod
    def clear_cluster_cache(cls) -> None:
        """Clear the MOSK cluster discovery cache.

        Useful when cluster configuration has changed and you want
        to force re-discovery on the next call.
        """
        cls._mosk_cluster_cache.clear()
        logger.debug("mosk_cluster_cache_cleared")

    async def get_mosk_machines_namespace(
        self,
        configured_namespace: str | None = None,
        _configured_cluster: str | None = None,
    ) -> str | None:
        """Get the namespace where MOSK Machine CRs are located.

        Uses configured value if provided, otherwise auto-discovers.

        Args:
            configured_namespace: Pre-configured namespace (from settings).
            _configured_cluster: Reserved for future cluster-aware discovery.

        Returns:
            Namespace string or None if not found.
        """
        # Use configured value if provided
        if configured_namespace:
            return configured_namespace

        # Auto-discover
        _cluster_name, namespace = await self.discover_mosk_cluster_namespace()
        return namespace

    async def list_openstack_deployments(
        self,
        namespace: str | None = None,
    ) -> builtins.list[dict[str, Any]]:
        """List OpenStackDeployment CRs.

        Args:
            namespace: Namespace to list from. Uses default if None.

        Returns:
            List of OpenStackDeployment resources.
        """
        mapping = self.CRD_MAPPINGS["openstackdeployments"]
        return await self.list_custom_resources(
            group=mapping["group"],
            version=mapping["version"],
            plural=mapping["plural"],
            namespace=namespace,
        )

    async def get_openstack_deployment(
        self,
        name: str,
        namespace: str | None = None,
    ) -> dict[str, Any]:
        """Get an OpenStackDeployment CR.

        Args:
            name: Deployment name.
            namespace: Namespace. Uses default if None.

        Returns:
            OpenStackDeployment resource.
        """
        mapping = self.CRD_MAPPINGS["openstackdeployments"]
        return await self.get_custom_resource(
            group=mapping["group"],
            version=mapping["version"],
            plural=mapping["plural"],
            name=name,
            namespace=namespace,
        )

    async def list_openstack_deployment_status(
        self,
        namespace: str | None = None,
    ) -> builtins.list[dict[str, Any]]:
        """List OpenStackDeploymentStatus (osdplst) CRs.

        OSDPLStatus contains the real status of the OpenStack deployment including:
        - status.osdpl: Overall state, health ratio, LCM progress
        - status.health: Per-component health (nova.api, neutron.server, etc.)
        - status.services: Per-service LCM state (compute, networking, etc.)

        Args:
            namespace: Namespace to list from. Uses default if None.

        Returns:
            List of OpenStackDeploymentStatus resources.
        """
        mapping = self.CRD_MAPPINGS["openstackdeploymentstatus"]
        return await self.list_custom_resources(
            group=mapping["group"],
            version=mapping["version"],
            plural=mapping["plural"],
            namespace=namespace,
        )

    async def get_openstack_deployment_status(
        self,
        name: str,
        namespace: str | None = None,
    ) -> dict[str, Any]:
        """Get an OpenStackDeploymentStatus (osdplst) CR.

        OSDPLStatus contains the real status of the OpenStack deployment including:
        - status.osdpl.state: Overall state (APPLIED, APPLYING, FAILED, etc.)
        - status.osdpl.health: Health ratio (e.g., "23/23")
        - status.osdpl.lcm_progress: LCM progress (e.g., "18/18")
        - status.health: Per-component health with status (Ready/NotReady)
        - status.services: Per-service LCM state and timestamps

        Args:
            name: Deployment status name (usually same as OSDPL name).
            namespace: Namespace. Uses default if None.

        Returns:
            OpenStackDeploymentStatus resource.
        """
        mapping = self.CRD_MAPPINGS["openstackdeploymentstatus"]
        return await self.get_custom_resource(
            group=mapping["group"],
            version=mapping["version"],
            plural=mapping["plural"],
            name=name,
            namespace=namespace,
        )

    async def patch_openstack_deployment(
        self,
        name: str,
        patch: builtins.list[dict[str, Any]],
        namespace: str | None = None,
    ) -> dict[str, Any]:
        """Patch an OpenStackDeployment (OSDPL) CR using JSON patch.

        This method applies a JSON Patch (RFC 6902) to the OSDPL resource.
        Only supports non-destructive operations (add, replace) on spec fields.

        PRIVILEGED OPERATION: Should only be called after CRQ validation.

        Args:
            name: Deployment name.
            namespace: Namespace. Uses default if None.
            patch: JSON Patch operations list.
                   Each operation must have: op, path, value
                   Only 'add' and 'replace' operations are allowed.

        Returns:
            Patched OpenStackDeployment resource.

        Raises:
            ValueError: If patch contains forbidden operations or invalid structure.
            ResourceNotFoundError: If OSDPL doesn't exist.
            KubernetesError: If operation fails.
        """
        # Validate patch is a non-empty list
        if not patch:
            raise ValueError("Patch operations list cannot be empty")
        if not isinstance(patch, list):
            raise ValueError("Patch must be a list of operations")

        # Validate each patch operation
        allowed_ops = {"add", "replace"}
        for idx, op in enumerate(patch):
            if not isinstance(op, dict):
                raise ValueError(
                    f"Patch operation at index {idx} must be a dictionary, got {type(op).__name__}"
                )

            # Validate required 'op' field
            op_type = op.get("op")
            if op_type is None:
                raise ValueError(f"Patch operation at index {idx} missing required 'op' field")
            if op_type not in allowed_ops:
                raise ValueError(
                    f"Operation '{op_type}' at index {idx} is not allowed. "
                    f"Only {allowed_ops} operations are permitted."
                )

            # Validate required 'path' field
            path = op.get("path")
            if path is None:
                raise ValueError(f"Patch operation at index {idx} missing required 'path' field")
            if not isinstance(path, str):
                raise ValueError(
                    f"Patch operation at index {idx}: 'path' must be a string, "
                    f"got {type(path).__name__}"
                )
            # Ensure we're only patching spec, not status or metadata
            if not path.startswith("/spec"):
                raise ValueError(
                    f"Path '{path}' at index {idx} is not allowed. "
                    "Only /spec/* paths can be patched."
                )
            # Validate path format (must start with / and not have consecutive slashes)
            if "//" in path or (len(path) > 1 and path.endswith("/")):
                raise ValueError(
                    f"Invalid path format '{path}' at index {idx}. "
                    "Path must not contain consecutive slashes or trailing slash."
                )

            # Validate required 'value' field for add/replace operations
            if "value" not in op:
                raise ValueError(
                    f"Patch operation '{op_type}' at index {idx} requires a 'value' field"
                )

        mapping = self.CRD_MAPPINGS["openstackdeployments"]
        return await self.patch_custom_resource(
            group=mapping["group"],
            version=mapping["version"],
            plural=mapping["plural"],
            name=name,
            patch=patch,
            namespace=namespace,
            patch_type="json",
        )

    async def list_maintenance_requests(
        self,
        _namespace: str | None = None,
        label_selector: str | None = None,
    ) -> builtins.list[dict[str, Any]]:
        """List NodeMaintenanceRequest CRs.

        Note: NodeMaintenanceRequest is cluster-scoped (not namespaced).
        The namespace parameter is accepted for API compatibility but ignored.

        Args:
            _namespace: Ignored - NodeMaintenanceRequest is cluster-scoped.
            label_selector: Label selector string.

        Returns:
            List of NodeMaintenanceRequest resources.
        """
        mapping = self.CRD_MAPPINGS["nodemaintenancerequests"]
        return await self.list_custom_resources(
            group=mapping["group"],
            version=mapping["version"],
            plural=mapping["plural"],
            namespace=None,  # Cluster-scoped, no namespace
            label_selector=label_selector,
            namespaced=False,  # NodeMaintenanceRequest is cluster-scoped
        )

    # =========================================================================
    # MiraCeph Methods
    # =========================================================================

    async def is_ceph_enabled(
        self,
        namespace: str | None = None,
    ) -> bool:
        """Check if Ceph is enabled in the cluster.

        This method checks for the presence of MiraCeph CRs
        to determine if Ceph storage is configured.

        Args:
            namespace: Namespace to check. Uses default if None.

        Returns:
            True if Ceph is enabled, False otherwise.
        """
        try:
            miracephs = await self.list_miraceph(namespace=namespace)
            if miracephs:
                return True
        except ResourceNotFoundError:
            # CRD not installed - Ceph not deployed
            pass
        except KubernetesError as e:
            logger.warning(
                "ceph_detection_failed",
                namespace=namespace,
                error=str(e),
            )
            # Return False but issue is logged - callers should check logs

        return False

    async def list_miraceph(
        self,
        namespace: str | None = None,
        label_selector: str | None = None,
    ) -> builtins.list[dict[str, Any]]:
        """List MiraCeph CRs.

        MiraCeph is the Ceph management CR for.

        Args:
            namespace: Namespace to list from. Uses default if None.
            label_selector: Label selector string.

        Returns:
            List of MiraCeph resources.
        """
        mapping = self.CRD_MAPPINGS["miracephs"]
        return await self.list_custom_resources(
            group=mapping["group"],
            version=mapping["version"],
            plural=mapping["plural"],
            namespace=namespace,
            label_selector=label_selector,
        )

    async def get_miraceph(
        self,
        name: str,
        namespace: str | None = None,
    ) -> dict[str, Any]:
        """Get a MiraCeph CR.

        Args:
            name: MiraCeph name.
            namespace: Namespace. Uses default if None.

        Returns:
            MiraCeph resource.
        """
        mapping = self.CRD_MAPPINGS["miracephs"]
        return await self.get_custom_resource(
            group=mapping["group"],
            version=mapping["version"],
            plural=mapping["plural"],
            name=name,
            namespace=namespace,
        )

    # =========================================================================
    # Cluster Maintenance Methods
    # =========================================================================

    async def list_cluster_maintenance_requests(
        self,
        label_selector: str | None = None,
    ) -> builtins.list[dict[str, Any]]:
        """List ClusterMaintenanceRequest CRs.

        ClusterMaintenanceRequest must be created before NodeMaintenanceRequest
        to enable maintenance mode at cluster level.

        Note: ClusterMaintenanceRequest is cluster-scoped (not namespaced).

        Args:
            label_selector: Label selector string.

        Returns:
            List of ClusterMaintenanceRequest resources.
        """
        mapping = self.CRD_MAPPINGS["clustermaintenancerequests"]
        return await self.list_custom_resources(
            group=mapping["group"],
            version=mapping["version"],
            plural=mapping["plural"],
            namespace=None,
            label_selector=label_selector,
            namespaced=False,  # ClusterMaintenanceRequest is cluster-scoped
        )

    async def get_cluster_maintenance_request(
        self,
        name: str,
    ) -> dict[str, Any]:
        """Get a ClusterMaintenanceRequest CR.

        Note: ClusterMaintenanceRequest is cluster-scoped (not namespaced).

        Args:
            name: Request name.

        Returns:
            ClusterMaintenanceRequest resource.
        """
        mapping = self.CRD_MAPPINGS["clustermaintenancerequests"]
        return await self.get_custom_resource(
            group=mapping["group"],
            version=mapping["version"],
            plural=mapping["plural"],
            name=name,
            namespace=None,
            namespaced=False,  # ClusterMaintenanceRequest is cluster-scoped
        )

    # =========================================================================
    # Graceful Reboot Methods
    # =========================================================================

    async def list_graceful_reboot_requests(
        self,
        namespace: str | None = None,
        label_selector: str | None = None,
    ) -> builtins.list[dict[str, Any]]:
        """List GracefulRebootRequest CRs.

        GracefulRebootRequest manages orchestrated cluster reboots.

        Args:
            namespace: Namespace to list from. Uses default if None.
            label_selector: Label selector string.

        Returns:
            List of GracefulRebootRequest resources.
        """
        mapping = self.CRD_MAPPINGS["gracefulrebootrequests"]
        return await self.list_custom_resources(
            group=mapping["group"],
            version=mapping["version"],
            plural=mapping["plural"],
            namespace=namespace,
            label_selector=label_selector,
        )

    async def get_graceful_reboot_request(
        self,
        name: str,
        namespace: str | None = None,
    ) -> dict[str, Any]:
        """Get a GracefulRebootRequest CR.

        Args:
            name: Request name.
            namespace: Namespace. Uses default if None.

        Returns:
            GracefulRebootRequest resource.
        """
        mapping = self.CRD_MAPPINGS["gracefulrebootrequests"]
        return await self.get_custom_resource(
            group=mapping["group"],
            version=mapping["version"],
            plural=mapping["plural"],
            name=name,
            namespace=namespace,
        )

    # =========================================================================
    # Cluster Update Plan Methods
    # =========================================================================

    async def list_cluster_update_plans(
        self,
        namespace: str | None = None,
        label_selector: str | None = None,
    ) -> builtins.list[dict[str, Any]]:
        """List ClusterUpdatePlan CRs.

        ClusterUpdatePlan manages granular MOSK updates.

        Args:
            namespace: Namespace to list from. Uses default if None.
            label_selector: Label selector string.

        Returns:
            List of ClusterUpdatePlan resources.
        """
        mapping = self.CRD_MAPPINGS["clusterupdateplans"]
        return await self.list_custom_resources(
            group=mapping["group"],
            version=mapping["version"],
            plural=mapping["plural"],
            namespace=namespace,
            label_selector=label_selector,
        )

    async def get_cluster_update_plan(
        self,
        name: str,
        namespace: str | None = None,
    ) -> dict[str, Any]:
        """Get a ClusterUpdatePlan CR.

        Args:
            name: Plan name.
            namespace: Namespace. Uses default if None.

        Returns:
            ClusterUpdatePlan resource.
        """
        mapping = self.CRD_MAPPINGS["clusterupdateplans"]
        return await self.get_custom_resource(
            group=mapping["group"],
            version=mapping["version"],
            plural=mapping["plural"],
            name=name,
            namespace=namespace,
        )

    async def find_cluster_update_plan(
        self,
        cluster_name: str,
        target_release: str,
        namespace: str | None = None,
    ) -> dict[str, Any] | None:
        """Find a ClusterUpdatePlan for a specific cluster and target release.

        Searches for an UpdatePlan where spec.cluster matches cluster_name
        and spec.target matches target_release.

        Args:
            cluster_name: Name of the cluster (e.g., 'mos').
            target_release: Target release (e.g., 'mosk-21-0-0-25-2').
            namespace: Namespace to search in. Uses default if None.

        Returns:
            ClusterUpdatePlan resource or None if not found.
        """
        plans = await self.list_cluster_update_plans(namespace=namespace)

        for plan in plans:
            spec = plan.get("spec", {})
            if spec.get("cluster") == cluster_name and spec.get("target") == target_release:
                return plan

        return None

    async def patch_cluster_update_plan_steps(
        self,
        name: str,
        namespace: str,
        step_ids: builtins.list[str] | None = None,
        commence: bool = True,
    ) -> dict[str, Any]:
        """Patch ClusterUpdatePlan steps to commence or stop them.

        This method patches the spec.steps[].commence field to start or stop
        upgrade steps. By default, it commences all steps. Pass step_ids to
        selectively commence specific steps only.

        PRIVILEGED OPERATION: Should only be called after CRQ validation.

        Args:
            name: ClusterUpdatePlan name.
            namespace: Namespace where the plan is located.
            step_ids: List of step IDs to patch. If None, patches all steps.
            commence: Value to set for commence field (True to start, False to stop).

        Returns:
            Patched ClusterUpdatePlan resource.

        Raises:
            ResourceNotFoundError: If plan doesn't exist.
            KubernetesError: If operation fails.
        """
        # First get the current plan to understand its structure
        plan = await self.get_cluster_update_plan(name=name, namespace=namespace)
        if not plan:
            raise ResourceNotFoundError(
                f"ClusterUpdatePlan '{name}' not found in namespace '{namespace}'",
                resource_type="ClusterUpdatePlan",
                resource_id=f"{namespace}/{name}",
            )

        # Build the patched steps list
        current_steps = plan.get("spec", {}).get("steps", [])
        patched_steps = []

        for step in current_steps:
            step_copy = dict(step)
            step_id = step.get("id", "")

            # If step_ids is None, patch all steps. Otherwise only patch specified ones.
            if step_ids is None or step_id in step_ids:
                step_copy["commence"] = commence

            patched_steps.append(step_copy)

        # Apply the patch
        patch = {"spec": {"steps": patched_steps}}

        mapping = self.CRD_MAPPINGS["clusterupdateplans"]

        logger.info(
            "patching_cluster_update_plan_steps",
            plan_name=name,
            namespace=namespace,
            step_ids=step_ids or "all",
            commence=commence,
        )

        return await self.patch_custom_resource(
            group=mapping["group"],
            version=mapping["version"],
            plural=mapping["plural"],
            name=name,
            patch=patch,
            namespace=namespace,
            patch_type="merge",
            kind="ClusterUpdatePlan",
        )

    # =========================================================================
    # KaaS Release Management (MCC)
    # =========================================================================

    async def list_kaas_releases(
        self,
        label_selector: str | None = None,
    ) -> builtins.list[dict[str, Any]]:
        """List KaaSRelease resources.

        KaaSRelease defines the MCC platform version and contains:
        - spec.version: MCC version (e.g., '2.30.2')
        - spec.clusterRelease: Default cluster release for MCC
        - spec.supportedClusterReleases: List of supported MOSK releases with upgrade paths

        KaaSReleases are cluster-scoped (not namespaced).

        Args:
            label_selector: Label selector for filtering.

        Returns:
            List of KaaSRelease resources.
        """
        mapping = self.CRD_MAPPINGS["kaasreleases"]
        return await self.list_custom_resources(
            group=mapping["group"],
            version=mapping["version"],
            plural=mapping["plural"],
            namespace=None,
            label_selector=label_selector,
            namespaced=False,  # KaaSRelease is cluster-scoped
        )

    async def get_kaas_release(
        self,
        name: str,
    ) -> dict[str, Any] | None:
        """Get a specific KaaSRelease by name.

        Args:
            name: Release name (e.g., 'kaas-2-30-2').

        Returns:
            KaaSRelease resource or None if not found.
        """
        mapping = self.CRD_MAPPINGS["kaasreleases"]
        try:
            return await self.get_custom_resource(
                group=mapping["group"],
                version=mapping["version"],
                plural=mapping["plural"],
                name=name,
                namespace=None,
                namespaced=False,  # KaaSRelease is cluster-scoped
            )
        except ResourceNotFoundError:
            return None

    async def get_active_kaas_release(self) -> dict[str, Any] | None:
        """Get the active KaaSRelease for the management cluster.

        Discovers the active KaaSRelease by:
        1. Finding the management cluster (kaas-mgmt)
        2. Getting its spec.providerSpec.value.release field
        3. Returning the corresponding KaaSRelease

        Returns:
            Active KaaSRelease resource or None if not found/not configured.

        Raises:
            Exception: If API call fails (network error, auth error, etc.).
                Callers should handle exceptions to distinguish API failures
                from legitimate "not found" cases (which return None).
        """
        # Get the management cluster - let API exceptions propagate
        mgmt_cluster = await self.get_cluster(name="kaas-mgmt", namespace="default")
        if not mgmt_cluster:
            logger.warning("management_cluster_not_found")
            return None

        # Get the kaas release name from the cluster
        kaas_release_name = (
            mgmt_cluster.get("spec", {}).get("providerSpec", {}).get("value", {}).get("release")
        )

        if not kaas_release_name:
            logger.warning("kaas_release_not_specified_in_mgmt_cluster")
            return None

        logger.debug("found_active_kaas_release", release_name=kaas_release_name)
        return await self.get_kaas_release(kaas_release_name)

    async def get_supported_upgrade_paths(
        self,
        current_release: str,
    ) -> builtins.list[dict[str, Any]]:
        """Get supported upgrade paths for a given MOSK release.

        Looks up the active KaaSRelease and finds the availableUpgrades
        for the specified current release.

        Args:
            current_release: Current MOSK release name (e.g., 'mosk-17-4-6-25-1-1').

        Returns:
            List of available upgrades with structure:
            [{"version": "21.0.0", "skipMaintenance": False, "rebootRequired": False}, ...]
            Empty list if no upgrades available or release not found.

        Raises:
            Exception: If API call to get KaaSRelease fails. Callers should
                handle exceptions to distinguish API failures from "no upgrades".
        """
        # Let API exceptions propagate - callers should handle them
        kaas_release = await self.get_active_kaas_release()
        if not kaas_release:
            logger.warning("no_active_kaas_release_for_upgrade_paths")
            return []

        supported_releases = kaas_release.get("spec", {}).get("supportedClusterReleases", [])

        for release_info in supported_releases:
            if release_info.get("name") == current_release:
                return cast("list[dict[str, Any]]", release_info.get("availableUpgrades", []))

        logger.debug(
            "release_not_found_in_supported_releases",
            current_release=current_release,
        )
        return []

    async def validate_upgrade_path(
        self,
        current_release: str,
        target_release: str,
    ) -> tuple[bool, str | None, dict[str, Any] | None]:
        """Validate if an upgrade path from current to target release is supported.

        Args:
            current_release: Current MOSK release name (e.g., 'mosk-17-4-6-25-1-1').
            target_release: Target MOSK release name (e.g., 'mosk-21-0-0-25-2').

        Returns:
            Tuple of (is_valid, error_message, upgrade_info):
            - is_valid: True if upgrade path is supported
            - error_message: Description of why validation failed (None if valid)
            - upgrade_info: Dict with upgrade details (skipMaintenance, rebootRequired, etc.)
        """
        # Get the target release to extract its version
        target_release_obj = await self.get_cluster_release(target_release)
        if not target_release_obj:
            return (
                False,
                f"Target ClusterRelease '{target_release}' not found",
                None,
            )

        target_version = target_release_obj.get("spec", {}).get("version")
        if not target_version:
            return (
                False,
                f"Target release '{target_release}' has no version specified",
                None,
            )

        # Get supported upgrade paths
        available_upgrades = await self.get_supported_upgrade_paths(current_release)
        if not available_upgrades:
            # Check if current release exists at all
            kaas_release = await self.get_active_kaas_release()
            if kaas_release:
                supported = kaas_release.get("spec", {}).get("supportedClusterReleases", [])
                release_names = [r.get("name") for r in supported]
                if current_release not in release_names:
                    return (
                        False,
                        f"Current release '{current_release}' is not in supportedClusterReleases",
                        None,
                    )
            return (
                False,
                f"No upgrade paths available from '{current_release}'",
                None,
            )

        # Find matching upgrade path by version
        for upgrade in available_upgrades:
            if upgrade.get("version") == target_version:
                return (
                    True,
                    None,
                    {
                        "version": target_version,
                        "skipMaintenance": upgrade.get("skipMaintenance", False),
                        "rebootRequired": upgrade.get("rebootRequired", False),
                    },
                )

        # Build helpful error message
        available_versions: list[str] = [
            v for u in available_upgrades if (v := u.get("version")) is not None
        ]
        return (
            False,
            f"Upgrade to version '{target_version}' is not supported from '{current_release}'. "
            f"Available upgrade versions: {', '.join(available_versions)}",
            None,
        )

    # =========================================================================
    # Cluster Release Management (MCC)
    # =========================================================================

    async def list_cluster_releases(
        self,
        label_selector: str | None = None,
    ) -> builtins.list[dict[str, Any]]:
        """List available ClusterRelease resources (MOSK platform releases).

        ClusterReleases are cluster-scoped (not namespaced) and define
        available MOSK platform versions for upgrade.

        Args:
            label_selector: Label selector for filtering.

        Returns:
            List of ClusterRelease resources.
        """
        mapping = self.CRD_MAPPINGS["clusterreleases"]
        return await self.list_custom_resources(
            group=mapping["group"],
            version=mapping["version"],
            plural=mapping["plural"],
            namespace=None,
            label_selector=label_selector,
            namespaced=False,  # ClusterRelease is cluster-scoped
        )

    async def get_cluster_release(
        self,
        name: str,
    ) -> dict[str, Any] | None:
        """Get a specific ClusterRelease by name.

        Args:
            name: Release name (e.g., 'mosk-21-0-2-25-2-2').

        Returns:
            ClusterRelease resource or None if not found.
        """
        mapping = self.CRD_MAPPINGS["clusterreleases"]
        try:
            return await self.get_custom_resource(
                group=mapping["group"],
                version=mapping["version"],
                plural=mapping["plural"],
                name=name,
                namespace=None,
                namespaced=False,  # ClusterRelease is cluster-scoped
            )
        except ResourceNotFoundError:
            return None

    async def get_cluster(
        self,
        name: str,
        namespace: str | None = None,
    ) -> dict[str, Any] | None:
        """Get a Cluster CR by name.

        Args:
            name: Cluster name (e.g., 'mos').
            namespace: Namespace where the cluster is defined.

        Returns:
            Cluster resource or None if not found.
        """
        mapping = self.CRD_MAPPINGS["clusters"]
        return await self.get_custom_resource(
            group=mapping["group"],
            version=mapping["version"],
            plural=mapping["plural"],
            name=name,
            namespace=namespace,
        )

    async def list_clusters(
        self,
        namespace: str | None = None,
        label_selector: str | None = None,
    ) -> builtins.list[dict[str, Any]]:
        """List Cluster CRs.

        Args:
            namespace: Namespace to list clusters from.
            label_selector: Optional label selector for filtering.

        Returns:
            List of Cluster resources.
        """
        mapping = self.CRD_MAPPINGS["clusters"]
        return await self.list_custom_resources(
            group=mapping["group"],
            version=mapping["version"],
            plural=mapping["plural"],
            namespace=namespace,
            label_selector=label_selector,
        )

    async def patch_cluster_release(
        self,
        name: str,
        target_release: str,
        namespace: str | None = None,
    ) -> dict[str, Any]:
        """Patch a Cluster CR to change its release version (triggers MOSK platform upgrade).

        This method applies a merge patch to update the spec.providerSpec.value.release
        field in the Cluster CR. This is the ONLY field modified.

        PRIVILEGED OPERATION: Should only be called after CRQ validation.

        Args:
            name: Cluster name (e.g., 'mos').
            target_release: Target release version (e.g., 'mosk-21-0-2-25-2-2').
            namespace: Namespace where the cluster is defined.

        Returns:
            Patched Cluster resource.

        Raises:
            ResourceNotFoundError: If cluster doesn't exist.
            KubernetesError: If operation fails.
        """
        mapping = self.CRD_MAPPINGS["clusters"]
        ns = self._resolve_namespace(namespace)

        logger.info(
            "patching_cluster_release",
            cluster_name=name,
            namespace=ns,
            target_release=target_release,
        )

        # Use merge patch to update only the release field
        patch = {
            "spec": {
                "providerSpec": {
                    "value": {
                        "release": target_release,
                    },
                },
            },
        }

        return await self.patch_custom_resource(
            group=mapping["group"],
            version=mapping["version"],
            plural=mapping["plural"],
            name=name,
            patch=patch,
            namespace=ns,
            patch_type="merge",
            kind="Cluster",
        )

    async def list_cluster_upgrade_statuses(
        self,
        namespace: str | None = None,
        label_selector: str | None = None,
    ) -> builtins.list[dict[str, Any]]:
        """List ClusterUpgradeStatus resources.

        Args:
            namespace: Namespace to list from. Uses default if None.
            label_selector: Label selector string.

        Returns:
            List of ClusterUpgradeStatus resources.
        """
        mapping = self.CRD_MAPPINGS["clusterupgradestatuses"]
        return await self.list_custom_resources(
            group=mapping["group"],
            version=mapping["version"],
            plural=mapping["plural"],
            namespace=namespace,
            label_selector=label_selector,
        )

    async def get_cluster_upgrade_status(
        self,
        name: str,
        namespace: str | None = None,
    ) -> dict[str, Any] | None:
        """Get a specific ClusterUpgradeStatus by name.

        Args:
            name: Status name (e.g., 'mos-21.0.2-25.2.2').
            namespace: Namespace. Uses default if None.

        Returns:
            ClusterUpgradeStatus resource or None if not found.
        """
        mapping = self.CRD_MAPPINGS["clusterupgradestatuses"]
        return await self.get_custom_resource(
            group=mapping["group"],
            version=mapping["version"],
            plural=mapping["plural"],
            name=name,
            namespace=namespace,
        )

    async def list_machine_upgrade_statuses(
        self,
        namespace: str | None = None,
        label_selector: str | None = None,
    ) -> builtins.list[dict[str, Any]]:
        """List MachineUpgradeStatus resources.

        Args:
            namespace: Namespace to list from. Uses default if None.
            label_selector: Label selector string.

        Returns:
            List of MachineUpgradeStatus resources.
        """
        mapping = self.CRD_MAPPINGS["machineupgradestatuses"]
        return await self.list_custom_resources(
            group=mapping["group"],
            version=mapping["version"],
            plural=mapping["plural"],
            namespace=namespace,
            label_selector=label_selector,
        )

    # =========================================================================
    # LCM Machine and Helm Bundle Methods (for upgrade tracking)
    # =========================================================================

    async def list_lcm_machines(
        self,
        namespace: str | None = None,
        label_selector: str | None = None,
    ) -> builtins.list[dict[str, Any]]:
        """List LCMMachine resources.

        LCMMachine tracks LCM state for each machine including:
        - status.state: Current LCM state (Ready, Prepare, Deploy, Reconfigure)
        - status.release: Current release version
        - status.stateItemStatuses: Detailed progress of LCM operations

        Args:
            namespace: Namespace to list from. Uses default if None.
            label_selector: Label selector string.

        Returns:
            List of LCMMachine resources.
        """
        mapping = self.CRD_MAPPINGS["lcmmachines"]
        return await self.list_custom_resources(
            group=mapping["group"],
            version=mapping["version"],
            plural=mapping["plural"],
            namespace=namespace,
            label_selector=label_selector,
        )

    async def get_lcm_machine(
        self,
        name: str,
        namespace: str | None = None,
    ) -> dict[str, Any]:
        """Get an LCMMachine resource.

        Args:
            name: LCMMachine name (typically same as Machine name).
            namespace: Namespace. Uses default if None.

        Returns:
            LCMMachine resource.
        """
        mapping = self.CRD_MAPPINGS["lcmmachines"]
        return await self.get_custom_resource(
            group=mapping["group"],
            version=mapping["version"],
            plural=mapping["plural"],
            name=name,
            namespace=namespace,
        )

    async def list_helm_bundles(
        self,
        namespace: str | None = None,
        label_selector: str | None = None,
    ) -> builtins.list[dict[str, Any]]:
        """List HelmBundle resources.

        HelmBundle tracks helm chart upgrade status including:
        - status.release: Current release version
        - status.releaseStatuses: Per-chart upgrade status (ready, success, etc.)

        Args:
            namespace: Namespace to list from. Uses default if None.
            label_selector: Label selector string.

        Returns:
            List of HelmBundle resources.
        """
        mapping = self.CRD_MAPPINGS["helmbundles"]
        return await self.list_custom_resources(
            group=mapping["group"],
            version=mapping["version"],
            plural=mapping["plural"],
            namespace=namespace,
            label_selector=label_selector,
        )

    async def get_helm_bundle(
        self,
        name: str,
        namespace: str | None = None,
    ) -> dict[str, Any]:
        """Get a HelmBundle resource.

        Args:
            name: HelmBundle name (typically same as cluster name).
            namespace: Namespace. Uses default if None.

        Returns:
            HelmBundle resource.
        """
        mapping = self.CRD_MAPPINGS["helmbundles"]
        return await self.get_custom_resource(
            group=mapping["group"],
            version=mapping["version"],
            plural=mapping["plural"],
            name=name,
            namespace=namespace,
        )

    async def list_lcm_cluster_upgrade_statuses(
        self,
        namespace: str | None = None,
        label_selector: str | None = None,
    ) -> builtins.list[dict[str, Any]]:
        """List LCMClusterUpgradeStatus resources.

        LCMClusterUpgradeStatus tracks LCM-level upgrade progress
        (different from kaas.mirantis.com ClusterUpgradeStatus).

        Args:
            namespace: Namespace to list from. Uses default if None.
            label_selector: Label selector string.

        Returns:
            List of LCMClusterUpgradeStatus resources.
        """
        mapping = self.CRD_MAPPINGS["lcmclusterupgradestatuses"]
        return await self.list_custom_resources(
            group=mapping["group"],
            version=mapping["version"],
            plural=mapping["plural"],
            namespace=namespace,
            label_selector=label_selector,
        )

    # =========================================================================
    # Resource Application (Apply)
    # =========================================================================

    async def apply(
        self,
        resource: KubernetesResource[Any, Any],
        namespace: str | None = None,
    ) -> dict[str, Any]:
        """Apply a MOSK CRD resource (create or update).

        This method accepts Pydantic CRD models and applies them to the cluster.

        Args:
            resource: MOSK CRD Pydantic model.
            namespace: Override namespace.

        Returns:
            Applied resource as dictionary.

        Raises:
            KubernetesError: If operation fails.
        """
        resource_dict = resource.to_kubernetes()
        name = resource.metadata.name
        ns = namespace or resource.metadata.namespace or self._namespace

        # Get CRD info from the resource
        group = resource.GROUP
        version = (
            resource.API_VERSION.split("/")[-1]
            if "/" in resource.API_VERSION
            else resource.API_VERSION
        )
        plural = resource.PLURAL

        logger.debug(
            "kubernetes_apply",
            kind=resource.KIND,
            name=name,
            namespace=ns,
        )

        # Try to get the resource first to determine if we create or patch
        try:
            await self.get_custom_resource(
                group=group,
                version=version,
                plural=plural,
                name=name,
                namespace=ns,
            )
            # Resource exists, patch it
            return await self.patch_custom_resource(
                group=group,
                version=version,
                plural=plural,
                name=name,
                patch=resource_dict,
                namespace=ns,
            )
        except ResourceNotFoundError:
            # Resource doesn't exist, create it
            return await self.create_custom_resource(
                group=group,
                version=version,
                plural=plural,
                resource=resource_dict,
                namespace=ns,
            )


@asynccontextmanager
async def kubernetes_client(
    settings: Settings | None = None,
    kubeconfig_path: Path | None = None,
    namespace: str = "default",
) -> AsyncGenerator[KubernetesAdapter, None]:
    """Context manager for Kubernetes client.

    This is a convenience function for creating and managing
    a KubernetesAdapter instance.

    Args:
        settings: Application settings. If provided, overrides other args.
        kubeconfig_path: Path to kubeconfig file.
        namespace: Default namespace.

    Yields:
        Connected KubernetesAdapter instance.

    Example:
        async with kubernetes_client(settings) as k8s:
            machines = await k8s.list_machines()
    """
    if settings:
        adapter = KubernetesAdapter.from_settings(settings)
    else:
        adapter = KubernetesAdapter(
            kubeconfig_path=kubeconfig_path,
            namespace=namespace,
        )

    try:
        await adapter.connect()
        yield adapter
    finally:
        await adapter.disconnect()
