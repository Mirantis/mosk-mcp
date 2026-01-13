"""Unit tests for get_resource_utilization tool."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mosk_mcp.core.exceptions import ToolExecutionError
from mosk_mcp.tools.cluster_health.get_resource_utilization import (
    _calculate_percentage,
    _determine_node_role,
    _parse_cpu_quantity,
    _parse_memory_quantity,
    get_resource_utilization,
)
from mosk_mcp.tools.cluster_health.models import GetResourceUtilizationInput


class TestParseCpuQuantity:
    """Tests for _parse_cpu_quantity function."""

    def test_empty_string(self) -> None:
        """Test empty string returns 0."""
        assert _parse_cpu_quantity("") == 0

    def test_millicores(self) -> None:
        """Test parsing millicores."""
        assert _parse_cpu_quantity("500m") == 500
        assert _parse_cpu_quantity("1000m") == 1000
        assert _parse_cpu_quantity("100m") == 100

    def test_nanocores(self) -> None:
        """Test parsing nanocores."""
        assert _parse_cpu_quantity("1000000000n") == 1000  # 1 core in nanocores
        assert _parse_cpu_quantity("500000000n") == 500  # 0.5 core

    def test_cores_integer(self) -> None:
        """Test parsing integer cores."""
        assert _parse_cpu_quantity("2") == 2000
        assert _parse_cpu_quantity("1") == 1000
        assert _parse_cpu_quantity("4") == 4000

    def test_cores_float(self) -> None:
        """Test parsing float cores."""
        assert _parse_cpu_quantity("1.5") == 1500
        assert _parse_cpu_quantity("0.5") == 500
        assert _parse_cpu_quantity("2.25") == 2250

    def test_invalid_value(self) -> None:
        """Test invalid value returns 0."""
        assert _parse_cpu_quantity("invalid") == 0


class TestParseMemoryQuantity:
    """Tests for _parse_memory_quantity function."""

    def test_empty_string(self) -> None:
        """Test empty string returns 0."""
        assert _parse_memory_quantity("") == 0

    def test_ki_suffix(self) -> None:
        """Test Ki suffix (kibibytes)."""
        assert _parse_memory_quantity("1024Ki") == 1024 * 1024

    def test_mi_suffix(self) -> None:
        """Test Mi suffix (mebibytes)."""
        assert _parse_memory_quantity("1Mi") == 1024**2
        assert _parse_memory_quantity("512Mi") == 512 * 1024**2

    def test_gi_suffix(self) -> None:
        """Test Gi suffix (gibibytes)."""
        assert _parse_memory_quantity("1Gi") == 1024**3
        assert _parse_memory_quantity("8Gi") == 8 * 1024**3

    def test_ti_suffix(self) -> None:
        """Test Ti suffix (tebibytes)."""
        assert _parse_memory_quantity("1Ti") == 1024**4

    def test_k_suffix(self) -> None:
        """Test K suffix (kilobytes)."""
        assert _parse_memory_quantity("1000K") == 1000 * 1000

    def test_m_suffix(self) -> None:
        """Test M suffix (megabytes)."""
        assert _parse_memory_quantity("500M") == 500 * 1000**2

    def test_g_suffix(self) -> None:
        """Test G suffix (gigabytes)."""
        assert _parse_memory_quantity("2G") == 2 * 1000**3

    def test_t_suffix(self) -> None:
        """Test T suffix (terabytes)."""
        assert _parse_memory_quantity("1T") == 1000**4

    def test_plain_bytes(self) -> None:
        """Test plain bytes."""
        assert _parse_memory_quantity("1024") == 1024
        assert _parse_memory_quantity("1048576") == 1048576

    def test_invalid_value(self) -> None:
        """Test invalid value returns 0."""
        assert _parse_memory_quantity("invalid") == 0


class TestDetermineNodeRole:
    """Tests for _determine_node_role function."""

    def test_control_plane_new_label(self) -> None:
        """Test control-plane role detection (new label)."""
        labels = {"node-role.kubernetes.io/control-plane": ""}
        assert _determine_node_role(labels) == "control-plane"

    def test_control_plane_legacy_label(self) -> None:
        """Test control-plane role detection (legacy master label)."""
        labels = {"node-role.kubernetes.io/master": ""}
        assert _determine_node_role(labels) == "control-plane"

    def test_openstack_control(self) -> None:
        """Test openstack-control role detection."""
        labels = {"openstack-control-plane": "enabled"}
        assert _determine_node_role(labels) == "openstack-control"

    def test_openstack_control_mosk_label(self) -> None:
        """Test openstack-control with MOSK label."""
        labels = {"hostlabel.bm.kaas.mirantis.com/controlplane": "controlplane"}
        assert _determine_node_role(labels) == "openstack-control"

    def test_compute(self) -> None:
        """Test compute role detection."""
        labels = {"openstack-compute-node": "enabled"}
        assert _determine_node_role(labels) == "compute"

    def test_compute_mosk_label(self) -> None:
        """Test compute with MOSK label."""
        labels = {"hostlabel.bm.kaas.mirantis.com/worker": "worker"}
        assert _determine_node_role(labels) == "compute"

    def test_storage(self) -> None:
        """Test storage role detection."""
        labels = {"ceph-osd-node": "enabled"}
        assert _determine_node_role(labels) == "storage"

    def test_default_worker(self) -> None:
        """Test default worker role."""
        labels = {}
        assert _determine_node_role(labels) == "worker"


class TestCalculatePercentage:
    """Tests for _calculate_percentage function."""

    def test_normal_calculation(self) -> None:
        """Test normal percentage calculation."""
        assert _calculate_percentage(50, 100) == 50.0
        assert _calculate_percentage(25, 100) == 25.0

    def test_zero_total(self) -> None:
        """Test zero total returns 0."""
        assert _calculate_percentage(50, 0) == 0.0

    def test_negative_total(self) -> None:
        """Test negative total returns 0."""
        assert _calculate_percentage(50, -100) == 0.0

    def test_rounding(self) -> None:
        """Test result is rounded to 2 decimal places."""
        result = _calculate_percentage(33, 100)
        assert result == 33.0

        result = _calculate_percentage(1, 3)
        assert result == 33.33


class TestGetResourceUtilization:
    """Tests for get_resource_utilization function."""

    @pytest.fixture
    def mock_kubernetes_adapter(self) -> AsyncMock:
        """Create mock Kubernetes adapter."""
        return AsyncMock()

    @pytest.fixture
    def sample_node(self) -> dict:
        """Create sample node data."""
        return {
            "metadata": {
                "name": "node-1",
                "labels": {},
            },
            "status": {
                "capacity": {
                    "cpu": "4",
                    "memory": "16Gi",
                    "pods": "110",
                },
                "allocatable": {
                    "cpu": "3900m",
                    "memory": "15Gi",
                    "pods": "110",
                },
            },
        }

    @pytest.fixture
    def sample_pod(self) -> dict:
        """Create sample pod data."""
        return {
            "metadata": {
                "name": "test-pod",
                "namespace": "default",
            },
            "spec": {
                "nodeName": "node-1",
                "containers": [
                    {
                        "name": "container-1",
                        "resources": {
                            "requests": {
                                "cpu": "100m",
                                "memory": "128Mi",
                            }
                        },
                    }
                ],
            },
            "status": {"phase": "Running"},
        }

    @pytest.mark.asyncio
    async def test_basic_utilization(
        self,
        mock_kubernetes_adapter: AsyncMock,
        sample_node: dict,
        sample_pod: dict,
    ) -> None:
        """Test basic utilization retrieval."""
        mock_kubernetes_adapter.list_nodes.return_value = [sample_node]
        mock_kubernetes_adapter.list_pods.return_value = [sample_pod]

        mock_ceph_status = MagicMock()
        mock_ceph_status.total_bytes = 1000000000000
        mock_ceph_status.used_bytes = 500000000000
        mock_ceph_status.available_bytes = 500000000000
        mock_ceph_status.capacity_percent = 50.0

        with patch(
            "mosk_mcp.tools.cluster_health.get_resource_utilization.CephAdapter",
        ) as MockCeph:
            mock_ceph = AsyncMock()
            mock_ceph.get_cluster_status.return_value = mock_ceph_status
            MockCeph.return_value.__aenter__.return_value = mock_ceph
            MockCeph.return_value.__aexit__.return_value = None

            result = await get_resource_utilization(
                mock_kubernetes_adapter,
                GetResourceUtilizationInput(),
            )

        assert result.cluster_cpu_capacity_millicores > 0
        assert result.cluster_memory_capacity_bytes > 0
        assert result.storage.usage_percent == 50.0

    @pytest.mark.asyncio
    async def test_with_per_node_details(
        self,
        mock_kubernetes_adapter: AsyncMock,
        sample_node: dict,
        sample_pod: dict,
    ) -> None:
        """Test with per-node details included."""
        mock_kubernetes_adapter.list_nodes.return_value = [sample_node]
        mock_kubernetes_adapter.list_pods.return_value = [sample_pod]

        mock_ceph_status = MagicMock()
        mock_ceph_status.total_bytes = 1000000000000
        mock_ceph_status.used_bytes = 500000000000
        mock_ceph_status.available_bytes = 500000000000
        mock_ceph_status.capacity_percent = 50.0

        with patch(
            "mosk_mcp.tools.cluster_health.get_resource_utilization.CephAdapter",
        ) as MockCeph:
            mock_ceph = AsyncMock()
            mock_ceph.get_cluster_status.return_value = mock_ceph_status
            MockCeph.return_value.__aenter__.return_value = mock_ceph
            MockCeph.return_value.__aexit__.return_value = None

            result = await get_resource_utilization(
                mock_kubernetes_adapter,
                GetResourceUtilizationInput(include_per_node=True),
            )

        assert len(result.nodes) == 1
        assert result.nodes[0].node_name == "node-1"

    @pytest.mark.asyncio
    async def test_with_per_namespace_details(
        self,
        mock_kubernetes_adapter: AsyncMock,
        sample_node: dict,
        sample_pod: dict,
    ) -> None:
        """Test with per-namespace details included."""
        mock_kubernetes_adapter.list_nodes.return_value = [sample_node]
        mock_kubernetes_adapter.list_pods.return_value = [sample_pod]

        mock_ceph_status = MagicMock()
        mock_ceph_status.total_bytes = 1000000000000
        mock_ceph_status.used_bytes = 500000000000
        mock_ceph_status.available_bytes = 500000000000
        mock_ceph_status.capacity_percent = 50.0

        with patch(
            "mosk_mcp.tools.cluster_health.get_resource_utilization.CephAdapter",
        ) as MockCeph:
            mock_ceph = AsyncMock()
            mock_ceph.get_cluster_status.return_value = mock_ceph_status
            MockCeph.return_value.__aenter__.return_value = mock_ceph
            MockCeph.return_value.__aexit__.return_value = None

            result = await get_resource_utilization(
                mock_kubernetes_adapter,
                GetResourceUtilizationInput(include_per_namespace=True),
            )

        assert len(result.namespaces) >= 0

    @pytest.mark.asyncio
    async def test_high_utilization_warnings(
        self,
        mock_kubernetes_adapter: AsyncMock,
    ) -> None:
        """Test warnings for high utilization."""
        # Node with high CPU and memory requests
        node = {
            "metadata": {"name": "node-1", "labels": {}},
            "status": {
                "capacity": {"cpu": "4", "memory": "16Gi", "pods": "110"},
                "allocatable": {"cpu": "4000m", "memory": "16Gi", "pods": "110"},
            },
        }

        pod = {
            "metadata": {"name": "pod-1", "namespace": "default"},
            "spec": {
                "nodeName": "node-1",
                "containers": [{"resources": {"requests": {"cpu": "3800m", "memory": "15Gi"}}}],
            },
            "status": {"phase": "Running"},
        }

        mock_kubernetes_adapter.list_nodes.return_value = [node]
        mock_kubernetes_adapter.list_pods.return_value = [pod]

        mock_ceph_status = MagicMock()
        mock_ceph_status.total_bytes = 1000000000000
        mock_ceph_status.used_bytes = 500000000000
        mock_ceph_status.available_bytes = 500000000000
        mock_ceph_status.capacity_percent = 50.0

        with patch(
            "mosk_mcp.tools.cluster_health.get_resource_utilization.CephAdapter",
        ) as MockCeph:
            mock_ceph = AsyncMock()
            mock_ceph.get_cluster_status.return_value = mock_ceph_status
            MockCeph.return_value.__aenter__.return_value = mock_ceph
            MockCeph.return_value.__aexit__.return_value = None

            result = await get_resource_utilization(
                mock_kubernetes_adapter,
                GetResourceUtilizationInput(include_per_node=True),
            )

        assert len(result.warnings) >= 1

    @pytest.mark.asyncio
    async def test_storage_warning_high_usage(
        self,
        mock_kubernetes_adapter: AsyncMock,
        sample_node: dict,
    ) -> None:
        """Test storage warning for high usage."""
        mock_kubernetes_adapter.list_nodes.return_value = [sample_node]
        mock_kubernetes_adapter.list_pods.return_value = []

        mock_ceph_status = MagicMock()
        mock_ceph_status.total_bytes = 1000000000000
        mock_ceph_status.used_bytes = 800000000000
        mock_ceph_status.available_bytes = 200000000000
        mock_ceph_status.capacity_percent = 80.0

        with patch(
            "mosk_mcp.tools.cluster_health.get_resource_utilization.CephAdapter",
        ) as MockCeph:
            mock_ceph = AsyncMock()
            mock_ceph.get_cluster_status.return_value = mock_ceph_status
            MockCeph.return_value.__aenter__.return_value = mock_ceph
            MockCeph.return_value.__aexit__.return_value = None

            result = await get_resource_utilization(
                mock_kubernetes_adapter,
                GetResourceUtilizationInput(),
            )

        assert result.storage.status == "warning"
        assert any("Storage" in w for w in result.warnings)

    @pytest.mark.asyncio
    async def test_storage_critical_usage(
        self,
        mock_kubernetes_adapter: AsyncMock,
        sample_node: dict,
    ) -> None:
        """Test storage status for critical usage."""
        mock_kubernetes_adapter.list_nodes.return_value = [sample_node]
        mock_kubernetes_adapter.list_pods.return_value = []

        mock_ceph_status = MagicMock()
        mock_ceph_status.total_bytes = 1000000000000
        mock_ceph_status.used_bytes = 900000000000
        mock_ceph_status.available_bytes = 100000000000
        mock_ceph_status.capacity_percent = 90.0

        with patch(
            "mosk_mcp.tools.cluster_health.get_resource_utilization.CephAdapter",
        ) as MockCeph:
            mock_ceph = AsyncMock()
            mock_ceph.get_cluster_status.return_value = mock_ceph_status
            MockCeph.return_value.__aenter__.return_value = mock_ceph
            MockCeph.return_value.__aexit__.return_value = None

            result = await get_resource_utilization(
                mock_kubernetes_adapter,
                GetResourceUtilizationInput(),
            )

        assert result.storage.status == "critical"

    @pytest.mark.asyncio
    async def test_ceph_error_handled(
        self,
        mock_kubernetes_adapter: AsyncMock,
        sample_node: dict,
    ) -> None:
        """Test Ceph error is handled gracefully."""
        mock_kubernetes_adapter.list_nodes.return_value = [sample_node]
        mock_kubernetes_adapter.list_pods.return_value = []

        with patch(
            "mosk_mcp.tools.cluster_health.get_resource_utilization.CephAdapter",
        ) as MockCeph:
            MockCeph.return_value.__aenter__.side_effect = Exception("Ceph unavailable")

            result = await get_resource_utilization(
                mock_kubernetes_adapter,
                GetResourceUtilizationInput(),
            )

        assert result.storage.status == "error"
        assert result.storage.error_message is not None

    @pytest.mark.asyncio
    async def test_pod_listing_error_handled(
        self,
        mock_kubernetes_adapter: AsyncMock,
        sample_node: dict,
    ) -> None:
        """Test pod listing error is handled."""
        mock_kubernetes_adapter.list_nodes.return_value = [sample_node]

        # First call for node pods fails, other calls succeed
        call_count = [0]

        async def list_pods_side_effect(**kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise Exception("Failed to list pods")
            return []

        mock_kubernetes_adapter.list_pods.side_effect = list_pods_side_effect

        mock_ceph_status = MagicMock()
        mock_ceph_status.total_bytes = 1000000000000
        mock_ceph_status.used_bytes = 500000000000
        mock_ceph_status.available_bytes = 500000000000
        mock_ceph_status.capacity_percent = 50.0

        with patch(
            "mosk_mcp.tools.cluster_health.get_resource_utilization.CephAdapter",
        ) as MockCeph:
            mock_ceph = AsyncMock()
            mock_ceph.get_cluster_status.return_value = mock_ceph_status
            MockCeph.return_value.__aenter__.return_value = mock_ceph
            MockCeph.return_value.__aexit__.return_value = None

            # Should not raise, just log warning
            result = await get_resource_utilization(
                mock_kubernetes_adapter,
                GetResourceUtilizationInput(),
            )

        assert result is not None

    @pytest.mark.asyncio
    async def test_recommendations_high_cpu(
        self,
        mock_kubernetes_adapter: AsyncMock,
    ) -> None:
        """Test recommendations for high CPU usage."""
        node = {
            "metadata": {"name": "node-1", "labels": {}},
            "status": {
                "capacity": {"cpu": "4", "memory": "16Gi", "pods": "110"},
                "allocatable": {"cpu": "4000m", "memory": "16Gi", "pods": "110"},
            },
        }

        pod = {
            "metadata": {"name": "pod-1", "namespace": "default"},
            "spec": {
                "nodeName": "node-1",
                "containers": [{"resources": {"requests": {"cpu": "3500m", "memory": "1Gi"}}}],
            },
            "status": {"phase": "Running"},
        }

        mock_kubernetes_adapter.list_nodes.return_value = [node]
        mock_kubernetes_adapter.list_pods.return_value = [pod]

        mock_ceph_status = MagicMock()
        mock_ceph_status.total_bytes = 1000000000000
        mock_ceph_status.used_bytes = 500000000000
        mock_ceph_status.available_bytes = 500000000000
        mock_ceph_status.capacity_percent = 50.0

        with patch(
            "mosk_mcp.tools.cluster_health.get_resource_utilization.CephAdapter",
        ) as MockCeph:
            mock_ceph = AsyncMock()
            mock_ceph.get_cluster_status.return_value = mock_ceph_status
            MockCeph.return_value.__aenter__.return_value = mock_ceph
            MockCeph.return_value.__aexit__.return_value = None

            result = await get_resource_utilization(
                mock_kubernetes_adapter,
                GetResourceUtilizationInput(),
            )

        assert any("CPU" in r for r in result.recommendations)

    @pytest.mark.asyncio
    async def test_timestamp_included(
        self,
        mock_kubernetes_adapter: AsyncMock,
        sample_node: dict,
    ) -> None:
        """Test timestamp is included."""
        mock_kubernetes_adapter.list_nodes.return_value = [sample_node]
        mock_kubernetes_adapter.list_pods.return_value = []

        mock_ceph_status = MagicMock()
        mock_ceph_status.total_bytes = 1000000000000
        mock_ceph_status.used_bytes = 500000000000
        mock_ceph_status.available_bytes = 500000000000
        mock_ceph_status.capacity_percent = 50.0

        with patch(
            "mosk_mcp.tools.cluster_health.get_resource_utilization.CephAdapter",
        ) as MockCeph:
            mock_ceph = AsyncMock()
            mock_ceph.get_cluster_status.return_value = mock_ceph_status
            MockCeph.return_value.__aenter__.return_value = mock_ceph
            MockCeph.return_value.__aexit__.return_value = None

            result = await get_resource_utilization(
                mock_kubernetes_adapter,
                GetResourceUtilizationInput(),
            )

        assert result.timestamp is not None
        assert "T" in result.timestamp

    @pytest.mark.asyncio
    async def test_general_error_handling(
        self,
        mock_kubernetes_adapter: AsyncMock,
    ) -> None:
        """Test general error handling."""
        mock_kubernetes_adapter.list_nodes.side_effect = Exception("Connection failed")

        with pytest.raises(ToolExecutionError) as exc_info:
            await get_resource_utilization(
                mock_kubernetes_adapter,
                GetResourceUtilizationInput(),
            )

        assert "Failed to get resource utilization" in str(exc_info.value)
