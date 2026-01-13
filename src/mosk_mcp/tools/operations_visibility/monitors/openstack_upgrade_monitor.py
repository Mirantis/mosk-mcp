"""OpenStack upgrade operation monitor.

This module provides the OpenStackUpgradeMonitor class for tracking
OpenStack upgrade progress through OSDPLStatus CR.

Uses MOSK kubeconfig for cluster access (the child cluster where
OpenStack is deployed).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from mosk_mcp.core.exceptions import ResourceNotFoundError
from mosk_mcp.observability.logging import get_logger
from mosk_mcp.tools.common.constants import OSDPLST_UPGRADING_STATES
from mosk_mcp.tools.operations_visibility.monitors.base import (
    BaseOperationMonitor,
    ProgressSnapshot,
)


if TYPE_CHECKING:
    from mosk_mcp.adapters.kubernetes import KubernetesAdapter


logger = get_logger(__name__)


class UpgradePhase:
    """OpenStack upgrade phases."""

    NOT_STARTED = "not_started"
    INITIALIZING = "initializing"
    UPGRADING_CONTROL_PLANE = "upgrading_control_plane"
    UPGRADING_SERVICES = "upgrading_services"
    UPGRADING_COMPUTE = "upgrading_compute"
    FINALIZING = "finalizing"
    COMPLETED = "completed"
    FAILED = "failed"


# Human-readable messages for each phase
PHASE_MESSAGES: dict[str, str] = {
    UpgradePhase.NOT_STARTED: "Upgrade not started",
    UpgradePhase.INITIALIZING: "Initializing upgrade process",
    UpgradePhase.UPGRADING_CONTROL_PLANE: "Upgrading control plane services",
    UpgradePhase.UPGRADING_SERVICES: "Upgrading OpenStack services",
    UpgradePhase.UPGRADING_COMPUTE: "Upgrading compute nodes",
    UpgradePhase.FINALIZING: "Finalizing upgrade",
    UpgradePhase.COMPLETED: "Upgrade completed successfully",
    UpgradePhase.FAILED: "Upgrade failed",
}


class OpenStackUpgradeMonitor(BaseOperationMonitor):
    """Monitor for OpenStack upgrade operations.

    Tracks progress of OpenStack version upgrades by monitoring
    the OSDPL and OSDPLStatus resources.

    Uses MOSK kubeconfig for accessing the child cluster where
    OpenStack is deployed.
    """

    def __init__(
        self,
        adapter: KubernetesAdapter,
        target: str,
        namespace: str = "openstack",
    ) -> None:
        """Initialize the OpenStack upgrade monitor.

        Args:
            adapter: MOSK Kubernetes adapter.
            target: OSDPL name to monitor (e.g., 'mos').
            namespace: OpenStack namespace (default: 'openstack').
        """
        super().__init__(adapter, target, namespace)
        self._current_phase: str = UpgradePhase.NOT_STARTED
        self._error_message: str | None = None
        self._is_upgrading: bool = False
        self._progress_percent: int = 0
        self._from_version: str | None = None
        self._to_version: str | None = None
        self._services_status: dict[str, Any] = {}

    async def get_progress(self) -> ProgressSnapshot:
        """Get current OpenStack upgrade progress.

        Queries OSDPL and OSDPLStatus resources to determine
        current upgrade state and progress.

        Returns:
            Progress snapshot with current state.
        """
        logger.debug(
            "polling_openstack_upgrade_progress",
            target=self.target,
            namespace=self.namespace,
        )

        # Get OSDPL and OSDPLStatus
        osdpl_data = await self._get_osdpl_data()
        osdplst_data = await self._get_osdplst_data()

        # Determine state from OSDPLStatus (source of truth) or fall back to OSDPL
        self._determine_state(osdpl_data, osdplst_data)

        message = PHASE_MESSAGES.get(self._current_phase, "Unknown state")

        # Add version info to message
        if self._from_version and self._to_version and self._from_version != self._to_version:
            message = f"{message} ({self._from_version} → {self._to_version})"

        if self._error_message:
            message = f"{message}: {self._error_message}"

        # Build details
        details: dict[str, Any] = {
            "from_version": self._from_version,
            "to_version": self._to_version,
            "is_upgrading": self._is_upgrading,
        }

        # Add service progress summary
        if self._services_status:
            completed_services = sum(
                1 for s in self._services_status.values() if s.get("state") == "APPLIED"
            )
            total_services = len(self._services_status)
            details["services_completed"] = completed_services
            details["services_total"] = total_services

            # List in-progress services
            in_progress = [
                name
                for name, s in self._services_status.items()
                if s.get("state") in OSDPLST_UPGRADING_STATES
            ]
            if in_progress:
                details["services_in_progress"] = in_progress[:5]  # Limit to 5

        return ProgressSnapshot.create(
            progress_percent=self._progress_percent,
            phase=self._current_phase,
            message=message,
            details=details,
        )

    def is_complete(self) -> bool:
        """Check if upgrade is complete."""
        return self._current_phase == UpgradePhase.COMPLETED

    def has_failed(self) -> bool:
        """Check if upgrade has failed."""
        return self._current_phase == UpgradePhase.FAILED

    def get_error_message(self) -> str | None:
        """Get error message if upgrade failed."""
        return self._error_message

    async def _get_osdpl_data(self) -> dict[str, Any]:
        """Get OpenStackDeployment data."""
        try:
            osdpl = await self.adapter.get_openstack_deployment(
                name=self.target,
                namespace=self.namespace,
            )
            return osdpl or {}
        except ResourceNotFoundError:
            logger.warning(
                "osdpl_not_found",
                name=self.target,
                namespace=self.namespace,
            )
            return {}
        except Exception as e:
            logger.warning("failed_to_get_osdpl", error=str(e))
            return {"error": str(e)}

    async def _get_osdplst_data(self) -> dict[str, Any]:
        """Get OpenStackDeploymentStatus data.

        Returns:
            OSDPLStatus resource data.

        Raises:
            ResourceNotFoundError: If OSDPLStatus is not found.
        """
        osdplst = await self.adapter.get_openstack_deployment_status(
            name=self.target,
            namespace=self.namespace,
        )
        if not osdplst:
            raise ResourceNotFoundError(
                f"OSDPLStatus '{self.target}' not found in namespace '{self.namespace}'. "
                "This tool requires OSDPLStatus CR which is available in modern MOSK versions."
            )
        return osdplst

    def _extract_started_at(
        self,
        osdpl: dict[str, Any],
        osdplst: dict[str, Any],
    ) -> str | None:
        """Extract operation start time from OSDPL resources.

        Args:
            osdpl: OSDPL resource data.
            osdplst: OSDPLStatus resource data.

        Returns:
            ISO timestamp string or None.
        """
        # Try OSDPLStatus first - it may have updateStartedAt
        if osdplst:
            osdplst_status = osdplst.get("status", {})
            started = osdplst_status.get("updateStartedAt")
            if started:
                return cast("str", started)

        # Fall back to OSDPL status
        status = osdpl.get("status", {})
        started = status.get("updateStartedAt")
        if started:
            return cast("str", started)

        # Fall back to creation time as last resort
        metadata = osdpl.get("metadata", {})
        return cast("str | None", metadata.get("creationTimestamp"))

    def _determine_state(
        self,
        osdpl: dict[str, Any],
        osdplst: dict[str, Any],
    ) -> None:
        """Determine current upgrade state from OSDPLStatus.

        Updates internal state variables based on cluster data.

        Args:
            osdpl: OSDPL resource data.
            osdplst: OSDPLStatus resource data (required).
        """
        # Handle errors
        if "error" in osdpl:
            self._current_phase = UpgradePhase.FAILED
            self._error_message = osdpl.get("error")
            self._progress_percent = -1
            return

        if not osdpl:
            self._current_phase = UpgradePhase.NOT_STARTED
            self._error_message = "OSDPL not found"
            self._progress_percent = 0
            return

        if not osdplst:
            self._current_phase = UpgradePhase.FAILED
            self._error_message = "OSDPLStatus not found"
            self._progress_percent = -1
            return

        # Extract actual operation start time
        actual_started = self._extract_started_at(osdpl, osdplst)
        if actual_started and self._started_at is None:
            self._started_at = actual_started

        spec = osdpl.get("spec", {})
        status = osdpl.get("status", {})

        # Get versions
        self._to_version = spec.get("openStackVersion", "unknown")
        self._from_version = status.get("openStackVersion", self._to_version)

        # Use OSDPLStatus (source of truth)
        osdplst_status = osdplst.get("status", {})
        osdpl_section = osdplst_status.get("osdpl", {})

        osdplst_state = osdpl_section.get("state")
        lcm_progress = osdpl_section.get("lcmProgress")
        self._services_status = osdplst_status.get("services", {})

        # Get version from osdplst if available
        if osdpl_section.get("openstackVersion"):
            self._from_version = osdpl_section.get("openstackVersion")

        # Determine state and progress from osdplst
        if osdplst_state == "FAILED":
            self._current_phase = UpgradePhase.FAILED
            self._is_upgrading = False
            self._progress_percent = -1
            self._error_message = "Upgrade failed"
            return

        if osdplst_state == "APPLIED":
            if self._from_version == self._to_version:
                self._current_phase = UpgradePhase.COMPLETED
                self._is_upgrading = False
                self._progress_percent = 100
                return
            else:
                # Version mismatch but state is APPLIED - unusual
                self._current_phase = UpgradePhase.FINALIZING
                self._is_upgrading = True
                self._progress_percent = 95
                return

        if osdplst_state in OSDPLST_UPGRADING_STATES:
            self._is_upgrading = True

            # Calculate progress from lcmProgress (e.g., "12/18")
            if lcm_progress:
                self._progress_percent = self._parse_lcm_progress(lcm_progress)
            else:
                self._progress_percent = 50

            # Determine phase based on progress
            if self._progress_percent < 20:
                self._current_phase = UpgradePhase.INITIALIZING
            elif self._progress_percent < 50:
                self._current_phase = UpgradePhase.UPGRADING_CONTROL_PLANE
            elif self._progress_percent < 80:
                self._current_phase = UpgradePhase.UPGRADING_SERVICES
            elif self._progress_percent < 95:
                self._current_phase = UpgradePhase.UPGRADING_COMPUTE
            else:
                self._current_phase = UpgradePhase.FINALIZING
            return

        # Unknown state from osdplst
        self._current_phase = UpgradePhase.NOT_STARTED
        self._is_upgrading = False
        self._progress_percent = 0

    def _parse_lcm_progress(self, progress_str: str) -> int:
        """Parse LCM progress string to percentage.

        Args:
            progress_str: Progress string like '12/18'.

        Returns:
            Progress percentage (0-100).
        """
        try:
            if "/" in progress_str:
                parts = progress_str.split("/")
                ready = int(parts[0])
                total = int(parts[1])
                if total > 0:
                    return int((ready / total) * 100)
        except (ValueError, IndexError):
            pass
        return 50  # Default to 50% if parsing fails
