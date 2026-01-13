"""Get component versions tool.

This module provides the get_component_versions tool that retrieves
current vs target versions for all OpenStack services, identifying
components that are out of sync during upgrades.

Safety Level: READ_ONLY
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from mosk_mcp.core.exceptions import ResourceNotFoundError, ToolExecutionError
from mosk_mcp.observability.logging import get_logger
from mosk_mcp.tools.operations_visibility.models import (
    ComponentVersion,
    GetComponentVersionsInput,
    GetComponentVersionsOutput,
)


if TYPE_CHECKING:
    from mosk_mcp.adapters.kubernetes import KubernetesAdapter


logger = get_logger(__name__)


# OpenStack services with their subcomponents
OPENSTACK_SERVICE_COMPONENTS = {
    "keystone": ["api"],
    "glance": ["api", "registry"],
    "nova": ["api", "conductor", "scheduler", "compute", "novncproxy"],
    "neutron": ["server", "l3-agent", "dhcp-agent", "metadata-agent", "openvswitch-agent"],
    "cinder": ["api", "scheduler", "volume"],
    "heat": ["api", "engine"],
    "horizon": ["dashboard"],
    "octavia": ["api", "worker", "housekeeping", "health-manager"],
    "barbican": ["api"],
    "manila": ["api", "scheduler", "share"],
    "designate": ["api", "central", "worker", "producer", "mdns"],
    "placement": ["api"],
    "ironic": ["api", "conductor"],
}


def _extract_version_from_image(image: str) -> str:
    """Extract version from container image tag.

    Args:
        image: Full container image with tag.

    Returns:
        Version string extracted from tag.
    """
    if not image:
        return "unknown"

    # Extract tag from image
    if ":" in image:
        tag = image.split(":")[-1]
        # Handle digest-only references
        if tag.startswith("sha256"):
            return "sha256"
        return tag
    return "latest"


def _get_service_deployments(
    deployments: list[dict[str, Any]],
    service_name: str,
) -> list[dict[str, Any]]:
    """Filter deployments for a specific OpenStack service.

    Args:
        deployments: List of all deployments.
        service_name: OpenStack service name (e.g., "nova").

    Returns:
        List of deployments for the service.
    """
    matching = []
    for deploy in deployments:
        name = deploy.get("metadata", {}).get("name", "")
        labels = deploy.get("metadata", {}).get("labels", {})

        # Check if deployment belongs to this service
        app_label = labels.get("application", "")
        component_label = labels.get("component", "")

        if (
            service_name in name
            or app_label == service_name
            or component_label.startswith(service_name)
        ):
            matching.append(deploy)

    return matching


def _parse_deployment_version(
    deployment: dict[str, Any],
    service_name: str,
    target_version: str,
    include_containers: bool,
) -> ComponentVersion | None:
    """Parse version information from a deployment.

    Args:
        deployment: Deployment resource.
        service_name: Parent service name.
        target_version: Target OpenStack version.
        include_containers: Whether to include container image.

    Returns:
        ComponentVersion or None if not applicable.
    """
    metadata = deployment.get("metadata", {})
    spec = deployment.get("spec", {})
    status = deployment.get("status", {})
    name = metadata.get("name", "")
    labels = metadata.get("labels", {})

    # Determine component type from name or labels
    component_type = labels.get("component", name)
    if service_name in component_type:
        component_type = component_type.replace(f"{service_name}-", "")

    # Get container image
    containers = spec.get("template", {}).get("spec", {}).get("containers", [])
    image = ""
    current_version = "unknown"

    if containers:
        image = containers[0].get("image", "")
        current_version = _extract_version_from_image(image)

    # Also check chart version from annotations
    chart_version = metadata.get("annotations", {}).get("meta.helm.sh/release-name")

    # Determine if current
    is_current = current_version == target_version or (
        status.get("updatedReplicas", 0) == status.get("replicas", 0)
        and status.get("availableReplicas", 0) == status.get("replicas", 0)
    )

    return ComponentVersion(
        component=name,
        service_type=component_type,
        current_version=current_version,
        target_version=target_version,
        is_current=is_current,
        image=image if include_containers else None,
        chart_version=chart_version,
    )


async def get_component_versions(
    kubernetes_adapter: KubernetesAdapter,
    input_data: GetComponentVersionsInput,
    mcc_adapter: KubernetesAdapter | None = None,
) -> GetComponentVersionsOutput:
    """Get component versions for all OpenStack services.

    Retrieves current and target versions for all OpenStack service
    components, identifying which are out of sync. Also retrieves
    OSDPL controller version and MCC cluster version if available.

    Args:
        kubernetes_adapter: Kubernetes client adapter (MOSK cluster).
        input_data: Input parameters.
        mcc_adapter: Optional MCC cluster adapter for cluster version info.

    Returns:
        Component version information.

    Raises:
        ResourceNotFoundError: If OSDPL is not found.
        ToolExecutionError: If operation fails.
    """
    logger.info(
        "get_component_versions_start",
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

        # Get OSDPL controller version from status.version
        osdpl_controller_version = status.get("version")

        # Get MCC cluster versions and LCM agent info if MCC adapter is available
        mcc_kaas_release = None
        mcc_cluster_release = None
        mosk_release = None
        lcm_agent_version = None
        ucp_version = None

        if mcc_adapter:
            try:
                # Get the management cluster (kaas-mgmt) to extract version info
                clusters = await mcc_adapter.list_custom_resources(
                    group="cluster.k8s.io",
                    version="v1alpha1",
                    plural="clusters",
                    namespace="default",
                )
                for cluster in clusters:
                    cluster_name = cluster.get("metadata", {}).get("name", "")
                    # Look for management cluster (kaas-mgmt)
                    if cluster_name == "kaas-mgmt":
                        provider_spec = (
                            cluster.get("spec", {}).get("providerSpec", {}).get("value", {})
                        )
                        # Get KaaS release from kaas.release
                        mcc_kaas_release = provider_spec.get("kaas", {}).get("release")
                        # Get cluster release from release
                        mcc_cluster_release = provider_spec.get("release")
                        break
            except Exception as e:
                logger.warning(
                    "failed_to_get_mcc_versions",
                    error=str(e),
                )

            # Get MOSK cluster release and LCM agent version
            try:
                # Find the MOSK child cluster - search all namespaces since
                # child clusters can be in any namespace (e.g., 'lab', 'production')
                clusters = await mcc_adapter.list_custom_resources(
                    group="cluster.k8s.io",
                    version="v1alpha1",
                    plural="clusters",
                    namespace="*",  # Search all namespaces
                )
                for cluster in clusters:
                    cluster_name = cluster.get("metadata", {}).get("name", "")
                    # Skip management cluster
                    if cluster_name == "kaas-mgmt":
                        continue
                    provider_spec = cluster.get("spec", {}).get("providerSpec", {}).get("value", {})
                    release = provider_spec.get("release", "")
                    # Look for MOSK release (starts with mosk-)
                    if release.startswith("mosk-"):
                        mosk_release = release
                        break

                # Get LCM agent version from LCMMachines
                if mosk_release:
                    cluster_namespace = cluster.get("metadata", {}).get("namespace", "default")
                    lcmmachines = await mcc_adapter.list_custom_resources(
                        group="lcm.mirantis.com",
                        version="v1alpha1",
                        plural="lcmmachines",
                        namespace=cluster_namespace,
                    )
                    if lcmmachines:
                        # Get version from first machine (all should be same)
                        first_machine = lcmmachines[0]
                        lcm_status = first_machine.get("status", {})
                        lcm_agent_version = lcm_status.get("agentVersion")
                        lcm_components = lcm_status.get("components", {})
                        ucp_version = lcm_components.get("ucpVersion")
            except Exception as e:
                logger.warning(
                    "failed_to_get_mosk_versions",
                    error=str(e),
                )

        # Get versions
        # Note: OSDPL uses openstack_version (snake_case) in spec
        target_version = spec.get("openstack_version") or spec.get("openStackVersion") or "unknown"
        current_version = (
            status.get("openstack_version") or status.get("openStackVersion") or target_version
        )

        # Get all deployments in the namespace
        deployments = await kubernetes_adapter.list(
            kind="Deployment",
            namespace=input_data.namespace,
            label_selector="application",  # OpenStack deployments have application label
        )

        # Get StatefulSets as well (some services use StatefulSets)
        statefulsets = await kubernetes_adapter.list(
            kind="StatefulSet",
            namespace=input_data.namespace,
            label_selector="application",
        )

        all_workloads = deployments + statefulsets

        # Parse component versions
        components: list[ComponentVersion] = []
        out_of_sync: list[str] = []

        for service_name in OPENSTACK_SERVICE_COMPONENTS:
            # Check if service is enabled
            svc_spec = spec.get("services", {}).get(service_name, {})
            if svc_spec and not svc_spec.get("enabled", True):
                continue

            # Find deployments for this service
            service_workloads = _get_service_deployments(all_workloads, service_name)

            for workload in service_workloads:
                comp_version = _parse_deployment_version(
                    deployment=workload,
                    service_name=service_name,
                    target_version=target_version,
                    include_containers=input_data.include_containers,
                )
                if comp_version:
                    components.append(comp_version)
                    if not comp_version.is_current:
                        out_of_sync.append(comp_version.component)

        # Sort components by name
        components.sort(key=lambda c: c.component)

        # Count stats
        components_current = sum(1 for c in components if c.is_current)
        components_total = len(components)
        versions_match = len(out_of_sync) == 0

        result = GetComponentVersionsOutput(
            name=input_data.name,
            namespace=input_data.namespace,
            openstack_version_current=current_version,
            openstack_version_target=target_version,
            osdpl_controller_version=osdpl_controller_version,
            mcc_kaas_release=mcc_kaas_release,
            mcc_cluster_release=mcc_cluster_release,
            mosk_release=mosk_release,
            lcm_agent_version=lcm_agent_version,
            ucp_version=ucp_version,
            versions_match=versions_match,
            components=components,
            components_current=components_current,
            components_total=components_total,
            out_of_sync_components=out_of_sync,
            timestamp=datetime.now(UTC).isoformat(),
        )

        logger.info(
            "get_component_versions_complete",
            name=input_data.name,
            components_total=components_total,
            components_current=components_current,
            versions_match=versions_match,
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
            "get_component_versions_error",
            name=input_data.name,
            error=str(e),
        )
        raise ToolExecutionError(
            f"Failed to get component versions: {e}",
            tool_name="get_component_versions",
        ) from e
