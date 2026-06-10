"""Tests for session management edge cases.

Tests cover edge cases and error handling in:
- SessionState.is_token_expired()
- TokenBasedAuthAdapter
- UserSession initialization and lifecycle
- Temp file cleanup on failure and logout
- Token refresh flows
"""

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mosk_mcp.auth.keycloak_client import MCCEndpoints, TokenResponse
from mosk_mcp.auth.session import (
    SessionState,
    TokenBasedAuthAdapter,
    UserSession,
    _cleanup_session_temp_files,
)
from mosk_mcp.core.exceptions import AuthenticationError, ConfigurationError


class TestSessionState:
    """Tests for SessionState dataclass."""

    def test_is_token_expired_not_authenticated(self) -> None:
        """Token is considered expired if not authenticated."""
        state = SessionState(authenticated=False)
        assert state.is_token_expired() is True

    def test_authenticated_requires_fields(self) -> None:
        """Authenticated=True requires authenticated_at, token_expires_at, username."""
        with pytest.raises(ValueError, match=r"requires.*to be set"):
            SessionState(authenticated=True, token_expires_at=None)

        with pytest.raises(ValueError, match=r"requires.*to be set"):
            SessionState(
                authenticated=True,
                token_expires_at=datetime.now(UTC) + timedelta(hours=1),
                authenticated_at=None,
                username="test",
            )

    def test_is_token_expired_token_valid(self) -> None:
        """Token is not expired when expiry is in the future beyond buffer."""
        now = datetime.now(UTC)
        future_time = now + timedelta(minutes=5)
        state = SessionState(
            authenticated=True,
            token_expires_at=future_time,
            authenticated_at=now,
            username="test-user",
        )
        assert state.is_token_expired() is False

    def test_is_token_expired_within_buffer(self) -> None:
        """Token is considered expired within buffer time."""
        now = datetime.now(UTC)
        # Expires in 30 seconds, buffer is 60 seconds
        almost_expired = now + timedelta(seconds=30)
        state = SessionState(
            authenticated=True,
            token_expires_at=almost_expired,
            authenticated_at=now,
            username="test-user",
        )
        assert state.is_token_expired(buffer_seconds=60) is True

    def test_is_token_expired_custom_buffer(self) -> None:
        """Custom buffer time works correctly."""
        now = datetime.now(UTC)
        # Expires in 90 seconds
        almost_expired = now + timedelta(seconds=90)
        state = SessionState(
            authenticated=True,
            token_expires_at=almost_expired,
            authenticated_at=now,
            username="test-user",
        )

        # With 60 second buffer, should not be expired
        assert state.is_token_expired(buffer_seconds=60) is False

        # With 120 second buffer, should be expired
        assert state.is_token_expired(buffer_seconds=120) is True

    def test_is_token_expired_exact_boundary(self) -> None:
        """Token at exact boundary is considered expired."""
        now = datetime.now(UTC)
        # Expires exactly at now + buffer
        boundary_time = now + timedelta(seconds=60)
        state = SessionState(
            authenticated=True,
            token_expires_at=boundary_time,
            authenticated_at=now,
            username="test-user",
        )

        # At boundary should be expired (>= check)
        # Due to timing, this test may be flaky, so we use a small margin
        assert state.is_token_expired(buffer_seconds=60) is True


class TestTokenBasedAuthAdapter:
    """Tests for TokenBasedAuthAdapter."""

    @pytest.fixture
    def mock_session(self) -> MagicMock:
        """Create a mock UserSession."""
        now = datetime.now(UTC)
        session = MagicMock(spec=UserSession)
        session._lock = asyncio.Lock()
        session.state = SessionState(
            authenticated=True,
            token_expires_at=now + timedelta(hours=1),
            authenticated_at=now,
            username="test-user",
        )
        session._mcc_tokens = TokenResponse(
            access_token="access",
            id_token="id_token_value",
            refresh_token="refresh",
            token_type="Bearer",
            expires_in=3600,
        )
        session._refresh_tokens_unlocked = AsyncMock(return_value=True)
        return session

    @pytest.mark.asyncio
    async def test_get_valid_id_token_success(self, mock_session: MagicMock) -> None:
        """Successfully gets id_token when session is valid."""
        adapter = TokenBasedAuthAdapter(mock_session)
        token = await adapter.get_valid_id_token()
        assert token == "id_token_value"

    @pytest.mark.asyncio
    async def test_get_valid_id_token_refreshes_expired(self, mock_session: MagicMock) -> None:
        """Triggers refresh when token is expired."""
        mock_session.state.token_expires_at = datetime.now(UTC) - timedelta(hours=1)

        adapter = TokenBasedAuthAdapter(mock_session)
        await adapter.get_valid_id_token()

        mock_session._refresh_tokens_unlocked.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_valid_id_token_no_token_raises(self, mock_session: MagicMock) -> None:
        """Raises AuthenticationError when no id_token available."""
        mock_session._mcc_tokens = None

        adapter = TokenBasedAuthAdapter(mock_session)
        with pytest.raises(AuthenticationError, match="No valid id_token available"):
            await adapter.get_valid_id_token()

    @pytest.mark.asyncio
    async def test_get_valid_id_token_empty_token_raises(self, mock_session: MagicMock) -> None:
        """Raises AuthenticationError when id_token is empty."""
        mock_session._mcc_tokens = TokenResponse(
            access_token="access",
            id_token="",  # Empty token
            refresh_token="refresh",
            token_type="Bearer",
            expires_in=3600,
        )

        adapter = TokenBasedAuthAdapter(mock_session)
        with pytest.raises(AuthenticationError, match="No valid id_token available"):
            await adapter.get_valid_id_token()


class TestUserSessionInitialization:
    """Tests for UserSession initialization."""

    @pytest.fixture
    def mock_settings(self) -> MagicMock:
        """Create mock settings."""
        settings = MagicMock()
        settings.mgmt_url = "https://mcc.example.com"
        settings.keycloak_url = None
        settings.keycloak_realm = None
        settings.mcc_oidc_client_id = None
        settings.ssl_verify = True
        settings.prometheus_url = None
        settings.alertmanager_url = None
        settings.opensearch_url = None
        return settings

    def test_init_no_mgmt_url_raises(self) -> None:
        """Raises ConfigurationError when management cluster URL is not set."""
        settings = MagicMock()
        settings.mgmt_url = None
        settings.keycloak_url = None
        settings.keycloak_realm = None
        settings.mcc_oidc_client_id = None

        with pytest.raises(ConfigurationError, match="Management cluster URL not configured"):
            UserSession(settings)

    def test_init_with_mgmt_url_succeeds(self, mock_settings: MagicMock) -> None:
        """Session initializes with valid management cluster URL."""
        session = UserSession(mock_settings)
        assert session._mgmt_url == "https://mcc.example.com"
        assert session.state.authenticated is False

    def test_init_with_url_override(self, mock_settings: MagicMock) -> None:
        """Session uses explicit URL override over settings."""
        session = UserSession(mock_settings, mgmt_url="https://override.example.com")
        assert session._mgmt_url == "https://override.example.com"

    def test_init_registers_cleanup(self, mock_settings: MagicMock) -> None:
        """Session registers atexit cleanup handler using weakref only.

        Note: We intentionally do NOT add the session to _sessions_to_cleanup
        to avoid creating a strong reference that would prevent garbage collection.
        The atexit handler uses a weakref instead.
        """
        with patch("mosk_mcp.auth.session.atexit.register") as mock_register:
            session = UserSession(mock_settings)
            mock_register.assert_called_once()
            # Verify atexit was registered with a weakref (not a strong reference)
            call_args = mock_register.call_args
            assert call_args is not None
            # The second argument should be a weakref to the session
            from weakref import ref

            assert isinstance(call_args[0][1], ref)
            # Session should NOT be in cleanup set (to prevent memory leak)
            from mosk_mcp.auth.session import _sessions_to_cleanup

            assert session not in _sessions_to_cleanup


class TestUserSessionAuthentication:
    """Tests for UserSession authentication methods."""

    @pytest.fixture
    def authenticated_session(self) -> UserSession:
        """Create an authenticated session."""
        settings = MagicMock()
        settings.mgmt_url = "https://mcc.example.com"
        settings.keycloak_url = None
        settings.keycloak_realm = None
        settings.mcc_oidc_client_id = None
        settings.ssl_verify = True

        session = UserSession(settings)
        session.state.authenticated = True
        session.state.username = "test_user"
        session.state.token_expires_at = datetime.now(UTC) + timedelta(hours=1)
        session._mcc_tokens = TokenResponse(
            access_token="access",
            id_token="id_token",
            refresh_token="refresh",
            token_type="Bearer",
            expires_in=3600,
        )
        session._mcc_endpoints = MCCEndpoints(
            keycloak_url="https://keycloak.example.com",
            k8s_api_url="https://k8s.example.com",
            keycloak_realm="kaas",
            keycloak_client_id="kaas",
        )
        return session

    def test_ensure_authenticated_raises_when_not_authenticated(self) -> None:
        """Raises AuthenticationError when not authenticated."""
        settings = MagicMock()
        settings.mgmt_url = "https://mcc.example.com"
        settings.keycloak_url = None
        settings.keycloak_realm = None
        settings.mcc_oidc_client_id = None

        session = UserSession(settings)
        with pytest.raises(AuthenticationError, match="Session not authenticated"):
            session._ensure_authenticated()

    def test_ensure_authenticated_updates_last_activity(
        self, authenticated_session: UserSession
    ) -> None:
        """_ensure_authenticated updates last_activity timestamp."""
        before = datetime.now(UTC)
        authenticated_session._ensure_authenticated()
        after = datetime.now(UTC)

        assert authenticated_session.state.last_activity is not None
        assert before <= authenticated_session.state.last_activity <= after


class TestUserSessionTempFileCleanup:
    """Tests for temp file cleanup."""

    @pytest.fixture
    def session_with_temp_files(self, tmp_path: Path) -> UserSession:
        """Create session with temp kubeconfig files."""
        settings = MagicMock()
        settings.mgmt_url = "https://mcc.example.com"
        settings.keycloak_url = None
        settings.keycloak_realm = None
        settings.mcc_oidc_client_id = None
        settings.ssl_verify = True

        session = UserSession(settings)

        # Create temp files
        mcc_file = tmp_path / "mcc-kubeconfig.yaml"
        mosk_file = tmp_path / "mosk-kubeconfig.yaml"
        mcc_file.write_text("test")
        mosk_file.write_text("test")

        session._mcc_kubeconfig_path = mcc_file
        session._mosk_kubeconfig_path = mosk_file

        return session

    def test_cleanup_temp_files_removes_files(self, session_with_temp_files: UserSession) -> None:
        """_cleanup_temp_files removes temp kubeconfig files."""
        mcc_path = session_with_temp_files._mcc_kubeconfig_path
        mosk_path = session_with_temp_files._mosk_kubeconfig_path

        assert mcc_path is not None and mcc_path.exists()
        assert mosk_path is not None and mosk_path.exists()

        session_with_temp_files._cleanup_temp_files()

        assert mcc_path is not None and not mcc_path.exists()
        assert mosk_path is not None and not mosk_path.exists()
        assert session_with_temp_files._mcc_kubeconfig_path is None
        assert session_with_temp_files._mosk_kubeconfig_path is None

    def test_cleanup_temp_files_handles_nonexistent_files(self) -> None:
        """_cleanup_temp_files handles files that don't exist (no-op)."""
        settings = MagicMock()
        settings.mgmt_url = "https://mcc.example.com"
        settings.keycloak_url = None
        settings.keycloak_realm = None
        settings.mcc_oidc_client_id = None

        session = UserSession(settings)
        session._mcc_kubeconfig_path = Path("/nonexistent/path.yaml")

        # Should not raise - nonexistent files are handled gracefully
        session._cleanup_temp_files()
        # Path is cleared after cleanup attempt to prevent double cleanup
        assert session._mcc_kubeconfig_path is None

    def test_cleanup_temp_files_handles_none_paths(self) -> None:
        """_cleanup_temp_files handles None paths."""
        settings = MagicMock()
        settings.mgmt_url = "https://mcc.example.com"
        settings.keycloak_url = None
        settings.keycloak_realm = None
        settings.mcc_oidc_client_id = None

        session = UserSession(settings)
        session._mcc_kubeconfig_path = None
        session._mosk_kubeconfig_path = None

        # Should not raise
        session._cleanup_temp_files()


class TestUserSessionLogout:
    """Tests for UserSession logout."""

    @pytest.fixture
    def session_with_resources(self) -> UserSession:
        """Create session with adapters and client."""
        settings = MagicMock()
        settings.mgmt_url = "https://mcc.example.com"
        settings.keycloak_url = None
        settings.keycloak_realm = None
        settings.mcc_oidc_client_id = None
        settings.ssl_verify = True

        session = UserSession(settings)
        session.state.authenticated = True
        session.state.username = "test_user"

        # Mock adapters
        session._mcc_adapter = AsyncMock()
        session._mcc_adapter.disconnect = AsyncMock()
        session._mosk_adapter = AsyncMock()
        session._mosk_adapter.disconnect = AsyncMock()

        # Mock StackLight client
        session._stacklight_client = AsyncMock()
        session._stacklight_client.__aexit__ = AsyncMock()

        # Mock tokens
        session._mcc_tokens = MagicMock()
        session._mosk_tokens = MagicMock()
        session._mcc_managed_tokens = MagicMock()
        session._mosk_managed_tokens = MagicMock()

        return session

    @pytest.mark.asyncio
    async def test_logout_disconnects_adapters(self, session_with_resources: UserSession) -> None:
        """Logout disconnects all adapters."""
        mcc_adapter = session_with_resources._mcc_adapter
        mosk_adapter = session_with_resources._mosk_adapter

        await session_with_resources.logout()

        mcc_adapter.disconnect.assert_called_once()
        mosk_adapter.disconnect.assert_called_once()

    @pytest.mark.asyncio
    async def test_logout_closes_stacklight_client(
        self, session_with_resources: UserSession
    ) -> None:
        """Logout closes StackLight client."""
        client = session_with_resources._stacklight_client

        await session_with_resources.logout()

        client.__aexit__.assert_called_once()

    @pytest.mark.asyncio
    async def test_logout_clears_tokens(self, session_with_resources: UserSession) -> None:
        """Logout clears all tokens."""
        await session_with_resources.logout()

        assert session_with_resources._mcc_tokens is None
        assert session_with_resources._mosk_tokens is None
        assert session_with_resources._mcc_managed_tokens is None
        assert session_with_resources._mosk_managed_tokens is None

    @pytest.mark.asyncio
    async def test_logout_resets_state(self, session_with_resources: UserSession) -> None:
        """Logout resets session state."""
        await session_with_resources.logout()

        assert session_with_resources.state.authenticated is False
        assert session_with_resources.state.username is None

    @pytest.mark.asyncio
    async def test_logout_handles_adapter_disconnect_failure(
        self, session_with_resources: UserSession
    ) -> None:
        """Logout handles adapter disconnect failures gracefully."""
        session_with_resources._mcc_adapter.disconnect.side_effect = Exception("Disconnect failed")

        # Should not raise
        await session_with_resources.logout()

        # Should still clear adapter
        assert session_with_resources._mcc_adapter is None


class TestUserSessionRefreshTokens:
    """Tests for token refresh functionality."""

    @pytest.fixture
    def session_for_refresh(self) -> UserSession:
        """Create session ready for token refresh tests."""
        settings = MagicMock()
        settings.mgmt_url = "https://mcc.example.com"
        settings.keycloak_url = None
        settings.keycloak_realm = None
        settings.mcc_oidc_client_id = None
        settings.ssl_verify = True

        session = UserSession(settings)
        session.state.authenticated = True
        session.state.username = "test_user"
        session.state.token_expires_at = datetime.now(UTC) + timedelta(hours=1)

        return session

    @pytest.mark.asyncio
    async def test_refresh_tokens_not_authenticated(self, session_for_refresh: UserSession) -> None:
        """refresh_tokens returns False when not authenticated."""
        session_for_refresh.state.authenticated = False
        result = await session_for_refresh.refresh_tokens()
        assert result is False

    @pytest.mark.asyncio
    async def test_refresh_tokens_not_expired(self, session_for_refresh: UserSession) -> None:
        """refresh_tokens returns True when token not expired."""
        result = await session_for_refresh.refresh_tokens()
        assert result is True

    @pytest.mark.asyncio
    async def test_refresh_tokens_no_method_available(
        self, session_for_refresh: UserSession
    ) -> None:
        """refresh_tokens raises when no refresh method available."""
        session_for_refresh.state.token_expires_at = datetime.now(UTC) - timedelta(hours=1)
        session_for_refresh._mcc_managed_tokens = None

        with pytest.raises(AuthenticationError, match="no refresh method available"):
            await session_for_refresh.refresh_tokens()


class TestAtexitCleanup:
    """Tests for atexit cleanup handler."""

    def test_cleanup_session_temp_files_with_valid_ref(self, tmp_path: Path) -> None:
        """Cleanup works with valid session reference."""
        import weakref

        settings = MagicMock()
        settings.mgmt_url = "https://mcc.example.com"
        settings.keycloak_url = None
        settings.keycloak_realm = None
        settings.mcc_oidc_client_id = None

        session = UserSession(settings)

        # Create temp file
        temp_file = tmp_path / "kubeconfig.yaml"
        temp_file.write_text("test")
        session._mcc_kubeconfig_path = temp_file

        # Call cleanup with weakref
        ref = weakref.ref(session)
        _cleanup_session_temp_files(ref)

        # File should be deleted
        assert not temp_file.exists()

    def test_cleanup_session_temp_files_with_dead_ref(self) -> None:
        """Cleanup handles dead weakref gracefully."""
        import weakref

        settings = MagicMock()
        settings.mgmt_url = "https://mcc.example.com"
        settings.keycloak_url = None
        settings.keycloak_realm = None
        settings.mcc_oidc_client_id = None

        session = UserSession(settings)
        ref = weakref.ref(session)

        # Delete session to make ref dead
        del session

        # Should not raise
        _cleanup_session_temp_files(ref)


class TestGetStatus:
    """Tests for get_status method."""

    def test_get_status_unauthenticated(self) -> None:
        """get_status returns correct info for unauthenticated session."""
        settings = MagicMock()
        settings.mgmt_url = "https://mcc.example.com"
        settings.keycloak_url = None
        settings.keycloak_realm = None
        settings.mcc_oidc_client_id = None

        session = UserSession(settings)
        status = session.get_status()

        assert status["authenticated"] is False
        assert status["username"] is None
        assert status["token_expired"] is True
        assert status["has_mcc_adapter"] is False
        assert status["has_mosk_adapter"] is False
        assert status["mgmt_url"] == "https://mcc.example.com"

    def test_get_status_authenticated(self) -> None:
        """get_status returns correct info for authenticated session."""
        settings = MagicMock()
        settings.mgmt_url = "https://mcc.example.com"
        settings.keycloak_url = None
        settings.keycloak_realm = None
        settings.mcc_oidc_client_id = None

        session = UserSession(settings)
        session.state.authenticated = True
        session.state.username = "admin"
        session.state.authenticated_at = datetime.now(UTC)
        session.state.token_expires_at = datetime.now(UTC) + timedelta(hours=1)
        session.state.iam_roles = ["admin", "operator"]
        session._mcc_managed_tokens = MagicMock()

        status = session.get_status()

        assert status["authenticated"] is True
        assert status["username"] == "admin"
        assert status["token_expired"] is False
        assert status["token_refresh_available"] is True
        assert status["iam_roles"] == ["admin", "operator"]


class TestContextManager:
    """Tests for async context manager protocol."""

    @pytest.mark.asyncio
    async def test_context_manager_calls_logout(self) -> None:
        """Context manager calls logout on exit."""
        settings = MagicMock()
        settings.mgmt_url = "https://mcc.example.com"
        settings.keycloak_url = None
        settings.keycloak_realm = None
        settings.mcc_oidc_client_id = None

        async with UserSession(settings) as session:
            session.state.authenticated = True
            session.state.username = "test"

        # After context, should be logged out
        assert session.state.authenticated is False
