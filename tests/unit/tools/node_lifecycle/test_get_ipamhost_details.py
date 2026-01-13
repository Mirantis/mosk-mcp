"""Unit tests for get_ipamhost_details tool."""

from typing import Any
from unittest.mock import AsyncMock

import pytest

from mosk_mcp.core.exceptions import KubernetesError, ResourceNotFoundError
from mosk_mcp.tools.node_lifecycle.get_ipamhost_details import (
    GetIpamHostDetailsInput,
    GetIpamHostDetailsOutput,
    NetworkBond,
    NetworkBridge,
    NetworkVLAN,
    NICMapping,
    ServiceMapping,
    _extract_netplan_config,
    _extract_nic_mappings,
    _extract_service_mappings,
    _get_primary_ip,
    get_ipamhost_details,
)


# =============================================================================
# Tests for Input/Output Models
# =============================================================================


class TestGetIpamHostDetailsInput:
    """Tests for GetIpamHostDetailsInput model."""

    def test_required_name(self) -> None:
        """Test name is required."""
        with pytest.raises(ValueError):
            GetIpamHostDetailsInput()

    def test_default_namespace(self) -> None:
        """Test default namespace is 'default'."""
        input_data = GetIpamHostDetailsInput(name="compute-01")
        assert input_data.namespace == "default"

    def test_default_include_netplan(self) -> None:
        """Test default include_netplan is True."""
        input_data = GetIpamHostDetailsInput(name="compute-01")
        assert input_data.include_netplan is True

    def test_custom_values(self) -> None:
        """Test custom values."""
        input_data = GetIpamHostDetailsInput(
            name="compute-01",
            namespace="lab",
            include_netplan=False,
        )
        assert input_data.name == "compute-01"
        assert input_data.namespace == "lab"
        assert input_data.include_netplan is False

    def test_name_min_length(self) -> None:
        """Test name minimum length."""
        with pytest.raises(ValueError):
            GetIpamHostDetailsInput(name="")

    def test_name_max_length(self) -> None:
        """Test name maximum length."""
        long_name = "a" * 253
        input_data = GetIpamHostDetailsInput(name=long_name)
        assert len(input_data.name) == 253

        with pytest.raises(ValueError):
            GetIpamHostDetailsInput(name="a" * 254)


class TestNICMapping:
    """Tests for NICMapping model."""

    def test_required_fields(self) -> None:
        """Test required fields."""
        nic = NICMapping(name="eno1", mac="aa:bb:cc:dd:ee:ff")
        assert nic.name == "eno1"
        assert nic.mac == "aa:bb:cc:dd:ee:ff"

    def test_defaults(self) -> None:
        """Test default values."""
        nic = NICMapping(name="eno1", mac="aa:bb:cc:dd:ee:ff")
        assert nic.ip is None
        assert nic.primary is False

    def test_all_fields(self) -> None:
        """Test all fields."""
        nic = NICMapping(
            name="eno1",
            mac="aa:bb:cc:dd:ee:ff",
            ip="10.0.0.1",
            primary=True,
        )
        assert nic.ip == "10.0.0.1"
        assert nic.primary is True


class TestNetworkBridge:
    """Tests for NetworkBridge model."""

    def test_required_fields(self) -> None:
        """Test required fields."""
        bridge = NetworkBridge(name="k8s-lcm")
        assert bridge.name == "k8s-lcm"

    def test_defaults(self) -> None:
        """Test default values."""
        bridge = NetworkBridge(name="k8s-lcm")
        assert bridge.addresses == []
        assert bridge.interfaces == []
        assert bridge.gateway is None
        assert bridge.nameservers == []

    def test_all_fields(self) -> None:
        """Test all fields."""
        bridge = NetworkBridge(
            name="k8s-lcm",
            addresses=["10.0.0.1/24"],
            interfaces=["bond0"],
            gateway="10.0.0.254",
            nameservers=["8.8.8.8", "8.8.4.4"],
        )
        assert bridge.addresses == ["10.0.0.1/24"]
        assert bridge.interfaces == ["bond0"]
        assert bridge.gateway == "10.0.0.254"
        assert bridge.nameservers == ["8.8.8.8", "8.8.4.4"]


class TestNetworkBond:
    """Tests for NetworkBond model."""

    def test_required_fields(self) -> None:
        """Test required fields."""
        bond = NetworkBond(name="bond0", mode="802.3ad")
        assert bond.name == "bond0"
        assert bond.mode == "802.3ad"

    def test_defaults(self) -> None:
        """Test default values."""
        bond = NetworkBond(name="bond0", mode="802.3ad")
        assert bond.interfaces == []

    def test_all_fields(self) -> None:
        """Test all fields."""
        bond = NetworkBond(
            name="bond0",
            mode="802.3ad",
            interfaces=["eno1", "eno2"],
        )
        assert bond.interfaces == ["eno1", "eno2"]


class TestNetworkVLAN:
    """Tests for NetworkVLAN model."""

    def test_required_fields(self) -> None:
        """Test required fields."""
        vlan = NetworkVLAN(name="vlan1722", id=1722, link="bond0")
        assert vlan.name == "vlan1722"
        assert vlan.id == 1722
        assert vlan.link == "bond0"


class TestServiceMapping:
    """Tests for ServiceMapping model."""

    def test_required_fields(self) -> None:
        """Test required fields."""
        mapping = ServiceMapping(
            service="ipam/SVC-k8s-lcm",
            interface="k8s-lcm",
            ip_address="10.0.0.1",
        )
        assert mapping.service == "ipam/SVC-k8s-lcm"
        assert mapping.interface == "k8s-lcm"
        assert mapping.ip_address == "10.0.0.1"


class TestGetIpamHostDetailsOutput:
    """Tests for GetIpamHostDetailsOutput model."""

    def test_required_fields(self) -> None:
        """Test required fields."""
        output = GetIpamHostDetailsOutput(
            name="compute-01",
            namespace="default",
            state="OK",
        )
        assert output.name == "compute-01"
        assert output.namespace == "default"
        assert output.state == "OK"

    def test_defaults(self) -> None:
        """Test default values."""
        output = GetIpamHostDetailsOutput(
            name="compute-01",
            namespace="default",
            state="OK",
        )
        assert output.l2_template_ref is None
        assert output.l2_template_selector is None
        assert output.nic_mappings == []
        assert output.bonds == []
        assert output.bridges == []
        assert output.vlans == []
        assert output.service_mappings == []
        assert output.primary_ip is None
        assert output.netplan_config is None
        assert output.labels == {}
        assert output.cluster_name is None
        assert output.creation_timestamp is None


# =============================================================================
# Tests for Helper Functions
# =============================================================================


class TestExtractNicMappings:
    """Tests for _extract_nic_mappings function."""

    def test_empty_spec(self) -> None:
        """Test with empty spec."""
        result = _extract_nic_mappings({})
        assert result == []

    def test_with_nic_mappings(self) -> None:
        """Test with NIC mappings."""
        spec: dict[str, Any] = {
            "nicMACmap": [
                {"name": "eno1", "mac": "aa:bb:cc:dd:ee:ff", "primary": True},
                {"name": "eno2", "mac": "11:22:33:44:55:66"},
            ]
        }

        result = _extract_nic_mappings(spec)

        assert len(result) == 2
        assert result[0].name == "eno1"
        assert result[0].mac == "aa:bb:cc:dd:ee:ff"
        assert result[0].primary is True
        assert result[1].name == "eno2"
        assert result[1].primary is False

    def test_with_ip(self) -> None:
        """Test NIC mapping with IP."""
        spec: dict[str, Any] = {
            "nicMACmap": [
                {"name": "eno1", "mac": "aa:bb:cc:dd:ee:ff", "ip": "10.0.0.1"},
            ]
        }

        result = _extract_nic_mappings(spec)

        assert result[0].ip == "10.0.0.1"


class TestExtractNetplanConfig:
    """Tests for _extract_netplan_config function."""

    def test_empty_status(self) -> None:
        """Test with empty status."""
        bonds, bridges, vlans, netplan = _extract_netplan_config({})

        assert bonds == []
        assert bridges == []
        assert vlans == []
        assert netplan is None

    def test_with_bonds(self) -> None:
        """Test extracting bonds."""
        status: dict[str, Any] = {
            "netconfigCandidate": {
                "bonds": {
                    "bond0": {
                        "interfaces": ["eno1", "eno2"],
                        "parameters": {"mode": "802.3ad"},
                    }
                }
            }
        }

        bonds, _bridges, _vlans, _netplan = _extract_netplan_config(status)

        assert len(bonds) == 1
        assert bonds[0].name == "bond0"
        assert bonds[0].mode == "802.3ad"
        assert bonds[0].interfaces == ["eno1", "eno2"]

    def test_with_bridges(self) -> None:
        """Test extracting bridges."""
        status: dict[str, Any] = {
            "netconfigCandidate": {
                "bridges": {
                    "k8s-lcm": {
                        "addresses": ["10.0.0.1/24"],
                        "interfaces": ["bond0"],
                        "gateway4": "10.0.0.254",
                        "nameservers": {"addresses": ["8.8.8.8"]},
                    }
                }
            }
        }

        _bonds, bridges, _vlans, _netplan = _extract_netplan_config(status)

        assert len(bridges) == 1
        assert bridges[0].name == "k8s-lcm"
        assert bridges[0].addresses == ["10.0.0.1/24"]
        assert bridges[0].interfaces == ["bond0"]
        assert bridges[0].gateway == "10.0.0.254"
        assert bridges[0].nameservers == ["8.8.8.8"]

    def test_with_vlans(self) -> None:
        """Test extracting VLANs."""
        status: dict[str, Any] = {
            "netconfigCandidate": {
                "vlans": {
                    "vlan1722": {"id": 1722, "link": "bond0"},
                }
            }
        }

        _bonds, _bridges, vlans, _netplan = _extract_netplan_config(status)

        assert len(vlans) == 1
        assert vlans[0].name == "vlan1722"
        assert vlans[0].id == 1722
        assert vlans[0].link == "bond0"

    def test_returns_full_netplan(self) -> None:
        """Test that full netplan is returned."""
        status: dict[str, Any] = {
            "netconfigCandidate": {
                "bonds": {},
                "bridges": {},
                "ethernets": {"eno1": {}},
            }
        }

        _, _, _, netplan = _extract_netplan_config(status)

        assert netplan is not None
        assert "ethernets" in netplan


class TestExtractServiceMappings:
    """Tests for _extract_service_mappings function."""

    def test_empty_status(self) -> None:
        """Test with empty status."""
        result = _extract_service_mappings({})
        assert result == []

    def test_with_service_mappings(self) -> None:
        """Test with service mappings."""
        status: dict[str, Any] = {
            "serviceMap": {
                "ipam/SVC-k8s-lcm": [
                    {"ifName": "k8s-lcm", "ipAddress": "10.0.0.1"},
                ],
                "ipam/SVC-storage": [
                    {"ifName": "storage", "ipAddress": "10.0.1.1"},
                    {"ifName": "storage-backend", "ipAddress": "10.0.2.1"},
                ],
            }
        }

        result = _extract_service_mappings(status)

        assert len(result) == 3
        services = {m.service for m in result}
        assert "ipam/SVC-k8s-lcm" in services
        assert "ipam/SVC-storage" in services


class TestGetPrimaryIp:
    """Tests for _get_primary_ip function."""

    def test_empty_lists(self) -> None:
        """Test with empty lists."""
        result = _get_primary_ip([], [])
        assert result is None

    def test_primary_nic_has_ip(self) -> None:
        """Test primary NIC with IP takes precedence."""
        nic_mappings = [
            NICMapping(name="eno1", mac="aa:bb:cc:dd:ee:ff", ip="10.0.0.1", primary=True),
            NICMapping(name="eno2", mac="11:22:33:44:55:66", ip="10.0.0.2"),
        ]
        service_mappings = [
            ServiceMapping(
                service="ipam/SVC-k8s-lcm",
                interface="k8s-lcm",
                ip_address="10.0.0.100",
            ),
        ]

        result = _get_primary_ip(nic_mappings, service_mappings)

        assert result == "10.0.0.1"

    def test_fallback_to_k8s_lcm_service(self) -> None:
        """Test fallback to k8s-lcm service."""
        nic_mappings = [
            NICMapping(name="eno1", mac="aa:bb:cc:dd:ee:ff"),  # No IP
        ]
        service_mappings = [
            ServiceMapping(
                service="ipam/SVC-k8s-lcm",
                interface="k8s-lcm",
                ip_address="10.0.0.100",
            ),
        ]

        result = _get_primary_ip(nic_mappings, service_mappings)

        assert result == "10.0.0.100"

    def test_no_primary_no_k8s_lcm(self) -> None:
        """Test returns None when no primary NIC and no k8s-lcm service."""
        nic_mappings = [
            NICMapping(name="eno1", mac="aa:bb:cc:dd:ee:ff"),
        ]
        service_mappings = [
            ServiceMapping(
                service="ipam/SVC-storage",
                interface="storage",
                ip_address="10.0.1.1",
            ),
        ]

        result = _get_primary_ip(nic_mappings, service_mappings)

        assert result is None


# =============================================================================
# Tests for get_ipamhost_details Function
# =============================================================================


class TestGetIpamHostDetails:
    """Tests for the get_ipamhost_details function."""

    @pytest.fixture
    def mock_k8s_adapter(self) -> AsyncMock:
        """Create a mock Kubernetes adapter."""
        adapter = AsyncMock()
        return adapter

    @pytest.fixture
    def sample_ipamhost(self) -> dict[str, Any]:
        """Create a sample IpamHost resource."""
        return {
            "apiVersion": "ipam.mirantis.com/v1alpha1",
            "kind": "IpamHost",
            "metadata": {
                "name": "compute-01",
                "namespace": "lab",
                "creationTimestamp": "2024-01-15T10:00:00Z",
                "labels": {
                    "cluster.sigs.k8s.io/cluster-name": "mos",
                    "kaas.mirantis.com/region": "region-one",
                },
            },
            "spec": {
                "nicMACmap": [
                    {
                        "name": "eno1",
                        "mac": "aa:bb:cc:dd:ee:ff",
                        "ip": "10.0.0.1",
                        "primary": True,
                    },
                    {"name": "eno2", "mac": "11:22:33:44:55:66"},
                ],
                "l2TemplateSelector": {"label": "compute-template"},
            },
            "status": {
                "state": "OK",
                "l2TemplateRef": "compute-l2template",
                "serviceMap": {
                    "ipam/SVC-k8s-lcm": [
                        {"ifName": "k8s-lcm", "ipAddress": "10.0.0.100"},
                    ],
                },
                "netconfigCandidate": {
                    "bonds": {
                        "bond0": {
                            "interfaces": ["eno1", "eno2"],
                            "parameters": {"mode": "802.3ad"},
                        }
                    },
                    "bridges": {
                        "k8s-lcm": {
                            "addresses": ["10.0.0.100/24"],
                            "interfaces": ["vlan100"],
                            "gateway4": "10.0.0.254",
                        }
                    },
                    "vlans": {
                        "vlan100": {"id": 100, "link": "bond0"},
                    },
                },
            },
        }

    @pytest.mark.asyncio
    async def test_success(
        self, mock_k8s_adapter: AsyncMock, sample_ipamhost: dict[str, Any]
    ) -> None:
        """Test successful IpamHost retrieval."""
        mock_k8s_adapter.get_custom_resource = AsyncMock(return_value=sample_ipamhost)

        result = await get_ipamhost_details(
            mock_k8s_adapter,
            GetIpamHostDetailsInput(name="compute-01", namespace="lab"),
        )

        assert result.name == "compute-01"
        assert result.namespace == "lab"
        assert result.state == "OK"
        assert result.l2_template_ref == "compute-l2template"
        assert result.l2_template_selector == "compute-template"
        assert result.cluster_name == "mos"
        assert result.creation_timestamp == "2024-01-15T10:00:00Z"

        # Check NIC mappings
        assert len(result.nic_mappings) == 2
        assert result.nic_mappings[0].primary is True

        # Check bonds
        assert len(result.bonds) == 1
        assert result.bonds[0].name == "bond0"
        assert result.bonds[0].mode == "802.3ad"

        # Check bridges
        assert len(result.bridges) == 1
        assert result.bridges[0].name == "k8s-lcm"

        # Check VLANs
        assert len(result.vlans) == 1
        assert result.vlans[0].id == 100

        # Check service mappings
        assert len(result.service_mappings) == 1
        assert result.service_mappings[0].ip_address == "10.0.0.100"

        # Check primary IP (from primary NIC)
        assert result.primary_ip == "10.0.0.1"

        # Check netplan is included
        assert result.netplan_config is not None

    @pytest.mark.asyncio
    async def test_without_netplan(
        self, mock_k8s_adapter: AsyncMock, sample_ipamhost: dict[str, Any]
    ) -> None:
        """Test IpamHost retrieval without netplan."""
        mock_k8s_adapter.get_custom_resource = AsyncMock(return_value=sample_ipamhost)

        result = await get_ipamhost_details(
            mock_k8s_adapter,
            GetIpamHostDetailsInput(name="compute-01", namespace="lab", include_netplan=False),
        )

        assert result.netplan_config is None

    @pytest.mark.asyncio
    async def test_not_found(self, mock_k8s_adapter: AsyncMock) -> None:
        """Test IpamHost not found."""
        mock_k8s_adapter.get_custom_resource = AsyncMock(
            side_effect=ResourceNotFoundError("ipamhosts/compute-01")
        )

        with pytest.raises(ResourceNotFoundError):
            await get_ipamhost_details(
                mock_k8s_adapter,
                GetIpamHostDetailsInput(name="compute-01"),
            )

    @pytest.mark.asyncio
    async def test_api_error(self, mock_k8s_adapter: AsyncMock) -> None:
        """Test API error handling."""
        mock_k8s_adapter.get_custom_resource = AsyncMock(
            side_effect=Exception("API connection failed")
        )

        with pytest.raises(KubernetesError) as exc_info:
            await get_ipamhost_details(
                mock_k8s_adapter,
                GetIpamHostDetailsInput(name="compute-01"),
            )

        assert "Failed to get IpamHost details" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_minimal_ipamhost(self, mock_k8s_adapter: AsyncMock) -> None:
        """Test with minimal IpamHost data."""
        minimal_ipamhost: dict[str, Any] = {
            "metadata": {"name": "compute-01", "namespace": "default"},
            "spec": {},
            "status": {"state": "Pending"},
        }
        mock_k8s_adapter.get_custom_resource = AsyncMock(return_value=minimal_ipamhost)

        result = await get_ipamhost_details(
            mock_k8s_adapter,
            GetIpamHostDetailsInput(name="compute-01"),
        )

        assert result.name == "compute-01"
        assert result.state == "Pending"
        assert result.nic_mappings == []
        assert result.bonds == []
        assert result.bridges == []
        assert result.vlans == []
        assert result.service_mappings == []
        assert result.primary_ip is None

    @pytest.mark.asyncio
    async def test_kubernetes_error_propagated(self, mock_k8s_adapter: AsyncMock) -> None:
        """Test KubernetesError is propagated."""
        mock_k8s_adapter.get_custom_resource = AsyncMock(
            side_effect=KubernetesError(
                "Forbidden",
                operation="get",
                resource_kind="IpamHost",
                resource_name="compute-01",
            )
        )

        with pytest.raises(KubernetesError) as exc_info:
            await get_ipamhost_details(
                mock_k8s_adapter,
                GetIpamHostDetailsInput(name="compute-01"),
            )

        assert "Forbidden" in str(exc_info.value)
