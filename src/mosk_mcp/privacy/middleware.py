"""Privacy middleware for FastMCP.

This middleware intercepts all tool responses and applies data redaction
to protect sensitive information before it's sent to LLM providers.

Usage:
    from fastmcp import FastMCP
    from mosk_mcp.privacy.middleware import PrivacyMiddleware

    mcp = FastMCP("my-server")
    mcp.add_middleware(PrivacyMiddleware())
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import mcp.types as mt
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools.tool import ToolResult

from mosk_mcp.observability.logging import get_logger
from mosk_mcp.privacy.redactor import (
    DataRedactor,
    PrivacyLevel,
    RedactionConfig,
    is_privacy_enabled,
)


if TYPE_CHECKING:
    from mosk_mcp.core.config import Settings

logger = get_logger(__name__)


class PrivacyMiddleware(Middleware):
    """Middleware that redacts sensitive data from tool responses.

    This middleware intercepts all tool call responses and applies
    configurable data redaction to protect:
    - IP addresses (IPv4, IPv6)
    - MAC addresses
    - Hostnames and FQDNs
    - UUIDs (instance IDs, volume IDs)
    - Usernames in paths
    - Credentials and secrets

    The middleware is designed to protect sensitive infrastructure data
    when using public LLM providers like Claude or OpenAI.

    Example:
        mcp = FastMCP("mosk-mcp")
        mcp.add_middleware(PrivacyMiddleware(level="standard"))

    Attributes:
        redactor: DataRedactor instance for processing responses.
        enabled: Whether privacy protection is active.
    """

    def __init__(
        self,
        level: str | PrivacyLevel | None = None,
        config: RedactionConfig | None = None,
        enabled: bool | None = None,
    ) -> None:
        """Initialize the privacy middleware.

        Args:
            level: Privacy level (none, minimal, standard, aggressive).
            config: Full redaction configuration.
            enabled: Override for enabling/disabling (default: from env).
        """
        super().__init__()

        # Determine if privacy is enabled
        self.enabled = enabled if enabled is not None else is_privacy_enabled()

        # Create redactor based on config
        if config is not None:
            self.redactor = DataRedactor(config)
        elif level is not None:
            if isinstance(level, str):
                level = PrivacyLevel(level)
            self.redactor = DataRedactor(RedactionConfig.from_level(level))
        else:
            # Use environment configuration
            self.redactor = DataRedactor(RedactionConfig.from_env())

        if self.enabled:
            logger.info(
                "privacy_middleware_initialized",
                level=self.redactor.config.level.value,
                redact_ipv4=self.redactor.config.redact_ipv4,
                redact_hostname=self.redactor.config.redact_hostname,
                redact_uuid=self.redactor.config.redact_uuid,
            )
        else:
            logger.warning(
                "privacy_middleware_disabled",
                message="Privacy protection is disabled. Sensitive data will be "
                "exposed to LLM providers. Enable with MCP_PRIVACY_ENABLED=true",
            )

    @classmethod
    def from_settings(cls, settings: Settings) -> PrivacyMiddleware:
        """Create middleware from application settings.

        Args:
            settings: Application settings instance.

        Returns:
            Configured PrivacyMiddleware instance.
        """
        try:
            level = PrivacyLevel(settings.privacy_level)
        except ValueError:
            logger.warning(
                "privacy_invalid_level",
                level=settings.privacy_level,
                fallback="standard",
            )
            level = PrivacyLevel.STANDARD

        config = RedactionConfig.from_level(level)

        # Apply individual settings overrides
        config.redact_uuid = settings.privacy_redact_uuid
        config.preserve_structure = settings.privacy_preserve_structure

        return cls(
            config=config,
            enabled=settings.privacy_enabled,
        )

    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: CallNext[mt.CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        """Intercept tool calls and redact sensitive data from responses.

        This hook is called for every tool invocation. It:
        1. Executes the tool via call_next
        2. Processes the response to redact sensitive data
        3. Returns the sanitized response

        Args:
            context: Middleware context with tool call parameters.
            call_next: Function to call the next handler in the chain.

        Returns:
            ToolResult with redacted sensitive data.
        """
        # Get tool name for logging
        tool_name = context.message.name if hasattr(context.message, "name") else "unknown"

        # Execute the tool
        result = await call_next(context)

        # Skip redaction if disabled
        if not self.enabled:
            return result

        # Skip redaction for certain tools (e.g., login, health_check)
        # These tools don't contain infrastructure data
        skip_tools = {
            "health_check",
            "server_info",
            "echo",
            "login_secure",
            "login_start",
            "login_complete",
            "logout",
        }
        if tool_name in skip_tools:
            return result

        # Reset redactor for each tool call to get fresh placeholders
        self.redactor.reset()

        # Process the result
        try:
            redacted_result = self._redact_tool_result(result)

            # Log redaction stats
            stats = self.redactor.get_stats()
            if stats["total_redactions"] > 0:
                logger.debug(
                    "privacy_redaction_applied",
                    tool=tool_name,
                    total_redactions=stats["total_redactions"],
                    by_type=stats["by_type"],
                )

            return redacted_result

        except Exception as e:
            # Don't fail the tool call if redaction fails
            # Log the error and return original result
            logger.error(
                "privacy_redaction_error",
                tool=tool_name,
                error=str(e),
            )
            return result

    def _redact_tool_result(self, result: ToolResult) -> ToolResult:
        """Redact sensitive data from a ToolResult.

        Args:
            result: Original tool result.

        Returns:
            ToolResult with redacted content.
        """
        # Handle ToolResult objects properly - must return ToolResult
        if isinstance(result, ToolResult):
            # Redact the content list
            redacted_content = self._redact_content_list(result.content)

            # Redact structured_content if present
            redacted_structured = None
            if result.structured_content is not None:
                redacted_structured = self.redactor.redact(result.structured_content)

            # Return a new ToolResult with redacted data
            return ToolResult(
                content=redacted_content,
                structured_content=redacted_structured,
                meta=result.meta,
            )

        # Fallback for other types (should not normally happen)
        if isinstance(result, dict):
            redacted = self.redactor.redact(result)
            return ToolResult(structured_content=redacted)
        elif isinstance(result, str):
            redacted = self.redactor.redact(result)
            return ToolResult(content=redacted)
        elif isinstance(result, list):
            redacted = [self.redactor.redact(item) for item in result]
            return ToolResult(content=redacted)
        elif hasattr(result, "model_dump"):
            data = result.model_dump()
            redacted = self.redactor.redact(data)
            return ToolResult(structured_content=redacted)
        else:
            # Unknown type - try to redact as-is
            redacted = self.redactor.redact(result)
            return ToolResult(content=str(redacted))

    def _redact_content_list(
        self, content: list[mt.TextContent | mt.ImageContent | mt.EmbeddedResource]
    ) -> list[mt.TextContent | mt.ImageContent | mt.EmbeddedResource]:
        """Redact content list from tool results.

        Args:
            content: List of content items.

        Returns:
            Redacted content list.
        """
        redacted = []
        for item in content:
            if isinstance(item, mt.TextContent):
                # Redact text content
                redacted_text = self.redactor.redact(item.text)
                redacted.append(mt.TextContent(type="text", text=redacted_text))
            else:
                # Keep other content types as-is
                redacted.append(item)
        return redacted


def create_privacy_middleware(settings: Settings) -> PrivacyMiddleware | None:
    """Factory function to create privacy middleware if enabled.

    Args:
        settings: Application settings.

    Returns:
        PrivacyMiddleware instance or None if disabled.
    """
    if not settings.privacy_enabled:
        logger.info(
            "privacy_middleware_skipped",
            message="Privacy protection disabled in settings",
        )
        return None

    return PrivacyMiddleware.from_settings(settings)
