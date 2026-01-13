"""Explain alert tool for intelligent troubleshooting.

This tool provides detailed explanations of alerts including context,
impact assessment, and remediation steps.

Safety Level: Read-only

This tool queries StackLight via OIDC/SSO authentication using
DirectStackLightClient. Authentication must be established before
calling this tool.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from mosk_mcp.adapters.stacklight import (
    Alert,
    DirectStackLightClient,
    StackLightAdapter,
)
from mosk_mcp.adapters.stacklight import (
    AlertSeverity as AdapterAlertSeverity,
)
from mosk_mcp.adapters.stacklight import (
    AlertState as AdapterAlertState,
)
from mosk_mcp.core.exceptions import ResourceNotFoundError, ToolExecutionError
from mosk_mcp.observability.logging import get_logger
from mosk_mcp.tools.common.enums import AlertSeverity, AlertState
from mosk_mcp.tools.troubleshooting.known_issues import get_known_issue_database
from mosk_mcp.tools.troubleshooting.models import (
    AlertContext,
    AlertExplanation,
    ExplainAlertOutput,
    LogEntryInfo,
    LogSeverity,
    RemediationStep,
)


logger = get_logger(__name__)


# Alert explanation knowledge base
ALERT_KNOWLEDGE: dict[str, dict[str, Any]] = {
    "HighCPUUsage": {
        "what_it_means": (
            "CPU usage on the host has exceeded the threshold for a sustained period. "
            "This indicates the host is under heavy computational load."
        ),
        "potential_impact": (
            "High CPU can cause VM performance degradation, slow API responses, "
            "and potential service timeouts. In severe cases, it may trigger "
            "health check failures."
        ),
        "common_causes": [
            "VM workload spike",
            "Runaway process",
            "Insufficient compute resources",
            "noisy neighbor effect",
            "Control plane operations (migrations, snapshots)",
        ],
        "remediation_steps": [
            ("Check top processes", "ssh compute-node 'top -bn1 | head -20'"),
            ("Identify high CPU VMs", "openstack server list --host compute-01 --long"),
            ("Check for migrations in progress", "openstack server migration list"),
            ("Review recent operations", "Check OpenStack logs for recent operations"),
        ],
        "escalation": "Infrastructure team if hardware-related",
    },
    "OSDDown": {
        "what_it_means": (
            "A Ceph OSD (Object Storage Daemon) is not responding. "
            "This affects the storage redundancy and may impact I/O performance."
        ),
        "potential_impact": (
            "Reduced storage redundancy, possible data unavailability if multiple OSDs fail, "
            "increased recovery load on remaining OSDs, potential for cascading failures."
        ),
        "common_causes": [
            "Disk failure",
            "OSD daemon crash",
            "Network connectivity issues",
            "Host failure",
            "Memory exhaustion on OSD host",
        ],
        "remediation_steps": [
            ("Check OSD status", "ceph osd tree"),
            ("Check OSD logs", "journalctl -u ceph-osd@<id> -n 100"),
            ("Check disk health", "smartctl -a /dev/<device>"),
            ("Restart OSD if daemon crashed", "systemctl restart ceph-osd@<id>"),
        ],
        "escalation": "Storage team",
    },
    "RabbitMQHighMemory": {
        "what_it_means": (
            "RabbitMQ message broker is using excessive memory. "
            "This can lead to message delivery delays or service disruption."
        ),
        "potential_impact": (
            "Message queue delays, RPC timeouts in OpenStack services, "
            "potential service failures if RabbitMQ crashes or starts rejecting connections."
        ),
        "common_causes": [
            "Message backlog due to slow consumers",
            "Connection leaks from OpenStack services",
            "Insufficient RabbitMQ resources",
            "Unacknowledged messages accumulating",
        ],
        "remediation_steps": [
            ("Check queue depths", "rabbitmqctl list_queues name messages"),
            ("Check connections", "rabbitmqctl list_connections | wc -l"),
            ("Identify slow consumers", "rabbitmqctl list_consumers"),
            ("Clear backlog if safe", "Restart affected OpenStack services"),
        ],
        "escalation": "Messaging team",
    },
    "NeutronAgentDown": {
        "what_it_means": (
            "A Neutron network agent is not responding. "
            "This can affect network operations like port creation, DHCP, or routing."
        ),
        "potential_impact": (
            "Network operations may fail, VMs may not get IP addresses, "
            "routing changes may not take effect, potential network isolation."
        ),
        "common_causes": [
            "Agent process crashed",
            "Lost connectivity to message queue",
            "OVS bridge issues",
            "Resource exhaustion on network node",
        ],
        "remediation_steps": [
            ("Check agent status", "openstack network agent list"),
            ("Check agent logs", "journalctl -u neutron-*-agent -n 100"),
            ("Check OVS status", "ovs-vsctl show"),
            ("Restart agent", "systemctl restart neutron-<type>-agent"),
        ],
        "escalation": "Network team",
    },
    "DiskSpaceWarning": {
        "what_it_means": (
            "Disk usage on the host has exceeded the warning threshold. "
            "Without action, the disk may fill up completely."
        ),
        "potential_impact": (
            "Service failures when disk is full, inability to write logs, "
            "potential data corruption, service degradation."
        ),
        "common_causes": [
            "Log file growth",
            "Leftover temporary files",
            "Backup files not cleaned up",
            "Growing database/index files",
        ],
        "remediation_steps": [
            ("Check disk usage", "df -h"),
            ("Find large files", "du -sh /* | sort -rh | head -20"),
            ("Clean old logs", "journalctl --vacuum-time=3d"),
            ("Remove temp files", "find /tmp -type f -mtime +7 -delete"),
        ],
        "escalation": "System administration team",
    },
    "CephSlowRequests": {
        "what_it_means": (
            "Ceph is reporting requests that are taking longer than expected to complete. "
            "This indicates storage performance degradation."
        ),
        "potential_impact": (
            "VM I/O latency, volume attachment delays, potential timeouts "
            "in cinder and nova operations."
        ),
        "common_causes": [
            "OSD disk I/O saturation",
            "Network congestion between OSDs",
            "Failing or degraded disks",
            "Recovery operations consuming bandwidth",
        ],
        "remediation_steps": [
            ("Check cluster status", "ceph -s"),
            ("Check OSD performance", "ceph osd perf"),
            ("Check for recovery", "ceph pg stat"),
            ("Identify slow OSDs", "ceph osd dump | grep 'slow'"),
        ],
        "escalation": "Storage team",
    },
}

# Default explanations for unknown alerts
DEFAULT_ALERT_KNOWLEDGE = {
    "what_it_means": (
        "This alert indicates an issue that requires investigation. "
        "Check the alert annotations and labels for more context."
    ),
    "potential_impact": (
        "Impact depends on the specific alert and affected services. "
        "Review the alert description for impact assessment."
    ),
    "common_causes": [
        "Service issue",
        "Resource constraint",
        "Configuration problem",
        "External dependency failure",
    ],
    "remediation_steps": [
        ("Check alert details", "Review alert annotations and labels"),
        ("Check service logs", "journalctl -u <service> -n 100"),
        ("Check service status", "systemctl status <service>"),
        ("Review documentation", "Check runbook URL in alert annotations"),
    ],
    "escalation": "On-call team",
}


async def explain_alert(
    direct_client: DirectStackLightClient,
    alert_name: str,
    alert_fingerprint: str | None = None,
    include_history: bool = True,
    include_related_logs: bool = True,
    include_runbook: bool = True,
) -> ExplainAlertOutput:
    """Explain an alert with context, impact assessment, and remediation steps.

    This tool provides comprehensive information about an alert via OIDC/SSO
    authentication including:
    - What the alert means in plain language
    - Potential impact if not addressed
    - Common causes
    - Step-by-step remediation instructions
    - Related log entries
    - Alert history

    The direct_client must be authenticated with valid Keycloak tokens
    before calling this tool.

    Safety Level: Read-only

    Args:
        direct_client: Authenticated DirectStackLightClient for StackLight access.
        alert_name: Name of the alert to explain.
        alert_fingerprint: Specific alert instance fingerprint (optional).
        include_history: Include alert history (default: True).
        include_related_logs: Include related log entries (default: True).
        include_runbook: Include runbook/remediation steps (default: True).

    Returns:
        ExplainAlertOutput with comprehensive alert explanation.

    Raises:
        ResourceNotFoundError: If alert is not found.
        ToolExecutionError: If explanation fails.

    Example:
        >>> result = await explain_alert(client, alert_name="OSDDown")

        >>> result = await explain_alert(
        ...     client,
        ...     alert_name="HighCPUUsage",
        ...     alert_fingerprint="fp-0001",
        ... )
    """
    logger.info(
        "explain_alert_started",
        alert_name=alert_name,
        fingerprint=alert_fingerprint,
    )

    try:
        # Create StackLight adapter with direct client
        stacklight = StackLightAdapter(direct_client=direct_client)
        await stacklight.connect()

        # Find the alert
        alert: Alert | None = None

        if alert_fingerprint:
            alert = await stacklight.get_alert_by_fingerprint(alert_fingerprint)
            if alert and alert.alert_name != alert_name:
                alert = None  # Fingerprint didn't match alert_name
        else:
            # Find by name
            alerts = await stacklight.get_alerts(limit=100)
            for a in alerts:
                if a.alert_name == alert_name:
                    alert = a
                    break

        if not alert:
            raise ResourceNotFoundError(
                f"Alert '{alert_name}' not found",
                resource_type="Alert",
                resource_id=alert_name,
            )

        # Map severity and state
        severity_map = {
            AdapterAlertSeverity.INFO: AlertSeverity.INFO,
            AdapterAlertSeverity.WARNING: AlertSeverity.WARNING,
            AdapterAlertSeverity.CRITICAL: AlertSeverity.CRITICAL,
            AdapterAlertSeverity.PAGE: AlertSeverity.PAGE,
        }
        state_map = {
            AdapterAlertState.FIRING: AlertState.FIRING,
            AdapterAlertState.PENDING: AlertState.PENDING,
            AdapterAlertState.RESOLVED: AlertState.RESOLVED,
        }

        # Calculate duration
        duration_minutes = None
        if alert.starts_at:
            end = alert.ends_at or datetime.now(UTC)
            duration_minutes = int((end - alert.starts_at).total_seconds() / 60)

        # Build alert explanation
        alert_explanation = AlertExplanation(
            alert_name=alert.alert_name,
            severity=severity_map.get(alert.severity, AlertSeverity.WARNING),
            state=state_map.get(alert.state, AlertState.FIRING),
            summary=alert.summary,
            description=alert.description,
            labels=alert.labels,
            annotations=alert.annotations,
            starts_at=alert.starts_at.isoformat() if alert.starts_at else None,
            ends_at=alert.ends_at.isoformat() if alert.ends_at else None,
            duration_minutes=duration_minutes,
        )

        # Get alert knowledge
        knowledge = ALERT_KNOWLEDGE.get(alert_name, DEFAULT_ALERT_KNOWLEDGE)

        # Determine affected services from labels
        affected_services = []
        service = alert.labels.get("service")
        if service:
            affected_services.append(service)

        # Build context
        context = AlertContext(
            what_it_means=knowledge["what_it_means"],
            potential_impact=knowledge["potential_impact"],
            common_causes=knowledge["common_causes"],
            affected_services=affected_services,
            affected_resources=[
                f"{k}: {v}"
                for k, v in alert.labels.items()
                if k not in ["severity", "service", "alertname"]
            ],
        )

        # Build remediation steps
        remediation_steps: list[RemediationStep] = []
        if include_runbook:
            for i, (action, command) in enumerate(knowledge["remediation_steps"], 1):
                step = RemediationStep(
                    step_number=i,
                    action=action,
                    command=command if not command.startswith("Check") else None,
                    expected_result=None,
                    requires_crq=False,
                )
                remediation_steps.append(step)

        # Get related logs - extract .logs from LogQueryResult
        related_logs: list[LogEntryInfo] = []
        if include_related_logs and service:
            log_result = await stacklight.query_logs(
                services=[service],
                severity="error",
                time_range_minutes=30,
                limit=10,
            )
            for log in log_result.logs:
                log_info = LogEntryInfo(
                    timestamp=log.timestamp.isoformat(),
                    message=log.message,
                    severity=LogSeverity(log.severity.value),
                    service=log.service,
                    host=log.host,
                    request_id=log.request_id,
                    namespace=log.namespace,
                )
                related_logs.append(log_info)

        # Build alert history from available alert data
        alert_history: list[dict[str, Any]] = []
        if include_history:
            # In production, this would query actual alert history
            alert_history = [
                {
                    "timestamp": alert.starts_at.isoformat() if alert.starts_at else None,
                    "event": "Alert started firing",
                    "state": "firing",
                },
            ]
            if alert.state == AdapterAlertState.RESOLVED and alert.ends_at:
                alert_history.append(
                    {
                        "timestamp": alert.ends_at.isoformat(),
                        "event": "Alert resolved",
                        "state": "resolved",
                    }
                )

        # Get runbook URL
        runbook_url = alert.annotations.get("runbook_url")

        # Check for known issues
        related_alerts: list[str] = []
        db = get_known_issue_database()
        if service:
            related_issues = db.get_by_service(service)
            for issue in related_issues[:3]:
                related_alerts.append(f"{issue.issue_id}: {issue.title}")

        result = ExplainAlertOutput(
            alert=alert_explanation,
            context=context,
            related_logs=related_logs,
            alert_history=alert_history,
            remediation_steps=remediation_steps,
            runbook_url=runbook_url,
            related_alerts=related_alerts,
            escalation_path=knowledge.get("escalation"),
            timestamp=datetime.now(UTC).isoformat(),
        )

        logger.info(
            "explain_alert_completed",
            alert_name=alert_name,
            state=alert_explanation.state.value,
        )

        return result

    except ResourceNotFoundError:
        raise
    except Exception as e:
        logger.error(
            "explain_alert_failed",
            error=str(e),
            alert_name=alert_name,
        )
        raise ToolExecutionError(
            f"Failed to explain alert: {e}",
            tool_name="explain_alert",
            phase="execution",
        ) from e


# Tool metadata for registration
TOOL_NAME = "explain_alert"
TOOL_DESCRIPTION = """Explain an alert with context, impact assessment, and remediation steps.

Provides comprehensive information including:
- Plain language explanation of what the alert means
- Potential impact if not addressed
- Common causes
- Step-by-step remediation instructions
- Related log entries and alerts

This is a READ-ONLY tool that does not modify any resources."""

TOOL_TAGS = ["troubleshooting", "alerts", "observability", "read-only"]
