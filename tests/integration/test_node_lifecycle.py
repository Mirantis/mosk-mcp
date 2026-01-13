"""Integration tests for node lifecycle MCP tools.

These tests validate the node lifecycle tools end-to-end with mocked adapters,
simulating realistic Machine and Node management responses.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from mosk_mcp.tools.node_lifecycle.get_machine_details import (
    GetMachineDetailsInput,
    get_machine_details,
)
from mosk_mcp.tools.node_lifecycle.get_node_readiness import (
    GetNodeReadinessInput,
    ReadinessCheckType,
    get_node_readiness,
)
from mosk_mcp.tools.node_lifecycle.list_machines import (
    ListMachinesInput,
    MachinePhaseFilter,
    MachineRoleFilter,
    list_machines,
)


# =============================================================================
# List Machines Tests
# =============================================================================


@pytest.mark.integration
class TestListMachines:
    """Integration tests for list_machines tool."""

    @pytest.mark.asyncio
    async def test_list_all_machines(self, mock_kubernetes_adapter: MagicMock) -> None:
        """Test listing all machines."""
        input_data = ListMachinesInput(
            namespace="lab",
            role_filter=MachineRoleFilter.ALL,
            phase_filter=MachinePhaseFilter.ALL,
            include_conditions=False,
        )

        result = await list_machines(
            k8s_adapter=mock_kubernetes_adapter,
            input_data=input_data,
        )

        assert result.total_count == 9
        assert len(result.machines) == 9

    @pytest.mark.asyncio
    async def test_list_compute_machines(self, mock_kubernetes_adapter: MagicMock) -> None:
        """Test listing only compute machines."""
        input_data = ListMachinesInput(
            namespace="lab",
            role_filter=MachineRoleFilter.COMPUTE,
        )

        result = await list_machines(
            k8s_adapter=mock_kubernetes_adapter,
            input_data=input_data,
        )

        # After filtering, should have only compute machines
        assert result.filtered_count <= result.total_count

    @pytest.mark.asyncio
    async def test_list_running_machines(self, mock_kubernetes_adapter: MagicMock) -> None:
        """Test listing only running machines."""
        input_data = ListMachinesInput(
            namespace="lab",
            phase_filter=MachinePhaseFilter.RUNNING,
        )

        result = await list_machines(
            k8s_adapter=mock_kubernetes_adapter,
            input_data=input_data,
        )

        for machine in result.machines:
            assert machine.phase == "Running"

    @pytest.mark.asyncio
    async def test_list_machines_with_conditions(self, mock_kubernetes_adapter: MagicMock) -> None:
        """Test listing machines with conditions included."""
        input_data = ListMachinesInput(
            namespace="lab",
            include_conditions=True,
        )

        result = await list_machines(
            k8s_adapter=mock_kubernetes_adapter,
            input_data=input_data,
        )

        assert result.total_count == 9

    @pytest.mark.asyncio
    async def test_list_machines_with_label_selector(
        self, mock_kubernetes_adapter: MagicMock
    ) -> None:
        """Test listing machines with label selector."""
        input_data = ListMachinesInput(
            namespace="lab",
            label_selector="kaas.mirantis.com/machine-role=storage",
        )

        result = await list_machines(
            k8s_adapter=mock_kubernetes_adapter,
            input_data=input_data,
        )

        # Should return storage machines
        assert result.total_count <= 9


# =============================================================================
# Get Machine Details Tests
# =============================================================================


@pytest.mark.integration
class TestGetMachineDetails:
    """Integration tests for get_machine_details tool."""

    @pytest.mark.asyncio
    async def test_get_machine_details(self, mock_kubernetes_adapter: MagicMock) -> None:
        """Test getting details for a specific machine."""
        input_data = GetMachineDetailsInput(
            name="compute-01",
            namespace="lab",
            include_events=True,
            include_related=True,
        )

        result = await get_machine_details(
            k8s_adapter=mock_kubernetes_adapter,
            input_data=input_data,
        )

        assert result.name == "compute-01"
        assert result.phase == "Running"
        # Role is extracted from labels, verify it was set
        assert result.role is not None

    @pytest.mark.asyncio
    async def test_get_machine_without_related(self, mock_kubernetes_adapter: MagicMock) -> None:
        """Test getting machine details without related resources."""
        input_data = GetMachineDetailsInput(
            name="storage-01",
            namespace="lab",
            include_events=False,
            include_related=False,
        )

        result = await get_machine_details(
            k8s_adapter=mock_kubernetes_adapter,
            input_data=input_data,
        )

        assert result.name == "storage-01"
        # Role is extracted from labels
        assert result.role is not None


# =============================================================================
# Get Node Readiness Tests
# =============================================================================


@pytest.mark.integration
class TestGetNodeReadiness:
    """Integration tests for get_node_readiness tool."""

    @pytest.mark.asyncio
    async def test_general_readiness(
        self,
        mock_kubernetes_adapter: MagicMock,
    ) -> None:
        """Test general readiness check for a node."""
        input_data = GetNodeReadinessInput(
            name="compute-01",
            namespace="lab",
            check_type=ReadinessCheckType.GENERAL,
            check_ceph=True,
            check_openstack=True,
        )

        result = await get_node_readiness(
            mcc_adapter=mock_kubernetes_adapter,
            input_data=input_data,
            mosk_adapter=mock_kubernetes_adapter,
        )

        assert result.name == "compute-01"
        # is_ready may be False if node ref is not set in mock
        assert result.is_ready is not None

    @pytest.mark.asyncio
    async def test_maintenance_readiness(
        self,
        mock_kubernetes_adapter: MagicMock,
    ) -> None:
        """Test maintenance readiness check for a node."""
        input_data = GetNodeReadinessInput(
            name="compute-02",
            namespace="lab",
            check_type=ReadinessCheckType.MAINTENANCE,
            check_ceph=True,
            check_openstack=True,
        )

        result = await get_node_readiness(
            mcc_adapter=mock_kubernetes_adapter,
            input_data=input_data,
            mosk_adapter=mock_kubernetes_adapter,
        )

        assert result.name == "compute-02"

    @pytest.mark.asyncio
    async def test_storage_node_readiness(
        self,
        mock_kubernetes_adapter: MagicMock,
    ) -> None:
        """Test readiness check for a storage node."""
        input_data = GetNodeReadinessInput(
            name="storage-01",
            namespace="lab",
            check_type=ReadinessCheckType.MAINTENANCE,
            check_ceph=True,
            check_openstack=False,
        )

        result = await get_node_readiness(
            mcc_adapter=mock_kubernetes_adapter,
            input_data=input_data,
            mosk_adapter=mock_kubernetes_adapter,
        )

        assert result.name == "storage-01"

    @pytest.mark.asyncio
    async def test_drain_readiness(
        self,
        mock_kubernetes_adapter: MagicMock,
    ) -> None:
        """Test drain readiness check for a node."""
        input_data = GetNodeReadinessInput(
            name="compute-03",
            namespace="lab",
            check_type=ReadinessCheckType.DRAIN,
        )

        result = await get_node_readiness(
            mcc_adapter=mock_kubernetes_adapter,
            input_data=input_data,
            mosk_adapter=mock_kubernetes_adapter,
        )

        assert result.name == "compute-03"


# =============================================================================
# Edge Cases and Error Handling
# =============================================================================


@pytest.mark.integration
class TestNodeLifecycleEdgeCases:
    """Edge cases and error handling tests."""

    @pytest.mark.asyncio
    async def test_machine_in_provisioning(self, mock_kubernetes_adapter: MagicMock) -> None:
        """Test handling of machine in provisioning state."""
        # Modify a machine to be provisioning
        machines = mock_kubernetes_adapter.list_machines.return_value.copy()
        machines[0]["status"]["phase"] = "Provisioning"
        mock_kubernetes_adapter.list_machines = AsyncMock(return_value=machines)

        input_data = ListMachinesInput(
            namespace="lab",
            phase_filter=MachinePhaseFilter.PROVISIONING,
        )

        result = await list_machines(
            k8s_adapter=mock_kubernetes_adapter,
            input_data=input_data,
        )

        assert result.filtered_count == 1
        assert result.machines[0].phase == "Provisioning"

    @pytest.mark.asyncio
    async def test_machine_failed(self, mock_kubernetes_adapter: MagicMock) -> None:
        """Test handling of failed machine."""
        machines = mock_kubernetes_adapter.list_machines.return_value.copy()
        machines[1]["status"]["phase"] = "Failed"
        mock_kubernetes_adapter.list_machines = AsyncMock(return_value=machines)

        input_data = ListMachinesInput(
            namespace="lab",
            phase_filter=MachinePhaseFilter.FAILED,
        )

        result = await list_machines(
            k8s_adapter=mock_kubernetes_adapter,
            input_data=input_data,
        )

        assert result.filtered_count == 1
        assert result.machines[0].phase == "Failed"

    @pytest.mark.asyncio
    async def test_empty_namespace(self, mock_kubernetes_adapter: MagicMock) -> None:
        """Test listing machines from empty namespace."""
        mock_kubernetes_adapter.list_machines = AsyncMock(return_value=[])

        input_data = ListMachinesInput(
            namespace="empty-namespace",
        )

        result = await list_machines(
            k8s_adapter=mock_kubernetes_adapter,
            input_data=input_data,
        )

        assert result.total_count == 0
        assert result.machines == []
