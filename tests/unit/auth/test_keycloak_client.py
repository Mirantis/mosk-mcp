"""Unit tests for mosk_mcp.auth.keycloak_client module.

Tests JWT utilities, token response handling, endpoint discovery,
kubeconfig generation, and token exchange functionality.
"""

import base64
import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from mosk_mcp.auth.keycloak_client import (
    ClusterOIDCInfo,
    MCCEndpoints,
    TokenResponse,
    decode_jwt_payload,
    discover_mcc_endpoints,
    discover_stacklight_endpoints,
    exchange_token_for_audience,
    generate_cluster_kubeconfig,
    generate_token_kubeconfig,
    get_cluster_oidc_info,
    get_iam_roles,
    get_jwt_claim,
)
from mosk_mcp.core.exceptions import AuthenticationError


# =============================================================================
# JWT Utilities Tests
# =============================================================================


class TestDecodeJWTPayload:
    """Tests for decode_jwt_payload function."""

    def _create_jwt(self, payload: dict) -> str:
        """Create a mock JWT token with given payload."""
        header = base64.urlsafe_b64encode(b'{"alg":"RS256","typ":"JWT"}').rstrip(b"=")
        payload_b64 = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=")
        signature = base64.urlsafe_b64encode(b"mock_signature").rstrip(b"=")
        return f"{header.decode()}.{payload_b64.decode()}.{signature.decode()}"

    def test_decode_valid_jwt(self) -> None:
        """Test decoding a valid JWT token."""
        payload = {
            "sub": "user-123",
            "preferred_username": "testuser",
            "iam_roles": ["m:kaas:admin", "m:kaas:viewer"],
            "exp": 1234567890,
        }
        token = self._create_jwt(payload)

        result = decode_jwt_payload(token)

        assert result["sub"] == "user-123"
        assert result["preferred_username"] == "testuser"
        assert result["iam_roles"] == ["m:kaas:admin", "m:kaas:viewer"]

    def test_decode_empty_token(self) -> None:
        """Test decoding an empty token returns None."""
        result = decode_jwt_payload("")
        assert result is None

    def test_decode_none_token(self) -> None:
        """Test decoding None token returns None."""
        result = decode_jwt_payload(None)  # type: ignore[arg-type]
        assert result is None

    def test_decode_invalid_format(self) -> None:
        """Test decoding token with wrong number of parts returns None."""
        result = decode_jwt_payload("header.payload")
        assert result is None

        result = decode_jwt_payload("single_part")
        assert result is None

    def test_decode_invalid_base64(self) -> None:
        """Test decoding token with invalid base64 in payload returns None."""
        result = decode_jwt_payload("header.!!!invalid!!!.signature")
        assert result is None

    def test_decode_invalid_json(self) -> None:
        """Test decoding token with invalid JSON in payload returns None."""
        # Create valid base64 but invalid JSON
        invalid_json = base64.urlsafe_b64encode(b"not valid json").rstrip(b"=")
        result = decode_jwt_payload(f"header.{invalid_json.decode()}.signature")
        assert result is None


class TestGetJWTClaim:
    """Tests for get_jwt_claim function."""

    def _create_jwt(self, payload: dict) -> str:
        """Create a mock JWT token with given payload."""
        header = base64.urlsafe_b64encode(b'{"alg":"RS256"}').rstrip(b"=")
        payload_b64 = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=")
        signature = base64.urlsafe_b64encode(b"sig").rstrip(b"=")
        return f"{header.decode()}.{payload_b64.decode()}.{signature.decode()}"

    def test_get_existing_claim(self) -> None:
        """Test extracting an existing claim."""
        token = self._create_jwt({"username": "test", "role": "admin"})
        assert get_jwt_claim(token, "username") == "test"
        assert get_jwt_claim(token, "role") == "admin"

    def test_get_missing_claim_returns_default(self) -> None:
        """Test that missing claim returns default value."""
        token = self._create_jwt({"username": "test"})
        assert get_jwt_claim(token, "missing") is None
        assert get_jwt_claim(token, "missing", "default") == "default"

    def test_get_claim_from_invalid_token(self) -> None:
        """Test that invalid token returns default."""
        assert get_jwt_claim("invalid", "claim", "default") == "default"


class TestGetIAMRoles:
    """Tests for get_iam_roles function."""

    def _create_jwt(self, payload: dict) -> str:
        """Create a mock JWT token with given payload."""
        header = base64.urlsafe_b64encode(b'{"alg":"RS256"}').rstrip(b"=")
        payload_b64 = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=")
        signature = base64.urlsafe_b64encode(b"sig").rstrip(b"=")
        return f"{header.decode()}.{payload_b64.decode()}.{signature.decode()}"

    def test_get_iam_roles(self) -> None:
        """Test extracting IAM roles from token."""
        roles = ["m:kaas:admin", "m:kaas:viewer", "m:sl:lab:mos@stacklight-admin"]
        token = self._create_jwt({"iam_roles": roles})

        result = get_iam_roles(token)
        assert result == roles

    def test_get_iam_roles_empty(self) -> None:
        """Test that missing iam_roles returns empty list."""
        token = self._create_jwt({"username": "test"})
        assert get_iam_roles(token) == []

    def test_get_iam_roles_invalid_token(self) -> None:
        """Test that invalid token returns empty list."""
        assert get_iam_roles("invalid") == []


# =============================================================================
# TokenResponse Tests
# =============================================================================


class TestTokenResponse:
    """Tests for TokenResponse dataclass."""

    def _create_jwt(self, payload: dict) -> str:
        """Create a mock JWT token with given payload."""
        header = base64.urlsafe_b64encode(b'{"alg":"RS256"}').rstrip(b"=")
        payload_b64 = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=")
        signature = base64.urlsafe_b64encode(b"sig").rstrip(b"=")
        return f"{header.decode()}.{payload_b64.decode()}.{signature.decode()}"

    def test_token_response_creation(self) -> None:
        """Test creating a TokenResponse with all fields."""
        id_token = self._create_jwt(
            {
                "preferred_username": "testuser",
                "sub": "user-123",
                "iam_roles": ["admin"],
            }
        )

        response = TokenResponse(
            access_token="access123",
            id_token=id_token,
            refresh_token="refresh456",
            expires_in=1800,
        )

        assert response.access_token == "access123"
        assert response.refresh_token == "refresh456"
        assert response.expires_in == 1800
        assert response.username == "testuser"
        assert response.subject == "user-123"
        assert response.iam_roles == ["admin"]

    def test_token_response_expiry(self) -> None:
        """Test token expiry calculations."""
        id_token = self._create_jwt({"preferred_username": "test"})

        # Token expiring in 30 seconds - should be considered expired (30s buffer)
        response = TokenResponse(
            access_token="access",
            id_token=id_token,
            refresh_token="refresh",
            expires_in=30,
        )
        assert response.is_expired is True

        # Token expiring in 5 minutes - should not be expired
        response = TokenResponse(
            access_token="access",
            id_token=id_token,
            refresh_token="refresh",
            expires_in=300,
        )
        assert response.is_expired is False

    def test_token_response_expiry_with_buffer(self) -> None:
        """Test is_expired_with_buffer method."""
        id_token = self._create_jwt({"preferred_username": "test"})

        # Token expiring in 90 seconds
        response = TokenResponse(
            access_token="access",
            id_token=id_token,
            refresh_token="refresh",
            expires_in=90,
        )

        # With 60 second buffer, should not be expired
        assert response.is_expired_with_buffer(60) is False

        # With 120 second buffer, should be expired
        assert response.is_expired_with_buffer(120) is True

    def test_token_response_to_dict(self) -> None:
        """Test to_dict serialization."""
        id_token = self._create_jwt(
            {
                "preferred_username": "testuser",
                "sub": "user-123",
                "iam_roles": ["admin"],
            }
        )

        response = TokenResponse(
            access_token="access",
            id_token=id_token,
            refresh_token="refresh",
            expires_in=1800,
            scope="openid profile",
        )

        result = response.to_dict()

        assert result["username"] == "testuser"
        assert result["subject"] == "user-123"
        assert result["expires_in"] == 1800
        assert result["iam_roles"] == ["admin"]
        assert result["scope"] == "openid profile"
        assert "is_expired" in result


# =============================================================================
# MCCEndpoints Tests
# =============================================================================


class TestMCCEndpoints:
    """Tests for MCCEndpoints dataclass."""

    def test_endpoint_urls(self) -> None:
        """Test derived endpoint URLs."""
        endpoints = MCCEndpoints(
            keycloak_url="https://keycloak.example.com",
            keycloak_realm="iam",
            keycloak_client_id="kaas",
            k8s_api_url="https://k8s.example.com:6443",
        )

        assert endpoints.token_endpoint == (
            "https://keycloak.example.com/auth/realms/iam/protocol/openid-connect/token"
        )
        assert endpoints.userinfo_endpoint == (
            "https://keycloak.example.com/auth/realms/iam/protocol/openid-connect/userinfo"
        )
        assert endpoints.issuer_url == "https://keycloak.example.com/auth/realms/iam"

    def test_endpoints_to_dict(self) -> None:
        """Test to_dict serialization."""
        endpoints = MCCEndpoints(
            keycloak_url="https://keycloak.example.com",
            keycloak_realm="iam",
            k8s_api_url="https://k8s.example.com:6443",
            prometheus_url="https://prometheus.example.com",
        )

        result = endpoints.to_dict()

        assert result["keycloak_url"] == "https://keycloak.example.com"
        assert result["keycloak_realm"] == "iam"
        assert result["k8s_api_url"] == "https://k8s.example.com:6443"
        assert result["prometheus_url"] == "https://prometheus.example.com"


# =============================================================================
# Endpoint Discovery Tests
# =============================================================================


class TestDiscoverMCCEndpoints:
    """Tests for discover_mcc_endpoints function."""

    @pytest.mark.asyncio
    async def test_discover_endpoints_success(self) -> None:
        """Test successful endpoint discovery."""
        config_js = """
        window.CONFIG = {
            "keycloak": {
                "url": "https://172.16.166.23/auth/realms/iam",
                "realm": "iam",
                "clientId": "kaas"
            },
            "kubernetes": {
                "server": "https://172.16.166.22:443"
            }
        };
        """

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = config_js

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(return_value=mock_response)
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_instance

            endpoints = await discover_mcc_endpoints("172.16.166.22")

            assert endpoints.keycloak_url == "https://172.16.166.23"
            assert endpoints.keycloak_realm == "iam"
            assert endpoints.keycloak_client_id == "kaas"
            assert endpoints.k8s_api_url == "https://172.16.166.22:443"

    @pytest.mark.asyncio
    async def test_discover_endpoints_url_normalization(self) -> None:
        """Test URL normalization (adding https://, removing trailing slash)."""
        config_js = '{"keycloak": {"url": "https://kc.example.com"}, "kubernetes": {}}'

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = config_js

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(return_value=mock_response)
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_instance

            # Test without https:// prefix
            await discover_mcc_endpoints("example.com")
            mock_instance.get.assert_called_with("https://example.com/config.js")

    @pytest.mark.asyncio
    async def test_discover_endpoints_http_error(self) -> None:
        """Test discovery failure on HTTP error."""
        mock_response = MagicMock()
        mock_response.status_code = 404

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(return_value=mock_response)
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_instance

            with pytest.raises(AuthenticationError, match="Failed to fetch MCC UI config"):
                await discover_mcc_endpoints("https://example.com")

    @pytest.mark.asyncio
    async def test_discover_endpoints_no_json(self) -> None:
        """Test discovery failure when no JSON found in config.js."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "// Just a comment, no JSON"

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(return_value=mock_response)
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_instance

            with pytest.raises(AuthenticationError, match="no JSON object found"):
                await discover_mcc_endpoints("https://example.com")

    @pytest.mark.asyncio
    async def test_discover_endpoints_connection_error(self) -> None:
        """Test discovery failure on connection error."""
        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(side_effect=httpx.RequestError("Connection failed"))
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_instance

            with pytest.raises(AuthenticationError, match="Failed to connect"):
                await discover_mcc_endpoints("https://example.com")


class TestDiscoverStacklightEndpoints:
    """Tests for discover_stacklight_endpoints function."""

    @pytest.mark.asyncio
    async def test_discover_stacklight_success(self) -> None:
        """Test successful StackLight endpoint discovery."""
        services_response = {
            "items": [
                {
                    "metadata": {"name": "prometheus-iam-proxy"},
                    "spec": {
                        "type": "LoadBalancer",
                        "ports": [{"port": 443}],
                    },
                    "status": {"loadBalancer": {"ingress": [{"ip": "10.0.0.1"}]}},
                },
                {
                    "metadata": {"name": "alertmanager-iam-proxy"},
                    "spec": {
                        "type": "LoadBalancer",
                        "ports": [{"port": 443}],
                    },
                    "status": {"loadBalancer": {"ingress": [{"ip": "10.0.0.2"}]}},
                },
            ]
        }

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = services_response

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(return_value=mock_response)
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_instance

            result = await discover_stacklight_endpoints(
                "https://k8s.example.com",
                "mock-token",
            )

            assert result["prometheus_url"] == "https://10.0.0.1"
            assert result["alertmanager_url"] == "https://10.0.0.2"

    @pytest.mark.asyncio
    async def test_discover_stacklight_rbac_denied(self) -> None:
        """Test StackLight discovery when RBAC denies access."""
        mock_response = MagicMock()
        mock_response.status_code = 403

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(return_value=mock_response)
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_instance

            result = await discover_stacklight_endpoints(
                "https://k8s.example.com",
                "mock-token",
            )

            # Should return empty dict, not raise
            assert result == {
                "prometheus_url": "",
                "alertmanager_url": "",
                "grafana_url": "",
                "opensearch_url": "",
            }


# =============================================================================
# Kubeconfig Generation Tests
# =============================================================================


class TestGenerateTokenKubeconfig:
    """Tests for generate_token_kubeconfig function."""

    def _create_jwt(self, payload: dict) -> str:
        """Create a mock JWT token with given payload."""
        header = base64.urlsafe_b64encode(b'{"alg":"RS256"}').rstrip(b"=")
        payload_b64 = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=")
        signature = base64.urlsafe_b64encode(b"sig").rstrip(b"=")
        return f"{header.decode()}.{payload_b64.decode()}.{signature.decode()}"

    def test_generate_kubeconfig_basic(self) -> None:
        """Test basic kubeconfig generation."""
        import yaml

        id_token = self._create_jwt({"preferred_username": "testuser"})
        endpoints = MCCEndpoints(
            keycloak_url="https://kc.example.com",
            k8s_api_url="https://k8s.example.com:6443",
        )
        tokens = TokenResponse(
            access_token="access",
            id_token=id_token,
            refresh_token="refresh",
        )

        result = generate_token_kubeconfig(endpoints, tokens, "test-cluster")
        config = yaml.safe_load(result)

        assert config["apiVersion"] == "v1"
        assert config["kind"] == "Config"
        assert config["current-context"] == "test-cluster-testuser"
        assert len(config["clusters"]) == 1
        assert config["clusters"][0]["name"] == "test-cluster"
        assert config["clusters"][0]["cluster"]["server"] == "https://k8s.example.com:6443"

    def test_generate_kubeconfig_with_ca_data(self) -> None:
        """Test kubeconfig generation with CA certificate."""
        import yaml

        id_token = self._create_jwt({"preferred_username": "testuser"})
        endpoints = MCCEndpoints(
            keycloak_url="https://kc.example.com",
            k8s_api_url="https://k8s.example.com:6443",
        )
        tokens = TokenResponse(
            access_token="access",
            id_token=id_token,
            refresh_token="refresh",
        )

        result = generate_token_kubeconfig(
            endpoints, tokens, "test-cluster", ca_data="BASE64CADATA"
        )
        config = yaml.safe_load(result)

        assert config["clusters"][0]["cluster"]["certificate-authority-data"] == "BASE64CADATA"
        assert "insecure-skip-tls-verify" not in config["clusters"][0]["cluster"]

    def test_generate_kubeconfig_insecure(self) -> None:
        """Test kubeconfig generation without CA (insecure)."""
        import yaml

        id_token = self._create_jwt({"preferred_username": "testuser"})
        endpoints = MCCEndpoints(
            keycloak_url="https://kc.example.com",
            k8s_api_url="https://k8s.example.com:6443",
        )
        tokens = TokenResponse(
            access_token="access",
            id_token=id_token,
            refresh_token="refresh",
        )

        result = generate_token_kubeconfig(endpoints, tokens, "test-cluster")
        config = yaml.safe_load(result)

        assert config["clusters"][0]["cluster"]["insecure-skip-tls-verify"] is True


class TestClusterOIDCInfo:
    """Tests for ClusterOIDCInfo dataclass."""

    def test_k8s_api_url(self) -> None:
        """Test Kubernetes API URL generation."""
        oidc_info = ClusterOIDCInfo(
            client_id="k8s",
            issuer_url="https://kc.example.com/auth/realms/iam",
            certificate="BASE64CERT",
            groups_claim="iam_roles",
            api_server_certificate="BASE64APICERT",
            load_balancer_host="10.0.0.100",
        )

        assert oidc_info.k8s_api_url == "https://10.0.0.100:443"


class TestGetClusterOIDCInfo:
    """Tests for get_cluster_oidc_info function."""

    @pytest.mark.asyncio
    async def test_get_cluster_oidc_info_success(self) -> None:
        """Test successful OIDC info retrieval."""
        cluster_cr = {
            "status": {
                "providerStatus": {
                    "loadBalancerHost": "10.0.0.100",
                    "apiServerCertificate": "BASE64CERT",
                    "oidc": {
                        "clientId": "k8s",
                        "issuerUrl": "https://kc.example.com/auth/realms/iam",
                        "certificate": "BASE64IDPCERT",
                        "groupsClaim": "iam_roles",
                    },
                }
            }
        }

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = cluster_cr

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(return_value=mock_response)
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_instance

            result = await get_cluster_oidc_info(
                "https://k8s.example.com",
                "mock-token",
                "mos",
                "lab",
            )

            assert result is not None
            assert result.client_id == "k8s"
            assert result.issuer_url == "https://kc.example.com/auth/realms/iam"
            assert result.load_balancer_host == "10.0.0.100"

    @pytest.mark.asyncio
    async def test_get_cluster_oidc_info_not_found(self) -> None:
        """Test OIDC info when cluster not found."""
        mock_response = MagicMock()
        mock_response.status_code = 404

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(return_value=mock_response)
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_instance

            result = await get_cluster_oidc_info(
                "https://k8s.example.com",
                "mock-token",
                "nonexistent",
                "lab",
            )

            assert result is None

    @pytest.mark.asyncio
    async def test_get_cluster_oidc_info_access_denied(self) -> None:
        """Test OIDC info when access denied."""
        mock_response = MagicMock()
        mock_response.status_code = 403

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(return_value=mock_response)
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_instance

            result = await get_cluster_oidc_info(
                "https://k8s.example.com",
                "mock-token",
                "mos",
                "lab",
            )

            assert result is None


class TestGenerateClusterKubeconfig:
    """Tests for generate_cluster_kubeconfig function."""

    def _create_jwt(self, payload: dict) -> str:
        """Create a mock JWT token with given payload."""
        header = base64.urlsafe_b64encode(b'{"alg":"RS256"}').rstrip(b"=")
        payload_b64 = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=")
        signature = base64.urlsafe_b64encode(b"sig").rstrip(b"=")
        return f"{header.decode()}.{payload_b64.decode()}.{signature.decode()}"

    def test_generate_cluster_kubeconfig(self) -> None:
        """Test cluster kubeconfig generation."""
        import yaml

        id_token = self._create_jwt({"preferred_username": "testuser"})

        oidc_info = ClusterOIDCInfo(
            client_id="k8s",
            issuer_url="https://kc.example.com/auth/realms/iam",
            certificate="BASE64IDPCERT",
            groups_claim="iam_roles",
            api_server_certificate="BASE64APICERT",
            load_balancer_host="10.0.0.100",
        )

        tokens = TokenResponse(
            access_token="access",
            id_token=id_token,
            refresh_token="refresh",
        )

        result = generate_cluster_kubeconfig(oidc_info, tokens, "mos")
        config = yaml.safe_load(result)

        assert config["current-context"] == "testuser@mos"
        assert config["clusters"][0]["cluster"]["server"] == "https://10.0.0.100:443"
        assert config["clusters"][0]["cluster"]["certificate-authority-data"] == "BASE64APICERT"
        assert config["users"][0]["user"]["token"] == id_token


# =============================================================================
# Token Exchange Tests
# =============================================================================


class TestExchangeTokenForAudience:
    """Tests for exchange_token_for_audience function."""

    @pytest.mark.asyncio
    async def test_token_exchange_success(self) -> None:
        """Test successful token exchange."""
        exchange_response = {
            "access_token": "new_access_token",
            "id_token": "new_id_token",
            "refresh_token": "new_refresh_token",
            "token_type": "Bearer",
            "expires_in": 1800,
        }

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = exchange_response

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.post = AsyncMock(return_value=mock_response)
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_instance

            result = await exchange_token_for_audience(
                "https://kc.example.com/auth/realms/iam",
                "source_token",
                "k8s",
            )

            assert result.access_token == "new_access_token"
            assert result.id_token == "new_id_token"
            assert result.refresh_token == "new_refresh_token"

    @pytest.mark.asyncio
    async def test_token_exchange_access_denied(self) -> None:
        """Test token exchange when access denied."""
        mock_response = MagicMock()
        mock_response.status_code = 403
        mock_response.headers = {"content-type": "application/json"}
        mock_response.json.return_value = {
            "error": "access_denied",
            "error_description": "Token exchange not permitted",
        }
        mock_response.text = "Token exchange not permitted"

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.post = AsyncMock(return_value=mock_response)
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_instance

            with pytest.raises(AuthenticationError, match="Token exchange denied"):
                await exchange_token_for_audience(
                    "https://kc.example.com/auth/realms/iam",
                    "source_token",
                    "k8s",
                )

    @pytest.mark.asyncio
    async def test_token_exchange_invalid_target(self) -> None:
        """Test token exchange with invalid target audience."""
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.headers = {"content-type": "application/json"}
        mock_response.json.return_value = {
            "error": "invalid_target",
            "error_description": "Unknown client",
        }
        mock_response.text = "Unknown client"

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.post = AsyncMock(return_value=mock_response)
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_instance

            with pytest.raises(AuthenticationError, match="Invalid target audience"):
                await exchange_token_for_audience(
                    "https://kc.example.com/auth/realms/iam",
                    "source_token",
                    "nonexistent",
                )

    @pytest.mark.asyncio
    async def test_token_exchange_connection_error(self) -> None:
        """Test token exchange on connection failure."""
        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.post = AsyncMock(side_effect=httpx.RequestError("Connection failed"))
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_instance

            with pytest.raises(AuthenticationError, match="Failed to connect"):
                await exchange_token_for_audience(
                    "https://kc.example.com/auth/realms/iam",
                    "source_token",
                    "k8s",
                )
