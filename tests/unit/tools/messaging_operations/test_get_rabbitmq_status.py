"""Unit tests for get_rabbitmq_status tool."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mosk_mcp.core.exceptions import ToolExecutionError
from mosk_mcp.tools.messaging_operations.get_rabbitmq_status import (
    _determine_health_level,
    _generate_health_summary,
    _generate_issues,
    _generate_recommendations,
    _generate_warnings,
    _is_safe_for_operations,
    get_rabbitmq_status,
)
from mosk_mcp.tools.messaging_operations.models import RabbitMQHealthLevel


class TestDetermineHealthLevel:
    """Tests for _determine_health_level function."""

    def test_healthy_cluster(self) -> None:
        """Test healthy cluster returns HEALTHY."""
        result = _determine_health_level(
            running_nodes=3,
            total_nodes=3,
            has_alarms=False,
            has_partitions=False,
            memory_percent=50.0,
        )
        assert result == RabbitMQHealthLevel.HEALTHY

    def test_no_running_nodes(self) -> None:
        """Test no running nodes returns CRITICAL."""
        result = _determine_health_level(
            running_nodes=0,
            total_nodes=3,
            has_alarms=False,
            has_partitions=False,
            memory_percent=50.0,
        )
        assert result == RabbitMQHealthLevel.CRITICAL

    def test_has_partitions(self) -> None:
        """Test network partitions returns CRITICAL."""
        result = _determine_health_level(
            running_nodes=3,
            total_nodes=3,
            has_alarms=False,
            has_partitions=True,
            memory_percent=50.0,
        )
        assert result == RabbitMQHealthLevel.CRITICAL

    def test_nodes_not_all_running(self) -> None:
        """Test some nodes down returns CRITICAL."""
        result = _determine_health_level(
            running_nodes=2,
            total_nodes=3,
            has_alarms=False,
            has_partitions=False,
            memory_percent=50.0,
        )
        assert result == RabbitMQHealthLevel.CRITICAL

    def test_has_alarms(self) -> None:
        """Test active alarms returns WARNING."""
        result = _determine_health_level(
            running_nodes=3,
            total_nodes=3,
            has_alarms=True,
            has_partitions=False,
            memory_percent=50.0,
        )
        assert result == RabbitMQHealthLevel.WARNING

    def test_high_memory(self) -> None:
        """Test high memory usage returns WARNING."""
        result = _determine_health_level(
            running_nodes=3,
            total_nodes=3,
            has_alarms=False,
            has_partitions=False,
            memory_percent=85.0,
        )
        assert result == RabbitMQHealthLevel.WARNING


class TestGenerateHealthSummary:
    """Tests for _generate_health_summary function."""

    def test_healthy_summary(self) -> None:
        """Test healthy cluster summary."""
        result = _generate_health_summary(
            health=RabbitMQHealthLevel.HEALTHY,
            running_nodes=3,
            total_nodes=3,
            vhost_count=5,
            has_alarms=False,
            has_partitions=False,
            memory_percent=50.0,
        )
        assert "Cluster is healthy" in result
        assert "all 3 node(s) running" in result
        assert "5 vhost(s)" in result
        assert "50.0% memory used" in result

    def test_warning_summary(self) -> None:
        """Test warning cluster summary."""
        result = _generate_health_summary(
            health=RabbitMQHealthLevel.WARNING,
            running_nodes=3,
            total_nodes=3,
            vhost_count=5,
            has_alarms=True,
            has_partitions=False,
            memory_percent=50.0,
        )
        assert "Cluster has warnings" in result
        assert "ALARMS ACTIVE" in result

    def test_critical_summary(self) -> None:
        """Test critical cluster summary."""
        result = _generate_health_summary(
            health=RabbitMQHealthLevel.CRITICAL,
            running_nodes=2,
            total_nodes=3,
            vhost_count=5,
            has_alarms=False,
            has_partitions=True,
            memory_percent=50.0,
        )
        assert "Cluster has critical issues" in result
        assert "2/3 nodes running" in result
        assert "NETWORK PARTITIONS DETECTED" in result

    def test_unknown_health_summary(self) -> None:
        """Test unknown health summary."""
        result = _generate_health_summary(
            health=RabbitMQHealthLevel.UNKNOWN,
            running_nodes=0,
            total_nodes=0,
            vhost_count=0,
            has_alarms=False,
            has_partitions=False,
            memory_percent=0.0,
        )
        assert "Cluster health unknown" in result


class TestGenerateIssues:
    """Tests for _generate_issues function."""

    def test_no_issues(self) -> None:
        """Test no issues."""
        result = _generate_issues(
            has_alarms=False,
            alarms=[],
            has_partitions=False,
            partitions=[],
            running_nodes=3,
            total_nodes=3,
            memory_percent=50.0,
        )
        assert result == []

    def test_nodes_down(self) -> None:
        """Test nodes down issue."""
        result = _generate_issues(
            has_alarms=False,
            alarms=[],
            has_partitions=False,
            partitions=[],
            running_nodes=2,
            total_nodes=3,
            memory_percent=50.0,
        )
        assert "1 node(s) not running" in result

    def test_partitions(self) -> None:
        """Test partitions issue."""
        result = _generate_issues(
            has_alarms=False,
            alarms=[],
            has_partitions=True,
            partitions=["node1", "node2"],
            running_nodes=3,
            total_nodes=3,
            memory_percent=50.0,
        )
        assert any("Network partitions detected" in issue for issue in result)

    def test_alarms(self) -> None:
        """Test alarms issue."""
        result = _generate_issues(
            has_alarms=True,
            alarms=["memory", "disk"],
            has_partitions=False,
            partitions=[],
            running_nodes=3,
            total_nodes=3,
            memory_percent=50.0,
        )
        assert any("Alarm active: memory" in issue for issue in result)
        assert any("Alarm active: disk" in issue for issue in result)

    def test_critical_memory(self) -> None:
        """Test critical memory issue."""
        result = _generate_issues(
            has_alarms=False,
            alarms=[],
            has_partitions=False,
            partitions=[],
            running_nodes=3,
            total_nodes=3,
            memory_percent=95.0,
        )
        assert any("Critical memory usage" in issue for issue in result)

    def test_high_memory(self) -> None:
        """Test high memory issue."""
        result = _generate_issues(
            has_alarms=False,
            alarms=[],
            has_partitions=False,
            partitions=[],
            running_nodes=3,
            total_nodes=3,
            memory_percent=85.0,
        )
        assert any("High memory usage" in issue for issue in result)


class TestGenerateWarnings:
    """Tests for _generate_warnings function."""

    def test_no_warnings(self) -> None:
        """Test no warnings."""
        result = _generate_warnings(
            memory_percent=50.0,
            maintenance_status="normal",
        )
        assert result == []

    def test_elevated_memory(self) -> None:
        """Test elevated memory warning."""
        result = _generate_warnings(
            memory_percent=75.0,
            maintenance_status="not under maintenance",
        )
        assert any("Memory usage is elevated" in w for w in result)

    def test_under_maintenance(self) -> None:
        """Test maintenance warning."""
        result = _generate_warnings(
            memory_percent=50.0,
            maintenance_status="under maintenance",
        )
        assert any("under maintenance" in w for w in result)


class TestGenerateRecommendations:
    """Tests for _generate_recommendations function."""

    def test_healthy_no_issues(self) -> None:
        """Test healthy cluster with no issues."""
        result = _generate_recommendations(
            health=RabbitMQHealthLevel.HEALTHY,
            issues=[],
            memory_percent=50.0,
            has_alarms=False,
        )
        assert any("No action required" in r for r in result)

    def test_critical_health(self) -> None:
        """Test critical health recommendation."""
        result = _generate_recommendations(
            health=RabbitMQHealthLevel.CRITICAL,
            issues=["Some issue"],
            memory_percent=50.0,
            has_alarms=False,
        )
        assert any("IMMEDIATE ACTION REQUIRED" in r for r in result)

    def test_has_alarms(self) -> None:
        """Test alarms recommendation."""
        result = _generate_recommendations(
            health=RabbitMQHealthLevel.WARNING,
            issues=["Alarm active"],
            memory_percent=50.0,
            has_alarms=True,
        )
        assert any("Investigate active alarms" in r for r in result)

    def test_high_memory(self) -> None:
        """Test high memory recommendations."""
        result = _generate_recommendations(
            health=RabbitMQHealthLevel.WARNING,
            issues=["High memory"],
            memory_percent=85.0,
            has_alarms=False,
        )
        assert any("list_rabbitmq_queues" in r for r in result)

    def test_moderate_memory(self) -> None:
        """Test moderate memory recommendations."""
        result = _generate_recommendations(
            health=RabbitMQHealthLevel.HEALTHY,
            issues=[],
            memory_percent=65.0,
            has_alarms=False,
        )
        assert any("Monitor queue depths" in r for r in result)


class TestIsSafeForOperations:
    """Tests for _is_safe_for_operations function."""

    def test_safe_cluster(self) -> None:
        """Test safe cluster."""
        result = _is_safe_for_operations(
            health=RabbitMQHealthLevel.HEALTHY,
            has_alarms=False,
            has_partitions=False,
            running_nodes=3,
            total_nodes=3,
        )
        assert result is True

    def test_critical_not_safe(self) -> None:
        """Test critical cluster not safe."""
        result = _is_safe_for_operations(
            health=RabbitMQHealthLevel.CRITICAL,
            has_alarms=False,
            has_partitions=False,
            running_nodes=3,
            total_nodes=3,
        )
        assert result is False

    def test_partitions_not_safe(self) -> None:
        """Test partitions not safe."""
        result = _is_safe_for_operations(
            health=RabbitMQHealthLevel.HEALTHY,
            has_alarms=False,
            has_partitions=True,
            running_nodes=3,
            total_nodes=3,
        )
        assert result is False

    def test_alarms_not_safe(self) -> None:
        """Test alarms not safe."""
        result = _is_safe_for_operations(
            health=RabbitMQHealthLevel.WARNING,
            has_alarms=True,
            has_partitions=False,
            running_nodes=3,
            total_nodes=3,
        )
        assert result is False

    def test_nodes_down_not_safe(self) -> None:
        """Test nodes down not safe."""
        result = _is_safe_for_operations(
            health=RabbitMQHealthLevel.WARNING,
            has_alarms=False,
            has_partitions=False,
            running_nodes=2,
            total_nodes=3,
        )
        assert result is False


class TestGetRabbitMQStatus:
    """Tests for get_rabbitmq_status function."""

    @pytest.fixture
    def mock_kubernetes_adapter(self) -> AsyncMock:
        """Create mock Kubernetes adapter."""
        return AsyncMock()

    @pytest.mark.asyncio
    async def test_get_status_success(self, mock_kubernetes_adapter: AsyncMock) -> None:
        """Test successful status retrieval."""
        mock_cluster_status = MagicMock()
        mock_cluster_status.cluster_name = "rabbit@cluster"
        mock_cluster_status.running_nodes = ["rabbit@node1", "rabbit@node2"]
        mock_cluster_status.disk_nodes = ["rabbit@node1", "rabbit@node2"]
        mock_cluster_status.alarms = []
        mock_cluster_status.partitions = []
        mock_cluster_status.maintenance_status = "not under maintenance"
        mock_cluster_status.listeners = ["amqp:5672"]
        mock_cluster_status.feature_flags = {}
        mock_cluster_status.rabbitmq_version = "3.12.10"
        mock_cluster_status.erlang_version = "25.3.2"
        mock_cluster_status.cpu_cores = 8

        mock_node_status = MagicMock()
        mock_node_status.memory_used_bytes = 512000000
        mock_node_status.memory_limit_bytes = 1024000000
        mock_node_status.memory_percent = 50.0

        mock_client = AsyncMock()
        mock_client.get_cluster_status.return_value = mock_cluster_status
        mock_client.get_node_status.return_value = mock_node_status
        mock_client.list_vhosts.return_value = ["nova", "neutron"]
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None

        with patch(
            "mosk_mcp.tools.messaging_operations.get_rabbitmq_status.RabbitMQClient",
            return_value=mock_client,
        ):
            result = await get_rabbitmq_status(mock_kubernetes_adapter)

        assert result.instance == "main"
        assert result.cluster_name == "rabbit@cluster"
        assert result.health == RabbitMQHealthLevel.HEALTHY
        assert result.is_healthy is True
        assert result.running_nodes == 2
        assert result.total_nodes == 2
        assert result.vhost_count == 2

    @pytest.mark.asyncio
    async def test_get_status_with_alarms(self, mock_kubernetes_adapter: AsyncMock) -> None:
        """Test status with alarms."""
        mock_cluster_status = MagicMock()
        mock_cluster_status.cluster_name = "rabbit@cluster"
        mock_cluster_status.running_nodes = ["rabbit@node1"]
        mock_cluster_status.disk_nodes = ["rabbit@node1"]
        mock_cluster_status.alarms = ["memory"]
        mock_cluster_status.partitions = []
        mock_cluster_status.maintenance_status = "not under maintenance"
        mock_cluster_status.listeners = []
        mock_cluster_status.feature_flags = {}
        mock_cluster_status.rabbitmq_version = "3.12.10"
        mock_cluster_status.erlang_version = "25.3.2"
        mock_cluster_status.cpu_cores = 8

        mock_node_status = MagicMock()
        mock_node_status.memory_used_bytes = 900000000
        mock_node_status.memory_limit_bytes = 1000000000
        mock_node_status.memory_percent = 90.0

        mock_client = AsyncMock()
        mock_client.get_cluster_status.return_value = mock_cluster_status
        mock_client.get_node_status.return_value = mock_node_status
        mock_client.list_vhosts.return_value = []
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None

        with patch(
            "mosk_mcp.tools.messaging_operations.get_rabbitmq_status.RabbitMQClient",
            return_value=mock_client,
        ):
            result = await get_rabbitmq_status(mock_kubernetes_adapter)

        assert result.health == RabbitMQHealthLevel.WARNING
        assert result.has_alarms is True
        assert result.is_safe_for_operations is False

    @pytest.mark.asyncio
    async def test_get_status_neutron_instance(self, mock_kubernetes_adapter: AsyncMock) -> None:
        """Test status for neutron instance."""
        mock_cluster_status = MagicMock()
        mock_cluster_status.cluster_name = "rabbit@neutron"
        mock_cluster_status.running_nodes = ["rabbit@neutron-node"]
        mock_cluster_status.disk_nodes = ["rabbit@neutron-node"]
        mock_cluster_status.alarms = []
        mock_cluster_status.partitions = []
        mock_cluster_status.maintenance_status = "not under maintenance"
        mock_cluster_status.listeners = []
        mock_cluster_status.feature_flags = {"stream_queue": True}
        mock_cluster_status.rabbitmq_version = "3.12.10"
        mock_cluster_status.erlang_version = "25.3.2"
        mock_cluster_status.cpu_cores = 4

        mock_node_status = MagicMock()
        mock_node_status.memory_used_bytes = 256000000
        mock_node_status.memory_limit_bytes = 512000000
        mock_node_status.memory_percent = 50.0

        mock_client = AsyncMock()
        mock_client.get_cluster_status.return_value = mock_cluster_status
        mock_client.get_node_status.return_value = mock_node_status
        mock_client.list_vhosts.return_value = ["neutron"]
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None

        with patch(
            "mosk_mcp.tools.messaging_operations.get_rabbitmq_status.RabbitMQClient",
            return_value=mock_client,
        ):
            result = await get_rabbitmq_status(
                mock_kubernetes_adapter,
                rabbitmq_instance="neutron",
                include_feature_flags=True,
            )

        assert result.instance == "neutron"
        assert result.feature_flags == {"stream_queue": True}

    @pytest.mark.asyncio
    async def test_get_status_error(self, mock_kubernetes_adapter: AsyncMock) -> None:
        """Test error handling."""
        mock_client = AsyncMock()
        mock_client.get_cluster_status.side_effect = Exception("Connection failed")
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None

        with patch(
            "mosk_mcp.tools.messaging_operations.get_rabbitmq_status.RabbitMQClient",
            return_value=mock_client,
        ):
            with pytest.raises(ToolExecutionError) as exc_info:
                await get_rabbitmq_status(mock_kubernetes_adapter)

        assert "Failed to get RabbitMQ status" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_get_status_tool_error_passthrough(
        self, mock_kubernetes_adapter: AsyncMock
    ) -> None:
        """Test ToolExecutionError is passed through."""
        mock_client = AsyncMock()
        mock_client.get_cluster_status.side_effect = ToolExecutionError(
            message="Pod not found",
            tool_name="get_rabbitmq_status",
        )
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None

        with patch(
            "mosk_mcp.tools.messaging_operations.get_rabbitmq_status.RabbitMQClient",
            return_value=mock_client,
        ):
            with pytest.raises(ToolExecutionError) as exc_info:
                await get_rabbitmq_status(mock_kubernetes_adapter)

        assert "Pod not found" in str(exc_info.value)
