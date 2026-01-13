"""Get RabbitMQ cluster status and health tool.

This module provides the get_rabbitmq_status MCP tool for retrieving
comprehensive RabbitMQ cluster health and operational status.

Safety Level: Read-only
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from mosk_mcp.core.exceptions import ToolExecutionError
from mosk_mcp.observability.logging import get_logger
from mosk_mcp.tools.messaging_operations.models import (
    GetRabbitMQStatusOutput,
    RabbitMQHealthLevel,
    RabbitMQNodeInfo,
)
from mosk_mcp.tools.messaging_operations.rabbitmq_client import RabbitMQClient


if TYPE_CHECKING:
    from mosk_mcp.adapters.kubernetes import KubernetesAdapter


logger = get_logger(__name__)


def _determine_health_level(
    running_nodes: int,
    total_nodes: int,
    has_alarms: bool,
    has_partitions: bool,
    memory_percent: float,
) -> RabbitMQHealthLevel:
    """Determine overall health level from cluster metrics.

    Args:
        running_nodes: Number of running nodes.
        total_nodes: Total number of nodes.
        has_alarms: Whether any alarms are active.
        has_partitions: Whether network partitions exist.
        memory_percent: Memory usage percentage.

    Returns:
        RabbitMQHealthLevel enum value.
    """
    # Critical conditions
    if running_nodes == 0:
        return RabbitMQHealthLevel.CRITICAL
    if has_partitions:
        return RabbitMQHealthLevel.CRITICAL
    if running_nodes < total_nodes:
        return RabbitMQHealthLevel.CRITICAL

    # Warning conditions
    if has_alarms:
        return RabbitMQHealthLevel.WARNING
    if memory_percent > 80:
        return RabbitMQHealthLevel.WARNING

    return RabbitMQHealthLevel.HEALTHY


def _generate_health_summary(
    health: RabbitMQHealthLevel,
    running_nodes: int,
    total_nodes: int,
    vhost_count: int,
    has_alarms: bool,
    has_partitions: bool,
    memory_percent: float,
) -> str:
    """Generate a human-readable health summary.

    Args:
        health: Current health level.
        running_nodes: Number of running nodes.
        total_nodes: Total number of nodes.
        vhost_count: Number of virtual hosts.
        has_alarms: Whether alarms are active.
        has_partitions: Whether partitions exist.
        memory_percent: Memory usage percentage.

    Returns:
        Human-readable summary string.
    """
    parts = []

    # Health status
    if health == RabbitMQHealthLevel.HEALTHY:
        parts.append("Cluster is healthy")
    elif health == RabbitMQHealthLevel.WARNING:
        parts.append("Cluster has warnings")
    elif health == RabbitMQHealthLevel.CRITICAL:
        parts.append("Cluster has critical issues")
    else:
        parts.append("Cluster health unknown")

    # Node status
    if running_nodes == total_nodes:
        parts.append(f"all {total_nodes} node(s) running")
    else:
        parts.append(f"{running_nodes}/{total_nodes} nodes running")

    # Vhost count
    parts.append(f"{vhost_count} vhost(s)")

    # Memory usage
    parts.append(f"{memory_percent:.1f}% memory used")

    # Alerts
    if has_alarms:
        parts.append("ALARMS ACTIVE")
    if has_partitions:
        parts.append("NETWORK PARTITIONS DETECTED")

    return "; ".join(parts) + "."


def _generate_issues(
    has_alarms: bool,
    alarms: list[str],
    has_partitions: bool,
    partitions: list[str],
    running_nodes: int,
    total_nodes: int,
    memory_percent: float,
) -> list[str]:
    """Generate list of current issues.

    Args:
        has_alarms: Whether alarms are active.
        alarms: List of active alarms.
        has_partitions: Whether partitions exist.
        partitions: List of partitions.
        running_nodes: Number of running nodes.
        total_nodes: Total number of nodes.
        memory_percent: Memory usage percentage.

    Returns:
        List of issue strings.
    """
    issues = []

    if running_nodes < total_nodes:
        issues.append(f"{total_nodes - running_nodes} node(s) not running")

    if has_partitions:
        issues.append(f"Network partitions detected: {', '.join(partitions)}")

    if has_alarms:
        for alarm in alarms:
            issues.append(f"Alarm active: {alarm}")

    if memory_percent > 90:
        issues.append(f"Critical memory usage: {memory_percent:.1f}%")
    elif memory_percent > 80:
        issues.append(f"High memory usage: {memory_percent:.1f}%")

    return issues


def _generate_warnings(
    memory_percent: float,
    maintenance_status: str,
) -> list[str]:
    """Generate list of warnings.

    Args:
        memory_percent: Memory usage percentage.
        maintenance_status: Maintenance status string.

    Returns:
        List of warning strings.
    """
    warnings = []

    if 70 < memory_percent <= 80:
        warnings.append(f"Memory usage is elevated: {memory_percent:.1f}%")

    if "under maintenance" in maintenance_status:
        warnings.append("Node is under maintenance")

    return warnings


def _generate_recommendations(
    health: RabbitMQHealthLevel,
    issues: list[str],
    memory_percent: float,
    has_alarms: bool,
) -> list[str]:
    """Generate actionable recommendations.

    Args:
        health: Current health level.
        issues: List of current issues.
        memory_percent: Memory usage percentage.
        has_alarms: Whether alarms are active.

    Returns:
        List of recommendation strings.
    """
    recommendations = []

    if health == RabbitMQHealthLevel.CRITICAL:
        recommendations.append("IMMEDIATE ACTION REQUIRED: Check RabbitMQ cluster status")

    if has_alarms:
        recommendations.append("Investigate active alarms: run 'rabbitmqctl status' for details")

    if memory_percent > 80:
        recommendations.append("Check for message backlogs using list_rabbitmq_queues tool")
        recommendations.append("Consider purging stale queues or scaling RabbitMQ")

    if memory_percent > 60:
        recommendations.append("Monitor queue depths and consumer health")

    if not issues and health == RabbitMQHealthLevel.HEALTHY:
        recommendations.append("No action required - cluster is operating normally")

    return recommendations


def _is_safe_for_operations(
    health: RabbitMQHealthLevel,
    has_alarms: bool,
    has_partitions: bool,
    running_nodes: int,
    total_nodes: int,
) -> bool:
    """Determine if cluster is safe for maintenance operations.

    Args:
        health: Current health level.
        has_alarms: Whether alarms are active.
        has_partitions: Whether partitions exist.
        running_nodes: Number of running nodes.
        total_nodes: Total number of nodes.

    Returns:
        True if safe for operations.
    """
    return (
        health != RabbitMQHealthLevel.CRITICAL
        and not has_partitions
        and not has_alarms
        and running_nodes >= total_nodes
    )


async def get_rabbitmq_status(
    kubernetes_adapter: KubernetesAdapter,
    rabbitmq_instance: Literal["main", "neutron"] = "main",
    include_feature_flags: bool = False,
) -> GetRabbitMQStatusOutput:
    """Get RabbitMQ cluster status and health.

    This tool retrieves comprehensive information about the RabbitMQ cluster
    including health status, node state, alarms, and virtual hosts.

    Safety Level: Read-only

    Args:
        kubernetes_adapter: Kubernetes adapter for cluster communication.
        rabbitmq_instance: RabbitMQ instance to query ('main' or 'neutron').
        include_feature_flags: Include enabled feature flags in output.

    Returns:
        GetRabbitMQStatusOutput with cluster status information.

    Raises:
        ToolExecutionError: If status cannot be retrieved.

    Example:
        >>> status = await get_rabbitmq_status(k8s_adapter)
        >>> print(f"Health: {status.health}")
        >>> print(f"Vhosts: {status.vhosts}")
    """
    logger.info(
        "getting_rabbitmq_status",
        instance=rabbitmq_instance,
        include_feature_flags=include_feature_flags,
    )

    try:
        async with RabbitMQClient(kubernetes_adapter, instance=rabbitmq_instance) as client:
            # Get cluster status
            cluster_status = await client.get_cluster_status()

            # Get node status for memory/disk info
            node_status = await client.get_node_status()

            # Get virtual hosts
            vhosts = await client.list_vhosts()

            # Build node info list
            nodes = []
            for node_name in cluster_status.running_nodes:
                node_info = RabbitMQNodeInfo(
                    name=node_name,
                    running=True,
                    memory_used_bytes=node_status.memory_used_bytes,
                    memory_limit_bytes=node_status.memory_limit_bytes,
                    memory_percent=node_status.memory_percent,
                    cpu_cores=cluster_status.cpu_cores,
                    erlang_version=cluster_status.erlang_version,
                    rabbitmq_version=cluster_status.rabbitmq_version,
                )
                nodes.append(node_info)

            running_nodes = len(cluster_status.running_nodes)
            total_nodes = max(len(cluster_status.disk_nodes), running_nodes)
            has_alarms = len(cluster_status.alarms) > 0
            has_partitions = len(cluster_status.partitions) > 0

            # Determine health
            health = _determine_health_level(
                running_nodes=running_nodes,
                total_nodes=total_nodes,
                has_alarms=has_alarms,
                has_partitions=has_partitions,
                memory_percent=node_status.memory_percent,
            )

            # Generate summaries
            health_summary = _generate_health_summary(
                health=health,
                running_nodes=running_nodes,
                total_nodes=total_nodes,
                vhost_count=len(vhosts),
                has_alarms=has_alarms,
                has_partitions=has_partitions,
                memory_percent=node_status.memory_percent,
            )

            issues = _generate_issues(
                has_alarms=has_alarms,
                alarms=cluster_status.alarms,
                has_partitions=has_partitions,
                partitions=cluster_status.partitions,
                running_nodes=running_nodes,
                total_nodes=total_nodes,
                memory_percent=node_status.memory_percent,
            )

            warnings = _generate_warnings(
                memory_percent=node_status.memory_percent,
                maintenance_status=cluster_status.maintenance_status,
            )

            recommendations = _generate_recommendations(
                health=health,
                issues=issues,
                memory_percent=node_status.memory_percent,
                has_alarms=has_alarms,
            )

            is_safe = _is_safe_for_operations(
                health=health,
                has_alarms=has_alarms,
                has_partitions=has_partitions,
                running_nodes=running_nodes,
                total_nodes=total_nodes,
            )

            output = GetRabbitMQStatusOutput(
                instance=rabbitmq_instance,
                cluster_name=cluster_status.cluster_name,
                health=health,
                health_summary=health_summary,
                nodes=nodes,
                running_nodes=running_nodes,
                total_nodes=total_nodes,
                alarms=cluster_status.alarms,
                has_alarms=has_alarms,
                partitions=cluster_status.partitions,
                has_partitions=has_partitions,
                maintenance_status=cluster_status.maintenance_status,
                vhosts=vhosts,
                vhost_count=len(vhosts),
                feature_flags=cluster_status.feature_flags if include_feature_flags else {},
                listeners=cluster_status.listeners,
                is_healthy=health == RabbitMQHealthLevel.HEALTHY,
                is_safe_for_operations=is_safe,
                issues=issues,
                warnings=warnings,
                recommendations=recommendations,
            )

            logger.info(
                "rabbitmq_status_retrieved",
                instance=rabbitmq_instance,
                health=output.health.value,
                is_healthy=output.is_healthy,
                vhost_count=output.vhost_count,
            )

            return output

    except ToolExecutionError:
        raise
    except Exception as e:
        logger.error("get_rabbitmq_status_failed", error=str(e), instance=rabbitmq_instance)
        raise ToolExecutionError(
            message=f"Failed to get RabbitMQ status: {e}",
            tool_name="get_rabbitmq_status",
            details={"error": str(e), "instance": rabbitmq_instance},
        ) from e
