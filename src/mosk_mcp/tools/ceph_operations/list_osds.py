"""List all OSDs with status, host, and capacity tool.

This module provides the list_osds MCP tool for retrieving
a comprehensive list of all OSDs in the Ceph cluster.

Safety Level: Read-only
"""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

from mosk_mcp.adapters.ceph import CephAdapter, OSDInfo
from mosk_mcp.core.exceptions import ToolExecutionError
from mosk_mcp.observability.logging import get_logger
from mosk_mcp.tools.ceph_operations.models import (
    ListOSDsOutput,
    OSDSummary,
)


if TYPE_CHECKING:
    from mosk_mcp.adapters.kubernetes import KubernetesAdapter


logger = get_logger(__name__)


def _osd_to_summary(osd: OSDInfo) -> OSDSummary:
    """Convert OSDInfo to OSDSummary.

    Args:
        osd: OSD information from adapter.

    Returns:
        OSDSummary model.
    """
    return OSDSummary(
        osd_id=osd.osd_id,
        host=osd.host,
        status=osd.status.value,
        state=osd.state.value,
        device_class=osd.device_class,
        utilization_percent=osd.utilization_percent,
        capacity_bytes=osd.total_bytes,
        used_bytes=osd.used_bytes,
        pgs=osd.pgs,
        is_healthy=osd.is_healthy,
    )


def _filter_osds(
    osds: list[OSDInfo],
    host_filter: str | None = None,
    status_filter: str | None = None,
) -> list[OSDInfo]:
    """Filter OSDs based on criteria.

    Args:
        osds: List of OSD information.
        host_filter: Filter by host name (substring match).
        status_filter: Filter by status ('up', 'down', or 'all').

    Returns:
        Filtered list of OSDs.
    """
    result = osds

    if host_filter:
        host_lower = host_filter.lower()
        result = [o for o in result if host_lower in o.host.lower()]

    if status_filter and status_filter != "all":
        status_lower = status_filter.lower()
        result = [o for o in result if o.status.value == status_lower]

    return result


def _aggregate_by_host(osds: list[OSDInfo]) -> dict[str, int]:
    """Aggregate OSD counts by host.

    Args:
        osds: List of OSD information.

    Returns:
        Dictionary mapping host name to OSD count.
    """
    counts: dict[str, int] = defaultdict(int)
    for osd in osds:
        counts[osd.host] += 1
    return dict(counts)


def _aggregate_by_device_class(osds: list[OSDInfo]) -> dict[str, int]:
    """Aggregate OSD counts by device class.

    Args:
        osds: List of OSD information.

    Returns:
        Dictionary mapping device class to OSD count.
    """
    counts: dict[str, int] = defaultdict(int)
    for osd in osds:
        device_class = osd.device_class or "unknown"
        counts[device_class] += 1
    return dict(counts)


async def list_osds(
    kubernetes_adapter: KubernetesAdapter,
    host_filter: str | None = None,
    status_filter: str | None = None,
    include_performance: bool = False,
) -> ListOSDsOutput:
    """List all OSDs with status, host, and capacity.

    This tool retrieves a comprehensive list of all OSDs in the Ceph cluster,
    including their operational status, host location, and capacity information.

    Safety Level: Read-only

    Args:
        kubernetes_adapter: Kubernetes adapter for cluster communication.
        host_filter: Filter OSDs by host name (substring match).
        status_filter: Filter by status ('all', 'up', or 'down').
        include_performance: Include latency metrics (not shown in summary).

    Returns:
        ListOSDsOutput with list of OSDs and aggregated statistics.

    Raises:
        ToolExecutionError: If OSD list cannot be retrieved.

    Example:
        >>> result = await list_osds(k8s_adapter)
        >>> print(f"Total OSDs: {result.total_count}")
        >>> for osd in result.osds:
        ...     print(f"OSD {osd.osd_id} on {osd.host}: {osd.status}")
    """
    logger.info(
        "listing_osds",
        host_filter=host_filter,
        status_filter=status_filter,
    )

    try:
        async with CephAdapter(kubernetes_adapter) as ceph:
            # Get all OSDs
            all_osds = await ceph.list_osds()

            # Apply filters
            filtered_osds = _filter_osds(
                osds=all_osds,
                host_filter=host_filter,
                status_filter=status_filter,
            )

            # Convert to summaries
            osd_summaries = [_osd_to_summary(osd) for osd in filtered_osds]

            # Sort by OSD ID
            osd_summaries.sort(key=lambda o: o.osd_id)

            # Calculate statistics
            total_count = len(all_osds)
            up_count = sum(1 for o in all_osds if o.status.value == "up")
            down_count = sum(1 for o in all_osds if o.status.value == "down")
            in_count = sum(1 for o in all_osds if o.state.value == "in")
            out_count = sum(1 for o in all_osds if o.state.value == "out")

            # Aggregations
            by_host = _aggregate_by_host(all_osds)
            by_device_class = _aggregate_by_device_class(all_osds)

            output = ListOSDsOutput(
                osds=osd_summaries,
                total_count=total_count,
                up_count=up_count,
                down_count=down_count,
                in_count=in_count,
                out_count=out_count,
                by_host=by_host,
                by_device_class=by_device_class,
            )

            logger.info(
                "osds_listed",
                total=total_count,
                returned=len(osd_summaries),
                up=up_count,
                down=down_count,
            )

            return output

    except Exception as e:
        logger.error("list_osds_failed", error=str(e))
        raise ToolExecutionError(
            message=f"Failed to list OSDs: {e}",
            tool_name="list_osds",
            details={"error": str(e)},
        ) from e
