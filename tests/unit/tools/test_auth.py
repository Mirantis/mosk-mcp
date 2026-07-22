"""Tests for authentication MCP tools.

Tests cover logout and session_status tools. Device Flow authentication
is tested separately in test_device_flow.py.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from mosk_mcp.auth.session import SessionState, UserSession
from mosk_mcp.core.exceptions import ToolExecutionError
from mosk_mcp.tools.auth.logout import logout
from mosk_mcp.tools.auth.models import (
    LogoutInput,
    SessionStatusInput,
    SessionStatusOutput,
)
from mosk_mcp.tools.auth.session_status import get_session_status


@pytest.fixture
def mock_session() -> MagicMock:
    """Create a mock UserSession."""
    session = MagicMock(spec=UserSession)
    session.state = SessionState(
        authenticated=True,
        authenticated_at=datetime.now(UTC),
        last_activity=datetime.now(UTC),
        token_expires_at=datetime.now(UTC) + timedelta(hours=1),
        username="test@example.com",
        iam_roles=["admin", "operator"],
    )
    session.logout = AsyncMock()
    # Mock _mosk_tokens to indicate MOSK auth status
    # By default, None (not authenticated to MOSK)
    session._mosk_tokens = None
    session.get_status = MagicMock(
        return_value={
            "authenticated": True,
            "username": "test@example.com",
            "authenticated_at": datetime.now(UTC).isoformat(),
            "last_activity": datetime.now(UTC).isoformat(),
            "token_expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
            "token_expired": False,
            "iam_roles": ["admin", "operator"],
            "has_mcc_adapter": True,
            "has_mosk_adapter": True,
            "has_stacklight_client": False,
        }
    )
    return session


@pytest.fixture
def unauthenticated_session() -> MagicMock:
    """Create a mock unauthenticated UserSession."""
    session = MagicMock(spec=UserSession)
    session.state = SessionState(authenticated=False)
    session.logout = AsyncMock()
    # Mock _mosk_tokens to indicate no MOSK auth
    session._mosk_tokens = None
    session.get_status = MagicMock(
        return_value={
            "authenticated": False,
            "username": None,
            "authenticated_at": None,
            "last_activity": None,
            "token_expires_at": None,
            "token_expired": True,
            "iam_roles": [],
            "has_mcc_adapter": False,
            "has_mosk_adapter": False,
            "has_stacklight_client": False,
        }
    )
    return session


class TestLogoutTool:
    """Tests for the logout tool."""

    @pytest.mark.asyncio
    async def test_logout_success(self, mock_session: MagicMock) -> None:
        """Test successful logout."""
        input_data = LogoutInput()

        result = await logout(mock_session, input_data)

        assert result.success is True
        assert "Successfully logged out" in result.message
        mock_session.logout.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_logout_no_active_session(self, unauthenticated_session: MagicMock) -> None:
        """Test logout with no active session."""
        input_data = LogoutInput()

        result = await logout(unauthenticated_session, input_data)

        assert result.success is True
        assert "No active session" in result.message
        unauthenticated_session.logout.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_logout_raises_tool_execution_error(self, mock_session: MagicMock) -> None:
        """Test that unexpected errors raise ToolExecutionError."""
        mock_session.logout = AsyncMock(side_effect=RuntimeError("Cleanup failed"))

        input_data = LogoutInput()

        with pytest.raises(ToolExecutionError) as exc_info:
            await logout(mock_session, input_data)

        assert "Logout failed" in str(exc_info.value)


class TestSessionStatusTool:
    """Tests for the session_status tool."""

    @pytest.mark.asyncio
    async def test_get_session_status_authenticated(self, mock_session: MagicMock) -> None:
        """Test getting session status for authenticated user."""
        input_data = SessionStatusInput()

        result = await get_session_status(mock_session, input_data)

        assert result.authenticated is True
        assert result.username == "test@example.com"
        assert result.iam_roles == ["admin", "operator"]
        assert result.token_expired is False
        assert result.has_mcc_adapter is True
        assert result.has_mosk_adapter is True

    @pytest.mark.asyncio
    async def test_get_session_status_unauthenticated(
        self, unauthenticated_session: MagicMock
    ) -> None:
        """Test getting session status for unauthenticated user."""
        input_data = SessionStatusInput()

        result = await get_session_status(unauthenticated_session, input_data)

        assert result.authenticated is False
        assert result.username is None
        assert result.iam_roles == []
        assert result.token_expired is True
        assert result.has_mcc_adapter is False

    @pytest.mark.asyncio
    async def test_get_session_status_raises_tool_execution_error(
        self, mock_session: MagicMock
    ) -> None:
        """Test that unexpected errors raise ToolExecutionError."""
        mock_session.get_status = MagicMock(side_effect=RuntimeError("Status error"))

        input_data = SessionStatusInput()

        with pytest.raises(ToolExecutionError) as exc_info:
            await get_session_status(mock_session, input_data)

        assert "Session status check failed" in str(exc_info.value)


class TestModels:
    """Tests for authentication models."""

    def test_session_status_output_model(self) -> None:
        """Test SessionStatusOutput model."""
        output = SessionStatusOutput(
            authenticated=True,
            username="user@example.com",
            authenticated_at="2024-01-01T00:00:00Z",
            token_expires_at="2024-01-01T01:00:00Z",
            token_expired=False,
            iam_roles=["admin", "operator"],
            has_mcc_adapter=True,
            has_mosk_adapter=True,
            has_stacklight_client=True,
        )
        assert output.authenticated is True
        assert len(output.iam_roles) == 2


# ==========================
# Additional Auth Model Tests
# ==========================
class TestAuthModels:
    """Extended tests for authentication models."""

    def test_auth_method_enum(self) -> None:
        """Test AuthMethod enum values."""
        from mosk_mcp.tools.auth.models import AuthMethod

        assert AuthMethod.DEVICE_FLOW.value == "device_flow"
        assert isinstance(AuthMethod.DEVICE_FLOW, str)

    def test_device_flow_login_input_defaults(self) -> None:
        """Test DeviceFlowLoginInput default values."""
        from mosk_mcp.tools.auth.models import DeviceFlowLoginInput

        input_model = DeviceFlowLoginInput()

        assert input_model.mosk_cluster_name is None
        assert input_model.mosk_namespace == "default"
        assert input_model.auto_discover_mosk is True

    def test_device_flow_login_input_custom(self) -> None:
        """Test DeviceFlowLoginInput with custom values."""
        from mosk_mcp.tools.auth.models import DeviceFlowLoginInput

        input_model = DeviceFlowLoginInput(
            mosk_cluster_name="mos",
            mosk_namespace="lab",
            auto_discover_mosk=False,
        )

        assert input_model.mosk_cluster_name == "mos"
        assert input_model.mosk_namespace == "lab"
        assert input_model.auto_discover_mosk is False

    def test_device_flow_init_output(self) -> None:
        """Test DeviceFlowInitOutput model."""
        from mosk_mcp.tools.auth.models import DeviceFlowInitOutput
        from mosk_mcp.tools.common.enums import DeviceFlowStatus

        output = DeviceFlowInitOutput(
            user_code="ABCD-EFGH",
            verification_uri="https://example.com/device",
            verification_uri_complete="https://example.com/device?code=ABCD-EFGH",
            expires_in=600,
            message="Please authenticate",
        )

        assert output.status == DeviceFlowStatus.AWAITING_USER
        assert output.user_code == "ABCD-EFGH"
        assert output.expires_in == 600
        assert output.poll_interval == 5  # Default

    def test_device_flow_complete_input_defaults(self) -> None:
        """Test DeviceFlowCompleteInput default values."""
        from mosk_mcp.tools.auth.models import DeviceFlowCompleteInput

        input_model = DeviceFlowCompleteInput()

        assert input_model.wait_for_completion is True
        assert input_model.timeout is None

    def test_device_flow_complete_output_success(self) -> None:
        """Test DeviceFlowCompleteOutput for successful auth."""
        from mosk_mcp.tools.auth.models import DeviceFlowCompleteOutput
        from mosk_mcp.tools.common.enums import DeviceFlowStatus

        output = DeviceFlowCompleteOutput(
            status=DeviceFlowStatus.COMPLETED,
            success=True,
            username="admin@example.com",
            message="Authentication successful",
            iam_roles=["admin"],
            token_expires_in=1800,
            mcc_authenticated=True,
            mosk_authenticated=True,
        )

        assert output.success is True
        assert output.username == "admin@example.com"
        assert output.mcc_authenticated is True
        assert output.mosk_authenticated is True

    def test_device_flow_complete_output_failure(self) -> None:
        """Test DeviceFlowCompleteOutput for failed auth."""
        from mosk_mcp.tools.auth.models import DeviceFlowCompleteOutput
        from mosk_mcp.tools.common.enums import DeviceFlowStatus

        output = DeviceFlowCompleteOutput(
            status=DeviceFlowStatus.ERROR,
            success=False,
            message="Authentication failed",
        )

        assert output.success is False
        assert output.status == DeviceFlowStatus.ERROR
        assert output.mcc_authenticated is False

    def test_logout_input_empty(self) -> None:
        """Test LogoutInput model (no fields)."""
        input_model = LogoutInput()
        assert input_model is not None

    def test_session_status_input_empty(self) -> None:
        """Test SessionStatusInput model (no fields)."""
        input_model = SessionStatusInput()
        assert input_model is not None

    def test_session_status_output_unauthenticated(self) -> None:
        """Test SessionStatusOutput for unauthenticated state."""
        output = SessionStatusOutput(
            authenticated=False,
            token_expired=True,
        )

        assert output.authenticated is False
        assert output.username is None
        assert output.token_expired is True
        assert output.device_flow_polling is False


# ==========================
# Device Flow Login Manager Tests
# ==========================
class TestManagedTokens:
    """Tests for ManagedTokens class."""

    def test_managed_tokens_creation(self) -> None:
        """Test creating ManagedTokens."""
        from mosk_mcp.tools.auth.device_flow_login import ManagedTokens

        mock_tokens = MagicMock()
        mock_tokens.is_expired_with_buffer = MagicMock(return_value=False)

        managed = ManagedTokens(
            tokens=mock_tokens,
            client_id="kaas",
            issuer_url="https://keycloak.example.com/auth/realms/mcc",
        )

        assert managed.client_id == "kaas"
        assert managed.issuer_url == "https://keycloak.example.com/auth/realms/mcc"

    def test_is_expired_not_expired(self) -> None:
        """Test is_expired when token is not expired."""
        from mosk_mcp.tools.auth.device_flow_login import ManagedTokens

        mock_tokens = MagicMock()
        mock_tokens.is_expired_with_buffer = MagicMock(return_value=False)

        managed = ManagedTokens(
            tokens=mock_tokens,
            client_id="kaas",
            issuer_url="https://example.com",
        )

        assert managed.is_expired() is False
        mock_tokens.is_expired_with_buffer.assert_called_once_with(60)

    def test_is_expired_custom_buffer(self) -> None:
        """Test is_expired with custom buffer."""
        from mosk_mcp.tools.auth.device_flow_login import ManagedTokens

        mock_tokens = MagicMock()
        mock_tokens.is_expired_with_buffer = MagicMock(return_value=True)

        managed = ManagedTokens(
            tokens=mock_tokens,
            client_id="k8s",
            issuer_url="https://example.com",
        )

        assert managed.is_expired(buffer_seconds=120) is True
        mock_tokens.is_expired_with_buffer.assert_called_once_with(120)


class TestDeviceFlowLoginManager:
    """Tests for DeviceFlowLoginManager."""

    @pytest.fixture
    def mock_settings(self) -> MagicMock:
        """Create mock settings."""
        settings = MagicMock()
        settings.mgmt_url = "https://mcc.example.com"
        settings.ssl_verify = True
        settings.device_flow_scope = "openid"
        settings.device_flow_max_poll_attempts = 60
        return settings

    @pytest.fixture
    def mock_manager_session(self) -> MagicMock:
        """Create mock session for manager."""
        session = MagicMock()
        session.state = MagicMock()
        session.state.authenticated = False
        session._lock = MagicMock()
        return session

    def test_manager_initialization(
        self, mock_settings: MagicMock, mock_manager_session: MagicMock
    ) -> None:
        """Test DeviceFlowLoginManager initialization."""
        from mosk_mcp.tools.auth.device_flow_login import DeviceFlowLoginManager

        manager = DeviceFlowLoginManager(mock_settings, mock_manager_session)

        assert manager.settings == mock_settings
        assert manager.session == mock_manager_session
        assert manager.mgmt_url == "https://mcc.example.com"
        assert manager.ssl_verify is True

    def test_manager_url_override(
        self, mock_settings: MagicMock, mock_manager_session: MagicMock
    ) -> None:
        """Test manager with URL override."""
        from mosk_mcp.tools.auth.device_flow_login import DeviceFlowLoginManager

        manager = DeviceFlowLoginManager(
            mock_settings,
            mock_manager_session,
            mgmt_url_override="https://override.example.com",
            ssl_verify_override=False,
        )

        assert manager.mgmt_url == "https://override.example.com"
        assert manager.ssl_verify is False

    def test_is_flow_active_no_auth(
        self, mock_settings: MagicMock, mock_manager_session: MagicMock
    ) -> None:
        """Test is_flow_active when no flow initiated."""
        from mosk_mcp.tools.auth.device_flow_login import DeviceFlowLoginManager

        manager = DeviceFlowLoginManager(mock_settings, mock_manager_session)

        assert manager.is_flow_active is False

    def test_time_remaining_no_flow(
        self, mock_settings: MagicMock, mock_manager_session: MagicMock
    ) -> None:
        """Test time_remaining when no flow initiated."""
        from mosk_mcp.tools.auth.device_flow_login import DeviceFlowLoginManager

        manager = DeviceFlowLoginManager(mock_settings, mock_manager_session)

        assert manager.time_remaining == 0

    def test_build_auth_message_not_initialized(
        self, mock_settings: MagicMock, mock_manager_session: MagicMock
    ) -> None:
        """Test build_auth_message when flow not initialized."""
        from mosk_mcp.tools.auth.device_flow_login import DeviceFlowLoginManager

        manager = DeviceFlowLoginManager(mock_settings, mock_manager_session)

        assert manager.build_auth_message() == "Device flow not initialized."


class TestDeviceFlowModuleFunctions:
    """Tests for module-level device flow functions."""

    @pytest.fixture
    def authenticated_flow_session(self) -> MagicMock:
        """Create mock authenticated session."""
        session = MagicMock()
        session.state = MagicMock()
        session.state.authenticated = True
        session.state.username = "test@example.com"
        session.state.iam_roles = ["admin"]
        session._mosk_cluster_name = "mos"
        return session

    @pytest.fixture
    def flow_settings(self) -> MagicMock:
        """Create mock settings."""
        settings = MagicMock()
        settings.mgmt_url = "https://mcc.example.com"
        settings.ssl_verify = True
        return settings

    @pytest.mark.asyncio
    async def test_device_flow_login_already_authenticated(
        self, authenticated_flow_session: MagicMock, flow_settings: MagicMock
    ) -> None:
        """Test login when already authenticated returns status."""
        from mosk_mcp.tools.auth.device_flow_login import device_flow_login
        from mosk_mcp.tools.auth.models import DeviceFlowLoginInput
        from mosk_mcp.tools.common.enums import DeviceFlowStatus

        result = await device_flow_login(
            authenticated_flow_session,
            flow_settings,
            DeviceFlowLoginInput(),
        )

        assert result.success is True
        assert result.status == DeviceFlowStatus.COMPLETED
        assert "Already authenticated" in result.message

    @pytest.mark.asyncio
    async def test_device_flow_login_complete_no_active_flow(
        self, flow_settings: MagicMock
    ) -> None:
        """Test login_complete when no flow is active."""
        from mosk_mcp.tools.auth.device_flow_login import (
            _clear_manager,
            device_flow_login_complete,
        )
        from mosk_mcp.tools.common.enums import DeviceFlowStatus

        mock_session = MagicMock()
        mock_session.state = MagicMock()
        mock_session.state.authenticated = False

        # Clear any existing manager
        _clear_manager(mock_session)

        result = await device_flow_login_complete(mock_session, flow_settings)

        assert result.success is False
        assert result.status == DeviceFlowStatus.ERROR
        assert "No active device flow" in result.message


class TestDeviceFlowStatus:
    """Tests for DeviceFlowStatus enum."""

    def test_status_values(self) -> None:
        """Test all status values exist."""
        from mosk_mcp.tools.common.enums import DeviceFlowStatus

        assert DeviceFlowStatus.AWAITING_USER.value == "awaiting_user"
        assert DeviceFlowStatus.POLLING.value == "polling"
        assert DeviceFlowStatus.COMPLETED.value == "completed"
        assert DeviceFlowStatus.EXPIRED.value == "expired"
        assert DeviceFlowStatus.ERROR.value == "error"

    def test_status_is_string_enum(self) -> None:
        """Test DeviceFlowStatus is a string enum."""
        from mosk_mcp.tools.common.enums import DeviceFlowStatus

        assert isinstance(DeviceFlowStatus.COMPLETED, str)
        assert DeviceFlowStatus.COMPLETED == "completed"
