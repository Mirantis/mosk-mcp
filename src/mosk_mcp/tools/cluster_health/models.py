"""Pydantic models for Cluster Health Summary tools.

This module defines input/output models for all cluster health MCP tools,
providing comprehensive health monitoring across Platform (MCC), Kubernetes,
OpenStack, Ceph, and StackLight components.

All tools in this module are READ_ONLY safety level.

Health Score Calculation:
- Platform:          20% (Cluster CR conditions, Machine CR phases from MCC)
- Kubernetes:        20% (Node readiness, system pod health, API server latency)
- OpenStack Control: 20% (API endpoint availability, service pod health)
- OpenStack Compute: 20% (Nova-compute status, hypervisor availability)
- Ceph Storage:      20% (Cluster health, OSD status, capacity headroom)

Health States:
- HEALTHY:  90-100 score
- DEGRADED: 70-89 score
- WARNING:  50-69 score
- CRITICAL: <50 score
"""

from __future__ import annotations

from enum import Enum
from types import MappingProxyType
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from mosk_mcp.tools.common.enums import (
    AlertSeverity,
    AlertState,
    HealthState,
    HealthStatus,
)


class PreflightCheckType(str, Enum):
    """Types of preflight checks."""

    MAINTENANCE = "maintenance"
    UPGRADE = "upgrade"
    NODE_REMOVAL = "node_removal"
    OSD_REMOVAL = "osd_removal"
    GENERAL = "general"


class PreflightStatus(str, Enum):
    """Preflight check result status."""

    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"
    SKIP = "skip"


# =============================================================================
# Health Score Thresholds
# =============================================================================


# Immutable threshold dictionaries to prevent accidental modification
HEALTH_SCORE_THRESHOLDS: MappingProxyType[str, int] = MappingProxyType(
    {
        "healthy_min": 90,
        "degraded_min": 70,
        "warning_min": 50,
    }
)

COMPONENT_WEIGHTS: MappingProxyType[str, float] = MappingProxyType(
    {
        "platform": 0.20,
        "kubernetes": 0.20,
        "openstack_control": 0.20,
        "openstack_compute": 0.20,
        "ceph": 0.20,
    }
)


def score_to_health_state(score: int) -> HealthState:
    """Convert numeric score to health state.

    Args:
        score: Health score (0-100).

    Returns:
        Corresponding HealthState enum value.
    """
    if score < 0:
        return HealthState.UNKNOWN
    if score >= HEALTH_SCORE_THRESHOLDS["healthy_min"]:
        return HealthState.HEALTHY
    if score >= HEALTH_SCORE_THRESHOLDS["degraded_min"]:
        return HealthState.DEGRADED
    if score >= HEALTH_SCORE_THRESHOLDS["warning_min"]:
        return HealthState.WARNING
    return HealthState.CRITICAL


# =============================================================================
# Shared Models
# =============================================================================


class HealthCheckResult(BaseModel):
    """Result of a single health check."""

    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(..., description="Check name")
    passed: bool = Field(..., description="Whether check passed")
    message: str = Field(..., description="Status message")
    details: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional details",
    )


class ComponentHealthSummary(BaseModel):
    """Health summary for a single component."""

    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(..., description="Component name")
    health: HealthStatus = Field(..., description="Component health status")
    score: int = Field(
        ...,
        description="Health score (0-100)",
        ge=0,
        le=100,
    )
    message: str = Field(..., description="Status summary message")
    checks: list[HealthCheckResult] = Field(
        default_factory=list,
        description="Individual health checks",
    )
    issues: list[str] = Field(
        default_factory=list,
        description="Current issues",
    )


# =============================================================================
# get_mosk_cluster_health models
# =============================================================================


class GetClusterHealthInput(BaseModel):
    """Input for get_mosk_cluster_health tool."""

    model_config = ConfigDict(populate_by_name=True)

    cluster_name: str | None = Field(
        default=None,
        alias="clusterName",
        description="MOSK Cluster CR name on MCC (e.g., 'mos'). If not provided, will auto-discover.",
    )
    cluster_namespace: str = Field(
        default="default",
        alias="clusterNamespace",
        description="Namespace where Cluster CR is defined on MCC",
    )
    osdpl_name: str | None = Field(
        default=None,
        alias="osdplName",
        description="OpenStackDeployment name (e.g., 'mos', 'openstack'). If not provided, will auto-discover the first OSDPL in the namespace.",
    )
    namespace: str = Field(
        default="openstack",
        description="Kubernetes namespace where OSDPL is deployed",
    )
    include_component_details: bool = Field(
        default=True,
        alias="includeComponentDetails",
        description="Include per-component health details",
    )
    include_recommendations: bool = Field(
        default=True,
        alias="includeRecommendations",
        description="Include actionable recommendations",
    )


class ClusterHealthScore(BaseModel):
    """Weighted health score calculation."""

    model_config = ConfigDict(populate_by_name=True)

    overall_score: int = Field(
        ...,
        alias="overallScore",
        description="Overall health score (0-100)",
        ge=0,
        le=100,
    )
    platform_score: int = Field(
        ...,
        alias="platformScore",
        description="Platform health score from MCC (Cluster CR, Machines) (0-100)",
        ge=0,
        le=100,
    )
    kubernetes_score: int = Field(
        ...,
        alias="kubernetesScore",
        description="Kubernetes health score (0-100)",
        ge=0,
        le=100,
    )
    openstack_control_score: int = Field(
        ...,
        alias="openstackControlScore",
        description="OpenStack control plane score (0-100)",
        ge=0,
        le=100,
    )
    openstack_compute_score: int = Field(
        ...,
        alias="openstackComputeScore",
        description="OpenStack compute score (0-100)",
        ge=0,
        le=100,
    )
    ceph_score: int = Field(
        ...,
        alias="cephScore",
        description="Ceph storage score (0-100)",
        ge=0,
        le=100,
    )
    weights: dict[str, float] = Field(
        default_factory=lambda: dict(COMPONENT_WEIGHTS),
        description="Score weights by component",
    )


class GetClusterHealthOutput(BaseModel):
    """Output from get_mosk_cluster_health tool."""

    model_config = ConfigDict(populate_by_name=True)

    health_state: HealthState = Field(
        ...,
        alias="healthState",
        description="Overall health state",
    )
    health_score: ClusterHealthScore = Field(
        ...,
        alias="healthScore",
        description="Detailed health scores",
    )
    platform: ComponentHealthSummary = Field(
        ...,
        description="Platform health (Cluster CR conditions, Machine phases from MCC)",
    )
    kubernetes: ComponentHealthSummary = Field(
        ...,
        description="Kubernetes health summary",
    )
    openstack_control: ComponentHealthSummary = Field(
        ...,
        alias="openstackControl",
        description="OpenStack control plane health",
    )
    openstack_compute: ComponentHealthSummary = Field(
        ...,
        alias="openstackCompute",
        description="OpenStack compute health",
    )
    ceph: ComponentHealthSummary = Field(
        ...,
        description="Ceph storage health",
    )

    # Platform details from MCC
    cluster_name: str | None = Field(
        default=None,
        alias="clusterName",
        description="MOSK Cluster CR name",
    )
    current_release: str | None = Field(
        default=None,
        alias="currentRelease",
        description="Current MOSK release version",
    )
    machines_total: int = Field(
        default=0,
        alias="machinesTotal",
        description="Total machines in cluster",
    )
    machines_ready: int = Field(
        default=0,
        alias="machinesReady",
        description="Machines in Ready phase",
    )

    # OpenStack details from OSDPLStatus
    openstack_version: str | None = Field(
        default=None,
        alias="openstackVersion",
        description="Deployed OpenStack version (e.g., 'antelope', 'caracal')",
    )
    osdplst_state: str | None = Field(
        default=None,
        alias="osdplstState",
        description="OSDPLStatus state (APPLIED, APPLYING, FAILED)",
    )
    osdplst_health: str | None = Field(
        default=None,
        alias="osdplstHealth",
        description="OSDPLStatus health ratio (e.g., '22/22')",
    )

    active_alerts_count: int = Field(
        ...,
        alias="activeAlertsCount",
        description="Number of active alerts",
    )
    critical_issues: list[str] = Field(
        default_factory=list,
        alias="criticalIssues",
        description="Critical issues requiring attention",
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="Warning messages",
    )
    recommendations: list[str] = Field(
        default_factory=list,
        description="Actionable recommendations",
    )
    is_safe_for_maintenance: bool = Field(
        ...,
        alias="isSafeForMaintenance",
        description="Whether cluster is safe for maintenance operations",
    )
    is_safe_for_upgrade: bool = Field(
        ...,
        alias="isSafeForUpgrade",
        description="Whether cluster is safe for upgrades",
    )
    last_check_time: str = Field(
        ...,
        alias="lastCheckTime",
        description="When health was last checked",
    )
    timestamp: str = Field(..., description="Query timestamp")


# =============================================================================
# get_kubernetes_health models
# =============================================================================


class GetKubernetesHealthInput(BaseModel):
    """Input for get_kubernetes_health tool."""

    model_config = ConfigDict(populate_by_name=True)

    include_node_details: bool = Field(
        default=True,
        alias="includeNodeDetails",
        description="Include per-node health details",
    )
    include_system_pods: bool = Field(
        default=True,
        alias="includeSystemPods",
        description="Include system pod health",
    )


class NodeHealthInfo(BaseModel):
    """Health information for a single node."""

    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(..., description="Node name")
    ready: bool = Field(..., description="Whether node is Ready")
    schedulable: bool = Field(..., description="Whether node is schedulable")
    role: str = Field(..., description="Node role")
    conditions_ok: bool = Field(
        ...,
        alias="conditionsOk",
        description="Whether all conditions are healthy",
    )
    issues: list[str] = Field(
        default_factory=list,
        description="Node issues",
    )
    cpu_pressure: bool = Field(
        default=False,
        alias="cpuPressure",
        description="CPU pressure detected",
    )
    memory_pressure: bool = Field(
        default=False,
        alias="memoryPressure",
        description="Memory pressure detected",
    )
    disk_pressure: bool = Field(
        default=False,
        alias="diskPressure",
        description="Disk pressure detected",
    )
    pid_pressure: bool = Field(
        default=False,
        alias="pidPressure",
        description="PID pressure detected",
    )


class SystemPodHealth(BaseModel):
    """Health information for system pods in a namespace."""

    model_config = ConfigDict(populate_by_name=True)

    namespace: str = Field(..., description="Namespace")
    total_pods: int = Field(
        ...,
        alias="totalPods",
        description="Total pods",
    )
    running_pods: int = Field(
        ...,
        alias="runningPods",
        description="Running pods",
    )
    ready_pods: int = Field(
        ...,
        alias="readyPods",
        description="Ready pods",
    )
    failed_pods: int = Field(
        ...,
        alias="failedPods",
        description="Failed/CrashLooping pods",
    )
    pending_pods: int = Field(
        ...,
        alias="pendingPods",
        description="Pending pods",
    )
    unhealthy_pods: list[str] = Field(
        default_factory=list,
        alias="unhealthyPods",
        description="Names of unhealthy pods",
    )


class GetKubernetesHealthOutput(BaseModel):
    """Output from get_kubernetes_health tool."""

    model_config = ConfigDict(populate_by_name=True)

    health: HealthStatus = Field(..., description="Overall Kubernetes health")
    score: int = Field(
        ...,
        description="Health score (0-100)",
        ge=0,
        le=100,
    )
    message: str = Field(..., description="Health summary message")

    # Cluster info
    server_version: str = Field(
        ...,
        alias="serverVersion",
        description="Kubernetes server version",
    )
    api_server_healthy: bool = Field(
        ...,
        alias="apiServerHealthy",
        description="Whether API server is healthy",
    )
    etcd_healthy: bool = Field(
        ...,
        alias="etcdHealthy",
        description="Whether etcd is healthy",
    )

    # Node status
    total_nodes: int = Field(
        ...,
        alias="totalNodes",
        description="Total nodes in cluster",
    )
    ready_nodes: int = Field(
        ...,
        alias="readyNodes",
        description="Ready nodes",
    )
    not_ready_nodes: int = Field(
        ...,
        alias="notReadyNodes",
        description="Not ready nodes",
    )
    cordoned_nodes: int = Field(
        ...,
        alias="cordonedNodes",
        description="Cordoned nodes",
    )
    nodes: list[NodeHealthInfo] = Field(
        default_factory=list,
        description="Per-node health (if requested)",
    )

    # System pods
    system_pods: list[SystemPodHealth] = Field(
        default_factory=list,
        alias="systemPods",
        description="System pod health by namespace",
    )

    # Issues and recommendations
    issues: list[str] = Field(
        default_factory=list,
        description="Current issues",
    )
    recommendations: list[str] = Field(
        default_factory=list,
        description="Recommendations",
    )
    timestamp: str = Field(..., description="Query timestamp")


# =============================================================================
# get_openstack_health models
# =============================================================================


class GetOpenStackHealthInput(BaseModel):
    """Input for get_openstack_health tool."""

    model_config = ConfigDict(populate_by_name=True)

    osdpl_name: str = Field(
        ...,
        alias="osdplName",
        description="OpenStackDeployment name (e.g., 'mos', 'openstack'). Required - use list_osdpl to discover available deployments.",
    )
    namespace: str = Field(
        default="openstack",
        description="Kubernetes namespace where OSDPL is deployed",
    )
    include_endpoints: bool = Field(
        default=True,
        alias="includeEndpoints",
        description="Include API endpoint health checks",
    )
    include_services: bool = Field(
        default=True,
        alias="includeServices",
        description="Include per-service health",
    )


class ServiceHealthInfo(BaseModel):
    """Health information for an OpenStack service."""

    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(..., description="Service name")
    healthy: bool = Field(..., description="Whether service is healthy")
    replicas_desired: int = Field(
        default=0,
        ge=0,
        alias="replicasDesired",
        description="Desired replicas",
    )
    replicas_ready: int = Field(
        default=0,
        ge=0,
        alias="replicasReady",
        description="Ready replicas",
    )
    replicas_available: int = Field(
        default=0,
        ge=0,
        alias="replicasAvailable",
        description="Available replicas",
    )
    endpoint_healthy: bool = Field(
        default=True,
        alias="endpointHealthy",
        description="Whether API endpoint is responding",
    )
    endpoint_latency_ms: int | None = Field(
        default=None,
        alias="endpointLatencyMs",
        description="API endpoint latency in milliseconds",
    )
    lcm_state: str | None = Field(
        default=None,
        alias="lcmState",
        description="LCM service state (APPLIED, APPLYING, FAILED)",
    )
    lcm_release: str | None = Field(
        default=None,
        alias="lcmRelease",
        description="LCM service release version",
    )
    lcm_timestamp: str | None = Field(
        default=None,
        alias="lcmTimestamp",
        description="LCM service last update timestamp",
    )
    issues: list[str] = Field(
        default_factory=list,
        description="Service issues",
    )


class HypervisorHealthInfo(BaseModel):
    """Health information for a Nova hypervisor."""

    model_config = ConfigDict(populate_by_name=True)

    hostname: str = Field(..., description="Hypervisor hostname")
    status: str = Field(..., description="Hypervisor status")
    state: str = Field(..., description="Hypervisor state")
    healthy: bool = Field(..., description="Whether hypervisor is healthy")
    vcpus_used: int = Field(
        default=0,
        alias="vcpusUsed",
        description="Used vCPUs",
    )
    vcpus_total: int = Field(
        default=0,
        alias="vcpusTotal",
        description="Total vCPUs",
    )
    memory_used_mb: int = Field(
        default=0,
        alias="memoryUsedMb",
        description="Used memory (MB)",
    )
    memory_total_mb: int = Field(
        default=0,
        alias="memoryTotalMb",
        description="Total memory (MB)",
    )
    running_vms: int = Field(
        default=0,
        alias="runningVms",
        description="Running VMs on this hypervisor",
    )


class GetOpenStackHealthOutput(BaseModel):
    """Output from get_openstack_health tool."""

    model_config = ConfigDict(populate_by_name=True)

    # Overall health
    control_plane_health: HealthStatus = Field(
        ...,
        alias="controlPlaneHealth",
        description="Control plane health",
    )
    compute_health: HealthStatus = Field(
        ...,
        alias="computeHealth",
        description="Compute (hypervisor) health",
    )
    control_plane_score: int = Field(
        ...,
        alias="controlPlaneScore",
        description="Control plane score (0-100)",
        ge=0,
        le=100,
    )
    compute_score: int = Field(
        ...,
        alias="computeScore",
        description="Compute score (0-100)",
        ge=0,
        le=100,
    )
    message: str = Field(..., description="Health summary message")

    # OSDPL info
    osdpl_phase: str = Field(
        ...,
        alias="osdplPhase",
        description="OpenStackDeployment phase (legacy)",
    )
    openstack_version: str = Field(
        ...,
        alias="openstackVersion",
        description="Deployed OpenStack version",
    )
    is_upgrading: bool = Field(
        ...,
        alias="isUpgrading",
        description="Whether upgrade is in progress",
    )

    # OSDPLStatus (osdplst) fields - the real status
    osdplst_state: str | None = Field(
        default=None,
        alias="osdplstState",
        description="Real state from OSDPLStatus (APPLIED, APPLYING, FAILED)",
    )
    osdplst_health: str | None = Field(
        default=None,
        alias="osdplstHealth",
        description="Health ratio from OSDPLStatus (e.g., '23/23')",
    )
    osdplst_health_ready: int | None = Field(
        default=None,
        alias="osdplstHealthReady",
        description="Number of healthy components from OSDPLStatus",
    )
    osdplst_health_total: int | None = Field(
        default=None,
        alias="osdplstHealthTotal",
        description="Total components from OSDPLStatus",
    )
    mosk_release: str | None = Field(
        default=None,
        alias="moskRelease",
        description="MOSK release version (e.g., '17.4.0+25.1')",
    )

    # Service health
    services_total: int = Field(
        ...,
        alias="servicesTotal",
        description="Total services",
    )
    services_healthy: int = Field(
        ...,
        alias="servicesHealthy",
        description="Healthy services",
    )
    services: list[ServiceHealthInfo] = Field(
        default_factory=list,
        description="Per-service health",
    )

    # Hypervisor health
    hypervisors_total: int = Field(
        ...,
        alias="hypervisorsTotal",
        description="Total hypervisors",
    )
    hypervisors_healthy: int = Field(
        ...,
        alias="hypervisorsHealthy",
        description="Healthy hypervisors",
    )
    hypervisors: list[HypervisorHealthInfo] = Field(
        default_factory=list,
        description="Per-hypervisor health",
    )

    # Endpoints
    endpoints: dict[str, str] = Field(
        default_factory=dict,
        description="Service endpoints",
    )

    # Issues
    issues: list[str] = Field(
        default_factory=list,
        description="Current issues",
    )
    recommendations: list[str] = Field(
        default_factory=list,
        description="Recommendations",
    )
    timestamp: str = Field(..., description="Query timestamp")


# =============================================================================
# get_ceph_health models
# =============================================================================


class GetCephHealthInput(BaseModel):
    """Input for get_ceph_health tool."""

    model_config = ConfigDict(populate_by_name=True)

    include_osd_details: bool = Field(
        default=False,
        alias="includeOsdDetails",
        description="Include per-OSD health details",
    )
    include_pool_details: bool = Field(
        default=False,
        alias="includePoolDetails",
        description="Include per-pool capacity details",
    )


class OSDHealthInfo(BaseModel):
    """Health information for a single OSD."""

    model_config = ConfigDict(populate_by_name=True)

    osd_id: int = Field(..., alias="osdId", description="OSD ID")
    up: bool = Field(..., description="Whether OSD is up")
    in_cluster: bool = Field(
        ...,
        alias="inCluster",
        description="Whether OSD is in cluster",
    )
    healthy: bool = Field(..., description="Whether OSD is healthy (up and in)")
    host: str = Field(..., description="Host running this OSD")
    device_class: str = Field(
        ...,
        alias="deviceClass",
        description="Device class (hdd, ssd, nvme)",
    )
    utilization_percent: float = Field(
        ...,
        alias="utilizationPercent",
        description="Storage utilization percentage",
        ge=0.0,
        le=100.0,
    )


class PoolHealthInfo(BaseModel):
    """Health information for a Ceph pool."""

    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(..., description="Pool name")
    used_bytes: int = Field(
        ...,
        alias="usedBytes",
        description="Used bytes",
        ge=0,
    )
    max_avail_bytes: int = Field(
        ...,
        alias="maxAvailBytes",
        description="Maximum available bytes",
        ge=0,
    )
    percent_used: float = Field(
        ...,
        alias="percentUsed",
        description="Utilization percentage",
        ge=0.0,
        le=100.0,
    )
    objects: int = Field(..., description="Object count")


class GetCephHealthOutput(BaseModel):
    """Output from get_ceph_health tool."""

    model_config = ConfigDict(populate_by_name=True)

    health: HealthStatus = Field(..., description="Overall Ceph health")
    score: int = Field(
        ...,
        description="Health score (0-100)",
        ge=0,
        le=100,
    )
    message: str = Field(..., description="Health summary message")

    # Cluster health
    ceph_health: str = Field(
        ...,
        alias="cephHealth",
        description="Native Ceph health status (HEALTH_OK, HEALTH_WARN, HEALTH_ERR)",
    )
    health_checks: dict[str, str] = Field(
        default_factory=dict,
        alias="healthChecks",
        description="Active health checks and their messages",
    )

    # OSD status
    osds_total: int = Field(
        ...,
        alias="osdsTotal",
        description="Total OSDs",
    )
    osds_up: int = Field(
        ...,
        alias="osdsUp",
        description="OSDs that are up",
    )
    osds_in: int = Field(
        ...,
        alias="osdsIn",
        description="OSDs that are in",
    )
    osds_down: list[int] = Field(
        default_factory=list,
        alias="osdsDown",
        description="List of down OSD IDs",
    )
    osds: list[OSDHealthInfo] = Field(
        default_factory=list,
        description="Per-OSD health (if requested)",
    )

    # PG status
    pgs_total: int = Field(
        ...,
        alias="pgsTotal",
        description="Total placement groups",
    )
    pgs_active_clean: int = Field(
        ...,
        alias="pgsActiveClean",
        description="Active+clean PGs",
    )
    pgs_degraded: int = Field(
        default=0,
        alias="pgsDegraded",
        description="Degraded PGs",
    )
    pgs_recovering: int = Field(
        default=0,
        alias="pgsRecovering",
        description="Recovering PGs",
    )

    # Capacity
    capacity_total_bytes: int = Field(
        ...,
        alias="capacityTotalBytes",
        description="Total capacity in bytes",
        ge=0,
    )
    capacity_used_bytes: int = Field(
        ...,
        alias="capacityUsedBytes",
        description="Used capacity in bytes",
        ge=0,
    )
    capacity_available_bytes: int = Field(
        ...,
        alias="capacityAvailableBytes",
        description="Available capacity in bytes",
        ge=0,
    )
    capacity_percent_used: float = Field(
        ...,
        alias="capacityPercentUsed",
        description="Capacity utilization percentage",
        ge=0.0,
        le=100.0,
    )
    capacity_status: str = Field(
        ...,
        alias="capacityStatus",
        description="Capacity status (normal, warning, critical, emergency)",
    )
    pools: list[PoolHealthInfo] = Field(
        default_factory=list,
        description="Per-pool health (if requested)",
    )
    osd_details_available: bool = Field(
        default=True,
        alias="osdDetailsAvailable",
        description="Whether OSD details were successfully retrieved (if requested)",
    )
    pool_details_available: bool = Field(
        default=True,
        alias="poolDetailsAvailable",
        description="Whether pool details were successfully retrieved (if requested)",
    )

    # Recovery status
    is_recovering: bool = Field(
        ...,
        alias="isRecovering",
        description="Whether recovery is in progress",
    )
    recovery_progress_percent: float | None = Field(
        default=None,
        alias="recoveryProgressPercent",
        description="Recovery progress percentage",
        ge=0.0,
        le=100.0,
    )

    # Issues
    issues: list[str] = Field(
        default_factory=list,
        description="Current issues",
    )
    recommendations: list[str] = Field(
        default_factory=list,
        description="Recommendations",
    )
    timestamp: str = Field(..., description="Query timestamp")


# =============================================================================
# list_active_alerts models
# =============================================================================


class ListActiveAlertsInput(BaseModel):
    """Input for list_active_alerts tool."""

    model_config = ConfigDict(populate_by_name=True)

    severity_filter: AlertSeverity | None = Field(
        default=None,
        alias="severityFilter",
        description="Filter by severity level",
    )
    component_filter: str | None = Field(
        default=None,
        alias="componentFilter",
        description="Filter by component (kubernetes, openstack, ceph)",
    )
    include_silenced: bool = Field(
        default=False,
        alias="includeSilenced",
        description="Include silenced alerts",
    )
    limit: int = Field(
        default=100,
        description="Maximum alerts to return",
        ge=1,
        le=500,
    )


class AlertInfo(BaseModel):
    """Information about an active alert."""

    model_config = ConfigDict(populate_by_name=True)

    alert_name: str = Field(..., alias="alertName", description="Alert name")
    severity: AlertSeverity = Field(..., description="Alert severity")
    state: AlertState = Field(..., description="Alert state")
    summary: str = Field(..., description="Alert summary")
    description: str | None = Field(None, description="Alert description")
    component: str = Field(..., description="Affected component")
    source: str = Field(..., description="Alert source (prometheus, alertmanager)")
    labels: dict[str, str] = Field(
        default_factory=dict,
        description="Alert labels",
    )
    annotations: dict[str, str] = Field(
        default_factory=dict,
        description="Alert annotations",
    )
    starts_at: str = Field(
        ...,
        alias="startsAt",
        description="When alert started firing",
    )
    fingerprint: str = Field(..., description="Alert fingerprint for deduplication")
    is_silenced: bool = Field(
        default=False,
        alias="isSilenced",
        description="Whether alert is silenced",
    )
    silence_reason: str | None = Field(
        default=None,
        alias="silenceReason",
        description="Silence reason if silenced",
    )


class ListActiveAlertsOutput(BaseModel):
    """Output from list_active_alerts tool."""

    model_config = ConfigDict(populate_by_name=True)

    alerts: list[AlertInfo] = Field(
        default_factory=list,
        description="Active alerts",
    )
    total_count: int = Field(
        ...,
        alias="totalCount",
        description="Total alerts found",
    )
    critical_count: int = Field(
        ...,
        alias="criticalCount",
        description="Critical alerts",
    )
    warning_count: int = Field(
        ...,
        alias="warningCount",
        description="Warning alerts",
    )
    info_count: int = Field(
        ...,
        alias="infoCount",
        description="Info alerts",
    )
    silenced_count: int = Field(
        ...,
        alias="silencedCount",
        description="Silenced alerts",
    )
    by_component: dict[str, int] = Field(
        default_factory=dict,
        alias="byComponent",
        description="Alert count by component",
    )
    by_severity: dict[str, int] = Field(
        default_factory=dict,
        alias="bySeverity",
        description="Alert count by severity",
    )
    most_critical: list[str] = Field(
        default_factory=list,
        alias="mostCritical",
        description="Most critical alert summaries",
    )
    timestamp: str = Field(..., description="Query timestamp")


# =============================================================================
# get_alert_details models
# =============================================================================


class GetAlertDetailsInput(BaseModel):
    """Input for get_alert_details tool."""

    model_config = ConfigDict(populate_by_name=True)

    alert_name: str = Field(
        ...,
        alias="alertName",
        description="Alert name to get details for",
    )
    fingerprint: str | None = Field(
        default=None,
        description="Alert fingerprint for specific instance",
    )
    include_history: bool = Field(
        default=False,
        alias="includeHistory",
        description="Include alert history",
    )
    history_hours: int | None = Field(
        default=24,
        alias="historyHours",
        description="Number of hours of history to include (default 24)",
    )


class AlertHistoryEntry(BaseModel):
    """Historical entry for an alert."""

    model_config = ConfigDict(populate_by_name=True)

    timestamp: str = Field(..., description="When state changed")
    state: AlertState = Field(..., description="Alert state")
    value: float | None = Field(None, description="Metric value at time of change")


class AlertContext(BaseModel):
    """Additional context for an alert."""

    model_config = ConfigDict(populate_by_name=True)

    affected_resources: list[str] = Field(
        default_factory=list,
        alias="affectedResources",
        description="Resources affected by this alert",
    )
    related_alerts: list[str] = Field(
        default_factory=list,
        alias="relatedAlerts",
        description="Related alert names",
    )
    runbook_url: str | None = Field(
        default=None,
        alias="runbookUrl",
        description="Runbook URL for remediation",
    )
    suggested_actions: list[str] = Field(
        default_factory=list,
        alias="suggestedActions",
        description="Suggested remediation actions",
    )


class GetAlertDetailsOutput(BaseModel):
    """Output from get_alert_details tool."""

    model_config = ConfigDict(populate_by_name=True)

    alert_name: str = Field(..., alias="alertName", description="Alert name")
    severity: AlertSeverity = Field(..., description="Alert severity")
    state: AlertState = Field(..., description="Current state")
    summary: str = Field(..., description="Alert summary")
    description: str = Field(..., description="Full description")
    expression: str = Field(..., description="Prometheus expression")

    # Current state
    current_value: float | None = Field(
        default=None,
        alias="currentValue",
        description="Current metric value",
    )
    threshold: float | None = Field(
        default=None,
        description="Alert threshold",
    )
    for_duration: str | None = Field(
        default=None,
        alias="forDuration",
        description="For duration in alert rule",
    )

    # Labels and annotations
    labels: dict[str, str] = Field(
        default_factory=dict,
        description="Alert labels",
    )
    annotations: dict[str, str] = Field(
        default_factory=dict,
        description="Alert annotations",
    )

    # Timing
    starts_at: str = Field(
        ...,
        alias="startsAt",
        description="When alert started",
    )
    ends_at: str | None = Field(
        default=None,
        alias="endsAt",
        description="When alert ended (if resolved)",
    )
    duration_seconds: int = Field(
        ...,
        alias="durationSeconds",
        description="How long alert has been firing",
    )

    # Context
    context: AlertContext = Field(..., description="Alert context and recommendations")
    history: list[AlertHistoryEntry] = Field(
        default_factory=list,
        description="Alert history (if requested)",
    )

    # Silencing
    is_silenced: bool = Field(
        ...,
        alias="isSilenced",
        description="Whether alert is silenced",
    )
    silence_id: str | None = Field(
        default=None,
        alias="silenceId",
        description="Silence ID if silenced",
    )
    silence_ends_at: str | None = Field(
        default=None,
        alias="silenceEndsAt",
        description="When silence expires",
    )

    timestamp: str = Field(..., description="Query timestamp")


# =============================================================================
# run_preflight_check models
# =============================================================================


class RunPreflightCheckInput(BaseModel):
    """Input for run_preflight_check tool."""

    model_config = ConfigDict(populate_by_name=True)

    check_type: PreflightCheckType = Field(
        ...,
        alias="checkType",
        description="Type of operation to check readiness for",
    )
    target_node: str | None = Field(
        default=None,
        alias="targetNode",
        description="Target node name (for node-specific checks)",
    )
    target_osd: int | None = Field(
        default=None,
        alias="targetOsd",
        description="Target OSD ID (for OSD-specific checks)",
    )
    osdpl_name: str | None = Field(
        default=None,
        alias="osdplName",
        description="OpenStackDeployment name (e.g., 'mos', 'openstack'). If not provided, will auto-discover.",
    )
    namespace: str = Field(
        default="openstack",
        description="Kubernetes namespace where OSDPL is deployed",
    )
    strict_mode: bool = Field(
        default=False,
        alias="strictMode",
        description="Treat warnings as failures",
    )
    target_release: str | None = Field(
        default=None,
        alias="targetRelease",
        description="Target MOSK release for upgrade validation (e.g., 'mosk-21-0-0-25-2'). Required for upgrade check type.",
    )
    cluster_name: str | None = Field(
        default=None,
        alias="clusterName",
        description="Cluster name for upgrade path validation (e.g., 'mos'). Auto-discovered if not provided.",
    )
    cluster_namespace: str | None = Field(
        default=None,
        alias="clusterNamespace",
        description="Namespace where Cluster CR is defined on MCC (e.g., 'lab'). Auto-discovered if not provided.",
    )


class PreflightCheckItem(BaseModel):
    """Individual preflight check result."""

    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(..., description="Check name")
    category: str = Field(..., description="Check category")
    status: PreflightStatus = Field(..., description="Check result status")
    message: str = Field(..., description="Status message")
    details: str | None = Field(None, description="Additional details")
    required: bool = Field(
        default=True,
        description="Whether this check is required to pass",
    )
    remediation: str | None = Field(
        default=None,
        description="How to fix if failed",
    )


class RunPreflightCheckOutput(BaseModel):
    """Output from run_preflight_check tool."""

    model_config = ConfigDict(populate_by_name=True)

    check_type: PreflightCheckType = Field(
        ...,
        alias="checkType",
        description="Type of check performed",
    )
    target: str | None = Field(
        default=None,
        description="Target of the check (node, OSD, etc.)",
    )
    overall_status: PreflightStatus = Field(
        ...,
        alias="overallStatus",
        description="Overall preflight status",
    )
    ready_for_operation: bool = Field(
        ...,
        alias="readyForOperation",
        description="Whether operation can proceed",
    )
    message: str = Field(..., description="Overall message")

    # Individual checks
    checks: list[PreflightCheckItem] = Field(
        default_factory=list,
        description="Individual check results",
    )
    checks_passed: int = Field(
        ...,
        alias="checksPassed",
        description="Number of passed checks",
    )
    checks_warned: int = Field(
        ...,
        alias="checksWarned",
        description="Number of warning checks",
    )
    checks_failed: int = Field(
        ...,
        alias="checksFailed",
        description="Number of failed checks",
    )
    checks_skipped: int = Field(
        ...,
        alias="checksSkipped",
        description="Number of skipped checks",
    )

    # Blockers and warnings
    blockers: list[str] = Field(
        default_factory=list,
        description="Issues blocking the operation",
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="Warnings to consider",
    )
    recommendations: list[str] = Field(
        default_factory=list,
        description="Recommendations before proceeding",
    )

    # Estimated impact
    estimated_duration_minutes: int | None = Field(
        default=None,
        alias="estimatedDurationMinutes",
        description="Estimated operation duration",
    )
    estimated_impact: str | None = Field(
        default=None,
        alias="estimatedImpact",
        description="Estimated service impact",
    )

    # Upgrade-specific info (populated for UPGRADE check type)
    upgrade_path_valid: bool | None = Field(
        default=None,
        alias="upgradePathValid",
        description="Whether the upgrade path is supported by KaasRelease",
    )
    upgrade_info: dict[str, Any] | None = Field(
        default=None,
        alias="upgradeInfo",
        description="Upgrade details (skipMaintenance, rebootRequired, updatePlanName, etc.)",
    )
    available_upgrade_versions: list[str] | None = Field(
        default=None,
        alias="availableUpgradeVersions",
        description="List of available upgrade versions from current release",
    )

    timestamp: str = Field(..., description="Query timestamp")


# =============================================================================
# get_resource_utilization models
# =============================================================================


class GetResourceUtilizationInput(BaseModel):
    """Input for get_resource_utilization tool."""

    model_config = ConfigDict(populate_by_name=True)

    include_per_node: bool = Field(
        default=False,
        alias="includePerNode",
        description="Include per-node utilization breakdown",
    )
    include_per_namespace: bool = Field(
        default=False,
        alias="includePerNamespace",
        description="Include per-namespace utilization",
    )


class NodeResourceUtilization(BaseModel):
    """Resource utilization for a single node."""

    model_config = ConfigDict(populate_by_name=True)

    node_name: str = Field(..., alias="nodeName", description="Node name")
    role: str = Field(..., description="Node role")

    # CPU
    cpu_capacity_millicores: int = Field(
        ...,
        alias="cpuCapacityMillicores",
        description="CPU capacity in millicores",
    )
    cpu_allocatable_millicores: int = Field(
        ...,
        alias="cpuAllocatableMillicores",
        description="Allocatable CPU in millicores",
    )
    cpu_requested_millicores: int = Field(
        ...,
        alias="cpuRequestedMillicores",
        description="Requested CPU in millicores",
    )
    cpu_used_millicores: int = Field(
        ...,
        alias="cpuUsedMillicores",
        description="Used CPU in millicores",
    )
    cpu_request_percent: float = Field(
        ...,
        alias="cpuRequestPercent",
        description="CPU request percentage",
        ge=0.0,
        le=100.0,
    )
    cpu_usage_percent: float = Field(
        ...,
        alias="cpuUsagePercent",
        description="CPU usage percentage",
        ge=0.0,
        le=100.0,
    )

    # Memory
    memory_capacity_bytes: int = Field(
        ...,
        alias="memoryCapacityBytes",
        description="Memory capacity in bytes",
        ge=0,
    )
    memory_allocatable_bytes: int = Field(
        ...,
        alias="memoryAllocatableBytes",
        description="Allocatable memory in bytes",
        ge=0,
    )
    memory_requested_bytes: int = Field(
        ...,
        alias="memoryRequestedBytes",
        description="Requested memory in bytes",
        ge=0,
    )
    memory_used_bytes: int = Field(
        ...,
        alias="memoryUsedBytes",
        description="Used memory in bytes",
        ge=0,
    )
    memory_request_percent: float = Field(
        ...,
        alias="memoryRequestPercent",
        description="Memory request percentage",
        ge=0.0,
        le=100.0,
    )
    memory_usage_percent: float = Field(
        ...,
        alias="memoryUsagePercent",
        description="Memory usage percentage",
        ge=0.0,
        le=100.0,
    )

    # Pods
    pods_capacity: int = Field(
        ...,
        alias="podsCapacity",
        description="Pod capacity",
    )
    pods_running: int = Field(
        ...,
        alias="podsRunning",
        description="Running pods",
    )
    pods_percent: float = Field(
        ...,
        alias="podsPercent",
        description="Pod utilization percentage",
        ge=0.0,
        le=100.0,
    )


class NamespaceResourceUtilization(BaseModel):
    """Resource utilization for a namespace."""

    model_config = ConfigDict(populate_by_name=True)

    namespace: str = Field(..., description="Namespace name")
    pods_count: int = Field(
        ...,
        alias="podsCount",
        description="Number of pods",
    )
    cpu_requested_millicores: int = Field(
        ...,
        alias="cpuRequestedMillicores",
        description="Total CPU requested",
    )
    cpu_limit_millicores: int = Field(
        ...,
        alias="cpuLimitMillicores",
        description="Total CPU limits",
    )
    memory_requested_bytes: int = Field(
        ...,
        alias="memoryRequestedBytes",
        description="Total memory requested",
        ge=0,
    )
    memory_limit_bytes: int = Field(
        ...,
        alias="memoryLimitBytes",
        description="Total memory limits",
        ge=0,
    )


class StorageUtilization(BaseModel):
    """Storage (Ceph) utilization summary."""

    model_config = ConfigDict(populate_by_name=True)

    total_bytes: int = Field(
        ...,
        alias="totalBytes",
        description="Total capacity",
        ge=0,
    )
    used_bytes: int = Field(
        ...,
        alias="usedBytes",
        description="Used capacity",
        ge=0,
    )
    available_bytes: int = Field(
        ...,
        alias="availableBytes",
        description="Available capacity",
        ge=0,
    )
    usage_percent: float = Field(
        ...,
        alias="usagePercent",
        description="Usage percentage",
        ge=0.0,
        le=100.0,
    )
    status: str = Field(..., description="Status (normal, warning, critical)")
    total_human: str = Field(
        ...,
        alias="totalHuman",
        description="Total capacity human-readable",
    )
    used_human: str = Field(
        ...,
        alias="usedHuman",
        description="Used capacity human-readable",
    )
    available_human: str = Field(
        ...,
        alias="availableHuman",
        description="Available capacity human-readable",
    )
    error_message: str | None = Field(
        default=None,
        alias="errorMessage",
        description="Error message if storage query failed",
    )


class GetResourceUtilizationOutput(BaseModel):
    """Output from get_resource_utilization tool."""

    model_config = ConfigDict(populate_by_name=True)

    # Overall cluster CPU
    cluster_cpu_capacity_millicores: int = Field(
        ...,
        alias="clusterCpuCapacityMillicores",
        description="Total cluster CPU capacity",
    )
    cluster_cpu_requested_millicores: int = Field(
        ...,
        alias="clusterCpuRequestedMillicores",
        description="Total cluster CPU requested",
    )
    cluster_cpu_used_millicores: int = Field(
        ...,
        alias="clusterCpuUsedMillicores",
        description="Total cluster CPU used",
    )
    cluster_cpu_request_percent: float = Field(
        ...,
        alias="clusterCpuRequestPercent",
        description="Cluster CPU request percentage",
        ge=0.0,
        le=100.0,
    )
    cluster_cpu_usage_percent: float = Field(
        ...,
        alias="clusterCpuUsagePercent",
        description="Cluster CPU usage percentage",
        ge=0.0,
        le=100.0,
    )

    # Overall cluster memory
    cluster_memory_capacity_bytes: int = Field(
        ...,
        alias="clusterMemoryCapacityBytes",
        description="Total cluster memory capacity",
        ge=0,
    )
    cluster_memory_requested_bytes: int = Field(
        ...,
        alias="clusterMemoryRequestedBytes",
        description="Total cluster memory requested",
        ge=0,
    )
    cluster_memory_used_bytes: int = Field(
        ...,
        alias="clusterMemoryUsedBytes",
        description="Total cluster memory used",
        ge=0,
    )
    cluster_memory_request_percent: float = Field(
        ...,
        alias="clusterMemoryRequestPercent",
        description="Cluster memory request percentage",
        ge=0.0,
        le=100.0,
    )
    cluster_memory_usage_percent: float = Field(
        ...,
        alias="clusterMemoryUsagePercent",
        description="Cluster memory usage percentage",
        ge=0.0,
        le=100.0,
    )

    # Pods
    cluster_pods_capacity: int = Field(
        ...,
        alias="clusterPodsCapacity",
        description="Total cluster pod capacity",
    )
    cluster_pods_running: int = Field(
        ...,
        alias="clusterPodsRunning",
        description="Total running pods",
    )
    cluster_pods_percent: float = Field(
        ...,
        alias="clusterPodsPercent",
        description="Cluster pod utilization percentage",
        ge=0.0,
        le=100.0,
    )

    # Storage
    storage: StorageUtilization = Field(
        ...,
        description="Storage utilization summary",
    )

    # Per-node breakdown
    nodes: list[NodeResourceUtilization] = Field(
        default_factory=list,
        description="Per-node utilization (if requested)",
    )

    # Per-namespace breakdown
    namespaces: list[NamespaceResourceUtilization] = Field(
        default_factory=list,
        description="Per-namespace utilization (if requested)",
    )

    # Top consumers
    top_cpu_consumers: list[str] = Field(
        default_factory=list,
        alias="topCpuConsumers",
        description="Top CPU consuming namespaces",
    )
    top_memory_consumers: list[str] = Field(
        default_factory=list,
        alias="topMemoryConsumers",
        description="Top memory consuming namespaces",
    )

    # Warnings
    warnings: list[str] = Field(
        default_factory=list,
        description="Resource utilization warnings",
    )
    recommendations: list[str] = Field(
        default_factory=list,
        description="Recommendations",
    )

    timestamp: str = Field(..., description="Query timestamp")
