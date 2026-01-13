"""Template generation tools registration for MOSK MCP Server.

This module registers template generation tools with the MCP server:
- generate_bmhi: Generate BareMetalHostInventory CR
- generate_bmhp: Generate BareMetalHostProfile CR
- generate_machine: Generate Machine CR
- generate_node_templates: Generate complete node templates
- generate_l2template: Generate L2Template CR
- generate_osdpl_patch: Generate OSDPL patch
- validate_template: Validate CR templates

All tools are READ_ONLY - they generate templates but do not modify cluster state.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from pydantic import Field

from mosk_mcp.observability.logging import get_logger
from mosk_mcp.registration.utils import with_logging_context
from mosk_mcp.tools.template_generation import (
    OutputFormat,
    generate_bmhi,
    generate_bmhp,
    generate_l2template,
    generate_machine,
    generate_node_templates,
    generate_osdpl_patch,
    validate_template,
)


if TYPE_CHECKING:
    from fastmcp import FastMCP


logger = get_logger(__name__)


def register_template_generation_tools(mcp: FastMCP) -> None:
    """Register template generation tools with the MCP server.

    All template generation tools are READ_ONLY - they generate templates
    but do not modify cluster state.

    Args:
        mcp: FastMCP server instance.
    """

    # generate_bmhi - BareMetalHostInventory
    @mcp.tool(
        name="generate_bmhi",
        description=(
            "Generate a BareMetalHostInventory CR for registering a bare metal server. "
            "Creates the BMHi resource and BMC credentials Secret template for hardware discovery."
        ),
    )
    async def _generate_bmhi(
        hostname: str = Field(..., description="Server hostname (used as resource name)"),
        bmc_address: str = Field(
            ..., description="BMC address (e.g., 'ipmi://192.168.1.100', 'redfish://host:443')"
        ),
        bmc_credentials_secret: str = Field(
            ..., description="Name of Secret containing BMC username/password"
        ),
        boot_mac_address: str = Field(
            ..., description="MAC address of primary boot interface (aa:bb:cc:dd:ee:ff)"
        ),
        bmc_type: Literal["ipmi", "redfish", "idrac", "ilo"] = Field(
            default="ipmi", description="BMC protocol type"
        ),
        disable_tls_verify: bool = Field(
            default=False, description="Skip TLS verification for Redfish/iDRAC/iLO"
        ),
        hardware_profile: str | None = Field(
            default=None, description="Optional BareMetalHostProfile reference"
        ),
        online: bool = Field(default=True, description="Desired power state"),
        namespace: str = Field(default="default", description="Kubernetes namespace"),
        labels: dict[str, str] | None = Field(default=None, description="Additional labels"),
        annotations: dict[str, str] | None = Field(default=None, description="Annotations"),
        output_format: Literal["yaml", "json", "kubectl"] = Field(
            default="yaml", description="Output format"
        ),
    ) -> dict[str, Any]:
        """Generate BareMetalHostInventory CR for hardware discovery."""
        async with with_logging_context("generate_bmhi"):
            result = await generate_bmhi(
                hostname=hostname,
                bmc_address=bmc_address,
                bmc_credentials_secret=bmc_credentials_secret,
                boot_mac_address=boot_mac_address,
                bmc_type=bmc_type,
                disable_tls_verify=disable_tls_verify,
                hardware_profile=hardware_profile,
                online=online,
                namespace=namespace,
                labels=labels,
                annotations=annotations,
                output_format=OutputFormat(output_format),
            )
            return result.model_dump()

    # generate_bmhp - BareMetalHostProfile
    @mcp.tool(
        name="generate_bmhp",
        description=(
            "Generate a BareMetalHostProfile CR for hardware configuration. "
            "Defines disk partitioning, RAID config, kernel parameters, and deployment scripts."
        ),
    )
    async def _generate_bmhp(
        profile_name: str = Field(..., description="Profile name (resource name)"),
        cluster_name: str = Field(..., description="Cluster name for labels"),
        role: Literal["compute", "storage", "control", "gateway", "generic"] = Field(
            default="generic", description="Intended node role"
        ),
        region: str = Field(default="region-one", description="Region for labels"),
        root_device_hints: dict[str, Any] | None = Field(
            default=None,
            description="Root disk selection hints (e.g., {'deviceType': 'ssd', 'minSizeGigabytes': 200})",
        ),
        kernel_parameters: list[str] | None = Field(
            default=None, description="Kernel boot parameters"
        ),
        grub_config: dict[str, str] | None = Field(default=None, description="GRUB configuration"),
        raid_config: dict[str, Any] | None = Field(default=None, description="RAID configuration"),
        disk_configs: list[dict[str, Any]] | None = Field(
            default=None, description="Disk partitioning configurations"
        ),
        pre_deploy_script: str | None = Field(default=None, description="Pre-deployment script"),
        post_deploy_script: str | None = Field(default=None, description="Post-deployment script"),
        namespace: str = Field(default="default", description="Kubernetes namespace"),
        labels: dict[str, str] | None = Field(default=None, description="Additional labels"),
        annotations: dict[str, str] | None = Field(default=None, description="Annotations"),
        output_format: Literal["yaml", "json", "kubectl"] = Field(
            default="yaml", description="Output format"
        ),
    ) -> dict[str, Any]:
        """Generate BareMetalHostProfile CR for hardware configuration."""
        async with with_logging_context("generate_bmhp"):
            result = await generate_bmhp(
                profile_name=profile_name,
                role=role,
                root_device_hints=root_device_hints,
                kernel_parameters=kernel_parameters,
                grub_config=grub_config,
                raid_config=raid_config,
                disk_configs=disk_configs,
                pre_deploy_script=pre_deploy_script,
                post_deploy_script=post_deploy_script,
                namespace=namespace,
                cluster_name=cluster_name,
                region=region,
                labels=labels,
                annotations=annotations,
                output_format=OutputFormat(output_format),
            )
            return result.model_dump()

    # generate_machine - Machine CR
    @mcp.tool(
        name="generate_machine",
        description=(
            "Generate a Machine CR for adding a node to MOSK cluster. "
            "Sets role labels and references to BMHp, IpamHost, and L2Template."
        ),
    )
    async def _generate_machine(
        name: str = Field(..., description="Machine name (matches BareMetalHostInventory)"),
        role: Literal["compute", "control", "storage", "gateway"] = Field(
            ..., description="Node role in cluster"
        ),
        bmhp_ref: str = Field(..., description="BareMetalHostProfile name to use"),
        bmhp_namespace: str | None = Field(
            default=None, description="BareMetalHostProfile namespace (defaults to same as Machine)"
        ),
        host_id: str | None = Field(
            default=None,
            description="Host ID for hostSelector to match BMHi (defaults to Machine name)",
        ),
        l2_template_label: str | None = Field(
            default=None, description="L2Template label value for l2TemplateSelector"
        ),
        cluster_name: str = Field(
            default="mos", description="Cluster name for cluster.sigs.k8s.io/cluster-name label"
        ),
        region: str = Field(
            default="region-one", description="Region label for kaas.mirantis.com/region"
        ),
        node_labels: list[dict[str, str]] | None = Field(
            default=None,
            description="Labels for K8s node [{'key': 'label-key', 'value': 'label-value'}]",
        ),
        host_repositories: list[str] | None = Field(
            default=None, description="Package repositories"
        ),
        public_keys: list[str] | None = Field(default=None, description="SSH public keys"),
        additional_labels: dict[str, str] | None = Field(
            default=None, description="Additional labels"
        ),
        annotations: dict[str, str] | None = Field(default=None, description="Annotations"),
        namespace: str = Field(default="default", description="Kubernetes namespace"),
        output_format: Literal["yaml", "json", "kubectl"] = Field(
            default="yaml", description="Output format"
        ),
    ) -> dict[str, Any]:
        """Generate Machine CR for adding a node to MOSK cluster."""
        async with with_logging_context("generate_machine"):
            result = await generate_machine(
                name=name,
                role=role,
                bmhp_ref=bmhp_ref,
                bmhp_namespace=bmhp_namespace,
                host_id=host_id,
                l2_template_label=l2_template_label,
                cluster_name=cluster_name,
                region=region,
                node_labels=node_labels,
                host_repositories=host_repositories,
                public_keys=public_keys,
                additional_labels=additional_labels,
                annotations=annotations,
                namespace=namespace,
                output_format=OutputFormat(output_format),
            )
            return result.model_dump()

    # generate_node_templates - Complete node templates
    @mcp.tool(
        name="generate_node_templates",
        description=(
            "Generate complete templates for adding a new node to MOSK cluster. "
            "Creates Secret, BMHi, and Machine CR with clear placeholders. "
            "Use this for adding compute, control, storage, or gateway nodes."
        ),
    )
    async def _generate_node_templates(
        node_name: str | None = Field(
            default=None,
            description="Node name (e.g., compute-04). Uses placeholder if not provided.",
        ),
        role: Literal["compute", "control", "storage", "gateway", "generic"] = Field(
            default="generic",
            description="Node role: compute, control, storage, gateway, or generic",
        ),
        namespace: str = Field(default="default", description="Kubernetes namespace"),
        cluster_name: str | None = Field(
            default=None, description="Cluster name (e.g., mos). Uses placeholder if not provided."
        ),
        region: str = Field(default="region-one", description="Region label value"),
        bmhp_name: str | None = Field(
            default=None, description="BareMetalHostProfile name. Uses placeholder if not provided."
        ),
        l2_template_label: str | None = Field(
            default=None, description="L2Template selector label. Uses placeholder if not provided."
        ),
        bmc_address: str | None = Field(
            default=None, description="BMC/IPMI address. Uses placeholder if not provided."
        ),
        boot_mac_address: str | None = Field(
            default=None, description="Boot NIC MAC address. Uses placeholder if not provided."
        ),
        additional_node_labels: list[dict[str, str]] | None = Field(
            default=None, description="Additional node labels [{key, value}]"
        ),
        output_format: Literal["yaml", "json"] = Field(default="yaml", description="Output format"),
    ) -> dict[str, Any]:
        """Generate complete templates for adding a new node."""
        async with with_logging_context("generate_node_templates"):
            result = await generate_node_templates(
                node_name=node_name,
                role=role,
                namespace=namespace,
                cluster_name=cluster_name,
                region=region,
                bmhp_name=bmhp_name,
                l2_template_label=l2_template_label,
                bmc_address=bmc_address,
                boot_mac_address=boot_mac_address,
                additional_node_labels=additional_node_labels,
            )
            return result.model_dump()

    # generate_l2template - L2Template CR
    @mcp.tool(
        name="generate_l2template",
        description=(
            "Generate an L2Template CR for layer 2 network configuration. "
            "Defines interfaces, bonds, bridges, VLANs, and network mappings."
        ),
    )
    async def _generate_l2template(
        name: str = Field(..., description="Template name"),
        cluster_name: str = Field(..., description="Cluster name (for required label)"),
        region: str = Field(default="region-one", description="Region for labels"),
        namespace: str = Field(default="default", description="Kubernetes namespace"),
        np_template: str | None = Field(
            default=None, description="Raw netplan Go template string (advanced)"
        ),
        if_mapping: list[str] | None = Field(
            default=None, description="Explicit interface mapping ['enp9s0f0', 'enp9s0f1']"
        ),
        auto_if_mapping_prio: list[str] | None = Field(
            default=None, description="Auto-discover by prefix priority ['eno', 'ens', 'enp']"
        ),
        subnets: list[dict[str, Any]] | None = Field(
            default=None,
            description="Subnet references: [{name: 'pxe', scope: 'namespace'}, ...]",
        ),
        topology: dict[str, Any] | None = Field(
            default=None,
            description=(
                "High-level network topology for auto-generating npTemplate: "
                "{nic_count: 4, bonds: [...], vlans: [...], bridges: [...]}"
            ),
        ),
        is_default: bool = Field(default=False, description="Mark as default template for cluster"),
        labels: dict[str, str] | None = Field(default=None, description="Additional labels"),
        annotations: dict[str, str] | None = Field(default=None, description="Annotations"),
        output_format: Literal["yaml", "json", "kubectl"] = Field(
            default="yaml", description="Output format"
        ),
    ) -> dict[str, Any]:
        """Generate L2Template CR for network configuration."""
        async with with_logging_context("generate_l2template"):
            result = await generate_l2template(
                name=name,
                cluster_name=cluster_name,
                region=region,
                namespace=namespace,
                np_template=np_template,
                if_mapping=if_mapping,
                auto_if_mapping_prio=auto_if_mapping_prio,
                subnets=subnets,
                topology=topology,
                is_default=is_default,
                labels=labels,
                annotations=annotations,
                output_format=OutputFormat(output_format),
            )
            return result.model_dump()

    # generate_osdpl_patch - OSDPL JSON patch
    @mcp.tool(
        name="generate_osdpl_patch",
        description=(
            "Generate a JSON patch for OpenStackDeployment resource. "
            "Creates patch with optional before/after diff preview."
        ),
    )
    async def _generate_osdpl_patch(
        osdpl_name: str = Field(
            ..., description="OSDPL resource name (e.g., 'mos', 'openstack'). Required."
        ),
        changes: list[dict[str, Any]] = Field(
            ...,
            description="List of changes: [{path: 'spec.services.nova.replicas', value: 5, description: '...'}]",
        ),
        namespace: str = Field(
            default="openstack", description="Kubernetes namespace where OSDPL is deployed"
        ),
        current_osdpl: dict[str, Any] | None = Field(
            default=None, description="Current OSDPL spec for diff preview"
        ),
        show_diff: bool = Field(default=True, description="Generate diff preview"),
        output_format: Literal["yaml", "json", "kubectl"] = Field(
            default="json", description="Output format"
        ),
    ) -> dict[str, Any]:
        """Generate JSON patch for OSDPL modification."""
        async with with_logging_context("generate_osdpl_patch"):
            result = await generate_osdpl_patch(
                changes=changes,
                current_osdpl=current_osdpl,
                osdpl_name=osdpl_name,
                namespace=namespace,
                show_diff=show_diff,
                output_format=OutputFormat(output_format),
            )
            return result.model_dump()

    # validate_template - Template validation
    @mcp.tool(
        name="validate_template",
        description=(
            "Validate a Kubernetes CR template for syntax, schema, and naming conventions. "
            "Can optionally check for conflicts with existing cluster resources."
        ),
    )
    async def _validate_template(
        template_yaml: str = Field(..., description="YAML template string to validate"),
        check_cluster_conflicts: bool = Field(
            default=False, description="Check for naming conflicts"
        ),
        existing_resources: list[str] | None = Field(
            default=None, description="Existing resources (Kind/name format) for conflict check"
        ),
        strict_mode: bool = Field(default=False, description="Treat warnings as errors"),
    ) -> dict[str, Any]:
        """Validate Kubernetes CR template."""
        async with with_logging_context("validate_template"):
            result = await validate_template(
                template_yaml=template_yaml,
                check_cluster_conflicts=check_cluster_conflicts,
                existing_resources=existing_resources,
                strict_mode=strict_mode,
            )
            return result.model_dump()

    logger.debug("template_generation_tools_registered", count=7)
