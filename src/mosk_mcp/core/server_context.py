"""Enterprise-ready Server Context for MOSK MCP Server.

This module provides a production-grade ServerContext that manages:
- Dual Kubernetes cluster connections (MCC and MOSK)
- Connection health monitoring with circuit breakers
- Automatic reconnection with exponential backoff
- Response caching with TTL
- Metrics collection and observability
- Graceful shutdown with resource cleanup
- Thread-safe lazy initialization

The ServerContext is designed for enterprise deployments with:
- High availability requirements
- Observability and monitoring
- Security and audit compliance
- Graceful degradation under failure

Architecture:
    ServerContext
    ├── ConnectionManager (MCC)
    │   ├── KubernetesAdapter
    │   ├── CircuitBreaker
    │   └── HealthMonitor
    ├── ConnectionManager (MOSK)
    │   ├── KubernetesAdapter
    │   ├── CircuitBreaker
    │   └── HealthMonitor
    ├── ResponseCache
    ├── MetricsCollector
    └── AuditLogger
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from mosk_mcp.core.connection import (
    ClusterType,
    ConnectionManager,
    ConnectionMetrics,
    ConnectionState,
    HealthStatus,
)
from mosk_mcp.core.exceptions import (
    MoskMCPError,
    RateLimitError,
    UnsupportedVersionError,
)
from mosk_mcp.infrastructure.cache import ResponseCache
from mosk_mcp.infrastructure.ratelimit import RateLimiter, RateLimitExceeded, set_rate_limiter
from mosk_mcp.infrastructure.version_checker import (
    MIN_SUPPORTED_VERSION_STR,
    MOSKVersionInfo,
    clear_cached_version_info,
    get_mosk_version,
    set_cached_version_info,
)
from mosk_mcp.observability.logging import get_logger


if TYPE_CHECKING:
    from mosk_mcp.adapters.kubernetes import KubernetesAdapter
    from mosk_mcp.adapters.stacklight import DirectStackLightClient
    from mosk_mcp.auth.rbac import RBACEnforcer
    from mosk_mcp.auth.session import UserSession
    from mosk_mcp.core.config import Settings
    from mosk_mcp.observability.audit import AuditLogger


logger = get_logger(__name__)


# Re-export from connection module for backward compatibility
__all__ = [
    "ClusterType",
    "ConnectionManager",
    "ConnectionMetrics",
    "ConnectionState",
    "HealthStatus",
    "SSOServerContext",
    "ServerContextConfig",
    "create_sso_server_context",
]


# =============================================================================
# Server Context Configuration
# =============================================================================


@dataclass
class ServerContextConfig:
    """Configuration for ServerContext.

    Attributes:
        cache_ttl_seconds: Default cache TTL.
        cache_max_entries: Maximum cache entries.
        circuit_breaker_failure_threshold: Failures before opening circuit.
        circuit_breaker_recovery_timeout: Recovery timeout in seconds.
        health_check_interval: Health check interval in seconds.
        enable_health_monitoring: Enable background health monitoring.
        enable_cache_cleanup: Enable background cache cleanup.
        max_reconnect_attempts: Max reconnection attempts.
    """

    cache_ttl_seconds: float = 30.0
    cache_max_entries: int = 1000
    circuit_breaker_failure_threshold: int = 5
    circuit_breaker_recovery_timeout: float = 30.0
    health_check_interval: float = 60.0
    enable_health_monitoring: bool = True
    enable_cache_cleanup: bool = True
    max_reconnect_attempts: int = 5


# =============================================================================
# SSO Server Context
# =============================================================================


class SSOServerContext:
    """SSO-authenticated server context for MOSK MCP Server.

    This context uses Keycloak OIDC for per-user authentication instead of
    static kubeconfig files. Each user authenticates with their own credentials
    and gets their own session with user-scoped permissions.

    Features:
    - Per-user OIDC authentication via Keycloak
    - Dynamic kubeconfig generation from OIDC tokens
    - User-scoped Kubernetes API access
    - User-scoped StackLight access via DirectStackLightClient
    - Session management with token refresh
    - Audit logging tied to authenticated user

    Architecture:
        SSOServerContext
        ├── UserSession (manages authenticated state)
        │   ├── OIDC Tokens (id_token, access_token, refresh_token)
        │   ├── KubernetesAdapter (MCC) - from OIDC kubeconfig
        │   ├── KubernetesAdapter (MOSK) - from OIDC kubeconfig
        │   └── DirectStackLightClient
        ├── ResponseCache (shared)
        └── AuditLogger

    Example:
        ```python
        # Create context
        context = SSOServerContext(settings)
        await context.initialize()

        # Authenticate via device flow (browser-based, supports MFA)
        result = await context.login_with_device_flow()

        # Use authenticated adapters
        mcc = await context.get_mcc_adapter()
        mosk = await context.get_mosk_adapter()

        # Shutdown
        await context.shutdown()
        ```
    """

    def __init__(
        self,
        settings: Settings,
        config: ServerContextConfig | None = None,
    ) -> None:
        """Initialize SSO server context.

        Args:
            settings: Application settings with Keycloak configuration.
            config: Optional context configuration.

        Raises:
            ConfigurationError: If SSO is not enabled or configured.
        """
        self.settings = settings
        self.config = config or ServerContextConfig()

        # User session (created on authenticate)
        self._session: UserSession | None = None

        # Response cache (shared across requests)
        self._cache = ResponseCache(
            default_ttl_seconds=self.config.cache_ttl_seconds,
            max_entries=self.config.cache_max_entries,
        )

        # Shared services
        self._audit_logger: AuditLogger | None = None
        self._rbac_enforcer: RBACEnforcer | None = None

        # Rate limiting
        self._rate_limiter: RateLimiter | None = None

        # MOSK version info (checked after login)
        self._mosk_version_info: MOSKVersionInfo | None = None

        # Lifecycle state
        self._initialized = False
        self._shutdown = False
        self._shutdown_event = asyncio.Event()
        self._start_time = datetime.now(UTC)

        # Device flow authentication state (set during login_secure/login_start)
        # Protected by _device_flow_lock to prevent TOCTOU race conditions
        self._device_flow_manager: Any = None
        self._device_flow_poll_task: asyncio.Task[Any] | None = None
        self._device_flow_lock = asyncio.Lock()

        logger.debug(
            "sso_server_context_created",
            keycloak_url=settings.keycloak_url,
            realm=settings.keycloak_realm,
        )

    @property
    def session(self) -> UserSession:
        """Get current user session.

        Returns:
            UserSession instance.

        Raises:
            MoskMCPError: If not authenticated.
        """
        if self._session is None:
            raise MoskMCPError(
                "Not authenticated. Use login_secure tool to authenticate.",
                error_code="NOT_AUTHENTICATED",
            )
        return self._session

    @property
    def is_authenticated(self) -> bool:
        """Check if user is authenticated."""
        return (
            self._session is not None
            and self._session.state.authenticated
            and not self._session.state.is_token_expired()
        )

    @property
    def mosk_version_info(self) -> MOSKVersionInfo | None:
        """Get MOSK version information.

        Returns:
            MOSKVersionInfo if version has been checked, None otherwise.
        """
        return self._mosk_version_info

    @property
    def is_mosk_version_supported(self) -> bool:
        """Check if MOSK version is supported (>= 25.1).

        Returns:
            True if version is supported, False if unsupported or unknown.
        """
        if self._mosk_version_info is None:
            return False
        return self._mosk_version_info.is_compatible

    async def check_mosk_version(
        self,
        cluster_name: str = "mos",
        namespace: str = "default",
    ) -> MOSKVersionInfo:
        """Check MOSK version and block if unsupported.

        This method is called after successful authentication to verify
        the cluster version meets minimum requirements (25.1+).

        IMPORTANT: This method will raise an exception if the MOSK version
        is not supported, effectively blocking login to unsupported clusters.

        Args:
            cluster_name: Name of the MOSK cluster CR.
            namespace: Namespace of the cluster CR.

        Returns:
            MOSKVersionInfo with version details.

        Raises:
            UnsupportedVersionError: If MOSK version is < 25.1 or cannot be determined.
        """
        try:
            mcc_adapter = await self.get_mcc_adapter()
            version_info = await get_mosk_version(
                mcc_adapter=mcc_adapter,
                cluster_name=cluster_name,
                namespace=namespace,
            )

            self._mosk_version_info = version_info
            set_cached_version_info(version_info)

            # Block if version is explicitly unsupported (< 25.1)
            if version_info.is_unsupported:
                logger.error(
                    "mosk_version_unsupported",
                    detected_version=version_info.version_string,
                    required_version=MIN_SUPPORTED_VERSION_STR,
                )
                raise UnsupportedVersionError(
                    message=(
                        f"MOSK version {version_info.version_string} is not supported. "
                        f"This MCP requires MOSK {MIN_SUPPORTED_VERSION_STR} or later. "
                        f"Please upgrade your MOSK cluster before using this tool."
                    ),
                    detected_version=version_info.version_string,
                    required_version=MIN_SUPPORTED_VERSION_STR,
                )

            # Block if version is unknown (could not detect)
            if not version_info.is_compatible:
                logger.error(
                    "mosk_version_unknown",
                    cluster_name=cluster_name,
                    namespace=namespace,
                )
                raise UnsupportedVersionError(
                    message=(
                        f"Could not determine MOSK version for cluster '{cluster_name}' "
                        f"in namespace '{namespace}'. "
                        f"This MCP requires MOSK {MIN_SUPPORTED_VERSION_STR} or later. "
                        f"Please verify your cluster version manually."
                    ),
                    detected_version=None,
                    required_version=MIN_SUPPORTED_VERSION_STR,
                    details={
                        "cluster_name": cluster_name,
                        "namespace": namespace,
                        "raw_data": version_info.raw_data,
                    },
                )

            # Version is supported
            logger.info(
                "mosk_version_supported",
                version=version_info.version_string,
                cluster_release=version_info.cluster_release,
            )
            return version_info

        except UnsupportedVersionError:
            # Re-raise version errors
            raise

        except Exception as e:
            logger.error("mosk_version_check_failed", error=str(e))
            raise UnsupportedVersionError(
                message=(
                    f"Failed to check MOSK version: {e}. "
                    f"This MCP requires MOSK {MIN_SUPPORTED_VERSION_STR} or later. "
                    f"Cannot proceed without version verification."
                ),
                detected_version=None,
                required_version=MIN_SUPPORTED_VERSION_STR,
                details={"error": str(e)},
            ) from e

    async def initialize(self) -> None:
        """Initialize server context."""
        if self._initialized:
            return

        logger.info("sso_server_context_initializing")

        # Initialize rate limiter
        self._rate_limiter = RateLimiter(
            settings=self.settings,
            enabled=self.settings.rate_limit_enabled,
        )
        set_rate_limiter(self._rate_limiter)
        logger.info(
            "rate_limiter_initialized",
            enabled=self.settings.rate_limit_enabled,
            requests_per_minute=self.settings.rate_limit_requests_per_minute,
            burst_size=self.settings.rate_limit_burst_size,
        )

        # Start cache cleanup task
        if self.config.enable_cache_cleanup:
            await self._cache.start_cleanup_task()

        self._initialized = True
        logger.info("sso_server_context_initialized")

    @classmethod
    async def create(
        cls,
        settings: Settings,
        config: ServerContextConfig | None = None,
    ) -> SSOServerContext:
        """Create and initialize SSO server context.

        Factory method for async initialization.

        Args:
            settings: Application settings.
            config: Optional context configuration.

        Returns:
            Initialized SSOServerContext.
        """
        context = cls(settings, config)
        await context.initialize()
        return context

    async def login_with_device_flow(
        self,
        mosk_cluster_name: str | None = None,
        mosk_namespace: str = "default",
        auto_discover_mosk: bool = True,
    ) -> dict[str, Any]:
        """Authenticate user with Keycloak using Device Flow (secure).

        Device Flow authentication does NOT require typing passwords in chat.
        Instead, returns a verification URL and code for the user to complete
        authentication in their browser (supports MFA/2FA).

        This is the RECOMMENDED authentication method for enterprise use.

        Args:
            mosk_cluster_name: MOSK cluster name for cluster auth.
            mosk_namespace: Namespace where MOSK cluster is defined.
            auto_discover_mosk: If True, auto-discover MOSK cluster.

        Returns:
            Dict with device flow result:
            - If pending: {status: "awaiting_user", user_code, verification_uri, ...}
            - If completed: {status: "completed", username, iam_roles, ...}

        Raises:
            AuthenticationError: If device flow initiation fails.
        """
        from mosk_mcp.auth.session import UserSession
        from mosk_mcp.tools.auth.device_flow_login import (
            DeviceFlowLoginInput,
            DeviceFlowLoginManager,
        )

        # Create new session if none exists
        if self._session is None:
            self._session = UserSession(
                settings=self.settings,
                keycloak_url=self.settings.keycloak_url,
                realm=self.settings.keycloak_realm,
                mcc_client_id=self.settings.mcc_oidc_client_id,
            )

        # Create device flow manager
        manager = DeviceFlowLoginManager(self.settings, self._session)

        # Create input
        input_data = DeviceFlowLoginInput(
            mosk_cluster_name=mosk_cluster_name,
            mosk_namespace=mosk_namespace,
            auto_discover_mosk=auto_discover_mosk,
        )

        # Initiate and complete device flow
        init_result = await manager.initiate(input_data)

        logger.info(
            "device_flow_initiated",
            user_code=init_result.user_code,
            verification_uri=init_result.verification_uri,
        )

        # Poll for completion (blocking)
        complete_result = await manager.complete()

        if complete_result.success:
            logger.info(
                "device_flow_login_success",
                username=complete_result.username,
                mosk_authenticated=complete_result.mosk_authenticated,
            )

            # Check MOSK version after successful login
            version_info = await self.check_mosk_version(
                cluster_name=mosk_cluster_name or "mos",
                namespace=mosk_namespace,
            )

            # Add version warnings to result
            result = complete_result.model_dump()
            result["mosk_version"] = version_info.to_dict()
            return result

        return complete_result.model_dump()

    async def logout(self) -> None:
        """Logout current user session."""
        if self._session is not None:
            await self._session.logout()
            self._session = None
            self._mosk_version_info = None
            clear_cached_version_info()
            logger.info("sso_logout")

    async def refresh_tokens(self) -> bool:
        """Refresh OIDC tokens if needed.

        Returns:
            True if tokens are valid (refreshed or still valid).
        """
        if self._session is None:
            return False
        return await self._session.refresh_tokens()

    async def get_mcc_adapter(self) -> KubernetesAdapter:
        """Get MCC Kubernetes adapter.

        Returns authenticated adapter from user session.

        Returns:
            Connected KubernetesAdapter for MCC cluster.

        Raises:
            MoskMCPError: If not authenticated.
            AuthenticationError: If adapter creation fails.
        """
        self._check_shutdown()
        await self._ensure_authenticated()
        return await self.session.get_mcc_adapter()

    async def get_mosk_adapter(self) -> KubernetesAdapter:
        """Get MOSK Kubernetes adapter.

        Returns authenticated adapter from user session.

        Returns:
            Connected KubernetesAdapter for MOSK cluster.

        Raises:
            MoskMCPError: If not authenticated.
            AuthenticationError: If adapter creation fails.
        """
        self._check_shutdown()
        await self._ensure_authenticated()
        return await self.session.get_mosk_adapter()

    async def get_stacklight_client(
        self,
        prometheus_url: str | None = None,
        alertmanager_url: str | None = None,
    ) -> DirectStackLightClient:
        """Get StackLight client.

        Returns authenticated DirectStackLightClient from user session.

        Args:
            prometheus_url: Override Prometheus URL.
            alertmanager_url: Override Alertmanager URL.

        Returns:
            Authenticated DirectStackLightClient.

        Raises:
            MoskMCPError: If not authenticated.
            AuthenticationError: If client creation fails.
        """
        self._check_shutdown()
        await self._ensure_authenticated()
        return await self.session.get_stacklight_client(
            prometheus_url=prometheus_url,
            alertmanager_url=alertmanager_url,
        )

    async def set_device_flow_poll_task(self, task: asyncio.Task[Any] | None) -> None:
        """Set the device flow poll task with proper synchronization.

        Cancels any existing poll task before setting a new one to prevent
        orphaned tasks.

        Args:
            task: The new poll task, or None to clear.
        """
        async with self._device_flow_lock:
            # Cancel existing task if any
            old_task = self._device_flow_poll_task
            if old_task is not None and not old_task.done():
                logger.debug("cancelling_existing_device_flow_poll_task")
                old_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await old_task
            self._device_flow_poll_task = task

    async def get_device_flow_poll_task(self) -> asyncio.Task[Any] | None:
        """Get the current device flow poll task with proper synchronization.

        Returns:
            The current poll task, or None if no poll is in progress.
        """
        async with self._device_flow_lock:
            return self._device_flow_poll_task

    async def _wait_for_device_flow_completion(self) -> None:
        """Wait for background device flow polling to complete.

        If a device flow authentication is in progress (background polling),
        waits for it to complete before proceeding with authentication checks.

        This prevents race conditions where tool calls are made while the user
        is still completing browser-based authentication.

        Uses _device_flow_lock to ensure thread-safe access to the poll task.
        """
        # Acquire lock to get a consistent view of the task
        async with self._device_flow_lock:
            poll_task: asyncio.Task[Any] | None = self._device_flow_poll_task

            if poll_task is None or poll_task.done():
                return

            logger.info("waiting_for_device_flow_poll_to_complete")

        # Wait outside the lock to avoid blocking other operations
        try:
            # Wait for background poll to complete (5 min timeout)
            await asyncio.wait_for(poll_task, timeout=300)
            logger.info("device_flow_poll_completed")
        except TimeoutError:
            logger.warning("device_flow_poll_timeout_in_ensure_authenticated")
            # Cancel the orphaned task to prevent resource leaks
            poll_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await poll_task
            # Clear the task reference
            async with self._device_flow_lock:
                if self._device_flow_poll_task is poll_task:
                    self._device_flow_poll_task = None
            # Continue - will fail the auth check in subsequent steps
        except asyncio.CancelledError:
            logger.debug("device_flow_poll_cancelled")
            # Ensure consistent cleanup for CancelledError (same as Exception path)
            # Cancel the task if not done and clear the reference to prevent resource leaks
            if not poll_task.done():
                poll_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await poll_task
            # Clear the task reference
            async with self._device_flow_lock:
                if self._device_flow_poll_task is poll_task:
                    self._device_flow_poll_task = None
            # Continue - will fail the auth check in subsequent steps
        except Exception as e:
            logger.warning("device_flow_poll_error", error=str(e))
            # Cancel the task on any error to prevent resource leaks
            if not poll_task.done():
                poll_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await poll_task
            # Clear the task reference
            async with self._device_flow_lock:
                if self._device_flow_poll_task is poll_task:
                    self._device_flow_poll_task = None
            # Continue - will fail the auth check in subsequent steps

    def _verify_session_exists(self) -> None:
        """Verify that a session exists and user was authenticated.

        Checks if session exists and user was authenticated at some point,
        even if token is now expired (we may be able to refresh it).

        Raises:
            MoskMCPError: If no session exists or user was never authenticated.
        """
        if self._session is None or not self._session.state.authenticated:
            raise MoskMCPError(
                "Not authenticated. Use login_secure tool to authenticate.",
                error_code="NOT_AUTHENTICATED",
            )

    async def _ensure_valid_tokens(self) -> None:
        """Ensure tokens are valid, refreshing if necessary.

        Tries to refresh tokens BEFORE checking is_authenticated.
        This is critical because is_authenticated returns False when tokens
        are expired, and we need to refresh first to avoid "session expired" errors.

        Raises:
            MoskMCPError: If tokens are expired and refresh fails.
        """
        # Session must exist (verified by _verify_session_exists)
        assert self._session is not None

        # Check if tokens are expired (with 60s buffer)
        if self._session.state.is_token_expired(buffer_seconds=60):
            logger.info(
                "token_expired_attempting_refresh",
                token_expires_at=self._session.state.token_expires_at.isoformat()
                if self._session.state.token_expires_at
                else None,
            )

            try:
                refreshed = await self.refresh_tokens()

                if refreshed:
                    logger.info("token_refresh_successful")

                    # Validate tokens are actually not expired after refresh
                    # This catches clock skew or Keycloak configuration issues
                    if self._session.state.is_token_expired(buffer_seconds=5):
                        logger.error(
                            "token_still_expired_after_refresh",
                            token_expires_at=self._session.state.token_expires_at.isoformat()
                            if self._session.state.token_expires_at
                            else None,
                        )
                        raise MoskMCPError(
                            "Token refresh succeeded but tokens still expired. "
                            "Please check system clock synchronization.",
                            error_code="TOKEN_REFRESH_INVALID",
                        )
                else:
                    logger.warning("token_refresh_returned_false")
                    raise MoskMCPError(
                        "Session expired and token refresh failed. Please login again.",
                        error_code="TOKEN_REFRESH_FAILED",
                    )

            except MoskMCPError:
                raise  # Re-raise our own errors
            except Exception as e:
                logger.warning("token_refresh_exception", error=str(e))
                raise MoskMCPError(
                    f"Session expired and token refresh failed: {e}. Please login again.",
                    error_code="TOKEN_REFRESH_FAILED",
                ) from e

        # Final verification that authentication is valid (should pass after refresh)
        if not self.is_authenticated:
            raise MoskMCPError(
                "Not authenticated or session expired. Use login_secure tool to authenticate.",
                error_code="NOT_AUTHENTICATED",
            )

    async def _enforce_rate_limits(self) -> None:
        """Enforce rate limits for authenticated user.

        Determines user role based on IAM roles and checks rate limits.
        Role hierarchy: administrator > operator > viewer.

        Raises:
            RateLimitError: If rate limit is exceeded.
        """
        # Only enforce if rate limiter is enabled and session exists
        if not (self._rate_limiter and self._rate_limiter.enabled and self._session):
            return

        try:
            # Get user ID from session state
            user_id = self._session.state.username or "unknown"

            # Determine role based on IAM roles (default to operator if can't determine)
            role_name = "operator"  # Default for authenticated users
            iam_roles = self._session.state.iam_roles or []

            if any("admin" in r.lower() for r in iam_roles):
                role_name = "administrator"
            elif any("viewer" in r.lower() or "readonly" in r.lower() for r in iam_roles):
                role_name = "viewer"

            # Check rate limit
            await self._rate_limiter.check_rate_limit(
                user_id=user_id,
                role_name=role_name,
            )

        except RateLimitExceeded as e:
            logger.warning(
                "rate_limit_exceeded",
                user=self._session.state.username,
                retry_after=e.retry_after,
                limit=e.limit,
            )
            raise RateLimitError(
                message=e.args[0],
                retry_after=int(e.retry_after) if e.retry_after else None,
                details={"limit": e.limit, "current": e.current},
            ) from e

    async def _ensure_authenticated(self) -> None:
        """Ensure user is authenticated with valid tokens.

        Orchestrates the authentication flow:
        1. Waits for device flow completion if in progress
        2. Verifies session exists
        3. Ensures tokens are valid (refreshing if needed)
        4. Enforces rate limits

        Raises:
            MoskMCPError: If not authenticated or tokens expired.
            RateLimitError: If rate limit is exceeded.
        """
        # Step 1: Wait for any in-progress device flow to complete
        await self._wait_for_device_flow_completion()

        # Step 2: Verify session exists and user was authenticated
        self._verify_session_exists()

        # Step 3: Ensure tokens are valid, refresh if necessary
        await self._ensure_valid_tokens()

        # Step 4: Enforce rate limits based on user role
        await self._enforce_rate_limits()

    def _check_shutdown(self) -> None:
        """Check if context is shutting down.

        Raises:
            MoskMCPError: If context is shutting down.
        """
        if self._shutdown:
            raise MoskMCPError(
                "Server context is shutting down",
                error_code="SERVER_SHUTTING_DOWN",
            )

    @property
    def cache(self) -> ResponseCache:
        """Get response cache."""
        return self._cache

    @property
    def rate_limiter(self) -> RateLimiter | None:
        """Get rate limiter instance."""
        return self._rate_limiter

    @property
    def audit_logger(self) -> AuditLogger:
        """Get audit logger instance."""
        if self._audit_logger is None:
            from mosk_mcp.observability.audit import AuditLogger

            self._audit_logger = AuditLogger.from_settings(self.settings)
        return self._audit_logger

    @property
    def rbac_enforcer(self) -> RBACEnforcer:
        """Get RBAC enforcer instance."""
        if self._rbac_enforcer is None:
            from mosk_mcp.auth.rbac import RBACEnforcer

            self._rbac_enforcer = RBACEnforcer()
        return self._rbac_enforcer

    async def shutdown(self, timeout: float | None = None) -> None:
        """Shutdown server context and cleanup resources.

        Args:
            timeout: Maximum time to wait for cleanup.
        """
        if self._shutdown:
            return

        self._shutdown = True
        timeout = timeout or self.settings.shutdown_timeout

        logger.info("sso_server_context_shutting_down", timeout=timeout)

        try:
            # Logout user session
            await self.logout()

            # Stop cache cleanup
            await self._cache.stop_cleanup_task()

            # Clear cache
            await self._cache.clear()

            self._shutdown_event.set()
            logger.info("sso_server_context_shutdown_complete")

        except Exception as e:
            logger.error("sso_server_context_shutdown_error", error=str(e))

    async def wait_for_shutdown(self) -> None:
        """Wait for shutdown to complete."""
        await self._shutdown_event.wait()

    def get_status(self) -> dict[str, Any]:
        """Get server context status summary.

        Returns:
            Status information including session state, MOSK version, and rate limiting.
        """
        session_status = {}
        if self._session:
            session_status = self._session.get_status()

        version_status = {}
        if self._mosk_version_info:
            version_status = self._mosk_version_info.to_dict()

        rate_limit_status = {
            "enabled": self.settings.rate_limit_enabled,
            "requests_per_minute": self.settings.rate_limit_requests_per_minute,
            "burst_size": self.settings.rate_limit_burst_size,
        }

        return {
            "mode": "sso",
            "initialized": self._initialized,
            "shutdown": self._shutdown,
            "start_time": self._start_time.isoformat(),
            "uptime_seconds": (datetime.now(UTC) - self._start_time).total_seconds(),
            "session": session_status,
            "mosk_version": version_status,
            "cache": self._cache.metrics,
            "rate_limiting": rate_limit_status,
            "settings": {
                "keycloak_url": self.settings.keycloak_url,
                "realm": self.settings.keycloak_realm,
                "auth_enabled": self.settings.auth_enabled,
                "audit_enabled": self.settings.audit_enabled,
            },
        }

    async def __aenter__(self) -> SSOServerContext:
        """Async context manager entry."""
        await self.initialize()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Async context manager exit."""
        await self.shutdown()


async def create_sso_server_context(
    settings: Settings | None = None,
    config: ServerContextConfig | None = None,
) -> SSOServerContext:
    """Create SSO server context with default settings.

    Factory function for SSO-authenticated server context.

    Args:
        settings: Application settings (uses get_settings() if None).
        config: Optional context configuration.

    Returns:
        Initialized SSOServerContext.

    Raises:
        ConfigurationError: If SSO is not enabled.
    """
    if settings is None:
        from mosk_mcp.core.config import get_settings

        settings = get_settings()

    context = SSOServerContext(settings, config)
    await context.initialize()
    return context
