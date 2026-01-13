"""Unit tests for get_component_versions tool."""

from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from mosk_mcp.core.exceptions import ResourceNotFoundError, ToolExecutionError
from mosk_mcp.tools.operations_visibility.get_component_versions import (
    _extract_version_from_image,
    _get_service_deployments,
    _parse_deployment_version,
    get_component_versions,
)
from mosk_mcp.tools.operations_visibility.models import (
    GetComponentVersionsInput,
)


class TestExtractVersionFromImage:
    """Tests for _extract_version_from_image helper."""

    def test_standard_tag(self):
        """Test extracting standard version tag."""
        result = _extract_version_from_image("docker.io/nova:antelope")
        assert result == "antelope"

    def test_semver_tag(self):
        """Test extracting semver tag."""
        result = _extract_version_from_image("registry.example.com/keystone:2024.1.0")
        assert result == "2024.1.0"

    def test_sha256_digest(self):
        """Test handling sha256 digest - extracts hash after @sha256:."""
        result = _extract_version_from_image("registry.example.com/glance@sha256:abc123...")
        # The function extracts just the hash part after sha256:
        assert result == "abc123..."

    def test_no_tag(self):
        """Test image without tag."""
        result = _extract_version_from_image("docker.io/neutron")
        assert result == "latest"

    def test_empty_image(self):
        """Test empty image string."""
        result = _extract_version_from_image("")
        assert result == "unknown"

    def test_none_image(self):
        """Test None image."""
        result = _extract_version_from_image(None)
        assert result == "unknown"

    def test_complex_registry_path(self):
        """Test image with complex registry path."""
        result = _extract_version_from_image("gcr.io/project/subpath/cinder-api:yoga")
        assert result == "yoga"


class TestGetServiceDeployments:
    """Tests for _get_service_deployments helper."""

    def test_filter_by_name(self):
        """Test filtering deployments by name."""
        deployments = [
            {"metadata": {"name": "nova-api", "labels": {}}},
            {"metadata": {"name": "nova-scheduler", "labels": {}}},
            {"metadata": {"name": "neutron-server", "labels": {}}},
        ]

        result = _get_service_deployments(deployments, "nova")

        assert len(result) == 2
        names = [d["metadata"]["name"] for d in result]
        assert "nova-api" in names
        assert "nova-scheduler" in names

    def test_filter_by_application_label(self):
        """Test filtering by application label."""
        deployments = [
            {
                "metadata": {
                    "name": "ks-api",
                    "labels": {"application": "keystone"},
                }
            },
            {
                "metadata": {
                    "name": "gl-api",
                    "labels": {"application": "glance"},
                }
            },
        ]

        result = _get_service_deployments(deployments, "keystone")

        assert len(result) == 1
        assert result[0]["metadata"]["name"] == "ks-api"

    def test_filter_by_component_label(self):
        """Test filtering by component label."""
        deployments = [
            {
                "metadata": {
                    "name": "api-server",
                    "labels": {"component": "heat-api"},
                }
            },
            {
                "metadata": {
                    "name": "engine",
                    "labels": {"component": "heat-engine"},
                }
            },
            {
                "metadata": {
                    "name": "other",
                    "labels": {"component": "cinder-api"},
                }
            },
        ]

        result = _get_service_deployments(deployments, "heat")

        assert len(result) == 2

    def test_no_matches(self):
        """Test no matching deployments."""
        deployments = [
            {"metadata": {"name": "nova-api", "labels": {}}},
        ]

        result = _get_service_deployments(deployments, "cinder")

        assert len(result) == 0

    def test_empty_deployments(self):
        """Test empty deployments list."""
        result = _get_service_deployments([], "nova")
        assert len(result) == 0


class TestParseDeploymentVersion:
    """Tests for _parse_deployment_version helper."""

    def test_parse_deployment_with_image(self):
        """Test parsing deployment with container image."""
        deployment = {
            "metadata": {
                "name": "nova-api",
                "labels": {"component": "api"},
                "annotations": {},
            },
            "spec": {"template": {"spec": {"containers": [{"image": "docker.io/nova:antelope"}]}}},
            "status": {
                "replicas": 3,
                "updatedReplicas": 3,
                "availableReplicas": 3,
            },
        }

        result = _parse_deployment_version(
            deployment=deployment,
            service_name="nova",
            target_version="antelope",
            include_containers=True,
        )

        assert result.component == "nova-api"
        assert result.current_version == "antelope"
        assert result.target_version == "antelope"
        assert result.is_current is True
        assert result.image == "docker.io/nova:antelope"

    def test_parse_deployment_out_of_sync(self):
        """Test parsing deployment that is out of sync."""
        deployment = {
            "metadata": {
                "name": "nova-scheduler",
                "labels": {"component": "scheduler"},
                "annotations": {},
            },
            "spec": {"template": {"spec": {"containers": [{"image": "docker.io/nova:yoga"}]}}},
            "status": {
                "replicas": 3,
                "updatedReplicas": 1,
                "availableReplicas": 2,
            },
        }

        result = _parse_deployment_version(
            deployment=deployment,
            service_name="nova",
            target_version="antelope",
            include_containers=True,
        )

        assert result.current_version == "yoga"
        assert result.target_version == "antelope"
        assert result.is_current is False

    def test_parse_deployment_no_containers(self):
        """Test parsing deployment without containers."""
        deployment = {
            "metadata": {
                "name": "test-deploy",
                "labels": {},
                "annotations": {},
            },
            "spec": {"template": {"spec": {"containers": []}}},
            "status": {},
        }

        result = _parse_deployment_version(
            deployment=deployment,
            service_name="test",
            target_version="antelope",
            include_containers=False,
        )

        assert result.current_version == "unknown"
        assert result.image is None

    def test_parse_deployment_with_chart_version(self):
        """Test parsing deployment with Helm chart annotation."""
        deployment = {
            "metadata": {
                "name": "keystone-api",
                "labels": {"component": "keystone-api"},
                "annotations": {"meta.helm.sh/release-name": "keystone"},
            },
            "spec": {"template": {"spec": {"containers": [{"image": "keystone:antelope"}]}}},
            "status": {
                "replicas": 2,
                "updatedReplicas": 2,
                "availableReplicas": 2,
            },
        }

        result = _parse_deployment_version(
            deployment=deployment,
            service_name="keystone",
            target_version="antelope",
            include_containers=True,
        )

        assert result.chart_version == "keystone"


class TestGetComponentVersionsInput:
    """Tests for GetComponentVersionsInput model."""

    def test_required_name(self):
        """Test name is required."""
        with pytest.raises(Exception):  # Pydantic validation error
            GetComponentVersionsInput()

    def test_default_values(self):
        """Test default values."""
        input_data = GetComponentVersionsInput(name="mos")

        assert input_data.name == "mos"
        assert input_data.namespace == "openstack"
        assert input_data.include_containers is False

    def test_custom_values(self):
        """Test custom values."""
        input_data = GetComponentVersionsInput(
            name="custom-osdpl",
            namespace="custom-ns",
            include_containers=True,
        )

        assert input_data.name == "custom-osdpl"
        assert input_data.namespace == "custom-ns"
        assert input_data.include_containers is True


class TestGetComponentVersionsFunction:
    """Tests for get_component_versions function."""

    @pytest.fixture
    def mock_k8s_adapter(self):
        """Create mock Kubernetes adapter."""
        adapter = AsyncMock()
        adapter.connect = AsyncMock()
        return adapter

    @pytest.fixture
    def mock_osdpl(self):
        """Create mock OSDPL resource."""
        return {
            "metadata": {"name": "mos", "namespace": "openstack"},
            "spec": {
                "openstack_version": "antelope",
                "services": {
                    "nova": {"enabled": True},
                    "neutron": {"enabled": True},
                    "keystone": {"enabled": True},
                },
            },
            "status": {
                "openstack_version": "antelope",
                "version": "1.2.3",
            },
        }

    @pytest.fixture
    def mock_deployments(self):
        """Create mock deployments."""
        return [
            {
                "metadata": {
                    "name": "nova-api",
                    "labels": {"application": "nova", "component": "api"},
                    "annotations": {},
                },
                "spec": {"template": {"spec": {"containers": [{"image": "nova:antelope"}]}}},
                "status": {
                    "replicas": 3,
                    "updatedReplicas": 3,
                    "availableReplicas": 3,
                },
            },
            {
                "metadata": {
                    "name": "keystone-api",
                    "labels": {"application": "keystone", "component": "api"},
                    "annotations": {},
                },
                "spec": {"template": {"spec": {"containers": [{"image": "keystone:antelope"}]}}},
                "status": {
                    "replicas": 2,
                    "updatedReplicas": 2,
                    "availableReplicas": 2,
                },
            },
        ]

    @pytest.mark.asyncio
    async def test_get_versions_success(self, mock_k8s_adapter, mock_osdpl, mock_deployments):
        """Test successful version retrieval."""
        mock_k8s_adapter.get_openstack_deployment = AsyncMock(return_value=mock_osdpl)
        mock_k8s_adapter.list = AsyncMock(side_effect=[mock_deployments, []])

        result = await get_component_versions(
            mock_k8s_adapter,
            GetComponentVersionsInput(name="mos"),
        )

        assert result.name == "mos"
        assert result.openstack_version_target == "antelope"
        assert result.openstack_version_current == "antelope"
        assert result.osdpl_controller_version == "1.2.3"
        assert len(result.components) > 0
        assert result.versions_match is True

    @pytest.mark.asyncio
    async def test_get_versions_with_out_of_sync(self, mock_k8s_adapter, mock_osdpl):
        """Test version retrieval with out-of-sync components."""
        # Create deployments with different versions
        deployments = [
            {
                "metadata": {
                    "name": "nova-api",
                    "labels": {"application": "nova"},
                    "annotations": {},
                },
                "spec": {
                    "template": {
                        "spec": {
                            "containers": [{"image": "nova:yoga"}]  # Old version
                        }
                    }
                },
                "status": {
                    "replicas": 3,
                    "updatedReplicas": 1,  # Not fully updated
                    "availableReplicas": 2,
                },
            },
        ]

        mock_k8s_adapter.get_openstack_deployment = AsyncMock(return_value=mock_osdpl)
        mock_k8s_adapter.list = AsyncMock(side_effect=[deployments, []])

        result = await get_component_versions(
            mock_k8s_adapter,
            GetComponentVersionsInput(name="mos"),
        )

        assert result.versions_match is False
        assert len(result.out_of_sync_components) > 0

    @pytest.mark.asyncio
    async def test_get_versions_with_containers(
        self, mock_k8s_adapter, mock_osdpl, mock_deployments
    ):
        """Test version retrieval including container images."""
        mock_k8s_adapter.get_openstack_deployment = AsyncMock(return_value=mock_osdpl)
        mock_k8s_adapter.list = AsyncMock(side_effect=[mock_deployments, []])

        result = await get_component_versions(
            mock_k8s_adapter,
            GetComponentVersionsInput(name="mos", include_containers=True),
        )

        # Check that images are included
        assert any(c.image is not None for c in result.components)

    @pytest.mark.asyncio
    async def test_get_versions_osdpl_not_found(self, mock_k8s_adapter):
        """Test when OSDPL is not found."""
        mock_k8s_adapter.get_openstack_deployment = AsyncMock(
            side_effect=ResourceNotFoundError("OSDPL 'mos' not found")
        )

        with pytest.raises(ResourceNotFoundError):
            await get_component_versions(
                mock_k8s_adapter,
                GetComponentVersionsInput(name="mos"),
            )

    @pytest.mark.asyncio
    async def test_get_versions_api_error(self, mock_k8s_adapter, mock_osdpl):
        """Test API error handling."""
        mock_k8s_adapter.get_openstack_deployment = AsyncMock(return_value=mock_osdpl)
        mock_k8s_adapter.list = AsyncMock(side_effect=Exception("Connection failed"))

        with pytest.raises(ToolExecutionError) as exc_info:
            await get_component_versions(
                mock_k8s_adapter,
                GetComponentVersionsInput(name="mos"),
            )

        assert "Failed to get component versions" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_get_versions_with_disabled_service(self, mock_k8s_adapter):
        """Test that disabled services are skipped."""
        osdpl = {
            "metadata": {"name": "mos"},
            "spec": {
                "openstack_version": "antelope",
                "services": {
                    "nova": {"enabled": True},
                    "heat": {"enabled": False},  # Disabled
                },
            },
            "status": {"version": "1.0.0"},
        }
        deployments = [
            {
                "metadata": {
                    "name": "nova-api",
                    "labels": {"application": "nova"},
                    "annotations": {},
                },
                "spec": {"template": {"spec": {"containers": [{"image": "nova:antelope"}]}}},
                "status": {
                    "replicas": 1,
                    "updatedReplicas": 1,
                    "availableReplicas": 1,
                },
            },
            {
                "metadata": {
                    "name": "heat-api",
                    "labels": {"application": "heat"},
                    "annotations": {},
                },
                "spec": {"template": {"spec": {"containers": [{"image": "heat:antelope"}]}}},
                "status": {"replicas": 1},
            },
        ]

        mock_k8s_adapter.get_openstack_deployment = AsyncMock(return_value=osdpl)
        mock_k8s_adapter.list = AsyncMock(side_effect=[deployments, []])

        result = await get_component_versions(
            mock_k8s_adapter,
            GetComponentVersionsInput(name="mos"),
        )

        # Heat should not be included since it's disabled
        component_names = [c.component for c in result.components]
        assert "nova-api" in component_names
        assert "heat-api" not in component_names

    @pytest.mark.asyncio
    async def test_get_versions_with_statefulsets(self, mock_k8s_adapter, mock_osdpl):
        """Test version retrieval with statefulsets."""
        deployments = []
        statefulsets = [
            {
                "metadata": {
                    "name": "nova-conductor",
                    "labels": {"application": "nova"},
                    "annotations": {},
                },
                "spec": {"template": {"spec": {"containers": [{"image": "nova:antelope"}]}}},
                "status": {
                    "replicas": 3,
                    "updatedReplicas": 3,
                    "currentReplicas": 3,
                },
            },
        ]

        mock_k8s_adapter.get_openstack_deployment = AsyncMock(return_value=mock_osdpl)
        mock_k8s_adapter.list = AsyncMock(side_effect=[deployments, statefulsets])

        result = await get_component_versions(
            mock_k8s_adapter,
            GetComponentVersionsInput(name="mos"),
        )

        assert len(result.components) > 0

    @pytest.mark.asyncio
    async def test_get_versions_with_mcc_adapter(
        self, mock_k8s_adapter, mock_osdpl, mock_deployments
    ):
        """Test version retrieval with MCC adapter."""
        mock_k8s_adapter.get_openstack_deployment = AsyncMock(return_value=mock_osdpl)
        mock_k8s_adapter.list = AsyncMock(side_effect=[mock_deployments, []])

        # Create MCC adapter mock
        mock_mcc_adapter = AsyncMock()
        mock_mcc_adapter.list_custom_resources = AsyncMock(
            side_effect=[
                # First call: Management cluster
                [
                    {
                        "metadata": {"name": "kaas-mgmt"},
                        "spec": {
                            "providerSpec": {
                                "value": {
                                    "kaas": {"release": "kaas-2.28"},
                                    "release": "mcc-release-1.0",
                                }
                            }
                        },
                    }
                ],
                # Second call: MOSK cluster
                [
                    {
                        "metadata": {"name": "mos", "namespace": "lab"},
                        "spec": {"providerSpec": {"value": {"release": "mosk-21-0-0"}}},
                    }
                ],
                # Third call: LCM machines
                [
                    {
                        "metadata": {"name": "machine-01"},
                        "status": {
                            "agentVersion": "2.28.0",
                            "components": {"ucpVersion": "1.0.0"},
                        },
                    }
                ],
            ]
        )

        result = await get_component_versions(
            mock_k8s_adapter,
            GetComponentVersionsInput(name="mos"),
            mcc_adapter=mock_mcc_adapter,
        )

        assert result.mcc_kaas_release == "kaas-2.28"
        assert result.mcc_cluster_release == "mcc-release-1.0"
        assert result.mosk_release == "mosk-21-0-0"
        assert result.lcm_agent_version == "2.28.0"
        assert result.ucp_version == "1.0.0"

    @pytest.mark.asyncio
    async def test_get_versions_mcc_adapter_error_handled(
        self, mock_k8s_adapter, mock_osdpl, mock_deployments
    ):
        """Test that MCC adapter errors are handled gracefully."""
        mock_k8s_adapter.get_openstack_deployment = AsyncMock(return_value=mock_osdpl)
        mock_k8s_adapter.list = AsyncMock(side_effect=[mock_deployments, []])

        mock_mcc_adapter = AsyncMock()
        mock_mcc_adapter.list_custom_resources = AsyncMock(
            side_effect=Exception("MCC connection failed")
        )

        # Should not raise, just skip MCC version info
        result = await get_component_versions(
            mock_k8s_adapter,
            GetComponentVersionsInput(name="mos"),
            mcc_adapter=mock_mcc_adapter,
        )

        # MCC info should be None but function should complete
        assert result.mcc_kaas_release is None
        assert result.mosk_release is None

    @pytest.mark.asyncio
    async def test_timestamp_set(self, mock_k8s_adapter, mock_osdpl, mock_deployments):
        """Test timestamp is set in result."""
        mock_k8s_adapter.get_openstack_deployment = AsyncMock(return_value=mock_osdpl)
        mock_k8s_adapter.list = AsyncMock(side_effect=[mock_deployments, []])

        result = await get_component_versions(
            mock_k8s_adapter,
            GetComponentVersionsInput(name="mos"),
        )

        assert result.timestamp is not None
        # Verify valid ISO format
        datetime.fromisoformat(result.timestamp.replace("Z", "+00:00"))
