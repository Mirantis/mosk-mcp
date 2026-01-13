"""Pydantic models for Operations Visibility tools.

This module defines input/output models for all operations visibility MCP tools,
providing comprehensive insight into OSDPL status, upgrades, migrations, and rollouts.

All tools in this module are READ_ONLY safety level.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from mosk_mcp.tools.common.enums import HealthStatus, MigrationStatus


class OSDPLPhase(str, Enum):
    """OpenStackDeployment lifecycle phases.

    Note: MOSK uses OSDPLStatus (osdplst) which has different state values.
    These phases are from the OSDPL CR status field.
    """

    PENDING = "Pending"
    DEPLOYING = "Deploying"
    DEPLOYED = "Deployed"
    UPDATING = "Updating"
    FAILED = "Failed"
    DELETING = "Deleting"
    UNKNOWN = "Unknown"


class OSDPLState(str, Enum):
    """OpenStackDeploymentStatus (osdplst) states.

    These are the actual states from status.osdpl.state in OSDPLStatus CR.
    """

    APPLIED = "APPLIED"
    APPLYING = "APPLYING"
    WAITING = "WAITING"
    FAILED = "FAILED"
    UNKNOWN = "Unknown"


class ConditionStatus(str, Enum):
    """Kubernetes condition status values."""

    TRUE = "True"
    FALSE = "False"
    UNKNOWN = "Unknown"


class UpgradeState(str, Enum):
    """Upgrade state indicators."""

    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    ROLLING_BACK = "rolling_back"
    ROLLED_BACK = "rolled_back"


class MaintenancePhase(str, Enum):
    """NodeMaintenanceRequest lifecycle phases."""

    PENDING = "Pending"
    DRAINING = "Draining"
    DRAINED = "Drained"
    MAINTAINING = "Maintaining"
    UNCORDONING = "Uncordoning"
    COMPLETED = "Completed"
    FAILED = "Failed"
    CANCELLED = "Cancelled"


class RolloutStatus(str, Enum):
    """Deployment rollout status."""

    PROGRESSING = "Progressing"
    AVAILABLE = "Available"
    FAILED = "Failed"
    COMPLETE = "Complete"
    PAUSED = "Paused"


# =============================================================================
# Condition Model (shared)
# =============================================================================


class Condition(BaseModel):
    """Kubernetes-style condition."""

    model_config = ConfigDict(populate_by_name=True)

    type: str = Field(..., description="Condition type")
    status: ConditionStatus = Field(..., description="Condition status")
    reason: str | None = Field(None, description="Machine-readable reason")
    message: str | None = Field(None, description="Human-readable message")
    last_transition_time: str | None = Field(
        None,
        alias="lastTransitionTime",
        description="When condition last transitioned",
    )
    last_update_time: str | None = Field(
        None,
        alias="lastUpdateTime",
        description="When condition was last updated",
    )


# =============================================================================
# get_openstack_deployment_status models
# =============================================================================


class GetOSDPLStatusInput(BaseModel):
    """Input for get_openstack_deployment_status tool."""

    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(
        ...,
        description="OpenStackDeployment name (e.g., 'mos', 'openstack'). Required - use list_osdpl to discover available deployments.",
    )
    namespace: str = Field(
        default="openstack",
        description="Kubernetes namespace where OSDPL is deployed",
    )
    include_conditions: bool = Field(
        default=True,
        description="Include detailed conditions",
    )
    include_services: bool = Field(
        default=True,
        description="Include per-service status",
    )


class ServiceStatusInfo(BaseModel):
    """Status of an individual OpenStack service."""

    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(..., description="Service name (e.g., nova, neutron)")
    ready: bool = Field(..., description="Whether service is ready")
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
    message: str | None = Field(None, description="Status message")
    is_updating: bool = Field(
        default=False,
        alias="isUpdating",
        description="Whether service is updating",
    )


# =============================================================================
# OSDPLStatus (osdplst) models - Real status from Kubernetes
# =============================================================================


class ComponentHealthInfo(BaseModel):
    """Health status of an individual component from OSDPLStatus.

    Represents status.health.<service>.<component> in OSDPLStatus CR.
    Example: status.health.nova.api-osapi = {status: "Ready", generation: 4}
    """

    model_config = ConfigDict(populate_by_name=True)

    service: str = Field(..., description="Parent service (e.g., nova, neutron)")
    component: str = Field(..., description="Component name (e.g., api, scheduler)")
    status: str = Field(..., description="Component status (Ready, NotReady, Progressing)")
    generation: int = Field(default=0, description="Component generation")
    is_ready: bool = Field(
        ...,
        alias="isReady",
        description="Whether component is Ready",
    )


class LCMServiceStatus(BaseModel):
    """LCM service status from OSDPLStatus.

    Represents status.services.<category> in OSDPLStatus CR.
    Example: status.services.compute = {state: "APPLIED", timestamp: "..."}
    """

    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(..., description="Service category (e.g., compute, networking)")
    state: OSDPLState = Field(..., description="LCM state (APPLIED, APPLYING, FAILED)")
    openstack_version: str = Field(
        default="unknown",
        alias="openstackVersion",
        description="OpenStack version",
    )
    controller_version: str = Field(
        default="unknown",
        alias="controllerVersion",
        description="Controller version",
    )
    release: str = Field(default="unknown", description="MOSK release version")
    timestamp: str | None = Field(None, description="Last update timestamp")
    fingerprint: str | None = Field(None, description="Configuration fingerprint")


class OSDPLStatusInfo(BaseModel):
    """Overall OSDPL status from status.osdpl in OSDPLStatus CR.

    Contains the high-level status summary including state, health ratio,
    and LCM progress.
    """

    model_config = ConfigDict(populate_by_name=True)

    state: OSDPLState = Field(..., description="Overall state (APPLIED, APPLYING, FAILED)")
    health: str = Field(
        ...,
        description="Health ratio (e.g., '23/23' meaning 23 of 23 components healthy)",
    )
    health_ready: int = Field(
        ...,
        alias="healthReady",
        description="Number of healthy components",
    )
    health_total: int = Field(
        ...,
        alias="healthTotal",
        description="Total number of components",
    )
    lcm_progress: str = Field(
        ...,
        alias="lcmProgress",
        description="LCM progress (e.g., '18/18' meaning 18 of 18 services deployed)",
    )
    lcm_ready: int = Field(
        ...,
        alias="lcmReady",
        description="Number of services deployed",
    )
    lcm_total: int = Field(
        ...,
        alias="lcmTotal",
        description="Total number of services",
    )
    openstack_version: str = Field(
        ...,
        alias="openstackVersion",
        description="OpenStack version (e.g., 'antelope')",
    )
    controller_version: str = Field(
        ...,
        alias="controllerVersion",
        description="Controller version",
    )
    release: str = Field(..., description="MOSK release (e.g., '17.4.0+25.1')")
    timestamp: str | None = Field(None, description="Last update timestamp")


class OSDPLStatusSummary(BaseModel):
    """Summary interpretation of OSDPL status."""

    model_config = ConfigDict(populate_by_name=True)

    interpretation: str = Field(..., description="Human-readable interpretation")
    typical_duration: str | None = Field(
        None,
        alias="typicalDuration",
        description="Typical duration for current state",
    )
    action_required: bool = Field(
        default=False,
        alias="actionRequired",
        description="Whether action is required",
    )
    recommendations: list[str] = Field(
        default_factory=list,
        description="Recommended actions",
    )


class GetOSDPLStatusOutput(BaseModel):
    """Output from get_openstack_deployment_status tool.

    Combines data from both OSDPL and OSDPLStatus (osdplst) resources.
    The osdplst_* fields contain the real status from OSDPLStatus CR.
    """

    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(..., description="OSDPL name")
    namespace: str = Field(..., description="OSDPL namespace")
    phase: OSDPLPhase = Field(..., description="Current phase (legacy, from OSDPL)")
    health: HealthStatus = Field(..., description="Overall health")
    openstack_version: str = Field(
        ...,
        alias="openstackVersion",
        description="Deployed OpenStack version",
    )
    target_version: str = Field(
        ...,
        alias="targetVersion",
        description="Target OpenStack version from spec",
    )
    is_updating: bool = Field(
        ...,
        alias="isUpdating",
        description="Whether OSDPL is currently updating",
    )
    is_ready: bool = Field(
        ...,
        alias="isReady",
        description="Whether OSDPL is fully ready",
    )
    conditions: list[Condition] = Field(
        default_factory=list,
        description="OSDPL conditions",
    )
    services: list[ServiceStatusInfo] = Field(
        default_factory=list,
        description="Per-service status (legacy)",
    )
    services_ready: int = Field(
        ...,
        alias="servicesReady",
        description="Number of ready services",
    )
    services_total: int = Field(
        ...,
        alias="servicesTotal",
        description="Total number of services",
    )
    summary: OSDPLStatusSummary = Field(..., description="Status summary")
    observed_generation: int | None = Field(
        None,
        alias="observedGeneration",
        description="Last observed generation",
    )
    endpoints: dict[str, str] = Field(
        default_factory=dict,
        description="Service endpoints",
    )
    last_updated: str = Field(
        ...,
        alias="lastUpdated",
        description="When status was last updated",
    )
    timestamp: str = Field(..., description="Query timestamp")

    # OSDPLStatus (osdplst) fields - the real status
    osdplst_state: OSDPLState | None = Field(
        None,
        alias="osdplstState",
        description="Real state from OSDPLStatus (APPLIED, APPLYING, FAILED)",
    )
    osdplst_health: str | None = Field(
        None,
        alias="osdplstHealth",
        description="Health ratio from OSDPLStatus (e.g., '23/23')",
    )
    osdplst_health_ready: int | None = Field(
        None,
        alias="osdplstHealthReady",
        description="Number of healthy components from OSDPLStatus",
    )
    osdplst_health_total: int | None = Field(
        None,
        alias="osdplstHealthTotal",
        description="Total components from OSDPLStatus",
    )
    osdplst_lcm_progress: str | None = Field(
        None,
        alias="osdplstLcmProgress",
        description="LCM progress from OSDPLStatus (e.g., '18/18')",
    )
    osdplst_release: str | None = Field(
        None,
        alias="osdplstRelease",
        description="MOSK release from OSDPLStatus (e.g., '17.4.0+25.1')",
    )
    component_health: list[ComponentHealthInfo] = Field(
        default_factory=list,
        alias="componentHealth",
        description="Per-component health from OSDPLStatus status.health",
    )
    lcm_services: list[LCMServiceStatus] = Field(
        default_factory=list,
        alias="lcmServices",
        description="Per-service LCM status from OSDPLStatus status.services",
    )
    unhealthy_components: list[str] = Field(
        default_factory=list,
        alias="unhealthyComponents",
        description="List of unhealthy component names (service.component)",
    )
    failed_services: list[str] = Field(
        default_factory=list,
        alias="failedServices",
        description="List of services not in APPLIED state",
    )


# =============================================================================
# get_openstack_upgrade_progress models
# =============================================================================


class GetUpgradeProgressInput(BaseModel):
    """Input for get_openstack_upgrade_progress tool."""

    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(
        ...,
        description="OpenStackDeployment name (e.g., 'mos', 'openstack'). Required - use list_osdpl to discover available deployments.",
    )
    namespace: str = Field(
        default="openstack",
        description="Kubernetes namespace where OSDPL is deployed",
    )
    include_component_details: bool = Field(
        default=True,
        alias="includeComponentDetails",
        description="Include per-component upgrade details",
    )


class ComponentUpgradeStatus(BaseModel):
    """Upgrade status for a specific component."""

    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(..., description="Component name")
    current_version: str = Field(
        ...,
        alias="currentVersion",
        description="Current version",
    )
    target_version: str = Field(
        ...,
        alias="targetVersion",
        description="Target version",
    )
    state: UpgradeState = Field(..., description="Upgrade state")
    progress_percent: int = Field(
        ...,
        alias="progressPercent",
        description="Progress percentage (0-100)",
        ge=0,
        le=100,
    )
    replicas_updated: int = Field(
        default=0,
        ge=0,
        alias="replicasUpdated",
        description="Replicas with new version",
    )
    replicas_total: int = Field(
        default=0,
        ge=0,
        alias="replicasTotal",
        description="Total replicas",
    )
    started_at: str | None = Field(
        None,
        alias="startedAt",
        description="When upgrade started",
    )
    completed_at: str | None = Field(
        None,
        alias="completedAt",
        description="When upgrade completed",
    )
    error_message: str | None = Field(
        None,
        alias="errorMessage",
        description="Error message if failed",
    )


class GetUpgradeProgressOutput(BaseModel):
    """Output from get_openstack_upgrade_progress tool."""

    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(..., description="OSDPL name")
    namespace: str = Field(..., description="Namespace")
    is_upgrading: bool = Field(
        ...,
        alias="isUpgrading",
        description="Whether upgrade is in progress",
    )
    upgrade_state: UpgradeState = Field(
        ...,
        alias="upgradeState",
        description="Overall upgrade state",
    )
    from_version: str = Field(
        ...,
        alias="fromVersion",
        description="Source OpenStack version",
    )
    to_version: str = Field(
        ...,
        alias="toVersion",
        description="Target OpenStack version",
    )
    overall_progress_percent: int = Field(
        ...,
        alias="overallProgressPercent",
        description="Overall progress percentage",
        ge=0,
        le=100,
    )
    components: list[ComponentUpgradeStatus] = Field(
        default_factory=list,
        description="Per-component upgrade status",
    )
    components_completed: int = Field(
        ...,
        alias="componentsCompleted",
        description="Number of components completed",
    )
    components_total: int = Field(
        ...,
        alias="componentsTotal",
        description="Total components to upgrade",
    )
    control_plane_ready: bool = Field(
        ...,
        alias="controlPlaneReady",
        description="Whether control plane is ready",
    )
    compute_nodes_ready: bool = Field(
        ...,
        alias="computeNodesReady",
        description="Whether compute nodes are ready",
    )
    started_at: str | None = Field(
        None,
        alias="startedAt",
        description="When upgrade started",
    )
    estimated_completion: str | None = Field(
        None,
        alias="estimatedCompletion",
        description="Estimated completion time",
    )
    estimated_remaining_minutes: int | None = Field(
        None,
        alias="estimatedRemainingMinutes",
        description="Estimated remaining time in minutes",
    )
    current_step: str | None = Field(
        None,
        alias="currentStep",
        description="Current upgrade step",
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="Active warnings",
    )
    blockers: list[str] = Field(
        default_factory=list,
        description="Issues blocking upgrade",
    )
    timestamp: str = Field(..., description="Query timestamp")


# =============================================================================
# get_component_versions models
# =============================================================================


class GetComponentVersionsInput(BaseModel):
    """Input for get_component_versions tool."""

    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(
        ...,
        description="OpenStackDeployment name (e.g., 'mos', 'openstack'). Required - use list_osdpl to discover available deployments.",
    )
    namespace: str = Field(
        default="openstack",
        description="Kubernetes namespace where OSDPL is deployed",
    )
    include_containers: bool = Field(
        default=False,
        alias="includeContainers",
        description="Include container image versions",
    )


class ComponentVersion(BaseModel):
    """Version information for a component."""

    model_config = ConfigDict(populate_by_name=True)

    component: str = Field(..., description="Component name")
    service_type: str = Field(
        ...,
        alias="serviceType",
        description="Service type (e.g., api, conductor, compute)",
    )
    current_version: str = Field(
        ...,
        alias="currentVersion",
        description="Currently deployed version",
    )
    target_version: str = Field(
        ...,
        alias="targetVersion",
        description="Target version from spec",
    )
    is_current: bool = Field(
        ...,
        alias="isCurrent",
        description="Whether running target version",
    )
    image: str | None = Field(
        None,
        description="Container image with tag",
    )
    chart_version: str | None = Field(
        None,
        alias="chartVersion",
        description="Helm chart version",
    )


class GetComponentVersionsOutput(BaseModel):
    """Output from get_component_versions tool."""

    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(..., description="OSDPL name")
    namespace: str = Field(..., description="Namespace")
    openstack_version_current: str = Field(
        ...,
        alias="openstackVersionCurrent",
        description="Current OpenStack release",
    )
    openstack_version_target: str = Field(
        ...,
        alias="openstackVersionTarget",
        description="Target OpenStack release",
    )
    # New version fields
    osdpl_controller_version: str | None = Field(
        None,
        alias="osdplControllerVersion",
        description="OSDPL controller version (e.g., '1.0.7')",
    )
    mcc_kaas_release: str | None = Field(
        None,
        alias="mccKaasRelease",
        description="MCC KaaS release version (e.g., 'kaas-2-30-2')",
    )
    mcc_cluster_release: str | None = Field(
        None,
        alias="mccClusterRelease",
        description="MCC cluster release version (e.g., 'mke-20-0-2-3-7-25')",
    )
    mosk_release: str | None = Field(
        None,
        alias="moskRelease",
        description="MOSK cluster release version (e.g., 'mosk-17-4-0-25-1')",
    )
    lcm_agent_version: str | None = Field(
        None,
        alias="lcmAgentVersion",
        description="LCM agent version on cluster nodes (e.g., '1.42.9')",
    )
    ucp_version: str | None = Field(
        None,
        alias="ucpVersion",
        description="UCP/MKE version on cluster nodes (e.g., '3.7.19')",
    )
    versions_match: bool = Field(
        ...,
        alias="versionsMatch",
        description="Whether all components match target",
    )
    components: list[ComponentVersion] = Field(
        default_factory=list,
        description="Component versions",
    )
    components_current: int = Field(
        ...,
        alias="componentsCurrent",
        description="Components at target version",
    )
    components_total: int = Field(
        ...,
        alias="componentsTotal",
        description="Total components",
    )
    out_of_sync_components: list[str] = Field(
        default_factory=list,
        alias="outOfSyncComponents",
        description="Components not at target version",
    )
    timestamp: str = Field(..., description="Query timestamp")


# =============================================================================
# list_live_migrations models
# =============================================================================


class ListLiveMigrationsInput(BaseModel):
    """Input for list_live_migrations tool."""

    model_config = ConfigDict(populate_by_name=True)

    source_host: str | None = Field(
        None,
        alias="sourceHost",
        description="Filter by source compute host",
    )
    target_host: str | None = Field(
        None,
        alias="targetHost",
        description="Filter by target compute host",
    )
    status_filter: MigrationStatus | None = Field(
        None,
        alias="statusFilter",
        description="Filter by migration status",
    )
    include_completed: bool = Field(
        default=False,
        alias="includeCompleted",
        description="Include completed migrations",
    )
    limit: int = Field(
        default=50,
        description="Maximum migrations to return",
        ge=1,
        le=500,
    )


class LiveMigrationInfo(BaseModel):
    """Information about a live migration."""

    model_config = ConfigDict(populate_by_name=True)

    migration_id: str = Field(..., alias="migrationId", description="Migration ID")
    vm_id: str = Field(..., alias="vmId", description="VM instance UUID")
    vm_name: str | None = Field(
        None,
        alias="vmName",
        description="VM instance name",
    )
    source_host: str = Field(..., alias="sourceHost", description="Source compute host")
    target_host: str | None = Field(
        None,
        alias="targetHost",
        description="Target compute host",
    )
    status: MigrationStatus = Field(..., description="Migration status")
    migration_type: str = Field(
        ...,
        alias="migrationType",
        description="Type (live-migration, cold-migration)",
    )
    created_at: str = Field(..., alias="createdAt", description="When migration started")
    updated_at: str = Field(..., alias="updatedAt", description="Last update time")
    memory_total_bytes: int | None = Field(
        None,
        alias="memoryTotalBytes",
        description="Total memory to migrate",
    )
    memory_processed_bytes: int | None = Field(
        None,
        alias="memoryProcessedBytes",
        description="Memory processed so far",
    )
    memory_remaining_bytes: int | None = Field(
        None,
        alias="memoryRemainingBytes",
        description="Memory remaining to migrate",
    )
    disk_total_bytes: int | None = Field(
        None,
        alias="diskTotalBytes",
        description="Total disk to migrate",
    )
    disk_processed_bytes: int | None = Field(
        None,
        alias="diskProcessedBytes",
        description="Disk processed so far",
    )
    progress_percent: int = Field(
        default=0,
        alias="progressPercent",
        description="Migration progress percentage",
        ge=0,
        le=100,
    )
    error_message: str | None = Field(
        None,
        alias="errorMessage",
        description="Error message if failed",
    )


class ListLiveMigrationsOutput(BaseModel):
    """Output from list_live_migrations tool."""

    model_config = ConfigDict(populate_by_name=True)

    migrations: list[LiveMigrationInfo] = Field(
        default_factory=list,
        description="List of migrations",
    )
    total_count: int = Field(..., alias="totalCount", description="Total migrations found")
    active_count: int = Field(..., alias="activeCount", description="Active migrations")
    queued_count: int = Field(..., alias="queuedCount", description="Queued migrations")
    completed_count: int = Field(
        ...,
        alias="completedCount",
        description="Completed migrations",
    )
    failed_count: int = Field(..., alias="failedCount", description="Failed migrations")
    by_source_host: dict[str, int] = Field(
        default_factory=dict,
        alias="bySourceHost",
        description="Migration count by source host",
    )
    by_target_host: dict[str, int] = Field(
        default_factory=dict,
        alias="byTargetHost",
        description="Migration count by target host",
    )
    timestamp: str = Field(..., description="Query timestamp")


# =============================================================================
# get_migration_eta models
# =============================================================================


class GetMigrationETAInput(BaseModel):
    """Input for get_migration_eta tool."""

    model_config = ConfigDict(populate_by_name=True)

    source_host: str | None = Field(
        None,
        alias="sourceHost",
        description="Filter by source host",
    )
    include_per_vm: bool = Field(
        default=True,
        alias="includePerVm",
        description="Include per-VM ETA breakdown",
    )


class VMMigrationETA(BaseModel):
    """ETA for a single VM migration."""

    model_config = ConfigDict(populate_by_name=True)

    vm_id: str = Field(..., alias="vmId", description="VM instance UUID")
    vm_name: str | None = Field(None, alias="vmName", description="VM instance name")
    status: MigrationStatus = Field(..., description="Migration status")
    progress_percent: int = Field(
        ...,
        alias="progressPercent",
        description="Progress percentage",
    )
    estimated_remaining_seconds: int | None = Field(
        None,
        alias="estimatedRemainingSeconds",
        description="Estimated seconds remaining",
    )
    estimated_completion: str | None = Field(
        None,
        alias="estimatedCompletion",
        description="Estimated completion time (ISO format)",
    )
    transfer_rate_mbps: float | None = Field(
        None,
        alias="transferRateMbps",
        description="Current transfer rate in Mbps",
    )


class GetMigrationETAOutput(BaseModel):
    """Output from get_migration_eta tool."""

    model_config = ConfigDict(populate_by_name=True)

    has_active_migrations: bool = Field(
        ...,
        alias="hasActiveMigrations",
        description="Whether there are active migrations",
    )
    total_active: int = Field(
        ...,
        alias="totalActive",
        description="Total active migrations",
    )
    total_queued: int = Field(
        ...,
        alias="totalQueued",
        description="Total queued migrations",
    )
    overall_progress_percent: int = Field(
        ...,
        alias="overallProgressPercent",
        description="Overall progress across all migrations",
    )
    estimated_total_remaining_seconds: int | None = Field(
        None,
        alias="estimatedTotalRemainingSeconds",
        description="Total estimated seconds remaining",
    )
    estimated_total_completion: str | None = Field(
        None,
        alias="estimatedTotalCompletion",
        description="Estimated time when all migrations complete",
    )
    average_transfer_rate_mbps: float | None = Field(
        None,
        alias="averageTransferRateMbps",
        description="Average transfer rate across migrations",
    )
    per_vm_eta: list[VMMigrationETA] = Field(
        default_factory=list,
        alias="perVmEta",
        description="Per-VM ETA breakdown",
    )
    bottleneck_host: str | None = Field(
        None,
        alias="bottleneckHost",
        description="Host with most pending migrations",
    )
    recommendations: list[str] = Field(
        default_factory=list,
        description="Recommendations for improving migration speed",
    )
    timestamp: str = Field(..., description="Query timestamp")


# =============================================================================
# list_maintenance_requests models
# =============================================================================


class ListMaintenanceRequestsInput(BaseModel):
    """Input for list_maintenance_requests tool."""

    model_config = ConfigDict(populate_by_name=True)

    namespace: str = Field(
        default="default",
        description="Kubernetes namespace",
    )
    node_filter: str | None = Field(
        None,
        alias="nodeFilter",
        description="Filter by node name",
    )
    phase_filter: MaintenancePhase | None = Field(
        None,
        alias="phaseFilter",
        description="Filter by maintenance phase",
    )
    include_completed: bool = Field(
        default=False,
        alias="includeCompleted",
        description="Include completed requests",
    )
    limit: int = Field(
        default=50,
        description="Maximum requests to return",
        ge=1,
        le=200,
    )


class MaintenanceRequestInfo(BaseModel):
    """Information about a maintenance request."""

    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(..., description="Request name")
    namespace: str = Field(..., description="Request namespace")
    node_name: str = Field(..., alias="nodeName", description="Target node name")
    phase: MaintenancePhase = Field(..., description="Current phase")
    reason: str = Field(..., description="Maintenance reason")
    description: str | None = Field(None, description="Description")
    drain_strategy: str = Field(
        ...,
        alias="drainStrategy",
        description="Drain strategy",
    )
    created_at: str = Field(..., alias="createdAt", description="When request was created")
    started_at: str | None = Field(
        None,
        alias="startedAt",
        description="When maintenance started",
    )
    completed_at: str | None = Field(
        None,
        alias="completedAt",
        description="When maintenance completed",
    )
    is_complete: bool = Field(
        ...,
        alias="isComplete",
        description="Whether maintenance is complete",
    )
    is_successful: bool = Field(
        ...,
        alias="isSuccessful",
        description="Whether maintenance succeeded",
    )
    pods_evicted: int = Field(
        default=0,
        alias="podsEvicted",
        description="Number of pods evicted",
    )
    error_message: str | None = Field(
        None,
        alias="errorMessage",
        description="Error message if failed",
    )
    crq_number: str | None = Field(
        None,
        alias="crqNumber",
        description="Associated change request",
    )


class ListMaintenanceRequestsOutput(BaseModel):
    """Output from list_maintenance_requests tool."""

    model_config = ConfigDict(populate_by_name=True)

    requests: list[MaintenanceRequestInfo] = Field(
        default_factory=list,
        description="Maintenance requests",
    )
    total_count: int = Field(..., alias="totalCount", description="Total requests found")
    active_count: int = Field(..., alias="activeCount", description="Active requests")
    pending_count: int = Field(
        ...,
        alias="pendingCount",
        description="Pending requests",
    )
    completed_count: int = Field(
        ...,
        alias="completedCount",
        description="Completed requests",
    )
    failed_count: int = Field(..., alias="failedCount", description="Failed requests")
    by_phase: dict[str, int] = Field(
        default_factory=dict,
        alias="byPhase",
        description="Request count by phase",
    )
    by_node: dict[str, int] = Field(
        default_factory=dict,
        alias="byNode",
        description="Request count by node",
    )
    nodes_in_maintenance: list[str] = Field(
        default_factory=list,
        alias="nodesInMaintenance",
        description="Nodes currently in maintenance",
    )
    timestamp: str = Field(..., description="Query timestamp")


# =============================================================================
# get_rollout_status models
# =============================================================================


class GetRolloutStatusInput(BaseModel):
    """Input for get_rollout_status tool."""

    model_config = ConfigDict(populate_by_name=True)

    namespace: str = Field(
        default="openstack",
        description="Kubernetes namespace where OpenStack workloads are deployed",
    )
    service_filter: str | None = Field(
        None,
        alias="serviceFilter",
        description="Filter by service name pattern",
    )
    include_history: bool = Field(
        default=False,
        alias="includeHistory",
        description="Include rollout history",
    )


class DeploymentRolloutInfo(BaseModel):
    """Rollout information for a Deployment."""

    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(..., description="Deployment name")
    namespace: str = Field(..., description="Deployment namespace")
    service: str = Field(..., description="Associated OpenStack service")
    status: RolloutStatus = Field(..., description="Rollout status")
    replicas_desired: int = Field(
        ...,
        ge=0,
        alias="replicasDesired",
        description="Desired replicas",
    )
    replicas_current: int = Field(
        ...,
        ge=0,
        alias="replicasCurrent",
        description="Current replicas",
    )
    replicas_updated: int = Field(
        ...,
        ge=0,
        alias="replicasUpdated",
        description="Updated replicas",
    )
    replicas_available: int = Field(
        ...,
        ge=0,
        alias="replicasAvailable",
        description="Available replicas",
    )
    replicas_unavailable: int = Field(
        default=0,
        ge=0,
        alias="replicasUnavailable",
        description="Unavailable replicas",
    )
    progress_percent: int = Field(
        ...,
        alias="progressPercent",
        description="Rollout progress percentage",
    )
    strategy: str = Field(..., description="Rollout strategy")
    max_surge: str | None = Field(
        None,
        alias="maxSurge",
        description="Max surge configuration",
    )
    max_unavailable: str | None = Field(
        None,
        alias="maxUnavailable",
        description="Max unavailable configuration",
    )
    conditions: list[Condition] = Field(
        default_factory=list,
        description="Deployment conditions",
    )
    generation: int = Field(..., description="Deployment generation")
    observed_generation: int = Field(
        ...,
        alias="observedGeneration",
        description="Observed generation",
    )
    is_complete: bool = Field(
        ...,
        alias="isComplete",
        description="Whether rollout is complete",
    )


class StatefulSetRolloutInfo(BaseModel):
    """Rollout information for a StatefulSet."""

    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(..., description="StatefulSet name")
    namespace: str = Field(..., description="StatefulSet namespace")
    service: str = Field(..., description="Associated OpenStack service")
    status: RolloutStatus = Field(..., description="Rollout status")
    replicas_desired: int = Field(
        ...,
        ge=0,
        alias="replicasDesired",
        description="Desired replicas",
    )
    replicas_current: int = Field(
        ...,
        ge=0,
        alias="replicasCurrent",
        description="Current replicas",
    )
    replicas_ready: int = Field(
        ...,
        ge=0,
        alias="replicasReady",
        description="Ready replicas",
    )
    replicas_updated: int = Field(
        ...,
        ge=0,
        alias="replicasUpdated",
        description="Updated replicas",
    )
    current_revision: str = Field(
        ...,
        alias="currentRevision",
        description="Current revision",
    )
    update_revision: str = Field(
        ...,
        alias="updateRevision",
        description="Update revision",
    )
    progress_percent: int = Field(
        ...,
        alias="progressPercent",
        description="Rollout progress percentage",
    )
    update_strategy: str = Field(
        ...,
        alias="updateStrategy",
        description="Update strategy",
    )
    partition: int | None = Field(
        None,
        description="Partition for staged rollouts",
    )
    is_complete: bool = Field(
        ...,
        alias="isComplete",
        description="Whether rollout is complete",
    )


class GetRolloutStatusOutput(BaseModel):
    """Output from get_rollout_status tool."""

    model_config = ConfigDict(populate_by_name=True)

    namespace: str = Field(..., description="Namespace queried")
    deployments: list[DeploymentRolloutInfo] = Field(
        default_factory=list,
        description="Deployment rollout status",
    )
    statefulsets: list[StatefulSetRolloutInfo] = Field(
        default_factory=list,
        description="StatefulSet rollout status",
    )
    total_workloads: int = Field(
        ...,
        alias="totalWorkloads",
        description="Total workloads",
    )
    workloads_complete: int = Field(
        ...,
        alias="workloadsComplete",
        description="Workloads with complete rollout",
    )
    workloads_in_progress: int = Field(
        ...,
        alias="workloadsInProgress",
        description="Workloads with rollout in progress",
    )
    workloads_failed: int = Field(
        ...,
        alias="workloadsFailed",
        description="Workloads with failed rollout",
    )
    overall_progress_percent: int = Field(
        ...,
        alias="overallProgressPercent",
        description="Overall progress percentage",
    )
    all_rollouts_complete: bool = Field(
        ...,
        alias="allRolloutsComplete",
        description="Whether all rollouts are complete",
    )
    stuck_workloads: list[str] = Field(
        default_factory=list,
        alias="stuckWorkloads",
        description="Workloads that appear stuck",
    )
    recommendations: list[str] = Field(
        default_factory=list,
        description="Recommendations",
    )
    timestamp: str = Field(..., description="Query timestamp")


# =============================================================================
# get_node_conditions models
# =============================================================================


class GetNodeConditionsInput(BaseModel):
    """Input for get_node_conditions tool."""

    model_config = ConfigDict(populate_by_name=True)

    node_name: str | None = Field(
        None,
        alias="nodeName",
        description="Specific node to query (or all if not specified)",
    )
    include_taints: bool = Field(
        default=True,
        alias="includeTaints",
        description="Include node taints",
    )
    include_labels: bool = Field(
        default=False,
        alias="includeLabels",
        description="Include node labels",
    )
    only_unhealthy: bool = Field(
        default=False,
        alias="onlyUnhealthy",
        description="Only show nodes with issues",
    )


class NodeTaint(BaseModel):
    """Node taint information."""

    model_config = ConfigDict(populate_by_name=True)

    key: str = Field(..., description="Taint key")
    value: str | None = Field(None, description="Taint value")
    effect: str = Field(..., description="Taint effect (NoSchedule, NoExecute, etc.)")


class NodeConditionInfo(BaseModel):
    """Condition information for a node."""

    model_config = ConfigDict(populate_by_name=True)

    node_name: str = Field(..., alias="nodeName", description="Node name")
    node_role: str = Field(..., alias="nodeRole", description="Node role")
    is_ready: bool = Field(..., alias="isReady", description="Whether node is Ready")
    is_schedulable: bool = Field(
        ...,
        alias="isSchedulable",
        description="Whether node is schedulable",
    )
    conditions: list[Condition] = Field(
        default_factory=list,
        description="Node conditions",
    )
    taints: list[NodeTaint] = Field(
        default_factory=list,
        description="Node taints",
    )
    labels: dict[str, str] = Field(
        default_factory=dict,
        description="Node labels (if requested)",
    )
    health_summary: str = Field(
        ...,
        alias="healthSummary",
        description="Health summary",
    )
    issues: list[str] = Field(
        default_factory=list,
        description="Current issues with the node",
    )
    kubelet_version: str = Field(
        ...,
        alias="kubeletVersion",
        description="Kubelet version",
    )
    container_runtime: str = Field(
        ...,
        alias="containerRuntime",
        description="Container runtime",
    )
    os_image: str = Field(..., alias="osImage", description="OS image")
    kernel_version: str = Field(
        ...,
        alias="kernelVersion",
        description="Kernel version",
    )
    cpu_capacity: str = Field(
        ...,
        alias="cpuCapacity",
        description="CPU capacity",
    )
    memory_capacity: str = Field(
        ...,
        alias="memoryCapacity",
        description="Memory capacity",
    )
    pods_capacity: int = Field(
        ...,
        alias="podsCapacity",
        description="Maximum pods",
    )
    pods_running: int = Field(
        ...,
        alias="podsRunning",
        description="Currently running pods",
    )


class GetNodeConditionsOutput(BaseModel):
    """Output from get_node_conditions tool."""

    model_config = ConfigDict(populate_by_name=True)

    nodes: list[NodeConditionInfo] = Field(
        default_factory=list,
        description="Node condition information",
    )
    total_nodes: int = Field(..., alias="totalNodes", description="Total nodes")
    ready_nodes: int = Field(..., alias="readyNodes", description="Ready nodes")
    not_ready_nodes: int = Field(
        ...,
        alias="notReadyNodes",
        description="Not ready nodes",
    )
    cordoned_nodes: int = Field(
        ...,
        alias="cordonedNodes",
        description="Cordoned/unschedulable nodes",
    )
    nodes_with_issues: list[str] = Field(
        default_factory=list,
        alias="nodesWithIssues",
        description="Nodes with issues",
    )
    cluster_health: HealthStatus = Field(
        ...,
        alias="clusterHealth",
        description="Overall cluster node health",
    )
    recommendations: list[str] = Field(
        default_factory=list,
        description="Recommendations",
    )
    timestamp: str = Field(..., description="Query timestamp")
