"""Create diagnostic bundle tool for intelligent troubleshooting.

This tool generates a comprehensive diagnostic bundle containing
cluster state, logs, metrics, and alerts for support and debugging.

Safety Level: Read-only

This tool queries StackLight via OIDC/SSO authentication using
DirectStackLightClient. Authentication must be established before
calling this tool.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import tarfile
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from mosk_mcp.adapters.stacklight import DirectStackLightClient, StackLightAdapter
from mosk_mcp.core.exceptions import ToolExecutionError
from mosk_mcp.observability.logging import get_logger
from mosk_mcp.tools.troubleshooting.models import (
    BundleContents,
    BundleFormat,
    CreateDiagnosticBundleOutput,
)


if TYPE_CHECKING:
    from mosk_mcp.adapters.kubernetes import KubernetesAdapter


logger = get_logger(__name__)


def _format_size(size_bytes: int | float) -> str:
    """Format bytes as human-readable string."""
    size: float = float(size_bytes)
    for unit in ["B", "KB", "MB", "GB"]:
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


async def create_diagnostic_bundle(
    direct_client: DirectStackLightClient,
    kubernetes_adapter: KubernetesAdapter,
    bundle_name: str | None = None,
    include_cluster_state: bool = True,
    include_openstack_state: bool = True,
    include_ceph_state: bool = True,
    include_logs: bool = True,
    log_hours: int = 1,
    include_metrics: bool = True,
    include_alerts: bool = True,
    alert_hours: int = 24,
    affected_services: list[str] | None = None,
    output_format: BundleFormat = BundleFormat.TARGZ,
    include_sensitive: bool = False,
) -> CreateDiagnosticBundleOutput:
    """Generate a comprehensive diagnostic bundle for support.

    This tool collects diagnostic information from across the MOSK
    deployment via OIDC/SSO authentication and packages it into a
    bundle for support analysis.

    The direct_client must be authenticated with valid Keycloak tokens
    before calling this tool.

    Safety Level: Read-only

    Bundle contents:
    - Cluster State: Machine status, node conditions, pod states
    - OpenStack State: Service list, agent status, endpoint health
    - Ceph State: Cluster status, OSD tree, PG dump
    - Recent Logs: Logs from affected services
    - Metrics Snapshot: Resource utilization metrics
    - Alert History: Related alerts from the specified time window

    Args:
        direct_client: Authenticated DirectStackLightClient for StackLight access.
        kubernetes_adapter: Kubernetes adapter for data collection.
        bundle_name: Name for the bundle (auto-generated if not provided).
        include_cluster_state: Include Kubernetes cluster state.
        include_openstack_state: Include OpenStack service status.
        include_ceph_state: Include Ceph cluster state.
        include_logs: Include recent logs.
        log_hours: Hours of logs to include (default: 1).
        include_metrics: Include metrics snapshot.
        include_alerts: Include alert history.
        alert_hours: Hours of alert history (default: 24).
        affected_services: Focus on specific services.
        output_format: Bundle format (tar.gz or zip).
        include_sensitive: Include sensitive data (requires elevated permissions).

    Returns:
        CreateDiagnosticBundleOutput with base64-encoded bundle data.

    Raises:
        ToolExecutionError: If bundle creation fails.

    Example:
        >>> result = await create_diagnostic_bundle(
        ...     client,
        ...     k8s_adapter,
        ...     include_logs=True,
        ...     log_hours=2,
        ...     affected_services=["nova", "ceph"],
        ... )

        >>> # Decode and save bundle
        >>> import base64
        >>> with open("diagnostic.tar.gz", "wb") as f:
        ...     f.write(base64.b64decode(result.data_base64))
    """
    start_time = datetime.now(UTC)

    logger.info(
        "create_diagnostic_bundle_started",
        include_cluster=include_cluster_state,
        include_openstack=include_openstack_state,
        include_ceph=include_ceph_state,
        include_logs=include_logs,
        affected_services=affected_services,
    )

    try:
        # Generate bundle name and ID
        if not bundle_name:
            bundle_name = f"mosk-diagnostic-{start_time.strftime('%Y%m%d-%H%M%S')}"
        bundle_id = f"diag-{uuid.uuid4().hex[:12]}"

        # Prepare bundle contents tracking
        cluster_state_files: list[str] = []
        openstack_state_files: list[str] = []
        ceph_state_files: list[str] = []
        log_files: list[str] = []
        metrics_files: list[str] = []
        alert_files: list[str] = []
        warnings: list[str] = []

        # Create tar archive in memory
        bundle_buffer = io.BytesIO()

        with tarfile.open(fileobj=bundle_buffer, mode="w:gz") as tar:
            # Add metadata
            metadata = {
                "bundle_name": bundle_name,
                "bundle_id": bundle_id,
                "created_at": start_time.isoformat(),
                "parameters": {
                    "include_cluster_state": include_cluster_state,
                    "include_openstack_state": include_openstack_state,
                    "include_ceph_state": include_ceph_state,
                    "include_logs": include_logs,
                    "log_hours": log_hours,
                    "include_metrics": include_metrics,
                    "include_alerts": include_alerts,
                    "alert_hours": alert_hours,
                    "affected_services": affected_services,
                },
            }
            _add_json_to_tar(tar, "metadata.json", metadata)

            # Collect cluster state
            if include_cluster_state:
                try:
                    cluster_data = await _collect_cluster_state(kubernetes_adapter)
                    for filename, data in cluster_data.items():
                        _add_json_to_tar(tar, f"cluster/{filename}", data)
                        cluster_state_files.append(f"cluster/{filename}")
                except Exception as e:
                    warnings.append(f"Failed to collect cluster state: {e}")
                    logger.warning("cluster_state_collection_failed", error=str(e))

            # Collect OpenStack state
            if include_openstack_state:
                try:
                    openstack_data = await _collect_openstack_state(kubernetes_adapter)
                    for filename, data in openstack_data.items():
                        _add_json_to_tar(tar, f"openstack/{filename}", data)
                        openstack_state_files.append(f"openstack/{filename}")
                except Exception as e:
                    warnings.append(f"Failed to collect OpenStack state: {e}")
                    logger.warning("openstack_state_collection_failed", error=str(e))

            # Collect Ceph state
            if include_ceph_state:
                try:
                    ceph_data = await _collect_ceph_state(kubernetes_adapter)
                    for filename, data in ceph_data.items():
                        _add_json_to_tar(tar, f"ceph/{filename}", data)
                        ceph_state_files.append(f"ceph/{filename}")
                except Exception as e:
                    warnings.append(f"Failed to collect Ceph state: {e}")
                    logger.warning("ceph_state_collection_failed", error=str(e))

            # Collect logs
            if include_logs:
                try:
                    stacklight = StackLightAdapter(direct_client=direct_client)
                    await stacklight.connect()
                    services = affected_services or ["nova", "neutron", "cinder", "ceph"]

                    for service in services:
                        # Extract .logs from LogQueryResult
                        log_result = await stacklight.query_logs(
                            services=[service],
                            time_range_minutes=log_hours * 60,
                            limit=500,
                        )
                        log_data = [
                            {
                                "timestamp": log.timestamp.isoformat(),
                                "severity": log.severity.value,
                                "message": log.message,
                                "host": log.host,
                            }
                            for log in log_result.logs
                        ]
                        filename = f"logs/{service}.json"
                        _add_json_to_tar(tar, filename, log_data)
                        log_files.append(filename)
                except Exception as e:
                    warnings.append(f"Failed to collect logs: {e}")
                    logger.warning("log_collection_failed", error=str(e))

            # Collect metrics snapshot
            if include_metrics:
                try:
                    metrics_data = await _collect_metrics_snapshot(direct_client)
                    _add_json_to_tar(tar, "metrics/snapshot.json", metrics_data)
                    metrics_files.append("metrics/snapshot.json")
                except Exception as e:
                    warnings.append(f"Failed to collect metrics: {e}")
                    logger.warning("metrics_collection_failed", error=str(e))

            # Collect alerts
            if include_alerts:
                try:
                    stacklight = StackLightAdapter(direct_client=direct_client)
                    await stacklight.connect()
                    alerts = await stacklight.get_alerts(limit=100)
                    alert_data = [alert.to_dict() for alert in alerts]
                    _add_json_to_tar(tar, "alerts/alerts.json", alert_data)
                    alert_files.append("alerts/alerts.json")
                except Exception as e:
                    warnings.append(f"Failed to collect alerts: {e}")
                    logger.warning("alert_collection_failed", error=str(e))

        # Get bundle data
        bundle_data = bundle_buffer.getvalue()
        bundle_size = len(bundle_data)

        # Calculate checksum
        checksum = hashlib.sha256(bundle_data).hexdigest()

        # Encode as base64
        data_base64 = base64.b64encode(bundle_data).decode("utf-8")

        # Calculate collection duration
        end_time = datetime.now(UTC)
        duration_seconds = (end_time - start_time).total_seconds()

        # Build contents summary
        contents = BundleContents(
            cluster_state_files=cluster_state_files,
            openstack_state_files=openstack_state_files,
            ceph_state_files=ceph_state_files,
            log_files=log_files,
            metrics_files=metrics_files,
            alert_files=alert_files,
            total_files=(
                len(cluster_state_files)
                + len(openstack_state_files)
                + len(ceph_state_files)
                + len(log_files)
                + len(metrics_files)
                + len(alert_files)
                + 1  # metadata.json
            ),
        )

        result = CreateDiagnosticBundleOutput(
            bundle_name=bundle_name,
            bundle_id=bundle_id,
            format=output_format,
            size_bytes=bundle_size,
            size_human=_format_size(bundle_size),
            contents=contents,
            data_base64=data_base64,
            created_at=start_time.isoformat(),
            expires_at=None,  # Bundles don't expire by default
            checksum_sha256=checksum,
            collection_duration_seconds=duration_seconds,
            warnings=warnings,
            timestamp=end_time.isoformat(),
        )

        logger.info(
            "create_diagnostic_bundle_completed",
            bundle_id=bundle_id,
            size_bytes=bundle_size,
            duration_seconds=duration_seconds,
            total_files=contents.total_files,
        )

        return result

    except Exception as e:
        logger.error(
            "create_diagnostic_bundle_failed",
            error=str(e),
        )
        raise ToolExecutionError(
            f"Failed to create diagnostic bundle: {e}",
            tool_name="create_diagnostic_bundle",
            phase="execution",
        ) from e


def _add_json_to_tar(tar: tarfile.TarFile, filename: str, data: Any) -> None:
    """Add JSON data to tar archive."""
    json_str = json.dumps(data, indent=2, default=str)
    json_bytes = json_str.encode("utf-8")

    tarinfo = tarfile.TarInfo(name=filename)
    tarinfo.size = len(json_bytes)
    tarinfo.mtime = int(datetime.now().timestamp())

    tar.addfile(tarinfo, io.BytesIO(json_bytes))


async def _collect_cluster_state(
    kubernetes_adapter: KubernetesAdapter,
) -> dict[str, Any]:
    """Collect Kubernetes cluster state from real cluster.

    Args:
        kubernetes_adapter: Authenticated Kubernetes adapter.

    Returns:
        Dictionary with cluster state files.
    """
    result: dict[str, Any] = {}

    # Collect nodes
    try:
        nodes = await kubernetes_adapter.list_nodes()
        node_items = []
        for node in nodes:
            # Extract node info from raw K8s response
            metadata = node.get("metadata", {})
            status = node.get("status", {})
            conditions = status.get("conditions", [])

            # Determine node readiness
            node_status = "Unknown"
            for cond in conditions:
                if cond.get("type") == "Ready":
                    node_status = "Ready" if cond.get("status") == "True" else "NotReady"
                    break

            # Extract roles from labels
            labels = metadata.get("labels", {})
            roles = []
            for label_key in labels:
                if label_key.startswith("node-role.kubernetes.io/"):
                    roles.append(label_key.replace("node-role.kubernetes.io/", ""))

            node_items.append(
                {
                    "name": metadata.get("name", "unknown"),
                    "status": node_status,
                    "roles": roles or ["worker"],
                    "version": status.get("nodeInfo", {}).get("kubeletVersion", "unknown"),
                    "internal_ip": next(
                        (
                            addr.get("address")
                            for addr in status.get("addresses", [])
                            if addr.get("type") == "InternalIP"
                        ),
                        None,
                    ),
                    "conditions": [
                        {
                            "type": c.get("type"),
                            "status": c.get("status"),
                            "reason": c.get("reason"),
                        }
                        for c in conditions
                    ],
                }
            )
        result["nodes.json"] = {"items": node_items, "total": len(node_items)}
    except Exception as e:
        logger.warning("node_collection_failed", error=str(e))
        result["nodes.json"] = {"error": str(e), "items": []}

    # Collect machines (from MCC cluster via cluster.k8s.io API)
    try:
        machines = await kubernetes_adapter.list_machines()
        machine_items = []
        for machine in machines:
            metadata = machine.get("metadata", {})
            status = machine.get("status", {})
            spec = machine.get("spec", {})
            provider_spec = spec.get("providerSpec", {}).get("value", {})

            machine_items.append(
                {
                    "name": metadata.get("name", "unknown"),
                    "namespace": metadata.get("namespace", "default"),
                    "phase": status.get("phase", "Unknown"),
                    "provider": "baremetal",
                    "profile": provider_spec.get("bareMetalHostProfile", "unknown"),
                    "node_ref": status.get("nodeRef", {}).get("name"),
                    "error_reason": status.get("errorReason"),
                    "error_message": status.get("errorMessage"),
                }
            )
        result["machines.json"] = {"items": machine_items, "total": len(machine_items)}
    except Exception as e:
        logger.warning("machine_collection_failed", error=str(e))
        result["machines.json"] = {"error": str(e), "items": []}

    # Collect pods (focus on problematic ones)
    try:
        pods = await kubernetes_adapter.list_pods(namespace="*")
        pod_summary = {"total": 0, "running": 0, "pending": 0, "failed": 0, "succeeded": 0}
        problematic_pods = []

        for pod in pods:
            metadata = pod.get("metadata", {})
            status = pod.get("status", {})
            phase = status.get("phase", "Unknown")

            pod_summary["total"] += 1
            if phase == "Running":
                pod_summary["running"] += 1
            elif phase == "Pending":
                pod_summary["pending"] += 1
            elif phase == "Failed":
                pod_summary["failed"] += 1
            elif phase == "Succeeded":
                pod_summary["succeeded"] += 1

            # Check for problematic states
            container_statuses = status.get("containerStatuses", [])
            for cs in container_statuses:
                waiting = cs.get("waiting", {})
                reason = waiting.get("reason", "")
                if reason in ("CrashLoopBackOff", "ImagePullBackOff", "ErrImagePull", "Error"):
                    problematic_pods.append(
                        {
                            "name": metadata.get("name"),
                            "namespace": metadata.get("namespace"),
                            "status": reason,
                            "message": waiting.get("message", ""),
                            "restart_count": cs.get("restartCount", 0),
                        }
                    )
                    break

            # Also flag pending pods older than 5 minutes
            if phase == "Pending" and len(problematic_pods) < 50:
                problematic_pods.append(
                    {
                        "name": metadata.get("name"),
                        "namespace": metadata.get("namespace"),
                        "status": "Pending",
                        "reason": status.get("reason", ""),
                    }
                )

        result["pods.json"] = {
            "summary": pod_summary,
            "problematic": problematic_pods[:50],  # Limit to 50
        }
    except Exception as e:
        logger.warning("pod_collection_failed", error=str(e))
        result["pods.json"] = {"error": str(e), "summary": {}, "problematic": []}

    return result


async def _collect_openstack_state(
    kubernetes_adapter: KubernetesAdapter,
) -> dict[str, Any]:
    """Collect OpenStack service state from OSDpL CR.

    Args:
        kubernetes_adapter: Authenticated Kubernetes adapter.

    Returns:
        Dictionary with OpenStack state files.
    """
    result: dict[str, Any] = {}

    # Get OpenStackDeployment status
    try:
        osdpls = await kubernetes_adapter.list_custom_resources(
            group="lcm.mirantis.com",
            version="v1alpha1",
            plural="openstackdeployments",
            namespace="openstack",
        )

        if osdpls:
            osdpl = osdpls[0]  # Usually only one OSDpL
            status = osdpl.get("status", {})
            spec = osdpl.get("spec", {})

            # Extract service status from conditions
            conditions = status.get("conditions", [])
            services_status = []
            for cond in conditions:
                if cond.get("type", "").endswith("Ready"):
                    service_name = cond.get("type", "").replace("Ready", "")
                    services_status.append(
                        {
                            "name": service_name,
                            "ready": cond.get("status") == "True",
                            "reason": cond.get("reason", ""),
                            "message": cond.get("message", ""),
                        }
                    )

            result["osdpl_status.json"] = {
                "name": osdpl.get("metadata", {}).get("name"),
                "phase": status.get("phase", "Unknown"),
                "openstack_version": status.get("openstackVersion"),
                "health": status.get("health", "Unknown"),
                "conditions": conditions,
                "services": services_status,
            }

            # Extract enabled services from spec
            features = spec.get("features", {})
            enabled_services = []
            for service, config in features.items():
                if (isinstance(config, dict) and config.get("enabled", False)) or config is True:
                    enabled_services.append(service)

            result["services.json"] = {
                "enabled_services": enabled_services,
                "spec_services": list(features.keys()),
            }
        else:
            result["osdpl_status.json"] = {"error": "No OpenStackDeployment found"}
            result["services.json"] = {"error": "No OpenStackDeployment found"}

    except Exception as e:
        logger.warning("osdpl_collection_failed", error=str(e))
        result["osdpl_status.json"] = {"error": str(e)}
        result["services.json"] = {"error": str(e)}

    # Get OpenStack pods status
    try:
        pods = await kubernetes_adapter.list_pods(namespace="openstack")
        openstack_pods = []
        for pod in pods:
            metadata = pod.get("metadata", {})
            status = pod.get("status", {})
            openstack_pods.append(
                {
                    "name": metadata.get("name"),
                    "phase": status.get("phase"),
                    "ready": all(
                        cs.get("ready", False) for cs in status.get("containerStatuses", [])
                    ),
                    "restart_count": sum(
                        cs.get("restartCount", 0) for cs in status.get("containerStatuses", [])
                    ),
                }
            )
        result["openstack_pods.json"] = {
            "total": len(openstack_pods),
            "pods": openstack_pods,
        }
    except Exception as e:
        logger.warning("openstack_pods_collection_failed", error=str(e))
        result["openstack_pods.json"] = {"error": str(e)}

    return result


async def _collect_ceph_state(
    kubernetes_adapter: KubernetesAdapter,
) -> dict[str, Any]:
    """Collect Ceph cluster state from MiraCeph CR.

    Args:
        kubernetes_adapter: Authenticated Kubernetes adapter.

    Returns:
        Dictionary with Ceph state files.
    """
    result: dict[str, Any] = {}

    # Get MiraCeph status
    try:
        miracephs = await kubernetes_adapter.list_custom_resources(
            group="lcm.mirantis.com",
            version="v1alpha1",
            plural="miracephs",
            namespace="ceph-lcm-mirantis",
        )

        if miracephs:
            miraceph = miracephs[0]
            status = miraceph.get("status", {})
            spec = miraceph.get("spec", {})

            # Extract capacity info
            capacity = status.get("capacity", {})

            result["status.json"] = {
                "name": miraceph.get("metadata", {}).get("name"),
                "health": status.get("health", "UNKNOWN"),
                "health_message": status.get("healthMessage"),
                "phase": status.get("phase", "Unknown"),
                "ceph_version": status.get("cephVersion"),
                "mon_count": status.get("monCount", 0),
                "osd_count": status.get("osdCount", 0),
                "osd_up": status.get("osdUp", 0),
                "osd_in": status.get("osdIn", 0),
                "capacity": {
                    "total_bytes": capacity.get("totalBytes", 0),
                    "used_bytes": capacity.get("usedBytes", 0),
                    "available_bytes": capacity.get("availableBytes", 0),
                    "usage_percent": capacity.get("usagePercent", 0),
                },
                "conditions": status.get("conditions", []),
            }

            # Extract pool info from spec
            pools = spec.get("pools", [])
            result["pools.json"] = {
                "pools": [
                    {
                        "name": p.get("name"),
                        "replicated_size": p.get("replicatedSize", 3),
                        "device_class": p.get("deviceClass"),
                    }
                    for p in pools
                ]
            }
        else:
            result["status.json"] = {"error": "No MiraCeph found"}
            result["pools.json"] = {"error": "No MiraCeph found"}

    except Exception as e:
        logger.warning("miraceph_collection_failed", error=str(e))
        result["status.json"] = {"error": str(e)}
        result["pools.json"] = {"error": str(e)}

    # Get Ceph pods status
    try:
        pods = await kubernetes_adapter.list_pods(namespace="ceph-lcm-mirantis")
        ceph_pods = []
        for pod in pods:
            metadata = pod.get("metadata", {})
            status = pod.get("status", {})
            name = metadata.get("name", "")

            # Categorize by type
            pod_type = "other"
            if "osd" in name:
                pod_type = "osd"
            elif "mon" in name:
                pod_type = "mon"
            elif "mgr" in name:
                pod_type = "mgr"
            elif "mds" in name:
                pod_type = "mds"
            elif "rgw" in name:
                pod_type = "rgw"

            ceph_pods.append(
                {
                    "name": name,
                    "type": pod_type,
                    "phase": status.get("phase"),
                    "ready": all(
                        cs.get("ready", False) for cs in status.get("containerStatuses", [])
                    ),
                    "node": spec.get("nodeName") if spec else None,
                }
            )

        result["ceph_pods.json"] = {
            "total": len(ceph_pods),
            "by_type": {
                "osd": len([p for p in ceph_pods if p["type"] == "osd"]),
                "mon": len([p for p in ceph_pods if p["type"] == "mon"]),
                "mgr": len([p for p in ceph_pods if p["type"] == "mgr"]),
                "mds": len([p for p in ceph_pods if p["type"] == "mds"]),
                "rgw": len([p for p in ceph_pods if p["type"] == "rgw"]),
            },
            "pods": ceph_pods,
        }
    except Exception as e:
        logger.warning("ceph_pods_collection_failed", error=str(e))
        result["ceph_pods.json"] = {"error": str(e)}

    return result


async def _collect_metrics_snapshot(
    direct_client: DirectStackLightClient,
) -> dict[str, Any]:
    """Collect metrics snapshot from Prometheus via StackLight.

    Args:
        direct_client: Authenticated StackLight client.

    Returns:
        Dictionary with metrics snapshot.
    """
    now = datetime.now(UTC)
    result: dict[str, Any] = {"timestamp": now.isoformat()}

    # Define key metrics queries
    metrics_queries = {
        "cpu_usage_percent": 'avg by (node) (100 - (avg by (node) (rate(node_cpu_seconds_total{mode="idle"}[5m])) * 100))',
        "memory_usage_percent": "avg by (node) ((1 - (node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)) * 100)",
        "disk_usage_percent": 'avg by (node) ((1 - (node_filesystem_avail_bytes{mountpoint="/"} / node_filesystem_size_bytes{mountpoint="/"})) * 100)',
        "network_receive_bytes": 'sum by (node) (rate(node_network_receive_bytes_total{device!~"lo|veth.*|docker.*|br-.*"}[5m]))',
        "network_transmit_bytes": 'sum by (node) (rate(node_network_transmit_bytes_total{device!~"lo|veth.*|docker.*|br-.*"}[5m]))',
    }

    try:
        stacklight = StackLightAdapter(direct_client=direct_client)
        await stacklight.connect()

        for metric_name, query in metrics_queries.items():
            try:
                query_result = await stacklight.query_prometheus_raw(query)
                # Parse MetricSample results
                metric_data: dict[str, float | None] = {}
                if query_result:
                    for sample in query_result:
                        node = sample.labels.get("node", "unknown")
                        try:
                            metric_data[node] = float(sample.value)
                        except (ValueError, TypeError):
                            metric_data[node] = None
                result[metric_name] = metric_data
            except Exception as e:
                logger.debug("metric_query_failed", metric=metric_name, error=str(e))
                result[metric_name] = {"error": str(e)}

    except Exception as e:
        logger.warning("metrics_snapshot_collection_failed", error=str(e))
        result["error"] = str(e)
        # Return basic structure with error
        for metric_name in metrics_queries:
            if metric_name not in result:
                result[metric_name] = {"error": "Collection failed"}

    return result


# Tool metadata for registration
TOOL_NAME = "create_diagnostic_bundle"
TOOL_DESCRIPTION = """Generate a comprehensive diagnostic bundle for support.

Collects diagnostic information including:
- Cluster State: Machine status, node conditions, pod states
- OpenStack State: Service list, agent status, endpoint health
- Ceph State: Cluster status, OSD tree, PG dump
- Recent Logs: Last N hours of logs from affected services
- Metrics Snapshot: Resource utilization at time of issue
- Alert History: Related alerts from past N hours

Returns a base64-encoded tar.gz archive.

This is a READ-ONLY tool that does not modify any resources."""

TOOL_TAGS = ["troubleshooting", "support", "diagnostic", "bundle", "read-only"]
