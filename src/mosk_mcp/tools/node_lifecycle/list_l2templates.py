"""List L2Templates tool for MOSK MCP Server.

This module provides the list_l2templates tool for discovering available
L2Template resources that define network configurations for nodes.

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
TOOL_NAME = "list_l2templates"
TOOL_SAFETY_LEVEL = ToolSafetyLevel.READ_ONLY
TOOL_DESCRIPTION = (
    "List available L2Template resources that define network configurations "
    "for MOSK nodes. L2Templates specify bonds, bridges, VLANs, and IP assignments."
)


class ListL2TemplatesInput(BaseModel):
    """Input parameters for list_l2templates tool.

    Attributes:
        namespace: Kubernetes namespace. Use '*' for all namespaces.
        label_selector: Label selector for filtering templates.
        limit: Maximum number of templates to return.
    """

    namespace: str = Field(
        default="default",
        description="Namespace to list L2Templates from. Use '*' for all namespaces.",
    )
    label_selector: str | None = Field(
        default=None,
        description="Label selector for filtering (e.g., 'role=compute')",
    )
    limit: int = Field(
        default=50,
        ge=1,
        le=200,
        description="Maximum number of L2Templates to return",
    )


class L2TemplateSummary(BaseModel):
    """Summary information for a single L2Template.

    Attributes:
        name: Template name.
        namespace: Template namespace.
        state: Template state (OK, Error, etc.).
        network_count: Number of networks defined.
        bond_count: Number of bonds defined.
        bridge_count: Number of bridges defined.
        vlan_count: Number of VLANs defined.
        labels: Template labels.
        age_seconds: Age in seconds.
    """

    name: str = Field(..., description="Template name")
    namespace: str = Field(..., description="Template namespace")
    state: str = Field(..., description="Template state")
    network_count: int = Field(0, description="Number of networks defined")
    bond_count: int = Field(0, description="Number of bonds defined")
    bridge_count: int = Field(0, description="Number of bridges defined")
    vlan_count: int = Field(0, description="Number of VLANs defined")
    labels: dict[str, str] = Field(default_factory=dict, description="Template labels")
    age_seconds: float | None = Field(None, description="Age in seconds")


class ListL2TemplatesOutput(BaseModel):
    """Output from list_l2templates tool.

    Attributes:
        templates: List of L2Template summaries.
        total_count: Total number of templates found.
        namespace: Namespace that was queried.
    """

    templates: list[L2TemplateSummary] = Field(
        default_factory=list, description="List of L2Template summaries"
    )
    total_count: int = Field(..., description="Total templates found")
    namespace: str = Field(..., description="Namespace queried")


def _extract_l2template_summary(data: dict[str, Any]) -> L2TemplateSummary:
    """Extract summary from L2Template CR.

    Args:
        data: Raw L2Template data from Kubernetes API.

    Returns:
        L2TemplateSummary with extracted information.
    """
    metadata = data.get("metadata", {})
    spec = data.get("spec", {})
    status = data.get("status", {})

    # Count network elements
    bond_count = len(spec.get("bonds", {}))
    bridge_count = len(spec.get("bridges", {}))
    vlan_count = len(spec.get("vlans", {}))
    network_count = len(spec.get("networks", []))

    # Calculate age using shared utility
    age_seconds = calculate_resource_age(metadata)

    return L2TemplateSummary(
        name=metadata.get("name", "unknown"),
        namespace=metadata.get("namespace", "default"),
        state=status.get("state", "Unknown"),
        network_count=network_count,
        bond_count=bond_count,
        bridge_count=bridge_count,
        vlan_count=vlan_count,
        labels=metadata.get("labels", {}),
        age_seconds=age_seconds,
    )


async def list_l2templates(
    k8s_adapter: KubernetesAdapter,
    input_data: ListL2TemplatesInput,
    context: UserContext | None = None,
    audit_logger: AuditLogger | None = None,
) -> ListL2TemplatesOutput:
    """List available L2Template resources.

    L2Templates define the network configuration applied to nodes including
    bonds, bridges, VLANs, and IP address assignments. They are referenced
    by IpamHost resources to configure node networking.

    Args:
        k8s_adapter: Kubernetes adapter for API operations.
        input_data: Input parameters for the query.
        context: User context for RBAC (optional).
        audit_logger: Audit logger for tracking operations (optional).

    Returns:
        ListL2TemplatesOutput with template summaries.

    Raises:
        KubernetesError: If the Kubernetes API call fails.

    Example:
        >>> async with KubernetesAdapter() as k8s:
        ...     result = await list_l2templates(k8s, ListL2TemplatesInput(namespace="lab"))
        ...     for tpl in result.templates:
        ...         print(f"{tpl.name}: {tpl.bridge_count} bridges, {tpl.vlan_count} VLANs")
    """
    logger.info(
        "listing_l2templates",
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

            raw_templates = await k8s_adapter.list_custom_resources(
                group="ipam.mirantis.com",
                version="v1alpha1",
                plural="l2templates",
                namespace=namespace,
                label_selector=input_data.label_selector,
            )

            total_count = len(raw_templates)

            # Extract summaries and apply limit
            templates = [_extract_l2template_summary(t) for t in raw_templates]
            templates = templates[: input_data.limit]

            logger.info(
                "l2templates_listed",
                total_count=total_count,
                namespace=input_data.namespace,
            )

            # Update audit details
            audit_details["total_count"] = total_count

            return ListL2TemplatesOutput(
                templates=templates,
                total_count=total_count,
                namespace=input_data.namespace,
            )

        except Exception as e:
            logger.error(
                "list_l2templates_failed",
                error=str(e),
                namespace=input_data.namespace,
            )

            if isinstance(e, KubernetesError):
                raise
            raise KubernetesError(
                f"Failed to list L2Templates: {e}",
                operation="list",
                resource_kind="L2Template",
                namespace=input_data.namespace,
            ) from e
