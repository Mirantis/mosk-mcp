"""Unit tests for OAuth 2.0 Device Authorization Grant (RFC 8628) implementation.

Tests cover:
- DeviceFlowAuthProvider initialization and configuration
- Device flow initiation
- Token polling with various response scenarios
- Error handling (expired, denied, network errors)
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mosk_mcp.auth.device_flow import (
    DeviceAuthorizationResponse,
    DeviceFlowAuthProvider,
    DeviceFlowResult,
    DeviceFlowStatus,
)
from mosk_mcp.auth.keycloak_client import MCCEndpoints, TokenResponse
from mosk_mcp.core.exceptions import AuthenticationError


class TestDeviceAuthorizationResponse:
    """Tests for DeviceAuthorizationResponse dataclass."""

    def test_creation(self) -> None:
        """Test creating a DeviceAuthorizationResponse."""
        response = DeviceAuthorizationResponse(
            device_code="device123",
            user_code="ABCD-EFGH",
            verification_uri="https://keycloak/device",
            verification_uri_complete="https://keycloak/device?user_code=ABCD-EFGH",
            expires_in=600,
            interval=5,
        )

        assert response.device_code == "device123"
        assert response.user_code == "ABCD-EFGH"
        assert response.verification_uri == "https://keycloak/device"
        assert response.expires_in == 600
        assert response.interval == 5

    def test_is_expired_false(self) -> None:
        """Test is_expired returns False for fresh response."""
        response = DeviceAuthorizationResponse(
            device_code="device123",
            user_code="ABCD-EFGH",
            verification_uri="https://keycloak/device",
            verification_uri_complete="https://keycloak/device?user_code=ABCD-EFGH",
            expires_in=600,
            interval=5,
        )

        assert not response.is_expired
        assert response.time_remaining > 0

    def test_is_expired_true(self) -> None:
        """Test is_expired returns True for expired response."""
        response = DeviceAuthorizationResponse(
            device_code="device123",
            user_code="ABCD-EFGH",
            verification_uri="https://keycloak/device",
            verification_uri_complete="https://keycloak/device?user_code=ABCD-EFGH",
            expires_in=0,  # Already expired
            interval=5,
            issued_at=datetime.now(UTC) - timedelta(seconds=10),
        )

        assert response.is_expired
        assert response.time_remaining == 0

    def test_to_dict(self) -> None:
        """Test converting response to dictionary."""
        response = DeviceAuthorizationResponse(
            device_code="device123",
            user_code="ABCD-EFGH",
            verification_uri="https://keycloak/device",
            verification_uri_complete="https://keycloak/device?user_code=ABCD-EFGH",
            expires_in=600,
            interval=5,
        )

        data = response.to_dict()

        assert data["user_code"] == "ABCD-EFGH"
        assert data["verification_uri"] == "https://keycloak/device"
        assert "device_code" not in data  # Should not expose device code
        assert data["expires_in"] == 600
        assert "time_remaining" in data


class TestDeviceFlowAuthProvider:
    """Tests for DeviceFlowAuthProvider."""

    @pytest.fixture
    def provider(self) -> DeviceFlowAuthProvider:
        """Create a DeviceFlowAuthProvider instance."""
        return DeviceFlowAuthProvider(
            keycloak_url="https://keycloak.example.com",
            realm="iam",
            client_id="kaas",
            verify_ssl=False,
        )

    def test_initialization(self, provider: DeviceFlowAuthProvider) -> None:
        """Test provider initialization."""
        assert provider.keycloak_url == "https://keycloak.example.com"
        assert provider.realm == "iam"
        assert provider.client_id == "kaas"

    def test_endpoints(self, provider: DeviceFlowAuthProvider) -> None:
        """Test endpoint URL generation."""
        assert (
            provider.device_authorization_endpoint
            == "https://keycloak.example.com/auth/realms/iam/protocol/openid-connect/auth/device"
        )
        assert (
            provider.token_endpoint
            == "https://keycloak.example.com/auth/realms/iam/protocol/openid-connect/token"
        )
        assert provider.issuer_url == "https://keycloak.example.com/auth/realms/iam"

    def test_from_mcc_endpoints(self) -> None:
        """Test creating provider from MCCEndpoints."""
        endpoints = MCCEndpoints(
            keycloak_url="https://keycloak.example.com",
            keycloak_realm="iam",
            keycloak_client_id="kaas",
            k8s_api_url="https://k8s.example.com",
            prometheus_url="https://prometheus.example.com",
            alertmanager_url="https://alertmanager.example.com",
        )

        provider = DeviceFlowAuthProvider.from_mcc_endpoints(
            endpoints,
            client_id="kaas",
        )

        assert provider.keycloak_url == "https://keycloak.example.com"
        assert provider.realm == "iam"
        assert provider.client_id == "kaas"

    @pytest.mark.asyncio
    async def test_init_client(self, provider: DeviceFlowAuthProvider) -> None:
        """Test HTTP client initialization."""
        assert provider._http_client is None

        await provider.init_client()
        assert provider._http_client is not None

        await provider.close()
        assert provider._http_client is None

    @pytest.mark.asyncio
    async def test_context_manager(self, provider: DeviceFlowAuthProvider) -> None:
        """Test async context manager."""
        async with provider:
            assert provider._http_client is not None

        assert provider._http_client is None

    @pytest.mark.asyncio
    async def test_ensure_client_not_initialized(self, provider: DeviceFlowAuthProvider) -> None:
        """Test _ensure_client raises error when not initialized."""
        with pytest.raises(RuntimeError, match="not initialized"):
            provider._ensure_client()

    @pytest.mark.asyncio
    async def test_initiate_device_flow_success(self, provider: DeviceFlowAuthProvider) -> None:
        """Test successful device flow initiation."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "device_code": "device123",
            "user_code": "ABCD-EFGH",
            "verification_uri": "https://keycloak/device",
            "verification_uri_complete": "https://keycloak/device?user_code=ABCD-EFGH",
            "expires_in": 600,
            "interval": 5,
        }

        async with provider:
            with patch.object(provider._http_client, "post", new_callable=AsyncMock) as mock_post:
                mock_post.return_value = mock_response

                result = await provider.initiate_device_flow()

                assert result.user_code == "ABCD-EFGH"
                assert result.device_code == "device123"
                assert result.verification_uri == "https://keycloak/device"
                assert result.expires_in == 600

                mock_post.assert_called_once()
                call_args = mock_post.call_args
                assert "client_id" in call_args.kwargs["data"]
                assert call_args.kwargs["data"]["client_id"] == "kaas"

    @pytest.mark.asyncio
    async def test_initiate_device_flow_unauthorized_client(
        self, provider: DeviceFlowAuthProvider
    ) -> None:
        """Test device flow initiation with unauthorized client."""
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.text = "Client not authorized"
        mock_response.headers = {"content-type": "application/json"}
        mock_response.json.return_value = {
            "error": "unauthorized_client",
            "error_description": "Device flow not enabled for client",
        }

        async with provider:
            with patch.object(provider._http_client, "post", new_callable=AsyncMock) as mock_post:
                mock_post.return_value = mock_response

                with pytest.raises(AuthenticationError) as exc_info:
                    await provider.initiate_device_flow()

                assert "not enabled" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_poll_for_token_success(self, provider: DeviceFlowAuthProvider) -> None:
        """Test successful token polling."""
        device_auth = DeviceAuthorizationResponse(
            device_code="device123",
            user_code="ABCD-EFGH",
            verification_uri="https://keycloak/device",
            verification_uri_complete="https://keycloak/device?user_code=ABCD-EFGH",
            expires_in=600,
            interval=1,  # Short interval for testing
        )

        # First response: pending, second response: success
        pending_response = MagicMock()
        pending_response.status_code = 400
        pending_response.headers = {"content-type": "application/json"}
        pending_response.json.return_value = {"error": "authorization_pending"}

        success_response = MagicMock()
        success_response.status_code = 200
        success_response.json.return_value = {
            "access_token": "access123",
            "id_token": "eyJ.test.token",
            "refresh_token": "refresh123",
            "token_type": "Bearer",
            "expires_in": 300,
            "refresh_expires_in": 1800,
            "scope": "openid profile email",
        }

        async with provider:
            with patch.object(provider._http_client, "post", new_callable=AsyncMock) as mock_post:
                mock_post.side_effect = [pending_response, success_response]

                tokens = await provider.poll_for_token(device_auth)

                assert tokens.access_token == "access123"
                assert tokens.refresh_token == "refresh123"
                assert mock_post.call_count == 2

    @pytest.mark.asyncio
    async def test_poll_for_token_expired(self, provider: DeviceFlowAuthProvider) -> None:
        """Test polling with expired device code."""
        device_auth = DeviceAuthorizationResponse(
            device_code="device123",
            user_code="ABCD-EFGH",
            verification_uri="https://keycloak/device",
            verification_uri_complete="https://keycloak/device?user_code=ABCD-EFGH",
            expires_in=0,  # Already expired
            interval=1,
            issued_at=datetime.now(UTC) - timedelta(seconds=10),
        )

        async with provider:
            with pytest.raises(AuthenticationError) as exc_info:
                await provider.poll_for_token(device_auth)

            # P0 fix changed message to "timeout exceeded" instead of "expired"
            error_msg = str(exc_info.value).lower()
            assert "expired" in error_msg or "timeout" in error_msg

    @pytest.mark.asyncio
    async def test_poll_for_token_access_denied(self, provider: DeviceFlowAuthProvider) -> None:
        """Test polling when user denies access."""
        device_auth = DeviceAuthorizationResponse(
            device_code="device123",
            user_code="ABCD-EFGH",
            verification_uri="https://keycloak/device",
            verification_uri_complete="https://keycloak/device?user_code=ABCD-EFGH",
            expires_in=600,
            interval=1,
        )

        denied_response = MagicMock()
        denied_response.status_code = 400
        denied_response.headers = {"content-type": "application/json"}
        denied_response.json.return_value = {"error": "access_denied"}

        async with provider:
            with patch.object(provider._http_client, "post", new_callable=AsyncMock) as mock_post:
                mock_post.return_value = denied_response

                with pytest.raises(AuthenticationError) as exc_info:
                    await provider.poll_for_token(device_auth)

                assert "denied" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_poll_for_token_slow_down(self, provider: DeviceFlowAuthProvider) -> None:
        """Test polling with slow_down response increases interval."""
        device_auth = DeviceAuthorizationResponse(
            device_code="device123",
            user_code="ABCD-EFGH",
            verification_uri="https://keycloak/device",
            verification_uri_complete="https://keycloak/device?user_code=ABCD-EFGH",
            expires_in=600,
            interval=1,
        )

        slow_down_response = MagicMock()
        slow_down_response.status_code = 400
        slow_down_response.headers = {"content-type": "application/json"}
        slow_down_response.json.return_value = {"error": "slow_down"}

        success_response = MagicMock()
        success_response.status_code = 200
        success_response.json.return_value = {
            "access_token": "access123",
            "id_token": "eyJ.test.token",
            "refresh_token": "refresh123",
            "token_type": "Bearer",
            "expires_in": 300,
        }

        async with provider:
            with patch.object(provider._http_client, "post", new_callable=AsyncMock) as mock_post:
                mock_post.side_effect = [slow_down_response, success_response]

                # Should handle slow_down and eventually succeed
                tokens = await provider.poll_for_token(device_auth, max_attempts=3)
                assert tokens.access_token == "access123"


class TestDeviceFlowResult:
    """Tests for DeviceFlowResult."""

    def test_success_result(self) -> None:
        """Test successful result creation."""
        tokens = TokenResponse(
            access_token="access123",
            id_token="eyJ.test.token",
            refresh_token="refresh123",
            token_type="Bearer",
            expires_in=300,
        )

        result = DeviceFlowResult(
            status=DeviceFlowStatus.COMPLETED,
            tokens=tokens,
        )

        assert result.is_success
        assert result.status == DeviceFlowStatus.COMPLETED
        assert result.tokens is not None

    def test_error_result(self) -> None:
        """Test error result creation."""
        result = DeviceFlowResult(
            status=DeviceFlowStatus.ERROR,
            error_message="Something went wrong",
            error_code="some_error",
        )

        assert not result.is_success
        assert result.status == DeviceFlowStatus.ERROR
        assert result.error_message == "Something went wrong"

    def test_to_dict(self) -> None:
        """Test converting result to dictionary."""
        result = DeviceFlowResult(
            status=DeviceFlowStatus.EXPIRED,
            error_message="Device code expired",
        )

        data = result.to_dict()

        assert data["status"] == "expired"
        assert data["success"] is False
        assert data["error_message"] == "Device code expired"


class TestDeviceFlowIntegration:
    """Integration tests for device flow (mocked Keycloak)."""

    @pytest.mark.asyncio
    async def test_full_flow_success(self) -> None:
        """Test complete device flow from initiation to token."""
        provider = DeviceFlowAuthProvider(
            keycloak_url="https://keycloak.example.com",
            realm="iam",
            client_id="kaas",
            verify_ssl=False,
        )

        # Mock device authorization response
        device_response = MagicMock()
        device_response.status_code = 200
        device_response.json.return_value = {
            "device_code": "device123",
            "user_code": "ABCD-EFGH",
            "verification_uri": "https://keycloak/device",
            "verification_uri_complete": "https://keycloak/device?user_code=ABCD-EFGH",
            "expires_in": 600,
            "interval": 1,
        }

        # Mock token response (first pending, then success)
        pending_response = MagicMock()
        pending_response.status_code = 400
        pending_response.headers = {"content-type": "application/json"}
        pending_response.json.return_value = {"error": "authorization_pending"}

        success_response = MagicMock()
        success_response.status_code = 200
        success_response.json.return_value = {
            "access_token": "access123",
            "id_token": "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJ1c2VyMTIzIiwibmFtZSI6IlRlc3QgVXNlciIsImVtYWlsIjoidGVzdEBleGFtcGxlLmNvbSIsImlhbV9yb2xlcyI6WyJhZG1pbiJdfQ.signature",
            "refresh_token": "refresh123",
            "token_type": "Bearer",
            "expires_in": 300,
            "refresh_expires_in": 1800,
            "scope": "openid profile email",
        }

        async with provider:
            with patch.object(provider._http_client, "post", new_callable=AsyncMock) as mock_post:
                # Setup mock responses in order
                mock_post.side_effect = [
                    device_response,  # initiate_device_flow
                    pending_response,  # poll 1
                    success_response,  # poll 2
                ]

                # Run full authentication flow
                result = await provider.authenticate()

                assert result.is_success
                assert result.status == DeviceFlowStatus.COMPLETED
                assert result.tokens is not None
                assert result.tokens.access_token == "access123"
