"""Get pod logs tool for RCA and troubleshooting.

This tool provides live Kubernetes pod log retrieval for root cause analysis.
Supports multiple ways to identify pods: by name, label selector, or both.

Safety Level: Read-only

This tool queries the Kubernetes API directly to fetch pod logs,
which is useful when OpenSearch/StackLight logs are not available
or when you need real-time log data.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from mosk_mcp.core.exceptions import ToolExecutionError, ValidationError
from mosk_mcp.observability.logging import get_logger
from mosk_mcp.tools.troubleshooting.models import (
    GetPodLogsOutput,
    PodLogEntry,
)


if TYPE_CHECKING:
    from mosk_mcp.adapters.kubernetes import KubernetesAdapter


logger = get_logger(__name__)


async def get_pod_logs(
    kubernetes_adapter: KubernetesAdapter,
    pod_name: str | None = None,
    namespace: str | None = None,
    label_selector: str | None = None,
    container: str | None = None,
    tail_lines: int | None = 500,
    since_seconds: int | None = None,
    previous: bool = False,
    timestamps: bool = False,
    limit_bytes: int | None = None,
) -> GetPodLogsOutput:
    """Get logs from Kubernetes pods for RCA and troubleshooting.

    This tool retrieves live container logs directly from the Kubernetes API.
    It's essential for root cause analysis when:
    - OpenSearch/StackLight is unavailable
    - You need real-time log data
    - You're debugging a specific pod or service
    - You need logs from crashed/previous containers

    Safety Level: Read-only

    Args:
        kubernetes_adapter: Authenticated KubernetesAdapter for cluster access.
        pod_name: Exact pod name. If provided, gets logs from this specific pod.
        namespace: Namespace to search in. Defaults to 'openstack' if not specified.
        label_selector: Label selector to find pods (e.g., 'app=nova-api',
            'application=keystone'). Returns logs from all matching pods.
        container: Container name. If pod has multiple containers and this
            is not specified, logs from the first container are returned.
        tail_lines: Number of lines from end of logs to return. Default is 500.
            Set to None for all logs (not recommended for large logs).
        since_seconds: Return logs newer than this many seconds.
            Useful for getting recent logs only (e.g., 3600 for last hour).
        previous: If True, return logs from previous terminated container.
            Essential for debugging CrashLoopBackOff pods.
        timestamps: If True, add RFC3339 timestamp to each log line.
        limit_bytes: Maximum bytes of logs to return. Useful for very large logs.

    Returns:
        GetPodLogsOutput with logs from each pod and summary statistics.

    Raises:
        ValidationError: If neither pod_name nor label_selector is provided.
        ToolExecutionError: If log retrieval fails completely.

    Common Use Cases:
        1. Debug specific pod:
           get_pod_logs(pod_name="nova-api-xyz", namespace="openstack")

        2. Get logs from all pods of a service:
           get_pod_logs(label_selector="application=nova", namespace="openstack")

        3. Debug crashed pod (previous container):
           get_pod_logs(pod_name="nova-api-xyz", previous=True)

        4. Get recent errors:
           get_pod_logs(label_selector="app=keystone", since_seconds=1800, tail_lines=200)

        5. OpenStack controller logs:
           get_pod_logs(label_selector="application=openstack-controller")

    OpenStack Service Label Examples:
        - Nova API: label_selector="application=nova,component=api"
        - Nova Compute: label_selector="application=nova,component=compute"
        - Neutron: label_selector="application=neutron"
        - Keystone: label_selector="application=keystone"
        - Glance: label_selector="application=glance"
        - Cinder: label_selector="application=cinder"
        - Heat: label_selector="application=heat"
        - Horizon: label_selector="application=horizon"
        - OpenStack Controller: label_selector="application=openstack-controller"
    """
    logger.info(
        "get_pod_logs_started",
        pod_name=pod_name,
        namespace=namespace,
        label_selector=label_selector,
        container=container,
        tail_lines=tail_lines,
        since_seconds=since_seconds,
        previous=previous,
    )

    # Validate inputs
    if not pod_name and not label_selector:
        raise ValidationError(
            "Either pod_name or label_selector must be provided",
            field="pod_name/label_selector",
            value=None,
        )

    # Default namespace for OpenStack workloads
    effective_namespace = namespace or "openstack"

    try:
        # Get logs from the adapter
        raw_results = await kubernetes_adapter.get_pod_logs(
            pod_name=pod_name,
            namespace=effective_namespace,
            label_selector=label_selector,
            container=container,
            tail_lines=tail_lines,
            since_seconds=since_seconds,
            previous=previous,
            timestamps=timestamps,
            limit_bytes=limit_bytes,
        )

        # Convert to output models
        pod_entries: list[PodLogEntry] = []
        for raw in raw_results:
            entry = PodLogEntry(
                pod_name=raw["pod_name"],
                namespace=raw["namespace"],
                container=raw.get("container"),
                available_containers=raw.get("available_containers", []),
                logs=raw.get("logs", ""),
                log_lines=raw.get("log_lines", 0),
                truncated=raw.get("truncated", False),
                error=raw.get("error"),
            )
            pod_entries.append(entry)

        # Calculate statistics
        successful = sum(1 for e in pod_entries if not e.error)
        failed = sum(1 for e in pod_entries if e.error)
        total_lines = sum(e.log_lines for e in pod_entries)

        # Build query info
        query_info = {
            "pod_name": pod_name,
            "namespace": effective_namespace,
            "label_selector": label_selector,
            "container": container,
            "tail_lines": tail_lines,
            "since_seconds": since_seconds,
            "previous": previous,
            "timestamps": timestamps,
            "limit_bytes": limit_bytes,
        }

        result = GetPodLogsOutput(
            pods=pod_entries,
            total_pods=len(pod_entries),
            successful_pods=successful,
            failed_pods=failed,
            total_log_lines=total_lines,
            query_info=query_info,
            timestamp=datetime.now(UTC).isoformat(),
        )

        logger.info(
            "get_pod_logs_completed",
            total_pods=result.total_pods,
            successful_pods=result.successful_pods,
            total_log_lines=result.total_log_lines,
        )

        return result

    except ValidationError:
        raise
    except Exception as e:
        logger.error(
            "get_pod_logs_failed",
            error=str(e),
            pod_name=pod_name,
            label_selector=label_selector,
        )
        raise ToolExecutionError(
            f"Failed to get pod logs: {e}",
            tool_name="get_pod_logs",
            phase="execution",
        ) from e


# Tool metadata for registration
TOOL_NAME = "get_pod_logs"
TOOL_DESCRIPTION = """Get live Kubernetes pod logs for RCA and troubleshooting.

Retrieves container logs directly from Kubernetes API. Essential for:
- Real-time debugging when StackLight/OpenSearch is unavailable
- Analyzing crashed pods (previous container logs)
- Investigating specific service issues

Supports finding pods by:
- Exact pod name
- Label selector (e.g., 'application=nova', 'app=keystone')

This is a READ-ONLY tool that does not modify any resources."""

TOOL_TAGS = ["troubleshooting", "logs", "kubernetes", "rca", "read-only"]
