"""NodeMaintenanceRequest CRD models for MOSK node maintenance.

This module provides Pydantic models for the NodeMaintenanceRequest custom resource,
which manages node maintenance operations in MOSK clusters.
"""

from __future__ import annotations

import contextlib
from enum import Enum
from typing import TYPE_CHECKING, Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field

from mosk_mcp.adapters.crd.base import (
    KubernetesMetadata,
    KubernetesResource,
)


if TYPE_CHECKING:
    from datetime import datetime


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


class MaintenanceReason(str, Enum):
    """Reasons for node maintenance."""

    HARDWARE_FAILURE = "HardwareFailure"
    FIRMWARE_UPDATE = "FirmwareUpdate"
    OS_UPDATE = "OSUpdate"
    DISK_REPLACEMENT = "DiskReplacement"
    MEMORY_UPGRADE = "MemoryUpgrade"
    NETWORK_RECONFIGURATION = "NetworkReconfiguration"
    SCHEDULED_MAINTENANCE = "ScheduledMaintenance"
    DECOMMISSION = "Decommission"
    OTHER = "Other"


class DrainStrategy(str, Enum):
    """Strategy for draining workloads from node."""

    GRACEFUL = "Graceful"
    FORCE = "Force"
    LIVE_MIGRATE = "LiveMigrate"


class NodeMaintenanceRequestSpec(BaseModel):
    """Specification for NodeMaintenanceRequest resource.

    Attributes:
        node_name: Name of the node to maintain.
        reason: Reason for maintenance.
        description: Human-readable description.
        drain_strategy: How to handle workloads during maintenance.
        grace_period_seconds: Grace period for pod termination.
        skip_drain: Skip the drain phase.
        skip_ceph_checks: Skip Ceph health checks.
        expected_duration_minutes: Expected maintenance duration.
        crq_number: Change request number for audit trail.
        auto_uncordon: Automatically uncordon when maintenance completes.
    """

    model_config = ConfigDict(populate_by_name=True)

    node_name: str = Field(
        ...,
        alias="nodeName",
        description="Name of the node to maintain",
    )
    reason: MaintenanceReason = Field(
        default=MaintenanceReason.SCHEDULED_MAINTENANCE,
        description="Reason for maintenance",
    )
    description: str | None = Field(
        None,
        description="Human-readable description",
    )
    drain_strategy: DrainStrategy = Field(
        default=DrainStrategy.GRACEFUL,
        alias="drainStrategy",
        description="Strategy for draining workloads",
    )
    grace_period_seconds: int = Field(
        default=300,
        alias="gracePeriodSeconds",
        description="Grace period for pod termination",
    )
    skip_drain: bool = Field(
        default=False,
        alias="skipDrain",
        description="Skip the drain phase",
    )
    skip_ceph_checks: bool = Field(
        default=False,
        alias="skipCephChecks",
        description="Skip Ceph health checks",
    )
    expected_duration_minutes: int | None = Field(
        None,
        alias="expectedDurationMinutes",
        description="Expected maintenance duration",
    )
    crq_number: str | None = Field(
        None,
        alias="crqNumber",
        description="Change request number",
    )
    auto_uncordon: bool = Field(
        default=False,
        alias="autoUncordon",
        description="Auto-uncordon when done",
    )

    def to_kubernetes(self) -> dict[str, Any]:
        """Convert to Kubernetes API format."""
        result: dict[str, Any] = {
            "nodeName": self.node_name,
            "reason": self.reason.value,
            "drainStrategy": self.drain_strategy.value,
            "gracePeriodSeconds": self.grace_period_seconds,
            "skipDrain": self.skip_drain,
            "skipCephChecks": self.skip_ceph_checks,
            "autoUncordon": self.auto_uncordon,
        }
        if self.description is not None:
            result["description"] = self.description
        if self.expected_duration_minutes is not None:
            result["expectedDurationMinutes"] = self.expected_duration_minutes
        if self.crq_number is not None:
            result["crqNumber"] = self.crq_number
        return result


class EvictedPod(BaseModel):
    """Information about a pod evicted during drain.

    Attributes:
        name: Pod name.
        namespace: Pod namespace.
        kind: Owning controller kind.
        owner_name: Owning controller name.
        evicted_at: When the pod was evicted.
        reason: Reason for eviction.
    """

    model_config = ConfigDict(populate_by_name=True)

    name: str
    namespace: str
    kind: str | None = Field(None, description="Owning controller kind")
    owner_name: str | None = Field(
        None,
        alias="ownerName",
        description="Owning controller name",
    )
    evicted_at: datetime | None = Field(
        None,
        alias="evictedAt",
        description="When evicted",
    )
    reason: str | None = None


class NodeMaintenanceRequestStatus(BaseModel):
    """Status of NodeMaintenanceRequest resource.

    Attributes:
        phase: Current maintenance phase.
        started_at: When maintenance started.
        completed_at: When maintenance completed.
        drain_started_at: When drain phase started.
        drain_completed_at: When drain phase completed.
        evicted_pods: List of pods evicted during drain.
        total_evicted: Total number of evicted pods.
        ceph_health_before: Ceph health before maintenance.
        ceph_health_after: Ceph health after maintenance.
        conditions: Status conditions.
        message: Status message.
        error_message: Error message if failed.
        last_error_time: When last error occurred.
    """

    model_config = ConfigDict(populate_by_name=True)

    phase: MaintenancePhase | None = Field(None, description="Current phase")
    started_at: datetime | None = Field(
        None,
        alias="startedAt",
        description="When maintenance started",
    )
    completed_at: datetime | None = Field(
        None,
        alias="completedAt",
        description="When maintenance completed",
    )
    drain_started_at: datetime | None = Field(
        None,
        alias="drainStartedAt",
        description="When drain started",
    )
    drain_completed_at: datetime | None = Field(
        None,
        alias="drainCompletedAt",
        description="When drain completed",
    )
    evicted_pods: list[EvictedPod] = Field(
        default_factory=list,
        alias="evictedPods",
        description="Pods evicted during drain",
    )
    total_evicted: int = Field(
        default=0,
        alias="totalEvicted",
        description="Total evicted pods",
    )
    ceph_health_before: str | None = Field(
        None,
        alias="cephHealthBefore",
        description="Ceph health before",
    )
    ceph_health_after: str | None = Field(
        None,
        alias="cephHealthAfter",
        description="Ceph health after",
    )
    conditions: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Status conditions",
    )
    message: str | None = Field(None, description="Status message")
    error_message: str | None = Field(
        None,
        alias="errorMessage",
        description="Error message",
    )
    last_error_time: datetime | None = Field(
        None,
        alias="lastErrorTime",
        description="When last error occurred",
    )

    @property
    def is_complete(self) -> bool:
        """Check if maintenance is complete.

        Returns:
            True if in a terminal state.
        """
        return self.phase in [
            MaintenancePhase.COMPLETED,
            MaintenancePhase.FAILED,
            MaintenancePhase.CANCELLED,
        ]

    @property
    def is_successful(self) -> bool:
        """Check if maintenance completed successfully.

        Returns:
            True if completed without error.
        """
        return self.phase == MaintenancePhase.COMPLETED

    @property
    def is_drained(self) -> bool:
        """Check if node has been drained.

        Returns:
            True if node is drained or beyond.
        """
        return self.phase in [
            MaintenancePhase.DRAINED,
            MaintenancePhase.MAINTAINING,
            MaintenancePhase.UNCORDONING,
            MaintenancePhase.COMPLETED,
        ]

    @property
    def drain_duration_seconds(self) -> float | None:
        """Calculate drain duration in seconds.

        Returns:
            Duration in seconds or None if not complete.
        """
        if self.drain_started_at and self.drain_completed_at:
            return (self.drain_completed_at - self.drain_started_at).total_seconds()
        return None


class NodeMaintenanceRequest(
    KubernetesResource[NodeMaintenanceRequestSpec, NodeMaintenanceRequestStatus]
):
    """NodeMaintenanceRequest custom resource.

    Manages node maintenance operations in a controlled, auditable manner,
    including workload draining and Ceph health monitoring.

    Example:
        nmr = NodeMaintenanceRequest(
            metadata=KubernetesMetadata(
                name="maintain-compute-01",
                namespace="default",
            ),
            spec=NodeMaintenanceRequestSpec(
                node_name="compute-01",
                reason=MaintenanceReason.DISK_REPLACEMENT,
                description="Replacing failed SSD in slot 3",
                drain_strategy=DrainStrategy.LIVE_MIGRATE,
                crq_number="CRQ123456789",
            ),
        )
    """

    API_VERSION: ClassVar[str] = "lcm.mirantis.com/v1alpha1"
    KIND: ClassVar[str] = "NodeMaintenanceRequest"
    PLURAL: ClassVar[str] = "nodemaintenancerequests"
    GROUP: ClassVar[str] = "lcm.mirantis.com"

    api_version: str = Field(default="lcm.mirantis.com/v1alpha1", alias="apiVersion")
    kind: str = Field(default="NodeMaintenanceRequest")
    spec: NodeMaintenanceRequestSpec
    status: NodeMaintenanceRequestStatus | None = None

    @property
    def is_complete(self) -> bool:
        """Check if maintenance is complete.

        Returns:
            True if in a terminal state.
        """
        return self.status is not None and self.status.is_complete

    @property
    def is_successful(self) -> bool:
        """Check if maintenance completed successfully.

        Returns:
            True if completed without error.
        """
        return self.status is not None and self.status.is_successful

    @property
    def is_drained(self) -> bool:
        """Check if node has been drained.

        Returns:
            True if node is drained.
        """
        return self.status is not None and self.status.is_drained

    @classmethod
    def create_for_disk_replacement(
        cls,
        node_name: str,
        namespace: str,
        description: str | None = None,
        crq_number: str | None = None,
    ) -> NodeMaintenanceRequest:
        """Create a maintenance request for disk replacement.

        Args:
            node_name: Node name.
            namespace: Kubernetes namespace.
            description: Description of the work.
            crq_number: Change request number.

        Returns:
            NodeMaintenanceRequest for disk replacement.
        """
        return cls(
            metadata=KubernetesMetadata(
                name=f"disk-replace-{node_name}",
                namespace=namespace,
            ),
            spec=NodeMaintenanceRequestSpec(
                node_name=node_name,
                reason=MaintenanceReason.DISK_REPLACEMENT,
                description=description,
                drain_strategy=DrainStrategy.LIVE_MIGRATE,
                crq_number=crq_number,
            ),
        )

    @classmethod
    def create_for_firmware_update(
        cls,
        node_name: str,
        namespace: str,
        description: str | None = None,
        crq_number: str | None = None,
    ) -> NodeMaintenanceRequest:
        """Create a maintenance request for firmware update.

        Args:
            node_name: Node name.
            namespace: Kubernetes namespace.
            description: Description of the work.
            crq_number: Change request number.

        Returns:
            NodeMaintenanceRequest for firmware update.
        """
        return cls(
            metadata=KubernetesMetadata(
                name=f"firmware-update-{node_name}",
                namespace=namespace,
            ),
            spec=NodeMaintenanceRequestSpec(
                node_name=node_name,
                reason=MaintenanceReason.FIRMWARE_UPDATE,
                description=description,
                drain_strategy=DrainStrategy.GRACEFUL,
                crq_number=crq_number,
            ),
        )

    @classmethod
    def create_for_decommission(
        cls,
        node_name: str,
        namespace: str,
        description: str | None = None,
        crq_number: str | None = None,
    ) -> NodeMaintenanceRequest:
        """Create a maintenance request for node decommission.

        Args:
            node_name: Node name.
            namespace: Kubernetes namespace.
            description: Description of the work.
            crq_number: Change request number.

        Returns:
            NodeMaintenanceRequest for decommission.
        """
        return cls(
            metadata=KubernetesMetadata(
                name=f"decommission-{node_name}",
                namespace=namespace,
            ),
            spec=NodeMaintenanceRequestSpec(
                node_name=node_name,
                reason=MaintenanceReason.DECOMMISSION,
                description=description,
                drain_strategy=DrainStrategy.LIVE_MIGRATE,
                auto_uncordon=False,  # Don't uncordon after decommission
                crq_number=crq_number,
            ),
        )

    @classmethod
    def from_kubernetes(cls, data: dict[str, Any]) -> NodeMaintenanceRequest:
        """Create from Kubernetes API response.

        Args:
            data: Resource dictionary from Kubernetes API.

        Returns:
            NodeMaintenanceRequest instance.
        """
        spec_data = data.get("spec", {})

        reason = MaintenanceReason.SCHEDULED_MAINTENANCE
        if "reason" in spec_data:
            with contextlib.suppress(ValueError):
                reason = MaintenanceReason(spec_data["reason"])

        drain_strategy = DrainStrategy.GRACEFUL
        if "drainStrategy" in spec_data:
            with contextlib.suppress(ValueError):
                drain_strategy = DrainStrategy(spec_data["drainStrategy"])

        spec = NodeMaintenanceRequestSpec(
            node_name=spec_data.get("nodeName", ""),
            reason=reason,
            description=spec_data.get("description"),
            drain_strategy=drain_strategy,
            grace_period_seconds=spec_data.get("gracePeriodSeconds", 300),
            skip_drain=spec_data.get("skipDrain", False),
            skip_ceph_checks=spec_data.get("skipCephChecks", False),
            expected_duration_minutes=spec_data.get("expectedDurationMinutes"),
            crq_number=spec_data.get("crqNumber"),
            auto_uncordon=spec_data.get("autoUncordon", False),
        )

        status = None
        if "status" in data:
            status_data = data["status"]
            phase = None
            if "phase" in status_data:
                with contextlib.suppress(ValueError):
                    phase = MaintenancePhase(status_data["phase"])

            evicted_pods = []
            for pod_data in status_data.get("evictedPods", []):
                evicted_pods.append(
                    EvictedPod(
                        name=pod_data.get("name", ""),
                        namespace=pod_data.get("namespace", ""),
                        kind=pod_data.get("kind"),
                        owner_name=pod_data.get("ownerName"),
                        evicted_at=pod_data.get("evictedAt"),
                        reason=pod_data.get("reason"),
                    )
                )

            status = NodeMaintenanceRequestStatus(
                phase=phase,
                started_at=status_data.get("startedAt"),
                completed_at=status_data.get("completedAt"),
                drain_started_at=status_data.get("drainStartedAt"),
                drain_completed_at=status_data.get("drainCompletedAt"),
                evicted_pods=evicted_pods,
                total_evicted=status_data.get("totalEvicted", 0),
                ceph_health_before=status_data.get("cephHealthBefore"),
                ceph_health_after=status_data.get("cephHealthAfter"),
                conditions=status_data.get("conditions", []),
                message=status_data.get("message"),
                error_message=status_data.get("errorMessage"),
                last_error_time=status_data.get("lastErrorTime"),
            )

        return cls(
            metadata=KubernetesMetadata.from_kubernetes(data["metadata"]),
            spec=spec,
            status=status,
        )
