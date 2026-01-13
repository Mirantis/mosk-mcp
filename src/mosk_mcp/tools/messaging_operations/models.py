"""Pydantic models for RabbitMQ messaging operations tools.

This module defines input/output models for all RabbitMQ-related MCP tools,
ensuring type safety and validation across the API.

All tools in this module are READ-ONLY and do not modify RabbitMQ state.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import Field

from mosk_mcp.tools.common.models import (
    IssuesMixin,
    MOSKBaseModel,
    MOSKOutputModel,
    RecommendationsMixin,
)


class RabbitMQHealthLevel(str, Enum):
    """RabbitMQ cluster health levels."""

    HEALTHY = "healthy"
    WARNING = "warning"
    CRITICAL = "critical"
    UNKNOWN = "unknown"


class ConnectionState(str, Enum):
    """RabbitMQ connection states."""

    RUNNING = "running"
    BLOCKED = "blocked"
    BLOCKING = "blocking"
    CLOSED = "closed"
    UNKNOWN = "unknown"


class AlarmType(str, Enum):
    """RabbitMQ alarm types."""

    MEMORY = "memory"
    DISK = "disk"
    NONE = "none"


# =============================================================================
# get_rabbitmq_status models
# =============================================================================


class GetRabbitMQStatusInput(MOSKBaseModel):
    """Input for get_rabbitmq_status tool."""

    rabbitmq_instance: Literal["main", "neutron"] = Field(
        default="main",
        description=(
            "RabbitMQ instance to query: 'main' for openstack-rabbitmq-rabbitmq-0, "
            "'neutron' for openstack-neutron-rabbitmq-rabbitmq-0"
        ),
    )
    include_feature_flags: bool = Field(
        default=False,
        description="Include enabled feature flags in output",
    )


class RabbitMQNodeInfo(MOSKBaseModel):
    """Information about a RabbitMQ node."""

    name: str = Field(..., description="Node name (e.g., rabbit@openstack-rabbitmq-rabbitmq-0)")
    running: bool = Field(..., description="Whether the node is running")
    memory_used_bytes: int = Field(default=0, description="Memory used in bytes")
    memory_limit_bytes: int = Field(default=0, description="Memory high watermark in bytes")
    memory_percent: float = Field(default=0.0, description="Memory usage percentage")
    disk_free_bytes: int = Field(default=0, description="Free disk space in bytes")
    cpu_cores: int = Field(default=0, description="Available CPU cores")
    erlang_version: str = Field(default="", description="Erlang/OTP version")
    rabbitmq_version: str = Field(default="", description="RabbitMQ version")


class GetRabbitMQStatusOutput(MOSKOutputModel, IssuesMixin, RecommendationsMixin):
    """Output from get_rabbitmq_status tool."""

    instance: str = Field(..., description="RabbitMQ instance queried (main or neutron)")
    cluster_name: str = Field(..., description="Cluster name")
    health: RabbitMQHealthLevel = Field(..., description="Overall cluster health")
    health_summary: str = Field(..., description="Human-readable health summary")

    # Node information
    nodes: list[RabbitMQNodeInfo] = Field(
        default_factory=list,
        description="Information about cluster nodes",
    )
    running_nodes: int = Field(..., description="Number of running nodes")
    total_nodes: int = Field(..., description="Total number of nodes")

    # Alarms
    alarms: list[str] = Field(
        default_factory=list,
        description="Active alarms (memory, disk)",
    )
    has_alarms: bool = Field(default=False, description="Whether any alarms are active")

    # Network partitions
    partitions: list[str] = Field(
        default_factory=list,
        description="Network partitions (should be empty)",
    )
    has_partitions: bool = Field(default=False, description="Whether partitions exist")

    # Maintenance status
    maintenance_status: str = Field(
        default="not under maintenance",
        description="Node maintenance status",
    )

    # Virtual hosts
    vhosts: list[str] = Field(default_factory=list, description="Configured virtual hosts")
    vhost_count: int = Field(default=0, description="Number of virtual hosts")

    # Feature flags (optional)
    feature_flags: dict[str, bool] = Field(
        default_factory=dict,
        description="Enabled feature flags (if requested)",
    )

    # Listeners
    listeners: list[str] = Field(
        default_factory=list,
        description="Active listeners (e.g., amqp:5672, http:15672)",
    )

    # Safety indicators
    is_healthy: bool = Field(..., description="Whether cluster is healthy")
    is_safe_for_operations: bool = Field(
        ...,
        description="Whether cluster is safe for maintenance operations",
    )


# =============================================================================
# list_rabbitmq_queues models
# =============================================================================


class ListRabbitMQQueuesInput(MOSKBaseModel):
    """Input for list_rabbitmq_queues tool."""

    rabbitmq_instance: Literal["main", "neutron"] = Field(
        default="main",
        description="RabbitMQ instance to query",
    )
    vhost: str | None = Field(
        default=None,
        description=(
            "Filter by vhost (e.g., 'nova', 'neutron', 'cinder'). "
            "If not specified, queries all vhosts."
        ),
    )
    show_empty: bool = Field(
        default=False,
        description="Include queues with zero messages",
    )
    include_consumers: bool = Field(
        default=True,
        description="Include consumer count per queue",
    )
    limit: int = Field(
        default=100,
        ge=1,
        le=1000,
        description="Maximum number of queues to return",
    )


class RabbitMQQueueInfo(MOSKBaseModel):
    """Information about a RabbitMQ queue."""

    name: str = Field(..., description="Queue name")
    vhost: str = Field(..., description="Virtual host")
    messages: int = Field(default=0, description="Number of messages in queue")
    messages_ready: int = Field(default=0, description="Messages ready for delivery")
    messages_unacked: int = Field(default=0, description="Messages awaiting acknowledgment")
    consumers: int = Field(default=0, description="Number of consumers")
    memory_bytes: int = Field(default=0, description="Memory used by queue in bytes")
    state: str = Field(default="running", description="Queue state")
    is_stale: bool = Field(
        default=False,
        description="True if queue has messages but no consumers",
    )


class QueuesByVhostSummary(MOSKBaseModel):
    """Queue summary for a virtual host."""

    vhost: str = Field(..., description="Virtual host name")
    queue_count: int = Field(default=0, description="Number of queues")
    total_messages: int = Field(default=0, description="Total messages across queues")
    total_consumers: int = Field(default=0, description="Total consumers")
    stale_queues: int = Field(default=0, description="Queues with messages but no consumers")


class ListRabbitMQQueuesOutput(MOSKOutputModel, RecommendationsMixin):
    """Output from list_rabbitmq_queues tool."""

    instance: str = Field(..., description="RabbitMQ instance queried")
    queues: list[RabbitMQQueueInfo] = Field(
        default_factory=list,
        description="List of queues matching filters",
    )

    # Summary statistics
    total_queues: int = Field(default=0, description="Total number of queues returned")
    total_messages: int = Field(default=0, description="Total messages across all queues")
    total_consumers: int = Field(default=0, description="Total consumers across all queues")
    stale_queue_count: int = Field(
        default=0,
        description="Queues with messages but no consumers",
    )

    # Per-vhost summary
    by_vhost: list[QueuesByVhostSummary] = Field(
        default_factory=list,
        description="Queue summary by virtual host",
    )

    # Top queues by message count
    top_queues_by_messages: list[str] = Field(
        default_factory=list,
        description="Top 5 queues by message count",
    )

    # Health indicators
    has_backlog: bool = Field(
        default=False,
        description="Whether any queue has significant message backlog",
    )
    has_stale_queues: bool = Field(
        default=False,
        description="Whether stale queues exist",
    )


# =============================================================================
# get_rabbitmq_connections models
# =============================================================================


class GetRabbitMQConnectionsInput(MOSKBaseModel):
    """Input for get_rabbitmq_connections tool."""

    rabbitmq_instance: Literal["main", "neutron"] = Field(
        default="main",
        description="RabbitMQ instance to query",
    )
    include_channels: bool = Field(
        default=False,
        description="Include channel information per connection",
    )
    group_by_user: bool = Field(
        default=True,
        description="Group connections by user/service",
    )
    limit: int = Field(
        default=200,
        ge=1,
        le=1000,
        description="Maximum number of connections to return",
    )


class RabbitMQConnectionInfo(MOSKBaseModel):
    """Information about a RabbitMQ connection."""

    name: str = Field(..., description="Connection name (client -> server)")
    user: str = Field(..., description="Username/service")
    state: ConnectionState = Field(default=ConnectionState.UNKNOWN, description="Connection state")
    ssl: bool = Field(default=False, description="Whether SSL is used")
    protocol: str = Field(default="AMQP 0-9-1", description="Protocol version")
    channels: int = Field(default=0, description="Number of channels")
    client_host: str = Field(default="", description="Client IP address")
    connected_at: str = Field(default="", description="Connection timestamp")


class ConnectionsByUserSummary(MOSKBaseModel):
    """Connection summary for a user/service."""

    user: str = Field(..., description="Username/service name")
    connection_count: int = Field(default=0, description="Number of connections")
    channel_count: int = Field(default=0, description="Total channels")
    service_name: str = Field(
        default="",
        description="Inferred OpenStack service (nova, neutron, etc.)",
    )


class GetRabbitMQConnectionsOutput(MOSKOutputModel, RecommendationsMixin):
    """Output from get_rabbitmq_connections tool."""

    instance: str = Field(..., description="RabbitMQ instance queried")
    connections: list[RabbitMQConnectionInfo] = Field(
        default_factory=list,
        description="List of connections",
    )

    # Summary statistics
    total_connections: int = Field(default=0, description="Total connection count")
    total_channels: int = Field(default=0, description="Total channel count")
    running_connections: int = Field(default=0, description="Connections in running state")
    blocked_connections: int = Field(default=0, description="Connections in blocked state")

    # Per-user/service summary
    by_user: list[ConnectionsByUserSummary] = Field(
        default_factory=list,
        description="Connection summary by user/service",
    )

    # Top consumers
    top_users: list[str] = Field(
        default_factory=list,
        description="Top 5 users by connection count",
    )

    # Health indicators
    connection_limit: int | None = Field(
        default=None,
        description="Connection limit (if known)",
    )
    connection_utilization_percent: float | None = Field(
        default=None,
        description="Connection pool utilization percentage",
    )
    has_blocked_connections: bool = Field(
        default=False,
        description="Whether any connections are blocked",
    )
    is_connection_pool_healthy: bool = Field(
        default=True,
        description="Whether connection pool is healthy",
    )


# =============================================================================
# diagnose_rabbitmq_issue models
# =============================================================================


class DiagnoseRabbitMQIssueInput(MOSKBaseModel):
    """Input for diagnose_rabbitmq_issue tool."""

    rabbitmq_instance: Literal["main", "neutron", "all"] = Field(
        default="all",
        description="RabbitMQ instance to diagnose ('all' checks both)",
    )
    include_queue_analysis: bool = Field(
        default=True,
        description="Include queue depth and consumer analysis",
    )
    include_connection_analysis: bool = Field(
        default=True,
        description="Include connection pool analysis",
    )
    check_for_known_issues: bool = Field(
        default=True,
        description="Check against known RabbitMQ issue patterns",
    )


class RabbitMQDiagnosticCheck(MOSKBaseModel):
    """Result of a diagnostic check."""

    check_name: str = Field(..., description="Check name")
    status: Literal["pass", "warn", "fail", "skip"] = Field(
        ...,
        description="Check status",
    )
    message: str = Field(..., description="Check result message")
    severity: Literal["info", "warning", "error", "critical"] = Field(
        default="info",
        description="Severity level",
    )
    details: dict = Field(default_factory=dict, description="Additional details")


class RabbitMQInstanceDiagnosis(MOSKBaseModel):
    """Diagnosis for a single RabbitMQ instance."""

    instance: str = Field(..., description="Instance name (main or neutron)")
    health: RabbitMQHealthLevel = Field(..., description="Instance health")
    checks: list[RabbitMQDiagnosticCheck] = Field(
        default_factory=list,
        description="Diagnostic check results",
    )
    issues_found: list[str] = Field(
        default_factory=list,
        description="Issues found during diagnosis",
    )
    known_issue_matches: list[str] = Field(
        default_factory=list,
        description="Matching known issue IDs",
    )


class DiagnoseRabbitMQIssueOutput(MOSKOutputModel, RecommendationsMixin):
    """Output from diagnose_rabbitmq_issue tool."""

    # Per-instance diagnosis
    instances: list[RabbitMQInstanceDiagnosis] = Field(
        default_factory=list,
        description="Diagnosis per RabbitMQ instance",
    )

    # Overall health
    overall_health: RabbitMQHealthLevel = Field(
        ...,
        description="Overall messaging system health",
    )
    health_summary: str = Field(..., description="Human-readable health summary")

    # Check summary
    total_checks: int = Field(default=0, description="Total checks performed")
    checks_passed: int = Field(default=0, description="Checks that passed")
    checks_warned: int = Field(default=0, description="Checks with warnings")
    checks_failed: int = Field(default=0, description="Checks that failed")

    # Issues
    critical_issues: list[str] = Field(
        default_factory=list,
        description="Critical issues requiring immediate attention",
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="Warnings that may need attention",
    )

    # Known issue matching
    known_issue_ids: list[str] = Field(
        default_factory=list,
        description="Matching known issue IDs from knowledge base",
    )

    # Is the messaging system healthy?
    is_healthy: bool = Field(..., description="Whether messaging is healthy")
    requires_immediate_action: bool = Field(
        default=False,
        description="Whether immediate action is required",
    )
