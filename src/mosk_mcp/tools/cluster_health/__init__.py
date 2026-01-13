"""Cluster Health Summary tools for MOSK MCP Server.

This module provides tools for comprehensive health monitoring across all
MOSK cluster layers:

- **MOSK Cluster Health**: Aggregated health across Kubernetes, OpenStack, and Ceph
- **Kubernetes Health**: Node readiness, system pods, API server status
- **OpenStack Health**: Control plane services, compute hypervisors
- **Ceph Health**: OSD status, PG health, capacity utilization
- **Alerts**: StackLight/Prometheus alerts monitoring
- **Preflight Checks**: Pre-operation readiness validation
- **Resource Utilization**: CPU, memory, and storage usage

All tools in this module are READ_ONLY safety level - they query cluster
state but do not modify it.

Health Score Calculation:
- Kubernetes:         25% weight
- OpenStack Control:  25% weight
- OpenStack Compute:  25% weight
- Ceph Storage:       25% weight

Health States:
- HEALTHY:  90-100 score
- DEGRADED: 70-89 score
- WARNING:  50-69 score
- CRITICAL: <50 score

Example usage:
    >>> from mosk_mcp.tools.cluster_health import get_mosk_cluster_health
    >>> result = await get_mosk_cluster_health(k8s_adapter, GetClusterHealthInput())
    >>> print(f"Health: {result.health_state}")
    >>> print(f"Score: {result.health_score.overall_score}/100")
"""

from __future__ import annotations

from mosk_mcp.tools.cluster_health.get_alert_details import get_alert_details
from mosk_mcp.tools.cluster_health.get_ceph_health import get_ceph_health
from mosk_mcp.tools.cluster_health.get_kubernetes_health import get_kubernetes_health
from mosk_mcp.tools.cluster_health.get_mosk_cluster_health import get_mosk_cluster_health
from mosk_mcp.tools.cluster_health.get_openstack_health import get_openstack_health
from mosk_mcp.tools.cluster_health.get_resource_utilization import (
    get_resource_utilization,
)
from mosk_mcp.tools.cluster_health.list_active_alerts import list_active_alerts
from mosk_mcp.tools.cluster_health.models import (
    COMPONENT_WEIGHTS,
    HEALTH_SCORE_THRESHOLDS,
    AlertContext,
    AlertHistoryEntry,
    AlertInfo,
    ClusterHealthScore,
    ComponentHealthSummary,
    GetAlertDetailsInput,
    GetAlertDetailsOutput,
    # get_ceph_health models
    GetCephHealthInput,
    GetCephHealthOutput,
    GetClusterHealthInput,
    GetClusterHealthOutput,
    # get_kubernetes_health models
    GetKubernetesHealthInput,
    GetKubernetesHealthOutput,
    # get_openstack_health models
    GetOpenStackHealthInput,
    GetOpenStackHealthOutput,
    # get_resource_utilization models
    GetResourceUtilizationInput,
    GetResourceUtilizationOutput,
    HealthCheckResult,
    HypervisorHealthInfo,
    ListActiveAlertsInput,
    ListActiveAlertsOutput,
    NamespaceResourceUtilization,
    NodeHealthInfo,
    NodeResourceUtilization,
    OSDHealthInfo,
    PoolHealthInfo,
    # run_preflight_check models
    PreflightCheckItem,
    PreflightCheckType,
    PreflightStatus,
    RunPreflightCheckInput,
    RunPreflightCheckOutput,
    ServiceHealthInfo,
    StorageUtilization,
    SystemPodHealth,
    # Utility functions
    score_to_health_state,
)
from mosk_mcp.tools.cluster_health.run_preflight_check import run_preflight_check
from mosk_mcp.tools.common.enums import (
    AlertSeverity,
    AlertState,
    HealthState,
    HealthStatus,
)


__all__ = [
    "COMPONENT_WEIGHTS",
    "HEALTH_SCORE_THRESHOLDS",
    "AlertContext",
    "AlertHistoryEntry",
    "AlertInfo",
    # Health State Enums
    "AlertSeverity",
    "AlertState",
    "ClusterHealthScore",
    # Shared Models
    "ComponentHealthSummary",
    "GetAlertDetailsInput",
    "GetAlertDetailsOutput",
    "GetCephHealthInput",
    "GetCephHealthOutput",
    "GetClusterHealthInput",
    "GetClusterHealthOutput",
    "GetKubernetesHealthInput",
    "GetKubernetesHealthOutput",
    "GetOpenStackHealthInput",
    "GetOpenStackHealthOutput",
    "GetResourceUtilizationInput",
    "GetResourceUtilizationOutput",
    "HealthCheckResult",
    "HealthState",
    "HealthStatus",
    "HypervisorHealthInfo",
    "ListActiveAlertsInput",
    "ListActiveAlertsOutput",
    "NamespaceResourceUtilization",
    "NodeHealthInfo",
    "NodeResourceUtilization",
    "OSDHealthInfo",
    "PoolHealthInfo",
    "PreflightCheckItem",
    "PreflightCheckType",
    "PreflightStatus",
    "RunPreflightCheckInput",
    "RunPreflightCheckOutput",
    "ServiceHealthInfo",
    "StorageUtilization",
    "SystemPodHealth",
    # get_alert_details
    "get_alert_details",
    # get_ceph_health
    "get_ceph_health",
    # get_kubernetes_health
    "get_kubernetes_health",
    # get_mosk_cluster_health
    "get_mosk_cluster_health",
    # get_openstack_health
    "get_openstack_health",
    # get_resource_utilization
    "get_resource_utilization",
    # list_active_alerts
    "list_active_alerts",
    # run_preflight_check
    "run_preflight_check",
    # Utility functions and constants
    "score_to_health_state",
]
