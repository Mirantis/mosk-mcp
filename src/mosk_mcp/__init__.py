"""MOSK MCP Server - AI-powered operations tool for Mirantis OpenStack for Kubernetes.

This package provides an MCP (Model Context Protocol) server that enables AI assistants
to interact with MOSK clusters for infrastructure automation, monitoring, and operations.

Package structure:
- core: Core server components (config, exceptions, server, server_context, validation)
- observability: Logging, metrics, health checks, and audit
- infrastructure: Shutdown handling, rate limiting, version checking
- adapters: Infrastructure adapters (Kubernetes, Ceph, StackLight, OpenStack)
- auth: Authentication, authorization, and RBAC
- tools: MCP tool implementations
- registration: Tool registration layer

Usage:
    from mosk_mcp import MoskMCPError, KubernetesError
    from mosk_mcp.adapters import KubernetesAdapter
    from mosk_mcp.auth import RBACEnforcer, CRQValidator
    from mosk_mcp.observability.audit import AuditLogger
"""

from __future__ import annotations

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
    ValidationError,
)


__version__ = "0.1.0"
__all__ = [
    # Auth exceptions
    "AuthenticationError",
    "AuthorizationError",
    # Connection exceptions
    "ConfigurationError",
    # Kubernetes exceptions
    "KubernetesError",
    "MoskConnectionError",
    # Base exception
    "MoskMCPError",
    "RateLimitError",
    "ResourceNotFoundError",
    # Tool exceptions
    "ToolExecutionError",
    # Validation exception
    "ValidationError",
    "__version__",
]
