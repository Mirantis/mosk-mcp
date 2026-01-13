"""Unit tests for get_ceph_health tool."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mosk_mcp.adapters.ceph import CephHealthStatus
from mosk_mcp.tools.cluster_health.get_ceph_health import (
    _calculate_ceph_score,
    _capacity_status,
    _generate_recommendations,
    _health_status_to_string,
    get_ceph_health,
)
from mosk_mcp.tools.cluster_health.models import GetCephHealthInput
from mosk_mcp.tools.common.enums import HealthStatus


class TestCalculateCephScore:
    """Tests for _calculate_ceph_score function."""

    def test_healthy_cluster(self) -> None:
        """Test score for healthy cluster."""
        score = _calculate_ceph_score(
            health_status=CephHealthStatus.HEALTH_OK,
            osds_total=10,
            osds_up=10,
            osds_in=10,
            pgs_total=100,
            pgs_active_clean=100,
            pgs_degraded=0,
            capacity_percent=50.0,
        )
        assert score == 100  # 25+30+25+20

    def test_health_warn_reduces_score(self) -> None:
        """Test health warning reduces score."""
        score = _calculate_ceph_score(
            health_status=CephHealthStatus.HEALTH_WARN,
            osds_total=10,
            osds_up=10,
            osds_in=10,
            pgs_total=100,
            pgs_active_clean=100,
            pgs_degraded=0,
            capacity_percent=50.0,
        )
        assert score == 90  # 15+30+25+20

    def test_health_err_reduces_score(self) -> None:
        """Test health error severely reduces score."""
        score = _calculate_ceph_score(
            health_status=CephHealthStatus.HEALTH_ERR,
            osds_total=10,
            osds_up=10,
            osds_in=10,
            pgs_total=100,
            pgs_active_clean=100,
            pgs_degraded=0,
            capacity_percent=50.0,
        )
        assert score == 75  # 0+30+25+20

    def test_osd_down_reduces_score(self) -> None:
        """Test OSDs down reduces score."""
        score = _calculate_ceph_score(
            health_status=CephHealthStatus.HEALTH_OK,
            osds_total=10,
            osds_up=8,
            osds_in=10,
            pgs_total=100,
            pgs_active_clean=100,
            pgs_degraded=0,
            capacity_percent=50.0,
        )
        # OSD score: min(8,10)/10 * 30 = 24
        assert score == 94  # 25+24+25+20

    def test_osd_out_reduces_score(self) -> None:
        """Test OSDs out reduces score."""
        score = _calculate_ceph_score(
            health_status=CephHealthStatus.HEALTH_OK,
            osds_total=10,
            osds_up=10,
            osds_in=8,
            pgs_total=100,
            pgs_active_clean=100,
            pgs_degraded=0,
            capacity_percent=50.0,
        )
        # OSD score: min(10,8)/10 * 30 = 24
        assert score == 94  # 25+24+25+20

    def test_no_osds(self) -> None:
        """Test cluster with no OSDs."""
        score = _calculate_ceph_score(
            health_status=CephHealthStatus.HEALTH_OK,
            osds_total=0,
            osds_up=0,
            osds_in=0,
            pgs_total=0,
            pgs_active_clean=0,
            pgs_degraded=0,
            capacity_percent=0.0,
        )
        # No OSDs = 0 OSD points, no PGs = 25 PG points
        assert score == 70  # 25+0+25+20

    def test_degraded_pgs_reduce_score(self) -> None:
        """Test degraded PGs reduce score."""
        score = _calculate_ceph_score(
            health_status=CephHealthStatus.HEALTH_OK,
            osds_total=10,
            osds_up=10,
            osds_in=10,
            pgs_total=100,
            pgs_active_clean=80,
            pgs_degraded=20,
            capacity_percent=50.0,
        )
        # PG score: max(0, 0.8 - 0.1) * 25 = 17
        assert score < 100

    def test_high_capacity_reduces_score(self) -> None:
        """Test high capacity reduces score."""
        score_normal = _calculate_ceph_score(
            health_status=CephHealthStatus.HEALTH_OK,
            osds_total=10,
            osds_up=10,
            osds_in=10,
            pgs_total=100,
            pgs_active_clean=100,
            pgs_degraded=0,
            capacity_percent=50.0,
        )
        score_warning = _calculate_ceph_score(
            health_status=CephHealthStatus.HEALTH_OK,
            osds_total=10,
            osds_up=10,
            osds_in=10,
            pgs_total=100,
            pgs_active_clean=100,
            pgs_degraded=0,
            capacity_percent=75.0,  # Between 70-80 (warning threshold)
        )
        score_critical = _calculate_ceph_score(
            health_status=CephHealthStatus.HEALTH_OK,
            osds_total=10,
            osds_up=10,
            osds_in=10,
            pgs_total=100,
            pgs_active_clean=100,
            pgs_degraded=0,
            capacity_percent=85.0,  # Between 80-95 (critical threshold)
        )

        assert score_normal > score_warning > score_critical

    def test_unknown_health_status(self) -> None:
        """Test unknown health status."""
        score = _calculate_ceph_score(
            health_status=CephHealthStatus.UNKNOWN,
            osds_total=10,
            osds_up=10,
            osds_in=10,
            pgs_total=100,
            pgs_active_clean=100,
            pgs_degraded=0,
            capacity_percent=50.0,
        )
        assert score == 85  # 10+30+25+20

    def test_score_clamped(self) -> None:
        """Test score is clamped to 0-100."""
        score = _calculate_ceph_score(
            health_status=CephHealthStatus.HEALTH_ERR,
            osds_total=10,
            osds_up=0,
            osds_in=0,
            pgs_total=100,
            pgs_active_clean=0,
            pgs_degraded=100,
            capacity_percent=98.0,
        )
        assert 0 <= score <= 100


class TestCapacityStatus:
    """Tests for _capacity_status function."""

    def test_normal_capacity(self) -> None:
        """Test normal capacity status."""
        assert _capacity_status(50.0) == "normal"
        assert _capacity_status(69.0) == "normal"

    def test_warning_capacity(self) -> None:
        """Test warning capacity status."""
        assert _capacity_status(70.0) == "warning"
        assert _capacity_status(79.0) == "warning"

    def test_critical_capacity(self) -> None:
        """Test critical capacity status."""
        assert _capacity_status(80.0) == "critical"
        assert _capacity_status(94.0) == "critical"

    def test_emergency_capacity(self) -> None:
        """Test emergency capacity status."""
        assert _capacity_status(95.0) == "emergency"
        assert _capacity_status(99.0) == "emergency"


class TestHealthStatusToString:
    """Tests for _health_status_to_string function."""

    def test_health_ok(self) -> None:
        """Test HEALTH_OK conversion."""
        assert _health_status_to_string(CephHealthStatus.HEALTH_OK) == "HEALTH_OK"

    def test_health_warn(self) -> None:
        """Test HEALTH_WARN conversion."""
        assert _health_status_to_string(CephHealthStatus.HEALTH_WARN) == "HEALTH_WARN"

    def test_health_err(self) -> None:
        """Test HEALTH_ERR conversion."""
        assert _health_status_to_string(CephHealthStatus.HEALTH_ERR) == "HEALTH_ERR"

    def test_unknown(self) -> None:
        """Test UNKNOWN conversion."""
        assert _health_status_to_string(CephHealthStatus.UNKNOWN) == "UNKNOWN"


class TestGenerateRecommendations:
    """Tests for _generate_recommendations function."""

    def test_healthy_cluster(self) -> None:
        """Test healthy cluster has no recommendations."""
        result = _generate_recommendations(
            health_status=CephHealthStatus.HEALTH_OK,
            osds_total=10,
            osds_up=10,
            osds_in=10,
            pgs_degraded=0,
            pgs_recovering=0,
            capacity_percent=50.0,
            is_recovering=False,
            health_checks={},
        )
        assert result == []

    def test_osds_down_recommendation(self) -> None:
        """Test OSDs down recommendation."""
        result = _generate_recommendations(
            health_status=CephHealthStatus.HEALTH_WARN,
            osds_total=10,
            osds_up=8,
            osds_in=10,
            pgs_degraded=0,
            pgs_recovering=0,
            capacity_percent=50.0,
            is_recovering=False,
            health_checks={},
        )
        assert any("OSD(s) are down" in r for r in result)

    def test_osds_out_recommendation(self) -> None:
        """Test OSDs out recommendation."""
        result = _generate_recommendations(
            health_status=CephHealthStatus.HEALTH_WARN,
            osds_total=10,
            osds_up=10,
            osds_in=8,
            pgs_degraded=0,
            pgs_recovering=0,
            capacity_percent=50.0,
            is_recovering=False,
            health_checks={},
        )
        assert any("OSD(s) are out" in r for r in result)

    def test_degraded_pgs_not_recovering(self) -> None:
        """Test degraded PGs not recovering recommendation."""
        result = _generate_recommendations(
            health_status=CephHealthStatus.HEALTH_WARN,
            osds_total=10,
            osds_up=10,
            osds_in=10,
            pgs_degraded=20,
            pgs_recovering=0,
            capacity_percent=50.0,
            is_recovering=False,
            health_checks={},
        )
        assert any("degraded but not recovering" in r for r in result)

    def test_recovery_in_progress(self) -> None:
        """Test recovery in progress recommendation."""
        result = _generate_recommendations(
            health_status=CephHealthStatus.HEALTH_WARN,
            osds_total=10,
            osds_up=10,
            osds_in=10,
            pgs_degraded=20,
            pgs_recovering=20,
            capacity_percent=50.0,
            is_recovering=True,
            health_checks={},
        )
        assert any("Recovery in progress" in r for r in result)

    def test_emergency_capacity(self) -> None:
        """Test emergency capacity recommendation."""
        result = _generate_recommendations(
            health_status=CephHealthStatus.HEALTH_WARN,
            osds_total=10,
            osds_up=10,
            osds_in=10,
            pgs_degraded=0,
            pgs_recovering=0,
            capacity_percent=96.0,
            is_recovering=False,
            health_checks={},
        )
        assert any("EMERGENCY" in r for r in result)

    def test_critical_capacity(self) -> None:
        """Test critical capacity recommendation."""
        result = _generate_recommendations(
            health_status=CephHealthStatus.HEALTH_WARN,
            osds_total=10,
            osds_up=10,
            osds_in=10,
            pgs_degraded=0,
            pgs_recovering=0,
            capacity_percent=88.0,
            is_recovering=False,
            health_checks={},
        )
        assert any("CRITICAL" in r for r in result)

    def test_warning_capacity(self) -> None:
        """Test warning capacity recommendation."""
        result = _generate_recommendations(
            health_status=CephHealthStatus.HEALTH_WARN,
            osds_total=10,
            osds_up=10,
            osds_in=10,
            pgs_degraded=0,
            pgs_recovering=0,
            capacity_percent=75.0,  # Between 70-80 (warning threshold)
            is_recovering=False,
            health_checks={},
        )
        assert any("WARNING" in r for r in result)

    def test_health_check_recommendations(self) -> None:
        """Test health check based recommendations."""
        result = _generate_recommendations(
            health_status=CephHealthStatus.HEALTH_WARN,
            osds_total=10,
            osds_up=10,
            osds_in=10,
            pgs_degraded=0,
            pgs_recovering=0,
            capacity_percent=50.0,
            is_recovering=False,
            health_checks={
                "OSD_NEAR_FULL": "Some OSDs are near full",
                "PG_DEGRADED": "Some PGs are degraded",
                "SLOW_OPS": "Slow operations detected",
            },
        )
        assert any("OSD" in r for r in result)
        assert any("PG" in r for r in result)
        assert any("Performance" in r for r in result)

    def test_max_recommendations(self) -> None:
        """Test recommendations are limited."""
        result = _generate_recommendations(
            health_status=CephHealthStatus.HEALTH_ERR,
            osds_total=10,
            osds_up=5,
            osds_in=5,
            pgs_degraded=50,
            pgs_recovering=0,
            capacity_percent=96.0,
            is_recovering=False,
            health_checks={f"CHECK_{i}": f"OSD issue {i}" for i in range(20)},
        )
        assert len(result) <= 10


class TestGetCephHealth:
    """Tests for get_ceph_health function."""

    @pytest.fixture
    def mock_kubernetes_adapter(self) -> AsyncMock:
        """Create mock Kubernetes adapter."""
        return AsyncMock()

    @pytest.fixture
    def mock_cluster_status_healthy(self) -> MagicMock:
        """Create mock healthy cluster status."""
        status = MagicMock()
        status.health = CephHealthStatus.HEALTH_OK
        status.health_checks = {}
        status.num_osds = 10
        status.num_osds_up = 10
        status.num_osds_in = 10
        status.num_pgs = 100
        status.pg_states = {"active+clean": 100}
        status.total_bytes = 1000000000000
        status.used_bytes = 500000000000
        status.available_bytes = 500000000000
        status.capacity_percent = 50.0
        return status

    @pytest.fixture
    def mock_cluster_status_unhealthy(self) -> MagicMock:
        """Create mock unhealthy cluster status."""
        status = MagicMock()
        status.health = CephHealthStatus.HEALTH_WARN
        status.health_checks = {"OSD_DOWN": {"summary": {"message": "OSD 5 is down"}}}
        status.num_osds = 10
        status.num_osds_up = 8
        status.num_osds_in = 10
        status.num_pgs = 100
        status.pg_states = {"active+clean": 80, "active+degraded": 20}
        status.total_bytes = 1000000000000
        status.used_bytes = 850000000000
        status.available_bytes = 150000000000
        status.capacity_percent = 85.0
        return status

    @pytest.mark.asyncio
    async def test_healthy_cluster(
        self, mock_kubernetes_adapter: AsyncMock, mock_cluster_status_healthy: MagicMock
    ) -> None:
        """Test healthy cluster status."""
        mock_ceph = AsyncMock()
        mock_ceph.get_cluster_status.return_value = mock_cluster_status_healthy

        with patch(
            "mosk_mcp.tools.cluster_health.get_ceph_health.CephAdapter",
        ) as MockCeph:
            MockCeph.return_value.__aenter__.return_value = mock_ceph
            MockCeph.return_value.__aexit__.return_value = None

            result = await get_ceph_health(mock_kubernetes_adapter, GetCephHealthInput())

        assert result.health == HealthStatus.HEALTHY
        assert result.ceph_health == "HEALTH_OK"
        assert result.osds_total == 10
        assert result.osds_up == 10
        assert result.pgs_active_clean == 100
        assert result.capacity_percent_used == 50.0

    @pytest.mark.asyncio
    async def test_unhealthy_cluster(
        self, mock_kubernetes_adapter: AsyncMock, mock_cluster_status_unhealthy: MagicMock
    ) -> None:
        """Test unhealthy cluster status."""
        mock_ceph = AsyncMock()
        mock_ceph.get_cluster_status.return_value = mock_cluster_status_unhealthy

        with patch(
            "mosk_mcp.tools.cluster_health.get_ceph_health.CephAdapter",
        ) as MockCeph:
            MockCeph.return_value.__aenter__.return_value = mock_ceph
            MockCeph.return_value.__aexit__.return_value = None

            result = await get_ceph_health(mock_kubernetes_adapter, GetCephHealthInput())

        assert result.health != HealthStatus.HEALTHY
        assert result.ceph_health == "HEALTH_WARN"
        assert result.osds_up == 8
        assert result.pgs_degraded == 20
        assert len(result.issues) > 0
        assert len(result.recommendations) > 0

    @pytest.mark.asyncio
    async def test_with_osd_details(
        self, mock_kubernetes_adapter: AsyncMock, mock_cluster_status_healthy: MagicMock
    ) -> None:
        """Test with OSD details included."""
        mock_osd1 = MagicMock()
        mock_osd1.osd_id = 0
        mock_osd1.is_up = True
        mock_osd1.is_in = True
        mock_osd1.host = "node1"
        mock_osd1.device_class = "ssd"
        mock_osd1.utilization_percent = 45.0

        mock_ceph = AsyncMock()
        mock_ceph.get_cluster_status.return_value = mock_cluster_status_healthy
        mock_ceph.list_osds.return_value = [mock_osd1]

        with patch(
            "mosk_mcp.tools.cluster_health.get_ceph_health.CephAdapter",
        ) as MockCeph:
            MockCeph.return_value.__aenter__.return_value = mock_ceph
            MockCeph.return_value.__aexit__.return_value = None

            result = await get_ceph_health(
                mock_kubernetes_adapter,
                GetCephHealthInput(include_osd_details=True),
            )

        assert len(result.osds) == 1
        assert result.osds[0].osd_id == 0
        assert result.osds[0].healthy is True
        assert result.osd_details_available is True

    @pytest.mark.asyncio
    async def test_osd_details_failure(
        self, mock_kubernetes_adapter: AsyncMock, mock_cluster_status_healthy: MagicMock
    ) -> None:
        """Test OSD details failure is handled."""
        mock_ceph = AsyncMock()
        mock_ceph.get_cluster_status.return_value = mock_cluster_status_healthy
        mock_ceph.list_osds.side_effect = Exception("Failed to list OSDs")

        with patch(
            "mosk_mcp.tools.cluster_health.get_ceph_health.CephAdapter",
        ) as MockCeph:
            MockCeph.return_value.__aenter__.return_value = mock_ceph
            MockCeph.return_value.__aexit__.return_value = None

            result = await get_ceph_health(
                mock_kubernetes_adapter,
                GetCephHealthInput(include_osd_details=True),
            )

        assert result.osd_details_available is False

    @pytest.mark.asyncio
    async def test_with_pool_details(
        self, mock_kubernetes_adapter: AsyncMock, mock_cluster_status_healthy: MagicMock
    ) -> None:
        """Test with pool details included."""
        mock_ceph = AsyncMock()
        mock_ceph.get_cluster_status.return_value = mock_cluster_status_healthy
        mock_ceph.get_capacity.return_value = {
            "pools": {
                "cinder-volumes": {
                    "used_bytes": 100000000,
                    "avail_bytes": 500000000,
                    "percent_used": 16.6,
                    "objects": 1000,
                }
            }
        }

        with patch(
            "mosk_mcp.tools.cluster_health.get_ceph_health.CephAdapter",
        ) as MockCeph:
            MockCeph.return_value.__aenter__.return_value = mock_ceph
            MockCeph.return_value.__aexit__.return_value = None

            result = await get_ceph_health(
                mock_kubernetes_adapter,
                GetCephHealthInput(include_pool_details=True),
            )

        assert len(result.pools) == 1
        assert result.pools[0].name == "cinder-volumes"
        assert result.pool_details_available is True

    @pytest.mark.asyncio
    async def test_pool_details_failure(
        self, mock_kubernetes_adapter: AsyncMock, mock_cluster_status_healthy: MagicMock
    ) -> None:
        """Test pool details failure is handled."""
        mock_ceph = AsyncMock()
        mock_ceph.get_cluster_status.return_value = mock_cluster_status_healthy
        mock_ceph.get_capacity.side_effect = Exception("Failed to get capacity")

        with patch(
            "mosk_mcp.tools.cluster_health.get_ceph_health.CephAdapter",
        ) as MockCeph:
            MockCeph.return_value.__aenter__.return_value = mock_ceph
            MockCeph.return_value.__aexit__.return_value = None

            result = await get_ceph_health(
                mock_kubernetes_adapter,
                GetCephHealthInput(include_pool_details=True),
            )

        assert result.pool_details_available is False

    @pytest.mark.asyncio
    async def test_recovering_cluster(self, mock_kubernetes_adapter: AsyncMock) -> None:
        """Test recovering cluster."""
        status = MagicMock()
        status.health = CephHealthStatus.HEALTH_WARN
        status.health_checks = {}
        status.num_osds = 10
        status.num_osds_up = 10
        status.num_osds_in = 10
        status.num_pgs = 100
        status.pg_states = {"active+clean": 80, "recovering": 20}
        status.total_bytes = 1000000000000
        status.used_bytes = 500000000000
        status.available_bytes = 500000000000
        status.capacity_percent = 50.0

        mock_ceph = AsyncMock()
        mock_ceph.get_cluster_status.return_value = status

        with patch(
            "mosk_mcp.tools.cluster_health.get_ceph_health.CephAdapter",
        ) as MockCeph:
            MockCeph.return_value.__aenter__.return_value = mock_ceph
            MockCeph.return_value.__aexit__.return_value = None

            result = await get_ceph_health(mock_kubernetes_adapter, GetCephHealthInput())

        assert result.is_recovering is True
        assert result.pgs_recovering == 20
        assert result.recovery_progress_percent is not None

    @pytest.mark.asyncio
    async def test_timestamp_included(
        self, mock_kubernetes_adapter: AsyncMock, mock_cluster_status_healthy: MagicMock
    ) -> None:
        """Test timestamp is included."""
        mock_ceph = AsyncMock()
        mock_ceph.get_cluster_status.return_value = mock_cluster_status_healthy

        with patch(
            "mosk_mcp.tools.cluster_health.get_ceph_health.CephAdapter",
        ) as MockCeph:
            MockCeph.return_value.__aenter__.return_value = mock_ceph
            MockCeph.return_value.__aexit__.return_value = None

            result = await get_ceph_health(mock_kubernetes_adapter, GetCephHealthInput())

        assert result.timestamp is not None
        assert "T" in result.timestamp  # ISO format
