"""List RabbitMQ queues with filtering and analysis tool.

This module provides the list_rabbitmq_queues MCP tool for retrieving
queue information with optional filtering by vhost.

Safety Level: Read-only
"""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING, Literal

from mosk_mcp.core.exceptions import ToolExecutionError
from mosk_mcp.observability.logging import get_logger
from mosk_mcp.tools.messaging_operations.models import (
    ListRabbitMQQueuesOutput,
    QueuesByVhostSummary,
    RabbitMQQueueInfo,
)
from mosk_mcp.tools.messaging_operations.rabbitmq_client import RabbitMQClient


if TYPE_CHECKING:
    from mosk_mcp.adapters.kubernetes import KubernetesAdapter


logger = get_logger(__name__)


# Threshold for considering a queue as having a backlog
BACKLOG_THRESHOLD = 1000

# Threshold for considering a queue as stale (messages with no consumers)
STALE_QUEUE_MIN_MESSAGES = 10


def _generate_recommendations(
    has_backlog: bool,
    has_stale_queues: bool,
    stale_queue_count: int,
    total_messages: int,
    top_queues: list[str],
) -> list[str]:
    """Generate recommendations based on queue analysis.

    Args:
        has_backlog: Whether any queue has a significant backlog.
        has_stale_queues: Whether stale queues exist.
        stale_queue_count: Number of stale queues.
        total_messages: Total messages across all queues.
        top_queues: List of top queues by message count.

    Returns:
        List of recommendation strings.
    """
    recommendations = []

    if has_backlog:
        recommendations.append(
            f"Message backlog detected ({total_messages} total messages). "
            "Check consumer health and processing capacity."
        )
        if top_queues:
            recommendations.append(f"Investigate top queues: {', '.join(top_queues[:3])}")

    if has_stale_queues:
        recommendations.append(
            f"{stale_queue_count} stale queue(s) found (messages with no consumers). "
            "These may indicate failed services or configuration issues."
        )

    if total_messages > 10000:
        recommendations.append(
            "High total message count. Consider scaling consumers or investigating slow processing."
        )

    if not has_backlog and not has_stale_queues:
        recommendations.append("Queue health is normal - no action required.")

    return recommendations


async def list_rabbitmq_queues(
    kubernetes_adapter: KubernetesAdapter,
    rabbitmq_instance: Literal["main", "neutron"] = "main",
    vhost: str | None = None,
    show_empty: bool = False,
    include_consumers: bool = True,
    limit: int = 100,
) -> ListRabbitMQQueuesOutput:
    """List RabbitMQ queues with filtering and analysis.

    This tool retrieves queue information from RabbitMQ, optionally filtered
    by virtual host, and provides analysis of queue health.

    Safety Level: Read-only

    Args:
        kubernetes_adapter: Kubernetes adapter for cluster communication.
        rabbitmq_instance: RabbitMQ instance to query ('main' or 'neutron').
        vhost: Virtual host to filter by (all vhosts if None).
        show_empty: Include queues with zero messages.
        include_consumers: Include consumer count per queue.
        limit: Maximum number of queues to return.

    Returns:
        ListRabbitMQQueuesOutput with queue information.

    Raises:
        ToolExecutionError: If queues cannot be retrieved.

    Example:
        >>> queues = await list_rabbitmq_queues(k8s_adapter, vhost="nova")
        >>> print(f"Total messages: {queues.total_messages}")
        >>> for q in queues.queues:
        ...     print(f"{q.name}: {q.messages} messages, {q.consumers} consumers")
    """
    logger.info(
        "listing_rabbitmq_queues",
        instance=rabbitmq_instance,
        vhost=vhost,
        show_empty=show_empty,
        limit=limit,
    )

    try:
        async with RabbitMQClient(kubernetes_adapter, instance=rabbitmq_instance) as client:
            all_queues: list[RabbitMQQueueInfo] = []
            vhost_stats: dict[str, dict] = defaultdict(
                lambda: {
                    "queue_count": 0,
                    "total_messages": 0,
                    "total_consumers": 0,
                    "stale_queues": 0,
                }
            )

            # Get vhosts to query
            if vhost:
                vhosts_to_query = [vhost]
            else:
                vhosts_to_query = await client.list_vhosts()

            # Query queues for each vhost
            for vh in vhosts_to_query:
                try:
                    queues = await client.list_queues(vhost=vh)

                    for q in queues:
                        # Skip empty queues if not requested
                        if not show_empty and q.messages == 0:
                            continue

                        # Determine if queue is stale
                        is_stale = q.messages >= STALE_QUEUE_MIN_MESSAGES and q.consumers == 0

                        queue_info = RabbitMQQueueInfo(
                            name=q.name,
                            vhost=vh,
                            messages=q.messages,
                            messages_ready=q.messages_ready,
                            messages_unacked=q.messages_unacked,
                            consumers=q.consumers if include_consumers else 0,
                            memory_bytes=q.memory_bytes,
                            state=q.state,
                            is_stale=is_stale,
                        )
                        all_queues.append(queue_info)

                        # Update vhost stats
                        vhost_stats[vh]["queue_count"] += 1
                        vhost_stats[vh]["total_messages"] += q.messages
                        vhost_stats[vh]["total_consumers"] += q.consumers
                        if is_stale:
                            vhost_stats[vh]["stale_queues"] += 1

                except ToolExecutionError as e:
                    logger.warning(
                        "failed_to_list_queues_for_vhost",
                        vhost=vh,
                        error=str(e),
                    )
                    continue

            # Sort by message count (descending) and apply limit
            all_queues.sort(key=lambda q: q.messages, reverse=True)
            limited_queues = all_queues[:limit]

            # Calculate totals
            total_messages = sum(q.messages for q in all_queues)
            total_consumers = sum(q.consumers for q in all_queues)
            stale_queue_count = sum(1 for q in all_queues if q.is_stale)

            # Build vhost summaries
            by_vhost = [
                QueuesByVhostSummary(
                    vhost=vh,
                    queue_count=stats["queue_count"],
                    total_messages=stats["total_messages"],
                    total_consumers=stats["total_consumers"],
                    stale_queues=stats["stale_queues"],
                )
                for vh, stats in sorted(
                    vhost_stats.items(), key=lambda x: x[1]["total_messages"], reverse=True
                )
            ]

            # Get top queues by message count
            top_queues = [q.name for q in all_queues[:5]]

            # Determine health indicators
            has_backlog = any(q.messages >= BACKLOG_THRESHOLD for q in all_queues)
            has_stale_queues = stale_queue_count > 0

            # Generate recommendations
            recommendations = _generate_recommendations(
                has_backlog=has_backlog,
                has_stale_queues=has_stale_queues,
                stale_queue_count=stale_queue_count,
                total_messages=total_messages,
                top_queues=top_queues,
            )

            output = ListRabbitMQQueuesOutput(
                instance=rabbitmq_instance,
                queues=limited_queues,
                total_queues=len(limited_queues),
                total_messages=total_messages,
                total_consumers=total_consumers,
                stale_queue_count=stale_queue_count,
                by_vhost=by_vhost,
                top_queues_by_messages=top_queues,
                has_backlog=has_backlog,
                has_stale_queues=has_stale_queues,
                recommendations=recommendations,
            )

            logger.info(
                "rabbitmq_queues_listed",
                instance=rabbitmq_instance,
                queue_count=len(limited_queues),
                total_messages=total_messages,
                stale_queues=stale_queue_count,
            )

            return output

    except ToolExecutionError:
        raise
    except Exception as e:
        logger.error("list_rabbitmq_queues_failed", error=str(e), instance=rabbitmq_instance)
        raise ToolExecutionError(
            message=f"Failed to list RabbitMQ queues: {e}",
            tool_name="list_rabbitmq_queues",
            details={"error": str(e), "instance": rabbitmq_instance, "vhost": vhost},
        ) from e
