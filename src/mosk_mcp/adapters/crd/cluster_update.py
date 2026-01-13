"""ClusterUpdatePlan CRD models for MOSK granular updates.

This module provides Pydantic models for the ClusterUpdatePlan custom resource,
which manages granular MOSK cluster updates with fine-grained control.
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


class ClusterUpdatePhase(str, Enum):
    """ClusterUpdatePlan lifecycle phases."""

    PENDING = "Pending"
    VALIDATING = "Validating"
    APPROVED = "Approved"
    IN_PROGRESS = "InProgress"
    PAUSED = "Paused"
    ROLLING_BACK = "RollingBack"
    COMPLETED = "Completed"
    FAILED = "Failed"
    CANCELLED = "Cancelled"


class UpdateType(str, Enum):
    """Types of cluster updates."""

    FULL_UPDATE = "FullUpdate"
    OPENSTACK_ONLY = "OpenStackOnly"
    KUBERNETES_ONLY = "KubernetesOnly"
    CEPH_ONLY = "CephOnly"
    SECURITY_PATCH = "SecurityPatch"
    COMPONENT_UPDATE = "ComponentUpdate"
    CONFIGURATION_CHANGE = "ConfigurationChange"


class UpdateStrategy(str, Enum):
    """Strategy for applying updates."""

    ROLLING = "Rolling"
    BLUE_GREEN = "BlueGreen"
    CANARY = "Canary"
    ALL_AT_ONCE = "AllAtOnce"


class ComponentUpdate(BaseModel):
    """Specification for updating a specific component.

    Attributes:
        name: Component name.
        target_version: Target version to update to.
        current_version: Current version.
        enabled: Whether this component update is enabled.
    """

    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(..., description="Component name")
    target_version: str = Field(
        ...,
        alias="targetVersion",
        description="Target version",
    )
    current_version: str | None = Field(
        None,
        alias="currentVersion",
        description="Current version",
    )
    enabled: bool = Field(default=True, description="Whether update is enabled")


class ClusterUpdatePlanSpec(BaseModel):
    """Specification for ClusterUpdatePlan resource.

    Attributes:
        cluster_name: Name of the cluster to update.
        target_release: Target release version.
        update_type: Type of update.
        update_strategy: Strategy for applying updates.
        components: List of component updates.
        max_unavailable_nodes: Maximum unavailable nodes during update.
        pause_between_nodes: Pause duration between node updates.
        skip_preflight_checks: Skip preflight validation.
        auto_approve: Auto-approve update stages.
        rollback_on_failure: Automatically rollback on failure.
        maintenance_window_start: Start of maintenance window.
        maintenance_window_end: End of maintenance window.
        crq_number: Change request number for audit trail.
        description: Human-readable description.
    """

    model_config = ConfigDict(populate_by_name=True)

    cluster_name: str = Field(
        ...,
        alias="clusterName",
        description="Name of the cluster to update",
    )
    target_release: str = Field(
        ...,
        alias="targetRelease",
        description="Target release version",
    )
    update_type: UpdateType = Field(
        default=UpdateType.FULL_UPDATE,
        alias="updateType",
        description="Type of update",
    )
    update_strategy: UpdateStrategy = Field(
        default=UpdateStrategy.ROLLING,
        alias="updateStrategy",
        description="Strategy for applying updates",
    )
    components: list[ComponentUpdate] = Field(
        default_factory=list,
        description="Component updates",
    )
    max_unavailable_nodes: int = Field(
        default=1,
        alias="maxUnavailableNodes",
        description="Maximum unavailable nodes",
    )
    pause_between_nodes: int = Field(
        default=0,
        alias="pauseBetweenNodes",
        description="Pause duration in seconds between node updates",
    )
    skip_preflight_checks: bool = Field(
        default=False,
        alias="skipPreflightChecks",
        description="Skip preflight validation",
    )
    auto_approve: bool = Field(
        default=False,
        alias="autoApprove",
        description="Auto-approve update stages",
    )
    rollback_on_failure: bool = Field(
        default=True,
        alias="rollbackOnFailure",
        description="Rollback on failure",
    )
    maintenance_window_start: datetime | None = Field(
        None,
        alias="maintenanceWindowStart",
        description="Start of maintenance window",
    )
    maintenance_window_end: datetime | None = Field(
        None,
        alias="maintenanceWindowEnd",
        description="End of maintenance window",
    )
    crq_number: str | None = Field(
        None,
        alias="crqNumber",
        description="Change request number",
    )
    description: str | None = Field(
        None,
        description="Human-readable description",
    )

    def to_kubernetes(self) -> dict[str, Any]:
        """Convert to Kubernetes API format."""
        result: dict[str, Any] = {
            "clusterName": self.cluster_name,
            "targetRelease": self.target_release,
            "updateType": self.update_type.value,
            "updateStrategy": self.update_strategy.value,
            "maxUnavailableNodes": self.max_unavailable_nodes,
            "pauseBetweenNodes": self.pause_between_nodes,
            "skipPreflightChecks": self.skip_preflight_checks,
            "autoApprove": self.auto_approve,
            "rollbackOnFailure": self.rollback_on_failure,
        }
        if self.components:
            result["components"] = [
                {
                    "name": c.name,
                    "targetVersion": c.target_version,
                    **({"currentVersion": c.current_version} if c.current_version else {}),
                    "enabled": c.enabled,
                }
                for c in self.components
            ]
        if self.maintenance_window_start is not None:
            result["maintenanceWindowStart"] = self.maintenance_window_start.isoformat()
        if self.maintenance_window_end is not None:
            result["maintenanceWindowEnd"] = self.maintenance_window_end.isoformat()
        if self.crq_number is not None:
            result["crqNumber"] = self.crq_number
        if self.description is not None:
            result["description"] = self.description
        return result


class ComponentStatus(BaseModel):
    """Status of a component update.

    Attributes:
        name: Component name.
        status: Current status.
        current_version: Current version.
        target_version: Target version.
        message: Status message.
    """

    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(..., description="Component name")
    status: str = Field(default="Pending", description="Current status")
    current_version: str | None = Field(None, alias="currentVersion", description="Current version")
    target_version: str | None = Field(None, alias="targetVersion", description="Target version")
    message: str | None = Field(None, description="Status message")


class NodeUpdateStatus(BaseModel):
    """Status of a node update.

    Attributes:
        node_name: Node name.
        phase: Current phase.
        started_at: When update started.
        completed_at: When update completed.
        components_updated: Number of components updated.
        message: Status message.
    """

    model_config = ConfigDict(populate_by_name=True)

    node_name: str = Field(..., alias="nodeName", description="Node name")
    phase: str = Field(default="Pending", description="Current phase")
    started_at: datetime | None = Field(None, alias="startedAt", description="Start time")
    completed_at: datetime | None = Field(None, alias="completedAt", description="Completion time")
    components_updated: int = Field(
        default=0, alias="componentsUpdated", description="Components updated"
    )
    message: str | None = Field(None, description="Status message")


class ClusterUpdatePlanStatus(BaseModel):
    """Status of ClusterUpdatePlan resource.

    Attributes:
        phase: Current update phase.
        started_at: When update started.
        completed_at: When update completed.
        current_release: Current release version.
        target_release: Target release version.
        progress_percent: Update progress percentage.
        nodes_updated: Number of nodes updated.
        nodes_total: Total number of nodes.
        nodes_pending: Number of nodes pending.
        node_statuses: Status of each node.
        component_statuses: Status of each component.
        preflight_results: Results of preflight checks.
        conditions: Status conditions.
        message: Status message.
        error_message: Error message if failed.
        rollback_available: Whether rollback is available.
    """

    model_config = ConfigDict(populate_by_name=True)

    phase: ClusterUpdatePhase | None = Field(None, description="Current phase")
    started_at: datetime | None = Field(
        None,
        alias="startedAt",
        description="When update started",
    )
    completed_at: datetime | None = Field(
        None,
        alias="completedAt",
        description="When update completed",
    )
    current_release: str | None = Field(
        None,
        alias="currentRelease",
        description="Current release",
    )
    target_release: str | None = Field(
        None,
        alias="targetRelease",
        description="Target release",
    )
    progress_percent: int = Field(
        default=0,
        alias="progressPercent",
        description="Progress percentage",
    )
    nodes_updated: int = Field(
        default=0,
        alias="nodesUpdated",
        description="Nodes updated",
    )
    nodes_total: int = Field(
        default=0,
        alias="nodesTotal",
        description="Total nodes",
    )
    nodes_pending: int = Field(
        default=0,
        alias="nodesPending",
        description="Nodes pending",
    )
    node_statuses: list[NodeUpdateStatus] = Field(
        default_factory=list,
        alias="nodeStatuses",
        description="Status of each node",
    )
    component_statuses: list[ComponentStatus] = Field(
        default_factory=list,
        alias="componentStatuses",
        description="Status of each component",
    )
    preflight_results: dict[str, Any] | None = Field(
        None,
        alias="preflightResults",
        description="Preflight check results",
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
    rollback_available: bool = Field(
        default=False,
        alias="rollbackAvailable",
        description="Whether rollback is available",
    )

    @property
    def is_complete(self) -> bool:
        """Check if update is complete."""
        return self.phase in [
            ClusterUpdatePhase.COMPLETED,
            ClusterUpdatePhase.FAILED,
            ClusterUpdatePhase.CANCELLED,
        ]

    @property
    def is_successful(self) -> bool:
        """Check if update completed successfully."""
        return self.phase == ClusterUpdatePhase.COMPLETED

    @property
    def is_in_progress(self) -> bool:
        """Check if update is in progress."""
        return self.phase in [
            ClusterUpdatePhase.VALIDATING,
            ClusterUpdatePhase.IN_PROGRESS,
        ]


class ClusterUpdatePlan(KubernetesResource[ClusterUpdatePlanSpec, ClusterUpdatePlanStatus]):
    """ClusterUpdatePlan custom resource.

    Manages granular MOSK cluster updates with fine-grained control
    over components, nodes, and update strategies.

    Example:
        plan = ClusterUpdatePlan(
            metadata=KubernetesMetadata(
                name="update-to-25.2",
                namespace="default",
            ),
            spec=ClusterUpdatePlanSpec(
                cluster_name="mosk-cluster",
                target_release="25.2.0",
                update_type=UpdateType.FULL_UPDATE,
                update_strategy=UpdateStrategy.ROLLING,
                crq_number="CRQ123456789",
            ),
        )
    """

    API_VERSION: ClassVar[str] = "kaas.mirantis.com/v1alpha1"
    KIND: ClassVar[str] = "ClusterUpdatePlan"
    PLURAL: ClassVar[str] = "clusterupdateplans"
    GROUP: ClassVar[str] = "kaas.mirantis.com"

    api_version: str = Field(default="kaas.mirantis.com/v1alpha1", alias="apiVersion")
    kind: str = Field(default="ClusterUpdatePlan")
    spec: ClusterUpdatePlanSpec
    status: ClusterUpdatePlanStatus | None = None

    @property
    def is_complete(self) -> bool:
        """Check if update is complete."""
        return self.status is not None and self.status.is_complete

    @property
    def is_successful(self) -> bool:
        """Check if update completed successfully."""
        return self.status is not None and self.status.is_successful

    @property
    def progress_percent(self) -> int:
        """Get update progress percentage."""
        if self.status is not None:
            return self.status.progress_percent
        return 0

    @classmethod
    def create_full_update(
        cls,
        cluster_name: str,
        namespace: str,
        target_release: str,
        description: str | None = None,
        crq_number: str | None = None,
    ) -> ClusterUpdatePlan:
        """Create a full cluster update plan.

        Args:
            cluster_name: Cluster name.
            namespace: Kubernetes namespace.
            target_release: Target release version.
            description: Description of the update.
            crq_number: Change request number.

        Returns:
            ClusterUpdatePlan for full update.
        """
        return cls(
            metadata=KubernetesMetadata(
                name=f"full-update-{cluster_name}-{target_release.replace('.', '-')}",
                namespace=namespace,
            ),
            spec=ClusterUpdatePlanSpec(
                cluster_name=cluster_name,
                target_release=target_release,
                update_type=UpdateType.FULL_UPDATE,
                update_strategy=UpdateStrategy.ROLLING,
                description=description,
                crq_number=crq_number,
            ),
        )

    @classmethod
    def create_openstack_update(
        cls,
        cluster_name: str,
        namespace: str,
        target_release: str,
        description: str | None = None,
        crq_number: str | None = None,
    ) -> ClusterUpdatePlan:
        """Create an OpenStack-only update plan.

        Args:
            cluster_name: Cluster name.
            namespace: Kubernetes namespace.
            target_release: Target release version.
            description: Description of the update.
            crq_number: Change request number.

        Returns:
            ClusterUpdatePlan for OpenStack update.
        """
        return cls(
            metadata=KubernetesMetadata(
                name=f"openstack-update-{cluster_name}-{target_release.replace('.', '-')}",
                namespace=namespace,
            ),
            spec=ClusterUpdatePlanSpec(
                cluster_name=cluster_name,
                target_release=target_release,
                update_type=UpdateType.OPENSTACK_ONLY,
                update_strategy=UpdateStrategy.ROLLING,
                description=description,
                crq_number=crq_number,
            ),
        )

    @classmethod
    def from_kubernetes(cls, data: dict[str, Any]) -> ClusterUpdatePlan:
        """Create from Kubernetes API response.

        Args:
            data: Resource dictionary from Kubernetes API.

        Returns:
            ClusterUpdatePlan instance.
        """
        spec_data = data.get("spec", {})

        update_type = UpdateType.FULL_UPDATE
        if "updateType" in spec_data:
            with contextlib.suppress(ValueError):
                update_type = UpdateType(spec_data["updateType"])

        update_strategy = UpdateStrategy.ROLLING
        if "updateStrategy" in spec_data:
            with contextlib.suppress(ValueError):
                update_strategy = UpdateStrategy(spec_data["updateStrategy"])

        components = []
        for comp_data in spec_data.get("components", []):
            components.append(
                ComponentUpdate(
                    name=comp_data.get("name", ""),
                    target_version=comp_data.get("targetVersion", ""),
                    current_version=comp_data.get("currentVersion"),
                    enabled=comp_data.get("enabled", True),
                )
            )

        spec = ClusterUpdatePlanSpec(
            cluster_name=spec_data.get("clusterName", ""),
            target_release=spec_data.get("targetRelease", ""),
            update_type=update_type,
            update_strategy=update_strategy,
            components=components,
            max_unavailable_nodes=spec_data.get("maxUnavailableNodes", 1),
            pause_between_nodes=spec_data.get("pauseBetweenNodes", 0),
            skip_preflight_checks=spec_data.get("skipPreflightChecks", False),
            auto_approve=spec_data.get("autoApprove", False),
            rollback_on_failure=spec_data.get("rollbackOnFailure", True),
            maintenance_window_start=spec_data.get("maintenanceWindowStart"),
            maintenance_window_end=spec_data.get("maintenanceWindowEnd"),
            crq_number=spec_data.get("crqNumber"),
            description=spec_data.get("description"),
        )

        status = None
        if "status" in data:
            status_data = data["status"]
            phase = None
            if "phase" in status_data:
                with contextlib.suppress(ValueError):
                    phase = ClusterUpdatePhase(status_data["phase"])

            node_statuses = []
            for ns_data in status_data.get("nodeStatuses", []):
                node_statuses.append(
                    NodeUpdateStatus(
                        node_name=ns_data.get("nodeName", ""),
                        phase=ns_data.get("phase", "Pending"),
                        started_at=ns_data.get("startedAt"),
                        completed_at=ns_data.get("completedAt"),
                        components_updated=ns_data.get("componentsUpdated", 0),
                        message=ns_data.get("message"),
                    )
                )

            component_statuses = []
            for cs_data in status_data.get("componentStatuses", []):
                component_statuses.append(
                    ComponentStatus(
                        name=cs_data.get("name", ""),
                        status=cs_data.get("status", "Pending"),
                        current_version=cs_data.get("currentVersion"),
                        target_version=cs_data.get("targetVersion"),
                        message=cs_data.get("message"),
                    )
                )

            status = ClusterUpdatePlanStatus(
                phase=phase,
                started_at=status_data.get("startedAt"),
                completed_at=status_data.get("completedAt"),
                current_release=status_data.get("currentRelease"),
                target_release=status_data.get("targetRelease"),
                progress_percent=status_data.get("progressPercent", 0),
                nodes_updated=status_data.get("nodesUpdated", 0),
                nodes_total=status_data.get("nodesTotal", 0),
                nodes_pending=status_data.get("nodesPending", 0),
                node_statuses=node_statuses,
                component_statuses=component_statuses,
                preflight_results=status_data.get("preflightResults"),
                conditions=status_data.get("conditions", []),
                message=status_data.get("message"),
                error_message=status_data.get("errorMessage"),
                rollback_available=status_data.get("rollbackAvailable", False),
            )

        return cls(
            metadata=KubernetesMetadata.from_kubernetes(data["metadata"]),
            spec=spec,
            status=status,
        )
