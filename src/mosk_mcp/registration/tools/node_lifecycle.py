"""Node lifecycle management tools registration for MOSK MCP Server.

This module registers node lifecycle management tools with the MCP server:
- list_machines: List Machine CRs in cluster
- get_machine_details: Get detailed machine information
- get_node_readiness: Check node readiness for operations
- get_migration_status: Track Nova live migrations
- get_ipamhost_details: Get network configuration details
- list_bmh: List BareMetalHost resources
- list_bmhp: List BareMetalHostProfile resources
- list_l2templates: List L2Template resources
- get_node_provision_progress: Track provisioning progress
- create_maintenance_request: Create NodeMaintenanceRequest CR
- apply_machine: Apply Machine CR (privileged)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from pydantic import Field

from mosk_mcp.observability.logging import get_logger
from mosk_mcp.registration.utils import create_adapter_getters, with_logging_context
from mosk_mcp.tools.node_lifecycle import (
    ApplyMachineInput,
    BMHOperationalStatusFilter,
    BMHStateFilter,
    CreateMaintenanceRequestInput,
    GetIpamHostDetailsInput,
    GetMachineDetailsInput,
    GetMigrationStatusInput,
    GetNodeProvisionProgressInput,
    GetNodeReadinessInput,
    ListBMHInput,
    ListBMHPInput,
    ListL2TemplatesInput,
    ListMachinesInput,
    MachinePhaseFilter,
    MachineRoleFilter,
    MaintenanceReason,
    MigrationStatus,
    ReadinessCheckType,
    apply_machine,
    create_maintenance_request,
    get_ipamhost_details,
    get_machine_details,
    get_migration_status,
    get_node_provision_progress,
    get_node_readiness,
    list_bmh,
    list_bmhp,
    list_l2templates,
    list_machines,
)


if TYPE_CHECKING:
    from collections.abc import Callable

    from fastmcp import FastMCP

    from mosk_mcp.core.config import Settings
    from mosk_mcp.core.server_context import SSOServerContext


logger = get_logger(__name__)


def register_node_lifecycle_tools(
    mcp: FastMCP, settings: Settings, context_getter: Callable[[], SSOServerContext | None]
) -> None:
    """Register node lifecycle management tools with the MCP server.

    These tools provide machine and node management capabilities including:
    - Listing and inspecting machines
    - Node readiness checks
    - Maintenance operations
    - Node draining and live migration
    - Machine CR application

    Safety levels vary from READ_ONLY to PRIVILEGED.

    CLUSTER ROUTING:
    - Machine CRs (list, get, apply, delete) -> MCC cluster (cluster.k8s.io/v1alpha1)
    - Node readiness (K8s nodes) -> MOSK cluster (v1 Nodes)
    - Maintenance requests -> MCC cluster (lcm.mirantis.com/v1alpha1)
    - VM migrations -> MOSK cluster (OpenStack Nova)

    Args:
        mcp: FastMCP server instance.
        settings: Application settings.
        context_getter: Function that returns the current global SSOServerContext.
    """

    get_mosk, get_mcc = create_adapter_getters(context_getter)

    # =========================================================================
    # Read-Only Node Lifecycle Tools
    # =========================================================================

    # list_machines - Machine CRs
    @mcp.tool(
        name="list_machines",
        description=(
            "List Machine CRs in the MOSK cluster with filtering options. "
            "Shows machine status, role, phase, and basic health. Read-only operation."
        ),
    )
    async def _list_machines(
        namespace: str = Field(default="default", description="Kubernetes namespace"),
        role_filter: Literal["all", "compute", "control", "storage", "gateway"] = Field(
            default="all", description="Filter by node role"
        ),
        phase_filter: Literal["all", "running", "provisioning", "failed", "deleting"] = Field(
            default="all", description="Filter by machine phase"
        ),
        label_selector: str | None = Field(
            default=None, description="Label selector (e.g., 'env=prod')"
        ),
        include_conditions: bool = Field(
            default=False, description="Include machine conditions in output"
        ),
        limit: int = Field(default=100, description="Maximum machines to return", ge=1, le=500),
    ) -> dict[str, Any]:
        """List machines in the cluster."""
        async with with_logging_context("list_machines"):
            k8s = await get_mcc()  # MCC: Machine CRD
            input_data = ListMachinesInput(
                namespace=namespace,
                role_filter=MachineRoleFilter(role_filter),
                phase_filter=MachinePhaseFilter(phase_filter),
                label_selector=label_selector,
                include_conditions=include_conditions,
                limit=limit,
            )
            result = await list_machines(k8s, input_data)
            return result.model_dump()

    # get_machine_details - Machine CR details
    @mcp.tool(
        name="get_machine_details",
        description=(
            "Get detailed information about a specific Machine CR including "
            "status, conditions, events, and related resources. Read-only operation."
        ),
    )
    async def _get_machine_details(
        name: str = Field(..., description="Machine name"),
        namespace: str = Field(default="default", description="Kubernetes namespace"),
        include_events: bool = Field(default=True, description="Include recent events"),
        include_related: bool = Field(
            default=True, description="Include related resources (BMHi, BMHp, Node)"
        ),
    ) -> dict[str, Any]:
        """Get detailed machine information."""
        async with with_logging_context("get_machine_details"):
            k8s = await get_mcc()  # MCC: Machine CRD
            input_data = GetMachineDetailsInput(
                name=name,
                namespace=namespace,
                include_events=include_events,
                include_related=include_related,
            )
            result = await get_machine_details(k8s, input_data)
            return result.model_dump()

    # get_node_readiness - Node readiness checks
    @mcp.tool(
        name="get_node_readiness",
        description=(
            "Check if a node is ready for operations (maintenance, drain, deletion). "
            "Performs comprehensive health and safety checks. Read-only operation."
        ),
    )
    async def _get_node_readiness(
        name: str = Field(..., description="Machine/node name"),
        namespace: str = Field(default="default", description="Kubernetes namespace"),
        check_type: Literal["general", "maintenance", "drain", "deletion"] = Field(
            default="general", description="Type of readiness check"
        ),
        check_ceph: bool = Field(default=True, description="Check Ceph OSD status"),
        check_openstack: bool = Field(default=True, description="Check OpenStack services"),
    ) -> dict[str, Any]:
        """Check node readiness for operations."""
        async with with_logging_context("get_node_readiness"):
            mcc_adapter = await get_mcc()  # MCC: Machine CRs
            mosk_adapter = await get_mosk()  # MOSK: K8s Nodes and Pods
            input_data = GetNodeReadinessInput(
                name=name,
                namespace=namespace,
                check_type=ReadinessCheckType(check_type),
                check_ceph=check_ceph,
                check_openstack=check_openstack,
            )
            result = await get_node_readiness(mcc_adapter, input_data, mosk_adapter)
            return result.model_dump()

    # get_migration_status - Nova live migrations
    @mcp.tool(
        name="get_migration_status",
        description=(
            "Track the progress of Nova live migrations. Query by migration ID, "
            "VM, or host. Read-only operation."
        ),
    )
    async def _get_migration_status(
        migration_id: str | None = Field(default=None, description="Specific migration ID"),
        vm_id: str | None = Field(default=None, description="Query migrations for a VM"),
        source_host: str | None = Field(default=None, description="Query migrations from a host"),
        target_host: str | None = Field(default=None, description="Query migrations to a host"),
        status_filter: Literal[
            "queued", "preparing", "running", "post-migrating", "completed", "failed", "cancelled"
        ]
        | None = Field(default=None, description="Filter by migration status"),
        include_completed: bool = Field(default=True, description="Include completed migrations"),
        limit: int = Field(default=50, description="Maximum migrations to return", ge=1, le=200),
    ) -> dict[str, Any]:
        """Get migration status."""
        async with with_logging_context("get_migration_status"):
            k8s = await get_mosk()  # MOSK: OpenStack Nova migrations
            status_enum = None
            if status_filter:
                status_enum = MigrationStatus(status_filter)
            input_data = GetMigrationStatusInput(
                migration_id=migration_id,
                vm_id=vm_id,
                source_host=source_host,
                target_host=target_host,
                status_filter=status_enum,
                include_completed=include_completed,
                limit=limit,
            )
            result = await get_migration_status(k8s, input_data)
            return result.model_dump()

    # get_ipamhost_details - IpamHost network configuration
    @mcp.tool(
        name="get_ipamhost_details",
        description=(
            "Get detailed network configuration for a node from its IpamHost resource. "
            "Shows IP addresses, netplan config, NIC mappings, bonds, bridges, VLANs. "
            "IpamHost is auto-created when Machine is applied. Read-only operation."
        ),
    )
    async def _get_ipamhost_details(
        name: str = Field(..., description="IpamHost name (usually same as Machine name)"),
        namespace: str = Field(default="default", description="Kubernetes namespace"),
        include_netplan: bool = Field(
            default=True, description="Include full netplan configuration"
        ),
    ) -> dict[str, Any]:
        """Get IpamHost network configuration details."""
        async with with_logging_context("get_ipamhost_details"):
            k8s = await get_mcc()  # MCC: IpamHost CRD
            input_data = GetIpamHostDetailsInput(
                name=name,
                namespace=namespace,
                include_netplan=include_netplan,
            )
            result = await get_ipamhost_details(k8s, input_data)
            return result.model_dump()

    # list_bmh - BareMetalHost resources
    @mcp.tool(
        name="list_bmh",
        description=(
            "List BareMetalHost resources with lifecycle state tracking. "
            "BMH lifecycle: registering -> inspecting -> preparing -> available -> "
            "provisioning -> provisioned. Use to track hardware provisioning. Read-only."
        ),
    )
    async def _list_bmh(
        namespace: str = Field(default="default", description="Kubernetes namespace"),
        state_filter: Literal[
            "all",
            "registering",
            "inspecting",
            "preparing",
            "available",
            "provisioning",
            "provisioned",
            "deprovisioning",
            "error",
        ] = Field(default="all", description="Filter by provisioning state"),
        status_filter: Literal["all", "OK", "error", "discovered"] = Field(
            default="all", description="Filter by operational status"
        ),
        label_selector: str | None = Field(
            default=None, description="Label selector for filtering"
        ),
        limit: int = Field(default=100, description="Maximum BMH to return", ge=1, le=500),
    ) -> dict[str, Any]:
        """List BareMetalHost resources."""
        async with with_logging_context("list_bmh"):
            k8s = await get_mcc()  # MCC: BareMetalHost CRD
            input_data = ListBMHInput(
                namespace=namespace,
                state_filter=BMHStateFilter(state_filter),
                status_filter=BMHOperationalStatusFilter(status_filter),
                label_selector=label_selector,
                limit=limit,
            )
            result = await list_bmh(k8s, input_data)
            return result.model_dump()

    # list_bmhp - BareMetalHostProfile resources
    @mcp.tool(
        name="list_bmhp",
        description=(
            "List available BareMetalHostProfile resources for hardware configuration. "
            "BMHPs define disk selection, kernel parameters, and deploy scripts. "
            "Required when creating Machine CRs. Read-only operation."
        ),
    )
    async def _list_bmhp(
        namespace: str = Field(default="default", description="Kubernetes namespace"),
        label_selector: str | None = Field(
            default=None, description="Label selector for filtering"
        ),
        limit: int = Field(default=50, description="Maximum profiles to return", ge=1, le=200),
    ) -> dict[str, Any]:
        """List BareMetalHostProfile resources."""
        async with with_logging_context("list_bmhp"):
            k8s = await get_mcc()  # MCC: BareMetalHostProfile CRD
            input_data = ListBMHPInput(
                namespace=namespace,
                label_selector=label_selector,
                limit=limit,
            )
            result = await list_bmhp(k8s, input_data)
            return result.model_dump()

    # list_l2templates - L2Template resources
    @mcp.tool(
        name="list_l2templates",
        description=(
            "List available L2Template resources for network configuration. "
            "L2Templates define bonds, bridges, VLANs, and IP assignments. "
            "Referenced by IpamHost for node networking. Read-only operation."
        ),
    )
    async def _list_l2templates(
        namespace: str = Field(default="default", description="Kubernetes namespace"),
        label_selector: str | None = Field(
            default=None, description="Label selector for filtering"
        ),
        limit: int = Field(default=50, description="Maximum templates to return", ge=1, le=200),
    ) -> dict[str, Any]:
        """List L2Template resources."""
        async with with_logging_context("list_l2templates"):
            k8s = await get_mcc()  # MCC: L2Template CRD
            input_data = ListL2TemplatesInput(
                namespace=namespace,
                label_selector=label_selector,
                limit=limit,
            )
            result = await list_l2templates(k8s, input_data)
            return result.model_dump()

    # get_node_provision_progress - Node provisioning progress
    @mcp.tool(
        name="get_node_provision_progress",
        description=(
            "Track complete node provisioning progress through all stages: "
            "BMHi -> BMH (registering->inspecting->preparing->available->provisioning->provisioned) "
            "-> Machine/LCMMachine (deploying->Ready) -> Node Ready. "
            "Shows progress percentage, current phase, and status of all resources."
        ),
    )
    async def _get_node_provision_progress(
        name: str = Field(..., description="Node/Machine name to track"),
        namespace: str = Field(default="default", description="Kubernetes namespace"),
    ) -> dict[str, Any]:
        """Track node provisioning progress."""
        async with with_logging_context("get_node_provision_progress"):
            k8s = await get_mcc()  # MCC: All node lifecycle CRDs
            input_data = GetNodeProvisionProgressInput(
                name=name,
                namespace=namespace,
            )
            result = await get_node_provision_progress(k8s, input_data)
            return result.model_dump()

    # =========================================================================
    # Non-Destructive Node Lifecycle Tools
    # =========================================================================

    # create_maintenance_request - NodeMaintenanceRequest CR
    @mcp.tool(
        name="create_maintenance_request",
        description=(
            "Create a NodeMaintenanceRequest CR for planned maintenance. "
            "Generates YAML template, optionally applies to cluster. Non-destructive by default."
        ),
    )
    async def _create_maintenance_request(
        node_name: str = Field(..., description="Node name to put in maintenance"),
        reason: Literal[
            "hardware-repair",
            "firmware-update",
            "os-upgrade",
            "security-patch",
            "performance-tuning",
            "disk-replacement",
            "network-maintenance",
            "planned-reboot",
            "other",
        ] = Field(..., description="Maintenance reason"),
        description: str = Field(default="", description="Detailed description"),
        drain_pods: bool = Field(default=True, description="Drain pods before maintenance"),
        force_drain: bool = Field(default=False, description="Force drain even with PDBs"),
        grace_period_seconds: int = Field(
            default=300, description="Pod termination grace period", ge=0, le=3600
        ),
        timeout_minutes: int = Field(
            default=60, description="Maintenance operation timeout", ge=1, le=1440
        ),
        namespace: str = Field(default="default", description="Kubernetes namespace"),
        dry_run: bool = Field(default=True, description="Generate template only, don't apply"),
    ) -> dict[str, Any]:
        """Create maintenance request."""
        async with with_logging_context("create_maintenance_request"):
            k8s = await get_mcc()  # MCC: NodeMaintenanceRequest CRD
            input_data = CreateMaintenanceRequestInput(
                node_name=node_name,
                reason=MaintenanceReason(reason),
                description=description,
                drain_pods=drain_pods,
                force_drain=force_drain,
                grace_period_seconds=grace_period_seconds,
                timeout_minutes=timeout_minutes,
                namespace=namespace,
                dry_run=dry_run,
            )
            result = await create_maintenance_request(k8s, input_data)
            return result.model_dump()

    # =========================================================================
    # Privileged Node Lifecycle Tools (require CRQ)
    # =========================================================================

    # apply_machine - Apply Machine CR
    @mcp.tool(
        name="apply_machine",
        description=(
            "Apply (create or update) a Machine CR. PRIVILEGED: Requires valid CRQ number "
            "for non-dry-run operations. Validates prerequisites before applying."
        ),
    )
    async def _apply_machine(
        machine_yaml: str | None = Field(default=None, description="Machine CR as YAML string"),
        machine_dict: dict[str, Any] | None = Field(
            default=None, description="Machine CR as dictionary"
        ),
        crq_number: str | None = Field(
            default=None, description="Change request number (CRQxxxxxxxxx)"
        ),
        namespace: str | None = Field(default=None, description="Override namespace"),
        validate_prerequisites: bool = Field(
            default=True, description="Validate BMHp, BMHi, IpamHost exist"
        ),
        server_side_apply: bool = Field(default=False, description="Use server-side apply"),
        dry_run: bool = Field(default=True, description="Preview only, don't apply"),
    ) -> dict[str, Any]:
        """Apply a Machine CR."""
        async with with_logging_context("apply_machine"):
            k8s = await get_mcc()  # MCC: Machine CRD
            input_data = ApplyMachineInput(
                machine_yaml=machine_yaml,
                machine_dict=machine_dict,
                crq_number=crq_number,
                namespace=namespace,
                validate_prerequisites=validate_prerequisites,
                server_side_apply=server_side_apply,
                dry_run=dry_run,
            )
            result = await apply_machine(k8s, input_data)
            return result.model_dump()

    logger.debug("node_lifecycle_tools_registered", count=11)
