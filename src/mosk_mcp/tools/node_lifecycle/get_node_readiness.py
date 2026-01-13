"""Get node readiness tool for MOSK MCP Server.

This module provides the get_node_readiness tool for checking whether
a node is ready for operations like maintenance, upgrade, or removal.

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
TOOL_NAME = "get_node_readiness"
TOOL_SAFETY_LEVEL = ToolSafetyLevel.READ_ONLY
TOOL_DESCRIPTION = (
    "Check if a node is ready for operations like maintenance, upgrade, or removal. "
    "Evaluates node conditions, pending pods, and resource availability."
)


class ReadinessCheckType(str, Enum):
    """Types of readiness checks."""

    MAINTENANCE = "maintenance"
    UPGRADE = "upgrade"
    REMOVAL = "removal"
    DRAIN = "drain"
    GENERAL = "general"


class CheckSeverity(str, Enum):
    """Severity levels for readiness check results."""

    PASS = "pass"
    WARNING = "warning"
    FAIL = "fail"
    INFO = "info"


class GetNodeReadinessInput(BaseModel):
    """Input parameters for get_node_readiness tool.

    Attributes:
        name: Machine name to check.
        namespace: Kubernetes namespace of the machine.
        check_type: Type of operation to check readiness for.
        check_ceph: Include Ceph-related readiness checks.
        check_openstack: Include OpenStack service checks.
    """

    name: str = Field(
        ...,
        description="Name of the machine/node to check",
        min_length=1,
        max_length=253,
    )
    namespace: str = Field(
        default="default",
        description="Kubernetes namespace of the machine",
    )
    check_type: ReadinessCheckType = Field(
        default=ReadinessCheckType.GENERAL,
        description="Type of operation to check readiness for",
    )
    check_ceph: bool = Field(
        default=True,
        description="Include Ceph-related readiness checks",
    )
    check_openstack: bool = Field(
        default=True,
        description="Include OpenStack service checks",
    )


class NodeConditionStatus(BaseModel):
    """Status of a node condition.

    Attributes:
        type: Condition type (e.g., Ready, MemoryPressure).
        status: Current status (True, False, Unknown).
        reason: Machine-readable reason.
        message: Human-readable message.
        is_healthy: Whether this condition indicates a healthy state.
    """

    type: str = Field(..., description="Condition type")
    status: str = Field(..., description="Current status")
    reason: str | None = Field(None, description="Reason code")
    message: str | None = Field(None, description="Human-readable message")
    is_healthy: bool = Field(..., description="Whether condition is healthy")


class ReadinessCheck(BaseModel):
    """Result of a single readiness check.

    Attributes:
        name: Check name.
        description: What this check evaluates.
        severity: Check result severity.
        passed: Whether the check passed.
        message: Result message.
        details: Additional details.
        remediation: Suggested remediation if failed.
    """

    name: str = Field(..., description="Check name")
    description: str = Field(..., description="What this check evaluates")
    severity: CheckSeverity = Field(..., description="Result severity")
    passed: bool = Field(..., description="Whether check passed")
    message: str = Field(..., description="Result message")
    details: dict[str, Any] = Field(default_factory=dict, description="Additional details")
    remediation: str | None = Field(None, description="Suggested remediation if failed")


class GetNodeReadinessOutput(BaseModel):
    """Output from get_node_readiness tool.

    Attributes:
        name: Machine/node name.
        namespace: Machine namespace.
        node_name: Kubernetes node name.
        check_type: Type of readiness check performed.
        is_ready: Overall readiness status.
        ready_for_operation: Whether node is ready for the specified operation.
        node_conditions: Current node conditions.
        checks: List of individual readiness checks.
        summary: Summary of check results.
        blocking_issues: List of issues blocking readiness.
        warnings: List of warnings that don't block readiness.
        recommendations: Recommendations before proceeding.
    """

    name: str = Field(..., description="Machine name")
    namespace: str = Field(..., description="Machine namespace")
    node_name: str | None = Field(None, description="Kubernetes node name")
    check_type: str = Field(..., description="Type of check performed")
    is_ready: bool = Field(..., description="Overall node readiness")
    ready_for_operation: bool = Field(..., description="Ready for specified operation")
    node_conditions: list[NodeConditionStatus] = Field(
        default_factory=list, description="Node conditions"
    )
    checks: list[ReadinessCheck] = Field(
        default_factory=list, description="Individual check results"
    )
    summary: dict[str, int] = Field(default_factory=dict, description="Summary of check results")
    blocking_issues: list[str] = Field(
        default_factory=list, description="Issues blocking readiness"
    )
    warnings: list[str] = Field(default_factory=list, description="Non-blocking warnings")
    recommendations: list[str] = Field(default_factory=list, description="Recommendations")


async def _check_node_conditions(
    k8s_adapter: KubernetesAdapter,
    node_name: str,
) -> tuple[list[NodeConditionStatus], list[ReadinessCheck]]:
    """Check Kubernetes node conditions.

    Args:
        k8s_adapter: Kubernetes adapter.
        node_name: Node name.

    Returns:
        Tuple of (conditions, checks).
    """
    conditions = []
    checks = []

    try:
        node = await k8s_adapter.get(
            kind="Node",
            name=node_name,
            namespace=None,
        )

        node_conditions = node.get("status", {}).get("conditions", [])

        # Expected healthy states for each condition type
        healthy_states = {
            "Ready": "True",
            "MemoryPressure": "False",
            "DiskPressure": "False",
            "PIDPressure": "False",
            "NetworkUnavailable": "False",
        }

        for cond in node_conditions:
            cond_type = cond.get("type", "Unknown")
            cond_status = cond.get("status", "Unknown")
            expected = healthy_states.get(cond_type)

            is_healthy = expected is None or cond_status == expected

            conditions.append(
                NodeConditionStatus(
                    type=cond_type,
                    status=cond_status,
                    reason=cond.get("reason"),
                    message=cond.get("message"),
                    is_healthy=is_healthy,
                )
            )

            # Create check for each important condition
            if cond_type in healthy_states:
                passed = is_healthy
                severity = CheckSeverity.PASS if passed else CheckSeverity.FAIL

                checks.append(
                    ReadinessCheck(
                        name=f"node_condition_{cond_type.lower()}",
                        description=f"Node condition {cond_type} check",
                        severity=severity,
                        passed=passed,
                        message=(
                            f"{cond_type} is {cond_status}"
                            if passed
                            else f"{cond_type} is unhealthy: {cond.get('message', 'No details')}"
                        ),
                        details={
                            "condition_type": cond_type,
                            "status": cond_status,
                            "expected": expected,
                        },
                        remediation=(
                            None
                            if passed
                            else f"Investigate {cond_type} condition on node {node_name}"
                        ),
                    )
                )

    except ResourceNotFoundError:
        checks.append(
            ReadinessCheck(
                name="node_exists",
                description="Check if Kubernetes node exists",
                severity=CheckSeverity.FAIL,
                passed=False,
                message=f"Node {node_name} not found in cluster",
                remediation="Verify the machine is fully provisioned and the node has joined the cluster",
            )
        )
    except Exception as e:
        logger.warning("failed_to_check_node", node=node_name, error=str(e))
        checks.append(
            ReadinessCheck(
                name="node_accessible",
                description="Check if node is accessible",
                severity=CheckSeverity.WARNING,
                passed=False,
                message=f"Could not check node conditions: {e}",
            )
        )

    return conditions, checks


async def _check_pending_pods(
    k8s_adapter: KubernetesAdapter,
    node_name: str,
    check_type: ReadinessCheckType,
) -> list[ReadinessCheck]:
    """Check for pending or critical pods on the node.

    Args:
        k8s_adapter: Kubernetes adapter.
        node_name: Node name.
        check_type: Type of operation being checked.

    Returns:
        List of readiness checks.
    """
    checks = []

    try:
        # Get all pods on this node
        all_pods = await k8s_adapter.list(
            kind="Pod",
            namespace="*",
            field_selector=f"spec.nodeName={node_name}",
        )

        total_pods = len(all_pods)
        running_pods = 0
        pending_pods = 0
        failed_pods = 0
        critical_pods = []

        # Critical namespaces that should be handled carefully
        critical_namespaces = {"kube-system", "openstack", "rook-ceph", "metallb-system"}

        for pod in all_pods:
            phase = pod.get("status", {}).get("phase", "Unknown")
            namespace = pod.get("metadata", {}).get("namespace", "")
            pod_name = pod.get("metadata", {}).get("name", "")

            if phase == "Running":
                running_pods += 1
            elif phase == "Pending":
                pending_pods += 1
            elif phase == "Failed":
                failed_pods += 1

            # Track critical pods
            if namespace in critical_namespaces:
                critical_pods.append(f"{namespace}/{pod_name}")

        # Check for pending pods
        if pending_pods > 0:
            checks.append(
                ReadinessCheck(
                    name="pending_pods",
                    description="Check for pending pods on node",
                    severity=CheckSeverity.WARNING,
                    passed=pending_pods < 5,  # Allow some pending pods
                    message=f"{pending_pods} pending pods on node",
                    details={"pending_count": pending_pods},
                    remediation="Wait for pending pods to be scheduled or investigate scheduling issues",
                )
            )

        # Check for failed pods
        if failed_pods > 0:
            checks.append(
                ReadinessCheck(
                    name="failed_pods",
                    description="Check for failed pods on node",
                    severity=CheckSeverity.WARNING,
                    passed=False,
                    message=f"{failed_pods} failed pods on node",
                    details={"failed_count": failed_pods},
                    remediation="Investigate and clean up failed pods before proceeding",
                )
            )

        # Report critical pods for drain/removal operations
        if check_type in (ReadinessCheckType.DRAIN, ReadinessCheckType.REMOVAL):
            checks.append(
                ReadinessCheck(
                    name="critical_pods",
                    description="Check for critical infrastructure pods",
                    severity=CheckSeverity.INFO,
                    passed=True,
                    message=f"{len(critical_pods)} critical pods on node",
                    details={
                        "critical_count": len(critical_pods),
                        "critical_namespaces": list(critical_namespaces),
                    },
                )
            )

        # Overall pod check
        checks.append(
            ReadinessCheck(
                name="pod_summary",
                description="Overall pod status on node",
                severity=CheckSeverity.PASS if running_pods > 0 else CheckSeverity.INFO,
                passed=True,
                message=f"Total: {total_pods}, Running: {running_pods}, Pending: {pending_pods}",
                details={
                    "total": total_pods,
                    "running": running_pods,
                    "pending": pending_pods,
                    "failed": failed_pods,
                },
            )
        )

    except Exception as e:
        logger.warning("failed_to_check_pods", node=node_name, error=str(e))
        checks.append(
            ReadinessCheck(
                name="pod_check",
                description="Check pods on node",
                severity=CheckSeverity.WARNING,
                passed=False,
                message=f"Could not check pods: {e}",
            )
        )

    return checks


async def _check_machine_status(
    k8s_adapter: KubernetesAdapter,
    machine_name: str,
    namespace: str,
) -> tuple[str | None, list[ReadinessCheck]]:
    """Check machine CR status.

    Args:
        k8s_adapter: Kubernetes adapter.
        machine_name: Machine name.
        namespace: Machine namespace.

    Returns:
        Tuple of (node_name, checks).
    """
    checks = []
    node_name = None

    try:
        machine = await k8s_adapter.get_machine(
            name=machine_name,
            namespace=namespace,
        )

        status = machine.get("status", {})
        phase = status.get("phase", "Unknown")
        node_ref = status.get("nodeRef", {})
        node_name = node_ref.get("name")

        # Check machine phase
        is_running = phase == "Running"
        checks.append(
            ReadinessCheck(
                name="machine_phase",
                description="Check machine phase is Running",
                severity=CheckSeverity.PASS if is_running else CheckSeverity.FAIL,
                passed=is_running,
                message=f"Machine phase is {phase}",
                details={"phase": phase},
                remediation=(
                    None
                    if is_running
                    else f"Wait for machine to reach Running phase (current: {phase})"
                ),
            )
        )

        # Check node reference
        has_node = node_name is not None
        checks.append(
            ReadinessCheck(
                name="machine_node_ref",
                description="Check machine has node reference",
                severity=CheckSeverity.PASS if has_node else CheckSeverity.FAIL,
                passed=has_node,
                message=(
                    f"Machine is linked to node {node_name}"
                    if has_node
                    else "Machine has no node reference"
                ),
                details={"node_name": node_name},
                remediation=(None if has_node else "Wait for machine provisioning to complete"),
            )
        )

        # Check for errors
        error_reason = status.get("errorReason")
        error_message = status.get("errorMessage")
        if error_reason or error_message:
            checks.append(
                ReadinessCheck(
                    name="machine_errors",
                    description="Check for machine errors",
                    severity=CheckSeverity.FAIL,
                    passed=False,
                    message=f"Machine has error: {error_reason} - {error_message}",
                    details={
                        "error_reason": error_reason,
                        "error_message": error_message,
                    },
                    remediation="Investigate and resolve machine error before proceeding",
                )
            )

    except ResourceNotFoundError:
        # Try to find the machine in other common namespaces
        found_namespace = None
        common_namespaces = ["mosk", "default", "lab", "kaas"]
        for ns in common_namespaces:
            if ns == namespace:
                continue  # Already tried this one
            try:
                await k8s_adapter.get_machine(name=machine_name, namespace=ns)
                found_namespace = ns
                break
            except ResourceNotFoundError:
                continue
            except Exception:
                continue

        if found_namespace:
            checks.append(
                ReadinessCheck(
                    name="machine_exists",
                    description="Check if machine exists",
                    severity=CheckSeverity.FAIL,
                    passed=False,
                    message=f"Machine {machine_name} not found in namespace '{namespace}' but exists in namespace '{found_namespace}'",
                    remediation=f"Use namespace='{found_namespace}' instead of '{namespace}'",
                    details={"searched_namespace": namespace, "found_namespace": found_namespace},
                )
            )
        else:
            checks.append(
                ReadinessCheck(
                    name="machine_exists",
                    description="Check if machine exists",
                    severity=CheckSeverity.FAIL,
                    passed=False,
                    message=f"Machine {machine_name} not found in namespace '{namespace}'",
                    remediation="Verify the machine name and namespace are correct. Common namespaces: mosk, default, lab",
                    details={"searched_namespace": namespace, "searched_common_namespaces": common_namespaces},
                )
            )
    except Exception as e:
        logger.warning("failed_to_check_machine", machine=machine_name, error=str(e))
        checks.append(
            ReadinessCheck(
                name="machine_accessible",
                description="Check if machine is accessible",
                severity=CheckSeverity.WARNING,
                passed=False,
                message=f"Could not check machine: {e}",
            )
        )

    return node_name, checks


async def _check_ceph_health(
    k8s_adapter: KubernetesAdapter,
    node_name: str,
) -> list[ReadinessCheck]:
    """Check Ceph-related readiness.

    Args:
        k8s_adapter: Kubernetes adapter.
        node_name: Node name.

    Returns:
        List of readiness checks.
    """
    checks = []

    try:
        # Check for OSDs on this node
        osd_pods = await k8s_adapter.list(
            kind="Pod",
            namespace="rook-ceph",
            label_selector="app=rook-ceph-osd",
            field_selector=f"spec.nodeName={node_name}",
        )

        osd_count = len(osd_pods)

        if osd_count > 0:
            checks.append(
                ReadinessCheck(
                    name="ceph_osds_on_node",
                    description="Check for Ceph OSDs on this node",
                    severity=CheckSeverity.INFO,
                    passed=True,
                    message=f"Node has {osd_count} Ceph OSD(s)",
                    details={"osd_count": osd_count},
                    remediation=(
                        "For maintenance/removal, ensure Ceph cluster is healthy "
                        "and consider reweighting OSDs before draining"
                    ),
                )
            )
        else:
            checks.append(
                ReadinessCheck(
                    name="ceph_osds_on_node",
                    description="Check for Ceph OSDs on this node",
                    severity=CheckSeverity.PASS,
                    passed=True,
                    message="No Ceph OSDs on this node",
                    details={"osd_count": 0},
                )
            )

    except Exception as e:
        logger.warning("failed_to_check_ceph", node=node_name, error=str(e))
        checks.append(
            ReadinessCheck(
                name="ceph_check",
                description="Check Ceph status",
                severity=CheckSeverity.WARNING,
                passed=True,  # Don't block on Ceph check failure
                message=f"Could not check Ceph status: {e}",
            )
        )

    return checks


async def _check_openstack_services(
    k8s_adapter: KubernetesAdapter,
    node_name: str,
) -> list[ReadinessCheck]:
    """Check OpenStack service readiness.

    Args:
        k8s_adapter: Kubernetes adapter.
        node_name: Node name.

    Returns:
        List of readiness checks.
    """
    checks = []

    try:
        # Check for OpenStack pods on this node
        os_pods = await k8s_adapter.list(
            kind="Pod",
            namespace="openstack",
            field_selector=f"spec.nodeName={node_name}",
        )

        os_pod_count = len(os_pods)

        # Identify key services
        compute_pods = [
            p for p in os_pods if "nova-compute" in p.get("metadata", {}).get("name", "")
        ]

        network_pods = [
            p
            for p in os_pods
            if any(
                svc in p.get("metadata", {}).get("name", "")
                for svc in ["neutron-l3", "neutron-dhcp", "neutron-metadata"]
            )
        ]

        if compute_pods:
            checks.append(
                ReadinessCheck(
                    name="nova_compute_on_node",
                    description="Check for Nova compute on this node",
                    severity=CheckSeverity.INFO,
                    passed=True,
                    message=f"Node runs {len(compute_pods)} Nova compute pod(s)",
                    details={"compute_pod_count": len(compute_pods)},
                    remediation=(
                        "For maintenance/removal, migrate or evacuate VMs "
                        "before draining this compute node"
                    ),
                )
            )

        if network_pods:
            checks.append(
                ReadinessCheck(
                    name="neutron_agents_on_node",
                    description="Check for Neutron agents on this node",
                    severity=CheckSeverity.INFO,
                    passed=True,
                    message=f"Node runs {len(network_pods)} Neutron agent pod(s)",
                    details={"network_pod_count": len(network_pods)},
                    remediation=(
                        "For maintenance/removal, ensure network agents can be "
                        "rescheduled or HA failover is configured"
                    ),
                )
            )

        checks.append(
            ReadinessCheck(
                name="openstack_pods_summary",
                description="OpenStack pods on this node",
                severity=CheckSeverity.PASS,
                passed=True,
                message=f"Node runs {os_pod_count} OpenStack pod(s)",
                details={"total_openstack_pods": os_pod_count},
            )
        )

    except Exception as e:
        logger.warning("failed_to_check_openstack", node=node_name, error=str(e))
        checks.append(
            ReadinessCheck(
                name="openstack_check",
                description="Check OpenStack services",
                severity=CheckSeverity.WARNING,
                passed=True,  # Don't block on OpenStack check failure
                message=f"Could not check OpenStack services: {e}",
            )
        )

    return checks


async def get_node_readiness(
    mcc_adapter: KubernetesAdapter,
    input_data: GetNodeReadinessInput,
    mosk_adapter: KubernetesAdapter | None = None,
    context: UserContext | None = None,
    audit_logger: AuditLogger | None = None,
) -> GetNodeReadinessOutput:
    """Check if a node is ready for operations.

    This tool evaluates various readiness conditions for a node including:
    - Machine CR status and phase
    - Kubernetes node conditions
    - Pending and critical pods
    - Ceph storage status (if applicable)
    - OpenStack services (if applicable)

    The readiness check can be tailored for different operation types:
    - maintenance: Check if node can be put into maintenance
    - upgrade: Check if node is ready for upgrade
    - removal: Check if node can be safely removed
    - drain: Check if node can be drained
    - general: General health check

    Args:
        mcc_adapter: MCC Kubernetes adapter for Machine CR lookups.
        input_data: Input parameters specifying the node and check type.
        mosk_adapter: MOSK Kubernetes adapter for Node/Pod checks.
        context: User context for RBAC (optional).
        audit_logger: Audit logger for tracking operations (optional).

    Returns:
        GetNodeReadinessOutput with comprehensive readiness information.

    Raises:
        KubernetesError: If Kubernetes API calls fail.

    Example:
        >>> async with KubernetesAdapter() as mcc, KubernetesAdapter() as mosk:
        ...     result = await get_node_readiness(
        ...         mcc,
        ...         mosk,
        ...         GetNodeReadinessInput(
        ...             name="compute-01", check_type=ReadinessCheckType.MAINTENANCE
        ...         ),
        ...     )
        ...     if result.ready_for_operation:
        ...         print("Node is ready for maintenance")
    """
    # Use mosk_adapter for Node/Pod checks, fall back to mcc_adapter if not provided
    k8s_adapter = mosk_adapter if mosk_adapter is not None else mcc_adapter
    logger.info(
        "checking_node_readiness",
        name=input_data.name,
        namespace=input_data.namespace,
        check_type=input_data.check_type.value,
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
            "check_type": input_data.check_type.value,
        },
    ) as audit_details:
        all_checks: list[ReadinessCheck] = []
        node_conditions: list[NodeConditionStatus] = []
        blocking_issues: list[str] = []
        warnings: list[str] = []
        recommendations: list[str] = []

        try:
            # Check machine status and get node name (Machine CRs are on MCC)
            node_name, machine_checks = await _check_machine_status(
                mcc_adapter,
                input_data.name,
                namespace,
            )
            all_checks.extend(machine_checks)

            # If we have a node name, check node conditions
            if node_name:
                conditions, node_checks = await _check_node_conditions(
                    k8s_adapter,
                    node_name,
                )
                node_conditions = conditions
                all_checks.extend(node_checks)

                # Check pods on the node
                pod_checks = await _check_pending_pods(
                    k8s_adapter,
                    node_name,
                    input_data.check_type,
                )
                all_checks.extend(pod_checks)

                # Check Ceph if requested
                if input_data.check_ceph:
                    ceph_checks = await _check_ceph_health(k8s_adapter, node_name)
                    all_checks.extend(ceph_checks)

                # Check OpenStack if requested
                if input_data.check_openstack:
                    os_checks = await _check_openstack_services(k8s_adapter, node_name)
                    all_checks.extend(os_checks)

            # Analyze results
            passed_count = sum(1 for c in all_checks if c.passed)
            failed_count = sum(
                1 for c in all_checks if not c.passed and c.severity == CheckSeverity.FAIL
            )
            warning_count = sum(
                1 for c in all_checks if c.severity == CheckSeverity.WARNING and not c.passed
            )

            # Determine overall readiness
            is_ready = all(c.is_healthy for c in node_conditions) if node_conditions else False
            ready_for_operation = failed_count == 0

            # Collect blocking issues and warnings
            for check in all_checks:
                if not check.passed:
                    if check.severity == CheckSeverity.FAIL:
                        blocking_issues.append(check.message)
                    elif check.severity == CheckSeverity.WARNING:
                        warnings.append(check.message)

                # Collect recommendations from remediation fields
                if check.remediation and not check.passed:
                    recommendations.append(check.remediation)

            # Add operation-specific recommendations
            if input_data.check_type == ReadinessCheckType.MAINTENANCE:
                recommendations.append(
                    "Create a NodeMaintenanceRequest before proceeding with maintenance"
                )
            elif input_data.check_type == ReadinessCheckType.REMOVAL:
                recommendations.append(
                    "Ensure all workloads are migrated and data is safe before removal"
                )

            summary = {
                "passed": passed_count,
                "failed": failed_count,
                "warnings": warning_count,
                "total": len(all_checks),
            }

            output = GetNodeReadinessOutput(
                name=input_data.name,
                namespace=namespace,
                node_name=node_name,
                check_type=input_data.check_type.value,
                is_ready=is_ready,
                ready_for_operation=ready_for_operation,
                node_conditions=node_conditions,
                checks=all_checks,
                summary=summary,
                blocking_issues=blocking_issues,
                warnings=warnings,
                recommendations=recommendations,
            )

            logger.info(
                "node_readiness_checked",
                name=input_data.name,
                is_ready=is_ready,
                ready_for_operation=ready_for_operation,
                passed=passed_count,
                failed=failed_count,
            )

            # Update audit details
            audit_details["is_ready"] = is_ready
            audit_details["ready_for_operation"] = ready_for_operation
            audit_details["summary"] = summary

            return output

        except Exception as e:
            logger.error(
                "node_readiness_check_failed",
                name=input_data.name,
                error=str(e),
            )

            if isinstance(e, (KubernetesError, ResourceNotFoundError)):
                raise
            raise KubernetesError(
                f"Failed to check node readiness: {e}",
                operation="get",
                resource_kind="Machine",
                resource_name=input_data.name,
                namespace=namespace,
            ) from e
