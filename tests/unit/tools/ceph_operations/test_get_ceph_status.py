"""Unit tests for get_ceph_status tool."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mosk_mcp.adapters.ceph import (
    CAPACITY_CRITICAL_THRESHOLD,
    CAPACITY_EMERGENCY_THRESHOLD,
    CAPACITY_WARNING_THRESHOLD,
    CephHealthStatus,
)
from mosk_mcp.core.exceptions import ToolExecutionError
from mosk_mcp.tools.ceph_operations.get_ceph_status import (
    _capacity_status_to_enum,
    _generate_health_summary,
    _generate_warnings,
    _health_status_to_level,
    _is_safe_for_operations,
    get_ceph_status,
)
from mosk_mcp.tools.ceph_operations.models import CephHealthLevel
from mosk_mcp.tools.common.enums import CapacityStatus


class TestHealthStatusToLevel:
    """Tests for _health_status_to_level function."""

    def test_health_ok(self) -> None:
        """Test HEALTH_OK mapping."""
        assert _health_status_to_level(CephHealthStatus.HEALTH_OK) == CephHealthLevel.HEALTH_OK

    def test_health_warn(self) -> None:
        """Test HEALTH_WARN mapping."""
        assert _health_status_to_level(CephHealthStatus.HEALTH_WARN) == CephHealthLevel.HEALTH_WARN

    def test_health_err(self) -> None:
        """Test HEALTH_ERR mapping."""
        assert _health_status_to_level(CephHealthStatus.HEALTH_ERR) == CephHealthLevel.HEALTH_ERR

    def test_unknown(self) -> None:
        """Test UNKNOWN mapping."""
        assert _health_status_to_level(CephHealthStatus.UNKNOWN) == CephHealthLevel.UNKNOWN


class TestCapacityStatusToEnum:
    """Tests for _capacity_status_to_enum function."""

    def test_normal(self) -> None:
        """Test normal status."""
        assert _capacity_status_to_enum("normal") == CapacityStatus.NORMAL
        assert _capacity_status_to_enum("NORMAL") == CapacityStatus.NORMAL

    def test_warning(self) -> None:
        """Test warning status."""
        assert _capacity_status_to_enum("warning") == CapacityStatus.WARNING

    def test_critical(self) -> None:
        """Test critical status."""
        assert _capacity_status_to_enum("critical") == CapacityStatus.CRITICAL

    def test_emergency(self) -> None:
        """Test emergency status."""
        assert _capacity_status_to_enum("emergency") == CapacityStatus.EMERGENCY

    def test_unknown_defaults_to_normal(self) -> None:
        """Test unknown status defaults to normal."""
        assert _capacity_status_to_enum("unknown") == CapacityStatus.NORMAL


class TestGenerateHealthSummary:
    """Tests for _generate_health_summary function."""

    def test_healthy_cluster(self) -> None:
        """Test summary for healthy cluster."""
        result = _generate_health_summary(
            health=CephHealthStatus.HEALTH_OK,
            num_osds=10,
            num_osds_up=10,
            num_pgs=100,
            pg_states={"active+clean": 100},
            capacity_percent=50.0,
        )
        assert "healthy" in result
        assert "all 10 OSDs up" in result
        assert "all 100 PGs active+clean" in result
        assert "50.0% capacity used" in result

    def test_cluster_with_warnings(self) -> None:
        """Test summary for cluster with warnings."""
        result = _generate_health_summary(
            health=CephHealthStatus.HEALTH_WARN,
            num_osds=10,
            num_osds_up=8,
            num_pgs=100,
            pg_states={"active+clean": 90, "degraded": 10},
            capacity_percent=75.0,
        )
        assert "warnings" in result
        assert "8/10 OSDs up" in result
        assert "90/100 PGs active+clean" in result

    def test_cluster_with_errors(self) -> None:
        """Test summary for cluster with errors."""
        result = _generate_health_summary(
            health=CephHealthStatus.HEALTH_ERR,
            num_osds=10,
            num_osds_up=5,
            num_pgs=100,
            pg_states={"active+clean": 50},
            capacity_percent=90.0,
        )
        assert "errors" in result

    def test_unknown_health(self) -> None:
        """Test summary for unknown health."""
        result = _generate_health_summary(
            health=CephHealthStatus.UNKNOWN,
            num_osds=10,
            num_osds_up=10,
            num_pgs=100,
            pg_states={},
            capacity_percent=50.0,
        )
        assert "unknown" in result


class TestGenerateWarnings:
    """Tests for _generate_warnings function."""

    def test_no_warnings(self) -> None:
        """Test no warnings for healthy cluster."""
        result = _generate_warnings(
            health=CephHealthStatus.HEALTH_OK,
            health_checks={},
            num_osds=10,
            num_osds_up=10,
            num_osds_in=10,
            capacity_percent=50.0,
            pg_states={"active+clean": 100},
        )
        assert result == []

    def test_osds_down_warning(self) -> None:
        """Test OSD down warning."""
        result = _generate_warnings(
            health=CephHealthStatus.HEALTH_WARN,
            health_checks={},
            num_osds=10,
            num_osds_up=8,
            num_osds_in=10,
            capacity_percent=50.0,
            pg_states={},
        )
        assert any("2 OSD(s) are down" in w for w in result)

    def test_osds_out_warning(self) -> None:
        """Test OSD out warning."""
        result = _generate_warnings(
            health=CephHealthStatus.HEALTH_WARN,
            health_checks={},
            num_osds=10,
            num_osds_up=10,
            num_osds_in=8,
            capacity_percent=50.0,
            pg_states={},
        )
        assert any("2 OSD(s) are out" in w for w in result)

    def test_emergency_capacity_warning(self) -> None:
        """Test emergency capacity warning."""
        result = _generate_warnings(
            health=CephHealthStatus.HEALTH_ERR,
            health_checks={},
            num_osds=10,
            num_osds_up=10,
            num_osds_in=10,
            capacity_percent=CAPACITY_EMERGENCY_THRESHOLD + 1,
            pg_states={},
        )
        assert any("EMERGENCY" in w for w in result)

    def test_critical_capacity_warning(self) -> None:
        """Test critical capacity warning."""
        result = _generate_warnings(
            health=CephHealthStatus.HEALTH_WARN,
            health_checks={},
            num_osds=10,
            num_osds_up=10,
            num_osds_in=10,
            capacity_percent=CAPACITY_CRITICAL_THRESHOLD + 1,
            pg_states={},
        )
        assert any("CRITICAL" in w for w in result)

    def test_warning_capacity(self) -> None:
        """Test warning capacity."""
        result = _generate_warnings(
            health=CephHealthStatus.HEALTH_WARN,
            health_checks={},
            num_osds=10,
            num_osds_up=10,
            num_osds_in=10,
            capacity_percent=CAPACITY_WARNING_THRESHOLD + 1,
            pg_states={},
        )
        assert any("WARNING" in w for w in result)

    def test_degraded_pg_warning(self) -> None:
        """Test degraded PG warning."""
        result = _generate_warnings(
            health=CephHealthStatus.HEALTH_WARN,
            health_checks={},
            num_osds=10,
            num_osds_up=10,
            num_osds_in=10,
            capacity_percent=50.0,
            pg_states={"active+clean": 90, "active+degraded": 10},
        )
        assert any("degraded" in w for w in result)

    def test_undersized_pg_warning(self) -> None:
        """Test undersized PG warning."""
        result = _generate_warnings(
            health=CephHealthStatus.HEALTH_WARN,
            health_checks={},
            num_osds=10,
            num_osds_up=10,
            num_osds_in=10,
            capacity_percent=50.0,
            pg_states={"active+clean": 90, "active+undersized": 10},
        )
        assert any("undersized" in w for w in result)

    def test_stale_pg_warning(self) -> None:
        """Test stale PG warning."""
        result = _generate_warnings(
            health=CephHealthStatus.HEALTH_ERR,
            health_checks={},
            num_osds=10,
            num_osds_up=10,
            num_osds_in=10,
            capacity_percent=50.0,
            pg_states={"active+clean": 90, "stale": 10},
        )
        assert any("stale" in w for w in result)

    def test_recovering_pg_warning(self) -> None:
        """Test recovering PG warning."""
        result = _generate_warnings(
            health=CephHealthStatus.HEALTH_WARN,
            health_checks={},
            num_osds=10,
            num_osds_up=10,
            num_osds_in=10,
            capacity_percent=50.0,
            pg_states={"active+clean": 90, "active+recovering": 10},
        )
        assert any("recovering" in w for w in result)

    def test_health_check_error(self) -> None:
        """Test health check error warning."""
        result = _generate_warnings(
            health=CephHealthStatus.HEALTH_ERR,
            health_checks={
                "MON_DOWN": {
                    "severity": "HEALTH_ERR",
                    "summary": {"message": "1 mon down"},
                }
            },
            num_osds=10,
            num_osds_up=10,
            num_osds_in=10,
            capacity_percent=50.0,
            pg_states={},
        )
        assert any("ERROR" in w for w in result)

    def test_health_check_warn(self) -> None:
        """Test health check warning."""
        result = _generate_warnings(
            health=CephHealthStatus.HEALTH_WARN,
            health_checks={
                "SLOW_OPS": {
                    "severity": "HEALTH_WARN",
                    "summary": {"message": "Slow operations detected"},
                }
            },
            num_osds=10,
            num_osds_up=10,
            num_osds_in=10,
            capacity_percent=50.0,
            pg_states={},
        )
        assert any("WARN" in w for w in result)


class TestIsSafeForOperations:
    """Tests for _is_safe_for_operations function."""

    def test_healthy_cluster_is_safe(self) -> None:
        """Test healthy cluster is safe."""
        result = _is_safe_for_operations(
            health=CephHealthStatus.HEALTH_OK,
            num_osds=10,
            num_osds_up=10,
            capacity_percent=50.0,
            pg_states={"active+clean": 100},
        )
        assert result is True

    def test_health_error_not_safe(self) -> None:
        """Test HEALTH_ERR is not safe."""
        result = _is_safe_for_operations(
            health=CephHealthStatus.HEALTH_ERR,
            num_osds=10,
            num_osds_up=10,
            capacity_percent=50.0,
            pg_states={},
        )
        assert result is False

    def test_osds_down_not_safe(self) -> None:
        """Test OSDs down is not safe."""
        result = _is_safe_for_operations(
            health=CephHealthStatus.HEALTH_WARN,
            num_osds=10,
            num_osds_up=8,
            capacity_percent=50.0,
            pg_states={},
        )
        assert result is False

    def test_critical_capacity_not_safe(self) -> None:
        """Test critical capacity is not safe."""
        result = _is_safe_for_operations(
            health=CephHealthStatus.HEALTH_WARN,
            num_osds=10,
            num_osds_up=10,
            capacity_percent=CAPACITY_CRITICAL_THRESHOLD + 1,
            pg_states={},
        )
        assert result is False

    def test_stale_pgs_not_safe(self) -> None:
        """Test stale PGs is not safe."""
        result = _is_safe_for_operations(
            health=CephHealthStatus.HEALTH_WARN,
            num_osds=10,
            num_osds_up=10,
            capacity_percent=50.0,
            pg_states={"active+clean": 90, "stale": 10},
        )
        assert result is False

    def test_incomplete_pgs_not_safe(self) -> None:
        """Test incomplete PGs is not safe."""
        result = _is_safe_for_operations(
            health=CephHealthStatus.HEALTH_WARN,
            num_osds=10,
            num_osds_up=10,
            capacity_percent=50.0,
            pg_states={"active+clean": 90, "incomplete": 10},
        )
        assert result is False

    def test_undersized_pgs_not_safe(self) -> None:
        """Test undersized PGs is not safe."""
        result = _is_safe_for_operations(
            health=CephHealthStatus.HEALTH_WARN,
            num_osds=10,
            num_osds_up=10,
            capacity_percent=50.0,
            pg_states={"active+clean": 90, "active+undersized": 10},
        )
        assert result is False


class TestGetCephStatus:
    """Tests for get_ceph_status function."""

    @pytest.fixture
    def mock_kubernetes_adapter(self) -> AsyncMock:
        """Create mock Kubernetes adapter."""
        return AsyncMock()

    @pytest.fixture
    def mock_cluster_status(self) -> MagicMock:
        """Create mock cluster status."""
        status = MagicMock()
        status.health = CephHealthStatus.HEALTH_OK
        status.health_checks = {}
        status.fsid = "abc-123"
        status.quorum = ["mon1", "mon2", "mon3"]
        status.num_osds = 10
        status.num_osds_up = 10
        status.num_osds_in = 10
        status.num_pgs = 100
        status.pg_states = {"active+clean": 100}
        status.total_bytes = 1000000000
        status.used_bytes = 500000000
        status.available_bytes = 500000000
        status.capacity_percent = 50.0
        status.capacity_status = "normal"
        status.is_healthy = True
        status.timestamp = datetime.now(UTC)
        return status

    @pytest.mark.asyncio
    async def test_get_status_success(
        self, mock_kubernetes_adapter: AsyncMock, mock_cluster_status: MagicMock
    ) -> None:
        """Test successful status retrieval."""
        mock_ceph_adapter = AsyncMock()
        mock_ceph_adapter.get_cluster_status.return_value = mock_cluster_status

        with patch(
            "mosk_mcp.tools.ceph_operations.get_ceph_status.CephAdapter",
        ) as MockCephAdapter:
            MockCephAdapter.return_value.__aenter__.return_value = mock_ceph_adapter
            MockCephAdapter.return_value.__aexit__.return_value = None

            result = await get_ceph_status(mock_kubernetes_adapter)

        assert result.health == CephHealthLevel.HEALTH_OK
        assert result.is_healthy is True
        assert result.num_osds == 10
        assert result.capacity.percent_used == 50.0

    @pytest.mark.asyncio
    async def test_get_status_with_health_details(
        self, mock_kubernetes_adapter: AsyncMock, mock_cluster_status: MagicMock
    ) -> None:
        """Test status with health details."""
        mock_cluster_status.health_checks = {
            "SLOW_OPS": {
                "severity": "HEALTH_WARN",
                "summary": {"message": "Slow operations"},
                "count": 5,
            }
        }
        mock_ceph_adapter = AsyncMock()
        mock_ceph_adapter.get_cluster_status.return_value = mock_cluster_status

        with patch(
            "mosk_mcp.tools.ceph_operations.get_ceph_status.CephAdapter",
        ) as MockCephAdapter:
            MockCephAdapter.return_value.__aenter__.return_value = mock_ceph_adapter
            MockCephAdapter.return_value.__aexit__.return_value = None

            result = await get_ceph_status(mock_kubernetes_adapter, include_health_details=True)

        assert "SLOW_OPS" in result.health_checks
        assert result.health_checks["SLOW_OPS"].severity == "HEALTH_WARN"

    @pytest.mark.asyncio
    async def test_get_status_without_health_details(
        self, mock_kubernetes_adapter: AsyncMock, mock_cluster_status: MagicMock
    ) -> None:
        """Test status without health details."""
        mock_cluster_status.health_checks = {"SLOW_OPS": {"severity": "HEALTH_WARN"}}
        mock_ceph_adapter = AsyncMock()
        mock_ceph_adapter.get_cluster_status.return_value = mock_cluster_status

        with patch(
            "mosk_mcp.tools.ceph_operations.get_ceph_status.CephAdapter",
        ) as MockCephAdapter:
            MockCephAdapter.return_value.__aenter__.return_value = mock_ceph_adapter
            MockCephAdapter.return_value.__aexit__.return_value = None

            result = await get_ceph_status(mock_kubernetes_adapter, include_health_details=False)

        assert result.health_checks == {}

    @pytest.mark.asyncio
    async def test_get_status_with_pg_summary(
        self, mock_kubernetes_adapter: AsyncMock, mock_cluster_status: MagicMock
    ) -> None:
        """Test status with PG summary."""
        mock_ceph_adapter = AsyncMock()
        mock_ceph_adapter.get_cluster_status.return_value = mock_cluster_status

        with patch(
            "mosk_mcp.tools.ceph_operations.get_ceph_status.CephAdapter",
        ) as MockCephAdapter:
            MockCephAdapter.return_value.__aenter__.return_value = mock_ceph_adapter
            MockCephAdapter.return_value.__aexit__.return_value = None

            result = await get_ceph_status(mock_kubernetes_adapter, include_pg_summary=True)

        assert result.pg_summary == {"active+clean": 100}

    @pytest.mark.asyncio
    async def test_get_status_without_pg_summary(
        self, mock_kubernetes_adapter: AsyncMock, mock_cluster_status: MagicMock
    ) -> None:
        """Test status without PG summary."""
        mock_ceph_adapter = AsyncMock()
        mock_ceph_adapter.get_cluster_status.return_value = mock_cluster_status

        with patch(
            "mosk_mcp.tools.ceph_operations.get_ceph_status.CephAdapter",
        ) as MockCephAdapter:
            MockCephAdapter.return_value.__aenter__.return_value = mock_ceph_adapter
            MockCephAdapter.return_value.__aexit__.return_value = None

            result = await get_ceph_status(mock_kubernetes_adapter, include_pg_summary=False)

        assert result.pg_summary == {}

    @pytest.mark.asyncio
    async def test_get_status_unhealthy_cluster(
        self, mock_kubernetes_adapter: AsyncMock, mock_cluster_status: MagicMock
    ) -> None:
        """Test status for unhealthy cluster."""
        mock_cluster_status.health = CephHealthStatus.HEALTH_ERR
        mock_cluster_status.is_healthy = False
        mock_cluster_status.num_osds_up = 8
        mock_ceph_adapter = AsyncMock()
        mock_ceph_adapter.get_cluster_status.return_value = mock_cluster_status

        with patch(
            "mosk_mcp.tools.ceph_operations.get_ceph_status.CephAdapter",
        ) as MockCephAdapter:
            MockCephAdapter.return_value.__aenter__.return_value = mock_ceph_adapter
            MockCephAdapter.return_value.__aexit__.return_value = None

            result = await get_ceph_status(mock_kubernetes_adapter)

        assert result.health == CephHealthLevel.HEALTH_ERR
        assert result.is_healthy is False
        assert result.is_safe_for_operations is False
        assert len(result.warnings) > 0

    @pytest.mark.asyncio
    async def test_get_status_error(self, mock_kubernetes_adapter: AsyncMock) -> None:
        """Test error handling."""
        mock_ceph_adapter = AsyncMock()
        mock_ceph_adapter.get_cluster_status.side_effect = Exception("Connection failed")

        with patch(
            "mosk_mcp.tools.ceph_operations.get_ceph_status.CephAdapter",
        ) as MockCephAdapter:
            MockCephAdapter.return_value.__aenter__.return_value = mock_ceph_adapter
            MockCephAdapter.return_value.__aexit__.return_value = None

            with pytest.raises(ToolExecutionError) as exc_info:
                await get_ceph_status(mock_kubernetes_adapter)

        assert "Failed to get Ceph status" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_get_status_tool_error_passthrough(
        self, mock_kubernetes_adapter: AsyncMock
    ) -> None:
        """Test ToolExecutionError is passed through."""
        mock_ceph_adapter = AsyncMock()
        mock_ceph_adapter.get_cluster_status.side_effect = ToolExecutionError(
            message="Pod not found",
            tool_name="get_ceph_status",
        )

        with patch(
            "mosk_mcp.tools.ceph_operations.get_ceph_status.CephAdapter",
        ) as MockCephAdapter:
            MockCephAdapter.return_value.__aenter__.return_value = mock_ceph_adapter
            MockCephAdapter.return_value.__aexit__.return_value = None

            with pytest.raises(ToolExecutionError) as exc_info:
                await get_ceph_status(mock_kubernetes_adapter)

        assert "Pod not found" in str(exc_info.value)
