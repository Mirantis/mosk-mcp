"""Common enumerations for MOSK MCP tools.

This module provides consolidated enum definitions used across multiple
tool modules, eliminating duplication and ensuring consistency.

Usage:
    from mosk_mcp.tools.common.enums import AlertSeverity, AlertState, HealthStatus

The severity and health enums support comparison operations for severity ordering.
"""

from __future__ import annotations

from enum import Enum
from types import MappingProxyType
from typing import Any


# Severity orderings for each enum type (module-level to avoid Enum member issues)
# Higher numbers = more severe
_ALERT_SEVERITY_ORDER: MappingProxyType[str, int] = MappingProxyType(
    {
        "none": 0,
        "info": 1,
        "warning": 2,
        "critical": 3,
        "page": 4,
    }
)

_HEALTH_STATE_ORDER: MappingProxyType[str, int] = MappingProxyType(
    {
        "unknown": 0,
        "healthy": 1,
        "degraded": 2,
        "warning": 3,
        "critical": 4,
    }
)

_CAPACITY_STATUS_ORDER: MappingProxyType[str, int] = MappingProxyType(
    {
        "normal": 0,
        "warning": 1,
        "critical": 2,
        "emergency": 3,
    }
)


def _make_ordered_enum_methods(
    order_map: MappingProxyType[str, int],
) -> dict[str, Any]:
    """Create comparison methods for an ordered enum.

    This factory function creates the comparison methods (__lt__, __le__, etc.)
    for an ordered enum, avoiding issues with enum metaclass treating class
    variables as enum members.

    Args:
        order_map: Mapping from enum values to their ordering integers.

    Returns:
        Dictionary of method names to method implementations.
    """

    def _get_order(self: Any) -> int:
        return order_map[self.value]

    def __lt__(self: Any, other: Any) -> bool:
        if not isinstance(other, self.__class__):
            return NotImplemented
        return order_map[self.value] < order_map[other.value]

    def __le__(self: Any, other: Any) -> bool:
        if not isinstance(other, self.__class__):
            return NotImplemented
        return order_map[self.value] <= order_map[other.value]

    def __gt__(self: Any, other: Any) -> bool:
        if not isinstance(other, self.__class__):
            return NotImplemented
        return order_map[self.value] > order_map[other.value]

    def __ge__(self: Any, other: Any) -> bool:
        if not isinstance(other, self.__class__):
            return NotImplemented
        return order_map[self.value] >= order_map[other.value]

    def is_at_least(self: Any, threshold: Any) -> bool:
        """Check if this severity is at least as severe as the threshold."""
        return self >= threshold

    def is_more_severe_than(self: Any, other: Any) -> bool:
        """Check if this severity is more severe than the other."""
        return self > other

    return {
        "_get_order": _get_order,
        "__lt__": __lt__,
        "__le__": __le__,
        "__gt__": __gt__,
        "__ge__": __ge__,
        "is_at_least": is_at_least,
        "is_more_severe_than": is_more_severe_than,
    }


# Pre-generate methods for each ordered enum type
_ALERT_SEVERITY_METHODS = _make_ordered_enum_methods(_ALERT_SEVERITY_ORDER)
_HEALTH_STATE_METHODS = _make_ordered_enum_methods(_HEALTH_STATE_ORDER)
_CAPACITY_STATUS_METHODS = _make_ordered_enum_methods(_CAPACITY_STATUS_ORDER)


class AlertSeverity(str, Enum):
    """StackLight/Prometheus alert severity levels.

    Unified enum combining severity values from cluster_health and troubleshooting.
    Supports comparison: PAGE > CRITICAL > WARNING > INFO > NONE

    Example:
        >>> AlertSeverity.CRITICAL > AlertSeverity.WARNING
        True
        >>> AlertSeverity.INFO.is_at_least(AlertSeverity.WARNING)
        False
    """

    CRITICAL = "critical"
    WARNING = "warning"
    INFO = "info"
    NONE = "none"  # Used for filtering (show all)
    PAGE = "page"  # Highest severity - requires immediate attention

    # Comparison methods added from factory
    __lt__ = _ALERT_SEVERITY_METHODS["__lt__"]
    __le__ = _ALERT_SEVERITY_METHODS["__le__"]
    __gt__ = _ALERT_SEVERITY_METHODS["__gt__"]
    __ge__ = _ALERT_SEVERITY_METHODS["__ge__"]
    is_at_least = _ALERT_SEVERITY_METHODS["is_at_least"]
    is_more_severe_than = _ALERT_SEVERITY_METHODS["is_more_severe_than"]


class AlertState(str, Enum):
    """Alert state values.

    Represents the current state of an alert in the alerting system.
    """

    FIRING = "firing"  # Alert is currently active
    PENDING = "pending"  # Alert condition met, waiting for duration
    RESOLVED = "resolved"  # Alert was firing but is now resolved


class HealthStatus(str, Enum):
    """Component health status.

    Used for individual component health representation.
    Previously duplicated as ComponentHealth in cluster_health
    and HealthStatus in operations_visibility.
    """

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


class HealthState(str, Enum):
    """Overall health state based on score thresholds.

    Used for cluster-wide health assessment with numeric scoring.
    Different from HealthStatus as it includes WARNING and CRITICAL
    levels for threshold-based evaluation.

    Supports comparison: CRITICAL > WARNING > DEGRADED > HEALTHY
    (UNKNOWN has severity 0, treated as least severe for comparisons)

    Example:
        >>> HealthState.CRITICAL > HealthState.WARNING
        True
        >>> HealthState.DEGRADED.is_at_least(HealthState.WARNING)
        False
    """

    HEALTHY = "healthy"  # Score >= 90
    DEGRADED = "degraded"  # Score >= 70
    WARNING = "warning"  # Score >= 50
    CRITICAL = "critical"  # Score < 50
    UNKNOWN = "unknown"  # Cannot determine

    # Comparison methods added from factory
    __lt__ = _HEALTH_STATE_METHODS["__lt__"]
    __le__ = _HEALTH_STATE_METHODS["__le__"]
    __gt__ = _HEALTH_STATE_METHODS["__gt__"]
    __ge__ = _HEALTH_STATE_METHODS["__ge__"]
    is_at_least = _HEALTH_STATE_METHODS["is_at_least"]
    is_more_severe_than = _HEALTH_STATE_METHODS["is_more_severe_than"]


class CapacityStatus(str, Enum):
    """Storage capacity status levels.

    Used for Ceph and storage capacity monitoring.
    Supports comparison: EMERGENCY > CRITICAL > WARNING > NORMAL

    Example:
        >>> CapacityStatus.CRITICAL > CapacityStatus.WARNING
        True
        >>> CapacityStatus.NORMAL.is_at_least(CapacityStatus.WARNING)
        False
    """

    NORMAL = "normal"  # Usage < 70%
    WARNING = "warning"  # Usage 70-85%
    CRITICAL = "critical"  # Usage 85-95%
    EMERGENCY = "emergency"  # Usage >= 95%

    # Comparison methods added from factory
    __lt__ = _CAPACITY_STATUS_METHODS["__lt__"]
    __le__ = _CAPACITY_STATUS_METHODS["__le__"]
    __gt__ = _CAPACITY_STATUS_METHODS["__gt__"]
    __ge__ = _CAPACITY_STATUS_METHODS["__ge__"]
    is_at_least = _CAPACITY_STATUS_METHODS["is_at_least"]
    is_more_severe_than = _CAPACITY_STATUS_METHODS["is_more_severe_than"]


class OperationPhase(str, Enum):
    """Generic operation phase tracking.

    Used for tracking long-running operations like upgrades,
    maintenance, and provisioning.
    """

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ValidationStatus(str, Enum):
    """Validation result status.

    Used for validation tier results and overall validation outcomes.
    Consolidates ValidationStatus and PlatformValidationStatus from
    run_post_upgrade_validation and run_mosk_platform_validation.
    """

    PASSED = "passed"
    PASSED_WITH_WARNINGS = "passed_with_warnings"
    FAILED = "failed"
    ERROR = "error"


class ValidationLevel(str, Enum):
    """Validation depth levels.

    Used to control how thorough a validation run should be.
    """

    QUICK = "quick"  # Basic checks only
    STANDARD = "standard"  # Standard depth
    COMPREHENSIVE = "comprehensive"  # Full validation with all checks


class LogSeverity(str, Enum):
    """Log severity levels for OpenSearch/StackLight logs.

    Consolidated from troubleshooting/models.py and adapters/stacklight/core.py.
    """

    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"
    UNKNOWN = "unknown"  # For parsing failures


class MigrationStatus(str, Enum):
    """Nova/OpenStack VM migration status.

    Consolidated from node_lifecycle, operations_visibility, and adapters/openstack.
    """

    QUEUED = "queued"
    PREPARING = "preparing"
    RUNNING = "running"
    POST_MIGRATING = "post-migrating"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    ERROR = "error"
    ACCEPTED = "accepted"  # From Nova API
    PRE_MIGRATING = "pre-migrating"  # From Nova API
    UNKNOWN = "unknown"  # For parsing failures


class CephHealthStatus(str, Enum):
    """Ceph cluster health status.

    Consolidated from adapters/ceph.py and adapters/crd/miraceph.py.
    Values match Ceph's native health status strings.
    """

    HEALTH_OK = "HEALTH_OK"
    HEALTH_WARN = "HEALTH_WARN"
    HEALTH_ERR = "HEALTH_ERR"
    UNKNOWN = "UNKNOWN"


class DeviceFlowStatus(str, Enum):
    """Status of OAuth 2.0 device flow authentication.

    Consolidated from tools/auth/models.py and auth/device_flow.py.
    """

    PENDING = "pending"  # Waiting for user to start
    AWAITING_USER = "awaiting_user"  # User needs to visit URL
    POLLING = "polling"  # Actively polling for token
    COMPLETED = "completed"  # Authentication successful
    EXPIRED = "expired"  # Device code expired
    DENIED = "denied"  # User denied access
    ERROR = "error"  # Authentication error


# Legacy alias removed - use HealthStatus directly
