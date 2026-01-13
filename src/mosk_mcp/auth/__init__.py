"""Authentication and authorization module for MOSK MCP Server.

This module provides authentication and authorization functionality:
- Role-Based Access Control (RBAC)
- Change Request (CRQ) validation
- Keycloak OIDC integration via Device Flow
- OAuth 2.0 Device Authorization Grant (RFC 8628)

Usage:
    # Device Flow auth (recommended)
    from mosk_mcp.auth import DeviceFlowAuthProvider

    provider = DeviceFlowAuthProvider(
        keycloak_url="https://keycloak.example.com",
        realm="iam",
        client_id="kaas",
    )
    async with provider:
        device_auth = await provider.initiate_device_flow()
        print(f"Visit: {device_auth.verification_uri}")
        print(f"Enter code: {device_auth.user_code}")
        tokens = await provider.poll_for_token(device_auth)

    # RBAC enforcement
    from mosk_mcp.auth import RBACEnforcer, Permission, UserContext

    enforcer = RBACEnforcer()
    enforcer.require_permission(context, Permission.WRITE_MACHINES)
"""

from __future__ import annotations

from mosk_mcp.auth.crq import (
    CRQContext,
    CRQStatus,
    CRQValidationResult,
    CRQValidator,
    get_crq_validator,
    require_crq,
    set_crq_validator,
    validate_crq,
)

# Device Flow authentication (OAuth 2.0 Device Authorization Grant)
from mosk_mcp.auth.device_flow import (
    DeviceAuthorizationResponse,
    DeviceFlowAuthProvider,
    DeviceFlowResult,
    DeviceFlowStatus,
)

# JWT utilities (part of keycloak_client)
from mosk_mcp.auth.keycloak_client import (
    decode_jwt_payload,
    get_iam_roles,
    get_jwt_claim,
)
from mosk_mcp.auth.rbac import (
    RBACEnforcer,
    ToolDefinition,
    ToolRegistry,
    ToolSafetyLevel,
    get_enforcer,
    require_permission_decorator,
    require_role_decorator,
    require_safety_level,
    set_enforcer,
)
from mosk_mcp.auth.types import (
    ROLE_PERMISSIONS,
    Permission,
    Role,
    UserContext,
)


__all__ = [
    "ROLE_PERMISSIONS",
    # CRQ
    "CRQContext",
    "CRQStatus",
    "CRQValidationResult",
    "CRQValidator",
    # Device Flow authentication
    "DeviceAuthorizationResponse",
    "DeviceFlowAuthProvider",
    "DeviceFlowResult",
    "DeviceFlowStatus",
    # Auth types
    "Permission",
    # RBAC
    "RBACEnforcer",
    "Role",
    "ToolDefinition",
    "ToolRegistry",
    "ToolSafetyLevel",
    "UserContext",
    # JWT utilities
    "decode_jwt_payload",
    "get_crq_validator",
    "get_enforcer",
    "get_iam_roles",
    "get_jwt_claim",
    "require_crq",
    "require_permission_decorator",
    "require_role_decorator",
    "require_safety_level",
    "set_crq_validator",
    "set_enforcer",
    "validate_crq",
]
