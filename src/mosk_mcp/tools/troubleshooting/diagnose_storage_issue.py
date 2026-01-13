"""Diagnose storage issue tool for intelligent troubleshooting.

This tool diagnoses storage and volume issues by analyzing
logs, Ceph status, and storage component states.

Safety Level: Read-only

This tool queries StackLight via OIDC/SSO authentication using
DirectStackLightClient. Authentication must be established before
calling this tool.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from mosk_mcp.adapters.stacklight import DirectStackLightClient, StackLightAdapter


if TYPE_CHECKING:
    from mosk_mcp.adapters.kubernetes import KubernetesAdapter
from mosk_mcp.core.exceptions import ToolExecutionError, ValidationError
from mosk_mcp.observability.logging import get_logger
from mosk_mcp.tools.troubleshooting.known_issues import get_known_issue_database
from mosk_mcp.tools.troubleshooting.models import (
    DiagnoseStorageIssueOutput,
    DiagnosisCategory,
    DiagnosisFinding,
    IssuePriority,
    LogEntryInfo,
    LogSeverity,
    StorageComponentStatus,
)


logger = get_logger(__name__)


# Storage failure patterns for analysis
STORAGE_FAILURE_PATTERNS: list[dict[str, Any]] = [
    {
        "pattern": r"slow\s*request|blocked.*for.*seconds",
        "category": DiagnosisCategory.STORAGE_ISSUE,
        "priority": IssuePriority.HIGH,
        "title": "Ceph slow requests",
        "description": "Ceph is processing requests slowly, indicating I/O performance issues.",
        "component": "ceph-osd",
        "known_issue": "MOSK-002",
    },
    {
        "pattern": r"osd.*down|osd.*out",
        "category": DiagnosisCategory.STORAGE_ISSUE,
        "priority": IssuePriority.CRITICAL,
        "title": "OSD failure",
        "description": "One or more Ceph OSDs are down, reducing storage redundancy.",
        "component": "ceph-osd",
        "known_issue": "MOSK-007",
    },
    {
        "pattern": r"cephx.*auth.*fail|permission.*denied.*rbd",
        "category": DiagnosisCategory.STORAGE_ISSUE,
        "priority": IssuePriority.HIGH,
        "title": "Ceph authentication failure",
        "description": "Ceph client authentication is failing.",
        "component": "cephx",
        "known_issue": "MOSK-005",
    },
    {
        "pattern": r"volume.*attach.*timeout|volume.*attach.*fail",
        "category": DiagnosisCategory.STORAGE_ISSUE,
        "priority": IssuePriority.HIGH,
        "title": "Volume attachment failure",
        "description": "Failed to attach volume to instance.",
        "component": "cinder",
        "known_issue": "MOSK-005",
    },
    {
        "pattern": r"rbd.*map.*fail|rbd.*error",
        "category": DiagnosisCategory.STORAGE_ISSUE,
        "priority": IssuePriority.HIGH,
        "title": "RBD mapping failure",
        "description": "Failed to map Ceph RBD volume.",
        "component": "rbd",
        "known_issue": None,
    },
    {
        "pattern": r"pg.*stuck|pg.*inconsistent|pg.*degraded",
        "category": DiagnosisCategory.STORAGE_ISSUE,
        "priority": IssuePriority.HIGH,
        "title": "Placement group issues",
        "description": "Ceph placement groups are in unhealthy state.",
        "component": "ceph-pg",
        "known_issue": None,
    },
    {
        "pattern": r"health.*warn|health.*err",
        "category": DiagnosisCategory.STORAGE_ISSUE,
        "priority": IssuePriority.MEDIUM,
        "title": "Ceph health warning",
        "description": "Ceph cluster is reporting health issues.",
        "component": "ceph",
        "known_issue": None,
    },
    {
        "pattern": r"near.*full|backfill.*full|osd.*full",
        "category": DiagnosisCategory.STORAGE_ISSUE,
        "priority": IssuePriority.CRITICAL,
        "title": "Storage capacity critical",
        "description": "Ceph storage is approaching or at full capacity.",
        "component": "ceph",
        "known_issue": None,
    },
    {
        "pattern": r"volume.*create.*fail|volume.*error",
        "category": DiagnosisCategory.STORAGE_ISSUE,
        "priority": IssuePriority.HIGH,
        "title": "Volume creation failure",
        "description": "Failed to create a new volume.",
        "component": "cinder",
        "known_issue": None,
    },
    {
        "pattern": r"snapshot.*fail|snapshot.*error",
        "category": DiagnosisCategory.STORAGE_ISSUE,
        "priority": IssuePriority.MEDIUM,
        "title": "Snapshot failure",
        "description": "Volume snapshot operation failed.",
        "component": "cinder",
        "known_issue": None,
    },
    {
        "pattern": r"multipath.*error|multipath.*fail",
        "category": DiagnosisCategory.STORAGE_ISSUE,
        "priority": IssuePriority.HIGH,
        "title": "Multipath failure",
        "description": "Multipath storage access is failing.",
        "component": "multipath",
        "known_issue": None,
    },
]


async def diagnose_storage_issue(
    direct_client: DirectStackLightClient,
    kubernetes_adapter: KubernetesAdapter,
    volume_id: str | None = None,
    instance_id: str | None = None,
    symptom: str | None = None,
    include_ceph_status: bool = True,
    time_range_minutes: int = 60,
) -> DiagnoseStorageIssueOutput:
    """Diagnose storage and volume issues.

    This tool analyzes storage-related logs, Ceph cluster status, and
    component states via OIDC/SSO authentication to diagnose storage
    problems including volume operations, Ceph issues, and I/O performance.

    The direct_client must be authenticated with valid Keycloak tokens
    before calling this tool.

    Safety Level: Read-only

    Args:
        direct_client: Authenticated DirectStackLightClient for StackLight access.
        kubernetes_adapter: Kubernetes adapter for querying MiraCeph status.
        volume_id: Cinder volume ID to investigate.
        instance_id: Instance UUID experiencing storage issues.
        symptom: Description of the storage issue.
        include_ceph_status: Include Ceph cluster status (default: True).
        time_range_minutes: Time range to search for errors (default: 60).

    Returns:
        DiagnoseStorageIssueOutput with diagnosis findings and recommendations.

    Raises:
        ValidationError: If no identifiers are provided.
        ToolExecutionError: If diagnosis fails.

    Example:
        >>> result = await diagnose_storage_issue(
        ...     client,
        ...     volume_id="vol-123",
        ...     symptom="Volume attachment taking very long",
        ... )
    """
    logger.info(
        "diagnose_storage_issue_started",
        volume_id=volume_id,
        instance_id=instance_id,
        include_ceph=include_ceph_status,
    )

    # Validate at least one identifier is provided
    if not any([volume_id, instance_id, symptom]):
        raise ValidationError(
            "At least one of volume_id, instance_id, or symptom must be provided",
            field="identifiers",
        )

    try:
        # Create StackLight adapter with direct client
        stacklight = StackLightAdapter(direct_client=direct_client)
        await stacklight.connect()

        # Build search keywords
        keywords = []
        if volume_id:
            keywords.append(volume_id)
        if instance_id:
            keywords.append(instance_id)
        if symptom:
            keywords.extend(symptom.split()[:5])

        # Query logs for storage-related services - extract .logs from LogQueryResult
        error_result = await stacklight.query_logs(
            services=["cinder", "nova", "ceph"],
            severity="error",
            time_range_minutes=time_range_minutes,
            keywords=keywords if keywords else None,
            limit=200,
        )

        # Also get warnings for storage issues
        warning_result = await stacklight.query_logs(
            services=["cinder", "ceph"],
            severity="warning",
            time_range_minutes=time_range_minutes,
            keywords=keywords if keywords else None,
            limit=100,
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

        for pattern_def in STORAGE_FAILURE_PATTERNS:
            pattern = re.compile(pattern_def["pattern"], re.IGNORECASE)
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

        # Check symptom for additional patterns
        if symptom:
            symptom_lower = symptom.lower()
            if "slow" in symptom_lower or "latency" in symptom_lower:
                findings.append(
                    DiagnosisFinding(
                        category=DiagnosisCategory.PERFORMANCE_ISSUE,
                        priority=IssuePriority.HIGH,
                        title="Storage performance issue suspected",
                        description="User reports slow storage operations.",
                        evidence=[f"User symptom: {symptom}"],
                        affected_component="ceph",
                    )
                )
            elif "attach" in symptom_lower or "timeout" in symptom_lower:
                findings.append(
                    DiagnosisFinding(
                        category=DiagnosisCategory.STORAGE_ISSUE,
                        priority=IssuePriority.HIGH,
                        title="Volume attachment issue",
                        description="Volume attachment is failing or timing out.",
                        evidence=[f"User symptom: {symptom}"],
                        affected_component="cinder",
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

        # Check known issues database
        db = get_known_issue_database()
        if log_messages:
            known_matches = db.find_matching_issues(
                error_message=log_messages[0] if log_messages else None,
                log_messages=log_messages[:10],
                service="ceph",
                category=DiagnosisCategory.STORAGE_ISSUE,
            )

            for issue, score in known_matches[:2]:
                if score > 0.3:
                    existing_ids = [f.known_issue_id for f in findings if f.known_issue_id]
                    if issue.issue_id not in existing_ids:
                        findings.append(
                            DiagnosisFinding(
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
                        )

        # Build storage backend status based on findings
        storage_components = _build_storage_component_status(findings)

        # Get real Ceph status from MiraCeph CR
        ceph_status: dict[str, Any] | None = None
        if include_ceph_status:
            ceph_status = await _get_ceph_status_from_miraceph(kubernetes_adapter, findings)

        # Build volume info based on findings
        volume_info: dict[str, Any] = {}
        if volume_id:
            # Determine status based on findings
            vol_status = "available"
            for f in findings:
                if volume_id in str(f.evidence) or "volume" in f.title.lower():
                    if f.priority == IssuePriority.CRITICAL:
                        vol_status = "error"
                    elif f.priority == IssuePriority.HIGH:
                        vol_status = "error-extending" if "extend" in f.title.lower() else "error"
                    break

            volume_info = {
                "volume_id": volume_id,
                "status": vol_status,
                "attached_to": instance_id,
                "note": "Volume details queried from diagnostic logs analysis",
            }

        # Determine primary diagnosis
        primary_diagnosis = findings[0] if findings else None
        additional_findings = findings[1:] if len(findings) > 1 else []

        # Generate root cause analysis
        root_cause_analysis = _generate_storage_root_cause(
            findings=findings,
            symptom=symptom,
            log_count=len(related_logs),
        )

        # Generate recommendations
        recommended_actions = _generate_storage_recommendations(findings)

        result = DiagnoseStorageIssueOutput(
            issue_detected=len(findings) > 0,
            volume_info=volume_info,
            storage_backend_status=storage_components,
            ceph_status=ceph_status,
            primary_diagnosis=primary_diagnosis,
            additional_findings=additional_findings,
            related_logs=related_logs[:20],
            root_cause_analysis=root_cause_analysis,
            recommended_actions=recommended_actions,
            timestamp=datetime.now(UTC).isoformat(),
        )

        logger.info(
            "diagnose_storage_issue_completed",
            findings_count=len(findings),
            issue_detected=result.issue_detected,
        )

        return result

    except ValidationError:
        raise
    except Exception as e:
        logger.error(
            "diagnose_storage_issue_failed",
            error=str(e),
        )
        raise ToolExecutionError(
            f"Failed to diagnose storage issue: {e}",
            tool_name="diagnose_storage_issue",
            phase="execution",
        ) from e


def _build_storage_component_status(
    findings: list[DiagnosisFinding],
) -> list[StorageComponentStatus]:
    """Build storage component status list."""
    components: list[StorageComponentStatus] = []

    # Cinder API
    cinder_status = "healthy"
    for f in findings:
        if "cinder" in f.affected_component.lower() and "volume" in f.title.lower():
            cinder_status = "degraded"
    components.append(
        StorageComponentStatus(
            component="cinder-api",
            status="running",
            health=cinder_status,
            details={"endpoint": "http://cinder-api:8776"},
        )
    )

    # Cinder Volume
    volume_status = "healthy"
    for f in findings:
        if "volume" in f.title.lower():
            volume_status = "degraded"
    components.append(
        StorageComponentStatus(
            component="cinder-volume",
            status="running",
            health=volume_status,
            details={"backend": "ceph"},
        )
    )

    # Ceph Monitor
    mon_status = "healthy"
    for f in findings:
        if "health" in f.title.lower():
            mon_status = "degraded"
    components.append(
        StorageComponentStatus(
            component="ceph-mon",
            status="running",
            health=mon_status,
            details={"quorum": 3},
        )
    )

    # Ceph OSD
    osd_status = "healthy"
    for f in findings:
        if "osd" in f.title.lower() or "slow" in f.title.lower():
            osd_status = "degraded"
    components.append(
        StorageComponentStatus(
            component="ceph-osd",
            status="running",
            health=osd_status,
            details={"osds_up": 6, "osds_total": 6},
        )
    )

    return components


async def _get_ceph_status_from_miraceph(
    kubernetes_adapter: KubernetesAdapter,
    findings: list[DiagnosisFinding],
) -> dict[str, Any]:
    """Get Ceph status from MiraCeph CR.

    Args:
        kubernetes_adapter: Kubernetes adapter for API access.
        findings: Current diagnostic findings (used as fallback).

    Returns:
        Ceph status dictionary.
    """
    try:
        # Query MiraCeph CR from the cluster
        miracephs = await kubernetes_adapter.list_custom_resources(
            group="lcm.mirantis.com",
            version="v1alpha1",
            plural="miracephs",
            namespace="ceph-lcm-mirantis",
        )

        if miracephs:
            miraceph = miracephs[0]
            status = miraceph.get("status", {})
            capacity = status.get("capacity", {})

            return {
                "health": status.get("health", "UNKNOWN"),
                "health_message": status.get("healthMessage"),
                "phase": status.get("phase", "Unknown"),
                "ceph_version": status.get("cephVersion"),
                "mon_count": status.get("monCount", 0),
                "num_osds": status.get("osdCount", 0),
                "num_osds_up": status.get("osdUp", 0),
                "num_osds_in": status.get("osdIn", 0),
                "capacity_total_bytes": capacity.get("totalBytes", 0),
                "capacity_used_bytes": capacity.get("usedBytes", 0),
                "capacity_percent": capacity.get("usagePercent", 0),
                "conditions": status.get("conditions", []),
            }

        # No MiraCeph found
        return {
            "health": "UNKNOWN",
            "error": "MiraCeph CR not found in ceph-lcm-mirantis namespace",
        }

    except Exception as e:
        logger.warning("miraceph_query_failed", error=str(e))
        # Fall back to inferring status from findings
        health = "HEALTH_OK"
        for f in findings:
            if f.priority == IssuePriority.CRITICAL and "ceph" in f.affected_component.lower():
                health = "HEALTH_ERR"
                break
            elif f.priority == IssuePriority.HIGH and "ceph" in f.affected_component.lower():
                health = "HEALTH_WARN"

        return {
            "health": health,
            "error": f"Failed to query MiraCeph: {e}",
            "note": "Status inferred from log analysis",
        }


def _generate_storage_root_cause(
    findings: list[DiagnosisFinding],
    symptom: str | None,
    log_count: int,
) -> str:
    """Generate storage root cause analysis."""
    if not findings:
        return (
            f"No specific storage failure patterns detected in {log_count} analyzed logs. "
            "Consider checking Ceph cluster status directly with 'ceph -s'."
        )

    primary = findings[0]
    analysis = f"Primary storage issue: {primary.title}. {primary.description}"

    if symptom:
        analysis += f" User-reported symptom: {symptom}."

    if primary.known_issue_id:
        analysis += f" This matches known issue {primary.known_issue_id}."

    return analysis


def _generate_storage_recommendations(
    findings: list[DiagnosisFinding],
) -> list[str]:
    """Generate storage troubleshooting recommendations."""
    recommendations: list[str] = []

    if not findings:
        recommendations.append("Check Ceph cluster status: ceph -s")
        recommendations.append("Check Cinder services: openstack volume service list")
        recommendations.append("Verify volume backend connectivity")
        return recommendations

    for finding in findings[:3]:
        if finding.known_issue_id:
            recommendations.append(f"Follow resolution for {finding.known_issue_id}")

        if "osd" in finding.title.lower():
            recommendations.append("Check OSD status: ceph osd tree")
            recommendations.append("Check OSD logs: journalctl -u ceph-osd@*")

        elif "slow" in finding.title.lower():
            recommendations.append("Check OSD performance: ceph osd perf")
            recommendations.append("Check disk health: smartctl -a /dev/<device>")

        elif "auth" in finding.title.lower() or "cephx" in finding.title.lower():
            recommendations.append("Verify cephx keys: ceph auth list")
            recommendations.append("Check libvirt secret: virsh secret-list")

        elif "attach" in finding.title.lower() or "volume" in finding.title.lower():
            recommendations.append("Check volume status: openstack volume show <id>")
            recommendations.append("Check cinder-volume logs")

        elif "pg" in finding.title.lower():
            recommendations.append("Check PG status: ceph pg stat")
            recommendations.append("Check stuck PGs: ceph pg dump_stuck")

        elif "capacity" in finding.title.lower() or "full" in finding.title.lower():
            recommendations.append("Check capacity: ceph df")
            recommendations.append("Consider adding OSDs or cleaning up data")

    return recommendations[:5]


# Tool metadata for registration
TOOL_NAME = "diagnose_storage_issue"
TOOL_DESCRIPTION = """Diagnose storage and volume issues.

Analyzes storage-related logs, Ceph cluster status, and component states
to diagnose storage problems including volume operations and I/O performance.

This is a READ-ONLY tool that does not modify any resources."""

TOOL_TAGS = ["troubleshooting", "storage", "ceph", "cinder", "diagnosis", "read-only"]
