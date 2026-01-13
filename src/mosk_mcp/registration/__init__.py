"""MOSK MCP Server registration package.

This package provides modular tool registration for the MCP server:

Submodules:
- models: Pydantic models for tool inputs/outputs
- tools: Tool registration functions organized by category

The main server functionality is in mosk_mcp.core.server (core/server.py).
This package provides the registration layer that connects tools to the FastMCP server.

Usage:
    from mosk_mcp.registration.models import ServerHealthResult, ServerInfo
    from mosk_mcp.registration.tools import (
        register_auth_tools,
        register_template_generation_tools,
        register_ceph_operations_tools,
    )
"""

from __future__ import annotations

from mosk_mcp.registration.models import ServerHealthResult, ServerInfo


__all__ = [
    "ServerHealthResult",
    "ServerInfo",
]
