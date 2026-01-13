"""Unit tests for get_node_conditions tool."""

from unittest.mock import AsyncMock

import pytest

from mosk_mcp.core.exceptions import ResourceNotFoundError, ToolExecutionError
from mosk_mcp.tools.common.enums import HealthStatus
from mosk_mcp.tools.operations_visibility.get_node_conditions import (
    _check_node_issues,
    _determine_health_summary,
    _determine_node_role,
    _parse_condition,
    _parse_node,
    _parse_taint,
    get_node_conditions,
)
from mosk_mcp.tools.operations_visibility.models import (
    Condition,
    ConditionStatus,
    GetNodeConditionsInput,
    GetNodeConditionsOutput,
    NodeConditionInfo,
    NodeTaint,
)


# =============================================================================
# Tests for helper functions
# =============================================================================


class TestParseCondition:
    """Tests for _parse_condition helper function."""

    def test_parse_condition_true(self) -> None:
        """Test parsing condition with True status."""
        cond_data = {
            "type": "Ready",
            "status": "True",
            "reason": "KubeletReady",
            "message": "kubelet is posting ready status",
            "lastTransitionTime": "2024-01-01T00:00:00Z",
            "lastHeartbeatTime": "2024-01-01T12:00:00Z",
        }

        result = _parse_condition(cond_data)

        assert result.type == "Ready"
        assert result.status == ConditionStatus.TRUE
        assert result.reason == "KubeletReady"
        assert result.message == "kubelet is posting ready status"
        assert result.last_transition_time == "2024-01-01T00:00:00Z"
        assert result.last_update_time == "2024-01-01T12:00:00Z"

    def test_parse_condition_false(self) -> None:
        """Test parsing condition with False status."""
        cond_data = {
            "type": "MemoryPressure",
            "status": "False",
            "reason": "KubeletHasSufficientMemory",
            "message": "kubelet has sufficient memory",
        }

        result = _parse_condition(cond_data)

        assert result.type == "MemoryPressure"
        assert result.status == ConditionStatus.FALSE

    def test_parse_condition_unknown_status(self) -> None:
        """Test parsing condition with unknown status."""
        cond_data = {
            "type": "Ready",
            "status": "InvalidStatus",
        }

        result = _parse_condition(cond_data)

        assert result.status == ConditionStatus.UNKNOWN

    def test_parse_condition_missing_fields(self) -> None:
        """Test parsing condition with missing fields."""
        cond_data = {}

        result = _parse_condition(cond_data)

        assert result.type == "Unknown"
        assert result.status == ConditionStatus.UNKNOWN
        assert result.reason is None
        assert result.message is None


class TestParseTaint:
    """Tests for _parse_taint helper function."""

    def test_parse_taint_complete(self) -> None:
        """Test parsing complete taint."""
        taint_data = {
            "key": "node.kubernetes.io/disk-pressure",
            "value": "true",
            "effect": "NoSchedule",
        }

        result = _parse_taint(taint_data)

        assert result.key == "node.kubernetes.io/disk-pressure"
        assert result.value == "true"
        assert result.effect == "NoSchedule"

    def test_parse_taint_no_value(self) -> None:
        """Test parsing taint without value."""
        taint_data = {
            "key": "node.kubernetes.io/unschedulable",
            "effect": "NoExecute",
        }

        result = _parse_taint(taint_data)

        assert result.key == "node.kubernetes.io/unschedulable"
        assert result.value is None
        assert result.effect == "NoExecute"

    def test_parse_taint_missing_fields(self) -> None:
        """Test parsing taint with missing fields."""
        taint_data = {}

        result = _parse_taint(taint_data)

        assert result.key == ""
        assert result.effect == "NoSchedule"


class TestDetermineNodeRole:
    """Tests for _determine_node_role helper function."""

    def test_standard_role_labels(self) -> None:
        """Test standard Kubernetes role labels."""
        labels = {
            "node-role.kubernetes.io/control-plane": "",
            "node-role.kubernetes.io/master": "",
        }

        result = _determine_node_role(labels)

        assert "control-plane" in result
        assert "master" in result

    def test_mosk_control_plane(self) -> None:
        """Test MOSK control plane labels."""
        labels = {"openstack-control-plane": "enabled"}

        result = _determine_node_role(labels)

        assert "control" in result

    def test_mosk_compute_node(self) -> None:
        """Test MOSK compute node labels."""
        labels = {"openstack-compute-node": "enabled"}

        result = _determine_node_role(labels)

        assert "compute" in result

    def test_mosk_gateway_node(self) -> None:
        """Test MOSK gateway node labels."""
        labels = {"openstack-gateway": "enabled"}

        result = _determine_node_role(labels)

        assert "gateway" in result

    def test_mosk_storage_node(self) -> None:
        """Test MOSK storage node labels."""
        labels = {"ceph-osd-node": "enabled"}

        result = _determine_node_role(labels)

        assert "storage" in result

    def test_hostlabel_controlplane(self) -> None:
        """Test hostlabel.bm.kaas.mirantis.com/controlplane label."""
        labels = {"hostlabel.bm.kaas.mirantis.com/controlplane": "controlplane"}

        result = _determine_node_role(labels)

        assert "control" in result

    def test_hostlabel_worker(self) -> None:
        """Test hostlabel.bm.kaas.mirantis.com/worker label."""
        labels = {"hostlabel.bm.kaas.mirantis.com/worker": "worker"}

        result = _determine_node_role(labels)

        assert "compute" in result

    def test_multiple_roles(self) -> None:
        """Test node with multiple roles."""
        labels = {
            "openstack-control-plane": "enabled",
            "ceph-osd-node": "enabled",
        }

        result = _determine_node_role(labels)

        assert "control" in result
        assert "storage" in result

    def test_no_role_labels(self) -> None:
        """Test node with no role labels defaults to worker."""
        labels = {"some-other-label": "value"}

        result = _determine_node_role(labels)

        assert result == "worker"


class TestCheckNodeIssues:
    """Tests for _check_node_issues helper function."""

    def test_no_issues(self) -> None:
        """Test node with no issues."""
        conditions = [
            Condition(type="Ready", status=ConditionStatus.TRUE),
            Condition(type="MemoryPressure", status=ConditionStatus.FALSE),
        ]
        taints = []

        result = _check_node_issues(conditions, taints, is_schedulable=True)

        assert result == []

    def test_not_ready_condition(self) -> None:
        """Test node not ready condition."""
        conditions = [
            Condition(
                type="Ready",
                status=ConditionStatus.FALSE,
                message="Kubelet not ready",
            ),
        ]

        result = _check_node_issues(conditions, [], is_schedulable=True)

        assert len(result) == 1
        assert "not Ready" in result[0]

    def test_memory_pressure_condition(self) -> None:
        """Test memory pressure condition."""
        conditions = [
            Condition(type="Ready", status=ConditionStatus.TRUE),
            Condition(
                type="MemoryPressure",
                status=ConditionStatus.TRUE,
                message="low memory",
            ),
        ]

        result = _check_node_issues(conditions, [], is_schedulable=True)

        assert len(result) == 1
        assert "MemoryPressure" in result[0]

    def test_disk_pressure_condition(self) -> None:
        """Test disk pressure condition."""
        conditions = [
            Condition(
                type="DiskPressure",
                status=ConditionStatus.TRUE,
                reason="DiskPressure",
            ),
        ]

        result = _check_node_issues(conditions, [], is_schedulable=True)

        assert len(result) == 1
        assert "DiskPressure" in result[0]

    def test_cordoned_taint(self) -> None:
        """Test cordoned node taint."""
        taints = [NodeTaint(key="node.kubernetes.io/unschedulable", effect="NoSchedule")]

        result = _check_node_issues([], taints, is_schedulable=False)

        assert "cordoned" in result[0]

    def test_unreachable_taint(self) -> None:
        """Test unreachable node taint."""
        taints = [NodeTaint(key="node.kubernetes.io/unreachable", effect="NoExecute")]

        result = _check_node_issues([], taints, is_schedulable=True)

        assert any("unreachable" in issue for issue in result)

    def test_not_ready_taint(self) -> None:
        """Test not-ready node taint."""
        taints = [NodeTaint(key="node.kubernetes.io/not-ready", effect="NoSchedule")]

        result = _check_node_issues([], taints, is_schedulable=True)

        assert any("not-ready" in issue for issue in result)

    def test_disk_pressure_taint(self) -> None:
        """Test disk pressure taint."""
        taints = [NodeTaint(key="node.kubernetes.io/disk-pressure", effect="NoSchedule")]

        result = _check_node_issues([], taints, is_schedulable=True)

        assert any("disk pressure" in issue for issue in result)

    def test_memory_pressure_taint(self) -> None:
        """Test memory pressure taint."""
        taints = [NodeTaint(key="node.kubernetes.io/memory-pressure", effect="NoSchedule")]

        result = _check_node_issues([], taints, is_schedulable=True)

        assert any("memory pressure" in issue for issue in result)

    def test_unschedulable_not_cordoned(self) -> None:
        """Test unschedulable node not via cordoning."""
        result = _check_node_issues([], [], is_schedulable=False)

        assert any("unschedulable" in issue for issue in result)


class TestDetermineHealthSummary:
    """Tests for _determine_health_summary helper function."""

    def test_healthy_node(self) -> None:
        """Test healthy node summary."""
        result = _determine_health_summary(
            is_ready=True,
            is_schedulable=True,
            issues=[],
        )

        assert "Healthy" in result

    def test_not_ready_node(self) -> None:
        """Test not ready node summary."""
        result = _determine_health_summary(
            is_ready=False,
            is_schedulable=True,
            issues=[],
        )

        assert "Not Ready" in result

    def test_cordoned_node(self) -> None:
        """Test cordoned node summary."""
        result = _determine_health_summary(
            is_ready=True,
            is_schedulable=False,
            issues=[],
        )

        assert "cordoned" in result or "unschedulable" in result

    def test_node_with_issues(self) -> None:
        """Test node with issues summary."""
        result = _determine_health_summary(
            is_ready=True,
            is_schedulable=True,
            issues=["MemoryPressure detected"],
        )

        assert "issue" in result


class TestParseNode:
    """Tests for _parse_node helper function."""

    def test_parse_healthy_node(self) -> None:
        """Test parsing healthy node."""
        node = {
            "metadata": {
                "name": "compute-01",
                "labels": {"openstack-compute-node": "enabled"},
            },
            "spec": {"unschedulable": False},
            "status": {
                "conditions": [
                    {"type": "Ready", "status": "True"},
                    {"type": "MemoryPressure", "status": "False"},
                ],
                "nodeInfo": {
                    "kubeletVersion": "v1.28.5",
                    "containerRuntimeVersion": "containerd://1.7.0",
                    "osImage": "Ubuntu 22.04",
                    "kernelVersion": "5.15.0",
                },
                "capacity": {
                    "cpu": "32",
                    "memory": "128Gi",
                    "pods": "110",
                },
            },
        }

        result = _parse_node(node, include_labels=False)

        assert result.node_name == "compute-01"
        assert "compute" in result.node_role
        assert result.is_ready is True
        assert result.is_schedulable is True
        assert result.kubelet_version == "v1.28.5"
        assert result.cpu_capacity == "32"
        assert len(result.labels) == 0

    def test_parse_node_with_labels(self) -> None:
        """Test parsing node with labels included."""
        node = {
            "metadata": {
                "name": "control-01",
                "labels": {"openstack-control-plane": "enabled", "env": "prod"},
            },
            "spec": {},
            "status": {
                "conditions": [{"type": "Ready", "status": "True"}],
                "nodeInfo": {},
                "capacity": {},
            },
        }

        result = _parse_node(node, include_labels=True)

        assert len(result.labels) == 2
        assert result.labels["env"] == "prod"

    def test_parse_cordoned_node(self) -> None:
        """Test parsing cordoned node."""
        node = {
            "metadata": {"name": "node-01"},
            "spec": {"unschedulable": True},
            "status": {
                "conditions": [{"type": "Ready", "status": "True"}],
                "nodeInfo": {},
                "capacity": {},
            },
        }

        result = _parse_node(node, include_labels=False)

        assert result.is_schedulable is False

    def test_parse_node_with_taints(self) -> None:
        """Test parsing node with taints."""
        node = {
            "metadata": {"name": "node-01"},
            "spec": {
                "taints": [
                    {"key": "node.kubernetes.io/disk-pressure", "effect": "NoSchedule"},
                ],
            },
            "status": {
                "conditions": [{"type": "Ready", "status": "True"}],
                "nodeInfo": {},
                "capacity": {},
            },
        }

        result = _parse_node(node, include_labels=False)

        assert len(result.taints) == 1
        assert result.taints[0].key == "node.kubernetes.io/disk-pressure"


# =============================================================================
# Tests for model validation
# =============================================================================


class TestGetNodeConditionsInput:
    """Tests for GetNodeConditionsInput model."""

    def test_defaults(self) -> None:
        """Test default values."""
        input_data = GetNodeConditionsInput()

        assert input_data.node_name is None
        assert input_data.only_unhealthy is False
        assert input_data.include_taints is True
        assert input_data.include_labels is False

    def test_custom_values(self) -> None:
        """Test custom values."""
        input_data = GetNodeConditionsInput(
            node_name="compute-01",
            only_unhealthy=True,
            include_labels=True,
        )

        assert input_data.node_name == "compute-01"
        assert input_data.only_unhealthy is True
        assert input_data.include_labels is True


class TestNodeConditionInfo:
    """Tests for NodeConditionInfo model."""

    def test_creation(self) -> None:
        """Test model creation."""
        info = NodeConditionInfo(
            node_name="compute-01",
            node_role="compute",
            is_ready=True,
            is_schedulable=True,
            conditions=[],
            taints=[],
            labels={},
            health_summary="Healthy",
            issues=[],
            kubelet_version="v1.28.5",
            container_runtime="containerd://1.7.0",
            os_image="Ubuntu 22.04",
            kernel_version="5.15.0",
            cpu_capacity="32",
            memory_capacity="128Gi",
            pods_capacity=110,
            pods_running=50,
        )

        assert info.node_name == "compute-01"
        assert info.is_ready is True


class TestGetNodeConditionsOutput:
    """Tests for GetNodeConditionsOutput model."""

    def test_creation(self) -> None:
        """Test model creation."""
        output = GetNodeConditionsOutput(
            nodes=[],
            total_nodes=10,
            ready_nodes=9,
            not_ready_nodes=1,
            cordoned_nodes=0,
            nodes_with_issues=["node-03"],
            cluster_health=HealthStatus.DEGRADED,
            recommendations=["Fix node-03"],
            timestamp="2024-01-01T00:00:00Z",
        )

        assert output.total_nodes == 10
        assert output.ready_nodes == 9
        assert output.cluster_health == HealthStatus.DEGRADED


# =============================================================================
# Tests for get_node_conditions function
# =============================================================================


class TestGetNodeConditions:
    """Tests for get_node_conditions function."""

    @pytest.fixture
    def mock_adapter(self) -> AsyncMock:
        """Create mock Kubernetes adapter."""
        adapter = AsyncMock()
        adapter.get = AsyncMock()
        adapter.list = AsyncMock()
        return adapter

    @pytest.fixture
    def mock_healthy_nodes(self) -> list[dict]:
        """Create mock healthy nodes."""
        return [
            {
                "metadata": {"name": "node-01", "labels": {}},
                "spec": {},
                "status": {
                    "conditions": [{"type": "Ready", "status": "True"}],
                    "nodeInfo": {"kubeletVersion": "v1.28.5"},
                    "capacity": {"cpu": "16", "memory": "64Gi", "pods": "110"},
                },
            },
            {
                "metadata": {"name": "node-02", "labels": {}},
                "spec": {},
                "status": {
                    "conditions": [{"type": "Ready", "status": "True"}],
                    "nodeInfo": {"kubeletVersion": "v1.28.5"},
                    "capacity": {"cpu": "16", "memory": "64Gi", "pods": "110"},
                },
            },
        ]

    @pytest.mark.asyncio
    async def test_get_all_nodes_healthy(
        self, mock_adapter: AsyncMock, mock_healthy_nodes: list[dict]
    ) -> None:
        """Test getting all healthy nodes."""
        mock_adapter.list.return_value = mock_healthy_nodes

        result = await get_node_conditions(
            mock_adapter,
            GetNodeConditionsInput(),
        )

        assert result.total_nodes == 2
        assert result.ready_nodes == 2
        assert result.not_ready_nodes == 0
        assert result.cluster_health == HealthStatus.HEALTHY
        assert len(result.nodes) == 2

    @pytest.mark.asyncio
    async def test_get_specific_node(self, mock_adapter: AsyncMock) -> None:
        """Test getting specific node."""
        mock_adapter.get.return_value = {
            "metadata": {"name": "node-01", "labels": {}},
            "spec": {},
            "status": {
                "conditions": [{"type": "Ready", "status": "True"}],
                "nodeInfo": {},
                "capacity": {"pods": "110"},
            },
        }
        mock_adapter.list.return_value = []

        result = await get_node_conditions(
            mock_adapter,
            GetNodeConditionsInput(node_name="node-01"),
        )

        assert result.total_nodes == 1
        assert result.nodes[0].node_name == "node-01"
        mock_adapter.get.assert_called_once()

    @pytest.mark.asyncio
    async def test_node_not_found(self, mock_adapter: AsyncMock) -> None:
        """Test when specific node is not found."""
        mock_adapter.get.side_effect = ResourceNotFoundError(
            message="Node not found",
            resource_type="Node",
            resource_id="nonexistent",
        )

        with pytest.raises(ResourceNotFoundError):
            await get_node_conditions(
                mock_adapter,
                GetNodeConditionsInput(node_name="nonexistent"),
            )

    @pytest.mark.asyncio
    async def test_only_unhealthy_filter(self, mock_adapter: AsyncMock) -> None:
        """Test only_unhealthy filter."""
        mock_adapter.list.return_value = [
            {
                "metadata": {"name": "node-01", "labels": {}},
                "spec": {},
                "status": {
                    "conditions": [{"type": "Ready", "status": "True"}],
                    "nodeInfo": {},
                    "capacity": {"pods": "110"},
                },
            },
            {
                "metadata": {"name": "node-02", "labels": {}},
                "spec": {},
                "status": {
                    "conditions": [{"type": "Ready", "status": "False"}],
                    "nodeInfo": {},
                    "capacity": {"pods": "110"},
                },
            },
        ]

        result = await get_node_conditions(
            mock_adapter,
            GetNodeConditionsInput(only_unhealthy=True),
        )

        # Only the not-ready node should be returned
        assert result.total_nodes == 1
        assert result.nodes[0].node_name == "node-02"

    @pytest.mark.asyncio
    async def test_degraded_cluster(self, mock_adapter: AsyncMock) -> None:
        """Test degraded cluster health detection."""
        mock_adapter.list.return_value = [
            {
                "metadata": {"name": "node-01", "labels": {}},
                "spec": {},
                "status": {
                    "conditions": [{"type": "Ready", "status": "True"}],
                    "nodeInfo": {},
                    "capacity": {"pods": "110"},
                },
            },
            {
                "metadata": {"name": "node-02", "labels": {}},
                "spec": {},
                "status": {
                    "conditions": [
                        {"type": "Ready", "status": "False", "message": "kubelet stopped"},
                    ],
                    "nodeInfo": {},
                    "capacity": {"pods": "110"},
                },
            },
            {
                "metadata": {"name": "node-03", "labels": {}},
                "spec": {},
                "status": {
                    "conditions": [{"type": "Ready", "status": "True"}],
                    "nodeInfo": {},
                    "capacity": {"pods": "110"},
                },
            },
        ]

        result = await get_node_conditions(
            mock_adapter,
            GetNodeConditionsInput(),
        )

        assert result.not_ready_nodes == 1
        assert result.cluster_health == HealthStatus.DEGRADED

    @pytest.mark.asyncio
    async def test_unhealthy_cluster(self, mock_adapter: AsyncMock) -> None:
        """Test unhealthy cluster (majority not ready)."""
        mock_adapter.list.return_value = [
            {
                "metadata": {"name": "node-01", "labels": {}},
                "spec": {},
                "status": {
                    "conditions": [{"type": "Ready", "status": "False"}],
                    "nodeInfo": {},
                    "capacity": {"pods": "110"},
                },
            },
            {
                "metadata": {"name": "node-02", "labels": {}},
                "spec": {},
                "status": {
                    "conditions": [{"type": "Ready", "status": "False"}],
                    "nodeInfo": {},
                    "capacity": {"pods": "110"},
                },
            },
            {
                "metadata": {"name": "node-03", "labels": {}},
                "spec": {},
                "status": {
                    "conditions": [{"type": "Ready", "status": "True"}],
                    "nodeInfo": {},
                    "capacity": {"pods": "110"},
                },
            },
        ]

        result = await get_node_conditions(
            mock_adapter,
            GetNodeConditionsInput(),
        )

        assert result.not_ready_nodes == 2
        assert result.cluster_health == HealthStatus.UNHEALTHY

    @pytest.mark.asyncio
    async def test_cordoned_nodes_counted(self, mock_adapter: AsyncMock) -> None:
        """Test cordoned nodes are counted."""
        mock_adapter.list.return_value = [
            {
                "metadata": {"name": "node-01", "labels": {}},
                "spec": {"unschedulable": True},
                "status": {
                    "conditions": [{"type": "Ready", "status": "True"}],
                    "nodeInfo": {},
                    "capacity": {"pods": "110"},
                },
            },
        ]

        result = await get_node_conditions(
            mock_adapter,
            GetNodeConditionsInput(),
        )

        assert result.cordoned_nodes == 1

    @pytest.mark.asyncio
    async def test_recommendations_for_not_ready(self, mock_adapter: AsyncMock) -> None:
        """Test recommendations for not ready nodes."""
        mock_adapter.list.return_value = [
            {
                "metadata": {"name": "node-01", "labels": {}},
                "spec": {},
                "status": {
                    "conditions": [{"type": "Ready", "status": "False"}],
                    "nodeInfo": {},
                    "capacity": {"pods": "110"},
                },
            },
        ]

        result = await get_node_conditions(
            mock_adapter,
            GetNodeConditionsInput(),
        )

        assert any("not ready" in r.lower() for r in result.recommendations)

    @pytest.mark.asyncio
    async def test_recommendations_for_cordoned(self, mock_adapter: AsyncMock) -> None:
        """Test recommendations for cordoned nodes."""
        mock_adapter.list.return_value = [
            {
                "metadata": {"name": "node-01", "labels": {}},
                "spec": {"unschedulable": True},
                "status": {
                    "conditions": [{"type": "Ready", "status": "True"}],
                    "nodeInfo": {},
                    "capacity": {"pods": "110"},
                },
            },
        ]

        result = await get_node_conditions(
            mock_adapter,
            GetNodeConditionsInput(),
        )

        assert any("cordoned" in r.lower() for r in result.recommendations)

    @pytest.mark.asyncio
    async def test_recommendations_for_memory_pressure(self, mock_adapter: AsyncMock) -> None:
        """Test recommendations for memory pressure."""
        mock_adapter.list.return_value = [
            {
                "metadata": {"name": "node-01", "labels": {}},
                "spec": {},
                "status": {
                    "conditions": [
                        {"type": "Ready", "status": "True"},
                        {"type": "MemoryPressure", "status": "True"},
                    ],
                    "nodeInfo": {},
                    "capacity": {"pods": "110"},
                },
            },
        ]

        result = await get_node_conditions(
            mock_adapter,
            GetNodeConditionsInput(),
        )

        assert any("memory pressure" in r.lower() for r in result.recommendations)

    @pytest.mark.asyncio
    async def test_recommendations_for_disk_pressure(self, mock_adapter: AsyncMock) -> None:
        """Test recommendations for disk pressure."""
        mock_adapter.list.return_value = [
            {
                "metadata": {"name": "node-01", "labels": {}},
                "spec": {},
                "status": {
                    "conditions": [
                        {"type": "Ready", "status": "True"},
                        {"type": "DiskPressure", "status": "True"},
                    ],
                    "nodeInfo": {},
                    "capacity": {"pods": "110"},
                },
            },
        ]

        result = await get_node_conditions(
            mock_adapter,
            GetNodeConditionsInput(),
        )

        assert any("disk pressure" in r.lower() for r in result.recommendations)

    @pytest.mark.asyncio
    async def test_pods_counted_per_node(self, mock_adapter: AsyncMock) -> None:
        """Test running pods are counted per node."""
        mock_adapter.list.side_effect = [
            [
                {
                    "metadata": {"name": "node-01", "labels": {}},
                    "spec": {},
                    "status": {
                        "conditions": [{"type": "Ready", "status": "True"}],
                        "nodeInfo": {},
                        "capacity": {"pods": "110"},
                    },
                },
            ],
            [
                {"spec": {"nodeName": "node-01"}, "status": {"phase": "Running"}},
                {"spec": {"nodeName": "node-01"}, "status": {"phase": "Running"}},
                {"spec": {"nodeName": "node-01"}, "status": {"phase": "Pending"}},
            ],
        ]

        result = await get_node_conditions(
            mock_adapter,
            GetNodeConditionsInput(),
        )

        # 2 running pods on node-01
        assert result.nodes[0].pods_running == 2

    @pytest.mark.asyncio
    async def test_api_error_handling(self, mock_adapter: AsyncMock) -> None:
        """Test API error handling."""
        mock_adapter.list.side_effect = Exception("API connection failed")

        with pytest.raises(ToolExecutionError) as exc_info:
            await get_node_conditions(
                mock_adapter,
                GetNodeConditionsInput(),
            )

        assert "Failed to get node conditions" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_pod_count_failure_graceful(self, mock_adapter: AsyncMock) -> None:
        """Test graceful handling when pod count fails."""
        # First call returns nodes, second call (pods) fails
        mock_adapter.list.side_effect = [
            [
                {
                    "metadata": {"name": "node-01", "labels": {}},
                    "spec": {},
                    "status": {
                        "conditions": [{"type": "Ready", "status": "True"}],
                        "nodeInfo": {},
                        "capacity": {"pods": "110"},
                    },
                },
            ],
            Exception("Failed to list pods"),
        ]

        # Should succeed even if pod listing fails
        result = await get_node_conditions(
            mock_adapter,
            GetNodeConditionsInput(),
        )

        assert result.total_nodes == 1
        assert result.nodes[0].pods_running == 0

    @pytest.mark.asyncio
    async def test_timestamp_included(self, mock_adapter: AsyncMock) -> None:
        """Test timestamp is included in output."""
        mock_adapter.list.return_value = []

        result = await get_node_conditions(
            mock_adapter,
            GetNodeConditionsInput(),
        )

        assert result.timestamp is not None
        assert len(result.timestamp) > 0
