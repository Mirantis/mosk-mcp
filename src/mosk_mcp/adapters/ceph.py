"""Ceph cluster adapter for MOSK MCP Server.

This module provides communication with Ceph storage clusters via:
- Ceph toolbox pod execution
- Ceph MGR Restful API (when available)
- Kubernetes CRDs for Rook-managed clusters

The adapter abstracts the underlying communication method and provides
a consistent interface for Ceph operations.
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import UTC, datetime
from enum import Enum
from typing import TYPE_CHECKING, Any, cast

from pydantic import BaseModel, Field, computed_field

from mosk_mcp.adapters.utils import execute_in_pod
from mosk_mcp.core.exceptions import (
    MoskConnectionError,
    MoskMCPError,
    ResourceNotFoundError,
)
from mosk_mcp.observability.logging import get_logger
from mosk_mcp.tools.common.enums import CephHealthStatus


if TYPE_CHECKING:
    from mosk_mcp.adapters.kubernetes import KubernetesAdapter
    from mosk_mcp.core.config import Settings


logger = get_logger(__name__)


def _validate_gather_result(
    result: Any,
    command_name: str,
    cluster_name: str,
) -> Any:
    """Validate a result from asyncio.gather with return_exceptions=True.

    Args:
        result: The result from gather (could be data or exception).
        command_name: Name of the command for error messages.
        cluster_name: Ceph cluster name for error context.

    Returns:
        The result if valid.

    Raises:
        CephError: If the result is an exception.
    """
    if isinstance(result, BaseException):
        logger.error(
            "ceph_command_failed_in_gather",
            command=command_name,
            cluster=cluster_name,
            error=str(result),
        )
        raise CephError(
            message=f"Ceph command '{command_name}' failed: {result}",
            ceph_command=command_name,
            cluster_name=cluster_name,
            details={"original_error": str(result)},
        )
    return result


class CephError(MoskMCPError):
    """Raised when Ceph operations fail.

    Attributes:
        message: Human-readable error message.
        ceph_command: The Ceph command that failed.
        cluster_name: The Ceph cluster name.
    """

    def __init__(
        self,
        message: str = "Ceph operation failed",
        ceph_command: str | None = None,
        cluster_name: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Initialize Ceph error.

        Args:
            message: Human-readable error message.
            ceph_command: The Ceph command that failed.
            cluster_name: The Ceph cluster name.
            details: Optional additional context.
        """
        details = details or {}
        if ceph_command:
            details["ceph_command"] = ceph_command
        if cluster_name:
            details["cluster_name"] = cluster_name
        super().__init__(message, details, error_code="CEPH_ERROR")
        self.ceph_command = ceph_command
        self.cluster_name = cluster_name


class OSDStatus(str, Enum):
    """OSD operational status."""

    UP = "up"
    DOWN = "down"
    UNKNOWN = "unknown"


class OSDState(str, Enum):
    """OSD in/out state."""

    IN = "in"
    OUT = "out"


class PGState(str, Enum):
    """Common PG states."""

    ACTIVE = "active"
    CLEAN = "clean"
    RECOVERING = "recovering"
    RECOVERY_WAIT = "recovery_wait"
    BACKFILLING = "backfilling"
    BACKFILL_WAIT = "backfill_wait"
    DEGRADED = "degraded"
    INCOMPLETE = "incomplete"
    STALE = "stale"
    PEERING = "peering"
    REMAPPED = "remapped"
    UNDERSIZED = "undersized"


# Capacity thresholds from PROJECT_TRACKER.md
CAPACITY_WARNING_THRESHOLD = 70  # %
CAPACITY_CRITICAL_THRESHOLD = 80  # %
CAPACITY_EMERGENCY_THRESHOLD = 85  # %


class CephClusterStatus(BaseModel):
    """Overall Ceph cluster status.

    Attributes:
        health: Cluster health status.
        health_checks: Active health checks and their severity.
        fsid: Cluster FSID.
        quorum: Monitor quorum status.
        num_osds: Total number of OSDs.
        num_osds_up: Number of OSDs that are up.
        num_osds_in: Number of OSDs that are in.
        num_pgs: Total number of placement groups.
        pg_states: PG state summary.
        total_bytes: Total raw storage in bytes.
        used_bytes: Used raw storage in bytes.
        available_bytes: Available raw storage in bytes.
        capacity_percent: Percentage of capacity used.
        timestamp: When status was retrieved.
    """

    health: CephHealthStatus
    health_checks: dict[str, dict[str, Any]] = Field(default_factory=dict)
    fsid: str = ""
    quorum: list[str] = Field(default_factory=list)
    num_osds: int = 0
    num_osds_up: int = 0
    num_osds_in: int = 0
    num_pgs: int = 0
    pg_states: dict[str, int] = Field(default_factory=dict)
    total_bytes: int = 0
    used_bytes: int = 0
    available_bytes: int = 0
    capacity_percent: float = 0.0
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @computed_field
    @property
    def is_healthy(self) -> bool:
        """Check if cluster is healthy."""
        return self.health == CephHealthStatus.HEALTH_OK

    @computed_field
    @property
    def all_osds_up(self) -> bool:
        """Check if all OSDs are up."""
        return self.num_osds == self.num_osds_up

    @computed_field
    @property
    def capacity_status(self) -> str:
        """Get capacity status based on thresholds."""
        if self.capacity_percent >= CAPACITY_EMERGENCY_THRESHOLD:
            return "emergency"
        if self.capacity_percent >= CAPACITY_CRITICAL_THRESHOLD:
            return "critical"
        if self.capacity_percent >= CAPACITY_WARNING_THRESHOLD:
            return "warning"
        return "normal"


class OSDInfo(BaseModel):
    """Information about a single OSD.

    Attributes:
        osd_id: OSD identifier.
        uuid: OSD UUID.
        status: Up/down status.
        state: In/out state.
        host: Host running this OSD.
        device_class: Device class (hdd, ssd, nvme).
        crush_weight: CRUSH weight.
        reweight: OSD reweight value.
        total_bytes: Total capacity in bytes.
        used_bytes: Used capacity in bytes.
        available_bytes: Available capacity in bytes.
        utilization_percent: Utilization percentage.
        pgs: Number of PGs on this OSD.
        commit_latency_ms: Average commit latency.
        apply_latency_ms: Average apply latency.
        state_since: When current state started.
    """

    osd_id: int
    uuid: str = ""
    status: OSDStatus = OSDStatus.UNKNOWN
    state: OSDState = OSDState.IN
    host: str = ""
    device_class: str = ""
    crush_weight: float = 0.0
    reweight: float = 1.0
    total_bytes: int = 0
    used_bytes: int = 0
    available_bytes: int = 0
    utilization_percent: float = 0.0
    pgs: int = 0
    commit_latency_ms: float = 0.0
    apply_latency_ms: float = 0.0
    state_since: datetime | None = None

    @computed_field
    @property
    def is_up(self) -> bool:
        """Check if OSD is up."""
        return self.status == OSDStatus.UP

    @computed_field
    @property
    def is_in(self) -> bool:
        """Check if OSD is in."""
        return self.state == OSDState.IN

    @computed_field
    @property
    def is_healthy(self) -> bool:
        """Check if OSD is healthy (up and in)."""
        return self.is_up and self.is_in


class PoolInfo(BaseModel):
    """Information about a Ceph pool.

    Attributes:
        pool_id: Pool identifier.
        pool_name: Pool name.
        pg_num: Number of placement groups.
        pgp_num: Number of placement group placement.
        size: Replication size.
        min_size: Minimum replication size.
        crush_rule: CRUSH rule name.
        application: Pool application (rbd, rgw, cephfs).
        total_bytes: Total capacity in bytes.
        used_bytes: Used capacity in bytes.
        percent_used: Utilization percentage.
        max_avail_bytes: Maximum available bytes.
        objects: Number of objects.
    """

    pool_id: int
    pool_name: str
    pg_num: int = 0
    pgp_num: int = 0
    size: int = 3
    min_size: int = 2
    crush_rule: int | str = ""
    application: str = ""
    total_bytes: int = 0
    used_bytes: int = 0
    percent_used: float = 0.0
    max_avail_bytes: int = 0
    objects: int = 0


class PGSummary(BaseModel):
    """Placement group summary.

    Attributes:
        total_pgs: Total number of PGs.
        active_clean: Number of active+clean PGs.
        states: PG state counts.
        stuck_pgs: PGs stuck in non-optimal states.
        misplaced_ratio: Ratio of misplaced objects.
        degraded_ratio: Ratio of degraded objects.
        recovering: Whether recovery is in progress.
        recovery_rate_bytes: Recovery rate in bytes/sec.
    """

    total_pgs: int = 0
    active_clean: int = 0
    states: dict[str, int] = Field(default_factory=dict)
    stuck_pgs: dict[str, int] = Field(default_factory=dict)
    misplaced_ratio: float = 0.0
    degraded_ratio: float = 0.0
    recovering: bool = False
    recovery_rate_bytes: int = 0

    @computed_field
    @property
    def is_healthy(self) -> bool:
        """Check if all PGs are active+clean."""
        return self.active_clean == self.total_pgs

    @computed_field
    @property
    def has_stuck_pgs(self) -> bool:
        """Check if there are stuck PGs."""
        return bool(self.stuck_pgs)


class RecoveryStatus(BaseModel):
    """Ceph recovery/rebalancing status.

    Attributes:
        is_recovering: Whether recovery is in progress.
        is_backfilling: Whether backfill is in progress.
        recovering_objects: Number of objects being recovered.
        recovering_bytes: Bytes being recovered.
        recovery_rate_objects: Recovery rate (objects/sec).
        recovery_rate_bytes: Recovery rate (bytes/sec).
        misplaced_objects: Number of misplaced objects.
        misplaced_total: Total objects that could be misplaced.
        misplaced_ratio: Ratio of misplaced objects.
        degraded_objects: Number of degraded objects.
        degraded_total: Total objects.
        degraded_ratio: Ratio of degraded objects.
        estimated_time_remaining_seconds: ETA in seconds.
    """

    is_recovering: bool = False
    is_backfilling: bool = False
    recovering_objects: int = 0
    recovering_bytes: int = 0
    recovery_rate_objects: int = 0
    recovery_rate_bytes: int = 0
    misplaced_objects: int = 0
    misplaced_total: int = 0
    misplaced_ratio: float = 0.0
    degraded_objects: int = 0
    degraded_total: int = 0
    degraded_ratio: float = 0.0
    estimated_time_remaining_seconds: int | None = None

    @computed_field
    @property
    def is_in_progress(self) -> bool:
        """Check if any recovery is in progress."""
        return self.is_recovering or self.is_backfilling


class CephAdapter:
    """Adapter for Ceph cluster communication.

    This adapter provides methods for querying and managing Ceph clusters
    in MOSK environments. It supports communication via:
    1. Ceph toolbox pod (kubectl exec)
    2. Rook-Ceph CRDs (for Rook-managed clusters)

    Attributes:
        _k8s: Kubernetes adapter for executing commands.
        _toolbox_namespace: Namespace where Ceph toolbox runs.
        _toolbox_pod_label: Label selector for toolbox pod.
        _cluster_name: Ceph cluster name.
        _connected: Whether adapter is connected.

    Example:
        async with CephAdapter(k8s_adapter) as ceph:
            status = await ceph.get_cluster_status()
            print(f"Cluster health: {status.health}")
    """

    DEFAULT_TOOLBOX_NAMESPACE = "rook-ceph"
    DEFAULT_TOOLBOX_LABEL = "app=rook-ceph-tools"
    DEFAULT_CLUSTER_NAME = "ceph"

    def __init__(
        self,
        kubernetes_adapter: KubernetesAdapter,
        toolbox_namespace: str | None = None,
        toolbox_pod_label: str | None = None,
        cluster_name: str | None = None,
    ) -> None:
        """Initialize the Ceph adapter.

        Args:
            kubernetes_adapter: Kubernetes adapter for command execution.
            toolbox_namespace: Namespace for Ceph toolbox pod.
            toolbox_pod_label: Label selector for toolbox pod.
            cluster_name: Ceph cluster name.
        """
        self._k8s = kubernetes_adapter
        self._toolbox_namespace = toolbox_namespace or self.DEFAULT_TOOLBOX_NAMESPACE
        self._toolbox_pod_label = toolbox_pod_label or self.DEFAULT_TOOLBOX_LABEL
        self._cluster_name = cluster_name or self.DEFAULT_CLUSTER_NAME
        self._toolbox_pod_name: str | None = None
        self._connected = False

    @classmethod
    def from_settings(
        cls,
        kubernetes_adapter: KubernetesAdapter,
        _settings: Settings,
    ) -> CephAdapter:
        """Create adapter from settings.

        Args:
            kubernetes_adapter: Kubernetes adapter.
            _settings: Application settings (reserved for future use).

        Returns:
            Configured CephAdapter instance.
        """
        # In future, could pull Ceph-specific settings from config
        return cls(kubernetes_adapter)

    async def connect(self) -> None:
        """Establish connection and find toolbox pod.

        Raises:
            MoskConnectionError: If toolbox pod cannot be found.
        """
        if self._connected:
            return

        logger.debug(
            "connecting_to_ceph",
            namespace=self._toolbox_namespace,
            label=self._toolbox_pod_label,
        )

        try:
            # Find toolbox pod
            pods = await self._k8s.list(
                kind="Pod",
                namespace=self._toolbox_namespace,
                label_selector=self._toolbox_pod_label,
            )

            running_pods = [p for p in pods if p.get("status", {}).get("phase") == "Running"]

            if not running_pods:
                raise MoskConnectionError(
                    f"No running Ceph toolbox pod found with label '{self._toolbox_pod_label}' "
                    f"in namespace '{self._toolbox_namespace}'",
                    service="ceph",
                )

            self._toolbox_pod_name = running_pods[0]["metadata"]["name"]
            self._connected = True

            logger.info(
                "ceph_connected",
                toolbox_pod=self._toolbox_pod_name,
                namespace=self._toolbox_namespace,
            )

        except MoskConnectionError:
            raise
        except Exception as e:
            raise MoskConnectionError(
                f"Failed to connect to Ceph cluster: {e}",
                service="ceph",
            ) from e

    async def disconnect(self) -> None:
        """Disconnect from Ceph cluster."""
        self._toolbox_pod_name = None
        self._connected = False
        logger.debug("ceph_disconnected")

    async def __aenter__(self) -> CephAdapter:
        """Async context manager entry."""
        await self.connect()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        """Async context manager exit."""
        await self.disconnect()

    def _ensure_connected(self) -> None:
        """Ensure adapter is connected.

        Raises:
            MoskConnectionError: If not connected.
        """
        if not self._connected:
            raise MoskConnectionError(
                "Ceph adapter not connected. Call connect() first.",
                service="ceph",
            )

    async def _execute_ceph_command(
        self,
        command: list[str],
        timeout: int = 30,
        json_output: bool = True,
    ) -> dict[str, Any] | str:
        """Execute a Ceph command via toolbox pod.

        Args:
            command: Ceph command arguments (without 'ceph' prefix).
            timeout: Command timeout in seconds.
            json_output: Whether to parse output as JSON.

        Returns:
            Command output as dict (if json_output) or string.

        Raises:
            CephError: If command fails.
        """
        self._ensure_connected()

        # Build full command
        ceph_cmd = ["ceph", *command]
        if json_output:
            ceph_cmd.extend(["-f", "json"])

        logger.debug(
            "executing_ceph_command",
            command=ceph_cmd,
            pod=self._toolbox_pod_name,
        )

        try:
            # Execute via kubectl exec using shared utility
            exec_result = await execute_in_pod(
                kubernetes_adapter=self._k8s,
                pod_name=self._toolbox_pod_name or "",
                namespace=self._toolbox_namespace,
                command=ceph_cmd,
                timeout=timeout,
                raise_on_error=False,  # Handle errors ourselves for CephError
                service_name="ceph_exec",
            )

            # Check for errors and convert to CephError
            if not exec_result.success:
                raise CephError(
                    f"Ceph command failed: {exec_result.stderr}",
                    ceph_command=" ".join(ceph_cmd),
                    cluster_name=self._cluster_name,
                    details={
                        "returncode": exec_result.return_code,
                        "stderr": exec_result.stderr,
                    },
                )

            result = exec_result.stdout

            if json_output:
                try:
                    return cast("dict[str, Any]", json.loads(result))
                except json.JSONDecodeError as e:
                    logger.warning(
                        "ceph_command_json_parse_failed",
                        command=ceph_cmd,
                        output=result[:200],
                        error=str(e),
                    )
                    raise CephError(
                        f"Failed to parse Ceph output as JSON: {e}",
                        ceph_command=" ".join(ceph_cmd),
                        cluster_name=self._cluster_name,
                    ) from e

            return result

        except CephError:
            raise
        except Exception as e:
            raise CephError(
                f"Ceph command failed: {e}",
                ceph_command=" ".join(ceph_cmd),
                cluster_name=self._cluster_name,
            ) from e

    # =========================================================================
    # Public API Methods
    # =========================================================================

    async def get_cluster_status(self) -> CephClusterStatus:
        """Get overall Ceph cluster status.

        Returns:
            CephClusterStatus with health and capacity info.

        Raises:
            CephError: If status cannot be retrieved.
        """
        self._ensure_connected()

        logger.debug("getting_ceph_status", cluster=self._cluster_name)

        result = await self._execute_ceph_command(["status"])
        if isinstance(result, str):
            result = json.loads(result)

        # Parse health
        health_data = result.get("health", {})
        health_status_str = health_data.get("status", "UNKNOWN")
        try:
            health = CephHealthStatus(health_status_str)
        except ValueError:
            health = CephHealthStatus.UNKNOWN

        health_checks = health_data.get("checks", {})

        # Parse OSD info
        osd_map = result.get("osdmap", {}).get("osdmap", {})
        if not osd_map:
            osd_map = result.get("osdmap", {})

        num_osds = osd_map.get("num_osds", 0)
        num_osds_up = osd_map.get("num_up_osds", 0)
        num_osds_in = osd_map.get("num_in_osds", 0)

        # Parse PG info
        pg_map = result.get("pgmap", {})
        num_pgs = pg_map.get("num_pgs", 0)

        pg_states: dict[str, int] = {}
        for state_info in pg_map.get("pgs_by_state", []):
            state_name = state_info.get("state_name", "unknown")
            count = state_info.get("count", 0)
            pg_states[state_name] = count

        # Parse capacity
        total_bytes = pg_map.get("bytes_total", 0)
        used_bytes = pg_map.get("bytes_used", 0)
        available_bytes = pg_map.get("bytes_avail", 0)

        capacity_percent = 0.0
        if total_bytes > 0:
            capacity_percent = (used_bytes / total_bytes) * 100

        # Parse quorum
        quorum = result.get("quorum_names", [])

        status = CephClusterStatus(
            health=health,
            health_checks=health_checks,
            fsid=result.get("fsid", ""),
            quorum=quorum,
            num_osds=num_osds,
            num_osds_up=num_osds_up,
            num_osds_in=num_osds_in,
            num_pgs=num_pgs,
            pg_states=pg_states,
            total_bytes=total_bytes,
            used_bytes=used_bytes,
            available_bytes=available_bytes,
            capacity_percent=capacity_percent,
        )

        logger.info(
            "ceph_status_retrieved",
            health=health.value,
            osds=f"{num_osds_up}/{num_osds}",
            capacity_percent=f"{capacity_percent:.1f}%",
        )

        return status

    async def list_osds(self) -> list[OSDInfo]:
        """List all OSDs with status and capacity.

        Returns:
            List of OSDInfo objects.

        Raises:
            CephError: If OSD list cannot be retrieved.
        """
        self._ensure_connected()

        logger.debug("listing_osds", cluster=self._cluster_name)

        # Execute all Ceph commands in parallel for better performance
        # Each command is independent and can run concurrently
        tree_task = self._execute_ceph_command(["osd", "tree"])
        df_task = self._execute_ceph_command(["osd", "df"])
        dump_task = self._execute_ceph_command(["osd", "dump"])
        perf_task = self._execute_ceph_command(["osd", "perf"])

        results = await asyncio.gather(
            tree_task, df_task, dump_task, perf_task, return_exceptions=True
        )

        # Validate results - raises CephError if any command failed
        tree_result = _validate_gather_result(results[0], "osd tree", self._cluster_name)
        df_result = _validate_gather_result(results[1], "osd df", self._cluster_name)
        dump_result = _validate_gather_result(results[2], "osd dump", self._cluster_name)
        perf_result = _validate_gather_result(results[3], "osd perf", self._cluster_name)

        # Parse JSON results (handle both str and dict returns)
        if isinstance(tree_result, str):
            tree_result = json.loads(tree_result)
        if isinstance(df_result, str):
            df_result = json.loads(df_result)
        if isinstance(dump_result, str):
            dump_result = json.loads(dump_result)
        if isinstance(perf_result, str):
            perf_result = json.loads(perf_result)

        # Build host mapping from tree
        host_map: dict[int, str] = {}
        for node in tree_result.get("nodes", []):
            if node.get("type") == "host":
                host_name = node.get("name", "")
                for child_id in node.get("children", []):
                    host_map[child_id] = host_name

        # Build UUID mapping from dump
        uuid_map: dict[int, str] = {}
        state_map: dict[int, tuple[bool, bool]] = {}  # (up, in)
        for osd in dump_result.get("osds", []):
            osd_id = osd.get("osd", 0)
            uuid_map[osd_id] = osd.get("uuid", "")
            state_map[osd_id] = (
                osd.get("up", 0) == 1,
                osd.get("in", 0) == 1,
            )

        # Build perf mapping
        perf_map: dict[int, tuple[float, float]] = {}
        for perf in perf_result.get("osd_perf_infos", []):
            osd_id = perf.get("id", 0)
            stats = perf.get("perf_stats", {})
            perf_map[osd_id] = (
                stats.get("commit_latency_ms", 0),
                stats.get("apply_latency_ms", 0),
            )

        # Build OSD list from df output
        osds: list[OSDInfo] = []
        for node in df_result.get("nodes", []):
            osd_id = node.get("id", 0)
            if osd_id < 0:
                continue  # Skip non-OSD nodes

            # Get status from tree nodes
            status = OSDStatus.UNKNOWN
            device_class = ""
            crush_weight = 0.0
            reweight = 1.0

            for tree_node in tree_result.get("nodes", []):
                if tree_node.get("id") == osd_id and tree_node.get("type") == "osd":
                    status_str = tree_node.get("status", "unknown")
                    status = OSDStatus.UP if status_str == "up" else OSDStatus.DOWN
                    device_class = tree_node.get("device_class", "")
                    crush_weight = tree_node.get("crush_weight", 0.0)
                    reweight = tree_node.get("reweight", 1.0)
                    break

            # Get in/out state
            _up, in_state = state_map.get(osd_id, (False, True))
            state = OSDState.IN if in_state else OSDState.OUT

            # Get latencies
            commit_lat, apply_lat = perf_map.get(osd_id, (0.0, 0.0))

            osd_info = OSDInfo(
                osd_id=osd_id,
                uuid=uuid_map.get(osd_id, ""),
                status=status,
                state=state,
                host=host_map.get(osd_id, ""),
                device_class=device_class,
                crush_weight=crush_weight,
                reweight=reweight,
                total_bytes=node.get("kb", 0) * 1024,
                used_bytes=node.get("kb_used", 0) * 1024,
                available_bytes=node.get("kb_avail", 0) * 1024,
                utilization_percent=node.get("utilization", 0.0),
                pgs=node.get("pgs", 0),
                commit_latency_ms=commit_lat,
                apply_latency_ms=apply_lat,
            )

            osds.append(osd_info)

        logger.info("osds_listed", count=len(osds))
        return osds

    async def get_osd_details(self, osd_id: int) -> OSDInfo:
        """Get detailed information about a specific OSD.

        Args:
            osd_id: OSD identifier.

        Returns:
            OSDInfo with detailed information.

        Raises:
            ResourceNotFoundError: If OSD doesn't exist.
            CephError: If details cannot be retrieved.
        """
        self._ensure_connected()

        logger.debug("getting_osd_details", osd_id=osd_id)

        osds = await self.list_osds()

        for osd in osds:
            if osd.osd_id == osd_id:
                logger.info("osd_details_retrieved", osd_id=osd_id)
                return osd

        raise ResourceNotFoundError(
            f"OSD {osd_id} not found",
            resource_type="OSD",
            resource_id=str(osd_id),
        )

    async def get_capacity(self) -> dict[str, Any]:
        """Get storage capacity breakdown by pool.

        Returns:
            Dictionary with capacity information per pool and totals.

        Raises:
            CephError: If capacity cannot be retrieved.
        """
        self._ensure_connected()

        logger.debug("getting_ceph_capacity", cluster=self._cluster_name)

        # Execute both commands in parallel for better performance
        df_task = self._execute_ceph_command(["df", "detail"])
        pool_task = self._execute_ceph_command(["osd", "pool", "ls", "detail"])

        results = await asyncio.gather(df_task, pool_task, return_exceptions=True)

        # Validate results - raises CephError if any command failed
        df_result = _validate_gather_result(results[0], "df detail", self._cluster_name)
        pool_result = _validate_gather_result(results[1], "osd pool ls detail", self._cluster_name)

        # Parse JSON results
        if isinstance(df_result, str):
            df_result = json.loads(df_result)
        if isinstance(pool_result, str):
            pool_result = json.loads(pool_result)

        # Build pool info mapping
        pool_info_map: dict[str, dict[str, Any]] = {}
        for pool in pool_result if isinstance(pool_result, list) else []:
            pool_name = pool.get("pool_name", "")
            pool_info_map[pool_name] = {
                "size": pool.get("size", 3),
                "min_size": pool.get("min_size", 2),
                "pg_num": pool.get("pg_num", 0),
                "crush_rule": pool.get("crush_rule", ""),
                "application": list(pool.get("application_metadata", {}).keys()),
            }

        # Parse global stats
        global_stats = df_result.get("stats", {})
        total_bytes = global_stats.get("total_bytes", 0)
        used_bytes = global_stats.get("total_used_bytes", 0)
        available_bytes = global_stats.get("total_avail_bytes", 0)

        # Calculate capacity percentage and status
        capacity_percent = (used_bytes / total_bytes * 100) if total_bytes > 0 else 0.0

        if capacity_percent >= CAPACITY_EMERGENCY_THRESHOLD:
            capacity_status = "emergency"
        elif capacity_percent >= CAPACITY_CRITICAL_THRESHOLD:
            capacity_status = "critical"
        elif capacity_percent >= CAPACITY_WARNING_THRESHOLD:
            capacity_status = "warning"
        else:
            capacity_status = "normal"

        # Parse pool data
        pools: list[PoolInfo] = []
        for pool_data in df_result.get("pools", []):
            pool_stats = pool_data.get("stats", {})
            pool_name = pool_data.get("name", "")
            pool_extra = pool_info_map.get(pool_name, {})

            pool = PoolInfo(
                pool_id=pool_data.get("id", 0),
                pool_name=pool_name,
                pg_num=pool_extra.get("pg_num", 0),
                size=pool_extra.get("size", 3),
                min_size=pool_extra.get("min_size", 2),
                crush_rule=pool_extra.get("crush_rule", ""),
                application=", ".join(pool_extra.get("application", [])),
                total_bytes=pool_stats.get("stored", 0),
                used_bytes=pool_stats.get("bytes_used", 0),
                percent_used=pool_stats.get("percent_used", 0.0) * 100,
                max_avail_bytes=pool_stats.get("max_avail", 0),
                objects=pool_stats.get("objects", 0),
            )
            pools.append(pool)

        result = {
            "total_bytes": total_bytes,
            "used_bytes": used_bytes,
            "available_bytes": available_bytes,
            "capacity_percent": capacity_percent,
            "capacity_status": capacity_status,
            "thresholds": {
                "warning": CAPACITY_WARNING_THRESHOLD,
                "critical": CAPACITY_CRITICAL_THRESHOLD,
                "emergency": CAPACITY_EMERGENCY_THRESHOLD,
            },
            "pools": [p.model_dump() for p in pools],
            "timestamp": datetime.now(UTC).isoformat(),
        }

        logger.info(
            "ceph_capacity_retrieved",
            capacity_percent=f"{capacity_percent:.1f}%",
            status=capacity_status,
            pool_count=len(pools),
        )

        return result

    async def get_pg_status(self) -> PGSummary:
        """Get placement group status summary.

        Returns:
            PGSummary with PG health information.

        Raises:
            CephError: If PG status cannot be retrieved.
        """
        self._ensure_connected()

        logger.debug("getting_pg_status", cluster=self._cluster_name)

        # Get cluster status for PG summary
        status = await self.get_cluster_status()

        # Calculate active+clean count
        active_clean = status.pg_states.get("active+clean", 0)

        # Determine if recovering
        recovering = any(
            state in status.pg_states
            for state in ["recovering", "recovery_wait", "backfilling", "backfill_wait"]
        )

        # Find stuck PGs (non-optimal states)
        stuck_states = ["stale", "incomplete", "peering", "undersized", "degraded"]
        stuck_pgs: dict[str, int] = {}
        for state, count in status.pg_states.items():
            for stuck in stuck_states:
                if stuck in state:
                    stuck_pgs[state] = count
                    break

        # Get health details for misplaced/degraded ratios
        health_result = await self._execute_ceph_command(["health", "detail"])
        if isinstance(health_result, str):
            health_result = json.loads(health_result)

        # Parse misplaced/degraded from health checks
        misplaced_ratio = 0.0
        degraded_ratio = 0.0

        for check_name, check_data in health_result.get("checks", {}).items():
            summary = check_data.get("summary", {}).get("message", "")
            if "misplaced" in check_name.lower():
                # Try to parse ratio from message
                match = re.search(r"(\d+\.?\d*)%", summary)
                if match:
                    misplaced_ratio = float(match.group(1))
            if "degraded" in check_name.lower():
                match = re.search(r"(\d+\.?\d*)%", summary)
                if match:
                    degraded_ratio = float(match.group(1))

        pg_summary = PGSummary(
            total_pgs=status.num_pgs,
            active_clean=active_clean,
            states=status.pg_states,
            stuck_pgs=stuck_pgs,
            misplaced_ratio=misplaced_ratio,
            degraded_ratio=degraded_ratio,
            recovering=recovering,
            recovery_rate_bytes=0,  # Would need `ceph pg stat` for this
        )

        logger.info(
            "pg_status_retrieved",
            total=pg_summary.total_pgs,
            active_clean=pg_summary.active_clean,
            is_healthy=pg_summary.is_healthy,
        )

        return pg_summary

    async def get_recovery_status(self) -> RecoveryStatus:
        """Get recovery/rebalancing progress.

        Returns:
            RecoveryStatus with recovery progress info.

        Raises:
            CephError: If recovery status cannot be retrieved.
        """
        self._ensure_connected()

        logger.debug("getting_recovery_status", cluster=self._cluster_name)

        # Execute both commands in parallel for better performance
        # Note: 'ceph progress' doesn't support -f json flag, use 'ceph progress json' subcommand
        progress_task = self._execute_ceph_command(["progress", "json"], json_output=False)
        health_task = self._execute_ceph_command(["health", "detail"])

        results = await asyncio.gather(progress_task, health_task, return_exceptions=True)

        # Validate results - raises CephError if any command failed
        progress_result = _validate_gather_result(results[0], "progress json", self._cluster_name)
        health_result = _validate_gather_result(results[1], "health detail", self._cluster_name)

        # Parse JSON results
        if isinstance(progress_result, str):
            progress_result = json.loads(progress_result)
        if isinstance(health_result, str):
            health_result = json.loads(health_result)

        # Parse recovery state from health checks
        is_recovering = False
        is_backfilling = False
        misplaced_objects = 0
        misplaced_total = 0
        degraded_objects = 0
        degraded_total = 0

        for check_name, check_data in health_result.get("checks", {}).items():
            summary = check_data.get("summary", {}).get("message", "")

            if "recovery" in check_name.lower():
                is_recovering = True
            if "backfill" in check_name.lower():
                is_backfilling = True

            # Parse misplaced objects
            if "PG_AVAILABILITY" in check_name or "misplaced" in check_name.lower():
                match = re.search(r"(\d+)/(\d+) objects misplaced", summary)
                if match:
                    misplaced_objects = int(match.group(1))
                    misplaced_total = int(match.group(2))

            # Parse degraded objects
            if "PG_DEGRADED" in check_name or "degraded" in check_name.lower():
                match = re.search(r"(\d+)/(\d+) objects degraded", summary)
                if match:
                    degraded_objects = int(match.group(1))
                    degraded_total = int(match.group(2))

        # Calculate ratios
        misplaced_ratio = 0.0
        if misplaced_total > 0:
            misplaced_ratio = (misplaced_objects / misplaced_total) * 100

        degraded_ratio = 0.0
        if degraded_total > 0:
            degraded_ratio = (degraded_objects / degraded_total) * 100

        # Check progress events for ETA
        eta_seconds: int | None = None
        for event in progress_result.get("events", []):
            if "recovery" in event.get("message", "").lower():
                # Progress is 0-1 range
                progress = event.get("progress", 0)
                if progress > 0 and progress < 1:
                    # Estimate based on elapsed time
                    # This is a rough estimate
                    pass

        recovery_status = RecoveryStatus(
            is_recovering=is_recovering,
            is_backfilling=is_backfilling,
            recovering_objects=0,  # Would need more detailed parsing
            recovering_bytes=0,
            recovery_rate_objects=0,
            recovery_rate_bytes=0,
            misplaced_objects=misplaced_objects,
            misplaced_total=misplaced_total,
            misplaced_ratio=misplaced_ratio,
            degraded_objects=degraded_objects,
            degraded_total=degraded_total,
            degraded_ratio=degraded_ratio,
            estimated_time_remaining_seconds=eta_seconds,
        )

        logger.info(
            "recovery_status_retrieved",
            is_recovering=recovery_status.is_recovering,
            is_backfilling=recovery_status.is_backfilling,
            misplaced_ratio=f"{misplaced_ratio:.2f}%",
        )

        return recovery_status

    async def check_health_for_operation(
        self,
        operation: str,
        strict: bool = True,
    ) -> tuple[bool, list[str]]:
        """Check if cluster health allows an operation.

        Args:
            operation: Operation type (e.g., 'osd-remove', 'osd-add').
            strict: If True, require HEALTH_OK; if False, allow HEALTH_WARN.

        Returns:
            Tuple of (is_safe, list of warnings/blockers).
        """
        self._ensure_connected()

        logger.debug("checking_health_for_operation", operation=operation)

        status = await self.get_cluster_status()
        warnings: list[str] = []

        # Check health status
        if strict and status.health != CephHealthStatus.HEALTH_OK:
            warnings.append(f"Cluster health is {status.health.value}, operation may be risky")
        elif status.health == CephHealthStatus.HEALTH_ERR:
            warnings.append("Cluster health is HEALTH_ERR, operation blocked until resolved")
            return False, warnings

        # Check if all OSDs are up for removal operations
        if "remove" in operation.lower() and not status.all_osds_up:
            warnings.append(
                f"Not all OSDs are up ({status.num_osds_up}/{status.num_osds}), "
                "removal may cause data loss"
            )

        # Check capacity for add/remove operations
        if "remove" in operation.lower():
            # Calculate post-removal capacity
            # Rough estimate: capacity will increase proportionally
            post_removal_percent = status.capacity_percent * (
                status.num_osds / max(status.num_osds - 1, 1)
            )
            if post_removal_percent >= CAPACITY_CRITICAL_THRESHOLD:
                warnings.append(
                    f"Post-removal capacity would be ~{post_removal_percent:.1f}%, "
                    "exceeding critical threshold"
                )

        # Check for ongoing recovery
        recovery = await self.get_recovery_status()
        if recovery.is_in_progress:
            warnings.append("Recovery/rebalancing is in progress, operation may slow recovery")

        is_safe = len([w for w in warnings if "blocked" in w.lower()]) == 0

        logger.info(
            "health_check_for_operation",
            operation=operation,
            is_safe=is_safe,
            warning_count=len(warnings),
        )

        return is_safe, warnings


# Singleton instance with thread-safe lock
_ceph_adapter: CephAdapter | None = None
_ceph_adapter_lock: asyncio.Lock | None = None
_ceph_adapter_loop_id: int | None = None  # Track which event loop owns the lock


def _get_ceph_adapter_lock() -> asyncio.Lock:
    """Get or create the singleton lock safely.

    Creates the lock lazily when first needed. The lock is created inside
    the running event loop context to avoid RuntimeError.

    Handles event loop changes (e.g., in testing frameworks) by detecting
    when the event loop has changed and recreating the lock. This prevents
    "Event loop is closed" errors when the event loop is replaced.

    Note: This function should only be called from within an async context
    (i.e., when an event loop is running).

    Returns:
        asyncio.Lock instance for synchronizing adapter creation.

    Raises:
        RuntimeError: If called outside of an async context (no running event loop).
    """
    global _ceph_adapter_lock, _ceph_adapter_loop_id

    # Verify we're in an async context and get current loop
    try:
        current_loop = asyncio.get_running_loop()
    except RuntimeError as e:
        raise RuntimeError(
            "Cannot create Ceph adapter lock outside of async context. "
            "Ensure get_ceph_adapter() is called from within an async function."
        ) from e

    current_loop_id = id(current_loop)

    # If event loop has changed, reset the lock (and adapter)
    # This handles test scenarios where event loops are recreated
    if _ceph_adapter_loop_id is not None and _ceph_adapter_loop_id != current_loop_id:
        # Event loop changed - reset everything
        reset_ceph_adapter()

    # Create lock if needed
    if _ceph_adapter_lock is None:
        _ceph_adapter_lock = asyncio.Lock()
        _ceph_adapter_loop_id = current_loop_id

    return _ceph_adapter_lock


async def get_ceph_adapter(
    kubernetes_adapter: KubernetesAdapter,
) -> CephAdapter:
    """Get or create the Ceph adapter singleton.

    Thread-safe: Uses asyncio.Lock to prevent race conditions
    when multiple coroutines try to create the adapter simultaneously.

    Args:
        kubernetes_adapter: Kubernetes adapter for communication.

    Returns:
        Connected CephAdapter instance.

    Raises:
        RuntimeError: If called outside of an async context.
    """
    global _ceph_adapter

    # Fast path - already initialized
    if _ceph_adapter is not None:
        return _ceph_adapter

    # Slow path - need to initialize with lock
    # Lock creation is safe here because we're in an async function
    async with _get_ceph_adapter_lock():
        # Double-check after acquiring lock
        if _ceph_adapter is None:
            _ceph_adapter = CephAdapter(kubernetes_adapter)
            await _ceph_adapter.connect()

    return _ceph_adapter


def reset_ceph_adapter() -> None:
    """Reset the Ceph adapter singleton (for testing).

    This clears the adapter instance, lock, and loop ID, allowing
    a fresh singleton to be created on next access.
    """
    global _ceph_adapter, _ceph_adapter_lock, _ceph_adapter_loop_id
    _ceph_adapter = None
    _ceph_adapter_lock = None
    _ceph_adapter_loop_id = None
