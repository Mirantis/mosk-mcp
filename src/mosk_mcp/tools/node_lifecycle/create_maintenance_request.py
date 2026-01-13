"""Create maintenance request tool for MOSK MCP Server.

This module provides the create_maintenance_request tool for creating
NodeMaintenanceRequest CRs to put nodes into maintenance mode.

Safety Level: NON_DESTRUCTIVE
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import TYPE_CHECKING, Any

import yaml
from pydantic import BaseModel, Field

from mosk_mcp.auth.rbac import ToolSafetyLevel, require_authenticated_context
from mosk_mcp.core.exceptions import KubernetesError, ValidationError
from mosk_mcp.observability.audit import AuditLevel
from mosk_mcp.observability.logging import get_logger
from mosk_mcp.tools.common import audit_tool_execution


if TYPE_CHECKING:
    from mosk_mcp.adapters.kubernetes import KubernetesAdapter
    from mosk_mcp.auth.types import UserContext
    from mosk_mcp.observability.audit import AuditLogger


logger = get_logger(__name__)

# Tool metadata
TOOL_NAME = "create_maintenance_request"
TOOL_SAFETY_LEVEL = ToolSafetyLevel.NON_DESTRUCTIVE
TOOL_DESCRIPTION = (
    "Create a NodeMaintenanceRequest CR to put a node into maintenance mode. "
    "This cordons the node and optionally drains workloads."
)


class MaintenanceReason(str, Enum):
    """Predefined maintenance reasons."""

    HARDWARE_REPAIR = "hardware-repair"
    FIRMWARE_UPDATE = "firmware-update"
    OS_UPGRADE = "os-upgrade"
    SECURITY_PATCH = "security-patch"
    PERFORMANCE_TUNING = "performance-tuning"
    DISK_REPLACEMENT = "disk-replacement"
    NETWORK_MAINTENANCE = "network-maintenance"
    PLANNED_REBOOT = "planned-reboot"
    OTHER = "other"


class CreateMaintenanceRequestInput(BaseModel):
    """Input parameters for create_maintenance_request tool.

    Attributes:
        node_name: Name of the Kubernetes node to put in maintenance.
        reason: Reason for the maintenance.
        description: Detailed description of the maintenance work.
        drain_pods: Whether to drain pods from the node.
        force_drain: Force drain even if pods don't have PDB.
        grace_period_seconds: Grace period for pod termination.
        timeout_minutes: Timeout for the maintenance operation.
        dry_run: Generate template without applying.
        namespace: Namespace for the maintenance request.
    """

    node_name: str = Field(
        ...,
        description="Name of the Kubernetes node to put in maintenance",
        min_length=1,
        max_length=253,
    )
    reason: MaintenanceReason = Field(
        ...,
        description="Reason for the maintenance",
    )
    description: str = Field(
        default="",
        description="Detailed description of the maintenance work",
        max_length=1024,
    )
    drain_pods: bool = Field(
        default=True,
        description="Whether to drain pods from the node",
    )
    force_drain: bool = Field(
        default=False,
        description="Force drain even if pods don't have PodDisruptionBudget",
    )
    grace_period_seconds: int = Field(
        default=300,
        ge=0,
        le=3600,
        description="Grace period for pod termination in seconds",
    )
    timeout_minutes: int = Field(
        default=60,
        ge=1,
        le=1440,
        description="Timeout for the maintenance operation in minutes",
    )
    dry_run: bool = Field(
        default=True,
        description="Generate template without applying to cluster",
    )
    namespace: str = Field(
        default="default",
        description="Namespace for the NodeMaintenanceRequest",
    )


class CreateMaintenanceRequestOutput(BaseModel):
    """Output from create_maintenance_request tool.

    Attributes:
        name: Name of the created/generated maintenance request.
        namespace: Namespace of the maintenance request.
        node_name: Target node name.
        applied: Whether the request was applied to the cluster.
        template_yaml: Generated YAML template.
        template_dict: Template as dictionary.
        status: Current status if applied.
        message: Result message.
        warnings: Any warnings generated.
    """

    name: str = Field(..., description="Maintenance request name")
    namespace: str = Field(..., description="Maintenance request namespace")
    node_name: str = Field(..., description="Target node name")
    applied: bool = Field(..., description="Whether request was applied")
    template_yaml: str = Field(..., description="Generated YAML template")
    template_dict: dict[str, Any] = Field(
        default_factory=dict, description="Template as dictionary"
    )
    status: str = Field(default="Pending", description="Current status")
    message: str = Field(..., description="Result message")
    warnings: list[str] = Field(default_factory=list, description="Warnings generated")


def _generate_maintenance_request_name(node_name: str) -> str:
    """Generate a unique name for the maintenance request.

    Args:
        node_name: Node being maintained.

    Returns:
        Unique maintenance request name.
    """
    timestamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    # Truncate node name if needed to fit K8s name constraints
    max_node_len = 253 - len(f"-maint-{timestamp}")
    truncated_node = node_name[:max_node_len]
    return f"{truncated_node}-maint-{timestamp}"


def _generate_maintenance_request_cr(
    input_data: CreateMaintenanceRequestInput,
    name: str,
) -> dict[str, Any]:
    """Generate NodeMaintenanceRequest CR.

    Args:
        input_data: Input parameters.
        name: Generated request name.

    Returns:
        NodeMaintenanceRequest CR as dictionary.
    """
    cr: dict[str, Any] = {
        "apiVersion": "maintenance.kaas.mirantis.com/v1alpha1",
        "kind": "NodeMaintenanceRequest",
        "metadata": {
            "name": name,
            "namespace": input_data.namespace,
            "labels": {
                "maintenance.kaas.mirantis.com/node": input_data.node_name,
                "maintenance.kaas.mirantis.com/reason": input_data.reason.value,
            },
            "annotations": {
                "maintenance.kaas.mirantis.com/created-by": "mosk-mcp-server",
                "maintenance.kaas.mirantis.com/description": input_data.description,
            },
        },
        "spec": {
            "nodeName": input_data.node_name,
            "reason": input_data.reason.value,
            "drainSpec": {
                "enabled": input_data.drain_pods,
                "force": input_data.force_drain,
                "gracePeriodSeconds": input_data.grace_period_seconds,
                "timeoutSeconds": input_data.timeout_minutes * 60,
            },
        },
    }

    if input_data.description:
        cr["spec"]["description"] = input_data.description

    return cr


async def _validate_node_exists(
    k8s_adapter: KubernetesAdapter,
    node_name: str,
) -> list[str]:
    """Validate that the target node exists and collect warnings.

    Args:
        k8s_adapter: Kubernetes adapter.
        node_name: Node to validate.

    Returns:
        List of warnings.

    Raises:
        ValidationError: If node doesn't exist.
    """
    warnings = []

    try:
        node = await k8s_adapter.get(
            kind="Node",
            name=node_name,
            namespace=None,
        )

        # Check if node is already cordoned
        spec = node.get("spec", {})
        if spec.get("unschedulable", False):
            warnings.append(f"Node {node_name} is already cordoned (unschedulable)")

        # Check node conditions
        status = node.get("status", {})
        conditions = status.get("conditions", [])
        ready_condition = next(
            (c for c in conditions if c.get("type") == "Ready"),
            None,
        )

        if ready_condition and ready_condition.get("status") != "True":
            warnings.append(f"Node {node_name} is not in Ready state")

    except Exception as e:
        raise ValidationError(
            f"Node {node_name} not found or inaccessible",
            field="node_name",
            value=node_name,
            details={"error": str(e)},
        ) from e

    return warnings


async def _check_existing_maintenance(
    k8s_adapter: KubernetesAdapter,
    node_name: str,
    namespace: str,
) -> list[str]:
    """Check for existing maintenance requests for this node.

    Args:
        k8s_adapter: Kubernetes adapter.
        node_name: Node to check.
        namespace: Namespace to check.

    Returns:
        List of warnings about existing requests.
    """
    warnings = []

    try:
        # List existing maintenance requests for this node
        requests = await k8s_adapter.list_custom_resources(
            group="maintenance.kaas.mirantis.com",
            version="v1alpha1",
            plural="nodemaintenancerequests",
            namespace=namespace,
            label_selector=f"maintenance.kaas.mirantis.com/node={node_name}",
        )

        active_requests = []
        for req in requests:
            status = req.get("status", {})
            phase = status.get("phase", "Unknown")
            if phase not in ("Completed", "Failed", "Cancelled"):
                active_requests.append(
                    {
                        "name": req.get("metadata", {}).get("name"),
                        "phase": phase,
                    }
                )

        if active_requests:
            names = [r["name"] for r in active_requests]
            warnings.append(
                f"Node {node_name} has active maintenance request(s): {', '.join(names)}"
            )

    except Exception as e:
        logger.warning(
            "failed_to_check_existing_maintenance",
            node=node_name,
            error=str(e),
        )

    return warnings


@require_authenticated_context(ToolSafetyLevel.NON_DESTRUCTIVE)
async def create_maintenance_request(
    k8s_adapter: KubernetesAdapter,
    input_data: CreateMaintenanceRequestInput,
    context: UserContext | None = None,
    audit_logger: AuditLogger | None = None,
) -> CreateMaintenanceRequestOutput:
    """Create a NodeMaintenanceRequest to put a node in maintenance mode.

    SECURITY: This is a NON_DESTRUCTIVE operation requiring authentication.
    The @require_authenticated_context decorator enforces that a valid
    UserContext with OPERATOR or ADMINISTRATOR role is provided.

    This tool creates a NodeMaintenanceRequest CR that:
    - Cordons the node (prevents new pods from scheduling)
    - Optionally drains existing pods from the node
    - Tracks the maintenance state

    The request can be created in dry-run mode to generate the template
    without applying it to the cluster.

    Args:
        k8s_adapter: Kubernetes adapter for API operations.
        input_data: Input parameters for the maintenance request.
        context: User context for RBAC (optional).
        audit_logger: Audit logger for tracking operations (optional).

    Returns:
        CreateMaintenanceRequestOutput with the generated/applied request.

    Raises:
        ValidationError: If input validation fails.
        KubernetesError: If Kubernetes API calls fail.

    Example:
        >>> async with KubernetesAdapter() as k8s:
        ...     result = await create_maintenance_request(
        ...         k8s,
        ...         CreateMaintenanceRequestInput(
        ...             node_name="compute-01",
        ...             reason=MaintenanceReason.HARDWARE_REPAIR,
        ...             description="Replacing failed disk",
        ...             dry_run=False,
        ...         ),
        ...     )
        ...     print(f"Created maintenance request: {result.name}")
    """
    logger.info(
        "creating_maintenance_request",
        node_name=input_data.node_name,
        reason=input_data.reason.value,
        dry_run=input_data.dry_run,
    )

    async with audit_tool_execution(
        TOOL_NAME,
        audit_logger,
        context,
        AuditLevel.WRITE,
        {
            "resource_type": "NodeMaintenanceRequest",
            "resource_name": input_data.node_name,
            "resource_namespace": input_data.namespace,
            "reason": input_data.reason.value,
            "dry_run": input_data.dry_run,
            "drain_pods": input_data.drain_pods,
        },
    ) as audit_details:
        warnings: list[str] = []

        try:
            # Validate node exists
            node_warnings = await _validate_node_exists(
                k8s_adapter,
                input_data.node_name,
            )
            warnings.extend(node_warnings)

            # Check for existing maintenance requests
            existing_warnings = await _check_existing_maintenance(
                k8s_adapter,
                input_data.node_name,
                input_data.namespace,
            )
            warnings.extend(existing_warnings)

            # Generate request name and CR
            request_name = _generate_maintenance_request_name(input_data.node_name)
            request_cr = _generate_maintenance_request_cr(input_data, request_name)

            # Generate YAML
            template_yaml = yaml.dump(
                request_cr,
                default_flow_style=False,
                sort_keys=False,
                allow_unicode=True,
            )

            applied = False
            status = "Generated"
            message = "Maintenance request template generated (dry-run mode)"

            # Apply if not dry-run
            if not input_data.dry_run:
                try:
                    await k8s_adapter.create_custom_resource(
                        group="maintenance.kaas.mirantis.com",
                        version="v1alpha1",
                        plural="nodemaintenancerequests",
                        namespace=input_data.namespace,
                        resource=request_cr,
                    )
                    applied = True
                    status = "Created"
                    message = f"Maintenance request '{request_name}' created successfully"

                    logger.info(
                        "maintenance_request_created",
                        name=request_name,
                        node=input_data.node_name,
                    )

                except Exception as e:
                    logger.error(
                        "failed_to_create_maintenance_request",
                        name=request_name,
                        error=str(e),
                    )
                    raise KubernetesError(
                        f"Failed to create maintenance request: {e}",
                        operation="create",
                        resource_kind="NodeMaintenanceRequest",
                        resource_name=request_name,
                        namespace=input_data.namespace,
                    ) from e

            output = CreateMaintenanceRequestOutput(
                name=request_name,
                namespace=input_data.namespace,
                node_name=input_data.node_name,
                applied=applied,
                template_yaml=template_yaml,
                template_dict=request_cr,
                status=status,
                message=message,
                warnings=warnings,
            )

            # Update audit details
            audit_details["applied"] = applied
            audit_details["request_name"] = request_name
            audit_details["warnings_count"] = len(warnings)

            return output

        except ValidationError:
            raise

        except Exception as e:
            logger.error(
                "create_maintenance_request_failed",
                node_name=input_data.node_name,
                error=str(e),
            )

            if isinstance(e, (KubernetesError, ValidationError)):
                raise
            raise KubernetesError(
                f"Failed to create maintenance request: {e}",
                operation="create",
                resource_kind="NodeMaintenanceRequest",
            ) from e
