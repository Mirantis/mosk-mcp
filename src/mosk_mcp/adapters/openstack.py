"""OpenStack adapter for MOSK MCP Server.

This module provides an adapter for interacting with OpenStack services
(Nova, Neutron, Cinder, etc.) via the keystone-client pod in MOSK clusters.

The adapter executes OpenStack CLI commands through kubectl exec to the
keystone-client pod, which has pre-configured credentials and endpoints.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any, cast, overload

from mosk_mcp.adapters.utils import execute_in_pod
from mosk_mcp.core.exceptions import (
    MoskConnectionError,
    MoskMCPError,
)
from mosk_mcp.observability.logging import get_logger
from mosk_mcp.tools.common.enums import MigrationStatus


if TYPE_CHECKING:
    from datetime import datetime

    from mosk_mcp.adapters.kubernetes import KubernetesAdapter


logger = get_logger(__name__)


# =============================================================================
# Constants
# =============================================================================

# Timeout constants (in seconds)
DEFAULT_COMMAND_TIMEOUT = 30  # Standard OpenStack CLI commands
CREATE_RESOURCE_TIMEOUT = 60  # Creating networks, subnets
VOLUME_OPERATION_TIMEOUT = 120  # Volume create/delete/attach/detach
SERVER_WAIT_TIMEOUT = 180  # Server delete/stop with wait
SERVER_CREATE_TIMEOUT = 300  # Server create with wait

# Default list limits
DEFAULT_LIST_LIMIT = 50  # Networks, images, volumes, stacks
MIGRATION_LIST_LIMIT = 100  # Migrations, servers
PROJECT_LIST_LIMIT = 10  # Projects (typically fewer)
CONSOLE_LOG_LINES = 50  # Default console log lines


# =============================================================================
# Data Classes
# =============================================================================


class MigrationType(str, Enum):
    """Nova migration type."""

    LIVE_MIGRATION = "live-migration"
    MIGRATION = "migration"
    RESIZE = "resize"
    EVACUATION = "evacuation"


@dataclass
class ServerMigration:
    """Nova server migration information."""

    id: str
    server_uuid: str
    server_name: str | None
    status: MigrationStatus
    migration_type: MigrationType
    source_compute: str
    dest_compute: str
    source_node: str | None = None
    dest_node: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    disk_processed_bytes: int = 0
    disk_remaining_bytes: int = 0
    disk_total_bytes: int = 0
    memory_processed_bytes: int = 0
    memory_remaining_bytes: int = 0
    memory_total_bytes: int = 0


@dataclass
class ComputeService:
    """Nova compute service information."""

    id: str
    binary: str
    host: str
    zone: str
    status: str  # enabled/disabled
    state: str  # up/down
    updated_at: datetime | None = None
    disabled_reason: str | None = None


@dataclass
class Hypervisor:
    """Nova hypervisor information."""

    id: str
    hypervisor_hostname: str
    host_ip: str
    state: str  # up/down
    status: str  # enabled/disabled
    hypervisor_type: str
    vcpus: int
    vcpus_used: int
    memory_mb: int
    memory_mb_used: int
    local_gb: int
    local_gb_used: int
    running_vms: int


# =============================================================================
# OpenStack Adapter
# =============================================================================


class OpenStackError(MoskMCPError):
    """Error from OpenStack operations."""

    def __init__(
        self,
        message: str,
        command: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.command = command
        self.details = details or {}


class OpenStackAdapter:
    """Adapter for OpenStack operations via keystone-client pod.

    This adapter executes OpenStack CLI commands through kubectl exec
    to the keystone-client pod in the openstack namespace.

    Example:
        async with OpenStackAdapter(k8s_adapter) as os:
            migrations = await os.list_migrations()
            hypervisors = await os.list_hypervisors()
    """

    DEFAULT_NAMESPACE = "openstack"
    DEFAULT_CLIENT_LABEL = "application=keystone,component=client"

    def __init__(
        self,
        kubernetes_adapter: KubernetesAdapter,
        namespace: str | None = None,
        client_pod_label: str | None = None,
    ) -> None:
        """Initialize OpenStack adapter.

        Args:
            kubernetes_adapter: Kubernetes adapter for cluster access.
            namespace: OpenStack namespace (default: openstack).
            client_pod_label: Label selector for client pod.
        """
        self._k8s = kubernetes_adapter
        self._namespace = namespace or self.DEFAULT_NAMESPACE
        self._client_pod_label = client_pod_label or self.DEFAULT_CLIENT_LABEL
        self._client_pod_name: str | None = None
        self._connected = False

    async def __aenter__(self) -> OpenStackAdapter:
        """Enter async context and connect."""
        await self.connect()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        """Exit async context and disconnect."""
        await self.disconnect()

    async def connect(self) -> None:
        """Establish connection and find client pod.

        Raises:
            MoskConnectionError: If client pod cannot be found.
        """
        if self._connected:
            return

        logger.debug(
            "connecting_to_openstack",
            namespace=self._namespace,
            label=self._client_pod_label,
        )

        try:
            # Find keystone-client pod
            pods = await self._k8s.list(
                kind="Pod",
                namespace=self._namespace,
                label_selector=self._client_pod_label,
            )

            running_pods = [p for p in pods if p.get("status", {}).get("phase") == "Running"]

            if not running_pods:
                raise MoskConnectionError(
                    f"No running OpenStack client pod found with label '{self._client_pod_label}' "
                    f"in namespace '{self._namespace}'",
                    service="openstack",
                )

            self._client_pod_name = running_pods[0]["metadata"]["name"]
            self._connected = True

            logger.info(
                "openstack_connected",
                client_pod=self._client_pod_name,
                namespace=self._namespace,
            )

        except MoskConnectionError:
            raise
        except Exception as e:
            raise MoskConnectionError(
                f"Failed to connect to OpenStack: {e}",
                service="openstack",
            ) from e

    async def disconnect(self) -> None:
        """Disconnect from OpenStack."""
        self._client_pod_name = None
        self._connected = False
        logger.debug("openstack_disconnected")

    def _ensure_connected(self) -> None:
        """Ensure adapter is connected."""
        if not self._connected or not self._client_pod_name:
            raise MoskConnectionError(
                "OpenStack adapter not connected. Call connect() first.",
                service="openstack",
            )

    async def _execute_openstack_command(
        self,
        command: list[str],
        timeout: int = DEFAULT_COMMAND_TIMEOUT,
    ) -> str:
        """Execute an OpenStack CLI command via kubectl exec.

        Args:
            command: OpenStack command (e.g., ['server', 'list', '-f', 'json']).
            timeout: Command timeout in seconds.

        Returns:
            Command stdout.

        Raises:
            OpenStackError: If command fails.
        """
        self._ensure_connected()

        # Build full openstack command
        full_command = ["openstack", *command]

        logger.debug(
            "executing_openstack_command",
            command=full_command,
            pod=self._client_pod_name,
        )

        try:
            # Execute via kubectl exec using shared utility
            exec_result = await execute_in_pod(
                kubernetes_adapter=self._k8s,
                pod_name=self._client_pod_name or "",
                namespace=self._namespace,
                command=full_command,
                timeout=timeout,
                raise_on_error=False,  # Handle errors ourselves for OpenStackError
                service_name="openstack_exec",
            )

            # Check for errors and convert to OpenStackError
            if not exec_result.success:
                error_output = (
                    exec_result.stderr
                    or exec_result.stdout
                    or f"Command failed with exit code {exec_result.return_code}"
                )
                raise OpenStackError(
                    f"OpenStack command failed: {error_output}",
                    command=" ".join(command),
                    details={
                        "returncode": exec_result.return_code,
                        "stderr": exec_result.stderr,
                        "stdout": exec_result.stdout,
                    },
                )

            return exec_result.stdout

        except OpenStackError:
            raise
        except Exception as e:
            raise OpenStackError(
                f"OpenStack command failed: {e}",
                command=" ".join(command),
            ) from e

    @overload
    async def _execute_and_parse_json(
        self,
        command: list[str],
        operation: str,
        timeout: int = ...,
        fallback: None = ...,
    ) -> dict[str, Any] | None: ...

    @overload
    async def _execute_and_parse_json(
        self,
        command: list[str],
        operation: str,
        timeout: int = ...,
        fallback: list[dict[str, Any]] = ...,
    ) -> list[dict[str, Any]]: ...

    async def _execute_and_parse_json(
        self,
        command: list[str],
        operation: str,
        timeout: int = DEFAULT_COMMAND_TIMEOUT,
        fallback: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]] | dict[str, Any] | None:
        """Execute OpenStack command and parse JSON result with logging.

        This helper method wraps command execution with proper error logging,
        avoiding silent failures while still returning fallback values for
        non-critical operations.

        Args:
            command: OpenStack CLI command arguments.
            operation: Human-readable operation name for logging.
            timeout: Command timeout in seconds.
            fallback: Value to return on failure (None or []).

        Returns:
            Parsed JSON result or fallback value on error.
        """
        try:
            result = await self._execute_openstack_command(command, timeout=timeout)
            if not result:
                return fallback
            parsed: list[dict[str, Any]] | dict[str, Any] = json.loads(result)
            return parsed
        except OpenStackError as e:
            logger.warning(
                "openstack_command_failed",
                operation=operation,
                command=" ".join(command),
                error=str(e),
                error_type="OpenStackError",
            )
            return fallback
        except json.JSONDecodeError as e:
            logger.warning(
                "openstack_json_parse_failed",
                operation=operation,
                command=" ".join(command),
                error=str(e),
                error_type="JSONDecodeError",
            )
            return fallback

    # =========================================================================
    # Server Migrations
    # =========================================================================

    async def list_migrations(
        self,
        status: MigrationStatus | None = None,
        migration_type: MigrationType | None = None,
        source_compute: str | None = None,
        dest_compute: str | None = None,
        limit: int = MIGRATION_LIST_LIMIT,
    ) -> list[ServerMigration]:
        """List server migrations.

        Args:
            status: Filter by migration status.
            migration_type: Filter by migration type.
            source_compute: Filter by source compute host.
            dest_compute: Filter by destination compute host.
            limit: Maximum number of migrations to return.

        Returns:
            List of server migrations.
        """
        cmd = ["server", "migration", "list", "-f", "json", "--limit", str(limit)]

        if status:
            cmd.extend(["--status", status.value])
        if migration_type:
            cmd.extend(["--type", migration_type.value])

        result = await self._execute_openstack_command(cmd)

        if not result or result == "[]":
            return []

        try:
            data = json.loads(result)
        except json.JSONDecodeError as e:
            # Raise exception - malformed JSON indicates API issues
            logger.error("failed_to_parse_migration_list", result=result[:200], error=str(e))
            raise OpenStackError(
                f"Failed to parse migration list response: {e}",
                command="server migration list",
                details={"response_preview": result[:200]},
            ) from e

        migrations = []
        for item in data:
            # Parse status
            status_str = item.get("Status", "").lower().replace("_", "-")
            try:
                mig_status = MigrationStatus(status_str)
            except ValueError:
                mig_status = MigrationStatus.ERROR

            # Parse type
            type_str = item.get("Type", "").lower().replace("_", "-")
            try:
                mig_type = MigrationType(type_str)
            except ValueError:
                mig_type = MigrationType.MIGRATION

            source = item.get("Source Compute", item.get("Source Host", ""))
            dest = item.get("Dest Compute", item.get("Dest Host", ""))

            # Apply filters
            if source_compute and source != source_compute:
                continue
            if dest_compute and dest != dest_compute:
                continue

            migration = ServerMigration(
                id=str(item.get("Id", "")),
                server_uuid=item.get("Server UUID", ""),
                server_name=item.get("Server Name"),
                status=mig_status,
                migration_type=mig_type,
                source_compute=source,
                dest_compute=dest,
                source_node=item.get("Source Node"),
                dest_node=item.get("Dest Node"),
            )
            migrations.append(migration)

        return migrations

    async def get_migration(
        self,
        server_id: str,
        migration_id: str,
    ) -> ServerMigration | None:
        """Get details of a specific migration.

        Args:
            server_id: Server UUID.
            migration_id: Migration ID.

        Returns:
            Migration details or None if not found.
        """
        cmd = ["server", "migration", "show", server_id, migration_id, "-f", "json"]

        try:
            result = await self._execute_openstack_command(cmd)
        except OpenStackError as e:
            if "not found" in str(e).lower():
                return None
            raise

        if not result:
            return None

        try:
            data = json.loads(result)
        except json.JSONDecodeError as e:
            # Log JSONDecodeError with context to distinguish parse errors from "not found"
            logger.warning(
                "migration_json_parse_failed",
                server_id=server_id,
                migration_id=migration_id,
                error=str(e),
                response_preview=result[:200] if result else None,
            )
            return None

        # Parse status
        status_str = data.get("status", "").lower().replace("_", "-")
        try:
            mig_status = MigrationStatus(status_str)
        except ValueError:
            mig_status = MigrationStatus.ERROR

        # Parse type
        type_str = data.get("migration_type", "").lower().replace("_", "-")
        try:
            mig_type = MigrationType(type_str)
        except ValueError:
            mig_type = MigrationType.MIGRATION

        return ServerMigration(
            id=str(data.get("id", migration_id)),
            server_uuid=data.get("server_uuid", server_id),
            server_name=data.get("server_name"),
            status=mig_status,
            migration_type=mig_type,
            source_compute=data.get("source_compute", ""),
            dest_compute=data.get("dest_compute", ""),
            source_node=data.get("source_node"),
            dest_node=data.get("dest_node"),
            disk_processed_bytes=data.get("disk_processed_bytes", 0),
            disk_remaining_bytes=data.get("disk_remaining_bytes", 0),
            disk_total_bytes=data.get("disk_total_bytes", 0),
            memory_processed_bytes=data.get("memory_processed_bytes", 0),
            memory_remaining_bytes=data.get("memory_remaining_bytes", 0),
            memory_total_bytes=data.get("memory_total_bytes", 0),
        )

    # =========================================================================
    # Compute Services
    # =========================================================================

    async def list_compute_services(
        self,
        host: str | None = None,
    ) -> list[ComputeService]:
        """List Nova compute services.

        Args:
            host: Filter by host name.

        Returns:
            List of compute services.
        """
        cmd = ["compute", "service", "list", "-f", "json"]
        if host:
            cmd.extend(["--host", host])

        result = await self._execute_openstack_command(cmd)

        if not result or result == "[]":
            return []

        try:
            data = json.loads(result)
        except json.JSONDecodeError as e:
            # Raise exception - malformed JSON indicates API issues
            logger.error(
                "openstack_json_parse_error",
                operation="list_compute_services",
                raw_response=result[:500] if result else None,
                error=str(e),
            )
            raise OpenStackError(
                f"Failed to parse compute service list response: {e}",
                command="compute service list",
                details={"response_preview": result[:200] if result else None},
            ) from e

        services = []
        for item in data:
            service = ComputeService(
                id=str(item.get("ID", "")),
                binary=item.get("Binary", ""),
                host=item.get("Host", ""),
                zone=item.get("Zone", ""),
                status=item.get("Status", "").lower(),
                state=item.get("State", "").lower(),
                disabled_reason=item.get("Disabled Reason"),
            )
            services.append(service)

        return services

    # =========================================================================
    # Hypervisors
    # =========================================================================

    async def list_hypervisors(self) -> list[Hypervisor]:
        """List Nova hypervisors.

        Returns:
            List of hypervisors.
        """
        cmd = ["hypervisor", "list", "-f", "json", "--long"]

        result = await self._execute_openstack_command(cmd)

        if not result or result == "[]":
            return []

        try:
            data = json.loads(result)
        except json.JSONDecodeError as e:
            # Raise exception - malformed JSON indicates API issues
            logger.error(
                "openstack_json_parse_error",
                operation="list_hypervisors",
                raw_response=result[:500] if result else None,
                error=str(e),
            )
            raise OpenStackError(
                f"Failed to parse hypervisor list response: {e}",
                command="hypervisor list",
                details={"response_preview": result[:200] if result else None},
            ) from e

        hypervisors = []
        for item in data:
            hypervisor = Hypervisor(
                id=str(item.get("ID", "")),
                hypervisor_hostname=item.get("Hypervisor Hostname", ""),
                host_ip=item.get("Host IP", ""),
                state=item.get("State", "").lower(),
                status=item.get("Status", "").lower(),
                hypervisor_type=item.get("Hypervisor Type", ""),
                vcpus=item.get("vCPUs", 0),
                vcpus_used=item.get("vCPUs Used", 0),
                memory_mb=item.get("Memory MB", 0),
                memory_mb_used=item.get("Memory MB Used", 0),
                local_gb=item.get("Local GB", 0),
                local_gb_used=item.get("Local GB Used", 0),
                running_vms=item.get("Running VMs", 0),
            )
            hypervisors.append(hypervisor)

        return hypervisors

    async def get_hypervisor(self, hostname: str) -> Hypervisor | None:
        """Get hypervisor details by hostname.

        Args:
            hostname: Hypervisor hostname.

        Returns:
            Hypervisor details or None if not found.
        """
        cmd = ["hypervisor", "show", hostname, "-f", "json"]

        try:
            result = await self._execute_openstack_command(cmd)
        except OpenStackError as e:
            if "not found" in str(e).lower():
                return None
            raise

        if not result:
            return None

        try:
            data = json.loads(result)
        except json.JSONDecodeError:
            return None

        return Hypervisor(
            id=str(data.get("id", "")),
            hypervisor_hostname=data.get("hypervisor_hostname", hostname),
            host_ip=data.get("host_ip", ""),
            state=data.get("state", "").lower(),
            status=data.get("status", "").lower(),
            hypervisor_type=data.get("hypervisor_type", ""),
            vcpus=data.get("vcpus", 0),
            vcpus_used=data.get("vcpus_used", 0),
            memory_mb=data.get("memory_mb", 0),
            memory_mb_used=data.get("memory_mb_used", 0),
            local_gb=data.get("local_gb", 0),
            local_gb_used=data.get("local_gb_used", 0),
            running_vms=data.get("running_vms", 0),
        )

    # =========================================================================
    # Server Operations
    # =========================================================================

    async def list_servers(
        self,
        host: str | None = None,
        status: str | None = None,
        all_projects: bool = True,
        limit: int = MIGRATION_LIST_LIMIT,
    ) -> list[dict[str, Any]]:
        """List servers.

        Args:
            host: Filter by compute host.
            status: Filter by server status.
            all_projects: Include servers from all projects.
            limit: Maximum servers to return.

        Returns:
            List of server dictionaries.
        """
        cmd = ["server", "list", "-f", "json", "--limit", str(limit)]

        if all_projects:
            cmd.append("--all-projects")
        if host:
            cmd.extend(["--host", host])
        if status:
            cmd.extend(["--status", status])

        result = await self._execute_openstack_command(cmd)

        if not result or result == "[]":
            return []

        try:
            return cast("list[dict[str, Any]]", json.loads(result))
        except json.JSONDecodeError as e:
            # Raise exception - malformed JSON indicates API issues
            logger.error(
                "openstack_json_parse_error",
                operation="list_servers",
                raw_response=result[:500] if result else None,
                error=str(e),
            )
            raise OpenStackError(
                f"Failed to parse server list response: {e}",
                command="server list",
                details={"response_preview": result[:200] if result else None},
            ) from e

    async def get_server(self, server_id: str) -> dict[str, Any] | None:
        """Get server details.

        Args:
            server_id: Server UUID or name.

        Returns:
            Server details or None if not found.
        """
        cmd = ["server", "show", server_id, "-f", "json"]

        try:
            result = await self._execute_openstack_command(cmd)
        except OpenStackError as e:
            if "not found" in str(e).lower():
                return None
            raise

        if not result:
            return None

        try:
            return cast("dict[str, Any]", json.loads(result))
        except json.JSONDecodeError:
            return None

    # =========================================================================
    # API Availability / Token Operations
    # =========================================================================

    async def get_token(self) -> dict[str, Any] | None:
        """Get current authentication token info.

        Returns:
            Token information or None if failed.
        """
        cmd = ["token", "issue", "-f", "json"]
        return await self._execute_and_parse_json(
            cmd, "get_token", timeout=DEFAULT_COMMAND_TIMEOUT, fallback=None
        )

    async def list_projects(self, limit: int = PROJECT_LIST_LIMIT) -> list[dict[str, Any]]:
        """List projects to verify Keystone API.

        Args:
            limit: Maximum projects to return.

        Returns:
            List of projects.
        """
        cmd = ["project", "list", "-f", "json", "--limit", str(limit)]
        return await self._execute_and_parse_json(cmd, "list_projects", fallback=[])

    async def list_services(self) -> list[dict[str, Any]]:
        """List OpenStack services from Keystone catalog.

        Returns:
            List of services.
        """
        cmd = ["service", "list", "-f", "json"]
        return await self._execute_and_parse_json(cmd, "list_services", fallback=[])

    async def list_endpoints(self) -> list[dict[str, Any]]:
        """List OpenStack endpoints from Keystone catalog.

        Returns:
            List of endpoints.
        """
        cmd = ["endpoint", "list", "-f", "json"]
        return await self._execute_and_parse_json(cmd, "list_endpoints", fallback=[])

    # =========================================================================
    # Neutron Operations
    # =========================================================================

    async def list_network_agents(self) -> list[dict[str, Any]]:
        """List Neutron agents.

        Returns:
            List of network agents.
        """
        cmd = ["network", "agent", "list", "-f", "json"]
        return await self._execute_and_parse_json(cmd, "list_network_agents", fallback=[])

    async def list_networks(self, limit: int = DEFAULT_LIST_LIMIT) -> list[dict[str, Any]]:
        """List networks.

        Args:
            limit: Maximum networks to return.

        Returns:
            List of networks.
        """
        cmd = ["network", "list", "-f", "json", "--limit", str(limit)]
        return await self._execute_and_parse_json(cmd, "list_networks", fallback=[])

    async def create_network(self, name: str) -> dict[str, Any] | None:
        """Create a network.

        Args:
            name: Network name.

        Returns:
            Created network or None if failed.
        """
        cmd = ["network", "create", name, "-f", "json"]
        return await self._execute_and_parse_json(
            cmd, "create_network", timeout=CREATE_RESOURCE_TIMEOUT, fallback=None
        )

    async def delete_network(self, name_or_id: str) -> bool:
        """Delete a network.

        Args:
            name_or_id: Network name or ID.

        Returns:
            True if deleted successfully.
        """
        cmd = ["network", "delete", name_or_id]

        try:
            await self._execute_openstack_command(cmd, timeout=CREATE_RESOURCE_TIMEOUT)
            return True
        except OpenStackError as e:
            logger.warning("network_delete_failed", network=name_or_id, error=str(e))
            return False

    async def create_subnet(
        self,
        name: str,
        network: str,
        subnet_range: str,
    ) -> dict[str, Any] | None:
        """Create a subnet.

        Args:
            name: Subnet name.
            network: Network name or ID.
            subnet_range: CIDR range (e.g., '192.168.100.0/24').

        Returns:
            Created subnet or None if failed.
        """
        cmd = [
            "subnet",
            "create",
            name,
            "--network",
            network,
            "--subnet-range",
            subnet_range,
            "-f",
            "json",
        ]
        return await self._execute_and_parse_json(
            cmd, "create_subnet", timeout=CREATE_RESOURCE_TIMEOUT, fallback=None
        )

    async def delete_subnet(self, name_or_id: str) -> bool:
        """Delete a subnet.

        Args:
            name_or_id: Subnet name or ID.

        Returns:
            True if deleted successfully.
        """
        cmd = ["subnet", "delete", name_or_id]

        try:
            await self._execute_openstack_command(cmd, timeout=CREATE_RESOURCE_TIMEOUT)
            return True
        except OpenStackError as e:
            logger.warning("subnet_delete_failed", subnet=name_or_id, error=str(e))
            return False

    # =========================================================================
    # Glance Operations
    # =========================================================================

    async def list_images(self, limit: int = DEFAULT_LIST_LIMIT) -> list[dict[str, Any]]:
        """List images.

        Args:
            limit: Maximum images to return.

        Returns:
            List of images.
        """
        cmd = ["image", "list", "-f", "json", "--limit", str(limit)]
        return await self._execute_and_parse_json(cmd, "list_images", fallback=[])

    # =========================================================================
    # Cinder Operations
    # =========================================================================

    async def list_volume_services(self) -> list[dict[str, Any]]:
        """List Cinder volume services.

        Returns:
            List of volume services.
        """
        cmd = ["volume", "service", "list", "-f", "json"]
        return await self._execute_and_parse_json(cmd, "list_volume_services", fallback=[])

    async def list_volumes(
        self,
        all_projects: bool = True,
        limit: int = DEFAULT_LIST_LIMIT,
    ) -> list[dict[str, Any]]:
        """List volumes.

        Args:
            all_projects: Include volumes from all projects.
            limit: Maximum volumes to return.

        Returns:
            List of volumes.
        """
        cmd = ["volume", "list", "-f", "json", "--limit", str(limit)]
        if all_projects:
            cmd.append("--all-projects")
        return await self._execute_and_parse_json(cmd, "list_volumes", fallback=[])

    async def create_volume(
        self,
        name: str,
        size_gb: int = 1,
    ) -> dict[str, Any] | None:
        """Create a volume.

        Args:
            name: Volume name.
            size_gb: Size in GB.

        Returns:
            Created volume or None if failed.
        """
        cmd = [
            "volume",
            "create",
            name,
            "--size",
            str(size_gb),
            "-f",
            "json",
        ]
        return await self._execute_and_parse_json(
            cmd, "create_volume", timeout=VOLUME_OPERATION_TIMEOUT, fallback=None
        )

    async def get_volume(self, volume_id: str) -> dict[str, Any] | None:
        """Get volume details.

        Args:
            volume_id: Volume ID or name.

        Returns:
            Volume details or None if not found.
        """
        cmd = ["volume", "show", volume_id, "-f", "json"]
        return await self._execute_and_parse_json(cmd, "get_volume", fallback=None)

    async def delete_volume(self, name_or_id: str, force: bool = False) -> bool:
        """Delete a volume.

        Args:
            name_or_id: Volume name or ID.
            force: Force delete even if in-use.

        Returns:
            True if deleted successfully.
        """
        cmd = ["volume", "delete", name_or_id]
        if force:
            cmd.append("--force")

        try:
            await self._execute_openstack_command(cmd, timeout=VOLUME_OPERATION_TIMEOUT)
            return True
        except OpenStackError as e:
            logger.warning("volume_delete_failed", volume=name_or_id, error=str(e))
            return False

    async def attach_volume(
        self,
        server: str,
        volume: str,
    ) -> bool:
        """Attach a volume to a server.

        Args:
            server: Server name or ID.
            volume: Volume name or ID.

        Returns:
            True if attached successfully.
        """
        cmd = ["server", "add", "volume", server, volume]

        try:
            await self._execute_openstack_command(cmd, timeout=VOLUME_OPERATION_TIMEOUT)
            return True
        except OpenStackError as e:
            logger.warning("volume_attach_failed", server=server, volume=volume, error=str(e))
            return False

    async def detach_volume(
        self,
        server: str,
        volume: str,
    ) -> bool:
        """Detach a volume from a server.

        Args:
            server: Server name or ID.
            volume: Volume name or ID.

        Returns:
            True if detached successfully.
        """
        cmd = ["server", "remove", "volume", server, volume]

        try:
            await self._execute_openstack_command(cmd, timeout=VOLUME_OPERATION_TIMEOUT)
            return True
        except OpenStackError as e:
            logger.warning("volume_detach_failed", server=server, volume=volume, error=str(e))
            return False

    # =========================================================================
    # Server Lifecycle Operations
    # =========================================================================

    async def create_server(
        self,
        name: str,
        image: str,
        flavor: str,
        network: str | None = None,
        key_name: str | None = None,
        security_group: str | None = None,
        wait: bool = True,
    ) -> dict[str, Any] | None:
        """Create a server.

        Args:
            name: Server name.
            image: Image name or ID.
            flavor: Flavor name or ID.
            network: Network name or ID (optional).
            key_name: SSH keypair name (optional).
            security_group: Security group name (optional).
            wait: Wait for server to become ACTIVE.

        Returns:
            Created server or None if failed.
        """
        cmd = [
            "server",
            "create",
            name,
            "--image",
            image,
            "--flavor",
            flavor,
            "-f",
            "json",
        ]

        if network:
            cmd.extend(["--network", network])
        if key_name:
            cmd.extend(["--key-name", key_name])
        if security_group:
            cmd.extend(["--security-group", security_group])
        if wait:
            cmd.append("--wait")

        return await self._execute_and_parse_json(
            cmd,
            "create_server",
            timeout=SERVER_CREATE_TIMEOUT if wait else CREATE_RESOURCE_TIMEOUT,
            fallback=None,
        )

    async def delete_server(
        self,
        name_or_id: str,
        wait: bool = True,
        force: bool = False,
    ) -> bool:
        """Delete a server.

        Args:
            name_or_id: Server name or ID.
            wait: Wait for server to be deleted.
            force: Force delete.

        Returns:
            True if deleted successfully.
        """
        cmd = ["server", "delete", name_or_id]
        if wait:
            cmd.append("--wait")
        if force:
            cmd.append("--force")

        try:
            await self._execute_openstack_command(
                cmd,
                timeout=SERVER_WAIT_TIMEOUT if wait else DEFAULT_COMMAND_TIMEOUT,
            )
            return True
        except OpenStackError as e:
            logger.warning("server_delete_failed", server=name_or_id, error=str(e))
            return False

    async def reboot_server(
        self,
        name_or_id: str,
        hard: bool = False,
        wait: bool = True,
    ) -> bool:
        """Reboot a server.

        Args:
            name_or_id: Server name or ID.
            hard: Hard reboot (vs soft reboot).
            wait: Wait for server to become ACTIVE.

        Returns:
            True if rebooted successfully.
        """
        cmd = ["server", "reboot", name_or_id]
        if hard:
            cmd.append("--hard")
        if wait:
            cmd.append("--wait")

        try:
            await self._execute_openstack_command(
                cmd,
                timeout=SERVER_WAIT_TIMEOUT if wait else DEFAULT_COMMAND_TIMEOUT,
            )
            return True
        except OpenStackError as e:
            logger.warning("server_reboot_failed", server=name_or_id, error=str(e))
            return False

    async def get_server_console_output(
        self,
        server_id: str,
        lines: int = CONSOLE_LOG_LINES,
    ) -> str | None:
        """Get server console output.

        Args:
            server_id: Server ID or name.
            lines: Number of lines to return.

        Returns:
            Console output or None if failed.
        """
        cmd = ["console", "log", "show", server_id, "--lines", str(lines)]

        try:
            return await self._execute_openstack_command(cmd, timeout=DEFAULT_COMMAND_TIMEOUT)
        except OpenStackError as e:
            logger.warning("console_output_failed", server=server_id, error=str(e))
            return None

    # =========================================================================
    # Keypair Operations
    # =========================================================================

    async def create_keypair(self, name: str) -> dict[str, Any] | None:
        """Create an SSH keypair.

        Args:
            name: Keypair name.

        Returns:
            Created keypair (including private key) or None if failed.
        """
        cmd = ["keypair", "create", name, "-f", "json"]
        return await self._execute_and_parse_json(cmd, "create_keypair", fallback=None)

    async def delete_keypair(self, name: str) -> bool:
        """Delete an SSH keypair.

        Args:
            name: Keypair name.

        Returns:
            True if deleted successfully.
        """
        cmd = ["keypair", "delete", name]

        try:
            await self._execute_openstack_command(cmd)
            return True
        except OpenStackError as e:
            logger.warning("keypair_delete_failed", keypair=name, error=str(e))
            return False

    async def list_keypairs(self) -> list[dict[str, Any]]:
        """List SSH keypairs.

        Returns:
            List of keypairs.
        """
        cmd = ["keypair", "list", "-f", "json"]
        return await self._execute_and_parse_json(cmd, "list_keypairs", fallback=[])

    # =========================================================================
    # Security Group Operations
    # =========================================================================

    async def create_security_group(
        self,
        name: str,
        description: str = "",
    ) -> dict[str, Any] | None:
        """Create a security group.

        Args:
            name: Security group name.
            description: Description.

        Returns:
            Created security group or None if failed.
        """
        cmd = ["security", "group", "create", name, "-f", "json"]
        if description:
            cmd.extend(["--description", description])
        return await self._execute_and_parse_json(cmd, "create_security_group", fallback=None)

    async def delete_security_group(self, name_or_id: str) -> bool:
        """Delete a security group.

        Args:
            name_or_id: Security group name or ID.

        Returns:
            True if deleted successfully.
        """
        cmd = ["security", "group", "delete", name_or_id]

        try:
            await self._execute_openstack_command(cmd)
            return True
        except OpenStackError as e:
            logger.warning("security_group_delete_failed", security_group=name_or_id, error=str(e))
            return False

    async def add_security_group_rule(
        self,
        group: str,
        protocol: str = "tcp",
        port: int | None = None,
        remote_ip: str = "0.0.0.0/0",
        ingress: bool = True,
    ) -> dict[str, Any] | None:
        """Add a rule to a security group.

        Args:
            group: Security group name or ID.
            protocol: Protocol (tcp, udp, icmp).
            port: Port number (for tcp/udp).
            remote_ip: Remote IP CIDR.
            ingress: Ingress rule (vs egress).

        Returns:
            Created rule or None if failed.
        """
        cmd = [
            "security",
            "group",
            "rule",
            "create",
            group,
            "--protocol",
            protocol,
            "--remote-ip",
            remote_ip,
            "-f",
            "json",
        ]

        if ingress:
            cmd.append("--ingress")
        else:
            cmd.append("--egress")

        if port and protocol in ("tcp", "udp"):
            cmd.extend(["--dst-port", str(port)])

        return await self._execute_and_parse_json(cmd, "add_security_group_rule", fallback=None)

    # =========================================================================
    # Flavor Operations
    # =========================================================================

    async def list_flavors(self) -> list[dict[str, Any]]:
        """List flavors.

        Returns:
            List of flavors.
        """
        cmd = ["flavor", "list", "-f", "json"]
        return await self._execute_and_parse_json(cmd, "list_flavors", fallback=[])

    # =========================================================================
    # Heat Operations
    # =========================================================================

    async def list_stacks(self, limit: int = DEFAULT_LIST_LIMIT) -> list[dict[str, Any]]:
        """List Heat stacks.

        Args:
            limit: Maximum stacks to return.

        Returns:
            List of stacks.
        """
        cmd = ["stack", "list", "-f", "json", "--limit", str(limit)]
        return await self._execute_and_parse_json(cmd, "list_stacks", fallback=[])
