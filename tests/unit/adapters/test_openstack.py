"""Tests for OpenStack adapter implementation.

Tests the OpenStackAdapter class, data structures, and enums.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mosk_mcp.adapters.openstack import (
    CONSOLE_LOG_LINES,
    CREATE_RESOURCE_TIMEOUT,
    DEFAULT_COMMAND_TIMEOUT,
    DEFAULT_LIST_LIMIT,
    MIGRATION_LIST_LIMIT,
    PROJECT_LIST_LIMIT,
    SERVER_CREATE_TIMEOUT,
    SERVER_WAIT_TIMEOUT,
    VOLUME_OPERATION_TIMEOUT,
    ComputeService,
    Hypervisor,
    MigrationStatus,
    MigrationType,
    OpenStackAdapter,
    OpenStackError,
    ServerMigration,
)
from mosk_mcp.core.exceptions import MoskConnectionError


# =============================================================================
# Constants Tests
# =============================================================================


class TestConstants:
    """Tests for module constants."""

    def test_timeout_constants(self) -> None:
        """Test timeout constants are defined."""
        assert DEFAULT_COMMAND_TIMEOUT == 30
        assert CREATE_RESOURCE_TIMEOUT == 60
        assert VOLUME_OPERATION_TIMEOUT == 120
        assert SERVER_WAIT_TIMEOUT == 180
        assert SERVER_CREATE_TIMEOUT == 300

    def test_limit_constants(self) -> None:
        """Test limit constants are defined."""
        assert DEFAULT_LIST_LIMIT == 50
        assert MIGRATION_LIST_LIMIT == 100
        assert PROJECT_LIST_LIMIT == 10
        assert CONSOLE_LOG_LINES == 50

    def test_timeout_ordering(self) -> None:
        """Test timeouts are in reasonable order."""
        assert DEFAULT_COMMAND_TIMEOUT < CREATE_RESOURCE_TIMEOUT
        assert CREATE_RESOURCE_TIMEOUT < VOLUME_OPERATION_TIMEOUT
        assert VOLUME_OPERATION_TIMEOUT < SERVER_WAIT_TIMEOUT
        assert SERVER_WAIT_TIMEOUT < SERVER_CREATE_TIMEOUT


# =============================================================================
# Enum Tests
# =============================================================================


class TestMigrationStatus:
    """Tests for MigrationStatus enum."""

    def test_all_statuses_defined(self) -> None:
        """Test all migration statuses are defined."""
        assert MigrationStatus.QUEUED == "queued"
        assert MigrationStatus.PREPARING == "preparing"
        assert MigrationStatus.RUNNING == "running"
        assert MigrationStatus.POST_MIGRATING == "post-migrating"
        assert MigrationStatus.COMPLETED == "completed"
        assert MigrationStatus.FAILED == "failed"
        assert MigrationStatus.CANCELLED == "cancelled"
        assert MigrationStatus.ERROR == "error"
        assert MigrationStatus.ACCEPTED == "accepted"
        assert MigrationStatus.PRE_MIGRATING == "pre-migrating"

    def test_status_count(self) -> None:
        """Test expected number of statuses (includes UNKNOWN for parsing failures)."""
        assert len(MigrationStatus) == 11


class TestMigrationType:
    """Tests for MigrationType enum."""

    def test_all_types_defined(self) -> None:
        """Test all migration types are defined."""
        assert MigrationType.LIVE_MIGRATION == "live-migration"
        assert MigrationType.MIGRATION == "migration"
        assert MigrationType.RESIZE == "resize"
        assert MigrationType.EVACUATION == "evacuation"

    def test_type_count(self) -> None:
        """Test expected number of types."""
        assert len(MigrationType) == 4


# =============================================================================
# Data Class Tests
# =============================================================================


class TestServerMigration:
    """Tests for ServerMigration dataclass."""

    def test_required_fields(self) -> None:
        """Test creating migration with required fields."""
        migration = ServerMigration(
            id="mig-123",
            server_uuid="server-456",
            server_name="test-server",
            status=MigrationStatus.RUNNING,
            migration_type=MigrationType.LIVE_MIGRATION,
            source_compute="compute-01",
            dest_compute="compute-02",
        )

        assert migration.id == "mig-123"
        assert migration.server_uuid == "server-456"
        assert migration.status == MigrationStatus.RUNNING
        assert migration.migration_type == MigrationType.LIVE_MIGRATION
        assert migration.source_compute == "compute-01"
        assert migration.dest_compute == "compute-02"

    def test_default_fields(self) -> None:
        """Test default field values."""
        migration = ServerMigration(
            id="mig-123",
            server_uuid="server-456",
            server_name=None,
            status=MigrationStatus.QUEUED,
            migration_type=MigrationType.MIGRATION,
            source_compute="compute-01",
            dest_compute="compute-02",
        )

        assert migration.source_node is None
        assert migration.dest_node is None
        assert migration.created_at is None
        assert migration.disk_processed_bytes == 0
        assert migration.memory_processed_bytes == 0


class TestComputeService:
    """Tests for ComputeService dataclass."""

    def test_all_fields(self) -> None:
        """Test creating compute service with all fields."""
        now = datetime.now(UTC)
        service = ComputeService(
            id="service-123",
            binary="nova-compute",
            host="compute-01",
            zone="nova",
            status="enabled",
            state="up",
            updated_at=now,
            disabled_reason=None,
        )

        assert service.id == "service-123"
        assert service.binary == "nova-compute"
        assert service.host == "compute-01"
        assert service.zone == "nova"
        assert service.status == "enabled"
        assert service.state == "up"
        assert service.updated_at == now


class TestHypervisor:
    """Tests for Hypervisor dataclass."""

    def test_all_fields(self) -> None:
        """Test creating hypervisor with all fields."""
        hypervisor = Hypervisor(
            id="hv-123",
            hypervisor_hostname="compute-01",
            host_ip="192.168.1.100",
            state="up",
            status="enabled",
            hypervisor_type="QEMU",
            vcpus=32,
            vcpus_used=16,
            memory_mb=65536,
            memory_mb_used=32768,
            local_gb=1000,
            local_gb_used=500,
            running_vms=10,
        )

        assert hypervisor.id == "hv-123"
        assert hypervisor.hypervisor_hostname == "compute-01"
        assert hypervisor.vcpus == 32
        assert hypervisor.vcpus_used == 16
        assert hypervisor.running_vms == 10


# =============================================================================
# OpenStackError Tests
# =============================================================================


class TestOpenStackError:
    """Tests for OpenStackError exception."""

    def test_basic_error(self) -> None:
        """Test basic error creation."""
        error = OpenStackError("Something failed")
        assert str(error) == "Something failed"
        assert error.command is None
        assert error.details == {}

    def test_error_with_command(self) -> None:
        """Test error with command."""
        error = OpenStackError(
            "Command failed",
            command="server list",
            details={"returncode": 1},
        )
        assert error.command == "server list"
        assert error.details["returncode"] == 1


# =============================================================================
# OpenStackAdapter Initialization Tests
# =============================================================================


class TestOpenStackAdapterInitialization:
    """Tests for OpenStackAdapter initialization."""

    def test_default_initialization(self) -> None:
        """Test default initialization."""
        mock_k8s = AsyncMock()
        adapter = OpenStackAdapter(mock_k8s)

        assert adapter._namespace == "openstack"
        assert adapter._client_pod_label == "application=keystone,component=client"
        assert adapter._connected is False

    def test_custom_initialization(self) -> None:
        """Test custom initialization."""
        mock_k8s = AsyncMock()
        adapter = OpenStackAdapter(
            mock_k8s,
            namespace="custom-openstack",
            client_pod_label="app=custom-client",
        )

        assert adapter._namespace == "custom-openstack"
        assert adapter._client_pod_label == "app=custom-client"


# =============================================================================
# OpenStackAdapter Connection Tests
# =============================================================================


class TestOpenStackAdapterConnection:
    """Tests for OpenStackAdapter connection operations."""

    @pytest.mark.asyncio
    async def test_connect_success(self) -> None:
        """Test successful connection."""
        mock_k8s = AsyncMock()
        mock_k8s.list = AsyncMock(
            return_value=[
                {
                    "metadata": {"name": "keystone-client-12345"},
                    "status": {"phase": "Running"},
                }
            ]
        )

        adapter = OpenStackAdapter(mock_k8s)
        await adapter.connect()

        assert adapter._connected is True
        assert adapter._client_pod_name == "keystone-client-12345"

    @pytest.mark.asyncio
    async def test_connect_no_running_pods(self) -> None:
        """Test connection fails when no running pods found."""
        mock_k8s = AsyncMock()
        mock_k8s.list = AsyncMock(
            return_value=[
                {
                    "metadata": {"name": "keystone-client-12345"},
                    "status": {"phase": "Pending"},
                }
            ]
        )

        adapter = OpenStackAdapter(mock_k8s)

        with pytest.raises(MoskConnectionError) as exc_info:
            await adapter.connect()

        assert "No running OpenStack client pod" in str(exc_info.value)
        assert adapter._connected is False

    @pytest.mark.asyncio
    async def test_connect_idempotent(self) -> None:
        """Test connect is idempotent."""
        mock_k8s = AsyncMock()
        mock_k8s.list = AsyncMock(
            return_value=[
                {
                    "metadata": {"name": "keystone-client-12345"},
                    "status": {"phase": "Running"},
                }
            ]
        )

        adapter = OpenStackAdapter(mock_k8s)
        await adapter.connect()
        await adapter.connect()  # Second call should be no-op

        # list should only be called once
        assert mock_k8s.list.call_count == 1

    @pytest.mark.asyncio
    async def test_disconnect(self) -> None:
        """Test disconnection."""
        mock_k8s = AsyncMock()
        mock_k8s.list = AsyncMock(
            return_value=[
                {
                    "metadata": {"name": "keystone-client-12345"},
                    "status": {"phase": "Running"},
                }
            ]
        )

        adapter = OpenStackAdapter(mock_k8s)
        await adapter.connect()
        await adapter.disconnect()

        assert adapter._connected is False
        assert adapter._client_pod_name is None

    @pytest.mark.asyncio
    async def test_context_manager(self) -> None:
        """Test async context manager."""
        mock_k8s = AsyncMock()
        mock_k8s.list = AsyncMock(
            return_value=[
                {
                    "metadata": {"name": "keystone-client-12345"},
                    "status": {"phase": "Running"},
                }
            ]
        )

        async with OpenStackAdapter(mock_k8s) as adapter:
            assert adapter._connected is True

        assert adapter._connected is False


class TestOpenStackAdapterEnsureConnected:
    """Tests for _ensure_connected method."""

    def test_ensure_connected_raises_when_not_connected(self) -> None:
        """Test _ensure_connected raises when not connected."""
        mock_k8s = AsyncMock()
        adapter = OpenStackAdapter(mock_k8s)

        with pytest.raises(MoskConnectionError) as exc_info:
            adapter._ensure_connected()

        assert "not connected" in str(exc_info.value)


# =============================================================================
# OpenStackAdapter Command Execution Tests
# =============================================================================


class TestOpenStackAdapterCommandExecution:
    """Tests for OpenStack command execution."""

    @pytest.mark.asyncio
    async def test_execute_command_success(self) -> None:
        """Test executing command successfully."""
        mock_k8s = AsyncMock()
        mock_k8s.list = AsyncMock(
            return_value=[
                {
                    "metadata": {"name": "keystone-client-12345"},
                    "status": {"phase": "Running"},
                }
            ]
        )

        adapter = OpenStackAdapter(mock_k8s)
        await adapter.connect()

        with patch("mosk_mcp.adapters.openstack.execute_in_pod") as mock_exec:
            mock_exec.return_value = MagicMock(
                success=True,
                stdout='[{"id": "server-1"}]',
                stderr="",
                return_code=0,
            )

            result = await adapter._execute_openstack_command(["server", "list", "-f", "json"])

            assert result == '[{"id": "server-1"}]'
            mock_exec.assert_called_once()

    @pytest.mark.asyncio
    async def test_execute_command_failure(self) -> None:
        """Test handling command failure."""
        mock_k8s = AsyncMock()
        mock_k8s.list = AsyncMock(
            return_value=[
                {
                    "metadata": {"name": "keystone-client-12345"},
                    "status": {"phase": "Running"},
                }
            ]
        )

        adapter = OpenStackAdapter(mock_k8s)
        await adapter.connect()

        with patch("mosk_mcp.adapters.openstack.execute_in_pod") as mock_exec:
            mock_exec.return_value = MagicMock(
                success=False,
                stdout="",
                stderr="Error: Authentication failed",
                return_code=1,
            )

            with pytest.raises(OpenStackError) as exc_info:
                await adapter._execute_openstack_command(["server", "list"])

            assert "Authentication failed" in str(exc_info.value)


class TestOpenStackAdapterExecuteAndParseJson:
    """Tests for _execute_and_parse_json method."""

    @pytest.mark.asyncio
    async def test_parse_json_success(self) -> None:
        """Test parsing JSON response successfully."""
        mock_k8s = AsyncMock()
        mock_k8s.list = AsyncMock(
            return_value=[
                {
                    "metadata": {"name": "keystone-client-12345"},
                    "status": {"phase": "Running"},
                }
            ]
        )

        adapter = OpenStackAdapter(mock_k8s)
        await adapter.connect()

        with patch.object(adapter, "_execute_openstack_command", return_value='[{"id": "1"}]'):
            result = await adapter._execute_and_parse_json(
                ["server", "list", "-f", "json"],
                operation="list_servers",
                fallback=[],
            )

            assert result == [{"id": "1"}]

    @pytest.mark.asyncio
    async def test_parse_json_fallback_on_error(self) -> None:
        """Test returning fallback on error."""
        mock_k8s = AsyncMock()
        mock_k8s.list = AsyncMock(
            return_value=[
                {
                    "metadata": {"name": "keystone-client-12345"},
                    "status": {"phase": "Running"},
                }
            ]
        )

        adapter = OpenStackAdapter(mock_k8s)
        await adapter.connect()

        with patch.object(
            adapter,
            "_execute_openstack_command",
            side_effect=OpenStackError("Failed"),
        ):
            result = await adapter._execute_and_parse_json(
                ["server", "list"],
                operation="list_servers",
                fallback=[],
            )

            assert result == []

    @pytest.mark.asyncio
    async def test_parse_json_fallback_on_invalid_json(self) -> None:
        """Test returning fallback on invalid JSON."""
        mock_k8s = AsyncMock()
        mock_k8s.list = AsyncMock(
            return_value=[
                {
                    "metadata": {"name": "keystone-client-12345"},
                    "status": {"phase": "Running"},
                }
            ]
        )

        adapter = OpenStackAdapter(mock_k8s)
        await adapter.connect()

        with patch.object(adapter, "_execute_openstack_command", return_value="not valid json"):
            result = await adapter._execute_and_parse_json(
                ["server", "list"],
                operation="list_servers",
                fallback=[],
            )

            assert result == []

    @pytest.mark.asyncio
    async def test_parse_json_empty_result(self) -> None:
        """Test returning fallback on empty result."""
        mock_k8s = AsyncMock()
        mock_k8s.list = AsyncMock(
            return_value=[
                {
                    "metadata": {"name": "keystone-client-12345"},
                    "status": {"phase": "Running"},
                }
            ]
        )

        adapter = OpenStackAdapter(mock_k8s)
        await adapter.connect()

        with patch.object(adapter, "_execute_openstack_command", return_value=""):
            result = await adapter._execute_and_parse_json(
                ["server", "list"],
                operation="list_servers",
                fallback=[],
            )

            assert result == []


# =============================================================================
# OpenStackAdapter Class Attributes Tests
# =============================================================================


class TestOpenStackAdapterClassAttributes:
    """Tests for class attributes."""

    def test_default_namespace(self) -> None:
        """Test default namespace is defined."""
        assert OpenStackAdapter.DEFAULT_NAMESPACE == "openstack"

    def test_default_client_label(self) -> None:
        """Test default client label is defined."""
        assert OpenStackAdapter.DEFAULT_CLIENT_LABEL == "application=keystone,component=client"
