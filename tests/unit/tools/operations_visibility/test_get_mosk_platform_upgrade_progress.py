"""Unit tests for get_mosk_platform_upgrade_progress tool."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mosk_mcp.core.exceptions import ResourceNotFoundError, ToolExecutionError
from mosk_mcp.tools.operations_visibility.get_mosk_platform_upgrade_progress import (
    ConditionUpgradeInfo,
    GetMoskPlatformUpgradeProgressInput,
    GetMoskPlatformUpgradeProgressOutput,
    MachineUpgradeInfo,
    UpdatePlanStepInfo,
    get_mosk_platform_upgrade_progress,
)
from mosk_mcp.tools.operations_visibility.monitors.base import ProgressSnapshot


# =============================================================================
# Tests for Models
# =============================================================================


class TestMachineUpgradeInfo:
    """Tests for MachineUpgradeInfo model."""

    def test_creation(self) -> None:
        """Test model creation."""
        info = MachineUpgradeInfo(
            name="compute-01",
            phase="Deploy",
            progress_percent=50,
        )

        assert info.name == "compute-01"
        assert info.phase == "Deploy"
        assert info.progress_percent == 50


class TestConditionUpgradeInfo:
    """Tests for ConditionUpgradeInfo model."""

    def test_creation(self) -> None:
        """Test model creation."""
        info = ConditionUpgradeInfo(
            type="Helm",
            ready=True,
            message="All charts deployed",
        )

        assert info.type == "Helm"
        assert info.ready is True
        assert info.message == "All charts deployed"

    def test_default_message(self) -> None:
        """Test default empty message."""
        info = ConditionUpgradeInfo(
            type="Ceph",
            ready=False,
        )

        assert info.message == ""


class TestUpdatePlanStepInfo:
    """Tests for UpdatePlanStepInfo model."""

    def test_creation(self) -> None:
        """Test model creation."""
        info = UpdatePlanStepInfo(
            id="openstack",
            name="OpenStack services",
            status="InProgress",
            commenced=True,
            message="LCM progress: 5/18",
            duration="15m30s",
            estimated_duration="1h",
            granularity="cluster",
        )

        assert info.id == "openstack"
        assert info.name == "OpenStack services"
        assert info.status == "InProgress"
        assert info.commenced is True
        assert "5/18" in info.message
        assert info.granularity == "cluster"

    def test_defaults(self) -> None:
        """Test default values."""
        info = UpdatePlanStepInfo(
            id="ceph",
            name="Ceph upgrade",
            status="NotStarted",
            commenced=False,
        )

        assert info.message == ""
        assert info.duration == ""
        assert info.estimated_duration == ""
        assert info.granularity == "cluster"


class TestGetMoskPlatformUpgradeProgressInput:
    """Tests for GetMoskPlatformUpgradeProgressInput model."""

    def test_required_fields(self) -> None:
        """Test required fields."""
        input_data = GetMoskPlatformUpgradeProgressInput(cluster_name="mos")

        assert input_data.cluster_name == "mos"

    def test_default_namespace(self) -> None:
        """Test default namespace."""
        input_data = GetMoskPlatformUpgradeProgressInput(cluster_name="mos")

        assert input_data.namespace == "default"

    def test_custom_namespace(self) -> None:
        """Test custom namespace."""
        input_data = GetMoskPlatformUpgradeProgressInput(
            cluster_name="mos",
            namespace="lab",
        )

        assert input_data.namespace == "lab"

    def test_cluster_name_validation(self) -> None:
        """Test cluster name validation."""
        with pytest.raises(ValueError):
            GetMoskPlatformUpgradeProgressInput(cluster_name="")


class TestGetMoskPlatformUpgradeProgressOutput:
    """Tests for GetMoskPlatformUpgradeProgressOutput model."""

    def test_required_fields(self) -> None:
        """Test required fields."""
        output = GetMoskPlatformUpgradeProgressOutput(
            cluster_name="mos",
            namespace="lab",
            phase="completed",
            phase_message="Upgrade complete",
            progress_percent=100,
            is_upgrading=False,
            is_complete=True,
            has_failed=False,
            machines_total=5,
            machines_ready=5,
            timestamp="2024-01-01T00:00:00Z",
        )

        assert output.cluster_name == "mos"
        assert output.progress_percent == 100

    def test_default_lists(self) -> None:
        """Test default list fields."""
        output = GetMoskPlatformUpgradeProgressOutput(
            cluster_name="mos",
            namespace="lab",
            phase="completed",
            phase_message="Upgrade complete",
            progress_percent=100,
            is_upgrading=False,
            is_complete=True,
            has_failed=False,
            machines_total=5,
            machines_ready=5,
            timestamp="2024-01-01T00:00:00Z",
        )

        assert output.machine_phases == {}
        assert output.machines_in_progress == []
        assert output.conditions == []
        assert output.conditions_not_ready == []
        assert output.helm_charts_not_ready == []
        assert output.update_plan_steps == []
        assert output.warnings == []


# =============================================================================
# Tests for get_mosk_platform_upgrade_progress function
# =============================================================================


class TestGetMoskPlatformUpgradeProgress:
    """Tests for get_mosk_platform_upgrade_progress function."""

    @pytest.fixture
    def mock_mcc_adapter(self) -> AsyncMock:
        """Create mock MCC adapter."""
        adapter = AsyncMock()
        adapter.get_cluster = AsyncMock(return_value=None)
        adapter.list_machines = AsyncMock(return_value=[])
        adapter.get_helm_bundle = AsyncMock(return_value=None)
        adapter.find_cluster_update_plan = AsyncMock(return_value=None)
        return adapter

    @pytest.fixture
    def mock_progress_snapshot(self) -> ProgressSnapshot:
        """Create mock progress snapshot."""
        return ProgressSnapshot.create(
            progress_percent=50,
            phase="machines_deploying",
            message="Deploying machine updates",
            details={
                "from_release": "mosk-21-0-0-25-2",
                "to_release": "mosk-21-0-1-25-2",
                "is_upgrading": True,
                "machines_total": 5,
                "machines_ready": 3,
                "machine_phases": {"Ready": 3, "Deploy": 2},
                "machines_in_progress": [
                    {"name": "compute-01", "phase": "Deploy"},
                    {"name": "compute-02", "phase": "Deploy"},
                ],
                "conditions": {"Helm": "ready", "Nodes": "not_ready"},
                "conditions_not_ready": [{"condition": "Nodes", "message": "2/5 nodes ready"}],
                "helm_not_ready": [{"chart": "nova", "ready": False}],
            },
        )

    @pytest.mark.asyncio
    async def test_upgrade_in_progress(
        self,
        mock_mcc_adapter: AsyncMock,
        mock_progress_snapshot: ProgressSnapshot,
    ) -> None:
        """Test upgrade in progress."""
        with patch(
            "mosk_mcp.tools.operations_visibility.get_mosk_platform_upgrade_progress.MoskUpgradeMonitor"
        ) as MockMonitor:
            mock_monitor = MagicMock()
            mock_monitor.get_progress = AsyncMock(return_value=mock_progress_snapshot)
            mock_monitor.is_complete = MagicMock(return_value=False)
            mock_monitor.has_failed = MagicMock(return_value=False)
            mock_monitor.get_error_message = MagicMock(return_value=None)
            MockMonitor.return_value = mock_monitor

            result = await get_mosk_platform_upgrade_progress(
                mock_mcc_adapter,
                GetMoskPlatformUpgradeProgressInput(cluster_name="mos", namespace="lab"),
            )

        assert result.is_upgrading is True
        assert result.is_complete is False
        assert result.machines_total == 5
        assert result.machines_ready == 3
        assert len(result.machines_in_progress) == 2
        assert result.from_release == "mosk-21-0-0-25-2"
        assert result.to_release == "mosk-21-0-1-25-2"

    @pytest.mark.asyncio
    async def test_upgrade_complete(self, mock_mcc_adapter: AsyncMock) -> None:
        """Test completed upgrade."""
        snapshot = ProgressSnapshot.create(
            progress_percent=100,
            phase="completed",
            message="Upgrade completed successfully",
            details={
                "from_release": "mosk-21-0-0-25-2",
                "to_release": "mosk-21-0-1-25-2",
                "is_upgrading": False,
                "machines_total": 5,
                "machines_ready": 5,
            },
        )

        with patch(
            "mosk_mcp.tools.operations_visibility.get_mosk_platform_upgrade_progress.MoskUpgradeMonitor"
        ) as MockMonitor:
            mock_monitor = MagicMock()
            mock_monitor.get_progress = AsyncMock(return_value=snapshot)
            mock_monitor.is_complete = MagicMock(return_value=True)
            mock_monitor.has_failed = MagicMock(return_value=False)
            mock_monitor.get_error_message = MagicMock(return_value=None)
            MockMonitor.return_value = mock_monitor

            result = await get_mosk_platform_upgrade_progress(
                mock_mcc_adapter,
                GetMoskPlatformUpgradeProgressInput(cluster_name="mos", namespace="lab"),
            )

        assert result.is_complete is True
        assert result.is_upgrading is False
        assert result.progress_percent == 100

    @pytest.mark.asyncio
    async def test_upgrade_failed(self, mock_mcc_adapter: AsyncMock) -> None:
        """Test failed upgrade."""
        snapshot = ProgressSnapshot.create(
            progress_percent=-1,
            phase="failed",
            message="Upgrade failed",
            details={
                "from_release": "mosk-21-0-0-25-2",
                "to_release": "mosk-21-0-1-25-2",
                "is_upgrading": False,
                "machines_total": 5,
                "machines_ready": 3,
            },
        )

        with patch(
            "mosk_mcp.tools.operations_visibility.get_mosk_platform_upgrade_progress.MoskUpgradeMonitor"
        ) as MockMonitor:
            mock_monitor = MagicMock()
            mock_monitor.get_progress = AsyncMock(return_value=snapshot)
            mock_monitor.is_complete = MagicMock(return_value=False)
            mock_monitor.has_failed = MagicMock(return_value=True)
            mock_monitor.get_error_message = MagicMock(return_value="Timeout during upgrade")
            MockMonitor.return_value = mock_monitor

            result = await get_mosk_platform_upgrade_progress(
                mock_mcc_adapter,
                GetMoskPlatformUpgradeProgressInput(cluster_name="mos", namespace="lab"),
            )

        assert result.has_failed is True
        assert result.error_message == "Timeout during upgrade"

    @pytest.mark.asyncio
    async def test_with_cluster_update_plan(self, mock_mcc_adapter: AsyncMock) -> None:
        """Test with ClusterUpdatePlan data."""
        snapshot = ProgressSnapshot.create(
            progress_percent=50,
            phase="machines_deploying",
            message="Deploying",
            details={
                "from_release": "mosk-21-0-0-25-2",
                "to_release": "mosk-21-0-1-25-2",
                "is_upgrading": True,
                "machines_total": 5,
                "machines_ready": 3,
            },
        )

        update_plan = {
            "metadata": {"name": "mos-upgrade-21-0-1"},
            "spec": {
                "steps": [
                    {"id": "k8s-controllers", "name": "K8s Controllers", "duration": {}},
                    {"id": "openstack", "name": "OpenStack", "duration": {"estimated": "30m"}},
                    {"id": "ceph", "name": "Ceph", "duration": {}},
                ],
            },
            "status": {
                "status": "InProgress",
                "startedAt": "2024-01-01T10:00:00Z",
                "steps": [
                    {"id": "k8s-controllers", "status": "Completed"},
                    {"id": "openstack", "status": "InProgress", "message": "LCM progress: 5/18"},
                    {"id": "ceph", "status": "NotStarted"},
                ],
            },
        }

        mock_mcc_adapter.find_cluster_update_plan = AsyncMock(return_value=update_plan)

        with patch(
            "mosk_mcp.tools.operations_visibility.get_mosk_platform_upgrade_progress.MoskUpgradeMonitor"
        ) as MockMonitor:
            mock_monitor = MagicMock()
            mock_monitor.get_progress = AsyncMock(return_value=snapshot)
            mock_monitor.is_complete = MagicMock(return_value=False)
            mock_monitor.has_failed = MagicMock(return_value=False)
            mock_monitor.get_error_message = MagicMock(return_value=None)
            MockMonitor.return_value = mock_monitor

            result = await get_mosk_platform_upgrade_progress(
                mock_mcc_adapter,
                GetMoskPlatformUpgradeProgressInput(cluster_name="mos", namespace="lab"),
            )

        assert result.update_plan_name == "mos-upgrade-21-0-1"
        assert result.update_plan_status == "InProgress"
        assert result.steps_total == 3
        assert result.steps_completed == 1
        assert result.current_step == "openstack"
        assert len(result.update_plan_steps) == 3

    @pytest.mark.asyncio
    async def test_progress_calculation_from_steps(self, mock_mcc_adapter: AsyncMock) -> None:
        """Test progress is calculated from update plan steps."""
        snapshot = ProgressSnapshot.create(
            progress_percent=50,
            phase="machines_deploying",
            message="Deploying",
            details={
                "to_release": "mosk-21-0-1-25-2",
                "machines_total": 5,
                "machines_ready": 3,
            },
        )

        # 2/4 steps completed = 50%, + half of in-progress step = 62.5%
        update_plan = {
            "metadata": {"name": "mos-upgrade"},
            "spec": {
                "steps": [
                    {"id": "step1", "name": "Step 1"},
                    {"id": "step2", "name": "Step 2"},
                    {"id": "step3", "name": "Step 3"},
                    {"id": "step4", "name": "Step 4"},
                ],
            },
            "status": {
                "status": "InProgress",
                "steps": [
                    {"id": "step1", "status": "Completed"},
                    {"id": "step2", "status": "Completed"},
                    {"id": "step3", "status": "InProgress"},
                    {"id": "step4", "status": "NotStarted"},
                ],
            },
        }

        mock_mcc_adapter.find_cluster_update_plan = AsyncMock(return_value=update_plan)

        with patch(
            "mosk_mcp.tools.operations_visibility.get_mosk_platform_upgrade_progress.MoskUpgradeMonitor"
        ) as MockMonitor:
            mock_monitor = MagicMock()
            mock_monitor.get_progress = AsyncMock(return_value=snapshot)
            mock_monitor.is_complete = MagicMock(return_value=False)
            mock_monitor.has_failed = MagicMock(return_value=False)
            mock_monitor.get_error_message = MagicMock(return_value=None)
            MockMonitor.return_value = mock_monitor

            result = await get_mosk_platform_upgrade_progress(
                mock_mcc_adapter,
                GetMoskPlatformUpgradeProgressInput(cluster_name="mos", namespace="lab"),
            )

        # 2/4 = 50% + (25/2) = 62%
        assert result.progress_percent == 62

    @pytest.mark.asyncio
    async def test_cluster_not_found(self, mock_mcc_adapter: AsyncMock) -> None:
        """Test when cluster is not found."""
        with patch(
            "mosk_mcp.tools.operations_visibility.get_mosk_platform_upgrade_progress.MoskUpgradeMonitor"
        ) as MockMonitor:
            mock_monitor = AsyncMock()
            mock_monitor.get_progress = AsyncMock(
                side_effect=ResourceNotFoundError(
                    message="Cluster 'mos' not found",
                    resource_type="Cluster",
                    resource_id="lab/mos",
                )
            )
            MockMonitor.return_value = mock_monitor

            with pytest.raises(ResourceNotFoundError):
                await get_mosk_platform_upgrade_progress(
                    mock_mcc_adapter,
                    GetMoskPlatformUpgradeProgressInput(cluster_name="mos", namespace="lab"),
                )

    @pytest.mark.asyncio
    async def test_api_error_handling(self, mock_mcc_adapter: AsyncMock) -> None:
        """Test API error handling."""
        with patch(
            "mosk_mcp.tools.operations_visibility.get_mosk_platform_upgrade_progress.MoskUpgradeMonitor"
        ) as MockMonitor:
            mock_monitor = AsyncMock()
            mock_monitor.get_progress = AsyncMock(side_effect=Exception("API connection failed"))
            MockMonitor.return_value = mock_monitor

            with pytest.raises(ToolExecutionError) as exc_info:
                await get_mosk_platform_upgrade_progress(
                    mock_mcc_adapter,
                    GetMoskPlatformUpgradeProgressInput(cluster_name="mos", namespace="lab"),
                )

            assert "Failed to get MOSK platform upgrade progress" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_update_plan_fetch_failure(self, mock_mcc_adapter: AsyncMock) -> None:
        """Test graceful handling when ClusterUpdatePlan fetch fails."""
        snapshot = ProgressSnapshot.create(
            progress_percent=50,
            phase="machines_deploying",
            message="Deploying",
            details={
                "to_release": "mosk-21-0-1-25-2",
                "machines_total": 5,
                "machines_ready": 3,
            },
        )

        mock_mcc_adapter.find_cluster_update_plan = AsyncMock(
            side_effect=Exception("Failed to fetch update plan")
        )

        with patch(
            "mosk_mcp.tools.operations_visibility.get_mosk_platform_upgrade_progress.MoskUpgradeMonitor"
        ) as MockMonitor:
            mock_monitor = MagicMock()
            mock_monitor.get_progress = AsyncMock(return_value=snapshot)
            mock_monitor.is_complete = MagicMock(return_value=False)
            mock_monitor.has_failed = MagicMock(return_value=False)
            mock_monitor.get_error_message = MagicMock(return_value=None)
            MockMonitor.return_value = mock_monitor

            result = await get_mosk_platform_upgrade_progress(
                mock_mcc_adapter,
                GetMoskPlatformUpgradeProgressInput(cluster_name="mos", namespace="lab"),
            )

        # Should still succeed, but with warning
        assert result is not None
        assert len(result.warnings) > 0
        assert "ClusterUpdatePlan" in result.warnings[0]

    @pytest.mark.asyncio
    async def test_conditions_parsing(
        self, mock_mcc_adapter: AsyncMock, mock_progress_snapshot: ProgressSnapshot
    ) -> None:
        """Test conditions are parsed correctly."""
        with patch(
            "mosk_mcp.tools.operations_visibility.get_mosk_platform_upgrade_progress.MoskUpgradeMonitor"
        ) as MockMonitor:
            mock_monitor = MagicMock()
            mock_monitor.get_progress = AsyncMock(return_value=mock_progress_snapshot)
            mock_monitor.is_complete = MagicMock(return_value=False)
            mock_monitor.has_failed = MagicMock(return_value=False)
            mock_monitor.get_error_message = MagicMock(return_value=None)
            MockMonitor.return_value = mock_monitor

            result = await get_mosk_platform_upgrade_progress(
                mock_mcc_adapter,
                GetMoskPlatformUpgradeProgressInput(cluster_name="mos", namespace="lab"),
            )

        assert len(result.conditions) == 2
        assert "Nodes" in result.conditions_not_ready

    @pytest.mark.asyncio
    async def test_helm_charts_not_ready(
        self, mock_mcc_adapter: AsyncMock, mock_progress_snapshot: ProgressSnapshot
    ) -> None:
        """Test helm charts not ready are extracted."""
        with patch(
            "mosk_mcp.tools.operations_visibility.get_mosk_platform_upgrade_progress.MoskUpgradeMonitor"
        ) as MockMonitor:
            mock_monitor = MagicMock()
            mock_monitor.get_progress = AsyncMock(return_value=mock_progress_snapshot)
            mock_monitor.is_complete = MagicMock(return_value=False)
            mock_monitor.has_failed = MagicMock(return_value=False)
            mock_monitor.get_error_message = MagicMock(return_value=None)
            MockMonitor.return_value = mock_monitor

            result = await get_mosk_platform_upgrade_progress(
                mock_mcc_adapter,
                GetMoskPlatformUpgradeProgressInput(cluster_name="mos", namespace="lab"),
            )

        assert "nova" in result.helm_charts_not_ready

    @pytest.mark.asyncio
    async def test_timestamp_included(self, mock_mcc_adapter: AsyncMock) -> None:
        """Test timestamp is included in output."""
        snapshot = ProgressSnapshot.create(
            progress_percent=0,
            phase="not_started",
            message="Not started",
            details={"machines_total": 0, "machines_ready": 0},
        )

        with patch(
            "mosk_mcp.tools.operations_visibility.get_mosk_platform_upgrade_progress.MoskUpgradeMonitor"
        ) as MockMonitor:
            mock_monitor = MagicMock()
            mock_monitor.get_progress = AsyncMock(return_value=snapshot)
            mock_monitor.is_complete = MagicMock(return_value=False)
            mock_monitor.has_failed = MagicMock(return_value=False)
            mock_monitor.get_error_message = MagicMock(return_value=None)
            MockMonitor.return_value = mock_monitor

            result = await get_mosk_platform_upgrade_progress(
                mock_mcc_adapter,
                GetMoskPlatformUpgradeProgressInput(cluster_name="mos", namespace="lab"),
            )

        assert result.timestamp is not None
        assert len(result.timestamp) > 0
