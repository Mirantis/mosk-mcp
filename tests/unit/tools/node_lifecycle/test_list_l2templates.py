"""Unit tests for list_l2templates tool."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from mosk_mcp.tools.node_lifecycle.list_l2templates import (
    L2TemplateSummary,
    ListL2TemplatesInput,
    ListL2TemplatesOutput,
    _extract_l2template_summary,
    list_l2templates,
)


# Sample L2Template data for testing (based on real MOSK cluster data)
SAMPLE_L2TEMPLATE_DEFAULT = {
    "apiVersion": "ipam.mirantis.com/v1alpha1",
    "kind": "L2Template",
    "metadata": {
        "name": "default",
        "namespace": "default",
        "creationTimestamp": "2024-01-15T10:00:00Z",
        "labels": {
            "cluster.sigs.k8s.io/cluster-name": "kaas-mgmt",
            "ipam/DefaultForCluster": "1",
            "kaas.mirantis.com/provider": "baremetal",
        },
    },
    "spec": {
        "autoIfMappingPrio": ["provision", "eno", "ens", "enp"],
        "l3Layout": [
            {"scope": "namespace", "subnetName": "kaas-mgmt"},
            {"scope": "namespace", "subnetName": "mgmt-pxe"},
        ],
        "npTemplate": "version: 2\nethernets: ...",
    },
    "status": {
        "state": "OK",
        "checksums": {
            "spec": "sha256:abc123",
        },
    },
}


SAMPLE_L2TEMPLATE_COMPUTE = {
    "apiVersion": "ipam.mirantis.com/v1alpha1",
    "kind": "L2Template",
    "metadata": {
        "name": "compute-l2",
        "namespace": "lab",
        "creationTimestamp": "2024-01-15T11:00:00Z",
        "labels": {
            "cluster.sigs.k8s.io/cluster-name": "mos",
            "role": "compute",
        },
    },
    "spec": {
        "ifMapping": ["enp9s0f0", "enp9s0f1"],
        "bonds": {
            "bond0": {
                "interfaces": ["enp9s0f0", "enp9s0f1"],
                "mode": "802.3ad",
            },
        },
        "vlans": {
            "vlan1722": {"id": 1722, "link": "bond0"},
            "vlan1723": {"id": 1723, "link": "bond0"},
        },
        "bridges": {
            "k8s-lcm": {"interfaces": ["vlan1722"]},
            "k8s-storage": {"interfaces": ["vlan1723"]},
        },
        "l3Layout": [],
        "npTemplate": "version: 2\n...",
    },
    "status": {
        "state": "OK",
    },
}


SAMPLE_L2TEMPLATE_ERROR = {
    "apiVersion": "ipam.mirantis.com/v1alpha1",
    "kind": "L2Template",
    "metadata": {
        "name": "broken-l2",
        "namespace": "default",
        "creationTimestamp": "2024-01-15T09:00:00Z",
    },
    "spec": {},
    "status": {
        "state": "Error",
        "errorMessage": "Invalid template configuration",
    },
}


class TestExtractL2TemplateSummary:
    """Tests for _extract_l2template_summary function."""

    def test_extract_default_template(self):
        """Test extracting summary from default L2Template."""
        summary = _extract_l2template_summary(SAMPLE_L2TEMPLATE_DEFAULT)

        assert summary.name == "default"
        assert summary.namespace == "default"
        assert summary.state == "OK"
        assert summary.labels["ipam/DefaultForCluster"] == "1"
        assert summary.bond_count == 0
        assert summary.bridge_count == 0
        assert summary.vlan_count == 0

    def test_extract_compute_template(self):
        """Test extracting summary from compute L2Template with bonds/vlans/bridges."""
        summary = _extract_l2template_summary(SAMPLE_L2TEMPLATE_COMPUTE)

        assert summary.name == "compute-l2"
        assert summary.namespace == "lab"
        assert summary.state == "OK"
        assert summary.bond_count == 1
        assert summary.bridge_count == 2
        assert summary.vlan_count == 2
        assert summary.labels.get("role") == "compute"

    def test_extract_error_template(self):
        """Test extracting summary from error state template."""
        summary = _extract_l2template_summary(SAMPLE_L2TEMPLATE_ERROR)

        assert summary.name == "broken-l2"
        assert summary.state == "Error"
        assert summary.bond_count == 0

    def test_extract_minimal_template(self):
        """Test extracting summary from minimal template data."""
        minimal_template = {
            "metadata": {"name": "minimal-l2"},
            "spec": {},
            "status": {},
        }
        summary = _extract_l2template_summary(minimal_template)

        assert summary.name == "minimal-l2"
        assert summary.namespace == "default"
        assert summary.state == "Unknown"
        assert summary.bond_count == 0


class TestListL2Templates:
    """Tests for list_l2templates function."""

    @pytest.fixture
    def mock_k8s_adapter(self):
        """Create a mock Kubernetes adapter."""
        adapter = MagicMock()
        adapter.list_custom_resources = AsyncMock(
            return_value=[
                SAMPLE_L2TEMPLATE_DEFAULT,
                SAMPLE_L2TEMPLATE_COMPUTE,
                SAMPLE_L2TEMPLATE_ERROR,
            ]
        )
        return adapter

    @pytest.mark.asyncio
    async def test_list_l2templates_default(self, mock_k8s_adapter):
        """Test listing L2Templates with default parameters."""
        input_data = ListL2TemplatesInput()

        result = await list_l2templates(mock_k8s_adapter, input_data)

        assert isinstance(result, ListL2TemplatesOutput)
        assert result.total_count == 3
        assert result.namespace == "default"
        assert len(result.templates) == 3

    @pytest.mark.asyncio
    async def test_list_l2templates_with_limit(self, mock_k8s_adapter):
        """Test listing L2Templates with limit."""
        input_data = ListL2TemplatesInput(limit=2)

        result = await list_l2templates(mock_k8s_adapter, input_data)

        assert len(result.templates) <= 2

    @pytest.mark.asyncio
    async def test_list_l2templates_all_namespaces(self, mock_k8s_adapter):
        """Test listing L2Templates in all namespaces."""
        input_data = ListL2TemplatesInput(namespace="*")

        await list_l2templates(mock_k8s_adapter, input_data)

        mock_k8s_adapter.list_custom_resources.assert_called_once()
        call_args = mock_k8s_adapter.list_custom_resources.call_args
        assert call_args.kwargs["namespace"] is None

    @pytest.mark.asyncio
    async def test_list_l2templates_with_label_selector(self, mock_k8s_adapter):
        """Test listing L2Templates with label selector."""
        input_data = ListL2TemplatesInput(label_selector="role=compute")

        await list_l2templates(mock_k8s_adapter, input_data)

        mock_k8s_adapter.list_custom_resources.assert_called_once()
        call_args = mock_k8s_adapter.list_custom_resources.call_args
        assert call_args.kwargs["label_selector"] == "role=compute"

    @pytest.mark.asyncio
    async def test_list_l2templates_correct_api_group(self, mock_k8s_adapter):
        """Test that list_l2templates uses correct API group."""
        input_data = ListL2TemplatesInput()

        await list_l2templates(mock_k8s_adapter, input_data)

        mock_k8s_adapter.list_custom_resources.assert_called_once()
        call_args = mock_k8s_adapter.list_custom_resources.call_args
        assert call_args.kwargs["group"] == "ipam.mirantis.com"
        assert call_args.kwargs["version"] == "v1alpha1"
        assert call_args.kwargs["plural"] == "l2templates"

    @pytest.mark.asyncio
    async def test_list_l2templates_empty_result(self):
        """Test listing L2Templates when none exist."""
        mock_adapter = MagicMock()
        mock_adapter.list_custom_resources = AsyncMock(return_value=[])

        input_data = ListL2TemplatesInput()

        result = await list_l2templates(mock_adapter, input_data)

        assert result.total_count == 0
        assert result.templates == []

    @pytest.mark.asyncio
    async def test_list_l2templates_error_handling(self):
        """Test error handling when Kubernetes API fails."""
        mock_adapter = MagicMock()
        mock_adapter.list_custom_resources = AsyncMock(
            side_effect=Exception("API connection failed")
        )

        input_data = ListL2TemplatesInput()

        with pytest.raises(Exception) as exc_info:
            await list_l2templates(mock_adapter, input_data)

        assert "L2Templates" in str(exc_info.value) or "API" in str(exc_info.value)


class TestListL2TemplatesInput:
    """Tests for ListL2TemplatesInput validation."""

    def test_default_values(self):
        """Test default input values."""
        input_data = ListL2TemplatesInput()

        assert input_data.namespace == "default"
        assert input_data.label_selector is None
        assert input_data.limit == 50

    def test_custom_namespace(self):
        """Test custom namespace input."""
        input_data = ListL2TemplatesInput(namespace="lab")

        assert input_data.namespace == "lab"

    def test_limit_validation(self):
        """Test limit validation."""
        # Valid limit
        input_data = ListL2TemplatesInput(limit=25)
        assert input_data.limit == 25

        # Invalid limit (too low)
        with pytest.raises(ValueError):
            ListL2TemplatesInput(limit=0)

        # Invalid limit (too high)
        with pytest.raises(ValueError):
            ListL2TemplatesInput(limit=201)


class TestL2TemplateSummary:
    """Tests for L2TemplateSummary model."""

    def test_required_fields(self):
        """Test that required fields are validated."""
        summary = L2TemplateSummary(
            name="test-l2",
            namespace="default",
            state="OK",
        )

        assert summary.name == "test-l2"
        assert summary.namespace == "default"
        assert summary.state == "OK"

    def test_optional_fields_defaults(self):
        """Test optional fields have correct defaults."""
        summary = L2TemplateSummary(
            name="test-l2",
            namespace="default",
            state="OK",
        )

        assert summary.network_count == 0
        assert summary.bond_count == 0
        assert summary.bridge_count == 0
        assert summary.vlan_count == 0
        assert summary.labels == {}
        assert summary.age_seconds is None

    def test_network_counts(self):
        """Test network element counts."""
        summary = L2TemplateSummary(
            name="test-l2",
            namespace="default",
            state="OK",
            bond_count=2,
            bridge_count=4,
            vlan_count=3,
        )

        assert summary.bond_count == 2
        assert summary.bridge_count == 4
        assert summary.vlan_count == 3
