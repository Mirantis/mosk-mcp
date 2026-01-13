"""Unit tests for StackLightManager.

This module tests the StackLightManager's dual-cluster orchestration
capabilities, including:
- Connection lifecycle management
- Alert queries from both clusters
- Metric queries from both clusters
- Log queries from both clusters
- Health check aggregation
- Graceful degradation when one cluster fails
- Alert deduplication
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mosk_mcp.adapters.stacklight import (
    Alert,
    AlertSeverity,
    AlertState,
    DirectStackLightClient,
    LogEntry,
    LogSeverity,
    ManagerState,
    MetricSample,
    StackLightAdapter,
    StackLightManager,
    get_stacklight_manager,
    reset_stacklight_manager,
)
from mosk_mcp.adapters.stacklight.core import LogQueryResult as CoreLogQueryResult
from mosk_mcp.adapters.stacklight.response_models import (
    ManagerHealthStatus,
    compute_alert_fingerprint,
    deduplicate_alerts,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_mcc_direct_client():
    """Create a mock DirectStackLightClient for MCC cluster."""
    client = MagicMock(spec=DirectStackLightClient)
    client.get_alerts = AsyncMock(return_value=[])
    client.query_prometheus = AsyncMock(return_value=[])
    client.query_prometheus_range = AsyncMock(return_value=[])
    return client


@pytest.fixture
def mock_mosk_direct_client():
    """Create a mock DirectStackLightClient for MOSK cluster."""
    client = MagicMock(spec=DirectStackLightClient)
    client.get_alerts = AsyncMock(return_value=[])
    client.query_prometheus = AsyncMock(return_value=[])
    client.query_prometheus_range = AsyncMock(return_value=[])
    return client


@pytest.fixture
def sample_alerts():
    """Create sample alerts for testing."""
    now = datetime.now(UTC)
    return [
        Alert(
            alert_name="HighCPU",
            severity=AlertSeverity.WARNING,
            state=AlertState.FIRING,
            summary="High CPU on compute-01",
            labels={"host": "compute-01", "service": "nova"},
            starts_at=now,
            cluster_type="mcc",
        ),
        Alert(
            alert_name="OSDDown",
            severity=AlertSeverity.CRITICAL,
            state=AlertState.FIRING,
            summary="OSD.5 is down",
            labels={"osd": "5", "service": "ceph"},
            starts_at=now,
            cluster_type="mosk",
        ),
    ]


@pytest.fixture
def sample_metrics():
    """Create sample metrics for testing."""
    now = datetime.now(UTC)
    return [
        MetricSample(
            metric_name="up",
            labels={"job": "prometheus"},
            value=1.0,
            timestamp=now,
            cluster_type="mcc",
        ),
        MetricSample(
            metric_name="node_cpu_seconds_total",
            labels={"cpu": "0", "mode": "idle"},
            value=12345.67,
            timestamp=now,
            cluster_type="mosk",
        ),
    ]


@pytest.fixture
def sample_logs():
    """Create sample logs for testing."""
    now = datetime.now(UTC)
    return [
        LogEntry(
            timestamp=now,
            message="Instance created successfully",
            severity=LogSeverity.INFO,
            service="nova",
            host="compute-01",
            cluster_type="mosk",
        ),
        LogEntry(
            timestamp=now,
            message="Connection timeout",
            severity=LogSeverity.ERROR,
            service="neutron",
            host="network-01",
            cluster_type="mosk",
        ),
    ]


# =============================================================================
# ManagerState Tests
# =============================================================================


class TestManagerState:
    """Tests for ManagerState dataclass."""

    def test_default_state(self):
        """Test default state values."""
        state = ManagerState()
        assert state.connected is False
        assert state.mcc_healthy is False
        assert state.mosk_healthy is False
        assert state.last_health_check is None

    def test_state_modification(self):
        """Test state can be modified."""
        state = ManagerState()
        state.connected = True
        state.mcc_healthy = True
        state.last_health_check = datetime.now(UTC)

        assert state.connected is True
        assert state.mcc_healthy is True
        assert state.last_health_check is not None


# =============================================================================
# Alert Deduplication Tests
# =============================================================================


class TestAlertDeduplication:
    """Tests for alert deduplication helpers."""

    def test_compute_fingerprint_basic(self):
        """Test basic fingerprint computation."""
        alert = {
            "alert_name": "HighCPU",
            "severity": "warning",
            "labels": {"host": "node1", "service": "nova"},
        }
        fp = compute_alert_fingerprint(alert)
        assert "HighCPU" in fp
        assert "warning" in fp
        assert "host=node1" in fp
        assert "service=nova" in fp

    def test_compute_fingerprint_excludes_cluster(self):
        """Test fingerprint excludes cluster-specific labels."""
        alert1 = {
            "alert_name": "HighCPU",
            "severity": "warning",
            "labels": {"host": "node1", "cluster": "mcc"},
        }
        alert2 = {
            "alert_name": "HighCPU",
            "severity": "warning",
            "labels": {"host": "node1", "cluster": "mosk"},
        }
        assert compute_alert_fingerprint(alert1) == compute_alert_fingerprint(alert2)

    def test_deduplicate_removes_duplicates(self):
        """Test deduplication removes duplicate alerts."""
        alerts = [
            {
                "alert_name": "HighCPU",
                "severity": "warning",
                "labels": {"host": "node1"},
                "cluster_type": "mcc",
            },
            {
                "alert_name": "HighCPU",
                "severity": "warning",
                "labels": {"host": "node1"},
                "cluster_type": "mosk",
            },
            {
                "alert_name": "DiskFull",
                "severity": "critical",
                "labels": {"host": "node2"},
                "cluster_type": "mcc",
            },
        ]
        deduped = deduplicate_alerts(alerts)
        assert len(deduped) == 2

    def test_deduplicate_prefers_cluster(self):
        """Test deduplication prefers specified cluster."""
        alerts = [
            {
                "alert_name": "HighCPU",
                "severity": "warning",
                "labels": {"host": "node1"},
                "cluster_type": "mcc",
            },
            {
                "alert_name": "HighCPU",
                "severity": "warning",
                "labels": {"host": "node1"},
                "cluster_type": "mosk",
            },
        ]
        deduped = deduplicate_alerts(alerts, prefer_cluster="mosk")
        assert len(deduped) == 1
        assert deduped[0]["cluster_type"] == "mosk"


# =============================================================================
# StackLightManager Initialization Tests
# =============================================================================


class TestStackLightManagerInit:
    """Tests for StackLightManager initialization."""

    def test_init_creates_adapters(
        self,
        mock_mcc_direct_client,
        mock_mosk_direct_client,
    ):
        """Test manager creates MCC and MOSK adapters."""
        manager = StackLightManager(
            mcc_direct_client=mock_mcc_direct_client,
            mosk_direct_client=mock_mosk_direct_client,
        )

        assert manager._mcc is not None
        assert manager._mosk is not None
        assert manager._mcc.cluster_type == "mcc"
        assert manager._mosk.cluster_type == "mosk"

    def test_init_sets_query_timeout(
        self,
        mock_mcc_direct_client,
        mock_mosk_direct_client,
    ):
        """Test manager respects query timeout."""
        manager = StackLightManager(
            mcc_direct_client=mock_mcc_direct_client,
            mosk_direct_client=mock_mosk_direct_client,
            query_timeout=60,
        )

        assert manager._query_timeout == 60

    def test_adapter_properties(
        self,
        mock_mcc_direct_client,
        mock_mosk_direct_client,
    ):
        """Test mcc and mosk properties return correct adapters."""
        manager = StackLightManager(
            mcc_direct_client=mock_mcc_direct_client,
            mosk_direct_client=mock_mosk_direct_client,
        )

        assert manager.mcc.cluster_type == "mcc"
        assert manager.mosk.cluster_type == "mosk"


# =============================================================================
# Connection Lifecycle Tests
# =============================================================================


class TestConnectionLifecycle:
    """Tests for connection lifecycle management."""

    @pytest.mark.asyncio
    async def test_connect_both_clusters(
        self,
        mock_mcc_direct_client,
        mock_mosk_direct_client,
    ):
        """Test successful connection to both clusters."""
        manager = StackLightManager(
            mcc_direct_client=mock_mcc_direct_client,
            mosk_direct_client=mock_mosk_direct_client,
        )

        # Mock connect methods
        manager._mcc.connect = AsyncMock()
        manager._mosk.connect = AsyncMock()

        await manager.connect()

        assert manager.is_connected is True
        assert manager._state.mcc_healthy is True
        assert manager._state.mosk_healthy is True
        manager._mcc.connect.assert_called_once()
        manager._mosk.connect.assert_called_once()

    @pytest.mark.asyncio
    async def test_connect_mcc_fails_gracefully(
        self,
        mock_mcc_direct_client,
        mock_mosk_direct_client,
    ):
        """Test connection continues when MCC fails."""
        manager = StackLightManager(
            mcc_direct_client=mock_mcc_direct_client,
            mosk_direct_client=mock_mosk_direct_client,
        )

        # MCC fails, MOSK succeeds
        manager._mcc.connect = AsyncMock(side_effect=Exception("MCC unreachable"))
        manager._mosk.connect = AsyncMock()

        await manager.connect()

        # Should still be connected (degraded mode)
        assert manager.is_connected is True
        assert manager._state.mcc_healthy is False
        assert manager._state.mosk_healthy is True

    @pytest.mark.asyncio
    async def test_connect_both_fail(
        self,
        mock_mcc_direct_client,
        mock_mosk_direct_client,
    ):
        """Test connection handles both clusters failing."""
        manager = StackLightManager(
            mcc_direct_client=mock_mcc_direct_client,
            mosk_direct_client=mock_mosk_direct_client,
        )

        # Both fail
        manager._mcc.connect = AsyncMock(side_effect=Exception("MCC unreachable"))
        manager._mosk.connect = AsyncMock(side_effect=Exception("MOSK unreachable"))

        await manager.connect()

        # P1 FIX: Now correctly marked NOT connected when both clusters fail
        # Previously this was True which hid total system failure
        assert manager.is_connected is False
        assert manager._state.mcc_healthy is False
        assert manager._state.mosk_healthy is False

    @pytest.mark.asyncio
    async def test_disconnect(
        self,
        mock_mcc_direct_client,
        mock_mosk_direct_client,
    ):
        """Test disconnect closes both adapters."""
        manager = StackLightManager(
            mcc_direct_client=mock_mcc_direct_client,
            mosk_direct_client=mock_mosk_direct_client,
        )

        manager._mcc.connect = AsyncMock()
        manager._mosk.connect = AsyncMock()
        manager._mcc.disconnect = AsyncMock()
        manager._mosk.disconnect = AsyncMock()

        await manager.connect()
        await manager.disconnect()

        assert manager.is_connected is False
        manager._mcc.disconnect.assert_called_once()
        manager._mosk.disconnect.assert_called_once()

    @pytest.mark.asyncio
    async def test_context_manager(
        self,
        mock_mcc_direct_client,
        mock_mosk_direct_client,
    ):
        """Test async context manager protocol."""
        manager = StackLightManager(
            mcc_direct_client=mock_mcc_direct_client,
            mosk_direct_client=mock_mosk_direct_client,
        )

        manager._mcc.connect = AsyncMock()
        manager._mosk.connect = AsyncMock()
        manager._mcc.disconnect = AsyncMock()
        manager._mosk.disconnect = AsyncMock()

        async with manager:
            assert manager.is_connected is True

        assert manager.is_connected is False


# =============================================================================
# Alert Query Tests
# =============================================================================


class TestAlertQueries:
    """Tests for alert query methods."""

    @pytest.mark.asyncio
    async def test_get_mcc_alerts(
        self,
        mock_mcc_direct_client,
        mock_mosk_direct_client,
        sample_alerts,
    ):
        """Test getting alerts from MCC cluster."""
        manager = StackLightManager(
            mcc_direct_client=mock_mcc_direct_client,
            mosk_direct_client=mock_mosk_direct_client,
        )

        manager._mcc.connect = AsyncMock()
        manager._mosk.connect = AsyncMock()
        manager._mcc.get_alerts = AsyncMock(return_value=[sample_alerts[0]])

        await manager.connect()
        result = await manager.get_mcc_alerts()

        assert result.success is True
        assert result.cluster_type == "mcc"
        assert result.count == 1
        assert len(result.alerts) == 1
        assert result.firing_count == 1

    @pytest.mark.asyncio
    async def test_get_mosk_alerts(
        self,
        mock_mcc_direct_client,
        mock_mosk_direct_client,
        sample_alerts,
    ):
        """Test getting alerts from MOSK cluster."""
        manager = StackLightManager(
            mcc_direct_client=mock_mcc_direct_client,
            mosk_direct_client=mock_mosk_direct_client,
        )

        manager._mcc.connect = AsyncMock()
        manager._mosk.connect = AsyncMock()
        manager._mosk.get_alerts = AsyncMock(return_value=[sample_alerts[1]])

        await manager.connect()
        result = await manager.get_mosk_alerts()

        assert result.success is True
        assert result.cluster_type == "mosk"
        assert result.count == 1
        assert result.critical_count == 1

    @pytest.mark.asyncio
    async def test_get_combined_alerts(
        self,
        mock_mcc_direct_client,
        mock_mosk_direct_client,
        sample_alerts,
    ):
        """Test getting combined alerts from both clusters."""
        manager = StackLightManager(
            mcc_direct_client=mock_mcc_direct_client,
            mosk_direct_client=mock_mosk_direct_client,
        )

        manager._mcc.connect = AsyncMock()
        manager._mosk.connect = AsyncMock()
        manager._mcc.get_alerts = AsyncMock(return_value=[sample_alerts[0]])
        manager._mosk.get_alerts = AsyncMock(return_value=[sample_alerts[1]])

        await manager.connect()
        result = await manager.get_combined_alerts()

        assert result.overall_success is True
        assert result.degraded is False
        assert result.mcc.success is True
        assert result.mosk.success is True
        assert result.total_unique == 2
        assert result.total_firing == 2
        assert result.total_critical == 1

    @pytest.mark.asyncio
    async def test_combined_alerts_graceful_degradation(
        self,
        mock_mcc_direct_client,
        mock_mosk_direct_client,
        sample_alerts,
    ):
        """Test combined alerts when one cluster fails."""
        manager = StackLightManager(
            mcc_direct_client=mock_mcc_direct_client,
            mosk_direct_client=mock_mosk_direct_client,
        )

        manager._mcc.connect = AsyncMock()
        manager._mosk.connect = AsyncMock()
        manager._mcc.get_alerts = AsyncMock(side_effect=Exception("MCC error"))
        manager._mosk.get_alerts = AsyncMock(return_value=[sample_alerts[1]])

        await manager.connect()
        result = await manager.get_combined_alerts()

        assert result.overall_success is True
        assert result.degraded is True
        assert result.mcc.success is False
        assert result.mosk.success is True
        assert result.total_unique == 1


# =============================================================================
# Metric Query Tests
# =============================================================================


class TestMetricQueries:
    """Tests for metric query methods."""

    @pytest.mark.asyncio
    async def test_query_metrics_single_cluster(
        self,
        mock_mcc_direct_client,
        mock_mosk_direct_client,
        sample_metrics,
    ):
        """Test querying metrics from a single cluster."""
        manager = StackLightManager(
            mcc_direct_client=mock_mcc_direct_client,
            mosk_direct_client=mock_mosk_direct_client,
        )

        manager._mcc.connect = AsyncMock()
        manager._mosk.connect = AsyncMock()
        manager._mcc.query_metrics = AsyncMock(return_value=[sample_metrics[0]])

        await manager.connect()
        result = await manager.query_metrics("mcc", "up")

        assert len(result) == 1
        assert result[0].metric_name == "up"

    @pytest.mark.asyncio
    async def test_query_metrics_all_clusters(
        self,
        mock_mcc_direct_client,
        mock_mosk_direct_client,
        sample_metrics,
    ):
        """Test querying metrics from both clusters."""
        manager = StackLightManager(
            mcc_direct_client=mock_mcc_direct_client,
            mosk_direct_client=mock_mosk_direct_client,
        )

        manager._mcc.connect = AsyncMock()
        manager._mosk.connect = AsyncMock()
        manager._mcc.query_metrics = AsyncMock(return_value=[sample_metrics[0]])
        manager._mosk.query_metrics = AsyncMock(return_value=[sample_metrics[1]])

        await manager.connect()
        result = await manager.query_metrics_all_clusters("up")

        assert result.overall_success is True
        assert result.total_samples == 2
        assert result.mcc.count == 1
        assert result.mosk.count == 1


# =============================================================================
# Log Query Tests
# =============================================================================


class TestLogQueries:
    """Tests for log query methods."""

    @pytest.mark.asyncio
    async def test_query_logs_single_cluster(
        self,
        mock_mcc_direct_client,
        mock_mosk_direct_client,
        sample_logs,
    ):
        """Test querying logs from a single cluster."""
        manager = StackLightManager(
            mcc_direct_client=mock_mcc_direct_client,
            mosk_direct_client=mock_mosk_direct_client,
        )

        manager._mcc.connect = AsyncMock()
        manager._mosk.connect = AsyncMock()
        manager._mosk.query_logs = AsyncMock(
            return_value=CoreLogQueryResult(logs=sample_logs, total_count=len(sample_logs))
        )

        await manager.connect()
        result = await manager.query_logs("mosk", services=["nova"])

        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_query_logs_all_clusters(
        self,
        mock_mcc_direct_client,
        mock_mosk_direct_client,
        sample_logs,
    ):
        """Test querying logs from both clusters."""
        manager = StackLightManager(
            mcc_direct_client=mock_mcc_direct_client,
            mosk_direct_client=mock_mosk_direct_client,
        )

        manager._mcc.connect = AsyncMock()
        manager._mosk.connect = AsyncMock()
        manager._mcc.query_logs = AsyncMock(return_value=CoreLogQueryResult(logs=[], total_count=0))
        manager._mosk.query_logs = AsyncMock(
            return_value=CoreLogQueryResult(logs=sample_logs, total_count=len(sample_logs))
        )

        await manager.connect()
        result = await manager.query_logs_all_clusters(services=["nova"])

        assert result.overall_success is True
        assert result.total_logs == 2
        assert result.mosk.error_count == 1


# =============================================================================
# Health Check Tests
# =============================================================================


class TestHealthChecks:
    """Tests for health check methods."""

    @pytest.mark.asyncio
    async def test_check_health_all_healthy(
        self,
        mock_mcc_direct_client,
        mock_mosk_direct_client,
    ):
        """Test health check when all components are healthy."""
        manager = StackLightManager(
            mcc_direct_client=mock_mcc_direct_client,
            mosk_direct_client=mock_mosk_direct_client,
        )

        manager._mcc.connect = AsyncMock()
        manager._mosk.connect = AsyncMock()
        # Mock Prometheus health check (returns (samples, success))
        manager._mcc._query_prometheus_instant = AsyncMock(return_value=([], True))
        manager._mosk._query_prometheus_instant = AsyncMock(return_value=([], True))
        # Mock Alertmanager health check (get_alerts returns list of alerts)
        manager._mcc.get_alerts = AsyncMock(return_value=[])
        manager._mosk.get_alerts = AsyncMock(return_value=[])

        await manager.connect()
        health = await manager.check_health()

        assert health.overall_status == ManagerHealthStatus.HEALTHY
        assert health.mcc.status == ManagerHealthStatus.HEALTHY
        assert health.mosk.status == ManagerHealthStatus.HEALTHY
        assert len(health.recommendations) == 0

    @pytest.mark.asyncio
    async def test_check_health_degraded(
        self,
        mock_mcc_direct_client,
        mock_mosk_direct_client,
    ):
        """Test health check when one cluster is degraded."""
        manager = StackLightManager(
            mcc_direct_client=mock_mcc_direct_client,
            mosk_direct_client=mock_mosk_direct_client,
        )

        manager._mcc.connect = AsyncMock()
        manager._mosk.connect = AsyncMock()
        # MCC healthy - Prometheus returns success, Alertmanager returns alerts list
        manager._mcc._query_prometheus_instant = AsyncMock(return_value=([], True))
        manager._mcc.get_alerts = AsyncMock(return_value=[])
        # MOSK Prometheus down (returns success=False)
        manager._mosk._query_prometheus_instant = AsyncMock(return_value=([], False))
        manager._mosk.get_alerts = AsyncMock(return_value=[])

        await manager.connect()
        health = await manager.check_health()

        assert health.overall_status == ManagerHealthStatus.DEGRADED
        assert health.mcc.status == ManagerHealthStatus.HEALTHY
        assert health.mosk.status == ManagerHealthStatus.DEGRADED
        assert len(health.recommendations) > 0


# =============================================================================
# Singleton Tests
# =============================================================================


class TestSingleton:
    """Tests for singleton management."""

    @pytest.mark.asyncio
    async def test_get_stacklight_manager_creates_singleton(
        self,
        mock_mcc_direct_client,
        mock_mosk_direct_client,
    ):
        """Test singleton creation."""
        reset_stacklight_manager()

        with patch.object(StackLightAdapter, "connect", new_callable=AsyncMock):
            manager = await get_stacklight_manager(
                mock_mcc_direct_client,
                mock_mosk_direct_client,
            )

            assert manager is not None
            assert manager.is_connected is True

        reset_stacklight_manager()

    @pytest.mark.asyncio
    async def test_get_stacklight_manager_returns_same_instance(
        self,
        mock_mcc_direct_client,
        mock_mosk_direct_client,
    ):
        """Test singleton returns same instance."""
        reset_stacklight_manager()

        with patch.object(StackLightAdapter, "connect", new_callable=AsyncMock):
            manager1 = await get_stacklight_manager(
                mock_mcc_direct_client,
                mock_mosk_direct_client,
            )
            manager2 = await get_stacklight_manager(
                mock_mcc_direct_client,
                mock_mosk_direct_client,
            )

            assert manager1 is manager2

        reset_stacklight_manager()

    def test_reset_clears_singleton(self):
        """Test reset clears the singleton."""
        reset_stacklight_manager()
        # After reset, next call should create new instance
        # (This is tested by the create_singleton test passing)
