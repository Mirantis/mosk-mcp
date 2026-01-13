"""Get detailed OSD information including PG distribution tool.

This module provides the get_osd_details MCP tool for retrieving
comprehensive information about a specific OSD.

Safety Level: Read-only
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from mosk_mcp.adapters.ceph import (
    CAPACITY_CRITICAL_THRESHOLD,
    CAPACITY_WARNING_THRESHOLD,
    CephAdapter,
)
from mosk_mcp.core.exceptions import ResourceNotFoundError, ToolExecutionError
from mosk_mcp.observability.logging import get_logger
from mosk_mcp.tools.ceph_operations.models import (
    CapacityStatus,
    CapacitySummary,
    GetOSDDetailsOutput,
    OSDDetails,
)
from mosk_mcp.tools.common import format_bytes


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
    if percent_used >= 85:
        return CapacityStatus.EMERGENCY
    if percent_used >= CAPACITY_CRITICAL_THRESHOLD:
        return CapacityStatus.CRITICAL
    if percent_used >= CAPACITY_WARNING_THRESHOLD:
        return CapacityStatus.WARNING
    return CapacityStatus.NORMAL


def _generate_health_warnings(
    osd_id: int,
    status: str,
    state: str,
    utilization_percent: float,
    commit_latency_ms: float,
    apply_latency_ms: float,
) -> list[str]:
    """Generate health warnings for an OSD.

    Args:
        osd_id: OSD identifier.
        status: OSD status (up/down).
        state: OSD state (in/out).
        utilization_percent: Utilization percentage.
        commit_latency_ms: Commit latency in ms.
        apply_latency_ms: Apply latency in ms.

    Returns:
        List of warning messages.
    """
    warnings: list[str] = []

    # Status warnings
    if status == "down":
        warnings.append(f"OSD {osd_id} is DOWN - may need attention")

    if state == "out":
        warnings.append(f"OSD {osd_id} is OUT - not receiving data")

    # Utilization warnings
    if utilization_percent >= 85:
        warnings.append(f"OSD {osd_id} utilization is {utilization_percent:.1f}% - critically high")
    elif utilization_percent >= 80:
        warnings.append(f"OSD {osd_id} utilization is {utilization_percent:.1f}% - high")

    # Latency warnings
    if commit_latency_ms > 100:
        warnings.append(f"OSD {osd_id} commit latency is {commit_latency_ms:.1f}ms - high")

    if apply_latency_ms > 100:
        warnings.append(f"OSD {osd_id} apply latency is {apply_latency_ms:.1f}ms - high")

    return warnings


def _generate_recommendations(
    osd_id: int,
    status: str,
    state: str,
    utilization_percent: float,
    commit_latency_ms: float,
    all_osds_avg_utilization: float,
) -> list[str]:
    """Generate operational recommendations for an OSD.

    Args:
        osd_id: OSD identifier.
        status: OSD status (up/down).
        state: OSD state (in/out).
        utilization_percent: Utilization percentage.
        commit_latency_ms: Commit latency in ms.
        all_osds_avg_utilization: Average utilization across all OSDs.

    Returns:
        List of recommendations.
    """
    recommendations: list[str] = []

    if status == "down":
        recommendations.append(
            f"Investigate why OSD {osd_id} is down. Check system logs and ceph-osd service status."
        )

    if state == "out":
        recommendations.append(
            f"If OSD {osd_id} is intentionally out for maintenance, "
            "remember to mark it 'in' when done."
        )

    # Imbalanced utilization
    if utilization_percent > 0 and all_osds_avg_utilization > 0:
        ratio = utilization_percent / all_osds_avg_utilization
        if ratio > 1.2:
            recommendations.append(
                f"OSD {osd_id} is {ratio:.0%} of average utilization. "
                "Consider reweighting to balance data."
            )
        elif ratio < 0.8:
            recommendations.append(
                f"OSD {osd_id} is only {ratio:.0%} of average utilization. "
                "May be underutilized or newly added."
            )

    # High latency
    if commit_latency_ms > 50:
        recommendations.append(
            f"OSD {osd_id} has elevated commit latency ({commit_latency_ms:.1f}ms). "
            "Check disk health and I/O patterns."
        )

    # High utilization
    if utilization_percent >= 85:
        recommendations.append(
            f"OSD {osd_id} is near full. Consider adding more OSDs or migrating data to other OSDs."
        )

    return recommendations


async def get_osd_details(
    kubernetes_adapter: KubernetesAdapter,
    osd_id: int,
    include_pg_distribution: bool = True,
    include_performance: bool = True,
) -> GetOSDDetailsOutput:
    """Get detailed information about a specific OSD.

    This tool retrieves comprehensive information about a specific OSD,
    including capacity, performance metrics, and operational status.

    Safety Level: Read-only

    Args:
        kubernetes_adapter: Kubernetes adapter for cluster communication.
        osd_id: OSD identifier (non-negative integer).
        include_pg_distribution: Include PG distribution information.
        include_performance: Include performance metrics.

    Returns:
        GetOSDDetailsOutput with detailed OSD information.

    Raises:
        ResourceNotFoundError: If OSD doesn't exist.
        ToolExecutionError: If details cannot be retrieved.

    Example:
        >>> details = await get_osd_details(k8s_adapter, osd_id=5)
        >>> print(f"OSD 5 on {details.osd.host}: {details.osd.status}")
        >>> print(f"Utilization: {details.osd.capacity.percent_used:.1f}%")
    """
    logger.info(
        "getting_osd_details",
        osd_id=osd_id,
        include_pg_distribution=include_pg_distribution,
        include_performance=include_performance,
    )

    try:
        async with CephAdapter(kubernetes_adapter) as ceph:
            # Get OSD details
            osd_info = await ceph.get_osd_details(osd_id)

            # Get all OSDs for comparison
            all_osds = await ceph.list_osds()
            total_osds = len(all_osds)
            avg_utilization = (
                sum(o.utilization_percent for o in all_osds) / total_osds if total_osds > 0 else 0.0
            )

            # Build capacity summary
            capacity_status = _get_capacity_status(osd_info.utilization_percent)
            capacity = CapacitySummary(
                total_bytes=osd_info.total_bytes,
                used_bytes=osd_info.used_bytes,
                available_bytes=osd_info.available_bytes,
                percent_used=osd_info.utilization_percent,
                status=capacity_status,
                total_human=format_bytes(osd_info.total_bytes),
                used_human=format_bytes(osd_info.used_bytes),
                available_human=format_bytes(osd_info.available_bytes),
            )

            # Build PG distribution - currently shows total PGs
            # Per-pool distribution would require additional 'ceph pg ls-by-osd' calls
            pg_distribution: dict[str, int] = {}
            if include_pg_distribution:
                pg_distribution["total"] = osd_info.pgs

            # Generate health warnings
            health_warnings = _generate_health_warnings(
                osd_id=osd_info.osd_id,
                status=osd_info.status.value,
                state=osd_info.state.value,
                utilization_percent=osd_info.utilization_percent,
                commit_latency_ms=osd_info.commit_latency_ms,
                apply_latency_ms=osd_info.apply_latency_ms,
            )

            # Build OSD details
            osd_details = OSDDetails(
                osd_id=osd_info.osd_id,
                uuid=osd_info.uuid,
                host=osd_info.host,
                status=osd_info.status.value,
                state=osd_info.state.value,
                device_class=osd_info.device_class,
                crush_weight=osd_info.crush_weight,
                reweight=osd_info.reweight,
                capacity=capacity,
                pgs=osd_info.pgs,
                pg_distribution=pg_distribution,
                commit_latency_ms=osd_info.commit_latency_ms,
                apply_latency_ms=osd_info.apply_latency_ms,
                is_healthy=osd_info.is_healthy,
                health_warnings=health_warnings,
            )

            # Generate recommendations
            recommendations = _generate_recommendations(
                osd_id=osd_info.osd_id,
                status=osd_info.status.value,
                state=osd_info.state.value,
                utilization_percent=osd_info.utilization_percent,
                commit_latency_ms=osd_info.commit_latency_ms,
                all_osds_avg_utilization=avg_utilization,
            )

            output = GetOSDDetailsOutput(
                osd=osd_details,
                recommendations=recommendations,
            )

            logger.info(
                "osd_details_retrieved",
                osd_id=osd_id,
                status=osd_details.status,
                state=osd_details.state,
                utilization=f"{osd_details.capacity.percent_used:.1f}%",
            )

            return output

    except ResourceNotFoundError:
        raise
    except Exception as e:
        logger.error("get_osd_details_failed", osd_id=osd_id, error=str(e))
        raise ToolExecutionError(
            message=f"Failed to get OSD {osd_id} details: {e}",
            tool_name="get_osd_details",
            details={"osd_id": osd_id, "error": str(e)},
        ) from e
