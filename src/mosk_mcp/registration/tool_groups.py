"""Optional MCP tool group configuration and registration.

Auth and cluster tools are registered unconditionally in the server; this module
covers the eight optional groups controlled by ``MCP_TOOLS``.
"""

from __future__ import annotations

from collections.abc import Callable
from enum import Enum
from typing import TYPE_CHECKING

from fastmcp import FastMCP

from mosk_mcp.registration.tools.ceph_operations import register_ceph_operations_tools
from mosk_mcp.registration.tools.cluster_health import register_cluster_health_tools
from mosk_mcp.registration.tools.messaging_operations import register_messaging_operations_tools
from mosk_mcp.registration.tools.node_lifecycle import register_node_lifecycle_tools
from mosk_mcp.registration.tools.operations_visibility import register_operations_visibility_tools
from mosk_mcp.registration.tools.template_generation import register_template_generation_tools
from mosk_mcp.registration.tools.troubleshooting import register_troubleshooting_tools
from mosk_mcp.registration.tools.validation import register_validation_tools


if TYPE_CHECKING:
    from mosk_mcp.core.config import Settings
    from mosk_mcp.core.server_context import SSOServerContext


class ToolGroup(str, Enum):
    """Optional tool groups selectable via ``MCP_TOOLS``."""

    TEMPLATES = "templates"
    CEPH = "ceph"
    RABBITMQ = "rabbitmq"
    NODES = "nodes"
    VISIBILITY = "visibility"
    HEALTH = "health"
    TROUBLESHOOTING = "troubleshooting"
    VALIDATION = "validation"


ALL_TOOL_GROUPS: frozenset[ToolGroup] = frozenset(ToolGroup)


def resolve_tool_groups(raw: str | None) -> frozenset[ToolGroup]:
    """Resolve configured optional tool groups.

    Args:
        raw: Comma-separated group ids from ``MCP_TOOLS``, or ``None`` for all groups.

    Returns:
        Set of optional tool groups to register.

    Raises:
        ValueError: If any group id is unknown.
    """
    if raw is None:
        return ALL_TOOL_GROUPS

    tokens = [part.strip().lower() for part in raw.split(",") if part.strip()]
    if not tokens:
        return ALL_TOOL_GROUPS

    resolved: set[ToolGroup] = set()
    unknown: list[str] = []

    for token in tokens:
        try:
            resolved.add(ToolGroup(token))
        except ValueError:
            unknown.append(token)

    if unknown:
        valid = ", ".join(sorted(g.value for g in ToolGroup))
        unknown_list = ", ".join(sorted(set(unknown)))
        raise ValueError(f"Unknown tool group(s): {unknown_list}. Valid groups: {valid}")

    return frozenset(resolved)


def tool_group_registration_summary(
    enabled: frozenset[ToolGroup],
) -> dict[str, list[str]]:
    """Build enabled/disabled group lists for startup logging."""
    disabled = sorted(g.value for g in ALL_TOOL_GROUPS - enabled)
    return {
        "enabled_groups": sorted(g.value for g in enabled),
        "disabled_groups": disabled,
    }


def register_tool_groups(
    mcp: FastMCP,
    settings: Settings,
    context_getter: Callable[[], SSOServerContext | None],
    groups: frozenset[ToolGroup],
) -> None:
    """Register optional tool groups enabled in configuration."""
    if ToolGroup.TEMPLATES in groups:
        register_template_generation_tools(mcp)
    if ToolGroup.CEPH in groups:
        register_ceph_operations_tools(mcp, settings, context_getter)
    if ToolGroup.RABBITMQ in groups:
        register_messaging_operations_tools(mcp, settings, context_getter)
    if ToolGroup.NODES in groups:
        register_node_lifecycle_tools(mcp, settings, context_getter)
    if ToolGroup.VISIBILITY in groups:
        register_operations_visibility_tools(mcp, settings, context_getter)
    if ToolGroup.HEALTH in groups:
        register_cluster_health_tools(mcp, settings, context_getter)
    if ToolGroup.TROUBLESHOOTING in groups:
        register_troubleshooting_tools(mcp, settings, context_getter)
    if ToolGroup.VALIDATION in groups:
        register_validation_tools(mcp, settings, context_getter)
