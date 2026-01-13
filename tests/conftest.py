"""Pytest configuration and fixtures for MOSK MCP Server tests.

This module provides common fixtures used across the test suite:
- Settings fixtures with various configurations
- MCP server fixtures
- Mock adapters
- Authentication fixtures
"""

import os
from collections.abc import Generator
from datetime import UTC
from unittest.mock import MagicMock, patch

import pytest

from mosk_mcp.auth.types import Permission, Role, UserContext
from mosk_mcp.core.config import Environment, LogFormat, LogLevel, Settings, TransportType


# =============================================================================
# Environment Fixtures
# =============================================================================


@pytest.fixture(autouse=True)
def clean_environment() -> Generator[None, None, None]:
    """Ensure clean environment for each test.

    Removes MCP-related environment variables before each test
    to ensure tests don't interfere with each other.
    """
    # Store original environment
    original_env = os.environ.copy()

    # Remove MCP-related env vars
    mcp_vars = [key for key in os.environ if key.startswith("MCP_")]
    for var in mcp_vars:
        del os.environ[var]

    yield

    # Restore original environment
    os.environ.clear()
    os.environ.update(original_env)


@pytest.fixture
def env_vars() -> Generator[dict[str, str], None, None]:
    """Fixture to set environment variables for a test.

    Usage:
        def test_something(env_vars):
            env_vars["MCP_LOG_LEVEL"] = "DEBUG"
            # ... test code
    """
    env_dict: dict[str, str] = {}

    class EnvVarSetter(dict):  # type: ignore[type-arg]
        def __setitem__(self, key: str, value: str) -> None:
            os.environ[key] = value
            super().__setitem__(key, value)

        def __delitem__(self, key: str) -> None:
            if key in os.environ:
                del os.environ[key]
            super().__delitem__(key)

    setter = EnvVarSetter(env_dict)
    yield setter

    # Cleanup
    for key in setter:
        if key in os.environ:
            del os.environ[key]


# =============================================================================
# Settings Fixtures
# =============================================================================


@pytest.fixture
def default_settings() -> Settings:
    """Create default settings for testing.

    Uses development mode which doesn't require MCC URL.
    """
    return Settings(
        app_name="mosk-mcp-test",
        app_version="0.1.0-test",
        transport=TransportType.STDIO,
        log_level=LogLevel.DEBUG,
        log_format=LogFormat.CONSOLE,
        environment=Environment.DEVELOPMENT,
        auth_enabled=False,
        otel_enabled=False,
    )


@pytest.fixture
def auth_enabled_settings() -> Settings:
    """Create settings with authentication enabled."""
    return Settings(
        app_name="mosk-mcp-test",
        app_version="0.1.0-test",
        transport=TransportType.STDIO,
        log_level=LogLevel.DEBUG,
        log_format=LogFormat.CONSOLE,
        environment=Environment.DEVELOPMENT,
        auth_enabled=True,
        otel_enabled=False,
    )


@pytest.fixture
def http_settings() -> Settings:
    """Create settings for HTTP transport."""
    return Settings(
        app_name="mosk-mcp-test",
        app_version="0.1.0-test",
        transport=TransportType.HTTP,
        http_host="127.0.0.1",
        http_port=8888,
        log_level=LogLevel.DEBUG,
        log_format=LogFormat.CONSOLE,
        environment=Environment.DEVELOPMENT,
        auth_enabled=False,
        otel_enabled=False,
    )


@pytest.fixture
def production_settings() -> Settings:
    """Create production-like settings.

    Production mode requires MCC URL.
    """
    return Settings(
        app_name="mosk-mcp",
        app_version="0.1.0",
        transport=TransportType.STDIO,
        log_level=LogLevel.INFO,
        log_format=LogFormat.JSON,
        environment=Environment.PRODUCTION,
        auth_enabled=True,
        otel_enabled=False,
        mcc_url="https://172.16.166.22",
    )


# =============================================================================
# Authentication Fixtures
# =============================================================================


@pytest.fixture
def viewer_context() -> UserContext:
    """Create a user context with viewer role."""
    from datetime import datetime

    return UserContext(
        user_id="test-viewer-001",
        username="test-viewer",
        role=Role.VIEWER,
        permissions=frozenset(
            [
                Permission.READ_MACHINES,
                Permission.READ_OSDPL,
                Permission.READ_CEPH,
                Permission.READ_LOGS,
                Permission.READ_HEALTH,
            ]
        ),
        authenticated_at=datetime.now(UTC),
        auth_method="test",
    )


@pytest.fixture
def operator_context() -> UserContext:
    """Create a user context with operator role."""
    from datetime import datetime

    return UserContext(
        user_id="test-operator-001",
        username="test-operator",
        role=Role.OPERATOR,
        permissions=frozenset(
            [
                Permission.READ_MACHINES,
                Permission.READ_OSDPL,
                Permission.READ_CEPH,
                Permission.READ_LOGS,
                Permission.READ_HEALTH,
                Permission.WRITE_MACHINES,
                Permission.WRITE_OSDPL,
                Permission.EXECUTE_MAINTENANCE,
                Permission.EXECUTE_CEPH_OPS,
            ]
        ),
        authenticated_at=datetime.now(UTC),
        auth_method="test",
    )


@pytest.fixture
def admin_context() -> UserContext:
    """Create a user context with administrator role."""
    from datetime import datetime

    return UserContext(
        user_id="test-admin-001",
        username="test-admin",
        role=Role.ADMINISTRATOR,
        permissions=frozenset(Permission),
        authenticated_at=datetime.now(UTC),
        auth_method="test",
    )


# =============================================================================
# Server Fixtures
# =============================================================================


@pytest.fixture
def mcp_server(default_settings: Settings):
    """Create an MCP server for testing."""
    from mosk_mcp.core.server import create_mcp_server

    return create_mcp_server(default_settings)


# =============================================================================
# Mock Fixtures
# =============================================================================


@pytest.fixture
def mock_kubernetes_client() -> Generator[MagicMock, None, None]:
    """Create a mock Kubernetes client."""
    with patch("kr8s.asyncio.api") as mock_api:
        mock_api.return_value = MagicMock()
        yield mock_api


# =============================================================================
# Utility Fixtures
# =============================================================================


@pytest.fixture
def capture_logs() -> Generator[list[dict], None, None]:
    """Capture structured log output for assertions.

    Usage:
        def test_logging(capture_logs):
            logger.info("test message", key="value")
            assert len(capture_logs) == 1
            assert capture_logs[0]["key"] == "value"
    """
    import structlog

    captured: list[dict] = []

    def capture_processor(logger: object, method_name: str, event_dict: dict) -> dict:
        captured.append(event_dict.copy())
        return event_dict

    # Get current processors and insert our capture processor
    original_processors = structlog.get_config().get("processors", [])

    # Configure with capture processor
    structlog.configure(
        processors=[capture_processor, *original_processors],
    )

    yield captured

    # Restore original configuration
    structlog.configure(processors=original_processors)
