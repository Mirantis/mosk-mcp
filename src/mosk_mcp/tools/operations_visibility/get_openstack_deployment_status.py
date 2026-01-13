"""Get OpenStack Deployment status tool.

This module provides the get_openstack_deployment_status tool that retrieves
comprehensive status information about an OpenStackDeployment resource, including:
- Current phase and health status
- Update/upgrade progress
- Per-service status
- Condition interpretation with recommendations

Safety Level: READ_ONLY
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from mosk_mcp.core.exceptions import ResourceNotFoundError, ToolExecutionError
from mosk_mcp.observability.logging import get_logger
from mosk_mcp.tools.common.enums import HealthStatus
from mosk_mcp.tools.operations_visibility.models import (
    ComponentHealthInfo,
    Condition,
    ConditionStatus,
    GetOSDPLStatusInput,
    GetOSDPLStatusOutput,
    LCMServiceStatus,
    OSDPLPhase,
    OSDPLState,
    OSDPLStatusSummary,
    ServiceStatusInfo,
)


if TYPE_CHECKING:
    from mosk_mcp.adapters.kubernetes import KubernetesAdapter


logger = get_logger(__name__)


# OSDPL condition interpretation mapping
CONDITION_INTERPRETATIONS = {
    ("Updating", "True"): {
        "interpretation": "Upgrade in progress - OpenStack services are being updated",
        "typical_duration": "30-120 minutes",
        "action_required": False,
    },
    ("Ready", "False", "Updating", "False"): {
        "interpretation": "Stuck or failed state - cluster may require investigation",
        "typical_duration": None,
        "action_required": True,
    },
    ("ControlPlaneReady", "False"): {
        "interpretation": "Control services are updating or unhealthy",
        "typical_duration": "15-30 minutes",
        "action_required": False,
    },
    ("ComputeNodesReady", "False"): {
        "interpretation": "Compute services rolling out to nodes",
        "typical_duration": "5-10 minutes per node",
        "action_required": False,
    },
    ("Ready", "True"): {
        "interpretation": "Cluster is healthy and fully operational",
        "typical_duration": None,
        "action_required": False,
    },
}


def _parse_conditions(conditions_data: list[dict[str, Any]]) -> list[Condition]:
    """Parse conditions from Kubernetes API response.

    Args:
        conditions_data: Raw conditions list from K8s API.

    Returns:
        List of parsed Condition objects.
    """
    conditions = []
    for cond in conditions_data:
        status_str = cond.get("status", "Unknown")
        try:
            status = ConditionStatus(status_str)
        except ValueError:
            status = ConditionStatus.UNKNOWN

        conditions.append(
            Condition(
                type=cond.get("type", "Unknown"),
                status=status,
                reason=cond.get("reason"),
                message=cond.get("message"),
                last_transition_time=cond.get("lastTransitionTime"),
                last_update_time=cond.get("lastUpdateTime"),
            )
        )
    return conditions


def _parse_services(services_data: dict[str, Any]) -> list[ServiceStatusInfo]:
    """Parse per-service status from OSDPL status.

    Args:
        services_data: Services status from OSDPL status.

    Returns:
        List of ServiceStatusInfo objects.
    """
    services = []
    for name, svc_data in services_data.items():
        if not isinstance(svc_data, dict):
            continue

        services.append(
            ServiceStatusInfo(
                name=name,
                ready=svc_data.get("ready", False),
                replicas_desired=svc_data.get("replicas", 0),
                replicas_ready=svc_data.get("readyReplicas", 0),
                replicas_available=svc_data.get("availableReplicas", 0),
                message=svc_data.get("message"),
                is_updating=svc_data.get("updating", False),
            )
        )
    return sorted(services, key=lambda s: s.name)


def _parse_health_ratio(health_str: str) -> tuple[int, int]:
    """Parse health ratio string like '23/23' into (ready, total).

    Args:
        health_str: Health ratio string (e.g., '23/23').

    Returns:
        Tuple of (ready_count, total_count).
    """
    try:
        parts = health_str.split("/")
        if len(parts) == 2:
            return int(parts[0]), int(parts[1])
    except (ValueError, AttributeError):
        pass
    return 0, 0


def _parse_component_health(
    health_data: dict[str, Any],
) -> tuple[list[ComponentHealthInfo], list[str]]:
    """Parse component health from OSDPLStatus status.health.

    Args:
        health_data: The status.health dict from OSDPLStatus CR.

    Returns:
        Tuple of (component_list, unhealthy_component_names).
    """
    components: list[ComponentHealthInfo] = []
    unhealthy: list[str] = []

    for service_name, service_components in health_data.items():
        if not isinstance(service_components, dict):
            continue

        for component_name, component_data in service_components.items():
            if not isinstance(component_data, dict):
                continue

            status = component_data.get("status", "Unknown")
            generation = component_data.get("generation", 0)
            is_ready = status == "Ready"

            components.append(
                ComponentHealthInfo(
                    service=service_name,
                    component=component_name,
                    status=status,
                    generation=generation,
                    is_ready=is_ready,
                )
            )

            if not is_ready:
                unhealthy.append(f"{service_name}.{component_name}")

    # Sort by service then component name
    components.sort(key=lambda c: (c.service, c.component))
    return components, unhealthy


def _parse_lcm_services(
    services_data: dict[str, Any],
) -> tuple[list[LCMServiceStatus], list[str]]:
    """Parse LCM service status from OSDPLStatus status.services.

    Args:
        services_data: The status.services dict from OSDPLStatus CR.

    Returns:
        Tuple of (service_list, failed_service_names).
    """
    services: list[LCMServiceStatus] = []
    failed: list[str] = []

    for service_name, service_data in services_data.items():
        if not isinstance(service_data, dict):
            continue

        state_str = service_data.get("state", "Unknown")
        try:
            state = OSDPLState(state_str)
        except ValueError:
            state = OSDPLState.UNKNOWN

        services.append(
            LCMServiceStatus(
                name=service_name,
                state=state,
                openstack_version=service_data.get("openstack_version", "unknown"),
                controller_version=service_data.get("controller_version", "unknown"),
                release=service_data.get("release", "unknown"),
                timestamp=service_data.get("timestamp"),
                fingerprint=service_data.get("fingerprint"),
            )
        )

        if state not in (OSDPLState.APPLIED, OSDPLState.UNKNOWN):
            failed.append(service_name)

    services.sort(key=lambda s: s.name)
    return services, failed


def _determine_health(
    phase: OSDPLPhase,
    conditions: list[Condition],
    osdplst_state: OSDPLState | None = None,
    health_ready: int | None = None,
    health_total: int | None = None,
) -> HealthStatus:
    """Determine overall health status from phase, conditions, and OSDPLStatus.

    Prefers OSDPLStatus data when available as it provides more accurate status.

    Args:
        phase: Current OSDPL phase (legacy).
        conditions: List of conditions.
        osdplst_state: State from OSDPLStatus (APPLIED, APPLYING, FAILED).
        health_ready: Number of healthy components from OSDPLStatus.
        health_total: Total components from OSDPLStatus.

    Returns:
        Computed health status.
    """
    # Prefer OSDPLStatus data when available
    if osdplst_state is not None:
        if osdplst_state == OSDPLState.FAILED:
            return HealthStatus.UNHEALTHY
        if osdplst_state == OSDPLState.APPLIED:
            # Check component health ratio
            if health_ready is not None and health_total is not None and health_total > 0:
                if health_ready == health_total:
                    return HealthStatus.HEALTHY
                # More than 80% healthy = degraded, less = unhealthy
                ratio = health_ready / health_total
                if ratio >= 0.8:
                    return HealthStatus.DEGRADED
                return HealthStatus.UNHEALTHY
            return HealthStatus.HEALTHY
        if osdplst_state == OSDPLState.APPLYING:
            return HealthStatus.DEGRADED
        if osdplst_state == OSDPLState.WAITING:
            return HealthStatus.DEGRADED

    # Fallback to legacy phase-based logic
    if phase == OSDPLPhase.FAILED:
        return HealthStatus.UNHEALTHY

    if phase == OSDPLPhase.DEPLOYED:
        # Check if Ready condition is True
        ready_cond = next((c for c in conditions if c.type == "Ready"), None)
        if ready_cond and ready_cond.status == ConditionStatus.TRUE:
            return HealthStatus.HEALTHY
        return HealthStatus.DEGRADED

    if phase in (OSDPLPhase.UPDATING, OSDPLPhase.DEPLOYING):
        return HealthStatus.DEGRADED

    return HealthStatus.UNKNOWN


def _interpret_status(
    phase: OSDPLPhase,
    conditions: list[Condition],
    is_updating: bool,
    osdplst_state: OSDPLState | None = None,
    unhealthy_components: list[str] | None = None,
    failed_services: list[str] | None = None,
) -> OSDPLStatusSummary:
    """Interpret OSDPL status and provide recommendations.

    Args:
        phase: Current OSDPL phase.
        conditions: List of conditions.
        is_updating: Whether OSDPL is currently updating.
        osdplst_state: State from OSDPLStatus (APPLIED, APPLYING, FAILED).
        unhealthy_components: List of unhealthy component names.
        failed_services: List of services not in APPLIED state.

    Returns:
        Status summary with interpretation.
    """
    recommendations: list[str] = []
    unhealthy_components = unhealthy_components or []
    failed_services = failed_services or []

    # Build condition lookup
    cond_map = {c.type: c.status for c in conditions}

    # Prefer OSDPLStatus-based interpretation when available
    if osdplst_state is not None:
        if osdplst_state == OSDPLState.APPLIED:
            if unhealthy_components:
                interpretation = (
                    f"Cluster deployed but {len(unhealthy_components)} component(s) unhealthy"
                )
                typical_duration = None
                action_required = True
                recommendations.append(
                    f"Investigate unhealthy components: {', '.join(unhealthy_components[:5])}"
                )
                if len(unhealthy_components) > 5:
                    recommendations.append(
                        f"...and {len(unhealthy_components) - 5} more unhealthy components"
                    )
            else:
                interpretation = "Cluster is healthy and fully operational (APPLIED)"
                typical_duration = None
                action_required = False
        elif osdplst_state == OSDPLState.APPLYING:
            interpretation = "Configuration changes being applied"
            typical_duration = "5-30 minutes"
            action_required = False
            if failed_services:
                recommendations.append(f"Services being updated: {', '.join(failed_services)}")
            recommendations.append("Monitor progress using get_rollout_status tool")
        elif osdplst_state == OSDPLState.WAITING:
            interpretation = "Cluster is waiting for dependencies"
            typical_duration = None
            action_required = False
            recommendations.append("Check for pending prerequisites")
        elif osdplst_state == OSDPLState.FAILED:
            interpretation = "Deployment has failed - immediate attention required"
            typical_duration = None
            action_required = True
            if failed_services:
                recommendations.append(f"Failed services: {', '.join(failed_services)}")
            recommendations.append("Review operator logs for details")
            recommendations.append("Contact support if issue persists")
        else:
            interpretation = f"Cluster is in {osdplst_state.value} state"
            typical_duration = None
            action_required = False

        return OSDPLStatusSummary(
            interpretation=interpretation,
            typical_duration=typical_duration,
            action_required=action_required,
            recommendations=recommendations,
        )

    # Fallback to legacy condition-based interpretation
    if is_updating or cond_map.get("Updating") == ConditionStatus.TRUE:
        interpretation = "Upgrade in progress - OpenStack services are being updated"
        typical_duration = "30-120 minutes"
        action_required = False
        recommendations.append("Monitor progress using get_openstack_upgrade_progress tool")
        recommendations.append("Check get_rollout_status for individual service status")
    elif (
        cond_map.get("Ready") == ConditionStatus.FALSE
        and cond_map.get("Updating") != ConditionStatus.TRUE
    ):
        interpretation = "Cluster is not ready - investigation may be required"
        typical_duration = None
        action_required = True
        recommendations.append("Check conditions for error messages")
        recommendations.append("Review OpenStack service pods for failures")
        recommendations.append("Check cluster events for issues")
    elif cond_map.get("ControlPlaneReady") == ConditionStatus.FALSE:
        interpretation = "Control plane services are updating or unhealthy"
        typical_duration = "15-30 minutes"
        action_required = False
        recommendations.append("Wait for control plane services to stabilize")
        recommendations.append("Check Keystone, Nova API, Neutron API pods")
    elif cond_map.get("ComputeNodesReady") == ConditionStatus.FALSE:
        interpretation = "Compute services are rolling out to nodes"
        typical_duration = "5-10 minutes per node"
        action_required = False
        recommendations.append("Check nova-compute pods on compute nodes")
        recommendations.append("Use list_machines to verify node status")
    elif phase == OSDPLPhase.DEPLOYED and cond_map.get("Ready") == ConditionStatus.TRUE:
        interpretation = "Cluster is healthy and fully operational"
        typical_duration = None
        action_required = False
    elif phase == OSDPLPhase.FAILED:
        interpretation = "Deployment has failed - immediate attention required"
        typical_duration = None
        action_required = True
        recommendations.append("Check conditions for failure reason")
        recommendations.append("Review operator logs for details")
        recommendations.append("Contact support if issue persists")
    else:
        interpretation = f"Cluster is in {phase.value} state"
        typical_duration = None
        action_required = False

    return OSDPLStatusSummary(
        interpretation=interpretation,
        typical_duration=typical_duration,
        action_required=action_required,
        recommendations=recommendations,
    )


async def get_openstack_deployment_status(
    kubernetes_adapter: KubernetesAdapter,
    input_data: GetOSDPLStatusInput,
) -> GetOSDPLStatusOutput:
    """Get OpenStack Deployment status.

    Retrieves comprehensive status information about an OpenStackDeployment
    resource including phase, conditions, per-service status, and interpreted
    recommendations.

    Also fetches OSDPLStatus (osdplst) CR which contains the real status:
    - status.osdpl: Overall state (APPLIED/APPLYING/FAILED), health ratio, LCM progress
    - status.health: Per-component health (nova.api, neutron.server, etc.)
    - status.services: Per-service LCM state (compute, networking, etc.)

    Args:
        kubernetes_adapter: Kubernetes client adapter (MOSK cluster).
        input_data: Input parameters.

    Returns:
        OpenStack deployment status with interpretation.

    Raises:
        ResourceNotFoundError: If OSDPL is not found.
        ToolExecutionError: If operation fails.
    """
    logger.info(
        "get_openstack_deployment_status_start",
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
        metadata = osdpl.get("metadata", {})

        # Parse phase (legacy)
        phase_str = status.get("phase", "Unknown")
        try:
            phase = OSDPLPhase(phase_str)
        except ValueError:
            phase = OSDPLPhase.UNKNOWN

        # Parse conditions
        conditions: list[Condition] = []
        if input_data.include_conditions:
            conditions = _parse_conditions(status.get("conditions", []))

        # Parse services (legacy)
        services: list[ServiceStatusInfo] = []
        if input_data.include_services:
            services = _parse_services(status.get("services", {}))

        # Count ready services (legacy)
        services_ready = sum(1 for s in services if s.ready)
        services_total = len(services)

        # Get versions
        target_version = spec.get("openstack_version", spec.get("openStackVersion", "unknown"))
        current_version = status.get("openStackVersion", target_version)

        # Get last updated time
        last_updated = status.get("lastUpdateTime") or metadata.get("creationTimestamp", "")

        # Get endpoints
        endpoints = status.get("endpoints", {})

        # ============================================================
        # Fetch OSDPLStatus (osdplst) for real status
        # ============================================================
        osdplst_state: OSDPLState | None = None
        osdplst_health: str | None = None
        osdplst_health_ready: int | None = None
        osdplst_health_total: int | None = None
        osdplst_lcm_progress: str | None = None
        osdplst_release: str | None = None
        component_health: list[ComponentHealthInfo] = []
        lcm_services: list[LCMServiceStatus] = []
        unhealthy_components: list[str] = []
        failed_services: list[str] = []

        try:
            osdplst = await kubernetes_adapter.get_openstack_deployment_status(
                name=input_data.name,
                namespace=input_data.namespace,
            )
            osdplst_status = osdplst.get("status", {})

            # Parse status.osdpl (overall status)
            osdpl_summary = osdplst_status.get("osdpl", {})
            if osdpl_summary:
                state_str = osdpl_summary.get("state", "Unknown")
                try:
                    osdplst_state = OSDPLState(state_str)
                except ValueError:
                    osdplst_state = OSDPLState.UNKNOWN

                osdplst_health = osdpl_summary.get("health", "")
                osdplst_health_ready, osdplst_health_total = _parse_health_ratio(osdplst_health)
                osdplst_lcm_progress = osdpl_summary.get("lcm_progress", "")
                osdplst_release = osdpl_summary.get("release", "")

                # Update current version from osdplst if available
                if osdpl_summary.get("openstack_version"):
                    current_version = osdpl_summary.get("openstack_version")

                # Update last_updated from osdplst if available
                if osdpl_summary.get("timestamp"):
                    last_updated = osdpl_summary.get("timestamp")

            # Parse status.health (component health)
            health_data = osdplst_status.get("health", {})
            if health_data and input_data.include_services:
                component_health, unhealthy_components = _parse_component_health(health_data)

            # Parse status.services (LCM service status)
            services_data = osdplst_status.get("services", {})
            if services_data and input_data.include_services:
                lcm_services, failed_services = _parse_lcm_services(services_data)

            # Update services count from OSDPLStatus
            if component_health:
                services_ready = sum(1 for c in component_health if c.is_ready)
                services_total = len(component_health)

            logger.info(
                "osdplst_fetched",
                name=input_data.name,
                state=osdplst_state.value if osdplst_state else "unknown",
                health=osdplst_health,
                components=len(component_health),
                unhealthy=len(unhealthy_components),
            )

        except ResourceNotFoundError:
            # OSDPLStatus is required - older MOSK versions not supported
            raise ResourceNotFoundError(
                f"OSDPLStatus '{input_data.name}' not found in namespace '{input_data.namespace}'. "
                "This tool requires OSDPLStatus CR which is available in modern MOSK versions."
            ) from None
        except Exception as e:
            logger.error(
                "osdplst_fetch_failed",
                name=input_data.name,
                error=str(e),
            )
            raise

        # ============================================================
        # Determine derived status fields
        # ============================================================

        # Determine if updating (prefer OSDPLStatus)
        if osdplst_state == OSDPLState.APPLYING:
            is_updating = True
        else:
            is_updating = phase == OSDPLPhase.UPDATING or any(
                c.type == "Updating" and c.status == ConditionStatus.TRUE for c in conditions
            )

        # Determine if ready (prefer OSDPLStatus)
        if osdplst_state == OSDPLState.APPLIED and not unhealthy_components:
            is_ready = True
        elif osdplst_state is not None:
            is_ready = False
        else:
            is_ready = phase == OSDPLPhase.DEPLOYED and any(
                c.type == "Ready" and c.status == ConditionStatus.TRUE for c in conditions
            )

        # Determine health (using OSDPLStatus data when available)
        health = _determine_health(
            phase,
            conditions,
            osdplst_state=osdplst_state,
            health_ready=osdplst_health_ready,
            health_total=osdplst_health_total,
        )

        # Interpret status (using OSDPLStatus data when available)
        summary = _interpret_status(
            phase,
            conditions,
            is_updating,
            osdplst_state=osdplst_state,
            unhealthy_components=unhealthy_components,
            failed_services=failed_services,
        )

        result = GetOSDPLStatusOutput(
            name=input_data.name,
            namespace=input_data.namespace,
            phase=phase,
            health=health,
            openstack_version=current_version,
            target_version=target_version,
            is_updating=is_updating,
            is_ready=is_ready,
            conditions=conditions,
            services=services,
            services_ready=services_ready,
            services_total=services_total,
            summary=summary,
            observed_generation=status.get("observedGeneration"),
            endpoints=endpoints,
            last_updated=last_updated,
            timestamp=datetime.now(UTC).isoformat(),
            # OSDPLStatus fields
            osdplst_state=osdplst_state,
            osdplst_health=osdplst_health,
            osdplst_health_ready=osdplst_health_ready,
            osdplst_health_total=osdplst_health_total,
            osdplst_lcm_progress=osdplst_lcm_progress,
            osdplst_release=osdplst_release,
            component_health=component_health,
            lcm_services=lcm_services,
            unhealthy_components=unhealthy_components,
            failed_services=failed_services,
        )

        logger.info(
            "get_openstack_deployment_status_complete",
            name=input_data.name,
            phase=phase.value,
            health=health.value,
            osdplst_state=osdplst_state.value if osdplst_state else "unknown",
            is_updating=is_updating,
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
            "get_openstack_deployment_status_error",
            name=input_data.name,
            error=str(e),
        )
        raise ToolExecutionError(
            f"Failed to get OpenStack deployment status: {e}",
            tool_name="get_openstack_deployment_status",
        ) from e
