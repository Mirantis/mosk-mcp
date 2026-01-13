"""Get machine details tool for MOSK MCP Server.

This module provides the get_machine_details tool for retrieving detailed
information about a specific Machine CR including conditions, events,
and related resources.

Safety Level: READ_ONLY
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from mosk_mcp.adapters.crd.machine import Machine
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
TOOL_NAME = "get_machine_details"
TOOL_SAFETY_LEVEL = ToolSafetyLevel.READ_ONLY
TOOL_DESCRIPTION = (
    "Get detailed information about a specific machine including conditions, "
    "events, and related resources."
)


class GetMachineDetailsInput(BaseModel):
    """Input parameters for get_machine_details tool.

    Attributes:
        name: Machine name to retrieve.
        namespace: Kubernetes namespace.
        include_events: Include recent events related to the machine.
        include_related: Include information about related resources.
    """

    name: str = Field(
        ...,
        description="Name of the machine to retrieve",
        min_length=1,
        max_length=253,
    )
    namespace: str = Field(
        default="default",
        description="Kubernetes namespace",
    )
    include_events: bool = Field(
        default=True,
        description="Include recent events related to the machine",
    )
    include_related: bool = Field(
        default=True,
        description="Include information about related resources (BMHi, BMHp, Node)",
    )


class MachineCondition(BaseModel):
    """A condition on a Machine resource.

    Attributes:
        type: Condition type (e.g., Ready, Provisioned).
        status: Condition status (True, False, Unknown).
        reason: Machine-readable reason for the condition.
        message: Human-readable message.
        last_transition_time: When the condition last changed.
    """

    type: str = Field(..., description="Condition type")
    status: str = Field(..., description="Condition status")
    reason: str | None = Field(None, description="Reason code")
    message: str | None = Field(None, description="Human-readable message")
    last_transition_time: str | None = Field(None, description="Last transition time")


class MachineEvent(BaseModel):
    """An event related to a Machine resource.

    Attributes:
        type: Event type (Normal, Warning).
        reason: Short reason for the event.
        message: Human-readable message.
        count: Number of occurrences.
        first_timestamp: When the event first occurred.
        last_timestamp: When the event last occurred.
        source: Component that generated the event.
    """

    type: str = Field(..., description="Event type (Normal/Warning)")
    reason: str = Field(..., description="Short reason")
    message: str = Field(..., description="Event message")
    count: int = Field(default=1, description="Occurrence count")
    first_timestamp: str | None = Field(None, description="First occurrence")
    last_timestamp: str | None = Field(None, description="Last occurrence")
    source: str | None = Field(None, description="Event source component")


class RelatedResource(BaseModel):
    """Information about a resource related to the Machine.

    Attributes:
        kind: Resource kind.
        name: Resource name.
        namespace: Resource namespace.
        exists: Whether the resource exists.
        status: Brief status summary.
        details: Additional details if available.
    """

    kind: str = Field(..., description="Resource kind")
    name: str = Field(..., description="Resource name")
    namespace: str | None = Field(None, description="Resource namespace")
    exists: bool = Field(..., description="Whether resource exists")
    status: str | None = Field(None, description="Brief status summary")
    details: dict[str, Any] = Field(default_factory=dict, description="Additional details")


class GetMachineDetailsOutput(BaseModel):
    """Output from get_machine_details tool.

    Attributes:
        name: Machine name.
        namespace: Machine namespace.
        role: Machine role.
        phase: Current machine phase.
        api_version: API version of the resource.
        creation_timestamp: When the machine was created.
        labels: Machine labels.
        annotations: Machine annotations.
        spec: Full machine specification.
        status: Full machine status.
        conditions: List of machine conditions.
        events: Recent events if requested.
        related_resources: Related resources if requested.
        addresses: Network addresses.
        provider_status: Provider-specific status.
        error_info: Error information if in failed state.
    """

    name: str = Field(..., description="Machine name")
    namespace: str = Field(..., description="Machine namespace")
    role: str = Field(..., description="Machine role")
    phase: str = Field(..., description="Current phase")
    api_version: str = Field(..., description="API version")
    creation_timestamp: str | None = Field(None, description="Creation time")
    labels: dict[str, str] = Field(default_factory=dict, description="Machine labels")
    annotations: dict[str, str] = Field(default_factory=dict, description="Machine annotations")
    spec: dict[str, Any] = Field(default_factory=dict, description="Machine specification")
    status: dict[str, Any] = Field(default_factory=dict, description="Machine status")
    conditions: list[MachineCondition] = Field(
        default_factory=list, description="Machine conditions"
    )
    events: list[MachineEvent] = Field(default_factory=list, description="Recent events")
    related_resources: list[RelatedResource] = Field(
        default_factory=list, description="Related resources"
    )
    addresses: list[dict[str, str]] = Field(default_factory=list, description="Network addresses")
    provider_status: dict[str, Any] = Field(
        default_factory=dict, description="Provider-specific status"
    )
    error_info: dict[str, str] | None = Field(None, description="Error information if failed")


def _parse_conditions(conditions_data: list[dict[str, Any]]) -> list[MachineCondition]:
    """Parse condition data from Kubernetes API.

    Args:
        conditions_data: Raw conditions from API response.

    Returns:
        List of MachineCondition objects.
    """
    conditions = []
    for cond in conditions_data:
        conditions.append(
            MachineCondition(
                type=cond.get("type", "Unknown"),
                status=cond.get("status", "Unknown"),
                reason=cond.get("reason"),
                message=cond.get("message"),
                last_transition_time=cond.get("lastTransitionTime"),
            )
        )
    return conditions


async def _get_machine_events(
    k8s_adapter: KubernetesAdapter,
    machine_name: str,
    namespace: str,
) -> list[MachineEvent]:
    """Get events related to a machine.

    Args:
        k8s_adapter: Kubernetes adapter.
        machine_name: Machine name.
        namespace: Machine namespace.

    Returns:
        List of related events.
    """
    events = []
    try:
        # Query events for this machine
        # Events reference the involved object by name
        raw_events = await k8s_adapter.list(
            kind="Event",
            namespace=namespace,
            field_selector=f"involvedObject.name={machine_name},involvedObject.kind=Machine",
        )

        for event in raw_events:
            events.append(
                MachineEvent(
                    type=event.get("type", "Normal"),
                    reason=event.get("reason", "Unknown"),
                    message=event.get("message", ""),
                    count=event.get("count", 1),
                    first_timestamp=event.get("firstTimestamp"),
                    last_timestamp=event.get("lastTimestamp"),
                    source=event.get("source", {}).get("component"),
                )
            )

        # Sort by last timestamp descending
        events.sort(
            key=lambda e: e.last_timestamp or "",
            reverse=True,
        )

        # Limit to 20 most recent events
        events = events[:20]

    except Exception as e:
        logger.warning(
            "failed_to_get_machine_events",
            machine=machine_name,
            error=str(e),
        )

    return events


async def _get_related_resources(
    k8s_adapter: KubernetesAdapter,
    machine_data: dict[str, Any],
    namespace: str,
) -> list[RelatedResource]:
    """Get information about resources related to the machine.

    Args:
        k8s_adapter: Kubernetes adapter.
        machine_data: Machine CR data.
        namespace: Machine namespace.

    Returns:
        List of related resource information.
    """
    related = []
    metadata = machine_data.get("metadata", {})
    spec = machine_data.get("spec", {})
    status = machine_data.get("status", {})
    machine_name = metadata.get("name", "")

    # Provider spec
    provider_spec = spec.get("providerSpec", {}).get("value", {})

    # Extract profile reference - handle both string and object formats
    profile_ref = provider_spec.get("bareMetalHostProfile", "")
    if isinstance(profile_ref, dict):
        profile_name = profile_ref.get("name", "")
        profile_namespace = profile_ref.get("namespace")
    elif isinstance(profile_ref, str):
        profile_name = profile_ref
        profile_namespace = None
    else:
        profile_name = ""
        profile_namespace = None

    # Check BareMetalHostProfile
    # Use the profile's namespace if specified, otherwise use the machine's namespace
    bmhp_namespace = profile_namespace or namespace

    if profile_name:
        try:
            bmhp = await k8s_adapter.get_custom_resource(
                group="kaas.mirantis.com",
                version="v1alpha1",
                plural="baremetalhostprofiles",
                name=profile_name,
                namespace=bmhp_namespace,
            )
            related.append(
                RelatedResource(
                    kind="BareMetalHostProfile",
                    name=profile_name,
                    namespace=bmhp_namespace,
                    exists=True,
                    status="Available",
                    details={
                        "role": bmhp.get("metadata", {}).get("labels", {}).get("role", "unknown"),
                    },
                )
            )
        except ResourceNotFoundError:
            related.append(
                RelatedResource(
                    kind="BareMetalHostProfile",
                    name=profile_name,
                    namespace=bmhp_namespace,
                    exists=False,
                    status="Missing",
                )
            )
        except Exception as e:
            logger.warning(
                "failed_to_check_bmhp",
                profile=profile_name,
                namespace=bmhp_namespace,
                error=str(e),
            )

    # Check BareMetalHostInventory (same name as machine)
    try:
        bmhi = await k8s_adapter.get_custom_resource(
            group="kaas.mirantis.com",
            version="v1alpha1",
            plural="baremetalhostinventories",
            name=machine_name,
            namespace=namespace,
        )
        bmhi_status = bmhi.get("status", {})
        related.append(
            RelatedResource(
                kind="BareMetalHostInventory",
                name=machine_name,
                namespace=namespace,
                exists=True,
                status=bmhi_status.get("operationalStatus", "Unknown"),
                details={
                    "power_state": bmhi_status.get("powerState", "unknown"),
                    "provisioning_state": bmhi_status.get("provisioningState", "unknown"),
                },
            )
        )
    except ResourceNotFoundError:
        related.append(
            RelatedResource(
                kind="BareMetalHostInventory",
                name=machine_name,
                namespace=namespace,
                exists=False,
                status="Missing",
            )
        )
    except Exception as e:
        logger.warning(
            "failed_to_check_bmhi",
            machine=machine_name,
            error=str(e),
        )

    # Check Kubernetes Node
    node_ref = status.get("nodeRef", {})
    node_name = node_ref.get("name")
    if node_name:
        try:
            node = await k8s_adapter.get(
                kind="Node",
                name=node_name,
                namespace=None,  # Nodes are cluster-scoped
            )
            node_status = node.get("status", {})
            node_conditions = node_status.get("conditions", [])

            # Find Ready condition
            ready_condition = next(
                (c for c in node_conditions if c.get("type") == "Ready"),
                None,
            )
            node_ready = ready_condition.get("status") == "True" if ready_condition else False

            related.append(
                RelatedResource(
                    kind="Node",
                    name=node_name,
                    namespace=None,
                    exists=True,
                    status="Ready" if node_ready else "NotReady",
                    details={
                        "kubelet_version": node_status.get("nodeInfo", {}).get(
                            "kubeletVersion", "unknown"
                        ),
                        "os_image": node_status.get("nodeInfo", {}).get("osImage", "unknown"),
                    },
                )
            )
        except ResourceNotFoundError:
            related.append(
                RelatedResource(
                    kind="Node",
                    name=node_name,
                    namespace=None,
                    exists=False,
                    status="Missing",
                )
            )
        except Exception as e:
            logger.warning(
                "failed_to_check_node",
                node=node_name,
                error=str(e),
            )

    return related


async def get_machine_details(
    k8s_adapter: KubernetesAdapter,
    input_data: GetMachineDetailsInput,
    context: UserContext | None = None,
    audit_logger: AuditLogger | None = None,
) -> GetMachineDetailsOutput:
    """Get detailed information about a specific machine.

    This tool retrieves comprehensive information about a Machine CR,
    including its conditions, events, and related resources like
    BareMetalHostInventory, BareMetalHostProfile, and Kubernetes Node.

    Args:
        k8s_adapter: Kubernetes adapter for API operations.
        input_data: Input parameters specifying which machine to retrieve.
        context: User context for RBAC (optional).
        audit_logger: Audit logger for tracking operations (optional).

    Returns:
        GetMachineDetailsOutput with complete machine details.

    Raises:
        ResourceNotFoundError: If the machine does not exist.
        KubernetesError: If the Kubernetes API call fails.

    Example:
        >>> async with KubernetesAdapter() as k8s:
        ...     result = await get_machine_details(
        ...         k8s, GetMachineDetailsInput(name="compute-01", namespace="default")
        ...     )
        ...     print(f"Machine {result.name} is in phase {result.phase}")
    """
    logger.info(
        "getting_machine_details",
        name=input_data.name,
        namespace=input_data.namespace,
    )

    namespace = input_data.namespace

    async with audit_tool_execution(
        TOOL_NAME,
        audit_logger,
        context,
        AuditLevel.READ,
        {
            "resource_type": "Machine",
            "resource_name": input_data.name,
            "resource_namespace": namespace,
        },
    ):
        try:
            # Get the machine
            machine_data = await k8s_adapter.get_machine(
                name=input_data.name,
                namespace=namespace,
            )

            # Parse the machine using the CRD model
            machine = Machine.from_kubernetes(machine_data)

            metadata = machine_data.get("metadata", {})
            spec = machine_data.get("spec", {})
            status = machine_data.get("status", {})

            # Parse conditions
            conditions = _parse_conditions(status.get("conditions", []))

            # Get events if requested
            events = []
            if input_data.include_events:
                events = await _get_machine_events(
                    k8s_adapter,
                    input_data.name,
                    namespace,
                )

            # Get related resources if requested
            related_resources = []
            if input_data.include_related:
                related_resources = await _get_related_resources(
                    k8s_adapter,
                    machine_data,
                    namespace,
                )

            # Extract addresses
            addresses = status.get("addresses", [])

            # Extract provider status
            provider_status = status.get("providerStatus", {})

            # Extract error info if present
            error_info = None
            if status.get("errorReason") or status.get("errorMessage"):
                error_info = {
                    "reason": status.get("errorReason", "Unknown"),
                    "message": status.get("errorMessage", "No message provided"),
                }

            output = GetMachineDetailsOutput(
                name=input_data.name,
                namespace=namespace,
                role=machine.role,
                phase=status.get("phase", "Unknown"),
                api_version=machine_data.get("apiVersion", "kaas.mirantis.com/v1alpha1"),
                creation_timestamp=metadata.get("creationTimestamp"),
                labels=metadata.get("labels", {}),
                annotations=metadata.get("annotations", {}),
                spec=spec,
                status=status,
                conditions=conditions,
                events=events,
                related_resources=related_resources,
                addresses=addresses,
                provider_status=provider_status,
                error_info=error_info,
            )

            logger.info(
                "machine_details_retrieved",
                name=input_data.name,
                phase=output.phase,
                conditions_count=len(conditions),
                events_count=len(events),
            )

            return output

        except ResourceNotFoundError:
            logger.warning(
                "machine_not_found",
                name=input_data.name,
                namespace=namespace,
            )
            raise

        except Exception as e:
            logger.error(
                "get_machine_details_failed",
                name=input_data.name,
                error=str(e),
            )

            if isinstance(e, (KubernetesError, ResourceNotFoundError)):
                raise
            raise KubernetesError(
                f"Failed to get machine details: {e}",
                operation="get",
                resource_kind="Machine",
                resource_name=input_data.name,
                namespace=namespace,
            ) from e
