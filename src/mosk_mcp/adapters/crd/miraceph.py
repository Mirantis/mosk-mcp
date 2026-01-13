"""MiraCeph CRD models for MOSK Ceph storage management.

This module provides Pydantic models for the MiraCeph custom resource,
which is the Ceph management CR for MOSK.
"""

from __future__ import annotations

import contextlib
from enum import Enum
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field

from mosk_mcp.adapters.crd.base import (
    KubernetesMetadata,
    KubernetesResource,
)
from mosk_mcp.tools.common.enums import CephHealthStatus


class MiraCephPhase(str, Enum):
    """MiraCeph lifecycle phases."""

    PENDING = "Pending"
    DEPLOYING = "Deploying"
    READY = "Ready"
    UPDATING = "Updating"
    DEGRADED = "Degraded"
    ERROR = "Error"
    DELETING = "Deleting"


class OSDSpec(BaseModel):
    """OSD specification for MiraCeph.

    Attributes:
        device_class: Device class (hdd, ssd, nvme).
        device_filter: Device filter pattern.
        nodes: List of nodes to deploy OSDs on.
    """

    model_config = ConfigDict(populate_by_name=True)

    device_class: str | None = Field(
        None,
        alias="deviceClass",
        description="Device class (hdd, ssd, nvme)",
    )
    device_filter: str | None = Field(
        None,
        alias="deviceFilter",
        description="Device filter pattern",
    )
    nodes: list[str] = Field(
        default_factory=list,
        description="Nodes to deploy OSDs on",
    )


class PoolSpec(BaseModel):
    """Pool specification for MiraCeph.

    Attributes:
        name: Pool name.
        replicated_size: Replication factor.
        pg_num: Number of placement groups.
        device_class: Device class for the pool.
    """

    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(..., description="Pool name")
    replicated_size: int = Field(
        default=3,
        alias="replicatedSize",
        description="Replication factor",
    )
    pg_num: int | None = Field(
        None,
        alias="pgNum",
        description="Number of placement groups",
    )
    device_class: str | None = Field(
        None,
        alias="deviceClass",
        description="Device class for the pool",
    )


class RGWSpec(BaseModel):
    """Rados Gateway specification.

    Attributes:
        enabled: Whether RGW is enabled.
        instances: Number of RGW instances.
        port: RGW port.
    """

    model_config = ConfigDict(populate_by_name=True)

    enabled: bool = Field(default=False, description="Whether RGW is enabled")
    instances: int = Field(default=2, description="Number of RGW instances")
    port: int = Field(default=8080, description="RGW port")


class MiraCephSpec(BaseModel):
    """Specification for MiraCeph resource.

    Attributes:
        cluster_name: Ceph cluster name.
        ceph_version: Ceph version to deploy.
        mon_count: Number of monitors.
        osd: OSD specification.
        pools: Pool specifications.
        rgw: Rados Gateway specification.
        dashboard_enabled: Whether dashboard is enabled.
        metrics_enabled: Whether Prometheus metrics are enabled.
    """

    model_config = ConfigDict(populate_by_name=True)

    cluster_name: str = Field(
        default="ceph",
        alias="clusterName",
        description="Ceph cluster name",
    )
    ceph_version: str | None = Field(
        None,
        alias="cephVersion",
        description="Ceph version to deploy",
    )
    mon_count: int = Field(
        default=3,
        alias="monCount",
        description="Number of monitors",
    )
    osd: OSDSpec | None = Field(
        None,
        description="OSD specification",
    )
    pools: list[PoolSpec] = Field(
        default_factory=list,
        description="Pool specifications",
    )
    rgw: RGWSpec | None = Field(
        None,
        description="Rados Gateway specification",
    )
    dashboard_enabled: bool = Field(
        default=True,
        alias="dashboardEnabled",
        description="Whether dashboard is enabled",
    )
    metrics_enabled: bool = Field(
        default=True,
        alias="metricsEnabled",
        description="Whether Prometheus metrics are enabled",
    )

    def to_kubernetes(self) -> dict[str, Any]:
        """Convert to Kubernetes API format."""
        result: dict[str, Any] = {
            "clusterName": self.cluster_name,
            "monCount": self.mon_count,
            "dashboardEnabled": self.dashboard_enabled,
            "metricsEnabled": self.metrics_enabled,
        }
        if self.ceph_version:
            result["cephVersion"] = self.ceph_version
        if self.osd:
            result["osd"] = {
                k: v
                for k, v in {
                    "deviceClass": self.osd.device_class,
                    "deviceFilter": self.osd.device_filter,
                    "nodes": self.osd.nodes if self.osd.nodes else None,
                }.items()
                if v is not None
            }
        if self.pools:
            result["pools"] = [
                {
                    "name": p.name,
                    "replicatedSize": p.replicated_size,
                    **({"pgNum": p.pg_num} if p.pg_num else {}),
                    **({"deviceClass": p.device_class} if p.device_class else {}),
                }
                for p in self.pools
            ]
        if self.rgw:
            result["rgw"] = {
                "enabled": self.rgw.enabled,
                "instances": self.rgw.instances,
                "port": self.rgw.port,
            }
        return result


class CephCapacity(BaseModel):
    """Ceph storage capacity information.

    Attributes:
        total_bytes: Total storage in bytes.
        used_bytes: Used storage in bytes.
        available_bytes: Available storage in bytes.
        usage_percent: Usage percentage.
    """

    model_config = ConfigDict(populate_by_name=True)

    total_bytes: int = Field(
        default=0,
        alias="totalBytes",
        description="Total storage in bytes",
    )
    used_bytes: int = Field(
        default=0,
        alias="usedBytes",
        description="Used storage in bytes",
    )
    available_bytes: int = Field(
        default=0,
        alias="availableBytes",
        description="Available storage in bytes",
    )
    usage_percent: float = Field(
        default=0.0,
        alias="usagePercent",
        description="Usage percentage",
    )


class MiraCephStatus(BaseModel):
    """Status of MiraCeph resource.

    Attributes:
        phase: Current MiraCeph phase.
        health: Ceph health status.
        health_message: Detailed health message.
        mon_count: Actual number of monitors.
        osd_count: Number of OSDs.
        osd_up: Number of up OSDs.
        osd_in: Number of in OSDs.
        capacity: Storage capacity information.
        ceph_version: Deployed Ceph version.
        conditions: Status conditions.
        message: Status message.
    """

    model_config = ConfigDict(populate_by_name=True)

    phase: MiraCephPhase | None = Field(None, description="Current phase")
    health: CephHealthStatus = Field(
        default=CephHealthStatus.UNKNOWN,
        description="Ceph health status",
    )
    health_message: str | None = Field(
        None,
        alias="healthMessage",
        description="Detailed health message",
    )
    mon_count: int = Field(
        default=0,
        alias="monCount",
        description="Actual number of monitors",
    )
    osd_count: int = Field(
        default=0,
        alias="osdCount",
        description="Number of OSDs",
    )
    osd_up: int = Field(
        default=0,
        alias="osdUp",
        description="Number of up OSDs",
    )
    osd_in: int = Field(
        default=0,
        alias="osdIn",
        description="Number of in OSDs",
    )
    capacity: CephCapacity | None = Field(
        None,
        description="Storage capacity",
    )
    ceph_version: str | None = Field(
        None,
        alias="cephVersion",
        description="Deployed Ceph version",
    )
    conditions: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Status conditions",
    )
    message: str | None = Field(None, description="Status message")

    @property
    def is_healthy(self) -> bool:
        """Check if Ceph cluster is healthy."""
        return self.health == CephHealthStatus.HEALTH_OK

    @property
    def is_ready(self) -> bool:
        """Check if MiraCeph is ready."""
        return self.phase == MiraCephPhase.READY

    @property
    def all_osds_up(self) -> bool:
        """Check if all OSDs are up."""
        return self.osd_up == self.osd_count and self.osd_count > 0


class MiraCeph(KubernetesResource[MiraCephSpec, MiraCephStatus]):
    """MiraCeph custom resource.

    MiraCeph is the Ceph management CR for MOSK.

    Example:
        miraceph = MiraCeph(
            metadata=KubernetesMetadata(
                name="ceph-cluster",
                namespace="ceph-lcm-mirantis",
            ),
            spec=MiraCephSpec(
                cluster_name="ceph",
                mon_count=3,
            ),
        )
    """

    API_VERSION: ClassVar[str] = "lcm.mirantis.com/v1alpha1"
    KIND: ClassVar[str] = "MiraCeph"
    PLURAL: ClassVar[str] = "miracephs"
    GROUP: ClassVar[str] = "lcm.mirantis.com"

    api_version: str = Field(default="lcm.mirantis.com/v1alpha1", alias="apiVersion")
    kind: str = Field(default="MiraCeph")
    spec: MiraCephSpec
    status: MiraCephStatus | None = None

    @property
    def is_healthy(self) -> bool:
        """Check if Ceph cluster is healthy."""
        return self.status is not None and self.status.is_healthy

    @property
    def is_ready(self) -> bool:
        """Check if MiraCeph is ready."""
        return self.status is not None and self.status.is_ready

    @classmethod
    def from_kubernetes(cls, data: dict[str, Any]) -> MiraCeph:
        """Create from Kubernetes API response.

        Args:
            data: Resource dictionary from Kubernetes API.

        Returns:
            MiraCeph instance.
        """
        spec_data = data.get("spec", {})

        osd = None
        if "osd" in spec_data:
            osd_data = spec_data["osd"]
            osd = OSDSpec(
                device_class=osd_data.get("deviceClass"),
                device_filter=osd_data.get("deviceFilter"),
                nodes=osd_data.get("nodes", []),
            )

        pools = []
        for pool_data in spec_data.get("pools", []):
            pools.append(
                PoolSpec(
                    name=pool_data.get("name", ""),
                    replicated_size=pool_data.get("replicatedSize", 3),
                    pg_num=pool_data.get("pgNum"),
                    device_class=pool_data.get("deviceClass"),
                )
            )

        rgw = None
        if "rgw" in spec_data:
            rgw_data = spec_data["rgw"]
            rgw = RGWSpec(
                enabled=rgw_data.get("enabled", False),
                instances=rgw_data.get("instances", 2),
                port=rgw_data.get("port", 8080),
            )

        spec = MiraCephSpec(
            cluster_name=spec_data.get("clusterName", "ceph"),
            ceph_version=spec_data.get("cephVersion"),
            mon_count=spec_data.get("monCount", 3),
            osd=osd,
            pools=pools,
            rgw=rgw,
            dashboard_enabled=spec_data.get("dashboardEnabled", True),
            metrics_enabled=spec_data.get("metricsEnabled", True),
        )

        status = None
        if "status" in data:
            status_data = data["status"]
            phase = None
            if "phase" in status_data:
                with contextlib.suppress(ValueError):
                    phase = MiraCephPhase(status_data["phase"])

            health = CephHealthStatus.UNKNOWN
            if "health" in status_data:
                with contextlib.suppress(ValueError):
                    health = CephHealthStatus(status_data["health"])

            capacity = None
            if "capacity" in status_data:
                cap_data = status_data["capacity"]
                capacity = CephCapacity(
                    total_bytes=cap_data.get("totalBytes", 0),
                    used_bytes=cap_data.get("usedBytes", 0),
                    available_bytes=cap_data.get("availableBytes", 0),
                    usage_percent=cap_data.get("usagePercent", 0.0),
                )

            status = MiraCephStatus(
                phase=phase,
                health=health,
                health_message=status_data.get("healthMessage"),
                mon_count=status_data.get("monCount", 0),
                osd_count=status_data.get("osdCount", 0),
                osd_up=status_data.get("osdUp", 0),
                osd_in=status_data.get("osdIn", 0),
                capacity=capacity,
                ceph_version=status_data.get("cephVersion"),
                conditions=status_data.get("conditions", []),
                message=status_data.get("message"),
            )

        return cls(
            metadata=KubernetesMetadata.from_kubernetes(data["metadata"]),
            spec=spec,
            status=status,
        )
