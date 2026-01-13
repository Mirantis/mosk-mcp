"""Unit tests for the metrics module.

Tests for:
- MetricsRegistry class functionality
- Tool invocation tracking
- Authentication failure tracking
- Privileged operation tracking
- Prometheus metrics generation
- Metrics endpoint
"""

import time

import pytest

from mosk_mcp.core.config import Environment, LogFormat, LogLevel, Settings, TransportType
from mosk_mcp.observability.metrics import (
    MetricsRegistry,
    SafetyLevel,
    ToolStatus,
    create_metrics_app,
    get_metrics_registry,
    init_metrics_registry,
    record_auth_failure,
    record_k8s_request,
    record_privileged_op,
    track_tool,
)


@pytest.fixture
def metrics_settings() -> Settings:
    """Create settings for metrics tests."""
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
    )


@pytest.fixture
def metrics_registry(metrics_settings: Settings) -> MetricsRegistry:
    """Create a metrics registry for testing."""
    return MetricsRegistry(metrics_settings)


class TestToolStatus:
    """Tests for ToolStatus enum."""

    def test_status_values(self) -> None:
        """Test that all expected status values exist."""
        assert ToolStatus.SUCCESS == "success"
        assert ToolStatus.ERROR == "error"
        assert ToolStatus.VALIDATION_ERROR == "validation_error"
        assert ToolStatus.AUTH_ERROR == "auth_error"
        assert ToolStatus.TIMEOUT == "timeout"


class TestSafetyLevel:
    """Tests for SafetyLevel enum."""

    def test_safety_level_values(self) -> None:
        """Test that all expected safety levels exist."""
        assert SafetyLevel.READ_ONLY == "read_only"
        assert SafetyLevel.NON_DESTRUCTIVE == "non_destructive"
        assert SafetyLevel.PRIVILEGED == "privileged"


class TestMetricsRegistry:
    """Tests for MetricsRegistry class."""

    def test_initialization(self, metrics_registry: MetricsRegistry) -> None:
        """Test MetricsRegistry initialization."""
        assert metrics_registry.registry is not None
        assert metrics_registry.requests_total is not None
        assert metrics_registry.request_duration_seconds is not None
        assert metrics_registry.active_connections is not None
        assert metrics_registry.auth_failures_total is not None
        assert metrics_registry.privileged_ops_total is not None
        assert metrics_registry.server_info is not None

    def test_record_tool_invocation(self, metrics_registry: MetricsRegistry) -> None:
        """Test recording a tool invocation."""
        metrics_registry.record_tool_invocation(
            tool="test_tool",
            status=ToolStatus.SUCCESS,
            duration_seconds=0.5,
            safety_level=SafetyLevel.READ_ONLY,
        )

        # Verify counter was incremented
        metrics_output = metrics_registry.generate_metrics().decode()
        assert "mosk_mcp_requests_total{" in metrics_output
        assert 'tool="test_tool"' in metrics_output
        assert 'status="success"' in metrics_output

    def test_record_tool_invocation_with_error(self, metrics_registry: MetricsRegistry) -> None:
        """Test recording a failed tool invocation."""
        metrics_registry.record_tool_invocation(
            tool="failing_tool",
            status=ToolStatus.ERROR,
            duration_seconds=1.0,
            safety_level=SafetyLevel.NON_DESTRUCTIVE,
        )

        metrics_output = metrics_registry.generate_metrics().decode()
        assert 'tool="failing_tool"' in metrics_output
        assert 'status="error"' in metrics_output
        assert 'safety_level="non_destructive"' in metrics_output

    def test_record_auth_failure(self, metrics_registry: MetricsRegistry) -> None:
        """Test recording an authentication failure."""
        metrics_registry.record_auth_failure(
            reason="invalid_key",
            auth_method="api_key",
        )

        metrics_output = metrics_registry.generate_metrics().decode()
        assert "mosk_mcp_auth_failures_total{" in metrics_output
        assert 'reason="invalid_key"' in metrics_output
        assert 'auth_method="api_key"' in metrics_output

    def test_record_privileged_operation(self, metrics_registry: MetricsRegistry) -> None:
        """Test recording a privileged operation."""
        metrics_registry.record_privileged_operation(
            tool="delete_machine",
            crq_number="CRQ000123456",
        )

        metrics_output = metrics_registry.generate_metrics().decode()
        assert "mosk_mcp_privileged_ops_total{" in metrics_output
        assert 'tool="delete_machine"' in metrics_output
        assert 'crq_number="CRQ000123456"' in metrics_output

    def test_record_tool_error(self, metrics_registry: MetricsRegistry) -> None:
        """Test recording a tool error."""
        metrics_registry.record_tool_error(
            tool="test_tool",
            error_type="ValidationError",
        )

        metrics_output = metrics_registry.generate_metrics().decode()
        assert "mosk_mcp_tool_errors_total{" in metrics_output
        assert 'tool="test_tool"' in metrics_output
        assert 'error_type="ValidationError"' in metrics_output

    def test_record_k8s_request(self, metrics_registry: MetricsRegistry) -> None:
        """Test recording a Kubernetes API request."""
        metrics_registry.record_k8s_request(
            operation="get",
            resource_kind="Machine",
            status="success",
            duration_seconds=0.1,
        )

        metrics_output = metrics_registry.generate_metrics().decode()
        assert "mosk_mcp_k8s_requests_total{" in metrics_output
        assert 'operation="get"' in metrics_output
        assert 'resource_kind="Machine"' in metrics_output

    def test_connection_tracking(self, metrics_registry: MetricsRegistry) -> None:
        """Test connection increment/decrement."""
        # Initial value should be 0
        metrics_registry.increment_connections()
        metrics_registry.increment_connections()

        metrics_output = metrics_registry.generate_metrics().decode()
        assert "mosk_mcp_active_connections 2.0" in metrics_output

        metrics_registry.decrement_connections()
        metrics_output = metrics_registry.generate_metrics().decode()
        assert "mosk_mcp_active_connections 1.0" in metrics_output

    def test_track_connection_context_manager(self, metrics_registry: MetricsRegistry) -> None:
        """Test connection tracking context manager."""
        with metrics_registry.track_connection():
            metrics_output = metrics_registry.generate_metrics().decode()
            # Connection should be incremented
            assert "mosk_mcp_active_connections 1.0" in metrics_output

        # After context exits, connection should be decremented
        metrics_output = metrics_registry.generate_metrics().decode()
        assert "mosk_mcp_active_connections 0.0" in metrics_output

    def test_track_tool_execution_success(self, metrics_registry: MetricsRegistry) -> None:
        """Test tool execution tracking context manager - success."""
        with metrics_registry.track_tool_execution("my_tool", SafetyLevel.READ_ONLY):
            time.sleep(0.01)  # Simulate some work

        metrics_output = metrics_registry.generate_metrics().decode()
        assert 'tool="my_tool"' in metrics_output
        assert 'status="success"' in metrics_output

    def test_track_tool_execution_error(self, metrics_registry: MetricsRegistry) -> None:
        """Test tool execution tracking context manager - error."""
        with (
            pytest.raises(ValueError),
            metrics_registry.track_tool_execution("failing_tool", SafetyLevel.NON_DESTRUCTIVE),
        ):
            raise ValueError("Test error")

        metrics_output = metrics_registry.generate_metrics().decode()
        assert 'tool="failing_tool"' in metrics_output
        assert 'status="error"' in metrics_output
        assert 'error_type="ValueError"' in metrics_output

    def test_track_tool_execution_validation_error(self, metrics_registry: MetricsRegistry) -> None:
        """Test tool execution tracking - validation error classification."""
        from mosk_mcp.core.exceptions import ValidationError

        with (
            pytest.raises(ValidationError),
            metrics_registry.track_tool_execution("tool_with_validation", SafetyLevel.READ_ONLY),
        ):
            raise ValidationError("Invalid input")

        metrics_output = metrics_registry.generate_metrics().decode()
        assert 'status="validation_error"' in metrics_output

    def test_track_tool_execution_auth_error(self, metrics_registry: MetricsRegistry) -> None:
        """Test tool execution tracking - auth error classification."""
        from mosk_mcp.core.exceptions import AuthenticationError

        with (
            pytest.raises(AuthenticationError),
            metrics_registry.track_tool_execution("protected_tool", SafetyLevel.PRIVILEGED),
        ):
            raise AuthenticationError("Not authenticated")

        metrics_output = metrics_registry.generate_metrics().decode()
        assert 'status="auth_error"' in metrics_output

    def test_generate_metrics(self, metrics_registry: MetricsRegistry) -> None:
        """Test generating Prometheus metrics output."""
        # Record some metrics
        metrics_registry.record_tool_invocation(
            "test", ToolStatus.SUCCESS, 0.1, SafetyLevel.READ_ONLY
        )

        output = metrics_registry.generate_metrics()

        assert isinstance(output, bytes)
        assert b"mosk_mcp_requests_total" in output
        assert b"mosk_mcp_server_info" in output

    def test_get_content_type(self, metrics_registry: MetricsRegistry) -> None:
        """Test getting content type for metrics."""
        content_type = metrics_registry.get_content_type()

        assert "text/plain" in content_type or "text/plain" in content_type

    def test_server_info_metric(self, metrics_registry: MetricsRegistry) -> None:
        """Test server info metric contains expected values."""
        metrics_output = metrics_registry.generate_metrics().decode()

        assert "mosk_mcp_server_info{" in metrics_output
        assert 'version="0.1.0-test"' in metrics_output
        assert 'app_name="mosk-mcp-test"' in metrics_output


class TestMetricsRegistryGlobalFunctions:
    """Tests for global metrics registry functions."""

    def test_init_metrics_registry(self, metrics_settings: Settings) -> None:
        """Test initializing global metrics registry."""
        registry = init_metrics_registry(metrics_settings)

        assert registry is not None
        assert get_metrics_registry() == registry

    def test_get_metrics_registry_not_initialized(self) -> None:
        """Test getting registry when not initialized."""
        result = get_metrics_registry()
        # Result is either None or a MetricsRegistry instance
        assert result is None or isinstance(result, MetricsRegistry)


class TestConvenienceFunctions:
    """Tests for convenience functions."""

    def test_record_privileged_op(self, metrics_settings: Settings) -> None:
        """Test record_privileged_op convenience function."""
        # Initialize registry
        registry = init_metrics_registry(metrics_settings)

        record_privileged_op("test_tool", "CRQ000000001")

        metrics_output = registry.generate_metrics().decode()
        assert 'tool="test_tool"' in metrics_output
        assert 'crq_number="CRQ000000001"' in metrics_output

    def test_record_auth_failure_function(self, metrics_settings: Settings) -> None:
        """Test record_auth_failure convenience function."""
        registry = init_metrics_registry(metrics_settings)

        record_auth_failure("expired_token", "jwt")

        metrics_output = registry.generate_metrics().decode()
        assert 'reason="expired_token"' in metrics_output
        assert 'auth_method="jwt"' in metrics_output

    def test_record_k8s_request_function(self, metrics_settings: Settings) -> None:
        """Test record_k8s_request convenience function."""
        registry = init_metrics_registry(metrics_settings)

        record_k8s_request("list", "Pod", "success", 0.5)

        metrics_output = registry.generate_metrics().decode()
        assert 'operation="list"' in metrics_output
        assert 'resource_kind="Pod"' in metrics_output


class TestTrackToolDecorator:
    """Tests for track_tool decorator."""

    @pytest.mark.asyncio
    async def test_track_tool_async_function(self, metrics_settings: Settings) -> None:
        """Test tracking an async function."""
        registry = init_metrics_registry(metrics_settings)

        @track_tool(name="decorated_tool", safety_level=SafetyLevel.READ_ONLY)
        async def my_async_tool() -> str:
            return "result"

        result = await my_async_tool()

        assert result == "result"
        metrics_output = registry.generate_metrics().decode()
        assert 'tool="decorated_tool"' in metrics_output
        assert 'status="success"' in metrics_output

    @pytest.mark.asyncio
    async def test_track_tool_async_function_error(self, metrics_settings: Settings) -> None:
        """Test tracking an async function that errors."""
        registry = init_metrics_registry(metrics_settings)

        @track_tool(name="error_tool", safety_level=SafetyLevel.NON_DESTRUCTIVE)
        async def my_failing_tool() -> str:
            raise RuntimeError("Tool failed")

        with pytest.raises(RuntimeError):
            await my_failing_tool()

        metrics_output = registry.generate_metrics().decode()
        assert 'tool="error_tool"' in metrics_output
        assert 'status="error"' in metrics_output

    def test_track_tool_sync_function(self, metrics_settings: Settings) -> None:
        """Test tracking a sync function."""
        registry = init_metrics_registry(metrics_settings)

        @track_tool(name="sync_tool", safety_level=SafetyLevel.READ_ONLY)
        def my_sync_tool() -> int:
            return 42

        result = my_sync_tool()

        assert result == 42
        metrics_output = registry.generate_metrics().decode()
        assert 'tool="sync_tool"' in metrics_output

    def test_track_tool_default_name(self, metrics_settings: Settings) -> None:
        """Test that decorator uses function name by default."""
        registry = init_metrics_registry(metrics_settings)

        @track_tool()
        def my_named_function() -> None:
            pass

        my_named_function()

        metrics_output = registry.generate_metrics().decode()
        assert 'tool="my_named_function"' in metrics_output


class TestCreateMetricsApp:
    """Tests for create_metrics_app function."""

    @pytest.fixture
    def metrics_app(self, metrics_registry: MetricsRegistry):
        """Create metrics app for testing."""
        return create_metrics_app(metrics_registry)

    def test_metrics_app_created(self, metrics_app) -> None:
        """Test that metrics app is created successfully."""
        assert metrics_app is not None

    def test_metrics_endpoint(self, metrics_registry: MetricsRegistry) -> None:
        """Test /metrics endpoint."""
        from starlette.testclient import TestClient

        # Record some metrics first
        metrics_registry.record_tool_invocation(
            "test", ToolStatus.SUCCESS, 0.1, SafetyLevel.READ_ONLY
        )

        app = create_metrics_app(metrics_registry)

        with TestClient(app) as client:
            response = client.get("/metrics")

            assert response.status_code == 200
            assert "text/plain" in response.headers["content-type"]
            assert "mosk_mcp_requests_total" in response.text
            assert "mosk_mcp_server_info" in response.text

    def test_root_metrics_endpoint(self, metrics_registry: MetricsRegistry) -> None:
        """Test root / endpoint returns metrics."""
        from starlette.testclient import TestClient

        app = create_metrics_app(metrics_registry)

        with TestClient(app) as client:
            response = client.get("/")

            assert response.status_code == 200
            assert "mosk_mcp_" in response.text


class TestMetricsHistogram:
    """Tests for histogram metrics."""

    def test_duration_histogram_buckets(self, metrics_registry: MetricsRegistry) -> None:
        """Test that duration histogram uses appropriate buckets."""
        # Record invocations at different durations
        metrics_registry.record_tool_invocation(
            "fast_tool", ToolStatus.SUCCESS, 0.01, SafetyLevel.READ_ONLY
        )
        metrics_registry.record_tool_invocation(
            "slow_tool", ToolStatus.SUCCESS, 5.0, SafetyLevel.READ_ONLY
        )

        metrics_output = metrics_registry.generate_metrics().decode()

        # Check that bucket boundaries exist
        assert "mosk_mcp_request_duration_seconds_bucket{" in metrics_output
        assert 'le="0.01"' in metrics_output
        assert 'le="1.0"' in metrics_output
        assert 'le="60.0"' in metrics_output

    def test_k8s_duration_histogram(self, metrics_registry: MetricsRegistry) -> None:
        """Test K8s request duration histogram."""
        metrics_registry.record_k8s_request("get", "Machine", "success", 0.05)

        metrics_output = metrics_registry.generate_metrics().decode()
        assert "mosk_mcp_k8s_request_duration_seconds_bucket{" in metrics_output


class TestMultipleInvocations:
    """Tests for multiple metric invocations."""

    def test_counter_accumulates(self, metrics_registry: MetricsRegistry) -> None:
        """Test that counters accumulate correctly."""
        # Record multiple invocations of the same tool
        for _ in range(5):
            metrics_registry.record_tool_invocation(
                "repeated_tool", ToolStatus.SUCCESS, 0.1, SafetyLevel.READ_ONLY
            )

        metrics_output = metrics_registry.generate_metrics().decode()

        # Find the specific counter line
        for line in metrics_output.split("\n"):
            if "mosk_mcp_requests_total{" in line and "repeated_tool" in line:
                assert "5.0" in line
                break

    def test_different_tools_tracked_separately(self, metrics_registry: MetricsRegistry) -> None:
        """Test that different tools are tracked separately."""
        metrics_registry.record_tool_invocation(
            "tool_a", ToolStatus.SUCCESS, 0.1, SafetyLevel.READ_ONLY
        )
        metrics_registry.record_tool_invocation(
            "tool_b", ToolStatus.ERROR, 0.2, SafetyLevel.NON_DESTRUCTIVE
        )

        metrics_output = metrics_registry.generate_metrics().decode()

        assert 'tool="tool_a"' in metrics_output
        assert 'tool="tool_b"' in metrics_output
