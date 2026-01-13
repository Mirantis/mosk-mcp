"""Unit tests for Cluster Health Summary tools.

This module tests all tools in the cluster_health module:
- get_mosk_cluster_health
- get_kubernetes_health
- get_openstack_health
- get_ceph_health
- list_active_alerts
- get_alert_details
- run_preflight_check
- get_resource_utilization
"""

from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mosk_mcp.tools.cluster_health import (
    COMPONENT_WEIGHTS,
    # Enums
    AlertSeverity,
    AlertState,
    GetCephHealthInput,
    # Input models
    GetClusterHealthInput,
    GetKubernetesHealthInput,
    GetOpenStackHealthInput,
    GetResourceUtilizationInput,
    HealthState,
    HealthStatus,
    ListActiveAlertsInput,
    PreflightCheckType,
    PreflightStatus,
    RunPreflightCheckInput,
    get_alert_details,
    get_ceph_health,
    get_kubernetes_health,
    # Functions
    get_mosk_cluster_health,
    get_openstack_health,
    get_resource_utilization,
    list_active_alerts,
    run_preflight_check,
    # Utility functions
    score_to_health_state,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_k8s_adapter() -> AsyncMock:
    """Create a mock Kubernetes adapter."""
    adapter = AsyncMock()
    adapter.get_server_version = AsyncMock(return_value="v1.28.0")
    adapter.check_api_health = AsyncMock()
    # Mock OSDPLStatus (osdplst) for OpenStack health checks
    adapter.get_openstack_deployment_status = AsyncMock(
        return_value={
            "status": {
                "osdpl": {
                    "state": "APPLIED",
                    "health": "23/23",
                }
            }
        }
    )
    return adapter


@pytest.fixture
def mock_direct_client() -> MagicMock:
    """Create a mock DirectStackLightClient for StackLight access."""
    from datetime import UTC

    from mosk_mcp.adapters.stacklight import (
        Alert,
        DirectStackLightClient,
    )
    from mosk_mcp.adapters.stacklight import (
        AlertSeverity as SLAlertSeverity,
    )
    from mosk_mcp.adapters.stacklight import (
        AlertState as SLAlertState,
    )

    client = MagicMock(spec=DirectStackLightClient)

    # Create sample alerts to return
    sample_alerts = [
        Alert(
            alert_name="CephOSDDown",
            severity=SLAlertSeverity.CRITICAL,
            state=SLAlertState.FIRING,
            summary="Ceph OSD 5 is down",
            description="OSD 5 on host storage-01 has been down for more than 5 minutes",
            labels={"severity": "critical", "alertname": "CephOSDDown", "osd": "5"},
            annotations={"summary": "Ceph OSD 5 is down"},
            starts_at=datetime.now(UTC),
            fingerprint="abc123",
            cluster_type="mosk",
        ),
        Alert(
            alert_name="HighCPUUsage",
            severity=SLAlertSeverity.WARNING,
            state=SLAlertState.FIRING,
            summary="High CPU usage on compute-01",
            description="CPU usage is above 90%",
            labels={"severity": "warning", "alertname": "HighCPUUsage", "host": "compute-01"},
            annotations={"summary": "High CPU usage"},
            starts_at=datetime.now(UTC),
            fingerprint="def456",
            cluster_type="mosk",
        ),
    ]

    client.get_alerts = AsyncMock(return_value=sample_alerts)
    client.query_prometheus = AsyncMock(return_value=[])
    client.query_prometheus_range = AsyncMock(return_value=[])
    return client


@pytest.fixture
def sample_nodes_healthy() -> list[dict[str, Any]]:
    """Sample healthy Kubernetes nodes."""
    return [
        {
            "metadata": {
                "name": "control-01",
                "labels": {
                    "node-role.kubernetes.io/control-plane": "",
                    "kubernetes.io/hostname": "control-01",
                },
            },
            "spec": {"unschedulable": False},
            "status": {
                "conditions": [
                    {"type": "Ready", "status": "True"},
                    {"type": "MemoryPressure", "status": "False"},
                    {"type": "DiskPressure", "status": "False"},
                    {"type": "PIDPressure", "status": "False"},
                ],
                "capacity": {"cpu": "8", "memory": "32Gi", "pods": "110"},
                "allocatable": {"cpu": "7800m", "memory": "30Gi", "pods": "110"},
            },
        },
        {
            "metadata": {
                "name": "compute-01",
                "labels": {
                    "node-role.kubernetes.io/worker": "",
                    "openstack-compute-node": "enabled",
                },
            },
            "spec": {"unschedulable": False},
            "status": {
                "conditions": [
                    {"type": "Ready", "status": "True"},
                    {"type": "MemoryPressure", "status": "False"},
                    {"type": "DiskPressure", "status": "False"},
                    {"type": "PIDPressure", "status": "False"},
                ],
                "capacity": {"cpu": "16", "memory": "64Gi", "pods": "110"},
                "allocatable": {"cpu": "15800m", "memory": "62Gi", "pods": "110"},
            },
        },
        {
            "metadata": {
                "name": "compute-02",
                "labels": {
                    "node-role.kubernetes.io/worker": "",
                    "openstack-compute-node": "enabled",
                },
            },
            "spec": {"unschedulable": False},
            "status": {
                "conditions": [
                    {"type": "Ready", "status": "True"},
                    {"type": "MemoryPressure", "status": "False"},
                    {"type": "DiskPressure", "status": "False"},
                    {"type": "PIDPressure", "status": "False"},
                ],
                "capacity": {"cpu": "16", "memory": "64Gi", "pods": "110"},
                "allocatable": {"cpu": "15800m", "memory": "62Gi", "pods": "110"},
            },
        },
    ]


@pytest.fixture
def sample_nodes_degraded() -> list[dict[str, Any]]:
    """Sample nodes with one unhealthy."""
    return [
        {
            "metadata": {
                "name": "control-01",
                "labels": {"node-role.kubernetes.io/control-plane": ""},
            },
            "spec": {"unschedulable": False},
            "status": {
                "conditions": [
                    {"type": "Ready", "status": "True"},
                    {"type": "MemoryPressure", "status": "False"},
                    {"type": "DiskPressure", "status": "False"},
                    {"type": "PIDPressure", "status": "False"},
                ],
            },
        },
        {
            "metadata": {
                "name": "compute-01",
                "labels": {"openstack-compute-node": "enabled"},
            },
            "spec": {"unschedulable": False},
            "status": {
                "conditions": [
                    {"type": "Ready", "status": "False", "reason": "KubeletNotReady"},
                    {"type": "MemoryPressure", "status": "True"},
                    {"type": "DiskPressure", "status": "False"},
                    {"type": "PIDPressure", "status": "False"},
                ],
            },
        },
    ]


@pytest.fixture
def sample_pods_healthy() -> list[dict[str, Any]]:
    """Sample healthy system pods."""
    return [
        {
            "metadata": {"name": "coredns-abc123", "namespace": "kube-system"},
            "status": {
                "phase": "Running",
                "containerStatuses": [
                    {"ready": True, "restartCount": 0},
                ],
            },
        },
        {
            "metadata": {"name": "kube-proxy-xyz789", "namespace": "kube-system"},
            "status": {
                "phase": "Running",
                "containerStatuses": [
                    {"ready": True, "restartCount": 0},
                ],
            },
        },
    ]


@pytest.fixture
def sample_osdpl_deployed() -> dict[str, Any]:
    """Sample deployed OpenStackDeployment."""
    return {
        "metadata": {"name": "openstack", "namespace": "openstack"},
        "spec": {"openStackVersion": "antelope"},
        "status": {
            "phase": "Deployed",
            "openStackVersion": "antelope",
            "services": {
                "keystone": {"ready": True, "replicas": 3, "readyReplicas": 3},
                "nova": {"ready": True, "replicas": 3, "readyReplicas": 3},
                "neutron": {"ready": True, "replicas": 3, "readyReplicas": 3},
            },
            "endpoints": {
                "keystone": "https://keystone.openstack:5000/v3",
                "nova": "https://nova.openstack:8774/v2.1",
            },
        },
    }


@pytest.fixture
def sample_osdpl_upgrading() -> dict[str, Any]:
    """Sample upgrading OpenStackDeployment."""
    return {
        "metadata": {"name": "openstack", "namespace": "openstack"},
        "spec": {"openStackVersion": "bobcat"},
        "status": {
            "phase": "Updating",
            "openStackVersion": "antelope",
            "services": {
                "keystone": {"ready": True, "replicas": 3, "readyReplicas": 3},
                "nova": {"ready": False, "replicas": 3, "readyReplicas": 1},
            },
        },
    }


@pytest.fixture
def sample_osdplst_deployed() -> dict[str, Any]:
    """Sample OSDPLStatus for deployed state."""
    return {
        "metadata": {"name": "mos", "namespace": "openstack"},
        "status": {
            "osdpl": {
                "state": "APPLIED",
                "health": "3/3",
                "openstackVersion": "antelope",
                "release": "mosk-21-0-0-25-2",
                "lcmProgress": "18/18",
            },
            "health": {
                "keystone": {"api": {"status": "Ready"}},
                "nova": {"api": {"status": "Ready"}, "conductor": {"status": "Ready"}},
                "neutron": {"api": {"status": "Ready"}},
            },
            "services": {
                "identity": {"state": "APPLIED", "openstackVersion": "antelope"},
                "compute": {"state": "APPLIED", "openstackVersion": "antelope"},
                "networking": {"state": "APPLIED", "openstackVersion": "antelope"},
            },
        },
    }


@pytest.fixture
def sample_osdplst_upgrading() -> dict[str, Any]:
    """Sample OSDPLStatus for upgrading state."""
    return {
        "metadata": {"name": "mos", "namespace": "openstack"},
        "status": {
            "osdpl": {
                "state": "APPLYING",
                "health": "2/3",
                "openstackVersion": "antelope",
                "release": "mosk-21-0-0-25-2",
                "lcmProgress": "10/18",
            },
            "services": {
                "keystone": {"state": "APPLIED", "openstackVersion": "antelope"},
                "nova": {"state": "APPLYING", "openstackVersion": "antelope"},
                "neutron": {"state": "WAITING", "openstackVersion": "antelope"},
            },
        },
    }


@pytest.fixture
def mock_ceph_status():
    """Create mock CephClusterStatus."""
    status = MagicMock()
    status.health = MagicMock()
    status.health.name = "HEALTH_OK"
    status.health_checks = {}
    status.num_osds = 6
    status.num_osds_up = 6
    status.num_osds_in = 6
    status.num_pgs = 128
    status.pg_states = {"active+clean": 128}
    status.total_bytes = 10 * 1024**4  # 10 TB
    status.used_bytes = 3 * 1024**4  # 3 TB
    status.available_bytes = 7 * 1024**4  # 7 TB
    status.capacity_percent = 30.0
    return status


# =============================================================================
# get_kubernetes_health Tests
# =============================================================================


class TestGetKubernetesHealth:
    """Tests for get_kubernetes_health tool."""

    @pytest.mark.asyncio
    async def test_kubernetes_health_healthy(
        self,
        mock_k8s_adapter: AsyncMock,
        sample_nodes_healthy: list[dict[str, Any]],
        sample_pods_healthy: list[dict[str, Any]],
    ) -> None:
        """Test Kubernetes health when cluster is healthy."""
        mock_k8s_adapter.list_nodes.return_value = sample_nodes_healthy
        mock_k8s_adapter.list_pods.return_value = sample_pods_healthy

        input_data = GetKubernetesHealthInput(
            include_node_details=True,
            include_system_pods=True,
        )

        result = await get_kubernetes_health(mock_k8s_adapter, input_data)

        assert result.health == HealthStatus.HEALTHY
        assert result.score >= 90
        assert result.total_nodes == 3
        assert result.ready_nodes == 3
        assert result.not_ready_nodes == 0
        assert result.api_server_healthy is True
        assert len(result.nodes) == 3

    @pytest.mark.asyncio
    async def test_kubernetes_health_degraded(
        self,
        mock_k8s_adapter: AsyncMock,
        sample_nodes_degraded: list[dict[str, Any]],
    ) -> None:
        """Test Kubernetes health when nodes are not ready."""
        mock_k8s_adapter.list_nodes.return_value = sample_nodes_degraded
        mock_k8s_adapter.list_pods.return_value = []

        input_data = GetKubernetesHealthInput()

        result = await get_kubernetes_health(mock_k8s_adapter, input_data)

        assert result.health in [HealthStatus.DEGRADED, HealthStatus.UNHEALTHY]
        assert result.score < 90
        assert result.not_ready_nodes == 1
        assert len(result.issues) > 0

    @pytest.mark.asyncio
    async def test_kubernetes_health_api_server_down(
        self,
        mock_k8s_adapter: AsyncMock,
    ) -> None:
        """Test Kubernetes health when API server is unhealthy."""
        mock_k8s_adapter.check_api_health.side_effect = Exception("Connection refused")
        mock_k8s_adapter.list_nodes.return_value = []
        mock_k8s_adapter.list_pods.return_value = []

        input_data = GetKubernetesHealthInput()

        result = await get_kubernetes_health(mock_k8s_adapter, input_data)

        assert result.api_server_healthy is False
        assert result.score < 80
        assert any("API server" in issue for issue in result.issues)


# =============================================================================
# get_openstack_health Tests
# =============================================================================


class TestGetOpenStackHealth:
    """Tests for get_openstack_health tool."""

    @pytest.mark.asyncio
    async def test_openstack_health_healthy(
        self,
        mock_k8s_adapter: AsyncMock,
        sample_osdpl_deployed: dict[str, Any],
        sample_osdplst_deployed: dict[str, Any],
    ) -> None:
        """Test OpenStack health when all services are healthy."""
        mock_k8s_adapter.get_openstack_deployment = AsyncMock(return_value=sample_osdpl_deployed)
        mock_k8s_adapter.get_openstack_deployment_status = AsyncMock(
            return_value=sample_osdplst_deployed
        )
        mock_k8s_adapter.list_machines = AsyncMock(
            return_value=[
                {
                    "metadata": {"name": "compute-01"},
                    "status": {"phase": "Ready"},
                },
                {
                    "metadata": {"name": "compute-02"},
                    "status": {"phase": "Ready"},
                },
            ]
        )

        input_data = GetOpenStackHealthInput(
            osdpl_name="mos",
            include_services=True,
            include_endpoints=True,
        )

        result = await get_openstack_health(mock_k8s_adapter, input_data)

        assert result.control_plane_health == HealthStatus.HEALTHY
        assert result.control_plane_score >= 90
        assert result.osdpl_phase == "Deployed"  # Legacy OSDPL phase
        assert result.osdplst_state == "APPLIED"  # OSDPLStatus state
        assert result.is_upgrading is False
        assert result.services_healthy == 3

    @pytest.mark.asyncio
    async def test_openstack_health_upgrading(
        self,
        mock_k8s_adapter: AsyncMock,
        sample_osdpl_upgrading: dict[str, Any],
    ) -> None:
        """Test OpenStack health during upgrade."""
        mock_k8s_adapter.get_openstack_deployment = AsyncMock(return_value=sample_osdpl_upgrading)
        mock_k8s_adapter.get_openstack_deployment_status = AsyncMock(return_value=None)
        mock_k8s_adapter.list_machines = AsyncMock(return_value=[])

        input_data = GetOpenStackHealthInput(osdpl_name="mos")

        result = await get_openstack_health(mock_k8s_adapter, input_data)

        assert result.osdpl_phase == "Updating"
        assert result.is_upgrading is True
        assert result.control_plane_score < 100


# =============================================================================
# get_ceph_health Tests
# =============================================================================


class TestGetCephHealth:
    """Tests for get_ceph_health tool."""

    @pytest.mark.asyncio
    async def test_ceph_health_healthy(
        self,
        mock_k8s_adapter: AsyncMock,
    ) -> None:
        """Test Ceph health when cluster is healthy."""
        from mosk_mcp.adapters.ceph import CephHealthStatus

        with patch("mosk_mcp.tools.cluster_health.get_ceph_health.CephAdapter") as MockAdapter:
            mock_ceph = AsyncMock()
            mock_status = MagicMock()
            mock_status.health = CephHealthStatus.HEALTH_OK
            mock_status.health_checks = {}
            mock_status.num_osds = 6
            mock_status.num_osds_up = 6
            mock_status.num_osds_in = 6
            mock_status.num_pgs = 128
            mock_status.pg_states = {"active+clean": 128}
            mock_status.total_bytes = 10 * 1024**4
            mock_status.used_bytes = 3 * 1024**4
            mock_status.available_bytes = 7 * 1024**4
            mock_status.capacity_percent = 30.0

            mock_ceph.get_cluster_status.return_value = mock_status
            mock_ceph.list_osds.return_value = []
            mock_ceph.get_pool_stats.return_value = []

            # Configure async context manager
            MockAdapter.return_value.__aenter__.return_value = mock_ceph
            MockAdapter.return_value.__aexit__.return_value = None

            input_data = GetCephHealthInput()

            result = await get_ceph_health(mock_k8s_adapter, input_data)

            assert result.health == HealthStatus.HEALTHY
            assert result.score >= 90
            assert result.ceph_health == "HEALTH_OK"
            assert result.osds_total == 6
            assert result.osds_up == 6
            assert result.capacity_status == "normal"

    @pytest.mark.asyncio
    async def test_ceph_health_degraded_osd_down(
        self,
        mock_k8s_adapter: AsyncMock,
    ) -> None:
        """Test Ceph health when OSDs are down."""
        from mosk_mcp.adapters.ceph import CephHealthStatus

        with patch("mosk_mcp.tools.cluster_health.get_ceph_health.CephAdapter") as MockAdapter:
            mock_ceph = AsyncMock()
            mock_status = MagicMock()
            mock_status.health = CephHealthStatus.HEALTH_WARN
            mock_status.health_checks = {"OSD_DOWN": {"summary": {"message": "1 OSD down"}}}
            mock_status.num_osds = 6
            mock_status.num_osds_up = 5
            mock_status.num_osds_in = 6
            mock_status.num_pgs = 128
            mock_status.pg_states = {"active+clean": 120, "degraded": 8}
            mock_status.total_bytes = 10 * 1024**4
            mock_status.used_bytes = 3 * 1024**4
            mock_status.available_bytes = 7 * 1024**4
            mock_status.capacity_percent = 30.0

            mock_ceph.get_cluster_status.return_value = mock_status
            mock_ceph.list_osds.return_value = []

            MockAdapter.return_value.__aenter__.return_value = mock_ceph
            MockAdapter.return_value.__aexit__.return_value = None

            input_data = GetCephHealthInput()

            result = await get_ceph_health(mock_k8s_adapter, input_data)

            assert result.health in [HealthStatus.DEGRADED, HealthStatus.UNHEALTHY]
            assert result.osds_up == 5
            assert len(result.issues) > 0


# =============================================================================
# list_active_alerts Tests
# =============================================================================


class TestListActiveAlerts:
    """Tests for list_active_alerts tool."""

    @pytest.mark.asyncio
    async def test_list_active_alerts_returns_mock_data(
        self,
        mock_direct_client: MagicMock,
    ) -> None:
        """Test listing alerts returns mock data."""
        input_data = ListActiveAlertsInput()

        result = await list_active_alerts(mock_direct_client, input_data)

        assert result.total_count > 0
        assert result.critical_count >= 0
        assert result.warning_count >= 0
        assert len(result.alerts) > 0
        assert result.timestamp is not None

    @pytest.mark.asyncio
    async def test_list_active_alerts_filter_by_severity(
        self,
        mock_direct_client: MagicMock,
    ) -> None:
        """Test listing alerts with severity filter."""
        input_data = ListActiveAlertsInput(
            severity_filter=AlertSeverity.CRITICAL,
        )

        result = await list_active_alerts(mock_direct_client, input_data)

        # All returned alerts should be critical
        for alert in result.alerts:
            assert alert.severity == AlertSeverity.CRITICAL

    @pytest.mark.asyncio
    async def test_list_active_alerts_filter_by_component(
        self,
        mock_direct_client: MagicMock,
    ) -> None:
        """Test listing alerts with component filter."""
        input_data = ListActiveAlertsInput(
            component_filter="ceph",
        )

        result = await list_active_alerts(mock_direct_client, input_data)

        # All returned alerts should be ceph-related
        for alert in result.alerts:
            assert alert.component == "ceph"


# =============================================================================
# get_alert_details Tests
# =============================================================================


class TestGetAlertDetails:
    """Tests for get_alert_details tool."""

    @pytest.fixture
    def mock_stacklight_client(self) -> AsyncMock:
        """Create a mock StackLight client."""
        client = AsyncMock()
        return client

    @pytest.fixture
    def mock_alert(self) -> MagicMock:
        """Create a mock alert object with to_dict method."""
        alert = MagicMock()
        alert.to_dict.return_value = {
            "labels": {
                "alertname": "CephOSDDown",
                "severity": "critical",
            },
            "annotations": {
                "description": "Ceph OSD is down",
                "summary": "OSD Down",
            },
            "status": {"state": "firing"},
            "startsAt": "2024-01-01T00:00:00Z",
        }
        return alert

    @pytest.mark.asyncio
    async def test_get_alert_details_known_alert(
        self,
        mock_k8s_adapter: AsyncMock,
        mock_stacklight_client: AsyncMock,
        mock_alert: MagicMock,
    ) -> None:
        """Test getting details for a known alert."""
        # Mock the StackLightAdapter
        with patch(
            "mosk_mcp.tools.cluster_health.get_alert_details.StackLightAdapter"
        ) as MockAdapter:
            mock_adapter = AsyncMock()
            mock_adapter.get_alerts.return_value = [mock_alert]
            mock_adapter.query_range.return_value = []  # History query
            MockAdapter.return_value = mock_adapter

            result = await get_alert_details(
                direct_client=mock_stacklight_client,
                alert_name="CephOSDDown",
                include_history=True,
            )

        assert result.alert_name == "CephOSDDown"
        assert result.severity == AlertSeverity.CRITICAL
        assert result.state == AlertState.FIRING
        assert result.context.runbook_url is not None
        assert len(result.context.suggested_actions) > 0

    @pytest.mark.asyncio
    async def test_get_alert_details_unknown_alert(
        self,
        mock_k8s_adapter: AsyncMock,
        mock_stacklight_client: AsyncMock,
    ) -> None:
        """Test getting details for an unknown alert."""
        from mosk_mcp.core.exceptions import ResourceNotFoundError

        # Mock the StackLightAdapter to return empty alerts
        with patch(
            "mosk_mcp.tools.cluster_health.get_alert_details.StackLightAdapter"
        ) as MockAdapter:
            mock_adapter = AsyncMock()
            mock_adapter.get_alerts.return_value = []
            MockAdapter.return_value = mock_adapter

            with pytest.raises(ResourceNotFoundError):
                await get_alert_details(
                    direct_client=mock_stacklight_client,
                    alert_name="NonExistentAlert",
                )


# =============================================================================
# run_preflight_check Tests
# =============================================================================


class TestRunPreflightCheck:
    """Tests for run_preflight_check tool."""

    @pytest.mark.asyncio
    async def test_preflight_check_maintenance_healthy(
        self,
        mock_k8s_adapter: AsyncMock,
        sample_nodes_healthy: list[dict[str, Any]],
        sample_osdpl_deployed: dict[str, Any],
    ) -> None:
        """Test preflight check for maintenance on healthy cluster."""
        from mosk_mcp.adapters.ceph import CephHealthStatus

        mock_k8s_adapter.list_nodes.return_value = sample_nodes_healthy
        mock_k8s_adapter.get_openstack_deployment.return_value = sample_osdpl_deployed

        with patch("mosk_mcp.tools.cluster_health.run_preflight_check.CephAdapter") as MockAdapter:
            mock_ceph = AsyncMock()
            mock_status = MagicMock()
            mock_status.health = CephHealthStatus.HEALTH_OK
            mock_status.health_checks = {}
            mock_status.num_osds = 6
            mock_status.num_osds_up = 6
            mock_status.num_osds_in = 6
            mock_status.num_pgs = 128
            mock_status.pg_states = {"active+clean": 128}
            mock_status.capacity_percent = 30.0

            mock_ceph.get_cluster_status.return_value = mock_status
            mock_ceph.list_osds.return_value = []

            MockAdapter.return_value.__aenter__.return_value = mock_ceph
            MockAdapter.return_value.__aexit__.return_value = None

            input_data = RunPreflightCheckInput(
                check_type=PreflightCheckType.MAINTENANCE,
            )

            result = await run_preflight_check(mock_k8s_adapter, input_data)

            assert result.check_type == PreflightCheckType.MAINTENANCE
            assert result.ready_for_operation is True
            assert result.overall_status == PreflightStatus.PASS
            assert result.checks_failed == 0

    @pytest.mark.asyncio
    async def test_preflight_check_with_target_node(
        self,
        mock_k8s_adapter: AsyncMock,
        sample_nodes_healthy: list[dict[str, Any]],
        sample_osdpl_deployed: dict[str, Any],
    ) -> None:
        """Test preflight check for specific node."""
        from mosk_mcp.adapters.ceph import CephHealthStatus

        mock_k8s_adapter.list_nodes.return_value = sample_nodes_healthy
        mock_k8s_adapter.get_openstack_deployment.return_value = sample_osdpl_deployed
        mock_k8s_adapter.list_pods.return_value = []

        with patch("mosk_mcp.tools.cluster_health.run_preflight_check.CephAdapter") as MockAdapter:
            mock_ceph = AsyncMock()
            mock_status = MagicMock()
            mock_status.health = CephHealthStatus.HEALTH_OK
            mock_status.health_checks = {}
            mock_status.num_osds = 6
            mock_status.num_osds_up = 6
            mock_status.num_osds_in = 6
            mock_status.num_pgs = 128
            mock_status.pg_states = {"active+clean": 128}
            mock_status.capacity_percent = 30.0

            mock_ceph.get_cluster_status.return_value = mock_status

            MockAdapter.return_value.__aenter__.return_value = mock_ceph
            MockAdapter.return_value.__aexit__.return_value = None

            input_data = RunPreflightCheckInput(
                check_type=PreflightCheckType.NODE_REMOVAL,
                target_node="compute-01",
            )

            result = await run_preflight_check(mock_k8s_adapter, input_data)

            assert result.target == "node/compute-01"
            # Should have target-specific checks
            target_checks = [c for c in result.checks if c.category == "target"]
            assert len(target_checks) > 0


# =============================================================================
# get_resource_utilization Tests
# =============================================================================


class TestGetResourceUtilization:
    """Tests for get_resource_utilization tool."""

    @pytest.mark.asyncio
    async def test_resource_utilization_basic(
        self,
        mock_k8s_adapter: AsyncMock,
        sample_nodes_healthy: list[dict[str, Any]],
    ) -> None:
        """Test basic resource utilization retrieval."""

        mock_k8s_adapter.list_nodes.return_value = sample_nodes_healthy
        mock_k8s_adapter.list_pods.return_value = [
            {
                "metadata": {"namespace": "openstack"},
                "spec": {
                    "nodeName": "compute-01",
                    "containers": [
                        {
                            "resources": {
                                "requests": {"cpu": "500m", "memory": "512Mi"},
                            },
                        },
                    ],
                },
                "status": {"phase": "Running"},
            },
        ]

        with patch(
            "mosk_mcp.tools.cluster_health.get_resource_utilization.CephAdapter"
        ) as MockAdapter:
            mock_ceph = AsyncMock()
            mock_status = MagicMock()
            mock_status.total_bytes = 10 * 1024**4
            mock_status.used_bytes = 3 * 1024**4
            mock_status.available_bytes = 7 * 1024**4
            mock_status.capacity_percent = 30.0

            mock_ceph.get_cluster_status.return_value = mock_status

            MockAdapter.return_value.__aenter__.return_value = mock_ceph
            MockAdapter.return_value.__aexit__.return_value = None

            input_data = GetResourceUtilizationInput()

            result = await get_resource_utilization(mock_k8s_adapter, input_data)

            assert result.cluster_cpu_capacity_millicores > 0
            assert result.cluster_memory_capacity_bytes > 0
            assert result.storage.total_bytes > 0
            assert result.timestamp is not None

    @pytest.mark.asyncio
    async def test_resource_utilization_with_per_node(
        self,
        mock_k8s_adapter: AsyncMock,
        sample_nodes_healthy: list[dict[str, Any]],
    ) -> None:
        """Test resource utilization with per-node breakdown."""

        mock_k8s_adapter.list_nodes.return_value = sample_nodes_healthy
        mock_k8s_adapter.list_pods.return_value = []

        with patch(
            "mosk_mcp.tools.cluster_health.get_resource_utilization.CephAdapter"
        ) as MockAdapter:
            mock_ceph = AsyncMock()
            mock_status = MagicMock()
            mock_status.total_bytes = 10 * 1024**4
            mock_status.used_bytes = 3 * 1024**4
            mock_status.available_bytes = 7 * 1024**4
            mock_status.capacity_percent = 30.0

            mock_ceph.get_cluster_status.return_value = mock_status

            MockAdapter.return_value.__aenter__.return_value = mock_ceph
            MockAdapter.return_value.__aexit__.return_value = None

            input_data = GetResourceUtilizationInput(
                include_per_node=True,
            )

            result = await get_resource_utilization(mock_k8s_adapter, input_data)

            assert len(result.nodes) == 3
            assert result.nodes[0].node_name == "control-01"
            assert result.nodes[0].role == "control-plane"


# =============================================================================
# get_mosk_cluster_health Tests
# =============================================================================


class TestGetMoskClusterHealth:
    """Tests for get_mosk_cluster_health tool."""

    @pytest.mark.asyncio
    async def test_cluster_health_all_healthy(
        self,
        mock_k8s_adapter: AsyncMock,
        sample_nodes_healthy: list[dict[str, Any]],
        sample_osdpl_deployed: dict[str, Any],
        sample_osdplst_deployed: dict[str, Any],
    ) -> None:
        """Test unified cluster health when all components are healthy."""
        from mosk_mcp.adapters.ceph import CephHealthStatus

        # Setup Kubernetes mocks - use AsyncMock for async methods
        mock_k8s_adapter.list_nodes = AsyncMock(return_value=sample_nodes_healthy)
        mock_k8s_adapter.list_pods = AsyncMock(return_value=[])
        mock_k8s_adapter.get_openstack_deployment = AsyncMock(return_value=sample_osdpl_deployed)
        mock_k8s_adapter.get_openstack_deployment_status = AsyncMock(
            return_value=sample_osdplst_deployed
        )
        mock_k8s_adapter.list_machines = AsyncMock(
            return_value=[
                {"metadata": {"name": "compute-01"}, "status": {"phase": "Ready"}},
            ]
        )
        # Mock OSDPL discovery
        mock_k8s_adapter.list_openstack_deployments = AsyncMock(
            return_value=[
                {"metadata": {"name": "mos", "namespace": "openstack"}},
            ]
        )

        with patch("mosk_mcp.tools.cluster_health.get_ceph_health.CephAdapter") as MockCeph:
            mock_ceph = AsyncMock()
            mock_status = MagicMock()
            mock_status.health = CephHealthStatus.HEALTH_OK
            mock_status.health_checks = {}
            mock_status.num_osds = 6
            mock_status.num_osds_up = 6
            mock_status.num_osds_in = 6
            mock_status.num_pgs = 128
            mock_status.pg_states = {"active+clean": 128}
            mock_status.total_bytes = 10 * 1024**4
            mock_status.used_bytes = 3 * 1024**4
            mock_status.available_bytes = 7 * 1024**4
            mock_status.capacity_percent = 30.0

            mock_ceph.get_cluster_status = AsyncMock(return_value=mock_status)
            mock_ceph.list_osds = AsyncMock(return_value=[])

            MockCeph.return_value.__aenter__.return_value = mock_ceph
            MockCeph.return_value.__aexit__.return_value = None

            input_data = GetClusterHealthInput(osdpl_name="mos")

            result = await get_mosk_cluster_health(mock_k8s_adapter, input_data)

            assert result.health_state == HealthState.HEALTHY
            assert result.health_score.overall_score >= 90
            assert result.is_safe_for_maintenance is True
            assert result.is_safe_for_upgrade is True

    @pytest.mark.asyncio
    async def test_cluster_health_degraded(
        self,
        mock_k8s_adapter: AsyncMock,
        sample_nodes_degraded: list[dict[str, Any]],
        sample_osdpl_deployed: dict[str, Any],
    ) -> None:
        """Test cluster health when components are degraded."""
        from mosk_mcp.adapters.ceph import CephHealthStatus

        mock_k8s_adapter.list_nodes = AsyncMock(return_value=sample_nodes_degraded)
        mock_k8s_adapter.list_pods = AsyncMock(return_value=[])
        mock_k8s_adapter.get_openstack_deployment = AsyncMock(return_value=sample_osdpl_deployed)
        mock_k8s_adapter.get_openstack_deployment_status = AsyncMock(return_value=None)
        mock_k8s_adapter.list_machines = AsyncMock(return_value=[])
        # Mock OSDPL discovery
        mock_k8s_adapter.list_openstack_deployments = AsyncMock(
            return_value=[
                {"metadata": {"name": "mos", "namespace": "openstack"}},
            ]
        )

        with patch("mosk_mcp.tools.cluster_health.get_ceph_health.CephAdapter") as MockCeph:
            mock_ceph = AsyncMock()
            mock_status = MagicMock()
            mock_status.health = CephHealthStatus.HEALTH_WARN
            mock_status.health_checks = {"OSD_DOWN": {"summary": {"message": "1 down"}}}
            mock_status.num_osds = 6
            mock_status.num_osds_up = 5
            mock_status.num_osds_in = 6
            mock_status.num_pgs = 128
            mock_status.pg_states = {"active+clean": 120, "degraded": 8}
            mock_status.total_bytes = 10 * 1024**4
            mock_status.used_bytes = 3 * 1024**4
            mock_status.available_bytes = 7 * 1024**4
            mock_status.capacity_percent = 30.0

            mock_ceph.get_cluster_status = AsyncMock(return_value=mock_status)
            mock_ceph.list_osds = AsyncMock(return_value=[])

            MockCeph.return_value.__aenter__.return_value = mock_ceph
            MockCeph.return_value.__aexit__.return_value = None

            input_data = GetClusterHealthInput(osdpl_name="mos")

            result = await get_mosk_cluster_health(mock_k8s_adapter, input_data)

            # With 5 components (20% each) and platform defaulting to 100 (no MCC adapter),
            # the overall score is higher. Check that degraded components are detected.
            assert result.kubernetes.health == HealthStatus.DEGRADED
            assert result.ceph.health == HealthStatus.DEGRADED
            assert result.health_score.kubernetes_score < 90
            assert result.health_score.ceph_score < 90
            assert len(result.recommendations) > 0 or len(result.warnings) > 0


# =============================================================================
# Utility Function Tests
# =============================================================================


class TestUtilityFunctions:
    """Tests for utility functions."""

    def test_score_to_health_state_healthy(self) -> None:
        """Test score mapping for healthy state."""
        assert score_to_health_state(100) == HealthState.HEALTHY
        assert score_to_health_state(95) == HealthState.HEALTHY
        assert score_to_health_state(90) == HealthState.HEALTHY

    def test_score_to_health_state_degraded(self) -> None:
        """Test score mapping for degraded state."""
        assert score_to_health_state(89) == HealthState.DEGRADED
        assert score_to_health_state(75) == HealthState.DEGRADED
        assert score_to_health_state(70) == HealthState.DEGRADED

    def test_score_to_health_state_warning(self) -> None:
        """Test score mapping for warning state."""
        assert score_to_health_state(69) == HealthState.WARNING
        assert score_to_health_state(55) == HealthState.WARNING
        assert score_to_health_state(50) == HealthState.WARNING

    def test_score_to_health_state_critical(self) -> None:
        """Test score mapping for critical state."""
        assert score_to_health_state(49) == HealthState.CRITICAL
        assert score_to_health_state(25) == HealthState.CRITICAL
        assert score_to_health_state(0) == HealthState.CRITICAL

    def test_score_to_health_state_unknown(self) -> None:
        """Test score mapping for invalid scores."""
        assert score_to_health_state(-1) == HealthState.UNKNOWN
        assert score_to_health_state(-100) == HealthState.UNKNOWN

    def test_component_weights_sum_to_one(self) -> None:
        """Test that component weights sum to 1.0."""
        total = sum(COMPONENT_WEIGHTS.values())
        assert abs(total - 1.0) < 0.001


# =============================================================================
# Model Validation Tests
# =============================================================================


class TestModelValidation:
    """Tests for input model validation."""

    def test_cluster_health_input_defaults(self) -> None:
        """Test default values for GetClusterHealthInput."""
        input_data = GetClusterHealthInput()

        assert input_data.include_component_details is True
        assert input_data.include_recommendations is True

    def test_kubernetes_health_input_defaults(self) -> None:
        """Test default values for GetKubernetesHealthInput."""
        input_data = GetKubernetesHealthInput()

        assert input_data.include_node_details is True
        assert input_data.include_system_pods is True

    def test_list_alerts_input_limit_validation(self) -> None:
        """Test limit validation for ListActiveAlertsInput."""
        # Valid limit
        input_data = ListActiveAlertsInput(limit=100)
        assert input_data.limit == 100

        # Max limit
        input_data = ListActiveAlertsInput(limit=500)
        assert input_data.limit == 500

    def test_preflight_check_input_check_types(self) -> None:
        """Test preflight check type enum values."""
        for check_type in PreflightCheckType:
            input_data = RunPreflightCheckInput(check_type=check_type)
            assert input_data.check_type == check_type
