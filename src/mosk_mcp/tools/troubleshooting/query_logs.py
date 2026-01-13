"""Query logs tool for intelligent troubleshooting.

This tool provides log querying capabilities with natural language support,
allowing operators to search logs across services using intuitive queries
like 'nova errors in last hour'.

Safety Level: Read-only

This tool queries OpenSearch via OIDC/SSO authentication using
DirectStackLightClient. Authentication must be established before
calling this tool.

When OpenSearch is unavailable, this tool can fall back to using
kubectl to retrieve pod logs directly from Kubernetes.
"""

from __future__ import annotations

import contextlib
import re
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from mosk_mcp.adapters.stacklight import (
    DirectStackLightClient,
    LogEntry,
    StackLightAdapter,
)
from mosk_mcp.adapters.stacklight import (
    LogSeverity as AdapterLogSeverity,
)
from mosk_mcp.core.exceptions import ToolExecutionError, ValidationError
from mosk_mcp.observability.logging import get_logger
from mosk_mcp.tools.troubleshooting.models import (
    LogEntryInfo,
    LogSeverity,
    QueryLogsOutput,
)


if TYPE_CHECKING:
    from mosk_mcp.adapters.kubernetes import KubernetesAdapter


logger = get_logger(__name__)

# Service to Kubernetes label mapping for fallback queries
SERVICE_TO_LABEL_SELECTOR: dict[str, str] = {
    "nova": "application=nova",
    "neutron": "application=neutron",
    "cinder": "application=cinder",
    "glance": "application=glance",
    "keystone": "application=keystone",
    "heat": "application=heat",
    "octavia": "application=octavia",
    "barbican": "application=barbican",
    "designate": "application=designate",
    "manila": "application=manila",
    "placement": "application=placement",
    "horizon": "application=horizon",
    "rabbitmq": "application=rabbitmq",
    "mariadb": "application=mariadb",
    "memcached": "application=memcached",
}

# Severity patterns for log line classification
SEVERITY_PATTERNS: dict[LogSeverity, list[re.Pattern[str]]] = {
    LogSeverity.CRITICAL: [
        re.compile(r"\b(CRITICAL|FATAL|EMERG|PANIC)\b", re.IGNORECASE),
    ],
    LogSeverity.ERROR: [
        re.compile(r"\b(ERROR|ERR|FAIL(?:ED|URE)?|EXCEPTION)\b", re.IGNORECASE),
    ],
    LogSeverity.WARNING: [
        re.compile(r"\b(WARN(?:ING)?)\b", re.IGNORECASE),
    ],
    LogSeverity.INFO: [
        re.compile(r"\b(INFO)\b", re.IGNORECASE),
    ],
    LogSeverity.DEBUG: [
        re.compile(r"\b(DEBUG)\b", re.IGNORECASE),
    ],
}

# Timestamp pattern for log lines (RFC3339, ISO8601, or common formats)
TIMESTAMP_PATTERN = re.compile(
    r"^(\d{4}-\d{2}-\d{2}[T\s]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)"
)


def _detect_severity(log_line: str) -> LogSeverity:
    """Detect log severity from a log line."""
    for severity, patterns in SEVERITY_PATTERNS.items():
        for pattern in patterns:
            if pattern.search(log_line):
                return severity
    return LogSeverity.INFO


def _parse_timestamp(log_line: str) -> datetime | None:
    """Try to parse timestamp from log line."""
    match = TIMESTAMP_PATTERN.match(log_line)
    if match:
        ts_str = match.group(1)
        # Normalize timestamp format
        ts_str = ts_str.replace("Z", "+00:00")
        if " " in ts_str and "T" not in ts_str:
            ts_str = ts_str.replace(" ", "T")
        try:
            return datetime.fromisoformat(ts_str)
        except ValueError:
            pass
    return None


def _matches_severity_filter(line_severity: LogSeverity, min_severity: LogSeverity | None) -> bool:
    """Check if log line meets minimum severity filter."""
    if min_severity is None:
        return True

    severity_order = [
        LogSeverity.DEBUG,
        LogSeverity.INFO,
        LogSeverity.WARNING,
        LogSeverity.ERROR,
        LogSeverity.CRITICAL,
    ]

    try:
        line_idx = severity_order.index(line_severity)
        min_idx = severity_order.index(min_severity)
        return line_idx >= min_idx
    except ValueError:
        return True


def _matches_keywords(log_line: str, keywords: list[str] | None) -> bool:
    """Check if log line contains any of the keywords."""
    if not keywords:
        return True
    log_lower = log_line.lower()
    return any(kw.lower() in log_lower for kw in keywords)


async def _fallback_to_kubectl(
    kubernetes_adapter: KubernetesAdapter,
    services: list[str] | None,
    severity: LogSeverity | None,
    time_range_minutes: int,
    keywords: list[str] | None,
    limit: int,
    namespace: str = "openstack",
) -> list[LogEntryInfo]:
    """Fallback to kubectl for log retrieval when OpenSearch is unavailable.

    Args:
        kubernetes_adapter: Authenticated KubernetesAdapter.
        services: Service names to filter.
        severity: Minimum severity level.
        time_range_minutes: Time range in minutes.
        keywords: Keywords to search for.
        limit: Maximum logs to return.
        namespace: Kubernetes namespace.

    Returns:
        List of LogEntryInfo from pod logs.
    """
    logger.info(
        "query_logs_kubectl_fallback_started",
        services=services,
        namespace=namespace,
        time_range_minutes=time_range_minutes,
    )

    log_entries: list[LogEntryInfo] = []

    # Build label selectors from services
    if services:
        label_selectors = []
        for svc in services:
            svc_lower = svc.lower()
            if svc_lower in SERVICE_TO_LABEL_SELECTOR:
                label_selectors.append(SERVICE_TO_LABEL_SELECTOR[svc_lower])
            else:
                # Try generic label
                label_selectors.append(f"application={svc_lower}")
    else:
        # Query all known OpenStack services when no filter is specified
        label_selectors = list(SERVICE_TO_LABEL_SELECTOR.values())

    # Query pods for each service
    for label_selector in label_selectors:
        try:
            pod_logs = await kubernetes_adapter.get_pod_logs(
                namespace=namespace,
                label_selector=label_selector,
                since_seconds=time_range_minutes * 60,
                tail_lines=1000,  # Get more lines for filtering
            )

            # Process each pod's logs
            for pod_result in pod_logs:
                pod_name = pod_result.get("pod_name", "unknown")
                raw_logs = pod_result.get("logs", "")
                container = pod_result.get("container", "")

                if pod_result.get("error"):
                    logger.warning(
                        "pod_log_retrieval_failed",
                        pod=pod_name,
                        container=container,
                        error=pod_result.get("error"),
                    )
                    continue

                if not raw_logs:
                    continue

                # Parse log lines
                for raw_line in raw_logs.split("\n"):
                    line = raw_line.strip()
                    if not line:
                        continue

                    # Detect severity
                    line_severity = _detect_severity(line)

                    # Apply severity filter
                    if not _matches_severity_filter(line_severity, severity):
                        continue

                    # Apply keyword filter
                    if not _matches_keywords(line, keywords):
                        continue

                    # Parse timestamp or use current time
                    timestamp = _parse_timestamp(line) or datetime.now(UTC)

                    # Extract service name from pod name
                    service_name = pod_name.split("-")[0] if "-" in pod_name else pod_name

                    log_entries.append(
                        LogEntryInfo(
                            timestamp=timestamp.isoformat(),
                            message=line,
                            severity=line_severity,
                            service=service_name,
                            host=pod_name,
                            request_id=None,
                            namespace=namespace,
                            extra={"container": container, "source": "kubectl"},
                        )
                    )

                    # Check limit
                    if len(log_entries) >= limit:
                        break

                if len(log_entries) >= limit:
                    break

        except Exception as e:
            logger.warning(
                "kubectl_fallback_query_error",
                label_selector=label_selector,
                error=str(e),
            )

        if len(log_entries) >= limit:
            break

    # Sort by timestamp descending (most recent first)
    log_entries.sort(key=lambda x: x.timestamp, reverse=True)

    # Trim to limit
    log_entries = log_entries[:limit]

    logger.info(
        "query_logs_kubectl_fallback_completed",
        total_entries=len(log_entries),
    )

    return log_entries


def _convert_log_entry(entry: LogEntry) -> LogEntryInfo:
    """Convert adapter LogEntry to model LogEntryInfo.

    Args:
        entry: LogEntry from StackLight adapter.

    Returns:
        LogEntryInfo model instance.
    """
    # Map adapter severity to model severity
    severity_map = {
        AdapterLogSeverity.DEBUG: LogSeverity.DEBUG,
        AdapterLogSeverity.INFO: LogSeverity.INFO,
        AdapterLogSeverity.WARNING: LogSeverity.WARNING,
        AdapterLogSeverity.ERROR: LogSeverity.ERROR,
        AdapterLogSeverity.CRITICAL: LogSeverity.CRITICAL,
        AdapterLogSeverity.UNKNOWN: LogSeverity.INFO,
    }

    return LogEntryInfo(
        timestamp=entry.timestamp.isoformat(),
        message=entry.message,
        severity=severity_map.get(entry.severity, LogSeverity.INFO),
        service=entry.service,
        host=entry.host,
        request_id=entry.request_id,
        namespace=entry.namespace,
        extra=entry.extra,
    )


async def query_logs(
    direct_client: DirectStackLightClient,
    query: str | None = None,
    services: list[str] | None = None,
    severity: LogSeverity | None = None,
    hosts: list[str] | None = None,
    time_range_minutes: int = 60,
    keywords: list[str] | None = None,
    request_id: str | None = None,
    limit: int = 100,
    kubernetes_adapter: KubernetesAdapter | None = None,
    namespace: str = "openstack",
    # New parameters for enhanced filtering
    namespaces: list[str] | None = None,
    containers: list[str] | None = None,
    pods: list[str] | None = None,
    providers: list[str] | None = None,
    http_methods: list[str] | None = None,
    http_status_codes: list[int] | None = None,
    min_duration_ms: int | None = None,
    max_duration_ms: int | None = None,
    http_path: str | None = None,
    event_sources: list[str] | None = None,
    # Pagination parameters
    cursor: str | None = None,
    aggregation_only: bool = False,
    # Index-specific parameters
    index_type: str | None = None,  # system, audit, k8s_events, notifications
    event_reason: str | None = None,
    event_type_filter: str | None = None,
    involved_kind: str | None = None,
    audit_provider: str | None = None,
    notification_event_type: str | None = None,
    notification_logger: str | None = None,
) -> QueryLogsOutput:
    """Query logs across services with natural language or structured filters.

    This tool searches logs from OpenSearch (StackLight) via OIDC/SSO
    authentication. The direct_client must be authenticated with valid
    Keycloak tokens before calling this tool.

    Supports cursor-based pagination for handling large result sets efficiently.
    For very large result sets, use aggregation_only=True to get statistics
    without downloading all log entries.

    If OpenSearch is not available and kubernetes_adapter is provided,
    falls back to querying pod logs directly from Kubernetes via kubectl.

    Safety Level: Read-only

    Natural language examples:
    - 'nova errors in last hour'
    - 'neutron warnings from compute-01 yesterday'
    - 'openstack namespace 500 errors'
    - 'slow requests over 1000ms'

    Args:
        direct_client: Authenticated DirectStackLightClient for StackLight access.
        query: Natural language query (e.g., 'nova errors in last hour').
        services: Filter by service names (e.g., ['nova', 'neutron']).
        severity: Minimum severity level to return.
        hosts: Filter by host names.
        time_range_minutes: Time range in minutes (default: 60, max: 10080).
        keywords: Additional keywords to search for.
        request_id: Filter by request/correlation ID.
        limit: Maximum number of logs to return per page (default: 100, max: 500).
        kubernetes_adapter: Optional KubernetesAdapter for kubectl fallback
            when OpenSearch is unavailable.
        namespace: Kubernetes namespace for kubectl fallback (default: openstack).
        namespaces: Filter by Kubernetes namespaces (e.g., ['openstack', 'stacklight']).
        containers: Filter by container names (e.g., ['nova-api', 'neutron-server']).
        pods: Filter by pod names.
        providers: Filter by event providers (e.g., ['nova-compute', 'neutron-server']).
        http_methods: Filter by HTTP methods (e.g., ['GET', 'POST']).
        http_status_codes: Filter by HTTP status codes (e.g., [500, 502, 503]).
        min_duration_ms: Minimum HTTP request duration in ms (find slow requests).
        max_duration_ms: Maximum HTTP request duration in ms.
        http_path: Filter by HTTP request path pattern (e.g., '/v2.1/servers').
        event_sources: Filter by event source (container, journal, file).
        cursor: Pagination cursor from previous response to fetch next page.
        aggregation_only: If True, return only statistics without log entries
            (useful for large result sets where you only need counts).

    Returns:
        QueryLogsOutput with matching log entries, pagination info, and statistics.
        - logs: List of log entries (empty if aggregation_only=True)
        - cursor: Cursor for fetching next page (None if no more results)
        - has_more: Whether more results are available
        - total_count: Total logs matching query (may be estimate for large datasets)

    Raises:
        ValidationError: If input parameters are invalid.
        ToolExecutionError: If log query fails.

    Example:
        >>> result = await query_logs(client, query="nova errors in last hour")

        >>> # Paginated query
        >>> page1 = await query_logs(client, services=["nova"], limit=100)
        >>> if page1.has_more:
        ...     page2 = await query_logs(client, services=["nova"], cursor=page1.cursor)

        >>> # Get only statistics for large result sets
        >>> stats = await query_logs(
        ...     client,
        ...     services=["nova"],
        ...     aggregation_only=True,
        ... )
        >>> print(f"Total errors: {stats.by_severity.get('error', 0)}")

        >>> # Find slow API requests (>1 second)
        >>> result = await query_logs(
        ...     client,
        ...     namespaces=["openstack"],
        ...     min_duration_ms=1000,
        ... )
    """
    logger.info(
        "query_logs_started",
        query=query,
        services=services,
        severity=severity.value if severity else None,
        time_range_minutes=time_range_minutes,
        namespaces=namespaces,
        index_type=index_type,
        cursor=cursor[:20] if cursor else None,
        aggregation_only=aggregation_only,
    )

    # Validate inputs
    if time_range_minutes < 1 or time_range_minutes > 10080:
        raise ValidationError(
            "time_range_minutes must be between 1 and 10080 (7 days)",
            field="time_range_minutes",
            value=time_range_minutes,
        )

    # Max limit reduced for pagination (was 1000, now 500)
    if limit < 1 or limit > 500:
        raise ValidationError(
            "limit must be between 1 and 500 (use pagination for more results)",
            field="limit",
            value=limit,
        )

    # Import constants for message truncation
    from mosk_mcp.tools.troubleshooting.models import MAX_LOG_MESSAGE_LENGTH

    try:
        # Create StackLight adapter with direct client
        stacklight = StackLightAdapter(direct_client=direct_client)
        await stacklight.connect()

        query_info: dict[str, Any] = {}
        query_result = None
        total_count = 0
        next_cursor: str | None = None
        has_more = False

        # Use natural language query if provided
        if query:
            query_result, parsed = await stacklight.query_logs_natural_language(
                query=query,
                limit=limit,
                cursor=cursor,
            )
            query_info = parsed
        else:
            # Use structured parameters
            severity_value = severity.value if severity else None
            query_result = await stacklight.query_logs(
                services=services,
                severity=severity_value,
                hosts=hosts,
                time_range_minutes=time_range_minutes,
                keywords=keywords,
                request_id=request_id,
                limit=limit,
                namespaces=namespaces,
                containers=containers,
                pods=pods,
                providers=providers,
                http_methods=http_methods,
                http_status_codes=http_status_codes,
                min_duration_ms=min_duration_ms,
                max_duration_ms=max_duration_ms,
                http_path=http_path,
                event_sources=event_sources,
                cursor=cursor,
                # Index-specific parameters
                index_type=index_type,
                event_reason=event_reason,
                event_type_filter=event_type_filter,
                involved_kind=involved_kind,
                audit_provider=audit_provider,
                notification_event_type=notification_event_type,
                notification_logger=notification_logger,
            )
            query_info = {
                "services": services,
                "severity": severity_value,
                "hosts": hosts,
                "time_range_minutes": time_range_minutes,
                "keywords": keywords,
                "request_id": request_id,
                "namespaces": namespaces,
                "containers": containers,
                "pods": pods,
                "providers": providers,
                "http_methods": http_methods,
                "http_status_codes": http_status_codes,
                "min_duration_ms": min_duration_ms,
                "max_duration_ms": max_duration_ms,
                "http_path": http_path,
                "event_sources": event_sources,
                "index_type": index_type,
            }

        # Extract pagination info from result
        total_count = query_result.total_count
        next_cursor = query_result.cursor
        has_more = query_result.has_more
        raw_logs = query_result.logs

        # Convert log entries with message truncation
        log_entries: list[LogEntryInfo] = []
        truncated_count = 0

        if not aggregation_only:
            for log in raw_logs:
                entry = _convert_log_entry(log)

                # Truncate long messages
                if len(entry.message) > MAX_LOG_MESSAGE_LENGTH:
                    original_length = len(entry.message)
                    entry.message = entry.message[:MAX_LOG_MESSAGE_LENGTH] + "... [truncated]"
                    entry.message_truncated = True
                    entry.original_length = original_length
                    truncated_count += 1

                log_entries.append(entry)

        # Fallback to kubectl if OpenSearch returned empty and kubernetes_adapter is available
        used_kubectl_fallback = False
        if (
            not log_entries
            and not aggregation_only
            and kubernetes_adapter is not None
            and not cursor
        ):
            logger.info(
                "opensearch_empty_using_kubectl_fallback",
                kubernetes_adapter_available=True,
            )

            # Parse services from natural language query if needed
            fallback_services = services
            fallback_severity = severity
            fallback_keywords = keywords
            fallback_time_range = time_range_minutes

            if query and query_info:
                fallback_services = query_info.get("services") or services
                sev_str = query_info.get("severity")
                if sev_str:
                    with contextlib.suppress(ValueError):
                        fallback_severity = LogSeverity(sev_str)
                fallback_keywords = query_info.get("keywords") or keywords
                fallback_time_range = query_info.get("time_range_minutes", time_range_minutes)

            log_entries = await _fallback_to_kubectl(
                kubernetes_adapter=kubernetes_adapter,
                services=fallback_services,
                severity=fallback_severity,
                time_range_minutes=fallback_time_range,
                keywords=fallback_keywords,
                limit=limit,
                namespace=namespace,
            )
            used_kubectl_fallback = True
            total_count = len(log_entries)
            next_cursor = None  # kubectl fallback doesn't support pagination
            has_more = False

            # Update query_info to indicate kubectl fallback was used
            query_info["source"] = "kubectl_fallback"
            query_info["namespace"] = namespace

        # Calculate statistics from returned logs
        severity_counts: Counter[str] = Counter()
        service_counts: Counter[str] = Counter()
        host_counts: Counter[str] = Counter()

        for entry in log_entries:
            severity_counts[entry.severity.value] += 1
            service_counts[entry.service] += 1
            if entry.host:
                host_counts[entry.host] += 1

        # Calculate time range
        now = datetime.now(UTC)
        actual_time_range = query_info.get("time_range_minutes", time_range_minutes)
        start_time = now - timedelta(minutes=actual_time_range)

        # Add note about fallback in query_info
        if used_kubectl_fallback:
            query_info["note"] = (
                "OpenSearch unavailable. Results from kubectl pod logs (limited to running pods)."
            )

        # Calculate approximate response size
        response_size = sum(len(e.message) for e in log_entries)

        result = QueryLogsOutput(
            logs=log_entries,
            total_count=total_count,
            returned_count=len(log_entries),
            cursor=next_cursor,
            has_more=has_more,
            page_size_bytes=response_size,
            query_info=query_info,
            by_severity=dict(severity_counts),
            by_service=dict(service_counts),
            by_host=dict(host_counts),
            time_range={
                "start": start_time.isoformat(),
                "end": now.isoformat(),
                "minutes": str(actual_time_range),
            },
            truncated_messages=truncated_count,
            timestamp=now.isoformat(),
        )

        logger.info(
            "query_logs_completed",
            total_count=result.total_count,
            returned_count=result.returned_count,
            has_more=result.has_more,
            truncated_messages=truncated_count,
        )

        return result

    except ValidationError:
        raise
    except Exception as e:
        logger.error(
            "query_logs_failed",
            error=str(e),
            query=query,
        )
        raise ToolExecutionError(
            f"Failed to query logs: {e}",
            tool_name="query_logs",
            phase="execution",
        ) from e


# Tool metadata for registration
TOOL_NAME = "query_logs"
TOOL_DESCRIPTION = """Search logs across OpenStack services with natural language or structured filters.

Supports natural language queries like:
- 'nova errors in last hour'
- 'neutron warnings from compute-01 yesterday'
- 'cinder failures for project abc123'

Or structured filtering by service, severity, host, keywords, project ID, or request ID.

This is a READ-ONLY tool that does not modify any resources."""

TOOL_TAGS = ["troubleshooting", "logs", "observability", "read-only"]
