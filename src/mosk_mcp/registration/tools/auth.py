"""Authentication tools registration for MOSK MCP Server.

This module registers authentication tools with the MCP server:
- login_secure: Device Flow authentication (recommended)
- login_start: Start Device Flow with background polling
- login_complete: Check/complete Device Flow authentication
- logout: End authenticated session
- session_status: Check session status
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from fastmcp import Context
from pydantic import Field

from mosk_mcp.observability.logging import get_logger
from mosk_mcp.registration.utils import with_logging_context
from mosk_mcp.tools.auth import (
    DeviceFlowCompleteOutput,
    DeviceFlowStatus,
    LogoutOutput,
    SessionStatusOutput,
)


if TYPE_CHECKING:
    from collections.abc import Callable

    from fastmcp import FastMCP

    from mosk_mcp.core.config import Settings
    from mosk_mcp.core.server_context import SSOServerContext


logger = get_logger(__name__)


async def _get_mgmt_url_from_cluster(settings: Settings) -> tuple[str | None, bool]:
    """Get management cluster URL from active cluster config, falling back to settings.

    Returns:
        Tuple of (mgmt_url, ssl_verify)
    """
    # First try to get URL from active cluster
    try:
        from mosk_mcp.cluster.manager import get_cluster_manager

        manager = get_cluster_manager()
        cluster_config = await manager.get_active_cluster_config()

        if cluster_config:
            logger.info(
                "using_cluster_config_for_auth",
                url=cluster_config.url,
                ssl_verify=cluster_config.ssl_verify,
            )
            return cluster_config.url, cluster_config.ssl_verify
        else:
            logger.warning(
                "no_active_cluster_config",
                message="No active cluster configuration found",
            )
    except Exception as e:
        logger.warning(
            "cluster_config_not_available",
            error=str(e),
            error_type=type(e).__name__,
        )

    # Fall back to settings
    logger.info(
        "falling_back_to_settings",
        mgmt_url=settings.mgmt_url,
        ssl_verify=settings.ssl_verify,
    )
    return settings.mgmt_url, settings.ssl_verify


def register_auth_tools(
    mcp: FastMCP, settings: Settings, context_getter: Callable[[], SSOServerContext | None]
) -> None:
    """Register authentication tools with the MCP server.

    These tools provide Keycloak SSO authentication for users to establish
    authenticated sessions before using other cluster management tools.

    Auth tools are always available regardless of auth_enabled setting since
    they ARE the mechanism for establishing authentication.

    Args:
        mcp: FastMCP server instance.
        settings: Application settings.
        context_getter: Function that returns the current global SSOServerContext.
                       Using a getter prevents capturing a stale context instance.
    """

    # =========================================================================
    # Device Flow Login Tools (Secure - Browser-based)
    # =========================================================================

    @mcp.tool(
        name="login_secure",
        description=(
            "Authenticate securely using OAuth 2.0 Device Flow. "
            "NO PASSWORD REQUIRED in chat - you authenticate in your browser. "
            "This is the RECOMMENDED authentication method. "
            "Supports MFA/2FA and keeps credentials secure. "
            "IMPORTANT: This tool returns a URL and code that YOU MUST display to the user. "
            "The user needs to visit the URL and enter the code to authenticate."
        ),
    )
    async def _login_secure(
        ctx: Context,
        mosk_cluster_name: str | None = Field(
            default=None,
            description="MOSK cluster name for cluster-level authentication. "
            "If not provided, will auto-discover the MOSK cluster.",
        ),
        mosk_namespace: str = Field(
            default="default",
            description="Kubernetes namespace where the MOSK cluster is defined",
        ),
        auto_discover_mosk: bool = Field(
            default=True,
            description="Auto-discover and authenticate to MOSK cluster if "
            "mosk_cluster_name is not provided. Set to False to skip MOSK auth.",
        ),
    ) -> dict[str, Any]:
        """Authenticate user with Device Flow (secure, browser-based)."""
        async with with_logging_context("login_secure"):
            logger.info(
                "device_flow_login_tool_invoked",
                mosk_cluster_name=mosk_cluster_name,
            )

            if not settings.device_flow_enabled:
                return DeviceFlowCompleteOutput(
                    status=DeviceFlowStatus.ERROR,
                    success=False,
                    message=(
                        "Device Flow authentication is disabled. "
                        "Set MCP_DEVICE_FLOW_ENABLED=true to enable."
                    ),
                ).model_dump()

            try:
                from mosk_mcp.auth.session import UserSession
                from mosk_mcp.tools.auth.device_flow_login import (
                    DeviceFlowLoginInput,
                    DeviceFlowLoginManager,
                )

                # Get management cluster URL from active cluster config (or fall back to settings)
                mgmt_url, ssl_verify = await _get_mgmt_url_from_cluster(settings)

                context = context_getter()
                if not context:
                    raise RuntimeError("Server context not initialized")

                if context._session is None:
                    context._session = UserSession(
                        settings=settings,
                        mgmt_url=mgmt_url,  # Pass URL from cluster config
                        ssl_verify=ssl_verify,  # Pass SSL verify from cluster config
                        keycloak_url=settings.keycloak_url,
                        realm=settings.keycloak_realm,
                        mcc_client_id=settings.mcc_oidc_client_id,
                    )

                manager = DeviceFlowLoginManager(
                    settings,
                    context._session,
                    mgmt_url_override=mgmt_url,
                    ssl_verify_override=ssl_verify,
                )
                context._device_flow_manager = manager

                input_data = DeviceFlowLoginInput(
                    mosk_cluster_name=mosk_cluster_name,
                    mosk_namespace=mosk_namespace,
                    auto_discover_mosk=auto_discover_mosk,
                )

                init_result = await manager.initiate(input_data)

                logger.info(
                    "device_flow_initiated",
                    user_code=init_result.user_code,
                    verification_uri=init_result.verification_uri,
                )

                # Start background polling task
                async def _background_poll() -> None:
                    """Background task that polls for auth completion."""
                    try:
                        complete_result = await manager.complete()
                        if complete_result.success:
                            logger.info(
                                "device_flow_background_poll_success",
                                username=complete_result.username,
                            )
                        else:
                            logger.warning(
                                "device_flow_background_poll_failed",
                                status=complete_result.status.value,
                                message=complete_result.message,
                            )
                    except Exception as e:
                        logger.error(
                            "device_flow_background_poll_error",
                            error=str(e),
                        )

                import asyncio

                poll_task = asyncio.create_task(_background_poll())
                # Use synchronized accessor to prevent TOCTOU race conditions
                await context.set_device_flow_poll_task(poll_task)

                return {
                    "status": "awaiting_user",
                    "success": False,
                    "user_code": init_result.user_code,
                    "verification_uri": init_result.verification_uri,
                    "verification_uri_complete": init_result.verification_uri_complete,
                    "expires_in": init_result.expires_in,
                    "message": init_result.message,
                    "polling": True,
                    "instructions": (
                        "🔐 AUTHENTICATION REQUIRED\n\n"
                        f"Please open this URL in your browser:\n"
                        f"   {init_result.verification_uri}\n\n"
                        f"Enter this code: {init_result.user_code}\n\n"
                        f"Or use this direct link (auto-fills code):\n"
                        f"{init_result.verification_uri_complete}\n\n"
                        f"Code expires in {init_result.expires_in // 60} minutes.\n\n"
                        f"⏳ After you complete login in your browser, simply proceed "
                        f"with your request (e.g., 'show cluster status'). "
                        f"No need to type anything - I'll be ready automatically."
                    ),
                }

            except Exception as e:
                logger.error("device_flow_login_failed", error=str(e))
                return DeviceFlowCompleteOutput(
                    status=DeviceFlowStatus.ERROR,
                    success=False,
                    message=f"Device Flow authentication failed: {e}",
                ).model_dump()

    @mcp.tool(
        name="login_start",
        description=(
            "Start Device Flow authentication with automatic background polling. "
            "Returns a URL and code for browser authentication. "
            "IMPORTANT: Display the returned URL and code to the user! "
            "Automatic polling will detect when authentication completes."
        ),
    )
    async def _login_start(
        mosk_cluster_name: str | None = Field(
            default=None,
            description="MOSK cluster name for cluster-level authentication.",
        ),
        mosk_namespace: str = Field(
            default="default",
            description="Kubernetes namespace where the MOSK cluster is defined",
        ),
        auto_discover_mosk: bool = Field(
            default=True,
            description="Auto-discover MOSK cluster if name not provided.",
        ),
    ) -> dict[str, Any]:
        """Start Device Flow with background polling."""
        async with with_logging_context("login_start"):
            logger.info(
                "device_flow_login_start_invoked",
                mosk_cluster_name=mosk_cluster_name,
            )

            if not settings.device_flow_enabled:
                return {
                    "status": "error",
                    "success": False,
                    "message": "Device Flow is disabled. Set MCP_DEVICE_FLOW_ENABLED=true",
                }

            try:
                from mosk_mcp.auth.session import UserSession
                from mosk_mcp.tools.auth.device_flow_login import (
                    DeviceFlowLoginInput,
                    DeviceFlowLoginManager,
                )

                # Get management cluster URL from active cluster config (or fall back to settings)
                mgmt_url, ssl_verify = await _get_mgmt_url_from_cluster(settings)

                context = context_getter()
                if not context:
                    raise RuntimeError("Server context not initialized")

                if context._session is None:
                    context._session = UserSession(
                        settings=settings,
                        mgmt_url=mgmt_url,  # Pass URL from cluster config
                        ssl_verify=ssl_verify,  # Pass SSL verify from cluster config
                        keycloak_url=settings.keycloak_url,
                        realm=settings.keycloak_realm,
                        mcc_client_id=settings.mcc_oidc_client_id,
                    )

                manager = DeviceFlowLoginManager(
                    settings,
                    context._session,
                    mgmt_url_override=mgmt_url,
                    ssl_verify_override=ssl_verify,
                )
                context._device_flow_manager = manager

                input_data = DeviceFlowLoginInput(
                    mosk_cluster_name=mosk_cluster_name,
                    mosk_namespace=mosk_namespace,
                    auto_discover_mosk=auto_discover_mosk,
                )

                init_result = await manager.initiate(input_data)

                logger.info(
                    "device_flow_started",
                    user_code=init_result.user_code,
                    verification_uri=init_result.verification_uri,
                )

                # Start background polling task
                async def _background_poll() -> None:
                    """Background task that polls for auth completion."""
                    try:
                        complete_result = await manager.complete()
                        if complete_result.success:
                            logger.info(
                                "device_flow_background_poll_success",
                                username=complete_result.username,
                            )
                        else:
                            logger.warning(
                                "device_flow_background_poll_failed",
                                status=complete_result.status.value,
                                message=complete_result.message,
                            )
                    except Exception as e:
                        logger.error(
                            "device_flow_background_poll_error",
                            error=str(e),
                        )

                import asyncio

                poll_task = asyncio.create_task(_background_poll())
                # Use synchronized accessor to prevent TOCTOU race conditions
                await context.set_device_flow_poll_task(poll_task)

                return {
                    "status": "awaiting_user",
                    "success": False,
                    "user_code": init_result.user_code,
                    "verification_uri": init_result.verification_uri,
                    "verification_uri_complete": init_result.verification_uri_complete,
                    "expires_in": init_result.expires_in,
                    "message": init_result.message,
                    "polling": True,
                    "instructions": (
                        "🔐 Authentication Required\n\n"
                        f"1. Open this URL in your browser:\n"
                        f"   {init_result.verification_uri}\n\n"
                        f"2. Enter this code: {init_result.user_code}\n\n"
                        f"Or use this direct link:\n"
                        f"{init_result.verification_uri_complete}\n\n"
                        f"⏳ After you complete login in your browser, simply proceed "
                        f"with your request. No need to type anything - I'll be ready."
                    ),
                }

            except Exception as e:
                logger.error("device_flow_login_start_failed", error=str(e))
                return {
                    "status": "error",
                    "success": False,
                    "message": f"Failed to start device flow: {e}",
                }

    @mcp.tool(
        name="login_complete",
        description=(
            "Check Device Flow authentication status or wait for completion. "
            "Usually not needed - login_secure polls automatically. "
            "Use this to check if background authentication completed."
        ),
    )
    async def _login_complete() -> dict[str, Any]:
        """Check or complete Device Flow authentication."""
        async with with_logging_context("login_complete"):
            logger.info("device_flow_login_complete_invoked")

            context = context_getter()
            if not context:
                return {
                    "status": "error",
                    "success": False,
                    "message": "Server context not initialized",
                }

            # Check if session is already authenticated
            if context._session and context._session.state.authenticated:
                username = context._session.state.username
                logger.info(
                    "device_flow_already_authenticated",
                    username=username,
                )
                return {
                    "status": "completed",
                    "success": True,
                    "username": username,
                    "message": f"Already authenticated as {username}",
                    "iam_roles": context._session.state.iam_roles,
                    "mcc_authenticated": True,
                    "mosk_authenticated": context._session._mosk_tokens is not None,
                }

            # Check if there's a background poll task running
            # Use synchronized accessor to prevent TOCTOU race conditions
            poll_task = await context.get_device_flow_poll_task()
            if poll_task is not None and not poll_task.done():
                import asyncio

                try:
                    await asyncio.wait_for(poll_task, timeout=300)
                except TimeoutError:
                    logger.warning("device_flow_poll_task_timeout")
                    return {
                        "status": "pending",
                        "success": False,
                        "message": "Authentication still pending. Complete login in browser.",
                    }

                # Check if authentication succeeded
                if context._session and context._session.state.authenticated:
                    username = context._session.state.username
                    return {
                        "status": "completed",
                        "success": True,
                        "username": username,
                        "message": f"Successfully authenticated as {username}",
                        "iam_roles": context._session.state.iam_roles,
                        "mcc_authenticated": True,
                        "mosk_authenticated": context._session._mosk_tokens is not None,
                    }

            # Fall back to manual polling via manager
            manager = getattr(context, "_device_flow_manager", None)

            if manager is None or not manager.is_flow_active:
                return {
                    "status": "error",
                    "success": False,
                    "message": (
                        "No active device flow. Call 'login_secure' first to "
                        "initiate authentication."
                    ),
                }

            try:
                complete_result = await manager.complete()
                return cast("dict[str, Any]", complete_result.model_dump())

            except Exception as e:
                logger.error("device_flow_login_complete_failed", error=str(e))
                return {
                    "status": "error",
                    "success": False,
                    "message": f"Failed to complete device flow: {e}",
                }

    # =========================================================================
    # Logout Tool
    # =========================================================================

    @mcp.tool(
        name="logout",
        description=(
            "End the current authenticated session. "
            "Cleans up OIDC tokens, kubeconfig files, and adapter connections."
        ),
    )
    async def _logout() -> dict[str, Any]:
        """End the current authenticated session."""
        async with with_logging_context("logout"):
            logger.info("logout_tool_invoked")

            context = context_getter()
            if not context:
                return LogoutOutput(
                    success=False, message="Server context not initialized"
                ).model_dump()

            try:
                username = None
                if context._session and context._session.state.authenticated:
                    username = context._session.state.username

                await context.logout()

                message = (
                    f"Successfully logged out user '{username}'"
                    if username
                    else "No active session to logout"
                )

                return LogoutOutput(
                    success=True,
                    message=message,
                ).model_dump()

            except Exception as e:
                logger.error("logout_failed", error=str(e))
                return LogoutOutput(
                    success=False,
                    message=f"Logout failed: {e}",
                ).model_dump()

    # =========================================================================
    # Session Status Tool
    # =========================================================================

    @mcp.tool(
        name="session_status",
        description=(
            "Check the current authentication session status. "
            "Returns information about authentication state, token expiry, "
            "IAM roles, and available cluster adapters. "
            "Also shows if device flow polling is active. Read-only operation."
        ),
    )
    async def _session_status() -> dict[str, Any]:
        """Get current session authentication status."""
        async with with_logging_context("session_status"):
            logger.debug("session_status_tool_invoked")

            context = context_getter()
            if not context:
                return {"authenticated": False, "error": "Server context not initialized"}

            try:
                # Use synchronized accessor to prevent TOCTOU race conditions
                poll_task = await context.get_device_flow_poll_task()
                device_flow_polling = poll_task is not None and not poll_task.done()

                if context._session is None:
                    result = SessionStatusOutput(
                        authenticated=False,
                        username=None,
                        token_expired=True,
                        has_mcc_adapter=False,
                        has_mosk_adapter=False,
                        has_stacklight_client=False,
                    ).model_dump()
                    result["device_flow_polling"] = device_flow_polling
                    if device_flow_polling:
                        result["message"] = (
                            "Authentication in progress - polling for browser login completion"
                        )
                    return result

                status = context._session.get_status()

                result = SessionStatusOutput(
                    authenticated=status["authenticated"],
                    username=status["username"],
                    authenticated_at=status["authenticated_at"],
                    last_activity=status["last_activity"],
                    token_expires_at=status["token_expires_at"],
                    token_expired=status["token_expired"],
                    iam_roles=status["iam_roles"],
                    has_mcc_adapter=status["has_mcc_adapter"],
                    has_mosk_adapter=status["has_mosk_adapter"],
                    has_stacklight_client=status["has_stacklight_client"],
                ).model_dump()
                result["device_flow_polling"] = device_flow_polling
                return result

            except Exception as e:
                logger.error("session_status_failed", error=str(e))
                result = SessionStatusOutput(
                    authenticated=False,
                    username=None,
                    token_expired=True,
                    has_mcc_adapter=False,
                    has_mosk_adapter=False,
                    has_stacklight_client=False,
                ).model_dump()
                result["device_flow_polling"] = False
                return result

    logger.debug("auth_tools_registered", count=5)
