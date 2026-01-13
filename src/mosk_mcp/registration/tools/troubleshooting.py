"""Troubleshooting tools registration for MOSK MCP Server.

This module registers intelligent troubleshooting tools with the MCP server:
- query_logs: Search logs across OpenStack services
- correlate_events: Find related events across logs and alerts
- explain_alert: Explain alert with context and remediation
- trace_request: Trace request across services
- diagnose_vm_failure: Diagnose VM failures
- diagnose_network_issue: Diagnose network issues
- diagnose_storage_issue: Diagnose storage issues
- get_known_issues: Search known issues database
- suggest_resolution: AI-powered resolution suggestions
- create_diagnostic_bundle: Generate support bundle
- get_pod_logs: Get live K8s pod logs
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from pydantic import Field

from mosk_mcp.observability.logging import get_logger
from mosk_mcp.registration.utils import create_adapter_getters, with_logging_context
from mosk_mcp.tools.troubleshooting import (
    DiagnosisCategory,
    get_known_issues,
    suggest_resolution,
)
from mosk_mcp.tools.troubleshooting.models import BundleFormat, LogSeverity


if TYPE_CHECKING:
    from collections.abc import Callable

    from fastmcp import FastMCP

    from mosk_mcp.core.config import Settings
    from mosk_mcp.core.server_context import SSOServerContext


logger = get_logger(__name__)


def register_troubleshooting_tools(
    mcp: FastMCP, settings: Settings, context_getter: Callable[[], SSOServerContext | None]
) -> None:
    """Register intelligent troubleshooting tools with the MCP server.

    These tools provide comprehensive troubleshooting capabilities including
    log querying, event correlation, alert explanation, request tracing,
    issue diagnosis, and diagnostic bundle generation.

    All tools are READ_ONLY safety level.

    CLUSTER ROUTING:
    - All troubleshooting tools -> MOSK cluster (logs, alerts, diagnostics)

    NOTE: Tools that require StackLight access (query_logs, correlate_events,
    explain_alert, trace_request, diagnose_vm_failure, diagnose_network_issue,
    diagnose_storage_issue, create_diagnostic_bundle) require SSO mode to be
    enabled. In static kubeconfig mode, these tools will return an error
    indicating SSO is required.

    Args:
        mcp: FastMCP server instance.
        settings: Application settings.
        context_getter: Function that returns the current global SSOServerContext.
    """

    get_mosk, _get_mcc = create_adapter_getters(context_getter)

    # =========================================================================
    # Log Query and Correlation Tools
    # =========================================================================

    # query_logs - Search logs across services
    @mcp.tool(
        name="query_logs",
        description=(
            "Search logs across OpenStack services with natural language or structured filters. "
            "Supports queries like 'nova errors in last hour'. Supports pagination for large "
            "result sets via cursor parameter. "
            "Supports multiple OpenSearch index types: system (default container/app logs), "
            "k8s_events (Kubernetes events), audit (security/sudo logs), notifications (OpenStack events). "
            "Read-only operation."
        ),
    )
    async def _query_logs(
        query: str | None = Field(
            default=None, description="Natural language query like 'nova errors in last hour'"
        ),
        services: list[str] | None = Field(
            default=None, description="Filter by service names (e.g., ['nova', 'neutron'])"
        ),
        severity: Literal["debug", "info", "warning", "error", "critical"] | None = Field(
            default=None, description="Minimum severity level"
        ),
        hosts: list[str] | None = Field(default=None, description="Filter by host names"),
        time_range_minutes: int = Field(
            default=60, description="Time range in minutes", ge=1, le=10080
        ),
        keywords: list[str] | None = Field(
            default=None, description="Additional keywords to search for"
        ),
        request_id: str | None = Field(
            default=None, description="Filter by request/correlation ID"
        ),
        limit: int = Field(
            default=100, description="Maximum logs to return per page", ge=1, le=500
        ),
        namespaces: list[str] | None = Field(
            default=None,
            description="Filter by Kubernetes namespaces (e.g., ['openstack', 'stacklight'])",
        ),
        cursor: str | None = Field(
            default=None,
            description="Pagination cursor from previous response to fetch next page",
        ),
        aggregation_only: bool = Field(
            default=False,
            description="Return only statistics without log entries (useful for large result sets)",
        ),
        # Index-specific parameters
        index_type: Literal["system", "k8s_events", "audit", "notifications"] | None = Field(
            default=None,
            description=(
                "OpenSearch index type: "
                "'system' (default - container/app logs), "
                "'k8s_events' (Kubernetes events with reason/type/involved_object), "
                "'audit' (security audit - sudo/sshd/auditd), "
                "'notifications' (OpenStack notifications - instance lifecycle, etc.)"
            ),
        ),
        event_reason: str | None = Field(
            default=None,
            description="[k8s_events only] Filter by event reason (e.g., 'ProbeWarning', 'Failed', 'Scheduled')",
        ),
        event_type_filter: Literal["Normal", "Warning"] | None = Field(
            default=None,
            description="[k8s_events only] Filter by K8s event type ('Normal' or 'Warning')",
        ),
        involved_kind: str | None = Field(
            default=None,
            description="[k8s_events only] Filter by involved object kind (e.g., 'Pod', 'Node', 'Deployment')",
        ),
        audit_provider: Literal["sudo", "sshd", "auditd"] | None = Field(
            default=None,
            description="[audit only] Filter by audit provider (sudo, sshd, auditd)",
        ),
        notification_event_type: str | None = Field(
            default=None,
            description="[notifications only] Filter by event type (e.g., 'compute.instance.create', 'volume.attach')",
        ),
        notification_logger: str | None = Field(
            default=None,
            description="[notifications only] Filter by logger service (e.g., 'nova', 'neutron', 'cinder')",
        ),
    ) -> dict[str, Any]:
        """Query logs across services with pagination support."""
        async with with_logging_context("query_logs"):
            from mosk_mcp.tools.troubleshooting.models import LogSeverity
            from mosk_mcp.tools.troubleshooting.query_logs import query_logs as query_logs_impl

            # Convert string severity to enum
            severity_enum = None
            if severity:
                severity_enum = LogSeverity(severity)

            context = context_getter()
            if not context:
                raise RuntimeError("Server context not initialized")

            stacklight = await context.get_stacklight_client()

            # Get kubernetes adapter for kubectl fallback when OpenSearch is unavailable
            try:
                kubernetes_adapter = await get_mosk()
            except Exception as e:
                logger.warning(
                    "kubectl_fallback_unavailable",
                    error=str(e),
                    error_type=type(e).__name__,
                    message="MOSK adapter unavailable, kubectl log fallback disabled",
                )
                kubernetes_adapter = None

            result = await query_logs_impl(
                direct_client=stacklight,
                query=query,
                services=services,
                severity=severity_enum,
                hosts=hosts,
                time_range_minutes=time_range_minutes,
                keywords=keywords,
                request_id=request_id,
                limit=limit,
                kubernetes_adapter=kubernetes_adapter,
                namespaces=namespaces,
                cursor=cursor,
                aggregation_only=aggregation_only,
                # Index-specific parameters
                index_type=index_type,
                event_reason=event_reason,
                event_type_filter=event_type_filter,
                involved_kind=involved_kind,
                audit_provider=audit_provider,
                notification_event_type=notification_event_type,
                notification_logger=notification_logger,
            )
            return result.model_dump()

    # correlate_events - Find related events
    @mcp.tool(
        name="correlate_events",
        description=(
            "Find related events across logs and alerts within a time window. "
            "Correlates events to help identify patterns and root causes. Read-only operation."
        ),
    )
    async def _correlate_events(
        anchor_time: str | None = Field(
            default=None, description="Central time point (ISO format, default: now)"
        ),
        window_minutes_before: int = Field(
            default=15, description="Minutes before anchor time", ge=1, le=120
        ),
        window_minutes_after: int = Field(
            default=15, description="Minutes after anchor time", ge=0, le=120
        ),
        services: list[str] | None = Field(
            default=None, description="Services to include (default: all)"
        ),
        min_severity: Literal["debug", "info", "warning", "error", "critical"] = Field(
            default="warning", description="Minimum severity to include"
        ),
        include_alerts: bool = Field(default=True, description="Include alerts in correlation"),
        include_metrics: bool = Field(default=False, description="Include metric anomalies"),
    ) -> dict[str, Any]:
        """Correlate events across sources."""
        async with with_logging_context("correlate_events"):
            from mosk_mcp.tools.troubleshooting.correlate_events import (
                correlate_events as correlate_events_impl,
            )

            context = context_getter()
            if not context:
                raise RuntimeError("Server context not initialized")

            stacklight = await context.get_stacklight_client()
            result = await correlate_events_impl(
                direct_client=stacklight,
                anchor_time=anchor_time,
                window_minutes_before=window_minutes_before,
                window_minutes_after=window_minutes_after,
                services=services,
                min_severity=LogSeverity(min_severity),
                include_alerts=include_alerts,
                include_metrics=include_metrics,
            )
            return result.model_dump()

    # explain_alert - Explain alert with context
    @mcp.tool(
        name="explain_alert",
        description=(
            "Explain an alert with context, impact assessment, and remediation steps. "
            "Provides comprehensive information about what the alert means. Read-only operation."
        ),
    )
    async def _explain_alert(
        alert_name: str = Field(..., description="Name of the alert to explain"),
        alert_fingerprint: str | None = Field(
            default=None, description="Specific alert instance fingerprint"
        ),
        include_history: bool = Field(default=True, description="Include alert history"),
        include_related_logs: bool = Field(default=True, description="Include related log entries"),
        include_runbook: bool = Field(
            default=True, description="Include runbook/remediation steps"
        ),
    ) -> dict[str, Any]:
        """Explain an alert in detail."""
        async with with_logging_context("explain_alert"):
            from mosk_mcp.tools.troubleshooting.explain_alert import (
                explain_alert as explain_alert_impl,
            )

            context = context_getter()
            if not context:
                raise RuntimeError("Server context not initialized")

            stacklight = await context.get_stacklight_client()
            result = await explain_alert_impl(
                direct_client=stacklight,
                alert_name=alert_name,
                alert_fingerprint=alert_fingerprint,
                include_history=include_history,
                include_related_logs=include_related_logs,
                include_runbook=include_runbook,
            )
            return result.model_dump()

    # trace_request - Trace request across services
    @mcp.tool(
        name="trace_request",
        description=(
            "Trace an OpenStack request across services using correlation ID. "
            "Reconstructs the request timeline and identifies failures. Read-only operation."
        ),
    )
    async def _trace_request(
        request_id_param: str = Field(
            ..., description="Request/correlation ID to trace (X-Request-ID)"
        ),
        time_range_minutes: int = Field(
            default=60, description="Time range to search", ge=1, le=1440
        ),
        include_metrics: bool = Field(default=False, description="Include associated metrics"),
    ) -> dict[str, Any]:
        """Trace a request across services."""
        async with with_logging_context("trace_request"):
            from mosk_mcp.tools.troubleshooting.trace_request import (
                trace_request as trace_request_impl,
            )

            context = context_getter()
            if not context:
                raise RuntimeError("Server context not initialized")

            stacklight = await context.get_stacklight_client()
            result = await trace_request_impl(
                direct_client=stacklight,
                request_id=request_id_param,
                time_range_minutes=time_range_minutes,
                include_metrics=include_metrics,
            )
            return result.model_dump()

    # =========================================================================
    # Issue Diagnosis Tools
    # =========================================================================

    # diagnose_vm_failure - Diagnose VM failures
    @mcp.tool(
        name="diagnose_vm_failure",
        description=(
            "Diagnose VM creation or operation failures. Analyzes logs and state "
            "to determine why a VM operation failed. Read-only operation."
        ),
    )
    async def _diagnose_vm_failure(
        instance_id: str | None = Field(default=None, description="VM instance UUID"),
        instance_name: str | None = Field(default=None, description="VM instance name"),
        failure_type: Literal[
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
        | None = Field(default=None, description="Type of failure"),
        time_range_minutes: int = Field(
            default=60, description="Time range to search", ge=1, le=1440
        ),
    ) -> dict[str, Any]:
        """Diagnose VM failures."""
        async with with_logging_context("diagnose_vm_failure"):
            from mosk_mcp.tools.troubleshooting.diagnose_vm_failure import (
                diagnose_vm_failure as diagnose_vm_impl,
            )

            context = context_getter()
            if not context:
                raise RuntimeError("Server context not initialized")

            stacklight = await context.get_stacklight_client()
            result = await diagnose_vm_impl(
                direct_client=stacklight,
                instance_id=instance_id,
                instance_name=instance_name,
                failure_type=failure_type,
                time_range_minutes=time_range_minutes,
            )
            return result.model_dump()

    # diagnose_network_issue - Diagnose network issues
    @mcp.tool(
        name="diagnose_network_issue",
        description=(
            "Diagnose network connectivity issues. Analyzes network-related logs "
            "and component status to identify problems. Read-only operation."
        ),
    )
    async def _diagnose_network_issue(
        source_ip: str | None = Field(default=None, description="Source IP address"),
        destination_ip: str | None = Field(default=None, description="Destination IP address"),
        port_id: str | None = Field(default=None, description="Neutron port ID"),
        network_id: str | None = Field(default=None, description="Network ID"),
        instance_id: str | None = Field(default=None, description="Instance UUID"),
        symptom: str | None = Field(default=None, description="Description of the network issue"),
        time_range_minutes: int = Field(
            default=60, description="Time range to search", ge=1, le=1440
        ),
    ) -> dict[str, Any]:
        """Diagnose network issues."""
        async with with_logging_context("diagnose_network_issue"):
            from mosk_mcp.tools.troubleshooting.diagnose_network_issue import (
                diagnose_network_issue as diagnose_net_impl,
            )

            context = context_getter()
            if not context:
                raise RuntimeError("Server context not initialized")

            stacklight = await context.get_stacklight_client()
            result = await diagnose_net_impl(
                direct_client=stacklight,
                source_ip=source_ip,
                destination_ip=destination_ip,
                port_id=port_id,
                network_id=network_id,
                instance_id=instance_id,
                symptom=symptom,
                time_range_minutes=time_range_minutes,
            )
            return result.model_dump()

    # diagnose_storage_issue - Diagnose storage issues
    @mcp.tool(
        name="diagnose_storage_issue",
        description=(
            "Diagnose storage and volume issues. Analyzes storage-related logs, "
            "Ceph status, and component states. Read-only operation."
        ),
    )
    async def _diagnose_storage_issue(
        volume_id: str | None = Field(default=None, description="Cinder volume ID"),
        instance_id: str | None = Field(default=None, description="Instance UUID"),
        symptom: str | None = Field(default=None, description="Description of the storage issue"),
        include_ceph_status: bool = Field(default=True, description="Include Ceph cluster status"),
        time_range_minutes: int = Field(
            default=60, description="Time range to search", ge=1, le=1440
        ),
    ) -> dict[str, Any]:
        """Diagnose storage issues."""
        async with with_logging_context("diagnose_storage_issue"):
            from mosk_mcp.tools.troubleshooting.diagnose_storage_issue import (
                diagnose_storage_issue as diagnose_storage_impl,
            )

            context = context_getter()
            if not context:
                raise RuntimeError("Server context not initialized")

            stacklight = await context.get_stacklight_client()
            mosk_adapter = await get_mosk()
            result = await diagnose_storage_impl(
                direct_client=stacklight,
                kubernetes_adapter=mosk_adapter,
                volume_id=volume_id,
                instance_id=instance_id,
                symptom=symptom,
                include_ceph_status=include_ceph_status,
                time_range_minutes=time_range_minutes,
            )
            return result.model_dump()

    # =========================================================================
    # Knowledge Base and Resolution Tools
    # =========================================================================

    # get_known_issues - Search known issues database
    @mcp.tool(
        name="get_known_issues",
        description=(
            "Match symptoms against known issue database. Searches the knowledge base "
            "of known MOSK issues for relevant matches. Read-only operation."
        ),
    )
    async def _get_known_issues(
        symptoms: list[str] | None = Field(default=None, description="List of symptoms to match"),
        error_message: str | None = Field(default=None, description="Error message to search for"),
        service: str | None = Field(default=None, description="Service name to filter by"),
        category: Literal[
            "vm_failure",
            "network_issue",
            "storage_issue",
            "service_issue",
            "performance_issue",
            "authentication_issue",
            "configuration_issue",
        ]
        | None = Field(default=None, description="Issue category to filter by"),
        include_resolved: bool = Field(
            default=False, description="Include issues fixed in newer versions"
        ),
        limit: int = Field(default=10, description="Maximum issues to return", ge=1, le=50),
    ) -> dict[str, Any]:
        """Search known issues."""
        async with with_logging_context("get_known_issues"):
            k8s = await get_mosk()  # MOSK: Known issue lookup
            category_enum = None
            if category:
                category_enum = DiagnosisCategory(category)
            result = await get_known_issues(
                kubernetes_adapter=k8s,
                symptoms=symptoms,
                error_message=error_message,
                service=service,
                category=category_enum,
                include_resolved=include_resolved,
                limit=limit,
            )
            return result.model_dump()

    # suggest_resolution - AI-powered resolution suggestions
    @mcp.tool(
        name="suggest_resolution",
        description=(
            "Provide AI-powered resolution suggestions. Analyzes error messages "
            "and symptoms to suggest resolution steps. Read-only operation."
        ),
    )
    async def _suggest_resolution(
        error_message: str | None = Field(default=None, description="Error message to analyze"),
        symptoms: list[str] | None = Field(default=None, description="List of observed symptoms"),
        affected_service: str | None = Field(default=None, description="Primary affected service"),
        context: dict[str, Any] | None = Field(default=None, description="Additional context"),
        include_preventive_measures: bool = Field(
            default=True, description="Include preventive recommendations"
        ),
    ) -> dict[str, Any]:
        """Suggest resolution steps."""
        async with with_logging_context("suggest_resolution"):
            k8s = await get_mosk()  # MOSK: Resolution suggestions
            result = await suggest_resolution(
                kubernetes_adapter=k8s,
                error_message=error_message,
                symptoms=symptoms,
                affected_service=affected_service,
                context=context,
                include_preventive_measures=include_preventive_measures,
            )
            return result.model_dump()

    # =========================================================================
    # Diagnostic Bundle Tool
    # =========================================================================

    # create_diagnostic_bundle - Generate support bundle
    @mcp.tool(
        name="create_diagnostic_bundle",
        description=(
            "Generate a comprehensive diagnostic bundle for support. Collects cluster state, "
            "logs, metrics, and alerts. Returns base64-encoded archive. Read-only operation."
        ),
    )
    async def _create_diagnostic_bundle(
        bundle_name: str | None = Field(
            default=None, description="Name for the bundle (auto-generated if not provided)"
        ),
        include_cluster_state: bool = Field(
            default=True, description="Include Kubernetes cluster state"
        ),
        include_openstack_state: bool = Field(
            default=True, description="Include OpenStack service status"
        ),
        include_ceph_state: bool = Field(default=True, description="Include Ceph cluster state"),
        include_logs: bool = Field(default=True, description="Include recent logs"),
        log_hours: int = Field(default=1, description="Hours of logs to include", ge=1, le=24),
        include_metrics: bool = Field(default=True, description="Include metrics snapshot"),
        include_alerts: bool = Field(default=True, description="Include alert history"),
        alert_hours: int = Field(default=24, description="Hours of alert history", ge=1, le=168),
        affected_services: list[str] | None = Field(
            default=None, description="Focus on specific services"
        ),
        output_format: Literal["tar.gz", "zip"] = Field(
            default="tar.gz", description="Bundle output format"
        ),
        include_sensitive: bool = Field(
            default=False, description="Include potentially sensitive data"
        ),
    ) -> dict[str, Any]:
        """Create diagnostic bundle."""
        async with with_logging_context("create_diagnostic_bundle"):
            from mosk_mcp.tools.troubleshooting.create_diagnostic_bundle import (
                create_diagnostic_bundle as create_bundle_impl,
            )

            context = context_getter()
            if not context:
                raise RuntimeError("Server context not initialized")

            stacklight = await context.get_stacklight_client()
            mosk_adapter = await get_mosk()
            result = await create_bundle_impl(
                direct_client=stacklight,
                kubernetes_adapter=mosk_adapter,
                bundle_name=bundle_name,
                include_logs=include_logs,
                include_cluster_state=include_cluster_state,
                include_openstack_state=include_openstack_state,
                include_ceph_state=include_ceph_state,
                include_metrics=include_metrics,
                include_alerts=include_alerts,
                log_hours=log_hours,
                alert_hours=alert_hours,
                affected_services=affected_services,
                output_format=BundleFormat(output_format),
                include_sensitive=include_sensitive,
            )
            return result.model_dump()

    # =========================================================================
    # Pod Logs Tool (Live K8s Logs for RCA)
    # =========================================================================

    # get_pod_logs - Get live K8s pod logs
    @mcp.tool(
        name="get_pod_logs",
        description=(
            "Get live Kubernetes pod logs for RCA and troubleshooting. "
            "Retrieves container logs directly from Kubernetes API. Supports "
            "finding pods by name or label selector. Essential for debugging "
            "when StackLight/OpenSearch is unavailable or for real-time analysis. "
            "Read-only operation."
        ),
    )
    async def _get_pod_logs(
        pod_name: str | None = Field(default=None, description="Exact pod name to get logs from"),
        namespace: str | None = Field(
            default=None, description="Namespace to search in (defaults to 'openstack')"
        ),
        label_selector: str | None = Field(
            default=None,
            description="Label selector to find pods (e.g., 'application=nova', 'app=keystone')",
        ),
        container: str | None = Field(
            default=None,
            description="Container name (required if pod has multiple containers)",
        ),
        tail_lines: int | None = Field(
            default=500, description="Number of lines from end of logs to return", ge=1, le=10000
        ),
        since_seconds: int | None = Field(
            default=None,
            description="Return logs newer than this many seconds (e.g., 3600 for last hour)",
            ge=1,
            le=604800,
        ),
        previous: bool = Field(
            default=False,
            description="Get logs from previous terminated container (for crashed pods)",
        ),
        timestamps: bool = Field(
            default=False, description="Add RFC3339 timestamp to each log line"
        ),
    ) -> dict[str, Any]:
        """Get pod logs for RCA."""
        async with with_logging_context("get_pod_logs"):
            from mosk_mcp.tools.troubleshooting.get_pod_logs import (
                get_pod_logs as get_pod_logs_impl,
            )

            mosk_adapter = await get_mosk()
            result = await get_pod_logs_impl(
                kubernetes_adapter=mosk_adapter,
                pod_name=pod_name,
                namespace=namespace,
                label_selector=label_selector,
                container=container,
                tail_lines=tail_lines,
                since_seconds=since_seconds,
                previous=previous,
                timestamps=timestamps,
            )
            return result.model_dump()

    logger.debug("troubleshooting_tools_registered", count=11)
