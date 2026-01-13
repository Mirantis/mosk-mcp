"""ClusterMaintenanceRequest CRD models for MOSK cluster maintenance.

This module provides Pydantic models for the ClusterMaintenanceRequest custom resource,
which manages cluster-level maintenance mode in MOSK clusters.

IMPORTANT: ClusterMaintenanceRequest must be created BEFORE NodeMaintenanceRequest.
To enable maintenance mode on a machine, first enable maintenance mode on the cluster.
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


class ClusterMaintenancePhase(str, Enum):
    """ClusterMaintenanceRequest lifecycle phases."""

    PENDING = "Pending"
    ACTIVE = "Active"
    MAINTENANCE = "Maintenance"
    EXITING = "Exiting"
    COMPLETED = "Completed"
    FAILED = "Failed"


class ClusterMaintenanceReason(str, Enum):
    """Reasons for cluster maintenance."""

    CLUSTER_UPGRADE = "ClusterUpgrade"
    SECURITY_UPDATE = "SecurityUpdate"
    HARDWARE_MAINTENANCE = "HardwareMaintenance"
    NETWORK_RECONFIGURATION = "NetworkReconfiguration"
    STORAGE_MAINTENANCE = "StorageMaintenance"
    DISASTER_RECOVERY_TEST = "DisasterRecoveryTest"
    SCHEDULED_MAINTENANCE = "ScheduledMaintenance"
    OTHER = "Other"


class ClusterMaintenanceRequestSpec(BaseModel):
    """Specification for ClusterMaintenanceRequest resource.

    Attributes:
        cluster_name: Name of the cluster to maintain.
        reason: Reason for maintenance.
        description: Human-readable description.
        scheduled_start: Scheduled start time.
        scheduled_end: Scheduled end time.
        auto_approve_nodes: Auto-approve node maintenance requests.
        max_unavailable_nodes: Maximum unavailable nodes during maintenance.
        disable_alerting: Disable alerting during maintenance.
        crq_number: Change request number for audit trail.
    """

    model_config = ConfigDict(populate_by_name=True)

    cluster_name: str = Field(
        ...,
        alias="clusterName",
        description="Name of the cluster to maintain",
    )
    reason: ClusterMaintenanceReason = Field(
        default=ClusterMaintenanceReason.SCHEDULED_MAINTENANCE,
        description="Reason for maintenance",
    )
    description: str | None = Field(
        None,
        description="Human-readable description",
    )
    scheduled_start: datetime | None = Field(
        None,
        alias="scheduledStart",
        description="Scheduled start time",
    )
    scheduled_end: datetime | None = Field(
        None,
        alias="scheduledEnd",
        description="Scheduled end time",
    )
    auto_approve_nodes: bool = Field(
        default=False,
        alias="autoApproveNodes",
        description="Auto-approve node maintenance requests",
    )
    max_unavailable_nodes: int = Field(
        default=1,
        alias="maxUnavailableNodes",
        description="Maximum unavailable nodes during maintenance",
    )
    disable_alerting: bool = Field(
        default=False,
        alias="disableAlerting",
        description="Disable alerting during maintenance",
    )
    crq_number: str | None = Field(
        None,
        alias="crqNumber",
        description="Change request number",
    )

    def to_kubernetes(self) -> dict[str, Any]:
        """Convert to Kubernetes API format."""
        result: dict[str, Any] = {
            "clusterName": self.cluster_name,
            "reason": self.reason.value,
            "autoApproveNodes": self.auto_approve_nodes,
            "maxUnavailableNodes": self.max_unavailable_nodes,
            "disableAlerting": self.disable_alerting,
        }
        if self.description is not None:
            result["description"] = self.description
        if self.scheduled_start is not None:
            result["scheduledStart"] = self.scheduled_start.isoformat()
        if self.scheduled_end is not None:
            result["scheduledEnd"] = self.scheduled_end.isoformat()
        if self.crq_number is not None:
            result["crqNumber"] = self.crq_number
        return result


class NodeMaintenanceStatus(BaseModel):
    """Status of a node within cluster maintenance.

    Attributes:
        node_name: Node name.
        phase: Current maintenance phase.
        started_at: When maintenance started.
        completed_at: When maintenance completed.
    """

    model_config = ConfigDict(populate_by_name=True)

    node_name: str = Field(..., alias="nodeName", description="Node name")
    phase: str = Field(default="Pending", description="Current phase")
    started_at: datetime | None = Field(None, alias="startedAt", description="Start time")
    completed_at: datetime | None = Field(None, alias="completedAt", description="Completion time")


class ClusterMaintenanceRequestStatus(BaseModel):
    """Status of ClusterMaintenanceRequest resource.

    Attributes:
        phase: Current maintenance phase.
        started_at: When maintenance started.
        completed_at: When maintenance completed.
        nodes_in_maintenance: Number of nodes in maintenance.
        nodes_completed: Number of nodes that completed maintenance.
        nodes_pending: Number of nodes pending maintenance.
        node_statuses: Status of each node.
        conditions: Status conditions.
        message: Status message.
        error_message: Error message if failed.
    """

    model_config = ConfigDict(populate_by_name=True)

    phase: ClusterMaintenancePhase | None = Field(None, description="Current phase")
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
    nodes_in_maintenance: int = Field(
        default=0,
        alias="nodesInMaintenance",
        description="Nodes in maintenance",
    )
    nodes_completed: int = Field(
        default=0,
        alias="nodesCompleted",
        description="Nodes completed",
    )
    nodes_pending: int = Field(
        default=0,
        alias="nodesPending",
        description="Nodes pending",
    )
    node_statuses: list[NodeMaintenanceStatus] = Field(
        default_factory=list,
        alias="nodeStatuses",
        description="Status of each node",
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

    @property
    def is_complete(self) -> bool:
        """Check if maintenance is complete."""
        return self.phase in [
            ClusterMaintenancePhase.COMPLETED,
            ClusterMaintenancePhase.FAILED,
        ]

    @property
    def is_active(self) -> bool:
        """Check if maintenance is active."""
        return self.phase in [
            ClusterMaintenancePhase.ACTIVE,
            ClusterMaintenancePhase.MAINTENANCE,
        ]


class ClusterMaintenanceRequest(
    KubernetesResource[ClusterMaintenanceRequestSpec, ClusterMaintenanceRequestStatus]
):
    """ClusterMaintenanceRequest custom resource.

    Manages cluster-level maintenance mode. Must be created before
    NodeMaintenanceRequest to enable maintenance on individual nodes.

    Example:
        cmr = ClusterMaintenanceRequest(
            metadata=KubernetesMetadata(
                name="cluster-maint-2024-01",
                namespace="default",
            ),
            spec=ClusterMaintenanceRequestSpec(
                cluster_name="mosk-cluster",
                reason=ClusterMaintenanceReason.SCHEDULED_MAINTENANCE,
                description="Monthly maintenance window",
                crq_number="CRQ123456789",
            ),
        )
    """

    API_VERSION: ClassVar[str] = "lcm.mirantis.com/v1alpha1"
    KIND: ClassVar[str] = "ClusterMaintenanceRequest"
    PLURAL: ClassVar[str] = "clustermaintenancerequests"
    GROUP: ClassVar[str] = "lcm.mirantis.com"

    api_version: str = Field(default="lcm.mirantis.com/v1alpha1", alias="apiVersion")
    kind: str = Field(default="ClusterMaintenanceRequest")
    spec: ClusterMaintenanceRequestSpec
    status: ClusterMaintenanceRequestStatus | None = None

    @property
    def is_complete(self) -> bool:
        """Check if maintenance is complete."""
        return self.status is not None and self.status.is_complete

    @property
    def is_active(self) -> bool:
        """Check if maintenance is active."""
        return self.status is not None and self.status.is_active

    @classmethod
    def create_for_upgrade(
        cls,
        cluster_name: str,
        namespace: str,
        description: str | None = None,
        crq_number: str | None = None,
    ) -> ClusterMaintenanceRequest:
        """Create a maintenance request for cluster upgrade.

        Args:
            cluster_name: Cluster name.
            namespace: Kubernetes namespace.
            description: Description of the work.
            crq_number: Change request number.

        Returns:
            ClusterMaintenanceRequest for upgrade.
        """
        return cls(
            metadata=KubernetesMetadata(
                name=f"upgrade-{cluster_name}",
                namespace=namespace,
            ),
            spec=ClusterMaintenanceRequestSpec(
                cluster_name=cluster_name,
                reason=ClusterMaintenanceReason.CLUSTER_UPGRADE,
                description=description,
                crq_number=crq_number,
            ),
        )

    @classmethod
    def from_kubernetes(cls, data: dict[str, Any]) -> ClusterMaintenanceRequest:
        """Create from Kubernetes API response.

        Args:
            data: Resource dictionary from Kubernetes API.

        Returns:
            ClusterMaintenanceRequest instance.
        """
        spec_data = data.get("spec", {})

        reason = ClusterMaintenanceReason.SCHEDULED_MAINTENANCE
        if "reason" in spec_data:
            with contextlib.suppress(ValueError):
                reason = ClusterMaintenanceReason(spec_data["reason"])

        spec = ClusterMaintenanceRequestSpec(
            cluster_name=spec_data.get("clusterName", ""),
            reason=reason,
            description=spec_data.get("description"),
            scheduled_start=spec_data.get("scheduledStart"),
            scheduled_end=spec_data.get("scheduledEnd"),
            auto_approve_nodes=spec_data.get("autoApproveNodes", False),
            max_unavailable_nodes=spec_data.get("maxUnavailableNodes", 1),
            disable_alerting=spec_data.get("disableAlerting", False),
            crq_number=spec_data.get("crqNumber"),
        )

        status = None
        if "status" in data:
            status_data = data["status"]
            phase = None
            if "phase" in status_data:
                with contextlib.suppress(ValueError):
                    phase = ClusterMaintenancePhase(status_data["phase"])

            node_statuses = []
            for ns_data in status_data.get("nodeStatuses", []):
                node_statuses.append(
                    NodeMaintenanceStatus(
                        node_name=ns_data.get("nodeName", ""),
                        phase=ns_data.get("phase", "Pending"),
                        started_at=ns_data.get("startedAt"),
                        completed_at=ns_data.get("completedAt"),
                    )
                )

            status = ClusterMaintenanceRequestStatus(
                phase=phase,
                started_at=status_data.get("startedAt"),
                completed_at=status_data.get("completedAt"),
                nodes_in_maintenance=status_data.get("nodesInMaintenance", 0),
                nodes_completed=status_data.get("nodesCompleted", 0),
                nodes_pending=status_data.get("nodesPending", 0),
                node_statuses=node_statuses,
                conditions=status_data.get("conditions", []),
                message=status_data.get("message"),
                error_message=status_data.get("errorMessage"),
            )

        return cls(
            metadata=KubernetesMetadata.from_kubernetes(data["metadata"]),
            spec=spec,
            status=status,
        )
