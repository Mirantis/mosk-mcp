"""Registration utilities for MOSK MCP tools.

This module provides shared utilities for tool registration to eliminate
code duplication across registration modules.

Usage:
    from mosk_mcp.registration.utils import (
        create_adapter_getters,
        with_logging_context,
    )

    # In register_*_tools function:
    get_mosk, get_mcc = create_adapter_getters(context)

    # In tool handler:
    async with with_logging_context("my_tool"):
        k8s = await get_mosk()
        ...
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Callable, Coroutine
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from mosk_mcp.observability.logging import LoggingContext, get_logger


if TYPE_CHECKING:
    from mosk_mcp.adapters.kubernetes import KubernetesAdapter
    from mosk_mcp.core.server_context import SSOServerContext


logger = get_logger(__name__)


# Type alias for adapter getter functions
AdapterGetter = Callable[[], Coroutine[Any, Any, "KubernetesAdapter"]]


def create_adapter_getters(
    context_getter: Callable[[], SSOServerContext | None],
) -> tuple[AdapterGetter, AdapterGetter]:
    """Create MOSK and MCC adapter getter functions.

    This factory function creates the standard adapter getter closures.
    It uses a context getter to ensure that the current live server context
    is accessed at runtime, preventing stale context issues.

    Args:
        context_getter: Function that returns the current global SSOServerContext.

    Returns:
        Tuple of (get_mosk_adapter, get_mcc_adapter) async functions.
    """

    async def get_mosk_adapter() -> KubernetesAdapter:
        """Get the MOSK adapter for workload cluster operations."""
        context = context_getter()
        if not context:
            raise RuntimeError("Server context not initialized")
        return await context.get_mosk_adapter()

    async def get_mcc_adapter() -> KubernetesAdapter:
        """Get the MCC adapter for management cluster operations."""
        context = context_getter()
        if not context:
            raise RuntimeError("Server context not initialized")
        return await context.get_mcc_adapter()

    return get_mosk_adapter, get_mcc_adapter


@asynccontextmanager
async def with_logging_context(tool_name: str, request_id: str | None = None) -> AsyncIterator[str]:
    """Async context manager for tool logging with auto-generated request ID.

    This utility wraps the common pattern of generating a request ID and
    entering a LoggingContext, which was previously repeated 73+ times
    across tool handlers:

        request_id = str(uuid.uuid4())
        async with LoggingContext(request_id=request_id, tool_name="..."):
            ...

    Args:
        tool_name: Name of the tool for logging context.
        request_id: Optional request ID. If not provided, generates a UUID.

    Yields:
        The generated or provided request_id for use in the tool handler.

    Example:
        async with with_logging_context("get_ceph_health") as request_id:
            # request_id is available if needed
            result = await some_operation()
    """
    if request_id is None:
        request_id = str(uuid.uuid4())

    async with LoggingContext(request_id=request_id, tool_name=tool_name):
        yield request_id


def generate_request_id() -> str:
    """Generate a new request ID for tool execution.

    Returns:
        A UUID string for request correlation.
    """
    return str(uuid.uuid4())


__all__ = [
    "AdapterGetter",
    "create_adapter_getters",
    "generate_request_id",
    "with_logging_context",
]
