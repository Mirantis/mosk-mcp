"""Get MOSK platform status tool.

This module provides the get_mosk_platform_status MCP tool that retrieves
comprehensive status information about a MOSK platform (Cluster CR on MCC),
including:
- Current MOSK release version
- Machine phases and readiness
- Cluster conditions (Helm, Ceph, Nodes, Kubernetes, etc.)
- Upgrade status if upgrade is in progress

This tool queries the MCC management cluster where Cluster CRs are managed.

Safety Level: READ_ONLY
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from mosk_mcp.core.exceptions import ResourceNotFoundError, ToolExecutionError
from mosk_mcp.observability.logging import get_logger
from mosk_mcp.tools.common.parsers import parse_mosk_condition_ready


if TYPE_CHECKING:
    from mosk_mcp.adapters.kubernetes import KubernetesAdapter


logger = get_logger(__name__)


class MoskPlatformPhase(str, Enum):
    """MOSK platform operational phases."""

    READY = "ready"
    UPGRADING = "upgrading"
    PROVISIONING = "provisioning"
    DEGRADED = "degraded"
    ERROR = "error"
    UNKNOWN = "unknown"


class MachinePhaseInfo(BaseModel):
    """Information about a machine's current phase."""

    name: str = Field(..., description="Machine name")
    phase: str = Field(..., description="Current LCM phase (Ready, Prepare, Deploy, Reconfigure)")
    is_ready: bool = Field(..., description="Whether machine is in Ready phase")


class ClusterConditionInfo(BaseModel):
    """Information about a cluster condition."""

    type: str = Field(..., description="Condition type (Helm, Ceph, Nodes, etc.)")
    ready: bool = Field(..., description="Whether condition is ready/healthy")
    message: str = Field(default="", description="Condition message")


class GetMoskPlatformStatusInput(BaseModel):
    """Input parameters for get_mosk_platform_status tool."""

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
    include_machine_details: bool = Field(
        default=True,
        description="Include per-machine phase information",
    )


class GetMoskPlatformStatusOutput(BaseModel):
    """Output from get_mosk_platform_status tool."""

    cluster_name: str = Field(..., description="Cluster name")
    namespace: str = Field(..., description="Cluster namespace")
    phase: MoskPlatformPhase = Field(..., description="Overall platform phase")

    # Release information
    current_release: str = Field(
        ..., description="Current MOSK release (e.g., 'mosk-17-4-6-25-1-1')"
    )
    target_release: str = Field(..., description="Target MOSK release from spec")
    is_upgrading: bool = Field(..., description="Whether an upgrade is in progress")

    # Machine status
    machines_total: int = Field(..., description="Total number of machines")
    machines_ready: int = Field(..., description="Number of machines in Ready phase")
    machine_phases: dict[str, int] = Field(
        default_factory=dict,
        description="Count of machines in each phase",
    )
    machines: list[MachinePhaseInfo] = Field(
        default_factory=list,
        description="Per-machine status (if include_machine_details=True)",
    )

    # Cluster conditions
    conditions: list[ClusterConditionInfo] = Field(
        default_factory=list,
        description="Cluster conditions from providerStatus",
    )
    all_conditions_ready: bool = Field(
        ...,
        description="Whether all important conditions are ready",
    )

    # Summary
    health_summary: str = Field(..., description="Human-readable health summary")
    warnings: list[str] = Field(default_factory=list, description="Warning messages")
    timestamp: str = Field(..., description="Query timestamp")


async def get_mosk_platform_status(
    mcc_adapter: KubernetesAdapter,
    input_data: GetMoskPlatformStatusInput,
) -> GetMoskPlatformStatusOutput:
    """Get MOSK platform status from MCC cluster.

    Retrieves comprehensive status information about a MOSK platform by
    querying the Cluster CR and associated Machine CRs on the MCC management
    cluster.

    This tool is useful for:
    - Checking current MOSK release version
    - Monitoring machine provisioning/upgrade progress
    - Verifying cluster health via conditions
    - Determining if an upgrade is in progress

    Args:
        mcc_adapter: Kubernetes adapter for MCC management cluster.
        input_data: Input parameters.

    Returns:
        GetMoskPlatformStatusOutput with comprehensive platform status.

    Raises:
        ResourceNotFoundError: If Cluster is not found.
        ToolExecutionError: If operation fails.
    """
    logger.info(
        "get_mosk_platform_status_start",
        cluster_name=input_data.cluster_name,
        namespace=input_data.namespace,
    )

    try:
        # Get Cluster CR
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

        # Extract release info
        provider_spec = cluster.get("spec", {}).get("providerSpec", {}).get("value", {})
        provider_status = cluster.get("status", {}).get("providerStatus", {})

        target_release = provider_spec.get("release", "unknown")
        current_release = provider_status.get("release") or target_release
        is_upgrading = current_release != target_release

        # Parse cluster conditions
        # Note: MOSK conditions use 'ready' field (bool) not 'status' field
        raw_conditions = provider_status.get("conditions", [])
        conditions: list[ClusterConditionInfo] = []
        important_conditions = {"Helm", "Ceph", "Nodes", "Kubernetes", "LCMAgent", "StackLight"}
        all_ready = True
        warnings: list[str] = []

        for cond in raw_conditions:
            cond_type = cond.get("type", "")
            is_ready = parse_mosk_condition_ready(cond)
            message = cond.get("message", "")

            conditions.append(
                ClusterConditionInfo(
                    type=cond_type,
                    ready=is_ready,
                    message=message[:200] if message else "",
                )
            )

            if cond_type in important_conditions and not is_ready:
                all_ready = False
                if message:
                    warnings.append(f"{cond_type}: {message[:100]}")

        # Get Machine CRs
        machines_list = await mcc_adapter.list_machines(namespace=input_data.namespace)

        # Filter machines belonging to this cluster
        cluster_machines: list[dict[str, Any]] = []
        for m in machines_list:
            owner_refs = m.get("metadata", {}).get("ownerReferences", [])
            labels = m.get("metadata", {}).get("labels", {})

            is_owned = any(
                ref.get("kind") == "Cluster" and ref.get("name") == input_data.cluster_name
                for ref in owner_refs
            )
            has_label = labels.get("cluster.sigs.k8s.io/cluster-name") == input_data.cluster_name

            if is_owned or has_label:
                cluster_machines.append(m)

        # Parse machine phases
        machine_phases: dict[str, int] = {}
        machines_info: list[MachinePhaseInfo] = []
        machines_ready = 0

        for machine in cluster_machines:
            name = machine.get("metadata", {}).get("name", "unknown")
            phase = machine.get("status", {}).get("phase", "Unknown")

            machine_phases[phase] = machine_phases.get(phase, 0) + 1

            is_ready = phase == "Ready"
            if is_ready:
                machines_ready += 1

            if input_data.include_machine_details:
                machines_info.append(
                    MachinePhaseInfo(
                        name=name,
                        phase=phase,
                        is_ready=is_ready,
                    )
                )

        machines_total = len(cluster_machines)

        # Determine overall phase
        if machines_total == 0:
            phase = MoskPlatformPhase.UNKNOWN
            health_summary = "No machines found for cluster"
        elif is_upgrading:
            phase = MoskPlatformPhase.UPGRADING
            health_summary = f"Upgrade in progress: {current_release} -> {target_release}"
        elif machines_ready == machines_total and all_ready:
            phase = MoskPlatformPhase.READY
            health_summary = f"Platform healthy - all {machines_total} machines ready"
        elif machines_ready == machines_total and not all_ready:
            phase = MoskPlatformPhase.DEGRADED
            not_ready_conds = [c.type for c in conditions if not c.ready]
            health_summary = (
                f"Machines ready but conditions not healthy: {', '.join(not_ready_conds)}"
            )
        elif machines_ready < machines_total:
            not_ready_count = machines_total - machines_ready
            if any(machine_phases.get(p, 0) > 0 for p in ["Prepare", "Deploy", "Reconfigure"]):
                phase = MoskPlatformPhase.PROVISIONING
                health_summary = f"Provisioning in progress - {not_ready_count}/{machines_total} machines not ready"
            else:
                phase = MoskPlatformPhase.DEGRADED
                health_summary = f"Degraded - {not_ready_count}/{machines_total} machines not ready"
        else:
            phase = MoskPlatformPhase.UNKNOWN
            health_summary = "Unable to determine platform status"

        result = GetMoskPlatformStatusOutput(
            cluster_name=input_data.cluster_name,
            namespace=input_data.namespace,
            phase=phase,
            current_release=current_release,
            target_release=target_release,
            is_upgrading=is_upgrading,
            machines_total=machines_total,
            machines_ready=machines_ready,
            machine_phases=machine_phases,
            machines=machines_info,
            conditions=conditions,
            all_conditions_ready=all_ready,
            health_summary=health_summary,
            warnings=warnings,
            timestamp=datetime.now(UTC).isoformat(),
        )

        logger.info(
            "get_mosk_platform_status_complete",
            cluster_name=input_data.cluster_name,
            phase=phase.value,
            machines_ready=machines_ready,
            machines_total=machines_total,
            is_upgrading=is_upgrading,
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
            "get_mosk_platform_status_error",
            cluster_name=input_data.cluster_name,
            error=str(e),
        )
        raise ToolExecutionError(
            f"Failed to get MOSK platform status: {e}",
            tool_name="get_mosk_platform_status",
        ) from e
