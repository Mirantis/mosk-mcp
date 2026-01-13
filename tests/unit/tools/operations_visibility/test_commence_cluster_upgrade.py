"""Unit tests for commence_cluster_upgrade tool."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mosk_mcp.core.exceptions import (
    ResourceNotFoundError,
    ToolExecutionError,
    ValidationError,
)
from mosk_mcp.tools.operations_visibility.commence_cluster_upgrade import (
    CommenceClusterUpgradeInput,
    CommenceClusterUpgradeOutput,
    UpgradeStepInfo,
    _extract_steps_info,
    commence_cluster_upgrade,
)


class TestUpgradeStepInfo:
    """Tests for UpgradeStepInfo model."""

    def test_required_fields(self):
        """Test required fields."""
        step = UpgradeStepInfo(
            id="openstack",
            name="OpenStack Upgrade",
            granularity="cluster",
            commenced=False,
            status="NotStarted",
        )

        assert step.id == "openstack"
        assert step.name == "OpenStack Upgrade"
        assert step.granularity == "cluster"
        assert step.commenced is False
        assert step.status == "NotStarted"
        assert step.estimated_duration is None
        assert step.user_impact is None
        assert step.workload_impact is None

    def test_all_fields(self):
        """Test all fields populated."""
        step = UpgradeStepInfo(
            id="ceph",
            name="Ceph Storage Upgrade",
            granularity="machine",
            commenced=True,
            status="InProgress",
            estimated_duration="2h30m0s",
            user_impact="minor",
            workload_impact="major",
        )

        assert step.id == "ceph"
        assert step.estimated_duration == "2h30m0s"
        assert step.user_impact == "minor"
        assert step.workload_impact == "major"


class TestCommenceClusterUpgradeInput:
    """Tests for CommenceClusterUpgradeInput model."""

    def test_required_fields(self):
        """Test required fields."""
        with pytest.raises(Exception):
            CommenceClusterUpgradeInput()

    def test_valid_input(self):
        """Test valid input with all required fields."""
        input_data = CommenceClusterUpgradeInput(
            cluster_name="mos",
            namespace="lab",
            target_release="mosk-21-0-0-25-2",
            crq_number="CRQ123456789",
        )

        assert input_data.cluster_name == "mos"
        assert input_data.namespace == "lab"
        assert input_data.target_release == "mosk-21-0-0-25-2"
        assert input_data.crq_number == "CRQ123456789"
        assert input_data.dry_run is False  # default
        assert input_data.step_ids is None  # default

    def test_dry_run_flag(self):
        """Test dry_run flag."""
        input_data = CommenceClusterUpgradeInput(
            cluster_name="mos",
            namespace="lab",
            target_release="mosk-21-0-0-25-2",
            crq_number="CRQ123456789",
            dry_run=True,
        )

        assert input_data.dry_run is True

    def test_step_ids(self):
        """Test step_ids parameter."""
        input_data = CommenceClusterUpgradeInput(
            cluster_name="mos",
            namespace="lab",
            target_release="mosk-21-0-0-25-2",
            crq_number="CRQ123456789",
            step_ids=["openstack", "ceph"],
        )

        assert input_data.step_ids == ["openstack", "ceph"]

    def test_crq_pattern_validation(self):
        """Test CRQ pattern validation."""
        # Invalid format - wrong prefix
        with pytest.raises(ValueError):
            CommenceClusterUpgradeInput(
                cluster_name="mos",
                namespace="lab",
                target_release="mosk-21-0-0-25-2",
                crq_number="CHG123456789",
            )

        # Invalid format - too short
        with pytest.raises(ValueError):
            CommenceClusterUpgradeInput(
                cluster_name="mos",
                namespace="lab",
                target_release="mosk-21-0-0-25-2",
                crq_number="CRQ12345678",
            )

        # Invalid format - too long
        with pytest.raises(ValueError):
            CommenceClusterUpgradeInput(
                cluster_name="mos",
                namespace="lab",
                target_release="mosk-21-0-0-25-2",
                crq_number="CRQ1234567890",
            )

        # Invalid format - letters instead of digits
        with pytest.raises(ValueError):
            CommenceClusterUpgradeInput(
                cluster_name="mos",
                namespace="lab",
                target_release="mosk-21-0-0-25-2",
                crq_number="CRQABCDEFGHI",
            )


class TestCommenceClusterUpgradeOutput:
    """Tests for CommenceClusterUpgradeOutput model."""

    def test_output_creation(self):
        """Test output model creation."""
        output = CommenceClusterUpgradeOutput(
            success=True,
            cluster_name="mos",
            namespace="lab",
            message="Upgrade commenced",
            crq_number="CRQ123456789",
            dry_run=False,
        )

        assert output.success is True
        assert output.cluster_name == "mos"
        assert output.commenced_at is None
        assert output.update_plan_name is None
        assert output.source_release is None
        assert output.target_release is None
        assert output.steps == []
        assert output.steps_commenced == []
        assert output.warnings == []

    def test_output_with_all_fields(self):
        """Test output with all fields populated."""
        output = CommenceClusterUpgradeOutput(
            success=True,
            cluster_name="mos",
            namespace="lab",
            message="Upgrade commenced",
            commenced_at="2025-01-01T00:00:00Z",
            crq_number="CRQ123456789",
            dry_run=False,
            update_plan_name="mos-upgrade-1",
            source_release="mosk-17-4-0-25-1",
            target_release="mosk-21-0-0-25-2",
            steps=[
                UpgradeStepInfo(
                    id="openstack",
                    name="OpenStack",
                    granularity="cluster",
                    commenced=True,
                    status="InProgress",
                )
            ],
            steps_commenced=["openstack"],
            total_estimated_duration="4h",
            user_impact="minor",
            workload_impact="major",
            skip_maintenance=False,
            reboot_required=True,
            available_upgrade_versions=["mosk-21-0-0-25-2", "mosk-24-0-0-25-3"],
            warnings=["Platform upgrade initiated"],
        )

        assert output.update_plan_name == "mos-upgrade-1"
        assert len(output.steps) == 1
        assert output.steps[0].id == "openstack"
        assert output.user_impact == "minor"
        assert output.reboot_required is True


class TestExtractStepsInfo:
    """Tests for _extract_steps_info helper."""

    def test_extracts_step_info(self):
        """Test extraction of step information."""
        plan = {
            "spec": {
                "steps": [
                    {
                        "id": "openstack",
                        "name": "OpenStack Upgrade",
                        "granularity": "cluster",
                        "commence": False,
                        "duration": {"estimated": "2h"},
                        "impact": {"users": "minor", "workloads": "none"},
                    },
                    {
                        "id": "ceph",
                        "name": "Ceph Storage Upgrade",
                        "granularity": "machine",
                        "commence": True,
                        "duration": {"estimated": "3h"},
                        "impact": {"users": "major", "workloads": "major"},
                    },
                ]
            },
            "status": {
                "steps": [
                    {"id": "openstack", "status": "NotStarted"},
                    {"id": "ceph", "status": "InProgress"},
                ]
            },
        }

        steps, total_duration, max_user, max_workload = _extract_steps_info(plan)

        assert len(steps) == 2

        assert steps[0].id == "openstack"
        assert steps[0].name == "OpenStack Upgrade"
        assert steps[0].granularity == "cluster"
        assert steps[0].commenced is False
        assert steps[0].status == "NotStarted"
        assert steps[0].estimated_duration == "2h"
        assert steps[0].user_impact == "minor"
        assert steps[0].workload_impact == "none"

        assert steps[1].id == "ceph"
        assert steps[1].commenced is True
        assert steps[1].status == "InProgress"
        assert steps[1].user_impact == "major"
        assert steps[1].workload_impact == "major"

        assert max_user == "major"
        assert max_workload == "major"
        assert total_duration is None  # Not calculated

    def test_handles_empty_plan(self):
        """Test handling of empty plan."""
        plan = {"spec": {}, "status": {}}

        steps, total_duration, max_user, max_workload = _extract_steps_info(plan)

        assert steps == []
        assert total_duration is None
        assert max_user == "none"
        assert max_workload == "none"

    def test_handles_missing_status(self):
        """Test handling of steps without status."""
        plan = {
            "spec": {
                "steps": [
                    {
                        "id": "openstack",
                        "name": "OpenStack Upgrade",
                        "granularity": "cluster",
                        "commence": False,
                    }
                ]
            },
            "status": {},  # No steps status
        }

        steps, _, _, _ = _extract_steps_info(plan)

        assert len(steps) == 1
        assert steps[0].status == "NotStarted"  # Default

    def test_handles_missing_impact(self):
        """Test handling of steps without impact info."""
        plan = {
            "spec": {
                "steps": [
                    {
                        "id": "openstack",
                        "name": "OpenStack Upgrade",
                        "granularity": "cluster",
                        "commence": False,
                    }
                ]
            },
            "status": {},
        }

        steps, _, max_user, max_workload = _extract_steps_info(plan)

        assert steps[0].user_impact == "none"
        assert steps[0].workload_impact == "none"
        assert max_user == "none"
        assert max_workload == "none"

    def test_impact_ordering(self):
        """Test that max impact is correctly determined."""
        plan = {
            "spec": {
                "steps": [
                    {
                        "id": "step1",
                        "name": "Step 1",
                        "granularity": "cluster",
                        "commence": False,
                        "impact": {"users": "none", "workloads": "minor"},
                    },
                    {
                        "id": "step2",
                        "name": "Step 2",
                        "granularity": "cluster",
                        "commence": False,
                        "impact": {"users": "minor", "workloads": "none"},
                    },
                    {
                        "id": "step3",
                        "name": "Step 3",
                        "granularity": "cluster",
                        "commence": False,
                        "impact": {"users": "major", "workloads": "major"},
                    },
                ]
            },
            "status": {},
        }

        _, _, max_user, max_workload = _extract_steps_info(plan)

        assert max_user == "major"
        assert max_workload == "major"


class TestCommenceClusterUpgradeFunction:
    """Tests for commence_cluster_upgrade function."""

    @pytest.fixture
    def mock_mcc_adapter(self):
        """Create mock MCC adapter."""
        adapter = AsyncMock()
        return adapter

    @pytest.fixture
    def valid_input(self):
        """Create valid input."""
        return CommenceClusterUpgradeInput(
            cluster_name="mos",
            namespace="lab",
            target_release="mosk-21-0-0-25-2",
            crq_number="CRQ123456789",
        )

    @pytest.fixture
    def mock_crq_validator(self):
        """Create mock CRQ validator."""
        validator = MagicMock()
        result = MagicMock()
        result.is_valid = True
        result.message = "CRQ is valid"
        validator.validate = MagicMock(return_value=result)
        return validator

    @pytest.fixture
    def mock_cluster(self):
        """Create mock cluster object."""
        return {
            "metadata": {"name": "mos", "namespace": "lab"},
            "spec": {
                "providerSpec": {
                    "value": {
                        "release": "mosk-17-4-0-25-1",
                    }
                }
            },
        }

    @pytest.fixture
    def mock_update_plan(self):
        """Create mock ClusterUpdatePlan."""
        return {
            "metadata": {"name": "mos-upgrade-1", "namespace": "lab"},
            "spec": {
                "steps": [
                    {
                        "id": "openstack",
                        "name": "OpenStack Upgrade",
                        "granularity": "cluster",
                        "commence": False,
                        "duration": {"estimated": "2h"},
                        "impact": {"users": "minor", "workloads": "none"},
                    },
                    {
                        "id": "ceph",
                        "name": "Ceph Storage Upgrade",
                        "granularity": "machine",
                        "commence": False,
                        "duration": {"estimated": "3h"},
                        "impact": {"users": "none", "workloads": "minor"},
                    },
                ]
            },
            "status": {
                "status": "NotStarted",
                "steps": [
                    {"id": "openstack", "status": "NotStarted"},
                    {"id": "ceph", "status": "NotStarted"},
                ],
            },
        }

    @pytest.mark.asyncio
    async def test_invalid_crq_rejected(self, mock_mcc_adapter, valid_input):
        """Test that invalid CRQ is rejected."""
        mock_validator = MagicMock()
        mock_result = MagicMock()
        mock_result.is_valid = False
        mock_result.message = "CRQ expired"
        mock_validator.validate = MagicMock(return_value=mock_result)

        with patch(
            "mosk_mcp.tools.operations_visibility.commence_cluster_upgrade.get_crq_validator",
            return_value=mock_validator,
        ):
            with pytest.raises(ValidationError) as exc_info:
                await commence_cluster_upgrade(mock_mcc_adapter, valid_input)

        assert "Invalid CRQ" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_cluster_not_found(self, mock_mcc_adapter, valid_input, mock_crq_validator):
        """Test when Cluster is not found."""
        mock_mcc_adapter.get_cluster = AsyncMock(return_value=None)

        with patch(
            "mosk_mcp.tools.operations_visibility.commence_cluster_upgrade.get_crq_validator",
            return_value=mock_crq_validator,
        ):
            with pytest.raises(ResourceNotFoundError) as exc_info:
                await commence_cluster_upgrade(mock_mcc_adapter, valid_input)

        assert "not found" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_update_plan_not_found(
        self, mock_mcc_adapter, valid_input, mock_crq_validator, mock_cluster
    ):
        """Test when ClusterUpdatePlan is not found."""
        mock_mcc_adapter.get_cluster = AsyncMock(return_value=mock_cluster)
        mock_mcc_adapter.find_cluster_update_plan = AsyncMock(return_value=None)

        with patch(
            "mosk_mcp.tools.operations_visibility.commence_cluster_upgrade.get_crq_validator",
            return_value=mock_crq_validator,
        ):
            with pytest.raises(ResourceNotFoundError) as exc_info:
                await commence_cluster_upgrade(mock_mcc_adapter, valid_input)

        assert "ClusterUpdatePlan" in str(exc_info.value)
        assert "No ClusterUpdatePlan found" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_upgrade_already_completed(
        self, mock_mcc_adapter, valid_input, mock_crq_validator, mock_cluster
    ):
        """Test when upgrade is already completed."""
        completed_plan = {
            "metadata": {"name": "mos-upgrade-1"},
            "spec": {"steps": []},
            "status": {"status": "Completed", "steps": []},
        }

        mock_mcc_adapter.get_cluster = AsyncMock(return_value=mock_cluster)
        mock_mcc_adapter.find_cluster_update_plan = AsyncMock(return_value=completed_plan)

        with patch(
            "mosk_mcp.tools.operations_visibility.commence_cluster_upgrade.get_crq_validator",
            return_value=mock_crq_validator,
        ):
            result = await commence_cluster_upgrade(mock_mcc_adapter, valid_input)

        assert result.success is True
        assert "already completed" in result.message
        assert result.commenced_at is None

    @pytest.mark.asyncio
    async def test_dry_run_success(
        self,
        mock_mcc_adapter,
        mock_crq_validator,
        mock_cluster,
        mock_update_plan,
    ):
        """Test dry-run returns success without commencing."""
        input_data = CommenceClusterUpgradeInput(
            cluster_name="mos",
            namespace="lab",
            target_release="mosk-21-0-0-25-2",
            crq_number="CRQ123456789",
            dry_run=True,
        )

        mock_mcc_adapter.get_cluster = AsyncMock(return_value=mock_cluster)
        mock_mcc_adapter.find_cluster_update_plan = AsyncMock(return_value=mock_update_plan)

        with patch(
            "mosk_mcp.tools.operations_visibility.commence_cluster_upgrade.get_crq_validator",
            return_value=mock_crq_validator,
        ):
            result = await commence_cluster_upgrade(mock_mcc_adapter, input_data)

        assert result.success is True
        assert result.dry_run is True
        assert result.commenced_at is None
        assert "Dry-run successful" in result.message
        assert len(result.steps) == 2
        assert len(result.steps_commenced) == 0  # Not yet commenced

        # Verify patch was NOT called
        mock_mcc_adapter.patch_cluster_update_plan_steps.assert_not_called()

    @pytest.mark.asyncio
    async def test_commence_success(
        self,
        mock_mcc_adapter,
        valid_input,
        mock_crq_validator,
        mock_cluster,
        mock_update_plan,
    ):
        """Test successful upgrade commence."""
        # Create patched plan with steps commenced
        patched_plan = {
            "metadata": {"name": "mos-upgrade-1"},
            "spec": {
                "steps": [
                    {
                        "id": "openstack",
                        "name": "OpenStack Upgrade",
                        "granularity": "cluster",
                        "commence": True,
                        "impact": {"users": "minor", "workloads": "none"},
                    },
                    {
                        "id": "ceph",
                        "name": "Ceph Storage Upgrade",
                        "granularity": "machine",
                        "commence": True,
                        "impact": {"users": "none", "workloads": "minor"},
                    },
                ]
            },
            "status": {
                "status": "InProgress",
                "steps": [
                    {"id": "openstack", "status": "InProgress"},
                    {"id": "ceph", "status": "NotStarted"},
                ],
            },
        }

        mock_mcc_adapter.get_cluster = AsyncMock(return_value=mock_cluster)
        mock_mcc_adapter.find_cluster_update_plan = AsyncMock(return_value=mock_update_plan)
        mock_mcc_adapter.patch_cluster_update_plan_steps = AsyncMock(return_value=patched_plan)

        with patch(
            "mosk_mcp.tools.operations_visibility.commence_cluster_upgrade.get_crq_validator",
            return_value=mock_crq_validator,
        ):
            result = await commence_cluster_upgrade(mock_mcc_adapter, valid_input)

        assert result.success is True
        assert result.dry_run is False
        assert result.commenced_at is not None
        assert result.update_plan_name == "mos-upgrade-1"
        assert result.source_release == "mosk-17-4-0-25-1"
        assert result.target_release == "mosk-21-0-0-25-2"
        assert "Successfully commenced" in result.message

        # Verify adapter was called correctly
        mock_mcc_adapter.patch_cluster_update_plan_steps.assert_called_once()

    @pytest.mark.asyncio
    async def test_selective_step_commence(
        self,
        mock_mcc_adapter,
        mock_crq_validator,
        mock_cluster,
        mock_update_plan,
    ):
        """Test commencing only specific steps."""
        input_data = CommenceClusterUpgradeInput(
            cluster_name="mos",
            namespace="lab",
            target_release="mosk-21-0-0-25-2",
            crq_number="CRQ123456789",
            step_ids=["openstack"],  # Only openstack
        )

        patched_plan = {
            "metadata": {"name": "mos-upgrade-1"},
            "spec": {
                "steps": [
                    {
                        "id": "openstack",
                        "commence": True,
                        "granularity": "cluster",
                        "name": "OpenStack",
                    },
                    {"id": "ceph", "commence": False, "granularity": "machine", "name": "Ceph"},
                ]
            },
            "status": {"steps": [{"id": "openstack", "status": "InProgress"}]},
        }

        mock_mcc_adapter.get_cluster = AsyncMock(return_value=mock_cluster)
        mock_mcc_adapter.find_cluster_update_plan = AsyncMock(return_value=mock_update_plan)
        mock_mcc_adapter.patch_cluster_update_plan_steps = AsyncMock(return_value=patched_plan)

        with patch(
            "mosk_mcp.tools.operations_visibility.commence_cluster_upgrade.get_crq_validator",
            return_value=mock_crq_validator,
        ):
            result = await commence_cluster_upgrade(mock_mcc_adapter, input_data)

        assert result.success is True
        assert "openstack" in result.steps_commenced

        # Verify only openstack was commenced
        mock_mcc_adapter.patch_cluster_update_plan_steps.assert_called_once_with(
            name="mos-upgrade-1",
            namespace="lab",
            step_ids=["openstack"],
            commence=True,
        )

    @pytest.mark.asyncio
    async def test_invalid_step_ids_rejected(
        self,
        mock_mcc_adapter,
        mock_crq_validator,
        mock_cluster,
        mock_update_plan,
    ):
        """Test that invalid step IDs are rejected."""
        input_data = CommenceClusterUpgradeInput(
            cluster_name="mos",
            namespace="lab",
            target_release="mosk-21-0-0-25-2",
            crq_number="CRQ123456789",
            step_ids=["invalid-step"],
        )

        mock_mcc_adapter.get_cluster = AsyncMock(return_value=mock_cluster)
        mock_mcc_adapter.find_cluster_update_plan = AsyncMock(return_value=mock_update_plan)

        with patch(
            "mosk_mcp.tools.operations_visibility.commence_cluster_upgrade.get_crq_validator",
            return_value=mock_crq_validator,
        ):
            with pytest.raises(ValidationError) as exc_info:
                await commence_cluster_upgrade(mock_mcc_adapter, input_data)

        assert "Invalid step IDs" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_already_commenced_steps_skipped(
        self,
        mock_mcc_adapter,
        mock_crq_validator,
        mock_cluster,
    ):
        """Test that already commenced steps are skipped."""
        # Plan with openstack already commenced
        plan_with_commenced = {
            "metadata": {"name": "mos-upgrade-1"},
            "spec": {
                "steps": [
                    {
                        "id": "openstack",
                        "name": "OpenStack",
                        "granularity": "cluster",
                        "commence": True,  # Already commenced
                        "impact": {"users": "minor", "workloads": "none"},
                    },
                    {
                        "id": "ceph",
                        "name": "Ceph",
                        "granularity": "machine",
                        "commence": False,
                        "impact": {"users": "none", "workloads": "minor"},
                    },
                ]
            },
            "status": {
                "status": "InProgress",
                "steps": [
                    {"id": "openstack", "status": "InProgress"},
                    {"id": "ceph", "status": "NotStarted"},
                ],
            },
        }

        input_data = CommenceClusterUpgradeInput(
            cluster_name="mos",
            namespace="lab",
            target_release="mosk-21-0-0-25-2",
            crq_number="CRQ123456789",
            step_ids=["openstack", "ceph"],  # Request both
        )

        patched_plan = {
            "metadata": {"name": "mos-upgrade-1"},
            "spec": {
                "steps": [
                    {
                        "id": "openstack",
                        "commence": True,
                        "granularity": "cluster",
                        "name": "OpenStack",
                    },
                    {"id": "ceph", "commence": True, "granularity": "machine", "name": "Ceph"},
                ]
            },
            "status": {"steps": []},
        }

        mock_mcc_adapter.get_cluster = AsyncMock(return_value=mock_cluster)
        mock_mcc_adapter.find_cluster_update_plan = AsyncMock(return_value=plan_with_commenced)
        mock_mcc_adapter.patch_cluster_update_plan_steps = AsyncMock(return_value=patched_plan)

        with patch(
            "mosk_mcp.tools.operations_visibility.commence_cluster_upgrade.get_crq_validator",
            return_value=mock_crq_validator,
        ):
            result = await commence_cluster_upgrade(mock_mcc_adapter, input_data)

        assert result.success is True
        assert any("already commenced" in w for w in result.warnings)

        # Only ceph should be commenced (openstack already commenced)
        mock_mcc_adapter.patch_cluster_update_plan_steps.assert_called_once_with(
            name="mos-upgrade-1",
            namespace="lab",
            step_ids=["ceph"],
            commence=True,
        )

    @pytest.mark.asyncio
    async def test_all_steps_already_commenced(
        self,
        mock_mcc_adapter,
        mock_crq_validator,
        mock_cluster,
    ):
        """Test when all requested steps are already commenced."""
        # Plan with all steps commenced
        plan_all_commenced = {
            "metadata": {"name": "mos-upgrade-1"},
            "spec": {
                "steps": [
                    {
                        "id": "openstack",
                        "name": "OpenStack",
                        "granularity": "cluster",
                        "commence": True,
                        "impact": {"users": "minor", "workloads": "none"},
                    },
                    {
                        "id": "ceph",
                        "name": "Ceph",
                        "granularity": "machine",
                        "commence": True,
                        "impact": {"users": "none", "workloads": "minor"},
                    },
                ]
            },
            "status": {
                "status": "InProgress",
                "steps": [
                    {"id": "openstack", "status": "InProgress"},
                    {"id": "ceph", "status": "InProgress"},
                ],
            },
        }

        input_data = CommenceClusterUpgradeInput(
            cluster_name="mos",
            namespace="lab",
            target_release="mosk-21-0-0-25-2",
            crq_number="CRQ123456789",
        )

        mock_mcc_adapter.get_cluster = AsyncMock(return_value=mock_cluster)
        mock_mcc_adapter.find_cluster_update_plan = AsyncMock(return_value=plan_all_commenced)

        with patch(
            "mosk_mcp.tools.operations_visibility.commence_cluster_upgrade.get_crq_validator",
            return_value=mock_crq_validator,
        ):
            result = await commence_cluster_upgrade(mock_mcc_adapter, input_data)

        assert result.success is True
        assert "already commenced" in result.message
        assert result.commenced_at is None

        # Patch should not be called
        mock_mcc_adapter.patch_cluster_update_plan_steps.assert_not_called()

    @pytest.mark.asyncio
    async def test_result_includes_warnings(
        self,
        mock_mcc_adapter,
        valid_input,
        mock_crq_validator,
        mock_cluster,
        mock_update_plan,
    ):
        """Test that result includes upgrade warnings."""
        patched_plan = {
            "metadata": {"name": "mos-upgrade-1"},
            "spec": {"steps": []},
            "status": {"steps": []},
        }

        mock_mcc_adapter.get_cluster = AsyncMock(return_value=mock_cluster)
        mock_mcc_adapter.find_cluster_update_plan = AsyncMock(return_value=mock_update_plan)
        mock_mcc_adapter.patch_cluster_update_plan_steps = AsyncMock(return_value=patched_plan)

        with patch(
            "mosk_mcp.tools.operations_visibility.commence_cluster_upgrade.get_crq_validator",
            return_value=mock_crq_validator,
        ):
            result = await commence_cluster_upgrade(mock_mcc_adapter, valid_input)

        assert len(result.warnings) > 0
        assert any("CRITICAL" in w for w in result.warnings)

    @pytest.mark.asyncio
    async def test_adapter_error_handled(
        self,
        mock_mcc_adapter,
        valid_input,
        mock_crq_validator,
        mock_cluster,
        mock_update_plan,
    ):
        """Test that adapter errors are handled."""
        mock_mcc_adapter.get_cluster = AsyncMock(return_value=mock_cluster)
        mock_mcc_adapter.find_cluster_update_plan = AsyncMock(return_value=mock_update_plan)
        mock_mcc_adapter.patch_cluster_update_plan_steps = AsyncMock(
            side_effect=Exception("API server unavailable")
        )

        with patch(
            "mosk_mcp.tools.operations_visibility.commence_cluster_upgrade.get_crq_validator",
            return_value=mock_crq_validator,
        ):
            with pytest.raises(ToolExecutionError) as exc_info:
                await commence_cluster_upgrade(mock_mcc_adapter, valid_input)

        assert "Failed to commence cluster upgrade" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_timestamp_format(
        self,
        mock_mcc_adapter,
        valid_input,
        mock_crq_validator,
        mock_cluster,
        mock_update_plan,
    ):
        """Test that commenced_at timestamp is valid ISO format."""
        patched_plan = {
            "metadata": {"name": "mos-upgrade-1"},
            "spec": {"steps": []},
            "status": {"steps": []},
        }

        mock_mcc_adapter.get_cluster = AsyncMock(return_value=mock_cluster)
        mock_mcc_adapter.find_cluster_update_plan = AsyncMock(return_value=mock_update_plan)
        mock_mcc_adapter.patch_cluster_update_plan_steps = AsyncMock(return_value=patched_plan)

        with patch(
            "mosk_mcp.tools.operations_visibility.commence_cluster_upgrade.get_crq_validator",
            return_value=mock_crq_validator,
        ):
            result = await commence_cluster_upgrade(mock_mcc_adapter, valid_input)

        assert result.commenced_at is not None
        # Verify valid ISO format
        datetime.fromisoformat(result.commenced_at.replace("Z", "+00:00"))

    @pytest.mark.asyncio
    async def test_upgrade_in_progress_adds_warning(
        self,
        mock_mcc_adapter,
        mock_crq_validator,
        mock_cluster,
    ):
        """Test that upgrade in progress adds warning."""
        # Plan already in progress
        plan_in_progress = {
            "metadata": {"name": "mos-upgrade-1"},
            "spec": {
                "steps": [
                    {
                        "id": "openstack",
                        "name": "OpenStack",
                        "granularity": "cluster",
                        "commence": True,
                        "impact": {"users": "minor", "workloads": "none"},
                    },
                    {
                        "id": "ceph",
                        "name": "Ceph",
                        "granularity": "machine",
                        "commence": False,
                        "impact": {"users": "none", "workloads": "minor"},
                    },
                ]
            },
            "status": {
                "status": "InProgress",
                "steps": [
                    {"id": "openstack", "status": "Completed"},
                    {"id": "ceph", "status": "NotStarted"},
                ],
            },
        }

        input_data = CommenceClusterUpgradeInput(
            cluster_name="mos",
            namespace="lab",
            target_release="mosk-21-0-0-25-2",
            crq_number="CRQ123456789",
            step_ids=["ceph"],
        )

        patched_plan = {
            "metadata": {"name": "mos-upgrade-1"},
            "spec": {"steps": []},
            "status": {"steps": []},
        }

        mock_mcc_adapter.get_cluster = AsyncMock(return_value=mock_cluster)
        mock_mcc_adapter.find_cluster_update_plan = AsyncMock(return_value=plan_in_progress)
        mock_mcc_adapter.patch_cluster_update_plan_steps = AsyncMock(return_value=patched_plan)

        with patch(
            "mosk_mcp.tools.operations_visibility.commence_cluster_upgrade.get_crq_validator",
            return_value=mock_crq_validator,
        ):
            result = await commence_cluster_upgrade(mock_mcc_adapter, input_data)

        assert result.success is True
        assert any("already in progress" in w for w in result.warnings)
