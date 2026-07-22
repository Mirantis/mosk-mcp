"""Pydantic models for server tool inputs/outputs.

This module contains data models used by the MCP server tools.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ServerHealthResult(BaseModel):
    """Result of the MCP server health check.

    This is the output for the server's health_check endpoint, showing
    the overall server status and component checks. Different from
    HealthCheckResult in cluster_health/models.py which represents
    individual cluster health check items.
    """

    status: str = Field(..., description="Health status: healthy, degraded, or unhealthy")
    timestamp: str = Field(..., description="ISO 8601 timestamp of the check")
    version: str = Field(..., description="Server version")
    checks: dict[str, dict] = Field(
        default_factory=dict, description="Individual component health checks"
    )


class ServerInfo(BaseModel):
    """Server information and capabilities."""

    name: str = Field(..., description="Server name")
    version: str = Field(..., description="Server version")
    transport: str = Field(..., description="Active transport type")
    auth_enabled: bool = Field(..., description="Whether authentication is enabled")
    capabilities: list[str] = Field(
        default_factory=list, description="List of available tool categories"
    )
    mosk_version: str | None = Field(
        default=None, description="MOSK cluster version (requires login)"
    )
    mosk_version_supported: bool | None = Field(
        default=None, description="Whether MOSK version meets minimum requirements (25.1+)"
    )
    warnings: list[str] = Field(
        default_factory=list, description="Version or compatibility warnings"
    )
