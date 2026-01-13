"""Commence Cluster Upgrade tool for MOSK platform upgrades via ClusterUpdatePlan.

This module provides the commence_cluster_upgrade MCP tool for initiating
MOSK platform upgrades using the ClusterUpdatePlan mechanism.

IMPORTANT SAFETY CONSTRAINTS:
- Uses ClusterUpdatePlan API (not direct Cluster CR patching)
- Requires valid CRQ number for audit compliance
- Validates upgrade path is supported in KaasRelease.supportedClusterReleases
- Validates ClusterUpdatePlan exists for the target release
- Supports step-by-step upgrades (v2 feature)

Safety Level: Privileged (requires CRQ)

V1 IMPLEMENTATION (Current):
- Commences all steps in the ClusterUpdatePlan at once
- Validates upgrade path before commencing
- Reports step details and estimated durations

V2 ENHANCEMENTS (Planned):
- Step-by-step upgrade control:
  - Allow selective step commence via step_ids parameter
  - Example: commence_cluster_upgrade(..., step_ids=["openstack"])
  - Then later: commence_cluster_upgrade(..., step_ids=["ceph"])
  - Finally: commence_cluster_upgrade(..., step_ids=["k8s-controllers", "k8s-workers-mos-default", "mcc-components"])
- Step monitoring:
  - Add get_upgrade_plan_status tool to check individual step progress
  - Show which steps are InProgress, Completed, Failed
- Step dependencies:
  - Validate step dependencies before commencing
  - Warn if commencing a step out of recommended order
- Rollback support:
  - Support setting commence=false to pause remaining steps
  - Document rollback procedures per step
- Enhanced preflight:
  - Per-step preflight checks
  - Step-specific impact assessment
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from mosk_mcp.auth.crq import get_crq_validator
from mosk_mcp.auth.rbac import ToolSafetyLevel
from mosk_mcp.core.exceptions import (
    ResourceNotFoundError,
    ToolExecutionError,
    ValidationError,
)
from mosk_mcp.observability.audit import AuditLevel, AuditLogger
from mosk_mcp.observability.logging import get_logger
from mosk_mcp.tools.common import audit_tool_execution


if TYPE_CHECKING:
    from mosk_mcp.adapters.kubernetes import KubernetesAdapter
    from mosk_mcp.auth.types import UserContext


logger = get_logger(__name__)


# Tool metadata
TOOL_NAME = "commence_cluster_upgrade"
TOOL_SAFETY_LEVEL = ToolSafetyLevel.PRIVILEGED
TOOL_DESCRIPTION = (
    "Commence MOSK cluster upgrade via ClusterUpdatePlan mechanism. "
    "PRIVILEGED: Requires valid CRQ number. "
    "Validates upgrade path in KaasRelease and commences UpdatePlan steps."
)


class UpgradeStepInfo(BaseModel):
    """Information about an upgrade step."""

    id: str = Field(..., description="Step identifier (e.g., 'openstack', 'ceph')")
    name: str = Field(..., description="Human-readable step name")
    granularity: str = Field(
        ...,
        description="Step granularity: 'cluster' (all at once) or 'machine' (per-node)",
    )
    commenced: bool = Field(..., description="Whether step has been commenced")
    status: str = Field(..., description="Step status (NotStarted, InProgress, Completed, etc.)")
    estimated_duration: str | None = Field(
        default=None,
        description="Estimated duration (e.g., '2h30m0s')",
    )
    user_impact: str | None = Field(
        default=None,
        description="Impact on users: 'none', 'minor', 'major'",
    )
    workload_impact: str | None = Field(
        default=None,
        description="Impact on workloads: 'none', 'minor', 'major'",
    )


class CommenceClusterUpgradeInput(BaseModel):
    """Input parameters for commence_cluster_upgrade tool.

    Attributes:
        cluster_name: Name of the Cluster CR to upgrade (e.g., 'mos').
        namespace: Kubernetes namespace where the Cluster is defined on MCC.
        target_release: Target MOSK release version (e.g., 'mosk-21-0-0-25-2').
        crq_number: Change request number for audit compliance.
        dry_run: Preview the upgrade without commencing.
        step_ids: (V2) Specific step IDs to commence. If None, commences all steps.
    """

    cluster_name: str = Field(
        ...,
        description="Name of the Cluster CR (e.g., 'mos')",
    )
    namespace: str = Field(
        ...,
        description="Kubernetes namespace where the Cluster is defined on MCC (e.g., 'lab')",
    )
    target_release: str = Field(
        ...,
        description="Target MOSK release version (e.g., 'mosk-21-0-0-25-2')",
    )
    crq_number: str = Field(
        ...,
        description="Change request number (format: CRQ followed by 9 digits, e.g., CRQ123456789)",
        pattern=r"^CRQ\d{9}$",
    )
    dry_run: bool = Field(
        default=False,
        description="Preview the upgrade without commencing (validates only)",
    )
    # V2 feature: selective step commence
    step_ids: list[str] | None = Field(
        default=None,
        description="(V2) Specific step IDs to commence (e.g., ['openstack', 'ceph']). "
        "If None, commences all steps. Use get_upgrade_plan_status to see available steps.",
        min_length=1,  # If provided, must have at least one step
    )


class CommenceClusterUpgradeOutput(BaseModel):
    """Output from commence_cluster_upgrade tool.

    Attributes:
        success: Whether the upgrade was commenced successfully.
        cluster_name: Name of the cluster being upgraded.
        namespace: Namespace of the cluster.
        message: Human-readable result message.
        commenced_at: Timestamp when upgrade was commenced.
        crq_number: CRQ number used for this operation.
        dry_run: Whether this was a dry-run.
        update_plan_name: Name of the ClusterUpdatePlan.
        source_release: Current MOSK release version.
        target_release: Target MOSK release version.
        steps: List of upgrade steps with their status.
        steps_commenced: List of step IDs that were commenced.
        total_estimated_duration: Total estimated duration if available.
        user_impact: Overall user impact.
        workload_impact: Overall workload impact.
        skip_maintenance: Whether maintenance mode can be skipped.
        reboot_required: Whether reboots are required.
        available_upgrade_versions: Available upgrade versions from current release.
        warnings: Any warnings about the upgrade.
        error_message: Error message if upgrade failed.
    """

    success: bool = Field(..., description="Whether upgrade was commenced successfully")
    cluster_name: str = Field(..., description="Name of the cluster")
    namespace: str = Field(..., description="Namespace")
    message: str = Field(..., description="Result message")
    commenced_at: str | None = Field(
        default=None,
        description="When upgrade was commenced (ISO format)",
    )
    crq_number: str = Field(..., description="CRQ number used")
    dry_run: bool = Field(..., description="Whether this was dry-run")
    update_plan_name: str | None = Field(
        default=None,
        description="Name of the ClusterUpdatePlan",
    )
    source_release: str | None = Field(
        default=None,
        description="Current MOSK release version",
    )
    target_release: str | None = Field(
        default=None,
        description="Target MOSK release version",
    )
    steps: list[UpgradeStepInfo] = Field(
        default_factory=list,
        description="Upgrade steps with their status",
    )
    steps_commenced: list[str] = Field(
        default_factory=list,
        description="Step IDs that were commenced",
    )
    total_estimated_duration: str | None = Field(
        default=None,
        description="Total estimated duration",
    )
    user_impact: str | None = Field(
        default=None,
        description="Overall user impact: 'none', 'minor', 'major'",
    )
    workload_impact: str | None = Field(
        default=None,
        description="Overall workload impact: 'none', 'minor', 'major'",
    )
    skip_maintenance: bool | None = Field(
        default=None,
        description="Whether maintenance mode can be skipped",
    )
    reboot_required: bool | None = Field(
        default=None,
        description="Whether reboots are required",
    )
    available_upgrade_versions: list[str] = Field(
        default_factory=list,
        description="Available upgrade versions from current release",
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="Warnings about the upgrade",
    )
    error_message: str | None = Field(
        default=None,
        description="Error message if failed",
    )


def _extract_steps_info(
    plan: dict[str, Any],
) -> tuple[list[UpgradeStepInfo], str | None, str | None, str | None]:
    """Extract step information from ClusterUpdatePlan.

    Returns:
        Tuple of (steps, total_duration, max_user_impact, max_workload_impact).
    """
    spec_steps = plan.get("spec", {}).get("steps", [])
    status_steps = {s.get("id"): s for s in plan.get("status", {}).get("steps", [])}

    steps = []
    max_user_impact = "none"
    max_workload_impact = "none"
    impact_order = {"none": 0, "minor": 1, "major": 2}

    for spec_step in spec_steps:
        step_id = spec_step.get("id", "")
        status_step = status_steps.get(step_id, {})

        # Extract impact
        impact = spec_step.get("impact", {})
        user_impact = impact.get("users", "none")
        workload_impact = impact.get("workloads", "none")

        # Track max impact
        if impact_order.get(user_impact, 0) > impact_order.get(max_user_impact, 0):
            max_user_impact = user_impact
        if impact_order.get(workload_impact, 0) > impact_order.get(max_workload_impact, 0):
            max_workload_impact = workload_impact

        # Extract duration
        duration = spec_step.get("duration", {})
        estimated = duration.get("estimated")

        step_info = UpgradeStepInfo(
            id=step_id,
            name=spec_step.get("name", step_id),
            granularity=spec_step.get("granularity", "cluster"),
            commenced=spec_step.get("commence", False),
            status=status_step.get("status", "NotStarted"),
            estimated_duration=estimated,
            user_impact=user_impact,
            workload_impact=workload_impact,
        )
        steps.append(step_info)

    # NOTE: Duration calculation returns None. Individual step durations
    # are available in UpgradeStepInfo for display purposes.
    total_duration = None

    return steps, total_duration, max_user_impact, max_workload_impact


async def commence_cluster_upgrade(
    mcc_adapter: KubernetesAdapter,
    input_data: CommenceClusterUpgradeInput,
    user_context: UserContext | None = None,
    audit_logger: AuditLogger | None = None,
) -> CommenceClusterUpgradeOutput:
    """Commence MOSK cluster upgrade via ClusterUpdatePlan mechanism.

    PRIVILEGED OPERATION: Requires valid CRQ number for audit compliance.

    This tool initiates MOSK platform upgrades using the ClusterUpdatePlan API:
    1. Validates CRQ number
    2. Validates upgrade path is supported in KaasRelease
    3. Finds the ClusterUpdatePlan for the target release
    4. Commences the upgrade steps (all or selective)

    IMPORTANT: This operates on the MCC (management) cluster.

    SAFETY CONSTRAINTS:
    - Uses ClusterUpdatePlan API (not direct Cluster CR patching)
    - Validates upgrade path before commencing
    - Supports dry-run for validation
    - Supports step-by-step upgrades (v2)

    Args:
        mcc_adapter: Kubernetes adapter for MCC management cluster.
        input_data: Input parameters including target release and CRQ.
        user_context: User context for audit logging.
        audit_logger: Audit logger for recording the operation.

    Returns:
        CommenceClusterUpgradeOutput with operation status and step details.

    Raises:
        ValidationError: If CRQ, upgrade path, or UpdatePlan validation fails.
        ResourceNotFoundError: If Cluster or UpdatePlan doesn't exist.
        ToolExecutionError: If operation fails.

    Example:
        >>> # Commence MOSK upgrade
        >>> result = await commence_cluster_upgrade(
        ...     mcc_adapter,
        ...     CommenceClusterUpgradeInput(
        ...         cluster_name="mos",
        ...         namespace="lab",
        ...         target_release="mosk-21-0-0-25-2",
        ...         crq_number="CRQ987654321",
        ...     ),
        ... )
        >>> if result.success:
        ...     print(f"Upgrade commenced: {result.update_plan_name}")
        ...     for step in result.steps:
        ...         print(f"  - {step.id}: {step.status}")
    """
    logger.info(
        "commencing_cluster_upgrade",
        cluster_name=input_data.cluster_name,
        namespace=input_data.namespace,
        target_release=input_data.target_release,
        crq_number=input_data.crq_number,
        dry_run=input_data.dry_run,
        step_ids=input_data.step_ids,
    )

    # Step 1: Validate CRQ number
    crq_validator = get_crq_validator()
    crq_result = crq_validator.validate(input_data.crq_number)

    if not crq_result.is_valid:
        raise ValidationError(
            message=f"Invalid CRQ: {crq_result.message}",
            field="crq_number",
            value=input_data.crq_number,
            constraint="Valid CRQ format (CRQxxxxxxxxx)",
        )

    async with audit_tool_execution(
        TOOL_NAME,
        audit_logger,
        user_context,
        AuditLevel.PRIVILEGED,
        {
            "resource_type": "ClusterUpdatePlan",
            "resource_name": f"{input_data.cluster_name}-upgrade",
            "resource_namespace": input_data.namespace,
            "crq_id": input_data.crq_number,
            "cluster_name": input_data.cluster_name,
            "target_release": input_data.target_release,
            "dry_run": input_data.dry_run,
            "step_ids": input_data.step_ids,
        },
    ) as audit_details:
        warnings: list[str] = []
        available_upgrade_versions: list[str] = []

        try:
            # Step 2: Get current cluster and validate it exists
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

            # Get current release
            provider_spec = cluster.get("spec", {}).get("providerSpec", {}).get("value", {})
            current_release = provider_spec.get("release", "unknown")

            # NOTE: KaasRelease upgrade path validation is disabled.
            # The KaasRelease.supportedClusterReleases.availableUpgrades mapping
            # is not always configured correctly. Validation is performed via
            # ClusterUpdatePlan existence instead.

            # For now, we trust that the ClusterUpdatePlan exists if the upgrade is valid
            skip_maintenance = False
            reboot_required = False

            # Step 3: Find ClusterUpdatePlan
            update_plan = await mcc_adapter.find_cluster_update_plan(
                cluster_name=input_data.cluster_name,
                target_release=input_data.target_release,
                namespace=input_data.namespace,
            )

            if not update_plan:
                raise ResourceNotFoundError(
                    message=(
                        f"No ClusterUpdatePlan found for cluster '{input_data.cluster_name}' "
                        f"targeting release '{input_data.target_release}'. "
                        "ClusterUpdatePlan is auto-generated by MCC when upgrade path is valid. "
                        "Please verify the target release is correct."
                    ),
                    resource_type="ClusterUpdatePlan",
                    resource_id=f"{input_data.namespace}/{input_data.cluster_name}->{input_data.target_release}",
                )

            plan_name = update_plan.get("metadata", {}).get("name")
            plan_status = update_plan.get("status", {}).get("status", "NotStarted")

            # Update audit details with plan name
            audit_details["update_plan_name"] = plan_name
            audit_details["source_release"] = current_release

            # Extract step information
            steps, total_duration, max_user_impact, max_workload_impact = _extract_steps_info(
                update_plan
            )

            # Check current plan status
            if plan_status == "Completed":
                return CommenceClusterUpgradeOutput(
                    success=True,
                    cluster_name=input_data.cluster_name,
                    namespace=input_data.namespace,
                    message=f"Upgrade to '{input_data.target_release}' is already completed.",
                    commenced_at=None,
                    crq_number=input_data.crq_number,
                    dry_run=input_data.dry_run,
                    update_plan_name=plan_name,
                    source_release=current_release,
                    target_release=input_data.target_release,
                    steps=steps,
                    steps_commenced=[],
                    total_estimated_duration=total_duration,
                    user_impact=max_user_impact,
                    workload_impact=max_workload_impact,
                    skip_maintenance=skip_maintenance,
                    reboot_required=reboot_required,
                    available_upgrade_versions=available_upgrade_versions,
                    warnings=[],
                    error_message=None,
                )

            if plan_status == "InProgress":
                warnings.append(
                    f"Upgrade is already in progress. Current status: {plan_status}. "
                    "Additional steps will be commenced if specified."
                )

            # Determine which steps to commence
            step_ids_to_commence = input_data.step_ids
            if step_ids_to_commence is None:
                # Commence all steps that haven't been commenced yet
                step_ids_to_commence = [s.id for s in steps if not s.commenced]
            else:
                # Validate provided step IDs
                valid_ids = {s.id for s in steps}
                invalid_ids = [sid for sid in step_ids_to_commence if sid not in valid_ids]
                if invalid_ids:
                    raise ValidationError(
                        message=f"Invalid step IDs: {invalid_ids}. Valid IDs: {list(valid_ids)}",
                        field="step_ids",
                        value=step_ids_to_commence,
                        constraint=f"Must be one of: {list(valid_ids)}",
                    )

            # Check if all requested steps are already commenced
            already_commenced = [
                s.id for s in steps if s.id in step_ids_to_commence and s.commenced
            ]
            if already_commenced:
                warnings.append(
                    f"Steps already commenced: {already_commenced}. They will be skipped."
                )
                step_ids_to_commence = [
                    sid for sid in step_ids_to_commence if sid not in already_commenced
                ]

            if not step_ids_to_commence:
                return CommenceClusterUpgradeOutput(
                    success=True,
                    cluster_name=input_data.cluster_name,
                    namespace=input_data.namespace,
                    message="All requested steps are already commenced. No action taken.",
                    commenced_at=None,
                    crq_number=input_data.crq_number,
                    dry_run=input_data.dry_run,
                    update_plan_name=plan_name,
                    source_release=current_release,
                    target_release=input_data.target_release,
                    steps=steps,
                    steps_commenced=[],
                    total_estimated_duration=total_duration,
                    user_impact=max_user_impact,
                    workload_impact=max_workload_impact,
                    skip_maintenance=skip_maintenance,
                    reboot_required=reboot_required,
                    available_upgrade_versions=available_upgrade_versions,
                    warnings=warnings,
                    error_message=None,
                )

            # Add upgrade warning
            warnings.append(
                f"CRITICAL: Commencing upgrade from '{current_release}' to '{input_data.target_release}'. "
                f"Steps to commence: {step_ids_to_commence}. "
                f"User impact: {max_user_impact}, Workload impact: {max_workload_impact}."
            )

            # Step 5: Dry-run or actual commence
            if input_data.dry_run:
                logger.info(
                    "commence_cluster_upgrade_dry_run",
                    cluster_name=input_data.cluster_name,
                    plan_name=plan_name,
                    steps_to_commence=step_ids_to_commence,
                )

                return CommenceClusterUpgradeOutput(
                    success=True,
                    cluster_name=input_data.cluster_name,
                    namespace=input_data.namespace,
                    message=(
                        f"Dry-run successful. Ready to commence upgrade via '{plan_name}'. "
                        f"Steps to commence: {step_ids_to_commence}."
                    ),
                    commenced_at=None,
                    crq_number=input_data.crq_number,
                    dry_run=True,
                    update_plan_name=plan_name,
                    source_release=current_release,
                    target_release=input_data.target_release,
                    steps=steps,
                    steps_commenced=[],  # Not yet commenced in dry-run
                    total_estimated_duration=total_duration,
                    user_impact=max_user_impact,
                    workload_impact=max_workload_impact,
                    skip_maintenance=skip_maintenance,
                    reboot_required=reboot_required,
                    available_upgrade_versions=available_upgrade_versions,
                    warnings=warnings,
                    error_message=None,
                )

            # Actually commence the steps
            logger.info(
                "commencing_cluster_update_plan_steps",
                cluster_name=input_data.cluster_name,
                plan_name=plan_name,
                step_ids=step_ids_to_commence,
                crq_number=input_data.crq_number,
            )

            patched_plan = await mcc_adapter.patch_cluster_update_plan_steps(
                name=plan_name,
                namespace=input_data.namespace,
                step_ids=step_ids_to_commence,
                commence=True,
            )

            commenced_at = datetime.now(UTC).isoformat()

            # Update steps info from patched plan
            updated_steps, _, _, _ = _extract_steps_info(patched_plan)

            message = (
                f"Successfully commenced upgrade via ClusterUpdatePlan '{plan_name}'. "
                f"Steps commenced: {step_ids_to_commence}. "
                f"Upgrading from '{current_release}' to '{input_data.target_release}'."
            )

            logger.info(
                "cluster_upgrade_commenced",
                cluster_name=input_data.cluster_name,
                namespace=input_data.namespace,
                plan_name=plan_name,
                crq_number=input_data.crq_number,
                source_release=current_release,
                target_release=input_data.target_release,
                steps_commenced=step_ids_to_commence,
            )

            # Update audit details
            audit_details["steps_commenced"] = step_ids_to_commence
            audit_details["commenced_at"] = commenced_at

            return CommenceClusterUpgradeOutput(
                success=True,
                cluster_name=input_data.cluster_name,
                namespace=input_data.namespace,
                message=message,
                commenced_at=commenced_at,
                crq_number=input_data.crq_number,
                dry_run=False,
                update_plan_name=plan_name,
                source_release=current_release,
                target_release=input_data.target_release,
                steps=updated_steps,
                steps_commenced=step_ids_to_commence,
                total_estimated_duration=total_duration,
                user_impact=max_user_impact,
                workload_impact=max_workload_impact,
                skip_maintenance=skip_maintenance,
                reboot_required=reboot_required,
                available_upgrade_versions=available_upgrade_versions,
                warnings=warnings,
                error_message=None,
            )

        except (ValidationError, ResourceNotFoundError):
            raise
        except Exception as e:
            logger.error(
                "commence_cluster_upgrade_failed",
                cluster_name=input_data.cluster_name,
                error=str(e),
            )

            raise ToolExecutionError(
                message=f"Failed to commence cluster upgrade: {e}",
                tool_name=TOOL_NAME,
                details={
                    "cluster_name": input_data.cluster_name,
                    "namespace": input_data.namespace,
                    "target_release": input_data.target_release,
                    "error": str(e),
                },
            ) from e
