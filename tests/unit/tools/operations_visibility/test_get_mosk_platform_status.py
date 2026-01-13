"""Unit tests for get_mosk_platform_status tool."""

from typing import Any
from unittest.mock import AsyncMock

import pytest

from mosk_mcp.core.exceptions import ResourceNotFoundError, ToolExecutionError
from mosk_mcp.tools.operations_visibility.get_mosk_platform_status import (
    ClusterConditionInfo,
    GetMoskPlatformStatusInput,
    GetMoskPlatformStatusOutput,
    MachinePhaseInfo,
    MoskPlatformPhase,
    get_mosk_platform_status,
)


# =============================================================================
# Tests for Enums and Models
# =============================================================================


class TestMoskPlatformPhase:
    """Tests for MoskPlatformPhase enum."""

    def test_all_phases_defined(self) -> None:
        """Test all phases are defined."""
        assert MoskPlatformPhase.READY == "ready"
        assert MoskPlatformPhase.UPGRADING == "upgrading"
        assert MoskPlatformPhase.PROVISIONING == "provisioning"
        assert MoskPlatformPhase.DEGRADED == "degraded"
        assert MoskPlatformPhase.ERROR == "error"
        assert MoskPlatformPhase.UNKNOWN == "unknown"

    def test_phases_are_string_enum(self) -> None:
        """Test phases can be used as strings."""
        phase = MoskPlatformPhase.READY
        assert phase.value == "ready"
        # String enum value accessible via .value
        assert phase == "ready"


class TestMachinePhaseInfo:
    """Tests for MachinePhaseInfo model."""

    def test_creation(self) -> None:
        """Test model creation."""
        info = MachinePhaseInfo(
            name="compute-01",
            phase="Ready",
            is_ready=True,
        )

        assert info.name == "compute-01"
        assert info.phase == "Ready"
        assert info.is_ready is True

    def test_not_ready_machine(self) -> None:
        """Test model for not ready machine."""
        info = MachinePhaseInfo(
            name="compute-02",
            phase="Deploy",
            is_ready=False,
        )

        assert info.name == "compute-02"
        assert info.phase == "Deploy"
        assert info.is_ready is False


class TestClusterConditionInfo:
    """Tests for ClusterConditionInfo model."""

    def test_creation(self) -> None:
        """Test model creation."""
        info = ClusterConditionInfo(
            type="Nodes",
            ready=True,
            message="All nodes ready",
        )

        assert info.type == "Nodes"
        assert info.ready is True
        assert info.message == "All nodes ready"

    def test_default_message(self) -> None:
        """Test default empty message."""
        info = ClusterConditionInfo(
            type="Helm",
            ready=True,
        )

        assert info.message == ""


class TestGetMoskPlatformStatusInput:
    """Tests for GetMoskPlatformStatusInput model."""

    def test_required_fields(self) -> None:
        """Test required fields."""
        input_data = GetMoskPlatformStatusInput(cluster_name="mos")

        assert input_data.cluster_name == "mos"

    def test_default_values(self) -> None:
        """Test default values."""
        input_data = GetMoskPlatformStatusInput(cluster_name="mos")

        assert input_data.namespace == "default"
        assert input_data.include_machine_details is True

    def test_custom_values(self) -> None:
        """Test custom values."""
        input_data = GetMoskPlatformStatusInput(
            cluster_name="mos",
            namespace="lab",
            include_machine_details=False,
        )

        assert input_data.cluster_name == "mos"
        assert input_data.namespace == "lab"
        assert input_data.include_machine_details is False

    def test_cluster_name_validation(self) -> None:
        """Test cluster name validation."""
        # Empty string should fail
        with pytest.raises(ValueError):
            GetMoskPlatformStatusInput(cluster_name="")


class TestGetMoskPlatformStatusOutput:
    """Tests for GetMoskPlatformStatusOutput model."""

    def test_required_fields(self) -> None:
        """Test required fields."""
        output = GetMoskPlatformStatusOutput(
            cluster_name="mos",
            namespace="lab",
            phase=MoskPlatformPhase.READY,
            current_release="mosk-21-0-0-25-2",
            target_release="mosk-21-0-0-25-2",
            is_upgrading=False,
            machines_total=5,
            machines_ready=5,
            all_conditions_ready=True,
            health_summary="All healthy",
            timestamp="2024-01-01T00:00:00Z",
        )

        assert output.cluster_name == "mos"
        assert output.namespace == "lab"
        assert output.phase == MoskPlatformPhase.READY

    def test_default_list_fields(self) -> None:
        """Test default list fields."""
        output = GetMoskPlatformStatusOutput(
            cluster_name="mos",
            namespace="lab",
            phase=MoskPlatformPhase.READY,
            current_release="mosk-21-0-0-25-2",
            target_release="mosk-21-0-0-25-2",
            is_upgrading=False,
            machines_total=5,
            machines_ready=5,
            all_conditions_ready=True,
            health_summary="All healthy",
            timestamp="2024-01-01T00:00:00Z",
        )

        assert output.machine_phases == {}
        assert output.machines == []
        assert output.conditions == []
        assert output.warnings == []


# =============================================================================
# Tests for get_mosk_platform_status function
# =============================================================================


class TestGetMoskPlatformStatus:
    """Tests for get_mosk_platform_status function."""

    @pytest.fixture
    def mock_mcc_adapter(self) -> AsyncMock:
        """Create mock MCC adapter."""
        adapter = AsyncMock()
        adapter.get_cluster = AsyncMock(return_value=None)
        adapter.list_machines = AsyncMock(return_value=[])
        return adapter

    @pytest.fixture
    def sample_cluster_ready(self) -> dict[str, Any]:
        """Sample cluster CR in ready state."""
        return {
            "metadata": {"name": "mos", "namespace": "lab"},
            "spec": {"providerSpec": {"value": {"release": "mosk-21-0-0-25-2"}}},
            "status": {
                "providerStatus": {
                    "release": "mosk-21-0-0-25-2",
                    "conditions": [
                        {"type": "Helm", "ready": True, "message": "All charts deployed"},
                        {"type": "Nodes", "ready": True, "message": "All nodes ready"},
                        {"type": "Ceph", "ready": True, "message": "Ceph healthy"},
                    ],
                }
            },
        }

    @pytest.fixture
    def sample_machines_ready(self) -> list[dict[str, Any]]:
        """Sample machines all in Ready state."""
        return [
            {
                "metadata": {
                    "name": "control-01",
                    "labels": {"cluster.sigs.k8s.io/cluster-name": "mos"},
                },
                "status": {"phase": "Ready"},
            },
            {
                "metadata": {
                    "name": "compute-01",
                    "labels": {"cluster.sigs.k8s.io/cluster-name": "mos"},
                },
                "status": {"phase": "Ready"},
            },
            {
                "metadata": {
                    "name": "storage-01",
                    "labels": {"cluster.sigs.k8s.io/cluster-name": "mos"},
                },
                "status": {"phase": "Ready"},
            },
        ]

    @pytest.mark.asyncio
    async def test_cluster_not_found(self, mock_mcc_adapter: AsyncMock) -> None:
        """Test when cluster is not found."""
        mock_mcc_adapter.get_cluster = AsyncMock(return_value=None)

        with pytest.raises(ResourceNotFoundError) as exc_info:
            await get_mosk_platform_status(
                mock_mcc_adapter,
                GetMoskPlatformStatusInput(cluster_name="mos", namespace="lab"),
            )

        assert "Cluster 'mos' not found" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_platform_ready(
        self,
        mock_mcc_adapter: AsyncMock,
        sample_cluster_ready: dict[str, Any],
        sample_machines_ready: list[dict[str, Any]],
    ) -> None:
        """Test platform in ready state."""
        mock_mcc_adapter.get_cluster = AsyncMock(return_value=sample_cluster_ready)
        mock_mcc_adapter.list_machines = AsyncMock(return_value=sample_machines_ready)

        result = await get_mosk_platform_status(
            mock_mcc_adapter,
            GetMoskPlatformStatusInput(cluster_name="mos", namespace="lab"),
        )

        assert result.phase == MoskPlatformPhase.READY
        assert result.is_upgrading is False
        assert result.machines_total == 3
        assert result.machines_ready == 3
        assert result.all_conditions_ready is True
        assert "healthy" in result.health_summary.lower()

    @pytest.mark.asyncio
    async def test_platform_upgrading(self, mock_mcc_adapter: AsyncMock) -> None:
        """Test platform during upgrade."""
        cluster = {
            "metadata": {"name": "mos", "namespace": "lab"},
            "spec": {
                "providerSpec": {
                    "value": {"release": "mosk-21-0-1-25-2"}  # Target
                }
            },
            "status": {
                "providerStatus": {
                    "release": "mosk-21-0-0-25-2",  # Current != target
                    "conditions": [],
                }
            },
        }
        machines = [
            {
                "metadata": {
                    "name": "compute-01",
                    "labels": {"cluster.sigs.k8s.io/cluster-name": "mos"},
                },
                "status": {"phase": "Deploy"},
            },
        ]

        mock_mcc_adapter.get_cluster = AsyncMock(return_value=cluster)
        mock_mcc_adapter.list_machines = AsyncMock(return_value=machines)

        result = await get_mosk_platform_status(
            mock_mcc_adapter,
            GetMoskPlatformStatusInput(cluster_name="mos", namespace="lab"),
        )

        assert result.phase == MoskPlatformPhase.UPGRADING
        assert result.is_upgrading is True
        assert result.current_release == "mosk-21-0-0-25-2"
        assert result.target_release == "mosk-21-0-1-25-2"

    @pytest.mark.asyncio
    async def test_platform_provisioning(self, mock_mcc_adapter: AsyncMock) -> None:
        """Test platform during provisioning."""
        cluster = {
            "metadata": {"name": "mos", "namespace": "lab"},
            "spec": {"providerSpec": {"value": {"release": "mosk-21-0-0-25-2"}}},
            "status": {
                "providerStatus": {
                    "release": "mosk-21-0-0-25-2",
                    "conditions": [],
                }
            },
        }
        machines = [
            {
                "metadata": {
                    "name": "compute-01",
                    "labels": {"cluster.sigs.k8s.io/cluster-name": "mos"},
                },
                "status": {"phase": "Ready"},
            },
            {
                "metadata": {
                    "name": "compute-02",
                    "labels": {"cluster.sigs.k8s.io/cluster-name": "mos"},
                },
                "status": {"phase": "Deploy"},
            },
            {
                "metadata": {
                    "name": "compute-03",
                    "labels": {"cluster.sigs.k8s.io/cluster-name": "mos"},
                },
                "status": {"phase": "Prepare"},
            },
        ]

        mock_mcc_adapter.get_cluster = AsyncMock(return_value=cluster)
        mock_mcc_adapter.list_machines = AsyncMock(return_value=machines)

        result = await get_mosk_platform_status(
            mock_mcc_adapter,
            GetMoskPlatformStatusInput(cluster_name="mos", namespace="lab"),
        )

        assert result.phase == MoskPlatformPhase.PROVISIONING
        assert result.machines_ready == 1
        assert result.machines_total == 3
        assert "Provisioning" in result.health_summary

    @pytest.mark.asyncio
    async def test_platform_degraded_conditions_not_ready(
        self, mock_mcc_adapter: AsyncMock
    ) -> None:
        """Test platform degraded when conditions not ready."""
        cluster = {
            "metadata": {"name": "mos", "namespace": "lab"},
            "spec": {"providerSpec": {"value": {"release": "mosk-21-0-0-25-2"}}},
            "status": {
                "providerStatus": {
                    "release": "mosk-21-0-0-25-2",
                    "conditions": [
                        {"type": "Helm", "ready": True, "message": "OK"},
                        {"type": "Ceph", "ready": False, "message": "Ceph degraded"},
                    ],
                }
            },
        }
        machines = [
            {
                "metadata": {
                    "name": "compute-01",
                    "labels": {"cluster.sigs.k8s.io/cluster-name": "mos"},
                },
                "status": {"phase": "Ready"},
            },
        ]

        mock_mcc_adapter.get_cluster = AsyncMock(return_value=cluster)
        mock_mcc_adapter.list_machines = AsyncMock(return_value=machines)

        result = await get_mosk_platform_status(
            mock_mcc_adapter,
            GetMoskPlatformStatusInput(cluster_name="mos", namespace="lab"),
        )

        assert result.phase == MoskPlatformPhase.DEGRADED
        assert result.all_conditions_ready is False
        assert len(result.warnings) > 0
        assert "Ceph" in result.health_summary

    @pytest.mark.asyncio
    async def test_platform_degraded_machines_not_ready(self, mock_mcc_adapter: AsyncMock) -> None:
        """Test platform degraded when machines not ready but not provisioning."""
        cluster = {
            "metadata": {"name": "mos", "namespace": "lab"},
            "spec": {"providerSpec": {"value": {"release": "mosk-21-0-0-25-2"}}},
            "status": {
                "providerStatus": {
                    "release": "mosk-21-0-0-25-2",
                    "conditions": [],
                }
            },
        }
        machines = [
            {
                "metadata": {
                    "name": "compute-01",
                    "labels": {"cluster.sigs.k8s.io/cluster-name": "mos"},
                },
                "status": {"phase": "Ready"},
            },
            {
                "metadata": {
                    "name": "compute-02",
                    "labels": {"cluster.sigs.k8s.io/cluster-name": "mos"},
                },
                "status": {"phase": "Unknown"},  # Not a provisioning phase
            },
        ]

        mock_mcc_adapter.get_cluster = AsyncMock(return_value=cluster)
        mock_mcc_adapter.list_machines = AsyncMock(return_value=machines)

        result = await get_mosk_platform_status(
            mock_mcc_adapter,
            GetMoskPlatformStatusInput(cluster_name="mos", namespace="lab"),
        )

        assert result.phase == MoskPlatformPhase.DEGRADED
        assert "Degraded" in result.health_summary

    @pytest.mark.asyncio
    async def test_no_machines(self, mock_mcc_adapter: AsyncMock) -> None:
        """Test when no machines found for cluster."""
        cluster = {
            "metadata": {"name": "mos", "namespace": "lab"},
            "spec": {"providerSpec": {"value": {"release": "mosk-21-0-0-25-2"}}},
            "status": {
                "providerStatus": {
                    "release": "mosk-21-0-0-25-2",
                    "conditions": [],
                }
            },
        }

        mock_mcc_adapter.get_cluster = AsyncMock(return_value=cluster)
        mock_mcc_adapter.list_machines = AsyncMock(return_value=[])

        result = await get_mosk_platform_status(
            mock_mcc_adapter,
            GetMoskPlatformStatusInput(cluster_name="mos", namespace="lab"),
        )

        assert result.phase == MoskPlatformPhase.UNKNOWN
        assert result.machines_total == 0
        assert "No machines found" in result.health_summary

    @pytest.mark.asyncio
    async def test_machine_filtering_by_label(self, mock_mcc_adapter: AsyncMock) -> None:
        """Test machines are filtered by cluster label."""
        cluster = {
            "metadata": {"name": "mos", "namespace": "lab"},
            "spec": {"providerSpec": {"value": {"release": "mosk-21-0-0-25-2"}}},
            "status": {
                "providerStatus": {
                    "release": "mosk-21-0-0-25-2",
                    "conditions": [],
                }
            },
        }
        machines = [
            {
                "metadata": {
                    "name": "compute-01",
                    "labels": {"cluster.sigs.k8s.io/cluster-name": "mos"},
                },
                "status": {"phase": "Ready"},
            },
            {
                "metadata": {
                    "name": "compute-02",
                    "labels": {"cluster.sigs.k8s.io/cluster-name": "other-cluster"},
                },
                "status": {"phase": "Ready"},
            },
        ]

        mock_mcc_adapter.get_cluster = AsyncMock(return_value=cluster)
        mock_mcc_adapter.list_machines = AsyncMock(return_value=machines)

        result = await get_mosk_platform_status(
            mock_mcc_adapter,
            GetMoskPlatformStatusInput(cluster_name="mos", namespace="lab"),
        )

        # Only 1 machine should be included (the one belonging to 'mos')
        assert result.machines_total == 1

    @pytest.mark.asyncio
    async def test_machine_filtering_by_owner_ref(self, mock_mcc_adapter: AsyncMock) -> None:
        """Test machines are filtered by owner reference."""
        cluster = {
            "metadata": {"name": "mos", "namespace": "lab"},
            "spec": {"providerSpec": {"value": {"release": "mosk-21-0-0-25-2"}}},
            "status": {
                "providerStatus": {
                    "release": "mosk-21-0-0-25-2",
                    "conditions": [],
                }
            },
        }
        machines = [
            {
                "metadata": {
                    "name": "compute-01",
                    "ownerReferences": [{"kind": "Cluster", "name": "mos"}],
                },
                "status": {"phase": "Ready"},
            },
        ]

        mock_mcc_adapter.get_cluster = AsyncMock(return_value=cluster)
        mock_mcc_adapter.list_machines = AsyncMock(return_value=machines)

        result = await get_mosk_platform_status(
            mock_mcc_adapter,
            GetMoskPlatformStatusInput(cluster_name="mos", namespace="lab"),
        )

        assert result.machines_total == 1

    @pytest.mark.asyncio
    async def test_include_machine_details_true(self, mock_mcc_adapter: AsyncMock) -> None:
        """Test machine details are included when requested."""
        cluster = {
            "metadata": {"name": "mos", "namespace": "lab"},
            "spec": {"providerSpec": {"value": {"release": "mosk-21-0-0-25-2"}}},
            "status": {
                "providerStatus": {
                    "release": "mosk-21-0-0-25-2",
                    "conditions": [],
                }
            },
        }
        machines = [
            {
                "metadata": {
                    "name": "compute-01",
                    "labels": {"cluster.sigs.k8s.io/cluster-name": "mos"},
                },
                "status": {"phase": "Ready"},
            },
        ]

        mock_mcc_adapter.get_cluster = AsyncMock(return_value=cluster)
        mock_mcc_adapter.list_machines = AsyncMock(return_value=machines)

        result = await get_mosk_platform_status(
            mock_mcc_adapter,
            GetMoskPlatformStatusInput(
                cluster_name="mos",
                namespace="lab",
                include_machine_details=True,
            ),
        )

        assert len(result.machines) == 1
        assert result.machines[0].name == "compute-01"
        assert result.machines[0].phase == "Ready"
        assert result.machines[0].is_ready is True

    @pytest.mark.asyncio
    async def test_include_machine_details_false(self, mock_mcc_adapter: AsyncMock) -> None:
        """Test machine details are excluded when not requested."""
        cluster = {
            "metadata": {"name": "mos", "namespace": "lab"},
            "spec": {"providerSpec": {"value": {"release": "mosk-21-0-0-25-2"}}},
            "status": {
                "providerStatus": {
                    "release": "mosk-21-0-0-25-2",
                    "conditions": [],
                }
            },
        }
        machines = [
            {
                "metadata": {
                    "name": "compute-01",
                    "labels": {"cluster.sigs.k8s.io/cluster-name": "mos"},
                },
                "status": {"phase": "Ready"},
            },
        ]

        mock_mcc_adapter.get_cluster = AsyncMock(return_value=cluster)
        mock_mcc_adapter.list_machines = AsyncMock(return_value=machines)

        result = await get_mosk_platform_status(
            mock_mcc_adapter,
            GetMoskPlatformStatusInput(
                cluster_name="mos",
                namespace="lab",
                include_machine_details=False,
            ),
        )

        assert len(result.machines) == 0  # No details
        assert result.machines_total == 1  # But count is still correct

    @pytest.mark.asyncio
    async def test_machine_phase_counts(self, mock_mcc_adapter: AsyncMock) -> None:
        """Test machine phase counts are correct."""
        cluster = {
            "metadata": {"name": "mos", "namespace": "lab"},
            "spec": {"providerSpec": {"value": {"release": "mosk-21-0-0-25-2"}}},
            "status": {
                "providerStatus": {
                    "release": "mosk-21-0-0-25-2",
                    "conditions": [],
                }
            },
        }
        machines = [
            {
                "metadata": {
                    "name": "control-01",
                    "labels": {"cluster.sigs.k8s.io/cluster-name": "mos"},
                },
                "status": {"phase": "Ready"},
            },
            {
                "metadata": {
                    "name": "compute-01",
                    "labels": {"cluster.sigs.k8s.io/cluster-name": "mos"},
                },
                "status": {"phase": "Ready"},
            },
            {
                "metadata": {
                    "name": "compute-02",
                    "labels": {"cluster.sigs.k8s.io/cluster-name": "mos"},
                },
                "status": {"phase": "Deploy"},
            },
            {
                "metadata": {
                    "name": "storage-01",
                    "labels": {"cluster.sigs.k8s.io/cluster-name": "mos"},
                },
                "status": {"phase": "Prepare"},
            },
        ]

        mock_mcc_adapter.get_cluster = AsyncMock(return_value=cluster)
        mock_mcc_adapter.list_machines = AsyncMock(return_value=machines)

        result = await get_mosk_platform_status(
            mock_mcc_adapter,
            GetMoskPlatformStatusInput(cluster_name="mos", namespace="lab"),
        )

        assert result.machine_phases["Ready"] == 2
        assert result.machine_phases["Deploy"] == 1
        assert result.machine_phases["Prepare"] == 1
        assert result.machines_ready == 2
        assert result.machines_total == 4

    @pytest.mark.asyncio
    async def test_api_error_handling(self, mock_mcc_adapter: AsyncMock) -> None:
        """Test API error handling."""
        mock_mcc_adapter.get_cluster = AsyncMock(side_effect=Exception("API connection failed"))

        with pytest.raises(ToolExecutionError) as exc_info:
            await get_mosk_platform_status(
                mock_mcc_adapter,
                GetMoskPlatformStatusInput(cluster_name="mos", namespace="lab"),
            )

        assert "Failed to get MOSK platform status" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_conditions_parsed_correctly(self, mock_mcc_adapter: AsyncMock) -> None:
        """Test cluster conditions are parsed correctly."""
        cluster = {
            "metadata": {"name": "mos", "namespace": "lab"},
            "spec": {"providerSpec": {"value": {"release": "mosk-21-0-0-25-2"}}},
            "status": {
                "providerStatus": {
                    "release": "mosk-21-0-0-25-2",
                    "conditions": [
                        {"type": "Helm", "ready": True, "message": "All charts deployed"},
                        {"type": "Nodes", "ready": True, "message": "3/3 nodes ready"},
                        {"type": "Ceph", "ready": False, "message": "Ceph warning: 1 OSD down"},
                        {"type": "Kubernetes", "ready": True, "message": ""},
                    ],
                }
            },
        }
        machines = [
            {
                "metadata": {
                    "name": "compute-01",
                    "labels": {"cluster.sigs.k8s.io/cluster-name": "mos"},
                },
                "status": {"phase": "Ready"},
            },
        ]

        mock_mcc_adapter.get_cluster = AsyncMock(return_value=cluster)
        mock_mcc_adapter.list_machines = AsyncMock(return_value=machines)

        result = await get_mosk_platform_status(
            mock_mcc_adapter,
            GetMoskPlatformStatusInput(cluster_name="mos", namespace="lab"),
        )

        assert len(result.conditions) == 4
        assert result.all_conditions_ready is False

        # Find Ceph condition
        ceph_cond = next(c for c in result.conditions if c.type == "Ceph")
        assert ceph_cond.ready is False
        assert "OSD down" in ceph_cond.message

    @pytest.mark.asyncio
    async def test_timestamp_included(self, mock_mcc_adapter: AsyncMock) -> None:
        """Test timestamp is included in output."""
        cluster = {
            "metadata": {"name": "mos", "namespace": "lab"},
            "spec": {"providerSpec": {"value": {"release": "mosk-21-0-0-25-2"}}},
            "status": {
                "providerStatus": {
                    "release": "mosk-21-0-0-25-2",
                    "conditions": [],
                }
            },
        }

        mock_mcc_adapter.get_cluster = AsyncMock(return_value=cluster)
        mock_mcc_adapter.list_machines = AsyncMock(return_value=[])

        result = await get_mosk_platform_status(
            mock_mcc_adapter,
            GetMoskPlatformStatusInput(cluster_name="mos", namespace="lab"),
        )

        assert result.timestamp is not None
        assert len(result.timestamp) > 0
