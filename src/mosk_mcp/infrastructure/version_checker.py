"""MOSK Version Checker for compatibility validation.

This module provides utilities to:
- Detect MOSK platform version from the cluster
- Validate version compatibility (requires 25.1+)
- Add version warnings to tool outputs

The MOSK MCP tools are designed for MOSK 25.1+ and may not work correctly
with earlier versions due to CRD schema differences and API changes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

from mosk_mcp.observability.logging import get_logger


if TYPE_CHECKING:
    from mosk_mcp.adapters.kubernetes import KubernetesAdapter


logger = get_logger(__name__)

# Minimum supported MOSK version
MIN_SUPPORTED_VERSION = (25, 1)
MIN_SUPPORTED_VERSION_STR = "25.1"


class VersionCompatibility(str, Enum):
    """Version compatibility status."""

    COMPATIBLE = "compatible"  # Version >= 25.1
    UNSUPPORTED = "unsupported"  # Version < 25.1
    UNKNOWN = "unknown"  # Could not determine version


@dataclass
class MOSKVersionInfo:
    """MOSK platform version information.

    Attributes:
        cluster_release: Full cluster release name (e.g., 'mosk-21-0-2-25-2-2').
        version_string: Parsed version string (e.g., '25.2').
        major: Major version number.
        minor: Minor version number.
        patch: Patch version number (optional).
        compatibility: Compatibility status.
        raw_data: Raw version data from cluster.
        warnings: List of version-related warnings.
    """

    cluster_release: str | None = None
    version_string: str | None = None
    major: int | None = None
    minor: int | None = None
    patch: int | None = None
    compatibility: VersionCompatibility = VersionCompatibility.UNKNOWN
    raw_data: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def is_compatible(self) -> bool:
        """Check if version is compatible (>= 25.1)."""
        return self.compatibility == VersionCompatibility.COMPATIBLE

    @property
    def is_unsupported(self) -> bool:
        """Check if version is explicitly unsupported."""
        return self.compatibility == VersionCompatibility.UNSUPPORTED

    @property
    def version_tuple(self) -> tuple[int, int, int] | None:
        """Get version as tuple (major, minor, patch)."""
        if self.major is not None and self.minor is not None:
            return (self.major, self.minor, self.patch or 0)
        return None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "cluster_release": self.cluster_release,
            "version_string": self.version_string,
            "major": self.major,
            "minor": self.minor,
            "patch": self.patch,
            "compatibility": self.compatibility.value,
            "is_compatible": self.is_compatible,
            "warnings": self.warnings,
        }


def parse_mosk_version(release_name: str) -> tuple[int | None, int | None, int | None]:
    """Parse MOSK version from cluster release name.

    MOSK release names follow the pattern:
    - mosk-<major>-<minor>-<patch>-<mosk_version_parts>
    - Example: mosk-21-0-2-25-2-2 -> MOSK version is in the suffix

    The MOSK version (25.x) is typically in the last parts of the release name.

    Args:
        release_name: Cluster release name (e.g., 'mosk-21-0-2-25-2-2').

    Returns:
        Tuple of (major, minor, patch) version numbers.
    """
    if not release_name:
        return None, None, None

    # Remove 'mosk-' prefix if present
    name = release_name.lower()
    if name.startswith("mosk-"):
        name = name[5:]

    # Split by dash and extract version parts
    parts = name.split("-")

    # Try to find the MOSK version (25.x pattern)
    # The version is typically at the end of the release name
    # Pattern: mosk-<kubernetes_version>-<mosk_version>
    # Example: mosk-21-0-2-25-2-2 -> k8s is 21.0.2, mosk is 25.2.2

    # Look for version >= 25 in the parts (MOSK versioning started at 25)
    for i, part in enumerate(parts):
        try:
            major = int(part)
            if major >= 25 and i + 1 < len(parts):
                minor = int(parts[i + 1]) if parts[i + 1].isdigit() else 0
                patch = int(parts[i + 2]) if i + 2 < len(parts) and parts[i + 2].isdigit() else 0
                return major, minor, patch
        except ValueError:
            continue

    # Fallback: try parsing from spec.version if available
    # This handles ClusterRelease.spec.version format like "21.0.0"
    version_match = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", release_name)
    if version_match:
        major = int(version_match.group(1))
        minor = int(version_match.group(2))
        patch = int(version_match.group(3)) if version_match.group(3) else 0
        return major, minor, patch

    return None, None, None


def check_version_compatibility(
    major: int | None,
    minor: int | None,
) -> VersionCompatibility:
    """Check if version meets minimum requirements.

    Args:
        major: Major version number.
        minor: Minor version number.

    Returns:
        VersionCompatibility status.
    """
    if major is None or minor is None:
        return VersionCompatibility.UNKNOWN

    if (major, minor) >= MIN_SUPPORTED_VERSION:
        return VersionCompatibility.COMPATIBLE

    return VersionCompatibility.UNSUPPORTED


async def get_mosk_version(
    mcc_adapter: KubernetesAdapter,
    cluster_name: str = "mos",
    namespace: str = "default",
) -> MOSKVersionInfo:
    """Get MOSK version information from the cluster.

    Retrieves version from:
    1. Cluster CR's spec.providerSpec.value.release
    2. ClusterRelease CR's spec.version

    Args:
        mcc_adapter: Connected MCC Kubernetes adapter.
        cluster_name: Name of the MOSK cluster CR.
        namespace: Namespace of the cluster CR.

    Returns:
        MOSKVersionInfo with version details and compatibility status.
    """
    version_info = MOSKVersionInfo()

    try:
        # Get Cluster CR to find the release name
        cluster_data = await mcc_adapter.get_custom_resource(
            group="cluster.k8s.io",
            version="v1alpha1",
            plural="clusters",
            name=cluster_name,
            namespace=namespace,
        )

        if cluster_data:
            # Extract release from spec.providerSpec.value.release
            provider_spec = cluster_data.get("spec", {}).get("providerSpec", {}).get("value", {})
            release_name = provider_spec.get("release", "")

            version_info.cluster_release = release_name
            version_info.raw_data["cluster"] = {
                "name": cluster_name,
                "namespace": namespace,
                "release": release_name,
            }

            # Parse version from release name
            major, minor, patch = parse_mosk_version(release_name)
            version_info.major = major
            version_info.minor = minor
            version_info.patch = patch

            if major is not None and minor is not None:
                version_info.version_string = f"{major}.{minor}"
                if patch:
                    version_info.version_string += f".{patch}"

            # Check compatibility
            version_info.compatibility = check_version_compatibility(major, minor)

            # Try to get more details from ClusterRelease if available
            if release_name:
                try:
                    cluster_release = await mcc_adapter.get_custom_resource(
                        group="kaas.mirantis.com",
                        version="v1alpha1",
                        plural="clusterreleases",
                        name=release_name,
                    )
                    if cluster_release:
                        spec_version = cluster_release.get("spec", {}).get("version")
                        if spec_version:
                            version_info.raw_data["cluster_release_version"] = spec_version
                            # Try parsing from spec.version if we didn't get it from release name
                            if major is None:
                                major, minor, patch = parse_mosk_version(spec_version)
                                version_info.major = major
                                version_info.minor = minor
                                version_info.patch = patch
                                if major and minor:
                                    version_info.version_string = f"{major}.{minor}"
                                version_info.compatibility = check_version_compatibility(
                                    major, minor
                                )
                except Exception as e:
                    logger.debug("cluster_release_fetch_failed", error=str(e))

    except Exception as e:
        logger.warning("mosk_version_detection_failed", error=str(e))
        version_info.warnings.append(f"Could not detect MOSK version: {e}")

    # Add compatibility warnings
    if version_info.compatibility == VersionCompatibility.UNSUPPORTED:
        version_info.warnings.append(
            f"⚠️ MOSK version {version_info.version_string} is not supported. "
            f"This MCP requires MOSK {MIN_SUPPORTED_VERSION_STR}+. "
            "Some tools may not work correctly or return incorrect results."
        )
    elif version_info.compatibility == VersionCompatibility.UNKNOWN:
        version_info.warnings.append(
            f"⚠️ Could not determine MOSK version. "
            f"This MCP is designed for MOSK {MIN_SUPPORTED_VERSION_STR}+. "
            "Please verify your cluster version manually."
        )

    logger.info(
        "mosk_version_detected",
        version=version_info.version_string,
        release=version_info.cluster_release,
        compatibility=version_info.compatibility.value,
    )

    return version_info


def get_version_warning_message(version_info: MOSKVersionInfo) -> str | None:
    """Get a warning message for unsupported versions.

    Args:
        version_info: MOSK version information.

    Returns:
        Warning message string if version is unsupported, None otherwise.
    """
    if version_info.is_compatible:
        return None

    if version_info.is_unsupported:
        return (
            f"⚠️ WARNING: MOSK version {version_info.version_string} is not supported. "
            f"This MCP requires MOSK {MIN_SUPPORTED_VERSION_STR} or later. "
            f"Results may be incorrect or tools may fail."
        )

    if version_info.compatibility == VersionCompatibility.UNKNOWN:
        return (
            f"⚠️ WARNING: Could not determine MOSK version. "
            f"This MCP is designed for MOSK {MIN_SUPPORTED_VERSION_STR}+. "
            f"Please verify your cluster meets the minimum requirements."
        )

    return None


def add_version_warning_to_output(
    output: dict[str, Any],
    version_info: MOSKVersionInfo,
) -> dict[str, Any]:
    """Add version warning to tool output if needed.

    Args:
        output: Tool output dictionary.
        version_info: MOSK version information.

    Returns:
        Output dict with warnings added if applicable.
    """
    warning = get_version_warning_message(version_info)
    if warning:
        if "warnings" not in output:
            output["warnings"] = []
        if isinstance(output["warnings"], list):
            output["warnings"].insert(0, warning)

    return output


# Global version info cache for the session
_cached_version_info: MOSKVersionInfo | None = None


def get_cached_version_info() -> MOSKVersionInfo | None:
    """Get cached MOSK version info."""
    return _cached_version_info


def set_cached_version_info(version_info: MOSKVersionInfo) -> None:
    """Set cached MOSK version info."""
    global _cached_version_info
    _cached_version_info = version_info


def clear_cached_version_info() -> None:
    """Clear cached MOSK version info."""
    global _cached_version_info
    _cached_version_info = None
