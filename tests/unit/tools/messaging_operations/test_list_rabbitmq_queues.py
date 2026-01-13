"""Unit tests for list_rabbitmq_queues tool."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mosk_mcp.core.exceptions import ToolExecutionError
from mosk_mcp.tools.messaging_operations.list_rabbitmq_queues import (
    BACKLOG_THRESHOLD,
    STALE_QUEUE_MIN_MESSAGES,
    _generate_recommendations,
    list_rabbitmq_queues,
)
from mosk_mcp.tools.messaging_operations.rabbitmq_client import QueueInfo


class TestGenerateRecommendations:
    """Tests for _generate_recommendations function."""

    def test_no_issues(self) -> None:
        """Test no issues returns normal message."""
        result = _generate_recommendations(
            has_backlog=False,
            has_stale_queues=False,
            stale_queue_count=0,
            total_messages=100,
            top_queues=[],
        )
        assert any("no action required" in r for r in result)

    def test_has_backlog(self) -> None:
        """Test backlog recommendations."""
        result = _generate_recommendations(
            has_backlog=True,
            has_stale_queues=False,
            stale_queue_count=0,
            total_messages=5000,
            top_queues=["queue1", "queue2", "queue3"],
        )
        assert any("Message backlog detected" in r for r in result)
        assert any("Investigate top queues" in r for r in result)

    def test_has_stale_queues(self) -> None:
        """Test stale queue recommendations."""
        result = _generate_recommendations(
            has_backlog=False,
            has_stale_queues=True,
            stale_queue_count=5,
            total_messages=100,
            top_queues=[],
        )
        assert any("stale queue" in r.lower() for r in result)

    def test_high_message_count(self) -> None:
        """Test high message count recommendations."""
        result = _generate_recommendations(
            has_backlog=False,
            has_stale_queues=False,
            stale_queue_count=0,
            total_messages=15000,
            top_queues=[],
        )
        assert any("High total message count" in r for r in result)


class TestListRabbitMQQueues:
    """Tests for list_rabbitmq_queues function."""

    @pytest.fixture
    def mock_kubernetes_adapter(self) -> AsyncMock:
        """Create mock Kubernetes adapter."""
        return AsyncMock()

    @pytest.fixture
    def mock_queue_info(self) -> MagicMock:
        """Create mock queue info."""
        queue = MagicMock(spec=QueueInfo)
        queue.name = "nova-scheduler"
        queue.messages = 50
        queue.messages_ready = 40
        queue.messages_unacked = 10
        queue.consumers = 2
        queue.memory_bytes = 1024
        queue.state = "running"
        return queue

    @pytest.mark.asyncio
    async def test_list_queues_success(
        self, mock_kubernetes_adapter: AsyncMock, mock_queue_info: MagicMock
    ) -> None:
        """Test successful queue listing."""
        mock_client = AsyncMock()
        mock_client.list_vhosts.return_value = ["nova"]
        mock_client.list_queues.return_value = [mock_queue_info]
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None

        with patch(
            "mosk_mcp.tools.messaging_operations.list_rabbitmq_queues.RabbitMQClient",
            return_value=mock_client,
        ):
            result = await list_rabbitmq_queues(mock_kubernetes_adapter)

        assert result.instance == "main"
        assert result.total_queues == 1
        assert result.total_messages == 50
        assert len(result.queues) == 1
        assert result.queues[0].name == "nova-scheduler"

    @pytest.mark.asyncio
    async def test_list_queues_with_vhost_filter(
        self, mock_kubernetes_adapter: AsyncMock, mock_queue_info: MagicMock
    ) -> None:
        """Test queue listing with vhost filter."""
        mock_client = AsyncMock()
        mock_client.list_queues.return_value = [mock_queue_info]
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None

        with patch(
            "mosk_mcp.tools.messaging_operations.list_rabbitmq_queues.RabbitMQClient",
            return_value=mock_client,
        ):
            result = await list_rabbitmq_queues(mock_kubernetes_adapter, vhost="nova")

        # Should not call list_vhosts when vhost is specified
        mock_client.list_vhosts.assert_not_called()
        mock_client.list_queues.assert_called_once_with(vhost="nova")

    @pytest.mark.asyncio
    async def test_list_queues_show_empty(self, mock_kubernetes_adapter: AsyncMock) -> None:
        """Test queue listing with show_empty=True."""
        empty_queue = MagicMock(spec=QueueInfo)
        empty_queue.name = "empty-queue"
        empty_queue.messages = 0
        empty_queue.messages_ready = 0
        empty_queue.messages_unacked = 0
        empty_queue.consumers = 1
        empty_queue.memory_bytes = 0
        empty_queue.state = "running"

        mock_client = AsyncMock()
        mock_client.list_vhosts.return_value = ["nova"]
        mock_client.list_queues.return_value = [empty_queue]
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None

        with patch(
            "mosk_mcp.tools.messaging_operations.list_rabbitmq_queues.RabbitMQClient",
            return_value=mock_client,
        ):
            result = await list_rabbitmq_queues(mock_kubernetes_adapter, show_empty=True)

        assert result.total_queues == 1

    @pytest.mark.asyncio
    async def test_list_queues_hide_empty(self, mock_kubernetes_adapter: AsyncMock) -> None:
        """Test queue listing with show_empty=False (default)."""
        empty_queue = MagicMock(spec=QueueInfo)
        empty_queue.name = "empty-queue"
        empty_queue.messages = 0
        empty_queue.messages_ready = 0
        empty_queue.messages_unacked = 0
        empty_queue.consumers = 1
        empty_queue.memory_bytes = 0
        empty_queue.state = "running"

        mock_client = AsyncMock()
        mock_client.list_vhosts.return_value = ["nova"]
        mock_client.list_queues.return_value = [empty_queue]
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None

        with patch(
            "mosk_mcp.tools.messaging_operations.list_rabbitmq_queues.RabbitMQClient",
            return_value=mock_client,
        ):
            result = await list_rabbitmq_queues(mock_kubernetes_adapter, show_empty=False)

        assert result.total_queues == 0

    @pytest.mark.asyncio
    async def test_list_queues_stale_detection(self, mock_kubernetes_adapter: AsyncMock) -> None:
        """Test stale queue detection."""
        stale_queue = MagicMock(spec=QueueInfo)
        stale_queue.name = "stale-queue"
        stale_queue.messages = STALE_QUEUE_MIN_MESSAGES + 5
        stale_queue.messages_ready = stale_queue.messages
        stale_queue.messages_unacked = 0
        stale_queue.consumers = 0  # No consumers = stale
        stale_queue.memory_bytes = 1024
        stale_queue.state = "running"

        mock_client = AsyncMock()
        mock_client.list_vhosts.return_value = ["nova"]
        mock_client.list_queues.return_value = [stale_queue]
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None

        with patch(
            "mosk_mcp.tools.messaging_operations.list_rabbitmq_queues.RabbitMQClient",
            return_value=mock_client,
        ):
            result = await list_rabbitmq_queues(mock_kubernetes_adapter)

        assert result.has_stale_queues is True
        assert result.stale_queue_count == 1
        assert result.queues[0].is_stale is True

    @pytest.mark.asyncio
    async def test_list_queues_backlog_detection(self, mock_kubernetes_adapter: AsyncMock) -> None:
        """Test backlog detection."""
        backlog_queue = MagicMock(spec=QueueInfo)
        backlog_queue.name = "backlog-queue"
        backlog_queue.messages = BACKLOG_THRESHOLD + 500
        backlog_queue.messages_ready = backlog_queue.messages
        backlog_queue.messages_unacked = 0
        backlog_queue.consumers = 2
        backlog_queue.memory_bytes = 1024
        backlog_queue.state = "running"

        mock_client = AsyncMock()
        mock_client.list_vhosts.return_value = ["nova"]
        mock_client.list_queues.return_value = [backlog_queue]
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None

        with patch(
            "mosk_mcp.tools.messaging_operations.list_rabbitmq_queues.RabbitMQClient",
            return_value=mock_client,
        ):
            result = await list_rabbitmq_queues(mock_kubernetes_adapter)

        assert result.has_backlog is True

    @pytest.mark.asyncio
    async def test_list_queues_limit(self, mock_kubernetes_adapter: AsyncMock) -> None:
        """Test queue limit."""
        queues = []
        for i in range(10):
            q = MagicMock(spec=QueueInfo)
            q.name = f"queue-{i}"
            q.messages = 100 - i
            q.messages_ready = q.messages
            q.messages_unacked = 0
            q.consumers = 1
            q.memory_bytes = 1024
            q.state = "running"
            queues.append(q)

        mock_client = AsyncMock()
        mock_client.list_vhosts.return_value = ["nova"]
        mock_client.list_queues.return_value = queues
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None

        with patch(
            "mosk_mcp.tools.messaging_operations.list_rabbitmq_queues.RabbitMQClient",
            return_value=mock_client,
        ):
            result = await list_rabbitmq_queues(mock_kubernetes_adapter, limit=5)

        assert result.total_queues == 5

    @pytest.mark.asyncio
    async def test_list_queues_vhost_error_handled(
        self, mock_kubernetes_adapter: AsyncMock
    ) -> None:
        """Test vhost query error is handled gracefully."""
        mock_client = AsyncMock()
        mock_client.list_vhosts.return_value = ["nova", "neutron"]
        mock_client.list_queues.side_effect = [
            ToolExecutionError(message="Failed", tool_name="test"),
            [],
        ]
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None

        with patch(
            "mosk_mcp.tools.messaging_operations.list_rabbitmq_queues.RabbitMQClient",
            return_value=mock_client,
        ):
            result = await list_rabbitmq_queues(mock_kubernetes_adapter)

        # Should complete without error
        assert result.total_queues == 0

    @pytest.mark.asyncio
    async def test_list_queues_error(self, mock_kubernetes_adapter: AsyncMock) -> None:
        """Test error handling."""
        mock_client = AsyncMock()
        mock_client.list_vhosts.side_effect = Exception("Connection failed")
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None

        with patch(
            "mosk_mcp.tools.messaging_operations.list_rabbitmq_queues.RabbitMQClient",
            return_value=mock_client,
        ):
            with pytest.raises(ToolExecutionError) as exc_info:
                await list_rabbitmq_queues(mock_kubernetes_adapter)

        assert "Failed to list RabbitMQ queues" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_list_queues_tool_error_passthrough(
        self, mock_kubernetes_adapter: AsyncMock
    ) -> None:
        """Test ToolExecutionError is passed through."""
        mock_client = AsyncMock()
        mock_client.list_vhosts.side_effect = ToolExecutionError(
            message="Pod not found",
            tool_name="list_rabbitmq_queues",
        )
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None

        with patch(
            "mosk_mcp.tools.messaging_operations.list_rabbitmq_queues.RabbitMQClient",
            return_value=mock_client,
        ):
            with pytest.raises(ToolExecutionError) as exc_info:
                await list_rabbitmq_queues(mock_kubernetes_adapter)

        assert "Pod not found" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_list_queues_neutron_instance(
        self, mock_kubernetes_adapter: AsyncMock, mock_queue_info: MagicMock
    ) -> None:
        """Test neutron instance."""
        mock_client = AsyncMock()
        mock_client.list_vhosts.return_value = ["neutron"]
        mock_client.list_queues.return_value = [mock_queue_info]
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None

        with patch(
            "mosk_mcp.tools.messaging_operations.list_rabbitmq_queues.RabbitMQClient",
            return_value=mock_client,
        ):
            result = await list_rabbitmq_queues(
                mock_kubernetes_adapter, rabbitmq_instance="neutron"
            )

        assert result.instance == "neutron"
