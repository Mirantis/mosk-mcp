"""Unit tests for get_migration_eta tool."""

from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest

from mosk_mcp.adapters.openstack import MigrationStatus as OSMigrationStatus
from mosk_mcp.adapters.openstack import MigrationType, ServerMigration
from mosk_mcp.core.exceptions import ToolExecutionError
from mosk_mcp.tools.operations_visibility.get_migration_eta import (
    _calculate_eta_for_migration,
    _os_status_to_model_status,
    get_migration_eta,
)
from mosk_mcp.tools.operations_visibility.models import (
    GetMigrationETAInput,
    MigrationStatus,
)


class TestOsStatusToModelStatus:
    """Tests for _os_status_to_model_status helper."""

    def test_queued_mapping(self):
        """Test QUEUED status mapping."""
        result = _os_status_to_model_status(OSMigrationStatus.QUEUED)
        assert result == MigrationStatus.QUEUED

    def test_preparing_mapping(self):
        """Test PREPARING status mapping."""
        result = _os_status_to_model_status(OSMigrationStatus.PREPARING)
        assert result == MigrationStatus.PREPARING

    def test_running_mapping(self):
        """Test RUNNING status mapping."""
        result = _os_status_to_model_status(OSMigrationStatus.RUNNING)
        assert result == MigrationStatus.RUNNING

    def test_post_migrating_mapping(self):
        """Test POST_MIGRATING status mapping."""
        result = _os_status_to_model_status(OSMigrationStatus.POST_MIGRATING)
        assert result == MigrationStatus.POST_MIGRATING

    def test_completed_mapping(self):
        """Test COMPLETED status mapping."""
        result = _os_status_to_model_status(OSMigrationStatus.COMPLETED)
        assert result == MigrationStatus.COMPLETED

    def test_failed_mapping(self):
        """Test FAILED status mapping."""
        result = _os_status_to_model_status(OSMigrationStatus.FAILED)
        assert result == MigrationStatus.FAILED

    def test_cancelled_mapping(self):
        """Test CANCELLED status mapping."""
        result = _os_status_to_model_status(OSMigrationStatus.CANCELLED)
        assert result == MigrationStatus.CANCELLED

    def test_error_mapping(self):
        """Test ERROR status mapping."""
        result = _os_status_to_model_status(OSMigrationStatus.ERROR)
        assert result == MigrationStatus.ERROR

    def test_accepted_maps_to_queued(self):
        """Test ACCEPTED maps to QUEUED."""
        result = _os_status_to_model_status(OSMigrationStatus.ACCEPTED)
        assert result == MigrationStatus.QUEUED

    def test_pre_migrating_maps_to_preparing(self):
        """Test PRE_MIGRATING maps to PREPARING."""
        result = _os_status_to_model_status(OSMigrationStatus.PRE_MIGRATING)
        assert result == MigrationStatus.PREPARING


class TestCalculateEtaForMigration:
    """Tests for _calculate_eta_for_migration helper."""

    def test_running_migration_with_progress(self):
        """Test ETA calculation for running migration with progress."""
        migration = ServerMigration(
            id="mig-123",
            server_uuid="vm-456",
            server_name="test-vm",
            source_compute="compute-01",
            dest_compute="compute-02",
            status=OSMigrationStatus.RUNNING,
            migration_type=MigrationType.LIVE_MIGRATION,
            memory_total_bytes=1_000_000_000,  # 1 GB
            memory_processed_bytes=500_000_000,  # 500 MB
            memory_remaining_bytes=500_000_000,
            disk_total_bytes=0,
            disk_processed_bytes=0,
            disk_remaining_bytes=0,
        )

        result = _calculate_eta_for_migration(migration, average_rate_bps=100_000_000)

        assert result.vm_id == "vm-456"
        assert result.vm_name == "test-vm"
        assert result.status == MigrationStatus.RUNNING
        assert result.progress_percent == 50
        assert result.estimated_remaining_seconds is not None
        assert result.estimated_remaining_seconds > 0
        assert result.estimated_completion is not None

    def test_queued_migration(self):
        """Test ETA calculation for queued migration."""
        migration = ServerMigration(
            id="mig-123",
            server_uuid="vm-456",
            server_name="test-vm",
            source_compute="compute-01",
            dest_compute="compute-02",
            status=OSMigrationStatus.QUEUED,
            migration_type=MigrationType.LIVE_MIGRATION,
            memory_total_bytes=1_000_000_000,
            memory_processed_bytes=0,
            memory_remaining_bytes=1_000_000_000,
            disk_total_bytes=0,
            disk_processed_bytes=0,
            disk_remaining_bytes=0,
        )

        result = _calculate_eta_for_migration(migration, average_rate_bps=100_000_000)

        assert result.status == MigrationStatus.QUEUED
        assert result.progress_percent == 0
        # Should estimate based on total size for queued
        assert result.estimated_remaining_seconds is not None

    def test_completed_migration(self):
        """Test ETA calculation for completed migration."""
        migration = ServerMigration(
            id="mig-123",
            server_uuid="vm-456",
            server_name="test-vm",
            source_compute="compute-01",
            dest_compute="compute-02",
            status=OSMigrationStatus.COMPLETED,
            migration_type=MigrationType.LIVE_MIGRATION,
            memory_total_bytes=1_000_000_000,
            memory_processed_bytes=1_000_000_000,
            memory_remaining_bytes=0,
            disk_total_bytes=0,
            disk_processed_bytes=0,
            disk_remaining_bytes=0,
        )

        result = _calculate_eta_for_migration(migration, average_rate_bps=100_000_000)

        assert result.status == MigrationStatus.COMPLETED
        assert result.progress_percent == 100
        # No remaining time for completed
        assert result.estimated_remaining_seconds is None

    def test_zero_total_bytes(self):
        """Test ETA calculation with zero total bytes."""
        migration = ServerMigration(
            id="mig-123",
            server_uuid="vm-456",
            server_name="test-vm",
            source_compute="compute-01",
            dest_compute="compute-02",
            status=OSMigrationStatus.RUNNING,
            migration_type=MigrationType.LIVE_MIGRATION,
            memory_total_bytes=0,
            memory_processed_bytes=0,
            memory_remaining_bytes=0,
            disk_total_bytes=0,
            disk_processed_bytes=0,
            disk_remaining_bytes=0,
        )

        result = _calculate_eta_for_migration(migration, average_rate_bps=100_000_000)

        assert result.progress_percent == 0  # 0/0 = 0

    def test_default_transfer_rate(self):
        """Test using default transfer rate when none provided."""
        migration = ServerMigration(
            id="mig-123",
            server_uuid="vm-456",
            server_name="test-vm",
            source_compute="compute-01",
            dest_compute="compute-02",
            status=OSMigrationStatus.RUNNING,
            migration_type=MigrationType.LIVE_MIGRATION,
            memory_total_bytes=1_000_000_000,
            memory_processed_bytes=500_000_000,
            memory_remaining_bytes=500_000_000,
            disk_total_bytes=0,
            disk_processed_bytes=0,
            disk_remaining_bytes=0,
        )

        result = _calculate_eta_for_migration(migration, average_rate_bps=None)

        # Should use default rate (500 Mbps)
        assert result.transfer_rate_mbps == 500.0
        assert result.estimated_remaining_seconds is not None


class TestGetMigrationETAInput:
    """Tests for GetMigrationETAInput model."""

    def test_default_values(self):
        """Test default values."""
        input_data = GetMigrationETAInput()

        assert input_data.source_host is None
        assert input_data.include_per_vm is True

    def test_custom_values(self):
        """Test custom values."""
        input_data = GetMigrationETAInput(
            source_host="compute-01",
            include_per_vm=False,
        )

        assert input_data.source_host == "compute-01"
        assert input_data.include_per_vm is False


class TestGetMigrationETAFunction:
    """Tests for get_migration_eta function."""

    @pytest.fixture
    def mock_k8s_adapter(self):
        """Create mock Kubernetes adapter."""
        adapter = AsyncMock()
        adapter.connect = AsyncMock()
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
                status=OSMigrationStatus.RUNNING,
                migration_type=MigrationType.LIVE_MIGRATION,
                memory_total_bytes=1_000_000_000,
                memory_processed_bytes=500_000_000,
                memory_remaining_bytes=500_000_000,
                disk_total_bytes=0,
                disk_processed_bytes=0,
                disk_remaining_bytes=0,
            ),
            ServerMigration(
                id="mig-2",
                server_uuid="vm-2",
                server_name="test-vm-2",
                source_compute="compute-01",
                dest_compute="compute-03",
                status=OSMigrationStatus.QUEUED,
                migration_type=MigrationType.LIVE_MIGRATION,
                memory_total_bytes=2_000_000_000,
                memory_processed_bytes=0,
                memory_remaining_bytes=2_000_000_000,
                disk_total_bytes=0,
                disk_processed_bytes=0,
                disk_remaining_bytes=0,
            ),
        ]

    @pytest.mark.asyncio
    async def test_active_migrations(self, mock_k8s_adapter, mock_migrations):
        """Test ETA calculation with active migrations."""
        mock_os_adapter = AsyncMock()
        mock_os_adapter.list_migrations = AsyncMock(return_value=mock_migrations)
        mock_os_adapter.__aenter__ = AsyncMock(return_value=mock_os_adapter)
        mock_os_adapter.__aexit__ = AsyncMock()

        with patch(
            "mosk_mcp.tools.operations_visibility.get_migration_eta.OpenStackAdapter",
            return_value=mock_os_adapter,
        ):
            result = await get_migration_eta(
                mock_k8s_adapter,
                GetMigrationETAInput(),
            )

        assert result.has_active_migrations is True
        assert result.total_active == 1  # 1 RUNNING
        assert result.total_queued == 1  # 1 QUEUED
        assert result.overall_progress_percent >= 0
        assert len(result.per_vm_eta) == 2
        assert result.timestamp is not None

    @pytest.mark.asyncio
    async def test_no_active_migrations(self, mock_k8s_adapter):
        """Test when no active migrations."""
        mock_os_adapter = AsyncMock()
        mock_os_adapter.list_migrations = AsyncMock(return_value=[])
        mock_os_adapter.__aenter__ = AsyncMock(return_value=mock_os_adapter)
        mock_os_adapter.__aexit__ = AsyncMock()

        with patch(
            "mosk_mcp.tools.operations_visibility.get_migration_eta.OpenStackAdapter",
            return_value=mock_os_adapter,
        ):
            result = await get_migration_eta(
                mock_k8s_adapter,
                GetMigrationETAInput(),
            )

        assert result.has_active_migrations is False
        assert result.total_active == 0
        assert result.total_queued == 0
        assert result.overall_progress_percent == 100
        assert result.per_vm_eta == []

    @pytest.mark.asyncio
    async def test_without_per_vm_eta(self, mock_k8s_adapter, mock_migrations):
        """Test without per-VM ETA details."""
        mock_os_adapter = AsyncMock()
        mock_os_adapter.list_migrations = AsyncMock(return_value=mock_migrations)
        mock_os_adapter.__aenter__ = AsyncMock(return_value=mock_os_adapter)
        mock_os_adapter.__aexit__ = AsyncMock()

        with patch(
            "mosk_mcp.tools.operations_visibility.get_migration_eta.OpenStackAdapter",
            return_value=mock_os_adapter,
        ):
            result = await get_migration_eta(
                mock_k8s_adapter,
                GetMigrationETAInput(include_per_vm=False),
            )

        assert result.per_vm_eta == []
        assert result.total_active == 1

    @pytest.mark.asyncio
    async def test_bottleneck_detection(self, mock_k8s_adapter):
        """Test bottleneck host detection."""
        migrations = [
            ServerMigration(
                id=f"mig-{i}",
                server_uuid=f"vm-{i}",
                server_name=f"test-vm-{i}",
                source_compute="compute-01",  # All from same host
                dest_compute=f"compute-0{i + 2}",
                status=OSMigrationStatus.RUNNING,
                migration_type=MigrationType.LIVE_MIGRATION,
                memory_total_bytes=1_000_000_000,
                memory_processed_bytes=100_000_000,
                memory_remaining_bytes=900_000_000,
                disk_total_bytes=0,
                disk_processed_bytes=0,
                disk_remaining_bytes=0,
            )
            for i in range(4)
        ]

        mock_os_adapter = AsyncMock()
        mock_os_adapter.list_migrations = AsyncMock(return_value=migrations)
        mock_os_adapter.__aenter__ = AsyncMock(return_value=mock_os_adapter)
        mock_os_adapter.__aexit__ = AsyncMock()

        with patch(
            "mosk_mcp.tools.operations_visibility.get_migration_eta.OpenStackAdapter",
            return_value=mock_os_adapter,
        ):
            result = await get_migration_eta(
                mock_k8s_adapter,
                GetMigrationETAInput(),
            )

        assert result.bottleneck_host == "compute-01"
        # Should have recommendations about bottleneck
        assert any("bottleneck" in r.lower() for r in result.recommendations)

    @pytest.mark.asyncio
    async def test_recommendations_for_many_concurrent(self, mock_k8s_adapter):
        """Test recommendations when many concurrent migrations."""
        migrations = [
            ServerMigration(
                id=f"mig-{i}",
                server_uuid=f"vm-{i}",
                server_name=f"test-vm-{i}",
                source_compute=f"compute-0{i}",
                dest_compute=f"compute-0{i + 10}",
                status=OSMigrationStatus.RUNNING,
                migration_type=MigrationType.LIVE_MIGRATION,
                memory_total_bytes=1_000_000_000,
                memory_processed_bytes=100_000_000,
                memory_remaining_bytes=900_000_000,
                disk_total_bytes=0,
                disk_processed_bytes=0,
                disk_remaining_bytes=0,
            )
            for i in range(5)
        ]

        mock_os_adapter = AsyncMock()
        mock_os_adapter.list_migrations = AsyncMock(return_value=migrations)
        mock_os_adapter.__aenter__ = AsyncMock(return_value=mock_os_adapter)
        mock_os_adapter.__aexit__ = AsyncMock()

        with patch(
            "mosk_mcp.tools.operations_visibility.get_migration_eta.OpenStackAdapter",
            return_value=mock_os_adapter,
        ):
            result = await get_migration_eta(
                mock_k8s_adapter,
                GetMigrationETAInput(),
            )

        assert result.total_active == 5
        # Should recommend reducing concurrent migrations
        assert any("concurrent" in r.lower() for r in result.recommendations)

    @pytest.mark.asyncio
    async def test_recommendations_for_many_queued(self, mock_k8s_adapter):
        """Test recommendations when many queued migrations."""
        migrations = [
            ServerMigration(
                id=f"mig-{i}",
                server_uuid=f"vm-{i}",
                server_name=f"test-vm-{i}",
                source_compute=f"compute-0{i}",
                dest_compute=f"compute-0{i + 10}",
                status=OSMigrationStatus.QUEUED,
                migration_type=MigrationType.LIVE_MIGRATION,
                memory_total_bytes=1_000_000_000,
                memory_processed_bytes=0,
                memory_remaining_bytes=1_000_000_000,
                disk_total_bytes=0,
                disk_processed_bytes=0,
                disk_remaining_bytes=0,
            )
            for i in range(7)
        ]

        mock_os_adapter = AsyncMock()
        mock_os_adapter.list_migrations = AsyncMock(return_value=migrations)
        mock_os_adapter.__aenter__ = AsyncMock(return_value=mock_os_adapter)
        mock_os_adapter.__aexit__ = AsyncMock()

        with patch(
            "mosk_mcp.tools.operations_visibility.get_migration_eta.OpenStackAdapter",
            return_value=mock_os_adapter,
        ):
            result = await get_migration_eta(
                mock_k8s_adapter,
                GetMigrationETAInput(),
            )

        assert result.total_queued == 7
        # Should recommend scheduling
        assert any("queued" in r.lower() for r in result.recommendations)

    @pytest.mark.asyncio
    async def test_api_error(self, mock_k8s_adapter):
        """Test API error handling."""
        mock_os_adapter = AsyncMock()
        mock_os_adapter.list_migrations = AsyncMock(side_effect=Exception("Connection failed"))
        mock_os_adapter.__aenter__ = AsyncMock(return_value=mock_os_adapter)
        # __aexit__ must return False to propagate exceptions
        mock_os_adapter.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "mosk_mcp.tools.operations_visibility.get_migration_eta.OpenStackAdapter",
            return_value=mock_os_adapter,
        ):
            with pytest.raises(ToolExecutionError) as exc_info:
                await get_migration_eta(
                    mock_k8s_adapter,
                    GetMigrationETAInput(),
                )

        assert "Failed to get migration ETA" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_with_source_host_filter(self, mock_k8s_adapter, mock_migrations):
        """Test with source host filter."""
        mock_os_adapter = AsyncMock()
        mock_os_adapter.list_migrations = AsyncMock(return_value=mock_migrations)
        mock_os_adapter.__aenter__ = AsyncMock(return_value=mock_os_adapter)
        mock_os_adapter.__aexit__ = AsyncMock()

        with patch(
            "mosk_mcp.tools.operations_visibility.get_migration_eta.OpenStackAdapter",
            return_value=mock_os_adapter,
        ):
            result = await get_migration_eta(
                mock_k8s_adapter,
                GetMigrationETAInput(source_host="compute-01"),
            )

        # Should pass filter to adapter
        mock_os_adapter.list_migrations.assert_called_once()
        call_kwargs = mock_os_adapter.list_migrations.call_args.kwargs
        assert call_kwargs.get("source_compute") == "compute-01"

    @pytest.mark.asyncio
    async def test_timestamp_set(self, mock_k8s_adapter, mock_migrations):
        """Test timestamp is set in result."""
        mock_os_adapter = AsyncMock()
        mock_os_adapter.list_migrations = AsyncMock(return_value=mock_migrations)
        mock_os_adapter.__aenter__ = AsyncMock(return_value=mock_os_adapter)
        mock_os_adapter.__aexit__ = AsyncMock()

        with patch(
            "mosk_mcp.tools.operations_visibility.get_migration_eta.OpenStackAdapter",
            return_value=mock_os_adapter,
        ):
            result = await get_migration_eta(
                mock_k8s_adapter,
                GetMigrationETAInput(),
            )

        assert result.timestamp is not None
        # Verify valid ISO format
        datetime.fromisoformat(result.timestamp.replace("Z", "+00:00"))
