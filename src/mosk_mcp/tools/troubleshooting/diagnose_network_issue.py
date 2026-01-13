"""Diagnose network issue tool for intelligent troubleshooting.

This tool diagnoses network connectivity issues by analyzing
logs, agent status, and network path components.

Safety Level: Read-only

This tool queries StackLight via OIDC/SSO authentication using
DirectStackLightClient. Authentication must be established before
calling this tool.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from mosk_mcp.adapters.stacklight import DirectStackLightClient, StackLightAdapter
from mosk_mcp.core.exceptions import ToolExecutionError, ValidationError
from mosk_mcp.observability.logging import get_logger
from mosk_mcp.tools.troubleshooting.models import (
    DiagnoseNetworkIssueOutput,
    DiagnosisCategory,
    DiagnosisFinding,
    IssuePriority,
    LogEntryInfo,
    LogSeverity,
    NetworkPathComponent,
)


logger = get_logger(__name__)


# Pre-compiled regex patterns for network failure analysis
_PATTERN_PORT_BINDING = re.compile(r"port.*binding.*fail", re.IGNORECASE)
_PATTERN_DHCP = re.compile(r"dhcp.*fail|dhcp.*error|no.*dhcp", re.IGNORECASE)
_PATTERN_AGENT_DOWN = re.compile(r"agent.*down|agent.*not.*responding", re.IGNORECASE)
_PATTERN_OVS = re.compile(r"ovs.*error|openvswitch.*fail", re.IGNORECASE)
_PATTERN_SECURITY_GROUP = re.compile(r"security.*group.*error|sg.*rule.*fail", re.IGNORECASE)
_PATTERN_L3_ROUTING = re.compile(r"router.*error|l3.*agent.*fail", re.IGNORECASE)
_PATTERN_FLOATING_IP = re.compile(r"floating.*ip.*fail|floatingip.*error", re.IGNORECASE)
_PATTERN_TUNNEL = re.compile(r"tunnel.*fail|vxlan.*error|gre.*error", re.IGNORECASE)
_PATTERN_MTU = re.compile(r"mtu.*error|mtu.*mismatch", re.IGNORECASE)
_PATTERN_ARP = re.compile(r"arp.*fail|arp.*timeout", re.IGNORECASE)

# Network failure patterns for analysis
NETWORK_FAILURE_PATTERNS: list[dict[str, Any]] = [
    {
        "pattern": _PATTERN_PORT_BINDING,
        "category": DiagnosisCategory.NETWORK_ISSUE,
        "priority": IssuePriority.HIGH,
        "title": "Port binding failure",
        "description": "Failed to bind a network port. This prevents VMs from getting network access.",
        "component": "neutron-server",
        "known_issue": "MOSK-006",
    },
    {
        "pattern": _PATTERN_DHCP,
        "category": DiagnosisCategory.NETWORK_ISSUE,
        "priority": IssuePriority.HIGH,
        "title": "DHCP failure",
        "description": "DHCP service is not working properly. VMs may not receive IP addresses.",
        "component": "neutron-dhcp-agent",
        "known_issue": None,
    },
    {
        "pattern": _PATTERN_AGENT_DOWN,
        "category": DiagnosisCategory.SERVICE_ISSUE,
        "priority": IssuePriority.HIGH,
        "title": "Network agent down",
        "description": "A Neutron network agent is not responding.",
        "component": "neutron-agent",
        "known_issue": "MOSK-006",
    },
    {
        "pattern": _PATTERN_OVS,
        "category": DiagnosisCategory.NETWORK_ISSUE,
        "priority": IssuePriority.CRITICAL,
        "title": "OVS failure",
        "description": "Open vSwitch is experiencing issues. Network traffic may be disrupted.",
        "component": "ovs",
        "known_issue": None,
    },
    {
        "pattern": _PATTERN_SECURITY_GROUP,
        "category": DiagnosisCategory.NETWORK_ISSUE,
        "priority": IssuePriority.MEDIUM,
        "title": "Security group error",
        "description": "Security group rules could not be applied.",
        "component": "neutron-ovs-agent",
        "known_issue": None,
    },
    {
        "pattern": _PATTERN_L3_ROUTING,
        "category": DiagnosisCategory.NETWORK_ISSUE,
        "priority": IssuePriority.HIGH,
        "title": "L3 routing failure",
        "description": "L3 agent is failing. External connectivity may be affected.",
        "component": "neutron-l3-agent",
        "known_issue": None,
    },
    {
        "pattern": _PATTERN_FLOATING_IP,
        "category": DiagnosisCategory.NETWORK_ISSUE,
        "priority": IssuePriority.HIGH,
        "title": "Floating IP failure",
        "description": "Failed to assign or configure floating IP.",
        "component": "neutron-l3-agent",
        "known_issue": None,
    },
    {
        "pattern": _PATTERN_TUNNEL,
        "category": DiagnosisCategory.NETWORK_ISSUE,
        "priority": IssuePriority.CRITICAL,
        "title": "Tunnel connectivity failure",
        "description": "Network overlay tunnel is not working properly.",
        "component": "ovs-tunnel",
        "known_issue": None,
    },
    {
        "pattern": _PATTERN_MTU,
        "category": DiagnosisCategory.CONFIGURATION_ISSUE,
        "priority": IssuePriority.MEDIUM,
        "title": "MTU mismatch",
        "description": "MTU configuration mismatch causing packet fragmentation or drops.",
        "component": "network-config",
        "known_issue": None,
    },
    {
        "pattern": _PATTERN_ARP,
        "category": DiagnosisCategory.NETWORK_ISSUE,
        "priority": IssuePriority.MEDIUM,
        "title": "ARP resolution failure",
        "description": "ARP resolution is failing. Hosts cannot discover MAC addresses.",
        "component": "network",
        "known_issue": None,
    },
]


async def diagnose_network_issue(
    direct_client: DirectStackLightClient,
    source_ip: str | None = None,
    destination_ip: str | None = None,
    port_id: str | None = None,
    network_id: str | None = None,
    instance_id: str | None = None,
    symptom: str | None = None,
    time_range_minutes: int = 60,
) -> DiagnoseNetworkIssueOutput:
    """Diagnose network connectivity issues.

    This tool analyzes network-related logs and component status via
    OIDC/SSO authentication to diagnose connectivity problems between
    VMs, networks, or external endpoints.

    The direct_client must be authenticated with valid Keycloak tokens
    before calling this tool.

    Safety Level: Read-only

    Args:
        direct_client: Authenticated DirectStackLightClient for StackLight access.
        source_ip: Source IP address involved in the issue.
        destination_ip: Destination IP address involved in the issue.
        port_id: Neutron port ID to investigate.
        network_id: Network ID to investigate.
        instance_id: Instance UUID experiencing network issues.
        symptom: Description of the network issue.
        time_range_minutes: Time range to search for errors (default: 60).

    Returns:
        DiagnoseNetworkIssueOutput with diagnosis findings and recommendations.

    Raises:
        ValidationError: If no identifiers are provided.
        ToolExecutionError: If diagnosis fails.

    Example:
        >>> result = await diagnose_network_issue(
        ...     client,
        ...     instance_id="abc123",
        ...     symptom="VM cannot reach external network",
        ... )
    """
    logger.info(
        "diagnose_network_issue_started",
        source_ip=source_ip,
        destination_ip=destination_ip,
        port_id=port_id,
        instance_id=instance_id,
    )

    # Validate at least one identifier is provided
    if not any([source_ip, destination_ip, port_id, network_id, instance_id, symptom]):
        raise ValidationError(
            "At least one identifier (source_ip, destination_ip, port_id, "
            "network_id, instance_id, or symptom) must be provided",
            field="identifiers",
        )

    try:
        # Create StackLight adapter with direct client
        stacklight = StackLightAdapter(direct_client=direct_client)
        await stacklight.connect()

        # Build search keywords
        keywords = []
        if source_ip:
            keywords.append(source_ip)
        if destination_ip:
            keywords.append(destination_ip)
        if port_id:
            keywords.append(port_id)
        if network_id:
            keywords.append(network_id)
        if instance_id:
            keywords.append(instance_id)
        if symptom:
            keywords.extend(symptom.split()[:5])  # First 5 words

        # Query logs for neutron and related services - extract .logs from LogQueryResult
        error_result = await stacklight.query_logs(
            services=["neutron", "nova", "ovs"],
            severity="error",
            time_range_minutes=time_range_minutes,
            keywords=keywords if keywords else None,
            limit=200,
        )

        # Also get warnings for network issues
        warning_result = await stacklight.query_logs(
            services=["neutron"],
            severity="warning",
            time_range_minutes=time_range_minutes,
            keywords=keywords if keywords else None,
            limit=50,
        )
        logs = list(error_result.logs) + list(warning_result.logs)

        # Convert logs to model format
        related_logs: list[LogEntryInfo] = []
        log_messages: list[str] = []

        for log in logs:
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
            log_messages.append(log.message)

        # Analyze logs for failure patterns
        findings: list[DiagnosisFinding] = []

        for pattern_def in NETWORK_FAILURE_PATTERNS:
            pattern = pattern_def["pattern"]  # Already pre-compiled
            matched_messages = [msg for msg in log_messages if pattern.search(msg)]

            if matched_messages:
                finding = DiagnosisFinding(
                    category=pattern_def["category"],
                    priority=pattern_def["priority"],
                    title=pattern_def["title"],
                    description=pattern_def["description"],
                    evidence=matched_messages[:3],
                    affected_component=pattern_def["component"],
                    known_issue_id=pattern_def.get("known_issue"),
                )
                findings.append(finding)

        # Also check if symptom matches known patterns
        if symptom:
            symptom_lower = symptom.lower()
            if "no ip" in symptom_lower or "dhcp" in symptom_lower:
                findings.append(
                    DiagnosisFinding(
                        category=DiagnosisCategory.NETWORK_ISSUE,
                        priority=IssuePriority.HIGH,
                        title="DHCP-related issue suspected",
                        description="Symptom suggests DHCP is not working.",
                        evidence=[f"User symptom: {symptom}"],
                        affected_component="neutron-dhcp-agent",
                    )
                )
            elif "external" in symptom_lower or "internet" in symptom_lower:
                findings.append(
                    DiagnosisFinding(
                        category=DiagnosisCategory.NETWORK_ISSUE,
                        priority=IssuePriority.HIGH,
                        title="External connectivity issue",
                        description="External network access is not working.",
                        evidence=[f"User symptom: {symptom}"],
                        affected_component="neutron-l3-agent",
                    )
                )

        # Sort findings by priority
        priority_order = {
            IssuePriority.CRITICAL: 0,
            IssuePriority.HIGH: 1,
            IssuePriority.MEDIUM: 2,
            IssuePriority.LOW: 3,
        }
        findings.sort(key=lambda f: priority_order.get(f.priority, 99))

        # Build network path components based on analysis findings
        path_components = _build_network_path(
            source_ip=source_ip,
            destination_ip=destination_ip,
            port_id=port_id,
            findings=findings,
        )

        # Build agent status based on analysis findings
        agent_status = {
            "dhcp-agent": "up",
            "l3-agent": "up",
            "ovs-agent": "up",
            "metadata-agent": "up",
        }
        # Mark agents as potentially down if we found related issues
        for finding in findings:
            if "dhcp" in finding.title.lower():
                agent_status["dhcp-agent"] = "degraded"
            elif "l3" in finding.title.lower() or "router" in finding.title.lower():
                agent_status["l3-agent"] = "degraded"
            elif "ovs" in finding.title.lower():
                agent_status["ovs-agent"] = "degraded"

        # Determine primary diagnosis
        primary_diagnosis = findings[0] if findings else None
        additional_findings = findings[1:] if len(findings) > 1 else []

        # Generate root cause analysis
        root_cause_analysis = _generate_network_root_cause(
            findings=findings,
            symptom=symptom,
            log_count=len(related_logs),
        )

        # Generate recommendations
        recommended_actions = _generate_network_recommendations(findings)

        result = DiagnoseNetworkIssueOutput(
            issue_detected=len(findings) > 0,
            path_components=path_components,
            primary_diagnosis=primary_diagnosis,
            additional_findings=additional_findings,
            agent_status=agent_status,
            related_logs=related_logs[:20],
            connectivity_test_results={},
            root_cause_analysis=root_cause_analysis,
            recommended_actions=recommended_actions,
            timestamp=datetime.now(UTC).isoformat(),
        )

        logger.info(
            "diagnose_network_issue_completed",
            findings_count=len(findings),
            issue_detected=result.issue_detected,
        )

        return result

    except ValidationError:
        raise
    except Exception as e:
        logger.error(
            "diagnose_network_issue_failed",
            error=str(e),
        )
        raise ToolExecutionError(
            f"Failed to diagnose network issue: {e}",
            tool_name="diagnose_network_issue",
            phase="execution",
        ) from e


def _build_network_path(
    source_ip: str | None,
    destination_ip: str | None,
    port_id: str | None,
    findings: list[DiagnosisFinding],
) -> list[NetworkPathComponent]:
    """Build a representation of the network path."""
    components: list[NetworkPathComponent] = []

    # Source VM/port
    if source_ip or port_id:
        status = "healthy"
        issues = []
        for f in findings:
            if "port" in f.title.lower():
                status = "degraded"
                issues.append(f.title)
        components.append(
            NetworkPathComponent(
                component_type="instance_port",
                name=port_id or f"IP: {source_ip}",
                status=status,
                issues=issues,
            )
        )

    # Security group
    sg_status = "healthy"
    sg_issues = []
    for f in findings:
        if "security" in f.title.lower():
            sg_status = "degraded"
            sg_issues.append(f.title)
    components.append(
        NetworkPathComponent(
            component_type="security_group",
            name="default-sg",
            status=sg_status,
            issues=sg_issues,
        )
    )

    # OVS bridge
    ovs_status = "healthy"
    ovs_issues = []
    for f in findings:
        if "ovs" in f.title.lower() or "openvswitch" in f.title.lower():
            ovs_status = "error"
            ovs_issues.append(f.title)
    components.append(
        NetworkPathComponent(
            component_type="ovs_bridge",
            name="br-int",
            status=ovs_status,
            issues=ovs_issues,
        )
    )

    # Router/L3 if external connectivity
    if destination_ip:
        router_status = "healthy"
        router_issues = []
        for f in findings:
            if "l3" in f.title.lower() or "router" in f.title.lower():
                router_status = "degraded"
                router_issues.append(f.title)
        components.append(
            NetworkPathComponent(
                component_type="router",
                name="router-external",
                status=router_status,
                issues=router_issues,
            )
        )

    return components


def _generate_network_root_cause(
    findings: list[DiagnosisFinding],
    symptom: str | None,
    log_count: int,
) -> str:
    """Generate network root cause analysis."""
    if not findings:
        return (
            f"No specific network failure patterns detected in {log_count} analyzed logs. "
            "Consider checking physical network connectivity and switch configurations."
        )

    primary = findings[0]
    analysis = f"Primary network issue: {primary.title}. {primary.description}"

    if symptom:
        analysis += f" User-reported symptom: {symptom}."

    if primary.known_issue_id:
        analysis += f" This matches known issue {primary.known_issue_id}."

    return analysis


def _generate_network_recommendations(
    findings: list[DiagnosisFinding],
) -> list[str]:
    """Generate network troubleshooting recommendations."""
    recommendations: list[str] = []

    if not findings:
        recommendations.append("Check network agent status: openstack network agent list")
        recommendations.append("Verify OVS configuration: ovs-vsctl show")
        recommendations.append("Test connectivity: ping from within network namespace")
        return recommendations

    for finding in findings[:3]:
        if "dhcp" in finding.title.lower():
            recommendations.append("Check DHCP agent: neutron agent-list --agent-type dhcp")
            recommendations.append(
                "List DHCP namespaces: ip netns list | grep qdhcp, then: "
                "ip netns exec qdhcp-<NETWORK_ID> ip a"
            )

        elif "l3" in finding.title.lower() or "router" in finding.title.lower():
            recommendations.append("Check L3 agent: neutron agent-list --agent-type l3")
            recommendations.append(
                "List router namespaces: ip netns list | grep qrouter, then: "
                "ip netns exec qrouter-<ROUTER_ID> ip route"
            )

        elif "ovs" in finding.title.lower():
            recommendations.append("Check OVS daemon: systemctl status openvswitch-switch")
            recommendations.append("Review OVS flows: ovs-ofctl dump-flows br-int")

        elif "port" in finding.title.lower():
            recommendations.append("Check port status: openstack port show <port-id>")
            recommendations.append(
                "Verify binding: openstack port show <port-id> -f value -c binding:host_id"
            )

        elif "security" in finding.title.lower():
            recommendations.append(
                "Review security group rules: openstack security group rule list"
            )
            recommendations.append("Check OVS flow rules for security")

    return recommendations[:5]


# Tool metadata for registration
TOOL_NAME = "diagnose_network_issue"
TOOL_DESCRIPTION = """Diagnose network connectivity issues.

Analyzes network-related logs and component status to diagnose connectivity
problems between VMs, networks, or external endpoints.

This is a READ-ONLY tool that does not modify any resources."""

TOOL_TAGS = ["troubleshooting", "network", "neutron", "diagnosis", "read-only"]
