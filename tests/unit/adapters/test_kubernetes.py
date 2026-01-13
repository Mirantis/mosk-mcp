"""Tests for Kubernetes adapter implementation.

Tests the KubernetesAdapter class, ConnectionPool, retry logic, and utilities.
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mosk_mcp.adapters.kubernetes import (
    ConnectionPool,
    ConnectionPoolExhausted,
    KubernetesAdapter,
    RetryConfig,
    _is_not_found_error,
    _validate_custom_resource_params,
    with_retry,
)
from mosk_mcp.core.exceptions import (
    MoskConnectionError,
)


# =============================================================================
# RetryConfig Tests
# =============================================================================


class TestRetryConfig:
    """Tests for RetryConfig dataclass."""

    def test_default_values(self) -> None:
        """Test default configuration values."""
        config = RetryConfig()
        assert config.max_retries == 3
        assert config.base_delay == 0.5
        assert config.max_delay == 30.0
        assert config.exponential_base == 2.0
        assert config.jitter is True

    def test_custom_values(self) -> None:
        """Test custom configuration values."""
        config = RetryConfig(
            max_retries=5,
            base_delay=1.0,
            max_delay=60.0,
            exponential_base=3.0,
            jitter=False,
        )
        assert config.max_retries == 5
        assert config.base_delay == 1.0
        assert config.max_delay == 60.0
        assert config.exponential_base == 3.0
        assert config.jitter is False

    def test_calculate_delay_without_jitter(self) -> None:
        """Test delay calculation without jitter."""
        config = RetryConfig(base_delay=1.0, exponential_base=2.0, jitter=False)

        # attempt 0: 1.0 * 2^0 = 1.0
        assert config.calculate_delay(0) == 1.0
        # attempt 1: 1.0 * 2^1 = 2.0
        assert config.calculate_delay(1) == 2.0
        # attempt 2: 1.0 * 2^2 = 4.0
        assert config.calculate_delay(2) == 4.0

    def test_calculate_delay_capped_at_max(self) -> None:
        """Test delay is capped at max_delay."""
        config = RetryConfig(
            base_delay=10.0,
            max_delay=15.0,
            exponential_base=2.0,
            jitter=False,
        )

        # attempt 2: 10 * 2^2 = 40, but capped at 15
        assert config.calculate_delay(2) == 15.0

    def test_calculate_delay_with_jitter(self) -> None:
        """Test delay with jitter is randomized."""
        config = RetryConfig(base_delay=10.0, jitter=True)

        # With jitter, result should be in range [5, 15]
        delay = config.calculate_delay(0)
        assert 5.0 <= delay <= 15.0


# =============================================================================
# Error Detection Utilities Tests
# =============================================================================


class TestIsNotFoundError:
    """Tests for _is_not_found_error function."""

    def test_detects_not_found_in_message(self) -> None:
        """Test detecting 'not found' in error message."""
        exc = Exception("Resource not found")
        assert _is_not_found_error(exc) is True

    def test_detects_404_in_message(self) -> None:
        """Test detecting 404 in error message."""
        exc = Exception("Error: 404 Not Found")
        assert _is_not_found_error(exc) is True

    def test_detects_does_not_exist(self) -> None:
        """Test detecting 'does not exist' in error message."""
        exc = Exception("Resource does not exist")
        assert _is_not_found_error(exc) is True

    def test_not_found_error_type(self) -> None:
        """Test detecting NotFoundError type."""

        class NotFoundError(Exception):
            pass

        exc = NotFoundError("Some resource missing")
        assert _is_not_found_error(exc) is True

    def test_regular_error_not_detected(self) -> None:
        """Test regular errors are not detected as not-found."""
        exc = Exception("Connection failed")
        assert _is_not_found_error(exc) is False


class TestValidateCustomResourceParams:
    """Tests for _validate_custom_resource_params function."""

    def test_valid_params(self) -> None:
        """Test valid parameter validation passes."""
        # Should not raise
        _validate_custom_resource_params(
            group="kaas.mirantis.com",
            version="v1alpha1",
            plural="machines",
        )

    def test_empty_version_raises(self) -> None:
        """Test empty version raises ValueError."""
        with pytest.raises(ValueError) as exc_info:
            _validate_custom_resource_params(
                group="kaas.mirantis.com",
                version="",
                plural="machines",
            )
        assert "version cannot be empty" in str(exc_info.value)

    def test_invalid_version_format_raises(self) -> None:
        """Test invalid version format raises ValueError."""
        with pytest.raises(ValueError) as exc_info:
            _validate_custom_resource_params(
                group="kaas.mirantis.com",
                version="invalid",
                plural="machines",
            )
        assert "must start with v1, v2, or v3" in str(exc_info.value)

    def test_empty_plural_raises(self) -> None:
        """Test empty plural raises ValueError."""
        with pytest.raises(ValueError) as exc_info:
            _validate_custom_resource_params(
                group="kaas.mirantis.com",
                version="v1",
                plural="",
            )
        assert "plural name cannot be empty" in str(exc_info.value)

    def test_uppercase_plural_raises(self) -> None:
        """Test uppercase plural raises ValueError."""
        with pytest.raises(ValueError) as exc_info:
            _validate_custom_resource_params(
                group="kaas.mirantis.com",
                version="v1",
                plural="Machines",
            )
        assert "must be lowercase" in str(exc_info.value)


# =============================================================================
# with_retry Tests
# =============================================================================


class TestWithRetry:
    """Tests for with_retry function."""

    @pytest.mark.asyncio
    async def test_success_no_retry(self) -> None:
        """Test successful call doesn't retry."""
        call_count = 0

        async def success_func() -> str:
            nonlocal call_count
            call_count += 1
            return "success"

        result = await with_retry(success_func, operation_name="test")

        assert result == "success"
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_retry_on_transient_error(self) -> None:
        """Test retries on transient error then succeeds."""
        call_count = 0

        async def flaky_func() -> str:
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("Transient failure")
            return "success"

        config = RetryConfig(max_retries=3, base_delay=0.01, jitter=False)
        result = await with_retry(flaky_func, config=config, operation_name="test")

        assert result == "success"
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_no_retry_on_non_retryable_error(self) -> None:
        """Test no retry on non-retryable errors."""
        call_count = 0

        async def fail_func() -> str:
            nonlocal call_count
            call_count += 1
            raise ValueError("Non-retryable error")

        config = RetryConfig(max_retries=3, base_delay=0.01)

        with pytest.raises(ValueError):
            await with_retry(fail_func, config=config, operation_name="test")

        assert call_count == 1  # No retries

    @pytest.mark.asyncio
    async def test_exhausted_retries(self) -> None:
        """Test error raised after all retries exhausted."""

        async def always_fail() -> str:
            raise ConnectionError("Always fails")

        config = RetryConfig(max_retries=2, base_delay=0.01, jitter=False)

        with pytest.raises(ConnectionError):
            await with_retry(always_fail, config=config, operation_name="test")


# =============================================================================
# ConnectionPoolExhausted Tests
# =============================================================================


class TestConnectionPoolExhausted:
    """Tests for ConnectionPoolExhausted exception."""

    def test_exception_attributes(self) -> None:
        """Test exception has required attributes."""
        exc = ConnectionPoolExhausted(
            "Pool exhausted",
            timeout=30.0,
            pool_size=10,
            in_use_count=10,
        )

        assert str(exc) == "Pool exhausted"
        assert exc.timeout == 30.0
        assert exc.pool_size == 10
        assert exc.in_use_count == 10


# =============================================================================
# ConnectionPool Tests
# =============================================================================


class TestConnectionPoolInitialization:
    """Tests for ConnectionPool initialization."""

    def test_default_initialization(self) -> None:
        """Test default initialization."""
        pool = ConnectionPool()

        assert pool._max_size == 10
        assert pool._acquire_timeout == 30.0
        assert pool._closed is False
        assert pool.size == 0
        assert pool.available == 0
        assert pool.in_use_count == 0

    def test_custom_initialization(self) -> None:
        """Test custom initialization."""
        pool = ConnectionPool(
            max_size=5,
            kubeconfig_path=Path("/tmp/kubeconfig"),
            acquire_timeout=60.0,
            health_check_interval=120.0,
        )

        assert pool._max_size == 5
        assert pool._kubeconfig_path == Path("/tmp/kubeconfig")
        assert pool._acquire_timeout == 60.0
        assert pool._health_check_interval == 120.0


class TestConnectionPoolAcquireRelease:
    """Tests for ConnectionPool acquire and release."""

    @pytest.mark.asyncio
    async def test_acquire_creates_connection(self) -> None:
        """Test acquire creates new connection when pool is empty."""
        pool = ConnectionPool(max_size=5)

        with patch.object(pool, "_create_connection") as mock_create:
            mock_conn = AsyncMock()
            mock_create.return_value = mock_conn

            conn = await pool.acquire()

            assert conn == mock_conn
            assert pool.in_use_count == 1
            mock_create.assert_called_once()

    @pytest.mark.asyncio
    async def test_release_returns_to_pool(self) -> None:
        """Test release returns connection to pool."""
        pool = ConnectionPool(max_size=5)

        with patch.object(pool, "_create_connection") as mock_create:
            mock_conn = AsyncMock()
            mock_create.return_value = mock_conn

            conn = await pool.acquire()
            assert pool.in_use_count == 1
            assert pool.available == 0

            await pool.release(conn)
            assert pool.in_use_count == 0
            assert pool.available == 1

    @pytest.mark.asyncio
    async def test_acquire_reuses_connection(self) -> None:
        """Test acquire reuses released connections."""
        pool = ConnectionPool(max_size=5)

        with patch.object(pool, "_create_connection") as mock_create:
            mock_conn = AsyncMock()
            mock_create.return_value = mock_conn

            with patch.object(pool, "_check_connection_health", return_value=True):
                conn1 = await pool.acquire()
                await pool.release(conn1)

                conn2 = await pool.acquire()

                assert conn1 is conn2
                assert mock_create.call_count == 1

    @pytest.mark.asyncio
    async def test_acquire_raises_when_closed(self) -> None:
        """Test acquire raises when pool is closed."""
        pool = ConnectionPool()
        pool._closed = True

        with pytest.raises(RuntimeError) as exc_info:
            await pool.acquire()

        assert "closed" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_acquire_timeout(self) -> None:
        """Test acquire raises on timeout."""
        pool = ConnectionPool(max_size=1, acquire_timeout=0.1)

        with patch.object(pool, "_create_connection") as mock_create:
            mock_conn = AsyncMock()
            mock_create.return_value = mock_conn

            # Acquire the only connection
            conn = await pool.acquire()
            assert pool.in_use_count == 1

            # Try to acquire another - should timeout
            with pytest.raises(ConnectionPoolExhausted) as exc_info:
                await pool.acquire(timeout=0.1)

            assert exc_info.value.timeout == 0.1

            await pool.release(conn)


class TestConnectionPoolClose:
    """Tests for ConnectionPool close."""

    @pytest.mark.asyncio
    async def test_close_clears_pool(self) -> None:
        """Test close clears all connections."""
        pool = ConnectionPool(max_size=5)

        with patch.object(pool, "_create_connection") as mock_create:
            mock_conn = AsyncMock()
            mock_conn.close = AsyncMock()
            mock_create.return_value = mock_conn

            # Create some connections
            conn1 = await pool.acquire()
            await pool.release(conn1)

            await pool.close()

            assert pool._closed is True
            assert pool.available == 0
            assert pool.in_use_count == 0

    @pytest.mark.asyncio
    async def test_connection_context_manager(self) -> None:
        """Test connection context manager."""
        pool = ConnectionPool(max_size=5)

        with patch.object(pool, "_create_connection") as mock_create:
            mock_conn = AsyncMock()
            mock_create.return_value = mock_conn

            async with pool.connection() as conn:
                assert conn == mock_conn
                assert pool.in_use_count == 1

            assert pool.in_use_count == 0
            assert pool.available == 1


# =============================================================================
# KubernetesAdapter Initialization Tests
# =============================================================================


class TestKubernetesAdapterInitialization:
    """Tests for KubernetesAdapter initialization."""

    def test_default_initialization(self) -> None:
        """Test default initialization."""
        adapter = KubernetesAdapter()

        assert adapter._kubeconfig_path is None
        assert adapter._namespace == "default"
        assert adapter._connected is False
        assert adapter._enable_retry is True

    def test_custom_initialization(self) -> None:
        """Test custom initialization."""
        adapter = KubernetesAdapter(
            kubeconfig_path="/tmp/kubeconfig",
            namespace="custom-ns",
            enable_retry=False,
        )

        assert adapter._kubeconfig_path == Path("/tmp/kubeconfig")
        assert adapter._namespace == "custom-ns"
        assert adapter._enable_retry is False

    def test_from_settings(self) -> None:
        """Test creating adapter from settings."""
        mock_settings = MagicMock()
        mock_settings.kubernetes_namespace = "test-ns"
        mock_settings.max_retries = 5

        adapter = KubernetesAdapter.from_settings(mock_settings)

        assert adapter._namespace == "test-ns"
        assert adapter._retry_config.max_retries == 5

    def test_crd_mappings_defined(self) -> None:
        """Test CRD mappings are defined."""
        assert "machines" in KubernetesAdapter.CRD_MAPPINGS
        assert "openstackdeployments" in KubernetesAdapter.CRD_MAPPINGS
        assert "clusters" in KubernetesAdapter.CRD_MAPPINGS


# =============================================================================
# KubernetesAdapter Connection Tests
# =============================================================================


class TestKubernetesAdapterConnection:
    """Tests for KubernetesAdapter connection operations."""

    @pytest.mark.asyncio
    async def test_connect_with_kubeconfig(self) -> None:
        """Test connecting with kubeconfig file."""
        adapter = KubernetesAdapter(kubeconfig_path="/tmp/kubeconfig")

        with (
            patch("mosk_mcp.adapters.kubernetes.kr8s.asyncio.api") as mock_api,
            patch.object(Path, "exists", return_value=True),
        ):
            mock_client = AsyncMock()
            mock_api.return_value = mock_client

            await adapter.connect()

            assert adapter._connected is True
            mock_api.assert_called_once()
            mock_client.version.assert_called_once()

    @pytest.mark.asyncio
    async def test_connect_in_cluster(self) -> None:
        """Test connecting with in-cluster config."""
        adapter = KubernetesAdapter()

        with patch("mosk_mcp.adapters.kubernetes.kr8s.asyncio.api") as mock_api:
            mock_client = AsyncMock()
            mock_api.return_value = mock_client

            await adapter.connect()

            assert adapter._connected is True
            mock_api.assert_called_once()

    @pytest.mark.asyncio
    async def test_connect_kubeconfig_not_found(self) -> None:
        """Test connection fails when kubeconfig not found."""
        adapter = KubernetesAdapter(kubeconfig_path="/nonexistent/kubeconfig")

        with pytest.raises(MoskConnectionError) as exc_info:
            await adapter.connect()

        assert "not found" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_connect_idempotent(self) -> None:
        """Test connect is idempotent."""
        adapter = KubernetesAdapter()

        with patch("mosk_mcp.adapters.kubernetes.kr8s.asyncio.api") as mock_api:
            mock_client = AsyncMock()
            mock_api.return_value = mock_client

            await adapter.connect()
            await adapter.connect()

            # Should only connect once
            assert mock_api.call_count == 1

    @pytest.mark.asyncio
    async def test_disconnect(self) -> None:
        """Test disconnection."""
        adapter = KubernetesAdapter()

        with patch("mosk_mcp.adapters.kubernetes.kr8s.asyncio.api") as mock_api:
            mock_client = AsyncMock()
            mock_client.async_close = AsyncMock()
            mock_api.return_value = mock_client

            await adapter.connect()
            await adapter.disconnect()

            assert adapter._connected is False
            assert adapter._api is None

    @pytest.mark.asyncio
    async def test_context_manager(self) -> None:
        """Test async context manager."""
        with patch("mosk_mcp.adapters.kubernetes.kr8s.asyncio.api") as mock_api:
            mock_client = AsyncMock()
            mock_client.async_close = AsyncMock()
            mock_api.return_value = mock_client

            async with KubernetesAdapter() as adapter:
                assert adapter._connected is True

            assert adapter._connected is False


class TestKubernetesAdapterEnsureConnected:
    """Tests for _ensure_connected method."""

    def test_ensure_connected_raises_when_not_connected(self) -> None:
        """Test _ensure_connected raises when not connected."""
        adapter = KubernetesAdapter()

        with pytest.raises(MoskConnectionError) as exc_info:
            adapter._ensure_connected()

        assert "not connected" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_ensure_connected_passes_when_connected(self) -> None:
        """Test _ensure_connected passes when connected."""
        adapter = KubernetesAdapter()

        with patch("mosk_mcp.adapters.kubernetes.kr8s.asyncio.api") as mock_api:
            mock_client = AsyncMock()
            mock_api.return_value = mock_client

            await adapter.connect()

            # Should not raise
            adapter._ensure_connected()


# =============================================================================
# KubernetesAdapter Operations Tests
# =============================================================================


class TestKubernetesAdapterList:
    """Tests for KubernetesAdapter list operations."""

    @pytest.mark.asyncio
    async def test_list_requires_connection(self) -> None:
        """Test list raises error when not connected."""
        adapter = KubernetesAdapter()

        with pytest.raises(MoskConnectionError):
            await adapter.list(kind="Pod", namespace="default")


class TestKubernetesAdapterGet:
    """Tests for KubernetesAdapter get operations."""

    @pytest.mark.asyncio
    async def test_get_requires_connection(self) -> None:
        """Test get raises error when not connected."""
        adapter = KubernetesAdapter()

        with pytest.raises(MoskConnectionError):
            await adapter.get(kind="Pod", name="test-pod", namespace="default")


# =============================================================================
# KubernetesAdapter Health Check Tests
# =============================================================================


class TestKubernetesAdapterHealthCheck:
    """Tests for KubernetesAdapter health check."""

    @pytest.mark.asyncio
    async def test_check_api_health_success(self) -> None:
        """Test health check succeeds."""
        adapter = KubernetesAdapter()

        with patch("mosk_mcp.adapters.kubernetes.kr8s.asyncio.api") as mock_api:
            mock_client = AsyncMock()
            mock_api.return_value = mock_client

            await adapter.connect()
            result = await adapter.check_api_health()

            assert result is True
            mock_client.version.assert_called()

    @pytest.mark.asyncio
    async def test_check_api_health_failure(self) -> None:
        """Test health check raises error on failure."""
        from mosk_mcp.core.exceptions import KubernetesError

        adapter = KubernetesAdapter()

        with patch("mosk_mcp.adapters.kubernetes.kr8s.asyncio.api") as mock_api:
            mock_client = AsyncMock()
            # Let connect succeed first
            mock_client.version = AsyncMock(return_value={"major": "1", "minor": "28"})
            mock_api.return_value = mock_client

            await adapter.connect()

            # Make version fail for health check
            mock_client.version.side_effect = Exception("Connection lost")

            with pytest.raises(KubernetesError) as exc_info:
                await adapter.check_api_health()

            assert "health check failed" in str(exc_info.value)


# =============================================================================
# KubernetesAdapter Plural to Kind Mapping Tests
# =============================================================================


class TestKubernetesAdapterPluralToKind:
    """Tests for plural to kind mapping."""

    def test_plural_to_kind_mapping_exists(self) -> None:
        """Test that plural to kind mapping exists for common resources."""
        assert KubernetesAdapter._PLURAL_TO_KIND_MAP["machines"] == "Machine"
        assert KubernetesAdapter._PLURAL_TO_KIND_MAP["clusters"] == "Cluster"
        assert (
            KubernetesAdapter._PLURAL_TO_KIND_MAP["openstackdeployments"] == "OpenStackDeployment"
        )
        assert (
            KubernetesAdapter._PLURAL_TO_KIND_MAP["baremetalhostinventories"]
            == "BareMetalHostInventory"
        )


# =============================================================================
# Cache Tests
# =============================================================================


class TestKubernetesAdapterCache:
    """Tests for adapter caching behavior."""

    def test_cache_attributes_exist(self) -> None:
        """Test cache class attributes exist."""
        assert hasattr(KubernetesAdapter, "_mosk_cluster_cache")
        assert hasattr(KubernetesAdapter, "_CACHE_TTL_SECONDS")
        assert KubernetesAdapter._CACHE_TTL_SECONDS == 300
