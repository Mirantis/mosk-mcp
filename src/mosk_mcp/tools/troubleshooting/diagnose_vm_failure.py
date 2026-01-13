"""Diagnose VM failure tool for intelligent troubleshooting.

This tool diagnoses VM creation and operation failures by analyzing
logs, state, and known issue patterns.

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
from mosk_mcp.tools.troubleshooting.known_issues import get_known_issue_database
from mosk_mcp.tools.troubleshooting.models import (
    DiagnoseVMFailureOutput,
    DiagnosisCategory,
    DiagnosisFinding,
    IssuePriority,
    LogEntryInfo,
    LogSeverity,
    VMDiagnosisInfo,
)


logger = get_logger(__name__)


# Pre-compiled regex patterns for VM failure analysis
_PATTERN_NO_VALID_HOST = re.compile(r"no\s*valid\s*host", re.IGNORECASE)
_PATTERN_LIBVIRT = re.compile(r"libvirt.*connection.*refused", re.IGNORECASE)
_PATTERN_RPC_TIMEOUT = re.compile(r"rpc.*timeout|messagetimeout", re.IGNORECASE)
_PATTERN_VOLUME_ATTACH = re.compile(
    r"volume.*attach.*fail|unable.*to.*attach.*volume", re.IGNORECASE
)
_PATTERN_PORT_BINDING = re.compile(r"port.*binding.*fail|network.*error", re.IGNORECASE)
_PATTERN_QUOTA = re.compile(r"quota.*exceeded", re.IGNORECASE)
_PATTERN_IMAGE = re.compile(r"image.*not.*found|glance.*error", re.IGNORECASE)
_PATTERN_FLAVOR = re.compile(r"flavor.*not.*found", re.IGNORECASE)
_PATTERN_MIGRATION = re.compile(r"migration.*stuck|migration.*timeout", re.IGNORECASE)
_PATTERN_OOM = re.compile(r"out\s*of\s*memory|oom|memory.*exhausted", re.IGNORECASE)

# VM failure patterns for analysis
VM_FAILURE_PATTERNS: list[dict[str, Any]] = [
    {
        "pattern": _PATTERN_NO_VALID_HOST,
        "category": DiagnosisCategory.VM_FAILURE,
        "priority": IssuePriority.HIGH,
        "title": "No valid host found",
        "description": "The scheduler could not find a suitable host for the instance.",
        "component": "nova-scheduler",
        "known_issue": "MOSK-010",
    },
    {
        "pattern": _PATTERN_LIBVIRT,
        "category": DiagnosisCategory.SERVICE_ISSUE,
        "priority": IssuePriority.CRITICAL,
        "title": "Libvirt connection failure",
        "description": "Nova-compute cannot connect to libvirt daemon.",
        "component": "libvirt",
        "known_issue": "MOSK-004",
    },
    {
        "pattern": _PATTERN_RPC_TIMEOUT,
        "category": DiagnosisCategory.SERVICE_ISSUE,
        "priority": IssuePriority.HIGH,
        "title": "RPC timeout",
        "description": "Communication timeout between Nova services.",
        "component": "rabbitmq",
        "known_issue": "MOSK-001",
    },
    {
        "pattern": _PATTERN_VOLUME_ATTACH,
        "category": DiagnosisCategory.STORAGE_ISSUE,
        "priority": IssuePriority.HIGH,
        "title": "Volume attachment failure",
        "description": "Failed to attach storage volume to the instance.",
        "component": "cinder",
        "known_issue": "MOSK-005",
    },
    {
        "pattern": _PATTERN_PORT_BINDING,
        "category": DiagnosisCategory.NETWORK_ISSUE,
        "priority": IssuePriority.HIGH,
        "title": "Port binding failure",
        "description": "Failed to bind network port to the instance.",
        "component": "neutron",
        "known_issue": "MOSK-006",
    },
    {
        "pattern": _PATTERN_QUOTA,
        "category": DiagnosisCategory.CONFIGURATION_ISSUE,
        "priority": IssuePriority.MEDIUM,
        "title": "Quota exceeded",
        "description": "Project quota has been exceeded.",
        "component": "nova",
        "known_issue": None,
    },
    {
        "pattern": _PATTERN_IMAGE,
        "category": DiagnosisCategory.CONFIGURATION_ISSUE,
        "priority": IssuePriority.MEDIUM,
        "title": "Image error",
        "description": "The specified image cannot be found or accessed.",
        "component": "glance",
        "known_issue": None,
    },
    {
        "pattern": _PATTERN_FLAVOR,
        "category": DiagnosisCategory.CONFIGURATION_ISSUE,
        "priority": IssuePriority.MEDIUM,
        "title": "Flavor not found",
        "description": "The specified flavor does not exist.",
        "component": "nova",
        "known_issue": None,
    },
    {
        "pattern": _PATTERN_MIGRATION,
        "category": DiagnosisCategory.VM_FAILURE,
        "priority": IssuePriority.HIGH,
        "title": "Live migration stuck",
        "description": "Live migration is not progressing.",
        "component": "nova",
        "known_issue": "MOSK-003",
    },
    {
        "pattern": _PATTERN_OOM,
        "category": DiagnosisCategory.PERFORMANCE_ISSUE,
        "priority": IssuePriority.CRITICAL,
        "title": "Memory exhaustion",
        "description": "Compute host is out of memory.",
        "component": "compute-host",
        "known_issue": None,
    },
]


async def diagnose_vm_failure(
    direct_client: DirectStackLightClient,
    instance_id: str | None = None,
    instance_name: str | None = None,
    failure_type: str | None = None,
    time_range_minutes: int = 60,
) -> DiagnoseVMFailureOutput:
    """Diagnose VM creation or operation failures.

    This tool analyzes logs and state via OIDC/SSO authentication to
    diagnose why a VM operation failed. It checks for common failure
    patterns, correlates events, and matches against known issues.

    The direct_client must be authenticated with valid Keycloak tokens
    before calling this tool.

    Safety Level: Read-only

    Args:
        direct_client: Authenticated DirectStackLightClient for StackLight access.
        instance_id: VM instance UUID (optional if name provided).
        instance_name: VM instance name (optional).
        failure_type: Type of failure to diagnose (spawn, boot, migrate, etc.).
        time_range_minutes: Time range to search for errors (default: 60).

    Returns:
        DiagnoseVMFailureOutput with diagnosis findings and recommendations.

    Raises:
        ValidationError: If neither instance_id nor instance_name provided.
        ToolExecutionError: If diagnosis fails.

    Example:
        >>> result = await diagnose_vm_failure(
        ...     client,
        ...     instance_id="abc123-def456",
        ...     failure_type="spawn",
        ... )
    """
    logger.info(
        "diagnose_vm_failure_started",
        instance_id=instance_id,
        instance_name=instance_name,
        failure_type=failure_type,
    )

    # Validate inputs
    if not instance_id and not instance_name:
        raise ValidationError(
            "Either instance_id or instance_name must be provided",
            field="instance_id",
        )

    try:
        # Create StackLight adapter with direct client
        stacklight = StackLightAdapter(direct_client=direct_client)
        await stacklight.connect()

        # Build search keywords
        keywords = []
        if instance_id:
            keywords.append(instance_id)
        if instance_name:
            keywords.append(instance_name)
        if failure_type:
            keywords.append(failure_type)

        # Query logs for nova and related services - extract .logs from LogQueryResult
        log_result = await stacklight.query_logs(
            services=["nova", "neutron", "cinder", "libvirt"],
            severity="error",
            time_range_minutes=time_range_minutes,
            keywords=keywords if keywords else None,
            limit=200,
        )

        # Convert logs to model format
        related_logs: list[LogEntryInfo] = []
        log_messages: list[str] = []

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
            log_messages.append(log.message)

        # Analyze logs for failure patterns
        findings: list[DiagnosisFinding] = []

        for pattern_def in VM_FAILURE_PATTERNS:
            pattern = pattern_def["pattern"]  # Already pre-compiled
            matched_messages = [msg for msg in log_messages if pattern.search(msg)]

            if matched_messages:
                finding = DiagnosisFinding(
                    category=pattern_def["category"],
                    priority=pattern_def["priority"],
                    title=pattern_def["title"],
                    description=pattern_def["description"],
                    evidence=matched_messages[:3],  # First 3 matches
                    affected_component=pattern_def["component"],
                    known_issue_id=pattern_def.get("known_issue"),
                )
                findings.append(finding)

        # Sort findings by priority
        priority_order = {
            IssuePriority.CRITICAL: 0,
            IssuePriority.HIGH: 1,
            IssuePriority.MEDIUM: 2,
            IssuePriority.LOW: 3,
        }
        findings.sort(key=lambda f: priority_order.get(f.priority, 99))

        # Check known issues database
        db = get_known_issue_database()
        if log_messages:
            known_matches = db.find_matching_issues(
                error_message=log_messages[0] if log_messages else None,
                log_messages=log_messages[:10],
                service="nova",
            )

            for issue, score in known_matches[:3]:
                if score > 0.3:
                    # Check if we already have this as a finding
                    existing_ids = [f.known_issue_id for f in findings if f.known_issue_id]
                    if issue.issue_id not in existing_ids:
                        finding = DiagnosisFinding(
                            category=issue.category,
                            priority=issue.priority,
                            title=f"Known Issue: {issue.title}",
                            description=issue.root_cause,
                            evidence=[f"Match score: {score:.0%}"],
                            affected_component=issue.affected_services[0]
                            if issue.affected_services
                            else "unknown",
                            known_issue_id=issue.issue_id,
                        )
                        findings.append(finding)

        # Build VM info from available data (full details require OpenStack API)
        vm_info = VMDiagnosisInfo(
            instance_id=instance_id or f"unknown-{instance_name}",
            instance_name=instance_name or "unknown",
            project_id=None,
            host=None,
            vm_state="error" if findings else "unknown",
            task_state=failure_type,
            power_state=None,
        )

        # Determine primary diagnosis
        primary_diagnosis = findings[0] if findings else None
        additional_findings = findings[1:] if len(findings) > 1 else []

        # Build timeline from logs
        timeline: list[str] = []
        for log_entry in related_logs[:10]:
            timeline.append(
                f"[{log_entry.timestamp}] {log_entry.service}: {log_entry.message[:80]}"
            )

        # Generate root cause analysis
        root_cause_analysis = _generate_root_cause_analysis(
            findings=findings,
            failure_type=failure_type,
            log_count=len(related_logs),
        )

        # Generate recommendations
        recommended_actions = _generate_vm_recommendations(
            findings=findings,
            failure_type=failure_type,
        )

        result = DiagnoseVMFailureOutput(
            vm_info=vm_info,
            failure_detected=len(findings) > 0,
            primary_diagnosis=primary_diagnosis,
            additional_findings=additional_findings,
            related_logs=related_logs[:20],
            timeline=timeline,
            root_cause_analysis=root_cause_analysis,
            recommended_actions=recommended_actions,
            timestamp=datetime.now(UTC).isoformat(),
        )

        logger.info(
            "diagnose_vm_failure_completed",
            findings_count=len(findings),
            failure_detected=result.failure_detected,
        )

        return result

    except ValidationError:
        raise
    except Exception as e:
        logger.error(
            "diagnose_vm_failure_failed",
            error=str(e),
            instance_id=instance_id,
        )
        raise ToolExecutionError(
            f"Failed to diagnose VM failure: {e}",
            tool_name="diagnose_vm_failure",
            phase="execution",
        ) from e


def _generate_root_cause_analysis(
    findings: list[DiagnosisFinding],
    failure_type: str | None,
    log_count: int,
) -> str:
    """Generate root cause analysis summary."""
    if not findings:
        return (
            f"No specific failure patterns detected in {log_count} analyzed logs. "
            "The issue may be intermittent or require additional log sources."
        )

    primary = findings[0]

    analysis = f"Primary issue identified: {primary.title}. "
    analysis += f"{primary.description} "

    if primary.known_issue_id:
        analysis += f"This matches known issue {primary.known_issue_id}. "

    if len(findings) > 1:
        analysis += f"Additionally, {len(findings) - 1} other related issues were detected."

    if failure_type:
        analysis += f" The failure occurred during a {failure_type} operation."

    return analysis


def _generate_vm_recommendations(
    findings: list[DiagnosisFinding],
    failure_type: str | None,
) -> list[str]:
    """Generate recommended actions based on findings."""
    recommendations: list[str] = []

    if not findings:
        recommendations.append("Review nova-compute logs for additional error details")
        recommendations.append("Check instance state: openstack server show <instance>")
        recommendations.append("Verify compute host resources: openstack hypervisor stats show")
        return recommendations

    for finding in findings[:3]:
        if finding.known_issue_id:
            recommendations.append(f"Follow resolution for {finding.known_issue_id}")

        if finding.category == DiagnosisCategory.VM_FAILURE:
            if "no valid host" in finding.title.lower():
                recommendations.append("Check scheduler filters: nova-manage host list")
                recommendations.append("Review compute capacity: openstack hypervisor list")
            elif "migration" in finding.title.lower():
                recommendations.append("Check migration status: nova live-migration-force-complete")
                recommendations.append("Consider aborting stuck migration")

        elif finding.category == DiagnosisCategory.SERVICE_ISSUE:
            recommendations.append(f"Restart {finding.affected_component} service")
            recommendations.append(f"Check {finding.affected_component} logs")

        elif finding.category == DiagnosisCategory.STORAGE_ISSUE:
            recommendations.append("Check Ceph cluster status: ceph -s")
            recommendations.append("Verify volume backend: cinder service-list")

        elif finding.category == DiagnosisCategory.NETWORK_ISSUE:
            recommendations.append("Check Neutron agents: openstack network agent list")
            recommendations.append("Verify OVS bridges: ovs-vsctl show")

    return recommendations[:5]  # Top 5 recommendations


# Tool metadata for registration
TOOL_NAME = "diagnose_vm_failure"
TOOL_DESCRIPTION = """Diagnose VM creation or operation failures.

Analyzes logs and state to determine why a VM operation failed. Checks for
common failure patterns, correlates events, and matches against known issues.

This is a READ-ONLY tool that does not modify any resources."""

TOOL_TAGS = ["troubleshooting", "vm", "nova", "diagnosis", "read-only"]
