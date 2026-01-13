"""Capacity forecasting based on growth trends tool.

This module provides the predict_capacity MCP tool for forecasting
future storage capacity based on historical growth trends.

Safety Level: Read-only
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from mosk_mcp.adapters.ceph import (
    CAPACITY_CRITICAL_THRESHOLD,
    CAPACITY_EMERGENCY_THRESHOLD,
    CAPACITY_WARNING_THRESHOLD,
    CephAdapter,
)
from mosk_mcp.core.exceptions import ToolExecutionError
from mosk_mcp.observability.logging import get_logger
from mosk_mcp.tools.ceph_operations.models import (
    CapacityForecast,
    CapacityStatus,
    PredictCapacityOutput,
)
from mosk_mcp.tools.common import format_bytes


if TYPE_CHECKING:
    from mosk_mcp.adapters.kubernetes import KubernetesAdapter


logger = get_logger(__name__)


# Default growth rate assumptions (for when historical data is unavailable)
DEFAULT_GROWTH_RATE_PERCENT_PER_DAY = 0.5  # 0.5% per day = ~15% per month


def _format_duration(days: int) -> str:
    """Format days to human-readable duration.

    Args:
        days: Number of days.

    Returns:
        Human-readable duration (e.g., "2 months").
    """
    if days < 0:
        return "unknown"
    if days == 0:
        return "today"
    if days == 1:
        return "1 day"
    if days < 7:
        return f"{days} days"
    if days < 30:
        weeks = days // 7
        return f"{weeks} week{'s' if weeks > 1 else ''}"
    if days < 365:
        months = days // 30
        return f"{months} month{'s' if months > 1 else ''}"

    years = days // 365
    remaining_months = (days % 365) // 30
    if remaining_months > 0:
        return f"{years} year{'s' if years > 1 else ''}, {remaining_months} month{'s' if remaining_months > 1 else ''}"
    return f"{years} year{'s' if years > 1 else ''}"


def _get_capacity_status(percent_used: float) -> CapacityStatus:
    """Determine capacity status from utilization percentage.

    Args:
        percent_used: Utilization percentage.

    Returns:
        CapacityStatus enum value.
    """
    if percent_used >= 100:
        return CapacityStatus.EMERGENCY
    if percent_used >= CAPACITY_EMERGENCY_THRESHOLD:
        return CapacityStatus.EMERGENCY
    if percent_used >= CAPACITY_CRITICAL_THRESHOLD:
        return CapacityStatus.CRITICAL
    if percent_used >= CAPACITY_WARNING_THRESHOLD:
        return CapacityStatus.WARNING
    return CapacityStatus.NORMAL


def _calculate_days_until_threshold(
    current_percent: float,
    threshold: float,
    growth_rate_percent_per_day: float,
) -> int | None:
    """Calculate days until a threshold is reached.

    Args:
        current_percent: Current utilization percentage.
        threshold: Target threshold percentage.
        growth_rate_percent_per_day: Daily growth rate percentage.

    Returns:
        Days until threshold, or None if already exceeded or won't be reached.
    """
    if current_percent >= threshold:
        return 0  # Already exceeded

    if growth_rate_percent_per_day <= 0:
        return None  # Will never reach if not growing

    days = (threshold - current_percent) / growth_rate_percent_per_day
    return max(1, int(days))


def _generate_forecasts(
    current_used_bytes: int,
    total_bytes: int,
    growth_rate_bytes_per_day: int,
    days_to_forecast: int,
) -> list[CapacityForecast]:
    """Generate capacity forecasts for future dates.

    Args:
        current_used_bytes: Current used storage.
        total_bytes: Total storage capacity.
        growth_rate_bytes_per_day: Growth rate in bytes per day.
        days_to_forecast: Number of days to forecast.

    Returns:
        List of CapacityForecast objects.
    """
    forecasts: list[CapacityForecast] = []
    now = datetime.now(UTC)

    # Generate forecasts at key intervals
    intervals: list[int] = []

    # Add daily forecasts for first week
    intervals.extend(range(1, min(8, days_to_forecast + 1)))

    # Add weekly forecasts for first month
    if days_to_forecast > 7:
        intervals.extend(range(14, min(32, days_to_forecast + 1), 7))

    # Add monthly forecasts beyond
    if days_to_forecast > 30:
        for month in range(2, (days_to_forecast // 30) + 2):
            day = month * 30
            if day <= days_to_forecast:
                intervals.append(day)

    # Ensure the final day is included
    if days_to_forecast not in intervals:
        intervals.append(days_to_forecast)

    # Remove duplicates and sort
    intervals = sorted(set(intervals))

    for days in intervals:
        forecast_date = now + timedelta(days=days)
        predicted_used = current_used_bytes + (growth_rate_bytes_per_day * days)

        # Cap at total capacity
        predicted_used = min(predicted_used, total_bytes)

        predicted_percent = (predicted_used / total_bytes * 100) if total_bytes > 0 else 0.0
        predicted_status = _get_capacity_status(predicted_percent)

        forecasts.append(
            CapacityForecast(
                date=forecast_date.strftime("%Y-%m-%d"),
                days_from_now=days,
                predicted_used_bytes=int(predicted_used),
                predicted_percent_used=round(predicted_percent, 2),
                predicted_status=predicted_status,
            )
        )

    return forecasts


def _generate_capacity_recommendations(
    current_percent: float,
    days_until_warning: int | None,
    days_until_critical: int | None,
    days_until_full: int | None,
    growth_rate_bytes_per_day: int,
    total_bytes: int,
) -> list[str]:
    """Generate capacity planning recommendations.

    Args:
        current_percent: Current utilization percentage.
        days_until_warning: Days until warning threshold.
        days_until_critical: Days until critical threshold.
        days_until_full: Days until storage is full.
        growth_rate_bytes_per_day: Growth rate in bytes per day.
        total_bytes: Total storage capacity.

    Returns:
        List of recommendations.
    """
    recommendations: list[str] = []

    # Already critical
    if current_percent >= CAPACITY_CRITICAL_THRESHOLD:
        recommendations.append(
            "URGENT: Storage is already at critical levels. "
            "Add capacity immediately or archive/delete data."
        )
        return recommendations

    # Already warning
    if current_percent >= CAPACITY_WARNING_THRESHOLD:
        recommendations.append("Storage is at warning levels. Begin capacity expansion planning.")

    # Approaching warning soon
    if days_until_warning is not None and days_until_warning <= 14:
        recommendations.append(
            f"Storage will reach warning threshold in ~{_format_duration(days_until_warning)}. "
            "Plan capacity expansion now."
        )
    elif days_until_warning is not None and days_until_warning <= 30:
        recommendations.append(
            f"Storage will reach warning threshold in ~{_format_duration(days_until_warning)}. "
            "Begin procurement process for additional storage."
        )

    # Approaching critical soon
    if days_until_critical is not None and days_until_critical <= 30:
        recommendations.append(
            f"Storage will reach critical threshold in ~{_format_duration(days_until_critical)}. "
            "Expedite capacity expansion."
        )

    # Calculate how much to add
    if days_until_critical is not None and days_until_critical <= 90:
        # Recommend enough storage for 6 more months
        growth_for_6_months = growth_rate_bytes_per_day * 180
        recommendations.append(
            f"Consider adding at least {format_bytes(growth_for_6_months)} "
            "to provide 6 months of growth runway."
        )

    # No growth detected
    if growth_rate_bytes_per_day <= 0:
        recommendations.append(
            "No storage growth detected. Forecast is based on current usage levels."
        )

    # Healthy state
    if not recommendations:
        recommendations.append(
            f"Storage growth rate is {format_bytes(growth_rate_bytes_per_day)}/day. "
            "Continue monitoring trends."
        )

        if days_until_warning is not None:
            recommendations.append(
                f"At current growth rate, warning threshold will be reached "
                f"in ~{_format_duration(days_until_warning)}."
            )

    return recommendations


def _determine_confidence(
    has_historical_data: bool,
    days_to_forecast: int,
    growth_rate_provided: bool,
) -> str:
    """Determine forecast confidence level.

    Args:
        has_historical_data: Whether historical data was available.
        days_to_forecast: Number of days in forecast.
        growth_rate_provided: Whether growth rate was manually provided.

    Returns:
        Confidence level string.
    """
    if growth_rate_provided:
        return "Medium (user-provided growth rate)"

    if not has_historical_data:
        return "Low (using estimated growth rate - no historical data)"

    if days_to_forecast <= 7:
        return "High (short-term forecast)"
    if days_to_forecast <= 30:
        return "Medium (monthly forecast)"
    if days_to_forecast <= 90:
        return "Medium-Low (quarterly forecast)"

    return "Low (long-term forecast - subject to change)"


async def predict_capacity(
    kubernetes_adapter: KubernetesAdapter,
    days_to_forecast: int = 30,
    growth_rate_gb_per_day: float | None = None,
    include_recommendations: bool = True,
) -> PredictCapacityOutput:
    """Predict future storage capacity based on growth trends.

    This tool forecasts storage capacity usage based on current utilization
    and growth trends. It provides predictions for key dates and alerts
    when capacity thresholds will be reached.

    Safety Level: Read-only

    Args:
        kubernetes_adapter: Kubernetes adapter for cluster communication.
        days_to_forecast: Number of days to forecast (1-365).
        growth_rate_gb_per_day: Override growth rate in GB/day. If not provided,
            the tool will attempt to estimate based on available data.
        include_recommendations: Include capacity planning recommendations.

    Returns:
        PredictCapacityOutput with capacity forecasts and recommendations.

    Raises:
        ToolExecutionError: If prediction cannot be generated.

    Example:
        >>> forecast = await predict_capacity(k8s_adapter, days_to_forecast=90)
        >>> print(f"Days until warning: {forecast.days_until_warning}")
        >>> for f in forecast.forecasts:
        ...     print(f"{f.date}: {f.predicted_percent_used:.1f}%")
    """
    logger.info(
        "predicting_capacity",
        days_to_forecast=days_to_forecast,
        growth_rate_override=growth_rate_gb_per_day,
    )

    try:
        async with CephAdapter(kubernetes_adapter) as ceph:
            # Get current capacity
            capacity_data = await ceph.get_capacity()

            total_bytes = capacity_data.get("total_bytes", 0)
            used_bytes = capacity_data.get("used_bytes", 0)
            current_percent = capacity_data.get("capacity_percent", 0.0)

            # Determine growth rate
            growth_rate_provided = growth_rate_gb_per_day is not None
            # Historical data would require Prometheus integration for accurate trends
            has_historical_data = False

            if growth_rate_gb_per_day is not None:
                # Use provided rate
                growth_rate_bytes_per_day = int(growth_rate_gb_per_day * 1024 * 1024 * 1024)
            else:
                # Use default growth rate assumption when no rate is provided
                # Users can provide explicit growth_rate_gb_per_day for accurate predictions
                growth_rate_bytes_per_day = int(
                    total_bytes * (DEFAULT_GROWTH_RATE_PERCENT_PER_DAY / 100)
                )

            # Calculate growth rate as percentage
            growth_rate_percent_per_day = (
                (growth_rate_bytes_per_day / total_bytes * 100) if total_bytes > 0 else 0.0
            )

            # Calculate days until thresholds
            days_until_warning = _calculate_days_until_threshold(
                current_percent=current_percent,
                threshold=CAPACITY_WARNING_THRESHOLD,
                growth_rate_percent_per_day=growth_rate_percent_per_day,
            )

            days_until_critical = _calculate_days_until_threshold(
                current_percent=current_percent,
                threshold=CAPACITY_CRITICAL_THRESHOLD,
                growth_rate_percent_per_day=growth_rate_percent_per_day,
            )

            days_until_full = _calculate_days_until_threshold(
                current_percent=current_percent,
                threshold=100.0,
                growth_rate_percent_per_day=growth_rate_percent_per_day,
            )

            # Generate forecasts
            forecasts = _generate_forecasts(
                current_used_bytes=used_bytes,
                total_bytes=total_bytes,
                growth_rate_bytes_per_day=growth_rate_bytes_per_day,
                days_to_forecast=days_to_forecast,
            )

            # Generate recommendations
            recommendations: list[str] = []
            if include_recommendations:
                recommendations = _generate_capacity_recommendations(
                    current_percent=current_percent,
                    days_until_warning=days_until_warning,
                    days_until_critical=days_until_critical,
                    days_until_full=days_until_full,
                    growth_rate_bytes_per_day=growth_rate_bytes_per_day,
                    total_bytes=total_bytes,
                )

            # Determine confidence
            confidence = _determine_confidence(
                has_historical_data=has_historical_data,
                days_to_forecast=days_to_forecast,
                growth_rate_provided=growth_rate_provided,
            )

            output = PredictCapacityOutput(
                current_used_bytes=used_bytes,
                current_percent_used=current_percent,
                growth_rate_bytes_per_day=growth_rate_bytes_per_day,
                growth_rate_human=f"{format_bytes(growth_rate_bytes_per_day)}/day",
                forecasts=forecasts,
                days_until_warning=days_until_warning,
                days_until_critical=days_until_critical,
                days_until_full=days_until_full,
                recommendations=recommendations,
                confidence=confidence,
            )

            logger.info(
                "capacity_predicted",
                days_to_forecast=days_to_forecast,
                current_percent=f"{current_percent:.1f}%",
                days_until_warning=days_until_warning,
                days_until_critical=days_until_critical,
            )

            return output

    except Exception as e:
        logger.error("predict_capacity_failed", error=str(e))
        raise ToolExecutionError(
            message=f"Failed to predict capacity: {e}",
            tool_name="predict_capacity",
            details={"error": str(e)},
        ) from e
