"""Core module for MOSK MCP Server.

This package contains the core server components:
- config.py: Pydantic settings and validation
- exceptions.py: Custom exception hierarchy
- validation.py: Input validation utilities
- server.py: FastMCP server setup
- server_context.py: SSO context and session management
"""

from __future__ import annotations

from mosk_mcp.core.config import (
    Settings,
    TransportType,
    get_settings,
    init_settings,
)
from mosk_mcp.core.exceptions import (
    AuthenticationError,
    AuthorizationError,
    ConfigurationError,
    KubernetesError,
    MoskConnectionError,
    MoskMCPError,
    RateLimitError,
    ResourceNotFoundError,
    ToolExecutionError,
    UnsupportedVersionError,
    ValidationError,
)
from mosk_mcp.core.server_context import (
    ServerContextConfig,
    SSOServerContext,
)

__all__ = [
    # Exceptions
    "AuthenticationError",
    "AuthorizationError",
    "ConfigurationError",
    "KubernetesError",
    "MoskConnectionError",
    "MoskMCPError",
    "RateLimitError",
    "ResourceNotFoundError",
    "SSOServerContext",
    # Server context
    "ServerContextConfig",
    # Config
    "Settings",
    "ToolExecutionError",
    "TransportType",
    "UnsupportedVersionError",
    "ValidationError",
    "get_settings",
    "init_settings",
]
