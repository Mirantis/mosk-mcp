"""Unit tests for RabbitMQ messaging operations tools.

This module contains comprehensive tests for all RabbitMQ-related MCP tools
including status monitoring, queue listing, connection analysis, and diagnostics.
"""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mosk_mcp.tools.messaging_operations.models import (
    AlarmType,
    ConnectionsByUserSummary,
    ConnectionState,
    DiagnoseRabbitMQIssueInput,
    DiagnoseRabbitMQIssueOutput,
    GetRabbitMQConnectionsInput,
    GetRabbitMQConnectionsOutput,
    GetRabbitMQStatusInput,
    GetRabbitMQStatusOutput,
    ListRabbitMQQueuesInput,
    ListRabbitMQQueuesOutput,
    QueuesByVhostSummary,
    RabbitMQConnectionInfo,
    RabbitMQDiagnosticCheck,
    RabbitMQHealthLevel,
    RabbitMQInstanceDiagnosis,
    RabbitMQNodeInfo,
    RabbitMQQueueInfo,
)


# =============================================================================
# Enum Tests
# =============================================================================


class TestRabbitMQHealthLevel:
    """Tests for RabbitMQHealthLevel enum."""

    def test_health_levels_defined(self) -> None:
        """Test all health levels are defined."""
        assert RabbitMQHealthLevel.HEALTHY == "healthy"
        assert RabbitMQHealthLevel.WARNING == "warning"
        assert RabbitMQHealthLevel.CRITICAL == "critical"
        assert RabbitMQHealthLevel.UNKNOWN == "unknown"

    def test_health_level_count(self) -> None:
        """Test correct number of health levels."""
        assert len(RabbitMQHealthLevel) == 4


class TestConnectionState:
    """Tests for ConnectionState enum."""

    def test_connection_states_defined(self) -> None:
        """Test all connection states are defined."""
        assert ConnectionState.RUNNING == "running"
        assert ConnectionState.BLOCKED == "blocked"
        assert ConnectionState.BLOCKING == "blocking"
        assert ConnectionState.CLOSED == "closed"
        assert ConnectionState.UNKNOWN == "unknown"

    def test_connection_state_count(self) -> None:
        """Test correct number of connection states."""
        assert len(ConnectionState) == 5


class TestAlarmType:
    """Tests for AlarmType enum."""

    def test_alarm_types_defined(self) -> None:
        """Test all alarm types are defined."""
        assert AlarmType.MEMORY == "memory"
        assert AlarmType.DISK == "disk"
        assert AlarmType.NONE == "none"

    def test_alarm_type_count(self) -> None:
        """Test correct number of alarm types."""
        assert len(AlarmType) == 3


# =============================================================================
# Input Model Tests
# =============================================================================


class TestGetRabbitMQStatusInput:
    """Tests for GetRabbitMQStatusInput model."""

    def test_default_values(self) -> None:
        """Test default input values."""
        input_model = GetRabbitMQStatusInput()
        assert input_model.rabbitmq_instance == "main"
        assert input_model.include_feature_flags is False

    def test_custom_instance(self) -> None:
        """Test custom instance value."""
        input_model = GetRabbitMQStatusInput(rabbitmq_instance="neutron")
        assert input_model.rabbitmq_instance == "neutron"

    def test_include_feature_flags(self) -> None:
        """Test feature flags option."""
        input_model = GetRabbitMQStatusInput(include_feature_flags=True)
        assert input_model.include_feature_flags is True


class TestListRabbitMQQueuesInput:
    """Tests for ListRabbitMQQueuesInput model."""

    def test_default_values(self) -> None:
        """Test default input values."""
        input_model = ListRabbitMQQueuesInput()
        assert input_model.rabbitmq_instance == "main"
        assert input_model.vhost is None
        assert input_model.show_empty is False
        assert input_model.include_consumers is True
        assert input_model.limit == 100

    def test_vhost_filter(self) -> None:
        """Test vhost filter."""
        input_model = ListRabbitMQQueuesInput(vhost="nova")
        assert input_model.vhost == "nova"

    def test_limit_validation(self) -> None:
        """Test limit validation."""
        input_model = ListRabbitMQQueuesInput(limit=500)
        assert input_model.limit == 500


class TestGetRabbitMQConnectionsInput:
    """Tests for GetRabbitMQConnectionsInput model."""

    def test_default_values(self) -> None:
        """Test default input values."""
        input_model = GetRabbitMQConnectionsInput()
        assert input_model.rabbitmq_instance == "main"
        assert input_model.include_channels is False
        assert input_model.group_by_user is True
        assert input_model.limit == 200

    def test_include_channels(self) -> None:
        """Test include_channels option."""
        input_model = GetRabbitMQConnectionsInput(include_channels=True)
        assert input_model.include_channels is True


class TestDiagnoseRabbitMQIssueInput:
    """Tests for DiagnoseRabbitMQIssueInput model."""

    def test_default_values(self) -> None:
        """Test default input values."""
        input_model = DiagnoseRabbitMQIssueInput()
        assert input_model.rabbitmq_instance == "all"
        assert input_model.include_queue_analysis is True
        assert input_model.include_connection_analysis is True
        assert input_model.check_for_known_issues is True

    def test_single_instance(self) -> None:
        """Test single instance check."""
        input_model = DiagnoseRabbitMQIssueInput(rabbitmq_instance="main")
        assert input_model.rabbitmq_instance == "main"


# =============================================================================
# Output Model Tests
# =============================================================================


class TestRabbitMQNodeInfo:
    """Tests for RabbitMQNodeInfo model."""

    def test_minimal_creation(self) -> None:
        """Test minimal node info creation."""
        node = RabbitMQNodeInfo(name="rabbit@node1", running=True)
        assert node.name == "rabbit@node1"
        assert node.running is True
        assert node.memory_used_bytes == 0
        assert node.memory_percent == 0.0

    def test_full_creation(self) -> None:
        """Test full node info creation."""
        node = RabbitMQNodeInfo(
            name="rabbit@node1",
            running=True,
            memory_used_bytes=1_000_000_000,
            memory_limit_bytes=2_000_000_000,
            memory_percent=50.0,
            disk_free_bytes=100_000_000_000,
            cpu_cores=8,
            erlang_version="25.0",
            rabbitmq_version="3.12.0",
        )
        assert node.memory_percent == 50.0
        assert node.erlang_version == "25.0"
        assert node.rabbitmq_version == "3.12.0"


class TestRabbitMQQueueInfo:
    """Tests for RabbitMQQueueInfo model."""

    def test_minimal_creation(self) -> None:
        """Test minimal queue info creation."""
        queue = RabbitMQQueueInfo(name="my-queue", vhost="nova")
        assert queue.name == "my-queue"
        assert queue.vhost == "nova"
        assert queue.messages == 0
        assert queue.consumers == 0
        assert queue.is_stale is False

    def test_stale_queue(self) -> None:
        """Test stale queue identification."""
        queue = RabbitMQQueueInfo(
            name="dead-queue",
            vhost="nova",
            messages=100,
            consumers=0,
            is_stale=True,
        )
        assert queue.is_stale is True
        assert queue.messages == 100
        assert queue.consumers == 0


class TestRabbitMQConnectionInfo:
    """Tests for RabbitMQConnectionInfo model."""

    def test_minimal_creation(self) -> None:
        """Test minimal connection info creation."""
        conn = RabbitMQConnectionInfo(name="conn-1", user="nova")
        assert conn.name == "conn-1"
        assert conn.user == "nova"
        assert conn.state == ConnectionState.UNKNOWN
        assert conn.ssl is False

    def test_running_connection(self) -> None:
        """Test running connection."""
        conn = RabbitMQConnectionInfo(
            name="conn-1",
            user="nova",
            state=ConnectionState.RUNNING,
            ssl=True,
            channels=5,
        )
        assert conn.state == ConnectionState.RUNNING
        assert conn.ssl is True
        assert conn.channels == 5


class TestQueuesByVhostSummary:
    """Tests for QueuesByVhostSummary model."""

    def test_creation(self) -> None:
        """Test vhost summary creation."""
        summary = QueuesByVhostSummary(
            vhost="nova",
            queue_count=10,
            total_messages=1000,
            total_consumers=5,
            stale_queues=2,
        )
        assert summary.vhost == "nova"
        assert summary.queue_count == 10
        assert summary.stale_queues == 2


class TestConnectionsByUserSummary:
    """Tests for ConnectionsByUserSummary model."""

    def test_creation(self) -> None:
        """Test user summary creation."""
        summary = ConnectionsByUserSummary(
            user="nova",
            connection_count=10,
            channel_count=50,
            service_name="Nova Compute",
        )
        assert summary.user == "nova"
        assert summary.connection_count == 10
        assert summary.service_name == "Nova Compute"


class TestRabbitMQDiagnosticCheck:
    """Tests for RabbitMQDiagnosticCheck model."""

    def test_passing_check(self) -> None:
        """Test passing check."""
        check = RabbitMQDiagnosticCheck(
            check_name="cluster_health",
            status="pass",
            message="Cluster is healthy",
            severity="info",
        )
        assert check.status == "pass"
        assert check.severity == "info"

    def test_failing_check(self) -> None:
        """Test failing check."""
        check = RabbitMQDiagnosticCheck(
            check_name="memory_alarm",
            status="fail",
            message="Memory alarm active",
            severity="critical",
            details={"alarm_type": "memory"},
        )
        assert check.status == "fail"
        assert check.severity == "critical"
        assert check.details["alarm_type"] == "memory"


class TestRabbitMQInstanceDiagnosis:
    """Tests for RabbitMQInstanceDiagnosis model."""

    def test_healthy_instance(self) -> None:
        """Test healthy instance diagnosis."""
        diagnosis = RabbitMQInstanceDiagnosis(
            instance="main",
            health=RabbitMQHealthLevel.HEALTHY,
            checks=[
                RabbitMQDiagnosticCheck(
                    check_name="health",
                    status="pass",
                    message="OK",
                )
            ],
        )
        assert diagnosis.instance == "main"
        assert diagnosis.health == RabbitMQHealthLevel.HEALTHY
        assert len(diagnosis.checks) == 1


class TestGetRabbitMQStatusOutput:
    """Tests for GetRabbitMQStatusOutput model."""

    def test_healthy_output(self) -> None:
        """Test healthy status output."""
        output = GetRabbitMQStatusOutput(
            instance="main",
            cluster_name="rabbit@cluster",
            health=RabbitMQHealthLevel.HEALTHY,
            health_summary="All nodes healthy",
            running_nodes=3,
            total_nodes=3,
            is_healthy=True,
            is_safe_for_operations=True,
        )
        assert output.is_healthy is True
        assert output.is_safe_for_operations is True
        assert output.running_nodes == output.total_nodes

    def test_unhealthy_output(self) -> None:
        """Test unhealthy status output."""
        output = GetRabbitMQStatusOutput(
            instance="main",
            cluster_name="rabbit@cluster",
            health=RabbitMQHealthLevel.CRITICAL,
            health_summary="Node down",
            running_nodes=2,
            total_nodes=3,
            has_alarms=True,
            alarms=["memory"],
            is_healthy=False,
            is_safe_for_operations=False,
            issues=["Memory alarm active"],
        )
        assert output.is_healthy is False
        assert output.has_alarms is True
        assert "memory" in output.alarms


class TestListRabbitMQQueuesOutput:
    """Tests for ListRabbitMQQueuesOutput model."""

    def test_healthy_output(self) -> None:
        """Test healthy queues output."""
        output = ListRabbitMQQueuesOutput(
            instance="main",
            queues=[
                RabbitMQQueueInfo(name="q1", vhost="nova", messages=10, consumers=2),
                RabbitMQQueueInfo(name="q2", vhost="nova", messages=5, consumers=1),
            ],
            total_queues=2,
            total_messages=15,
            total_consumers=3,
            stale_queue_count=0,
            has_backlog=False,
            has_stale_queues=False,
        )
        assert output.total_queues == 2
        assert output.total_messages == 15
        assert output.has_stale_queues is False

    def test_output_with_stale_queues(self) -> None:
        """Test output with stale queues."""
        output = ListRabbitMQQueuesOutput(
            instance="main",
            queues=[
                RabbitMQQueueInfo(
                    name="dead", vhost="nova", messages=100, consumers=0, is_stale=True
                ),
            ],
            total_queues=1,
            total_messages=100,
            total_consumers=0,
            stale_queue_count=1,
            has_backlog=True,
            has_stale_queues=True,
            recommendations=["Consider purging stale queue: dead"],
        )
        assert output.has_stale_queues is True
        assert output.stale_queue_count == 1


class TestGetRabbitMQConnectionsOutput:
    """Tests for GetRabbitMQConnectionsOutput model."""

    def test_healthy_output(self) -> None:
        """Test healthy connections output."""
        output = GetRabbitMQConnectionsOutput(
            instance="main",
            connections=[
                RabbitMQConnectionInfo(name="c1", user="nova", state=ConnectionState.RUNNING),
            ],
            total_connections=100,
            total_channels=500,
            running_connections=100,
            blocked_connections=0,
            connection_limit=65536,
            connection_utilization_percent=0.15,
            has_blocked_connections=False,
            is_connection_pool_healthy=True,
        )
        assert output.is_connection_pool_healthy is True
        assert output.blocked_connections == 0

    def test_output_with_blocked_connections(self) -> None:
        """Test output with blocked connections."""
        output = GetRabbitMQConnectionsOutput(
            instance="main",
            connections=[],
            total_connections=100,
            total_channels=500,
            running_connections=90,
            blocked_connections=10,
            has_blocked_connections=True,
            is_connection_pool_healthy=False,
            recommendations=["Check memory pressure"],
        )
        assert output.has_blocked_connections is True
        assert output.blocked_connections == 10


class TestDiagnoseRabbitMQIssueOutput:
    """Tests for DiagnoseRabbitMQIssueOutput model."""

    def test_healthy_output(self) -> None:
        """Test healthy diagnosis output."""
        output = DiagnoseRabbitMQIssueOutput(
            instances=[
                RabbitMQInstanceDiagnosis(
                    instance="main",
                    health=RabbitMQHealthLevel.HEALTHY,
                ),
            ],
            overall_health=RabbitMQHealthLevel.HEALTHY,
            health_summary="All instances healthy",
            total_checks=10,
            checks_passed=10,
            checks_warned=0,
            checks_failed=0,
            is_healthy=True,
            requires_immediate_action=False,
        )
        assert output.is_healthy is True
        assert output.checks_passed == 10

    def test_critical_output(self) -> None:
        """Test critical diagnosis output."""
        output = DiagnoseRabbitMQIssueOutput(
            instances=[],
            overall_health=RabbitMQHealthLevel.CRITICAL,
            health_summary="Critical issues found",
            total_checks=10,
            checks_passed=5,
            checks_warned=2,
            checks_failed=3,
            critical_issues=["Memory alarm active", "Disk alarm active"],
            is_healthy=False,
            requires_immediate_action=True,
        )
        assert output.is_healthy is False
        assert output.requires_immediate_action is True
        assert len(output.critical_issues) == 2


# =============================================================================
# RabbitMQ Client Tests
# =============================================================================


@dataclass
class MockExecResult:
    """Mock pod exec result."""

    stdout: bytes
    stderr: bytes
    returncode: int


class TestRabbitMQClient:
    """Tests for RabbitMQClient class."""

    def _create_mock_adapter(self) -> MagicMock:
        """Create a mock Kubernetes adapter."""
        adapter = MagicMock()
        adapter._api = MagicMock()
        return adapter

    @pytest.mark.asyncio
    async def test_client_context_manager(self) -> None:
        """Test client can be used as async context manager."""
        from mosk_mcp.tools.messaging_operations.rabbitmq_client import RabbitMQClient

        adapter = self._create_mock_adapter()

        # Mock finding the pod
        mock_pod = MagicMock()
        mock_pod.name = "openstack-rabbitmq-rabbitmq-0"
        mock_pod.status = MagicMock()
        mock_pod.status.phase = "Running"

        with patch("kr8s.asyncio.objects.Pod") as MockPod:
            MockPod.objects = MagicMock(return_value=MagicMock())
            MockPod.objects.return_value.filter = MagicMock(return_value=MagicMock())
            MockPod.objects.return_value.filter.return_value.__aiter__ = AsyncMock(
                return_value=iter([mock_pod])
            )

            async with RabbitMQClient(adapter, instance="main") as client:
                assert client is not None
                assert client.instance == "main"

    @pytest.mark.asyncio
    async def test_client_pod_name_selection(self) -> None:
        """Test pod name selection for different instances."""
        from mosk_mcp.tools.messaging_operations.rabbitmq_client import RabbitMQClient

        adapter = self._create_mock_adapter()

        # Test main instance
        client = RabbitMQClient(adapter, instance="main")
        assert "openstack-rabbitmq-rabbitmq" in client.pod_name

        # Test neutron instance
        client_neutron = RabbitMQClient(adapter, instance="neutron")
        assert "openstack-neutron-rabbitmq-rabbitmq" in client_neutron.pod_name

    def test_infer_service_from_user(self) -> None:
        """Test service inference from username."""
        from mosk_mcp.tools.messaging_operations.rabbitmq_client import RabbitMQClient

        # Test various usernames - returns lowercase service names
        assert RabbitMQClient.infer_service_from_user("nova") == "nova"
        assert RabbitMQClient.infer_service_from_user("neutron") == "neutron"
        assert RabbitMQClient.infer_service_from_user("cinder") == "cinder"
        assert RabbitMQClient.infer_service_from_user("glance") == "glance"
        assert RabbitMQClient.infer_service_from_user("heat") == "heat"
        assert RabbitMQClient.infer_service_from_user("keystone") == "keystone"
        assert RabbitMQClient.infer_service_from_user("octavia") == "octavia"
        assert RabbitMQClient.infer_service_from_user("placement") == "placement"
        # Unknown users return empty string
        assert RabbitMQClient.infer_service_from_user("custom_user") == ""


# =============================================================================
# Tool Function Tests
# =============================================================================


class TestGetRabbitMQStatusTool:
    """Tests for get_rabbitmq_status tool function."""

    def _create_mock_client(
        self,
        health: RabbitMQHealthLevel = RabbitMQHealthLevel.HEALTHY,
        running_nodes: int = 1,
        disk_nodes: int = 1,
        alarms: list[str] | None = None,
        partitions: list[str] | None = None,
    ) -> MagicMock:
        """Create a mock RabbitMQ client with cluster status."""

        @dataclass
        class MockClusterStatus:
            cluster_name: str = "rabbit@openstack-rabbitmq-rabbitmq-0"
            running_nodes: list[str] = None
            disk_nodes: list[str] = None
            cpu_cores: int = 4
            erlang_version: str = "25.0"
            rabbitmq_version: str = "3.12.0"
            alarms: list[str] = None
            partitions: list[str] = None
            maintenance_status: str = "not under maintenance"
            listeners: list[str] = None
            feature_flags: dict[str, bool] = None

            def __post_init__(self):
                if self.running_nodes is None:
                    self.running_nodes = [f"rabbit@node-{i}" for i in range(running_nodes)]
                if self.disk_nodes is None:
                    self.disk_nodes = [f"rabbit@node-{i}" for i in range(disk_nodes)]
                if self.alarms is None:
                    self.alarms = alarms or []
                if self.partitions is None:
                    self.partitions = partitions or []
                if self.listeners is None:
                    self.listeners = ["amqp:5672", "http:15672"]
                if self.feature_flags is None:
                    self.feature_flags = {}

        @dataclass
        class MockNodeStatus:
            memory_used_bytes: int = 500_000_000
            memory_limit_bytes: int = 1_000_000_000
            memory_percent: float = 50.0
            disk_free_bytes: int = 100_000_000_000

        mock_client = MagicMock()
        mock_client.get_cluster_status = AsyncMock(return_value=MockClusterStatus())
        mock_client.get_node_status = AsyncMock(return_value=MockNodeStatus())
        mock_client.list_vhosts = AsyncMock(return_value=["nova", "neutron", "cinder"])
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        return mock_client

    @pytest.mark.asyncio
    async def test_healthy_status(self) -> None:
        """Test getting healthy cluster status."""
        from mosk_mcp.tools.messaging_operations.get_rabbitmq_status import (
            get_rabbitmq_status,
        )

        adapter = MagicMock()
        mock_client = self._create_mock_client()

        with patch(
            "mosk_mcp.tools.messaging_operations.get_rabbitmq_status.RabbitMQClient",
            return_value=mock_client,
        ):
            result = await get_rabbitmq_status(adapter)

            assert result.instance == "main"
            assert result.health == RabbitMQHealthLevel.HEALTHY
            assert result.is_healthy is True
            assert result.is_safe_for_operations is True
            assert result.has_alarms is False


class TestListRabbitMQQueuesTool:
    """Tests for list_rabbitmq_queues tool function."""

    def _create_mock_client(
        self,
        queues: list | None = None,
    ) -> MagicMock:
        """Create a mock RabbitMQ client with queue data."""

        @dataclass
        class MockQueueInfo:
            name: str
            messages: int = 0
            messages_ready: int = 0
            messages_unacked: int = 0
            consumers: int = 0
            memory_bytes: int = 1000
            state: str = "running"

        default_queues = [
            MockQueueInfo(name="queue1", messages=10, consumers=2),
            MockQueueInfo(name="queue2", messages=5, consumers=1),
        ]

        mock_client = MagicMock()
        mock_client.list_vhosts = AsyncMock(return_value=["nova"])
        mock_client.list_queues = AsyncMock(return_value=queues or default_queues)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        return mock_client

    @pytest.mark.asyncio
    async def test_list_queues(self) -> None:
        """Test listing queues."""
        from mosk_mcp.tools.messaging_operations.list_rabbitmq_queues import (
            list_rabbitmq_queues,
        )

        adapter = MagicMock()
        mock_client = self._create_mock_client()

        with patch(
            "mosk_mcp.tools.messaging_operations.list_rabbitmq_queues.RabbitMQClient",
            return_value=mock_client,
        ):
            result = await list_rabbitmq_queues(adapter)

            assert result.instance == "main"
            assert result.total_queues == 2
            assert len(result.queues) == 2
            assert result.total_messages == 15  # 10 + 5


class TestGetRabbitMQConnectionsTool:
    """Tests for get_rabbitmq_connections tool function."""

    def _create_mock_client(
        self,
        connections: list | None = None,
    ) -> MagicMock:
        """Create a mock RabbitMQ client with connection data."""

        @dataclass
        class MockConnectionInfo:
            name: str
            user: str
            state: str = "running"
            ssl: bool = False
            protocol: str = "AMQP 0-9-1"
            channels: int = 5
            client_host: str = "10.0.0.1"

        default_connections = [
            MockConnectionInfo(name="conn1", user="nova"),
            MockConnectionInfo(name="conn2", user="nova"),
            MockConnectionInfo(name="conn3", user="neutron"),
        ]

        mock_client = MagicMock()
        mock_client.list_connections = AsyncMock(return_value=connections or default_connections)
        mock_client.list_channels = AsyncMock(return_value=[])
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        return mock_client

    @pytest.mark.asyncio
    async def test_get_connections(self) -> None:
        """Test getting connections."""
        from mosk_mcp.tools.messaging_operations.get_rabbitmq_connections import (
            get_rabbitmq_connections,
        )

        adapter = MagicMock()
        mock_client = self._create_mock_client()

        with (
            patch(
                "mosk_mcp.tools.messaging_operations.get_rabbitmq_connections.RabbitMQClient",
                return_value=mock_client,
            ) as MockClientClass,
        ):
            # Need to patch the static method on the class
            MockClientClass.infer_service_from_user = MagicMock(return_value="nova")

            result = await get_rabbitmq_connections(adapter)

            assert result.instance == "main"
            assert result.total_connections == 3
            assert result.blocked_connections == 0
            assert result.is_connection_pool_healthy is True


# =============================================================================
# Helper Function Tests
# =============================================================================


class TestHealthDetermination:
    """Tests for health level determination functions."""

    def test_determine_health_level_healthy(self) -> None:
        """Test healthy determination."""
        from mosk_mcp.tools.messaging_operations.get_rabbitmq_status import (
            _determine_health_level,
        )

        result = _determine_health_level(
            running_nodes=3,
            total_nodes=3,
            has_alarms=False,
            has_partitions=False,
            memory_percent=50.0,
        )
        assert result == RabbitMQHealthLevel.HEALTHY

    def test_determine_health_level_warning_memory(self) -> None:
        """Test warning due to high memory."""
        from mosk_mcp.tools.messaging_operations.get_rabbitmq_status import (
            _determine_health_level,
        )

        result = _determine_health_level(
            running_nodes=3,
            total_nodes=3,
            has_alarms=False,
            has_partitions=False,
            memory_percent=85.0,
        )
        assert result == RabbitMQHealthLevel.WARNING

    def test_determine_health_level_warning_alarms(self) -> None:
        """Test warning due to alarms."""
        from mosk_mcp.tools.messaging_operations.get_rabbitmq_status import (
            _determine_health_level,
        )

        result = _determine_health_level(
            running_nodes=3,
            total_nodes=3,
            has_alarms=True,
            has_partitions=False,
            memory_percent=50.0,
        )
        assert result == RabbitMQHealthLevel.WARNING

    def test_determine_health_level_critical_node_down(self) -> None:
        """Test critical due to node down."""
        from mosk_mcp.tools.messaging_operations.get_rabbitmq_status import (
            _determine_health_level,
        )

        result = _determine_health_level(
            running_nodes=2,
            total_nodes=3,
            has_alarms=False,
            has_partitions=False,
            memory_percent=50.0,
        )
        assert result == RabbitMQHealthLevel.CRITICAL

    def test_determine_health_level_critical_partition(self) -> None:
        """Test critical due to network partition."""
        from mosk_mcp.tools.messaging_operations.get_rabbitmq_status import (
            _determine_health_level,
        )

        result = _determine_health_level(
            running_nodes=3,
            total_nodes=3,
            has_alarms=False,
            has_partitions=True,
            memory_percent=50.0,
        )
        assert result == RabbitMQHealthLevel.CRITICAL


class TestConnectionStateMapping:
    """Tests for connection state mapping."""

    def test_map_connection_state_running(self) -> None:
        """Test mapping running state."""
        from mosk_mcp.tools.messaging_operations.get_rabbitmq_connections import (
            _map_connection_state,
        )

        assert _map_connection_state("running") == ConnectionState.RUNNING
        assert _map_connection_state("RUNNING") == ConnectionState.RUNNING

    def test_map_connection_state_blocked(self) -> None:
        """Test mapping blocked state."""
        from mosk_mcp.tools.messaging_operations.get_rabbitmq_connections import (
            _map_connection_state,
        )

        assert _map_connection_state("blocked") == ConnectionState.BLOCKED
        assert _map_connection_state("blocking") == ConnectionState.BLOCKING

    def test_map_connection_state_unknown(self) -> None:
        """Test mapping unknown state."""
        from mosk_mcp.tools.messaging_operations.get_rabbitmq_connections import (
            _map_connection_state,
        )

        assert _map_connection_state("unexpected") == ConnectionState.UNKNOWN


class TestRecommendationGeneration:
    """Tests for recommendation generation functions."""

    def test_generate_recommendations_healthy(self) -> None:
        """Test recommendations for healthy cluster."""
        from mosk_mcp.tools.messaging_operations.get_rabbitmq_connections import (
            _generate_recommendations,
        )

        recs = _generate_recommendations(
            total_connections=100,
            blocked_connections=0,
            utilization_percent=10.0,
            top_users=["nova", "neutron"],
        )

        assert any("healthy" in r.lower() for r in recs)

    def test_generate_recommendations_blocked(self) -> None:
        """Test recommendations when connections are blocked."""
        from mosk_mcp.tools.messaging_operations.get_rabbitmq_connections import (
            _generate_recommendations,
        )

        recs = _generate_recommendations(
            total_connections=100,
            blocked_connections=5,
            utilization_percent=10.0,
            top_users=["nova"],
        )

        assert any("blocked" in r.lower() for r in recs)

    def test_generate_recommendations_high_utilization(self) -> None:
        """Test recommendations for high utilization."""
        from mosk_mcp.tools.messaging_operations.get_rabbitmq_connections import (
            _generate_recommendations,
        )

        recs = _generate_recommendations(
            total_connections=100,
            blocked_connections=0,
            utilization_percent=95.0,
            top_users=["nova"],
        )

        assert any("critical" in r.lower() for r in recs)


# =============================================================================
# DiagnoseRabbitMQIssue Tests
# =============================================================================


class TestCreateCheck:
    """Tests for _create_check helper function."""

    def test_create_passing_check(self) -> None:
        """Test creating a passing check."""
        from mosk_mcp.tools.messaging_operations.diagnose_rabbitmq_issue import (
            _create_check,
        )

        check = _create_check(
            "test_check",
            passed=True,
            message="All good",
            severity="info",
        )
        assert check.check_name == "test_check"
        assert check.status == "pass"
        assert check.message == "All good"
        assert check.severity == "info"

    def test_create_failing_check_warning(self) -> None:
        """Test creating a failing check with warning severity."""
        from mosk_mcp.tools.messaging_operations.diagnose_rabbitmq_issue import (
            _create_check,
        )

        check = _create_check(
            "test_check",
            passed=False,
            message="Something wrong",
            severity="warning",
        )
        assert check.status == "warn"
        assert check.severity == "warning"

    def test_create_failing_check_critical(self) -> None:
        """Test creating a failing check with critical severity."""
        from mosk_mcp.tools.messaging_operations.diagnose_rabbitmq_issue import (
            _create_check,
        )

        check = _create_check(
            "test_check",
            passed=False,
            message="Critical failure",
            severity="critical",
        )
        assert check.status == "fail"
        assert check.severity == "critical"

    def test_create_check_with_details(self) -> None:
        """Test creating a check with details."""
        from mosk_mcp.tools.messaging_operations.diagnose_rabbitmq_issue import (
            _create_check,
        )

        check = _create_check(
            "test_check",
            passed=True,
            message="OK",
            severity="info",
            details={"key": "value"},
        )
        assert check.details == {"key": "value"}


class TestDiagnoseGenerateRecommendations:
    """Tests for _generate_recommendations in diagnose_rabbitmq_issue."""

    def test_recommendations_critical(self) -> None:
        """Test recommendations for critical health."""
        from mosk_mcp.tools.messaging_operations.diagnose_rabbitmq_issue import (
            _generate_recommendations,
        )

        recs = _generate_recommendations(
            instances=[],
            overall_health=RabbitMQHealthLevel.CRITICAL,
            critical_issues=["Memory alarm active"],
        )

        assert any("IMMEDIATE ACTION" in r for r in recs)
        assert any("alarm" in r.lower() for r in recs)

    def test_recommendations_healthy(self) -> None:
        """Test recommendations for healthy cluster."""
        from mosk_mcp.tools.messaging_operations.diagnose_rabbitmq_issue import (
            _generate_recommendations,
        )

        recs = _generate_recommendations(
            instances=[],
            overall_health=RabbitMQHealthLevel.HEALTHY,
            critical_issues=[],
        )

        assert any("healthy" in r.lower() for r in recs)

    def test_recommendations_known_issue_mosk001(self) -> None:
        """Test recommendations when MOSK-001 pattern matches."""
        from mosk_mcp.tools.messaging_operations.diagnose_rabbitmq_issue import (
            _generate_recommendations,
        )

        # Create an instance diagnosis with MOSK-001 match
        instance = RabbitMQInstanceDiagnosis(
            instance="main",
            health=RabbitMQHealthLevel.WARNING,
            checks=[],
            known_issue_matches=["MOSK-001"],
        )

        recs = _generate_recommendations(
            instances=[instance],
            overall_health=RabbitMQHealthLevel.WARNING,
            critical_issues=[],
        )

        assert any("MOSK-001" in r for r in recs)
        assert any("connection" in r.lower() for r in recs)

    def test_recommendations_blocked_connections(self) -> None:
        """Test recommendations for blocked connections."""
        from mosk_mcp.tools.messaging_operations.diagnose_rabbitmq_issue import (
            _generate_recommendations,
        )

        recs = _generate_recommendations(
            instances=[],
            overall_health=RabbitMQHealthLevel.CRITICAL,
            critical_issues=["Blocked connections: 10"],
        )

        assert any("blocked" in r.lower() for r in recs)

    def test_recommendations_partition(self) -> None:
        """Test recommendations for network partition."""
        from mosk_mcp.tools.messaging_operations.diagnose_rabbitmq_issue import (
            _generate_recommendations,
        )

        recs = _generate_recommendations(
            instances=[],
            overall_health=RabbitMQHealthLevel.CRITICAL,
            critical_issues=["Network partition detected"],
        )

        assert any("partition" in r.lower() for r in recs)
        assert any("split-brain" in r.lower() for r in recs)


class TestDiagnoseRabbitMQIssueTool:
    """Tests for diagnose_rabbitmq_issue tool function."""

    def _create_mock_status_healthy(self) -> GetRabbitMQStatusOutput:
        """Create mock healthy status output."""
        return GetRabbitMQStatusOutput(
            instance="main",
            cluster_name="rabbit@cluster",
            health=RabbitMQHealthLevel.HEALTHY,
            health_summary="Healthy",
            running_nodes=1,
            total_nodes=1,
            is_healthy=True,
            is_safe_for_operations=True,
            has_alarms=False,
            alarms=[],
            has_partitions=False,
            partitions=[],
            nodes=[
                RabbitMQNodeInfo(
                    name="rabbit@node1",
                    running=True,
                    memory_percent=50.0,
                )
            ],
        )

    def _create_mock_queues_healthy(self) -> ListRabbitMQQueuesOutput:
        """Create mock healthy queues output."""
        return ListRabbitMQQueuesOutput(
            instance="main",
            queues=[],
            total_queues=5,
            total_messages=100,
            total_consumers=10,
            stale_queue_count=0,
            has_backlog=False,
            has_stale_queues=False,
        )

    def _create_mock_connections_healthy(self) -> GetRabbitMQConnectionsOutput:
        """Create mock healthy connections output."""
        return GetRabbitMQConnectionsOutput(
            instance="main",
            connections=[],
            total_connections=50,
            total_channels=100,
            running_connections=50,
            blocked_connections=0,
            has_blocked_connections=False,
            is_connection_pool_healthy=True,
        )

    @pytest.mark.asyncio
    async def test_diagnose_healthy(self) -> None:
        """Test diagnosis of healthy instance."""
        from mosk_mcp.tools.messaging_operations.diagnose_rabbitmq_issue import (
            diagnose_rabbitmq_issue,
        )

        adapter = MagicMock()

        with (
            patch(
                "mosk_mcp.tools.messaging_operations.diagnose_rabbitmq_issue.get_rabbitmq_status",
                AsyncMock(return_value=self._create_mock_status_healthy()),
            ),
            patch(
                "mosk_mcp.tools.messaging_operations.diagnose_rabbitmq_issue.list_rabbitmq_queues",
                AsyncMock(return_value=self._create_mock_queues_healthy()),
            ),
            patch(
                "mosk_mcp.tools.messaging_operations.diagnose_rabbitmq_issue.get_rabbitmq_connections",
                AsyncMock(return_value=self._create_mock_connections_healthy()),
            ),
        ):
            result = await diagnose_rabbitmq_issue(
                adapter,
                rabbitmq_instance="main",
            )

        assert result.is_healthy is True
        assert result.overall_health == RabbitMQHealthLevel.HEALTHY
        assert result.checks_passed > 0

    @pytest.mark.asyncio
    async def test_diagnose_all_instances(self) -> None:
        """Test diagnosis of all instances."""
        from mosk_mcp.tools.messaging_operations.diagnose_rabbitmq_issue import (
            diagnose_rabbitmq_issue,
        )

        adapter = MagicMock()

        with (
            patch(
                "mosk_mcp.tools.messaging_operations.diagnose_rabbitmq_issue.get_rabbitmq_status",
                AsyncMock(return_value=self._create_mock_status_healthy()),
            ),
            patch(
                "mosk_mcp.tools.messaging_operations.diagnose_rabbitmq_issue.list_rabbitmq_queues",
                AsyncMock(return_value=self._create_mock_queues_healthy()),
            ),
            patch(
                "mosk_mcp.tools.messaging_operations.diagnose_rabbitmq_issue.get_rabbitmq_connections",
                AsyncMock(return_value=self._create_mock_connections_healthy()),
            ),
        ):
            result = await diagnose_rabbitmq_issue(
                adapter,
                rabbitmq_instance="all",
            )

        # Should have diagnosed both main and neutron
        assert len(result.instances) == 2
        assert any(i.instance == "main" for i in result.instances)
        assert any(i.instance == "neutron" for i in result.instances)

    @pytest.mark.asyncio
    async def test_diagnose_with_alarms(self) -> None:
        """Test diagnosis when alarms are active."""
        from mosk_mcp.tools.messaging_operations.diagnose_rabbitmq_issue import (
            diagnose_rabbitmq_issue,
        )

        adapter = MagicMock()

        # Create status with active alarm
        status_with_alarm = GetRabbitMQStatusOutput(
            instance="main",
            cluster_name="rabbit@cluster",
            health=RabbitMQHealthLevel.WARNING,
            health_summary="Memory alarm",
            running_nodes=1,
            total_nodes=1,
            is_healthy=False,
            is_safe_for_operations=False,
            has_alarms=True,
            alarms=["memory"],
            has_partitions=False,
            partitions=[],
            nodes=[
                RabbitMQNodeInfo(
                    name="rabbit@node1",
                    running=True,
                    memory_percent=90.0,
                )
            ],
        )

        with (
            patch(
                "mosk_mcp.tools.messaging_operations.diagnose_rabbitmq_issue.get_rabbitmq_status",
                AsyncMock(return_value=status_with_alarm),
            ),
            patch(
                "mosk_mcp.tools.messaging_operations.diagnose_rabbitmq_issue.list_rabbitmq_queues",
                AsyncMock(return_value=self._create_mock_queues_healthy()),
            ),
            patch(
                "mosk_mcp.tools.messaging_operations.diagnose_rabbitmq_issue.get_rabbitmq_connections",
                AsyncMock(return_value=self._create_mock_connections_healthy()),
            ),
        ):
            result = await diagnose_rabbitmq_issue(
                adapter,
                rabbitmq_instance="main",
            )

        # Should detect alarm as critical
        assert result.is_healthy is False
        assert len(result.critical_issues) > 0

    @pytest.mark.asyncio
    async def test_diagnose_with_stale_queues(self) -> None:
        """Test diagnosis with stale queues."""
        from mosk_mcp.tools.messaging_operations.diagnose_rabbitmq_issue import (
            diagnose_rabbitmq_issue,
        )

        adapter = MagicMock()

        # Create queues output with stale queues
        queues_with_stale = ListRabbitMQQueuesOutput(
            instance="main",
            queues=[
                RabbitMQQueueInfo(
                    name="stale1",
                    vhost="nova",
                    messages=1000,
                    consumers=0,
                    is_stale=True,
                ),
                RabbitMQQueueInfo(
                    name="stale2",
                    vhost="nova",
                    messages=500,
                    consumers=0,
                    is_stale=True,
                ),
            ],
            total_queues=2,
            total_messages=1500,
            total_consumers=0,
            stale_queue_count=2,
            has_backlog=True,
            has_stale_queues=True,
        )

        with (
            patch(
                "mosk_mcp.tools.messaging_operations.diagnose_rabbitmq_issue.get_rabbitmq_status",
                AsyncMock(return_value=self._create_mock_status_healthy()),
            ),
            patch(
                "mosk_mcp.tools.messaging_operations.diagnose_rabbitmq_issue.list_rabbitmq_queues",
                AsyncMock(return_value=queues_with_stale),
            ),
            patch(
                "mosk_mcp.tools.messaging_operations.diagnose_rabbitmq_issue.get_rabbitmq_connections",
                AsyncMock(return_value=self._create_mock_connections_healthy()),
            ),
        ):
            result = await diagnose_rabbitmq_issue(
                adapter,
                rabbitmq_instance="main",
            )

        # Should detect stale queues
        assert result.checks_warned > 0 or result.checks_failed > 0

    @pytest.mark.asyncio
    async def test_diagnose_with_blocked_connections(self) -> None:
        """Test diagnosis with blocked connections."""
        from mosk_mcp.tools.messaging_operations.diagnose_rabbitmq_issue import (
            diagnose_rabbitmq_issue,
        )

        adapter = MagicMock()

        # Create connections output with blocked connections
        connections_blocked = GetRabbitMQConnectionsOutput(
            instance="main",
            connections=[],
            total_connections=100,
            total_channels=200,
            running_connections=90,
            blocked_connections=10,
            has_blocked_connections=True,
            is_connection_pool_healthy=False,
        )

        with (
            patch(
                "mosk_mcp.tools.messaging_operations.diagnose_rabbitmq_issue.get_rabbitmq_status",
                AsyncMock(return_value=self._create_mock_status_healthy()),
            ),
            patch(
                "mosk_mcp.tools.messaging_operations.diagnose_rabbitmq_issue.list_rabbitmq_queues",
                AsyncMock(return_value=self._create_mock_queues_healthy()),
            ),
            patch(
                "mosk_mcp.tools.messaging_operations.diagnose_rabbitmq_issue.get_rabbitmq_connections",
                AsyncMock(return_value=connections_blocked),
            ),
        ):
            result = await diagnose_rabbitmq_issue(
                adapter,
                rabbitmq_instance="main",
            )

        # Should detect blocked connections (goes to warnings, triggers MOSK-001)
        assert result.requires_immediate_action is True
        assert len(result.warnings) > 0
        assert "MOSK-001" in result.known_issue_ids

    @pytest.mark.asyncio
    async def test_diagnose_without_queue_analysis(self) -> None:
        """Test diagnosis without queue analysis."""
        from mosk_mcp.tools.messaging_operations.diagnose_rabbitmq_issue import (
            diagnose_rabbitmq_issue,
        )

        adapter = MagicMock()
        list_queues_mock = AsyncMock(return_value=self._create_mock_queues_healthy())

        with (
            patch(
                "mosk_mcp.tools.messaging_operations.diagnose_rabbitmq_issue.get_rabbitmq_status",
                AsyncMock(return_value=self._create_mock_status_healthy()),
            ),
            patch(
                "mosk_mcp.tools.messaging_operations.diagnose_rabbitmq_issue.list_rabbitmq_queues",
                list_queues_mock,
            ),
            patch(
                "mosk_mcp.tools.messaging_operations.diagnose_rabbitmq_issue.get_rabbitmq_connections",
                AsyncMock(return_value=self._create_mock_connections_healthy()),
            ),
        ):
            await diagnose_rabbitmq_issue(
                adapter,
                rabbitmq_instance="main",
                include_queue_analysis=False,
            )

        # Should not call list_rabbitmq_queues
        list_queues_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_diagnose_without_connection_analysis(self) -> None:
        """Test diagnosis without connection analysis."""
        from mosk_mcp.tools.messaging_operations.diagnose_rabbitmq_issue import (
            diagnose_rabbitmq_issue,
        )

        adapter = MagicMock()
        get_connections_mock = AsyncMock(return_value=self._create_mock_connections_healthy())

        with (
            patch(
                "mosk_mcp.tools.messaging_operations.diagnose_rabbitmq_issue.get_rabbitmq_status",
                AsyncMock(return_value=self._create_mock_status_healthy()),
            ),
            patch(
                "mosk_mcp.tools.messaging_operations.diagnose_rabbitmq_issue.list_rabbitmq_queues",
                AsyncMock(return_value=self._create_mock_queues_healthy()),
            ),
            patch(
                "mosk_mcp.tools.messaging_operations.diagnose_rabbitmq_issue.get_rabbitmq_connections",
                get_connections_mock,
            ),
        ):
            await diagnose_rabbitmq_issue(
                adapter,
                rabbitmq_instance="main",
                include_connection_analysis=False,
            )

        # Should not call get_rabbitmq_connections
        get_connections_mock.assert_not_called()
