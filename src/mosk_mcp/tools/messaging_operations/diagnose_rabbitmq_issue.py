"""Diagnose RabbitMQ issues tool.

This module provides the diagnose_rabbitmq_issue MCP tool for comprehensive
RabbitMQ diagnostics and issue detection.

Safety Level: Read-only
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from mosk_mcp.core.exceptions import ToolExecutionError
from mosk_mcp.observability.logging import get_logger
from mosk_mcp.tools.messaging_operations.get_rabbitmq_connections import (
    get_rabbitmq_connections,
)
from mosk_mcp.tools.messaging_operations.get_rabbitmq_status import (
    get_rabbitmq_status,
)
from mosk_mcp.tools.messaging_operations.list_rabbitmq_queues import (
    list_rabbitmq_queues,
)
from mosk_mcp.tools.messaging_operations.models import (
    DiagnoseRabbitMQIssueOutput,
    RabbitMQDiagnosticCheck,
    RabbitMQHealthLevel,
    RabbitMQInstanceDiagnosis,
)


if TYPE_CHECKING:
    from mosk_mcp.adapters.kubernetes import KubernetesAdapter


logger = get_logger(__name__)


# Known issue patterns from MOSK-001
KNOWN_ISSUES = {
    "MOSK-001": {
        "title": "RPC timeout from RabbitMQ connection exhaustion",
        "patterns": [
            "connection_pool_exhausted",
            "blocked_connections",
            "memory_alarm",
            "disk_alarm",
        ],
    },
}

# Thresholds for diagnostic checks
MEMORY_WARNING_THRESHOLD = 70.0
MEMORY_CRITICAL_THRESHOLD = 85.0
CONNECTION_WARNING_THRESHOLD = 70.0
CONNECTION_CRITICAL_THRESHOLD = 90.0
QUEUE_BACKLOG_THRESHOLD = 1000
STALE_QUEUE_THRESHOLD = 5


def _create_check(
    name: str,
    passed: bool,
    message: str,
    severity: Literal["info", "warning", "error", "critical"] = "info",
    details: dict | None = None,
) -> RabbitMQDiagnosticCheck:
    """Create a diagnostic check result.

    Args:
        name: Check name.
        passed: Whether the check passed.
        message: Check result message.
        severity: Severity level.
        details: Additional details.

    Returns:
        RabbitMQDiagnosticCheck instance.
    """
    status: Literal["pass", "warn", "fail", "skip"]
    if passed:
        status = "pass"
    elif severity in ("warning",):
        status = "warn"
    else:
        status = "fail"

    return RabbitMQDiagnosticCheck(
        check_name=name,
        status=status,
        message=message,
        severity=severity,
        details=details or {},
    )


async def _diagnose_instance(
    kubernetes_adapter: KubernetesAdapter,
    instance: Literal["main", "neutron"],
    include_queue_analysis: bool,
    include_connection_analysis: bool,
    check_for_known_issues: bool,
) -> RabbitMQInstanceDiagnosis:
    """Diagnose a single RabbitMQ instance.

    Args:
        kubernetes_adapter: Kubernetes adapter.
        instance: RabbitMQ instance to diagnose.
        include_queue_analysis: Include queue analysis.
        include_connection_analysis: Include connection analysis.
        check_for_known_issues: Check for known issue patterns.

    Returns:
        RabbitMQInstanceDiagnosis with results.
    """
    checks: list[RabbitMQDiagnosticCheck] = []
    issues_found: list[str] = []
    known_issue_matches: list[str] = []
    matched_patterns: set[str] = set()

    # Get status
    try:
        status = await get_rabbitmq_status(
            kubernetes_adapter,
            rabbitmq_instance=instance,
            include_feature_flags=False,
        )

        # Check 1: Node health
        if status.running_nodes == status.total_nodes:
            checks.append(
                _create_check(
                    "node_health",
                    True,
                    f"All {status.total_nodes} node(s) running",
                    "info",
                )
            )
        else:
            checks.append(
                _create_check(
                    "node_health",
                    False,
                    f"Only {status.running_nodes}/{status.total_nodes} node(s) running",
                    "critical",
                )
            )
            issues_found.append(
                f"Node(s) down: {status.total_nodes - status.running_nodes} not running"
            )

        # Check 2: Alarms
        if not status.has_alarms:
            checks.append(
                _create_check(
                    "alarms",
                    True,
                    "No alarms active",
                    "info",
                )
            )
        else:
            checks.append(
                _create_check(
                    "alarms",
                    False,
                    f"Active alarms: {', '.join(status.alarms)}",
                    "critical",
                )
            )
            issues_found.append(f"Active alarms: {', '.join(status.alarms)}")
            if "memory" in str(status.alarms).lower():
                matched_patterns.add("memory_alarm")
            if "disk" in str(status.alarms).lower():
                matched_patterns.add("disk_alarm")

        # Check 3: Network partitions
        if not status.has_partitions:
            checks.append(
                _create_check(
                    "network_partitions",
                    True,
                    "No network partitions",
                    "info",
                )
            )
        else:
            checks.append(
                _create_check(
                    "network_partitions",
                    False,
                    f"Network partitions detected: {', '.join(status.partitions)}",
                    "critical",
                )
            )
            issues_found.append(f"Network partitions: {', '.join(status.partitions)}")

        # Check 4: Memory usage
        if status.nodes:
            memory_percent = status.nodes[0].memory_percent
            if memory_percent < MEMORY_WARNING_THRESHOLD:
                checks.append(
                    _create_check(
                        "memory_usage",
                        True,
                        f"Memory usage normal: {memory_percent:.1f}%",
                        "info",
                        {"memory_percent": memory_percent},
                    )
                )
            elif memory_percent < MEMORY_CRITICAL_THRESHOLD:
                checks.append(
                    _create_check(
                        "memory_usage",
                        False,
                        f"Elevated memory usage: {memory_percent:.1f}%",
                        "warning",
                        {"memory_percent": memory_percent},
                    )
                )
                issues_found.append(f"Elevated memory: {memory_percent:.1f}%")
            else:
                checks.append(
                    _create_check(
                        "memory_usage",
                        False,
                        f"Critical memory usage: {memory_percent:.1f}%",
                        "critical",
                        {"memory_percent": memory_percent},
                    )
                )
                issues_found.append(f"Critical memory: {memory_percent:.1f}%")

    except ToolExecutionError as e:
        checks.append(
            _create_check(
                "status_check",
                False,
                f"Failed to get status: {e}",
                "error",
            )
        )
        issues_found.append(f"Status check failed: {e}")

    # Queue analysis
    if include_queue_analysis:
        try:
            queues = await list_rabbitmq_queues(
                kubernetes_adapter,
                rabbitmq_instance=instance,
                show_empty=False,
                limit=500,
            )

            # Check 5: Queue backlog
            if not queues.has_backlog:
                checks.append(
                    _create_check(
                        "queue_backlog",
                        True,
                        "No significant queue backlogs",
                        "info",
                    )
                )
            else:
                checks.append(
                    _create_check(
                        "queue_backlog",
                        False,
                        f"Queue backlog detected: {queues.total_messages} total messages",
                        "warning",
                        {"total_messages": queues.total_messages},
                    )
                )
                issues_found.append(f"Message backlog: {queues.total_messages} messages")

            # Check 6: Stale queues
            if queues.stale_queue_count < STALE_QUEUE_THRESHOLD:
                checks.append(
                    _create_check(
                        "stale_queues",
                        True,
                        f"{queues.stale_queue_count} stale queue(s) - within threshold",
                        "info",
                    )
                )
            else:
                checks.append(
                    _create_check(
                        "stale_queues",
                        False,
                        f"{queues.stale_queue_count} stale queues (messages with no consumers)",
                        "warning",
                        {"stale_queue_count": queues.stale_queue_count},
                    )
                )
                issues_found.append(f"Stale queues: {queues.stale_queue_count}")

        except ToolExecutionError as e:
            checks.append(
                _create_check(
                    "queue_analysis",
                    False,
                    f"Failed to analyze queues: {e}",
                    "error",
                )
            )

    # Connection analysis
    if include_connection_analysis:
        try:
            connections = await get_rabbitmq_connections(
                kubernetes_adapter,
                rabbitmq_instance=instance,
                include_channels=False,
                group_by_user=True,
                limit=500,
            )

            # Check 7: Blocked connections
            if not connections.has_blocked_connections:
                checks.append(
                    _create_check(
                        "blocked_connections",
                        True,
                        "No blocked connections",
                        "info",
                    )
                )
            else:
                checks.append(
                    _create_check(
                        "blocked_connections",
                        False,
                        f"{connections.blocked_connections} connection(s) blocked",
                        "critical",
                        {"blocked_count": connections.blocked_connections},
                    )
                )
                issues_found.append(f"Blocked connections: {connections.blocked_connections}")
                matched_patterns.add("blocked_connections")

            # Check 8: Connection pool utilization
            if connections.connection_utilization_percent is not None:
                util = connections.connection_utilization_percent
                if util < CONNECTION_WARNING_THRESHOLD:
                    checks.append(
                        _create_check(
                            "connection_pool",
                            True,
                            f"Connection pool utilization: {util:.1f}%",
                            "info",
                        )
                    )
                elif util < CONNECTION_CRITICAL_THRESHOLD:
                    checks.append(
                        _create_check(
                            "connection_pool",
                            False,
                            f"Elevated connection pool utilization: {util:.1f}%",
                            "warning",
                            {"utilization_percent": util},
                        )
                    )
                    issues_found.append(f"Connection pool at {util:.1f}%")
                else:
                    checks.append(
                        _create_check(
                            "connection_pool",
                            False,
                            f"Critical connection pool utilization: {util:.1f}%",
                            "critical",
                            {"utilization_percent": util},
                        )
                    )
                    issues_found.append(f"Connection pool exhaustion: {util:.1f}%")
                    matched_patterns.add("connection_pool_exhausted")

        except ToolExecutionError as e:
            checks.append(
                _create_check(
                    "connection_analysis",
                    False,
                    f"Failed to analyze connections: {e}",
                    "error",
                )
            )

    # Check for known issues
    if check_for_known_issues and matched_patterns:
        for issue_id, issue_info in KNOWN_ISSUES.items():
            matching_patterns = set(issue_info["patterns"]) & matched_patterns
            if matching_patterns:
                known_issue_matches.append(issue_id)
                logger.info(
                    "known_issue_matched",
                    issue_id=issue_id,
                    patterns=list(matching_patterns),
                )

    # Determine overall health for this instance
    failed_checks = [c for c in checks if c.status == "fail"]
    warned_checks = [c for c in checks if c.status == "warn"]

    if any(c.severity == "critical" for c in failed_checks):
        health = RabbitMQHealthLevel.CRITICAL
    elif failed_checks or warned_checks:
        health = RabbitMQHealthLevel.WARNING
    else:
        health = RabbitMQHealthLevel.HEALTHY

    return RabbitMQInstanceDiagnosis(
        instance=instance,
        health=health,
        checks=checks,
        issues_found=issues_found,
        known_issue_matches=known_issue_matches,
    )


def _generate_recommendations(
    instances: list[RabbitMQInstanceDiagnosis],
    overall_health: RabbitMQHealthLevel,
    critical_issues: list[str],
) -> list[str]:
    """Generate recommendations based on diagnosis.

    Args:
        instances: Diagnosis per instance.
        overall_health: Overall health level.
        critical_issues: List of critical issues.

    Returns:
        List of recommendation strings.
    """
    recommendations = []

    if overall_health == RabbitMQHealthLevel.CRITICAL:
        recommendations.append("IMMEDIATE ACTION REQUIRED: Critical RabbitMQ issues detected.")

    # Check for known issues
    known_ids = set()
    for inst in instances:
        known_ids.update(inst.known_issue_matches)

    if "MOSK-001" in known_ids:
        recommendations.extend(
            [
                "Pattern matches MOSK-001 (RPC timeout from connection exhaustion):",
                "  1. Check connection limits: rabbitmqctl list_connections | wc -l",
                "  2. Restart affected OpenStack services to release stale connections",
                "  3. Consider scaling RabbitMQ or increasing connection limits",
            ]
        )

    if critical_issues:
        if any("alarm" in issue.lower() for issue in critical_issues):
            recommendations.append(
                "Investigate active alarms: memory or disk pressure may be causing issues"
            )
        if any("blocked" in issue.lower() for issue in critical_issues):
            recommendations.append(
                "Blocked connections indicate RabbitMQ is under resource pressure. "
                "This will cause RPC timeouts in OpenStack services."
            )
        if any("partition" in issue.lower() for issue in critical_issues):
            recommendations.append(
                "Network partitions are critical - cluster may be in split-brain state. "
                "Immediate intervention required."
            )

    if overall_health == RabbitMQHealthLevel.HEALTHY:
        recommendations.append("RabbitMQ messaging system is healthy - no action required.")

    return recommendations


async def diagnose_rabbitmq_issue(
    kubernetes_adapter: KubernetesAdapter,
    rabbitmq_instance: Literal["main", "neutron", "all"] = "all",
    include_queue_analysis: bool = True,
    include_connection_analysis: bool = True,
    check_for_known_issues: bool = True,
) -> DiagnoseRabbitMQIssueOutput:
    """Diagnose RabbitMQ issues comprehensively.

    This tool performs comprehensive diagnostics on RabbitMQ instances,
    checking for common issues and matching against known problem patterns.

    Safety Level: Read-only

    Args:
        kubernetes_adapter: Kubernetes adapter for cluster communication.
        rabbitmq_instance: Instance to diagnose ('main', 'neutron', or 'all').
        include_queue_analysis: Include queue depth and consumer analysis.
        include_connection_analysis: Include connection pool analysis.
        check_for_known_issues: Check against known issue patterns.

    Returns:
        DiagnoseRabbitMQIssueOutput with diagnosis results.

    Raises:
        ToolExecutionError: If diagnosis fails.

    Example:
        >>> diagnosis = await diagnose_rabbitmq_issue(k8s_adapter)
        >>> if not diagnosis.is_healthy:
        ...     print(f"Issues found: {diagnosis.critical_issues}")
        ...     print(f"Recommendations: {diagnosis.recommendations}")
    """
    logger.info(
        "diagnosing_rabbitmq_issue",
        instance=rabbitmq_instance,
        include_queue_analysis=include_queue_analysis,
        include_connection_analysis=include_connection_analysis,
    )

    try:
        # Determine instances to check
        if rabbitmq_instance == "all":
            instances_to_check: list[Literal["main", "neutron"]] = ["main", "neutron"]
        else:
            instances_to_check = [rabbitmq_instance]

        # Diagnose each instance
        instance_results: list[RabbitMQInstanceDiagnosis] = []
        for inst in instances_to_check:
            try:
                result = await _diagnose_instance(
                    kubernetes_adapter=kubernetes_adapter,
                    instance=inst,
                    include_queue_analysis=include_queue_analysis,
                    include_connection_analysis=include_connection_analysis,
                    check_for_known_issues=check_for_known_issues,
                )
                instance_results.append(result)
            except ToolExecutionError as e:
                logger.warning(
                    "instance_diagnosis_failed",
                    instance=inst,
                    error=str(e),
                )
                # Create a failed diagnosis for this instance
                instance_results.append(
                    RabbitMQInstanceDiagnosis(
                        instance=inst,
                        health=RabbitMQHealthLevel.UNKNOWN,
                        checks=[
                            _create_check(
                                "instance_check",
                                False,
                                f"Failed to diagnose instance: {e}",
                                "error",
                            )
                        ],
                        issues_found=[f"Diagnosis failed: {e}"],
                        known_issue_matches=[],
                    )
                )

        # Calculate overall statistics
        total_checks = sum(len(inst.checks) for inst in instance_results)
        checks_passed = sum(
            1 for inst in instance_results for check in inst.checks if check.status == "pass"
        )
        checks_warned = sum(
            1 for inst in instance_results for check in inst.checks if check.status == "warn"
        )
        checks_failed = sum(
            1 for inst in instance_results for check in inst.checks if check.status == "fail"
        )

        # Collect all issues
        critical_issues = []
        warnings = []
        known_issue_ids = set()

        for inst in instance_results:
            for issue in inst.issues_found:
                # Check severity based on associated checks
                is_critical = any(
                    c.severity == "critical" and issue.lower() in c.message.lower()
                    for c in inst.checks
                )
                if is_critical:
                    critical_issues.append(f"[{inst.instance}] {issue}")
                else:
                    warnings.append(f"[{inst.instance}] {issue}")
            known_issue_ids.update(inst.known_issue_matches)

        # Determine overall health
        instance_healths = [inst.health for inst in instance_results]
        if RabbitMQHealthLevel.CRITICAL in instance_healths:
            overall_health = RabbitMQHealthLevel.CRITICAL
        elif RabbitMQHealthLevel.WARNING in instance_healths:
            overall_health = RabbitMQHealthLevel.WARNING
        elif RabbitMQHealthLevel.UNKNOWN in instance_healths:
            overall_health = RabbitMQHealthLevel.UNKNOWN
        else:
            overall_health = RabbitMQHealthLevel.HEALTHY

        # Generate summary
        if overall_health == RabbitMQHealthLevel.HEALTHY:
            health_summary = "RabbitMQ messaging system is healthy"
        elif overall_health == RabbitMQHealthLevel.WARNING:
            health_summary = f"RabbitMQ has {len(warnings)} warning(s) requiring attention"
        elif overall_health == RabbitMQHealthLevel.CRITICAL:
            health_summary = (
                f"RabbitMQ has {len(critical_issues)} critical issue(s) requiring immediate action"
            )
        else:
            health_summary = "Unable to determine RabbitMQ health status"

        # Generate recommendations
        recommendations = _generate_recommendations(
            instances=instance_results,
            overall_health=overall_health,
            critical_issues=critical_issues,
        )

        output = DiagnoseRabbitMQIssueOutput(
            instances=instance_results,
            overall_health=overall_health,
            health_summary=health_summary,
            total_checks=total_checks,
            checks_passed=checks_passed,
            checks_warned=checks_warned,
            checks_failed=checks_failed,
            critical_issues=critical_issues,
            warnings=warnings,
            known_issue_ids=list(known_issue_ids),
            is_healthy=overall_health == RabbitMQHealthLevel.HEALTHY,
            requires_immediate_action=overall_health == RabbitMQHealthLevel.CRITICAL,
            recommendations=recommendations,
        )

        logger.info(
            "rabbitmq_diagnosis_complete",
            overall_health=overall_health.value,
            total_checks=total_checks,
            checks_passed=checks_passed,
            checks_failed=checks_failed,
            critical_issues=len(critical_issues),
        )

        return output

    except Exception as e:
        logger.error("diagnose_rabbitmq_issue_failed", error=str(e))
        raise ToolExecutionError(
            message=f"Failed to diagnose RabbitMQ issues: {e}",
            tool_name="diagnose_rabbitmq_issue",
            details={"error": str(e), "instance": rabbitmq_instance},
        ) from e
