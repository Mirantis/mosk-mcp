"""Unit tests for get_rabbitmq_connections tool."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mosk_mcp.core.exceptions import ToolExecutionError
from mosk_mcp.tools.messaging_operations.get_rabbitmq_connections import (
    CONNECTION_UTILIZATION_CRITICAL,
    CONNECTION_UTILIZATION_WARNING,
    _generate_recommendations,
    _map_connection_state,
    get_rabbitmq_connections,
)
from mosk_mcp.tools.messaging_operations.models import ConnectionState
from mosk_mcp.tools.messaging_operations.rabbitmq_client import ConnectionInfo


class TestMapConnectionState:
    """Tests for _map_connection_state function."""

    def test_running_state(self) -> None:
        """Test running state mapping."""
        assert _map_connection_state("running") == ConnectionState.RUNNING
        assert _map_connection_state("RUNNING") == ConnectionState.RUNNING

    def test_blocked_state(self) -> None:
        """Test blocked state mapping."""
        assert _map_connection_state("blocked") == ConnectionState.BLOCKED

    def test_blocking_state(self) -> None:
        """Test blocking state mapping."""
        assert _map_connection_state("blocking") == ConnectionState.BLOCKING

    def test_closed_state(self) -> None:
        """Test closed state mapping."""
        assert _map_connection_state("closed") == ConnectionState.CLOSED

    def test_unknown_state(self) -> None:
        """Test unknown state mapping."""
        assert _map_connection_state("unknown") == ConnectionState.UNKNOWN
        assert _map_connection_state("other") == ConnectionState.UNKNOWN


class TestGenerateRecommendations:
    """Tests for _generate_recommendations function."""

    def test_healthy_pool(self) -> None:
        """Test healthy pool recommendations."""
        result = _generate_recommendations(
            total_connections=100,
            blocked_connections=0,
            utilization_percent=10.0,
            top_users=["nova", "neutron"],
        )
        assert any("healthy" in r.lower() for r in result)

    def test_blocked_connections(self) -> None:
        """Test blocked connections recommendations."""
        result = _generate_recommendations(
            total_connections=100,
            blocked_connections=5,
            utilization_percent=10.0,
            top_users=[],
        )
        assert any("blocked" in r.lower() for r in result)

    def test_critical_utilization(self) -> None:
        """Test critical utilization recommendations."""
        result = _generate_recommendations(
            total_connections=1000,
            blocked_connections=0,
            utilization_percent=CONNECTION_UTILIZATION_CRITICAL + 5,
            top_users=[],
        )
        assert any("CRITICAL" in r for r in result)

    def test_warning_utilization(self) -> None:
        """Test warning utilization recommendations."""
        result = _generate_recommendations(
            total_connections=1000,
            blocked_connections=0,
            utilization_percent=CONNECTION_UTILIZATION_WARNING + 5,
            top_users=[],
        )
        assert any("WARNING" in r for r in result)

    def test_high_connection_count(self) -> None:
        """Test high connection count recommendations."""
        result = _generate_recommendations(
            total_connections=1500,
            blocked_connections=0,
            utilization_percent=10.0,
            top_users=[],
        )
        assert any("High connection count" in r for r in result)

    def test_top_users_shown(self) -> None:
        """Test top users are shown."""
        result = _generate_recommendations(
            total_connections=100,
            blocked_connections=0,
            utilization_percent=10.0,
            top_users=["nova", "neutron", "cinder"],
        )
        assert any("Top connection consumers" in r for r in result)

    def test_no_utilization_data(self) -> None:
        """Test no utilization data."""
        result = _generate_recommendations(
            total_connections=100,
            blocked_connections=0,
            utilization_percent=None,
            top_users=[],
        )
        assert any("healthy" in r.lower() for r in result)


class TestGetRabbitMQConnections:
    """Tests for get_rabbitmq_connections function."""

    @pytest.fixture
    def mock_kubernetes_adapter(self) -> AsyncMock:
        """Create mock Kubernetes adapter."""
        return AsyncMock()

    @pytest.fixture
    def mock_connection_info(self) -> MagicMock:
        """Create mock connection info."""
        conn = MagicMock(spec=ConnectionInfo)
        conn.name = "192.168.1.10:5672 -> 10.0.0.1:35123"
        conn.user = "nova"
        conn.state = "running"
        conn.ssl = False
        conn.protocol = "AMQP 0-9-1"
        conn.channels = 5
        conn.client_host = "192.168.1.10"
        return conn

    @pytest.mark.asyncio
    async def test_get_connections_success(
        self, mock_kubernetes_adapter: AsyncMock, mock_connection_info: MagicMock
    ) -> None:
        """Test successful connection listing."""
        mock_client = AsyncMock()
        mock_client.list_connections.return_value = [mock_connection_info]
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None

        with (
            patch(
                "mosk_mcp.tools.messaging_operations.get_rabbitmq_connections.RabbitMQClient",
                return_value=mock_client,
            ),
            patch(
                "mosk_mcp.tools.messaging_operations.get_rabbitmq_connections.RabbitMQClient.infer_service_from_user",
                return_value="nova",
            ),
        ):
            result = await get_rabbitmq_connections(mock_kubernetes_adapter)

        assert result.instance == "main"
        assert result.total_connections == 1
        assert result.running_connections == 1
        assert result.blocked_connections == 0
        assert len(result.connections) == 1

    @pytest.mark.asyncio
    async def test_get_connections_with_channels(
        self, mock_kubernetes_adapter: AsyncMock, mock_connection_info: MagicMock
    ) -> None:
        """Test connection listing with channels."""
        mock_client = AsyncMock()
        mock_client.list_connections.return_value = [mock_connection_info]
        mock_client.list_channels.return_value = [
            {"connection": mock_connection_info.name, "name": "chan1"},
            {"connection": mock_connection_info.name, "name": "chan2"},
        ]
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None

        with (
            patch(
                "mosk_mcp.tools.messaging_operations.get_rabbitmq_connections.RabbitMQClient",
                return_value=mock_client,
            ),
            patch(
                "mosk_mcp.tools.messaging_operations.get_rabbitmq_connections.RabbitMQClient.infer_service_from_user",
                return_value="nova",
            ),
        ):
            result = await get_rabbitmq_connections(mock_kubernetes_adapter, include_channels=True)

        mock_client.list_channels.assert_called_once()
        # Channel count from list_channels (2) should override connection.channels
        assert result.connections[0].channels == 2

    @pytest.mark.asyncio
    async def test_get_connections_group_by_user(self, mock_kubernetes_adapter: AsyncMock) -> None:
        """Test connection grouping by user."""
        conn1 = MagicMock(spec=ConnectionInfo)
        conn1.name = "conn1"
        conn1.user = "nova"
        conn1.state = "running"
        conn1.ssl = False
        conn1.protocol = "AMQP 0-9-1"
        conn1.channels = 3
        conn1.client_host = "10.0.0.1"

        conn2 = MagicMock(spec=ConnectionInfo)
        conn2.name = "conn2"
        conn2.user = "nova"
        conn2.state = "running"
        conn2.ssl = False
        conn2.protocol = "AMQP 0-9-1"
        conn2.channels = 2
        conn2.client_host = "10.0.0.2"

        conn3 = MagicMock(spec=ConnectionInfo)
        conn3.name = "conn3"
        conn3.user = "neutron"
        conn3.state = "running"
        conn3.ssl = False
        conn3.protocol = "AMQP 0-9-1"
        conn3.channels = 5
        conn3.client_host = "10.0.0.3"

        mock_client = AsyncMock()
        mock_client.list_connections.return_value = [conn1, conn2, conn3]
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None

        def infer_service(user: str) -> str:
            return user  # Return user as service name

        with (
            patch(
                "mosk_mcp.tools.messaging_operations.get_rabbitmq_connections.RabbitMQClient",
                return_value=mock_client,
            ),
            patch(
                "mosk_mcp.tools.messaging_operations.get_rabbitmq_connections.RabbitMQClient.infer_service_from_user",
                side_effect=infer_service,
            ),
        ):
            result = await get_rabbitmq_connections(mock_kubernetes_adapter, group_by_user=True)

        assert len(result.by_user) == 2
        # Nova has more connections so should be first
        assert result.by_user[0].user == "nova"
        assert result.by_user[0].connection_count == 2

    @pytest.mark.asyncio
    async def test_get_connections_no_grouping(
        self, mock_kubernetes_adapter: AsyncMock, mock_connection_info: MagicMock
    ) -> None:
        """Test connection listing without grouping."""
        mock_client = AsyncMock()
        mock_client.list_connections.return_value = [mock_connection_info]
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None

        with patch(
            "mosk_mcp.tools.messaging_operations.get_rabbitmq_connections.RabbitMQClient",
            return_value=mock_client,
        ):
            result = await get_rabbitmq_connections(mock_kubernetes_adapter, group_by_user=False)

        assert result.by_user == []

    @pytest.mark.asyncio
    async def test_get_connections_blocked(self, mock_kubernetes_adapter: AsyncMock) -> None:
        """Test blocked connections are detected."""
        blocked_conn = MagicMock(spec=ConnectionInfo)
        blocked_conn.name = "conn1"
        blocked_conn.user = "nova"
        blocked_conn.state = "blocked"
        blocked_conn.ssl = False
        blocked_conn.protocol = "AMQP 0-9-1"
        blocked_conn.channels = 2
        blocked_conn.client_host = "10.0.0.1"

        mock_client = AsyncMock()
        mock_client.list_connections.return_value = [blocked_conn]
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None

        with (
            patch(
                "mosk_mcp.tools.messaging_operations.get_rabbitmq_connections.RabbitMQClient",
                return_value=mock_client,
            ),
            patch(
                "mosk_mcp.tools.messaging_operations.get_rabbitmq_connections.RabbitMQClient.infer_service_from_user",
                return_value="nova",
            ),
        ):
            result = await get_rabbitmq_connections(mock_kubernetes_adapter)

        assert result.blocked_connections == 1
        assert result.has_blocked_connections is True
        assert result.is_connection_pool_healthy is False

    @pytest.mark.asyncio
    async def test_get_connections_limit(self, mock_kubernetes_adapter: AsyncMock) -> None:
        """Test connection limit."""
        connections = []
        for i in range(10):
            conn = MagicMock(spec=ConnectionInfo)
            conn.name = f"conn{i}"
            conn.user = "nova"
            conn.state = "running"
            conn.ssl = False
            conn.protocol = "AMQP 0-9-1"
            conn.channels = 1
            conn.client_host = f"10.0.0.{i}"
            connections.append(conn)

        mock_client = AsyncMock()
        mock_client.list_connections.return_value = connections
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None

        with (
            patch(
                "mosk_mcp.tools.messaging_operations.get_rabbitmq_connections.RabbitMQClient",
                return_value=mock_client,
            ),
            patch(
                "mosk_mcp.tools.messaging_operations.get_rabbitmq_connections.RabbitMQClient.infer_service_from_user",
                return_value="nova",
            ),
        ):
            result = await get_rabbitmq_connections(mock_kubernetes_adapter, limit=5)

        assert len(result.connections) == 5
        assert result.total_connections == 10

    @pytest.mark.asyncio
    async def test_get_connections_error(self, mock_kubernetes_adapter: AsyncMock) -> None:
        """Test error handling."""
        mock_client = AsyncMock()
        mock_client.list_connections.side_effect = Exception("Connection failed")
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None

        with patch(
            "mosk_mcp.tools.messaging_operations.get_rabbitmq_connections.RabbitMQClient",
            return_value=mock_client,
        ):
            with pytest.raises(ToolExecutionError) as exc_info:
                await get_rabbitmq_connections(mock_kubernetes_adapter)

        assert "Failed to get RabbitMQ connections" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_get_connections_tool_error_passthrough(
        self, mock_kubernetes_adapter: AsyncMock
    ) -> None:
        """Test ToolExecutionError is passed through."""
        mock_client = AsyncMock()
        mock_client.list_connections.side_effect = ToolExecutionError(
            message="Pod not found",
            tool_name="get_rabbitmq_connections",
        )
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None

        with patch(
            "mosk_mcp.tools.messaging_operations.get_rabbitmq_connections.RabbitMQClient",
            return_value=mock_client,
        ):
            with pytest.raises(ToolExecutionError) as exc_info:
                await get_rabbitmq_connections(mock_kubernetes_adapter)

        assert "Pod not found" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_get_connections_neutron_instance(
        self, mock_kubernetes_adapter: AsyncMock, mock_connection_info: MagicMock
    ) -> None:
        """Test neutron instance."""
        mock_client = AsyncMock()
        mock_client.list_connections.return_value = [mock_connection_info]
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None

        with (
            patch(
                "mosk_mcp.tools.messaging_operations.get_rabbitmq_connections.RabbitMQClient",
                return_value=mock_client,
            ),
            patch(
                "mosk_mcp.tools.messaging_operations.get_rabbitmq_connections.RabbitMQClient.infer_service_from_user",
                return_value="nova",
            ),
        ):
            result = await get_rabbitmq_connections(
                mock_kubernetes_adapter, rabbitmq_instance="neutron"
            )

        assert result.instance == "neutron"
