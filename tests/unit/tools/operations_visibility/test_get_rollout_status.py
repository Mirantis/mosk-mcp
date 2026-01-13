"""Unit tests for get_rollout_status tool."""

from unittest.mock import AsyncMock

import pytest

from mosk_mcp.core.exceptions import ToolExecutionError
from mosk_mcp.tools.operations_visibility.get_rollout_status import (
    _determine_deployment_rollout_status,
    _parse_condition,
    _parse_deployment,
    _parse_statefulset,
    get_rollout_status,
)
from mosk_mcp.tools.operations_visibility.models import (
    ConditionStatus,
    GetRolloutStatusInput,
    RolloutStatus,
)


# =============================================================================
# Tests for helper functions
# =============================================================================


class TestParseCondition:
    """Tests for _parse_condition helper function."""

    def test_parse_complete_condition(self) -> None:
        """Test parsing complete condition."""
        cond_data = {
            "type": "Available",
            "status": "True",
            "reason": "MinimumReplicasAvailable",
            "message": "Deployment has minimum availability",
            "lastTransitionTime": "2024-01-01T00:00:00Z",
            "lastUpdateTime": "2024-01-01T12:00:00Z",
        }

        result = _parse_condition(cond_data)

        assert result.type == "Available"
        assert result.status == ConditionStatus.TRUE
        assert result.reason == "MinimumReplicasAvailable"
        assert result.message == "Deployment has minimum availability"

    def test_parse_unknown_status(self) -> None:
        """Test parsing unknown status."""
        cond_data = {"type": "Test", "status": "InvalidStatus"}

        result = _parse_condition(cond_data)

        assert result.status == ConditionStatus.UNKNOWN

    def test_parse_missing_fields(self) -> None:
        """Test parsing with missing fields."""
        cond_data = {}

        result = _parse_condition(cond_data)

        assert result.type == "Unknown"
        assert result.status == ConditionStatus.UNKNOWN


class TestDetermineDeploymentRolloutStatus:
    """Tests for _determine_deployment_rollout_status helper function."""

    def test_complete_rollout(self) -> None:
        """Test completed rollout."""
        deployment = {
            "spec": {"replicas": 3},
            "status": {
                "replicas": 3,
                "updatedReplicas": 3,
                "availableReplicas": 3,
                "readyReplicas": 3,
            },
        }

        status, progress, is_complete = _determine_deployment_rollout_status(deployment)

        assert status == RolloutStatus.COMPLETE
        assert progress == 100
        assert is_complete is True

    def test_progressing_rollout(self) -> None:
        """Test progressing rollout."""
        deployment = {
            "spec": {"replicas": 3},
            "status": {
                "replicas": 4,
                "updatedReplicas": 2,
                "availableReplicas": 2,
                "readyReplicas": 2,
                "unavailableReplicas": 1,
                "conditions": [
                    {
                        "type": "Progressing",
                        "status": "True",
                        "reason": "ReplicaSetUpdated",
                    },
                ],
            },
        }

        status, progress, is_complete = _determine_deployment_rollout_status(deployment)

        assert status == RolloutStatus.PROGRESSING
        assert progress == 66  # 2/3 * 100
        assert is_complete is False

    def test_failed_rollout(self) -> None:
        """Test failed rollout due to deadline exceeded."""
        deployment = {
            "spec": {"replicas": 3},
            "status": {
                "updatedReplicas": 1,
                "availableReplicas": 1,
                "conditions": [
                    {
                        "type": "Progressing",
                        "status": "False",
                        "reason": "ProgressDeadlineExceeded",
                    },
                ],
            },
        }

        status, _progress, is_complete = _determine_deployment_rollout_status(deployment)

        assert status == RolloutStatus.FAILED
        assert is_complete is False

    def test_paused_rollout(self) -> None:
        """Test paused rollout."""
        deployment = {
            "spec": {"replicas": 3},
            "status": {
                "updatedReplicas": 1,
                "conditions": [
                    {"type": "Progressing", "status": "False", "reason": "Paused"},
                ],
            },
        }

        status, _progress, _is_complete = _determine_deployment_rollout_status(deployment)

        assert status == RolloutStatus.PAUSED

    def test_available_status(self) -> None:
        """Test available but not complete status."""
        deployment = {
            "spec": {"replicas": 3},
            "status": {
                "replicas": 3,
                "updatedReplicas": 2,
                "availableReplicas": 2,
                "readyReplicas": 2,
                "conditions": [
                    {"type": "Available", "status": "True"},
                ],
            },
        }

        status, _progress, is_complete = _determine_deployment_rollout_status(deployment)

        assert status == RolloutStatus.AVAILABLE
        assert is_complete is False

    def test_zero_replicas(self) -> None:
        """Test with zero replicas."""
        deployment = {
            "spec": {"replicas": 0},
            "status": {},
        }

        _status, progress, _is_complete = _determine_deployment_rollout_status(deployment)

        assert progress == 100  # Division by zero protection


class TestParseDeployment:
    """Tests for _parse_deployment helper function."""

    def test_parse_complete_deployment(self) -> None:
        """Test parsing complete deployment."""
        deployment = {
            "metadata": {
                "name": "nova-api",
                "namespace": "openstack",
                "labels": {"application": "nova"},
                "generation": 5,
            },
            "spec": {
                "replicas": 3,
                "strategy": {
                    "type": "RollingUpdate",
                    "rollingUpdate": {"maxSurge": "25%", "maxUnavailable": "0"},
                },
            },
            "status": {
                "replicas": 3,
                "updatedReplicas": 3,
                "availableReplicas": 3,
                "readyReplicas": 3,
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
        }

        result = _parse_deployment(deployment)

        assert result.name == "nova-api"
        assert result.namespace == "openstack"
        assert result.service == "nova"
        assert result.status == RolloutStatus.COMPLETE
        assert result.replicas_desired == 3
        assert result.replicas_current == 3
        assert result.replicas_available == 3
        assert result.strategy == "RollingUpdate"
        assert result.max_surge == "25%"
        assert result.max_unavailable == "0"
        assert result.is_complete is True

    def test_parse_deployment_recreate_strategy(self) -> None:
        """Test parsing deployment with Recreate strategy."""
        deployment = {
            "metadata": {"name": "nova-api", "namespace": "openstack"},
            "spec": {"replicas": 3, "strategy": {"type": "Recreate"}},
            "status": {
                "replicas": 3,
                "updatedReplicas": 3,
                "availableReplicas": 3,
                "readyReplicas": 3,
            },
        }

        result = _parse_deployment(deployment)

        assert result.strategy == "Recreate"
        assert result.max_surge is None
        assert result.max_unavailable is None

    def test_parse_deployment_app_label(self) -> None:
        """Test service extracted from app label."""
        deployment = {
            "metadata": {"name": "keystone-api", "labels": {"app": "keystone"}},
            "spec": {"replicas": 1},
            "status": {
                "replicas": 1,
                "updatedReplicas": 1,
                "availableReplicas": 1,
                "readyReplicas": 1,
            },
        }

        result = _parse_deployment(deployment)

        assert result.service == "keystone"

    def test_parse_deployment_service_from_name(self) -> None:
        """Test service extracted from name."""
        deployment = {
            "metadata": {"name": "neutron-server"},
            "spec": {"replicas": 1},
            "status": {
                "replicas": 1,
                "updatedReplicas": 1,
                "availableReplicas": 1,
                "readyReplicas": 1,
            },
        }

        result = _parse_deployment(deployment)

        assert result.service == "neutron"


class TestParseStatefulSet:
    """Tests for _parse_statefulset helper function."""

    def test_parse_complete_statefulset(self) -> None:
        """Test parsing complete statefulset."""
        sts = {
            "metadata": {
                "name": "mariadb",
                "namespace": "openstack",
                "labels": {"application": "mariadb"},
            },
            "spec": {
                "replicas": 3,
                "updateStrategy": {
                    "type": "RollingUpdate",
                    "rollingUpdate": {"partition": 0},
                },
            },
            "status": {
                "currentReplicas": 3,
                "readyReplicas": 3,
                "updatedReplicas": 3,
                "currentRevision": "mariadb-12345",
                "updateRevision": "mariadb-12345",
            },
        }

        result = _parse_statefulset(sts)

        assert result.name == "mariadb"
        assert result.namespace == "openstack"
        assert result.service == "mariadb"
        assert result.status == RolloutStatus.COMPLETE
        assert result.replicas_desired == 3
        assert result.replicas_ready == 3
        assert result.current_revision == "mariadb-12345"
        assert result.update_revision == "mariadb-12345"
        assert result.update_strategy == "RollingUpdate"
        assert result.partition == 0
        assert result.is_complete is True

    def test_parse_statefulset_progressing(self) -> None:
        """Test parsing progressing statefulset."""
        sts = {
            "metadata": {"name": "rabbitmq"},
            "spec": {"replicas": 3},
            "status": {
                "currentReplicas": 3,
                "readyReplicas": 2,
                "updatedReplicas": 2,
                "currentRevision": "rabbitmq-11111",
                "updateRevision": "rabbitmq-22222",
            },
        }

        result = _parse_statefulset(sts)

        assert result.status == RolloutStatus.PROGRESSING
        assert result.is_complete is False
        assert result.progress_percent == 66  # 2/3 * 100

    def test_parse_statefulset_zero_replicas(self) -> None:
        """Test statefulset with zero replicas."""
        sts = {
            "metadata": {"name": "test-sts"},
            "spec": {"replicas": 0},
            "status": {},
        }

        result = _parse_statefulset(sts)

        assert result.progress_percent == 100


# =============================================================================
# Tests for model validation
# =============================================================================


class TestGetRolloutStatusInput:
    """Tests for GetRolloutStatusInput model."""

    def test_defaults(self) -> None:
        """Test default values."""
        input_data = GetRolloutStatusInput()

        assert input_data.namespace == "openstack"
        assert input_data.service_filter is None
        assert input_data.include_history is False

    def test_custom_values(self) -> None:
        """Test custom values."""
        input_data = GetRolloutStatusInput(
            namespace="kube-system",
            service_filter="nova",
            include_history=True,
        )

        assert input_data.namespace == "kube-system"
        assert input_data.service_filter == "nova"
        assert input_data.include_history is True


# =============================================================================
# Tests for get_rollout_status function
# =============================================================================


class TestGetRolloutStatus:
    """Tests for get_rollout_status function."""

    @pytest.fixture
    def mock_adapter(self) -> AsyncMock:
        """Create mock Kubernetes adapter."""
        adapter = AsyncMock()
        adapter.list = AsyncMock()
        return adapter

    @pytest.mark.asyncio
    async def test_all_rollouts_complete(self, mock_adapter: AsyncMock) -> None:
        """Test when all rollouts are complete."""
        mock_adapter.list.side_effect = [
            [
                {
                    "metadata": {"name": "nova-api", "labels": {"application": "nova"}},
                    "spec": {"replicas": 3},
                    "status": {
                        "replicas": 3,
                        "updatedReplicas": 3,
                        "availableReplicas": 3,
                        "readyReplicas": 3,
                    },
                },
            ],
            [
                {
                    "metadata": {"name": "mariadb", "labels": {"application": "mariadb"}},
                    "spec": {"replicas": 3},
                    "status": {
                        "currentReplicas": 3,
                        "readyReplicas": 3,
                        "updatedReplicas": 3,
                        "currentRevision": "v1",
                        "updateRevision": "v1",
                    },
                },
            ],
        ]

        result = await get_rollout_status(
            mock_adapter,
            GetRolloutStatusInput(),
        )

        assert result.total_workloads == 2
        assert result.workloads_complete == 2
        assert result.workloads_in_progress == 0
        assert result.all_rollouts_complete is True
        assert result.overall_progress_percent == 100

    @pytest.mark.asyncio
    async def test_rollouts_in_progress(self, mock_adapter: AsyncMock) -> None:
        """Test with rollouts in progress."""
        mock_adapter.list.side_effect = [
            [
                {
                    "metadata": {"name": "nova-api"},
                    "spec": {"replicas": 3},
                    "status": {
                        "replicas": 3,
                        "updatedReplicas": 2,
                        "availableReplicas": 2,
                        "readyReplicas": 2,
                        "unavailableReplicas": 1,
                    },
                },
            ],
            [],
        ]

        result = await get_rollout_status(
            mock_adapter,
            GetRolloutStatusInput(),
        )

        assert result.workloads_in_progress == 1
        assert result.all_rollouts_complete is False

    @pytest.mark.asyncio
    async def test_failed_rollout(self, mock_adapter: AsyncMock) -> None:
        """Test with failed rollout."""
        mock_adapter.list.side_effect = [
            [
                {
                    "metadata": {"name": "nova-api"},
                    "spec": {"replicas": 3},
                    "status": {
                        "updatedReplicas": 1,
                        "conditions": [
                            {
                                "type": "Progressing",
                                "status": "False",
                                "reason": "ProgressDeadlineExceeded",
                            },
                        ],
                    },
                },
            ],
            [],
        ]

        result = await get_rollout_status(
            mock_adapter,
            GetRolloutStatusInput(),
        )

        assert result.workloads_failed == 1
        assert "nova-api" in result.stuck_workloads[0]
        assert any("failed" in r.lower() for r in result.recommendations)

    @pytest.mark.asyncio
    async def test_stuck_workloads_detected(self, mock_adapter: AsyncMock) -> None:
        """Test stuck workloads are detected."""
        mock_adapter.list.side_effect = [
            [
                {
                    "metadata": {"name": "slow-deployment"},
                    "spec": {"replicas": 10},
                    "status": {
                        "replicas": 10,
                        "updatedReplicas": 3,  # 30% progress
                        "availableReplicas": 3,
                        "readyReplicas": 3,
                        "unavailableReplicas": 7,
                    },
                },
            ],
            [],
        ]

        result = await get_rollout_status(
            mock_adapter,
            GetRolloutStatusInput(),
        )

        assert len(result.stuck_workloads) > 0
        assert "slow-deployment" in result.stuck_workloads[0]

    @pytest.mark.asyncio
    async def test_service_filter(self, mock_adapter: AsyncMock) -> None:
        """Test service filter."""
        mock_adapter.list.side_effect = [[], []]

        await get_rollout_status(
            mock_adapter,
            GetRolloutStatusInput(service_filter="nova"),
        )

        # Verify label selector was used
        calls = mock_adapter.list.call_args_list
        assert calls[0].kwargs.get("label_selector") == "application=nova"

    @pytest.mark.asyncio
    async def test_custom_namespace(self, mock_adapter: AsyncMock) -> None:
        """Test custom namespace."""
        mock_adapter.list.side_effect = [[], []]

        result = await get_rollout_status(
            mock_adapter,
            GetRolloutStatusInput(namespace="kube-system"),
        )

        calls = mock_adapter.list.call_args_list
        assert calls[0].kwargs.get("namespace") == "kube-system"
        assert result.namespace == "kube-system"

    @pytest.mark.asyncio
    async def test_no_workloads(self, mock_adapter: AsyncMock) -> None:
        """Test with no workloads."""
        mock_adapter.list.side_effect = [[], []]

        result = await get_rollout_status(
            mock_adapter,
            GetRolloutStatusInput(),
        )

        assert result.total_workloads == 0
        assert result.all_rollouts_complete is True
        assert result.overall_progress_percent == 100

    @pytest.mark.asyncio
    async def test_many_concurrent_rollouts_warning(self, mock_adapter: AsyncMock) -> None:
        """Test warning for many concurrent rollouts."""
        deployments = []
        for i in range(6):
            deployments.append(
                {
                    "metadata": {"name": f"deployment-{i}"},
                    "spec": {"replicas": 3},
                    "status": {
                        "updatedReplicas": 2,
                        "availableReplicas": 2,
                        "readyReplicas": 2,
                        "unavailableReplicas": 1,
                    },
                }
            )

        mock_adapter.list.side_effect = [deployments, []]

        result = await get_rollout_status(
            mock_adapter,
            GetRolloutStatusInput(),
        )

        assert result.workloads_in_progress == 6
        assert any("concurrent" in r.lower() for r in result.recommendations)

    @pytest.mark.asyncio
    async def test_api_error_handling(self, mock_adapter: AsyncMock) -> None:
        """Test API error handling."""
        mock_adapter.list.side_effect = Exception("API connection failed")

        with pytest.raises(ToolExecutionError) as exc_info:
            await get_rollout_status(
                mock_adapter,
                GetRolloutStatusInput(),
            )

        assert "Failed to get rollout status" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_timestamp_included(self, mock_adapter: AsyncMock) -> None:
        """Test timestamp is included."""
        mock_adapter.list.side_effect = [[], []]

        result = await get_rollout_status(
            mock_adapter,
            GetRolloutStatusInput(),
        )

        assert result.timestamp is not None
        assert len(result.timestamp) > 0

    @pytest.mark.asyncio
    async def test_overall_progress_calculation(self, mock_adapter: AsyncMock) -> None:
        """Test overall progress is calculated correctly."""
        mock_adapter.list.side_effect = [
            [
                {
                    "metadata": {"name": "deploy-1"},
                    "spec": {"replicas": 2},
                    "status": {
                        "replicas": 2,
                        "updatedReplicas": 2,
                        "availableReplicas": 2,
                        "readyReplicas": 2,
                    },
                },
                {
                    "metadata": {"name": "deploy-2"},
                    "spec": {"replicas": 2},
                    "status": {
                        "replicas": 2,
                        "updatedReplicas": 1,  # 50% progress
                        "availableReplicas": 1,
                        "readyReplicas": 1,
                        "unavailableReplicas": 1,
                    },
                },
            ],
            [],
        ]

        result = await get_rollout_status(
            mock_adapter,
            GetRolloutStatusInput(),
        )

        # (100 + 50) / 2 = 75
        assert result.overall_progress_percent == 75
