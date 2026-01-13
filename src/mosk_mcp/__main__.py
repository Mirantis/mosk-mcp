"""Entry point for MOSK MCP Server.

This module provides the main entry point for running the MCP server.
It can be invoked directly with `python -m mosk_mcp` or through the
`mosk-mcp` console script defined in pyproject.toml.

Usage:
    # Using the module directly
    python -m mosk_mcp

    # Using the console script (after installation)
    mosk-mcp

    # With environment variables
    MCP_TRANSPORT=http MCP_HTTP_PORT=8080 mosk-mcp

    # With .env file
    # Create .env file with configuration, then run:
    mosk-mcp
"""

from __future__ import annotations

import argparse
import sys

from mosk_mcp.core.config import LogFormat, LogLevel, Settings, TransportType, get_settings
from mosk_mcp.core.server import run_server
from mosk_mcp.observability.logging import get_logger, setup_logging


def parse_args() -> argparse.Namespace:
    """Parse command line arguments.

    Returns:
        Parsed arguments namespace.

    """
    parser = argparse.ArgumentParser(
        prog="mosk-mcp",
        description="MCP Server for Mirantis OpenStack for Kubernetes (MOSK) operations",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Environment Variables:
  MCP_TRANSPORT       Transport type: stdio, http, streamable-http (default: stdio)
  MCP_HTTP_HOST       HTTP server host (default: 0.0.0.0)
  MCP_HTTP_PORT       HTTP server port (default: 8080)
  MCP_LOG_LEVEL       Log level: DEBUG, INFO, WARNING, ERROR (default: INFO)
  MCP_LOG_FORMAT      Log format: json, console (default: json)
  MCP_AUTH_ENABLED    Enable authentication (default: true)
  MCP_METRICS_ENABLED Enable Prometheus metrics (default: true)
  MCP_METRICS_HOST    Metrics server host (default: 0.0.0.0)
  MCP_METRICS_PORT    Metrics/health server port (default: 9090)

Examples:
  # Run with STDIO transport (for Claude Desktop)
  mosk-mcp

  # Run with HTTP transport
  MCP_TRANSPORT=http mosk-mcp

  # Run in development mode with debug logging
  MCP_LOG_FORMAT=console MCP_LOG_LEVEL=DEBUG mosk-mcp

  # Disable metrics for local development
  MCP_METRICS_ENABLED=false mosk-mcp
""",
    )

    parser.add_argument(
        "--version",
        action="version",
        version="%(prog)s 0.1.0",
    )

    parser.add_argument(
        "--transport",
        type=str,
        choices=["stdio", "http", "streamable-http"],
        help="Transport type (overrides MCP_TRANSPORT env var)",
    )

    parser.add_argument(
        "--host",
        type=str,
        help="HTTP server host (overrides MCP_HTTP_HOST env var)",
    )

    parser.add_argument(
        "--port",
        type=int,
        help="HTTP server port (overrides MCP_HTTP_PORT env var)",
    )

    parser.add_argument(
        "--log-level",
        type=str,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Log level (overrides MCP_LOG_LEVEL env var)",
    )

    parser.add_argument(
        "--log-format",
        type=str,
        choices=["json", "console"],
        help="Log format (overrides MCP_LOG_FORMAT env var)",
    )

    parser.add_argument(
        "--no-auth",
        action="store_true",
        help="Disable authentication (for development only)",
    )

    parser.add_argument(
        "--metrics-port",
        type=int,
        help="Metrics/health server port (overrides MCP_METRICS_PORT env var)",
    )

    parser.add_argument(
        "--no-metrics",
        action="store_true",
        help="Disable Prometheus metrics and health endpoints",
    )

    return parser.parse_args()


def apply_cli_overrides(settings: Settings, args: argparse.Namespace) -> Settings:
    """Apply command line argument overrides to settings.

    Args:
        settings: Base settings from environment.
        args: Parsed command line arguments.

    Returns:
        Settings with CLI overrides applied.

    """
    # Create a dict of overrides from CLI args
    overrides: dict[str, TransportType | str | int | LogLevel | LogFormat | bool] = {}

    if args.transport:
        overrides["transport"] = TransportType(args.transport)
    if args.host:
        overrides["http_host"] = args.host
    if args.port:
        overrides["http_port"] = args.port
    if args.log_level:
        overrides["log_level"] = LogLevel(args.log_level)
    if args.log_format:
        overrides["log_format"] = LogFormat(args.log_format)
    if args.no_auth:
        overrides["auth_enabled"] = False
    if args.metrics_port:
        overrides["metrics_port"] = args.metrics_port
    if args.no_metrics:
        overrides["metrics_enabled"] = False

    # If no overrides, return original settings
    if not overrides:
        return settings

    # Create new settings with overrides
    # We need to copy all values and apply overrides
    settings_dict = {
        "app_name": settings.app_name,
        "app_version": settings.app_version,
        "transport": overrides.get("transport", settings.transport),
        "http_host": overrides.get("http_host", settings.http_host),
        "http_port": overrides.get("http_port", settings.http_port),
        "log_level": overrides.get("log_level", settings.log_level),
        "log_format": overrides.get("log_format", settings.log_format),
        "auth_enabled": overrides.get("auth_enabled", settings.auth_enabled),
        # SSO authentication via MCC URL
        "mcc_url": settings.mcc_url,
        "kubernetes_namespace": settings.kubernetes_namespace,
        # MOSK cluster identification
        "mosk_cluster_name": settings.mosk_cluster_name,
        "mosk_cluster_namespace": settings.mosk_cluster_namespace,
        "audit_log_path": settings.audit_log_path,
        "audit_enabled": settings.audit_enabled,
        "audit_rotation_enabled": settings.audit_rotation_enabled,
        "audit_max_size_mb": settings.audit_max_size_mb,
        "audit_backup_count": settings.audit_backup_count,
        "audit_rotation_when": settings.audit_rotation_when,
        "request_timeout": settings.request_timeout,
        "max_retries": settings.max_retries,
        "otel_enabled": settings.otel_enabled,
        "otel_service_name": settings.otel_service_name,
        "otel_exporter_endpoint": settings.otel_exporter_endpoint,
        "metrics_enabled": overrides.get("metrics_enabled", settings.metrics_enabled),
        "metrics_port": overrides.get("metrics_port", settings.metrics_port),
        "metrics_host": settings.metrics_host,
        "health_check_timeout_seconds": settings.health_check_timeout_seconds,
        "health_check_k8s_enabled": settings.health_check_k8s_enabled,
        # Rate limiting settings
        "rate_limit_enabled": settings.rate_limit_enabled,
        "rate_limit_requests_per_minute": settings.rate_limit_requests_per_minute,
        "rate_limit_burst_size": settings.rate_limit_burst_size,
        # Graceful shutdown settings
        "shutdown_timeout": settings.shutdown_timeout,
        "drain_timeout": settings.drain_timeout,
        # Connection pool settings
        "connection_pool_size": settings.connection_pool_size,
        "connection_pool_timeout": settings.connection_pool_timeout,
        "connection_health_check_interval": settings.connection_health_check_interval,
        # Circuit breaker settings
        "circuit_breaker_failure_threshold": settings.circuit_breaker_failure_threshold,
        "circuit_breaker_recovery_timeout": settings.circuit_breaker_recovery_timeout,
        # Environment
        "environment": settings.environment,
    }

    return Settings.model_validate(settings_dict)


def main() -> None:
    """Run the MOSK MCP server.

    Parse arguments, configure the server, and run it.
    """
    import asyncio

    args = parse_args()

    # Load settings from environment
    settings = get_settings()

    # Apply CLI overrides
    settings = apply_cli_overrides(settings, args)

    # Initialize logging early
    setup_logging(settings)
    logger = get_logger(__name__)

    logger.info(
        "starting_mosk_mcp",
        version=settings.app_version,
        transport=settings.transport.value,
        log_level=settings.log_level.value,
        auth_enabled=settings.auth_enabled,
        metrics_enabled=settings.metrics_enabled,
        metrics_port=settings.metrics_port if settings.metrics_enabled else None,
    )

    # Run the server
    try:
        asyncio.run(run_server(settings))
    except KeyboardInterrupt:
        logger.info("server_shutdown", reason="keyboard_interrupt")
        sys.exit(0)
    except Exception as e:
        logger.error(
            "server_fatal_error",
            error=str(e),
            error_type=type(e).__name__,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
