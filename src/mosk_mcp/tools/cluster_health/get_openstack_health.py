"""Get OpenStack services health status tool.

This module provides the get_openstack_health MCP tool for retrieving
comprehensive OpenStack health information including control plane services,
API endpoints, and compute hypervisor status.

Uses OSDPLStatus (osdplst) CR for accurate real-time status when available,
falling back to OSDPL CR status for legacy compatibility.

Safety Level: Read-only
"""

from __future__ import annotations


__all__ = [
    "GetOpenStackHealthInput",
    "GetOpenStackHealthOutput",
    "get_openstack_health",
]

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from mosk_mcp.core.exceptions import ToolExecutionError
from mosk_mcp.observability.logging import get_logger
from mosk_mcp.tools.cluster_health.models import (
    GetOpenStackHealthInput,
    GetOpenStackHealthOutput,
    HypervisorHealthInfo,
    ServiceHealthInfo,
)
from mosk_mcp.tools.common import score_to_health
from mosk_mcp.tools.common.constants import (
    COMPONENT_TO_LCM_NAME,
    CONTROL_PLANE_SERVICES,
    OSDPLST_APPLYING_STATES,
    UPGRADE_PHASES,
)
from mosk_mcp.tools.common.enums import HealthStatus
from mosk_mcp.tools.common.parsers import parse_health_ratio


if TYPE_CHECKING:
    from mosk_mcp.adapters.kubernetes import KubernetesAdapter


logger = get_logger(__name__)


def _calculate_control_plane_score(
    services: list[ServiceHealthInfo],
    osdpl_phase: str,
    is_upgrading: bool,
    osdplst_state: str | None = None,
    osdplst_health_ready: int | None = None,
    osdplst_health_total: int | None = None,
) -> int:
    """Calculate OpenStack control plane health score (0-100).

    Scoring breakdown:
    - Service/component health: 70 points (from osdplst health ratio if available)
    - OSDPL state: 20 points (from osdplst state if available, else legacy phase)
    - API endpoint health: 10 points (bonus from service health)

    Args:
        services: List of service health information.
        osdpl_phase: Current OSDPL phase (legacy).
        is_upgrading: Whether upgrade is in progress.
        osdplst_state: Real state from OSDPLStatus (APPLIED, APPLYING, FAILED).
        osdplst_health_ready: Number of healthy components from OSDPLStatus.
        osdplst_health_total: Total components from OSDPLStatus.

    Returns:
        Health score from 0-100.
    """
    score = 0

    # Service/component health (70 points)
    # Prefer osdplst health ratio if available
    if osdplst_health_total is not None and osdplst_health_total > 0:
        health_ratio = (
            osdplst_health_ready / osdplst_health_total
            if osdplst_health_ready and osdplst_health_total and osdplst_health_total > 0
            else 0
        )
        score += int(health_ratio * 70)
    elif services:
        # services is truthy here, so len(services) > 0 is guaranteed
        healthy_services = sum(1 for s in services if s.healthy)
        total_services = len(services)
        service_score = (healthy_services / total_services) * 70
        score += int(service_score)
    else:
        score += 70  # No services configured is OK

    # OSDPL state (20 points) - OSDPLStatus is required
    if osdplst_state:
        if osdplst_state == "APPLIED":
            score += 20
        elif osdplst_state in OSDPLST_APPLYING_STATES:
            score += 10  # Partial credit during apply/upgrade
        elif osdplst_state == "FAILED":
            score += 0
        else:
            score += 5  # Unknown states get minimal credit
    else:
        # OSDPLStatus is required in modern MOSK - give minimal score if missing
        score += 0

    # API endpoint health bonus (10 points)
    if services:
        # services is truthy here, so len(services) > 0 is guaranteed
        endpoints_healthy = sum(1 for s in services if s.endpoint_healthy)
        endpoint_score = (endpoints_healthy / len(services)) * 10
        score += int(endpoint_score)
    else:
        score += 10

    return min(100, max(0, score))


def _calculate_compute_score(
    hypervisors: list[HypervisorHealthInfo],
) -> int:
    """Calculate OpenStack compute health score (0-100).

    Args:
        hypervisors: List of hypervisor health information.

    Returns:
        Health score from 0-100.
    """
    if not hypervisors:
        # No hypervisors could mean compute is not deployed
        return 100

    # hypervisors is truthy here, so len(hypervisors) > 0 is guaranteed
    healthy = sum(1 for h in hypervisors if h.healthy)
    total = len(hypervisors)

    return int((healthy / total) * 100)


def _extract_service_health_from_osdplst(
    service_name: str,
    service_health: dict[str, Any],
    lcm_service: dict[str, Any] | None = None,
    endpoint: str | None = None,
) -> ServiceHealthInfo:
    """Extract health information for an OpenStack service from OSDPLStatus.

    Combines data from two OSDPLStatus sections:
    1. status.health - Component-level health (e.g., keystone.api: Ready)
    2. status.services - LCM service-level state (e.g., identity: APPLIED)

    Args:
        service_name: Name of the service (e.g., "keystone", "nova").
        service_health: Health data for the service from osdplst.status.health.
        lcm_service: LCM service data from osdplst.status.services (optional).
        endpoint: Service endpoint URL if available.

    Returns:
        ServiceHealthInfo object with component health and LCM state.
    """
    # Count components and their health status
    total_components = 0
    ready_components = 0
    issues: list[str] = []

    for component_name, component_data in service_health.items():
        if isinstance(component_data, dict):
            total_components += 1
            status = component_data.get("status", "Unknown")
            if status == "Ready":
                ready_components += 1
            else:
                issues.append(f"{service_name}/{component_name}: {status}")

    # Service is healthy if all components are Ready
    healthy = total_components > 0 and ready_components == total_components

    # Extract LCM service data if available
    lcm_state: str | None = None
    lcm_release: str | None = None
    lcm_timestamp: str | None = None

    if lcm_service:
        lcm_state = lcm_service.get("state")
        lcm_release = lcm_service.get("release")
        lcm_timestamp = lcm_service.get("timestamp")

        # Add issue if LCM state is not APPLIED
        if lcm_state and lcm_state != "APPLIED":
            issues.append(f"LCM state: {lcm_state}")

    # Endpoint health - assume healthy if we have an endpoint
    endpoint_healthy = endpoint is not None

    return ServiceHealthInfo(
        name=service_name,
        healthy=healthy,
        replicas_desired=total_components,  # Use component count as "replicas"
        replicas_ready=ready_components,
        replicas_available=ready_components,
        endpoint_healthy=endpoint_healthy,
        endpoint_latency_ms=None,
        lcm_state=lcm_state,
        lcm_release=lcm_release,
        lcm_timestamp=lcm_timestamp,
        issues=issues,
    )


def _extract_hypervisor_health(hypervisor_data: dict[str, Any]) -> HypervisorHealthInfo:
    """Extract health information for a Nova hypervisor.

    Args:
        hypervisor_data: Hypervisor data from Nova API or Machine CR.

    Returns:
        HypervisorHealthInfo object.
    """
    # Handle both Nova API response and simplified formats
    hostname = hypervisor_data.get(
        "hypervisor_hostname", hypervisor_data.get("hostname", "unknown")
    )
    status = hypervisor_data.get("status", "unknown")
    state = hypervisor_data.get("state", "unknown")

    # Hypervisor is healthy if status=enabled and state=up
    healthy = status.lower() == "enabled" and state.lower() == "up"

    return HypervisorHealthInfo(
        hostname=hostname,
        status=status,
        state=state,
        healthy=healthy,
        vcpus_used=hypervisor_data.get("vcpus_used", 0),
        vcpus_total=hypervisor_data.get("vcpus", 0),
        memory_used_mb=hypervisor_data.get("memory_mb_used", 0),
        memory_total_mb=hypervisor_data.get("memory_mb", 0),
        running_vms=hypervisor_data.get("running_vms", 0),
    )


def _generate_recommendations(
    control_score: int,
    compute_score: int,
    services: list[ServiceHealthInfo],
    hypervisors: list[HypervisorHealthInfo],
    is_upgrading: bool,
    osdpl_phase: str,
    osdplst_state: str | None = None,
    osdplst_health_ready: int | None = None,
    osdplst_health_total: int | None = None,
) -> list[str]:
    """Generate recommendations based on health status.

    Args:
        control_score: Control plane health score.
        compute_score: Compute health score.
        services: Service health information.
        hypervisors: Hypervisor health information.
        is_upgrading: Whether upgrade is in progress.
        osdpl_phase: Current OSDPL phase (legacy).
        osdplst_state: Real state from OSDPLStatus.
        osdplst_health_ready: Number of healthy components from OSDPLStatus.
        osdplst_health_total: Total components from OSDPLStatus.

    Returns:
        List of recommendations.
    """
    recommendations: list[str] = []

    # Check osdplst state first, then fall back to legacy phase
    if osdplst_state:
        if osdplst_state in OSDPLST_APPLYING_STATES:
            recommendations.append(
                f"Deployment state is {osdplst_state} - monitor OSDPLStatus and avoid maintenance operations"
            )
        elif osdplst_state == "FAILED":
            recommendations.append(
                "OSDPLStatus is in FAILED state - check osdplst conditions and controller logs"
            )
    else:
        if is_upgrading:
            recommendations.append(
                "Upgrade in progress - monitor OSDPL status and avoid maintenance operations"
            )
        if osdpl_phase == "Failed":
            recommendations.append(
                "OSDPL is in Failed state - check OSDPL conditions and controller logs"
            )

    # Check for unhealthy components from osdplst
    if osdplst_health_total and osdplst_health_ready is not None:
        unhealthy_count = osdplst_health_total - osdplst_health_ready
        if unhealthy_count > 0:
            recommendations.append(
                f"{unhealthy_count} component(s) not healthy - check OSDPLStatus health section for details"
            )

    # Service recommendations
    unhealthy_services = [s for s in services if not s.healthy]
    for svc in unhealthy_services[:3]:  # Limit to first 3
        recommendations.append(f"Service {svc.name} unhealthy - check {svc.name} pods and logs")

    # Hypervisor recommendations
    unhealthy_hypervisors = [h for h in hypervisors if not h.healthy]
    if unhealthy_hypervisors:
        recommendations.append(
            f"{len(unhealthy_hypervisors)} hypervisor(s) unhealthy - "
            "check nova-compute services and Machine status"
        )

    # Capacity recommendations
    high_util_hypervisors = [
        h for h in hypervisors if h.vcpus_total > 0 and (h.vcpus_used / h.vcpus_total) > 0.9
    ]
    if high_util_hypervisors:
        recommendations.append(
            f"{len(high_util_hypervisors)} hypervisor(s) at >90% CPU - "
            "consider adding compute capacity"
        )

    return recommendations[:10]


async def get_openstack_health(
    kubernetes_adapter: KubernetesAdapter,
    input_data: GetOpenStackHealthInput,
    mcc_adapter: KubernetesAdapter | None = None,
) -> GetOpenStackHealthOutput:
    """Get OpenStack services health status.

    This tool retrieves comprehensive health information about OpenStack
    services including control plane health, API endpoints, and compute
    hypervisor status.

    Safety Level: Read-only

    Args:
        kubernetes_adapter: MOSK Kubernetes adapter for OSDPL and OpenStack services.
        input_data: Input parameters for the query.
        mcc_adapter: Optional MCC Kubernetes adapter for Machine CRs (compute nodes).
            If not provided, hypervisor status from Machines will be skipped.

    Returns:
        GetOpenStackHealthOutput with OpenStack health information.

    Raises:
        ToolExecutionError: If health check fails.

    Example:
        >>> health = await get_openstack_health(
        ...     mosk_adapter, GetOpenStackHealthInput(), mcc_adapter
        ... )
        >>> print(f"Control plane: {health.control_plane_health}")
        >>> print(f"Compute: {health.compute_health}")
    """
    logger.info(
        "getting_openstack_health",
        osdpl_name=input_data.osdpl_name,
        namespace=input_data.namespace,
    )

    try:
        timestamp = datetime.now(UTC).isoformat()
        issues: list[str] = []

        # Get OSDPL status
        osdpl = await kubernetes_adapter.get_openstack_deployment(
            name=input_data.osdpl_name,
            namespace=input_data.namespace,
        )

        if not osdpl:
            raise ToolExecutionError(
                message=f"OpenStackDeployment '{input_data.osdpl_name}' not found",
                tool_name="get_openstack_health",
                details={"osdpl_name": input_data.osdpl_name},
            )

        # Extract OSDPL info (legacy)
        spec = osdpl.get("spec", {})
        status = osdpl.get("status", {})

        osdpl_phase = status.get("phase", "Unknown")
        openstack_version = status.get("openStackVersion", spec.get("openStackVersion", "unknown"))
        is_upgrading = osdpl_phase in UPGRADE_PHASES

        # Get OSDPLStatus (osdplst) for real status - this is the source of truth
        osdplst_state: str | None = None
        osdplst_health: str | None = None
        osdplst_health_ready: int | None = None
        osdplst_health_total: int | None = None
        osdplst_health_details: dict[str, Any] = {}  # Full health per-service data
        osdplst_lcm_services: dict[str, Any] = {}  # LCM service state/release data
        mosk_release: str | None = None

        try:
            osdplst = await kubernetes_adapter.get_openstack_deployment_status(
                name=input_data.osdpl_name,
                namespace=input_data.namespace,
            )
            if osdplst:
                osdplst_status = osdplst.get("status", {})
                osdpl_section = osdplst_status.get("osdpl", {})

                # Extract state and health from osdplst
                osdplst_state = osdpl_section.get("state")
                osdplst_health = osdpl_section.get("health")
                mosk_release = osdpl_section.get("release")

                # Extract detailed health per-service (for service health extraction)
                # Structure: {keystone: {api: {status: Ready}}, nova: {...}, ...}
                osdplst_health_details = osdplst_status.get("health", {})

                # Extract LCM services data (for state, release, timestamp)
                # Structure: {block-storage: {state: APPLIED, release: ...}, ...}
                osdplst_lcm_services = osdplst_status.get("services", {})

                # Get OpenStack version from osdplst if available
                if osdpl_section.get("openstackVersion"):
                    openstack_version = osdpl_section.get("openstackVersion")

                # Parse health ratio
                if osdplst_health:
                    osdplst_health_ready, osdplst_health_total = parse_health_ratio(osdplst_health)

                # Update is_upgrading based on osdplst state
                if osdplst_state:
                    is_upgrading = osdplst_state in OSDPLST_APPLYING_STATES

                logger.debug(
                    "osdplst_data_retrieved",
                    state=osdplst_state,
                    health=osdplst_health,
                    health_ready=osdplst_health_ready,
                    health_total=osdplst_health_total,
                    health_services_count=len(osdplst_health_details),
                    lcm_services_count=len(osdplst_lcm_services),
                )

        except Exception as e:
            logger.warning("failed_to_get_osdplst", error=str(e))
            issues.append(f"Could not retrieve OSDPLStatus: {e}")

        # Check for failed state in OSDPLStatus
        if osdplst_state == "FAILED":
            issues.append("OSDPLStatus is in FAILED state")

        # Get endpoints (may be in OSDPL status)
        endpoints: dict[str, str] = {}
        if input_data.include_endpoints:
            endpoints = status.get("endpoints", {})

        # Extract service health from osdplst.status.health and osdplst.status.services
        # health: component-level Ready status (keystone.api: Ready)
        # services: LCM service-level state (identity: APPLIED, release: ...)
        services: list[ServiceHealthInfo] = []

        if input_data.include_services and osdplst_health_details:
            for service_name in CONTROL_PLANE_SERVICES:
                if service_name in osdplst_health_details:
                    svc_health = osdplst_health_details[service_name]
                    endpoint = endpoints.get(service_name)

                    # Look up corresponding LCM service using mapping
                    # e.g., "nova" -> "compute", "cinder" -> "block-storage"
                    lcm_name = COMPONENT_TO_LCM_NAME.get(service_name)
                    lcm_service = osdplst_lcm_services.get(lcm_name) if lcm_name else None

                    service_health = _extract_service_health_from_osdplst(
                        service_name=service_name,
                        service_health=svc_health,
                        lcm_service=lcm_service,
                        endpoint=endpoint,
                    )
                    services.append(service_health)

                    if not service_health.healthy:
                        issues.append(f"Service {service_name} is unhealthy")

        # Get hypervisor status (from Machines labeled as compute/worker)
        # Machines are in the MOSK cluster namespace on MCC (e.g., "lab" namespace)
        # Requires MCC adapter since Machine CRDs are on the management cluster
        hypervisors: list[HypervisorHealthInfo] = []
        if mcc_adapter:
            try:
                # Discover the namespace where MOSK Machines are located
                mosk_machines_namespace = await mcc_adapter.get_mosk_machines_namespace()
                # NOTE: Do NOT fall back to "*" (all namespaces) as this requires
                # cluster-wide RBAC permissions that many users don't have.
                # If namespace discovery fails, skip hypervisor status from Machines.
                if not mosk_machines_namespace:
                    logger.debug(
                        "skipping_machine_hypervisor_status",
                        reason="Could not discover MOSK machines namespace",
                    )
                    # Skip to next section - hypervisor list will be empty
                    raise ValueError("Namespace discovery failed")

                machines_namespace = mosk_machines_namespace

                logger.debug(
                    "querying_compute_machines",
                    namespace=machines_namespace,
                )

                # Query compute nodes using the MOSK label
                compute_machines = await mcc_adapter.list_machines(
                    namespace=machines_namespace,
                    label_selector="openstack-compute-node=enabled",
                )

                for machine in compute_machines:
                    machine_status = machine.get("status", {})
                    machine_name = machine.get("metadata", {}).get("name", "unknown")

                    # Determine health from machine status
                    phase = machine_status.get("phase", "Unknown")
                    ready = phase == "Ready"

                    hypervisor_data = {
                        "hostname": machine_name,
                        "status": "enabled" if ready else "disabled",
                        "state": "up" if ready else "down",
                        "vcpus_used": 0,  # Would need Nova API for actual values
                        "vcpus": 0,
                        "memory_mb_used": 0,
                        "memory_mb": 0,
                        "running_vms": 0,
                    }

                    hypervisor = _extract_hypervisor_health(hypervisor_data)
                    hypervisors.append(hypervisor)

                    if not hypervisor.healthy:
                        issues.append(f"Hypervisor {machine_name} is not healthy")

            except ValueError:
                # Namespace discovery failed - this is expected with restricted RBAC
                # Don't add to issues as it's not a real error
                pass
            except Exception as e:
                logger.warning("failed_to_get_hypervisors", error=str(e))
                # Only add to issues if it's a real error, not namespace discovery
                if "Namespace discovery" not in str(e):
                    issues.append(f"Could not retrieve hypervisor status: {e}")
        else:
            logger.debug("skipping_hypervisor_check_no_mcc_adapter")

        # Calculate scores using osdplst data when available
        control_plane_score = _calculate_control_plane_score(
            services=services,
            osdpl_phase=osdpl_phase,
            is_upgrading=is_upgrading,
            osdplst_state=osdplst_state,
            osdplst_health_ready=osdplst_health_ready,
            osdplst_health_total=osdplst_health_total,
        )
        compute_score = _calculate_compute_score(hypervisors)

        control_plane_health = score_to_health(control_plane_score)
        compute_health = score_to_health(compute_score)

        # Generate message - prefer osdplst data when available
        healthy_services = sum(1 for s in services if s.healthy)
        healthy_hypervisors = sum(1 for h in hypervisors if h.healthy)

        if control_plane_health == HealthStatus.HEALTHY and compute_health == HealthStatus.HEALTHY:
            if osdplst_health:
                message = (
                    f"OpenStack healthy: state={osdplst_state}, health={osdplst_health}, "
                    f"{healthy_hypervisors}/{len(hypervisors)} hypervisors"
                )
            else:
                message = (
                    f"OpenStack healthy: {healthy_services}/{len(services)} services, "
                    f"{healthy_hypervisors}/{len(hypervisors)} hypervisors"
                )
        elif osdplst_state:
            message = (
                f"OpenStack degraded: state={osdplst_state}, health={osdplst_health or 'unknown'}, "
                f"{len(hypervisors) - healthy_hypervisors} unhealthy hypervisors"
            )
        else:
            message = (
                f"OpenStack degraded: phase={osdpl_phase}, "
                f"{len(services) - healthy_services} unhealthy services, "
                f"{len(hypervisors) - healthy_hypervisors} unhealthy hypervisors"
            )

        # Generate recommendations
        recommendations = _generate_recommendations(
            control_score=control_plane_score,
            compute_score=compute_score,
            services=services,
            hypervisors=hypervisors,
            is_upgrading=is_upgrading,
            osdpl_phase=osdpl_phase,
            osdplst_state=osdplst_state,
            osdplst_health_ready=osdplst_health_ready,
            osdplst_health_total=osdplst_health_total,
        )

        output = GetOpenStackHealthOutput(
            control_plane_health=control_plane_health,
            compute_health=compute_health,
            control_plane_score=control_plane_score,
            compute_score=compute_score,
            message=message,
            osdpl_phase=osdpl_phase,
            openstack_version=openstack_version,
            is_upgrading=is_upgrading,
            osdplst_state=osdplst_state,
            osdplst_health=osdplst_health,
            osdplst_health_ready=osdplst_health_ready,
            osdplst_health_total=osdplst_health_total,
            mosk_release=mosk_release,
            services_total=len(services),
            services_healthy=healthy_services,
            services=services if input_data.include_services else [],
            hypervisors_total=len(hypervisors),
            hypervisors_healthy=healthy_hypervisors,
            hypervisors=hypervisors,
            endpoints=endpoints if input_data.include_endpoints else {},
            issues=issues,
            recommendations=recommendations,
            timestamp=timestamp,
        )

        logger.info(
            "openstack_health_retrieved",
            control_health=control_plane_health.value,
            compute_health=compute_health.value,
            control_score=control_plane_score,
            compute_score=compute_score,
        )

        return output

    except ToolExecutionError:
        raise
    except Exception as e:
        logger.error("get_openstack_health_failed", error=str(e))
        raise ToolExecutionError(
            message=f"Failed to get OpenStack health: {e}",
            tool_name="get_openstack_health",
            details={"error": str(e)},
        ) from e
