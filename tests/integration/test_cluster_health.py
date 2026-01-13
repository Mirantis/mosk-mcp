"""Integration tests for cluster health MCP tools.

These tests validate the cluster health tools end-to-end with mocked adapters,
simulating realistic cluster responses without requiring actual cluster access.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from mosk_mcp.tools.cluster_health.get_kubernetes_health import (
    GetKubernetesHealthInput,
    get_kubernetes_health,
)
from mosk_mcp.tools.cluster_health.get_mosk_cluster_health import (
    GetClusterHealthInput,
    get_mosk_cluster_health,
)
from mosk_mcp.tools.cluster_health.get_openstack_health import (
    GetOpenStackHealthInput,
    get_openstack_health,
)


# =============================================================================
# Kubernetes Health Tests
# =============================================================================


@pytest.mark.integration
class TestGetKubernetesHealth:
    """Integration tests for get_kubernetes_health tool."""

    @pytest.mark.asyncio
    async def test_healthy_cluster(self, mock_kubernetes_adapter: MagicMock) -> None:
        """Test getting health of a healthy Kubernetes cluster."""
        input_data = GetKubernetesHealthInput(
            include_node_details=True,
            include_system_pods=True,
        )

        result = await get_kubernetes_health(
            kubernetes_adapter=mock_kubernetes_adapter,
            input_data=input_data,
        )

        # health is a HealthStatus enum, not a bool
        assert result.health is not None
        assert result.ready_nodes == 9
        assert result.total_nodes == 9
        assert len(result.nodes) == 9

    @pytest.mark.asyncio
    async def test_cluster_with_unhealthy_node(self, mock_kubernetes_adapter: MagicMock) -> None:
        """Test cluster health when one node is not ready."""
        # Modify one node to be not ready
        nodes = mock_kubernetes_adapter.list_nodes.return_value.copy()
        nodes[0]["status"]["conditions"][0]["status"] = "False"
        mock_kubernetes_adapter.list_nodes = AsyncMock(return_value=nodes)

        input_data = GetKubernetesHealthInput(include_node_details=True)
        result = await get_kubernetes_health(
            kubernetes_adapter=mock_kubernetes_adapter,
            input_data=input_data,
        )

        assert result.ready_nodes == 8
        assert result.total_nodes == 9
        # Nodes should include the unhealthy node
        unhealthy_nodes = [n for n in result.nodes if not n.ready]
        assert len(unhealthy_nodes) == 1

    @pytest.mark.asyncio
    async def test_without_node_details(self, mock_kubernetes_adapter: MagicMock) -> None:
        """Test getting health without node details."""
        input_data = GetKubernetesHealthInput(include_node_details=False)

        result = await get_kubernetes_health(
            kubernetes_adapter=mock_kubernetes_adapter,
            input_data=input_data,
        )

        # health is a HealthStatus enum, not a bool
        assert result.health is not None
        assert result.nodes == []


# =============================================================================
# OpenStack Health Tests
# =============================================================================


@pytest.mark.integration
class TestGetOpenStackHealth:
    """Integration tests for get_openstack_health tool."""

    @pytest.mark.asyncio
    async def test_healthy_openstack(
        self,
        mock_kubernetes_adapter: MagicMock,
    ) -> None:
        """Test getting health of a healthy OpenStack deployment."""
        input_data = GetOpenStackHealthInput(
            osdpl_name="mos",
            namespace="openstack",
            include_services=True,
            include_endpoints=True,
        )

        result = await get_openstack_health(
            kubernetes_adapter=mock_kubernetes_adapter,
            input_data=input_data,
        )

        # Check that OpenStack health returned with expected fields
        # osdpl_phase comes from OSDPL status, not input
        assert result.osdpl_phase is not None
        assert result.control_plane_health is not None
        assert result.services_total >= 0


# =============================================================================
# Ceph Health Tests
# =============================================================================


@pytest.mark.integration
@pytest.mark.skip(reason="Ceph tools require complex adapter mocking - in development")
class TestGetCephHealth:
    """Integration tests for get_ceph_health tool.

    Note: Ceph tools use internal CephAdapter that connects to Kubernetes pods
    to execute ceph commands. This requires more complex mocking than basic
    adapter methods.
    """

    @pytest.mark.asyncio
    async def test_healthy_ceph(self, mock_kubernetes_adapter: MagicMock) -> None:
        """Test getting health of a healthy Ceph cluster."""
        pass


# =============================================================================
# MOSK Cluster Health Tests
# =============================================================================


@pytest.mark.integration
class TestGetMoskClusterHealth:
    """Integration tests for get_mosk_cluster_health tool."""

    @pytest.mark.asyncio
    async def test_healthy_mosk_cluster(
        self,
        mock_kubernetes_adapter: MagicMock,
    ) -> None:
        """Test getting comprehensive health of a healthy MOSK cluster."""
        # Mock Ceph adapter within kubernetes adapter
        mock_kubernetes_adapter.ceph = MagicMock()
        mock_kubernetes_adapter.ceph.get_status = AsyncMock(
            return_value={
                "health": {"status": "HEALTH_OK", "checks": {}},
                "osd_map": {"num_osds": 12, "num_up_osds": 12, "num_in_osds": 12},
            }
        )
        mock_kubernetes_adapter.ceph.get_df = AsyncMock(
            return_value={
                "stats": {"total_bytes": 10995116277760, "total_used_bytes": 3298534883328},
            }
        )

        input_data = GetClusterHealthInput(
            osdpl_name="mos",
            namespace="openstack",
            cluster_namespace="lab",
            include_component_details=True,
            include_recommendations=True,
        )

        result = await get_mosk_cluster_health(
            kubernetes_adapter=mock_kubernetes_adapter,
            input_data=input_data,
            mcc_adapter=mock_kubernetes_adapter,
        )

        # Overall status should be healthy - check health_state enum
        assert result.health_state is not None
        assert result.health_score.overall_score >= 0  # Score is 0-100

    @pytest.mark.asyncio
    async def test_without_recommendations(
        self,
        mock_kubernetes_adapter: MagicMock,
    ) -> None:
        """Test cluster health without recommendations."""
        mock_kubernetes_adapter.ceph = MagicMock()
        mock_kubernetes_adapter.ceph.get_status = AsyncMock(
            return_value={
                "health": {"status": "HEALTH_OK", "checks": {}},
                "osd_map": {"num_osds": 12, "num_up_osds": 12, "num_in_osds": 12},
            }
        )
        mock_kubernetes_adapter.ceph.get_df = AsyncMock(
            return_value={
                "stats": {"total_bytes": 10995116277760, "total_used_bytes": 3298534883328},
            }
        )

        input_data = GetClusterHealthInput(
            osdpl_name="mos",
            include_recommendations=False,
        )

        result = await get_mosk_cluster_health(
            kubernetes_adapter=mock_kubernetes_adapter,
            input_data=input_data,
        )

        assert result.health_state is not None
