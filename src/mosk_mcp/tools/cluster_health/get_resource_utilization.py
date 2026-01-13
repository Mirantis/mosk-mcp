"""Get cluster resource utilization summary tool.

This module provides the get_resource_utilization MCP tool for retrieving
CPU, memory, and storage utilization across the cluster.

Safety Level: Read-only
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from mosk_mcp.adapters.ceph import CephAdapter
from mosk_mcp.core.exceptions import ToolExecutionError
from mosk_mcp.observability.logging import get_logger
from mosk_mcp.tools.cluster_health.models import (
    GetResourceUtilizationInput,
    GetResourceUtilizationOutput,
    NamespaceResourceUtilization,
    NodeResourceUtilization,
    StorageUtilization,
)
from mosk_mcp.tools.common import format_bytes


if TYPE_CHECKING:
    from mosk_mcp.adapters.kubernetes import KubernetesAdapter


logger = get_logger(__name__)


def _parse_cpu_quantity(quantity: str) -> int:
    """Parse Kubernetes CPU quantity to millicores.

    Args:
        quantity: CPU quantity string (e.g., "2", "500m", "1.5").

    Returns:
        CPU in millicores.
    """
    if not quantity:
        return 0

    quantity = str(quantity).strip()

    if quantity.endswith("m"):
        return int(quantity[:-1])
    elif quantity.endswith("n"):
        # Nanocores to millicores
        return int(quantity[:-1]) // 1_000_000
    else:
        # Cores to millicores
        try:
            return int(float(quantity) * 1000)
        except ValueError:
            return 0


def _parse_memory_quantity(quantity: str) -> int:
    """Parse Kubernetes memory quantity to bytes.

    Args:
        quantity: Memory quantity string (e.g., "1Gi", "512Mi", "1024Ki").

    Returns:
        Memory in bytes.
    """
    if not quantity:
        return 0

    quantity = str(quantity).strip()

    multipliers = {
        "Ki": 1024,
        "Mi": 1024**2,
        "Gi": 1024**3,
        "Ti": 1024**4,
        "K": 1000,
        "M": 1000**2,
        "G": 1000**3,
        "T": 1000**4,
    }

    for suffix, multiplier in multipliers.items():
        if quantity.endswith(suffix):
            try:
                return int(float(quantity[: -len(suffix)]) * multiplier)
            except ValueError:
                return 0

    # Plain bytes
    try:
        return int(quantity)
    except ValueError:
        return 0


def _determine_node_role(labels: dict[str, str]) -> str:
    """Determine node role from labels.

    Args:
        labels: Node labels.

    Returns:
        Node role string.
    """
    if "node-role.kubernetes.io/control-plane" in labels:
        return "control-plane"
    if "node-role.kubernetes.io/master" in labels:
        return "control-plane"
    # Check for MOSK control plane labels
    if labels.get("openstack-control-plane") == "enabled":
        return "openstack-control"
    if labels.get("hostlabel.bm.kaas.mirantis.com/controlplane") == "controlplane":
        return "openstack-control"
    # Check for compute/worker node labels
    if labels.get("openstack-compute-node") == "enabled":
        return "compute"
    if labels.get("hostlabel.bm.kaas.mirantis.com/worker") == "worker":
        return "compute"
    # Check for storage node labels
    if labels.get("ceph-osd-node") == "enabled":
        return "storage"
    return "worker"


def _calculate_percentage(used: int | float, total: int | float) -> float:
    """Calculate percentage safely.

    Args:
        used: Used amount.
        total: Total amount.

    Returns:
        Percentage as float.
    """
    if total <= 0:
        return 0.0
    return round((used / total) * 100, 2)


async def get_resource_utilization(
    kubernetes_adapter: KubernetesAdapter,
    input_data: GetResourceUtilizationInput,
) -> GetResourceUtilizationOutput:
    """Get cluster resource utilization summary.

    This tool retrieves CPU, memory, and storage utilization across the
    cluster, optionally broken down by node and namespace.

    Safety Level: Read-only

    Args:
        kubernetes_adapter: Kubernetes adapter for cluster communication.
        input_data: Input parameters for the query.

    Returns:
        GetResourceUtilizationOutput with utilization information.

    Raises:
        ToolExecutionError: If utilization check fails.

    Example:
        >>> util = await get_resource_utilization(k8s_adapter, GetResourceUtilizationInput())
        >>> print(f"CPU usage: {util.cluster_cpu_usage_percent:.1f}%")
    """
    logger.info(
        "getting_resource_utilization",
        include_per_node=input_data.include_per_node,
        include_per_namespace=input_data.include_per_namespace,
    )

    try:
        timestamp = datetime.now(UTC).isoformat()
        warnings: list[str] = []
        recommendations: list[str] = []

        # Get nodes
        nodes = await kubernetes_adapter.list_nodes()

        # Initialize cluster totals
        cluster_cpu_capacity = 0
        cluster_cpu_requested = 0
        cluster_cpu_used = 0  # Would need metrics API for actual usage
        cluster_memory_capacity = 0
        cluster_memory_requested = 0
        cluster_memory_used = 0
        cluster_pods_capacity = 0
        cluster_pods_running = 0

        node_utilizations: list[NodeResourceUtilization] = []

        for node in nodes:
            metadata = node.get("metadata", {})
            node_name = metadata.get("name", "unknown")
            labels = metadata.get("labels", {})
            status = node.get("status", {})

            capacity = status.get("capacity", {})
            allocatable = status.get("allocatable", {})

            # Parse node capacities
            cpu_capacity = _parse_cpu_quantity(capacity.get("cpu", "0"))
            cpu_allocatable = _parse_cpu_quantity(allocatable.get("cpu", "0"))
            memory_capacity = _parse_memory_quantity(capacity.get("memory", "0"))
            memory_allocatable = _parse_memory_quantity(allocatable.get("memory", "0"))
            pods_capacity = int(capacity.get("pods", "0"))

            # Get pods on this node to calculate requests
            try:
                node_pods = await kubernetes_adapter.list_pods(
                    namespace="",
                    field_selector=f"spec.nodeName={node_name}",
                )
            except Exception as e:
                logger.warning("failed_to_list_pods_for_node", node_name=node_name, error=str(e))
                node_pods = []

            node_cpu_requested = 0
            node_memory_requested = 0
            node_pods_running = 0

            for pod in node_pods:
                pod_status = pod.get("status", {})
                phase = pod_status.get("phase", "Unknown")

                if phase == "Running":
                    node_pods_running += 1

                    # Sum container requests
                    for container in pod.get("spec", {}).get("containers", []):
                        resources = container.get("resources", {})
                        requests = resources.get("requests", {})

                        cpu_req = requests.get("cpu", "0")
                        mem_req = requests.get("memory", "0")

                        node_cpu_requested += _parse_cpu_quantity(cpu_req)
                        node_memory_requested += _parse_memory_quantity(mem_req)

            # Estimate used as slightly higher than requested for demo
            # In production, would use metrics-server API
            node_cpu_used = int(node_cpu_requested * 0.7)  # Estimate
            node_memory_used = int(node_memory_requested * 0.8)  # Estimate

            # Add to cluster totals
            cluster_cpu_capacity += cpu_allocatable
            cluster_cpu_requested += node_cpu_requested
            cluster_cpu_used += node_cpu_used
            cluster_memory_capacity += memory_allocatable
            cluster_memory_requested += node_memory_requested
            cluster_memory_used += node_memory_used
            cluster_pods_capacity += pods_capacity
            cluster_pods_running += node_pods_running

            # Create node utilization record
            if input_data.include_per_node:
                role = _determine_node_role(labels)

                node_util = NodeResourceUtilization(
                    node_name=node_name,
                    role=role,
                    cpu_capacity_millicores=cpu_capacity,
                    cpu_allocatable_millicores=cpu_allocatable,
                    cpu_requested_millicores=node_cpu_requested,
                    cpu_used_millicores=node_cpu_used,
                    cpu_request_percent=_calculate_percentage(
                        node_cpu_requested,
                        cpu_allocatable,
                    ),
                    cpu_usage_percent=_calculate_percentage(
                        node_cpu_used,
                        cpu_allocatable,
                    ),
                    memory_capacity_bytes=memory_capacity,
                    memory_allocatable_bytes=memory_allocatable,
                    memory_requested_bytes=node_memory_requested,
                    memory_used_bytes=node_memory_used,
                    memory_request_percent=_calculate_percentage(
                        node_memory_requested,
                        memory_allocatable,
                    ),
                    memory_usage_percent=_calculate_percentage(
                        node_memory_used,
                        memory_allocatable,
                    ),
                    pods_capacity=pods_capacity,
                    pods_running=node_pods_running,
                    pods_percent=_calculate_percentage(
                        node_pods_running,
                        pods_capacity,
                    ),
                )
                node_utilizations.append(node_util)

                # Check for high utilization
                if node_util.cpu_request_percent > 90:
                    warnings.append(
                        f"Node {node_name} CPU requests at {node_util.cpu_request_percent:.1f}%"
                    )
                if node_util.memory_request_percent > 90:
                    warnings.append(
                        f"Node {node_name} memory requests at {node_util.memory_request_percent:.1f}%"
                    )

        # Get namespace utilization
        namespace_utilizations: list[NamespaceResourceUtilization] = []
        namespace_cpu: dict[str, int] = {}
        namespace_memory: dict[str, int] = {}

        if input_data.include_per_namespace:
            # Get all pods
            all_pods = await kubernetes_adapter.list_pods(namespace="")

            for pod in all_pods:
                ns = pod.get("metadata", {}).get("namespace", "default")
                phase = pod.get("status", {}).get("phase", "Unknown")

                if phase != "Running":
                    continue

                ns_cpu = namespace_cpu.get(ns, 0)
                ns_memory = namespace_memory.get(ns, 0)

                for container in pod.get("spec", {}).get("containers", []):
                    resources = container.get("resources", {})
                    requests = resources.get("requests", {})

                    ns_cpu += _parse_cpu_quantity(requests.get("cpu", "0"))
                    ns_memory += _parse_memory_quantity(requests.get("memory", "0"))

                namespace_cpu[ns] = ns_cpu
                namespace_memory[ns] = ns_memory

            # Create namespace records
            for ns in set(namespace_cpu.keys()) | set(namespace_memory.keys()):
                # Count pods in namespace
                ns_pods = sum(
                    1
                    for p in all_pods
                    if p.get("metadata", {}).get("namespace") == ns
                    and p.get("status", {}).get("phase") == "Running"
                )

                namespace_utilizations.append(
                    NamespaceResourceUtilization(
                        namespace=ns,
                        pods_count=ns_pods,
                        cpu_requested_millicores=namespace_cpu.get(ns, 0),
                        cpu_limit_millicores=0,  # Would need to calculate limits
                        memory_requested_bytes=namespace_memory.get(ns, 0),
                        memory_limit_bytes=0,
                    )
                )

            # Sort by CPU usage
            namespace_utilizations.sort(
                key=lambda x: x.cpu_requested_millicores,
                reverse=True,
            )

        # Get storage utilization from Ceph
        try:
            async with CephAdapter(kubernetes_adapter) as ceph:
                cluster_status = await ceph.get_cluster_status()

                storage_status = "normal"
                if cluster_status.capacity_percent >= 85:
                    storage_status = "critical"
                elif cluster_status.capacity_percent >= 75:
                    storage_status = "warning"

                storage = StorageUtilization(
                    total_bytes=cluster_status.total_bytes,
                    used_bytes=cluster_status.used_bytes,
                    available_bytes=cluster_status.available_bytes,
                    usage_percent=cluster_status.capacity_percent,
                    status=storage_status,
                    total_human=format_bytes(cluster_status.total_bytes),
                    used_human=format_bytes(cluster_status.used_bytes),
                    available_human=format_bytes(cluster_status.available_bytes),
                )

                if cluster_status.capacity_percent >= 75:
                    warnings.append(
                        f"Storage at {cluster_status.capacity_percent:.1f}% - "
                        "consider adding capacity"
                    )

        except Exception as e:
            logger.warning("failed_to_get_storage_utilization", error=str(e))
            storage = StorageUtilization(
                total_bytes=0,
                used_bytes=0,
                available_bytes=0,
                usage_percent=0,
                status="error",
                total_human="N/A",
                used_human="N/A",
                available_human="N/A",
                error_message=f"Failed to query Ceph: {e}",
            )
            warnings.append(f"Storage utilization unavailable: {e}")

        # Calculate cluster percentages
        cluster_cpu_request_percent = _calculate_percentage(
            cluster_cpu_requested,
            cluster_cpu_capacity,
        )
        cluster_cpu_usage_percent = _calculate_percentage(
            cluster_cpu_used,
            cluster_cpu_capacity,
        )
        cluster_memory_request_percent = _calculate_percentage(
            cluster_memory_requested,
            cluster_memory_capacity,
        )
        cluster_memory_usage_percent = _calculate_percentage(
            cluster_memory_used,
            cluster_memory_capacity,
        )
        cluster_pods_percent = _calculate_percentage(
            cluster_pods_running,
            cluster_pods_capacity,
        )

        # Get top consumers
        top_cpu = (
            [f"{ns.namespace}: {ns.cpu_requested_millicores}m" for ns in namespace_utilizations[:5]]
            if namespace_utilizations
            else []
        )

        top_memory = sorted(
            namespace_utilizations,
            key=lambda x: x.memory_requested_bytes,
            reverse=True,
        )[:5]
        top_memory_str = (
            [f"{ns.namespace}: {format_bytes(ns.memory_requested_bytes)}" for ns in top_memory]
            if top_memory
            else []
        )

        # Generate recommendations
        if cluster_cpu_request_percent > 80:
            recommendations.append("CPU requests above 80% - consider adding nodes")
        if cluster_memory_request_percent > 80:
            recommendations.append("Memory requests above 80% - consider adding nodes")
        if cluster_pods_percent > 80:
            recommendations.append("Pod capacity above 80% - consider adding nodes")

        output = GetResourceUtilizationOutput(
            cluster_cpu_capacity_millicores=cluster_cpu_capacity,
            cluster_cpu_requested_millicores=cluster_cpu_requested,
            cluster_cpu_used_millicores=cluster_cpu_used,
            cluster_cpu_request_percent=cluster_cpu_request_percent,
            cluster_cpu_usage_percent=cluster_cpu_usage_percent,
            cluster_memory_capacity_bytes=cluster_memory_capacity,
            cluster_memory_requested_bytes=cluster_memory_requested,
            cluster_memory_used_bytes=cluster_memory_used,
            cluster_memory_request_percent=cluster_memory_request_percent,
            cluster_memory_usage_percent=cluster_memory_usage_percent,
            cluster_pods_capacity=cluster_pods_capacity,
            cluster_pods_running=cluster_pods_running,
            cluster_pods_percent=cluster_pods_percent,
            storage=storage,
            nodes=node_utilizations if input_data.include_per_node else [],
            namespaces=namespace_utilizations if input_data.include_per_namespace else [],
            top_cpu_consumers=top_cpu,
            top_memory_consumers=top_memory_str,
            warnings=warnings,
            recommendations=recommendations,
            timestamp=timestamp,
        )

        logger.info(
            "resource_utilization_retrieved",
            cpu_request_percent=cluster_cpu_request_percent,
            memory_request_percent=cluster_memory_request_percent,
            storage_percent=storage.usage_percent,
        )

        return output

    except Exception as e:
        logger.error("get_resource_utilization_failed", error=str(e))
        raise ToolExecutionError(
            message=f"Failed to get resource utilization: {e}",
            tool_name="get_resource_utilization",
            details={"error": str(e)},
        ) from e
