"""Unit tests for get_migration_status tool."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from mosk_mcp.adapters.openstack import MigrationType, ServerMigration
from mosk_mcp.tools.common.enums import MigrationStatus
from mosk_mcp.tools.node_lifecycle.get_migration_status import (
    GetMigrationStatusInput,
    GetMigrationStatusOutput,
    MigrationInfo,
    _parse_migration,
    get_migration_status,
)


# Note: _get_migrations_from_nova and get_migration_status require
# full OpenStack integration mocking. Testing these requires integration tests
# with actual OpenStack cluster or comprehensive mocking of OpenStackAdapter.
# The following tests cover model validation and input/output structure.


class TestGetMigrationStatusInput:
    """Tests for GetMigrationStatusInput validation."""

    def test_all_optional(self):
        """Test all fields are optional."""
        input_data = GetMigrationStatusInput()

        assert input_data.migration_id is None
        assert input_data.vm_id is None
        assert input_data.source_host is None
        assert input_data.target_host is None
        assert input_data.status_filter is None

    def test_default_values(self):
        """Test default values."""
        input_data = GetMigrationStatusInput()

        assert input_data.limit == 50
        assert input_data.include_completed is True

    def test_limit_bounds(self):
        """Test limit bounds."""
        # Valid
        input_data = GetMigrationStatusInput(limit=1)
        assert input_data.limit == 1

        input_data = GetMigrationStatusInput(limit=200)
        assert input_data.limit == 200

        # Invalid
        with pytest.raises(ValueError):
            GetMigrationStatusInput(limit=0)

        with pytest.raises(ValueError):
            GetMigrationStatusInput(limit=201)

    def test_status_filter(self):
        """Test status filter."""
        input_data = GetMigrationStatusInput(
            status_filter=MigrationStatus.RUNNING,
        )

        assert input_data.status_filter == MigrationStatus.RUNNING


class TestMigrationInfo:
    """Tests for MigrationInfo model."""

    def test_required_fields(self):
        """Test required fields."""
        info = MigrationInfo(
            migration_id="mig-123",
            vm_id="vm-456",
            source_host="compute-01",
            status=MigrationStatus.RUNNING,
        )

        assert info.migration_id == "mig-123"
        assert info.vm_id == "vm-456"
        assert info.source_host == "compute-01"
        assert info.status == MigrationStatus.RUNNING

    def test_optional_fields_defaults(self):
        """Test optional fields have defaults."""
        info = MigrationInfo(
            migration_id="mig-123",
            vm_id="vm-456",
            source_host="compute-01",
            status=MigrationStatus.RUNNING,
        )

        assert info.vm_name == "unknown"
        assert info.target_host is None
        assert info.migration_type == "live-migration"
        assert info.progress_percent == 0
        assert info.memory_total_bytes is None
        assert info.memory_remaining_bytes is None
        assert info.disk_total_bytes is None
        assert info.disk_remaining_bytes is None
        assert info.created_at is None
        assert info.updated_at is None
        assert info.error_message is None
        assert info.details == {}

    def test_progress_bounds(self):
        """Test progress percentage bounds."""
        # Valid
        info = MigrationInfo(
            migration_id="mig-123",
            vm_id="vm-456",
            source_host="compute-01",
            status=MigrationStatus.RUNNING,
            progress_percent=50,
        )
        assert info.progress_percent == 50

        # Invalid
        with pytest.raises(ValueError):
            MigrationInfo(
                migration_id="mig-123",
                vm_id="vm-456",
                source_host="compute-01",
                status=MigrationStatus.RUNNING,
                progress_percent=-1,
            )

        with pytest.raises(ValueError):
            MigrationInfo(
                migration_id="mig-123",
                vm_id="vm-456",
                source_host="compute-01",
                status=MigrationStatus.RUNNING,
                progress_percent=101,
            )


class TestMigrationStatus:
    """Tests for MigrationStatus enum."""

    def test_all_statuses(self):
        """Test all statuses are defined."""
        statuses = list(MigrationStatus)

        assert MigrationStatus.QUEUED in statuses
        assert MigrationStatus.PREPARING in statuses
        assert MigrationStatus.RUNNING in statuses
        assert MigrationStatus.POST_MIGRATING in statuses
        assert MigrationStatus.COMPLETED in statuses
        assert MigrationStatus.FAILED in statuses
        assert MigrationStatus.CANCELLED in statuses
        assert MigrationStatus.ERROR in statuses
        assert MigrationStatus.UNKNOWN in statuses


class TestGetMigrationStatusOutput:
    """Tests for GetMigrationStatusOutput model."""

    def test_required_fields(self):
        """Test required fields."""
        output = GetMigrationStatusOutput(
            total_count=5,
            message="Found 5 migrations",
        )

        assert output.total_count == 5
        assert output.message == "Found 5 migrations"

    def test_optional_fields_defaults(self):
        """Test optional fields have defaults."""
        output = GetMigrationStatusOutput(
            total_count=0,
            message="No migrations",
        )

        assert output.migrations == []
        assert output.active_count == 0
        assert output.completed_count == 0
        assert output.failed_count == 0
        assert output.summary == {}
        assert output.simulated is False


# =============================================================================
# Extended Tests with Function Testing
# =============================================================================


class TestParseMigration:
    """Tests for _parse_migration helper function."""

    def test_parse_basic_migration(self):
        """Test parsing a basic migration."""
        migration = ServerMigration(
            id="mig-123",
            server_uuid="vm-456",
            server_name="test-vm",
            source_compute="compute-01",
            dest_compute="compute-02",
            status=MigrationStatus.RUNNING,
            migration_type=MigrationType.LIVE_MIGRATION,
            memory_total_bytes=1024 * 1024 * 100,  # 100 MB
            memory_processed_bytes=1024 * 1024 * 50,  # 50 MB
            memory_remaining_bytes=1024 * 1024 * 50,
            disk_total_bytes=1024 * 1024 * 1024,  # 1 GB
            disk_processed_bytes=1024 * 1024 * 512,  # 512 MB
            disk_remaining_bytes=1024 * 1024 * 512,
            created_at=datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC),
            updated_at=datetime(2024, 1, 1, 12, 5, 0, tzinfo=UTC),
        )

        result = _parse_migration(migration)

        assert result.migration_id == "mig-123"
        assert result.vm_id == "vm-456"
        assert result.vm_name == "test-vm"
        assert result.source_host == "compute-01"
        assert result.target_host == "compute-02"
        assert result.status == MigrationStatus.RUNNING
        assert result.migration_type == "live-migration"
        # Progress should be calculated from total vs processed
        assert result.progress_percent >= 0
        assert result.progress_percent <= 100

    def test_parse_migration_status_mapping(self):
        """Test that ACCEPTED maps to QUEUED and PRE_MIGRATING maps to PREPARING."""
        # Test ACCEPTED -> QUEUED
        migration_accepted = ServerMigration(
            id="mig-1",
            server_uuid="vm-1",
            server_name="test-vm-1",
            source_compute="compute-01",
            dest_compute="compute-02",
            status=MigrationStatus.ACCEPTED,
            migration_type=MigrationType.LIVE_MIGRATION,
        )
        result = _parse_migration(migration_accepted)
        assert result.status == MigrationStatus.QUEUED

        # Test PRE_MIGRATING -> PREPARING
        migration_pre = ServerMigration(
            id="mig-2",
            server_uuid="vm-2",
            server_name="test-vm-2",
            source_compute="compute-01",
            dest_compute="compute-03",
            status=MigrationStatus.PRE_MIGRATING,
            migration_type=MigrationType.LIVE_MIGRATION,
        )
        result = _parse_migration(migration_pre)
        assert result.status == MigrationStatus.PREPARING

    def test_parse_migration_zero_bytes(self):
        """Test progress calculation with zero bytes."""
        migration = ServerMigration(
            id="mig-123",
            server_uuid="vm-456",
            server_name="test-vm",
            source_compute="compute-01",
            dest_compute="compute-02",
            status=MigrationStatus.RUNNING,
            migration_type=MigrationType.LIVE_MIGRATION,
            memory_total_bytes=0,
            memory_processed_bytes=0,
            disk_total_bytes=0,
            disk_processed_bytes=0,
        )

        result = _parse_migration(migration)
        assert result.progress_percent == 0

    def test_parse_migration_completed(self):
        """Test parsing completed migration."""
        migration = ServerMigration(
            id="mig-123",
            server_uuid="vm-456",
            server_name="test-vm",
            source_compute="compute-01",
            dest_compute="compute-02",
            status=MigrationStatus.COMPLETED,
            migration_type=MigrationType.LIVE_MIGRATION,
            memory_total_bytes=100,
            memory_processed_bytes=100,
            disk_total_bytes=100,
            disk_processed_bytes=100,
        )

        result = _parse_migration(migration)
        assert result.status == MigrationStatus.COMPLETED
        assert result.progress_percent == 100


class TestGetMigrationStatusFunction:
    """Tests for the get_migration_status function."""

    @pytest.fixture
    def mock_k8s_adapter(self):
        """Create a mock Kubernetes adapter."""
        adapter = AsyncMock()
        adapter.connect = AsyncMock()
        adapter.list = AsyncMock(return_value=[])
        adapter.get = AsyncMock()
        return adapter

    @pytest.fixture
    def mock_migrations(self):
        """Create mock migrations for testing."""
        return [
            ServerMigration(
                id="mig-1",
                server_uuid="vm-1",
                server_name="test-vm-1",
                source_compute="compute-01",
                dest_compute="compute-02",
                status=MigrationStatus.RUNNING,
                migration_type=MigrationType.LIVE_MIGRATION,
                memory_total_bytes=1000,
                memory_processed_bytes=500,
                disk_total_bytes=0,
                disk_processed_bytes=0,
            ),
            ServerMigration(
                id="mig-2",
                server_uuid="vm-2",
                server_name="test-vm-2",
                source_compute="compute-01",
                dest_compute="compute-03",
                status=MigrationStatus.COMPLETED,
                migration_type=MigrationType.LIVE_MIGRATION,
                memory_total_bytes=1000,
                memory_processed_bytes=1000,
                disk_total_bytes=0,
                disk_processed_bytes=0,
            ),
            ServerMigration(
                id="mig-3",
                server_uuid="vm-3",
                server_name="test-vm-3",
                source_compute="compute-02",
                dest_compute="compute-01",
                status=MigrationStatus.FAILED,
                migration_type=MigrationType.LIVE_MIGRATION,
                memory_total_bytes=0,
                memory_processed_bytes=0,
                disk_total_bytes=0,
                disk_processed_bytes=0,
            ),
        ]

    @pytest.mark.asyncio
    async def test_get_migration_status_success(self, mock_k8s_adapter, mock_migrations):
        """Test successful migration status retrieval."""
        # Mock OpenStackAdapter
        mock_os_adapter = AsyncMock()
        mock_os_adapter.list_migrations = AsyncMock(return_value=mock_migrations)
        mock_os_adapter.__aenter__ = AsyncMock(return_value=mock_os_adapter)
        mock_os_adapter.__aexit__ = AsyncMock()

        with patch(
            "mosk_mcp.tools.node_lifecycle.get_migration_status.OpenStackAdapter",
            return_value=mock_os_adapter,
        ):
            result = await get_migration_status(
                mock_k8s_adapter,
                GetMigrationStatusInput(),
            )

        assert result.total_count == 3
        assert result.active_count == 1  # 1 RUNNING
        assert result.completed_count == 1  # 1 COMPLETED
        assert result.failed_count == 1  # 1 FAILED
        assert result.simulated is False

    @pytest.mark.asyncio
    async def test_get_migration_status_with_source_filter(self, mock_k8s_adapter, mock_migrations):
        """Test migration status with source host filter."""
        # Filter to only migrations from compute-01
        filtered_migrations = [m for m in mock_migrations if m.source_compute == "compute-01"]

        mock_os_adapter = AsyncMock()
        mock_os_adapter.list_migrations = AsyncMock(return_value=filtered_migrations)
        mock_os_adapter.__aenter__ = AsyncMock(return_value=mock_os_adapter)
        mock_os_adapter.__aexit__ = AsyncMock()

        with patch(
            "mosk_mcp.tools.node_lifecycle.get_migration_status.OpenStackAdapter",
            return_value=mock_os_adapter,
        ):
            result = await get_migration_status(
                mock_k8s_adapter,
                GetMigrationStatusInput(source_host="compute-01"),
            )

        assert result.total_count == 2
        mock_os_adapter.list_migrations.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_migration_status_empty_result(self, mock_k8s_adapter):
        """Test migration status with no migrations."""
        mock_os_adapter = AsyncMock()
        mock_os_adapter.list_migrations = AsyncMock(return_value=[])
        mock_os_adapter.__aenter__ = AsyncMock(return_value=mock_os_adapter)
        mock_os_adapter.__aexit__ = AsyncMock()

        with patch(
            "mosk_mcp.tools.node_lifecycle.get_migration_status.OpenStackAdapter",
            return_value=mock_os_adapter,
        ):
            result = await get_migration_status(
                mock_k8s_adapter,
                GetMigrationStatusInput(),
            )

        assert result.total_count == 0
        assert result.migrations == []
        assert "0 migration" in result.message

    @pytest.mark.asyncio
    async def test_get_migration_status_api_error(self, mock_k8s_adapter):
        """Test migration status with API error."""
        from mosk_mcp.core.exceptions import KubernetesError

        mock_os_adapter = AsyncMock()
        mock_os_adapter.list_migrations = AsyncMock(side_effect=Exception("API connection failed"))
        mock_os_adapter.__aenter__ = AsyncMock(return_value=mock_os_adapter)
        mock_os_adapter.__aexit__ = AsyncMock()

        with patch(
            "mosk_mcp.tools.node_lifecycle.get_migration_status.OpenStackAdapter",
            return_value=mock_os_adapter,
        ):
            with pytest.raises(KubernetesError) as exc_info:
                await get_migration_status(
                    mock_k8s_adapter,
                    GetMigrationStatusInput(),
                )

        assert "Failed to get migration status" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_get_migration_status_exclude_completed(self, mock_k8s_adapter, mock_migrations):
        """Test migration status excluding completed migrations."""
        mock_os_adapter = AsyncMock()
        mock_os_adapter.list_migrations = AsyncMock(return_value=mock_migrations)
        mock_os_adapter.__aenter__ = AsyncMock(return_value=mock_os_adapter)
        mock_os_adapter.__aexit__ = AsyncMock()

        with patch(
            "mosk_mcp.tools.node_lifecycle.get_migration_status.OpenStackAdapter",
            return_value=mock_os_adapter,
        ):
            result = await get_migration_status(
                mock_k8s_adapter,
                GetMigrationStatusInput(include_completed=False),
            )

        # Should only include RUNNING (active) migrations
        assert result.total_count == 1
        assert all(
            m.status
            not in (MigrationStatus.COMPLETED, MigrationStatus.FAILED, MigrationStatus.CANCELLED)
            for m in result.migrations
        )

    @pytest.mark.asyncio
    async def test_get_migration_status_summary(self, mock_k8s_adapter, mock_migrations):
        """Test migration status summary statistics."""
        mock_os_adapter = AsyncMock()
        mock_os_adapter.list_migrations = AsyncMock(return_value=mock_migrations)
        mock_os_adapter.__aenter__ = AsyncMock(return_value=mock_os_adapter)
        mock_os_adapter.__aexit__ = AsyncMock()

        with patch(
            "mosk_mcp.tools.node_lifecycle.get_migration_status.OpenStackAdapter",
            return_value=mock_os_adapter,
        ):
            result = await get_migration_status(
                mock_k8s_adapter,
                GetMigrationStatusInput(),
            )

        assert "by_status" in result.summary
        assert result.summary["total"] == 3
        assert result.summary["active"] == 1
        assert result.summary["completed"] == 1
        assert result.summary["failed"] == 1
