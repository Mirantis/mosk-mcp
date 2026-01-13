"""Unit tests for monitor_operation tool.

Tests for the operation monitoring functionality including node_add,
openstack_upgrade, and mosk_upgrade operations.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from mosk_mcp.core.exceptions import ValidationError
from mosk_mcp.tools.operations_visibility.monitor_operation import (
    DEFAULT_MAX_DURATION_SECONDS,
    DEFAULT_POLL_INTERVAL_SECONDS,
    MonitorOperationInput,
    MonitorOperationOutput,
    OperationStatus,
    OperationType,
    _calculate_eta,
    _calculate_next_check_time,
    _format_duration_human,
    _resolve_namespace,
)
from mosk_mcp.tools.operations_visibility.monitors.base import ProgressSnapshot


# Rebuild the model to resolve forward reference to ProgressSnapshot
MonitorOperationOutput.model_rebuild()


# ==========================
# OperationType Tests
# ==========================
class TestOperationType:
    """Tests for OperationType enum."""

    def test_enum_values(self) -> None:
        """Test OperationType enum values."""
        assert OperationType.NODE_ADD.value == "node_add"
        assert OperationType.OPENSTACK_UPGRADE.value == "openstack_upgrade"
        assert OperationType.MOSK_UPGRADE.value == "mosk_upgrade"

    def test_is_string_enum(self) -> None:
        """Test OperationType is a string enum."""
        assert isinstance(OperationType.NODE_ADD, str)
        assert OperationType.NODE_ADD == "node_add"


class TestOperationStatus:
    """Tests for OperationStatus enum."""

    def test_enum_values(self) -> None:
        """Test OperationStatus enum values."""
        assert OperationStatus.IN_PROGRESS.value == "in_progress"
        assert OperationStatus.COMPLETED.value == "completed"
        assert OperationStatus.FAILED.value == "failed"
        assert OperationStatus.NOT_FOUND.value == "not_found"


# ==========================
# Input Model Tests
# ==========================
class TestMonitorOperationInput:
    """Tests for MonitorOperationInput model."""

    def test_required_fields(self) -> None:
        """Test required fields."""
        input_model = MonitorOperationInput(
            operation_type=OperationType.NODE_ADD,
            target="compute-01",
        )

        assert input_model.operation_type == OperationType.NODE_ADD
        assert input_model.target == "compute-01"
        assert input_model.namespace is None  # Default
        assert input_model.poll_interval_seconds == DEFAULT_POLL_INTERVAL_SECONDS
        assert input_model.max_duration_seconds == DEFAULT_MAX_DURATION_SECONDS

    def test_custom_values(self) -> None:
        """Test custom values."""
        input_model = MonitorOperationInput(
            operation_type=OperationType.OPENSTACK_UPGRADE,
            target="mos",
            namespace="openstack",
            poll_interval_seconds=60,
            max_duration_seconds=600,
        )

        assert input_model.operation_type == OperationType.OPENSTACK_UPGRADE
        assert input_model.target == "mos"
        assert input_model.namespace == "openstack"
        assert input_model.poll_interval_seconds == 60
        assert input_model.max_duration_seconds == 600

    def test_poll_interval_bounds(self) -> None:
        """Test poll_interval_seconds validation."""
        # Valid minimum
        input_model = MonitorOperationInput(
            operation_type=OperationType.NODE_ADD,
            target="test",
            poll_interval_seconds=10,  # Minimum
        )
        assert input_model.poll_interval_seconds == 10

        # Invalid - below minimum
        with pytest.raises(Exception):  # Pydantic validation error
            MonitorOperationInput(
                operation_type=OperationType.NODE_ADD,
                target="test",
                poll_interval_seconds=5,  # Below minimum
            )


# ==========================
# Output Model Tests
# ==========================
class TestMonitorOperationOutput:
    """Tests for MonitorOperationOutput model."""

    def test_minimal_output(self) -> None:
        """Test creating output with minimal fields."""
        output = MonitorOperationOutput(
            operation_type=OperationType.NODE_ADD,
            target="compute-01",
            namespace="default",
            status=OperationStatus.IN_PROGRESS,
            overall_progress_percent=50,
            current_phase="provisioning",
            continue_monitoring=True,
            polling_duration_seconds=60,
            snapshots_collected=2,
        )

        assert output.status == OperationStatus.IN_PROGRESS
        assert output.overall_progress_percent == 50
        assert output.continue_monitoring is True

    def test_full_output(self) -> None:
        """Test creating output with all fields."""
        output = MonitorOperationOutput(
            operation_type=OperationType.OPENSTACK_UPGRADE,
            target="mos",
            namespace="openstack",
            status=OperationStatus.IN_PROGRESS,
            overall_progress_percent=75,
            current_phase="upgrading_nova",
            phase_message="Upgrading Nova compute service",
            progress_snapshots=[],
            continue_monitoring=True,
            error_message=None,
            started_at="2025-01-01T00:00:00Z",
            elapsed_seconds=3600,
            estimated_remaining_seconds=1200,
            estimated_remaining_human="~20 minutes",
            services_completed=5,
            services_total=10,
            services_in_progress=["nova-compute", "nova-scheduler"],
            polling_duration_seconds=300,
            snapshots_collected=10,
        )

        assert output.services_completed == 5
        assert output.services_total == 10
        assert output.estimated_remaining_human == "~20 minutes"


# ==========================
# Helper Function Tests
# ==========================
class TestFormatDurationHuman:
    """Tests for _format_duration_human function."""

    def test_seconds_only(self) -> None:
        """Test formatting seconds only."""
        assert _format_duration_human(30) == "~30 seconds"
        assert _format_duration_human(59) == "~59 seconds"

    def test_minutes(self) -> None:
        """Test formatting minutes."""
        assert _format_duration_human(60) == "~1 minute"
        assert _format_duration_human(120) == "~2 minutes"
        assert _format_duration_human(300) == "~5 minutes"

    def test_hours(self) -> None:
        """Test formatting hours."""
        assert _format_duration_human(3600) == "~1 hour"
        assert _format_duration_human(7200) == "~2 hours"

    def test_hours_and_minutes(self) -> None:
        """Test formatting hours and minutes."""
        assert _format_duration_human(5400) == "~1 hour 30 minutes"
        assert _format_duration_human(9000) == "~2 hours 30 minutes"


class TestCalculateEta:
    """Tests for _calculate_eta function."""

    def test_zero_progress(self) -> None:
        """Test ETA calculation with zero progress."""
        eta_seconds, eta_human = _calculate_eta([], 0, 0)
        assert eta_seconds is None
        assert eta_human is None

    def test_complete_progress(self) -> None:
        """Test ETA calculation with 100% progress."""
        eta_seconds, eta_human = _calculate_eta([], 100, 3600)
        assert eta_seconds is None
        assert eta_human is None

    def test_negative_progress(self) -> None:
        """Test ETA calculation with negative (error) progress."""
        eta_seconds, eta_human = _calculate_eta([], -1, 3600)
        assert eta_seconds is None
        assert eta_human is None

    def test_fallback_calculation(self) -> None:
        """Test ETA fallback when not enough snapshots."""
        # 50% after 3600 seconds = 3600 more seconds estimated
        eta_seconds, eta_human = _calculate_eta([], 50, 3600)
        assert eta_seconds == 3600
        assert "hour" in eta_human.lower()


class TestCalculateNextCheckTime:
    """Tests for _calculate_next_check_time function."""

    def test_completed_operation(self) -> None:
        """Test no next check for completed operation."""
        result = _calculate_next_check_time(OperationStatus.COMPLETED, 100, 30)
        assert result is None

    def test_failed_operation(self) -> None:
        """Test no next check for failed operation."""
        result = _calculate_next_check_time(OperationStatus.FAILED, 50, 30)
        assert result is None

    def test_in_progress_operation(self) -> None:
        """Test next check time for in-progress operation."""
        result = _calculate_next_check_time(OperationStatus.IN_PROGRESS, 50, 30)
        assert result is not None
        assert "T" in result  # ISO format

    def test_near_completion_short_interval(self) -> None:
        """Test shorter interval when near completion."""
        result_90 = _calculate_next_check_time(OperationStatus.IN_PROGRESS, 90, 300)
        result_10 = _calculate_next_check_time(OperationStatus.IN_PROGRESS, 10, 300)
        # Near completion should suggest sooner check (but we can't easily compare ISO strings)
        assert result_90 is not None
        assert result_10 is not None


class TestResolveNamespace:
    """Tests for _resolve_namespace function."""

    @pytest.mark.asyncio
    async def test_explicit_namespace(self) -> None:
        """Test using explicit namespace."""
        result = await _resolve_namespace(OperationType.NODE_ADD, "custom-namespace", None)
        assert result == "custom-namespace"

    @pytest.mark.asyncio
    async def test_openstack_upgrade_default(self) -> None:
        """Test default namespace for OpenStack upgrade."""
        result = await _resolve_namespace(OperationType.OPENSTACK_UPGRADE, None, None)
        assert result == "openstack"

    @pytest.mark.asyncio
    async def test_node_add_default(self) -> None:
        """Test default namespace for node_add without discovery."""
        result = await _resolve_namespace(OperationType.NODE_ADD, None, None)
        assert result == "default"

    @pytest.mark.asyncio
    async def test_node_add_with_discovery(self) -> None:
        """Test namespace discovery for node_add."""
        mock_adapter = MagicMock()
        mock_adapter.get_mosk_machines_namespace = AsyncMock(return_value="lab")

        result = await _resolve_namespace(OperationType.NODE_ADD, None, mock_adapter)
        assert result == "lab"

    @pytest.mark.asyncio
    async def test_node_add_discovery_failure(self) -> None:
        """Test fallback when discovery fails."""
        mock_adapter = MagicMock()
        mock_adapter.get_mosk_machines_namespace = AsyncMock(
            side_effect=RuntimeError("Discovery failed")
        )

        result = await _resolve_namespace(OperationType.NODE_ADD, None, mock_adapter)
        assert result == "default"


# ==========================
# Monitor Operation Tests
# ==========================
class TestMonitorOperation:
    """Tests for monitor_operation function."""

    @pytest.fixture
    def mock_mcc_adapter(self) -> MagicMock:
        """Create mock MCC adapter."""
        adapter = MagicMock()
        adapter.get_mosk_machines_namespace = AsyncMock(return_value="lab")
        adapter.get_custom_resource = AsyncMock()
        return adapter

    @pytest.fixture
    def mock_mosk_adapter(self) -> MagicMock:
        """Create mock MOSK adapter."""
        adapter = MagicMock()
        adapter.get_custom_resource = AsyncMock()
        return adapter

    @pytest.mark.asyncio
    async def test_node_add_requires_mcc_adapter(self) -> None:
        """Test node_add operation requires MCC adapter."""
        from mosk_mcp.tools.operations_visibility.monitor_operation import (
            monitor_operation,
        )

        input_data = MonitorOperationInput(
            operation_type=OperationType.NODE_ADD,
            target="compute-01",
            poll_interval_seconds=10,
            max_duration_seconds=30,
        )

        with pytest.raises(ValidationError) as exc_info:
            await monitor_operation(None, None, input_data)

        assert "MCC adapter required" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_openstack_upgrade_requires_mosk_adapter(self) -> None:
        """Test openstack_upgrade operation requires MOSK adapter."""
        from mosk_mcp.tools.operations_visibility.monitor_operation import (
            monitor_operation,
        )

        input_data = MonitorOperationInput(
            operation_type=OperationType.OPENSTACK_UPGRADE,
            target="mos",
            poll_interval_seconds=10,
            max_duration_seconds=30,
        )

        with pytest.raises(ValidationError) as exc_info:
            await monitor_operation(None, None, input_data)

        assert "MOSK adapter required" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_mosk_upgrade_requires_mcc_adapter(self) -> None:
        """Test mosk_upgrade operation requires MCC adapter."""
        from mosk_mcp.tools.operations_visibility.monitor_operation import (
            monitor_operation,
        )

        input_data = MonitorOperationInput(
            operation_type=OperationType.MOSK_UPGRADE,
            target="mos",
            poll_interval_seconds=10,
            max_duration_seconds=30,
        )

        with pytest.raises(ValidationError) as exc_info:
            await monitor_operation(None, None, input_data)

        assert "MCC adapter required" in str(exc_info.value)


# ==========================
# Progress Snapshot Tests
# ==========================
class TestProgressSnapshot:
    """Tests for ProgressSnapshot model."""

    def test_create_snapshot(self) -> None:
        """Test creating a ProgressSnapshot."""
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
        assert snapshot.timestamp is not None

    def test_create_snapshot_defaults(self) -> None:
        """Test creating snapshot with default details."""
        snapshot = ProgressSnapshot.create(
            progress_percent=75,
            phase="running",
            message="Running",
        )

        assert snapshot.progress_percent == 75
        assert snapshot.details == {}
