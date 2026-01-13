"""Cluster management tools registration for MOSK MCP Server.

This module registers cluster management tools with the MCP server:
- list_clusters: List all configured MCC clusters
- switch_cluster: Switch to a different cluster
- current_cluster: Get active cluster info
- add_cluster: Add a new cluster configuration
- lock_cluster: Lock/unlock cluster switching

These tools enable safe multi-cluster management with production safeguards.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import Field

from mosk_mcp.observability.logging import get_logger
from mosk_mcp.registration.utils import with_logging_context


if TYPE_CHECKING:
    from collections.abc import Callable

    from fastmcp import FastMCP

    from mosk_mcp.core.config import Settings
    from mosk_mcp.core.server_context import SSOServerContext


logger = get_logger(__name__)


def register_cluster_tools(
    mcp: FastMCP, settings: Settings, context_getter: Callable[[], SSOServerContext | None]
) -> None:
    """Register cluster management tools with the MCP server.

    These tools provide safe multi-cluster management:
    - List available clusters and their status
    - Switch between clusters with safety checks
    - Lock clusters to prevent accidental switches
    - Add new cluster configurations

    Args:
        mcp: FastMCP server instance.
        settings: Application settings.
        context_getter: Function that returns the current global SSOServerContext.
    """

    # =========================================================================
    # List Clusters Tool
    # =========================================================================

    @mcp.tool(
        name="list_clusters",
        description=(
            "List all configured MCC clusters with their status and safety indicators. "
            "Shows cluster IDs, names, URLs, environments (development/staging/production), "
            "lock status, and which cluster is currently active. "
            "Use this to see available clusters before switching."
        ),
    )
    async def _list_clusters() -> dict[str, Any]:
        """List all configured MCC clusters."""
        async with with_logging_context("list_clusters"):
            logger.info("list_clusters_tool_invoked")

            try:
                from mosk_mcp.tools.cluster import list_clusters

                result = await list_clusters()
                return result.model_dump()

            except Exception as e:
                logger.error("list_clusters_failed", error=str(e))
                return {
                    "active_cluster": None,
                    "clusters": [],
                    "total_count": 0,
                    "error": str(e),
                }

    # =========================================================================
    # Current Cluster Tool
    # =========================================================================

    @mcp.tool(
        name="current_cluster",
        description=(
            "Get detailed information about the currently active MCC cluster. "
            "Shows cluster configuration, authentication status, fingerprint verification, "
            "and any safety warnings. Use this to verify which cluster you're connected to."
        ),
    )
    async def _current_cluster() -> dict[str, Any]:
        """Get information about the currently active cluster."""
        async with with_logging_context("current_cluster"):
            logger.info("current_cluster_tool_invoked")

            try:
                from mosk_mcp.tools.cluster import current_cluster

                result = await current_cluster()
                return result.model_dump()

            except Exception as e:
                logger.error("current_cluster_failed", error=str(e))
                return {
                    "has_active_cluster": False,
                    "cluster": None,
                    "is_authenticated": False,
                    "error": str(e),
                }

    # =========================================================================
    # Switch Cluster Tool
    # =========================================================================

    @mcp.tool(
        name="switch_cluster",
        description=(
            "Switch to a different MCC cluster. "
            "SECURITY: Switching clusters clears ALL previous authentication state. "
            "Production clusters require confirm_production=True. "
            "Locked clusters require force=True (use with extreme caution). "
            "After switching, you must re-authenticate with login_secure."
        ),
    )
    async def _switch_cluster(
        cluster_id: str = Field(description="ID of the cluster to switch to (from list_clusters)"),
        confirm_production: bool = Field(
            default=False,
            description="Set to True to confirm switching to a production cluster",
        ),
        force: bool = Field(
            default=False,
            description="Force switch even if current cluster is locked (DANGEROUS)",
        ),
    ) -> dict[str, Any]:
        """Switch to a different MCC cluster."""
        async with with_logging_context("switch_cluster"):
            logger.info(
                "switch_cluster_tool_invoked",
                cluster_id=cluster_id,
                confirm_production=confirm_production,
                force=force,
            )

            try:
                from mosk_mcp.cluster.manager import (
                    ClusterLockedError,
                    ClusterSecurityError,
                )
                from mosk_mcp.tools.cluster import switch_cluster

                result = await switch_cluster(
                    cluster_id=cluster_id,
                    confirm_production=confirm_production,
                    force=force,
                )

                # If switch was successful, clear the current session
                if result.success and result.session_cleared:
                    context = context_getter()
                    if context:
                        await context.logout()
                        logger.info(
                            "switch_cluster_session_cleared",
                            new_cluster=cluster_id,
                        )
                    else:
                        logger.warning("switch_cluster_no_context_for_logout")

                return result.model_dump()

            except ClusterLockedError as e:
                logger.warning(
                    "switch_cluster_locked",
                    cluster_id=cluster_id,
                    error=str(e),
                )
                return {
                    "success": False,
                    "error": str(e),
                    "error_type": "cluster_locked",
                    "message": (
                        "Cannot switch from locked cluster. "
                        "Use force=True to override (dangerous) or unlock the cluster first."
                    ),
                }

            except ClusterSecurityError as e:
                logger.error(
                    "switch_cluster_security_error",
                    cluster_id=cluster_id,
                    error=str(e),
                )
                return {
                    "success": False,
                    "error": str(e),
                    "error_type": "security_error",
                    "message": str(e),
                }

            except ValueError as e:
                logger.warning(
                    "switch_cluster_invalid",
                    cluster_id=cluster_id,
                    error=str(e),
                )
                return {
                    "success": False,
                    "error": str(e),
                    "error_type": "invalid_cluster",
                    "message": str(e),
                }

            except Exception as e:
                logger.error(
                    "switch_cluster_failed",
                    cluster_id=cluster_id,
                    error=str(e),
                )
                return {
                    "success": False,
                    "error": str(e),
                    "error_type": "unknown",
                    "message": f"Failed to switch cluster: {e}",
                }

    # =========================================================================
    # Add Cluster Tool
    # =========================================================================

    @mcp.tool(
        name="add_cluster",
        description=(
            "Add a new MCC cluster configuration. "
            "SECURITY: Production clusters MUST use HTTPS and SSL verification. "
            "The cluster fingerprint is captured on first authentication to prevent "
            "man-in-the-middle attacks and cluster identity spoofing."
        ),
    )
    async def _add_cluster(
        cluster_id: str = Field(
            description="Unique identifier (e.g., 'prod', 'staging'). "
            "Must start with a letter and contain only alphanumeric, - or _"
        ),
        url: str = Field(description="MCC cluster URL (must be https:// for production)"),
        name: str | None = Field(
            default=None,
            description="Human-readable display name (defaults to cluster_id)",
        ),
        environment: str = Field(
            default="development",
            description="Environment type: 'development', 'staging', or 'production'",
        ),
        ssl_verify: bool = Field(
            default=True,
            description="Verify SSL certificates (required for production)",
        ),
        description: str | None = Field(
            default=None,
            description="Optional description of this cluster",
        ),
        set_active: bool = Field(
            default=False,
            description="Make this the active cluster after adding",
        ),
    ) -> dict[str, Any]:
        """Add a new MCC cluster configuration."""
        async with with_logging_context("add_cluster"):
            logger.info(
                "add_cluster_tool_invoked",
                cluster_id=cluster_id,
                url=url,
                environment=environment,
            )

            try:
                from mosk_mcp.tools.cluster import add_cluster

                result = await add_cluster(
                    cluster_id=cluster_id,
                    url=url,
                    name=name,
                    environment=environment,
                    ssl_verify=ssl_verify,
                    description=description,
                    set_active=set_active,
                )

                return result.model_dump()

            except ValueError as e:
                logger.warning(
                    "add_cluster_invalid",
                    cluster_id=cluster_id,
                    error=str(e),
                )
                return {
                    "success": False,
                    "cluster_id": cluster_id,
                    "cluster_url": url,
                    "is_active": False,
                    "error": str(e),
                    "error_type": "validation_error",
                    "message": str(e),
                }

            except Exception as e:
                logger.error(
                    "add_cluster_failed",
                    cluster_id=cluster_id,
                    error=str(e),
                )
                return {
                    "success": False,
                    "cluster_id": cluster_id,
                    "cluster_url": url,
                    "is_active": False,
                    "error": str(e),
                    "error_type": "unknown",
                    "message": f"Failed to add cluster: {e}",
                }

    # =========================================================================
    # Lock Cluster Tool
    # =========================================================================

    @mcp.tool(
        name="lock_cluster",
        description=(
            "Lock or unlock a cluster to prevent accidental switches. "
            "Locked clusters require force=True in switch_cluster to switch away. "
            "Use this when performing critical operations on production to prevent "
            "accidentally targeting the wrong cluster."
        ),
    )
    async def _lock_cluster(
        cluster_id: str | None = Field(
            default=None,
            description="Cluster to lock/unlock (defaults to active cluster)",
        ),
        lock: bool = Field(
            default=True,
            description="True to lock, False to unlock",
        ),
    ) -> dict[str, Any]:
        """Lock or unlock a cluster."""
        async with with_logging_context("lock_cluster"):
            logger.info(
                "lock_cluster_tool_invoked",
                cluster_id=cluster_id or "(active)",
                lock=lock,
            )

            try:
                from mosk_mcp.tools.cluster import lock_cluster

                result = await lock_cluster(
                    cluster_id=cluster_id,
                    lock=lock,
                )

                return result.model_dump()

            except ValueError as e:
                logger.warning(
                    "lock_cluster_invalid",
                    cluster_id=cluster_id,
                    error=str(e),
                )
                return {
                    "success": False,
                    "cluster_id": cluster_id or "(unknown)",
                    "is_locked": False,
                    "error": str(e),
                    "message": str(e),
                }

            except Exception as e:
                logger.error(
                    "lock_cluster_failed",
                    cluster_id=cluster_id,
                    error=str(e),
                )
                return {
                    "success": False,
                    "cluster_id": cluster_id or "(unknown)",
                    "is_locked": False,
                    "error": str(e),
                    "message": f"Failed to lock cluster: {e}",
                }

    logger.debug("cluster_tools_registered", count=5)
