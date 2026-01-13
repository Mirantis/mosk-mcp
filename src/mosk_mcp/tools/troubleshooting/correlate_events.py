"""Correlate events tool for intelligent troubleshooting.

This tool finds related events across logs, alerts, and metrics
within a time window, helping identify root causes of issues.

Safety Level: Read-only

This tool queries StackLight via OIDC/SSO authentication using
DirectStackLightClient. Authentication must be established before
calling this tool.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from mosk_mcp.adapters.stacklight import (
    AlertState,
    DirectStackLightClient,
    StackLightAdapter,
)
from mosk_mcp.adapters.stacklight import (
    LogSeverity as AdapterLogSeverity,
)
from mosk_mcp.core.exceptions import ToolExecutionError, ValidationError
from mosk_mcp.observability.logging import get_logger
from mosk_mcp.tools.troubleshooting.known_issues import get_known_issue_database
from mosk_mcp.tools.troubleshooting.models import (
    CorrelatedEvent,
    CorrelateEventsOutput,
    EventCluster,
    LogSeverity,
)


logger = get_logger(__name__)


def _severity_to_str(severity: AdapterLogSeverity) -> str:
    """Convert adapter severity to string."""
    return severity.value


def _meets_min_severity(
    severity: AdapterLogSeverity,
    min_severity: LogSeverity,
) -> bool:
    """Check if severity meets minimum threshold."""
    severity_order = {
        "debug": 0,
        "info": 1,
        "warning": 2,
        "error": 3,
        "critical": 4,
    }
    return severity_order.get(severity.value, 0) >= severity_order.get(min_severity.value, 0)


async def correlate_events(
    direct_client: DirectStackLightClient,
    anchor_time: str | None = None,
    window_minutes_before: int = 15,
    window_minutes_after: int = 15,
    services: list[str] | None = None,
    min_severity: LogSeverity = LogSeverity.WARNING,
    include_alerts: bool = True,
    include_metrics: bool = False,
) -> CorrelateEventsOutput:
    """Find related events across logs and alerts within a time window.

    This tool correlates events from different sources (logs, alerts, metrics)
    around a specific time point via OIDC/SSO authentication to help identify
    patterns and root causes. Events are clustered by similarity and service
    relationships.

    The direct_client must be authenticated with valid Keycloak tokens
    before calling this tool.

    Safety Level: Read-only

    Args:
        direct_client: Authenticated DirectStackLightClient for StackLight access.
        anchor_time: Central time point for correlation (ISO format, default: now).
        window_minutes_before: Minutes before anchor time to search (default: 15).
        window_minutes_after: Minutes after anchor time to search (default: 15).
        services: Services to include (default: all).
        min_severity: Minimum severity to include (default: WARNING).
        include_alerts: Include alerts in correlation (default: True).
        include_metrics: Include metric anomalies (default: False).

    Returns:
        CorrelateEventsOutput with correlated events, clusters, and analysis.

    Raises:
        ValidationError: If input parameters are invalid.
        ToolExecutionError: If correlation fails.

    Example:
        >>> result = await correlate_events(client, window_minutes_before=30)

        >>> result = await correlate_events(
        ...     client,
        ...     anchor_time="2024-01-15T10:30:00Z",
        ...     window_minutes_before=15,
        ...     window_minutes_after=15,
        ...     services=["nova", "cinder"],
        ... )
    """
    logger.info(
        "correlate_events_started",
        anchor_time=anchor_time,
        window_before=window_minutes_before,
        window_after=window_minutes_after,
        services=services,
    )

    # Validate inputs
    if window_minutes_before < 1 or window_minutes_before > 120:
        raise ValidationError(
            "window_minutes_before must be between 1 and 120",
            field="window_minutes_before",
            value=window_minutes_before,
        )

    if window_minutes_after < 0 or window_minutes_after > 120:
        raise ValidationError(
            "window_minutes_after must be between 0 and 120",
            field="window_minutes_after",
            value=window_minutes_after,
        )

    try:
        # Parse anchor time
        if anchor_time:
            try:
                anchor_dt = datetime.fromisoformat(anchor_time.replace("Z", "+00:00"))
            except ValueError as e:
                raise ValidationError(
                    f"Invalid anchor_time format: {anchor_time}",
                    field="anchor_time",
                    value=anchor_time,
                ) from e
        else:
            anchor_dt = datetime.now(UTC)

        # Calculate time window
        window_start = anchor_dt - timedelta(minutes=window_minutes_before)
        window_end = anchor_dt + timedelta(minutes=window_minutes_after)
        total_minutes = window_minutes_before + window_minutes_after

        # Create StackLight adapter with direct client
        stacklight = StackLightAdapter(direct_client=direct_client)
        await stacklight.connect()

        # Collect events
        events: list[CorrelatedEvent] = []

        # Query logs - extract .logs from LogQueryResult
        log_result = await stacklight.query_logs(
            services=services,
            time_range_minutes=total_minutes,
            limit=500,
        )

        for log in log_result.logs:
            # Filter by minimum severity
            if not _meets_min_severity(log.severity, min_severity):
                continue

            # Filter by time window
            if log.timestamp < window_start or log.timestamp > window_end:
                continue

            # Calculate relative time from anchor
            relative_seconds = int((log.timestamp - anchor_dt).total_seconds())

            event = CorrelatedEvent(
                event_type="log",
                timestamp=log.timestamp.isoformat(),
                relative_seconds=relative_seconds,
                service=log.service,
                severity=log.severity.value,
                message=log.message,
                host=log.host,
                correlation_score=0.0,  # Will be calculated
                related_events=[],
            )
            events.append(event)

        # Query alerts if requested
        if include_alerts:
            alerts = await stacklight.get_alerts(
                state=AlertState.FIRING,
                limit=100,
            )

            for alert in alerts:
                if alert.starts_at and alert.starts_at < window_start:
                    continue
                if alert.starts_at and alert.starts_at > window_end:
                    continue

                relative_seconds = 0
                if alert.starts_at:
                    relative_seconds = int((alert.starts_at - anchor_dt).total_seconds())

                service = alert.labels.get("service", "unknown")
                if services and service not in services:
                    continue

                event = CorrelatedEvent(
                    event_type="alert",
                    timestamp=alert.starts_at.isoformat()
                    if alert.starts_at
                    else anchor_dt.isoformat(),
                    relative_seconds=relative_seconds,
                    service=service,
                    severity=alert.severity.value,
                    message=alert.summary,
                    host=alert.labels.get("host"),
                    correlation_score=0.5,  # Alerts are inherently correlated
                    related_events=[],
                )
                events.append(event)

        # Sort events by timestamp
        events.sort(key=lambda e: e.timestamp)

        # Calculate correlation scores based on temporal proximity and service relationships
        _calculate_correlation_scores(events, anchor_dt)

        # Cluster events by service and temporal proximity
        clusters = _cluster_events(events)

        # Generate timeline summary
        timeline_summary = _generate_timeline_summary(events, anchor_dt)

        # Try to identify root cause using known issues
        likely_root_cause = None
        recommendations = []

        if events:
            # Extract error messages for pattern matching
            error_messages = [e.message for e in events if e.severity in ["error", "critical"]]

            # Check known issues database
            db = get_known_issue_database()
            if error_messages:
                best_match = db.find_best_match(
                    error_message=error_messages[0] if error_messages else None,
                    log_messages=error_messages,
                )
                if best_match and best_match[1] > 0.3:
                    issue, score = best_match
                    likely_root_cause = f"{issue.issue_id}: {issue.title} (confidence: {score:.0%})"
                    recommendations.append(f"Check known issue {issue.issue_id}")

        # Add general recommendations
        if not recommendations:
            if any(e.severity == "critical" for e in events):
                recommendations.append("Critical events detected - prioritize investigation")
            if len({e.service for e in events}) > 3:
                recommendations.append(
                    "Multiple services affected - check shared dependencies (RabbitMQ, DB)"
                )
            if not events:
                recommendations.append(
                    "No significant events in time window - consider expanding search"
                )

        result = CorrelateEventsOutput(
            anchor_time=anchor_dt.isoformat(),
            window_start=window_start.isoformat(),
            window_end=window_end.isoformat(),
            total_events=len(events),
            events=events,
            clusters=clusters,
            timeline_summary=timeline_summary,
            likely_root_cause=likely_root_cause,
            recommendations=recommendations,
            timestamp=datetime.now(UTC).isoformat(),
        )

        logger.info(
            "correlate_events_completed",
            total_events=len(events),
            clusters=len(clusters),
        )

        return result

    except ValidationError:
        raise
    except Exception as e:
        logger.error(
            "correlate_events_failed",
            error=str(e),
        )
        raise ToolExecutionError(
            f"Failed to correlate events: {e}",
            tool_name="correlate_events",
            phase="execution",
        ) from e


def _calculate_correlation_scores(
    events: list[CorrelatedEvent],
    anchor_dt: datetime,
) -> None:
    """Calculate correlation scores for events based on proximity to anchor.

    Events closer to the anchor time get higher scores.
    """
    max_seconds = 900  # 15 minutes

    for event in events:
        # Time-based score (closer to anchor = higher score)
        abs_seconds = abs(event.relative_seconds)
        time_score = max(0, 1 - (abs_seconds / max_seconds))

        # Severity boost
        severity_boost = {
            "critical": 0.3,
            "error": 0.2,
            "warning": 0.1,
            "info": 0.0,
            "debug": 0.0,
        }
        boost = severity_boost.get(event.severity, 0.0)

        # Alert boost (alerts are more significant)
        if event.event_type == "alert":
            boost += 0.2

        event.correlation_score = min(1.0, time_score + boost)


def _cluster_events(events: list[CorrelatedEvent]) -> list[EventCluster]:
    """Cluster events by service and temporal proximity."""
    if not events:
        return []

    # Group events by service
    by_service: dict[str, list[CorrelatedEvent]] = {}
    for event in events:
        if event.service not in by_service:
            by_service[event.service] = []
        by_service[event.service].append(event)

    clusters: list[EventCluster] = []

    for service, service_events in by_service.items():
        if not service_events:
            continue

        # Calculate time span
        timestamps = [e.relative_seconds for e in service_events]
        time_span = max(timestamps) - min(timestamps) if len(timestamps) > 1 else 0

        # Try to identify likely cause from error patterns
        likely_cause = None
        error_events = [e for e in service_events if e.severity in ["error", "critical"]]
        if error_events:
            # Use first error as likely cause indicator
            likely_cause = error_events[0].message[:100]

        cluster = EventCluster(
            cluster_id=f"cluster-{service}-{uuid.uuid4().hex[:8]}",
            primary_service=service,
            event_count=len(service_events),
            time_span_seconds=abs(time_span),
            events=service_events,
            likely_cause=likely_cause,
            confidence=sum(e.correlation_score for e in service_events) / len(service_events),
        )
        clusters.append(cluster)

    # Sort by event count descending
    clusters.sort(key=lambda c: c.event_count, reverse=True)

    return clusters


def _generate_timeline_summary(
    events: list[CorrelatedEvent],
    anchor_dt: datetime,
) -> list[str]:
    """Generate human-readable timeline summary."""
    if not events:
        return ["No events found in the specified time window."]

    summary: list[str] = []

    # Count by type and severity
    log_count = sum(1 for e in events if e.event_type == "log")
    alert_count = sum(1 for e in events if e.event_type == "alert")
    error_count = sum(1 for e in events if e.severity in ["error", "critical"])

    summary.append(f"Found {len(events)} events: {log_count} logs, {alert_count} alerts")

    if error_count > 0:
        summary.append(f"  - {error_count} error/critical events")

    # Services involved
    services = {e.service for e in events}
    summary.append(f"Services involved: {', '.join(sorted(services))}")

    # Time distribution
    before = sum(1 for e in events if e.relative_seconds < 0)
    after = sum(1 for e in events if e.relative_seconds > 0)
    at_anchor = sum(1 for e in events if e.relative_seconds == 0)

    summary.append(f"Timeline: {before} before, {at_anchor} at, {after} after anchor")

    # First error
    errors = [e for e in events if e.severity in ["error", "critical"]]
    if errors:
        first_error = min(errors, key=lambda e: e.relative_seconds)
        summary.append(
            f"First error: {first_error.service} at {first_error.relative_seconds}s - "
            f"{first_error.message[:50]}..."
        )

    return summary


# Tool metadata for registration
TOOL_NAME = "correlate_events"
TOOL_DESCRIPTION = """Find related events across logs and alerts within a time window.

Correlates events from different sources around a specific time point to help
identify patterns and root causes. Events are clustered by similarity and
service relationships.

This is a READ-ONLY tool that does not modify any resources."""

TOOL_TAGS = ["troubleshooting", "correlation", "observability", "read-only"]
