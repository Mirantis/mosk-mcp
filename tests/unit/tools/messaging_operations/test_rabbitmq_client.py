"""Unit tests for RabbitMQ client."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from mosk_mcp.adapters.utils.pod_exec import PodExecResult
from mosk_mcp.tools.messaging_operations.rabbitmq_client import (
    RABBITMQ_NAMESPACE,
    RABBITMQ_PODS,
    ClusterStatusResult,
    ConnectionInfo,
    NodeStatusResult,
    QueueInfo,
    RabbitMQClient,
)


class TestRabbitMQClientConstants:
    """Tests for RabbitMQ client constants."""

    def test_rabbitmq_pods_mapping(self) -> None:
        """Test pod name mappings."""
        assert RABBITMQ_PODS["main"] == "openstack-rabbitmq-rabbitmq-0"
        assert RABBITMQ_PODS["neutron"] == "openstack-neutron-rabbitmq-rabbitmq-0"

    def test_namespace(self) -> None:
        """Test default namespace."""
        assert RABBITMQ_NAMESPACE == "openstack"


class TestClusterStatusResult:
    """Tests for ClusterStatusResult dataclass."""

    def test_default_values(self) -> None:
        """Test default values."""
        result = ClusterStatusResult()
        assert result.cluster_name == ""
        assert result.running_nodes == []
        assert result.disk_nodes == []
        assert result.alarms == []
        assert result.partitions == []
        assert result.maintenance_status == "not under maintenance"
        assert result.listeners == []
        assert result.feature_flags == {}
        assert result.rabbitmq_version == ""
        assert result.erlang_version == ""
        assert result.cpu_cores == 0


class TestNodeStatusResult:
    """Tests for NodeStatusResult dataclass."""

    def test_default_values(self) -> None:
        """Test default values."""
        result = NodeStatusResult()
        assert result.memory_used_bytes == 0
        assert result.memory_limit_bytes == 0
        assert result.memory_percent == 0.0
        assert result.disk_free_bytes == 0
        assert result.has_memory_alarm is False
        assert result.has_disk_alarm is False


class TestQueueInfo:
    """Tests for QueueInfo dataclass."""

    def test_required_fields(self) -> None:
        """Test required fields."""
        queue = QueueInfo(name="test-queue", vhost="nova")
        assert queue.name == "test-queue"
        assert queue.vhost == "nova"

    def test_default_values(self) -> None:
        """Test default values."""
        queue = QueueInfo(name="test", vhost="/")
        assert queue.messages == 0
        assert queue.messages_ready == 0
        assert queue.messages_unacked == 0
        assert queue.consumers == 0
        assert queue.memory_bytes == 0
        assert queue.state == "running"


class TestConnectionInfo:
    """Tests for ConnectionInfo dataclass."""

    def test_required_fields(self) -> None:
        """Test required fields."""
        conn = ConnectionInfo(name="conn1", user="nova")
        assert conn.name == "conn1"
        assert conn.user == "nova"

    def test_default_values(self) -> None:
        """Test default values."""
        conn = ConnectionInfo(name="test", user="test")
        assert conn.state == "running"
        assert conn.channels == 0
        assert conn.client_host == ""
        assert conn.ssl is False
        assert conn.protocol == "AMQP 0-9-1"


class TestRabbitMQClientInit:
    """Tests for RabbitMQClient initialization."""

    def test_init_main_instance(self) -> None:
        """Test initialization with main instance."""
        mock_adapter = MagicMock()
        client = RabbitMQClient(mock_adapter, instance="main")

        assert client._instance == "main"
        assert client._pod_name == "openstack-rabbitmq-rabbitmq-0"
        assert client._namespace == RABBITMQ_NAMESPACE

    def test_init_neutron_instance(self) -> None:
        """Test initialization with neutron instance."""
        mock_adapter = MagicMock()
        client = RabbitMQClient(mock_adapter, instance="neutron")

        assert client._instance == "neutron"
        assert client._pod_name == "openstack-neutron-rabbitmq-rabbitmq-0"

    def test_init_custom_namespace(self) -> None:
        """Test initialization with custom namespace."""
        mock_adapter = MagicMock()
        client = RabbitMQClient(mock_adapter, namespace="custom-ns")

        assert client._namespace == "custom-ns"

    def test_properties(self) -> None:
        """Test property accessors."""
        mock_adapter = MagicMock()
        client = RabbitMQClient(mock_adapter, instance="main")

        assert client.instance == "main"
        assert client.pod_name == "openstack-rabbitmq-rabbitmq-0"


class TestRabbitMQClientContextManager:
    """Tests for RabbitMQClient context manager."""

    @pytest.mark.asyncio
    async def test_async_context_manager(self) -> None:
        """Test async context manager."""
        mock_adapter = MagicMock()

        async with RabbitMQClient(mock_adapter, instance="main") as client:
            assert client is not None
            assert isinstance(client, RabbitMQClient)


class TestRabbitMQClientParseClusterStatus:
    """Tests for cluster_status parsing."""

    def test_parse_cluster_name(self) -> None:
        """Test parsing cluster name."""
        output = "Cluster name: rabbit@openstack-rabbitmq-rabbitmq-0"
        mock_adapter = MagicMock()
        client = RabbitMQClient(mock_adapter)

        result = client._parse_cluster_status(output)
        assert result.cluster_name == "rabbit@openstack-rabbitmq-rabbitmq-0"

    def test_parse_running_nodes(self) -> None:
        """Test parsing running nodes."""
        output = """Running Nodes

rabbit@openstack-rabbitmq-rabbitmq-0
rabbit@openstack-rabbitmq-rabbitmq-1
rabbit@openstack-rabbitmq-rabbitmq-2

Disk Nodes
"""
        mock_adapter = MagicMock()
        client = RabbitMQClient(mock_adapter)

        result = client._parse_cluster_status(output)
        assert len(result.running_nodes) == 3
        assert "rabbit@openstack-rabbitmq-rabbitmq-0" in result.running_nodes

    def test_parse_disk_nodes(self) -> None:
        """Test parsing disk nodes."""
        output = """Disk Nodes

rabbit@openstack-rabbitmq-rabbitmq-0
rabbit@openstack-rabbitmq-rabbitmq-1

Running Nodes
"""
        mock_adapter = MagicMock()
        client = RabbitMQClient(mock_adapter)

        result = client._parse_cluster_status(output)
        assert len(result.disk_nodes) == 2

    def test_parse_alarms_none(self) -> None:
        """Test parsing no alarms."""
        output = """Alarms

(none)

"""
        mock_adapter = MagicMock()
        client = RabbitMQClient(mock_adapter)

        result = client._parse_cluster_status(output)
        assert result.alarms == []

    def test_parse_alarms_active(self) -> None:
        """Test parsing active alarms."""
        output = """Alarms

memory
disk

"""
        mock_adapter = MagicMock()
        client = RabbitMQClient(mock_adapter)

        result = client._parse_cluster_status(output)
        assert "memory" in result.alarms
        assert "disk" in result.alarms

    def test_parse_partitions_none(self) -> None:
        """Test parsing no partitions."""
        output = """Network Partitions

(none)

"""
        mock_adapter = MagicMock()
        client = RabbitMQClient(mock_adapter)

        result = client._parse_cluster_status(output)
        assert result.partitions == []

    def test_parse_maintenance_status(self) -> None:
        """Test parsing maintenance status."""
        output = "status: not under maintenance"
        mock_adapter = MagicMock()
        client = RabbitMQClient(mock_adapter)

        result = client._parse_cluster_status(output)
        assert result.maintenance_status == "not under maintenance"

    def test_parse_maintenance_under_maintenance(self) -> None:
        """Test parsing under maintenance."""
        output = "status: under maintenance"
        mock_adapter = MagicMock()
        client = RabbitMQClient(mock_adapter)

        result = client._parse_cluster_status(output)
        assert result.maintenance_status == "under maintenance"

    def test_parse_listeners(self) -> None:
        """Test parsing listeners."""
        output = """Listeners

port: 5672, protocol: amqp
port: 15672, protocol: http

"""
        mock_adapter = MagicMock()
        client = RabbitMQClient(mock_adapter)

        result = client._parse_cluster_status(output)
        assert "amqp:5672" in result.listeners
        assert "http:15672" in result.listeners

    def test_parse_versions(self) -> None:
        """Test parsing RabbitMQ and Erlang versions."""
        output = "RabbitMQ 3.12.10 on Erlang 25.3.2"
        mock_adapter = MagicMock()
        client = RabbitMQClient(mock_adapter)

        result = client._parse_cluster_status(output)
        assert result.rabbitmq_version == "3.12.10"
        assert result.erlang_version == "25.3.2"

    def test_parse_cpu_cores(self) -> None:
        """Test parsing CPU cores."""
        output = "available CPU cores: 8"
        mock_adapter = MagicMock()
        client = RabbitMQClient(mock_adapter)

        result = client._parse_cluster_status(output)
        assert result.cpu_cores == 8

    def test_parse_feature_flags(self) -> None:
        """Test parsing feature flags."""
        output = """Feature flags

Flag: stream_queue, state: enabled
Flag: quorum_queue, state: disabled

"""
        mock_adapter = MagicMock()
        client = RabbitMQClient(mock_adapter)

        result = client._parse_cluster_status(output)
        assert result.feature_flags.get("stream_queue") is True
        assert result.feature_flags.get("quorum_queue") is False


class TestRabbitMQClientParseNodeStatus:
    """Tests for node_status parsing."""

    def test_parse_memory_used(self) -> None:
        """Test parsing memory used."""
        output = "Total memory used: 512.5 MB"
        mock_adapter = MagicMock()
        client = RabbitMQClient(mock_adapter)

        result = client._parse_node_status(output)
        assert result.memory_used_bytes > 0

    def test_parse_memory_limit(self) -> None:
        """Test parsing memory limit."""
        output = "computed to: 2.0 GB"
        mock_adapter = MagicMock()
        client = RabbitMQClient(mock_adapter)

        result = client._parse_node_status(output)
        assert result.memory_limit_bytes > 0

    def test_parse_memory_percent(self) -> None:
        """Test parsing memory percentage."""
        output = """Total memory used: 512.0 MB
computed to: 1024.0 MB"""
        mock_adapter = MagicMock()
        client = RabbitMQClient(mock_adapter)

        result = client._parse_node_status(output)
        assert result.memory_percent == pytest.approx(50.0, rel=0.1)

    def test_parse_memory_alarm(self) -> None:
        """Test parsing memory alarm."""
        output = "memory alarm triggered"
        mock_adapter = MagicMock()
        client = RabbitMQClient(mock_adapter)

        result = client._parse_node_status(output)
        assert result.has_memory_alarm is True

    def test_parse_disk_alarm(self) -> None:
        """Test parsing disk alarm."""
        output = "disk alarm triggered"
        mock_adapter = MagicMock()
        client = RabbitMQClient(mock_adapter)

        result = client._parse_node_status(output)
        assert result.has_disk_alarm is True


class TestRabbitMQClientConvertToBytes:
    """Tests for byte conversion."""

    def test_convert_bytes(self) -> None:
        """Test converting bytes."""
        assert RabbitMQClient._convert_to_bytes(100, "b") == 100
        assert RabbitMQClient._convert_to_bytes(100, "bytes") == 100

    def test_convert_kilobytes(self) -> None:
        """Test converting kilobytes."""
        assert RabbitMQClient._convert_to_bytes(1, "kb") == 1024

    def test_convert_megabytes(self) -> None:
        """Test converting megabytes."""
        assert RabbitMQClient._convert_to_bytes(1, "mb") == 1024**2

    def test_convert_gigabytes(self) -> None:
        """Test converting gigabytes."""
        assert RabbitMQClient._convert_to_bytes(1, "gb") == 1024**3

    def test_convert_terabytes(self) -> None:
        """Test converting terabytes."""
        assert RabbitMQClient._convert_to_bytes(1, "tb") == 1024**4

    def test_convert_unknown_unit(self) -> None:
        """Test converting unknown unit."""
        assert RabbitMQClient._convert_to_bytes(100, "unknown") == 100


class TestRabbitMQClientInferService:
    """Tests for service inference from username."""

    def test_infer_nova(self) -> None:
        """Test inferring nova service."""
        assert RabbitMQClient.infer_service_from_user("nova") == "nova"
        assert RabbitMQClient.infer_service_from_user("NOVA_USER") == "nova"
        assert RabbitMQClient.infer_service_from_user("openstack-nova") == "nova"

    def test_infer_neutron(self) -> None:
        """Test inferring neutron service."""
        assert RabbitMQClient.infer_service_from_user("neutron") == "neutron"
        assert RabbitMQClient.infer_service_from_user("neutron-user") == "neutron"

    def test_infer_cinder(self) -> None:
        """Test inferring cinder service."""
        assert RabbitMQClient.infer_service_from_user("cinder") == "cinder"

    def test_infer_glance(self) -> None:
        """Test inferring glance service."""
        assert RabbitMQClient.infer_service_from_user("glance") == "glance"

    def test_infer_keystone(self) -> None:
        """Test inferring keystone service."""
        assert RabbitMQClient.infer_service_from_user("keystone") == "keystone"

    def test_infer_heat(self) -> None:
        """Test inferring heat service."""
        assert RabbitMQClient.infer_service_from_user("heat") == "heat"

    def test_infer_octavia(self) -> None:
        """Test inferring octavia service."""
        assert RabbitMQClient.infer_service_from_user("octavia") == "octavia"

    def test_infer_unknown(self) -> None:
        """Test inferring unknown service."""
        assert RabbitMQClient.infer_service_from_user("admin") == ""
        assert RabbitMQClient.infer_service_from_user("test") == ""


class TestRabbitMQClientParseQueues:
    """Tests for queue parsing."""

    def test_parse_queues_basic(self) -> None:
        """Test parsing basic queue output."""
        output = "queue1\t10\t8\t2\t1\t1024\trunning"
        columns = [
            "name",
            "messages",
            "messages_ready",
            "messages_unacknowledged",
            "consumers",
            "memory",
            "state",
        ]
        mock_adapter = MagicMock()
        client = RabbitMQClient(mock_adapter)

        queues = client._parse_queues(output, "nova", columns)
        assert len(queues) == 1
        assert queues[0].name == "queue1"
        assert queues[0].messages == 10
        assert queues[0].consumers == 1

    def test_parse_queues_skip_headers(self) -> None:
        """Test skipping header lines."""
        output = """Listing queues...
queue1\t10\t8\t2\t1\t1024\trunning
Timeout"""
        columns = [
            "name",
            "messages",
            "messages_ready",
            "messages_unacknowledged",
            "consumers",
            "memory",
            "state",
        ]
        mock_adapter = MagicMock()
        client = RabbitMQClient(mock_adapter)

        queues = client._parse_queues(output, "/", columns)
        assert len(queues) == 1

    def test_parse_queues_empty_output(self) -> None:
        """Test parsing empty output."""
        output = ""
        columns = ["name", "messages"]
        mock_adapter = MagicMock()
        client = RabbitMQClient(mock_adapter)

        queues = client._parse_queues(output, "/", columns)
        assert queues == []


class TestRabbitMQClientParseConnections:
    """Tests for connection parsing."""

    def test_parse_connections_basic(self) -> None:
        """Test parsing basic connection output."""
        output = (
            "192.168.1.10:5672 -> 10.0.0.1:35123\tnova\trunning\t5\t192.168.1.10\tfalse\tAMQP 0-9-1"
        )
        columns = ["name", "user", "state", "channels", "peer_host", "ssl", "protocol"]
        mock_adapter = MagicMock()
        client = RabbitMQClient(mock_adapter)

        connections = client._parse_connections(output, columns)
        assert len(connections) == 1
        assert connections[0].user == "nova"
        assert connections[0].channels == 5

    def test_parse_connections_skip_listing(self) -> None:
        """Test skipping listing header."""
        output = """Listing connections...
conn1\tnova\trunning\t1\t10.0.0.1\tfalse\tAMQP 0-9-1"""
        columns = ["name", "user", "state", "channels", "peer_host", "ssl", "protocol"]
        mock_adapter = MagicMock()
        client = RabbitMQClient(mock_adapter)

        connections = client._parse_connections(output, columns)
        assert len(connections) == 1

    def test_parse_connections_empty(self) -> None:
        """Test parsing empty output."""
        output = ""
        columns = ["name", "user"]
        mock_adapter = MagicMock()
        client = RabbitMQClient(mock_adapter)

        connections = client._parse_connections(output, columns)
        assert connections == []


class TestRabbitMQClientMethods:
    """Tests for RabbitMQClient methods with mocked execution."""

    @pytest.fixture
    def mock_adapter(self) -> AsyncMock:
        """Create mock adapter."""
        return AsyncMock()

    @pytest.mark.asyncio
    async def test_get_cluster_status(self, mock_adapter: AsyncMock) -> None:
        """Test get_cluster_status method."""
        client = RabbitMQClient(mock_adapter)

        # Mock the pod execution
        exec_result = PodExecResult(
            return_code=0,
            success=True,
            stdout="""Cluster name: rabbit@cluster
Running Nodes

rabbit@node1

""",
            stderr="",
        )

        with pytest.MonkeyPatch().context() as m:
            m.setattr(
                "mosk_mcp.tools.messaging_operations.rabbitmq_client.execute_in_pod",
                AsyncMock(return_value=exec_result),
            )
            result = await client.get_cluster_status()
            assert result.cluster_name == "rabbit@cluster"

    @pytest.mark.asyncio
    async def test_get_node_status(self, mock_adapter: AsyncMock) -> None:
        """Test get_node_status method."""
        client = RabbitMQClient(mock_adapter)

        exec_result = PodExecResult(
            return_code=0,
            success=True,
            stdout="Total memory used: 512.0 MB\ncomputed to: 1024.0 MB",
            stderr="",
        )

        with pytest.MonkeyPatch().context() as m:
            m.setattr(
                "mosk_mcp.tools.messaging_operations.rabbitmq_client.execute_in_pod",
                AsyncMock(return_value=exec_result),
            )
            result = await client.get_node_status()
            assert result.memory_percent == pytest.approx(50.0, rel=0.1)

    @pytest.mark.asyncio
    async def test_list_vhosts(self, mock_adapter: AsyncMock) -> None:
        """Test list_vhosts method."""
        client = RabbitMQClient(mock_adapter)

        exec_result = PodExecResult(
            return_code=0,
            success=True,
            stdout="nova\nneutron\ncinder",
            stderr="",
        )

        with pytest.MonkeyPatch().context() as m:
            m.setattr(
                "mosk_mcp.tools.messaging_operations.rabbitmq_client.execute_in_pod",
                AsyncMock(return_value=exec_result),
            )
            result = await client.list_vhosts()
            assert "nova" in result
            assert "neutron" in result
            assert "cinder" in result

    @pytest.mark.asyncio
    async def test_list_queues(self, mock_adapter: AsyncMock) -> None:
        """Test list_queues method."""
        client = RabbitMQClient(mock_adapter)

        exec_result = PodExecResult(
            return_code=0,
            success=True,
            stdout="queue1\t10\t8\t2\t1\t1024\trunning",
            stderr="",
        )

        with pytest.MonkeyPatch().context() as m:
            m.setattr(
                "mosk_mcp.tools.messaging_operations.rabbitmq_client.execute_in_pod",
                AsyncMock(return_value=exec_result),
            )
            result = await client.list_queues(vhost="nova")
            assert len(result) == 1
            assert result[0].name == "queue1"

    @pytest.mark.asyncio
    async def test_list_connections(self, mock_adapter: AsyncMock) -> None:
        """Test list_connections method."""
        client = RabbitMQClient(mock_adapter)

        exec_result = PodExecResult(
            return_code=0,
            success=True,
            stdout="conn1\tnova\trunning\t5\t10.0.0.1\tfalse\tAMQP 0-9-1",
            stderr="",
        )

        with pytest.MonkeyPatch().context() as m:
            m.setattr(
                "mosk_mcp.tools.messaging_operations.rabbitmq_client.execute_in_pod",
                AsyncMock(return_value=exec_result),
            )
            result = await client.list_connections()
            assert len(result) == 1
            assert result[0].user == "nova"

    @pytest.mark.asyncio
    async def test_list_channels(self, mock_adapter: AsyncMock) -> None:
        """Test list_channels method."""
        client = RabbitMQClient(mock_adapter)

        exec_result = PodExecResult(
            return_code=0,
            success=True,
            stdout="conn1\tchan1\t5\t2",
            stderr="",
        )

        with pytest.MonkeyPatch().context() as m:
            m.setattr(
                "mosk_mcp.tools.messaging_operations.rabbitmq_client.execute_in_pod",
                AsyncMock(return_value=exec_result),
            )
            result = await client.list_channels()
            assert len(result) == 1
            assert result[0]["connection"] == "conn1"
            assert result[0]["consumer_count"] == 5
