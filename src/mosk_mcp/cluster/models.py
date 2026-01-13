"""Pydantic models for cluster management MCP tools.

These models define the inputs and outputs for cluster management operations
with strict validation to prevent security issues.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from mosk_mcp.cluster.config import ClusterEnvironment


class ClusterInfo(BaseModel):
    """Information about a single cluster."""

    id: str = Field(description="Unique cluster identifier")
    name: str = Field(description="Display name")
    url: str = Field(description="MCC URL")
    environment: ClusterEnvironment = Field(description="Environment type")
    ssl_verify: bool = Field(description="SSL verification enabled")
    is_active: bool = Field(description="Whether this is the active cluster")
    is_authenticated: bool = Field(description="Whether user is authenticated")
    is_locked: bool = Field(description="Whether cluster is locked")
    has_fingerprint: bool = Field(description="Whether cluster has verified fingerprint")
    description: str | None = Field(default=None, description="Cluster description")
    last_used_at: datetime | None = Field(default=None)

    @property
    def safety_indicator(self) -> str:
        """Visual safety indicator for the cluster."""
        indicators = []
        if self.environment == ClusterEnvironment.PRODUCTION:
            indicators.append("[PROD]")
        if self.is_locked:
            indicators.append("[LOCKED]")
        if not self.ssl_verify:
            indicators.append("[NO-SSL]")
        if self.is_active:
            indicators.append("[ACTIVE]")
        return " ".join(indicators) if indicators else ""


class ListClustersOutput(BaseModel):
    """Output for list_clusters tool."""

    active_cluster: str | None = Field(description="ID of currently active cluster")
    clusters: list[ClusterInfo] = Field(description="List of configured clusters")
    total_count: int = Field(description="Total number of clusters")

    # Safety information
    active_is_production: bool = Field(
        default=False,
        description="Whether active cluster is production",
    )
    warning: str | None = Field(
        default=None,
        description="Safety warning if applicable",
    )


class SwitchClusterInput(BaseModel):
    """Input for switch_cluster tool."""

    cluster_id: str = Field(
        description="ID of the cluster to switch to",
        min_length=1,
        max_length=63,
    )
    confirm_production: bool = Field(
        default=False,
        description="Confirm switching to production cluster (required for prod)",
    )
    force: bool = Field(
        default=False,
        description="Force switch even if current cluster is locked (dangerous)",
    )


class ClusterSwitchConfirmation(BaseModel):
    """Confirmation request when switching to production."""

    requires_confirmation: bool = Field(
        default=True,
        description="Whether user must confirm the switch",
    )
    target_cluster: str = Field(description="Cluster being switched to")
    target_environment: ClusterEnvironment = Field(description="Target environment")
    warning_message: str = Field(description="Warning to display to user")
    confirmation_phrase: str = Field(description="Phrase user must provide to confirm")


class SwitchClusterOutput(BaseModel):
    """Output for switch_cluster tool."""

    success: bool = Field(description="Whether switch was successful")
    previous_cluster: str | None = Field(description="Previous active cluster")
    new_cluster: str = Field(description="New active cluster")
    new_cluster_url: str = Field(description="URL of new cluster")
    new_cluster_environment: ClusterEnvironment = Field(description="Environment type")

    # Status
    requires_login: bool = Field(
        default=True,
        description="Whether user needs to authenticate",
    )
    session_cleared: bool = Field(
        default=True,
        description="Whether previous session was cleared",
    )

    # Safety messages
    message: str = Field(description="Human-readable status message")
    warnings: list[str] = Field(
        default_factory=list,
        description="Safety warnings",
    )

    # Confirmation (if required but not provided)
    confirmation_required: ClusterSwitchConfirmation | None = Field(
        default=None,
        description="Confirmation required before switch completes",
    )


class CurrentClusterOutput(BaseModel):
    """Output for current_cluster tool."""

    has_active_cluster: bool = Field(description="Whether a cluster is active")
    cluster: ClusterInfo | None = Field(
        default=None,
        description="Active cluster information",
    )

    # Authentication status
    is_authenticated: bool = Field(
        default=False,
        description="Whether user is authenticated to the cluster",
    )
    auth_expires_at: datetime | None = Field(
        default=None,
        description="When authentication expires",
    )
    username: str | None = Field(
        default=None,
        description="Authenticated username",
    )

    # Safety status
    fingerprint_verified: bool = Field(
        default=False,
        description="Whether cluster fingerprint is verified",
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="Active warnings",
    )

    # Helpful guidance
    next_action: str | None = Field(
        default=None,
        description="Suggested next action for user",
    )


class AddClusterInput(BaseModel):
    """Input for add_cluster tool."""

    cluster_id: str = Field(
        description="Unique identifier (e.g., 'prod', 'staging')",
        min_length=1,
        max_length=63,
        pattern=r"^[a-zA-Z][a-zA-Z0-9_-]*$",
    )
    url: str = Field(
        description="MCC cluster URL (https://...)",
        min_length=10,
    )
    name: str | None = Field(
        default=None,
        description="Human-readable name",
        max_length=100,
    )
    environment: ClusterEnvironment = Field(
        default=ClusterEnvironment.DEVELOPMENT,
        description="Environment type (affects safety checks)",
    )
    ssl_verify: bool = Field(
        default=True,
        description="Verify SSL certificates",
    )
    description: str | None = Field(
        default=None,
        description="Cluster description",
        max_length=500,
    )
    set_active: bool = Field(
        default=False,
        description="Make this the active cluster after adding",
    )


class AddClusterOutput(BaseModel):
    """Output for add_cluster tool."""

    success: bool = Field(description="Whether cluster was added")
    cluster_id: str = Field(description="ID of the added cluster")
    cluster_url: str = Field(description="URL of the added cluster")
    is_active: bool = Field(description="Whether cluster is now active")

    # Validation results
    url_reachable: bool = Field(
        default=False,
        description="Whether URL was reachable during validation",
    )
    validation_warnings: list[str] = Field(
        default_factory=list,
        description="Warnings from validation",
    )

    # Next steps
    message: str = Field(description="Status message")
    next_action: str = Field(
        description="Suggested next action",
    )


class RemoveClusterInput(BaseModel):
    """Input for remove_cluster tool."""

    cluster_id: str = Field(
        description="ID of cluster to remove",
        min_length=1,
        max_length=63,
    )
    confirm: bool = Field(
        default=False,
        description="Confirm removal (required)",
    )


class RemoveClusterOutput(BaseModel):
    """Output for remove_cluster tool."""

    success: bool = Field(description="Whether removal was successful")
    removed_cluster_id: str = Field(description="ID of removed cluster")
    message: str = Field(description="Status message")
    remaining_clusters: int = Field(description="Number of remaining clusters")


class LockClusterInput(BaseModel):
    """Input for lock_cluster tool."""

    cluster_id: str | None = Field(
        default=None,
        description="Cluster to lock (defaults to active)",
    )
    lock: bool = Field(
        default=True,
        description="True to lock, False to unlock",
    )


class LockClusterOutput(BaseModel):
    """Output for lock_cluster tool."""

    success: bool = Field(description="Whether operation succeeded")
    cluster_id: str = Field(description="Affected cluster")
    is_locked: bool = Field(description="Current lock state")
    message: str = Field(description="Status message")


# Rebuild models with forward references (datetime) to resolve type annotations
# This is required because `from __future__ import annotations` makes all
# annotations into strings, and Pydantic needs explicit rebuilding to resolve them.
ClusterInfo.model_rebuild()
CurrentClusterOutput.model_rebuild()
