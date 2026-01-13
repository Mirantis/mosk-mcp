"""L2Template CRD models for layer 2 network configuration.

This module provides Pydantic models for the L2Template custom resource,
which defines the netplan-based network configuration for MOSK nodes.

The L2Template uses Go template syntax in the npTemplate field to generate
netplan configurations. Available template functions:
- {{nic N}}: Returns the NIC name at index N from ifMapping
- {{mac N}}: Returns the MAC address of NIC at index N
- {{ip "iface:subnet"}}: Allocates an IP from the specified subnet
- {{gateway_from_subnet "name"}}: Returns gateway from subnet
- {{nameservers_from_subnet "name"}}: Returns nameservers from subnet
- {{cidr_from_subnet "name"}}: Returns CIDR notation from subnet
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


class L3LayoutScope(str, Enum):
    """Scope for L3Layout subnet reference."""

    NAMESPACE = "namespace"
    CLUSTER = "cluster"


class L3LayoutEntry(BaseModel):
    """Entry in the L3Layout array.

    Attributes:
        scope: Scope for subnet lookup (namespace or cluster).
        subnet_name: Name of the Subnet CR to reference.
        label_selector: Optional label selector for filtering.
    """

    model_config = ConfigDict(populate_by_name=True)

    scope: L3LayoutScope = Field(
        default=L3LayoutScope.NAMESPACE,
        description="Scope for subnet lookup",
    )
    subnet_name: str = Field(
        ...,
        alias="subnetName",
        description="Name of the Subnet CR to reference",
    )
    label_selector: dict[str, str] | None = Field(
        None,
        alias="labelSelector",
        description="Optional label selector for filtering",
    )

    def to_kubernetes(self) -> dict[str, Any]:
        """Convert to Kubernetes API format."""
        result: dict[str, Any] = {
            "scope": self.scope.value,
            "subnetName": self.subnet_name,
        }
        if self.label_selector:
            result["labelSelector"] = self.label_selector
        return result


class L2TemplateSpec(BaseModel):
    """Specification for L2Template resource.

    The L2Template defines network configuration using a Go template that
    generates netplan YAML. It references subnets for IP allocation.

    Attributes:
        if_mapping: Explicit interface name mapping (e.g., ["enp9s0f0", "enp9s0f1"]).
            Use {{nic N}} and {{mac N}} to reference interfaces by index.
        auto_if_mapping_prio: Auto-discover interfaces by prefix priority.
            Alternative to if_mapping. E.g., ["eno", "ens", "enp"].
        l3_layout: List of subnet references for IP allocation.
        np_template: Netplan Go template string.
        cluster_ref: Reference to the Cluster CR name (optional, usually auto-set).
    """

    model_config = ConfigDict(populate_by_name=True)

    if_mapping: list[str] | None = Field(
        None,
        alias="ifMapping",
        description="Explicit interface name mapping",
    )
    auto_if_mapping_prio: list[str] | None = Field(
        None,
        alias="autoIfMappingPrio",
        description="Auto-discover interfaces by prefix priority",
    )
    l3_layout: list[L3LayoutEntry] = Field(
        default_factory=list,
        alias="l3Layout",
        description="List of subnet references for IP allocation",
    )
    np_template: str = Field(
        "",
        alias="npTemplate",
        description="Netplan Go template string",
    )
    cluster_ref: str | None = Field(
        None,
        alias="clusterRef",
        description="Reference to Cluster CR name",
    )

    def to_kubernetes(self) -> dict[str, Any]:
        """Convert to Kubernetes API format."""
        result: dict[str, Any] = {}
        if self.if_mapping is not None:
            result["ifMapping"] = self.if_mapping
        if self.auto_if_mapping_prio is not None:
            result["autoIfMappingPrio"] = self.auto_if_mapping_prio
        if self.l3_layout:
            result["l3Layout"] = [entry.to_kubernetes() for entry in self.l3_layout]
        if self.np_template:
            result["npTemplate"] = self.np_template
        if self.cluster_ref is not None:
            result["clusterRef"] = self.cluster_ref
        return result


class L2TemplateStatus(BaseModel):
    """Status of L2Template resource.

    Attributes:
        state: Current state (OK, Error, etc.).
        checksums: Checksums for change detection.
    """

    model_config = ConfigDict(populate_by_name=True)

    state: str | None = Field(None, description="Current state")
    checksums: dict[str, str] | None = Field(
        None,
        description="Checksums for change detection",
    )


class L2Template(KubernetesResource[L2TemplateSpec, L2TemplateStatus]):
    """L2Template custom resource.

        Defines the layer 2 network configuration template for MOSK nodes
        using netplan Go templates and subnet references.

        Required label: cluster.sigs.k8s.io/cluster-name

        Example:
            l2_template = L2Template(
                metadata=KubernetesMetadata(
                    name="compute-l2",
                    namespace="lab",
                    labels={
                        "cluster.sigs.k8s.io/cluster-name": "mos",
                    },
                ),
                spec=L2TemplateSpec(
                    if_mapping=["enp9s0f0", "enp9s0f1", "eno1"],
                    l3_layout=[
                        L3LayoutEntry(scope=L3LayoutScope.NAMESPACE, subnet_name="pxe"),
                        L3LayoutEntry(scope=L3LayoutScope.NAMESPACE, subnet_name="lcm"),
                    ],
                    np_template='''version: 2
    ethernets:
      {{nic 0}}:
        dhcp4: false
        match:
          macaddress: {{mac 0}}
        set-name: {{nic 0}}
    bonds:
      bond0:
        interfaces:
          - {{nic 0}}
          - {{nic 1}}
        parameters:
          mode: 802.3ad
    bridges:
      k8s-lcm:
        interfaces:
          - bond0
        addresses:
          - {{ip "k8s-lcm:lcm"}}
    ''',
                ),
            )
    """

    API_VERSION: ClassVar[str] = "ipam.mirantis.com/v1alpha1"
    KIND: ClassVar[str] = "L2Template"
    PLURAL: ClassVar[str] = "l2templates"
    GROUP: ClassVar[str] = "ipam.mirantis.com"

    # Required label for cluster association
    CLUSTER_LABEL: ClassVar[str] = "cluster.sigs.k8s.io/cluster-name"
    # Optional label for default template
    DEFAULT_LABEL: ClassVar[str] = "ipam/DefaultForCluster"

    api_version: str = Field(default="ipam.mirantis.com/v1alpha1", alias="apiVersion")
    kind: str = Field(default="L2Template")
    spec: L2TemplateSpec
    status: L2TemplateStatus | None = None

    @classmethod
    def from_kubernetes(cls, data: dict[str, Any]) -> L2Template:
        """Create from Kubernetes API response.

        Args:
            data: Resource dictionary from Kubernetes API.

        Returns:
            L2Template instance.
        """
        spec_data = data.get("spec", {})

        # Parse l3Layout entries
        l3_layout = []
        for entry_data in spec_data.get("l3Layout", []):
            scope = L3LayoutScope.NAMESPACE
            if "scope" in entry_data:
                with contextlib.suppress(ValueError):
                    scope = L3LayoutScope(entry_data["scope"])
            l3_layout.append(
                L3LayoutEntry(
                    scope=scope,
                    subnet_name=entry_data.get("subnetName", ""),
                    label_selector=entry_data.get("labelSelector"),
                )
            )

        spec = L2TemplateSpec(
            if_mapping=spec_data.get("ifMapping"),
            auto_if_mapping_prio=spec_data.get("autoIfMappingPrio"),
            l3_layout=l3_layout,
            np_template=spec_data.get("npTemplate", ""),
            cluster_ref=spec_data.get("clusterRef"),
        )

        status = None
        if "status" in data:
            status_data = data["status"]
            status = L2TemplateStatus(
                state=status_data.get("state"),
                checksums=status_data.get("checksums"),
            )

        return cls(
            metadata=KubernetesMetadata.from_kubernetes(data["metadata"]),
            spec=spec,
            status=status,
        )
