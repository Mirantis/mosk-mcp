"""IpamHost CRD models for IP address management.

This module provides Pydantic models for the IpamHost custom resource,
which manages IP address assignments for MOSK nodes.
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


class IpamHostPhase(str, Enum):
    """IpamHost lifecycle phases."""

    PENDING = "Pending"
    ALLOCATED = "Allocated"
    BOUND = "Bound"
    FAILED = "Failed"


class NetworkAssignment(BaseModel):
    """IP address assignment for a specific network.

    Attributes:
        network: Network name (e.g., 'management', 'storage', 'tenant').
        subnet: Subnet CIDR.
        address: Assigned IP address.
        gateway: Gateway IP address.
        vlan_id: VLAN ID for tagged traffic.
        mtu: MTU for the network.
    """

    model_config = ConfigDict(populate_by_name=True)

    network: str = Field(..., description="Network name")
    subnet: str | None = Field(None, description="Subnet CIDR")
    address: str | None = Field(None, description="Assigned IP address")
    gateway: str | None = Field(None, description="Gateway IP address")
    vlan_id: int | None = Field(None, alias="vlanId", description="VLAN ID")
    mtu: int | None = Field(None, description="MTU for the network")

    def to_kubernetes(self) -> dict[str, Any]:
        """Convert to Kubernetes API format."""
        result: dict[str, Any] = {"network": self.network}
        if self.subnet is not None:
            result["subnet"] = self.subnet
        if self.address is not None:
            result["address"] = self.address
        if self.gateway is not None:
            result["gateway"] = self.gateway
        if self.vlan_id is not None:
            result["vlanId"] = self.vlan_id
        if self.mtu is not None:
            result["mtu"] = self.mtu
        return result


class IpamHostSpec(BaseModel):
    """Specification for IpamHost resource.

    Attributes:
        l2_template: Reference to L2Template for network configuration.
        network_assignments: List of network assignments.
        host_ref: Reference to the Machine this IpamHost is for.
    """

    model_config = ConfigDict(populate_by_name=True)

    l2_template: str = Field(
        ...,
        alias="l2Template",
        description="Reference to L2Template",
    )
    network_assignments: list[NetworkAssignment] = Field(
        default_factory=list,
        alias="networkAssignments",
        description="List of network assignments",
    )
    host_ref: dict[str, str] | None = Field(
        None,
        alias="hostRef",
        description="Reference to Machine",
    )

    def to_kubernetes(self) -> dict[str, Any]:
        """Convert to Kubernetes API format."""
        result: dict[str, Any] = {
            "l2Template": self.l2_template,
        }
        if self.network_assignments:
            result["networkAssignments"] = [na.to_kubernetes() for na in self.network_assignments]
        if self.host_ref is not None:
            result["hostRef"] = self.host_ref
        return result


class IpamHostStatus(BaseModel):
    """Status of IpamHost resource.

    Attributes:
        phase: Current phase.
        allocated_addresses: Map of network to allocated address.
        conditions: Status conditions.
        error_message: Error message if in Failed phase.
    """

    model_config = ConfigDict(populate_by_name=True)

    phase: IpamHostPhase | None = Field(None, description="Current phase")
    allocated_addresses: dict[str, str] = Field(
        default_factory=dict,
        alias="allocatedAddresses",
        description="Map of network to allocated address",
    )
    conditions: list[dict[str, Any]] = Field(default_factory=list, description="Status conditions")
    error_message: str | None = Field(
        None,
        alias="errorMessage",
        description="Error message if failed",
    )

    def get_address(self, network: str) -> str | None:
        """Get allocated address for a network.

        Args:
            network: Network name.

        Returns:
            Allocated address or None.
        """
        return self.allocated_addresses.get(network)


class IpamHost(KubernetesResource[IpamHostSpec, IpamHostStatus]):
    """IpamHost custom resource.

    Manages IP address assignments for a MOSK node across multiple networks.

    Example:
        ipam_host = IpamHost(
            metadata=KubernetesMetadata(name="compute-01-ipam", namespace="default"),
            spec=IpamHostSpec(
                l2_template="standard-compute-template",
                network_assignments=[
                    NetworkAssignment(
                        network="management",
                        subnet="10.0.0.0/24",
                        address="10.0.0.10",
                        gateway="10.0.0.1",
                    ),
                    NetworkAssignment(
                        network="storage",
                        subnet="10.0.1.0/24",
                        address="10.0.1.10",
                        vlan_id=100,
                    ),
                ],
            ),
        )
    """

    API_VERSION: ClassVar[str] = "ipam.mirantis.com/v1alpha1"
    KIND: ClassVar[str] = "IpamHost"
    PLURAL: ClassVar[str] = "ipamhosts"
    GROUP: ClassVar[str] = "ipam.mirantis.com"

    api_version: str = Field(default="ipam.mirantis.com/v1alpha1", alias="apiVersion")
    kind: str = Field(default="IpamHost")
    spec: IpamHostSpec
    status: IpamHostStatus | None = None

    # Common network names
    NETWORK_MANAGEMENT: ClassVar[str] = "management"
    NETWORK_STORAGE: ClassVar[str] = "storage"
    NETWORK_STORAGE_REPLICATION: ClassVar[str] = "storage-replication"
    NETWORK_TENANT: ClassVar[str] = "tenant"
    NETWORK_EXTERNAL: ClassVar[str] = "external"
    NETWORK_PXE: ClassVar[str] = "pxe"

    def get_management_ip(self) -> str | None:
        """Get the management network IP address.

        Returns:
            Management IP or None if not allocated.
        """
        if self.status:
            return self.status.get_address(self.NETWORK_MANAGEMENT)

        # Fall back to spec if status not available
        for na in self.spec.network_assignments:
            if na.network == self.NETWORK_MANAGEMENT:
                return na.address
        return None

    def get_storage_ip(self) -> str | None:
        """Get the storage network IP address.

        Returns:
            Storage IP or None if not allocated.
        """
        if self.status:
            return self.status.get_address(self.NETWORK_STORAGE)

        for na in self.spec.network_assignments:
            if na.network == self.NETWORK_STORAGE:
                return na.address
        return None

    @classmethod
    def from_kubernetes(cls, data: dict[str, Any]) -> IpamHost:
        """Create from Kubernetes API response.

        Args:
            data: Resource dictionary from Kubernetes API.

        Returns:
            IpamHost instance.
        """
        spec_data = data.get("spec", {})

        network_assignments = []
        for na_data in spec_data.get("networkAssignments", []):
            network_assignments.append(
                NetworkAssignment(
                    network=na_data.get("network", ""),
                    subnet=na_data.get("subnet"),
                    address=na_data.get("address"),
                    gateway=na_data.get("gateway"),
                    vlan_id=na_data.get("vlanId"),
                    mtu=na_data.get("mtu"),
                )
            )

        spec = IpamHostSpec(
            l2_template=spec_data.get("l2Template", ""),
            network_assignments=network_assignments,
            host_ref=spec_data.get("hostRef"),
        )

        status = None
        if "status" in data:
            status_data = data["status"]
            phase = None
            if "phase" in status_data:
                with contextlib.suppress(ValueError):
                    phase = IpamHostPhase(status_data["phase"])

            status = IpamHostStatus(
                phase=phase,
                allocated_addresses=status_data.get("allocatedAddresses", {}),
                conditions=status_data.get("conditions", []),
                error_message=status_data.get("errorMessage"),
            )

        return cls(
            metadata=KubernetesMetadata.from_kubernetes(data["metadata"]),
            spec=spec,
            status=status,
        )
