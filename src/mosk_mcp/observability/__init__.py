"""Observability module for MOSK MCP Server.

This package contains observability components:
- logging.py: Structured JSON logging
- metrics.py: Prometheus metrics
- health.py: Health check endpoints
- audit.py: Audit logging for compliance
"""

from __future__ import annotations

from mosk_mcp.observability.audit import (
    AuditCategory,
    AuditContext,
    AuditEvent,
    AuditLevel,
    AuditLogger,
    AuditStatus,
)
from mosk_mcp.observability.health import (
    CheckResult,
    HealthChecker,
    HealthResponse,
    HealthStatus,
    create_health_app,
    get_health_checker,
    init_health_checker,
)
from mosk_mcp.observability.logging import (
    LoggingContext,
    get_logger,
    setup_logging,
)
from mosk_mcp.observability.metrics import (
    MetricsRegistry,
    SafetyLevel,
    ToolStatus,
    create_metrics_app,
    get_metrics_registry,
    init_metrics_registry,
    record_auth_failure,
    record_k8s_request,
    record_privileged_op,
    track_tool,
)


__all__ = [
    # Audit
    "AuditCategory",
    "AuditContext",
    "AuditEvent",
    "AuditLevel",
    "AuditLogger",
    "AuditStatus",
    # Health
    "CheckResult",
    "HealthChecker",
    "HealthResponse",
    "HealthStatus",
    # Logging
    "LoggingContext",
    # Metrics
    "MetricsRegistry",
    "SafetyLevel",
    "ToolStatus",
    "create_health_app",
    "create_metrics_app",
    "get_health_checker",
    "get_logger",
    "get_metrics_registry",
    "init_health_checker",
    "init_metrics_registry",
    "record_auth_failure",
    "record_k8s_request",
    "record_privileged_op",
    "setup_logging",
    "track_tool",
]
