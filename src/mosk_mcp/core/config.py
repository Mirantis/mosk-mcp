"""Configuration management for MOSK MCP Server using Pydantic Settings.

Process-wide access uses :func:`init_settings` once at startup, then :func:`get_settings`.
Loading rules for :class:`Settings` itself:
- Environment variables (``MCP_*``)
- ``.env`` file (path via ``DOTENV_PATH``, default ``.env``)
- Default values with validation
"""

from __future__ import annotations

import os
from enum import Enum
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, CliToggleFlag, SettingsConfigDict

from mosk_mcp._version import __version__ as _PACKAGE_VERSION
from mosk_mcp.url_validation import validate_http_url

# Path to dotenv file; not MCP_-prefixed so it can be set without reading .env first.
_DOTENV_PATH_ENV = "DOTENV_PATH"
_DEFAULT_DOTENV_FILE = ".env"


def _resolve_dotenv_path() -> str | Path:
    """Return path to the env file for pydantic-settings (default: .env)."""
    raw = os.environ.get(_DOTENV_PATH_ENV, _DEFAULT_DOTENV_FILE)
    s = raw.strip()
    return _DEFAULT_DOTENV_FILE if not s else s


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
    """Deployment environment for the MCP server process (logging, security rules).

    Distinct from per-cluster ``safety_tier`` in ``clusters.yaml``. Used with
    ``MCP_ENVIRONMENT`` to tune production vs development behavior.
    """

    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"


PrivacyLevel = Literal["none", "minimal", "standard", "aggressive"]


class Settings(BaseSettings):
    """Application settings loaded from environment variables and optional ``.env`` file.

    **Prefix:** Environment variables use ``MCP_<FIELD_NAME>`` (see ``model_config``).
    **Dotenv:** ``DOTENV_PATH`` (not ``MCP_``-prefixed) selects the env file (default: ``.env``).

    **Authentication:** SSO via Keycloak OIDC (OAuth 2.0 Device Flow). Set ``MCP_MCC_URL``
    when not using multi-cluster ``clusters.yaml``; other endpoints are auto-discovered from
    MCC ``config.js``.
    """
    # **CLI:** The ``mosk-mcp`` console script uses ``CliApp`` (see ``mosk_mcp.cli``): kebab-case
    # flags, ``cli_shortcuts`` for names like ``--host`` / ``--port``, and ``CliToggleFlag`` on
    # selected booleans for ``--no-auth`` / ``--no-metrics``-style switches.
   
    model_config = SettingsConfigDict(
        env_prefix="MCP_",
        env_file=_resolve_dotenv_path(),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Application metadata
    app_name: str = "mosk-mcp"
    app_version: str = _PACKAGE_VERSION

    # --- MCP transport (HTTP / stdio) ---
    transport: TransportType = Field(
        default=TransportType.STDIO,
        description=(
            "MCP wire transport. Use ``stdio`` for Claude Desktop and similar clients; "
            "``http`` or ``streamable-http`` for network deployments."
        ),
    )
    http_host: str = Field(
        default="0.0.0.0",
        description="Bind address for the HTTP MCP server when ``transport`` is ``http`` or ``streamable-http``.",
    )
    http_port: Annotated[
        int,
        Field(
            ge=1,
            le=65535,
            description="TCP port for the HTTP MCP server when using HTTP transports.",
        ),
    ] = 8080

    # --- Process environment & logging ---
    environment: Environment = Field(
        default=Environment.DEVELOPMENT,
        description=(
            "Deployment mode for this server process: affects security validation, production "
            "detection, and logging. Not the same as per-cluster safety tier in ``clusters.yaml``."
        ),
    )
    log_level: LogLevel = Field(
        default=LogLevel.INFO,
        description="Minimum log level (DEBUG, INFO, WARNING, ERROR, CRITICAL).",
    )
    log_format: LogFormat = Field(
        default=LogFormat.JSON,
        description=(
            "``json`` for structured logs (typical in production); ``console`` for human-readable "
            "output in development."
        ),
    )

    # --- Authentication (SSO / Device Flow) ---
    auth_enabled: CliToggleFlag[bool] = Field(
        default=True,
        description=(
            "When true, use OAuth 2.0 Device Flow (Keycloak) for user login. "
            "Disabling is only for local development; production requires auth."
        ),
    )

    kubernetes_namespace: str = Field(
        default="default",
        description="Default Kubernetes namespace for tools that target a namespace.",
    )

    mosk_cluster_name: str | None = Field(
        default=None,
        description='Optional MOSK Cluster CR name on MCC (e.g. ``"mos"``); auto-discovered if unset.',
    )
    mosk_cluster_namespace: str | None = Field(
        default=None,
        description="Namespace on MCC where MOSK Cluster/Machine CRs live; auto-discovered if unset.",
    )

    # --- Audit logging ---
    audit_log_path: Path = Field(
        default=Path("/var/log/mosk-mcp/audit.log"),
        description="Filesystem path for the audit log (security-sensitive actions).",
    )
    audit_enabled: bool = Field(
        default=True,
        description="Enable writing audit events to ``audit_log_path``.",
    )
    audit_rotation_enabled: bool = Field(
        default=True,
        description="Rotate audit files by size/time when supported by the logging setup.",
    )
    audit_max_size_mb: Annotated[
        int,
        Field(
            ge=1,
            le=1000,
            description="Maximum audit log file size in MB before rotation.",
        ),
    ] = 100
    audit_backup_count: Annotated[
        int,
        Field(
            ge=0,
            le=100,
            description="Number of rotated audit files to retain.",
        ),
    ] = 10
    audit_rotation_when: str = Field(
        default="midnight",
        description="Rotation schedule keyword (e.g. ``midnight``, ``h`` hourly, ``d`` daily).",
    )

    log_to_stderr_only: bool = Field(
        default=True,
        description=(
            "If true, logs go to stderr only (ideal for Docker ``docker logs``); "
            "disables separate file logging for the main logger."
        ),
    )

    # --- HTTP client / retries ---
    request_timeout: Annotated[
        int,
        Field(
            ge=1,
            le=300,
            description="Default timeout in seconds for outbound HTTP/API requests.",
        ),
    ] = 30
    max_retries: Annotated[
        int,
        Field(
            ge=0,
            le=10,
            description="Maximum retry attempts for transient failures on outbound requests.",
        ),
    ] = 3

    # --- OpenTelemetry ---
    otel_enabled: bool = Field(
        default=False,
        description="Export traces when true; requires ``otel_exporter_endpoint``.",
    )
    otel_service_name: str = Field(
        default="mosk-mcp",
        description="Service name label attached to OpenTelemetry spans.",
    )
    otel_exporter_endpoint: str | None = Field(
        default=None,
        description="OTLP/HTTP or gRPC collector URL for traces (required if ``otel_enabled``).",
    )

    # --- Metrics & health ---
    metrics_enabled: CliToggleFlag[bool] = Field(
        default=True,
        description="Expose Prometheus metrics and shared health endpoints on ``metrics_host:metrics_port``.",
    )
    metrics_port: Annotated[
        int,
        Field(
            ge=1,
            le=65535,
            description="TCP port for Prometheus metrics and health checks.",
        ),
    ] = 9090
    metrics_host: str = Field(
        default="0.0.0.0",
        description="Bind address for the metrics/health HTTP server.",
    )
    health_check_timeout_seconds: Annotated[
        int,
        Field(
            ge=1,
            le=60,
            description="Timeout in seconds for individual health check probes.",
        ),
    ] = 10
    health_check_k8s_enabled: bool = Field(
        default=True,
        description="Whether Kubernetes API connectivity is included in health checks.",
    )

    # --- Rate limiting ---
    rate_limit_enabled: bool = Field(
        default=True,
        description="Apply per-client rate limiting on HTTP transports when enabled.",
    )
    rate_limit_requests_per_minute: Annotated[
        int,
        Field(
            ge=1,
            le=10000,
            description="Sustained request rate limit per client per minute.",
        ),
    ] = 60
    rate_limit_burst_size: Annotated[
        int,
        Field(
            ge=1,
            le=100,
            description="Burst allowance above the sustained rate.",
        ),
    ] = 10

    # --- Graceful shutdown ---
    shutdown_timeout: Annotated[
        int,
        Field(
            ge=5,
            le=300,
            description="Seconds to wait for in-flight work during shutdown.",
        ),
    ] = 60
    drain_timeout: Annotated[
        int,
        Field(
            ge=1,
            le=120,
            description="Seconds to wait when draining connections before force-close.",
        ),
    ] = 30

    # --- Connection pool ---
    connection_pool_size: Annotated[
        int,
        Field(
            ge=1,
            le=50,
            description="Maximum concurrent connections in the shared HTTP client pool.",
        ),
    ] = 10
    connection_pool_timeout: Annotated[
        int,
        Field(
            ge=5,
            le=120,
            description="Seconds to wait when acquiring a connection from the pool.",
        ),
    ] = 30
    connection_health_check_interval: Annotated[
        int,
        Field(
            ge=10,
            le=300,
            description="Interval in seconds between idle connection health checks.",
        ),
    ] = 60

    # --- Circuit breaker ---
    circuit_breaker_failure_threshold: Annotated[
        int,
        Field(
            ge=1,
            le=20,
            description="Consecutive failures before the circuit opens.",
        ),
    ] = 5
    circuit_breaker_recovery_timeout: Annotated[
        int,
        Field(
            ge=5,
            le=300,
            description="Seconds to wait before attempting half-open recovery.",
        ),
    ] = 30

    # --- MCC / SSO endpoints ---
    mcc_url: str | None = Field(
        default=None,
        description=(
            "MCC UI base URL (e.g. ``https://mcc.example.com``). Required in production when "
            "not relying solely on multi-cluster config. Keycloak and other URLs are read from "
            "MCC ``config.js`` when unset."
        ),
    )
    keycloak_url: str | None = Field(
        default=None,
        description="Override Keycloak base URL if auto-discovery from MCC fails.",
    )
    keycloak_realm: str | None = Field(
        default=None,
        description='Override Keycloak realm (default from MCC is often ``"iam"``).',
    )
    mcc_oidc_client_id: str | None = Field(
        default=None,
        description='Override OIDC client id (MCC default is often ``"kaas"``).',
    )
    prometheus_url: str | None = Field(
        default=None,
        description="Override Prometheus IAM proxy URL (StackLight) from MCC discovery.",
    )
    alertmanager_url: str | None = Field(
        default=None,
        description="Override Alertmanager IAM proxy URL from MCC discovery.",
    )
    opensearch_url: str | None = Field(
        default=None,
        description="Override OpenSearch/Kibana IAM proxy URL from MCC discovery.",
    )

    # --- Multi-cluster (clusters.yaml) ---
    config_path: Path | None = Field(
        default=None,
        description=(
            "Path to ``clusters.yaml``. When unset, the cluster manager uses "
            "``~/.config/mosk-mcp/clusters.yaml``."
        ),
    )
    profile: str | None = Field(
        default=None,
        description=(
            "Active cluster id under ``clusters:``; when set, overrides the ``active`` key in the "
            "file after load."
        ),
    )

    ssl_verify: bool = Field(
        default=True,
        description=(
            "Verify TLS certificates for HTTPS calls to MCC and APIs. Set false only for "
            "self-signed certs in dev/lab; prefer ``ssl_ca_cert_path`` when possible."
        ),
    )
    ssl_ca_cert_path: Path | None = Field(
        default=None,
        description="Path to a CA bundle to trust (e.g. corporate root) in addition to system CAs.",
    )

    # --- OAuth 2.0 Device Flow (RFC 8628) ---
    device_flow_enabled: bool = Field(
        default=True,
        description="Use browser-based Device Flow for login (recommended); disable only for special testing.",
    )
    device_flow_client_id: str = Field(
        default="kaas",
        description="Keycloak OAuth client id used for Device Flow (must match MCC ``kaas`` client).",
    )
    device_flow_code_lifespan: Annotated[
        int,
        Field(
            ge=60,
            le=1800,
            description="Device code lifetime in seconds (should match Keycloak client settings).",
        ),
    ] = 600
    device_flow_poll_interval: Annotated[
        int,
        Field(
            ge=1,
            le=60,
            description="Seconds between polls to Keycloak while waiting for user authorization.",
        ),
    ] = 5
    device_flow_max_poll_attempts: Annotated[
        int,
        Field(
            ge=0,
            le=1000,
            description="Max poll attempts (0 = unlimited until the device code expires).",
        ),
    ] = 0
    device_flow_scope: str = Field(
        default="openid profile email offline_access",
        description="Space-separated OAuth scopes for Device Flow (``offline_access`` enables refresh tokens).",
    )

    # --- Privacy (LLM response redaction) ---
    privacy_enabled: bool = Field(
        default=False,
        description="Redact sensitive infrastructure data from tool outputs before LLMs see them.",
    )
    privacy_level: PrivacyLevel = Field(
        default="standard",
        description="Redaction profile: ``none``, ``minimal``, ``standard``, or ``aggressive``.",
    )
    privacy_redact_uuid: bool = Field(
        default=False,
        description="When true, also redact UUIDs (e.g. volume IDs); implied by aggressive level.",
    )
    privacy_preserve_structure: bool = Field(
        default=True,
        description="Keep placeholder tokens like ``[IP-1]`` vs replacing all with ``[REDACTED]``.",
    )

    @field_validator("audit_log_path", mode="before")
    @classmethod
    def validate_audit_log_path(cls, v: Any) -> Path:
        """Validate and convert audit log path."""
        if isinstance(v, str):
            return Path(v)
        if isinstance(v, Path):
            return v
        return Path(str(v))

    @field_validator("config_path", mode="before")
    @classmethod
    def validate_clusters_config_path(cls, v: Any) -> Path | None:
        """Normalize multi-cluster config path; empty means use default file location."""
        if v is None or v == "":
            return None
        if isinstance(v, Path):
            return v
        s = str(v).strip()
        return None if not s else Path(s)

    @field_validator("profile", mode="before")
    @classmethod
    def validate_cluster_profile(cls, v: Any) -> str | None:
        """Treat blank profile override as unset."""
        if v is None or v == "":
            return None
        s = str(v).strip()
        return None if not s else s

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

        return validate_http_url(url)

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
            raise ValueError(
                "MCP_OTEL_EXPORTER_ENDPOINT must be set when MCP_OTEL_ENABLED is true"
            )
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


_settings: Settings | None = None


def init_settings(settings: Settings) -> None:
    """Install the process-wide settings singleton.

    Must be called exactly once before :func:`get_settings`. Typically invoked from the
    server entrypoint with CLI/env-merged :class:`Settings` (or ``Settings()`` for env-only).

    Raises:
        RuntimeError: If called more than once per process (tests should call
            :func:`reset_settings_for_testing` between scenarios).
    """
    global _settings
    if _settings is not None:
        raise RuntimeError("init_settings() has already been called")
    _settings = settings


def get_settings() -> Settings:
    """Return the settings installed by :func:`init_settings`.

    Raises:
        RuntimeError: If :func:`init_settings` has not been called yet.
    """
    if _settings is None:
        raise RuntimeError(
            "get_settings() called before init_settings(); "
            "call init_settings() from the application entrypoint first"
        )
    return _settings


def reset_settings_for_testing() -> None:
    """Clear the settings singleton so another :func:`init_settings` can run.

    Intended for the test suite only; production code should not rely on re-initialization.
    """
    global _settings
    _settings = None
