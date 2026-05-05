"""CLI for the mosk-mcp console script and ``python -m mosk_mcp``.

Environment variables, ``.env``, and flags are merged via :class:`MoskMcpCliSettings`
and :class:`pydantic_settings.CliApp` (see :func:`main`).
"""

from __future__ import annotations

import sys
from typing import Any

from pydantic import BaseModel
from pydantic.fields import FieldInfo
from pydantic_settings import CliApp, CliSettingsSource, SettingsConfigDict

from mosk_mcp._version import __version__
from mosk_mcp.core.config import Settings, _resolve_dotenv_path
from mosk_mcp.core.server import run_server
from mosk_mcp.observability.logging import get_logger, setup_logging

CLI_PROG_NAME = "mosk-mcp"
_VERSION_FLAGS = frozenset(("--version", "-V"))
CLI_ALLOWED_FIELDS = frozenset(
    {
        "transport",
        "http_host",
        "http_port",
        "log_level",
        "log_format",
        "auth_enabled",
        "metrics_enabled",
        "metrics_host",
        "metrics_port",
        "config_path",
        "profile",
    }
)


class WhitelistedCliSettingsSource(CliSettingsSource[Any]):
    """CLI source that only exposes fields listed in ``CLI_ALLOWED_FIELDS``."""

    def _sort_arg_fields(self, model: type[BaseModel]) -> list[tuple[str, FieldInfo]]:
        fields = super()._sort_arg_fields(model)
        if model is self.settings_cls:
            return [(name, info) for name, info in fields if name in CLI_ALLOWED_FIELDS]
        return fields


class MoskMcpCliSettings(Settings):
    """MCP Server for Mirantis OpenStack for Kubernetes (MOSK) operations.

    Environment Variables:
      DOTENV_PATH         Path to .env file for MCP_* settings (default: .env; not MCP_-prefixed)
      MCP_TRANSPORT       Transport type: stdio, http, streamable-http (default: stdio)
      MCP_HTTP_HOST       HTTP server host (default: 0.0.0.0)
      MCP_HTTP_PORT       HTTP server port (default: 8080)
      MCP_LOG_LEVEL       Log level: DEBUG, INFO, WARNING, ERROR, CRITICAL (default: INFO)
      MCP_LOG_FORMAT      Log format: json, console (default: json)
      MCP_AUTH_ENABLED    Enable authentication (default: true)
      MCP_METRICS_ENABLED Enable Prometheus metrics (default: true)
      MCP_METRICS_HOST    Metrics server host (default: 0.0.0.0)
      MCP_METRICS_PORT    Metrics/health server port (default: 9090)
      MCP_CONFIG_PATH     Path to clusters.yaml (default: ~/.config/mosk-mcp/clusters.yaml)
      MCP_PROFILE         Active cluster profile name (must exist under clusters: in clusters.yaml)

    Examples:
      # Run with STDIO transport (for Claude Desktop)
      mosk-mcp

      # Load MCP_* settings from a specific dotenv file
      DOTENV_PATH=/path/to/config.env mosk-mcp

      # Run with HTTP transport
      MCP_TRANSPORT=http mosk-mcp

      # Run in development mode with debug logging
      MCP_LOG_FORMAT=console MCP_LOG_LEVEL=DEBUG mosk-mcp

      # Disable metrics for local development
      MCP_METRICS_ENABLED=false mosk-mcp
    """

    model_config = SettingsConfigDict(
        **{
            **dict(Settings.model_config),
            "cli_prog_name": CLI_PROG_NAME,
            "cli_kebab_case": True,
            "cli_implicit_flags": False,
            "cli_shortcuts": {
                "http-host": "host",
                "http-port": "port",
                "auth-enabled": "auth",
                "metrics-enabled": "metrics",
            },
        },
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[Settings],
        init_settings: Any,
        env_settings: Any,
        dotenv_settings: Any,
        file_secret_settings: Any,
    ) -> tuple[Any, ...]:
        """Inject a CLI source that only registers whitelisted top-level fields."""
        cli_settings = WhitelistedCliSettingsSource(settings_cls)
        return (
            cli_settings,
            init_settings,
            env_settings,
            dotenv_settings,
            file_secret_settings,
        )

    async def cli_cmd(self) -> None:
        """Run the MOSK MCP server (parsed settings are ``self``)."""
        setup_logging(self)
        logger = get_logger(__name__)

        logger.info(
            "starting_mosk_mcp",
            version=self.app_version,
            transport=self.transport.value,
            log_level=self.log_level.value,
            auth_enabled=self.auth_enabled,
            metrics_enabled=self.metrics_enabled,
            metrics_port=self.metrics_port if self.metrics_enabled else None,
        )

        try:
            await run_server(self)
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


def _is_version_only_invocation(argv: list[str]) -> bool:
    """True if the user passed ``--version`` or ``-V`` as the first argument."""
    return len(argv) >= 2 and argv[1] in _VERSION_FLAGS


def _print_version() -> None:
    """Print program name and version (from :mod:`mosk_mcp._version`)."""
    print(f"{CLI_PROG_NAME} {__version__}")


def main() -> None:
    """Run the MOSK MCP server via :class:`CliApp` (CLI + env + optional ``.env``)."""
    if _is_version_only_invocation(sys.argv):
        _print_version()
        raise SystemExit(0)

    try:
        # CliApp.run() must receive _env_file explicitly: otherwise pydantic-settings can treat the
        # default as "no dotenv" and skip model_config env_file (see pydantic-settings #795 / #796).
        CliApp.run(
            MoskMcpCliSettings,
            cli_args=None,
            _env_file=_resolve_dotenv_path(),
        )
    except KeyboardInterrupt:
        # Help / parse paths can still raise in edge cases; normal shutdown is handled in cli_cmd.
        sys.exit(130)
