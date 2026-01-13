"""Known issue database and pattern matching for MOSK troubleshooting.

This module provides a database of known issues with pattern matching
capabilities to identify issues based on symptoms, error messages,
and log patterns.

The known issues are based on common problems encountered in
Mirantis OpenStack for Kubernetes deployments.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from mosk_mcp.observability.logging import get_logger
from mosk_mcp.tools.troubleshooting.models import (
    DiagnosisCategory,
    IssuePriority,
    KnownIssue,
)


logger = get_logger(__name__)


@dataclass
class IssuePattern:
    """Pattern definition for matching known issues.

    Attributes:
        issue_id: Unique issue identifier.
        title: Issue title.
        category: Issue category.
        priority: Issue priority.
        symptoms: List of symptoms.
        root_cause: Root cause description.
        affected_services: Services affected.
        affected_versions: MOSK versions affected.
        resolution: Resolution steps.
        workaround: Temporary workaround.
        requires_crq: Whether fix requires CRQ.
        documentation_url: Link to documentation.
        is_resolved_upstream: Whether fixed in newer version.
        error_patterns: Regex patterns for error messages.
        log_patterns: Regex patterns for log messages.
        symptom_keywords: Keywords that indicate this issue.
        service_patterns: Service-specific patterns.
    """

    issue_id: str
    title: str
    category: DiagnosisCategory
    priority: IssuePriority
    symptoms: list[str]
    root_cause: str
    affected_services: list[str]
    resolution: str
    affected_versions: list[str] = field(default_factory=list)
    workaround: str | None = None
    requires_crq: bool = False
    documentation_url: str | None = None
    is_resolved_upstream: bool = False
    error_patterns: list[str] = field(default_factory=list)
    log_patterns: list[str] = field(default_factory=list)
    symptom_keywords: list[str] = field(default_factory=list)
    service_patterns: dict[str, list[str]] = field(default_factory=dict)

    def __hash__(self) -> int:
        """Make IssuePattern hashable by issue_id."""
        return hash(self.issue_id)

    def __eq__(self, other: object) -> bool:
        """Check equality based on issue_id."""
        if not isinstance(other, IssuePattern):
            return NotImplemented
        return self.issue_id == other.issue_id

    def __post_init__(self) -> None:
        """Compile regex patterns for efficiency."""
        self._compiled_error_patterns: list[re.Pattern[str]] = []
        self._compiled_log_patterns: list[re.Pattern[str]] = []

        for pattern in self.error_patterns:
            try:
                self._compiled_error_patterns.append(re.compile(pattern, re.IGNORECASE))
            except re.error as e:
                logger.warning(
                    "invalid_error_pattern",
                    issue_id=self.issue_id,
                    pattern=pattern,
                    error=str(e),
                )

        for pattern in self.log_patterns:
            try:
                self._compiled_log_patterns.append(re.compile(pattern, re.IGNORECASE))
            except re.error as e:
                logger.warning(
                    "invalid_log_pattern",
                    issue_id=self.issue_id,
                    pattern=pattern,
                    error=str(e),
                )

    def match_score(
        self,
        error_message: str | None = None,
        symptoms: list[str] | None = None,
        service: str | None = None,
        log_messages: list[str] | None = None,
    ) -> float:
        """Calculate match score for this issue against input.

        Args:
            error_message: Error message to match.
            symptoms: List of symptoms to match.
            service: Service name.
            log_messages: Log messages to match.

        Returns:
            Match score between 0.0 and 1.0.
        """
        score = 0.0
        max_score = 0.0

        # Match error patterns (high weight)
        if error_message and self._compiled_error_patterns:
            max_score += 0.4
            for pattern in self._compiled_error_patterns:
                if pattern.search(error_message):
                    score += 0.4
                    break

        # Match log patterns (high weight)
        if log_messages and self._compiled_log_patterns:
            max_score += 0.3
            matched_patterns = 0
            for msg in log_messages:
                for pattern in self._compiled_log_patterns:
                    if pattern.search(msg):
                        matched_patterns += 1
                        break
            if self._compiled_log_patterns:
                pattern_ratio = matched_patterns / len(self._compiled_log_patterns)
                score += 0.3 * min(pattern_ratio, 1.0)

        # Match symptom keywords
        if symptoms and self.symptom_keywords:
            max_score += 0.2
            symptoms_lower = [s.lower() for s in symptoms]
            symptoms_text = " ".join(symptoms_lower)
            matched_keywords = sum(1 for kw in self.symptom_keywords if kw.lower() in symptoms_text)
            if self.symptom_keywords:
                keyword_ratio = matched_keywords / len(self.symptom_keywords)
                score += 0.2 * min(keyword_ratio, 1.0)

        # Match service (medium weight)
        if service and self.affected_services:
            max_score += 0.1
            if service.lower() in [s.lower() for s in self.affected_services]:
                score += 0.1

        # Normalize score
        if max_score > 0:
            return min(score / max_score, 1.0)
        return 0.0

    def to_known_issue(self, match_score: float = 0.0) -> KnownIssue:
        """Convert to KnownIssue model.

        Args:
            match_score: Match score to include.

        Returns:
            KnownIssue model instance.
        """
        return KnownIssue(
            issue_id=self.issue_id,
            title=self.title,
            category=self.category,
            priority=self.priority,
            symptoms=self.symptoms,
            root_cause=self.root_cause,
            affected_services=self.affected_services,
            affected_versions=self.affected_versions,
            resolution=self.resolution,
            workaround=self.workaround,
            requires_crq=self.requires_crq,
            documentation_url=self.documentation_url,
            is_resolved_upstream=self.is_resolved_upstream,
            match_score=match_score,
        )


# =============================================================================
# Known Issue Database
# =============================================================================

KNOWN_ISSUES: list[IssuePattern] = [
    # MOSK-001: RabbitMQ Connection Exhaustion
    IssuePattern(
        issue_id="MOSK-001",
        title="RPC timeout errors due to RabbitMQ connection exhaustion",
        category=DiagnosisCategory.SERVICE_ISSUE,
        priority=IssuePriority.HIGH,
        symptoms=[
            "RPC timeout errors in nova logs",
            "Slow API responses",
            "Nova operations hanging",
            "Services failing to communicate",
        ],
        root_cause="RabbitMQ connection exhaustion caused by too many concurrent connections or connection leaks",
        affected_services=["nova", "neutron", "cinder", "rabbitmq"],
        resolution=(
            "1. Check RabbitMQ connection limits: rabbitmqctl list_connections | wc -l\n"
            "2. Check for connection leaks in services\n"
            "3. Scale RabbitMQ cluster if needed\n"
            "4. Increase connection limits in rabbitmq.conf\n"
            "5. Restart affected OpenStack services to clear stale connections"
        ),
        workaround="Restart affected OpenStack services to temporarily clear connections",
        requires_crq=True,
        error_patterns=[
            r"rpc\s*timeout",
            r"MessagingTimeout",
            r"AMQP\s*server.*closed\s*the\s*connection",
            r"connection.*reset.*by.*peer",
            r"unable\s*to\s*connect\s*to\s*amqp",
        ],
        log_patterns=[
            r"MessagingTimeout.*waiting.*for.*response",
            r"AMQP\s*connection.*closed",
            r"rabbit.*connection.*error",
        ],
        symptom_keywords=["rpc", "timeout", "rabbitmq", "amqp", "messaging", "connection"],
        service_patterns={
            "nova": [r"rpc.*timeout", r"messaging.*error"],
            "neutron": [r"rpc.*timeout"],
            "rabbitmq": [r"connection.*limit", r"connection.*refused"],
        },
    ),
    # MOSK-002: Ceph Slow Requests
    IssuePattern(
        issue_id="MOSK-002",
        title="Ceph slow requests exceeding 30 seconds",
        category=DiagnosisCategory.STORAGE_ISSUE,
        priority=IssuePriority.HIGH,
        symptoms=[
            "Ceph slow requests > 30s warnings",
            "Volume operations taking too long",
            "VM storage I/O latency",
            "Ceph HEALTH_WARN status",
        ],
        root_cause="OSD disk I/O saturation, often caused by failing disks, excessive replication, or undersized storage",
        affected_services=["ceph", "cinder", "nova"],
        resolution=(
            "1. Check OSD disk health: smartctl -a /dev/sdX\n"
            "2. Check OSD utilization: ceph osd df\n"
            "3. Identify slow OSDs: ceph osd perf\n"
            "4. Check for recovery operations: ceph -s\n"
            "5. Consider SSD upgrade for HDD-backed OSDs\n"
            "6. Add more OSDs to distribute load"
        ),
        workaround="Limit concurrent operations or pause non-critical workloads",
        requires_crq=True,
        error_patterns=[
            r"slow\s*request.*\d+\.\d+\s*seconds",
            r"osd.*blocked.*for.*seconds",
            r"slow.*ops",
        ],
        log_patterns=[
            r"slow\s*request",
            r"blocked\s*for",
            r"request.*taking.*too.*long",
        ],
        symptom_keywords=["slow", "ceph", "osd", "latency", "io", "disk", "blocked"],
        service_patterns={
            "ceph": [r"slow.*request", r"blocked.*for"],
            "cinder": [r"rbd.*timeout", r"volume.*timeout"],
        },
    ),
    # MOSK-003: Live Migration Stuck at 99%
    IssuePattern(
        issue_id="MOSK-003",
        title="Live migration stuck at 99% completion",
        category=DiagnosisCategory.VM_FAILURE,
        priority=IssuePriority.MEDIUM,
        symptoms=[
            "Live migration stuck at 99%",
            "Migration taking too long",
            "VM unresponsive during migration",
            "Memory dirty rate warnings",
        ],
        root_cause="Memory dirty rate exceeds migration bandwidth - VM is writing memory faster than it can be transferred",
        affected_services=["nova", "libvirt"],
        resolution=(
            "1. Check VM memory dirty rate: virsh domjobinfo <domain>\n"
            "2. Reduce VM workload if possible\n"
            "3. Increase migration bandwidth in nova.conf\n"
            "4. Consider using post-copy migration\n"
            "5. Abort migration if necessary: nova live-migration-abort <server>"
        ),
        workaround="Reduce VM workload or abort migration and retry during lower activity",
        requires_crq=False,
        error_patterns=[
            r"migration.*stuck",
            r"migration.*99.*percent",
            r"dirty.*rate.*exceeds",
            r"migration.*timed.*out",
        ],
        log_patterns=[
            r"migration.*progress.*99",
            r"dirty.*memory.*rate",
            r"migration.*taking.*too.*long",
        ],
        symptom_keywords=["migration", "stuck", "99%", "dirty", "memory", "bandwidth"],
        service_patterns={
            "nova": [r"live.*migration.*stuck", r"migration.*timeout"],
            "libvirt": [r"migration.*failed", r"dirty.*rate"],
        },
    ),
    # MOSK-004: Nova Compute Service Down
    IssuePattern(
        issue_id="MOSK-004",
        title="Nova-compute service down due to libvirt failure",
        category=DiagnosisCategory.SERVICE_ISSUE,
        priority=IssuePriority.CRITICAL,
        symptoms=[
            "Nova-compute service down",
            "Cannot spawn VMs on host",
            "Libvirt connection refused",
            "Compute host not responding",
        ],
        root_cause="Libvirt connection failure, often due to libvirtd service crash or socket permission issues",
        affected_services=["nova", "libvirt"],
        resolution=(
            "1. Check libvirt status: systemctl status libvirtd\n"
            "2. Check socket permissions: ls -la /var/run/libvirt/libvirt-sock\n"
            "3. Restart libvirt: systemctl restart libvirtd\n"
            "4. Restart nova-compute: systemctl restart nova-compute\n"
            "5. Verify connectivity: virsh list --all"
        ),
        workaround="Restart libvirt and nova-compute services",
        requires_crq=False,
        error_patterns=[
            r"libvirt.*connection.*refused",
            r"cannot.*connect.*to.*libvirt",
            r"unable.*to.*open.*connection.*to.*libvirt",
            r"socket.*permission.*denied",
        ],
        log_patterns=[
            r"libvirt.*error",
            r"connection.*refused.*libvirt",
            r"failed.*to.*connect.*hypervisor",
        ],
        symptom_keywords=[
            "libvirt",
            "nova-compute",
            "connection",
            "refused",
            "socket",
            "hypervisor",
        ],
        service_patterns={
            "nova": [r"libvirt.*connection.*error", r"hypervisor.*unavailable"],
        },
    ),
    # MOSK-005: Volume Attach Timeout
    IssuePattern(
        issue_id="MOSK-005",
        title="Volume attachment timeout due to Ceph auth failure",
        category=DiagnosisCategory.STORAGE_ISSUE,
        priority=IssuePriority.HIGH,
        symptoms=[
            "Volume attach timeout",
            "Ceph auth failure errors",
            "Instance volume attachment failing",
            "cephx authentication errors",
        ],
        root_cause="Ceph client authentication failure due to invalid or expired cephx keys",
        affected_services=["cinder", "nova", "ceph"],
        resolution=(
            "1. Verify ceph keyring on compute nodes\n"
            "2. Regenerate cephx keys if necessary: ceph auth get-or-create client.cinder\n"
            "3. Update ceph secret in libvirt: virsh secret-define --file secret.xml\n"
            "4. Restart cinder-volume and nova-compute services\n"
            "5. Verify with: rbd ls volumes"
        ),
        workaround="Manually update ceph secrets on affected hosts",
        requires_crq=True,
        error_patterns=[
            r"cephx.*auth.*fail",
            r"auth.*failure.*ceph",
            r"permission.*denied.*rbd",
            r"volume.*attach.*timeout",
        ],
        log_patterns=[
            r"ceph.*auth.*error",
            r"cephx.*authentication.*failed",
            r"rbd.*map.*failed",
        ],
        symptom_keywords=["cephx", "auth", "volume", "attach", "timeout", "permission", "rbd"],
        service_patterns={
            "cinder": [r"ceph.*auth", r"rbd.*error"],
            "nova": [r"volume.*attach.*fail", r"disk.*attach.*error"],
        },
    ),
    # MOSK-006: Neutron Agent Down
    IssuePattern(
        issue_id="MOSK-006",
        title="Neutron agent not responding",
        category=DiagnosisCategory.NETWORK_ISSUE,
        priority=IssuePriority.HIGH,
        symptoms=[
            "Neutron agent down alerts",
            "Network connectivity issues",
            "New VMs cannot get network",
            "DHCP failures",
        ],
        root_cause="Neutron agent crashed or lost connectivity to message queue",
        affected_services=["neutron", "rabbitmq"],
        resolution=(
            "1. Check agent status: openstack network agent list\n"
            "2. Check agent logs: journalctl -u neutron-*-agent\n"
            "3. Verify RabbitMQ connectivity\n"
            "4. Restart affected agent: systemctl restart neutron-<agent>-agent\n"
            "5. Verify OVS bridge status: ovs-vsctl show"
        ),
        workaround="Restart the affected Neutron agent",
        requires_crq=False,
        error_patterns=[
            r"neutron.*agent.*down",
            r"agent.*not.*responding",
            r"dhcp.*agent.*error",
            r"l3.*agent.*failed",
        ],
        log_patterns=[
            r"agent.*heartbeat.*missed",
            r"agent.*state.*down",
            r"failed.*to.*report.*state",
        ],
        symptom_keywords=["neutron", "agent", "down", "dhcp", "l3", "ovs", "network"],
        service_patterns={
            "neutron": [r"agent.*down", r"heartbeat.*failed"],
        },
    ),
    # MOSK-007: OSD Down
    IssuePattern(
        issue_id="MOSK-007",
        title="Ceph OSD marked down",
        category=DiagnosisCategory.STORAGE_ISSUE,
        priority=IssuePriority.CRITICAL,
        symptoms=[
            "OSD marked down",
            "Ceph HEALTH_WARN/HEALTH_ERR",
            "PGs in degraded state",
            "Recovery in progress",
        ],
        root_cause="OSD failure due to disk failure, network issues, or OSD daemon crash",
        affected_services=["ceph", "cinder", "nova"],
        resolution=(
            "1. Check OSD status: ceph osd tree\n"
            "2. Check OSD logs: journalctl -u ceph-osd@<id>\n"
            "3. Check disk health: smartctl -a /dev/<device>\n"
            "4. If disk failed, replace disk and recreate OSD\n"
            "5. If daemon crashed, restart: systemctl restart ceph-osd@<id>"
        ),
        workaround="Monitor cluster health and ensure replication maintains data availability",
        requires_crq=True,
        error_patterns=[
            r"osd\.\d+.*down",
            r"osd.*marked.*out",
            r"osd.*boot.*failed",
        ],
        log_patterns=[
            r"osd.*marked.*down",
            r"osd.*daemon.*crashed",
            r"disk.*error",
        ],
        symptom_keywords=["osd", "down", "ceph", "degraded", "pg", "recovery"],
        service_patterns={
            "ceph": [r"osd.*down", r"health.*warn"],
        },
    ),
    # MOSK-008: Database Connection Pool Exhaustion
    IssuePattern(
        issue_id="MOSK-008",
        title="Database connection pool exhaustion",
        category=DiagnosisCategory.SERVICE_ISSUE,
        priority=IssuePriority.HIGH,
        symptoms=[
            "Database connection errors",
            "API requests failing",
            "Services returning 503 errors",
            "Connection pool exhausted messages",
        ],
        root_cause="Too many concurrent database connections or connection leaks",
        affected_services=["nova", "neutron", "cinder", "keystone", "mariadb"],
        resolution=(
            "1. Check DB connections: SHOW PROCESSLIST;\n"
            "2. Increase max_connections in MariaDB if needed\n"
            "3. Increase connection pool size in service configs\n"
            "4. Check for connection leaks\n"
            "5. Restart affected services to clear stale connections"
        ),
        workaround="Restart affected services to release connections",
        requires_crq=True,
        error_patterns=[
            r"too.*many.*connections",
            r"connection.*pool.*exhausted",
            r"database.*connection.*error",
            r"mysql.*connection.*error",
        ],
        log_patterns=[
            r"connection.*pool.*full",
            r"unable.*to.*acquire.*connection",
            r"database.*error",
        ],
        symptom_keywords=["database", "connection", "pool", "exhausted", "mysql", "mariadb"],
        service_patterns={
            "mariadb": [r"max.*connections", r"too.*many"],
            "nova": [r"db.*connection.*error"],
        },
    ),
    # MOSK-009: Keystone Token Validation Failures
    IssuePattern(
        issue_id="MOSK-009",
        title="Keystone token validation failures",
        category=DiagnosisCategory.AUTHENTICATION_ISSUE,
        priority=IssuePriority.HIGH,
        symptoms=[
            "Authentication failures",
            "401 Unauthorized errors",
            "Token validation timeout",
            "Services unable to authenticate",
        ],
        root_cause="Keystone service overloaded, token expired, or memcached issues",
        affected_services=["keystone", "memcached"],
        resolution=(
            "1. Check Keystone status: openstack token issue\n"
            "2. Check memcached: telnet memcached 11211\n"
            "3. Verify Keystone endpoints: openstack endpoint list\n"
            "4. Clear token cache if corrupted\n"
            "5. Restart Keystone if necessary"
        ),
        workaround="Clear memcached cache and retry authentication",
        requires_crq=False,
        error_patterns=[
            r"401.*unauthorized",
            r"token.*validation.*failed",
            r"authentication.*failure",
            r"invalid.*token",
        ],
        log_patterns=[
            r"token.*expired",
            r"unable.*to.*validate.*token",
            r"authentication.*error",
        ],
        symptom_keywords=["keystone", "token", "auth", "401", "unauthorized", "validation"],
        service_patterns={
            "keystone": [r"token.*invalid", r"auth.*failure"],
        },
    ),
    # MOSK-010: Scheduler No Valid Host
    IssuePattern(
        issue_id="MOSK-010",
        title="No valid host found for instance scheduling",
        category=DiagnosisCategory.VM_FAILURE,
        priority=IssuePriority.MEDIUM,
        symptoms=[
            "No valid host found error",
            "Instance creation failing",
            "Scheduler filter failures",
            "All compute hosts filtered out",
        ],
        root_cause="No compute hosts meet scheduling requirements (CPU, RAM, disk, or custom filters)",
        affected_services=["nova"],
        resolution=(
            "1. Check compute hosts: openstack compute service list\n"
            "2. Check host resources: openstack hypervisor stats show\n"
            "3. Review scheduler filters: nova-manage cell_v2 list_hosts\n"
            "4. Check for over-committed hosts\n"
            "5. Add more compute capacity if needed"
        ),
        workaround="Use explicit host targeting or reduce instance requirements",
        requires_crq=False,
        error_patterns=[
            r"no.*valid.*host.*found",
            r"scheduler.*no.*hosts",
            r"filter.*returned.*0.*hosts",
        ],
        log_patterns=[
            r"no.*valid.*host",
            r"all.*hosts.*filtered",
            r"scheduler.*failed",
        ],
        symptom_keywords=["scheduler", "host", "filter", "capacity", "spawn", "no valid"],
        service_patterns={
            "nova": [r"no.*valid.*host", r"scheduler.*error"],
        },
    ),
]


class KnownIssueDatabase:
    """Database of known issues with pattern matching.

    Attributes:
        _issues: List of known issue patterns.
        _index_by_id: Index of issues by ID.
        _index_by_category: Index of issues by category.
        _index_by_service: Index of issues by service.

    Example:
        db = KnownIssueDatabase()
        matches = db.find_matching_issues(
            error_message="RPC timeout waiting for response",
            service="nova",
        )
    """

    def __init__(self, issues: list[IssuePattern] | None = None) -> None:
        """Initialize the database.

        Args:
            issues: Optional custom list of issues (defaults to KNOWN_ISSUES).
        """
        self._issues = issues if issues is not None else KNOWN_ISSUES
        self._build_indexes()

    def _build_indexes(self) -> None:
        """Build indexes for efficient lookup."""
        self._index_by_id: dict[str, IssuePattern] = {}
        self._index_by_category: dict[DiagnosisCategory, list[IssuePattern]] = {}
        self._index_by_service: dict[str, list[IssuePattern]] = {}

        for issue in self._issues:
            # Index by ID
            self._index_by_id[issue.issue_id] = issue

            # Index by category
            if issue.category not in self._index_by_category:
                self._index_by_category[issue.category] = []
            self._index_by_category[issue.category].append(issue)

            # Index by service
            for service in issue.affected_services:
                service_lower = service.lower()
                if service_lower not in self._index_by_service:
                    self._index_by_service[service_lower] = []
                self._index_by_service[service_lower].append(issue)

    def get_by_id(self, issue_id: str) -> IssuePattern | None:
        """Get issue by ID.

        Args:
            issue_id: Issue ID (e.g., 'MOSK-001').

        Returns:
            IssuePattern if found, None otherwise.
        """
        return self._index_by_id.get(issue_id)

    def get_by_category(self, category: DiagnosisCategory) -> list[IssuePattern]:
        """Get issues by category.

        Args:
            category: Issue category.

        Returns:
            List of matching issues.
        """
        return self._index_by_category.get(category, [])

    def get_by_service(self, service: str) -> list[IssuePattern]:
        """Get issues by service.

        Args:
            service: Service name.

        Returns:
            List of matching issues.
        """
        return self._index_by_service.get(service.lower(), [])

    def get_all(self) -> list[IssuePattern]:
        """Get all known issues.

        Returns:
            List of all issue patterns.
        """
        return list(self._issues)

    def find_matching_issues(
        self,
        error_message: str | None = None,
        symptoms: list[str] | None = None,
        service: str | None = None,
        category: DiagnosisCategory | None = None,
        log_messages: list[str] | None = None,
        min_score: float = 0.1,
        limit: int = 10,
    ) -> list[tuple[IssuePattern, float]]:
        """Find issues matching the given criteria.

        Args:
            error_message: Error message to match.
            symptoms: List of symptoms.
            service: Service name to filter.
            category: Category to filter.
            log_messages: Log messages to analyze.
            min_score: Minimum match score (0-1).
            limit: Maximum number of results.

        Returns:
            List of (IssuePattern, score) tuples sorted by score descending.
        """
        # Start with all issues or filtered set
        candidates = self._issues

        if service:
            service_issues = set(self.get_by_service(service))
            if service_issues:
                candidates = [i for i in candidates if i in service_issues]

        if category:
            category_issues = set(self.get_by_category(category))
            if category_issues:
                candidates = [i for i in candidates if i in category_issues]

        # Score each candidate
        scored: list[tuple[IssuePattern, float]] = []
        for issue in candidates:
            score = issue.match_score(
                error_message=error_message,
                symptoms=symptoms,
                service=service,
                log_messages=log_messages,
            )
            if score >= min_score:
                scored.append((issue, score))

        # Sort by score descending
        scored.sort(key=lambda x: x[1], reverse=True)

        logger.debug(
            "found_matching_issues",
            total_candidates=len(candidates),
            matches=len(scored),
            top_score=scored[0][1] if scored else 0.0,
        )

        return scored[:limit]

    def find_best_match(
        self,
        error_message: str | None = None,
        symptoms: list[str] | None = None,
        service: str | None = None,
        log_messages: list[str] | None = None,
    ) -> tuple[IssuePattern, float] | None:
        """Find the best matching issue.

        Args:
            error_message: Error message to match.
            symptoms: List of symptoms.
            service: Service name.
            log_messages: Log messages to analyze.

        Returns:
            Tuple of (IssuePattern, score) or None if no match.
        """
        matches = self.find_matching_issues(
            error_message=error_message,
            symptoms=symptoms,
            service=service,
            log_messages=log_messages,
            min_score=0.0,
            limit=1,
        )
        return matches[0] if matches else None


# Singleton instance
_known_issue_db: KnownIssueDatabase | None = None


def get_known_issue_database() -> KnownIssueDatabase:
    """Get the known issue database singleton.

    Returns:
        KnownIssueDatabase instance.
    """
    global _known_issue_db
    if _known_issue_db is None:
        _known_issue_db = KnownIssueDatabase()
    return _known_issue_db


def reset_known_issue_database() -> None:
    """Reset the known issue database singleton (for testing)."""
    global _known_issue_db
    _known_issue_db = None
