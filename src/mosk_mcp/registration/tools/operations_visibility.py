"""Operations visibility tools registration for MOSK MCP Server.

This module registers operations visibility tools with the MCP server:
- list_osdpl: List OpenStackDeployment resources
- get_openstack_deployment_status: Get OSDPL status and health
- get_openstack_upgrade_progress: Track OpenStack upgrade progress
- get_mosk_platform_status: Get MOSK platform status from MCC
- get_mosk_platform_upgrade_progress: Track MOSK platform upgrade
- list_available_releases: List available MOSK releases for upgrade
- monitor_operation: Monitor long-running operations
- apply_osdpl_patch: Change OpenStack version (privileged)
- apply_cluster_release_patch: Change MOSK release (privileged)
- commence_cluster_upgrade: Commence cluster upgrade (privileged)
- get_component_versions: Get service version info
- list_live_migrations: List VM live migrations
- get_migration_eta: Get migration completion estimates
- list_maintenance_requests: List maintenance requests
- get_rollout_status: Get deployment rollout status
- get_node_conditions: Get node health conditions
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from pydantic import Field

from mosk_mcp.core.exceptions import ResourceNotFoundError, ToolExecutionError
from mosk_mcp.observability.logging import get_logger
from mosk_mcp.registration.utils import create_adapter_getters, with_logging_context
from mosk_mcp.tools.operations_visibility import (
    ApplyClusterReleasePatchInput,
    ApplyOSDPLPatchInput,
    CommenceClusterUpgradeInput,
    GetComponentVersionsInput,
    GetMigrationETAInput,
    GetMoskPlatformStatusInput,
    GetMoskPlatformUpgradeProgressInput,
    GetNodeConditionsInput,
    GetOSDPLStatusInput,
    GetRolloutStatusInput,
    GetUpgradeProgressInput,
    ListAvailableReleasesInput,
    ListLiveMigrationsInput,
    ListMaintenanceRequestsInput,
    MaintenancePhase,
    MonitorOperationInput,
    OperationType,
    apply_cluster_release_patch,
    apply_osdpl_patch,
    commence_cluster_upgrade,
    get_component_versions,
    get_migration_eta,
    get_mosk_platform_status,
    get_mosk_platform_upgrade_progress,
    get_node_conditions,
    get_openstack_deployment_status,
    get_openstack_upgrade_progress,
    get_rollout_status,
    list_available_releases,
    list_live_migrations,
    monitor_operation,
)
from mosk_mcp.tools.operations_visibility import (
    MigrationStatus as VisMigrationStatus,
)
from mosk_mcp.tools.operations_visibility import (
    list_maintenance_requests as list_maintenance_requests_vis,
)


if TYPE_CHECKING:
    from collections.abc import Callable

    from fastmcp import FastMCP

    from mosk_mcp.core.config import Settings
    from mosk_mcp.core.server_context import SSOServerContext


logger = get_logger(__name__)


def register_operations_visibility_tools(
    mcp: FastMCP, settings: Settings, context_getter: Callable[[], SSOServerContext | None]
) -> None:
    """Register operations visibility tools with the MCP server.

    These tools provide comprehensive visibility into MOSK cluster operations
    including OSDPL status, upgrades, migrations, and rollouts.

    All tools are READ_ONLY safety level except apply_* and commence_* which are PRIVILEGED.

    CLUSTER ROUTING:
    - OSDPL status/upgrades/versions -> MOSK cluster (OpenStackDeployment)
    - Live migrations/ETAs -> MOSK cluster (OpenStack Nova)
    - Rollout status -> MOSK cluster (K8s Deployments)
    - Node conditions -> MOSK cluster (K8s Nodes)
    - Maintenance requests -> MCC cluster (NodeMaintenanceRequest CRD)
    - MOSK platform status/upgrades -> MCC cluster (Cluster CR)

    Args:
        mcp: FastMCP server instance.
        settings: Application settings.
        context_getter: Function that returns the current global SSOServerContext.
    """

    get_mosk, get_mcc = create_adapter_getters(context_getter)

    # =========================================================================
    # OSDPL Status and Upgrade Tools
    # =========================================================================

    # list_osdpl - List OpenStackDeployment resources
    @mcp.tool(
        name="list_osdpl",
        description=(
            "List all OpenStackDeployment resources in a namespace. Use this to discover "
            "available OSDPL names before querying status, health, or upgrade progress. "
            "Read-only operation."
        ),
    )
    async def _list_osdpl(
        namespace: str = Field(
            default="openstack",
            description="Kubernetes namespace to search for OSDPLs",
        ),
    ) -> dict[str, Any]:
        """List OpenStackDeployment resources."""
        async with with_logging_context("list_osdpl"):
            k8s = await get_mosk()  # MOSK: OpenStackDeployment
            osdpls = await k8s.list_openstack_deployments(namespace=namespace)

            # Also fetch OSDPLStatus resources for accurate state/version info
            osdplst_map: dict[str, dict[str, Any]] = {}
            try:
                osdplst_list = await k8s.list_openstack_deployment_status(namespace=namespace)
                for osdplst in osdplst_list:
                    name = osdplst.get("metadata", {}).get("name")
                    if name:
                        osdplst_map[name] = osdplst
            except ResourceNotFoundError:
                # OSDPLStatus CRD may not exist in older MOSK clusters - expected
                logger.debug("osdplstatus_crd_not_found", namespace=namespace)
            except Exception as e:
                # Unexpected error - log for debugging but continue without status data
                logger.warning(
                    "osdplstatus_query_failed",
                    namespace=namespace,
                    error=str(e),
                    error_type=type(e).__name__,
                )

            results = []
            for osdpl in osdpls:
                metadata = osdpl.get("metadata", {})
                status = osdpl.get("status", {})
                spec = osdpl.get("spec", {})
                name = metadata.get("name")

                # Get version from spec (authoritative source)
                # Use spec.openstack_version (snake_case) as primary source
                openstack_version = (
                    spec.get("openstack_version") or spec.get("openStackVersion") or "unknown"
                )

                # Get state from OSDPLStatus if available, otherwise use legacy phase
                osdplst = osdplst_map.get(name, {})
                osdplst_status = osdplst.get("status", {})
                osdpl_summary = osdplst_status.get("osdpl", {})

                # State from OSDPLStatus (APPLIED, APPLYING, FAILED, WAITING)
                state = osdpl_summary.get("state", status.get("phase", "Unknown"))

                # Health ratio from OSDPLStatus (e.g., "23/23")
                health = osdpl_summary.get("health", "")

                # Determine if ready based on state
                is_ready = state == "APPLIED"

                results.append(
                    {
                        "name": name,
                        "namespace": metadata.get("namespace"),
                        "state": state,
                        "openstack_version": openstack_version,
                        "health": health,
                        "ready": is_ready,
                    }
                )

            return {
                "osdpls": results,
                "count": len(results),
                "namespace": namespace,
                "message": f"Found {len(results)} OpenStackDeployment(s) in namespace '{namespace}'"
                if results
                else f"No OpenStackDeployment found in namespace '{namespace}'",
            }

    # get_openstack_deployment_status - OSDPL status and health
    @mcp.tool(
        name="get_openstack_deployment_status",
        description=(
            "Get OpenStack deployment status and health. Shows phase, conditions, "
            "per-service status, and interpreted recommendations. Read-only operation. "
            "NOTE: This is for OpenStack (OSDPL) status, NOT MOSK platform status. "
            "For MOSK platform status or upgrade progress, use get_mosk_platform_status "
            "or get_mosk_platform_upgrade_progress instead."
        ),
    )
    async def _get_openstack_deployment_status(
        name: str | None = Field(
            default=None,
            description="OpenStackDeployment resource name (e.g., 'mos', 'openstack'). If not provided, will auto-discover.",
        ),
        namespace: str = Field(
            default="openstack",
            description="Kubernetes namespace where OpenStackDeployment is deployed",
        ),
        include_conditions: bool = Field(default=True, description="Include detailed conditions"),
        include_services: bool = Field(default=True, description="Include per-service status"),
    ) -> dict[str, Any]:
        """Get OpenStack deployment status."""
        async with with_logging_context("get_openstack_deployment_status"):
            k8s = await get_mosk()  # MOSK: OpenStackDeployment

            # Use session's OSDPL info or auto-discover
            effective_name = name
            effective_namespace = namespace

            context = context_getter()
            if context and context._session and not effective_name and context._session.osdpl_name:
                effective_name = context._session.osdpl_name
                effective_namespace = context._session.osdpl_namespace
                logger.debug(
                    "using_session_osdpl",
                    name=effective_name,
                    namespace=effective_namespace,
                )

            # Auto-discover OSDPL if not provided and not in session
            if not effective_name:
                osdpls = await k8s.list_openstack_deployments(namespace=effective_namespace)
                if osdpls:
                    effective_name = osdpls[0].get("metadata", {}).get("name")
                    logger.debug("auto_discovered_osdpl", name=effective_name)
                    # Cache in session for future use
                    if context and context._session and effective_name:
                        context._session.set_osdpl_info(effective_name, effective_namespace)

            if not effective_name:
                raise ToolExecutionError(
                    "OSDPL name is required. Either provide it explicitly or ensure OSDPL exists in the namespace.",
                    tool_name="get_openstack_deployment_status",
                )

            input_data = GetOSDPLStatusInput(
                name=effective_name,
                namespace=effective_namespace,
                include_conditions=include_conditions,
                include_services=include_services,
            )
            result = await get_openstack_deployment_status(k8s, input_data)
            return result.model_dump()

    # get_openstack_upgrade_progress - OpenStack upgrade progress
    @mcp.tool(
        name="get_openstack_upgrade_progress",
        description=(
            "Track OpenStack upgrade progress. Shows per-component status, "
            "progress percentages, and estimated completion time. Read-only operation. "
            "NOTE: This tracks OpenStack service upgrades (Keystone, Nova, etc.) on MOSK cluster. "
            "For MOSK platform/release upgrades (mosk-17-4-x), use get_mosk_platform_upgrade_progress instead."
        ),
    )
    async def _get_openstack_upgrade_progress(
        name: str | None = Field(
            default=None,
            description="OpenStackDeployment resource name (e.g., 'mos', 'openstack'). If not provided, will auto-discover.",
        ),
        namespace: str = Field(
            default="openstack",
            description="Kubernetes namespace where OpenStackDeployment is deployed",
        ),
        include_component_details: bool = Field(
            default=True, description="Include per-component upgrade details"
        ),
    ) -> dict[str, Any]:
        """Get OpenStack upgrade progress details."""
        async with with_logging_context("get_openstack_upgrade_progress"):
            k8s = await get_mosk()  # MOSK: OpenStackDeployment

            # Use session's OSDPL info or auto-discover
            effective_name = name
            effective_namespace = namespace

            context = context_getter()
            if context and context._session and not effective_name and context._session.osdpl_name:
                effective_name = context._session.osdpl_name
                effective_namespace = context._session.osdpl_namespace

            # Auto-discover OSDPL if not provided and not in session
            if not effective_name:
                osdpls = await k8s.list_openstack_deployments(namespace=effective_namespace)
                if osdpls:
                    effective_name = osdpls[0].get("metadata", {}).get("name")
                    # Cache in session for future use
                    if context and context._session and effective_name:
                        context._session.set_osdpl_info(effective_name, effective_namespace)

            if not effective_name:
                raise ToolExecutionError(
                    "OSDPL name is required. Either provide it explicitly or ensure OSDPL exists in the namespace.",
                    tool_name="get_openstack_upgrade_progress",
                )

            input_data = GetUpgradeProgressInput(
                name=effective_name,
                namespace=effective_namespace,
                include_component_details=include_component_details,
            )
            result = await get_openstack_upgrade_progress(k8s, input_data)
            return result.model_dump()

    # =========================================================================
    # MOSK Platform Status (MCC Cluster)
    # =========================================================================

    # get_mosk_platform_status - MOSK platform status from MCC
    @mcp.tool(
        name="get_mosk_platform_status",
        description=(
            "Get MOSK platform status from MCC management cluster ONLY (Cluster CR, "
            "Machine CRs, release versions). Does NOT include OpenStack or Ceph status. "
            "For comprehensive cluster status including all layers, use get_mosk_cluster_health instead. "
            "Read-only operation."
        ),
    )
    async def _get_mosk_platform_status(
        cluster_name: str | None = Field(
            default=None,
            description="Name of the Cluster CR on MCC (e.g., 'mos'). If not provided, uses session's discovered cluster.",
        ),
        namespace: str = Field(
            default="default", description="Namespace where Cluster CR is located"
        ),
    ) -> dict[str, Any]:
        """Get MOSK platform status from MCC management cluster."""
        async with with_logging_context("get_mosk_platform_status"):
            mcc = await get_mcc()  # MCC: Cluster CR

            # Use session's discovered cluster info if not explicitly provided
            effective_cluster_name = cluster_name
            effective_namespace = namespace

            context = context_getter()
            if context and context._session:
                if not effective_cluster_name and context._session.mosk_cluster_name:
                    effective_cluster_name = context._session.mosk_cluster_name
                    logger.debug(
                        "using_session_cluster_name",
                        cluster_name=effective_cluster_name,
                    )
                if effective_namespace == "default" and context._session.mosk_cluster_namespace:
                    effective_namespace = context._session.mosk_cluster_namespace
                    logger.debug(
                        "using_session_cluster_namespace",
                        cluster_namespace=effective_namespace,
                    )

            if not effective_cluster_name:
                raise ToolExecutionError(
                    "cluster_name is required. Either provide it explicitly or login first to auto-discover.",
                    tool_name="get_mosk_platform_status",
                )

            input_data = GetMoskPlatformStatusInput(
                cluster_name=effective_cluster_name,
                namespace=effective_namespace,
            )
            result = await get_mosk_platform_status(mcc, input_data)
            return result.model_dump()

    # list_available_releases - List available MOSK releases
    @mcp.tool(
        name="list_available_releases",
        description=(
            "List all available MOSK platform releases (ClusterRelease CRs) from MCC. "
            "Shows release names, versions, supported OpenStack releases, and component versions. "
            "Use this when user asks about available MOSK versions, supported releases, or upgrade options. "
            "Read-only operation."
        ),
    )
    async def _list_available_releases(
        cluster_name: str | None = Field(
            default=None,
            description="Name of the Cluster CR to check current release. If not provided, will auto-discover.",
        ),
        cluster_namespace: str = Field(
            default="default",
            description="Namespace where Cluster CR is located",
        ),
        include_all_versions: bool = Field(
            default=True,
            description="Include all MOSK versions (True) or only versions newer than current (False)",
        ),
        include_component_details: bool = Field(
            default=True,
            description="Include detailed component versions for each release",
        ),
    ) -> dict[str, Any]:
        """List available MOSK releases from MCC management cluster."""
        async with with_logging_context("list_available_releases"):
            mcc = await get_mcc()  # MCC: ClusterRelease CRs

            # Use session's discovered cluster info if not explicitly provided
            effective_cluster_name = cluster_name
            effective_namespace = cluster_namespace

            context = context_getter()
            if context and context._session:
                if not effective_cluster_name and context._session.mosk_cluster_name:
                    effective_cluster_name = context._session.mosk_cluster_name
                    logger.debug(
                        "using_session_cluster_name",
                        cluster_name=effective_cluster_name,
                    )
                if effective_namespace == "default" and context._session.mosk_cluster_namespace:
                    effective_namespace = context._session.mosk_cluster_namespace
                    logger.debug(
                        "using_session_cluster_namespace",
                        cluster_namespace=effective_namespace,
                    )

            input_data = ListAvailableReleasesInput(
                cluster_name=effective_cluster_name,
                cluster_namespace=effective_namespace,
                include_all_versions=include_all_versions,
                include_component_details=include_component_details,
            )
            result = await list_available_releases(mcc, input_data)
            return result.model_dump()

    # get_mosk_platform_upgrade_progress - MOSK platform upgrade progress
    @mcp.tool(
        name="get_mosk_platform_upgrade_progress",
        description=(
            "Track MOSK platform upgrade progress. Shows Machine phases, cluster conditions, "
            "HelmBundle status, and overall progress percentage. Use this to monitor MOSK "
            "release upgrades (e.g., mosk-17-4-0 to mosk-17-4-6). Read-only operation. "
            "NOTE: When user asks about 'MOSK upgrade' or 'MOSK status', use THIS tool. "
            "For OpenStack service upgrades (Keystone, Nova), use get_openstack_upgrade_progress instead."
        ),
    )
    async def _get_mosk_platform_upgrade_progress(
        cluster_name: str | None = Field(
            default=None,
            description="Name of the Cluster CR on MCC (e.g., 'mos'). If not provided, uses session's discovered cluster.",
        ),
        namespace: str = Field(
            default="default", description="Namespace where Cluster CR is located"
        ),
    ) -> dict[str, Any]:
        """Get MOSK platform upgrade progress from MCC management cluster."""
        async with with_logging_context("get_mosk_platform_upgrade_progress"):
            mcc = await get_mcc()  # MCC: Cluster CR

            # Use session's discovered cluster info if not explicitly provided
            effective_cluster_name = cluster_name
            effective_namespace = namespace

            context = context_getter()
            if context and context._session:
                if not effective_cluster_name and context._session.mosk_cluster_name:
                    effective_cluster_name = context._session.mosk_cluster_name
                if effective_namespace == "default" and context._session.mosk_cluster_namespace:
                    effective_namespace = context._session.mosk_cluster_namespace

            if not effective_cluster_name:
                raise ToolExecutionError(
                    "cluster_name is required. Either provide it explicitly or login first to auto-discover.",
                    tool_name="get_mosk_platform_upgrade_progress",
                )

            input_data = GetMoskPlatformUpgradeProgressInput(
                cluster_name=effective_cluster_name,
                namespace=effective_namespace,
            )
            result = await get_mosk_platform_upgrade_progress(mcc, input_data)
            return result.model_dump()

    # monitor_operation - Monitor long-running operations
    @mcp.tool(
        name="monitor_operation",
        description=(
            "Monitor long-running MOSK operations with periodic progress updates. "
            "Polls for up to 5 minutes at 30-second intervals, returning accumulated "
            "progress snapshots. Supports: node_add (MCC cluster), openstack_upgrade "
            "(MOSK cluster). Returns continue_monitoring=true if operation not complete. "
            "Read-only operation."
        ),
    )
    async def _monitor_operation(
        operation_type: Literal["node_add", "openstack_upgrade"] = Field(
            ...,
            description="Type of operation to monitor",
        ),
        target: str = Field(
            ...,
            description="Resource name to monitor (node name for node_add, OSDPL name for openstack_upgrade)",
        ),
        namespace: str | None = Field(
            default=None,
            description="Kubernetes namespace. Auto-discovered if not provided.",
        ),
    ) -> dict[str, Any]:
        """Monitor a long-running operation with periodic progress updates."""
        async with with_logging_context("monitor_operation"):
            mcc_adapter = await get_mcc()  # MCC: node_add
            mosk_adapter = await get_mosk()  # MOSK: openstack_upgrade
            input_data = MonitorOperationInput(
                operation_type=OperationType(operation_type),
                target=target,
                namespace=namespace,
            )
            result = await monitor_operation(mcc_adapter, mosk_adapter, input_data)
            return result.model_dump()

    # =========================================================================
    # Privileged OSDPL and Cluster Tools
    # =========================================================================

    # apply_osdpl_patch - Change OpenStack version (privileged)
    @mcp.tool(
        name="apply_osdpl_patch",
        description=(
            "Change OpenStack version to trigger an upgrade. "
            "PRIVILEGED: Requires valid CRQ number. ONLY allows changing "
            "/spec/openstack_version - no other modifications permitted."
        ),
    )
    async def _apply_osdpl_patch(
        osdpl_name: str = Field(
            ..., description="Name of the OpenStackDeployment resource (e.g., 'mos')"
        ),
        patch: list[dict[str, Any]] = Field(
            ...,
            description="Single patch operation: [{op: 'replace', path: '/spec/openstack_version', value: 'caracal'}]",
        ),
        crq_number: str = Field(..., description="Change request number (format: CRQxxxxxxxxx)"),
        namespace: str = Field(
            default="openstack", description="Kubernetes namespace where OSDPL is deployed"
        ),
        dry_run: bool = Field(
            default=False, description="Preview the patch without applying (validates only)"
        ),
    ) -> dict[str, Any]:
        """Change OpenStack version to trigger upgrade. PRIVILEGED operation requiring CRQ."""
        async with with_logging_context("apply_osdpl_patch"):
            k8s = await get_mosk()  # MOSK: OpenStackDeployment
            input_data = ApplyOSDPLPatchInput(
                osdpl_name=osdpl_name,
                namespace=namespace,
                patch=patch,
                crq_number=crq_number,
                dry_run=dry_run,
            )
            result = await apply_osdpl_patch(k8s, input_data)
            return result.model_dump()

    # apply_cluster_release_patch - Change MOSK release (privileged)
    @mcp.tool(
        name="apply_cluster_release_patch",
        description=(
            "Change MOSK cluster release version to trigger a platform upgrade. "
            "PRIVILEGED: Requires valid CRQ number. "
            "ONLY allows changing spec.providerSpec.value.release - no other modifications permitted."
        ),
    )
    async def _apply_cluster_release_patch(
        cluster_name: str = Field(..., description="Name of the Cluster CR (e.g., 'mos')"),
        namespace: str = Field(
            ..., description="Kubernetes namespace where the Cluster is defined (e.g., 'lab')"
        ),
        target_release: str = Field(
            ..., description="Target MOSK release version (e.g., 'mosk-21-0-2-25-2-2')"
        ),
        crq_number: str = Field(..., description="Change request number (format: CRQxxxxxxxxx)"),
        dry_run: bool = Field(
            default=False, description="Preview the patch without applying (validates only)"
        ),
    ) -> dict[str, Any]:
        """Change MOSK platform release version to trigger upgrade. PRIVILEGED operation requiring CRQ."""
        async with with_logging_context("apply_cluster_release_patch"):
            k8s = await get_mcc()  # MCC: Cluster CR is on management cluster
            input_data = ApplyClusterReleasePatchInput(
                cluster_name=cluster_name,
                namespace=namespace,
                target_release=target_release,
                crq_number=crq_number,
                dry_run=dry_run,
            )
            result = await apply_cluster_release_patch(k8s, input_data)
            return result.model_dump()

    # commence_cluster_upgrade - Commence cluster upgrade (privileged)
    @mcp.tool(
        name="commence_cluster_upgrade",
        description=(
            "Commence MOSK cluster upgrade via ClusterUpdatePlan mechanism. "
            "PRIVILEGED: Requires valid CRQ number. "
            "Validates upgrade path in KaasRelease and commences UpdatePlan steps. "
            "Supports step-by-step upgrades (specify step_ids for selective commence)."
        ),
    )
    async def _commence_cluster_upgrade(
        cluster_name: str = Field(..., description="Name of the Cluster CR (e.g., 'mos')"),
        namespace: str = Field(
            ...,
            description="Kubernetes namespace where the Cluster is defined on MCC (e.g., 'lab')",
        ),
        target_release: str = Field(
            ..., description="Target MOSK release version (e.g., 'mosk-21-0-0-25-2')"
        ),
        crq_number: str = Field(..., description="Change request number (format: CRQxxxxxxxxx)"),
        dry_run: bool = Field(
            default=False, description="Preview the upgrade without commencing (validates only)"
        ),
        step_ids: list[str] | None = Field(
            default=None,
            description="(V2) Specific step IDs to commence (e.g., ['openstack', 'ceph']). If None, commences all steps.",
        ),
    ) -> dict[str, Any]:
        """Commence MOSK platform upgrade via ClusterUpdatePlan. PRIVILEGED operation requiring CRQ."""
        async with with_logging_context("commence_cluster_upgrade"):
            k8s = await get_mcc()  # MCC: ClusterUpdatePlan is on management cluster
            input_data = CommenceClusterUpgradeInput(
                cluster_name=cluster_name,
                namespace=namespace,
                target_release=target_release,
                crq_number=crq_number,
                dry_run=dry_run,
                step_ids=step_ids,
            )
            result = await commence_cluster_upgrade(k8s, input_data)
            return result.model_dump()

    # get_component_versions - Get service version info
    @mcp.tool(
        name="get_component_versions",
        description=(
            "Get current vs target versions for all OpenStack services. "
            "Identifies components that are out of sync. Read-only operation."
        ),
    )
    async def _get_component_versions(
        name: str = Field(
            ..., description="OSDPL resource name (e.g., 'mos', 'openstack'). Required."
        ),
        namespace: str = Field(
            default="openstack", description="Kubernetes namespace where OSDPL is deployed"
        ),
        include_containers: bool = Field(
            default=False, description="Include container image versions"
        ),
    ) -> dict[str, Any]:
        """Get component version information."""
        async with with_logging_context("get_component_versions"):
            mosk_adapter = await get_mosk()  # MOSK: OpenStackDeployment
            mcc_adapter = await get_mcc()  # MCC: Cluster CR for versions
            input_data = GetComponentVersionsInput(
                name=name,
                namespace=namespace,
                include_containers=include_containers,
            )
            result = await get_component_versions(mosk_adapter, input_data, mcc_adapter)
            return result.model_dump()

    # =========================================================================
    # Live Migration Tools
    # =========================================================================

    # list_live_migrations - List VM live migrations
    @mcp.tool(
        name="list_live_migrations",
        description=(
            "List active VM live migrations from Nova. Shows source/target hosts, "
            "progress, and status. Read-only operation."
        ),
    )
    async def _list_live_migrations(
        source_host: str | None = Field(default=None, description="Filter by source compute host"),
        target_host: str | None = Field(default=None, description="Filter by target compute host"),
        status_filter: Literal[
            "queued", "preparing", "running", "post-migrating", "completed", "failed", "cancelled"
        ]
        | None = Field(default=None, description="Filter by migration status"),
        include_completed: bool = Field(default=False, description="Include completed migrations"),
        limit: int = Field(default=50, description="Maximum migrations to return", ge=1, le=500),
    ) -> dict[str, Any]:
        """List live migrations."""
        async with with_logging_context("list_live_migrations"):
            k8s = await get_mosk()  # MOSK: OpenStack Nova migrations
            status_enum = None
            if status_filter:
                status_enum = VisMigrationStatus(status_filter)
            input_data = ListLiveMigrationsInput(
                source_host=source_host,
                target_host=target_host,
                status_filter=status_enum,
                include_completed=include_completed,
                limit=limit,
            )
            result = await list_live_migrations(k8s, input_data)
            return result.model_dump()

    # get_migration_eta - Get migration completion estimates
    @mcp.tool(
        name="get_migration_eta",
        description=(
            "Get estimated completion time for active migrations. Shows overall "
            "and per-VM ETAs based on transfer rates. Read-only operation."
        ),
    )
    async def _get_migration_eta(
        source_host: str | None = Field(default=None, description="Filter by source host"),
        include_per_vm: bool = Field(default=True, description="Include per-VM ETA breakdown"),
    ) -> dict[str, Any]:
        """Get migration ETA."""
        async with with_logging_context("get_migration_eta"):
            k8s = await get_mosk()  # MOSK: OpenStack Nova migrations
            input_data = GetMigrationETAInput(
                source_host=source_host,
                include_per_vm=include_per_vm,
            )
            result = await get_migration_eta(k8s, input_data)
            return result.model_dump()

    # =========================================================================
    # Maintenance and Rollout Tools
    # =========================================================================

    # list_maintenance_requests - List maintenance requests
    @mcp.tool(
        name="list_maintenance_requests",
        description=(
            "List active NodeMaintenanceRequest CRs. Shows maintenance phase, "
            "node, and progress. Read-only operation."
        ),
    )
    async def _list_maintenance_requests(
        namespace: str = Field(default="default", description="Kubernetes namespace"),
        node_filter: str | None = Field(default=None, description="Filter by node name"),
        phase_filter: Literal[
            "Pending",
            "Draining",
            "Drained",
            "Maintaining",
            "Uncordoning",
            "Completed",
            "Failed",
            "Cancelled",
        ]
        | None = Field(default=None, description="Filter by maintenance phase"),
        include_completed: bool = Field(default=False, description="Include completed requests"),
        limit: int = Field(default=50, description="Maximum requests to return", ge=1, le=200),
    ) -> dict[str, Any]:
        """List maintenance requests."""
        async with with_logging_context("list_maintenance_requests"):
            k8s = await get_mcc()  # MCC: NodeMaintenanceRequest CRD
            phase_enum = None
            if phase_filter:
                phase_enum = MaintenancePhase(phase_filter)
            input_data = ListMaintenanceRequestsInput(
                namespace=namespace,
                node_filter=node_filter,
                phase_filter=phase_enum,
                include_completed=include_completed,
                limit=limit,
            )
            result = await list_maintenance_requests_vis(k8s, input_data)
            return result.model_dump()

    # get_rollout_status - Get deployment rollout status
    @mcp.tool(
        name="get_rollout_status",
        description=(
            "Get Deployment and StatefulSet rollout status for OpenStack services. "
            "Tracks progress and identifies stuck rollouts. Read-only operation."
        ),
    )
    async def _get_rollout_status(
        namespace: str = Field(
            default="openstack",
            description="Kubernetes namespace where OpenStack workloads are deployed",
        ),
        service_filter: str | None = Field(
            default=None, description="Filter by service name pattern"
        ),
        include_history: bool = Field(default=False, description="Include rollout history"),
    ) -> dict[str, Any]:
        """Get rollout status."""
        async with with_logging_context("get_rollout_status"):
            k8s = await get_mosk()  # MOSK: K8s Deployments/StatefulSets
            input_data = GetRolloutStatusInput(
                namespace=namespace,
                service_filter=service_filter,
                include_history=include_history,
            )
            result = await get_rollout_status(k8s, input_data)
            return result.model_dump()

    # =========================================================================
    # Node Condition Tools
    # =========================================================================

    # get_node_conditions - Get node health conditions
    @mcp.tool(
        name="get_node_conditions",
        description=(
            "Get node conditions and readiness gates. Shows health status, "
            "taints, and issues for cluster nodes. Read-only operation."
        ),
    )
    async def _get_node_conditions(
        node_name: str | None = Field(
            default=None, description="Specific node to query (or all if not specified)"
        ),
        include_taints: bool = Field(default=True, description="Include node taints"),
        include_labels: bool = Field(default=False, description="Include node labels"),
        only_unhealthy: bool = Field(default=False, description="Only show nodes with issues"),
    ) -> dict[str, Any]:
        """Get node conditions."""
        async with with_logging_context("get_node_conditions"):
            k8s = await get_mosk()  # MOSK: K8s Nodes
            input_data = GetNodeConditionsInput(
                node_name=node_name,
                include_taints=include_taints,
                include_labels=include_labels,
                only_unhealthy=only_unhealthy,
            )
            result = await get_node_conditions(k8s, input_data)
            return result.model_dump()

    logger.debug("operations_visibility_tools_registered", count=16)
