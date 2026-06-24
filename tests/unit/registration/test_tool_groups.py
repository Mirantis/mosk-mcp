"""Tests for optional MCP tool group configuration."""

import pytest

from mosk_mcp.core.config import Settings
from mosk_mcp.registration.tool_groups import (
    ALL_TOOL_GROUPS,
    ToolGroup,
    resolve_tool_groups,
    tool_group_registration_summary,
)


class TestResolveToolGroups:
    """Tests for resolve_tool_groups."""

    def test_none_enables_all_groups(self) -> None:
        assert resolve_tool_groups(None) == ALL_TOOL_GROUPS

    def test_empty_string_enables_all_groups(self) -> None:
        assert resolve_tool_groups("") == ALL_TOOL_GROUPS

    def test_whitespace_only_enables_all_groups(self) -> None:
        assert resolve_tool_groups("  ,  , ") == ALL_TOOL_GROUPS

    def test_subset_parses_and_normalizes(self) -> None:
        groups = resolve_tool_groups(" Templates , CEPH ")
        assert groups == frozenset({ToolGroup.TEMPLATES, ToolGroup.CEPH})

    def test_unknown_group_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown tool group"):
            resolve_tool_groups("foo")

    def test_auth_is_unknown_group(self) -> None:
        with pytest.raises(ValueError, match="auth"):
            resolve_tool_groups("auth")

    def test_long_alias_rejected(self) -> None:
        with pytest.raises(ValueError, match="template_generation"):
            resolve_tool_groups("template_generation")


class TestToolGroupRegistrationSummary:
    """Tests for startup logging summary helper."""

    def test_all_enabled_has_empty_disabled(self) -> None:
        summary = tool_group_registration_summary(ALL_TOOL_GROUPS)
        assert summary["disabled_groups"] == []
        assert len(summary["enabled_groups"]) == 8

    def test_partial_config_lists_disabled(self) -> None:
        enabled = frozenset({ToolGroup.TEMPLATES, ToolGroup.CEPH})
        summary = tool_group_registration_summary(enabled)
        assert summary["enabled_groups"] == ["ceph", "templates"]
        assert "rabbitmq" in summary["disabled_groups"]
        assert len(summary["disabled_groups"]) == 6


class TestSettingsToolsValidation:
    """Tests for MCP_TOOLS settings validation."""

    def test_settings_rejects_unknown_tools(self) -> None:
        with pytest.raises(ValueError, match="Unknown tool group"):
            Settings(
                auth_enabled=False,
                otel_enabled=False,
                tools="not-a-group",
            )

    def test_settings_accepts_valid_tools(self) -> None:
        settings = Settings(
            auth_enabled=False,
            otel_enabled=False,
            tools="templates,ceph",
        )
        assert settings.tools == "templates,ceph"
