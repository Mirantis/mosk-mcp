"""Pydantic models for authentication MCP tools.

This module defines input and output schemas for authentication operations
using secure Device Flow authentication.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from mosk_mcp.tools.common.enums import DeviceFlowStatus


class AuthMethod(str, Enum):
    """Authentication method to use."""

    DEVICE_FLOW = "device_flow"  # OAuth 2.0 Device Authorization Grant


# =============================================================================
# Device Flow Authentication Models (Secure - No password in chat)
# =============================================================================


class DeviceFlowLoginInput(BaseModel):
    """Input parameters for Device Flow login.

    Device Flow authentication does NOT require password in chat.
    User authenticates via browser, supporting MFA/2FA.

    Attributes:
        mosk_cluster_name: Optional MOSK cluster name for cluster authentication.
        mosk_namespace: Namespace where MOSK cluster is defined.
        auto_discover_mosk: If True, auto-discover MOSK cluster.
    """

    mosk_cluster_name: str | None = Field(
        default=None,
        description="MOSK cluster name on MCC for cluster authentication. "
        "If not provided, will auto-discover the MOSK cluster.",
    )
    mosk_namespace: str = Field(
        default="default",
        description="Namespace where MOSK cluster is defined",
    )
    auto_discover_mosk: bool = Field(
        default=True,
        description="Auto-discover and authenticate to MOSK cluster if "
        "mosk_cluster_name is not provided. Set to False to skip MOSK auth.",
    )


class DeviceFlowInitOutput(BaseModel):
    """Output from initiating Device Flow authentication.

    This response contains the information to display to the user.
    The user must visit verification_uri and enter user_code.

    Attributes:
        status: Current status (awaiting_user).
        user_code: Short code user enters in browser (e.g., "ABCD-EFGH").
        verification_uri: URL user visits to authenticate.
        verification_uri_complete: URL with code pre-filled (for QR/clicking).
        expires_in: Seconds until device code expires.
        message: Human-readable instructions for user.
        poll_interval: Seconds to wait between status checks.
    """

    status: DeviceFlowStatus = Field(
        default=DeviceFlowStatus.AWAITING_USER,
        description="Current authentication status",
    )
    user_code: str = Field(
        ...,
        description="Short code user enters in browser (e.g., 'ABCD-EFGH')",
    )
    verification_uri: str = Field(
        ...,
        description="URL user visits to authenticate",
    )
    verification_uri_complete: str = Field(
        ...,
        description="URL with code pre-filled (for clicking or QR code)",
    )
    expires_in: int = Field(
        ...,
        ge=1,
        description="Seconds until device code expires",
    )
    message: str = Field(
        ...,
        description="Human-readable instructions for user",
    )
    poll_interval: int = Field(
        default=5,
        ge=1,
        description="Recommended seconds between status checks",
    )


class DeviceFlowCompleteInput(BaseModel):
    """Input for checking/completing Device Flow authentication.

    Call this after user has been shown the verification URL.
    This will poll Keycloak until authentication completes.

    Attributes:
        wait_for_completion: If True, block until auth completes or expires.
        timeout: Maximum seconds to wait (only if wait_for_completion=True).
    """

    wait_for_completion: bool = Field(
        default=True,
        description="If True, wait for user to complete authentication. "
        "If False, return immediately with current status.",
    )
    timeout: int | None = Field(
        default=None,
        ge=1,
        description="Maximum seconds to wait for completion. "
        "None means wait until device code expires.",
    )


class DeviceFlowCompleteOutput(BaseModel):
    """Output from Device Flow completion/status check.

    Attributes:
        status: Current authentication status.
        success: Whether authentication completed successfully.
        username: Authenticated username (if completed).
        message: Human-readable status message.
        iam_roles: User's IAM roles (if completed).
        token_expires_in: Seconds until token expires (if completed).
        mcc_authenticated: Whether MCC authentication succeeded.
        mosk_authenticated: Whether MOSK cluster authentication succeeded.
        time_remaining: Seconds until device code expires (if still pending).
    """

    status: DeviceFlowStatus = Field(
        ...,
        description="Current authentication status",
    )
    success: bool = Field(
        ...,
        description="Whether authentication completed successfully",
    )
    username: str | None = Field(
        default=None,
        description="Authenticated username",
    )
    message: str = Field(
        ...,
        description="Human-readable status message",
    )
    iam_roles: list[str] = Field(
        default_factory=list,
        description="User's IAM roles from token",
    )
    token_expires_in: int | None = Field(
        default=None,
        ge=0,
        description="Seconds until token expires",
    )
    mcc_authenticated: bool = Field(
        default=False,
        description="Whether MCC authentication succeeded",
    )
    mosk_authenticated: bool = Field(
        default=False,
        description="Whether MOSK cluster authentication succeeded",
    )
    time_remaining: int | None = Field(
        default=None,
        ge=0,
        description="Seconds until device code expires (if still pending)",
    )


# =============================================================================
# Session Management Models
# =============================================================================


class LogoutInput(BaseModel):
    """Input parameters for the logout tool.

    No parameters required - logs out the current session.
    """

    pass


class LogoutOutput(BaseModel):
    """Output from the logout tool.

    Attributes:
        success: Whether logout succeeded.
        message: Human-readable status message.
    """

    success: bool = Field(..., description="Whether logout succeeded")
    message: str = Field(..., description="Human-readable status message")


class SessionStatusInput(BaseModel):
    """Input parameters for the session_status tool.

    No parameters required - returns current session status.
    """

    pass


class SessionStatusOutput(BaseModel):
    """Output from the session_status tool.

    Attributes:
        authenticated: Whether user is authenticated.
        username: Authenticated username.
        authenticated_at: When authentication occurred.
        last_activity: Last activity timestamp.
        token_expires_at: When tokens expire.
        token_expired: Whether tokens are expired.
        iam_roles: User's IAM roles.
        has_mcc_adapter: Whether MCC adapter is available.
        has_mosk_adapter: Whether MOSK adapter is available.
        has_stacklight_client: Whether StackLight client is available.
    """

    authenticated: bool = Field(..., description="Whether user is authenticated")
    username: str | None = Field(default=None, description="Authenticated username")
    authenticated_at: str | None = Field(
        default=None,
        description="When authentication occurred (ISO format)",
    )
    last_activity: str | None = Field(
        default=None,
        description="Last activity timestamp (ISO format)",
    )
    token_expires_at: str | None = Field(
        default=None,
        description="When tokens expire (ISO format)",
    )
    token_expired: bool = Field(
        default=True,
        description="Whether tokens are expired",
    )
    iam_roles: list[str] = Field(
        default_factory=list,
        description="User's IAM roles",
    )
    has_mcc_adapter: bool = Field(
        default=False,
        description="Whether MCC adapter is available",
    )
    has_mosk_adapter: bool = Field(
        default=False,
        description="Whether MOSK adapter is available",
    )
    has_stacklight_client: bool = Field(
        default=False,
        description="Whether StackLight client is available",
    )
    device_flow_polling: bool = Field(
        default=False,
        description="Whether Device Flow polling is active",
    )
