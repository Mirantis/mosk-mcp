"""List BareMetalHostProfiles tool for MOSK MCP Server.

This module provides the list_bmhp tool for discovering available
BareMetalHostProfile resources that define hardware configuration templates.

Safety Level: READ_ONLY
"""

from __future__ import annotations

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
TOOL_NAME = "list_bmhp"
TOOL_SAFETY_LEVEL = ToolSafetyLevel.READ_ONLY
TOOL_DESCRIPTION = (
    "List available BareMetalHostProfile resources that define hardware "
    "configuration templates including disk selection, kernel parameters, "
    "and device settings. Required for Machine creation."
)


class ListBMHPInput(BaseModel):
    """Input parameters for list_bmhp tool.

    Attributes:
        namespace: Kubernetes namespace. Use '*' for all namespaces.
        label_selector: Label selector for filtering profiles.
        limit: Maximum number of profiles to return.
    """

    namespace: str = Field(
        default="default",
        description="Namespace to list BMHPs from. Use '*' for all namespaces.",
    )
    label_selector: str | None = Field(
        default=None,
        description="Label selector for filtering (e.g., 'role=compute')",
    )
    limit: int = Field(
        default=50,
        ge=1,
        le=200,
        description="Maximum number of BMHPs to return",
    )


class BMHPSummary(BaseModel):
    """Summary information for a single BareMetalHostProfile.

    Attributes:
        name: Profile name.
        namespace: Profile namespace.
        is_default: Whether this is the default profile.
        has_root_device_hints: Whether root device hints are configured.
        kernel_parameters: Kernel parameters (can be list or dict with sysctl).
        has_pre_deploy_script: Whether a pre-deploy script is configured.
        has_post_deploy_script: Whether a post-deploy script is configured.
        labels: Profile labels.
        age_seconds: Age in seconds.
    """

    name: str = Field(..., description="Profile name")
    namespace: str = Field(..., description="Profile namespace")
    is_default: bool = Field(False, description="Whether this is the default profile")
    has_root_device_hints: bool = Field(
        False, description="Whether root device hints are configured"
    )
    kernel_parameters: list[str] | dict[str, Any] = Field(
        default_factory=list, description="Kernel parameters"
    )
    has_pre_deploy_script: bool = Field(False, description="Has pre-deploy script")
    has_post_deploy_script: bool = Field(False, description="Has post-deploy script")
    labels: dict[str, str] = Field(default_factory=dict, description="Profile labels")
    age_seconds: float | None = Field(None, description="Age in seconds")


class ListBMHPOutput(BaseModel):
    """Output from list_bmhp tool.

    Attributes:
        profiles: List of BMHP summaries.
        total_count: Total number of profiles found.
        namespace: Namespace that was queried.
        default_profile: Name of the default profile if found.
    """

    profiles: list[BMHPSummary] = Field(default_factory=list, description="List of BMHP summaries")
    total_count: int = Field(..., description="Total profiles found")
    namespace: str = Field(..., description="Namespace queried")
    default_profile: str | None = Field(None, description="Default profile name")


def _extract_bmhp_summary(data: dict[str, Any]) -> BMHPSummary:
    """Extract summary from BareMetalHostProfile CR.

    Args:
        data: Raw BMHP data from Kubernetes API.

    Returns:
        BMHPSummary with extracted information.
    """
    metadata = data.get("metadata", {})
    spec = data.get("spec", {})
    labels = metadata.get("labels", {})

    # Check if default
    is_default = labels.get("kaas.mirantis.com/default") == "true"

    # Check hardware profile
    hardware_profile = spec.get("hardwareProfile", {})
    has_root_device_hints = "rootDeviceHints" in hardware_profile

    # Get kernel parameters - can be list or dict with sysctl
    kernel_params_raw = spec.get("kernelParameters", [])
    # Convert to native dict if it's a Box object
    if hasattr(kernel_params_raw, "to_dict"):
        kernel_params = kernel_params_raw.to_dict()
    elif isinstance(kernel_params_raw, dict):
        kernel_params = dict(kernel_params_raw)
    else:
        kernel_params = kernel_params_raw

    # Check scripts
    has_pre_deploy = bool(spec.get("preDeployScript"))
    has_post_deploy = bool(spec.get("postDeployScript"))

    # Calculate age using shared utility
    age_seconds = calculate_resource_age(metadata)

    return BMHPSummary(
        name=metadata.get("name", "unknown"),
        namespace=metadata.get("namespace", "default"),
        is_default=is_default,
        has_root_device_hints=has_root_device_hints,
        kernel_parameters=kernel_params,
        has_pre_deploy_script=has_pre_deploy,
        has_post_deploy_script=has_post_deploy,
        labels=labels,
        age_seconds=age_seconds,
    )


async def list_bmhp(
    k8s_adapter: KubernetesAdapter,
    input_data: ListBMHPInput,
    context: UserContext | None = None,
    audit_logger: AuditLogger | None = None,
) -> ListBMHPOutput:
    """List available BareMetalHostProfile resources.

    BareMetalHostProfiles define hardware configuration templates that are
    applied to nodes during provisioning. They specify disk selection hints,
    kernel parameters, GRUB configuration, and pre/post deployment scripts.

    Args:
        k8s_adapter: Kubernetes adapter for API operations.
        input_data: Input parameters for the query.
        context: User context for RBAC (optional).
        audit_logger: Audit logger for tracking operations (optional).

    Returns:
        ListBMHPOutput with profile summaries.

    Raises:
        KubernetesError: If the Kubernetes API call fails.

    Example:
        >>> async with KubernetesAdapter() as k8s:
        ...     result = await list_bmhp(k8s, ListBMHPInput(namespace="lab"))
        ...     for profile in result.profiles:
        ...         print(f"{profile.name}: default={profile.is_default}")
    """
    logger.info(
        "listing_bmhp",
        namespace=input_data.namespace,
    )

    async with audit_tool_execution(
        TOOL_NAME,
        audit_logger,
        context,
        AuditLevel.READ,
        {"namespace": input_data.namespace},
    ) as audit_details:
        try:
            namespace = None if input_data.namespace == "*" else input_data.namespace

            raw_profiles = await k8s_adapter.list_custom_resources(
                group="metal3.io",
                version="v1alpha1",
                plural="baremetalhostprofiles",
                namespace=namespace,
                label_selector=input_data.label_selector,
            )

            total_count = len(raw_profiles)

            # Extract summaries
            profiles = [_extract_bmhp_summary(p) for p in raw_profiles]

            # Find default profile
            default_profile = None
            for p in profiles:
                if p.is_default:
                    default_profile = p.name
                    break

            # Apply limit
            profiles = profiles[: input_data.limit]

            logger.info(
                "bmhp_listed",
                total_count=total_count,
                namespace=input_data.namespace,
                default_profile=default_profile,
            )

            # Update audit details
            audit_details["total_count"] = total_count

            return ListBMHPOutput(
                profiles=profiles,
                total_count=total_count,
                namespace=input_data.namespace,
                default_profile=default_profile,
            )

        except Exception as e:
            logger.error(
                "list_bmhp_failed",
                error=str(e),
                namespace=input_data.namespace,
            )

            if isinstance(e, KubernetesError):
                raise
            raise KubernetesError(
                f"Failed to list BareMetalHostProfiles: {e}",
                operation="list",
                resource_kind="BareMetalHostProfile",
                namespace=input_data.namespace,
            ) from e
