"""Apply Cluster Release patch tool for MOSK platform upgrades.

This module provides the apply_cluster_release_patch MCP tool for changing
the release version of a MOSK cluster, which triggers a platform upgrade.

IMPORTANT SAFETY CONSTRAINTS:
- ONLY spec.providerSpec.value.release can be modified
- Requires valid CRQ number for audit compliance
- Validates target release exists in ClusterRelease resources
- Uses MCC kubeconfig (management cluster) for operations

Safety Level: Privileged (requires CRQ)
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from mosk_mcp.auth.crq import get_crq_validator
from mosk_mcp.auth.rbac import ToolSafetyLevel
from mosk_mcp.core.exceptions import (
    ResourceNotFoundError,
    ToolExecutionError,
    ValidationError,
)
from mosk_mcp.observability.logging import get_logger


if TYPE_CHECKING:
    from mosk_mcp.adapters.kubernetes import KubernetesAdapter


logger = get_logger(__name__)


# Tool metadata
TOOL_NAME = "apply_cluster_release_patch"
TOOL_SAFETY_LEVEL = ToolSafetyLevel.PRIVILEGED
TOOL_DESCRIPTION = (
    "Change MOSK cluster release version to trigger a platform upgrade. "
    "PRIVILEGED: Requires valid CRQ number. "
    "ONLY allows changing spec.providerSpec.value.release - no other modifications permitted."
)


class ApplyClusterReleasePatchInput(BaseModel):
    """Input parameters for apply_cluster_release_patch tool.

    Attributes:
        cluster_name: Name of the Cluster CR to patch (e.g., 'mos').
        namespace: Kubernetes namespace where the Cluster is defined.
        target_release: Target MOSK release version (e.g., 'mosk-21-0-2-25-2-2').
        crq_number: Change request number for audit compliance.
        dry_run: Preview the patch without applying.
    """

    cluster_name: str = Field(
        ...,
        description="Name of the Cluster CR (e.g., 'mos')",
    )
    namespace: str = Field(
        ...,
        description="Kubernetes namespace where the Cluster is defined (e.g., 'lab')",
    )
    target_release: str = Field(
        ...,
        description="Target MOSK release version (e.g., 'mosk-21-0-2-25-2-2')",
    )
    crq_number: str = Field(
        ...,
        description="Change request number (format: CRQxxxxxxxxx)",
        min_length=12,
        max_length=12,
    )
    dry_run: bool = Field(
        default=False,
        description="Preview the patch without applying (validates only)",
    )


class ApplyClusterReleasePatchOutput(BaseModel):
    """Output from apply_cluster_release_patch tool.

    Attributes:
        success: Whether the patch was applied successfully.
        cluster_name: Name of the patched Cluster.
        namespace: Namespace of the Cluster.
        message: Human-readable result message.
        applied_at: Timestamp when patch was applied.
        crq_number: CRQ number used for this operation.
        dry_run: Whether this was a dry-run.
        before_release: MOSK release version before patch.
        after_release: MOSK release version after patch (if changed).
        available_releases: List of available MOSK releases (for reference).
        warnings: Any warnings about the changes.
        error_message: Error message if patch failed.
    """

    success: bool = Field(..., description="Whether patch was successful")
    cluster_name: str = Field(..., description="Name of the patched Cluster")
    namespace: str = Field(..., description="Namespace")
    message: str = Field(..., description="Result message")
    applied_at: str | None = Field(
        default=None,
        description="When patch was applied (ISO format)",
    )
    crq_number: str = Field(..., description="CRQ number used")
    dry_run: bool = Field(..., description="Whether this was dry-run")
    before_release: str | None = Field(
        default=None,
        description="MOSK release version before patch",
    )
    after_release: str | None = Field(
        default=None,
        description="MOSK release version after patch",
    )
    available_releases: list[str] = Field(
        default_factory=list,
        description="Available MOSK releases for reference",
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="Warnings about the changes",
    )
    error_message: str | None = Field(
        default=None,
        description="Error message if failed",
    )


async def _get_available_mosk_releases(
    mcc_adapter: KubernetesAdapter,
) -> list[str]:
    """Get list of available MOSK ClusterRelease names.

    Args:
        mcc_adapter: Kubernetes adapter for MCC cluster.

    Returns:
        List of MOSK release names (e.g., ['mosk-17-4-0-25-1', 'mosk-21-0-2-25-2-2']).
    """
    try:
        releases = await mcc_adapter.list_cluster_releases()
        # Filter for MOSK releases (start with 'mosk-')
        mosk_releases = [
            r["metadata"]["name"]
            for r in releases
            if r.get("metadata", {}).get("name", "").startswith("mosk-")
        ]
        return sorted(mosk_releases)
    except Exception as e:
        logger.warning("failed_to_list_cluster_releases", error=str(e))
        return []


async def _validate_target_release(
    mcc_adapter: KubernetesAdapter,
    target_release: str,
) -> tuple[bool, str | None]:
    """Validate that the target release exists.

    Args:
        mcc_adapter: Kubernetes adapter for MCC cluster.
        target_release: Target release to validate.

    Returns:
        Tuple of (is_valid, error_message).
    """
    if not target_release.startswith("mosk-"):
        return (
            False,
            f"Invalid release format: '{target_release}'. MOSK releases must start with 'mosk-'",
        )

    try:
        release = await mcc_adapter.get_cluster_release(target_release)
        if release is None:
            available = await _get_available_mosk_releases(mcc_adapter)
            return False, (
                f"ClusterRelease '{target_release}' not found. "
                f"Available MOSK releases: {', '.join(available) if available else 'none found'}"
            )
        return True, None
    except ResourceNotFoundError:
        available = await _get_available_mosk_releases(mcc_adapter)
        return False, (
            f"ClusterRelease '{target_release}' not found. "
            f"Available MOSK releases: {', '.join(available) if available else 'none found'}"
        )
    except Exception as e:
        logger.error("failed_to_validate_release", error=str(e))
        # Fail closed for safety - cluster upgrades require validation
        return False, (
            f"Failed to validate release '{target_release}': {e}. "
            "Cannot proceed with upgrade without validation. "
            "Please check cluster connectivity and try again."
        )


async def apply_cluster_release_patch(
    mcc_adapter: KubernetesAdapter,
    input_data: ApplyClusterReleasePatchInput,
) -> ApplyClusterReleasePatchOutput:
    """Apply a patch to change MOSK cluster release version (triggers platform upgrade).

    PRIVILEGED OPERATION: Requires valid CRQ number for audit compliance.

    This tool is STRICTLY LIMITED to modifying ONLY the release field
    (spec.providerSpec.value.release) in a Cluster CR. This is the ONLY
    operation this tool can perform - it cannot modify any other fields.

    IMPORTANT: This operates on the MCC (management) cluster, not the MOSK cluster.

    SAFETY CONSTRAINTS:
    - ONLY spec.providerSpec.value.release can be modified
    - Requires valid CRQ number
    - Validates target release exists in ClusterRelease resources
    - Supports dry-run for validation

    Use case:
    - Upgrade MOSK platform version (e.g., mosk-17-4-0-25-1 -> mosk-21-0-2-25-2-2)

    Args:
        mcc_adapter: Kubernetes adapter for MCC management cluster.
        input_data: Input parameters including target release and CRQ.

    Returns:
        ApplyClusterReleasePatchOutput with operation status and results.

    Raises:
        ValidationError: If CRQ or release validation fails.
        ResourceNotFoundError: If Cluster doesn't exist.
        ToolExecutionError: If operation fails.

    Example:
        >>> # Upgrade MOSK platform
        >>> result = await apply_cluster_release_patch(
        ...     mcc_adapter,
        ...     ApplyClusterReleasePatchInput(
        ...         cluster_name="mos",
        ...         namespace="lab",
        ...         target_release="mosk-21-0-2-25-2-2",
        ...         crq_number="CRQ123456789",
        ...     ),
        ... )
        >>> if result.success:
        ...     print(f"Upgrade initiated: {result.message}")
    """
    logger.info(
        "applying_cluster_release_patch",
        cluster_name=input_data.cluster_name,
        namespace=input_data.namespace,
        target_release=input_data.target_release,
        crq_number=input_data.crq_number,
        dry_run=input_data.dry_run,
    )

    warnings: list[str] = []
    available_releases: list[str] = []

    # Step 1: Validate CRQ number (required for both dry-run and actual execution)
    crq_validator = get_crq_validator()
    crq_result = crq_validator.validate(input_data.crq_number)

    if not crq_result.is_valid:
        raise ValidationError(
            message=f"Invalid CRQ: {crq_result.message}",
            field="crq_number",
            value=input_data.crq_number,
            constraint="Valid CRQ format (CRQxxxxxxxxx)",
        )

    # Step 2: Get available releases for reference
    available_releases = await _get_available_mosk_releases(mcc_adapter)

    # Step 3: Validate target release exists
    is_valid, error_msg = await _validate_target_release(mcc_adapter, input_data.target_release)
    if not is_valid:
        raise ValidationError(
            message=error_msg or "Invalid target release",
            field="target_release",
            value=input_data.target_release,
            constraint="Must be a valid ClusterRelease name",
        )

    try:
        # Step 4: Get current Cluster to verify it exists and record before state
        cluster = await mcc_adapter.get_cluster(
            name=input_data.cluster_name,
            namespace=input_data.namespace,
        )

        if not cluster:
            raise ResourceNotFoundError(
                message=f"Cluster '{input_data.cluster_name}' not found in namespace '{input_data.namespace}'",
                resource_type="Cluster",
                resource_id=f"{input_data.namespace}/{input_data.cluster_name}",
            )

        # Record before state
        provider_spec = cluster.get("spec", {}).get("providerSpec", {}).get("value", {})
        before_release = provider_spec.get("release", "unknown")

        # Check if already at target release
        if before_release == input_data.target_release:
            return ApplyClusterReleasePatchOutput(
                success=True,
                cluster_name=input_data.cluster_name,
                namespace=input_data.namespace,
                message=f"Cluster is already at release '{input_data.target_release}'. No changes needed.",
                applied_at=None,
                crq_number=input_data.crq_number,
                dry_run=input_data.dry_run,
                before_release=before_release,
                after_release=before_release,
                available_releases=available_releases,
                warnings=[],
                error_message=None,
            )

        # Add warning about upgrade being triggered
        warnings.append(
            f"CRITICAL: Changing release from '{before_release}' to '{input_data.target_release}' "
            "will trigger a MOSK platform upgrade. This operation will upgrade Kubernetes, "
            "LCM components, and system services. Ensure you have a valid maintenance window."
        )

        # Step 5: Apply patch (or dry-run)
        if input_data.dry_run:
            logger.info(
                "cluster_release_patch_dry_run",
                cluster_name=input_data.cluster_name,
                before_release=before_release,
                target_release=input_data.target_release,
            )

            return ApplyClusterReleasePatchOutput(
                success=True,
                cluster_name=input_data.cluster_name,
                namespace=input_data.namespace,
                message=(
                    f"Dry-run successful. Patch is valid and ready to apply. "
                    f"Will upgrade from '{before_release}' to '{input_data.target_release}'."
                ),
                applied_at=None,
                crq_number=input_data.crq_number,
                dry_run=True,
                before_release=before_release,
                after_release=None,
                available_releases=available_releases,
                warnings=warnings,
                error_message=None,
            )

        # Actually apply the patch
        logger.info(
            "applying_cluster_release_patch_to_cluster",
            cluster_name=input_data.cluster_name,
            namespace=input_data.namespace,
            target_release=input_data.target_release,
            crq_number=input_data.crq_number,
        )

        patched_cluster = await mcc_adapter.patch_cluster_release(
            name=input_data.cluster_name,
            target_release=input_data.target_release,
            namespace=input_data.namespace,
        )

        applied_at = datetime.now(UTC).isoformat()

        # Get after state
        after_provider_spec = (
            patched_cluster.get("spec", {}).get("providerSpec", {}).get("value", {})
        )
        after_release = after_provider_spec.get("release", input_data.target_release)

        # Add monitoring guidance
        warnings.append(
            "Platform upgrade initiated. Monitor progress with 'monitor_operation' tool "
            "using operation_type='mosk_upgrade' (when implemented) or check "
            "ClusterUpgradeStatus resources directly."
        )

        message = (
            f"Successfully changed release for cluster '{input_data.cluster_name}' "
            f"from '{before_release}' to '{after_release}'. Platform upgrade initiated."
        )

        logger.info(
            "cluster_release_patch_applied",
            cluster_name=input_data.cluster_name,
            namespace=input_data.namespace,
            crq_number=input_data.crq_number,
            before_release=before_release,
            after_release=after_release,
        )

        return ApplyClusterReleasePatchOutput(
            success=True,
            cluster_name=input_data.cluster_name,
            namespace=input_data.namespace,
            message=message,
            applied_at=applied_at,
            crq_number=input_data.crq_number,
            dry_run=False,
            before_release=before_release,
            after_release=after_release,
            available_releases=available_releases,
            warnings=warnings,
            error_message=None,
        )

    except (ValidationError, ResourceNotFoundError):
        raise
    except Exception as e:
        logger.error(
            "apply_cluster_release_patch_failed",
            cluster_name=input_data.cluster_name,
            error=str(e),
        )
        raise ToolExecutionError(
            message=f"Failed to apply cluster release patch: {e}",
            tool_name="apply_cluster_release_patch",
            details={
                "cluster_name": input_data.cluster_name,
                "namespace": input_data.namespace,
                "target_release": input_data.target_release,
                "error": str(e),
            },
        ) from e
