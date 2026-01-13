"""Unit tests for list_machines tool."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from mosk_mcp.tools.node_lifecycle.list_machines import (
    ListMachinesInput,
    ListMachinesOutput,
    MachinePhaseFilter,
    MachineRoleFilter,
    MachineSummary,
    _apply_filters,
    _extract_machine_summary,
    _generate_summary,
    list_machines,
)


# Sample machine data for testing
SAMPLE_MACHINE_DATA = {
    "apiVersion": "kaas.mirantis.com/v1alpha1",
    "kind": "Machine",
    "metadata": {
        "name": "compute-01",
        "namespace": "default",
        "labels": {
            "openstack-compute-node": "enabled",
            "kaas.mirantis.com/provider": "baremetal",
        },
        "creationTimestamp": "2024-01-15T10:00:00Z",
    },
    "spec": {
        "providerSpec": {
            "value": {
                "bareMetalHostProfile": "compute-profile",
            },
        },
    },
    "status": {
        "phase": "Ready",
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
            },
        ],
    },
}


SAMPLE_CONTROL_MACHINE_DATA = {
    "apiVersion": "kaas.mirantis.com/v1alpha1",
    "kind": "Machine",
    "metadata": {
        "name": "control-01",
        "namespace": "default",
        "labels": {
            "openstack-control-plane": "enabled",
            "kaas.mirantis.com/provider": "baremetal",
        },
        "creationTimestamp": "2024-01-15T09:00:00Z",
    },
    "spec": {
        "providerSpec": {
            "value": {
                "bareMetalHostProfile": "control-profile",
            },
        },
    },
    "status": {
        "phase": "Ready",
        "addresses": [
            {"type": "InternalIP", "address": "192.168.1.10"},
        ],
        "nodeRef": {"name": "control-01"},
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
        "errorMessage": "Provisioning failed: hardware error",
    },
}


class TestExtractMachineSummary:
    """Tests for _extract_machine_summary function."""

    def test_extract_compute_machine(self):
        """Test extracting summary from a compute machine."""
        summary = _extract_machine_summary(SAMPLE_MACHINE_DATA)

        assert summary.name == "compute-01"
        assert summary.namespace == "default"
        assert summary.role == "compute"
        assert summary.phase == "Ready"
        assert summary.internal_ip == "192.168.1.100"
        assert summary.hostname == "compute-01.local"
        assert summary.node_name == "compute-01"
        assert summary.profile == "compute-profile"
        assert summary.error_message is None

    def test_extract_control_machine(self):
        """Test extracting summary from a control machine."""
        summary = _extract_machine_summary(SAMPLE_CONTROL_MACHINE_DATA)

        assert summary.name == "control-01"
        assert summary.role == "control"
        assert summary.phase == "Ready"
        assert summary.internal_ip == "192.168.1.10"

    def test_extract_failed_machine(self):
        """Test extracting summary from a failed machine."""
        summary = _extract_machine_summary(SAMPLE_FAILED_MACHINE_DATA)

        assert summary.name == "compute-02"
        assert summary.phase == "Failed"
        assert summary.error_message == "Provisioning failed: hardware error"

    def test_extract_with_conditions(self):
        """Test extracting summary with conditions included."""
        summary = _extract_machine_summary(SAMPLE_MACHINE_DATA, include_conditions=True)

        assert len(summary.conditions) == 1
        assert summary.conditions[0]["type"] == "Ready"
        assert summary.conditions[0]["status"] == "True"

    def test_extract_without_conditions(self):
        """Test extracting summary without conditions."""
        summary = _extract_machine_summary(SAMPLE_MACHINE_DATA, include_conditions=False)

        assert summary.conditions == []


class TestApplyFilters:
    """Tests for _apply_filters function."""

    @pytest.fixture
    def sample_machines(self) -> list[MachineSummary]:
        """Create sample machine summaries for testing."""
        return [
            MachineSummary(
                name="compute-01",
                namespace="default",
                role="compute",
                phase="Ready",
                profile="compute-profile",
            ),
            MachineSummary(
                name="compute-02",
                namespace="default",
                role="compute",
                phase="Failed",
                profile="compute-profile",
            ),
            MachineSummary(
                name="control-01",
                namespace="default",
                role="control",
                phase="Ready",
                profile="control-profile",
            ),
            MachineSummary(
                name="storage-01",
                namespace="default",
                role="storage",
                phase="Provisioning",
                profile="storage-profile",
            ),
        ]

    def test_no_filter(self, sample_machines):
        """Test with no filters applied."""
        result = _apply_filters(
            sample_machines,
            MachineRoleFilter.ALL,
            MachinePhaseFilter.ALL,
        )

        assert len(result) == 4

    def test_role_filter_compute(self, sample_machines):
        """Test filtering by compute role."""
        result = _apply_filters(
            sample_machines,
            MachineRoleFilter.COMPUTE,
            MachinePhaseFilter.ALL,
        )

        assert len(result) == 2
        assert all(m.role == "compute" for m in result)

    def test_role_filter_control(self, sample_machines):
        """Test filtering by control role."""
        result = _apply_filters(
            sample_machines,
            MachineRoleFilter.CONTROL,
            MachinePhaseFilter.ALL,
        )

        assert len(result) == 1
        assert result[0].name == "control-01"

    def test_phase_filter_running(self, sample_machines):
        """Test filtering by Running phase (maps to Ready in MOSK)."""
        result = _apply_filters(
            sample_machines,
            MachineRoleFilter.ALL,
            MachinePhaseFilter.RUNNING,
        )

        assert len(result) == 2
        assert all(m.phase == "Ready" for m in result)

    def test_phase_filter_failed(self, sample_machines):
        """Test filtering by Failed phase."""
        result = _apply_filters(
            sample_machines,
            MachineRoleFilter.ALL,
            MachinePhaseFilter.FAILED,
        )

        assert len(result) == 1
        assert result[0].name == "compute-02"

    def test_combined_filters(self, sample_machines):
        """Test combining role and phase filters."""
        result = _apply_filters(
            sample_machines,
            MachineRoleFilter.COMPUTE,
            MachinePhaseFilter.RUNNING,
        )

        assert len(result) == 1
        assert result[0].name == "compute-01"


class TestGenerateSummary:
    """Tests for _generate_summary function."""

    def test_generate_summary(self):
        """Test generating summary statistics."""
        machines = [
            MachineSummary(
                name="compute-01",
                namespace="default",
                role="compute",
                phase="Ready",
                profile="compute-profile",
            ),
            MachineSummary(
                name="compute-02",
                namespace="default",
                role="compute",
                phase="Ready",
                profile="compute-profile",
            ),
            MachineSummary(
                name="control-01",
                namespace="default",
                role="control",
                phase="Ready",
                profile="control-profile",
            ),
            MachineSummary(
                name="storage-01",
                namespace="default",
                role="storage",
                phase="Failed",
                profile="storage-profile",
            ),
        ]

        summary = _generate_summary(machines)

        assert summary.by_role["compute"] == 2
        assert summary.by_role["control"] == 1
        assert summary.by_role["storage"] == 1
        assert summary.by_phase["Ready"] == 3
        assert summary.by_phase["Failed"] == 1
        assert summary.healthy_count == 3
        assert summary.unhealthy_count == 1


class TestListMachines:
    """Tests for list_machines function."""

    @pytest.fixture
    def mock_k8s_adapter(self):
        """Create a mock Kubernetes adapter."""
        adapter = MagicMock()
        adapter.list_machines = AsyncMock(
            return_value=[
                SAMPLE_MACHINE_DATA,
                SAMPLE_CONTROL_MACHINE_DATA,
                SAMPLE_FAILED_MACHINE_DATA,
            ]
        )
        # Mock auto-discovery to return None (use default namespace)
        adapter.get_mosk_machines_namespace = AsyncMock(return_value=None)
        return adapter

    @pytest.mark.asyncio
    async def test_list_machines_default(self, mock_k8s_adapter):
        """Test listing machines with default parameters."""
        input_data = ListMachinesInput()

        result = await list_machines(mock_k8s_adapter, input_data)

        assert isinstance(result, ListMachinesOutput)
        assert result.total_count == 3
        assert result.filtered_count == 3
        assert result.namespace == "default"
        assert len(result.machines) == 3

    @pytest.mark.asyncio
    async def test_list_machines_with_role_filter(self, mock_k8s_adapter):
        """Test listing machines with role filter."""
        input_data = ListMachinesInput(role_filter=MachineRoleFilter.COMPUTE)

        result = await list_machines(mock_k8s_adapter, input_data)

        assert result.total_count == 3
        # Only compute machines after filtering
        assert all(m.role == "compute" for m in result.machines)

    @pytest.mark.asyncio
    async def test_list_machines_with_phase_filter(self, mock_k8s_adapter):
        """Test listing machines with phase filter."""
        input_data = ListMachinesInput(phase_filter=MachinePhaseFilter.RUNNING)

        result = await list_machines(mock_k8s_adapter, input_data)

        # Only "Ready" machines after filtering (MOSK uses "Ready" for running machines)
        assert all(m.phase == "Ready" for m in result.machines)

    @pytest.mark.asyncio
    async def test_list_machines_with_limit(self, mock_k8s_adapter):
        """Test listing machines with limit."""
        input_data = ListMachinesInput(limit=2)

        result = await list_machines(mock_k8s_adapter, input_data)

        assert len(result.machines) <= 2

    @pytest.mark.asyncio
    async def test_list_machines_pagination_has_more(self, mock_k8s_adapter):
        """Test pagination returns has_more when more results exist."""
        input_data = ListMachinesInput(limit=2, offset=0)

        result = await list_machines(mock_k8s_adapter, input_data)

        assert len(result.machines) == 2
        assert result.filtered_count == 3
        assert result.has_more is True
        assert result.next_offset == 2

    @pytest.mark.asyncio
    async def test_list_machines_pagination_with_offset(self, mock_k8s_adapter):
        """Test pagination with offset skips items."""
        input_data = ListMachinesInput(limit=2, offset=1)

        result = await list_machines(mock_k8s_adapter, input_data)

        assert len(result.machines) == 2
        assert result.filtered_count == 3
        assert result.has_more is False
        assert result.next_offset is None

    @pytest.mark.asyncio
    async def test_list_machines_pagination_last_page(self, mock_k8s_adapter):
        """Test pagination on last page has no more."""
        input_data = ListMachinesInput(limit=100, offset=0)

        result = await list_machines(mock_k8s_adapter, input_data)

        assert len(result.machines) == 3
        assert result.has_more is False
        assert result.next_offset is None

    @pytest.mark.asyncio
    async def test_list_machines_all_namespaces(self, mock_k8s_adapter):
        """Test listing machines in all namespaces."""
        input_data = ListMachinesInput(namespace="*")

        await list_machines(mock_k8s_adapter, input_data)

        mock_k8s_adapter.list_machines.assert_called_once()
        call_args = mock_k8s_adapter.list_machines.call_args
        assert call_args.kwargs["namespace"] == "*"

    @pytest.mark.asyncio
    async def test_list_machines_with_label_selector(self, mock_k8s_adapter):
        """Test listing machines with label selector."""
        input_data = ListMachinesInput(label_selector="env=prod")

        await list_machines(mock_k8s_adapter, input_data)

        mock_k8s_adapter.list_machines.assert_called_once()
        call_args = mock_k8s_adapter.list_machines.call_args
        assert call_args.kwargs["label_selector"] == "env=prod"

    @pytest.mark.asyncio
    async def test_list_machines_summary_generated(self, mock_k8s_adapter):
        """Test that summary statistics are generated."""
        input_data = ListMachinesInput()

        result = await list_machines(mock_k8s_adapter, input_data)

        assert hasattr(result.summary, "by_role")
        assert hasattr(result.summary, "by_phase")
        assert hasattr(result.summary, "healthy_count")

    @pytest.mark.asyncio
    async def test_list_machines_empty_result(self):
        """Test listing machines when none exist."""
        mock_adapter = MagicMock()
        mock_adapter.list_machines = AsyncMock(return_value=[])
        mock_adapter.get_mosk_machines_namespace = AsyncMock(return_value=None)

        input_data = ListMachinesInput()

        result = await list_machines(mock_adapter, input_data)

        assert result.total_count == 0
        assert result.filtered_count == 0
        assert result.machines == []

    @pytest.mark.asyncio
    async def test_list_machines_with_conditions(self, mock_k8s_adapter):
        """Test listing machines with conditions included."""
        input_data = ListMachinesInput(include_conditions=True)

        result = await list_machines(mock_k8s_adapter, input_data)

        # At least one machine should have conditions
        machine_with_conditions = next(
            (m for m in result.machines if m.name == "compute-01"),
            None,
        )
        assert machine_with_conditions is not None
        assert len(machine_with_conditions.conditions) > 0


class TestListMachinesInput:
    """Tests for ListMachinesInput validation."""

    def test_default_values(self):
        """Test default input values."""
        input_data = ListMachinesInput()

        assert input_data.namespace == "default"
        assert input_data.role_filter == MachineRoleFilter.ALL
        assert input_data.phase_filter == MachinePhaseFilter.ALL
        assert input_data.include_conditions is False
        assert input_data.limit == 100

    def test_custom_namespace(self):
        """Test custom namespace input."""
        input_data = ListMachinesInput(namespace="openstack")

        assert input_data.namespace == "openstack"

    def test_limit_validation(self):
        """Test limit validation."""
        # Valid limit
        input_data = ListMachinesInput(limit=50)
        assert input_data.limit == 50

        # Invalid limit (too low)
        with pytest.raises(ValueError):
            ListMachinesInput(limit=0)

        # Invalid limit (too high)
        with pytest.raises(ValueError):
            ListMachinesInput(limit=1000)


class TestMachineSummary:
    """Tests for MachineSummary model."""

    def test_required_fields(self):
        """Test that required fields are validated."""
        summary = MachineSummary(
            name="test-machine",
            namespace="default",
            role="compute",
            phase="Running",
            profile="test-profile",
        )

        assert summary.name == "test-machine"
        assert summary.namespace == "default"

    def test_optional_fields(self):
        """Test optional fields have correct defaults."""
        summary = MachineSummary(
            name="test-machine",
            namespace="default",
            role="compute",
            phase="Running",
            profile="test-profile",
        )

        assert summary.internal_ip is None
        assert summary.hostname is None
        assert summary.node_name is None
        assert summary.age_seconds is None
        assert summary.conditions == []
        assert summary.error_message is None
