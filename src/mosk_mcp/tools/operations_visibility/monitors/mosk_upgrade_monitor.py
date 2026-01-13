"""MOSK platform upgrade operation monitor.

This module provides the MoskUpgradeMonitor class for tracking
MOSK platform upgrade progress by monitoring:

1. Machine.status.phase - Primary progress indicator
   - Ready -> Prepare -> Deploy -> Reconfigure -> Ready

2. LCMMachine.status - Detailed LCM state
   - status.state: Current LCM phase
   - status.release: Current release on machine
   - status.stateItemStatuses: Granular task progress

3. Cluster conditions - Stage completion indicators
   - Helm: Helm bundle upgrades
   - Ceph: Ceph controller upgrades
   - Nodes: Node readiness count
   - Kubernetes: K8s object upgrades
   - LCMAgent, StackLight, etc.

4. HelmBundle.status.releaseStatuses - Per-chart upgrade status

This improved implementation was created after observing that:
- ClusterUpgradeStatus.stages is often empty during upgrades
- MachineUpgradeStatus may not be created for minor upgrades
- Machine.status.phase is the most reliable progress indicator

Uses MCC kubeconfig for cluster access (the management cluster where
Cluster CRs are managed).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from mosk_mcp.core.exceptions import ResourceNotFoundError
from mosk_mcp.observability.logging import get_logger
from mosk_mcp.tools.common.parsers import parse_mosk_condition_ready
from mosk_mcp.tools.operations_visibility.monitors.base import (
    BaseOperationMonitor,
    ProgressSnapshot,
)


if TYPE_CHECKING:
    from mosk_mcp.adapters.kubernetes import KubernetesAdapter


logger = get_logger(__name__)


class MoskUpgradePhase:
    """MOSK platform upgrade phases."""

    NOT_STARTED = "not_started"
    HELM_UPGRADING = "helm_upgrading"
    MACHINES_PREPARING = "machines_preparing"
    MACHINES_DEPLOYING = "machines_deploying"
    MACHINES_RECONFIGURING = "machines_reconfiguring"
    CEPH_UPGRADING = "ceph_upgrading"
    FINALIZING = "finalizing"
    COMPLETED = "completed"
    FAILED = "failed"


class MachinePhase:
    """Machine LCM phases."""

    READY = "Ready"
    PREPARE = "Prepare"
    DEPLOY = "Deploy"
    RECONFIGURE = "Reconfigure"


# Human-readable messages for upgrade phases
PHASE_MESSAGES: dict[str, str] = {
    MoskUpgradePhase.NOT_STARTED: "Upgrade not started",
    MoskUpgradePhase.HELM_UPGRADING: "Upgrading Helm charts",
    MoskUpgradePhase.MACHINES_PREPARING: "Machines preparing for upgrade",
    MoskUpgradePhase.MACHINES_DEPLOYING: "Deploying machine updates",
    MoskUpgradePhase.MACHINES_RECONFIGURING: "Reconfiguring machines",
    MoskUpgradePhase.CEPH_UPGRADING: "Upgrading Ceph components",
    MoskUpgradePhase.FINALIZING: "Finalizing upgrade",
    MoskUpgradePhase.COMPLETED: "Upgrade completed successfully",
    MoskUpgradePhase.FAILED: "Upgrade failed",
}

# Machine phase weights for progress calculation
MACHINE_PHASE_WEIGHTS: dict[str, int] = {
    MachinePhase.READY: 100,  # Final state
    MachinePhase.PREPARE: 20,
    MachinePhase.DEPLOY: 50,
    MachinePhase.RECONFIGURE: 80,
}

# Cluster condition weights for progress tracking
# Format: condition_name -> (weight_when_ready, weight_when_not_ready)
CONDITION_WEIGHTS: dict[str, tuple[int, int]] = {
    "Helm": (20, 5),
    "Kubernetes": (25, 10),
    "Ceph": (30, 15),
    "LCMAgent": (35, 20),
    "StackLight": (40, 25),
    "Nodes": (90, 30),  # Nodes ready is a late-stage indicator
}


class MoskUpgradeMonitor(BaseOperationMonitor):
    """Monitor for MOSK platform upgrade operations.

    Tracks progress of MOSK platform upgrades by monitoring
    Machine.status.phase, cluster conditions, and HelmBundle status.

    Uses MCC kubeconfig for accessing the management cluster where
    Cluster CRs are managed.
    """

    def __init__(
        self,
        adapter: KubernetesAdapter,
        target: str,
        namespace: str = "default",
    ) -> None:
        """Initialize the MOSK upgrade monitor.

        Args:
            adapter: MCC Kubernetes adapter.
            target: Cluster name to monitor (e.g., 'mos').
            namespace: Cluster namespace (e.g., 'lab').
        """
        super().__init__(adapter, target, namespace)
        self._current_phase: str = MoskUpgradePhase.NOT_STARTED
        self._error_message: str | None = None
        self._is_upgrading: bool = False
        self._progress_percent: int = 0
        self._from_release: str | None = None
        self._to_release: str | None = None
        self._machine_phases: dict[str, str] = {}
        self._cluster_conditions: dict[str, dict[str, Any]] = {}
        self._helm_statuses: dict[str, dict[str, Any]] = {}

    async def get_progress(self) -> ProgressSnapshot:
        """Get current MOSK platform upgrade progress.

        Queries Machine phases, cluster conditions, and HelmBundle status
        to determine current upgrade state and progress.

        Returns:
            Progress snapshot with current state.
        """
        logger.debug(
            "polling_mosk_upgrade_progress",
            target=self.target,
            namespace=self.namespace,
        )

        # Get Cluster data (conditions and release info)
        cluster_data = await self._get_cluster_data()

        # Get Machine phases
        machine_data = await self._get_machine_phases()

        # Get LCMMachine data for detailed status
        lcm_machine_data = await self._get_lcm_machine_data()

        # Get HelmBundle status
        helm_bundle_data = await self._get_helm_bundle_data()

        # Determine state from all data sources
        self._determine_state(cluster_data, machine_data, lcm_machine_data, helm_bundle_data)

        message = PHASE_MESSAGES.get(self._current_phase, "Unknown state")

        # Add release info to message
        if self._from_release and self._to_release and self._from_release != self._to_release:
            message = f"{message} ({self._from_release} -> {self._to_release})"

        if self._error_message:
            message = f"{message}: {self._error_message}"

        # Build details
        details: dict[str, Any] = {
            "from_release": self._from_release,
            "to_release": self._to_release,
            "is_upgrading": self._is_upgrading,
        }

        # Add machine phase summary
        if self._machine_phases:
            phase_counts: dict[str, int] = {}
            for phase in self._machine_phases.values():
                phase_counts[phase] = phase_counts.get(phase, 0) + 1

            details["machine_phases"] = phase_counts
            details["machines_total"] = len(self._machine_phases)
            details["machines_ready"] = phase_counts.get(MachinePhase.READY, 0)

            # List machines not in Ready state
            not_ready = [
                {"name": name, "phase": phase}
                for name, phase in self._machine_phases.items()
                if phase != MachinePhase.READY
            ]
            if not_ready:
                details["machines_in_progress"] = not_ready[:6]

        # Add cluster condition summary
        if self._cluster_conditions:
            conditions_summary = {}
            not_ready_conditions = []
            for name, cond in self._cluster_conditions.items():
                is_ready = cond.get("ready", False)
                conditions_summary[name] = "ready" if is_ready else "not_ready"
                if not is_ready:
                    not_ready_conditions.append(
                        {
                            "condition": name,
                            "message": cond.get("message", "")[:100],
                        }
                    )

            details["conditions"] = conditions_summary
            if not_ready_conditions:
                details["conditions_not_ready"] = not_ready_conditions[:5]

        # Add helm upgrade status
        if self._helm_statuses:
            not_ready_charts = [
                {"chart": name, "ready": status.get("ready")}
                for name, status in self._helm_statuses.items()
                if not status.get("ready")
            ]
            if not_ready_charts:
                details["helm_not_ready"] = not_ready_charts[:5]

        return ProgressSnapshot.create(
            progress_percent=self._progress_percent,
            phase=self._current_phase,
            message=message,
            details=details,
        )

    def is_complete(self) -> bool:
        """Check if upgrade is complete."""
        return self._current_phase == MoskUpgradePhase.COMPLETED

    def has_failed(self) -> bool:
        """Check if upgrade has failed."""
        return self._current_phase == MoskUpgradePhase.FAILED

    def get_error_message(self) -> str | None:
        """Get error message if upgrade failed."""
        return self._error_message

    async def _get_cluster_data(self) -> dict[str, Any]:
        """Get Cluster CR data including conditions."""
        try:
            cluster = await self.adapter.get_cluster(
                name=self.target,
                namespace=self.namespace,
            )
            return cluster or {}
        except ResourceNotFoundError:
            logger.warning(
                "cluster_not_found",
                name=self.target,
                namespace=self.namespace,
            )
            return {}
        except Exception as e:
            logger.warning("failed_to_get_cluster", error=str(e))
            return {"error": str(e)}

    async def _get_machine_phases(self) -> list[dict[str, Any]]:
        """Get Machine CRs with their phases for this cluster."""
        try:
            machines = await self.adapter.list_machines(namespace=self.namespace)

            # Filter machines belonging to this cluster
            cluster_machines = [m for m in machines if self._is_owned_by_cluster(m, self.target)]

            return cluster_machines

        except Exception as e:
            logger.warning("failed_to_get_machines", error=str(e))
            return []

    async def _get_lcm_machine_data(self) -> list[dict[str, Any]]:
        """Get LCMMachine CRs for detailed LCM state."""
        try:
            lcm_machines = await self.adapter.list_lcm_machines(namespace=self.namespace)
            return lcm_machines
        except Exception as e:
            logger.warning("failed_to_get_lcm_machines", error=str(e))
            return []

    async def _get_helm_bundle_data(self) -> dict[str, Any] | None:
        """Get HelmBundle status for this cluster."""
        try:
            helm_bundle = await self.adapter.get_helm_bundle(
                name=self.target,
                namespace=self.namespace,
            )
            return helm_bundle
        except ResourceNotFoundError:
            return None
        except Exception as e:
            logger.warning("failed_to_get_helm_bundle", error=str(e))
            return None

    def _is_owned_by_cluster(self, resource: dict[str, Any], cluster_name: str) -> bool:
        """Check if resource is owned by the specified cluster."""
        owner_refs = resource.get("metadata", {}).get("ownerReferences", [])
        for ref in owner_refs:
            if ref.get("kind") == "Cluster" and ref.get("name") == cluster_name:
                return True

        # For machines, also check the cluster label
        labels = resource.get("metadata", {}).get("labels", {})
        return bool(labels.get("cluster.sigs.k8s.io/cluster-name") == cluster_name)

    def _determine_state(
        self,
        cluster: dict[str, Any],
        machines: list[dict[str, Any]],
        lcm_machines: list[dict[str, Any]],
        helm_bundle: dict[str, Any] | None,
    ) -> None:
        """Determine current upgrade state from all data sources.

        Args:
            cluster: Cluster CR data.
            machines: List of Machine CRs.
            lcm_machines: List of LCMMachine CRs (used for release tracking).
            helm_bundle: HelmBundle CR data.
        """
        # Handle errors and edge cases
        if "error" in cluster:
            self._set_failed_state(cluster.get("error", "Unknown error"))
            return

        if not cluster:
            self._set_not_started_state("Cluster not found")
            return

        # Parse all data sources
        self._parse_cluster_data(cluster)
        self._parse_machine_data(machines, lcm_machines)
        self._parse_helm_data(helm_bundle)

        # Check for completion
        cluster_ready = cluster.get("status", {}).get("ready", True)
        if self._check_completion(cluster_ready):
            return

        # Determine current upgrade phase
        self._is_upgrading = True
        self._determine_current_phase()

    def _set_failed_state(self, error: str) -> None:
        """Set monitor to failed state."""
        self._current_phase = MoskUpgradePhase.FAILED
        self._error_message = error
        self._progress_percent = -1

    def _set_not_started_state(self, message: str) -> None:
        """Set monitor to not started state."""
        self._current_phase = MoskUpgradePhase.NOT_STARTED
        self._error_message = message
        self._progress_percent = 0

    def _parse_cluster_data(self, cluster: dict[str, Any]) -> None:
        """Parse cluster CR data for release and conditions."""
        provider_spec = cluster.get("spec", {}).get("providerSpec", {}).get("value", {})
        provider_status = cluster.get("status", {}).get("providerStatus", {})

        self._to_release = provider_spec.get("release", "unknown")
        self._from_release = provider_status.get("release") or self._to_release

        # Parse cluster conditions
        # Note: MOSK conditions use 'ready' field (bool) not 'status' field
        conditions = provider_status.get("conditions", [])
        self._cluster_conditions = {}
        for cond in conditions:
            cond_type = cond.get("type")
            if cond_type:
                is_ready = parse_mosk_condition_ready(cond)
                self._cluster_conditions[cond_type] = {
                    "status": "True" if is_ready else "False",
                    "ready": is_ready,
                    "message": cond.get("message", ""),
                }

    def _parse_machine_data(
        self,
        machines: list[dict[str, Any]],
        lcm_machines: list[dict[str, Any]],
    ) -> None:
        """Parse machine and LCM machine data."""
        # Build LCM machine lookup by name for release info
        lcm_by_name = {m.get("metadata", {}).get("name", ""): m for m in lcm_machines}

        self._machine_phases = {}
        for machine in machines:
            name = machine.get("metadata", {}).get("name", "unknown")
            phase = machine.get("status", {}).get("phase", "")
            # Also check providerStatus for LCM status
            provider_status_machine = machine.get("status", {}).get("providerStatus", {})
            lcm_status = provider_status_machine.get("status", phase)

            # Get additional info from LCMMachine if available
            lcm_machine = lcm_by_name.get(name)
            if lcm_machine and not lcm_status:
                lcm_status = lcm_machine.get("status", {}).get("state", "")

            self._machine_phases[name] = lcm_status or phase or "Unknown"

    def _parse_helm_data(self, helm_bundle: dict[str, Any] | None) -> None:
        """Parse helm bundle status."""
        self._helm_statuses = {}
        if helm_bundle:
            release_statuses = helm_bundle.get("status", {}).get("releaseStatuses", {})
            for chart_name, status in release_statuses.items():
                self._helm_statuses[chart_name] = {
                    "ready": status.get("ready", False),
                    "success": status.get("success", False),
                    "status": status.get("status", ""),
                }

    def _check_completion(self, cluster_ready: bool) -> bool:
        """Check if upgrade is complete. Returns True if complete."""
        if cluster_ready:
            all_machines_ready = all(
                phase == MachinePhase.READY for phase in self._machine_phases.values()
            )
            if all_machines_ready:
                self._current_phase = MoskUpgradePhase.COMPLETED
                self._is_upgrading = False
                self._progress_percent = 100
                return True
        return False

    def _determine_current_phase(self) -> None:
        """Determine current phase based on cluster conditions and machine states."""
        # Check Helm status first
        helm_ready = self._cluster_conditions.get("Helm", {}).get("ready", False)
        if not helm_ready:
            self._current_phase = MoskUpgradePhase.HELM_UPGRADING
            self._progress_percent = 5
            return

        # Check Ceph status
        ceph_cond = self._cluster_conditions.get("Ceph", {})
        ceph_ready = ceph_cond.get("ready", False)
        ceph_message = ceph_cond.get("message", "")
        if not ceph_ready and ("osd" in ceph_message.lower() or "ceph" in ceph_message.lower()):
            self._current_phase = MoskUpgradePhase.CEPH_UPGRADING
            self._progress_percent = self._calculate_progress_from_machines()
            return

        # Determine phase from machine states
        self._determine_phase_from_machines()

    def _determine_phase_from_machines(self) -> None:
        """Determine upgrade phase based on machine phases."""
        phase_counts: dict[str, int] = {}
        for phase in self._machine_phases.values():
            phase_counts[phase] = phase_counts.get(phase, 0) + 1

        total_machines = len(self._machine_phases)

        # Determine phase based on machine states
        if phase_counts.get(MachinePhase.DEPLOY, 0) > 0:
            self._current_phase = MoskUpgradePhase.MACHINES_DEPLOYING
        elif phase_counts.get(MachinePhase.RECONFIGURE, 0) > 0:
            self._current_phase = MoskUpgradePhase.MACHINES_RECONFIGURING
        elif phase_counts.get(MachinePhase.PREPARE, 0) > 0:
            self._current_phase = MoskUpgradePhase.MACHINES_PREPARING
        elif total_machines > 0 and phase_counts.get(MachinePhase.READY, 0) == total_machines:
            self._current_phase = MoskUpgradePhase.FINALIZING
        else:
            self._current_phase = MoskUpgradePhase.MACHINES_PREPARING

        # Calculate progress
        self._progress_percent = self._calculate_progress_from_machines()

    def _calculate_progress_from_machines(self) -> int:
        """Calculate overall progress based on machine phases."""
        if not self._machine_phases:
            return 0

        total_weight = 0
        for phase in self._machine_phases.values():
            weight = MACHINE_PHASE_WEIGHTS.get(phase, 0)
            total_weight += weight

        avg_progress = total_weight // len(self._machine_phases)

        # Scale to 10-90% range (leaving room for helm start and finalize)
        return 10 + int(avg_progress * 0.8)
