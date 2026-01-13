"""Intelligent Troubleshooting tools for MOSK MCP Server.

This module provides comprehensive troubleshooting capabilities including:
- Log querying with natural language support
- Event correlation across services
- Alert explanation and remediation guidance
- Request tracing across OpenStack services
- Diagnostic tools for VM, network, and storage issues
- Known issue matching and resolution suggestions
- Diagnostic bundle generation for support

All tools in this module are READ-ONLY and do not modify any resources.

Example:
    from mosk_mcp.tools.troubleshooting import (
        query_logs,
        correlate_events,
        explain_alert,
        trace_request,
        diagnose_vm_failure,
        diagnose_network_issue,
        diagnose_storage_issue,
        get_known_issues,
        suggest_resolution,
        create_diagnostic_bundle,
    )

    # Query logs with natural language
    result = await query_logs(
        k8s_adapter,
        query="nova errors in last hour",
    )

    # Diagnose VM failure
    result = await diagnose_vm_failure(
        k8s_adapter,
        instance_id="abc123",
        failure_type="spawn",
    )
"""

from __future__ import annotations

from mosk_mcp.tools.common.enums import AlertSeverity, AlertState
from mosk_mcp.tools.troubleshooting.correlate_events import (
    TOOL_DESCRIPTION as CORRELATE_EVENTS_DESCRIPTION,
)
from mosk_mcp.tools.troubleshooting.correlate_events import (
    TOOL_NAME as CORRELATE_EVENTS_NAME,
)
from mosk_mcp.tools.troubleshooting.correlate_events import (
    TOOL_TAGS as CORRELATE_EVENTS_TAGS,
)
from mosk_mcp.tools.troubleshooting.correlate_events import (
    correlate_events,
)
from mosk_mcp.tools.troubleshooting.create_diagnostic_bundle import (
    TOOL_DESCRIPTION as CREATE_DIAGNOSTIC_BUNDLE_DESCRIPTION,
)
from mosk_mcp.tools.troubleshooting.create_diagnostic_bundle import (
    TOOL_NAME as CREATE_DIAGNOSTIC_BUNDLE_NAME,
)
from mosk_mcp.tools.troubleshooting.create_diagnostic_bundle import (
    TOOL_TAGS as CREATE_DIAGNOSTIC_BUNDLE_TAGS,
)
from mosk_mcp.tools.troubleshooting.create_diagnostic_bundle import (
    create_diagnostic_bundle,
)
from mosk_mcp.tools.troubleshooting.diagnose_network_issue import (
    TOOL_DESCRIPTION as DIAGNOSE_NETWORK_ISSUE_DESCRIPTION,
)
from mosk_mcp.tools.troubleshooting.diagnose_network_issue import (
    TOOL_NAME as DIAGNOSE_NETWORK_ISSUE_NAME,
)
from mosk_mcp.tools.troubleshooting.diagnose_network_issue import (
    TOOL_TAGS as DIAGNOSE_NETWORK_ISSUE_TAGS,
)
from mosk_mcp.tools.troubleshooting.diagnose_network_issue import (
    diagnose_network_issue,
)
from mosk_mcp.tools.troubleshooting.diagnose_storage_issue import (
    TOOL_DESCRIPTION as DIAGNOSE_STORAGE_ISSUE_DESCRIPTION,
)
from mosk_mcp.tools.troubleshooting.diagnose_storage_issue import (
    TOOL_NAME as DIAGNOSE_STORAGE_ISSUE_NAME,
)
from mosk_mcp.tools.troubleshooting.diagnose_storage_issue import (
    TOOL_TAGS as DIAGNOSE_STORAGE_ISSUE_TAGS,
)
from mosk_mcp.tools.troubleshooting.diagnose_storage_issue import (
    diagnose_storage_issue,
)
from mosk_mcp.tools.troubleshooting.diagnose_vm_failure import (
    TOOL_DESCRIPTION as DIAGNOSE_VM_FAILURE_DESCRIPTION,
)
from mosk_mcp.tools.troubleshooting.diagnose_vm_failure import (
    TOOL_NAME as DIAGNOSE_VM_FAILURE_NAME,
)
from mosk_mcp.tools.troubleshooting.diagnose_vm_failure import (
    TOOL_TAGS as DIAGNOSE_VM_FAILURE_TAGS,
)
from mosk_mcp.tools.troubleshooting.diagnose_vm_failure import (
    diagnose_vm_failure,
)
from mosk_mcp.tools.troubleshooting.explain_alert import (
    TOOL_DESCRIPTION as EXPLAIN_ALERT_DESCRIPTION,
)
from mosk_mcp.tools.troubleshooting.explain_alert import (
    TOOL_NAME as EXPLAIN_ALERT_NAME,
)
from mosk_mcp.tools.troubleshooting.explain_alert import (
    TOOL_TAGS as EXPLAIN_ALERT_TAGS,
)
from mosk_mcp.tools.troubleshooting.explain_alert import (
    explain_alert,
)
from mosk_mcp.tools.troubleshooting.get_known_issues import (
    TOOL_DESCRIPTION as GET_KNOWN_ISSUES_DESCRIPTION,
)
from mosk_mcp.tools.troubleshooting.get_known_issues import (
    TOOL_NAME as GET_KNOWN_ISSUES_NAME,
)
from mosk_mcp.tools.troubleshooting.get_known_issues import (
    TOOL_TAGS as GET_KNOWN_ISSUES_TAGS,
)
from mosk_mcp.tools.troubleshooting.get_known_issues import (
    get_known_issues,
)
from mosk_mcp.tools.troubleshooting.get_pod_logs import (
    TOOL_DESCRIPTION as GET_POD_LOGS_DESCRIPTION,
)
from mosk_mcp.tools.troubleshooting.get_pod_logs import (
    TOOL_NAME as GET_POD_LOGS_NAME,
)
from mosk_mcp.tools.troubleshooting.get_pod_logs import (
    TOOL_TAGS as GET_POD_LOGS_TAGS,
)
from mosk_mcp.tools.troubleshooting.get_pod_logs import (
    get_pod_logs,
)
from mosk_mcp.tools.troubleshooting.known_issues import (
    KNOWN_ISSUES,
    IssuePattern,
    KnownIssueDatabase,
    get_known_issue_database,
    reset_known_issue_database,
)
from mosk_mcp.tools.troubleshooting.models import (
    AlertContext,
    AlertExplanation,
    BundleContents,
    BundleFormat,
    CorrelatedEvent,
    CorrelateEventsInput,
    CorrelateEventsOutput,
    CreateDiagnosticBundleInput,
    CreateDiagnosticBundleOutput,
    DiagnoseNetworkIssueInput,
    DiagnoseNetworkIssueOutput,
    DiagnoseStorageIssueInput,
    DiagnoseStorageIssueOutput,
    DiagnoseVMFailureInput,
    DiagnoseVMFailureOutput,
    DiagnosisCategory,
    DiagnosisFinding,
    EventCluster,
    ExplainAlertInput,
    ExplainAlertOutput,
    GetKnownIssuesInput,
    GetKnownIssuesOutput,
    GetPodLogsOutput,
    IssuePriority,
    KnownIssue,
    LogEntryInfo,
    LogSeverity,
    NetworkPathComponent,
    PodLogEntry,
    PreventiveMeasure,
    QueryLogsInput,
    QueryLogsOutput,
    RemediationStep,
    ResolutionConfidence,
    ResolutionSuggestion,
    StorageComponentStatus,
    SuggestResolutionInput,
    SuggestResolutionOutput,
    TraceRequestInput,
    TraceRequestOutput,
    TraceSpan,
    VMDiagnosisInfo,
)
from mosk_mcp.tools.troubleshooting.query_logs import (
    TOOL_DESCRIPTION as QUERY_LOGS_DESCRIPTION,
)
from mosk_mcp.tools.troubleshooting.query_logs import (
    TOOL_NAME as QUERY_LOGS_NAME,
)
from mosk_mcp.tools.troubleshooting.query_logs import (
    TOOL_TAGS as QUERY_LOGS_TAGS,
)
from mosk_mcp.tools.troubleshooting.query_logs import (
    query_logs,
)
from mosk_mcp.tools.troubleshooting.suggest_resolution import (
    TOOL_DESCRIPTION as SUGGEST_RESOLUTION_DESCRIPTION,
)
from mosk_mcp.tools.troubleshooting.suggest_resolution import (
    TOOL_NAME as SUGGEST_RESOLUTION_NAME,
)
from mosk_mcp.tools.troubleshooting.suggest_resolution import (
    TOOL_TAGS as SUGGEST_RESOLUTION_TAGS,
)
from mosk_mcp.tools.troubleshooting.suggest_resolution import (
    suggest_resolution,
)
from mosk_mcp.tools.troubleshooting.trace_request import (
    TOOL_DESCRIPTION as TRACE_REQUEST_DESCRIPTION,
)
from mosk_mcp.tools.troubleshooting.trace_request import (
    TOOL_NAME as TRACE_REQUEST_NAME,
)
from mosk_mcp.tools.troubleshooting.trace_request import (
    TOOL_TAGS as TRACE_REQUEST_TAGS,
)
from mosk_mcp.tools.troubleshooting.trace_request import (
    trace_request,
)


__all__ = [
    "CORRELATE_EVENTS_DESCRIPTION",
    "CORRELATE_EVENTS_NAME",
    "CORRELATE_EVENTS_TAGS",
    "CREATE_DIAGNOSTIC_BUNDLE_DESCRIPTION",
    "CREATE_DIAGNOSTIC_BUNDLE_NAME",
    "CREATE_DIAGNOSTIC_BUNDLE_TAGS",
    "DIAGNOSE_NETWORK_ISSUE_DESCRIPTION",
    "DIAGNOSE_NETWORK_ISSUE_NAME",
    "DIAGNOSE_NETWORK_ISSUE_TAGS",
    "DIAGNOSE_STORAGE_ISSUE_DESCRIPTION",
    "DIAGNOSE_STORAGE_ISSUE_NAME",
    "DIAGNOSE_STORAGE_ISSUE_TAGS",
    "DIAGNOSE_VM_FAILURE_DESCRIPTION",
    "DIAGNOSE_VM_FAILURE_NAME",
    "DIAGNOSE_VM_FAILURE_TAGS",
    "EXPLAIN_ALERT_DESCRIPTION",
    "EXPLAIN_ALERT_NAME",
    "EXPLAIN_ALERT_TAGS",
    "GET_KNOWN_ISSUES_DESCRIPTION",
    "GET_KNOWN_ISSUES_NAME",
    "GET_KNOWN_ISSUES_TAGS",
    "GET_POD_LOGS_DESCRIPTION",
    "GET_POD_LOGS_NAME",
    "GET_POD_LOGS_TAGS",
    # Known Issues
    "KNOWN_ISSUES",
    "QUERY_LOGS_DESCRIPTION",
    # Tool metadata
    "QUERY_LOGS_NAME",
    "QUERY_LOGS_TAGS",
    "SUGGEST_RESOLUTION_DESCRIPTION",
    "SUGGEST_RESOLUTION_NAME",
    "SUGGEST_RESOLUTION_TAGS",
    "TRACE_REQUEST_DESCRIPTION",
    "TRACE_REQUEST_NAME",
    "TRACE_REQUEST_TAGS",
    "AlertContext",
    "AlertExplanation",
    "AlertSeverity",
    "AlertState",
    "BundleContents",
    "BundleFormat",
    "CorrelateEventsInput",
    "CorrelateEventsOutput",
    "CorrelatedEvent",
    "CreateDiagnosticBundleInput",
    "CreateDiagnosticBundleOutput",
    "DiagnoseNetworkIssueInput",
    "DiagnoseNetworkIssueOutput",
    "DiagnoseStorageIssueInput",
    "DiagnoseStorageIssueOutput",
    "DiagnoseVMFailureInput",
    "DiagnoseVMFailureOutput",
    "DiagnosisCategory",
    "DiagnosisFinding",
    "EventCluster",
    "ExplainAlertInput",
    "ExplainAlertOutput",
    "GetKnownIssuesInput",
    "GetKnownIssuesOutput",
    "GetPodLogsOutput",
    "IssuePattern",
    "IssuePriority",
    "KnownIssue",
    "KnownIssueDatabase",
    # Models - Supporting
    "LogEntryInfo",
    # Models - Enums
    "LogSeverity",
    "NetworkPathComponent",
    "PodLogEntry",
    "PreventiveMeasure",
    # Models - Input
    "QueryLogsInput",
    # Models - Output
    "QueryLogsOutput",
    "RemediationStep",
    "ResolutionConfidence",
    "ResolutionSuggestion",
    "StorageComponentStatus",
    "SuggestResolutionInput",
    "SuggestResolutionOutput",
    "TraceRequestInput",
    "TraceRequestOutput",
    "TraceSpan",
    "VMDiagnosisInfo",
    "correlate_events",
    "create_diagnostic_bundle",
    "diagnose_network_issue",
    "diagnose_storage_issue",
    "diagnose_vm_failure",
    "explain_alert",
    "get_known_issue_database",
    "get_known_issues",
    "get_pod_logs",
    # Tools
    "query_logs",
    "reset_known_issue_database",
    "suggest_resolution",
    "trace_request",
]
