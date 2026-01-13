"""Get node provisioning progress tool for MOSK MCP Server.

This module provides the get_node_provision_progress tool for tracking
the complete node addition workflow through all stages:

1. BMHi → BMH lifecycle: registering → inspecting → preparing → available
2. Machine applied → BMH: provisioning → provisioned
3. Machine/LCMMachine: Provisioning → Ready
4. IpamHost: auto-created with state OK
5. Kubernetes Node: Ready

Safety Level: READ_ONLY
"""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from mosk_mcp.auth.rbac import ToolSafetyLevel
from mosk_mcp.core.exceptions import KubernetesError, ResourceNotFoundError
from mosk_mcp.observability.audit import AuditLevel
from mosk_mcp.observability.logging import get_logger
from mosk_mcp.tools.common import audit_tool_execution


if TYPE_CHECKING:
    from mosk_mcp.adapters.kubernetes import KubernetesAdapter
    from mosk_mcp.auth.types import UserContext
    from mosk_mcp.observability.audit import AuditLogger


logger = get_logger(__name__)

# Tool metadata
TOOL_NAME = "get_node_provision_progress"
TOOL_SAFETY_LEVEL = ToolSafetyLevel.READ_ONLY
TOOL_DESCRIPTION = (
    "Track the complete node provisioning progress through all stages. "
    "Shows BMHi, BMH, Machine, LCMMachine, IpamHost, and Node status with "
    "overall progress percentage and current phase."
)


class ProvisionPhase(str, Enum):
    """Node provisioning phases."""

    NOT_STARTED = "not_started"
    BMHI_CREATED = "bmhi_created"
    BMH_REGISTERING = "bmh_registering"
    BMH_INSPECTING = "bmh_inspecting"
    BMH_PREPARING = "bmh_preparing"
    BMH_AVAILABLE = "bmh_available"
    MACHINE_CREATED = "machine_created"
    BMH_PROVISIONING = "bmh_provisioning"
    BMH_PROVISIONED = "bmh_provisioned"
    MACHINE_DEPLOYING = "machine_deploying"
    MACHINE_READY = "machine_ready"
    NODE_READY = "node_ready"
    COMPLETED = "completed"
    ERROR = "error"


# Progress percentage for each phase
PHASE_PROGRESS = {
    ProvisionPhase.NOT_STARTED: 0,
    ProvisionPhase.BMHI_CREATED: 5,
    ProvisionPhase.BMH_REGISTERING: 10,
    ProvisionPhase.BMH_INSPECTING: 20,
    ProvisionPhase.BMH_PREPARING: 30,
    ProvisionPhase.BMH_AVAILABLE: 40,
    ProvisionPhase.MACHINE_CREATED: 45,
    ProvisionPhase.BMH_PROVISIONING: 55,
    ProvisionPhase.BMH_PROVISIONED: 70,
    ProvisionPhase.MACHINE_DEPLOYING: 80,
    ProvisionPhase.MACHINE_READY: 90,
    ProvisionPhase.NODE_READY: 95,
    ProvisionPhase.COMPLETED: 100,
    ProvisionPhase.ERROR: -1,
}


class GetNodeProvisionProgressInput(BaseModel):
    """Input parameters for get_node_provision_progress tool.

    Attributes:
        name: Node/Machine name to track.
        namespace: Kubernetes namespace.
    """

    name: str = Field(
        ...,
        description="Name of the node/machine to track provisioning progress",
        min_length=1,
        max_length=253,
    )
    namespace: str = Field(
        default="default",
        description="Kubernetes namespace",
    )


class ResourceStatus(BaseModel):
    """Status of a single resource in the provisioning workflow.

    Attributes:
        exists: Whether the resource exists.
        query_failed: Whether the query to check the resource failed (API error, etc.).
        state: Current state/phase of the resource.
        status: Operational status (OK, Error, etc.).
        message: Additional status message.
        details: Extra details about the resource.

    Note:
        - exists=False, query_failed=False means resource genuinely doesn't exist
        - exists=False, query_failed=True means we couldn't check (API error)
        - exists=True means resource was found
    """

    exists: bool = Field(False, description="Whether resource exists")
    query_failed: bool = Field(False, description="Whether query failed (API error)")
    state: str | None = Field(None, description="Current state/phase")
    status: str | None = Field(None, description="Operational status")
    message: str | None = Field(None, description="Status message")
    details: dict[str, Any] = Field(default_factory=dict, description="Extra details")


class GetNodeProvisionProgressOutput(BaseModel):
    """Output from get_node_provision_progress tool.

    Attributes:
        name: Node name being tracked.
        namespace: Namespace.
        current_phase: Current provisioning phase.
        progress_percent: Overall progress percentage (0-100).
        is_complete: Whether provisioning is complete.
        has_error: Whether there's an error.
        error_message: Error message if any.
        bmhi_status: BareMetalHostInventory status.
        bmh_status: BareMetalHost status.
        machine_status: Machine status.
        lcmmachine_status: LCMMachine status.
        ipamhost_status: IpamHost status.
        node_status: Kubernetes Node status.
        next_expected_phase: What phase comes next.
        estimated_remaining_steps: Number of steps remaining.
    """

    name: str = Field(..., description="Node name")
    namespace: str = Field(..., description="Namespace")
    current_phase: ProvisionPhase = Field(..., description="Current phase")
    progress_percent: int = Field(..., description="Progress percentage", ge=-1, le=100)
    is_complete: bool = Field(False, description="Whether complete")
    has_error: bool = Field(False, description="Whether there's an error")
    error_message: str | None = Field(None, description="Error message")
    bmhi_status: ResourceStatus = Field(default_factory=ResourceStatus, description="BMHi status")
    bmh_status: ResourceStatus = Field(default_factory=ResourceStatus, description="BMH status")
    machine_status: ResourceStatus = Field(
        default_factory=ResourceStatus, description="Machine status"
    )
    lcmmachine_status: ResourceStatus = Field(
        default_factory=ResourceStatus, description="LCMMachine status"
    )
    ipamhost_status: ResourceStatus = Field(
        default_factory=ResourceStatus, description="IpamHost status"
    )
    node_status: ResourceStatus = Field(
        default_factory=ResourceStatus, description="K8s Node status"
    )
    next_expected_phase: str | None = Field(None, description="Next expected phase")
    estimated_remaining_steps: int = Field(0, description="Remaining steps")


async def _get_bmhi_status(
    k8s_adapter: KubernetesAdapter, name: str, namespace: str
) -> ResourceStatus:
    """Get BareMetalHostInventory status."""
    try:
        bmhi = await k8s_adapter.get_custom_resource(
            group="kaas.mirantis.com",
            version="v1alpha1",
            plural="baremetalhostinventories",
            name=name,
            namespace=namespace,
        )
        status = bmhi.get("status", {})
        return ResourceStatus(
            exists=True,
            state=status.get("operationalStatus", "Unknown"),
            status=status.get("operationalStatus"),
            message=status.get("errorMessage"),
            details={
                "bmc": bmhi.get("spec", {}).get("bmc", {}).get("address"),
                "online": bmhi.get("spec", {}).get("online", False),
            },
        )
    except ResourceNotFoundError:
        return ResourceStatus(exists=False, query_failed=False)
    except Exception as e:
        # Query failed - distinguish from "not found"
        return ResourceStatus(exists=False, query_failed=True, message=f"Query failed: {e}")


async def _get_bmh_status(
    k8s_adapter: KubernetesAdapter, name: str, namespace: str
) -> ResourceStatus:
    """Get BareMetalHost status."""
    try:
        bmh = await k8s_adapter.get_custom_resource(
            group="metal3.io",
            version="v1alpha1",
            plural="baremetalhosts",
            name=name,
            namespace=namespace,
        )
        status = bmh.get("status", {})
        provisioning = status.get("provisioning", {})
        return ResourceStatus(
            exists=True,
            state=provisioning.get("state", "unknown"),
            status=status.get("operationalStatus"),
            message=status.get("errorMessage"),
            details={
                "powered_on": status.get("poweredOn", False),
                "consumer": bmh.get("spec", {}).get("consumerRef", {}).get("name"),
            },
        )
    except ResourceNotFoundError:
        return ResourceStatus(exists=False, query_failed=False)
    except Exception as e:
        # Query failed - distinguish from "not found"
        return ResourceStatus(exists=False, query_failed=True, message=f"Query failed: {e}")


async def _get_machine_status(
    k8s_adapter: KubernetesAdapter, name: str, namespace: str
) -> ResourceStatus:
    """Get Machine status."""
    try:
        machine = await k8s_adapter.get_custom_resource(
            group="cluster.k8s.io",
            version="v1alpha1",
            plural="machines",
            name=name,
            namespace=namespace,
        )
        status = machine.get("status", {})
        return ResourceStatus(
            exists=True,
            state=status.get("phase", "Unknown"),
            status=status.get("phase"),
            message=status.get("errorMessage"),
            details={
                "node_ref": status.get("nodeRef", {}).get("name"),
                "addresses": status.get("addresses", []),
            },
        )
    except ResourceNotFoundError:
        return ResourceStatus(exists=False, query_failed=False)
    except Exception as e:
        # Query failed - distinguish from "not found"
        return ResourceStatus(exists=False, query_failed=True, message=f"Query failed: {e}")


async def _get_lcmmachine_status(
    k8s_adapter: KubernetesAdapter, name: str, namespace: str
) -> ResourceStatus:
    """Get LCMMachine status."""
    try:
        lcm = await k8s_adapter.get_custom_resource(
            group="lcm.mirantis.com",
            version="v1alpha1",
            plural="lcmmachines",
            name=name,
            namespace=namespace,
        )
        status = lcm.get("status", {})
        spec = lcm.get("spec", {})
        return ResourceStatus(
            exists=True,
            state=status.get("state", "Unknown"),
            status=status.get("state"),
            details={
                "type": spec.get("type"),  # control or worker
                "internal_ip": status.get("internalIP"),
                "hostname": status.get("hostname"),
            },
        )
    except ResourceNotFoundError:
        return ResourceStatus(exists=False, query_failed=False)
    except Exception as e:
        # Query failed - distinguish from "not found"
        return ResourceStatus(exists=False, query_failed=True, message=f"Query failed: {e}")


async def _get_ipamhost_status(
    k8s_adapter: KubernetesAdapter, name: str, namespace: str
) -> ResourceStatus:
    """Get IpamHost status."""
    try:
        ipam = await k8s_adapter.get_custom_resource(
            group="ipam.mirantis.com",
            version="v1alpha1",
            plural="ipamhosts",
            name=name,
            namespace=namespace,
        )
        status = ipam.get("status", {})
        return ResourceStatus(
            exists=True,
            state=status.get("state", "Unknown"),
            status=status.get("state"),
            details={
                "l2_template": status.get("l2TemplateRef"),
                "service_map": list(status.get("serviceMap", {}).keys()),
            },
        )
    except ResourceNotFoundError:
        return ResourceStatus(exists=False, query_failed=False)
    except Exception as e:
        # Query failed - distinguish from "not found"
        return ResourceStatus(exists=False, query_failed=True, message=f"Query failed: {e}")


async def _get_node_status(k8s_adapter: KubernetesAdapter, node_name: str | None) -> ResourceStatus:
    """Get Kubernetes Node status."""
    if not node_name:
        return ResourceStatus(exists=False, message="Node reference not found")

    try:
        node = await k8s_adapter.get(
            kind="Node",
            name=node_name,
            namespace=None,  # Nodes are cluster-scoped
        )
        conditions = node.get("status", {}).get("conditions", [])

        # Find Ready condition
        ready_condition = next(
            (c for c in conditions if c.get("type") == "Ready"),
            None,
        )
        is_ready = ready_condition and ready_condition.get("status") == "True"

        return ResourceStatus(
            exists=True,
            state="Ready" if is_ready else "NotReady",
            status="Ready" if is_ready else "NotReady",
            message=ready_condition.get("message") if ready_condition else None,
            details={
                "kubelet_version": node.get("status", {}).get("nodeInfo", {}).get("kubeletVersion"),
            },
        )
    except ResourceNotFoundError:
        return ResourceStatus(exists=False, query_failed=False)
    except Exception as e:
        # Query failed - distinguish from "not found"
        return ResourceStatus(exists=False, query_failed=True, message=f"Query failed: {e}")


def _determine_phase(
    bmhi: ResourceStatus,
    bmh: ResourceStatus,
    machine: ResourceStatus,
    lcm: ResourceStatus,
    ipam: ResourceStatus,
    node: ResourceStatus,
) -> tuple[ProvisionPhase, str | None, str | None]:
    """Determine current provisioning phase based on resource states.

    Returns:
        Tuple of (current_phase, error_message, next_expected_phase).
    """
    # Check for errors first
    for resource, name in [
        (bmhi, "BMHi"),
        (bmh, "BMH"),
        (machine, "Machine"),
        (lcm, "LCMMachine"),
    ]:
        if resource.exists and resource.status and "error" in resource.status.lower():
            return ProvisionPhase.ERROR, f"{name}: {resource.message}", None
        if resource.message and "error" in resource.message.lower():
            return ProvisionPhase.ERROR, f"{name}: {resource.message}", None

    # Check completion first
    if (
        node.exists
        and node.state == "Ready"
        and machine.exists
        and machine.state == "Ready"
        and lcm.exists
        and lcm.state == "Ready"
    ):
        return ProvisionPhase.COMPLETED, None, None

    # Node ready but waiting for final checks
    if node.exists and node.state == "Ready":
        return ProvisionPhase.NODE_READY, None, "completed"

    # Machine/LCMMachine ready
    if machine.exists and machine.state == "Ready":
        if lcm.exists and lcm.state == "Ready":
            return ProvisionPhase.MACHINE_READY, None, "node_ready"
        return ProvisionPhase.MACHINE_DEPLOYING, None, "machine_ready"

    # BMH provisioned
    if bmh.exists and bmh.state == "provisioned":
        if machine.exists:
            return ProvisionPhase.BMH_PROVISIONED, None, "machine_deploying"
        return ProvisionPhase.BMH_PROVISIONED, None, "machine_created"

    # BMH provisioning
    if bmh.exists and bmh.state == "provisioning":
        return ProvisionPhase.BMH_PROVISIONING, None, "bmh_provisioned"

    # Machine created but BMH not yet provisioning
    if machine.exists and bmh.exists and bmh.state == "available":
        return ProvisionPhase.MACHINE_CREATED, None, "bmh_provisioning"

    # BMH available
    if bmh.exists and bmh.state == "available":
        return ProvisionPhase.BMH_AVAILABLE, None, "machine_created"

    # BMH preparing
    if bmh.exists and bmh.state == "preparing":
        return ProvisionPhase.BMH_PREPARING, None, "bmh_available"

    # BMH inspecting
    if bmh.exists and bmh.state == "inspecting":
        return ProvisionPhase.BMH_INSPECTING, None, "bmh_preparing"

    # BMH registering
    if bmh.exists and bmh.state == "registering":
        return ProvisionPhase.BMH_REGISTERING, None, "bmh_inspecting"

    # BMHi exists but no BMH yet
    if bmhi.exists and not bmh.exists:
        return ProvisionPhase.BMHI_CREATED, None, "bmh_registering"

    # BMHi exists and BMH exists (early state)
    if bmhi.exists and bmh.exists:
        return ProvisionPhase.BMH_REGISTERING, None, "bmh_inspecting"

    # Nothing exists
    return ProvisionPhase.NOT_STARTED, None, "bmhi_created"


async def get_node_provision_progress(
    k8s_adapter: KubernetesAdapter,
    input_data: GetNodeProvisionProgressInput,
    context: UserContext | None = None,
    audit_logger: AuditLogger | None = None,
) -> GetNodeProvisionProgressOutput:
    """Track the complete node provisioning progress.

    This tool queries all resources involved in node provisioning and
    calculates the overall progress through the workflow:

    1. BMHi created → BMH lifecycle (registering → inspecting → preparing → available)
    2. Machine applied → BMH provisioning → provisioned
    3. Machine/LCMMachine deploying → Ready
    4. IpamHost auto-created
    5. Kubernetes Node Ready

    Args:
        k8s_adapter: Kubernetes adapter for API operations.
        input_data: Input parameters with node name and namespace.
        context: User context for RBAC (optional).
        audit_logger: Audit logger for tracking operations (optional).

    Returns:
        GetNodeProvisionProgressOutput with complete provisioning status.

    Raises:
        KubernetesError: If the Kubernetes API calls fail.

    Example:
        >>> async with KubernetesAdapter() as k8s:
        ...     result = await get_node_provision_progress(
        ...         k8s, GetNodeProvisionProgressInput(name="compute-04", namespace="lab")
        ...     )
        ...     print(f"Progress: {result.progress_percent}%")
        ...     print(f"Phase: {result.current_phase}")
        ...     print(f"Next: {result.next_expected_phase}")
    """
    logger.info(
        "getting_node_provision_progress",
        name=input_data.name,
        namespace=input_data.namespace,
    )

    async with audit_tool_execution(
        TOOL_NAME,
        audit_logger,
        context,
        AuditLevel.READ,
        {
            "resource_type": "Node",
            "resource_name": input_data.name,
            "resource_namespace": input_data.namespace,
        },
    ) as audit_details:
        try:
            # Use namespace exactly as specified by user (consistent with list_machines)
            namespace = input_data.namespace

            # Query all resources in parallel
            bmhi_status = await _get_bmhi_status(k8s_adapter, input_data.name, namespace)
            bmh_status = await _get_bmh_status(k8s_adapter, input_data.name, namespace)
            machine_status = await _get_machine_status(k8s_adapter, input_data.name, namespace)
            lcm_status = await _get_lcmmachine_status(k8s_adapter, input_data.name, namespace)
            ipam_status = await _get_ipamhost_status(k8s_adapter, input_data.name, namespace)

            # Get node name from machine status
            node_name = machine_status.details.get("node_ref")
            node_status = await _get_node_status(k8s_adapter, node_name)

            # Determine current phase
            current_phase, error_msg, next_phase = _determine_phase(
                bmhi_status,
                bmh_status,
                machine_status,
                lcm_status,
                ipam_status,
                node_status,
            )

            # Calculate progress
            progress = PHASE_PROGRESS.get(current_phase, 0)

            # Calculate remaining steps
            phase_order = list(PHASE_PROGRESS.keys())
            if current_phase in phase_order:
                current_idx = phase_order.index(current_phase)
                completed_idx = phase_order.index(ProvisionPhase.COMPLETED)
                remaining_steps = max(0, completed_idx - current_idx)
            else:
                remaining_steps = len(phase_order)

            output = GetNodeProvisionProgressOutput(
                name=input_data.name,
                namespace=namespace,
                current_phase=current_phase,
                progress_percent=progress,
                is_complete=current_phase == ProvisionPhase.COMPLETED,
                has_error=current_phase == ProvisionPhase.ERROR,
                error_message=error_msg,
                bmhi_status=bmhi_status,
                bmh_status=bmh_status,
                machine_status=machine_status,
                lcmmachine_status=lcm_status,
                ipamhost_status=ipam_status,
                node_status=node_status,
                next_expected_phase=next_phase,
                estimated_remaining_steps=remaining_steps,
            )

            logger.info(
                "node_provision_progress_retrieved",
                name=input_data.name,
                phase=current_phase.value,
                progress=progress,
                is_complete=output.is_complete,
            )

            # Update audit details
            audit_details["phase"] = current_phase.value
            audit_details["progress"] = progress

            return output

        except Exception as e:
            logger.error(
                "get_node_provision_progress_failed",
                name=input_data.name,
                error=str(e),
            )

            if isinstance(e, KubernetesError):
                raise
            raise KubernetesError(
                f"Failed to get node provision progress: {e}",
                operation="get",
                resource_kind="Node",
                resource_name=input_data.name,
                namespace=input_data.namespace,
            ) from e
