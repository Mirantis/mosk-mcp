"""List machines tool for MOSK MCP Server.

This module provides the list_machines tool for querying Machine CRs
in the cluster with filtering and summarization capabilities.

Safety Level: READ_ONLY
"""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from mosk_mcp.adapters.crd.machine import Machine
from mosk_mcp.auth.rbac import ToolSafetyLevel
from mosk_mcp.core.exceptions import KubernetesError
from mosk_mcp.observability.audit import AuditLevel
from mosk_mcp.observability.logging import get_logger
from mosk_mcp.tools.common import audit_tool_execution, calculate_resource_age


if TYPE_CHECKING:
    from mosk_mcp.adapters.kubernetes import KubernetesAdapter
    from mosk_mcp.auth.types import UserContext
    from mosk_mcp.observability.audit import AuditLogger


logger = get_logger(__name__)

# Tool metadata
TOOL_NAME = "list_machines"
TOOL_SAFETY_LEVEL = ToolSafetyLevel.READ_ONLY
TOOL_DESCRIPTION = (
    "List all machines in the MOSK cluster with their status, role, and phase. "
    "Supports filtering by namespace, role, and phase."
)


class MachineRoleFilter(str, Enum):
    """Filter options for machine role."""

    ALL = "all"
    COMPUTE = "compute"
    CONTROL = "control"
    STORAGE = "storage"
    GATEWAY = "gateway"


class MachinePhaseFilter(str, Enum):
    """Filter options for machine phase."""

    ALL = "all"
    RUNNING = "running"
    PROVISIONING = "provisioning"
    FAILED = "failed"
    DELETING = "deleting"


class ListMachinesInput(BaseModel):
    """Input parameters for list_machines tool.

    Attributes:
        namespace: Kubernetes namespace to query. Use '*' for all namespaces.
        role_filter: Filter by machine role.
        phase_filter: Filter by machine phase.
        label_selector: Additional label selector for filtering.
        include_conditions: Include condition details in output.
        limit: Maximum number of machines to return.
    """

    namespace: str = Field(
        default="default",
        description="Namespace to list machines from. Use '*' for all namespaces.",
    )
    role_filter: MachineRoleFilter = Field(
        default=MachineRoleFilter.ALL,
        description="Filter by machine role",
    )
    phase_filter: MachinePhaseFilter = Field(
        default=MachinePhaseFilter.ALL,
        description="Filter by machine phase",
    )
    label_selector: str | None = Field(
        default=None,
        description="Additional Kubernetes label selector (e.g., 'env=prod')",
    )
    include_conditions: bool = Field(
        default=False,
        description="Include condition details in the output",
    )
    limit: int = Field(
        default=100,
        ge=1,
        le=500,
        description="Maximum number of machines to return",
    )
    offset: int = Field(
        default=0,
        ge=0,
        description="Number of machines to skip (for pagination)",
    )


class MachineListSummary(BaseModel):
    """Summary statistics for a list of machines.

    Attributes:
        by_role: Count of machines by role (compute, control, storage, gateway).
        by_phase: Count of machines by phase (Ready, Provisioning, Failed, etc.).
        healthy_count: Number of healthy machines (phase=Ready).
        unhealthy_count: Number of unhealthy machines (phase=Failed or Unknown).
        provisioning_count: Number of machines currently provisioning.
    """

    by_role: dict[str, int] = Field(default_factory=dict, description="Count of machines by role")
    by_phase: dict[str, int] = Field(default_factory=dict, description="Count of machines by phase")
    healthy_count: int = Field(default=0, ge=0, description="Number of healthy machines")
    unhealthy_count: int = Field(default=0, ge=0, description="Number of unhealthy machines")
    provisioning_count: int = Field(default=0, ge=0, description="Number of machines provisioning")


class MachineSummary(BaseModel):
    """Summary information for a single machine.

    Attributes:
        name: Machine name.
        namespace: Machine namespace.
        role: Machine role (compute, control, storage, gateway).
        phase: Current machine phase.
        ready: Whether machine is ready (from providerStatus.ready).
        internal_ip: Internal IP address.
        hostname: Machine hostname.
        node_name: Kubernetes node name if provisioned.
        profile: BareMetalHostProfile reference.
        age_seconds: Age of the machine in seconds.
        conditions: List of conditions if requested.
        error_message: Error message if in failed state.
    """

    name: str = Field(..., description="Machine name")
    namespace: str = Field(..., description="Machine namespace")
    role: str = Field(..., description="Machine role")
    phase: str = Field(..., description="Current phase")
    ready: bool | None = Field(None, description="Whether machine is ready (from providerStatus)")
    internal_ip: str | None = Field(None, description="Internal IP address")
    hostname: str | None = Field(None, description="Hostname")
    node_name: str | None = Field(None, description="Kubernetes node name")
    profile: str = Field(..., description="BareMetalHostProfile reference")
    age_seconds: float | None = Field(None, description="Age in seconds")
    conditions: list[dict[str, Any]] = Field(default_factory=list, description="Machine conditions")
    error_message: str | None = Field(None, description="Error message if failed")


class ListMachinesOutput(BaseModel):
    """Output from list_machines tool.

    Attributes:
        machines: List of machine summaries.
        total_count: Total number of machines found.
        filtered_count: Number of machines after filtering.
        namespace: Namespace that was queried.
        summary: Summary statistics by role and phase.
    """

    machines: list[MachineSummary] = Field(
        default_factory=list, description="List of machine summaries"
    )
    total_count: int = Field(..., description="Total machines found")
    filtered_count: int = Field(..., description="Machines after filtering")
    namespace: str = Field(..., description="Namespace queried")
    summary: MachineListSummary = Field(
        default_factory=MachineListSummary, description="Summary statistics"
    )
    has_more: bool = Field(
        default=False, description="Whether more machines exist beyond current page"
    )
    next_offset: int | None = Field(
        default=None, description="Offset to use for next page, if has_more is true"
    )


def _extract_machine_summary(
    machine_data: dict[str, Any],
    include_conditions: bool = False,
) -> MachineSummary:
    """Extract summary information from a Machine CR.

    Args:
        machine_data: Raw machine data from Kubernetes API.
        include_conditions: Whether to include conditions.

    Returns:
        MachineSummary with extracted information.
    """
    machine = Machine.from_kubernetes(machine_data)
    metadata = machine_data.get("metadata", {})
    spec = machine_data.get("spec", {})
    status = machine_data.get("status", {})
    provider_spec = spec.get("providerSpec", {}).get("value", {})
    provider_status = status.get("providerStatus", {})

    # Extract phase
    phase = status.get("phase", "Unknown")

    # Extract ready from providerStatus (the actual ready boolean)
    # status.ready is typically None, the real value is in providerStatus.ready
    ready = provider_status.get("ready")

    # Extract IP and hostname from addresses
    internal_ip = None
    hostname = None
    for addr in status.get("addresses", []):
        if addr.get("type") == "InternalIP":
            internal_ip = addr.get("address")
        elif addr.get("type") == "Hostname":
            hostname = addr.get("address")

    # Extract node reference
    node_ref = status.get("nodeRef", {})
    node_name = node_ref.get("name") if node_ref else None

    # Calculate age using shared utility
    age_seconds = calculate_resource_age(metadata)

    # Extract conditions if requested
    conditions = []
    if include_conditions:
        conditions = status.get("conditions", [])

    # Extract error message
    error_message = status.get("errorMessage")

    # Extract profile reference - handle both string and object formats
    profile_ref = provider_spec.get("bareMetalHostProfile", "unknown")
    if isinstance(profile_ref, dict):
        profile_name = profile_ref.get("name", "unknown")
        profile_namespace = profile_ref.get("namespace")
        profile = f"{profile_namespace}/{profile_name}" if profile_namespace else profile_name
    elif isinstance(profile_ref, str):
        profile = profile_ref
    else:
        profile = "unknown"

    return MachineSummary(
        name=metadata.get("name", "unknown"),
        namespace=metadata.get("namespace", "default"),
        role=machine.role,
        phase=phase,
        ready=ready,
        internal_ip=internal_ip,
        hostname=hostname,
        node_name=node_name,
        profile=profile,
        age_seconds=age_seconds,
        conditions=conditions,
        error_message=error_message,
    )


def _apply_filters(
    machines: list[MachineSummary],
    role_filter: MachineRoleFilter,
    phase_filter: MachinePhaseFilter,
) -> list[MachineSummary]:
    """Apply role and phase filters to machine list.

    Args:
        machines: List of machine summaries.
        role_filter: Role filter to apply.
        phase_filter: Phase filter to apply.

    Returns:
        Filtered list of machines.
    """
    result = machines

    # Apply role filter
    if role_filter != MachineRoleFilter.ALL:
        result = [m for m in result if m.role == role_filter.value]

    # Apply phase filter
    if phase_filter != MachinePhaseFilter.ALL:
        if phase_filter == MachinePhaseFilter.RUNNING:
            # "running" filter matches "Ready" phase (MOSK machines use "Ready" when running)
            result = [m for m in result if m.phase == "Ready"]
        else:
            # Case-insensitive match for other phases
            result = [m for m in result if m.phase.lower() == phase_filter.value]

    return result


def _generate_summary(machines: list[MachineSummary]) -> MachineListSummary:
    """Generate summary statistics for machines.

    Args:
        machines: List of machine summaries.

    Returns:
        MachineListSummary with role and phase counts.
    """
    by_role: dict[str, int] = {}
    by_phase: dict[str, int] = {}
    healthy_count = 0
    unhealthy_count = 0

    for machine in machines:
        # Count by role
        by_role[machine.role] = by_role.get(machine.role, 0) + 1

        # Count by phase
        by_phase[machine.phase] = by_phase.get(machine.phase, 0) + 1

        # Count healthy vs unhealthy
        # Note: MOSK machines report "Ready" when running successfully
        if machine.phase == "Ready":
            healthy_count += 1
        elif machine.phase in ("Failed", "Unknown"):
            unhealthy_count += 1

    return MachineListSummary(
        by_role=by_role,
        by_phase=by_phase,
        healthy_count=healthy_count,
        unhealthy_count=unhealthy_count,
        provisioning_count=by_phase.get("Provisioning", 0),
    )


async def list_machines(
    k8s_adapter: KubernetesAdapter,
    input_data: ListMachinesInput,
    context: UserContext | None = None,
    audit_logger: AuditLogger | None = None,
) -> ListMachinesOutput:
    """List all machines in the MOSK cluster.

    This tool queries Machine CRs and returns a summary of each machine
    including its role, phase, IP addresses, and related information.

    Args:
        k8s_adapter: Kubernetes adapter for API operations.
        input_data: Input parameters for the query.
        context: User context for RBAC (optional).
        audit_logger: Audit logger for tracking operations (optional).

    Returns:
        ListMachinesOutput with machine summaries and statistics.

    Raises:
        KubernetesError: If the Kubernetes API call fails.

    Example:
        >>> async with KubernetesAdapter() as k8s:
        ...     result = await list_machines(
        ...         k8s, ListMachinesInput(namespace="default", role_filter="compute")
        ...     )
        ...     for machine in result.machines:
        ...         print(f"{machine.name}: {machine.phase}")
    """
    logger.info(
        "listing_machines",
        namespace=input_data.namespace,
        role_filter=input_data.role_filter.value,
        phase_filter=input_data.phase_filter.value,
    )

    namespace = input_data.namespace

    async with audit_tool_execution(
        TOOL_NAME,
        audit_logger,
        context,
        AuditLevel.READ,
        {
            "namespace": namespace,
            "role_filter": input_data.role_filter.value,
            "phase_filter": input_data.phase_filter.value,
        },
    ) as audit_details:
        try:
            raw_machines = await k8s_adapter.list_machines(
                namespace=namespace,
                label_selector=input_data.label_selector,
            )

            total_count = len(raw_machines)

            # Extract summaries
            machines = [
                _extract_machine_summary(m, input_data.include_conditions) for m in raw_machines
            ]

            # Apply filters
            filtered_machines = _apply_filters(
                machines,
                input_data.role_filter,
                input_data.phase_filter,
            )

            # Count after filtering but before pagination
            filtered_count = len(filtered_machines)

            # Generate summary from all filtered machines (before pagination)
            summary = _generate_summary(filtered_machines)

            # Apply pagination (offset + limit)
            end_index = input_data.offset + input_data.limit
            paginated_machines = filtered_machines[input_data.offset : end_index]

            # Calculate pagination metadata
            has_more = end_index < filtered_count
            next_offset = end_index if has_more else None

            logger.info(
                "machines_listed",
                total_count=total_count,
                filtered_count=filtered_count,
                returned_count=len(paginated_machines),
                offset=input_data.offset,
                namespace=namespace,
            )

            # Update audit details for success log
            audit_details["total_count"] = total_count
            audit_details["filtered_count"] = filtered_count

            return ListMachinesOutput(
                machines=paginated_machines,
                total_count=total_count,
                filtered_count=filtered_count,
                namespace=namespace,
                summary=summary,
                has_more=has_more,
                next_offset=next_offset,
            )

        except Exception as e:
            logger.error(
                "list_machines_failed",
                error=str(e),
                namespace=namespace,
            )

            if isinstance(e, KubernetesError):
                raise
            raise KubernetesError(
                f"Failed to list machines: {e}",
                operation="list",
                resource_kind="Machine",
                namespace=namespace,
            ) from e
