"""Cluster configuration models with strict security validation.

SECURITY FEATURES:
1. Cluster environments (dev/staging/prod) with different safety levels
2. Production clusters marked explicitly - require confirmation for switches
3. Fingerprint storage for cluster identity verification
4. Strict URL validation to prevent injection attacks
5. Immutable cluster IDs after creation
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

from mosk_mcp.url_validation import validate_http_url

logger = logging.getLogger(__name__)

# Cluster ID pattern - alphanumeric, hyphens, underscores only
_CLUSTER_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,62}$", re.IGNORECASE)


class ClusterEnvironment(str, Enum):
    """Cluster environment classification.

    SECURITY: Environment determines safety checks applied:
    - DEVELOPMENT: Minimal confirmations, allows HTTP
    - STAGING: Moderate checks, HTTPS recommended
    - PRODUCTION: Strict checks, HTTPS required, confirmations required
    """

    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"

    @property
    def requires_confirmation(self) -> bool:
        """Whether switching to this environment requires confirmation."""
        return self == ClusterEnvironment.PRODUCTION

    @property
    def requires_https(self) -> bool:
        """Whether this environment requires HTTPS."""
        return self == ClusterEnvironment.PRODUCTION

    @property
    def safety_level(self) -> int:
        """Numeric safety level (higher = more dangerous)."""
        return {
            ClusterEnvironment.DEVELOPMENT: 1,
            ClusterEnvironment.STAGING: 2,
            ClusterEnvironment.PRODUCTION: 3,
        }[self]


class ClusterConfig(BaseModel):
    """Configuration for a single MCC cluster.

    SECURITY PROPERTIES:
    - url: Validated URL, HTTPS required for production
    - environment: Determines safety checks
    - fingerprint: Cryptographic identity of the cluster
    - is_locked: Prevents accidental switches away from this cluster
    """

    # Required fields
    url: str = Field(
        ...,
        description="MCC cluster URL (https://...)",
        examples=["https://mcc.example.com"],
    )

    # Display information
    name: str | None = Field(
        default=None,
        description="Human-readable cluster name",
        max_length=100,
    )
    description: str | None = Field(
        default=None,
        description="Cluster description",
        max_length=500,
    )

    # Environment classification
    environment: ClusterEnvironment = Field(
        default=ClusterEnvironment.DEVELOPMENT,
        description="Cluster environment (affects safety checks)",
    )

    # Security settings
    ssl_verify: bool = Field(
        default=True,
        description="Verify SSL certificates (MUST be true for production)",
    )

    # Optional overrides (auto-discovered if not set)
    keycloak_url: str | None = Field(
        default=None,
        description="Override Keycloak URL",
    )
    keycloak_realm: str | None = Field(
        default=None,
        description="Override Keycloak realm",
    )

    # Cluster identity fingerprint (computed after first connection)
    # This prevents connecting to wrong cluster if URL changes
    fingerprint: str | None = Field(
        default=None,
        description="Cluster fingerprint for identity verification",
    )
    fingerprint_updated_at: datetime | None = Field(
        default=None,
        description="When fingerprint was last updated",
    )

    # Safety features
    is_locked: bool = Field(
        default=False,
        description="Prevent switching away from this cluster",
    )
    require_confirmation: bool | None = Field(
        default=None,
        description="Override confirmation requirement (defaults to env setting)",
    )

    # Metadata
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
    )
    last_used_at: datetime | None = Field(default=None)

    @field_validator("url", mode="before")
    @classmethod
    def validate_url(cls, v: Any) -> str:
        """Validate and normalize URL."""
        if not isinstance(v, str):
            raise ValueError("URL must be a string")

        url = v.strip()
        if not url:
            raise ValueError("URL cannot be empty")

        return validate_http_url(url)

    @field_validator("name", "description", mode="before")
    @classmethod
    def sanitize_text(cls, v: Any) -> str | None:
        """Sanitize text fields to prevent injection."""
        if v is None:
            return None
        if not isinstance(v, str):
            v = str(v)
        # Remove control characters and excessive whitespace
        v = re.sub(r"[\x00-\x1f\x7f-\x9f]", "", v)
        v = " ".join(v.split())
        return v if v else None

    @model_validator(mode="after")
    def validate_security_settings(self) -> ClusterConfig:
        """Enforce security rules based on environment."""
        # Production clusters MUST use HTTPS
        if self.environment == ClusterEnvironment.PRODUCTION:
            if self.url.startswith("http://"):
                raise ValueError(
                    "SECURITY ERROR: Production clusters MUST use HTTPS. "
                    f"URL '{self.url}' uses insecure HTTP."
                )
            if not self.ssl_verify:
                raise ValueError(
                    "SECURITY ERROR: Production clusters MUST have SSL verification enabled. "
                    "Set ssl_verify: true or change environment to 'staging' or 'development'."
                )

        return self

    @property
    def requires_switch_confirmation(self) -> bool:
        """Whether switching TO this cluster requires confirmation."""
        if self.require_confirmation is not None:
            return self.require_confirmation
        return self.environment.requires_confirmation

    @property
    def display_name(self) -> str:
        """Get display name (name or URL if no name set)."""
        return self.name or self.url

    def compute_fingerprint(
        self,
        keycloak_issuer: str,
        k8s_api_url: str,
    ) -> str:
        """Compute cluster fingerprint from identity components.

        SECURITY: Fingerprint is SHA-256 hash of:
        - Keycloak issuer URL (unique per realm)
        - Kubernetes API URL (unique per cluster)

        This ensures we detect if:
        - MCC URL is changed but points to different cluster
        - DNS hijacking redirects to malicious cluster
        """
        identity = f"{keycloak_issuer}|{k8s_api_url}"
        return hashlib.sha256(identity.encode()).hexdigest()[:32]

    def verify_fingerprint(
        self,
        keycloak_issuer: str,
        k8s_api_url: str,
    ) -> tuple[bool, str | None]:
        """Verify cluster fingerprint matches expected identity.

        Returns:
            Tuple of (is_valid, error_message)
        """
        if not self.fingerprint:
            # No fingerprint yet - first connection
            return True, None

        current = self.compute_fingerprint(keycloak_issuer, k8s_api_url)
        if current != self.fingerprint:
            return False, (
                f"SECURITY ALERT: Cluster fingerprint mismatch!\n"
                f"Expected: {self.fingerprint}\n"
                f"Got: {current}\n"
                f"This could indicate:\n"
                f"  - MCC URL now points to a different cluster\n"
                f"  - DNS hijacking attack\n"
                f"  - Cluster was rebuilt\n"
                f"If this is expected, delete the cluster and re-add it."
            )

        return True, None


class ClustersConfig(BaseModel):
    """Root configuration for all clusters.

    SECURITY FEATURES:
    - Single active cluster at a time
    - Version tracking for config changes
    - Locked cluster cannot be switched from
    """

    # Config metadata
    config_version: str = Field(
        default="1.0",
        description="Config file version for migrations",
    )

    # Active cluster
    active: str | None = Field(
        default=None,
        description="Currently active cluster ID",
    )

    # Cluster definitions
    clusters: dict[str, ClusterConfig] = Field(
        default_factory=dict,
        description="Named cluster configurations",
    )

    # Global safety settings
    confirm_production_switch: bool = Field(
        default=True,
        description="Require confirmation when switching to production clusters",
    )
    allow_http_clusters: bool = Field(
        default=True,
        description="Allow HTTP (non-HTTPS) clusters (dev only)",
    )

    # Audit
    last_modified_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
    )

    @field_validator("clusters", mode="before")
    @classmethod
    def validate_cluster_ids(cls, v: Any) -> dict[str, Any]:
        """Validate cluster IDs are safe."""
        if not isinstance(v, dict):
            return v

        for cluster_id in v:
            if not _CLUSTER_ID_PATTERN.match(cluster_id):
                raise ValueError(
                    f"Invalid cluster ID: '{cluster_id}'. "
                    "Must start with letter, contain only alphanumeric, "
                    "hyphens, underscores, max 63 chars."
                )

        return v

    @model_validator(mode="after")
    def validate_active_cluster(self) -> ClustersConfig:
        """Ensure active cluster exists in clusters dict."""
        if self.active and self.active not in self.clusters:
            raise ValueError(
                f"Active cluster '{self.active}' not found in clusters. "
                f"Available: {list(self.clusters.keys())}"
            )
        return self

    def get_active_cluster(self) -> tuple[str | None, ClusterConfig | None]:
        """Get the active cluster config.

        Returns:
            Tuple of (cluster_id, cluster_config) or (None, None)
        """
        if not self.active:
            return None, None
        return self.active, self.clusters.get(self.active)

    def is_cluster_locked(self) -> bool:
        """Check if the current active cluster is locked."""
        _, cluster = self.get_active_cluster()
        return cluster.is_locked if cluster else False

    @classmethod
    def from_yaml_file(cls, path: Path) -> ClustersConfig:
        """Load config from YAML file with security checks.

        SECURITY: File permissions are checked (should be 600 or 644).
        """
        if not path.exists():
            return cls()

        # Check file permissions (warn if too open)
        mode = path.stat().st_mode & 0o777
        if mode & 0o077:  # World or group readable/writable
            logger.warning(
                "SECURITY WARNING: Cluster config file %s has loose permissions "
                "(%s). Recommend: chmod 600 %s",
                path,
                oct(mode),
                path,
            )

        with path.open(encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        return cls.model_validate(data)

    def to_yaml_file(self, path: Path) -> None:
        """Save config to YAML file with secure permissions."""
        self.last_modified_at = datetime.now(UTC)

        # Ensure parent directory exists
        path.parent.mkdir(parents=True, exist_ok=True)

        # Convert to dict, handling datetime serialization
        data = self.model_dump(mode="json", exclude_none=True)

        # Write with secure permissions
        with path.open("w", encoding="utf-8") as f:
            yaml.safe_dump(
                data,
                f,
                default_flow_style=False,
                sort_keys=False,
                allow_unicode=True,
            )

        # Set secure permissions (user read/write only)
        path.chmod(0o600)

    def add_cluster(
        self,
        cluster_id: str,
        config: ClusterConfig,
        set_active: bool = False,
    ) -> None:
        """Add a new cluster configuration.

        Args:
            cluster_id: Unique identifier for the cluster
            config: Cluster configuration
            set_active: Whether to make this the active cluster
        """
        if not _CLUSTER_ID_PATTERN.match(cluster_id):
            raise ValueError(
                f"Invalid cluster ID: '{cluster_id}'. "
                "Must start with letter, contain only alphanumeric, "
                "hyphens, underscores, max 63 chars."
            )

        if cluster_id in self.clusters:
            raise ValueError(f"Cluster '{cluster_id}' already exists")

        self.clusters[cluster_id] = config

        if set_active or not self.active:
            self.active = cluster_id

    def remove_cluster(self, cluster_id: str) -> None:
        """Remove a cluster configuration.

        SECURITY: Cannot remove the active cluster or locked clusters.
        """
        if cluster_id not in self.clusters:
            raise ValueError(f"Cluster '{cluster_id}' not found")

        if cluster_id == self.active:
            raise ValueError(
                f"Cannot remove active cluster '{cluster_id}'. Switch to another cluster first."
            )

        cluster = self.clusters[cluster_id]
        if cluster.is_locked:
            raise ValueError(f"Cannot remove locked cluster '{cluster_id}'. Unlock it first.")

        del self.clusters[cluster_id]
