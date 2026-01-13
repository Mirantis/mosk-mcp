"""Authentication MCP tools.

This module provides MCP tools for user authentication via Keycloak SSO.

Authentication Method:
    Device Flow: Secure browser-based authentication
    - No password in chat
    - Supports MFA/2FA
    - Industry standard (GitHub CLI, Azure CLI pattern)
"""

from __future__ import annotations

# Device Flow authentication tools
from mosk_mcp.tools.auth.device_flow_login import (
    DeviceFlowLoginManager,
    ManagedTokens,
    device_flow_login,
    device_flow_login_complete,
    device_flow_login_start,
    refresh_tokens,
)
from mosk_mcp.tools.auth.logout import logout

# Models
from mosk_mcp.tools.auth.models import (
    AuthMethod,
    DeviceFlowCompleteInput,
    DeviceFlowCompleteOutput,
    DeviceFlowInitOutput,
    DeviceFlowLoginInput,
    DeviceFlowStatus,
    LogoutInput,
    LogoutOutput,
    SessionStatusInput,
    SessionStatusOutput,
)
from mosk_mcp.tools.auth.session_status import get_session_status


__all__ = [
    "AuthMethod",
    "DeviceFlowCompleteInput",
    "DeviceFlowCompleteOutput",
    "DeviceFlowInitOutput",
    "DeviceFlowLoginInput",
    "DeviceFlowLoginManager",
    "DeviceFlowStatus",
    "LogoutInput",
    "LogoutOutput",
    "ManagedTokens",
    "SessionStatusInput",
    "SessionStatusOutput",
    "device_flow_login",
    "device_flow_login_complete",
    "device_flow_login_start",
    "get_session_status",
    "logout",
    "refresh_tokens",
]
