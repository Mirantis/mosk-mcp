"""Tests for configuration management."""

import pytest

from mosk_mcp.core.config import (
    Environment,
    LogFormat,
    LogLevel,
    Settings,
    TransportType,
    get_settings,
    reload_settings,
)


class TestSettings:
    """Tests for Settings class."""

    def test_default_values(self) -> None:
        """Test that default values are set correctly.

        Note: We set log_format=CONSOLE and environment=DEVELOPMENT
        to test development mode where MCC URL is not required.
        """
        settings = Settings(
            auth_enabled=False,
            log_format=LogFormat.CONSOLE,
            environment=Environment.DEVELOPMENT,
        )

        assert settings.app_name == "mosk-mcp"
        assert settings.app_version == "0.1.0"
        assert settings.transport == TransportType.STDIO
        assert settings.http_port == 8080
        assert settings.log_level == LogLevel.INFO
        assert settings.log_format == LogFormat.CONSOLE
        assert settings.auth_enabled is False
        assert settings.kubernetes_namespace == "default"

    def test_transport_enum_values(self) -> None:
        """Test transport enum values."""
        assert TransportType.STDIO.value == "stdio"
        assert TransportType.HTTP.value == "http"
        assert TransportType.STREAMABLE_HTTP.value == "streamable-http"

    def test_log_level_enum_values(self) -> None:
        """Test log level enum values."""
        assert LogLevel.DEBUG.value == "DEBUG"
        assert LogLevel.INFO.value == "INFO"
        assert LogLevel.WARNING.value == "WARNING"
        assert LogLevel.ERROR.value == "ERROR"

    def test_env_var_override(self, env_vars: dict[str, str]) -> None:
        """Test that environment variables override defaults."""
        env_vars["MCP_TRANSPORT"] = "http"
        env_vars["MCP_HTTP_PORT"] = "9090"
        env_vars["MCP_LOG_LEVEL"] = "DEBUG"
        env_vars["MCP_AUTH_ENABLED"] = "false"
        env_vars["MCP_LOG_FORMAT"] = "console"
        env_vars["MCP_ENVIRONMENT"] = "development"

        # Clear cache and reload
        settings = reload_settings()

        assert settings.transport == TransportType.HTTP
        assert settings.http_port == 9090
        assert settings.log_level == LogLevel.DEBUG

    def test_http_port_validation(self) -> None:
        """Test HTTP port validation."""
        # Valid port
        settings = Settings(
            http_port=8080,
            auth_enabled=False,
            log_format=LogFormat.CONSOLE,
            environment=Environment.DEVELOPMENT,
        )
        assert settings.http_port == 8080

        # Invalid ports should raise
        with pytest.raises(ValueError):
            Settings(
                http_port=0,
                auth_enabled=False,
                log_format=LogFormat.CONSOLE,
                environment=Environment.DEVELOPMENT,
            )

        with pytest.raises(ValueError):
            Settings(
                http_port=70000,
                auth_enabled=False,
                log_format=LogFormat.CONSOLE,
                environment=Environment.DEVELOPMENT,
            )

    def test_is_development_property(self) -> None:
        """Test is_development property."""
        # Development mode (explicit environment)
        dev_settings = Settings(
            environment=Environment.DEVELOPMENT,
        )
        assert dev_settings.is_development is True
        assert dev_settings.is_production is False

        # Production mode (explicit environment) - requires auth enabled and MCC URL
        prod_settings = Settings(
            environment=Environment.PRODUCTION,
            auth_enabled=True,
            mcc_url="https://172.16.166.22",
        )
        assert prod_settings.is_development is False
        assert prod_settings.is_production is True

    def test_auth_validation_production_disabled_not_allowed(self) -> None:
        """Test that production mode does NOT allow auth to be disabled.

        SECURITY: Authentication cannot be disabled in production mode.
        This is enforced by the Settings validation.
        """
        with pytest.raises(ValueError, match="Authentication cannot be disabled in production"):
            Settings(
                environment=Environment.PRODUCTION,
                auth_enabled=False,
                mcc_url="https://172.16.166.22",
            )

    def test_otel_validation(self) -> None:
        """Test OpenTelemetry configuration validation."""
        # OTEL enabled without endpoint should raise
        with pytest.raises(ValueError, match="OTEL_EXPORTER_ENDPOINT"):
            Settings(
                otel_enabled=True,
                otel_exporter_endpoint=None,
                auth_enabled=False,
                log_format=LogFormat.CONSOLE,
                environment=Environment.DEVELOPMENT,
            )

        # OTEL enabled with endpoint should work
        settings = Settings(
            otel_enabled=True,
            otel_exporter_endpoint="http://localhost:4317",
            auth_enabled=False,
            log_format=LogFormat.CONSOLE,
            environment=Environment.DEVELOPMENT,
        )
        assert settings.otel_enabled is True

    def test_request_timeout_validation(self) -> None:
        """Test request timeout validation."""
        # Valid timeout
        settings = Settings(
            request_timeout=60,
            auth_enabled=False,
            log_format=LogFormat.CONSOLE,
            environment=Environment.DEVELOPMENT,
        )
        assert settings.request_timeout == 60

        # Invalid timeouts should raise
        with pytest.raises(ValueError):
            Settings(
                request_timeout=0,
                auth_enabled=False,
                log_format=LogFormat.CONSOLE,
                environment=Environment.DEVELOPMENT,
            )

        with pytest.raises(ValueError):
            Settings(
                request_timeout=500,
                auth_enabled=False,
                log_format=LogFormat.CONSOLE,
                environment=Environment.DEVELOPMENT,
            )

    def test_max_retries_validation(self) -> None:
        """Test max retries validation."""
        settings = Settings(
            max_retries=5,
            auth_enabled=False,
            log_format=LogFormat.CONSOLE,
            environment=Environment.DEVELOPMENT,
        )
        assert settings.max_retries == 5

        with pytest.raises(ValueError):
            Settings(
                max_retries=-1,
                auth_enabled=False,
                log_format=LogFormat.CONSOLE,
                environment=Environment.DEVELOPMENT,
            )

        with pytest.raises(ValueError):
            Settings(
                max_retries=15,
                auth_enabled=False,
                log_format=LogFormat.CONSOLE,
                environment=Environment.DEVELOPMENT,
            )


class TestSSOSettings:
    """Tests for SSO mode settings.

    SSO mode uses auto-discovery: only MCP_MCC_URL is required in production.
    In development mode, MCC URL is optional for testing.
    """

    def test_sso_mode_development_no_mcc_url(self) -> None:
        """Test that development mode doesn't require MCC URL."""
        settings = Settings(
            auth_enabled=False,
            log_format=LogFormat.CONSOLE,
            environment=Environment.DEVELOPMENT,
        )
        assert settings.mcc_url is None
        assert settings.is_development is True

    def test_sso_mode_production_requires_mcc_url(self) -> None:
        """Test that production mode requires MCC URL."""
        with pytest.raises(ValueError, match="MCC URL is required in production"):
            Settings(
                environment=Environment.PRODUCTION,
                auth_enabled=True,
                mcc_url=None,
            )

    def test_sso_mode_with_auto_discovery(self) -> None:
        """Test SSO mode with MCC URL (everything else auto-discovered)."""
        settings = Settings(
            mcc_url="https://172.16.166.22",
            auth_enabled=False,
            log_format=LogFormat.CONSOLE,
            environment=Environment.DEVELOPMENT,
        )
        assert settings.mcc_url == "https://172.16.166.22"
        # All other settings should be None (will be auto-discovered)
        assert settings.keycloak_url is None
        assert settings.keycloak_realm is None
        assert settings.mcc_oidc_client_id is None
        assert settings.prometheus_url is None
        assert settings.alertmanager_url is None

    def test_sso_mode_with_overrides(self) -> None:
        """Test SSO mode with optional override settings."""
        settings = Settings(
            mcc_url="https://172.16.166.22",
            # Optional overrides (normally auto-discovered)
            keycloak_url="https://keycloak.example.com",
            keycloak_realm="iam",
            mcc_oidc_client_id="kaas",
            prometheus_url="https://prometheus.example.com",
            alertmanager_url="https://alertmanager.example.com",
            auth_enabled=False,
            log_format=LogFormat.CONSOLE,
            environment=Environment.DEVELOPMENT,
        )
        assert settings.mcc_url == "https://172.16.166.22"
        assert settings.keycloak_url == "https://keycloak.example.com"
        assert settings.keycloak_realm == "iam"
        assert settings.mcc_oidc_client_id == "kaas"
        assert settings.prometheus_url == "https://prometheus.example.com"
        assert settings.alertmanager_url == "https://alertmanager.example.com"

    def test_development_mode_default(self) -> None:
        """Test that development mode is the default."""
        settings = Settings(
            auth_enabled=False,
            log_format=LogFormat.CONSOLE,
            environment=Environment.DEVELOPMENT,
        )
        assert settings.is_development is True
        assert settings.is_production is False


class TestGetSettings:
    """Tests for settings caching."""

    def test_settings_are_cached(self, env_vars: dict[str, str]) -> None:
        """Test that get_settings returns cached instance."""
        env_vars["MCP_AUTH_ENABLED"] = "false"
        env_vars["MCP_LOG_FORMAT"] = "console"
        env_vars["MCP_ENVIRONMENT"] = "development"

        # Clear cache first
        get_settings.cache_clear()

        settings1 = get_settings()
        settings2 = get_settings()

        assert settings1 is settings2

    def test_reload_settings_clears_cache(self, env_vars: dict[str, str]) -> None:
        """Test that reload_settings clears the cache."""
        env_vars["MCP_AUTH_ENABLED"] = "false"
        env_vars["MCP_LOG_FORMAT"] = "console"
        env_vars["MCP_ENVIRONMENT"] = "development"

        # Clear cache first
        get_settings.cache_clear()

        settings1 = get_settings()

        env_vars["MCP_LOG_LEVEL"] = "ERROR"
        settings2 = reload_settings()

        # Should be different instances
        assert settings1 is not settings2
        assert settings2.log_level == LogLevel.ERROR
