"""Apply OSDPL patch tool (with CRQ validation).

This module provides the apply_osdpl_patch MCP tool for applying
JSON patches to OpenStackDeployment resources. This is a privileged
operation that requires CRQ validation.

IMPORTANT SAFETY CONSTRAINTS:
- ONLY /spec/openstack_version can be modified (for OpenStack upgrades)
- Only 'replace' operation is allowed (no add, delete, remove, move, copy)
- Requires valid CRQ number for audit compliance
- No destructive operations permitted

Safety Level: Privileged (requires CRQ)
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from mosk_mcp.auth.crq import get_crq_validator
from mosk_mcp.core.exceptions import (
    ResourceNotFoundError,
    ToolExecutionError,
    ValidationError,
)
from mosk_mcp.observability.logging import get_logger


if TYPE_CHECKING:
    from mosk_mcp.adapters.kubernetes import KubernetesAdapter


logger = get_logger(__name__)


# Allowed patch operations - only replace for version changes
ALLOWED_PATCH_OPS = {"replace"}

# The ONLY path that can be modified - openstack_version for upgrades
ALLOWED_PATH = "/spec/openstack_version"

# Valid OpenStack release versions supported by MOSK
# MOSK only supports every other release (A, C, E pattern)
VALID_OPENSTACK_VERSIONS = {
    "antelope",  # 2023.1 - MOSK supported
    "caracal",  # 2024.1 - MOSK supported
    "epoxy",  # 2025.1 - MOSK supported
}


class ApplyOSDPLPatchInput(BaseModel):
    """Input parameters for apply_osdpl_patch tool.

    Attributes:
        osdpl_name: Name of the OpenStackDeployment to patch.
        namespace: Kubernetes namespace where OSDPL is deployed.
        patch: JSON Patch operations to apply.
        crq_number: Change request number for audit compliance.
        dry_run: Preview the patch without applying.
    """

    osdpl_name: str = Field(
        ...,
        description="Name of the OpenStackDeployment resource (e.g., 'mos')",
    )
    namespace: str = Field(
        default="openstack",
        description="Kubernetes namespace where OSDPL is deployed",
    )
    patch: list[dict[str, Any]] = Field(
        ...,
        description="JSON Patch operations (RFC 6902). Only 'replace' on /spec/openstack_version allowed.",
        min_length=1,
        max_length=1,  # Only one operation allowed
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


class ApplyOSDPLPatchOutput(BaseModel):
    """Output from apply_osdpl_patch tool.

    Attributes:
        success: Whether the patch was applied successfully.
        osdpl_name: Name of the patched OSDPL.
        namespace: Namespace of the OSDPL.
        message: Human-readable result message.
        applied_at: Timestamp when patch was applied.
        crq_number: CRQ number used for this operation.
        dry_run: Whether this was a dry-run.
        changes_applied: Summary of changes applied.
        warnings: Any warnings about the changes.
        before_version: OpenStack version before patch.
        after_version: OpenStack version after patch (if changed).
        error_message: Error message if patch failed.
    """

    success: bool = Field(..., description="Whether patch was successful")
    osdpl_name: str = Field(..., description="Name of the patched OSDPL")
    namespace: str = Field(..., description="Namespace")
    message: str = Field(..., description="Result message")
    applied_at: str | None = Field(
        default=None,
        description="When patch was applied (ISO format)",
    )
    crq_number: str = Field(..., description="CRQ number used")
    dry_run: bool = Field(..., description="Whether this was dry-run")
    changes_applied: list[str] = Field(
        default_factory=list,
        description="Summary of changes applied",
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="Warnings about the changes",
    )
    before_version: str | None = Field(
        default=None,
        description="OpenStack version before patch",
    )
    after_version: str | None = Field(
        default=None,
        description="OpenStack version after patch",
    )
    error_message: str | None = Field(
        default=None,
        description="Error message if failed",
    )


def _validate_patch_safety(patch: list[dict[str, Any]]) -> tuple[bool, list[str], list[str]]:
    """Validate that patch operations are safe and allowed.

    This tool is STRICTLY limited to only modifying /spec/openstack_version
    for triggering OpenStack upgrades. No other paths are permitted.

    Args:
        patch: List of JSON Patch operations.

    Returns:
        Tuple of (is_valid, errors, warnings).
    """
    errors: list[str] = []
    warnings: list[str] = []

    # Only allow single operation
    if len(patch) != 1:
        errors.append(f"Only one patch operation is allowed. Got {len(patch)} operations.")
        return False, errors, warnings

    op = patch[0]
    op_type = op.get("op")
    path = op.get("path", "")
    value = op.get("value")

    # Check operation type - only 'replace' allowed
    if op_type not in ALLOWED_PATCH_OPS:
        errors.append(
            f"Operation '{op_type}' is not allowed. "
            f"Only 'replace' is permitted for OpenStack version changes."
        )

    # Check path - ONLY /spec/openstack_version allowed
    if path != ALLOWED_PATH:
        errors.append(
            f"Path '{path}' is not allowed. Only '{ALLOWED_PATH}' can be modified with this tool."
        )

    # Validate value is present and valid
    if "value" not in op:
        errors.append("'replace' operation requires a 'value' field.")
    elif not isinstance(value, str) or not value:
        errors.append(
            "Invalid value for openstack_version. Must be a non-empty string "
            "(e.g., 'caracal', 'antelope', 'epoxy')."
        )
    elif value.lower() not in VALID_OPENSTACK_VERSIONS:
        errors.append(
            f"Invalid OpenStack version '{value}'. "
            f"Valid versions are: {', '.join(sorted(VALID_OPENSTACK_VERSIONS))}."
        )

    # Add warning about upgrade being triggered
    if not errors:
        warnings.append(
            "CRITICAL: Changing openstack_version will trigger a cluster upgrade. "
            "Ensure you have a valid maintenance window and backup."
        )

    is_valid = len(errors) == 0
    return is_valid, errors, warnings


async def apply_osdpl_patch(
    kubernetes_adapter: KubernetesAdapter,
    input_data: ApplyOSDPLPatchInput,
) -> ApplyOSDPLPatchOutput:
    """Apply a JSON patch to change OpenStack version (triggers upgrade).

    PRIVILEGED OPERATION: Requires valid CRQ number for audit compliance.

    This tool is STRICTLY LIMITED to modifying ONLY the openstack_version field
    in an OpenStackDeployment resource. This is the ONLY operation this tool
    can perform - it cannot modify any other fields.

    SAFETY CONSTRAINTS:
    - ONLY /spec/openstack_version can be modified
    - Only 'replace' operation is allowed
    - Requires valid CRQ number
    - Supports dry-run for validation

    Use case:
    - Upgrade OpenStack version (e.g., antelope -> caracal -> epoxy)

    Args:
        kubernetes_adapter: Kubernetes adapter for cluster communication.
        input_data: Input parameters including patch operations and CRQ.

    Returns:
        ApplyOSDPLPatchOutput with operation status and results.

    Raises:
        ValidationError: If CRQ or patch validation fails.
        ResourceNotFoundError: If OSDPL doesn't exist.
        ToolExecutionError: If operation fails.

    Example:
        >>> # Upgrade OpenStack from antelope to caracal
        >>> result = await apply_osdpl_patch(
        ...     k8s_adapter,
        ...     ApplyOSDPLPatchInput(
        ...         osdpl_name="mos",
        ...         patch=[
        ...             {"op": "replace", "path": "/spec/openstack_version", "value": "caracal"}
        ...         ],
        ...         crq_number="CRQ123456789",
        ...     ),
        ... )
        >>> if result.success:
        ...     print(f"Upgrade initiated: {result.message}")
    """
    logger.info(
        "applying_osdpl_patch",
        osdpl_name=input_data.osdpl_name,
        namespace=input_data.namespace,
        crq_number=input_data.crq_number,
        dry_run=input_data.dry_run,
        patch_operations=len(input_data.patch),
    )

    # Step 1: Validate patch safety FIRST (fast, doesn't consume CRQ)
    # This ensures invalid patches fail immediately without CRQ validation
    is_valid, errors, warnings = _validate_patch_safety(input_data.patch)

    if not is_valid:
        error_msg = "Patch validation failed:\n" + "\n".join(f"  - {e}" for e in errors)
        raise ValidationError(
            message=error_msg,
            field="patch",
            value=str(input_data.patch),
            constraint="Only 'replace' on /spec/openstack_version is allowed",
        )

    # Step 2: Validate CRQ number (required for both dry-run and actual execution)
    # Note: CRQ is required even for dry-run to ensure audit compliance
    crq_validator = get_crq_validator()
    crq_result = crq_validator.validate(input_data.crq_number)

    if not crq_result.is_valid:
        raise ValidationError(
            message=f"Invalid CRQ: {crq_result.message}",
            field="crq_number",
            value=input_data.crq_number,
            constraint="Valid CRQ format (CRQxxxxxxxxx)",
        )

    try:
        # Step 3: Get current OSDPL to verify it exists and record before state
        osdpl = await kubernetes_adapter.get_openstack_deployment(
            name=input_data.osdpl_name,
            namespace=input_data.namespace,
        )

        if not osdpl:
            raise ResourceNotFoundError(
                message=f"OpenStackDeployment '{input_data.osdpl_name}' not found",
                resource_type="OpenStackDeployment",
                resource_id=input_data.osdpl_name,
            )

        # Record before state
        spec = osdpl.get("spec", {})
        before_version = spec.get("openstack_version", "unknown")

        # Build changes summary
        changes_applied: list[str] = []
        for op in input_data.patch:
            path = op.get("path", "")
            value = op.get("value", "")
            op_type = op.get("op", "")
            changes_applied.append(f"{op_type} {path} = {value}")

        # Step 4: Apply patch (or dry-run)
        if input_data.dry_run:
            logger.info(
                "osdpl_patch_dry_run",
                osdpl_name=input_data.osdpl_name,
                changes=changes_applied,
            )

            return ApplyOSDPLPatchOutput(
                success=True,
                osdpl_name=input_data.osdpl_name,
                namespace=input_data.namespace,
                message="Dry-run successful. Patch is valid and ready to apply.",
                applied_at=None,
                crq_number=input_data.crq_number,
                dry_run=True,
                changes_applied=changes_applied,
                warnings=warnings,
                before_version=before_version,
                after_version=None,
                error_message=None,
            )

        # Actually apply the patch
        logger.info(
            "applying_osdpl_patch_to_cluster",
            osdpl_name=input_data.osdpl_name,
            crq_number=input_data.crq_number,
        )

        patched_osdpl = await kubernetes_adapter.patch_openstack_deployment(
            name=input_data.osdpl_name,
            patch=input_data.patch,
            namespace=input_data.namespace,
        )

        applied_at = datetime.now(UTC).isoformat()

        # Get after state
        after_spec = patched_osdpl.get("spec", {})
        after_version = after_spec.get("openstack_version", before_version)

        # Check if version changed (upgrade triggered)
        version_changed = before_version != after_version
        if version_changed:
            warnings.append(
                f"OpenStack version changed from '{before_version}' to '{after_version}'. "
                "Upgrade will be triggered. Monitor progress with get_openstack_upgrade_progress tool."
            )

        message = f"Successfully applied {len(input_data.patch)} patch operation(s) to OSDPL '{input_data.osdpl_name}'"
        if version_changed:
            message += f". Upgrade from {before_version} to {after_version} initiated."

        logger.info(
            "osdpl_patch_applied",
            osdpl_name=input_data.osdpl_name,
            crq_number=input_data.crq_number,
            before_version=before_version,
            after_version=after_version,
            version_changed=version_changed,
        )

        return ApplyOSDPLPatchOutput(
            success=True,
            osdpl_name=input_data.osdpl_name,
            namespace=input_data.namespace,
            message=message,
            applied_at=applied_at,
            crq_number=input_data.crq_number,
            dry_run=False,
            changes_applied=changes_applied,
            warnings=warnings,
            before_version=before_version,
            after_version=after_version,
            error_message=None,
        )

    except (ValidationError, ResourceNotFoundError):
        raise
    except ValueError as e:
        # Raised by adapter for invalid operations
        raise ValidationError(
            message=str(e),
            field="patch",
            value=str(input_data.patch),
            constraint="Valid patch operations",
        ) from e
    except Exception as e:
        logger.error(
            "apply_osdpl_patch_failed",
            osdpl_name=input_data.osdpl_name,
            error=str(e),
        )
        raise ToolExecutionError(
            message=f"Failed to apply OSDPL patch: {e}",
            tool_name="apply_osdpl_patch",
            details={
                "osdpl_name": input_data.osdpl_name,
                "error": str(e),
            },
        ) from e
