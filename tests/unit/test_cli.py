"""Tests for the mosk-mcp CLI entrypoint."""

import sys
from unittest.mock import patch

import pytest

from mosk_mcp import __version__
from mosk_mcp.cli import (
    CLI_ALLOWED_FIELDS,
    CLI_PROG_NAME,
    MoskMcpCliSettings,
    _is_version_only_invocation,
    main,
)
from mosk_mcp.core.config import (
    Settings,
    TransportType,
    _resolve_dotenv_path,
    get_settings,
    init_settings,
)


def test_version_flags_detected() -> None:
    assert _is_version_only_invocation(["mosk-mcp", "--version"]) is True
    assert _is_version_only_invocation(["mosk-mcp", "-V"]) is True
    assert _is_version_only_invocation(["mosk-mcp"]) is False
    assert _is_version_only_invocation(["mosk-mcp", "--help"]) is False


def test_main_version_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    with patch.object(sys, "argv", [CLI_PROG_NAME, "--version"]):
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == f"{CLI_PROG_NAME} {__version__}"


def _parse_cli(*args: str) -> MoskMcpCliSettings:
    """Build settings the same way :func:`CliApp.run` does (CLI + env + dotenv)."""
    return MoskMcpCliSettings(
        _cli_parse_args=list(args),
        _env_file=_resolve_dotenv_path(),
    )


def test_cli_parsed_settings_match_get_settings_after_init() -> None:
    """MoskMcpCliSettings from flags is the same object as get_settings after init_settings."""
    cli = _parse_cli("--transport", "http", "--port", "3000", "--host", "10.0.0.1", "--no-auth")
    init_settings(cli)

    assert get_settings() is cli
    assert get_settings().transport is TransportType.HTTP
    assert get_settings().http_port == 3000
    assert get_settings().http_host == "10.0.0.1"
    assert get_settings().auth_enabled is False


def test_cli_port_overrides_mcp_http_port_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """CLI ``--port`` wins over ``MCP_HTTP_PORT`` for the registered settings object."""
    monkeypatch.setenv("MCP_HTTP_PORT", "1111")
    cli = _parse_cli("--port", "2222")
    init_settings(cli)

    assert get_settings() is cli
    assert get_settings().http_port == 2222


def test_non_cli_setting_flag_is_not_exposed() -> None:
    """Non-whitelisted settings should not be available as CLI flags."""
    with pytest.raises(SystemExit):
        _parse_cli("--mcc-url", "https://example.com")


def test_cli_allowed_fields_align_with_settings_model() -> None:
    """Whitelist should only contain valid top-level Settings fields."""
    unknown = CLI_ALLOWED_FIELDS.difference(Settings.model_fields.keys())
    assert unknown == set()
