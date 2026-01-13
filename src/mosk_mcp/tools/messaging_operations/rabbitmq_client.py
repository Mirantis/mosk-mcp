"""RabbitMQ client for executing rabbitmqctl commands.

This module provides a client for executing read-only rabbitmqctl commands
in RabbitMQ pods. All operations are read-only and do not modify state.

Example:
    async with RabbitMQClient(k8s_adapter, instance="main") as client:
        status = await client.get_cluster_status()
        queues = await client.list_queues(vhost="nova")
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from mosk_mcp.adapters.utils.pod_exec import PodExecResult, execute_in_pod
from mosk_mcp.observability.logging import get_logger


if TYPE_CHECKING:
    from mosk_mcp.adapters.kubernetes import KubernetesAdapter

logger = get_logger(__name__)


# RabbitMQ pod names by instance
RABBITMQ_PODS = {
    "main": "openstack-rabbitmq-rabbitmq-0",
    "neutron": "openstack-neutron-rabbitmq-rabbitmq-0",
}

# Default namespace for RabbitMQ pods
RABBITMQ_NAMESPACE = "openstack"

# Command timeout in seconds
RABBITMQ_COMMAND_TIMEOUT = 60


@dataclass
class ClusterStatusResult:
    """Parsed result from rabbitmqctl cluster_status."""

    cluster_name: str = ""
    running_nodes: list[str] = field(default_factory=list)
    disk_nodes: list[str] = field(default_factory=list)
    alarms: list[str] = field(default_factory=list)
    partitions: list[str] = field(default_factory=list)
    maintenance_status: str = "not under maintenance"
    listeners: list[str] = field(default_factory=list)
    feature_flags: dict[str, bool] = field(default_factory=dict)
    rabbitmq_version: str = ""
    erlang_version: str = ""
    cpu_cores: int = 0


@dataclass
class NodeStatusResult:
    """Parsed result from rabbitmqctl status."""

    memory_used_bytes: int = 0
    memory_limit_bytes: int = 0
    memory_percent: float = 0.0
    disk_free_bytes: int = 0
    has_memory_alarm: bool = False
    has_disk_alarm: bool = False


@dataclass
class QueueInfo:
    """Information about a single queue."""

    name: str
    vhost: str
    messages: int = 0
    messages_ready: int = 0
    messages_unacked: int = 0
    consumers: int = 0
    memory_bytes: int = 0
    state: str = "running"


@dataclass
class ConnectionInfo:
    """Information about a single connection."""

    name: str
    user: str
    state: str = "running"
    channels: int = 0
    client_host: str = ""
    ssl: bool = False
    protocol: str = "AMQP 0-9-1"


class RabbitMQClient:
    """Client for executing rabbitmqctl commands in RabbitMQ pods.

    This client provides a high-level interface for RabbitMQ operations.
    All operations are read-only.

    Usage:
        async with RabbitMQClient(k8s_adapter, instance="main") as client:
            status = await client.get_cluster_status()
    """

    def __init__(
        self,
        kubernetes_adapter: KubernetesAdapter,
        instance: Literal["main", "neutron"] = "main",
        namespace: str = RABBITMQ_NAMESPACE,
    ) -> None:
        """Initialize the RabbitMQ client.

        Args:
            kubernetes_adapter: Kubernetes adapter for pod execution.
            instance: RabbitMQ instance ('main' or 'neutron').
            namespace: Kubernetes namespace.
        """
        self._k8s = kubernetes_adapter
        self._instance = instance
        self._namespace = namespace
        self._pod_name = RABBITMQ_PODS.get(instance, RABBITMQ_PODS["main"])

    async def __aenter__(self) -> RabbitMQClient:
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Async context manager exit."""
        pass

    @property
    def instance(self) -> str:
        """Get the RabbitMQ instance name."""
        return self._instance

    @property
    def pod_name(self) -> str:
        """Get the RabbitMQ pod name."""
        return self._pod_name

    async def _exec_rabbitmqctl(
        self,
        subcommand: list[str],
        timeout: int = RABBITMQ_COMMAND_TIMEOUT,
        raise_on_error: bool = True,
    ) -> PodExecResult:
        """Execute a rabbitmqctl command in the RabbitMQ pod.

        Args:
            subcommand: rabbitmqctl subcommand and arguments.
            timeout: Command timeout in seconds.
            raise_on_error: Whether to raise on non-zero exit code.

        Returns:
            PodExecResult with command output.

        Raises:
            ToolExecutionError: If command execution fails.
        """
        command = ["rabbitmqctl", *subcommand]

        logger.debug(
            "executing_rabbitmqctl",
            pod=self._pod_name,
            subcommand=subcommand,
            instance=self._instance,
        )

        return await execute_in_pod(
            kubernetes_adapter=self._k8s,
            pod_name=self._pod_name,
            namespace=self._namespace,
            command=command,
            timeout=timeout,
            raise_on_error=raise_on_error,
            service_name="rabbitmq_client",
        )

    async def get_cluster_status(self) -> ClusterStatusResult:
        """Get RabbitMQ cluster status.

        Returns:
            ClusterStatusResult with parsed cluster information.
        """
        result = await self._exec_rabbitmqctl(["cluster_status"])
        return self._parse_cluster_status(result.stdout)

    async def get_node_status(self) -> NodeStatusResult:
        """Get RabbitMQ node status including memory and disk.

        Returns:
            NodeStatusResult with memory and disk information.
        """
        result = await self._exec_rabbitmqctl(["status"])
        return self._parse_node_status(result.stdout)

    async def list_vhosts(self) -> list[str]:
        """List all virtual hosts.

        Returns:
            List of vhost names.
        """
        result = await self._exec_rabbitmqctl(["list_vhosts", "name", "--no-table-headers"])
        return [line.strip() for line in result.stdout.split("\n") if line.strip()]

    async def list_queues(
        self,
        vhost: str | None = None,
        columns: list[str] | None = None,
    ) -> list[QueueInfo]:
        """List queues with optional vhost filter.

        Args:
            vhost: Virtual host to query (all vhosts if None).
            columns: Columns to retrieve.

        Returns:
            List of QueueInfo objects.
        """
        if columns is None:
            columns = [
                "name",
                "messages",
                "messages_ready",
                "messages_unacknowledged",
                "consumers",
                "memory",
                "state",
            ]

        cmd = ["list_queues"]
        if vhost:
            cmd.extend(["-p", vhost])
        cmd.extend(columns)
        cmd.append("--no-table-headers")

        result = await self._exec_rabbitmqctl(cmd)
        return self._parse_queues(result.stdout, vhost or "/", columns)

    async def list_connections(
        self,
        columns: list[str] | None = None,
    ) -> list[ConnectionInfo]:
        """List all connections.

        Args:
            columns: Columns to retrieve.

        Returns:
            List of ConnectionInfo objects.
        """
        if columns is None:
            columns = ["name", "user", "state", "channels", "peer_host", "ssl", "protocol"]

        cmd = ["list_connections", *columns, "--no-table-headers"]

        result = await self._exec_rabbitmqctl(cmd)
        return self._parse_connections(result.stdout, columns)

    async def list_channels(self) -> list[dict[str, Any]]:
        """List all channels.

        Returns:
            List of channel information dictionaries.
        """
        columns = ["connection", "name", "consumer_count", "messages_unacknowledged"]
        cmd = ["list_channels", *columns, "--no-table-headers"]

        result = await self._exec_rabbitmqctl(cmd)
        channels = []
        for line in result.stdout.split("\n"):
            if not line.strip():
                continue
            parts = line.split("\t")
            if len(parts) >= 4:
                channels.append(
                    {
                        "connection": parts[0],
                        "name": parts[1],
                        "consumer_count": int(parts[2]) if parts[2].isdigit() else 0,
                        "messages_unacked": int(parts[3]) if parts[3].isdigit() else 0,
                    }
                )
        return channels

    def _parse_cluster_status(self, output: str) -> ClusterStatusResult:
        """Parse rabbitmqctl cluster_status output.

        Args:
            output: Raw command output.

        Returns:
            ClusterStatusResult with parsed data.
        """
        result = ClusterStatusResult()

        # Parse cluster name
        cluster_match = re.search(r"Cluster name:\s*(.+)", output)
        if cluster_match:
            result.cluster_name = cluster_match.group(1).strip()

        # Parse running nodes
        running_match = re.search(r"Running Nodes\s*\n\s*(.+?)(?:\n\n|\Z)", output, re.DOTALL)
        if running_match:
            nodes_text = running_match.group(1)
            result.running_nodes = [
                n.strip()
                for n in nodes_text.split("\n")
                if n.strip() and n.strip().startswith("rabbit@")
            ]

        # Parse disk nodes
        disk_match = re.search(r"Disk Nodes\s*\n\s*(.+?)(?:\n\n|\Z)", output, re.DOTALL)
        if disk_match:
            nodes_text = disk_match.group(1)
            result.disk_nodes = [
                n.strip()
                for n in nodes_text.split("\n")
                if n.strip() and n.strip().startswith("rabbit@")
            ]

        # Parse alarms
        alarms_match = re.search(r"Alarms\s*\n\s*(.+?)(?:\n\n|\Z)", output, re.DOTALL)
        if alarms_match:
            alarms_text = alarms_match.group(1).strip()
            if alarms_text and alarms_text != "(none)":
                result.alarms = [a.strip() for a in alarms_text.split("\n") if a.strip()]

        # Parse network partitions
        partitions_match = re.search(
            r"Network Partitions\s*\n\s*(.+?)(?:\n\n|\Z)", output, re.DOTALL
        )
        if partitions_match:
            partitions_text = partitions_match.group(1).strip()
            if partitions_text and partitions_text != "(none)":
                result.partitions = [p.strip() for p in partitions_text.split("\n") if p.strip()]

        # Parse maintenance status
        maintenance_match = re.search(
            r"status:\s*(not under maintenance|under maintenance)", output
        )
        if maintenance_match:
            result.maintenance_status = maintenance_match.group(1)

        # Parse listeners
        listeners_section = re.search(r"Listeners\s*\n(.+?)(?:\n\n|\Z)", output, re.DOTALL)
        if listeners_section:
            for line in listeners_section.group(1).split("\n"):
                port_match = re.search(r"port:\s*(\d+),\s*protocol:\s*(\w+)", line)
                if port_match:
                    result.listeners.append(f"{port_match.group(2)}:{port_match.group(1)}")

        # Parse versions
        version_match = re.search(
            r"RabbitMQ\s+(\d+\.\d+\.\d+)\s+on\s+Erlang\s+(\d+\.\d+(?:\.\d+)?)", output
        )
        if version_match:
            result.rabbitmq_version = version_match.group(1)
            result.erlang_version = version_match.group(2)

        # Parse CPU cores
        cores_match = re.search(r"available CPU cores:\s*(\d+)", output)
        if cores_match:
            result.cpu_cores = int(cores_match.group(1))

        # Parse feature flags
        flags_section = re.search(r"Feature flags\s*\n(.+?)(?:\n\n|\Z)", output, re.DOTALL)
        if flags_section:
            for line in flags_section.group(1).split("\n"):
                flag_match = re.search(r"Flag:\s*(\w+),\s*state:\s*(enabled|disabled)", line)
                if flag_match:
                    result.feature_flags[flag_match.group(1)] = flag_match.group(2) == "enabled"

        return result

    def _parse_node_status(self, output: str) -> NodeStatusResult:
        """Parse rabbitmqctl status output.

        Args:
            output: Raw command output.

        Returns:
            NodeStatusResult with parsed data.
        """
        result = NodeStatusResult()

        # Parse memory used
        mem_used_match = re.search(r"Total memory used:\s*([\d.]+)\s*(\w+)", output)
        if mem_used_match:
            value = float(mem_used_match.group(1))
            unit = mem_used_match.group(2).lower()
            result.memory_used_bytes = self._convert_to_bytes(value, unit)

        # Parse memory limit
        mem_limit_match = re.search(r"computed to:\s*([\d.]+)\s*(\w+)", output)
        if mem_limit_match:
            value = float(mem_limit_match.group(1))
            unit = mem_limit_match.group(2).lower()
            result.memory_limit_bytes = self._convert_to_bytes(value, unit)

        # Calculate memory percentage
        if result.memory_limit_bytes > 0:
            result.memory_percent = (result.memory_used_bytes / result.memory_limit_bytes) * 100

        # Check for memory alarm
        result.has_memory_alarm = "memory" in output.lower() and "alarm" in output.lower()

        # Check for disk alarm
        result.has_disk_alarm = "disk" in output.lower() and "alarm" in output.lower()

        return result

    def _parse_queues(
        self,
        output: str,
        vhost: str,
        columns: list[str],
    ) -> list[QueueInfo]:
        """Parse rabbitmqctl list_queues output.

        Args:
            output: Raw command output.
            vhost: Virtual host for the queues.
            columns: Column order in output.

        Returns:
            List of QueueInfo objects.
        """
        queues = []
        col_idx = {col: i for i, col in enumerate(columns)}

        for line in output.split("\n"):
            stripped_line = line.strip()
            if (
                not stripped_line
                or stripped_line.startswith("Timeout")
                or stripped_line.startswith("Listing")
            ):
                continue

            parts = stripped_line.split("\t")
            if len(parts) < len(columns):
                continue

            try:
                queue = QueueInfo(
                    name=parts[col_idx.get("name", 0)] if "name" in col_idx else "",
                    vhost=vhost,
                    messages=int(parts[col_idx["messages"]]) if "messages" in col_idx else 0,
                    messages_ready=int(parts[col_idx["messages_ready"]])
                    if "messages_ready" in col_idx
                    else 0,
                    messages_unacked=int(parts[col_idx["messages_unacknowledged"]])
                    if "messages_unacknowledged" in col_idx
                    else 0,
                    consumers=int(parts[col_idx["consumers"]]) if "consumers" in col_idx else 0,
                    memory_bytes=int(parts[col_idx["memory"]]) if "memory" in col_idx else 0,
                    state=parts[col_idx["state"]] if "state" in col_idx else "running",
                )
                queues.append(queue)
            except (ValueError, IndexError) as e:
                logger.debug("failed_to_parse_queue", line=stripped_line, error=str(e))
                continue

        return queues

    def _parse_connections(
        self,
        output: str,
        columns: list[str],
    ) -> list[ConnectionInfo]:
        """Parse rabbitmqctl list_connections output.

        Args:
            output: Raw command output.
            columns: Column order in output.

        Returns:
            List of ConnectionInfo objects.
        """
        connections = []
        col_idx = {col: i for i, col in enumerate(columns)}

        for line in output.split("\n"):
            stripped_line = line.strip()
            if not stripped_line or stripped_line.startswith("Listing"):
                continue

            parts = stripped_line.split("\t")
            if len(parts) < 2:  # Minimum: name and user
                continue

            try:
                conn = ConnectionInfo(
                    name=parts[col_idx.get("name", 0)] if "name" in col_idx else "",
                    user=parts[col_idx.get("user", 1)] if "user" in col_idx else "",
                    state=parts[col_idx["state"]] if "state" in col_idx else "running",
                    channels=int(parts[col_idx["channels"]]) if "channels" in col_idx else 0,
                    client_host=parts[col_idx["peer_host"]] if "peer_host" in col_idx else "",
                    ssl=parts[col_idx["ssl"]].lower() == "true" if "ssl" in col_idx else False,
                    protocol=parts[col_idx["protocol"]] if "protocol" in col_idx else "AMQP 0-9-1",
                )
                connections.append(conn)
            except (ValueError, IndexError) as e:
                logger.debug("failed_to_parse_connection", line=stripped_line, error=str(e))
                continue

        return connections

    @staticmethod
    def _convert_to_bytes(value: float, unit: str) -> int:
        """Convert a value with unit to bytes.

        Args:
            value: Numeric value.
            unit: Unit string (gb, mb, kb, etc.).

        Returns:
            Value in bytes.
        """
        unit = unit.lower()
        multipliers = {
            "b": 1,
            "bytes": 1,
            "kb": 1024,
            "mb": 1024**2,
            "gb": 1024**3,
            "tb": 1024**4,
        }
        return int(value * multipliers.get(unit, 1))

    @staticmethod
    def infer_service_from_user(user: str) -> str:
        """Infer OpenStack service from RabbitMQ username.

        RabbitMQ usernames typically contain the service name.

        Args:
            user: RabbitMQ username.

        Returns:
            Inferred service name or empty string.
        """
        user_lower = user.lower()
        services = [
            "nova",
            "neutron",
            "cinder",
            "glance",
            "keystone",
            "heat",
            "octavia",
            "designate",
            "barbican",
            "placement",
        ]
        for service in services:
            if service in user_lower:
                return service
        return ""
