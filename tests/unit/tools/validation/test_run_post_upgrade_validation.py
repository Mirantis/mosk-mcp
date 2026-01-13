"""Tests for run_post_upgrade_validation tool.

Tests cover:
- ValidationLevel enum
- ValidationStatus enum
- TierResult dataclass
- Input/output models
"""

from datetime import UTC, datetime

import pytest

from mosk_mcp.tools.validation.run_post_upgrade_validation import (
    RunPostUpgradeValidationInput,
    RunPostUpgradeValidationOutput,
    TierResult,
    TierResultOutput,
    ValidationLevel,
    ValidationStatus,
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
            name="Infrastructure Health",
            status=ValidationStatus.PASSED,
        )
        assert result.tier == 1
        assert result.name == "Infrastructure Health"
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
            name="Service Availability",
            status=ValidationStatus.PASSED_WITH_WARNINGS,
            checks_passed=5,
            checks_failed=1,
            checks_skipped=0,
            duration_seconds=30.5,
            details={"services_checked": 6},
            error_message="One service degraded",
        )
        assert result.checks_passed == 5
        assert result.checks_failed == 1
        assert result.duration_seconds == 30.5


# =============================================================================
# Input Model Tests
# =============================================================================


class TestRunPostUpgradeValidationInput:
    """Tests for RunPostUpgradeValidationInput model."""

    def test_input_defaults(self) -> None:
        """Test input has correct defaults."""
        input_data = RunPostUpgradeValidationInput()
        assert input_data.level == "standard"
        assert input_data.osdpl_name is None
        assert input_data.namespace == "openstack"
        assert input_data.include_smoke_tests is None
        assert input_data.cleanup_smoke_tests is True
        assert input_data.timeout_seconds == 600

    def test_input_with_custom_values(self) -> None:
        """Test input with custom values."""
        input_data = RunPostUpgradeValidationInput(
            level="comprehensive",
            osdpl_name="mos",
            namespace="custom-ns",
            include_smoke_tests=["vm_lifecycle"],
            cleanup_smoke_tests=False,
            timeout_seconds=900,
        )
        assert input_data.level == "comprehensive"
        assert input_data.osdpl_name == "mos"
        assert input_data.namespace == "custom-ns"
        assert input_data.include_smoke_tests == ["vm_lifecycle"]
        assert input_data.cleanup_smoke_tests is False
        assert input_data.timeout_seconds == 900

    def test_input_timeout_constraints(self) -> None:
        """Test timeout field constraints."""
        # Valid values
        RunPostUpgradeValidationInput(timeout_seconds=60)
        RunPostUpgradeValidationInput(timeout_seconds=1800)

        # Invalid values
        with pytest.raises(ValueError):
            RunPostUpgradeValidationInput(timeout_seconds=59)
        with pytest.raises(ValueError):
            RunPostUpgradeValidationInput(timeout_seconds=1801)


# =============================================================================
# Output Model Tests
# =============================================================================


class TestRunPostUpgradeValidationOutput:
    """Tests for RunPostUpgradeValidationOutput model."""

    def test_output_creation(self) -> None:
        """Test output model creation."""
        output = RunPostUpgradeValidationOutput(
            overall_status="passed",
            validation_level="standard",
            tiers_run=2,
            tiers_passed=2,
            tiers_failed=0,
            tier_results=[
                TierResultOutput(
                    tier=1,
                    name="Infrastructure Health",
                    status="passed",
                    checks_passed=5,
                    checks_failed=0,
                    duration_seconds=10.0,
                )
            ],
            duration_seconds=30.5,
            timestamp=datetime.now(UTC).isoformat(),
            summary="All tiers passed",
        )
        assert output.overall_status == "passed"
        assert output.validation_level == "standard"
        assert output.tiers_run == 2
        assert output.tiers_passed == 2
        assert len(output.tier_results) == 1
        assert output.recommendations == []
        assert output.summary == "All tiers passed"


class TestTierResultOutput:
    """Tests for TierResultOutput model."""

    def test_tier_result_output_creation(self) -> None:
        """Test tier result output model creation."""
        result = TierResultOutput(
            tier=3,
            name="Smoke Tests",
            status="passed",
            checks_passed=3,
            checks_failed=0,
            duration_seconds=120.0,
            details={"tests_run": ["vm_lifecycle"]},
        )
        assert result.tier == 3
        assert result.name == "Smoke Tests"
        assert result.status == "passed"
        assert result.details["tests_run"] == ["vm_lifecycle"]
