"""Unit tests for monitor_operation tool."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mosk_mcp.core.exceptions import ToolExecutionError, ValidationError
from mosk_mcp.tools.operations_visibility.monitor_operation import (
    MonitorOperationInput,
    OperationStatus,
    OperationType,
    _calculate_eta,
    _calculate_next_check_time,
    _format_duration_human,
    _resolve_namespace,
    monitor_operation,
)
from mosk_mcp.tools.operations_visibility.monitors.base import ProgressSnapshot


class TestFormatDurationHuman:
    """Tests for _format_duration_human helper."""

    def test_format_seconds(self):
        """Test formatting seconds."""
        result = _format_duration_human(45)
        assert result == "~45 seconds"

    def test_format_one_minute(self):
        """Test formatting one minute."""
        result = _format_duration_human(60)
        assert result == "~1 minute"

    def test_format_multiple_minutes(self):
        """Test formatting multiple minutes."""
        result = _format_duration_human(300)
        assert result == "~5 minutes"

    def test_format_one_hour(self):
        """Test formatting one hour."""
        result = _format_duration_human(3600)
        assert result == "~1 hour"

    def test_format_hours_and_minutes(self):
        """Test formatting hours and minutes."""
        result = _format_duration_human(5400)  # 1.5 hours
        assert result == "~1 hour 30 minutes"

    def test_format_multiple_hours(self):
        """Test formatting multiple hours."""
        result = _format_duration_human(7200)
        assert result == "~2 hours"

    def test_format_hours_and_one_minute(self):
        """Test formatting hours with one minute."""
        result = _format_duration_human(3660)  # 1 hour 1 minute
        assert result == "~1 hour 1 minute"


class TestCalculateEta:
    """Tests for _calculate_eta helper."""

    def test_eta_with_no_progress(self):
        """Test ETA with no progress."""
        result = _calculate_eta([], 0, 100)
        assert result == (None, None)

    def test_eta_with_complete_progress(self):
        """Test ETA when progress is complete."""
        result = _calculate_eta([], 100, 100)
        assert result == (None, None)

    def test_eta_with_sufficient_snapshots(self):
        """Test ETA calculation with sufficient snapshots."""
        now = datetime.utcnow()
        snapshots = [
            ProgressSnapshot(
                timestamp=(now.replace(microsecond=0)).isoformat() + "Z",
                progress_percent=10,
                phase="phase1",
                message="msg",
            ),
            ProgressSnapshot(
                timestamp=(
                    now.replace(second=now.second + 30 if now.second < 30 else 30, microsecond=0)
                ).isoformat()
                + "Z",
                progress_percent=20,
                phase="phase2",
                message="msg",
            ),
        ]

        # Progress went from 10% to 20% in 30 seconds
        # Rate: 10% / 30s = 0.33% per second
        # Remaining: 80%, so ~240 seconds
        eta_seconds, eta_human = _calculate_eta(snapshots, 20, 60)

        assert eta_seconds is not None
        assert eta_human is not None
        assert "minute" in eta_human or "second" in eta_human

    def test_eta_fallback_from_elapsed(self):
        """Test ETA fallback using elapsed time extrapolation."""
        # With only one snapshot, fallback is used
        snapshots = [
            ProgressSnapshot(
                timestamp=datetime.utcnow().isoformat() + "Z",
                progress_percent=25,
                phase="phase",
                message="msg",
            )
        ]

        # At 25% after 100 seconds, estimate total time ~400 seconds
        # Remaining ~300 seconds
        eta_seconds, eta_human = _calculate_eta(snapshots, 25, 100)

        assert eta_seconds is not None
        assert eta_seconds > 0
        assert eta_human is not None

    def test_eta_capped_at_24_hours(self):
        """Test ETA is capped at 24 hours."""
        # Very slow progress - should cap
        snapshots = [
            ProgressSnapshot(
                timestamp=datetime.utcnow().isoformat() + "Z",
                progress_percent=1,
                phase="phase",
                message="msg",
            )
        ]

        eta_seconds, _ = _calculate_eta(snapshots, 1, 100)

        if eta_seconds is not None:
            assert eta_seconds <= 86400  # 24 hours max


class TestCalculateNextCheckTime:
    """Tests for _calculate_next_check_time helper."""

    def test_no_next_check_when_completed(self):
        """Test no next check when operation is completed."""
        result = _calculate_next_check_time(OperationStatus.COMPLETED, 100, 30)
        assert result is None

    def test_no_next_check_when_failed(self):
        """Test no next check when operation has failed."""
        result = _calculate_next_check_time(OperationStatus.FAILED, 50, 30)
        assert result is None

    def test_frequent_check_near_completion(self):
        """Test more frequent checks near completion (>=90%)."""
        result = _calculate_next_check_time(OperationStatus.IN_PROGRESS, 95, 120)
        assert result is not None
        # Should be ISO timestamp
        datetime.fromisoformat(result.replace("Z", "+00:00"))

    def test_less_frequent_check_early_stages(self):
        """Test less frequent checks in early stages."""
        result = _calculate_next_check_time(OperationStatus.IN_PROGRESS, 10, 30)
        assert result is not None
        datetime.fromisoformat(result.replace("Z", "+00:00"))


class TestResolveNamespace:
    """Tests for _resolve_namespace helper."""

    @pytest.mark.asyncio
    async def test_use_provided_namespace(self):
        """Test using explicitly provided namespace."""
        result = await _resolve_namespace(OperationType.NODE_ADD, "custom-ns", None)
        assert result == "custom-ns"

    @pytest.mark.asyncio
    async def test_openstack_upgrade_default_namespace(self):
        """Test default namespace for OpenStack upgrade."""
        result = await _resolve_namespace(OperationType.OPENSTACK_UPGRADE, None, None)
        assert result == "openstack"

    @pytest.mark.asyncio
    async def test_node_add_default_namespace(self):
        """Test default namespace for node add without MCC adapter."""
        result = await _resolve_namespace(OperationType.NODE_ADD, None, None)
        assert result == "default"

    @pytest.mark.asyncio
    async def test_node_add_auto_discover_namespace(self):
        """Test auto-discovering namespace from MCC adapter."""
        mock_adapter = AsyncMock()
        mock_adapter.get_mosk_machines_namespace = AsyncMock(return_value="lab")

        result = await _resolve_namespace(OperationType.NODE_ADD, None, mock_adapter)
        assert result == "lab"

    @pytest.mark.asyncio
    async def test_node_add_fallback_on_discovery_error(self):
        """Test fallback when namespace discovery fails."""
        mock_adapter = AsyncMock()
        mock_adapter.get_mosk_machines_namespace = AsyncMock(
            side_effect=Exception("Discovery failed")
        )

        result = await _resolve_namespace(OperationType.NODE_ADD, None, mock_adapter)
        assert result == "default"


class TestMonitorOperationInput:
    """Tests for MonitorOperationInput model."""

    def test_required_fields(self):
        """Test required fields."""
        with pytest.raises(Exception):  # Pydantic validation error
            MonitorOperationInput()

    def test_valid_input(self):
        """Test valid input with defaults."""
        input_data = MonitorOperationInput(
            operation_type=OperationType.NODE_ADD,
            target="compute-01",
        )

        assert input_data.operation_type == OperationType.NODE_ADD
        assert input_data.target == "compute-01"
        assert input_data.namespace is None
        assert input_data.poll_interval_seconds == 30
        assert input_data.max_duration_seconds == 300

    def test_custom_polling_params(self):
        """Test custom polling parameters."""
        input_data = MonitorOperationInput(
            operation_type=OperationType.OPENSTACK_UPGRADE,
            target="mos",
            namespace="openstack",
            poll_interval_seconds=60,
            max_duration_seconds=600,
        )

        assert input_data.poll_interval_seconds == 60
        assert input_data.max_duration_seconds == 600

    def test_poll_interval_bounds(self):
        """Test poll interval bounds validation."""
        # Too low
        with pytest.raises(ValueError):
            MonitorOperationInput(
                operation_type=OperationType.NODE_ADD,
                target="node",
                poll_interval_seconds=5,
            )

        # Too high
        with pytest.raises(ValueError):
            MonitorOperationInput(
                operation_type=OperationType.NODE_ADD,
                target="node",
                poll_interval_seconds=400,
            )

    def test_max_duration_bounds(self):
        """Test max duration bounds validation."""
        # Too low
        with pytest.raises(ValueError):
            MonitorOperationInput(
                operation_type=OperationType.NODE_ADD,
                target="node",
                max_duration_seconds=10,
            )

        # Too high
        with pytest.raises(ValueError):
            MonitorOperationInput(
                operation_type=OperationType.NODE_ADD,
                target="node",
                max_duration_seconds=3600,
            )


class TestMonitorOperationFunction:
    """Tests for monitor_operation function."""

    @pytest.fixture
    def mock_mcc_adapter(self):
        """Create mock MCC adapter."""
        adapter = AsyncMock()
        adapter.connect = AsyncMock()
        adapter.get_mosk_machines_namespace = AsyncMock(return_value="default")
        return adapter

    @pytest.fixture
    def mock_mosk_adapter(self):
        """Create mock MOSK adapter."""
        adapter = AsyncMock()
        adapter.connect = AsyncMock()
        return adapter

    @pytest.mark.asyncio
    async def test_node_add_requires_mcc_adapter(self):
        """Test node_add requires MCC adapter."""
        with pytest.raises(ValidationError) as exc_info:
            await monitor_operation(
                mcc_adapter=None,
                mosk_adapter=None,
                input_data=MonitorOperationInput(
                    operation_type=OperationType.NODE_ADD,
                    target="compute-01",
                ),
            )

        assert "MCC adapter required" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_openstack_upgrade_requires_mosk_adapter(self):
        """Test openstack_upgrade requires MOSK adapter."""
        with pytest.raises(ValidationError) as exc_info:
            await monitor_operation(
                mcc_adapter=None,
                mosk_adapter=None,
                input_data=MonitorOperationInput(
                    operation_type=OperationType.OPENSTACK_UPGRADE,
                    target="mos",
                ),
            )

        assert "MOSK adapter required" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_mosk_upgrade_requires_mcc_adapter(self):
        """Test mosk_upgrade requires MCC adapter."""
        with pytest.raises(ValidationError) as exc_info:
            await monitor_operation(
                mcc_adapter=None,
                mosk_adapter=None,
                input_data=MonitorOperationInput(
                    operation_type=OperationType.MOSK_UPGRADE,
                    target="mos",
                ),
            )

        assert "MCC adapter required" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_monitor_node_add_completes(self, mock_mcc_adapter):
        """Test monitoring node add that completes."""
        # Create mock monitor
        mock_snapshot = ProgressSnapshot(
            timestamp=datetime.utcnow().isoformat() + "Z",
            progress_percent=100,
            phase="Ready",
            message="Node provisioning complete",
            details={},
        )

        with patch(
            "mosk_mcp.tools.operations_visibility.monitor_operation.NodeAddMonitor"
        ) as MockMonitor:
            mock_monitor = MagicMock()
            mock_monitor.poll = AsyncMock(return_value=mock_snapshot)
            mock_monitor.is_complete = MagicMock(return_value=True)
            mock_monitor.has_failed = MagicMock(return_value=False)
            mock_monitor.started_at = datetime.utcnow().isoformat() + "Z"
            mock_monitor.completed_at = datetime.utcnow().isoformat() + "Z"
            mock_monitor.get_error_message = MagicMock(return_value=None)
            MockMonitor.return_value = mock_monitor

            result = await monitor_operation(
                mcc_adapter=mock_mcc_adapter,
                mosk_adapter=None,
                input_data=MonitorOperationInput(
                    operation_type=OperationType.NODE_ADD,
                    target="compute-01",
                    namespace="default",
                    poll_interval_seconds=10,
                    max_duration_seconds=30,
                ),
            )

        assert result.status == OperationStatus.COMPLETED
        assert result.continue_monitoring is False
        assert result.overall_progress_percent == 100
        assert result.snapshots_collected >= 1

    @pytest.mark.asyncio
    async def test_monitor_operation_fails(self, mock_mcc_adapter):
        """Test monitoring operation that fails."""
        mock_snapshot = ProgressSnapshot(
            timestamp=datetime.utcnow().isoformat() + "Z",
            progress_percent=50,
            phase="Failed",
            message="Provisioning failed",
            details={"error": "BMC unreachable"},
        )

        with patch(
            "mosk_mcp.tools.operations_visibility.monitor_operation.NodeAddMonitor"
        ) as MockMonitor:
            mock_monitor = MagicMock()
            mock_monitor.poll = AsyncMock(return_value=mock_snapshot)
            mock_monitor.is_complete = MagicMock(return_value=False)
            mock_monitor.has_failed = MagicMock(return_value=True)
            mock_monitor.started_at = datetime.utcnow().isoformat() + "Z"
            mock_monitor.completed_at = None
            mock_monitor.get_error_message = MagicMock(return_value="BMC unreachable")
            MockMonitor.return_value = mock_monitor

            result = await monitor_operation(
                mcc_adapter=mock_mcc_adapter,
                mosk_adapter=None,
                input_data=MonitorOperationInput(
                    operation_type=OperationType.NODE_ADD,
                    target="compute-01",
                    namespace="default",
                    poll_interval_seconds=10,
                    max_duration_seconds=30,
                ),
            )

        assert result.status == OperationStatus.FAILED
        assert result.continue_monitoring is False
        assert result.error_message == "BMC unreachable"

    @pytest.mark.asyncio
    async def test_monitor_operation_in_progress(self, mock_mcc_adapter):
        """Test monitoring operation still in progress after polling window."""
        mock_snapshot = ProgressSnapshot(
            timestamp=datetime.utcnow().isoformat() + "Z",
            progress_percent=50,
            phase="Provisioning",
            message="Installing OS",
            details={},
        )

        with patch(
            "mosk_mcp.tools.operations_visibility.monitor_operation.NodeAddMonitor"
        ) as MockMonitor:
            mock_monitor = MagicMock()
            mock_monitor.poll = AsyncMock(return_value=mock_snapshot)
            mock_monitor.is_complete = MagicMock(return_value=False)
            mock_monitor.has_failed = MagicMock(return_value=False)
            mock_monitor.started_at = datetime.utcnow().isoformat() + "Z"
            mock_monitor.completed_at = None
            mock_monitor.get_error_message = MagicMock(return_value=None)
            MockMonitor.return_value = mock_monitor

            with patch("mosk_mcp.tools.operations_visibility.monitor_operation.asyncio.sleep"):
                result = await monitor_operation(
                    mcc_adapter=mock_mcc_adapter,
                    mosk_adapter=None,
                    input_data=MonitorOperationInput(
                        operation_type=OperationType.NODE_ADD,
                        target="compute-01",
                        namespace="default",
                        poll_interval_seconds=10,
                        max_duration_seconds=30,  # Only 3 polls max
                    ),
                )

        assert result.status == OperationStatus.IN_PROGRESS
        assert result.continue_monitoring is True
        assert result.next_check_recommended is not None

    @pytest.mark.asyncio
    async def test_monitor_openstack_upgrade(self, mock_mosk_adapter):
        """Test monitoring OpenStack upgrade."""
        mock_snapshot = ProgressSnapshot(
            timestamp=datetime.utcnow().isoformat() + "Z",
            progress_percent=75,
            phase="Upgrading",
            message="Upgrading services",
            details={
                "services_completed": 15,
                "services_total": 20,
                "services_in_progress": ["nova", "neutron"],
            },
        )

        with patch(
            "mosk_mcp.tools.operations_visibility.monitor_operation.OpenStackUpgradeMonitor"
        ) as MockMonitor:
            mock_monitor = MagicMock()
            mock_monitor.poll = AsyncMock(return_value=mock_snapshot)
            mock_monitor.is_complete = MagicMock(return_value=False)
            mock_monitor.has_failed = MagicMock(return_value=False)
            mock_monitor.started_at = datetime.utcnow().isoformat() + "Z"
            mock_monitor.completed_at = None
            mock_monitor.get_error_message = MagicMock(return_value=None)
            MockMonitor.return_value = mock_monitor

            with patch("mosk_mcp.tools.operations_visibility.monitor_operation.asyncio.sleep"):
                result = await monitor_operation(
                    mcc_adapter=None,
                    mosk_adapter=mock_mosk_adapter,
                    input_data=MonitorOperationInput(
                        operation_type=OperationType.OPENSTACK_UPGRADE,
                        target="mos",
                        poll_interval_seconds=10,
                        max_duration_seconds=30,
                    ),
                )

        assert result.operation_type == OperationType.OPENSTACK_UPGRADE
        assert result.namespace == "openstack"
        assert result.services_completed == 15
        assert result.services_total == 20
        assert result.services_in_progress == ["nova", "neutron"]

    @pytest.mark.asyncio
    async def test_monitor_mosk_upgrade(self, mock_mcc_adapter):
        """Test monitoring MOSK platform upgrade."""
        mock_snapshot = ProgressSnapshot(
            timestamp=datetime.utcnow().isoformat() + "Z",
            progress_percent=60,
            phase="Upgrading",
            message="Upgrading machines",
            details={
                "machines_completed": 3,
                "machines_total": 5,
                "machines_in_progress": [
                    {"name": "compute-01", "progress": 80},
                    {"name": "compute-02", "progress": 40},
                ],
            },
        )

        with patch(
            "mosk_mcp.tools.operations_visibility.monitor_operation.MoskUpgradeMonitor"
        ) as MockMonitor:
            mock_monitor = MagicMock()
            mock_monitor.poll = AsyncMock(return_value=mock_snapshot)
            mock_monitor.is_complete = MagicMock(return_value=False)
            mock_monitor.has_failed = MagicMock(return_value=False)
            mock_monitor.started_at = datetime.utcnow().isoformat() + "Z"
            mock_monitor.completed_at = None
            mock_monitor.get_error_message = MagicMock(return_value=None)
            MockMonitor.return_value = mock_monitor

            with patch("mosk_mcp.tools.operations_visibility.monitor_operation.asyncio.sleep"):
                result = await monitor_operation(
                    mcc_adapter=mock_mcc_adapter,
                    mosk_adapter=None,
                    input_data=MonitorOperationInput(
                        operation_type=OperationType.MOSK_UPGRADE,
                        target="mos",
                        namespace="lab",
                        poll_interval_seconds=10,
                        max_duration_seconds=30,
                    ),
                )

        assert result.operation_type == OperationType.MOSK_UPGRADE
        assert result.machines_completed == 3
        assert result.machines_total == 5
        assert len(result.machines_in_progress) == 2

    @pytest.mark.asyncio
    async def test_monitor_collects_multiple_snapshots(self, mock_mcc_adapter):
        """Test that multiple snapshots are collected during polling."""
        poll_count = [0]

        def create_snapshot():
            poll_count[0] += 1
            return ProgressSnapshot(
                timestamp=datetime.utcnow().isoformat() + "Z",
                progress_percent=poll_count[0] * 25,
                phase="Phase" + str(poll_count[0]),
                message="Message " + str(poll_count[0]),
                details={},
            )

        with patch(
            "mosk_mcp.tools.operations_visibility.monitor_operation.NodeAddMonitor"
        ) as MockMonitor:
            mock_monitor = MagicMock()
            mock_monitor.poll = AsyncMock(side_effect=lambda: create_snapshot())
            mock_monitor.is_complete = MagicMock(side_effect=lambda: poll_count[0] >= 3)
            mock_monitor.has_failed = MagicMock(return_value=False)
            mock_monitor.started_at = datetime.utcnow().isoformat() + "Z"
            mock_monitor.completed_at = datetime.utcnow().isoformat() + "Z"
            mock_monitor.get_error_message = MagicMock(return_value=None)
            MockMonitor.return_value = mock_monitor

            with patch("mosk_mcp.tools.operations_visibility.monitor_operation.asyncio.sleep"):
                result = await monitor_operation(
                    mcc_adapter=mock_mcc_adapter,
                    mosk_adapter=None,
                    input_data=MonitorOperationInput(
                        operation_type=OperationType.NODE_ADD,
                        target="compute-01",
                        namespace="default",
                        poll_interval_seconds=10,
                        max_duration_seconds=60,
                    ),
                )

        assert result.snapshots_collected == 3
        assert len(result.progress_snapshots) == 3
        # Verify snapshots are in order
        assert result.progress_snapshots[0].progress_percent == 25
        assert result.progress_snapshots[1].progress_percent == 50
        assert result.progress_snapshots[2].progress_percent == 75

    @pytest.mark.asyncio
    async def test_monitor_handles_poll_exception(self, mock_mcc_adapter):
        """Test handling exception during polling."""
        with patch(
            "mosk_mcp.tools.operations_visibility.monitor_operation.NodeAddMonitor"
        ) as MockMonitor:
            mock_monitor = MagicMock()
            mock_monitor.poll = AsyncMock(side_effect=Exception("Poll failed"))
            MockMonitor.return_value = mock_monitor

            with pytest.raises(ToolExecutionError) as exc_info:
                await monitor_operation(
                    mcc_adapter=mock_mcc_adapter,
                    mosk_adapter=None,
                    input_data=MonitorOperationInput(
                        operation_type=OperationType.NODE_ADD,
                        target="compute-01",
                    ),
                )

        assert "Failed to monitor operation" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_timestamp_in_output(self, mock_mcc_adapter):
        """Test that polling duration is tracked."""
        mock_snapshot = ProgressSnapshot(
            timestamp=datetime.utcnow().isoformat() + "Z",
            progress_percent=100,
            phase="Complete",
            message="Done",
            details={},
        )

        with patch(
            "mosk_mcp.tools.operations_visibility.monitor_operation.NodeAddMonitor"
        ) as MockMonitor:
            mock_monitor = MagicMock()
            mock_monitor.poll = AsyncMock(return_value=mock_snapshot)
            mock_monitor.is_complete = MagicMock(return_value=True)
            mock_monitor.has_failed = MagicMock(return_value=False)
            mock_monitor.started_at = datetime.utcnow().isoformat() + "Z"
            mock_monitor.completed_at = datetime.utcnow().isoformat() + "Z"
            mock_monitor.get_error_message = MagicMock(return_value=None)
            MockMonitor.return_value = mock_monitor

            result = await monitor_operation(
                mcc_adapter=mock_mcc_adapter,
                mosk_adapter=None,
                input_data=MonitorOperationInput(
                    operation_type=OperationType.NODE_ADD,
                    target="compute-01",
                ),
            )

        assert result.polling_duration_seconds >= 0
        assert result.started_at is not None
        assert result.completed_at is not None
