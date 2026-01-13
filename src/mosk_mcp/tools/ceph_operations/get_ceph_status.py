"""Get overall Ceph cluster health and status tool.

This module provides the get_ceph_status MCP tool for retrieving
comprehensive Ceph cluster health and operational status.

Safety Level: Read-only
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from mosk_mcp.adapters.ceph import (
    CAPACITY_CRITICAL_THRESHOLD,
    CAPACITY_EMERGENCY_THRESHOLD,
    CAPACITY_WARNING_THRESHOLD,
    CephAdapter,
    CephHealthStatus,
)
from mosk_mcp.core.exceptions import ToolExecutionError
from mosk_mcp.observability.logging import get_logger
from mosk_mcp.tools.ceph_operations.models import (
    CapacityStatus,
    CapacitySummary,
    CephHealthLevel,
    GetCephStatusOutput,
    HealthCheckInfo,
)
from mosk_mcp.tools.common import format_bytes


if TYPE_CHECKING:
    from mosk_mcp.adapters.kubernetes import KubernetesAdapter


logger = get_logger(__name__)


def _health_status_to_level(status: CephHealthStatus) -> CephHealthLevel:
    """Convert CephHealthStatus to CephHealthLevel.

    Args:
        status: Ceph health status.

    Returns:
        Corresponding health level enum.
    """
    mapping = {
        CephHealthStatus.HEALTH_OK: CephHealthLevel.HEALTH_OK,
        CephHealthStatus.HEALTH_WARN: CephHealthLevel.HEALTH_WARN,
        CephHealthStatus.HEALTH_ERR: CephHealthLevel.HEALTH_ERR,
        CephHealthStatus.UNKNOWN: CephHealthLevel.UNKNOWN,
    }
    return mapping.get(status, CephHealthLevel.UNKNOWN)


def _capacity_status_to_enum(status: str) -> CapacityStatus:
    """Convert capacity status string to enum.

    Args:
        status: Capacity status string.

    Returns:
        CapacityStatus enum value.
    """
    mapping = {
        "normal": CapacityStatus.NORMAL,
        "warning": CapacityStatus.WARNING,
        "critical": CapacityStatus.CRITICAL,
        "emergency": CapacityStatus.EMERGENCY,
    }
    return mapping.get(status.lower(), CapacityStatus.NORMAL)


def _generate_health_summary(
    health: CephHealthStatus,
    num_osds: int,
    num_osds_up: int,
    num_pgs: int,
    pg_states: dict[str, int],
    capacity_percent: float,
) -> str:
    """Generate a human-readable health summary.

    Args:
        health: Cluster health status.
        num_osds: Total OSDs.
        num_osds_up: OSDs that are up.
        num_pgs: Total PGs.
        pg_states: PG state counts.
        capacity_percent: Capacity utilization.

    Returns:
        Human-readable summary string.
    """
    parts = []

    # Health status
    if health == CephHealthStatus.HEALTH_OK:
        parts.append("Cluster is healthy")
    elif health == CephHealthStatus.HEALTH_WARN:
        parts.append("Cluster has warnings")
    elif health == CephHealthStatus.HEALTH_ERR:
        parts.append("Cluster has errors")
    else:
        parts.append("Cluster health unknown")

    # OSD status
    if num_osds == num_osds_up:
        parts.append(f"all {num_osds} OSDs up")
    else:
        parts.append(f"{num_osds_up}/{num_osds} OSDs up")

    # PG status
    active_clean = pg_states.get("active+clean", 0)
    if active_clean == num_pgs:
        parts.append(f"all {num_pgs} PGs active+clean")
    else:
        parts.append(f"{active_clean}/{num_pgs} PGs active+clean")

    # Capacity
    parts.append(f"{capacity_percent:.1f}% capacity used")

    return "; ".join(parts) + "."


def _generate_warnings(
    health: CephHealthStatus,
    health_checks: dict[str, Any],
    num_osds: int,
    num_osds_up: int,
    num_osds_in: int,
    capacity_percent: float,
    pg_states: dict[str, int],
) -> list[str]:
    """Generate warning messages based on cluster state.

    Args:
        health: Cluster health status.
        health_checks: Active health checks.
        num_osds: Total OSDs.
        num_osds_up: OSDs that are up.
        num_osds_in: OSDs that are in.
        capacity_percent: Capacity utilization.
        pg_states: PG state counts.

    Returns:
        List of warning messages.
    """
    warnings: list[str] = []

    # OSD warnings
    if num_osds_up < num_osds:
        down_count = num_osds - num_osds_up
        warnings.append(f"{down_count} OSD(s) are down")

    if num_osds_in < num_osds:
        out_count = num_osds - num_osds_in
        warnings.append(f"{out_count} OSD(s) are out")

    # Capacity warnings
    if capacity_percent >= CAPACITY_EMERGENCY_THRESHOLD:
        warnings.append(
            f"EMERGENCY: Capacity at {capacity_percent:.1f}% - immediate action required"
        )
    elif capacity_percent >= CAPACITY_CRITICAL_THRESHOLD:
        warnings.append(f"CRITICAL: Capacity at {capacity_percent:.1f}% - add storage soon")
    elif capacity_percent >= CAPACITY_WARNING_THRESHOLD:
        warnings.append(f"WARNING: Capacity at {capacity_percent:.1f}% - plan storage expansion")

    # PG warnings
    for state, count in pg_states.items():
        if "degraded" in state:
            warnings.append(f"{count} PGs are degraded")
        elif "undersized" in state:
            warnings.append(f"{count} PGs are undersized")
        elif "stale" in state:
            warnings.append(f"{count} PGs are stale")
        elif "recovering" in state:
            warnings.append(f"{count} PGs are recovering")

    # Health check warnings
    for check_name, check_data in health_checks.items():
        severity = check_data.get("severity", "HEALTH_WARN")
        summary = check_data.get("summary", {}).get("message", check_name)
        if severity == "HEALTH_ERR":
            warnings.insert(0, f"ERROR: {summary}")
        elif severity == "HEALTH_WARN":
            warnings.append(f"WARN: {summary}")

    return warnings


def _is_safe_for_operations(
    health: CephHealthStatus,
    num_osds: int,
    num_osds_up: int,
    capacity_percent: float,
    pg_states: dict[str, int],
) -> bool:
    """Determine if cluster is safe for maintenance operations.

    Args:
        health: Cluster health status.
        num_osds: Total OSDs.
        num_osds_up: OSDs that are up.
        capacity_percent: Capacity utilization.
        pg_states: PG state counts.

    Returns:
        True if cluster is safe for operations.
    """
    # Not safe if health is ERROR
    if health == CephHealthStatus.HEALTH_ERR:
        return False

    # Not safe if OSDs are down
    if num_osds_up < num_osds:
        return False

    # Not safe if capacity is critical or emergency
    if capacity_percent >= CAPACITY_CRITICAL_THRESHOLD:
        return False

    # Not safe if there are stuck PGs
    stuck_states = ["stale", "incomplete", "undersized"]
    for state in pg_states:
        for stuck in stuck_states:
            if stuck in state:
                return False

    return True


async def get_ceph_status(
    kubernetes_adapter: KubernetesAdapter,
    include_health_details: bool = True,
    include_pg_summary: bool = True,
) -> GetCephStatusOutput:
    """Get overall Ceph cluster health and status.

    This tool retrieves comprehensive information about the Ceph cluster
    including health status, OSD state, PG distribution, and capacity.

    Safety Level: Read-only

    Args:
        kubernetes_adapter: Kubernetes adapter for cluster communication.
        include_health_details: Include detailed health check information.
        include_pg_summary: Include placement group summary.

    Returns:
        GetCephStatusOutput with cluster status information.

    Raises:
        ToolExecutionError: If status cannot be retrieved.

    Example:
        >>> status = await get_ceph_status(k8s_adapter)
        >>> print(f"Health: {status.health}")
        >>> print(f"Capacity: {status.capacity.percent_used:.1f}%")
    """
    logger.info("getting_ceph_status", include_health_details=include_health_details)

    try:
        async with CephAdapter(kubernetes_adapter) as ceph:
            # Get cluster status
            cluster_status = await ceph.get_cluster_status()

            # Build health checks dictionary
            health_checks: dict[str, HealthCheckInfo] = {}
            if include_health_details:
                for check_name, check_data in cluster_status.health_checks.items():
                    health_checks[check_name] = HealthCheckInfo(
                        severity=check_data.get("severity", "HEALTH_WARN"),
                        message=check_data.get("summary", {}).get("message", check_name),
                        count=check_data.get("count", 1),
                    )

            # Build PG summary
            pg_summary: dict[str, int] = {}
            if include_pg_summary:
                pg_summary = cluster_status.pg_states

            # Build capacity summary
            capacity_status = _capacity_status_to_enum(cluster_status.capacity_status)
            capacity = CapacitySummary(
                total_bytes=cluster_status.total_bytes,
                used_bytes=cluster_status.used_bytes,
                available_bytes=cluster_status.available_bytes,
                percent_used=cluster_status.capacity_percent,
                status=capacity_status,
                total_human=format_bytes(cluster_status.total_bytes),
                used_human=format_bytes(cluster_status.used_bytes),
                available_human=format_bytes(cluster_status.available_bytes),
            )

            # Generate health summary
            health_summary = _generate_health_summary(
                health=cluster_status.health,
                num_osds=cluster_status.num_osds,
                num_osds_up=cluster_status.num_osds_up,
                num_pgs=cluster_status.num_pgs,
                pg_states=cluster_status.pg_states,
                capacity_percent=cluster_status.capacity_percent,
            )

            # Generate warnings
            warnings = _generate_warnings(
                health=cluster_status.health,
                health_checks=cluster_status.health_checks,
                num_osds=cluster_status.num_osds,
                num_osds_up=cluster_status.num_osds_up,
                num_osds_in=cluster_status.num_osds_in,
                capacity_percent=cluster_status.capacity_percent,
                pg_states=cluster_status.pg_states,
            )

            # Check if safe for operations
            is_safe = _is_safe_for_operations(
                health=cluster_status.health,
                num_osds=cluster_status.num_osds,
                num_osds_up=cluster_status.num_osds_up,
                capacity_percent=cluster_status.capacity_percent,
                pg_states=cluster_status.pg_states,
            )

            output = GetCephStatusOutput(
                health=_health_status_to_level(cluster_status.health),
                health_summary=health_summary,
                health_checks=health_checks,
                fsid=cluster_status.fsid,
                quorum=cluster_status.quorum,
                num_osds=cluster_status.num_osds,
                num_osds_up=cluster_status.num_osds_up,
                num_osds_in=cluster_status.num_osds_in,
                num_pgs=cluster_status.num_pgs,
                pg_summary=pg_summary,
                capacity=capacity,
                is_healthy=cluster_status.is_healthy,
                is_safe_for_operations=is_safe,
                warnings=warnings,
                timestamp=cluster_status.timestamp.isoformat(),
            )

            logger.info(
                "ceph_status_retrieved",
                health=output.health.value,
                is_healthy=output.is_healthy,
                capacity_percent=f"{output.capacity.percent_used:.1f}%",
            )

            return output

    except Exception as e:
        logger.error("get_ceph_status_failed", error=str(e))
        raise ToolExecutionError(
            message=f"Failed to get Ceph status: {e}",
            tool_name="get_ceph_status",
            details={"error": str(e)},
        ) from e
