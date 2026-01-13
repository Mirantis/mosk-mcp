"""Get migration ETA tool.

This module provides the get_migration_eta tool that calculates
estimated completion times for active VM migrations based on
current transfer rates and remaining data.

Safety Level: READ_ONLY
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from mosk_mcp.adapters.openstack import (
    MigrationStatus as OSMigrationStatus,
)
from mosk_mcp.adapters.openstack import (
    OpenStackAdapter,
    ServerMigration,
)
from mosk_mcp.core.exceptions import ToolExecutionError
from mosk_mcp.observability.logging import get_logger
from mosk_mcp.tools.operations_visibility.models import (
    GetMigrationETAInput,
    GetMigrationETAOutput,
    MigrationStatus,
    VMMigrationETA,
)


if TYPE_CHECKING:
    from mosk_mcp.adapters.kubernetes import KubernetesAdapter


logger = get_logger(__name__)


# Default transfer rates for estimation when actual rate unknown
DEFAULT_TRANSFER_RATE_MBPS = 500  # 500 Mbps default assumption


def _os_status_to_model_status(os_status: OSMigrationStatus) -> MigrationStatus:
    """Convert OpenStack migration status to model status."""
    mapping = {
        OSMigrationStatus.QUEUED: MigrationStatus.QUEUED,
        OSMigrationStatus.PREPARING: MigrationStatus.PREPARING,
        OSMigrationStatus.RUNNING: MigrationStatus.RUNNING,
        OSMigrationStatus.POST_MIGRATING: MigrationStatus.POST_MIGRATING,
        OSMigrationStatus.COMPLETED: MigrationStatus.COMPLETED,
        OSMigrationStatus.FAILED: MigrationStatus.FAILED,
        OSMigrationStatus.CANCELLED: MigrationStatus.CANCELLED,
        OSMigrationStatus.ERROR: MigrationStatus.ERROR,
        OSMigrationStatus.ACCEPTED: MigrationStatus.QUEUED,
        OSMigrationStatus.PRE_MIGRATING: MigrationStatus.PREPARING,
    }
    return mapping.get(os_status, MigrationStatus.ERROR)


async def _get_active_migrations(
    kubernetes_adapter: KubernetesAdapter,
    source_host: str | None,
) -> list[ServerMigration]:
    """Get active migrations from Nova API via OpenStack adapter.

    Args:
        kubernetes_adapter: Kubernetes client adapter.
        source_host: Optional source host filter.

    Returns:
        List of active ServerMigration objects.
    """
    active_statuses = [
        OSMigrationStatus.QUEUED,
        OSMigrationStatus.PREPARING,
        OSMigrationStatus.RUNNING,
        OSMigrationStatus.POST_MIGRATING,
        OSMigrationStatus.ACCEPTED,
        OSMigrationStatus.PRE_MIGRATING,
    ]

    async with OpenStackAdapter(kubernetes_adapter) as os_adapter:
        all_migrations = await os_adapter.list_migrations(
            source_compute=source_host,
            limit=200,
        )

        # Filter for active migrations only
        filtered = [m for m in all_migrations if m.status in active_statuses]

        return filtered


def _calculate_eta_for_migration(
    migration: ServerMigration,
    average_rate_bps: int | None,
) -> VMMigrationETA:
    """Calculate ETA for a single migration.

    Args:
        migration: ServerMigration from OpenStack adapter.
        average_rate_bps: Average cluster transfer rate if available.

    Returns:
        VMMigrationETA with calculated estimates.
    """
    status = _os_status_to_model_status(migration.status)

    # Calculate progress
    memory_total = migration.memory_total_bytes
    memory_processed = migration.memory_processed_bytes
    disk_total = migration.disk_total_bytes
    disk_processed = migration.disk_processed_bytes

    total_bytes = memory_total + disk_total
    processed_bytes = memory_processed + disk_processed
    remaining_bytes = migration.memory_remaining_bytes + migration.disk_remaining_bytes

    progress = int(processed_bytes / total_bytes * 100) if total_bytes > 0 else 0

    # Get transfer rate - use average if actual not available
    rate_bps = average_rate_bps or (DEFAULT_TRANSFER_RATE_MBPS * 1_000_000)
    rate_mbps = rate_bps / 1_000_000

    # Calculate remaining time
    estimated_remaining_seconds = None
    estimated_completion = None

    if rate_bps > 0 and remaining_bytes > 0:
        estimated_remaining_seconds = int(remaining_bytes / (rate_bps / 8))
        completion_time = datetime.now(UTC) + timedelta(seconds=estimated_remaining_seconds)
        estimated_completion = completion_time.isoformat()
    elif status == MigrationStatus.QUEUED:
        # Estimate based on total size for queued migrations
        if rate_bps > 0 and total_bytes > 0:
            estimated_remaining_seconds = int(total_bytes / (rate_bps / 8))
            completion_time = datetime.now(UTC) + timedelta(seconds=estimated_remaining_seconds)
            estimated_completion = completion_time.isoformat()

    return VMMigrationETA(
        vm_id=migration.server_uuid,
        vm_name=migration.server_name,
        status=status,
        progress_percent=progress,
        estimated_remaining_seconds=estimated_remaining_seconds,
        estimated_completion=estimated_completion,
        transfer_rate_mbps=rate_mbps if rate_bps else None,
    )


async def get_migration_eta(
    kubernetes_adapter: KubernetesAdapter,
    input_data: GetMigrationETAInput,
) -> GetMigrationETAOutput:
    """Get estimated completion time for migrations.

    Calculates ETAs for active migrations based on transfer rates
    and remaining data, providing overall and per-VM estimates.

    Args:
        kubernetes_adapter: Kubernetes client adapter.
        input_data: Input parameters.

    Returns:
        Migration ETA information.

    Raises:
        ToolExecutionError: If operation fails.
    """
    logger.info(
        "get_migration_eta_start",
        source_host=input_data.source_host,
    )

    try:
        # Get active migrations
        migrations = await _get_active_migrations(kubernetes_adapter, input_data.source_host)

        if not migrations:
            return GetMigrationETAOutput(
                has_active_migrations=False,
                total_active=0,
                total_queued=0,
                overall_progress_percent=100,
                estimated_total_remaining_seconds=None,
                estimated_total_completion=None,
                average_transfer_rate_mbps=None,
                per_vm_eta=[],
                bottleneck_host=None,
                recommendations=[],
                timestamp=datetime.now(UTC).isoformat(),
            )

        # Use default transfer rate (Nova doesn't expose per-migration rates easily)
        average_rate_bps = DEFAULT_TRANSFER_RATE_MBPS * 1_000_000
        average_rate_mbps = float(DEFAULT_TRANSFER_RATE_MBPS)

        # Calculate per-VM ETAs
        per_vm_eta: list[VMMigrationETA] = []
        if input_data.include_per_vm:
            for m in migrations:
                eta = _calculate_eta_for_migration(m, average_rate_bps)
                per_vm_eta.append(eta)

        # Calculate overall statistics
        active_statuses = [
            OSMigrationStatus.RUNNING,
            OSMigrationStatus.PREPARING,
            OSMigrationStatus.POST_MIGRATING,
            OSMigrationStatus.PRE_MIGRATING,
        ]
        queued_statuses = [OSMigrationStatus.QUEUED, OSMigrationStatus.ACCEPTED]

        total_active = sum(1 for m in migrations if m.status in active_statuses)
        total_queued = sum(1 for m in migrations if m.status in queued_statuses)

        # Calculate overall progress
        total_bytes = sum(m.memory_total_bytes + m.disk_total_bytes for m in migrations)
        processed_bytes = sum(m.memory_processed_bytes + m.disk_processed_bytes for m in migrations)
        overall_progress = int(processed_bytes / total_bytes * 100) if total_bytes > 0 else 0

        # Calculate total remaining time
        remaining_bytes = sum(m.memory_remaining_bytes + m.disk_remaining_bytes for m in migrations)
        use_rate = average_rate_bps

        if remaining_bytes > 0 and use_rate > 0:
            # Account for concurrent migrations (simplified model)
            concurrent_factor = max(1, total_active)
            effective_rate = use_rate / concurrent_factor
            total_remaining_seconds = int(remaining_bytes / (effective_rate / 8))
            total_completion = datetime.now(UTC) + timedelta(seconds=total_remaining_seconds)
            estimated_total_completion = total_completion.isoformat()
        else:
            total_remaining_seconds = None
            estimated_total_completion = None

        # Find bottleneck host
        source_counts: dict[str, int] = {}
        for m in migrations:
            host = m.source_compute or "unknown"
            source_counts[host] = source_counts.get(host, 0) + 1
        bottleneck_host = (
            max(source_counts, key=lambda h: source_counts.get(h, 0)) if source_counts else None
        )

        # Generate recommendations
        recommendations: list[str] = []
        if total_active > 3:
            recommendations.append(
                f"Consider reducing concurrent migrations (currently {total_active} active)"
            )
        if average_rate_mbps and average_rate_mbps < 200:
            recommendations.append("Transfer rate is below optimal; check network bandwidth")
        if total_queued > 5:
            recommendations.append(
                f"{total_queued} migrations queued; consider scheduling during low-traffic periods"
            )
        if bottleneck_host and source_counts.get(bottleneck_host, 0) > 2:
            recommendations.append(
                f"Host {bottleneck_host} is a bottleneck with {source_counts[bottleneck_host]} migrations"
            )

        result = GetMigrationETAOutput(
            has_active_migrations=True,
            total_active=total_active,
            total_queued=total_queued,
            overall_progress_percent=overall_progress,
            estimated_total_remaining_seconds=total_remaining_seconds,
            estimated_total_completion=estimated_total_completion,
            average_transfer_rate_mbps=average_rate_mbps,
            per_vm_eta=per_vm_eta,
            bottleneck_host=bottleneck_host,
            recommendations=recommendations,
            timestamp=datetime.now(UTC).isoformat(),
        )

        logger.info(
            "get_migration_eta_complete",
            total_active=total_active,
            total_queued=total_queued,
            overall_progress=overall_progress,
        )

        return result

    except Exception as e:
        logger.error(
            "get_migration_eta_error",
            error=str(e),
        )
        raise ToolExecutionError(
            f"Failed to get migration ETA: {e}",
            tool_name="get_migration_eta",
        ) from e
