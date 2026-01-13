"""Unit tests for get_openstack_deployment_status tool."""

from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from mosk_mcp.core.exceptions import ResourceNotFoundError, ToolExecutionError
from mosk_mcp.tools.common.enums import HealthStatus
from mosk_mcp.tools.operations_visibility.get_openstack_deployment_status import (
    _determine_health,
    _interpret_status,
    _parse_component_health,
    _parse_conditions,
    _parse_health_ratio,
    _parse_lcm_services,
    _parse_services,
    get_openstack_deployment_status,
)
from mosk_mcp.tools.operations_visibility.models import (
    Condition,
    ConditionStatus,
    GetOSDPLStatusInput,
    OSDPLPhase,
    OSDPLState,
)


class TestParseConditions:
    """Tests for _parse_conditions helper function."""

    def test_parse_single_condition(self):
        """Test parsing a single condition."""
        conditions_data = [
            {
                "type": "Ready",
                "status": "True",
                "reason": "AllComponentsReady",
                "message": "All components are ready",
                "lastTransitionTime": "2024-01-01T12:00:00Z",
                "lastUpdateTime": "2024-01-01T12:00:00Z",
            }
        ]

        result = _parse_conditions(conditions_data)

        assert len(result) == 1
        assert result[0].type == "Ready"
        assert result[0].status == ConditionStatus.TRUE
        assert result[0].reason == "AllComponentsReady"
        assert result[0].message == "All components are ready"
        assert result[0].last_transition_time == "2024-01-01T12:00:00Z"

    def test_parse_multiple_conditions(self):
        """Test parsing multiple conditions."""
        conditions_data = [
            {"type": "Ready", "status": "True"},
            {"type": "Updating", "status": "False"},
            {"type": "ControlPlaneReady", "status": "True"},
        ]

        result = _parse_conditions(conditions_data)

        assert len(result) == 3
        assert result[0].type == "Ready"
        assert result[0].status == ConditionStatus.TRUE
        assert result[1].type == "Updating"
        assert result[1].status == ConditionStatus.FALSE
        assert result[2].type == "ControlPlaneReady"
        assert result[2].status == ConditionStatus.TRUE

    def test_parse_unknown_status(self):
        """Test parsing condition with unknown status."""
        conditions_data = [
            {"type": "Custom", "status": "InvalidStatus"},
        ]

        result = _parse_conditions(conditions_data)

        assert len(result) == 1
        assert result[0].type == "Custom"
        assert result[0].status == ConditionStatus.UNKNOWN

    def test_parse_empty_conditions(self):
        """Test parsing empty conditions list."""
        result = _parse_conditions([])

        assert len(result) == 0

    def test_parse_missing_fields(self):
        """Test parsing condition with missing fields."""
        conditions_data = [
            {"type": "Ready"},  # Missing status
        ]

        result = _parse_conditions(conditions_data)

        assert len(result) == 1
        assert result[0].type == "Ready"
        assert result[0].status == ConditionStatus.UNKNOWN
        assert result[0].reason is None
        assert result[0].message is None


class TestParseServices:
    """Tests for _parse_services helper function."""

    def test_parse_single_service(self):
        """Test parsing a single service."""
        services_data = {
            "keystone": {
                "ready": True,
                "replicas": 3,
                "readyReplicas": 3,
                "availableReplicas": 3,
                "message": "All replicas ready",
                "updating": False,
            }
        }

        result = _parse_services(services_data)

        assert len(result) == 1
        assert result[0].name == "keystone"
        assert result[0].ready is True
        assert result[0].replicas_desired == 3
        assert result[0].replicas_ready == 3
        assert result[0].replicas_available == 3
        assert result[0].message == "All replicas ready"
        assert result[0].is_updating is False

    def test_parse_multiple_services(self):
        """Test parsing multiple services."""
        services_data = {
            "nova": {"ready": True, "replicas": 3, "readyReplicas": 3},
            "neutron": {"ready": True, "replicas": 2, "readyReplicas": 2},
            "cinder": {"ready": False, "replicas": 3, "readyReplicas": 1},
        }

        result = _parse_services(services_data)

        assert len(result) == 3
        # Should be sorted by name
        assert result[0].name == "cinder"
        assert result[1].name == "neutron"
        assert result[2].name == "nova"

    def test_parse_updating_service(self):
        """Test parsing service that is updating."""
        services_data = {
            "nova": {"ready": False, "replicas": 3, "readyReplicas": 2, "updating": True},
        }

        result = _parse_services(services_data)

        assert len(result) == 1
        assert result[0].name == "nova"
        assert result[0].is_updating is True

    def test_skip_non_dict_values(self):
        """Test that non-dict values are skipped."""
        services_data = {
            "keystone": {"ready": True},
            "invalid": "not a dict",
            "nova": {"ready": True},
        }

        result = _parse_services(services_data)

        assert len(result) == 2
        names = [s.name for s in result]
        assert "keystone" in names
        assert "nova" in names
        assert "invalid" not in names


class TestParseHealthRatio:
    """Tests for _parse_health_ratio helper function."""

    def test_parse_valid_ratio(self):
        """Test parsing valid health ratio."""
        ready, total = _parse_health_ratio("23/23")

        assert ready == 23
        assert total == 23

    def test_parse_partial_ratio(self):
        """Test parsing partial health ratio."""
        ready, total = _parse_health_ratio("15/20")

        assert ready == 15
        assert total == 20

    def test_parse_invalid_format(self):
        """Test parsing invalid format."""
        ready, total = _parse_health_ratio("invalid")

        assert ready == 0
        assert total == 0

    def test_parse_empty_string(self):
        """Test parsing empty string."""
        ready, total = _parse_health_ratio("")

        assert ready == 0
        assert total == 0

    def test_parse_non_numeric(self):
        """Test parsing non-numeric values."""
        ready, total = _parse_health_ratio("abc/def")

        assert ready == 0
        assert total == 0


class TestParseComponentHealth:
    """Tests for _parse_component_health helper function."""

    def test_parse_healthy_components(self):
        """Test parsing healthy components."""
        health_data = {
            "nova": {
                "api": {"status": "Ready", "generation": 1},
                "scheduler": {"status": "Ready", "generation": 2},
            },
            "neutron": {
                "server": {"status": "Ready", "generation": 1},
            },
        }

        components, unhealthy = _parse_component_health(health_data)

        assert len(components) == 3
        assert len(unhealthy) == 0
        # Should be sorted by service then component
        assert components[0].service == "neutron"
        assert components[0].component == "server"
        assert components[1].service == "nova"
        assert components[1].component == "api"

    def test_parse_unhealthy_components(self):
        """Test parsing with unhealthy components."""
        health_data = {
            "nova": {
                "api": {"status": "Ready", "generation": 1},
                "scheduler": {"status": "Error", "generation": 2},
            },
        }

        components, unhealthy = _parse_component_health(health_data)

        assert len(components) == 2
        assert len(unhealthy) == 1
        assert "nova.scheduler" in unhealthy

    def test_parse_empty_health(self):
        """Test parsing empty health data."""
        components, unhealthy = _parse_component_health({})

        assert len(components) == 0
        assert len(unhealthy) == 0

    def test_skip_non_dict_values(self):
        """Test that non-dict values are skipped."""
        health_data = {
            "nova": {
                "api": {"status": "Ready"},
                "invalid": "not a dict",
            },
        }

        components, _unhealthy = _parse_component_health(health_data)

        assert len(components) == 1
        assert components[0].component == "api"


class TestParseLcmServices:
    """Tests for _parse_lcm_services helper function."""

    def test_parse_applied_services(self):
        """Test parsing services in APPLIED state."""
        services_data = {
            "compute": {
                "state": "APPLIED",
                "openstack_version": "antelope",
                "controller_version": "1.2.3",
                "release": "mosk-21-0",
                "timestamp": "2024-01-01T12:00:00Z",
                "fingerprint": "abc123",
            },
        }

        services, failed = _parse_lcm_services(services_data)

        assert len(services) == 1
        assert len(failed) == 0
        assert services[0].name == "compute"
        assert services[0].state == OSDPLState.APPLIED
        assert services[0].openstack_version == "antelope"

    def test_parse_applying_service(self):
        """Test parsing service in APPLYING state."""
        services_data = {
            "networking": {
                "state": "APPLYING",
                "openstack_version": "antelope",
            },
        }

        services, failed = _parse_lcm_services(services_data)

        assert len(services) == 1
        assert len(failed) == 1
        assert services[0].state == OSDPLState.APPLYING
        assert "networking" in failed

    def test_parse_failed_service(self):
        """Test parsing service in FAILED state."""
        services_data = {
            "storage": {
                "state": "FAILED",
                "openstack_version": "antelope",
            },
        }

        services, failed = _parse_lcm_services(services_data)

        assert len(services) == 1
        assert len(failed) == 1
        assert services[0].state == OSDPLState.FAILED
        assert "storage" in failed

    def test_parse_unknown_state(self):
        """Test parsing service with unknown state."""
        services_data = {
            "custom": {
                "state": "InvalidState",
            },
        }

        services, failed = _parse_lcm_services(services_data)

        assert len(services) == 1
        assert len(failed) == 0  # UNKNOWN is not considered failed
        assert services[0].state == OSDPLState.UNKNOWN


class TestDetermineHealth:
    """Tests for _determine_health helper function."""

    def test_osdplst_failed_state(self):
        """Test health is UNHEALTHY when OSDPLStatus is FAILED."""
        result = _determine_health(
            phase=OSDPLPhase.DEPLOYED,
            conditions=[],
            osdplst_state=OSDPLState.FAILED,
        )

        assert result == HealthStatus.UNHEALTHY

    def test_osdplst_applied_fully_healthy(self):
        """Test health is HEALTHY when all components are healthy."""
        result = _determine_health(
            phase=OSDPLPhase.DEPLOYED,
            conditions=[],
            osdplst_state=OSDPLState.APPLIED,
            health_ready=23,
            health_total=23,
        )

        assert result == HealthStatus.HEALTHY

    def test_osdplst_applied_degraded(self):
        """Test health is DEGRADED when 80%+ components are healthy."""
        result = _determine_health(
            phase=OSDPLPhase.DEPLOYED,
            conditions=[],
            osdplst_state=OSDPLState.APPLIED,
            health_ready=18,
            health_total=20,
        )

        assert result == HealthStatus.DEGRADED

    def test_osdplst_applied_unhealthy(self):
        """Test health is UNHEALTHY when <80% components are healthy."""
        result = _determine_health(
            phase=OSDPLPhase.DEPLOYED,
            conditions=[],
            osdplst_state=OSDPLState.APPLIED,
            health_ready=10,
            health_total=20,
        )

        assert result == HealthStatus.UNHEALTHY

    def test_osdplst_applying_state(self):
        """Test health is DEGRADED when OSDPLStatus is APPLYING."""
        result = _determine_health(
            phase=OSDPLPhase.DEPLOYED,
            conditions=[],
            osdplst_state=OSDPLState.APPLYING,
        )

        assert result == HealthStatus.DEGRADED

    def test_osdplst_waiting_state(self):
        """Test health is DEGRADED when OSDPLStatus is WAITING."""
        result = _determine_health(
            phase=OSDPLPhase.DEPLOYED,
            conditions=[],
            osdplst_state=OSDPLState.WAITING,
        )

        assert result == HealthStatus.DEGRADED

    def test_legacy_failed_phase(self):
        """Test health is UNHEALTHY when phase is FAILED (legacy)."""
        result = _determine_health(
            phase=OSDPLPhase.FAILED,
            conditions=[],
            osdplst_state=None,
        )

        assert result == HealthStatus.UNHEALTHY

    def test_legacy_deployed_ready(self):
        """Test health is HEALTHY when deployed and Ready condition is True."""
        conditions = [
            Condition(
                type="Ready",
                status=ConditionStatus.TRUE,
            )
        ]

        result = _determine_health(
            phase=OSDPLPhase.DEPLOYED,
            conditions=conditions,
            osdplst_state=None,
        )

        assert result == HealthStatus.HEALTHY

    def test_legacy_deployed_not_ready(self):
        """Test health is DEGRADED when deployed but Ready condition is not True."""
        conditions = [
            Condition(
                type="Ready",
                status=ConditionStatus.FALSE,
            )
        ]

        result = _determine_health(
            phase=OSDPLPhase.DEPLOYED,
            conditions=conditions,
            osdplst_state=None,
        )

        assert result == HealthStatus.DEGRADED

    def test_legacy_updating_phase(self):
        """Test health is DEGRADED when updating."""
        result = _determine_health(
            phase=OSDPLPhase.UPDATING,
            conditions=[],
            osdplst_state=None,
        )

        assert result == HealthStatus.DEGRADED

    def test_legacy_unknown_phase(self):
        """Test health is UNKNOWN for unknown phase."""
        result = _determine_health(
            phase=OSDPLPhase.UNKNOWN,
            conditions=[],
            osdplst_state=None,
        )

        assert result == HealthStatus.UNKNOWN


class TestInterpretStatus:
    """Tests for _interpret_status helper function."""

    def test_osdplst_applied_healthy(self):
        """Test interpretation for APPLIED state with no issues."""
        result = _interpret_status(
            phase=OSDPLPhase.DEPLOYED,
            conditions=[],
            is_updating=False,
            osdplst_state=OSDPLState.APPLIED,
            unhealthy_components=[],
            failed_services=[],
        )

        assert "healthy" in result.interpretation.lower()
        assert result.action_required is False

    def test_osdplst_applied_unhealthy_components(self):
        """Test interpretation for APPLIED state with unhealthy components."""
        result = _interpret_status(
            phase=OSDPLPhase.DEPLOYED,
            conditions=[],
            is_updating=False,
            osdplst_state=OSDPLState.APPLIED,
            unhealthy_components=["nova.api", "neutron.server"],
            failed_services=[],
        )

        assert "unhealthy" in result.interpretation.lower()
        assert result.action_required is True
        assert len(result.recommendations) > 0

    def test_osdplst_applying(self):
        """Test interpretation for APPLYING state."""
        result = _interpret_status(
            phase=OSDPLPhase.DEPLOYED,
            conditions=[],
            is_updating=True,
            osdplst_state=OSDPLState.APPLYING,
            unhealthy_components=[],
            failed_services=["compute"],
        )

        # Message is "Configuration changes being applied"
        assert "applied" in result.interpretation.lower()
        assert result.action_required is False
        assert result.typical_duration is not None

    def test_osdplst_waiting(self):
        """Test interpretation for WAITING state."""
        result = _interpret_status(
            phase=OSDPLPhase.DEPLOYED,
            conditions=[],
            is_updating=False,
            osdplst_state=OSDPLState.WAITING,
            unhealthy_components=[],
            failed_services=[],
        )

        assert "waiting" in result.interpretation.lower()
        assert result.action_required is False

    def test_osdplst_failed(self):
        """Test interpretation for FAILED state."""
        result = _interpret_status(
            phase=OSDPLPhase.DEPLOYED,
            conditions=[],
            is_updating=False,
            osdplst_state=OSDPLState.FAILED,
            unhealthy_components=[],
            failed_services=["storage"],
        )

        assert "failed" in result.interpretation.lower()
        assert result.action_required is True
        assert len(result.recommendations) > 0

    def test_legacy_updating(self):
        """Test interpretation for updating (legacy)."""
        result = _interpret_status(
            phase=OSDPLPhase.UPDATING,
            conditions=[],
            is_updating=True,
            osdplst_state=None,
        )

        assert (
            "upgrade" in result.interpretation.lower()
            or "updating" in result.interpretation.lower()
        )
        assert result.action_required is False

    def test_legacy_not_ready(self):
        """Test interpretation for not ready (legacy)."""
        conditions = [
            Condition(type="Ready", status=ConditionStatus.FALSE),
        ]

        result = _interpret_status(
            phase=OSDPLPhase.DEPLOYED,
            conditions=conditions,
            is_updating=False,
            osdplst_state=None,
        )

        assert "not ready" in result.interpretation.lower()
        assert result.action_required is True

    def test_legacy_control_plane_not_ready(self):
        """Test interpretation for control plane not ready (legacy)."""
        conditions = [
            Condition(type="ControlPlaneReady", status=ConditionStatus.FALSE),
        ]

        result = _interpret_status(
            phase=OSDPLPhase.DEPLOYED,
            conditions=conditions,
            is_updating=False,
            osdplst_state=None,
        )

        assert "control plane" in result.interpretation.lower()

    def test_legacy_compute_nodes_not_ready(self):
        """Test interpretation for compute nodes not ready (legacy)."""
        conditions = [
            Condition(type="ComputeNodesReady", status=ConditionStatus.FALSE),
        ]

        result = _interpret_status(
            phase=OSDPLPhase.DEPLOYED,
            conditions=conditions,
            is_updating=False,
            osdplst_state=None,
        )

        assert "compute" in result.interpretation.lower()

    def test_legacy_fully_ready(self):
        """Test interpretation for fully ready (legacy)."""
        conditions = [
            Condition(type="Ready", status=ConditionStatus.TRUE),
        ]

        result = _interpret_status(
            phase=OSDPLPhase.DEPLOYED,
            conditions=conditions,
            is_updating=False,
            osdplst_state=None,
        )

        assert "healthy" in result.interpretation.lower()
        assert result.action_required is False

    def test_legacy_failed_phase(self):
        """Test interpretation for failed phase (legacy)."""
        result = _interpret_status(
            phase=OSDPLPhase.FAILED,
            conditions=[],
            is_updating=False,
            osdplst_state=None,
        )

        assert "failed" in result.interpretation.lower()
        assert result.action_required is True


class TestGetOSDPLStatusInput:
    """Tests for GetOSDPLStatusInput model."""

    def test_required_name(self):
        """Test name is required."""
        with pytest.raises(Exception):  # Pydantic validation error
            GetOSDPLStatusInput()

    def test_default_values(self):
        """Test default values when name is provided."""
        input_data = GetOSDPLStatusInput(name="mos")

        assert input_data.name == "mos"
        assert input_data.namespace == "openstack"
        assert input_data.include_conditions is True
        assert input_data.include_services is True

    def test_custom_values(self):
        """Test custom values."""
        input_data = GetOSDPLStatusInput(
            name="mos",
            namespace="custom-namespace",
            include_conditions=False,
            include_services=False,
        )

        assert input_data.name == "mos"
        assert input_data.namespace == "custom-namespace"
        assert input_data.include_conditions is False
        assert input_data.include_services is False


class TestGetOpenStackDeploymentStatusFunction:
    """Tests for get_openstack_deployment_status function."""

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
            "metadata": {
                "name": "mos",
                "namespace": "openstack",
                "creationTimestamp": "2024-01-01T00:00:00Z",
            },
            "spec": {
                "openstack_version": "antelope",
            },
            "status": {
                "phase": "Deployed",
                "openStackVersion": "antelope",
                "lastUpdateTime": "2024-01-01T12:00:00Z",
                "observedGeneration": 5,
                "conditions": [
                    {"type": "Ready", "status": "True"},
                ],
                "services": {
                    "keystone": {"ready": True, "replicas": 3, "readyReplicas": 3},
                },
                "endpoints": {"keystone": "https://keystone.example.com"},
            },
        }

    @pytest.fixture
    def mock_osdplst(self):
        """Create mock OSDPLStatus resource."""
        return {
            "status": {
                "osdpl": {
                    "state": "APPLIED",
                    "health": "23/23",
                    "lcm_progress": "100%",
                    "release": "mosk-21-0",
                    "openstack_version": "antelope",
                    "timestamp": "2024-01-01T12:00:00Z",
                },
                "health": {
                    "nova": {
                        "api": {"status": "Ready", "generation": 1},
                        "scheduler": {"status": "Ready", "generation": 2},
                    },
                },
                "services": {
                    "compute": {
                        "state": "APPLIED",
                        "openstack_version": "antelope",
                    },
                },
            },
        }

    @pytest.mark.asyncio
    async def test_get_status_success_healthy(self, mock_k8s_adapter, mock_osdpl, mock_osdplst):
        """Test successful status retrieval for healthy cluster."""
        mock_k8s_adapter.get_openstack_deployment = AsyncMock(return_value=mock_osdpl)
        mock_k8s_adapter.get_openstack_deployment_status = AsyncMock(return_value=mock_osdplst)

        result = await get_openstack_deployment_status(
            mock_k8s_adapter,
            GetOSDPLStatusInput(name="mos"),
        )

        assert result.name == "mos"
        assert result.phase == OSDPLPhase.DEPLOYED
        assert result.health == HealthStatus.HEALTHY
        assert result.osdplst_state == OSDPLState.APPLIED
        assert result.osdplst_health == "23/23"
        assert result.osdplst_health_ready == 23
        assert result.osdplst_health_total == 23
        assert result.is_ready is True
        assert result.is_updating is False

    @pytest.mark.asyncio
    async def test_get_status_updating(self, mock_k8s_adapter, mock_osdpl, mock_osdplst):
        """Test status retrieval during update."""
        # Modify mock data for updating state
        mock_osdpl["status"]["phase"] = "Updating"
        mock_osdplst["status"]["osdpl"]["state"] = "APPLYING"
        mock_osdplst["status"]["osdpl"]["health"] = "20/23"

        mock_k8s_adapter.get_openstack_deployment = AsyncMock(return_value=mock_osdpl)
        mock_k8s_adapter.get_openstack_deployment_status = AsyncMock(return_value=mock_osdplst)

        result = await get_openstack_deployment_status(
            mock_k8s_adapter,
            GetOSDPLStatusInput(name="mos"),
        )

        assert result.osdplst_state == OSDPLState.APPLYING
        assert result.is_updating is True
        assert result.health == HealthStatus.DEGRADED

    @pytest.mark.asyncio
    async def test_get_status_with_unhealthy_components(
        self, mock_k8s_adapter, mock_osdpl, mock_osdplst
    ):
        """Test status retrieval with unhealthy components."""
        # Add unhealthy component
        mock_osdplst["status"]["health"]["nova"]["scheduler"]["status"] = "Error"
        mock_osdplst["status"]["osdpl"]["health"] = "22/23"

        mock_k8s_adapter.get_openstack_deployment = AsyncMock(return_value=mock_osdpl)
        mock_k8s_adapter.get_openstack_deployment_status = AsyncMock(return_value=mock_osdplst)

        result = await get_openstack_deployment_status(
            mock_k8s_adapter,
            GetOSDPLStatusInput(name="mos"),
        )

        assert len(result.unhealthy_components) == 1
        assert "nova.scheduler" in result.unhealthy_components

    @pytest.mark.asyncio
    async def test_get_status_osdpl_not_found(self, mock_k8s_adapter):
        """Test status retrieval when OSDPL not found."""
        mock_k8s_adapter.get_openstack_deployment = AsyncMock(
            side_effect=ResourceNotFoundError("OSDPL 'mos' not found")
        )

        with pytest.raises(ResourceNotFoundError):
            await get_openstack_deployment_status(
                mock_k8s_adapter,
                GetOSDPLStatusInput(name="mos"),
            )

    @pytest.mark.asyncio
    async def test_get_status_osdplst_not_found(self, mock_k8s_adapter, mock_osdpl):
        """Test status retrieval when OSDPLStatus not found."""
        mock_k8s_adapter.get_openstack_deployment = AsyncMock(return_value=mock_osdpl)
        mock_k8s_adapter.get_openstack_deployment_status = AsyncMock(
            side_effect=ResourceNotFoundError("OSDPLStatus 'mos' not found")
        )

        with pytest.raises(ResourceNotFoundError) as exc_info:
            await get_openstack_deployment_status(
                mock_k8s_adapter,
                GetOSDPLStatusInput(name="mos"),
            )

        assert "OSDPLStatus" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_get_status_osdplst_fetch_error(self, mock_k8s_adapter, mock_osdpl):
        """Test status retrieval when OSDPLStatus fetch fails."""
        mock_k8s_adapter.get_openstack_deployment = AsyncMock(return_value=mock_osdpl)
        mock_k8s_adapter.get_openstack_deployment_status = AsyncMock(
            side_effect=Exception("Connection error")
        )

        with pytest.raises(Exception) as exc_info:
            await get_openstack_deployment_status(
                mock_k8s_adapter,
                GetOSDPLStatusInput(name="mos"),
            )

        assert "Connection error" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_get_status_api_error(self, mock_k8s_adapter):
        """Test status retrieval with API error."""
        mock_k8s_adapter.get_openstack_deployment = AsyncMock(
            side_effect=Exception("API connection failed")
        )

        with pytest.raises(ToolExecutionError) as exc_info:
            await get_openstack_deployment_status(
                mock_k8s_adapter,
                GetOSDPLStatusInput(name="mos"),
            )

        assert "Failed to get OpenStack deployment status" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_get_status_without_conditions(self, mock_k8s_adapter, mock_osdpl, mock_osdplst):
        """Test status retrieval without conditions."""
        mock_k8s_adapter.get_openstack_deployment = AsyncMock(return_value=mock_osdpl)
        mock_k8s_adapter.get_openstack_deployment_status = AsyncMock(return_value=mock_osdplst)

        result = await get_openstack_deployment_status(
            mock_k8s_adapter,
            GetOSDPLStatusInput(name="mos", include_conditions=False),
        )

        assert result.conditions == []

    @pytest.mark.asyncio
    async def test_get_status_without_services(self, mock_k8s_adapter, mock_osdpl, mock_osdplst):
        """Test status retrieval without services."""
        mock_k8s_adapter.get_openstack_deployment = AsyncMock(return_value=mock_osdpl)
        mock_k8s_adapter.get_openstack_deployment_status = AsyncMock(return_value=mock_osdplst)

        result = await get_openstack_deployment_status(
            mock_k8s_adapter,
            GetOSDPLStatusInput(name="mos", include_services=False),
        )

        assert result.services == []
        # Component health should also be empty when include_services is False
        assert result.component_health == []

    @pytest.mark.asyncio
    async def test_get_status_failed_state(self, mock_k8s_adapter, mock_osdpl, mock_osdplst):
        """Test status retrieval for failed deployment."""
        mock_osdpl["status"]["phase"] = "Failed"
        mock_osdplst["status"]["osdpl"]["state"] = "FAILED"

        mock_k8s_adapter.get_openstack_deployment = AsyncMock(return_value=mock_osdpl)
        mock_k8s_adapter.get_openstack_deployment_status = AsyncMock(return_value=mock_osdplst)

        result = await get_openstack_deployment_status(
            mock_k8s_adapter,
            GetOSDPLStatusInput(name="mos"),
        )

        assert result.osdplst_state == OSDPLState.FAILED
        assert result.health == HealthStatus.UNHEALTHY
        assert result.summary.action_required is True

    @pytest.mark.asyncio
    async def test_get_status_unknown_phase(self, mock_k8s_adapter, mock_osdpl, mock_osdplst):
        """Test status retrieval with unknown phase."""
        mock_osdpl["status"]["phase"] = "InvalidPhase"

        mock_k8s_adapter.get_openstack_deployment = AsyncMock(return_value=mock_osdpl)
        mock_k8s_adapter.get_openstack_deployment_status = AsyncMock(return_value=mock_osdplst)

        result = await get_openstack_deployment_status(
            mock_k8s_adapter,
            GetOSDPLStatusInput(name="mos"),
        )

        assert result.phase == OSDPLPhase.UNKNOWN

    @pytest.mark.asyncio
    async def test_get_status_timestamp_set(self, mock_k8s_adapter, mock_osdpl, mock_osdplst):
        """Test that timestamp is set in result."""
        mock_k8s_adapter.get_openstack_deployment = AsyncMock(return_value=mock_osdpl)
        mock_k8s_adapter.get_openstack_deployment_status = AsyncMock(return_value=mock_osdplst)

        result = await get_openstack_deployment_status(
            mock_k8s_adapter,
            GetOSDPLStatusInput(name="mos"),
        )

        assert result.timestamp is not None
        # Verify it's a valid ISO format
        datetime.fromisoformat(result.timestamp.replace("Z", "+00:00"))

    @pytest.mark.asyncio
    async def test_get_status_lcm_services_parsed(self, mock_k8s_adapter, mock_osdpl, mock_osdplst):
        """Test that LCM services are parsed correctly."""
        mock_k8s_adapter.get_openstack_deployment = AsyncMock(return_value=mock_osdpl)
        mock_k8s_adapter.get_openstack_deployment_status = AsyncMock(return_value=mock_osdplst)

        result = await get_openstack_deployment_status(
            mock_k8s_adapter,
            GetOSDPLStatusInput(name="mos"),
        )

        assert len(result.lcm_services) == 1
        assert result.lcm_services[0].name == "compute"
        assert result.lcm_services[0].state == OSDPLState.APPLIED
