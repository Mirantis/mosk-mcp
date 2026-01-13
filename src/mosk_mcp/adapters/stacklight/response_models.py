"""Pydantic models for StackLight Manager responses.

This module provides enterprise-grade, type-safe models for StackLight
operations across dual clusters (MCC and MOSK).

Models support:
- Cluster-aware alert and metric responses
- Health status tracking for both StackLight deployments
- Combined query results with deduplication metadata
- Graceful degradation status indicators

Architecture:
    StackLightManager uses these models to provide structured,
    validated responses for dual-cluster queries. Each response
    includes success/failure status per cluster for graceful
    degradation handling.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


# Type alias for cluster types (matches stacklight.py and server_context.py)
ClusterType = Literal["mcc", "mosk"]


class ComponentHealthStatus(str, Enum):
    """Health status for individual StackLight components."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


class ManagerHealthStatus(str, Enum):
    """Overall health status for StackLight Manager."""

    HEALTHY = "healthy"  # Both clusters fully operational
    DEGRADED = "degraded"  # One or more components unavailable
    UNAVAILABLE = "unavailable"  # All services unavailable
    UNKNOWN = "unknown"  # Unable to determine status


# =============================================================================
# Component Health Models
# =============================================================================


@dataclass
class ComponentHealth:
    """Health status for a single StackLight component.

    Attributes:
        component: Component name (opensearch, prometheus, alertmanager).
        status: Current health status.
        pod_name: Name of the pod if discovered.
        last_check: Timestamp of last health check.
        error_message: Error message if unhealthy.
        latency_ms: Response latency in milliseconds.
    """

    component: str
    status: ComponentHealthStatus = ComponentHealthStatus.UNKNOWN
    pod_name: str | None = None
    last_check: datetime = field(default_factory=lambda: datetime.now(UTC))
    error_message: str | None = None
    latency_ms: float | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "component": self.component,
            "status": self.status.value,
            "pod_name": self.pod_name,
            "last_check": self.last_check.isoformat(),
            "error_message": self.error_message,
            "latency_ms": self.latency_ms,
        }


@dataclass
class ClusterStackLightHealth:
    """Health status for a single cluster's StackLight deployment.

    Attributes:
        cluster_type: Cluster identifier (mcc or mosk).
        status: Overall health status for this cluster.
        components: Health status per component.
        last_check: Timestamp of last health check.
        message: Human-readable status message.
    """

    cluster_type: ClusterType
    status: ManagerHealthStatus = ManagerHealthStatus.UNKNOWN
    components: dict[str, ComponentHealth] = field(default_factory=dict)
    last_check: datetime = field(default_factory=lambda: datetime.now(UTC))
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "cluster_type": self.cluster_type,
            "status": self.status.value,
            "components": {k: v.to_dict() for k, v in self.components.items()},
            "last_check": self.last_check.isoformat(),
            "message": self.message,
        }


@dataclass
class StackLightManagerHealth:
    """Combined health status for StackLight Manager.

    Attributes:
        overall_status: Combined health across both clusters.
        mcc: Health status for MCC cluster StackLight.
        mosk: Health status for MOSK cluster StackLight.
        timestamp: When this health check was performed.
        recommendations: Actionable recommendations if unhealthy.
    """

    overall_status: ManagerHealthStatus = ManagerHealthStatus.UNKNOWN
    mcc: ClusterStackLightHealth | None = None
    mosk: ClusterStackLightHealth | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    recommendations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "overall_status": self.overall_status.value,
            "mcc": self.mcc.to_dict() if self.mcc else None,
            "mosk": self.mosk.to_dict() if self.mosk else None,
            "timestamp": self.timestamp.isoformat(),
            "recommendations": self.recommendations,
        }


# =============================================================================
# Query Response Models (Pydantic for validation)
# =============================================================================


class ClusterQueryResult(BaseModel):
    """Result from querying a single cluster.

    Generic container for query results with success/failure tracking.
    Used by alert, log, and metric queries.
    """

    model_config = ConfigDict(extra="allow")

    cluster_type: ClusterType = Field(description="Cluster identifier")
    success: bool = Field(description="Whether query succeeded")
    count: int = Field(default=0, description="Number of results returned")
    error_message: str | None = Field(default=None, description="Error message if query failed")
    query_duration_ms: float | None = Field(
        default=None, description="Query duration in milliseconds"
    )


class AlertQueryResult(ClusterQueryResult):
    """Result from querying alerts on a single cluster.

    Extends ClusterQueryResult with alert-specific fields.
    """

    alerts: list[dict[str, Any]] = Field(
        default_factory=list, description="List of alerts as dictionaries"
    )
    firing_count: int = Field(default=0, description="Count of firing alerts")
    warning_count: int = Field(default=0, description="Count of warning alerts")
    critical_count: int = Field(default=0, description="Count of critical alerts")


class CombinedAlertResult(BaseModel):
    """Combined alert query result from both clusters.

    Provides unified view with deduplication and per-cluster breakdown.
    """

    mcc: AlertQueryResult = Field(description="MCC cluster alert results")
    mosk: AlertQueryResult = Field(description="MOSK cluster alert results")
    combined_alerts: list[dict[str, Any]] = Field(
        default_factory=list, description="Deduplicated combined alerts"
    )
    total_unique: int = Field(default=0, description="Total unique alerts")
    total_firing: int = Field(default=0, description="Total firing alerts")
    total_critical: int = Field(default=0, description="Total critical alerts")
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Query timestamp",
    )
    overall_success: bool = Field(
        default=False, description="True if at least one cluster succeeded"
    )
    degraded: bool = Field(default=False, description="True if one cluster failed")


class MetricQueryResult(ClusterQueryResult):
    """Result from querying metrics on a single cluster.

    Extends ClusterQueryResult with metric-specific fields.
    """

    samples: list[dict[str, Any]] = Field(
        default_factory=list, description="List of metric samples as dictionaries"
    )
    metric_names: list[str] = Field(
        default_factory=list, description="Unique metric names in results"
    )


class CombinedMetricResult(BaseModel):
    """Combined metric query result from both clusters.

    Provides unified view with per-cluster breakdown.
    """

    mcc: MetricQueryResult = Field(description="MCC cluster metric results")
    mosk: MetricQueryResult = Field(description="MOSK cluster metric results")
    combined_samples: list[dict[str, Any]] = Field(
        default_factory=list, description="All samples with cluster labels"
    )
    total_samples: int = Field(default=0, description="Total sample count")
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Query timestamp",
    )
    overall_success: bool = Field(
        default=False, description="True if at least one cluster succeeded"
    )
    degraded: bool = Field(default=False, description="True if one cluster failed")


class LogQueryResult(ClusterQueryResult):
    """Result from querying logs on a single cluster.

    Extends ClusterQueryResult with log-specific fields.
    """

    logs: list[dict[str, Any]] = Field(
        default_factory=list, description="List of log entries as dictionaries"
    )
    services: list[str] = Field(default_factory=list, description="Unique services in results")
    error_count: int = Field(default=0, description="Count of error logs")
    warning_count: int = Field(default=0, description="Count of warning logs")


class CombinedLogResult(BaseModel):
    """Combined log query result from both clusters.

    Provides unified view with per-cluster breakdown.
    """

    mcc: LogQueryResult = Field(description="MCC cluster log results")
    mosk: LogQueryResult = Field(description="MOSK cluster log results")
    combined_logs: list[dict[str, Any]] = Field(
        default_factory=list, description="All logs with cluster labels, sorted by time"
    )
    total_logs: int = Field(default=0, description="Total log count")
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Query timestamp",
    )
    overall_success: bool = Field(
        default=False, description="True if at least one cluster succeeded"
    )
    degraded: bool = Field(default=False, description="True if one cluster failed")


# =============================================================================
# Alert Deduplication Helpers
# =============================================================================


def compute_alert_fingerprint(alert_dict: dict[str, Any]) -> str:
    """Compute a fingerprint for alert deduplication.

    Alerts are considered duplicates if they have the same:
    - alert_name
    - severity
    - Core labels (excluding cluster-specific labels)

    Args:
        alert_dict: Alert as dictionary.

    Returns:
        Fingerprint string for deduplication.
    """
    # Use alert name, severity, and key labels for fingerprint
    name = alert_dict.get("alert_name", "")
    severity = alert_dict.get("severity", "")
    labels = alert_dict.get("labels", {})

    # Exclude cluster-specific labels from fingerprint
    exclude_labels = {"cluster", "cluster_type", "kubernetes_cluster"}
    key_labels = {k: v for k, v in sorted(labels.items()) if k not in exclude_labels}

    # Build fingerprint
    parts = [name, severity]
    for k, v in key_labels.items():
        parts.append(f"{k}={v}")

    return "|".join(parts)


def deduplicate_alerts(
    alerts: list[dict[str, Any]],
    prefer_cluster: ClusterType | None = None,
) -> list[dict[str, Any]]:
    """Deduplicate alerts from multiple clusters.

    When the same alert fires on both clusters, keep only one copy.
    Prefers the alert from the specified cluster, or the most recent one.

    Args:
        alerts: List of alerts with cluster_type field.
        prefer_cluster: Preferred cluster when deduplicating.

    Returns:
        Deduplicated list of alerts.
    """
    seen: dict[str, dict[str, Any]] = {}

    for alert in alerts:
        fingerprint = compute_alert_fingerprint(alert)

        if fingerprint not in seen:
            seen[fingerprint] = alert
        else:
            existing = seen[fingerprint]
            alert_cluster = alert.get("cluster_type")
            existing_cluster = existing.get("cluster_type")

            # Prefer specified cluster
            if prefer_cluster and alert_cluster == prefer_cluster:
                seen[fingerprint] = alert
            elif prefer_cluster and existing_cluster == prefer_cluster:
                pass  # Keep existing
            else:
                # Prefer more recent alert
                alert_time = alert.get("starts_at")
                existing_time = existing.get("starts_at")
                if alert_time and existing_time and alert_time > existing_time:
                    seen[fingerprint] = alert

    return list(seen.values())


# =============================================================================
# Factory functions for creating empty results
# =============================================================================


def empty_alert_result(cluster_type: ClusterType, error: str | None = None) -> AlertQueryResult:
    """Create an empty alert query result.

    Args:
        cluster_type: Cluster identifier.
        error: Optional error message.

    Returns:
        AlertQueryResult with no alerts.
    """
    return AlertQueryResult(
        cluster_type=cluster_type,
        success=error is None,
        count=0,
        error_message=error,
        alerts=[],
        firing_count=0,
        warning_count=0,
        critical_count=0,
    )


def empty_metric_result(cluster_type: ClusterType, error: str | None = None) -> MetricQueryResult:
    """Create an empty metric query result.

    Args:
        cluster_type: Cluster identifier.
        error: Optional error message.

    Returns:
        MetricQueryResult with no samples.
    """
    return MetricQueryResult(
        cluster_type=cluster_type,
        success=error is None,
        count=0,
        error_message=error,
        samples=[],
        metric_names=[],
    )


def empty_log_result(cluster_type: ClusterType, error: str | None = None) -> LogQueryResult:
    """Create an empty log query result.

    Args:
        cluster_type: Cluster identifier.
        error: Optional error message.

    Returns:
        LogQueryResult with no logs.
    """
    return LogQueryResult(
        cluster_type=cluster_type,
        success=error is None,
        count=0,
        error_message=error,
        logs=[],
        services=[],
        error_count=0,
        warning_count=0,
    )
