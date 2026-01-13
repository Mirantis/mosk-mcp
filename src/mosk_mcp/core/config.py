"""Configuration management for MOSK MCP Server using Pydantic Settings.

This module provides centralized configuration management with support for:
- Environment variables
- .env files
- Default values with validation
- Type-safe access to all settings
"""

from __future__ import annotations

import re
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# URL validation pattern - matches http:// or https:// URLs
_URL_PATTERN = re.compile(
    r"^https?://"  # http:// or https://
    r"(?:[\w.-]+|\[[a-fA-F0-9:]+\])"  # hostname or IPv6 in brackets
    r"(?::\d{1,5})?"  # optional port
    r"(?:/.*)?$",  # optional path
    re.IGNORECASE,
)


class TransportType(str, Enum):
    """Supported MCP transport types."""

    STDIO = "stdio"
    HTTP = "http"
    STREAMABLE_HTTP = "streamable-http"


class LogLevel(str, Enum):
    """Log level configuration."""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class LogFormat(str, Enum):
    """Log output format."""

    JSON = "json"
    CONSOLE = "console"


class Environment(str, Enum):
    """Deployment environment."""

    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"


class Settings(BaseSettings):
    """Application settings loaded from environment variables.

    All settings can be configured via environment variables with the MCP_ prefix.
    For example, MCP_TRANSPORT=http sets the transport type.

    Authentication:
        This server uses SSO (Keycloak OIDC) for authentication.
        - Requires MCP_MCC_URL (e.g., https://mcc.example.com)
        - Keycloak URL and other endpoints are auto-discovered from MCC config.js
        - Each user authenticates with their own credentials via the `login` tool
        - Kubeconfigs are generated dynamically from OIDC tokens
        - Provides user-scoped permissions and audit trails

    Attributes:
        app_name: Application name for identification.
        app_version: Application version string.
        transport: MCP transport type (stdio, http, or streamable-http).
        http_host: Host to bind HTTP server to.
        http_port: Port for HTTP transport.
        log_level: Logging level.
        log_format: Log output format (json for production, console for development).
        auth_enabled: Whether authentication is enabled (uses SSO Device Flow).
        kubernetes_namespace: Default Kubernetes namespace for operations.
        audit_log_path: Path for audit log file.
        audit_enabled: Whether audit logging is enabled.
        request_timeout: Default timeout for external requests in seconds.
        max_retries: Maximum number of retries for failed operations.
        otel_enabled: Whether OpenTelemetry tracing is enabled.
        otel_service_name: Service name for OpenTelemetry.
        otel_exporter_endpoint: OpenTelemetry exporter endpoint.
        metrics_enabled: Whether Prometheus metrics are enabled.
        metrics_port: Port for Prometheus metrics endpoint.
        metrics_host: Host to bind metrics server to.
        health_check_timeout_seconds: Timeout for health check operations.
        health_check_k8s_enabled: Whether to check K8s connectivity in health checks.
        mcc_url: MCC UI URL (required, e.g., https://mcc.example.com).
        keycloak_url: Override for Keycloak server URL (auto-discovered from MCC).
        keycloak_realm: Override for Keycloak realm name (auto-discovered from MCC).
        mcc_oidc_client_id: Override for OIDC client ID (auto-discovered from MCC).
        prometheus_url: Override for Prometheus IAM proxy URL (auto-discovered).
        alertmanager_url: Override for Alertmanager IAM proxy URL (auto-discovered).
        opensearch_url: Override for OpenSearch/Kibana IAM proxy URL (auto-discovered).
    """

    model_config = SettingsConfigDict(
        env_prefix="MCP_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Application metadata
    app_name: str = "mosk-mcp"
    app_version: str = "0.1.0"

    # MCP Transport settings
    transport: TransportType = TransportType.STDIO
    http_host: str = "0.0.0.0"
    http_port: Annotated[int, Field(ge=1, le=65535)] = 8080

    # Environment and logging settings
    environment: Environment = Environment.DEVELOPMENT
    log_level: LogLevel = LogLevel.INFO
    log_format: LogFormat = LogFormat.JSON

    # Authentication settings (SSO via Device Flow)
    auth_enabled: bool = True

    # Default namespace for operations
    kubernetes_namespace: str = "default"

    # MOSK cluster identification on MCC (for Machine CR queries)
    # These are auto-discovered if not set, but can be overridden
    mosk_cluster_name: str | None = None  # e.g., "mos" - the Cluster CR name
    mosk_cluster_namespace: str | None = (
        None  # e.g., "lab" - namespace where MOSK Cluster/Machines live
    )

    # Audit settings
    audit_log_path: Path = Path("/var/log/mosk-mcp/audit.log")
    audit_enabled: bool = True
    audit_rotation_enabled: bool = True
    audit_max_size_mb: Annotated[int, Field(ge=1, le=1000)] = 100
    audit_backup_count: Annotated[int, Field(ge=0, le=100)] = 10
    audit_rotation_when: str = "midnight"  # midnight, h (hourly), d (daily)

    # Docker-friendly logging - logs go to stderr only (visible in `docker logs`)
    # When True, file-based logging is disabled and all logs go to stderr
    # This is the recommended setting for containerized deployments
    log_to_stderr_only: bool = True

    # Operation settings
    request_timeout: Annotated[int, Field(ge=1, le=300)] = 30
    max_retries: Annotated[int, Field(ge=0, le=10)] = 3

    # OpenTelemetry settings
    otel_enabled: bool = False
    otel_service_name: str = "mosk-mcp"
    otel_exporter_endpoint: str | None = None

    # Metrics settings
    metrics_enabled: bool = True
    metrics_port: Annotated[int, Field(ge=1, le=65535)] = 9090
    metrics_host: str = "0.0.0.0"

    # Health check settings
    health_check_timeout_seconds: Annotated[int, Field(ge=1, le=60)] = 10
    health_check_k8s_enabled: bool = True

    # Rate limiting settings
    rate_limit_enabled: bool = True
    rate_limit_requests_per_minute: Annotated[int, Field(ge=1, le=10000)] = 60
    rate_limit_burst_size: Annotated[int, Field(ge=1, le=100)] = 10

    # Graceful shutdown settings
    shutdown_timeout: Annotated[int, Field(ge=5, le=300)] = 60
    drain_timeout: Annotated[int, Field(ge=1, le=120)] = 30

    # Connection pool settings
    connection_pool_size: Annotated[int, Field(ge=1, le=50)] = 10
    connection_pool_timeout: Annotated[int, Field(ge=5, le=120)] = 30
    connection_health_check_interval: Annotated[int, Field(ge=10, le=300)] = 60

    # Circuit breaker settings
    circuit_breaker_failure_threshold: Annotated[int, Field(ge=1, le=20)] = 5
    circuit_breaker_recovery_timeout: Annotated[int, Field(ge=5, le=300)] = 30

    # SSO settings (Keycloak OIDC-based authentication)
    # Users authenticate with Keycloak and kubeconfigs are generated dynamically

    # MCC UI URL - the ONLY required setting
    # All other endpoints (Keycloak, K8s API, StackLight) are auto-discovered from config.js
    mcc_url: str | None = None  # MCC UI URL (e.g., https://mcc.example.com)

    # Optional overrides - normally auto-discovered from MCC UI config.js
    # Only set these if auto-discovery doesn't work for your environment
    keycloak_url: str | None = None  # Override Keycloak server URL
    keycloak_realm: str | None = None  # Override Keycloak realm (default: discovered or "iam")
    mcc_oidc_client_id: str | None = None  # Override OIDC client ID (default: discovered or "kaas")
    prometheus_url: str | None = None  # Override Prometheus IAM proxy URL
    alertmanager_url: str | None = None  # Override Alertmanager IAM proxy URL
    opensearch_url: str | None = None  # Override OpenSearch/Kibana IAM proxy URL

    # SSL/TLS settings for API connections
    # SECURITY: In production, ssl_verify should be True with proper CA certificates
    ssl_verify: bool = (
        True  # Verify SSL certificates (disable only for self-signed certs in dev/lab)
    )
    ssl_ca_cert_path: Path | None = None  # Path to CA certificate bundle for verification

    # Device Flow Authentication Settings (OAuth 2.0 Device Authorization Grant - RFC 8628)
    # Device Flow allows secure authentication without typing passwords in chat.
    # Users authenticate via browser while MCP polls for the token.
    #
    # SECURITY: Device Flow is the recommended authentication method for CLI/chat tools.
    # It avoids exposing credentials in chat history and supports MFA/2FA.

    # Enable Device Flow authentication (recommended for production)
    # When enabled, the login tool will use Device Flow instead of ROPC
    device_flow_enabled: bool = True

    # OAuth client ID for Device Flow authentication
    # Uses the standard MCC "kaas" client with Device Flow enabled
    # This ensures tokens have the correct audience and iam_roles for K8s API access
    device_flow_client_id: str = "kaas"

    # Device code lifespan in seconds (how long user has to complete authentication)
    # Must match Keycloak client configuration
    # Default: 600 seconds (10 minutes) - recommended for interactive use
    device_flow_code_lifespan: Annotated[int, Field(ge=60, le=1800)] = 600

    # Polling interval in seconds (how often MCP checks if user completed auth)
    # Must be >= Keycloak's configured interval to avoid rate limiting
    # Default: 5 seconds - balances responsiveness with server load
    device_flow_poll_interval: Annotated[int, Field(ge=1, le=60)] = 5

    # Maximum polling attempts before giving up
    # 0 = unlimited (poll until device code expires)
    # Set a limit to prevent indefinite waiting
    device_flow_max_poll_attempts: Annotated[int, Field(ge=0, le=1000)] = 0

    # OAuth scopes to request during Device Flow authentication
    # offline_access: Enables long-lived refresh tokens
    # openid, profile, email: Standard OIDC scopes for user info
    device_flow_scope: str = "openid profile email offline_access"

    # ==========================================================================
    # Privacy & Data Protection Settings
    # ==========================================================================
    # These settings control what sensitive data is redacted from tool responses
    # before being sent to LLM providers (Claude, OpenAI, etc.)
    #
    # IMPORTANT: When using public LLMs, sensitive infrastructure data (IPs,
    # hostnames, credentials) could be exposed. Enable privacy protection to
    # automatically redact this information from responses.
    #
    # Levels:
    #   - none: No redaction (not recommended for public LLMs)
    #   - minimal: Only redact secrets/credentials
    #   - standard: Redact IPs, MACs, hostnames, secrets (recommended)
    #   - aggressive: Also redact UUIDs (instance IDs, volume IDs)

    # Enable privacy protection (default: disabled, enable with MCP_PRIVACY_ENABLED=true)
    privacy_enabled: bool = False

    # Privacy protection level
    # Options: none, minimal, standard, aggressive
    privacy_level: str = "standard"

    # Redact UUIDs (instance IDs, volume IDs) - off by default in standard mode
    # Enable for extra privacy when sharing with external LLMs
    privacy_redact_uuid: bool = False

    # Preserve data structure in output (show types like [IP-1], [HOST-2])
    # If false, all redacted values become generic [REDACTED]
    privacy_preserve_structure: bool = True

    @field_validator("audit_log_path", mode="before")
    @classmethod
    def validate_audit_log_path(cls, v: Any) -> Path:
        """Validate and convert audit log path."""
        if isinstance(v, str):
            return Path(v)
        if isinstance(v, Path):
            return v
        return Path(str(v))

    @field_validator(
        "mcc_url",
        "keycloak_url",
        "prometheus_url",
        "alertmanager_url",
        "opensearch_url",
        "otel_exporter_endpoint",
        mode="before",
    )
    @classmethod
    def validate_url_format(cls, v: Any) -> str | None:
        """Validate URL format and normalize by stripping trailing slashes.

        Ensures URLs have valid http:// or https:// scheme and strips
        trailing slashes for consistency.

        Args:
            v: URL value to validate.

        Returns:
            Normalized URL string or None.

        Raises:
            ValueError: If URL format is invalid.
        """
        if v is None or v == "":
            return None

        # Convert to string and strip whitespace
        url: str = str(v).strip()
        if not url:
            return None

        # Validate URL format
        if not _URL_PATTERN.match(url):
            raise ValueError(
                f"Invalid URL format: '{url}'. "
                "URL must start with http:// or https:// and contain a valid hostname."
            )

        # Strip trailing slashes for consistency
        return url.rstrip("/")

    @model_validator(mode="after")
    def validate_auth_settings(self) -> Settings:
        """Validate authentication configuration.

        SECURITY RULES:
        1. In production mode, authentication MUST be enabled.
        2. In development mode, auth can be disabled but anonymous users
           get read-only access only.

        Raises:
            ValueError: In production mode when auth is disabled.
        """
        if self.is_production and not self.auth_enabled:
            raise ValueError(
                "SECURITY ERROR: Authentication cannot be disabled in production. "
                "Set MCP_AUTH_ENABLED=true and use Device Flow authentication."
            )
        return self

    @model_validator(mode="after")
    def validate_sso_settings(self) -> Settings:
        """Validate SSO configuration.

        SSO authentication requires MCC URL to be set in production mode.
        In development mode, MCC URL is optional to allow testing without
        a real MCC cluster.

        Raises:
            ValueError: If MCC URL is not configured in production mode.
        """
        # In production, MCC URL is always required
        if self.is_production and not self.mcc_url:
            raise ValueError(
                "MCC URL is required in production. Set MCP_MCC_URL environment variable "
                "(e.g., https://mcc.example.com). "
                "Keycloak and other endpoints will be auto-discovered from MCC config.js."
            )
        return self

    @model_validator(mode="after")
    def validate_ssl_settings(self) -> Settings:
        """Validate SSL/TLS configuration.

        SECURITY NOTE:
        SSL verification is recommended in production to prevent MITM attacks.
        However, it can be disabled for environments using self-signed certificates.
        A warning will be logged when SSL verification is disabled.
        """
        # SSL verification warning is handled by has_ssl_warning property
        # No validation error - allow self-signed certificates in all environments
        return self

    @model_validator(mode="after")
    def validate_otel_settings(self) -> Settings:
        """Validate OpenTelemetry configuration."""
        if self.otel_enabled and self.otel_exporter_endpoint is None:
            raise ValueError("OTEL_EXPORTER_ENDPOINT must be set when OTEL_ENABLED is true")
        return self

    @property
    def is_development(self) -> bool:
        """Check if running in development mode.

        Uses explicit environment setting rather than inferring from log format.
        """
        return self.environment == Environment.DEVELOPMENT

    @property
    def is_staging(self) -> bool:
        """Check if running in staging mode."""
        return self.environment == Environment.STAGING

    @property
    def is_production(self) -> bool:
        """Check if running in production mode.

        Uses explicit environment setting for production detection.
        If environment is explicitly set to DEVELOPMENT or STAGING,
        it takes precedence over other indicators.

        Production mode enforces stricter security requirements.
        """
        import os

        # Explicit environment setting takes precedence
        if self.environment == Environment.PRODUCTION:
            return True

        # If explicitly set to non-production, respect that
        if self.environment in (Environment.DEVELOPMENT, Environment.STAGING):
            return False

        # Running in Kubernetes without explicit environment is likely production
        if os.getenv("KUBERNETES_SERVICE_HOST") is not None:
            return True

        # JSON logging typically indicates production (when environment not explicitly set)
        return self.log_format == LogFormat.JSON

    @property
    def has_ssl_warning(self) -> bool:
        """Check if there's an SSL configuration warning.

        Returns True if SSL verification is disabled, which is a security
        concern but allowed for self-signed certificate environments.
        """
        return not self.ssl_verify

    @property
    def ssl_warning_message(self) -> str | None:
        """Get the SSL warning message if applicable.

        Returns:
            Warning message string or None if no warning.
        """
        if self.has_ssl_warning:
            return (
                "SECURITY WARNING: SSL certificate verification is disabled. "
                "This allows connections to servers with self-signed certificates but "
                "also makes the server vulnerable to man-in-the-middle attacks. "
                "For production use with self-signed certificates, consider configuring "
                "MCP_SSL_CA_CERT_PATH with a CA bundle that includes your certificate."
            )
        return None


@lru_cache
def get_settings() -> Settings:
    """Get cached application settings.

    Returns:
        Settings instance loaded from environment.

    Note:
        This function is cached to ensure settings are loaded only once.
        To reload settings, clear the cache with get_settings.cache_clear().
    """
    return Settings()


def reload_settings() -> Settings:
    """Reload settings by clearing the cache.

    Returns:
        Fresh Settings instance loaded from environment.
    """
    get_settings.cache_clear()
    return get_settings()
