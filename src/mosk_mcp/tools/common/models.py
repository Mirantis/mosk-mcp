"""Common base models for MOSK MCP tools.

This module provides base model classes to eliminate duplication of common
Pydantic configuration patterns across tool modules.

Usage:
    from mosk_mcp.tools.common.models import MOSKBaseModel, TimestampMixin

    class MyToolOutput(MOSKBaseModel, TimestampMixin):
        result: str = Field(..., description="The result")
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields, is_dataclass
from typing import Annotated, Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from mosk_mcp.tools.common.enums import ValidationStatus
from mosk_mcp.tools.common.parsers import utc_timestamp


# Generic type for operation results
T = TypeVar("T")


class MOSKBaseModel(BaseModel):
    """Base model for all MOSK MCP tool input/output models.

    Provides common configuration:
    - populate_by_name=True: Allow both alias and field name
    - Extra fields are forbidden by default

    This eliminates the need to repeat `model_config = ConfigDict(populate_by_name=True)`
    in every model class (previously repeated 191 times across the codebase).
    """

    model_config = ConfigDict(populate_by_name=True)


class MOSKInputModel(MOSKBaseModel):
    """Base model for tool input parameters.

    Inherits from MOSKBaseModel with no additional constraints.
    Use this for tool input parameters.
    """

    pass


class MOSKOutputModel(MOSKBaseModel):
    """Base model for tool output responses.

    Inherits from MOSKBaseModel with timestamp support.
    All output models should include a timestamp field.
    """

    timestamp: str = Field(
        default_factory=utc_timestamp,
        description="Query timestamp in ISO 8601 format",
    )


class RecommendationsMixin(BaseModel):
    """Mixin for models that include recommendations.

    Provides a standard recommendations field for tool outputs
    that suggest actions to the user.
    """

    recommendations: list[str] = Field(
        default_factory=list,
        description="Actionable recommendations based on the analysis",
    )


class IssuesMixin(BaseModel):
    """Mixin for models that track issues and warnings.

    Provides standard fields for issues and warnings that
    tools may report during analysis.
    """

    issues: list[str] = Field(
        default_factory=list,
        description="Current issues detected",
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="Warnings that may need attention",
    )


class HealthScoreMixin(BaseModel):
    """Mixin for models that include health scoring.

    Provides standard health score field with proper bounds.
    """

    health_score: int = Field(
        default=0,
        ge=0,
        le=100,
        description="Health score from 0-100 (100 = fully healthy)",
    )


class PaginationMixin(BaseModel):
    """Mixin for models that support pagination.

    Provides standard pagination fields for large result sets.
    """

    has_more: bool = Field(
        default=False,
        description="Whether more results are available",
    )
    cursor: str | None = Field(
        default=None,
        description="Cursor for fetching next page of results",
    )
    total_count: int | None = Field(
        default=None,
        description="Total count of items (if known)",
    )


class CheckResultMixin(BaseModel):
    """Mixin for models that report check results.

    Provides standard fields for tracking passed/failed/skipped checks.
    """

    checks_passed: int = Field(default=0, description="Number of checks that passed")
    checks_failed: int = Field(default=0, description="Number of checks that failed")
    checks_skipped: int = Field(default=0, description="Number of checks skipped")

    @property
    def total_checks(self) -> int:
        """Total number of checks performed."""
        return self.checks_passed + self.checks_failed + self.checks_skipped


# Type aliases for common field patterns
ProgressPercent = Annotated[int, Field(ge=0, le=100, description="Progress percentage (0-100)")]


# =============================================================================
# Validation Tier Models
# =============================================================================


@dataclass
class TierResult:
    """Result of a validation tier.

    This dataclass consolidates TierResult and PlatformTierResult from
    run_post_upgrade_validation and run_mosk_platform_validation.

    Attributes:
        tier: Tier number (1, 2, or 3)
        name: Human-readable tier name
        status: Validation status (passed, failed, error, passed_with_warnings)
        checks_passed: Number of checks that passed
        checks_failed: Number of checks that failed
        checks_skipped: Number of checks skipped
        duration_seconds: Time taken for this tier
        details: Additional tier-specific details
        error_message: Error message if tier failed with error
    """

    tier: int
    name: str
    status: ValidationStatus
    checks_passed: int = 0
    checks_failed: int = 0
    checks_skipped: int = 0
    duration_seconds: float = 0.0
    details: dict[str, Any] = field(default_factory=dict)
    error_message: str | None = None


class TierResultOutput(MOSKBaseModel):
    """Pydantic output model for a single tier result.

    Used in API responses for validation tool outputs.
    """

    tier: int = Field(description="Tier number (1, 2, or 3)")
    name: str = Field(description="Tier name")
    status: str = Field(description="Tier status (passed, failed, error)")
    checks_passed: int = Field(default=0, description="Number of checks passed")
    checks_failed: int = Field(default=0, description="Number of checks failed")
    checks_skipped: int = Field(default=0, description="Number of checks skipped")
    duration_seconds: float = Field(default=0.0, description="Tier duration in seconds")
    details: dict[str, Any] = Field(default_factory=dict, description="Tier details")
    error_message: str | None = Field(default=None, description="Error if tier failed")

    @classmethod
    def from_tier_result(cls, result: TierResult) -> TierResultOutput:
        """Create output from a TierResult dataclass."""
        return cls(
            tier=result.tier,
            name=result.name,
            status=result.status.value,
            checks_passed=result.checks_passed,
            checks_failed=result.checks_failed,
            checks_skipped=result.checks_skipped,
            duration_seconds=result.duration_seconds,
            details=result.details,
            error_message=result.error_message,
        )


# =============================================================================
# Generic Operation Result for Functions That May Fail
# =============================================================================


@dataclass
class OperationResult(Generic[T]):
    """Generic result type for operations that may fail.

    Use this instead of returning None on error to distinguish between
    "no data" and "operation failed".

    This pattern replaces the common anti-pattern:
        def get_something() -> T | None:
            try:
                return data
            except:
                return None  # Can't tell if data is missing or error occurred

    With the safer pattern:
        def get_something() -> "OperationResult"[T]:
            try:
                return OperationResult.success(data)
            except Exception as e:
                return OperationResult.failure(str(e))

    Attributes:
        success: Whether the operation succeeded.
        value: The result value (only valid if success=True).
        error: Error message (only valid if success=False).
        error_type: Type of error that occurred.
        details: Additional error context.
    """

    success: bool
    value: T | None = None
    error: str | None = None
    error_type: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Validate success/value and failure/error invariants."""
        if self.success and self.value is None:
            raise ValueError("OperationResult: success=True requires a non-None value")
        if not self.success and self.error is None:
            raise ValueError("OperationResult: success=False requires an error message")

    @classmethod
    def ok(cls, value: T) -> OperationResult[T]:
        """Create a successful result with a value."""
        return cls(success=True, value=value)

    @classmethod
    def failure(
        cls,
        error: str,
        error_type: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> OperationResult[T]:
        """Create a failed result with an error message."""
        return cls(
            success=False,
            error=error,
            error_type=error_type,
            details=details or {},
        )

    @classmethod
    def from_exception(cls, exc: Exception, context: str | None = None) -> OperationResult[T]:
        """Create a failed result from an exception."""
        error_msg = str(exc)
        if context:
            error_msg = f"{context}: {error_msg}"
        return cls(
            success=False,
            error=error_msg,
            error_type=type(exc).__name__,
        )

    def unwrap(self) -> T:
        """Get the value or raise an error if operation failed.

        Returns:
            The result value.

        Raises:
            ValueError: If the operation failed.
        """
        if not self.success:
            raise ValueError(f"Operation failed: {self.error}")
        if self.value is None:
            raise ValueError("Operation succeeded but value is None")
        return self.value

    def unwrap_or(self, default: T) -> T:
        """Get the value or return a default if operation failed."""
        if self.success and self.value is not None:
            return self.value
        return default


# =============================================================================
# Dataclass Serialization Mixin
# =============================================================================


class DataclassSerializationMixin:
    """Mixin providing to_dict() method for dataclasses.

    This eliminates the need to manually implement to_dict() on each
    dataclass (previously duplicated ~20+ times across the codebase).

    Usage:
        @dataclass
        class MyData(DataclassSerializationMixin):
            name: str
            value: int

        data = MyData(name="test", value=42)
        data.to_dict()  # {"name": "test", "value": 42}

    Note: For Pydantic models, use model_dump() instead.
    """

    def to_dict(self) -> dict[str, Any]:
        """Convert dataclass to dictionary.

        Handles nested dataclasses, lists, and tuples recursively.
        Filters out None values by default.

        Returns:
            Dictionary representation of the dataclass.
        """
        if not is_dataclass(self):
            raise TypeError(f"{type(self).__name__} is not a dataclass")

        def _convert(obj: Any) -> Any:
            if is_dataclass(obj) and not isinstance(obj, type):
                return {
                    f.name: _convert(getattr(obj, f.name))
                    for f in fields(obj)
                    if getattr(obj, f.name) is not None
                }
            elif isinstance(obj, (list, tuple)):
                return [_convert(item) for item in obj]
            elif isinstance(obj, dict):
                return {k: _convert(v) for k, v in obj.items() if v is not None}
            return obj

        return _convert(self)

    def to_dict_full(self) -> dict[str, Any]:
        """Convert dataclass to dictionary including None values.

        Uses dataclasses.asdict() for complete serialization.

        Returns:
            Complete dictionary representation including None values.
        """
        if not is_dataclass(self):
            raise TypeError(f"{type(self).__name__} is not a dataclass")
        return asdict(self)


__all__ = [
    "CheckResultMixin",
    "DataclassSerializationMixin",
    "HealthScoreMixin",
    "IssuesMixin",
    "MOSKBaseModel",
    "MOSKInputModel",
    "MOSKOutputModel",
    "OperationResult",
    "PaginationMixin",
    "ProgressPercent",
    "RecommendationsMixin",
    "TierResult",
    "TierResultOutput",
]
