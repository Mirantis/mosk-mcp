"""Unit tests for get_openstack_health tool."""

from unittest.mock import AsyncMock

import pytest

from mosk_mcp.core.exceptions import ToolExecutionError
from mosk_mcp.tools.cluster_health.get_openstack_health import (
    _calculate_compute_score,
    _calculate_control_plane_score,
    _extract_hypervisor_health,
    _extract_service_health_from_osdplst,
    _generate_recommendations,
    get_openstack_health,
)
from mosk_mcp.tools.cluster_health.models import (
    GetOpenStackHealthInput,
    HypervisorHealthInfo,
    ServiceHealthInfo,
)
from mosk_mcp.tools.common.enums import HealthStatus


class TestCalculateControlPlaneScore:
    """Tests for _calculate_control_plane_score function."""

    def test_fully_healthy_cluster_with_osdplst(self) -> None:
        """Test fully healthy cluster with OSDPLStatus data."""
        services = [
            ServiceHealthInfo(
                name="keystone",
                healthy=True,
                endpoint_healthy=True,
            ),
            ServiceHealthInfo(
                name="nova",
                healthy=True,
                endpoint_healthy=True,
            ),
        ]
        score = _calculate_control_plane_score(
            services=services,
            osdpl_phase="Deployed",
            is_upgrading=False,
            osdplst_state="APPLIED",
            osdplst_health_ready=22,
            osdplst_health_total=22,
        )
        # 70 (health ratio) + 20 (APPLIED state) + 10 (endpoints) = 100
        assert score == 100

    def test_partial_health_with_osdplst(self) -> None:
        """Test partial health from OSDPLStatus."""
        services = [
            ServiceHealthInfo(name="keystone", healthy=True, endpoint_healthy=True),
            ServiceHealthInfo(name="nova", healthy=True, endpoint_healthy=True),
        ]
        score = _calculate_control_plane_score(
            services=services,
            osdpl_phase="Deployed",
            is_upgrading=False,
            osdplst_state="APPLIED",
            osdplst_health_ready=20,
            osdplst_health_total=22,
        )
        # 20/22 * 70 = 63 + 20 (APPLIED) + 10 (endpoints) = 93
        expected_health_score = int((20 / 22) * 70)
        assert score == expected_health_score + 20 + 10

    def test_applying_state_reduces_score(self) -> None:
        """Test APPLYING state gives partial credit."""
        services = [
            ServiceHealthInfo(name="keystone", healthy=True, endpoint_healthy=True),
        ]
        score = _calculate_control_plane_score(
            services=services,
            osdpl_phase="Upgrading",
            is_upgrading=True,
            osdplst_state="APPLYING",
            osdplst_health_ready=22,
            osdplst_health_total=22,
        )
        # 70 (health ratio) + 10 (APPLYING state) + 10 (endpoints) = 90
        assert score == 90

    def test_failed_state_zero_credit(self) -> None:
        """Test FAILED state gives zero state credit."""
        services = [
            ServiceHealthInfo(name="keystone", healthy=True, endpoint_healthy=True),
        ]
        score = _calculate_control_plane_score(
            services=services,
            osdpl_phase="Failed",
            is_upgrading=False,
            osdplst_state="FAILED",
            osdplst_health_ready=22,
            osdplst_health_total=22,
        )
        # 70 (health ratio) + 0 (FAILED state) + 10 (endpoints) = 80
        assert score == 80

    def test_unknown_osdplst_state(self) -> None:
        """Test unknown OSDPLStatus state gives minimal credit."""
        services = [
            ServiceHealthInfo(name="keystone", healthy=True, endpoint_healthy=True),
        ]
        score = _calculate_control_plane_score(
            services=services,
            osdpl_phase="Deployed",
            is_upgrading=False,
            osdplst_state="UNKNOWN_STATE",
            osdplst_health_ready=22,
            osdplst_health_total=22,
        )
        # 70 (health ratio) + 5 (unknown state) + 10 (endpoints) = 85
        assert score == 85

    def test_missing_osdplst_state(self) -> None:
        """Test missing OSDPLStatus gives zero state credit."""
        services = [
            ServiceHealthInfo(name="keystone", healthy=True, endpoint_healthy=True),
        ]
        score = _calculate_control_plane_score(
            services=services,
            osdpl_phase="Deployed",
            is_upgrading=False,
            osdplst_state=None,
            osdplst_health_ready=None,
            osdplst_health_total=None,
        )
        # Service-based: 1/1 * 70 = 70 + 0 (no state) + 10 (endpoints) = 80
        assert score == 80

    def test_fallback_to_services_without_osdplst_health(self) -> None:
        """Test fallback to services when no osdplst health data."""
        services = [
            ServiceHealthInfo(name="keystone", healthy=True, endpoint_healthy=True),
            ServiceHealthInfo(name="nova", healthy=False, endpoint_healthy=False),
        ]
        score = _calculate_control_plane_score(
            services=services,
            osdpl_phase="Deployed",
            is_upgrading=False,
            osdplst_state="APPLIED",
            osdplst_health_ready=None,
            osdplst_health_total=None,
        )
        # 1/2 * 70 = 35 + 20 (APPLIED) + 5 (1/2 endpoints) = 60
        assert score == 35 + 20 + 5

    def test_no_services_configured(self) -> None:
        """Test no services configured still gives base score."""
        score = _calculate_control_plane_score(
            services=[],
            osdpl_phase="Deployed",
            is_upgrading=False,
            osdplst_state="APPLIED",
            osdplst_health_ready=None,
            osdplst_health_total=None,
        )
        # 70 (no services = OK) + 20 (APPLIED) + 10 (no services = OK) = 100
        assert score == 100

    def test_score_clamped_to_valid_range(self) -> None:
        """Test score is clamped between 0 and 100."""
        # Test minimum clamping
        services = [
            ServiceHealthInfo(name="keystone", healthy=False, endpoint_healthy=False),
        ]
        score = _calculate_control_plane_score(
            services=services,
            osdpl_phase="Failed",
            is_upgrading=False,
            osdplst_state="FAILED",
            osdplst_health_ready=0,
            osdplst_health_total=22,
        )
        assert 0 <= score <= 100


class TestCalculateComputeScore:
    """Tests for _calculate_compute_score function."""

    def test_no_hypervisors(self) -> None:
        """Test no hypervisors returns 100."""
        score = _calculate_compute_score([])
        assert score == 100

    def test_all_healthy_hypervisors(self) -> None:
        """Test all healthy hypervisors."""
        hypervisors = [
            HypervisorHealthInfo(
                hostname="compute-01",
                status="enabled",
                state="up",
                healthy=True,
            ),
            HypervisorHealthInfo(
                hostname="compute-02",
                status="enabled",
                state="up",
                healthy=True,
            ),
        ]
        score = _calculate_compute_score(hypervisors)
        assert score == 100

    def test_partial_healthy_hypervisors(self) -> None:
        """Test some unhealthy hypervisors."""
        hypervisors = [
            HypervisorHealthInfo(
                hostname="compute-01",
                status="enabled",
                state="up",
                healthy=True,
            ),
            HypervisorHealthInfo(
                hostname="compute-02",
                status="disabled",
                state="down",
                healthy=False,
            ),
        ]
        score = _calculate_compute_score(hypervisors)
        assert score == 50  # 1/2 healthy

    def test_no_healthy_hypervisors(self) -> None:
        """Test all unhealthy hypervisors."""
        hypervisors = [
            HypervisorHealthInfo(
                hostname="compute-01",
                status="disabled",
                state="down",
                healthy=False,
            ),
            HypervisorHealthInfo(
                hostname="compute-02",
                status="disabled",
                state="down",
                healthy=False,
            ),
        ]
        score = _calculate_compute_score(hypervisors)
        assert score == 0


class TestExtractServiceHealthFromOsdplst:
    """Tests for _extract_service_health_from_osdplst function."""

    def test_all_components_ready(self) -> None:
        """Test service with all components Ready."""
        service_health = {
            "api": {"status": "Ready"},
            "conductor": {"status": "Ready"},
            "scheduler": {"status": "Ready"},
        }
        result = _extract_service_health_from_osdplst(
            service_name="nova",
            service_health=service_health,
            endpoint="http://nova.openstack.svc:8774",
        )

        assert result.name == "nova"
        assert result.healthy is True
        assert result.replicas_desired == 3
        assert result.replicas_ready == 3
        assert result.endpoint_healthy is True
        assert result.issues == []

    def test_some_components_not_ready(self) -> None:
        """Test service with some components not Ready."""
        service_health = {
            "api": {"status": "Ready"},
            "conductor": {"status": "Ready"},
            "scheduler": {"status": "NotReady"},
        }
        result = _extract_service_health_from_osdplst(
            service_name="nova",
            service_health=service_health,
        )

        assert result.name == "nova"
        assert result.healthy is False
        assert result.replicas_desired == 3
        assert result.replicas_ready == 2
        assert len(result.issues) == 1
        assert "nova/scheduler: NotReady" in result.issues[0]

    def test_with_lcm_service_data(self) -> None:
        """Test with LCM service data included."""
        service_health = {
            "api": {"status": "Ready"},
        }
        lcm_service = {
            "state": "APPLIED",
            "release": "17.4.0+25.1",
            "timestamp": "2024-01-15T10:30:00Z",
        }
        result = _extract_service_health_from_osdplst(
            service_name="keystone",
            service_health=service_health,
            lcm_service=lcm_service,
            endpoint="http://keystone.openstack.svc:5000",
        )

        assert result.lcm_state == "APPLIED"
        assert result.lcm_release == "17.4.0+25.1"
        assert result.lcm_timestamp == "2024-01-15T10:30:00Z"
        assert result.endpoint_healthy is True

    def test_lcm_not_applied_adds_issue(self) -> None:
        """Test LCM state not APPLIED adds issue."""
        service_health = {
            "api": {"status": "Ready"},
        }
        lcm_service = {
            "state": "APPLYING",
        }
        result = _extract_service_health_from_osdplst(
            service_name="keystone",
            service_health=service_health,
            lcm_service=lcm_service,
        )

        assert "LCM state: APPLYING" in result.issues

    def test_empty_service_health(self) -> None:
        """Test empty service health data."""
        result = _extract_service_health_from_osdplst(
            service_name="test",
            service_health={},
        )

        assert result.name == "test"
        assert result.healthy is False  # No components = not healthy
        assert result.replicas_desired == 0
        assert result.replicas_ready == 0

    def test_no_endpoint(self) -> None:
        """Test endpoint_healthy is False when no endpoint."""
        service_health = {
            "api": {"status": "Ready"},
        }
        result = _extract_service_health_from_osdplst(
            service_name="test",
            service_health=service_health,
            endpoint=None,
        )

        assert result.endpoint_healthy is False

    def test_non_dict_component_data_ignored(self) -> None:
        """Test non-dict component data is skipped."""
        service_health = {
            "api": {"status": "Ready"},
            "some_string_value": "not a dict",
            "some_number": 123,
            "conductor": {"status": "Ready"},
        }
        result = _extract_service_health_from_osdplst(
            service_name="nova",
            service_health=service_health,
        )

        # Only dict items should be counted as components
        assert result.replicas_desired == 2  # api and conductor only
        assert result.replicas_ready == 2
        assert result.healthy is True


class TestExtractHypervisorHealth:
    """Tests for _extract_hypervisor_health function."""

    def test_healthy_hypervisor(self) -> None:
        """Test healthy hypervisor extraction."""
        hypervisor_data = {
            "hypervisor_hostname": "compute-01",
            "status": "enabled",
            "state": "up",
            "vcpus_used": 10,
            "vcpus": 32,
            "memory_mb_used": 8192,
            "memory_mb": 65536,
            "running_vms": 5,
        }
        result = _extract_hypervisor_health(hypervisor_data)

        assert result.hostname == "compute-01"
        assert result.status == "enabled"
        assert result.state == "up"
        assert result.healthy is True
        assert result.vcpus_used == 10
        assert result.vcpus_total == 32
        assert result.memory_used_mb == 8192
        assert result.memory_total_mb == 65536
        assert result.running_vms == 5

    def test_unhealthy_hypervisor_disabled(self) -> None:
        """Test unhealthy hypervisor - disabled."""
        hypervisor_data = {
            "hypervisor_hostname": "compute-02",
            "status": "disabled",
            "state": "up",
        }
        result = _extract_hypervisor_health(hypervisor_data)

        assert result.hostname == "compute-02"
        assert result.healthy is False

    def test_unhealthy_hypervisor_down(self) -> None:
        """Test unhealthy hypervisor - down."""
        hypervisor_data = {
            "hypervisor_hostname": "compute-03",
            "status": "enabled",
            "state": "down",
        }
        result = _extract_hypervisor_health(hypervisor_data)

        assert result.hostname == "compute-03"
        assert result.healthy is False

    def test_simplified_format(self) -> None:
        """Test simplified data format with hostname key."""
        hypervisor_data = {
            "hostname": "compute-04",
            "status": "enabled",
            "state": "up",
        }
        result = _extract_hypervisor_health(hypervisor_data)

        assert result.hostname == "compute-04"
        assert result.healthy is True

    def test_missing_fields_use_defaults(self) -> None:
        """Test missing fields use default values."""
        hypervisor_data = {}
        result = _extract_hypervisor_health(hypervisor_data)

        assert result.hostname == "unknown"
        assert result.vcpus_used == 0
        assert result.vcpus_total == 0


class TestGenerateRecommendations:
    """Tests for _generate_recommendations function."""

    def test_healthy_cluster_no_recommendations(self) -> None:
        """Test healthy cluster produces no recommendations."""
        result = _generate_recommendations(
            control_score=100,
            compute_score=100,
            services=[
                ServiceHealthInfo(name="keystone", healthy=True),
            ],
            hypervisors=[
                HypervisorHealthInfo(
                    hostname="compute-01",
                    status="enabled",
                    state="up",
                    healthy=True,
                ),
            ],
            is_upgrading=False,
            osdpl_phase="Deployed",
            osdplst_state="APPLIED",
            osdplst_health_ready=22,
            osdplst_health_total=22,
        )
        assert result == []

    def test_osdplst_applying_state(self) -> None:
        """Test APPLYING state produces recommendation."""
        result = _generate_recommendations(
            control_score=90,
            compute_score=100,
            services=[],
            hypervisors=[],
            is_upgrading=True,
            osdpl_phase="Upgrading",
            osdplst_state="APPLYING",
        )
        assert any("APPLYING" in r for r in result)
        assert any("avoid maintenance" in r for r in result)

    def test_osdplst_failed_state(self) -> None:
        """Test FAILED state produces recommendation."""
        result = _generate_recommendations(
            control_score=50,
            compute_score=100,
            services=[],
            hypervisors=[],
            is_upgrading=False,
            osdpl_phase="Failed",
            osdplst_state="FAILED",
        )
        assert any("FAILED" in r for r in result)
        assert any("controller logs" in r for r in result)

    def test_legacy_upgrade_in_progress(self) -> None:
        """Test legacy upgrade detection when no osdplst state."""
        result = _generate_recommendations(
            control_score=90,
            compute_score=100,
            services=[],
            hypervisors=[],
            is_upgrading=True,
            osdpl_phase="Upgrading",
            osdplst_state=None,
        )
        assert any("Upgrade in progress" in r for r in result)

    def test_legacy_failed_phase(self) -> None:
        """Test legacy Failed phase detection when no osdplst state."""
        result = _generate_recommendations(
            control_score=50,
            compute_score=100,
            services=[],
            hypervisors=[],
            is_upgrading=False,
            osdpl_phase="Failed",
            osdplst_state=None,
        )
        assert any("Failed state" in r for r in result)

    def test_unhealthy_components_from_osdplst(self) -> None:
        """Test unhealthy component count recommendation."""
        result = _generate_recommendations(
            control_score=80,
            compute_score=100,
            services=[],
            hypervisors=[],
            is_upgrading=False,
            osdpl_phase="Deployed",
            osdplst_state="APPLIED",
            osdplst_health_ready=20,
            osdplst_health_total=22,
        )
        assert any("2 component(s) not healthy" in r for r in result)

    def test_unhealthy_services(self) -> None:
        """Test unhealthy services produce recommendations."""
        result = _generate_recommendations(
            control_score=70,
            compute_score=100,
            services=[
                ServiceHealthInfo(name="keystone", healthy=False),
                ServiceHealthInfo(name="nova", healthy=False),
                ServiceHealthInfo(name="neutron", healthy=True),
            ],
            hypervisors=[],
            is_upgrading=False,
            osdpl_phase="Deployed",
            osdplst_state="APPLIED",
        )
        assert any("keystone unhealthy" in r for r in result)
        assert any("nova unhealthy" in r for r in result)

    def test_unhealthy_hypervisors(self) -> None:
        """Test unhealthy hypervisors produce recommendation."""
        result = _generate_recommendations(
            control_score=100,
            compute_score=50,
            services=[],
            hypervisors=[
                HypervisorHealthInfo(
                    hostname="compute-01",
                    status="enabled",
                    state="up",
                    healthy=True,
                ),
                HypervisorHealthInfo(
                    hostname="compute-02",
                    status="disabled",
                    state="down",
                    healthy=False,
                ),
            ],
            is_upgrading=False,
            osdpl_phase="Deployed",
            osdplst_state="APPLIED",
        )
        assert any("1 hypervisor(s) unhealthy" in r for r in result)

    def test_high_cpu_utilization(self) -> None:
        """Test high CPU utilization recommendation."""
        result = _generate_recommendations(
            control_score=100,
            compute_score=100,
            services=[],
            hypervisors=[
                HypervisorHealthInfo(
                    hostname="compute-01",
                    status="enabled",
                    state="up",
                    healthy=True,
                    vcpus_used=95,
                    vcpus_total=100,
                ),
            ],
            is_upgrading=False,
            osdpl_phase="Deployed",
            osdplst_state="APPLIED",
        )
        assert any(">90% CPU" in r for r in result)

    def test_max_recommendations_limit(self) -> None:
        """Test recommendations are limited to 10."""
        unhealthy_services = [
            ServiceHealthInfo(name=f"service-{i}", healthy=False) for i in range(15)
        ]
        result = _generate_recommendations(
            control_score=10,
            compute_score=10,
            services=unhealthy_services,
            hypervisors=[
                HypervisorHealthInfo(
                    hostname=f"compute-{i}",
                    status="disabled",
                    state="down",
                    healthy=False,
                )
                for i in range(10)
            ],
            is_upgrading=True,
            osdpl_phase="Failed",
            osdplst_state="FAILED",
            osdplst_health_ready=0,
            osdplst_health_total=22,
        )
        assert len(result) <= 10


class TestGetOpenStackHealth:
    """Tests for get_openstack_health function."""

    @pytest.fixture
    def mock_kubernetes_adapter(self) -> AsyncMock:
        """Create mock MOSK Kubernetes adapter."""
        return AsyncMock()

    @pytest.fixture
    def mock_mcc_adapter(self) -> AsyncMock:
        """Create mock MCC Kubernetes adapter."""
        return AsyncMock()

    @pytest.fixture
    def healthy_osdpl(self) -> dict:
        """Create healthy OSDPL response."""
        return {
            "metadata": {"name": "mos", "namespace": "openstack"},
            "spec": {"openStackVersion": "antelope"},
            "status": {
                "phase": "Deployed",
                "openStackVersion": "antelope",
                "endpoints": {
                    "keystone": "http://keystone.openstack.svc:5000",
                    "nova": "http://nova.openstack.svc:8774",
                },
            },
        }

    @pytest.fixture
    def healthy_osdplst(self) -> dict:
        """Create healthy OSDPLStatus response."""
        return {
            "metadata": {"name": "mos", "namespace": "openstack"},
            "status": {
                "osdpl": {
                    "state": "APPLIED",
                    "health": "22/22",
                    "release": "17.4.0+25.1",
                    "openstackVersion": "antelope",
                },
                "health": {
                    "keystone": {
                        "api": {"status": "Ready"},
                    },
                    "nova": {
                        "api": {"status": "Ready"},
                        "conductor": {"status": "Ready"},
                        "scheduler": {"status": "Ready"},
                    },
                },
                "services": {
                    "identity": {
                        "state": "APPLIED",
                        "release": "17.4.0+25.1",
                        "timestamp": "2024-01-15T10:30:00Z",
                    },
                    "compute": {
                        "state": "APPLIED",
                        "release": "17.4.0+25.1",
                        "timestamp": "2024-01-15T10:30:00Z",
                    },
                },
            },
        }

    @pytest.mark.asyncio
    async def test_healthy_cluster(
        self,
        mock_kubernetes_adapter: AsyncMock,
        mock_mcc_adapter: AsyncMock,
        healthy_osdpl: dict,
        healthy_osdplst: dict,
    ) -> None:
        """Test healthy OpenStack cluster."""
        mock_kubernetes_adapter.get_openstack_deployment.return_value = healthy_osdpl
        mock_kubernetes_adapter.get_openstack_deployment_status.return_value = healthy_osdplst
        mock_mcc_adapter.get_mosk_machines_namespace.return_value = "lab"
        mock_mcc_adapter.list_machines.return_value = [
            {
                "metadata": {"name": "compute-01"},
                "status": {"phase": "Ready"},
            },
        ]

        result = await get_openstack_health(
            mock_kubernetes_adapter,
            GetOpenStackHealthInput(osdpl_name="mos"),
            mock_mcc_adapter,
        )

        assert result.control_plane_health == HealthStatus.HEALTHY
        assert result.compute_health == HealthStatus.HEALTHY
        assert result.osdplst_state == "APPLIED"
        assert result.osdplst_health == "22/22"
        assert result.openstack_version == "antelope"
        assert result.mosk_release == "17.4.0+25.1"
        assert result.is_upgrading is False

    @pytest.mark.asyncio
    async def test_osdpl_not_found(
        self,
        mock_kubernetes_adapter: AsyncMock,
    ) -> None:
        """Test OSDPL not found raises error."""
        mock_kubernetes_adapter.get_openstack_deployment.return_value = None

        with pytest.raises(ToolExecutionError) as exc_info:
            await get_openstack_health(
                mock_kubernetes_adapter,
                GetOpenStackHealthInput(osdpl_name="missing"),
            )

        assert "not found" in str(exc_info.value)
        assert exc_info.value.tool_name == "get_openstack_health"

    @pytest.mark.asyncio
    async def test_osdplst_failure_handled(
        self,
        mock_kubernetes_adapter: AsyncMock,
        healthy_osdpl: dict,
    ) -> None:
        """Test OSDPLStatus failure is handled gracefully."""
        mock_kubernetes_adapter.get_openstack_deployment.return_value = healthy_osdpl
        mock_kubernetes_adapter.get_openstack_deployment_status.side_effect = Exception(
            "OSDPLStatus not found"
        )

        result = await get_openstack_health(
            mock_kubernetes_adapter,
            GetOpenStackHealthInput(osdpl_name="mos"),
        )

        # Should still return result, just without osdplst data
        assert result.osdplst_state is None
        assert "Could not retrieve OSDPLStatus" in result.issues[0]

    @pytest.mark.asyncio
    async def test_failed_osdplst_state(
        self,
        mock_kubernetes_adapter: AsyncMock,
        mock_mcc_adapter: AsyncMock,
        healthy_osdpl: dict,
    ) -> None:
        """Test FAILED OSDPLStatus state."""
        mock_kubernetes_adapter.get_openstack_deployment.return_value = healthy_osdpl
        mock_kubernetes_adapter.get_openstack_deployment_status.return_value = {
            "status": {
                "osdpl": {
                    "state": "FAILED",
                    "health": "20/22",
                },
                "health": {},
                "services": {},
            },
        }
        mock_mcc_adapter.get_mosk_machines_namespace.return_value = "lab"
        mock_mcc_adapter.list_machines.return_value = []

        result = await get_openstack_health(
            mock_kubernetes_adapter,
            GetOpenStackHealthInput(osdpl_name="mos"),
            mock_mcc_adapter,
        )

        assert result.osdplst_state == "FAILED"
        assert "OSDPLStatus is in FAILED state" in result.issues

    @pytest.mark.asyncio
    async def test_upgrading_state(
        self,
        mock_kubernetes_adapter: AsyncMock,
        healthy_osdpl: dict,
    ) -> None:
        """Test upgrading/applying state detection."""
        mock_kubernetes_adapter.get_openstack_deployment.return_value = healthy_osdpl
        mock_kubernetes_adapter.get_openstack_deployment_status.return_value = {
            "status": {
                "osdpl": {
                    "state": "APPLYING",
                    "health": "22/22",
                },
                "health": {},
                "services": {},
            },
        }

        result = await get_openstack_health(
            mock_kubernetes_adapter,
            GetOpenStackHealthInput(osdpl_name="mos"),
        )

        assert result.osdplst_state == "APPLYING"
        assert result.is_upgrading is True

    @pytest.mark.asyncio
    async def test_without_mcc_adapter(
        self,
        mock_kubernetes_adapter: AsyncMock,
        healthy_osdpl: dict,
        healthy_osdplst: dict,
    ) -> None:
        """Test without MCC adapter skips hypervisor check."""
        mock_kubernetes_adapter.get_openstack_deployment.return_value = healthy_osdpl
        mock_kubernetes_adapter.get_openstack_deployment_status.return_value = healthy_osdplst

        result = await get_openstack_health(
            mock_kubernetes_adapter,
            GetOpenStackHealthInput(osdpl_name="mos"),
            mcc_adapter=None,
        )

        # Hypervisors should be empty
        assert result.hypervisors_total == 0
        assert result.hypervisors == []
        # Compute should still be healthy (no hypervisors = 100)
        assert result.compute_health == HealthStatus.HEALTHY

    @pytest.mark.asyncio
    async def test_namespace_discovery_failure(
        self,
        mock_kubernetes_adapter: AsyncMock,
        mock_mcc_adapter: AsyncMock,
        healthy_osdpl: dict,
        healthy_osdplst: dict,
    ) -> None:
        """Test namespace discovery failure is handled."""
        mock_kubernetes_adapter.get_openstack_deployment.return_value = healthy_osdpl
        mock_kubernetes_adapter.get_openstack_deployment_status.return_value = healthy_osdplst
        mock_mcc_adapter.get_mosk_machines_namespace.return_value = None

        result = await get_openstack_health(
            mock_kubernetes_adapter,
            GetOpenStackHealthInput(osdpl_name="mos"),
            mock_mcc_adapter,
        )

        # Should still succeed, just with no hypervisors
        assert result.hypervisors_total == 0
        # No issues added for namespace discovery failure
        assert not any("namespace" in issue.lower() for issue in result.issues)

    @pytest.mark.asyncio
    async def test_hypervisor_query_failure(
        self,
        mock_kubernetes_adapter: AsyncMock,
        mock_mcc_adapter: AsyncMock,
        healthy_osdpl: dict,
        healthy_osdplst: dict,
    ) -> None:
        """Test hypervisor query failure is handled."""
        mock_kubernetes_adapter.get_openstack_deployment.return_value = healthy_osdpl
        mock_kubernetes_adapter.get_openstack_deployment_status.return_value = healthy_osdplst
        mock_mcc_adapter.get_mosk_machines_namespace.return_value = "lab"
        mock_mcc_adapter.list_machines.side_effect = Exception("API error")

        result = await get_openstack_health(
            mock_kubernetes_adapter,
            GetOpenStackHealthInput(osdpl_name="mos"),
            mock_mcc_adapter,
        )

        # Should add issue for hypervisor failure
        assert any("hypervisor" in issue.lower() for issue in result.issues)

    @pytest.mark.asyncio
    async def test_include_services_false(
        self,
        mock_kubernetes_adapter: AsyncMock,
        healthy_osdpl: dict,
        healthy_osdplst: dict,
    ) -> None:
        """Test include_services=False returns empty services list."""
        mock_kubernetes_adapter.get_openstack_deployment.return_value = healthy_osdpl
        mock_kubernetes_adapter.get_openstack_deployment_status.return_value = healthy_osdplst

        result = await get_openstack_health(
            mock_kubernetes_adapter,
            GetOpenStackHealthInput(osdpl_name="mos", include_services=False),
        )

        assert result.services == []

    @pytest.mark.asyncio
    async def test_include_endpoints_false(
        self,
        mock_kubernetes_adapter: AsyncMock,
        healthy_osdpl: dict,
        healthy_osdplst: dict,
    ) -> None:
        """Test include_endpoints=False returns empty endpoints."""
        mock_kubernetes_adapter.get_openstack_deployment.return_value = healthy_osdpl
        mock_kubernetes_adapter.get_openstack_deployment_status.return_value = healthy_osdplst

        result = await get_openstack_health(
            mock_kubernetes_adapter,
            GetOpenStackHealthInput(osdpl_name="mos", include_endpoints=False),
        )

        assert result.endpoints == {}

    @pytest.mark.asyncio
    async def test_service_health_extraction(
        self,
        mock_kubernetes_adapter: AsyncMock,
        healthy_osdpl: dict,
        healthy_osdplst: dict,
    ) -> None:
        """Test service health is extracted from osdplst."""
        mock_kubernetes_adapter.get_openstack_deployment.return_value = healthy_osdpl
        mock_kubernetes_adapter.get_openstack_deployment_status.return_value = healthy_osdplst

        result = await get_openstack_health(
            mock_kubernetes_adapter,
            GetOpenStackHealthInput(osdpl_name="mos", include_services=True),
        )

        # Should have keystone and nova services
        service_names = [s.name for s in result.services]
        assert "keystone" in service_names
        assert "nova" in service_names

        # Check nova has correct component counts
        nova_service = next(s for s in result.services if s.name == "nova")
        assert nova_service.replicas_desired == 3  # api, conductor, scheduler
        assert nova_service.replicas_ready == 3
        assert nova_service.healthy is True

    @pytest.mark.asyncio
    async def test_unhealthy_service_detection(
        self,
        mock_kubernetes_adapter: AsyncMock,
        healthy_osdpl: dict,
    ) -> None:
        """Test unhealthy service detection adds issues."""
        osdplst_unhealthy = {
            "status": {
                "osdpl": {
                    "state": "APPLIED",
                    "health": "20/22",
                },
                "health": {
                    "nova": {
                        "api": {"status": "Ready"},
                        "conductor": {"status": "NotReady"},
                        "scheduler": {"status": "Ready"},
                    },
                },
                "services": {
                    "compute": {"state": "APPLIED"},
                },
            },
        }
        mock_kubernetes_adapter.get_openstack_deployment.return_value = healthy_osdpl
        mock_kubernetes_adapter.get_openstack_deployment_status.return_value = osdplst_unhealthy

        result = await get_openstack_health(
            mock_kubernetes_adapter,
            GetOpenStackHealthInput(osdpl_name="mos", include_services=True),
        )

        # Should have nova as unhealthy
        nova_service = next(s for s in result.services if s.name == "nova")
        assert nova_service.healthy is False
        assert "nova/conductor: NotReady" in nova_service.issues

        # Should have issue added
        assert any("nova" in issue.lower() for issue in result.issues)

    @pytest.mark.asyncio
    async def test_hypervisor_health_from_machines(
        self,
        mock_kubernetes_adapter: AsyncMock,
        mock_mcc_adapter: AsyncMock,
        healthy_osdpl: dict,
        healthy_osdplst: dict,
    ) -> None:
        """Test hypervisor health extracted from Machine CRs."""
        mock_kubernetes_adapter.get_openstack_deployment.return_value = healthy_osdpl
        mock_kubernetes_adapter.get_openstack_deployment_status.return_value = healthy_osdplst
        mock_mcc_adapter.get_mosk_machines_namespace.return_value = "lab"
        mock_mcc_adapter.list_machines.return_value = [
            {
                "metadata": {"name": "compute-01"},
                "status": {"phase": "Ready"},
            },
            {
                "metadata": {"name": "compute-02"},
                "status": {"phase": "Ready"},
            },
            {
                "metadata": {"name": "compute-03"},
                "status": {"phase": "Provisioning"},
            },
        ]

        result = await get_openstack_health(
            mock_kubernetes_adapter,
            GetOpenStackHealthInput(osdpl_name="mos"),
            mock_mcc_adapter,
        )

        assert result.hypervisors_total == 3
        assert result.hypervisors_healthy == 2

        # Check individual hypervisors
        compute01 = next(h for h in result.hypervisors if h.hostname == "compute-01")
        assert compute01.healthy is True

        compute03 = next(h for h in result.hypervisors if h.hostname == "compute-03")
        assert compute03.healthy is False

    @pytest.mark.asyncio
    async def test_timestamp_included(
        self,
        mock_kubernetes_adapter: AsyncMock,
        healthy_osdpl: dict,
        healthy_osdplst: dict,
    ) -> None:
        """Test timestamp is included in output."""
        mock_kubernetes_adapter.get_openstack_deployment.return_value = healthy_osdpl
        mock_kubernetes_adapter.get_openstack_deployment_status.return_value = healthy_osdplst

        result = await get_openstack_health(
            mock_kubernetes_adapter,
            GetOpenStackHealthInput(osdpl_name="mos"),
        )

        assert result.timestamp is not None
        assert "T" in result.timestamp  # ISO format

    @pytest.mark.asyncio
    async def test_message_healthy(
        self,
        mock_kubernetes_adapter: AsyncMock,
        mock_mcc_adapter: AsyncMock,
        healthy_osdpl: dict,
        healthy_osdplst: dict,
    ) -> None:
        """Test healthy message format."""
        mock_kubernetes_adapter.get_openstack_deployment.return_value = healthy_osdpl
        mock_kubernetes_adapter.get_openstack_deployment_status.return_value = healthy_osdplst
        mock_mcc_adapter.get_mosk_machines_namespace.return_value = "lab"
        mock_mcc_adapter.list_machines.return_value = []

        result = await get_openstack_health(
            mock_kubernetes_adapter,
            GetOpenStackHealthInput(osdpl_name="mos"),
            mock_mcc_adapter,
        )

        assert "healthy" in result.message.lower()
        assert "APPLIED" in result.message

    @pytest.mark.asyncio
    async def test_message_degraded(
        self,
        mock_kubernetes_adapter: AsyncMock,
        healthy_osdpl: dict,
    ) -> None:
        """Test degraded message format."""
        osdplst_degraded = {
            "status": {
                "osdpl": {
                    "state": "APPLYING",
                    "health": "18/22",
                },
                "health": {},
                "services": {},
            },
        }
        mock_kubernetes_adapter.get_openstack_deployment.return_value = healthy_osdpl
        mock_kubernetes_adapter.get_openstack_deployment_status.return_value = osdplst_degraded

        result = await get_openstack_health(
            mock_kubernetes_adapter,
            GetOpenStackHealthInput(osdpl_name="mos"),
        )

        assert "degraded" in result.message.lower()

    @pytest.mark.asyncio
    async def test_recommendations_generated(
        self,
        mock_kubernetes_adapter: AsyncMock,
        mock_mcc_adapter: AsyncMock,
        healthy_osdpl: dict,
    ) -> None:
        """Test recommendations are generated for issues."""
        osdplst_issues = {
            "status": {
                "osdpl": {
                    "state": "APPLYING",
                    "health": "20/22",
                },
                "health": {},
                "services": {},
            },
        }
        mock_kubernetes_adapter.get_openstack_deployment.return_value = healthy_osdpl
        mock_kubernetes_adapter.get_openstack_deployment_status.return_value = osdplst_issues
        mock_mcc_adapter.get_mosk_machines_namespace.return_value = "lab"
        mock_mcc_adapter.list_machines.return_value = [
            {
                "metadata": {"name": "compute-01"},
                "status": {"phase": "Failed"},
            },
        ]

        result = await get_openstack_health(
            mock_kubernetes_adapter,
            GetOpenStackHealthInput(osdpl_name="mos"),
            mock_mcc_adapter,
        )

        assert len(result.recommendations) > 0

    @pytest.mark.asyncio
    async def test_general_exception_handling(
        self,
        mock_kubernetes_adapter: AsyncMock,
    ) -> None:
        """Test general exception is wrapped in ToolExecutionError."""
        mock_kubernetes_adapter.get_openstack_deployment.side_effect = Exception("Unexpected error")

        with pytest.raises(ToolExecutionError) as exc_info:
            await get_openstack_health(
                mock_kubernetes_adapter,
                GetOpenStackHealthInput(osdpl_name="mos"),
            )

        assert "Failed to get OpenStack health" in str(exc_info.value)
        assert exc_info.value.tool_name == "get_openstack_health"

    @pytest.mark.asyncio
    async def test_control_plane_score_calculation(
        self,
        mock_kubernetes_adapter: AsyncMock,
        healthy_osdpl: dict,
        healthy_osdplst: dict,
    ) -> None:
        """Test control plane score is calculated correctly."""
        mock_kubernetes_adapter.get_openstack_deployment.return_value = healthy_osdpl
        mock_kubernetes_adapter.get_openstack_deployment_status.return_value = healthy_osdplst

        result = await get_openstack_health(
            mock_kubernetes_adapter,
            GetOpenStackHealthInput(osdpl_name="mos"),
        )

        assert 0 <= result.control_plane_score <= 100
        assert result.control_plane_score >= 90  # Healthy cluster

    @pytest.mark.asyncio
    async def test_compute_score_calculation(
        self,
        mock_kubernetes_adapter: AsyncMock,
        mock_mcc_adapter: AsyncMock,
        healthy_osdpl: dict,
        healthy_osdplst: dict,
    ) -> None:
        """Test compute score is calculated correctly."""
        mock_kubernetes_adapter.get_openstack_deployment.return_value = healthy_osdpl
        mock_kubernetes_adapter.get_openstack_deployment_status.return_value = healthy_osdplst
        mock_mcc_adapter.get_mosk_machines_namespace.return_value = "lab"
        mock_mcc_adapter.list_machines.return_value = [
            {"metadata": {"name": "compute-01"}, "status": {"phase": "Ready"}},
            {"metadata": {"name": "compute-02"}, "status": {"phase": "Ready"}},
        ]

        result = await get_openstack_health(
            mock_kubernetes_adapter,
            GetOpenStackHealthInput(osdpl_name="mos"),
            mock_mcc_adapter,
        )

        assert result.compute_score == 100  # All healthy

    @pytest.mark.asyncio
    async def test_osdplst_returns_none(
        self,
        mock_kubernetes_adapter: AsyncMock,
        healthy_osdpl: dict,
    ) -> None:
        """Test OSDPLStatus returning None is handled."""
        mock_kubernetes_adapter.get_openstack_deployment.return_value = healthy_osdpl
        mock_kubernetes_adapter.get_openstack_deployment_status.return_value = None

        result = await get_openstack_health(
            mock_kubernetes_adapter,
            GetOpenStackHealthInput(osdpl_name="mos"),
        )

        # Should still return result without osdplst data
        assert result.osdplst_state is None
        assert result.osdplst_health is None

    @pytest.mark.asyncio
    async def test_osdplst_missing_health_field(
        self,
        mock_kubernetes_adapter: AsyncMock,
        healthy_osdpl: dict,
    ) -> None:
        """Test OSDPLStatus with missing health field."""
        osdplst_no_health = {
            "status": {
                "osdpl": {
                    "state": "APPLIED",
                    # health field is missing
                    "release": "17.4.0+25.1",
                },
                "health": {},
                "services": {},
            },
        }
        mock_kubernetes_adapter.get_openstack_deployment.return_value = healthy_osdpl
        mock_kubernetes_adapter.get_openstack_deployment_status.return_value = osdplst_no_health

        result = await get_openstack_health(
            mock_kubernetes_adapter,
            GetOpenStackHealthInput(osdpl_name="mos"),
        )

        assert result.osdplst_state == "APPLIED"
        assert result.osdplst_health is None
        assert result.osdplst_health_ready is None
        assert result.osdplst_health_total is None

    @pytest.mark.asyncio
    async def test_osdplst_missing_state_field(
        self,
        mock_kubernetes_adapter: AsyncMock,
        healthy_osdpl: dict,
    ) -> None:
        """Test OSDPLStatus with missing state field."""
        osdplst_no_state = {
            "status": {
                "osdpl": {
                    # state field is missing
                    "health": "22/22",
                    "release": "17.4.0+25.1",
                },
                "health": {},
                "services": {},
            },
        }
        mock_kubernetes_adapter.get_openstack_deployment.return_value = healthy_osdpl
        mock_kubernetes_adapter.get_openstack_deployment_status.return_value = osdplst_no_state

        result = await get_openstack_health(
            mock_kubernetes_adapter,
            GetOpenStackHealthInput(osdpl_name="mos"),
        )

        assert result.osdplst_state is None
        assert result.osdplst_health == "22/22"

    @pytest.mark.asyncio
    async def test_healthy_message_without_osdplst_health(
        self,
        mock_kubernetes_adapter: AsyncMock,
        mock_mcc_adapter: AsyncMock,
        healthy_osdpl: dict,
    ) -> None:
        """Test healthy message when osdplst_health is None."""
        # Create osdplst with APPLIED state but no health field
        osdplst_no_health = {
            "status": {
                "osdpl": {
                    "state": "APPLIED",
                    # health field is missing - triggers service-based message
                },
                "health": {
                    "keystone": {
                        "api": {"status": "Ready"},
                    },
                },
                "services": {},
            },
        }
        mock_kubernetes_adapter.get_openstack_deployment.return_value = healthy_osdpl
        mock_kubernetes_adapter.get_openstack_deployment_status.return_value = osdplst_no_health
        mock_mcc_adapter.get_mosk_machines_namespace.return_value = "lab"
        mock_mcc_adapter.list_machines.return_value = []

        result = await get_openstack_health(
            mock_kubernetes_adapter,
            GetOpenStackHealthInput(osdpl_name="mos"),
            mock_mcc_adapter,
        )

        # Should use service-based message format
        assert "healthy" in result.message.lower()
        assert "services" in result.message.lower()

    @pytest.mark.asyncio
    async def test_hypervisor_exception_with_namespace_discovery_message(
        self,
        mock_kubernetes_adapter: AsyncMock,
        mock_mcc_adapter: AsyncMock,
        healthy_osdpl: dict,
        healthy_osdplst: dict,
    ) -> None:
        """Test hypervisor exception with 'Namespace discovery' in message is suppressed."""
        mock_kubernetes_adapter.get_openstack_deployment.return_value = healthy_osdpl
        mock_kubernetes_adapter.get_openstack_deployment_status.return_value = healthy_osdplst
        mock_mcc_adapter.get_mosk_machines_namespace.return_value = "lab"
        mock_mcc_adapter.list_machines.side_effect = Exception(
            "Namespace discovery failed: not found"
        )

        result = await get_openstack_health(
            mock_kubernetes_adapter,
            GetOpenStackHealthInput(osdpl_name="mos"),
            mock_mcc_adapter,
        )

        # Should NOT add issue for namespace discovery failure
        assert not any("hypervisor" in issue.lower() for issue in result.issues)

    @pytest.mark.asyncio
    async def test_openstack_version_from_osdplst(
        self,
        mock_kubernetes_adapter: AsyncMock,
        healthy_osdpl: dict,
    ) -> None:
        """Test OpenStack version is taken from osdplst when available."""
        osdplst_with_version = {
            "status": {
                "osdpl": {
                    "state": "APPLIED",
                    "health": "22/22",
                    "openstackVersion": "caracal",  # Different from OSDPL
                },
                "health": {},
                "services": {},
            },
        }
        mock_kubernetes_adapter.get_openstack_deployment.return_value = healthy_osdpl
        mock_kubernetes_adapter.get_openstack_deployment_status.return_value = osdplst_with_version

        result = await get_openstack_health(
            mock_kubernetes_adapter,
            GetOpenStackHealthInput(osdpl_name="mos"),
        )

        # Should use version from osdplst
        assert result.openstack_version == "caracal"

    @pytest.mark.asyncio
    async def test_osdplst_health_ready_zero(
        self,
        mock_kubernetes_adapter: AsyncMock,
        healthy_osdpl: dict,
    ) -> None:
        """Test when osdplst_health_ready is 0 (all unhealthy)."""
        osdplst_all_unhealthy = {
            "status": {
                "osdpl": {
                    "state": "FAILED",
                    "health": "0/22",
                },
                "health": {},
                "services": {},
            },
        }
        mock_kubernetes_adapter.get_openstack_deployment.return_value = healthy_osdpl
        mock_kubernetes_adapter.get_openstack_deployment_status.return_value = osdplst_all_unhealthy

        result = await get_openstack_health(
            mock_kubernetes_adapter,
            GetOpenStackHealthInput(osdpl_name="mos"),
        )

        assert result.osdplst_health_ready == 0
        assert result.osdplst_health_total == 22
        assert result.control_plane_health != HealthStatus.HEALTHY


class TestCalculateControlPlaneScoreEdgeCases:
    """Additional edge case tests for _calculate_control_plane_score."""

    def test_osdplst_health_ready_none_with_total(self) -> None:
        """Test when osdplst_health_ready is None but total exists."""
        score = _calculate_control_plane_score(
            services=[],
            osdpl_phase="Deployed",
            is_upgrading=False,
            osdplst_state="APPLIED",
            osdplst_health_ready=None,
            osdplst_health_total=22,
        )
        # health_ratio should be 0 when ready is None
        # 0 * 70 = 0 + 20 (APPLIED) + 10 (no services) = 30
        assert score == 30

    def test_services_with_no_endpoints_healthy(self) -> None:
        """Test services without endpoint_healthy attribute."""
        services = [
            ServiceHealthInfo(
                name="keystone",
                healthy=True,
                endpoint_healthy=False,
            ),
            ServiceHealthInfo(
                name="nova",
                healthy=True,
                endpoint_healthy=False,
            ),
        ]
        score = _calculate_control_plane_score(
            services=services,
            osdpl_phase="Deployed",
            is_upgrading=False,
            osdplst_state="APPLIED",
            osdplst_health_ready=None,
            osdplst_health_total=None,
        )
        # Services: 2/2 * 70 = 70 + 20 (APPLIED) + 0 (no endpoints healthy) = 90
        assert score == 90
