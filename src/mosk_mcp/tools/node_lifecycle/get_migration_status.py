"""Get migration status tool for MOSK MCP Server.

This module provides the get_migration_status tool for tracking
the progress of Nova live migrations.

Safety Level: READ_ONLY
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from mosk_mcp.adapters.openstack import (
    OpenStackAdapter,
    ServerMigration,
)
from mosk_mcp.auth.rbac import ToolSafetyLevel
from mosk_mcp.core.exceptions import KubernetesError
from mosk_mcp.observability.audit import AuditLevel
from mosk_mcp.observability.logging import get_logger
from mosk_mcp.tools.common import audit_tool_execution
from mosk_mcp.tools.common.enums import MigrationStatus


if TYPE_CHECKING:
    from mosk_mcp.adapters.kubernetes import KubernetesAdapter
    from mosk_mcp.auth.types import UserContext
    from mosk_mcp.observability.audit import AuditLogger


logger = get_logger(__name__)

# Tool metadata
TOOL_NAME = "get_migration_status"
TOOL_SAFETY_LEVEL = ToolSafetyLevel.READ_ONLY
TOOL_DESCRIPTION = (
    "Track the progress of Nova live migrations. "
    "Can query specific migrations or list migrations for a host."
)


class GetMigrationStatusInput(BaseModel):
    """Input parameters for get_migration_status tool.

    Attributes:
        migration_id: Specific migration ID to query.
        vm_id: Query migrations for a specific VM.
        source_host: Query migrations from a source host.
        target_host: Query migrations to a target host.
        status_filter: Filter by migration status.
        limit: Maximum number of migrations to return.
        include_completed: Include completed migrations.
    """

    migration_id: str | None = Field(
        default=None,
        description="Specific migration ID to query",
    )
    vm_id: str | None = Field(
        default=None,
        description="Query migrations for a specific VM UUID",
    )
    source_host: str | None = Field(
        default=None,
        description="Query migrations from a source compute host",
    )
    target_host: str | None = Field(
        default=None,
        description="Query migrations to a target compute host",
    )
    status_filter: MigrationStatus | None = Field(
        default=None,
        description="Filter by migration status",
    )
    limit: int = Field(
        default=50,
        ge=1,
        le=200,
        description="Maximum number of migrations to return",
    )
    include_completed: bool = Field(
        default=True,
        description="Include completed and failed migrations",
    )


class MigrationInfo(BaseModel):
    """Information about a single migration.

    Attributes:
        migration_id: Migration ID.
        vm_id: VM UUID.
        vm_name: VM name.
        source_host: Source compute host.
        target_host: Target compute host.
        status: Migration status.
        migration_type: Type of migration.
        progress_percent: Migration progress (0-100).
        memory_total_bytes: Total memory to migrate.
        memory_remaining_bytes: Memory remaining to migrate.
        disk_total_bytes: Total disk to migrate.
        disk_remaining_bytes: Disk remaining to migrate.
        created_at: Migration creation time.
        updated_at: Last update time.
        error_message: Error message if failed.
        details: Additional migration details.
    """

    migration_id: str = Field(..., description="Migration ID")
    vm_id: str = Field(..., description="VM UUID")
    vm_name: str = Field(default="unknown", description="VM name")
    source_host: str = Field(..., description="Source compute host")
    target_host: str | None = Field(None, description="Target compute host")
    status: MigrationStatus = Field(..., description="Migration status")
    migration_type: str = Field(default="live-migration", description="Migration type")
    progress_percent: int = Field(default=0, ge=0, le=100, description="Progress percentage")
    memory_total_bytes: int | None = Field(None, description="Total memory to migrate")
    memory_remaining_bytes: int | None = Field(None, description="Memory remaining")
    disk_total_bytes: int | None = Field(None, description="Total disk to migrate")
    disk_remaining_bytes: int | None = Field(None, description="Disk remaining")
    created_at: str | None = Field(None, description="Creation time")
    updated_at: str | None = Field(None, description="Last update time")
    error_message: str | None = Field(None, description="Error message")
    details: dict[str, Any] = Field(default_factory=dict, description="Additional details")


class GetMigrationStatusOutput(BaseModel):
    """Output from get_migration_status tool.

    Attributes:
        migrations: List of migration info.
        total_count: Total migrations found.
        active_count: Number of active migrations.
        completed_count: Number of completed migrations.
        failed_count: Number of failed migrations.
        summary: Summary statistics.
        message: Result message.
        simulated: Whether data is simulated (no OpenStack connection).
    """

    migrations: list[MigrationInfo] = Field(
        default_factory=list, description="Migration information"
    )
    total_count: int = Field(..., description="Total migrations found")
    active_count: int = Field(default=0, description="Active migrations")
    completed_count: int = Field(default=0, description="Completed migrations")
    failed_count: int = Field(default=0, description="Failed migrations")
    summary: dict[str, Any] = Field(default_factory=dict, description="Summary statistics")
    message: str = Field(..., description="Result message")
    simulated: bool = Field(default=False, description="Whether data is simulated")


async def _get_migrations_from_nova(
    kubernetes_adapter: KubernetesAdapter,
    input_data: GetMigrationStatusInput,
) -> list[ServerMigration]:
    """Query migrations from Nova API via OpenStack adapter.

    Args:
        kubernetes_adapter: Kubernetes client adapter.
        input_data: Query parameters.

    Returns:
        List of ServerMigration objects from Nova.
    """
    logger.info(
        "querying_migrations",
        migration_id=input_data.migration_id,
        vm_id=input_data.vm_id,
        source_host=input_data.source_host,
    )

    async with OpenStackAdapter(kubernetes_adapter) as os_adapter:
        migrations = await os_adapter.list_migrations(
            status=input_data.status_filter,
            source_compute=input_data.source_host,
            dest_compute=input_data.target_host,
            limit=input_data.limit,
        )

        # Filter by migration_id if specified
        if input_data.migration_id:
            migrations = [m for m in migrations if m.id == input_data.migration_id]

        # Filter by vm_id if specified
        if input_data.vm_id:
            migrations = [m for m in migrations if m.server_uuid == input_data.vm_id]

        # Filter completed if needed
        if not input_data.include_completed:
            migrations = [
                m
                for m in migrations
                if m.status
                not in (
                    MigrationStatus.COMPLETED,
                    MigrationStatus.FAILED,
                    MigrationStatus.CANCELLED,
                )
            ]

        return migrations


def _parse_migration(migration: ServerMigration) -> MigrationInfo:
    """Parse ServerMigration into MigrationInfo.

    Args:
        migration: Migration data from OpenStack adapter.

    Returns:
        Parsed MigrationInfo object.
    """
    # Map ACCEPTED and PRE_MIGRATING to standard statuses for display
    status = migration.status
    if status == MigrationStatus.ACCEPTED:
        status = MigrationStatus.QUEUED
    elif status == MigrationStatus.PRE_MIGRATING:
        status = MigrationStatus.PREPARING

    # Calculate progress
    memory_total = migration.memory_total_bytes
    memory_processed = migration.memory_processed_bytes
    disk_total = migration.disk_total_bytes
    disk_processed = migration.disk_processed_bytes

    total_bytes = memory_total + disk_total
    processed_bytes = memory_processed + disk_processed

    progress = int(processed_bytes / total_bytes * 100) if total_bytes > 0 else 0

    return MigrationInfo(
        migration_id=migration.id,
        vm_id=migration.server_uuid,
        vm_name=migration.server_name or "unknown",
        source_host=migration.source_compute or "unknown",
        target_host=migration.dest_compute,
        status=status,
        migration_type=migration.migration_type.value,
        progress_percent=progress,
        memory_total_bytes=memory_total if memory_total > 0 else None,
        memory_remaining_bytes=migration.memory_remaining_bytes
        if migration.memory_remaining_bytes > 0
        else None,
        disk_total_bytes=disk_total if disk_total > 0 else None,
        disk_remaining_bytes=migration.disk_remaining_bytes
        if migration.disk_remaining_bytes > 0
        else None,
        created_at=migration.created_at.isoformat() if migration.created_at else None,
        updated_at=migration.updated_at.isoformat() if migration.updated_at else None,
        error_message=None,
        details={},
    )


async def get_migration_status(
    k8s_adapter: KubernetesAdapter,
    input_data: GetMigrationStatusInput,
    context: UserContext | None = None,
    audit_logger: AuditLogger | None = None,
) -> GetMigrationStatusOutput:
    """Get the status of Nova live migrations.

    This tool queries Nova for migration status and progress. It can:
    - Query a specific migration by ID
    - List migrations for a specific VM
    - List migrations from/to a specific host
    - Filter by migration status

    Args:
        k8s_adapter: Kubernetes adapter for API operations.
        input_data: Query parameters for migrations.
        context: User context for RBAC (optional).
        audit_logger: Audit logger for tracking operations (optional).

    Returns:
        GetMigrationStatusOutput with migration information.

    Raises:
        KubernetesError: If API calls fail.

    Example:
        >>> async with KubernetesAdapter() as k8s:
        ...     result = await get_migration_status(
        ...         k8s, GetMigrationStatusInput(source_host="compute-01")
        ...     )
        ...     for mig in result.migrations:
        ...         print(f"{mig.vm_name}: {mig.status} ({mig.progress_percent}%)")
    """
    logger.info(
        "getting_migration_status",
        migration_id=input_data.migration_id,
        vm_id=input_data.vm_id,
        source_host=input_data.source_host,
    )

    async with audit_tool_execution(
        TOOL_NAME,
        audit_logger,
        context,
        AuditLevel.READ,
        {
            "migration_id": input_data.migration_id,
            "vm_id": input_data.vm_id,
            "source_host": input_data.source_host,
        },
    ) as audit_details:
        try:
            # Query real migrations from Nova via OpenStack adapter
            raw_migrations = await _get_migrations_from_nova(
                k8s_adapter,
                input_data,
            )

            # Convert to MigrationInfo objects
            migrations = [_parse_migration(m) for m in raw_migrations]

            # Apply limit
            migrations = migrations[: input_data.limit]

            # Calculate counts
            total_count = len(migrations)
            active_count = sum(
                1
                for m in migrations
                if m.status
                in (
                    MigrationStatus.QUEUED,
                    MigrationStatus.PREPARING,
                    MigrationStatus.RUNNING,
                    MigrationStatus.POST_MIGRATING,
                )
            )
            completed_count = sum(1 for m in migrations if m.status == MigrationStatus.COMPLETED)
            failed_count = sum(
                1
                for m in migrations
                if m.status
                in (
                    MigrationStatus.FAILED,
                    MigrationStatus.ERROR,
                    MigrationStatus.CANCELLED,
                )
            )

            # Generate summary
            by_status: dict[str, int] = {}
            for mig in migrations:
                status = mig.status.value
                by_status[status] = by_status.get(status, 0) + 1

            summary: dict[str, Any] = {
                "total": total_count,
                "active": active_count,
                "completed": completed_count,
                "failed": failed_count,
                "by_status": by_status,
            }

            # Generate message
            message = (
                f"Found {total_count} migration(s): "
                f"{active_count} active, {completed_count} completed, {failed_count} failed"
            )

            output = GetMigrationStatusOutput(
                migrations=migrations,
                total_count=total_count,
                active_count=active_count,
                completed_count=completed_count,
                failed_count=failed_count,
                summary=summary,
                message=message,
                simulated=False,
            )

            logger.info(
                "migration_status_retrieved",
                total_count=total_count,
                active_count=active_count,
            )

            # Update audit details
            audit_details["total_count"] = total_count
            audit_details["active_count"] = active_count

            return output

        except Exception as e:
            logger.error(
                "get_migration_status_failed",
                error=str(e),
            )

            if isinstance(e, KubernetesError):
                raise
            raise KubernetesError(
                f"Failed to get migration status: {e}",
                operation="get",
                resource_kind="Migration",
            ) from e
