"""Get node conditions tool.

This module provides the get_node_conditions tool that retrieves
node conditions and readiness gates for Kubernetes nodes, helping
identify unhealthy or problematic nodes.

Safety Level: READ_ONLY
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from mosk_mcp.core.exceptions import ResourceNotFoundError, ToolExecutionError
from mosk_mcp.observability.logging import get_logger
from mosk_mcp.tools.common.enums import HealthStatus
from mosk_mcp.tools.operations_visibility.models import (
    Condition,
    ConditionStatus,
    GetNodeConditionsInput,
    GetNodeConditionsOutput,
    NodeConditionInfo,
    NodeTaint,
)


if TYPE_CHECKING:
    from mosk_mcp.adapters.kubernetes import KubernetesAdapter


logger = get_logger(__name__)


# Standard node condition types and their healthy states
NODE_CONDITION_HEALTHY_STATE = {
    "Ready": ConditionStatus.TRUE,
    "MemoryPressure": ConditionStatus.FALSE,
    "DiskPressure": ConditionStatus.FALSE,
    "PIDPressure": ConditionStatus.FALSE,
    "NetworkUnavailable": ConditionStatus.FALSE,
}


def _parse_condition(cond_data: dict[str, Any]) -> Condition:
    """Parse a Kubernetes condition.

    Args:
        cond_data: Raw condition from API.

    Returns:
        Parsed Condition object.
    """
    status_str = cond_data.get("status", "Unknown")
    try:
        status = ConditionStatus(status_str)
    except ValueError:
        status = ConditionStatus.UNKNOWN

    return Condition(
        type=cond_data.get("type", "Unknown"),
        status=status,
        reason=cond_data.get("reason"),
        message=cond_data.get("message"),
        last_transition_time=cond_data.get("lastTransitionTime"),
        last_update_time=cond_data.get("lastHeartbeatTime"),
    )


def _parse_taint(taint_data: dict[str, Any]) -> NodeTaint:
    """Parse a node taint.

    Args:
        taint_data: Raw taint from API.

    Returns:
        Parsed NodeTaint object.
    """
    return NodeTaint(
        key=taint_data.get("key", ""),
        value=taint_data.get("value"),
        effect=taint_data.get("effect", "NoSchedule"),
    )


def _determine_node_role(labels: dict[str, str]) -> str:
    """Determine node role from labels.

    Args:
        labels: Node labels.

    Returns:
        Node role string.
    """
    roles = []

    # Check for standard role labels
    for key in labels:
        if key.startswith("node-role.kubernetes.io/"):
            role = key.split("/")[-1]
            roles.append(role)

    # Check for MOSK-specific labels (both legacy and current patterns)
    if labels.get("openstack-control-plane") == "enabled":
        roles.append("control")
    if (
        labels.get("hostlabel.bm.kaas.mirantis.com/controlplane") == "controlplane"
        and "control" not in roles
    ):
        roles.append("control")
    if labels.get("openstack-compute-node") == "enabled":
        roles.append("compute")
    if labels.get("hostlabel.bm.kaas.mirantis.com/worker") == "worker" and "compute" not in roles:
        roles.append("compute")
    if labels.get("openstack-gateway") == "enabled":
        roles.append("gateway")
    if labels.get("ceph-osd-node") == "enabled":
        roles.append("storage")

    return ", ".join(roles) if roles else "worker"


def _check_node_issues(
    conditions: list[Condition],
    taints: list[NodeTaint],
    is_schedulable: bool,
) -> list[str]:
    """Check for issues with a node.

    Args:
        conditions: Node conditions.
        taints: Node taints.
        is_schedulable: Whether node is schedulable.

    Returns:
        List of issue descriptions.
    """
    issues: list[str] = []

    # Check conditions
    for cond in conditions:
        expected_status = NODE_CONDITION_HEALTHY_STATE.get(cond.type)
        if expected_status and cond.status != expected_status:
            if cond.type == "Ready":
                issues.append(f"Node is not Ready: {cond.message or cond.reason or 'unknown'}")
            else:
                issues.append(f"{cond.type} detected: {cond.message or cond.reason or ''}")

    # Check taints
    for taint in taints:
        if taint.effect in ("NoSchedule", "NoExecute"):
            if taint.key == "node.kubernetes.io/unschedulable":
                issues.append("Node is cordoned")
            elif taint.key == "node.kubernetes.io/unreachable":
                issues.append("Node is unreachable")
            elif taint.key == "node.kubernetes.io/not-ready":
                issues.append("Node is marked not-ready")
            elif taint.key == "node.kubernetes.io/disk-pressure":
                issues.append("Node has disk pressure")
            elif taint.key == "node.kubernetes.io/memory-pressure":
                issues.append("Node has memory pressure")

    if not is_schedulable and "Node is cordoned" not in issues:
        issues.append("Node is unschedulable")

    return issues


def _determine_health_summary(
    is_ready: bool,
    is_schedulable: bool,
    issues: list[str],
) -> str:
    """Generate health summary for a node.

    Args:
        is_ready: Whether node is Ready.
        is_schedulable: Whether node is schedulable.
        issues: List of issues.

    Returns:
        Health summary string.
    """
    if not is_ready:
        return "Not Ready - node may have issues"
    if not is_schedulable:
        return "Ready but cordoned/unschedulable"
    if issues:
        return f"Ready with {len(issues)} issue(s)"
    return "Healthy and ready for workloads"


def _parse_node(node: dict[str, Any], include_labels: bool) -> NodeConditionInfo:
    """Parse a Node resource to NodeConditionInfo.

    Args:
        node: Node resource.
        include_labels: Whether to include labels.

    Returns:
        Parsed NodeConditionInfo.
    """
    metadata = node.get("metadata", {})
    spec = node.get("spec", {})
    status = node.get("status", {})

    name = metadata.get("name", "unknown")
    labels = metadata.get("labels", {})

    # Determine role
    role = _determine_node_role(labels)

    # Parse conditions
    conditions = [_parse_condition(c) for c in status.get("conditions", [])]

    # Check if ready
    ready_cond = next((c for c in conditions if c.type == "Ready"), None)
    is_ready = ready_cond is not None and ready_cond.status == ConditionStatus.TRUE

    # Check if schedulable
    is_schedulable = not spec.get("unschedulable", False)

    # Parse taints
    taints = [_parse_taint(t) for t in spec.get("taints", [])]

    # Check for issues
    issues = _check_node_issues(conditions, taints, is_schedulable)

    # Generate health summary
    health_summary = _determine_health_summary(is_ready, is_schedulable, issues)

    # Get node info
    node_info = status.get("nodeInfo", {})

    # Get capacity
    capacity = status.get("capacity", {})
    status.get("allocatable", {})

    # Note: pods_running will be populated by the caller if pod data is available
    # This is set to 0 here as a default; the main function can update it
    pods_running = 0

    return NodeConditionInfo(
        node_name=name,
        node_role=role,
        is_ready=is_ready,
        is_schedulable=is_schedulable,
        conditions=conditions,
        taints=taints,
        labels=labels if include_labels else {},
        health_summary=health_summary,
        issues=issues,
        kubelet_version=node_info.get("kubeletVersion", "unknown"),
        container_runtime=node_info.get("containerRuntimeVersion", "unknown"),
        os_image=node_info.get("osImage", "unknown"),
        kernel_version=node_info.get("kernelVersion", "unknown"),
        cpu_capacity=capacity.get("cpu", "0"),
        memory_capacity=capacity.get("memory", "0"),
        pods_capacity=int(capacity.get("pods", 110)),
        pods_running=pods_running,
    )


async def get_node_conditions(
    kubernetes_adapter: KubernetesAdapter,
    input_data: GetNodeConditionsInput,
) -> GetNodeConditionsOutput:
    """Get node conditions and readiness status.

    Retrieves node conditions, taints, and health status for
    all or specific nodes in the cluster.

    Args:
        kubernetes_adapter: Kubernetes client adapter.
        input_data: Filter parameters.

    Returns:
        Node conditions and health information.

    Raises:
        ResourceNotFoundError: If specified node not found.
        ToolExecutionError: If operation fails.
    """
    logger.info(
        "get_node_conditions_start",
        node_name=input_data.node_name,
        only_unhealthy=input_data.only_unhealthy,
    )

    try:
        # Get nodes (Node is a cluster-scoped resource, adapter handles this automatically)
        if input_data.node_name:
            # Get specific node
            try:
                node = await kubernetes_adapter.get(
                    kind="Node",
                    name=input_data.node_name,
                )
                raw_nodes = [node]
            except ResourceNotFoundError:
                logger.warning(
                    "node_not_found",
                    node_name=input_data.node_name,
                )
                raise
        else:
            # Get all nodes
            raw_nodes = await kubernetes_adapter.list(
                kind="Node",
            )

        # Get all pods to count running pods per node
        try:
            all_pods = await kubernetes_adapter.list(kind="Pod")
            # Build a mapping of node_name -> running pod count
            pods_per_node: dict[str, int] = {}
            for pod in all_pods:
                pod_spec = pod.get("spec", {})
                pod_status = pod.get("status", {})
                node_name = pod_spec.get("nodeName")
                phase = pod_status.get("phase", "")
                if node_name and phase == "Running":
                    pods_per_node[node_name] = pods_per_node.get(node_name, 0) + 1
        except Exception as e:
            logger.debug("pod_count_failed", error=str(e))
            pods_per_node = {}

        # Parse nodes
        nodes: list[NodeConditionInfo] = []
        for raw_node in raw_nodes:
            node_info = _parse_node(raw_node, input_data.include_labels)

            # Update pods_running with actual count
            node_info.pods_running = pods_per_node.get(node_info.node_name, 0)

            # Apply unhealthy filter
            if (
                input_data.only_unhealthy
                and node_info.is_ready
                and node_info.is_schedulable
                and not node_info.issues
            ):
                continue

            nodes.append(node_info)

        # Calculate statistics
        total_nodes = len(nodes)
        ready_nodes = sum(1 for n in nodes if n.is_ready)
        not_ready_nodes = total_nodes - ready_nodes
        cordoned_nodes = sum(1 for n in nodes if not n.is_schedulable)

        # Get nodes with issues
        nodes_with_issues = [n.node_name for n in nodes if n.issues]

        # Determine overall cluster health
        if not_ready_nodes == 0 and not nodes_with_issues:
            cluster_health = HealthStatus.HEALTHY
        elif not_ready_nodes > total_nodes // 2:
            cluster_health = HealthStatus.UNHEALTHY
        else:
            cluster_health = HealthStatus.DEGRADED

        # Generate recommendations
        recommendations: list[str] = []
        if not_ready_nodes > 0:
            recommendations.append(
                f"{not_ready_nodes} node(s) are not ready - investigate kubelet status"
            )
        if cordoned_nodes > 0:
            recommendations.append(
                f"{cordoned_nodes} node(s) are cordoned - check if maintenance is complete"
            )

        # Check for specific conditions
        memory_pressure = any(
            c.type == "MemoryPressure" and c.status == ConditionStatus.TRUE
            for n in nodes
            for c in n.conditions
        )
        disk_pressure = any(
            c.type == "DiskPressure" and c.status == ConditionStatus.TRUE
            for n in nodes
            for c in n.conditions
        )

        if memory_pressure:
            recommendations.append(
                "Some nodes have memory pressure - consider scaling or resource limits"
            )
        if disk_pressure:
            recommendations.append(
                "Some nodes have disk pressure - check for log rotation or expand storage"
            )

        result = GetNodeConditionsOutput(
            nodes=nodes,
            total_nodes=total_nodes,
            ready_nodes=ready_nodes,
            not_ready_nodes=not_ready_nodes,
            cordoned_nodes=cordoned_nodes,
            nodes_with_issues=nodes_with_issues,
            cluster_health=cluster_health,
            recommendations=recommendations,
            timestamp=datetime.now(UTC).isoformat(),
        )

        logger.info(
            "get_node_conditions_complete",
            total_nodes=total_nodes,
            ready_nodes=ready_nodes,
            cluster_health=cluster_health.value,
        )

        return result

    except ResourceNotFoundError:
        raise
    except Exception as e:
        logger.error(
            "get_node_conditions_error",
            error=str(e),
        )
        raise ToolExecutionError(
            f"Failed to get node conditions: {e}",
            tool_name="get_node_conditions",
        ) from e
