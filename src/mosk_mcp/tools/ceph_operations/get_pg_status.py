"""Get placement group health and states tool.

This module provides the get_pg_status MCP tool for retrieving
placement group health and distribution information.

Safety Level: Read-only
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from mosk_mcp.adapters.ceph import CephAdapter
from mosk_mcp.core.exceptions import ToolExecutionError
from mosk_mcp.observability.logging import get_logger
from mosk_mcp.tools.ceph_operations.models import (
    GetPGStatusOutput,
    PGStateCount,
)


if TYPE_CHECKING:
    from mosk_mcp.adapters.kubernetes import KubernetesAdapter


logger = get_logger(__name__)


# States considered healthy
HEALTHY_STATES = {"active+clean", "active"}

# States that indicate problems
PROBLEM_STATES = {
    "degraded": "Data is degraded (fewer replicas than expected)",
    "undersized": "PG has fewer copies than the pool's replication size",
    "stale": "PG has not been updated by the primary OSD",
    "incomplete": "PG is missing a necessary replica",
    "inconsistent": "Replicas are not consistent",
    "peering": "PG is establishing replication",
    "recovery_wait": "PG is waiting to start recovery",
    "backfill_wait": "PG is waiting for backfill",
    "remapped": "PG has been temporarily mapped to different OSDs",
}


def _classify_state(state: str) -> tuple[bool, str]:
    """Classify a PG state as healthy or not.

    Args:
        state: PG state string (may contain multiple states).

    Returns:
        Tuple of (is_healthy, description).
    """
    state_lower = state.lower()

    # Check if purely healthy
    if state_lower in HEALTHY_STATES:
        return True, "Healthy"

    # Check for problem states
    for problem_state, description in PROBLEM_STATES.items():
        if problem_state in state_lower:
            return False, description

    # Unknown states - assume healthy if contains "active+clean"
    if "active+clean" in state_lower:
        return True, "Healthy with additional flags"

    # Default to unhealthy for unknown
    return False, "Unknown state"


def _generate_pg_health_summary(
    total_pgs: int,
    active_clean: int,
    states: dict[str, int],
    is_healthy: bool,
    recovery_active: bool,
) -> str:
    """Generate a human-readable PG health summary.

    Args:
        total_pgs: Total number of PGs.
        active_clean: Number of active+clean PGs.
        states: PG state counts.
        is_healthy: Whether all PGs are healthy.
        recovery_active: Whether recovery is in progress.

    Returns:
        Human-readable summary string.
    """
    if is_healthy:
        return f"All {total_pgs} PGs are active+clean. Cluster is healthy."

    parts = []

    if active_clean > 0:
        parts.append(f"{active_clean}/{total_pgs} PGs active+clean")

    # Summarize problem states
    problem_count = total_pgs - active_clean
    if problem_count > 0:
        parts.append(f"{problem_count} PGs in non-optimal states")

    if recovery_active:
        parts.append("recovery in progress")

    return "; ".join(parts) + "."


def _generate_pg_recommendations(
    states: dict[str, int],
    stuck_pgs: dict[str, int],
    is_healthy: bool,
    recovery_active: bool,
    misplaced_ratio: float,
    degraded_ratio: float,
) -> list[str]:
    """Generate PG-related recommendations.

    Args:
        states: PG state counts.
        stuck_pgs: Stuck PG counts by type.
        is_healthy: Whether all PGs are healthy.
        recovery_active: Whether recovery is in progress.
        misplaced_ratio: Ratio of misplaced objects.
        degraded_ratio: Ratio of degraded objects.

    Returns:
        List of recommendations.
    """
    recommendations: list[str] = []

    if is_healthy:
        recommendations.append("PG distribution is healthy. No action required.")
        return recommendations

    # Recovery in progress
    if recovery_active:
        recommendations.append(
            "Recovery is in progress. Monitor progress and avoid additional "
            "cluster changes until complete."
        )

    # Degraded PGs
    degraded_count = sum(count for state, count in states.items() if "degraded" in state.lower())
    if degraded_count > 0:
        recommendations.append(
            f"{degraded_count} PGs are degraded. Check OSD health and ensure "
            "all OSDs are up and in."
        )

    # Stuck PGs
    if stuck_pgs:
        stuck_types = ", ".join(stuck_pgs.keys())
        recommendations.append(
            f"Some PGs are stuck in: {stuck_types}. This may indicate "
            "OSD issues or network problems."
        )

    # Stale PGs
    stale_count = sum(count for state, count in states.items() if "stale" in state.lower())
    if stale_count > 0:
        recommendations.append(
            f"{stale_count} PGs are stale. The primary OSD may be down or "
            "unreachable. Check OSD status."
        )

    # High misplaced ratio
    if misplaced_ratio > 5:
        recommendations.append(
            f"{misplaced_ratio:.1f}% of objects are misplaced. Rebalancing "
            "is in progress. Wait for completion."
        )

    # High degraded ratio
    if degraded_ratio > 1:
        recommendations.append(
            f"{degraded_ratio:.1f}% of objects are degraded. Data redundancy "
            "is reduced. Address OSD issues promptly."
        )

    # Undersized PGs
    undersized_count = sum(
        count for state, count in states.items() if "undersized" in state.lower()
    )
    if undersized_count > 0:
        recommendations.append(
            f"{undersized_count} PGs are undersized. Check pool replication "
            "settings and OSD availability."
        )

    if not recommendations:
        recommendations.append(
            "Some PGs are in non-optimal states. Monitor the situation and check OSD health."
        )

    return recommendations


async def get_pg_status(
    kubernetes_adapter: KubernetesAdapter,
    include_stuck: bool = True,
    include_recovery: bool = True,
) -> GetPGStatusOutput:
    """Get placement group health and states.

    This tool retrieves placement group status including state distribution,
    stuck PG analysis, and recovery progress.

    Safety Level: Read-only

    Args:
        kubernetes_adapter: Kubernetes adapter for cluster communication.
        include_stuck: Include stuck PG analysis.
        include_recovery: Include recovery progress information.

    Returns:
        GetPGStatusOutput with PG health information.

    Raises:
        ToolExecutionError: If PG status cannot be retrieved.

    Example:
        >>> pg_status = await get_pg_status(k8s_adapter)
        >>> print(f"Total PGs: {pg_status.total_pgs}")
        >>> print(f"Active+clean: {pg_status.active_clean}")
        >>> if not pg_status.is_healthy:
        ...     for state in pg_status.states:
        ...         if not state.is_healthy:
        ...             print(f"  {state.state}: {state.count}")
    """
    logger.info(
        "getting_pg_status",
        include_stuck=include_stuck,
        include_recovery=include_recovery,
    )

    try:
        async with CephAdapter(kubernetes_adapter) as ceph:
            # Get PG summary
            pg_summary = await ceph.get_pg_status()

            # Get recovery status if requested
            recovery_status = None
            if include_recovery:
                recovery_status = await ceph.get_recovery_status()

            # Build state counts with health classification
            states: list[PGStateCount] = []
            for state_name, count in pg_summary.states.items():
                is_healthy, _ = _classify_state(state_name)
                states.append(
                    PGStateCount(
                        state=state_name,
                        count=count,
                        is_healthy=is_healthy,
                    )
                )

            # Sort: unhealthy first, then by count descending
            states.sort(key=lambda s: (s.is_healthy, -s.count))

            # Get stuck PGs
            stuck_pgs: dict[str, int] = {}
            if include_stuck:
                stuck_pgs = pg_summary.stuck_pgs

            # Determine if recovery is active
            recovery_active = False
            if recovery_status:
                recovery_active = recovery_status.is_in_progress

            # Get misplaced and degraded ratios
            misplaced_ratio = pg_summary.misplaced_ratio
            degraded_ratio = pg_summary.degraded_ratio

            # Generate health summary
            health_summary = _generate_pg_health_summary(
                total_pgs=pg_summary.total_pgs,
                active_clean=pg_summary.active_clean,
                states=pg_summary.states,
                is_healthy=pg_summary.is_healthy,
                recovery_active=recovery_active,
            )

            # Generate recommendations
            recommendations = _generate_pg_recommendations(
                states=pg_summary.states,
                stuck_pgs=stuck_pgs,
                is_healthy=pg_summary.is_healthy,
                recovery_active=recovery_active,
                misplaced_ratio=misplaced_ratio,
                degraded_ratio=degraded_ratio,
            )

            output = GetPGStatusOutput(
                total_pgs=pg_summary.total_pgs,
                active_clean=pg_summary.active_clean,
                states=states,
                stuck_pgs=stuck_pgs,
                is_healthy=pg_summary.is_healthy,
                recovery_active=recovery_active,
                misplaced_ratio=misplaced_ratio,
                degraded_ratio=degraded_ratio,
                health_summary=health_summary,
                recommendations=recommendations,
            )

            logger.info(
                "pg_status_retrieved",
                total=pg_summary.total_pgs,
                active_clean=pg_summary.active_clean,
                is_healthy=pg_summary.is_healthy,
            )

            return output

    except Exception as e:
        logger.error("get_pg_status_failed", error=str(e))
        raise ToolExecutionError(
            message=f"Failed to get PG status: {e}",
            tool_name="get_pg_status",
            details={"error": str(e)},
        ) from e
