"""Unit tests for NodeAddMonitor."""

from unittest.mock import AsyncMock

import pytest

from mosk_mcp.core.exceptions import ResourceNotFoundError
from mosk_mcp.tools.operations_visibility.monitors.node_add_monitor import (
    PHASE_MESSAGES,
    PHASE_PROGRESS,
    NodeAddMonitor,
    ProvisionPhase,
)


class TestProvisionPhase:
    """Tests for ProvisionPhase enum."""

    def test_all_phases_have_progress(self):
        """Test all phases have a progress percentage defined."""
        for phase in ProvisionPhase:
            assert phase in PHASE_PROGRESS

    def test_all_phases_have_messages(self):
        """Test all phases have a message defined."""
        for phase in ProvisionPhase:
            assert phase in PHASE_MESSAGES

    def test_progress_range(self):
        """Test progress values are in valid range."""
        for _phase, progress in PHASE_PROGRESS.items():
            # -1 for error, 0-100 for valid progress
            assert progress >= -1 and progress <= 100

    def test_completed_is_100_percent(self):
        """Test completed phase is 100%."""
        assert PHASE_PROGRESS[ProvisionPhase.COMPLETED] == 100

    def test_error_is_negative_one(self):
        """Test error phase is -1."""
        assert PHASE_PROGRESS[ProvisionPhase.ERROR] == -1


class TestNodeAddMonitor:
    """Tests for NodeAddMonitor class."""

    @pytest.fixture
    def mock_adapter(self):
        """Create mock Kubernetes adapter."""
        adapter = AsyncMock()
        return adapter

    @pytest.fixture
    def monitor(self, mock_adapter):
        """Create monitor instance."""
        return NodeAddMonitor(
            adapter=mock_adapter,
            target="compute-01",
            namespace="default",
        )

    def test_initialization(self, monitor):
        """Test monitor initialization."""
        assert monitor.target == "compute-01"
        assert monitor.namespace == "default"
        assert monitor._current_phase == ProvisionPhase.NOT_STARTED
        assert monitor._error_message is None

    def test_is_complete_false_initially(self, monitor):
        """Test is_complete returns False initially."""
        assert monitor.is_complete() is False

    def test_has_failed_false_initially(self, monitor):
        """Test has_failed returns False initially."""
        assert monitor.has_failed() is False

    def test_get_error_message_none_initially(self, monitor):
        """Test get_error_message returns None initially."""
        assert monitor.get_error_message() is None


class TestGetBmhiState:
    """Tests for _get_bmhi_state method."""

    @pytest.fixture
    def mock_adapter(self):
        """Create mock adapter."""
        return AsyncMock()

    @pytest.fixture
    def monitor(self, mock_adapter):
        """Create monitor."""
        return NodeAddMonitor(mock_adapter, "compute-01", "default")

    @pytest.mark.asyncio
    async def test_bmhi_exists(self, monitor, mock_adapter):
        """Test when BMHi exists."""
        mock_adapter.get_custom_resource = AsyncMock(
            return_value={
                "metadata": {"name": "compute-01"},
                "status": {"operationalStatus": "OK"},
            }
        )

        result = await monitor._get_bmhi_state()

        assert result["exists"] is True
        assert result["state"] == "OK"

    @pytest.mark.asyncio
    async def test_bmhi_not_found(self, monitor, mock_adapter):
        """Test when BMHi does not exist."""
        mock_adapter.get_custom_resource = AsyncMock(
            side_effect=ResourceNotFoundError("BMHi not found")
        )

        result = await monitor._get_bmhi_state()

        assert result["exists"] is False
        assert result.get("query_failed") is False

    @pytest.mark.asyncio
    async def test_bmhi_query_error(self, monitor, mock_adapter):
        """Test when BMHi query fails."""
        mock_adapter.get_custom_resource = AsyncMock(side_effect=Exception("Connection failed"))

        result = await monitor._get_bmhi_state()

        assert result["exists"] is False
        assert result.get("query_failed") is True
        assert "Connection failed" in result.get("error", "")


class TestGetBmhState:
    """Tests for _get_bmh_state method."""

    @pytest.fixture
    def mock_adapter(self):
        """Create mock adapter."""
        return AsyncMock()

    @pytest.fixture
    def monitor(self, mock_adapter):
        """Create monitor."""
        return NodeAddMonitor(mock_adapter, "compute-01", "default")

    @pytest.mark.asyncio
    async def test_bmh_exists_provisioning(self, monitor, mock_adapter):
        """Test when BMH exists and is provisioning."""
        mock_adapter.get_custom_resource = AsyncMock(
            return_value={
                "metadata": {"name": "compute-01"},
                "spec": {"consumerRef": {"name": "compute-01-machine"}},
                "status": {
                    "provisioning": {"state": "provisioning"},
                    "operationalStatus": "OK",
                    "poweredOn": True,
                },
            }
        )

        result = await monitor._get_bmh_state()

        assert result["exists"] is True
        assert result["state"] == "provisioning"
        assert result["powered_on"] is True
        assert result["consumer"] == "compute-01-machine"

    @pytest.mark.asyncio
    async def test_bmh_not_found(self, monitor, mock_adapter):
        """Test when BMH does not exist."""
        mock_adapter.get_custom_resource = AsyncMock(
            side_effect=ResourceNotFoundError("BMH not found")
        )

        result = await monitor._get_bmh_state()

        assert result["exists"] is False

    @pytest.mark.asyncio
    async def test_bmh_query_error(self, monitor, mock_adapter):
        """Test when BMH query fails."""
        mock_adapter.get_custom_resource = AsyncMock(side_effect=Exception("API error"))

        result = await monitor._get_bmh_state()

        assert result["exists"] is False
        assert result.get("query_failed") is True


class TestGetMachineState:
    """Tests for _get_machine_state method."""

    @pytest.fixture
    def mock_adapter(self):
        """Create mock adapter."""
        return AsyncMock()

    @pytest.fixture
    def monitor(self, mock_adapter):
        """Create monitor."""
        return NodeAddMonitor(mock_adapter, "compute-01", "default")

    @pytest.mark.asyncio
    async def test_machine_exists_ready(self, monitor, mock_adapter):
        """Test when Machine exists and is Ready."""
        mock_adapter.get_custom_resource = AsyncMock(
            return_value={
                "metadata": {"name": "compute-01"},
                "status": {
                    "phase": "Ready",
                    "nodeRef": {"name": "compute-01"},
                },
            }
        )

        result = await monitor._get_machine_state()

        assert result["exists"] is True
        assert result["state"] == "Ready"
        assert result["node_ref"] == "compute-01"

    @pytest.mark.asyncio
    async def test_machine_not_found(self, monitor, mock_adapter):
        """Test when Machine does not exist."""
        mock_adapter.get_custom_resource = AsyncMock(
            side_effect=ResourceNotFoundError("Machine not found")
        )

        result = await monitor._get_machine_state()

        assert result["exists"] is False


class TestGetLcmmachineState:
    """Tests for _get_lcmmachine_state method."""

    @pytest.fixture
    def mock_adapter(self):
        """Create mock adapter."""
        return AsyncMock()

    @pytest.fixture
    def monitor(self, mock_adapter):
        """Create monitor."""
        return NodeAddMonitor(mock_adapter, "compute-01", "default")

    @pytest.mark.asyncio
    async def test_lcmmachine_exists_ready(self, monitor, mock_adapter):
        """Test when LCMMachine exists and is Ready."""
        mock_adapter.get_custom_resource = AsyncMock(
            return_value={
                "metadata": {"name": "compute-01"},
                "status": {"state": "Ready"},
            }
        )

        result = await monitor._get_lcmmachine_state()

        assert result["exists"] is True
        assert result["state"] == "Ready"

    @pytest.mark.asyncio
    async def test_lcmmachine_not_found(self, monitor, mock_adapter):
        """Test when LCMMachine does not exist."""
        mock_adapter.get_custom_resource = AsyncMock(
            side_effect=ResourceNotFoundError("LCMMachine not found")
        )

        result = await monitor._get_lcmmachine_state()

        assert result["exists"] is False


class TestGetNodeState:
    """Tests for _get_node_state method."""

    @pytest.fixture
    def mock_adapter(self):
        """Create mock adapter."""
        return AsyncMock()

    @pytest.fixture
    def monitor(self, mock_adapter):
        """Create monitor."""
        return NodeAddMonitor(mock_adapter, "compute-01", "default")

    @pytest.mark.asyncio
    async def test_node_no_name(self, monitor):
        """Test when no node name provided."""
        result = await monitor._get_node_state(None)

        assert result["exists"] is False

    @pytest.mark.asyncio
    async def test_node_exists_ready(self, monitor, mock_adapter):
        """Test when Node exists and is Ready."""
        mock_adapter.get = AsyncMock(
            return_value={
                "metadata": {"name": "compute-01"},
                "status": {
                    "conditions": [{"type": "Ready", "status": "True", "message": "Node is ready"}]
                },
            }
        )

        result = await monitor._get_node_state("compute-01")

        assert result["exists"] is True
        assert result["ready"] is True
        assert result["message"] == "Node is ready"

    @pytest.mark.asyncio
    async def test_node_exists_not_ready(self, monitor, mock_adapter):
        """Test when Node exists but is not Ready."""
        mock_adapter.get = AsyncMock(
            return_value={
                "metadata": {"name": "compute-01"},
                "status": {
                    "conditions": [
                        {"type": "Ready", "status": "False", "message": "Node not ready"}
                    ]
                },
            }
        )

        result = await monitor._get_node_state("compute-01")

        assert result["exists"] is True
        assert result["ready"] is False

    @pytest.mark.asyncio
    async def test_node_not_found(self, monitor, mock_adapter):
        """Test when Node does not exist."""
        mock_adapter.get = AsyncMock(side_effect=ResourceNotFoundError("Node not found"))

        result = await monitor._get_node_state("compute-01")

        assert result["exists"] is False


class TestDeterminePhase:
    """Tests for _determine_phase method."""

    @pytest.fixture
    def mock_adapter(self):
        """Create mock adapter."""
        return AsyncMock()

    @pytest.fixture
    def monitor(self, mock_adapter):
        """Create monitor."""
        return NodeAddMonitor(mock_adapter, "compute-01", "default")

    def test_phase_not_started(self, monitor):
        """Test NOT_STARTED phase when nothing exists."""
        phase, error = monitor._determine_phase(
            bmhi={"exists": False},
            bmh={"exists": False},
            machine={"exists": False},
            lcm={"exists": False},
            node={"exists": False},
        )

        assert phase == ProvisionPhase.NOT_STARTED
        assert error is None

    def test_phase_bmhi_created(self, monitor):
        """Test BMHI_CREATED phase."""
        phase, error = monitor._determine_phase(
            bmhi={"exists": True, "state": "OK"},
            bmh={"exists": False},
            machine={"exists": False},
            lcm={"exists": False},
            node={"exists": False},
        )

        assert phase == ProvisionPhase.BMHI_CREATED
        assert error is None

    def test_phase_bmh_registering(self, monitor):
        """Test BMH_REGISTERING phase."""
        phase, error = monitor._determine_phase(
            bmhi={"exists": True, "state": "OK"},
            bmh={"exists": True, "state": "registering"},
            machine={"exists": False},
            lcm={"exists": False},
            node={"exists": False},
        )

        assert phase == ProvisionPhase.BMH_REGISTERING
        assert error is None

    def test_phase_bmh_inspecting(self, monitor):
        """Test BMH_INSPECTING phase."""
        phase, error = monitor._determine_phase(
            bmhi={"exists": True},
            bmh={"exists": True, "state": "inspecting"},
            machine={"exists": False},
            lcm={"exists": False},
            node={"exists": False},
        )

        assert phase == ProvisionPhase.BMH_INSPECTING
        assert error is None

    def test_phase_bmh_preparing(self, monitor):
        """Test BMH_PREPARING phase."""
        phase, error = monitor._determine_phase(
            bmhi={"exists": True},
            bmh={"exists": True, "state": "preparing"},
            machine={"exists": False},
            lcm={"exists": False},
            node={"exists": False},
        )

        assert phase == ProvisionPhase.BMH_PREPARING
        assert error is None

    def test_phase_bmh_available(self, monitor):
        """Test BMH_AVAILABLE phase."""
        phase, error = monitor._determine_phase(
            bmhi={"exists": True},
            bmh={"exists": True, "state": "available"},
            machine={"exists": False},
            lcm={"exists": False},
            node={"exists": False},
        )

        assert phase == ProvisionPhase.BMH_AVAILABLE
        assert error is None

    def test_phase_machine_created(self, monitor):
        """Test MACHINE_CREATED phase."""
        phase, error = monitor._determine_phase(
            bmhi={"exists": True},
            bmh={"exists": True, "state": "available"},
            machine={"exists": True, "state": "Provisioning"},
            lcm={"exists": False},
            node={"exists": False},
        )

        assert phase == ProvisionPhase.MACHINE_CREATED
        assert error is None

    def test_phase_bmh_provisioning(self, monitor):
        """Test BMH_PROVISIONING phase."""
        phase, error = monitor._determine_phase(
            bmhi={"exists": True},
            bmh={"exists": True, "state": "provisioning"},
            machine={"exists": True, "state": "Provisioning"},
            lcm={"exists": False},
            node={"exists": False},
        )

        assert phase == ProvisionPhase.BMH_PROVISIONING
        assert error is None

    def test_phase_bmh_provisioned(self, monitor):
        """Test BMH_PROVISIONED phase."""
        phase, error = monitor._determine_phase(
            bmhi={"exists": True},
            bmh={"exists": True, "state": "provisioned"},
            machine={"exists": True, "state": "Deploying"},
            lcm={"exists": False},
            node={"exists": False},
        )

        assert phase == ProvisionPhase.BMH_PROVISIONED
        assert error is None

    def test_phase_machine_deploying(self, monitor):
        """Test MACHINE_DEPLOYING phase when machine is Ready but LCM is not."""
        phase, error = monitor._determine_phase(
            bmhi={"exists": True},
            bmh={"exists": True, "state": "provisioned"},
            machine={"exists": True, "state": "Ready"},
            lcm={"exists": True, "state": "Deploying"},
            node={"exists": False},
        )

        assert phase == ProvisionPhase.MACHINE_DEPLOYING
        assert error is None

    def test_phase_machine_ready(self, monitor):
        """Test MACHINE_READY phase."""
        phase, error = monitor._determine_phase(
            bmhi={"exists": True},
            bmh={"exists": True, "state": "provisioned"},
            machine={"exists": True, "state": "Ready"},
            lcm={"exists": True, "state": "Ready"},
            node={"exists": False},
        )

        assert phase == ProvisionPhase.MACHINE_READY
        assert error is None

    def test_phase_node_ready(self, monitor):
        """Test NODE_READY phase."""
        phase, error = monitor._determine_phase(
            bmhi={"exists": True},
            bmh={"exists": True, "state": "provisioned"},
            machine={"exists": True, "state": "Ready"},
            lcm={"exists": True, "state": "Deploying"},  # Not ready yet
            node={"exists": True, "ready": True},
        )

        assert phase == ProvisionPhase.NODE_READY
        assert error is None

    def test_phase_completed(self, monitor):
        """Test COMPLETED phase."""
        phase, error = monitor._determine_phase(
            bmhi={"exists": True},
            bmh={"exists": True, "state": "provisioned"},
            machine={"exists": True, "state": "Ready"},
            lcm={"exists": True, "state": "Ready"},
            node={"exists": True, "ready": True},
        )

        assert phase == ProvisionPhase.COMPLETED
        assert error is None

    def test_phase_error_from_bmhi(self, monitor):
        """Test ERROR phase from BMHi error."""
        phase, error = monitor._determine_phase(
            bmhi={"exists": True, "error": "BMC connection error"},
            bmh={"exists": False},
            machine={"exists": False},
            lcm={"exists": False},
            node={"exists": False},
        )

        assert phase == ProvisionPhase.ERROR
        assert "BMHi" in error

    def test_phase_error_from_bmh(self, monitor):
        """Test ERROR phase from BMH error status."""
        phase, error = monitor._determine_phase(
            bmhi={"exists": True},
            bmh={"exists": True, "state": "error", "error": "Provisioning failed"},
            machine={"exists": False},
            lcm={"exists": False},
            node={"exists": False},
        )

        assert phase == ProvisionPhase.ERROR
        assert "BMH" in error

    def test_phase_error_from_machine(self, monitor):
        """Test ERROR phase from Machine error."""
        phase, error = monitor._determine_phase(
            bmhi={"exists": True},
            bmh={"exists": True, "state": "provisioned"},
            machine={"exists": True, "state": "error", "error": "Machine failed"},
            lcm={"exists": False},
            node={"exists": False},
        )

        assert phase == ProvisionPhase.ERROR
        assert "Machine" in error


class TestGetProgress:
    """Tests for get_progress method."""

    @pytest.fixture
    def mock_adapter(self):
        """Create mock adapter."""
        return AsyncMock()

    @pytest.fixture
    def monitor(self, mock_adapter):
        """Create monitor."""
        return NodeAddMonitor(mock_adapter, "compute-01", "default")

    @pytest.mark.asyncio
    async def test_get_progress_completed(self, monitor, mock_adapter):
        """Test get_progress for completed provisioning."""
        # Mock all resources in completed state
        mock_adapter.get_custom_resource = AsyncMock(
            side_effect=[
                # BMHi
                {"status": {"operationalStatus": "OK"}},
                # BMH
                {"status": {"provisioning": {"state": "provisioned"}, "poweredOn": True}},
                # Machine
                {"status": {"phase": "Ready", "nodeRef": {"name": "compute-01"}}},
                # LCMMachine
                {"status": {"state": "Ready"}},
            ]
        )
        mock_adapter.get = AsyncMock(
            return_value={"status": {"conditions": [{"type": "Ready", "status": "True"}]}}
        )

        snapshot = await monitor.get_progress()

        assert snapshot.progress_percent == 100
        assert snapshot.phase == "completed"
        assert monitor.is_complete() is True
        assert monitor.has_failed() is False

    @pytest.mark.asyncio
    async def test_get_progress_in_progress(self, monitor, mock_adapter):
        """Test get_progress for in-progress provisioning."""
        mock_adapter.get_custom_resource = AsyncMock(
            side_effect=[
                # BMHi
                {"status": {"operationalStatus": "OK"}},
                # BMH
                {"status": {"provisioning": {"state": "provisioning"}, "poweredOn": True}},
                # Machine
                {"status": {"phase": "Provisioning"}},
                # LCMMachine
                ResourceNotFoundError("Not found"),
            ]
        )

        snapshot = await monitor.get_progress()

        assert snapshot.progress_percent == PHASE_PROGRESS[ProvisionPhase.BMH_PROVISIONING]
        assert snapshot.phase == "bmh_provisioning"
        assert monitor.is_complete() is False
        assert monitor.has_failed() is False

    @pytest.mark.asyncio
    async def test_get_progress_with_error(self, monitor, mock_adapter):
        """Test get_progress when provisioning has failed."""
        mock_adapter.get_custom_resource = AsyncMock(
            side_effect=[
                # BMHi
                {"status": {"operationalStatus": "OK"}},
                # BMH with error
                {"status": {"provisioning": {"state": "error"}, "errorMessage": "BMC unreachable"}},
                # Machine
                ResourceNotFoundError("Not found"),
                # LCMMachine
                ResourceNotFoundError("Not found"),
            ]
        )

        snapshot = await monitor.get_progress()

        assert snapshot.progress_percent == -1
        assert snapshot.phase == "error"
        assert monitor.is_complete() is False
        assert monitor.has_failed() is True
        assert monitor.get_error_message() is not None

    @pytest.mark.asyncio
    async def test_get_progress_includes_details(self, monitor, mock_adapter):
        """Test that progress snapshot includes resource details."""
        mock_adapter.get_custom_resource = AsyncMock(
            side_effect=[
                # BMHi
                {"status": {"operationalStatus": "OK"}},
                # BMH
                {"status": {"provisioning": {"state": "available"}, "poweredOn": True}},
                # Machine
                ResourceNotFoundError("Not found"),
                # LCMMachine
                ResourceNotFoundError("Not found"),
            ]
        )

        snapshot = await monitor.get_progress()

        assert "bmhi_exists" in snapshot.details
        assert "bmh_state" in snapshot.details
        assert "powered_on" in snapshot.details
        assert snapshot.details["bmhi_exists"] is True
        assert snapshot.details["bmh_state"] == "available"
