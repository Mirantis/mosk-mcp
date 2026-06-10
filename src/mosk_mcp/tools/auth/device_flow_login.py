"""Device Flow Login for secure Keycloak SSO authentication.

This module implements OAuth 2.0 Device Authorization Grant (RFC 8628) for
authenticating users to both MCC and MOSK clusters without exposing credentials.

Authentication Flow:
    1. MCP initiates two device flows (kaas for MCC, k8s for MOSK)
    2. User sees both verification URLs in a single message
    3. Step 1: User authenticates with kaas (enters password)
    4. Step 2: User authenticates with k8s (clicks "Allow" - SSO session active)
    5. MCP polls both flows concurrently
    6. Session established with tokens for both clusters

Security Benefits:
    - No credentials in chat history
    - Supports MFA/2FA via browser
    - Keycloak SSO enables single password entry for both clusters
    - Automatic token refresh extends session lifetime
"""

from __future__ import annotations


__all__ = [
    "DeviceFlowCompleteInput",
    "DeviceFlowCompleteOutput",
    "DeviceFlowInitOutput",
    "DeviceFlowLoginInput",
    "DeviceFlowLoginManager",
    "device_flow_login_complete",
    "device_flow_login_start",
]

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import httpx

from mosk_mcp.auth.device_flow import (
    DeviceAuthorizationResponse,
    DeviceFlowAuthProvider,
)
from mosk_mcp.auth.keycloak_client import (
    ClusterOIDCInfo,
    MCCEndpoints,
    TokenResponse,
    discover_mcc_endpoints,
    get_cluster_oidc_info,
)
from mosk_mcp.core.exceptions import AuthenticationError, ToolExecutionError
from mosk_mcp.observability.logging import get_logger
from mosk_mcp.tools.auth.models import (
    DeviceFlowCompleteInput,
    DeviceFlowCompleteOutput,
    DeviceFlowInitOutput,
    DeviceFlowLoginInput,
)
from mosk_mcp.tools.auth.models import (
    DeviceFlowStatus as ModelDeviceFlowStatus,
)


if TYPE_CHECKING:
    from mosk_mcp.auth.session import UserSession
    from mosk_mcp.core.config import Settings

logger = get_logger(__name__)


# =============================================================================
# Token Management
# =============================================================================


@dataclass
class ManagedTokens:
    """Holds tokens with refresh capability.

    Attributes:
        tokens: Current token response.
        client_id: OAuth client ID used to obtain tokens.
        issuer_url: Keycloak issuer URL for refresh.
    """

    tokens: TokenResponse
    client_id: str
    issuer_url: str

    def is_expired(self, buffer_seconds: int = 60) -> bool:
        """Check if access token is expired or will expire within buffer."""
        return self.tokens.is_expired_with_buffer(buffer_seconds)


async def refresh_tokens(
    managed: ManagedTokens,
    *,
    verify_ssl: bool = True,
    timeout: float = 30.0,
) -> TokenResponse:
    """Refresh tokens using refresh_token grant.

    Args:
        managed: ManagedTokens containing current tokens and client info.
        verify_ssl: Whether to verify SSL certificates.
        timeout: HTTP request timeout.

    Returns:
        New TokenResponse with refreshed tokens.

    Raises:
        AuthenticationError: If refresh fails.
    """
    if not managed.tokens.refresh_token:
        raise AuthenticationError(
            "No refresh token available. Re-authentication required.",
            details={"client_id": managed.client_id},
        )

    token_endpoint = f"{managed.issuer_url}/protocol/openid-connect/token"

    logger.info(
        "refreshing_tokens",
        client_id=managed.client_id,
        username=managed.tokens.username,
    )

    try:
        async with httpx.AsyncClient(
            verify=verify_ssl,
            timeout=httpx.Timeout(timeout),
        ) as client:
            response = await client.post(
                token_endpoint,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": managed.tokens.refresh_token,
                    "client_id": managed.client_id,
                },
            )

            if response.status_code != 200:
                error_data = {}
                if response.headers.get("content-type", "").startswith("application/json"):
                    error_data = response.json()

                error_msg = error_data.get("error_description", response.text)
                error_code = error_data.get("error", "refresh_failed")

                logger.warning(
                    "token_refresh_failed",
                    client_id=managed.client_id,
                    status_code=response.status_code,
                    error=error_msg,
                )

                if error_code in ("invalid_grant", "expired_token"):
                    raise AuthenticationError(
                        "Refresh token expired. Please re-authenticate.",
                        details={"error_code": error_code, "client_id": managed.client_id},
                    )

                raise AuthenticationError(
                    f"Token refresh failed: {error_msg}",
                    details={"error_code": error_code, "client_id": managed.client_id},
                )

            data = response.json()
            new_tokens = TokenResponse(
                access_token=data["access_token"],
                id_token=data["id_token"],
                refresh_token=data.get("refresh_token", managed.tokens.refresh_token),
                token_type=data.get("token_type", "Bearer"),
                expires_in=data.get("expires_in", 1800),
                refresh_expires_in=data.get("refresh_expires_in", 3600),
                scope=data.get("scope", ""),
            )

            logger.info(
                "tokens_refreshed",
                client_id=managed.client_id,
                username=new_tokens.username,
                expires_in=new_tokens.expires_in,
            )

            return new_tokens

    except httpx.RequestError as e:
        logger.error("token_refresh_network_error", error=str(e))
        raise AuthenticationError(
            f"Network error during token refresh: {e}",
            details={"endpoint": token_endpoint},
        ) from e


# =============================================================================
# Dual Device Flow Manager
# =============================================================================


class DeviceFlowLoginManager:
    """Manages dual device flow authentication for MCC and MOSK.

    This class handles the complete authentication flow:
    1. Initiates device flows for both kaas (MCC) and k8s (MOSK) clients
    2. Presents both URLs to user in Step 1/Step 2 format
    3. Polls both flows concurrently
    4. Stores tokens for both clusters in session

    The SSO session created during kaas authentication allows the k8s
    authentication to complete with just a click (no password re-entry).
    """

    def __init__(
        self,
        settings: Settings,
        session: UserSession,
        *,
        mgmt_url_override: str | None = None,
        ssl_verify_override: bool | None = None,
    ) -> None:
        """Initialize dual device flow manager.

        Args:
            settings: Application settings.
            session: User session to authenticate.
            mgmt_url_override: Override management cluster URL (from cluster config).
            ssl_verify_override: Override SSL verify setting (from cluster config).
        """
        self.settings = settings
        self.session = session

        # URL overrides from cluster config
        self._mgmt_url_override = mgmt_url_override
        self._ssl_verify_override = ssl_verify_override

        logger.debug(
            "device_flow_manager_initialized",
            mgmt_url_override=mgmt_url_override,
            ssl_verify_override=ssl_verify_override,
            settings_mgmt_url=settings.mgmt_url,
            settings_ssl_verify=settings.ssl_verify,
        )

        # Flow state
        self._mcc_endpoints: MCCEndpoints | None = None
        self._kaas_device_auth: DeviceAuthorizationResponse | None = None
        self._k8s_device_auth: DeviceAuthorizationResponse | None = None
        self._kaas_provider: DeviceFlowAuthProvider | None = None
        self._k8s_provider: DeviceFlowAuthProvider | None = None

        # MOSK cluster info
        self._mosk_cluster_name: str | None = None
        self._mosk_namespace: str = "default"
        self._mosk_oidc_info: ClusterOIDCInfo | None = None

    @property
    def mgmt_url(self) -> str | None:
        """Get effective management cluster URL (override or settings).

        Uses explicit None check to properly handle cluster config override.
        This ensures cluster config URL is used even if settings.mgmt_url is also set.
        """
        if self._mgmt_url_override is not None:
            return self._mgmt_url_override
        return self.settings.mgmt_url

    @property
    def ssl_verify(self) -> bool:
        """Get effective SSL verify setting (override or settings)."""
        if self._ssl_verify_override is not None:
            return self._ssl_verify_override
        return self.settings.ssl_verify

    @property
    def is_flow_active(self) -> bool:
        """Check if a device flow is currently active."""
        if self._kaas_device_auth is None:
            return False
        return not self._kaas_device_auth.is_expired

    @property
    def time_remaining(self) -> int:
        """Get seconds remaining in current flow."""
        if self._kaas_device_auth:
            return self._kaas_device_auth.time_remaining
        return 0

    async def initiate(
        self,
        input_data: DeviceFlowLoginInput,
    ) -> DeviceFlowInitOutput:
        """Initiate device flow authentication (non-blocking).

        This starts the device flows for both MCC (kaas) and MOSK (k8s)
        and returns the verification URLs/codes for the user to authenticate.

        Call complete() after user has authenticated in their browser.

        Args:
            input_data: Login parameters.

        Returns:
            DeviceFlowInitOutput with verification URLs and codes.
        """
        try:
            # Step 1: Discover endpoints
            await self._discover_endpoints()

            # Step 2: Discover MOSK cluster if needed
            await self._discover_mosk_cluster(input_data)

            # Step 3: Initiate both device flows
            await self._initiate_device_flows()

            # Return the verification info
            if not self._kaas_device_auth:
                raise ToolExecutionError(
                    message="Failed to initiate device flow",
                    tool_name="login",
                    details={},
                )

            return DeviceFlowInitOutput(
                status=ModelDeviceFlowStatus.AWAITING_USER,
                user_code=self._kaas_device_auth.user_code,
                verification_uri=self._kaas_device_auth.verification_uri,
                verification_uri_complete=self._kaas_device_auth.verification_uri_complete,
                expires_in=self._kaas_device_auth.expires_in,
                message=self.build_auth_message(),
                poll_interval=self._kaas_device_auth.interval,
            )

        except AuthenticationError as e:
            logger.error("device_flow_initiate_failed", error=str(e))
            raise ToolExecutionError(
                message=f"Authentication failed: {e.message}",
                tool_name="login",
                details=e.details or {},
            ) from e

        except Exception as e:
            logger.error(
                "device_flow_initiate_error",
                error=str(e),
                error_type=type(e).__name__,
            )
            raise ToolExecutionError(
                message=f"Authentication error: {e}",
                tool_name="login",
                details={},
            ) from e

    async def complete(self) -> DeviceFlowCompleteOutput:
        """Complete device flow authentication by polling for tokens.

        Call this after initiate() and after user has authenticated
        in their browser.

        Returns:
            DeviceFlowCompleteOutput with authentication result.
        """
        try:
            if not self.is_flow_active:
                return DeviceFlowCompleteOutput(
                    status=ModelDeviceFlowStatus.ERROR,
                    success=False,
                    message="No active device flow. Call initiate() first.",
                )

            # Poll both flows concurrently
            kaas_tokens, k8s_tokens = await self._poll_device_flows()

            # Establish session
            return await self._establish_session(kaas_tokens, k8s_tokens)

        except AuthenticationError as e:
            logger.error("device_flow_complete_failed", error=str(e))
            return DeviceFlowCompleteOutput(
                status=ModelDeviceFlowStatus.ERROR,
                success=False,
                message=f"Authentication failed: {e.message}",
            )

        except Exception as e:
            logger.error(
                "device_flow_complete_error",
                error=str(e),
                error_type=type(e).__name__,
            )
            return DeviceFlowCompleteOutput(
                status=ModelDeviceFlowStatus.ERROR,
                success=False,
                message=f"Authentication error: {e}",
            )

        finally:
            await self._cleanup()

    async def authenticate(
        self,
        input_data: DeviceFlowLoginInput,
    ) -> DeviceFlowCompleteOutput:
        """Perform complete dual device flow authentication.

        Args:
            input_data: Login parameters.

        Returns:
            DeviceFlowCompleteOutput with authentication result.
        """
        try:
            # Step 1: Discover endpoints
            await self._discover_endpoints()

            # Step 2: Discover MOSK cluster if needed
            await self._discover_mosk_cluster(input_data)

            # Step 3: Initiate both device flows
            await self._initiate_device_flows()

            # Step 4: Display instructions to user (via return value)
            # The message is shown, then we immediately start polling

            # Step 5: Poll both flows concurrently
            kaas_tokens, k8s_tokens = await self._poll_device_flows()

            # Step 6: Establish session
            return await self._establish_session(kaas_tokens, k8s_tokens)

        except AuthenticationError as e:
            logger.error("dual_device_flow_failed", error=str(e))
            raise ToolExecutionError(
                message=f"Authentication failed: {e.message}",
                tool_name="login",
                details=e.details or {},
            ) from e

        except Exception as e:
            logger.error(
                "dual_device_flow_error",
                error=str(e),
                error_type=type(e).__name__,
            )
            raise ToolExecutionError(
                message=f"Authentication error: {e}",
                tool_name="login",
                details={},
            ) from e

        finally:
            await self._cleanup()

    async def _discover_endpoints(self) -> None:
        """Discover MCC endpoints from config.js."""
        logger.debug(
            "discover_endpoints_url_sources",
            mgmt_url_override=self._mgmt_url_override,
            settings_mgmt_url=self.settings.mgmt_url,
            effective_mgmt_url=self.mgmt_url,
        )

        mgmt_url = self.mgmt_url
        if not mgmt_url:
            raise ToolExecutionError(
                message=(
                    "Management cluster URL not configured. Set MCP_MGMT_URL environment variable "
                    "or configure a cluster with 'add_cluster'. "
                    f"Override: {self._mgmt_url_override!r}, "
                    f"Settings: {self.settings.mgmt_url!r}"
                ),
                tool_name="login",
                details={
                    "config_key": "mgmt_url",
                    "mgmt_url_override": self._mgmt_url_override,
                    "settings_mgmt_url": self.settings.mgmt_url,
                },
            )

        logger.debug("discovering_mcc_endpoints", mgmt_url=mgmt_url)
        self._mcc_endpoints = await discover_mcc_endpoints(
            mcc_ui_url=mgmt_url,
            verify_ssl=self.ssl_verify,
        )

        logger.info(
            "mcc_endpoints_discovered",
            keycloak_url=self._mcc_endpoints.keycloak_url,
            realm=self._mcc_endpoints.keycloak_realm,
        )

    async def _discover_mosk_cluster(self, input_data: DeviceFlowLoginInput) -> None:
        """Discover MOSK cluster name and OIDC info.

        Args:
            input_data: Login parameters with optional cluster name.
        """
        if not self._mcc_endpoints:
            raise AuthenticationError(
                "MCC endpoints not discovered",
                details={},
            )

        # Use provided cluster name or auto-discover
        self._mosk_cluster_name = input_data.mosk_cluster_name
        self._mosk_namespace = input_data.mosk_namespace

        if not self._mosk_cluster_name and input_data.auto_discover_mosk:
            logger.info("auto_discovering_mosk_cluster")
            # We need MCC tokens first to discover the cluster
            # For now, store that we need to discover after MCC auth
            # The cluster will be discovered during session establishment
            pass

    async def _initiate_device_flows(self) -> None:
        """Initiate device flows for both kaas and k8s clients."""
        if not self._mcc_endpoints:
            raise AuthenticationError(
                "MCC endpoints not discovered",
                details={},
            )

        keycloak_url = self._mcc_endpoints.keycloak_url
        realm = self._mcc_endpoints.keycloak_realm

        # Create providers for both clients
        self._kaas_provider = DeviceFlowAuthProvider(
            keycloak_url=keycloak_url,
            realm=realm,
            client_id="kaas",
            verify_ssl=self.ssl_verify,
            default_scope=self.settings.device_flow_scope,
        )
        await self._kaas_provider.init_client()

        self._k8s_provider = DeviceFlowAuthProvider(
            keycloak_url=keycloak_url,
            realm=realm,
            client_id="k8s",
            verify_ssl=self.ssl_verify,
            default_scope=self.settings.device_flow_scope,
        )
        await self._k8s_provider.init_client()

        # Initiate both flows concurrently
        logger.info("initiating_dual_device_flows")
        kaas_task = self._kaas_provider.initiate_device_flow()
        k8s_task = self._k8s_provider.initiate_device_flow()

        self._kaas_device_auth, self._k8s_device_auth = await asyncio.gather(kaas_task, k8s_task)

        # Verify both flows completed successfully - use runtime check instead of assert
        # (asserts are disabled with Python -O flag)
        if self._kaas_device_auth is None:
            raise AuthenticationError(
                "KaaS device flow initiation failed - no device auth returned",
                error_code="DEVICE_FLOW_INIT_FAILED",
            )
        if self._k8s_device_auth is None:
            raise AuthenticationError(
                "K8s device flow initiation failed - no device auth returned",
                error_code="DEVICE_FLOW_INIT_FAILED",
            )

        logger.info(
            "dual_device_flows_initiated",
            kaas_user_code=self._kaas_device_auth.user_code,
            k8s_user_code=self._k8s_device_auth.user_code,
            expires_in=self._kaas_device_auth.expires_in,
        )

        # Log the authentication instructions
        self._log_auth_instructions()

    def _log_auth_instructions(self) -> None:
        """Log authentication instructions for the user."""
        if not self._kaas_device_auth or not self._k8s_device_auth:
            return

        logger.info(
            "device_flow_auth_instructions",
            message=(
                "\n"
                "═══════════════════════════════════════════════════════════════\n"
                "                    AUTHENTICATION REQUIRED\n"
                "═══════════════════════════════════════════════════════════════\n"
                "\n"
                "Please complete authentication in your browser:\n"
                "\n"
                f"  STEP 1 - MCC Access (enter username and password):\n"
                f"  {self._kaas_device_auth.verification_uri_complete}\n"
                "\n"
                f"  STEP 2 - MOSK Access (click 'Allow' - already logged in via SSO):\n"
                f"  {self._k8s_device_auth.verification_uri_complete}\n"
                "\n"
                f"  Codes expire in {self._kaas_device_auth.expires_in // 60} minutes.\n"
                "\n"
                "═══════════════════════════════════════════════════════════════\n"
            ),
        )

    async def _poll_device_flows(self) -> tuple[TokenResponse, TokenResponse]:
        """Poll both device flows concurrently.

        Returns:
            Tuple of (kaas_tokens, k8s_tokens).

        Raises:
            AuthenticationError: If either flow fails.
        """
        if not self._kaas_provider or not self._k8s_provider:
            raise AuthenticationError(
                "Device flow providers not initialized",
                details={},
            )

        if not self._kaas_device_auth or not self._k8s_device_auth:
            raise AuthenticationError(
                "Device flows not initiated",
                details={},
            )

        logger.info("polling_dual_device_flows")

        # Poll both flows concurrently
        kaas_task = self._kaas_provider.poll_for_token(
            self._kaas_device_auth,
            max_attempts=self.settings.device_flow_max_poll_attempts or None,
        )
        k8s_task = self._k8s_provider.poll_for_token(
            self._k8s_device_auth,
            max_attempts=self.settings.device_flow_max_poll_attempts or None,
        )

        try:
            kaas_tokens, k8s_tokens = await asyncio.gather(kaas_task, k8s_task)
            logger.info(
                "dual_device_flows_completed",
                username=kaas_tokens.username,
            )
            return kaas_tokens, k8s_tokens

        except Exception as e:
            # If one fails, cancel the other
            logger.error("dual_device_flow_poll_failed", error=str(e))
            raise

    async def _establish_session(
        self,
        kaas_tokens: TokenResponse,
        k8s_tokens: TokenResponse,
    ) -> DeviceFlowCompleteOutput:
        """Establish session with obtained tokens.

        Args:
            kaas_tokens: Tokens from kaas client (MCC).
            k8s_tokens: Tokens from k8s client (MOSK).

        Returns:
            DeviceFlowCompleteOutput with session details.
        """
        if not self._mcc_endpoints:
            raise AuthenticationError(
                "MCC endpoints not discovered",
                details={},
            )

        logger.info(
            "establishing_session",
            username=kaas_tokens.username,
        )

        # Create managed token objects for refresh capability
        issuer_url = (
            f"{self._mcc_endpoints.keycloak_url}/auth/realms/{self._mcc_endpoints.keycloak_realm}"
        )

        mcc_managed = ManagedTokens(
            tokens=kaas_tokens,
            client_id="kaas",
            issuer_url=issuer_url,
        )

        mosk_managed = ManagedTokens(
            tokens=k8s_tokens,
            client_id="k8s",
            issuer_url=issuer_url,
        )

        # Store MCC tokens and endpoints BEFORE auto-discovery
        # This is required because _auto_discover_mosk_cluster needs tokens
        # to create the MCC adapter for querying cluster info
        async with self.session._lock:
            # Invalidate any cached adapters from previous authentication
            # This is critical for re-authentication: old adapters use old tokens
            # stored in their kubeconfigs and must be recreated with new tokens
            await self.session._invalidate_cached_adapters_unlocked()

            self.session._mcc_endpoints = self._mcc_endpoints
            self.session._mcc_tokens = kaas_tokens
            self.session._mcc_managed_tokens = mcc_managed

        # Auto-discover MOSK cluster if needed
        if not self._mosk_cluster_name:
            await self._auto_discover_mosk_cluster()

        # Get MOSK OIDC info for kubeconfig generation
        # This is required to create the MOSK adapter - without it, we can't access MOSK
        mosk_oidc_failed = False
        if self._mosk_cluster_name:
            self._mosk_oidc_info = await get_cluster_oidc_info(
                mcc_k8s_api_url=self._mcc_endpoints.k8s_api_url,
                mcc_id_token=kaas_tokens.id_token,
                cluster_name=self._mosk_cluster_name,
                namespace=self._mosk_namespace,
                verify_ssl=self.ssl_verify,
            )
            # If OIDC info retrieval failed, we can't access MOSK cluster
            if self._mosk_oidc_info is None:
                mosk_oidc_failed = True
                logger.warning(
                    "mosk_oidc_info_retrieval_failed",
                    cluster=self._mosk_cluster_name,
                    namespace=self._mosk_namespace,
                    hint="User may not have permission to read Cluster CR",
                )

        # Update session state with MOSK tokens and remaining state
        # Note: MCC tokens/endpoints were stored earlier (before auto-discovery)
        async with self.session._lock:
            # Store MOSK tokens with refresh info
            self.session._mosk_tokens = k8s_tokens
            self.session._mosk_managed_tokens = mosk_managed

            # Store MOSK cluster info
            self.session._mosk_cluster_name = self._mosk_cluster_name
            self.session._mosk_cluster_namespace = self._mosk_namespace
            self.session._mosk_oidc_info = self._mosk_oidc_info

            # Update session state
            self.session.state.authenticated = True
            self.session.state.authenticated_at = datetime.now(UTC)
            self.session.state.last_activity = datetime.now(UTC)
            self.session.state.username = kaas_tokens.username

            # Set token expiry from the token's expires_in field
            # TokenResponse.expires_in defaults to 1800 if not provided by the API
            # Use the access_token_expiry computed field for consistency
            self.session.state.token_expires_at = kaas_tokens.access_token_expiry

            self.session.state.iam_roles = kaas_tokens.iam_roles

        # Determine MOSK authentication status - requires BOTH cluster name AND OIDC info
        mosk_fully_authenticated = (
            self._mosk_cluster_name is not None and self._mosk_oidc_info is not None
        )

        # Build response message
        message = f"Successfully authenticated as {kaas_tokens.username}"
        if mosk_fully_authenticated:
            message += f" with access to MCC and MOSK cluster '{self._mosk_cluster_name}'"
        elif self._mosk_cluster_name and mosk_oidc_failed:
            message += (
                f" with access to MCC. MOSK cluster '{self._mosk_cluster_name}' was discovered "
                "but OIDC info retrieval failed (check user permissions on Cluster CR)"
            )
        else:
            message += " with access to MCC (MOSK cluster not discovered)"

        logger.info(
            "session_established",
            username=kaas_tokens.username,
            mosk_cluster=self._mosk_cluster_name,
            mosk_authenticated=mosk_fully_authenticated,
        )

        return DeviceFlowCompleteOutput(
            status=ModelDeviceFlowStatus.COMPLETED,
            success=True,
            username=kaas_tokens.username,
            message=message,
            iam_roles=kaas_tokens.iam_roles,
            token_expires_in=kaas_tokens.expires_in,
            mcc_authenticated=True,
            mosk_authenticated=mosk_fully_authenticated,
        )

    async def _auto_discover_mosk_cluster(self) -> None:
        """Auto-discover MOSK cluster using MCC API."""
        if not self._mcc_endpoints:
            return

        try:
            # Create temporary MCC adapter
            mcc_adapter = await self.session._get_mcc_adapter_unlocked()
            cluster_name, namespace = await mcc_adapter.discover_mosk_cluster_namespace()

            if cluster_name:
                self._mosk_cluster_name = cluster_name
                self._mosk_namespace = namespace or "default"
                logger.info(
                    "mosk_cluster_auto_discovered",
                    cluster=cluster_name,
                    namespace=self._mosk_namespace,
                )
            else:
                logger.warning("mosk_cluster_not_found")

        except Exception as e:
            logger.warning("mosk_cluster_discovery_failed", error=str(e))

    async def _cleanup(self) -> None:
        """Cleanup device flow resources."""
        if self._kaas_provider:
            try:
                await self._kaas_provider.close()
            except Exception as e:
                logger.debug("kaas_provider_cleanup_error", error=str(e))
            self._kaas_provider = None

        if self._k8s_provider:
            try:
                await self._k8s_provider.close()
            except Exception as e:
                logger.debug("k8s_provider_cleanup_error", error=str(e))
            self._k8s_provider = None

        self._kaas_device_auth = None
        self._k8s_device_auth = None

    def build_auth_message(self) -> str:
        """Build user-facing authentication instructions.

        Returns:
            Formatted instructions string.
        """
        if not self._kaas_device_auth or not self._k8s_device_auth:
            return "Device flow not initialized."

        return (
            "Please complete authentication in your browser:\n\n"
            "STEP 1 - MCC Access (enter username and password):\n"
            f"  {self._kaas_device_auth.verification_uri_complete}\n\n"
            "STEP 2 - MOSK Access (click 'Allow' - already logged in via SSO):\n"
            f"  {self._k8s_device_auth.verification_uri_complete}\n\n"
            f"Codes expire in {self._kaas_device_auth.expires_in // 60} minutes.\n"
            "Waiting for authentication..."
        )


# =============================================================================
# Tool Functions
# =============================================================================


# Module-level manager storage (per session)
_managers: dict[int, DeviceFlowLoginManager] = {}


def _get_manager(session: UserSession, settings: Settings) -> DeviceFlowLoginManager:
    """Get or create manager for session."""
    session_id = id(session)
    if session_id not in _managers:
        _managers[session_id] = DeviceFlowLoginManager(settings, session)
    return _managers[session_id]


def _clear_manager(session: UserSession) -> None:
    """Clear manager for session."""
    session_id = id(session)
    _managers.pop(session_id, None)


async def device_flow_login(
    session: UserSession,
    settings: Settings,
    input_data: DeviceFlowLoginInput,
) -> DeviceFlowInitOutput | DeviceFlowCompleteOutput:
    """Initiate secure Device Flow authentication.

    Authenticates to both MCC and MOSK clusters using OAuth 2.0 Device
    Authorization Grant. User authenticates once with password (kaas),
    then clicks "Allow" for MOSK access (k8s) via SSO.

    Args:
        session: User session to authenticate.
        settings: Application settings.
        input_data: Login parameters.

    Returns:
        DeviceFlowCompleteOutput with authentication result.
    """
    manager = _get_manager(session, settings)

    # If already authenticated, return status
    if session.state.authenticated:
        return DeviceFlowCompleteOutput(
            status=ModelDeviceFlowStatus.COMPLETED,
            success=True,
            username=session.state.username,
            message=f"Already authenticated as {session.state.username}",
            iam_roles=session.state.iam_roles,
            mcc_authenticated=True,
            mosk_authenticated=session._mosk_cluster_name is not None,
        )

    # Perform dual device flow authentication
    result = await manager.authenticate(input_data)

    # Clear manager after successful auth
    _clear_manager(session)

    return result


async def device_flow_login_start(
    session: UserSession,
    settings: Settings,
    input_data: DeviceFlowLoginInput,
) -> DeviceFlowInitOutput:
    """Start Device Flow authentication (non-blocking).

    This is provided for compatibility but the main login function
    handles the complete flow including polling.

    Args:
        session: User session to authenticate.
        settings: Application settings.
        input_data: Login parameters.

    Returns:
        DeviceFlowInitOutput with verification URLs.
    """
    # For now, redirect to main login function
    # The non-blocking flow can be implemented if needed
    manager = _get_manager(session, settings)

    # Discover endpoints
    await manager._discover_endpoints()
    await manager._discover_mosk_cluster(input_data)
    await manager._initiate_device_flows()

    # Return init output with both URLs
    return DeviceFlowInitOutput(
        status=ModelDeviceFlowStatus.AWAITING_USER,
        user_code=manager._kaas_device_auth.user_code if manager._kaas_device_auth else "",
        verification_uri=manager._kaas_device_auth.verification_uri
        if manager._kaas_device_auth
        else "",
        verification_uri_complete=manager._kaas_device_auth.verification_uri_complete
        if manager._kaas_device_auth
        else "",
        expires_in=manager._kaas_device_auth.expires_in if manager._kaas_device_auth else 0,
        message=manager.build_auth_message(),
        poll_interval=manager._kaas_device_auth.interval if manager._kaas_device_auth else 5,
    )


async def device_flow_login_complete(
    session: UserSession,
    settings: Settings,
    input_data: DeviceFlowCompleteInput | None = None,
) -> DeviceFlowCompleteOutput:
    """Complete Device Flow authentication.

    Polls for token completion after device_flow_login_start.

    Args:
        session: User session to authenticate.
        settings: Application settings.
        input_data: Completion parameters.

    Returns:
        DeviceFlowCompleteOutput with authentication result.
    """
    manager = _get_manager(session, settings)

    if not manager.is_flow_active:
        return DeviceFlowCompleteOutput(
            status=ModelDeviceFlowStatus.ERROR,
            success=False,
            message="No active device flow. Please call login first.",
        )

    # Poll for tokens
    try:
        kaas_tokens, k8s_tokens = await manager._poll_device_flows()
        result = await manager._establish_session(kaas_tokens, k8s_tokens)
        _clear_manager(session)
        return result

    except Exception as e:
        _clear_manager(session)
        return DeviceFlowCompleteOutput(
            status=ModelDeviceFlowStatus.ERROR,
            success=False,
            message=f"Authentication failed: {e}",
        )


# Module exports
__all__ = [
    "DeviceFlowLoginManager",
    "ManagedTokens",
    "device_flow_login",
    "device_flow_login_complete",
    "device_flow_login_start",
    "refresh_tokens",
]
