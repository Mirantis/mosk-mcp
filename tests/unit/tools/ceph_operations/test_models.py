"""Unit tests for ceph_operations models."""

import pytest
from pydantic import ValidationError

from mosk_mcp.tools.ceph_operations.models import (
    CapacityForecast,
    CapacitySummary,
    CephHealthLevel,
    GetCephCapacityInput,
    GetCephCapacityOutput,
    GetCephStatusInput,
    GetCephStatusOutput,
    GetOSDDetailsInput,
    GetOSDDetailsOutput,
    GetPGStatusInput,
    GetPGStatusOutput,
    GetRecoveryStatusInput,
    GetRecoveryStatusOutput,
    HealthCheckInfo,
    ListOSDsInput,
    ListOSDsOutput,
    OSDDetails,
    OSDSummary,
    PGStateCount,
    PoolCapacity,
    PredictCapacityInput,
    PredictCapacityOutput,
    RecoveryProgress,
)
from mosk_mcp.tools.common.enums import CapacityStatus


class TestCephHealthLevel:
    """Tests for CephHealthLevel enum."""

    def test_all_levels_defined(self) -> None:
        """Test all expected health levels are defined."""
        assert CephHealthLevel.HEALTH_OK == "HEALTH_OK"
        assert CephHealthLevel.HEALTH_WARN == "HEALTH_WARN"
        assert CephHealthLevel.HEALTH_ERR == "HEALTH_ERR"
        assert CephHealthLevel.UNKNOWN == "UNKNOWN"

    def test_enum_values(self) -> None:
        """Test enum values are strings."""
        for level in CephHealthLevel:
            assert isinstance(level.value, str)


class TestGetCephStatusInput:
    """Tests for GetCephStatusInput model."""

    def test_default_values(self) -> None:
        """Test default values."""
        input_model = GetCephStatusInput()
        assert input_model.include_health_details is True
        assert input_model.include_pg_summary is True

    def test_custom_values(self) -> None:
        """Test custom values."""
        input_model = GetCephStatusInput(
            include_health_details=False,
            include_pg_summary=False,
        )
        assert input_model.include_health_details is False
        assert input_model.include_pg_summary is False


class TestHealthCheckInfo:
    """Tests for HealthCheckInfo model."""

    def test_required_fields(self) -> None:
        """Test required fields."""
        info = HealthCheckInfo(severity="WARN", message="Test warning")
        assert info.severity == "WARN"
        assert info.message == "Test warning"
        assert info.count == 1

    def test_custom_count(self) -> None:
        """Test custom count."""
        info = HealthCheckInfo(severity="ERR", message="Test error", count=5)
        assert info.count == 5


class TestCapacitySummary:
    """Tests for CapacitySummary model."""

    def test_required_fields(self) -> None:
        """Test required fields."""
        summary = CapacitySummary(
            total_bytes=1000000000,
            used_bytes=500000000,
            available_bytes=500000000,
            percent_used=50.0,
            status=CapacityStatus.NORMAL,
            total_human="1 GB",
            used_human="500 MB",
            available_human="500 MB",
        )
        assert summary.total_bytes == 1000000000
        assert summary.percent_used == 50.0
        assert summary.status == CapacityStatus.NORMAL

    def test_validation_percent_used_bounds(self) -> None:
        """Test percent_used validation bounds."""
        # Valid: 0%
        summary = CapacitySummary(
            total_bytes=100,
            used_bytes=0,
            available_bytes=100,
            percent_used=0.0,
            status=CapacityStatus.NORMAL,
            total_human="100",
            used_human="0",
            available_human="100",
        )
        assert summary.percent_used == 0.0

        # Valid: 100%
        summary = CapacitySummary(
            total_bytes=100,
            used_bytes=100,
            available_bytes=0,
            percent_used=100.0,
            status=CapacityStatus.CRITICAL,
            total_human="100",
            used_human="100",
            available_human="0",
        )
        assert summary.percent_used == 100.0

    def test_validation_negative_bytes(self) -> None:
        """Test negative bytes validation."""
        with pytest.raises(ValidationError):
            CapacitySummary(
                total_bytes=-100,
                used_bytes=50,
                available_bytes=50,
                percent_used=50.0,
                status=CapacityStatus.NORMAL,
                total_human="100",
                used_human="50",
                available_human="50",
            )


class TestGetCephStatusOutput:
    """Tests for GetCephStatusOutput model."""

    @pytest.fixture
    def capacity_summary(self) -> CapacitySummary:
        """Create capacity summary for tests."""
        return CapacitySummary(
            total_bytes=1000000000,
            used_bytes=500000000,
            available_bytes=500000000,
            percent_used=50.0,
            status=CapacityStatus.NORMAL,
            total_human="1 GB",
            used_human="500 MB",
            available_human="500 MB",
        )

    def test_required_fields(self, capacity_summary: CapacitySummary) -> None:
        """Test required fields."""
        output = GetCephStatusOutput(
            health=CephHealthLevel.HEALTH_OK,
            health_summary="Cluster is healthy",
            fsid="abc-123",
            quorum=["mon1", "mon2", "mon3"],
            num_osds=10,
            num_osds_up=10,
            num_osds_in=10,
            num_pgs=100,
            capacity=capacity_summary,
            is_healthy=True,
            is_safe_for_operations=True,
            timestamp="2025-01-01T00:00:00Z",
        )
        assert output.health == CephHealthLevel.HEALTH_OK
        assert output.is_healthy is True
        assert output.num_osds == 10

    def test_default_values(self, capacity_summary: CapacitySummary) -> None:
        """Test default values."""
        output = GetCephStatusOutput(
            health=CephHealthLevel.HEALTH_OK,
            health_summary="OK",
            fsid="abc",
            quorum=["mon1"],
            num_osds=1,
            num_osds_up=1,
            num_osds_in=1,
            num_pgs=1,
            capacity=capacity_summary,
            is_healthy=True,
            is_safe_for_operations=True,
            timestamp="2025-01-01T00:00:00Z",
        )
        assert output.health_checks == {}
        assert output.pg_summary == {}
        assert output.warnings == []


class TestListOSDsInput:
    """Tests for ListOSDsInput model."""

    def test_default_values(self) -> None:
        """Test default values."""
        input_model = ListOSDsInput()
        assert input_model.host_filter is None
        assert input_model.status_filter is None
        assert input_model.include_performance is False

    def test_status_filter_values(self) -> None:
        """Test valid status filter values."""
        for status in ["all", "up", "down"]:
            input_model = ListOSDsInput(status_filter=status)
            assert input_model.status_filter == status


class TestOSDSummary:
    """Tests for OSDSummary model."""

    def test_required_fields(self) -> None:
        """Test required fields."""
        osd = OSDSummary(
            osd_id=0,
            host="node1",
            status="up",
            state="in",
            utilization_percent=50.0,
            capacity_bytes=1000000000,
            used_bytes=500000000,
            pgs=100,
            is_healthy=True,
        )
        assert osd.osd_id == 0
        assert osd.host == "node1"
        assert osd.status == "up"
        assert osd.is_healthy is True

    def test_default_values(self) -> None:
        """Test default values."""
        osd = OSDSummary(
            osd_id=0,
            host="node1",
            status="up",
            state="in",
            utilization_percent=50.0,
            capacity_bytes=1000000000,
            used_bytes=500000000,
            pgs=100,
            is_healthy=True,
        )
        assert osd.device_class == ""


class TestListOSDsOutput:
    """Tests for ListOSDsOutput model."""

    def test_required_fields(self) -> None:
        """Test required fields."""
        output = ListOSDsOutput(
            osds=[],
            total_count=0,
            up_count=0,
            down_count=0,
            in_count=0,
            out_count=0,
        )
        assert output.total_count == 0
        assert output.osds == []

    def test_default_values(self) -> None:
        """Test default values."""
        output = ListOSDsOutput(
            osds=[],
            total_count=0,
            up_count=0,
            down_count=0,
            in_count=0,
            out_count=0,
        )
        assert output.by_host == {}
        assert output.by_device_class == {}


class TestGetOSDDetailsInput:
    """Tests for GetOSDDetailsInput model."""

    def test_required_osd_id(self) -> None:
        """Test OSD ID is required."""
        input_model = GetOSDDetailsInput(osd_id=5)
        assert input_model.osd_id == 5

    def test_default_values(self) -> None:
        """Test default values."""
        input_model = GetOSDDetailsInput(osd_id=0)
        assert input_model.include_pg_distribution is True
        assert input_model.include_performance is True

    def test_osd_id_validation(self) -> None:
        """Test OSD ID must be non-negative."""
        with pytest.raises(ValidationError):
            GetOSDDetailsInput(osd_id=-1)


class TestOSDDetails:
    """Tests for OSDDetails model."""

    @pytest.fixture
    def capacity_summary(self) -> CapacitySummary:
        """Create capacity summary for tests."""
        return CapacitySummary(
            total_bytes=1000000000,
            used_bytes=500000000,
            available_bytes=500000000,
            percent_used=50.0,
            status=CapacityStatus.NORMAL,
            total_human="1 GB",
            used_human="500 MB",
            available_human="500 MB",
        )

    def test_required_fields(self, capacity_summary: CapacitySummary) -> None:
        """Test required fields."""
        osd = OSDDetails(
            osd_id=0,
            uuid="abc-123",
            host="node1",
            status="up",
            state="in",
            crush_weight=1.0,
            reweight=1.0,
            capacity=capacity_summary,
            pgs=100,
            commit_latency_ms=0.5,
            apply_latency_ms=1.0,
            is_healthy=True,
        )
        assert osd.osd_id == 0
        assert osd.uuid == "abc-123"
        assert osd.is_healthy is True


class TestGetOSDDetailsOutput:
    """Tests for GetOSDDetailsOutput model."""

    @pytest.fixture
    def osd_details(self) -> OSDDetails:
        """Create OSD details for tests."""
        capacity = CapacitySummary(
            total_bytes=1000000000,
            used_bytes=500000000,
            available_bytes=500000000,
            percent_used=50.0,
            status=CapacityStatus.NORMAL,
            total_human="1 GB",
            used_human="500 MB",
            available_human="500 MB",
        )
        return OSDDetails(
            osd_id=0,
            uuid="abc-123",
            host="node1",
            status="up",
            state="in",
            crush_weight=1.0,
            reweight=1.0,
            capacity=capacity,
            pgs=100,
            commit_latency_ms=0.5,
            apply_latency_ms=1.0,
            is_healthy=True,
        )

    def test_required_fields(self, osd_details: OSDDetails) -> None:
        """Test required fields."""
        output = GetOSDDetailsOutput(osd=osd_details)
        assert output.osd.osd_id == 0

    def test_default_values(self, osd_details: OSDDetails) -> None:
        """Test default values."""
        output = GetOSDDetailsOutput(osd=osd_details)
        assert output.recommendations == []


class TestGetCephCapacityInput:
    """Tests for GetCephCapacityInput model."""

    def test_default_values(self) -> None:
        """Test default values."""
        input_model = GetCephCapacityInput()
        assert input_model.include_pools is True
        assert input_model.include_classes is True


class TestPoolCapacity:
    """Tests for PoolCapacity model."""

    def test_required_fields(self) -> None:
        """Test required fields."""
        pool = PoolCapacity(
            pool_id=1,
            pool_name="rbd",
            stored_bytes=100000000,
            used_bytes=300000000,
            max_available_bytes=700000000,
            percent_used=30.0,
            objects=1000,
            replication_size=3,
        )
        assert pool.pool_id == 1
        assert pool.pool_name == "rbd"
        assert pool.replication_size == 3


class TestGetCephCapacityOutput:
    """Tests for GetCephCapacityOutput model."""

    def test_required_fields(self) -> None:
        """Test required fields."""
        output = GetCephCapacityOutput(
            total_bytes=1000000000,
            used_bytes=500000000,
            available_bytes=500000000,
            percent_used=50.0,
            status=CapacityStatus.NORMAL,
            thresholds={"warning": 70, "critical": 85},
            timestamp="2025-01-01T00:00:00Z",
        )
        assert output.percent_used == 50.0
        assert output.status == CapacityStatus.NORMAL

    def test_default_values(self) -> None:
        """Test default values."""
        output = GetCephCapacityOutput(
            total_bytes=1000000000,
            used_bytes=500000000,
            available_bytes=500000000,
            percent_used=50.0,
            status=CapacityStatus.NORMAL,
            thresholds={},
            timestamp="2025-01-01T00:00:00Z",
        )
        assert output.pools == []
        assert output.by_device_class == {}
        assert output.recommendations == []


class TestGetPGStatusInput:
    """Tests for GetPGStatusInput model."""

    def test_default_values(self) -> None:
        """Test default values."""
        input_model = GetPGStatusInput()
        assert input_model.include_stuck is True
        assert input_model.include_recovery is True


class TestPGStateCount:
    """Tests for PGStateCount model."""

    def test_required_fields(self) -> None:
        """Test required fields."""
        state = PGStateCount(
            state="active+clean",
            count=100,
            is_healthy=True,
        )
        assert state.state == "active+clean"
        assert state.count == 100
        assert state.is_healthy is True


class TestGetPGStatusOutput:
    """Tests for GetPGStatusOutput model."""

    def test_required_fields(self) -> None:
        """Test required fields."""
        output = GetPGStatusOutput(
            total_pgs=100,
            active_clean=100,
            states=[],
            is_healthy=True,
            recovery_active=False,
            misplaced_ratio=0.0,
            degraded_ratio=0.0,
            health_summary="All PGs healthy",
        )
        assert output.total_pgs == 100
        assert output.is_healthy is True

    def test_default_values(self) -> None:
        """Test default values."""
        output = GetPGStatusOutput(
            total_pgs=100,
            active_clean=100,
            states=[],
            is_healthy=True,
            recovery_active=False,
            misplaced_ratio=0.0,
            degraded_ratio=0.0,
            health_summary="OK",
        )
        assert output.stuck_pgs == {}
        assert output.recommendations == []


class TestPredictCapacityInput:
    """Tests for PredictCapacityInput model."""

    def test_default_values(self) -> None:
        """Test default values."""
        input_model = PredictCapacityInput()
        assert input_model.days_to_forecast == 30
        assert input_model.growth_rate_gb_per_day is None
        assert input_model.include_recommendations is True

    def test_days_bounds(self) -> None:
        """Test days_to_forecast bounds."""
        # Valid: 1 day
        input_model = PredictCapacityInput(days_to_forecast=1)
        assert input_model.days_to_forecast == 1

        # Valid: 365 days
        input_model = PredictCapacityInput(days_to_forecast=365)
        assert input_model.days_to_forecast == 365

        # Invalid: 0 days
        with pytest.raises(ValidationError):
            PredictCapacityInput(days_to_forecast=0)

        # Invalid: 366 days
        with pytest.raises(ValidationError):
            PredictCapacityInput(days_to_forecast=366)


class TestCapacityForecast:
    """Tests for CapacityForecast model."""

    def test_required_fields(self) -> None:
        """Test required fields."""
        forecast = CapacityForecast(
            date="2025-02-01",
            days_from_now=30,
            predicted_used_bytes=600000000,
            predicted_percent_used=60.0,
            predicted_status=CapacityStatus.NORMAL,
        )
        assert forecast.date == "2025-02-01"
        assert forecast.days_from_now == 30
        assert forecast.predicted_percent_used == 60.0


class TestPredictCapacityOutput:
    """Tests for PredictCapacityOutput model."""

    def test_required_fields(self) -> None:
        """Test required fields."""
        output = PredictCapacityOutput(
            current_used_bytes=500000000,
            current_percent_used=50.0,
            growth_rate_bytes_per_day=10000000,
            growth_rate_human="10 MB/day",
            forecasts=[],
            confidence="medium",
        )
        assert output.current_percent_used == 50.0
        assert output.confidence == "medium"

    def test_optional_fields(self) -> None:
        """Test optional fields."""
        output = PredictCapacityOutput(
            current_used_bytes=500000000,
            current_percent_used=50.0,
            growth_rate_bytes_per_day=10000000,
            growth_rate_human="10 MB/day",
            forecasts=[],
            days_until_warning=30,
            days_until_critical=60,
            days_until_full=90,
            confidence="high",
        )
        assert output.days_until_warning == 30
        assert output.days_until_critical == 60
        assert output.days_until_full == 90


class TestGetRecoveryStatusInput:
    """Tests for GetRecoveryStatusInput model."""

    def test_default_values(self) -> None:
        """Test default values."""
        input_model = GetRecoveryStatusInput()
        assert input_model.include_pg_details is False
        assert input_model.include_osd_details is False


class TestRecoveryProgress:
    """Tests for RecoveryProgress model."""

    def test_required_fields(self) -> None:
        """Test required fields."""
        progress = RecoveryProgress(
            objects_recovered=500,
            objects_to_recover=1000,
            bytes_recovered=50000000,
            bytes_to_recover=100000000,
            percent_complete=50.0,
            recovery_rate_bytes_per_sec=1000000,
            estimated_time_remaining="50 seconds",
        )
        assert progress.percent_complete == 50.0
        assert progress.estimated_time_remaining == "50 seconds"


class TestGetRecoveryStatusOutput:
    """Tests for GetRecoveryStatusOutput model."""

    def test_required_fields(self) -> None:
        """Test required fields."""
        output = GetRecoveryStatusOutput(
            is_recovering=False,
            is_backfilling=False,
            is_rebalancing=False,
            misplaced_objects=0,
            misplaced_ratio=0.0,
            degraded_objects=0,
            degraded_ratio=0.0,
            pgs_recovering=0,
            pgs_backfilling=0,
            status_summary="Cluster is idle",
            timestamp="2025-01-01T00:00:00Z",
        )
        assert output.is_recovering is False
        assert output.status_summary == "Cluster is idle"

    def test_with_recovery_progress(self) -> None:
        """Test with recovery progress."""
        progress = RecoveryProgress(
            objects_recovered=500,
            objects_to_recover=1000,
            bytes_recovered=50000000,
            bytes_to_recover=100000000,
            percent_complete=50.0,
            recovery_rate_bytes_per_sec=1000000,
            estimated_time_remaining="50 seconds",
        )
        output = GetRecoveryStatusOutput(
            is_recovering=True,
            is_backfilling=False,
            is_rebalancing=False,
            recovery_progress=progress,
            misplaced_objects=500,
            misplaced_ratio=0.05,
            degraded_objects=100,
            degraded_ratio=0.01,
            pgs_recovering=10,
            pgs_backfilling=0,
            status_summary="Recovery in progress",
            timestamp="2025-01-01T00:00:00Z",
        )
        assert output.recovery_progress is not None
        assert output.recovery_progress.percent_complete == 50.0
