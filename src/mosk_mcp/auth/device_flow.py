"""OAuth 2.0 Device Authorization Grant (RFC 8628) for Keycloak.

This module provides secure authentication for MCP tools without exposing
credentials in the chat window. Users authenticate via browser while MCP
polls for the token.

Flow:
    1. MCP requests device code from Keycloak
    2. User sees verification URL and code in chat (not sensitive)
    3. User opens browser, enters code, authenticates (supports MFA)
    4. MCP polls Keycloak until user completes authentication
    5. MCP receives tokens and establishes session

Security Benefits:
    - No credentials in chat history
    - Supports MFA/2FA
    - Browser-based auth with full Keycloak security
    - Industry standard (GitHub CLI, Azure CLI, kubectl)

Example:
    provider = DeviceFlowAuthProvider(
        keycloak_url="https://keycloak.example.com",
        realm="iam",
        client_id="kaas",
    )

    async with provider:
        # Start device flow
        device_auth = await provider.initiate_device_flow()
        print(f"Visit: {device_auth.verification_uri}")
        print(f"Enter code: {device_auth.user_code}")

        # Poll for token (blocks until user completes auth)
        tokens = await provider.poll_for_token(device_auth)
        print(f"Authenticated as: {tokens.username}")

References:
    - RFC 8628: https://datatracker.ietf.org/doc/html/rfc8628
    - Keycloak Device Flow: https://www.keycloak.org/docs/latest/server_admin/#_device-authorization-grant
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import httpx

from mosk_mcp.auth.keycloak_client import MCCEndpoints, TokenResponse
from mosk_mcp.core.exceptions import AuthenticationError
from mosk_mcp.observability.logging import get_logger
from mosk_mcp.tools.common.enums import DeviceFlowStatus


logger = get_logger(__name__)


@dataclass
class DeviceAuthorizationResponse:
    """Response from Keycloak device authorization endpoint.

    This contains the information to display to the user and
    the device_code used for polling.

    Attributes:
        device_code: Opaque code used when polling for token (not shown to user).
        user_code: Short code user enters in browser (e.g., "ABCD-EFGH").
        verification_uri: URL user visits to authenticate.
        verification_uri_complete: URL with user_code pre-filled (for QR codes).
        expires_in: Seconds until device_code expires.
        interval: Minimum seconds between poll attempts.
        issued_at: When this response was issued.
    """

    device_code: str
    user_code: str
    verification_uri: str
    verification_uri_complete: str
    expires_in: int
    interval: int = 5
    issued_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def expires_at(self) -> datetime:
        """Calculate expiration timestamp."""
        return self.issued_at + timedelta(seconds=self.expires_in)

    @property
    def is_expired(self) -> bool:
        """Check if device code has expired."""
        return datetime.now(UTC) >= self.expires_at

    @property
    def time_remaining(self) -> int:
        """Seconds remaining until expiration."""
        remaining = (self.expires_at - datetime.now(UTC)).total_seconds()
        return max(0, int(remaining))

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "user_code": self.user_code,
            "verification_uri": self.verification_uri,
            "verification_uri_complete": self.verification_uri_complete,
            "expires_in": self.expires_in,
            "time_remaining": self.time_remaining,
            "interval": self.interval,
        }


@dataclass
class DeviceFlowResult:
    """Result of device flow authentication attempt.

    Attributes:
        status: Current status of the flow.
        tokens: Token response if authentication succeeded.
        device_auth: Original device authorization response.
        error_message: Error description if authentication failed.
        error_code: OAuth error code if authentication failed.
    """

    status: DeviceFlowStatus
    tokens: TokenResponse | None = None
    device_auth: DeviceAuthorizationResponse | None = None
    error_message: str | None = None
    error_code: str | None = None

    @property
    def is_success(self) -> bool:
        """Check if authentication succeeded."""
        return self.status == DeviceFlowStatus.COMPLETED and self.tokens is not None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        result: dict[str, Any] = {
            "status": self.status.value,
            "success": self.is_success,
        }

        if self.device_auth:
            result["device_auth"] = self.device_auth.to_dict()

        if self.tokens:
            result["username"] = self.tokens.username
            result["expires_in"] = self.tokens.expires_in

        if self.error_message:
            result["error_message"] = self.error_message

        if self.error_code:
            result["error_code"] = self.error_code

        return result


# Type alias for progress callback
ProgressCallback = Callable[[DeviceFlowStatus, str], None]


class DeviceFlowAuthProvider:
    """OAuth 2.0 Device Authorization Grant provider for Keycloak.

    This provider implements RFC 8628 Device Authorization Grant,
    enabling secure authentication without exposing credentials.

    The provider is designed to be:
    - Enterprise-ready: Configurable timeouts, retry logic, proper error handling
    - Secure: No credentials in memory longer than necessary
    - Observable: Progress callbacks for UI integration
    - Resilient: Handles network issues, rate limiting, expiration

    Attributes:
        keycloak_url: Keycloak server base URL.
        realm: Keycloak realm name.
        client_id: OAuth client ID (must have device flow enabled).

    Example:
        provider = DeviceFlowAuthProvider(
            keycloak_url="https://keycloak.example.com",
            realm="iam",
            client_id="kaas",
        )

        async with provider:
            result = await provider.authenticate()
            if result.is_success:
                print(f"Authenticated as {result.tokens.username}")
    """

    # OAuth 2.0 Device Flow error codes (RFC 8628 Section 3.5)
    ERROR_AUTHORIZATION_PENDING = "authorization_pending"
    ERROR_SLOW_DOWN = "slow_down"
    ERROR_ACCESS_DENIED = "access_denied"
    ERROR_EXPIRED_TOKEN = "expired_token"

    def __init__(
        self,
        keycloak_url: str,
        realm: str,
        client_id: str,
        *,
        timeout: float = 30.0,
        verify_ssl: bool = True,
        default_scope: str = "openid profile email offline_access",
    ) -> None:
        """Initialize Device Flow provider.

        Args:
            keycloak_url: Keycloak server base URL (e.g., "https://keycloak.example.com").
            realm: Keycloak realm name (e.g., "iam").
            client_id: OAuth client ID with device flow enabled.
            timeout: HTTP request timeout in seconds.
            verify_ssl: Whether to verify SSL certificates.
            default_scope: Default OAuth scopes to request.
        """
        self.keycloak_url = keycloak_url.rstrip("/")
        self.realm = realm
        self.client_id = client_id
        self._timeout = timeout
        self._verify_ssl = verify_ssl
        self._default_scope = default_scope
        self._http_client: httpx.AsyncClient | None = None

        # Computed endpoints
        self._base_url = f"{self.keycloak_url}/auth/realms/{self.realm}"

    @classmethod
    def from_mcc_endpoints(
        cls,
        endpoints: MCCEndpoints,
        client_id: str | None = None,
        *,
        verify_ssl: bool = True,
    ) -> DeviceFlowAuthProvider:
        """Create provider from discovered MCC endpoints.

        Args:
            endpoints: Discovered MCC endpoints.
            client_id: OAuth client ID (defaults to endpoints.keycloak_client_id).
            verify_ssl: Whether to verify SSL certificates.

        Returns:
            Configured DeviceFlowAuthProvider.
        """
        return cls(
            keycloak_url=endpoints.keycloak_url,
            realm=endpoints.keycloak_realm,
            client_id=client_id or endpoints.keycloak_client_id,
            verify_ssl=verify_ssl,
        )

    @property
    def device_authorization_endpoint(self) -> str:
        """Get device authorization endpoint URL."""
        return f"{self._base_url}/protocol/openid-connect/auth/device"

    @property
    def token_endpoint(self) -> str:
        """Get token endpoint URL."""
        return f"{self._base_url}/protocol/openid-connect/token"

    @property
    def issuer_url(self) -> str:
        """Get OIDC issuer URL."""
        return self._base_url

    async def __aenter__(self) -> DeviceFlowAuthProvider:
        """Async context manager entry - create HTTP client."""
        await self.init_client()
        return self

    async def __aexit__(self, *args: Any) -> None:
        """Async context manager exit - close HTTP client."""
        await self.close()

    async def init_client(self) -> None:
        """Initialize HTTP client.

        Call this before using the provider if not using context manager.
        """
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(
                verify=self._verify_ssl,
                timeout=httpx.Timeout(self._timeout),
            )

    async def close(self) -> None:
        """Close HTTP client and cleanup resources."""
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None

    def _ensure_client(self) -> httpx.AsyncClient:
        """Ensure HTTP client is available.

        Returns:
            The HTTP client.

        Raises:
            RuntimeError: If not initialized.
        """
        if not self._http_client:
            raise RuntimeError(
                "DeviceFlowAuthProvider not initialized. "
                "Call init_client() or use 'async with' context manager."
            )
        return self._http_client

    async def initiate_device_flow(
        self,
        scope: str | None = None,
    ) -> DeviceAuthorizationResponse:
        """Start device authorization flow.

        Requests a device code from Keycloak. The response contains
        the user_code and verification_uri to display to the user.

        Args:
            scope: OAuth scopes to request (uses default_scope if None).

        Returns:
            DeviceAuthorizationResponse with user_code and verification_uri.

        Raises:
            AuthenticationError: If device flow initiation fails.
        """
        client = self._ensure_client()

        logger.info(
            "device_flow_initiating",
            client_id=self.client_id,
            endpoint=self.device_authorization_endpoint,
        )

        try:
            response = await client.post(
                self.device_authorization_endpoint,
                data={
                    "client_id": self.client_id,
                    "scope": scope or self._default_scope,
                },
            )

            if response.status_code != 200:
                error_data = self._parse_error_response(response)
                error_msg = error_data.get("error_description", response.text)
                error_code = error_data.get("error", "device_flow_failed")

                logger.error(
                    "device_flow_initiation_failed",
                    status_code=response.status_code,
                    error=error_msg,
                    error_code=error_code,
                )

                # Provide helpful error messages
                if error_code == "unauthorized_client":
                    raise AuthenticationError(
                        "Device Flow is not enabled for this client. "
                        "Enable 'OAuth 2.0 Device Authorization Grant' in Keycloak client settings.",
                        details={
                            "client_id": self.client_id,
                            "error_code": error_code,
                            "hint": "See docs/KEYCLOAK_DEVICE_FLOW_SETUP.md",
                        },
                    )

                raise AuthenticationError(
                    f"Failed to initiate device flow: {error_msg}",
                    details={
                        "client_id": self.client_id,
                        "error_code": error_code,
                        "status_code": response.status_code,
                    },
                )

            data = response.json()

            device_auth = DeviceAuthorizationResponse(
                device_code=data["device_code"],
                user_code=data["user_code"],
                verification_uri=data["verification_uri"],
                verification_uri_complete=data.get(
                    "verification_uri_complete",
                    f"{data['verification_uri']}?user_code={data['user_code']}",
                ),
                expires_in=data.get("expires_in", 600),
                interval=data.get("interval", 5),
            )

            logger.info(
                "device_flow_initiated",
                user_code=device_auth.user_code,
                verification_uri=device_auth.verification_uri,
                expires_in=device_auth.expires_in,
            )

            return device_auth

        except httpx.RequestError as e:
            logger.error("device_flow_request_error", error=str(e))
            raise AuthenticationError(
                f"Failed to connect to Keycloak: {e}",
                details={"endpoint": self.device_authorization_endpoint},
            ) from e

    async def poll_for_token(
        self,
        device_auth: DeviceAuthorizationResponse,
        *,
        progress_callback: ProgressCallback | None = None,
        max_attempts: int | None = None,
    ) -> TokenResponse:
        """Poll for token until user completes authentication.

        This method blocks until:
        - User completes authentication (returns tokens)
        - Device code expires (raises AuthenticationError)
        - User denies access (raises AuthenticationError)
        - Max attempts reached (raises AuthenticationError)
        - Wall-clock timeout exceeded (raises AuthenticationError)

        Args:
            device_auth: Response from initiate_device_flow().
            progress_callback: Optional callback for progress updates.
            max_attempts: Maximum poll attempts (None = unlimited until expiry).

        Returns:
            TokenResponse with access_token, id_token, refresh_token.

        Raises:
            AuthenticationError: If authentication fails or times out.
        """
        client = self._ensure_client()
        interval = device_auth.interval
        attempts = 0
        network_errors = 0
        max_network_errors = 10  # Limit consecutive network errors

        # Add wall-clock timeout to prevent infinite loops on network errors
        # Use 150% of device code lifetime as absolute maximum
        import time

        start_time = time.monotonic()
        max_duration = device_auth.expires_in * 1.5

        logger.info(
            "device_flow_polling_started",
            user_code=device_auth.user_code,
            expires_in=device_auth.expires_in,
            max_duration=max_duration,
        )

        if progress_callback:
            progress_callback(
                DeviceFlowStatus.POLLING,
                f"Waiting for user to authenticate (code: {device_auth.user_code})",
            )

        while True:
            # Check wall-clock timeout first (catches network error loops)
            elapsed = time.monotonic() - start_time
            if elapsed > max_duration:
                logger.warning(
                    "device_flow_wall_clock_timeout",
                    user_code=device_auth.user_code,
                    elapsed_seconds=elapsed,
                    max_duration=max_duration,
                )
                if progress_callback:
                    progress_callback(
                        DeviceFlowStatus.EXPIRED,
                        "Authentication timeout. Please try again.",
                    )
                raise AuthenticationError(
                    "Device flow polling timeout exceeded. Please initiate a new authentication.",
                    details={
                        "user_code": device_auth.user_code,
                        "elapsed_seconds": elapsed,
                        "max_duration": max_duration,
                    },
                )

            # Check expiration
            if device_auth.is_expired:
                logger.warning("device_flow_expired", user_code=device_auth.user_code)
                if progress_callback:
                    progress_callback(
                        DeviceFlowStatus.EXPIRED,
                        "Device code expired. Please try again.",
                    )
                raise AuthenticationError(
                    "Device code expired. Please initiate a new authentication.",
                    details={"user_code": device_auth.user_code},
                )

            # Check max attempts
            if max_attempts and attempts >= max_attempts:
                logger.warning(
                    "device_flow_max_attempts",
                    attempts=attempts,
                    user_code=device_auth.user_code,
                )
                raise AuthenticationError(
                    f"Maximum poll attempts ({max_attempts}) reached.",
                    details={"attempts": attempts},
                )

            # Wait before polling
            await asyncio.sleep(interval)
            attempts += 1

            try:
                response = await client.post(
                    self.token_endpoint,
                    data={
                        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                        "device_code": device_auth.device_code,
                        "client_id": self.client_id,
                    },
                )

                if response.status_code == 200:
                    # Success! User authenticated
                    data = response.json()
                    tokens = TokenResponse(
                        access_token=data["access_token"],
                        id_token=data["id_token"],
                        refresh_token=data["refresh_token"],
                        token_type=data.get("token_type", "Bearer"),
                        expires_in=data.get("expires_in", 1800),
                        refresh_expires_in=data.get("refresh_expires_in", 3600),
                        scope=data.get("scope", ""),
                    )

                    logger.info(
                        "device_flow_completed",
                        username=tokens.username,
                        attempts=attempts,
                    )

                    if progress_callback:
                        progress_callback(
                            DeviceFlowStatus.COMPLETED,
                            f"Authenticated as {tokens.username}",
                        )

                    return tokens

                # Handle error responses
                error_data = self._parse_error_response(response)
                error_code = error_data.get("error", "")

                if error_code == self.ERROR_AUTHORIZATION_PENDING:
                    # User hasn't completed auth yet - continue polling
                    logger.debug(
                        "device_flow_pending",
                        attempts=attempts,
                        time_remaining=device_auth.time_remaining,
                    )
                    continue

                if error_code == self.ERROR_SLOW_DOWN:
                    # Server wants us to slow down - increase interval
                    interval += 5
                    logger.info(
                        "device_flow_slow_down",
                        new_interval=interval,
                    )
                    continue

                if error_code == self.ERROR_ACCESS_DENIED:
                    logger.warning("device_flow_access_denied")
                    if progress_callback:
                        progress_callback(
                            DeviceFlowStatus.DENIED,
                            "User denied access.",
                        )
                    raise AuthenticationError(
                        "User denied access.",
                        details={"error_code": error_code},
                    )

                if error_code == self.ERROR_EXPIRED_TOKEN:
                    logger.warning("device_flow_token_expired")
                    if progress_callback:
                        progress_callback(
                            DeviceFlowStatus.EXPIRED,
                            "Device code expired.",
                        )
                    raise AuthenticationError(
                        "Device code expired. Please initiate a new authentication.",
                        details={"error_code": error_code},
                    )

                # Unknown error
                error_msg = error_data.get("error_description", response.text)
                logger.error(
                    "device_flow_poll_error",
                    error_code=error_code,
                    error=error_msg,
                )
                if progress_callback:
                    progress_callback(
                        DeviceFlowStatus.ERROR,
                        f"Authentication error: {error_msg}",
                    )
                raise AuthenticationError(
                    f"Device flow authentication failed: {error_msg}",
                    details={"error_code": error_code},
                )

            except httpx.RequestError as e:
                # Network error - log and retry with limit
                network_errors += 1
                logger.warning(
                    "device_flow_poll_network_error",
                    error=str(e),
                    attempts=attempts,
                    network_errors=network_errors,
                    max_network_errors=max_network_errors,
                )
                # Limit consecutive network errors to prevent infinite loops
                if network_errors >= max_network_errors:
                    if progress_callback:
                        progress_callback(
                            DeviceFlowStatus.ERROR,
                            f"Too many network errors ({network_errors}). Please check connectivity.",
                        )
                    raise AuthenticationError(
                        f"Device flow polling failed after {network_errors} consecutive network errors",
                        details={
                            "error": str(e),
                            "network_errors": network_errors,
                            "attempts": attempts,
                        },
                    ) from e
                # Retry after network error
                continue

    async def authenticate(
        self,
        scope: str | None = None,
        *,
        progress_callback: ProgressCallback | None = None,
    ) -> DeviceFlowResult:
        """Perform complete device flow authentication.

        This is a convenience method that combines initiate_device_flow()
        and poll_for_token() into a single call.

        Note: This method blocks until authentication completes. For
        non-blocking usage, use initiate_device_flow() and poll_for_token()
        separately.

        Args:
            scope: OAuth scopes to request.
            progress_callback: Optional callback for progress updates.

        Returns:
            DeviceFlowResult with status and tokens (if successful).
        """
        try:
            # Initiate device flow
            device_auth = await self.initiate_device_flow(scope)

            if progress_callback:
                progress_callback(
                    DeviceFlowStatus.PENDING,
                    f"Please visit {device_auth.verification_uri} and enter code: {device_auth.user_code}",
                )

            # Poll for token
            tokens = await self.poll_for_token(
                device_auth,
                progress_callback=progress_callback,
            )

            return DeviceFlowResult(
                status=DeviceFlowStatus.COMPLETED,
                tokens=tokens,
                device_auth=device_auth,
            )

        except AuthenticationError as e:
            # Determine status from error
            status = DeviceFlowStatus.ERROR
            if "expired" in str(e).lower():
                status = DeviceFlowStatus.EXPIRED
            elif "denied" in str(e).lower():
                status = DeviceFlowStatus.DENIED

            return DeviceFlowResult(
                status=status,
                error_message=str(e),
                error_code=e.details.get("error_code") if e.details else None,
            )

    def _parse_error_response(self, response: httpx.Response) -> dict[str, Any]:
        """Parse error response from Keycloak.

        Args:
            response: HTTP response.

        Returns:
            Parsed error data or empty dict.
        """
        try:
            if response.headers.get("content-type", "").startswith("application/json"):
                return cast("dict[str, Any]", response.json())
        except json.JSONDecodeError:
            # Expected for malformed JSON - Keycloak may return HTML error pages
            logger.debug(
                "device_flow_error_response_not_json",
                status_code=response.status_code,
                content_type=response.headers.get("content-type"),
            )
        except Exception as e:
            # Unexpected error parsing error response
            logger.debug(
                "device_flow_error_response_parse_failed",
                error_type=type(e).__name__,
                error=str(e),
            )
        return {}


# Module exports
__all__ = [
    "DeviceAuthorizationResponse",
    "DeviceFlowAuthProvider",
    "DeviceFlowResult",
    "DeviceFlowStatus",
    "ProgressCallback",
]
