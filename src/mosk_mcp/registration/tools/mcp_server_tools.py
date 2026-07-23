"""MCP server utility tools registration (always-on).

This module registers core server tools that are always available:
- health_check: MCP server health and connection status
- server_info: Server version, transport, and enabled capabilities
- echo: Connectivity test
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from pydantic import Field

from mosk_mcp.infrastructure.version_checker import get_cached_version_info
from mosk_mcp.observability.logging import get_logger
from mosk_mcp.registration.models import ServerHealthResult, ServerInfo
from mosk_mcp.registration.utils import with_logging_context


if TYPE_CHECKING:
    from fastmcp import FastMCP

    from mosk_mcp.core.config import Settings


logger = get_logger(__name__)


def register_mcp_server_tools(
    mcp: FastMCP,
    settings: Settings,
    enabled_capabilities: Sequence[str],
) -> None:
    """Register always-on MCP server utility tools.

    These tools are registered unconditionally and are not controlled by
    ``MCP_TOOLS``.

    Args:
        mcp: FastMCP server instance.
        settings: Application settings.
        enabled_capabilities: Optional tool group ids enabled via ``MCP_TOOLS``,
            reported by ``server_info``.
    """
    capabilities = list(enabled_capabilities)

    @mcp.tool(
        name="health_check",
        description="Check the health of the MOSK MCP server and its connections",
    )
    async def health_check() -> ServerHealthResult:
        """Perform a health check of the server.

        Returns:
            ServerHealthResult with status and component checks.
        """
        async with with_logging_context("health_check"):
            logger.debug("health_check_started")

            checks: dict[str, dict] = {}

            checks["server"] = {"status": "healthy", "message": "Server is running"}

            checks["config"] = {
                "status": "healthy",
                "auth_enabled": settings.auth_enabled,
                "transport": settings.transport.value,
            }

            all_healthy = all(
                c.get("status") == "healthy" for c in checks.values() if isinstance(c, dict)
            )
            status = "healthy" if all_healthy else "degraded"

            result = ServerHealthResult(
                status=status,
                timestamp=datetime.now(UTC).isoformat(),
                version=settings.app_version,
                checks=checks,
            )

            logger.info("health_check_completed", status=status)

            return result

    @mcp.tool(
        name="server_info",
        description="Get information about the MOSK MCP server and its capabilities",
    )
    async def server_info() -> ServerInfo:
        """Get server information and capabilities.

        Returns:
            ServerInfo with server details and available capabilities.
        """
        async with with_logging_context("server_info"):
            logger.debug("server_info_requested")

            version_info = get_cached_version_info()
            mosk_version = version_info.version_string if version_info else None
            mosk_version_supported = version_info.is_compatible if version_info else None
            warnings = version_info.warnings if version_info else []

            info = ServerInfo(
                name=settings.app_name,
                version=settings.app_version,
                transport=settings.transport.value,
                auth_enabled=settings.auth_enabled,
                capabilities=capabilities,
                mosk_version=mosk_version,
                mosk_version_supported=mosk_version_supported,
                warnings=warnings,
            )

            logger.debug("server_info_returned", mosk_version=mosk_version)
            return info

    @mcp.tool(
        name="echo",
        description="Echo back a message - useful for testing connectivity",
    )
    async def echo(message: str = Field(..., description="Message to echo back")) -> str:
        """Echo back a message for testing.

        Args:
            message: The message to echo back.

        Returns:
            The same message with a prefix.
        """
        async with with_logging_context("echo"):
            logger.debug("echo_received", message_length=len(message))
            return f"[MOSK MCP] {message}"
