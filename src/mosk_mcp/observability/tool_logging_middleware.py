"""Tool execution logging middleware for FastMCP.

This middleware intercepts all tool calls and logs them to stderr,
ensuring visibility via `docker logs` in containerized deployments.

The middleware logs:
- Tool invocation (name, parameters, user context)
- Tool completion (result status, duration)
- Tool errors (exception details)

All logs go to structlog which outputs to stderr, making them visible
in `docker logs` output.

Usage:
    from fastmcp import FastMCP
    from mosk_mcp.observability.tool_logging_middleware import ToolExecutionLoggingMiddleware

    mcp = FastMCP("my-server")
    mcp.add_middleware(ToolExecutionLoggingMiddleware())
"""

from __future__ import annotations

import time
import uuid
from typing import TYPE_CHECKING, Any

from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext

from mosk_mcp.observability.logging import LoggingContext, get_logger


if TYPE_CHECKING:
    import mcp.types as mt
    from fastmcp.tools.tool import ToolResult

    from mosk_mcp.core.config import Settings


logger = get_logger(__name__)


# Tools that should not have their parameters logged (may contain sensitive data)
SENSITIVE_PARAM_TOOLS = {
    "login",
    "login_secure",
    "login_start",
    "login_complete",
}

# Tools that should not be logged at all (too noisy or internal)
SKIP_LOGGING_TOOLS: set[str] = set()  # Currently none, but can be configured


class ToolExecutionLoggingMiddleware(Middleware):
    """Middleware that logs all tool executions to stderr.

    This middleware provides comprehensive logging of tool execution,
    making all tool calls visible via `docker logs` in containerized
    deployments.

    Logs include:
    - Tool name and sanitized parameters
    - Request ID for correlation
    - User context (if authenticated)
    - Execution duration
    - Success/failure status
    - Error details on failure

    The logging is automatic and requires no changes to individual tools.

    Example:
        mcp = FastMCP("mosk-mcp")
        mcp.add_middleware(ToolExecutionLoggingMiddleware())

    Attributes:
        enabled: Whether logging is enabled.
        log_parameters: Whether to log tool parameters.
        log_results: Whether to log result summaries.
    """

    def __init__(
        self,
        enabled: bool = True,
        log_parameters: bool = True,
        log_results: bool = True,
        max_param_length: int = 500,
    ) -> None:
        """Initialize the tool execution logging middleware.

        Args:
            enabled: Whether logging is enabled (default: True).
            log_parameters: Whether to log tool parameters (default: True).
            log_results: Whether to log result summaries (default: True).
            max_param_length: Maximum length for parameter values in logs (default: 500).
        """
        super().__init__()
        self.enabled = enabled
        self.log_parameters = log_parameters
        self.log_results = log_results
        self.max_param_length = max_param_length

        if self.enabled:
            logger.info(
                "tool_logging_middleware_initialized",
                log_parameters=log_parameters,
                log_results=log_results,
            )

    @classmethod
    def from_settings(cls, _settings: Settings) -> ToolExecutionLoggingMiddleware:
        """Create middleware from application settings.

        Args:
            settings: Application settings instance.

        Returns:
            Configured ToolExecutionLoggingMiddleware instance.
        """
        return cls(
            enabled=True,  # Always enabled - this is the whole point
            log_parameters=True,
            log_results=True,
        )

    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: CallNext[mt.CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        """Intercept tool calls and log execution details.

        This hook is called for every tool invocation. It:
        1. Logs tool invocation with parameters
        2. Executes the tool via call_next
        3. Logs completion with result status and duration
        4. Handles and logs any errors

        Args:
            context: Middleware context with tool call parameters.
            call_next: Function to call the next handler in the chain.

        Returns:
            ToolResult from the tool execution.
        """
        # Get tool name
        tool_name = context.message.name if hasattr(context.message, "name") else "unknown"

        # Skip logging for certain tools
        if tool_name in SKIP_LOGGING_TOOLS or not self.enabled:
            return await call_next(context)

        # Generate request ID for correlation
        request_id = str(uuid.uuid4())[:8]
        start_time = time.monotonic()

        # Get parameters (sanitize sensitive tools)
        params = self._get_sanitized_params(context, tool_name)

        # Log tool invocation
        logger.info(
            "tool_call_start",
            tool=tool_name,
            request_id=request_id,
            params=params if self.log_parameters else None,
        )

        try:
            # Execute the tool with logging context
            async with LoggingContext(request_id=request_id, tool_name=tool_name):
                result = await call_next(context)

            # Calculate duration
            duration_ms = (time.monotonic() - start_time) * 1000

            # Log successful completion
            result_summary = self._get_result_summary(result) if self.log_results else None

            logger.info(
                "tool_call_success",
                tool=tool_name,
                request_id=request_id,
                duration_ms=round(duration_ms, 2),
                result_summary=result_summary,
            )

            return result

        except Exception as e:
            # Calculate duration
            duration_ms = (time.monotonic() - start_time) * 1000

            # Log error
            logger.error(
                "tool_call_error",
                tool=tool_name,
                request_id=request_id,
                duration_ms=round(duration_ms, 2),
                error_type=type(e).__name__,
                error_message=str(e),
            )

            # Re-raise the exception
            raise

    def _get_sanitized_params(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        tool_name: str,
    ) -> dict[str, Any] | None:
        """Get sanitized parameters for logging.

        Sensitive parameters (passwords, tokens) are redacted.
        Large values are truncated.

        Args:
            context: Middleware context with tool parameters.
            tool_name: Name of the tool being called.

        Returns:
            Sanitized parameter dictionary or None.
        """
        try:
            # Get arguments from context
            if not hasattr(context.message, "arguments"):
                return None

            arguments = context.message.arguments
            if arguments is None:
                return None

            # For sensitive tools, don't log parameters at all
            if tool_name in SENSITIVE_PARAM_TOOLS:
                return {"[REDACTED]": "sensitive tool parameters"}

            # Sanitize parameters
            sanitized = {}
            for key, value in arguments.items():
                # Redact sensitive parameter names
                if any(
                    s in key.lower() for s in ["password", "token", "secret", "key", "credential"]
                ):
                    sanitized[key] = "[REDACTED]"
                elif isinstance(value, str) and len(value) > self.max_param_length:
                    # Truncate long strings
                    sanitized[key] = value[: self.max_param_length] + "..."
                elif isinstance(value, (dict, list)):
                    # Summarize complex types
                    if isinstance(value, dict):
                        sanitized[key] = f"<dict with {len(value)} keys>"
                    else:
                        sanitized[key] = f"<list with {len(value)} items>"
                else:
                    sanitized[key] = value

            return sanitized

        except Exception as e:
            logger.debug(
                "argument_sanitization_failed",
                error=str(e),
                error_type=type(e).__name__,
            )
            return None

    def _get_result_summary(self, result: ToolResult) -> dict[str, Any] | None:
        """Get a summary of the tool result for logging.

        Args:
            result: The tool result to summarize.

        Returns:
            Summary dictionary or None.
        """
        try:
            summary: dict[str, Any] = {}

            # Check for structured content
            if hasattr(result, "structured_content") and result.structured_content:
                structured = result.structured_content
                if isinstance(structured, dict):
                    summary["type"] = "structured"
                    summary["keys"] = list(structured.keys())[:10]  # First 10 keys
                    if "error" in structured:
                        summary["has_error"] = True
                elif isinstance(structured, list):
                    summary["type"] = "list"
                    summary["count"] = len(structured)
                else:
                    summary["type"] = type(structured).__name__

            # Check content list
            if hasattr(result, "content") and result.content:
                content_count = len(result.content)
                summary["content_items"] = content_count
                # Get first text content preview
                for item in result.content[:1]:
                    if hasattr(item, "text"):
                        text = item.text[:100] if len(item.text) > 100 else item.text
                        summary["text_preview"] = text

            return summary if summary else {"type": "empty"}

        except Exception as e:
            logger.debug(
                "result_summary_failed",
                error=str(e),
                error_type=type(e).__name__,
            )
            return {"type": "unknown"}


def create_tool_logging_middleware(
    settings: Settings | None = None,
) -> ToolExecutionLoggingMiddleware:
    """Factory function to create tool logging middleware.

    Args:
        settings: Optional application settings.

    Returns:
        ToolExecutionLoggingMiddleware instance.
    """
    if settings is not None:
        return ToolExecutionLoggingMiddleware.from_settings(settings)
    return ToolExecutionLoggingMiddleware()
