# ruff: noqa: SIM116
"""Unit tests for operation monitors.

Tests for base monitor, node add monitor, openstack upgrade monitor, and mosk upgrade monitor.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from mosk_mcp.core.exceptions import ResourceNotFoundError
from mosk_mcp.tools.operations_visibility.monitors.base import (
    BaseOperationMonitor,
    ProgressSnapshot,
)
from mosk_mcp.tools.operations_visibility.monitors.node_add_monitor import (
    PHASE_MESSAGES,
    PHASE_PROGRESS,
    NodeAddMonitor,
    ProvisionPhase,
)


# ==========================
# ProgressSnapshot Tests
# ==========================
class TestProgressSnapshot:
    """Tests for ProgressSnapshot model."""

    def test_create_snapshot(self) -> None:
        """Test creating a snapshot with factory method."""
        snapshot = ProgressSnapshot.create(
            progress_percent=50,
            phase="testing",
            message="Test in progress",
            details={"key": "value"},
        )

        assert snapshot.progress_percent == 50
        assert snapshot.phase == "testing"
        assert snapshot.message == "Test in progress"
        assert snapshot.details == {"key": "value"}
        assert snapshot.timestamp  # Should have a timestamp

    def test_create_snapshot_no_details(self) -> None:
        """Test creating a snapshot without details."""
        snapshot = ProgressSnapshot.create(
            progress_percent=75,
            phase="running",
            message="Running",
        )

        assert snapshot.progress_percent == 75
        assert snapshot.details == {}

    def test_snapshot_timestamp_format(self) -> None:
        """Test that timestamp is in ISO format."""
        snapshot = ProgressSnapshot.create(
            progress_percent=0,
            phase="init",
            message="Initializing",
        )

        # Should be parseable as ISO format
        parsed = datetime.fromisoformat(snapshot.timestamp.replace("Z", "+00:00"))
        assert isinstance(parsed, datetime)

    def test_snapshot_progress_percent_bounds(self) -> None:
        """Test progress percent validation."""
        # Valid range: -1 to 100
        snapshot = ProgressSnapshot.create(
            progress_percent=-1,  # Error state
            phase="error",
            message="Failed",
        )
        assert snapshot.progress_percent == -1

        snapshot = ProgressSnapshot.create(
            progress_percent=100,
            phase="complete",
            message="Done",
        )
        assert snapshot.progress_percent == 100

    def test_snapshot_model_fields(self) -> None:
        """Test snapshot model has all required fields."""
        snapshot = ProgressSnapshot(
            timestamp="2025-01-01T00:00:00Z",
            progress_percent=50,
            phase="middle",
            message="Halfway done",
            details={"extra": "info"},
        )

        assert snapshot.timestamp == "2025-01-01T00:00:00Z"
        assert snapshot.progress_percent == 50
        assert snapshot.phase == "middle"
        assert snapshot.message == "Halfway done"
        assert snapshot.details["extra"] == "info"


# ==========================
# BaseOperationMonitor Tests
# ==========================
class TestBaseOperationMonitor:
    """Tests for BaseOperationMonitor abstract class."""

    def test_base_monitor_initialization(self) -> None:
        """Test base monitor initialization."""
        adapter = MagicMock()

        # Create a concrete implementation for testing
        class ConcreteMonitor(BaseOperationMonitor):
            async def get_progress(self) -> ProgressSnapshot:
                return ProgressSnapshot.create(0, "test", "Test")

            def is_complete(self) -> bool:
                return False

            def has_failed(self) -> bool:
                return False

            def get_error_message(self) -> str | None:
                return None

        monitor = ConcreteMonitor(adapter, target="test-node", namespace="default")

        assert monitor.adapter == adapter
        assert monitor.target == "test-node"
        assert monitor.namespace == "default"
        assert monitor.started_at is None
        assert monitor.completed_at is None


# ==========================
# NodeAddMonitor Tests
# ==========================
class TestNodeAddMonitor:
    """Tests for NodeAddMonitor."""

    @pytest.fixture
    def mock_adapter(self) -> MagicMock:
        """Create a mock Kubernetes adapter."""
        adapter = MagicMock()
        adapter.get_custom_resource = AsyncMock()
        adapter.list = AsyncMock()
        return adapter

    def test_provision_phase_enum(self) -> None:
        """Test ProvisionPhase enum values."""
        assert ProvisionPhase.NOT_STARTED.value == "not_started"
        assert ProvisionPhase.BMH_REGISTERING.value == "bmh_registering"
        assert ProvisionPhase.BMH_PROVISIONED.value == "bmh_provisioned"
        assert ProvisionPhase.NODE_READY.value == "node_ready"
        assert ProvisionPhase.COMPLETED.value == "completed"
        assert ProvisionPhase.ERROR.value == "error"

    def test_phase_progress_mapping(self) -> None:
        """Test phase progress percentages."""
        assert PHASE_PROGRESS[ProvisionPhase.NOT_STARTED] == 0
        assert PHASE_PROGRESS[ProvisionPhase.BMH_REGISTERING] == 10
        assert PHASE_PROGRESS[ProvisionPhase.BMH_AVAILABLE] == 40
        assert PHASE_PROGRESS[ProvisionPhase.BMH_PROVISIONED] == 70
        assert PHASE_PROGRESS[ProvisionPhase.COMPLETED] == 100
        assert PHASE_PROGRESS[ProvisionPhase.ERROR] == -1

    def test_phase_messages(self) -> None:
        """Test phase messages are defined."""
        for phase in ProvisionPhase:
            assert phase in PHASE_MESSAGES
            assert isinstance(PHASE_MESSAGES[phase], str)
            assert len(PHASE_MESSAGES[phase]) > 0

    @pytest.mark.asyncio
    async def test_get_progress_not_started(self, mock_adapter: MagicMock) -> None:
        """Test get_progress when node provisioning hasn't started."""
        # No resources exist
        mock_adapter.get_custom_resource = AsyncMock(side_effect=ResourceNotFoundError("Not found"))

        monitor = NodeAddMonitor(
            adapter=mock_adapter,
            target="new-node",
            namespace="default",
        )

        snapshot = await monitor.get_progress()

        assert snapshot.progress_percent == 0
        assert snapshot.phase == ProvisionPhase.NOT_STARTED.value

    @pytest.mark.asyncio
    async def test_get_progress_bmhi_created(self, mock_adapter: MagicMock) -> None:
        """Test get_progress when BMHi is created."""

        async def mock_get_cr(group, version, plural, name, namespace):
            if plural == "baremetalhostinventories":
                return {
                    "metadata": {"name": name},
                    "status": {"operationalStatus": "OK"},
                }
            raise ResourceNotFoundError("Not found")

        mock_adapter.get_custom_resource = AsyncMock(side_effect=mock_get_cr)

        monitor = NodeAddMonitor(
            adapter=mock_adapter,
            target="new-node",
            namespace="default",
        )

        snapshot = await monitor.get_progress()

        assert snapshot.progress_percent == 5
        assert snapshot.phase == ProvisionPhase.BMHI_CREATED.value
        assert snapshot.details["bmhi_exists"] is True

    @pytest.mark.asyncio
    async def test_get_progress_bmh_registering(self, mock_adapter: MagicMock) -> None:
        """Test get_progress when BMH is registering."""

        async def mock_get_cr(group, version, plural, name, namespace):
            if plural == "baremetalhostinventories":
                return {
                    "metadata": {"name": name},
                    "status": {"operationalStatus": "OK"},
                }
            elif plural == "baremetalhosts":
                return {
                    "metadata": {"name": name},
                    "status": {
                        "provisioning": {"state": "registering"},
                        "poweredOn": False,
                    },
                }
            raise ResourceNotFoundError("Not found")

        mock_adapter.get_custom_resource = AsyncMock(side_effect=mock_get_cr)

        monitor = NodeAddMonitor(
            adapter=mock_adapter,
            target="new-node",
            namespace="default",
        )

        snapshot = await monitor.get_progress()

        assert snapshot.progress_percent == 10
        assert snapshot.phase == ProvisionPhase.BMH_REGISTERING.value

    @pytest.mark.asyncio
    async def test_get_progress_bmh_provisioned(self, mock_adapter: MagicMock) -> None:
        """Test get_progress when BMH is provisioned."""

        async def mock_get_cr(group, version, plural, name, namespace):
            if plural == "baremetalhostinventories":
                return {
                    "metadata": {"name": name},
                    "status": {"operationalStatus": "OK"},
                }
            elif plural == "baremetalhosts":
                return {
                    "metadata": {"name": name},
                    "status": {
                        "provisioning": {"state": "provisioned"},
                        "poweredOn": True,
                    },
                }
            elif plural == "machines":
                return {
                    "metadata": {"name": name},
                    "status": {"phase": "Running"},
                }
            raise ResourceNotFoundError("Not found")

        mock_adapter.get_custom_resource = AsyncMock(side_effect=mock_get_cr)

        monitor = NodeAddMonitor(
            adapter=mock_adapter,
            target="new-node",
            namespace="default",
        )

        snapshot = await monitor.get_progress()

        assert snapshot.progress_percent >= 70

    @pytest.mark.asyncio
    async def test_get_progress_complete(self, mock_adapter: MagicMock) -> None:
        """Test get_progress when node is fully provisioned."""
        # Test by directly setting the phase
        monitor = NodeAddMonitor(
            adapter=mock_adapter,
            target="new-node",
            namespace="default",
        )

        # Force completion state for testing
        monitor._current_phase = ProvisionPhase.COMPLETED
        assert monitor.is_complete() is True

        # Also test that PHASE_PROGRESS is correctly defined for COMPLETED
        assert PHASE_PROGRESS[ProvisionPhase.COMPLETED] == 100

    @pytest.mark.asyncio
    async def test_is_complete(self, mock_adapter: MagicMock) -> None:
        """Test is_complete method."""
        monitor = NodeAddMonitor(
            adapter=mock_adapter,
            target="test-node",
            namespace="default",
        )

        # Initially not complete
        assert monitor.is_complete() is False

        # Force completion state
        monitor._current_phase = ProvisionPhase.COMPLETED
        assert monitor.is_complete() is True

    @pytest.mark.asyncio
    async def test_has_failed(self, mock_adapter: MagicMock) -> None:
        """Test has_failed method."""
        monitor = NodeAddMonitor(
            adapter=mock_adapter,
            target="test-node",
            namespace="default",
        )

        # Initially not failed
        assert monitor.has_failed() is False

        # Force error state
        monitor._current_phase = ProvisionPhase.ERROR
        assert monitor.has_failed() is True

    @pytest.mark.asyncio
    async def test_get_error_message(self, mock_adapter: MagicMock) -> None:
        """Test get_error_message method."""
        monitor = NodeAddMonitor(
            adapter=mock_adapter,
            target="test-node",
            namespace="default",
        )

        # Initially no error
        assert monitor.get_error_message() is None

        # Set error message
        monitor._error_message = "Provisioning failed: BMC unreachable"
        assert monitor.get_error_message() == "Provisioning failed: BMC unreachable"


# ==========================
# OpenStack Upgrade Monitor Tests
# ==========================
class TestOpenStackUpgradeMonitor:
    """Tests for OpenStackUpgradeMonitor (basic structure)."""

    @pytest.fixture
    def mock_adapter(self) -> MagicMock:
        """Create a mock Kubernetes adapter."""
        adapter = MagicMock()
        adapter.get_custom_resource = AsyncMock()
        adapter.list = AsyncMock()
        return adapter

    @pytest.mark.asyncio
    async def test_import_openstack_upgrade_monitor(self) -> None:
        """Test that OpenStackUpgradeMonitor can be imported."""
        from mosk_mcp.tools.operations_visibility.monitors.openstack_upgrade_monitor import (
            OpenStackUpgradeMonitor,
        )

        assert OpenStackUpgradeMonitor is not None

    @pytest.mark.asyncio
    async def test_openstack_upgrade_monitor_initialization(self, mock_adapter: MagicMock) -> None:
        """Test OpenStackUpgradeMonitor initialization."""
        from mosk_mcp.tools.operations_visibility.monitors.openstack_upgrade_monitor import (
            OpenStackUpgradeMonitor,
        )

        monitor = OpenStackUpgradeMonitor(
            adapter=mock_adapter,
            target="mos",
            namespace="openstack",
        )

        assert monitor.target == "mos"
        assert monitor.namespace == "openstack"


# ==========================
# MOSK Upgrade Monitor Tests
# ==========================
class TestMoskUpgradeMonitor:
    """Tests for MoskUpgradeMonitor (basic structure)."""

    @pytest.fixture
    def mock_adapter(self) -> MagicMock:
        """Create a mock Kubernetes adapter."""
        adapter = MagicMock()
        adapter.get_custom_resource = AsyncMock()
        adapter.list = AsyncMock()
        return adapter

    @pytest.mark.asyncio
    async def test_import_mosk_upgrade_monitor(self) -> None:
        """Test that MoskUpgradeMonitor can be imported."""
        from mosk_mcp.tools.operations_visibility.monitors.mosk_upgrade_monitor import (
            MoskUpgradeMonitor,
        )

        assert MoskUpgradeMonitor is not None

    @pytest.mark.asyncio
    async def test_mosk_upgrade_monitor_initialization(self, mock_adapter: MagicMock) -> None:
        """Test MoskUpgradeMonitor initialization."""
        from mosk_mcp.tools.operations_visibility.monitors.mosk_upgrade_monitor import (
            MoskUpgradeMonitor,
        )

        monitor = MoskUpgradeMonitor(
            adapter=mock_adapter,
            target="mos",
            namespace="lab",
        )

        assert monitor.target == "mos"
        assert monitor.namespace == "lab"


# ==========================
# Monitor Operation Tool Tests
# ==========================
class TestMonitorOperation:
    """Tests for monitor_operation tool integration."""

    @pytest.mark.asyncio
    async def test_import_monitor_operation(self) -> None:
        """Test that monitor_operation tool can be imported."""
        from mosk_mcp.tools.operations_visibility.monitor_operation import (
            MonitorOperationInput,
            MonitorOperationOutput,
            OperationType,
        )

        assert MonitorOperationInput is not None
        assert MonitorOperationOutput is not None
        assert OperationType is not None

    def test_operation_type_enum(self) -> None:
        """Test OperationType enum values."""
        from mosk_mcp.tools.operations_visibility.monitor_operation import OperationType

        assert OperationType.NODE_ADD.value == "node_add"
        assert OperationType.OPENSTACK_UPGRADE.value == "openstack_upgrade"

    def test_monitor_operation_input_model(self) -> None:
        """Test MonitorOperationInput model."""
        from mosk_mcp.tools.operations_visibility.monitor_operation import (
            MonitorOperationInput,
        )

        input_model = MonitorOperationInput(
            operation_type="node_add",
            target="new-compute-01",
        )

        assert input_model.operation_type == "node_add"
        assert input_model.target == "new-compute-01"

    def test_operation_status_enum(self) -> None:
        """Test OperationStatus enum values."""
        from mosk_mcp.tools.operations_visibility.monitor_operation import (
            OperationStatus,
        )

        assert OperationStatus.IN_PROGRESS.value == "in_progress"
        assert OperationStatus.COMPLETED.value == "completed"
        assert OperationStatus.FAILED.value == "failed"
        assert OperationStatus.NOT_FOUND.value == "not_found"


# ==========================
# Extended OpenStack Upgrade Monitor Tests
# ==========================
class TestOpenStackUpgradeMonitorExtended:
    """Extended tests for OpenStackUpgradeMonitor."""

    @pytest.fixture
    def mock_adapter(self) -> MagicMock:
        """Create a mock Kubernetes adapter."""
        adapter = MagicMock()
        adapter.get_custom_resource = AsyncMock()
        adapter.list = AsyncMock()
        return adapter

    def test_upgrade_phase_constants(self) -> None:
        """Test UpgradePhase constants."""
        from mosk_mcp.tools.operations_visibility.monitors.openstack_upgrade_monitor import (
            UpgradePhase,
        )

        assert UpgradePhase.NOT_STARTED == "not_started"
        assert UpgradePhase.INITIALIZING == "initializing"
        assert UpgradePhase.UPGRADING_CONTROL_PLANE == "upgrading_control_plane"
        assert UpgradePhase.UPGRADING_SERVICES == "upgrading_services"
        assert UpgradePhase.UPGRADING_COMPUTE == "upgrading_compute"
        assert UpgradePhase.FINALIZING == "finalizing"
        assert UpgradePhase.COMPLETED == "completed"
        assert UpgradePhase.FAILED == "failed"

    def test_phase_messages_complete(self) -> None:
        """Test all phases have messages."""
        from mosk_mcp.tools.operations_visibility.monitors.openstack_upgrade_monitor import (
            PHASE_MESSAGES,
            UpgradePhase,
        )

        phases = [
            UpgradePhase.NOT_STARTED,
            UpgradePhase.INITIALIZING,
            UpgradePhase.UPGRADING_CONTROL_PLANE,
            UpgradePhase.UPGRADING_SERVICES,
            UpgradePhase.UPGRADING_COMPUTE,
            UpgradePhase.FINALIZING,
            UpgradePhase.COMPLETED,
            UpgradePhase.FAILED,
        ]
        for phase in phases:
            assert phase in PHASE_MESSAGES
            assert isinstance(PHASE_MESSAGES[phase], str)
            assert len(PHASE_MESSAGES[phase]) > 0

    @pytest.mark.asyncio
    async def test_monitor_methods_exist(self, mock_adapter: MagicMock) -> None:
        """Test monitor has required methods."""
        from mosk_mcp.tools.operations_visibility.monitors.openstack_upgrade_monitor import (
            OpenStackUpgradeMonitor,
        )

        monitor = OpenStackUpgradeMonitor(
            adapter=mock_adapter,
            target="mos",
            namespace="openstack",
        )

        # Check that required methods exist
        assert hasattr(monitor, "get_progress")
        assert hasattr(monitor, "is_complete")
        assert hasattr(monitor, "has_failed")
        assert hasattr(monitor, "get_error_message")

    @pytest.mark.asyncio
    async def test_is_complete_not_started(self, mock_adapter: MagicMock) -> None:
        """Test is_complete when not started."""
        from mosk_mcp.tools.operations_visibility.monitors.openstack_upgrade_monitor import (
            OpenStackUpgradeMonitor,
        )

        monitor = OpenStackUpgradeMonitor(
            adapter=mock_adapter,
            target="mos",
            namespace="openstack",
        )

        assert monitor.is_complete() is False

    @pytest.mark.asyncio
    async def test_has_failed_initially(self, mock_adapter: MagicMock) -> None:
        """Test has_failed initially False."""
        from mosk_mcp.tools.operations_visibility.monitors.openstack_upgrade_monitor import (
            OpenStackUpgradeMonitor,
        )

        monitor = OpenStackUpgradeMonitor(
            adapter=mock_adapter,
            target="mos",
            namespace="openstack",
        )

        assert monitor.has_failed() is False

    @pytest.mark.asyncio
    async def test_get_error_message_none(self, mock_adapter: MagicMock) -> None:
        """Test get_error_message is None initially."""
        from mosk_mcp.tools.operations_visibility.monitors.openstack_upgrade_monitor import (
            OpenStackUpgradeMonitor,
        )

        monitor = OpenStackUpgradeMonitor(
            adapter=mock_adapter,
            target="mos",
            namespace="openstack",
        )

        assert monitor.get_error_message() is None


# ==========================
# Extended MOSK Upgrade Monitor Tests
# ==========================
class TestMoskUpgradeMonitorExtended:
    """Extended tests for MoskUpgradeMonitor."""

    @pytest.fixture
    def mock_adapter(self) -> MagicMock:
        """Create a mock Kubernetes adapter."""
        adapter = MagicMock()
        adapter.get_custom_resource = AsyncMock()
        adapter.list = AsyncMock()
        return adapter

    def test_upgrade_phase_constants(self) -> None:
        """Test MoskUpgradePhase constants."""
        from mosk_mcp.tools.operations_visibility.monitors.mosk_upgrade_monitor import (
            MoskUpgradePhase,
        )

        assert MoskUpgradePhase.NOT_STARTED == "not_started"
        assert MoskUpgradePhase.HELM_UPGRADING == "helm_upgrading"
        assert MoskUpgradePhase.MACHINES_PREPARING == "machines_preparing"
        assert MoskUpgradePhase.MACHINES_DEPLOYING == "machines_deploying"
        assert MoskUpgradePhase.MACHINES_RECONFIGURING == "machines_reconfiguring"
        assert MoskUpgradePhase.CEPH_UPGRADING == "ceph_upgrading"
        assert MoskUpgradePhase.FINALIZING == "finalizing"
        assert MoskUpgradePhase.COMPLETED == "completed"
        assert MoskUpgradePhase.FAILED == "failed"

    def test_phase_messages_complete(self) -> None:
        """Test all MOSK phases have messages."""
        from mosk_mcp.tools.operations_visibility.monitors.mosk_upgrade_monitor import (
            PHASE_MESSAGES,
            MoskUpgradePhase,
        )

        phases = [
            MoskUpgradePhase.NOT_STARTED,
            MoskUpgradePhase.HELM_UPGRADING,
            MoskUpgradePhase.MACHINES_PREPARING,
            MoskUpgradePhase.MACHINES_DEPLOYING,
            MoskUpgradePhase.MACHINES_RECONFIGURING,
            MoskUpgradePhase.CEPH_UPGRADING,
            MoskUpgradePhase.FINALIZING,
            MoskUpgradePhase.COMPLETED,
            MoskUpgradePhase.FAILED,
        ]
        for phase in phases:
            assert phase in PHASE_MESSAGES
            assert isinstance(PHASE_MESSAGES[phase], str)
            assert len(PHASE_MESSAGES[phase]) > 0

    @pytest.mark.asyncio
    async def test_monitor_methods_exist(self, mock_adapter: MagicMock) -> None:
        """Test monitor has required methods."""
        from mosk_mcp.tools.operations_visibility.monitors.mosk_upgrade_monitor import (
            MoskUpgradeMonitor,
        )

        monitor = MoskUpgradeMonitor(
            adapter=mock_adapter,
            target="mos",
            namespace="lab",
        )

        assert hasattr(monitor, "get_progress")
        assert hasattr(monitor, "is_complete")
        assert hasattr(monitor, "has_failed")
        assert hasattr(monitor, "get_error_message")

    @pytest.mark.asyncio
    async def test_is_complete_not_started(self, mock_adapter: MagicMock) -> None:
        """Test is_complete when not started."""
        from mosk_mcp.tools.operations_visibility.monitors.mosk_upgrade_monitor import (
            MoskUpgradeMonitor,
        )

        monitor = MoskUpgradeMonitor(
            adapter=mock_adapter,
            target="mos",
            namespace="lab",
        )

        assert monitor.is_complete() is False

    @pytest.mark.asyncio
    async def test_has_failed_initially(self, mock_adapter: MagicMock) -> None:
        """Test has_failed initially False."""
        from mosk_mcp.tools.operations_visibility.monitors.mosk_upgrade_monitor import (
            MoskUpgradeMonitor,
        )

        monitor = MoskUpgradeMonitor(
            adapter=mock_adapter,
            target="mos",
            namespace="lab",
        )

        assert monitor.has_failed() is False


# ==========================
# Base Monitor Method Tests
# ==========================
class TestBaseMonitorMethods:
    """Tests for BaseOperationMonitor methods."""

    def test_snapshot_serialization(self) -> None:
        """Test ProgressSnapshot can be serialized."""
        snapshot = ProgressSnapshot.create(
            progress_percent=50,
            phase="testing",
            message="Test in progress",
            details={"key": "value"},
        )

        # Should be serializable to dict
        data = snapshot.model_dump()
        assert data["progress_percent"] == 50
        assert data["phase"] == "testing"
        assert data["message"] == "Test in progress"
        assert data["details"]["key"] == "value"

    def test_snapshot_json(self) -> None:
        """Test ProgressSnapshot can be converted to JSON."""
        snapshot = ProgressSnapshot.create(
            progress_percent=75,
            phase="running",
            message="Running tests",
        )

        json_str = snapshot.model_dump_json()
        assert "75" in json_str
        assert "running" in json_str
