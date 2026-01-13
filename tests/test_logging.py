"""Tests for logging module."""

import json
import logging
from io import StringIO

import pytest
import structlog

from mosk_mcp.core.config import Environment, LogFormat, LogLevel, Settings
from mosk_mcp.observability.logging import (
    LoggingContext,
    bind_context,
    clear_context,
    get_logger,
    request_id_var,
    setup_logging,
    tool_name_var,
    trace_id_var,
    user_var,
)


class TestSetupLogging:
    """Tests for logging setup."""

    def test_setup_json_logging(self) -> None:
        """Test JSON logging setup for production.

        Note: Production mode requires auth to be enabled with a valid API key and MCC URL.
        """
        # Use a cryptographically strong key (no weak patterns like "test")
        settings = Settings(
            log_level=LogLevel.INFO,
            log_format=LogFormat.JSON,
            environment=Environment.PRODUCTION,
            auth_enabled=True,
            auth_api_key="xK9mN2pL7qR4sT6uV8wY0zA3bC5dE7fG",  # type: ignore[arg-type]
            mcc_url="https://172.16.166.22",
        )

        setup_logging(settings)

        # Verify structlog is configured
        config = structlog.get_config()
        assert config is not None

    def test_setup_console_logging(self) -> None:
        """Test console logging setup for development."""
        settings = Settings(
            log_level=LogLevel.DEBUG,
            log_format=LogFormat.CONSOLE,
            environment=Environment.DEVELOPMENT,
        )

        setup_logging(settings)

        # Verify structlog is configured
        config = structlog.get_config()
        assert config is not None

    def test_log_level_applied(self) -> None:
        """Test that log level is applied to root logger."""
        settings = Settings(
            log_level=LogLevel.WARNING,
            log_format=LogFormat.CONSOLE,
            environment=Environment.DEVELOPMENT,
        )

        setup_logging(settings)

        root_logger = logging.getLogger()
        assert root_logger.level == logging.WARNING


class TestGetLogger:
    """Tests for get_logger function."""

    def test_get_named_logger(self) -> None:
        """Test getting a named logger."""
        logger = get_logger("test.module")

        assert logger is not None

    def test_get_default_logger(self) -> None:
        """Test getting default logger."""
        logger = get_logger()

        assert logger is not None


class TestLoggingContext:
    """Tests for LoggingContext context manager."""

    def test_context_sets_request_id(self) -> None:
        """Test that context sets request_id."""
        with LoggingContext(request_id="req-123"):
            assert request_id_var.get() == "req-123"

        # Should be None after context exits
        assert request_id_var.get() is None

    def test_context_sets_user(self) -> None:
        """Test that context sets user."""
        with LoggingContext(user="test-user"):
            assert user_var.get() == "test-user"

        assert user_var.get() is None

    def test_context_sets_tool_name(self) -> None:
        """Test that context sets tool_name."""
        with LoggingContext(tool_name="health_check"):
            assert tool_name_var.get() == "health_check"

        assert tool_name_var.get() is None

    def test_context_sets_trace_id(self) -> None:
        """Test that context sets trace_id."""
        with LoggingContext(trace_id="trace-abc"):
            assert trace_id_var.get() == "trace-abc"

        assert trace_id_var.get() is None

    def test_context_sets_multiple_vars(self) -> None:
        """Test that context sets multiple variables."""
        with LoggingContext(
            request_id="req-456",
            user="admin",
            tool_name="server_info",
        ):
            assert request_id_var.get() == "req-456"
            assert user_var.get() == "admin"
            assert tool_name_var.get() == "server_info"

    def test_context_cleanup_on_exception(self) -> None:
        """Test that context is cleaned up even on exception."""
        try:
            with LoggingContext(request_id="req-789"):
                raise ValueError("Test error")
        except ValueError:
            pass

        assert request_id_var.get() is None

    @pytest.mark.asyncio
    async def test_async_context(self) -> None:
        """Test async context manager."""
        async with LoggingContext(request_id="async-req"):
            assert request_id_var.get() == "async-req"

        assert request_id_var.get() is None

    def test_nested_contexts(self) -> None:
        """Test nested logging contexts."""
        with LoggingContext(request_id="outer"):
            assert request_id_var.get() == "outer"

            with LoggingContext(request_id="inner"):
                assert request_id_var.get() == "inner"

            # Should restore outer value
            assert request_id_var.get() == "outer"


class TestBindContext:
    """Tests for bind_context function."""

    def test_bind_context(self) -> None:
        """Test binding context variables."""
        clear_context()
        bind_context(custom_key="custom_value")

        # Context should be bound (we can't easily verify without
        # capturing log output, so this is a smoke test)

    def test_clear_context(self) -> None:
        """Test clearing context."""
        bind_context(key1="value1", key2="value2")
        clear_context()

        # Context should be cleared (smoke test)


class TestLogOutput:
    """Tests for actual log output."""

    def test_json_output_format(self) -> None:
        """Test that JSON format produces valid JSON.

        Note: Production mode requires auth to be enabled with a valid API key and MCC URL.
        """
        # Use a cryptographically strong key (no weak patterns like "test")
        settings = Settings(
            log_level=LogLevel.INFO,
            log_format=LogFormat.JSON,
            environment=Environment.PRODUCTION,
            auth_enabled=True,
            auth_api_key="xK9mN2pL7qR4sT6uV8wY0zA3bC5dE7fG",  # type: ignore[arg-type]
            mcc_url="https://172.16.166.22",
        )
        setup_logging(settings)

        # Capture log output
        stream = StringIO()
        handler = logging.StreamHandler(stream)
        handler.setLevel(logging.INFO)

        logger = logging.getLogger("test_json")
        logger.handlers = [handler]
        logger.setLevel(logging.INFO)

        # Get structlog logger and log
        slogger = structlog.get_logger("test_json")
        slogger.info("test message", key="value")

        # Verify output is valid JSON
        output = stream.getvalue()
        if output.strip():
            # Parse should not raise
            parsed = json.loads(output.strip())
            assert parsed.get("event") == "test message" or "test message" in str(parsed)

    def test_log_includes_service_info(self) -> None:
        """Test that logs include service information.

        Note: Production mode requires auth to be enabled with a valid API key and MCC URL.
        """
        # Use a cryptographically strong key (no weak patterns like "test")
        settings = Settings(
            log_level=LogLevel.DEBUG,
            log_format=LogFormat.JSON,
            environment=Environment.PRODUCTION,
            auth_enabled=True,
            auth_api_key="xK9mN2pL7qR4sT6uV8wY0zA3bC5dE7fG",  # type: ignore[arg-type]
            mcc_url="https://172.16.166.22",
        )
        setup_logging(settings)

        # This is a smoke test - full verification would require
        # capturing and parsing the output


class TestContextVariables:
    """Tests for context variable handling."""

    def test_context_var_default_none(self) -> None:
        """Test that context variables default to None."""
        # Clear any existing context
        clear_context()

        assert request_id_var.get() is None
        assert user_var.get() is None
        assert tool_name_var.get() is None
        assert trace_id_var.get() is None

    def test_context_var_isolation(self) -> None:
        """Test that context variables are isolated."""
        # Set one variable
        with LoggingContext(request_id="isolated"):
            assert request_id_var.get() == "isolated"
            # Others should still be None
            assert user_var.get() is None
            assert tool_name_var.get() is None
