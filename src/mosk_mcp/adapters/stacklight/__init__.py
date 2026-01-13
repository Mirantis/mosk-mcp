"""StackLight adapter subpackage for Prometheus, Alertmanager, and OpenSearch.

This subpackage provides monitoring and logging integration:

- core.py: Main StackLightAdapter, DirectStackLightClient, and data models
- manager.py: Multi-cluster StackLight orchestration (MCC + MOSK)
- response_models.py: Pydantic models for query results
"""

from __future__ import annotations

from mosk_mcp.adapters.stacklight.core import (
    Alert,
    AlertSeverity,
    AlertState,
    DirectStackLightClient,
    LogEntry,
    LogQueryResult,
    LogSeverity,
    MetricSample,
    NaturalLanguageQueryParser,
    StackLightAdapter,
    StackLightError,
    get_stacklight_adapter,
    reset_stacklight_adapter,
)
from mosk_mcp.adapters.stacklight.manager import (
    ManagerState,
    StackLightManager,
    get_stacklight_manager,
    reset_stacklight_manager,
)
from mosk_mcp.adapters.stacklight.response_models import (
    AlertQueryResult,
    ClusterQueryResult,
    ClusterStackLightHealth,
    CombinedAlertResult,
    CombinedLogResult,
    CombinedMetricResult,
    ComponentHealth,
    ComponentHealthStatus,
    ManagerHealthStatus,
    MetricQueryResult,
    StackLightManagerHealth,
)


__all__ = [
    "Alert",
    "AlertQueryResult",
    "AlertSeverity",
    "AlertState",
    "ClusterQueryResult",
    "ClusterStackLightHealth",
    "CombinedAlertResult",
    "CombinedLogResult",
    "CombinedMetricResult",
    "ComponentHealth",
    "ComponentHealthStatus",
    "DirectStackLightClient",
    "LogEntry",
    "LogQueryResult",
    "LogSeverity",
    "ManagerHealthStatus",
    "ManagerState",
    "MetricQueryResult",
    "MetricSample",
    "NaturalLanguageQueryParser",
    "StackLightAdapter",
    "StackLightError",
    "StackLightManager",
    "StackLightManagerHealth",
    "get_stacklight_adapter",
    "get_stacklight_manager",
    "reset_stacklight_adapter",
    "reset_stacklight_manager",
]
