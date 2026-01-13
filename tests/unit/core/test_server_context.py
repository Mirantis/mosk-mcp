"""Tests for server context implementation.

Tests the ServerContext, ConnectionManager, and related classes.
"""

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mosk_mcp.core.exceptions import MoskConnectionError, MoskMCPError
from mosk_mcp.core.server_context import (
    ClusterType,
    ConnectionManager,
    ConnectionMetrics,
    ConnectionState,
    HealthStatus,
    ServerContextConfig,
    SSOServerContext,
)


# =============================================================================
# Enum Tests
# =============================================================================


class TestConnectionState:
    """Tests for ConnectionState enum."""

    def test_all_states_defined(self) -> None:
        """Test all connection states are defined."""
        assert ConnectionState.DISCONNECTED == "disconnected"
        assert ConnectionState.CONNECTING == "connecting"
        assert ConnectionState.CONNECTED == "connected"
        assert ConnectionState.RECONNECTING == "reconnecting"
        assert ConnectionState.FAILED == "failed"
        assert ConnectionState.CLOSED == "closed"

    def test_state_count(self) -> None:
        """Test expected number of states."""
        assert len(ConnectionState) == 6


class TestClusterType:
    """Tests for ClusterType enum."""

    def test_cluster_types_defined(self) -> None:
        """Test cluster types are defined."""
        assert ClusterType.MCC == "mcc"
        assert ClusterType.MOSK == "mosk"

    def test_cluster_type_count(self) -> None:
        """Test expected number of cluster types."""
        assert len(ClusterType) == 2


class TestHealthStatus:
    """Tests for HealthStatus enum."""

    def test_health_statuses_defined(self) -> None:
        """Test health statuses are defined."""
        assert HealthStatus.HEALTHY == "healthy"
        assert HealthStatus.DEGRADED == "degraded"
        assert HealthStatus.UNHEALTHY == "unhealthy"
        assert HealthStatus.UNKNOWN == "unknown"

    def test_health_status_count(self) -> None:
        """Test expected number of health statuses."""
        assert len(HealthStatus) == 4


# =============================================================================
# ConnectionMetrics Tests
# =============================================================================


class TestConnectionMetrics:
    """Tests for ConnectionMetrics dataclass."""

    def test_default_values(self) -> None:
        """Test default metric values."""
        metrics = ConnectionMetrics()
        assert metrics.connect_count == 0
        assert metrics.disconnect_count == 0
        assert metrics.reconnect_count == 0
        assert metrics.last_connected_at is None
        assert metrics.last_disconnected_at is None
        assert metrics.last_error is None
        assert metrics.total_requests == 0
        assert metrics.failed_requests == 0

    def test_to_dict(self) -> None:
        """Test converting metrics to dictionary."""
        metrics = ConnectionMetrics(
            connect_count=5,
            disconnect_count=2,
            reconnect_count=1,
            total_requests=100,
            failed_requests=10,
        )
        result = metrics.to_dict()

        assert result["connect_count"] == 5
        assert result["disconnect_count"] == 2
        assert result["reconnect_count"] == 1
        assert result["total_requests"] == 100
        assert result["failed_requests"] == 10
        assert result["last_connected_at"] is None
        assert result["last_disconnected_at"] is None
        assert result["last_error"] is None
        # Success rate: (100 - 10) / 100 = 0.9
        assert result["success_rate"] == 0.9

    def test_to_dict_with_timestamps(self) -> None:
        """Test converting metrics with timestamps."""
        now = datetime.now(UTC)
        metrics = ConnectionMetrics(
            last_connected_at=now,
            last_disconnected_at=now,
        )
        result = metrics.to_dict()

        assert result["last_connected_at"] == now.isoformat()
        assert result["last_disconnected_at"] == now.isoformat()

    def test_success_rate_zero_requests(self) -> None:
        """Test success rate when no requests made."""
        metrics = ConnectionMetrics()
        result = metrics.to_dict()
        # None indicates "no data" rather than misleading "100% success"
        assert result["success_rate"] is None


# =============================================================================
# ServerContextConfig Tests
# =============================================================================


class TestServerContextConfig:
    """Tests for ServerContextConfig dataclass."""

    def test_default_config(self) -> None:
        """Test default configuration values."""
        config = ServerContextConfig()
        assert config.cache_ttl_seconds == 30.0
        assert config.cache_max_entries == 1000
        assert config.circuit_breaker_failure_threshold == 5
        assert config.circuit_breaker_recovery_timeout == 30.0
        assert config.health_check_interval == 60.0
        assert config.enable_health_monitoring is True
        assert config.enable_cache_cleanup is True
        assert config.max_reconnect_attempts == 5

    def test_custom_config(self) -> None:
        """Test custom configuration values."""
        config = ServerContextConfig(
            cache_ttl_seconds=60.0,
            cache_max_entries=500,
            circuit_breaker_failure_threshold=3,
            health_check_interval=120.0,
            enable_health_monitoring=False,
        )
        assert config.cache_ttl_seconds == 60.0
        assert config.cache_max_entries == 500
        assert config.circuit_breaker_failure_threshold == 3
        assert config.health_check_interval == 120.0
        assert config.enable_health_monitoring is False


# =============================================================================
# ConnectionManager Tests
# =============================================================================


class TestConnectionManagerInitialization:
    """Tests for ConnectionManager initialization."""

    def test_initialization(self) -> None:
        """Test connection manager initialization."""
        mock_factory = MagicMock(return_value=AsyncMock())
        manager = ConnectionManager(
            cluster_type=ClusterType.MCC,
            adapter_factory=mock_factory,
        )

        assert manager.cluster_type == ClusterType.MCC
        assert manager.state == ConnectionState.DISCONNECTED
        assert manager.is_connected is False
        assert manager.health_status == HealthStatus.UNKNOWN

    def test_custom_config(self) -> None:
        """Test connection manager with custom config."""
        from mosk_mcp.infrastructure.circuit_breaker import CircuitBreakerConfig

        config = CircuitBreakerConfig(failure_threshold=3)
        mock_factory = MagicMock(return_value=AsyncMock())

        manager = ConnectionManager(
            cluster_type=ClusterType.MOSK,
            adapter_factory=mock_factory,
            circuit_breaker_config=config,
            max_reconnect_attempts=10,
            reconnect_base_delay=2.0,
            reconnect_max_delay=120.0,
        )

        assert manager.circuit_breaker.config.failure_threshold == 3
        assert manager.max_reconnect_attempts == 10
        assert manager.reconnect_base_delay == 2.0
        assert manager.reconnect_max_delay == 120.0


class TestConnectionManagerConnect:
    """Tests for ConnectionManager connect operations."""

    @pytest.mark.asyncio
    async def test_get_adapter_connects(self) -> None:
        """Test get_adapter establishes connection."""
        mock_adapter = AsyncMock()
        mock_adapter.connect = AsyncMock()
        mock_factory = MagicMock(return_value=mock_adapter)

        manager = ConnectionManager(
            cluster_type=ClusterType.MCC,
            adapter_factory=mock_factory,
        )

        adapter = await manager.get_adapter()

        assert adapter == mock_adapter
        mock_adapter.connect.assert_called_once()
        assert manager.state == ConnectionState.CONNECTED
        assert manager.is_connected is True

    @pytest.mark.asyncio
    async def test_get_adapter_returns_cached(self) -> None:
        """Test get_adapter returns cached adapter when connected."""
        mock_adapter = AsyncMock()
        mock_adapter.connect = AsyncMock()
        mock_factory = MagicMock(return_value=mock_adapter)

        manager = ConnectionManager(
            cluster_type=ClusterType.MCC,
            adapter_factory=mock_factory,
        )

        # First call
        adapter1 = await manager.get_adapter()
        # Second call
        adapter2 = await manager.get_adapter()

        assert adapter1 == adapter2
        # Factory should only be called once
        assert mock_factory.call_count == 1

    @pytest.mark.asyncio
    async def test_connect_failure(self) -> None:
        """Test connection failure handling."""
        mock_adapter = AsyncMock()
        mock_adapter.connect = AsyncMock(side_effect=Exception("Connection failed"))
        mock_factory = MagicMock(return_value=mock_adapter)

        manager = ConnectionManager(
            cluster_type=ClusterType.MCC,
            adapter_factory=mock_factory,
        )

        with pytest.raises(MoskConnectionError) as exc_info:
            await manager.get_adapter()

        assert "Failed to connect to mcc cluster" in str(exc_info.value)
        assert manager.state == ConnectionState.FAILED
        assert manager.health_status == HealthStatus.UNHEALTHY


class TestConnectionManagerReconnect:
    """Tests for ConnectionManager reconnect operations."""

    @pytest.mark.asyncio
    async def test_reconnect_success(self) -> None:
        """Test successful reconnection."""
        mock_adapter = AsyncMock()
        mock_adapter.connect = AsyncMock()
        mock_adapter.disconnect = AsyncMock()
        mock_factory = MagicMock(return_value=mock_adapter)

        manager = ConnectionManager(
            cluster_type=ClusterType.MCC,
            adapter_factory=mock_factory,
            max_reconnect_attempts=3,
            reconnect_base_delay=0.01,  # Fast for testing
        )

        adapter = await manager.reconnect()

        assert adapter == mock_adapter
        assert manager.state == ConnectionState.CONNECTED
        assert manager.metrics.reconnect_count == 1

    @pytest.mark.asyncio
    async def test_reconnect_all_attempts_fail(self) -> None:
        """Test reconnection fails after all attempts."""
        mock_adapter = AsyncMock()
        mock_adapter.connect = AsyncMock(side_effect=Exception("Connection failed"))
        mock_adapter.disconnect = AsyncMock()
        mock_factory = MagicMock(return_value=mock_adapter)

        manager = ConnectionManager(
            cluster_type=ClusterType.MCC,
            adapter_factory=mock_factory,
            max_reconnect_attempts=2,
            reconnect_base_delay=0.01,
        )

        with pytest.raises(MoskConnectionError) as exc_info:
            await manager.reconnect()

        assert "Failed to reconnect to mcc after 2 attempts" in str(exc_info.value)
        assert manager.state == ConnectionState.FAILED


class TestConnectionManagerDisconnect:
    """Tests for ConnectionManager disconnect operations."""

    @pytest.mark.asyncio
    async def test_disconnect(self) -> None:
        """Test disconnection."""
        mock_adapter = AsyncMock()
        mock_adapter.connect = AsyncMock()
        mock_adapter.disconnect = AsyncMock()
        mock_factory = MagicMock(return_value=mock_adapter)

        manager = ConnectionManager(
            cluster_type=ClusterType.MCC,
            adapter_factory=mock_factory,
        )

        # Connect first
        await manager.get_adapter()
        assert manager.is_connected is True

        # Disconnect
        await manager.disconnect()

        mock_adapter.disconnect.assert_called_once()
        assert manager.state == ConnectionState.DISCONNECTED
        assert manager.metrics.disconnect_count == 1

    @pytest.mark.asyncio
    async def test_close(self) -> None:
        """Test closing connection permanently."""
        mock_adapter = AsyncMock()
        mock_adapter.connect = AsyncMock()
        mock_adapter.disconnect = AsyncMock()
        mock_factory = MagicMock(return_value=mock_adapter)

        manager = ConnectionManager(
            cluster_type=ClusterType.MCC,
            adapter_factory=mock_factory,
        )

        await manager.get_adapter()
        await manager.close()

        assert manager.state == ConnectionState.CLOSED
        assert manager._adapter is None


class TestConnectionManagerHealthCheck:
    """Tests for ConnectionManager health check operations."""

    @pytest.mark.asyncio
    async def test_health_check_healthy(self) -> None:
        """Test health check returns healthy."""
        mock_adapter = AsyncMock()
        mock_adapter.connect = AsyncMock()
        mock_adapter.check_api_health = AsyncMock(return_value=True)
        mock_factory = MagicMock(return_value=mock_adapter)

        manager = ConnectionManager(
            cluster_type=ClusterType.MCC,
            adapter_factory=mock_factory,
        )

        await manager.get_adapter()
        status = await manager.health_check()

        assert status == HealthStatus.HEALTHY
        assert manager.health_status == HealthStatus.HEALTHY

    @pytest.mark.asyncio
    async def test_health_check_degraded(self) -> None:
        """Test health check returns degraded."""
        mock_adapter = AsyncMock()
        mock_adapter.connect = AsyncMock()
        mock_adapter.check_api_health = AsyncMock(return_value=False)
        mock_factory = MagicMock(return_value=mock_adapter)

        manager = ConnectionManager(
            cluster_type=ClusterType.MCC,
            adapter_factory=mock_factory,
        )

        await manager.get_adapter()
        status = await manager.health_check()

        assert status == HealthStatus.DEGRADED

    @pytest.mark.asyncio
    async def test_health_check_unhealthy_when_not_connected(self) -> None:
        """Test health check returns unhealthy when not connected."""
        mock_factory = MagicMock(return_value=AsyncMock())

        manager = ConnectionManager(
            cluster_type=ClusterType.MCC,
            adapter_factory=mock_factory,
        )

        status = await manager.health_check()

        assert status == HealthStatus.UNHEALTHY

    @pytest.mark.asyncio
    @pytest.mark.filterwarnings("ignore::pytest.PytestUnraisableExceptionWarning")
    async def test_health_check_timeout(self) -> None:
        """Test health check handles timeout."""
        mock_adapter = AsyncMock()
        mock_adapter.connect = AsyncMock()
        # Use AsyncMock for the health check
        mock_adapter.check_api_health = AsyncMock(return_value=True)
        mock_factory = MagicMock(return_value=mock_adapter)

        manager = ConnectionManager(
            cluster_type=ClusterType.MCC,
            adapter_factory=mock_factory,
        )

        await manager.get_adapter()

        # This should timeout and return degraded
        with patch.object(asyncio, "wait_for", side_effect=TimeoutError):
            status = await manager.health_check()

        assert status == HealthStatus.DEGRADED


@pytest.mark.filterwarnings("ignore::pytest.PytestUnraisableExceptionWarning")
class TestConnectionManagerHealthMonitoring:
    """Tests for ConnectionManager health monitoring."""

    @pytest.mark.asyncio
    async def test_start_stop_health_monitoring(self) -> None:
        """Test starting and stopping health monitoring."""
        mock_factory = MagicMock(return_value=AsyncMock())

        manager = ConnectionManager(
            cluster_type=ClusterType.MCC,
            adapter_factory=mock_factory,
            health_check_interval=0.1,
        )

        await manager.start_health_monitoring()
        assert manager._health_check_task is not None
        assert not manager._health_check_task.done()

        await manager.stop_health_monitoring()
        assert manager._health_check_task is None


class TestConnectionManagerStatus:
    """Tests for ConnectionManager status reporting."""

    def test_get_status(self) -> None:
        """Test getting connection status."""
        mock_factory = MagicMock(return_value=AsyncMock())

        manager = ConnectionManager(
            cluster_type=ClusterType.MCC,
            adapter_factory=mock_factory,
        )

        status = manager.get_status()

        assert status["cluster_type"] == "mcc"
        assert status["state"] == "disconnected"
        assert status["health_status"] == "unknown"
        assert "circuit_breaker" in status
        assert "metrics" in status


# =============================================================================
# SSOServerContext Tests
# =============================================================================


@pytest.mark.filterwarnings("ignore::pytest.PytestUnraisableExceptionWarning")
class TestSSOServerContextInitialization:
    """Tests for SSOServerContext initialization."""

    def test_initialization(self) -> None:
        """Test SSO server context initialization."""
        mock_settings = MagicMock()
        mock_settings.keycloak_url = "https://keycloak.example.com"
        mock_settings.keycloak_realm = "mosk"
        mock_settings.rate_limit_enabled = False

        context = SSOServerContext(mock_settings)

        assert context.settings == mock_settings
        assert context._initialized is False
        assert context._shutdown is False
        assert context._session is None
        assert context.is_authenticated is False

    def test_initialization_with_config(self) -> None:
        """Test initialization with custom config."""
        mock_settings = MagicMock()
        mock_settings.keycloak_url = "https://keycloak.example.com"
        mock_settings.keycloak_realm = "mosk"

        config = ServerContextConfig(cache_ttl_seconds=60.0)
        context = SSOServerContext(mock_settings, config)

        assert context.config.cache_ttl_seconds == 60.0


class TestSSOServerContextSession:
    """Tests for SSOServerContext session management."""

    def test_session_property_raises_when_not_authenticated(self) -> None:
        """Test session property raises when not authenticated."""
        mock_settings = MagicMock()
        mock_settings.keycloak_url = "https://keycloak.example.com"
        mock_settings.keycloak_realm = "mosk"

        context = SSOServerContext(mock_settings)

        with pytest.raises(MoskMCPError) as exc_info:
            _ = context.session

        assert "Not authenticated" in str(exc_info.value)

    def test_is_authenticated_false_when_no_session(self) -> None:
        """Test is_authenticated is False when no session."""
        mock_settings = MagicMock()
        mock_settings.keycloak_url = "https://keycloak.example.com"
        mock_settings.keycloak_realm = "mosk"

        context = SSOServerContext(mock_settings)

        assert context.is_authenticated is False


@pytest.mark.filterwarnings("ignore::pytest.PytestUnraisableExceptionWarning")
class TestSSOServerContextVersionCheck:
    """Tests for MOSK version checking."""

    def test_mosk_version_info_initially_none(self) -> None:
        """Test MOSK version info is None initially."""
        mock_settings = MagicMock()
        mock_settings.keycloak_url = "https://keycloak.example.com"
        mock_settings.keycloak_realm = "mosk"

        context = SSOServerContext(mock_settings)

        assert context.mosk_version_info is None
        assert context.is_mosk_version_supported is False


@pytest.mark.filterwarnings("ignore::pytest.PytestUnraisableExceptionWarning")
class TestSSOServerContextLifecycle:
    """Tests for SSOServerContext lifecycle management."""

    @pytest.mark.asyncio
    async def test_initialize(self) -> None:
        """Test context initialization."""
        mock_settings = MagicMock()
        mock_settings.keycloak_url = "https://keycloak.example.com"
        mock_settings.keycloak_realm = "mosk"
        mock_settings.rate_limit_enabled = False
        mock_settings.rate_limit_requests_per_minute = 60
        mock_settings.rate_limit_burst_size = 10

        context = SSOServerContext(mock_settings)
        context.config = ServerContextConfig(enable_cache_cleanup=False)

        await context.initialize()

        assert context._initialized is True

    @pytest.mark.asyncio
    async def test_initialize_idempotent(self) -> None:
        """Test initialize is idempotent."""
        mock_settings = MagicMock()
        mock_settings.keycloak_url = "https://keycloak.example.com"
        mock_settings.keycloak_realm = "mosk"
        mock_settings.rate_limit_enabled = False
        mock_settings.rate_limit_requests_per_minute = 60
        mock_settings.rate_limit_burst_size = 10

        context = SSOServerContext(mock_settings)
        context.config = ServerContextConfig(enable_cache_cleanup=False)

        await context.initialize()
        await context.initialize()  # Second call should be no-op

        assert context._initialized is True

    @pytest.mark.asyncio
    async def test_shutdown(self) -> None:
        """Test context shutdown."""
        mock_settings = MagicMock()
        mock_settings.keycloak_url = "https://keycloak.example.com"
        mock_settings.keycloak_realm = "mosk"
        mock_settings.rate_limit_enabled = False
        mock_settings.rate_limit_requests_per_minute = 60
        mock_settings.rate_limit_burst_size = 10
        mock_settings.shutdown_timeout = 30.0

        context = SSOServerContext(mock_settings)
        context.config = ServerContextConfig(enable_cache_cleanup=False)

        await context.initialize()
        await context.shutdown()

        assert context._shutdown is True

    @pytest.mark.asyncio
    async def test_shutdown_idempotent(self) -> None:
        """Test shutdown is idempotent."""
        mock_settings = MagicMock()
        mock_settings.keycloak_url = "https://keycloak.example.com"
        mock_settings.keycloak_realm = "mosk"
        mock_settings.rate_limit_enabled = False
        mock_settings.rate_limit_requests_per_minute = 60
        mock_settings.rate_limit_burst_size = 10
        mock_settings.shutdown_timeout = 30.0

        context = SSOServerContext(mock_settings)
        context.config = ServerContextConfig(enable_cache_cleanup=False)

        await context.initialize()
        await context.shutdown()
        await context.shutdown()  # Second call should be no-op

        assert context._shutdown is True

    @pytest.mark.asyncio
    async def test_context_manager(self) -> None:
        """Test async context manager."""
        mock_settings = MagicMock()
        mock_settings.keycloak_url = "https://keycloak.example.com"
        mock_settings.keycloak_realm = "mosk"
        mock_settings.rate_limit_enabled = False
        mock_settings.rate_limit_requests_per_minute = 60
        mock_settings.rate_limit_burst_size = 10
        mock_settings.shutdown_timeout = 30.0

        async with SSOServerContext(mock_settings) as context:
            context.config = ServerContextConfig(enable_cache_cleanup=False)
            assert context._initialized is True

        assert context._shutdown is True


class TestSSOServerContextAdapters:
    """Tests for SSOServerContext adapter access."""

    @pytest.mark.asyncio
    async def test_get_mcc_adapter_raises_when_not_authenticated(self) -> None:
        """Test get_mcc_adapter raises when not authenticated."""
        mock_settings = MagicMock()
        mock_settings.keycloak_url = "https://keycloak.example.com"
        mock_settings.keycloak_realm = "mosk"
        mock_settings.rate_limit_enabled = False
        mock_settings.rate_limit_requests_per_minute = 60
        mock_settings.rate_limit_burst_size = 10

        context = SSOServerContext(mock_settings)
        context.config = ServerContextConfig(enable_cache_cleanup=False)
        await context.initialize()

        with pytest.raises(MoskMCPError) as exc_info:
            await context.get_mcc_adapter()

        assert "Not authenticated" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_get_mosk_adapter_raises_when_not_authenticated(self) -> None:
        """Test get_mosk_adapter raises when not authenticated."""
        mock_settings = MagicMock()
        mock_settings.keycloak_url = "https://keycloak.example.com"
        mock_settings.keycloak_realm = "mosk"
        mock_settings.rate_limit_enabled = False
        mock_settings.rate_limit_requests_per_minute = 60
        mock_settings.rate_limit_burst_size = 10

        context = SSOServerContext(mock_settings)
        context.config = ServerContextConfig(enable_cache_cleanup=False)
        await context.initialize()

        with pytest.raises(MoskMCPError) as exc_info:
            await context.get_mosk_adapter()

        assert "Not authenticated" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_get_stacklight_client_raises_when_not_authenticated(self) -> None:
        """Test get_stacklight_client raises when not authenticated."""
        mock_settings = MagicMock()
        mock_settings.keycloak_url = "https://keycloak.example.com"
        mock_settings.keycloak_realm = "mosk"
        mock_settings.rate_limit_enabled = False
        mock_settings.rate_limit_requests_per_minute = 60
        mock_settings.rate_limit_burst_size = 10

        context = SSOServerContext(mock_settings)
        context.config = ServerContextConfig(enable_cache_cleanup=False)
        await context.initialize()

        with pytest.raises(MoskMCPError) as exc_info:
            await context.get_stacklight_client()

        assert "Not authenticated" in str(exc_info.value)


class TestSSOServerContextCheckShutdown:
    """Tests for shutdown checking."""

    @pytest.mark.asyncio
    async def test_check_shutdown_raises_when_shutting_down(self) -> None:
        """Test _check_shutdown raises when shutting down."""
        mock_settings = MagicMock()
        mock_settings.keycloak_url = "https://keycloak.example.com"
        mock_settings.keycloak_realm = "mosk"
        mock_settings.rate_limit_enabled = False
        mock_settings.rate_limit_requests_per_minute = 60
        mock_settings.rate_limit_burst_size = 10
        mock_settings.shutdown_timeout = 30.0

        context = SSOServerContext(mock_settings)
        context.config = ServerContextConfig(enable_cache_cleanup=False)
        await context.initialize()
        await context.shutdown()

        with pytest.raises(MoskMCPError) as exc_info:
            context._check_shutdown()

        assert "shutting down" in str(exc_info.value)


class TestSSOServerContextProperties:
    """Tests for SSOServerContext properties."""

    def test_cache_property(self) -> None:
        """Test cache property returns cache."""
        mock_settings = MagicMock()
        mock_settings.keycloak_url = "https://keycloak.example.com"
        mock_settings.keycloak_realm = "mosk"

        context = SSOServerContext(mock_settings)

        assert context.cache is not None
        assert context.cache == context._cache

    def test_rate_limiter_property_initially_none(self) -> None:
        """Test rate_limiter property is None before initialization."""
        mock_settings = MagicMock()
        mock_settings.keycloak_url = "https://keycloak.example.com"
        mock_settings.keycloak_realm = "mosk"

        context = SSOServerContext(mock_settings)

        assert context.rate_limiter is None


class TestSSOServerContextStatus:
    """Tests for SSOServerContext status reporting."""

    @pytest.mark.asyncio
    async def test_get_status(self) -> None:
        """Test getting context status."""
        mock_settings = MagicMock()
        mock_settings.keycloak_url = "https://keycloak.example.com"
        mock_settings.keycloak_realm = "mosk"
        mock_settings.rate_limit_enabled = False
        mock_settings.rate_limit_requests_per_minute = 60
        mock_settings.rate_limit_burst_size = 10
        mock_settings.auth_enabled = True
        mock_settings.audit_enabled = False

        context = SSOServerContext(mock_settings)
        context.config = ServerContextConfig(enable_cache_cleanup=False)
        await context.initialize()

        status = context.get_status()

        assert status["mode"] == "sso"
        assert status["initialized"] is True
        assert status["shutdown"] is False
        assert "start_time" in status
        assert "uptime_seconds" in status
        assert "session" in status
        assert "mosk_version" in status
        assert "cache" in status
        assert "rate_limiting" in status
        assert "settings" in status


class TestSSOServerContextLogout:
    """Tests for SSOServerContext logout."""

    @pytest.mark.asyncio
    async def test_logout_when_not_authenticated(self) -> None:
        """Test logout when not authenticated is no-op."""
        mock_settings = MagicMock()
        mock_settings.keycloak_url = "https://keycloak.example.com"
        mock_settings.keycloak_realm = "mosk"

        context = SSOServerContext(mock_settings)

        # Should not raise
        await context.logout()

        assert context._session is None
        assert context._mosk_version_info is None


class TestSSOServerContextRefreshTokens:
    """Tests for token refresh."""

    @pytest.mark.asyncio
    async def test_refresh_tokens_no_session(self) -> None:
        """Test refresh_tokens returns False when no session."""
        mock_settings = MagicMock()
        mock_settings.keycloak_url = "https://keycloak.example.com"
        mock_settings.keycloak_realm = "mosk"

        context = SSOServerContext(mock_settings)

        result = await context.refresh_tokens()

        assert result is False


class TestSSOServerContextLazyInitialization:
    """Tests for lazy initialization of services."""

    def test_audit_logger_lazy_init(self) -> None:
        """Test audit logger is lazily initialized."""
        mock_settings = MagicMock()
        mock_settings.keycloak_url = "https://keycloak.example.com"
        mock_settings.keycloak_realm = "mosk"
        mock_settings.audit_enabled = False
        mock_settings.audit_format = "json"
        mock_settings.log_level = "INFO"

        context = SSOServerContext(mock_settings)

        # Initially None
        assert context._audit_logger is None

        # Access triggers creation
        with patch("mosk_mcp.observability.audit.AuditLogger") as mock_audit:
            mock_audit.from_settings.return_value = MagicMock()
            logger = context.audit_logger
            assert logger is not None

    def test_rbac_enforcer_lazy_init(self) -> None:
        """Test RBAC enforcer is lazily initialized."""
        mock_settings = MagicMock()
        mock_settings.keycloak_url = "https://keycloak.example.com"
        mock_settings.keycloak_realm = "mosk"

        context = SSOServerContext(mock_settings)

        # Initially None
        assert context._rbac_enforcer is None

        # Access triggers creation
        with patch("mosk_mcp.auth.rbac.RBACEnforcer") as mock_rbac:
            mock_rbac.return_value = MagicMock()
            enforcer = context.rbac_enforcer
            assert enforcer is not None
