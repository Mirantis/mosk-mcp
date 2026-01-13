"""Ceph operations tools registration for MOSK MCP Server.

This module registers read-only Ceph storage monitoring tools with the MCP server:
- get_ceph_status: Get Ceph cluster health and status
- list_osds: List all OSDs with status
- get_osd_details: Get detailed OSD information
- get_ceph_capacity: Get storage capacity breakdown
- get_pg_status: Get placement group status
- predict_capacity: Forecast future capacity
- get_recovery_status: Track recovery progress

All tools are read-only. For Ceph modifications, use kubectl directly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from pydantic import Field

from mosk_mcp.observability.logging import get_logger
from mosk_mcp.registration.utils import create_adapter_getters, with_logging_context
from mosk_mcp.tools.ceph_operations import (
    get_ceph_capacity,
    get_ceph_status,
    get_osd_details,
    get_pg_status,
    get_recovery_status,
    list_osds,
    predict_capacity,
)


if TYPE_CHECKING:
    from collections.abc import Callable

    from fastmcp import FastMCP

    from mosk_mcp.core.config import Settings
    from mosk_mcp.core.server_context import SSOServerContext


logger = get_logger(__name__)


def register_ceph_operations_tools(
    mcp: FastMCP, settings: Settings, context_getter: Callable[[], SSOServerContext | None]
) -> None:
    """Register Ceph storage monitoring tools with the MCP server.

    These tools provide read-only Ceph cluster monitoring capabilities
    for MOSK environments using MiraCeph.

    All tools are READ_ONLY safety level.

    CLUSTER ROUTING:
    - All Ceph status tools -> MOSK cluster (CephCluster, rook-ceph namespace)

    Args:
        mcp: FastMCP server instance.
        settings: Application settings.
        context_getter: Function that returns the current global SSOServerContext.
    """

    get_mosk, _get_mcc = create_adapter_getters(context_getter)

    # =========================================================================
    # Read-Only Ceph Tools
    # =========================================================================

    @mcp.tool(
        name="get_ceph_status",
        description=(
            "Get overall Ceph cluster health and status including OSD state, "
            "PG distribution, capacity, and health checks. Read-only operation."
        ),
    )
    async def _get_ceph_status(
        include_health_details: bool = Field(
            default=True, description="Include detailed health check information"
        ),
        include_pg_summary: bool = Field(
            default=True, description="Include placement group summary"
        ),
    ) -> dict[str, Any]:
        """Get Ceph cluster status."""
        async with with_logging_context("get_ceph_status"):
            k8s = await get_mosk()  # MOSK: Ceph cluster status
            result = await get_ceph_status(
                kubernetes_adapter=k8s,
                include_health_details=include_health_details,
                include_pg_summary=include_pg_summary,
            )
            return result.model_dump()

    # list_osds
    @mcp.tool(
        name="list_osds",
        description=(
            "List all OSDs with status, host, and capacity information. "
            "Supports filtering by host or status. Read-only operation."
        ),
    )
    async def _list_osds(
        host_filter: str | None = Field(default=None, description="Filter OSDs by host name"),
        status_filter: Literal["all", "up", "down"] | None = Field(
            default=None, description="Filter by OSD status"
        ),
        include_performance: bool = Field(default=False, description="Include latency metrics"),
    ) -> dict[str, Any]:
        """List all OSDs in the Ceph cluster."""
        async with with_logging_context("list_osds"):
            k8s = await get_mosk()  # MOSK: OSD listing
            result = await list_osds(
                kubernetes_adapter=k8s,
                host_filter=host_filter,
                status_filter=status_filter,
                include_performance=include_performance,
            )
            return result.model_dump()

    # get_osd_details
    @mcp.tool(
        name="get_osd_details",
        description=(
            "Get detailed information about a specific OSD including capacity, "
            "PG distribution, and performance metrics. Read-only operation."
        ),
    )
    async def _get_osd_details(
        osd_id: int = Field(..., description="OSD identifier", ge=0),
        include_pg_distribution: bool = Field(
            default=True, description="Include PG distribution info"
        ),
        include_performance: bool = Field(default=True, description="Include performance metrics"),
    ) -> dict[str, Any]:
        """Get detailed OSD information."""
        async with with_logging_context("get_osd_details"):
            k8s = await get_mosk()  # MOSK: OSD details
            result = await get_osd_details(
                kubernetes_adapter=k8s,
                osd_id=osd_id,
                include_pg_distribution=include_pg_distribution,
                include_performance=include_performance,
            )
            return result.model_dump()

    # get_ceph_capacity
    @mcp.tool(
        name="get_ceph_capacity",
        description=(
            "Get storage capacity breakdown by pool including total, used, "
            "available, and per-pool utilization. Read-only operation."
        ),
    )
    async def _get_ceph_capacity(
        include_pools: bool = Field(
            default=True, description="Include per-pool capacity breakdown"
        ),
        include_classes: bool = Field(default=True, description="Include capacity by device class"),
    ) -> dict[str, Any]:
        """Get Ceph storage capacity information."""
        async with with_logging_context("get_ceph_capacity"):
            k8s = await get_mosk()  # MOSK: Ceph capacity
            result = await get_ceph_capacity(
                kubernetes_adapter=k8s,
                include_pools=include_pools,
                include_classes=include_classes,
            )
            return result.model_dump()

    # get_pg_status
    @mcp.tool(
        name="get_pg_status",
        description=(
            "Get placement group health and states including active+clean count, "
            "stuck PGs, and recovery progress. Read-only operation."
        ),
    )
    async def _get_pg_status(
        include_stuck: bool = Field(default=True, description="Include stuck PG analysis"),
        include_recovery: bool = Field(default=True, description="Include recovery progress"),
    ) -> dict[str, Any]:
        """Get PG status information."""
        async with with_logging_context("get_pg_status"):
            k8s = await get_mosk()  # MOSK: PG status
            result = await get_pg_status(
                kubernetes_adapter=k8s,
                include_stuck=include_stuck,
                include_recovery=include_recovery,
            )
            return result.model_dump()

    # predict_capacity
    @mcp.tool(
        name="predict_capacity",
        description=(
            "Forecast future storage capacity based on growth trends. "
            "Predicts when warning/critical thresholds will be reached. Read-only operation."
        ),
    )
    async def _predict_capacity(
        days_to_forecast: int = Field(
            default=30, description="Number of days to forecast", ge=1, le=365
        ),
        growth_rate_gb_per_day: float | None = Field(
            default=None, description="Override growth rate in GB/day"
        ),
        include_recommendations: bool = Field(
            default=True, description="Include capacity planning recommendations"
        ),
    ) -> dict[str, Any]:
        """Predict future storage capacity."""
        async with with_logging_context("predict_capacity"):
            k8s = await get_mosk()  # MOSK: Capacity prediction
            result = await predict_capacity(
                kubernetes_adapter=k8s,
                days_to_forecast=days_to_forecast,
                growth_rate_gb_per_day=growth_rate_gb_per_day,
                include_recommendations=include_recommendations,
            )
            return result.model_dump()

    # get_recovery_status
    @mcp.tool(
        name="get_recovery_status",
        description=(
            "Track data recovery/rebalancing progress including misplaced objects, "
            "recovery rate, and ETA. Read-only operation."
        ),
    )
    async def _get_recovery_status(
        include_pg_details: bool = Field(
            default=False, description="Include per-PG recovery details"
        ),
        include_osd_details: bool = Field(
            default=False, description="Include per-OSD recovery details"
        ),
    ) -> dict[str, Any]:
        """Get recovery/rebalancing status."""
        async with with_logging_context("get_recovery_status"):
            k8s = await get_mosk()  # MOSK: Recovery status
            result = await get_recovery_status(
                kubernetes_adapter=k8s,
                include_pg_details=include_pg_details,
                include_osd_details=include_osd_details,
            )
            return result.model_dump()

    logger.debug("ceph_operations_tools_registered", count=7)
