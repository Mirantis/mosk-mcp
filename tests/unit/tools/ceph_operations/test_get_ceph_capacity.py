"""Unit tests for get_ceph_capacity tool."""

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from mosk_mcp.adapters.ceph import (
    CAPACITY_CRITICAL_THRESHOLD,
    CAPACITY_EMERGENCY_THRESHOLD,
    CAPACITY_WARNING_THRESHOLD,
    OSDInfo,
    OSDState,
    OSDStatus,
)
from mosk_mcp.core.exceptions import ToolExecutionError
from mosk_mcp.tools.ceph_operations.get_ceph_capacity import (
    _generate_capacity_recommendations,
    _get_capacity_status,
    get_ceph_capacity,
)
from mosk_mcp.tools.ceph_operations.models import CapacityStatus


class TestGetCapacityStatus:
    """Tests for _get_capacity_status function."""

    def test_normal_status(self) -> None:
        """Test normal capacity status."""
        assert _get_capacity_status(50.0) == CapacityStatus.NORMAL
        assert _get_capacity_status(0.0) == CapacityStatus.NORMAL

    def test_warning_status(self) -> None:
        """Test warning capacity status."""
        assert _get_capacity_status(CAPACITY_WARNING_THRESHOLD) == CapacityStatus.WARNING
        assert _get_capacity_status(75.0) == CapacityStatus.WARNING

    def test_critical_status(self) -> None:
        """Test critical capacity status."""
        assert _get_capacity_status(CAPACITY_CRITICAL_THRESHOLD) == CapacityStatus.CRITICAL
        assert _get_capacity_status(82.0) == CapacityStatus.CRITICAL

    def test_emergency_status(self) -> None:
        """Test emergency capacity status."""
        assert _get_capacity_status(CAPACITY_EMERGENCY_THRESHOLD) == CapacityStatus.EMERGENCY
        assert _get_capacity_status(95.0) == CapacityStatus.EMERGENCY

    def test_boundary_values(self) -> None:
        """Test boundary values between thresholds."""
        # Just below warning
        assert _get_capacity_status(CAPACITY_WARNING_THRESHOLD - 0.1) == CapacityStatus.NORMAL
        # Just below critical
        assert _get_capacity_status(CAPACITY_CRITICAL_THRESHOLD - 0.1) == CapacityStatus.WARNING
        # Just below emergency
        assert _get_capacity_status(CAPACITY_EMERGENCY_THRESHOLD - 0.1) == CapacityStatus.CRITICAL


class TestGenerateCapacityRecommendations:
    """Tests for _generate_capacity_recommendations function."""

    def test_healthy_capacity(self) -> None:
        """Test healthy capacity recommendations."""
        result = _generate_capacity_recommendations(
            percent_used=50.0,
            total_bytes=1000000000000,
            pools=[],
        )

        assert any("healthy" in r.lower() for r in result)

    def test_warning_capacity(self) -> None:
        """Test warning capacity recommendations."""
        result = _generate_capacity_recommendations(
            percent_used=CAPACITY_WARNING_THRESHOLD + 1,
            total_bytes=1000000000000,
            pools=[],
        )

        assert any("approaching warning" in r.lower() for r in result)

    def test_critical_capacity(self) -> None:
        """Test critical capacity recommendations."""
        result = _generate_capacity_recommendations(
            percent_used=CAPACITY_CRITICAL_THRESHOLD + 1,
            total_bytes=1000000000000,
            pools=[],
        )

        assert any("plan to add osds" in r.lower() for r in result)

    def test_emergency_capacity(self) -> None:
        """Test emergency capacity recommendations."""
        result = _generate_capacity_recommendations(
            percent_used=CAPACITY_EMERGENCY_THRESHOLD + 1,
            total_bytes=1000000000000,
            pools=[],
        )

        assert any("urgent" in r.lower() for r in result)

    def test_high_utilization_pools(self) -> None:
        """Test high utilization pool recommendations."""
        pools: list[dict[str, Any]] = [
            {"pool_name": "volumes", "percent_used": 75.0},
            {"pool_name": "images", "percent_used": 55.0},
        ]

        result = _generate_capacity_recommendations(
            percent_used=50.0,
            total_bytes=1000000000000,
            pools=pools,
        )

        assert any("high utilization" in r.lower() for r in result)
        assert any("volumes" in r for r in result)
        assert any("images" in r for r in result)

    def test_low_remaining_capacity(self) -> None:
        """Test low remaining capacity warning when 75% used."""
        result = _generate_capacity_recommendations(
            percent_used=75.0,
            total_bytes=1000000000000,
            pools=[],
        )

        assert any("remaining" in r.lower() for r in result)

    def test_zero_usage(self) -> None:
        """Test zero usage case."""
        result = _generate_capacity_recommendations(
            percent_used=0.0,
            total_bytes=1000000000000,
            pools=[],
        )

        assert len(result) == 1
        assert "healthy" in result[0].lower()


class TestGetCephCapacity:
    """Tests for get_ceph_capacity function."""

    @pytest.fixture
    def mock_kubernetes_adapter(self) -> AsyncMock:
        """Create mock Kubernetes adapter."""
        return AsyncMock()

    @pytest.fixture
    def sample_capacity_data(self) -> dict[str, Any]:
        """Create sample capacity data."""
        return {
            "total_bytes": 10000000000000,  # 10 TB
            "used_bytes": 4500000000000,  # 4.5 TB
            "available_bytes": 5500000000000,  # 5.5 TB
            "capacity_percent": 45.0,
            "timestamp": "2024-01-01T00:00:00Z",
            "pools": [
                {
                    "pool_id": 1,
                    "pool_name": "volumes",
                    "total_bytes": 2000000000000,
                    "used_bytes": 1000000000000,
                    "max_avail_bytes": 1000000000000,
                    "percent_used": 50.0,
                    "objects": 1000,
                    "size": 3,
                },
                {
                    "pool_id": 2,
                    "pool_name": "images",
                    "total_bytes": 1000000000000,
                    "used_bytes": 250000000000,
                    "max_avail_bytes": 750000000000,
                    "percent_used": 25.0,
                    "objects": 100,
                    "size": 3,
                },
            ],
        }

    @pytest.fixture
    def sample_osds(self) -> list[OSDInfo]:
        """Create sample OSDs for device class testing."""
        return [
            OSDInfo(
                osd_id=0,
                host="node1",
                status=OSDStatus.UP,
                state=OSDState.IN,
                device_class="ssd",
                total_bytes=2000000000000,
                used_bytes=1000000000000,
            ),
            OSDInfo(
                osd_id=1,
                host="node1",
                status=OSDStatus.UP,
                state=OSDState.IN,
                device_class="ssd",
                total_bytes=2000000000000,
                used_bytes=900000000000,
            ),
            OSDInfo(
                osd_id=2,
                host="node2",
                status=OSDStatus.UP,
                state=OSDState.IN,
                device_class="hdd",
                total_bytes=4000000000000,
                used_bytes=1500000000000,
            ),
        ]

    @pytest.mark.asyncio
    async def test_get_capacity_success(
        self,
        mock_kubernetes_adapter: AsyncMock,
        sample_capacity_data: dict[str, Any],
        sample_osds: list[OSDInfo],
    ) -> None:
        """Test successful capacity retrieval."""
        mock_ceph = AsyncMock()
        mock_ceph.get_capacity.return_value = sample_capacity_data
        mock_ceph.list_osds.return_value = sample_osds

        with patch(
            "mosk_mcp.tools.ceph_operations.get_ceph_capacity.CephAdapter",
        ) as MockCephAdapter:
            MockCephAdapter.return_value.__aenter__.return_value = mock_ceph
            MockCephAdapter.return_value.__aexit__.return_value = None

            result = await get_ceph_capacity(mock_kubernetes_adapter)

        assert result.total_bytes == 10000000000000
        assert result.used_bytes == 4500000000000
        assert result.available_bytes == 5500000000000
        assert result.percent_used == 45.0
        assert result.status == CapacityStatus.NORMAL
        assert len(result.pools) == 2
        assert len(result.recommendations) > 0

    @pytest.mark.asyncio
    async def test_get_capacity_with_pools(
        self,
        mock_kubernetes_adapter: AsyncMock,
        sample_capacity_data: dict[str, Any],
        sample_osds: list[OSDInfo],
    ) -> None:
        """Test capacity with pool details."""
        mock_ceph = AsyncMock()
        mock_ceph.get_capacity.return_value = sample_capacity_data
        mock_ceph.list_osds.return_value = sample_osds

        with patch(
            "mosk_mcp.tools.ceph_operations.get_ceph_capacity.CephAdapter",
        ) as MockCephAdapter:
            MockCephAdapter.return_value.__aenter__.return_value = mock_ceph
            MockCephAdapter.return_value.__aexit__.return_value = None

            result = await get_ceph_capacity(mock_kubernetes_adapter, include_pools=True)

        # Pools sorted by utilization (highest first)
        assert len(result.pools) == 2
        assert result.pools[0].pool_name == "volumes"  # 50%
        assert result.pools[1].pool_name == "images"  # 25%

    @pytest.mark.asyncio
    async def test_get_capacity_without_pools(
        self,
        mock_kubernetes_adapter: AsyncMock,
        sample_capacity_data: dict[str, Any],
        sample_osds: list[OSDInfo],
    ) -> None:
        """Test capacity without pool details."""
        mock_ceph = AsyncMock()
        mock_ceph.get_capacity.return_value = sample_capacity_data
        mock_ceph.list_osds.return_value = sample_osds

        with patch(
            "mosk_mcp.tools.ceph_operations.get_ceph_capacity.CephAdapter",
        ) as MockCephAdapter:
            MockCephAdapter.return_value.__aenter__.return_value = mock_ceph
            MockCephAdapter.return_value.__aexit__.return_value = None

            result = await get_ceph_capacity(mock_kubernetes_adapter, include_pools=False)

        assert result.pools == []

    @pytest.mark.asyncio
    async def test_get_capacity_with_device_classes(
        self,
        mock_kubernetes_adapter: AsyncMock,
        sample_capacity_data: dict[str, Any],
        sample_osds: list[OSDInfo],
    ) -> None:
        """Test capacity with device class breakdown."""
        mock_ceph = AsyncMock()
        mock_ceph.get_capacity.return_value = sample_capacity_data
        mock_ceph.list_osds.return_value = sample_osds

        with patch(
            "mosk_mcp.tools.ceph_operations.get_ceph_capacity.CephAdapter",
        ) as MockCephAdapter:
            MockCephAdapter.return_value.__aenter__.return_value = mock_ceph
            MockCephAdapter.return_value.__aexit__.return_value = None

            result = await get_ceph_capacity(mock_kubernetes_adapter, include_classes=True)

        assert "ssd" in result.by_device_class
        assert "hdd" in result.by_device_class

        ssd = result.by_device_class["ssd"]
        assert ssd["osd_count"] == 2
        assert ssd["total_bytes"] == 4000000000000  # 2 + 2 TB
        assert ssd["used_bytes"] == 1900000000000  # 1 + 0.9 TB

        hdd = result.by_device_class["hdd"]
        assert hdd["osd_count"] == 1
        assert hdd["total_bytes"] == 4000000000000

    @pytest.mark.asyncio
    async def test_get_capacity_without_device_classes(
        self,
        mock_kubernetes_adapter: AsyncMock,
        sample_capacity_data: dict[str, Any],
    ) -> None:
        """Test capacity without device class breakdown."""
        mock_ceph = AsyncMock()
        mock_ceph.get_capacity.return_value = sample_capacity_data

        with patch(
            "mosk_mcp.tools.ceph_operations.get_ceph_capacity.CephAdapter",
        ) as MockCephAdapter:
            MockCephAdapter.return_value.__aenter__.return_value = mock_ceph
            MockCephAdapter.return_value.__aexit__.return_value = None

            result = await get_ceph_capacity(mock_kubernetes_adapter, include_classes=False)

        assert result.by_device_class == {}
        mock_ceph.list_osds.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_capacity_thresholds(
        self,
        mock_kubernetes_adapter: AsyncMock,
        sample_capacity_data: dict[str, Any],
    ) -> None:
        """Test capacity thresholds are included."""
        mock_ceph = AsyncMock()
        mock_ceph.get_capacity.return_value = sample_capacity_data

        with patch(
            "mosk_mcp.tools.ceph_operations.get_ceph_capacity.CephAdapter",
        ) as MockCephAdapter:
            MockCephAdapter.return_value.__aenter__.return_value = mock_ceph
            MockCephAdapter.return_value.__aexit__.return_value = None

            result = await get_ceph_capacity(
                mock_kubernetes_adapter,
                include_pools=False,
                include_classes=False,
            )

        assert result.thresholds["warning"] == CAPACITY_WARNING_THRESHOLD
        assert result.thresholds["critical"] == CAPACITY_CRITICAL_THRESHOLD
        assert result.thresholds["emergency"] == CAPACITY_EMERGENCY_THRESHOLD

    @pytest.mark.asyncio
    async def test_get_capacity_critical_status(self, mock_kubernetes_adapter: AsyncMock) -> None:
        """Test capacity with critical status."""
        capacity_data = {
            "total_bytes": 10000000000000,
            "used_bytes": 8200000000000,
            "available_bytes": 1800000000000,
            "capacity_percent": 82.0,  # Above critical threshold
            "pools": [],
        }

        mock_ceph = AsyncMock()
        mock_ceph.get_capacity.return_value = capacity_data

        with patch(
            "mosk_mcp.tools.ceph_operations.get_ceph_capacity.CephAdapter",
        ) as MockCephAdapter:
            MockCephAdapter.return_value.__aenter__.return_value = mock_ceph
            MockCephAdapter.return_value.__aexit__.return_value = None

            result = await get_ceph_capacity(
                mock_kubernetes_adapter,
                include_pools=False,
                include_classes=False,
            )

        assert result.status == CapacityStatus.CRITICAL
        assert any("add osds" in r.lower() for r in result.recommendations)

    @pytest.mark.asyncio
    async def test_get_capacity_unknown_device_class(
        self, mock_kubernetes_adapter: AsyncMock
    ) -> None:
        """Test capacity with unknown device class."""
        capacity_data = {
            "total_bytes": 10000000000000,
            "used_bytes": 4500000000000,
            "available_bytes": 5500000000000,
            "capacity_percent": 45.0,
            "pools": [],
        }
        osds = [
            OSDInfo(
                osd_id=0,
                host="node1",
                status=OSDStatus.UP,
                state=OSDState.IN,
                device_class="",  # Empty device class
                total_bytes=2000000000000,
                used_bytes=1000000000000,
            ),
        ]

        mock_ceph = AsyncMock()
        mock_ceph.get_capacity.return_value = capacity_data
        mock_ceph.list_osds.return_value = osds

        with patch(
            "mosk_mcp.tools.ceph_operations.get_ceph_capacity.CephAdapter",
        ) as MockCephAdapter:
            MockCephAdapter.return_value.__aenter__.return_value = mock_ceph
            MockCephAdapter.return_value.__aexit__.return_value = None

            result = await get_ceph_capacity(mock_kubernetes_adapter, include_classes=True)

        assert "unknown" in result.by_device_class

    @pytest.mark.asyncio
    async def test_get_capacity_error(self, mock_kubernetes_adapter: AsyncMock) -> None:
        """Test error handling."""
        mock_ceph = AsyncMock()
        mock_ceph.get_capacity.side_effect = Exception("Ceph connection failed")

        with patch(
            "mosk_mcp.tools.ceph_operations.get_ceph_capacity.CephAdapter",
        ) as MockCephAdapter:
            MockCephAdapter.return_value.__aenter__.return_value = mock_ceph
            MockCephAdapter.return_value.__aexit__.return_value = None

            with pytest.raises(ToolExecutionError) as exc_info:
                await get_ceph_capacity(mock_kubernetes_adapter)

        assert "Failed to get Ceph capacity" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_get_capacity_timestamp(
        self,
        mock_kubernetes_adapter: AsyncMock,
        sample_capacity_data: dict[str, Any],
    ) -> None:
        """Test timestamp is included."""
        mock_ceph = AsyncMock()
        mock_ceph.get_capacity.return_value = sample_capacity_data

        with patch(
            "mosk_mcp.tools.ceph_operations.get_ceph_capacity.CephAdapter",
        ) as MockCephAdapter:
            MockCephAdapter.return_value.__aenter__.return_value = mock_ceph
            MockCephAdapter.return_value.__aexit__.return_value = None

            result = await get_ceph_capacity(
                mock_kubernetes_adapter,
                include_pools=False,
                include_classes=False,
            )

        assert result.timestamp == "2024-01-01T00:00:00Z"
