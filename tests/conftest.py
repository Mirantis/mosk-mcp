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
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from pydantic_settings import SettingsConfigDict

from mosk_mcp import __version__
from mosk_mcp.auth.types import Permission, Role, UserContext
from mosk_mcp.core.config import Environment, LogFormat, LogLevel, Settings, TransportType

# =============================================================================
# FastMCP json_schema_type monkey patch (object with arbitrary fields → dict)
# =============================================================================
#
# FastMCP's json_schema_to_type turns nested "object" schemas that have only
# additionalProperties (no fixed "properties") into an empty dataclass, so
# validated result.data in the tools tests may have no expected attributes
# and the tests fail. Patch _schema_to_type to return dict[str, Any] for that
# case until upstream fixes it.

def _fastmcp_has_arbitrary_object_bug() -> bool:
    """Return True if the current FastMCP still turns nested
    object+additionalProperties into empty dataclass."""
    from fastmcp.utilities.json_schema_type import json_schema_to_type
    from fastmcp.utilities.types import get_cached_typeadapter

    schema = {
        "type": "object",
        "properties": {
            "checks": {
                "type": "object",
                "additionalProperties": True,
            },
        },
        "required": ["checks"],
    }
    try:
        output_type = json_schema_to_type(schema)
        adapter = get_cached_typeadapter(output_type)
        data = adapter.validate_python({"checks": {"server": {"status": "ok"}}})
        checks = getattr(data, "checks", None)
        return not (isinstance(checks, dict) and "server" in checks)
    except Exception:
        return True  # Assume bug present if probe fails


def _apply_fastmcp_json_schema_patch() -> None:
    """Monkey-patch fastmcp so object schemas with only
    additionalProperties become dict[str, Any]."""

    import warnings

    if not _fastmcp_has_arbitrary_object_bug():
        warnings.warn(
            "FastMCP json_schema arbitrary-object bug NOT detected; "
            "skipping conftest monkey patch. You can remove this code since "
            "you are using a fixed FastMCP release.",
            UserWarning,
            stacklevel=2,
        )
        return

    import fastmcp.utilities.json_schema_type as _json_schema_type

    _original_schema_to_type = _json_schema_type._schema_to_type

    def _patched_schema_to_type(schema: Any, schemas: Any) -> Any:
        if (
            schema.get("type") == "object"
            and not schema.get("properties")
            and schema.get("additionalProperties")
        ):
            return dict[str, Any]
        return _original_schema_to_type(schema, schemas)

    _json_schema_type._schema_to_type = _patched_schema_to_type


def _patch_settings_env_file_for_pytest() -> None:
    """Monkey-patch ``Settings.model_config`` so ``env_file`` is ``None``.

    Stops pydantic-settings from loading a project ``.env`` during tests; values
    come from Field defaults and ``MCP_*`` environment variables only.
    """
    merged = dict(Settings.model_config)
    merged["env_file"] = None
    Settings.model_config = SettingsConfigDict(**merged)


def pytest_configure(config: pytest.Config) -> None:
    """Apply FastMCP json_schema patch before any tests run."""
    _patch_settings_env_file_for_pytest()
    _apply_fastmcp_json_schema_patch()

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
        app_version=__version__,
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


@pytest.fixture
async def mcp_client(mcp_server):
    """MCP client connected to the test server (in-process) for testing tools.

    Uses FastMCP in-memory transport so the client talks to the server
    in the same process. Use in async tests to call list_tools(), call_tool(), etc.

    Example:
        @pytest.mark.asyncio
        async def test_health(mcp_client):
            result = await mcp_client.call_tool("health_check", {})
            assert result.data is not None
    """
    from fastmcp.client import Client

    async with Client(transport=mcp_server) as client:
        yield client


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

