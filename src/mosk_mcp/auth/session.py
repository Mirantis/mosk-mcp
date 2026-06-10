"""User Session Management for MOSK MCP Server.

This module provides user session management for SSO-authenticated access
to MOSK clusters. A UserSession holds the authenticated user's OIDC tokens
and provides access to:
- Kubernetes API (via OIDC kubeconfig)
- StackLight services (via DirectStackLightClient)

Architecture:
    UserSession
    ├── OIDC Tokens (id_token, access_token, refresh_token)
    ├── Generated Kubeconfigs (MCC and MOSK clusters)
    ├── KubernetesAdapter instances (created from OIDC kubeconfigs)
    └── DirectStackLightClient (for StackLight access)

Authentication Flow (Device Flow):
1. User initiates login via login_secure tool
2. User authenticates in browser (supports MFA/2FA)
3. Device Flow manager obtains OIDC tokens
4. UserSession stores tokens and managed token references
5. On demand, generates OIDC kubeconfigs for clusters
6. Creates KubernetesAdapter instances from generated kubeconfigs
7. Creates DirectStackLightClient for StackLight access

Example:
    # Session is authenticated via device_flow_login tools
    # Tokens are set externally after browser authentication

    # Use authenticated adapters
    mcc_adapter = await session.get_mcc_adapter()
    mosk_adapter = await session.get_mosk_adapter()

    # Use StackLight client
    stacklight_client = await session.get_stacklight_client()
"""

from __future__ import annotations

import asyncio
import atexit
import contextlib
import os
import tempfile
import weakref
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from mosk_mcp.auth.keycloak_client import (
    ClusterOIDCInfo,
    MCCEndpoints,
    TokenResponse,
    discover_stacklight_endpoints,
    generate_cluster_kubeconfig,
    get_iam_roles,
)
from mosk_mcp.core.exceptions import AuthenticationError, ConfigurationError
from mosk_mcp.observability.logging import get_logger


if TYPE_CHECKING:
    from mosk_mcp.adapters.kubernetes import KubernetesAdapter
    from mosk_mcp.adapters.stacklight import DirectStackLightClient
    from mosk_mcp.core.config import Settings

logger = get_logger(__name__)

# Track sessions for cleanup on process exit
# Using Any type to avoid forward reference issues
_sessions_to_cleanup: set[Any] = set()


def _cleanup_session_temp_files(session_ref: weakref.ref[UserSession]) -> None:
    """Cleanup function called by atexit to remove temp kubeconfig files.

    Args:
        session_ref: Weak reference to the session to clean up.

    Note:
        This function is called during interpreter shutdown (atexit),
        so we must suppress all exceptions to avoid tracebacks.
        Logging may not be available during shutdown, so we use stderr directly.

        We only use weakref for atexit - sessions are NOT added to
        _sessions_to_cleanup set (that would create strong references
        preventing garbage collection).
    """
    try:
        session = session_ref()
        if session is not None:
            session._cleanup_temp_files()
            # Note: We don't discard from _sessions_to_cleanup because
            # sessions are never added to it (only weakrefs are used)
    except Exception as e:
        # Suppress all exceptions during interpreter shutdown
        # File operations can fail if filesystem is already unmounted
        # or logging is already shut down
        # Best-effort: try to write to stderr before giving up
        try:
            import sys

            print(f"MOSK MCP: Session cleanup error (non-fatal): {e}", file=sys.stderr)
        except Exception:
            pass


@dataclass
class SessionState:
    """State tracking for user session.

    Attributes:
        authenticated: Whether user is authenticated.
        authenticated_at: When authentication occurred.
        last_activity: Last activity timestamp.
        token_expires_at: When tokens expire.
        username: Authenticated username.
        iam_roles: User's IAM roles.

    Invariants:
        If authenticated=True:
        - authenticated_at must be set
        - token_expires_at must be set
        - username must be set
    """

    authenticated: bool = False
    authenticated_at: datetime | None = None
    last_activity: datetime | None = None
    token_expires_at: datetime | None = None
    username: str | None = None
    iam_roles: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Validate session state consistency.

        Raises:
            ValueError: If authenticated=True but required fields are missing.
        """
        if self.authenticated:
            missing_fields = []
            if self.authenticated_at is None:
                missing_fields.append("authenticated_at")
            if self.token_expires_at is None:
                missing_fields.append("token_expires_at")
            if self.username is None:
                missing_fields.append("username")

            if missing_fields:
                raise ValueError(
                    f"SessionState: authenticated=True requires {missing_fields} to be set. "
                    "Cannot have an authenticated session without knowing when auth occurred, "
                    "when tokens expire, and who is authenticated."
                )

    def is_token_expired(self, buffer_seconds: int = 60) -> bool:
        """Check if token is expired or will expire soon.

        Args:
            buffer_seconds: Buffer time before actual expiry.

        Returns:
            True if token is expired or will expire within buffer time.
        """
        if not self.authenticated or self.token_expires_at is None:
            return True

        expiry_with_buffer = self.token_expires_at - timedelta(seconds=buffer_seconds)
        return datetime.now(UTC) >= expiry_with_buffer


class TokenBasedAuthAdapter:
    """Adapter to provide auth_provider interface using session tokens.

    This adapter wraps a UserSession's tokens to provide the get_valid_id_token()
    interface required by DirectStackLightClient.

    The adapter holds a reference to the session and delegates token access
    and refresh to the session's token management.

    Thread Safety:
        Uses the session's lock to prevent race conditions during token refresh.
        Multiple concurrent calls will be serialized to avoid duplicate refreshes.
    """

    def __init__(self, session: UserSession) -> None:
        """Initialize adapter with session reference.

        Args:
            session: "UserSession" with authenticated tokens.
        """
        self._session = session

    async def get_valid_id_token(self) -> str:
        """Get valid id_token, refreshing if needed.

        Thread Safety:
            This method is thread-safe. The expiry check and refresh are
            performed atomically under the session lock to prevent race
            conditions where multiple callers could trigger concurrent refreshes.

        Returns:
            Valid id_token string for Bearer authentication.

        Raises:
            AuthenticationError: If not authenticated or refresh fails.
        """
        # Acquire session lock to prevent race condition where multiple callers
        # all see expired token and try to refresh simultaneously.
        # The lock ensures check-and-refresh is atomic.
        async with self._session._lock:
            if self._session.state.is_token_expired():
                # Call internal refresh method (lock is already held by us)
                # Note: _refresh_tokens_unlocked expects caller to hold the lock
                await self._session._refresh_tokens_unlocked()

            if not self._session._mcc_tokens or not self._session._mcc_tokens.id_token:
                raise AuthenticationError(
                    "No valid id_token available",
                    details={"username": self._session.state.username},
                )

            return self._session._mcc_tokens.id_token


class UserSession:
    """Authenticated user session for MOSK MCP Server.

    Manages OIDC-based authentication and provides access to:
    - MCC Kubernetes API (management cluster)
    - MOSK Kubernetes API (workload cluster)
    - StackLight services (Prometheus, Alertmanager)

    All access is authenticated using the user's OIDC tokens from Keycloak,
    ensuring user-scoped permissions and audit trails.

    Authentication:
        Uses OAuth 2.0 Device Flow for secure browser-based authentication.
        Tokens are obtained via the login_secure/login_start tools and
        stored in the session for adapter access.

    Auto-Discovery:
        By default, only the MCC UI URL is required. All other endpoints
        (Keycloak, K8s API, StackLight) are auto-discovered from the MCC
        config.js endpoint, similar to how the browser UI works.

    Concurrency:
        This class uses a single asyncio.Lock (self._lock) to protect mutable state.

        Lock Ordering Rules:
        - There is only one lock in this class, so no ordering is needed internally.
        - External callers must ensure they don't hold other locks when calling
          public methods that acquire self._lock (get_mcc_adapter, get_mosk_adapter,
          get_stacklight_client, refresh_tokens, logout).

        Methods That Acquire self._lock:
        - get_mcc_adapter(): Acquires lock, may create/refresh adapters
        - get_mosk_adapter(): Acquires lock, may create/refresh adapters
        - get_stacklight_client(): Acquires lock, may create client
        - refresh_tokens(): Acquires lock to refresh OIDC tokens
        - logout(): Acquires lock to cleanup session state

        Methods That Expect Lock Already Held (internal use only):
        - _refresh_tokens_unlocked(): MUST be called with self._lock held
        - _save_kubeconfig_unlocked(): MUST be called with self._lock held

        These "_unlocked" methods are called by the TokenProvider during token refresh,
        which already holds the lock from the calling context.

    Attributes:
        settings: Application settings.
        state: Current session state.

    Example:
        # Session is authenticated via device_flow_login tools
        session = UserSession(settings)

        # Get adapters (after authentication)
        mcc = await session.get_mcc_adapter()
        mosk = await session.get_mosk_adapter()

        # Logout
        await session.logout()
    """

    def __init__(
        self,
        settings: Settings,
        mgmt_url: str | None = None,
        ssl_verify: bool | None = None,
        keycloak_url: str | None = None,
        realm: str | None = None,
        mcc_client_id: str | None = None,
    ) -> None:
        """Initialize user session.

        Args:
            settings: Application settings.
            mgmt_url: Management cluster UI URL (e.g., https://mgmt.example.com). Required for auto-discovery.
            ssl_verify: Override SSL verification setting (from cluster config).
            keycloak_url: Override Keycloak server URL (auto-discovered if None).
            realm: Override Keycloak realm name (auto-discovered if None).
            mcc_client_id: Override OIDC client ID (auto-discovered if None).
        """
        self.settings = settings
        self._mgmt_url = mgmt_url or settings.mgmt_url
        self._ssl_verify_override = ssl_verify

        # Optional overrides (normally auto-discovered)
        self._keycloak_url_override = keycloak_url or settings.keycloak_url
        self._realm_override = realm or settings.keycloak_realm
        self._mcc_client_id_override = mcc_client_id or settings.mcc_oidc_client_id

        # Validate required settings
        if not self._mgmt_url:
            raise ConfigurationError(
                "Management cluster URL not configured. Set MCP_MGMT_URL environment variable "
                "(e.g., https://mgmt.example.com). Keycloak and other endpoints "
                "will be auto-discovered from management cluster config.js.",
                config_key="mgmt_url",
            )

        # Discovered endpoints (populated during authenticate)
        self._mcc_endpoints: MCCEndpoints | None = None
        self._stacklight_endpoints: dict[str, str] | None = None

        # Authentication state
        self.state = SessionState()
        self._mcc_tokens: TokenResponse | None = None
        self._mosk_tokens: TokenResponse | None = None

        # Managed tokens for refresh capability (set by device flow login)
        # These contain client_id and issuer_url needed for token refresh
        self._mcc_managed_tokens: Any = None  # ManagedTokens from device_flow_login
        self._mosk_managed_tokens: Any = None  # ManagedTokens from device_flow_login

        # OIDC info for clusters
        self._mcc_oidc_info: ClusterOIDCInfo | None = None
        self._mosk_oidc_info: ClusterOIDCInfo | None = None

        # Generated kubeconfig files (temp files)
        self._mcc_kubeconfig_path: Path | None = None
        self._mosk_kubeconfig_path: Path | None = None

        # Cached adapters
        self._mcc_adapter: KubernetesAdapter | None = None
        self._mosk_adapter: KubernetesAdapter | None = None
        self._stacklight_client: DirectStackLightClient | None = None

        # Discovered MOSK cluster info (set during authenticate)
        self._mosk_cluster_name: str | None = None
        self._mosk_cluster_namespace: str | None = None

        # Discovered OSDPL info (set lazily on first use)
        self._osdpl_name: str | None = None
        self._osdpl_namespace: str = "openstack"

        # Lock for thread-safe operations on session state.
        # See class docstring "Concurrency" section for lock ordering rules.
        # Public methods acquire this lock; _unlocked() methods expect it held.
        self._lock = asyncio.Lock()

        # Register cleanup handler for temp files on process exit
        # Use weak reference to avoid preventing garbage collection
        self._register_cleanup()

        logger.debug(
            "user_session_created",
            mgmt_url=self._mgmt_url,
        )

    def _register_cleanup(self) -> None:
        """Register cleanup handler to remove temp files on process exit.

        Uses weakref to avoid preventing garbage collection of the session.
        Temp kubeconfig files contain OIDC tokens and must be cleaned up.

        Note:
            We only register the atexit handler with a weakref - we do NOT add
            the session to _sessions_to_cleanup set to avoid creating a strong
            reference that would prevent garbage collection.
        """
        # Only use weakref for atexit to avoid memory leak from strong references
        # The cleanup function will be called during interpreter shutdown
        atexit.register(_cleanup_session_temp_files, weakref.ref(self))

    def _cleanup_temp_files(self) -> None:
        """Clean up temporary kubeconfig files.

        This is called both on logout and by atexit handler.
        Safe to call multiple times.
        """
        for path_attr in ["_mcc_kubeconfig_path", "_mosk_kubeconfig_path"]:
            path = getattr(self, path_attr, None)
            if path and isinstance(path, Path):
                try:
                    # Use missing_ok=True to avoid TOCTOU race condition
                    # where file could be deleted between exists() check and unlink()
                    path.unlink(missing_ok=True)
                    logger.debug("temp_kubeconfig_deleted", path=str(path))
                except Exception as e:
                    logger.warning(
                        "failed_to_delete_temp_kubeconfig",
                        path=str(path),
                        error=str(e),
                    )
                finally:
                    setattr(self, path_attr, None)

    @property
    def ssl_verify(self) -> bool:
        """Get effective SSL verify setting (override or settings).

        Uses explicit None check to properly handle cluster config override.
        """
        if self._ssl_verify_override is not None:
            return self._ssl_verify_override
        return self.settings.ssl_verify

    async def _discover_mosk_cluster(
        self,
        raise_on_error: bool = False,
    ) -> tuple[str | None, str | None, str | None]:
        """Discover MOSK cluster name and namespace from MCC.

        Uses the MCC adapter to list clusters and find the non-management
        cluster (the MOSK child cluster).

        Args:
            raise_on_error: If True, raise exceptions instead of returning None.
                           Useful when caller needs to know the exact failure reason.

        Returns:
            Tuple of (cluster_name, namespace, error_message).
            - On success: (cluster_name, namespace, None)
            - On failure: (None, None, error_message)

        Raises:
            AuthenticationError: If raise_on_error=True and adapter creation fails.
            MoskConnectionError: If raise_on_error=True and discovery fails.
        """
        error_message: str | None = None

        try:
            # Get MCC adapter (creates it if needed)
            # Note: We need to temporarily release the lock to avoid deadlock
            # since get_mcc_adapter also acquires the lock
            logger.debug("creating_mcc_adapter_for_discovery")
            try:
                mcc_adapter = await self._get_mcc_adapter_unlocked()
            except Exception as adapter_error:
                error_message = (
                    f"Failed to create MCC adapter: {adapter_error}. "
                    "This may indicate OIDC token issues or MCC connectivity problems."
                )
                logger.error(
                    "mcc_adapter_creation_failed_during_discovery",
                    error=str(adapter_error),
                    error_type=type(adapter_error).__name__,
                )
                if raise_on_error:
                    from mosk_mcp.core.exceptions import AuthenticationError

                    raise AuthenticationError(
                        message=error_message,
                        details={
                            "error": str(adapter_error),
                            "error_type": type(adapter_error).__name__,
                        },
                    ) from adapter_error
                return None, None, error_message

            logger.debug("mcc_adapter_created_for_discovery")

            # Use the adapter's discover method
            cluster_name, namespace = await mcc_adapter.discover_mosk_cluster_namespace()

            if cluster_name:
                logger.info(
                    "mosk_cluster_discovered",
                    cluster=cluster_name,
                    namespace=namespace,
                )
                return cluster_name, namespace, None

            # No cluster found - this is not an error, just informational
            logger.warning(
                "mosk_cluster_not_found",
                message="No non-management cluster found in MCC",
            )
            return None, None, "No MOSK cluster found in MCC"

        except Exception as e:
            error_message = (
                f"Failed to discover MOSK cluster: {e}. "
                "This may be due to OIDC kubeconfig issues or MCC API access problems."
            )
            # Log detailed error information to help diagnose discovery failures
            logger.error(
                "mosk_cluster_discovery_error",
                error=str(e),
                error_type=type(e).__name__,
                message=error_message,
            )
            if raise_on_error:
                from mosk_mcp.core.exceptions import MoskConnectionError

                raise MoskConnectionError(
                    message=error_message,
                    service="MCC",
                    details={
                        "error": str(e),
                        "error_type": type(e).__name__,
                    },
                ) from e
            return None, None, error_message

    async def _get_mcc_adapter_unlocked(self) -> KubernetesAdapter:
        """Get MCC adapter without acquiring lock (for internal use).

        This method is used during authentication when the lock is already held.

        Returns:
            KubernetesAdapter for MCC cluster.
        """
        if self._mcc_adapter is not None:
            return self._mcc_adapter

        # Generate OIDC kubeconfig
        kubeconfig_yaml = self._generate_mcc_kubeconfig()

        # Write to temp file with secure permissions using tempfile.mkstemp
        # for truly unique, unpredictable names (more secure than PID+id pattern)
        # Mode 0o600 = owner read/write only (secure)
        fd, temp_path = tempfile.mkstemp(
            prefix="mosk-mcp-kubeconfig-mcc-",
            suffix=".yaml",
            dir=tempfile.gettempdir(),
        )
        try:
            # Set file permissions to 0o600 (owner read/write only)
            os.fchmod(fd, 0o600)
            os.write(fd, kubeconfig_yaml.encode())
        except Exception:
            # Clean up file on write error
            os.close(fd)
            with contextlib.suppress(OSError):
                Path(temp_path).unlink()
            raise
        finally:
            os.close(fd)
        self._mcc_kubeconfig_path = Path(temp_path)

        # Create adapter (import here to avoid circular imports)
        from mosk_mcp.adapters.kubernetes import KubernetesAdapter

        # Create and connect adapter using local variable first.
        # Only assign to self._mcc_adapter AFTER successful connection.
        # This prevents leaving an unconnected adapter cached if connect() fails.
        # Wrap in try-except to cleanup temp file if connection fails.
        adapter = KubernetesAdapter(kubeconfig_path=self._mcc_kubeconfig_path)
        try:
            await adapter.connect()
        except Exception:
            # Cleanup temp kubeconfig file on connection failure
            if self._mcc_kubeconfig_path and self._mcc_kubeconfig_path.exists():
                with contextlib.suppress(OSError):
                    self._mcc_kubeconfig_path.unlink()
                self._mcc_kubeconfig_path = None
            raise
        self._mcc_adapter = adapter

        logger.info("mcc_adapter_created_from_oidc")
        return self._mcc_adapter

    def _parse_iam_roles(self, id_token: str) -> list[str]:
        """Parse IAM roles from ID token.

        Args:
            id_token: JWT ID token.

        Returns:
            List of IAM role strings.
        """
        return get_iam_roles(id_token)

    async def _refresh_tokens_unlocked(self) -> bool:
        """Refresh OIDC tokens if needed (internal, lock must be held by caller).

        IMPORTANT: This method MUST only be called with self._lock held.
        The '_unlocked' suffix indicates this method does not acquire the lock
        itself - the caller is responsible for holding the lock.

        DEADLOCK PREVENTION:
        - This method and all methods it calls must NEVER try to acquire self._lock
        - Callers must acquire self._lock BEFORE calling this method
        - Used by refresh_tokens(), get_mcc_adapter(), and TokenBasedAuthAdapter

        Returns:
            True if tokens were refreshed or still valid.

        Raises:
            AuthenticationError: If refresh fails.
        """
        if not self.state.authenticated:
            logger.debug("refresh_tokens_skipped_not_authenticated")
            return False

        # Check if refresh is needed (60 second buffer)
        is_expired = self.state.is_token_expired()
        logger.debug(
            "refresh_tokens_check",
            is_expired=is_expired,
            token_expires_at=self.state.token_expires_at.isoformat()
            if self.state.token_expires_at
            else None,
            has_mcc_managed_tokens=self._mcc_managed_tokens is not None,
            has_mosk_managed_tokens=self._mosk_managed_tokens is not None,
        )
        if not is_expired:
            return True

        logger.info("user_session_refreshing_tokens")

        try:
            # Device Flow managed tokens
            if self._mcc_managed_tokens:
                await self._refresh_device_flow_tokens()
                # Validate tokens were successfully refreshed
                if not self._mcc_tokens or not self._mcc_tokens.id_token:
                    logger.error("token_refresh_returned_invalid_tokens")
                    self.state.authenticated = False
                    raise AuthenticationError(
                        "Token refresh succeeded but returned invalid tokens",
                        details={"username": self.state.username},
                    )
                return True

            # No refresh method available - token expired but can't refresh
            logger.warning(
                "user_session_no_refresh_method",
                has_mcc_managed_tokens=self._mcc_managed_tokens is not None,
                has_mcc_tokens=self._mcc_tokens is not None,
            )
            self.state.authenticated = False
            raise AuthenticationError(
                "Session expired and no refresh method available. Please re-authenticate.",
                details={"username": self.state.username},
            )

        except Exception as e:
            logger.error("user_session_token_refresh_failed", error=str(e))
            self.state.authenticated = False
            raise AuthenticationError(
                f"Token refresh failed: {e}",
                details={"username": self.state.username},
            ) from e

    async def refresh_tokens(self) -> bool:
        """Refresh OIDC tokens if needed.

        Uses Device Flow managed tokens for refresh. Thread-safe wrapper
        around _refresh_tokens_unlocked().

        Returns:
            True if tokens were refreshed or still valid.

        Raises:
            AuthenticationError: If refresh fails.
        """
        async with self._lock:
            return await self._refresh_tokens_unlocked()

    async def _invalidate_cached_adapters_unlocked(self) -> None:
        """Invalidate all cached adapters (must hold lock).

        This method disconnects and clears all cached adapters and their
        temp kubeconfig files. Used when re-authenticating to ensure
        new tokens are used for all future adapter access.

        IMPORTANT: This method MUST only be called with self._lock held.
        """
        # Invalidate MCC adapter
        if self._mcc_adapter:
            try:
                await self._mcc_adapter.disconnect()
            except Exception as e:
                logger.debug("mcc_adapter_disconnect_error", error=str(e))
            self._mcc_adapter = None
            if self._mcc_kubeconfig_path and self._mcc_kubeconfig_path.exists():
                with contextlib.suppress(OSError):
                    self._mcc_kubeconfig_path.unlink()
                self._mcc_kubeconfig_path = None

        # Invalidate MOSK adapter
        if self._mosk_adapter:
            try:
                await self._mosk_adapter.disconnect()
            except Exception as e:
                logger.debug("mosk_adapter_disconnect_error", error=str(e))
            self._mosk_adapter = None
            if self._mosk_kubeconfig_path and self._mosk_kubeconfig_path.exists():
                with contextlib.suppress(OSError):
                    self._mosk_kubeconfig_path.unlink()
                self._mosk_kubeconfig_path = None

        # Invalidate StackLight client
        if self._stacklight_client:
            try:
                await self._stacklight_client.__aexit__(None, None, None)
            except Exception as e:
                logger.debug("stacklight_client_cleanup_error", error=str(e))
            self._stacklight_client = None
            self._stacklight_endpoints = None

        logger.debug("cached_adapters_invalidated")

    async def _refresh_device_flow_tokens(self) -> None:
        """Refresh tokens obtained via device flow.

        Refreshes both MCC (kaas) and MOSK (k8s) tokens.
        Invalidates cached adapters so they get regenerated with new tokens.
        """
        from mosk_mcp.tools.auth.device_flow_login import refresh_tokens

        # Refresh MCC tokens
        if self._mcc_managed_tokens:
            logger.debug("refreshing_mcc_tokens")
            new_tokens = await refresh_tokens(
                self._mcc_managed_tokens,
                verify_ssl=self.ssl_verify,
            )
            self._mcc_tokens = new_tokens
            self._mcc_managed_tokens.tokens = new_tokens

            # Validate token expiry before updating session state
            # If access_token_expiry is None, fall back to expires_in calculation
            if new_tokens.access_token_expiry is not None:
                self.state.token_expires_at = new_tokens.access_token_expiry
            elif new_tokens.expires_in is not None and new_tokens.expires_in > 0:
                # Fall back to calculating expiry from expires_in
                self.state.token_expires_at = datetime.now(UTC) + timedelta(
                    seconds=new_tokens.expires_in
                )
                logger.warning(
                    "token_expiry_fallback",
                    message="access_token_expiry was None, using expires_in",
                    expires_in=new_tokens.expires_in,
                )
            else:
                # No valid expiry information - set a conservative 5-minute expiry
                # to force another refresh soon and log a warning
                self.state.token_expires_at = datetime.now(UTC) + timedelta(minutes=5)
                logger.warning(
                    "token_expiry_missing",
                    message="No valid token expiry info, using 5-minute fallback",
                )

            # Validate token expiry is in the future (catches clock skew issues)
            now = datetime.now(UTC)
            if self.state.token_expires_at <= now:
                logger.error(
                    "token_refresh_expiry_in_past",
                    token_expires_at=self.state.token_expires_at.isoformat(),
                    current_time=now.isoformat(),
                    message="Refreshed token expiry is in the past - possible clock skew",
                )
                raise AuthenticationError(
                    "Token refresh failed: expiry time is in the past. "
                    "Please check system clock synchronization.",
                    details={
                        "token_expires_at": self.state.token_expires_at.isoformat(),
                        "current_time": now.isoformat(),
                    },
                )

            # Invalidate MCC adapter (will be regenerated with new token)
            if self._mcc_adapter:
                try:
                    await self._mcc_adapter.disconnect()
                except Exception as e:
                    logger.debug("mcc_adapter_disconnect_error_during_refresh", error=str(e))
                self._mcc_adapter = None
                # Delete temp kubeconfig
                if self._mcc_kubeconfig_path and self._mcc_kubeconfig_path.exists():
                    self._mcc_kubeconfig_path.unlink()
                    self._mcc_kubeconfig_path = None

        # Refresh MOSK tokens
        if self._mosk_managed_tokens:
            logger.debug("refreshing_mosk_tokens")
            new_tokens = await refresh_tokens(
                self._mosk_managed_tokens,
                verify_ssl=self.ssl_verify,
            )
            self._mosk_tokens = new_tokens
            self._mosk_managed_tokens.tokens = new_tokens

            # Invalidate MOSK adapter
            if self._mosk_adapter:
                try:
                    await self._mosk_adapter.disconnect()
                except Exception as e:
                    logger.debug("mosk_adapter_disconnect_error_during_refresh", error=str(e))
                self._mosk_adapter = None
                # Delete temp kubeconfig
                if self._mosk_kubeconfig_path and self._mosk_kubeconfig_path.exists():
                    self._mosk_kubeconfig_path.unlink()
                    self._mosk_kubeconfig_path = None

        logger.info("device_flow_tokens_refreshed")

    async def _create_adapter_from_kubeconfig(
        self,
        kubeconfig_yaml: str,
        cluster_name: str,
    ) -> tuple[KubernetesAdapter, Path]:
        """Create and connect a KubernetesAdapter from kubeconfig YAML.

        This helper method consolidates the common pattern of:
        1. Writing kubeconfig to secure temp file
        2. Creating KubernetesAdapter
        3. Connecting to cluster
        4. Cleaning up temp file on failure

        Args:
            kubeconfig_yaml: YAML string of the kubeconfig.
            cluster_name: Name for logging and temp file prefix.

        Returns:
            Tuple of (connected adapter, temp file path).

        Raises:
            Exception: If connection fails (temp file is cleaned up).
        """
        # Write to temp file with secure permissions using tempfile.mkstemp
        # for truly unique, unpredictable names (more secure than PID+id pattern)
        fd, temp_path = tempfile.mkstemp(
            prefix=f"mosk-mcp-kubeconfig-{cluster_name}-",
            suffix=".yaml",
            dir=tempfile.gettempdir(),
        )
        try:
            # Set file permissions to 0o600 (owner read/write only)
            os.fchmod(fd, 0o600)
            os.write(fd, kubeconfig_yaml.encode())
        except Exception:
            # Clean up file on write error
            os.close(fd)
            with contextlib.suppress(OSError):
                Path(temp_path).unlink()
            raise
        finally:
            os.close(fd)

        kubeconfig_path = Path(temp_path)

        # Create adapter
        from mosk_mcp.adapters.kubernetes import KubernetesAdapter

        # Create and connect adapter using local variable first.
        # Only return AFTER successful connection.
        # This prevents leaving an unconnected adapter cached if connect() fails.
        adapter = KubernetesAdapter(kubeconfig_path=kubeconfig_path)
        try:
            await adapter.connect()
        except Exception:
            # Cleanup temp kubeconfig file on connection failure
            if kubeconfig_path.exists():
                with contextlib.suppress(OSError):
                    kubeconfig_path.unlink()
            raise

        return adapter, kubeconfig_path

    async def get_mcc_adapter(self) -> KubernetesAdapter:
        """Get authenticated KubernetesAdapter for MCC cluster.

        Creates a KubernetesAdapter using OIDC kubeconfig generated
        from the user's tokens. Auto-refreshes tokens if expired.

        Returns:
            Authenticated KubernetesAdapter for MCC.

        Raises:
            AuthenticationError: If not authenticated.
        """
        self._ensure_authenticated()

        async with self._lock:
            # Auto-refresh tokens if expired (under same lock to avoid race)
            await self._refresh_tokens_unlocked()

            if self._mcc_adapter is not None:
                return self._mcc_adapter

            # Generate OIDC kubeconfig and create adapter
            kubeconfig_yaml = self._generate_mcc_kubeconfig()
            adapter, kubeconfig_path = await self._create_adapter_from_kubeconfig(
                kubeconfig_yaml, cluster_name="mcc"
            )
            self._mcc_adapter = adapter
            self._mcc_kubeconfig_path = kubeconfig_path

            logger.info("mcc_adapter_created_from_oidc")
            return self._mcc_adapter

    async def get_mosk_adapter(self) -> KubernetesAdapter:
        """Get authenticated KubernetesAdapter for MOSK cluster.

        Creates a KubernetesAdapter using OIDC kubeconfig generated
        from the user's tokens. Auto-refreshes tokens if expired.

        Returns:
            Authenticated KubernetesAdapter for MOSK.

        Raises:
            AuthenticationError: If not authenticated or MOSK not configured.
        """
        self._ensure_authenticated()

        async with self._lock:
            # Auto-refresh tokens if expired (under same lock to avoid race)
            await self._refresh_tokens_unlocked()

            if not self._mosk_tokens or not self._mosk_oidc_info:
                raise AuthenticationError(
                    "MOSK cluster authentication required. "
                    "Device Flow authentication completed but OIDC info for MOSK cluster "
                    "could not be retrieved. Check user permissions to read Cluster CR.",
                    details={
                        "username": self.state.username,
                        "has_mosk_tokens": self._mosk_tokens is not None,
                        "has_mosk_oidc_info": self._mosk_oidc_info is not None,
                        "mosk_cluster_name": self._mosk_cluster_name,
                    },
                )

            if self._mosk_adapter is not None:
                return self._mosk_adapter

            # Generate OIDC kubeconfig and create adapter
            kubeconfig_yaml = generate_cluster_kubeconfig(
                oidc_info=self._mosk_oidc_info,
                tokens=self._mosk_tokens,
                cluster_name="mosk",
            )
            adapter, kubeconfig_path = await self._create_adapter_from_kubeconfig(
                kubeconfig_yaml, cluster_name="mosk"
            )
            self._mosk_adapter = adapter
            self._mosk_kubeconfig_path = kubeconfig_path

            logger.info("mosk_adapter_created_from_oidc")
            return self._mosk_adapter

    async def get_stacklight_client(
        self,
        prometheus_url: str | None = None,
        alertmanager_url: str | None = None,
    ) -> DirectStackLightClient:
        """Get authenticated DirectStackLightClient.

        Creates a DirectStackLightClient using the user's OIDC tokens
        for authentication with StackLight IAM Proxy.

        Auto-Discovery:
            If URLs are not provided, attempts to discover them from
            the MCC environment in this order:
            1. Explicit parameters
            2. Settings overrides
            3. Auto-discovered from MCC StackLight endpoints

        Args:
            prometheus_url: Prometheus IAM proxy URL (auto-discovered if None).
            alertmanager_url: Alertmanager IAM proxy URL (auto-discovered if None).

        Returns:
            Authenticated DirectStackLightClient.

        Raises:
            AuthenticationError: If not authenticated.
        """
        self._ensure_authenticated()

        async with self._lock:
            if self._stacklight_client is not None:
                return self._stacklight_client

            from mosk_mcp.adapters.stacklight import DirectStackLightClient

            # Use TokenBasedAuthAdapter to provide token access for StackLight
            if not self._mcc_tokens:
                raise AuthenticationError(
                    "No authentication available for StackLight",
                    details={"username": self.state.username},
                )
            auth_adapter = TokenBasedAuthAdapter(self)

            # Determine Prometheus URL (explicit > settings override > auto-discover)
            prom_url = prometheus_url or self.settings.prometheus_url
            alert_url = alertmanager_url or self.settings.alertmanager_url

            # Auto-discover from MOSK cluster (StackLight runs on MOSK, not MCC)
            if not prom_url or not alert_url:
                if (
                    self._stacklight_endpoints is None
                    and self._mosk_oidc_info
                    and self._mosk_tokens
                ):
                    try:
                        self._stacklight_endpoints = await discover_stacklight_endpoints(
                            k8s_api_url=self._mosk_oidc_info.k8s_api_url,
                            id_token=self._mosk_tokens.id_token,
                            verify_ssl=self.ssl_verify,
                        )
                        logger.info(
                            "stacklight_endpoints_discovered",
                            endpoints=self._stacklight_endpoints,
                            source="mosk",
                        )
                    except Exception as e:
                        logger.warning(
                            "stacklight_discovery_failed",
                            error=str(e),
                        )
                        self._stacklight_endpoints = {}

                if self._stacklight_endpoints:
                    prom_url = prom_url or self._stacklight_endpoints.get("prometheus_url", "")
                    alert_url = alert_url or self._stacklight_endpoints.get("alertmanager_url", "")

            # Get OpenSearch URL (explicit > settings override > auto-discover)
            # Note: OpenSearch is accessed via Kibana IAM proxy which provides OpenSearch API access
            opensearch_url = self.settings.opensearch_url or ""
            if not opensearch_url and self._stacklight_endpoints:
                opensearch_url = self._stacklight_endpoints.get("opensearch_url", "")

            self._stacklight_client = DirectStackLightClient(
                auth_provider=auth_adapter,
                prometheus_url=prom_url or "",
                alertmanager_url=alert_url or "",
                opensearch_url=opensearch_url,
                verify_ssl=self.ssl_verify,
            )

            # Initialize the HTTP client by entering the async context manager
            # This creates the httpx.AsyncClient needed for API calls
            await self._stacklight_client.__aenter__()

            logger.info(
                "stacklight_client_created",
                prometheus_url=prom_url,
                alertmanager_url=alert_url,
                opensearch_url=opensearch_url,
            )
            return self._stacklight_client

    def _generate_mcc_kubeconfig(self) -> str:
        """Generate OIDC kubeconfig for MCC cluster.

        Returns:
            Kubeconfig YAML string.
        """
        if not self._mcc_tokens:
            raise AuthenticationError(
                "MCC tokens not available",
                details={"username": self.state.username},
            )

        if not self._mcc_endpoints:
            raise AuthenticationError(
                "MCC endpoints not discovered",
                details={"username": self.state.username},
            )

        # Build kubeconfig with OIDC auth using discovered K8s API URL
        mcc_api_url = self._mcc_endpoints.k8s_api_url

        # Build cluster config based on SSL settings
        cluster_config: dict[str, Any] = {"server": mcc_api_url}
        if not self.ssl_verify:
            cluster_config["insecure-skip-tls-verify"] = True

        kubeconfig = {
            "apiVersion": "v1",
            "kind": "Config",
            "current-context": "mcc-oidc",
            "clusters": [
                {
                    "name": "mcc",
                    "cluster": cluster_config,
                }
            ],
            "contexts": [
                {
                    "name": "mcc-oidc",
                    "context": {
                        "cluster": "mcc",
                        "user": "mcc-oidc-user",
                    },
                }
            ],
            "users": [
                {
                    "name": "mcc-oidc-user",
                    "user": {
                        "token": self._mcc_tokens.id_token,
                    },
                }
            ],
        }

        return yaml.dump(kubeconfig, default_flow_style=False)

    def _ensure_authenticated(self) -> None:
        """Ensure session is authenticated.

        Raises:
            AuthenticationError: If not authenticated.
        """
        if not self.state.authenticated:
            raise AuthenticationError(
                "Session not authenticated. Use login_secure tool to authenticate.",
                details={},
            )

        # Update last activity
        self.state.last_activity = datetime.now(UTC)

    async def logout(self) -> None:
        """Logout and cleanup session resources."""
        async with self._lock:
            logger.info("user_session_logout", username=self.state.username)

            # Disconnect adapters
            if self._mcc_adapter:
                try:
                    await self._mcc_adapter.disconnect()
                except Exception as e:
                    logger.warning(
                        "adapter_disconnect_failed_during_logout",
                        adapter="mcc",
                        error=str(e),
                    )
                self._mcc_adapter = None

            if self._mosk_adapter:
                try:
                    await self._mosk_adapter.disconnect()
                except Exception as e:
                    logger.warning(
                        "adapter_disconnect_failed_during_logout",
                        adapter="mosk",
                        error=str(e),
                    )
                self._mosk_adapter = None

            # Close StackLight client (exit the async context manager)
            if self._stacklight_client:
                try:
                    await self._stacklight_client.__aexit__(None, None, None)
                except Exception as e:
                    logger.warning(
                        "stacklight_client_close_failed_during_logout",
                        error=str(e),
                    )
                self._stacklight_client = None

            # Remove temp kubeconfig files (also done by atexit handler)
            self._cleanup_temp_files()
            _sessions_to_cleanup.discard(self)

            # Clear tokens and managed tokens
            self._mcc_tokens = None
            self._mosk_tokens = None
            self._mcc_managed_tokens = None
            self._mosk_managed_tokens = None

            # Clear discovered endpoints (keep for re-authentication)
            # Note: We don't clear _mcc_endpoints or _stacklight_endpoints
            # as they can be reused for re-authentication

            # Reset state
            self.state = SessionState()

    @property
    def mcc_endpoints(self) -> MCCEndpoints | None:
        """Get discovered MCC endpoints.

        Returns:
            MCCEndpoints if discovered, None otherwise.
        """
        return self._mcc_endpoints

    @property
    def mcc_k8s_api_url(self) -> str | None:
        """Get the discovered MCC K8s API URL.

        Returns:
            K8s API URL if discovered, None otherwise.
        """
        return self._mcc_endpoints.k8s_api_url if self._mcc_endpoints else None

    @property
    def mosk_cluster_name(self) -> str | None:
        """Get the discovered/configured MOSK cluster name.

        Returns:
            Cluster name if authenticated to MOSK, None otherwise.
        """
        return self._mosk_cluster_name

    @property
    def mosk_cluster_namespace(self) -> str | None:
        """Get the discovered/configured MOSK cluster namespace on MCC.

        Returns:
            Cluster namespace if authenticated to MOSK, None otherwise.
        """
        return self._mosk_cluster_namespace

    @property
    def osdpl_name(self) -> str | None:
        """Get the discovered/configured OpenStackDeployment name.

        Returns:
            OSDPL name if discovered, None otherwise.
        """
        return self._osdpl_name

    @property
    def osdpl_namespace(self) -> str:
        """Get the OpenStackDeployment namespace.

        Returns:
            OSDPL namespace (defaults to 'openstack').
        """
        return self._osdpl_namespace

    def set_osdpl_info(self, name: str, namespace: str = "openstack") -> None:
        """Set the discovered OSDPL info.

        Args:
            name: OSDPL name.
            namespace: OSDPL namespace.
        """
        self._osdpl_name = name
        self._osdpl_namespace = namespace
        logger.debug("osdpl_info_set", name=name, namespace=namespace)

    def get_status(self) -> dict[str, Any]:
        """Get session status.

        Returns:
            Dictionary with session status information.
        """
        return {
            "authenticated": self.state.authenticated,
            "username": self.state.username,
            "authenticated_at": (
                self.state.authenticated_at.isoformat() if self.state.authenticated_at else None
            ),
            "last_activity": (
                self.state.last_activity.isoformat() if self.state.last_activity else None
            ),
            "token_expires_at": (
                self.state.token_expires_at.isoformat() if self.state.token_expires_at else None
            ),
            "token_expired": self.state.is_token_expired(),
            "token_refresh_available": self._mcc_managed_tokens is not None,
            "iam_roles": self.state.iam_roles,
            "has_mcc_adapter": self._mcc_adapter is not None
            or self._mcc_tokens is not None,
            "has_mosk_adapter": self._mosk_adapter is not None
            or (self._mosk_tokens is not None and self._mosk_oidc_info is not None),
            "has_stacklight_client": self._stacklight_client is not None,
            "mgmt_url": self._mgmt_url,
            "mcc_k8s_api_url": self.mcc_k8s_api_url,
            "mosk_cluster_name": self._mosk_cluster_name,
        }

    async def __aenter__(self) -> UserSession:
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Async context manager exit."""
        await self.logout()
