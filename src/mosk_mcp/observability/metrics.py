"""Prometheus metrics for MOSK MCP Server.

This module provides Prometheus metrics instrumentation including:
- Request counters (total invocations by tool and status)
- Request duration histograms
- Active connection gauges
- Authentication failure counters
- Privileged operation counters

Metrics are exposed via a /metrics endpoint for Prometheus scraping.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Generator
from contextlib import contextmanager
from contextvars import ContextVar
from enum import Enum
from functools import wraps
from typing import TYPE_CHECKING, Any, TypeVar

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    Info,
    generate_latest,
)

from mosk_mcp.observability.logging import get_logger


if TYPE_CHECKING:
    from mosk_mcp.core.config import Settings


logger = get_logger(__name__)


# Type variable for decorators
F = TypeVar("F", bound=Callable[..., Any])


class ToolStatus(str, Enum):
    """Status values for tool invocations."""

    SUCCESS = "success"
    ERROR = "error"
    VALIDATION_ERROR = "validation_error"
    AUTH_ERROR = "auth_error"
    TIMEOUT = "timeout"


class SafetyLevel(str, Enum):
    """Safety levels for tool operations."""

    READ_ONLY = "read_only"
    NON_DESTRUCTIVE = "non_destructive"
    PRIVILEGED = "privileged"


# Context variable to track current tool being executed
current_tool_var: ContextVar[str | None] = ContextVar("current_tool", default=None)


class MetricsRegistry:
    """Prometheus metrics registry for MOSK MCP Server.

    This class manages all Prometheus metrics and provides methods
    for recording metric values. It uses a custom registry to avoid
    conflicts with default metrics.

    Attributes:
        registry: Prometheus collector registry.
        requests_total: Counter for total tool invocations.
        request_duration_seconds: Histogram for tool execution duration.
        active_connections: Gauge for current MCP client connections.
        auth_failures_total: Counter for authentication failures.
        privileged_ops_total: Counter for privileged operations.
        server_info: Info metric with server metadata.
    """

    def __init__(self, settings: Settings) -> None:
        """Initialize the metrics registry.

        Args:
            settings: Application settings.
        """
        self.registry = CollectorRegistry()
        self.settings = settings

        # Total tool invocations counter
        self.requests_total = Counter(
            "mosk_mcp_requests_total",
            "Total number of MCP tool invocations",
            labelnames=["tool", "status", "safety_level"],
            registry=self.registry,
        )

        # Request duration histogram with appropriate buckets
        # Buckets from 10ms to 5 minutes, covering typical tool execution times
        self.request_duration_seconds = Histogram(
            "mosk_mcp_request_duration_seconds",
            "Duration of MCP tool execution in seconds",
            labelnames=["tool", "safety_level"],
            buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 300.0),
            registry=self.registry,
        )

        # Active MCP connections gauge
        self.active_connections = Gauge(
            "mosk_mcp_active_connections",
            "Number of currently active MCP client connections",
            registry=self.registry,
        )

        # Authentication failures counter
        self.auth_failures_total = Counter(
            "mosk_mcp_auth_failures_total",
            "Total number of authentication failures",
            labelnames=["reason", "auth_method"],
            registry=self.registry,
        )

        # Privileged operations counter (tracks CRQ usage)
        self.privileged_ops_total = Counter(
            "mosk_mcp_privileged_ops_total",
            "Total number of privileged operations executed",
            labelnames=["tool", "crq_number"],
            registry=self.registry,
        )

        # Tool errors by type
        self.tool_errors_total = Counter(
            "mosk_mcp_tool_errors_total",
            "Total number of tool execution errors by error type",
            labelnames=["tool", "error_type"],
            registry=self.registry,
        )

        # Kubernetes API call metrics
        self.k8s_requests_total = Counter(
            "mosk_mcp_k8s_requests_total",
            "Total number of Kubernetes API requests",
            labelnames=["operation", "resource_kind", "status"],
            registry=self.registry,
        )

        self.k8s_request_duration_seconds = Histogram(
            "mosk_mcp_k8s_request_duration_seconds",
            "Duration of Kubernetes API requests in seconds",
            labelnames=["operation", "resource_kind"],
            buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
            registry=self.registry,
        )

        # Server info metric
        self.server_info = Info(
            "mosk_mcp_server",
            "MOSK MCP Server information",
            registry=self.registry,
        )
        self.server_info.info(
            {
                "version": settings.app_version,
                "app_name": settings.app_name,
                "transport": settings.transport.value,
            }
        )

        logger.info(
            "metrics_registry_initialized",
            metrics_enabled=settings.metrics_enabled,
            metrics_port=settings.metrics_port,
        )

    def record_tool_invocation(
        self,
        tool: str,
        status: ToolStatus,
        duration_seconds: float,
        safety_level: SafetyLevel = SafetyLevel.READ_ONLY,
    ) -> None:
        """Record a tool invocation.

        Args:
            tool: Name of the tool that was invoked.
            status: Status of the invocation.
            duration_seconds: Duration of the invocation.
            safety_level: Safety level of the tool.
        """
        self.requests_total.labels(
            tool=tool,
            status=status.value,
            safety_level=safety_level.value,
        ).inc()

        self.request_duration_seconds.labels(
            tool=tool,
            safety_level=safety_level.value,
        ).observe(duration_seconds)

    def record_auth_failure(self, reason: str, auth_method: str = "api_key") -> None:
        """Record an authentication failure.

        Args:
            reason: Reason for the failure.
            auth_method: Authentication method that failed.
        """
        self.auth_failures_total.labels(
            reason=reason,
            auth_method=auth_method,
        ).inc()
        logger.debug("auth_failure_recorded", reason=reason, auth_method=auth_method)

    def record_privileged_operation(self, tool: str, crq_number: str) -> None:
        """Record a privileged operation.

        Args:
            tool: Name of the privileged tool.
            crq_number: Change request number authorizing the operation.
        """
        self.privileged_ops_total.labels(
            tool=tool,
            crq_number=crq_number,
        ).inc()
        logger.debug("privileged_operation_recorded", tool=tool, crq_number=crq_number)

    def record_tool_error(self, tool: str, error_type: str) -> None:
        """Record a tool error.

        Args:
            tool: Name of the tool that errored.
            error_type: Type of the error (exception class name).
        """
        self.tool_errors_total.labels(
            tool=tool,
            error_type=error_type,
        ).inc()

    def record_k8s_request(
        self,
        operation: str,
        resource_kind: str,
        status: str,
        duration_seconds: float,
    ) -> None:
        """Record a Kubernetes API request.

        Args:
            operation: The API operation (get, list, create, delete, etc.).
            resource_kind: The kind of resource accessed.
            status: Status of the request (success, error).
            duration_seconds: Duration of the request.
        """
        self.k8s_requests_total.labels(
            operation=operation,
            resource_kind=resource_kind,
            status=status,
        ).inc()

        self.k8s_request_duration_seconds.labels(
            operation=operation,
            resource_kind=resource_kind,
        ).observe(duration_seconds)

    def increment_connections(self) -> None:
        """Increment the active connections gauge."""
        self.active_connections.inc()

    def decrement_connections(self) -> None:
        """Decrement the active connections gauge."""
        self.active_connections.dec()

    @contextmanager
    def track_connection(self) -> Generator[None, None, None]:
        """Context manager for tracking connection lifetime."""
        self.increment_connections()
        try:
            yield
        finally:
            self.decrement_connections()

    @contextmanager
    def track_tool_execution(
        self,
        tool: str,
        safety_level: SafetyLevel = SafetyLevel.READ_ONLY,
    ) -> Generator[None, None, None]:
        """Context manager for tracking tool execution.

        Args:
            tool: Name of the tool being executed.
            safety_level: Safety level of the tool.

        Yields:
            None - execution proceeds within the context.
        """
        start_time = time.time()
        token = current_tool_var.set(tool)
        status = ToolStatus.SUCCESS

        try:
            yield
        except Exception as e:
            status = self._classify_error(e)
            self.record_tool_error(tool, type(e).__name__)
            raise
        finally:
            duration = time.time() - start_time
            self.record_tool_invocation(tool, status, duration, safety_level)
            current_tool_var.reset(token)

    def _classify_error(self, error: Exception) -> ToolStatus:
        """Classify an error into a tool status.

        Args:
            error: The exception to classify.

        Returns:
            Appropriate ToolStatus for the error.
        """
        from mosk_mcp.core.exceptions import (
            AuthenticationError,
            AuthorizationError,
            ValidationError,
        )

        error_type = type(error).__name__

        if isinstance(error, ValidationError):
            return ToolStatus.VALIDATION_ERROR
        elif isinstance(error, (AuthenticationError, AuthorizationError)):
            return ToolStatus.AUTH_ERROR
        elif "timeout" in error_type.lower() or "Timeout" in str(error):
            return ToolStatus.TIMEOUT
        else:
            return ToolStatus.ERROR

    def generate_metrics(self) -> bytes:
        """Generate Prometheus metrics output.

        Returns:
            Metrics in Prometheus text format.
        """
        return generate_latest(self.registry)

    def get_content_type(self) -> str:
        """Get the content type for metrics output.

        Returns:
            Content type string for Prometheus metrics.
        """
        return CONTENT_TYPE_LATEST


# Global metrics registry instance
_metrics_registry: MetricsRegistry | None = None


def get_metrics_registry() -> MetricsRegistry | None:
    """Get the global metrics registry.

    Returns:
        The metrics registry, or None if not initialized.
    """
    return _metrics_registry


def init_metrics_registry(settings: Settings) -> MetricsRegistry:
    """Initialize the global metrics registry.

    Args:
        settings: Application settings.

    Returns:
        Initialized metrics registry.
    """
    global _metrics_registry
    _metrics_registry = MetricsRegistry(settings)
    return _metrics_registry


def create_metrics_app(metrics_registry: MetricsRegistry) -> Any:
    """Create a Starlette application for the metrics endpoint.

    This creates a separate ASGI application that serves the /metrics
    endpoint independently of the MCP server.

    Args:
        metrics_registry: The metrics registry to expose.

    Returns:
        Starlette application with metrics endpoint.
    """
    from starlette.applications import Starlette
    from starlette.responses import Response
    from starlette.routing import Route

    async def metrics_endpoint(request: Any) -> Response:
        """Handle metrics requests."""
        metrics_output = metrics_registry.generate_metrics()
        return Response(
            content=metrics_output,
            media_type=metrics_registry.get_content_type(),
        )

    routes = [
        Route("/", metrics_endpoint),
        Route("/metrics", metrics_endpoint),
    ]

    app = Starlette(routes=routes)
    return app


def track_tool(
    name: str | None = None,
    safety_level: SafetyLevel = SafetyLevel.READ_ONLY,
) -> Callable[[F], F]:
    """Decorator for tracking tool execution metrics.

    Args:
        name: Optional tool name override.
        safety_level: Safety level of the tool.

    Returns:
        Decorated function with metrics tracking.

    Example:
        @track_tool(safety_level=SafetyLevel.PRIVILEGED)
        async def my_privileged_tool(...):
            ...
    """

    def decorator(func: F) -> F:
        tool_name = name or func.__name__

        @wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            registry = get_metrics_registry()
            if registry is None:
                # Metrics not enabled, just run the function
                return await func(*args, **kwargs)

            with registry.track_tool_execution(tool_name, safety_level):
                return await func(*args, **kwargs)

        @wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            registry = get_metrics_registry()
            if registry is None:
                return func(*args, **kwargs)

            with registry.track_tool_execution(tool_name, safety_level):
                return func(*args, **kwargs)

        # Return appropriate wrapper based on function type
        import inspect

        if inspect.iscoroutinefunction(func):
            return async_wrapper  # type: ignore[return-value]
        else:
            return sync_wrapper  # type: ignore[return-value]

    return decorator


def record_privileged_op(tool: str, crq_number: str) -> None:
    """Convenience function to record a privileged operation.

    Args:
        tool: Name of the tool.
        crq_number: Change request number.
    """
    registry = get_metrics_registry()
    if registry is not None:
        registry.record_privileged_operation(tool, crq_number)


def record_auth_failure(reason: str, auth_method: str = "api_key") -> None:
    """Convenience function to record an authentication failure.

    Args:
        reason: Reason for the failure.
        auth_method: Authentication method that failed.
    """
    registry = get_metrics_registry()
    if registry is not None:
        registry.record_auth_failure(reason, auth_method)


def record_k8s_request(
    operation: str,
    resource_kind: str,
    status: str,
    duration_seconds: float,
) -> None:
    """Convenience function to record a Kubernetes API request.

    Args:
        operation: The API operation.
        resource_kind: The kind of resource.
        status: Status of the request.
        duration_seconds: Duration of the request.
    """
    registry = get_metrics_registry()
    if registry is not None:
        registry.record_k8s_request(operation, resource_kind, status, duration_seconds)
