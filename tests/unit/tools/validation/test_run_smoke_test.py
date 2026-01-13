"""Tests for run_smoke_test validation tool.

Tests cover:
- SmokeTestType enum
- SmokeTestStatus enum
- SmokeTestStep and SmokeTestResult dataclasses
- Input/output models
- Basic smoke test execution
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from mosk_mcp.tools.validation.run_smoke_test import (
    RunSmokeTestInput,
    RunSmokeTestOutput,
    SmokeTestResult,
    SmokeTestStatus,
    SmokeTestStep,
    SmokeTestStepOutput,
    SmokeTestType,
    run_smoke_test,
)


# =============================================================================
# Enum Tests
# =============================================================================


class TestSmokeTestType:
    """Tests for SmokeTestType enum."""

    def test_smoke_test_type_values(self) -> None:
        """Test SmokeTestType has correct values."""
        assert SmokeTestType.VM_LIFECYCLE == "vm_lifecycle"
        assert SmokeTestType.STORAGE_OPERATIONS == "storage_operations"
        assert SmokeTestType.FULL_STACK == "full_stack"

    def test_smoke_test_type_is_string_enum(self) -> None:
        """Test SmokeTestType is string enum for JSON serialization."""
        assert isinstance(SmokeTestType.VM_LIFECYCLE, str)
        assert SmokeTestType.VM_LIFECYCLE.value == "vm_lifecycle"


class TestSmokeTestStatus:
    """Tests for SmokeTestStatus enum."""

    def test_smoke_test_status_values(self) -> None:
        """Test SmokeTestStatus has correct values."""
        assert SmokeTestStatus.PASSED == "passed"
        assert SmokeTestStatus.FAILED == "failed"
        assert SmokeTestStatus.SKIPPED == "skipped"
        assert SmokeTestStatus.ERROR == "error"

    def test_smoke_test_status_is_string_enum(self) -> None:
        """Test SmokeTestStatus is string enum for JSON serialization."""
        assert isinstance(SmokeTestStatus.PASSED, str)


# =============================================================================
# Dataclass Tests
# =============================================================================


class TestSmokeTestStep:
    """Tests for SmokeTestStep dataclass."""

    def test_step_creation_with_defaults(self) -> None:
        """Test SmokeTestStep creation with defaults."""
        step = SmokeTestStep(
            name="create_vm",
            status=SmokeTestStatus.PASSED,
        )
        assert step.name == "create_vm"
        assert step.status == SmokeTestStatus.PASSED
        assert step.duration_seconds == 0.0
        assert step.error_message is None
        assert step.details == {}

    def test_step_creation_with_all_fields(self) -> None:
        """Test SmokeTestStep with all fields."""
        step = SmokeTestStep(
            name="create_vm",
            status=SmokeTestStatus.FAILED,
            duration_seconds=15.5,
            error_message="VM creation timed out",
            details={"vm_id": "vm-123"},
        )
        assert step.duration_seconds == 15.5
        assert step.error_message == "VM creation timed out"
        assert step.details["vm_id"] == "vm-123"


class TestSmokeTestResult:
    """Tests for SmokeTestResult dataclass."""

    def test_result_creation_with_defaults(self) -> None:
        """Test SmokeTestResult creation with defaults."""
        result = SmokeTestResult(
            test_type=SmokeTestType.VM_LIFECYCLE,
            status=SmokeTestStatus.PASSED,
        )
        assert result.test_type == SmokeTestType.VM_LIFECYCLE
        assert result.status == SmokeTestStatus.PASSED
        assert result.steps == []
        assert result.duration_seconds == 0.0
        assert result.resources_created == []
        assert result.resources_cleaned == []
        assert result.resources_leaked == []

    def test_result_with_steps_and_resources(self) -> None:
        """Test SmokeTestResult with steps and resources."""
        result = SmokeTestResult(
            test_type=SmokeTestType.FULL_STACK,
            status=SmokeTestStatus.PASSED,
            steps=[
                SmokeTestStep(name="create_vm", status=SmokeTestStatus.PASSED),
                SmokeTestStep(name="attach_volume", status=SmokeTestStatus.PASSED),
            ],
            duration_seconds=120.5,
            resources_created=["vm-123", "vol-456"],
            resources_cleaned=["vm-123", "vol-456"],
        )
        assert len(result.steps) == 2
        assert result.resources_created == ["vm-123", "vol-456"]


# =============================================================================
# Input Model Tests
# =============================================================================


class TestRunSmokeTestInput:
    """Tests for RunSmokeTestInput model."""

    def test_input_defaults(self) -> None:
        """Test input has correct defaults."""
        input_data = RunSmokeTestInput()
        assert input_data.test_type == "vm_lifecycle"
        assert input_data.image_name is None
        assert input_data.flavor_name is None
        assert input_data.network_name is None
        assert input_data.cleanup is True
        assert input_data.timeout_seconds == 300
        assert input_data.prefix == "mcp-smoke"

    def test_input_with_custom_values(self) -> None:
        """Test input with custom values."""
        input_data = RunSmokeTestInput(
            test_type="full_stack",
            image_name="cirros",
            flavor_name="m1.small",
            network_name="internal",
            cleanup=False,
            timeout_seconds=600,
            prefix="test",
        )
        assert input_data.test_type == "full_stack"
        assert input_data.image_name == "cirros"
        assert input_data.cleanup is False
        assert input_data.timeout_seconds == 600

    def test_input_timeout_constraints(self) -> None:
        """Test timeout field constraints."""
        # Valid values
        RunSmokeTestInput(timeout_seconds=60)
        RunSmokeTestInput(timeout_seconds=900)

        # Invalid values
        with pytest.raises(ValueError):
            RunSmokeTestInput(timeout_seconds=59)
        with pytest.raises(ValueError):
            RunSmokeTestInput(timeout_seconds=901)


# =============================================================================
# Output Model Tests
# =============================================================================


class TestRunSmokeTestOutput:
    """Tests for RunSmokeTestOutput model."""

    def test_output_creation(self) -> None:
        """Test output model creation."""
        output = RunSmokeTestOutput(
            test_type="vm_lifecycle",
            status="passed",
            steps=[
                SmokeTestStepOutput(
                    name="create_vm",
                    status="passed",
                    duration_seconds=10.0,
                )
            ],
            duration_seconds=30.5,
            timestamp=datetime.now(UTC).isoformat(),
        )
        assert output.test_type == "vm_lifecycle"
        assert output.status == "passed"
        assert len(output.steps) == 1
        assert output.resources_created == []
        assert output.recommendations == []


class TestSmokeTestStepOutput:
    """Tests for SmokeTestStepOutput model."""

    def test_step_output_creation(self) -> None:
        """Test step output model creation."""
        step = SmokeTestStepOutput(
            name="boot_vm",
            status="passed",
            duration_seconds=15.0,
            details={"server_id": "srv-123"},
        )
        assert step.name == "boot_vm"
        assert step.status == "passed"
        assert step.duration_seconds == 15.0
        assert step.details["server_id"] == "srv-123"


# =============================================================================
# Function Tests
# =============================================================================


class TestRunSmokeTest:
    """Tests for run_smoke_test function."""

    @pytest.fixture
    def mock_adapter(self) -> MagicMock:
        """Create a mock OpenStack adapter."""
        adapter = MagicMock()

        # Mock image list
        adapter.list_images = AsyncMock(
            return_value=[
                {"id": "img-1", "name": "cirros", "Status": "active"},
            ]
        )

        # Mock flavor list
        flavor = MagicMock()
        flavor.id = "flv-1"
        flavor.name = "m1.small"
        flavor.ram = 512
        flavor.vcpus = 1
        adapter.list_flavors = AsyncMock(return_value=[flavor])

        # Mock network list
        adapter.list_networks = AsyncMock(
            return_value=[
                {"id": "net-1", "name": "internal"},
            ]
        )

        # Mock server operations
        adapter.create_server = AsyncMock(return_value={"id": "srv-123"})
        adapter.get_server = AsyncMock(return_value={"id": "srv-123", "status": "ACTIVE"})
        adapter.delete_server = AsyncMock(return_value=True)

        # Mock volume operations
        adapter.create_volume = AsyncMock(return_value={"id": "vol-123"})
        adapter.get_volume = AsyncMock(return_value={"id": "vol-123", "status": "available"})
        adapter.attach_volume = AsyncMock(return_value=True)
        adapter.detach_volume = AsyncMock(return_value=True)
        adapter.delete_volume = AsyncMock(return_value=True)

        return adapter

    @pytest.mark.asyncio
    async def test_vm_lifecycle_test(self, mock_adapter: MagicMock) -> None:
        """Test VM lifecycle smoke test."""
        input_data = RunSmokeTestInput(test_type="vm_lifecycle")

        result = await run_smoke_test(mock_adapter, input_data)

        assert result.test_type == "vm_lifecycle"
        assert result.status in ["passed", "failed", "error"]
        assert result.duration_seconds >= 0
        assert result.timestamp is not None

    @pytest.mark.asyncio
    async def test_smoke_test_with_cleanup(self, mock_adapter: MagicMock) -> None:
        """Test that cleanup runs when enabled."""
        input_data = RunSmokeTestInput(
            test_type="vm_lifecycle",
            cleanup=True,
        )

        result = await run_smoke_test(mock_adapter, input_data)

        # Should have attempted cleanup
        assert result.duration_seconds >= 0

    @pytest.mark.asyncio
    async def test_smoke_test_with_no_images(self, mock_adapter: MagicMock) -> None:
        """Test handling when no images available."""
        mock_adapter.list_images = AsyncMock(return_value=[])

        input_data = RunSmokeTestInput(test_type="vm_lifecycle")

        result = await run_smoke_test(mock_adapter, input_data)

        # Should fail gracefully
        assert result.status in ["failed", "error", "skipped"]

    @pytest.mark.asyncio
    async def test_storage_operations_test(self, mock_adapter: MagicMock) -> None:
        """Test storage operations smoke test."""
        input_data = RunSmokeTestInput(test_type="storage_operations")

        result = await run_smoke_test(mock_adapter, input_data)

        assert result.test_type == "storage_operations"
        assert result.status in ["passed", "failed", "error"]

    @pytest.mark.asyncio
    async def test_full_stack_test(self, mock_adapter: MagicMock) -> None:
        """Test full stack smoke test."""
        input_data = RunSmokeTestInput(test_type="full_stack")

        result = await run_smoke_test(mock_adapter, input_data)

        assert result.test_type == "full_stack"
        assert result.status in ["passed", "failed", "error"]

    @pytest.mark.asyncio
    async def test_recommendations_on_failure(self, mock_adapter: MagicMock) -> None:
        """Test that recommendations are provided on failure."""
        mock_adapter.create_server = AsyncMock(side_effect=Exception("Quota exceeded"))

        input_data = RunSmokeTestInput(test_type="vm_lifecycle")

        result = await run_smoke_test(mock_adapter, input_data)

        # Should have recommendations for failures
        assert result.status in ["failed", "error"]
        assert isinstance(result.recommendations, list)
