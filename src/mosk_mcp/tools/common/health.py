"""Health score conversion utilities.

This module provides common health scoring functions used across cluster health tools.
"""

from __future__ import annotations

from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from mosk_mcp.tools.common.enums import HealthStatus

# Capacity thresholds (from PROJECT_TRACKER.md)
# - Warning (70%): Alert operator, suggest capacity planning
# - Critical (80%): Require immediate attention
# - Emergency (85%): Trigger OSD nearfull, automated alerts
CAPACITY_WARNING_THRESHOLD = 70.0
CAPACITY_CRITICAL_THRESHOLD = 80.0
CAPACITY_EMERGENCY_THRESHOLD = 85.0


def score_to_health(score: int) -> HealthStatus:
    """Convert a numeric health score to HealthStatus enum.

    This function provides a consistent mapping from health scores to
    health status across all cluster health tools.

    Args:
        score: Health score from 0 to 100.
            - 90-100: HEALTHY
            - 70-89: DEGRADED
            - 0-69: UNHEALTHY

    Returns:
        HealthStatus enum value.

    Example:
        >>> score_to_health(95)
        HealthStatus.HEALTHY
        >>> score_to_health(75)
        HealthStatus.DEGRADED
        >>> score_to_health(50)
        HealthStatus.UNHEALTHY
    """
    # Import at runtime to avoid circular dependency
    from mosk_mcp.tools.common.enums import HealthStatus

    if score >= 90:
        return HealthStatus.HEALTHY
    if score >= 70:
        return HealthStatus.DEGRADED
    return HealthStatus.UNHEALTHY


def capacity_status(percent: float) -> str:
    """Determine capacity status from utilization percentage.

    Uses thresholds from PROJECT_TRACKER.md:
    - < 70%: OK
    - 70-80%: WARNING (alert operator, capacity planning)
    - 80-85%: CRITICAL (require immediate attention)
    - >= 85%: EMERGENCY (OSD nearfull, automated alerts)

    Args:
        percent: Capacity utilization percentage (0-100).

    Returns:
        Status string: "OK", "WARNING", "CRITICAL", or "EMERGENCY".

    Example:
        >>> capacity_status(50.0)
        'OK'
        >>> capacity_status(75.0)
        'WARNING'
        >>> capacity_status(82.0)
        'CRITICAL'
    """
    if percent >= CAPACITY_EMERGENCY_THRESHOLD:
        return "EMERGENCY"
    if percent >= CAPACITY_CRITICAL_THRESHOLD:
        return "CRITICAL"
    if percent >= CAPACITY_WARNING_THRESHOLD:
        return "WARNING"
    return "OK"
