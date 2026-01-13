"""Get OpenStack upgrade progress tool.

This module provides the get_openstack_upgrade_progress tool that tracks detailed
upgrade/update progress for OpenStackDeployment resources, including:
- Per-component upgrade status
- Progress percentages
- ETA calculations
- Control plane and compute node readiness

Uses OSDPLStatus (osdplst) CR for accurate real-time status.

Safety Level: READ_ONLY
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from mosk_mcp.core.exceptions import ResourceNotFoundError, ToolExecutionError
from mosk_mcp.observability.logging import get_logger
from mosk_mcp.tools.common.constants import OSDPLST_UPGRADING_STATES
from mosk_mcp.tools.common.parsers import parse_health_ratio
from mosk_mcp.tools.operations_visibility.models import (
    ComponentUpgradeStatus,
    GetUpgradeProgressInput,
    GetUpgradeProgressOutput,
    UpgradeState,
)


if TYPE_CHECKING:
    from mosk_mcp.adapters.kubernetes import KubernetesAdapter


logger = get_logger(__name__)


# OpenStack services that are tracked during upgrades (LCM service categories)
# Note: This is a comprehensive list for upgrade tracking, not general control plane
LCM_UPGRADE_SERVICE_CATEGORIES = [
    "messaging",
    "database",
    "memcached",
    "ingress",
    "identity",
    "image",
    "compute",
    "networking",
    "orchestration",
    "dashboard",
    "load-balancing",
    "key-manager",
    "shared-file-system",
    "dns",
    "container-infra",
    "placement",
    "baremetal",
    "block-storage",
    "metering",
    "alarming",
    "event",
    "metric",
    "object-storage",
    "coordination",
    "tempest",
    "stacklight",
]


def _determine_component_state_from_osdplst(
    lcm_state: str,
) -> UpgradeState:
    """Determine upgrade state from OSDPLStatus LCM service state.

    Args:
        lcm_state: Service state from osdplst (APPLIED, APPLYING, FAILED, WAITING).

    Returns:
        Component upgrade state.
    """
    if lcm_state == "APPLIED":
        return UpgradeState.COMPLETED
    elif lcm_state in OSDPLST_UPGRADING_STATES:
        return UpgradeState.IN_PROGRESS
    elif lcm_state == "FAILED":
        return UpgradeState.FAILED
    else:
        return UpgradeState.NOT_STARTED


def _parse_osdplst_service_status(
    name: str,
    svc_data: dict[str, Any],
    target_version: str,
) -> ComponentUpgradeStatus:
    """Parse component status from OSDPLStatus services section.

    Args:
        name: Service category name (e.g., 'compute', 'networking').
        svc_data: Service data from osdplst status.services.
        target_version: Target OpenStack version.

    Returns:
        ComponentUpgradeStatus object.
    """
    state_str = svc_data.get("state", "Unknown")
    state = _determine_component_state_from_osdplst(state_str)

    current_version = svc_data.get("openstackVersion", "unknown")
    is_ready = state == UpgradeState.COMPLETED

    # Calculate progress based on state
    if state == UpgradeState.COMPLETED:
        progress = 100
    elif state == UpgradeState.IN_PROGRESS:
        progress = 50  # Mid-progress for APPLYING state
    elif state == UpgradeState.FAILED:
        progress = 0
    else:
        progress = 0

    return ComponentUpgradeStatus(
        name=name,
        current_version=current_version,
        target_version=target_version,
        state=state,
        progress_percent=progress,
        replicas_updated=1 if is_ready else 0,
        replicas_total=1,
        started_at=svc_data.get("timestamp"),
        completed_at=svc_data.get("timestamp") if is_ready else None,
        error_message=None,
    )


def _estimate_remaining_time(
    components: list[ComponentUpgradeStatus],
    started_at: str | None,
) -> tuple[int | None, str | None]:
    """Estimate remaining time for upgrade.

    Args:
        components: List of component statuses.
        started_at: When upgrade started.

    Returns:
        Tuple of (minutes remaining, completion time ISO string).
    """
    if not started_at:
        return None, None

    # Count components in various states
    in_progress = [c for c in components if c.state == UpgradeState.IN_PROGRESS]
    not_started = [c for c in components if c.state == UpgradeState.NOT_STARTED]
    [c for c in components if c.state == UpgradeState.COMPLETED]

    if not in_progress and not not_started:
        # All complete
        return 0, datetime.now(UTC).isoformat()

    # Estimate based on typical durations
    # Control plane services: ~5-10 minutes each
    # Compute-related services: ~5 minutes per node
    remaining_minutes = 0

    for comp in in_progress:
        # Estimate based on progress
        if comp.progress_percent > 0:
            # Linear estimate based on progress
            elapsed_estimate = 10 * (comp.progress_percent / 100)
            remaining_estimate = 10 - elapsed_estimate
            remaining_minutes += max(1, int(remaining_estimate))
        else:
            remaining_minutes += 10

    # Not started components
    remaining_minutes += len(not_started) * 10

    if remaining_minutes > 0:
        completion_time = datetime.now(UTC)
        from datetime import timedelta

        completion_time = completion_time + timedelta(minutes=remaining_minutes)
        return remaining_minutes, completion_time.isoformat()

    return None, None


async def get_openstack_upgrade_progress(
    kubernetes_adapter: KubernetesAdapter,
    input_data: GetUpgradeProgressInput,
) -> GetUpgradeProgressOutput:
    """Get detailed OpenStack upgrade progress.

    Tracks the progress of an OpenStackDeployment upgrade, providing
    per-component status, progress percentages, and time estimates.

    Uses OSDPLStatus (osdplst) CR for accurate real-time status.

    Args:
        kubernetes_adapter: Kubernetes client adapter (MOSK cluster).
        input_data: Input parameters.

    Returns:
        Upgrade progress details.

    Raises:
        ResourceNotFoundError: If OSDPL or OSDPLStatus is not found.
        ToolExecutionError: If operation fails.
    """
    logger.info(
        "get_openstack_upgrade_progress_start",
        name=input_data.name,
        namespace=input_data.namespace,
    )

    try:
        # Get the OpenStackDeployment resource
        osdpl = await kubernetes_adapter.get_openstack_deployment(
            name=input_data.name,
            namespace=input_data.namespace,
        )

        spec = osdpl.get("spec", {})
        status = osdpl.get("status", {})

        # Get target version from spec
        target_version = spec.get("openStackVersion", "unknown")

        # Get OSDPLStatus (osdplst) - this is required
        osdplst = await kubernetes_adapter.get_openstack_deployment_status(
            name=input_data.name,
            namespace=input_data.namespace,
        )
        if not osdplst:
            raise ResourceNotFoundError(
                f"OSDPLStatus '{input_data.name}' not found in namespace '{input_data.namespace}'. "
                "This tool requires OSDPLStatus CR which is available in modern MOSK versions."
            )

        osdplst_status = osdplst.get("status", {})
        osdpl_section = osdplst_status.get("osdpl", {})

        # Extract state and progress from osdplst
        osdplst_state = osdpl_section.get("state")
        osdplst_health = osdpl_section.get("health")
        osdplst_lcm_progress = osdpl_section.get("lcmProgress")

        # Get OpenStack version from osdplst
        current_version = osdpl_section.get("openstackVersion") or status.get(
            "openStackVersion", target_version
        )

        # Get per-service status from osdplst
        osdplst_services = osdplst_status.get("services", {})

        logger.debug(
            "osdplst_data_retrieved_for_upgrade",
            state=osdplst_state,
            health=osdplst_health,
            lcm_progress=osdplst_lcm_progress,
            services_count=len(osdplst_services),
        )

        # Determine overall upgrade state from osdplst
        is_upgrading = osdplst_state in OSDPLST_UPGRADING_STATES
        if osdplst_state == "FAILED":
            upgrade_state = UpgradeState.FAILED
        elif osdplst_state == "APPLIED":
            upgrade_state = UpgradeState.COMPLETED
        elif is_upgrading:
            upgrade_state = UpgradeState.IN_PROGRESS
        else:
            upgrade_state = UpgradeState.NOT_STARTED

        # Parse per-component status
        components: list[ComponentUpgradeStatus] = []

        if input_data.include_component_details:
            for svc_name, svc_data in osdplst_services.items():
                components.append(
                    _parse_osdplst_service_status(
                        name=svc_name,
                        svc_data=svc_data,
                        target_version=target_version,
                    )
                )

        # Count component states
        components_completed = sum(1 for c in components if c.state == UpgradeState.COMPLETED)
        components_total = len(components)

        # Calculate overall progress
        if osdplst_lcm_progress:
            # Use LCM progress from osdplst (e.g., "18/18")
            lcm_ready, lcm_total = parse_health_ratio(osdplst_lcm_progress)
            if lcm_total > 0:
                overall_progress = int((lcm_ready / lcm_total) * 100)
            else:
                overall_progress = 100 if upgrade_state == UpgradeState.COMPLETED else 0
        elif components_total > 0:
            total_progress = sum(c.progress_percent for c in components)
            overall_progress = int(total_progress / components_total)
        else:
            overall_progress = 100 if upgrade_state == UpgradeState.COMPLETED else 0

        # Check control plane and compute readiness from osdplst health
        if osdplst_health:
            health_ready, health_total = parse_health_ratio(osdplst_health)
            control_plane_ready = health_ready == health_total and health_total > 0
            compute_nodes_ready = control_plane_ready  # Infer from overall health
        else:
            # No health info available
            control_plane_ready = upgrade_state == UpgradeState.COMPLETED
            compute_nodes_ready = upgrade_state == UpgradeState.COMPLETED

        # Get timing information
        started_at = status.get("updateStartedAt")

        # Estimate remaining time
        remaining_minutes, estimated_completion = _estimate_remaining_time(components, started_at)

        # Determine current step
        current_step = None
        in_progress_components = [c for c in components if c.state == UpgradeState.IN_PROGRESS]
        if in_progress_components:
            current_step = f"Upgrading {', '.join(c.name for c in in_progress_components[:3])}"
            if len(in_progress_components) > 3:
                current_step += f" and {len(in_progress_components) - 3} more"
        elif is_upgrading and osdplst_state:
            current_step = (
                f"State: {osdplst_state}, LCM Progress: {osdplst_lcm_progress or 'unknown'}"
            )

        # Collect warnings and blockers
        warnings: list[str] = []
        blockers: list[str] = []

        failed_components = [c for c in components if c.state == UpgradeState.FAILED]
        for comp in failed_components:
            if comp.error_message:
                blockers.append(f"{comp.name}: {comp.error_message}")
            else:
                blockers.append(f"{comp.name} upgrade failed")

        if not control_plane_ready and is_upgrading:
            warnings.append("Control plane is not yet ready")
        if not compute_nodes_ready and is_upgrading:
            warnings.append("Compute nodes are not yet ready")

        # Add version mismatch warning
        if current_version != target_version and not is_upgrading:
            warnings.append(f"Version mismatch: current={current_version}, target={target_version}")

        result = GetUpgradeProgressOutput(
            name=input_data.name,
            namespace=input_data.namespace,
            is_upgrading=is_upgrading,
            upgrade_state=upgrade_state,
            from_version=current_version,
            to_version=target_version,
            overall_progress_percent=overall_progress,
            components=components,
            components_completed=components_completed,
            components_total=components_total,
            control_plane_ready=control_plane_ready,
            compute_nodes_ready=compute_nodes_ready,
            started_at=started_at,
            estimated_completion=estimated_completion,
            estimated_remaining_minutes=remaining_minutes,
            current_step=current_step,
            warnings=warnings,
            blockers=blockers,
            timestamp=datetime.now(UTC).isoformat(),
        )

        logger.info(
            "get_openstack_upgrade_progress_complete",
            name=input_data.name,
            is_upgrading=is_upgrading,
            progress=overall_progress,
            osdplst_state=osdplst_state,
        )

        return result

    except ResourceNotFoundError:
        logger.warning(
            "osdpl_not_found",
            name=input_data.name,
            namespace=input_data.namespace,
        )
        raise
    except Exception as e:
        logger.error(
            "get_openstack_upgrade_progress_error",
            name=input_data.name,
            error=str(e),
        )
        raise ToolExecutionError(
            f"Failed to get OpenStack upgrade progress: {e}",
            tool_name="get_openstack_upgrade_progress",
        ) from e
