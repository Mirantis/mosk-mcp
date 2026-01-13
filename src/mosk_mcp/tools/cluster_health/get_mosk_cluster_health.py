"""Get MOSK cluster health summary tool.

This module provides the get_mosk_cluster_health MCP tool for retrieving
a unified health summary across all MOSK layers: Platform (MCC), Kubernetes,
OpenStack, Ceph, and StackLight alerts.

Safety Level: Read-only

Health Score Calculation:
- Platform:           20% (Cluster CR conditions, Machine CR phases from MCC)
- Kubernetes:         20% (Node readiness, system pod health, API server latency)
- OpenStack Control:  20% (API endpoint availability, service pod health)
- OpenStack Compute:  20% (Nova-compute status, hypervisor availability)
- Ceph Storage:       20% (Cluster health, OSD status, capacity headroom)

Health States:
- HEALTHY:  90-100 score
- DEGRADED: 70-89 score
- WARNING:  50-69 score
- CRITICAL: <50 score
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

from mosk_mcp.core.exceptions import ToolExecutionError
from mosk_mcp.observability.logging import get_logger
from mosk_mcp.tools.cluster_health.get_ceph_health import (
    GetCephHealthInput,
    get_ceph_health,
)
from mosk_mcp.tools.cluster_health.get_kubernetes_health import (
    GetKubernetesHealthInput,
    get_kubernetes_health,
)
from mosk_mcp.tools.cluster_health.get_openstack_health import (
    GetOpenStackHealthInput,
    get_openstack_health,
)
from mosk_mcp.tools.cluster_health.models import (
    COMPONENT_WEIGHTS,
    HEALTH_SCORE_THRESHOLDS,
    ClusterHealthScore,
    ComponentHealthSummary,
    GetClusterHealthInput,
    GetClusterHealthOutput,
    HealthCheckResult,
    score_to_health_state,
)
from mosk_mcp.tools.common.enums import HealthStatus
from mosk_mcp.tools.common.parsers import parse_mosk_condition_ready


if TYPE_CHECKING:
    from mosk_mcp.adapters.kubernetes import KubernetesAdapter


logger = get_logger(__name__)


@dataclass
class PlatformHealthResult:
    """Result of platform health check from MCC."""

    score: int = 100
    health: HealthStatus = field(default=HealthStatus.UNKNOWN)
    message: str = "MCC adapter not provided"
    issues: list[str] = field(default_factory=list)
    is_upgrading: bool = False
    cluster_name: str | None = None
    current_release: str | None = None
    machines_total: int = 0
    machines_ready: int = 0


@dataclass
class K8sHealthResult:
    """Result of Kubernetes health check."""

    score: int = 0
    health: HealthStatus = field(default=HealthStatus.UNKNOWN)
    message: str = ""
    issues: list[str] = field(default_factory=list)


@dataclass
class OpenStackHealthResult:
    """Result of OpenStack health check."""

    control_score: int = 0
    compute_score: int = 0
    control_health: HealthStatus = field(default=HealthStatus.UNKNOWN)
    compute_health: HealthStatus = field(default=HealthStatus.UNKNOWN)
    message: str = ""
    issues: list[str] = field(default_factory=list)
    is_upgrading: bool = False
    # OpenStack status details
    openstack_version: str | None = None
    osdplst_state: str | None = None
    osdplst_health: str | None = None


@dataclass
class CephHealthResult:
    """Result of Ceph health check."""

    score: int = 0
    health: HealthStatus = field(default=HealthStatus.UNKNOWN)
    message: str = ""
    issues: list[str] = field(default_factory=list)
    is_recovering: bool = False
    capacity_critical: bool = False
    capacity_message: str | None = None


async def _get_platform_health(
    mcc_adapter: KubernetesAdapter | None,
    cluster_name: str | None,
    cluster_namespace: str,
) -> PlatformHealthResult:
    """Get platform health from MCC (Cluster CR and Machine CRs).

    Args:
        mcc_adapter: MCC Kubernetes adapter.
        cluster_name: MOSK cluster name (optional, will auto-discover).
        cluster_namespace: Namespace containing the cluster resources.

    Returns:
        PlatformHealthResult with health status and metrics.
    """
    result = PlatformHealthResult()

    if not mcc_adapter:
        result.score = 100  # Don't penalize if MCC not available
        result.message = "MCC adapter not provided - platform health not checked"
        return result

    try:
        # Auto-discover cluster name if not provided
        target_cluster = cluster_name
        discovered_namespace: str | None = None

        if not target_cluster:
            (
                discovered_cluster,
                discovered_namespace,
            ) = await mcc_adapter.discover_mosk_cluster_namespace()
            if discovered_cluster:
                target_cluster = discovered_cluster
                logger.info(
                    "auto_discovered_cluster",
                    cluster_name=target_cluster,
                    namespace=discovered_namespace,
                )

        if not target_cluster:
            result.issues.append("Could not discover MOSK cluster name")
            result.message = "Could not discover MOSK cluster name"
            return result

        result.cluster_name = target_cluster
        effective_namespace = discovered_namespace or cluster_namespace

        # Get Cluster CR
        cluster = await mcc_adapter.get_cluster(
            name=target_cluster,
            namespace=effective_namespace,
        )

        if not cluster:
            result.issues.append(f"Cluster CR '{target_cluster}' not found")
            result.score = 0
            result.message = f"Cluster CR '{target_cluster}' not found"
            return result

        # Extract release info
        provider_spec = cluster.get("spec", {}).get("providerSpec", {}).get("value", {})
        provider_status = cluster.get("status", {}).get("providerStatus", {})

        target_release = provider_spec.get("release", "unknown")
        result.current_release = provider_status.get("release") or target_release
        result.is_upgrading = result.current_release != target_release

        # Parse cluster conditions
        conditions_healthy, conditions_total = _parse_cluster_conditions(
            provider_status.get("conditions", []),
            result.issues,
        )

        # Get Machine CRs
        machines_list = await mcc_adapter.list_machines(namespace=effective_namespace)
        result.machines_ready, result.machines_total = _count_cluster_machines(
            machines_list,
            target_cluster,
            result.issues,
        )

        # Calculate platform score (50% conditions, 50% machines)
        conditions_score = (
            (conditions_healthy / conditions_total * 100) if conditions_total > 0 else 100
        )
        machines_score = (
            (result.machines_ready / result.machines_total * 100)
            if result.machines_total > 0
            else 100
        )
        result.score = int((conditions_score * 0.5) + (machines_score * 0.5))

        # Determine platform health status
        result.health, result.message = _determine_platform_health_status(
            result.score,
            result.is_upgrading,
            result.current_release,
            target_release,
            result.machines_ready,
            result.machines_total,
            conditions_healthy,
            conditions_total,
        )

    except Exception as e:
        logger.warning(
            "platform_health_check_failed",
            error=str(e),
            error_type=type(e).__name__,
            exc_info=True,  # Include traceback for debugging
        )
        result.score = 0
        result.health = HealthStatus.UNKNOWN
        result.message = f"Platform health check failed: {e}"
        result.issues = [str(e)]

    return result


def _parse_cluster_conditions(
    raw_conditions: list[dict[str, Any]],
    issues: list[str],
) -> tuple[int, int]:
    """Parse Cluster CR conditions and count healthy ones.

    Args:
        raw_conditions: List of condition dicts from Cluster CR status.
        issues: List to append issues to.

    Returns:
        Tuple of (healthy_count, total_count).
    """
    important_conditions = {"Helm", "Ceph", "Nodes", "Kubernetes", "LCMAgent", "StackLight"}
    conditions_healthy = 0
    conditions_total = 0

    for cond in raw_conditions:
        cond_type = cond.get("type", "")
        if cond_type not in important_conditions:
            continue

        conditions_total += 1
        is_ready = parse_mosk_condition_ready(cond)
        message = cond.get("message", "")

        if is_ready:
            conditions_healthy += 1
        else:
            issues.append(f"{cond_type}: {message[:100] if message else 'not ready'}")

    return conditions_healthy, conditions_total


def _count_cluster_machines(
    machines_list: list[dict[str, Any]],
    cluster_name: str,
    issues: list[str],
) -> tuple[int, int]:
    """Count machines belonging to a cluster and their readiness.

    Args:
        machines_list: List of Machine CR dicts.
        cluster_name: Name of the cluster to filter by.
        issues: List to append issues to.

    Returns:
        Tuple of (ready_count, total_count).
    """
    machines_total = 0
    machines_ready = 0

    for m in machines_list:
        owner_refs = m.get("metadata", {}).get("ownerReferences", [])
        labels = m.get("metadata", {}).get("labels", {})

        is_owned = any(
            ref.get("kind") == "Cluster" and ref.get("name") == cluster_name for ref in owner_refs
        )
        has_label = labels.get("cluster.sigs.k8s.io/cluster-name") == cluster_name

        if is_owned or has_label:
            machines_total += 1
            phase = m.get("status", {}).get("phase", "Unknown")
            if phase == "Ready":
                machines_ready += 1
            elif phase in ["Failed", "Error"]:
                machine_name = m.get("metadata", {}).get("name", "unknown")
                issues.append(f"Machine {machine_name} in {phase} phase")

    return machines_ready, machines_total


def _determine_platform_health_status(
    score: int,
    is_upgrading: bool,
    current_release: str | None,
    target_release: str,
    machines_ready: int,
    machines_total: int,
    conditions_healthy: int,
    conditions_total: int,
) -> tuple[HealthStatus, str]:
    """Determine platform health status and message.

    Args:
        score: Platform health score.
        is_upgrading: Whether platform is upgrading.
        current_release: Current release version.
        target_release: Target release version.
        machines_ready: Number of ready machines.
        machines_total: Total number of machines.
        conditions_healthy: Number of healthy conditions.
        conditions_total: Total number of conditions.

    Returns:
        Tuple of (HealthStatus, message).
    """
    if is_upgrading:
        return (
            HealthStatus.DEGRADED,
            f"Upgrade in progress: {current_release} -> {target_release}",
        )
    if score >= HEALTH_SCORE_THRESHOLDS["healthy_min"]:
        return (
            HealthStatus.HEALTHY,
            f"{machines_ready}/{machines_total} machines ready, {conditions_healthy}/{conditions_total} conditions healthy",
        )
    if score >= HEALTH_SCORE_THRESHOLDS["degraded_min"]:
        return (
            HealthStatus.DEGRADED,
            f"{machines_ready}/{machines_total} machines ready",
        )
    return (
        HealthStatus.UNHEALTHY,
        f"Platform unhealthy: {machines_ready}/{machines_total} machines ready",
    )


async def _get_kubernetes_health_result(
    kubernetes_adapter: KubernetesAdapter,
    include_details: bool,
) -> K8sHealthResult:
    """Get Kubernetes health and return a result object.

    Args:
        kubernetes_adapter: MOSK Kubernetes adapter.
        include_details: Whether to include detailed node info.

    Returns:
        K8sHealthResult with health data.
    """
    result = K8sHealthResult()
    try:
        k8s_health = await get_kubernetes_health(
            kubernetes_adapter,
            GetKubernetesHealthInput(
                include_node_details=include_details,
                include_system_pods=True,
            ),
        )
        result.score = k8s_health.score
        result.health = k8s_health.health
        result.message = k8s_health.message
        result.issues = k8s_health.issues.copy()
    except Exception as e:
        logger.warning(
            "kubernetes_health_check_failed",
            error=str(e),
            error_type=type(e).__name__,
            exc_info=True,
        )
        result.message = f"Health check failed: {e}"
        result.issues = [str(e)]
    return result


async def _get_openstack_health_result(
    kubernetes_adapter: KubernetesAdapter,
    osdpl_name: str | None,
    namespace: str,
    include_details: bool,
    mcc_adapter: KubernetesAdapter | None,
) -> OpenStackHealthResult:
    """Get OpenStack health and return a result object.

    Args:
        kubernetes_adapter: MOSK Kubernetes adapter.
        osdpl_name: OpenStack deployment name.
        namespace: Namespace for OpenStack.
        include_details: Whether to include service/endpoint details.
        mcc_adapter: MCC adapter for upgrade info.

    Returns:
        OpenStackHealthResult with health data.
    """
    result = OpenStackHealthResult()
    try:
        if not osdpl_name:
            raise RuntimeError("No OSDPL found or name not provided")

        os_health = await get_openstack_health(
            kubernetes_adapter,
            GetOpenStackHealthInput(
                osdpl_name=osdpl_name,
                namespace=namespace,
                include_services=include_details,
                include_endpoints=include_details,
            ),
            mcc_adapter=mcc_adapter,
        )
        result.control_score = os_health.control_plane_score
        result.compute_score = os_health.compute_score
        result.control_health = os_health.control_plane_health
        result.compute_health = os_health.compute_health
        result.message = os_health.message
        result.issues = os_health.issues.copy()
        result.is_upgrading = os_health.is_upgrading
        # Capture OpenStack status details
        result.openstack_version = os_health.openstack_version
        result.osdplst_state = os_health.osdplst_state
        result.osdplst_health = os_health.osdplst_health
    except Exception as e:
        logger.warning(
            "openstack_health_check_failed",
            error=str(e),
            error_type=type(e).__name__,
            exc_info=True,
        )
        result.message = f"Health check failed: {e}"
        result.issues = [str(e)]
    return result


async def _get_ceph_health_result(
    kubernetes_adapter: KubernetesAdapter,
    include_details: bool,
) -> CephHealthResult:
    """Get Ceph health and return a result object.

    Args:
        kubernetes_adapter: MOSK Kubernetes adapter.
        include_details: Whether to include OSD details.

    Returns:
        CephHealthResult with health data.
    """
    result = CephHealthResult()
    try:
        ceph_health = await get_ceph_health(
            kubernetes_adapter,
            GetCephHealthInput(
                include_osd_details=include_details,
                include_pool_details=False,
            ),
        )
        result.score = ceph_health.score
        result.health = ceph_health.health
        result.message = ceph_health.message
        result.issues = ceph_health.issues.copy()
        result.is_recovering = ceph_health.is_recovering
        if ceph_health.capacity_status in ["critical", "emergency"]:
            result.capacity_critical = True
            result.capacity_message = (
                f"Ceph capacity {ceph_health.capacity_status}: "
                f"{ceph_health.capacity_percent_used:.1f}%"
            )
    except Exception as e:
        logger.warning(
            "ceph_health_check_failed",
            error=str(e),
            error_type=type(e).__name__,
            exc_info=True,
        )
        result.message = f"Health check failed: {e}"
        result.issues = [str(e)]
    return result


def _calculate_overall_score(
    platform_score: int,
    k8s_score: int,
    os_control_score: int,
    os_compute_score: int,
    ceph_score: int,
) -> int:
    """Calculate weighted overall health score.

    Args:
        platform_score: Platform health score from MCC (0-100).
        k8s_score: Kubernetes health score (0-100).
        os_control_score: OpenStack control plane score (0-100).
        os_compute_score: OpenStack compute score (0-100).
        ceph_score: Ceph storage score (0-100).

    Returns:
        Weighted overall score (0-100).
    """
    weighted_score = (
        platform_score * COMPONENT_WEIGHTS["platform"]
        + k8s_score * COMPONENT_WEIGHTS["kubernetes"]
        + os_control_score * COMPONENT_WEIGHTS["openstack_control"]
        + os_compute_score * COMPONENT_WEIGHTS["openstack_compute"]
        + ceph_score * COMPONENT_WEIGHTS["ceph"]
    )
    return int(min(100, max(0, weighted_score)))


def _create_component_summary(
    name: str,
    health: HealthStatus,
    score: int,
    message: str,
    issues: list[str],
    checks: list[HealthCheckResult] | None = None,
) -> ComponentHealthSummary:
    """Create a ComponentHealthSummary from component health data.

    Args:
        name: Component name.
        health: Component health status.
        score: Health score.
        message: Status message.
        issues: List of issues.
        checks: Optional list of health check results.

    Returns:
        ComponentHealthSummary object.
    """
    return ComponentHealthSummary(
        name=name,
        health=health,
        score=score,
        message=message,
        checks=checks or [],
        issues=issues,
    )


def _is_safe_for_maintenance(
    platform: PlatformHealthResult,
    k8s: K8sHealthResult,
    openstack: OpenStackHealthResult,
    ceph: CephHealthResult,
) -> bool:
    """Determine if cluster is safe for maintenance operations."""
    if openstack.is_upgrading or ceph.is_recovering or platform.is_upgrading:
        return False

    min_score = HEALTH_SCORE_THRESHOLDS["degraded_min"]
    return (
        platform.score >= min_score
        and k8s.score >= min_score
        and openstack.control_score >= min_score
        and openstack.compute_score >= min_score
        and ceph.score >= min_score
    )


def _is_safe_for_upgrade(
    platform: PlatformHealthResult,
    k8s: K8sHealthResult,
    openstack: OpenStackHealthResult,
    ceph: CephHealthResult,
) -> bool:
    """Determine if cluster is safe for upgrades."""
    if openstack.is_upgrading or ceph.is_recovering or platform.is_upgrading:
        return False

    min_score = HEALTH_SCORE_THRESHOLDS["healthy_min"]
    return (
        platform.score >= min_score
        and k8s.score >= min_score
        and openstack.control_score >= min_score
        and openstack.compute_score >= min_score
        and ceph.score >= min_score
    )


def _generate_warnings(
    platform: PlatformHealthResult,
    openstack: OpenStackHealthResult,
    ceph: CephHealthResult,
    cluster_name: str | None,
    safe_for_maintenance: bool,
    safe_for_upgrade: bool,
) -> list[str]:
    """Generate warnings list."""
    warnings: list[str] = []
    if platform.is_upgrading:
        warnings.append(
            f"MOSK platform upgrade in progress: {platform.current_release} -> {cluster_name or 'target'}"
        )
    if openstack.is_upgrading:
        warnings.append("OpenStack upgrade in progress")
    if ceph.is_recovering:
        warnings.append("Ceph recovery in progress")
    if not safe_for_maintenance:
        warnings.append("Cluster is not safe for maintenance operations")
    if not safe_for_upgrade:
        warnings.append("Cluster is not safe for upgrades")
    return warnings


def _collect_critical_issues(
    platform: PlatformHealthResult,
    k8s: K8sHealthResult,
    openstack: OpenStackHealthResult,
    ceph: CephHealthResult,
) -> list[str]:
    """Collect critical issues from all components."""
    issues: list[str] = []

    if platform.health == HealthStatus.UNHEALTHY:
        issues.extend(f"Platform: {issue}" for issue in platform.issues[:3])

    if k8s.health == HealthStatus.UNHEALTHY:
        issues.extend(f"K8s: {issue}" for issue in k8s.issues[:3])
    elif k8s.health == HealthStatus.UNKNOWN and k8s.issues:
        issues.append(f"Kubernetes health check failed: {k8s.issues[0]}")

    if openstack.control_health == HealthStatus.UNHEALTHY:
        issues.extend(f"OpenStack control: {issue}" for issue in openstack.issues[:2])
    if openstack.compute_health == HealthStatus.UNHEALTHY:
        issues.append("OpenStack compute is unhealthy")
    if openstack.control_health == HealthStatus.UNKNOWN and openstack.issues:
        issues.append(f"OpenStack health check failed: {openstack.issues[0]}")

    if ceph.health == HealthStatus.UNHEALTHY:
        issues.extend(f"Ceph: {issue}" for issue in ceph.issues[:3])
    if ceph.capacity_critical and ceph.capacity_message:
        issues.append(ceph.capacity_message)
    if ceph.health == HealthStatus.UNKNOWN and ceph.issues:
        issues.append(f"Ceph health check failed: {ceph.issues[0]}")

    return issues


async def _discover_osdpl_name(
    kubernetes_adapter: KubernetesAdapter,
    namespace: str,
) -> str | None:
    """Auto-discover OSDPL name from cluster."""
    try:
        osdpls = await kubernetes_adapter.list_openstack_deployments(namespace=namespace)
        if osdpls:
            name = osdpls[0].get("metadata", {}).get("name")
            logger.info("auto_discovered_osdpl", osdpl_name=name, namespace=namespace)
            return cast("str | None", name)
    except Exception as e:
        logger.warning("osdpl_auto_discovery_failed", error=str(e))
    return None


def _generate_cluster_recommendations(
    overall_score: int,
    platform: PlatformHealthResult,
    k8s: K8sHealthResult,
    openstack: OpenStackHealthResult,
    ceph: CephHealthResult,
    critical_alerts: int,
) -> list[str]:
    """Generate cluster-wide recommendations."""
    recommendations: list[str] = []

    if critical_alerts > 0:
        recommendations.append(f"Address {critical_alerts} critical alert(s) immediately")

    if platform.is_upgrading:
        recommendations.append("MOSK platform upgrade in progress - monitor machine phases closely")
    if openstack.is_upgrading:
        recommendations.append(
            "OpenStack upgrade in progress - monitor closely and avoid other operations"
        )
    if ceph.is_recovering:
        recommendations.append("Ceph recovery in progress - wait for completion before maintenance")

    if overall_score < HEALTH_SCORE_THRESHOLDS["warning_min"]:
        recommendations.append(
            "Cluster health is critical - investigate and resolve issues urgently"
        )
    elif overall_score < HEALTH_SCORE_THRESHOLDS["degraded_min"]:
        recommendations.append(
            "Cluster health is degraded - schedule maintenance to resolve issues"
        )
    elif overall_score < HEALTH_SCORE_THRESHOLDS["healthy_min"]:
        recommendations.append("Minor issues detected - monitor and plan for remediation")

    all_issues = platform.issues + k8s.issues + openstack.issues + ceph.issues
    for issue in all_issues[:5]:
        if issue not in recommendations:
            recommendations.append(f"Investigate: {issue}")

    return recommendations[:10]


async def get_mosk_cluster_health(
    kubernetes_adapter: KubernetesAdapter,
    input_data: GetClusterHealthInput,
    mcc_adapter: KubernetesAdapter | None = None,
) -> GetClusterHealthOutput:
    """Get MOSK cluster health summary across all layers.

    This tool provides a comprehensive health summary combining Platform (MCC),
    Kubernetes, OpenStack, and Ceph health status with weighted scoring. It's
    the primary entry point for understanding overall MOSK cluster health.

    Safety Level: Read-only

    Args:
        kubernetes_adapter: MOSK Kubernetes adapter for cluster communication.
        input_data: Input parameters for the query.
        mcc_adapter: MCC Kubernetes adapter for Cluster CR and Machine CRs.
            Required for full platform health. If not provided, platform
            health will show as unknown.

    Returns:
        GetClusterHealthOutput with comprehensive health information.

    Raises:
        ToolExecutionError: If health check fails.

    Example:
        >>> health = await get_mosk_cluster_health(
        ...     mosk_adapter, GetClusterHealthInput(), mcc_adapter
        ... )
        >>> print(f"Overall health: {health.health_state}")
        >>> print(f"Score: {health.health_score.overall_score}/100")
    """
    logger.info(
        "getting_cluster_health",
        include_component_details=input_data.include_component_details,
    )

    try:
        timestamp = datetime.now(UTC).isoformat()
        warnings: list[str] = []

        # Get Platform health from MCC (Cluster CR and Machine CRs)
        platform = await _get_platform_health(
            mcc_adapter=mcc_adapter,
            cluster_name=input_data.cluster_name,
            cluster_namespace=input_data.cluster_namespace,
        )

        # Add platform-related warnings
        if not mcc_adapter:
            warnings.append("MCC adapter not provided - platform health not available")
        elif platform.health == HealthStatus.UNKNOWN and platform.issues:
            warnings.append(f"Platform health check failed: {platform.issues[0]}")

        # Auto-discover OSDPL name
        osdpl_name = input_data.osdpl_name or await _discover_osdpl_name(
            kubernetes_adapter, input_data.namespace
        )

        # Get component health results
        k8s = await _get_kubernetes_health_result(
            kubernetes_adapter, input_data.include_component_details
        )
        openstack = await _get_openstack_health_result(
            kubernetes_adapter,
            osdpl_name,
            input_data.namespace,
            input_data.include_component_details,
            mcc_adapter,
        )
        ceph = await _get_ceph_health_result(
            kubernetes_adapter, input_data.include_component_details
        )

        # Collect critical issues from all components
        critical_issues = _collect_critical_issues(platform, k8s, openstack, ceph)

        # Calculate overall score
        overall_score = _calculate_overall_score(
            platform_score=platform.score,
            k8s_score=k8s.score,
            os_control_score=openstack.control_score,
            os_compute_score=openstack.compute_score,
            ceph_score=ceph.score,
        )

        health_state = score_to_health_state(overall_score)

        # Create component summaries
        platform_summary = _create_component_summary(
            name="platform",
            health=platform.health,
            score=platform.score,
            message=platform.message,
            issues=platform.issues,
        )

        kubernetes_summary = _create_component_summary(
            name="kubernetes",
            health=k8s.health,
            score=k8s.score,
            message=k8s.message,
            issues=k8s.issues,
        )

        openstack_control_summary = _create_component_summary(
            name="openstack_control",
            health=openstack.control_health,
            score=openstack.control_score,
            message=openstack.message,
            issues=[i for i in openstack.issues if "hypervisor" not in i.lower()],
        )

        openstack_compute_summary = _create_component_summary(
            name="openstack_compute",
            health=openstack.compute_health,
            score=openstack.compute_score,
            message=openstack.message,
            issues=[i for i in openstack.issues if "hypervisor" in i.lower()],
        )

        ceph_summary = _create_component_summary(
            name="ceph",
            health=ceph.health,
            score=ceph.score,
            message=ceph.message,
            issues=ceph.issues,
        )

        # Create health score object
        health_score = ClusterHealthScore(
            overall_score=overall_score,
            platform_score=platform.score,
            kubernetes_score=k8s.score,
            openstack_control_score=openstack.control_score,
            openstack_compute_score=openstack.compute_score,
            ceph_score=ceph.score,
        )

        # Check safety for operations
        safe_for_maintenance = _is_safe_for_maintenance(platform, k8s, openstack, ceph)
        safe_for_upgrade = _is_safe_for_upgrade(platform, k8s, openstack, ceph)

        # Generate warnings and recommendations
        warnings.extend(
            _generate_warnings(
                platform,
                openstack,
                ceph,
                input_data.cluster_name,
                safe_for_maintenance,
                safe_for_upgrade,
            )
        )
        recommendations: list[str] = []
        if input_data.include_recommendations:
            recommendations = _generate_cluster_recommendations(
                overall_score, platform, k8s, openstack, ceph, len(critical_issues)
            )

        # Count active alerts based on critical issues found during health check
        active_alerts_count = len(critical_issues)

        output = GetClusterHealthOutput(
            health_state=health_state,
            health_score=health_score,
            platform=platform_summary,
            kubernetes=kubernetes_summary,
            openstack_control=openstack_control_summary,
            openstack_compute=openstack_compute_summary,
            ceph=ceph_summary,
            cluster_name=platform.cluster_name,
            current_release=platform.current_release,
            machines_total=platform.machines_total,
            machines_ready=platform.machines_ready,
            # OpenStack status details
            openstack_version=openstack.openstack_version,
            osdplst_state=openstack.osdplst_state,
            osdplst_health=openstack.osdplst_health,
            active_alerts_count=active_alerts_count,
            critical_issues=critical_issues,
            warnings=warnings,
            recommendations=recommendations,
            is_safe_for_maintenance=safe_for_maintenance,
            is_safe_for_upgrade=safe_for_upgrade,
            last_check_time=timestamp,
            timestamp=timestamp,
        )

        logger.info(
            "cluster_health_retrieved",
            health_state=health_state.value,
            overall_score=overall_score,
            platform_score=platform.score,
            k8s_score=k8s.score,
            os_control_score=openstack.control_score,
            os_compute_score=openstack.compute_score,
            ceph_score=ceph.score,
            safe_for_maintenance=safe_for_maintenance,
            machines_total=platform.machines_total,
            machines_ready=platform.machines_ready,
        )

        return output

    except Exception as e:
        logger.error(
            "get_mosk_cluster_health_failed",
            error=str(e),
            error_type=type(e).__name__,
            exc_info=True,
        )
        raise ToolExecutionError(
            message=f"Failed to get MOSK cluster health: {e}",
            tool_name="get_mosk_cluster_health",
            details={"error": str(e), "error_type": type(e).__name__},
        ) from e
