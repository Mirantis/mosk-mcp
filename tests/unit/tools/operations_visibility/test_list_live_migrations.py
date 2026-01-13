"""Unit tests for list_live_migrations tool."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from mosk_mcp.adapters.openstack import MigrationStatus as OSMigrationStatus
from mosk_mcp.adapters.openstack import MigrationType, ServerMigration
from mosk_mcp.core.exceptions import ToolExecutionError
from mosk_mcp.tools.operations_visibility.list_live_migrations import (
    _os_status_to_model_status,
    _parse_migration,
    list_live_migrations,
)
from mosk_mcp.tools.operations_visibility.models import (
    ListLiveMigrationsInput,
    MigrationStatus,
)


# =============================================================================
# Tests for helper functions
# =============================================================================


class TestOsStatusToModelStatus:
    """Tests for _os_status_to_model_status helper function."""

    def test_queued_status(self) -> None:
        """Test QUEUED status mapping."""
        result = _os_status_to_model_status(OSMigrationStatus.QUEUED)
        assert result == MigrationStatus.QUEUED

    def test_preparing_status(self) -> None:
        """Test PREPARING status mapping."""
        result = _os_status_to_model_status(OSMigrationStatus.PREPARING)
        assert result == MigrationStatus.PREPARING

    def test_running_status(self) -> None:
        """Test RUNNING status mapping."""
        result = _os_status_to_model_status(OSMigrationStatus.RUNNING)
        assert result == MigrationStatus.RUNNING

    def test_post_migrating_status(self) -> None:
        """Test POST_MIGRATING status mapping."""
        result = _os_status_to_model_status(OSMigrationStatus.POST_MIGRATING)
        assert result == MigrationStatus.POST_MIGRATING

    def test_completed_status(self) -> None:
        """Test COMPLETED status mapping."""
        result = _os_status_to_model_status(OSMigrationStatus.COMPLETED)
        assert result == MigrationStatus.COMPLETED

    def test_failed_status(self) -> None:
        """Test FAILED status mapping."""
        result = _os_status_to_model_status(OSMigrationStatus.FAILED)
        assert result == MigrationStatus.FAILED

    def test_cancelled_status(self) -> None:
        """Test CANCELLED status mapping."""
        result = _os_status_to_model_status(OSMigrationStatus.CANCELLED)
        assert result == MigrationStatus.CANCELLED

    def test_error_status(self) -> None:
        """Test ERROR status mapping."""
        result = _os_status_to_model_status(OSMigrationStatus.ERROR)
        assert result == MigrationStatus.ERROR

    def test_accepted_maps_to_queued(self) -> None:
        """Test ACCEPTED maps to QUEUED."""
        result = _os_status_to_model_status(OSMigrationStatus.ACCEPTED)
        assert result == MigrationStatus.QUEUED

    def test_pre_migrating_maps_to_preparing(self) -> None:
        """Test PRE_MIGRATING maps to PREPARING."""
        result = _os_status_to_model_status(OSMigrationStatus.PRE_MIGRATING)
        assert result == MigrationStatus.PREPARING


class TestParseMigration:
    """Tests for _parse_migration helper function."""

    def test_parse_running_migration(self) -> None:
        """Test parsing running migration."""
        migration = ServerMigration(
            id="mig-123",
            server_uuid="vm-456",
            server_name="test-vm",
            source_compute="compute-01",
            dest_compute="compute-02",
            status=OSMigrationStatus.RUNNING,
            migration_type=MigrationType.LIVE_MIGRATION,
            memory_total_bytes=1024 * 1024 * 100,
            memory_processed_bytes=1024 * 1024 * 50,
            memory_remaining_bytes=1024 * 1024 * 50,
            disk_total_bytes=1024 * 1024 * 1024,
            disk_processed_bytes=1024 * 1024 * 512,
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
        assert result.progress_percent > 0
        assert result.progress_percent < 100

    def test_parse_migration_zero_bytes(self) -> None:
        """Test parsing migration with zero bytes."""
        migration = ServerMigration(
            id="mig-123",
            server_uuid="vm-456",
            server_name="test-vm",
            source_compute="compute-01",
            dest_compute="compute-02",
            status=OSMigrationStatus.QUEUED,
            migration_type=MigrationType.LIVE_MIGRATION,
            memory_total_bytes=0,
            memory_processed_bytes=0,
            memory_remaining_bytes=0,
            disk_total_bytes=0,
            disk_processed_bytes=0,
            disk_remaining_bytes=0,
        )

        result = _parse_migration(migration)

        assert result.progress_percent == 0

    def test_parse_migration_no_target_host(self) -> None:
        """Test parsing migration without target host."""
        migration = ServerMigration(
            id="mig-123",
            server_uuid="vm-456",
            server_name="test-vm",
            source_compute="compute-01",
            dest_compute=None,
            status=OSMigrationStatus.QUEUED,
            migration_type=MigrationType.LIVE_MIGRATION,
            memory_total_bytes=0,
            memory_processed_bytes=0,
            disk_total_bytes=0,
            disk_processed_bytes=0,
        )

        result = _parse_migration(migration)

        assert result.target_host is None

    def test_parse_migration_completed(self) -> None:
        """Test parsing completed migration."""
        migration = ServerMigration(
            id="mig-123",
            server_uuid="vm-456",
            server_name="test-vm",
            source_compute="compute-01",
            dest_compute="compute-02",
            status=OSMigrationStatus.COMPLETED,
            migration_type=MigrationType.LIVE_MIGRATION,
            memory_total_bytes=100,
            memory_processed_bytes=100,
            disk_total_bytes=100,
            disk_processed_bytes=100,
        )

        result = _parse_migration(migration)

        assert result.status == MigrationStatus.COMPLETED
        assert result.progress_percent == 100


# =============================================================================
# Tests for model validation
# =============================================================================


class TestListLiveMigrationsInput:
    """Tests for ListLiveMigrationsInput model."""

    def test_defaults(self) -> None:
        """Test default values."""
        input_data = ListLiveMigrationsInput()

        assert input_data.source_host is None
        assert input_data.target_host is None
        assert input_data.status_filter is None
        assert input_data.limit == 50
        assert input_data.include_completed is False

    def test_custom_values(self) -> None:
        """Test custom values."""
        input_data = ListLiveMigrationsInput(
            source_host="compute-01",
            target_host="compute-02",
            status_filter=MigrationStatus.RUNNING,
            limit=100,
            include_completed=True,
        )

        assert input_data.source_host == "compute-01"
        assert input_data.target_host == "compute-02"
        assert input_data.status_filter == MigrationStatus.RUNNING
        assert input_data.limit == 100
        assert input_data.include_completed is True


# =============================================================================
# Tests for list_live_migrations function
# =============================================================================


class TestListLiveMigrations:
    """Tests for list_live_migrations function."""

    @pytest.fixture
    def mock_k8s_adapter(self) -> AsyncMock:
        """Create mock Kubernetes adapter."""
        adapter = AsyncMock()
        adapter.connect = AsyncMock()
        return adapter

    @pytest.fixture
    def mock_migrations(self) -> list[ServerMigration]:
        """Create mock migrations for testing."""
        return [
            ServerMigration(
                id="mig-1",
                server_uuid="vm-1",
                server_name="test-vm-1",
                source_compute="compute-01",
                dest_compute="compute-02",
                status=OSMigrationStatus.RUNNING,
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
                status=OSMigrationStatus.COMPLETED,
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
                status=OSMigrationStatus.FAILED,
                migration_type=MigrationType.LIVE_MIGRATION,
                memory_total_bytes=0,
                memory_processed_bytes=0,
                disk_total_bytes=0,
                disk_processed_bytes=0,
            ),
            ServerMigration(
                id="mig-4",
                server_uuid="vm-4",
                server_name="test-vm-4",
                source_compute="compute-03",
                dest_compute=None,
                status=OSMigrationStatus.QUEUED,
                migration_type=MigrationType.LIVE_MIGRATION,
                memory_total_bytes=0,
                memory_processed_bytes=0,
                disk_total_bytes=0,
                disk_processed_bytes=0,
            ),
        ]

    @pytest.mark.asyncio
    async def test_list_migrations_success(
        self, mock_k8s_adapter: AsyncMock, mock_migrations: list[ServerMigration]
    ) -> None:
        """Test successful migration listing."""
        mock_os_adapter = AsyncMock()
        mock_os_adapter.list_migrations = AsyncMock(return_value=mock_migrations)
        mock_os_adapter.__aenter__ = AsyncMock(return_value=mock_os_adapter)
        mock_os_adapter.__aexit__ = AsyncMock()

        with patch(
            "mosk_mcp.tools.operations_visibility.list_live_migrations.OpenStackAdapter",
            return_value=mock_os_adapter,
        ):
            result = await list_live_migrations(
                mock_k8s_adapter,
                ListLiveMigrationsInput(include_completed=True),
            )

        assert result.total_count == 4
        assert result.active_count == 1  # 1 RUNNING
        assert result.queued_count == 1
        assert result.completed_count == 1
        assert result.failed_count == 1

    @pytest.mark.asyncio
    async def test_list_migrations_exclude_completed(
        self, mock_k8s_adapter: AsyncMock, mock_migrations: list[ServerMigration]
    ) -> None:
        """Test excluding completed migrations."""
        # Filter to only active migrations
        active_migrations = [
            m
            for m in mock_migrations
            if m.status
            not in (
                OSMigrationStatus.COMPLETED,
                OSMigrationStatus.FAILED,
                OSMigrationStatus.CANCELLED,
            )
        ]

        mock_os_adapter = AsyncMock()
        mock_os_adapter.list_migrations = AsyncMock(return_value=active_migrations)
        mock_os_adapter.__aenter__ = AsyncMock(return_value=mock_os_adapter)
        mock_os_adapter.__aexit__ = AsyncMock()

        with patch(
            "mosk_mcp.tools.operations_visibility.list_live_migrations.OpenStackAdapter",
            return_value=mock_os_adapter,
        ):
            result = await list_live_migrations(
                mock_k8s_adapter,
                ListLiveMigrationsInput(include_completed=False),
            )

        assert result.total_count == 2  # 1 RUNNING + 1 QUEUED
        assert result.completed_count == 0
        assert result.failed_count == 0

    @pytest.mark.asyncio
    async def test_list_migrations_with_source_filter(self, mock_k8s_adapter: AsyncMock) -> None:
        """Test filtering by source host."""
        mock_os_adapter = AsyncMock()
        mock_os_adapter.list_migrations = AsyncMock(return_value=[])
        mock_os_adapter.__aenter__ = AsyncMock(return_value=mock_os_adapter)
        mock_os_adapter.__aexit__ = AsyncMock()

        with patch(
            "mosk_mcp.tools.operations_visibility.list_live_migrations.OpenStackAdapter",
            return_value=mock_os_adapter,
        ):
            await list_live_migrations(
                mock_k8s_adapter,
                ListLiveMigrationsInput(source_host="compute-01"),
            )

        mock_os_adapter.list_migrations.assert_called_once()
        call_kwargs = mock_os_adapter.list_migrations.call_args[1]
        assert call_kwargs["source_compute"] == "compute-01"

    @pytest.mark.asyncio
    async def test_list_migrations_with_target_filter(self, mock_k8s_adapter: AsyncMock) -> None:
        """Test filtering by target host."""
        mock_os_adapter = AsyncMock()
        mock_os_adapter.list_migrations = AsyncMock(return_value=[])
        mock_os_adapter.__aenter__ = AsyncMock(return_value=mock_os_adapter)
        mock_os_adapter.__aexit__ = AsyncMock()

        with patch(
            "mosk_mcp.tools.operations_visibility.list_live_migrations.OpenStackAdapter",
            return_value=mock_os_adapter,
        ):
            await list_live_migrations(
                mock_k8s_adapter,
                ListLiveMigrationsInput(target_host="compute-02"),
            )

        mock_os_adapter.list_migrations.assert_called_once()
        call_kwargs = mock_os_adapter.list_migrations.call_args[1]
        assert call_kwargs["dest_compute"] == "compute-02"

    @pytest.mark.asyncio
    async def test_list_migrations_with_status_filter(self, mock_k8s_adapter: AsyncMock) -> None:
        """Test filtering by status."""
        mock_os_adapter = AsyncMock()
        mock_os_adapter.list_migrations = AsyncMock(return_value=[])
        mock_os_adapter.__aenter__ = AsyncMock(return_value=mock_os_adapter)
        mock_os_adapter.__aexit__ = AsyncMock()

        with patch(
            "mosk_mcp.tools.operations_visibility.list_live_migrations.OpenStackAdapter",
            return_value=mock_os_adapter,
        ):
            await list_live_migrations(
                mock_k8s_adapter,
                ListLiveMigrationsInput(status_filter=MigrationStatus.RUNNING),
            )

        mock_os_adapter.list_migrations.assert_called_once()
        call_kwargs = mock_os_adapter.list_migrations.call_args[1]
        assert call_kwargs["status"] == OSMigrationStatus.RUNNING

    @pytest.mark.asyncio
    async def test_list_migrations_host_counts(
        self, mock_k8s_adapter: AsyncMock, mock_migrations: list[ServerMigration]
    ) -> None:
        """Test by_source_host and by_target_host counts."""
        mock_os_adapter = AsyncMock()
        mock_os_adapter.list_migrations = AsyncMock(return_value=mock_migrations)
        mock_os_adapter.__aenter__ = AsyncMock(return_value=mock_os_adapter)
        mock_os_adapter.__aexit__ = AsyncMock()

        with patch(
            "mosk_mcp.tools.operations_visibility.list_live_migrations.OpenStackAdapter",
            return_value=mock_os_adapter,
        ):
            result = await list_live_migrations(
                mock_k8s_adapter,
                ListLiveMigrationsInput(include_completed=True),
            )

        # compute-01 is source for 2 migrations
        assert result.by_source_host.get("compute-01") == 2
        # compute-02 is target for 1 migration
        assert result.by_target_host.get("compute-02") == 1

    @pytest.mark.asyncio
    async def test_list_migrations_empty_result(self, mock_k8s_adapter: AsyncMock) -> None:
        """Test with no migrations."""
        mock_os_adapter = AsyncMock()
        mock_os_adapter.list_migrations = AsyncMock(return_value=[])
        mock_os_adapter.__aenter__ = AsyncMock(return_value=mock_os_adapter)
        mock_os_adapter.__aexit__ = AsyncMock()

        with patch(
            "mosk_mcp.tools.operations_visibility.list_live_migrations.OpenStackAdapter",
            return_value=mock_os_adapter,
        ):
            result = await list_live_migrations(
                mock_k8s_adapter,
                ListLiveMigrationsInput(),
            )

        assert result.total_count == 0
        assert result.migrations == []
        assert result.by_source_host == {}
        assert result.by_target_host == {}

    @pytest.mark.asyncio
    async def test_list_migrations_api_error(self, mock_k8s_adapter: AsyncMock) -> None:
        """Test API error handling."""
        mock_os_adapter = AsyncMock()
        mock_os_adapter.list_migrations = AsyncMock(side_effect=Exception("API connection failed"))
        mock_os_adapter.__aenter__ = AsyncMock(return_value=mock_os_adapter)
        mock_os_adapter.__aexit__ = AsyncMock()

        with patch(
            "mosk_mcp.tools.operations_visibility.list_live_migrations.OpenStackAdapter",
            return_value=mock_os_adapter,
        ):
            with pytest.raises(ToolExecutionError) as exc_info:
                await list_live_migrations(
                    mock_k8s_adapter,
                    ListLiveMigrationsInput(),
                )

        assert "Failed to list live migrations" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_list_migrations_timestamp_included(self, mock_k8s_adapter: AsyncMock) -> None:
        """Test timestamp is included."""
        mock_os_adapter = AsyncMock()
        mock_os_adapter.list_migrations = AsyncMock(return_value=[])
        mock_os_adapter.__aenter__ = AsyncMock(return_value=mock_os_adapter)
        mock_os_adapter.__aexit__ = AsyncMock()

        with patch(
            "mosk_mcp.tools.operations_visibility.list_live_migrations.OpenStackAdapter",
            return_value=mock_os_adapter,
        ):
            result = await list_live_migrations(
                mock_k8s_adapter,
                ListLiveMigrationsInput(),
            )

        assert result.timestamp is not None
        assert len(result.timestamp) > 0

    @pytest.mark.asyncio
    async def test_list_migrations_preparing_status(self, mock_k8s_adapter: AsyncMock) -> None:
        """Test migration in preparing status counted as active."""
        migration = ServerMigration(
            id="mig-1",
            server_uuid="vm-1",
            server_name="test-vm",
            source_compute="compute-01",
            dest_compute="compute-02",
            status=OSMigrationStatus.PREPARING,
            migration_type=MigrationType.LIVE_MIGRATION,
            memory_total_bytes=0,
            memory_processed_bytes=0,
            disk_total_bytes=0,
            disk_processed_bytes=0,
        )

        mock_os_adapter = AsyncMock()
        mock_os_adapter.list_migrations = AsyncMock(return_value=[migration])
        mock_os_adapter.__aenter__ = AsyncMock(return_value=mock_os_adapter)
        mock_os_adapter.__aexit__ = AsyncMock()

        with patch(
            "mosk_mcp.tools.operations_visibility.list_live_migrations.OpenStackAdapter",
            return_value=mock_os_adapter,
        ):
            result = await list_live_migrations(
                mock_k8s_adapter,
                ListLiveMigrationsInput(include_completed=True),
            )

        assert result.active_count == 1

    @pytest.mark.asyncio
    async def test_list_migrations_post_migrating_status(self, mock_k8s_adapter: AsyncMock) -> None:
        """Test migration in post_migrating status counted as active."""
        migration = ServerMigration(
            id="mig-1",
            server_uuid="vm-1",
            server_name="test-vm",
            source_compute="compute-01",
            dest_compute="compute-02",
            status=OSMigrationStatus.POST_MIGRATING,
            migration_type=MigrationType.LIVE_MIGRATION,
            memory_total_bytes=0,
            memory_processed_bytes=0,
            disk_total_bytes=0,
            disk_processed_bytes=0,
        )

        mock_os_adapter = AsyncMock()
        mock_os_adapter.list_migrations = AsyncMock(return_value=[migration])
        mock_os_adapter.__aenter__ = AsyncMock(return_value=mock_os_adapter)
        mock_os_adapter.__aexit__ = AsyncMock()

        with patch(
            "mosk_mcp.tools.operations_visibility.list_live_migrations.OpenStackAdapter",
            return_value=mock_os_adapter,
        ):
            result = await list_live_migrations(
                mock_k8s_adapter,
                ListLiveMigrationsInput(include_completed=True),
            )

        assert result.active_count == 1
