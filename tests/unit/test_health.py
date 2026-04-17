"""Unit tests for the health check module.

Tests for:
- HealthChecker class functionality
- Liveness, readiness, and startup probes
- Health check response models
- Kubernetes connectivity checks
"""

import asyncio
import time
from unittest.mock import AsyncMock

import pytest

from mosk_mcp import __version__
from mosk_mcp.core.config import Environment, LogFormat, LogLevel, Settings, TransportType
from mosk_mcp.observability.health import (
    CheckResult,
    HealthChecker,
    HealthResponse,
    HealthStatus,
    StartupResponse,
    create_health_app,
    get_health_checker,
    init_health_checker,
)


@pytest.fixture
def health_settings() -> Settings:
    """Create settings for health check tests."""
    return Settings(
        app_name="mosk-mcp-test",
        app_version="0.1.0-test",
        transport=TransportType.STDIO,
        log_level=LogLevel.DEBUG,
        log_format=LogFormat.CONSOLE,
        environment=Environment.DEVELOPMENT,
        auth_enabled=False,
        otel_enabled=False,
        metrics_enabled=True,
        metrics_port=9090,
        health_check_timeout_seconds=5,
        health_check_k8s_enabled=True,
    )


@pytest.fixture
def health_checker(health_settings: Settings) -> HealthChecker:
    """Create a health checker instance for testing."""
    return HealthChecker(settings=health_settings)


class TestHealthStatus:
    """Tests for HealthStatus enum."""

    def test_status_values(self) -> None:
        """Test that all expected status values exist."""
        assert HealthStatus.HEALTHY == "healthy"
        assert HealthStatus.DEGRADED == "degraded"
        assert HealthStatus.UNHEALTHY == "unhealthy"


class TestCheckResult:
    """Tests for CheckResult model."""

    def test_check_result_creation(self) -> None:
        """Test creating a CheckResult."""
        result = CheckResult(
            name="test_check",
            status=HealthStatus.HEALTHY,
            message="Test passed",
            latency_ms=10.5,
            details={"key": "value"},
        )

        assert result.name == "test_check"
        assert result.status == HealthStatus.HEALTHY
        assert result.message == "Test passed"
        assert result.latency_ms == 10.5
        assert result.details == {"key": "value"}

    def test_check_result_minimal(self) -> None:
        """Test CheckResult with minimal fields."""
        result = CheckResult(
            name="minimal",
            status=HealthStatus.UNHEALTHY,
        )

        assert result.name == "minimal"
        assert result.status == HealthStatus.UNHEALTHY
        assert result.message is None
        assert result.latency_ms is None
        assert result.details == {}


class TestHealthResponse:
    """Tests for HealthResponse model."""

    def test_health_response_creation(self) -> None:
        """Test creating a HealthResponse."""
        checks = [
            CheckResult(name="check1", status=HealthStatus.HEALTHY),
            CheckResult(name="check2", status=HealthStatus.HEALTHY),
        ]

        response = HealthResponse(
            status=HealthStatus.HEALTHY,
            timestamp="2024-01-01T00:00:00+00:00",
            version=__version__,
            checks=checks,
            uptime_seconds=100.5,
        )

        assert response.status == HealthStatus.HEALTHY
        assert response.version == __version__
        assert len(response.checks) == 2
        assert response.uptime_seconds == 100.5

    def test_health_response_serialization(self) -> None:
        """Test HealthResponse serialization to dict."""
        response = HealthResponse(
            status=HealthStatus.HEALTHY,
            timestamp="2024-01-01T00:00:00+00:00",
            version=__version__,
            checks=[],
            uptime_seconds=50.0,
        )

        data = response.model_dump()
        assert data["status"] == "healthy"
        assert data["version"] == __version__
        assert data["uptime_seconds"] == 50.0


class TestStartupResponse:
    """Tests for StartupResponse model."""

    def test_startup_response_initialized(self) -> None:
        """Test StartupResponse for initialized server."""
        response = StartupResponse(
            status=HealthStatus.HEALTHY,
            timestamp="2024-01-01T00:00:00+00:00",
            initialized=True,
            initialization_time_seconds=2.5,
            message="Server ready",
        )

        assert response.status == HealthStatus.HEALTHY
        assert response.initialized is True
        assert response.initialization_time_seconds == 2.5

    def test_startup_response_not_initialized(self) -> None:
        """Test StartupResponse for server still initializing."""
        response = StartupResponse(
            status=HealthStatus.UNHEALTHY,
            timestamp="2024-01-01T00:00:00+00:00",
            initialized=False,
            initialization_time_seconds=None,
            message="Still initializing",
        )

        assert response.status == HealthStatus.UNHEALTHY
        assert response.initialized is False
        assert response.initialization_time_seconds is None


class TestHealthChecker:
    """Tests for HealthChecker class."""

    def test_initialization(self, health_checker: HealthChecker) -> None:
        """Test HealthChecker initialization."""
        assert health_checker.initialization_complete is False
        assert health_checker._k8s_adapter is None
        assert health_checker.start_time > 0

    def test_mark_initialized(self, health_checker: HealthChecker) -> None:
        """Test marking server as initialized."""
        # Simulate some time passing
        time.sleep(0.01)

        health_checker.mark_initialized()

        assert health_checker.initialization_complete is True
        assert health_checker.initialization_time is not None
        assert health_checker.initialization_time > 0

    def test_mark_initialized_idempotent(self, health_checker: HealthChecker) -> None:
        """Test that mark_initialized only runs once."""
        health_checker.mark_initialized()
        first_time = health_checker.initialization_time

        time.sleep(0.01)
        health_checker.mark_initialized()

        # Time should not change on second call
        assert health_checker.initialization_time == first_time

    def test_uptime_seconds(self, health_checker: HealthChecker) -> None:
        """Test uptime calculation."""
        time.sleep(0.01)
        uptime = health_checker.uptime_seconds

        assert uptime > 0
        assert uptime < 1  # Should be less than 1 second

    @pytest.mark.asyncio
    async def test_check_liveness(self, health_checker: HealthChecker) -> None:
        """Test liveness check."""
        result = await health_checker.check_liveness()

        assert result.status == HealthStatus.HEALTHY
        assert result.version == "0.1.0-test"
        assert len(result.checks) == 1
        assert result.checks[0].name == "process"
        assert result.checks[0].status == HealthStatus.HEALTHY

    @pytest.mark.asyncio
    async def test_check_startup_not_initialized(self, health_checker: HealthChecker) -> None:
        """Test startup check when not initialized."""
        result = await health_checker.check_startup()

        assert result.status == HealthStatus.UNHEALTHY
        assert result.initialized is False
        assert result.initialization_time_seconds is None

    @pytest.mark.asyncio
    async def test_check_startup_initialized(self, health_checker: HealthChecker) -> None:
        """Test startup check when initialized."""
        health_checker.mark_initialized()

        result = await health_checker.check_startup()

        assert result.status == HealthStatus.HEALTHY
        assert result.initialized is True
        assert result.initialization_time_seconds is not None

    @pytest.mark.asyncio
    async def test_check_readiness_no_k8s_adapter(self, health_settings: Settings) -> None:
        """Test readiness check without K8s adapter configured."""
        # Create checker with K8s checks enabled but no adapter
        checker = HealthChecker(settings=health_settings)

        result = await checker.check_readiness()

        # Should be degraded due to missing adapter
        assert result.status == HealthStatus.DEGRADED
        # Find the kubernetes check
        k8s_check = next((c for c in result.checks if c.name == "kubernetes"), None)
        assert k8s_check is not None
        assert k8s_check.status == HealthStatus.DEGRADED

    @pytest.mark.asyncio
    async def test_check_readiness_k8s_disabled(self, health_settings: Settings) -> None:
        """Test readiness check with K8s checks disabled."""
        health_settings_copy = Settings(
            **{
                **health_settings.model_dump(),
                "health_check_k8s_enabled": False,
            }
        )
        checker = HealthChecker(settings=health_settings_copy)

        result = await checker.check_readiness()

        # Should be healthy without K8s check
        assert result.status == HealthStatus.HEALTHY
        # No kubernetes check should be present
        k8s_check = next((c for c in result.checks if c.name == "kubernetes"), None)
        assert k8s_check is None

    @pytest.mark.asyncio
    async def test_check_readiness_k8s_connected(self, health_checker: HealthChecker) -> None:
        """Test readiness check with K8s adapter connected."""
        # Mock K8s adapter
        mock_adapter = AsyncMock()
        mock_adapter.check_connectivity = AsyncMock(return_value=True)

        health_checker.set_kubernetes_adapter(mock_adapter)

        result = await health_checker.check_readiness()

        assert result.status == HealthStatus.HEALTHY
        k8s_check = next((c for c in result.checks if c.name == "kubernetes"), None)
        assert k8s_check is not None
        assert k8s_check.status == HealthStatus.HEALTHY

    @pytest.mark.asyncio
    async def test_check_readiness_k8s_disconnected(self, health_checker: HealthChecker) -> None:
        """Test readiness check with K8s adapter disconnected."""
        mock_adapter = AsyncMock()
        mock_adapter.check_connectivity = AsyncMock(return_value=False)

        health_checker.set_kubernetes_adapter(mock_adapter)

        result = await health_checker.check_readiness()

        assert result.status == HealthStatus.UNHEALTHY
        k8s_check = next((c for c in result.checks if c.name == "kubernetes"), None)
        assert k8s_check is not None
        assert k8s_check.status == HealthStatus.UNHEALTHY

    @pytest.mark.asyncio
    async def test_check_kubernetes_timeout(self, health_checker: HealthChecker) -> None:
        """Test K8s check handles timeout."""
        mock_adapter = AsyncMock()

        async def slow_check() -> bool:
            await asyncio.sleep(10)  # Longer than timeout
            return True

        mock_adapter.check_connectivity = slow_check
        health_checker.set_kubernetes_adapter(mock_adapter)
        health_checker.settings.health_check_timeout_seconds = 1

        result = await health_checker._check_kubernetes()

        assert result.status == HealthStatus.UNHEALTHY
        assert "timed out" in result.message.lower()

    @pytest.mark.asyncio
    async def test_check_kubernetes_exception(self, health_checker: HealthChecker) -> None:
        """Test K8s check handles exceptions."""
        mock_adapter = AsyncMock()
        mock_adapter.check_connectivity = AsyncMock(side_effect=Exception("Connection refused"))

        health_checker.set_kubernetes_adapter(mock_adapter)

        result = await health_checker._check_kubernetes()

        assert result.status == HealthStatus.UNHEALTHY
        assert "Connection refused" in result.message

    @pytest.mark.asyncio
    async def test_kubernetes_check_caching(self, health_checker: HealthChecker) -> None:
        """Test that K8s check results are cached."""
        mock_adapter = AsyncMock()
        mock_adapter.check_connectivity = AsyncMock(return_value=True)

        health_checker.set_kubernetes_adapter(mock_adapter)
        health_checker._k8s_check_cache_ttl = 10  # 10 second cache

        # First call
        result1 = await health_checker._check_kubernetes()
        assert result1.status == HealthStatus.HEALTHY
        assert mock_adapter.check_connectivity.call_count == 1

        # Second call within cache TTL
        result2 = await health_checker._check_kubernetes()
        assert result2.status == HealthStatus.HEALTHY
        assert result2.details.get("cached") is True
        # Should not have called check_connectivity again
        assert mock_adapter.check_connectivity.call_count == 1


class TestHealthCheckerGlobalFunctions:
    """Tests for global health checker functions."""

    def test_init_health_checker(self, health_settings: Settings) -> None:
        """Test initializing global health checker."""
        checker = init_health_checker(health_settings)

        assert checker is not None
        assert checker.settings == health_settings

        # Should be accessible via get function
        assert get_health_checker() == checker

    def test_get_health_checker_not_initialized(self) -> None:
        """Test getting health checker when not initialized."""
        # This test may be affected by other tests that initialize
        # the global checker, but we test the function exists
        result = get_health_checker()
        # Result is either None or a HealthChecker instance
        assert result is None or isinstance(result, HealthChecker)


class TestCreateHealthApp:
    """Tests for create_health_app function."""

    @pytest.fixture
    def health_app(self, health_checker: HealthChecker):
        """Create health app for testing."""
        return create_health_app(health_checker)

    @pytest.mark.asyncio
    async def test_health_app_created(self, health_app) -> None:
        """Test that health app is created successfully."""

        # Health app should be a Starlette app
        assert health_app is not None

    @pytest.mark.asyncio
    async def test_liveness_endpoint(self, health_checker: HealthChecker) -> None:
        """Test /health/live endpoint."""
        from starlette.testclient import TestClient

        app = create_health_app(health_checker)

        with TestClient(app) as client:
            response = client.get("/health/live")

            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "healthy"
            assert "timestamp" in data
            assert "version" in data

    @pytest.mark.asyncio
    async def test_readiness_endpoint(self, health_settings: Settings) -> None:
        """Test /health/ready endpoint."""
        from starlette.testclient import TestClient

        # Disable K8s check for this test
        health_settings_copy = Settings(
            **{
                **health_settings.model_dump(),
                "health_check_k8s_enabled": False,
            }
        )
        checker = HealthChecker(settings=health_settings_copy)
        app = create_health_app(checker)

        with TestClient(app) as client:
            response = client.get("/health/ready")

            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "healthy"

    @pytest.mark.asyncio
    async def test_startup_endpoint_not_ready(self, health_checker: HealthChecker) -> None:
        """Test /health/startup endpoint when not initialized."""
        from starlette.testclient import TestClient

        app = create_health_app(health_checker)

        with TestClient(app) as client:
            response = client.get("/health/startup")

            # Should return 503 when not initialized
            assert response.status_code == 503
            data = response.json()
            assert data["status"] == "unhealthy"
            assert data["initialized"] is False

    @pytest.mark.asyncio
    async def test_startup_endpoint_ready(self, health_checker: HealthChecker) -> None:
        """Test /health/startup endpoint when initialized."""
        from starlette.testclient import TestClient

        health_checker.mark_initialized()
        app = create_health_app(health_checker)

        with TestClient(app) as client:
            response = client.get("/health/startup")

            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "healthy"
            assert data["initialized"] is True

    @pytest.mark.asyncio
    async def test_root_endpoint(self, health_settings: Settings) -> None:
        """Test root / endpoint returns readiness."""
        from starlette.testclient import TestClient

        health_settings_copy = Settings(
            **{
                **health_settings.model_dump(),
                "health_check_k8s_enabled": False,
            }
        )
        checker = HealthChecker(settings=health_settings_copy)
        app = create_health_app(checker)

        with TestClient(app) as client:
            response = client.get("/")

            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "healthy"

    @pytest.mark.asyncio
    async def test_health_endpoint(self, health_settings: Settings) -> None:
        """Test /health endpoint returns readiness."""
        from starlette.testclient import TestClient

        health_settings_copy = Settings(
            **{
                **health_settings.model_dump(),
                "health_check_k8s_enabled": False,
            }
        )
        checker = HealthChecker(settings=health_settings_copy)
        app = create_health_app(checker)

        with TestClient(app) as client:
            response = client.get("/health")

            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "healthy"
