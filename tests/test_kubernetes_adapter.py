"""Tests for the Kubernetes adapter.

This module tests the KubernetesAdapter class with mocked kr8s client.
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mosk_mcp.adapters.kubernetes import KubernetesAdapter, kubernetes_client
from mosk_mcp.core.config import Environment, LogFormat, Settings
from mosk_mcp.core.exceptions import (
    ConfigurationError,
    MoskConnectionError,
    ResourceNotFoundError,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_kr8s_api():
    """Create a mock kr8s API client."""
    mock_api = AsyncMock()
    mock_api.version = AsyncMock(
        return_value={
            "gitVersion": "v1.28.0",
            "platform": "linux/amd64",
            "goVersion": "go1.21.0",
        }
    )
    return mock_api


@pytest.fixture
def adapter():
    """Create a KubernetesAdapter instance."""
    return KubernetesAdapter(namespace="test-namespace")


@pytest.fixture
def settings():
    """Create test settings.

    Note: environment=DEVELOPMENT allows auth_enabled=False
    and doesn't require MCC URL.
    """
    return Settings(
        kubernetes_namespace="test-namespace",
        auth_enabled=False,
        otel_enabled=False,
        log_format=LogFormat.CONSOLE,
        environment=Environment.DEVELOPMENT,
    )


# =============================================================================
# Connection Tests
# =============================================================================


class TestKubernetesAdapterConnection:
    """Tests for connection management."""

    @pytest.mark.asyncio
    async def test_connect_without_kubeconfig(self, mock_kr8s_api):
        """Test connecting without explicit kubeconfig."""
        adapter = KubernetesAdapter()

        with patch("kr8s.asyncio.api", return_value=mock_kr8s_api):
            await adapter.connect()

            assert adapter.is_connected
            assert adapter._api is not None

        await adapter.disconnect()

    @pytest.mark.asyncio
    async def test_connect_with_kubeconfig(self, mock_kr8s_api, tmp_path):
        """Test connecting with kubeconfig file."""
        # Create a fake kubeconfig file
        kubeconfig = tmp_path / "kubeconfig"
        kubeconfig.write_text("apiVersion: v1\nkind: Config")

        adapter = KubernetesAdapter(kubeconfig_path=kubeconfig)

        with patch("kr8s.asyncio.api", return_value=mock_kr8s_api):
            await adapter.connect()
            assert adapter.is_connected

        await adapter.disconnect()

    @pytest.mark.asyncio
    async def test_connect_with_missing_kubeconfig(self):
        """Test connecting with non-existent kubeconfig."""
        adapter = KubernetesAdapter(kubeconfig_path=Path("/nonexistent/kubeconfig"))

        # The ConfigurationError gets wrapped in a MoskConnectionError
        with pytest.raises((ConfigurationError, MoskConnectionError)) as exc_info:
            await adapter.connect()

        assert "not found" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_connect_failure(self):
        """Test handling connection failure."""
        adapter = KubernetesAdapter()

        with patch("kr8s.asyncio.api", side_effect=Exception("Connection refused")):
            with pytest.raises(MoskConnectionError) as exc_info:
                await adapter.connect()

            assert "kubernetes" in exc_info.value.service.lower()

    @pytest.mark.asyncio
    async def test_disconnect(self, mock_kr8s_api):
        """Test disconnecting from cluster."""
        adapter = KubernetesAdapter()

        with patch("kr8s.asyncio.api", return_value=mock_kr8s_api):
            await adapter.connect()
            assert adapter.is_connected

            await adapter.disconnect()
            assert not adapter.is_connected
            assert adapter._api is None

    @pytest.mark.asyncio
    async def test_context_manager(self, mock_kr8s_api):
        """Test using adapter as context manager."""
        with patch("kr8s.asyncio.api", return_value=mock_kr8s_api):
            async with KubernetesAdapter() as adapter:
                assert adapter.is_connected

            assert not adapter.is_connected

    @pytest.mark.asyncio
    async def test_ensure_connected_raises(self, adapter):
        """Test that operations fail when not connected."""
        with pytest.raises(MoskConnectionError):
            adapter._ensure_connected()


# =============================================================================
# Health Check Tests
# =============================================================================


class TestKubernetesAdapterHealth:
    """Tests for health checking."""

    @pytest.mark.asyncio
    async def test_check_health_success(self, mock_kr8s_api):
        """Test successful health check."""
        adapter = KubernetesAdapter()

        with patch("kr8s.asyncio.api", return_value=mock_kr8s_api):
            await adapter.connect()
            health = await adapter.check_health()

            assert health["status"] == "healthy"
            assert health["server_version"] == "v1.28.0"

    @pytest.mark.asyncio
    async def test_check_health_failure(self, mock_kr8s_api):
        """Test health check when API fails."""
        adapter = KubernetesAdapter()

        with patch("kr8s.asyncio.api", return_value=mock_kr8s_api):
            await adapter.connect()
            # Now make version fail after connection
            mock_kr8s_api.version.side_effect = Exception("API error")
            health = await adapter.check_health()

            assert health["status"] == "unhealthy"
            assert "error" in health

    @pytest.mark.asyncio
    async def test_get_server_version(self, mock_kr8s_api):
        """Test getting server version."""
        adapter = KubernetesAdapter()

        with patch("kr8s.asyncio.api", return_value=mock_kr8s_api):
            await adapter.connect()
            version = await adapter.get_server_version()

            assert version == "v1.28.0"


# =============================================================================
# Resource Operation Tests
# =============================================================================


class TestKubernetesAdapterResourceOperations:
    """Tests for resource operations."""

    @pytest.mark.asyncio
    async def test_get_resource(self, mock_kr8s_api):
        """Test getting a resource."""
        mock_resource = MagicMock()
        mock_resource.raw = {
            "apiVersion": "v1",
            "kind": "Pod",
            "metadata": {"name": "test-pod", "namespace": "default"},
        }

        # kr8s.asyncio.get returns an async generator
        async def mock_get_generator(*args, **kwargs):
            yield mock_resource

        adapter = KubernetesAdapter()

        with (
            patch("kr8s.asyncio.api", return_value=mock_kr8s_api),
            patch("kr8s.asyncio.get", mock_get_generator),
        ):
            await adapter.connect()
            result = await adapter.get("Pod", "test-pod", namespace="default")

            assert result["kind"] == "Pod"
            assert result["metadata"]["name"] == "test-pod"

    @pytest.mark.asyncio
    async def test_get_resource_not_found(self, mock_kr8s_api):
        """Test getting a non-existent resource."""

        # Empty async generator to simulate not found
        async def mock_empty_generator(*args, **kwargs):
            return
            yield  # Make this a generator

        adapter = KubernetesAdapter()

        with (
            patch("kr8s.asyncio.api", return_value=mock_kr8s_api),
            patch("kr8s.asyncio.get", mock_empty_generator),
        ):
            await adapter.connect()

            with pytest.raises(ResourceNotFoundError) as exc_info:
                await adapter.get("Pod", "nonexistent")

            assert "not found" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_list_resources(self, mock_kr8s_api):
        """Test listing resources."""
        mock_resources = [
            MagicMock(raw={"metadata": {"name": "pod-1"}}),
            MagicMock(raw={"metadata": {"name": "pod-2"}}),
        ]

        # Create an async generator for kr8s.asyncio.get
        async def mock_get_generator(*args, **kwargs):
            for r in mock_resources:
                yield r

        adapter = KubernetesAdapter()

        with (
            patch("kr8s.asyncio.api", return_value=mock_kr8s_api),
            patch("kr8s.asyncio.get", mock_get_generator),
        ):
            await adapter.connect()
            result = await adapter.list("Pod", namespace="default")

            assert len(result) == 2
            assert result[0]["metadata"]["name"] == "pod-1"

    @pytest.mark.asyncio
    async def test_list_resources_empty(self, mock_kr8s_api):
        """Test listing resources returns empty list."""

        # Create an empty async generator for kr8s.asyncio.get
        async def mock_get_generator(*args, **kwargs):
            return
            yield  # Makes this an async generator

        adapter = KubernetesAdapter()

        with (
            patch("kr8s.asyncio.api", return_value=mock_kr8s_api),
            patch("kr8s.asyncio.get", mock_get_generator),
        ):
            await adapter.connect()
            result = await adapter.list("Pod", namespace="default")

            assert result == []

    @pytest.mark.asyncio
    async def test_create_resource(self, mock_kr8s_api):
        """Test creating a resource."""
        resource_dict = {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {"name": "test-cm", "namespace": "default"},
            "data": {"key": "value"},
        }

        mock_obj = MagicMock()
        mock_obj.raw = resource_dict
        mock_obj.create = AsyncMock()

        adapter = KubernetesAdapter()

        with patch("kr8s.asyncio.api", return_value=mock_kr8s_api):
            await adapter.connect()

            with patch("mosk_mcp.adapters.kubernetes.APIObject", return_value=mock_obj):
                result = await adapter.create(resource_dict)

                mock_obj.create.assert_called_once()
                assert result == resource_dict

    @pytest.mark.asyncio
    async def test_patch_resource(self, mock_kr8s_api):
        """Test patching a resource."""
        mock_resource = MagicMock()
        mock_resource.raw = {"metadata": {"name": "test-pod"}, "spec": {"updated": True}}
        mock_resource.patch = AsyncMock()

        adapter = KubernetesAdapter()

        with (
            patch("kr8s.asyncio.api", return_value=mock_kr8s_api),
            patch("kr8s.asyncio.get", new_callable=AsyncMock, return_value=mock_resource),
        ):
            await adapter.connect()
            await adapter.patch(
                "Pod",
                "test-pod",
                {"spec": {"updated": True}},
                namespace="default",
            )

            mock_resource.patch.assert_called_once()

    @pytest.mark.asyncio
    async def test_delete_resource(self, mock_kr8s_api):
        """Test deleting a resource."""
        mock_resource = MagicMock()
        mock_resource.delete = AsyncMock()

        adapter = KubernetesAdapter()

        with (
            patch("kr8s.asyncio.api", return_value=mock_kr8s_api),
            patch("kr8s.asyncio.get", new_callable=AsyncMock, return_value=mock_resource),
        ):
            await adapter.connect()
            await adapter.delete("Pod", "test-pod", namespace="default")

            mock_resource.delete.assert_called_once()

    @pytest.mark.asyncio
    async def test_delete_resource_not_found(self, mock_kr8s_api):
        """Test deleting a non-existent resource."""
        adapter = KubernetesAdapter()

        with (
            patch("kr8s.asyncio.api", return_value=mock_kr8s_api),
            patch("kr8s.asyncio.get", new_callable=AsyncMock, return_value=None),
        ):
            await adapter.connect()

            with pytest.raises(ResourceNotFoundError):
                await adapter.delete("Pod", "nonexistent")


# =============================================================================
# Cluster-Scoped Resource Tests
# =============================================================================


class TestClusterScopedResources:
    """Tests for cluster-scoped resource handling."""

    def test_cluster_scoped_kinds_defined(self):
        """Test that cluster-scoped kinds are properly defined."""
        assert "Node" in KubernetesAdapter.CLUSTER_SCOPED_KINDS
        assert "Namespace" in KubernetesAdapter.CLUSTER_SCOPED_KINDS
        assert "PersistentVolume" in KubernetesAdapter.CLUSTER_SCOPED_KINDS
        assert "ClusterRole" in KubernetesAdapter.CLUSTER_SCOPED_KINDS
        assert "ClusterRoleBinding" in KubernetesAdapter.CLUSTER_SCOPED_KINDS

    def test_namespaced_resources_not_in_cluster_scoped(self):
        """Test that namespaced resources are not in cluster-scoped set."""
        assert "Pod" not in KubernetesAdapter.CLUSTER_SCOPED_KINDS
        assert "Deployment" not in KubernetesAdapter.CLUSTER_SCOPED_KINDS
        assert "ConfigMap" not in KubernetesAdapter.CLUSTER_SCOPED_KINDS
        assert "Secret" not in KubernetesAdapter.CLUSTER_SCOPED_KINDS

    @pytest.mark.asyncio
    async def test_get_node_uses_cluster_scope(self, mock_kr8s_api):
        """Test that getting a Node uses cluster scope (no namespace)."""
        import kr8s

        mock_resource = MagicMock()
        mock_resource.raw = {
            "apiVersion": "v1",
            "kind": "Node",
            "metadata": {"name": "worker-01"},
            "status": {"conditions": []},
        }

        # kr8s.asyncio.get returns an async generator
        captured_kwargs = {}

        async def capturing_get_generator(*args, **kwargs):
            captured_kwargs.update(kwargs)
            yield mock_resource

        adapter = KubernetesAdapter(namespace="default")

        with (
            patch("kr8s.asyncio.api", return_value=mock_kr8s_api),
            patch("kr8s.asyncio.get", capturing_get_generator),
        ):
            await adapter.connect()
            result = await adapter.get("Node", "worker-01")

            # Verify kr8s.ALL was passed for namespace
            assert captured_kwargs.get("namespace") == kr8s.ALL

            assert result["kind"] == "Node"
            assert result["metadata"]["name"] == "worker-01"

    @pytest.mark.asyncio
    async def test_get_node_ignores_namespace_param(self, mock_kr8s_api):
        """Test that namespace parameter is ignored for cluster-scoped resources."""
        import kr8s

        mock_resource = MagicMock()
        mock_resource.raw = {
            "apiVersion": "v1",
            "kind": "Node",
            "metadata": {"name": "worker-01"},
        }

        captured_kwargs = {}

        async def capturing_get_generator(*args, **kwargs):
            captured_kwargs.update(kwargs)
            yield mock_resource

        adapter = KubernetesAdapter()

        with (
            patch("kr8s.asyncio.api", return_value=mock_kr8s_api),
            patch("kr8s.asyncio.get", capturing_get_generator),
        ):
            await adapter.connect()
            # Pass a namespace - it should be ignored for Node
            await adapter.get("Node", "worker-01", namespace="kube-system")

            # Verify kr8s.ALL was passed regardless of namespace param
            assert captured_kwargs.get("namespace") == kr8s.ALL

    @pytest.mark.asyncio
    async def test_list_nodes_uses_cluster_scope(self, mock_kr8s_api):
        """Test that listing Nodes uses cluster scope."""

        mock_resources = [
            MagicMock(raw={"metadata": {"name": "worker-01"}}),
            MagicMock(raw={"metadata": {"name": "worker-02"}}),
        ]

        async def mock_get_generator(*args, **kwargs):
            for r in mock_resources:
                yield r

        adapter = KubernetesAdapter(namespace="default")

        with (
            patch("kr8s.asyncio.api", return_value=mock_kr8s_api),
            patch("kr8s.asyncio.get", mock_get_generator),
        ):
            await adapter.connect()
            result = await adapter.list("Node")

            assert len(result) == 2
            assert result[0]["metadata"]["name"] == "worker-01"

    @pytest.mark.asyncio
    async def test_get_pod_uses_namespace(self, mock_kr8s_api):
        """Test that getting a Pod (namespaced resource) uses namespace."""
        mock_resource = MagicMock()
        mock_resource.raw = {
            "apiVersion": "v1",
            "kind": "Pod",
            "metadata": {"name": "my-pod", "namespace": "my-namespace"},
        }

        captured_kwargs = {}

        async def capturing_get_generator(*args, **kwargs):
            captured_kwargs.update(kwargs)
            yield mock_resource

        adapter = KubernetesAdapter(namespace="default")

        with (
            patch("kr8s.asyncio.api", return_value=mock_kr8s_api),
            patch("kr8s.asyncio.get", capturing_get_generator),
        ):
            await adapter.connect()
            await adapter.get("Pod", "my-pod", namespace="my-namespace")

            # Verify namespace was passed for namespaced resource
            assert captured_kwargs.get("namespace") == "my-namespace"


# =============================================================================
# Custom Resource Tests
# =============================================================================


class TestKubernetesAdapterCustomResources:
    """Tests for custom resource operations."""

    @pytest.mark.asyncio
    async def test_list_custom_resources(self, mock_kr8s_api):
        """Test listing custom resources."""
        mock_resources = [
            MagicMock(raw={"metadata": {"name": "machine-1"}}),
            MagicMock(raw={"metadata": {"name": "machine-2"}}),
        ]

        # Create an async generator for the list() method
        async def mock_list_generator(*args, **kwargs):
            for r in mock_resources:
                yield r

        mock_resource_class = MagicMock()
        mock_resource_class.list = mock_list_generator

        adapter = KubernetesAdapter()

        with patch("kr8s.asyncio.api", return_value=mock_kr8s_api):
            await adapter.connect()

            with patch("kr8s.asyncio.objects.new_class", return_value=mock_resource_class):
                result = await adapter.list_custom_resources(
                    group="kaas.mirantis.com",
                    version="v1alpha1",
                    plural="machines",
                )

                assert len(result) == 2

    @pytest.mark.asyncio
    async def test_get_custom_resource(self, mock_kr8s_api):
        """Test getting a custom resource."""
        mock_resource = MagicMock()
        mock_resource.raw = {
            "apiVersion": "kaas.mirantis.com/v1alpha1",
            "kind": "Machine",
            "metadata": {"name": "compute-01", "namespace": "default"},
        }

        mock_resource_class = MagicMock()
        mock_resource_class.get = AsyncMock(return_value=mock_resource)

        adapter = KubernetesAdapter()

        with patch("kr8s.asyncio.api", return_value=mock_kr8s_api):
            await adapter.connect()

            with patch("kr8s.asyncio.objects.new_class", return_value=mock_resource_class):
                result = await adapter.get_custom_resource(
                    group="kaas.mirantis.com",
                    version="v1alpha1",
                    plural="machines",
                    name="compute-01",
                )

                assert result["kind"] == "Machine"
                assert result["metadata"]["name"] == "compute-01"

    @pytest.mark.asyncio
    async def test_get_custom_resource_not_found(self, mock_kr8s_api):
        """Test getting non-existent custom resource."""
        mock_resource_class = MagicMock()
        mock_resource_class.get = AsyncMock(return_value=None)

        adapter = KubernetesAdapter()

        with patch("kr8s.asyncio.api", return_value=mock_kr8s_api):
            await adapter.connect()

            with (
                patch("kr8s.asyncio.objects.new_class", return_value=mock_resource_class),
                pytest.raises(ResourceNotFoundError),
            ):
                await adapter.get_custom_resource(
                    group="kaas.mirantis.com",
                    version="v1alpha1",
                    plural="machines",
                    name="nonexistent",
                )


# =============================================================================
# MOSK Convenience Method Tests
# =============================================================================


class TestKubernetesAdapterMOSKMethods:
    """Tests for MOSK-specific convenience methods."""

    @pytest.mark.asyncio
    async def test_list_machines(self, mock_kr8s_api):
        """Test listing machines."""
        mock_resources = [MagicMock(raw={"metadata": {"name": "compute-01"}})]

        # Create an async generator for the list() method
        async def mock_list_generator(*args, **kwargs):
            for r in mock_resources:
                yield r

        mock_resource_class = MagicMock()
        mock_resource_class.list = mock_list_generator

        adapter = KubernetesAdapter()

        with patch("kr8s.asyncio.api", return_value=mock_kr8s_api):
            await adapter.connect()

            with patch("kr8s.asyncio.objects.new_class", return_value=mock_resource_class):
                result = await adapter.list_machines()

                assert len(result) == 1
                assert result[0]["metadata"]["name"] == "compute-01"

    @pytest.mark.asyncio
    async def test_get_machine(self, mock_kr8s_api):
        """Test getting a machine."""
        mock_resource = MagicMock()
        mock_resource.raw = {
            "apiVersion": "kaas.mirantis.com/v1alpha1",
            "kind": "Machine",
            "metadata": {"name": "compute-01"},
        }
        mock_resource_class = MagicMock()
        mock_resource_class.get = AsyncMock(return_value=mock_resource)

        adapter = KubernetesAdapter()

        with patch("kr8s.asyncio.api", return_value=mock_kr8s_api):
            await adapter.connect()

            with patch("kr8s.asyncio.objects.new_class", return_value=mock_resource_class):
                result = await adapter.get_machine("compute-01")

                assert result["metadata"]["name"] == "compute-01"

    @pytest.mark.asyncio
    async def test_list_openstack_deployments(self, mock_kr8s_api):
        """Test listing OpenStack deployments."""
        mock_resources = [MagicMock(raw={"metadata": {"name": "openstack"}})]

        # Create an async generator for the list() method
        async def mock_list_generator(*args, **kwargs):
            for r in mock_resources:
                yield r

        mock_resource_class = MagicMock()
        mock_resource_class.list = mock_list_generator

        adapter = KubernetesAdapter()

        with patch("kr8s.asyncio.api", return_value=mock_kr8s_api):
            await adapter.connect()

            with patch("kr8s.asyncio.objects.new_class", return_value=mock_resource_class):
                result = await adapter.list_openstack_deployments()

                assert len(result) == 1


# =============================================================================
# Factory Function Tests
# =============================================================================


class TestKubernetesAdapterFactory:
    """Tests for factory functions."""

    def test_from_settings(self, settings):
        """Test creating adapter from settings."""
        adapter = KubernetesAdapter.from_settings(settings)

        assert adapter.namespace == "test-namespace"

    @pytest.mark.asyncio
    async def test_kubernetes_client_context_manager(self, mock_kr8s_api, settings):
        """Test kubernetes_client context manager."""
        with patch("kr8s.asyncio.api", return_value=mock_kr8s_api):
            async with kubernetes_client(settings=settings) as k8s:
                assert k8s.is_connected
                assert k8s.namespace == "test-namespace"

            assert not k8s.is_connected


# =============================================================================
# CRD Mapping Tests
# =============================================================================


class TestCRDMappings:
    """Tests for CRD mappings."""

    def test_crd_mappings_exist(self):
        """Test that all expected CRD mappings exist."""
        expected_crds = [
            "machines",
            "baremetalhostinventories",
            "baremetalhostprofiles",
            "ipamhosts",
            "l2templates",
            "openstackdeployments",
            "kaascephoperationrequests",
            "nodemaintenancerequests",
        ]

        for crd in expected_crds:
            assert crd in KubernetesAdapter.CRD_MAPPINGS
            mapping = KubernetesAdapter.CRD_MAPPINGS[crd]
            assert "group" in mapping
            assert "version" in mapping
            assert "plural" in mapping

    def test_machines_crd_mapping(self):
        """Test machines CRD mapping."""
        mapping = KubernetesAdapter.CRD_MAPPINGS["machines"]

        # Machine CRD is part of cluster.k8s.io API group
        assert mapping["group"] == "cluster.k8s.io"
        assert mapping["version"] == "v1alpha1"
        assert mapping["plural"] == "machines"

    def test_osdpl_crd_mapping(self):
        """Test OpenStackDeployment CRD mapping."""
        mapping = KubernetesAdapter.CRD_MAPPINGS["openstackdeployments"]

        assert mapping["group"] == "lcm.mirantis.com"
        assert mapping["version"] == "v1alpha1"
