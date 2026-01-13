"""Unit tests for get_node_provision_progress tool."""

from unittest.mock import AsyncMock

import pytest

from mosk_mcp.core.exceptions import KubernetesError, ResourceNotFoundError
from mosk_mcp.tools.node_lifecycle.get_node_provision_progress import (
    PHASE_PROGRESS,
    GetNodeProvisionProgressInput,
    GetNodeProvisionProgressOutput,
    ProvisionPhase,
    ResourceStatus,
    _determine_phase,
    _get_bmh_status,
    _get_bmhi_status,
    _get_ipamhost_status,
    _get_lcmmachine_status,
    _get_machine_status,
    _get_node_status,
    get_node_provision_progress,
)


# =============================================================================
# Tests for ProvisionPhase Enum
# =============================================================================


class TestProvisionPhase:
    """Tests for ProvisionPhase enum."""

    def test_all_phases_defined(self) -> None:
        """Test all provisioning phases are defined."""
        phases = list(ProvisionPhase)
        expected_phases = [
            ProvisionPhase.NOT_STARTED,
            ProvisionPhase.BMHI_CREATED,
            ProvisionPhase.BMH_REGISTERING,
            ProvisionPhase.BMH_INSPECTING,
            ProvisionPhase.BMH_PREPARING,
            ProvisionPhase.BMH_AVAILABLE,
            ProvisionPhase.MACHINE_CREATED,
            ProvisionPhase.BMH_PROVISIONING,
            ProvisionPhase.BMH_PROVISIONED,
            ProvisionPhase.MACHINE_DEPLOYING,
            ProvisionPhase.MACHINE_READY,
            ProvisionPhase.NODE_READY,
            ProvisionPhase.COMPLETED,
            ProvisionPhase.ERROR,
        ]
        for phase in expected_phases:
            assert phase in phases

    def test_phase_values(self) -> None:
        """Test phase string values."""
        assert ProvisionPhase.NOT_STARTED.value == "not_started"
        assert ProvisionPhase.COMPLETED.value == "completed"
        assert ProvisionPhase.ERROR.value == "error"


class TestPhaseProgress:
    """Tests for PHASE_PROGRESS mapping."""

    def test_all_phases_have_progress(self) -> None:
        """Test all phases have progress values."""
        for phase in ProvisionPhase:
            assert phase in PHASE_PROGRESS

    def test_progress_increases(self) -> None:
        """Test progress increases through normal phases."""
        ordered_phases = [
            ProvisionPhase.NOT_STARTED,
            ProvisionPhase.BMHI_CREATED,
            ProvisionPhase.BMH_REGISTERING,
            ProvisionPhase.BMH_INSPECTING,
            ProvisionPhase.BMH_PREPARING,
            ProvisionPhase.BMH_AVAILABLE,
            ProvisionPhase.MACHINE_CREATED,
            ProvisionPhase.BMH_PROVISIONING,
            ProvisionPhase.BMH_PROVISIONED,
            ProvisionPhase.MACHINE_DEPLOYING,
            ProvisionPhase.MACHINE_READY,
            ProvisionPhase.NODE_READY,
            ProvisionPhase.COMPLETED,
        ]
        prev_progress = -2  # Start below error (-1)
        for phase in ordered_phases:
            assert PHASE_PROGRESS[phase] > prev_progress
            prev_progress = PHASE_PROGRESS[phase]

    def test_error_phase_negative(self) -> None:
        """Test error phase has negative progress."""
        assert PHASE_PROGRESS[ProvisionPhase.ERROR] == -1

    def test_completed_phase_100(self) -> None:
        """Test completed phase is 100%."""
        assert PHASE_PROGRESS[ProvisionPhase.COMPLETED] == 100


# =============================================================================
# Tests for Input/Output Models
# =============================================================================


class TestGetNodeProvisionProgressInput:
    """Tests for GetNodeProvisionProgressInput model."""

    def test_required_name(self) -> None:
        """Test name is required."""
        with pytest.raises(ValueError):
            GetNodeProvisionProgressInput()

    def test_default_namespace(self) -> None:
        """Test default namespace."""
        input_data = GetNodeProvisionProgressInput(name="compute-01")
        assert input_data.namespace == "default"

    def test_custom_namespace(self) -> None:
        """Test custom namespace."""
        input_data = GetNodeProvisionProgressInput(name="compute-01", namespace="lab")
        assert input_data.namespace == "lab"

    def test_name_min_length(self) -> None:
        """Test name minimum length."""
        with pytest.raises(ValueError):
            GetNodeProvisionProgressInput(name="")

    def test_name_max_length(self) -> None:
        """Test name maximum length."""
        # Should pass with 253 chars (k8s max)
        long_name = "a" * 253
        input_data = GetNodeProvisionProgressInput(name=long_name)
        assert len(input_data.name) == 253

        # Should fail with 254 chars
        with pytest.raises(ValueError):
            GetNodeProvisionProgressInput(name="a" * 254)


class TestResourceStatus:
    """Tests for ResourceStatus model."""

    def test_defaults(self) -> None:
        """Test default values."""
        status = ResourceStatus()
        assert status.exists is False
        assert status.query_failed is False
        assert status.state is None
        assert status.status is None
        assert status.message is None
        assert status.details == {}

    def test_existing_resource(self) -> None:
        """Test existing resource."""
        status = ResourceStatus(
            exists=True,
            state="Ready",
            status="OK",
            message="All checks passed",
            details={"version": "1.0"},
        )
        assert status.exists is True
        assert status.state == "Ready"
        assert status.status == "OK"
        assert status.message == "All checks passed"
        assert status.details == {"version": "1.0"}

    def test_query_failed(self) -> None:
        """Test query failed state."""
        status = ResourceStatus(
            exists=False,
            query_failed=True,
            message="API timeout",
        )
        assert status.exists is False
        assert status.query_failed is True
        assert status.message == "API timeout"


class TestGetNodeProvisionProgressOutput:
    """Tests for GetNodeProvisionProgressOutput model."""

    def test_required_fields(self) -> None:
        """Test required fields."""
        output = GetNodeProvisionProgressOutput(
            name="compute-01",
            namespace="default",
            current_phase=ProvisionPhase.NOT_STARTED,
            progress_percent=0,
        )
        assert output.name == "compute-01"
        assert output.namespace == "default"
        assert output.current_phase == ProvisionPhase.NOT_STARTED
        assert output.progress_percent == 0

    def test_default_values(self) -> None:
        """Test default values."""
        output = GetNodeProvisionProgressOutput(
            name="compute-01",
            namespace="default",
            current_phase=ProvisionPhase.NOT_STARTED,
            progress_percent=0,
        )
        assert output.is_complete is False
        assert output.has_error is False
        assert output.error_message is None
        assert output.next_expected_phase is None
        assert output.estimated_remaining_steps == 0

    def test_progress_bounds_error_valid(self) -> None:
        """Test progress percentage bounds for error (-1) is valid."""
        output = GetNodeProvisionProgressOutput(
            name="compute-01",
            namespace="default",
            current_phase=ProvisionPhase.ERROR,
            progress_percent=-1,
        )
        assert output.progress_percent == -1

    def test_progress_bounds_completed_valid(self) -> None:
        """Test progress percentage bounds for completed (100) is valid."""
        output = GetNodeProvisionProgressOutput(
            name="compute-01",
            namespace="default",
            current_phase=ProvisionPhase.COMPLETED,
            progress_percent=100,
        )
        assert output.progress_percent == 100

    def test_progress_bounds_below_negative_one_invalid(self) -> None:
        """Test progress percentage below -1 is invalid."""
        with pytest.raises(ValueError):
            GetNodeProvisionProgressOutput(
                name="compute-01",
                namespace="default",
                current_phase=ProvisionPhase.ERROR,
                progress_percent=-2,
            )

    def test_progress_bounds_above_100_invalid(self) -> None:
        """Test progress percentage above 100 is invalid."""
        with pytest.raises(ValueError):
            GetNodeProvisionProgressOutput(
                name="compute-01",
                namespace="default",
                current_phase=ProvisionPhase.COMPLETED,
                progress_percent=101,
            )


# =============================================================================
# Tests for Helper Functions
# =============================================================================


class TestGetBmhiStatus:
    """Tests for _get_bmhi_status helper."""

    @pytest.fixture
    def mock_adapter(self) -> AsyncMock:
        """Create mock adapter."""
        return AsyncMock()

    @pytest.mark.asyncio
    async def test_bmhi_exists(self, mock_adapter: AsyncMock) -> None:
        """Test BMHi exists."""
        mock_adapter.get_custom_resource = AsyncMock(
            return_value={
                "metadata": {"name": "compute-01"},
                "spec": {
                    "bmc": {"address": "ipmi://192.168.1.10"},
                    "online": True,
                },
                "status": {
                    "operationalStatus": "OK",
                },
            }
        )

        result = await _get_bmhi_status(mock_adapter, "compute-01", "default")

        assert result.exists is True
        assert result.state == "OK"
        assert result.status == "OK"
        assert result.details["bmc"] == "ipmi://192.168.1.10"
        assert result.details["online"] is True

    @pytest.mark.asyncio
    async def test_bmhi_not_found(self, mock_adapter: AsyncMock) -> None:
        """Test BMHi not found."""
        mock_adapter.get_custom_resource = AsyncMock(
            side_effect=ResourceNotFoundError("baremetalhostinventories/compute-01")
        )

        result = await _get_bmhi_status(mock_adapter, "compute-01", "default")

        assert result.exists is False
        assert result.query_failed is False

    @pytest.mark.asyncio
    async def test_bmhi_query_failed(self, mock_adapter: AsyncMock) -> None:
        """Test BMHi query failed."""
        mock_adapter.get_custom_resource = AsyncMock(side_effect=Exception("API timeout"))

        result = await _get_bmhi_status(mock_adapter, "compute-01", "default")

        assert result.exists is False
        assert result.query_failed is True
        assert "Query failed" in result.message


class TestGetBmhStatus:
    """Tests for _get_bmh_status helper."""

    @pytest.fixture
    def mock_adapter(self) -> AsyncMock:
        """Create mock adapter."""
        return AsyncMock()

    @pytest.mark.asyncio
    async def test_bmh_exists(self, mock_adapter: AsyncMock) -> None:
        """Test BMH exists."""
        mock_adapter.get_custom_resource = AsyncMock(
            return_value={
                "metadata": {"name": "compute-01"},
                "spec": {
                    "consumerRef": {"name": "compute-01"},
                },
                "status": {
                    "provisioning": {"state": "provisioned"},
                    "operationalStatus": "OK",
                    "poweredOn": True,
                },
            }
        )

        result = await _get_bmh_status(mock_adapter, "compute-01", "default")

        assert result.exists is True
        assert result.state == "provisioned"
        assert result.status == "OK"
        assert result.details["powered_on"] is True
        assert result.details["consumer"] == "compute-01"

    @pytest.mark.asyncio
    async def test_bmh_not_found(self, mock_adapter: AsyncMock) -> None:
        """Test BMH not found."""
        mock_adapter.get_custom_resource = AsyncMock(
            side_effect=ResourceNotFoundError("baremetalhosts/compute-01")
        )

        result = await _get_bmh_status(mock_adapter, "compute-01", "default")

        assert result.exists is False
        assert result.query_failed is False


class TestGetMachineStatus:
    """Tests for _get_machine_status helper."""

    @pytest.fixture
    def mock_adapter(self) -> AsyncMock:
        """Create mock adapter."""
        return AsyncMock()

    @pytest.mark.asyncio
    async def test_machine_exists(self, mock_adapter: AsyncMock) -> None:
        """Test Machine exists."""
        mock_adapter.get_custom_resource = AsyncMock(
            return_value={
                "metadata": {"name": "compute-01"},
                "status": {
                    "phase": "Ready",
                    "nodeRef": {"name": "compute-01"},
                    "addresses": [{"type": "InternalIP", "address": "10.0.0.1"}],
                },
            }
        )

        result = await _get_machine_status(mock_adapter, "compute-01", "default")

        assert result.exists is True
        assert result.state == "Ready"
        assert result.status == "Ready"
        assert result.details["node_ref"] == "compute-01"

    @pytest.mark.asyncio
    async def test_machine_not_found(self, mock_adapter: AsyncMock) -> None:
        """Test Machine not found."""
        mock_adapter.get_custom_resource = AsyncMock(
            side_effect=ResourceNotFoundError("machines/compute-01")
        )

        result = await _get_machine_status(mock_adapter, "compute-01", "default")

        assert result.exists is False
        assert result.query_failed is False


class TestGetLcmmachineStatus:
    """Tests for _get_lcmmachine_status helper."""

    @pytest.fixture
    def mock_adapter(self) -> AsyncMock:
        """Create mock adapter."""
        return AsyncMock()

    @pytest.mark.asyncio
    async def test_lcmmachine_exists(self, mock_adapter: AsyncMock) -> None:
        """Test LCMMachine exists."""
        mock_adapter.get_custom_resource = AsyncMock(
            return_value={
                "metadata": {"name": "compute-01"},
                "spec": {"type": "worker"},
                "status": {
                    "state": "Ready",
                    "internalIP": "10.0.0.1",
                    "hostname": "compute-01",
                },
            }
        )

        result = await _get_lcmmachine_status(mock_adapter, "compute-01", "default")

        assert result.exists is True
        assert result.state == "Ready"
        assert result.status == "Ready"
        assert result.details["type"] == "worker"
        assert result.details["internal_ip"] == "10.0.0.1"

    @pytest.mark.asyncio
    async def test_lcmmachine_not_found(self, mock_adapter: AsyncMock) -> None:
        """Test LCMMachine not found."""
        mock_adapter.get_custom_resource = AsyncMock(
            side_effect=ResourceNotFoundError("lcmmachines/compute-01")
        )

        result = await _get_lcmmachine_status(mock_adapter, "compute-01", "default")

        assert result.exists is False
        assert result.query_failed is False


class TestGetIpamhostStatus:
    """Tests for _get_ipamhost_status helper."""

    @pytest.fixture
    def mock_adapter(self) -> AsyncMock:
        """Create mock adapter."""
        return AsyncMock()

    @pytest.mark.asyncio
    async def test_ipamhost_exists(self, mock_adapter: AsyncMock) -> None:
        """Test IpamHost exists."""
        mock_adapter.get_custom_resource = AsyncMock(
            return_value={
                "metadata": {"name": "compute-01"},
                "status": {
                    "state": "OK",
                    "l2TemplateRef": "compute-l2template",
                    "serviceMap": {"pxe": {}, "storage": {}},
                },
            }
        )

        result = await _get_ipamhost_status(mock_adapter, "compute-01", "default")

        assert result.exists is True
        assert result.state == "OK"
        assert result.status == "OK"
        assert result.details["l2_template"] == "compute-l2template"
        assert "pxe" in result.details["service_map"]
        assert "storage" in result.details["service_map"]

    @pytest.mark.asyncio
    async def test_ipamhost_not_found(self, mock_adapter: AsyncMock) -> None:
        """Test IpamHost not found."""
        mock_adapter.get_custom_resource = AsyncMock(
            side_effect=ResourceNotFoundError("ipamhosts/compute-01")
        )

        result = await _get_ipamhost_status(mock_adapter, "compute-01", "default")

        assert result.exists is False
        assert result.query_failed is False


class TestGetNodeStatus:
    """Tests for _get_node_status helper."""

    @pytest.fixture
    def mock_adapter(self) -> AsyncMock:
        """Create mock adapter."""
        return AsyncMock()

    @pytest.mark.asyncio
    async def test_node_ready(self, mock_adapter: AsyncMock) -> None:
        """Test Node is ready."""
        mock_adapter.get = AsyncMock(
            return_value={
                "metadata": {"name": "compute-01"},
                "status": {
                    "conditions": [
                        {"type": "Ready", "status": "True", "message": "kubelet is ready"},
                    ],
                    "nodeInfo": {"kubeletVersion": "v1.30.0"},
                },
            }
        )

        result = await _get_node_status(mock_adapter, "compute-01")

        assert result.exists is True
        assert result.state == "Ready"
        assert result.status == "Ready"
        assert result.message == "kubelet is ready"
        assert result.details["kubelet_version"] == "v1.30.0"

    @pytest.mark.asyncio
    async def test_node_not_ready(self, mock_adapter: AsyncMock) -> None:
        """Test Node is not ready."""
        mock_adapter.get = AsyncMock(
            return_value={
                "metadata": {"name": "compute-01"},
                "status": {
                    "conditions": [
                        {"type": "Ready", "status": "False", "message": "kubelet not ready"},
                    ],
                },
            }
        )

        result = await _get_node_status(mock_adapter, "compute-01")

        assert result.exists is True
        assert result.state == "NotReady"
        assert result.status == "NotReady"

    @pytest.mark.asyncio
    async def test_node_no_name(self, mock_adapter: AsyncMock) -> None:
        """Test with no node name."""
        result = await _get_node_status(mock_adapter, None)

        assert result.exists is False
        assert result.message == "Node reference not found"

    @pytest.mark.asyncio
    async def test_node_not_found(self, mock_adapter: AsyncMock) -> None:
        """Test Node not found."""
        mock_adapter.get = AsyncMock(side_effect=ResourceNotFoundError("nodes/compute-01"))

        result = await _get_node_status(mock_adapter, "compute-01")

        assert result.exists is False
        assert result.query_failed is False


# =============================================================================
# Tests for _determine_phase
# =============================================================================


class TestDeterminePhase:
    """Tests for _determine_phase function."""

    def _resource_status(
        self,
        exists: bool = False,
        state: str | None = None,
        status: str | None = None,
        message: str | None = None,
    ) -> ResourceStatus:
        """Create ResourceStatus for testing."""
        return ResourceStatus(
            exists=exists,
            state=state,
            status=status,
            message=message,
        )

    def test_not_started(self) -> None:
        """Test NOT_STARTED when nothing exists."""
        phase, error, next_phase = _determine_phase(
            bmhi=self._resource_status(),
            bmh=self._resource_status(),
            machine=self._resource_status(),
            lcm=self._resource_status(),
            ipam=self._resource_status(),
            node=self._resource_status(),
        )
        assert phase == ProvisionPhase.NOT_STARTED
        assert error is None
        assert next_phase == "bmhi_created"

    def test_bmhi_created(self) -> None:
        """Test BMHI_CREATED when only BMHi exists."""
        phase, error, next_phase = _determine_phase(
            bmhi=self._resource_status(exists=True, state="OK"),
            bmh=self._resource_status(),
            machine=self._resource_status(),
            lcm=self._resource_status(),
            ipam=self._resource_status(),
            node=self._resource_status(),
        )
        assert phase == ProvisionPhase.BMHI_CREATED
        assert error is None
        assert next_phase == "bmh_registering"

    def test_bmh_registering(self) -> None:
        """Test BMH_REGISTERING phase."""
        phase, error, next_phase = _determine_phase(
            bmhi=self._resource_status(exists=True, state="OK"),
            bmh=self._resource_status(exists=True, state="registering"),
            machine=self._resource_status(),
            lcm=self._resource_status(),
            ipam=self._resource_status(),
            node=self._resource_status(),
        )
        assert phase == ProvisionPhase.BMH_REGISTERING
        assert error is None
        assert next_phase == "bmh_inspecting"

    def test_bmh_inspecting(self) -> None:
        """Test BMH_INSPECTING phase."""
        phase, error, next_phase = _determine_phase(
            bmhi=self._resource_status(exists=True),
            bmh=self._resource_status(exists=True, state="inspecting"),
            machine=self._resource_status(),
            lcm=self._resource_status(),
            ipam=self._resource_status(),
            node=self._resource_status(),
        )
        assert phase == ProvisionPhase.BMH_INSPECTING
        assert error is None
        assert next_phase == "bmh_preparing"

    def test_bmh_preparing(self) -> None:
        """Test BMH_PREPARING phase."""
        phase, error, next_phase = _determine_phase(
            bmhi=self._resource_status(exists=True),
            bmh=self._resource_status(exists=True, state="preparing"),
            machine=self._resource_status(),
            lcm=self._resource_status(),
            ipam=self._resource_status(),
            node=self._resource_status(),
        )
        assert phase == ProvisionPhase.BMH_PREPARING
        assert error is None
        assert next_phase == "bmh_available"

    def test_bmh_available(self) -> None:
        """Test BMH_AVAILABLE phase."""
        phase, error, next_phase = _determine_phase(
            bmhi=self._resource_status(exists=True),
            bmh=self._resource_status(exists=True, state="available"),
            machine=self._resource_status(),
            lcm=self._resource_status(),
            ipam=self._resource_status(),
            node=self._resource_status(),
        )
        assert phase == ProvisionPhase.BMH_AVAILABLE
        assert error is None
        assert next_phase == "machine_created"

    def test_machine_created(self) -> None:
        """Test MACHINE_CREATED phase."""
        phase, error, next_phase = _determine_phase(
            bmhi=self._resource_status(exists=True),
            bmh=self._resource_status(exists=True, state="available"),
            machine=self._resource_status(exists=True, state="Pending"),
            lcm=self._resource_status(),
            ipam=self._resource_status(),
            node=self._resource_status(),
        )
        assert phase == ProvisionPhase.MACHINE_CREATED
        assert error is None
        assert next_phase == "bmh_provisioning"

    def test_bmh_provisioning(self) -> None:
        """Test BMH_PROVISIONING phase."""
        phase, error, next_phase = _determine_phase(
            bmhi=self._resource_status(exists=True),
            bmh=self._resource_status(exists=True, state="provisioning"),
            machine=self._resource_status(exists=True, state="Pending"),
            lcm=self._resource_status(),
            ipam=self._resource_status(),
            node=self._resource_status(),
        )
        assert phase == ProvisionPhase.BMH_PROVISIONING
        assert error is None
        assert next_phase == "bmh_provisioned"

    def test_bmh_provisioned(self) -> None:
        """Test BMH_PROVISIONED phase."""
        phase, error, next_phase = _determine_phase(
            bmhi=self._resource_status(exists=True),
            bmh=self._resource_status(exists=True, state="provisioned"),
            machine=self._resource_status(exists=True, state="Pending"),
            lcm=self._resource_status(),
            ipam=self._resource_status(),
            node=self._resource_status(),
        )
        assert phase == ProvisionPhase.BMH_PROVISIONED
        assert error is None
        assert next_phase == "machine_deploying"

    def test_machine_deploying(self) -> None:
        """Test MACHINE_DEPLOYING phase."""
        phase, error, next_phase = _determine_phase(
            bmhi=self._resource_status(exists=True),
            bmh=self._resource_status(exists=True, state="provisioned"),
            machine=self._resource_status(exists=True, state="Ready"),
            lcm=self._resource_status(exists=True, state="Deploying"),
            ipam=self._resource_status(exists=True),
            node=self._resource_status(),
        )
        assert phase == ProvisionPhase.MACHINE_DEPLOYING
        assert error is None
        assert next_phase == "machine_ready"

    def test_machine_ready(self) -> None:
        """Test MACHINE_READY phase."""
        phase, error, next_phase = _determine_phase(
            bmhi=self._resource_status(exists=True),
            bmh=self._resource_status(exists=True, state="provisioned"),
            machine=self._resource_status(exists=True, state="Ready"),
            lcm=self._resource_status(exists=True, state="Ready"),
            ipam=self._resource_status(exists=True),
            node=self._resource_status(),
        )
        assert phase == ProvisionPhase.MACHINE_READY
        assert error is None
        assert next_phase == "node_ready"

    def test_node_ready(self) -> None:
        """Test NODE_READY phase."""
        phase, error, next_phase = _determine_phase(
            bmhi=self._resource_status(exists=True),
            bmh=self._resource_status(exists=True, state="provisioned"),
            machine=self._resource_status(exists=True, state="Pending"),  # Not quite ready yet
            lcm=self._resource_status(exists=True, state="Deploying"),
            ipam=self._resource_status(exists=True),
            node=self._resource_status(exists=True, state="Ready"),
        )
        assert phase == ProvisionPhase.NODE_READY
        assert error is None
        assert next_phase == "completed"

    def test_completed(self) -> None:
        """Test COMPLETED phase."""
        phase, error, next_phase = _determine_phase(
            bmhi=self._resource_status(exists=True),
            bmh=self._resource_status(exists=True, state="provisioned"),
            machine=self._resource_status(exists=True, state="Ready"),
            lcm=self._resource_status(exists=True, state="Ready"),
            ipam=self._resource_status(exists=True),
            node=self._resource_status(exists=True, state="Ready"),
        )
        assert phase == ProvisionPhase.COMPLETED
        assert error is None
        assert next_phase is None

    def test_error_from_status(self) -> None:
        """Test ERROR phase from error status."""
        phase, error, next_phase = _determine_phase(
            bmhi=self._resource_status(exists=True, status="error"),
            bmh=self._resource_status(),
            machine=self._resource_status(),
            lcm=self._resource_status(),
            ipam=self._resource_status(),
            node=self._resource_status(),
        )
        assert phase == ProvisionPhase.ERROR
        assert error is not None
        assert next_phase is None

    def test_error_from_message(self) -> None:
        """Test ERROR phase from error in message."""
        phase, error, next_phase = _determine_phase(
            bmhi=self._resource_status(exists=True, message="Registration error: BMC unreachable"),
            bmh=self._resource_status(),
            machine=self._resource_status(),
            lcm=self._resource_status(),
            ipam=self._resource_status(),
            node=self._resource_status(),
        )
        assert phase == ProvisionPhase.ERROR
        assert "BMHi" in error
        assert next_phase is None


# =============================================================================
# Tests for get_node_provision_progress Function
# =============================================================================


class TestGetNodeProvisionProgress:
    """Tests for the get_node_provision_progress function."""

    @pytest.fixture
    def mock_k8s_adapter(self) -> AsyncMock:
        """Create a mock Kubernetes adapter."""
        adapter = AsyncMock()
        adapter.get_custom_resource = AsyncMock()
        adapter.get = AsyncMock()
        return adapter

    @pytest.mark.asyncio
    async def test_not_started(self, mock_k8s_adapter: AsyncMock) -> None:
        """Test progress when no resources exist."""
        mock_k8s_adapter.get_custom_resource = AsyncMock(
            side_effect=ResourceNotFoundError("resource not found")
        )
        mock_k8s_adapter.get = AsyncMock(side_effect=ResourceNotFoundError("resource not found"))

        result = await get_node_provision_progress(
            mock_k8s_adapter,
            GetNodeProvisionProgressInput(name="compute-01"),
        )

        assert result.current_phase == ProvisionPhase.NOT_STARTED
        assert result.progress_percent == 0
        assert result.is_complete is False
        assert result.has_error is False

    @pytest.mark.asyncio
    async def test_bmhi_created(self, mock_k8s_adapter: AsyncMock) -> None:
        """Test progress when BMHi is created."""

        async def mock_get_custom_resource(group, version, plural, name, namespace):
            if plural == "baremetalhostinventories":
                return {
                    "metadata": {"name": name},
                    "spec": {"bmc": {"address": "ipmi://192.168.1.10"}, "online": True},
                    "status": {"operationalStatus": "OK"},
                }
            raise ResourceNotFoundError(f"{plural}/{name}")

        mock_k8s_adapter.get_custom_resource = mock_get_custom_resource
        mock_k8s_adapter.get = AsyncMock(side_effect=ResourceNotFoundError("resource not found"))

        result = await get_node_provision_progress(
            mock_k8s_adapter,
            GetNodeProvisionProgressInput(name="compute-01"),
        )

        assert result.current_phase == ProvisionPhase.BMHI_CREATED
        assert result.progress_percent == 5
        assert result.bmhi_status.exists is True
        assert result.bmh_status.exists is False

    @pytest.mark.asyncio
    async def test_completed(self, mock_k8s_adapter: AsyncMock) -> None:
        """Test progress when provisioning is complete."""

        async def mock_get_custom_resource(group, version, plural, name, namespace):
            resources = {
                "baremetalhostinventories": {
                    "metadata": {"name": name},
                    "spec": {"bmc": {"address": "ipmi://192.168.1.10"}, "online": True},
                    "status": {"operationalStatus": "OK"},
                },
                "baremetalhosts": {
                    "metadata": {"name": name},
                    "spec": {"consumerRef": {"name": name}},
                    "status": {
                        "provisioning": {"state": "provisioned"},
                        "operationalStatus": "OK",
                        "poweredOn": True,
                    },
                },
                "machines": {
                    "metadata": {"name": name},
                    "status": {
                        "phase": "Ready",
                        "nodeRef": {"name": name},
                    },
                },
                "lcmmachines": {
                    "metadata": {"name": name},
                    "spec": {"type": "worker"},
                    "status": {"state": "Ready", "internalIP": "10.0.0.1"},
                },
                "ipamhosts": {
                    "metadata": {"name": name},
                    "status": {"state": "OK", "serviceMap": {}},
                },
            }
            if plural in resources:
                return resources[plural]
            raise ResourceNotFoundError(f"{plural}/{name}")

        mock_k8s_adapter.get_custom_resource = mock_get_custom_resource
        mock_k8s_adapter.get = AsyncMock(
            return_value={
                "metadata": {"name": "compute-01"},
                "status": {
                    "conditions": [{"type": "Ready", "status": "True"}],
                    "nodeInfo": {"kubeletVersion": "v1.30.0"},
                },
            }
        )

        result = await get_node_provision_progress(
            mock_k8s_adapter,
            GetNodeProvisionProgressInput(name="compute-01"),
        )

        assert result.current_phase == ProvisionPhase.COMPLETED
        assert result.progress_percent == 100
        assert result.is_complete is True
        assert result.has_error is False
        assert result.estimated_remaining_steps == 0

    @pytest.mark.asyncio
    async def test_with_error(self, mock_k8s_adapter: AsyncMock) -> None:
        """Test progress when there's an error."""

        async def mock_get_custom_resource(group, version, plural, name, namespace):
            if plural == "baremetalhostinventories":
                return {
                    "metadata": {"name": name},
                    "spec": {"bmc": {"address": "ipmi://192.168.1.10"}},
                    "status": {
                        "operationalStatus": "error",
                        "errorMessage": "BMC unreachable",
                    },
                }
            raise ResourceNotFoundError(f"{plural}/{name}")

        mock_k8s_adapter.get_custom_resource = mock_get_custom_resource
        mock_k8s_adapter.get = AsyncMock(side_effect=ResourceNotFoundError("resource not found"))

        result = await get_node_provision_progress(
            mock_k8s_adapter,
            GetNodeProvisionProgressInput(name="compute-01"),
        )

        assert result.current_phase == ProvisionPhase.ERROR
        assert result.progress_percent == -1
        assert result.has_error is True
        assert result.error_message is not None

    @pytest.mark.asyncio
    async def test_api_error_results_in_query_failed(self, mock_k8s_adapter: AsyncMock) -> None:
        """Test handling of API errors results in query_failed status."""
        mock_k8s_adapter.get_custom_resource = AsyncMock(
            side_effect=KubernetesError(
                "API server unavailable",
                operation="get",
                resource_kind="BMHi",
                resource_name="compute-01",
            )
        )
        mock_k8s_adapter.get = AsyncMock(
            side_effect=KubernetesError(
                "API server unavailable",
                operation="get",
                resource_kind="Node",
                resource_name="compute-01",
            )
        )

        result = await get_node_provision_progress(
            mock_k8s_adapter,
            GetNodeProvisionProgressInput(name="compute-01"),
        )

        # Helper functions catch exceptions and return query_failed=True
        assert result.bmhi_status.query_failed is True
        assert result.bmh_status.query_failed is True
        assert result.machine_status.query_failed is True
        assert "Query failed" in result.bmhi_status.message

    @pytest.mark.asyncio
    async def test_custom_namespace(self, mock_k8s_adapter: AsyncMock) -> None:
        """Test with custom namespace."""
        mock_k8s_adapter.get_custom_resource = AsyncMock(
            side_effect=ResourceNotFoundError("resource not found")
        )
        mock_k8s_adapter.get = AsyncMock(side_effect=ResourceNotFoundError("resource not found"))

        result = await get_node_provision_progress(
            mock_k8s_adapter,
            GetNodeProvisionProgressInput(name="compute-01", namespace="lab"),
        )

        assert result.namespace == "lab"
        # Verify namespace was passed to API calls
        mock_k8s_adapter.get_custom_resource.assert_called()
        call_args = mock_k8s_adapter.get_custom_resource.call_args_list[0]
        assert call_args.kwargs["namespace"] == "lab"
