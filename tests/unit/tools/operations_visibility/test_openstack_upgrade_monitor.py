"""Unit tests for OpenStack upgrade monitor."""

from typing import Any
from unittest.mock import AsyncMock

import pytest

from mosk_mcp.core.exceptions import ResourceNotFoundError
from mosk_mcp.tools.operations_visibility.monitors.openstack_upgrade_monitor import (
    PHASE_MESSAGES,
    OpenStackUpgradeMonitor,
    UpgradePhase,
)


# =============================================================================
# Tests for Constants
# =============================================================================


class TestUpgradePhase:
    """Tests for UpgradePhase constants."""

    def test_all_phases_defined(self) -> None:
        """Test all phases are defined."""
        assert hasattr(UpgradePhase, "NOT_STARTED")
        assert hasattr(UpgradePhase, "INITIALIZING")
        assert hasattr(UpgradePhase, "UPGRADING_CONTROL_PLANE")
        assert hasattr(UpgradePhase, "UPGRADING_SERVICES")
        assert hasattr(UpgradePhase, "UPGRADING_COMPUTE")
        assert hasattr(UpgradePhase, "FINALIZING")
        assert hasattr(UpgradePhase, "COMPLETED")
        assert hasattr(UpgradePhase, "FAILED")

    def test_phase_values(self) -> None:
        """Test phase values are strings."""
        assert UpgradePhase.NOT_STARTED == "not_started"
        assert UpgradePhase.INITIALIZING == "initializing"
        assert UpgradePhase.COMPLETED == "completed"
        assert UpgradePhase.FAILED == "failed"


class TestPhaseMessages:
    """Tests for PHASE_MESSAGES dictionary."""

    def test_all_phases_have_messages(self) -> None:
        """Test all phases have corresponding messages."""
        phases = [
            UpgradePhase.NOT_STARTED,
            UpgradePhase.INITIALIZING,
            UpgradePhase.UPGRADING_CONTROL_PLANE,
            UpgradePhase.UPGRADING_SERVICES,
            UpgradePhase.UPGRADING_COMPUTE,
            UpgradePhase.FINALIZING,
            UpgradePhase.COMPLETED,
            UpgradePhase.FAILED,
        ]
        for phase in phases:
            assert phase in PHASE_MESSAGES
            assert isinstance(PHASE_MESSAGES[phase], str)
            assert len(PHASE_MESSAGES[phase]) > 0


# =============================================================================
# Tests for OpenStackUpgradeMonitor
# =============================================================================


class TestOpenStackUpgradeMonitor:
    """Tests for OpenStackUpgradeMonitor class."""

    @pytest.fixture
    def mock_adapter(self) -> AsyncMock:
        """Create mock MOSK adapter."""
        adapter = AsyncMock()
        adapter.get_openstack_deployment = AsyncMock(return_value=None)
        adapter.get_openstack_deployment_status = AsyncMock(return_value=None)
        return adapter

    def test_initialization(self, mock_adapter: AsyncMock) -> None:
        """Test monitor initialization."""
        monitor = OpenStackUpgradeMonitor(
            adapter=mock_adapter,
            target="mos",
            namespace="openstack",
        )

        assert monitor.target == "mos"
        assert monitor.namespace == "openstack"
        assert monitor._current_phase == UpgradePhase.NOT_STARTED
        assert monitor._progress_percent == 0
        assert monitor._is_upgrading is False
        assert monitor._from_version is None
        assert monitor._to_version is None

    def test_initialization_default_namespace(self, mock_adapter: AsyncMock) -> None:
        """Test monitor uses default namespace."""
        monitor = OpenStackUpgradeMonitor(
            adapter=mock_adapter,
            target="mos",
        )

        assert monitor.namespace == "openstack"

    def test_is_complete_false_initially(self, mock_adapter: AsyncMock) -> None:
        """Test is_complete returns False initially."""
        monitor = OpenStackUpgradeMonitor(mock_adapter, "mos")
        assert monitor.is_complete() is False

    def test_is_complete_true_when_completed(self, mock_adapter: AsyncMock) -> None:
        """Test is_complete returns True when phase is COMPLETED."""
        monitor = OpenStackUpgradeMonitor(mock_adapter, "mos")
        monitor._current_phase = UpgradePhase.COMPLETED
        assert monitor.is_complete() is True

    def test_has_failed_false_initially(self, mock_adapter: AsyncMock) -> None:
        """Test has_failed returns False initially."""
        monitor = OpenStackUpgradeMonitor(mock_adapter, "mos")
        assert monitor.has_failed() is False

    def test_has_failed_true_when_failed(self, mock_adapter: AsyncMock) -> None:
        """Test has_failed returns True when phase is FAILED."""
        monitor = OpenStackUpgradeMonitor(mock_adapter, "mos")
        monitor._current_phase = UpgradePhase.FAILED
        assert monitor.has_failed() is True

    def test_get_error_message_none_initially(self, mock_adapter: AsyncMock) -> None:
        """Test get_error_message returns None initially."""
        monitor = OpenStackUpgradeMonitor(mock_adapter, "mos")
        assert monitor.get_error_message() is None

    def test_get_error_message_returns_error(self, mock_adapter: AsyncMock) -> None:
        """Test get_error_message returns error when set."""
        monitor = OpenStackUpgradeMonitor(mock_adapter, "mos")
        monitor._error_message = "Upgrade failed due to timeout"
        assert monitor.get_error_message() == "Upgrade failed due to timeout"


class TestOpenStackUpgradeMonitorGetOsdplData:
    """Tests for _get_osdpl_data method."""

    @pytest.fixture
    def mock_adapter(self) -> AsyncMock:
        """Create mock adapter."""
        adapter = AsyncMock()
        return adapter

    @pytest.mark.asyncio
    async def test_get_osdpl_data_success(self, mock_adapter: AsyncMock) -> None:
        """Test _get_osdpl_data returns OSDPL data."""
        mock_adapter.get_openstack_deployment = AsyncMock(
            return_value={
                "metadata": {"name": "mos"},
                "spec": {"openStackVersion": "caracal"},
                "status": {"openStackVersion": "antelope"},
            }
        )

        monitor = OpenStackUpgradeMonitor(mock_adapter, "mos")
        result = await monitor._get_osdpl_data()

        assert result["metadata"]["name"] == "mos"
        assert result["spec"]["openStackVersion"] == "caracal"

    @pytest.mark.asyncio
    async def test_get_osdpl_data_not_found(self, mock_adapter: AsyncMock) -> None:
        """Test _get_osdpl_data when OSDPL not found."""
        mock_adapter.get_openstack_deployment = AsyncMock(
            side_effect=ResourceNotFoundError("osdpl/mos")
        )

        monitor = OpenStackUpgradeMonitor(mock_adapter, "mos")
        result = await monitor._get_osdpl_data()

        assert result == {}

    @pytest.mark.asyncio
    async def test_get_osdpl_data_none(self, mock_adapter: AsyncMock) -> None:
        """Test _get_osdpl_data when adapter returns None."""
        mock_adapter.get_openstack_deployment = AsyncMock(return_value=None)

        monitor = OpenStackUpgradeMonitor(mock_adapter, "mos")
        result = await monitor._get_osdpl_data()

        assert result == {}

    @pytest.mark.asyncio
    async def test_get_osdpl_data_error(self, mock_adapter: AsyncMock) -> None:
        """Test _get_osdpl_data with API error."""
        mock_adapter.get_openstack_deployment = AsyncMock(
            side_effect=Exception("API connection failed")
        )

        monitor = OpenStackUpgradeMonitor(mock_adapter, "mos")
        result = await monitor._get_osdpl_data()

        assert "error" in result
        assert "API connection failed" in result["error"]


class TestOpenStackUpgradeMonitorGetOsdplstData:
    """Tests for _get_osdplst_data method."""

    @pytest.fixture
    def mock_adapter(self) -> AsyncMock:
        """Create mock adapter."""
        adapter = AsyncMock()
        return adapter

    @pytest.mark.asyncio
    async def test_get_osdplst_data_success(self, mock_adapter: AsyncMock) -> None:
        """Test _get_osdplst_data returns OSDPLStatus data."""
        mock_adapter.get_openstack_deployment_status = AsyncMock(
            return_value={
                "metadata": {"name": "mos"},
                "status": {
                    "osdpl": {"state": "APPLIED", "lcmProgress": "18/18"},
                    "services": {},
                },
            }
        )

        monitor = OpenStackUpgradeMonitor(mock_adapter, "mos")
        result = await monitor._get_osdplst_data()

        assert result["status"]["osdpl"]["state"] == "APPLIED"

    @pytest.mark.asyncio
    async def test_get_osdplst_data_not_found(self, mock_adapter: AsyncMock) -> None:
        """Test _get_osdplst_data raises when OSDPLStatus not found."""
        mock_adapter.get_openstack_deployment_status = AsyncMock(return_value=None)

        monitor = OpenStackUpgradeMonitor(mock_adapter, "mos")

        with pytest.raises(ResourceNotFoundError) as exc_info:
            await monitor._get_osdplst_data()

        assert "OSDPLStatus 'mos' not found" in str(exc_info.value)


class TestOpenStackUpgradeMonitorExtractStartedAt:
    """Tests for _extract_started_at method."""

    @pytest.fixture
    def mock_adapter(self) -> AsyncMock:
        """Create mock adapter."""
        return AsyncMock()

    def test_extract_from_osdplst(self, mock_adapter: AsyncMock) -> None:
        """Test extracting start time from OSDPLStatus."""
        monitor = OpenStackUpgradeMonitor(mock_adapter, "mos")

        osdpl: dict[str, Any] = {}
        osdplst: dict[str, Any] = {"status": {"updateStartedAt": "2024-01-01T12:00:00Z"}}

        result = monitor._extract_started_at(osdpl, osdplst)
        assert result == "2024-01-01T12:00:00Z"

    def test_extract_from_osdpl_status(self, mock_adapter: AsyncMock) -> None:
        """Test extracting start time from OSDPL status."""
        monitor = OpenStackUpgradeMonitor(mock_adapter, "mos")

        osdpl: dict[str, Any] = {"status": {"updateStartedAt": "2024-01-01T10:00:00Z"}}
        osdplst: dict[str, Any] = {}

        result = monitor._extract_started_at(osdpl, osdplst)
        assert result == "2024-01-01T10:00:00Z"

    def test_extract_from_creation_time(self, mock_adapter: AsyncMock) -> None:
        """Test falling back to creation timestamp."""
        monitor = OpenStackUpgradeMonitor(mock_adapter, "mos")

        osdpl: dict[str, Any] = {"metadata": {"creationTimestamp": "2024-01-01T08:00:00Z"}}
        osdplst: dict[str, Any] = {}

        result = monitor._extract_started_at(osdpl, osdplst)
        assert result == "2024-01-01T08:00:00Z"

    def test_extract_none_when_no_time(self, mock_adapter: AsyncMock) -> None:
        """Test returning None when no time available."""
        monitor = OpenStackUpgradeMonitor(mock_adapter, "mos")

        osdpl: dict[str, Any] = {}
        osdplst: dict[str, Any] = {}

        result = monitor._extract_started_at(osdpl, osdplst)
        assert result is None


class TestOpenStackUpgradeMonitorParseLcmProgress:
    """Tests for _parse_lcm_progress method."""

    @pytest.fixture
    def mock_adapter(self) -> AsyncMock:
        """Create mock adapter."""
        return AsyncMock()

    def test_parse_valid_progress(self, mock_adapter: AsyncMock) -> None:
        """Test parsing valid progress string."""
        monitor = OpenStackUpgradeMonitor(mock_adapter, "mos")

        assert monitor._parse_lcm_progress("12/18") == 66
        assert monitor._parse_lcm_progress("0/10") == 0
        assert monitor._parse_lcm_progress("10/10") == 100
        assert monitor._parse_lcm_progress("5/20") == 25

    def test_parse_zero_total(self, mock_adapter: AsyncMock) -> None:
        """Test parsing with zero total returns default."""
        monitor = OpenStackUpgradeMonitor(mock_adapter, "mos")

        result = monitor._parse_lcm_progress("0/0")
        assert result == 50  # Default value

    def test_parse_invalid_format(self, mock_adapter: AsyncMock) -> None:
        """Test parsing invalid format returns default."""
        monitor = OpenStackUpgradeMonitor(mock_adapter, "mos")

        assert monitor._parse_lcm_progress("invalid") == 50
        assert monitor._parse_lcm_progress("") == 50
        assert monitor._parse_lcm_progress("abc/def") == 50


class TestOpenStackUpgradeMonitorDetermineState:
    """Tests for _determine_state method."""

    @pytest.fixture
    def mock_adapter(self) -> AsyncMock:
        """Create mock adapter."""
        return AsyncMock()

    def test_determine_state_osdpl_error(self, mock_adapter: AsyncMock) -> None:
        """Test state when OSDPL has error."""
        monitor = OpenStackUpgradeMonitor(mock_adapter, "mos")

        osdpl = {"error": "API connection failed"}
        osdplst: dict[str, Any] = {}

        monitor._determine_state(osdpl, osdplst)

        assert monitor._current_phase == UpgradePhase.FAILED
        assert monitor._error_message == "API connection failed"
        assert monitor._progress_percent == -1

    def test_determine_state_osdpl_not_found(self, mock_adapter: AsyncMock) -> None:
        """Test state when OSDPL not found."""
        monitor = OpenStackUpgradeMonitor(mock_adapter, "mos")

        osdpl: dict[str, Any] = {}
        osdplst: dict[str, Any] = {"status": {}}

        monitor._determine_state(osdpl, osdplst)

        assert monitor._current_phase == UpgradePhase.NOT_STARTED
        assert monitor._error_message == "OSDPL not found"
        assert monitor._progress_percent == 0

    def test_determine_state_osdplst_not_found(self, mock_adapter: AsyncMock) -> None:
        """Test state when OSDPLStatus not found."""
        monitor = OpenStackUpgradeMonitor(mock_adapter, "mos")

        osdpl = {"metadata": {"name": "mos"}}
        osdplst: dict[str, Any] = {}

        monitor._determine_state(osdpl, osdplst)

        assert monitor._current_phase == UpgradePhase.FAILED
        assert monitor._error_message == "OSDPLStatus not found"
        assert monitor._progress_percent == -1

    def test_determine_state_completed(self, mock_adapter: AsyncMock) -> None:
        """Test state when upgrade is completed."""
        monitor = OpenStackUpgradeMonitor(mock_adapter, "mos")

        osdpl = {
            "metadata": {"name": "mos"},
            "spec": {"openStackVersion": "caracal"},
            "status": {"openStackVersion": "caracal"},
        }
        osdplst = {
            "status": {
                "osdpl": {"state": "APPLIED", "openstackVersion": "caracal"},
                "services": {},
            }
        }

        monitor._determine_state(osdpl, osdplst)

        assert monitor._current_phase == UpgradePhase.COMPLETED
        assert monitor._is_upgrading is False
        assert monitor._progress_percent == 100

    def test_determine_state_failed(self, mock_adapter: AsyncMock) -> None:
        """Test state when upgrade has failed."""
        monitor = OpenStackUpgradeMonitor(mock_adapter, "mos")

        osdpl = {
            "metadata": {"name": "mos"},
            "spec": {"openStackVersion": "caracal"},
            "status": {"openStackVersion": "antelope"},
        }
        osdplst = {
            "status": {
                "osdpl": {"state": "FAILED"},
                "services": {},
            }
        }

        monitor._determine_state(osdpl, osdplst)

        assert monitor._current_phase == UpgradePhase.FAILED
        assert monitor._is_upgrading is False
        assert monitor._progress_percent == -1
        assert monitor._error_message == "Upgrade failed"

    def test_determine_state_upgrading_initializing(self, mock_adapter: AsyncMock) -> None:
        """Test state during early upgrade phase."""
        monitor = OpenStackUpgradeMonitor(mock_adapter, "mos")

        osdpl = {
            "metadata": {"name": "mos"},
            "spec": {"openStackVersion": "caracal"},
            "status": {"openStackVersion": "antelope"},
        }
        osdplst = {
            "status": {
                "osdpl": {"state": "APPLYING", "lcmProgress": "3/18"},
                "services": {},
            }
        }

        monitor._determine_state(osdpl, osdplst)

        assert monitor._current_phase == UpgradePhase.INITIALIZING
        assert monitor._is_upgrading is True
        assert monitor._progress_percent == 16  # 3/18 = ~16%

    def test_determine_state_upgrading_control_plane(self, mock_adapter: AsyncMock) -> None:
        """Test state during control plane upgrade phase."""
        monitor = OpenStackUpgradeMonitor(mock_adapter, "mos")

        osdpl = {
            "metadata": {"name": "mos"},
            "spec": {"openStackVersion": "caracal"},
            "status": {"openStackVersion": "antelope"},
        }
        osdplst = {
            "status": {
                "osdpl": {"state": "APPLYING", "lcmProgress": "6/18"},
                "services": {},
            }
        }

        monitor._determine_state(osdpl, osdplst)

        assert monitor._current_phase == UpgradePhase.UPGRADING_CONTROL_PLANE
        assert monitor._is_upgrading is True
        assert monitor._progress_percent == 33  # 6/18 = 33%

    def test_determine_state_upgrading_services(self, mock_adapter: AsyncMock) -> None:
        """Test state during services upgrade phase."""
        monitor = OpenStackUpgradeMonitor(mock_adapter, "mos")

        osdpl = {
            "metadata": {"name": "mos"},
            "spec": {"openStackVersion": "caracal"},
            "status": {"openStackVersion": "antelope"},
        }
        osdplst = {
            "status": {
                "osdpl": {"state": "APPLYING", "lcmProgress": "12/18"},
                "services": {},
            }
        }

        monitor._determine_state(osdpl, osdplst)

        assert monitor._current_phase == UpgradePhase.UPGRADING_SERVICES
        assert monitor._is_upgrading is True
        assert monitor._progress_percent == 66  # 12/18 = 66%

    def test_determine_state_upgrading_compute(self, mock_adapter: AsyncMock) -> None:
        """Test state during compute upgrade phase."""
        monitor = OpenStackUpgradeMonitor(mock_adapter, "mos")

        osdpl = {
            "metadata": {"name": "mos"},
            "spec": {"openStackVersion": "caracal"},
            "status": {"openStackVersion": "antelope"},
        }
        osdplst = {
            "status": {
                "osdpl": {"state": "APPLYING", "lcmProgress": "16/18"},
                "services": {},
            }
        }

        monitor._determine_state(osdpl, osdplst)

        assert monitor._current_phase == UpgradePhase.UPGRADING_COMPUTE
        assert monitor._is_upgrading is True
        assert monitor._progress_percent == 88  # 16/18 = 88%

    def test_determine_state_finalizing(self, mock_adapter: AsyncMock) -> None:
        """Test state during finalization phase."""
        monitor = OpenStackUpgradeMonitor(mock_adapter, "mos")

        osdpl = {
            "metadata": {"name": "mos"},
            "spec": {"openStackVersion": "caracal"},
            "status": {"openStackVersion": "antelope"},
        }
        osdplst = {
            "status": {
                "osdpl": {"state": "APPLYING", "lcmProgress": "17/18"},
                "services": {},
            }
        }

        monitor._determine_state(osdpl, osdplst)

        # 17/18 = 94%, which is < 95, so still UPGRADING_COMPUTE
        # Let's test with 18/18 still in APPLYING state
        osdplst["status"]["osdpl"]["lcmProgress"] = "18/18"
        monitor._determine_state(osdpl, osdplst)

        assert monitor._current_phase == UpgradePhase.FINALIZING
        assert monitor._progress_percent == 100

    def test_determine_state_version_mismatch_but_applied(self, mock_adapter: AsyncMock) -> None:
        """Test state when versions mismatch but state is APPLIED."""
        monitor = OpenStackUpgradeMonitor(mock_adapter, "mos")

        osdpl = {
            "metadata": {"name": "mos"},
            "spec": {"openStackVersion": "caracal"},
            "status": {"openStackVersion": "antelope"},
        }
        osdplst = {
            "status": {
                "osdpl": {"state": "APPLIED", "openstackVersion": "antelope"},
                "services": {},
            }
        }

        monitor._determine_state(osdpl, osdplst)

        # Version mismatch but APPLIED - unusual state
        assert monitor._current_phase == UpgradePhase.FINALIZING
        assert monitor._is_upgrading is True
        assert monitor._progress_percent == 95

    def test_determine_state_no_lcm_progress(self, mock_adapter: AsyncMock) -> None:
        """Test state when lcmProgress is not available."""
        monitor = OpenStackUpgradeMonitor(mock_adapter, "mos")

        osdpl = {
            "metadata": {"name": "mos"},
            "spec": {"openStackVersion": "caracal"},
            "status": {"openStackVersion": "antelope"},
        }
        osdplst = {
            "status": {
                "osdpl": {"state": "APPLYING"},  # No lcmProgress
                "services": {},
            }
        }

        monitor._determine_state(osdpl, osdplst)

        assert monitor._is_upgrading is True
        assert monitor._progress_percent == 50  # Default
        assert monitor._current_phase == UpgradePhase.UPGRADING_SERVICES

    def test_determine_state_unknown_state(self, mock_adapter: AsyncMock) -> None:
        """Test state with unknown OSDPLStatus state."""
        monitor = OpenStackUpgradeMonitor(mock_adapter, "mos")

        osdpl = {
            "metadata": {"name": "mos"},
            "spec": {"openStackVersion": "caracal"},
            "status": {"openStackVersion": "antelope"},
        }
        osdplst = {
            "status": {
                "osdpl": {"state": "UNKNOWN_STATE"},
                "services": {},
            }
        }

        monitor._determine_state(osdpl, osdplst)

        assert monitor._current_phase == UpgradePhase.NOT_STARTED
        assert monitor._is_upgrading is False
        assert monitor._progress_percent == 0


class TestOpenStackUpgradeMonitorGetProgress:
    """Tests for get_progress method."""

    @pytest.fixture
    def mock_adapter(self) -> AsyncMock:
        """Create mock adapter."""
        adapter = AsyncMock()
        return adapter

    @pytest.mark.asyncio
    async def test_get_progress_completed(self, mock_adapter: AsyncMock) -> None:
        """Test get_progress for completed upgrade."""
        mock_adapter.get_openstack_deployment = AsyncMock(
            return_value={
                "metadata": {"name": "mos"},
                "spec": {"openStackVersion": "caracal"},
                "status": {"openStackVersion": "caracal"},
            }
        )
        mock_adapter.get_openstack_deployment_status = AsyncMock(
            return_value={
                "metadata": {"name": "mos"},
                "status": {
                    "osdpl": {"state": "APPLIED", "openstackVersion": "caracal"},
                    "services": {},
                },
            }
        )

        monitor = OpenStackUpgradeMonitor(mock_adapter, "mos")
        snapshot = await monitor.get_progress()

        assert snapshot.progress_percent == 100
        assert snapshot.phase == UpgradePhase.COMPLETED
        assert monitor.is_complete() is True

    @pytest.mark.asyncio
    async def test_get_progress_upgrading(self, mock_adapter: AsyncMock) -> None:
        """Test get_progress during upgrade."""
        mock_adapter.get_openstack_deployment = AsyncMock(
            return_value={
                "metadata": {"name": "mos"},
                "spec": {"openStackVersion": "caracal"},
                "status": {"openStackVersion": "antelope"},
            }
        )
        mock_adapter.get_openstack_deployment_status = AsyncMock(
            return_value={
                "metadata": {"name": "mos"},
                "status": {
                    "osdpl": {"state": "APPLYING", "lcmProgress": "10/18"},
                    "services": {
                        "keystone": {"state": "APPLIED"},
                        "nova": {"state": "APPLYING"},
                        "neutron": {"state": "WAITING"},
                    },
                },
            }
        )

        monitor = OpenStackUpgradeMonitor(mock_adapter, "mos")
        snapshot = await monitor.get_progress()

        assert snapshot.progress_percent == 55  # 10/18
        assert snapshot.details["is_upgrading"] is True
        assert snapshot.details["services_completed"] == 1
        assert snapshot.details["services_total"] == 3

    @pytest.mark.asyncio
    async def test_get_progress_with_version_info(self, mock_adapter: AsyncMock) -> None:
        """Test get_progress includes version info in message."""
        mock_adapter.get_openstack_deployment = AsyncMock(
            return_value={
                "metadata": {"name": "mos"},
                "spec": {"openStackVersion": "caracal"},
                "status": {"openStackVersion": "antelope"},
            }
        )
        mock_adapter.get_openstack_deployment_status = AsyncMock(
            return_value={
                "metadata": {"name": "mos"},
                "status": {
                    "osdpl": {"state": "APPLYING", "lcmProgress": "5/18"},
                    "services": {},
                },
            }
        )

        monitor = OpenStackUpgradeMonitor(mock_adapter, "mos")
        snapshot = await monitor.get_progress()

        assert "antelope" in snapshot.message
        assert "caracal" in snapshot.message

    @pytest.mark.asyncio
    async def test_get_progress_failed(self, mock_adapter: AsyncMock) -> None:
        """Test get_progress for failed upgrade."""
        mock_adapter.get_openstack_deployment = AsyncMock(
            return_value={
                "metadata": {"name": "mos"},
                "spec": {"openStackVersion": "caracal"},
                "status": {"openStackVersion": "antelope"},
            }
        )
        mock_adapter.get_openstack_deployment_status = AsyncMock(
            return_value={
                "metadata": {"name": "mos"},
                "status": {
                    "osdpl": {"state": "FAILED"},
                    "services": {},
                },
            }
        )

        monitor = OpenStackUpgradeMonitor(mock_adapter, "mos")
        snapshot = await monitor.get_progress()

        assert snapshot.progress_percent == -1
        assert snapshot.phase == UpgradePhase.FAILED
        assert monitor.has_failed() is True

    @pytest.mark.asyncio
    async def test_get_progress_services_in_progress(self, mock_adapter: AsyncMock) -> None:
        """Test get_progress lists services in progress."""
        mock_adapter.get_openstack_deployment = AsyncMock(
            return_value={
                "metadata": {"name": "mos"},
                "spec": {"openStackVersion": "caracal"},
                "status": {"openStackVersion": "antelope"},
            }
        )
        mock_adapter.get_openstack_deployment_status = AsyncMock(
            return_value={
                "metadata": {"name": "mos"},
                "status": {
                    "osdpl": {"state": "APPLYING", "lcmProgress": "8/18"},
                    "services": {
                        "keystone": {"state": "APPLIED"},
                        "nova": {"state": "APPLYING"},
                        "neutron": {"state": "WAITING"},
                        "glance": {"state": "APPLIED"},
                    },
                },
            }
        )

        monitor = OpenStackUpgradeMonitor(mock_adapter, "mos")
        snapshot = await monitor.get_progress()

        assert "services_in_progress" in snapshot.details
        in_progress = snapshot.details["services_in_progress"]
        assert "nova" in in_progress
        assert "neutron" in in_progress
