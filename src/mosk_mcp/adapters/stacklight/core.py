"""StackLight adapter for log queries and metrics.

This module provides integration with StackLight components:
- OpenSearch for log queries
- Prometheus for metrics queries
- Alertmanager for alert management

The adapter uses Keycloak OIDC/SSO authentication to access StackLight
services via their IAM Proxy endpoints. This provides:
- User-scoped access (respects IAM RBAC permissions)
- No dependency on admin kubeconfig
- Direct HTTP calls for performance

The adapter requires:
- A DirectStackLightClient with valid Keycloak auth tokens
- StackLight IAM Proxy endpoints (prometheus, alertmanager)

Tools using this adapter:
- list_active_alerts (queries Alertmanager via OIDC)
- get_alert_details (queries Alertmanager via OIDC)
- explain_alert
- diagnose_vm_failure
- diagnose_network_issue
- diagnose_storage_issue
- query_logs (queries OpenSearch via OIDC)
- correlate_events
- trace_request
- create_diagnostic_bundle
"""

from __future__ import annotations

import asyncio
import contextlib
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, ClassVar, Literal, cast

from mosk_mcp.core.exceptions import (
    MoskConnectionError,
    MoskMCPError,
)
from mosk_mcp.observability.logging import get_logger
from mosk_mcp.tools.common.enums import AlertSeverity, AlertState, LogSeverity


if TYPE_CHECKING:
    from mosk_mcp.core.config import Settings

# Type alias for cluster types
ClusterType = Literal["mcc", "mosk"]


logger = get_logger(__name__)


class StackLightError(MoskMCPError):
    """Raised when StackLight operations fail.

    Attributes:
        message: Human-readable error message.
        component: The StackLight component that failed.
        query: The query that failed (if applicable).
    """

    def __init__(
        self,
        message: str = "StackLight operation failed",
        component: str | None = None,
        query: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Initialize StackLight error.

        Args:
            message: Human-readable error message.
            component: The StackLight component (opensearch, prometheus, alertmanager).
            query: The query that failed.
            details: Optional additional context.
        """
        details = details or {}
        if component:
            details["component"] = component
        if query:
            details["query"] = query[:200] if len(query) > 200 else query
        super().__init__(message, details, error_code="STACKLIGHT_ERROR")
        self.component = component
        self.query = query


@dataclass
class LogQueryResult:
    """Result of a paginated log query.

    Attributes:
        logs: List of log entries for this page.
        total_count: Total number of logs matching the query (may be estimate).
        cursor: Cursor for fetching the next page (None if no more results).
        has_more: Whether more results are available.
    """

    logs: list[LogEntry]
    total_count: int
    cursor: str | None = None
    has_more: bool = False

    @property
    def returned_count(self) -> int:
        """Number of logs returned in this page."""
        return len(self.logs)


@dataclass
class LogEntry:
    """A single log entry from OpenSearch.

    Attributes:
        timestamp: Log timestamp (@timestamp).
        message: Log message.
        severity: Log severity level (log.level).
        service: Source service name (orchestrator.labels.application).
        host: Source host (host.hostname).
        request_id: HTTP request/correlation ID (http.request.id).
        cluster_type: Source cluster (mcc or mosk).
        extra: Additional fields not mapped to specific attributes.

        # Kubernetes/orchestrator fields
        namespace: Kubernetes namespace (orchestrator.namespace).
        pod: Pod name (orchestrator.pod).
        container_name: Container name (container.name).
        container_id: Container ID (container.id).
        labels: Kubernetes labels (orchestrator.labels).

        # Event source fields
        event_source: Event source type (event.source: container, journal, file).
        event_provider: Event provider/app name (event.provider).

        # HTTP request fields (for API logs)
        http_method: HTTP method (http.request.method).
        http_path: HTTP request path (http.request.path).
        http_status_code: HTTP response status (http.response.status_code).
        http_duration_us: Request duration in microseconds (http.request.duration).
        http_source_address: Source IP address (http.source.address).
    """

    timestamp: datetime
    message: str
    severity: LogSeverity = LogSeverity.INFO
    service: str = ""
    host: str = ""
    request_id: str | None = None
    cluster_type: ClusterType | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    # Kubernetes/orchestrator fields
    namespace: str | None = None
    pod: str | None = None
    container_name: str | None = None
    container_id: str | None = None
    labels: dict[str, str] = field(default_factory=dict)

    # Event source fields
    event_source: str | None = None
    event_provider: str | None = None

    # HTTP request fields
    http_method: str | None = None
    http_path: str | None = None
    http_status_code: int | None = None
    http_duration_us: int | None = None
    http_source_address: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        result = {
            "timestamp": self.timestamp.isoformat(),
            "message": self.message,
            "severity": self.severity.value,
            "service": self.service,
            "host": self.host,
            "request_id": self.request_id,
            "cluster_type": self.cluster_type,
            "namespace": self.namespace,
            "pod": self.pod,
            "container_name": self.container_name,
            "labels": self.labels if self.labels else None,
            "event_provider": self.event_provider,
        }
        # Add HTTP fields if present (API logs)
        if self.http_method:
            http_info: dict[str, Any] = {
                "method": self.http_method,
                "path": self.http_path,
                "status_code": self.http_status_code,
                "duration_us": self.http_duration_us,
                "source_address": self.http_source_address,
            }
            result["http"] = http_info
        # Add extra fields
        if self.extra:
            result["extra"] = self.extra
        return result

    @property
    def http_duration_ms(self) -> float | None:
        """Get HTTP duration in milliseconds."""
        if self.http_duration_us is not None:
            return self.http_duration_us / 1000.0
        return None

    @property
    def is_error(self) -> bool:
        """Check if this is an error log."""
        return self.severity in (LogSeverity.ERROR, LogSeverity.CRITICAL)

    @property
    def is_http_error(self) -> bool:
        """Check if this is an HTTP error (4xx or 5xx)."""
        if self.http_status_code:
            return self.http_status_code >= 400
        return False


@dataclass
class Alert:
    """An alert from Alertmanager.

    Attributes:
        alert_name: Name of the alert (labels.alertname).
        severity: Alert severity (labels.severity).
        state: Alert state (status.state: active/firing, pending, resolved).
        summary: Alert summary (annotations.summary).
        description: Detailed description (annotations.description).
        labels: All alert labels.
        annotations: All alert annotations.
        starts_at: When alert started firing (startsAt).
        ends_at: When alert ended or will end (endsAt).
        fingerprint: Unique alert fingerprint for deduplication.
        generator_url: URL to Prometheus query (generatorURL).
        cluster_type: Source cluster (mcc or mosk).

        # Additional Alertmanager fields
        updated_at: When the alert was last updated (updatedAt).
        receivers: List of receiver names that will handle this alert.
        inhibited_by: List of alert fingerprints inhibiting this alert.
        silenced_by: List of silence IDs affecting this alert.
        muted_by: List of mute IDs affecting this alert.
    """

    alert_name: str
    severity: AlertSeverity
    state: AlertState
    summary: str = ""
    description: str = ""
    labels: dict[str, str] = field(default_factory=dict)
    annotations: dict[str, str] = field(default_factory=dict)
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    fingerprint: str = ""
    generator_url: str = ""
    cluster_type: ClusterType | None = None

    # Additional Alertmanager fields
    updated_at: datetime | None = None
    receivers: list[str] = field(default_factory=list)
    inhibited_by: list[str] = field(default_factory=list)
    silenced_by: list[str] = field(default_factory=list)
    muted_by: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "alert_name": self.alert_name,
            "severity": self.severity.value,
            "state": self.state.value,
            "summary": self.summary,
            "description": self.description,
            "labels": self.labels,
            "annotations": self.annotations,
            "starts_at": self.starts_at.isoformat() if self.starts_at else None,
            "ends_at": self.ends_at.isoformat() if self.ends_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "fingerprint": self.fingerprint,
            "generator_url": self.generator_url,
            "cluster_type": self.cluster_type,
            "receivers": self.receivers,
            "is_silenced": self.is_silenced,
            "is_inhibited": self.is_inhibited,
        }

    @property
    def is_silenced(self) -> bool:
        """Check if this alert is silenced."""
        return len(self.silenced_by) > 0

    @property
    def is_inhibited(self) -> bool:
        """Check if this alert is inhibited by another alert."""
        return len(self.inhibited_by) > 0

    @property
    def is_muted(self) -> bool:
        """Check if this alert is muted."""
        return len(self.muted_by) > 0

    @property
    def service(self) -> str:
        """Get the service label."""
        return self.labels.get("service", "")

    @property
    def duration_seconds(self) -> float | None:
        """Get how long the alert has been firing in seconds."""
        if self.starts_at:
            now = datetime.now(UTC)
            return (now - self.starts_at).total_seconds()
        return None


@dataclass
class MetricSample:
    """A metric sample from Prometheus.

    Attributes:
        metric_name: Name of the metric.
        labels: Metric labels.
        value: Metric value.
        timestamp: Sample timestamp.
        cluster_type: Source cluster (mcc or mosk).
    """

    metric_name: str
    labels: dict[str, str]
    value: float
    timestamp: datetime
    cluster_type: ClusterType | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "metric_name": self.metric_name,
            "labels": self.labels,
            "value": self.value,
            "timestamp": self.timestamp.isoformat(),
            "cluster_type": self.cluster_type,
        }


class NaturalLanguageQueryParser:
    """Parse natural language queries into OpenSearch DSL.

    Supports queries like:
    - 'nova errors in last hour'
    - 'neutron warnings from compute-01 yesterday'
    - 'openstack namespace 500 errors'
    - 'slow requests over 1000ms'
    - '500 errors in /v2.1/servers'
    """

    # Service name mappings
    SERVICE_ALIASES: ClassVar[dict[str, list[str]]] = {
        "nova": ["nova", "compute", "vm", "instance"],
        "neutron": ["neutron", "network", "networking"],
        "cinder": ["cinder", "volume", "block", "storage"],
        "glance": ["glance", "image", "images"],
        "keystone": ["keystone", "auth", "authentication", "identity"],
        "heat": ["heat", "orchestration", "stack"],
        "octavia": ["octavia", "loadbalancer", "lb"],
        "barbican": ["barbican", "secret", "secrets"],
        "designate": ["designate", "dns"],
        "manila": ["manila", "share", "shares"],
        "placement": ["placement"],
        "horizon": ["horizon", "dashboard"],
        "rabbitmq": ["rabbitmq", "rabbit", "amqp", "rpc"],
        "mariadb": ["mariadb", "mysql", "database", "db"],
        "memcached": ["memcached", "cache"],
        "ceph": ["ceph", "rbd", "rados"],
    }

    # Namespace keywords (maps user-friendly names to actual namespace values)
    NAMESPACE_KEYWORDS: ClassVar[dict[str, str]] = {
        "openstack": "openstack",
        "stacklight": "stacklight",
        "monitoring": "stacklight",
        "rook-ceph": "rook-ceph",
        "ceph": "rook-ceph",
        "kube-system": "kube-system",
        "kubernetes": "kube-system",
        "osh-system": "osh-system",
        "metallb": "metallb-system",
        "redis": "openstack-redis",
        "ceph-lcm": "ceph-lcm-mirantis",
        "tf": "tf",
        "tungsten": "tf",
        "kaas": "kaas",
    }

    # Severity mappings
    SEVERITY_KEYWORDS: ClassVar[dict[str, LogSeverity]] = {
        "error": LogSeverity.ERROR,
        "errors": LogSeverity.ERROR,
        "err": LogSeverity.ERROR,
        "fail": LogSeverity.ERROR,
        "failed": LogSeverity.ERROR,
        "failure": LogSeverity.ERROR,
        "failures": LogSeverity.ERROR,
        "critical": LogSeverity.CRITICAL,
        "crit": LogSeverity.CRITICAL,
        "fatal": LogSeverity.CRITICAL,
        "warning": LogSeverity.WARNING,
        "warn": LogSeverity.WARNING,
        "warnings": LogSeverity.WARNING,
        "info": LogSeverity.INFO,
        "debug": LogSeverity.DEBUG,
    }

    # Time range patterns
    TIME_PATTERNS: ClassVar[list[tuple[re.Pattern[str], int]]] = [
        (re.compile(r"last\s+(\d+)\s*hours?", re.IGNORECASE), 60),  # hours -> minutes
        (re.compile(r"last\s+(\d+)\s*mins?(?:utes)?", re.IGNORECASE), 1),  # minutes
        (re.compile(r"last\s+(\d+)\s*days?", re.IGNORECASE), 1440),  # days -> minutes
        (re.compile(r"past\s+(\d+)\s*hours?", re.IGNORECASE), 60),
        (re.compile(r"past\s+(\d+)\s*mins?(?:utes)?", re.IGNORECASE), 1),
        (re.compile(r"past\s+(\d+)\s*days?", re.IGNORECASE), 1440),
        (re.compile(r"since\s+yesterday", re.IGNORECASE), 1440),
        (re.compile(r"today", re.IGNORECASE), 1440),
        (re.compile(r"yesterday", re.IGNORECASE), 2880),
    ]

    # HTTP status code patterns
    HTTP_STATUS_PATTERNS: ClassVar[list[tuple[re.Pattern[str], list[int]]]] = [
        # Specific status codes like "500 errors" or "404 not found"
        (re.compile(r"\b(500)\b", re.IGNORECASE), [500]),
        (re.compile(r"\b(502)\b", re.IGNORECASE), [502]),
        (re.compile(r"\b(503)\b", re.IGNORECASE), [503]),
        (re.compile(r"\b(504)\b", re.IGNORECASE), [504]),
        (re.compile(r"\b(401)\b", re.IGNORECASE), [401]),
        (re.compile(r"\b(403)\b", re.IGNORECASE), [403]),
        (re.compile(r"\b(404)\b", re.IGNORECASE), [404]),
        # Status code ranges like "5xx errors" or "4xx errors"
        (re.compile(r"\b5xx\b", re.IGNORECASE), [500, 501, 502, 503, 504]),
        (re.compile(r"\b4xx\b", re.IGNORECASE), [400, 401, 403, 404, 405, 408, 429]),
        # Server errors (general)
        (re.compile(r"server\s*errors?", re.IGNORECASE), [500, 502, 503, 504]),
        (re.compile(r"internal\s*server\s*errors?", re.IGNORECASE), [500]),
        (re.compile(r"gateway\s*errors?", re.IGNORECASE), [502, 503, 504]),
        (re.compile(r"timeout\s*errors?", re.IGNORECASE), [504, 408]),
    ]

    # Duration/latency patterns: (pattern, default_ms, is_seconds)
    # is_seconds=True means the captured value is in seconds and needs conversion to ms
    DURATION_PATTERNS: ClassVar[list[tuple[re.Pattern[str], int, bool]]] = [
        # "slow requests" implies > 1000ms
        (re.compile(r"slow\s*(?:requests?|responses?|calls?)?", re.IGNORECASE), 1000, False),
        # "very slow" implies > 5000ms
        (re.compile(r"very\s*slow", re.IGNORECASE), 5000, False),
        # "over X ms" or "more than X ms"
        (
            re.compile(
                r"(?:over|more\s+than|>\s*|above)\s*(\d+)\s*(?:ms|milliseconds?)", re.IGNORECASE
            ),
            0,
            False,
        ),
        # "over X seconds" or "more than X seconds"
        (
            re.compile(
                r"(?:over|more\s+than|>\s*|above)\s*(\d+)\s*(?:s|sec(?:onds?)?)\b", re.IGNORECASE
            ),
            0,
            True,
        ),
    ]

    def parse(self, query: str) -> dict[str, Any]:
        """Parse natural language query into structured query parameters.

        Args:
            query: Natural language query string.

        Returns:
            Dictionary with parsed query parameters:
            - services: List of service names to filter
            - severity: Minimum severity level
            - hosts: List of hosts to filter
            - time_range_minutes: Time range in minutes
            - keywords: Additional keywords to search for
            - request_id: Request/correlation ID to filter
            - namespaces: List of Kubernetes namespaces to filter
            - http_status_codes: List of HTTP status codes to filter
            - http_path: HTTP path pattern to filter
        """
        result: dict[str, Any] = {
            "services": [],
            "severity": None,
            "hosts": [],
            "time_range_minutes": 60,  # Default 1 hour
            "keywords": [],
            "request_id": None,
            "namespaces": [],
            "http_status_codes": None,
            "http_path": None,
            "min_duration_ms": None,
        }

        query_lower = query.lower()
        words = query_lower.split()

        # Extract namespaces
        for keyword, namespace in self.NAMESPACE_KEYWORDS.items():
            if (keyword in words or keyword in query_lower) and namespace not in result[
                "namespaces"
            ]:
                result["namespaces"].append(namespace)

        # Extract services
        for service_name, aliases in self.SERVICE_ALIASES.items():
            for alias in aliases:
                if (alias in words or alias in query_lower) and service_name not in result[
                    "services"
                ]:
                    result["services"].append(service_name)

        # Extract severity
        for keyword, severity in self.SEVERITY_KEYWORDS.items():
            if keyword in words:
                result["severity"] = severity.value
                break

        # Extract HTTP status codes
        for pattern, status_codes in self.HTTP_STATUS_PATTERNS:
            match = pattern.search(query)
            if match:
                if result["http_status_codes"] is None:
                    result["http_status_codes"] = []
                # If the pattern has a capture group (like specific status code), use it
                if match.groups() and match.group(1).isdigit():
                    result["http_status_codes"].append(int(match.group(1)))
                else:
                    result["http_status_codes"].extend(status_codes)
                # Remove duplicates
                result["http_status_codes"] = list(set(result["http_status_codes"]))

        # Extract duration/latency
        for pattern, default_ms, is_seconds in self.DURATION_PATTERNS:
            match = pattern.search(query)
            if match:
                if match.groups() and match.group(1):
                    value = int(match.group(1))
                    # Convert seconds to milliseconds if needed
                    if is_seconds:
                        value = value * 1000
                    result["min_duration_ms"] = value
                else:
                    result["min_duration_ms"] = default_ms
                break

        # Extract HTTP path - look for paths like "/v2.1/servers" or "/api/v1"
        path_pattern = re.compile(r"(/[a-zA-Z0-9._/-]+)", re.IGNORECASE)
        path_match = path_pattern.search(query)
        if path_match:
            result["http_path"] = path_match.group(1)

        # Extract time range
        for pattern, multiplier in self.TIME_PATTERNS:
            match = pattern.search(query)
            if match:
                if match.groups():
                    result["time_range_minutes"] = int(match.group(1)) * multiplier
                else:
                    # Fixed patterns like "yesterday"
                    result["time_range_minutes"] = multiplier
                break

        # Extract hosts (look for patterns like "from host-name" or "on host-name")
        host_pattern = re.compile(
            r"(?:from|on|host)\s+([a-z0-9][\w.-]*)",
            re.IGNORECASE,
        )
        host_matches = host_pattern.findall(query)
        result["hosts"] = host_matches

        # Extract request ID (look for UUID-like patterns with "request" keyword)
        request_pattern = re.compile(
            r"(?:request|req-|trace)\s*[-:]?\s*([a-f0-9-]{32,36})",
            re.IGNORECASE,
        )
        request_match = request_pattern.search(query)
        if request_match:
            result["request_id"] = request_match.group(1)

        # Extract remaining keywords (words not matched elsewhere)
        stop_words = {
            "in",
            "the",
            "from",
            "on",
            "for",
            "with",
            "last",
            "past",
            "since",
            "today",
            "yesterday",
            "hours",
            "hour",
            "minutes",
            "minute",
            "days",
            "day",
            "logs",
            "log",
            "show",
            "get",
            "find",
            "project",
            "tenant",
            "request",
            "namespace",
            "slow",
            "requests",
            "over",
            "more",
            "than",
            "ms",
            "milliseconds",
            "seconds",
            "sec",
            "errors",
            "status",
            "code",
            "http",
        }
        for word in words:
            if (
                word not in stop_words
                and word not in self.SEVERITY_KEYWORDS
                and word not in self.NAMESPACE_KEYWORDS
                and not any(word in aliases for aliases in self.SERVICE_ALIASES.values())
                and len(word) > 2
                and not word.isdigit()  # Skip numbers (like status codes)
            ) and word not in result["keywords"]:
                result["keywords"].append(word)

        # Clean up empty lists for cleaner output
        if not result["namespaces"]:
            result["namespaces"] = None
        if not result["services"]:
            result["services"] = None
        if not result["hosts"]:
            result["hosts"] = None
        if not result["keywords"]:
            result["keywords"] = None

        logger.debug(
            "parsed_natural_language_query",
            original=query,
            parsed=result,
        )

        return result


class StackLightAdapter:
    """Adapter for StackLight monitoring stack using OIDC/SSO authentication.

    This adapter provides access to:
    - OpenSearch for log queries
    - Prometheus for metrics
    - Alertmanager for alerts

    The adapter uses DirectStackLightClient with Keycloak OIDC tokens
    to access StackLight services via their IAM Proxy endpoints. This provides:
    - User-scoped access (respects IAM RBAC permissions)
    - No dependency on kubectl or admin kubeconfig
    - Direct HTTP calls for better performance

    Supports dual-cluster architecture where MCC (management) and MOSK (workload)
    clusters each have their own StackLight deployment.

    Attributes:
        _cluster_type: Type of cluster (mcc or mosk).
        _connected: Whether adapter is connected.
        _nl_parser: Natural language query parser.
        _direct_client: "DirectStackLightClient" for OIDC-based access.

    Example:
        # Authentication is handled via Device Flow (login_secure tool)
        # After authentication, use the session to get a DirectStackLightClient

        client = await session.get_stacklight_client()

        async with client:
            adapter = StackLightAdapter(direct_client=client, cluster_type="mosk")
            async with adapter:
                alerts = await adapter.get_alerts()
    """

    DEFAULT_QUERY_TIMEOUT = 30

    def __init__(
        self,
        direct_client: DirectStackLightClient,
        cluster_type: ClusterType = "mosk",
        query_timeout: int | None = None,
    ) -> None:
        """Initialize the StackLight adapter with OIDC/SSO client.

        The adapter requires a DirectStackLightClient with valid Keycloak tokens.
        All StackLight access uses the user's OIDC credentials to respect
        IAM RBAC permissions.

        Args:
            direct_client: "DirectStackLightClient" for OIDC-based access (required).
            cluster_type: Type of cluster ("mcc" or "mosk").
            query_timeout: Query timeout in seconds.

        Raises:
            ValueError: If direct_client is None.
        """
        if direct_client is None:
            raise ValueError(
                "DirectStackLightClient is required. "
                "Use Keycloak SSO to authenticate and create a DirectStackLightClient."
            )

        self._direct_client = direct_client
        self._cluster_type: ClusterType = cluster_type
        self._query_timeout = query_timeout or self.DEFAULT_QUERY_TIMEOUT
        self._connected = False
        self._nl_parser = NaturalLanguageQueryParser()

    @property
    def cluster_type(self) -> ClusterType:
        """Get the cluster type for this adapter."""
        return self._cluster_type

    @property
    def direct_client(self) -> DirectStackLightClient:
        """Get the direct client for OIDC-based access."""
        return self._direct_client

    @classmethod
    def from_settings(
        cls,
        direct_client: DirectStackLightClient,
        settings: Settings,
        cluster_type: ClusterType = "mosk",
    ) -> StackLightAdapter:
        """Create adapter from settings.

        Args:
            direct_client: "DirectStackLightClient" for OIDC access (required).
            settings: Application settings.
            cluster_type: Type of cluster ("mcc" or "mosk").

        Returns:
            Configured StackLightAdapter instance.
        """
        return cls(
            direct_client=direct_client,
            cluster_type=cluster_type,
            query_timeout=settings.request_timeout,
        )

    async def connect(self) -> None:
        """Establish connection to StackLight services.

        Verifies that the DirectStackLightClient is ready and
        marks the adapter as connected.

        Raises:
            MoskConnectionError: If services cannot be reached.
        """
        if self._connected:
            return

        logger.debug(
            "connecting_to_stacklight",
            cluster_type=self._cluster_type,
            has_direct_client=self._direct_client is not None,
        )

        # DirectStackLightClient handles its own connection management
        self._connected = True
        logger.info(
            "stacklight_connected",
            cluster_type=self._cluster_type,
        )

    async def disconnect(self) -> None:
        """Disconnect from StackLight services."""
        self._connected = False
        logger.debug("stacklight_disconnected")

    async def __aenter__(self) -> StackLightAdapter:
        """Async context manager entry."""
        await self.connect()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        """Async context manager exit."""
        await self.disconnect()

    def _ensure_connected(self) -> None:
        """Ensure adapter is connected.

        Raises:
            MoskConnectionError: If not connected.
        """
        if not self._connected:
            raise MoskConnectionError(
                "StackLight adapter not connected. Call connect() first.",
                service="stacklight",
            )

    # =========================================================================
    # Prometheus Methods
    # =========================================================================

    async def _query_prometheus_instant(
        self,
        query: str,
        time: datetime | None = None,
    ) -> tuple[list[MetricSample], bool]:
        """Query Prometheus instant API via OIDC/SSO.

        GET /api/v1/query?query=<promql>&time=<rfc3339>

        Args:
            query: PromQL query string.
            time: Optional timestamp for the query (default: now).

        Returns:
            Tuple of (list of MetricSamples, success boolean).
        """
        try:
            samples = await self._direct_client.query_prometheus(query, time)
            logger.info(
                "prometheus_instant_query_success",
                sample_count=len(samples),
                cluster_type=self._cluster_type,
            )
            return samples, True
        except Exception as e:
            logger.warning(
                "prometheus_instant_query_failed",
                error=str(e),
                query=query[:100],
            )
            return [], False

    async def _query_prometheus_range(
        self,
        query: str,
        start: datetime,
        end: datetime,
        step: int = 60,
    ) -> tuple[list[MetricSample], bool]:
        """Query Prometheus range API via OIDC/SSO.

        GET /api/v1/query_range?query=<promql>&start=<rfc3339>&end=<rfc3339>&step=<seconds>

        Args:
            query: PromQL query string.
            start: Start timestamp.
            end: End timestamp.
            step: Query resolution step in seconds.

        Returns:
            Tuple of (list of MetricSamples, success boolean).
        """
        try:
            samples = await self._direct_client.query_prometheus_range(query, start, end, step)
            logger.info(
                "prometheus_range_query_success",
                sample_count=len(samples),
                cluster_type=self._cluster_type,
            )
            return samples, True
        except Exception as e:
            logger.warning(
                "prometheus_range_query_failed",
                error=str(e),
                query=query[:100],
            )
            return [], False

    def _parse_prometheus_response(
        self,
        response: dict[str, Any],
    ) -> list[MetricSample]:
        """Parse Prometheus API response into MetricSample objects.

        Handles both instant (vector) and range (matrix) result types.

        Args:
            response: Prometheus API response.

        Returns:
            List of MetricSample objects.
        """
        samples: list[MetricSample] = []
        data = response.get("data", {})
        result_type = data.get("resultType", "")
        results = data.get("result", [])

        for result in results:
            metric = result.get("metric", {})
            metric_name = metric.pop("__name__", "unknown")
            labels = metric  # Remaining keys are labels

            if result_type == "vector":
                # Instant query: single value per series
                value_pair = result.get("value", [])
                if len(value_pair) == 2:
                    timestamp = datetime.fromtimestamp(value_pair[0], tz=UTC)
                    try:
                        value = float(value_pair[1])
                    except (ValueError, TypeError) as e:
                        logger.debug(
                            "metric_value_parse_failed",
                            metric=metric_name,
                            raw_value=str(value_pair[1])[:100],
                            error=str(e),
                        )
                        continue

                    samples.append(
                        MetricSample(
                            metric_name=metric_name,
                            labels=labels,
                            value=value,
                            timestamp=timestamp,
                            cluster_type=self._cluster_type,
                        )
                    )

            elif result_type == "matrix":
                # Range query: multiple values per series
                values = result.get("values", [])
                for value_pair in values:
                    if len(value_pair) == 2:
                        timestamp = datetime.fromtimestamp(value_pair[0], tz=UTC)
                        try:
                            value = float(value_pair[1])
                        except (ValueError, TypeError) as e:
                            logger.debug(
                                "metric_value_parse_failed",
                                metric=metric_name,
                                raw_value=str(value_pair[1])[:100],
                                error=str(e),
                            )
                            continue

                        samples.append(
                            MetricSample(
                                metric_name=metric_name,
                                labels=labels.copy(),
                                value=value,
                                timestamp=timestamp,
                                cluster_type=self._cluster_type,
                            )
                        )

        return samples

    # =========================================================================
    # Natural Language Query Support
    # =========================================================================

    def parse_natural_language_query(self, query: str) -> dict[str, Any]:
        """Parse natural language query into structured parameters.

        Args:
            query: Natural language query like 'nova errors in last hour'.

        Returns:
            Structured query parameters.
        """
        return self._nl_parser.parse(query)

    # =========================================================================
    # Log Query Methods
    # =========================================================================

    async def _query_opensearch(
        self,
        services: list[str] | None = None,
        severity: str | None = None,
        hosts: list[str] | None = None,
        time_range_minutes: int = 60,
        keywords: list[str] | None = None,
        request_id: str | None = None,
        limit: int = 100,
        index_pattern: str = "system*",
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
        # Index-specific parameters
        index_type: str | None = None,
        event_reason: str | None = None,
        event_type_filter: str | None = None,
        involved_kind: str | None = None,
        audit_provider: str | None = None,
        notification_event_type: str | None = None,
        notification_logger: str | None = None,
    ) -> tuple[LogQueryResult | None, bool]:
        """Query OpenSearch via OIDC/SSO with pagination support.

        Uses the DirectStackLightClient to query OpenSearch via IAM Proxy.
        Requires opensearch_url to be configured in the DirectStackLightClient.

        Returns:
            Tuple of (LogQueryResult, success boolean).
            Returns (None, False) when OpenSearch is not available or query fails.
        """
        # Check if OpenSearch is available via DirectStackLightClient
        if not self._direct_client.opensearch_available:
            logger.info(
                "opensearch_not_configured",
                message="OpenSearch URL not configured in DirectStackLightClient. "
                "Configure opensearch_url to enable log queries via OpenSearch API.",
            )
            return None, False

        try:
            result = await self._direct_client.query_opensearch(
                services=services,
                severity=severity,
                hosts=hosts,
                time_range_minutes=time_range_minutes,
                keywords=keywords,
                request_id=request_id,
                limit=limit,
                index_pattern=index_pattern,
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
            return result, True
        except Exception as e:
            logger.warning(
                "opensearch_query_failed",
                error=str(e),
            )
            return None, False

    async def query_logs(
        self,
        services: list[str] | None = None,
        severity: str | None = None,
        hosts: list[str] | None = None,
        time_range_minutes: int = 60,
        keywords: list[str] | None = None,
        request_id: str | None = None,
        limit: int = 100,
        index_pattern: str = "system*",
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
        # Index-specific parameters
        index_type: str | None = None,
        event_reason: str | None = None,
        event_type_filter: str | None = None,
        involved_kind: str | None = None,
        audit_provider: str | None = None,
        notification_event_type: str | None = None,
        notification_logger: str | None = None,
    ) -> LogQueryResult:
        """Query logs from OpenSearch via OIDC/SSO with pagination support.

        Supports multiple OpenSearch index types:
        - system* (default): Container/application logs
        - audit*: Security audit logs (sudo, sshd)
        - kubernetes_events-*: Kubernetes events (pod lifecycle, scheduling)
        - notification-*: OpenStack notifications (instance lifecycle)

        Args:
            services: Filter by service/application names.
            severity: Minimum severity level.
            hosts: Filter by host names.
            time_range_minutes: Time range to query.
            keywords: Additional keywords to search.
            request_id: Filter by request/correlation ID.
            limit: Maximum number of logs to return per page.
            index_pattern: OpenSearch index pattern (default: system*).
            namespaces: Filter by Kubernetes namespaces (e.g., ['openstack', 'stacklight']).
            containers: Filter by container names (e.g., ['nova-api', 'neutron-server']).
            pods: Filter by pod names.
            providers: Filter by event providers.
            http_methods: Filter by HTTP methods (GET, POST, etc.).
            http_status_codes: Filter by HTTP status codes (e.g., [500, 502, 503]).
            min_duration_ms: Minimum HTTP request duration in ms (find slow requests).
            max_duration_ms: Maximum HTTP request duration in ms.
            http_path: Filter by HTTP request path pattern.
            event_sources: Filter by event source (container, journal, file).
            cursor: Pagination cursor from previous response.
            index_type: Index type to query (system, audit, k8s_events, notifications).
            event_reason: [k8s_events only] Filter by event reason.
            event_type_filter: [k8s_events only] Filter by event type ('Normal' or 'Warning').
            involved_kind: [k8s_events only] Filter by involved object kind.
            audit_provider: [audit only] Filter by provider (sudo, sshd, auditd).
            notification_event_type: [notifications only] Filter by event type.
            notification_logger: [notifications only] Filter by logger.

        Returns:
            LogQueryResult with logs, total_count, cursor, and has_more flag.

        Raises:
            StackLightError: If query fails.
        """
        self._ensure_connected()

        logger.debug(
            "querying_logs",
            services=services,
            severity=severity,
            time_range_minutes=time_range_minutes,
            namespaces=namespaces,
            index_type=index_type,
            cursor=cursor[:20] if cursor else None,
        )

        # Query OpenSearch via OIDC/SSO
        result, success = await self._query_opensearch(
            services=services,
            severity=severity,
            hosts=hosts,
            time_range_minutes=time_range_minutes,
            keywords=keywords,
            request_id=request_id,
            limit=limit,
            index_pattern=index_pattern,
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

        if not success or result is None:
            logger.info("opensearch_unavailable_returning_empty")
            return LogQueryResult(logs=[], total_count=0, cursor=None, has_more=False)

        logger.info("logs_queried", count=len(result.logs), total=result.total_count)
        return result

    async def query_logs_natural_language(
        self,
        query: str,
        limit: int = 100,
        cursor: str | None = None,
    ) -> tuple[LogQueryResult, dict[str, Any]]:
        """Query logs using natural language with pagination support.

        Args:
            query: Natural language query string.
            limit: Maximum number of logs to return per page.
            cursor: Pagination cursor from previous response.

        Returns:
            Tuple of (LogQueryResult, parsed query parameters).

        Raises:
            StackLightError: If query fails.
        """
        self._ensure_connected()

        parsed = self.parse_natural_language_query(query)

        result = await self.query_logs(
            services=parsed.get("services"),
            severity=parsed.get("severity"),
            hosts=parsed.get("hosts"),
            time_range_minutes=parsed.get("time_range_minutes", 60),
            keywords=parsed.get("keywords"),
            request_id=parsed.get("request_id"),
            limit=limit,
            namespaces=parsed.get("namespaces"),
            http_status_codes=parsed.get("http_status_codes"),
            http_path=parsed.get("http_path"),
            min_duration_ms=parsed.get("min_duration_ms"),
            cursor=cursor,
        )

        return result, parsed

    async def get_logs_by_request_id(
        self,
        request_id: str,
        time_range_minutes: int = 60,
        limit: int = 500,
    ) -> list[LogEntry]:
        """Get all logs for a specific request ID across services.

        Args:
            request_id: Request/correlation ID.
            time_range_minutes: Time range to search.
            limit: Maximum logs to return.

        Returns:
            List of LogEntry objects sorted by timestamp.

        Raises:
            StackLightError: If query fails.
        """
        self._ensure_connected()

        result = await self.query_logs(
            request_id=request_id,
            time_range_minutes=time_range_minutes,
            limit=limit,
        )

        # Sort by timestamp for trace view
        logs = result.logs
        logs.sort(key=lambda x: x.timestamp)

        return logs

    # =========================================================================
    # Alert Query Methods
    # =========================================================================

    async def get_alerts(
        self,
        state: AlertState | None = None,
        severity: AlertSeverity | None = None,
        labels: dict[str, str] | None = None,
        limit: int = 100,
    ) -> list[Alert]:
        """Get alerts from Alertmanager via OIDC/SSO.

        Uses DirectStackLightClient with Keycloak tokens to query
        Alertmanager via IAM Proxy.

        Args:
            state: Filter by alert state.
            severity: Filter by severity.
            labels: Filter by label values.
            limit: Maximum alerts to return.

        Returns:
            List of Alert objects.

        Raises:
            StackLightError: If query fails.
        """
        self._ensure_connected()

        logger.debug(
            "getting_alerts",
            state=state,
            severity=severity,
        )

        try:
            alerts = await self._direct_client.get_alerts()
            logger.debug(
                "alerts_retrieved",
                count=len(alerts),
            )
        except Exception as e:
            logger.warning(
                "alertmanager_query_failed",
                error=str(e),
            )
            return []

        # Apply filters
        filtered_alerts: list[Alert] = []
        for alert in alerts:
            # Filter by state
            if state and alert.state != state:
                continue

            # Filter by severity
            if severity and alert.severity != severity:
                continue

            # Filter by labels
            if labels:
                match = all(alert.labels.get(k) == v for k, v in labels.items())
                if not match:
                    continue

            filtered_alerts.append(alert)
            if len(filtered_alerts) >= limit:
                break

        logger.info("alerts_retrieved", count=len(filtered_alerts))
        return filtered_alerts

    async def get_alert_by_fingerprint(self, fingerprint: str) -> Alert | None:
        """Get a specific alert by fingerprint.

        Args:
            fingerprint: Alert fingerprint.

        Returns:
            Alert if found, None otherwise.

        Raises:
            StackLightError: If query fails.
        """
        self._ensure_connected()

        alerts = await self.get_alerts(limit=1000)
        for alert in alerts:
            if alert.fingerprint == fingerprint:
                return alert

        return None

    # =========================================================================
    # Metrics Query Methods
    # =========================================================================

    def _build_promql_query(
        self,
        metric_name: str,
        labels: dict[str, str] | None = None,
    ) -> str:
        """Build a PromQL query from metric name and labels.

        Args:
            metric_name: Metric name (can include PromQL functions).
            labels: Optional label filters.

        Returns:
            PromQL query string.
        """
        if not labels:
            return metric_name

        # Build label selector
        label_parts = [f'{k}="{v}"' for k, v in labels.items()]
        label_selector = ",".join(label_parts)

        # Check if metric_name is already a PromQL expression
        if "{" in metric_name or "(" in metric_name:
            # It's a PromQL expression - don't add labels
            return metric_name

        return f"{metric_name}{{{label_selector}}}"

    async def query_metrics(
        self,
        metric_name: str,
        labels: dict[str, str] | None = None,
        time_range_minutes: int = 60,
        step_seconds: int = 60,
    ) -> list[MetricSample]:
        """Query metrics from Prometheus.

        Attempts to query real Prometheus first. If unavailable,
        returns empty list.

        Args:
            metric_name: Metric name or PromQL query.
            labels: Label filters (only applied if metric_name is simple).
            time_range_minutes: Time range to query.
            step_seconds: Query resolution step.

        Returns:
            List of MetricSample objects.

        Raises:
            StackLightError: If query fails critically.
        """
        self._ensure_connected()

        logger.debug(
            "querying_metrics",
            metric=metric_name,
            labels=labels,
            time_range=time_range_minutes,
            cluster_type=self._cluster_type,
        )

        # Build PromQL query
        query = self._build_promql_query(metric_name, labels)

        # Calculate time range
        now = datetime.now(UTC)
        start = now - timedelta(minutes=time_range_minutes)

        # Try real Prometheus range query
        samples, success = await self._query_prometheus_range(
            query=query,
            start=start,
            end=now,
            step=step_seconds,
        )

        if not success:
            logger.info(
                "prometheus_unavailable_returning_empty",
                cluster_type=self._cluster_type,
            )
            return []

        logger.info(
            "metrics_queried",
            count=len(samples),
            cluster_type=self._cluster_type,
        )
        return samples

    async def query_metrics_instant(
        self,
        metric_name: str,
        labels: dict[str, str] | None = None,
        time: datetime | None = None,
    ) -> list[MetricSample]:
        """Query instant metrics from Prometheus.

        Useful for getting current values without time range.

        Args:
            metric_name: Metric name or PromQL query.
            labels: Label filters (only applied if metric_name is simple).
            time: Optional timestamp for the query (default: now).

        Returns:
            List of MetricSample objects.
        """
        self._ensure_connected()

        logger.debug(
            "querying_metrics_instant",
            metric=metric_name,
            labels=labels,
            cluster_type=self._cluster_type,
        )

        # Build PromQL query
        query = self._build_promql_query(metric_name, labels)

        # Try real Prometheus instant query
        samples, success = await self._query_prometheus_instant(
            query=query,
            time=time,
        )

        if not success:
            logger.info(
                "prometheus_instant_unavailable_returning_empty",
                cluster_type=self._cluster_type,
            )
            return []

        logger.info(
            "metrics_instant_queried",
            count=len(samples),
            cluster_type=self._cluster_type,
        )
        return samples

    async def query_prometheus_raw(
        self,
        query: str,
        query_type: str = "instant",
        time_range_minutes: int = 60,
        step_seconds: int = 60,
    ) -> list[MetricSample]:
        """Execute a raw PromQL query.

        Allows full PromQL expressions without label building.

        Args:
            query: Raw PromQL query string.
            query_type: "instant" or "range".
            time_range_minutes: Time range for range queries.
            step_seconds: Step for range queries.

        Returns:
            List of MetricSample objects.
        """
        self._ensure_connected()

        logger.debug(
            "querying_prometheus_raw",
            query=query[:100],
            query_type=query_type,
            cluster_type=self._cluster_type,
        )

        if query_type == "instant":
            samples, success = await self._query_prometheus_instant(query=query)
        else:
            now = datetime.now(UTC)
            start = now - timedelta(minutes=time_range_minutes)
            samples, success = await self._query_prometheus_range(
                query=query,
                start=start,
                end=now,
                step=step_seconds,
            )

        if not success:
            logger.info(
                "prometheus_raw_query_failed",
                cluster_type=self._cluster_type,
            )
            return []

        return samples


# =============================================================================
# Direct HTTP-based StackLight Client (Keycloak SSO)
# =============================================================================


class DirectStackLightClient:
    """Direct HTTP client for StackLight using Keycloak OIDC tokens.

    This client bypasses kubectl exec and connects directly to StackLight
    IAM Proxy endpoints using the user's OIDC id_token. This provides:
    - User-scoped access (respects IAM RBAC)
    - No dependency on kubectl or admin kubeconfig
    - Direct HTTP calls are faster than kubectl exec

    The client requires:
    - A valid Keycloak auth provider with tokens
    - StackLight IAM Proxy endpoints (prometheus_url, alertmanager_url)

    Example:
        # Authentication is handled via Device Flow (login_secure tool)
        # After authentication, use the session to get a StackLight client

        client = await session.get_stacklight_client()
        async with client:
            alerts = await client.get_alerts()
            metrics = await client.query_prometheus("up")
    """

    DEFAULT_TIMEOUT = 30

    def __init__(
        self,
        auth_provider: Any,  # TokenBasedAuthAdapter or similar
        prometheus_url: str = "",
        alertmanager_url: str = "",
        grafana_url: str = "",
        opensearch_url: str = "",
        cluster_type: ClusterType = "mosk",
        *,
        timeout: float = 30.0,
        verify_ssl: bool = True,
    ) -> None:
        """Initialize the direct StackLight client.

        Args:
            auth_provider: Auth provider with get_valid_id_token() method.
            prometheus_url: Prometheus IAM Proxy URL.
            alertmanager_url: Alertmanager IAM Proxy URL.
            grafana_url: Grafana IAM Proxy URL.
            opensearch_url: OpenSearch IAM Proxy URL (Kibana IAM proxy provides API access).
            cluster_type: Cluster type (mcc or mosk).
            timeout: HTTP request timeout in seconds.
            verify_ssl: Whether to verify SSL certificates.
        """
        self._auth = auth_provider
        self._prometheus_url = prometheus_url.rstrip("/") if prometheus_url else ""
        self._alertmanager_url = alertmanager_url.rstrip("/") if alertmanager_url else ""
        self._grafana_url = grafana_url.rstrip("/") if grafana_url else ""
        self._opensearch_url = opensearch_url.rstrip("/") if opensearch_url else ""
        self._cluster_type: ClusterType = cluster_type
        self._timeout = timeout
        self._verify_ssl = verify_ssl
        self._http_client: Any | None = None  # httpx.AsyncClient

    async def __aenter__(self) -> DirectStackLightClient:
        """Async context manager entry - create HTTP client."""
        import httpx

        self._http_client = httpx.AsyncClient(
            verify=self._verify_ssl,
            timeout=httpx.Timeout(self._timeout),
        )
        return self

    async def __aexit__(self, *args: Any) -> None:
        """Async context manager exit - close HTTP client."""
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None

    def _ensure_client(self) -> Any:
        """Ensure HTTP client is available."""
        if not self._http_client:
            raise MoskConnectionError(
                "DirectStackLightClient not initialized. Use 'async with' context manager.",
                service="stacklight",
            )
        return self._http_client

    async def _get_headers(self) -> dict[str, str]:
        """Get authorization headers with valid id_token."""
        id_token = await self._auth.get_valid_id_token()
        return {
            "Authorization": f"Bearer {id_token}",
            "Accept": "application/json",
        }

    @property
    def prometheus_available(self) -> bool:
        """Check if Prometheus URL is configured."""
        return bool(self._prometheus_url)

    @property
    def alertmanager_available(self) -> bool:
        """Check if Alertmanager URL is configured."""
        return bool(self._alertmanager_url)

    @property
    def opensearch_available(self) -> bool:
        """Check if OpenSearch URL is configured."""
        return bool(self._opensearch_url)

    # -------------------------------------------------------------------------
    # Prometheus Methods
    # -------------------------------------------------------------------------

    async def query_prometheus(
        self,
        query: str,
        time: datetime | None = None,
    ) -> list[MetricSample]:
        """Execute Prometheus instant query via HTTP.

        Args:
            query: PromQL query string.
            time: Optional timestamp (default: now).

        Returns:
            List of MetricSample objects.

        Raises:
            StackLightError: If query fails.
        """
        if not self._prometheus_url:
            raise StackLightError(
                "Prometheus URL not configured",
                component="prometheus",
            )

        client = self._ensure_client()
        headers = await self._get_headers()

        params: dict[str, str] = {"query": query}
        if time:
            params["time"] = str(time.timestamp())

        try:
            response = await client.get(
                f"{self._prometheus_url}/api/v1/query",
                params=params,
                headers=headers,
            )

            if response.status_code == 401:
                raise StackLightError(
                    "Authentication failed for Prometheus",
                    component="prometheus",
                    details={"status": 401},
                )

            if response.status_code == 403:
                raise StackLightError(
                    "Access denied to Prometheus (RBAC)",
                    component="prometheus",
                    details={"status": 403},
                )

            if response.status_code != 200:
                raise StackLightError(
                    f"Prometheus query failed: HTTP {response.status_code}",
                    component="prometheus",
                    query=query,
                    details={"status": response.status_code},
                )

            data = response.json()
            if data.get("status") != "success":
                raise StackLightError(
                    f"Prometheus query error: {data.get('error', 'unknown')}",
                    component="prometheus",
                    query=query,
                )

            return self._parse_prometheus_response(data, "vector")

        except Exception as e:
            if isinstance(e, StackLightError):
                raise
            logger.error("prometheus_http_query_error", error=str(e))
            raise StackLightError(
                f"Failed to query Prometheus: {e}",
                component="prometheus",
                query=query,
            ) from e

    async def query_prometheus_range(
        self,
        query: str,
        start: datetime,
        end: datetime,
        step: int = 60,
    ) -> list[MetricSample]:
        """Execute Prometheus range query via HTTP.

        Args:
            query: PromQL query string.
            start: Start timestamp.
            end: End timestamp.
            step: Resolution step in seconds.

        Returns:
            List of MetricSample objects.

        Raises:
            StackLightError: If query fails.
        """
        if not self._prometheus_url:
            raise StackLightError(
                "Prometheus URL not configured",
                component="prometheus",
            )

        client = self._ensure_client()
        headers = await self._get_headers()

        try:
            response = await client.get(
                f"{self._prometheus_url}/api/v1/query_range",
                params={
                    "query": query,
                    "start": str(start.timestamp()),
                    "end": str(end.timestamp()),
                    "step": f"{step}s",
                },
                headers=headers,
            )

            if response.status_code != 200:
                raise StackLightError(
                    f"Prometheus range query failed: HTTP {response.status_code}",
                    component="prometheus",
                    query=query,
                )

            data = response.json()
            if data.get("status") != "success":
                raise StackLightError(
                    f"Prometheus query error: {data.get('error', 'unknown')}",
                    component="prometheus",
                    query=query,
                )

            return self._parse_prometheus_response(data, "matrix")

        except Exception as e:
            if isinstance(e, StackLightError):
                raise
            raise StackLightError(
                f"Failed to query Prometheus: {e}",
                component="prometheus",
                query=query,
            ) from e

    async def get_prometheus_alerts(self) -> list[dict[str, Any]]:
        """Get alerts from Prometheus via HTTP.

        Returns:
            List of alert dictionaries.

        Raises:
            StackLightError: If Prometheus is unavailable or returns an error.
        """
        if not self._prometheus_url:
            # No Prometheus URL configured - this is expected in some deployments
            logger.debug("prometheus_alerts_skipped_no_url")
            return []

        client = self._ensure_client()
        headers = await self._get_headers()

        try:
            response = await client.get(
                f"{self._prometheus_url}/api/v1/alerts",
                headers=headers,
            )

            if response.status_code != 200:
                # Raise exception instead of silent empty list
                # This lets callers distinguish "no alerts" from "monitoring unavailable"
                logger.error(
                    "prometheus_alerts_http_error",
                    status=response.status_code,
                    url=f"{self._prometheus_url}/api/v1/alerts",
                )
                raise StackLightError(
                    f"Prometheus alerts API returned HTTP {response.status_code}",
                    component="prometheus",
                    details={"status_code": response.status_code},
                )

            data = response.json()
            return cast("list[dict[str, Any]]", data.get("data", {}).get("alerts", []))

        except StackLightError:
            # Re-raise our own errors
            raise
        except Exception as e:
            # Raise exception instead of silent empty list
            logger.error(
                "prometheus_alerts_error",
                error=str(e),
                error_type=type(e).__name__,
                url=self._prometheus_url,
            )
            raise StackLightError(
                f"Failed to fetch Prometheus alerts: {e}",
                component="prometheus",
            ) from e

    def _parse_prometheus_response(
        self,
        response: dict[str, Any],
        result_type: str,
    ) -> list[MetricSample]:
        """Parse Prometheus API response into MetricSample objects."""
        samples: list[MetricSample] = []
        data = response.get("data", {})
        results = data.get("result", [])

        for result in results:
            metric = result.get("metric", {})
            metric_name = metric.pop("__name__", "unknown")
            labels = metric

            if result_type == "vector":
                value_pair = result.get("value", [])
                if len(value_pair) == 2:
                    timestamp = datetime.fromtimestamp(value_pair[0], tz=UTC)
                    try:
                        value = float(value_pair[1])
                    except (ValueError, TypeError):
                        continue

                    samples.append(
                        MetricSample(
                            metric_name=metric_name,
                            labels=labels,
                            value=value,
                            timestamp=timestamp,
                            cluster_type=self._cluster_type,
                        )
                    )

            elif result_type == "matrix":
                values = result.get("values", [])
                for value_pair in values:
                    if len(value_pair) == 2:
                        timestamp = datetime.fromtimestamp(value_pair[0], tz=UTC)
                        try:
                            value = float(value_pair[1])
                        except (ValueError, TypeError):
                            continue

                        samples.append(
                            MetricSample(
                                metric_name=metric_name,
                                labels=labels.copy(),
                                value=value,
                                timestamp=timestamp,
                                cluster_type=self._cluster_type,
                            )
                        )

        return samples

    # -------------------------------------------------------------------------
    # Alertmanager Methods
    # -------------------------------------------------------------------------

    async def get_alerts(
        self,
        silenced: bool = False,
        inhibited: bool = False,
    ) -> list[Alert]:
        """Get alerts from Alertmanager via HTTP.

        Args:
            silenced: Include silenced alerts.
            inhibited: Include inhibited alerts.

        Returns:
            List of Alert objects.

        Raises:
            StackLightError: If query fails.
        """
        if not self._alertmanager_url:
            raise StackLightError(
                "Alertmanager URL not configured",
                component="alertmanager",
            )

        client = self._ensure_client()
        headers = await self._get_headers()

        try:
            response = await client.get(
                f"{self._alertmanager_url}/api/v2/alerts",
                params={
                    "silenced": str(silenced).lower(),
                    "inhibited": str(inhibited).lower(),
                },
                headers=headers,
            )

            if response.status_code == 401:
                raise StackLightError(
                    "Authentication failed for Alertmanager",
                    component="alertmanager",
                )

            if response.status_code == 403:
                raise StackLightError(
                    "Access denied to Alertmanager (RBAC)",
                    component="alertmanager",
                )

            if response.status_code != 200:
                raise StackLightError(
                    f"Alertmanager query failed: HTTP {response.status_code}",
                    component="alertmanager",
                )

            alerts_data = response.json()
            return self._parse_alertmanager_response(alerts_data)

        except Exception as e:
            if isinstance(e, StackLightError):
                raise
            raise StackLightError(
                f"Failed to query Alertmanager: {e}",
                component="alertmanager",
            ) from e

    async def get_alertmanager_status(self) -> dict[str, Any]:
        """Get Alertmanager status via HTTP.

        Returns:
            Status dictionary with version info, or dict with 'error' key on failure.
        """
        if not self._alertmanager_url:
            return {"error": "alertmanager_url not configured", "available": False}

        client = self._ensure_client()
        headers = await self._get_headers()

        try:
            response = await client.get(
                f"{self._alertmanager_url}/api/v2/status",
                headers=headers,
            )

            if response.status_code != 200:
                logger.warning(
                    "alertmanager_status_http_error",
                    status_code=response.status_code,
                    url=f"{self._alertmanager_url}/api/v2/status",
                )
                return {"error": f"HTTP {response.status_code}", "available": False}

            return cast("dict[str, Any]", response.json())

        except Exception as e:
            logger.warning(
                "alertmanager_status_error",
                error=str(e),
                error_type=type(e).__name__,
            )
            return {"error": str(e), "available": False}

    def _parse_alertmanager_response(
        self,
        alerts_data: list[dict[str, Any]],
    ) -> list[Alert]:
        """Parse Alertmanager API v2 response into Alert objects.

        Handles full Alertmanager API v2 alert format including:
        - labels, annotations
        - status (state, silencedBy, inhibitedBy, mutedBy)
        - receivers list
        - timestamps (startsAt, endsAt, updatedAt)
        - fingerprint, generatorURL

        Args:
            alerts_data: List of alert dictionaries from /api/v2/alerts.

        Returns:
            List of Alert objects with all fields populated.
        """
        alerts: list[Alert] = []

        for alert_data in alerts_data:
            labels = alert_data.get("labels", {})
            annotations = alert_data.get("annotations", {})
            status = alert_data.get("status", {})

            # Map severity
            sev_str = labels.get("severity", "warning").lower()
            severity = AlertSeverity.WARNING
            if sev_str == "critical":
                severity = AlertSeverity.CRITICAL
            elif sev_str in ("info", "informational"):
                severity = AlertSeverity.INFO
            elif sev_str == "page":
                severity = AlertSeverity.PAGE

            # Map state - Alertmanager uses "active" instead of "firing"
            state_str = status.get("state", "firing").lower()
            state = AlertState.FIRING
            if state_str in ("active", "firing"):
                state = AlertState.FIRING
            elif state_str == "pending":
                state = AlertState.PENDING
            elif state_str in ("resolved", "suppressed"):
                state = AlertState.RESOLVED

            # Parse timestamps
            starts_at = None
            if "startsAt" in alert_data:
                with contextlib.suppress(ValueError, TypeError):
                    starts_at = datetime.fromisoformat(
                        alert_data["startsAt"].replace("Z", "+00:00")
                    )

            ends_at = None
            if "endsAt" in alert_data:
                with contextlib.suppress(ValueError, TypeError):
                    ends_at = datetime.fromisoformat(alert_data["endsAt"].replace("Z", "+00:00"))

            updated_at = None
            if "updatedAt" in alert_data:
                with contextlib.suppress(ValueError, TypeError):
                    updated_at = datetime.fromisoformat(
                        alert_data["updatedAt"].replace("Z", "+00:00")
                    )

            # Extract receivers list (list of receiver objects with "name" key)
            receivers_data = alert_data.get("receivers", [])
            receivers: list[str] = []
            for recv in receivers_data:
                if isinstance(recv, dict) and "name" in recv:
                    receivers.append(recv["name"])
                elif isinstance(recv, str):
                    receivers.append(recv)

            # Extract silenced_by, inhibited_by, muted_by from status
            # These are lists of IDs/fingerprints
            silenced_by = status.get("silencedBy", []) or []
            inhibited_by = status.get("inhibitedBy", []) or []
            muted_by = status.get("mutedBy", []) or []

            alert = Alert(
                alert_name=labels.get("alertname", "Unknown"),
                severity=severity,
                state=state,
                summary=annotations.get("summary", ""),
                description=annotations.get("description", ""),
                labels=labels,
                annotations=annotations,
                starts_at=starts_at,
                ends_at=ends_at,
                fingerprint=alert_data.get("fingerprint", ""),
                generator_url=alert_data.get("generatorURL", ""),
                cluster_type=self._cluster_type,
                # Additional Alertmanager fields
                updated_at=updated_at,
                receivers=receivers,
                inhibited_by=inhibited_by,
                silenced_by=silenced_by,
                muted_by=muted_by,
            )
            alerts.append(alert)

        return alerts

    # -------------------------------------------------------------------------
    # OpenSearch Methods
    # -------------------------------------------------------------------------

    def _build_k8s_events_query(
        self,
        time_range_minutes: int = 60,
        namespaces: list[str] | None = None,
        event_reason: str | None = None,
        event_type_filter: str | None = None,
        involved_kind: str | None = None,
        keywords: list[str] | None = None,
        limit: int = 100,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """Build query for kubernetes_events-* index.

        Fields in kubernetes_events-* index:
        - kubernetes.event.reason: ProbeWarning, Failed, Created, Scheduled, etc.
        - kubernetes.event.type: Normal, Warning
        - kubernetes.event.message: Event description
        - kubernetes.event.involved_object.kind: Pod, Node, Deployment, etc.
        - kubernetes.event.involved_object.name: Object name
        - kubernetes.event.involved_object.namespace: Object namespace (alias for metadata.namespace)
        - kubernetes.event.metadata.namespace: Kubernetes namespace
        - kubernetes.event.count: Number of occurrences
        """
        must_clauses: list[dict[str, Any]] = []

        # Time range filter
        now = datetime.now(UTC)
        start = now - timedelta(minutes=time_range_minutes)
        must_clauses.append(
            {"range": {"@timestamp": {"gte": start.isoformat(), "lte": now.isoformat()}}}
        )

        # Namespace filter
        if namespaces:
            must_clauses.append(
                {
                    "bool": {
                        "should": [
                            {"term": {"kubernetes.event.metadata.namespace": ns}}
                            for ns in namespaces
                        ],
                        "minimum_should_match": 1,
                    }
                }
            )

        # Event reason filter
        if event_reason:
            must_clauses.append({"term": {"kubernetes.event.reason": event_reason}})

        # Event type filter (Normal or Warning)
        if event_type_filter:
            must_clauses.append({"term": {"kubernetes.event.type": event_type_filter}})

        # Involved object kind filter
        if involved_kind:
            must_clauses.append({"term": {"kubernetes.event.involved_object.kind": involved_kind}})

        # Keywords filter
        if keywords:
            keyword_clauses = [
                {"match_phrase": {"kubernetes.event.message": kw}} for kw in keywords
            ]
            must_clauses.append({"bool": {"should": keyword_clauses, "minimum_should_match": 1}})

        query: dict[str, Any] = {
            "query": {"bool": {"must": must_clauses}},
            "size": limit,
            "sort": [{"@timestamp": {"order": "desc"}}, {"_id": {"order": "desc"}}],
            "track_total_hits": True,
        }

        if cursor:
            import base64
            import json as json_module

            with contextlib.suppress(ValueError, TypeError):
                cursor_data = json_module.loads(base64.b64decode(cursor).decode("utf-8"))
                query["search_after"] = cursor_data

        return query

    def _build_audit_query(
        self,
        time_range_minutes: int = 60,
        hosts: list[str] | None = None,
        audit_provider: str | None = None,
        keywords: list[str] | None = None,
        limit: int = 100,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """Build query for audit* index.

        Fields in audit* index:
        - event.provider: sudo, sshd, auditd
        - event.source: journal
        - message: JSON containing sudo command, SSH login details, etc.
        - host.hostname: Host where event occurred
        """
        must_clauses: list[dict[str, Any]] = []

        # Time range filter
        now = datetime.now(UTC)
        start = now - timedelta(minutes=time_range_minutes)
        must_clauses.append(
            {"range": {"@timestamp": {"gte": start.isoformat(), "lte": now.isoformat()}}}
        )

        # Host filter
        if hosts:
            must_clauses.append(
                {
                    "bool": {
                        "should": [{"term": {"host.hostname": h}} for h in hosts],
                        "minimum_should_match": 1,
                    }
                }
            )

        # Audit provider filter (sudo, sshd, auditd)
        if audit_provider:
            must_clauses.append({"term": {"event.provider": audit_provider}})

        # Keywords filter
        if keywords:
            keyword_clauses = [{"match_phrase": {"message": kw}} for kw in keywords]
            must_clauses.append({"bool": {"should": keyword_clauses, "minimum_should_match": 1}})

        query: dict[str, Any] = {
            "query": {"bool": {"must": must_clauses}},
            "size": limit,
            "sort": [{"@timestamp": {"order": "desc"}}, {"_id": {"order": "desc"}}],
            "track_total_hits": True,
        }

        if cursor:
            import base64
            import json as json_module

            with contextlib.suppress(ValueError, TypeError):
                cursor_data = json_module.loads(base64.b64decode(cursor).decode("utf-8"))
                query["search_after"] = cursor_data

        return query

    def _build_notifications_query(
        self,
        time_range_minutes: int = 60,
        notification_event_type: str | None = None,
        notification_logger: str | None = None,
        keywords: list[str] | None = None,
        limit: int = 100,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """Build query for notification-* index.

        Fields in notification-* index:
        - event_type: compute.instance.create, volume.attach, etc.
        - Logger: nova, neutron, cinder, etc.
        - Payload: JSON containing event details
        - publisher_id: compute.hostname
        - priority: INFO, ERROR
        - Timestamp: Event timestamp
        """
        must_clauses: list[dict[str, Any]] = []

        # Time range filter (use Timestamp field for notifications)
        now = datetime.now(UTC)
        start = now - timedelta(minutes=time_range_minutes)
        must_clauses.append(
            {"range": {"Timestamp": {"gte": start.isoformat(), "lte": now.isoformat()}}}
        )

        # Event type filter
        if notification_event_type:
            must_clauses.append({"wildcard": {"event_type": f"*{notification_event_type}*"}})

        # Logger filter (nova, neutron, cinder)
        if notification_logger:
            must_clauses.append({"term": {"Logger": notification_logger}})

        # Keywords filter (search in Payload)
        if keywords:
            keyword_clauses = [{"match_phrase": {"Payload": kw}} for kw in keywords]
            must_clauses.append({"bool": {"should": keyword_clauses, "minimum_should_match": 1}})

        query: dict[str, Any] = {
            "query": {"bool": {"must": must_clauses}},
            "size": limit,
            "sort": [{"Timestamp": {"order": "desc"}}, {"_id": {"order": "desc"}}],
            "track_total_hits": True,
        }

        if cursor:
            import base64
            import json as json_module

            with contextlib.suppress(ValueError, TypeError):
                cursor_data = json_module.loads(base64.b64decode(cursor).decode("utf-8"))
                query["search_after"] = cursor_data

        return query

    def _parse_k8s_events_response(
        self,
        response_data: dict[str, Any],
    ) -> list[LogEntry]:
        """Parse kubernetes_events-* index response into LogEntry objects."""
        logs: list[LogEntry] = []
        hits = response_data.get("hits", {}).get("hits", [])

        for hit in hits:
            source = hit.get("_source", {})
            k8s_event = source.get("kubernetes", {}).get("event", {})

            # Parse timestamp
            ts_str = source.get("@timestamp", "")
            timestamp = datetime.now(UTC)
            if ts_str:
                with contextlib.suppress(ValueError, TypeError):
                    timestamp = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))

            # Map event type to severity
            event_type = k8s_event.get("type", "Normal")
            severity = LogSeverity.WARNING if event_type == "Warning" else LogSeverity.INFO

            # Extract involved object info
            involved = k8s_event.get("involved_object", {})
            metadata = k8s_event.get("metadata", {})

            message = k8s_event.get("message", "")
            service = k8s_event.get("source", {}).get("component", "kubernetes")
            host = k8s_event.get("source", {}).get("host", "")
            namespace = metadata.get("namespace")

            log_entry = LogEntry(
                timestamp=timestamp,
                message=message,
                severity=severity,
                service=service,
                host=host,
                cluster_type=self._cluster_type,
                namespace=namespace,
                extra={
                    "reason": k8s_event.get("reason"),
                    "event_type": event_type,
                    "count": k8s_event.get("count", 1),
                    "involved_object": {
                        "kind": involved.get("kind"),
                        "name": involved.get("name"),
                        "namespace": involved.get("namespace") or namespace,
                    },
                },
            )
            logs.append(log_entry)

        return logs

    def _parse_audit_response(
        self,
        response_data: dict[str, Any],
    ) -> list[LogEntry]:
        """Parse audit* index response into LogEntry objects."""
        logs: list[LogEntry] = []
        hits = response_data.get("hits", {}).get("hits", [])

        for hit in hits:
            source = hit.get("_source", {})

            # Parse timestamp
            ts_str = source.get("@timestamp", "")
            timestamp = datetime.now(UTC)
            if ts_str:
                with contextlib.suppress(ValueError, TypeError):
                    timestamp = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))

            event_obj = source.get("event", {})
            host_obj = source.get("host", {})

            message = source.get("message", "")
            service = event_obj.get("provider", "audit")
            host = host_obj.get("hostname", "")

            log_entry = LogEntry(
                timestamp=timestamp,
                message=message,
                severity=LogSeverity.INFO,  # Audit logs don't have severity
                service=service,
                host=host,
                cluster_type=self._cluster_type,
                event_source=event_obj.get("source"),
                event_provider=event_obj.get("provider"),
                extra={"audit": True},
            )
            logs.append(log_entry)

        return logs

    def _parse_notifications_response(
        self,
        response_data: dict[str, Any],
    ) -> list[LogEntry]:
        """Parse notification-* index response into LogEntry objects."""
        logs: list[LogEntry] = []
        hits = response_data.get("hits", {}).get("hits", [])

        for hit in hits:
            source = hit.get("_source", {})

            # Parse timestamp (use Timestamp field)
            ts_str = source.get("Timestamp", source.get("@timestamp", ""))
            timestamp = datetime.now(UTC)
            if ts_str:
                with contextlib.suppress(ValueError, TypeError):
                    timestamp = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))

            # Map priority to severity
            priority = source.get("priority", "INFO")
            severity = LogSeverity.ERROR if priority == "ERROR" else LogSeverity.INFO

            event_type = source.get("event_type", "")
            logger = source.get("Logger", "")
            publisher_id = source.get("publisher_id", "")
            payload = source.get("Payload", "")

            # Extract host from publisher_id (format: service.hostname)
            host = ""
            if "." in publisher_id:
                parts = publisher_id.split(".")
                if len(parts) >= 2:
                    host = parts[1]

            log_entry = LogEntry(
                timestamp=timestamp,
                message=f"[{event_type}] {payload[:500]}..."
                if len(payload) > 500
                else f"[{event_type}] {payload}",
                severity=severity,
                service=logger,
                host=host,
                cluster_type=self._cluster_type,
                extra={
                    "event_type": event_type,
                    "publisher_id": publisher_id,
                    "priority": priority,
                    "message_id": source.get("message_id"),
                    "notification": True,
                },
            )
            logs.append(log_entry)

        return logs

    def _build_opensearch_query(
        self,
        services: list[str] | None = None,
        severity: str | None = None,
        hosts: list[str] | None = None,
        time_range_minutes: int = 60,
        keywords: list[str] | None = None,
        request_id: str | None = None,
        limit: int = 100,
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
    ) -> dict[str, Any]:
        """Build OpenSearch DSL query for log search.

        Args:
            services: Filter by service/application names (orchestrator.labels.application).
            severity: Minimum severity level (log.level).
            hosts: Filter by host names (host.hostname).
            time_range_minutes: Time range in minutes.
            keywords: Keywords to search for in message.
            request_id: Filter by HTTP request ID (http.request.id).
            limit: Maximum results.
            namespaces: Filter by Kubernetes namespaces (orchestrator.namespace).
            containers: Filter by container names (container.name).
            pods: Filter by pod names (orchestrator.pod).
            providers: Filter by event providers (event.provider).
            http_methods: Filter by HTTP methods (GET, POST, etc.).
            http_status_codes: Filter by HTTP status codes (e.g., [500, 502, 503]).
            min_duration_ms: Minimum HTTP request duration in microseconds.
            max_duration_ms: Maximum HTTP request duration in microseconds.
            http_path: Filter by HTTP request path pattern.
            event_sources: Filter by event source (container, journal, file).

        Returns:
            OpenSearch DSL query dictionary.
        """
        # Known OpenStack services that run in 'openstack' namespace
        OPENSTACK_SERVICES = {
            "nova",
            "neutron",
            "cinder",
            "glance",
            "keystone",
            "heat",
            "octavia",
            "barbican",
            "designate",
            "manila",
            "placement",
            "horizon",
            "ironic",
            "magnum",
            "sahara",
            "trove",
            "aodh",
            "gnocchi",
            "panko",
            "ceilometer",
            "cloudkitty",
            "watcher",
            "congress",
            "mistral",
            "murano",
            "senlin",
            "zaqar",
            "vitrage",
            "blazar",
            "cyborg",
            "masakari",
            "freezer",
            "monasca",
            "tacker",
        }

        # Build must clauses for bool query
        must_clauses: list[dict[str, Any]] = []

        # Auto-detect OpenStack services and add namespace filter if not explicitly set
        effective_namespaces = namespaces
        if not namespaces and services:
            # Check if any requested service is an OpenStack service
            requested_openstack_services = [s for s in services if s.lower() in OPENSTACK_SERVICES]
            if requested_openstack_services:
                # Automatically filter by openstack namespace for better results
                effective_namespaces = ["openstack"]
                logger.debug(
                    "auto_adding_openstack_namespace",
                    services=requested_openstack_services,
                )

        # Time range filter
        now = datetime.now(UTC)
        start = now - timedelta(minutes=time_range_minutes)
        must_clauses.append(
            {
                "range": {
                    "@timestamp": {
                        "gte": start.isoformat(),
                        "lte": now.isoformat(),
                    }
                }
            }
        )

        # Namespace filter (CRITICAL - orchestrator.namespace)
        if effective_namespaces:
            must_clauses.append(
                {
                    "bool": {
                        "should": [
                            {"term": {"orchestrator.namespace": ns}} for ns in effective_namespaces
                        ],
                        "minimum_should_match": 1,
                    }
                }
            )

        # Service/application filter (IMPROVED: use wildcards for broader matching)
        if services:
            should_services: list[dict[str, Any]] = []
            for svc in services:
                svc_lower = svc.lower()
                # Match against orchestrator labels (flat_object requires match)
                should_services.append({"match": {"orchestrator.labels.application": svc_lower}})
                should_services.append({"match": {"orchestrator.labels.app": svc_lower}})
                # Use wildcard for container.name to match nova-api, nova-compute, etc.
                should_services.append({"wildcard": {"container.name": f"*{svc_lower}*"}})
                # Match event.provider which often contains the service name
                should_services.append({"wildcard": {"event.provider": f"*{svc_lower}*"}})
                # Also check orchestrator.pod for pod names like nova-api-7f46b47c7c-xxx
                should_services.append({"wildcard": {"orchestrator.pod": f"*{svc_lower}*"}})
                # Check message field for service name mentions
                should_services.append({"match_phrase": {"message": svc_lower}})
            must_clauses.append({"bool": {"should": should_services, "minimum_should_match": 1}})

        # Container filter (NEW - container.name)
        if containers:
            must_clauses.append(
                {
                    "bool": {
                        "should": [{"term": {"container.name": c}} for c in containers],
                        "minimum_should_match": 1,
                    }
                }
            )

        # Pod filter (NEW - orchestrator.pod)
        if pods:
            must_clauses.append(
                {
                    "bool": {
                        "should": [{"term": {"orchestrator.pod": p}} for p in pods],
                        "minimum_should_match": 1,
                    }
                }
            )

        # Provider filter (NEW - event.provider)
        if providers:
            must_clauses.append(
                {
                    "bool": {
                        "should": [{"term": {"event.provider": p}} for p in providers],
                        "minimum_should_match": 1,
                    }
                }
            )

        # Event source filter (NEW - event.source: container, journal, file)
        if event_sources:
            must_clauses.append(
                {
                    "bool": {
                        "should": [{"term": {"event.source": s}} for s in event_sources],
                        "minimum_should_match": 1,
                    }
                }
            )

        # Severity filter (FIXED: use correct log.level values)
        if severity:
            # Map severity to actual values in the index
            severity_levels = {
                "debug": ["debug", "trace"],
                "info": ["info", "notice"],
                "warning": ["warning", "warn"],
                "error": ["error", "fluentd_error"],
                "critical": ["critical", "fatal", "panic"],
            }
            # Build inclusive list (this level and above)
            inclusive_levels: list[str] = []
            severity_order = ["debug", "info", "warning", "error", "critical"]
            try:
                start_idx = severity_order.index(severity.lower())
                for lvl in severity_order[start_idx:]:
                    inclusive_levels.extend(severity_levels.get(lvl, []))
            except ValueError:
                inclusive_levels = severity_levels.get(severity.lower(), [])

            if inclusive_levels:
                must_clauses.append(
                    {
                        "bool": {
                            "should": [
                                {"term": {"log.level": level}} for level in inclusive_levels
                            ],
                            "minimum_should_match": 1,
                        }
                    }
                )

        # Host filter (FIXED: use host.hostname)
        if hosts:
            must_clauses.append(
                {
                    "bool": {
                        "should": [{"term": {"host.hostname": host}} for host in hosts],
                        "minimum_should_match": 1,
                    }
                }
            )

        # HTTP method filter (NEW - http.request.method)
        if http_methods:
            must_clauses.append(
                {
                    "bool": {
                        "should": [
                            {"term": {"http.request.method": m.upper()}} for m in http_methods
                        ],
                        "minimum_should_match": 1,
                    }
                }
            )

        # HTTP status code filter (NEW - http.response.status_code)
        if http_status_codes:
            must_clauses.append(
                {
                    "bool": {
                        "should": [
                            {"term": {"http.response.status_code": str(code)}}
                            for code in http_status_codes
                        ],
                        "minimum_should_match": 1,
                    }
                }
            )

        # HTTP duration filter (NEW - http.request.duration in microseconds)
        if min_duration_ms is not None or max_duration_ms is not None:
            duration_range: dict[str, Any] = {}
            if min_duration_ms is not None:
                # Convert ms to microseconds (the field stores microseconds)
                duration_range["gte"] = min_duration_ms * 1000
            if max_duration_ms is not None:
                duration_range["lte"] = max_duration_ms * 1000
            must_clauses.append({"range": {"http.request.duration": duration_range}})

        # HTTP path filter (NEW - http.request.path)
        if http_path:
            must_clauses.append({"wildcard": {"http.request.path": f"*{http_path}*"}})

        # Request ID filter (FIXED: use http.request.id and also search in message)
        if request_id:
            must_clauses.append(
                {
                    "bool": {
                        "should": [
                            {"term": {"http.request.id": request_id}},
                            {"match_phrase": {"message": request_id}},
                        ],
                        "minimum_should_match": 1,
                    }
                }
            )

        # Keywords filter (message search)
        if keywords:
            keyword_clauses = [{"match_phrase": {"message": kw}} for kw in keywords]
            must_clauses.append({"bool": {"should": keyword_clauses, "minimum_should_match": 1}})

        # Build the query with pagination support
        # Sort by timestamp + _id for stable cursor-based pagination
        query: dict[str, Any] = {
            "query": {"bool": {"must": must_clauses}},
            "size": limit,
            "sort": [
                {"@timestamp": {"order": "desc"}},
                {"_id": {"order": "desc"}},  # Secondary sort for stable pagination
            ],
            "track_total_hits": True,  # Get accurate total count
        }

        # Add search_after for cursor-based pagination
        if cursor:
            import base64
            import json as json_module

            try:
                cursor_data = json_module.loads(base64.b64decode(cursor).decode("utf-8"))
                query["search_after"] = cursor_data
            except (ValueError, TypeError) as e:
                logger.warning("invalid_pagination_cursor", cursor=cursor[:50], error=str(e))
                # Continue without cursor if invalid

        return query

    def _parse_opensearch_response(
        self,
        response_data: dict[str, Any],
    ) -> list[LogEntry]:
        """Parse OpenSearch response into LogEntry objects.

        Handles the StackLight .ds-system-* format with full field extraction:
        - Kubernetes: orchestrator.namespace, orchestrator.pod, container.name/id
        - Event: event.source, event.provider
        - HTTP: http.request.*, http.response.*, http.source.*
        - Host: host.hostname
        - Log: log.level, message

        Args:
            response_data: OpenSearch search response.

        Returns:
            List of LogEntry objects with all fields populated.
        """
        logs: list[LogEntry] = []
        hits = response_data.get("hits", {}).get("hits", [])

        for hit in hits:
            source = hit.get("_source", {})

            # Parse timestamp
            ts_str = source.get("@timestamp", "")
            timestamp = datetime.now(UTC)
            if ts_str:
                with contextlib.suppress(ValueError, TypeError):
                    # Handle various timestamp formats
                    ts_clean = ts_str.replace("Z", "+00:00")
                    # Remove nanoseconds if present (keep only microseconds)
                    if "." in ts_clean and "+" in ts_clean:
                        parts = ts_clean.split("+")
                        if len(parts[0].split(".")[-1]) > 6:
                            ts_clean = parts[0][:26] + "+" + parts[1]
                    timestamp = datetime.fromisoformat(ts_clean)

            # Parse severity from log.level
            log_obj = source.get("log", {})
            sev_str = (
                log_obj.get("level", "")
                or source.get("Severity", "")
                or source.get("level", "")
                or source.get("log_level", "")
            )
            sev_str = sev_str.upper() if isinstance(sev_str, str) else ""

            severity = LogSeverity.INFO
            if sev_str in ("ERROR", "ERR", "FLUENTD_ERROR"):
                severity = LogSeverity.ERROR
            elif sev_str in ("WARNING", "WARN"):
                severity = LogSeverity.WARNING
            elif sev_str in ("CRITICAL", "FATAL", "EMERGENCY", "PANIC"):
                severity = LogSeverity.CRITICAL
            elif sev_str in ("DEBUG", "TRACE"):
                severity = LogSeverity.DEBUG

            # Parse message
            message = source.get("message", "") or source.get("Payload", "")

            # Parse orchestrator fields (Kubernetes metadata)
            orchestrator = source.get("orchestrator", {})
            container_obj = source.get("container", {})
            event_obj = source.get("event", {})
            host_obj = source.get("host", {})
            http_obj = source.get("http", {})
            http_request = http_obj.get("request", {})
            http_response = http_obj.get("response", {})
            http_source = http_obj.get("source", {})

            # Extract orchestrator labels (flat_object type)
            orch_labels = orchestrator.get("labels", {})
            # Convert to dict[str, str] for type safety
            labels: dict[str, str] = {}
            if isinstance(orch_labels, dict):
                for k, v in orch_labels.items():
                    if isinstance(v, str):
                        labels[k] = v
                    else:
                        labels[k] = str(v)

            # Parse service from multiple sources
            service = (
                labels.get("application", "")
                or labels.get("app", "")
                or labels.get("app.kubernetes.io/name", "")
                or container_obj.get("name", "")
                or event_obj.get("provider", "")
                or source.get("programname", "")
            )

            # Parse host (hostname from host object)
            host = (
                host_obj.get("hostname", "")
                or host_obj.get("name", "")
                or source.get("hostname", "")
            )

            # Extract request ID from http.request.id or message
            request_id = http_request.get("id") or source.get("request_id")

            # Parse HTTP status code (can be int or string)
            http_status_code: int | None = None
            status_val = http_response.get("status_code")
            if status_val is not None:
                with contextlib.suppress(ValueError, TypeError):
                    http_status_code = int(status_val)

            # Parse HTTP duration (stored in microseconds)
            http_duration_us: int | None = None
            duration_val = http_request.get("duration")
            if duration_val is not None:
                with contextlib.suppress(ValueError, TypeError):
                    http_duration_us = int(duration_val)

            # Build extra fields for anything not mapped to specific fields
            extra: dict[str, Any] = {}
            if container_obj.get("image"):
                extra["image"] = container_obj["image"]

            log_entry = LogEntry(
                timestamp=timestamp,
                message=message,
                severity=severity,
                service=service,
                host=host,
                request_id=request_id,
                cluster_type=self._cluster_type,
                extra=extra,
                # Kubernetes/orchestrator fields
                namespace=orchestrator.get("namespace"),
                pod=orchestrator.get("pod"),
                container_name=container_obj.get("name"),
                container_id=container_obj.get("id"),
                labels=labels,
                # Event source fields
                event_source=event_obj.get("source"),
                event_provider=event_obj.get("provider"),
                # HTTP request fields
                http_method=http_request.get("method"),
                http_path=http_request.get("path"),
                http_status_code=http_status_code,
                http_duration_us=http_duration_us,
                http_source_address=http_source.get("address"),
            )
            logs.append(log_entry)

        return logs

    async def query_opensearch(
        self,
        services: list[str] | None = None,
        severity: str | None = None,
        hosts: list[str] | None = None,
        time_range_minutes: int = 60,
        keywords: list[str] | None = None,
        request_id: str | None = None,
        limit: int = 100,
        index_pattern: str = "system*",
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
        # Index-specific parameters
        index_type: str | None = None,  # system, audit, k8s_events, notifications
        # K8s events specific filters
        event_reason: str | None = None,
        event_type_filter: str | None = None,
        involved_kind: str | None = None,
        # Audit specific filters
        audit_provider: str | None = None,
        # Notifications specific filters
        notification_event_type: str | None = None,
        notification_logger: str | None = None,
    ) -> LogQueryResult:
        """Query OpenSearch for logs via Kibana/OpenSearch Dashboards IAM Proxy.

        Uses the Kibana console proxy API to query OpenSearch with OIDC authentication.
        The Kibana IAM proxy provides access via /api/console/proxy endpoint.

        Supports cursor-based pagination for efficient handling of large result sets.
        The cursor is a base64-encoded sort value that can be passed to subsequent
        requests to fetch the next page.

        Args:
            services: Filter by service/application names.
            severity: Minimum severity level.
            hosts: Filter by host names.
            time_range_minutes: Time range in minutes.
            keywords: Keywords to search for.
            request_id: Filter by request/correlation ID.
            limit: Maximum logs to return per page (default: 100, max: 500).
            index_pattern: OpenSearch index pattern (default: system*). Overridden if index_type is provided.
            namespaces: Filter by Kubernetes namespaces.
            containers: Filter by container names.
            pods: Filter by pod names.
            providers: Filter by event providers.
            http_methods: Filter by HTTP methods (GET, POST, etc.).
            http_status_codes: Filter by HTTP status codes.
            min_duration_ms: Minimum HTTP request duration in ms.
            max_duration_ms: Maximum HTTP request duration in ms.
            http_path: Filter by HTTP request path pattern.
            event_sources: Filter by event source (container, journal, file).
            cursor: Pagination cursor from previous response (base64-encoded).
            index_type: Index type to query (system, audit, k8s_events, notifications).
                If provided, overrides index_pattern with the appropriate pattern.
            event_reason: [k8s_events only] Filter by event reason (e.g., 'ProbeWarning', 'Failed').
            event_type_filter: [k8s_events only] Filter by event type ('Normal' or 'Warning').
            involved_kind: [k8s_events only] Filter by involved object kind (e.g., 'Pod', 'Node').
            audit_provider: [audit only] Filter by provider (e.g., 'sudo', 'sshd', 'auditd').
            notification_event_type: [notifications only] Filter by event type (e.g., 'compute.instance.create').
            notification_logger: [notifications only] Filter by logger (e.g., 'nova', 'neutron').

        Returns:
            LogQueryResult with logs, total_count, cursor for next page, and has_more flag.

        Raises:
            StackLightError: If query fails.
        """
        if not self._opensearch_url:
            raise StackLightError(
                "OpenSearch URL not configured",
                component="opensearch",
            )

        client = self._ensure_client()
        headers = await self._get_headers()
        headers["Content-Type"] = "application/json"
        # Required for Kibana/OpenSearch Dashboards API calls
        headers["osd-xsrf"] = "true"
        headers["kbn-xsrf"] = "true"

        # Determine actual index pattern from index_type
        actual_index_pattern = index_pattern
        if index_type:
            index_type_patterns = {
                "system": "system*",
                "audit": ".ds-audit*",
                "k8s_events": "kubernetes_events-*",
                "notifications": "notification-*",
            }
            actual_index_pattern = index_type_patterns.get(index_type, index_pattern)

        # Build the query based on index type
        if index_type == "k8s_events":
            query = self._build_k8s_events_query(
                time_range_minutes=time_range_minutes,
                namespaces=namespaces,
                event_reason=event_reason,
                event_type_filter=event_type_filter,
                involved_kind=involved_kind,
                keywords=keywords,
                limit=limit,
                cursor=cursor,
            )
        elif index_type == "audit":
            query = self._build_audit_query(
                time_range_minutes=time_range_minutes,
                hosts=hosts,
                audit_provider=audit_provider,
                keywords=keywords,
                limit=limit,
                cursor=cursor,
            )
        elif index_type == "notifications":
            query = self._build_notifications_query(
                time_range_minutes=time_range_minutes,
                notification_event_type=notification_event_type,
                notification_logger=notification_logger,
                keywords=keywords,
                limit=limit,
                cursor=cursor,
            )
        else:
            # Default to system* query (container/application logs)
            query = self._build_opensearch_query(
                services=services,
                severity=severity,
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
            )

        try:
            # Use Kibana console proxy API to query OpenSearch
            # The Kibana IAM proxy doesn't expose direct ES API, but the console proxy works
            url = f"{self._opensearch_url}/api/console/proxy"
            params = {
                "path": f"{actual_index_pattern}/_search",
                "method": "POST",
            }
            logger.debug(
                "opensearch_query_started",
                url=url,
                index_pattern=actual_index_pattern,
                index_type=index_type,
                query=str(query)[:200],
            )

            import json

            response = await client.post(
                url,
                params=params,
                content=json.dumps(query),
                headers=headers,
            )

            if response.status_code == 401:
                raise StackLightError(
                    "Authentication failed for OpenSearch",
                    component="opensearch",
                    details={"status": 401},
                )

            if response.status_code == 403:
                raise StackLightError(
                    "Access denied to OpenSearch (RBAC)",
                    component="opensearch",
                    details={"status": 403},
                )

            if response.status_code == 404:
                # Index might not exist - return empty results
                logger.warning(
                    "opensearch_index_not_found",
                    index_pattern=actual_index_pattern,
                )
                return LogQueryResult(logs=[], total_count=0, cursor=None, has_more=False)

            if response.status_code != 200:
                raise StackLightError(
                    f"OpenSearch query failed: HTTP {response.status_code}",
                    component="opensearch",
                    details={"status": response.status_code, "body": response.text[:500]},
                )

            data = response.json()

            # Parse response using appropriate parser based on index type
            if index_type == "k8s_events":
                logs = self._parse_k8s_events_response(data)
            elif index_type == "audit":
                logs = self._parse_audit_response(data)
            elif index_type == "notifications":
                logs = self._parse_notifications_response(data)
            else:
                # Default parser for system* index
                logs = self._parse_opensearch_response(data)

            # Extract total count (can be exact or lower_bound depending on track_total_hits)
            total_obj = data.get("hits", {}).get("total", {})
            if isinstance(total_obj, dict):
                total_count = total_obj.get("value", 0)
            else:
                total_count = int(total_obj) if total_obj else 0

            # Generate cursor for next page from last hit's sort values
            next_cursor: str | None = None
            has_more = False
            hits = data.get("hits", {}).get("hits", [])
            if hits and len(hits) >= limit:
                # More results may be available
                last_hit = hits[-1]
                sort_values = last_hit.get("sort")
                if sort_values:
                    import base64

                    next_cursor = base64.b64encode(json.dumps(sort_values).encode("utf-8")).decode(
                        "utf-8"
                    )
                    # Has more if we got a full page and there are more total
                    has_more = len(logs) < total_count

            logger.info(
                "opensearch_query_completed",
                hits=len(logs),
                total=total_count,
                has_more=has_more,
            )

            return LogQueryResult(
                logs=logs,
                total_count=total_count,
                cursor=next_cursor,
                has_more=has_more,
            )

        except Exception as e:
            if isinstance(e, StackLightError):
                raise
            logger.error("opensearch_query_error", error=str(e))
            raise StackLightError(
                f"Failed to query OpenSearch: {e}",
                component="opensearch",
            ) from e


# Singleton instance with thread-safe lock
_stacklight_adapter: StackLightAdapter | None = None
_stacklight_adapter_lock: asyncio.Lock | None = None


def _get_stacklight_adapter_lock() -> asyncio.Lock:
    """Get or create the singleton lock (handles event loop creation)."""
    global _stacklight_adapter_lock
    if _stacklight_adapter_lock is None:
        _stacklight_adapter_lock = asyncio.Lock()
    return _stacklight_adapter_lock


async def get_stacklight_adapter(
    direct_client: DirectStackLightClient,
    cluster_type: ClusterType = "mosk",
) -> StackLightAdapter:
    """Get or create the StackLight adapter singleton.

    Thread-safe: Uses asyncio.Lock to prevent race conditions
    when multiple coroutines try to create the adapter simultaneously.

    Args:
        direct_client: "DirectStackLightClient" for OIDC-based access (required).
        cluster_type: Type of cluster ("mcc" or "mosk").

    Returns:
        Connected StackLightAdapter instance.
    """
    global _stacklight_adapter

    # Fast path - already initialized
    if _stacklight_adapter is not None:
        return _stacklight_adapter

    # Slow path - need to initialize with lock
    async with _get_stacklight_adapter_lock():
        # Double-check after acquiring lock
        if _stacklight_adapter is None:
            _stacklight_adapter = StackLightAdapter(
                direct_client=direct_client,
                cluster_type=cluster_type,
            )
            await _stacklight_adapter.connect()

    return _stacklight_adapter


def reset_stacklight_adapter() -> None:
    """Reset the StackLight adapter singleton (for testing)."""
    global _stacklight_adapter, _stacklight_adapter_lock
    _stacklight_adapter = None
    _stacklight_adapter_lock = None
