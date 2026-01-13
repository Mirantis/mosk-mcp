"""Get rollout status tool.

This module provides the get_rollout_status tool that retrieves
Deployment and StatefulSet rollout status for OpenStack services,
tracking progress and identifying stuck or failed rollouts.

Safety Level: READ_ONLY
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from mosk_mcp.core.exceptions import ToolExecutionError
from mosk_mcp.observability.logging import get_logger
from mosk_mcp.tools.operations_visibility.models import (
    Condition,
    ConditionStatus,
    DeploymentRolloutInfo,
    GetRolloutStatusInput,
    GetRolloutStatusOutput,
    RolloutStatus,
    StatefulSetRolloutInfo,
)


if TYPE_CHECKING:
    from mosk_mcp.adapters.kubernetes import KubernetesAdapter


logger = get_logger(__name__)


def _parse_condition(cond_data: dict[str, Any]) -> Condition:
    """Parse a Kubernetes condition.

    Args:
        cond_data: Raw condition from API.

    Returns:
        Parsed Condition object.
    """
    status_str = cond_data.get("status", "Unknown")
    try:
        status = ConditionStatus(status_str)
    except ValueError:
        status = ConditionStatus.UNKNOWN

    return Condition(
        type=cond_data.get("type", "Unknown"),
        status=status,
        reason=cond_data.get("reason"),
        message=cond_data.get("message"),
        last_transition_time=cond_data.get("lastTransitionTime"),
        last_update_time=cond_data.get("lastUpdateTime"),
    )


def _determine_deployment_rollout_status(
    deployment: dict[str, Any],
) -> tuple[RolloutStatus, int, bool]:
    """Determine rollout status for a Deployment.

    Args:
        deployment: Deployment resource.

    Returns:
        Tuple of (status, progress_percent, is_complete).
    """
    status = deployment.get("status", {})
    spec = deployment.get("spec", {})

    replicas = spec.get("replicas", 1)
    updated_replicas = status.get("updatedReplicas", 0)
    available_replicas = status.get("availableReplicas", 0)
    ready_replicas = status.get("readyReplicas", 0)
    unavailable = status.get("unavailableReplicas", 0)

    # Check conditions
    conditions = status.get("conditions", [])
    progressing_cond = next((c for c in conditions if c.get("type") == "Progressing"), None)
    available_cond = next((c for c in conditions if c.get("type") == "Available"), None)

    # Calculate progress
    progress = int(updated_replicas / replicas * 100) if replicas > 0 else 100

    # Determine status
    is_complete = (
        updated_replicas == replicas
        and available_replicas == replicas
        and ready_replicas == replicas
    )

    if is_complete:
        return RolloutStatus.COMPLETE, 100, True

    if progressing_cond:
        reason = progressing_cond.get("reason", "")
        if reason == "ReplicaSetUpdated" and progressing_cond.get("status") == "True":
            return RolloutStatus.PROGRESSING, progress, False
        elif reason == "ProgressDeadlineExceeded":
            return RolloutStatus.FAILED, progress, False
        elif reason == "Paused":
            return RolloutStatus.PAUSED, progress, False

    if available_cond and available_cond.get("status") == "True":
        return RolloutStatus.AVAILABLE, progress, False

    if unavailable > 0:
        return RolloutStatus.PROGRESSING, progress, False

    return RolloutStatus.PROGRESSING, progress, False


def _parse_deployment(deployment: dict[str, Any]) -> DeploymentRolloutInfo:
    """Parse a Deployment resource to rollout info.

    Args:
        deployment: Deployment resource.

    Returns:
        Parsed DeploymentRolloutInfo.
    """
    metadata = deployment.get("metadata", {})
    spec = deployment.get("spec", {})
    status = deployment.get("status", {})

    name = metadata.get("name", "unknown")
    namespace = metadata.get("namespace", "default")
    labels = metadata.get("labels", {})

    # Determine service from labels
    service = labels.get("application", labels.get("app", name.split("-")[0]))

    # Get replica counts
    replicas_desired = spec.get("replicas", 1)
    replicas_current = status.get("replicas", 0)
    replicas_updated = status.get("updatedReplicas", 0)
    replicas_available = status.get("availableReplicas", 0)
    replicas_unavailable = status.get("unavailableReplicas", 0)

    # Determine status
    rollout_status, progress, is_complete = _determine_deployment_rollout_status(deployment)

    # Get strategy info
    strategy_spec = spec.get("strategy", {})
    strategy = strategy_spec.get("type", "RollingUpdate")
    max_surge = None
    max_unavailable = None

    if strategy == "RollingUpdate":
        rolling_update = strategy_spec.get("rollingUpdate", {})
        max_surge = str(rolling_update.get("maxSurge", "25%"))
        max_unavailable = str(rolling_update.get("maxUnavailable", "25%"))

    # Parse conditions
    conditions = [_parse_condition(c) for c in status.get("conditions", [])]

    return DeploymentRolloutInfo(
        name=name,
        namespace=namespace,
        service=service,
        status=rollout_status,
        replicas_desired=replicas_desired,
        replicas_current=replicas_current,
        replicas_updated=replicas_updated,
        replicas_available=replicas_available,
        replicas_unavailable=replicas_unavailable,
        progress_percent=progress,
        strategy=strategy,
        max_surge=max_surge,
        max_unavailable=max_unavailable,
        conditions=conditions,
        generation=metadata.get("generation", 1),
        observed_generation=status.get("observedGeneration", 1),
        is_complete=is_complete,
    )


def _parse_statefulset(sts: dict[str, Any]) -> StatefulSetRolloutInfo:
    """Parse a StatefulSet resource to rollout info.

    Args:
        sts: StatefulSet resource.

    Returns:
        Parsed StatefulSetRolloutInfo.
    """
    metadata = sts.get("metadata", {})
    spec = sts.get("spec", {})
    status = sts.get("status", {})

    name = metadata.get("name", "unknown")
    namespace = metadata.get("namespace", "default")
    labels = metadata.get("labels", {})

    # Determine service from labels
    service = labels.get("application", labels.get("app", name.split("-")[0]))

    # Get replica counts
    replicas_desired = spec.get("replicas", 1)
    replicas_current = status.get("currentReplicas", 0)
    replicas_ready = status.get("readyReplicas", 0)
    replicas_updated = status.get("updatedReplicas", 0)

    # Get revision info
    current_revision = status.get("currentRevision", "")
    update_revision = status.get("updateRevision", "")

    # Calculate progress
    progress = int(replicas_updated / replicas_desired * 100) if replicas_desired > 0 else 100

    # Determine if complete
    is_complete = (
        replicas_updated == replicas_desired
        and replicas_ready == replicas_desired
        and current_revision == update_revision
    )

    # Determine status
    if is_complete:
        rollout_status = RolloutStatus.COMPLETE
    elif replicas_updated < replicas_desired or replicas_ready < replicas_desired:
        rollout_status = RolloutStatus.PROGRESSING
    else:
        rollout_status = RolloutStatus.AVAILABLE

    # Get strategy info
    update_strategy = spec.get("updateStrategy", {})
    strategy_type = update_strategy.get("type", "RollingUpdate")
    partition = None
    if strategy_type == "RollingUpdate":
        partition = update_strategy.get("rollingUpdate", {}).get("partition")

    return StatefulSetRolloutInfo(
        name=name,
        namespace=namespace,
        service=service,
        status=rollout_status,
        replicas_desired=replicas_desired,
        replicas_current=replicas_current,
        replicas_ready=replicas_ready,
        replicas_updated=replicas_updated,
        current_revision=current_revision,
        update_revision=update_revision,
        progress_percent=progress,
        update_strategy=strategy_type,
        partition=partition,
        is_complete=is_complete,
    )


async def get_rollout_status(
    kubernetes_adapter: KubernetesAdapter,
    input_data: GetRolloutStatusInput,
) -> GetRolloutStatusOutput:
    """Get rollout status for OpenStack service workloads.

    Retrieves status for Deployments and StatefulSets, tracking
    rollout progress and identifying issues.

    Args:
        kubernetes_adapter: Kubernetes client adapter.
        input_data: Filter parameters.

    Returns:
        Rollout status for all workloads.

    Raises:
        ToolExecutionError: If operation fails.
    """
    logger.info(
        "get_rollout_status_start",
        namespace=input_data.namespace,
        service_filter=input_data.service_filter,
    )

    try:
        # Build label selector
        label_selector = None
        if input_data.service_filter:
            label_selector = f"application={input_data.service_filter}"

        # Get Deployments
        raw_deployments = await kubernetes_adapter.list(
            kind="Deployment",
            namespace=input_data.namespace,
            label_selector=label_selector,
        )

        # Get StatefulSets
        raw_statefulsets = await kubernetes_adapter.list(
            kind="StatefulSet",
            namespace=input_data.namespace,
            label_selector=label_selector,
        )

        # Parse deployments
        deployments = [_parse_deployment(d) for d in raw_deployments]

        # Parse statefulsets
        statefulsets = [_parse_statefulset(s) for s in raw_statefulsets]

        # Calculate statistics
        all_workloads = deployments + statefulsets
        total_workloads = len(all_workloads)

        workloads_complete = sum(1 for w in all_workloads if w.is_complete)
        workloads_in_progress = sum(
            1 for w in all_workloads if w.status == RolloutStatus.PROGRESSING
        )
        workloads_failed = sum(1 for w in all_workloads if w.status == RolloutStatus.FAILED)

        # Calculate overall progress
        if total_workloads > 0:
            total_progress = sum(getattr(w, "progress_percent", 0) for w in all_workloads)
            overall_progress = int(total_progress / total_workloads)
        else:
            overall_progress = 100

        all_complete = workloads_complete == total_workloads

        # Identify stuck workloads
        stuck_workloads: list[str] = []
        for w in all_workloads:
            if w.status == RolloutStatus.FAILED:
                stuck_workloads.append(f"{w.name} (failed)")
            elif w.status == RolloutStatus.PROGRESSING and w.progress_percent < 50:
                # Check if it seems stuck (low progress for extended time)
                stuck_workloads.append(f"{w.name} (progressing: {w.progress_percent}%)")

        # Generate recommendations
        recommendations: list[str] = []
        if workloads_failed > 0:
            recommendations.append(
                f"{workloads_failed} workload(s) have failed rollouts - check pod events"
            )
        if stuck_workloads:
            recommendations.append(
                "Some workloads appear stuck - check pod status and resource constraints"
            )
        if workloads_in_progress > 5:
            recommendations.append(
                "Many concurrent rollouts in progress - this may increase cluster load"
            )

        result = GetRolloutStatusOutput(
            namespace=input_data.namespace,
            deployments=deployments,
            statefulsets=statefulsets,
            total_workloads=total_workloads,
            workloads_complete=workloads_complete,
            workloads_in_progress=workloads_in_progress,
            workloads_failed=workloads_failed,
            overall_progress_percent=overall_progress,
            all_rollouts_complete=all_complete,
            stuck_workloads=stuck_workloads,
            recommendations=recommendations,
            timestamp=datetime.now(UTC).isoformat(),
        )

        logger.info(
            "get_rollout_status_complete",
            total_workloads=total_workloads,
            complete=workloads_complete,
            in_progress=workloads_in_progress,
        )

        return result

    except Exception as e:
        logger.error(
            "get_rollout_status_error",
            error=str(e),
        )
        raise ToolExecutionError(
            f"Failed to get rollout status: {e}",
            tool_name="get_rollout_status",
        ) from e
