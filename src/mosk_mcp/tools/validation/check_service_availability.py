"""Check OpenStack service availability tool.

Tier 2 validation: Probes OpenStack APIs to verify services are responding
and functional after upgrades or maintenance operations.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from mosk_mcp.observability.logging import get_logger


if TYPE_CHECKING:
    from mosk_mcp.adapters.openstack import OpenStackAdapter


logger = get_logger(__name__)


class ServiceStatus(str, Enum):
    """Service availability status."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


@dataclass
class ServiceCheckResult:
    """Result of checking a single service."""

    service_name: str
    status: ServiceStatus
    response_time_ms: float | None = None
    endpoint_count: int = 0
    agent_count: int = 0
    agents_up: int = 0
    error_message: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


class CheckServiceAvailabilityInput(BaseModel):
    """Input for check_service_availability tool."""

    services: list[str] | None = Field(
        default=None,
        description=(
            "Services to check. If not provided, checks all core services. "
            "Options: keystone, nova, neutron, glance, cinder, heat"
        ),
    )
    include_agents: bool = Field(
        default=True,
        description="Check service agents (nova-compute, neutron agents)",
    )
    timeout_seconds: int = Field(
        default=30,
        description="Timeout per service check in seconds",
        ge=5,
        le=120,
    )


class ServiceCheckOutput(BaseModel):
    """Output for a single service check."""

    service_name: str = Field(description="Service name")
    status: str = Field(description="Service status (healthy, degraded, unavailable)")
    response_time_ms: float | None = Field(
        default=None, description="API response time in milliseconds"
    )
    endpoint_count: int = Field(default=0, description="Number of registered endpoints")
    agent_count: int = Field(default=0, description="Total agent count")
    agents_up: int = Field(default=0, description="Number of agents up")
    error_message: str | None = Field(default=None, description="Error if check failed")
    details: dict[str, Any] = Field(default_factory=dict, description="Additional service details")


class CheckServiceAvailabilityOutput(BaseModel):
    """Output for check_service_availability tool."""

    overall_status: str = Field(description="Overall status (healthy, degraded, unavailable)")
    services_checked: int = Field(description="Number of services checked")
    services_healthy: int = Field(description="Number of healthy services")
    services_degraded: int = Field(description="Number of degraded services")
    services_unavailable: int = Field(description="Number of unavailable services")
    results: list[ServiceCheckOutput] = Field(description="Per-service check results")
    timestamp: str = Field(description="Check timestamp (ISO format)")
    duration_seconds: float = Field(description="Total check duration")
    recommendations: list[str] = Field(
        default_factory=list, description="Recommendations based on check results"
    )


# Core OpenStack services to check
CORE_SERVICES = ["keystone", "nova", "neutron", "glance", "cinder"]

# Optional services
OPTIONAL_SERVICES = ["heat", "octavia", "barbican", "manila"]


async def check_service_availability(
    adapter: OpenStackAdapter,
    input_data: CheckServiceAvailabilityInput,
) -> CheckServiceAvailabilityOutput:
    """Check OpenStack service availability by probing APIs.

    Performs the following checks for each service:
    - Keystone: Token issuance, service catalog, endpoint listing
    - Nova: Compute service list, hypervisor status
    - Neutron: Agent list and status
    - Glance: Image list
    - Cinder: Volume service list
    - Heat: Stack list (if enabled)

    Args:
        adapter: OpenStack adapter for API calls.
        input_data: Check configuration.

    Returns:
        Service availability status and per-service results.
    """
    start_time = datetime.now(UTC)
    logger.info(
        "starting_service_availability_check",
        services=input_data.services,
        include_agents=input_data.include_agents,
    )

    # Determine services to check
    services_to_check = input_data.services or CORE_SERVICES

    # Run checks concurrently
    tasks = []
    for service in services_to_check:
        tasks.append(
            _check_service(
                adapter,
                service,
                input_data.include_agents,
                input_data.timeout_seconds,
            )
        )

    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Process results
    check_results: list[ServiceCheckResult] = []
    for i, result in enumerate(results):
        service_name = services_to_check[i]
        if isinstance(result, Exception):
            check_results.append(
                ServiceCheckResult(
                    service_name=service_name,
                    status=ServiceStatus.UNAVAILABLE,
                    error_message=str(result),
                )
            )
        elif isinstance(result, ServiceCheckResult):
            check_results.append(result)

    # Calculate summary
    services_healthy = sum(1 for r in check_results if r.status == ServiceStatus.HEALTHY)
    services_degraded = sum(1 for r in check_results if r.status == ServiceStatus.DEGRADED)
    services_unavailable = sum(1 for r in check_results if r.status == ServiceStatus.UNAVAILABLE)

    # Determine overall status
    if services_unavailable > 0:
        overall_status = ServiceStatus.UNAVAILABLE
    elif services_degraded > 0:
        overall_status = ServiceStatus.DEGRADED
    else:
        overall_status = ServiceStatus.HEALTHY

    # Generate recommendations
    recommendations = _generate_recommendations(check_results)

    end_time = datetime.now(UTC)
    duration = (end_time - start_time).total_seconds()

    logger.info(
        "service_availability_check_complete",
        overall_status=overall_status.value,
        services_checked=len(check_results),
        services_healthy=services_healthy,
        services_degraded=services_degraded,
        services_unavailable=services_unavailable,
        duration_seconds=duration,
    )

    return CheckServiceAvailabilityOutput(
        overall_status=overall_status.value,
        services_checked=len(check_results),
        services_healthy=services_healthy,
        services_degraded=services_degraded,
        services_unavailable=services_unavailable,
        results=[
            ServiceCheckOutput(
                service_name=r.service_name,
                status=r.status.value,
                response_time_ms=r.response_time_ms,
                endpoint_count=r.endpoint_count,
                agent_count=r.agent_count,
                agents_up=r.agents_up,
                error_message=r.error_message,
                details=r.details,
            )
            for r in check_results
        ],
        timestamp=start_time.isoformat(),
        duration_seconds=round(duration, 2),
        recommendations=recommendations,
    )


async def _check_service(
    adapter: OpenStackAdapter,
    service_name: str,
    include_agents: bool,
    timeout: int,
) -> ServiceCheckResult:
    """Check a single OpenStack service.

    Args:
        adapter: OpenStack adapter.
        service_name: Service to check.
        include_agents: Whether to check agents.
        timeout: Timeout in seconds.

    Returns:
        Service check result.
    """
    try:
        check_func = SERVICE_CHECKERS.get(service_name)
        if not check_func:
            return ServiceCheckResult(
                service_name=service_name,
                status=ServiceStatus.UNKNOWN,
                error_message=f"No checker defined for service: {service_name}",
            )

        return await asyncio.wait_for(
            check_func(adapter, include_agents),
            timeout=timeout,
        )
    except TimeoutError:
        return ServiceCheckResult(
            service_name=service_name,
            status=ServiceStatus.UNAVAILABLE,
            error_message=f"Check timed out after {timeout}s",
        )
    except Exception as e:
        logger.warning(
            "service_check_failed",
            service=service_name,
            error=str(e),
        )
        return ServiceCheckResult(
            service_name=service_name,
            status=ServiceStatus.UNAVAILABLE,
            error_message=str(e),
        )


async def _check_keystone(
    adapter: OpenStackAdapter,
    include_agents: bool,
) -> ServiceCheckResult:
    """Check Keystone service availability."""
    start = datetime.now(UTC)
    details: dict[str, Any] = {}

    try:
        # Test token issuance
        token = await adapter.get_token()
        if not token:
            return ServiceCheckResult(
                service_name="keystone",
                status=ServiceStatus.UNAVAILABLE,
                error_message="Failed to obtain authentication token",
            )

        # Get endpoints
        endpoints = await adapter.list_endpoints()
        endpoint_count = len(endpoints) if endpoints else 0
        details["endpoints"] = endpoint_count

        # Get services
        services = await adapter.list_services()
        service_count = len(services) if services else 0
        details["services_registered"] = service_count

        # Get projects (basic check)
        projects = await adapter.list_projects(limit=5)
        details["projects_accessible"] = len(projects) if projects else 0

        response_time = (datetime.now(UTC) - start).total_seconds() * 1000

        return ServiceCheckResult(
            service_name="keystone",
            status=ServiceStatus.HEALTHY,
            response_time_ms=round(response_time, 2),
            endpoint_count=endpoint_count,
            details=details,
        )

    except Exception as e:
        return ServiceCheckResult(
            service_name="keystone",
            status=ServiceStatus.UNAVAILABLE,
            error_message=str(e),
        )


async def _check_nova(
    adapter: OpenStackAdapter,
    include_agents: bool,
) -> ServiceCheckResult:
    """Check Nova compute service availability."""
    start = datetime.now(UTC)
    details: dict[str, Any] = {}

    try:
        # List compute services
        compute_services = await adapter.list_compute_services()

        if not compute_services:
            return ServiceCheckResult(
                service_name="nova",
                status=ServiceStatus.DEGRADED,
                error_message="No compute services found",
            )

        agent_count = len(compute_services)
        agents_up = sum(1 for s in compute_services if s.state == "up")
        agents_enabled = sum(1 for s in compute_services if s.status == "enabled")

        details["compute_services"] = agent_count
        details["services_up"] = agents_up
        details["services_enabled"] = agents_enabled

        # List hypervisors (optional - may fail in some configs)
        try:
            hypervisors = await adapter.list_hypervisors()
            if hypervisors:
                total_vcpus = sum(h.vcpus for h in hypervisors)
                total_memory_gb = sum(h.memory_mb for h in hypervisors) // 1024
                details["hypervisors"] = len(hypervisors)
                details["total_vcpus"] = total_vcpus
                details["total_memory_gb"] = total_memory_gb
                details["hypervisors_available"] = True
        except Exception as e:
            logger.debug("hypervisor_stats_unavailable", error=str(e))
            details["hypervisors_available"] = False

        response_time = (datetime.now(UTC) - start).total_seconds() * 1000

        # Determine status
        if agents_up == 0:
            status = ServiceStatus.UNAVAILABLE
            error_msg = "All compute services are down"
        elif agents_up < agent_count:
            status = ServiceStatus.DEGRADED
            error_msg = f"{agent_count - agents_up} compute services are down"
        else:
            status = ServiceStatus.HEALTHY
            error_msg = None

        return ServiceCheckResult(
            service_name="nova",
            status=status,
            response_time_ms=round(response_time, 2),
            agent_count=agent_count,
            agents_up=agents_up,
            error_message=error_msg,
            details=details,
        )

    except Exception as e:
        return ServiceCheckResult(
            service_name="nova",
            status=ServiceStatus.UNAVAILABLE,
            error_message=str(e),
        )


async def _check_neutron(
    adapter: OpenStackAdapter,
    include_agents: bool,
) -> ServiceCheckResult:
    """Check Neutron network service availability."""
    start = datetime.now(UTC)
    details: dict[str, Any] = {}

    try:
        # List networks (basic API check)
        networks = await adapter.list_networks(limit=10)
        details["networks_found"] = len(networks) if networks else 0

        agent_count = 0
        agents_up = 0

        if include_agents:
            # List network agents
            agents = await adapter.list_network_agents()
            if agents:
                agent_count = len(agents)
                agents_up = sum(1 for a in agents if a.get("alive", False) is True)
                details["total_agents"] = agent_count
                details["agents_alive"] = agents_up

                # Count by type
                agent_types: dict[str, int] = {}
                for agent in agents:
                    agent_type = agent.get("agent_type", "unknown")
                    agent_types[agent_type] = agent_types.get(agent_type, 0) + 1
                details["agent_types"] = agent_types

        response_time = (datetime.now(UTC) - start).total_seconds() * 1000

        # Determine status
        if include_agents and agent_count > 0:
            if agents_up == 0:
                status = ServiceStatus.UNAVAILABLE
                error_msg = "All network agents are down"
            elif agents_up < agent_count:
                status = ServiceStatus.DEGRADED
                error_msg = f"{agent_count - agents_up} network agents are down"
            else:
                status = ServiceStatus.HEALTHY
                error_msg = None
        else:
            # No agents or not checking - base on API response
            status = ServiceStatus.HEALTHY
            error_msg = None

        return ServiceCheckResult(
            service_name="neutron",
            status=status,
            response_time_ms=round(response_time, 2),
            agent_count=agent_count,
            agents_up=agents_up,
            error_message=error_msg,
            details=details,
        )

    except Exception as e:
        return ServiceCheckResult(
            service_name="neutron",
            status=ServiceStatus.UNAVAILABLE,
            error_message=str(e),
        )


async def _check_glance(
    adapter: OpenStackAdapter,
    include_agents: bool,
) -> ServiceCheckResult:
    """Check Glance image service availability."""
    start = datetime.now(UTC)
    details: dict[str, Any] = {}

    try:
        # List images
        images = await adapter.list_images(limit=10)
        image_count = len(images) if images else 0
        details["images_found"] = image_count

        # Count active images
        if images:
            active_images = sum(1 for img in images if img.get("Status", "").lower() == "active")
            details["active_images"] = active_images

        response_time = (datetime.now(UTC) - start).total_seconds() * 1000

        return ServiceCheckResult(
            service_name="glance",
            status=ServiceStatus.HEALTHY,
            response_time_ms=round(response_time, 2),
            details=details,
        )

    except Exception as e:
        return ServiceCheckResult(
            service_name="glance",
            status=ServiceStatus.UNAVAILABLE,
            error_message=str(e),
        )


async def _check_cinder(
    adapter: OpenStackAdapter,
    include_agents: bool,
) -> ServiceCheckResult:
    """Check Cinder volume service availability."""
    start = datetime.now(UTC)
    details: dict[str, Any] = {}

    try:
        # List volume services
        volume_services = await adapter.list_volume_services()

        if not volume_services:
            return ServiceCheckResult(
                service_name="cinder",
                status=ServiceStatus.DEGRADED,
                error_message="No volume services found",
            )

        agent_count = len(volume_services)
        agents_up = sum(1 for s in volume_services if s.get("State", "").lower() == "up")
        details["volume_services"] = agent_count
        details["services_up"] = agents_up

        # Count by binary
        by_binary: dict[str, int] = {}
        for svc in volume_services:
            binary = svc.get("Binary", "unknown")
            by_binary[binary] = by_binary.get(binary, 0) + 1
        details["services_by_type"] = by_binary

        response_time = (datetime.now(UTC) - start).total_seconds() * 1000

        # Determine status
        if agents_up == 0:
            status = ServiceStatus.UNAVAILABLE
            error_msg = "All volume services are down"
        elif agents_up < agent_count:
            status = ServiceStatus.DEGRADED
            error_msg = f"{agent_count - agents_up} volume services are down"
        else:
            status = ServiceStatus.HEALTHY
            error_msg = None

        return ServiceCheckResult(
            service_name="cinder",
            status=status,
            response_time_ms=round(response_time, 2),
            agent_count=agent_count,
            agents_up=agents_up,
            error_message=error_msg,
            details=details,
        )

    except Exception as e:
        return ServiceCheckResult(
            service_name="cinder",
            status=ServiceStatus.UNAVAILABLE,
            error_message=str(e),
        )


async def _check_heat(
    adapter: OpenStackAdapter,
    include_agents: bool,
) -> ServiceCheckResult:
    """Check Heat orchestration service availability."""
    start = datetime.now(UTC)
    details: dict[str, Any] = {}

    try:
        # List stacks (basic API check)
        stacks = await adapter.list_stacks(limit=5)
        details["stacks_found"] = len(stacks) if stacks else 0

        response_time = (datetime.now(UTC) - start).total_seconds() * 1000

        return ServiceCheckResult(
            service_name="heat",
            status=ServiceStatus.HEALTHY,
            response_time_ms=round(response_time, 2),
            details=details,
        )

    except Exception as e:
        # Heat may not be deployed in all environments
        return ServiceCheckResult(
            service_name="heat",
            status=ServiceStatus.UNAVAILABLE,
            error_message=str(e),
        )


# Service checker mapping
SERVICE_CHECKERS = {
    "keystone": _check_keystone,
    "nova": _check_nova,
    "neutron": _check_neutron,
    "glance": _check_glance,
    "cinder": _check_cinder,
    "heat": _check_heat,
}


def _generate_recommendations(results: list[ServiceCheckResult]) -> list[str]:
    """Generate recommendations based on check results.

    Args:
        results: Service check results.

    Returns:
        List of recommendations.
    """
    recommendations = []

    for result in results:
        if result.status == ServiceStatus.UNAVAILABLE:
            recommendations.append(
                f"{result.service_name.upper()}: Service unavailable - "
                f"check pod status and logs. Error: {result.error_message}"
            )
        elif result.status == ServiceStatus.DEGRADED:
            if result.agents_up < result.agent_count:
                recommendations.append(
                    f"{result.service_name.upper()}: {result.agent_count - result.agents_up} "
                    f"agents down - investigate unhealthy agents"
                )
            else:
                recommendations.append(
                    f"{result.service_name.upper()}: Service degraded - {result.error_message}"
                )

    if not recommendations:
        recommendations.append("All services are healthy - no action required")

    return recommendations
