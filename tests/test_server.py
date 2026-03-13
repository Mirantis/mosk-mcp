"""Tests for MCP server."""

import pytest

from fastmcp.client import Client

from mosk_mcp.core.config import Settings
from mosk_mcp.core.exceptions import (
    AuthenticationError,
    AuthorizationError,
    MoskMCPError,
    ToolExecutionError,
    ValidationError,
)
from mosk_mcp.core.server import (
    create_mcp_server,
    handle_tool_error,
)
from mosk_mcp.registration.models import ServerHealthResult, ServerInfo


def _tool_result_text(data: object) -> str:
    """Extract display text from call_tool result.data (str or MCP content list)."""
    if isinstance(data, str):
        return data
    if isinstance(data, list) and data:
        first = data[0]
        if hasattr(first, "text"):
            return getattr(first, "text", "")
        if isinstance(first, dict):
            return first.get("text", "")
    return ""

class TestCreateMcpServer:
    """Tests for MCP server creation."""

    def test_create_server_with_default_settings(self, default_settings: Settings) -> None:
        """Test server creation with default settings."""
        server = create_mcp_server(default_settings)

        assert server is not None
        assert server.name == "mosk-mcp-test"

    @pytest.mark.asyncio
    async def test_create_server_registers_tools(self, mcp_client: Client) -> None:
        """Test that tools are registered on server creation (FastMCP 3 list_tools)."""
        tools = await mcp_client.list_tools()
        tool_names = [t.name for t in tools]

        assert "health_check" in tool_names
        assert "server_info" in tool_names
        assert "echo" in tool_names

    def test_create_server_with_http_settings(self, http_settings: Settings) -> None:
        """Test server creation with HTTP transport settings."""
        server = create_mcp_server(http_settings)

        assert server is not None


class TestHealthCheckTool:
    """Tests for health_check tool."""

    @pytest.mark.asyncio
    async def test_health_check_returns_healthy(
        self, mcp_client: Client, default_settings: Settings
    ) -> None:
        """Test that health check returns healthy status."""
        result = await mcp_client.call_tool("health_check", {})

        assert result.data is not None
        data = result.data

        assert getattr(data, "status") == "healthy"
        assert getattr(data, "version") == default_settings.app_version
        checks = getattr(data, "checks")
        assert "server" in checks
        assert "config" in checks

    @pytest.mark.asyncio
    async def test_health_check_includes_timestamp(self, mcp_client: Client) -> None:
        """Test that health check includes ISO timestamp."""
        result = await mcp_client.call_tool("health_check", {})

        assert result.data is not None
        data = result.data 
        
        from datetime import datetime

        datetime.fromisoformat(getattr(data, "timestamp").replace("Z", "+00:00"))


class TestServerInfoTool:
    """Tests for server_info tool."""

    @pytest.mark.asyncio
    async def test_server_info_returns_correct_data(
        self, mcp_client: Client, default_settings: Settings
    ) -> None:
        """Test that server info returns correct data."""
        result = await mcp_client.call_tool("server_info", {})

        assert result.data is not None
        data = result.data 

        assert getattr(data, "name") == default_settings.app_name
        assert getattr(data, "version") == default_settings.app_version
        assert getattr(data, "transport") == default_settings.transport.value
        assert getattr(data, "auth_enabled") == default_settings.auth_enabled

    @pytest.mark.asyncio
    async def test_server_info_lists_capabilities(self, mcp_client: Client) -> None:
        """Test that server info lists capabilities."""
        result = await mcp_client.call_tool("server_info", {})

        assert result.data is not None
        data = result.data 
        
        expected_capabilities = [
            "template_generation",
            "node_lifecycle",
            "ceph_operations",
            "visibility",
            "health",
            "troubleshooting",
        ]
        capabilities = getattr(data, "capabilities")
        for cap in expected_capabilities:
            assert cap in capabilities


class TestEchoTool:
    """Tests for echo tool."""

    @pytest.mark.asyncio
    async def test_echo_returns_message(self, mcp_client: Client) -> None:
        """Test that echo returns the message."""
        result = await mcp_client.call_tool("echo", {"message": "Hello, MOSK!"})

        assert result.data is not None
        content = _tool_result_text(result.data)
        assert "[MOSK MCP]" in content
        assert "Hello, MOSK!" in content

    @pytest.mark.asyncio
    async def test_echo_handles_empty_message(self, mcp_client: Client) -> None:
        """Test echo with empty message."""
        result = await mcp_client.call_tool("echo", {"message": ""})

        assert result.data is not None
        content = _tool_result_text(result.data)
        assert "[MOSK MCP]" in content


class TestHandleToolError:
    """Tests for error handling."""

    def test_handle_validation_error(self) -> None:
        """Test handling of ValidationError."""
        error = ValidationError(
            "Invalid input",
            field="hostname",
            value="bad!host",
        )

        result = handle_tool_error(error, "test_tool")

        assert result["error"] == "validation_error"
        assert "Invalid input" in result["message"]
        assert result["details"]["field"] == "hostname"

    def test_handle_authentication_error(self) -> None:
        """Test handling of AuthenticationError."""
        error = AuthenticationError("Invalid token", auth_method="oidc")

        result = handle_tool_error(error, "test_tool")

        assert result["error"] == "authentication_error"
        assert "Invalid token" in result["message"]

    def test_handle_authorization_error(self) -> None:
        """Test handling of AuthorizationError."""
        error = AuthorizationError(
            "Access denied",
            required_permission="admin:cluster",
            user="test-user",
        )

        result = handle_tool_error(error, "test_tool")

        assert result["error"] == "authorization_error"
        assert "Access denied" in result["message"]

    def test_handle_tool_execution_error(self) -> None:
        """Test handling of ToolExecutionError."""
        error = ToolExecutionError(
            "Tool timed out",
            tool_name="generate_machine",
            phase="execution",
        )

        result = handle_tool_error(error, "test_tool")

        assert result["error"] == "tool_execution_error"

    def test_handle_generic_mosk_error(self) -> None:
        """Test handling of generic MoskMCPError."""
        error = MoskMCPError(
            "Something went wrong",
            error_code="CUSTOM_ERROR",
        )

        result = handle_tool_error(error, "test_tool")

        assert result["error"] == "custom_error"

    def test_handle_unexpected_error(self) -> None:
        """Test handling of unexpected exceptions."""
        error = RuntimeError("Unexpected failure")

        result = handle_tool_error(error, "test_tool")

        assert result["error"] == "internal_error"
        assert result["details"]["error_type"] == "RuntimeError"
        # Should not expose internal error message
        assert "Unexpected failure" not in result["message"]


class TestServerModels:
    """Tests for Pydantic models."""

    def test_health_check_result_model(self) -> None:
        """Test ServerHealthResult model."""
        result = ServerHealthResult(
            status="healthy",
            timestamp="2024-01-01T00:00:00Z",
            version="0.1.0",
            checks={"server": {"status": "healthy"}},
        )

        assert result.status == "healthy"
        assert result.checks["server"]["status"] == "healthy"

    def test_server_info_model(self) -> None:
        """Test ServerInfo model."""
        info = ServerInfo(
            name="mosk-mcp",
            version="0.1.0",
            transport="stdio",
            auth_enabled=True,
            capabilities=["template_generation"],
        )

        assert info.name == "mosk-mcp"
        assert "template_generation" in info.capabilities
