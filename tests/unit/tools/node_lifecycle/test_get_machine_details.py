"""Unit tests for get_machine_details tool."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from mosk_mcp.core.exceptions import ResourceNotFoundError
from mosk_mcp.tools.node_lifecycle.get_machine_details import (
    GetMachineDetailsInput,
    GetMachineDetailsOutput,
    MachineCondition,
    MachineEvent,
    RelatedResource,
    _parse_conditions,
    get_machine_details,
)


SAMPLE_MACHINE_DATA = {
    "apiVersion": "kaas.mirantis.com/v1alpha1",
    "kind": "Machine",
    "metadata": {
        "name": "compute-01",
        "namespace": "default",
        "labels": {
            "openstack-compute-node": "enabled",
            "kaas.mirantis.com/provider": "baremetal",
            "kaas.mirantis.com/ipam-host": "compute-01-ipam",
        },
        "annotations": {
            "description": "Test compute node",
        },
        "creationTimestamp": "2024-01-15T10:00:00Z",
    },
    "spec": {
        "providerSpec": {
            "value": {
                "bareMetalHostProfile": "compute-profile",
                "hostRepositories": ["apt-repo"],
            },
        },
    },
    "status": {
        "phase": "Running",
        "addresses": [
            {"type": "InternalIP", "address": "192.168.1.100"},
            {"type": "Hostname", "address": "compute-01.local"},
        ],
        "nodeRef": {"name": "compute-01"},
        "conditions": [
            {
                "type": "Ready",
                "status": "True",
                "reason": "NodeReady",
                "message": "Node is ready",
                "lastTransitionTime": "2024-01-15T10:05:00Z",
            },
            {
                "type": "Provisioned",
                "status": "True",
                "reason": "ProvisioningSucceeded",
                "message": "Machine provisioned",
                "lastTransitionTime": "2024-01-15T10:03:00Z",
            },
        ],
        "providerStatus": {
            "instanceState": "running",
        },
    },
}


SAMPLE_FAILED_MACHINE_DATA = {
    "apiVersion": "kaas.mirantis.com/v1alpha1",
    "kind": "Machine",
    "metadata": {
        "name": "compute-02",
        "namespace": "default",
        "labels": {
            "openstack-compute-node": "enabled",
        },
    },
    "spec": {
        "providerSpec": {
            "value": {
                "bareMetalHostProfile": "compute-profile",
            },
        },
    },
    "status": {
        "phase": "Failed",
        "errorReason": "ProvisioningFailed",
        "errorMessage": "Hardware error during provisioning",
        "conditions": [
            {
                "type": "Ready",
                "status": "False",
                "reason": "ProvisioningFailed",
                "message": "Failed to provision machine",
            },
        ],
    },
}


class TestParseConditions:
    """Tests for _parse_conditions function."""

    def test_parse_conditions(self):
        """Test parsing conditions from API response."""
        conditions_data = [
            {
                "type": "Ready",
                "status": "True",
                "reason": "NodeReady",
                "message": "Node is ready",
                "lastTransitionTime": "2024-01-15T10:05:00Z",
            },
            {
                "type": "Provisioned",
                "status": "True",
                "reason": "ProvisioningSucceeded",
            },
        ]

        conditions = _parse_conditions(conditions_data)

        assert len(conditions) == 2
        assert conditions[0].type == "Ready"
        assert conditions[0].status == "True"
        assert conditions[0].reason == "NodeReady"
        assert conditions[0].message == "Node is ready"
        assert conditions[0].last_transition_time == "2024-01-15T10:05:00Z"

        assert conditions[1].type == "Provisioned"
        assert conditions[1].reason == "ProvisioningSucceeded"
        assert conditions[1].message is None

    def test_parse_empty_conditions(self):
        """Test parsing empty conditions list."""
        conditions = _parse_conditions([])
        assert conditions == []

    def test_parse_condition_missing_fields(self):
        """Test parsing condition with missing optional fields."""
        conditions_data = [
            {
                "type": "Unknown",
                "status": "Unknown",
            },
        ]

        conditions = _parse_conditions(conditions_data)

        assert len(conditions) == 1
        assert conditions[0].type == "Unknown"
        assert conditions[0].status == "Unknown"
        assert conditions[0].reason is None
        assert conditions[0].message is None


class TestGetMachineDetails:
    """Tests for get_machine_details function."""

    @pytest.fixture
    def mock_k8s_adapter(self):
        """Create a mock Kubernetes adapter."""
        adapter = MagicMock()
        adapter.get_machine = AsyncMock(return_value=SAMPLE_MACHINE_DATA)
        adapter.list = AsyncMock(return_value=[])
        adapter.get = AsyncMock(
            return_value={
                "status": {
                    "conditions": [{"type": "Ready", "status": "True"}],
                    "nodeInfo": {
                        "kubeletVersion": "v1.28.0",
                        "osImage": "Ubuntu 22.04",
                    },
                },
            }
        )
        adapter.get_custom_resource = AsyncMock(
            return_value={
                "metadata": {"name": "test", "labels": {"role": "compute"}},
                "status": {"operationalStatus": "Active", "powerState": "on"},
            }
        )
        # Mock auto-discovery to return None (use default namespace)
        adapter.get_mosk_machines_namespace = AsyncMock(return_value=None)
        return adapter

    @pytest.mark.asyncio
    async def test_get_machine_details_basic(self, mock_k8s_adapter):
        """Test getting basic machine details."""
        input_data = GetMachineDetailsInput(
            name="compute-01",
            namespace="default",
            include_events=False,
            include_related=False,
        )

        result = await get_machine_details(mock_k8s_adapter, input_data)

        assert isinstance(result, GetMachineDetailsOutput)
        assert result.name == "compute-01"
        assert result.namespace == "default"
        assert result.role == "compute"
        assert result.phase == "Running"
        assert result.api_version == "kaas.mirantis.com/v1alpha1"

    @pytest.mark.asyncio
    async def test_get_machine_details_with_conditions(self, mock_k8s_adapter):
        """Test machine details include conditions."""
        input_data = GetMachineDetailsInput(
            name="compute-01",
            include_events=False,
            include_related=False,
        )

        result = await get_machine_details(mock_k8s_adapter, input_data)

        assert len(result.conditions) == 2
        assert result.conditions[0].type == "Ready"
        assert result.conditions[0].status == "True"

    @pytest.mark.asyncio
    async def test_get_machine_details_with_addresses(self, mock_k8s_adapter):
        """Test machine details include addresses."""
        input_data = GetMachineDetailsInput(
            name="compute-01",
            include_events=False,
            include_related=False,
        )

        result = await get_machine_details(mock_k8s_adapter, input_data)

        assert len(result.addresses) == 2
        assert any(addr["type"] == "InternalIP" for addr in result.addresses)

    @pytest.mark.asyncio
    async def test_get_machine_details_with_events(self, mock_k8s_adapter):
        """Test getting machine details with events."""
        # Mock events
        mock_k8s_adapter.list = AsyncMock(
            return_value=[
                {
                    "type": "Normal",
                    "reason": "Created",
                    "message": "Machine created",
                    "count": 1,
                    "firstTimestamp": "2024-01-15T10:00:00Z",
                    "lastTimestamp": "2024-01-15T10:00:00Z",
                    "source": {"component": "machine-controller"},
                },
            ]
        )

        input_data = GetMachineDetailsInput(
            name="compute-01",
            include_events=True,
            include_related=False,
        )

        result = await get_machine_details(mock_k8s_adapter, input_data)

        assert len(result.events) == 1
        assert result.events[0].type == "Normal"
        assert result.events[0].reason == "Created"

    @pytest.mark.asyncio
    async def test_get_machine_details_with_related(self, mock_k8s_adapter):
        """Test getting machine details with related resources."""
        input_data = GetMachineDetailsInput(
            name="compute-01",
            include_events=False,
            include_related=True,
        )

        result = await get_machine_details(mock_k8s_adapter, input_data)

        # Should have checked for BMHp, BMHi, Node, and IpamHost
        assert len(result.related_resources) > 0

    @pytest.mark.asyncio
    async def test_get_machine_details_failed_machine(self, mock_k8s_adapter):
        """Test getting details of a failed machine."""
        mock_k8s_adapter.get_machine = AsyncMock(return_value=SAMPLE_FAILED_MACHINE_DATA)

        input_data = GetMachineDetailsInput(
            name="compute-02",
            include_events=False,
            include_related=False,
        )

        result = await get_machine_details(mock_k8s_adapter, input_data)

        assert result.phase == "Failed"
        assert result.error_info is not None
        assert result.error_info["reason"] == "ProvisioningFailed"
        assert "Hardware error" in result.error_info["message"]

    @pytest.mark.asyncio
    async def test_get_machine_details_not_found(self, mock_k8s_adapter):
        """Test getting details of non-existent machine."""
        mock_k8s_adapter.get_machine = AsyncMock(
            side_effect=ResourceNotFoundError(
                "Machine not found",
                resource_type="Machine",
                resource_id="nonexistent",
            )
        )

        input_data = GetMachineDetailsInput(
            name="nonexistent",
        )

        with pytest.raises(ResourceNotFoundError):
            await get_machine_details(mock_k8s_adapter, input_data)

    @pytest.mark.asyncio
    async def test_get_machine_details_labels_and_annotations(self, mock_k8s_adapter):
        """Test that labels and annotations are included."""
        input_data = GetMachineDetailsInput(
            name="compute-01",
            include_events=False,
            include_related=False,
        )

        result = await get_machine_details(mock_k8s_adapter, input_data)

        assert "openstack-compute-node" in result.labels
        assert "description" in result.annotations

    @pytest.mark.asyncio
    async def test_get_machine_details_spec_and_status(self, mock_k8s_adapter):
        """Test that full spec and status are included."""
        input_data = GetMachineDetailsInput(
            name="compute-01",
            include_events=False,
            include_related=False,
        )

        result = await get_machine_details(mock_k8s_adapter, input_data)

        assert "providerSpec" in result.spec
        assert "phase" in result.status


class TestGetMachineDetailsInput:
    """Tests for GetMachineDetailsInput validation."""

    def test_required_name(self):
        """Test that name is required."""
        with pytest.raises(ValueError):
            GetMachineDetailsInput(name="")

    def test_default_values(self):
        """Test default values."""
        input_data = GetMachineDetailsInput(name="test-machine")

        assert input_data.namespace == "default"
        assert input_data.include_events is True
        assert input_data.include_related is True

    def test_custom_namespace(self):
        """Test custom namespace."""
        input_data = GetMachineDetailsInput(
            name="test-machine",
            namespace="production",
        )

        assert input_data.namespace == "production"


class TestMachineCondition:
    """Tests for MachineCondition model."""

    def test_condition_fields(self):
        """Test condition model fields."""
        condition = MachineCondition(
            type="Ready",
            status="True",
            reason="NodeReady",
            message="Node is ready",
            last_transition_time="2024-01-15T10:00:00Z",
        )

        assert condition.type == "Ready"
        assert condition.status == "True"
        assert condition.reason == "NodeReady"
        assert condition.message == "Node is ready"

    def test_condition_optional_fields(self):
        """Test condition with optional fields."""
        condition = MachineCondition(
            type="Unknown",
            status="Unknown",
        )

        assert condition.reason is None
        assert condition.message is None
        assert condition.last_transition_time is None


class TestMachineEvent:
    """Tests for MachineEvent model."""

    def test_event_fields(self):
        """Test event model fields."""
        event = MachineEvent(
            type="Normal",
            reason="Created",
            message="Machine created",
            count=1,
            first_timestamp="2024-01-15T10:00:00Z",
            last_timestamp="2024-01-15T10:00:00Z",
            source="machine-controller",
        )

        assert event.type == "Normal"
        assert event.reason == "Created"
        assert event.count == 1

    def test_event_default_count(self):
        """Test event default count."""
        event = MachineEvent(
            type="Warning",
            reason="Error",
            message="Something went wrong",
        )

        assert event.count == 1


class TestRelatedResource:
    """Tests for RelatedResource model."""

    def test_related_resource_exists(self):
        """Test related resource that exists."""
        resource = RelatedResource(
            kind="BareMetalHostProfile",
            name="compute-profile",
            namespace="default",
            exists=True,
            status="Available",
            details={"role": "compute"},
        )

        assert resource.exists is True
        assert resource.status == "Available"

    def test_related_resource_missing(self):
        """Test related resource that doesn't exist."""
        resource = RelatedResource(
            kind="BareMetalHostInventory",
            name="missing-bmhi",
            namespace="default",
            exists=False,
            status="Missing",
        )

        assert resource.exists is False
        assert resource.status == "Missing"
