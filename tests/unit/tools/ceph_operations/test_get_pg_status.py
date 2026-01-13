"""Unit tests for get_pg_status tool."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mosk_mcp.core.exceptions import ToolExecutionError
from mosk_mcp.tools.ceph_operations.get_pg_status import (
    _classify_state,
    _generate_pg_health_summary,
    _generate_pg_recommendations,
    get_pg_status,
)


class TestClassifyState:
    """Tests for _classify_state function."""

    def test_active_clean_healthy(self) -> None:
        """Test active+clean is healthy."""
        is_healthy, desc = _classify_state("active+clean")
        assert is_healthy is True
        assert "Healthy" in desc

    def test_active_healthy(self) -> None:
        """Test active is healthy."""
        is_healthy, _ = _classify_state("active")
        assert is_healthy is True

    def test_degraded_unhealthy(self) -> None:
        """Test degraded is unhealthy."""
        is_healthy, desc = _classify_state("active+degraded")
        assert is_healthy is False
        assert "degraded" in desc.lower()

    def test_undersized_unhealthy(self) -> None:
        """Test undersized is unhealthy."""
        is_healthy, desc = _classify_state("active+undersized")
        assert is_healthy is False
        assert "fewer copies" in desc.lower()

    def test_stale_unhealthy(self) -> None:
        """Test stale is unhealthy."""
        is_healthy, desc = _classify_state("stale+active")
        assert is_healthy is False
        assert "not been updated" in desc.lower()

    def test_incomplete_unhealthy(self) -> None:
        """Test incomplete is unhealthy."""
        is_healthy, _ = _classify_state("incomplete")
        assert is_healthy is False

    def test_peering_unhealthy(self) -> None:
        """Test peering is unhealthy."""
        is_healthy, _ = _classify_state("peering")
        assert is_healthy is False

    def test_active_clean_with_extra_flags(self) -> None:
        """Test active+clean with extra flags is healthy."""
        is_healthy, desc = _classify_state("active+clean+laggy")
        assert is_healthy is True
        assert "additional flags" in desc.lower()

    def test_case_insensitive(self) -> None:
        """Test state classification is case insensitive."""
        is_healthy, _ = _classify_state("ACTIVE+CLEAN")
        assert is_healthy is True

    def test_unknown_state(self) -> None:
        """Test unknown state."""
        is_healthy, desc = _classify_state("unknown_state")
        assert is_healthy is False
        assert "Unknown" in desc


class TestGeneratePgHealthSummary:
    """Tests for _generate_pg_health_summary function."""

    def test_healthy_cluster(self) -> None:
        """Test healthy cluster summary."""
        result = _generate_pg_health_summary(
            total_pgs=100,
            active_clean=100,
            states={"active+clean": 100},
            is_healthy=True,
            recovery_active=False,
        )

        assert "All 100 PGs are active+clean" in result
        assert "healthy" in result.lower()

    def test_unhealthy_cluster(self) -> None:
        """Test unhealthy cluster summary."""
        result = _generate_pg_health_summary(
            total_pgs=100,
            active_clean=90,
            states={"active+clean": 90, "active+degraded": 10},
            is_healthy=False,
            recovery_active=False,
        )

        assert "90/100 PGs active+clean" in result
        assert "non-optimal" in result.lower()

    def test_recovery_active(self) -> None:
        """Test recovery active in summary."""
        result = _generate_pg_health_summary(
            total_pgs=100,
            active_clean=80,
            states={"active+clean": 80, "recovering": 20},
            is_healthy=False,
            recovery_active=True,
        )

        assert "recovery in progress" in result.lower()


class TestGeneratePgRecommendations:
    """Tests for _generate_pg_recommendations function."""

    def test_healthy_pgs(self) -> None:
        """Test healthy PGs recommendations."""
        result = _generate_pg_recommendations(
            states={"active+clean": 100},
            stuck_pgs={},
            is_healthy=True,
            recovery_active=False,
            misplaced_ratio=0.0,
            degraded_ratio=0.0,
        )

        assert any("No action required" in r for r in result)

    def test_recovery_active(self) -> None:
        """Test recovery active recommendations."""
        result = _generate_pg_recommendations(
            states={"active+clean": 80, "recovering": 20},
            stuck_pgs={},
            is_healthy=False,
            recovery_active=True,
            misplaced_ratio=5.0,
            degraded_ratio=0.0,
        )

        assert any("recovery" in r.lower() for r in result)

    def test_degraded_pgs(self) -> None:
        """Test degraded PGs recommendations."""
        result = _generate_pg_recommendations(
            states={"active+clean": 80, "active+degraded": 20},
            stuck_pgs={},
            is_healthy=False,
            recovery_active=False,
            misplaced_ratio=0.0,
            degraded_ratio=5.0,
        )

        assert any("degraded" in r.lower() for r in result)

    def test_stuck_pgs(self) -> None:
        """Test stuck PGs recommendations."""
        result = _generate_pg_recommendations(
            states={"active+clean": 90},
            stuck_pgs={"stale": 5, "unclean": 5},
            is_healthy=False,
            recovery_active=False,
            misplaced_ratio=0.0,
            degraded_ratio=0.0,
        )

        assert any("stuck" in r.lower() for r in result)

    def test_stale_pgs(self) -> None:
        """Test stale PGs recommendations."""
        result = _generate_pg_recommendations(
            states={"active+clean": 90, "stale": 10},
            stuck_pgs={},
            is_healthy=False,
            recovery_active=False,
            misplaced_ratio=0.0,
            degraded_ratio=0.0,
        )

        assert any("stale" in r.lower() for r in result)

    def test_high_misplaced_ratio(self) -> None:
        """Test high misplaced ratio recommendations."""
        result = _generate_pg_recommendations(
            states={"active+clean": 100},
            stuck_pgs={},
            is_healthy=False,
            recovery_active=True,
            misplaced_ratio=10.0,
            degraded_ratio=0.0,
        )

        assert any("misplaced" in r.lower() for r in result)

    def test_high_degraded_ratio(self) -> None:
        """Test high degraded ratio recommendations."""
        result = _generate_pg_recommendations(
            states={"active+clean": 100},
            stuck_pgs={},
            is_healthy=False,
            recovery_active=False,
            misplaced_ratio=0.0,
            degraded_ratio=5.0,
        )

        assert any("redundancy" in r.lower() for r in result)

    def test_undersized_pgs(self) -> None:
        """Test undersized PGs recommendations."""
        result = _generate_pg_recommendations(
            states={"active+clean": 90, "active+undersized": 10},
            stuck_pgs={},
            is_healthy=False,
            recovery_active=False,
            misplaced_ratio=0.0,
            degraded_ratio=0.0,
        )

        assert any("undersized" in r.lower() for r in result)


class TestGetPgStatus:
    """Tests for get_pg_status function."""

    @pytest.fixture
    def mock_kubernetes_adapter(self) -> AsyncMock:
        """Create mock Kubernetes adapter."""
        return AsyncMock()

    @pytest.fixture
    def mock_pg_summary_healthy(self) -> MagicMock:
        """Create healthy PG summary."""
        summary = MagicMock()
        summary.total_pgs = 100
        summary.active_clean = 100
        summary.states = {"active+clean": 100}
        summary.stuck_pgs = {}
        summary.is_healthy = True
        summary.misplaced_ratio = 0.0
        summary.degraded_ratio = 0.0
        summary.recovering = False
        return summary

    @pytest.fixture
    def mock_pg_summary_unhealthy(self) -> MagicMock:
        """Create unhealthy PG summary."""
        summary = MagicMock()
        summary.total_pgs = 100
        summary.active_clean = 80
        summary.states = {"active+clean": 80, "active+degraded": 20}
        summary.stuck_pgs = {"stale": 5}
        summary.is_healthy = False
        summary.misplaced_ratio = 5.0
        summary.degraded_ratio = 2.0
        summary.recovering = True
        return summary

    @pytest.fixture
    def mock_recovery_status(self) -> MagicMock:
        """Create mock recovery status."""
        status = MagicMock()
        status.is_in_progress = False
        return status

    @pytest.mark.asyncio
    async def test_get_pg_status_healthy(
        self,
        mock_kubernetes_adapter: AsyncMock,
        mock_pg_summary_healthy: MagicMock,
        mock_recovery_status: MagicMock,
    ) -> None:
        """Test healthy PG status retrieval."""
        mock_ceph = AsyncMock()
        mock_ceph.get_pg_status.return_value = mock_pg_summary_healthy
        mock_ceph.get_recovery_status.return_value = mock_recovery_status

        with patch(
            "mosk_mcp.tools.ceph_operations.get_pg_status.CephAdapter",
        ) as MockCephAdapter:
            MockCephAdapter.return_value.__aenter__.return_value = mock_ceph
            MockCephAdapter.return_value.__aexit__.return_value = None

            result = await get_pg_status(mock_kubernetes_adapter)

        assert result.total_pgs == 100
        assert result.active_clean == 100
        assert result.is_healthy is True
        assert len(result.states) == 1

    @pytest.mark.asyncio
    async def test_get_pg_status_unhealthy(
        self,
        mock_kubernetes_adapter: AsyncMock,
        mock_pg_summary_unhealthy: MagicMock,
    ) -> None:
        """Test unhealthy PG status retrieval."""
        mock_recovery = MagicMock()
        mock_recovery.is_in_progress = True

        mock_ceph = AsyncMock()
        mock_ceph.get_pg_status.return_value = mock_pg_summary_unhealthy
        mock_ceph.get_recovery_status.return_value = mock_recovery

        with patch(
            "mosk_mcp.tools.ceph_operations.get_pg_status.CephAdapter",
        ) as MockCephAdapter:
            MockCephAdapter.return_value.__aenter__.return_value = mock_ceph
            MockCephAdapter.return_value.__aexit__.return_value = None

            result = await get_pg_status(mock_kubernetes_adapter)

        assert result.total_pgs == 100
        assert result.active_clean == 80
        assert result.is_healthy is False
        assert result.recovery_active is True
        assert len(result.recommendations) > 0

    @pytest.mark.asyncio
    async def test_get_pg_status_with_stuck_pgs(
        self,
        mock_kubernetes_adapter: AsyncMock,
        mock_pg_summary_unhealthy: MagicMock,
        mock_recovery_status: MagicMock,
    ) -> None:
        """Test PG status with stuck PGs."""
        mock_ceph = AsyncMock()
        mock_ceph.get_pg_status.return_value = mock_pg_summary_unhealthy
        mock_ceph.get_recovery_status.return_value = mock_recovery_status

        with patch(
            "mosk_mcp.tools.ceph_operations.get_pg_status.CephAdapter",
        ) as MockCephAdapter:
            MockCephAdapter.return_value.__aenter__.return_value = mock_ceph
            MockCephAdapter.return_value.__aexit__.return_value = None

            result = await get_pg_status(mock_kubernetes_adapter, include_stuck=True)

        assert "stale" in result.stuck_pgs

    @pytest.mark.asyncio
    async def test_get_pg_status_without_stuck(
        self,
        mock_kubernetes_adapter: AsyncMock,
        mock_pg_summary_unhealthy: MagicMock,
        mock_recovery_status: MagicMock,
    ) -> None:
        """Test PG status without stuck PGs."""
        mock_ceph = AsyncMock()
        mock_ceph.get_pg_status.return_value = mock_pg_summary_unhealthy
        mock_ceph.get_recovery_status.return_value = mock_recovery_status

        with patch(
            "mosk_mcp.tools.ceph_operations.get_pg_status.CephAdapter",
        ) as MockCephAdapter:
            MockCephAdapter.return_value.__aenter__.return_value = mock_ceph
            MockCephAdapter.return_value.__aexit__.return_value = None

            result = await get_pg_status(mock_kubernetes_adapter, include_stuck=False)

        assert result.stuck_pgs == {}

    @pytest.mark.asyncio
    async def test_get_pg_status_without_recovery(
        self,
        mock_kubernetes_adapter: AsyncMock,
        mock_pg_summary_healthy: MagicMock,
    ) -> None:
        """Test PG status without recovery info."""
        mock_ceph = AsyncMock()
        mock_ceph.get_pg_status.return_value = mock_pg_summary_healthy

        with patch(
            "mosk_mcp.tools.ceph_operations.get_pg_status.CephAdapter",
        ) as MockCephAdapter:
            MockCephAdapter.return_value.__aenter__.return_value = mock_ceph
            MockCephAdapter.return_value.__aexit__.return_value = None

            result = await get_pg_status(mock_kubernetes_adapter, include_recovery=False)

        mock_ceph.get_recovery_status.assert_not_called()
        assert result.recovery_active is False

    @pytest.mark.asyncio
    async def test_get_pg_status_states_sorted(
        self,
        mock_kubernetes_adapter: AsyncMock,
        mock_recovery_status: MagicMock,
    ) -> None:
        """Test PG states are sorted correctly."""
        mock_summary = MagicMock()
        mock_summary.total_pgs = 100
        mock_summary.active_clean = 50
        mock_summary.states = {
            "active+clean": 50,
            "active+degraded": 30,
            "recovering": 20,
        }
        mock_summary.stuck_pgs = {}
        mock_summary.is_healthy = False
        mock_summary.misplaced_ratio = 0.0
        mock_summary.degraded_ratio = 0.0

        mock_ceph = AsyncMock()
        mock_ceph.get_pg_status.return_value = mock_summary
        mock_ceph.get_recovery_status.return_value = mock_recovery_status

        with patch(
            "mosk_mcp.tools.ceph_operations.get_pg_status.CephAdapter",
        ) as MockCephAdapter:
            MockCephAdapter.return_value.__aenter__.return_value = mock_ceph
            MockCephAdapter.return_value.__aexit__.return_value = None

            result = await get_pg_status(mock_kubernetes_adapter)

        # Unhealthy states should come first
        assert result.states[0].is_healthy is False
        # active+clean should be last (healthy)
        assert result.states[-1].state == "active+clean"

    @pytest.mark.asyncio
    async def test_get_pg_status_error(self, mock_kubernetes_adapter: AsyncMock) -> None:
        """Test error handling."""
        mock_ceph = AsyncMock()
        mock_ceph.get_pg_status.side_effect = Exception("Connection failed")

        with patch(
            "mosk_mcp.tools.ceph_operations.get_pg_status.CephAdapter",
        ) as MockCephAdapter:
            MockCephAdapter.return_value.__aenter__.return_value = mock_ceph
            MockCephAdapter.return_value.__aexit__.return_value = None

            with pytest.raises(ToolExecutionError) as exc_info:
                await get_pg_status(mock_kubernetes_adapter)

        assert "Failed to get PG status" in str(exc_info.value)
