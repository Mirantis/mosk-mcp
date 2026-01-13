"""Unit tests for list_maintenance_requests tool."""

from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from mosk_mcp.core.exceptions import ToolExecutionError
from mosk_mcp.tools.operations_visibility.list_maintenance_requests import (
    _parse_maintenance_phase,
    _parse_maintenance_request,
    list_maintenance_requests,
)
from mosk_mcp.tools.operations_visibility.models import (
    ListMaintenanceRequestsInput,
    MaintenancePhase,
)


class TestParseMaintenancePhase:
    """Tests for _parse_maintenance_phase helper."""

    def test_parse_pending(self):
        """Test parsing Pending phase."""
        result = _parse_maintenance_phase("Pending")
        assert result == MaintenancePhase.PENDING

    def test_parse_draining(self):
        """Test parsing Draining phase."""
        result = _parse_maintenance_phase("Draining")
        assert result == MaintenancePhase.DRAINING

    def test_parse_drained(self):
        """Test parsing Drained phase."""
        result = _parse_maintenance_phase("Drained")
        assert result == MaintenancePhase.DRAINED

    def test_parse_maintaining(self):
        """Test parsing Maintaining phase."""
        result = _parse_maintenance_phase("Maintaining")
        assert result == MaintenancePhase.MAINTAINING

    def test_parse_uncordoning(self):
        """Test parsing Uncordoning phase."""
        result = _parse_maintenance_phase("Uncordoning")
        assert result == MaintenancePhase.UNCORDONING

    def test_parse_completed(self):
        """Test parsing Completed phase."""
        result = _parse_maintenance_phase("Completed")
        assert result == MaintenancePhase.COMPLETED

    def test_parse_failed(self):
        """Test parsing Failed phase."""
        result = _parse_maintenance_phase("Failed")
        assert result == MaintenancePhase.FAILED

    def test_parse_cancelled(self):
        """Test parsing Cancelled phase."""
        result = _parse_maintenance_phase("Cancelled")
        assert result == MaintenancePhase.CANCELLED

    def test_parse_unknown_defaults_to_pending(self):
        """Test unknown phase defaults to Pending."""
        result = _parse_maintenance_phase("UnknownPhase")
        assert result == MaintenancePhase.PENDING


class TestParseMaintenanceRequest:
    """Tests for _parse_maintenance_request helper."""

    def test_parse_complete_request(self):
        """Test parsing a complete maintenance request."""
        request = {
            "metadata": {
                "name": "maintenance-compute-01",
                "namespace": "default",
                "creationTimestamp": "2024-01-01T12:00:00Z",
            },
            "spec": {
                "nodeName": "compute-01",
                "reason": "hardware-repair",
                "description": "Replace faulty disk",
                "drainStrategy": "Graceful",
                "crqNumber": "CRQ123456789",
            },
            "status": {
                "phase": "Draining",
                "startedAt": "2024-01-01T12:05:00Z",
                "totalEvicted": 5,
            },
        }

        result = _parse_maintenance_request(request)

        assert result.name == "maintenance-compute-01"
        assert result.namespace == "default"
        assert result.node_name == "compute-01"
        assert result.phase == MaintenancePhase.DRAINING
        assert result.reason == "hardware-repair"
        assert result.description == "Replace faulty disk"
        assert result.drain_strategy == "Graceful"
        assert result.crq_number == "CRQ123456789"
        assert result.started_at == "2024-01-01T12:05:00Z"
        assert result.pods_evicted == 5
        assert result.is_complete is False
        assert result.is_successful is False

    def test_parse_completed_request(self):
        """Test parsing a completed maintenance request."""
        request = {
            "metadata": {
                "name": "maintenance-compute-02",
                "namespace": "default",
                "creationTimestamp": "2024-01-01T10:00:00Z",
            },
            "spec": {
                "nodeName": "compute-02",
                "reason": "os-upgrade",
            },
            "status": {
                "phase": "Completed",
                "startedAt": "2024-01-01T10:05:00Z",
                "completedAt": "2024-01-01T11:00:00Z",
                "totalEvicted": 10,
            },
        }

        result = _parse_maintenance_request(request)

        assert result.phase == MaintenancePhase.COMPLETED
        assert result.is_complete is True
        assert result.is_successful is True
        assert result.completed_at == "2024-01-01T11:00:00Z"

    def test_parse_failed_request(self):
        """Test parsing a failed maintenance request."""
        request = {
            "metadata": {
                "name": "maintenance-compute-03",
                "namespace": "default",
                "creationTimestamp": "2024-01-01T09:00:00Z",
            },
            "spec": {
                "nodeName": "compute-03",
                "reason": "firmware-update",
            },
            "status": {
                "phase": "Failed",
                "errorMessage": "Pod eviction timed out",
            },
        }

        result = _parse_maintenance_request(request)

        assert result.phase == MaintenancePhase.FAILED
        assert result.is_complete is True
        assert result.is_successful is False
        assert result.error_message == "Pod eviction timed out"

    def test_parse_cancelled_request(self):
        """Test parsing a cancelled maintenance request."""
        request = {
            "metadata": {"name": "maintenance-compute-04"},
            "spec": {"nodeName": "compute-04"},
            "status": {"phase": "Cancelled"},
        }

        result = _parse_maintenance_request(request)

        assert result.phase == MaintenancePhase.CANCELLED
        assert result.is_complete is True
        assert result.is_successful is False

    def test_parse_minimal_request(self):
        """Test parsing request with minimal data."""
        request = {}

        result = _parse_maintenance_request(request)

        assert result.name == "unknown"
        assert result.namespace == "default"
        assert result.node_name == "unknown"
        assert result.phase == MaintenancePhase.PENDING
        assert result.reason == "Unknown"
        assert result.drain_strategy == "Graceful"


class TestListMaintenanceRequestsInput:
    """Tests for ListMaintenanceRequestsInput model."""

    def test_default_values(self):
        """Test default values."""
        input_data = ListMaintenanceRequestsInput()

        assert input_data.namespace == "default"
        assert input_data.node_filter is None
        assert input_data.phase_filter is None
        assert input_data.include_completed is False
        assert input_data.limit == 50

    def test_custom_values(self):
        """Test custom values."""
        input_data = ListMaintenanceRequestsInput(
            namespace="custom",
            node_filter="compute-01",
            phase_filter=MaintenancePhase.DRAINING,
            include_completed=True,
            limit=100,
        )

        assert input_data.namespace == "custom"
        assert input_data.node_filter == "compute-01"
        assert input_data.phase_filter == MaintenancePhase.DRAINING
        assert input_data.include_completed is True
        assert input_data.limit == 100

    def test_limit_bounds(self):
        """Test limit bounds validation."""
        # Valid limits
        input_data = ListMaintenanceRequestsInput(limit=1)
        assert input_data.limit == 1

        input_data = ListMaintenanceRequestsInput(limit=200)
        assert input_data.limit == 200

        # Invalid limits
        with pytest.raises(ValueError):
            ListMaintenanceRequestsInput(limit=0)

        with pytest.raises(ValueError):
            ListMaintenanceRequestsInput(limit=201)


class TestListMaintenanceRequestsFunction:
    """Tests for list_maintenance_requests function."""

    @pytest.fixture
    def mock_k8s_adapter(self):
        """Create mock Kubernetes adapter."""
        adapter = AsyncMock()
        adapter.connect = AsyncMock()
        return adapter

    @pytest.fixture
    def mock_requests(self):
        """Create mock maintenance requests."""
        return [
            {
                "metadata": {
                    "name": "maintenance-compute-01",
                    "namespace": "default",
                    "creationTimestamp": "2024-01-01T12:00:00Z",
                },
                "spec": {
                    "nodeName": "compute-01",
                    "reason": "hardware-repair",
                },
                "status": {
                    "phase": "Draining",
                    "totalEvicted": 3,
                },
            },
            {
                "metadata": {
                    "name": "maintenance-compute-02",
                    "namespace": "default",
                    "creationTimestamp": "2024-01-01T11:00:00Z",
                },
                "spec": {
                    "nodeName": "compute-02",
                    "reason": "os-upgrade",
                },
                "status": {
                    "phase": "Completed",
                    "totalEvicted": 10,
                },
            },
            {
                "metadata": {
                    "name": "maintenance-compute-03",
                    "namespace": "default",
                    "creationTimestamp": "2024-01-01T10:00:00Z",
                },
                "spec": {
                    "nodeName": "compute-03",
                    "reason": "firmware-update",
                },
                "status": {
                    "phase": "Pending",
                },
            },
        ]

    @pytest.mark.asyncio
    async def test_list_all_requests(self, mock_k8s_adapter, mock_requests):
        """Test listing all maintenance requests."""
        mock_k8s_adapter.list_maintenance_requests = AsyncMock(return_value=mock_requests)

        result = await list_maintenance_requests(
            mock_k8s_adapter,
            ListMaintenanceRequestsInput(include_completed=True),
        )

        assert result.total_count == 3
        assert result.active_count == 1  # Draining
        assert result.pending_count == 1
        assert result.completed_count == 1
        assert len(result.requests) == 3

    @pytest.mark.asyncio
    async def test_exclude_completed(self, mock_k8s_adapter, mock_requests):
        """Test excluding completed requests."""
        mock_k8s_adapter.list_maintenance_requests = AsyncMock(return_value=mock_requests)

        result = await list_maintenance_requests(
            mock_k8s_adapter,
            ListMaintenanceRequestsInput(include_completed=False),
        )

        # Should exclude the Completed request
        assert result.total_count == 2
        assert all(r.phase != MaintenancePhase.COMPLETED for r in result.requests)

    @pytest.mark.asyncio
    async def test_filter_by_node(self, mock_k8s_adapter, mock_requests):
        """Test filtering by node name."""
        mock_k8s_adapter.list_maintenance_requests = AsyncMock(return_value=mock_requests)

        result = await list_maintenance_requests(
            mock_k8s_adapter,
            ListMaintenanceRequestsInput(
                node_filter="compute-01",
                include_completed=True,
            ),
        )

        assert result.total_count == 1
        assert result.requests[0].node_name == "compute-01"

    @pytest.mark.asyncio
    async def test_filter_by_phase(self, mock_k8s_adapter, mock_requests):
        """Test filtering by phase."""
        mock_k8s_adapter.list_maintenance_requests = AsyncMock(return_value=mock_requests)

        result = await list_maintenance_requests(
            mock_k8s_adapter,
            ListMaintenanceRequestsInput(
                phase_filter=MaintenancePhase.PENDING,
                include_completed=True,
            ),
        )

        assert result.total_count == 1
        assert result.requests[0].phase == MaintenancePhase.PENDING

    @pytest.mark.asyncio
    async def test_apply_limit(self, mock_k8s_adapter, mock_requests):
        """Test applying limit."""
        mock_k8s_adapter.list_maintenance_requests = AsyncMock(return_value=mock_requests)

        result = await list_maintenance_requests(
            mock_k8s_adapter,
            ListMaintenanceRequestsInput(include_completed=True, limit=2),
        )

        assert len(result.requests) == 2

    @pytest.mark.asyncio
    async def test_empty_result(self, mock_k8s_adapter):
        """Test empty result."""
        mock_k8s_adapter.list_maintenance_requests = AsyncMock(return_value=[])

        result = await list_maintenance_requests(
            mock_k8s_adapter,
            ListMaintenanceRequestsInput(),
        )

        assert result.total_count == 0
        assert result.active_count == 0
        assert result.requests == []

    @pytest.mark.asyncio
    async def test_api_error(self, mock_k8s_adapter):
        """Test API error handling."""
        mock_k8s_adapter.list_maintenance_requests = AsyncMock(
            side_effect=Exception("Connection failed")
        )

        with pytest.raises(ToolExecutionError) as exc_info:
            await list_maintenance_requests(
                mock_k8s_adapter,
                ListMaintenanceRequestsInput(),
            )

        assert "Failed to list maintenance requests" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_statistics_calculation(self, mock_k8s_adapter):
        """Test statistics calculation."""
        requests = [
            {
                "metadata": {"name": "req1"},
                "spec": {"nodeName": "node-01"},
                "status": {"phase": "Draining"},
            },
            {
                "metadata": {"name": "req2"},
                "spec": {"nodeName": "node-01"},
                "status": {"phase": "Maintaining"},
            },
            {
                "metadata": {"name": "req3"},
                "spec": {"nodeName": "node-02"},
                "status": {"phase": "Pending"},
            },
            {
                "metadata": {"name": "req4"},
                "spec": {"nodeName": "node-03"},
                "status": {"phase": "Failed"},
            },
        ]
        mock_k8s_adapter.list_maintenance_requests = AsyncMock(return_value=requests)

        result = await list_maintenance_requests(
            mock_k8s_adapter,
            ListMaintenanceRequestsInput(include_completed=True),
        )

        assert result.total_count == 4
        assert result.active_count == 2  # Draining + Maintaining
        assert result.pending_count == 1
        assert result.failed_count == 1
        assert len(result.nodes_in_maintenance) == 1  # node-01 (has active requests)
        assert "node-01" in result.nodes_in_maintenance

        # Check by_phase
        assert result.by_phase["Draining"] == 1
        assert result.by_phase["Maintaining"] == 1
        assert result.by_phase["Pending"] == 1
        assert result.by_phase["Failed"] == 1

        # Check by_node
        assert result.by_node["node-01"] == 2
        assert result.by_node["node-02"] == 1
        assert result.by_node["node-03"] == 1

    @pytest.mark.asyncio
    async def test_timestamp_set(self, mock_k8s_adapter):
        """Test timestamp is set in result."""
        mock_k8s_adapter.list_maintenance_requests = AsyncMock(return_value=[])

        result = await list_maintenance_requests(
            mock_k8s_adapter,
            ListMaintenanceRequestsInput(),
        )

        assert result.timestamp is not None
        # Verify valid ISO format
        datetime.fromisoformat(result.timestamp.replace("Z", "+00:00"))
