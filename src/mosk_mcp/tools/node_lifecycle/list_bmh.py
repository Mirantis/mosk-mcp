"""List BareMetalHost resources tool for MOSK MCP Server.

This module provides the list_bmh tool for querying BareMetalHost CRs
with their lifecycle states. BMH is automatically created from BMHi
and goes through states: registering -> inspecting -> preparing ->
available -> provisioning -> provisioned.

Safety Level: READ_ONLY
"""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

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
TOOL_NAME = "list_bmh"
TOOL_SAFETY_LEVEL = ToolSafetyLevel.READ_ONLY
TOOL_DESCRIPTION = (
    "List all BareMetalHost resources with their lifecycle state. "
    "BMH lifecycle: registering -> inspecting -> preparing -> available -> "
    "provisioning -> provisioned. Use to track hardware provisioning progress."
)


class BMHStateFilter(str, Enum):
    """Filter options for BMH state."""

    ALL = "all"
    REGISTERING = "registering"
    INSPECTING = "inspecting"
    PREPARING = "preparing"
    AVAILABLE = "available"
    PROVISIONING = "provisioning"
    PROVISIONED = "provisioned"
    DEPROVISIONING = "deprovisioning"
    ERROR = "error"


class BMHOperationalStatusFilter(str, Enum):
    """Filter options for BMH operational status."""

    ALL = "all"
    OK = "OK"
    ERROR = "error"
    DISCOVERED = "discovered"


class ListBMHInput(BaseModel):
    """Input parameters for list_bmh tool.

    Attributes:
        namespace: Kubernetes namespace. Use '*' for all namespaces.
        state_filter: Filter by provisioning state.
        status_filter: Filter by operational status.
        label_selector: Additional label selector for filtering.
        limit: Maximum number of BMH resources to return.
    """

    namespace: str = Field(
        default="default",
        description="Namespace to list BMH from. Use '*' for all namespaces.",
    )
    state_filter: BMHStateFilter = Field(
        default=BMHStateFilter.ALL,
        description="Filter by provisioning state",
    )
    status_filter: BMHOperationalStatusFilter = Field(
        default=BMHOperationalStatusFilter.ALL,
        description="Filter by operational status",
    )
    label_selector: str | None = Field(
        default=None,
        description="Additional Kubernetes label selector",
    )
    limit: int = Field(
        default=100,
        ge=1,
        le=500,
        description="Maximum number of BMH resources to return",
    )


class BMHListSummary(BaseModel):
    """Summary statistics for a list of BareMetalHost resources.

    Attributes:
        by_state: Count of BMH by provisioning state.
        by_operational_status: Count of BMH by operational status.
        online_count: Number of BMH that are powered on.
        error_count: Number of BMH in error state.
        provisioned_count: Number of BMH in provisioned state.
        in_progress_count: Number of BMH in transitional states.
    """

    by_state: dict[str, int] = Field(
        default_factory=dict, description="Count of BMH by provisioning state"
    )
    by_operational_status: dict[str, int] = Field(
        default_factory=dict, description="Count of BMH by operational status"
    )
    online_count: int = Field(default=0, ge=0, description="Number of powered on BMH")
    error_count: int = Field(default=0, ge=0, description="Number of BMH in error state")
    provisioned_count: int = Field(default=0, ge=0, description="Number of provisioned BMH")
    in_progress_count: int = Field(
        default=0, ge=0, description="Number of BMH in transitional states"
    )


class BMHSummary(BaseModel):
    """Summary information for a single BareMetalHost.

    Attributes:
        name: BMH name.
        namespace: BMH namespace.
        state: Provisioning state (registering, inspecting, etc.).
        operational_status: Operational status (OK, error, discovered).
        bmc_address: BMC address for out-of-band management.
        online: Whether the host is powered on.
        consumer: Name of consuming resource (usually Machine name).
        error_message: Error message if in error state.
        age_seconds: Age of the BMH in seconds.
        hardware_vendor: Hardware vendor if discovered.
        hardware_model: Hardware model if discovered.
    """

    name: str = Field(..., description="BMH name")
    namespace: str = Field(..., description="BMH namespace")
    state: str = Field(..., description="Provisioning state")
    operational_status: str = Field(..., description="Operational status")
    bmc_address: str | None = Field(None, description="BMC address")
    online: bool = Field(False, description="Power state")
    consumer: str | None = Field(None, description="Consuming resource name")
    error_message: str | None = Field(None, description="Error message if failed")
    age_seconds: float | None = Field(None, description="Age in seconds")
    hardware_vendor: str | None = Field(None, description="Hardware vendor")
    hardware_model: str | None = Field(None, description="Hardware model")


class ListBMHOutput(BaseModel):
    """Output from list_bmh tool.

    Attributes:
        bmh_list: List of BMH summaries.
        total_count: Total number of BMH found.
        filtered_count: Number after filtering.
        namespace: Namespace that was queried.
        summary: Summary statistics by state.
    """

    bmh_list: list[BMHSummary] = Field(default_factory=list, description="List of BMH summaries")
    total_count: int = Field(..., description="Total BMH found")
    filtered_count: int = Field(..., description="BMH after filtering")
    namespace: str = Field(..., description="Namespace queried")
    summary: BMHListSummary = Field(
        default_factory=BMHListSummary, description="Summary statistics"
    )


def _extract_bmh_summary(bmh_data: dict[str, Any]) -> BMHSummary:
    """Extract summary information from a BareMetalHost CR.

    Args:
        bmh_data: Raw BMH data from Kubernetes API.

    Returns:
        BMHSummary with extracted information.
    """
    metadata = bmh_data.get("metadata", {})
    spec = bmh_data.get("spec", {})
    status = bmh_data.get("status", {})
    provisioning = status.get("provisioning", {})

    # Extract state
    state = provisioning.get("state", "unknown")

    # Extract operational status
    operational_status = status.get("operationalStatus", "unknown")

    # Extract BMC address
    bmc = spec.get("bmc", {})
    bmc_address = bmc.get("address")

    # Extract consumer reference
    consumer_ref = spec.get("consumerRef", {})
    consumer = consumer_ref.get("name") if consumer_ref else None

    # Calculate age using shared utility
    age_seconds = calculate_resource_age(metadata)

    # Extract hardware details
    hardware = status.get("hardware", {})
    system_vendor = hardware.get("systemVendor", {})
    hardware_vendor = system_vendor.get("manufacturer")
    hardware_model = system_vendor.get("productName")

    # Extract error message
    error_message = status.get("errorMessage")

    return BMHSummary(
        name=metadata.get("name", "unknown"),
        namespace=metadata.get("namespace", "default"),
        state=state,
        operational_status=operational_status,
        bmc_address=bmc_address,
        online=status.get("poweredOn", False),
        consumer=consumer,
        error_message=error_message,
        age_seconds=age_seconds,
        hardware_vendor=hardware_vendor,
        hardware_model=hardware_model,
    )


def _apply_filters(
    bmh_list: list[BMHSummary],
    state_filter: BMHStateFilter,
    status_filter: BMHOperationalStatusFilter,
) -> list[BMHSummary]:
    """Apply state and status filters to BMH list.

    Args:
        bmh_list: List of BMH summaries.
        state_filter: State filter to apply.
        status_filter: Status filter to apply.

    Returns:
        Filtered list of BMH.
    """
    result = bmh_list

    # Apply state filter
    if state_filter != BMHStateFilter.ALL:
        result = [b for b in result if b.state.lower() == state_filter.value.lower()]

    # Apply status filter
    if status_filter != BMHOperationalStatusFilter.ALL:
        result = [b for b in result if b.operational_status.lower() == status_filter.value.lower()]

    return result


def _generate_summary(bmh_list: list[BMHSummary]) -> BMHListSummary:
    """Generate summary statistics for BMH resources.

    Args:
        bmh_list: List of BMH summaries.

    Returns:
        BMHListSummary with state and status counts.
    """
    by_state: dict[str, int] = {}
    by_status: dict[str, int] = {}
    online_count = 0
    error_count = 0
    provisioned_count = 0
    in_progress_count = 0

    for bmh in bmh_list:
        # Count by state
        by_state[bmh.state] = by_state.get(bmh.state, 0) + 1

        # Count by operational status
        by_status[bmh.operational_status] = by_status.get(bmh.operational_status, 0) + 1

        # Count online
        if bmh.online:
            online_count += 1

        # Count by status type
        if bmh.operational_status.lower() == "error" or bmh.error_message:
            error_count += 1
        if bmh.state == "provisioned":
            provisioned_count += 1
        if bmh.state in (
            "registering",
            "inspecting",
            "preparing",
            "provisioning",
            "deprovisioning",
        ):
            in_progress_count += 1

    return BMHListSummary(
        by_state=by_state,
        by_operational_status=by_status,
        online_count=online_count,
        error_count=error_count,
        provisioned_count=provisioned_count,
        in_progress_count=in_progress_count,
    )


async def list_bmh(
    k8s_adapter: KubernetesAdapter,
    input_data: ListBMHInput,
    context: UserContext | None = None,
    audit_logger: AuditLogger | None = None,
) -> ListBMHOutput:
    """List all BareMetalHost resources with their lifecycle state.

    This tool queries BareMetalHost CRs and returns a summary of each
    including its provisioning state, operational status, and hardware info.

    BMH Lifecycle States:
    - registering: Initial state, BMH is being registered
    - inspecting: Hardware inspection in progress
    - preparing: Preparing for provisioning
    - available: Ready to be provisioned
    - provisioning: OS deployment in progress
    - provisioned: Successfully provisioned
    - deprovisioning: Being deprovisioned
    - error: An error occurred

    Args:
        k8s_adapter: Kubernetes adapter for API operations.
        input_data: Input parameters for the query.
        context: User context for RBAC (optional).
        audit_logger: Audit logger for tracking operations (optional).

    Returns:
        ListBMHOutput with BMH summaries and statistics.

    Raises:
        KubernetesError: If the Kubernetes API call fails.

    Example:
        >>> async with KubernetesAdapter() as k8s:
        ...     result = await list_bmh(
        ...         k8s, ListBMHInput(namespace="lab", state_filter="provisioning")
        ...     )
        ...     for bmh in result.bmh_list:
        ...         print(f"{bmh.name}: {bmh.state}")
    """
    logger.info(
        "listing_bmh",
        namespace=input_data.namespace,
        state_filter=input_data.state_filter.value,
        status_filter=input_data.status_filter.value,
    )

    async with audit_tool_execution(
        TOOL_NAME,
        audit_logger,
        context,
        AuditLevel.READ,
        {
            "namespace": input_data.namespace,
            "state_filter": input_data.state_filter.value,
            "status_filter": input_data.status_filter.value,
        },
    ) as audit_details:
        try:
            # Query BMH from Kubernetes
            namespace = None if input_data.namespace == "*" else input_data.namespace

            raw_bmh = await k8s_adapter.list_custom_resources(
                group="metal3.io",
                version="v1alpha1",
                plural="baremetalhosts",
                namespace=namespace,
                label_selector=input_data.label_selector,
            )

            total_count = len(raw_bmh)

            # Extract summaries
            bmh_list = [_extract_bmh_summary(b) for b in raw_bmh]

            # Apply filters
            filtered_bmh = _apply_filters(
                bmh_list,
                input_data.state_filter,
                input_data.status_filter,
            )

            # Apply limit
            filtered_bmh = filtered_bmh[: input_data.limit]
            filtered_count = len(filtered_bmh)

            # Generate summary
            summary = _generate_summary(filtered_bmh)

            logger.info(
                "bmh_listed",
                total_count=total_count,
                filtered_count=filtered_count,
                namespace=input_data.namespace,
            )

            # Update audit details
            audit_details["total_count"] = total_count
            audit_details["filtered_count"] = filtered_count

            return ListBMHOutput(
                bmh_list=filtered_bmh,
                total_count=total_count,
                filtered_count=filtered_count,
                namespace=input_data.namespace,
                summary=summary,
            )

        except Exception as e:
            logger.error(
                "list_bmh_failed",
                error=str(e),
                namespace=input_data.namespace,
            )

            if isinstance(e, KubernetesError):
                raise
            raise KubernetesError(
                f"Failed to list BareMetalHosts: {e}",
                operation="list",
                resource_kind="BareMetalHost",
                namespace=input_data.namespace,
            ) from e
