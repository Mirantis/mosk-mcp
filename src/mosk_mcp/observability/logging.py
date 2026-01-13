"""Structured logging setup for MOSK MCP Server using structlog.

This module configures structlog with:
- JSON output for production (machine-readable)
- Console output for development (human-readable)
- OpenTelemetry-compatible fields (trace_id, span_id)
- Contextual logging support (request_id, user, tool_name)
"""

from __future__ import annotations

import logging
import sys
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any, cast

import structlog

from mosk_mcp.core.config import LogFormat, Settings


if TYPE_CHECKING:
    from structlog.types import EventDict, Processor


# Context variables for request-scoped logging context
request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
user_var: ContextVar[str | None] = ContextVar("user", default=None)
tool_name_var: ContextVar[str | None] = ContextVar("tool_name", default=None)
trace_id_var: ContextVar[str | None] = ContextVar("trace_id", default=None)
span_id_var: ContextVar[str | None] = ContextVar("span_id", default=None)

# Mapping from field names to their ContextVar objects for LoggingContext
_CONTEXT_VAR_MAP: dict[str, ContextVar[str | None]] = {
    "request_id": request_id_var,
    "user": user_var,
    "tool_name": tool_name_var,
    "trace_id": trace_id_var,
    "span_id": span_id_var,
}


def add_context_vars(logger: logging.Logger, method_name: str, event_dict: EventDict) -> EventDict:
    """Add context variables to log events.

    This processor adds request-scoped context to all log entries,
    enabling correlation of logs across a single request/operation.

    Args:
        logger: The logger instance (unused but required by structlog).
        method_name: The logging method name (unused but required by structlog).
        event_dict: The event dictionary to modify.

    Returns:
        Modified event dictionary with context variables added.
    """
    # Add request context
    if (request_id := request_id_var.get()) is not None:
        event_dict["request_id"] = request_id

    if (user := user_var.get()) is not None:
        event_dict["user"] = user

    if (tool_name := tool_name_var.get()) is not None:
        event_dict["tool_name"] = tool_name

    # Add OpenTelemetry trace context if available
    if (trace_id := trace_id_var.get()) is not None:
        event_dict["trace_id"] = trace_id

    if (span_id := span_id_var.get()) is not None:
        event_dict["span_id"] = span_id

    return event_dict


def add_service_info(logger: logging.Logger, method_name: str, event_dict: EventDict) -> EventDict:
    """Add service identification to log events.

    Args:
        logger: The logger instance (unused but required by structlog).
        method_name: The logging method name (unused but required by structlog).
        event_dict: The event dictionary to modify.

    Returns:
        Modified event dictionary with service info added.
    """
    event_dict["service"] = "mosk-mcp"
    return event_dict


def setup_logging(settings: Settings, use_stderr: bool = True) -> None:
    """Configure structlog based on application settings.

    Sets up structlog with appropriate processors for either
    JSON (production) or console (development) output.

    Args:
        settings: Application settings containing log configuration.
        use_stderr: If True, logs to stderr instead of stdout. This is
            required for MCP servers using STDIO transport, as stdout
            is reserved for JSON-RPC communication. Defaults to True.
    """
    # Determine if we're in development mode
    is_dev = settings.log_format == LogFormat.CONSOLE

    # Common processors for all environments
    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
        add_service_info,
        add_context_vars,
    ]

    if is_dev:
        # Development: pretty console output
        processors: list[Processor] = [
            *shared_processors,
            structlog.processors.ExceptionPrettyPrinter(),
            structlog.dev.ConsoleRenderer(
                colors=True,
                exception_formatter=structlog.dev.plain_traceback,
            ),
        ]
    else:
        # Production: JSON output for log aggregation
        processors = [
            *shared_processors,
            structlog.processors.dict_tracebacks,
            structlog.processors.JSONRenderer(),
        ]

    # Configure structlog
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Configure standard library logging
    log_level = getattr(logging, settings.log_level.value)

    # Root logger configuration
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # Remove existing handlers
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Add new handler - use stderr for MCP STDIO transport compatibility
    # stdout is reserved for JSON-RPC communication in STDIO mode
    output_stream = sys.stderr if use_stderr else sys.stdout
    handler = logging.StreamHandler(output_stream)
    handler.setLevel(log_level)

    if is_dev:
        # Simple format for development (structlog handles the formatting)
        handler.setFormatter(logging.Formatter("%(message)s"))
    else:
        # JSON format for production
        handler.setFormatter(logging.Formatter("%(message)s"))

    root_logger.addHandler(handler)

    # Quiet noisy loggers
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Get a structured logger instance.

    Args:
        name: Optional logger name. If not provided, uses the calling module.

    Returns:
        A bound structlog logger instance.
    """
    return cast("structlog.stdlib.BoundLogger", structlog.get_logger(name))


class LoggingContext:
    """Context manager for setting request-scoped logging context.

    Usage:
        async with LoggingContext(request_id="123", user="admin"):
            logger.info("Processing request")
            # All logs within this block will include request_id and user
    """

    def __init__(
        self,
        request_id: str | None = None,
        user: str | None = None,
        tool_name: str | None = None,
        trace_id: str | None = None,
        span_id: str | None = None,
    ) -> None:
        """Initialize logging context.

        Args:
            request_id: Unique request identifier.
            user: User identifier.
            tool_name: Name of the tool being executed.
            trace_id: OpenTelemetry trace ID.
            span_id: OpenTelemetry span ID.
        """
        # Store values as a dict for iteration in __enter__/__exit__
        self._values: dict[str, str | None] = {
            "request_id": request_id,
            "user": user,
            "tool_name": tool_name,
            "trace_id": trace_id,
            "span_id": span_id,
        }
        self._tokens: dict[str, Any] = {}

    def __enter__(self) -> LoggingContext:
        """Enter the context and set context variables."""
        for name, value in self._values.items():
            if value is not None:
                self._tokens[name] = _CONTEXT_VAR_MAP[name].set(value)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        """Exit the context and reset context variables."""
        for name, token in self._tokens.items():
            _CONTEXT_VAR_MAP[name].reset(token)

    async def __aenter__(self) -> LoggingContext:
        """Async enter the context."""
        return self.__enter__()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        """Async exit the context."""
        self.__exit__(exc_type, exc_val, exc_tb)


def bind_context(**kwargs: Any) -> None:
    """Bind additional context to the current structlog context.

    This is a convenience wrapper around structlog.contextvars.bind_contextvars.

    Args:
        **kwargs: Key-value pairs to bind to the logging context.
    """
    structlog.contextvars.bind_contextvars(**kwargs)


def clear_context() -> None:
    """Clear all bound context from structlog.

    This should be called at the start of each request to ensure
    clean context.
    """
    structlog.contextvars.clear_contextvars()
