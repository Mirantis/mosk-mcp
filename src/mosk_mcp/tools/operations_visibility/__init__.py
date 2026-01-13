"""Operations Visibility tools for MOSK MCP Server.

This module provides tools for comprehensive visibility into MOSK cluster
operations, including:
- OpenStack deployment status and health monitoring
- OpenStack upgrade progress tracking
- MOSK platform status and upgrade progress
- Component version management
- Live migration monitoring
- Maintenance request tracking
- Rollout status monitoring
- Node condition analysis
- Operation monitoring with progress snapshots
- OSDPL patch application (privileged)

Most tools are READ_ONLY safety level - they query cluster state but do not
modify it. The apply_osdpl_patch tool is PRIVILEGED and requires CRQ validation.

Example usage:
    >>> from mosk_mcp.tools.operations_visibility import get_openstack_deployment_status
    >>> result = await get_openstack_deployment_status(k8s_adapter, GetOSDPLStatusInput())
    >>> print(f"OSDPL phase: {result.phase}")

    >>> from mosk_mcp.tools.operations_visibility import get_mosk_platform_status
    >>> result = await get_mosk_platform_status(mcc_adapter, GetMoskPlatformStatusInput(...))
    >>> print(f"Platform phase: {result.phase}")
"""

from __future__ import annotations

from mosk_mcp.tools.common.enums import HealthStatus
from mosk_mcp.tools.operations_visibility.apply_cluster_release_patch import (
    ApplyClusterReleasePatchInput,
    ApplyClusterReleasePatchOutput,
    apply_cluster_release_patch,
)
from mosk_mcp.tools.operations_visibility.apply_osdpl_patch import (
    ApplyOSDPLPatchInput,
    ApplyOSDPLPatchOutput,
    apply_osdpl_patch,
)
from mosk_mcp.tools.operations_visibility.commence_cluster_upgrade import (
    CommenceClusterUpgradeInput,
    CommenceClusterUpgradeOutput,
    UpgradeStepInfo,
    commence_cluster_upgrade,
)
from mosk_mcp.tools.operations_visibility.get_component_versions import (
    get_component_versions,
)
from mosk_mcp.tools.operations_visibility.get_migration_eta import get_migration_eta
from mosk_mcp.tools.operations_visibility.get_mosk_platform_status import (
    GetMoskPlatformStatusInput,
    GetMoskPlatformStatusOutput,
    MoskPlatformPhase,
    get_mosk_platform_status,
)
from mosk_mcp.tools.operations_visibility.get_mosk_platform_upgrade_progress import (
    GetMoskPlatformUpgradeProgressInput,
    GetMoskPlatformUpgradeProgressOutput,
    UpdatePlanStepInfo,
    get_mosk_platform_upgrade_progress,
)
from mosk_mcp.tools.operations_visibility.get_node_conditions import get_node_conditions
from mosk_mcp.tools.operations_visibility.get_openstack_deployment_status import (
    get_openstack_deployment_status,
)
from mosk_mcp.tools.operations_visibility.get_openstack_upgrade_progress import (
    get_openstack_upgrade_progress,
)
from mosk_mcp.tools.operations_visibility.get_rollout_status import get_rollout_status
from mosk_mcp.tools.operations_visibility.list_available_releases import (
    ComponentVersions,
    ListAvailableReleasesInput,
    ListAvailableReleasesOutput,
    OpenStackReleaseInfo,
    ReleaseInfo,
    UpgradePathInfo,
    list_available_releases,
)
from mosk_mcp.tools.operations_visibility.list_live_migrations import (
    list_live_migrations,
)
from mosk_mcp.tools.operations_visibility.list_maintenance_requests import (
    list_maintenance_requests,
)
from mosk_mcp.tools.operations_visibility.models import (
    ComponentUpgradeStatus,
    ComponentVersion,
    Condition,
    ConditionStatus,
    DeploymentRolloutInfo,
    GetComponentVersionsInput,
    GetComponentVersionsOutput,
    GetMigrationETAInput,
    GetMigrationETAOutput,
    GetNodeConditionsInput,
    GetNodeConditionsOutput,
    GetOSDPLStatusInput,
    GetOSDPLStatusOutput,
    GetRolloutStatusInput,
    GetRolloutStatusOutput,
    GetUpgradeProgressInput,
    GetUpgradeProgressOutput,
    # list_live_migrations models
    ListLiveMigrationsInput,
    ListLiveMigrationsOutput,
    # list_maintenance_requests models
    ListMaintenanceRequestsInput,
    ListMaintenanceRequestsOutput,
    LiveMigrationInfo,
    MaintenancePhase,
    MaintenanceRequestInfo,
    MigrationStatus,
    NodeConditionInfo,
    NodeTaint,
    OSDPLPhase,
    OSDPLStatusSummary,
    RolloutStatus,
    ServiceStatusInfo,
    StatefulSetRolloutInfo,
    UpgradeState,
    VMMigrationETA,
)
from mosk_mcp.tools.operations_visibility.monitor_operation import (
    MonitorOperationInput,
    MonitorOperationOutput,
    OperationStatus,
    OperationType,
    monitor_operation,
)
from mosk_mcp.tools.operations_visibility.monitors import ProgressSnapshot


__all__ = [
    # Models and enums (sorted alphabetically)
    "ApplyClusterReleasePatchInput",
    "ApplyClusterReleasePatchOutput",
    "ApplyOSDPLPatchInput",
    "ApplyOSDPLPatchOutput",
    "CommenceClusterUpgradeInput",
    "CommenceClusterUpgradeOutput",
    "ComponentUpgradeStatus",
    "ComponentVersion",
    "ComponentVersions",
    "Condition",
    "ConditionStatus",
    "DeploymentRolloutInfo",
    "GetComponentVersionsInput",
    "GetComponentVersionsOutput",
    "GetMigrationETAInput",
    "GetMigrationETAOutput",
    "GetMoskPlatformStatusInput",
    "GetMoskPlatformStatusOutput",
    "GetMoskPlatformUpgradeProgressInput",
    "GetMoskPlatformUpgradeProgressOutput",
    "GetNodeConditionsInput",
    "GetNodeConditionsOutput",
    "GetOSDPLStatusInput",
    "GetOSDPLStatusOutput",
    "GetRolloutStatusInput",
    "GetRolloutStatusOutput",
    "GetUpgradeProgressInput",
    "GetUpgradeProgressOutput",
    "HealthStatus",
    "ListAvailableReleasesInput",
    "ListAvailableReleasesOutput",
    "ListLiveMigrationsInput",
    "ListLiveMigrationsOutput",
    "ListMaintenanceRequestsInput",
    "ListMaintenanceRequestsOutput",
    "LiveMigrationInfo",
    "MaintenancePhase",
    "MaintenanceRequestInfo",
    "MigrationStatus",
    "MonitorOperationInput",
    "MonitorOperationOutput",
    "MoskPlatformPhase",
    "NodeConditionInfo",
    "NodeTaint",
    "OSDPLPhase",
    "OSDPLStatusSummary",
    "OpenStackReleaseInfo",
    "OperationStatus",
    "OperationType",
    "ProgressSnapshot",
    "ReleaseInfo",
    "RolloutStatus",
    "ServiceStatusInfo",
    "StatefulSetRolloutInfo",
    "UpdatePlanStepInfo",
    "UpgradePathInfo",
    "UpgradeState",
    "UpgradeStepInfo",
    "VMMigrationETA",
    # Functions (sorted alphabetically)
    "apply_cluster_release_patch",
    "apply_osdpl_patch",
    "commence_cluster_upgrade",
    "get_component_versions",
    "get_migration_eta",
    "get_mosk_platform_status",
    "get_mosk_platform_upgrade_progress",
    "get_node_conditions",
    "get_openstack_deployment_status",
    "get_openstack_upgrade_progress",
    "get_rollout_status",
    "list_available_releases",
    "list_live_migrations",
    "list_maintenance_requests",
    "monitor_operation",
]
