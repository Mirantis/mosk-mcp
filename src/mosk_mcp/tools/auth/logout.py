"""Logout MCP tool for ending user sessions.

This module provides the logout MCP tool that ends the current
authenticated session and cleans up resources.

Safety Level: Write (destroys authenticated session)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from mosk_mcp.core.exceptions import ToolExecutionError
from mosk_mcp.observability.logging import get_logger
from mosk_mcp.tools.auth.models import LogoutInput, LogoutOutput


if TYPE_CHECKING:
    from mosk_mcp.auth.session import UserSession

logger = get_logger(__name__)


async def logout(
    session: UserSession,
    input_data: LogoutInput,
) -> LogoutOutput:
    """End the current authenticated session.

    This tool logs out the current user and cleans up:
    - OIDC tokens
    - Generated kubeconfig files
    - Kubernetes adapter connections
    - StackLight client connections

    Safety Level: Write (destroys session state)

    Args:
        session: UserSession instance to logout.
        input_data: Logout parameters (currently none required).

    Returns:
        LogoutOutput with logout result.

    Raises:
        ToolExecutionError: If logout fails.

    Example:
        >>> output = await logout(session, LogoutInput())
        >>> print(output.message)
    """
    username = session.state.username

    logger.info(
        "logout_attempt",
        username=username,
    )

    try:
        if not session.state.authenticated:
            return LogoutOutput(
                success=True,
                message="No active session to logout",
            )

        await session.logout()

        logger.info(
            "logout_success",
            username=username,
        )

        return LogoutOutput(
            success=True,
            message=f"Successfully logged out user '{username}'",
        )

    except Exception as e:
        logger.error(
            "logout_error",
            username=username,
            error=str(e),
        )
        raise ToolExecutionError(
            message=f"Logout failed: {e}",
            tool_name="logout",
            details={"username": username},
        ) from e
