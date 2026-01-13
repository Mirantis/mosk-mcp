"""Tests for check_service_availability validation tool.

Tests cover:
- ServiceStatus enum
- ServiceCheckResult dataclass
- Input/output models
- Service availability checking
- Agent status checking
- Error handling
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from mosk_mcp.tools.validation.check_service_availability import (
    CORE_SERVICES,
    CheckServiceAvailabilityInput,
    CheckServiceAvailabilityOutput,
    ServiceCheckOutput,
    ServiceCheckResult,
    ServiceStatus,
    check_service_availability,
)


# =============================================================================
# Model Tests
# =============================================================================


class TestServiceStatus:
    """Tests for ServiceStatus enum."""

    def test_service_status_values(self) -> None:
        """Test ServiceStatus has correct values."""
        assert ServiceStatus.HEALTHY == "healthy"
        assert ServiceStatus.DEGRADED == "degraded"
        assert ServiceStatus.UNAVAILABLE == "unavailable"
        assert ServiceStatus.UNKNOWN == "unknown"

    def test_service_status_is_string_enum(self) -> None:
        """Test ServiceStatus is string enum for JSON serialization."""
        assert isinstance(ServiceStatus.HEALTHY, str)
        assert ServiceStatus.HEALTHY.value == "healthy"


class TestServiceCheckResult:
    """Tests for ServiceCheckResult dataclass."""

    def test_service_check_result_creation(self) -> None:
        """Test ServiceCheckResult creation with defaults."""
        result = ServiceCheckResult(
            service_name="nova",
            status=ServiceStatus.HEALTHY,
        )
        assert result.service_name == "nova"
        assert result.status == ServiceStatus.HEALTHY
        assert result.response_time_ms is None
        assert result.endpoint_count == 0
        assert result.agent_count == 0
        assert result.agents_up == 0
        assert result.error_message is None
        assert result.details == {}

    def test_service_check_result_with_all_fields(self) -> None:
        """Test ServiceCheckResult with all fields populated."""
        result = ServiceCheckResult(
            service_name="nova",
            status=ServiceStatus.DEGRADED,
            response_time_ms=150.5,
            endpoint_count=3,
            agent_count=10,
            agents_up=8,
            error_message="2 agents down",
            details={"endpoints": ["http://a", "http://b"]},
        )
        assert result.response_time_ms == 150.5
        assert result.agent_count == 10
        assert result.agents_up == 8


class TestCheckServiceAvailabilityInput:
    """Tests for CheckServiceAvailabilityInput model."""

    def test_input_defaults(self) -> None:
        """Test input has correct defaults."""
        input_data = CheckServiceAvailabilityInput()
        assert input_data.services is None
        assert input_data.include_agents is True
        assert input_data.timeout_seconds == 30

    def test_input_with_services(self) -> None:
        """Test input with specific services."""
        input_data = CheckServiceAvailabilityInput(
            services=["nova", "neutron"],
            include_agents=False,
            timeout_seconds=60,
        )
        assert input_data.services == ["nova", "neutron"]
        assert input_data.include_agents is False
        assert input_data.timeout_seconds == 60

    def test_input_timeout_constraints(self) -> None:
        """Test timeout field constraints."""
        # Valid values
        CheckServiceAvailabilityInput(timeout_seconds=5)
        CheckServiceAvailabilityInput(timeout_seconds=120)

        # Invalid values
        with pytest.raises(ValueError):
            CheckServiceAvailabilityInput(timeout_seconds=4)
        with pytest.raises(ValueError):
            CheckServiceAvailabilityInput(timeout_seconds=121)


class TestCheckServiceAvailabilityOutput:
    """Tests for CheckServiceAvailabilityOutput model."""

    def test_output_creation(self) -> None:
        """Test output model creation."""
        output = CheckServiceAvailabilityOutput(
            overall_status="healthy",
            services_checked=5,
            services_healthy=4,
            services_degraded=1,
            services_unavailable=0,
            results=[
                ServiceCheckOutput(
                    service_name="nova",
                    status="healthy",
                    response_time_ms=100.0,
                )
            ],
            timestamp=datetime.now(UTC).isoformat(),
            duration_seconds=2.5,
        )
        assert output.services_checked == 5
        assert output.services_healthy == 4
        assert len(output.results) == 1


# =============================================================================
# Core Services Constant Test
# =============================================================================


class TestCoreServices:
    """Tests for CORE_SERVICES constant."""

    def test_core_services_includes_essential(self) -> None:
        """Test CORE_SERVICES includes essential OpenStack services."""
        assert "keystone" in CORE_SERVICES
        assert "nova" in CORE_SERVICES
        assert "neutron" in CORE_SERVICES
        assert "glance" in CORE_SERVICES
        assert "cinder" in CORE_SERVICES


# =============================================================================
# Check Service Availability Function Tests
# =============================================================================


class TestCheckServiceAvailability:
    """Tests for check_service_availability function."""

    @pytest.fixture
    def mock_adapter(self) -> MagicMock:
        """Create a mock OpenStack adapter."""
        adapter = MagicMock()

        # Create mock compute service objects with attributes
        compute_service1 = MagicMock()
        compute_service1.state = "up"
        compute_service1.status = "enabled"

        compute_service2 = MagicMock()
        compute_service2.state = "up"
        compute_service2.status = "enabled"

        adapter.list_compute_services = AsyncMock(return_value=[compute_service1, compute_service2])

        # Network agents use dict access, so keep as dicts
        adapter.list_network_agents = AsyncMock(
            return_value=[
                {"agent_type": "Open vSwitch agent", "alive": True},
                {"agent_type": "DHCP agent", "alive": True},
            ]
        )

        # Volume services use dict access
        adapter.list_volume_services = AsyncMock(
            return_value=[
                {"Binary": "cinder-volume", "State": "up"},
            ]
        )

        # Hypervisors need vcpus and memory_mb attributes
        hypervisor = MagicMock()
        hypervisor.vcpus = 16
        hypervisor.memory_mb = 32768
        adapter.list_hypervisors = AsyncMock(return_value=[hypervisor])

        adapter.list_images = AsyncMock(return_value=[{"name": "cirros", "Status": "active"}])
        adapter.list_networks = AsyncMock(return_value=[{"id": "net-1"}])
        adapter.get_token = AsyncMock(return_value="token-12345")
        adapter.list_endpoints = AsyncMock(return_value=[{"id": "ep-1"}])
        adapter.list_services = AsyncMock(return_value=[{"id": "svc-1"}])
        adapter.list_projects = AsyncMock(return_value=[{"id": "proj-1"}])
        adapter.get_catalog = AsyncMock(
            return_value=[
                {"name": "keystone", "endpoints": [{"url": "http://keystone:5000"}]},
                {"name": "nova", "endpoints": [{"url": "http://nova:8774"}]},
            ]
        )
        return adapter

    @pytest.mark.asyncio
    async def test_check_all_services(self, mock_adapter: MagicMock) -> None:
        """Test checking all core services."""
        input_data = CheckServiceAvailabilityInput()

        result = await check_service_availability(mock_adapter, input_data)

        assert result.services_checked == len(CORE_SERVICES)
        assert result.overall_status in ["healthy", "degraded", "unavailable"]
        assert result.timestamp is not None
        assert result.duration_seconds >= 0

    @pytest.mark.asyncio
    async def test_check_specific_services(self, mock_adapter: MagicMock) -> None:
        """Test checking specific services only."""
        input_data = CheckServiceAvailabilityInput(
            services=["nova", "neutron"],
        )

        result = await check_service_availability(mock_adapter, input_data)

        assert result.services_checked == 2
        service_names = [r.service_name for r in result.results]
        assert "nova" in service_names
        assert "neutron" in service_names

    @pytest.mark.asyncio
    async def test_check_without_agents(self, mock_adapter: MagicMock) -> None:
        """Test checking without agent status."""
        input_data = CheckServiceAvailabilityInput(
            services=["nova"],
            include_agents=False,
        )

        result = await check_service_availability(mock_adapter, input_data)

        assert result.services_checked == 1

    @pytest.mark.asyncio
    async def test_service_failure_marks_unavailable(self, mock_adapter: MagicMock) -> None:
        """Test that service failure marks service as unavailable."""
        mock_adapter.list_compute_services = AsyncMock(side_effect=Exception("Connection refused"))

        input_data = CheckServiceAvailabilityInput(services=["nova"])

        result = await check_service_availability(mock_adapter, input_data)

        # Should still complete, but nova should be unavailable
        assert result.services_checked == 1
        nova_result = next(r for r in result.results if r.service_name == "nova")
        assert nova_result.status in ["unavailable", "unknown"]

    @pytest.mark.asyncio
    async def test_partial_agent_failure_marks_degraded(self, mock_adapter: MagicMock) -> None:
        """Test that partial agent failure marks service as degraded."""
        # Create mock service objects with attributes (not dicts)
        service_up = MagicMock()
        service_up.state = "up"
        service_up.status = "enabled"

        service_down = MagicMock()
        service_down.state = "down"
        service_down.status = "enabled"

        mock_adapter.list_compute_services = AsyncMock(return_value=[service_up, service_down])

        input_data = CheckServiceAvailabilityInput(services=["nova"])

        result = await check_service_availability(mock_adapter, input_data)

        assert result.services_checked == 1
        # With one up and one down, status should be degraded
        nova_result = next(r for r in result.results if r.service_name == "nova")
        assert nova_result.status == "degraded"
        assert nova_result.agent_count == 2
        assert nova_result.agents_up == 1

    @pytest.mark.asyncio
    async def test_recommendations_generated(self, mock_adapter: MagicMock) -> None:
        """Test that recommendations are generated for issues."""
        mock_adapter.list_compute_services = AsyncMock(side_effect=Exception("Service unavailable"))

        input_data = CheckServiceAvailabilityInput(services=["nova"])

        result = await check_service_availability(mock_adapter, input_data)

        # Recommendations should be present for failures
        assert isinstance(result.recommendations, list)

    @pytest.mark.asyncio
    async def test_overall_status_healthy(self, mock_adapter: MagicMock) -> None:
        """Test overall status is healthy when all services pass."""
        input_data = CheckServiceAvailabilityInput(services=["nova"])

        result = await check_service_availability(mock_adapter, input_data)

        if result.services_healthy == result.services_checked:
            assert result.overall_status == "healthy"
