"""Unit tests for Ceph storage monitoring tools.

This module contains tests for read-only Ceph monitoring MCP tools
including status monitoring, capacity tracking, and recovery monitoring.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from mosk_mcp.adapters.ceph import (
    CAPACITY_CRITICAL_THRESHOLD,
    CAPACITY_EMERGENCY_THRESHOLD,
    CAPACITY_WARNING_THRESHOLD,
    CephAdapter,
    CephClusterStatus,
    CephHealthStatus,
    OSDInfo,
    OSDState,
    OSDStatus,
    PGSummary,
    RecoveryStatus,
)
from mosk_mcp.core.exceptions import ResourceNotFoundError
from mosk_mcp.tools.ceph_operations import (
    get_ceph_capacity,
    get_ceph_status,
    get_osd_details,
    get_pg_status,
    get_recovery_status,
    list_osds,
    predict_capacity,
)
from mosk_mcp.tools.ceph_operations.models import (
    CapacityStatus,
    CephHealthLevel,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_k8s_adapter():
    """Create a mock Kubernetes adapter."""
    adapter = AsyncMock()
    adapter.connect = AsyncMock()
    adapter.list = AsyncMock(
        return_value=[
            {
                "metadata": {"name": "rook-ceph-tools-abc123"},
                "status": {"phase": "Running"},
            }
        ]
    )
    adapter.get = AsyncMock()
    adapter.patch = AsyncMock()
    return adapter


@pytest.fixture
def mock_cluster_status():
    """Create a mock CephClusterStatus."""
    return CephClusterStatus(
        health=CephHealthStatus.HEALTH_OK,
        health_checks={},
        fsid="12345678-1234-1234-1234-123456789012",
        quorum=["mon-a", "mon-b", "mon-c"],
        num_osds=6,
        num_osds_up=6,
        num_osds_in=6,
        num_pgs=256,
        pg_states={"active+clean": 256},
        total_bytes=6_000_000_000_000,  # 6 TB
        used_bytes=1_800_000_000_000,  # 1.8 TB (30%)
        available_bytes=4_200_000_000_000,
        capacity_percent=30.0,
        timestamp=datetime.now(UTC),
    )


@pytest.fixture
def mock_osd_list():
    """Create a mock list of OSDInfo objects."""
    return [
        OSDInfo(
            osd_id=0,
            uuid="uuid-0",
            status=OSDStatus.UP,
            state=OSDState.IN,
            host="storage-01",
            device_class="ssd",
            crush_weight=1.0,
            reweight=1.0,
            total_bytes=1_000_000_000_000,
            used_bytes=300_000_000_000,
            available_bytes=700_000_000_000,
            utilization_percent=30.0,
            pgs=43,
            commit_latency_ms=1.0,
            apply_latency_ms=2.0,
        ),
        OSDInfo(
            osd_id=1,
            uuid="uuid-1",
            status=OSDStatus.UP,
            state=OSDState.IN,
            host="storage-01",
            device_class="ssd",
            crush_weight=1.0,
            reweight=1.0,
            total_bytes=1_000_000_000_000,
            used_bytes=300_000_000_000,
            available_bytes=700_000_000_000,
            utilization_percent=30.0,
            pgs=42,
            commit_latency_ms=1.0,
            apply_latency_ms=2.0,
        ),
        OSDInfo(
            osd_id=2,
            uuid="uuid-2",
            status=OSDStatus.UP,
            state=OSDState.IN,
            host="storage-02",
            device_class="ssd",
            crush_weight=1.0,
            reweight=1.0,
            total_bytes=1_000_000_000_000,
            used_bytes=300_000_000_000,
            available_bytes=700_000_000_000,
            utilization_percent=30.0,
            pgs=43,
            commit_latency_ms=1.0,
            apply_latency_ms=2.0,
        ),
    ]


@pytest.fixture
def mock_pg_summary():
    """Create a mock PGSummary."""
    return PGSummary(
        total_pgs=256,
        active_clean=256,
        states={"active+clean": 256},
        stuck_pgs={},
        misplaced_ratio=0.0,
        degraded_ratio=0.0,
        recovering=False,
        recovery_rate_bytes=0,
    )


@pytest.fixture
def mock_recovery_status():
    """Create a mock RecoveryStatus."""
    return RecoveryStatus(
        is_recovering=False,
        is_backfilling=False,
        recovering_objects=0,
        recovering_bytes=0,
        recovery_rate_objects=0,
        recovery_rate_bytes=0,
        misplaced_objects=0,
        misplaced_total=0,
        misplaced_ratio=0.0,
        degraded_objects=0,
        degraded_total=0,
        degraded_ratio=0.0,
    )


# =============================================================================
# Test get_ceph_status
# =============================================================================


class TestGetCephStatus:
    """Tests for get_ceph_status tool."""

    @pytest.mark.asyncio
    async def test_get_ceph_status_healthy(
        self,
        mock_k8s_adapter,
        mock_cluster_status,
    ):
        """Test getting status of a healthy cluster."""
        with (
            patch.object(
                CephAdapter,
                "get_cluster_status",
                new_callable=AsyncMock,
                return_value=mock_cluster_status,
            ),
            patch.object(CephAdapter, "connect", new_callable=AsyncMock),
        ):
            result = await get_ceph_status(
                kubernetes_adapter=mock_k8s_adapter,
                include_health_details=True,
                include_pg_summary=True,
            )

        assert result.health == CephHealthLevel.HEALTH_OK
        assert result.is_healthy is True
        assert result.is_safe_for_operations is True
        assert result.num_osds == 6
        assert result.num_osds_up == 6
        assert result.capacity.percent_used == 30.0
        assert result.capacity.status == CapacityStatus.NORMAL

    @pytest.mark.asyncio
    async def test_get_ceph_status_with_warnings(self, mock_k8s_adapter):
        """Test getting status when cluster has warnings."""
        status = CephClusterStatus(
            health=CephHealthStatus.HEALTH_WARN,
            health_checks={
                "OSD_DOWN": {
                    "severity": "HEALTH_WARN",
                    "summary": {"message": "1 osd(s) down"},
                }
            },
            num_osds=6,
            num_osds_up=5,
            num_osds_in=6,
            num_pgs=256,
            pg_states={"active+clean": 250, "active+degraded": 6},
            total_bytes=6_000_000_000_000,
            used_bytes=1_800_000_000_000,
            capacity_percent=30.0,
        )

        with (
            patch.object(
                CephAdapter,
                "get_cluster_status",
                new_callable=AsyncMock,
                return_value=status,
            ),
            patch.object(CephAdapter, "connect", new_callable=AsyncMock),
        ):
            result = await get_ceph_status(mock_k8s_adapter)

        assert result.health == CephHealthLevel.HEALTH_WARN
        assert result.is_healthy is False
        assert result.is_safe_for_operations is False
        assert len(result.warnings) > 0
        assert "1 OSD(s) are down" in result.warnings

    @pytest.mark.asyncio
    async def test_get_ceph_status_capacity_warning(self, mock_k8s_adapter):
        """Test capacity warning threshold detection."""
        status = CephClusterStatus(
            health=CephHealthStatus.HEALTH_OK,
            num_osds=6,
            num_osds_up=6,
            num_osds_in=6,
            num_pgs=256,
            pg_states={"active+clean": 256},
            total_bytes=6_000_000_000_000,
            used_bytes=4_500_000_000_000,  # 75%
            capacity_percent=75.0,
        )

        with (
            patch.object(
                CephAdapter,
                "get_cluster_status",
                new_callable=AsyncMock,
                return_value=status,
            ),
            patch.object(CephAdapter, "connect", new_callable=AsyncMock),
        ):
            result = await get_ceph_status(mock_k8s_adapter)

        assert result.capacity.status == CapacityStatus.WARNING
        assert any("WARNING" in w and "75.0%" in w for w in result.warnings)


# =============================================================================
# Test list_osds
# =============================================================================


class TestListOSDs:
    """Tests for list_osds tool."""

    @pytest.mark.asyncio
    async def test_list_osds_all(self, mock_k8s_adapter, mock_osd_list):
        """Test listing all OSDs."""
        with (
            patch.object(
                CephAdapter,
                "list_osds",
                new_callable=AsyncMock,
                return_value=mock_osd_list,
            ),
            patch.object(CephAdapter, "connect", new_callable=AsyncMock),
        ):
            result = await list_osds(mock_k8s_adapter)

        assert result.total_count == 3
        assert result.up_count == 3
        assert result.down_count == 0
        assert len(result.osds) == 3
        assert "storage-01" in result.by_host
        assert result.by_host["storage-01"] == 2

    @pytest.mark.asyncio
    async def test_list_osds_filter_by_host(self, mock_k8s_adapter, mock_osd_list):
        """Test filtering OSDs by host."""
        with (
            patch.object(
                CephAdapter,
                "list_osds",
                new_callable=AsyncMock,
                return_value=mock_osd_list,
            ),
            patch.object(CephAdapter, "connect", new_callable=AsyncMock),
        ):
            result = await list_osds(
                mock_k8s_adapter,
                host_filter="storage-01",
            )

        # Should return only OSDs from storage-01
        assert len(result.osds) == 2
        assert all(osd.host == "storage-01" for osd in result.osds)

    @pytest.mark.asyncio
    async def test_list_osds_filter_by_status(self, mock_k8s_adapter):
        """Test filtering OSDs by status."""
        osd_list = [
            OSDInfo(osd_id=0, status=OSDStatus.UP, state=OSDState.IN, host="h1"),
            OSDInfo(osd_id=1, status=OSDStatus.DOWN, state=OSDState.IN, host="h1"),
            OSDInfo(osd_id=2, status=OSDStatus.UP, state=OSDState.IN, host="h2"),
        ]

        with (
            patch.object(
                CephAdapter,
                "list_osds",
                new_callable=AsyncMock,
                return_value=osd_list,
            ),
            patch.object(CephAdapter, "connect", new_callable=AsyncMock),
        ):
            result = await list_osds(
                mock_k8s_adapter,
                status_filter="up",
            )

        assert len(result.osds) == 2
        assert all(osd.status == "up" for osd in result.osds)


# =============================================================================
# Test get_osd_details
# =============================================================================


class TestGetOSDDetails:
    """Tests for get_osd_details tool."""

    @pytest.mark.asyncio
    async def test_get_osd_details_success(self, mock_k8s_adapter, mock_osd_list):
        """Test getting details of a specific OSD."""
        with (
            patch.object(
                CephAdapter,
                "get_osd_details",
                new_callable=AsyncMock,
                return_value=mock_osd_list[0],
            ),
            patch.object(
                CephAdapter,
                "list_osds",
                new_callable=AsyncMock,
                return_value=mock_osd_list,
            ),
            patch.object(CephAdapter, "connect", new_callable=AsyncMock),
        ):
            result = await get_osd_details(mock_k8s_adapter, osd_id=0)

        assert result.osd.osd_id == 0
        assert result.osd.host == "storage-01"
        assert result.osd.status == "up"
        assert result.osd.state == "in"
        assert result.osd.is_healthy is True

    @pytest.mark.asyncio
    async def test_get_osd_details_not_found(self, mock_k8s_adapter):
        """Test getting details of non-existent OSD."""
        with (
            patch.object(
                CephAdapter,
                "get_osd_details",
                new_callable=AsyncMock,
                side_effect=ResourceNotFoundError(
                    "OSD 99 not found",
                    resource_type="OSD",
                    resource_id="99",
                ),
            ),
            patch.object(CephAdapter, "connect", new_callable=AsyncMock),
        ):
            with pytest.raises(ResourceNotFoundError):
                await get_osd_details(mock_k8s_adapter, osd_id=99)


# =============================================================================
# Test get_ceph_capacity
# =============================================================================


class TestGetCephCapacity:
    """Tests for get_ceph_capacity tool."""

    @pytest.mark.asyncio
    async def test_get_ceph_capacity_normal(self, mock_k8s_adapter, mock_osd_list):
        """Test getting capacity with normal utilization."""
        capacity_data = {
            "total_bytes": 6_000_000_000_000,
            "used_bytes": 1_800_000_000_000,
            "available_bytes": 4_200_000_000_000,
            "capacity_percent": 30.0,
            "pools": [
                {
                    "pool_id": 1,
                    "pool_name": "volumes",
                    "total_bytes": 1_000_000_000_000,
                    "used_bytes": 500_000_000_000,
                    "percent_used": 50.0,
                    "max_avail_bytes": 2_000_000_000_000,
                    "objects": 10000,
                }
            ],
            "timestamp": datetime.now(UTC).isoformat(),
        }

        with (
            patch.object(
                CephAdapter,
                "get_capacity",
                new_callable=AsyncMock,
                return_value=capacity_data,
            ),
            patch.object(
                CephAdapter,
                "list_osds",
                new_callable=AsyncMock,
                return_value=mock_osd_list,
            ),
            patch.object(CephAdapter, "connect", new_callable=AsyncMock),
        ):
            result = await get_ceph_capacity(mock_k8s_adapter)

        assert result.percent_used == 30.0
        assert result.status == CapacityStatus.NORMAL
        assert len(result.pools) == 1
        assert result.pools[0].pool_name == "volumes"

    @pytest.mark.asyncio
    async def test_get_ceph_capacity_critical(self, mock_k8s_adapter, mock_osd_list):
        """Test getting capacity at critical level."""
        capacity_data = {
            "total_bytes": 6_000_000_000_000,
            "used_bytes": 5_100_000_000_000,  # 85%
            "available_bytes": 900_000_000_000,
            "capacity_percent": 85.0,
            "pools": [],
            "timestamp": datetime.now(UTC).isoformat(),
        }

        with (
            patch.object(
                CephAdapter,
                "get_capacity",
                new_callable=AsyncMock,
                return_value=capacity_data,
            ),
            patch.object(
                CephAdapter,
                "list_osds",
                new_callable=AsyncMock,
                return_value=mock_osd_list,
            ),
            patch.object(CephAdapter, "connect", new_callable=AsyncMock),
        ):
            result = await get_ceph_capacity(mock_k8s_adapter)

        assert result.percent_used == 85.0
        assert result.status == CapacityStatus.EMERGENCY


# =============================================================================
# Test get_pg_status
# =============================================================================


class TestGetPGStatus:
    """Tests for get_pg_status tool."""

    @pytest.mark.asyncio
    async def test_get_pg_status_healthy(
        self,
        mock_k8s_adapter,
        mock_pg_summary,
        mock_recovery_status,
    ):
        """Test PG status when all healthy."""
        with (
            patch.object(
                CephAdapter,
                "get_pg_status",
                new_callable=AsyncMock,
                return_value=mock_pg_summary,
            ),
            patch.object(
                CephAdapter,
                "get_recovery_status",
                new_callable=AsyncMock,
                return_value=mock_recovery_status,
            ),
            patch.object(CephAdapter, "connect", new_callable=AsyncMock),
        ):
            result = await get_pg_status(mock_k8s_adapter)

        assert result.is_healthy is True
        assert result.total_pgs == 256
        assert result.active_clean == 256
        assert result.recovery_active is False

    @pytest.mark.asyncio
    async def test_get_pg_status_with_recovery(self, mock_k8s_adapter):
        """Test PG status during recovery."""
        pg_summary = PGSummary(
            total_pgs=256,
            active_clean=240,
            states={"active+clean": 240, "active+recovering": 16},
            stuck_pgs={},
            misplaced_ratio=5.0,
            degraded_ratio=1.0,
            recovering=True,
        )

        recovery = RecoveryStatus(
            is_recovering=True,
            is_backfilling=False,
            misplaced_objects=1000,
            misplaced_total=20000,
            misplaced_ratio=5.0,
        )

        with (
            patch.object(
                CephAdapter,
                "get_pg_status",
                new_callable=AsyncMock,
                return_value=pg_summary,
            ),
            patch.object(
                CephAdapter,
                "get_recovery_status",
                new_callable=AsyncMock,
                return_value=recovery,
            ),
            patch.object(CephAdapter, "connect", new_callable=AsyncMock),
        ):
            result = await get_pg_status(mock_k8s_adapter)

        assert result.is_healthy is False
        assert result.recovery_active is True
        assert result.misplaced_ratio == 5.0


# =============================================================================
# Test predict_capacity
# =============================================================================


class TestPredictCapacity:
    """Tests for predict_capacity tool."""

    @pytest.mark.asyncio
    async def test_predict_capacity_healthy(self, mock_k8s_adapter):
        """Test capacity prediction for healthy cluster."""
        capacity_data = {
            "total_bytes": 6_000_000_000_000,
            "used_bytes": 1_800_000_000_000,  # 30%
            "available_bytes": 4_200_000_000_000,
            "capacity_percent": 30.0,
            "pools": [],
            "timestamp": datetime.now(UTC).isoformat(),
        }

        with (
            patch.object(
                CephAdapter,
                "get_capacity",
                new_callable=AsyncMock,
                return_value=capacity_data,
            ),
            patch.object(CephAdapter, "connect", new_callable=AsyncMock),
        ):
            result = await predict_capacity(
                mock_k8s_adapter,
                days_to_forecast=30,
            )

        assert result.current_percent_used == 30.0
        assert len(result.forecasts) > 0
        # With default growth rate, warning should be in the future
        assert result.days_until_warning is None or result.days_until_warning > 0

    @pytest.mark.asyncio
    async def test_predict_capacity_with_custom_growth(self, mock_k8s_adapter):
        """Test capacity prediction with custom growth rate."""
        capacity_data = {
            "total_bytes": 6_000_000_000_000,
            "used_bytes": 4_200_000_000_000,  # 70%
            "available_bytes": 1_800_000_000_000,
            "capacity_percent": 70.0,
            "pools": [],
            "timestamp": datetime.now(UTC).isoformat(),
        }

        with (
            patch.object(
                CephAdapter,
                "get_capacity",
                new_callable=AsyncMock,
                return_value=capacity_data,
            ),
            patch.object(CephAdapter, "connect", new_callable=AsyncMock),
        ):
            result = await predict_capacity(
                mock_k8s_adapter,
                days_to_forecast=90,
                growth_rate_gb_per_day=10.0,  # 10 GB/day
            )

        assert result.current_percent_used == 70.0
        assert result.growth_rate_bytes_per_day == 10 * 1024 * 1024 * 1024
        # Should reach warning soon since already at 70%
        assert result.days_until_warning == 0 or result.days_until_warning is None


# =============================================================================
# Test get_recovery_status
# =============================================================================


class TestGetRecoveryStatus:
    """Tests for get_recovery_status tool."""

    @pytest.mark.asyncio
    async def test_get_recovery_status_idle(
        self,
        mock_k8s_adapter,
        mock_pg_summary,
        mock_recovery_status,
    ):
        """Test recovery status when no recovery is in progress."""
        with (
            patch.object(
                CephAdapter,
                "get_recovery_status",
                new_callable=AsyncMock,
                return_value=mock_recovery_status,
            ),
            patch.object(
                CephAdapter,
                "get_pg_status",
                new_callable=AsyncMock,
                return_value=mock_pg_summary,
            ),
            patch.object(CephAdapter, "connect", new_callable=AsyncMock),
        ):
            result = await get_recovery_status(mock_k8s_adapter)

        assert result.is_recovering is False
        assert result.is_backfilling is False
        assert result.recovery_progress is None
        assert "No recovery" in result.status_summary

    @pytest.mark.asyncio
    async def test_get_recovery_status_active(self, mock_k8s_adapter):
        """Test recovery status when recovery is active."""
        recovery = RecoveryStatus(
            is_recovering=True,
            is_backfilling=False,
            recovering_bytes=1_000_000_000,
            recovery_rate_bytes=100_000_000,  # 100 MB/s
            misplaced_objects=1000,
            misplaced_total=20000,
            misplaced_ratio=5.0,
        )

        pg_summary = PGSummary(
            total_pgs=256,
            active_clean=240,
            states={"active+clean": 240, "active+recovering": 16},
            recovering=True,
        )

        with (
            patch.object(
                CephAdapter,
                "get_recovery_status",
                new_callable=AsyncMock,
                return_value=recovery,
            ),
            patch.object(
                CephAdapter,
                "get_pg_status",
                new_callable=AsyncMock,
                return_value=pg_summary,
            ),
            patch.object(CephAdapter, "connect", new_callable=AsyncMock),
        ):
            result = await get_recovery_status(mock_k8s_adapter)

        assert result.is_recovering is True
        assert result.pgs_recovering == 16
        assert result.misplaced_ratio == 5.0


# =============================================================================
# Test Models
# =============================================================================


class TestModels:
    """Tests for Pydantic models."""

    def test_capacity_status_enum(self):
        """Test CapacityStatus enum values."""
        assert CapacityStatus.NORMAL.value == "normal"
        assert CapacityStatus.WARNING.value == "warning"
        assert CapacityStatus.CRITICAL.value == "critical"
        assert CapacityStatus.EMERGENCY.value == "emergency"

    def test_ceph_health_level_enum(self):
        """Test CephHealthLevel enum values."""
        assert CephHealthLevel.HEALTH_OK.value == "HEALTH_OK"
        assert CephHealthLevel.HEALTH_WARN.value == "HEALTH_WARN"
        assert CephHealthLevel.HEALTH_ERR.value == "HEALTH_ERR"

    def test_capacity_thresholds(self):
        """Test capacity threshold constants."""
        assert CAPACITY_WARNING_THRESHOLD == 70
        assert CAPACITY_CRITICAL_THRESHOLD == 80
        assert CAPACITY_EMERGENCY_THRESHOLD == 85
