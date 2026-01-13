"""Session status MCP tool for checking authentication state.

This module provides the session_status MCP tool that returns
information about the current user session.

Safety Level: Read-only
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from mosk_mcp.core.exceptions import ToolExecutionError
from mosk_mcp.observability.logging import get_logger
from mosk_mcp.tools.auth.models import SessionStatusInput, SessionStatusOutput


if TYPE_CHECKING:
    from mosk_mcp.auth.session import UserSession

logger = get_logger(__name__)


async def get_session_status(
    session: UserSession,
    input_data: SessionStatusInput,
) -> SessionStatusOutput:
    """Get current session status.

    Returns information about the current authentication state including:
    - Whether user is authenticated
    - Username and IAM roles
    - Token expiration status
    - Available adapters and clients

    Safety Level: Read-only

    Args:
        session: UserSession instance to check.
        input_data: Status parameters (currently none required).

    Returns:
        SessionStatusOutput with session information.

    Raises:
        ToolExecutionError: If status check fails.

    Example:
        >>> output = await get_session_status(session, SessionStatusInput())
        >>> print(f"Authenticated: {output.authenticated}")
        >>> print(f"Username: {output.username}")
    """
    logger.debug("session_status_check")

    try:
        status = session.get_status()

        return SessionStatusOutput(
            authenticated=status["authenticated"],
            username=status["username"],
            authenticated_at=status["authenticated_at"],
            last_activity=status["last_activity"],
            token_expires_at=status["token_expires_at"],
            token_expired=status["token_expired"],
            iam_roles=status["iam_roles"],
            has_mcc_adapter=status["has_mcc_adapter"],
            has_mosk_adapter=status["has_mosk_adapter"],
            has_stacklight_client=status["has_stacklight_client"],
        )

    except Exception as e:
        logger.error(
            "session_status_error",
            error=str(e),
        )
        raise ToolExecutionError(
            message=f"Session status check failed: {e}",
            tool_name="session_status",
            details={},
        ) from e
