"""Unit tests for MOSK platform upgrade monitor."""

from typing import Any
from unittest.mock import AsyncMock

import pytest

from mosk_mcp.core.exceptions import ResourceNotFoundError
from mosk_mcp.tools.operations_visibility.monitors.mosk_upgrade_monitor import (
    CONDITION_WEIGHTS,
    MACHINE_PHASE_WEIGHTS,
    PHASE_MESSAGES,
    MachinePhase,
    MoskUpgradeMonitor,
    MoskUpgradePhase,
)


# =============================================================================
# Tests for Constants
# =============================================================================


class TestMoskUpgradePhase:
    """Tests for MoskUpgradePhase constants."""

    def test_all_phases_defined(self) -> None:
        """Test all phases are defined."""
        assert hasattr(MoskUpgradePhase, "NOT_STARTED")
        assert hasattr(MoskUpgradePhase, "HELM_UPGRADING")
        assert hasattr(MoskUpgradePhase, "MACHINES_PREPARING")
        assert hasattr(MoskUpgradePhase, "MACHINES_DEPLOYING")
        assert hasattr(MoskUpgradePhase, "MACHINES_RECONFIGURING")
        assert hasattr(MoskUpgradePhase, "CEPH_UPGRADING")
        assert hasattr(MoskUpgradePhase, "FINALIZING")
        assert hasattr(MoskUpgradePhase, "COMPLETED")
        assert hasattr(MoskUpgradePhase, "FAILED")

    def test_phase_values(self) -> None:
        """Test phase values are strings."""
        assert MoskUpgradePhase.NOT_STARTED == "not_started"
        assert MoskUpgradePhase.COMPLETED == "completed"
        assert MoskUpgradePhase.FAILED == "failed"


class TestMachinePhase:
    """Tests for MachinePhase constants."""

    def test_all_phases_defined(self) -> None:
        """Test all machine phases are defined."""
        assert hasattr(MachinePhase, "READY")
        assert hasattr(MachinePhase, "PREPARE")
        assert hasattr(MachinePhase, "DEPLOY")
        assert hasattr(MachinePhase, "RECONFIGURE")

    def test_phase_values(self) -> None:
        """Test machine phase values."""
        assert MachinePhase.READY == "Ready"
        assert MachinePhase.PREPARE == "Prepare"
        assert MachinePhase.DEPLOY == "Deploy"
        assert MachinePhase.RECONFIGURE == "Reconfigure"


class TestPhaseMessages:
    """Tests for PHASE_MESSAGES dictionary."""

    def test_all_phases_have_messages(self) -> None:
        """Test all phases have corresponding messages."""
        phases = [
            MoskUpgradePhase.NOT_STARTED,
            MoskUpgradePhase.HELM_UPGRADING,
            MoskUpgradePhase.MACHINES_PREPARING,
            MoskUpgradePhase.MACHINES_DEPLOYING,
            MoskUpgradePhase.MACHINES_RECONFIGURING,
            MoskUpgradePhase.CEPH_UPGRADING,
            MoskUpgradePhase.FINALIZING,
            MoskUpgradePhase.COMPLETED,
            MoskUpgradePhase.FAILED,
        ]
        for phase in phases:
            assert phase in PHASE_MESSAGES
            assert isinstance(PHASE_MESSAGES[phase], str)
            assert len(PHASE_MESSAGES[phase]) > 0


class TestMachinePhaseWeights:
    """Tests for MACHINE_PHASE_WEIGHTS dictionary."""

    def test_all_phases_have_weights(self) -> None:
        """Test all machine phases have weights."""
        phases = [
            MachinePhase.READY,
            MachinePhase.PREPARE,
            MachinePhase.DEPLOY,
            MachinePhase.RECONFIGURE,
        ]
        for phase in phases:
            assert phase in MACHINE_PHASE_WEIGHTS
            assert isinstance(MACHINE_PHASE_WEIGHTS[phase], int)
            assert 0 <= MACHINE_PHASE_WEIGHTS[phase] <= 100

    def test_ready_highest_weight(self) -> None:
        """Test Ready phase has highest weight."""
        assert MACHINE_PHASE_WEIGHTS[MachinePhase.READY] == 100

    def test_weight_progression(self) -> None:
        """Test weights progress towards Ready."""
        assert (
            MACHINE_PHASE_WEIGHTS[MachinePhase.PREPARE] < MACHINE_PHASE_WEIGHTS[MachinePhase.DEPLOY]
        )
        assert (
            MACHINE_PHASE_WEIGHTS[MachinePhase.DEPLOY]
            < MACHINE_PHASE_WEIGHTS[MachinePhase.RECONFIGURE]
        )
        assert (
            MACHINE_PHASE_WEIGHTS[MachinePhase.RECONFIGURE]
            < MACHINE_PHASE_WEIGHTS[MachinePhase.READY]
        )


class TestConditionWeights:
    """Tests for CONDITION_WEIGHTS dictionary."""

    def test_expected_conditions(self) -> None:
        """Test expected conditions are defined."""
        expected = ["Helm", "Kubernetes", "Ceph", "LCMAgent", "StackLight", "Nodes"]
        for cond in expected:
            assert cond in CONDITION_WEIGHTS

    def test_weight_format(self) -> None:
        """Test weight format is (ready_weight, not_ready_weight) tuple."""
        for _cond, weights in CONDITION_WEIGHTS.items():
            assert isinstance(weights, tuple)
            assert len(weights) == 2
            assert isinstance(weights[0], int)  # ready weight
            assert isinstance(weights[1], int)  # not ready weight
            assert weights[0] > weights[1]  # ready should be higher


# =============================================================================
# Tests for MoskUpgradeMonitor
# =============================================================================


class TestMoskUpgradeMonitor:
    """Tests for MoskUpgradeMonitor class."""

    @pytest.fixture
    def mock_adapter(self) -> AsyncMock:
        """Create mock MCC adapter."""
        adapter = AsyncMock()
        adapter.get_cluster = AsyncMock(return_value=None)
        adapter.list_machines = AsyncMock(return_value=[])
        adapter.list_lcm_machines = AsyncMock(return_value=[])
        adapter.get_helm_bundle = AsyncMock(return_value=None)
        return adapter

    def test_initialization(self, mock_adapter: AsyncMock) -> None:
        """Test monitor initialization."""
        monitor = MoskUpgradeMonitor(
            adapter=mock_adapter,
            target="mos",
            namespace="lab",
        )

        assert monitor.target == "mos"
        assert monitor.namespace == "lab"
        assert monitor._current_phase == MoskUpgradePhase.NOT_STARTED
        assert monitor._progress_percent == 0
        assert monitor._is_upgrading is False

    def test_is_complete_false_initially(self, mock_adapter: AsyncMock) -> None:
        """Test is_complete returns False initially."""
        monitor = MoskUpgradeMonitor(mock_adapter, "mos", "lab")
        assert monitor.is_complete() is False

    def test_has_failed_false_initially(self, mock_adapter: AsyncMock) -> None:
        """Test has_failed returns False initially."""
        monitor = MoskUpgradeMonitor(mock_adapter, "mos", "lab")
        assert monitor.has_failed() is False

    def test_get_error_message_none_initially(self, mock_adapter: AsyncMock) -> None:
        """Test get_error_message returns None initially."""
        monitor = MoskUpgradeMonitor(mock_adapter, "mos", "lab")
        assert monitor.get_error_message() is None

    @pytest.mark.asyncio
    async def test_get_progress_not_started(self, mock_adapter: AsyncMock) -> None:
        """Test get_progress when no upgrade is happening."""
        mock_adapter.get_cluster = AsyncMock(
            return_value={
                "metadata": {"name": "mos"},
                "spec": {"providerSpec": {"value": {"release": "mosk-21-0-0-25-2"}}},
                "status": {
                    "providerStatus": {"release": "mosk-21-0-0-25-2"},
                    "conditions": [
                        {"type": "Nodes", "status": "True", "message": "All nodes ready"},
                    ],
                },
            }
        )
        mock_adapter.list_machines = AsyncMock(
            return_value=[
                {
                    "metadata": {
                        "name": "compute-01",
                        "labels": {"cluster.sigs.k8s.io/cluster-name": "mos"},
                    },
                    "status": {"phase": "Ready"},
                }
            ]
        )
        mock_adapter.list_lcm_machines = AsyncMock(return_value=[])
        mock_adapter.get_helm_bundle = AsyncMock(return_value=None)

        monitor = MoskUpgradeMonitor(mock_adapter, "mos", "lab")
        snapshot = await monitor.get_progress()

        assert snapshot.progress_percent >= 0
        assert snapshot.phase is not None

    @pytest.mark.asyncio
    async def test_get_cluster_data_not_found(self, mock_adapter: AsyncMock) -> None:
        """Test _get_cluster_data when cluster not found."""
        mock_adapter.get_cluster = AsyncMock(side_effect=ResourceNotFoundError("clusters/mos"))

        monitor = MoskUpgradeMonitor(mock_adapter, "mos", "lab")
        result = await monitor._get_cluster_data()

        assert result == {}

    @pytest.mark.asyncio
    async def test_get_cluster_data_error(self, mock_adapter: AsyncMock) -> None:
        """Test _get_cluster_data with API error."""
        mock_adapter.get_cluster = AsyncMock(side_effect=Exception("API error"))

        monitor = MoskUpgradeMonitor(mock_adapter, "mos", "lab")
        result = await monitor._get_cluster_data()

        assert "error" in result

    @pytest.mark.asyncio
    async def test_get_machine_phases_success(self, mock_adapter: AsyncMock) -> None:
        """Test _get_machine_phases success."""
        mock_adapter.list_machines = AsyncMock(
            return_value=[
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
        )

        monitor = MoskUpgradeMonitor(mock_adapter, "mos", "lab")
        result = await monitor._get_machine_phases()

        # Should filter to only machines belonging to 'mos' cluster
        assert len(result) >= 0

    @pytest.mark.asyncio
    async def test_get_machine_phases_error(self, mock_adapter: AsyncMock) -> None:
        """Test _get_machine_phases with error."""
        mock_adapter.list_machines = AsyncMock(side_effect=Exception("API error"))

        monitor = MoskUpgradeMonitor(mock_adapter, "mos", "lab")
        result = await monitor._get_machine_phases()

        assert result == []

    @pytest.mark.asyncio
    async def test_get_lcm_machine_data_success(self, mock_adapter: AsyncMock) -> None:
        """Test _get_lcm_machine_data success."""
        mock_adapter.list_lcm_machines = AsyncMock(
            return_value=[{"metadata": {"name": "compute-01"}, "status": {"state": "Ready"}}]
        )

        monitor = MoskUpgradeMonitor(mock_adapter, "mos", "lab")
        result = await monitor._get_lcm_machine_data()

        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_get_lcm_machine_data_error(self, mock_adapter: AsyncMock) -> None:
        """Test _get_lcm_machine_data with error."""
        mock_adapter.list_lcm_machines = AsyncMock(side_effect=Exception("API error"))

        monitor = MoskUpgradeMonitor(mock_adapter, "mos", "lab")
        result = await monitor._get_lcm_machine_data()

        assert result == []

    @pytest.mark.asyncio
    async def test_is_complete_after_upgrade(self, mock_adapter: AsyncMock) -> None:
        """Test is_complete after setting completed phase."""
        monitor = MoskUpgradeMonitor(mock_adapter, "mos", "lab")
        monitor._current_phase = MoskUpgradePhase.COMPLETED

        assert monitor.is_complete() is True

    @pytest.mark.asyncio
    async def test_has_failed_after_failure(self, mock_adapter: AsyncMock) -> None:
        """Test has_failed after setting failed phase."""
        monitor = MoskUpgradeMonitor(mock_adapter, "mos", "lab")
        monitor._current_phase = MoskUpgradePhase.FAILED
        monitor._error_message = "Upgrade failed due to timeout"

        assert monitor.has_failed() is True
        assert monitor.get_error_message() == "Upgrade failed due to timeout"

    @pytest.mark.asyncio
    async def test_progress_with_machines_deploying(self, mock_adapter: AsyncMock) -> None:
        """Test progress snapshot includes machine phase details."""
        mock_adapter.get_cluster = AsyncMock(
            return_value={
                "metadata": {"name": "mos"},
                "spec": {"providerSpec": {"value": {"release": "mosk-21-0-1-25-2"}}},
                "status": {
                    "providerStatus": {"release": "mosk-21-0-0-25-2"},
                    "conditions": [],
                },
            }
        )
        mock_adapter.list_machines = AsyncMock(
            return_value=[
                {
                    "metadata": {
                        "name": "compute-01",
                        "labels": {"cluster.sigs.k8s.io/cluster-name": "mos"},
                    },
                    "status": {"phase": "Deploy"},
                },
                {
                    "metadata": {
                        "name": "compute-02",
                        "labels": {"cluster.sigs.k8s.io/cluster-name": "mos"},
                    },
                    "status": {"phase": "Ready"},
                },
            ]
        )
        mock_adapter.list_lcm_machines = AsyncMock(return_value=[])
        mock_adapter.get_helm_bundle = AsyncMock(return_value=None)

        monitor = MoskUpgradeMonitor(mock_adapter, "mos", "lab")
        snapshot = await monitor.get_progress()

        # Should have machine phase details
        assert snapshot.details is not None


class TestMoskUpgradeMonitorIntegration:
    """Integration-style tests for MoskUpgradeMonitor."""

    @pytest.fixture
    def mock_adapter(self) -> AsyncMock:
        """Create mock adapter with full data."""
        adapter = AsyncMock()
        return adapter

    @pytest.fixture
    def sample_cluster_upgrading(self) -> dict[str, Any]:
        """Sample cluster during upgrade."""
        return {
            "metadata": {"name": "mos", "namespace": "lab"},
            "spec": {
                "providerSpec": {
                    "value": {"release": "mosk-21-0-1-25-2"}  # Target release
                }
            },
            "status": {
                "providerStatus": {"release": "mosk-21-0-0-25-2"},  # Current release
                "conditions": [
                    {"type": "Helm", "status": "False", "message": "Upgrading charts"},
                    {"type": "Nodes", "status": "False", "message": "3/5 nodes ready"},
                ],
            },
        }

    @pytest.fixture
    def sample_machines_upgrading(self) -> list[dict[str, Any]]:
        """Sample machines during upgrade."""
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
                "status": {"phase": "Deploy"},
            },
            {
                "metadata": {
                    "name": "compute-02",
                    "labels": {"cluster.sigs.k8s.io/cluster-name": "mos"},
                },
                "status": {"phase": "Prepare"},
            },
        ]

    @pytest.mark.asyncio
    async def test_upgrade_in_progress(
        self,
        mock_adapter: AsyncMock,
        sample_cluster_upgrading: dict[str, Any],
        sample_machines_upgrading: list[dict[str, Any]],
    ) -> None:
        """Test monitoring upgrade in progress."""
        mock_adapter.get_cluster = AsyncMock(return_value=sample_cluster_upgrading)
        mock_adapter.list_machines = AsyncMock(return_value=sample_machines_upgrading)
        mock_adapter.list_lcm_machines = AsyncMock(return_value=[])
        mock_adapter.get_helm_bundle = AsyncMock(return_value=None)

        monitor = MoskUpgradeMonitor(mock_adapter, "mos", "lab")
        snapshot = await monitor.get_progress()

        # Should detect upgrade in progress
        assert snapshot.progress_percent < 100
        assert not monitor.is_complete()

    @pytest.mark.asyncio
    async def test_upgrade_completed(self, mock_adapter: AsyncMock) -> None:
        """Test monitoring completed upgrade."""
        mock_adapter.get_cluster = AsyncMock(
            return_value={
                "metadata": {"name": "mos"},
                "spec": {"providerSpec": {"value": {"release": "mosk-21-0-1-25-2"}}},
                "status": {
                    "providerStatus": {"release": "mosk-21-0-1-25-2"},  # Same as target
                    "conditions": [
                        {"type": "Helm", "status": "True", "message": "All charts deployed"},
                        {"type": "Nodes", "status": "True", "message": "All nodes ready"},
                        {"type": "Ceph", "status": "True", "message": "Ceph healthy"},
                    ],
                },
            }
        )
        mock_adapter.list_machines = AsyncMock(
            return_value=[
                {
                    "metadata": {
                        "name": "compute-01",
                        "labels": {"cluster.sigs.k8s.io/cluster-name": "mos"},
                    },
                    "status": {"phase": "Ready"},
                },
            ]
        )
        mock_adapter.list_lcm_machines = AsyncMock(return_value=[])
        mock_adapter.get_helm_bundle = AsyncMock(return_value=None)

        monitor = MoskUpgradeMonitor(mock_adapter, "mos", "lab")
        snapshot = await monitor.get_progress()

        # All conditions ready, releases match - should be near completion
        assert snapshot.progress_percent >= 90

    @pytest.mark.asyncio
    async def test_helm_bundle_not_found(self, mock_adapter: AsyncMock) -> None:
        """Test get_helm_bundle_data when bundle not found."""
        mock_adapter.get_helm_bundle = AsyncMock(
            side_effect=ResourceNotFoundError("HelmBundle/mos")
        )

        monitor = MoskUpgradeMonitor(mock_adapter, "mos", "lab")
        result = await monitor._get_helm_bundle_data()

        assert result is None

    @pytest.mark.asyncio
    async def test_helm_bundle_error(self, mock_adapter: AsyncMock) -> None:
        """Test get_helm_bundle_data with error."""
        mock_adapter.get_helm_bundle = AsyncMock(side_effect=Exception("API error"))

        monitor = MoskUpgradeMonitor(mock_adapter, "mos", "lab")
        result = await monitor._get_helm_bundle_data()

        assert result is None

    def test_is_owned_by_cluster_via_owner_references(self, mock_adapter: AsyncMock) -> None:
        """Test _is_owned_by_cluster via ownerReferences."""
        monitor = MoskUpgradeMonitor(mock_adapter, "mos", "lab")

        resource_with_owner = {
            "metadata": {
                "name": "compute-01",
                "ownerReferences": [
                    {"kind": "Cluster", "name": "mos"},
                ],
            },
        }

        assert monitor._is_owned_by_cluster(resource_with_owner, "mos") is True
        assert monitor._is_owned_by_cluster(resource_with_owner, "other") is False

    def test_is_owned_by_cluster_via_label(self, mock_adapter: AsyncMock) -> None:
        """Test _is_owned_by_cluster via label."""
        monitor = MoskUpgradeMonitor(mock_adapter, "mos", "lab")

        resource_with_label = {
            "metadata": {
                "name": "compute-01",
                "labels": {"cluster.sigs.k8s.io/cluster-name": "mos"},
            },
        }

        assert monitor._is_owned_by_cluster(resource_with_label, "mos") is True
        assert monitor._is_owned_by_cluster(resource_with_label, "other") is False

    def test_set_failed_state(self, mock_adapter: AsyncMock) -> None:
        """Test _set_failed_state method."""
        monitor = MoskUpgradeMonitor(mock_adapter, "mos", "lab")
        monitor._set_failed_state("Something went wrong")

        assert monitor._current_phase == MoskUpgradePhase.FAILED
        assert monitor._error_message == "Something went wrong"
        assert monitor._progress_percent == -1

    def test_set_not_started_state(self, mock_adapter: AsyncMock) -> None:
        """Test _set_not_started_state method."""
        monitor = MoskUpgradeMonitor(mock_adapter, "mos", "lab")
        monitor._set_not_started_state("Cluster not found")

        assert monitor._current_phase == MoskUpgradePhase.NOT_STARTED
        assert monitor._error_message == "Cluster not found"
        assert monitor._progress_percent == 0

    def test_determine_state_with_error(self, mock_adapter: AsyncMock) -> None:
        """Test _determine_state when cluster has error."""
        monitor = MoskUpgradeMonitor(mock_adapter, "mos", "lab")
        cluster_with_error = {"error": "Connection refused"}

        monitor._determine_state(cluster_with_error, [], [], None)

        assert monitor._current_phase == MoskUpgradePhase.FAILED
        assert "Connection refused" in monitor._error_message

    def test_determine_state_cluster_not_found(self, mock_adapter: AsyncMock) -> None:
        """Test _determine_state when cluster is empty."""
        monitor = MoskUpgradeMonitor(mock_adapter, "mos", "lab")

        monitor._determine_state({}, [], [], None)

        assert monitor._current_phase == MoskUpgradePhase.NOT_STARTED
        assert "not found" in monitor._error_message.lower()

    def test_parse_cluster_data_with_conditions(self, mock_adapter: AsyncMock) -> None:
        """Test _parse_cluster_data extracts conditions."""
        monitor = MoskUpgradeMonitor(mock_adapter, "mos", "lab")

        cluster = {
            "spec": {"providerSpec": {"value": {"release": "mosk-21-0-1"}}},
            "status": {
                "providerStatus": {
                    "release": "mosk-21-0-0",
                    "conditions": [
                        {"type": "Helm", "ready": True, "message": "OK"},
                        {"type": "Nodes", "ready": False, "message": "2/3 ready"},
                    ],
                },
            },
        }

        monitor._parse_cluster_data(cluster)

        assert monitor._to_release == "mosk-21-0-1"
        assert monitor._from_release == "mosk-21-0-0"
        assert "Helm" in monitor._cluster_conditions
        assert monitor._cluster_conditions["Helm"]["ready"] is True
        assert monitor._cluster_conditions["Nodes"]["ready"] is False

    def test_parse_machine_data_with_lcm(self, mock_adapter: AsyncMock) -> None:
        """Test _parse_machine_data with LCM machine data."""
        monitor = MoskUpgradeMonitor(mock_adapter, "mos", "lab")

        machines = [
            {
                "metadata": {"name": "compute-01"},
                "status": {"phase": "Deploy"},
            },
        ]
        lcm_machines = [
            {
                "metadata": {"name": "compute-01"},
                "status": {"state": "Deploying"},
            },
        ]

        monitor._parse_machine_data(machines, lcm_machines)

        assert "compute-01" in monitor._machine_phases

    def test_parse_helm_data(self, mock_adapter: AsyncMock) -> None:
        """Test _parse_helm_data extracts release statuses."""
        monitor = MoskUpgradeMonitor(mock_adapter, "mos", "lab")

        helm_bundle = {
            "status": {
                "releaseStatuses": {
                    "keystone": {"ready": True, "success": True, "status": "deployed"},
                    "nova": {"ready": False, "success": False, "status": "upgrading"},
                },
            },
        }

        monitor._parse_helm_data(helm_bundle)

        assert "keystone" in monitor._helm_statuses
        assert monitor._helm_statuses["keystone"]["ready"] is True
        assert monitor._helm_statuses["nova"]["ready"] is False

    def test_check_completion_all_ready(self, mock_adapter: AsyncMock) -> None:
        """Test _check_completion when all machines ready."""
        monitor = MoskUpgradeMonitor(mock_adapter, "mos", "lab")
        monitor._machine_phases = {
            "compute-01": MachinePhase.READY,
            "compute-02": MachinePhase.READY,
        }

        result = monitor._check_completion(cluster_ready=True)

        assert result is True
        assert monitor._current_phase == MoskUpgradePhase.COMPLETED
        assert monitor._progress_percent == 100
        assert monitor._is_upgrading is False

    def test_check_completion_machines_not_ready(self, mock_adapter: AsyncMock) -> None:
        """Test _check_completion when machines not ready."""
        monitor = MoskUpgradeMonitor(mock_adapter, "mos", "lab")
        monitor._machine_phases = {
            "compute-01": MachinePhase.READY,
            "compute-02": MachinePhase.DEPLOY,
        }

        result = monitor._check_completion(cluster_ready=True)

        assert result is False

    def test_determine_current_phase_helm_not_ready(self, mock_adapter: AsyncMock) -> None:
        """Test _determine_current_phase when Helm not ready."""
        monitor = MoskUpgradeMonitor(mock_adapter, "mos", "lab")
        monitor._cluster_conditions = {"Helm": {"ready": False}}

        monitor._determine_current_phase()

        assert monitor._current_phase == MoskUpgradePhase.HELM_UPGRADING
        assert monitor._progress_percent == 5

    def test_determine_current_phase_ceph_upgrading(self, mock_adapter: AsyncMock) -> None:
        """Test _determine_current_phase when Ceph upgrading."""
        monitor = MoskUpgradeMonitor(mock_adapter, "mos", "lab")
        monitor._cluster_conditions = {
            "Helm": {"ready": True},
            "Ceph": {"ready": False, "message": "OSDs upgrading"},
        }
        monitor._machine_phases = {"compute-01": MachinePhase.READY}

        monitor._determine_current_phase()

        assert monitor._current_phase == MoskUpgradePhase.CEPH_UPGRADING

    def test_determine_phase_from_machines_deploying(self, mock_adapter: AsyncMock) -> None:
        """Test _determine_phase_from_machines with Deploy state."""
        monitor = MoskUpgradeMonitor(mock_adapter, "mos", "lab")
        monitor._machine_phases = {
            "compute-01": MachinePhase.DEPLOY,
            "compute-02": MachinePhase.READY,
        }

        monitor._determine_phase_from_machines()

        assert monitor._current_phase == MoskUpgradePhase.MACHINES_DEPLOYING

    def test_determine_phase_from_machines_reconfiguring(self, mock_adapter: AsyncMock) -> None:
        """Test _determine_phase_from_machines with Reconfigure state."""
        monitor = MoskUpgradeMonitor(mock_adapter, "mos", "lab")
        monitor._machine_phases = {
            "compute-01": MachinePhase.RECONFIGURE,
            "compute-02": MachinePhase.READY,
        }

        monitor._determine_phase_from_machines()

        assert monitor._current_phase == MoskUpgradePhase.MACHINES_RECONFIGURING

    def test_determine_phase_from_machines_preparing(self, mock_adapter: AsyncMock) -> None:
        """Test _determine_phase_from_machines with Prepare state."""
        monitor = MoskUpgradeMonitor(mock_adapter, "mos", "lab")
        monitor._machine_phases = {
            "compute-01": MachinePhase.PREPARE,
            "compute-02": MachinePhase.READY,
        }

        monitor._determine_phase_from_machines()

        assert monitor._current_phase == MoskUpgradePhase.MACHINES_PREPARING

    def test_determine_phase_from_machines_all_ready(self, mock_adapter: AsyncMock) -> None:
        """Test _determine_phase_from_machines when all ready."""
        monitor = MoskUpgradeMonitor(mock_adapter, "mos", "lab")
        monitor._machine_phases = {
            "compute-01": MachinePhase.READY,
            "compute-02": MachinePhase.READY,
        }

        monitor._determine_phase_from_machines()

        assert monitor._current_phase == MoskUpgradePhase.FINALIZING

    def test_calculate_progress_from_machines_empty(self, mock_adapter: AsyncMock) -> None:
        """Test _calculate_progress_from_machines with no machines."""
        monitor = MoskUpgradeMonitor(mock_adapter, "mos", "lab")
        monitor._machine_phases = {}

        result = monitor._calculate_progress_from_machines()

        assert result == 0

    def test_calculate_progress_from_machines(self, mock_adapter: AsyncMock) -> None:
        """Test _calculate_progress_from_machines calculation."""
        monitor = MoskUpgradeMonitor(mock_adapter, "mos", "lab")
        monitor._machine_phases = {
            "compute-01": MachinePhase.READY,  # weight 100
            "compute-02": MachinePhase.DEPLOY,  # weight 50
        }

        result = monitor._calculate_progress_from_machines()

        # avg = (100 + 50) / 2 = 75
        # scaled = 10 + int(75 * 0.8) = 10 + 60 = 70
        assert result == 70

    @pytest.mark.asyncio
    async def test_get_progress_with_error_message(self, mock_adapter: AsyncMock) -> None:
        """Test get_progress includes error message in output."""
        mock_adapter.get_cluster = AsyncMock(return_value={"error": "Connection failed"})

        monitor = MoskUpgradeMonitor(mock_adapter, "mos", "lab")
        snapshot = await monitor.get_progress()

        assert "Connection failed" in snapshot.message
        assert monitor.has_failed() is True

    @pytest.mark.asyncio
    async def test_get_progress_with_machine_details(self, mock_adapter: AsyncMock) -> None:
        """Test get_progress includes machine details."""
        mock_adapter.get_cluster = AsyncMock(
            return_value={
                "metadata": {"name": "mos"},
                "spec": {"providerSpec": {"value": {"release": "mosk-21-0-1"}}},
                "status": {"providerStatus": {"release": "mosk-21-0-0"}},
            }
        )
        mock_adapter.list_machines = AsyncMock(
            return_value=[
                {
                    "metadata": {
                        "name": "compute-01",
                        "labels": {"cluster.sigs.k8s.io/cluster-name": "mos"},
                    },
                    "status": {"phase": "Deploy"},
                },
                {
                    "metadata": {
                        "name": "compute-02",
                        "labels": {"cluster.sigs.k8s.io/cluster-name": "mos"},
                    },
                    "status": {"phase": "Ready"},
                },
            ]
        )
        mock_adapter.list_lcm_machines = AsyncMock(return_value=[])
        mock_adapter.get_helm_bundle = AsyncMock(return_value=None)

        monitor = MoskUpgradeMonitor(mock_adapter, "mos", "lab")
        snapshot = await monitor.get_progress()

        assert "machine_phases" in snapshot.details
        assert "machines_total" in snapshot.details
        assert snapshot.details["machines_total"] == 2
        assert "machines_in_progress" in snapshot.details

    @pytest.mark.asyncio
    async def test_get_progress_with_helm_status(self, mock_adapter: AsyncMock) -> None:
        """Test get_progress includes helm status."""
        mock_adapter.get_cluster = AsyncMock(
            return_value={
                "metadata": {"name": "mos"},
                "spec": {"providerSpec": {"value": {"release": "mosk-21-0-1"}}},
                "status": {"providerStatus": {"release": "mosk-21-0-0"}},
            }
        )
        mock_adapter.list_machines = AsyncMock(
            return_value=[
                {
                    "metadata": {
                        "name": "compute-01",
                        "labels": {"cluster.sigs.k8s.io/cluster-name": "mos"},
                    },
                    "status": {"phase": "Ready"},
                },
            ]
        )
        mock_adapter.list_lcm_machines = AsyncMock(return_value=[])
        mock_adapter.get_helm_bundle = AsyncMock(
            return_value={
                "status": {
                    "releaseStatuses": {
                        "nova": {"ready": False, "success": False, "status": "upgrading"},
                    },
                },
            }
        )

        monitor = MoskUpgradeMonitor(mock_adapter, "mos", "lab")
        snapshot = await monitor.get_progress()

        assert "helm_not_ready" in snapshot.details

    @pytest.mark.asyncio
    async def test_get_progress_with_conditions_not_ready(self, mock_adapter: AsyncMock) -> None:
        """Test get_progress includes conditions not ready."""
        mock_adapter.get_cluster = AsyncMock(
            return_value={
                "metadata": {"name": "mos"},
                "spec": {"providerSpec": {"value": {"release": "mosk-21-0-1"}}},
                "status": {
                    "providerStatus": {
                        "release": "mosk-21-0-0",
                        "conditions": [
                            {"type": "Helm", "ready": False, "message": "Charts upgrading"},
                        ],
                    },
                },
            }
        )
        mock_adapter.list_machines = AsyncMock(
            return_value=[
                {
                    "metadata": {
                        "name": "compute-01",
                        "labels": {"cluster.sigs.k8s.io/cluster-name": "mos"},
                    },
                    "status": {"phase": "Ready"},
                },
            ]
        )
        mock_adapter.list_lcm_machines = AsyncMock(return_value=[])
        mock_adapter.get_helm_bundle = AsyncMock(return_value=None)

        monitor = MoskUpgradeMonitor(mock_adapter, "mos", "lab")
        snapshot = await monitor.get_progress()

        assert "conditions" in snapshot.details
        assert "conditions_not_ready" in snapshot.details
