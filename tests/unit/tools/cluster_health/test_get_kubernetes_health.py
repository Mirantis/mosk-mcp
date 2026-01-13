"""Unit tests for get_kubernetes_health tool."""

from unittest.mock import AsyncMock

import pytest

from mosk_mcp.tools.cluster_health.get_kubernetes_health import (
    _analyze_system_pods,
    _calculate_kubernetes_score,
    _extract_node_health,
    _generate_recommendations,
    _parse_node_conditions,
    get_kubernetes_health,
)
from mosk_mcp.tools.cluster_health.models import (
    GetKubernetesHealthInput,
    NodeHealthInfo,
    SystemPodHealth,
)
from mosk_mcp.tools.common.enums import HealthStatus


class TestCalculateKubernetesScore:
    """Tests for _calculate_kubernetes_score function."""

    def test_healthy_cluster(self) -> None:
        """Test score for healthy cluster."""
        score = _calculate_kubernetes_score(
            total_nodes=10,
            ready_nodes=10,
            cordoned_nodes=0,
            api_server_healthy=True,
            etcd_healthy=True,
            system_pods_health=[],
        )
        assert score == 100

    def test_no_nodes(self) -> None:
        """Test score with no nodes."""
        score = _calculate_kubernetes_score(
            total_nodes=0,
            ready_nodes=0,
            cordoned_nodes=0,
            api_server_healthy=True,
            etcd_healthy=True,
            system_pods_health=[],
        )
        # No nodes = 0 node points, api=20, etcd=15, pods=25
        assert score == 60

    def test_nodes_not_ready(self) -> None:
        """Test score with nodes not ready."""
        score = _calculate_kubernetes_score(
            total_nodes=10,
            ready_nodes=8,
            cordoned_nodes=0,
            api_server_healthy=True,
            etcd_healthy=True,
            system_pods_health=[],
        )
        # Node score: 8/10 * 40 = 32
        assert score == 92  # 32+20+15+25

    def test_cordoned_nodes(self) -> None:
        """Test score with cordoned nodes."""
        score = _calculate_kubernetes_score(
            total_nodes=10,
            ready_nodes=10,
            cordoned_nodes=2,
            api_server_healthy=True,
            etcd_healthy=True,
            system_pods_health=[],
        )
        # Node score: (10 - 2*0.5)/10 * 40 = 36
        assert score == 96  # 36+20+15+25

    def test_api_unhealthy(self) -> None:
        """Test score with unhealthy API server."""
        score = _calculate_kubernetes_score(
            total_nodes=10,
            ready_nodes=10,
            cordoned_nodes=0,
            api_server_healthy=False,
            etcd_healthy=True,
            system_pods_health=[],
        )
        assert score == 80  # 40+0+15+25

    def test_etcd_unhealthy(self) -> None:
        """Test score with unhealthy etcd."""
        score = _calculate_kubernetes_score(
            total_nodes=10,
            ready_nodes=10,
            cordoned_nodes=0,
            api_server_healthy=True,
            etcd_healthy=False,
            system_pods_health=[],
        )
        assert score == 85  # 40+20+0+25

    def test_system_pods_unhealthy(self) -> None:
        """Test score with system pods issues."""
        system_pods = [
            SystemPodHealth(
                namespace="kube-system",
                total_pods=10,
                running_pods=8,
                ready_pods=8,
                failed_pods=2,
                pending_pods=0,
                unhealthy_pods=[],
            )
        ]
        score = _calculate_kubernetes_score(
            total_nodes=10,
            ready_nodes=10,
            cordoned_nodes=0,
            api_server_healthy=True,
            etcd_healthy=True,
            system_pods_health=system_pods,
        )
        # Pod score: 8/10 * 25 = 20
        assert score == 95  # 40+20+15+20

    def test_score_clamped(self) -> None:
        """Test score is clamped to 0-100."""
        score = _calculate_kubernetes_score(
            total_nodes=10,
            ready_nodes=0,
            cordoned_nodes=10,
            api_server_healthy=False,
            etcd_healthy=False,
            system_pods_health=[
                SystemPodHealth(
                    namespace="kube-system",
                    total_pods=10,
                    running_pods=0,
                    ready_pods=0,
                    failed_pods=10,
                    pending_pods=0,
                    unhealthy_pods=[],
                )
            ],
        )
        assert 0 <= score <= 100


class TestParseNodeConditions:
    """Tests for _parse_node_conditions function."""

    def test_ready_node(self) -> None:
        """Test parsing ready node conditions."""
        conditions = [
            {"type": "Ready", "status": "True"},
            {"type": "MemoryPressure", "status": "False"},
            {"type": "DiskPressure", "status": "False"},
            {"type": "PIDPressure", "status": "False"},
        ]
        result = _parse_node_conditions(conditions)
        assert result["Ready"] is True
        assert result["MemoryPressure"] is True  # Inverted - False means healthy
        assert result["DiskPressure"] is True
        assert result["PIDPressure"] is True

    def test_not_ready_node(self) -> None:
        """Test parsing not ready node conditions."""
        conditions = [{"type": "Ready", "status": "False"}]
        result = _parse_node_conditions(conditions)
        assert result["Ready"] is False

    def test_memory_pressure(self) -> None:
        """Test parsing memory pressure."""
        conditions = [{"type": "MemoryPressure", "status": "True"}]
        result = _parse_node_conditions(conditions)
        assert result["MemoryPressure"] is False  # True status means unhealthy

    def test_unknown_status(self) -> None:
        """Test unknown status handling."""
        conditions = [{"type": "Ready", "status": "Unknown"}]
        result = _parse_node_conditions(conditions)
        assert result["Ready"] is False

    def test_empty_conditions(self) -> None:
        """Test empty conditions."""
        result = _parse_node_conditions([])
        assert result == {}


class TestExtractNodeHealth:
    """Tests for _extract_node_health function."""

    def test_healthy_worker_node(self) -> None:
        """Test extracting healthy worker node."""
        node = {
            "metadata": {"name": "worker-01", "labels": {}},
            "spec": {"unschedulable": False},
            "status": {
                "conditions": [
                    {"type": "Ready", "status": "True"},
                    {"type": "MemoryPressure", "status": "False"},
                    {"type": "DiskPressure", "status": "False"},
                    {"type": "PIDPressure", "status": "False"},
                ]
            },
        }
        result = _extract_node_health(node)
        assert result.name == "worker-01"
        assert result.ready is True
        assert result.schedulable is True
        assert result.role == "worker"
        assert result.conditions_ok is True
        assert result.issues == []

    def test_control_plane_node(self) -> None:
        """Test control plane node role detection."""
        node = {
            "metadata": {
                "name": "cp-01",
                "labels": {"node-role.kubernetes.io/control-plane": ""},
            },
            "spec": {},
            "status": {"conditions": [{"type": "Ready", "status": "True"}]},
        }
        result = _extract_node_health(node)
        assert result.role == "control-plane"

    def test_compute_node(self) -> None:
        """Test compute node role detection."""
        node = {
            "metadata": {
                "name": "compute-01",
                "labels": {"hostlabel.bm.kaas.mirantis.com/worker": "worker"},
            },
            "spec": {},
            "status": {"conditions": [{"type": "Ready", "status": "True"}]},
        }
        result = _extract_node_health(node)
        assert result.role == "compute"

    def test_storage_node(self) -> None:
        """Test storage node role detection."""
        node = {
            "metadata": {
                "name": "storage-01",
                "labels": {"ceph-osd-node": "true"},
            },
            "spec": {},
            "status": {"conditions": [{"type": "Ready", "status": "True"}]},
        }
        result = _extract_node_health(node)
        assert result.role == "storage"

    def test_cordoned_node(self) -> None:
        """Test cordoned node detection."""
        node = {
            "metadata": {"name": "worker-01", "labels": {}},
            "spec": {"unschedulable": True},
            "status": {"conditions": [{"type": "Ready", "status": "True"}]},
        }
        result = _extract_node_health(node)
        assert result.schedulable is False
        assert "cordoned" in result.issues[0].lower()

    def test_node_with_pressure(self) -> None:
        """Test node with resource pressure."""
        node = {
            "metadata": {"name": "worker-01", "labels": {}},
            "spec": {},
            "status": {
                "conditions": [
                    {"type": "Ready", "status": "True"},
                    {"type": "MemoryPressure", "status": "True"},
                    {"type": "DiskPressure", "status": "True"},
                ]
            },
        }
        result = _extract_node_health(node)
        assert result.memory_pressure is True
        assert result.disk_pressure is True
        assert result.conditions_ok is False
        assert len(result.issues) >= 2


class TestAnalyzeSystemPods:
    """Tests for _analyze_system_pods function."""

    def test_all_healthy_pods(self) -> None:
        """Test all pods healthy."""
        pods = [
            {
                "metadata": {"name": "pod-1"},
                "status": {
                    "phase": "Running",
                    "containerStatuses": [{"ready": True, "restartCount": 0}],
                },
            },
            {
                "metadata": {"name": "pod-2"},
                "status": {
                    "phase": "Running",
                    "containerStatuses": [{"ready": True, "restartCount": 0}],
                },
            },
        ]
        result = _analyze_system_pods(pods, "kube-system")
        assert result.total_pods == 2
        assert result.running_pods == 2
        assert result.ready_pods == 2
        assert result.failed_pods == 0
        assert result.unhealthy_pods == []

    def test_pending_pods(self) -> None:
        """Test pending pods."""
        pods = [
            {
                "metadata": {"name": "pending-pod"},
                "status": {"phase": "Pending"},
            }
        ]
        result = _analyze_system_pods(pods, "kube-system")
        assert result.pending_pods == 1
        assert len(result.unhealthy_pods) == 1

    def test_failed_pods(self) -> None:
        """Test failed pods."""
        pods = [
            {
                "metadata": {"name": "failed-pod"},
                "status": {"phase": "Failed"},
            }
        ]
        result = _analyze_system_pods(pods, "kube-system")
        assert result.failed_pods == 1

    def test_high_restart_count(self) -> None:
        """Test high restart count detection."""
        pods = [
            {
                "metadata": {"name": "crash-pod"},
                "status": {
                    "phase": "Running",
                    "containerStatuses": [{"ready": True, "restartCount": 10}],
                },
            }
        ]
        result = _analyze_system_pods(pods, "kube-system")
        assert any("high restarts" in p for p in result.unhealthy_pods)

    def test_succeeded_jobs(self) -> None:
        """Test succeeded jobs are counted as ready."""
        pods = [
            {
                "metadata": {"name": "job-pod"},
                "status": {"phase": "Succeeded"},
            }
        ]
        result = _analyze_system_pods(pods, "kube-system")
        assert result.ready_pods == 1
        assert result.running_pods == 1

    def test_unhealthy_pods_limited(self) -> None:
        """Test unhealthy pods list is limited."""
        pods = [
            {
                "metadata": {"name": f"pending-{i}"},
                "status": {"phase": "Pending"},
            }
            for i in range(20)
        ]
        result = _analyze_system_pods(pods, "kube-system")
        assert len(result.unhealthy_pods) <= 10


class TestGenerateRecommendations:
    """Tests for _generate_recommendations function."""

    def test_healthy_cluster(self) -> None:
        """Test healthy cluster has no recommendations."""
        nodes = [
            NodeHealthInfo(
                name="node-1",
                ready=True,
                schedulable=True,
                role="worker",
                conditions_ok=True,
                issues=[],
                cpu_pressure=False,
                memory_pressure=False,
                disk_pressure=False,
                pid_pressure=False,
            )
        ]
        result = _generate_recommendations(
            score=100,
            nodes=nodes,
            system_pods=[],
            api_healthy=True,
            etcd_healthy=True,
        )
        assert result == []

    def test_api_unhealthy(self) -> None:
        """Test API unhealthy recommendation."""
        result = _generate_recommendations(
            score=50,
            nodes=[],
            system_pods=[],
            api_healthy=False,
            etcd_healthy=True,
        )
        assert any("API server" in r for r in result)

    def test_etcd_unhealthy(self) -> None:
        """Test etcd unhealthy recommendation."""
        result = _generate_recommendations(
            score=50,
            nodes=[],
            system_pods=[],
            api_healthy=True,
            etcd_healthy=False,
        )
        assert any("etcd" in r for r in result)

    def test_nodes_not_ready(self) -> None:
        """Test nodes not ready recommendation."""
        nodes = [
            NodeHealthInfo(
                name="node-1",
                ready=False,
                schedulable=True,
                role="worker",
                conditions_ok=False,
                issues=["Not ready"],
                cpu_pressure=False,
                memory_pressure=False,
                disk_pressure=False,
                pid_pressure=False,
            )
        ]
        result = _generate_recommendations(
            score=50,
            nodes=nodes,
            system_pods=[],
            api_healthy=True,
            etcd_healthy=True,
        )
        assert any("not ready" in r for r in result)

    def test_cordoned_nodes(self) -> None:
        """Test cordoned nodes recommendation."""
        nodes = [
            NodeHealthInfo(
                name="node-1",
                ready=True,
                schedulable=False,
                role="worker",
                conditions_ok=True,
                issues=[],
                cpu_pressure=False,
                memory_pressure=False,
                disk_pressure=False,
                pid_pressure=False,
            )
        ]
        result = _generate_recommendations(
            score=80,
            nodes=nodes,
            system_pods=[],
            api_healthy=True,
            etcd_healthy=True,
        )
        assert any("cordoned" in r for r in result)

    def test_resource_pressure(self) -> None:
        """Test resource pressure recommendation."""
        nodes = [
            NodeHealthInfo(
                name="node-1",
                ready=True,
                schedulable=True,
                role="worker",
                conditions_ok=False,
                issues=[],
                cpu_pressure=False,
                memory_pressure=True,
                disk_pressure=True,
                pid_pressure=False,
            )
        ]
        result = _generate_recommendations(
            score=80,
            nodes=nodes,
            system_pods=[],
            api_healthy=True,
            etcd_healthy=True,
        )
        assert any("pressure" in r for r in result)

    def test_failed_pods(self) -> None:
        """Test failed pods recommendation."""
        system_pods = [
            SystemPodHealth(
                namespace="kube-system",
                total_pods=10,
                running_pods=8,
                ready_pods=8,
                failed_pods=2,
                pending_pods=0,
                unhealthy_pods=[],
            )
        ]
        result = _generate_recommendations(
            score=80,
            nodes=[],
            system_pods=system_pods,
            api_healthy=True,
            etcd_healthy=True,
        )
        assert any("failed" in r for r in result)


class TestGetKubernetesHealth:
    """Tests for get_kubernetes_health function."""

    @pytest.fixture
    def mock_kubernetes_adapter(self) -> AsyncMock:
        """Create mock Kubernetes adapter."""
        return AsyncMock()

    @pytest.fixture
    def healthy_node(self) -> dict:
        """Create healthy node data."""
        return {
            "metadata": {"name": "node-1", "labels": {}},
            "spec": {"unschedulable": False},
            "status": {
                "conditions": [
                    {"type": "Ready", "status": "True"},
                    {"type": "MemoryPressure", "status": "False"},
                    {"type": "DiskPressure", "status": "False"},
                    {"type": "PIDPressure", "status": "False"},
                ]
            },
        }

    @pytest.mark.asyncio
    async def test_healthy_cluster(
        self, mock_kubernetes_adapter: AsyncMock, healthy_node: dict
    ) -> None:
        """Test healthy cluster status."""
        mock_kubernetes_adapter.get_server_version.return_value = "v1.28.0"
        mock_kubernetes_adapter.check_api_health.return_value = None
        mock_kubernetes_adapter.list_pods.return_value = [
            {
                "metadata": {"name": "etcd-1"},
                "status": {"phase": "Running"},
            }
        ]
        mock_kubernetes_adapter.list_nodes.return_value = [healthy_node]

        result = await get_kubernetes_health(
            mock_kubernetes_adapter,
            GetKubernetesHealthInput(include_node_details=True, include_system_pods=False),
        )

        assert result.health == HealthStatus.HEALTHY
        assert result.api_server_healthy is True
        assert result.etcd_healthy is True
        assert result.total_nodes == 1
        assert result.ready_nodes == 1
        assert len(result.nodes) == 1

    @pytest.mark.asyncio
    async def test_unhealthy_api_server(
        self, mock_kubernetes_adapter: AsyncMock, healthy_node: dict
    ) -> None:
        """Test unhealthy API server."""
        mock_kubernetes_adapter.get_server_version.return_value = "v1.28.0"
        mock_kubernetes_adapter.check_api_health.side_effect = Exception("Connection refused")
        mock_kubernetes_adapter.list_pods.return_value = []
        mock_kubernetes_adapter.list_nodes.return_value = [healthy_node]

        result = await get_kubernetes_health(
            mock_kubernetes_adapter,
            GetKubernetesHealthInput(include_system_pods=False),
        )

        assert result.api_server_healthy is False
        assert len(result.issues) > 0

    @pytest.mark.asyncio
    async def test_unhealthy_etcd(
        self, mock_kubernetes_adapter: AsyncMock, healthy_node: dict
    ) -> None:
        """Test unhealthy etcd."""
        mock_kubernetes_adapter.get_server_version.return_value = "v1.28.0"
        mock_kubernetes_adapter.check_api_health.return_value = None
        mock_kubernetes_adapter.list_pods.return_value = [
            {
                "metadata": {"name": "etcd-1"},
                "status": {"phase": "Pending"},
            }
        ]
        mock_kubernetes_adapter.list_nodes.return_value = [healthy_node]

        result = await get_kubernetes_health(
            mock_kubernetes_adapter,
            GetKubernetesHealthInput(include_system_pods=False),
        )

        assert result.etcd_healthy is False

    @pytest.mark.asyncio
    async def test_nodes_not_ready(self, mock_kubernetes_adapter: AsyncMock) -> None:
        """Test nodes not ready."""
        mock_kubernetes_adapter.get_server_version.return_value = "v1.28.0"
        mock_kubernetes_adapter.check_api_health.return_value = None
        mock_kubernetes_adapter.list_pods.return_value = []
        mock_kubernetes_adapter.list_nodes.return_value = [
            {
                "metadata": {"name": "node-1", "labels": {}},
                "spec": {},
                "status": {"conditions": [{"type": "Ready", "status": "False"}]},
            }
        ]

        result = await get_kubernetes_health(
            mock_kubernetes_adapter,
            GetKubernetesHealthInput(include_system_pods=False),
        )

        assert result.not_ready_nodes == 1
        assert result.health != HealthStatus.HEALTHY

    @pytest.mark.asyncio
    async def test_with_system_pods(
        self, mock_kubernetes_adapter: AsyncMock, healthy_node: dict
    ) -> None:
        """Test with system pods analysis."""
        mock_kubernetes_adapter.get_server_version.return_value = "v1.28.0"
        mock_kubernetes_adapter.check_api_health.return_value = None
        mock_kubernetes_adapter.list_nodes.return_value = [healthy_node]

        def list_pods_side_effect(namespace=None, label_selector=None):
            if label_selector == "component=etcd":
                return [{"metadata": {"name": "etcd"}, "status": {"phase": "Running"}}]
            if namespace == "kube-system":
                return [
                    {
                        "metadata": {"name": "coredns"},
                        "status": {
                            "phase": "Running",
                            "containerStatuses": [{"ready": True, "restartCount": 0}],
                        },
                    }
                ]
            return []

        mock_kubernetes_adapter.list_pods.side_effect = list_pods_side_effect

        result = await get_kubernetes_health(
            mock_kubernetes_adapter,
            GetKubernetesHealthInput(include_system_pods=True),
        )

        assert len(result.system_pods) >= 1

    @pytest.mark.asyncio
    async def test_version_retrieval_failure(
        self, mock_kubernetes_adapter: AsyncMock, healthy_node: dict
    ) -> None:
        """Test version retrieval failure is handled."""
        mock_kubernetes_adapter.get_server_version.side_effect = Exception("Connection error")
        mock_kubernetes_adapter.check_api_health.return_value = None
        mock_kubernetes_adapter.list_pods.return_value = []
        mock_kubernetes_adapter.list_nodes.return_value = [healthy_node]

        result = await get_kubernetes_health(
            mock_kubernetes_adapter,
            GetKubernetesHealthInput(include_system_pods=False),
        )

        assert result.server_version == "unknown"

    @pytest.mark.asyncio
    async def test_timestamp_included(
        self, mock_kubernetes_adapter: AsyncMock, healthy_node: dict
    ) -> None:
        """Test timestamp is included."""
        mock_kubernetes_adapter.get_server_version.return_value = "v1.28.0"
        mock_kubernetes_adapter.check_api_health.return_value = None
        mock_kubernetes_adapter.list_pods.return_value = []
        mock_kubernetes_adapter.list_nodes.return_value = [healthy_node]

        result = await get_kubernetes_health(
            mock_kubernetes_adapter,
            GetKubernetesHealthInput(include_system_pods=False),
        )

        assert result.timestamp is not None
        assert "T" in result.timestamp
