"""Cluster manager with strict security controls.

SECURITY ARCHITECTURE:
1. Fingerprint Verification - Each cluster has a cryptographic fingerprint
   computed from its Keycloak issuer and K8s API URL. This prevents:
   - Connecting to wrong cluster if MCC URL is changed
   - DNS hijacking attacks
   - Accidental misconfigurations

2. Session Isolation - Each cluster has completely isolated:
   - Authentication tokens
   - Kubeconfig files
   - Cached adapters
   Switching clusters invalidates ALL previous session state.

3. Production Safety - Production clusters require:
   - Explicit confirmation to switch
   - HTTPS (no HTTP allowed)
   - SSL verification enabled
   - Cannot be accidentally removed

4. Cluster Locking - Critical clusters can be locked to prevent
   accidental switches during sensitive operations.

5. Audit Trail - All cluster operations are logged for compliance.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from mosk_mcp.cluster.config import (
    ClusterConfig,
    ClusterEnvironment,
    ClustersConfig,
)
from mosk_mcp.core.config import get_settings
from mosk_mcp.cluster.models import (
    AddClusterInput,
    AddClusterOutput,
    ClusterInfo,
    ClusterSwitchConfirmation,
    CurrentClusterOutput,
    ListClustersOutput,
    SwitchClusterInput,
    SwitchClusterOutput,
)


if TYPE_CHECKING:
    from mosk_mcp.auth.session import SessionManager

logger = logging.getLogger(__name__)


class ClusterSecurityError(Exception):
    """Raised when a cluster operation violates security rules."""

    pass


class ClusterFingerprintMismatchError(ClusterSecurityError):
    """Raised when cluster fingerprint doesn't match expected value."""

    pass


class ClusterLockedError(ClusterSecurityError):
    """Raised when trying to switch from a locked cluster."""

    pass


class ProductionConfirmationRequired(ClusterSecurityError):
    """Raised when production switch requires confirmation."""

    def __init__(self, confirmation: ClusterSwitchConfirmation):
        self.confirmation = confirmation
        super().__init__(confirmation.warning_message)


class ClusterManager:
    """Manages cluster configurations with security controls.

    Thread-safe cluster management with:
    - Config file persistence
    - Session isolation
    - Fingerprint verification
    - Audit logging
    """

    def __init__(
        self,
        config_path: Path | None = None,
        profile_override: str | None = None,
        session_manager: SessionManager | None = None,
    ):
        """Initialize cluster manager.

        Args:
            config_path: Path to clusters.yaml (defaults to None)
            profile_override: Active cluster id override
            session_manager: Session manager for auth state (optional)
            
        """
        self._config_path = config_path
        self._profile_override = profile_override
        self._session_manager = session_manager
        self._config: ClustersConfig | None = None
        self._lock = asyncio.Lock()

        # Track authenticated state per cluster
        self._authenticated_clusters: dict[str, bool] = {}

        logger.info(
            "cluster_manager_initialized",
            extra={
                "config_path": str(self._config_path),
                "config_exists": self._config_path.exists(),
            },
        )

    @property
    def config_path(self) -> Path:
        """Get the config file path."""
        return self._config_path

    async def _load_config(self) -> ClustersConfig:
        """Load config from file, creating default if needed."""
        if self._config is not None:
            return self._config

        async with self._lock:
            # Double-check after acquiring lock
            if self._config is not None:
                return self._config

            if self._config_path.exists():
                self._config = ClustersConfig.from_yaml_file(self._config_path)
                logger.info(
                    "cluster_config_loaded",
                    extra={
                        "path": str(self._config_path),
                        "cluster_count": len(self._config.clusters),
                        "active": self._config.active,
                    },
                )
            else:
                self._config = ClustersConfig()
                logger.info(
                    "cluster_config_created_default",
                    extra={"path": str(self._config_path)},
                )

            # Apply profile override from settings (or constructor)
            profile_override = self._profile_override
            if (
                profile_override
                and profile_override in self._config.clusters
                and self._config.active != profile_override
            ):
                logger.info(
                    "cluster_profile_override",
                    extra={
                        "profile": profile_override,
                        "previous": self._config.active,
                    },
                )
                self._config.active = profile_override

            return self._config

    async def _save_config(self) -> None:
        """Save config to file."""
        if self._config is None:
            return

        async with self._lock:
            self._config.to_yaml_file(self._config_path)
            logger.info(
                "cluster_config_saved",
                extra={
                    "path": str(self._config_path),
                    "cluster_count": len(self._config.clusters),
                },
            )

    async def get_config(self) -> ClustersConfig:
        """Get the current configuration."""
        return await self._load_config()

    async def list_clusters(self) -> ListClustersOutput:
        """List all configured clusters.

        Returns cluster information with safety indicators.
        """
        config = await self._load_config()

        clusters: list[ClusterInfo] = []
        for cluster_id, cluster in config.clusters.items():
            is_active = cluster_id == config.active
            is_authenticated = self._authenticated_clusters.get(cluster_id, False)

            clusters.append(
                ClusterInfo(
                    id=cluster_id,
                    name=cluster.display_name,
                    url=cluster.url,
                    environment=cluster.environment,
                    ssl_verify=cluster.ssl_verify,
                    is_active=is_active,
                    is_authenticated=is_authenticated,
                    is_locked=cluster.is_locked,
                    has_fingerprint=cluster.fingerprint is not None,
                    description=cluster.description,
                    last_used_at=cluster.last_used_at,
                )
            )

        # Sort: active first, then by name
        clusters.sort(key=lambda c: (not c.is_active, c.name.lower()))

        # Check if active cluster is production
        _active_id, active_cluster = config.get_active_cluster()
        active_is_prod = (
            active_cluster.environment == ClusterEnvironment.PRODUCTION if active_cluster else False
        )

        # Generate warning if needed
        warning = None
        if active_is_prod:
            warning = (
                "WARNING: Active cluster is PRODUCTION. Operations will affect production systems."
            )
        elif not config.active and clusters:
            warning = "No active cluster selected. Use switch_cluster to select a cluster."

        return ListClustersOutput(
            active_cluster=config.active,
            clusters=clusters,
            total_count=len(clusters),
            active_is_production=active_is_prod,
            warning=warning,
        )

    async def switch_cluster(
        self,
        input: SwitchClusterInput,
    ) -> SwitchClusterOutput:
        """Switch to a different cluster.

        SECURITY CHECKS:
        1. Target cluster must exist
        2. Current cluster must not be locked (unless force=True)
        3. Production clusters require confirmation
        4. Previous session is completely invalidated
        """
        config = await self._load_config()

        # Validate target cluster exists
        if input.cluster_id not in config.clusters:
            available = list(config.clusters.keys())
            raise ValueError(
                f"Cluster '{input.cluster_id}' not found. Available clusters: {available}"
            )

        target_cluster = config.clusters[input.cluster_id]
        previous_cluster = config.active

        # Check if already on this cluster
        if config.active == input.cluster_id:
            is_auth = self._authenticated_clusters.get(input.cluster_id, False)
            return SwitchClusterOutput(
                success=True,
                previous_cluster=previous_cluster,
                new_cluster=input.cluster_id,
                new_cluster_url=target_cluster.url,
                new_cluster_environment=target_cluster.environment,
                requires_login=not is_auth,
                session_cleared=False,
                message=f"Already on cluster '{input.cluster_id}'.",
                warnings=[],
            )

        # SECURITY: Check if current cluster is locked
        if config.is_cluster_locked() and not input.force:
            current = config.active
            raise ClusterLockedError(
                f"Current cluster '{current}' is LOCKED. "
                f"This is a safety feature to prevent accidental switches. "
                f"To unlock: ask to unlock cluster '{current}' first, "
                f"or use force=True (dangerous)."
            )

        # SECURITY: Production clusters require confirmation
        if (
            target_cluster.requires_switch_confirmation
            and not input.confirm_production
            and config.confirm_production_switch
        ):
            confirmation = ClusterSwitchConfirmation(
                requires_confirmation=True,
                target_cluster=input.cluster_id,
                target_environment=target_cluster.environment,
                warning_message=(
                    f"PRODUCTION CLUSTER WARNING\n"
                    f"You are about to switch to '{input.cluster_id}' "
                    f"which is a {target_cluster.environment.value.upper()} cluster.\n"
                    f"URL: {target_cluster.url}\n\n"
                    f"Operations on this cluster will affect PRODUCTION systems.\n"
                    f"To confirm, call switch_cluster with confirm_production=True"
                ),
                confirmation_phrase=f"switch to {input.cluster_id} production",
            )

            return SwitchClusterOutput(
                success=False,
                previous_cluster=previous_cluster,
                new_cluster=input.cluster_id,
                new_cluster_url=target_cluster.url,
                new_cluster_environment=target_cluster.environment,
                requires_login=True,
                session_cleared=False,
                message="Production cluster requires confirmation.",
                warnings=[
                    f"Target cluster '{input.cluster_id}' is PRODUCTION",
                    "Set confirm_production=True to proceed",
                ],
                confirmation_required=confirmation,
            )

        # SECURITY: Clear ALL previous session state
        await self._clear_session(previous_cluster)

        # Update active cluster
        config.active = input.cluster_id
        target_cluster.last_used_at = datetime.now(UTC)

        # Save config
        await self._save_config()

        # Log the switch for audit
        logger.warning(
            "cluster_switched",
            extra={
                "previous_cluster": previous_cluster,
                "new_cluster": input.cluster_id,
                "new_environment": target_cluster.environment.value,
                "force_used": input.force,
                "production_confirmed": input.confirm_production,
            },
        )

        # Build warnings
        warnings = []
        if target_cluster.environment == ClusterEnvironment.PRODUCTION:
            warnings.append("Now operating on PRODUCTION cluster")
        if not target_cluster.ssl_verify:
            warnings.append("SSL verification is DISABLED for this cluster")
        if not target_cluster.fingerprint:
            warnings.append(
                "Cluster fingerprint not yet verified. "
                "Will be set on first successful authentication."
            )

        return SwitchClusterOutput(
            success=True,
            previous_cluster=previous_cluster,
            new_cluster=input.cluster_id,
            new_cluster_url=target_cluster.url,
            new_cluster_environment=target_cluster.environment,
            requires_login=True,
            session_cleared=True,
            message=(
                f"Switched to cluster '{input.cluster_id}' "
                f"({target_cluster.environment.value}). "
                f"Please login to authenticate."
            ),
            warnings=warnings,
        )

    async def get_current_cluster(self) -> CurrentClusterOutput:
        """Get information about the current active cluster."""
        config = await self._load_config()
        cluster_id, cluster = config.get_active_cluster()

        if not cluster_id or not cluster:
            return CurrentClusterOutput(
                has_active_cluster=False,
                cluster=None,
                is_authenticated=False,
                fingerprint_verified=False,
                warnings=["No active cluster. Use list_clusters and switch_cluster."],
                next_action="Call list_clusters to see available clusters",
            )

        is_authenticated = self._authenticated_clusters.get(cluster_id, False)

        # Build warnings
        warnings = []
        if cluster.environment == ClusterEnvironment.PRODUCTION:
            warnings.append("Operating on PRODUCTION cluster")
        if not cluster.ssl_verify:
            warnings.append("SSL verification is DISABLED")
        if cluster.is_locked:
            warnings.append("Cluster is LOCKED (cannot switch away)")

        # Determine next action
        if not is_authenticated:
            next_action = "Call login_secure to authenticate"
        else:
            next_action = "Ready for operations"

        cluster_info = ClusterInfo(
            id=cluster_id,
            name=cluster.display_name,
            url=cluster.url,
            environment=cluster.environment,
            ssl_verify=cluster.ssl_verify,
            is_active=True,
            is_authenticated=is_authenticated,
            is_locked=cluster.is_locked,
            has_fingerprint=cluster.fingerprint is not None,
            description=cluster.description,
            last_used_at=cluster.last_used_at,
        )

        return CurrentClusterOutput(
            has_active_cluster=True,
            cluster=cluster_info,
            is_authenticated=is_authenticated,
            fingerprint_verified=cluster.fingerprint is not None,
            warnings=warnings,
            next_action=next_action,
        )

    async def add_cluster(
        self,
        input: AddClusterInput,
    ) -> AddClusterOutput:
        """Add a new cluster configuration.

        Validates the cluster URL is reachable and stores the configuration.
        """
        config = await self._load_config()

        # Check if cluster ID already exists
        if input.cluster_id in config.clusters:
            raise ValueError(
                f"Cluster '{input.cluster_id}' already exists. "
                f"Use a different ID or remove the existing cluster first."
            )

        # Create cluster config
        cluster = ClusterConfig(
            url=input.url,
            name=input.name,
            description=input.description,
            environment=input.environment,
            ssl_verify=input.ssl_verify,
        )

        # Validate URL is reachable (best effort)
        url_reachable = False
        validation_warnings = []

        try:
            import aiohttp

            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                # Try to reach the MCC config.js endpoint
                config_url = f"{input.url}/config.js"
                async with session.get(
                    config_url,
                    ssl=input.ssl_verify,
                ) as response:
                    url_reachable = response.status < 500
        except Exception as e:
            validation_warnings.append(
                f"Could not validate URL: {e}. Cluster added but may not be reachable."
            )

        # Add to config
        config.add_cluster(input.cluster_id, cluster, set_active=input.set_active)

        # Save config
        await self._save_config()

        logger.info(
            "cluster_added",
            extra={
                "cluster_id": input.cluster_id,
                "url": input.url,
                "environment": input.environment.value,
                "set_active": input.set_active,
            },
        )

        # Determine next action
        if input.set_active:
            next_action = "Call login_secure to authenticate to the new cluster"
        else:
            next_action = (
                f"Call switch_cluster(cluster_id='{input.cluster_id}') to use this cluster"
            )

        return AddClusterOutput(
            success=True,
            cluster_id=input.cluster_id,
            cluster_url=input.url,
            is_active=input.set_active,
            url_reachable=url_reachable,
            validation_warnings=validation_warnings,
            message=(
                f"Added cluster '{input.cluster_id}' ({input.environment.value}). "
                + ("Now active." if input.set_active else "")
            ),
            next_action=next_action,
        )

    async def verify_cluster_fingerprint(
        self,
        keycloak_issuer: str,
        k8s_api_url: str,
    ) -> tuple[bool, str | None]:
        """Verify the active cluster's fingerprint.

        Called after successful authentication to verify cluster identity.

        Returns:
            Tuple of (is_valid, error_message)
        """
        config = await self._load_config()
        cluster_id, cluster = config.get_active_cluster()

        if not cluster_id or not cluster:
            return False, "No active cluster"

        is_valid, error = cluster.verify_fingerprint(keycloak_issuer, k8s_api_url)

        if not is_valid:
            logger.error(
                "cluster_fingerprint_mismatch",
                extra={
                    "cluster_id": cluster_id,
                    "expected": cluster.fingerprint,
                    "keycloak_issuer": keycloak_issuer,
                    "k8s_api_url": k8s_api_url,
                },
            )
            return False, error

        # If no fingerprint set, compute and store it
        if not cluster.fingerprint:
            cluster.fingerprint = cluster.compute_fingerprint(keycloak_issuer, k8s_api_url)
            cluster.fingerprint_updated_at = datetime.now(UTC)
            await self._save_config()

            logger.info(
                "cluster_fingerprint_set",
                extra={
                    "cluster_id": cluster_id,
                    "fingerprint": cluster.fingerprint,
                },
            )

        return True, None

    async def mark_authenticated(self, cluster_id: str | None = None) -> None:
        """Mark a cluster as authenticated."""
        config = await self._load_config()
        target = cluster_id or config.active

        if target:
            self._authenticated_clusters[target] = True
            logger.info(
                "cluster_authenticated",
                extra={"cluster_id": target},
            )

    async def _clear_session(self, cluster_id: str | None) -> None:
        """Clear all session state for a cluster.

        SECURITY: This ensures no credentials leak between clusters.
        """
        if cluster_id:
            self._authenticated_clusters.pop(cluster_id, None)

        # Clear session manager if available
        if self._session_manager:
            try:
                await self._session_manager.clear_session()
            except Exception as e:
                logger.warning(
                    "session_clear_failed",
                    extra={"error": str(e)},
                )

        logger.info(
            "cluster_session_cleared",
            extra={"cluster_id": cluster_id},
        )

    async def get_active_cluster_url(self) -> str | None:
        """Get the URL of the active cluster.

        Used by other components to get the current MCC URL.
        """
        config = await self._load_config()
        _, cluster = config.get_active_cluster()
        return cluster.url if cluster else None

    async def get_active_cluster_config(self) -> ClusterConfig | None:
        """Get the config of the active cluster."""
        config = await self._load_config()
        _, cluster = config.get_active_cluster()
        return cluster

    async def lock_cluster(
        self,
        cluster_id: str | None = None,
        lock: bool = True,
    ) -> tuple[str, bool]:
        """Lock or unlock a cluster.

        Locked clusters cannot be switched away from.

        Returns:
            Tuple of (cluster_id, is_locked)
        """
        config = await self._load_config()
        target = cluster_id or config.active

        if not target or target not in config.clusters:
            raise ValueError(f"Cluster '{target}' not found")

        cluster = config.clusters[target]
        cluster.is_locked = lock
        await self._save_config()

        logger.info(
            "cluster_lock_changed",
            extra={
                "cluster_id": target,
                "is_locked": lock,
            },
        )

        return target, lock


# Global instance (lazy initialized; tied to current :func:`~mosk_mcp.core.config.get_settings` object)
_cluster_manager: ClusterManager | None = None
_cluster_manager_settings_id: int | None = None


def get_cluster_manager() -> ClusterManager:
    """Return the process-wide cluster manager for the current settings snapshot.

    The manager is built from :func:`~mosk_mcp.core.config.get_settings` (``config_path`` and ``profile``).
    After a new :func:`~mosk_mcp.core.config.init_settings` (e.g. in tests via
    :func:`~mosk_mcp.core.config.reset_settings_for_testing` then ``init_settings``), the
    active ``Settings`` object is replaced; a new :class:`ClusterManager` is created on the next call
    so cluster configuration matches the new settings.
    """
    global _cluster_manager, _cluster_manager_settings_id
    settings = get_settings()
    sid = id(settings)
    if _cluster_manager is None or _cluster_manager_settings_id != sid:
        resolved_path = (
            settings.config_path
            if settings.config_path is not None
            else ClusterManager.DEFAULT_CONFIG_PATH
        )
        _cluster_manager = ClusterManager(
            config_path=resolved_path,
            profile_override=settings.profile,
        )
        _cluster_manager_settings_id = sid
    return _cluster_manager


def reset_cluster_manager() -> None:
    """Reset the global cluster manager (for testing)."""
    global _cluster_manager, _cluster_manager_settings_id
    _cluster_manager = None
    _cluster_manager_settings_id = None
