"""GracefulRebootRequest CRD models for MOSK orchestrated reboots.

This module provides Pydantic models for the GracefulRebootRequest custom resource,
which manages orchestrated cluster reboots with proper workload handling.
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


class GracefulRebootPhase(str, Enum):
    """GracefulRebootRequest lifecycle phases."""

    PENDING = "Pending"
    VALIDATING = "Validating"
    DRAINING = "Draining"
    REBOOTING = "Rebooting"
    WAITING_FOR_NODE = "WaitingForNode"
    UNCORDONING = "Uncordoning"
    COMPLETED = "Completed"
    FAILED = "Failed"
    CANCELLED = "Cancelled"


class RebootReason(str, Enum):
    """Reasons for node reboot."""

    KERNEL_UPDATE = "KernelUpdate"
    SECURITY_PATCH = "SecurityPatch"
    DRIVER_UPDATE = "DriverUpdate"
    FIRMWARE_UPDATE = "FirmwareUpdate"
    MEMORY_ISSUE = "MemoryIssue"
    SCHEDULED_REBOOT = "ScheduledReboot"
    OTHER = "Other"


class RebootStrategy(str, Enum):
    """Strategy for handling workloads during reboot."""

    GRACEFUL = "Graceful"
    LIVE_MIGRATE = "LiveMigrate"
    FORCE = "Force"


class GracefulRebootRequestSpec(BaseModel):
    """Specification for GracefulRebootRequest resource.

    Attributes:
        node_name: Name of the node to reboot.
        reason: Reason for reboot.
        description: Human-readable description.
        reboot_strategy: Strategy for handling workloads.
        drain_timeout_seconds: Timeout for drain operation.
        reboot_timeout_seconds: Timeout for reboot operation.
        grace_period_seconds: Grace period for pod termination.
        skip_ceph_checks: Skip Ceph health checks.
        force_reboot: Force reboot even if drain fails.
        crq_number: Change request number for audit trail.
    """

    model_config = ConfigDict(populate_by_name=True)

    node_name: str = Field(
        ...,
        alias="nodeName",
        description="Name of the node to reboot",
    )
    reason: RebootReason = Field(
        default=RebootReason.SCHEDULED_REBOOT,
        description="Reason for reboot",
    )
    description: str | None = Field(
        None,
        description="Human-readable description",
    )
    reboot_strategy: RebootStrategy = Field(
        default=RebootStrategy.GRACEFUL,
        alias="rebootStrategy",
        description="Strategy for handling workloads",
    )
    drain_timeout_seconds: int = Field(
        default=600,
        alias="drainTimeoutSeconds",
        description="Timeout for drain operation",
    )
    reboot_timeout_seconds: int = Field(
        default=300,
        alias="rebootTimeoutSeconds",
        description="Timeout for reboot operation",
    )
    grace_period_seconds: int = Field(
        default=300,
        alias="gracePeriodSeconds",
        description="Grace period for pod termination",
    )
    skip_ceph_checks: bool = Field(
        default=False,
        alias="skipCephChecks",
        description="Skip Ceph health checks",
    )
    force_reboot: bool = Field(
        default=False,
        alias="forceReboot",
        description="Force reboot even if drain fails",
    )
    crq_number: str | None = Field(
        None,
        alias="crqNumber",
        description="Change request number",
    )

    def to_kubernetes(self) -> dict[str, Any]:
        """Convert to Kubernetes API format."""
        result: dict[str, Any] = {
            "nodeName": self.node_name,
            "reason": self.reason.value,
            "rebootStrategy": self.reboot_strategy.value,
            "drainTimeoutSeconds": self.drain_timeout_seconds,
            "rebootTimeoutSeconds": self.reboot_timeout_seconds,
            "gracePeriodSeconds": self.grace_period_seconds,
            "skipCephChecks": self.skip_ceph_checks,
            "forceReboot": self.force_reboot,
        }
        if self.description is not None:
            result["description"] = self.description
        if self.crq_number is not None:
            result["crqNumber"] = self.crq_number
        return result


class GracefulRebootRequestStatus(BaseModel):
    """Status of GracefulRebootRequest resource.

    Attributes:
        phase: Current reboot phase.
        started_at: When reboot process started.
        completed_at: When reboot process completed.
        drain_started_at: When drain phase started.
        drain_completed_at: When drain phase completed.
        reboot_started_at: When actual reboot started.
        node_ready_at: When node became ready after reboot.
        pods_evicted: Number of pods evicted.
        vms_migrated: Number of VMs migrated.
        conditions: Status conditions.
        message: Status message.
        error_message: Error message if failed.
        uptime_before: Node uptime before reboot.
        uptime_after: Node uptime after reboot.
    """

    model_config = ConfigDict(populate_by_name=True)

    phase: GracefulRebootPhase | None = Field(None, description="Current phase")
    started_at: datetime | None = Field(
        None,
        alias="startedAt",
        description="When reboot process started",
    )
    completed_at: datetime | None = Field(
        None,
        alias="completedAt",
        description="When reboot process completed",
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
    reboot_started_at: datetime | None = Field(
        None,
        alias="rebootStartedAt",
        description="When actual reboot started",
    )
    node_ready_at: datetime | None = Field(
        None,
        alias="nodeReadyAt",
        description="When node became ready",
    )
    pods_evicted: int = Field(
        default=0,
        alias="podsEvicted",
        description="Number of pods evicted",
    )
    vms_migrated: int = Field(
        default=0,
        alias="vmsMigrated",
        description="Number of VMs migrated",
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
    uptime_before: str | None = Field(
        None,
        alias="uptimeBefore",
        description="Node uptime before reboot",
    )
    uptime_after: str | None = Field(
        None,
        alias="uptimeAfter",
        description="Node uptime after reboot",
    )

    @property
    def is_complete(self) -> bool:
        """Check if reboot is complete."""
        return self.phase in [
            GracefulRebootPhase.COMPLETED,
            GracefulRebootPhase.FAILED,
            GracefulRebootPhase.CANCELLED,
        ]

    @property
    def is_successful(self) -> bool:
        """Check if reboot completed successfully."""
        return self.phase == GracefulRebootPhase.COMPLETED

    @property
    def is_rebooting(self) -> bool:
        """Check if node is currently rebooting."""
        return self.phase in [
            GracefulRebootPhase.REBOOTING,
            GracefulRebootPhase.WAITING_FOR_NODE,
        ]


class GracefulRebootRequest(
    KubernetesResource[GracefulRebootRequestSpec, GracefulRebootRequestStatus]
):
    """GracefulRebootRequest custom resource.

    Manages orchestrated node reboots with proper workload handling,
    including draining, live migration, and health checks.

    Example:
        grr = GracefulRebootRequest(
            metadata=KubernetesMetadata(
                name="reboot-compute-01",
                namespace="default",
            ),
            spec=GracefulRebootRequestSpec(
                node_name="compute-01",
                reason=RebootReason.KERNEL_UPDATE,
                reboot_strategy=RebootStrategy.LIVE_MIGRATE,
                crq_number="CRQ123456789",
            ),
        )
    """

    API_VERSION: ClassVar[str] = "kaas.mirantis.com/v1alpha1"
    KIND: ClassVar[str] = "GracefulRebootRequest"
    PLURAL: ClassVar[str] = "gracefulrebootrequests"
    GROUP: ClassVar[str] = "kaas.mirantis.com"

    api_version: str = Field(default="kaas.mirantis.com/v1alpha1", alias="apiVersion")
    kind: str = Field(default="GracefulRebootRequest")
    spec: GracefulRebootRequestSpec
    status: GracefulRebootRequestStatus | None = None

    @property
    def is_complete(self) -> bool:
        """Check if reboot is complete."""
        return self.status is not None and self.status.is_complete

    @property
    def is_successful(self) -> bool:
        """Check if reboot completed successfully."""
        return self.status is not None and self.status.is_successful

    @classmethod
    def create_for_kernel_update(
        cls,
        node_name: str,
        namespace: str,
        description: str | None = None,
        crq_number: str | None = None,
    ) -> GracefulRebootRequest:
        """Create a reboot request for kernel update.

        Args:
            node_name: Node name.
            namespace: Kubernetes namespace.
            description: Description of the work.
            crq_number: Change request number.

        Returns:
            GracefulRebootRequest for kernel update.
        """
        return cls(
            metadata=KubernetesMetadata(
                name=f"kernel-update-{node_name}",
                namespace=namespace,
            ),
            spec=GracefulRebootRequestSpec(
                node_name=node_name,
                reason=RebootReason.KERNEL_UPDATE,
                description=description,
                reboot_strategy=RebootStrategy.LIVE_MIGRATE,
                crq_number=crq_number,
            ),
        )

    @classmethod
    def create_for_security_patch(
        cls,
        node_name: str,
        namespace: str,
        description: str | None = None,
        crq_number: str | None = None,
    ) -> GracefulRebootRequest:
        """Create a reboot request for security patch.

        Args:
            node_name: Node name.
            namespace: Kubernetes namespace.
            description: Description of the work.
            crq_number: Change request number.

        Returns:
            GracefulRebootRequest for security patch.
        """
        return cls(
            metadata=KubernetesMetadata(
                name=f"security-patch-{node_name}",
                namespace=namespace,
            ),
            spec=GracefulRebootRequestSpec(
                node_name=node_name,
                reason=RebootReason.SECURITY_PATCH,
                description=description,
                reboot_strategy=RebootStrategy.LIVE_MIGRATE,
                crq_number=crq_number,
            ),
        )

    @classmethod
    def from_kubernetes(cls, data: dict[str, Any]) -> GracefulRebootRequest:
        """Create from Kubernetes API response.

        Args:
            data: Resource dictionary from Kubernetes API.

        Returns:
            GracefulRebootRequest instance.
        """
        spec_data = data.get("spec", {})

        reason = RebootReason.SCHEDULED_REBOOT
        if "reason" in spec_data:
            with contextlib.suppress(ValueError):
                reason = RebootReason(spec_data["reason"])

        reboot_strategy = RebootStrategy.GRACEFUL
        if "rebootStrategy" in spec_data:
            with contextlib.suppress(ValueError):
                reboot_strategy = RebootStrategy(spec_data["rebootStrategy"])

        spec = GracefulRebootRequestSpec(
            node_name=spec_data.get("nodeName", ""),
            reason=reason,
            description=spec_data.get("description"),
            reboot_strategy=reboot_strategy,
            drain_timeout_seconds=spec_data.get("drainTimeoutSeconds", 600),
            reboot_timeout_seconds=spec_data.get("rebootTimeoutSeconds", 300),
            grace_period_seconds=spec_data.get("gracePeriodSeconds", 300),
            skip_ceph_checks=spec_data.get("skipCephChecks", False),
            force_reboot=spec_data.get("forceReboot", False),
            crq_number=spec_data.get("crqNumber"),
        )

        status = None
        if "status" in data:
            status_data = data["status"]
            phase = None
            if "phase" in status_data:
                with contextlib.suppress(ValueError):
                    phase = GracefulRebootPhase(status_data["phase"])

            status = GracefulRebootRequestStatus(
                phase=phase,
                started_at=status_data.get("startedAt"),
                completed_at=status_data.get("completedAt"),
                drain_started_at=status_data.get("drainStartedAt"),
                drain_completed_at=status_data.get("drainCompletedAt"),
                reboot_started_at=status_data.get("rebootStartedAt"),
                node_ready_at=status_data.get("nodeReadyAt"),
                pods_evicted=status_data.get("podsEvicted", 0),
                vms_migrated=status_data.get("vmsMigrated", 0),
                conditions=status_data.get("conditions", []),
                message=status_data.get("message"),
                error_message=status_data.get("errorMessage"),
                uptime_before=status_data.get("uptimeBefore"),
                uptime_after=status_data.get("uptimeAfter"),
            )

        return cls(
            metadata=KubernetesMetadata.from_kubernetes(data["metadata"]),
            spec=spec,
            status=status,
        )
