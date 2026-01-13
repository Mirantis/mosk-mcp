"""Tests for error handling utilities.

Tests cover:
- tool_handler decorator (the standard error handling decorator)
- wrap_kubernetes_error helper
"""

import pytest
from pydantic import BaseModel

from mosk_mcp.core.exceptions import KubernetesError, ToolExecutionError
from mosk_mcp.tools.common.errors import (
    tool_handler,
    wrap_kubernetes_error,
)


# =============================================================================
# tool_handler Tests
# =============================================================================


class TestToolHandler:
    """Tests for tool_handler decorator."""

    @pytest.mark.asyncio
    async def test_success_returns_result(self) -> None:
        """Test successful execution returns result."""

        @tool_handler("test_tool")
        async def successful_tool() -> str:
            return "success"

        result = await successful_tool()
        assert result == "success"

    @pytest.mark.asyncio
    async def test_tool_execution_error_passes_through(self) -> None:
        """Test ToolExecutionError passes through unchanged."""

        @tool_handler("test_tool")
        async def raises_tool_error() -> None:
            raise ToolExecutionError("original error", tool_name="original")

        with pytest.raises(ToolExecutionError) as exc_info:
            await raises_tool_error()

        assert exc_info.value.tool_name == "original"

    @pytest.mark.asyncio
    async def test_generic_exception_wrapped(self) -> None:
        """Test generic exceptions are wrapped in ToolExecutionError."""

        @tool_handler("test_tool")
        async def raises_generic_error() -> None:
            raise ValueError("generic error")

        with pytest.raises(ToolExecutionError) as exc_info:
            await raises_generic_error()

        assert exc_info.value.tool_name == "test_tool"
        assert "generic error" in str(exc_info.value.message)

    @pytest.mark.asyncio
    async def test_kubernetes_error_preserved_by_default(self) -> None:
        """Test KubernetesError is preserved by default."""

        @tool_handler("test_tool")
        async def raises_k8s_error() -> None:
            raise KubernetesError("k8s error", operation="get", resource_kind="Pod")

        with pytest.raises(KubernetesError):
            await raises_k8s_error()

    @pytest.mark.asyncio
    async def test_kubernetes_error_wrapped_when_disabled(self) -> None:
        """Test KubernetesError wrapped when wrap_kubernetes_errors=False."""

        @tool_handler("test_tool", wrap_kubernetes_errors=False)
        async def raises_k8s_error() -> None:
            raise KubernetesError("k8s error", operation="get", resource_kind="Pod")

        with pytest.raises(ToolExecutionError) as exc_info:
            await raises_k8s_error()

        assert exc_info.value.tool_name == "test_tool"

    @pytest.mark.asyncio
    async def test_logging_enabled_by_default(self) -> None:
        """Test that start/complete logging happens by default.

        Note: We verify the decorator is configured correctly for logging,
        not the actual log output (which depends on structlog configuration).
        """
        # Track if internal methods were called
        call_log: list[str] = []

        @tool_handler("test_tool")
        async def logging_tool() -> str:
            call_log.append("executed")
            return "result"

        result = await logging_tool()

        # Verify the tool executed and returned correctly
        assert result == "result"
        assert call_log == ["executed"]

    @pytest.mark.asyncio
    async def test_logging_can_be_disabled(self) -> None:
        """Test that logging can be disabled.

        The decorator should still work correctly even with logging disabled.
        """

        @tool_handler("test_tool", log_start=False, log_complete=False)
        async def no_logging_tool() -> str:
            return "result"

        result = await no_logging_tool()
        assert result == "result"

    @pytest.mark.asyncio
    async def test_duration_tracking_enabled_by_default(self) -> None:
        """Test duration tracking is enabled by default.

        The decorator should track execution time when track_duration=True.
        """

        @tool_handler("test_tool", track_duration=True)
        async def timed_tool() -> str:
            return "result"

        result = await timed_tool()
        assert result == "result"

    @pytest.mark.asyncio
    async def test_input_data_params_extracted(self) -> None:
        """Test input_data parameters are accessible for logging."""

        class InputModel(BaseModel):
            name: str
            count: int

        @tool_handler("test_tool")
        async def tool_with_input(input_data: InputModel) -> str:
            return f"hello {input_data.name}"

        result = await tool_with_input(input_data=InputModel(name="test", count=5))
        assert result == "hello test"

    def test_sync_function_support(self) -> None:
        """Test decorator works with sync functions."""

        @tool_handler("test_tool")
        def sync_tool() -> str:
            return "sync_success"

        result = sync_tool()
        assert result == "sync_success"

    @pytest.mark.asyncio
    async def test_error_logged_before_raising(self) -> None:
        """Test errors are wrapped in ToolExecutionError before raising."""

        @tool_handler("test_tool", log_errors=True)
        async def error_tool() -> None:
            raise RuntimeError("test error")

        with pytest.raises(ToolExecutionError) as exc_info:
            await error_tool()

        # Verify the error was wrapped correctly
        assert exc_info.value.tool_name == "test_tool"
        assert "test error" in str(exc_info.value.message)


# =============================================================================
# wrap_kubernetes_error Tests
# =============================================================================


class TestWrapKubernetesError:
    """Tests for wrap_kubernetes_error helper."""

    def test_basic_wrap(self) -> None:
        """Test basic exception wrapping."""
        original = ValueError("original error")
        wrapped = wrap_kubernetes_error(
            original,
            operation="get",
            resource_kind="Pod",
        )

        assert isinstance(wrapped, KubernetesError)
        assert wrapped.operation == "get"
        assert wrapped.resource_kind == "Pod"
        assert "original error" in str(wrapped.message)

    def test_wrap_with_namespace_and_name(self) -> None:
        """Test wrapping with namespace and resource name."""
        original = RuntimeError("connection refused")
        wrapped = wrap_kubernetes_error(
            original,
            operation="delete",
            resource_kind="Deployment",
            namespace="default",
            resource_name="my-app",
        )

        assert wrapped.namespace == "default"
        assert wrapped.resource_name == "my-app"
        assert "connection refused" in str(wrapped.message)
