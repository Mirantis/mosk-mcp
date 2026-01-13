"""Audit logging utilities for MCP tools.

This module provides a context manager for consistent audit logging
across all MCP tools, reducing boilerplate code.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from mosk_mcp.auth.types import UserContext
    from mosk_mcp.observability.audit import AuditLevel, AuditLogger


@contextlib.asynccontextmanager
async def audit_tool_execution(
    tool_name: str,
    audit_logger: AuditLogger | None,
    context: UserContext | None,
    level: AuditLevel,
    details: dict[str, Any] | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
    """Context manager for automatic audit logging of tool execution.

    This context manager handles the common audit logging pattern:
    1. Log STARTED status on entry
    2. Log SUCCESS status on successful exit
    3. Log FAILURE status on exception

    Args:
        tool_name: Name of the tool being executed.
        audit_logger: AuditLogger instance (can be None to skip logging).
        context: UserContext for user information (can be None to skip logging).
        level: Audit level (READ, WRITE, PRIVILEGED).
        details: Additional details to include in audit logs.

    Yields:
        A mutable dictionary that can be updated with additional details
        during execution. These details will be included in the SUCCESS log.

    Example:
        >>> async with audit_tool_execution(
        ...     "list_machines",
        ...     audit_logger,
        ...     context,
        ...     AuditLevel.READ,
        ...     {"namespace": "default"},
        ... ) as audit_details:
        ...     result = await do_work()
        ...     audit_details["count"] = len(result)
        ...     return result
    """
    # Initialize result details that can be updated during execution
    result_details: dict[str, Any] = dict(details) if details else {}

    # Skip logging if no logger or context
    if not audit_logger or not context:
        yield result_details
        return

    from mosk_mcp.observability.audit import AuditCategory, AuditStatus

    # Log start
    await audit_logger.log(
        category=AuditCategory.TOOL_EXECUTION,
        level=level,
        status=AuditStatus.STARTED,
        user_id=context.user_id,
        username=context.username,
        action=f"tool:{tool_name}",
        tool_name=tool_name,
        details=result_details.copy(),
    )

    try:
        yield result_details

        # Log success
        await audit_logger.log(
            category=AuditCategory.TOOL_EXECUTION,
            level=level,
            status=AuditStatus.SUCCESS,
            user_id=context.user_id,
            username=context.username,
            action=f"tool:{tool_name}",
            tool_name=tool_name,
            details=result_details,
        )

    except Exception as e:
        # Log failure
        await audit_logger.log(
            category=AuditCategory.TOOL_EXECUTION,
            level=level,
            status=AuditStatus.FAILURE,
            user_id=context.user_id,
            username=context.username,
            action=f"tool:{tool_name}",
            tool_name=tool_name,
            error_message=str(e),
            details=result_details,
        )
        raise
