"""Tests for exception hierarchy."""

import pytest

from mosk_mcp.core.exceptions import (
    AuthenticationError,
    AuthorizationError,
    ConfigurationError,
    KubernetesError,
    MoskConnectionError,
    MoskMCPError,
    RateLimitError,
    ResourceNotFoundError,
    ToolExecutionError,
    ValidationError,
)


class TestMoskMCPError:
    """Tests for base exception class."""

    def test_basic_creation(self) -> None:
        """Test basic exception creation."""
        error = MoskMCPError("Something went wrong")

        assert str(error) == "Something went wrong"
        assert error.message == "Something went wrong"
        assert error.details == {}
        assert error.error_code == "MOSKMCPERROR"

    def test_with_details(self) -> None:
        """Test exception with details."""
        error = MoskMCPError(
            "Operation failed",
            details={"resource": "machine", "action": "create"},
        )

        assert error.details == {"resource": "machine", "action": "create"}
        assert "resource" in str(error)

    def test_with_custom_error_code(self) -> None:
        """Test exception with custom error code."""
        error = MoskMCPError(
            "Custom error",
            error_code="CUSTOM_ERROR_001",
        )

        assert error.error_code == "CUSTOM_ERROR_001"

    def test_to_dict(self) -> None:
        """Test conversion to dictionary."""
        error = MoskMCPError(
            "Test error",
            details={"key": "value"},
            error_code="TEST_ERROR",
        )

        result = error.to_dict()

        assert result == {
            "error": "TEST_ERROR",
            "message": "Test error",
            "details": {"key": "value"},
        }

    def test_repr(self) -> None:
        """Test repr output."""
        error = MoskMCPError("Test", details={"a": 1}, error_code="TEST")
        repr_str = repr(error)

        assert "MoskMCPError" in repr_str
        assert "Test" in repr_str


class TestAuthenticationError:
    """Tests for AuthenticationError."""

    def test_default_message(self) -> None:
        """Test default error message."""
        error = AuthenticationError()

        assert error.message == "Authentication failed"
        assert error.error_code == "AUTHENTICATION_ERROR"

    def test_with_auth_method(self) -> None:
        """Test with authentication method."""
        error = AuthenticationError(
            message="Token expired",
            auth_method="oidc",
        )

        assert error.auth_method == "oidc"
        assert error.details["auth_method"] == "oidc"


class TestAuthorizationError:
    """Tests for AuthorizationError."""

    def test_with_permission(self) -> None:
        """Test with required permission."""
        error = AuthorizationError(
            message="Access denied",
            required_permission="admin:cluster",
            user="test-user",
            resource="machine/compute-01",
        )

        assert error.required_permission == "admin:cluster"
        assert error.user == "test-user"
        assert error.resource == "machine/compute-01"
        assert error.error_code == "AUTHORIZATION_ERROR"


class TestValidationError:
    """Tests for ValidationError."""

    def test_with_field_info(self) -> None:
        """Test with field validation info."""
        error = ValidationError(
            message="Invalid hostname",
            field="hostname",
            value="INVALID_HOST!",
            constraint="must match ^[a-z0-9-]+$",
        )

        assert error.field == "hostname"
        assert error.constraint == "must match ^[a-z0-9-]+$"

    def test_value_sanitization(self) -> None:
        """Test that long values are truncated."""
        long_value = "x" * 200
        error = ValidationError(
            message="Value too long",
            field="data",
            value=long_value,
        )

        # Value should be truncated in details
        assert len(error.details["value"]) <= 103  # 100 + "..."


class TestKubernetesError:
    """Tests for KubernetesError."""

    def test_with_resource_info(self) -> None:
        """Test with Kubernetes resource information."""
        error = KubernetesError(
            message="Failed to create machine",
            operation="create",
            resource_kind="Machine",
            resource_name="compute-01",
            namespace="default",
            status_code=409,
        )

        assert error.operation == "create"
        assert error.resource_kind == "Machine"
        assert error.resource_name == "compute-01"
        assert error.namespace == "default"
        assert error.status_code == 409


class TestToolExecutionError:
    """Tests for ToolExecutionError."""

    def test_with_tool_info(self) -> None:
        """Test with tool execution information."""
        error = ToolExecutionError(
            message="Tool timed out",
            tool_name="generate_machine",
            phase="execution",
        )

        assert error.tool_name == "generate_machine"
        assert error.phase == "execution"


class TestConfigurationError:
    """Tests for ConfigurationError."""

    def test_with_config_key(self) -> None:
        """Test with configuration key."""
        error = ConfigurationError(
            message="Missing required configuration",
            config_key="MCP_AUTH_API_KEY",
        )

        assert error.config_key == "MCP_AUTH_API_KEY"


class TestMoskConnectionError:
    """Tests for MoskConnectionError."""

    def test_with_service_info(self) -> None:
        """Test with service connection information."""
        error = MoskConnectionError(
            message="Cannot connect to cluster",
            service="kubernetes",
            endpoint="https://k8s.example.com:6443",
        )

        assert error.service == "kubernetes"
        assert error.endpoint == "https://k8s.example.com:6443"


class TestResourceNotFoundError:
    """Tests for ResourceNotFoundError."""

    def test_with_resource_info(self) -> None:
        """Test with resource information."""
        error = ResourceNotFoundError(
            message="Machine not found",
            resource_type="Machine",
            resource_id="compute-99",
        )

        assert error.resource_type == "Machine"
        assert error.resource_id == "compute-99"
        assert error.error_code == "RESOURCE_NOT_FOUND"


class TestRateLimitError:
    """Tests for RateLimitError."""

    def test_with_retry_after(self) -> None:
        """Test with retry-after information."""
        error = RateLimitError(
            message="Too many requests",
            retry_after=60,
        )

        assert error.retry_after == 60
        assert error.details["retry_after"] == 60


class TestExceptionHierarchy:
    """Tests for exception inheritance."""

    def test_all_exceptions_inherit_from_base(self) -> None:
        """Test that all exceptions inherit from MoskMCPError."""
        exceptions = [
            AuthenticationError(),
            AuthorizationError(),
            ValidationError(),
            KubernetesError(),
            ToolExecutionError(),
            ConfigurationError(),
            MoskConnectionError(),
            ResourceNotFoundError(),
            RateLimitError(),
        ]

        for exc in exceptions:
            assert isinstance(exc, MoskMCPError)
            assert isinstance(exc, Exception)

    def test_catch_base_exception(self) -> None:
        """Test that base exception catches all derived exceptions."""
        with pytest.raises(MoskMCPError):
            raise ValidationError("test")

        with pytest.raises(MoskMCPError):
            raise KubernetesError("test")

        with pytest.raises(MoskMCPError):
            raise AuthenticationError("test")
