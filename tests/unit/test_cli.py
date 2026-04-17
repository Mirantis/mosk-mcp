"""Tests for the mosk-mcp CLI entrypoint."""

import sys
from unittest.mock import patch

import pytest

from mosk_mcp import __version__
from mosk_mcp.cli import CLI_PROG_NAME, _is_version_only_invocation, main


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
