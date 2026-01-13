"""Get storage capacity breakdown by pool tool.

This module provides the get_ceph_capacity MCP tool for retrieving
detailed storage capacity information across the Ceph cluster.

Safety Level: Read-only
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from mosk_mcp.adapters.ceph import (
    CAPACITY_CRITICAL_THRESHOLD,
    CAPACITY_EMERGENCY_THRESHOLD,
    CAPACITY_WARNING_THRESHOLD,
    CephAdapter,
)
from mosk_mcp.core.exceptions import ToolExecutionError
from mosk_mcp.observability.logging import get_logger
from mosk_mcp.tools.ceph_operations.models import (
    CapacityStatus,
    GetCephCapacityOutput,
    PoolCapacity,
)


if TYPE_CHECKING:
    from mosk_mcp.adapters.kubernetes import KubernetesAdapter


logger = get_logger(__name__)


def _get_capacity_status(percent_used: float) -> CapacityStatus:
    """Determine capacity status from utilization percentage.

    Args:
        percent_used: Utilization percentage.

    Returns:
        CapacityStatus enum value.
    """
    if percent_used >= CAPACITY_EMERGENCY_THRESHOLD:
        return CapacityStatus.EMERGENCY
    if percent_used >= CAPACITY_CRITICAL_THRESHOLD:
        return CapacityStatus.CRITICAL
    if percent_used >= CAPACITY_WARNING_THRESHOLD:
        return CapacityStatus.WARNING
    return CapacityStatus.NORMAL


def _generate_capacity_recommendations(
    percent_used: float,
    total_bytes: int,
    pools: list[dict[str, Any]],
) -> list[str]:
    """Generate capacity planning recommendations.

    Args:
        percent_used: Overall utilization percentage.
        total_bytes: Total storage capacity in bytes.
        pools: Pool capacity information.

    Returns:
        List of recommendations.
    """
    recommendations: list[str] = []

    # Overall capacity recommendations
    if percent_used >= CAPACITY_EMERGENCY_THRESHOLD:
        recommendations.append(
            "URGENT: Storage capacity is critical. Add OSDs immediately or delete unnecessary data."
        )
    elif percent_used >= CAPACITY_CRITICAL_THRESHOLD:
        recommendations.append(
            "Storage capacity is high. Plan to add OSDs within the next maintenance window."
        )
    elif percent_used >= CAPACITY_WARNING_THRESHOLD:
        recommendations.append(
            "Storage capacity is approaching warning threshold. Begin capacity planning."
        )
    else:
        recommendations.append("Storage capacity is healthy. Continue monitoring growth trends.")

    # Per-pool recommendations
    high_util_pools = [p for p in pools if p.get("percent_used", 0) > 50]
    if high_util_pools:
        pool_names = ", ".join(p.get("pool_name", "unknown") for p in high_util_pools)
        recommendations.append(
            f"Pools with high utilization: {pool_names}. "
            "Review data retention policies for these pools."
        )

    # Growth projection
    if percent_used > 0:
        remaining_percent = 100 - percent_used
        # Rough estimate: if growing at 1% per day, calculate days until warning
        if remaining_percent < (100 - CAPACITY_WARNING_THRESHOLD):
            recommendations.append(
                f"Only {remaining_percent:.1f}% capacity remaining. "
                "Monitor daily growth rate closely."
            )

    return recommendations


async def get_ceph_capacity(
    kubernetes_adapter: KubernetesAdapter,
    include_pools: bool = True,
    include_classes: bool = True,
) -> GetCephCapacityOutput:
    """Get storage capacity breakdown by pool.

    This tool retrieves detailed storage capacity information including
    overall cluster capacity, per-pool breakdown, and capacity by device class.

    Safety Level: Read-only

    Args:
        kubernetes_adapter: Kubernetes adapter for cluster communication.
        include_pools: Include per-pool capacity breakdown.
        include_classes: Include capacity by device class.

    Returns:
        GetCephCapacityOutput with capacity information.

    Raises:
        ToolExecutionError: If capacity cannot be retrieved.

    Example:
        >>> capacity = await get_ceph_capacity(k8s_adapter)
        >>> print(f"Total: {capacity.total_bytes / 1024**4:.2f} TB")
        >>> print(f"Used: {capacity.percent_used:.1f}%")
        >>> for pool in capacity.pools:
        ...     print(f"  {pool.pool_name}: {pool.percent_used:.1f}%")
    """
    logger.info(
        "getting_ceph_capacity",
        include_pools=include_pools,
        include_classes=include_classes,
    )

    try:
        async with CephAdapter(kubernetes_adapter) as ceph:
            # Get capacity information
            capacity_data = await ceph.get_capacity()

            # Extract totals
            total_bytes = capacity_data.get("total_bytes", 0)
            used_bytes = capacity_data.get("used_bytes", 0)
            available_bytes = capacity_data.get("available_bytes", 0)
            percent_used = capacity_data.get("capacity_percent", 0.0)

            # Get status
            status = _get_capacity_status(percent_used)

            # Build pool capacity list
            pools: list[PoolCapacity] = []
            if include_pools:
                for pool_data in capacity_data.get("pools", []):
                    pool = PoolCapacity(
                        pool_id=pool_data.get("pool_id", 0),
                        pool_name=pool_data.get("pool_name", ""),
                        stored_bytes=pool_data.get("total_bytes", 0),
                        used_bytes=pool_data.get("used_bytes", 0),
                        max_available_bytes=pool_data.get("max_avail_bytes", 0),
                        percent_used=pool_data.get("percent_used", 0.0),
                        objects=pool_data.get("objects", 0),
                        replication_size=pool_data.get("size", 3),
                    )
                    pools.append(pool)

                # Sort by utilization descending
                pools.sort(key=lambda p: p.percent_used, reverse=True)

            # Build device class breakdown
            by_device_class: dict[str, dict[str, Any]] = {}
            if include_classes:
                # Get OSD list and aggregate by class
                osds = await ceph.list_osds()
                class_totals: dict[str, dict[str, int]] = {}

                for osd in osds:
                    device_class = osd.device_class or "unknown"
                    if device_class not in class_totals:
                        class_totals[device_class] = {
                            "total_bytes": 0,
                            "used_bytes": 0,
                            "osd_count": 0,
                        }

                    class_totals[device_class]["total_bytes"] += osd.total_bytes
                    class_totals[device_class]["used_bytes"] += osd.used_bytes
                    class_totals[device_class]["osd_count"] += 1

                for device_class, totals in class_totals.items():
                    class_total = totals["total_bytes"]
                    class_used = totals["used_bytes"]
                    class_percent = (class_used / class_total * 100) if class_total > 0 else 0.0
                    by_device_class[device_class] = {
                        "total_bytes": class_total,
                        "used_bytes": class_used,
                        "available_bytes": class_total - class_used,
                        "percent_used": round(class_percent, 2),
                        "osd_count": totals["osd_count"],
                    }

            # Generate recommendations
            pool_data_for_recs = [
                {
                    "pool_name": p.pool_name,
                    "percent_used": p.percent_used,
                }
                for p in pools
            ]
            recommendations = _generate_capacity_recommendations(
                percent_used=percent_used,
                total_bytes=total_bytes,
                pools=pool_data_for_recs,
            )

            # Build thresholds
            thresholds = {
                "warning": CAPACITY_WARNING_THRESHOLD,
                "critical": CAPACITY_CRITICAL_THRESHOLD,
                "emergency": CAPACITY_EMERGENCY_THRESHOLD,
            }

            output = GetCephCapacityOutput(
                total_bytes=total_bytes,
                used_bytes=used_bytes,
                available_bytes=available_bytes,
                percent_used=percent_used,
                status=status,
                thresholds=thresholds,
                pools=pools,
                by_device_class=by_device_class,
                recommendations=recommendations,
                timestamp=capacity_data.get(
                    "timestamp",
                    "",
                ),
            )

            logger.info(
                "ceph_capacity_retrieved",
                percent_used=f"{percent_used:.1f}%",
                status=status.value,
                pool_count=len(pools),
            )

            return output

    except Exception as e:
        logger.error("get_ceph_capacity_failed", error=str(e))
        raise ToolExecutionError(
            message=f"Failed to get Ceph capacity: {e}",
            tool_name="get_ceph_capacity",
            details={"error": str(e)},
        ) from e
