"""Unit tests for predict_capacity tool."""

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from mosk_mcp.adapters.ceph import (
    CAPACITY_CRITICAL_THRESHOLD,
    CAPACITY_WARNING_THRESHOLD,
)
from mosk_mcp.core.exceptions import ToolExecutionError
from mosk_mcp.tools.ceph_operations.models import CapacityStatus
from mosk_mcp.tools.ceph_operations.predict_capacity import (
    _calculate_days_until_threshold,
    _determine_confidence,
    _format_duration,
    _generate_capacity_recommendations,
    _generate_forecasts,
    _get_capacity_status,
    predict_capacity,
)


class TestFormatDuration:
    """Tests for _format_duration function."""

    def test_negative_days(self) -> None:
        """Test negative days return unknown."""
        assert _format_duration(-1) == "unknown"

    def test_today(self) -> None:
        """Test zero days return today."""
        assert _format_duration(0) == "today"

    def test_one_day(self) -> None:
        """Test one day."""
        assert _format_duration(1) == "1 day"

    def test_days(self) -> None:
        """Test days formatting."""
        assert _format_duration(5) == "5 days"

    def test_weeks(self) -> None:
        """Test weeks formatting."""
        assert _format_duration(7) == "1 week"
        assert _format_duration(14) == "2 weeks"

    def test_months(self) -> None:
        """Test months formatting."""
        assert _format_duration(30) == "1 month"
        assert _format_duration(60) == "2 months"

    def test_years(self) -> None:
        """Test years formatting."""
        assert _format_duration(365) == "1 year"
        assert _format_duration(730) == "2 years"

    def test_years_with_months(self) -> None:
        """Test years with remaining months."""
        assert "1 year" in _format_duration(400)
        assert "month" in _format_duration(400)


class TestGetCapacityStatus:
    """Tests for _get_capacity_status function."""

    def test_normal(self) -> None:
        """Test normal status."""
        assert _get_capacity_status(50.0) == CapacityStatus.NORMAL

    def test_warning(self) -> None:
        """Test warning status."""
        assert _get_capacity_status(CAPACITY_WARNING_THRESHOLD) == CapacityStatus.WARNING

    def test_critical(self) -> None:
        """Test critical status."""
        assert _get_capacity_status(CAPACITY_CRITICAL_THRESHOLD) == CapacityStatus.CRITICAL

    def test_emergency(self) -> None:
        """Test emergency status."""
        assert _get_capacity_status(85.0) == CapacityStatus.EMERGENCY
        assert _get_capacity_status(100.0) == CapacityStatus.EMERGENCY


class TestCalculateDaysUntilThreshold:
    """Tests for _calculate_days_until_threshold function."""

    def test_already_exceeded(self) -> None:
        """Test already exceeded threshold."""
        result = _calculate_days_until_threshold(
            current_percent=80.0,
            threshold=70.0,
            growth_rate_percent_per_day=1.0,
        )
        assert result == 0

    def test_no_growth(self) -> None:
        """Test no growth rate."""
        result = _calculate_days_until_threshold(
            current_percent=50.0,
            threshold=70.0,
            growth_rate_percent_per_day=0.0,
        )
        assert result is None

    def test_negative_growth(self) -> None:
        """Test negative growth rate."""
        result = _calculate_days_until_threshold(
            current_percent=50.0,
            threshold=70.0,
            growth_rate_percent_per_day=-1.0,
        )
        assert result is None

    def test_positive_growth(self) -> None:
        """Test positive growth rate."""
        result = _calculate_days_until_threshold(
            current_percent=50.0,
            threshold=70.0,
            growth_rate_percent_per_day=1.0,
        )
        assert result == 20


class TestGenerateForecasts:
    """Tests for _generate_forecasts function."""

    def test_short_forecast(self) -> None:
        """Test short-term forecast."""
        result = _generate_forecasts(
            current_used_bytes=500000000000,
            total_bytes=1000000000000,
            growth_rate_bytes_per_day=10000000000,
            days_to_forecast=7,
        )

        assert len(result) >= 7
        assert result[0].days_from_now == 1
        assert result[-1].days_from_now == 7

    def test_long_forecast(self) -> None:
        """Test long-term forecast."""
        result = _generate_forecasts(
            current_used_bytes=500000000000,
            total_bytes=1000000000000,
            growth_rate_bytes_per_day=1000000000,
            days_to_forecast=90,
        )

        # Should include daily, weekly, and monthly intervals
        assert len(result) > 7
        assert result[-1].days_from_now == 90

    def test_capped_at_total(self) -> None:
        """Test usage is capped at total capacity."""
        result = _generate_forecasts(
            current_used_bytes=900000000000,
            total_bytes=1000000000000,
            growth_rate_bytes_per_day=50000000000,  # Very high growth
            days_to_forecast=30,
        )

        for forecast in result:
            assert forecast.predicted_used_bytes <= 1000000000000
            assert forecast.predicted_percent_used <= 100.0

    def test_status_progression(self) -> None:
        """Test status progresses from normal to emergency."""
        result = _generate_forecasts(
            current_used_bytes=500000000000,  # 50%
            total_bytes=1000000000000,
            growth_rate_bytes_per_day=10000000000,  # 1%/day
            days_to_forecast=60,
        )

        # Should see status progression
        statuses = [f.predicted_status for f in result]
        assert CapacityStatus.NORMAL in statuses or CapacityStatus.WARNING in statuses


class TestGenerateCapacityRecommendations:
    """Tests for _generate_capacity_recommendations function."""

    def test_critical_current(self) -> None:
        """Test critical current capacity."""
        result = _generate_capacity_recommendations(
            current_percent=85.0,
            days_until_warning=None,
            days_until_critical=None,
            days_until_full=5,
            growth_rate_bytes_per_day=10000000000,
            total_bytes=1000000000000,
        )

        assert any("URGENT" in r for r in result)

    def test_warning_current(self) -> None:
        """Test warning current capacity."""
        result = _generate_capacity_recommendations(
            current_percent=72.0,
            days_until_warning=None,
            days_until_critical=20,
            days_until_full=60,
            growth_rate_bytes_per_day=5000000000,
            total_bytes=1000000000000,
        )

        assert any("warning" in r.lower() for r in result)

    def test_approaching_warning_soon(self) -> None:
        """Test approaching warning threshold soon."""
        result = _generate_capacity_recommendations(
            current_percent=60.0,
            days_until_warning=10,
            days_until_critical=30,
            days_until_full=60,
            growth_rate_bytes_per_day=5000000000,
            total_bytes=1000000000000,
        )

        assert any("warning threshold" in r.lower() for r in result)

    def test_no_growth(self) -> None:
        """Test no growth detected."""
        result = _generate_capacity_recommendations(
            current_percent=50.0,
            days_until_warning=None,
            days_until_critical=None,
            days_until_full=None,
            growth_rate_bytes_per_day=0,
            total_bytes=1000000000000,
        )

        assert any("No storage growth detected" in r for r in result)

    def test_healthy_with_growth(self) -> None:
        """Test healthy capacity with normal growth."""
        result = _generate_capacity_recommendations(
            current_percent=40.0,
            days_until_warning=120,
            days_until_critical=180,
            days_until_full=365,
            growth_rate_bytes_per_day=1000000000,
            total_bytes=1000000000000,
        )

        assert any("growth rate" in r.lower() for r in result)


class TestDetermineConfidence:
    """Tests for _determine_confidence function."""

    def test_user_provided_rate(self) -> None:
        """Test user-provided growth rate confidence."""
        result = _determine_confidence(
            has_historical_data=True,
            days_to_forecast=30,
            growth_rate_provided=True,
        )

        assert "Medium" in result
        assert "user-provided" in result.lower()

    def test_no_historical_data(self) -> None:
        """Test no historical data confidence."""
        result = _determine_confidence(
            has_historical_data=False,
            days_to_forecast=30,
            growth_rate_provided=False,
        )

        assert "Low" in result
        assert "estimated" in result.lower()

    def test_short_term_forecast(self) -> None:
        """Test short-term forecast confidence."""
        result = _determine_confidence(
            has_historical_data=True,
            days_to_forecast=5,
            growth_rate_provided=False,
        )

        assert "High" in result

    def test_monthly_forecast(self) -> None:
        """Test monthly forecast confidence."""
        result = _determine_confidence(
            has_historical_data=True,
            days_to_forecast=25,
            growth_rate_provided=False,
        )

        assert "Medium" in result

    def test_long_term_forecast(self) -> None:
        """Test long-term forecast confidence."""
        result = _determine_confidence(
            has_historical_data=True,
            days_to_forecast=180,
            growth_rate_provided=False,
        )

        assert "Low" in result


class TestPredictCapacity:
    """Tests for predict_capacity function."""

    @pytest.fixture
    def mock_kubernetes_adapter(self) -> AsyncMock:
        """Create mock Kubernetes adapter."""
        return AsyncMock()

    @pytest.fixture
    def sample_capacity_data(self) -> dict[str, Any]:
        """Create sample capacity data."""
        return {
            "total_bytes": 10000000000000,  # 10 TB
            "used_bytes": 5000000000000,  # 5 TB (50%)
            "available_bytes": 5000000000000,
            "capacity_percent": 50.0,
        }

    @pytest.mark.asyncio
    async def test_predict_success(
        self,
        mock_kubernetes_adapter: AsyncMock,
        sample_capacity_data: dict[str, Any],
    ) -> None:
        """Test successful capacity prediction."""
        mock_ceph = AsyncMock()
        mock_ceph.get_capacity.return_value = sample_capacity_data

        with patch(
            "mosk_mcp.tools.ceph_operations.predict_capacity.CephAdapter",
        ) as MockCephAdapter:
            MockCephAdapter.return_value.__aenter__.return_value = mock_ceph
            MockCephAdapter.return_value.__aexit__.return_value = None

            result = await predict_capacity(mock_kubernetes_adapter, days_to_forecast=30)

        assert result.current_used_bytes == 5000000000000
        assert result.current_percent_used == 50.0
        assert len(result.forecasts) > 0
        assert result.confidence is not None

    @pytest.mark.asyncio
    async def test_predict_with_custom_growth_rate(
        self,
        mock_kubernetes_adapter: AsyncMock,
        sample_capacity_data: dict[str, Any],
    ) -> None:
        """Test prediction with custom growth rate."""
        mock_ceph = AsyncMock()
        mock_ceph.get_capacity.return_value = sample_capacity_data

        with patch(
            "mosk_mcp.tools.ceph_operations.predict_capacity.CephAdapter",
        ) as MockCephAdapter:
            MockCephAdapter.return_value.__aenter__.return_value = mock_ceph
            MockCephAdapter.return_value.__aexit__.return_value = None

            result = await predict_capacity(
                mock_kubernetes_adapter,
                days_to_forecast=30,
                growth_rate_gb_per_day=10.0,  # 10 GB/day
            )

        assert result.growth_rate_bytes_per_day == 10 * 1024 * 1024 * 1024
        assert "user-provided" in result.confidence.lower()

    @pytest.mark.asyncio
    async def test_predict_with_recommendations(
        self,
        mock_kubernetes_adapter: AsyncMock,
        sample_capacity_data: dict[str, Any],
    ) -> None:
        """Test prediction with recommendations."""
        mock_ceph = AsyncMock()
        mock_ceph.get_capacity.return_value = sample_capacity_data

        with patch(
            "mosk_mcp.tools.ceph_operations.predict_capacity.CephAdapter",
        ) as MockCephAdapter:
            MockCephAdapter.return_value.__aenter__.return_value = mock_ceph
            MockCephAdapter.return_value.__aexit__.return_value = None

            result = await predict_capacity(
                mock_kubernetes_adapter,
                days_to_forecast=30,
                include_recommendations=True,
            )

        assert len(result.recommendations) > 0

    @pytest.mark.asyncio
    async def test_predict_without_recommendations(
        self,
        mock_kubernetes_adapter: AsyncMock,
        sample_capacity_data: dict[str, Any],
    ) -> None:
        """Test prediction without recommendations."""
        mock_ceph = AsyncMock()
        mock_ceph.get_capacity.return_value = sample_capacity_data

        with patch(
            "mosk_mcp.tools.ceph_operations.predict_capacity.CephAdapter",
        ) as MockCephAdapter:
            MockCephAdapter.return_value.__aenter__.return_value = mock_ceph
            MockCephAdapter.return_value.__aexit__.return_value = None

            result = await predict_capacity(
                mock_kubernetes_adapter,
                days_to_forecast=30,
                include_recommendations=False,
            )

        assert result.recommendations == []

    @pytest.mark.asyncio
    async def test_predict_short_term(
        self,
        mock_kubernetes_adapter: AsyncMock,
        sample_capacity_data: dict[str, Any],
    ) -> None:
        """Test short-term prediction."""
        mock_ceph = AsyncMock()
        mock_ceph.get_capacity.return_value = sample_capacity_data

        with patch(
            "mosk_mcp.tools.ceph_operations.predict_capacity.CephAdapter",
        ) as MockCephAdapter:
            MockCephAdapter.return_value.__aenter__.return_value = mock_ceph
            MockCephAdapter.return_value.__aexit__.return_value = None

            result = await predict_capacity(mock_kubernetes_adapter, days_to_forecast=7)

        assert len(result.forecasts) >= 7
        assert result.forecasts[-1].days_from_now == 7

    @pytest.mark.asyncio
    async def test_predict_long_term(
        self,
        mock_kubernetes_adapter: AsyncMock,
        sample_capacity_data: dict[str, Any],
    ) -> None:
        """Test long-term prediction."""
        mock_ceph = AsyncMock()
        mock_ceph.get_capacity.return_value = sample_capacity_data

        with patch(
            "mosk_mcp.tools.ceph_operations.predict_capacity.CephAdapter",
        ) as MockCephAdapter:
            MockCephAdapter.return_value.__aenter__.return_value = mock_ceph
            MockCephAdapter.return_value.__aexit__.return_value = None

            result = await predict_capacity(mock_kubernetes_adapter, days_to_forecast=365)

        assert result.forecasts[-1].days_from_now == 365
        assert "Low" in result.confidence

    @pytest.mark.asyncio
    async def test_predict_days_until_thresholds(
        self,
        mock_kubernetes_adapter: AsyncMock,
        sample_capacity_data: dict[str, Any],
    ) -> None:
        """Test days until threshold calculation."""
        mock_ceph = AsyncMock()
        mock_ceph.get_capacity.return_value = sample_capacity_data

        with patch(
            "mosk_mcp.tools.ceph_operations.predict_capacity.CephAdapter",
        ) as MockCephAdapter:
            MockCephAdapter.return_value.__aenter__.return_value = mock_ceph
            MockCephAdapter.return_value.__aexit__.return_value = None

            result = await predict_capacity(mock_kubernetes_adapter, days_to_forecast=90)

        # With 50% usage and default growth, should calculate days until thresholds
        assert result.days_until_warning is not None or result.days_until_warning == 0
        assert result.days_until_critical is not None or result.days_until_critical == 0

    @pytest.mark.asyncio
    async def test_predict_error(self, mock_kubernetes_adapter: AsyncMock) -> None:
        """Test error handling."""
        mock_ceph = AsyncMock()
        mock_ceph.get_capacity.side_effect = Exception("Connection failed")

        with patch(
            "mosk_mcp.tools.ceph_operations.predict_capacity.CephAdapter",
        ) as MockCephAdapter:
            MockCephAdapter.return_value.__aenter__.return_value = mock_ceph
            MockCephAdapter.return_value.__aexit__.return_value = None

            with pytest.raises(ToolExecutionError) as exc_info:
                await predict_capacity(mock_kubernetes_adapter)

        assert "Failed to predict capacity" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_predict_growth_rate_human(
        self,
        mock_kubernetes_adapter: AsyncMock,
        sample_capacity_data: dict[str, Any],
    ) -> None:
        """Test human-readable growth rate."""
        mock_ceph = AsyncMock()
        mock_ceph.get_capacity.return_value = sample_capacity_data

        with patch(
            "mosk_mcp.tools.ceph_operations.predict_capacity.CephAdapter",
        ) as MockCephAdapter:
            MockCephAdapter.return_value.__aenter__.return_value = mock_ceph
            MockCephAdapter.return_value.__aexit__.return_value = None

            result = await predict_capacity(mock_kubernetes_adapter)

        assert "/day" in result.growth_rate_human
