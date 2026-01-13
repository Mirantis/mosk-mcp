"""Tests for run_mosk_platform_validation tool.

Tests cover:
- ValidationLevel enum
- ValidationStatus enum
- TierResult dataclass
- Input/output models
"""

from datetime import UTC, datetime

import pytest

from mosk_mcp.tools.common import (
    TierResult,
    TierResultOutput,
    ValidationLevel,
    ValidationStatus,
)
from mosk_mcp.tools.validation.run_mosk_platform_validation import (
    RunMoskPlatformValidationInput,
    RunMoskPlatformValidationOutput,
)


# =============================================================================
# Enum Tests
# =============================================================================


class TestValidationLevel:
    """Tests for ValidationLevel enum."""

    def test_validation_level_values(self) -> None:
        """Test ValidationLevel has correct values."""
        assert ValidationLevel.QUICK == "quick"
        assert ValidationLevel.STANDARD == "standard"
        assert ValidationLevel.COMPREHENSIVE == "comprehensive"

    def test_validation_level_is_string_enum(self) -> None:
        """Test ValidationLevel is string enum for JSON serialization."""
        assert isinstance(ValidationLevel.QUICK, str)
        assert ValidationLevel.QUICK.value == "quick"


class TestValidationStatus:
    """Tests for ValidationStatus enum."""

    def test_validation_status_values(self) -> None:
        """Test ValidationStatus has correct values."""
        assert ValidationStatus.PASSED == "passed"
        assert ValidationStatus.PASSED_WITH_WARNINGS == "passed_with_warnings"
        assert ValidationStatus.FAILED == "failed"
        assert ValidationStatus.ERROR == "error"

    def test_validation_status_is_string_enum(self) -> None:
        """Test ValidationStatus is string enum for JSON serialization."""
        assert isinstance(ValidationStatus.PASSED, str)


# =============================================================================
# Dataclass Tests
# =============================================================================


class TestTierResult:
    """Tests for TierResult dataclass."""

    def test_tier_result_creation_with_defaults(self) -> None:
        """Test TierResult creation with defaults."""
        result = TierResult(
            tier=1,
            name="Kubernetes Infrastructure",
            status=ValidationStatus.PASSED,
        )
        assert result.tier == 1
        assert result.name == "Kubernetes Infrastructure"
        assert result.status == ValidationStatus.PASSED
        assert result.checks_passed == 0
        assert result.checks_failed == 0
        assert result.checks_skipped == 0
        assert result.duration_seconds == 0.0
        assert result.details == {}
        assert result.error_message is None

    def test_tier_result_with_all_fields(self) -> None:
        """Test TierResult with all fields."""
        result = TierResult(
            tier=2,
            name="Platform Services",
            status=ValidationStatus.PASSED_WITH_WARNINGS,
            checks_passed=8,
            checks_failed=0,
            checks_skipped=2,
            duration_seconds=45.5,
            details={"services_checked": 10},
            error_message="Some services not deployed",
        )
        assert result.checks_passed == 8
        assert result.checks_skipped == 2
        assert result.duration_seconds == 45.5


# =============================================================================
# Input Model Tests
# =============================================================================


class TestRunMoskPlatformValidationInput:
    """Tests for RunMoskPlatformValidationInput model."""

    def test_input_defaults(self) -> None:
        """Test input has correct defaults."""
        input_data = RunMoskPlatformValidationInput()
        assert input_data.level == "standard"
        assert input_data.cluster_name is None
        assert input_data.cluster_namespace == "lab"
        assert input_data.openstack_namespace == "openstack"
        assert input_data.timeout_seconds == 300

    def test_input_with_custom_values(self) -> None:
        """Test input with custom values."""
        input_data = RunMoskPlatformValidationInput(
            level="comprehensive",
            cluster_name="mos",
            cluster_namespace="production",
            openstack_namespace="custom-os",
            timeout_seconds=600,
        )
        assert input_data.level == "comprehensive"
        assert input_data.cluster_name == "mos"
        assert input_data.cluster_namespace == "production"
        assert input_data.openstack_namespace == "custom-os"
        assert input_data.timeout_seconds == 600

    def test_input_timeout_constraints(self) -> None:
        """Test timeout field constraints."""
        # Valid values
        RunMoskPlatformValidationInput(timeout_seconds=60)
        RunMoskPlatformValidationInput(timeout_seconds=900)

        # Invalid values
        with pytest.raises(ValueError):
            RunMoskPlatformValidationInput(timeout_seconds=59)
        with pytest.raises(ValueError):
            RunMoskPlatformValidationInput(timeout_seconds=901)


# =============================================================================
# Output Model Tests
# =============================================================================


class TestRunMoskPlatformValidationOutput:
    """Tests for RunMoskPlatformValidationOutput model."""

    def test_output_creation(self) -> None:
        """Test output model creation."""
        output = RunMoskPlatformValidationOutput(
            overall_status="passed",
            validation_level="standard",
            tiers_run=2,
            tiers_passed=2,
            tiers_failed=0,
            tier_results=[
                TierResultOutput(
                    tier=1,
                    name="Kubernetes Infrastructure",
                    status="passed",
                    checks_passed=5,
                    checks_failed=0,
                    duration_seconds=10.0,
                )
            ],
            duration_seconds=30.5,
            timestamp=datetime.now(UTC).isoformat(),
            cluster_name="mos",
            kubernetes_version="1.28.5",
            summary="All tiers passed",
        )
        assert output.overall_status == "passed"
        assert output.validation_level == "standard"
        assert output.tiers_run == 2
        assert output.tiers_passed == 2
        assert len(output.tier_results) == 1
        assert output.cluster_name == "mos"
        assert output.kubernetes_version == "1.28.5"
        assert output.recommendations == []


class TestTierResultOutput:
    """Tests for TierResultOutput model."""

    def test_tier_result_output_creation(self) -> None:
        """Test tier result output model creation."""
        result = TierResultOutput(
            tier=3,
            name="OpenStack Health",
            status="passed",
            checks_passed=3,
            checks_failed=0,
            duration_seconds=60.0,
            details={"osdpl_status": "healthy"},
        )
        assert result.tier == 3
        assert result.name == "OpenStack Health"
        assert result.status == "passed"
        assert result.details["osdpl_status"] == "healthy"
