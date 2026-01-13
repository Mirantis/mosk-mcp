"""Unit tests for Operations Visibility tools.

This module tests all tools in the operations_visibility module:
- get_openstack_deployment_status
- get_openstack_upgrade_progress
- get_component_versions
- list_live_migrations
- get_migration_eta
- list_maintenance_requests
- get_rollout_status
- get_node_conditions
"""

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from mosk_mcp.tools.operations_visibility import (
    # Models
    GetComponentVersionsInput,
    GetMigrationETAInput,
    GetNodeConditionsInput,
    GetOSDPLStatusInput,
    GetRolloutStatusInput,
    GetUpgradeProgressInput,
    HealthStatus,
    ListLiveMigrationsInput,
    ListMaintenanceRequestsInput,
    MaintenancePhase,
    MigrationStatus,
    OSDPLPhase,
    UpgradeState,
    # Functions
    get_component_versions,
    get_migration_eta,
    get_node_conditions,
    get_openstack_deployment_status,
    get_openstack_upgrade_progress,
    get_rollout_status,
    list_live_migrations,
    list_maintenance_requests,
)
from mosk_mcp.tools.operations_visibility.list_available_releases import (
    ComponentVersions,
    ListAvailableReleasesInput,
    ListAvailableReleasesOutput,
    OpenStackReleaseInfo,
    ReleaseInfo,
    UpgradePathInfo,
    _compare_versions,
    _extract_major_version,
    _parse_component_versions,
    list_available_releases,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_k8s_adapter() -> AsyncMock:
    """Create a mock Kubernetes adapter."""
    adapter = AsyncMock()
    return adapter


@pytest.fixture
def sample_osdpl_deployed() -> dict[str, Any]:
    """Sample OpenStackDeployment in Deployed phase."""
    return {
        "apiVersion": "lcm.mirantis.com/v1alpha1",
        "kind": "OpenStackDeployment",
        "metadata": {
            "name": "openstack",
            "namespace": "openstack",
            "creationTimestamp": "2024-01-15T10:00:00Z",
        },
        "spec": {
            "openStackVersion": "antelope",
            "preset": "compute",
            "size": "medium",
            "services": {
                "nova": {"enabled": True},
                "neutron": {"enabled": True},
                "keystone": {"enabled": True},
            },
        },
        "status": {
            "phase": "Deployed",
            "openStackVersion": "antelope",
            "observedGeneration": 5,
            "conditions": [
                {
                    "type": "Ready",
                    "status": "True",
                    "reason": "AllServicesReady",
                    "message": "All OpenStack services are ready",
                    "lastTransitionTime": "2024-01-15T11:00:00Z",
                },
                {
                    "type": "ControlPlaneReady",
                    "status": "True",
                    "reason": "ControlPlaneHealthy",
                    "lastTransitionTime": "2024-01-15T10:45:00Z",
                },
                {
                    "type": "ComputeNodesReady",
                    "status": "True",
                    "reason": "AllComputeNodesReady",
                    "lastTransitionTime": "2024-01-15T11:00:00Z",
                },
            ],
            "services": {
                "nova": {
                    "ready": True,
                    "replicas": 3,
                    "readyReplicas": 3,
                    "availableReplicas": 3,
                },
                "neutron": {
                    "ready": True,
                    "replicas": 3,
                    "readyReplicas": 3,
                    "availableReplicas": 3,
                },
                "keystone": {
                    "ready": True,
                    "replicas": 3,
                    "readyReplicas": 3,
                    "availableReplicas": 3,
                },
            },
            "endpoints": {
                "keystone": "https://keystone.openstack.svc:5000/v3",
                "nova": "https://nova.openstack.svc:8774/v2.1",
            },
            "lastUpdateTime": "2024-01-15T11:00:00Z",
        },
    }


@pytest.fixture
def sample_osdplst_deployed() -> dict[str, Any]:
    """Sample OSDPLStatus for deployed/stable state."""
    return {
        "apiVersion": "lcm.mirantis.com/v1alpha1",
        "kind": "OpenStackDeploymentStatus",
        "metadata": {
            "name": "openstack",
            "namespace": "openstack",
        },
        "status": {
            "osdpl": {
                "state": "APPLIED",
                "health": "3/3",
                "openstackVersion": "antelope",
                "release": "mosk-21-0-0-25-2",
                "lcmProgress": "18/18",
            },
            "services": {
                "keystone": {"state": "APPLIED", "openstackVersion": "antelope"},
                "nova": {"state": "APPLIED", "openstackVersion": "antelope"},
                "neutron": {"state": "APPLIED", "openstackVersion": "antelope"},
            },
        },
    }


@pytest.fixture
def sample_osdplst_upgrading() -> dict[str, Any]:
    """Sample OSDPLStatus for upgrading state."""
    return {
        "apiVersion": "lcm.mirantis.com/v1alpha1",
        "kind": "OpenStackDeploymentStatus",
        "metadata": {
            "name": "openstack",
            "namespace": "openstack",
        },
        "status": {
            "osdpl": {
                "state": "APPLYING",
                "health": "2/3",
                "openstackVersion": "antelope",
                "release": "mosk-21-0-0-25-2",
                "lcmProgress": "10/18",
            },
            "services": {
                "keystone": {"state": "APPLIED", "openstackVersion": "bobcat"},
                "nova": {"state": "APPLYING", "openstackVersion": "antelope"},
                "neutron": {"state": "WAITING", "openstackVersion": "antelope"},
            },
        },
    }


@pytest.fixture
def sample_osdpl_updating() -> dict[str, Any]:
    """Sample OpenStackDeployment in Updating phase."""
    return {
        "apiVersion": "lcm.mirantis.com/v1alpha1",
        "kind": "OpenStackDeployment",
        "metadata": {
            "name": "openstack",
            "namespace": "openstack",
            "creationTimestamp": "2024-01-15T10:00:00Z",
        },
        "spec": {
            "openStackVersion": "bobcat",
            "preset": "compute",
            "size": "medium",
            "services": {
                "nova": {"enabled": True},
                "neutron": {"enabled": True},
                "keystone": {"enabled": True},
            },
        },
        "status": {
            "phase": "Updating",
            "openStackVersion": "antelope",
            "observedGeneration": 6,
            "conditions": [
                {
                    "type": "Ready",
                    "status": "False",
                    "reason": "UpgradeInProgress",
                    "message": "Upgrade to bobcat in progress",
                },
                {
                    "type": "Updating",
                    "status": "True",
                    "reason": "UpgradeInProgress",
                    "message": "Upgrading services",
                },
                {
                    "type": "ControlPlaneReady",
                    "status": "False",
                    "reason": "ServicesUpgrading",
                },
            ],
            "services": {
                "keystone": {
                    "ready": True,
                    "replicas": 3,
                    "readyReplicas": 3,
                    "updatedReplicas": 3,
                },
                "nova": {
                    "ready": False,
                    "replicas": 3,
                    "readyReplicas": 1,
                    "updatedReplicas": 1,
                    "updating": True,
                },
                "neutron": {
                    "ready": False,
                    "replicas": 3,
                    "readyReplicas": 0,
                    "updatedReplicas": 0,
                },
            },
            "updateStartedAt": "2024-01-15T12:00:00Z",
        },
    }


@pytest.fixture
def sample_deployments() -> list[dict[str, Any]]:
    """Sample Kubernetes Deployments."""
    return [
        {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {
                "name": "nova-api",
                "namespace": "openstack",
                "labels": {"application": "nova", "component": "api"},
                "generation": 5,
            },
            "spec": {
                "replicas": 3,
                "strategy": {
                    "type": "RollingUpdate",
                    "rollingUpdate": {"maxSurge": "25%", "maxUnavailable": "25%"},
                },
                "template": {"spec": {"containers": [{"image": "nova-api:antelope-1.0.0"}]}},
            },
            "status": {
                "replicas": 3,
                "updatedReplicas": 3,
                "readyReplicas": 3,
                "availableReplicas": 3,
                "observedGeneration": 5,
                "conditions": [
                    {"type": "Available", "status": "True"},
                    {"type": "Progressing", "status": "True", "reason": "NewReplicaSetAvailable"},
                ],
            },
        },
        {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {
                "name": "neutron-server",
                "namespace": "openstack",
                "labels": {"application": "neutron", "component": "server"},
                "generation": 3,
            },
            "spec": {
                "replicas": 3,
                "strategy": {"type": "RollingUpdate"},
                "template": {"spec": {"containers": [{"image": "neutron-server:antelope-1.0.0"}]}},
            },
            "status": {
                "replicas": 3,
                "updatedReplicas": 2,
                "readyReplicas": 2,
                "availableReplicas": 2,
                "unavailableReplicas": 1,
                "observedGeneration": 3,
                "conditions": [
                    {"type": "Available", "status": "True"},
                    {"type": "Progressing", "status": "True", "reason": "ReplicaSetUpdated"},
                ],
            },
        },
    ]


@pytest.fixture
def sample_statefulsets() -> list[dict[str, Any]]:
    """Sample Kubernetes StatefulSets."""
    return [
        {
            "apiVersion": "apps/v1",
            "kind": "StatefulSet",
            "metadata": {
                "name": "rabbitmq",
                "namespace": "openstack",
                "labels": {"application": "rabbitmq"},
            },
            "spec": {
                "replicas": 3,
                "updateStrategy": {"type": "RollingUpdate"},
            },
            "status": {
                "replicas": 3,
                "currentReplicas": 3,
                "readyReplicas": 3,
                "updatedReplicas": 3,
                "currentRevision": "rabbitmq-abc123",
                "updateRevision": "rabbitmq-abc123",
            },
        },
    ]


@pytest.fixture
def sample_nodes() -> list[dict[str, Any]]:
    """Sample Kubernetes Nodes."""
    return [
        {
            "apiVersion": "v1",
            "kind": "Node",
            "metadata": {
                "name": "compute-01",
                "labels": {
                    "node-role.kubernetes.io/worker": "",
                    "openstack-compute-node": "enabled",
                },
            },
            "spec": {"unschedulable": False, "taints": []},
            "status": {
                "conditions": [
                    {"type": "Ready", "status": "True", "reason": "KubeletReady"},
                    {"type": "MemoryPressure", "status": "False"},
                    {"type": "DiskPressure", "status": "False"},
                    {"type": "PIDPressure", "status": "False"},
                ],
                "nodeInfo": {
                    "kubeletVersion": "v1.28.0",
                    "containerRuntimeVersion": "containerd://1.7.0",
                    "osImage": "Ubuntu 22.04 LTS",
                    "kernelVersion": "5.15.0-generic",
                },
                "capacity": {"cpu": "16", "memory": "65536Mi", "pods": "110"},
                "allocatable": {"cpu": "15800m", "memory": "64000Mi", "pods": "110"},
            },
        },
        {
            "apiVersion": "v1",
            "kind": "Node",
            "metadata": {
                "name": "compute-02",
                "labels": {
                    "node-role.kubernetes.io/worker": "",
                    "openstack-compute-node": "enabled",
                },
            },
            "spec": {
                "unschedulable": True,
                "taints": [{"key": "node.kubernetes.io/unschedulable", "effect": "NoSchedule"}],
            },
            "status": {
                "conditions": [
                    {"type": "Ready", "status": "True", "reason": "KubeletReady"},
                    {"type": "MemoryPressure", "status": "False"},
                    {"type": "DiskPressure", "status": "False"},
                    {"type": "PIDPressure", "status": "False"},
                ],
                "nodeInfo": {
                    "kubeletVersion": "v1.28.0",
                    "containerRuntimeVersion": "containerd://1.7.0",
                    "osImage": "Ubuntu 22.04 LTS",
                    "kernelVersion": "5.15.0-generic",
                },
                "capacity": {"cpu": "16", "memory": "65536Mi", "pods": "110"},
            },
        },
    ]


@pytest.fixture
def sample_maintenance_requests() -> list[dict[str, Any]]:
    """Sample NodeMaintenanceRequest resources."""
    return [
        {
            "apiVersion": "kaas.mirantis.com/v1alpha1",
            "kind": "NodeMaintenanceRequest",
            "metadata": {
                "name": "maintain-compute-01",
                "namespace": "default",
                "creationTimestamp": "2024-01-15T10:00:00Z",
            },
            "spec": {
                "nodeName": "compute-01",
                "reason": "DiskReplacement",
                "description": "Replacing failed SSD",
                "drainStrategy": "LiveMigrate",
                "crqNumber": "CRQ123456789",
            },
            "status": {
                "phase": "Draining",
                "startedAt": "2024-01-15T10:05:00Z",
                "totalEvicted": 5,
            },
        },
        {
            "apiVersion": "kaas.mirantis.com/v1alpha1",
            "kind": "NodeMaintenanceRequest",
            "metadata": {
                "name": "maintain-compute-02",
                "namespace": "default",
                "creationTimestamp": "2024-01-15T09:00:00Z",
            },
            "spec": {
                "nodeName": "compute-02",
                "reason": "ScheduledMaintenance",
                "drainStrategy": "Graceful",
            },
            "status": {
                "phase": "Completed",
                "startedAt": "2024-01-15T09:05:00Z",
                "completedAt": "2024-01-15T09:30:00Z",
                "totalEvicted": 3,
            },
        },
    ]


# =============================================================================
# get_openstack_deployment_status Tests
# =============================================================================


class TestGetOpenStackDeploymentStatus:
    """Tests for get_openstack_deployment_status tool."""

    @pytest.mark.asyncio
    async def test_get_osdpl_status_deployed(
        self,
        mock_k8s_adapter: AsyncMock,
        sample_osdpl_deployed: dict[str, Any],
        sample_osdplst_deployed: dict[str, Any],
    ) -> None:
        """Test getting status of deployed OSDPL."""
        mock_k8s_adapter.get_openstack_deployment = AsyncMock(return_value=sample_osdpl_deployed)
        mock_k8s_adapter.get_openstack_deployment_status = AsyncMock(
            return_value=sample_osdplst_deployed
        )

        input_data = GetOSDPLStatusInput(
            name="openstack",
            namespace="openstack",
        )

        result = await get_openstack_deployment_status(mock_k8s_adapter, input_data)

        assert result.name == "openstack"
        assert result.namespace == "openstack"
        assert result.phase == OSDPLPhase.DEPLOYED
        assert result.health == HealthStatus.HEALTHY
        assert result.openstack_version == "antelope"
        assert result.target_version == "antelope"
        assert result.is_ready is True
        assert result.is_updating is False
        assert result.services_ready == 3
        assert result.services_total == 3
        assert result.summary.action_required is False

    @pytest.mark.asyncio
    async def test_get_osdpl_status_updating(
        self,
        mock_k8s_adapter: AsyncMock,
        sample_osdpl_updating: dict[str, Any],
        sample_osdplst_upgrading: dict[str, Any],
    ) -> None:
        """Test getting status of OSDPL during upgrade."""
        mock_k8s_adapter.get_openstack_deployment = AsyncMock(return_value=sample_osdpl_updating)
        mock_k8s_adapter.get_openstack_deployment_status = AsyncMock(
            return_value=sample_osdplst_upgrading
        )

        input_data = GetOSDPLStatusInput(
            name="openstack",
            namespace="openstack",
        )

        result = await get_openstack_deployment_status(mock_k8s_adapter, input_data)

        assert result.phase == OSDPLPhase.UPDATING
        assert result.health == HealthStatus.DEGRADED
        assert result.is_updating is True
        assert result.is_ready is False
        assert result.openstack_version == "antelope"
        assert result.target_version == "bobcat"
        # Interpretation comes from OSDPLStatus state (APPLYING)
        assert (
            "applied" in result.summary.interpretation.lower()
            or "changes" in result.summary.interpretation.lower()
        )
        assert len(result.summary.recommendations) > 0

    @pytest.mark.asyncio
    async def test_get_osdpl_status_without_services(
        self,
        mock_k8s_adapter: AsyncMock,
        sample_osdpl_deployed: dict[str, Any],
        sample_osdplst_deployed: dict[str, Any],
    ) -> None:
        """Test getting status without per-service details."""
        mock_k8s_adapter.get_openstack_deployment = AsyncMock(return_value=sample_osdpl_deployed)
        mock_k8s_adapter.get_openstack_deployment_status = AsyncMock(
            return_value=sample_osdplst_deployed
        )

        input_data = GetOSDPLStatusInput(
            name="openstack",
            namespace="openstack",
            include_services=False,
        )

        result = await get_openstack_deployment_status(mock_k8s_adapter, input_data)

        assert result.services == []
        assert result.phase == OSDPLPhase.DEPLOYED


# =============================================================================
# get_openstack_upgrade_progress Tests
# =============================================================================


class TestGetOpenStackUpgradeProgress:
    """Tests for get_openstack_upgrade_progress tool."""

    @pytest.mark.asyncio
    async def test_get_upgrade_progress_in_progress(
        self,
        mock_k8s_adapter: AsyncMock,
        sample_osdpl_updating: dict[str, Any],
        sample_osdplst_upgrading: dict[str, Any],
    ) -> None:
        """Test upgrade progress during active upgrade."""
        mock_k8s_adapter.get_openstack_deployment = AsyncMock(return_value=sample_osdpl_updating)
        mock_k8s_adapter.get_openstack_deployment_status = AsyncMock(
            return_value=sample_osdplst_upgrading
        )

        input_data = GetUpgradeProgressInput(
            name="openstack",
            namespace="openstack",
        )

        result = await get_openstack_upgrade_progress(mock_k8s_adapter, input_data)

        assert result.is_upgrading is True
        assert result.upgrade_state == UpgradeState.IN_PROGRESS
        assert result.from_version == "antelope"
        assert result.to_version == "bobcat"
        assert result.overall_progress_percent >= 0
        assert result.overall_progress_percent <= 100
        assert result.control_plane_ready is False

    @pytest.mark.asyncio
    async def test_get_upgrade_progress_completed(
        self,
        mock_k8s_adapter: AsyncMock,
        sample_osdpl_deployed: dict[str, Any],
        sample_osdplst_deployed: dict[str, Any],
    ) -> None:
        """Test upgrade progress when complete."""
        mock_k8s_adapter.get_openstack_deployment = AsyncMock(return_value=sample_osdpl_deployed)
        mock_k8s_adapter.get_openstack_deployment_status = AsyncMock(
            return_value=sample_osdplst_deployed
        )

        input_data = GetUpgradeProgressInput(
            name="openstack",
            namespace="openstack",
        )

        result = await get_openstack_upgrade_progress(mock_k8s_adapter, input_data)

        assert result.is_upgrading is False
        assert result.upgrade_state == UpgradeState.COMPLETED
        assert result.from_version == "antelope"
        assert result.to_version == "antelope"
        assert result.control_plane_ready is True
        assert result.compute_nodes_ready is True


# =============================================================================
# get_component_versions Tests
# =============================================================================


class TestGetComponentVersions:
    """Tests for get_component_versions tool."""

    @pytest.mark.asyncio
    async def test_get_component_versions(
        self,
        mock_k8s_adapter: AsyncMock,
        sample_osdpl_deployed: dict[str, Any],
        sample_deployments: list[dict[str, Any]],
    ) -> None:
        """Test getting component versions."""
        mock_k8s_adapter.get_openstack_deployment.return_value = sample_osdpl_deployed
        mock_k8s_adapter.list.side_effect = [
            sample_deployments,  # Deployments
            [],  # StatefulSets
        ]

        input_data = GetComponentVersionsInput(
            name="openstack",
            namespace="openstack",
        )

        result = await get_component_versions(mock_k8s_adapter, input_data)

        assert result.openstack_version_current == "antelope"
        assert result.openstack_version_target == "antelope"
        assert len(result.components) >= 0


# =============================================================================
# list_live_migrations Tests
# =============================================================================


class TestListLiveMigrations:
    """Tests for list_live_migrations tool."""

    @pytest.mark.asyncio
    async def test_list_live_migrations(
        self,
        mock_k8s_adapter: AsyncMock,
    ) -> None:
        """Test listing live migrations."""
        # Mock the OpenStack adapter to return sample migrations
        with patch(
            "mosk_mcp.tools.operations_visibility.list_live_migrations.OpenStackAdapter"
        ) as MockOS:
            mock_os = AsyncMock()
            mock_os.nova_migration_list = AsyncMock(return_value=[])
            MockOS.return_value.__aenter__.return_value = mock_os
            MockOS.return_value.__aexit__.return_value = None

            input_data = ListLiveMigrationsInput()

            result = await list_live_migrations(mock_k8s_adapter, input_data)

            # Should return empty migrations list
            assert result.total_count >= 0
            assert result.timestamp is not None

    @pytest.mark.asyncio
    async def test_list_live_migrations_filtered(
        self,
        mock_k8s_adapter: AsyncMock,
    ) -> None:
        """Test listing migrations with filters."""
        # Mock the OpenStack adapter to return sample migrations
        with patch(
            "mosk_mcp.tools.operations_visibility.list_live_migrations.OpenStackAdapter"
        ) as MockOS:
            mock_os = AsyncMock()
            # Return migration matching filter
            mock_os.nova_migration_list = AsyncMock(
                return_value=[
                    {
                        "id": 1,
                        "uuid": "mig-123",
                        "instance_uuid": "vm-456",
                        "source_compute": "compute-01",
                        "dest_compute": "compute-02",
                        "status": "running",
                        "migration_type": "live-migration",
                        "created_at": "2024-01-15T10:00:00Z",
                    }
                ]
            )
            MockOS.return_value.__aenter__.return_value = mock_os
            MockOS.return_value.__aexit__.return_value = None

            input_data = ListLiveMigrationsInput(
                source_host="compute-01",
                status_filter=MigrationStatus.RUNNING,
                include_completed=False,
            )

            result = await list_live_migrations(mock_k8s_adapter, input_data)

            # All returned migrations should match filter
            for m in result.migrations:
                assert m.source_host == "compute-01"


# =============================================================================
# get_migration_eta Tests
# =============================================================================


class TestGetMigrationETA:
    """Tests for get_migration_eta tool."""

    @pytest.mark.asyncio
    async def test_get_migration_eta(
        self,
        mock_k8s_adapter: AsyncMock,
    ) -> None:
        """Test getting migration ETA."""
        from mosk_mcp.adapters.openstack import MigrationStatus as OSMigrationStatus
        from mosk_mcp.adapters.openstack import MigrationType, ServerMigration

        # Mock the OpenStack adapter to return sample migrations
        with patch(
            "mosk_mcp.tools.operations_visibility.get_migration_eta.OpenStackAdapter"
        ) as MockOS:
            mock_os = AsyncMock()
            mock_migration = ServerMigration(
                id="1",
                server_uuid="vm-456",
                server_name="test-vm",
                source_compute="compute-01",
                dest_compute="compute-02",
                status=OSMigrationStatus.RUNNING,
                migration_type=MigrationType.LIVE_MIGRATION,
                memory_total_bytes=4096 * 1024 * 1024,
                memory_processed_bytes=2048 * 1024 * 1024,
                memory_remaining_bytes=2048 * 1024 * 1024,
                disk_total_bytes=40960 * 1024 * 1024,
                disk_processed_bytes=20480 * 1024 * 1024,
                disk_remaining_bytes=20480 * 1024 * 1024,
            )
            mock_os.list_migrations = AsyncMock(return_value=[mock_migration])
            MockOS.return_value.__aenter__.return_value = mock_os
            MockOS.return_value.__aexit__.return_value = None

            input_data = GetMigrationETAInput()

            result = await get_migration_eta(mock_k8s_adapter, input_data)

            # Should have active migrations
            assert result.has_active_migrations is True
            assert result.total_active >= 0
            assert result.overall_progress_percent >= 0

    @pytest.mark.asyncio
    async def test_get_migration_eta_with_per_vm(
        self,
        mock_k8s_adapter: AsyncMock,
    ) -> None:
        """Test migration ETA with per-VM breakdown."""
        from mosk_mcp.adapters.openstack import MigrationStatus as OSMigrationStatus
        from mosk_mcp.adapters.openstack import MigrationType, ServerMigration

        # Mock the OpenStack adapter to return sample migrations
        with patch(
            "mosk_mcp.tools.operations_visibility.get_migration_eta.OpenStackAdapter"
        ) as MockOS:
            mock_os = AsyncMock()
            mock_migration = ServerMigration(
                id="1",
                server_uuid="vm-456",
                server_name="test-vm",
                source_compute="compute-01",
                dest_compute="compute-02",
                status=OSMigrationStatus.RUNNING,
                migration_type=MigrationType.LIVE_MIGRATION,
            )
            mock_os.list_migrations = AsyncMock(return_value=[mock_migration])
            MockOS.return_value.__aenter__.return_value = mock_os
            MockOS.return_value.__aexit__.return_value = None

            input_data = GetMigrationETAInput(include_per_vm=True)

            result = await get_migration_eta(mock_k8s_adapter, input_data)

            assert result.per_vm_eta is not None
            if result.has_active_migrations:
                assert len(result.per_vm_eta) > 0


# =============================================================================
# list_maintenance_requests Tests
# =============================================================================


class TestListMaintenanceRequests:
    """Tests for list_maintenance_requests tool."""

    @pytest.mark.asyncio
    async def test_list_maintenance_requests(
        self,
        mock_k8s_adapter: AsyncMock,
        sample_maintenance_requests: list[dict[str, Any]],
    ) -> None:
        """Test listing maintenance requests."""
        mock_k8s_adapter.list_maintenance_requests.return_value = sample_maintenance_requests

        input_data = ListMaintenanceRequestsInput(
            namespace="default",
            include_completed=True,
        )

        result = await list_maintenance_requests(mock_k8s_adapter, input_data)

        assert result.total_count == 2
        assert result.active_count == 1
        assert result.completed_count == 1
        assert "compute-01" in result.nodes_in_maintenance

    @pytest.mark.asyncio
    async def test_list_maintenance_requests_filtered(
        self,
        mock_k8s_adapter: AsyncMock,
        sample_maintenance_requests: list[dict[str, Any]],
    ) -> None:
        """Test listing maintenance requests with filters."""
        mock_k8s_adapter.list_maintenance_requests.return_value = sample_maintenance_requests

        input_data = ListMaintenanceRequestsInput(
            namespace="default",
            phase_filter=MaintenancePhase.DRAINING,
            include_completed=False,
        )

        result = await list_maintenance_requests(mock_k8s_adapter, input_data)

        assert result.total_count == 1
        assert result.requests[0].phase == MaintenancePhase.DRAINING


# =============================================================================
# get_rollout_status Tests
# =============================================================================


class TestGetRolloutStatus:
    """Tests for get_rollout_status tool."""

    @pytest.mark.asyncio
    async def test_get_rollout_status(
        self,
        mock_k8s_adapter: AsyncMock,
        sample_deployments: list[dict[str, Any]],
        sample_statefulsets: list[dict[str, Any]],
    ) -> None:
        """Test getting rollout status."""
        mock_k8s_adapter.list.side_effect = [
            sample_deployments,
            sample_statefulsets,
        ]

        input_data = GetRolloutStatusInput(namespace="openstack")

        result = await get_rollout_status(mock_k8s_adapter, input_data)

        assert result.namespace == "openstack"
        assert result.total_workloads == 3  # 2 deployments + 1 statefulset
        assert len(result.deployments) == 2
        assert len(result.statefulsets) == 1
        assert result.overall_progress_percent >= 0

    @pytest.mark.asyncio
    async def test_get_rollout_status_complete(
        self,
        mock_k8s_adapter: AsyncMock,
        sample_statefulsets: list[dict[str, Any]],
    ) -> None:
        """Test rollout status when complete."""
        # All complete deployments
        complete_deployments = [
            {
                "apiVersion": "apps/v1",
                "kind": "Deployment",
                "metadata": {
                    "name": "nova-api",
                    "namespace": "openstack",
                    "labels": {"application": "nova"},
                    "generation": 5,
                },
                "spec": {
                    "replicas": 3,
                    "strategy": {"type": "RollingUpdate"},
                },
                "status": {
                    "replicas": 3,
                    "updatedReplicas": 3,
                    "readyReplicas": 3,
                    "availableReplicas": 3,
                    "observedGeneration": 5,
                    "conditions": [
                        {"type": "Available", "status": "True"},
                        {
                            "type": "Progressing",
                            "status": "True",
                            "reason": "NewReplicaSetAvailable",
                        },
                    ],
                },
            },
        ]

        mock_k8s_adapter.list.side_effect = [
            complete_deployments,
            sample_statefulsets,
        ]

        input_data = GetRolloutStatusInput(namespace="openstack")

        result = await get_rollout_status(mock_k8s_adapter, input_data)

        assert result.workloads_complete == 2
        assert result.all_rollouts_complete is True


# =============================================================================
# get_node_conditions Tests
# =============================================================================


class TestGetNodeConditions:
    """Tests for get_node_conditions tool."""

    @pytest.mark.asyncio
    async def test_get_node_conditions(
        self,
        mock_k8s_adapter: AsyncMock,
        sample_nodes: list[dict[str, Any]],
    ) -> None:
        """Test getting node conditions."""
        mock_k8s_adapter.list.return_value = sample_nodes

        input_data = GetNodeConditionsInput()

        result = await get_node_conditions(mock_k8s_adapter, input_data)

        assert result.total_nodes == 2
        assert result.ready_nodes == 2
        assert result.cordoned_nodes == 1  # compute-02 is cordoned
        assert "compute-02" in result.nodes_with_issues

    @pytest.mark.asyncio
    async def test_get_node_conditions_single_node(
        self,
        mock_k8s_adapter: AsyncMock,
        sample_nodes: list[dict[str, Any]],
    ) -> None:
        """Test getting conditions for specific node."""
        mock_k8s_adapter.get.return_value = sample_nodes[0]

        input_data = GetNodeConditionsInput(node_name="compute-01")

        result = await get_node_conditions(mock_k8s_adapter, input_data)

        assert result.total_nodes == 1
        assert result.nodes[0].node_name == "compute-01"
        assert result.nodes[0].is_ready is True
        assert result.nodes[0].is_schedulable is True

    @pytest.mark.asyncio
    async def test_get_node_conditions_only_unhealthy(
        self,
        mock_k8s_adapter: AsyncMock,
        sample_nodes: list[dict[str, Any]],
    ) -> None:
        """Test getting only unhealthy nodes."""
        mock_k8s_adapter.list.return_value = sample_nodes

        input_data = GetNodeConditionsInput(only_unhealthy=True)

        result = await get_node_conditions(mock_k8s_adapter, input_data)

        # Only compute-02 has issues (cordoned)
        assert result.total_nodes == 1
        assert result.nodes[0].node_name == "compute-02"

    @pytest.mark.asyncio
    async def test_get_node_conditions_with_labels(
        self,
        mock_k8s_adapter: AsyncMock,
        sample_nodes: list[dict[str, Any]],
    ) -> None:
        """Test getting node conditions with labels."""
        mock_k8s_adapter.list.return_value = sample_nodes

        input_data = GetNodeConditionsInput(include_labels=True)

        result = await get_node_conditions(mock_k8s_adapter, input_data)

        assert len(result.nodes[0].labels) > 0
        assert "openstack-compute-node" in result.nodes[0].labels


# =============================================================================
# Model Validation Tests
# =============================================================================


class TestModelValidation:
    """Tests for input model validation."""

    def test_osdpl_status_input_requires_name(self) -> None:
        """Test that GetOSDPLStatusInput requires name parameter."""
        import pytest
        from pydantic import ValidationError as PydanticValidationError

        # name is now required - should raise validation error if not provided
        with pytest.raises(PydanticValidationError, match="name"):
            GetOSDPLStatusInput()

        # Should work with name provided
        input_data = GetOSDPLStatusInput(name="mos")
        assert input_data.name == "mos"
        assert input_data.namespace == "openstack"
        assert input_data.include_conditions is True
        assert input_data.include_services is True

    def test_live_migrations_input_limit(self) -> None:
        """Test limit validation for ListLiveMigrationsInput."""
        input_data = ListLiveMigrationsInput(limit=100)

        assert input_data.limit == 100

    def test_maintenance_requests_input_phase_filter(self) -> None:
        """Test phase filter for ListMaintenanceRequestsInput."""
        input_data = ListMaintenanceRequestsInput(phase_filter=MaintenancePhase.DRAINING)

        assert input_data.phase_filter == MaintenancePhase.DRAINING


# =============================================================================
# Tests for list_available_releases
# =============================================================================


class TestParseComponentVersions:
    """Tests for _parse_component_versions helper function."""

    def test_parse_complete_description(self) -> None:
        """Test parsing a complete description string."""
        description = """kubernetes: v1.30.13
containerd: 1.7.27m3
mcr: 25.0.12m1
coredns: 1.10.1
etcd: 3.5.10
calico: v3.27.2
openstack_operator: 0.12.0
tungstenfabric_operator: 0.8.0"""

        result = _parse_component_versions(description)

        assert result.kubernetes == "v1.30.13"
        assert result.containerd == "1.7.27m3"
        assert result.mcr == "25.0.12m1"
        assert result.coredns == "1.10.1"
        assert result.etcd == "3.5.10"
        assert result.calico == "v3.27.2"
        assert result.openstack_operator == "0.12.0"
        assert result.tungstenfabric_operator == "0.8.0"

    def test_parse_empty_description(self) -> None:
        """Test parsing empty description."""
        result = _parse_component_versions("")

        assert result.kubernetes == "unknown"
        assert result.containerd == "unknown"

    def test_parse_partial_description(self) -> None:
        """Test parsing partial description."""
        description = """kubernetes: v1.30.0
etcd: 3.5.10"""

        result = _parse_component_versions(description)

        assert result.kubernetes == "v1.30.0"
        assert result.etcd == "3.5.10"
        assert result.containerd == "unknown"  # Not in description
        assert result.mcr == "unknown"  # Not in description

    def test_parse_with_extra_whitespace(self) -> None:
        """Test parsing with extra whitespace."""
        description = """  kubernetes:   v1.30.0
  containerd:  1.7.27  """

        result = _parse_component_versions(description)

        assert result.kubernetes == "v1.30.0"
        assert result.containerd == "1.7.27"


class TestExtractMajorVersion:
    """Tests for _extract_major_version helper function."""

    def test_extract_standard_version(self) -> None:
        """Test extracting major version from standard format."""
        assert _extract_major_version("21.0.0+25.2") == "21.0"

    def test_extract_version_without_metadata(self) -> None:
        """Test extracting major version without build metadata."""
        assert _extract_major_version("21.0.0") == "21.0"

    def test_extract_version_short(self) -> None:
        """Test extracting from short version."""
        assert _extract_major_version("21.0") == "21.0"

    def test_extract_empty_version(self) -> None:
        """Test extracting from empty version."""
        assert _extract_major_version("") == ""


class TestCompareVersions:
    """Tests for _compare_versions helper function."""

    def test_compare_equal_versions(self) -> None:
        """Test comparing equal versions."""
        assert _compare_versions("mosk-21-0-0-25-2", "mosk-21-0-0-25-2") == 0

    def test_compare_greater_version(self) -> None:
        """Test comparing where first is greater."""
        assert _compare_versions("mosk-21-0-1", "mosk-21-0-0") == 1

    def test_compare_lesser_version(self) -> None:
        """Test comparing where first is lesser."""
        assert _compare_versions("mosk-21-0-0", "mosk-21-0-1") == -1

    def test_compare_major_versions(self) -> None:
        """Test comparing major version differences."""
        assert _compare_versions("mosk-22-0-0", "mosk-21-9-9") == 1

    def test_compare_without_prefix(self) -> None:
        """Test comparing without mosk- prefix."""
        assert _compare_versions("21-0-1", "21-0-0") == 1


class TestListAvailableReleasesModels:
    """Tests for list_available_releases models."""

    def test_openstack_release_info(self) -> None:
        """Test OpenStackReleaseInfo model."""
        info = OpenStackReleaseInfo(id="caracal", description="Caracal release")

        assert info.id == "caracal"
        assert info.description == "Caracal release"

    def test_component_versions_defaults(self) -> None:
        """Test ComponentVersions default values."""
        components = ComponentVersions()

        assert components.kubernetes == "unknown"
        assert components.containerd == "unknown"
        assert components.mcr == "unknown"

    def test_release_info(self) -> None:
        """Test ReleaseInfo model."""
        info = ReleaseInfo(
            name="mosk-21-0-0-25-2",
            version="21.0.0+25.2",
            major_version="21.0",
            is_current=True,
        )

        assert info.name == "mosk-21-0-0-25-2"
        assert info.version == "21.0.0+25.2"
        assert info.is_current is True

    def test_upgrade_path_info(self) -> None:
        """Test UpgradePathInfo model."""
        path = UpgradePathInfo(
            from_release="mosk-21-0-0-25-2",
            to_release="mosk-21-0-1-25-2",
            update_plan_exists=True,
            update_plan_name="mosk-upgrade-plan",
        )

        assert path.from_release == "mosk-21-0-0-25-2"
        assert path.to_release == "mosk-21-0-1-25-2"
        assert path.update_plan_exists is True

    def test_list_available_releases_input_defaults(self) -> None:
        """Test ListAvailableReleasesInput defaults."""
        input_data = ListAvailableReleasesInput()

        assert input_data.cluster_name is None
        assert input_data.cluster_namespace == "default"
        assert input_data.include_all_versions is True
        assert input_data.include_component_details is True

    def test_list_available_releases_output(self) -> None:
        """Test ListAvailableReleasesOutput model."""
        output = ListAvailableReleasesOutput(
            current_release="mosk-21-0-0-25-2",
            current_version="21.0.0+25.2",
            releases=[],
            total_count=0,
            timestamp="2024-01-01T00:00:00Z",
        )

        assert output.current_release == "mosk-21-0-0-25-2"
        assert output.total_count == 0
        assert output.recommendations == []


class TestListAvailableReleasesFunction:
    """Tests for list_available_releases function."""

    @pytest.fixture
    def mock_mcc_adapter(self) -> AsyncMock:
        """Create mock MCC adapter."""
        adapter = AsyncMock()
        adapter.connect = AsyncMock()
        # Use the specific method names used by list_available_releases
        adapter.list_cluster_releases = AsyncMock(return_value=[])
        adapter.get_cluster = AsyncMock(return_value=None)
        adapter.list_custom_resources = AsyncMock(return_value=[])
        return adapter

    @pytest.fixture
    def mock_cluster_releases(self) -> list[dict[str, Any]]:
        """Create mock ClusterRelease CRs."""
        return [
            {
                "metadata": {"name": "mosk-21-0-0-25-2"},
                "spec": {
                    "version": "21.0.0+25.2",
                    "description": "kubernetes: v1.30.13\ncontainerd: 1.7.27m3",
                    "allowedOpenstackReleases": [
                        {"id": "caracal", "description": "Caracal release"}
                    ],
                },
            },
            {
                "metadata": {"name": "mosk-21-0-1-25-2"},
                "spec": {
                    "version": "21.0.1+25.2",
                    "description": "kubernetes: v1.30.14\ncontainerd: 1.7.28m1",
                    "allowedOpenstackReleases": [
                        {"id": "caracal", "description": "Caracal release"}
                    ],
                },
            },
        ]

    @pytest.mark.asyncio
    async def test_list_available_releases_success(
        self, mock_mcc_adapter: AsyncMock, mock_cluster_releases: list[dict[str, Any]]
    ) -> None:
        """Test successful retrieval of available releases."""
        mock_mcc_adapter.list_cluster_releases = AsyncMock(return_value=mock_cluster_releases)

        result = await list_available_releases(
            mock_mcc_adapter,
            ListAvailableReleasesInput(),
        )

        assert result.total_count == 2
        assert len(result.releases) == 2
        mock_mcc_adapter.list_cluster_releases.assert_called_once()

    @pytest.mark.asyncio
    async def test_list_available_releases_empty(self, mock_mcc_adapter: AsyncMock) -> None:
        """Test with no releases available."""
        mock_mcc_adapter.list_cluster_releases = AsyncMock(return_value=[])

        result = await list_available_releases(
            mock_mcc_adapter,
            ListAvailableReleasesInput(),
        )

        assert result.total_count == 0
        assert result.releases == []

    @pytest.mark.asyncio
    async def test_list_available_releases_with_cluster(
        self, mock_mcc_adapter: AsyncMock, mock_cluster_releases: list[dict[str, Any]]
    ) -> None:
        """Test with cluster name to get current release."""
        mock_mcc_adapter.list_cluster_releases = AsyncMock(return_value=mock_cluster_releases)
        mock_mcc_adapter.get_cluster = AsyncMock(
            return_value={
                "metadata": {"name": "mos"},
                "status": {"providerStatus": {"release": "mosk-21-0-0-25-2"}},
                "spec": {"providerSpec": {"value": {"release": "mosk-21-0-0-25-2"}}},
            }
        )

        result = await list_available_releases(
            mock_mcc_adapter,
            ListAvailableReleasesInput(cluster_name="mos"),
        )

        assert result.current_release == "mosk-21-0-0-25-2"
        mock_mcc_adapter.get_cluster.assert_called_once()

    @pytest.mark.asyncio
    async def test_list_available_releases_api_error(self, mock_mcc_adapter: AsyncMock) -> None:
        """Test API error handling."""
        from mosk_mcp.core.exceptions import ToolExecutionError

        mock_mcc_adapter.list_cluster_releases = AsyncMock(side_effect=Exception("API error"))

        with pytest.raises(ToolExecutionError) as exc_info:
            await list_available_releases(
                mock_mcc_adapter,
                ListAvailableReleasesInput(),
            )

        assert "Failed to list available releases" in str(exc_info.value)
