"""Tests for configuration module.

Tests cover:
- URL validation for config settings
- Environment-specific validation
- SSL configuration validation
- Settings loading and caching
"""

import os
from unittest.mock import patch

import pytest

from mosk_mcp.core.config import (
    LogFormat,
    Settings,
    TransportType,
    get_settings,
    init_settings,
    reset_settings_for_testing,
)


class TestURLValidation:
    """Tests for URL field validation."""

    def test_valid_https_url(self) -> None:
        """Test valid HTTPS URL is accepted."""
        with patch.dict(os.environ, {"MCP_MGMT_URL": "https://example.com"}, clear=False):
            settings = Settings()
            assert settings.mgmt_url == "https://example.com"

    def test_valid_http_url(self) -> None:
        """Test valid HTTP URL is accepted."""
        with patch.dict(os.environ, {"MCP_MGMT_URL": "http://example.com"}, clear=False):
            settings = Settings()
            assert settings.mgmt_url == "http://example.com"

    def test_url_with_port(self) -> None:
        """Test URL with port is accepted."""
        with patch.dict(os.environ, {"MCP_MGMT_URL": "https://example.com:8443"}, clear=False):
            settings = Settings()
            assert settings.mgmt_url == "https://example.com:8443"

    def test_url_with_ip_address(self) -> None:
        """Test URL with IP address is accepted."""
        with patch.dict(os.environ, {"MCP_MGMT_URL": "https://192.168.1.100"}, clear=False):
            settings = Settings()
            assert settings.mgmt_url == "https://192.168.1.100"

    def test_url_with_ip_and_port(self) -> None:
        """Test URL with IP address and port is accepted."""
        with patch.dict(os.environ, {"MCP_MGMT_URL": "https://172.16.166.22:443"}, clear=False):
            settings = Settings()
            assert settings.mgmt_url == "https://172.16.166.22:443"

    def test_url_with_path(self) -> None:
        """Test URL with path is accepted."""
        with patch.dict(os.environ, {"MCP_MGMT_URL": "https://example.com/api/v1"}, clear=False):
            settings = Settings()
            assert settings.mgmt_url == "https://example.com/api/v1"

    def test_url_trailing_slash_stripped(self) -> None:
        """Test trailing slashes are stripped for consistency."""
        with patch.dict(os.environ, {"MCP_MGMT_URL": "https://example.com/"}, clear=False):
            settings = Settings()
            assert settings.mgmt_url == "https://example.com"

    def test_url_multiple_trailing_slashes_stripped(self) -> None:
        """Test multiple trailing slashes are stripped."""
        with patch.dict(os.environ, {"MCP_MGMT_URL": "https://example.com///"}, clear=False):
            settings = Settings()
            assert settings.mgmt_url == "https://example.com"

    def test_url_whitespace_stripped(self) -> None:
        """Test whitespace around URL is stripped."""
        with patch.dict(os.environ, {"MCP_MGMT_URL": "  https://example.com  "}, clear=False):
            settings = Settings()
            assert settings.mgmt_url == "https://example.com"

    def test_empty_url_becomes_none(self) -> None:
        """Test empty string becomes None."""
        with patch.dict(os.environ, {"MCP_MGMT_URL": ""}, clear=False):
            settings = Settings()
            assert settings.mgmt_url is None

    def test_whitespace_only_url_becomes_none(self) -> None:
        """Test whitespace-only string becomes None."""
        with patch.dict(os.environ, {"MCP_MGMT_URL": "   "}, clear=False):
            settings = Settings()
            assert settings.mgmt_url is None

    def test_invalid_url_no_scheme(self) -> None:
        """Test URL without scheme is rejected."""
        with patch.dict(os.environ, {"MCP_MGMT_URL": "example.com"}, clear=False):
            with pytest.raises(ValueError, match="Invalid URL format"):
                Settings()

    def test_invalid_url_wrong_scheme(self) -> None:
        """Test URL with non-http scheme is rejected."""
        with patch.dict(os.environ, {"MCP_MGMT_URL": "ftp://example.com"}, clear=False):
            with pytest.raises(ValueError, match="Invalid URL format"):
                Settings()

    def test_invalid_url_no_hostname(self) -> None:
        """Test URL without hostname is rejected."""
        with patch.dict(os.environ, {"MCP_MGMT_URL": "https://"}, clear=False):
            with pytest.raises(ValueError, match="Invalid URL format"):
                Settings()

    def test_keycloak_url_validation(self) -> None:
        """Test Keycloak URL is validated."""
        with patch.dict(
            os.environ, {"MCP_KEYCLOAK_URL": "https://keycloak.example.com/"}, clear=False
        ):
            settings = Settings()
            assert settings.keycloak_url == "https://keycloak.example.com"

    def test_oidc_client_id_from_env(self) -> None:
        """Test OIDC client id is loaded from MCP_OIDC_CLIENT_ID."""
        with patch.dict(os.environ, {"MCP_OIDC_CLIENT_ID": "kaas"}, clear=False):
            settings = Settings()
            assert settings.oidc_client_id == "kaas"

    def test_prometheus_url_validation(self) -> None:
        """Test Prometheus URL is validated."""
        with patch.dict(
            os.environ, {"MCP_PROMETHEUS_URL": "https://prometheus.example.com:9090/"}, clear=False
        ):
            settings = Settings()
            assert settings.prometheus_url == "https://prometheus.example.com:9090"

    def test_alertmanager_url_validation(self) -> None:
        """Test Alertmanager URL is validated."""
        with patch.dict(
            os.environ, {"MCP_ALERTMANAGER_URL": "https://alertmanager.example.com/"}, clear=False
        ):
            settings = Settings()
            assert settings.alertmanager_url == "https://alertmanager.example.com"

    def test_opensearch_url_validation(self) -> None:
        """Test OpenSearch URL is validated."""
        with patch.dict(
            os.environ, {"MCP_OPENSEARCH_URL": "https://opensearch.example.com:9200/"}, clear=False
        ):
            settings = Settings()
            assert settings.opensearch_url == "https://opensearch.example.com:9200"

    def test_otel_exporter_url_validation(self) -> None:
        """Test OTEL exporter endpoint is validated."""
        with patch.dict(
            os.environ,
            {"MCP_OTEL_EXPORTER_ENDPOINT": "https://otel.example.com:4317/"},
            clear=False,
        ):
            settings = Settings()
            assert settings.otel_exporter_endpoint == "https://otel.example.com:4317"

    def test_ipv6_url(self) -> None:
        """Test IPv6 URL in brackets is accepted."""
        with patch.dict(os.environ, {"MCP_MGMT_URL": "https://[::1]:8443"}, clear=False):
            settings = Settings()
            assert settings.mgmt_url == "https://[::1]:8443"

    def test_ipv6_url_full(self) -> None:
        """Test full IPv6 URL is accepted."""
        with patch.dict(os.environ, {"MCP_MGMT_URL": "https://[2001:db8::1]:443"}, clear=False):
            settings = Settings()
            assert settings.mgmt_url == "https://[2001:db8::1]:443"


class TestEnvironmentValidation:
    """Tests for environment-specific validation."""

    def test_production_requires_auth(self) -> None:
        """Test production mode requires authentication enabled."""
        with patch.dict(
            os.environ,
            {
                "MCP_ENVIRONMENT": "production",
                "MCP_AUTH_ENABLED": "false",
                "MCP_MGMT_URL": "https://example.com",
            },
            clear=False,
        ), pytest.raises(ValueError, match="Authentication cannot be disabled in production"):
            Settings()

    def test_production_requires_mgmt_url(self) -> None:
        """Test production mode requires management cluster URL."""
        with patch.dict(
            os.environ,
            {
                "MCP_ENVIRONMENT": "production",
                "MCP_AUTH_ENABLED": "true",
            },
            clear=False,
        ):
            # Clear MCP_MGMT_URL if it was set
            env = os.environ.copy()
            env.pop("MCP_MGMT_URL", None)
            with patch.dict(os.environ, env, clear=True):
                with pytest.raises(ValueError, match="Management cluster URL is required in production"):
                    Settings()

    def test_development_allows_no_auth(self) -> None:
        """Test development mode allows auth disabled."""
        with patch.dict(
            os.environ,
            {
                "MCP_ENVIRONMENT": "development",
                "MCP_AUTH_ENABLED": "false",
            },
            clear=False,
        ):
            settings = Settings()
            assert settings.auth_enabled is False

    def test_is_production_explicit(self) -> None:
        """Test is_production with explicit setting."""
        with patch.dict(
            os.environ,
            {
                "MCP_ENVIRONMENT": "production",
                "MCP_AUTH_ENABLED": "true",
                "MCP_MGMT_URL": "https://example.com",
            },
            clear=False,
        ):
            settings = Settings()
            assert settings.is_production is True

    def test_is_development_explicit(self) -> None:
        """Test is_development with explicit setting."""
        with patch.dict(os.environ, {"MCP_ENVIRONMENT": "development"}, clear=False):
            settings = Settings()
            assert settings.is_development is True
            assert settings.is_production is False


class TestSSLConfiguration:
    """Tests for SSL/TLS configuration."""

    def test_ssl_verify_default_true(self) -> None:
        """Test SSL verification is enabled by default."""
        settings = Settings()
        assert settings.ssl_verify is True

    def test_ssl_verify_disabled_warning(self) -> None:
        """Test warning when SSL verification is disabled."""
        with patch.dict(os.environ, {"MCP_SSL_VERIFY": "false"}, clear=False):
            settings = Settings()
            assert settings.has_ssl_warning is True
            assert "SECURITY WARNING" in settings.ssl_warning_message

    def test_ssl_verify_enabled_no_warning(self) -> None:
        """Test no warning when SSL verification is enabled."""
        with patch.dict(os.environ, {"MCP_SSL_VERIFY": "true"}, clear=False):
            settings = Settings()
            assert settings.has_ssl_warning is False
            assert settings.ssl_warning_message is None


class TestOTELConfiguration:
    """Tests for OpenTelemetry configuration."""

    def test_otel_enabled_requires_endpoint(self) -> None:
        """Test OTEL enabled requires endpoint."""
        with patch.dict(
            os.environ,
            {"MCP_OTEL_ENABLED": "true"},
            clear=False,
        ):
            # Clear endpoint if set
            env = os.environ.copy()
            env.pop("MCP_OTEL_EXPORTER_ENDPOINT", None)
            with patch.dict(os.environ, env, clear=True):
                with patch.dict(os.environ, {"MCP_OTEL_ENABLED": "true"}, clear=False):
                    with pytest.raises(ValueError, match="OTEL_EXPORTER_ENDPOINT must be set"):
                        Settings()

    def test_otel_enabled_with_endpoint(self) -> None:
        """Test OTEL works with endpoint configured."""
        with patch.dict(
            os.environ,
            {
                "MCP_OTEL_ENABLED": "true",
                "MCP_OTEL_EXPORTER_ENDPOINT": "https://otel.example.com:4317",
            },
            clear=False,
        ):
            settings = Settings()
            assert settings.otel_enabled is True
            assert settings.otel_exporter_endpoint == "https://otel.example.com:4317"


class TestSettingsDefaults:
    """Tests for default settings values."""

    def test_default_transport(self) -> None:
        """Test default transport is STDIO."""
        settings = Settings()
        assert settings.transport == TransportType.STDIO

    def test_default_log_level(self) -> None:
        """Test default log level is INFO."""
        settings = Settings()
        assert settings.log_level.value == "INFO"

    def test_default_log_format(self) -> None:
        """Test default log format is JSON."""
        settings = Settings()
        assert settings.log_format == LogFormat.JSON

    def test_default_auth_enabled(self) -> None:
        """Test auth is enabled by default."""
        settings = Settings()
        assert settings.auth_enabled is True

    def test_default_namespace(self) -> None:
        """Test default namespace is 'default'."""
        settings = Settings()
        assert settings.kubernetes_namespace == "default"

    def test_default_request_timeout(self) -> None:
        """Test default request timeout."""
        settings = Settings()
        assert settings.request_timeout == 30

    def test_default_max_retries(self) -> None:
        """Test default max retries."""
        settings = Settings()
        assert settings.max_retries == 3


class TestInitSettings:
    """Tests for init_settings / get_settings."""

    def test_get_settings_returns_singleton_after_init(self) -> None:
        s = Settings()
        init_settings(s)
        assert get_settings() is s
        assert get_settings() is s

    def test_get_settings_before_init_raises(self) -> None:
        with pytest.raises(RuntimeError, match="init_settings"):
            get_settings()

    def test_init_settings_twice_raises(self) -> None:
        init_settings(Settings())
        with pytest.raises(RuntimeError, match="already been called"):
            init_settings(Settings())

    def test_reset_settings_for_testing_allows_second_init(self) -> None:
        init_settings(Settings(http_port=1111))
        assert get_settings().http_port == 1111
        reset_settings_for_testing()
        b = Settings(http_port=2222)
        init_settings(b)
        assert get_settings() is b

    def test_reinit_reads_updated_env(self, env_vars: dict[str, str]) -> None:
        """Changing env and re-init yields new Settings from env."""
        env_vars["MCP_AUTH_ENABLED"] = "false"
        env_vars["MCP_LOG_FORMAT"] = "console"
        env_vars["MCP_ENVIRONMENT"] = "development"
        env_vars["MCP_HTTP_PORT"] = "9123"
        init_settings(Settings())
        assert get_settings().http_port == 9123


class TestNumericConstraints:
    """Tests for numeric field constraints."""

    def test_http_port_range(self) -> None:
        """Test HTTP port must be in valid range."""
        with patch.dict(os.environ, {"MCP_HTTP_PORT": "0"}, clear=False):
            with pytest.raises(ValueError):
                Settings()

        with patch.dict(os.environ, {"MCP_HTTP_PORT": "70000"}, clear=False):
            with pytest.raises(ValueError):
                Settings()

    def test_request_timeout_range(self) -> None:
        """Test request timeout must be in valid range."""
        with patch.dict(os.environ, {"MCP_REQUEST_TIMEOUT": "0"}, clear=False):
            with pytest.raises(ValueError):
                Settings()

        with patch.dict(os.environ, {"MCP_REQUEST_TIMEOUT": "500"}, clear=False):
            with pytest.raises(ValueError):
                Settings()

    def test_max_retries_range(self) -> None:
        """Test max retries must be in valid range."""
        with patch.dict(os.environ, {"MCP_MAX_RETRIES": "-1"}, clear=False):
            with pytest.raises(ValueError):
                Settings()

        with patch.dict(os.environ, {"MCP_MAX_RETRIES": "20"}, clear=False):
            with pytest.raises(ValueError):
                Settings()
