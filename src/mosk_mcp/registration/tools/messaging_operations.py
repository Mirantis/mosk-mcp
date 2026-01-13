"""RabbitMQ messaging operations tools registration for MOSK MCP Server.

This module registers RabbitMQ messaging operations tools with the MCP server:
- get_rabbitmq_status: Get RabbitMQ cluster health and status
- list_rabbitmq_queues: List queues with backlog analysis
- get_rabbitmq_connections: Get connection pool information
- diagnose_rabbitmq_issue: Comprehensive RabbitMQ diagnostics

All tools are READ-ONLY and do not modify RabbitMQ state.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from pydantic import Field

from mosk_mcp.observability.logging import get_logger
from mosk_mcp.registration.utils import create_adapter_getters, with_logging_context
from mosk_mcp.tools.messaging_operations import (
    diagnose_rabbitmq_issue,
    get_rabbitmq_connections,
    get_rabbitmq_status,
    list_rabbitmq_queues,
)


if TYPE_CHECKING:
    from collections.abc import Callable

    from fastmcp import FastMCP

    from mosk_mcp.core.config import Settings
    from mosk_mcp.core.server_context import SSOServerContext


logger = get_logger(__name__)


def register_messaging_operations_tools(
    mcp: FastMCP, settings: Settings, context_getter: Callable[[], SSOServerContext | None]
) -> None:
    """Register RabbitMQ messaging operations tools with the MCP server.

    These tools provide RabbitMQ cluster monitoring and diagnostics capabilities.
    All tools are READ-ONLY.

    CLUSTER ROUTING:
    - All RabbitMQ tools -> MOSK cluster (openstack namespace, rabbitmq pods)

    MOSK has two RabbitMQ instances:
    - main: openstack-rabbitmq-rabbitmq-0 (most OpenStack services)
    - neutron: openstack-neutron-rabbitmq-rabbitmq-0 (Neutron-specific)

    Args:
        mcp: FastMCP server instance.
        settings: Application settings.
        context_getter: Function that returns the current global SSOServerContext.
    """

    get_mosk, _get_mcc = create_adapter_getters(context_getter)

    # =========================================================================
    # Read-Only RabbitMQ Tools
    # =========================================================================

    @mcp.tool(
        name="get_rabbitmq_status",
        description=(
            "Get RabbitMQ cluster health and status including node state, alarms, "
            "partitions, and virtual hosts. Checks memory usage and cluster health. "
            "Read-only operation."
        ),
    )
    async def _get_rabbitmq_status(
        rabbitmq_instance: Literal["main", "neutron"] = Field(
            default="main",
            description=(
                "RabbitMQ instance to query: 'main' for openstack-rabbitmq-rabbitmq-0, "
                "'neutron' for openstack-neutron-rabbitmq-rabbitmq-0"
            ),
        ),
        include_feature_flags: bool = Field(
            default=False, description="Include enabled feature flags in output"
        ),
    ) -> dict[str, Any]:
        """Get RabbitMQ cluster status and health."""
        async with with_logging_context("get_rabbitmq_status"):
            k8s = await get_mosk()  # MOSK: RabbitMQ cluster
            result = await get_rabbitmq_status(
                kubernetes_adapter=k8s,
                rabbitmq_instance=rabbitmq_instance,
                include_feature_flags=include_feature_flags,
            )
            return result.model_dump()

    @mcp.tool(
        name="list_rabbitmq_queues",
        description=(
            "List RabbitMQ queues with filtering and analysis. Shows message counts, "
            "consumer counts, and identifies stale queues (messages with no consumers). "
            "Supports filtering by virtual host. Read-only operation."
        ),
    )
    async def _list_rabbitmq_queues(
        rabbitmq_instance: Literal["main", "neutron"] = Field(
            default="main", description="RabbitMQ instance to query"
        ),
        vhost: str | None = Field(
            default=None,
            description=(
                "Filter by vhost (e.g., 'nova', 'neutron', 'cinder'). "
                "If not specified, queries all vhosts."
            ),
        ),
        show_empty: bool = Field(default=False, description="Include queues with zero messages"),
        include_consumers: bool = Field(
            default=True, description="Include consumer count per queue"
        ),
        limit: int = Field(
            default=100, description="Maximum number of queues to return", ge=1, le=1000
        ),
    ) -> dict[str, Any]:
        """List RabbitMQ queues with analysis."""
        async with with_logging_context("list_rabbitmq_queues"):
            k8s = await get_mosk()  # MOSK: RabbitMQ queues
            result = await list_rabbitmq_queues(
                kubernetes_adapter=k8s,
                rabbitmq_instance=rabbitmq_instance,
                vhost=vhost,
                show_empty=show_empty,
                include_consumers=include_consumers,
                limit=limit,
            )
            return result.model_dump()

    @mcp.tool(
        name="get_rabbitmq_connections",
        description=(
            "Get RabbitMQ connection pool information and health analysis. "
            "Shows connections grouped by user/service, identifies blocked connections, "
            "and checks pool utilization. Read-only operation."
        ),
    )
    async def _get_rabbitmq_connections(
        rabbitmq_instance: Literal["main", "neutron"] = Field(
            default="main", description="RabbitMQ instance to query"
        ),
        include_channels: bool = Field(
            default=False, description="Include channel information per connection"
        ),
        group_by_user: bool = Field(default=True, description="Group connections by user/service"),
        limit: int = Field(
            default=200, description="Maximum number of connections to return", ge=1, le=1000
        ),
    ) -> dict[str, Any]:
        """Get RabbitMQ connection pool information."""
        async with with_logging_context("get_rabbitmq_connections"):
            k8s = await get_mosk()  # MOSK: RabbitMQ connections
            result = await get_rabbitmq_connections(
                kubernetes_adapter=k8s,
                rabbitmq_instance=rabbitmq_instance,
                include_channels=include_channels,
                group_by_user=group_by_user,
                limit=limit,
            )
            return result.model_dump()

    @mcp.tool(
        name="diagnose_rabbitmq_issue",
        description=(
            "Comprehensive RabbitMQ diagnostics. Checks cluster health, queue backlogs, "
            "connection pool status, and matches against known issue patterns. "
            "Can check both main and neutron instances. Read-only operation."
        ),
    )
    async def _diagnose_rabbitmq_issue(
        rabbitmq_instance: Literal["main", "neutron", "all"] = Field(
            default="all", description="RabbitMQ instance to diagnose ('all' checks both)"
        ),
        include_queue_analysis: bool = Field(
            default=True, description="Include queue depth and consumer analysis"
        ),
        include_connection_analysis: bool = Field(
            default=True, description="Include connection pool analysis"
        ),
        check_for_known_issues: bool = Field(
            default=True, description="Check against known RabbitMQ issue patterns"
        ),
    ) -> dict[str, Any]:
        """Diagnose RabbitMQ issues."""
        async with with_logging_context("diagnose_rabbitmq_issue"):
            k8s = await get_mosk()  # MOSK: RabbitMQ diagnostics
            result = await diagnose_rabbitmq_issue(
                kubernetes_adapter=k8s,
                rabbitmq_instance=rabbitmq_instance,
                include_queue_analysis=include_queue_analysis,
                include_connection_analysis=include_connection_analysis,
                check_for_known_issues=check_for_known_issues,
            )
            return result.model_dump()

    logger.debug("messaging_operations_tools_registered", count=4)
