"""Get Kubernetes cluster health status tool.

This module provides the get_kubernetes_health MCP tool for retrieving
comprehensive Kubernetes cluster health information including node status,
system pod health, and API server status.

Safety Level: Read-only
"""

from __future__ import annotations


__all__ = [
    "GetKubernetesHealthInput",
    "GetKubernetesHealthOutput",
    "get_kubernetes_health",
]

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from mosk_mcp.observability.logging import get_logger
from mosk_mcp.tools.cluster_health.models import (
    GetKubernetesHealthInput,
    GetKubernetesHealthOutput,
    NodeHealthInfo,
    SystemPodHealth,
)
from mosk_mcp.tools.common import score_to_health
from mosk_mcp.tools.common.enums import HealthStatus
from mosk_mcp.tools.common.errors import tool_handler


if TYPE_CHECKING:
    from mosk_mcp.adapters.kubernetes import KubernetesAdapter


logger = get_logger(__name__)

# System namespaces to check for pod health
SYSTEM_NAMESPACES = [
    "kube-system",
    "kaas",
    "ceph",
    "openstack",
    "stacklight",
]

# Pod phases considered unhealthy
UNHEALTHY_PHASES = ["Failed", "Unknown"]

# Restart threshold to consider a pod unhealthy
RESTART_THRESHOLD = 5


def _calculate_kubernetes_score(
    total_nodes: int,
    ready_nodes: int,
    cordoned_nodes: int,
    api_server_healthy: bool,
    etcd_healthy: bool,
    system_pods_health: list[SystemPodHealth],
) -> int:
    """Calculate Kubernetes health score (0-100).

    Scoring breakdown:
    - Node readiness: 40 points
    - API server health: 20 points
    - etcd health: 15 points
    - System pod health: 25 points

    Args:
        total_nodes: Total number of nodes.
        ready_nodes: Number of ready nodes.
        cordoned_nodes: Number of cordoned nodes.
        api_server_healthy: Whether API server is healthy.
        etcd_healthy: Whether etcd is healthy.
        system_pods_health: System pod health information.

    Returns:
        Health score from 0-100.
    """
    score = 0

    # Node readiness (40 points)
    if total_nodes > 0:
        # Penalize not-ready and cordoned nodes (cordoned nodes count as half-ready)
        # Clamp effective_ready to [0, ready_nodes] to prevent negative values
        effective_ready = max(0.0, ready_nodes - (cordoned_nodes * 0.5))
        node_score = (effective_ready / total_nodes) * 40
        score += int(node_score)
    # else: No nodes is critical - score stays at 0

    # API server health (20 points)
    if api_server_healthy:
        score += 20

    # etcd health (15 points)
    if etcd_healthy:
        score += 15

    # System pod health (25 points)
    if system_pods_health:
        total_pods = sum(s.total_pods for s in system_pods_health)
        ready_pods = sum(s.ready_pods for s in system_pods_health)
        if total_pods > 0:
            pod_score = (ready_pods / total_pods) * 25
            score += int(pod_score)
        else:
            score += 25  # No system pods configured is OK
    else:
        score += 25

    return min(100, max(0, score))


def _parse_node_conditions(conditions: list[dict[str, Any]]) -> dict[str, bool]:
    """Parse node conditions into a simplified dict.

    Args:
        conditions: List of condition dicts from node status.

    Returns:
        Dict mapping condition type to boolean (True = healthy).
    """
    result = {}
    for cond in conditions:
        cond_type = cond.get("type", "")
        status = cond.get("status", "Unknown")

        if cond_type == "Ready":
            # Ready should be True for healthy
            result["Ready"] = status == "True"
        elif cond_type in ["MemoryPressure", "DiskPressure", "PIDPressure"]:
            # These should be False for healthy
            result[cond_type] = status != "True"

    return result


def _extract_node_health(node: dict[str, Any]) -> NodeHealthInfo:
    """Extract health information from a node resource.

    Args:
        node: Node resource dict.

    Returns:
        NodeHealthInfo object.
    """
    metadata = node.get("metadata", {})
    spec = node.get("spec", {})
    status = node.get("status", {})

    name = metadata.get("name", "unknown")
    labels = metadata.get("labels", {})

    # Determine role (check both legacy and current MOSK labels)
    role = "worker"
    if (
        "node-role.kubernetes.io/control-plane" in labels
        or "node-role.kubernetes.io/master" in labels
    ):
        role = "control-plane"
    elif (
        "openstack-control-plane" in labels
        or labels.get("hostlabel.bm.kaas.mirantis.com/controlplane") == "controlplane"
    ):
        role = "openstack-control"
    elif (
        "openstack-compute-node" in labels
        or labels.get("hostlabel.bm.kaas.mirantis.com/worker") == "worker"
    ):
        role = "compute"
    elif "openstack-gateway" in labels:
        role = "gateway"
    elif "ceph-osd-node" in labels:
        role = "storage"

    # Check if schedulable
    schedulable = not spec.get("unschedulable", False)

    # Parse conditions
    conditions = status.get("conditions", [])
    cond_status = _parse_node_conditions(conditions)

    ready = cond_status.get("Ready", False)
    memory_ok = cond_status.get("MemoryPressure", True)
    disk_ok = cond_status.get("DiskPressure", True)
    pid_ok = cond_status.get("PIDPressure", True)

    conditions_ok = ready and memory_ok and disk_ok and pid_ok

    # Collect issues
    issues: list[str] = []
    if not ready:
        issues.append("Node is not Ready")
    if not schedulable:
        issues.append("Node is cordoned (unschedulable)")
    if not memory_ok:
        issues.append("Memory pressure detected")
    if not disk_ok:
        issues.append("Disk pressure detected")
    if not pid_ok:
        issues.append("PID pressure detected")

    return NodeHealthInfo(
        name=name,
        ready=ready,
        schedulable=schedulable,
        role=role,
        conditions_ok=conditions_ok,
        issues=issues,
        cpu_pressure=False,  # Not a standard K8s condition
        memory_pressure=not memory_ok,
        disk_pressure=not disk_ok,
        pid_pressure=not pid_ok,
    )


def _analyze_system_pods(
    pods: list[dict[str, Any]],
    namespace: str,
) -> SystemPodHealth:
    """Analyze system pod health for a namespace.

    Args:
        pods: List of pod resources in the namespace.
        namespace: Namespace name.

    Returns:
        SystemPodHealth object.
    """
    total = len(pods)
    running = 0
    ready = 0
    failed = 0
    pending = 0
    unhealthy_names: list[str] = []

    for pod in pods:
        metadata = pod.get("metadata", {})
        status = pod.get("status", {})
        pod_name = metadata.get("name", "unknown")
        phase = status.get("phase", "Unknown")

        if phase == "Running":
            running += 1
            # Check container ready status
            container_statuses = status.get("containerStatuses", [])
            all_ready = (
                all(cs.get("ready", False) for cs in container_statuses)
                if container_statuses
                else False
            )

            if all_ready:
                ready += 1
            else:
                unhealthy_names.append(pod_name)

            # Check restart count
            total_restarts = sum(cs.get("restartCount", 0) for cs in container_statuses)
            if total_restarts >= RESTART_THRESHOLD and pod_name not in unhealthy_names:
                unhealthy_names.append(f"{pod_name} (high restarts: {total_restarts})")

        elif phase == "Pending":
            pending += 1
            unhealthy_names.append(f"{pod_name} (Pending)")
        elif phase in UNHEALTHY_PHASES:
            failed += 1
            unhealthy_names.append(f"{pod_name} ({phase})")
        elif phase == "Succeeded":
            # Completed jobs are OK
            ready += 1
            running += 1

    return SystemPodHealth(
        namespace=namespace,
        total_pods=total,
        running_pods=running,
        ready_pods=ready,
        failed_pods=failed,
        pending_pods=pending,
        unhealthy_pods=unhealthy_names[:10],  # Limit to first 10
    )


def _generate_recommendations(
    score: int,
    nodes: list[NodeHealthInfo],
    system_pods: list[SystemPodHealth],
    api_healthy: bool,
    etcd_healthy: bool,
) -> list[str]:
    """Generate recommendations based on health status.

    Args:
        score: Health score.
        nodes: Node health information.
        system_pods: System pod health.
        api_healthy: Whether API server is healthy.
        etcd_healthy: Whether etcd is healthy.

    Returns:
        List of recommendations.
    """
    recommendations: list[str] = []

    if not api_healthy:
        recommendations.append("API server is unhealthy - check kube-apiserver pods and logs")

    if not etcd_healthy:
        recommendations.append("etcd is unhealthy - check etcd pods and cluster membership")

    # Node recommendations
    not_ready = [n for n in nodes if not n.ready]
    if not_ready:
        recommendations.append(f"{len(not_ready)} node(s) not ready - investigate node conditions")

    cordoned = [n for n in nodes if not n.schedulable]
    if cordoned:
        recommendations.append(
            f"{len(cordoned)} node(s) cordoned - uncordon when maintenance complete"
        )

    pressure_nodes = [n for n in nodes if n.memory_pressure or n.disk_pressure]
    if pressure_nodes:
        recommendations.append(
            f"{len(pressure_nodes)} node(s) under resource pressure - check resource utilization"
        )

    # System pod recommendations
    for ns_health in system_pods:
        if ns_health.failed_pods > 0:
            recommendations.append(
                f"{ns_health.failed_pods} failed pod(s) in {ns_health.namespace} - "
                "investigate pod logs"
            )
        if ns_health.pending_pods > 0:
            recommendations.append(
                f"{ns_health.pending_pods} pending pod(s) in {ns_health.namespace} - "
                "check resource availability and scheduling"
            )

    return recommendations[:10]  # Limit recommendations


@tool_handler("get_kubernetes_health")
async def get_kubernetes_health(
    kubernetes_adapter: KubernetesAdapter,
    input_data: GetKubernetesHealthInput,
) -> GetKubernetesHealthOutput:
    """Get Kubernetes cluster health status.

    This tool retrieves comprehensive health information about the Kubernetes
    cluster including node status, system pod health, and API server status.

    Safety Level: Read-only

    Args:
        kubernetes_adapter: Kubernetes adapter for cluster communication.
        input_data: Input parameters for the query.

    Returns:
        GetKubernetesHealthOutput with cluster health information.

    Raises:
        ToolExecutionError: If health check fails.

    Example:
        >>> health = await get_kubernetes_health(k8s_adapter, GetKubernetesHealthInput())
        >>> print(f"Health: {health.health}")
        >>> print(f"Ready nodes: {health.ready_nodes}/{health.total_nodes}")
    """
    logger.info(
        "getting_kubernetes_health",
        include_node_details=input_data.include_node_details,
        include_system_pods=input_data.include_system_pods,
    )

    timestamp = datetime.now(UTC).isoformat()
    issues: list[str] = []

    # Get server version
    try:
        server_version = await kubernetes_adapter.get_server_version()
    except Exception as e:
        logger.warning("failed_to_get_server_version", error=str(e))
        server_version = "unknown"
        issues.append("Could not retrieve server version")

    # Check API server health
    api_server_healthy = True
    try:
        await kubernetes_adapter.check_api_health()
    except Exception as e:
        logger.warning("api_server_unhealthy", error=str(e))
        api_server_healthy = False
        issues.append(f"API server health check failed: {e}")

    # Check etcd health (via kube-system pods)
    etcd_healthy = True
    try:
        etcd_pods = await kubernetes_adapter.list_pods(
            namespace="kube-system",
            label_selector="component=etcd",
        )
        if etcd_pods:
            unhealthy_etcd = [p for p in etcd_pods if p.get("status", {}).get("phase") != "Running"]
            if unhealthy_etcd:
                etcd_healthy = False
                issues.append(f"{len(unhealthy_etcd)} etcd pod(s) unhealthy")
    except Exception as e:
        logger.warning("failed_to_check_etcd", error=str(e))
        etcd_healthy = False
        issues.append(f"Could not check etcd health: {e}")

    # Get node status
    nodes_data = await kubernetes_adapter.list_nodes()
    nodes_health: list[NodeHealthInfo] = []

    total_nodes = len(nodes_data)
    ready_nodes = 0
    not_ready_nodes = 0
    cordoned_nodes = 0

    for node in nodes_data:
        node_health = _extract_node_health(node)
        nodes_health.append(node_health)

        if node_health.ready:
            ready_nodes += 1
        else:
            not_ready_nodes += 1
            issues.append(f"Node {node_health.name} is not ready")

        if not node_health.schedulable:
            cordoned_nodes += 1

    # Get system pod health
    system_pods_health: list[SystemPodHealth] = []
    if input_data.include_system_pods:
        for namespace in SYSTEM_NAMESPACES:
            try:
                pods = await kubernetes_adapter.list_pods(namespace=namespace)
                if pods:
                    ns_health = _analyze_system_pods(pods, namespace)
                    system_pods_health.append(ns_health)

                    if ns_health.failed_pods > 0:
                        issues.append(f"{ns_health.failed_pods} failed pods in {namespace}")
            except Exception as e:
                logger.warning(
                    "failed_to_list_pods",
                    namespace=namespace,
                    error=str(e),
                )

    # Calculate score
    score = _calculate_kubernetes_score(
        total_nodes=total_nodes,
        ready_nodes=ready_nodes,
        cordoned_nodes=cordoned_nodes,
        api_server_healthy=api_server_healthy,
        etcd_healthy=etcd_healthy,
        system_pods_health=system_pods_health,
    )

    health = score_to_health(score)

    # Generate message
    if health == HealthStatus.HEALTHY:
        message = f"Kubernetes cluster healthy: {ready_nodes}/{total_nodes} nodes ready"
    elif health == HealthStatus.DEGRADED:
        message = f"Kubernetes cluster degraded: {not_ready_nodes} node(s) not ready"
    else:
        message = f"Kubernetes cluster unhealthy: {len(issues)} issue(s) detected"

    # Generate recommendations
    recommendations = _generate_recommendations(
        score=score,
        nodes=nodes_health,
        system_pods=system_pods_health,
        api_healthy=api_server_healthy,
        etcd_healthy=etcd_healthy,
    )

    output = GetKubernetesHealthOutput(
        health=health,
        score=score,
        message=message,
        server_version=server_version,
        api_server_healthy=api_server_healthy,
        etcd_healthy=etcd_healthy,
        total_nodes=total_nodes,
        ready_nodes=ready_nodes,
        not_ready_nodes=not_ready_nodes,
        cordoned_nodes=cordoned_nodes,
        nodes=nodes_health if input_data.include_node_details else [],
        system_pods=system_pods_health if input_data.include_system_pods else [],
        issues=issues,
        recommendations=recommendations,
        timestamp=timestamp,
    )

    logger.info(
        "kubernetes_health_retrieved",
        health=health.value,
        score=score,
        ready_nodes=ready_nodes,
        total_nodes=total_nodes,
    )

    return output
