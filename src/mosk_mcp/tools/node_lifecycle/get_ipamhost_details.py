"""Get IpamHost details tool for MOSK MCP Server.

This module provides the get_ipamhost_details tool for retrieving detailed
network configuration for a node including IP addresses, netplan, and
network interface mappings.

Safety Level: READ_ONLY
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from mosk_mcp.auth.rbac import ToolSafetyLevel
from mosk_mcp.core.exceptions import KubernetesError, ResourceNotFoundError
from mosk_mcp.observability.audit import AuditLevel
from mosk_mcp.observability.logging import get_logger
from mosk_mcp.tools.common import audit_tool_execution


if TYPE_CHECKING:
    from mosk_mcp.adapters.kubernetes import KubernetesAdapter
    from mosk_mcp.auth.types import UserContext
    from mosk_mcp.observability.audit import AuditLogger


logger = get_logger(__name__)

# Tool metadata
TOOL_NAME = "get_ipamhost_details"
TOOL_SAFETY_LEVEL = ToolSafetyLevel.READ_ONLY
TOOL_DESCRIPTION = (
    "Get detailed network configuration for a node including IP addresses, "
    "netplan configuration, NIC mappings, and L2 template reference. "
    "IpamHost is automatically created when a Machine is applied."
)


class GetIpamHostDetailsInput(BaseModel):
    """Input parameters for get_ipamhost_details tool.

    Attributes:
        name: Name of the IpamHost resource (usually same as Machine name).
        namespace: Kubernetes namespace.
        include_netplan: Include the full netplan configuration in output.
    """

    name: str = Field(
        ...,
        description="Name of the IpamHost resource (usually same as Machine name)",
        min_length=1,
        max_length=253,
    )
    namespace: str = Field(
        default="default",
        description="Kubernetes namespace",
    )
    include_netplan: bool = Field(
        default=True,
        description="Include the full netplan configuration in the output",
    )


class NICMapping(BaseModel):
    """Network interface to MAC address mapping.

    Attributes:
        name: Interface name (e.g., 'eno1', 'enp5s0f0').
        mac: MAC address.
        ip: IP address if assigned to this interface.
        primary: Whether this is the primary boot interface.
    """

    name: str = Field(..., description="Interface name")
    mac: str = Field(..., description="MAC address")
    ip: str | None = Field(None, description="IP address if assigned")
    primary: bool = Field(False, description="Whether this is the primary interface")


class NetworkBridge(BaseModel):
    """Network bridge configuration.

    Attributes:
        name: Bridge name (e.g., 'k8s-lcm', 'cephfront').
        addresses: List of IP addresses with CIDR.
        interfaces: Member interfaces.
        gateway: Gateway address if configured.
        nameservers: DNS nameservers if configured.
    """

    name: str = Field(..., description="Bridge name")
    addresses: list[str] = Field(default_factory=list, description="IP addresses with CIDR")
    interfaces: list[str] = Field(default_factory=list, description="Member interfaces")
    gateway: str | None = Field(None, description="Gateway address")
    nameservers: list[str] = Field(default_factory=list, description="DNS nameservers")


class NetworkBond(BaseModel):
    """Network bond configuration.

    Attributes:
        name: Bond name (e.g., 'bond0', 'bond1').
        mode: Bonding mode (e.g., '802.3ad').
        interfaces: Member interfaces.
    """

    name: str = Field(..., description="Bond name")
    mode: str = Field(..., description="Bonding mode")
    interfaces: list[str] = Field(default_factory=list, description="Member interfaces")


class NetworkVLAN(BaseModel):
    """VLAN configuration.

    Attributes:
        name: VLAN interface name (e.g., 'vlan1722').
        id: VLAN ID.
        link: Parent interface.
    """

    name: str = Field(..., description="VLAN interface name")
    id: int = Field(..., description="VLAN ID")
    link: str = Field(..., description="Parent interface")


class ServiceMapping(BaseModel):
    """Service to network mapping.

    Attributes:
        service: Service name (e.g., 'ipam/SVC-k8s-lcm').
        interface: Interface name.
        ip_address: IP address for this service.
    """

    service: str = Field(..., description="Service name")
    interface: str = Field(..., description="Interface name")
    ip_address: str = Field(..., description="IP address")


class GetIpamHostDetailsOutput(BaseModel):
    """Output from get_ipamhost_details tool.

    Attributes:
        name: IpamHost name.
        namespace: IpamHost namespace.
        state: Current state (e.g., 'OK', 'Error').
        l2_template_ref: Reference to the L2Template being used.
        l2_template_selector: Label selector for L2Template.
        nic_mappings: List of NIC to MAC address mappings.
        bonds: List of bond configurations.
        bridges: List of bridge configurations.
        vlans: List of VLAN configurations.
        service_mappings: Service to IP mappings.
        primary_ip: Primary management IP address.
        netplan_config: Full netplan configuration if requested.
        labels: Resource labels.
        cluster_name: Cluster name from labels.
        creation_timestamp: When the resource was created.
    """

    name: str = Field(..., description="IpamHost name")
    namespace: str = Field(..., description="IpamHost namespace")
    state: str = Field(..., description="Current state")
    l2_template_ref: str | None = Field(None, description="L2Template reference")
    l2_template_selector: str | None = Field(None, description="L2Template label selector")
    nic_mappings: list[NICMapping] = Field(default_factory=list, description="NIC to MAC mappings")
    bonds: list[NetworkBond] = Field(default_factory=list, description="Bond configurations")
    bridges: list[NetworkBridge] = Field(default_factory=list, description="Bridge configurations")
    vlans: list[NetworkVLAN] = Field(default_factory=list, description="VLAN configurations")
    service_mappings: list[ServiceMapping] = Field(
        default_factory=list, description="Service to IP mappings"
    )
    primary_ip: str | None = Field(None, description="Primary management IP")
    netplan_config: dict[str, Any] | None = Field(None, description="Full netplan configuration")
    labels: dict[str, str] = Field(default_factory=dict, description="Resource labels")
    cluster_name: str | None = Field(None, description="Cluster name")
    creation_timestamp: str | None = Field(None, description="Creation timestamp")


def _extract_nic_mappings(spec: dict[str, Any]) -> list[NICMapping]:
    """Extract NIC mappings from IpamHost spec.

    Args:
        spec: IpamHost spec dictionary.

    Returns:
        List of NICMapping objects.
    """
    mappings = []
    for nic in spec.get("nicMACmap", []):
        mappings.append(
            NICMapping(
                name=nic.get("name", "unknown"),
                mac=nic.get("mac", ""),
                ip=nic.get("ip"),
                primary=nic.get("primary", False),
            )
        )
    return mappings


def _extract_netplan_config(
    status: dict[str, Any],
) -> tuple[list[NetworkBond], list[NetworkBridge], list[NetworkVLAN], dict[str, Any] | None]:
    """Extract network configuration from IpamHost status.

    Args:
        status: IpamHost status dictionary.

    Returns:
        Tuple of (bonds, bridges, vlans, full_netplan_config).
    """
    bonds: list[NetworkBond] = []
    bridges: list[NetworkBridge] = []
    vlans: list[NetworkVLAN] = []

    netplan = status.get("netconfigCandidate", {})
    if not netplan:
        return bonds, bridges, vlans, None

    # Extract bonds
    for name, config in netplan.get("bonds", {}).items():
        bonds.append(
            NetworkBond(
                name=name,
                mode=config.get("parameters", {}).get("mode", "unknown"),
                interfaces=config.get("interfaces", []),
            )
        )

    # Extract bridges
    for name, config in netplan.get("bridges", {}).items():
        bridges.append(
            NetworkBridge(
                name=name,
                addresses=config.get("addresses", []),
                interfaces=config.get("interfaces", []),
                gateway=config.get("gateway4"),
                nameservers=config.get("nameservers", {}).get("addresses", []),
            )
        )

    # Extract VLANs
    for name, config in netplan.get("vlans", {}).items():
        vlans.append(
            NetworkVLAN(
                name=name,
                id=config.get("id", 0),
                link=config.get("link", ""),
            )
        )

    return bonds, bridges, vlans, netplan


def _extract_service_mappings(status: dict[str, Any]) -> list[ServiceMapping]:
    """Extract service to IP mappings from IpamHost status.

    Args:
        status: IpamHost status dictionary.

    Returns:
        List of ServiceMapping objects.
    """
    mappings = []
    for service, entries in status.get("serviceMap", {}).items():
        for entry in entries:
            mappings.append(
                ServiceMapping(
                    service=service,
                    interface=entry.get("ifName", ""),
                    ip_address=entry.get("ipAddress", ""),
                )
            )
    return mappings


def _get_primary_ip(
    nic_mappings: list[NICMapping], service_mappings: list[ServiceMapping]
) -> str | None:
    """Get the primary management IP address.

    Args:
        nic_mappings: List of NIC mappings.
        service_mappings: List of service mappings.

    Returns:
        Primary IP address or None.
    """
    # First try to get from primary NIC
    for nic in nic_mappings:
        if nic.primary and nic.ip:
            return nic.ip

    # Fall back to k8s-lcm service mapping
    for svc in service_mappings:
        if "k8s-lcm" in svc.service:
            return svc.ip_address

    return None


async def get_ipamhost_details(
    k8s_adapter: KubernetesAdapter,
    input_data: GetIpamHostDetailsInput,
    context: UserContext | None = None,
    audit_logger: AuditLogger | None = None,
) -> GetIpamHostDetailsOutput:
    """Get detailed network configuration for a node.

    This tool retrieves comprehensive network information from the IpamHost
    resource which is automatically created when a Machine is applied.
    Includes IP addresses, netplan configuration, NIC mappings, bonds,
    bridges, VLANs, and service mappings.

    Args:
        k8s_adapter: Kubernetes adapter for API operations.
        input_data: Input parameters specifying which IpamHost to retrieve.
        context: User context for RBAC (optional).
        audit_logger: Audit logger for tracking operations (optional).

    Returns:
        GetIpamHostDetailsOutput with complete network configuration.

    Raises:
        ResourceNotFoundError: If the IpamHost does not exist.
        KubernetesError: If the Kubernetes API call fails.

    Example:
        >>> async with KubernetesAdapter() as k8s:
        ...     result = await get_ipamhost_details(
        ...         k8s, GetIpamHostDetailsInput(name="compute-01", namespace="lab")
        ...     )
        ...     print(f"Primary IP: {result.primary_ip}")
        ...     for bridge in result.bridges:
        ...         print(f"  {bridge.name}: {bridge.addresses}")
    """
    logger.info(
        "getting_ipamhost_details",
        name=input_data.name,
        namespace=input_data.namespace,
    )

    async with audit_tool_execution(
        TOOL_NAME,
        audit_logger,
        context,
        AuditLevel.READ,
        {
            "resource_type": "IpamHost",
            "resource_name": input_data.name,
            "resource_namespace": input_data.namespace,
        },
    ):
        try:
            # Get the IpamHost resource
            ipamhost_data = await k8s_adapter.get_custom_resource(
                group="ipam.mirantis.com",
                version="v1alpha1",
                plural="ipamhosts",
                name=input_data.name,
                namespace=input_data.namespace,
            )

            metadata = ipamhost_data.get("metadata", {})
            spec = ipamhost_data.get("spec", {})
            status = ipamhost_data.get("status", {})
            labels = metadata.get("labels", {})

            # Extract NIC mappings
            nic_mappings = _extract_nic_mappings(spec)

            # Extract network configuration
            bonds, bridges, vlans, netplan = _extract_netplan_config(status)

            # Extract service mappings
            service_mappings = _extract_service_mappings(status)

            # Get primary IP
            primary_ip = _get_primary_ip(nic_mappings, service_mappings)

            # Get L2Template reference
            l2_template_ref = status.get("l2TemplateRef")
            l2_template_selector = spec.get("l2TemplateSelector", {}).get("label")

            # Get cluster name from labels
            cluster_name = labels.get("cluster.sigs.k8s.io/cluster-name")

            output = GetIpamHostDetailsOutput(
                name=input_data.name,
                namespace=input_data.namespace,
                state=status.get("state", "Unknown"),
                l2_template_ref=l2_template_ref,
                l2_template_selector=l2_template_selector,
                nic_mappings=nic_mappings,
                bonds=bonds,
                bridges=bridges,
                vlans=vlans,
                service_mappings=service_mappings,
                primary_ip=primary_ip,
                netplan_config=netplan if input_data.include_netplan else None,
                labels=labels,
                cluster_name=cluster_name,
                creation_timestamp=metadata.get("creationTimestamp"),
            )

            logger.info(
                "ipamhost_details_retrieved",
                name=input_data.name,
                state=output.state,
                primary_ip=primary_ip,
                nic_count=len(nic_mappings),
                bridge_count=len(bridges),
            )

            return output

        except ResourceNotFoundError:
            logger.warning(
                "ipamhost_not_found",
                name=input_data.name,
                namespace=input_data.namespace,
            )
            raise

        except Exception as e:
            logger.error(
                "get_ipamhost_details_failed",
                name=input_data.name,
                error=str(e),
            )

            if isinstance(e, (KubernetesError, ResourceNotFoundError)):
                raise
            raise KubernetesError(
                f"Failed to get IpamHost details: {e}",
                operation="get",
                resource_kind="IpamHost",
                resource_name=input_data.name,
                namespace=input_data.namespace,
            ) from e
