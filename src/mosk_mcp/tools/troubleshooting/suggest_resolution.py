"""Suggest resolution tool for intelligent troubleshooting.

This tool provides AI-powered resolution suggestions based on
error messages, symptoms, and context analysis.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from mosk_mcp.core.exceptions import ToolExecutionError, ValidationError
from mosk_mcp.observability.logging import get_logger
from mosk_mcp.tools.troubleshooting.known_issues import (
    IssuePattern,
    get_known_issue_database,
)
from mosk_mcp.tools.troubleshooting.models import (
    IssuePriority,
    PreventiveMeasure,
    RemediationStep,
    ResolutionConfidence,
    ResolutionSuggestion,
    SuggestResolutionOutput,
)


if TYPE_CHECKING:
    from mosk_mcp.adapters.kubernetes import KubernetesAdapter


logger = get_logger(__name__)


# Resolution templates for common issues
RESOLUTION_TEMPLATES: dict[str, dict[str, Any]] = {
    "rpc_timeout": {
        "title": "Resolve RPC/Messaging Timeout",
        "description": (
            "RPC timeouts typically indicate RabbitMQ connectivity issues or "
            "service communication problems. These steps help restore messaging."
        ),
        "confidence": ResolutionConfidence.HIGH,
        "steps": [
            ("Check RabbitMQ status", "rabbitmqctl status"),
            ("Check connection count", "rabbitmqctl list_connections | wc -l"),
            ("Check queue depths", "rabbitmqctl list_queues name messages_ready"),
            ("Restart affected service", "systemctl restart <service>"),
            ("Monitor for recurrence", "tail -f /var/log/<service>/<service>.log"),
        ],
        "estimated_time": 15,
        "risk_level": "low",
        "requires_downtime": False,
        "requires_crq": False,
    },
    "osd_down": {
        "title": "Recover Failed Ceph OSD",
        "description": (
            "An OSD is down which reduces storage redundancy. "
            "These steps help identify the cause and recover the OSD."
        ),
        "confidence": ResolutionConfidence.HIGH,
        "steps": [
            ("Check OSD tree", "ceph osd tree"),
            ("Check OSD logs", "journalctl -u ceph-osd@<id> -n 100"),
            ("Check disk health", "smartctl -a /dev/<device>"),
            ("Restart OSD if daemon crashed", "systemctl restart ceph-osd@<id>"),
            ("Verify cluster recovery", "ceph -s"),
        ],
        "estimated_time": 30,
        "risk_level": "medium",
        "requires_downtime": False,
        "requires_crq": True,
    },
    "slow_requests": {
        "title": "Address Ceph Slow Requests",
        "description": (
            "Slow requests indicate storage I/O performance issues. "
            "These steps help identify bottlenecks and improve performance."
        ),
        "confidence": ResolutionConfidence.MEDIUM,
        "steps": [
            ("Check cluster status", "ceph -s"),
            ("Identify slow OSDs", "ceph osd perf"),
            ("Check for recovery operations", "ceph pg stat"),
            ("Check disk I/O", "iostat -x 1 5"),
            ("Review OSD configuration", "ceph config show osd.<id>"),
        ],
        "estimated_time": 45,
        "risk_level": "low",
        "requires_downtime": False,
        "requires_crq": False,
    },
    "libvirt_failure": {
        "title": "Restore Libvirt Connectivity",
        "description": (
            "Nova-compute cannot connect to libvirt. "
            "These steps restore the connection and verify VM operations."
        ),
        "confidence": ResolutionConfidence.HIGH,
        "steps": [
            ("Check libvirt status", "systemctl status libvirtd"),
            ("Check socket permissions", "ls -la /var/run/libvirt/libvirt-sock"),
            ("Restart libvirt", "systemctl restart libvirtd"),
            ("Restart nova-compute", "systemctl restart nova-compute"),
            ("Verify VM listing", "virsh list --all"),
        ],
        "estimated_time": 10,
        "risk_level": "medium",
        "requires_downtime": False,
        "requires_crq": False,
    },
    "volume_attach_failure": {
        "title": "Resolve Volume Attachment Issues",
        "description": (
            "Volume attachment is failing, often due to Ceph authentication "
            "or backend connectivity issues."
        ),
        "confidence": ResolutionConfidence.MEDIUM,
        "steps": [
            ("Check volume status", "openstack volume show <volume-id>"),
            ("Verify Ceph client auth", "ceph auth list"),
            ("Check libvirt secrets", "virsh secret-list"),
            ("Test RBD access", "rbd ls volumes"),
            ("Restart cinder-volume if needed", "systemctl restart cinder-volume"),
        ],
        "estimated_time": 20,
        "risk_level": "low",
        "requires_downtime": False,
        "requires_crq": True,
    },
    "network_agent_down": {
        "title": "Recover Network Agent",
        "description": (
            "A Neutron network agent is not responding. "
            "These steps help restore network operations."
        ),
        "confidence": ResolutionConfidence.HIGH,
        "steps": [
            ("Check agent status", "openstack network agent list"),
            ("Check agent logs", "journalctl -u neutron-*-agent -n 100"),
            ("Verify message queue connectivity", "rabbitmqctl list_connections"),
            ("Restart affected agent", "systemctl restart neutron-<type>-agent"),
            ("Verify OVS bridges", "ovs-vsctl show"),
        ],
        "estimated_time": 15,
        "risk_level": "medium",
        "requires_downtime": False,
        "requires_crq": False,
    },
    "generic": {
        "title": "General Troubleshooting Steps",
        "description": (
            "General troubleshooting approach for OpenStack issues. "
            "Follow these steps to gather more information."
        ),
        "confidence": ResolutionConfidence.LOW,
        "steps": [
            ("Check service status", "openstack service list"),
            ("Review relevant logs", "journalctl -u <service> -n 100"),
            ("Check resource constraints", "df -h && free -m"),
            ("Verify connectivity", "ping <dependent-service>"),
            ("Collect diagnostic bundle", "Use create_diagnostic_bundle tool"),
        ],
        "estimated_time": 30,
        "risk_level": "low",
        "requires_downtime": False,
        "requires_crq": False,
    },
}


async def suggest_resolution(
    kubernetes_adapter: KubernetesAdapter,
    error_message: str | None = None,
    symptoms: list[str] | None = None,
    affected_service: str | None = None,
    context: dict[str, Any] | None = None,
    include_preventive_measures: bool = True,
) -> SuggestResolutionOutput:
    """Provide AI-powered resolution suggestions.

    This tool analyzes error messages and symptoms to suggest
    resolution steps. It combines pattern matching against known
    issues with heuristic analysis to provide actionable guidance.

    Args:
        kubernetes_adapter: Kubernetes adapter (for consistency).
        error_message: Error message to analyze.
        symptoms: List of observed symptoms.
        affected_service: Primary affected service.
        context: Additional context (logs, status, etc.).
        include_preventive_measures: Include preventive recommendations.

    Returns:
        SuggestResolutionOutput with resolution suggestions.

    Raises:
        ValidationError: If no input provided.
        ToolExecutionError: If analysis fails.

    Example:
        result = await suggest_resolution(
            k8s,
            error_message="RPC timeout waiting for response from nova-compute",
            affected_service="nova",
        )
    """
    logger.info(
        "suggest_resolution_started",
        error_message=error_message[:50] if error_message else None,
        symptoms=symptoms,
        service=affected_service,
    )

    # Validate input
    if not any([error_message, symptoms]):
        raise ValidationError(
            "Either error_message or symptoms must be provided",
            field="input",
        )

    try:
        # Combine error message and symptoms for analysis
        analysis_text = ""
        if error_message:
            analysis_text += error_message.lower()
        if symptoms:
            analysis_text += " " + " ".join(s.lower() for s in symptoms)

        # Match against resolution templates
        template_key = _match_resolution_template(analysis_text, affected_service)
        template = RESOLUTION_TEMPLATES[template_key]

        # Check known issues database
        db = get_known_issue_database()
        known_matches = db.find_matching_issues(
            error_message=error_message,
            symptoms=symptoms,
            service=affected_service,
            limit=3,
        )

        # Build primary suggestion
        related_known_issue = None
        if known_matches:
            best_match = known_matches[0]
            if best_match[1] > 0.3:
                related_known_issue = best_match[0].issue_id

        steps = [
            RemediationStep(
                step_number=i,
                action=action,
                command=command if not command.startswith("Use") else None,
                expected_result=None,
                requires_crq=False,
            )
            for i, (action, command) in enumerate(template["steps"], 1)
        ]

        primary_suggestion = ResolutionSuggestion(
            title=template["title"],
            description=template["description"],
            confidence=template["confidence"],
            steps=steps,
            estimated_time_minutes=template["estimated_time"],
            risk_level=template["risk_level"],
            requires_downtime=template["requires_downtime"],
            requires_crq=template["requires_crq"],
            related_known_issue=related_known_issue,
        )

        # Build alternative suggestions from known issues
        alternative_suggestions: list[ResolutionSuggestion] = []
        for issue, score in known_matches[1:3]:  # Next 2 matches
            if score > 0.2:
                alt = ResolutionSuggestion(
                    title=f"Known Issue: {issue.title}",
                    description=issue.root_cause,
                    confidence=ResolutionConfidence.MEDIUM
                    if score > 0.4
                    else ResolutionConfidence.LOW,
                    steps=[
                        RemediationStep(
                            step_number=1,
                            action=f"Follow resolution for {issue.issue_id}",
                            command=None,
                            requires_crq=issue.requires_crq,
                        ),
                        RemediationStep(
                            step_number=2,
                            action=issue.resolution.split("\n")[0],
                            command=None,
                            requires_crq=issue.requires_crq,
                        ),
                    ],
                    estimated_time_minutes=30,
                    risk_level="medium",
                    requires_downtime=False,
                    requires_crq=issue.requires_crq,
                    related_known_issue=issue.issue_id,
                )
                alternative_suggestions.append(alt)

        # Build preventive measures
        preventive_measures: list[PreventiveMeasure] = []
        if include_preventive_measures:
            preventive_measures = _get_preventive_measures(template_key, affected_service)

        # Generate analysis summary
        analysis_summary = _generate_analysis_summary(
            template_key=template_key,
            error_message=error_message,
            symptoms=symptoms,
            known_matches=known_matches,
        )

        # Determine confidence explanation
        confidence_explanation = _get_confidence_explanation(
            template["confidence"],
            template_key,
            known_matches,
        )

        # Determine if escalation is needed
        escalation_recommended = template_key == "generic" or (
            len(known_matches) > 0 and known_matches[0][1] < 0.3
        )
        escalation_reason = None
        if escalation_recommended:
            escalation_reason = (
                "Issue does not match known patterns well. "
                "Consider escalating to subject matter experts."
            )

        result = SuggestResolutionOutput(
            primary_suggestion=primary_suggestion,
            alternative_suggestions=alternative_suggestions,
            preventive_measures=preventive_measures,
            analysis_summary=analysis_summary,
            confidence_explanation=confidence_explanation,
            escalation_recommended=escalation_recommended,
            escalation_reason=escalation_reason,
            timestamp=datetime.now(UTC).isoformat(),
        )

        logger.info(
            "suggest_resolution_completed",
            template=template_key,
            confidence=template["confidence"].value,
        )

        return result

    except ValidationError:
        raise
    except Exception as e:
        logger.error(
            "suggest_resolution_failed",
            error=str(e),
        )
        raise ToolExecutionError(
            f"Failed to suggest resolution: {e}",
            tool_name="suggest_resolution",
            phase="execution",
        ) from e


def _match_resolution_template(
    analysis_text: str,
    affected_service: str | None,
) -> str:
    """Match analysis text to resolution template."""
    # Priority patterns
    patterns = [
        ("rpc_timeout", ["rpc timeout", "messaging", "rabbitmq", "amqp"]),
        ("osd_down", ["osd down", "osd out", "ceph osd"]),
        ("slow_requests", ["slow request", "slow ops", "blocked for"]),
        ("libvirt_failure", ["libvirt", "connection refused", "hypervisor"]),
        ("volume_attach_failure", ["volume attach", "cephx auth", "rbd map"]),
        ("network_agent_down", ["agent down", "dhcp", "l3 agent", "neutron agent"]),
    ]

    for template_key, keywords in patterns:
        for keyword in keywords:
            if keyword in analysis_text:
                return template_key

    # Service-based fallback
    if affected_service:
        service_templates = {
            "nova": "generic",
            "neutron": "network_agent_down",
            "cinder": "volume_attach_failure",
            "ceph": "slow_requests",
            "rabbitmq": "rpc_timeout",
        }
        return service_templates.get(affected_service.lower(), "generic")

    return "generic"


def _get_preventive_measures(
    template_key: str,
    affected_service: str | None,
) -> list[PreventiveMeasure]:
    """Get preventive measures based on issue type."""
    measures: list[PreventiveMeasure] = []

    if template_key == "rpc_timeout":
        measures.append(
            PreventiveMeasure(
                title="Implement connection pooling monitoring",
                description="Set up alerts for RabbitMQ connection pool usage",
                implementation_effort="low",
                priority=IssuePriority.HIGH,
            )
        )
        measures.append(
            PreventiveMeasure(
                title="Configure connection limits",
                description="Set appropriate connection limits in RabbitMQ and OpenStack services",
                implementation_effort="medium",
                priority=IssuePriority.MEDIUM,
            )
        )

    elif template_key in ("osd_down", "slow_requests"):
        measures.append(
            PreventiveMeasure(
                title="Enable predictive disk failure monitoring",
                description="Use SMART data to predict disk failures before they occur",
                implementation_effort="medium",
                priority=IssuePriority.HIGH,
            )
        )
        measures.append(
            PreventiveMeasure(
                title="Implement capacity planning",
                description="Set up alerts for OSD utilization above 70%",
                implementation_effort="low",
                priority=IssuePriority.HIGH,
            )
        )

    elif template_key == "network_agent_down":
        measures.append(
            PreventiveMeasure(
                title="Implement agent health checks",
                description="Set up proactive monitoring for Neutron agent heartbeats",
                implementation_effort="low",
                priority=IssuePriority.HIGH,
            )
        )

    # Generic measures
    measures.append(
        PreventiveMeasure(
            title="Regular backup verification",
            description="Test backup restoration procedures monthly",
            implementation_effort="medium",
            priority=IssuePriority.MEDIUM,
        )
    )
    measures.append(
        PreventiveMeasure(
            title="Document runbooks",
            description="Maintain up-to-date runbooks for common issues",
            implementation_effort="medium",
            priority=IssuePriority.MEDIUM,
        )
    )

    return measures[:4]  # Return top 4


def _generate_analysis_summary(
    template_key: str,
    error_message: str | None,
    symptoms: list[str] | None,
    known_matches: list[tuple[IssuePattern, float]],
) -> str:
    """Generate analysis summary."""
    parts = ["Analysis of provided information:"]

    if error_message:
        parts.append(f"- Error indicates {template_key.replace('_', ' ')} issue")

    if symptoms:
        parts.append(f"- Symptoms analyzed: {len(symptoms)} items")

    if known_matches:
        best_score = known_matches[0][1]
        if best_score > 0.5:
            parts.append(f"- Strong match to known issue ({best_score:.0%} confidence)")
        elif best_score > 0.3:
            parts.append(f"- Moderate match to known issue ({best_score:.0%} confidence)")
        else:
            parts.append("- No strong match to known issues")
    else:
        parts.append("- No known issue matches found")

    return " ".join(parts)


def _get_confidence_explanation(
    confidence: ResolutionConfidence,
    template_key: str,
    known_matches: list[tuple[IssuePattern, float]],
) -> str:
    """Get explanation for confidence level."""
    if confidence == ResolutionConfidence.HIGH:
        return (
            "High confidence: This is a well-known issue pattern with "
            "proven resolution steps that have worked reliably."
        )
    elif confidence == ResolutionConfidence.MEDIUM:
        return (
            "Medium confidence: The symptoms match known patterns, but "
            "there may be multiple possible causes. Follow steps carefully."
        )
    elif confidence == ResolutionConfidence.LOW:
        return (
            "Low confidence: The issue doesn't strongly match known patterns. "
            "These are general troubleshooting steps. Consider escalation."
        )
    else:
        return (
            "Experimental: This resolution approach is based on general "
            "principles and should be applied with caution."
        )


# Tool metadata for registration
TOOL_NAME = "suggest_resolution"
TOOL_DESCRIPTION = """Provide AI-powered resolution suggestions.

Analyzes error messages and symptoms to suggest resolution steps.
Combines pattern matching against known issues with heuristic analysis.

This is a READ-ONLY tool that does not modify any resources."""

TOOL_TAGS = ["troubleshooting", "resolution", "guidance", "read-only"]
