"""Common utilities for MOSK MCP tools.

This package provides shared utilities to eliminate code duplication across tools:

- enums.py: Consolidated enum definitions (AlertSeverity, AlertState, HealthStatus, etc.)
- health.py: Health score conversion utilities
- kubernetes.py: Kubernetes resource utilities (age calculation, etc.)
- audit.py: Audit logging context manager
- errors.py: Error handling decorators
- models.py: Common base models (MOSKBaseModel, mixins)
- parsers.py: Kubernetes response parsing utilities

Usage:
    from mosk_mcp.tools.common import (
        # Enums
        AlertSeverity,
        AlertState,
        HealthStatus,
        # Utilities
        score_to_health,
        calculate_resource_age,
        audit_tool_execution,
        tool_handler,
        # Base models and mixins
        MOSKBaseModel,
        MOSKInputModel,
        MOSKOutputModel,
        RecommendationsMixin,
        IssuesMixin,
        HealthScoreMixin,
    )
"""

from __future__ import annotations

from mosk_mcp.tools.common.audit import audit_tool_execution
from mosk_mcp.tools.common.enums import (
    AlertSeverity,
    AlertState,
    CapacityStatus,
    HealthState,
    HealthStatus,
    OperationPhase,
    ValidationLevel,
    ValidationStatus,
)
from mosk_mcp.tools.common.errors import (
    tool_handler,
    wrap_kubernetes_error,
)
from mosk_mcp.tools.common.health import (
    CAPACITY_CRITICAL_THRESHOLD,
    CAPACITY_EMERGENCY_THRESHOLD,
    CAPACITY_WARNING_THRESHOLD,
    capacity_status,
    score_to_health,
)
from mosk_mcp.tools.common.kubernetes import (
    calculate_resource_age,
    format_age,
    format_bytes,
    parse_kubernetes_quantity,
)
from mosk_mcp.tools.common.models import (
    CheckResultMixin,
    DataclassSerializationMixin,
    HealthScoreMixin,
    IssuesMixin,
    MOSKBaseModel,
    MOSKInputModel,
    MOSKOutputModel,
    OperationResult,
    PaginationMixin,
    RecommendationsMixin,
    TierResult,
    TierResultOutput,
)
from mosk_mcp.tools.common.parsers import (
    find_condition_by_type,
    get_condition_message,
    get_status_conditions,
    is_condition_true,
    is_resource_ready,
    parse_k8s_condition,
    parse_k8s_conditions,
    parse_label_selector,
    safe_get_nested,
    utc_timestamp,
)
from mosk_mcp.tools.common.recommendations import (
    Priority,
    Recommendation,
    RecommendationBuilder,
    RecommendationPriority,
)
from mosk_mcp.tools.common.scoring import (
    ScoreCalculator,
    ScoreComponent,
    calculate_ratio_score,
    calculate_threshold_score,
)


# Sorted alphabetically per RUF022
__all__ = [
    "CAPACITY_CRITICAL_THRESHOLD",
    "CAPACITY_EMERGENCY_THRESHOLD",
    "CAPACITY_WARNING_THRESHOLD",
    "AlertSeverity",
    "AlertState",
    "CapacityStatus",
    "CheckResultMixin",
    "DataclassSerializationMixin",
    "HealthScoreMixin",
    "HealthState",
    "HealthStatus",
    "IssuesMixin",
    "MOSKBaseModel",
    "MOSKInputModel",
    "MOSKOutputModel",
    "OperationPhase",
    "OperationResult",
    "PaginationMixin",
    "Priority",
    "Recommendation",
    "RecommendationBuilder",
    "RecommendationPriority",
    "RecommendationsMixin",
    "ScoreCalculator",
    "ScoreComponent",
    "TierResult",
    "TierResultOutput",
    "ValidationLevel",
    "ValidationStatus",
    "audit_tool_execution",
    "calculate_ratio_score",
    "calculate_resource_age",
    "calculate_threshold_score",
    "capacity_status",
    "find_condition_by_type",
    "format_age",
    "format_bytes",
    "get_condition_message",
    "get_status_conditions",
    "is_condition_true",
    "is_resource_ready",
    "parse_k8s_condition",
    "parse_k8s_conditions",
    "parse_kubernetes_quantity",
    "parse_label_selector",
    "safe_get_nested",
    "score_to_health",
    "tool_handler",
    "utc_timestamp",
    "wrap_kubernetes_error",
]
