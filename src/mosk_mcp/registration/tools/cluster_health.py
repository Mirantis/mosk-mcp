"""Cluster health monitoring tools registration for MOSK MCP Server.

This module registers cluster health monitoring tools with the MCP server:
- get_mosk_cluster_health: Comprehensive MOSK cluster health summary
- get_kubernetes_health: Kubernetes cluster health
- get_openstack_health: OpenStack service health
- get_ceph_health: Ceph storage cluster health
- list_active_alerts: List active StackLight/Prometheus alerts
- get_alert_details: Get detailed alert information
- run_preflight_check: Pre-operation readiness checks
- get_resource_utilization: CPU, memory, storage utilization
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from pydantic import Field

from mosk_mcp.observability.logging import get_logger
from mosk_mcp.registration.utils import create_adapter_getters, with_logging_context
from mosk_mcp.tools.cluster_health import (
    GetCephHealthInput,
    GetClusterHealthInput,
    GetKubernetesHealthInput,
    GetOpenStackHealthInput,
    GetResourceUtilizationInput,
    PreflightCheckType,
    RunPreflightCheckInput,
    get_ceph_health,
    get_kubernetes_health,
    get_mosk_cluster_health,
    get_openstack_health,
    get_resource_utilization,
    run_preflight_check,
)


if TYPE_CHECKING:
    from collections.abc import Callable

    from fastmcp import FastMCP

    from mosk_mcp.core.config import Settings
    from mosk_mcp.core.server_context import SSOServerContext


logger = get_logger(__name__)


def register_cluster_health_tools(
    mcp: FastMCP, settings: Settings, context_getter: Callable[[], SSOServerContext | None]
) -> None:
    """Register cluster health monitoring tools with the MCP server.

    These tools provide comprehensive health monitoring across all MOSK
    cluster layers including Kubernetes, OpenStack, Ceph, and StackLight.

    All tools are READ_ONLY safety level.

    CLUSTER ROUTING:
    - All health monitoring tools -> MOSK cluster (K8s, OpenStack, Ceph status)
    - Alerts and preflight checks -> MOSK cluster (StackLight/Prometheus)

    Args:
        mcp: FastMCP server instance.
        settings: Application settings.
        context_getter: Function that returns the current global SSOServerContext.
    """

    get_mosk, get_mcc = create_adapter_getters(context_getter)

    # =========================================================================
    # Unified Health Summary
    # =========================================================================

    # get_mosk_cluster_health - Comprehensive cluster health
    @mcp.tool(
        name="get_mosk_cluster_health",
        description=(
            "Get comprehensive MOSK cluster status and health - the primary tool for "
            "'cluster status' queries. Returns unified status across all layers: "
            "Platform (machines, release), Kubernetes (nodes, pods), OpenStack "
            "(services, endpoints), and Ceph (storage health, capacity). "
            "Provides weighted health scores and safety recommendations. Read-only."
        ),
    )
    async def _get_mosk_cluster_health(
        cluster_name: str | None = Field(
            default=None,
            description="MOSK Cluster CR name on MCC (e.g., 'mos'). If not provided, will auto-discover.",
        ),
        cluster_namespace: str = Field(
            default="default",
            description="Namespace where Cluster CR is defined on MCC (e.g., 'lab', 'default')",
        ),
        osdpl_name: str | None = Field(
            default=None,
            description="OSDPL name (e.g., 'mos', 'openstack'). If not provided, will auto-discover.",
        ),
        namespace: str = Field(
            default="openstack",
            description="Kubernetes namespace where OSDPL is deployed",
        ),
        include_component_details: bool = Field(
            default=True, description="Include per-component health details"
        ),
        include_recommendations: bool = Field(
            default=True, description="Include actionable recommendations"
        ),
    ) -> dict[str, Any]:
        """Get unified MOSK cluster health summary."""
        async with with_logging_context("get_mosk_cluster_health"):
            k8s = await get_mosk()  # MOSK: Unified health summary
            mcc = await get_mcc()  # MCC: Machine CRs for compute nodes

            # Use session's discovered cluster info if not explicitly provided
            effective_cluster_name = cluster_name
            effective_cluster_namespace = cluster_namespace

            context = context_getter()
            if context and context._session:
                if not effective_cluster_name and context._session.mosk_cluster_name:
                    effective_cluster_name = context._session.mosk_cluster_name
                    logger.debug(
                        "using_session_cluster_name",
                        cluster_name=effective_cluster_name,
                    )
                if (
                    effective_cluster_namespace == "default"
                    and context._session.mosk_cluster_namespace
                ):
                    effective_cluster_namespace = context._session.mosk_cluster_namespace
                    logger.debug(
                        "using_session_cluster_namespace",
                        cluster_namespace=effective_cluster_namespace,
                    )

            input_data = GetClusterHealthInput(
                cluster_name=effective_cluster_name,
                cluster_namespace=effective_cluster_namespace,
                osdpl_name=osdpl_name,
                namespace=namespace,
                include_component_details=include_component_details,
                include_recommendations=include_recommendations,
            )
            result = await get_mosk_cluster_health(k8s, input_data, mcc_adapter=mcc)
            return result.model_dump()

    # =========================================================================
    # Kubernetes Health
    # =========================================================================

    # get_kubernetes_health - Kubernetes cluster health
    @mcp.tool(
        name="get_kubernetes_health",
        description=(
            "Get Kubernetes cluster health including node readiness, system pod "
            "health, and API server status. Read-only operation."
        ),
    )
    async def _get_kubernetes_health(
        include_node_details: bool = Field(
            default=True, description="Include per-node health details"
        ),
        include_system_pods: bool = Field(default=True, description="Include system pod health"),
    ) -> dict[str, Any]:
        """Get Kubernetes cluster health."""
        async with with_logging_context("get_kubernetes_health"):
            k8s = await get_mosk()  # MOSK: K8s cluster health
            input_data = GetKubernetesHealthInput(
                include_node_details=include_node_details,
                include_system_pods=include_system_pods,
            )
            result = await get_kubernetes_health(k8s, input_data)
            return result.model_dump()

    # =========================================================================
    # OpenStack Health
    # =========================================================================

    # get_openstack_health - OpenStack service health
    @mcp.tool(
        name="get_openstack_health",
        description=(
            "Get OpenStack service health including control plane status, API "
            "endpoints, and compute hypervisor health. Read-only operation."
        ),
    )
    async def _get_openstack_health(
        osdpl_name: str = Field(
            ..., description="OpenStackDeployment name (e.g., 'mos', 'openstack'). Required."
        ),
        namespace: str = Field(
            default="openstack", description="Kubernetes namespace where OSDPL is deployed"
        ),
        include_endpoints: bool = Field(
            default=True, description="Include API endpoint health checks"
        ),
        include_services: bool = Field(default=True, description="Include per-service health"),
    ) -> dict[str, Any]:
        """Get OpenStack service health."""
        async with with_logging_context("get_openstack_health"):
            k8s = await get_mosk()  # MOSK: OpenStack service health
            mcc = await get_mcc()  # MCC: Machine CRs for compute nodes
            input_data = GetOpenStackHealthInput(
                osdpl_name=osdpl_name,
                namespace=namespace,
                include_endpoints=include_endpoints,
                include_services=include_services,
            )
            result = await get_openstack_health(k8s, input_data, mcc_adapter=mcc)
            return result.model_dump()

    # =========================================================================
    # Ceph Health
    # =========================================================================

    # get_ceph_health - Ceph storage cluster health
    @mcp.tool(
        name="get_ceph_health",
        description=(
            "Get Ceph storage cluster health including OSD status, PG health, "
            "and capacity utilization. Read-only operation."
        ),
    )
    async def _get_ceph_health(
        include_osd_details: bool = Field(
            default=False, description="Include per-OSD health details"
        ),
        include_pool_details: bool = Field(
            default=False, description="Include per-pool capacity details"
        ),
    ) -> dict[str, Any]:
        """Get Ceph storage cluster health."""
        async with with_logging_context("get_ceph_health"):
            k8s = await get_mosk()  # MOSK: Ceph cluster health
            input_data = GetCephHealthInput(
                include_osd_details=include_osd_details,
                include_pool_details=include_pool_details,
            )
            result = await get_ceph_health(k8s, input_data)
            return result.model_dump()

    # =========================================================================
    # Alert Management
    # =========================================================================

    # list_active_alerts - List active StackLight/Prometheus alerts
    @mcp.tool(
        name="list_active_alerts",
        description=(
            "List active StackLight/Prometheus alerts with filtering by severity "
            "and component. Read-only operation."
        ),
    )
    async def _list_active_alerts(
        severity_filter: Literal["critical", "warning", "info", "none"] | None = Field(
            default=None, description="Filter by severity level"
        ),
        component_filter: str | None = Field(
            default=None, description="Filter by component (kubernetes, openstack, ceph)"
        ),
        include_silenced: bool = Field(default=False, description="Include silenced alerts"),
        limit: int = Field(default=100, description="Maximum alerts to return", ge=1, le=500),
    ) -> dict[str, Any]:
        """List active alerts."""
        async with with_logging_context("list_active_alerts"):
            from mosk_mcp.tools.cluster_health.list_active_alerts import (
                list_active_alerts as list_alerts_impl,
            )
            from mosk_mcp.tools.cluster_health.models import ListActiveAlertsInput
            from mosk_mcp.tools.common.enums import AlertSeverity

            # Convert string severity to enum
            severity_enum = None
            if severity_filter:
                severity_enum = AlertSeverity(severity_filter)

            # Create input model
            input_data = ListActiveAlertsInput(
                severity_filter=severity_enum,
                component_filter=component_filter,
                include_silenced=include_silenced,
                limit=limit,
            )

            context = context_getter()
            if not context:
                raise RuntimeError("Server context not initialized")

            stacklight = await context.get_stacklight_client()
            result = await list_alerts_impl(
                direct_client=stacklight,
                input_data=input_data,
            )
            return result.model_dump()

    # get_alert_details - Get detailed alert information
    @mcp.tool(
        name="get_alert_details",
        description=(
            "Get detailed information about a specific alert including context, "
            "history, and suggested remediation actions. Read-only operation."
        ),
    )
    async def _get_alert_details(
        alert_name: str = Field(..., description="Alert name to get details for"),
        fingerprint: str | None = Field(
            default=None, description="Alert fingerprint for specific instance"
        ),
        include_history: bool = Field(default=False, description="Include alert history"),
    ) -> dict[str, Any]:
        """Get alert details."""
        async with with_logging_context("get_alert_details"):
            from mosk_mcp.tools.cluster_health.get_alert_details import (
                get_alert_details as get_alert_details_impl,
            )

            context = context_getter()
            if not context:
                raise RuntimeError("Server context not initialized")

            stacklight = await context.get_stacklight_client()
            result = await get_alert_details_impl(
                direct_client=stacklight,
                alert_name=alert_name,
                fingerprint=fingerprint,
                include_history=include_history,
            )
            return result.model_dump()

    # =========================================================================
    # Preflight Checks
    # =========================================================================

    # run_preflight_check - Pre-operation readiness checks
    @mcp.tool(
        name="run_preflight_check",
        description=(
            "Run pre-operation readiness checks for maintenance, upgrades, or "
            "node/OSD removal. Validates cluster state before operations. "
            "For UPGRADE type, validates upgrade path in KaasRelease and ClusterUpdatePlan. "
            "Read-only operation."
        ),
    )
    async def _run_preflight_check(
        check_type: Literal[
            "maintenance", "upgrade", "node_removal", "osd_removal", "general"
        ] = Field(..., description="Type of operation to check readiness for"),
        target_node: str | None = Field(
            default=None, description="Target node name (for node-specific checks)"
        ),
        target_osd: int | None = Field(
            default=None, description="Target OSD ID (for OSD-specific checks)", ge=0
        ),
        strict_mode: bool = Field(default=False, description="Treat warnings as failures"),
        target_release: str | None = Field(
            default=None,
            description="Target MOSK release for upgrade validation (e.g., 'mosk-21-0-0-25-2'). Required for upgrade check type.",
        ),
        cluster_name: str | None = Field(
            default=None,
            description="Cluster name for upgrade path validation (e.g., 'mos'). Auto-discovered if not provided.",
        ),
        cluster_namespace: str | None = Field(
            default=None,
            description="Namespace where Cluster CR is defined on MCC (e.g., 'lab'). Auto-discovered if not provided.",
        ),
    ) -> dict[str, Any]:
        """Run preflight checks."""
        async with with_logging_context("run_preflight_check"):
            mosk_k8s = await get_mosk()  # MOSK: Preflight validation
            mcc_k8s = None
            # Get MCC adapter for upgrade checks
            if check_type == "upgrade":
                mcc_k8s = await get_mcc()  # MCC: KaasRelease and ClusterUpdatePlan
            input_data = RunPreflightCheckInput(
                check_type=PreflightCheckType(check_type),
                target_node=target_node,
                target_osd=target_osd,
                strict_mode=strict_mode,
                target_release=target_release,
                cluster_name=cluster_name,
                cluster_namespace=cluster_namespace,
            )
            result = await run_preflight_check(mosk_k8s, input_data, mcc_adapter=mcc_k8s)
            return result.model_dump()

    # =========================================================================
    # Resource Utilization
    # =========================================================================

    # get_resource_utilization - CPU, memory, storage utilization
    @mcp.tool(
        name="get_resource_utilization",
        description=(
            "Get CPU, memory, and storage utilization summary across the cluster. "
            "Optionally includes per-node and per-namespace breakdown. Read-only operation."
        ),
    )
    async def _get_resource_utilization(
        include_per_node: bool = Field(
            default=False, description="Include per-node utilization breakdown"
        ),
        include_per_namespace: bool = Field(
            default=False, description="Include per-namespace utilization"
        ),
    ) -> dict[str, Any]:
        """Get resource utilization."""
        async with with_logging_context("get_resource_utilization"):
            k8s = await get_mosk()  # MOSK: Resource utilization
            input_data = GetResourceUtilizationInput(
                include_per_node=include_per_node,
                include_per_namespace=include_per_namespace,
            )
            result = await get_resource_utilization(k8s, input_data)
            return result.model_dump()

    logger.debug("cluster_health_tools_registered", count=8)
