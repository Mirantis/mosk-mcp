"""L2Template template generation tool.

This module provides the generate_l2template tool for generating L2Template
custom resources for layer 2 network configuration in MOSK clusters.

The L2Template uses Go template syntax in npTemplate to generate netplan YAML.
Available template functions:
- {{nic N}}: Returns NIC name at index N from ifMapping
- {{mac N}}: Returns MAC address of NIC at index N
- {{ip "iface:subnet"}}: Allocates IP from subnet
- {{gateway_from_subnet "name"}}: Returns gateway from subnet
- {{nameservers_from_subnet "name"}}: Returns nameservers from subnet
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from mosk_mcp.adapters.crd.base import KubernetesMetadata
from mosk_mcp.adapters.crd.l2template import (
    L2Template,
    L2TemplateSpec,
    L3LayoutEntry,
    L3LayoutScope,
)
from mosk_mcp.observability.logging import get_logger
from mosk_mcp.tools.template_generation.base import (
    BaseTemplateGenerator,
    OutputFormat,
    TemplateOutput,
)


logger = get_logger(__name__)


# =============================================================================
# Input Models for High-Level Network Topology
# =============================================================================


class SubnetRefInput(BaseModel):
    """Subnet reference for L3 layout.

    Attributes:
        name: Subnet CR name.
        scope: Scope (namespace or cluster).
        label_selector: Optional label selector.
    """

    name: str = Field(..., description="Subnet CR name", min_length=1)
    scope: Literal["namespace", "cluster"] = Field(
        default="namespace",
        description="Scope for subnet lookup",
    )
    label_selector: dict[str, str] | None = Field(
        default=None,
        description="Optional label selector for filtering",
    )


class BondConfigInput(BaseModel):
    """Bond configuration for netplan generation.

    Attributes:
        name: Bond interface name (e.g., 'bond0').
        nic_indices: NIC indices to bond (references ifMapping).
        mode: Bonding mode.
        mtu: Optional MTU.
    """

    name: str = Field(..., description="Bond interface name", min_length=1)
    nic_indices: list[int] = Field(
        ...,
        description="NIC indices to bond (references ifMapping)",
        min_length=2,
    )
    mode: Literal[
        "balance-rr",
        "active-backup",
        "balance-xor",
        "broadcast",
        "802.3ad",
        "balance-tlb",
        "balance-alb",
    ] = Field(default="802.3ad", description="Bonding mode")
    mtu: int | None = Field(
        default=None,
        description="MTU for bond interface",
        ge=68,
        le=65535,
    )


class VlanConfigInput(BaseModel):
    """VLAN configuration for netplan generation.

    Attributes:
        name: VLAN interface name (e.g., 'vlan1722').
        id: VLAN ID.
        parent: Parent interface (bond name or nic index as 'nic:N').
    """

    name: str = Field(..., description="VLAN interface name", min_length=1)
    id: int = Field(..., description="VLAN ID", ge=1, le=4094)
    parent: str = Field(
        ...,
        description="Parent interface (bond name or 'nic:N' for NIC index)",
        min_length=1,
    )


class BridgeConfigInput(BaseModel):
    """Bridge configuration for netplan generation.

    Attributes:
        name: Bridge interface name (e.g., 'k8s-lcm').
        interfaces: Interfaces to add to bridge (VLAN names or bond names).
        subnet: Subnet name for IP allocation (used in {{ip}} template).
        is_gateway: Whether to add default route via this bridge.
        add_nameservers: Whether to add nameservers from subnet.
    """

    name: str = Field(..., description="Bridge interface name", min_length=1)
    interfaces: list[str] = Field(
        ...,
        description="Interfaces to add to bridge",
        min_length=1,
    )
    subnet: str = Field(
        ...,
        description="Subnet name for IP allocation",
        min_length=1,
    )
    is_gateway: bool = Field(
        default=False,
        description="Add default route via this bridge",
    )
    add_nameservers: bool = Field(
        default=False,
        description="Add nameservers from subnet",
    )


class NetworkTopologyInput(BaseModel):
    """High-level network topology for npTemplate generation.

    Attributes:
        nic_count: Number of NICs to configure (generates ethernet entries).
        bonds: Optional bond configurations.
        vlans: Optional VLAN configurations.
        bridges: Bridge configurations.
    """

    nic_count: int = Field(
        ...,
        description="Number of NICs to configure",
        ge=1,
        le=16,
    )
    bonds: list[BondConfigInput] = Field(
        default_factory=list,
        description="Bond configurations",
    )
    vlans: list[VlanConfigInput] = Field(
        default_factory=list,
        description="VLAN configurations",
    )
    bridges: list[BridgeConfigInput] = Field(
        default_factory=list,
        description="Bridge configurations",
    )


# =============================================================================
# Main Input/Output Models
# =============================================================================


class GenerateL2TemplateInput(BaseModel):
    """Input parameters for generating an L2Template CR.

    Supports two modes:
    1. Raw npTemplate mode: Provide np_template directly
    2. High-level mode: Provide topology and subnets for auto-generation

    Attributes:
        name: Template name (used as resource name).
        cluster_name: Cluster name (required for cluster.sigs.k8s.io/cluster-name label).
        namespace: Kubernetes namespace.
        if_mapping: Explicit interface name mapping.
        auto_if_mapping_prio: Auto-discover interfaces by prefix.
        subnets: Subnet references for L3 layout.
        np_template: Raw netplan Go template (if provided, skips generation).
        topology: High-level network topology for npTemplate generation.
        is_default: Whether this is the default L2Template for the cluster.
        labels: Additional labels.
        annotations: Annotations.
        output_format: Output format.
    """

    name: str = Field(
        ...,
        description="Template name (used as resource name)",
        min_length=1,
        max_length=63,
        pattern=r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$",
    )
    cluster_name: str = Field(
        ...,
        description="Cluster name (required label)",
        min_length=1,
    )
    region: str = Field(
        default="region-one",
        description="Region for kaas.mirantis.com/region label",
    )
    namespace: str = Field(
        default="default",
        description="Kubernetes namespace",
    )
    if_mapping: list[str] | None = Field(
        default=None,
        description="Explicit interface name mapping (e.g., ['enp9s0f0', 'enp9s0f1'])",
    )
    auto_if_mapping_prio: list[str] | None = Field(
        default=None,
        description="Auto-discover interfaces by prefix priority (e.g., ['eno', 'ens'])",
    )
    subnets: list[SubnetRefInput] = Field(
        default_factory=list,
        description="Subnet references for L3 layout",
    )
    np_template: str | None = Field(
        default=None,
        description="Raw netplan Go template. If provided, topology is ignored.",
    )
    topology: NetworkTopologyInput | None = Field(
        default=None,
        description="High-level topology for npTemplate generation",
    )
    is_default: bool = Field(
        default=False,
        description="Whether this is the default L2Template for the cluster",
    )
    labels: dict[str, str] = Field(
        default_factory=dict,
        description="Additional labels",
    )
    annotations: dict[str, str] = Field(
        default_factory=dict,
        description="Annotations",
    )
    output_format: OutputFormat = Field(
        default=OutputFormat.YAML,
        description="Output format",
    )

    @model_validator(mode="after")
    def validate_interface_mapping(self) -> GenerateL2TemplateInput:
        """Validate interface mapping configuration.

        Either if_mapping or auto_if_mapping_prio can be provided, but not both.
        Both can be None if using raw np_template (interface mapping becomes optional).
        """
        if self.if_mapping is not None and self.auto_if_mapping_prio is not None:
            raise ValueError("Cannot specify both if_mapping and auto_if_mapping_prio")
        return self

    @model_validator(mode="after")
    def validate_template_or_topology(self) -> GenerateL2TemplateInput:
        """Ensure either np_template or topology is provided."""
        if self.np_template is None and self.topology is None:
            raise ValueError("Either np_template or topology must be provided")
        return self


class GenerateL2TemplateOutput(BaseModel):
    """Output from generate_l2template tool.

    Attributes:
        template: Generated template output.
        np_template_preview: Preview of generated npTemplate.
        subnet_refs: List of referenced subnets.
        warnings: Any warnings generated.
    """

    template: TemplateOutput = Field(..., description="Generated L2Template template")
    np_template_preview: str = Field(
        ...,
        description="Preview of npTemplate content",
    )
    subnet_refs: list[str] = Field(
        default_factory=list,
        description="List of referenced subnet names",
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="Warnings generated",
    )


# =============================================================================
# npTemplate Generator Helper
# =============================================================================


class NpTemplateBuilder:
    """Helper class to build netplan Go templates from high-level topology."""

    def __init__(self, nic_count: int):
        self.nic_count = nic_count
        self.lines: list[str] = []

    def add_line(self, line: str, indent: int = 0) -> None:
        """Add a line with indentation."""
        self.lines.append("  " * indent + line)

    def build_ethernets(self) -> None:
        """Build ethernet section for all NICs."""
        self.add_line("ethernets:")
        for i in range(self.nic_count):
            self.add_line(f"{{{{nic {i}}}}}:", 1)
            self.add_line("dhcp4: false", 2)
            self.add_line("dhcp6: false", 2)
            self.add_line("match:", 2)
            self.add_line(f"macaddress: {{{{mac {i}}}}}", 3)
            self.add_line(f"set-name: {{{{nic {i}}}}}", 2)

    def build_bonds(self, bonds: list[BondConfigInput]) -> None:
        """Build bonds section."""
        if not bonds:
            return
        self.add_line("bonds:")
        for bond in bonds:
            self.add_line(f"{bond.name}:", 1)
            self.add_line("interfaces:", 2)
            for idx in bond.nic_indices:
                self.add_line(f"- {{{{nic {idx}}}}}", 3)
            self.add_line("parameters:", 2)
            self.add_line(f"mode: {bond.mode}", 3)
            self.add_line("dhcp4: false", 2)
            self.add_line("dhcp6: false", 2)
            if bond.mtu:
                self.add_line(f"mtu: {bond.mtu}", 2)

    def build_vlans(self, vlans: list[VlanConfigInput]) -> None:
        """Build VLANs section."""
        if not vlans:
            return
        self.add_line("vlans:")
        for vlan in vlans:
            self.add_line(f"{vlan.name}:", 1)
            self.add_line(f"id: {vlan.id}", 2)
            # Handle nic:N format for parent
            if vlan.parent.startswith("nic:"):
                idx = int(vlan.parent[4:])
                self.add_line(f"link: {{{{nic {idx}}}}}", 2)
            else:
                self.add_line(f"link: {vlan.parent}", 2)

    def build_bridges(self, bridges: list[BridgeConfigInput]) -> None:
        """Build bridges section."""
        if not bridges:
            return
        self.add_line("bridges:")
        for bridge in bridges:
            self.add_line(f"{bridge.name}:", 1)
            self.add_line("interfaces:", 2)
            for iface in bridge.interfaces:
                self.add_line(f"- {iface}", 3)
            self.add_line("addresses:", 2)
            self.add_line(f'- {{{{ip "{bridge.name}:{bridge.subnet}"}}}}', 3)
            if bridge.is_gateway:
                self.add_line(f'gateway4: {{{{gateway_from_subnet "{bridge.subnet}"}}}}', 2)
            if bridge.add_nameservers:
                self.add_line("nameservers:", 2)
                self.add_line(f'addresses: {{{{nameservers_from_subnet "{bridge.subnet}"}}}}', 3)

    def build(self, topology: NetworkTopologyInput) -> str:
        """Build complete npTemplate from topology."""
        self.lines = ["version: 2"]
        self.build_ethernets()
        self.build_bonds(topology.bonds)
        self.build_vlans(topology.vlans)
        self.build_bridges(topology.bridges)
        return "\n".join(self.lines) + "\n"


# =============================================================================
# Generator Class
# =============================================================================


class L2TemplateGenerator(BaseTemplateGenerator[L2Template]):
    """Generator for L2Template custom resources.

        Creates L2Template CRs that define layer 2 network configuration
        using netplan Go templates.

        Example (raw npTemplate):
            generator = L2TemplateGenerator()
            output = generator.generate_l2template(GenerateL2TemplateInput(
                name="compute-l2",
                cluster_name="mos",
                if_mapping=["enp9s0f0", "enp9s0f1"],
                subnets=[SubnetRefInput(name="lcm"), SubnetRefInput(name="pxe")],
                np_template='''version: 2
    ethernets:
      {{nic 0}}:
        dhcp4: false
        match:
          macaddress: {{mac 0}}
    bridges:
      k8s-lcm:
        interfaces:
          - {{nic 0}}
        addresses:
          - {{ip "k8s-lcm:lcm"}}
    ''',
            ))

        Example (high-level topology):
            output = generator.generate_l2template(GenerateL2TemplateInput(
                name="compute-l2",
                cluster_name="mos",
                if_mapping=["enp9s0f0", "enp9s0f1", "eno1"],
                subnets=[
                    SubnetRefInput(name="pxe"),
                    SubnetRefInput(name="lcm"),
                ],
                topology=NetworkTopologyInput(
                    nic_count=3,
                    bonds=[BondConfigInput(name="bond0", nic_indices=[0, 1], mode="802.3ad")],
                    vlans=[VlanConfigInput(name="vlan1722", id=1722, parent="bond0")],
                    bridges=[
                        BridgeConfigInput(
                            name="k8s-pxe",
                            interfaces=["bond0"],
                            subnet="pxe",
                            is_gateway=True,
                        ),
                        BridgeConfigInput(
                            name="k8s-lcm",
                            interfaces=["vlan1722"],
                            subnet="lcm",
                        ),
                    ],
                ),
            ))
    """

    def generate(self, **kwargs: Any) -> L2Template:
        """Generate an L2Template resource."""
        input_data = GenerateL2TemplateInput(**kwargs)
        return self._create_l2template(input_data)

    def _create_l2template(self, input_data: GenerateL2TemplateInput) -> L2Template:
        """Create L2Template from input."""
        # Validate name
        self.validate_dns_label(input_data.name, "name")

        # Build labels based on real MOSK L2Template resources
        labels = self.build_standard_labels(
            cluster_name=input_data.cluster_name,
            region=input_data.region,
            additional=input_data.labels,
        )
        # Add ipam/Cluster label (used by IPAM controller)
        labels[L2Template.CLUSTER_LABEL] = input_data.cluster_name
        # Add template name as label (used for l2TemplateSelector matching)
        labels[input_data.name] = input_data.name
        if input_data.is_default:
            labels[L2Template.DEFAULT_LABEL] = "1"

        # Create metadata
        metadata = KubernetesMetadata(
            name=input_data.name,
            namespace=input_data.namespace,
            labels=labels,
            annotations=input_data.annotations,
        )

        # Create l3Layout
        l3_layout = [
            L3LayoutEntry(
                scope=L3LayoutScope(subnet.scope),
                subnet_name=subnet.name,
                label_selector=subnet.label_selector,
            )
            for subnet in input_data.subnets
        ]

        # Generate or use provided npTemplate
        if input_data.np_template:
            np_template = input_data.np_template
        elif input_data.topology:
            builder = NpTemplateBuilder(input_data.topology.nic_count)
            np_template = builder.build(input_data.topology)
        else:
            np_template = ""

        # Create spec
        spec = L2TemplateSpec(
            if_mapping=input_data.if_mapping,
            auto_if_mapping_prio=input_data.auto_if_mapping_prio,
            l3_layout=l3_layout,
            np_template=np_template,
        )

        return L2Template(
            metadata=metadata,
            spec=spec,
        )

    def generate_l2template(self, input_data: GenerateL2TemplateInput) -> GenerateL2TemplateOutput:
        """Generate complete L2Template output.

        Args:
            input_data: Input parameters.

        Returns:
            Complete output with L2Template and metadata.
        """
        logger.info(
            "generating_l2template",
            name=input_data.name,
            cluster=input_data.cluster_name,
            has_raw_template=input_data.np_template is not None,
        )

        warnings: list[str] = []

        # Validate topology if provided
        if input_data.topology:
            topology = input_data.topology

            # Check bond NIC indices
            for bond in topology.bonds:
                for idx in bond.nic_indices:
                    if idx >= topology.nic_count:
                        warnings.append(
                            f"Bond '{bond.name}' references NIC index {idx} "
                            f"but only {topology.nic_count} NICs configured"
                        )

            # Check VLAN parents
            bond_names = {b.name for b in topology.bonds}
            for vlan in topology.vlans:
                if not vlan.parent.startswith("nic:") and vlan.parent not in bond_names:
                    warnings.append(f"VLAN '{vlan.name}' references unknown parent '{vlan.parent}'")

            # Check bridge interfaces
            vlan_names = {v.name for v in topology.vlans}
            for bridge in topology.bridges:
                for iface in bridge.interfaces:
                    if iface not in bond_names and iface not in vlan_names:
                        warnings.append(
                            f"Bridge '{bridge.name}' references unknown interface '{iface}'"
                        )

        # Validate subnets match topology references
        subnet_names = {s.name for s in input_data.subnets}
        if input_data.topology:
            for bridge in input_data.topology.bridges:
                if bridge.subnet not in subnet_names:
                    warnings.append(
                        f"Bridge '{bridge.name}' references subnet '{bridge.subnet}' "
                        "not in subnets list"
                    )

        # Generate L2Template
        l2template = self._create_l2template(input_data)

        # Generate template output
        template = self.generate_template(l2template, input_data.output_format)
        template.warnings = warnings

        logger.info(
            "generated_l2template",
            name=input_data.name,
            namespace=input_data.namespace,
            warnings_count=len(warnings),
        )

        return GenerateL2TemplateOutput(
            template=template,
            np_template_preview=l2template.spec.np_template[:500] + "..."
            if len(l2template.spec.np_template) > 500
            else l2template.spec.np_template,
            subnet_refs=[s.name for s in input_data.subnets],
            warnings=warnings,
        )


# =============================================================================
# Singleton and Async Function
# =============================================================================

_generator: L2TemplateGenerator | None = None


def get_l2template_generator() -> L2TemplateGenerator:
    """Get the singleton L2Template generator instance."""
    global _generator
    if _generator is None:
        _generator = L2TemplateGenerator()
    return _generator


async def generate_l2template(
    name: str,
    cluster_name: str,
    region: str = "region-one",
    if_mapping: list[str] | None = None,
    auto_if_mapping_prio: list[str] | None = None,
    subnets: list[dict[str, Any]] | None = None,
    np_template: str | None = None,
    topology: dict[str, Any] | None = None,
    is_default: bool = False,
    namespace: str = "default",
    labels: dict[str, str] | None = None,
    annotations: dict[str, str] | None = None,
    output_format: OutputFormat = OutputFormat.YAML,
) -> GenerateL2TemplateOutput:
    """Generate an L2Template CR for network configuration.

    This tool generates an L2Template custom resource that defines the
    netplan-based network configuration for MOSK nodes.

    The L2Template uses Go template syntax in npTemplate field:
    - {{nic N}}: Returns NIC name at index N from ifMapping
    - {{mac N}}: Returns MAC address of NIC at index N
    - {{ip "iface:subnet"}}: Allocates IP from the specified subnet
    - {{gateway_from_subnet "name"}}: Returns gateway from subnet
    - {{nameservers_from_subnet "name"}}: Returns nameservers from subnet

    Two modes of operation:
    1. Raw mode: Provide np_template string directly
    2. High-level mode: Provide topology dict for auto-generation

    Args:
        name: Template name (used as resource name). Must be a valid DNS label.
        cluster_name: Cluster name for required label (cluster.sigs.k8s.io/cluster-name).
        if_mapping: Explicit interface names (e.g., ["enp9s0f0", "enp9s0f1"]).
            Either this or auto_if_mapping_prio must be provided.
        auto_if_mapping_prio: Auto-discover interfaces by prefix priority
            (e.g., ["eno", "ens", "enp"]). Alternative to if_mapping.
        subnets: Subnet references for L3 layout. Each dict has:
            - name: Subnet CR name (required)
            - scope: "namespace" or "cluster" (default: namespace)
            - label_selector: Optional label selector dict
        np_template: Raw netplan Go template string. If provided, topology is ignored.
        topology: High-level topology for npTemplate generation. Dict with:
            - nic_count: Number of NICs to configure (required)
            - bonds: List of bond configs [{name, nic_indices, mode}]
            - vlans: List of VLAN configs [{name, id, parent}]
            - bridges: List of bridge configs [{name, interfaces, subnet, is_gateway}]
        is_default: Whether this is the default L2Template (adds ipam/DefaultForCluster label).
        namespace: Kubernetes namespace for the resource.
        labels: Additional labels for the resource.
        annotations: Annotations for the resource.
        output_format: Output format (yaml, json, or kubectl command).

    Returns:
        GenerateL2TemplateOutput containing:
        - template: The generated L2Template CR
        - np_template_preview: Preview of npTemplate content
        - subnet_refs: List of referenced subnet names
        - warnings: Configuration warnings

    Example (raw npTemplate):
        >>> output = await generate_l2template(
        ...     name="compute-l2",
        ...     cluster_name="mos",
        ...     namespace="lab",
        ...     if_mapping=["enp9s0f0", "enp9s0f1"],
        ...     subnets=[{"name": "pxe"}, {"name": "lcm"}],
        ...     np_template='''version: 2
        ... ethernets:
        ...   {{nic 0}}:
        ...     dhcp4: false
        ...     match:
        ...       macaddress: {{mac 0}}
        ... bridges:
        ...   k8s-pxe:
        ...     interfaces:
        ...       - {{nic 0}}
        ...     addresses:
        ...       - {{ip "k8s-pxe:pxe"}}
        ... ''',
        ... )

    Example (high-level topology):
        >>> output = await generate_l2template(
        ...     name="compute-l2",
        ...     cluster_name="mos",
        ...     namespace="lab",
        ...     if_mapping=["enp9s0f0", "enp9s0f1", "eno1"],
        ...     subnets=[{"name": "pxe"}, {"name": "lcm"}],
        ...     topology={
        ...         "nic_count": 3,
        ...         "bonds": [{"name": "bond0", "nic_indices": [0, 1], "mode": "802.3ad"}],
        ...         "vlans": [{"name": "vlan1722", "id": 1722, "parent": "bond0"}],
        ...         "bridges": [
        ...             {
        ...                 "name": "k8s-pxe",
        ...                 "interfaces": ["bond0"],
        ...                 "subnet": "pxe",
        ...                 "is_gateway": True,
        ...             },
        ...             {"name": "k8s-lcm", "interfaces": ["vlan1722"], "subnet": "lcm"},
        ...         ],
        ...     },
        ... )
    """
    generator = get_l2template_generator()

    # Convert input dicts to typed objects
    subnet_inputs = [SubnetRefInput(**s) for s in (subnets or [])]
    topology_input = NetworkTopologyInput(**topology) if topology else None

    input_data = GenerateL2TemplateInput(
        name=name,
        cluster_name=cluster_name,
        region=region,
        namespace=namespace,
        if_mapping=if_mapping,
        auto_if_mapping_prio=auto_if_mapping_prio,
        subnets=subnet_inputs,
        np_template=np_template,
        topology=topology_input,
        is_default=is_default,
        labels=labels or {},
        annotations=annotations or {},
        output_format=output_format,
    )

    return generator.generate_l2template(input_data)
