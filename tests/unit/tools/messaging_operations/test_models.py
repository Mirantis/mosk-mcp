"""Unit tests for messaging_operations models."""

import pytest
from pydantic import ValidationError

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


class TestRabbitMQHealthLevel:
    """Tests for RabbitMQHealthLevel enum."""

    def test_all_levels_defined(self) -> None:
        """Test all expected health levels are defined."""
        assert RabbitMQHealthLevel.HEALTHY == "healthy"
        assert RabbitMQHealthLevel.WARNING == "warning"
        assert RabbitMQHealthLevel.CRITICAL == "critical"
        assert RabbitMQHealthLevel.UNKNOWN == "unknown"

    def test_enum_values(self) -> None:
        """Test enum values are strings."""
        for level in RabbitMQHealthLevel:
            assert isinstance(level.value, str)


class TestConnectionState:
    """Tests for ConnectionState enum."""

    def test_all_states_defined(self) -> None:
        """Test all expected connection states are defined."""
        assert ConnectionState.RUNNING == "running"
        assert ConnectionState.BLOCKED == "blocked"
        assert ConnectionState.BLOCKING == "blocking"
        assert ConnectionState.CLOSED == "closed"
        assert ConnectionState.UNKNOWN == "unknown"


class TestAlarmType:
    """Tests for AlarmType enum."""

    def test_all_types_defined(self) -> None:
        """Test all expected alarm types are defined."""
        assert AlarmType.MEMORY == "memory"
        assert AlarmType.DISK == "disk"
        assert AlarmType.NONE == "none"


class TestGetRabbitMQStatusInput:
    """Tests for GetRabbitMQStatusInput model."""

    def test_default_values(self) -> None:
        """Test default values."""
        input_model = GetRabbitMQStatusInput()
        assert input_model.rabbitmq_instance == "main"
        assert input_model.include_feature_flags is False

    def test_main_instance(self) -> None:
        """Test main instance."""
        input_model = GetRabbitMQStatusInput(rabbitmq_instance="main")
        assert input_model.rabbitmq_instance == "main"

    def test_neutron_instance(self) -> None:
        """Test neutron instance."""
        input_model = GetRabbitMQStatusInput(rabbitmq_instance="neutron")
        assert input_model.rabbitmq_instance == "neutron"

    def test_include_feature_flags(self) -> None:
        """Test include_feature_flags flag."""
        input_model = GetRabbitMQStatusInput(include_feature_flags=True)
        assert input_model.include_feature_flags is True

    def test_invalid_instance(self) -> None:
        """Test invalid instance raises validation error."""
        with pytest.raises(ValidationError):
            GetRabbitMQStatusInput(rabbitmq_instance="invalid")


class TestRabbitMQNodeInfo:
    """Tests for RabbitMQNodeInfo model."""

    def test_required_fields(self) -> None:
        """Test required fields."""
        node = RabbitMQNodeInfo(
            name="rabbit@openstack-rabbitmq-rabbitmq-0",
            running=True,
        )
        assert node.name == "rabbit@openstack-rabbitmq-rabbitmq-0"
        assert node.running is True

    def test_default_values(self) -> None:
        """Test default values."""
        node = RabbitMQNodeInfo(name="test", running=True)
        assert node.memory_used_bytes == 0
        assert node.memory_limit_bytes == 0
        assert node.memory_percent == 0.0
        assert node.disk_free_bytes == 0
        assert node.cpu_cores == 0
        assert node.erlang_version == ""
        assert node.rabbitmq_version == ""

    def test_all_fields(self) -> None:
        """Test all fields."""
        node = RabbitMQNodeInfo(
            name="rabbit@test",
            running=True,
            memory_used_bytes=1024000000,
            memory_limit_bytes=2048000000,
            memory_percent=50.0,
            disk_free_bytes=10000000000,
            cpu_cores=8,
            erlang_version="25.3.2",
            rabbitmq_version="3.12.10",
        )
        assert node.memory_used_bytes == 1024000000
        assert node.memory_percent == 50.0
        assert node.cpu_cores == 8


class TestGetRabbitMQStatusOutput:
    """Tests for GetRabbitMQStatusOutput model."""

    def test_required_fields(self) -> None:
        """Test required fields."""
        output = GetRabbitMQStatusOutput(
            instance="main",
            cluster_name="rabbit@cluster",
            health=RabbitMQHealthLevel.HEALTHY,
            health_summary="Cluster is healthy",
            running_nodes=3,
            total_nodes=3,
            is_healthy=True,
            is_safe_for_operations=True,
        )
        assert output.instance == "main"
        assert output.health == RabbitMQHealthLevel.HEALTHY
        assert output.is_healthy is True

    def test_default_values(self) -> None:
        """Test default values."""
        output = GetRabbitMQStatusOutput(
            instance="main",
            cluster_name="test",
            health=RabbitMQHealthLevel.HEALTHY,
            health_summary="OK",
            running_nodes=1,
            total_nodes=1,
            is_healthy=True,
            is_safe_for_operations=True,
        )
        assert output.nodes == []
        assert output.alarms == []
        assert output.has_alarms is False
        assert output.partitions == []
        assert output.has_partitions is False
        assert output.vhosts == []
        assert output.vhost_count == 0
        assert output.feature_flags == {}
        assert output.listeners == []


class TestListRabbitMQQueuesInput:
    """Tests for ListRabbitMQQueuesInput model."""

    def test_default_values(self) -> None:
        """Test default values."""
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

    def test_limit_bounds(self) -> None:
        """Test limit bounds."""
        input_model = ListRabbitMQQueuesInput(limit=1)
        assert input_model.limit == 1

        input_model = ListRabbitMQQueuesInput(limit=1000)
        assert input_model.limit == 1000

    def test_limit_below_minimum(self) -> None:
        """Test limit below minimum raises error."""
        with pytest.raises(ValidationError):
            ListRabbitMQQueuesInput(limit=0)

    def test_limit_above_maximum(self) -> None:
        """Test limit above maximum raises error."""
        with pytest.raises(ValidationError):
            ListRabbitMQQueuesInput(limit=1001)


class TestRabbitMQQueueInfo:
    """Tests for RabbitMQQueueInfo model."""

    def test_required_fields(self) -> None:
        """Test required fields."""
        queue = RabbitMQQueueInfo(name="queue1", vhost="nova")
        assert queue.name == "queue1"
        assert queue.vhost == "nova"

    def test_default_values(self) -> None:
        """Test default values."""
        queue = RabbitMQQueueInfo(name="test", vhost="/")
        assert queue.messages == 0
        assert queue.messages_ready == 0
        assert queue.messages_unacked == 0
        assert queue.consumers == 0
        assert queue.memory_bytes == 0
        assert queue.state == "running"
        assert queue.is_stale is False

    def test_stale_queue(self) -> None:
        """Test stale queue."""
        queue = RabbitMQQueueInfo(
            name="stale-queue",
            vhost="nova",
            messages=100,
            consumers=0,
            is_stale=True,
        )
        assert queue.is_stale is True


class TestQueuesByVhostSummary:
    """Tests for QueuesByVhostSummary model."""

    def test_required_fields(self) -> None:
        """Test required fields."""
        summary = QueuesByVhostSummary(vhost="nova")
        assert summary.vhost == "nova"

    def test_default_values(self) -> None:
        """Test default values."""
        summary = QueuesByVhostSummary(vhost="test")
        assert summary.queue_count == 0
        assert summary.total_messages == 0
        assert summary.total_consumers == 0
        assert summary.stale_queues == 0


class TestListRabbitMQQueuesOutput:
    """Tests for ListRabbitMQQueuesOutput model."""

    def test_required_fields(self) -> None:
        """Test required fields."""
        output = ListRabbitMQQueuesOutput(instance="main")
        assert output.instance == "main"

    def test_default_values(self) -> None:
        """Test default values."""
        output = ListRabbitMQQueuesOutput(instance="main")
        assert output.queues == []
        assert output.total_queues == 0
        assert output.total_messages == 0
        assert output.total_consumers == 0
        assert output.stale_queue_count == 0
        assert output.by_vhost == []
        assert output.top_queues_by_messages == []
        assert output.has_backlog is False
        assert output.has_stale_queues is False


class TestGetRabbitMQConnectionsInput:
    """Tests for GetRabbitMQConnectionsInput model."""

    def test_default_values(self) -> None:
        """Test default values."""
        input_model = GetRabbitMQConnectionsInput()
        assert input_model.rabbitmq_instance == "main"
        assert input_model.include_channels is False
        assert input_model.group_by_user is True
        assert input_model.limit == 200

    def test_limit_bounds(self) -> None:
        """Test limit bounds."""
        input_model = GetRabbitMQConnectionsInput(limit=1)
        assert input_model.limit == 1

        input_model = GetRabbitMQConnectionsInput(limit=1000)
        assert input_model.limit == 1000


class TestRabbitMQConnectionInfo:
    """Tests for RabbitMQConnectionInfo model."""

    def test_required_fields(self) -> None:
        """Test required fields."""
        conn = RabbitMQConnectionInfo(
            name="192.168.1.10:5672",
            user="nova",
        )
        assert conn.name == "192.168.1.10:5672"
        assert conn.user == "nova"

    def test_default_values(self) -> None:
        """Test default values."""
        conn = RabbitMQConnectionInfo(name="test", user="test")
        assert conn.state == ConnectionState.UNKNOWN
        assert conn.ssl is False
        assert conn.protocol == "AMQP 0-9-1"
        assert conn.channels == 0
        assert conn.client_host == ""
        assert conn.connected_at == ""


class TestConnectionsByUserSummary:
    """Tests for ConnectionsByUserSummary model."""

    def test_required_fields(self) -> None:
        """Test required fields."""
        summary = ConnectionsByUserSummary(user="nova")
        assert summary.user == "nova"

    def test_default_values(self) -> None:
        """Test default values."""
        summary = ConnectionsByUserSummary(user="test")
        assert summary.connection_count == 0
        assert summary.channel_count == 0
        assert summary.service_name == ""


class TestGetRabbitMQConnectionsOutput:
    """Tests for GetRabbitMQConnectionsOutput model."""

    def test_required_fields(self) -> None:
        """Test required fields."""
        output = GetRabbitMQConnectionsOutput(instance="main")
        assert output.instance == "main"

    def test_default_values(self) -> None:
        """Test default values."""
        output = GetRabbitMQConnectionsOutput(instance="main")
        assert output.connections == []
        assert output.total_connections == 0
        assert output.total_channels == 0
        assert output.running_connections == 0
        assert output.blocked_connections == 0
        assert output.by_user == []
        assert output.top_users == []
        assert output.connection_limit is None
        assert output.connection_utilization_percent is None
        assert output.has_blocked_connections is False
        assert output.is_connection_pool_healthy is True


class TestDiagnoseRabbitMQIssueInput:
    """Tests for DiagnoseRabbitMQIssueInput model."""

    def test_default_values(self) -> None:
        """Test default values."""
        input_model = DiagnoseRabbitMQIssueInput()
        assert input_model.rabbitmq_instance == "all"
        assert input_model.include_queue_analysis is True
        assert input_model.include_connection_analysis is True
        assert input_model.check_for_known_issues is True

    def test_instance_values(self) -> None:
        """Test instance values."""
        for instance in ["main", "neutron", "all"]:
            input_model = DiagnoseRabbitMQIssueInput(rabbitmq_instance=instance)
            assert input_model.rabbitmq_instance == instance


class TestRabbitMQDiagnosticCheck:
    """Tests for RabbitMQDiagnosticCheck model."""

    def test_required_fields(self) -> None:
        """Test required fields."""
        check = RabbitMQDiagnosticCheck(
            check_name="cluster_health",
            status="pass",
            message="Cluster is healthy",
        )
        assert check.check_name == "cluster_health"
        assert check.status == "pass"
        assert check.message == "Cluster is healthy"

    def test_default_values(self) -> None:
        """Test default values."""
        check = RabbitMQDiagnosticCheck(
            check_name="test",
            status="pass",
            message="OK",
        )
        assert check.severity == "info"
        assert check.details == {}

    def test_status_values(self) -> None:
        """Test status values."""
        for status in ["pass", "warn", "fail", "skip"]:
            check = RabbitMQDiagnosticCheck(
                check_name="test",
                status=status,
                message="test",
            )
            assert check.status == status

    def test_severity_values(self) -> None:
        """Test severity values."""
        for severity in ["info", "warning", "error", "critical"]:
            check = RabbitMQDiagnosticCheck(
                check_name="test",
                status="pass",
                message="test",
                severity=severity,
            )
            assert check.severity == severity


class TestRabbitMQInstanceDiagnosis:
    """Tests for RabbitMQInstanceDiagnosis model."""

    def test_required_fields(self) -> None:
        """Test required fields."""
        diagnosis = RabbitMQInstanceDiagnosis(
            instance="main",
            health=RabbitMQHealthLevel.HEALTHY,
        )
        assert diagnosis.instance == "main"
        assert diagnosis.health == RabbitMQHealthLevel.HEALTHY

    def test_default_values(self) -> None:
        """Test default values."""
        diagnosis = RabbitMQInstanceDiagnosis(
            instance="main",
            health=RabbitMQHealthLevel.HEALTHY,
        )
        assert diagnosis.checks == []
        assert diagnosis.issues_found == []
        assert diagnosis.known_issue_matches == []


class TestDiagnoseRabbitMQIssueOutput:
    """Tests for DiagnoseRabbitMQIssueOutput model."""

    def test_required_fields(self) -> None:
        """Test required fields."""
        output = DiagnoseRabbitMQIssueOutput(
            overall_health=RabbitMQHealthLevel.HEALTHY,
            health_summary="All systems healthy",
            is_healthy=True,
        )
        assert output.overall_health == RabbitMQHealthLevel.HEALTHY
        assert output.health_summary == "All systems healthy"
        assert output.is_healthy is True

    def test_default_values(self) -> None:
        """Test default values."""
        output = DiagnoseRabbitMQIssueOutput(
            overall_health=RabbitMQHealthLevel.HEALTHY,
            health_summary="OK",
            is_healthy=True,
        )
        assert output.instances == []
        assert output.total_checks == 0
        assert output.checks_passed == 0
        assert output.checks_warned == 0
        assert output.checks_failed == 0
        assert output.critical_issues == []
        assert output.warnings == []
        assert output.known_issue_ids == []
        assert output.requires_immediate_action is False
