"""FastMCP server setup for MOSK MCP Server.

This module initializes and configures the FastMCP server with:
- Proper metadata and versioning
- Transport configuration (STDIO, HTTP)
- Tool registration
- Error handling middleware
- Structured logging integration
- Shared adapter management via ServerContext
"""

from __future__ import annotations

import contextlib
import sys
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from fastmcp import FastMCP
from pydantic import Field

from mosk_mcp.core.config import Settings, TransportType, get_settings, init_settings
from mosk_mcp.core.exceptions import (
    AuthenticationError,
    AuthorizationError,
    MoskMCPError,
    ToolExecutionError,
    ValidationError,
)
from mosk_mcp.core.server_context import ServerContextConfig, SSOServerContext
from mosk_mcp.infrastructure.version_checker import get_cached_version_info
from mosk_mcp.observability.logging import LoggingContext, get_logger, setup_logging
from mosk_mcp.observability.tool_logging_middleware import create_tool_logging_middleware
from mosk_mcp.privacy.middleware import create_privacy_middleware
from mosk_mcp.registration.models import ServerHealthResult, ServerInfo
from mosk_mcp.registration.tools import (
    register_auth_tools,
    register_cluster_tools,
)
from mosk_mcp.registration.tool_groups import (
    ToolGroup,
    register_tool_groups,
    resolve_tool_groups,
    tool_group_registration_summary,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable


logger = get_logger(__name__)


# =============================================================================
# Server Context (Enterprise-ready implementation in server_context.py)
# =============================================================================
# Note: ServerContext is now imported from mosk_mcp.server_context module
# which provides enterprise features:
# - Circuit breaker for connection resilience
# - Automatic reconnection with exponential backoff
# - Health monitoring with background checks
# - Response caching with TTL and LRU eviction
# - Graceful shutdown with proper cleanup
# - Connection metrics and observability
# =============================================================================


# Global server context (set during server creation)
_server_context: SSOServerContext | None = None


def get_server_context() -> SSOServerContext | None:
    """Get the current server context.

    Returns:
        The current SSOServerContext or None if not initialized.
    """
    return _server_context


def create_mcp_server(settings: Settings | None = None) -> FastMCP:
    """Create and configure the FastMCP server with SSO authentication.

    This function creates the MCP server with SSOServerContext that provides:
    - Keycloak SSO authentication (login/logout tools)
    - Dynamic kubeconfig generation from OIDC tokens
    - No static kubeconfig files required
    - Connection pooling with circuit breakers
    - Automatic token refresh
    - Response caching with TTL
    - Graceful shutdown with proper cleanup

    Users must authenticate using the 'login' tool before accessing cluster
    resources. All cluster operations use dynamically generated kubeconfigs
    based on the user's OIDC tokens.

    Args:
        settings: Application settings. If None, uses :func:`get_settings` (requires
            :func:`init_settings` to have been called first).

    Returns:
        Configured FastMCP server instance.
    """
    from contextlib import asynccontextmanager

    global _server_context

    if settings is None:
        settings = get_settings()

    # Initialize logging
    setup_logging(settings)

    logger.info(
        "initializing_server",
        app_name=settings.app_name,
        version=settings.app_version,
        transport=settings.transport.value,
        environment=settings.environment.value,
        mgmt_url=settings.mgmt_url,
    )

    # Create server context configuration from settings
    context_config = ServerContextConfig(
        cache_ttl_seconds=30.0,
        cache_max_entries=1000,
        circuit_breaker_failure_threshold=settings.circuit_breaker_failure_threshold,
        circuit_breaker_recovery_timeout=settings.circuit_breaker_recovery_timeout,
        health_check_interval=settings.connection_health_check_interval,
        enable_health_monitoring=True,
        enable_cache_cleanup=True,
        max_reconnect_attempts=settings.max_retries,
    )

    # Note: SSOServerContext will be initialized asynchronously in lifespan
    # Create a placeholder that will be set during lifespan startup
    _server_context = None

    @asynccontextmanager
    async def lifespan(app: FastMCP) -> AsyncIterator[None]:
        """Lifespan context for initializing SSO server context.

        The SSOServerContext is initialized here with:
        - Keycloak endpoint discovery from MCC URL
        - Cache and health monitoring setup
        - No cluster pre-connection (requires login first)
        """
        global _server_context

        # Initialize SSO server context
        _server_context = await SSOServerContext.create(
            settings,
            config=context_config,
        )

        logger.info(
            "sso_server_context_ready",
            mgmt_url=settings.mgmt_url,
            keycloak_url=settings.keycloak_url,
            health_monitoring=context_config.enable_health_monitoring,
            cache_enabled=context_config.enable_cache_cleanup,
        )

        yield

        # Graceful shutdown with timeout from settings
        if _server_context:
            await _server_context.shutdown(timeout=settings.shutdown_timeout)

    # Create FastMCP instance with metadata and lifespan
    mcp = FastMCP(
        name=settings.app_name,
        instructions=(
            "MCP Server for Mirantis OpenStack for Kubernetes (MOSK) operations. "
            "Provides tools for cluster management, monitoring, and automation. "
            "Use the 'login' tool to authenticate before accessing cluster resources."
        ),
        lifespan=lifespan,
    )

    # Register tools with the shared context getter
    # We pass the get_server_context function instead of the context object itself
    enabled_tool_groups = resolve_tool_groups(settings.tools)
    _register_tools(mcp, settings, get_server_context, enabled_tool_groups)

    # Register tool execution logging middleware
    # This middleware logs all tool calls to stderr for docker logs visibility
    # Must be registered FIRST so it wraps all other middleware
    tool_logging_middleware = create_tool_logging_middleware(settings)
    mcp.add_middleware(tool_logging_middleware)
    logger.info("tool_logging_middleware_registered")

    # Register privacy middleware for data protection
    # This middleware redacts sensitive data (IPs, hostnames, credentials)
    # from tool responses before they're sent to LLM providers
    privacy_middleware = create_privacy_middleware(settings)
    if privacy_middleware is not None:
        mcp.add_middleware(privacy_middleware)
        logger.info(
            "privacy_middleware_registered",
            level=settings.privacy_level,
            enabled=settings.privacy_enabled,
        )

    # FastMCP 3.x: list_tools() is async; tool count is not available in sync context
    logger.info("server_initialized")

    return mcp


def _register_tools(
    mcp: FastMCP,
    settings: Settings,
    context_getter: Callable[[], SSOServerContext | None],
    enabled_tool_groups: frozenset[ToolGroup],
) -> None:
    """Register all tools with the MCP server.

    Args:
        mcp: FastMCP server instance.
        settings: Application settings.
        context_getter: Function that returns the current global SSOServerContext.
        enabled_tool_groups: Optional tool groups enabled via ``MCP_TOOLS``.
    """
    enabled_group_ids = sorted(g.value for g in enabled_tool_groups)

    # Health check tool - always available
    @mcp.tool(
        name="health_check",
        description="Check the health of the MOSK MCP server and its connections",
    )
    async def health_check() -> ServerHealthResult:
        """Perform a health check of the server.

        Returns:
            ServerHealthResult with status and component checks.
        """
        request_id = str(uuid.uuid4())

        async with LoggingContext(request_id=request_id, tool_name="health_check"):
            logger.debug("health_check_started")

            checks: dict[str, dict] = {}

            # Check basic functionality
            checks["server"] = {"status": "healthy", "message": "Server is running"}

            # Check configuration
            checks["config"] = {
                "status": "healthy",
                "auth_enabled": settings.auth_enabled,
                "transport": settings.transport.value,
            }

            # Determine overall status
            all_healthy = all(
                c.get("status") == "healthy" for c in checks.values() if isinstance(c, dict)
            )
            status = "healthy" if all_healthy else "degraded"

            result = ServerHealthResult(
                status=status,
                timestamp=datetime.now(UTC).isoformat(),
                version=settings.app_version,
                checks=checks,
            )

            logger.info("health_check_completed", status=status)

            return result

    # Server info tool
    @mcp.tool(
        name="server_info",
        description="Get information about the MOSK MCP server and its capabilities",
    )
    async def server_info() -> ServerInfo:
        """Get server information and capabilities.

        Returns:
            ServerInfo with server details and available capabilities.
        """
        request_id = str(uuid.uuid4())

        async with LoggingContext(request_id=request_id, tool_name="server_info"):
            logger.debug("server_info_requested")

            capabilities = enabled_group_ids

            # Get MOSK version info if available (populated after login)
            version_info = get_cached_version_info()
            mosk_version = version_info.version_string if version_info else None
            mosk_version_supported = version_info.is_compatible if version_info else None
            warnings = version_info.warnings if version_info else []

            info = ServerInfo(
                name=settings.app_name,
                version=settings.app_version,
                transport=settings.transport.value,
                auth_enabled=settings.auth_enabled,
                capabilities=capabilities,
                mosk_version=mosk_version,
                mosk_version_supported=mosk_version_supported,
                warnings=warnings,
            )

            logger.debug("server_info_returned", mosk_version=mosk_version)
            return info

    # Echo tool for testing
    @mcp.tool(
        name="echo",
        description="Echo back a message - useful for testing connectivity",
    )
    async def echo(message: str = Field(..., description="Message to echo back")) -> str:
        """Echo back a message for testing.

        Args:
            message: The message to echo back.

        Returns:
            The same message with a prefix.
        """
        request_id = str(uuid.uuid4())

        async with LoggingContext(request_id=request_id, tool_name="echo"):
            logger.debug("echo_received", message_length=len(message))
            return f"[MOSK MCP] {message}"

    # =========================================================================
    # Authentication Tools (SSO Login/Logout/Status)
    # =========================================================================

    register_auth_tools(mcp, settings, context_getter)
    register_cluster_tools(mcp, settings, context_getter)

    register_tool_groups(mcp, settings, context_getter, enabled_tool_groups)

    logger.info(
        "tool_groups_configured",
        **tool_group_registration_summary(enabled_tool_groups),
    )


async def run_server(settings: Settings | None = None) -> None:
    """Run the MCP server with the configured transport.

    This function also starts the health check and metrics servers
    when running in HTTP mode or when metrics are enabled.

    Includes graceful shutdown with request draining:
    1. Signal handlers catch SIGTERM/SIGINT
    2. Server stops accepting new requests
    3. Health checks report unhealthy (removed from LB)
    4. Wait for in-flight requests to complete
    5. Clean up resources and exit

    Args:
        settings: Application settings. If None, default :class:`Settings` is built from
            environment and dotenv, then installed via :func:`init_settings` so :func:`get_settings`
            matches the running server.
    """
    import asyncio

    resolved = settings if settings is not None else Settings()
    init_settings(resolved)
    settings = get_settings()

    # Initialize shutdown manager first
    from mosk_mcp.infrastructure.shutdown import GracefulShutdownManager, set_shutdown_manager

    shutdown_manager = GracefulShutdownManager(
        settings=settings,
        shutdown_timeout=settings.shutdown_timeout,
        drain_timeout=settings.drain_timeout,
    )
    set_shutdown_manager(shutdown_manager)

    # Initialize health checker and metrics registry
    from mosk_mcp.observability.health import create_health_app, init_health_checker
    from mosk_mcp.observability.metrics import create_metrics_app, init_metrics_registry

    health_checker = init_health_checker(settings)
    metrics_registry = None

    if settings.metrics_enabled:
        metrics_registry = init_metrics_registry(settings)

    mcp = create_mcp_server(settings)

    # Register shutdown hooks for cleanup
    async def cleanup_server_context() -> None:
        """Clean up server context resources."""
        ctx = get_server_context()
        if ctx:
            await ctx.shutdown()

    shutdown_manager.register_hook(
        name="server_context_cleanup",
        callback=cleanup_server_context,
        priority=50,  # Run early
        timeout=10.0,
    )

    # Install signal handlers for graceful shutdown
    shutdown_manager.install_signal_handlers()

    # Mark initialization complete
    health_checker.mark_initialized()

    logger.info(
        "starting_server",
        transport=settings.transport.value,
        host=settings.http_host if settings.transport != TransportType.STDIO else None,
        port=settings.http_port if settings.transport != TransportType.STDIO else None,
        metrics_enabled=settings.metrics_enabled,
        metrics_port=settings.metrics_port if settings.metrics_enabled else None,
    )

    # Background tasks for auxiliary servers
    auxiliary_tasks: list[asyncio.Task[None]] = []

    try:
        # Start metrics server if enabled (works in all transport modes)
        if settings.metrics_enabled and metrics_registry is not None:
            metrics_app = create_metrics_app(metrics_registry)
            health_app = create_health_app(health_checker)

            # Create combined app with both health and metrics endpoints
            combined_app = _create_combined_auxiliary_app(health_app, metrics_app)

            auxiliary_task = asyncio.create_task(
                _run_auxiliary_server(
                    combined_app,
                    settings.metrics_host,
                    settings.metrics_port,
                    "metrics_and_health",
                )
            )
            auxiliary_tasks.append(auxiliary_task)
            logger.info(
                "auxiliary_server_started",
                host=settings.metrics_host,
                port=settings.metrics_port,
                endpoints=[
                    "/metrics",
                    "/health",
                    "/health/live",
                    "/health/ready",
                    "/health/startup",
                ],
            )

        if settings.transport == TransportType.STDIO:
            # Run with STDIO transport
            await mcp.run_stdio_async()
        elif settings.transport == TransportType.HTTP:
            # Run with HTTP transport
            await mcp.run_http_async(
                host=settings.http_host,
                port=settings.http_port,
            )
        elif settings.transport == TransportType.STREAMABLE_HTTP:
            # Run with streamable HTTP transport
            await mcp.run_http_async(
                host=settings.http_host,
                port=settings.http_port,
            )
        else:
            logger.error("unsupported_transport", transport=settings.transport.value)
            raise ValueError(f"Unsupported transport: {settings.transport}")

    except KeyboardInterrupt:
        logger.info("server_shutdown", reason="keyboard_interrupt")
    except Exception as e:
        logger.error("server_error", error=str(e), error_type=type(e).__name__)
        raise
    finally:
        # Cancel auxiliary tasks
        for task in auxiliary_tasks:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        logger.info("auxiliary_servers_stopped")


async def _run_auxiliary_server(
    app: Any,
    host: str,
    port: int,
    name: str,
) -> None:
    """Run an auxiliary HTTP server (health/metrics).

    Args:
        app: Starlette ASGI application.
        host: Host to bind to.
        port: Port to bind to.
        name: Server name for logging.
    """
    import uvicorn

    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="warning",  # Quiet logs for auxiliary servers
        access_log=False,
    )
    server = uvicorn.Server(config)

    logger.debug(f"{name}_server_starting", host=host, port=port)

    try:
        await server.serve()
    except Exception as e:
        logger.error(f"{name}_server_error", error=str(e), error_type=type(e).__name__)


def _create_combined_auxiliary_app(health_app: Any, metrics_app: Any) -> Any:
    """Create a combined ASGI app for health and metrics endpoints.

    This combines health check and metrics endpoints into a single
    server to minimize resource usage.

    Args:
        health_app: Starlette app for health endpoints.
        metrics_app: Starlette app for metrics endpoints.

    Returns:
        Combined Starlette application.
    """
    from starlette.applications import Starlette
    from starlette.routing import Mount

    # Create routes that delegate to the appropriate app
    routes = [
        Mount("/metrics", app=metrics_app),
        Mount("/health", app=health_app),
        Mount("/", app=health_app),  # Root serves health endpoint
    ]

    return Starlette(routes=routes)


def handle_tool_error(error: Exception, tool_name: str) -> dict[str, Any]:
    """Handle errors from tool execution.

    Converts exceptions to structured error responses suitable
    for MCP error handling.

    Args:
        error: The exception that occurred.
        tool_name: Name of the tool that failed.

    Returns:
        Structured error dictionary.
    """
    logger.error(
        "tool_error",
        tool_name=tool_name,
        error_type=type(error).__name__,
        error_message=str(error),
    )

    if isinstance(error, ValidationError):
        return {
            "error": "validation_error",
            "message": str(error),
            "details": error.details,
        }
    elif isinstance(error, AuthenticationError):
        return {
            "error": "authentication_error",
            "message": str(error),
            "details": error.details,
        }
    elif isinstance(error, AuthorizationError):
        return {
            "error": "authorization_error",
            "message": str(error),
            "details": error.details,
        }
    elif isinstance(error, ToolExecutionError):
        return {
            "error": "tool_execution_error",
            "message": str(error),
            "details": error.details,
        }
    elif isinstance(error, MoskMCPError):
        return {
            "error": error.error_code.lower(),
            "message": str(error),
            "details": error.details,
        }
    else:
        # Generic error handling
        return {
            "error": "internal_error",
            "message": "An unexpected error occurred",
            "details": {"error_type": type(error).__name__},
        }


def main() -> None:
    """Synchronous entry point for the server."""
    import asyncio

    try:
        asyncio.run(run_server())
    except KeyboardInterrupt:
        logger.info("server_shutdown", reason="keyboard_interrupt")
        sys.exit(0)
    except Exception as e:
        logger.error("server_fatal_error", error=str(e), error_type=type(e).__name__)
        sys.exit(1)


if __name__ == "__main__":
    main()
