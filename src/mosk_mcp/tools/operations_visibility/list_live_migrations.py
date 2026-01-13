"""List live migrations tool.

This module provides the list_live_migrations tool that retrieves
active VM migrations from Nova, including source/target hosts and progress.

Safety Level: READ_ONLY
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from mosk_mcp.adapters.openstack import (
    MigrationStatus as OSMigrationStatus,
)
from mosk_mcp.adapters.openstack import (
    MigrationType,
    OpenStackAdapter,
    ServerMigration,
)
from mosk_mcp.core.exceptions import ToolExecutionError
from mosk_mcp.observability.logging import get_logger
from mosk_mcp.tools.operations_visibility.models import (
    ListLiveMigrationsInput,
    ListLiveMigrationsOutput,
    LiveMigrationInfo,
    MigrationStatus,
)


if TYPE_CHECKING:
    from mosk_mcp.adapters.kubernetes import KubernetesAdapter


logger = get_logger(__name__)


def _os_status_to_model_status(os_status: OSMigrationStatus) -> MigrationStatus:
    """Convert OpenStack migration status to model status.

    Args:
        os_status: OpenStack migration status.

    Returns:
        Model migration status.
    """
    mapping = {
        OSMigrationStatus.QUEUED: MigrationStatus.QUEUED,
        OSMigrationStatus.PREPARING: MigrationStatus.PREPARING,
        OSMigrationStatus.RUNNING: MigrationStatus.RUNNING,
        OSMigrationStatus.POST_MIGRATING: MigrationStatus.POST_MIGRATING,
        OSMigrationStatus.COMPLETED: MigrationStatus.COMPLETED,
        OSMigrationStatus.FAILED: MigrationStatus.FAILED,
        OSMigrationStatus.CANCELLED: MigrationStatus.CANCELLED,
        OSMigrationStatus.ERROR: MigrationStatus.ERROR,
        OSMigrationStatus.ACCEPTED: MigrationStatus.QUEUED,  # Map to queued
        OSMigrationStatus.PRE_MIGRATING: MigrationStatus.PREPARING,  # Map to preparing
    }
    return mapping.get(os_status, MigrationStatus.ERROR)


async def _get_nova_migrations_from_api(
    kubernetes_adapter: KubernetesAdapter,
    input_data: ListLiveMigrationsInput,
) -> list[ServerMigration]:
    """Get migrations from Nova API via OpenStack adapter.

    Args:
        kubernetes_adapter: Kubernetes client adapter.
        input_data: Filter parameters.

    Returns:
        List of ServerMigration objects from Nova API.
    """
    async with OpenStackAdapter(kubernetes_adapter) as os_adapter:
        # Map model status to OpenStack status for filtering
        os_status = None
        if input_data.status_filter:
            status_mapping = {
                MigrationStatus.QUEUED: OSMigrationStatus.QUEUED,
                MigrationStatus.PREPARING: OSMigrationStatus.PREPARING,
                MigrationStatus.RUNNING: OSMigrationStatus.RUNNING,
                MigrationStatus.POST_MIGRATING: OSMigrationStatus.POST_MIGRATING,
                MigrationStatus.COMPLETED: OSMigrationStatus.COMPLETED,
                MigrationStatus.FAILED: OSMigrationStatus.FAILED,
                MigrationStatus.CANCELLED: OSMigrationStatus.CANCELLED,
                MigrationStatus.ERROR: OSMigrationStatus.ERROR,
            }
            os_status = status_mapping.get(input_data.status_filter)

        migrations = await os_adapter.list_migrations(
            status=os_status,
            migration_type=MigrationType.LIVE_MIGRATION,
            source_compute=input_data.source_host,
            dest_compute=input_data.target_host,
            limit=input_data.limit,
        )

        # Filter completed if needed
        if not input_data.include_completed:
            migrations = [
                m
                for m in migrations
                if m.status
                not in (
                    OSMigrationStatus.COMPLETED,
                    OSMigrationStatus.FAILED,
                    OSMigrationStatus.CANCELLED,
                )
            ]

        return migrations


def _parse_migration(migration: ServerMigration) -> LiveMigrationInfo:
    """Parse ServerMigration into LiveMigrationInfo.

    Args:
        migration: Migration data from OpenStack adapter.

    Returns:
        Parsed LiveMigrationInfo object.
    """
    status = _os_status_to_model_status(migration.status)

    # Calculate progress
    memory_total = migration.memory_total_bytes
    memory_processed = migration.memory_processed_bytes
    disk_total = migration.disk_total_bytes
    disk_processed = migration.disk_processed_bytes

    total_bytes = memory_total + disk_total
    processed_bytes = memory_processed + disk_processed

    progress = int(processed_bytes / total_bytes * 100) if total_bytes > 0 else 0

    return LiveMigrationInfo(
        migration_id=migration.id,
        vm_id=migration.server_uuid,
        vm_name=migration.server_name,
        source_host=migration.source_compute,
        target_host=migration.dest_compute if migration.dest_compute else None,
        status=status,
        migration_type=migration.migration_type.value,
        created_at=migration.created_at.isoformat() if migration.created_at else "",
        updated_at=migration.updated_at.isoformat() if migration.updated_at else "",
        memory_total_bytes=memory_total if memory_total > 0 else None,
        memory_processed_bytes=memory_processed if memory_processed > 0 else None,
        memory_remaining_bytes=migration.memory_remaining_bytes
        if migration.memory_remaining_bytes > 0
        else None,
        disk_total_bytes=disk_total if disk_total > 0 else None,
        disk_processed_bytes=disk_processed if disk_processed > 0 else None,
        progress_percent=progress,
        error_message=None,
    )


async def list_live_migrations(
    kubernetes_adapter: KubernetesAdapter,
    input_data: ListLiveMigrationsInput,
) -> ListLiveMigrationsOutput:
    """List active VM live migrations.

    Retrieves information about Nova live migrations including
    source/target hosts, progress, and status.

    Args:
        kubernetes_adapter: Kubernetes client adapter.
        input_data: Filter parameters.

    Returns:
        List of live migrations with summary statistics.

    Raises:
        ToolExecutionError: If operation fails.
    """
    logger.info(
        "list_live_migrations_start",
        source_host=input_data.source_host,
        target_host=input_data.target_host,
        status_filter=input_data.status_filter.value if input_data.status_filter else None,
    )

    try:
        # Get migrations from Nova API
        raw_migrations = await _get_nova_migrations_from_api(kubernetes_adapter, input_data)

        # Parse migrations
        migrations = [_parse_migration(m) for m in raw_migrations]

        # Calculate statistics
        active_count = sum(
            1
            for m in migrations
            if m.status
            in (MigrationStatus.RUNNING, MigrationStatus.PREPARING, MigrationStatus.POST_MIGRATING)
        )
        queued_count = sum(1 for m in migrations if m.status == MigrationStatus.QUEUED)
        completed_count = sum(1 for m in migrations if m.status == MigrationStatus.COMPLETED)
        failed_count = sum(
            1 for m in migrations if m.status in (MigrationStatus.FAILED, MigrationStatus.ERROR)
        )

        # Count by host
        by_source: dict[str, int] = {}
        by_target: dict[str, int] = {}
        for m in migrations:
            by_source[m.source_host] = by_source.get(m.source_host, 0) + 1
            if m.target_host:
                by_target[m.target_host] = by_target.get(m.target_host, 0) + 1

        result = ListLiveMigrationsOutput(
            migrations=migrations,
            total_count=len(migrations),
            active_count=active_count,
            queued_count=queued_count,
            completed_count=completed_count,
            failed_count=failed_count,
            by_source_host=by_source,
            by_target_host=by_target,
            timestamp=datetime.now(UTC).isoformat(),
        )

        logger.info(
            "list_live_migrations_complete",
            total=len(migrations),
            active=active_count,
            queued=queued_count,
        )

        return result

    except Exception as e:
        logger.error(
            "list_live_migrations_error",
            error=str(e),
        )
        raise ToolExecutionError(
            f"Failed to list live migrations: {e}",
            tool_name="list_live_migrations",
        ) from e
