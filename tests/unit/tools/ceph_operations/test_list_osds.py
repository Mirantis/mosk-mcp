"""Unit tests for list_osds tool."""

from unittest.mock import AsyncMock, patch

import pytest

from mosk_mcp.adapters.ceph import OSDInfo, OSDState, OSDStatus
from mosk_mcp.core.exceptions import ToolExecutionError
from mosk_mcp.tools.ceph_operations.list_osds import (
    _aggregate_by_device_class,
    _aggregate_by_host,
    _filter_osds,
    _osd_to_summary,
    list_osds,
)


class TestOsdToSummary:
    """Tests for _osd_to_summary function."""

    def test_converts_osd_info_to_summary(self) -> None:
        """Test basic OSD info to summary conversion."""
        osd = OSDInfo(
            osd_id=0,
            host="node1",
            status=OSDStatus.UP,
            state=OSDState.IN,
            device_class="ssd",
            utilization_percent=45.5,
            total_bytes=1000000000,
            used_bytes=455000000,
            pgs=100,
        )

        result = _osd_to_summary(osd)

        assert result.osd_id == 0
        assert result.host == "node1"
        assert result.status == "up"
        assert result.state == "in"
        assert result.device_class == "ssd"
        assert result.utilization_percent == 45.5
        assert result.capacity_bytes == 1000000000
        assert result.used_bytes == 455000000
        assert result.pgs == 100
        assert result.is_healthy is True

    def test_converts_down_osd(self) -> None:
        """Test conversion of down OSD."""
        osd = OSDInfo(
            osd_id=1,
            host="node2",
            status=OSDStatus.DOWN,
            state=OSDState.OUT,
            device_class="hdd",
            utilization_percent=0.0,
            total_bytes=2000000000,
            used_bytes=0,
            pgs=0,
        )

        result = _osd_to_summary(osd)

        assert result.osd_id == 1
        assert result.status == "down"
        assert result.state == "out"
        assert result.is_healthy is False

    def test_handles_unknown_status(self) -> None:
        """Test handling of unknown status."""
        osd = OSDInfo(
            osd_id=2,
            host="node3",
            status=OSDStatus.UNKNOWN,
            state=OSDState.IN,
        )

        result = _osd_to_summary(osd)

        assert result.status == "unknown"


class TestFilterOsds:
    """Tests for _filter_osds function."""

    @pytest.fixture
    def sample_osds(self) -> list[OSDInfo]:
        """Create sample OSD list for testing."""
        return [
            OSDInfo(osd_id=0, host="node1", status=OSDStatus.UP, state=OSDState.IN),
            OSDInfo(osd_id=1, host="node1", status=OSDStatus.UP, state=OSDState.IN),
            OSDInfo(osd_id=2, host="node2", status=OSDStatus.UP, state=OSDState.IN),
            OSDInfo(osd_id=3, host="node2", status=OSDStatus.DOWN, state=OSDState.OUT),
            OSDInfo(osd_id=4, host="storage-node", status=OSDStatus.UP, state=OSDState.IN),
        ]

    def test_no_filter_returns_all(self, sample_osds: list[OSDInfo]) -> None:
        """Test that no filter returns all OSDs."""
        result = _filter_osds(sample_osds)

        assert len(result) == 5

    def test_filter_by_host_exact_match(self, sample_osds: list[OSDInfo]) -> None:
        """Test filtering by exact host name."""
        result = _filter_osds(sample_osds, host_filter="node1")

        assert len(result) == 2
        assert all(o.host == "node1" for o in result)

    def test_filter_by_host_substring(self, sample_osds: list[OSDInfo]) -> None:
        """Test filtering by host substring."""
        result = _filter_osds(sample_osds, host_filter="node")

        # All nodes contain "node"
        assert len(result) == 5

    def test_filter_by_host_case_insensitive(self, sample_osds: list[OSDInfo]) -> None:
        """Test that host filter is case insensitive."""
        result = _filter_osds(sample_osds, host_filter="NODE1")

        assert len(result) == 2

    def test_filter_by_status_up(self, sample_osds: list[OSDInfo]) -> None:
        """Test filtering by up status."""
        result = _filter_osds(sample_osds, status_filter="up")

        assert len(result) == 4
        assert all(o.status == OSDStatus.UP for o in result)

    def test_filter_by_status_down(self, sample_osds: list[OSDInfo]) -> None:
        """Test filtering by down status."""
        result = _filter_osds(sample_osds, status_filter="down")

        assert len(result) == 1
        assert result[0].osd_id == 3

    def test_filter_by_status_all(self, sample_osds: list[OSDInfo]) -> None:
        """Test filtering by 'all' status returns everything."""
        result = _filter_osds(sample_osds, status_filter="all")

        assert len(result) == 5

    def test_combined_filters(self, sample_osds: list[OSDInfo]) -> None:
        """Test combining host and status filters."""
        result = _filter_osds(sample_osds, host_filter="node2", status_filter="up")

        assert len(result) == 1
        assert result[0].osd_id == 2

    def test_no_matches(self, sample_osds: list[OSDInfo]) -> None:
        """Test filter with no matches."""
        result = _filter_osds(sample_osds, host_filter="nonexistent")

        assert len(result) == 0


class TestAggregateByHost:
    """Tests for _aggregate_by_host function."""

    def test_aggregates_by_host(self) -> None:
        """Test aggregation by host."""
        osds = [
            OSDInfo(osd_id=0, host="node1"),
            OSDInfo(osd_id=1, host="node1"),
            OSDInfo(osd_id=2, host="node2"),
            OSDInfo(osd_id=3, host="node2"),
            OSDInfo(osd_id=4, host="node2"),
        ]

        result = _aggregate_by_host(osds)

        assert result["node1"] == 2
        assert result["node2"] == 3

    def test_single_host(self) -> None:
        """Test aggregation with single host."""
        osds = [OSDInfo(osd_id=0, host="node1")]

        result = _aggregate_by_host(osds)

        assert result == {"node1": 1}

    def test_empty_list(self) -> None:
        """Test aggregation with empty list."""
        result = _aggregate_by_host([])

        assert result == {}


class TestAggregateByDeviceClass:
    """Tests for _aggregate_by_device_class function."""

    def test_aggregates_by_device_class(self) -> None:
        """Test aggregation by device class."""
        osds = [
            OSDInfo(osd_id=0, device_class="ssd"),
            OSDInfo(osd_id=1, device_class="ssd"),
            OSDInfo(osd_id=2, device_class="hdd"),
            OSDInfo(osd_id=3, device_class="nvme"),
        ]

        result = _aggregate_by_device_class(osds)

        assert result["ssd"] == 2
        assert result["hdd"] == 1
        assert result["nvme"] == 1

    def test_unknown_device_class(self) -> None:
        """Test aggregation with empty device class."""
        osds = [
            OSDInfo(osd_id=0, device_class="ssd"),
            OSDInfo(osd_id=1, device_class=""),
            OSDInfo(osd_id=2),  # No device class set
        ]

        result = _aggregate_by_device_class(osds)

        assert result["ssd"] == 1
        assert result["unknown"] == 2

    def test_empty_list(self) -> None:
        """Test aggregation with empty list."""
        result = _aggregate_by_device_class([])

        assert result == {}


class TestListOsds:
    """Tests for list_osds function."""

    @pytest.fixture
    def mock_kubernetes_adapter(self) -> AsyncMock:
        """Create mock Kubernetes adapter."""
        return AsyncMock()

    @pytest.fixture
    def sample_osds(self) -> list[OSDInfo]:
        """Create sample OSD list for testing."""
        return [
            OSDInfo(
                osd_id=0,
                host="node1",
                status=OSDStatus.UP,
                state=OSDState.IN,
                device_class="ssd",
                utilization_percent=50.0,
                total_bytes=1000000000,
                used_bytes=500000000,
                pgs=100,
            ),
            OSDInfo(
                osd_id=1,
                host="node1",
                status=OSDStatus.UP,
                state=OSDState.IN,
                device_class="ssd",
                utilization_percent=60.0,
                total_bytes=1000000000,
                used_bytes=600000000,
                pgs=120,
            ),
            OSDInfo(
                osd_id=2,
                host="node2",
                status=OSDStatus.DOWN,
                state=OSDState.OUT,
                device_class="hdd",
                utilization_percent=0.0,
                total_bytes=2000000000,
                used_bytes=0,
                pgs=0,
            ),
        ]

    @pytest.mark.asyncio
    async def test_list_osds_success(
        self, mock_kubernetes_adapter: AsyncMock, sample_osds: list[OSDInfo]
    ) -> None:
        """Test successful OSD listing."""
        mock_ceph = AsyncMock()
        mock_ceph.list_osds.return_value = sample_osds

        with patch(
            "mosk_mcp.tools.ceph_operations.list_osds.CephAdapter",
        ) as MockCephAdapter:
            MockCephAdapter.return_value.__aenter__.return_value = mock_ceph
            MockCephAdapter.return_value.__aexit__.return_value = None

            result = await list_osds(mock_kubernetes_adapter)

        assert result.total_count == 3
        assert result.up_count == 2
        assert result.down_count == 1
        assert result.in_count == 2
        assert result.out_count == 1
        assert len(result.osds) == 3

    @pytest.mark.asyncio
    async def test_list_osds_sorted_by_id(self, mock_kubernetes_adapter: AsyncMock) -> None:
        """Test OSDs are sorted by ID."""
        osds = [
            OSDInfo(osd_id=2, host="node1", status=OSDStatus.UP, state=OSDState.IN),
            OSDInfo(osd_id=0, host="node1", status=OSDStatus.UP, state=OSDState.IN),
            OSDInfo(osd_id=1, host="node1", status=OSDStatus.UP, state=OSDState.IN),
        ]

        mock_ceph = AsyncMock()
        mock_ceph.list_osds.return_value = osds

        with patch(
            "mosk_mcp.tools.ceph_operations.list_osds.CephAdapter",
        ) as MockCephAdapter:
            MockCephAdapter.return_value.__aenter__.return_value = mock_ceph
            MockCephAdapter.return_value.__aexit__.return_value = None

            result = await list_osds(mock_kubernetes_adapter)

        assert [o.osd_id for o in result.osds] == [0, 1, 2]

    @pytest.mark.asyncio
    async def test_list_osds_with_host_filter(
        self, mock_kubernetes_adapter: AsyncMock, sample_osds: list[OSDInfo]
    ) -> None:
        """Test OSD listing with host filter."""
        mock_ceph = AsyncMock()
        mock_ceph.list_osds.return_value = sample_osds

        with patch(
            "mosk_mcp.tools.ceph_operations.list_osds.CephAdapter",
        ) as MockCephAdapter:
            MockCephAdapter.return_value.__aenter__.return_value = mock_ceph
            MockCephAdapter.return_value.__aexit__.return_value = None

            result = await list_osds(mock_kubernetes_adapter, host_filter="node1")

        # Only node1 OSDs returned, but total_count is from all OSDs
        assert len(result.osds) == 2
        assert result.total_count == 3
        assert all(o.host == "node1" for o in result.osds)

    @pytest.mark.asyncio
    async def test_list_osds_with_status_filter(
        self, mock_kubernetes_adapter: AsyncMock, sample_osds: list[OSDInfo]
    ) -> None:
        """Test OSD listing with status filter."""
        mock_ceph = AsyncMock()
        mock_ceph.list_osds.return_value = sample_osds

        with patch(
            "mosk_mcp.tools.ceph_operations.list_osds.CephAdapter",
        ) as MockCephAdapter:
            MockCephAdapter.return_value.__aenter__.return_value = mock_ceph
            MockCephAdapter.return_value.__aexit__.return_value = None

            result = await list_osds(mock_kubernetes_adapter, status_filter="up")

        assert len(result.osds) == 2
        assert all(o.status == "up" for o in result.osds)

    @pytest.mark.asyncio
    async def test_list_osds_aggregations(
        self, mock_kubernetes_adapter: AsyncMock, sample_osds: list[OSDInfo]
    ) -> None:
        """Test OSD aggregation statistics."""
        mock_ceph = AsyncMock()
        mock_ceph.list_osds.return_value = sample_osds

        with patch(
            "mosk_mcp.tools.ceph_operations.list_osds.CephAdapter",
        ) as MockCephAdapter:
            MockCephAdapter.return_value.__aenter__.return_value = mock_ceph
            MockCephAdapter.return_value.__aexit__.return_value = None

            result = await list_osds(mock_kubernetes_adapter)

        assert result.by_host == {"node1": 2, "node2": 1}
        assert result.by_device_class == {"ssd": 2, "hdd": 1}

    @pytest.mark.asyncio
    async def test_list_osds_empty(self, mock_kubernetes_adapter: AsyncMock) -> None:
        """Test OSD listing with no OSDs."""
        mock_ceph = AsyncMock()
        mock_ceph.list_osds.return_value = []

        with patch(
            "mosk_mcp.tools.ceph_operations.list_osds.CephAdapter",
        ) as MockCephAdapter:
            MockCephAdapter.return_value.__aenter__.return_value = mock_ceph
            MockCephAdapter.return_value.__aexit__.return_value = None

            result = await list_osds(mock_kubernetes_adapter)

        assert result.total_count == 0
        assert result.up_count == 0
        assert result.down_count == 0
        assert result.osds == []
        assert result.by_host == {}
        assert result.by_device_class == {}

    @pytest.mark.asyncio
    async def test_list_osds_error(self, mock_kubernetes_adapter: AsyncMock) -> None:
        """Test error handling."""
        mock_ceph = AsyncMock()
        mock_ceph.list_osds.side_effect = Exception("Connection failed")

        with patch(
            "mosk_mcp.tools.ceph_operations.list_osds.CephAdapter",
        ) as MockCephAdapter:
            MockCephAdapter.return_value.__aenter__.return_value = mock_ceph
            MockCephAdapter.return_value.__aexit__.return_value = None

            with pytest.raises(ToolExecutionError) as exc_info:
                await list_osds(mock_kubernetes_adapter)

        assert "Failed to list OSDs" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_list_osds_all_down(self, mock_kubernetes_adapter: AsyncMock) -> None:
        """Test listing when all OSDs are down."""
        osds = [
            OSDInfo(osd_id=0, host="node1", status=OSDStatus.DOWN, state=OSDState.OUT),
            OSDInfo(osd_id=1, host="node1", status=OSDStatus.DOWN, state=OSDState.OUT),
        ]

        mock_ceph = AsyncMock()
        mock_ceph.list_osds.return_value = osds

        with patch(
            "mosk_mcp.tools.ceph_operations.list_osds.CephAdapter",
        ) as MockCephAdapter:
            MockCephAdapter.return_value.__aenter__.return_value = mock_ceph
            MockCephAdapter.return_value.__aexit__.return_value = None

            result = await list_osds(mock_kubernetes_adapter)

        assert result.total_count == 2
        assert result.up_count == 0
        assert result.down_count == 2
        assert result.in_count == 0
        assert result.out_count == 2
