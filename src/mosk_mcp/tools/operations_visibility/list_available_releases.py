"""List available MOSK releases tool.

This module provides the list_available_releases MCP tool that retrieves
all available MOSK platform releases (ClusterRelease CRs) from the MCC
management cluster, including:
- Release names and versions
- Kubernetes versions
- Supported OpenStack releases
- Component versions (containerd, MCR, etc.)
- Upgrade path information from current cluster release

This tool is essential for:
- Discovering available MOSK versions for upgrade planning
- Understanding what OpenStack releases are supported by each MOSK version
- Checking component versions across releases

Safety Level: READ_ONLY
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from mosk_mcp.core.exceptions import ToolExecutionError
from mosk_mcp.observability.logging import get_logger


if TYPE_CHECKING:
    from mosk_mcp.adapters.kubernetes import KubernetesAdapter


logger = get_logger(__name__)


class OpenStackReleaseInfo(BaseModel):
    """Information about a supported OpenStack release."""

    id: str = Field(..., description="OpenStack release codename (e.g., 'caracal', 'epoxy')")
    description: str = Field(default="", description="Description of the release")


class ComponentVersions(BaseModel):
    """Component versions included in a MOSK release."""

    kubernetes: str = Field(default="unknown", description="Kubernetes version")
    containerd: str = Field(default="unknown", description="containerd version")
    mcr: str = Field(default="unknown", description="MCR (Mirantis Container Runtime) version")
    coredns: str = Field(default="unknown", description="CoreDNS version")
    etcd: str = Field(default="unknown", description="etcd version")
    calico: str = Field(default="unknown", description="Calico version")
    openstack_operator: str = Field(default="unknown", description="OpenStack operator version")
    tungstenfabric_operator: str = Field(
        default="unknown", description="Tungsten Fabric operator version"
    )


class ReleaseInfo(BaseModel):
    """Detailed information about a MOSK release."""

    name: str = Field(..., description="Release name (e.g., 'mosk-21-0-0-25-2')")
    version: str = Field(..., description="Version string (e.g., '21.0.0+25.2')")
    major_version: str = Field(default="", description="Major version series (e.g., '21.0')")
    description: str = Field(default="", description="Release description with component versions")
    components: ComponentVersions = Field(
        default_factory=ComponentVersions, description="Component versions in this release"
    )
    openstack_releases: list[OpenStackReleaseInfo] = Field(
        default_factory=list, description="Supported OpenStack releases"
    )
    is_current: bool = Field(
        default=False, description="Whether this is the current cluster release"
    )
    upgrade_available: bool = Field(
        default=False, description="Whether upgrade to this release is available"
    )


class UpgradePathInfo(BaseModel):
    """Information about available upgrade paths."""

    from_release: str = Field(..., description="Current release name")
    to_release: str = Field(..., description="Target release name")
    update_plan_exists: bool = Field(
        default=False, description="Whether a ClusterUpdatePlan exists for this upgrade"
    )
    update_plan_name: str | None = Field(
        default=None, description="Name of the ClusterUpdatePlan if it exists"
    )


class ListAvailableReleasesInput(BaseModel):
    """Input parameters for list_available_releases tool."""

    cluster_name: str | None = Field(
        default=None,
        description="Name of the Cluster CR to check current release. If not provided, will auto-discover.",
    )
    cluster_namespace: str = Field(
        default="default",
        description="Namespace where Cluster CR is located",
    )
    include_all_versions: bool = Field(
        default=True,
        description="Include all MOSK versions (True) or only versions newer than current (False)",
    )
    include_component_details: bool = Field(
        default=True,
        description="Include detailed component versions for each release",
    )


class ListAvailableReleasesOutput(BaseModel):
    """Output from list_available_releases tool."""

    current_release: str | None = Field(
        default=None, description="Current cluster release (if cluster_name provided)"
    )
    current_version: str | None = Field(default=None, description="Current version string")
    releases: list[ReleaseInfo] = Field(
        default_factory=list, description="List of available MOSK releases"
    )
    total_count: int = Field(..., description="Total number of releases found")
    upgrade_paths: list[UpgradePathInfo] = Field(
        default_factory=list, description="Available upgrade paths from current release"
    )
    newest_release: str | None = Field(
        default=None, description="Name of the newest available release"
    )
    recommendations: list[str] = Field(default_factory=list, description="Upgrade recommendations")
    timestamp: str = Field(..., description="Query timestamp")


def _parse_component_versions(description: str) -> ComponentVersions:
    """Parse component versions from release description string.

    Description format:
    kubernetes: v1.30.13
    containerd: 1.7.27m3
    mcr: 25.0.12m1
    ...
    """
    components = ComponentVersions()
    if not description:
        return components

    for raw_line in description.strip().split("\n"):
        stripped_line = raw_line.strip()
        if ":" not in stripped_line:
            continue
        key, value = stripped_line.split(":", 1)
        key = key.strip().lower().replace("-", "_")
        value = value.strip()

        if key == "kubernetes":
            components.kubernetes = value
        elif key == "containerd":
            components.containerd = value
        elif key == "mcr":
            components.mcr = value
        elif key == "coredns":
            components.coredns = value
        elif key == "etcd":
            components.etcd = value
        elif key == "calico":
            components.calico = value
        elif key == "openstack_operator":
            components.openstack_operator = value
        elif key == "tungstenfabric_operator":
            components.tungstenfabric_operator = value

    return components


def _extract_major_version(version: str) -> str:
    """Extract major version series from version string.

    Example: "21.0.0+25.2" -> "21.0"
    """
    if not version:
        return ""
    # Remove any build metadata after +
    base = version.split("+")[0]
    parts = base.split(".")
    if len(parts) >= 2:
        return f"{parts[0]}.{parts[1]}"
    return base


def _compare_versions(v1: str, v2: str) -> int:
    """Compare two MOSK version strings.

    Returns: -1 if v1 < v2, 0 if equal, 1 if v1 > v2
    """

    def normalize(v: str) -> tuple[int, ...]:
        # Remove leading 'mosk-' if present
        v = v.replace("mosk-", "")
        # Split by - or . and convert to integers
        parts = []
        for p in v.replace("-", ".").split("."):
            try:
                parts.append(int(p))
            except ValueError:
                # Handle suffixes like 'm1' by extracting number
                num = "".join(c for c in p if c.isdigit())
                parts.append(int(num) if num else 0)
        return tuple(parts)

    n1, n2 = normalize(v1), normalize(v2)
    if n1 < n2:
        return -1
    elif n1 > n2:
        return 1
    return 0


async def list_available_releases(
    mcc_adapter: KubernetesAdapter,
    input_data: ListAvailableReleasesInput,
) -> ListAvailableReleasesOutput:
    """List available MOSK releases from MCC cluster.

    Retrieves all ClusterRelease resources from the MCC management cluster
    and provides detailed information about each release including supported
    OpenStack versions and component details.

    This tool is useful for:
    - Discovering available MOSK versions for upgrade planning
    - Understanding OpenStack compatibility per MOSK version
    - Checking component versions before upgrades

    Args:
        mcc_adapter: Kubernetes adapter for MCC management cluster.
        input_data: Input parameters.

    Returns:
        ListAvailableReleasesOutput with all available releases and upgrade paths.

    Raises:
        ToolExecutionError: If operation fails.
    """
    logger.info(
        "list_available_releases_start",
        cluster_name=input_data.cluster_name,
        cluster_namespace=input_data.cluster_namespace,
    )

    try:
        # Get current cluster release if cluster_name provided
        current_release: str | None = None
        current_version: str | None = None

        if input_data.cluster_name:
            cluster = await mcc_adapter.get_cluster(
                name=input_data.cluster_name,
                namespace=input_data.cluster_namespace,
            )
            if cluster:
                provider_status = cluster.get("status", {}).get("providerStatus", {})
                provider_spec = cluster.get("spec", {}).get("providerSpec", {}).get("value", {})
                current_release = provider_status.get("release") or provider_spec.get("release")
                logger.debug("current_cluster_release", release=current_release)

        # Get all ClusterRelease resources
        all_releases = await mcc_adapter.list_cluster_releases()

        # Filter for MOSK releases only (name starts with 'mosk-')
        mosk_releases: list[dict[str, Any]] = [
            r for r in all_releases if r.get("metadata", {}).get("name", "").startswith("mosk-")
        ]

        logger.debug("found_mosk_releases", count=len(mosk_releases))

        # Parse releases
        releases: list[ReleaseInfo] = []
        for release in mosk_releases:
            metadata = release.get("metadata", {})
            spec = release.get("spec", {})

            name = metadata.get("name", "")
            version = spec.get("version", "")
            description = spec.get("description", "")

            # Parse OpenStack releases
            allowed_os = spec.get("allowedOpenstackReleases", [])
            openstack_releases = [
                OpenStackReleaseInfo(
                    id=os_rel.get("id", ""),
                    description=os_rel.get("description", "").strip(),
                )
                for os_rel in allowed_os
            ]

            # Parse component versions if requested
            components = ComponentVersions()
            if input_data.include_component_details:
                components = _parse_component_versions(description)

            # Check if this is the current release
            is_current = name == current_release

            # Get current version string if this is current release
            if is_current and version:
                current_version = version

            # Determine if upgrade is available (newer than current)
            upgrade_available = False
            if current_release and not is_current:
                upgrade_available = _compare_versions(name, current_release) > 0

            release_info = ReleaseInfo(
                name=name,
                version=version,
                major_version=_extract_major_version(version),
                description=description.strip(),
                components=components,
                openstack_releases=openstack_releases,
                is_current=is_current,
                upgrade_available=upgrade_available,
            )
            releases.append(release_info)

        # Sort releases by version (newest first)
        releases.sort(key=lambda r: r.name, reverse=True)

        # Filter if not including all versions
        if not input_data.include_all_versions and current_release:
            releases = [r for r in releases if r.upgrade_available or r.is_current]

        # Get newest release
        newest_release = releases[0].name if releases else None

        # Check for ClusterUpdatePlan resources to determine upgrade paths
        upgrade_paths: list[UpgradePathInfo] = []
        if current_release:
            try:
                update_plans = await mcc_adapter.list_custom_resources(
                    group="kaas.mirantis.com",
                    version="v1alpha1",
                    plural="clusterupdateplans",
                    namespace=input_data.cluster_namespace,
                    namespaced=True,
                )

                for plan in update_plans:
                    plan_spec = plan.get("spec", {})
                    source = plan_spec.get("source", "")
                    plan_name = plan.get("metadata", {}).get("name", "")

                    # Find the target release from the plan name
                    # Plan names are like "mosk-21.0.1" which maps to "mosk-21-0-1-25-2-1"
                    if source == current_release:
                        # Find matching target release
                        for rel in releases:
                            if rel.upgrade_available:
                                upgrade_paths.append(
                                    UpgradePathInfo(
                                        from_release=current_release,
                                        to_release=rel.name,
                                        update_plan_exists=True,
                                        update_plan_name=plan_name,
                                    )
                                )
                                break
            except Exception as e:
                logger.warning("failed_to_get_update_plans", error=str(e))

        # Generate recommendations
        recommendations: list[str] = []
        if current_release and newest_release and current_release != newest_release:
            current_idx = next((i for i, r in enumerate(releases) if r.is_current), -1)
            if current_idx > 0:
                versions_behind = current_idx
                recommendations.append(
                    f"Current release is {versions_behind} version(s) behind the latest ({newest_release})"
                )

                # Find the next upgrade target
                next_upgrade = next((r for r in reversed(releases) if r.upgrade_available), None)
                if next_upgrade:
                    recommendations.append(
                        f"Next recommended upgrade target: {next_upgrade.name} ({next_upgrade.version})"
                    )
        elif current_release == newest_release:
            recommendations.append("Cluster is running the latest available MOSK release")

        result = ListAvailableReleasesOutput(
            current_release=current_release,
            current_version=current_version,
            releases=releases,
            total_count=len(releases),
            upgrade_paths=upgrade_paths,
            newest_release=newest_release,
            recommendations=recommendations,
            timestamp=datetime.now(UTC).isoformat(),
        )

        logger.info(
            "list_available_releases_complete",
            total_releases=len(releases),
            current_release=current_release,
            newest_release=newest_release,
        )

        return result

    except Exception as e:
        logger.error(
            "list_available_releases_error",
            error=str(e),
        )
        raise ToolExecutionError(
            f"Failed to list available releases: {e}",
            tool_name="list_available_releases",
        ) from e
