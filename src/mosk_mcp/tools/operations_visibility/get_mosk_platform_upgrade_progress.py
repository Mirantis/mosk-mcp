"""Get MOSK platform upgrade progress tool.

This module provides the get_mosk_platform_upgrade_progress MCP tool that
tracks detailed progress of MOSK platform upgrades by monitoring:
- Machine phases (Ready -> Prepare -> Deploy -> Reconfigure -> Ready)
- Cluster conditions (Helm, Ceph, Nodes, Kubernetes, etc.)
- HelmBundle status

This tool queries the MCC management cluster where Cluster CRs are managed.

Safety Level: READ_ONLY
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from mosk_mcp.core.exceptions import ResourceNotFoundError, ToolExecutionError
from mosk_mcp.observability.logging import get_logger
from mosk_mcp.tools.operations_visibility.monitors.mosk_upgrade_monitor import (
    MACHINE_PHASE_WEIGHTS,
    MoskUpgradeMonitor,
)


if TYPE_CHECKING:
    from mosk_mcp.adapters.kubernetes import KubernetesAdapter


logger = get_logger(__name__)


class MachineUpgradeInfo(BaseModel):
    """Information about a machine's upgrade status."""

    name: str = Field(..., description="Machine name")
    phase: str = Field(..., description="Current LCM phase")
    progress_percent: int = Field(..., description="Estimated progress for this machine")


class ConditionUpgradeInfo(BaseModel):
    """Information about a cluster condition during upgrade."""

    type: str = Field(..., description="Condition type")
    ready: bool = Field(..., description="Whether condition is ready")
    message: str = Field(default="", description="Condition message")


class UpdatePlanStepInfo(BaseModel):
    """Information about a ClusterUpdatePlan step."""

    id: str = Field(..., description="Step ID (e.g., 'openstack', 'ceph', 'k8s-controllers')")
    name: str = Field(..., description="Step name")
    status: str = Field(..., description="Step status (NotStarted, InProgress, Completed, Failed)")
    commenced: bool = Field(..., description="Whether step has been commenced")
    message: str = Field(
        default="", description="Step progress message (e.g., 'LCM progress: 0/18')"
    )
    duration: str = Field(default="", description="Step duration so far")
    estimated_duration: str = Field(default="", description="Estimated total duration")
    granularity: str = Field(default="cluster", description="Step granularity (cluster or machine)")


class GetMoskPlatformUpgradeProgressInput(BaseModel):
    """Input parameters for get_mosk_platform_upgrade_progress tool."""

    cluster_name: str = Field(
        ...,
        description="Name of the Cluster CR on MCC (e.g., 'mos')",
        min_length=1,
        max_length=253,
    )
    namespace: str = Field(
        default="default",
        description="Namespace where Cluster CR is located (e.g., 'lab')",
    )


class GetMoskPlatformUpgradeProgressOutput(BaseModel):
    """Output from get_mosk_platform_upgrade_progress tool."""

    cluster_name: str = Field(..., description="Cluster name")
    namespace: str = Field(..., description="Cluster namespace")

    # Upgrade status
    phase: str = Field(..., description="Current upgrade phase")
    phase_message: str = Field(..., description="Human-readable phase description")
    progress_percent: int = Field(..., description="Overall progress percentage (0-100)")
    is_upgrading: bool = Field(..., description="Whether upgrade is in progress")
    is_complete: bool = Field(..., description="Whether upgrade is complete")
    has_failed: bool = Field(..., description="Whether upgrade has failed")

    # Release information
    from_release: str | None = Field(None, description="Source MOSK release")
    to_release: str | None = Field(None, description="Target MOSK release")

    # Machine progress
    machines_total: int = Field(..., description="Total number of machines")
    machines_ready: int = Field(..., description="Machines in Ready phase")
    machine_phases: dict[str, int] = Field(
        default_factory=dict,
        description="Count of machines in each phase",
    )
    machines_in_progress: list[MachineUpgradeInfo] = Field(
        default_factory=list,
        description="Machines currently being upgraded",
    )

    # Conditions
    conditions: list[ConditionUpgradeInfo] = Field(
        default_factory=list,
        description="Cluster conditions status",
    )
    conditions_not_ready: list[str] = Field(
        default_factory=list,
        description="Conditions that are not ready",
    )

    # Helm status
    helm_charts_not_ready: list[str] = Field(
        default_factory=list,
        description="Helm charts that are not ready",
    )

    # ClusterUpdatePlan status (source of truth for upgrade progress)
    update_plan_name: str | None = Field(None, description="Name of the active ClusterUpdatePlan")
    update_plan_status: str | None = Field(
        None, description="ClusterUpdatePlan status (NotStarted, InProgress, Completed, Failed)"
    )
    update_plan_started_at: str | None = Field(None, description="When the upgrade started")
    update_plan_steps: list[UpdatePlanStepInfo] = Field(
        default_factory=list,
        description="ClusterUpdatePlan step details",
    )
    current_step: str | None = Field(None, description="Currently executing step ID")
    steps_completed: int = Field(0, description="Number of completed steps")
    steps_total: int = Field(0, description="Total number of steps")

    # Summary
    error_message: str | None = Field(None, description="Error message if failed")
    warnings: list[str] = Field(default_factory=list, description="Warning messages")
    timestamp: str = Field(..., description="Query timestamp")


async def get_mosk_platform_upgrade_progress(
    mcc_adapter: KubernetesAdapter,
    input_data: GetMoskPlatformUpgradeProgressInput,
) -> GetMoskPlatformUpgradeProgressOutput:
    """Get MOSK platform upgrade progress.

    Tracks the progress of a MOSK platform upgrade by monitoring Machine phases,
    cluster conditions, and HelmBundle status. This tool uses the MoskUpgradeMonitor
    internally for accurate progress tracking.

    This tool is useful for:
    - Monitoring MOSK release upgrades (e.g., mosk-17-4-0 to mosk-17-4-6)
    - Tracking which machines are being upgraded
    - Checking Helm chart upgrade status
    - Identifying upgrade blockers

    Args:
        mcc_adapter: Kubernetes adapter for MCC management cluster.
        input_data: Input parameters.

    Returns:
        GetMoskPlatformUpgradeProgressOutput with detailed upgrade progress.

    Raises:
        ResourceNotFoundError: If Cluster is not found.
        ToolExecutionError: If operation fails.
    """
    logger.info(
        "get_mosk_platform_upgrade_progress_start",
        cluster_name=input_data.cluster_name,
        namespace=input_data.namespace,
    )

    try:
        # Use the MoskUpgradeMonitor for accurate progress tracking
        monitor = MoskUpgradeMonitor(
            adapter=mcc_adapter,
            target=input_data.cluster_name,
            namespace=input_data.namespace,
        )

        # Get current progress snapshot
        snapshot = await monitor.get_progress()

        # Extract details from snapshot
        details = snapshot.details or {}
        machine_phases = details.get("machine_phases", {})
        machines_total = details.get("machines_total", 0)
        machines_ready = details.get("machines_ready", 0)

        # Build machines in progress list
        machines_in_progress: list[MachineUpgradeInfo] = []
        raw_machines_in_progress = details.get("machines_in_progress", [])
        for m in raw_machines_in_progress:
            if isinstance(m, dict):
                phase = m.get("phase", "Unknown")
                machines_in_progress.append(
                    MachineUpgradeInfo(
                        name=m.get("name", "unknown"),
                        phase=phase,
                        progress_percent=MACHINE_PHASE_WEIGHTS.get(phase, 0),
                    )
                )

        # Build conditions list
        conditions: list[ConditionUpgradeInfo] = []
        conditions_not_ready: list[str] = []
        raw_conditions = details.get("conditions", {})
        for cond_type, status in raw_conditions.items():
            is_ready = status == "ready"
            conditions.append(
                ConditionUpgradeInfo(
                    type=cond_type,
                    ready=is_ready,
                    message="",
                )
            )
            if not is_ready:
                conditions_not_ready.append(cond_type)

        # Get conditions not ready with messages
        raw_conditions_not_ready = details.get("conditions_not_ready", [])
        warnings: list[str] = []
        for cond in raw_conditions_not_ready:
            if isinstance(cond, dict):
                msg = cond.get("message", "")
                if msg:
                    warnings.append(f"{cond.get('condition', 'Unknown')}: {msg}")

        # Get helm charts not ready
        helm_not_ready: list[str] = []
        raw_helm = details.get("helm_not_ready", [])
        for h in raw_helm:
            if isinstance(h, dict):
                helm_not_ready.append(h.get("chart", "unknown"))

        # Fetch ClusterUpdatePlan for accurate upgrade progress
        # This is the source of truth for step-by-step upgrade status
        update_plan_name: str | None = None
        update_plan_status: str | None = None
        update_plan_started_at: str | None = None
        update_plan_steps: list[UpdatePlanStepInfo] = []
        current_step: str | None = None
        steps_completed = 0
        steps_total = 0
        update_plan_is_upgrading = False
        update_plan_is_complete = False
        update_plan_has_failed = False

        try:
            # Get the target release from the Cluster CR to find the right UpdatePlan
            target_release = details.get("to_release")
            if target_release:
                # Find ClusterUpdatePlan for this target release
                update_plan = await mcc_adapter.find_cluster_update_plan(
                    cluster_name=input_data.cluster_name,
                    target_release=target_release,
                    namespace=input_data.namespace,
                )

                if update_plan:
                    update_plan_name = update_plan.get("metadata", {}).get("name")
                    plan_status = update_plan.get("status", {})
                    update_plan_status = plan_status.get("status", "Unknown")
                    update_plan_started_at = plan_status.get("startedAt")

                    # Determine upgrade state from ClusterUpdatePlan status
                    update_plan_is_upgrading = update_plan_status == "InProgress"
                    update_plan_is_complete = update_plan_status == "Completed"
                    update_plan_has_failed = update_plan_status == "Failed"

                    # Process steps from spec and status
                    spec_steps = update_plan.get("spec", {}).get("steps", [])
                    status_steps = {s.get("id"): s for s in plan_status.get("steps", [])}

                    steps_total = len(spec_steps)
                    for spec_step in spec_steps:
                        step_id = spec_step.get("id", "")
                        status_step = status_steps.get(step_id, {})
                        step_status = status_step.get("status", "NotStarted")

                        # Track current step (first InProgress step)
                        if step_status == "InProgress" and current_step is None:
                            current_step = step_id

                        # Count completed steps
                        if step_status == "Completed":
                            steps_completed += 1

                        # Get estimated duration from spec
                        duration_info = spec_step.get("duration", {})
                        estimated_duration = duration_info.get("estimated", "")

                        update_plan_steps.append(
                            UpdatePlanStepInfo(
                                id=step_id,
                                name=spec_step.get("name", step_id),
                                status=step_status,
                                commenced=spec_step.get("commence", False),
                                message=status_step.get("message", ""),
                                duration=str(status_step.get("duration", "")),
                                estimated_duration=estimated_duration,
                                granularity=spec_step.get("granularity", "cluster"),
                            )
                        )

                    logger.debug(
                        "cluster_update_plan_found",
                        plan_name=update_plan_name,
                        status=update_plan_status,
                        current_step=current_step,
                        steps_completed=steps_completed,
                        steps_total=steps_total,
                    )
        except Exception as e:
            logger.warning("failed_to_get_cluster_update_plan", error=str(e))
            warnings.append(f"Could not fetch ClusterUpdatePlan: {e}")

        # Override is_upgrading/is_complete based on ClusterUpdatePlan if available
        # ClusterUpdatePlan is more accurate than Cluster CR conditions
        effective_is_upgrading = (
            update_plan_is_upgrading if update_plan_name else details.get("is_upgrading", False)
        )
        effective_is_complete = (
            update_plan_is_complete if update_plan_name else monitor.is_complete()
        )
        effective_has_failed = update_plan_has_failed if update_plan_name else monitor.has_failed()

        # Calculate progress from ClusterUpdatePlan steps if available
        if steps_total > 0:
            # Weight progress by step completion + current step progress
            base_progress = int((steps_completed / steps_total) * 100)
            # If there's a current step in progress, add partial progress
            if current_step and steps_completed < steps_total:
                # Add up to (100/steps_total)% for the current step
                step_weight = 100 // steps_total
                # Use a conservative 50% estimate for in-progress step
                base_progress += step_weight // 2
            effective_progress = min(base_progress, 100)
        else:
            effective_progress = snapshot.progress_percent

        # Update phase message to include ClusterUpdatePlan info
        if update_plan_status == "InProgress" and current_step:
            current_step_info = next((s for s in update_plan_steps if s.id == current_step), None)
            if current_step_info:
                phase_message = f"Upgrading: {current_step_info.name}"
                if current_step_info.message:
                    phase_message += f" ({current_step_info.message})"
            else:
                phase_message = f"Upgrading: step {current_step}"
        elif update_plan_status == "Completed":
            phase_message = "Upgrade completed successfully"
        elif update_plan_status == "Failed":
            phase_message = "Upgrade failed"
        else:
            phase_message = snapshot.message

        result = GetMoskPlatformUpgradeProgressOutput(
            cluster_name=input_data.cluster_name,
            namespace=input_data.namespace,
            phase=update_plan_status or snapshot.phase,
            phase_message=phase_message,
            progress_percent=effective_progress,
            is_upgrading=effective_is_upgrading,
            is_complete=effective_is_complete,
            has_failed=effective_has_failed,
            from_release=details.get("from_release"),
            to_release=details.get("to_release"),
            machines_total=machines_total,
            machines_ready=machines_ready,
            machine_phases=machine_phases,
            machines_in_progress=machines_in_progress,
            conditions=conditions,
            conditions_not_ready=conditions_not_ready,
            helm_charts_not_ready=helm_not_ready,
            # ClusterUpdatePlan fields
            update_plan_name=update_plan_name,
            update_plan_status=update_plan_status,
            update_plan_started_at=update_plan_started_at,
            update_plan_steps=update_plan_steps,
            current_step=current_step,
            steps_completed=steps_completed,
            steps_total=steps_total,
            error_message=monitor.get_error_message(),
            warnings=warnings,
            timestamp=datetime.now(UTC).isoformat(),
        )

        logger.info(
            "get_mosk_platform_upgrade_progress_complete",
            cluster_name=input_data.cluster_name,
            phase=update_plan_status or snapshot.phase,
            progress=effective_progress,
            is_complete=effective_is_complete,
            update_plan=update_plan_name,
            current_step=current_step,
        )

        return result

    except ResourceNotFoundError:
        logger.warning(
            "cluster_not_found",
            cluster_name=input_data.cluster_name,
            namespace=input_data.namespace,
        )
        raise
    except Exception as e:
        logger.error(
            "get_mosk_platform_upgrade_progress_error",
            cluster_name=input_data.cluster_name,
            error=str(e),
        )
        raise ToolExecutionError(
            f"Failed to get MOSK platform upgrade progress: {e}",
            tool_name="get_mosk_platform_upgrade_progress",
        ) from e
