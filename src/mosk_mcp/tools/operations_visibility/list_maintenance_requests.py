"""List maintenance requests tool.

This module provides the list_maintenance_requests tool that retrieves
active NodeMaintenanceRequest CRs from the cluster with status and progress.

Safety Level: READ_ONLY
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from mosk_mcp.core.exceptions import ToolExecutionError
from mosk_mcp.observability.logging import get_logger
from mosk_mcp.tools.operations_visibility.models import (
    ListMaintenanceRequestsInput,
    ListMaintenanceRequestsOutput,
    MaintenancePhase,
    MaintenanceRequestInfo,
)


if TYPE_CHECKING:
    from mosk_mcp.adapters.kubernetes import KubernetesAdapter


logger = get_logger(__name__)


def _parse_maintenance_phase(phase_str: str) -> MaintenancePhase:
    """Parse maintenance phase string to enum.

    Args:
        phase_str: Phase string from API.

    Returns:
        MaintenancePhase enum value.
    """
    try:
        return MaintenancePhase(phase_str)
    except ValueError:
        return MaintenancePhase.PENDING


def _parse_maintenance_request(
    request: dict[str, Any],
) -> MaintenanceRequestInfo:
    """Parse a NodeMaintenanceRequest resource.

    Args:
        request: Raw request from Kubernetes API.

    Returns:
        Parsed MaintenanceRequestInfo object.
    """
    metadata = request.get("metadata", {})
    spec = request.get("spec", {})
    status = request.get("status", {})

    phase = _parse_maintenance_phase(status.get("phase", "Pending"))

    # Determine completion status
    is_complete = phase in (
        MaintenancePhase.COMPLETED,
        MaintenancePhase.FAILED,
        MaintenancePhase.CANCELLED,
    )
    is_successful = phase == MaintenancePhase.COMPLETED

    return MaintenanceRequestInfo(
        name=metadata.get("name", "unknown"),
        namespace=metadata.get("namespace", "default"),
        node_name=spec.get("nodeName", "unknown"),
        phase=phase,
        reason=spec.get("reason", "Unknown"),
        description=spec.get("description"),
        drain_strategy=spec.get("drainStrategy", "Graceful"),
        created_at=metadata.get("creationTimestamp", ""),
        started_at=status.get("startedAt"),
        completed_at=status.get("completedAt"),
        is_complete=is_complete,
        is_successful=is_successful,
        pods_evicted=status.get("totalEvicted", 0),
        error_message=status.get("errorMessage"),
        crq_number=spec.get("crqNumber"),
    )


async def list_maintenance_requests(
    kubernetes_adapter: KubernetesAdapter,
    input_data: ListMaintenanceRequestsInput,
) -> ListMaintenanceRequestsOutput:
    """List NodeMaintenanceRequest resources.

    Retrieves maintenance requests from the cluster with optional
    filtering by node, phase, and completion status.

    Args:
        kubernetes_adapter: Kubernetes client adapter.
        input_data: Filter parameters.

    Returns:
        List of maintenance requests with statistics.

    Raises:
        ToolExecutionError: If operation fails.
    """
    logger.info(
        "list_maintenance_requests_start",
        namespace=input_data.namespace,
        node_filter=input_data.node_filter,
        phase_filter=input_data.phase_filter.value if input_data.phase_filter else None,
    )

    try:
        # Get all NodeMaintenanceRequest CRs (cluster-scoped, namespace ignored)
        raw_requests = await kubernetes_adapter.list_maintenance_requests()

        # Parse and filter requests
        requests: list[MaintenanceRequestInfo] = []
        for raw in raw_requests:
            req = _parse_maintenance_request(raw)

            # Apply node filter
            if input_data.node_filter and req.node_name != input_data.node_filter:
                continue

            # Apply phase filter
            if input_data.phase_filter and req.phase != input_data.phase_filter:
                continue

            # Apply completed filter
            if not input_data.include_completed and req.is_complete:
                continue

            requests.append(req)

        # Apply limit
        requests = requests[: input_data.limit]

        # Calculate statistics
        active_phases = (
            MaintenancePhase.DRAINING,
            MaintenancePhase.DRAINED,
            MaintenancePhase.MAINTAINING,
            MaintenancePhase.UNCORDONING,
        )
        active_count = sum(1 for r in requests if r.phase in active_phases)
        pending_count = sum(1 for r in requests if r.phase == MaintenancePhase.PENDING)
        completed_count = sum(1 for r in requests if r.phase == MaintenancePhase.COMPLETED)
        failed_count = sum(
            1 for r in requests if r.phase in (MaintenancePhase.FAILED, MaintenancePhase.CANCELLED)
        )

        # Count by phase
        by_phase: dict[str, int] = {}
        for r in requests:
            phase_name = r.phase.value
            by_phase[phase_name] = by_phase.get(phase_name, 0) + 1

        # Count by node
        by_node: dict[str, int] = {}
        for r in requests:
            by_node[r.node_name] = by_node.get(r.node_name, 0) + 1

        # Get nodes currently in maintenance
        nodes_in_maintenance = list({r.node_name for r in requests if r.phase in active_phases})

        result = ListMaintenanceRequestsOutput(
            requests=requests,
            total_count=len(requests),
            active_count=active_count,
            pending_count=pending_count,
            completed_count=completed_count,
            failed_count=failed_count,
            by_phase=by_phase,
            by_node=by_node,
            nodes_in_maintenance=sorted(nodes_in_maintenance),
            timestamp=datetime.now(UTC).isoformat(),
        )

        logger.info(
            "list_maintenance_requests_complete",
            total=len(requests),
            active=active_count,
            nodes_in_maintenance=len(nodes_in_maintenance),
        )

        return result

    except Exception as e:
        logger.error(
            "list_maintenance_requests_error",
            error=str(e),
        )
        raise ToolExecutionError(
            f"Failed to list maintenance requests: {e}",
            tool_name="list_maintenance_requests",
        ) from e
