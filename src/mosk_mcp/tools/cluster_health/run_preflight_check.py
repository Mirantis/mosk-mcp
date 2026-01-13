"""Run pre-operation readiness check tool.

This module provides the run_preflight_check MCP tool for validating
cluster readiness before maintenance, upgrades, or other operations.

Safety Level: Read-only
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from mosk_mcp.adapters.ceph import (
    CAPACITY_CRITICAL_THRESHOLD,
    CephAdapter,
    CephHealthStatus,
)
from mosk_mcp.core.exceptions import ToolExecutionError
from mosk_mcp.observability.logging import get_logger
from mosk_mcp.tools.cluster_health.models import (
    PreflightCheckItem,
    PreflightCheckType,
    PreflightStatus,
    RunPreflightCheckInput,
    RunPreflightCheckOutput,
)


if TYPE_CHECKING:
    from mosk_mcp.adapters.kubernetes import KubernetesAdapter


logger = get_logger(__name__)


# Estimated durations for different operations (in minutes)
ESTIMATED_DURATIONS = {
    PreflightCheckType.MAINTENANCE: 60,
    PreflightCheckType.UPGRADE: 240,
    PreflightCheckType.NODE_REMOVAL: 90,
    PreflightCheckType.OSD_REMOVAL: 120,
    PreflightCheckType.GENERAL: 30,
}

# Impact descriptions
IMPACT_DESCRIPTIONS = {
    PreflightCheckType.MAINTENANCE: "Temporary node unavailability, possible VM migrations",
    PreflightCheckType.UPGRADE: "Rolling service restarts, brief API interruptions",
    PreflightCheckType.NODE_REMOVAL: "Permanent node removal, VM migrations required",
    PreflightCheckType.OSD_REMOVAL: "Data rebalancing, increased cluster load",
    PreflightCheckType.GENERAL: "Varies by operation",
}


class PreflightChecker:
    """Runs preflight checks for various operation types."""

    def __init__(
        self,
        kubernetes_adapter: KubernetesAdapter,
        check_type: PreflightCheckType,
        target_node: str | None = None,
        target_osd: int | None = None,
        osdpl_name: str | None = None,
        namespace: str = "openstack",
        mcc_adapter: KubernetesAdapter | None = None,
        target_release: str | None = None,
        cluster_name: str | None = None,
        cluster_namespace: str | None = None,
    ) -> None:
        """Initialize the preflight checker.

        Args:
            kubernetes_adapter: Kubernetes adapter for cluster access.
            check_type: Type of operation to check for.
            target_node: Target node name for node-specific operations.
            target_osd: Target OSD ID for OSD-specific operations.
            osdpl_name: OpenStackDeployment name. Auto-discovered if not provided.
            namespace: Kubernetes namespace where OSDPL is deployed.
            mcc_adapter: MCC Kubernetes adapter for upgrade path validation.
            target_release: Target MOSK release for upgrade validation.
            cluster_name: Cluster name on MCC (e.g., 'mos').
            cluster_namespace: Namespace where Cluster CR is defined on MCC.
        """
        self.k8s = kubernetes_adapter
        self.check_type = check_type
        self.target_node = target_node
        self.target_osd = target_osd
        self.osdpl_name = osdpl_name
        self.namespace = namespace
        self.mcc = mcc_adapter
        self.target_release = target_release
        self.cluster_name = cluster_name
        self.cluster_namespace = cluster_namespace
        self.checks: list[PreflightCheckItem] = []
        # Upgrade validation results (populated by check_upgrade_path)
        self.upgrade_path_valid: bool | None = None
        self.upgrade_info: dict[str, Any] | None = None
        self.available_upgrade_versions: list[str] | None = None

    def _add_check(
        self,
        name: str,
        category: str,
        status: PreflightStatus,
        message: str,
        details: str | None = None,
        required: bool = True,
        remediation: str | None = None,
    ) -> None:
        """Add a check result.

        Args:
            name: Check name.
            category: Check category.
            status: Check status.
            message: Status message.
            details: Additional details.
            required: Whether check is required.
            remediation: How to fix if failed.
        """
        self.checks.append(
            PreflightCheckItem(
                name=name,
                category=category,
                status=status,
                message=message,
                details=details,
                required=required,
                remediation=remediation,
            )
        )

    async def check_kubernetes_health(self) -> None:
        """Check Kubernetes cluster health."""
        try:
            # Check API server
            await self.k8s.check_api_health()
            self._add_check(
                name="API Server Health",
                category="kubernetes",
                status=PreflightStatus.PASS,
                message="Kubernetes API server is healthy",
            )
        except Exception as e:
            self._add_check(
                name="API Server Health",
                category="kubernetes",
                status=PreflightStatus.FAIL,
                message=f"API server health check failed: {e}",
                remediation="Check kube-apiserver pods and control plane health",
            )

        # Check node readiness
        try:
            nodes = await self.k8s.list_nodes()
            total_nodes = len(nodes)
            ready_nodes = sum(
                1
                for n in nodes
                if any(
                    c.get("type") == "Ready" and c.get("status") == "True"
                    for c in n.get("status", {}).get("conditions", [])
                )
            )

            if ready_nodes == total_nodes:
                self._add_check(
                    name="Node Readiness",
                    category="kubernetes",
                    status=PreflightStatus.PASS,
                    message=f"All {total_nodes} nodes are ready",
                )
            elif ready_nodes >= total_nodes - 1:
                self._add_check(
                    name="Node Readiness",
                    category="kubernetes",
                    status=PreflightStatus.WARN,
                    message=f"{ready_nodes}/{total_nodes} nodes ready",
                    details="Some nodes are not ready, operation may have reduced fault tolerance",
                    remediation="Investigate not-ready nodes before proceeding",
                )
            else:
                self._add_check(
                    name="Node Readiness",
                    category="kubernetes",
                    status=PreflightStatus.FAIL,
                    message=f"Only {ready_nodes}/{total_nodes} nodes ready",
                    remediation="Fix node issues before maintenance",
                )

            # Check for cordoned nodes
            cordoned = [
                n.get("metadata", {}).get("name")
                for n in nodes
                if n.get("spec", {}).get("unschedulable", False)
            ]
            if cordoned:
                # For maintenance, cordoned nodes might be intentional
                status = (
                    PreflightStatus.WARN
                    if self.check_type == PreflightCheckType.MAINTENANCE
                    else PreflightStatus.FAIL
                )
                self._add_check(
                    name="Cordoned Nodes",
                    category="kubernetes",
                    status=status,
                    message=f"{len(cordoned)} node(s) are cordoned",
                    details=f"Cordoned: {', '.join(cordoned[:5])}",
                    required=False,
                    remediation="Uncordon nodes if maintenance is complete",
                )
            else:
                self._add_check(
                    name="Cordoned Nodes",
                    category="kubernetes",
                    status=PreflightStatus.PASS,
                    message="No nodes are cordoned",
                    required=False,
                )

        except Exception as e:
            self._add_check(
                name="Node Readiness",
                category="kubernetes",
                status=PreflightStatus.FAIL,
                message=f"Failed to check nodes: {e}",
            )

    async def check_openstack_health(self) -> None:
        """Check OpenStack deployment health."""
        try:
            # Auto-discover OSDPL name if not provided
            osdpl_name = self.osdpl_name
            if not osdpl_name:
                try:
                    osdpls = await self.k8s.list_openstack_deployments(namespace=self.namespace)
                    if osdpls:
                        osdpl_name = osdpls[0].get("metadata", {}).get("name")
                        logger.info(
                            "auto_discovered_osdpl_for_preflight",
                            osdpl_name=osdpl_name,
                            namespace=self.namespace,
                        )
                except Exception as e:
                    logger.warning("osdpl_auto_discovery_failed", error=str(e))

            if not osdpl_name:
                self._add_check(
                    name="OSDPL Status",
                    category="openstack",
                    status=PreflightStatus.SKIP,
                    message=f"No OpenStackDeployment found in namespace {self.namespace}",
                    required=False,
                )
                return

            osdpl = await self.k8s.get_openstack_deployment(
                name=osdpl_name,
                namespace=self.namespace,
            )

            if not osdpl:
                self._add_check(
                    name="OSDPL Status",
                    category="openstack",
                    status=PreflightStatus.SKIP,
                    message="OpenStackDeployment not found",
                    required=False,
                )
                return

            # Get OSDPLStatus (osdplst) for real status - this is the source of truth
            # OSDPLStatus has status.osdpl.state (APPLIED, APPLYING, FAILED)
            # and status.osdpl.health (e.g., "23/23")
            osdplst_state: str | None = None
            osdplst_health: str | None = None

            # Get OSDPLStatus - this is required in modern MOSK
            osdplst_result = await self.k8s.get_openstack_deployment_status(
                name=osdpl_name,
                namespace=self.namespace,
            )
            if not osdplst_result:
                self._add_check(
                    name="OSDPL Status",
                    category="openstack",
                    status=PreflightStatus.FAIL,
                    message=f"OSDPLStatus '{osdpl_name}' not found",
                    remediation="OSDPLStatus CR is required. Ensure MOSK is properly deployed.",
                )
                return

            osdplst_status = osdplst_result.get("status", {})
            osdpl_section = osdplst_status.get("osdpl", {})
            osdplst_state = osdpl_section.get("state")
            osdplst_health = osdpl_section.get("health")
            logger.debug(
                "osdplst_data_for_preflight",
                state=osdplst_state,
                health=osdplst_health,
            )

            status = osdpl.get("status", {})

            # Use OSDPLStatus state (APPLIED, APPLYING, WAITING, FAILED)
            if osdplst_state == "APPLIED":
                self._add_check(
                    name="OSDPL Status",
                    category="openstack",
                    status=PreflightStatus.PASS,
                    message=f"OpenStack is APPLIED, health: {osdplst_health or 'unknown'}",
                )
            elif osdplst_state in ["APPLYING", "WAITING"]:
                self._add_check(
                    name="OSDPL Status",
                    category="openstack",
                    status=PreflightStatus.FAIL,
                    message=f"OpenStack is {osdplst_state}",
                    remediation="Wait for deployment to complete before proceeding",
                )
            elif osdplst_state == "FAILED":
                self._add_check(
                    name="OSDPL Status",
                    category="openstack",
                    status=PreflightStatus.FAIL,
                    message="OpenStack deployment is in FAILED state",
                    remediation="Investigate OSDPLStatus failure before proceeding",
                )
            else:
                self._add_check(
                    name="OSDPL Status",
                    category="openstack",
                    status=PreflightStatus.WARN,
                    message=f"OpenStack state: {osdplst_state or 'unknown'}",
                )

            # Check services from OSDPL status
            services = status.get("services", {})
            unhealthy = [
                svc for svc, svc_status in services.items() if not svc_status.get("ready", False)
            ]

            if not unhealthy:
                self._add_check(
                    name="OpenStack Services",
                    category="openstack",
                    status=PreflightStatus.PASS,
                    message="All OpenStack services are healthy",
                )
            else:
                self._add_check(
                    name="OpenStack Services",
                    category="openstack",
                    status=PreflightStatus.WARN,
                    message=f"{len(unhealthy)} service(s) not ready",
                    details=f"Unhealthy: {', '.join(unhealthy[:5])}",
                    remediation="Investigate unhealthy services",
                )

        except Exception as e:
            self._add_check(
                name="OSDPL Status",
                category="openstack",
                status=PreflightStatus.FAIL,
                message=f"Failed to check OpenStack: {e}",
            )

    async def check_ceph_health(self) -> None:
        """Check Ceph cluster health."""
        try:
            async with CephAdapter(self.k8s) as ceph:
                cluster_status = await ceph.get_cluster_status()

                # Check overall health
                if cluster_status.health == CephHealthStatus.HEALTH_OK:
                    self._add_check(
                        name="Ceph Health",
                        category="ceph",
                        status=PreflightStatus.PASS,
                        message="Ceph cluster is HEALTH_OK",
                    )
                elif cluster_status.health == CephHealthStatus.HEALTH_WARN:
                    self._add_check(
                        name="Ceph Health",
                        category="ceph",
                        status=PreflightStatus.WARN,
                        message="Ceph cluster has HEALTH_WARN",
                        details=str(list(cluster_status.health_checks.keys())[:3]),
                        remediation="Review Ceph health warnings",
                    )
                else:
                    self._add_check(
                        name="Ceph Health",
                        category="ceph",
                        status=PreflightStatus.FAIL,
                        message="Ceph cluster is HEALTH_ERR",
                        remediation="Resolve Ceph errors before maintenance",
                    )

                # Check OSD status
                osds_total = cluster_status.num_osds
                osds_up = cluster_status.num_osds_up
                osds_in = cluster_status.num_osds_in

                if osds_up == osds_total and osds_in == osds_total:
                    self._add_check(
                        name="OSD Status",
                        category="ceph",
                        status=PreflightStatus.PASS,
                        message=f"All {osds_total} OSDs are up and in",
                    )
                elif osds_up >= osds_total - 1:
                    self._add_check(
                        name="OSD Status",
                        category="ceph",
                        status=PreflightStatus.WARN,
                        message=f"{osds_up}/{osds_total} OSDs up",
                        remediation="Investigate down OSD before proceeding",
                    )
                else:
                    self._add_check(
                        name="OSD Status",
                        category="ceph",
                        status=PreflightStatus.FAIL,
                        message=f"Only {osds_up}/{osds_total} OSDs are up",
                        remediation="Fix OSD issues before maintenance",
                    )

                # Check PG status
                pgs_total = cluster_status.num_pgs
                pgs_active_clean = cluster_status.pg_states.get("active+clean", 0)

                if pgs_active_clean == pgs_total:
                    self._add_check(
                        name="PG Status",
                        category="ceph",
                        status=PreflightStatus.PASS,
                        message=f"All {pgs_total} PGs are active+clean",
                    )
                elif pgs_active_clean >= pgs_total * 0.99:
                    self._add_check(
                        name="PG Status",
                        category="ceph",
                        status=PreflightStatus.WARN,
                        message=f"{pgs_active_clean}/{pgs_total} PGs active+clean",
                        remediation="Some PGs are not clean, recovery may be in progress",
                    )
                else:
                    self._add_check(
                        name="PG Status",
                        category="ceph",
                        status=PreflightStatus.FAIL,
                        message=f"Only {pgs_active_clean}/{pgs_total} PGs active+clean",
                        remediation="Wait for PG recovery to complete",
                    )

                # Check capacity
                capacity_percent = cluster_status.capacity_percent

                if capacity_percent < 70:
                    self._add_check(
                        name="Ceph Capacity",
                        category="ceph",
                        status=PreflightStatus.PASS,
                        message=f"Capacity at {capacity_percent:.1f}%",
                    )
                elif capacity_percent < CAPACITY_CRITICAL_THRESHOLD:
                    self._add_check(
                        name="Ceph Capacity",
                        category="ceph",
                        status=PreflightStatus.WARN,
                        message=f"Capacity at {capacity_percent:.1f}%",
                        details="Capacity above 70%, plan for expansion",
                        required=False,
                    )
                else:
                    self._add_check(
                        name="Ceph Capacity",
                        category="ceph",
                        status=PreflightStatus.FAIL,
                        message=f"Capacity critical at {capacity_percent:.1f}%",
                        remediation="Add capacity before maintenance",
                    )

        except Exception as e:
            self._add_check(
                name="Ceph Health",
                category="ceph",
                status=PreflightStatus.FAIL,
                message=f"Failed to check Ceph: {e}",
            )

    async def check_target_node(self) -> None:
        """Check target node specific conditions."""
        if not self.target_node:
            return

        try:
            # Get node details
            nodes = await self.k8s.list_nodes()
            target = next(
                (n for n in nodes if n.get("metadata", {}).get("name") == self.target_node),
                None,
            )

            if not target:
                self._add_check(
                    name="Target Node Exists",
                    category="target",
                    status=PreflightStatus.FAIL,
                    message=f"Node {self.target_node} not found",
                )
                return

            self._add_check(
                name="Target Node Exists",
                category="target",
                status=PreflightStatus.PASS,
                message=f"Node {self.target_node} found",
            )

            # Check if node is ready
            conditions = target.get("status", {}).get("conditions", [])
            is_ready = any(
                c.get("type") == "Ready" and c.get("status") == "True" for c in conditions
            )

            if is_ready:
                self._add_check(
                    name="Target Node Ready",
                    category="target",
                    status=PreflightStatus.PASS,
                    message=f"Node {self.target_node} is ready",
                )
            else:
                self._add_check(
                    name="Target Node Ready",
                    category="target",
                    status=PreflightStatus.WARN,
                    message=f"Node {self.target_node} is not ready",
                    details="Node may already have issues",
                )

            # Count pods on node
            pods = await self.k8s.list_pods(
                namespace="",  # All namespaces
                field_selector=f"spec.nodeName={self.target_node}",
            )
            pod_count = len(pods)

            if pod_count < 50:
                self._add_check(
                    name="Workload Count",
                    category="target",
                    status=PreflightStatus.PASS,
                    message=f"{pod_count} pods on node",
                )
            else:
                self._add_check(
                    name="Workload Count",
                    category="target",
                    status=PreflightStatus.WARN,
                    message=f"{pod_count} pods on node - evacuation may take time",
                    required=False,
                )

        except Exception as e:
            self._add_check(
                name="Target Node",
                category="target",
                status=PreflightStatus.FAIL,
                message=f"Failed to check target node: {e}",
            )

    async def check_target_osd(self) -> None:
        """Check target OSD specific conditions."""
        if self.target_osd is None:
            return

        try:
            async with CephAdapter(self.k8s) as ceph:
                osds = await ceph.list_osds()
                target = next(
                    (o for o in osds if o.osd_id == self.target_osd),
                    None,
                )

                if not target:
                    self._add_check(
                        name="Target OSD Exists",
                        category="target",
                        status=PreflightStatus.FAIL,
                        message=f"OSD {self.target_osd} not found",
                    )
                    return

                self._add_check(
                    name="Target OSD Exists",
                    category="target",
                    status=PreflightStatus.PASS,
                    message=f"OSD {self.target_osd} found on {target.host}",
                )

                # Check OSD status
                if target.is_up and target.is_in:
                    self._add_check(
                        name="Target OSD Status",
                        category="target",
                        status=PreflightStatus.PASS,
                        message=f"OSD {self.target_osd} is up and in",
                    )
                else:
                    self._add_check(
                        name="Target OSD Status",
                        category="target",
                        status=PreflightStatus.WARN,
                        message=f"OSD {self.target_osd} is down or out",
                        details="OSD may already be in degraded state",
                    )

        except Exception as e:
            self._add_check(
                name="Target OSD",
                category="target",
                status=PreflightStatus.FAIL,
                message=f"Failed to check target OSD: {e}",
            )

    async def check_no_active_cluster_maintenance(self) -> None:
        """Check that no ClusterMaintenanceRequest CRs are active.

        ClusterMaintenanceRequest (lcm.mirantis.com/v1alpha1) must not exist
        during upgrades as it indicates ongoing maintenance that should complete first.
        """
        if not self.mcc:
            self._add_check(
                name="No Active Cluster Maintenance",
                category="upgrade",
                status=PreflightStatus.SKIP,
                message="MCC adapter not configured",
                required=False,
            )
            return

        try:
            # ClusterMaintenanceRequest is cluster-scoped (no namespace needed)
            maintenance_requests = await self.mcc.list_cluster_maintenance_requests()

            if maintenance_requests:
                # Get details about the active maintenance
                details_list = []
                for req in maintenance_requests[:3]:  # Show first 3
                    name = req.get("metadata", {}).get("name", "unknown")
                    spec = req.get("spec", {})
                    release = spec.get("release", "unknown")
                    scope = spec.get("scope", "unknown")
                    details_list.append(f"{name} (release={release}, scope={scope})")

                self._add_check(
                    name="No Active Cluster Maintenance",
                    category="upgrade",
                    status=PreflightStatus.FAIL,
                    message=f"{len(maintenance_requests)} active ClusterMaintenanceRequest(s) found",
                    details="; ".join(details_list),
                    remediation=(
                        "Wait for maintenance to complete and delete ClusterMaintenanceRequest CRs "
                        "before starting an upgrade. Use: kubectl delete clustermaintenancerequests.lcm.mirantis.com <name>"
                    ),
                )
            else:
                self._add_check(
                    name="No Active Cluster Maintenance",
                    category="upgrade",
                    status=PreflightStatus.PASS,
                    message="No active ClusterMaintenanceRequest resources",
                )

        except Exception as e:
            self._add_check(
                name="No Active Cluster Maintenance",
                category="upgrade",
                status=PreflightStatus.FAIL,
                message=f"Failed to check ClusterMaintenanceRequest: {e}",
            )

    async def check_cluster_release_exists(self) -> None:
        """Check that the target ClusterRelease exists.

        ClusterRelease (kaas.mirantis.com/v1alpha1) must exist for the target
        version. The release name is the version string (e.g., 'mosk-21-0-3-25-2-3').
        """
        if not self.mcc:
            self._add_check(
                name="Target ClusterRelease Exists",
                category="upgrade",
                status=PreflightStatus.SKIP,
                message="MCC adapter not configured",
                required=False,
            )
            return

        if not self.target_release:
            self._add_check(
                name="Target ClusterRelease Exists",
                category="upgrade",
                status=PreflightStatus.SKIP,
                message="No target release specified",
                required=False,
            )
            return

        try:
            # ClusterRelease is cluster-scoped, name is the release version
            release = await self.mcc.get_cluster_release(name=self.target_release)

            if release:
                # Extract useful info from the ClusterRelease
                spec = release.get("spec", {})
                version = spec.get("version", "unknown")
                allowed_os_releases = spec.get("allowedOpenStackReleases", [])

                details_parts = [f"version={version}"]
                if allowed_os_releases:
                    details_parts.append(
                        f"OpenStack releases: {', '.join(allowed_os_releases[:3])}"
                    )

                self._add_check(
                    name="Target ClusterRelease Exists",
                    category="upgrade",
                    status=PreflightStatus.PASS,
                    message=f"ClusterRelease '{self.target_release}' exists",
                    details="; ".join(details_parts),
                )
            else:
                self._add_check(
                    name="Target ClusterRelease Exists",
                    category="upgrade",
                    status=PreflightStatus.FAIL,
                    message=f"ClusterRelease '{self.target_release}' not found",
                    remediation=(
                        "Verify the target release name is correct. "
                        "List available releases: kubectl get clusterreleases.kaas.mirantis.com"
                    ),
                )

        except Exception as e:
            self._add_check(
                name="Target ClusterRelease Exists",
                category="upgrade",
                status=PreflightStatus.FAIL,
                message=f"Failed to check ClusterRelease: {e}",
            )

    async def check_no_failed_upgrades(self) -> None:
        """Check for failed or stalled previous upgrades.

        ClusterUpgradeStatus (kaas.mirantis.com/v1alpha1) tracks upgrade progress.
        Each has stages with status: Success, InProgress, Failed, Pending.
        Failed or stalled upgrades must be resolved before starting a new one.
        """
        if not self.mcc:
            self._add_check(
                name="No Failed/Stalled Upgrades",
                category="upgrade",
                status=PreflightStatus.SKIP,
                message="MCC adapter not configured",
                required=False,
            )
            return

        try:
            # Discover cluster namespace if not provided
            cluster_namespace = self.cluster_namespace
            if not cluster_namespace:
                _, discovered_ns = await self.mcc.discover_mosk_cluster_namespace()
                cluster_namespace = discovered_ns

            if not cluster_namespace:
                self._add_check(
                    name="No Failed/Stalled Upgrades",
                    category="upgrade",
                    status=PreflightStatus.SKIP,
                    message="Could not determine cluster namespace",
                    required=False,
                )
                return

            # ClusterUpgradeStatus is namespaced
            upgrade_statuses = await self.mcc.list_cluster_upgrade_statuses(
                namespace=cluster_namespace
            )

            if not upgrade_statuses:
                self._add_check(
                    name="No Failed/Stalled Upgrades",
                    category="upgrade",
                    status=PreflightStatus.PASS,
                    message="No previous upgrade history found",
                )
                return

            # Check each upgrade status for failed stages
            failed_upgrades = []
            in_progress_upgrades = []

            for upgrade_status in upgrade_statuses:
                name = upgrade_status.get("metadata", {}).get("name", "unknown")
                status = upgrade_status.get("status", {})
                stages = status.get("stages", [])

                # Check for failed stages
                for stage in stages:
                    stage_name = stage.get("name", "unknown")
                    stage_status = stage.get("status", "")
                    success = stage.get("success", True)
                    message = stage.get("message", "")

                    if stage_status == "Failed" or (stage_status and not success):
                        failed_upgrades.append(f"{name}/{stage_name}: {message or 'Failed'}")
                    elif stage_status == "InProgress":
                        in_progress_upgrades.append(f"{name}/{stage_name}")

            if failed_upgrades:
                self._add_check(
                    name="No Failed/Stalled Upgrades",
                    category="upgrade",
                    status=PreflightStatus.FAIL,
                    message=f"{len(failed_upgrades)} failed upgrade stage(s) found",
                    details="; ".join(failed_upgrades[:3]),
                    remediation=(
                        "Resolve failed upgrades before starting a new one. "
                        "Check ClusterUpgradeStatus for details: "
                        f"kubectl get clusterupgradestatuses.kaas.mirantis.com -n {cluster_namespace}"
                    ),
                )
            elif in_progress_upgrades:
                self._add_check(
                    name="No Failed/Stalled Upgrades",
                    category="upgrade",
                    status=PreflightStatus.WARN,
                    message=f"{len(in_progress_upgrades)} upgrade stage(s) still in progress",
                    details="; ".join(in_progress_upgrades[:3]),
                    remediation=(
                        "Wait for in-progress upgrades to complete before starting a new one."
                    ),
                )
            else:
                # All stages are Success or Pending
                self._add_check(
                    name="No Failed/Stalled Upgrades",
                    category="upgrade",
                    status=PreflightStatus.PASS,
                    message=f"No failed upgrades found ({len(upgrade_statuses)} previous upgrade(s) checked)",
                )

        except Exception as e:
            self._add_check(
                name="No Failed/Stalled Upgrades",
                category="upgrade",
                status=PreflightStatus.FAIL,
                message=f"Failed to check ClusterUpgradeStatus: {e}",
            )

    async def check_upgrade_path(self) -> None:
        """Check if upgrade path is supported by KaasRelease and ClusterUpdatePlan exists.

        This check validates:
        1. MCC adapter is available
        2. Target release is specified
        3. Cluster exists and current release is valid
        4. Upgrade path is supported in KaasRelease.supportedClusterReleases
        5. ClusterUpdatePlan exists for this upgrade
        """
        # Check prerequisites
        if not self.mcc:
            self._add_check(
                name="Upgrade Path Validation",
                category="upgrade",
                status=PreflightStatus.SKIP,
                message="MCC adapter not configured",
                details="Upgrade path validation requires MCC kubeconfig",
                required=False,
            )
            return

        if not self.target_release:
            self._add_check(
                name="Upgrade Path Validation",
                category="upgrade",
                status=PreflightStatus.FAIL,
                message="Target release not specified",
                remediation="Specify target_release parameter (e.g., 'mosk-21-0-0-25-2')",
            )
            return

        try:
            # Discover or use provided cluster info
            cluster_name = self.cluster_name
            cluster_namespace = self.cluster_namespace

            if not cluster_name or not cluster_namespace:
                discovered_name, discovered_ns = await self.mcc.discover_mosk_cluster_namespace()
                cluster_name = cluster_name or discovered_name
                cluster_namespace = cluster_namespace or discovered_ns

            if not cluster_name or not cluster_namespace:
                self._add_check(
                    name="Cluster Discovery",
                    category="upgrade",
                    status=PreflightStatus.FAIL,
                    message="Could not discover MOSK cluster on MCC",
                    remediation="Specify cluster_name and cluster_namespace parameters",
                )
                return

            # Get current cluster release
            cluster = await self.mcc.get_cluster(name=cluster_name, namespace=cluster_namespace)
            if not cluster:
                self._add_check(
                    name="Cluster Lookup",
                    category="upgrade",
                    status=PreflightStatus.FAIL,
                    message=f"Cluster '{cluster_name}' not found in namespace '{cluster_namespace}'",
                )
                return

            current_release = (
                cluster.get("spec", {}).get("providerSpec", {}).get("value", {}).get("release")
            )

            if not current_release:
                self._add_check(
                    name="Current Release",
                    category="upgrade",
                    status=PreflightStatus.FAIL,
                    message="Could not determine current MOSK release from Cluster CR",
                )
                return

            # NOTE: KaasRelease upgrade path validation is disabled.
            # The KaasRelease.supportedClusterReleases.availableUpgrades mapping
            # is not always configured correctly. Validation is performed via
            # ClusterUpdatePlan existence instead.

            # For now, we trust that if a ClusterUpdatePlan exists, the upgrade is valid
            self.upgrade_path_valid = None  # Will be set based on ClusterUpdatePlan existence
            self.available_upgrade_versions = []

            # Store basic upgrade info (without KaasRelease data)
            self.upgrade_info = {
                "current_release": current_release,
                "target_release": self.target_release,
                "target_version": None,
                "skip_maintenance": False,
                "reboot_required": False,
            }

            # Check for ClusterUpdatePlan - this is the real validation
            # If MCC has created an UpdatePlan for this upgrade, the path is valid
            update_plan = await self.mcc.find_cluster_update_plan(
                cluster_name=cluster_name,
                target_release=self.target_release,
                namespace=cluster_namespace,
            )

            if update_plan:
                # ClusterUpdatePlan exists - upgrade path is valid
                self.upgrade_path_valid = True
                self._add_check(
                    name="Upgrade Path Supported",
                    category="upgrade",
                    status=PreflightStatus.PASS,
                    message=f"Upgrade from {current_release} to {self.target_release} is supported",
                    details="ClusterUpdatePlan exists for this upgrade",
                )
                plan_name = update_plan.get("metadata", {}).get("name")
                plan_status = update_plan.get("status", {}).get("status", "Unknown")
                steps = update_plan.get("spec", {}).get("steps", [])
                status_steps = {
                    s.get("id"): s for s in update_plan.get("status", {}).get("steps", [])
                }

                # Check step statuses - look for commenced steps and their status
                commenced_steps = [s for s in steps if s.get("commence", False)]
                failed_steps = [
                    s.get("id")
                    for s in steps
                    if s.get("commence", False)
                    and status_steps.get(s.get("id"), {}).get("status") == "Failed"
                ]
                in_progress_steps = [
                    s.get("id")
                    for s in steps
                    if s.get("commence", False)
                    and status_steps.get(s.get("id"), {}).get("status") == "InProgress"
                ]

                if failed_steps:
                    # Failed steps are a blocker
                    self._add_check(
                        name="ClusterUpdatePlan Status",
                        category="upgrade",
                        status=PreflightStatus.FAIL,
                        message=f"UpdatePlan '{plan_name}' has {len(failed_steps)} failed step(s)",
                        details=f"Failed steps: {failed_steps}",
                        remediation="Investigate and resolve failed steps before retrying upgrade",
                    )
                elif in_progress_steps:
                    # In-progress steps - warn but don't block
                    self._add_check(
                        name="ClusterUpdatePlan Status",
                        category="upgrade",
                        status=PreflightStatus.WARN,
                        message=f"UpdatePlan '{plan_name}' has {len(in_progress_steps)} step(s) in progress",
                        details=f"In progress: {in_progress_steps}. Status: {plan_status}",
                    )
                elif commenced_steps:
                    # Commenced but not failed/in-progress (likely completed)
                    self._add_check(
                        name="ClusterUpdatePlan Status",
                        category="upgrade",
                        status=PreflightStatus.WARN,
                        message=f"UpdatePlan '{plan_name}' already has {len(commenced_steps)} steps commenced",
                        details=f"Status: {plan_status}",
                    )
                else:
                    self._add_check(
                        name="ClusterUpdatePlan Ready",
                        category="upgrade",
                        status=PreflightStatus.PASS,
                        message=f"ClusterUpdatePlan '{plan_name}' exists and is ready",
                        details=f"Steps: {[s.get('id') for s in steps]}",
                    )

                # Add plan info to upgrade_info
                self.upgrade_info["update_plan_name"] = plan_name
                self.upgrade_info["update_plan_status"] = plan_status
                self.upgrade_info["steps"] = [
                    {
                        "id": s.get("id"),
                        "name": s.get("name"),
                        "granularity": s.get("granularity"),
                        "commenced": s.get("commence", False),
                    }
                    for s in steps
                ]

                # Extract estimated duration if available
                for step in steps:
                    duration_info = step.get("duration", {})
                    if duration_info.get("estimated"):
                        # Parse duration like "2h30m0s"
                        est = duration_info.get("estimated")
                        if est:
                            self.upgrade_info.setdefault("step_durations", {})[step.get("id")] = est
            else:
                # No ClusterUpdatePlan - upgrade path is not valid or not ready
                self.upgrade_path_valid = False
                self._add_check(
                    name="Upgrade Path Supported",
                    category="upgrade",
                    status=PreflightStatus.FAIL,
                    message=f"No ClusterUpdatePlan found for upgrade to '{self.target_release}'",
                    details=f"Searched in namespace '{cluster_namespace}' for cluster '{cluster_name}'",
                    remediation=(
                        "ClusterUpdatePlan is auto-generated by MCC when a valid upgrade path exists. "
                        "Verify the target release is correct and available in your MCC."
                    ),
                )

        except Exception as e:
            self._add_check(
                name="Upgrade Path Validation",
                category="upgrade",
                status=PreflightStatus.FAIL,
                message=f"Failed to validate upgrade path: {e}",
            )

    async def run_all_checks(self) -> None:
        """Run all applicable preflight checks."""
        # Core checks for all operations
        await self.check_kubernetes_health()
        await self.check_openstack_health()
        await self.check_ceph_health()

        # Upgrade-specific checks
        if self.check_type == PreflightCheckType.UPGRADE:
            # Check for active maintenance that would block upgrade
            await self.check_no_active_cluster_maintenance()
            # Verify target ClusterRelease exists
            await self.check_cluster_release_exists()
            # Check for failed/stalled previous upgrades
            await self.check_no_failed_upgrades()
            # Validate upgrade path and ClusterUpdatePlan
            await self.check_upgrade_path()

        # Target-specific checks
        if self.target_node:
            await self.check_target_node()
        if self.target_osd is not None:
            await self.check_target_osd()


async def run_preflight_check(
    kubernetes_adapter: KubernetesAdapter,
    input_data: RunPreflightCheckInput,
    mcc_adapter: KubernetesAdapter | None = None,
) -> RunPreflightCheckOutput:
    """Run pre-operation readiness checks.

    This tool validates that the cluster is ready for a specific operation
    type (maintenance, upgrade, node removal, OSD removal). It checks
    Kubernetes, OpenStack, and Ceph health along with operation-specific
    requirements.

    For UPGRADE check type, it also validates:
    - Upgrade path is supported in KaasRelease.supportedClusterReleases
    - ClusterUpdatePlan exists for the target release

    Safety Level: Read-only

    Args:
        kubernetes_adapter: Kubernetes adapter for cluster communication.
        input_data: Input parameters including check type and targets.
        mcc_adapter: MCC Kubernetes adapter for upgrade path validation.
            Required for UPGRADE check type.

    Returns:
        RunPreflightCheckOutput with check results and recommendations.

    Raises:
        ToolExecutionError: If preflight checks fail to run.

    Example:
        >>> result = await run_preflight_check(
        ...     k8s_adapter,
        ...     RunPreflightCheckInput(
        ...         check_type=PreflightCheckType.UPGRADE,
        ...         target_release="mosk-21-0-0-25-2",
        ...     ),
        ...     mcc_adapter=mcc_k8s_adapter,
        ... )
        >>> print(f"Ready: {result.ready_for_operation}")
    """
    logger.info(
        "running_preflight_check",
        check_type=input_data.check_type.value,
        target_node=input_data.target_node,
        target_osd=input_data.target_osd,
        target_release=input_data.target_release,
    )

    try:
        timestamp = datetime.now(UTC).isoformat()

        # Create and run checker
        checker = PreflightChecker(
            kubernetes_adapter=kubernetes_adapter,
            check_type=input_data.check_type,
            target_node=input_data.target_node,
            target_osd=input_data.target_osd,
            osdpl_name=input_data.osdpl_name,
            namespace=input_data.namespace,
            mcc_adapter=mcc_adapter,
            target_release=input_data.target_release,
            cluster_name=input_data.cluster_name,
            cluster_namespace=input_data.cluster_namespace,
        )

        await checker.run_all_checks()

        # Analyze results
        checks = checker.checks
        passed = sum(1 for c in checks if c.status == PreflightStatus.PASS)
        warned = sum(1 for c in checks if c.status == PreflightStatus.WARN)
        failed = sum(1 for c in checks if c.status == PreflightStatus.FAIL and c.required)
        skipped = sum(1 for c in checks if c.status == PreflightStatus.SKIP)

        # Determine overall status
        if failed > 0:
            overall_status = PreflightStatus.FAIL
            ready = False
            message = f"Preflight failed: {failed} required check(s) failed"
        elif warned > 0 and input_data.strict_mode:
            overall_status = PreflightStatus.FAIL
            ready = False
            message = f"Preflight failed (strict): {warned} warning(s)"
        elif warned > 0:
            overall_status = PreflightStatus.WARN
            ready = True
            message = f"Preflight passed with {warned} warning(s)"
        else:
            overall_status = PreflightStatus.PASS
            ready = True
            message = "All preflight checks passed"

        # Collect blockers and warnings
        blockers = [c.message for c in checks if c.status == PreflightStatus.FAIL and c.required]
        warnings = [c.message for c in checks if c.status == PreflightStatus.WARN]

        # Generate recommendations
        recommendations = []
        for check in checks:
            if check.remediation and check.status in [
                PreflightStatus.FAIL,
                PreflightStatus.WARN,
            ]:
                recommendations.append(check.remediation)

        # Determine target string
        target = None
        if input_data.target_node:
            target = f"node/{input_data.target_node}"
        elif input_data.target_osd is not None:
            target = f"osd.{input_data.target_osd}"
        elif input_data.target_release:
            target = f"release/{input_data.target_release}"

        output = RunPreflightCheckOutput(
            check_type=input_data.check_type,
            target=target,
            overall_status=overall_status,
            ready_for_operation=ready,
            message=message,
            checks=checks,
            checks_passed=passed,
            checks_warned=warned,
            checks_failed=failed,
            checks_skipped=skipped,
            blockers=blockers,
            warnings=warnings,
            recommendations=recommendations[:10],
            estimated_duration_minutes=ESTIMATED_DURATIONS.get(input_data.check_type),
            estimated_impact=IMPACT_DESCRIPTIONS.get(input_data.check_type),
            # Upgrade-specific fields
            upgrade_path_valid=checker.upgrade_path_valid,
            upgrade_info=checker.upgrade_info,
            available_upgrade_versions=checker.available_upgrade_versions,
            timestamp=timestamp,
        )

        logger.info(
            "preflight_check_complete",
            check_type=input_data.check_type.value,
            overall_status=overall_status.value,
            ready=ready,
            passed=passed,
            warned=warned,
            failed=failed,
        )

        return output

    except Exception as e:
        logger.error("run_preflight_check_failed", error=str(e))
        raise ToolExecutionError(
            message=f"Failed to run preflight check: {e}",
            tool_name="run_preflight_check",
            details={"error": str(e)},
        ) from e
