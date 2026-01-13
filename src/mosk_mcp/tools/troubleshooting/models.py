"""Pydantic models for Intelligent Troubleshooting tools.

This module defines input/output models for all troubleshooting-related MCP tools,
providing log querying, event correlation, alert explanation, request tracing,
issue diagnosis, and diagnostic bundle generation.

All tools in this module are READ_ONLY safety level.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from mosk_mcp.tools.common.enums import AlertSeverity, AlertState, LogSeverity


class DiagnosisCategory(str, Enum):
    """Issue diagnosis categories."""

    VM_FAILURE = "vm_failure"
    NETWORK_ISSUE = "network_issue"
    STORAGE_ISSUE = "storage_issue"
    SERVICE_ISSUE = "service_issue"
    PERFORMANCE_ISSUE = "performance_issue"
    AUTHENTICATION_ISSUE = "authentication_issue"
    CONFIGURATION_ISSUE = "configuration_issue"


class IssuePriority(str, Enum):
    """Issue priority levels."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ResolutionConfidence(str, Enum):
    """Confidence level for resolution suggestions."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    EXPERIMENTAL = "experimental"


class BundleFormat(str, Enum):
    """Diagnostic bundle output format."""

    TARGZ = "tar.gz"
    ZIP = "zip"


class IndexType(str, Enum):
    """OpenSearch index types for different log sources.

    Each index type has a different structure and use case:
    - SYSTEM: Container/application logs (default)
    - AUDIT: Security audit logs (sudo, sshd, privileged access)
    - K8S_EVENTS: Kubernetes events (pod lifecycle, scheduling, probes)
    - NOTIFICATIONS: OpenStack notification bus events (instance lifecycle, etc.)
    """

    SYSTEM = "system"  # Default - container/app logs (system*)
    AUDIT = "audit"  # Security audit logs (audit*)
    K8S_EVENTS = "k8s_events"  # Kubernetes events (kubernetes_events-*)
    NOTIFICATIONS = "notifications"  # OpenStack notifications (notification-*)

    def to_index_pattern(self) -> str:
        """Convert to OpenSearch index pattern."""
        patterns = {
            IndexType.SYSTEM: "system*",
            IndexType.AUDIT: ".ds-audit*",  # Uses data stream
            IndexType.K8S_EVENTS: "kubernetes_events-*",
            IndexType.NOTIFICATIONS: "notification-*",
        }
        return patterns[self]


# =============================================================================
# query_logs models
# =============================================================================


class QueryLogsInput(BaseModel):
    """Input for query_logs tool."""

    model_config = ConfigDict(populate_by_name=True)

    query: str | None = Field(
        default=None,
        description="Natural language query like 'nova errors in last hour'",
    )
    services: list[str] | None = Field(
        default=None,
        description="Filter by service names (e.g., ['nova', 'neutron'])",
    )
    severity: LogSeverity | None = Field(
        default=None,
        description="Minimum severity level to return",
    )
    hosts: list[str] | None = Field(
        default=None,
        description="Filter by host names",
    )
    time_range_minutes: int = Field(
        default=60,
        description="Time range in minutes (default: 60)",
        ge=1,
        le=10080,  # Max 7 days
    )
    keywords: list[str] | None = Field(
        default=None,
        description="Additional keywords to search for",
    )
    project_id: str | None = Field(
        default=None,
        alias="projectId",
        description="Filter by OpenStack project/tenant ID",
    )
    request_id: str | None = Field(
        default=None,
        alias="requestId",
        description="Filter by request/correlation ID",
    )
    limit: int = Field(
        default=100,
        description="Maximum number of logs to return per page",
        ge=1,
        le=500,  # Reduced max for pagination
    )
    cursor: str | None = Field(
        default=None,
        description="Pagination cursor from previous response to fetch next page",
    )
    aggregation_only: bool = Field(
        default=False,
        alias="aggregationOnly",
        description="Return only aggregations without log entries (for large result sets)",
    )
    index_type: IndexType = Field(
        default=IndexType.SYSTEM,
        alias="indexType",
        description=(
            "OpenSearch index to query. Options: "
            "'system' (container logs, default), "
            "'audit' (security/sudo logs), "
            "'k8s_events' (Kubernetes events), "
            "'notifications' (OpenStack notifications)"
        ),
    )
    # K8s events specific filters
    event_reason: str | None = Field(
        default=None,
        alias="eventReason",
        description="Filter K8s events by reason (e.g., 'ProbeWarning', 'Failed', 'Created')",
    )
    event_type_filter: str | None = Field(
        default=None,
        alias="eventTypeFilter",
        description="Filter K8s events by type ('Normal' or 'Warning')",
    )
    involved_kind: str | None = Field(
        default=None,
        alias="involvedKind",
        description="Filter K8s events by involved object kind (e.g., 'Pod', 'Node')",
    )
    # Audit logs specific filters
    audit_provider: str | None = Field(
        default=None,
        alias="auditProvider",
        description="Filter audit logs by provider (e.g., 'sudo', 'sshd', 'auditd')",
    )
    # OpenStack notifications specific filters
    notification_event_type: str | None = Field(
        default=None,
        alias="notificationEventType",
        description="Filter notifications by event type (e.g., 'compute.instance.create')",
    )
    notification_logger: str | None = Field(
        default=None,
        alias="notificationLogger",
        description="Filter notifications by logger (e.g., 'nova', 'neutron')",
    )


# Constants for log handling
MAX_LOG_MESSAGE_LENGTH = 4096  # Max characters per log message before truncation
MAX_RESPONSE_SIZE_BYTES = 2 * 1024 * 1024  # 2MB max response size


class LogEntryInfo(BaseModel):
    """Information about a single log entry."""

    model_config = ConfigDict(populate_by_name=True)

    timestamp: str = Field(..., description="Log timestamp (ISO format)")
    message: str = Field(..., description="Log message (may be truncated if > 4KB)")
    severity: LogSeverity = Field(..., description="Log severity level")
    service: str = Field(..., description="Source service name")
    host: str = Field(..., description="Source host name")
    request_id: str | None = Field(
        None,
        alias="requestId",
        description="Request/correlation ID",
    )
    namespace: str | None = Field(
        None,
        description="Kubernetes namespace",
    )
    extra: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional fields",
    )
    message_truncated: bool = Field(
        default=False,
        alias="messageTruncated",
        description="Whether message was truncated due to size",
    )
    original_length: int | None = Field(
        default=None,
        alias="originalLength",
        description="Original message length if truncated",
    )


class QueryLogsOutput(BaseModel):
    """Output from query_logs tool with pagination support.

    For large result sets:
    - Use cursor to fetch subsequent pages
    - Set aggregation_only=True to get statistics without log entries
    - Individual log messages over 4KB are truncated
    """

    model_config = ConfigDict(populate_by_name=True)

    logs: list[LogEntryInfo] = Field(
        default_factory=list,
        description="List of log entries (empty if aggregation_only=True)",
    )
    total_count: int = Field(
        ...,
        alias="totalCount",
        description="Total logs matching query (may be estimate for large datasets)",
    )
    returned_count: int = Field(
        ...,
        alias="returnedCount",
        description="Number of logs returned in this response",
    )
    # Pagination fields
    cursor: str | None = Field(
        default=None,
        description="Cursor for fetching next page (null if no more results)",
    )
    has_more: bool = Field(
        default=False,
        alias="hasMore",
        description="Whether more results are available",
    )
    page_size_bytes: int = Field(
        default=0,
        alias="pageSizeBytes",
        description="Approximate size of this response in bytes",
    )
    # Aggregations (always included)
    query_info: dict[str, Any] = Field(
        default_factory=dict,
        alias="queryInfo",
        description="Parsed query information",
    )
    by_severity: dict[str, int] = Field(
        default_factory=dict,
        alias="bySeverity",
        description="Log count by severity (from total, not just this page)",
    )
    by_service: dict[str, int] = Field(
        default_factory=dict,
        alias="byService",
        description="Log count by service (from total, not just this page)",
    )
    by_host: dict[str, int] = Field(
        default_factory=dict,
        alias="byHost",
        description="Log count by host (from total, not just this page)",
    )
    time_range: dict[str, str] = Field(
        default_factory=dict,
        alias="timeRange",
        description="Query time range",
    )
    # Response metadata
    truncated_messages: int = Field(
        default=0,
        alias="truncatedMessages",
        description="Number of log messages that were truncated",
    )
    timestamp: str = Field(..., description="Query timestamp")


# =============================================================================
# correlate_events models
# =============================================================================


class CorrelateEventsInput(BaseModel):
    """Input for correlate_events tool."""

    model_config = ConfigDict(populate_by_name=True)

    anchor_time: str | None = Field(
        default=None,
        alias="anchorTime",
        description="Central time point for correlation (ISO format, default: now)",
    )
    window_minutes_before: int = Field(
        default=15,
        alias="windowMinutesBefore",
        description="Minutes before anchor time",
        ge=1,
        le=120,
    )
    window_minutes_after: int = Field(
        default=15,
        alias="windowMinutesAfter",
        description="Minutes after anchor time",
        ge=0,
        le=120,
    )
    services: list[str] | None = Field(
        default=None,
        description="Services to include (default: all)",
    )
    min_severity: LogSeverity = Field(
        default=LogSeverity.WARNING,
        alias="minSeverity",
        description="Minimum severity to include",
    )
    include_alerts: bool = Field(
        default=True,
        alias="includeAlerts",
        description="Include alerts in correlation",
    )
    include_metrics: bool = Field(
        default=False,
        alias="includeMetrics",
        description="Include metric anomalies",
    )


class CorrelatedEvent(BaseModel):
    """A single correlated event."""

    model_config = ConfigDict(populate_by_name=True)

    event_type: Literal["log", "alert", "metric"] = Field(
        ...,
        alias="eventType",
        description="Type of event",
    )
    timestamp: str = Field(..., description="Event timestamp")
    relative_seconds: int = Field(
        ...,
        alias="relativeSeconds",
        description="Seconds from anchor time (negative = before)",
    )
    service: str = Field(..., description="Service name")
    severity: str = Field(..., description="Severity level")
    message: str = Field(..., description="Event message/summary")
    host: str | None = Field(None, description="Host name")
    correlation_score: float = Field(
        default=0.0,
        ge=0,
        le=1,
        alias="correlationScore",
        description="Correlation relevance score (0-1)",
    )
    related_events: list[str] = Field(
        default_factory=list,
        alias="relatedEvents",
        description="IDs of related events",
    )


class EventCluster(BaseModel):
    """A cluster of related events."""

    model_config = ConfigDict(populate_by_name=True)

    cluster_id: str = Field(..., alias="clusterId", description="Cluster identifier")
    primary_service: str = Field(
        ...,
        alias="primaryService",
        description="Primary service involved",
    )
    event_count: int = Field(
        ...,
        alias="eventCount",
        description="Number of events in cluster",
    )
    time_span_seconds: int = Field(
        ...,
        alias="timeSpanSeconds",
        description="Time span of events",
    )
    events: list[CorrelatedEvent] = Field(
        default_factory=list,
        description="Events in this cluster",
    )
    likely_cause: str | None = Field(
        None,
        alias="likelyCause",
        description="Likely root cause if identifiable",
    )
    confidence: float = Field(
        default=0.0,
        ge=0,
        le=1,
        description="Confidence in cluster analysis (0-1)",
    )


class CorrelateEventsOutput(BaseModel):
    """Output from correlate_events tool."""

    model_config = ConfigDict(populate_by_name=True)

    anchor_time: str = Field(
        ...,
        alias="anchorTime",
        description="Anchor time used for correlation",
    )
    window_start: str = Field(
        ...,
        alias="windowStart",
        description="Start of time window",
    )
    window_end: str = Field(
        ...,
        alias="windowEnd",
        description="End of time window",
    )
    total_events: int = Field(
        ...,
        alias="totalEvents",
        description="Total events found",
    )
    events: list[CorrelatedEvent] = Field(
        default_factory=list,
        description="All correlated events (sorted by time)",
    )
    clusters: list[EventCluster] = Field(
        default_factory=list,
        description="Event clusters",
    )
    timeline_summary: list[str] = Field(
        default_factory=list,
        alias="timelineSummary",
        description="Human-readable timeline summary",
    )
    likely_root_cause: str | None = Field(
        None,
        alias="likelyRootCause",
        description="Most likely root cause",
    )
    recommendations: list[str] = Field(
        default_factory=list,
        description="Recommended next steps",
    )
    timestamp: str = Field(..., description="Query timestamp")


# =============================================================================
# explain_alert models
# =============================================================================


class ExplainAlertInput(BaseModel):
    """Input for explain_alert tool."""

    model_config = ConfigDict(populate_by_name=True)

    alert_name: str = Field(
        ...,
        alias="alertName",
        description="Name of the alert to explain",
    )
    alert_fingerprint: str | None = Field(
        default=None,
        alias="alertFingerprint",
        description="Specific alert instance fingerprint",
    )
    include_history: bool = Field(
        default=True,
        alias="includeHistory",
        description="Include alert history",
    )
    include_related_logs: bool = Field(
        default=True,
        alias="includeRelatedLogs",
        description="Include related log entries",
    )
    include_runbook: bool = Field(
        default=True,
        alias="includeRunbook",
        description="Include runbook/remediation steps",
    )


class AlertExplanation(BaseModel):
    """Detailed explanation of an alert."""

    model_config = ConfigDict(populate_by_name=True)

    alert_name: str = Field(..., alias="alertName", description="Alert name")
    severity: AlertSeverity = Field(..., description="Alert severity")
    state: AlertState = Field(..., description="Current state")
    summary: str = Field(..., description="Alert summary")
    description: str = Field(..., description="Detailed description")
    labels: dict[str, str] = Field(
        default_factory=dict,
        description="Alert labels",
    )
    annotations: dict[str, str] = Field(
        default_factory=dict,
        description="Alert annotations",
    )
    starts_at: str | None = Field(
        None,
        alias="startsAt",
        description="When alert started firing",
    )
    ends_at: str | None = Field(
        None,
        alias="endsAt",
        description="When alert ended (if resolved)",
    )
    duration_minutes: int | None = Field(
        None,
        alias="durationMinutes",
        description="How long alert has been firing",
    )


class AlertContext(BaseModel):
    """Context information for an alert."""

    model_config = ConfigDict(populate_by_name=True)

    what_it_means: str = Field(
        ...,
        alias="whatItMeans",
        description="Plain language explanation of what the alert indicates",
    )
    potential_impact: str = Field(
        ...,
        alias="potentialImpact",
        description="Potential impact if not addressed",
    )
    common_causes: list[str] = Field(
        default_factory=list,
        alias="commonCauses",
        description="Common causes for this alert",
    )
    affected_services: list[str] = Field(
        default_factory=list,
        alias="affectedServices",
        description="Services potentially affected",
    )
    affected_resources: list[str] = Field(
        default_factory=list,
        alias="affectedResources",
        description="Specific resources affected",
    )


class RemediationStep(BaseModel):
    """A remediation step."""

    model_config = ConfigDict(populate_by_name=True)

    step_number: int = Field(..., alias="stepNumber", description="Step number")
    action: str = Field(..., description="Action to take")
    command: str | None = Field(
        None,
        description="Command to run (if applicable)",
    )
    expected_result: str | None = Field(
        None,
        alias="expectedResult",
        description="Expected result after action",
    )
    requires_crq: bool = Field(
        default=False,
        alias="requiresCrq",
        description="Whether this action requires a CRQ",
    )


class ExplainAlertOutput(BaseModel):
    """Output from explain_alert tool."""

    model_config = ConfigDict(populate_by_name=True)

    alert: AlertExplanation = Field(..., description="Alert details")
    context: AlertContext = Field(..., description="Alert context")
    related_logs: list[LogEntryInfo] = Field(
        default_factory=list,
        alias="relatedLogs",
        description="Related log entries",
    )
    alert_history: list[dict[str, Any]] = Field(
        default_factory=list,
        alias="alertHistory",
        description="Recent alert history",
    )
    remediation_steps: list[RemediationStep] = Field(
        default_factory=list,
        alias="remediationSteps",
        description="Steps to remediate",
    )
    runbook_url: str | None = Field(
        None,
        alias="runbookUrl",
        description="URL to runbook documentation",
    )
    related_alerts: list[str] = Field(
        default_factory=list,
        alias="relatedAlerts",
        description="Other related alerts",
    )
    escalation_path: str | None = Field(
        None,
        alias="escalationPath",
        description="Who to escalate to",
    )
    timestamp: str = Field(..., description="Query timestamp")


# =============================================================================
# trace_request models
# =============================================================================


class TraceRequestInput(BaseModel):
    """Input for trace_request tool."""

    model_config = ConfigDict(populate_by_name=True)

    request_id: str = Field(
        ...,
        alias="requestId",
        description="Request/correlation ID to trace (X-Request-ID)",
    )
    time_range_minutes: int = Field(
        default=60,
        alias="timeRangeMinutes",
        description="Time range to search",
        ge=1,
        le=1440,
    )
    include_metrics: bool = Field(
        default=False,
        alias="includeMetrics",
        description="Include associated metrics",
    )


class TraceSpan(BaseModel):
    """A span in a request trace."""

    model_config = ConfigDict(populate_by_name=True)

    span_id: str = Field(..., alias="spanId", description="Span identifier")
    service: str = Field(..., description="Service name")
    operation: str = Field(..., description="Operation/function name")
    host: str = Field(..., description="Host where operation ran")
    start_time: str = Field(
        ...,
        alias="startTime",
        description="Span start time",
    )
    end_time: str | None = Field(
        None,
        alias="endTime",
        description="Span end time",
    )
    duration_ms: float = Field(
        ...,
        alias="durationMs",
        description="Duration in milliseconds",
    )
    status: Literal["success", "error", "timeout"] = Field(
        ...,
        description="Span status",
    )
    error_message: str | None = Field(
        None,
        alias="errorMessage",
        description="Error message if failed",
    )
    logs: list[LogEntryInfo] = Field(
        default_factory=list,
        description="Logs within this span",
    )
    tags: dict[str, str] = Field(
        default_factory=dict,
        description="Span tags/attributes",
    )


class TraceRequestOutput(BaseModel):
    """Output from trace_request tool."""

    model_config = ConfigDict(populate_by_name=True)

    request_id: str = Field(
        ...,
        alias="requestId",
        description="Traced request ID",
    )
    found: bool = Field(..., description="Whether trace was found")
    start_time: str | None = Field(
        None,
        alias="startTime",
        description="When request started",
    )
    end_time: str | None = Field(
        None,
        alias="endTime",
        description="When request ended",
    )
    total_duration_ms: float | None = Field(
        None,
        alias="totalDurationMs",
        description="Total request duration",
    )
    status: str = Field(..., description="Overall request status")
    services_involved: list[str] = Field(
        default_factory=list,
        alias="servicesInvolved",
        description="Services involved in request",
    )
    spans: list[TraceSpan] = Field(
        default_factory=list,
        description="Request spans (in order)",
    )
    error_span: TraceSpan | None = Field(
        None,
        alias="errorSpan",
        description="First span with error (if any)",
    )
    bottleneck_span: TraceSpan | None = Field(
        None,
        alias="bottleneckSpan",
        description="Slowest span",
    )
    trace_summary: str = Field(
        ...,
        alias="traceSummary",
        description="Human-readable trace summary",
    )
    recommendations: list[str] = Field(
        default_factory=list,
        description="Performance/debugging recommendations",
    )
    timestamp: str = Field(..., description="Query timestamp")


# =============================================================================
# diagnose_vm_failure models
# =============================================================================


class DiagnoseVMFailureInput(BaseModel):
    """Input for diagnose_vm_failure tool."""

    model_config = ConfigDict(populate_by_name=True)

    instance_id: str | None = Field(
        default=None,
        alias="instanceId",
        description="VM instance UUID (optional if name provided)",
    )
    instance_name: str | None = Field(
        default=None,
        alias="instanceName",
        description="VM instance name",
    )
    failure_type: (
        Literal[
            "spawn",
            "boot",
            "shutdown",
            "reboot",
            "resize",
            "migrate",
            "attach_volume",
            "detach_volume",
            "other",
        ]
        | None
    ) = Field(
        default=None,
        alias="failureType",
        description="Type of failure",
    )
    time_range_minutes: int = Field(
        default=60,
        alias="timeRangeMinutes",
        description="Time range to search for errors",
        ge=1,
        le=1440,
    )


class VMDiagnosisInfo(BaseModel):
    """Information about VM diagnosis."""

    model_config = ConfigDict(populate_by_name=True)

    instance_id: str = Field(..., alias="instanceId", description="VM instance UUID")
    instance_name: str = Field(..., alias="instanceName", description="VM name")
    project_id: str | None = Field(
        None,
        alias="projectId",
        description="Project/tenant ID",
    )
    host: str | None = Field(None, description="Compute host")
    vm_state: str | None = Field(
        None,
        alias="vmState",
        description="Current VM state",
    )
    task_state: str | None = Field(
        None,
        alias="taskState",
        description="Current task state",
    )
    power_state: str | None = Field(
        None,
        alias="powerState",
        description="Current power state",
    )


class DiagnosisFinding(BaseModel):
    """A diagnosis finding."""

    model_config = ConfigDict(populate_by_name=True)

    category: DiagnosisCategory = Field(..., description="Finding category")
    priority: IssuePriority = Field(..., description="Issue priority")
    title: str = Field(..., description="Finding title")
    description: str = Field(..., description="Detailed description")
    evidence: list[str] = Field(
        default_factory=list,
        description="Evidence supporting this finding",
    )
    affected_component: str = Field(
        ...,
        alias="affectedComponent",
        description="Affected component",
    )
    known_issue_id: str | None = Field(
        None,
        alias="knownIssueId",
        description="Related known issue ID (e.g., MOSK-001)",
    )


class DiagnoseVMFailureOutput(BaseModel):
    """Output from diagnose_vm_failure tool."""

    model_config = ConfigDict(populate_by_name=True)

    vm_info: VMDiagnosisInfo | None = Field(
        None,
        alias="vmInfo",
        description="VM information",
    )
    failure_detected: bool = Field(
        ...,
        alias="failureDetected",
        description="Whether failure was detected",
    )
    primary_diagnosis: DiagnosisFinding | None = Field(
        None,
        alias="primaryDiagnosis",
        description="Primary diagnosis",
    )
    additional_findings: list[DiagnosisFinding] = Field(
        default_factory=list,
        alias="additionalFindings",
        description="Additional findings",
    )
    related_logs: list[LogEntryInfo] = Field(
        default_factory=list,
        alias="relatedLogs",
        description="Related log entries",
    )
    timeline: list[str] = Field(
        default_factory=list,
        description="Event timeline",
    )
    root_cause_analysis: str = Field(
        ...,
        alias="rootCauseAnalysis",
        description="Root cause analysis summary",
    )
    recommended_actions: list[str] = Field(
        default_factory=list,
        alias="recommendedActions",
        description="Recommended actions",
    )
    timestamp: str = Field(..., description="Query timestamp")


# =============================================================================
# diagnose_network_issue models
# =============================================================================


class DiagnoseNetworkIssueInput(BaseModel):
    """Input for diagnose_network_issue tool."""

    model_config = ConfigDict(populate_by_name=True)

    source_ip: str | None = Field(
        default=None,
        alias="sourceIp",
        description="Source IP address",
    )
    destination_ip: str | None = Field(
        default=None,
        alias="destinationIp",
        description="Destination IP address",
    )
    port_id: str | None = Field(
        default=None,
        alias="portId",
        description="Neutron port ID",
    )
    network_id: str | None = Field(
        default=None,
        alias="networkId",
        description="Network ID",
    )
    instance_id: str | None = Field(
        default=None,
        alias="instanceId",
        description="Instance UUID",
    )
    symptom: str | None = Field(
        default=None,
        description="Description of the network issue",
    )
    time_range_minutes: int = Field(
        default=60,
        alias="timeRangeMinutes",
        description="Time range to search",
        ge=1,
        le=1440,
    )


class NetworkPathComponent(BaseModel):
    """A component in the network path."""

    model_config = ConfigDict(populate_by_name=True)

    component_type: str = Field(
        ...,
        alias="componentType",
        description="Type (instance, port, router, network, etc.)",
    )
    name: str = Field(..., description="Component name/ID")
    status: str = Field(..., description="Component status")
    issues: list[str] = Field(
        default_factory=list,
        description="Issues with this component",
    )


class DiagnoseNetworkIssueOutput(BaseModel):
    """Output from diagnose_network_issue tool."""

    model_config = ConfigDict(populate_by_name=True)

    issue_detected: bool = Field(
        ...,
        alias="issueDetected",
        description="Whether network issue was detected",
    )
    path_components: list[NetworkPathComponent] = Field(
        default_factory=list,
        alias="pathComponents",
        description="Network path components",
    )
    primary_diagnosis: DiagnosisFinding | None = Field(
        None,
        alias="primaryDiagnosis",
        description="Primary diagnosis",
    )
    additional_findings: list[DiagnosisFinding] = Field(
        default_factory=list,
        alias="additionalFindings",
        description="Additional findings",
    )
    agent_status: dict[str, str] = Field(
        default_factory=dict,
        alias="agentStatus",
        description="Status of relevant network agents",
    )
    related_logs: list[LogEntryInfo] = Field(
        default_factory=list,
        alias="relatedLogs",
        description="Related log entries",
    )
    connectivity_test_results: dict[str, Any] = Field(
        default_factory=dict,
        alias="connectivityTestResults",
        description="Results of connectivity checks",
    )
    root_cause_analysis: str = Field(
        ...,
        alias="rootCauseAnalysis",
        description="Root cause analysis",
    )
    recommended_actions: list[str] = Field(
        default_factory=list,
        alias="recommendedActions",
        description="Recommended actions",
    )
    timestamp: str = Field(..., description="Query timestamp")


# =============================================================================
# diagnose_storage_issue models
# =============================================================================


class DiagnoseStorageIssueInput(BaseModel):
    """Input for diagnose_storage_issue tool."""

    model_config = ConfigDict(populate_by_name=True)

    volume_id: str | None = Field(
        default=None,
        alias="volumeId",
        description="Cinder volume ID",
    )
    instance_id: str | None = Field(
        default=None,
        alias="instanceId",
        description="Instance UUID",
    )
    symptom: str | None = Field(
        default=None,
        description="Description of the storage issue",
    )
    include_ceph_status: bool = Field(
        default=True,
        alias="includeCephStatus",
        description="Include Ceph cluster status",
    )
    time_range_minutes: int = Field(
        default=60,
        alias="timeRangeMinutes",
        description="Time range to search",
        ge=1,
        le=1440,
    )


class StorageComponentStatus(BaseModel):
    """Status of a storage component."""

    model_config = ConfigDict(populate_by_name=True)

    component: str = Field(..., description="Component name")
    status: str = Field(..., description="Component status")
    health: str = Field(..., description="Health status")
    details: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional details",
    )


class DiagnoseStorageIssueOutput(BaseModel):
    """Output from diagnose_storage_issue tool."""

    model_config = ConfigDict(populate_by_name=True)

    issue_detected: bool = Field(
        ...,
        alias="issueDetected",
        description="Whether storage issue was detected",
    )
    volume_info: dict[str, Any] = Field(
        default_factory=dict,
        alias="volumeInfo",
        description="Volume information",
    )
    storage_backend_status: list[StorageComponentStatus] = Field(
        default_factory=list,
        alias="storageBackendStatus",
        description="Storage backend component status",
    )
    ceph_status: dict[str, Any] | None = Field(
        None,
        alias="cephStatus",
        description="Ceph cluster status summary",
    )
    primary_diagnosis: DiagnosisFinding | None = Field(
        None,
        alias="primaryDiagnosis",
        description="Primary diagnosis",
    )
    additional_findings: list[DiagnosisFinding] = Field(
        default_factory=list,
        alias="additionalFindings",
        description="Additional findings",
    )
    related_logs: list[LogEntryInfo] = Field(
        default_factory=list,
        alias="relatedLogs",
        description="Related log entries",
    )
    root_cause_analysis: str = Field(
        ...,
        alias="rootCauseAnalysis",
        description="Root cause analysis",
    )
    recommended_actions: list[str] = Field(
        default_factory=list,
        alias="recommendedActions",
        description="Recommended actions",
    )
    timestamp: str = Field(..., description="Query timestamp")


# =============================================================================
# get_known_issues models
# =============================================================================


class GetKnownIssuesInput(BaseModel):
    """Input for get_known_issues tool."""

    model_config = ConfigDict(populate_by_name=True)

    symptoms: list[str] | None = Field(
        default=None,
        description="List of symptoms to match against",
    )
    error_message: str | None = Field(
        default=None,
        alias="errorMessage",
        description="Error message to match",
    )
    service: str | None = Field(
        default=None,
        description="Service to filter by",
    )
    category: DiagnosisCategory | None = Field(
        default=None,
        description="Category to filter by",
    )
    include_resolved: bool = Field(
        default=False,
        alias="includeResolved",
        description="Include resolved/fixed issues",
    )
    limit: int = Field(
        default=10,
        description="Maximum issues to return",
        ge=1,
        le=50,
    )


class KnownIssue(BaseModel):
    """A known issue from the knowledge base."""

    model_config = ConfigDict(populate_by_name=True)

    issue_id: str = Field(..., alias="issueId", description="Issue ID (e.g., MOSK-001)")
    title: str = Field(..., description="Issue title")
    category: DiagnosisCategory = Field(..., description="Issue category")
    priority: IssuePriority = Field(..., description="Issue priority")
    symptoms: list[str] = Field(
        default_factory=list,
        description="Common symptoms",
    )
    root_cause: str = Field(..., alias="rootCause", description="Root cause")
    affected_services: list[str] = Field(
        default_factory=list,
        alias="affectedServices",
        description="Affected services",
    )
    affected_versions: list[str] = Field(
        default_factory=list,
        alias="affectedVersions",
        description="Affected MOSK versions",
    )
    resolution: str = Field(..., description="Resolution steps")
    workaround: str | None = Field(None, description="Temporary workaround")
    requires_crq: bool = Field(
        default=False,
        alias="requiresCrq",
        description="Whether fix requires CRQ",
    )
    documentation_url: str | None = Field(
        None,
        alias="documentationUrl",
        description="Link to documentation",
    )
    is_resolved_upstream: bool = Field(
        default=False,
        alias="isResolvedUpstream",
        description="Whether fixed in newer version",
    )
    match_score: float = Field(
        default=0.0,
        alias="matchScore",
        description="How well this matches input (0-1)",
    )


class GetKnownIssuesOutput(BaseModel):
    """Output from get_known_issues tool."""

    model_config = ConfigDict(populate_by_name=True)

    issues: list[KnownIssue] = Field(
        default_factory=list,
        description="Matching known issues",
    )
    total_matches: int = Field(
        ...,
        alias="totalMatches",
        description="Total matching issues",
    )
    best_match: KnownIssue | None = Field(
        None,
        alias="bestMatch",
        description="Best matching issue",
    )
    search_criteria: dict[str, Any] = Field(
        default_factory=dict,
        alias="searchCriteria",
        description="Search criteria used",
    )
    timestamp: str = Field(..., description="Query timestamp")


# =============================================================================
# suggest_resolution models
# =============================================================================


class SuggestResolutionInput(BaseModel):
    """Input for suggest_resolution tool."""

    model_config = ConfigDict(populate_by_name=True)

    error_message: str | None = Field(
        default=None,
        alias="errorMessage",
        description="Error message to analyze",
    )
    symptoms: list[str] | None = Field(
        default=None,
        description="List of observed symptoms",
    )
    affected_service: str | None = Field(
        default=None,
        alias="affectedService",
        description="Primary affected service",
    )
    context: dict[str, Any] | None = Field(
        default=None,
        description="Additional context (logs, status, etc.)",
    )
    include_preventive_measures: bool = Field(
        default=True,
        alias="includePreventiveMeasures",
        description="Include preventive measures",
    )


class ResolutionSuggestion(BaseModel):
    """A resolution suggestion."""

    model_config = ConfigDict(populate_by_name=True)

    title: str = Field(..., description="Suggestion title")
    description: str = Field(..., description="Detailed description")
    confidence: ResolutionConfidence = Field(
        ...,
        description="Confidence level",
    )
    steps: list[RemediationStep] = Field(
        default_factory=list,
        description="Resolution steps",
    )
    estimated_time_minutes: int | None = Field(
        None,
        alias="estimatedTimeMinutes",
        description="Estimated time to implement",
    )
    risk_level: Literal["low", "medium", "high"] = Field(
        ...,
        alias="riskLevel",
        description="Risk level of implementing this fix",
    )
    requires_downtime: bool = Field(
        default=False,
        alias="requiresDowntime",
        description="Whether fix requires downtime",
    )
    requires_crq: bool = Field(
        default=False,
        alias="requiresCrq",
        description="Whether fix requires CRQ",
    )
    related_known_issue: str | None = Field(
        None,
        alias="relatedKnownIssue",
        description="Related known issue ID",
    )


class PreventiveMeasure(BaseModel):
    """A preventive measure."""

    model_config = ConfigDict(populate_by_name=True)

    title: str = Field(..., description="Measure title")
    description: str = Field(..., description="Description")
    implementation_effort: Literal["low", "medium", "high"] = Field(
        ...,
        alias="implementationEffort",
        description="Effort to implement",
    )
    priority: IssuePriority = Field(..., description="Priority")


class SuggestResolutionOutput(BaseModel):
    """Output from suggest_resolution tool."""

    model_config = ConfigDict(populate_by_name=True)

    primary_suggestion: ResolutionSuggestion | None = Field(
        None,
        alias="primarySuggestion",
        description="Primary resolution suggestion",
    )
    alternative_suggestions: list[ResolutionSuggestion] = Field(
        default_factory=list,
        alias="alternativeSuggestions",
        description="Alternative approaches",
    )
    preventive_measures: list[PreventiveMeasure] = Field(
        default_factory=list,
        alias="preventiveMeasures",
        description="Preventive measures",
    )
    analysis_summary: str = Field(
        ...,
        alias="analysisSummary",
        description="Summary of analysis",
    )
    confidence_explanation: str = Field(
        ...,
        alias="confidenceExplanation",
        description="Why this confidence level",
    )
    escalation_recommended: bool = Field(
        default=False,
        alias="escalationRecommended",
        description="Whether escalation is recommended",
    )
    escalation_reason: str | None = Field(
        None,
        alias="escalationReason",
        description="Why escalation is recommended",
    )
    timestamp: str = Field(..., description="Query timestamp")


# =============================================================================
# create_diagnostic_bundle models
# =============================================================================


class CreateDiagnosticBundleInput(BaseModel):
    """Input for create_diagnostic_bundle tool."""

    model_config = ConfigDict(populate_by_name=True)

    bundle_name: str | None = Field(
        default=None,
        alias="bundleName",
        description="Name for the bundle (auto-generated if not provided)",
    )
    include_cluster_state: bool = Field(
        default=True,
        alias="includeClusterState",
        description="Include Kubernetes cluster state",
    )
    include_openstack_state: bool = Field(
        default=True,
        alias="includeOpenstackState",
        description="Include OpenStack service status",
    )
    include_ceph_state: bool = Field(
        default=True,
        alias="includeCephState",
        description="Include Ceph cluster state",
    )
    include_logs: bool = Field(
        default=True,
        alias="includeLogs",
        description="Include recent logs",
    )
    log_hours: int = Field(
        default=1,
        alias="logHours",
        description="Hours of logs to include",
        ge=1,
        le=24,
    )
    include_metrics: bool = Field(
        default=True,
        alias="includeMetrics",
        description="Include metrics snapshot",
    )
    include_alerts: bool = Field(
        default=True,
        alias="includeAlerts",
        description="Include alert history",
    )
    alert_hours: int = Field(
        default=24,
        alias="alertHours",
        description="Hours of alert history",
        ge=1,
        le=168,
    )
    affected_services: list[str] | None = Field(
        default=None,
        alias="affectedServices",
        description="Focus on specific services",
    )
    output_format: BundleFormat = Field(
        default=BundleFormat.TARGZ,
        alias="outputFormat",
        description="Bundle output format",
    )
    include_sensitive: bool = Field(
        default=False,
        alias="includeSensitive",
        description="Include potentially sensitive data (requires elevated permissions)",
    )


class BundleContents(BaseModel):
    """Summary of bundle contents."""

    model_config = ConfigDict(populate_by_name=True)

    cluster_state_files: list[str] = Field(
        default_factory=list,
        alias="clusterStateFiles",
        description="Cluster state files included",
    )
    openstack_state_files: list[str] = Field(
        default_factory=list,
        alias="openstackStateFiles",
        description="OpenStack state files included",
    )
    ceph_state_files: list[str] = Field(
        default_factory=list,
        alias="cephStateFiles",
        description="Ceph state files included",
    )
    log_files: list[str] = Field(
        default_factory=list,
        alias="logFiles",
        description="Log files included",
    )
    metrics_files: list[str] = Field(
        default_factory=list,
        alias="metricsFiles",
        description="Metrics files included",
    )
    alert_files: list[str] = Field(
        default_factory=list,
        alias="alertFiles",
        description="Alert files included",
    )
    total_files: int = Field(
        ...,
        alias="totalFiles",
        description="Total number of files",
    )


class CreateDiagnosticBundleOutput(BaseModel):
    """Output from create_diagnostic_bundle tool."""

    model_config = ConfigDict(populate_by_name=True)

    bundle_name: str = Field(
        ...,
        alias="bundleName",
        description="Bundle name",
    )
    bundle_id: str = Field(
        ...,
        alias="bundleId",
        description="Unique bundle identifier",
    )
    format: BundleFormat = Field(..., description="Bundle format")
    size_bytes: int = Field(
        ...,
        alias="sizeBytes",
        description="Bundle size in bytes",
    )
    size_human: str = Field(
        ...,
        alias="sizeHuman",
        description="Human-readable size",
    )
    contents: BundleContents = Field(..., description="Bundle contents summary")
    data_base64: str = Field(
        ...,
        alias="dataBase64",
        description="Base64-encoded bundle data",
    )
    created_at: str = Field(
        ...,
        alias="createdAt",
        description="When bundle was created",
    )
    expires_at: str | None = Field(
        None,
        alias="expiresAt",
        description="When bundle expires (if applicable)",
    )
    checksum_sha256: str = Field(
        ...,
        alias="checksumSha256",
        description="SHA256 checksum of bundle",
    )
    collection_duration_seconds: float = Field(
        ...,
        alias="collectionDurationSeconds",
        description="Time to collect data",
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="Warnings during collection",
    )
    timestamp: str = Field(..., description="Query timestamp")


# =============================================================================
# Get Pod Logs Models
# =============================================================================


class PodLogEntry(BaseModel):
    """Log entry from a single pod container."""

    model_config = ConfigDict(populate_by_name=True)

    pod_name: str = Field(
        ...,
        alias="podName",
        description="Name of the pod",
    )
    namespace: str = Field(..., description="Pod namespace")
    container: str | None = Field(
        None,
        description="Container name logs were retrieved from",
    )
    available_containers: list[str] = Field(
        default_factory=list,
        alias="availableContainers",
        description="List of all containers in the pod",
    )
    logs: str = Field(
        ...,
        description="Log content",
    )
    log_lines: int = Field(
        ...,
        alias="logLines",
        description="Number of log lines returned",
    )
    truncated: bool = Field(
        False,
        description="Whether logs were truncated due to size limits",
    )
    error: str | None = Field(
        None,
        description="Error message if log retrieval failed",
    )


class GetPodLogsOutput(BaseModel):
    """Output from get_pod_logs tool."""

    model_config = ConfigDict(populate_by_name=True)

    pods: list[PodLogEntry] = Field(
        default_factory=list,
        description="Log entries from each pod",
    )
    total_pods: int = Field(
        ...,
        alias="totalPods",
        description="Total number of pods logs were requested from",
    )
    successful_pods: int = Field(
        ...,
        alias="successfulPods",
        description="Number of pods logs were successfully retrieved from",
    )
    failed_pods: int = Field(
        ...,
        alias="failedPods",
        description="Number of pods where log retrieval failed",
    )
    total_log_lines: int = Field(
        ...,
        alias="totalLogLines",
        description="Total log lines across all pods",
    )
    query_info: dict[str, Any] = Field(
        default_factory=dict,
        alias="queryInfo",
        description="Information about the query parameters used",
    )
    timestamp: str = Field(..., description="Query timestamp")
