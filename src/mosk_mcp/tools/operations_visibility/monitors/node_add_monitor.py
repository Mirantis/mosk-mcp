"""Node add operation monitor.

This module provides the NodeAddMonitor class for tracking node
provisioning progress through all stages:
BMHi → BMH (registering→inspecting→preparing→available→provisioning→provisioned)
→ Machine → LCMMachine → Node Ready

Uses MCC kubeconfig for cluster access.
"""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING, Any

from mosk_mcp.core.exceptions import ResourceNotFoundError
from mosk_mcp.observability.logging import get_logger
from mosk_mcp.tools.operations_visibility.monitors.base import (
    BaseOperationMonitor,
    ProgressSnapshot,
)


if TYPE_CHECKING:
    from mosk_mcp.adapters.kubernetes import KubernetesAdapter


logger = get_logger(__name__)


class ProvisionPhase(str, Enum):
    """Node provisioning phases."""

    NOT_STARTED = "not_started"
    BMHI_CREATED = "bmhi_created"
    BMH_REGISTERING = "bmh_registering"
    BMH_INSPECTING = "bmh_inspecting"
    BMH_PREPARING = "bmh_preparing"
    BMH_AVAILABLE = "bmh_available"
    MACHINE_CREATED = "machine_created"
    BMH_PROVISIONING = "bmh_provisioning"
    BMH_PROVISIONED = "bmh_provisioned"
    MACHINE_DEPLOYING = "machine_deploying"
    MACHINE_READY = "machine_ready"
    NODE_READY = "node_ready"
    COMPLETED = "completed"
    ERROR = "error"


# Progress percentage for each phase
PHASE_PROGRESS: dict[ProvisionPhase, int] = {
    ProvisionPhase.NOT_STARTED: 0,
    ProvisionPhase.BMHI_CREATED: 5,
    ProvisionPhase.BMH_REGISTERING: 10,
    ProvisionPhase.BMH_INSPECTING: 20,
    ProvisionPhase.BMH_PREPARING: 30,
    ProvisionPhase.BMH_AVAILABLE: 40,
    ProvisionPhase.MACHINE_CREATED: 45,
    ProvisionPhase.BMH_PROVISIONING: 55,
    ProvisionPhase.BMH_PROVISIONED: 70,
    ProvisionPhase.MACHINE_DEPLOYING: 80,
    ProvisionPhase.MACHINE_READY: 90,
    ProvisionPhase.NODE_READY: 95,
    ProvisionPhase.COMPLETED: 100,
    ProvisionPhase.ERROR: -1,
}

# Human-readable messages for each phase
PHASE_MESSAGES: dict[ProvisionPhase, str] = {
    ProvisionPhase.NOT_STARTED: "Node provisioning not started",
    ProvisionPhase.BMHI_CREATED: "BareMetalHostInventory created, waiting for BMH",
    ProvisionPhase.BMH_REGISTERING: "BMH registering with BMC",
    ProvisionPhase.BMH_INSPECTING: "BMH inspecting hardware",
    ProvisionPhase.BMH_PREPARING: "BMH preparing for provisioning",
    ProvisionPhase.BMH_AVAILABLE: "BMH available, ready for Machine",
    ProvisionPhase.MACHINE_CREATED: "Machine CR created, starting provisioning",
    ProvisionPhase.BMH_PROVISIONING: "BMH provisioning OS to bare metal",
    ProvisionPhase.BMH_PROVISIONED: "BMH provisioned, Machine deploying",
    ProvisionPhase.MACHINE_DEPLOYING: "Machine deploying, waiting for Ready",
    ProvisionPhase.MACHINE_READY: "Machine ready, waiting for Node",
    ProvisionPhase.NODE_READY: "Kubernetes Node ready",
    ProvisionPhase.COMPLETED: "Node provisioning completed successfully",
    ProvisionPhase.ERROR: "Node provisioning failed",
}


class NodeAddMonitor(BaseOperationMonitor):
    """Monitor for node add (provisioning) operations.

    Tracks progress through the complete node provisioning workflow
    from BMHi creation to Kubernetes Node ready state.

    Uses MCC kubeconfig for accessing the management cluster.
    """

    def __init__(
        self,
        adapter: KubernetesAdapter,
        target: str,
        namespace: str,
    ) -> None:
        """Initialize the node add monitor.

        Args:
            adapter: MCC Kubernetes adapter.
            target: Node/Machine name to monitor.
            namespace: MOSK machines namespace in MCC cluster.
        """
        super().__init__(adapter, target, namespace)
        self._current_phase: ProvisionPhase = ProvisionPhase.NOT_STARTED
        self._error_message: str | None = None
        self._resource_states: dict[str, Any] = {}

    async def get_progress(self) -> ProgressSnapshot:
        """Get current node provisioning progress.

        Queries BMHi, BMH, Machine, LCMMachine, and Node resources
        to determine current provisioning phase.

        Returns:
            Progress snapshot with current state.
        """
        logger.debug(
            "polling_node_add_progress",
            target=self.target,
            namespace=self.namespace,
        )

        # Query all resources
        bmhi_state = await self._get_bmhi_state()
        bmh_state = await self._get_bmh_state()
        machine_state = await self._get_machine_state()
        lcm_state = await self._get_lcmmachine_state()
        node_state = await self._get_node_state(machine_state.get("node_ref"))

        # Store states for details
        self._resource_states = {
            "bmhi": bmhi_state,
            "bmh": bmh_state,
            "machine": machine_state,
            "lcmmachine": lcm_state,
            "node": node_state,
        }

        # Determine current phase
        self._current_phase, self._error_message = self._determine_phase(
            bmhi_state, bmh_state, machine_state, lcm_state, node_state
        )

        progress = PHASE_PROGRESS.get(self._current_phase, 0)
        message = PHASE_MESSAGES.get(self._current_phase, "Unknown state")

        if self._error_message:
            message = f"{message}: {self._error_message}"

        return ProgressSnapshot.create(
            progress_percent=progress,
            phase=self._current_phase.value,
            message=message,
            details={
                "bmhi_exists": bmhi_state.get("exists", False),
                "bmh_state": bmh_state.get("state"),
                "machine_state": machine_state.get("state"),
                "lcmmachine_state": lcm_state.get("state"),
                "node_ready": node_state.get("ready", False),
                "powered_on": bmh_state.get("powered_on"),
            },
        )

    def is_complete(self) -> bool:
        """Check if node provisioning is complete."""
        return self._current_phase == ProvisionPhase.COMPLETED

    def has_failed(self) -> bool:
        """Check if node provisioning has failed."""
        return self._current_phase == ProvisionPhase.ERROR

    def get_error_message(self) -> str | None:
        """Get error message if provisioning failed."""
        return self._error_message

    async def _get_bmhi_state(self) -> dict[str, Any]:
        """Get BareMetalHostInventory state."""
        try:
            bmhi = await self.adapter.get_custom_resource(
                group="kaas.mirantis.com",
                version="v1alpha1",
                plural="baremetalhostinventories",
                name=self.target,
                namespace=self.namespace,
            )
            status = bmhi.get("status", {})
            return {
                "exists": True,
                "state": status.get("operationalStatus", "Unknown"),
                "error": status.get("errorMessage"),
            }
        except ResourceNotFoundError:
            return {"exists": False, "query_failed": False}
        except Exception as e:
            logger.warning("failed_to_get_bmhi", error=str(e))
            return {"exists": False, "query_failed": True, "error": str(e)}

    async def _get_bmh_state(self) -> dict[str, Any]:
        """Get BareMetalHost state."""
        try:
            bmh = await self.adapter.get_custom_resource(
                group="metal3.io",
                version="v1alpha1",
                plural="baremetalhosts",
                name=self.target,
                namespace=self.namespace,
            )
            status = bmh.get("status", {})
            provisioning = status.get("provisioning", {})
            return {
                "exists": True,
                "state": provisioning.get("state", "unknown"),
                "operational_status": status.get("operationalStatus"),
                "error": status.get("errorMessage"),
                "powered_on": status.get("poweredOn", False),
                "consumer": bmh.get("spec", {}).get("consumerRef", {}).get("name"),
            }
        except ResourceNotFoundError:
            return {"exists": False, "query_failed": False}
        except Exception as e:
            logger.warning("failed_to_get_bmh", error=str(e))
            return {"exists": False, "query_failed": True, "error": str(e)}

    async def _get_machine_state(self) -> dict[str, Any]:
        """Get Machine state."""
        try:
            machine = await self.adapter.get_custom_resource(
                group="cluster.k8s.io",
                version="v1alpha1",
                plural="machines",
                name=self.target,
                namespace=self.namespace,
            )
            status = machine.get("status", {})
            return {
                "exists": True,
                "state": status.get("phase", "Unknown"),
                "error": status.get("errorMessage"),
                "node_ref": status.get("nodeRef", {}).get("name"),
            }
        except ResourceNotFoundError:
            return {"exists": False, "query_failed": False}
        except Exception as e:
            logger.warning("failed_to_get_machine", error=str(e))
            return {"exists": False, "query_failed": True, "error": str(e)}

    async def _get_lcmmachine_state(self) -> dict[str, Any]:
        """Get LCMMachine state."""
        try:
            lcm = await self.adapter.get_custom_resource(
                group="lcm.mirantis.com",
                version="v1alpha1",
                plural="lcmmachines",
                name=self.target,
                namespace=self.namespace,
            )
            status = lcm.get("status", {})
            return {
                "exists": True,
                "state": status.get("state", "Unknown"),
            }
        except ResourceNotFoundError:
            return {"exists": False, "query_failed": False}
        except Exception as e:
            logger.warning("failed_to_get_lcmmachine", error=str(e))
            return {"exists": False, "query_failed": True, "error": str(e)}

    async def _get_node_state(self, node_name: str | None) -> dict[str, Any]:
        """Get Kubernetes Node state."""
        if not node_name:
            return {"exists": False}

        try:
            node = await self.adapter.get(
                kind="Node",
                name=node_name,
                namespace=None,  # Nodes are cluster-scoped
            )
            conditions = node.get("status", {}).get("conditions", [])
            ready_condition = next(
                (c for c in conditions if c.get("type") == "Ready"),
                None,
            )
            is_ready = ready_condition and ready_condition.get("status") == "True"
            return {
                "exists": True,
                "ready": is_ready,
                "message": ready_condition.get("message") if ready_condition else None,
            }
        except ResourceNotFoundError:
            return {"exists": False, "query_failed": False}
        except Exception as e:
            logger.warning("failed_to_get_node", error=str(e))
            return {"exists": False, "query_failed": True, "error": str(e)}

    def _determine_phase(
        self,
        bmhi: dict[str, Any],
        bmh: dict[str, Any],
        machine: dict[str, Any],
        lcm: dict[str, Any],
        node: dict[str, Any],
    ) -> tuple[ProvisionPhase, str | None]:
        """Determine current provisioning phase based on resource states.

        Args:
            bmhi: BMHi state dict.
            bmh: BMH state dict.
            machine: Machine state dict.
            lcm: LCMMachine state dict.
            node: Node state dict.

        Returns:
            Tuple of (current_phase, error_message).
        """
        # Check for errors first
        for resource, name in [
            (bmhi, "BMHi"),
            (bmh, "BMH"),
            (machine, "Machine"),
            (lcm, "LCMMachine"),
        ]:
            error = resource.get("error")
            if error and "error" in str(error).lower():
                return ProvisionPhase.ERROR, f"{name}: {error}"
            status = resource.get("operational_status") or resource.get("state")
            if status and "error" in str(status).lower():
                return ProvisionPhase.ERROR, f"{name}: {status}"

        # Check completion
        if (
            node.get("exists")
            and node.get("ready")
            and machine.get("exists")
            and machine.get("state") == "Ready"
            and lcm.get("exists")
            and lcm.get("state") == "Ready"
        ):
            return ProvisionPhase.COMPLETED, None

        # Node ready but waiting for final checks
        if node.get("exists") and node.get("ready"):
            return ProvisionPhase.NODE_READY, None

        # Machine/LCMMachine ready
        if machine.get("exists") and machine.get("state") == "Ready":
            if lcm.get("exists") and lcm.get("state") == "Ready":
                return ProvisionPhase.MACHINE_READY, None
            return ProvisionPhase.MACHINE_DEPLOYING, None

        # BMH provisioned
        if bmh.get("exists") and bmh.get("state") == "provisioned":
            if machine.get("exists"):
                return ProvisionPhase.BMH_PROVISIONED, None
            return ProvisionPhase.BMH_PROVISIONED, None

        # BMH provisioning
        if bmh.get("exists") and bmh.get("state") == "provisioning":
            return ProvisionPhase.BMH_PROVISIONING, None

        # Machine created but BMH not yet provisioning
        if machine.get("exists") and bmh.get("exists") and bmh.get("state") == "available":
            return ProvisionPhase.MACHINE_CREATED, None

        # BMH available
        if bmh.get("exists") and bmh.get("state") == "available":
            return ProvisionPhase.BMH_AVAILABLE, None

        # BMH preparing
        if bmh.get("exists") and bmh.get("state") == "preparing":
            return ProvisionPhase.BMH_PREPARING, None

        # BMH inspecting
        if bmh.get("exists") and bmh.get("state") == "inspecting":
            return ProvisionPhase.BMH_INSPECTING, None

        # BMH registering
        if bmh.get("exists") and bmh.get("state") == "registering":
            return ProvisionPhase.BMH_REGISTERING, None

        # BMHi exists but no BMH yet
        if bmhi.get("exists") and not bmh.get("exists"):
            return ProvisionPhase.BMHI_CREATED, None

        # BMHi exists and BMH exists (early state)
        if bmhi.get("exists") and bmh.get("exists"):
            return ProvisionPhase.BMH_REGISTERING, None

        # Nothing exists
        return ProvisionPhase.NOT_STARTED, None
