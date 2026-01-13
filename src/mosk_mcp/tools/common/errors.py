"""Error handling utilities for MCP tools.

This module provides decorators for consistent error handling across tools,
reducing boilerplate try-catch code.
"""

from __future__ import annotations

import functools
import inspect
import time
from typing import TYPE_CHECKING, Any, TypeVar

from mosk_mcp.core.exceptions import KubernetesError, ToolExecutionError
from mosk_mcp.observability.logging import get_logger


if TYPE_CHECKING:
    from collections.abc import Callable

logger = get_logger(__name__)

F = TypeVar("F", bound="Callable[..., Any]")


def _handle_exception(
    exc: Exception,
    tool_name: str,
    *,
    wrap_kubernetes_errors: bool,
    log_errors: bool,
    exc_logger: Any,
    error_event: str = "tool_execution_failed",
    k8s_error_event: str = "kubernetes_error_wrapped",
) -> None:
    """Handle exceptions in tool execution with consistent behavior.

    This is the shared error handling logic used by both handle_tool_errors
    and tool_handler decorators.

    Args:
        exc: The exception that was raised.
        tool_name: Name of the tool for error messages.
        wrap_kubernetes_errors: If True, KubernetesErrors are preserved (re-raised).
            If False, they are wrapped in ToolExecutionError.
        log_errors: If True, errors are logged before re-raising.
        exc_logger: Logger instance to use for error logging.
        error_event: Event name for general errors (default: "tool_execution_failed").
        k8s_error_event: Event name for k8s errors (default: "kubernetes_error_wrapped").

    Raises:
        ToolExecutionError: Always raises, wrapping the original exception.
        KubernetesError: Re-raised if wrap_kubernetes_errors is True.
    """
    if isinstance(exc, ToolExecutionError):
        raise exc

    if isinstance(exc, KubernetesError):
        if wrap_kubernetes_errors:
            raise exc
        if log_errors:
            exc_logger.error(
                k8s_error_event,
                tool_name=tool_name,
                error=str(exc),
                error_type=type(exc).__name__,
            )
        raise ToolExecutionError(
            message=f"Failed to execute {tool_name}: {exc}",
            tool_name=tool_name,
            details={"error": str(exc), "error_type": type(exc).__name__},
        ) from exc

    # General exception
    if log_errors:
        exc_logger.error(
            error_event,
            tool_name=tool_name,
            error=str(exc),
            error_type=type(exc).__name__,
        )
    raise ToolExecutionError(
        message=f"Failed to execute {tool_name}: {exc}",
        tool_name=tool_name,
        details={"error": str(exc), "error_type": type(exc).__name__},
    ) from exc


def wrap_kubernetes_error(
    exception: Exception,
    operation: str,
    resource_kind: str,
    namespace: str | None = None,
    resource_name: str | None = None,
) -> KubernetesError:
    """Wrap an exception in a KubernetesError with context.

    This utility function creates a properly formatted KubernetesError
    with consistent details.

    Args:
        exception: The original exception to wrap.
        operation: The Kubernetes operation that failed (e.g., "list", "get", "create").
        resource_kind: The kind of resource (e.g., "Machine", "Pod").
        namespace: Optional namespace of the resource.
        resource_name: Optional name of the resource.

    Returns:
        KubernetesError with full context.

    Example:
        >>> try:
        ...     await k8s.get_machine(name="test")
        ... except Exception as e:
        ...     raise wrap_kubernetes_error(
        ...         e, "get", "Machine", namespace="default", resource_name="test"
        ...     )
    """
    return KubernetesError(
        message=f"Failed to {operation} {resource_kind}: {exception}",
        operation=operation,
        resource_kind=resource_kind,
        resource_name=resource_name,
        namespace=namespace,
        details={"original_error": str(exception)},
    )


def tool_handler(
    tool_name: str,
    *,
    log_start: bool = True,
    log_complete: bool = True,
    log_errors: bool = True,
    wrap_kubernetes_errors: bool = True,
    track_duration: bool = True,
) -> Callable[[F], F]:
    """Decorator for standardized tool error handling and logging.

    This decorator provides comprehensive tool lifecycle management:
    1. Start/complete logging with optional duration tracking
    2. Consistent error handling and wrapping
    3. Automatic parameter extraction for logging

    This is the recommended decorator for all MCP tool functions as it
    reduces boilerplate and ensures consistent behavior.

    Args:
        tool_name: Name of the tool for logging and error messages.
        log_start: If True, log when tool execution starts.
        log_complete: If True, log when tool execution completes.
        log_errors: If True, log errors before re-raising.
        wrap_kubernetes_errors: If True, preserve KubernetesErrors.
            If False, wrap them in ToolExecutionError.
        track_duration: If True, include duration_seconds in completion log.

    Returns:
        Decorated function.

    Example:
        >>> @tool_handler("get_ceph_health")
        ... async def get_ceph_health(adapter, input_data):
        ...     # Tool implementation
        ...     return result

        This replaces the common pattern of:
        >>> async def get_ceph_health(adapter, input_data):
        ...     logger.info("get_ceph_health_start", ...)
        ...     try:
        ...         result = ...
        ...         logger.info("get_ceph_health_complete", ...)
        ...         return result
        ...     except Exception as e:
        ...         logger.error("get_ceph_health_error", ...)
        ...         raise ToolExecutionError(...) from e
    """

    def _extract_log_params(args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, Any]:
        """Extract meaningful parameters for logging, skipping adapters."""
        log_params: dict[str, Any] = {}
        skip_suffixes = ("_adapter", "_client", "adapter", "client")

        for key, value in kwargs.items():
            # Skip adapter/client arguments
            if any(key.endswith(suffix) for suffix in skip_suffixes):
                continue
            # Skip internal arguments
            if key.startswith("_"):
                continue
            # For input_data objects, try to extract key fields
            if key == "input_data" and hasattr(value, "model_dump"):
                try:
                    # Get a subset of input fields for logging
                    dumped = value.model_dump(exclude_unset=True)
                    # Limit to first few keys to avoid log spam
                    for k, v in list(dumped.items())[:5]:
                        if v is not None:
                            log_params[k] = v
                except Exception as e:
                    logger.debug(
                        "input_data_extraction_failed",
                        error=str(e),
                        error_type=type(e).__name__,
                    )
            elif isinstance(value, (str, int, float, bool)):
                log_params[key] = value

        return log_params

    def decorator(func: F) -> F:
        func_logger = get_logger(func.__module__)

        if inspect.iscoroutinefunction(func):

            @functools.wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                start_time = time.monotonic() if track_duration else 0

                if log_start:
                    log_params = _extract_log_params(args, kwargs)
                    func_logger.info(f"{tool_name}_start", **log_params)

                try:
                    result = await func(*args, **kwargs)

                    if log_complete:
                        complete_params: dict[str, Any] = {}
                        if track_duration:
                            complete_params["duration_seconds"] = round(
                                time.monotonic() - start_time, 3
                            )
                        func_logger.info(f"{tool_name}_complete", **complete_params)

                    return result

                except Exception as e:
                    _handle_exception(
                        e,
                        tool_name,
                        wrap_kubernetes_errors=wrap_kubernetes_errors,
                        log_errors=log_errors,
                        exc_logger=func_logger,
                        error_event=f"{tool_name}_error",
                        k8s_error_event=f"{tool_name}_error",
                    )

            return async_wrapper  # type: ignore[return-value]

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            start_time = time.monotonic() if track_duration else 0

            if log_start:
                log_params = _extract_log_params(args, kwargs)
                func_logger.info(f"{tool_name}_start", **log_params)

            try:
                result = func(*args, **kwargs)

                if log_complete:
                    complete_params: dict[str, Any] = {}
                    if track_duration:
                        complete_params["duration_seconds"] = round(
                            time.monotonic() - start_time, 3
                        )
                    func_logger.info(f"{tool_name}_complete", **complete_params)

                return result

            except Exception as e:
                _handle_exception(
                    e,
                    tool_name,
                    wrap_kubernetes_errors=wrap_kubernetes_errors,
                    log_errors=log_errors,
                    exc_logger=func_logger,
                    error_event=f"{tool_name}_error",
                    k8s_error_event=f"{tool_name}_error",
                )

        return sync_wrapper  # type: ignore[return-value]

    return decorator
