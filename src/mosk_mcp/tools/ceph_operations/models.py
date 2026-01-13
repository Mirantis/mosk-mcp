"""Pydantic models for Ceph storage operations tools.

This module defines input/output models for all Ceph-related MCP tools,
ensuring type safety and validation across the API.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import Field

# Import CapacityStatus from common enums to avoid duplication
# The canonical definition with comparison operators is in common.enums
from mosk_mcp.tools.common.enums import CapacityStatus
from mosk_mcp.tools.common.models import MOSKBaseModel


# Re-export for backward compatibility
__all__ = ["CapacityStatus"]


class CephHealthLevel(str, Enum):
    """Ceph cluster health levels."""

    HEALTH_OK = "HEALTH_OK"
    HEALTH_WARN = "HEALTH_WARN"
    HEALTH_ERR = "HEALTH_ERR"
    UNKNOWN = "UNKNOWN"


# =============================================================================
# get_ceph_status models
# =============================================================================


class GetCephStatusInput(MOSKBaseModel):
    """Input for get_ceph_status tool."""

    include_health_details: bool = Field(
        default=True,
        description="Include detailed health check information",
    )
    include_pg_summary: bool = Field(
        default=True,
        description="Include placement group summary",
    )


class HealthCheckInfo(MOSKBaseModel):
    """Information about a health check."""

    severity: str = Field(..., description="Severity level (WARN, ERR)")
    message: str = Field(..., description="Health check message")
    count: int = Field(default=1, description="Number of occurrences")


class GetCephStatusOutput(MOSKBaseModel):
    """Output from get_ceph_status tool."""

    health: CephHealthLevel = Field(..., description="Overall cluster health status")
    health_summary: str = Field(..., description="Human-readable health summary")
    health_checks: dict[str, HealthCheckInfo] = Field(
        default_factory=dict,
        description="Active health checks",
    )
    fsid: str = Field(..., description="Cluster FSID")
    quorum: list[str] = Field(..., description="Monitor quorum members")
    num_osds: int = Field(..., description="Total number of OSDs")
    num_osds_up: int = Field(..., description="Number of OSDs that are up")
    num_osds_in: int = Field(..., description="Number of OSDs that are in")
    num_pgs: int = Field(..., description="Total number of placement groups")
    pg_summary: dict[str, int] = Field(
        default_factory=dict,
        description="PG state counts",
    )
    capacity: CapacitySummary = Field(..., description="Storage capacity summary")
    is_healthy: bool = Field(..., description="Whether cluster is healthy")
    is_safe_for_operations: bool = Field(
        ...,
        description="Whether cluster is safe for maintenance operations",
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="Active warnings",
    )
    timestamp: str = Field(..., description="Status retrieval timestamp")


class CapacitySummary(MOSKBaseModel):
    """Storage capacity summary."""

    total_bytes: int = Field(..., description="Total raw storage in bytes", ge=0)
    used_bytes: int = Field(..., description="Used raw storage in bytes", ge=0)
    available_bytes: int = Field(..., description="Available raw storage in bytes", ge=0)
    percent_used: float = Field(..., description="Percentage of capacity used", ge=0.0, le=100.0)
    status: CapacityStatus = Field(..., description="Capacity status level")
    total_human: str = Field(..., description="Human-readable total")
    used_human: str = Field(..., description="Human-readable used")
    available_human: str = Field(..., description="Human-readable available")


# =============================================================================
# list_osds models
# =============================================================================


class ListOSDsInput(MOSKBaseModel):
    """Input for list_osds tool."""

    host_filter: str | None = Field(
        default=None,
        description="Filter OSDs by host name",
    )
    status_filter: Literal["all", "up", "down"] | None = Field(
        default=None,
        description="Filter OSDs by status",
    )
    include_performance: bool = Field(
        default=False,
        description="Include latency metrics",
    )


class OSDSummary(MOSKBaseModel):
    """Summary information about an OSD."""

    osd_id: int = Field(..., description="OSD identifier", ge=0)
    host: str = Field(..., description="Host running this OSD")
    status: str = Field(..., description="up or down")
    state: str = Field(..., description="in or out")
    device_class: str = Field(default="", description="Device class (hdd, ssd, nvme)")
    utilization_percent: float = Field(..., description="Utilization percentage", ge=0.0, le=100.0)
    capacity_bytes: int = Field(..., description="Total capacity in bytes", ge=0)
    used_bytes: int = Field(..., description="Used capacity in bytes", ge=0)
    pgs: int = Field(..., description="Number of PGs", ge=0)
    is_healthy: bool = Field(..., description="Whether OSD is healthy (up and in)")


class ListOSDsOutput(MOSKBaseModel):
    """Output from list_osds tool."""

    osds: list[OSDSummary] = Field(..., description="List of OSDs")
    total_count: int = Field(..., description="Total number of OSDs", ge=0)
    up_count: int = Field(..., description="Number of up OSDs", ge=0)
    down_count: int = Field(..., description="Number of down OSDs", ge=0)
    in_count: int = Field(..., description="Number of in OSDs", ge=0)
    out_count: int = Field(..., description="Number of out OSDs", ge=0)
    by_host: dict[str, int] = Field(
        default_factory=dict,
        description="OSD count per host",
    )
    by_device_class: dict[str, int] = Field(
        default_factory=dict,
        description="OSD count per device class",
    )


# =============================================================================
# get_osd_details models
# =============================================================================


class GetOSDDetailsInput(MOSKBaseModel):
    """Input for get_osd_details tool."""

    osd_id: int = Field(..., description="OSD identifier", ge=0)
    include_pg_distribution: bool = Field(
        default=True,
        description="Include PG distribution information",
    )
    include_performance: bool = Field(
        default=True,
        description="Include performance metrics",
    )


class OSDDetails(MOSKBaseModel):
    """Detailed information about an OSD."""

    osd_id: int = Field(..., description="OSD identifier")
    uuid: str = Field(..., description="OSD UUID")
    host: str = Field(..., description="Host running this OSD")
    status: str = Field(..., description="up or down")
    state: str = Field(..., description="in or out")
    device_class: str = Field(default="", description="Device class")
    crush_weight: float = Field(..., description="CRUSH weight")
    reweight: float = Field(..., description="Reweight value")
    capacity: CapacitySummary = Field(..., description="Capacity information")
    pgs: int = Field(..., description="Number of PGs")
    pg_distribution: dict[str, int] = Field(
        default_factory=dict,
        description="PGs per pool",
    )
    commit_latency_ms: float = Field(..., description="Commit latency in ms")
    apply_latency_ms: float = Field(..., description="Apply latency in ms")
    is_healthy: bool = Field(..., description="Whether OSD is healthy")
    health_warnings: list[str] = Field(
        default_factory=list,
        description="Health warnings for this OSD",
    )


class GetOSDDetailsOutput(MOSKBaseModel):
    """Output from get_osd_details tool."""

    osd: OSDDetails = Field(..., description="OSD details")
    recommendations: list[str] = Field(
        default_factory=list,
        description="Operational recommendations",
    )


# =============================================================================
# get_ceph_capacity models
# =============================================================================


class GetCephCapacityInput(MOSKBaseModel):
    """Input for get_ceph_capacity tool."""

    include_pools: bool = Field(
        default=True,
        description="Include per-pool capacity breakdown",
    )
    include_classes: bool = Field(
        default=True,
        description="Include capacity by device class",
    )


class PoolCapacity(MOSKBaseModel):
    """Capacity information for a pool."""

    pool_id: int = Field(..., description="Pool identifier")
    pool_name: str = Field(..., description="Pool name")
    stored_bytes: int = Field(..., description="Data stored in bytes")
    used_bytes: int = Field(..., description="Raw used bytes (with replication)")
    max_available_bytes: int = Field(..., description="Maximum available bytes")
    percent_used: float = Field(..., description="Percentage used")
    objects: int = Field(..., description="Number of objects")
    replication_size: int = Field(..., description="Replication factor")


class GetCephCapacityOutput(MOSKBaseModel):
    """Output from get_ceph_capacity tool."""

    total_bytes: int = Field(..., description="Total raw storage")
    used_bytes: int = Field(..., description="Used raw storage")
    available_bytes: int = Field(..., description="Available raw storage")
    percent_used: float = Field(..., description="Percentage used")
    status: CapacityStatus = Field(..., description="Capacity status")
    thresholds: dict[str, int] = Field(..., description="Capacity thresholds")
    pools: list[PoolCapacity] = Field(
        default_factory=list,
        description="Per-pool capacity",
    )
    by_device_class: dict[str, dict[str, Any]] = Field(
        default_factory=dict,
        description="Capacity by device class",
    )
    recommendations: list[str] = Field(
        default_factory=list,
        description="Capacity recommendations",
    )
    timestamp: str = Field(..., description="Retrieval timestamp")


# =============================================================================
# get_pg_status models
# =============================================================================


class GetPGStatusInput(MOSKBaseModel):
    """Input for get_pg_status tool."""

    include_stuck: bool = Field(
        default=True,
        description="Include stuck PG analysis",
    )
    include_recovery: bool = Field(
        default=True,
        description="Include recovery progress",
    )


class PGStateCount(MOSKBaseModel):
    """Count of PGs in a particular state."""

    state: str = Field(..., description="PG state string")
    count: int = Field(..., description="Number of PGs in this state")
    is_healthy: bool = Field(..., description="Whether this is a healthy state")


class GetPGStatusOutput(MOSKBaseModel):
    """Output from get_pg_status tool."""

    total_pgs: int = Field(..., description="Total number of PGs")
    active_clean: int = Field(..., description="PGs in active+clean state")
    states: list[PGStateCount] = Field(..., description="PG state breakdown")
    stuck_pgs: dict[str, int] = Field(
        default_factory=dict,
        description="Stuck PGs by type",
    )
    is_healthy: bool = Field(..., description="Whether all PGs are healthy")
    recovery_active: bool = Field(..., description="Whether recovery is active")
    misplaced_ratio: float = Field(..., description="Misplaced object ratio")
    degraded_ratio: float = Field(..., description="Degraded object ratio")
    health_summary: str = Field(..., description="PG health summary")
    recommendations: list[str] = Field(
        default_factory=list,
        description="Recommendations",
    )


# =============================================================================
# predict_capacity models
# =============================================================================


class PredictCapacityInput(MOSKBaseModel):
    """Input for predict_capacity tool."""

    days_to_forecast: int = Field(
        default=30,
        description="Number of days to forecast",
        ge=1,
        le=365,
    )
    growth_rate_gb_per_day: float | None = Field(
        default=None,
        description="Override growth rate in GB/day (auto-detected if not provided)",
    )
    include_recommendations: bool = Field(
        default=True,
        description="Include capacity planning recommendations",
    )


class CapacityForecast(MOSKBaseModel):
    """Capacity forecast for a specific date."""

    date: str = Field(..., description="Forecast date (ISO format)")
    days_from_now: int = Field(..., description="Days from current date")
    predicted_used_bytes: int = Field(..., description="Predicted used bytes")
    predicted_percent_used: float = Field(..., description="Predicted utilization %")
    predicted_status: CapacityStatus = Field(..., description="Predicted status")


class PredictCapacityOutput(MOSKBaseModel):
    """Output from predict_capacity tool."""

    current_used_bytes: int = Field(..., description="Current used bytes")
    current_percent_used: float = Field(..., description="Current utilization %")
    growth_rate_bytes_per_day: int = Field(..., description="Growth rate bytes/day")
    growth_rate_human: str = Field(..., description="Human-readable growth rate")
    forecasts: list[CapacityForecast] = Field(..., description="Capacity forecasts")
    days_until_warning: int | None = Field(
        None,
        description="Days until warning threshold",
    )
    days_until_critical: int | None = Field(
        None,
        description="Days until critical threshold",
    )
    days_until_full: int | None = Field(
        None,
        description="Days until storage is full",
    )
    recommendations: list[str] = Field(
        default_factory=list,
        description="Capacity planning recommendations",
    )
    confidence: str = Field(..., description="Forecast confidence level")


# =============================================================================
# get_recovery_status models
# =============================================================================


class GetRecoveryStatusInput(MOSKBaseModel):
    """Input for get_recovery_status tool."""

    include_pg_details: bool = Field(
        default=False,
        description="Include per-PG recovery details",
    )
    include_osd_details: bool = Field(
        default=False,
        description="Include per-OSD recovery details",
    )


class RecoveryProgress(MOSKBaseModel):
    """Recovery progress information."""

    objects_recovered: int = Field(..., description="Objects recovered")
    objects_to_recover: int = Field(..., description="Total objects to recover")
    bytes_recovered: int = Field(..., description="Bytes recovered")
    bytes_to_recover: int = Field(..., description="Total bytes to recover")
    percent_complete: float = Field(..., description="Recovery percentage")
    recovery_rate_bytes_per_sec: int = Field(..., description="Recovery rate")
    estimated_time_remaining: str = Field(..., description="Estimated time remaining")


class GetRecoveryStatusOutput(MOSKBaseModel):
    """Output from get_recovery_status tool."""

    is_recovering: bool = Field(..., description="Whether recovery is in progress")
    is_backfilling: bool = Field(..., description="Whether backfill is in progress")
    is_rebalancing: bool = Field(..., description="Whether rebalancing is active")
    recovery_progress: RecoveryProgress | None = Field(
        None,
        description="Recovery progress (if active)",
    )
    misplaced_objects: int = Field(..., description="Misplaced object count")
    misplaced_ratio: float = Field(..., description="Misplaced ratio %")
    degraded_objects: int = Field(..., description="Degraded object count")
    degraded_ratio: float = Field(..., description="Degraded ratio %")
    pgs_recovering: int = Field(..., description="PGs in recovery")
    pgs_backfilling: int = Field(..., description="PGs in backfill")
    status_summary: str = Field(..., description="Human-readable summary")
    recommendations: list[str] = Field(
        default_factory=list,
        description="Recommendations",
    )
    timestamp: str = Field(..., description="Status timestamp")


# Update forward references
GetCephStatusOutput.model_rebuild()
