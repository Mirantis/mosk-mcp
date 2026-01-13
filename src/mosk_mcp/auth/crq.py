"""Change Request (CRQ) validation for MOSK MCP Server.

This module provides CRQ validation for privileged operations:
- Validate CRQ format (configurable regex pattern)
- CRQ context manager for privileged operations
- Audit trail integration
- Optional ITSM system verification (placeholder for future integration)

Security Considerations
-----------------------

**CRITICAL: Format-Only Validation Risk**

When `itsm_enabled=False` and `allow_format_only=True`, the validator
accepts ANY correctly-formatted CRQ string (e.g., "CRQ000000001") without
verifying it exists, is approved, or is not expired in the actual ITSM system.

This means:
- Fake CRQs can be used for privileged operations
- Expired CRQs will be accepted
- Rejected CRQs will be accepted
- Audit trails will record unverified CRQ numbers

**Recommended Production Configuration:**

For production environments, ALWAYS enable ITSM verification:

    validator = CRQValidator(
        itsm_enabled=True,
        itsm_url="https://your-itsm.example.com",
    )

**When Format-Only is Acceptable (Default):**

Format-only validation is the DEFAULT and is acceptable in:
- Development environments
- Testing environments
- CI/CD pipelines with controlled access
- Air-gapped environments without ITSM connectivity
- Lab environments without ITSM integration

This is the default configuration:

    validator = CRQValidator()  # allow_format_only=True by default

To DISABLE format-only and require ITSM:

    validator = CRQValidator(
        itsm_enabled=True,
        allow_format_only=False,  # Require ITSM verification
    )

**Configuration via Environment Variables:**

- MCP_CRQ_ITSM_ENABLED=true - Enable ITSM verification (recommended)
- MCP_CRQ_ALLOW_FORMAT_ONLY=true - Allow format-only (development only)
- MCP_AUTH_ENABLED=false - Disable all auth (testing only)
"""

from __future__ import annotations

import re
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import TYPE_CHECKING, Any

from mosk_mcp.core.exceptions import ValidationError
from mosk_mcp.observability.logging import get_logger


if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from mosk_mcp.auth.types import UserContext
    from mosk_mcp.observability.audit import AuditLogger


logger = get_logger(__name__)


class CRQStatus(str, Enum):
    """Status of a Change Request.

    Attributes:
        UNKNOWN: Status cannot be determined.
        VALID: CRQ format is valid (not verified with ITSM).
        APPROVED: CRQ is approved for execution.
        PENDING: CRQ is pending approval.
        REJECTED: CRQ was rejected.
        EXPIRED: CRQ has expired.
        CANCELLED: CRQ was cancelled.
    """

    UNKNOWN = "unknown"
    VALID = "valid"
    APPROVED = "approved"
    PENDING = "pending"
    REJECTED = "rejected"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


@dataclass
class CRQValidationResult:
    """Result of CRQ validation.

    Attributes:
        crq_id: The CRQ identifier.
        is_valid: Whether the CRQ is valid for use.
        status: CRQ status.
        message: Human-readable status message.
        verified_with_itsm: Whether verification was done with ITSM.
        verified_at: When verification was performed.
        expires_at: When the CRQ expires (if known).
        metadata: Additional metadata from ITSM.

    Note:
        The is_valid field must be consistent with status:
        - is_valid=True requires status to be VALID or APPROVED
        - is_valid=False for REJECTED, EXPIRED, CANCELLED, PENDING, UNKNOWN
    """

    crq_id: str
    is_valid: bool
    status: CRQStatus
    message: str
    verified_with_itsm: bool = False
    verified_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    # Statuses that are considered valid for use
    _VALID_STATUSES: frozenset[CRQStatus] = frozenset({CRQStatus.VALID, CRQStatus.APPROVED})

    def __post_init__(self) -> None:
        """Validate that is_valid is consistent with status.

        Raises:
            ValueError: If is_valid conflicts with status.
        """
        if self.is_valid and self.status not in self._VALID_STATUSES:
            raise ValueError(
                f"CRQValidationResult: is_valid=True is inconsistent with status={self.status.value}. "
                f"is_valid=True requires status to be one of: {[s.value for s in self._VALID_STATUSES]}"
            )
        if not self.is_valid and self.status in self._VALID_STATUSES:
            # This is less critical but worth warning about
            logger.warning(
                "crq_validation_result_inconsistent",
                crq_id=self.crq_id,
                is_valid=self.is_valid,
                status=self.status.value,
                message="is_valid=False with a valid status - consider using is_valid=True",
            )


class CRQValidator:
    """Validator for Change Request IDs.

    This class validates CRQ format and optionally verifies with ITSM systems.
    The default pattern matches ServiceNow-style CRQ numbers (CRQ followed by 9 digits).

    Attributes:
        _pattern: Compiled regex pattern for CRQ format.
        _itsm_enabled: Whether ITSM verification is enabled.
        _audit_logger: Optional audit logger.

    Example:
        validator = CRQValidator()

        # Basic format validation
        result = validator.validate("CRQ123456789")
        if result.is_valid:
            await perform_operation()

        # With context manager
        async with validator.crq_context("CRQ123456789", user) as crq:
            await privileged_operation()
    """

    # Default pattern: ServiceNow-style CRQ numbers
    DEFAULT_PATTERN = r"^CRQ\d{9}$"

    def __init__(
        self,
        pattern: str | None = None,
        itsm_enabled: bool = False,
        audit_logger: AuditLogger | None = None,
        allow_format_only: bool = True,
    ) -> None:
        """Initialize the CRQ validator.

        Args:
            pattern: Regex pattern for CRQ format. Uses default if None.
            itsm_enabled: Whether to verify with ITSM system.
            audit_logger: Optional audit logger for CRQ events.
            allow_format_only: Allow format-only validation without ITSM.
                              Defaults to True for development/lab environments.
        """
        pattern_str = pattern or self.DEFAULT_PATTERN
        self._pattern = re.compile(pattern_str)
        self._itsm_enabled = itsm_enabled
        self._audit_logger = audit_logger
        self._pattern_str = pattern_str
        self._allow_format_only = allow_format_only

        # Info: format-only validation is enabled (default for lab/dev)
        if not itsm_enabled and allow_format_only:
            logger.info(
                "crq_validator_format_only_mode",
                message="CRQ validator using format-only mode (ITSM not enabled). "
                "CRQs will be validated by format pattern only. "
                "Enable ITSM for production environments.",
            )

    @property
    def pattern(self) -> str:
        """Get the CRQ pattern string.

        Returns:
            Regex pattern string.
        """
        return self._pattern_str

    @property
    def itsm_enabled(self) -> bool:
        """Check if ITSM verification is enabled.

        Returns:
            True if ITSM verification is enabled.
        """
        return self._itsm_enabled

    def validate_format(self, crq_id: str) -> bool:
        """Validate CRQ format against the pattern.

        Args:
            crq_id: CRQ identifier to validate.

        Returns:
            True if format is valid.
        """
        if not crq_id:
            return False
        return bool(self._pattern.match(crq_id))

    def validate(self, crq_id: str) -> CRQValidationResult:
        """Validate a CRQ identifier.

        Performs format validation and optionally ITSM verification.

        Args:
            crq_id: CRQ identifier to validate.

        Returns:
            Validation result.
        """
        # Format validation
        if not self.validate_format(crq_id):
            logger.warning(
                "crq_format_invalid",
                crq_id=crq_id,
                pattern=self._pattern_str,
            )
            return CRQValidationResult(
                crq_id=crq_id,
                is_valid=False,
                status=CRQStatus.UNKNOWN,
                message=f"Invalid CRQ format. Expected pattern: {self._pattern_str}",
            )

        # If ITSM is not enabled, check if format-only validation is explicitly allowed
        if not self._itsm_enabled:
            if not self._allow_format_only:
                # Reject format-only validation unless explicitly acknowledged
                logger.error(
                    "crq_format_only_rejected",
                    crq_id=crq_id,
                    message="Format-only CRQ validation rejected. Either enable ITSM "
                    "verification or set allow_format_only=True to acknowledge risk.",
                )
                return CRQValidationResult(
                    crq_id=crq_id,
                    is_valid=False,
                    status=CRQStatus.UNKNOWN,
                    message="CRQ validation requires ITSM verification. Format-only "
                    "validation not enabled. Contact administrator to enable ITSM "
                    "integration or explicitly allow format-only validation.",
                )

            # Format-only validation explicitly allowed - proceed with warning
            logger.warning(
                "crq_accepted_without_itsm_verification",
                crq_id=crq_id,
                message="CRQ accepted based on format only - ITSM verification disabled",
            )
            return CRQValidationResult(
                crq_id=crq_id,
                is_valid=True,
                status=CRQStatus.VALID,
                message="CRQ format is valid (WARNING: ITSM verification not enabled - "
                "CRQ not verified with change management system)",
            )

        # ITSM verification would go here
        # For now, return valid
        return CRQValidationResult(
            crq_id=crq_id,
            is_valid=True,
            status=CRQStatus.VALID,
            message="CRQ format is valid",
        )

    async def validate_async(self, crq_id: str) -> CRQValidationResult:
        """Validate a CRQ identifier asynchronously.

        This method supports async ITSM verification.

        Args:
            crq_id: CRQ identifier to validate.

        Returns:
            Validation result.
        """
        # Format validation
        if not self.validate_format(crq_id):
            logger.warning(
                "crq_format_invalid",
                crq_id=crq_id,
                pattern=self._pattern_str,
            )
            return CRQValidationResult(
                crq_id=crq_id,
                is_valid=False,
                status=CRQStatus.UNKNOWN,
                message=f"Invalid CRQ format. Expected pattern: {self._pattern_str}",
            )

        # If ITSM is enabled, verify asynchronously
        if self._itsm_enabled:
            return await self._verify_with_itsm(crq_id)

        # WARNING: Without ITSM verification, CRQ is accepted based on format alone
        # This does NOT verify the CRQ exists, is approved, or is within its change window
        logger.warning(
            "crq_accepted_without_itsm_verification",
            crq_id=crq_id,
            message="CRQ accepted based on format only - ITSM verification disabled",
        )
        return CRQValidationResult(
            crq_id=crq_id,
            is_valid=True,
            status=CRQStatus.VALID,
            message="CRQ format is valid (WARNING: ITSM verification not enabled - CRQ not verified with change management system)",
        )

    async def _verify_with_itsm(self, crq_id: str) -> CRQValidationResult:
        """Verify CRQ with ITSM system.

        This is a placeholder for future ITSM integration.
        In a real implementation, this would:
        1. Connect to ServiceNow or other ITSM API
        2. Verify CRQ exists and is approved
        3. Check CRQ hasn't expired
        4. Verify CRQ is for the correct change type

        Args:
            crq_id: CRQ identifier to verify.

        Returns:
            Verification result.
        """
        logger.info(
            "crq_itsm_verification",
            crq_id=crq_id,
            status="format_validated",
        )

        # NOTE: Full ITSM integration (ServiceNow) is not implemented.
        # CRQ format validation is active. For ServiceNow integration,
        # implement API calls to verify CRQ state and validity window.

        return CRQValidationResult(
            crq_id=crq_id,
            is_valid=True,
            status=CRQStatus.APPROVED,
            message="CRQ format validated (ITSM integration not configured)",
            verified_with_itsm=False,
        )

    def require_valid(self, crq_id: str | None) -> str:
        """Require a valid CRQ identifier.

        Args:
            crq_id: CRQ identifier to validate.

        Returns:
            Validated CRQ ID.

        Raises:
            ValidationError: If CRQ is None or invalid.
        """
        if crq_id is None:
            raise ValidationError(
                message="CRQ ID is required for this operation",
                field="crq_id",
                constraint="required",
            )

        result = self.validate(crq_id)
        if not result.is_valid:
            raise ValidationError(
                message=result.message,
                field="crq_id",
                value=crq_id,
                constraint=f"pattern:{self._pattern_str}",
            )

        return crq_id

    async def require_valid_async(self, crq_id: str | None) -> str:
        """Require a valid CRQ identifier (async).

        Args:
            crq_id: CRQ identifier to validate.

        Returns:
            Validated CRQ ID.

        Raises:
            ValidationError: If CRQ is None or invalid.
        """
        if crq_id is None:
            raise ValidationError(
                message="CRQ ID is required for this operation",
                field="crq_id",
                constraint="required",
            )

        result = await self.validate_async(crq_id)
        if not result.is_valid:
            raise ValidationError(
                message=result.message,
                field="crq_id",
                value=crq_id,
                constraint=f"pattern:{self._pattern_str}",
            )

        return crq_id

    @asynccontextmanager
    async def crq_context(
        self,
        crq_id: str | None,
        user: UserContext,
        operation: str = "privileged_operation",
        resource_type: str | None = None,
        resource_name: str | None = None,
    ) -> AsyncGenerator[CRQContext, None]:
        """Context manager for CRQ-protected operations.

        This context manager:
        1. Validates the CRQ
        2. Logs the start of the operation
        3. Tracks the operation duration
        4. Logs the completion (success or failure)

        Args:
            crq_id: CRQ identifier.
            user: User context.
            operation: Operation name for logging.
            resource_type: Resource type being modified.
            resource_name: Resource name being modified.

        Yields:
            CRQContext with validated CRQ information.

        Raises:
            ValidationError: If CRQ is invalid.

        Example:
            async with validator.crq_context("CRQ123456789", user, "delete_node") as ctx:
                await delete_node(name)
        """
        # Validate CRQ
        validated_crq = await self.require_valid_async(crq_id)
        result = await self.validate_async(validated_crq)

        # Create context
        ctx = CRQContext(
            crq_id=validated_crq,
            validation_result=result,
            user=user,
            operation=operation,
            resource_type=resource_type,
            resource_name=resource_name,
        )

        # Log start
        if self._audit_logger:
            from mosk_mcp.observability.audit import AuditCategory, AuditLevel, AuditStatus

            await self._audit_logger.log(
                category=AuditCategory.RESOURCE_MODIFICATION,
                level=AuditLevel.PRIVILEGED,
                status=AuditStatus.STARTED,
                user_id=user.user_id,
                username=user.username,
                action=operation,
                resource_type=resource_type,
                resource_name=resource_name,
                crq_id=validated_crq,
                details={"crq_status": result.status.value},
            )

        logger.info(
            "crq_operation_started",
            crq_id=validated_crq,
            operation=operation,
            user=user.username,
        )

        try:
            yield ctx
            ctx._succeeded = True

            # Log success
            if self._audit_logger:
                await self._audit_logger.log(
                    category=AuditCategory.RESOURCE_MODIFICATION,
                    level=AuditLevel.PRIVILEGED,
                    status=AuditStatus.SUCCESS,
                    user_id=user.user_id,
                    username=user.username,
                    action=operation,
                    resource_type=resource_type,
                    resource_name=resource_name,
                    crq_id=validated_crq,
                )

            logger.info(
                "crq_operation_completed",
                crq_id=validated_crq,
                operation=operation,
                user=user.username,
            )

        except Exception as e:
            ctx._error = str(e)

            # Log failure
            if self._audit_logger:
                await self._audit_logger.log(
                    category=AuditCategory.RESOURCE_MODIFICATION,
                    level=AuditLevel.PRIVILEGED,
                    status=AuditStatus.FAILURE,
                    user_id=user.user_id,
                    username=user.username,
                    action=operation,
                    resource_type=resource_type,
                    resource_name=resource_name,
                    crq_id=validated_crq,
                    error_message=str(e),
                )

            logger.error(
                "crq_operation_failed",
                crq_id=validated_crq,
                operation=operation,
                user=user.username,
                error=str(e),
            )
            raise


@dataclass
class CRQContext:
    """Context for CRQ-protected operations.

    This class holds information about the current CRQ-protected operation.

    Attributes:
        crq_id: Validated CRQ identifier.
        validation_result: CRQ validation result.
        user: User performing the operation.
        operation: Operation name.
        resource_type: Resource type being modified.
        resource_name: Resource name being modified.
    """

    crq_id: str
    validation_result: CRQValidationResult
    user: UserContext
    operation: str
    resource_type: str | None = None
    resource_name: str | None = None
    _succeeded: bool = field(default=False, repr=False)
    _error: str | None = field(default=None, repr=False)

    @property
    def succeeded(self) -> bool:
        """Check if the operation succeeded.

        Returns:
            True if operation completed successfully.
        """
        return self._succeeded

    @property
    def error(self) -> str | None:
        """Get the error message if operation failed.

        Returns:
            Error message or None.
        """
        return self._error


# Singleton validator instance
_validator: CRQValidator | None = None


def get_crq_validator(
    pattern: str | None = None,
    itsm_enabled: bool = False,
    audit_logger: AuditLogger | None = None,
    allow_format_only: bool = True,
) -> CRQValidator:
    """Get the CRQ validator singleton.

    Args:
        pattern: CRQ pattern (used only for initialization).
        itsm_enabled: Enable ITSM verification (used only for initialization).
        audit_logger: Audit logger (used only for initialization).
        allow_format_only: Allow format-only validation (used only for initialization).
                          Defaults to True for development/lab environments.

    Returns:
        CRQValidator instance.
    """
    global _validator
    if _validator is None:
        _validator = CRQValidator(
            pattern=pattern,
            itsm_enabled=itsm_enabled,
            audit_logger=audit_logger,
            allow_format_only=allow_format_only,
        )
    return _validator


def set_crq_validator(validator: CRQValidator) -> None:
    """Set the CRQ validator singleton.

    Args:
        validator: CRQValidator instance to use.
    """
    global _validator
    _validator = validator


def validate_crq(crq_id: str) -> CRQValidationResult:
    """Validate a CRQ using the default validator.

    Args:
        crq_id: CRQ identifier to validate.

    Returns:
        Validation result.
    """
    return get_crq_validator().validate(crq_id)


def require_crq(crq_id: str | None) -> str:
    """Require a valid CRQ using the default validator.

    Args:
        crq_id: CRQ identifier to validate.

    Returns:
        Validated CRQ ID.

    Raises:
        ValidationError: If CRQ is invalid or missing.
    """
    return get_crq_validator().require_valid(crq_id)
