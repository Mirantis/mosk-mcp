"""List active StackLight alerts tool.

This module provides the list_active_alerts MCP tool for retrieving
active alerts from StackLight/Prometheus Alertmanager with filtering
and categorization.

Safety Level: Read-only

This tool queries Alertmanager via OIDC/SSO authentication using
DirectStackLightClient. Authentication must be established before
calling this tool.
"""

from __future__ import annotations

from datetime import UTC, datetime

from mosk_mcp.adapters.stacklight import DirectStackLightClient, StackLightAdapter
from mosk_mcp.observability.logging import get_logger
from mosk_mcp.tools.cluster_health.models import (
    AlertInfo,
    ListActiveAlertsInput,
    ListActiveAlertsOutput,
)
from mosk_mcp.tools.common.enums import AlertSeverity, AlertState
from mosk_mcp.tools.common.errors import tool_handler


logger = get_logger(__name__)


# Component classification based on alert labels
COMPONENT_KEYWORDS = {
    "kubernetes": ["kube", "node", "pod", "container", "kubelet", "etcd", "apiserver"],
    "openstack": ["nova", "neutron", "keystone", "glance", "cinder", "heat", "openstack"],
    "ceph": ["ceph", "osd", "mon", "mds", "rgw", "storage"],
    "stacklight": ["prometheus", "alertmanager", "opensearch", "grafana"],
}


def _classify_component(alert_name: str, labels: dict[str, str]) -> str:
    """Classify an alert into a component category.

    Args:
        alert_name: Name of the alert.
        labels: Alert labels.

    Returns:
        Component name (kubernetes, openstack, ceph, stacklight, or other).
    """
    # Check alert name and labels for component keywords
    search_text = alert_name.lower() + " " + " ".join(f"{k}={v}".lower() for k, v in labels.items())

    for component, keywords in COMPONENT_KEYWORDS.items():
        for keyword in keywords:
            if keyword.lower() in search_text:
                return component

    return "other"


def _severity_to_enum(severity: str) -> AlertSeverity:
    """Convert severity string to AlertSeverity enum.

    Args:
        severity: Severity string from Alertmanager.

    Returns:
        AlertSeverity enum value.
    """
    severity_lower = severity.lower()
    if severity_lower == "critical":
        return AlertSeverity.CRITICAL
    if severity_lower == "warning":
        return AlertSeverity.WARNING
    if severity_lower == "info":
        return AlertSeverity.INFO
    return AlertSeverity.NONE


@tool_handler("list_active_alerts")
async def list_active_alerts(
    direct_client: DirectStackLightClient,
    input_data: ListActiveAlertsInput,
) -> ListActiveAlertsOutput:
    """List active StackLight alerts with filtering and categorization.

    This tool retrieves active alerts from StackLight/Prometheus Alertmanager
    via OIDC/SSO authentication. The direct_client must be authenticated
    with valid Keycloak tokens before calling this tool.

    If Alertmanager is not available, returns an empty result set.

    Safety Level: Read-only

    Args:
        direct_client: Authenticated DirectStackLightClient for StackLight access.
        input_data: Input parameters for the query.

    Returns:
        ListActiveAlertsOutput with alert information.

    Raises:
        ToolExecutionError: If alert retrieval fails.

    Example:
        >>> alerts = await list_active_alerts(client, ListActiveAlertsInput())
        >>> print(f"Total alerts: {alerts.total_count}")
        >>> print(f"Critical: {alerts.critical_count}")
    """
    logger.info(
        "listing_active_alerts",
        severity_filter=input_data.severity_filter,
        component_filter=input_data.component_filter,
        include_silenced=input_data.include_silenced,
    )

    timestamp = datetime.now(UTC).isoformat()

    # Create StackLight adapter with direct client and query Alertmanager
    stacklight = StackLightAdapter(direct_client=direct_client)
    await stacklight.connect()
    raw_alerts = await stacklight.get_alerts(limit=input_data.limit)

    # Parse and filter alerts
    alerts: list[AlertInfo] = []
    critical_count = 0
    warning_count = 0
    info_count = 0
    silenced_count = 0
    by_component: dict[str, int] = {}
    by_severity: dict[str, int] = {}

    for raw_alert in raw_alerts:
        # Convert StackLight Alert to AlertInfo
        severity = _severity_to_enum(raw_alert.severity.value)
        state = AlertState.FIRING if raw_alert.state.value == "firing" else AlertState.PENDING
        component = _classify_component(raw_alert.alert_name, raw_alert.labels)

        alert = AlertInfo(
            alert_name=raw_alert.alert_name,
            severity=severity,
            state=state,
            summary=raw_alert.summary,
            description=raw_alert.description,
            component=component,
            source="alertmanager",
            labels=raw_alert.labels,
            annotations=raw_alert.annotations,
            starts_at=raw_alert.starts_at.isoformat() if raw_alert.starts_at else "",
            fingerprint=raw_alert.fingerprint,
            is_silenced=False,  # Silenced alerts are filtered out by the adapter
            silence_reason=None,
        )

        # Apply severity filter
        if input_data.severity_filter and alert.severity != input_data.severity_filter:
            continue

        # Apply component filter
        if input_data.component_filter and alert.component != input_data.component_filter:
            continue

        alerts.append(alert)

        # Count by severity
        if alert.severity == AlertSeverity.CRITICAL:
            critical_count += 1
        elif alert.severity == AlertSeverity.WARNING:
            warning_count += 1
        elif alert.severity == AlertSeverity.INFO:
            info_count += 1

        # Count by component
        by_component[alert.component] = by_component.get(alert.component, 0) + 1

        # Count by severity string
        sev_str = alert.severity.value
        by_severity[sev_str] = by_severity.get(sev_str, 0) + 1

    # Apply limit
    alerts = alerts[: input_data.limit]

    # Get most critical alert summaries
    critical_alerts = [a for a in alerts if a.severity == AlertSeverity.CRITICAL]
    most_critical = [a.summary for a in critical_alerts[:5]]

    output = ListActiveAlertsOutput(
        alerts=alerts,
        total_count=len(alerts),
        critical_count=critical_count,
        warning_count=warning_count,
        info_count=info_count,
        silenced_count=silenced_count,
        by_component=by_component,
        by_severity=by_severity,
        most_critical=most_critical,
        timestamp=timestamp,
    )

    logger.info(
        "alerts_listed",
        total_count=output.total_count,
        critical_count=critical_count,
        warning_count=warning_count,
    )

    return output
