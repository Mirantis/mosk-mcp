"""Get detailed alert information tool.

This module provides the get_alert_details MCP tool for retrieving
detailed information about a specific alert including context,
history, and suggested remediation actions.

Safety Level: Read-only

This tool queries Alertmanager via StackLight to retrieve real alert data.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from mosk_mcp.adapters.stacklight import DirectStackLightClient, StackLightAdapter
from mosk_mcp.core.exceptions import ResourceNotFoundError, ToolExecutionError
from mosk_mcp.observability.logging import get_logger
from mosk_mcp.tools.cluster_health.models import (
    AlertContext,
    AlertHistoryEntry,
    GetAlertDetailsOutput,
)
from mosk_mcp.tools.common.enums import AlertSeverity, AlertState


logger = get_logger(__name__)


# Runbook URLs for common alerts
RUNBOOKS = {
    "KubeNodeNotReady": "https://docs.mirantis.com/mosk/runbooks/node-not-ready",
    "CephOSDDown": "https://docs.mirantis.com/mosk/runbooks/ceph-osd-down",
    "NovaComputeDown": "https://docs.mirantis.com/mosk/runbooks/nova-compute-down",
    "CephCapacityWarning": "https://docs.mirantis.com/mosk/runbooks/ceph-capacity",
    "KubePodCrashLooping": "https://docs.mirantis.com/mosk/runbooks/pod-crashloop",
}

# Suggested actions for common alerts
SUGGESTED_ACTIONS = {
    "KubeNodeNotReady": [
        "Check node conditions: kubectl describe node <node-name>",
        "Check kubelet status: ssh to node and run 'systemctl status kubelet'",
        "Check kubelet logs: journalctl -u kubelet -f",
        "Verify network connectivity between node and control plane",
        "Check if node resources (disk, memory) are exhausted",
    ],
    "CephOSDDown": [
        "Check OSD status: ceph osd tree",
        "Check OSD pod status: kubectl get pods -n ceph -l app=rook-ceph-osd",
        "Check OSD logs: kubectl logs -n ceph <osd-pod-name>",
        "Verify underlying disk health",
        "Consider restarting the OSD if hardware is healthy",
    ],
    "NovaComputeDown": [
        "Check nova-compute service: openstack compute service list",
        "Check nova-compute pods: kubectl get pods -n openstack -l application=nova",
        "Check nova-compute logs for errors",
        "Verify libvirt is running on the compute node",
        "Check network connectivity to message queue",
    ],
    "CephCapacityWarning": [
        "Check cluster capacity: ceph df",
        "Identify pools with high usage: ceph osd pool stats",
        "Consider adding OSDs to expand capacity",
        "Review and clean up unused volumes/snapshots",
        "Implement quotas to prevent capacity overcommit",
    ],
    "KubePodCrashLooping": [
        "Check pod status: kubectl describe pod <pod-name> -n <namespace>",
        "Check pod logs: kubectl logs <pod-name> -n <namespace> --previous",
        "Check for resource constraints (OOMKilled)",
        "Verify ConfigMaps and Secrets are present",
        "Check for image pull issues",
    ],
}

# Related alerts mapping
RELATED_ALERTS = {
    "KubeNodeNotReady": ["PrometheusTargetDown", "KubeletDown", "NodeDiskPressure"],
    "CephOSDDown": ["CephPGsUnhealthy", "CephCapacityWarning", "CephSlowOps"],
    "NovaComputeDown": ["KubeNodeNotReady", "RabbitmqDown", "MariadbDown"],
    "CephCapacityWarning": ["CephOSDFull", "CephPoolNearFull"],
    "KubePodCrashLooping": ["ContainerOOMKilled", "ImagePullBackOff"],
}


async def _query_alert_from_alertmanager(
    direct_client: DirectStackLightClient,
    alert_name: str,
    fingerprint: str | None = None,
) -> dict[str, Any] | None:
    """Query alert details from Alertmanager.

    Args:
        direct_client: Authenticated StackLight client.
        alert_name: Name of the alert to find.
        fingerprint: Optional fingerprint for specific alert instance.

    Returns:
        Alert data or None if not found.

    Raises:
        Exception: If Alertmanager query fails (network error, auth error, etc.).
            Callers should handle exceptions to distinguish query failures
            from legitimate "alert not found" cases (which return None).
    """
    # Let exceptions propagate - callers should handle API failures
    stacklight = StackLightAdapter(direct_client=direct_client)
    await stacklight.connect()

    # Get all alerts from Alertmanager
    alerts = await stacklight.get_alerts(limit=500)

    # Find matching alert
    for alert in alerts:
        alert_dict = alert.to_dict()
        labels = alert_dict.get("labels", {})

        # Match by name
        if labels.get("alertname") == alert_name:
            # If fingerprint provided, also match that
            if fingerprint:
                if alert_dict.get("fingerprint") == fingerprint:
                    return alert_dict
            else:
                return alert_dict

    return None


async def _query_alert_history(
    direct_client: DirectStackLightClient,
    alert_name: str,
    hours: int = 24,
) -> list[AlertHistoryEntry]:
    """Query alert history from Prometheus.

    Args:
        direct_client: Authenticated StackLight client.
        alert_name: Name of the alert.
        hours: Hours of history to retrieve.

    Returns:
        List of history entries.
    """
    history: list[AlertHistoryEntry] = []

    try:
        stacklight = StackLightAdapter(direct_client=direct_client)
        await stacklight.connect()

        # Query ALERTS metric for history using query_prometheus_raw
        query = f'ALERTS{{alertname="{alert_name}"}}'
        samples = await stacklight.query_prometheus_raw(
            query=query,
            query_type="range",
            time_range_minutes=hours * 60,
            step_seconds=300,  # 5 minutes
        )

        # Process MetricSample results
        if samples:
            for sample in samples:
                ts = sample.timestamp.isoformat()
                val = sample.value
                state = AlertState.FIRING if val > 0 else AlertState.RESOLVED
                history.append(
                    AlertHistoryEntry(
                        timestamp=ts,
                        state=state,
                        value=val,
                    )
                )

    except Exception as e:
        logger.warning("alert_history_query_failed", error=str(e))

    return history


async def get_alert_details(
    direct_client: DirectStackLightClient,
    alert_name: str,
    fingerprint: str | None = None,
    include_history: bool = False,
    history_hours: int = 24,
) -> GetAlertDetailsOutput:
    """Get detailed information about a specific alert.

    This tool retrieves comprehensive information about an alert including
    its current state, context, affected resources, suggested remediation
    actions, and optionally its history.

    Safety Level: Read-only

    Args:
        kubernetes_adapter: Kubernetes adapter for cluster communication.
        input_data: Input parameters including alert name.

    Returns:
        GetAlertDetailsOutput with detailed alert information.

    Raises:
        ToolExecutionError: If alert details cannot be retrieved.
        ResourceNotFoundError: If alert is not found.

    Example:
        >>> details = await get_alert_details(
        ...     k8s_adapter, GetAlertDetailsInput(alert_name="CephOSDDown")
        ... )
        >>> print(f"Severity: {details.severity}")
        >>> print(f"Actions: {details.context.suggested_actions}")
    """
    logger.info(
        "getting_alert_details",
        alert_name=alert_name,
        fingerprint=fingerprint,
        include_history=include_history,
    )

    try:
        timestamp = datetime.now(UTC).isoformat()

        # Query real alert data from Alertmanager
        alert_data = await _query_alert_from_alertmanager(
            direct_client,
            alert_name,
            fingerprint,
        )

        if not alert_data:
            raise ResourceNotFoundError(
                message=f"Alert '{alert_name}' not found",
                resource_type="Alert",
                resource_id=alert_name,
            )

        # Parse alert data
        labels = alert_data.get("labels", {})
        annotations = alert_data.get("annotations", {})
        status = alert_data.get("status", {})

        alert_name_resolved = labels.get("alertname", alert_name)
        severity_str = labels.get("severity", "warning")
        severity = (
            AlertSeverity.CRITICAL
            if severity_str == "critical"
            else (AlertSeverity.WARNING if severity_str == "warning" else AlertSeverity.INFO)
        )

        state_str = status.get("state", "firing")
        state = AlertState.FIRING if state_str == "firing" else AlertState.PENDING

        starts_at = alert_data.get("startsAt", timestamp)
        ends_at = None if state == AlertState.FIRING else timestamp

        # Calculate duration
        try:
            start_dt = datetime.fromisoformat(starts_at.replace("Z", "+00:00"))
            duration_seconds = int((datetime.now(UTC) - start_dt).total_seconds())
        except (ValueError, TypeError):
            duration_seconds = 0

        # Build context
        runbook_url = RUNBOOKS.get(alert_name_resolved)
        suggested_actions = SUGGESTED_ACTIONS.get(
            alert_name_resolved,
            [
                "Check logs for the affected component",
                "Review recent changes that may have caused the issue",
                "Consult documentation for this alert type",
            ],
        )
        related_alerts = RELATED_ALERTS.get(alert_name_resolved, [])
        affected_resources = alert_data.get("affected_resources", [])

        context = AlertContext(
            affected_resources=affected_resources,
            related_alerts=related_alerts,
            runbook_url=runbook_url,
            suggested_actions=suggested_actions,
        )

        # Get history if requested
        history: list[AlertHistoryEntry] = []
        if include_history:
            history = await _query_alert_history(
                direct_client,
                alert_name_resolved,
                hours=history_hours,
            )

        # Check silencing
        silenced_by = status.get("silencedBy", [])
        is_silenced = len(silenced_by) > 0
        silence_id = silenced_by[0] if silenced_by else None
        silence_ends_at = None
        if is_silenced:
            # In production, would fetch silence details
            silence_ends_at = (datetime.now(UTC) + timedelta(hours=24)).isoformat()

        output = GetAlertDetailsOutput(
            alert_name=alert_name_resolved,
            severity=severity,
            state=state,
            summary=annotations.get("summary", alert_name_resolved),
            description=annotations.get("description", "No description available"),
            expression=alert_data.get("expression", "unknown"),
            current_value=alert_data.get("currentValue"),
            threshold=alert_data.get("threshold"),
            for_duration=alert_data.get("forDuration"),
            labels=labels,
            annotations=annotations,
            starts_at=starts_at,
            ends_at=ends_at,
            duration_seconds=duration_seconds,
            context=context,
            history=history,
            is_silenced=is_silenced,
            silence_id=silence_id,
            silence_ends_at=silence_ends_at,
            timestamp=timestamp,
        )

        logger.info(
            "alert_details_retrieved",
            alert_name=alert_name_resolved,
            severity=severity.value,
            state=state.value,
            duration_seconds=duration_seconds,
        )

        return output

    except ResourceNotFoundError:
        raise
    except Exception as e:
        logger.error("get_alert_details_failed", error=str(e))
        raise ToolExecutionError(
            message=f"Failed to get alert details: {e}",
            tool_name="get_alert_details",
            details={"error": str(e), "alert_name": alert_name},
        ) from e
