"""Trace request tool for intelligent troubleshooting.

This tool traces an OpenStack request across services using
correlation IDs to understand the full request lifecycle.

Safety Level: Read-only

This tool queries StackLight via OIDC/SSO authentication using
DirectStackLightClient. Authentication must be established before
calling this tool.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from mosk_mcp.adapters.stacklight import DirectStackLightClient, StackLightAdapter
from mosk_mcp.core.exceptions import ToolExecutionError, ValidationError
from mosk_mcp.observability.logging import get_logger
from mosk_mcp.tools.troubleshooting.models import (
    LogEntryInfo,
    LogSeverity,
    TraceRequestOutput,
    TraceSpan,
)


logger = get_logger(__name__)


async def trace_request(
    direct_client: DirectStackLightClient,
    request_id: str,
    time_range_minutes: int = 60,
    include_metrics: bool = False,
) -> TraceRequestOutput:
    """Trace an OpenStack request across services using correlation ID.

    This tool follows a request as it flows through different OpenStack
    services (Keystone -> Nova -> Neutron -> Cinder, etc.) using the
    X-Request-ID correlation header via OIDC/SSO authentication.

    The direct_client must be authenticated with valid Keycloak tokens
    before calling this tool.

    Safety Level: Read-only

    Args:
        direct_client: Authenticated DirectStackLightClient for StackLight access.
        request_id: Request/correlation ID to trace (X-Request-ID format).
        time_range_minutes: Time range to search for logs (default: 60).
        include_metrics: Include associated metrics (default: False).

    Returns:
        TraceRequestOutput with request trace, spans, and analysis.

    Raises:
        ValidationError: If request_id format is invalid.
        ToolExecutionError: If tracing fails.

    Example:
        >>> result = await trace_request(
        ...     client,
        ...     request_id="req-abc123-def456-789",
        ...     time_range_minutes=120,
        ... )
    """
    logger.info(
        "trace_request_started",
        request_id=request_id,
        time_range_minutes=time_range_minutes,
    )

    # Validate request_id format
    if not request_id or len(request_id) < 8:
        raise ValidationError(
            "request_id must be at least 8 characters",
            field="request_id",
            value=request_id,
        )

    if time_range_minutes < 1 or time_range_minutes > 1440:
        raise ValidationError(
            "time_range_minutes must be between 1 and 1440",
            field="time_range_minutes",
            value=time_range_minutes,
        )

    try:
        # Create StackLight adapter with direct client
        stacklight = StackLightAdapter(direct_client=direct_client)
        await stacklight.connect()

        # Get logs for this request ID
        logs = await stacklight.get_logs_by_request_id(
            request_id=request_id,
            time_range_minutes=time_range_minutes,
            limit=500,
        )

        if not logs:
            return TraceRequestOutput(
                request_id=request_id,
                found=False,
                start_time=None,
                end_time=None,
                total_duration_ms=None,
                status="not_found",
                services_involved=[],
                spans=[],
                error_span=None,
                bottleneck_span=None,
                trace_summary=f"No trace found for request ID: {request_id}",
                recommendations=[
                    "Verify the request ID is correct",
                    f"Try expanding the time range (currently {time_range_minutes} minutes)",
                    "Check if logging is enabled for the services",
                ],
                timestamp=datetime.now(UTC).isoformat(),
            )

        # Build spans from logs
        spans: list[TraceSpan] = []
        services_seen: set[str] = set()

        # Group logs by service to create spans
        current_span: dict[str, Any] | None = None
        span_logs: list[LogEntryInfo] = []

        for log in logs:
            services_seen.add(log.service)

            log_info = LogEntryInfo(
                timestamp=log.timestamp.isoformat(),
                message=log.message,
                severity=LogSeverity(log.severity.value),
                service=log.service,
                host=log.host,
                request_id=log.request_id,
                namespace=log.namespace,
            )

            # Check if this is a new service span
            if current_span is None or current_span["service"] != log.service:
                # Finish previous span
                if current_span is not None:
                    current_span["logs"] = span_logs
                    spans.append(_create_span(current_span))

                # Start new span
                current_span = {
                    "service": log.service,
                    "start_time": log.timestamp,
                    "end_time": log.timestamp,
                    "host": log.host,
                    "status": "success",
                    "error_message": None,
                }
                span_logs = [log_info]
            else:
                # Extend current span
                current_span["end_time"] = log.timestamp
                span_logs.append(log_info)

                # Check for errors
                if log.severity.value in ["error", "critical"]:
                    current_span["status"] = "error"
                    if not current_span["error_message"]:
                        current_span["error_message"] = log.message

        # Don't forget the last span
        if current_span is not None:
            current_span["logs"] = span_logs
            spans.append(_create_span(current_span))

        # Calculate overall timing
        start_time = logs[0].timestamp if logs else None
        end_time = logs[-1].timestamp if logs else None
        total_duration_ms = None
        if start_time and end_time:
            total_duration_ms = (end_time - start_time).total_seconds() * 1000

        # Determine overall status
        status = "success"
        error_span: TraceSpan | None = None
        for span in spans:
            if span.status == "error":
                status = "error"
                if error_span is None:
                    error_span = span
                break

        # Find bottleneck (slowest span)
        bottleneck_span: TraceSpan | None = None
        if spans:
            bottleneck_span = max(spans, key=lambda s: s.duration_ms)

        # Generate trace summary
        trace_summary = _generate_trace_summary(
            request_id=request_id,
            services=list(services_seen),
            spans=spans,
            total_duration_ms=total_duration_ms,
            status=status,
        )

        # Generate recommendations
        recommendations = _generate_recommendations(spans, error_span, bottleneck_span)

        result = TraceRequestOutput(
            request_id=request_id,
            found=True,
            start_time=start_time.isoformat() if start_time else None,
            end_time=end_time.isoformat() if end_time else None,
            total_duration_ms=total_duration_ms,
            status=status,
            services_involved=sorted(services_seen),
            spans=spans,
            error_span=error_span,
            bottleneck_span=bottleneck_span,
            trace_summary=trace_summary,
            recommendations=recommendations,
            timestamp=datetime.now(UTC).isoformat(),
        )

        logger.info(
            "trace_request_completed",
            request_id=request_id,
            found=True,
            spans=len(spans),
            status=status,
        )

        return result

    except ValidationError:
        raise
    except Exception as e:
        logger.error(
            "trace_request_failed",
            error=str(e),
            request_id=request_id,
        )
        raise ToolExecutionError(
            f"Failed to trace request: {e}",
            tool_name="trace_request",
            phase="execution",
        ) from e


def _create_span(span_data: dict[str, Any]) -> TraceSpan:
    """Create a TraceSpan from span data."""
    start_time = span_data["start_time"]
    end_time = span_data["end_time"]
    duration_ms = (end_time - start_time).total_seconds() * 1000

    # Extract operation from first log message
    operation = "unknown"
    logs = span_data.get("logs", [])
    if logs:
        first_msg = logs[0].message.lower()
        if "create" in first_msg or "spawn" in first_msg:
            operation = "create"
        elif "delete" in first_msg or "destroy" in first_msg:
            operation = "delete"
        elif "update" in first_msg or "patch" in first_msg:
            operation = "update"
        elif "get" in first_msg or "list" in first_msg or "show" in first_msg:
            operation = "read"
        elif "auth" in first_msg or "token" in first_msg:
            operation = "authenticate"
        elif "attach" in first_msg:
            operation = "attach"
        elif "detach" in first_msg:
            operation = "detach"
        else:
            operation = "process"

    return TraceSpan(
        span_id=f"span-{uuid.uuid4().hex[:12]}",
        service=span_data["service"],
        operation=operation,
        host=span_data["host"],
        start_time=start_time.isoformat(),
        end_time=end_time.isoformat(),
        duration_ms=max(duration_ms, 1.0),  # Minimum 1ms
        status=span_data["status"],
        error_message=span_data.get("error_message"),
        logs=logs,
        tags={},
    )


def _generate_trace_summary(
    request_id: str,
    services: list[str],
    spans: list[TraceSpan],
    total_duration_ms: float | None,
    status: str,
) -> str:
    """Generate a human-readable trace summary."""
    parts = [f"Request {request_id}"]

    if total_duration_ms is not None:
        if total_duration_ms < 1000:
            parts.append(f"completed in {total_duration_ms:.0f}ms")
        else:
            parts.append(f"completed in {total_duration_ms / 1000:.1f}s")

    parts.append(f"with status: {status}")

    summary = " ".join(parts) + "."

    if services:
        summary += f" Traversed {len(services)} services: {' -> '.join(services)}."

    if status == "error":
        error_spans = [s for s in spans if s.status == "error"]
        if error_spans:
            summary += f" Error occurred in {error_spans[0].service}."

    return summary


def _generate_recommendations(
    spans: list[TraceSpan],
    error_span: TraceSpan | None,
    bottleneck_span: TraceSpan | None,
) -> list[str]:
    """Generate recommendations based on trace analysis."""
    recommendations: list[str] = []

    # Error recommendations
    if error_span:
        recommendations.append(
            f"Investigate error in {error_span.service}: {error_span.error_message or 'check logs'}"
        )
        recommendations.append(f"Check {error_span.service} logs around {error_span.start_time}")

    # Bottleneck recommendations
    if bottleneck_span and bottleneck_span.duration_ms > 1000:
        recommendations.append(
            f"Performance: {bottleneck_span.service} took {bottleneck_span.duration_ms:.0f}ms "
            f"(operation: {bottleneck_span.operation})"
        )

    # General recommendations
    if not recommendations:
        recommendations.append("Request completed successfully - no issues detected")

    if len(spans) > 5:
        recommendations.append(
            "Request touched many services - consider simplifying the operation path"
        )

    total_duration = sum(s.duration_ms for s in spans)
    if total_duration > 5000:
        recommendations.append(
            "Total processing time exceeds 5 seconds - review for optimization opportunities"
        )

    return recommendations


# Tool metadata for registration
TOOL_NAME = "trace_request"
TOOL_DESCRIPTION = """Trace an OpenStack request across services using correlation ID.

Follows a request through different OpenStack services using the X-Request-ID
header, reconstructing the request timeline and identifying failures or bottlenecks.

This is a READ-ONLY tool that does not modify any resources."""

TOOL_TAGS = ["troubleshooting", "tracing", "observability", "read-only"]
