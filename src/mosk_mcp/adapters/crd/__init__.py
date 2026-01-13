"""Custom Resource Definition models for MOSK Kubernetes resources.

This module provides Pydantic models for MOSK-specific Kubernetes CRDs:
- BareMetalHostInventory (BMHi)
- BareMetalHostProfile (BMHp)
- Machine
- IpamHost
- L2Template
- OpenStackDeployment (OSDPL)
- NodeMaintenanceRequest
- ClusterMaintenanceRequest (cluster-level maintenance)
- GracefulRebootRequest (orchestrated reboots)
- ClusterUpdatePlan (granular updates)
- MiraCeph

All models support serialization to/from Kubernetes API format.
"""

from __future__ import annotations

from mosk_mcp.adapters.crd.baremetal import (
    BareMetalHostInventory,
    BareMetalHostInventorySpec,
    BareMetalHostProfile,
    BareMetalHostProfileSpec,
    BMCSpec,
    DiskSelector,
    HardwareProfile,
    NICBondingSpec,
)
from mosk_mcp.adapters.crd.base import (
    KubernetesMetadata,
    KubernetesResource,
    KubernetesResourceList,
    OwnerReference,
)
from mosk_mcp.adapters.crd.cluster_maintenance import (
    ClusterMaintenancePhase,
    ClusterMaintenanceReason,
    ClusterMaintenanceRequest,
    ClusterMaintenanceRequestSpec,
    ClusterMaintenanceRequestStatus,
)
from mosk_mcp.adapters.crd.cluster_update import (
    ClusterUpdatePhase,
    ClusterUpdatePlan,
    ClusterUpdatePlanSpec,
    ClusterUpdatePlanStatus,
    ComponentUpdate,
    UpdateStrategy,
    UpdateType,
)
from mosk_mcp.adapters.crd.graceful_reboot import (
    GracefulRebootPhase,
    GracefulRebootRequest,
    GracefulRebootRequestSpec,
    GracefulRebootRequestStatus,
    RebootReason,
    RebootStrategy,
)
from mosk_mcp.adapters.crd.ipam import (
    IpamHost,
    IpamHostSpec,
    IpamHostStatus,
    NetworkAssignment,
)
from mosk_mcp.adapters.crd.l2template import (
    L2Template,
    L2TemplateSpec,
    L2TemplateStatus,
    L3LayoutEntry,
    L3LayoutScope,
)
from mosk_mcp.adapters.crd.machine import (
    BareMetalHostProfileRef,
    Machine,
    MachinePhase,
    MachineProviderSpec,
    MachineSpec,
    MachineStatus,
)
from mosk_mcp.adapters.crd.maintenance import (
    MaintenancePhase,
    NodeMaintenanceRequest,
    NodeMaintenanceRequestSpec,
    NodeMaintenanceRequestStatus,
)
from mosk_mcp.adapters.crd.miraceph import (
    CephCapacity,
    CephHealthStatus,
    MiraCeph,
    MiraCephPhase,
    MiraCephSpec,
    MiraCephStatus,
    OSDSpec,
    PoolSpec,
    RGWSpec,
)
from mosk_mcp.adapters.crd.osdpl import (
    NodeSelector,
    OpenStackDeployment,
    OpenStackDeploymentSpec,
    OpenStackDeploymentStatus,
    OpenStackFeatures,
    OpenStackNetworkingSpec,
    OpenStackServicesSpec,
)


__all__ = [
    "BMCSpec",
    "BareMetalHostInventory",
    "BareMetalHostInventorySpec",
    "BareMetalHostProfile",
    "BareMetalHostProfileRef",
    "BareMetalHostProfileSpec",
    "CephCapacity",
    "CephHealthStatus",
    "ClusterMaintenancePhase",
    "ClusterMaintenanceReason",
    "ClusterMaintenanceRequest",
    "ClusterMaintenanceRequestSpec",
    "ClusterMaintenanceRequestStatus",
    "ClusterUpdatePhase",
    "ClusterUpdatePlan",
    "ClusterUpdatePlanSpec",
    "ClusterUpdatePlanStatus",
    "ComponentUpdate",
    "DiskSelector",
    "GracefulRebootPhase",
    "GracefulRebootRequest",
    "GracefulRebootRequestSpec",
    "GracefulRebootRequestStatus",
    "HardwareProfile",
    "IpamHost",
    "IpamHostSpec",
    "IpamHostStatus",
    "KubernetesMetadata",
    "KubernetesResource",
    "KubernetesResourceList",
    "L2Template",
    "L2TemplateSpec",
    "L2TemplateStatus",
    "L3LayoutEntry",
    "L3LayoutScope",
    "Machine",
    "MachinePhase",
    "MachineProviderSpec",
    "MachineSpec",
    "MachineStatus",
    "MaintenancePhase",
    "MiraCeph",
    "MiraCephPhase",
    "MiraCephSpec",
    "MiraCephStatus",
    "NICBondingSpec",
    "NetworkAssignment",
    "NodeMaintenanceRequest",
    "NodeMaintenanceRequestSpec",
    "NodeMaintenanceRequestStatus",
    "NodeSelector",
    "OSDSpec",
    "OpenStackDeployment",
    "OpenStackDeploymentSpec",
    "OpenStackDeploymentStatus",
    "OpenStackFeatures",
    "OpenStackNetworkingSpec",
    "OpenStackServicesSpec",
    "OwnerReference",
    "PoolSpec",
    "RGWSpec",
    "RebootReason",
    "RebootStrategy",
    "UpdateStrategy",
    "UpdateType",
]
