"""Unit tests for get_recovery_status tool."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mosk_mcp.core.exceptions import ToolExecutionError
from mosk_mcp.tools.ceph_operations.get_recovery_status import (
    _format_duration,
    _generate_recommendations,
    _generate_status_summary,
    get_recovery_status,
)
from mosk_mcp.tools.ceph_operations.models import RecoveryProgress


class TestFormatDuration:
    """Tests for _format_duration function."""

    def test_unknown_negative(self) -> None:
        """Test negative values return unknown."""
        assert _format_duration(-1) == "unknown"

    def test_unknown_none(self) -> None:
        """Test None values return unknown."""
        assert _format_duration(None) == "unknown"

    def test_seconds(self) -> None:
        """Test seconds formatting."""
        assert _format_duration(30) == "30 seconds"
        assert _format_duration(59) == "59 seconds"

    def test_minutes(self) -> None:
        """Test minutes formatting."""
        assert _format_duration(60) == "1 minute"
        assert _format_duration(120) == "2 minutes"
        assert _format_duration(3540) == "59 minutes"

    def test_hours(self) -> None:
        """Test hours formatting."""
        assert _format_duration(3600) == "1 hour"
        assert _format_duration(7200) == "2 hours"
        assert _format_duration(7800) == "2h 10m"

    def test_days(self) -> None:
        """Test days formatting."""
        assert _format_duration(86400) == "1 day"
        assert _format_duration(172800) == "2 days"
        assert _format_duration(172800 + 3600) == "2d 1h"


class TestGenerateStatusSummary:
    """Tests for _generate_status_summary function."""

    def test_no_recovery(self) -> None:
        """Test no recovery in progress."""
        result = _generate_status_summary(
            is_recovering=False,
            is_backfilling=False,
            is_rebalancing=False,
            misplaced_ratio=0.0,
            degraded_ratio=0.0,
            recovery_progress=None,
        )

        assert "stable" in result.lower()

    def test_stalled_recovery(self) -> None:
        """Test stalled recovery detection."""
        result = _generate_status_summary(
            is_recovering=False,
            is_backfilling=False,
            is_rebalancing=False,
            misplaced_ratio=5.0,
            degraded_ratio=0.0,
            recovery_progress=None,
        )

        assert "stalled" in result.lower() or "waiting" in result.lower()

    def test_recovering(self) -> None:
        """Test recovery in progress."""
        result = _generate_status_summary(
            is_recovering=True,
            is_backfilling=False,
            is_rebalancing=False,
            misplaced_ratio=5.0,
            degraded_ratio=0.0,
            recovery_progress=None,
        )

        assert "recovery in progress" in result.lower()

    def test_backfilling(self) -> None:
        """Test backfill in progress."""
        result = _generate_status_summary(
            is_recovering=False,
            is_backfilling=True,
            is_rebalancing=False,
            misplaced_ratio=5.0,
            degraded_ratio=0.0,
            recovery_progress=None,
        )

        assert "backfill in progress" in result.lower()

    def test_rebalancing(self) -> None:
        """Test rebalancing in progress."""
        result = _generate_status_summary(
            is_recovering=False,
            is_backfilling=False,
            is_rebalancing=True,
            misplaced_ratio=5.0,
            degraded_ratio=0.0,
            recovery_progress=None,
        )

        assert "rebalancing active" in result.lower()

    def test_with_progress(self) -> None:
        """Test summary with progress info."""
        progress = RecoveryProgress(
            objects_recovered=500,
            objects_to_recover=1000,
            bytes_recovered=5000000,
            bytes_to_recover=10000000,
            percent_complete=50.0,
            recovery_rate_bytes_per_sec=100000,
            estimated_time_remaining="1 hour",
        )

        result = _generate_status_summary(
            is_recovering=True,
            is_backfilling=False,
            is_rebalancing=False,
            misplaced_ratio=5.0,
            degraded_ratio=0.0,
            recovery_progress=progress,
        )

        assert "50.0% complete" in result
        assert "ETA: 1 hour" in result


class TestGenerateRecommendations:
    """Tests for _generate_recommendations function."""

    def test_healthy(self) -> None:
        """Test healthy cluster recommendations."""
        result = _generate_recommendations(
            is_recovering=False,
            is_backfilling=False,
            is_rebalancing=False,
            misplaced_ratio=0.0,
            degraded_ratio=0.0,
            recovery_rate_bytes_per_sec=0,
        )

        assert any("healthy" in r.lower() for r in result)

    def test_active_recovery(self) -> None:
        """Test active recovery recommendations."""
        result = _generate_recommendations(
            is_recovering=True,
            is_backfilling=False,
            is_rebalancing=False,
            misplaced_ratio=5.0,
            degraded_ratio=0.0,
            recovery_rate_bytes_per_sec=100000000,
        )

        assert any("avoid making cluster changes" in r.lower() for r in result)

    def test_low_recovery_rate(self) -> None:
        """Test low recovery rate warning."""
        result = _generate_recommendations(
            is_recovering=True,
            is_backfilling=False,
            is_rebalancing=False,
            misplaced_ratio=5.0,
            degraded_ratio=0.0,
            recovery_rate_bytes_per_sec=5 * 1024 * 1024,  # 5 MB/s (low)
        )

        assert any("low" in r.lower() for r in result)

    def test_high_degraded_ratio(self) -> None:
        """Test high degraded ratio warning."""
        result = _generate_recommendations(
            is_recovering=False,
            is_backfilling=False,
            is_rebalancing=False,
            misplaced_ratio=0.0,
            degraded_ratio=10.0,
            recovery_rate_bytes_per_sec=0,
        )

        assert any("degraded" in r.lower() and "redundancy" in r.lower() for r in result)

    def test_moderate_degraded_ratio(self) -> None:
        """Test moderate degraded ratio warning."""
        result = _generate_recommendations(
            is_recovering=True,
            is_backfilling=False,
            is_rebalancing=False,
            misplaced_ratio=0.0,
            degraded_ratio=2.0,
            recovery_rate_bytes_per_sec=100000000,
        )

        assert any("monitor" in r.lower() for r in result)

    def test_high_misplaced_ratio(self) -> None:
        """Test high misplaced ratio warning."""
        result = _generate_recommendations(
            is_recovering=True,
            is_backfilling=False,
            is_rebalancing=False,
            misplaced_ratio=15.0,
            degraded_ratio=0.0,
            recovery_rate_bytes_per_sec=100000000,
        )

        assert any("misplaced" in r.lower() and "performance" in r.lower() for r in result)

    def test_stalled_recovery(self) -> None:
        """Test stalled recovery warning."""
        result = _generate_recommendations(
            is_recovering=False,
            is_backfilling=False,
            is_rebalancing=False,
            misplaced_ratio=5.0,
            degraded_ratio=0.0,
            recovery_rate_bytes_per_sec=0,
        )

        assert any("not active" in r.lower() for r in result)


class TestGetRecoveryStatus:
    """Tests for get_recovery_status function."""

    @pytest.fixture
    def mock_kubernetes_adapter(self) -> AsyncMock:
        """Create mock Kubernetes adapter."""
        return AsyncMock()

    @pytest.fixture
    def mock_recovery_idle(self) -> MagicMock:
        """Create mock idle recovery status."""
        recovery = MagicMock()
        recovery.is_in_progress = False
        recovery.is_recovering = False
        recovery.is_backfilling = False
        recovery.misplaced_objects = 0
        recovery.misplaced_ratio = 0.0
        recovery.degraded_objects = 0
        recovery.degraded_ratio = 0.0
        recovery.recovering_bytes = 0
        recovery.recovery_rate_bytes = 0
        recovery.estimated_time_remaining_seconds = None
        return recovery

    @pytest.fixture
    def mock_recovery_active(self) -> MagicMock:
        """Create mock active recovery status."""
        recovery = MagicMock()
        recovery.is_in_progress = True
        recovery.is_recovering = True
        recovery.is_backfilling = False
        recovery.misplaced_objects = 1000
        recovery.misplaced_ratio = 5.0
        recovery.degraded_objects = 500
        recovery.degraded_ratio = 2.0
        recovery.recovering_bytes = 10000000000
        recovery.recovery_rate_bytes = 100000000
        recovery.estimated_time_remaining_seconds = 3600
        return recovery

    @pytest.fixture
    def mock_pg_status_healthy(self) -> MagicMock:
        """Create mock healthy PG status."""
        pg = MagicMock()
        pg.states = {"active+clean": 100}
        pg.recovering = False
        return pg

    @pytest.fixture
    def mock_pg_status_recovering(self) -> MagicMock:
        """Create mock recovering PG status."""
        pg = MagicMock()
        pg.states = {"active+clean": 80, "recovering": 10, "backfilling": 10}
        pg.recovering = True
        return pg

    @pytest.mark.asyncio
    async def test_idle_recovery(
        self,
        mock_kubernetes_adapter: AsyncMock,
        mock_recovery_idle: MagicMock,
        mock_pg_status_healthy: MagicMock,
    ) -> None:
        """Test idle recovery status."""
        mock_ceph = AsyncMock()
        mock_ceph.get_recovery_status.return_value = mock_recovery_idle
        mock_ceph.get_pg_status.return_value = mock_pg_status_healthy

        with patch(
            "mosk_mcp.tools.ceph_operations.get_recovery_status.CephAdapter",
        ) as MockCephAdapter:
            MockCephAdapter.return_value.__aenter__.return_value = mock_ceph
            MockCephAdapter.return_value.__aexit__.return_value = None

            result = await get_recovery_status(mock_kubernetes_adapter)

        assert result.is_recovering is False
        assert result.is_backfilling is False
        assert result.recovery_progress is None
        assert result.misplaced_objects == 0

    @pytest.mark.asyncio
    async def test_active_recovery(
        self,
        mock_kubernetes_adapter: AsyncMock,
        mock_recovery_active: MagicMock,
        mock_pg_status_recovering: MagicMock,
    ) -> None:
        """Test active recovery status."""
        mock_ceph = AsyncMock()
        mock_ceph.get_recovery_status.return_value = mock_recovery_active
        mock_ceph.get_pg_status.return_value = mock_pg_status_recovering

        with patch(
            "mosk_mcp.tools.ceph_operations.get_recovery_status.CephAdapter",
        ) as MockCephAdapter:
            MockCephAdapter.return_value.__aenter__.return_value = mock_ceph
            MockCephAdapter.return_value.__aexit__.return_value = None

            result = await get_recovery_status(mock_kubernetes_adapter)

        assert result.is_recovering is True
        assert result.recovery_progress is not None
        assert result.pgs_recovering == 10
        assert result.pgs_backfilling == 10
        assert len(result.recommendations) > 0

    @pytest.mark.asyncio
    async def test_recovery_with_eta(
        self,
        mock_kubernetes_adapter: AsyncMock,
        mock_recovery_active: MagicMock,
        mock_pg_status_recovering: MagicMock,
    ) -> None:
        """Test recovery with ETA calculation."""
        mock_ceph = AsyncMock()
        mock_ceph.get_recovery_status.return_value = mock_recovery_active
        mock_ceph.get_pg_status.return_value = mock_pg_status_recovering

        with patch(
            "mosk_mcp.tools.ceph_operations.get_recovery_status.CephAdapter",
        ) as MockCephAdapter:
            MockCephAdapter.return_value.__aenter__.return_value = mock_ceph
            MockCephAdapter.return_value.__aexit__.return_value = None

            result = await get_recovery_status(mock_kubernetes_adapter)

        assert result.recovery_progress is not None
        assert result.recovery_progress.estimated_time_remaining != "unknown"

    @pytest.mark.asyncio
    async def test_rebalancing_detection(
        self,
        mock_kubernetes_adapter: AsyncMock,
        mock_pg_status_recovering: MagicMock,
    ) -> None:
        """Test rebalancing detection."""
        recovery = MagicMock()
        recovery.is_in_progress = True
        recovery.is_recovering = False
        recovery.is_backfilling = False
        recovery.misplaced_objects = 1000
        recovery.misplaced_ratio = 5.0
        recovery.degraded_objects = 0
        recovery.degraded_ratio = 0.0
        recovery.recovering_bytes = 0
        recovery.recovery_rate_bytes = 0
        recovery.estimated_time_remaining_seconds = None

        mock_ceph = AsyncMock()
        mock_ceph.get_recovery_status.return_value = recovery
        mock_ceph.get_pg_status.return_value = mock_pg_status_recovering

        with patch(
            "mosk_mcp.tools.ceph_operations.get_recovery_status.CephAdapter",
        ) as MockCephAdapter:
            MockCephAdapter.return_value.__aenter__.return_value = mock_ceph
            MockCephAdapter.return_value.__aexit__.return_value = None

            result = await get_recovery_status(mock_kubernetes_adapter)

        assert result.is_rebalancing is True

    @pytest.mark.asyncio
    async def test_timestamp_included(
        self,
        mock_kubernetes_adapter: AsyncMock,
        mock_recovery_idle: MagicMock,
        mock_pg_status_healthy: MagicMock,
    ) -> None:
        """Test timestamp is included."""
        mock_ceph = AsyncMock()
        mock_ceph.get_recovery_status.return_value = mock_recovery_idle
        mock_ceph.get_pg_status.return_value = mock_pg_status_healthy

        with patch(
            "mosk_mcp.tools.ceph_operations.get_recovery_status.CephAdapter",
        ) as MockCephAdapter:
            MockCephAdapter.return_value.__aenter__.return_value = mock_ceph
            MockCephAdapter.return_value.__aexit__.return_value = None

            result = await get_recovery_status(mock_kubernetes_adapter)

        assert result.timestamp is not None
        assert "T" in result.timestamp  # ISO format

    @pytest.mark.asyncio
    async def test_error_handling(self, mock_kubernetes_adapter: AsyncMock) -> None:
        """Test error handling."""
        mock_ceph = AsyncMock()
        mock_ceph.get_recovery_status.side_effect = Exception("Connection failed")

        with patch(
            "mosk_mcp.tools.ceph_operations.get_recovery_status.CephAdapter",
        ) as MockCephAdapter:
            MockCephAdapter.return_value.__aenter__.return_value = mock_ceph
            MockCephAdapter.return_value.__aexit__.return_value = None

            with pytest.raises(ToolExecutionError) as exc_info:
                await get_recovery_status(mock_kubernetes_adapter)

        assert "Failed to get recovery status" in str(exc_info.value)
