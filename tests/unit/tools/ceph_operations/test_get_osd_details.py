"""Unit tests for get_osd_details tool."""

from unittest.mock import AsyncMock, patch

import pytest

from mosk_mcp.adapters.ceph import OSDInfo, OSDState, OSDStatus
from mosk_mcp.core.exceptions import ResourceNotFoundError, ToolExecutionError
from mosk_mcp.tools.ceph_operations.get_osd_details import (
    _generate_health_warnings,
    _generate_recommendations,
    _get_capacity_status,
    get_osd_details,
)
from mosk_mcp.tools.ceph_operations.models import CapacityStatus


class TestGetCapacityStatus:
    """Tests for _get_capacity_status function."""

    def test_normal_status(self) -> None:
        """Test normal capacity status."""
        assert _get_capacity_status(50.0) == CapacityStatus.NORMAL
        assert _get_capacity_status(69.9) == CapacityStatus.NORMAL

    def test_warning_status(self) -> None:
        """Test warning capacity status."""
        assert _get_capacity_status(70.0) == CapacityStatus.WARNING
        assert _get_capacity_status(79.9) == CapacityStatus.WARNING

    def test_critical_status(self) -> None:
        """Test critical capacity status."""
        assert _get_capacity_status(80.0) == CapacityStatus.CRITICAL
        assert _get_capacity_status(84.9) == CapacityStatus.CRITICAL

    def test_emergency_status(self) -> None:
        """Test emergency capacity status."""
        assert _get_capacity_status(85.0) == CapacityStatus.EMERGENCY
        assert _get_capacity_status(95.0) == CapacityStatus.EMERGENCY


class TestGenerateHealthWarnings:
    """Tests for _generate_health_warnings function."""

    def test_healthy_osd(self) -> None:
        """Test healthy OSD has no warnings."""
        result = _generate_health_warnings(
            osd_id=0,
            status="up",
            state="in",
            utilization_percent=50.0,
            commit_latency_ms=10.0,
            apply_latency_ms=15.0,
        )

        assert len(result) == 0

    def test_down_status(self) -> None:
        """Test down status warning."""
        result = _generate_health_warnings(
            osd_id=1,
            status="down",
            state="in",
            utilization_percent=50.0,
            commit_latency_ms=10.0,
            apply_latency_ms=15.0,
        )

        assert len(result) == 1
        assert "DOWN" in result[0]

    def test_out_state(self) -> None:
        """Test out state warning."""
        result = _generate_health_warnings(
            osd_id=2,
            status="up",
            state="out",
            utilization_percent=50.0,
            commit_latency_ms=10.0,
            apply_latency_ms=15.0,
        )

        assert len(result) == 1
        assert "OUT" in result[0]

    def test_high_utilization(self) -> None:
        """Test high utilization warning."""
        result = _generate_health_warnings(
            osd_id=3,
            status="up",
            state="in",
            utilization_percent=82.0,
            commit_latency_ms=10.0,
            apply_latency_ms=15.0,
        )

        assert len(result) == 1
        assert "high" in result[0].lower()

    def test_critical_utilization(self) -> None:
        """Test critically high utilization warning."""
        result = _generate_health_warnings(
            osd_id=4,
            status="up",
            state="in",
            utilization_percent=90.0,
            commit_latency_ms=10.0,
            apply_latency_ms=15.0,
        )

        assert len(result) == 1
        assert "critically high" in result[0].lower()

    def test_high_commit_latency(self) -> None:
        """Test high commit latency warning."""
        result = _generate_health_warnings(
            osd_id=5,
            status="up",
            state="in",
            utilization_percent=50.0,
            commit_latency_ms=150.0,
            apply_latency_ms=15.0,
        )

        assert len(result) == 1
        assert "commit latency" in result[0].lower()

    def test_high_apply_latency(self) -> None:
        """Test high apply latency warning."""
        result = _generate_health_warnings(
            osd_id=6,
            status="up",
            state="in",
            utilization_percent=50.0,
            commit_latency_ms=10.0,
            apply_latency_ms=150.0,
        )

        assert len(result) == 1
        assert "apply latency" in result[0].lower()

    def test_multiple_warnings(self) -> None:
        """Test multiple warnings."""
        result = _generate_health_warnings(
            osd_id=7,
            status="down",
            state="out",
            utilization_percent=90.0,
            commit_latency_ms=150.0,
            apply_latency_ms=150.0,
        )

        assert len(result) == 5


class TestGenerateRecommendations:
    """Tests for _generate_recommendations function."""

    def test_healthy_osd(self) -> None:
        """Test healthy OSD has no recommendations."""
        result = _generate_recommendations(
            osd_id=0,
            status="up",
            state="in",
            utilization_percent=50.0,
            commit_latency_ms=10.0,
            all_osds_avg_utilization=50.0,
        )

        assert len(result) == 0

    def test_down_status_recommendation(self) -> None:
        """Test down status recommendation."""
        result = _generate_recommendations(
            osd_id=1,
            status="down",
            state="in",
            utilization_percent=0.0,
            commit_latency_ms=0.0,
            all_osds_avg_utilization=50.0,
        )

        assert len(result) == 1
        assert "investigate" in result[0].lower()

    def test_out_state_recommendation(self) -> None:
        """Test out state recommendation."""
        result = _generate_recommendations(
            osd_id=2,
            status="up",
            state="out",
            utilization_percent=0.0,
            commit_latency_ms=10.0,
            all_osds_avg_utilization=50.0,
        )

        assert len(result) == 1
        assert "maintenance" in result[0].lower()

    def test_overutilized_recommendation(self) -> None:
        """Test overutilized OSD recommendation."""
        result = _generate_recommendations(
            osd_id=3,
            status="up",
            state="in",
            utilization_percent=65.0,
            commit_latency_ms=10.0,
            all_osds_avg_utilization=50.0,
        )

        assert any("reweighting" in r.lower() for r in result)

    def test_underutilized_recommendation(self) -> None:
        """Test underutilized OSD recommendation."""
        result = _generate_recommendations(
            osd_id=4,
            status="up",
            state="in",
            utilization_percent=30.0,
            commit_latency_ms=10.0,
            all_osds_avg_utilization=50.0,
        )

        assert any("underutilized" in r.lower() for r in result)

    def test_high_latency_recommendation(self) -> None:
        """Test high latency recommendation."""
        result = _generate_recommendations(
            osd_id=5,
            status="up",
            state="in",
            utilization_percent=50.0,
            commit_latency_ms=75.0,
            all_osds_avg_utilization=50.0,
        )

        assert any("latency" in r.lower() for r in result)

    def test_high_utilization_recommendation(self) -> None:
        """Test high utilization recommendation."""
        result = _generate_recommendations(
            osd_id=6,
            status="up",
            state="in",
            utilization_percent=90.0,
            commit_latency_ms=10.0,
            all_osds_avg_utilization=50.0,
        )

        assert any("near full" in r.lower() for r in result)

    def test_zero_utilization_no_division_error(self) -> None:
        """Test zero utilization doesn't cause division error."""
        result = _generate_recommendations(
            osd_id=7,
            status="up",
            state="in",
            utilization_percent=0.0,
            commit_latency_ms=10.0,
            all_osds_avg_utilization=50.0,
        )

        # No error should occur
        assert isinstance(result, list)


class TestGetOsdDetails:
    """Tests for get_osd_details function."""

    @pytest.fixture
    def mock_kubernetes_adapter(self) -> AsyncMock:
        """Create mock Kubernetes adapter."""
        return AsyncMock()

    @pytest.fixture
    def sample_osd_info(self) -> OSDInfo:
        """Create sample OSD info."""
        return OSDInfo(
            osd_id=5,
            uuid="abc-123",
            host="node1",
            status=OSDStatus.UP,
            state=OSDState.IN,
            device_class="ssd",
            crush_weight=3.5,
            reweight=1.0,
            total_bytes=4000000000000,
            used_bytes=2000000000000,
            available_bytes=2000000000000,
            utilization_percent=50.0,
            pgs=200,
            commit_latency_ms=10.0,
            apply_latency_ms=15.0,
        )

    @pytest.fixture
    def sample_osds(self) -> list[OSDInfo]:
        """Create sample OSD list for average calculation."""
        return [
            OSDInfo(osd_id=0, utilization_percent=40.0),
            OSDInfo(osd_id=1, utilization_percent=50.0),
            OSDInfo(osd_id=2, utilization_percent=60.0),
        ]

    @pytest.mark.asyncio
    async def test_get_details_success(
        self,
        mock_kubernetes_adapter: AsyncMock,
        sample_osd_info: OSDInfo,
        sample_osds: list[OSDInfo],
    ) -> None:
        """Test successful OSD details retrieval."""
        mock_ceph = AsyncMock()
        mock_ceph.get_osd_details.return_value = sample_osd_info
        mock_ceph.list_osds.return_value = sample_osds

        with patch(
            "mosk_mcp.tools.ceph_operations.get_osd_details.CephAdapter",
        ) as MockCephAdapter:
            MockCephAdapter.return_value.__aenter__.return_value = mock_ceph
            MockCephAdapter.return_value.__aexit__.return_value = None

            result = await get_osd_details(mock_kubernetes_adapter, osd_id=5)

        assert result.osd.osd_id == 5
        assert result.osd.host == "node1"
        assert result.osd.status == "up"
        assert result.osd.state == "in"
        assert result.osd.device_class == "ssd"
        assert result.osd.capacity.percent_used == 50.0
        assert result.osd.is_healthy is True

    @pytest.mark.asyncio
    async def test_get_details_with_pg_distribution(
        self,
        mock_kubernetes_adapter: AsyncMock,
        sample_osd_info: OSDInfo,
        sample_osds: list[OSDInfo],
    ) -> None:
        """Test OSD details with PG distribution."""
        mock_ceph = AsyncMock()
        mock_ceph.get_osd_details.return_value = sample_osd_info
        mock_ceph.list_osds.return_value = sample_osds

        with patch(
            "mosk_mcp.tools.ceph_operations.get_osd_details.CephAdapter",
        ) as MockCephAdapter:
            MockCephAdapter.return_value.__aenter__.return_value = mock_ceph
            MockCephAdapter.return_value.__aexit__.return_value = None

            result = await get_osd_details(
                mock_kubernetes_adapter, osd_id=5, include_pg_distribution=True
            )

        assert "total" in result.osd.pg_distribution
        assert result.osd.pg_distribution["total"] == 200

    @pytest.mark.asyncio
    async def test_get_details_without_pg_distribution(
        self,
        mock_kubernetes_adapter: AsyncMock,
        sample_osd_info: OSDInfo,
        sample_osds: list[OSDInfo],
    ) -> None:
        """Test OSD details without PG distribution."""
        mock_ceph = AsyncMock()
        mock_ceph.get_osd_details.return_value = sample_osd_info
        mock_ceph.list_osds.return_value = sample_osds

        with patch(
            "mosk_mcp.tools.ceph_operations.get_osd_details.CephAdapter",
        ) as MockCephAdapter:
            MockCephAdapter.return_value.__aenter__.return_value = mock_ceph
            MockCephAdapter.return_value.__aexit__.return_value = None

            result = await get_osd_details(
                mock_kubernetes_adapter, osd_id=5, include_pg_distribution=False
            )

        assert result.osd.pg_distribution == {}

    @pytest.mark.asyncio
    async def test_get_details_unhealthy_osd(
        self,
        mock_kubernetes_adapter: AsyncMock,
        sample_osds: list[OSDInfo],
    ) -> None:
        """Test unhealthy OSD with warnings."""
        unhealthy_osd = OSDInfo(
            osd_id=10,
            host="node2",
            status=OSDStatus.DOWN,
            state=OSDState.OUT,
            utilization_percent=0.0,
            total_bytes=4000000000000,
            used_bytes=0,
            available_bytes=4000000000000,
            pgs=0,
            commit_latency_ms=0.0,
            apply_latency_ms=0.0,
        )

        mock_ceph = AsyncMock()
        mock_ceph.get_osd_details.return_value = unhealthy_osd
        mock_ceph.list_osds.return_value = sample_osds

        with patch(
            "mosk_mcp.tools.ceph_operations.get_osd_details.CephAdapter",
        ) as MockCephAdapter:
            MockCephAdapter.return_value.__aenter__.return_value = mock_ceph
            MockCephAdapter.return_value.__aexit__.return_value = None

            result = await get_osd_details(mock_kubernetes_adapter, osd_id=10)

        assert result.osd.status == "down"
        assert result.osd.state == "out"
        assert result.osd.is_healthy is False
        assert len(result.osd.health_warnings) > 0
        assert len(result.recommendations) > 0

    @pytest.mark.asyncio
    async def test_get_details_capacity_status(
        self,
        mock_kubernetes_adapter: AsyncMock,
        sample_osds: list[OSDInfo],
    ) -> None:
        """Test capacity status calculation."""
        high_util_osd = OSDInfo(
            osd_id=15,
            host="node3",
            status=OSDStatus.UP,
            state=OSDState.IN,
            utilization_percent=90.0,
            total_bytes=4000000000000,
            used_bytes=3600000000000,
            available_bytes=400000000000,
            pgs=300,
        )

        mock_ceph = AsyncMock()
        mock_ceph.get_osd_details.return_value = high_util_osd
        mock_ceph.list_osds.return_value = sample_osds

        with patch(
            "mosk_mcp.tools.ceph_operations.get_osd_details.CephAdapter",
        ) as MockCephAdapter:
            MockCephAdapter.return_value.__aenter__.return_value = mock_ceph
            MockCephAdapter.return_value.__aexit__.return_value = None

            result = await get_osd_details(mock_kubernetes_adapter, osd_id=15)

        assert result.osd.capacity.status == CapacityStatus.EMERGENCY

    @pytest.mark.asyncio
    async def test_get_details_osd_not_found(self, mock_kubernetes_adapter: AsyncMock) -> None:
        """Test OSD not found error."""
        mock_ceph = AsyncMock()
        mock_ceph.get_osd_details.side_effect = ResourceNotFoundError(
            resource_type="OSD",
            resource_id="999",
            message="OSD 999 not found",
        )

        with patch(
            "mosk_mcp.tools.ceph_operations.get_osd_details.CephAdapter",
        ) as MockCephAdapter:
            MockCephAdapter.return_value.__aenter__.return_value = mock_ceph
            MockCephAdapter.return_value.__aexit__.return_value = None

            with pytest.raises(ResourceNotFoundError):
                await get_osd_details(mock_kubernetes_adapter, osd_id=999)

    @pytest.mark.asyncio
    async def test_get_details_error(self, mock_kubernetes_adapter: AsyncMock) -> None:
        """Test error handling."""
        mock_ceph = AsyncMock()
        mock_ceph.get_osd_details.side_effect = Exception("Connection failed")

        with patch(
            "mosk_mcp.tools.ceph_operations.get_osd_details.CephAdapter",
        ) as MockCephAdapter:
            MockCephAdapter.return_value.__aenter__.return_value = mock_ceph
            MockCephAdapter.return_value.__aexit__.return_value = None

            with pytest.raises(ToolExecutionError) as exc_info:
                await get_osd_details(mock_kubernetes_adapter, osd_id=5)

        assert "Failed to get OSD 5 details" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_get_details_empty_osd_list(
        self,
        mock_kubernetes_adapter: AsyncMock,
        sample_osd_info: OSDInfo,
    ) -> None:
        """Test with empty OSD list for average calculation."""
        mock_ceph = AsyncMock()
        mock_ceph.get_osd_details.return_value = sample_osd_info
        mock_ceph.list_osds.return_value = []

        with patch(
            "mosk_mcp.tools.ceph_operations.get_osd_details.CephAdapter",
        ) as MockCephAdapter:
            MockCephAdapter.return_value.__aenter__.return_value = mock_ceph
            MockCephAdapter.return_value.__aexit__.return_value = None

            result = await get_osd_details(mock_kubernetes_adapter, osd_id=5)

        assert result.osd.osd_id == 5
