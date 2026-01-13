"""Tests for the CRQ validation module.

This module tests the CRQValidator class and related functionality.
"""

from datetime import UTC, datetime

import pytest

from mosk_mcp.auth.crq import (
    CRQContext,
    CRQStatus,
    CRQValidationResult,
    CRQValidator,
    get_crq_validator,
    require_crq,
    set_crq_validator,
    validate_crq,
)
from mosk_mcp.auth.types import Permission, Role, UserContext
from mosk_mcp.core.exceptions import ValidationError


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def validator():
    """Create a CRQ validator with default pattern for format-only validation tests."""
    # allow_format_only=True for testing format-only validation behavior
    return CRQValidator(allow_format_only=True)


@pytest.fixture
def custom_validator():
    """Create a CRQ validator with custom pattern for format-only validation tests."""
    return CRQValidator(pattern=r"^CHG\d{6}$", allow_format_only=True)


@pytest.fixture
def itsm_validator():
    """Create a CRQ validator with ITSM enabled."""
    return CRQValidator(itsm_enabled=True)


@pytest.fixture
def user_context():
    """Create a test user context."""
    return UserContext(
        user_id="test-user-123",
        username="testuser",
        role=Role.ADMINISTRATOR,
        permissions=frozenset(Permission),
        authenticated_at=datetime.now(UTC),
        auth_method="test",
    )


@pytest.fixture(autouse=True)
def reset_singleton():
    """Reset singleton validator between tests."""
    set_crq_validator(CRQValidator())
    yield


# =============================================================================
# CRQValidationResult Tests
# =============================================================================


class TestCRQValidationResult:
    """Tests for CRQValidationResult dataclass."""

    def test_create_valid_result(self):
        """Test creating a valid result."""
        result = CRQValidationResult(
            crq_id="CRQ123456789",
            is_valid=True,
            status=CRQStatus.VALID,
            message="CRQ format is valid",
        )

        assert result.crq_id == "CRQ123456789"
        assert result.is_valid
        assert result.status == CRQStatus.VALID

    def test_create_invalid_result(self):
        """Test creating an invalid result."""
        result = CRQValidationResult(
            crq_id="invalid",
            is_valid=False,
            status=CRQStatus.UNKNOWN,
            message="Invalid CRQ format",
        )

        assert not result.is_valid
        assert result.status == CRQStatus.UNKNOWN

    def test_result_with_itsm_verification(self):
        """Test result with ITSM verification."""
        result = CRQValidationResult(
            crq_id="CRQ123456789",
            is_valid=True,
            status=CRQStatus.APPROVED,
            message="CRQ verified with ITSM",
            verified_with_itsm=True,
        )

        assert result.verified_with_itsm
        assert result.status == CRQStatus.APPROVED


# =============================================================================
# CRQStatus Tests
# =============================================================================


class TestCRQStatus:
    """Tests for CRQStatus enum."""

    def test_status_values(self):
        """Test status enum values."""
        assert CRQStatus.UNKNOWN.value == "unknown"
        assert CRQStatus.VALID.value == "valid"
        assert CRQStatus.APPROVED.value == "approved"
        assert CRQStatus.PENDING.value == "pending"
        assert CRQStatus.REJECTED.value == "rejected"
        assert CRQStatus.EXPIRED.value == "expired"
        assert CRQStatus.CANCELLED.value == "cancelled"


# =============================================================================
# CRQValidator Format Tests
# =============================================================================


class TestCRQValidatorFormat:
    """Tests for CRQ format validation."""

    def test_default_pattern(self, validator):
        """Test default pattern validation."""
        assert validator.pattern == r"^CRQ\d{9}$"

    def test_validate_format_valid(self, validator):
        """Test valid CRQ formats."""
        valid_crqs = [
            "CRQ123456789",
            "CRQ000000001",
            "CRQ999999999",
        ]

        for crq in valid_crqs:
            assert validator.validate_format(crq), f"Should be valid: {crq}"

    def test_validate_format_invalid(self, validator):
        """Test invalid CRQ formats."""
        invalid_crqs = [
            "",
            "CRQ",
            "CRQ12345678",  # Too short
            "CRQ1234567890",  # Too long
            "crq123456789",  # Wrong case
            "CHG123456789",  # Wrong prefix
            "CRQ12345678A",  # Contains letter
            "123456789",  # No prefix
            None,
        ]

        for crq in invalid_crqs:
            if crq is not None:
                assert not validator.validate_format(crq), f"Should be invalid: {crq}"

    def test_validate_format_empty(self, validator):
        """Test empty string is invalid."""
        assert not validator.validate_format("")

    def test_custom_pattern(self, custom_validator):
        """Test custom pattern validation."""
        assert custom_validator.validate_format("CHG123456")
        assert not custom_validator.validate_format("CRQ123456789")

    def test_validate_returns_result(self, validator):
        """Test validate returns CRQValidationResult."""
        result = validator.validate("CRQ123456789")

        assert isinstance(result, CRQValidationResult)
        assert result.is_valid
        assert result.crq_id == "CRQ123456789"

    def test_validate_invalid_returns_result(self, validator):
        """Test validate returns invalid result for bad format."""
        result = validator.validate("invalid")

        assert isinstance(result, CRQValidationResult)
        assert not result.is_valid
        assert result.status == CRQStatus.UNKNOWN


# =============================================================================
# CRQValidator Async Tests
# =============================================================================


class TestCRQValidatorAsync:
    """Tests for async CRQ validation."""

    @pytest.mark.asyncio
    async def test_validate_async_valid(self, validator):
        """Test async validation of valid CRQ."""
        result = await validator.validate_async("CRQ123456789")

        assert result.is_valid
        assert result.status == CRQStatus.VALID

    @pytest.mark.asyncio
    async def test_validate_async_invalid(self, validator):
        """Test async validation of invalid CRQ."""
        result = await validator.validate_async("invalid")

        assert not result.is_valid
        assert result.status == CRQStatus.UNKNOWN

    @pytest.mark.asyncio
    async def test_validate_async_with_itsm(self, itsm_validator):
        """Test async validation with ITSM enabled.

        Note: ITSM integration is not implemented, so verified_with_itsm=False
        even when the validator is configured with verify_with_itsm=True.
        """
        result = await itsm_validator.validate_async("CRQ123456789")

        assert result.is_valid
        # ITSM integration is not implemented, format validation only
        assert not result.verified_with_itsm


# =============================================================================
# CRQValidator Require Tests
# =============================================================================


class TestCRQValidatorRequire:
    """Tests for require_valid methods."""

    def test_require_valid_success(self, validator):
        """Test require_valid succeeds with valid CRQ."""
        crq = validator.require_valid("CRQ123456789")

        assert crq == "CRQ123456789"

    def test_require_valid_none(self, validator):
        """Test require_valid raises for None."""
        with pytest.raises(ValidationError) as exc_info:
            validator.require_valid(None)

        assert "required" in str(exc_info.value).lower()

    def test_require_valid_invalid(self, validator):
        """Test require_valid raises for invalid format."""
        with pytest.raises(ValidationError) as exc_info:
            validator.require_valid("invalid")

        assert "crq" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_require_valid_async_success(self, validator):
        """Test async require_valid succeeds with valid CRQ."""
        crq = await validator.require_valid_async("CRQ123456789")

        assert crq == "CRQ123456789"

    @pytest.mark.asyncio
    async def test_require_valid_async_none(self, validator):
        """Test async require_valid raises for None."""
        with pytest.raises(ValidationError):
            await validator.require_valid_async(None)

    @pytest.mark.asyncio
    async def test_require_valid_async_invalid(self, validator):
        """Test async require_valid raises for invalid format."""
        with pytest.raises(ValidationError):
            await validator.require_valid_async("invalid")


# =============================================================================
# CRQ Context Manager Tests
# =============================================================================


class TestCRQContext:
    """Tests for CRQ context manager."""

    @pytest.mark.asyncio
    async def test_crq_context_success(self, validator, user_context):
        """Test CRQ context manager for successful operation."""
        async with validator.crq_context(
            crq_id="CRQ123456789",
            user=user_context,
            operation="delete_machine",
        ) as ctx:
            assert ctx.crq_id == "CRQ123456789"
            assert ctx.user == user_context
            assert ctx.operation == "delete_machine"

        assert ctx.succeeded

    @pytest.mark.asyncio
    async def test_crq_context_with_resource(self, validator, user_context):
        """Test CRQ context with resource info."""
        async with validator.crq_context(
            crq_id="CRQ123456789",
            user=user_context,
            operation="delete_machine",
            resource_type="Machine",
            resource_name="compute-01",
        ) as ctx:
            assert ctx.resource_type == "Machine"
            assert ctx.resource_name == "compute-01"

    @pytest.mark.asyncio
    async def test_crq_context_failure(self, validator, user_context):
        """Test CRQ context manager for failed operation."""
        with pytest.raises(ValueError):
            async with validator.crq_context(
                crq_id="CRQ123456789",
                user=user_context,
                operation="failing_operation",
            ) as ctx:
                raise ValueError("Operation failed")

        assert not ctx.succeeded
        assert ctx.error == "Operation failed"

    @pytest.mark.asyncio
    async def test_crq_context_invalid_crq(self, validator, user_context):
        """Test CRQ context raises for invalid CRQ."""
        with pytest.raises(ValidationError):
            async with validator.crq_context(
                crq_id="invalid",
                user=user_context,
                operation="operation",
            ):
                pass

    @pytest.mark.asyncio
    async def test_crq_context_none_crq(self, validator, user_context):
        """Test CRQ context raises for None CRQ."""
        with pytest.raises(ValidationError):
            async with validator.crq_context(
                crq_id=None,
                user=user_context,
                operation="operation",
            ):
                pass


# =============================================================================
# CRQContext Dataclass Tests
# =============================================================================


class TestCRQContextDataclass:
    """Tests for CRQContext dataclass."""

    def test_context_properties(self, user_context):
        """Test context properties."""
        result = CRQValidationResult(
            crq_id="CRQ123456789",
            is_valid=True,
            status=CRQStatus.VALID,
            message="Valid",
        )

        ctx = CRQContext(
            crq_id="CRQ123456789",
            validation_result=result,
            user=user_context,
            operation="test_op",
        )

        assert ctx.crq_id == "CRQ123456789"
        assert not ctx.succeeded  # Default is False
        assert ctx.error is None

    def test_context_succeeded_state(self, user_context):
        """Test context succeeded state."""
        result = CRQValidationResult(
            crq_id="CRQ123456789",
            is_valid=True,
            status=CRQStatus.VALID,
            message="Valid",
        )

        ctx = CRQContext(
            crq_id="CRQ123456789",
            validation_result=result,
            user=user_context,
            operation="test_op",
        )

        ctx._succeeded = True
        assert ctx.succeeded


# =============================================================================
# Singleton Tests
# =============================================================================


class TestCRQValidatorSingleton:
    """Tests for CRQ validator singleton."""

    def test_get_crq_validator(self):
        """Test getting singleton validator."""
        v1 = get_crq_validator()
        v2 = get_crq_validator()

        assert v1 is v2

    def test_set_crq_validator(self):
        """Test setting singleton validator."""
        custom = CRQValidator(pattern=r"^CHG\d{6}$")
        set_crq_validator(custom)

        assert get_crq_validator() is custom


# =============================================================================
# Module Function Tests
# =============================================================================


class TestModuleFunctions:
    """Tests for module-level convenience functions."""

    @pytest.fixture(autouse=True)
    def setup_validator(self):
        """Set up a format-only validator for module function tests."""
        # Set a validator that allows format-only validation for testing
        set_crq_validator(CRQValidator(allow_format_only=True))
        yield
        # Reset after each test
        set_crq_validator(CRQValidator(allow_format_only=True))

    def test_validate_crq_valid(self):
        """Test validate_crq with valid CRQ."""
        result = validate_crq("CRQ123456789")

        assert result.is_valid

    def test_validate_crq_invalid(self):
        """Test validate_crq with invalid CRQ."""
        result = validate_crq("invalid")

        assert not result.is_valid

    def test_require_crq_valid(self):
        """Test require_crq with valid CRQ."""
        crq = require_crq("CRQ123456789")

        assert crq == "CRQ123456789"

    def test_require_crq_none(self):
        """Test require_crq with None."""
        with pytest.raises(ValidationError):
            require_crq(None)

    def test_require_crq_invalid(self):
        """Test require_crq with invalid format."""
        with pytest.raises(ValidationError):
            require_crq("invalid")


# =============================================================================
# Edge Case Tests
# =============================================================================


class TestCRQValidatorEdgeCases:
    """Tests for edge cases."""

    def test_pattern_with_special_chars(self):
        """Test pattern with special regex characters."""
        validator = CRQValidator(pattern=r"^CRQ-\d{4}-\d{4}$")

        assert validator.validate_format("CRQ-1234-5678")
        assert not validator.validate_format("CRQ12345678")

    def test_empty_pattern(self):
        """Test validator still works with permissive pattern."""
        validator = CRQValidator(pattern=r".*")

        assert validator.validate_format("anything")
        # Empty string returns False due to explicit empty check in validate_format
        assert not validator.validate_format("")

    def test_properties(self, validator, itsm_validator):
        """Test validator properties."""
        assert validator.pattern == r"^CRQ\d{9}$"
        assert not validator.itsm_enabled
        assert itsm_validator.itsm_enabled


# =============================================================================
# Integration Tests
# =============================================================================


class TestCRQValidatorIntegration:
    """Integration tests for CRQ validation."""

    @pytest.mark.asyncio
    async def test_full_workflow(self, user_context):
        """Test full CRQ validation workflow."""
        from mosk_mcp.core.config import Environment, LogFormat, Settings
        from mosk_mcp.observability.audit import AuditLogger

        # Create validator with audit logger
        # Note: environment=DEVELOPMENT allows auth_enabled=False
        # and doesn't require MCC URL.
        settings = Settings(
            audit_enabled=False,
            auth_enabled=False,
            otel_enabled=False,
            log_format=LogFormat.CONSOLE,
            environment=Environment.DEVELOPMENT,
        )
        audit = AuditLogger(settings)
        validator = CRQValidator(audit_logger=audit)

        # Validate CRQ
        result = await validator.validate_async("CRQ123456789")
        assert result.is_valid

        # Use context manager
        async with validator.crq_context(
            crq_id="CRQ123456789",
            user=user_context,
            operation="test_operation",
            resource_type="TestResource",
            resource_name="test-1",
        ) as ctx:
            # Perform operation
            pass

        assert ctx.succeeded
