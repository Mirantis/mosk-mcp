"""Track data recovery/rebalancing progress tool.

This module provides the get_recovery_status MCP tool for tracking
Ceph data recovery and rebalancing progress.

Safety Level: Read-only
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from mosk_mcp.adapters.ceph import CephAdapter
from mosk_mcp.core.exceptions import ToolExecutionError
from mosk_mcp.observability.logging import get_logger
from mosk_mcp.tools.ceph_operations.models import (
    GetRecoveryStatusOutput,
    RecoveryProgress,
)
from mosk_mcp.tools.common import format_bytes


if TYPE_CHECKING:
    from mosk_mcp.adapters.kubernetes import KubernetesAdapter


logger = get_logger(__name__)


def _format_duration(seconds: int | None) -> str:
    """Format seconds to human-readable duration.

    Args:
        seconds: Duration in seconds.

    Returns:
        Human-readable duration string.
    """
    if seconds is None or seconds < 0:
        return "unknown"

    if seconds < 60:
        return f"{seconds} seconds"

    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} minute{'s' if minutes != 1 else ''}"

    hours = minutes // 60
    remaining_minutes = minutes % 60
    if hours < 24:
        if remaining_minutes > 0:
            return f"{hours}h {remaining_minutes}m"
        return f"{hours} hour{'s' if hours != 1 else ''}"

    days = hours // 24
    remaining_hours = hours % 24
    if remaining_hours > 0:
        return f"{days}d {remaining_hours}h"
    return f"{days} day{'s' if days != 1 else ''}"


def _generate_status_summary(
    is_recovering: bool,
    is_backfilling: bool,
    is_rebalancing: bool,
    misplaced_ratio: float,
    degraded_ratio: float,
    recovery_progress: RecoveryProgress | None,
) -> str:
    """Generate a human-readable status summary.

    Args:
        is_recovering: Whether recovery is in progress.
        is_backfilling: Whether backfill is in progress.
        is_rebalancing: Whether rebalancing is active.
        misplaced_ratio: Ratio of misplaced objects.
        degraded_ratio: Ratio of degraded objects.
        recovery_progress: Recovery progress info.

    Returns:
        Human-readable summary string.
    """
    if not any([is_recovering, is_backfilling, is_rebalancing]):
        if misplaced_ratio == 0 and degraded_ratio == 0:
            return "No recovery or rebalancing in progress. Cluster data is stable."
        elif misplaced_ratio > 0 or degraded_ratio > 0:
            return (
                "No active recovery but some data is misplaced/degraded. "
                "Recovery may have stalled or is waiting to start."
            )

    parts = []

    if is_recovering:
        parts.append("data recovery in progress")
    if is_backfilling:
        parts.append("backfill in progress")
    if is_rebalancing:
        parts.append("rebalancing active")

    summary = ", ".join(parts).capitalize()

    # Add progress if available
    if recovery_progress and recovery_progress.percent_complete > 0:
        summary += f" ({recovery_progress.percent_complete:.1f}% complete)"

    # Add ETA if available
    if recovery_progress and recovery_progress.estimated_time_remaining != "unknown":
        summary += f". ETA: {recovery_progress.estimated_time_remaining}"

    return summary + "."


def _generate_recommendations(
    is_recovering: bool,
    is_backfilling: bool,
    is_rebalancing: bool,
    misplaced_ratio: float,
    degraded_ratio: float,
    recovery_rate_bytes_per_sec: int,
) -> list[str]:
    """Generate recommendations based on recovery status.

    Args:
        is_recovering: Whether recovery is in progress.
        is_backfilling: Whether backfill is in progress.
        is_rebalancing: Whether rebalancing is active.
        misplaced_ratio: Ratio of misplaced objects.
        degraded_ratio: Ratio of degraded objects.
        recovery_rate_bytes_per_sec: Recovery throughput.

    Returns:
        List of recommendations.
    """
    recommendations: list[str] = []

    # Active recovery
    if is_recovering or is_backfilling or is_rebalancing:
        recommendations.append(
            "Recovery/rebalancing is in progress. Avoid making cluster changes "
            "until complete to prevent additional data movement."
        )

        # Low recovery rate
        if recovery_rate_bytes_per_sec > 0 and recovery_rate_bytes_per_sec < 10 * 1024 * 1024:
            recommendations.append(
                f"Recovery rate is {format_bytes(recovery_rate_bytes_per_sec)}/s which is low. "
                "Check for network bottlenecks or OSD overload."
            )

    # High degraded ratio
    if degraded_ratio > 5:
        recommendations.append(
            f"Degraded ratio is {degraded_ratio:.1f}%. Data redundancy is reduced. "
            "Ensure all OSDs are up and healthy."
        )
    elif degraded_ratio > 1:
        recommendations.append(
            f"Degraded ratio is {degraded_ratio:.1f}%. Monitor until recovery completes."
        )

    # High misplaced ratio
    if misplaced_ratio > 10:
        recommendations.append(
            f"Misplaced ratio is {misplaced_ratio:.1f}%. Significant data movement "
            "is occurring. This may impact client performance."
        )
    elif misplaced_ratio > 5:
        recommendations.append(
            f"Misplaced ratio is {misplaced_ratio:.1f}%. Some data is being relocated."
        )

    # Stalled recovery
    if (misplaced_ratio > 0 or degraded_ratio > 0) and not (is_recovering or is_backfilling):
        recommendations.append(
            "Data is misplaced/degraded but recovery is not active. "
            "Check for stuck PGs or OSD issues."
        )

    # All good
    if not recommendations:
        recommendations.append("Cluster data distribution is healthy. No action required.")

    return recommendations


async def get_recovery_status(
    kubernetes_adapter: KubernetesAdapter,
    include_pg_details: bool = False,
    include_osd_details: bool = False,
) -> GetRecoveryStatusOutput:
    """Track data recovery/rebalancing progress.

    This tool provides detailed information about Ceph data recovery and
    rebalancing operations, including progress metrics and time estimates.

    Safety Level: Read-only

    Args:
        kubernetes_adapter: Kubernetes adapter for cluster communication.
        include_pg_details: Include per-PG recovery details (can be slow).
        include_osd_details: Include per-OSD recovery details.

    Returns:
        GetRecoveryStatusOutput with recovery progress and recommendations.

    Raises:
        ToolExecutionError: If recovery status cannot be retrieved.

    Example:
        >>> status = await get_recovery_status(k8s_adapter)
        >>> if status.is_recovering:
        ...     print(f"Recovery: {status.recovery_progress.percent_complete:.1f}%")
        ...     print(f"ETA: {status.recovery_progress.estimated_time_remaining}")
        >>> else:
        ...     print("No recovery in progress")
    """
    logger.info(
        "getting_recovery_status",
        include_pg_details=include_pg_details,
        include_osd_details=include_osd_details,
    )

    try:
        async with CephAdapter(kubernetes_adapter) as ceph:
            # Get recovery status from adapter
            recovery = await ceph.get_recovery_status()

            # Get PG status for additional context
            pg_status = await ceph.get_pg_status()

            # Calculate recovery progress
            recovery_progress: RecoveryProgress | None = None

            objects_to_recover = recovery.misplaced_objects + recovery.degraded_objects
            bytes_to_recover = recovery.recovering_bytes

            if recovery.is_in_progress and objects_to_recover > 0:
                objects_recovered = 0  # Would need historical data
                bytes_recovered = 0  # Would need historical data

                # Estimate percent complete from ratios
                total_ratio = recovery.misplaced_ratio + recovery.degraded_ratio
                percent_complete = max(0, 100 - total_ratio)

                # Estimate time remaining
                eta_str = "unknown"
                if recovery.estimated_time_remaining_seconds is not None:
                    eta_str = _format_duration(recovery.estimated_time_remaining_seconds)
                elif recovery.recovery_rate_bytes > 0 and bytes_to_recover > 0:
                    # Rough estimate
                    seconds = bytes_to_recover / recovery.recovery_rate_bytes
                    eta_str = _format_duration(int(seconds))

                recovery_progress = RecoveryProgress(
                    objects_recovered=objects_recovered,
                    objects_to_recover=objects_to_recover,
                    bytes_recovered=bytes_recovered,
                    bytes_to_recover=bytes_to_recover,
                    percent_complete=percent_complete,
                    recovery_rate_bytes_per_sec=recovery.recovery_rate_bytes,
                    estimated_time_remaining=eta_str,
                )

            # Count recovering/backfilling PGs
            pgs_recovering = 0
            pgs_backfilling = 0

            for state, count in pg_status.states.items():
                if "recovering" in state:
                    pgs_recovering += count
                if "backfilling" in state or "backfill" in state:
                    pgs_backfilling += count

            # Determine if rebalancing (misplaced but not degraded)
            is_rebalancing = (
                recovery.misplaced_ratio > 0
                and recovery.degraded_ratio == 0
                and pg_status.recovering
            )

            # Generate summary
            status_summary = _generate_status_summary(
                is_recovering=recovery.is_recovering,
                is_backfilling=recovery.is_backfilling,
                is_rebalancing=is_rebalancing,
                misplaced_ratio=recovery.misplaced_ratio,
                degraded_ratio=recovery.degraded_ratio,
                recovery_progress=recovery_progress,
            )

            # Generate recommendations
            recommendations = _generate_recommendations(
                is_recovering=recovery.is_recovering,
                is_backfilling=recovery.is_backfilling,
                is_rebalancing=is_rebalancing,
                misplaced_ratio=recovery.misplaced_ratio,
                degraded_ratio=recovery.degraded_ratio,
                recovery_rate_bytes_per_sec=recovery.recovery_rate_bytes,
            )

            output = GetRecoveryStatusOutput(
                is_recovering=recovery.is_recovering,
                is_backfilling=recovery.is_backfilling,
                is_rebalancing=is_rebalancing,
                recovery_progress=recovery_progress,
                misplaced_objects=recovery.misplaced_objects,
                misplaced_ratio=recovery.misplaced_ratio,
                degraded_objects=recovery.degraded_objects,
                degraded_ratio=recovery.degraded_ratio,
                pgs_recovering=pgs_recovering,
                pgs_backfilling=pgs_backfilling,
                status_summary=status_summary,
                recommendations=recommendations,
                timestamp=datetime.now(UTC).isoformat(),
            )

            logger.info(
                "recovery_status_retrieved",
                is_recovering=output.is_recovering,
                is_backfilling=output.is_backfilling,
                misplaced_ratio=f"{output.misplaced_ratio:.2f}%",
                degraded_ratio=f"{output.degraded_ratio:.2f}%",
            )

            return output

    except Exception as e:
        logger.error("get_recovery_status_failed", error=str(e))
        raise ToolExecutionError(
            message=f"Failed to get recovery status: {e}",
            tool_name="get_recovery_status",
            details={"error": str(e)},
        ) from e
