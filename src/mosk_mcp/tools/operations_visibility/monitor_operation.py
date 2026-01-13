"""Monitor operation tool for tracking long-running MOSK operations.

This module provides the monitor_operation MCP tool that polls operation
progress and returns accumulated snapshots. Supports monitoring:
- Node add (provisioning): Uses MCC kubeconfig
- OpenStack upgrade: Uses MOSK kubeconfig
- MOSK platform upgrade: Uses MCC kubeconfig

The tool polls for a fixed 5-minute window (configurable) at 30-second
intervals, returning progress snapshots collected during that period.

Safety Level: READ_ONLY
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from enum import Enum
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from mosk_mcp.auth.rbac import ToolSafetyLevel
from mosk_mcp.core.exceptions import ToolExecutionError, ValidationError
from mosk_mcp.observability.logging import get_logger
from mosk_mcp.tools.operations_visibility.monitors.mosk_upgrade_monitor import (
    MoskUpgradeMonitor,
)
from mosk_mcp.tools.operations_visibility.monitors.node_add_monitor import (
    NodeAddMonitor,
)
from mosk_mcp.tools.operations_visibility.monitors.openstack_upgrade_monitor import (
    OpenStackUpgradeMonitor,
)
from mosk_mcp.tools.operations_visibility.monitors.base import (
    ProgressSnapshot,
)


if TYPE_CHECKING:
    from mosk_mcp.adapters.kubernetes import KubernetesAdapter
    from mosk_mcp.tools.operations_visibility.monitors.base import (
        BaseOperationMonitor,
    )


logger = get_logger(__name__)


# Tool metadata
TOOL_NAME = "monitor_operation"
TOOL_SAFETY_LEVEL = ToolSafetyLevel.READ_ONLY
TOOL_DESCRIPTION = (
    "Monitor long-running MOSK operations with periodic progress updates. "
    "Polls for up to 5 minutes at 30-second intervals, returning accumulated progress snapshots. "
    "Supports: node_add (MCC cluster), openstack_upgrade (MOSK cluster), mosk_upgrade (MCC cluster)."
)

# Polling configuration
DEFAULT_POLL_INTERVAL_SECONDS = 30
DEFAULT_MAX_DURATION_SECONDS = 300  # 5 minutes
MAX_POLL_INTERVAL_SECONDS = 300  # 5 minutes max between polls
MAX_DURATION_SECONDS = 1800  # 30 minutes max monitoring window
MAX_SNAPSHOTS = 60  # Safety limit (supports longer windows)


class OperationType(str, Enum):
    """Supported operation types for monitoring."""

    NODE_ADD = "node_add"
    OPENSTACK_UPGRADE = "openstack_upgrade"
    MOSK_UPGRADE = "mosk_upgrade"


class OperationStatus(str, Enum):
    """Overall operation status."""

    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    NOT_FOUND = "not_found"


class MonitorOperationInput(BaseModel):
    """Input parameters for monitor_operation tool.

    Attributes:
        operation_type: Type of operation to monitor.
        target: Resource name (node name for node_add, OSDPL name for upgrade).
        namespace: Optional namespace override. Auto-discovered if not provided.
        poll_interval_seconds: Interval between progress checks (default 30s).
        max_duration_seconds: Maximum monitoring window (default 300s/5min).
    """

    operation_type: OperationType = Field(
        ...,
        description="Type of operation to monitor (node_add, openstack_upgrade, mosk_upgrade)",
    )
    target: str = Field(
        ...,
        description="Resource name to monitor (node name, OSDPL name, or Cluster name)",
        min_length=1,
        max_length=253,
    )
    namespace: str | None = Field(
        default=None,
        description="Kubernetes namespace. Auto-discovered if not provided.",
    )
    poll_interval_seconds: int = Field(
        default=DEFAULT_POLL_INTERVAL_SECONDS,
        description="Seconds between progress checks (default 30, max 300)",
        ge=10,
        le=MAX_POLL_INTERVAL_SECONDS,
    )
    max_duration_seconds: int = Field(
        default=DEFAULT_MAX_DURATION_SECONDS,
        description="Maximum monitoring window in seconds (default 300, max 1800)",
        ge=30,
        le=MAX_DURATION_SECONDS,
    )


class MonitorOperationOutput(BaseModel):
    """Output from monitor_operation tool.

    Attributes:
        operation_type: Type of operation being monitored.
        target: Resource name being monitored.
        namespace: Resolved namespace.
        status: Overall operation status.
        overall_progress_percent: Current progress percentage (0-100).
        current_phase: Current phase of the operation.
        phase_message: Human-readable description of current phase.
        progress_snapshots: Array of progress snapshots collected during polling.
        continue_monitoring: True if operation not yet complete (call again).
        error_message: Error message if operation failed.
        started_at: When the operation started (if known).
        completed_at: When the operation completed (if done).
        elapsed_seconds: Time elapsed since operation started.
        estimated_remaining_seconds: Estimated time to completion (if calculable).
        estimated_remaining_human: Human-readable ETA (e.g., "~1 hour 30 minutes").
        next_check_recommended: ISO timestamp for recommended next check.
        services_completed: Number of services completed (for upgrades).
        services_total: Total number of services (for upgrades).
        services_in_progress: List of services currently upgrading.
        polling_duration_seconds: How long the tool polled for.
        snapshots_collected: Number of snapshots in this response.
    """

    operation_type: OperationType = Field(..., description="Operation type")
    target: str = Field(..., description="Resource name")
    namespace: str = Field(..., description="Kubernetes namespace")
    status: OperationStatus = Field(..., description="Overall status")
    overall_progress_percent: int = Field(..., description="Progress percentage", ge=-1, le=100)
    current_phase: str = Field(..., description="Current phase")
    phase_message: str = Field(default="", description="Human-readable phase description")
    progress_snapshots: list[ProgressSnapshot] = Field(
        default_factory=list, description="Progress snapshots collected"
    )
    continue_monitoring: bool = Field(
        ..., description="Whether to continue monitoring (call again)"
    )
    error_message: str | None = Field(None, description="Error message if failed")
    started_at: str | None = Field(None, description="When operation started")
    completed_at: str | None = Field(None, description="When operation completed")
    elapsed_seconds: int | None = Field(None, description="Seconds elapsed since start")
    estimated_remaining_seconds: int | None = Field(
        None, description="Estimated seconds to completion"
    )
    estimated_remaining_human: str | None = Field(
        None, description="Human-readable ETA (e.g., '~1 hour 30 minutes')"
    )
    next_check_recommended: str | None = Field(
        None, description="ISO timestamp for recommended next check"
    )
    services_completed: int | None = Field(
        None, description="Number of services completed (OpenStack upgrades)"
    )
    services_total: int | None = Field(None, description="Total services (OpenStack upgrades)")
    services_in_progress: list[str] | None = Field(
        None, description="Services currently upgrading (OpenStack upgrades)"
    )
    machines_completed: int | None = Field(
        None, description="Number of machines completed (MOSK platform upgrades)"
    )
    machines_total: int | None = Field(None, description="Total machines (MOSK platform upgrades)")
    machines_in_progress: list[dict[str, Any]] | None = Field(
        None, description="Machines currently upgrading with progress (MOSK platform upgrades)"
    )
    polling_duration_seconds: int = Field(..., description="How long polling lasted")
    snapshots_collected: int = Field(..., description="Number of snapshots")


def _format_duration_human(seconds: int) -> str:
    """Format seconds into human-readable duration.

    Args:
        seconds: Duration in seconds.

    Returns:
        Human-readable string like "~1 hour 30 minutes".
    """
    if seconds < 60:
        return f"~{seconds} seconds"
    elif seconds < 3600:
        minutes = seconds // 60
        return f"~{minutes} minute{'s' if minutes != 1 else ''}"
    else:
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        if minutes > 0:
            return f"~{hours} hour{'s' if hours != 1 else ''} {minutes} minute{'s' if minutes != 1 else ''}"
        return f"~{hours} hour{'s' if hours != 1 else ''}"


def _calculate_eta(
    snapshots: list[ProgressSnapshot],
    current_progress: int,
    elapsed_seconds: int | None,
) -> tuple[int | None, str | None]:
    """Calculate estimated time remaining based on progress rate.

    Uses progress rate from snapshots to estimate remaining time.
    Falls back to elapsed time extrapolation if snapshots are insufficient.

    Args:
        snapshots: List of collected progress snapshots.
        current_progress: Current progress percentage.
        elapsed_seconds: Seconds elapsed since operation start.

    Returns:
        Tuple of (estimated_seconds, human_readable_string).
    """
    if current_progress <= 0 or current_progress >= 100:
        return None, None

    # Try to calculate from snapshot progress rate
    if len(snapshots) >= 2:
        first = snapshots[0]
        last = snapshots[-1]
        progress_delta = last.progress_percent - first.progress_percent

        if progress_delta > 0:
            # Calculate time between first and last snapshot
            try:
                first_time = datetime.fromisoformat(first.timestamp.replace("Z", "+00:00"))
                last_time = datetime.fromisoformat(last.timestamp.replace("Z", "+00:00"))
                time_delta = (last_time - first_time).total_seconds()

                if time_delta > 0:
                    # Progress rate: percent per second
                    rate = progress_delta / time_delta
                    remaining_progress = 100 - current_progress
                    estimated_seconds = int(remaining_progress / rate)

                    # Sanity check: cap at 24 hours
                    estimated_seconds = min(estimated_seconds, 86400)

                    return estimated_seconds, _format_duration_human(estimated_seconds)
            except (ValueError, TypeError):
                pass

    # Fallback: extrapolate from elapsed time
    if elapsed_seconds and elapsed_seconds > 0 and current_progress > 0:
        # If we're at X% after Y seconds, estimate total time
        total_estimated = int((elapsed_seconds / current_progress) * 100)
        remaining = total_estimated - elapsed_seconds

        if remaining > 0:
            # Cap at 24 hours
            remaining = min(remaining, 86400)
            return remaining, _format_duration_human(remaining)

    return None, None


def _calculate_next_check_time(
    status: OperationStatus,
    progress_percent: int,
    poll_interval: int,
) -> str | None:
    """Calculate recommended next check time.

    Adjusts recommendation based on progress - more frequent near completion.

    Args:
        status: Current operation status.
        progress_percent: Current progress percentage.
        poll_interval: Configured poll interval.

    Returns:
        ISO timestamp string or None if operation is complete.
    """
    if status in [OperationStatus.COMPLETED, OperationStatus.FAILED]:
        return None

    # Adjust interval based on progress
    if progress_percent >= 90:
        # Near completion - check more frequently
        interval_seconds = min(poll_interval, 60)
    elif progress_percent >= 70:
        interval_seconds = min(poll_interval * 2, 300)
    elif progress_percent >= 30:
        interval_seconds = min(poll_interval * 5, 600)
    else:
        # Early stages - longer intervals ok
        interval_seconds = min(poll_interval * 10, 900)

    next_time = datetime.now(UTC).replace(microsecond=0)
    from datetime import timedelta

    next_time = next_time + timedelta(seconds=interval_seconds)

    return next_time.isoformat()


async def _resolve_namespace(
    operation_type: OperationType,
    namespace: str | None,
    mcc_adapter: KubernetesAdapter | None,
) -> str:
    """Resolve namespace based on operation type.

    Args:
        operation_type: The operation type.
        namespace: User-provided namespace (may be None).
        mcc_adapter: MCC adapter for namespace discovery.

    Returns:
        Resolved namespace string.
    """
    if namespace:
        return namespace

    if operation_type == OperationType.OPENSTACK_UPGRADE:
        return "openstack"

    if operation_type in (OperationType.NODE_ADD, OperationType.MOSK_UPGRADE):
        # Try to auto-discover MOSK machines namespace from MCC
        if mcc_adapter:
            try:
                discovered = await mcc_adapter.get_mosk_machines_namespace()
                if discovered:
                    logger.debug("auto_discovered_namespace", namespace=discovered)
                    return discovered
            except Exception as e:
                logger.warning("namespace_discovery_failed", error=str(e))
        return "default"

    return "default"


async def monitor_operation(
    mcc_adapter: KubernetesAdapter | None,
    mosk_adapter: KubernetesAdapter | None,
    input_data: MonitorOperationInput,
) -> MonitorOperationOutput:
    """Monitor a long-running MOSK operation with periodic progress updates.

    This tool polls the operation status at regular intervals (30 seconds)
    for up to 5 minutes, collecting progress snapshots. When the polling
    window expires or the operation completes/fails, it returns all
    collected snapshots.

    If the operation is still in progress when the window expires,
    `continue_monitoring` will be True, indicating the caller should
    invoke the tool again to continue monitoring.

    Operation types and their cluster contexts:
    - node_add: Uses MCC adapter (management cluster)
    - openstack_upgrade: Uses MOSK adapter (child cluster)
    - mosk_upgrade: Uses MCC adapter (management cluster)

    Args:
        mcc_adapter: Kubernetes adapter for MCC management cluster.
        mosk_adapter: Kubernetes adapter for MOSK child cluster.
        input_data: Input parameters specifying operation to monitor.

    Returns:
        MonitorOperationOutput with progress snapshots and status.

    Raises:
        ValidationError: If required adapter is not available.
        ToolExecutionError: If monitoring fails.

    Example:
        >>> result = await monitor_operation(
        ...     mcc_adapter,
        ...     mosk_adapter,
        ...     MonitorOperationInput(
        ...         operation_type=OperationType.NODE_ADD,
        ...         target="compute-05",
        ...     ),
        ... )
        >>> print(f"Progress: {result.overall_progress_percent}%")
        >>> if result.continue_monitoring:
        ...     print("Call again to continue monitoring")
    """
    logger.info(
        "monitor_operation_start",
        operation_type=input_data.operation_type.value,
        target=input_data.target,
        namespace=input_data.namespace,
    )

    poll_start = datetime.now(UTC)

    try:
        # Validate we have the required adapter
        if input_data.operation_type in (OperationType.NODE_ADD, OperationType.MOSK_UPGRADE):
            if not mcc_adapter:
                raise ValidationError(
                    message=f"MCC adapter required for {input_data.operation_type.value} operations",
                    field="mcc_adapter",
                    value=None,
                    constraint="MCC adapter must be available",
                )
            adapter = mcc_adapter
        elif input_data.operation_type == OperationType.OPENSTACK_UPGRADE:
            if not mosk_adapter:
                raise ValidationError(
                    message="MOSK adapter required for openstack_upgrade operations",
                    field="mosk_adapter",
                    value=None,
                    constraint="MOSK adapter must be available",
                )
            adapter = mosk_adapter
        else:
            raise ValidationError(
                message=f"Unsupported operation type: {input_data.operation_type}",
                field="operation_type",
                value=input_data.operation_type.value,
                constraint="Must be node_add, openstack_upgrade, or mosk_upgrade",
            )

        # Resolve namespace
        namespace = await _resolve_namespace(
            input_data.operation_type,
            input_data.namespace,
            mcc_adapter,
        )

        # Create the appropriate monitor
        monitor: BaseOperationMonitor
        if input_data.operation_type == OperationType.NODE_ADD:
            monitor = NodeAddMonitor(
                adapter=adapter,
                target=input_data.target,
                namespace=namespace,
            )
        elif input_data.operation_type == OperationType.MOSK_UPGRADE:
            monitor = MoskUpgradeMonitor(
                adapter=adapter,
                target=input_data.target,
                namespace=namespace,
            )
        else:  # OPENSTACK_UPGRADE
            monitor = OpenStackUpgradeMonitor(
                adapter=adapter,
                target=input_data.target,
                namespace=namespace,
            )

        # Use configurable polling parameters
        poll_interval = input_data.poll_interval_seconds
        max_duration = input_data.max_duration_seconds
        max_polls = max_duration // poll_interval

        # Polling loop
        snapshots: list[ProgressSnapshot] = []
        poll_count = 0

        while poll_count < max_polls and len(snapshots) < MAX_SNAPSHOTS:
            # Get current progress
            snapshot = await monitor.poll()
            snapshots.append(snapshot)
            poll_count += 1

            logger.debug(
                "poll_snapshot_collected",
                poll_count=poll_count,
                progress=snapshot.progress_percent,
                phase=snapshot.phase,
            )

            # Check if operation is complete
            if monitor.is_complete() or monitor.has_failed():
                logger.info(
                    "operation_finished_during_poll",
                    is_complete=monitor.is_complete(),
                    has_failed=monitor.has_failed(),
                    poll_count=poll_count,
                )
                break

            # Wait before next poll (unless this was the last allowed poll)
            if poll_count < max_polls and len(snapshots) < MAX_SNAPSHOTS:
                await asyncio.sleep(poll_interval)

        # Determine final status
        if monitor.has_failed():
            status = OperationStatus.FAILED
            continue_monitoring = False
        elif monitor.is_complete():
            status = OperationStatus.COMPLETED
            continue_monitoring = False
        else:
            status = OperationStatus.IN_PROGRESS
            continue_monitoring = True

        # Get final snapshot for current state
        final_snapshot = snapshots[-1] if snapshots else None
        poll_end = datetime.now(UTC)
        polling_duration = int((poll_end - poll_start).total_seconds())

        # Calculate elapsed time
        elapsed_seconds: int | None = None
        if monitor.started_at:
            try:
                started = datetime.fromisoformat(monitor.started_at.replace("Z", "+00:00"))
                elapsed_seconds = int((poll_end - started).total_seconds())
            except (ValueError, TypeError):
                pass

        # Calculate ETA
        current_progress = final_snapshot.progress_percent if final_snapshot else 0
        eta_seconds, eta_human = _calculate_eta(snapshots, current_progress, elapsed_seconds)

        # Calculate next check recommendation
        next_check = _calculate_next_check_time(status, current_progress, poll_interval)

        # Extract operation-specific info from final snapshot details
        services_completed: int | None = None
        services_total: int | None = None
        services_in_progress: list[str] | None = None
        machines_completed: int | None = None
        machines_total: int | None = None
        machines_in_progress: list[dict[str, Any]] | None = None
        phase_message = final_snapshot.message if final_snapshot else ""

        if final_snapshot and final_snapshot.details:
            details = final_snapshot.details
            # OpenStack upgrade fields
            if "services_completed" in details:
                services_completed = details.get("services_completed")
            if "services_total" in details:
                services_total = details.get("services_total")
            if "services_in_progress" in details:
                services_in_progress = details.get("services_in_progress")
            # MOSK platform upgrade fields
            if "machines_completed" in details:
                machines_completed = details.get("machines_completed")
            if "machines_total" in details:
                machines_total = details.get("machines_total")
            if "machines_in_progress" in details:
                machines_in_progress = details.get("machines_in_progress")

        result = MonitorOperationOutput(
            operation_type=input_data.operation_type,
            target=input_data.target,
            namespace=namespace,
            status=status,
            overall_progress_percent=current_progress,
            current_phase=final_snapshot.phase if final_snapshot else "unknown",
            phase_message=phase_message,
            progress_snapshots=snapshots,
            continue_monitoring=continue_monitoring,
            error_message=monitor.get_error_message(),
            started_at=monitor.started_at,
            completed_at=monitor.completed_at,
            elapsed_seconds=elapsed_seconds,
            estimated_remaining_seconds=eta_seconds,
            estimated_remaining_human=eta_human,
            next_check_recommended=next_check,
            services_completed=services_completed,
            services_total=services_total,
            services_in_progress=services_in_progress,
            machines_completed=machines_completed,
            machines_total=machines_total,
            machines_in_progress=machines_in_progress,
            polling_duration_seconds=polling_duration,
            snapshots_collected=len(snapshots),
        )

        logger.info(
            "monitor_operation_complete",
            operation_type=input_data.operation_type.value,
            target=input_data.target,
            status=status.value,
            progress=result.overall_progress_percent,
            snapshots=len(snapshots),
            continue_monitoring=continue_monitoring,
            eta_seconds=eta_seconds,
        )

        return result

    except (ValidationError, ToolExecutionError):
        raise
    except Exception as e:
        logger.error(
            "monitor_operation_error",
            operation_type=input_data.operation_type.value,
            target=input_data.target,
            error=str(e),
        )
        raise ToolExecutionError(
            message=f"Failed to monitor operation: {e}",
            tool_name=TOOL_NAME,
            details={
                "operation_type": input_data.operation_type.value,
                "target": input_data.target,
                "error": str(e),
            },
        ) from e
