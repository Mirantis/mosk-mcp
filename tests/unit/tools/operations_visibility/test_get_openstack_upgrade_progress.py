"""Unit tests for get_openstack_upgrade_progress tool."""

from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from mosk_mcp.core.exceptions import ResourceNotFoundError, ToolExecutionError
from mosk_mcp.tools.operations_visibility.get_openstack_upgrade_progress import (
    _determine_component_state_from_osdplst,
    _estimate_remaining_time,
    _parse_osdplst_service_status,
    get_openstack_upgrade_progress,
)
from mosk_mcp.tools.operations_visibility.models import (
    ComponentUpgradeStatus,
    GetUpgradeProgressInput,
    UpgradeState,
)


class TestDetermineComponentStateFromOsdplst:
    """Tests for _determine_component_state_from_osdplst helper."""

    def test_applied_returns_completed(self):
        """Test APPLIED state returns COMPLETED."""
        result = _determine_component_state_from_osdplst("APPLIED")
        assert result == UpgradeState.COMPLETED

    def test_applying_returns_in_progress(self):
        """Test APPLYING state returns IN_PROGRESS."""
        result = _determine_component_state_from_osdplst("APPLYING")
        assert result == UpgradeState.IN_PROGRESS

    def test_waiting_returns_in_progress(self):
        """Test WAITING state returns IN_PROGRESS."""
        result = _determine_component_state_from_osdplst("WAITING")
        assert result == UpgradeState.IN_PROGRESS

    def test_failed_returns_failed(self):
        """Test FAILED state returns FAILED."""
        result = _determine_component_state_from_osdplst("FAILED")
        assert result == UpgradeState.FAILED

    def test_unknown_returns_not_started(self):
        """Test unknown state returns NOT_STARTED."""
        result = _determine_component_state_from_osdplst("UNKNOWN")
        assert result == UpgradeState.NOT_STARTED

    def test_empty_returns_not_started(self):
        """Test empty state returns NOT_STARTED."""
        result = _determine_component_state_from_osdplst("")
        assert result == UpgradeState.NOT_STARTED


class TestParseOsdplstServiceStatus:
    """Tests for _parse_osdplst_service_status helper."""

    def test_parse_applied_service(self):
        """Test parsing a service in APPLIED state."""
        svc_data = {
            "state": "APPLIED",
            "openstackVersion": "antelope",
            "timestamp": "2024-01-01T12:00:00Z",
        }

        result = _parse_osdplst_service_status(
            name="compute",
            svc_data=svc_data,
            target_version="antelope",
        )

        assert result.name == "compute"
        assert result.current_version == "antelope"
        assert result.target_version == "antelope"
        assert result.state == UpgradeState.COMPLETED
        assert result.progress_percent == 100
        assert result.replicas_updated == 1
        assert result.replicas_total == 1
        assert result.completed_at == "2024-01-01T12:00:00Z"

    def test_parse_applying_service(self):
        """Test parsing a service in APPLYING state."""
        svc_data = {
            "state": "APPLYING",
            "openstackVersion": "yoga",
            "timestamp": "2024-01-01T12:00:00Z",
        }

        result = _parse_osdplst_service_status(
            name="networking",
            svc_data=svc_data,
            target_version="antelope",
        )

        assert result.name == "networking"
        assert result.current_version == "yoga"
        assert result.state == UpgradeState.IN_PROGRESS
        assert result.progress_percent == 50  # Mid-progress for APPLYING
        assert result.replicas_updated == 0
        assert result.completed_at is None

    def test_parse_failed_service(self):
        """Test parsing a service in FAILED state."""
        svc_data = {
            "state": "FAILED",
            "openstackVersion": "yoga",
        }

        result = _parse_osdplst_service_status(
            name="storage",
            svc_data=svc_data,
            target_version="antelope",
        )

        assert result.name == "storage"
        assert result.state == UpgradeState.FAILED
        assert result.progress_percent == 0

    def test_parse_service_missing_fields(self):
        """Test parsing service with missing fields."""
        svc_data = {}

        result = _parse_osdplst_service_status(
            name="identity",
            svc_data=svc_data,
            target_version="antelope",
        )

        assert result.name == "identity"
        assert result.current_version == "unknown"
        assert result.state == UpgradeState.NOT_STARTED
        assert result.progress_percent == 0


class TestEstimateRemainingTime:
    """Tests for _estimate_remaining_time helper."""

    def test_no_started_at_returns_none(self):
        """Test returns None when no started_at timestamp."""
        components = []
        minutes, completion = _estimate_remaining_time(components, None)

        assert minutes is None
        assert completion is None

    def test_all_completed_returns_zero(self):
        """Test returns 0 when all components are completed."""
        components = [
            ComponentUpgradeStatus(
                name="compute",
                current_version="antelope",
                target_version="antelope",
                state=UpgradeState.COMPLETED,
                progress_percent=100,
            ),
            ComponentUpgradeStatus(
                name="networking",
                current_version="antelope",
                target_version="antelope",
                state=UpgradeState.COMPLETED,
                progress_percent=100,
            ),
        ]

        minutes, completion = _estimate_remaining_time(components, "2024-01-01T12:00:00Z")

        assert minutes == 0
        assert completion is not None

    def test_in_progress_components(self):
        """Test estimation with in-progress components."""
        components = [
            ComponentUpgradeStatus(
                name="compute",
                current_version="yoga",
                target_version="antelope",
                state=UpgradeState.IN_PROGRESS,
                progress_percent=50,
            ),
        ]

        minutes, completion = _estimate_remaining_time(components, "2024-01-01T12:00:00Z")

        assert minutes is not None
        assert minutes > 0
        assert completion is not None

    def test_not_started_components(self):
        """Test estimation with not started components."""
        components = [
            ComponentUpgradeStatus(
                name="compute",
                current_version="yoga",
                target_version="antelope",
                state=UpgradeState.NOT_STARTED,
                progress_percent=0,
            ),
            ComponentUpgradeStatus(
                name="networking",
                current_version="yoga",
                target_version="antelope",
                state=UpgradeState.NOT_STARTED,
                progress_percent=0,
            ),
        ]

        minutes, completion = _estimate_remaining_time(components, "2024-01-01T12:00:00Z")

        assert minutes is not None
        # 2 components at 10 min each = 20 min
        assert minutes >= 20
        assert completion is not None

    def test_mixed_components(self):
        """Test estimation with mixed component states."""
        components = [
            ComponentUpgradeStatus(
                name="compute",
                current_version="antelope",
                target_version="antelope",
                state=UpgradeState.COMPLETED,
                progress_percent=100,
            ),
            ComponentUpgradeStatus(
                name="networking",
                current_version="yoga",
                target_version="antelope",
                state=UpgradeState.IN_PROGRESS,
                progress_percent=0,
            ),
            ComponentUpgradeStatus(
                name="storage",
                current_version="yoga",
                target_version="antelope",
                state=UpgradeState.NOT_STARTED,
                progress_percent=0,
            ),
        ]

        minutes, completion = _estimate_remaining_time(components, "2024-01-01T12:00:00Z")

        assert minutes is not None
        assert minutes > 0
        assert completion is not None


class TestGetUpgradeProgressInput:
    """Tests for GetUpgradeProgressInput model."""

    def test_required_name(self):
        """Test name is required."""
        with pytest.raises(Exception):  # Pydantic validation error
            GetUpgradeProgressInput()

    def test_default_values(self):
        """Test default values."""
        input_data = GetUpgradeProgressInput(name="mos")

        assert input_data.name == "mos"
        assert input_data.namespace == "openstack"
        assert input_data.include_component_details is True

    def test_custom_values(self):
        """Test custom values."""
        input_data = GetUpgradeProgressInput(
            name="custom-osdpl",
            namespace="custom-ns",
            include_component_details=False,
        )

        assert input_data.name == "custom-osdpl"
        assert input_data.namespace == "custom-ns"
        assert input_data.include_component_details is False


class TestGetOpenStackUpgradeProgressFunction:
    """Tests for get_openstack_upgrade_progress function."""

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
            },
            "spec": {
                "openStackVersion": "antelope",
            },
            "status": {
                "openStackVersion": "yoga",
                "phase": "Updating",
                "updateStartedAt": "2024-01-01T12:00:00Z",
            },
        }

    @pytest.fixture
    def mock_osdplst(self):
        """Create mock OSDPLStatus resource."""
        return {
            "status": {
                "osdpl": {
                    "state": "APPLYING",
                    "health": "20/23",
                    "lcmProgress": "15/18",
                    "openstackVersion": "yoga",
                },
                "services": {
                    "compute": {
                        "state": "APPLIED",
                        "openstackVersion": "antelope",
                        "timestamp": "2024-01-01T12:30:00Z",
                    },
                    "networking": {
                        "state": "APPLYING",
                        "openstackVersion": "yoga",
                        "timestamp": "2024-01-01T12:35:00Z",
                    },
                    "storage": {
                        "state": "WAITING",
                        "openstackVersion": "yoga",
                    },
                },
            },
        }

    @pytest.mark.asyncio
    async def test_upgrade_in_progress(self, mock_k8s_adapter, mock_osdpl, mock_osdplst):
        """Test upgrade progress during active upgrade."""
        mock_k8s_adapter.get_openstack_deployment = AsyncMock(return_value=mock_osdpl)
        mock_k8s_adapter.get_openstack_deployment_status = AsyncMock(return_value=mock_osdplst)

        result = await get_openstack_upgrade_progress(
            mock_k8s_adapter,
            GetUpgradeProgressInput(name="mos"),
        )

        assert result.name == "mos"
        assert result.is_upgrading is True
        assert result.upgrade_state == UpgradeState.IN_PROGRESS
        assert result.from_version == "yoga"
        assert result.to_version == "antelope"
        assert result.overall_progress_percent > 0
        assert len(result.components) == 3

    @pytest.mark.asyncio
    async def test_upgrade_completed(self, mock_k8s_adapter, mock_osdpl, mock_osdplst):
        """Test upgrade progress when completed."""
        # Modify for completed state
        mock_osdpl["status"]["openStackVersion"] = "antelope"
        mock_osdplst["status"]["osdpl"]["state"] = "APPLIED"
        mock_osdplst["status"]["osdpl"]["health"] = "23/23"
        mock_osdplst["status"]["osdpl"]["lcmProgress"] = "18/18"
        mock_osdplst["status"]["osdpl"]["openstackVersion"] = "antelope"
        for svc in mock_osdplst["status"]["services"].values():
            svc["state"] = "APPLIED"
            svc["openstackVersion"] = "antelope"

        mock_k8s_adapter.get_openstack_deployment = AsyncMock(return_value=mock_osdpl)
        mock_k8s_adapter.get_openstack_deployment_status = AsyncMock(return_value=mock_osdplst)

        result = await get_openstack_upgrade_progress(
            mock_k8s_adapter,
            GetUpgradeProgressInput(name="mos"),
        )

        assert result.is_upgrading is False
        assert result.upgrade_state == UpgradeState.COMPLETED
        assert result.overall_progress_percent == 100
        assert result.control_plane_ready is True
        assert result.compute_nodes_ready is True

    @pytest.mark.asyncio
    async def test_upgrade_failed(self, mock_k8s_adapter, mock_osdpl, mock_osdplst):
        """Test upgrade progress when failed."""
        mock_osdplst["status"]["osdpl"]["state"] = "FAILED"
        mock_osdplst["status"]["services"]["networking"]["state"] = "FAILED"

        mock_k8s_adapter.get_openstack_deployment = AsyncMock(return_value=mock_osdpl)
        mock_k8s_adapter.get_openstack_deployment_status = AsyncMock(return_value=mock_osdplst)

        result = await get_openstack_upgrade_progress(
            mock_k8s_adapter,
            GetUpgradeProgressInput(name="mos"),
        )

        assert result.upgrade_state == UpgradeState.FAILED
        assert len(result.blockers) > 0

    @pytest.mark.asyncio
    async def test_osdpl_not_found(self, mock_k8s_adapter):
        """Test when OSDPL is not found."""
        mock_k8s_adapter.get_openstack_deployment = AsyncMock(
            side_effect=ResourceNotFoundError("OSDPL 'mos' not found")
        )

        with pytest.raises(ResourceNotFoundError):
            await get_openstack_upgrade_progress(
                mock_k8s_adapter,
                GetUpgradeProgressInput(name="mos"),
            )

    @pytest.mark.asyncio
    async def test_osdplst_not_found(self, mock_k8s_adapter, mock_osdpl):
        """Test when OSDPLStatus is not found."""
        mock_k8s_adapter.get_openstack_deployment = AsyncMock(return_value=mock_osdpl)
        mock_k8s_adapter.get_openstack_deployment_status = AsyncMock(return_value=None)

        with pytest.raises(ResourceNotFoundError):
            await get_openstack_upgrade_progress(
                mock_k8s_adapter,
                GetUpgradeProgressInput(name="mos"),
            )

    @pytest.mark.asyncio
    async def test_api_error(self, mock_k8s_adapter):
        """Test API error handling."""
        mock_k8s_adapter.get_openstack_deployment = AsyncMock(
            side_effect=Exception("Connection failed")
        )

        with pytest.raises(ToolExecutionError) as exc_info:
            await get_openstack_upgrade_progress(
                mock_k8s_adapter,
                GetUpgradeProgressInput(name="mos"),
            )

        assert "Failed to get OpenStack upgrade progress" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_without_component_details(self, mock_k8s_adapter, mock_osdpl, mock_osdplst):
        """Test without component details."""
        mock_k8s_adapter.get_openstack_deployment = AsyncMock(return_value=mock_osdpl)
        mock_k8s_adapter.get_openstack_deployment_status = AsyncMock(return_value=mock_osdplst)

        result = await get_openstack_upgrade_progress(
            mock_k8s_adapter,
            GetUpgradeProgressInput(name="mos", include_component_details=False),
        )

        assert result.components == []
        assert result.components_total == 0

    @pytest.mark.asyncio
    async def test_no_upgrade_in_progress(self, mock_k8s_adapter, mock_osdpl, mock_osdplst):
        """Test when no upgrade is in progress."""
        # Set to APPLIED/stable state
        mock_osdpl["spec"]["openStackVersion"] = "yoga"
        mock_osdpl["status"]["openStackVersion"] = "yoga"
        mock_osdplst["status"]["osdpl"]["state"] = "APPLIED"
        mock_osdplst["status"]["osdpl"]["openstackVersion"] = "yoga"
        mock_osdplst["status"]["osdpl"]["health"] = "23/23"
        for svc in mock_osdplst["status"]["services"].values():
            svc["state"] = "APPLIED"

        mock_k8s_adapter.get_openstack_deployment = AsyncMock(return_value=mock_osdpl)
        mock_k8s_adapter.get_openstack_deployment_status = AsyncMock(return_value=mock_osdplst)

        result = await get_openstack_upgrade_progress(
            mock_k8s_adapter,
            GetUpgradeProgressInput(name="mos"),
        )

        assert result.is_upgrading is False
        assert result.upgrade_state == UpgradeState.COMPLETED

    @pytest.mark.asyncio
    async def test_warnings_generated(self, mock_k8s_adapter, mock_osdpl, mock_osdplst):
        """Test warnings are generated for control plane not ready."""
        # Ensure control plane is not ready
        mock_osdplst["status"]["osdpl"]["health"] = "15/23"

        mock_k8s_adapter.get_openstack_deployment = AsyncMock(return_value=mock_osdpl)
        mock_k8s_adapter.get_openstack_deployment_status = AsyncMock(return_value=mock_osdplst)

        result = await get_openstack_upgrade_progress(
            mock_k8s_adapter,
            GetUpgradeProgressInput(name="mos"),
        )

        assert len(result.warnings) > 0
        assert result.control_plane_ready is False

    @pytest.mark.asyncio
    async def test_current_step_shows_in_progress_components(
        self, mock_k8s_adapter, mock_osdpl, mock_osdplst
    ):
        """Test current step shows in-progress components."""
        mock_k8s_adapter.get_openstack_deployment = AsyncMock(return_value=mock_osdpl)
        mock_k8s_adapter.get_openstack_deployment_status = AsyncMock(return_value=mock_osdplst)

        result = await get_openstack_upgrade_progress(
            mock_k8s_adapter,
            GetUpgradeProgressInput(name="mos"),
        )

        assert result.current_step is not None
        assert "networking" in result.current_step or "storage" in result.current_step

    @pytest.mark.asyncio
    async def test_timestamp_set(self, mock_k8s_adapter, mock_osdpl, mock_osdplst):
        """Test timestamp is set in result."""
        mock_k8s_adapter.get_openstack_deployment = AsyncMock(return_value=mock_osdpl)
        mock_k8s_adapter.get_openstack_deployment_status = AsyncMock(return_value=mock_osdplst)

        result = await get_openstack_upgrade_progress(
            mock_k8s_adapter,
            GetUpgradeProgressInput(name="mos"),
        )

        assert result.timestamp is not None
        # Verify valid ISO format
        datetime.fromisoformat(result.timestamp.replace("Z", "+00:00"))

    @pytest.mark.asyncio
    async def test_version_mismatch_warning(self, mock_k8s_adapter, mock_osdpl, mock_osdplst):
        """Test warning generated for version mismatch when not upgrading."""
        # Set different versions but APPLIED state
        mock_osdpl["spec"]["openStackVersion"] = "antelope"
        mock_osdplst["status"]["osdpl"]["state"] = "APPLIED"
        mock_osdplst["status"]["osdpl"]["openstackVersion"] = "yoga"
        mock_osdplst["status"]["osdpl"]["health"] = "23/23"
        for svc in mock_osdplst["status"]["services"].values():
            svc["state"] = "APPLIED"

        mock_k8s_adapter.get_openstack_deployment = AsyncMock(return_value=mock_osdpl)
        mock_k8s_adapter.get_openstack_deployment_status = AsyncMock(return_value=mock_osdplst)

        result = await get_openstack_upgrade_progress(
            mock_k8s_adapter,
            GetUpgradeProgressInput(name="mos"),
        )

        # Should have warning about version mismatch
        assert any("mismatch" in w.lower() for w in result.warnings)

    @pytest.mark.asyncio
    async def test_no_health_info(self, mock_k8s_adapter, mock_osdpl, mock_osdplst):
        """Test when no health info is available."""
        del mock_osdplst["status"]["osdpl"]["health"]

        mock_k8s_adapter.get_openstack_deployment = AsyncMock(return_value=mock_osdpl)
        mock_k8s_adapter.get_openstack_deployment_status = AsyncMock(return_value=mock_osdplst)

        result = await get_openstack_upgrade_progress(
            mock_k8s_adapter,
            GetUpgradeProgressInput(name="mos"),
        )

        # Should still work, just infer readiness from upgrade state
        assert result is not None
        assert result.control_plane_ready is False  # Since APPLYING

    @pytest.mark.asyncio
    async def test_no_lcm_progress(self, mock_k8s_adapter, mock_osdpl, mock_osdplst):
        """Test when no LCM progress is available."""
        del mock_osdplst["status"]["osdpl"]["lcmProgress"]

        mock_k8s_adapter.get_openstack_deployment = AsyncMock(return_value=mock_osdpl)
        mock_k8s_adapter.get_openstack_deployment_status = AsyncMock(return_value=mock_osdplst)

        result = await get_openstack_upgrade_progress(
            mock_k8s_adapter,
            GetUpgradeProgressInput(name="mos"),
        )

        # Should calculate progress from component progress
        assert result.overall_progress_percent >= 0
        assert result.overall_progress_percent <= 100
