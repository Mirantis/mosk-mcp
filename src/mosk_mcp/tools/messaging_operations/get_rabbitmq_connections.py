"""Get RabbitMQ connections with analysis tool.

This module provides the get_rabbitmq_connections MCP tool for retrieving
connection pool information and health analysis.

Safety Level: Read-only
"""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING, Literal

from mosk_mcp.core.exceptions import ToolExecutionError
from mosk_mcp.observability.logging import get_logger
from mosk_mcp.tools.messaging_operations.models import (
    ConnectionsByUserSummary,
    ConnectionState,
    GetRabbitMQConnectionsOutput,
    RabbitMQConnectionInfo,
)
from mosk_mcp.tools.messaging_operations.rabbitmq_client import RabbitMQClient


if TYPE_CHECKING:
    from mosk_mcp.adapters.kubernetes import KubernetesAdapter


logger = get_logger(__name__)


# Default connection limit if not detected
DEFAULT_CONNECTION_LIMIT = 65536

# Warning threshold for connection utilization
CONNECTION_UTILIZATION_WARNING = 70.0

# Critical threshold for connection utilization
CONNECTION_UTILIZATION_CRITICAL = 90.0


_STATE_MAP: dict[str, ConnectionState] = {
    "running": ConnectionState.RUNNING,
    "blocked": ConnectionState.BLOCKED,
    "blocking": ConnectionState.BLOCKING,
    "closed": ConnectionState.CLOSED,
}


def _map_connection_state(state_str: str) -> ConnectionState:
    """Map connection state string to enum.

    Args:
        state_str: State string from rabbitmqctl.

    Returns:
        ConnectionState enum value.
    """
    return _STATE_MAP.get(state_str.lower(), ConnectionState.UNKNOWN)


def _generate_recommendations(
    total_connections: int,
    blocked_connections: int,
    utilization_percent: float | None,
    top_users: list[str],
) -> list[str]:
    """Generate recommendations based on connection analysis.

    Args:
        total_connections: Total number of connections.
        blocked_connections: Number of blocked connections.
        utilization_percent: Connection pool utilization percentage.
        top_users: Top users by connection count.

    Returns:
        List of recommendation strings.
    """
    recommendations = []

    if blocked_connections > 0:
        recommendations.append(
            f"WARNING: {blocked_connections} connection(s) are blocked. "
            "This typically indicates memory or disk pressure on RabbitMQ. "
            "Check for memory/disk alarms using get_rabbitmq_status."
        )

    if utilization_percent is not None:
        if utilization_percent >= CONNECTION_UTILIZATION_CRITICAL:
            recommendations.append(
                f"CRITICAL: Connection pool at {utilization_percent:.1f}% utilization. "
                "Risk of connection exhaustion. Consider scaling RabbitMQ or "
                "investigating connection leaks."
            )
        elif utilization_percent >= CONNECTION_UTILIZATION_WARNING:
            recommendations.append(
                f"WARNING: Connection pool at {utilization_percent:.1f}% utilization. "
                "Monitor for connection exhaustion."
            )

    if total_connections > 1000:
        recommendations.append(
            f"High connection count ({total_connections}). "
            "Consider reviewing connection pooling settings in OpenStack services."
        )

    if top_users and len(top_users) > 0:
        # Check for concentration
        recommendations.append(f"Top connection consumers: {', '.join(top_users[:3])}")

    if blocked_connections == 0 and (
        utilization_percent is None or utilization_percent < CONNECTION_UTILIZATION_WARNING
    ):
        recommendations.append("Connection pool is healthy - no action required.")

    return recommendations


async def get_rabbitmq_connections(
    kubernetes_adapter: KubernetesAdapter,
    rabbitmq_instance: Literal["main", "neutron"] = "main",
    include_channels: bool = False,
    group_by_user: bool = True,
    limit: int = 200,
) -> GetRabbitMQConnectionsOutput:
    """Get RabbitMQ connections with analysis.

    This tool retrieves connection pool information from RabbitMQ,
    grouped by user/service, and provides health analysis.

    Safety Level: Read-only

    Args:
        kubernetes_adapter: Kubernetes adapter for cluster communication.
        rabbitmq_instance: RabbitMQ instance to query ('main' or 'neutron').
        include_channels: Include channel information per connection.
        group_by_user: Group connections by user/service.
        limit: Maximum number of connections to return.

    Returns:
        GetRabbitMQConnectionsOutput with connection information.

    Raises:
        ToolExecutionError: If connections cannot be retrieved.

    Example:
        >>> conns = await get_rabbitmq_connections(k8s_adapter)
        >>> print(f"Total connections: {conns.total_connections}")
        >>> for user in conns.by_user:
        ...     print(f"{user.user}: {user.connection_count} connections")
    """
    logger.info(
        "getting_rabbitmq_connections",
        instance=rabbitmq_instance,
        include_channels=include_channels,
        group_by_user=group_by_user,
        limit=limit,
    )

    try:
        async with RabbitMQClient(kubernetes_adapter, instance=rabbitmq_instance) as client:
            # Get connections
            connections = await client.list_connections()

            # Get channels if requested
            channels_by_connection: dict[str, int] = {}
            if include_channels:
                channels = await client.list_channels()
                for ch in channels:
                    conn_name = ch.get("connection", "")
                    channels_by_connection[conn_name] = channels_by_connection.get(conn_name, 0) + 1

            # Build connection info list
            all_connections: list[RabbitMQConnectionInfo] = []
            user_stats: dict[str, dict] = defaultdict(
                lambda: {"connection_count": 0, "channel_count": 0, "service_name": ""}
            )

            running_count = 0
            blocked_count = 0
            total_channels = 0

            for conn in connections:
                state = _map_connection_state(conn.state)
                channel_count = channels_by_connection.get(conn.name, conn.channels)

                conn_info = RabbitMQConnectionInfo(
                    name=conn.name,
                    user=conn.user,
                    state=state,
                    ssl=conn.ssl,
                    protocol=conn.protocol,
                    channels=channel_count,
                    client_host=conn.client_host,
                    connected_at="",  # Not available from list_connections
                )
                all_connections.append(conn_info)

                # Update stats
                if state == ConnectionState.RUNNING:
                    running_count += 1
                elif state in (ConnectionState.BLOCKED, ConnectionState.BLOCKING):
                    blocked_count += 1

                total_channels += channel_count

                # Update user stats
                if group_by_user:
                    user_stats[conn.user]["connection_count"] += 1
                    user_stats[conn.user]["channel_count"] += channel_count
                    if not user_stats[conn.user]["service_name"]:
                        user_stats[conn.user]["service_name"] = (
                            RabbitMQClient.infer_service_from_user(conn.user)
                        )

            # Apply limit
            limited_connections = all_connections[:limit]

            # Build user summaries
            by_user = (
                [
                    ConnectionsByUserSummary(
                        user=user,
                        connection_count=stats["connection_count"],
                        channel_count=stats["channel_count"],
                        service_name=stats["service_name"],
                    )
                    for user, stats in sorted(
                        user_stats.items(),
                        key=lambda x: x[1]["connection_count"],
                        reverse=True,
                    )
                ]
                if group_by_user
                else []
            )

            # Get top users
            top_users = [u.user for u in by_user[:5]]

            # Calculate utilization
            connection_limit = DEFAULT_CONNECTION_LIMIT
            utilization_percent: float | None = None
            if connection_limit > 0:
                utilization_percent = (len(all_connections) / connection_limit) * 100

            # Determine health
            is_healthy = blocked_count == 0 and (
                utilization_percent is None or utilization_percent < CONNECTION_UTILIZATION_CRITICAL
            )

            # Generate recommendations
            recommendations = _generate_recommendations(
                total_connections=len(all_connections),
                blocked_connections=blocked_count,
                utilization_percent=utilization_percent,
                top_users=top_users,
            )

            output = GetRabbitMQConnectionsOutput(
                instance=rabbitmq_instance,
                connections=limited_connections,
                total_connections=len(all_connections),
                total_channels=total_channels,
                running_connections=running_count,
                blocked_connections=blocked_count,
                by_user=by_user,
                top_users=top_users,
                connection_limit=connection_limit,
                connection_utilization_percent=utilization_percent,
                has_blocked_connections=blocked_count > 0,
                is_connection_pool_healthy=is_healthy,
                recommendations=recommendations,
            )

            logger.info(
                "rabbitmq_connections_retrieved",
                instance=rabbitmq_instance,
                total_connections=len(all_connections),
                blocked_connections=blocked_count,
                is_healthy=is_healthy,
            )

            return output

    except ToolExecutionError:
        raise
    except Exception as e:
        logger.error("get_rabbitmq_connections_failed", error=str(e), instance=rabbitmq_instance)
        raise ToolExecutionError(
            message=f"Failed to get RabbitMQ connections: {e}",
            tool_name="get_rabbitmq_connections",
            details={"error": str(e), "instance": rabbitmq_instance},
        ) from e
