"""Get Ceph storage cluster health status tool.

This module provides the get_ceph_health MCP tool for retrieving
comprehensive Ceph cluster health information including OSD status,
placement group health, and capacity utilization.

Safety Level: Read-only
"""

from __future__ import annotations


__all__ = [
    "GetCephHealthInput",
    "GetCephHealthOutput",
    "get_ceph_health",
]

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from mosk_mcp.adapters.ceph import (
    CAPACITY_CRITICAL_THRESHOLD,
    CAPACITY_WARNING_THRESHOLD,
    CephAdapter,
    CephHealthStatus,
)
from mosk_mcp.observability.logging import get_logger
from mosk_mcp.tools.cluster_health.models import (
    GetCephHealthInput,
    GetCephHealthOutput,
    OSDHealthInfo,
    PoolHealthInfo,
)
from mosk_mcp.tools.common import score_to_health, tool_handler
from mosk_mcp.tools.common.enums import HealthStatus


if TYPE_CHECKING:
    from mosk_mcp.adapters.kubernetes import KubernetesAdapter


logger = get_logger(__name__)


def _calculate_ceph_score(
    health_status: CephHealthStatus,
    osds_total: int,
    osds_up: int,
    osds_in: int,
    pgs_total: int,
    pgs_active_clean: int,
    pgs_degraded: int,
    capacity_percent: float,
) -> int:
    """Calculate Ceph health score (0-100).

    Scoring breakdown:
    - Cluster health status: 25 points
    - OSD health: 30 points
    - PG health: 25 points
    - Capacity headroom: 20 points

    Args:
        health_status: Ceph health status.
        osds_total: Total OSDs.
        osds_up: OSDs that are up.
        osds_in: OSDs that are in cluster.
        pgs_total: Total placement groups.
        pgs_active_clean: Active+clean PGs.
        pgs_degraded: Degraded PGs.
        capacity_percent: Capacity utilization percentage.

    Returns:
        Health score from 0-100.
    """
    score = 0

    # Cluster health status (25 points)
    if health_status == CephHealthStatus.HEALTH_OK:
        score += 25
    elif health_status == CephHealthStatus.HEALTH_WARN:
        score += 15
    elif health_status == CephHealthStatus.HEALTH_ERR:
        score += 0
    else:
        score += 10  # Unknown

    # OSD health (30 points)
    if osds_total > 0:
        # OSDs need to be both up AND in
        osd_healthy_ratio = min(osds_up, osds_in) / osds_total
        osd_score = osd_healthy_ratio * 30
        score += int(osd_score)
    else:
        # No OSDs is critical but might mean Ceph isn't deployed
        pass

    # PG health (25 points)
    if pgs_total > 0:
        # Penalize degraded PGs more heavily
        clean_ratio = pgs_active_clean / pgs_total
        degraded_penalty = (pgs_degraded / pgs_total) * 0.5 if pgs_total > 0 else 0
        pg_score = max(0, clean_ratio - degraded_penalty) * 25
        score += int(pg_score)
    else:
        score += 25  # No PGs might mean pools aren't created yet

    # Capacity headroom (20 points)
    if capacity_percent < 70:
        score += 20  # Plenty of headroom
    elif capacity_percent < CAPACITY_WARNING_THRESHOLD:
        score += 15
    elif capacity_percent < CAPACITY_CRITICAL_THRESHOLD:
        score += 8
    else:
        score += 0  # Critical capacity

    return min(100, max(0, score))


def _capacity_status(percent: float) -> str:
    """Determine capacity status from utilization percentage.

    Args:
        percent: Capacity utilization percentage.

    Returns:
        Status string (normal, warning, critical, emergency).
    """
    if percent < CAPACITY_WARNING_THRESHOLD:
        return "normal"
    if percent < CAPACITY_CRITICAL_THRESHOLD:
        return "warning"
    if percent < 95:
        return "critical"
    return "emergency"


def _health_status_to_string(status: CephHealthStatus) -> str:
    """Convert CephHealthStatus to string.

    Args:
        status: Ceph health status enum.

    Returns:
        String representation (HEALTH_OK, HEALTH_WARN, HEALTH_ERR).
    """
    mapping = {
        CephHealthStatus.HEALTH_OK: "HEALTH_OK",
        CephHealthStatus.HEALTH_WARN: "HEALTH_WARN",
        CephHealthStatus.HEALTH_ERR: "HEALTH_ERR",
        CephHealthStatus.UNKNOWN: "UNKNOWN",
    }
    return mapping.get(status, "UNKNOWN")


def _generate_recommendations(
    health_status: CephHealthStatus,
    osds_total: int,
    osds_up: int,
    osds_in: int,
    pgs_degraded: int,
    pgs_recovering: int,
    capacity_percent: float,
    is_recovering: bool,
    health_checks: dict[str, str],
) -> list[str]:
    """Generate recommendations based on Ceph health status.

    Args:
        health_status: Ceph health status.
        osds_total: Total OSDs.
        osds_up: OSDs that are up.
        osds_in: OSDs in cluster.
        pgs_degraded: Degraded PGs.
        pgs_recovering: Recovering PGs.
        capacity_percent: Capacity utilization.
        is_recovering: Whether recovery is in progress.
        health_checks: Active health checks.

    Returns:
        List of recommendations.
    """
    recommendations: list[str] = []

    # OSD recommendations
    osds_down = osds_total - osds_up
    if osds_down > 0:
        recommendations.append(f"{osds_down} OSD(s) are down - check ceph-osd pods and node status")

    osds_out = osds_total - osds_in
    if osds_out > 0:
        recommendations.append(
            f"{osds_out} OSD(s) are out - investigate and mark in if appropriate"
        )

    # PG recommendations
    if pgs_degraded > 0 and not is_recovering:
        recommendations.append(f"{pgs_degraded} PGs degraded but not recovering - check OSD status")

    if is_recovering:
        recommendations.append("Recovery in progress - avoid maintenance operations until complete")

    # Capacity recommendations
    if capacity_percent >= 95:
        recommendations.append("EMERGENCY: Capacity >95% - immediately add storage or remove data")
    elif capacity_percent >= CAPACITY_CRITICAL_THRESHOLD:
        recommendations.append(
            f"CRITICAL: Capacity at {capacity_percent:.1f}% - add storage urgently"
        )
    elif capacity_percent >= CAPACITY_WARNING_THRESHOLD:
        recommendations.append(
            f"WARNING: Capacity at {capacity_percent:.1f}% - plan storage expansion"
        )

    # Health check based recommendations
    for check_name, message in health_checks.items():
        if "OSD" in check_name.upper():
            recommendations.append(f"Ceph alert: {message}")
        elif "PG" in check_name.upper():
            recommendations.append(f"PG issue: {message}")
        elif "SLOW" in check_name.upper():
            recommendations.append(f"Performance: {message}")

    return recommendations[:10]


@tool_handler("get_ceph_health")
async def get_ceph_health(
    kubernetes_adapter: KubernetesAdapter,
    input_data: GetCephHealthInput,
) -> GetCephHealthOutput:
    """Get Ceph storage cluster health status.

    This tool retrieves comprehensive health information about the Ceph
    cluster including OSD status, placement group health, and capacity
    utilization.

    Safety Level: Read-only

    Args:
        kubernetes_adapter: Kubernetes adapter for cluster communication.
        input_data: Input parameters for the query.

    Returns:
        GetCephHealthOutput with Ceph health information.

    Raises:
        ToolExecutionError: If health check fails.

    Example:
        >>> health = await get_ceph_health(k8s_adapter, GetCephHealthInput())
        >>> print(f"Health: {health.ceph_health}")
        >>> print(f"Capacity: {health.capacity_percent_used:.1f}%")
    """
    logger.info(
        "getting_ceph_health",
        include_osd_details=input_data.include_osd_details,
        include_pool_details=input_data.include_pool_details,
    )
    timestamp = datetime.now(UTC).isoformat()
    issues: list[str] = []

    async with CephAdapter(kubernetes_adapter) as ceph:
        # Get cluster status
        cluster_status = await ceph.get_cluster_status()

        # Extract health status
        health_status = cluster_status.health
        ceph_health_str = _health_status_to_string(health_status)

        if health_status == CephHealthStatus.HEALTH_ERR:
            issues.append("Ceph cluster is in HEALTH_ERR state")
        elif health_status == CephHealthStatus.HEALTH_WARN:
            issues.append("Ceph cluster has warnings")

        # Build health checks dict
        health_checks: dict[str, str] = {}
        for check_name, check_data in cluster_status.health_checks.items():
            message = check_data.get("summary", {}).get("message", check_name)
            health_checks[check_name] = message

        # OSD information
        osds_total = cluster_status.num_osds
        osds_up = cluster_status.num_osds_up
        osds_in = cluster_status.num_osds_in

        osds_down_list: list[int] = []
        if osds_up < osds_total:
            # We don't have individual OSD IDs from cluster_status,
            # would need to query OSD tree for that
            issues.append(f"{osds_total - osds_up} OSD(s) are down")

        # OSD details (if requested)
        osds: list[OSDHealthInfo] = []
        osd_details_available = True
        if input_data.include_osd_details:
            try:
                osd_list = await ceph.list_osds()
                for osd_data in osd_list:
                    osd_id = osd_data.osd_id
                    osd_up = osd_data.is_up
                    osd_in = osd_data.is_in

                    if not osd_up:
                        osds_down_list.append(osd_id)

                    osds.append(
                        OSDHealthInfo(
                            osd_id=osd_id,
                            up=osd_up,
                            in_cluster=osd_in,
                            healthy=osd_up and osd_in,
                            host=osd_data.host,
                            device_class=osd_data.device_class,
                            utilization_percent=osd_data.utilization_percent,
                        )
                    )
            except Exception as e:
                logger.warning("failed_to_get_osd_details", error=str(e))
                osd_details_available = False

        # PG information
        pgs_total = cluster_status.num_pgs
        pg_states = cluster_status.pg_states

        pgs_active_clean = pg_states.get("active+clean", 0)
        pgs_degraded = sum(count for state, count in pg_states.items() if "degraded" in state)
        pgs_recovering = sum(
            count
            for state, count in pg_states.items()
            if "recovering" in state or "recovery" in state
        )

        if pgs_degraded > 0:
            issues.append(f"{pgs_degraded} PGs are degraded")
        if pgs_recovering > 0:
            issues.append(f"{pgs_recovering} PGs are recovering")

        # Capacity
        capacity_total = cluster_status.total_bytes
        capacity_used = cluster_status.used_bytes
        capacity_available = cluster_status.available_bytes
        capacity_percent = cluster_status.capacity_percent

        cap_status = _capacity_status(capacity_percent)
        if cap_status in {"critical", "emergency"}:
            issues.append(f"Capacity {cap_status}: {capacity_percent:.1f}% used")
        elif cap_status == "warning":
            issues.append(f"Capacity warning: {capacity_percent:.1f}% used")

        # Pool details (if requested)
        # Note: Pool stats are extracted from get_capacity() which already
        # queries pool information. Using the pools from capacity response.
        pools: list[PoolHealthInfo] = []
        pool_details_available = True
        if input_data.include_pool_details:
            try:
                capacity_data = await ceph.get_capacity()
                for pool_name, pool_info in capacity_data.get("pools", {}).items():
                    pools.append(
                        PoolHealthInfo(
                            name=pool_name,
                            used_bytes=pool_info.get("used_bytes", 0),
                            max_avail_bytes=pool_info.get("avail_bytes", 0),
                            percent_used=pool_info.get("percent_used", 0.0),
                            objects=pool_info.get("objects", 0),
                        )
                    )
            except Exception as e:
                logger.warning("failed_to_get_pool_details", error=str(e))
                pool_details_available = False

        # Recovery status
        is_recovering = pgs_recovering > 0
        recovery_progress = None
        if is_recovering and pgs_total > 0:
            # Rough estimate based on PG recovery (pgs_total already checked > 0)
            recovery_progress = (pgs_active_clean / pgs_total) * 100

        # Calculate score
        score = _calculate_ceph_score(
            health_status=health_status,
            osds_total=osds_total,
            osds_up=osds_up,
            osds_in=osds_in,
            pgs_total=pgs_total,
            pgs_active_clean=pgs_active_clean,
            pgs_degraded=pgs_degraded,
            capacity_percent=capacity_percent,
        )

        health = score_to_health(score)

        # Generate message
        if health == HealthStatus.HEALTHY:
            message = (
                f"Ceph healthy: {ceph_health_str}, "
                f"{osds_up}/{osds_total} OSDs up, "
                f"{capacity_percent:.1f}% capacity"
            )
        else:
            message = f"Ceph {health.value}: {ceph_health_str}, {len(issues)} issue(s)"

        # Generate recommendations
        recommendations = _generate_recommendations(
            health_status=health_status,
            osds_total=osds_total,
            osds_up=osds_up,
            osds_in=osds_in,
            pgs_degraded=pgs_degraded,
            pgs_recovering=pgs_recovering,
            capacity_percent=capacity_percent,
            is_recovering=is_recovering,
            health_checks=health_checks,
        )

        output = GetCephHealthOutput(
            health=health,
            score=score,
            message=message,
            ceph_health=ceph_health_str,
            health_checks=health_checks,
            osds_total=osds_total,
            osds_up=osds_up,
            osds_in=osds_in,
            osds_down=osds_down_list,
            osds=osds if input_data.include_osd_details else [],
            pgs_total=pgs_total,
            pgs_active_clean=pgs_active_clean,
            pgs_degraded=pgs_degraded,
            pgs_recovering=pgs_recovering,
            capacity_total_bytes=capacity_total,
            capacity_used_bytes=capacity_used,
            capacity_available_bytes=capacity_available,
            capacity_percent_used=capacity_percent,
            capacity_status=cap_status,
            pools=pools if input_data.include_pool_details else [],
            osd_details_available=osd_details_available,
            pool_details_available=pool_details_available,
            is_recovering=is_recovering,
            recovery_progress_percent=recovery_progress,
            issues=issues,
            recommendations=recommendations,
            timestamp=timestamp,
        )

        logger.info(
            "ceph_health_retrieved",
            health=health.value,
            score=score,
            ceph_status=ceph_health_str,
            capacity_percent=f"{capacity_percent:.1f}%",
        )

        return output
