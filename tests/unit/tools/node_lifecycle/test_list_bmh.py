"""Unit tests for list_bmh tool."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from mosk_mcp.tools.node_lifecycle.list_bmh import (
    BMHOperationalStatusFilter,
    BMHStateFilter,
    BMHSummary,
    ListBMHInput,
    ListBMHOutput,
    _apply_filters,
    _extract_bmh_summary,
    _generate_summary,
    list_bmh,
)


# Sample BMH data for testing
SAMPLE_BMH_PROVISIONED = {
    "apiVersion": "metal3.io/v1alpha1",
    "kind": "BareMetalHost",
    "metadata": {
        "name": "node-01",
        "namespace": "default",
        "creationTimestamp": "2024-01-15T10:00:00Z",
    },
    "spec": {
        "bmc": {
            "address": "ipmi://192.168.1.100",
            "credentialsName": "node-01-bmc",
        },
        "consumerRef": {
            "name": "compute-01",
            "kind": "Machine",
        },
    },
    "status": {
        "provisioning": {
            "state": "provisioned",
        },
        "operationalStatus": "OK",
        "poweredOn": True,
        "hardware": {
            "systemVendor": {
                "manufacturer": "Dell Inc.",
                "productName": "PowerEdge R640",
            },
        },
    },
}


SAMPLE_BMH_INSPECTING = {
    "apiVersion": "metal3.io/v1alpha1",
    "kind": "BareMetalHost",
    "metadata": {
        "name": "node-02",
        "namespace": "default",
        "creationTimestamp": "2024-01-15T11:00:00Z",
    },
    "spec": {
        "bmc": {
            "address": "ipmi://192.168.1.101",
            "credentialsName": "node-02-bmc",
        },
    },
    "status": {
        "provisioning": {
            "state": "inspecting",
        },
        "operationalStatus": "discovered",
        "poweredOn": True,
        "hardware": {
            "systemVendor": {
                "manufacturer": "HP",
                "productName": "ProLiant DL360",
            },
        },
    },
}


SAMPLE_BMH_ERROR = {
    "apiVersion": "metal3.io/v1alpha1",
    "kind": "BareMetalHost",
    "metadata": {
        "name": "node-03",
        "namespace": "default",
        "creationTimestamp": "2024-01-15T09:00:00Z",
    },
    "spec": {
        "bmc": {
            "address": "ipmi://192.168.1.102",
        },
    },
    "status": {
        "provisioning": {
            "state": "error",
        },
        "operationalStatus": "error",
        "poweredOn": False,
        "errorMessage": "BMC connection failed: timeout",
    },
}


SAMPLE_BMH_AVAILABLE = {
    "apiVersion": "metal3.io/v1alpha1",
    "kind": "BareMetalHost",
    "metadata": {
        "name": "node-04",
        "namespace": "default",
        "creationTimestamp": "2024-01-15T08:00:00Z",
    },
    "spec": {
        "bmc": {
            "address": "redfish://192.168.1.103/redfish/v1",
        },
    },
    "status": {
        "provisioning": {
            "state": "available",
        },
        "operationalStatus": "OK",
        "poweredOn": True,
        "hardware": {
            "systemVendor": {
                "manufacturer": "Supermicro",
                "productName": "SYS-1029P",
            },
        },
    },
}


class TestExtractBMHSummary:
    """Tests for _extract_bmh_summary function."""

    def test_extract_provisioned_bmh(self):
        """Test extracting summary from a provisioned BMH."""
        summary = _extract_bmh_summary(SAMPLE_BMH_PROVISIONED)

        assert summary.name == "node-01"
        assert summary.namespace == "default"
        assert summary.state == "provisioned"
        assert summary.operational_status == "OK"
        assert summary.bmc_address == "ipmi://192.168.1.100"
        assert summary.online is True
        assert summary.consumer == "compute-01"
        assert summary.hardware_vendor == "Dell Inc."
        assert summary.hardware_model == "PowerEdge R640"
        assert summary.error_message is None

    def test_extract_inspecting_bmh(self):
        """Test extracting summary from an inspecting BMH."""
        summary = _extract_bmh_summary(SAMPLE_BMH_INSPECTING)

        assert summary.name == "node-02"
        assert summary.state == "inspecting"
        assert summary.operational_status == "discovered"
        assert summary.consumer is None
        assert summary.hardware_vendor == "HP"

    def test_extract_error_bmh(self):
        """Test extracting summary from a failed BMH."""
        summary = _extract_bmh_summary(SAMPLE_BMH_ERROR)

        assert summary.name == "node-03"
        assert summary.state == "error"
        assert summary.operational_status == "error"
        assert summary.online is False
        assert summary.error_message == "BMC connection failed: timeout"

    def test_extract_available_bmh(self):
        """Test extracting summary from an available BMH."""
        summary = _extract_bmh_summary(SAMPLE_BMH_AVAILABLE)

        assert summary.name == "node-04"
        assert summary.state == "available"
        assert summary.operational_status == "OK"
        assert summary.bmc_address == "redfish://192.168.1.103/redfish/v1"
        assert summary.hardware_vendor == "Supermicro"

    def test_extract_minimal_bmh(self):
        """Test extracting summary from minimal BMH data."""
        minimal_bmh = {
            "metadata": {"name": "minimal-node"},
            "status": {},
        }
        summary = _extract_bmh_summary(minimal_bmh)

        assert summary.name == "minimal-node"
        assert summary.state == "unknown"
        assert summary.operational_status == "unknown"


class TestApplyFilters:
    """Tests for _apply_filters function."""

    @pytest.fixture
    def sample_bmh_list(self) -> list[BMHSummary]:
        """Create sample BMH summaries for testing."""
        return [
            BMHSummary(
                name="node-01",
                namespace="default",
                state="provisioned",
                operational_status="OK",
                online=True,
            ),
            BMHSummary(
                name="node-02",
                namespace="default",
                state="inspecting",
                operational_status="discovered",
                online=True,
            ),
            BMHSummary(
                name="node-03",
                namespace="default",
                state="error",
                operational_status="error",
                online=False,
                error_message="BMC failed",
            ),
            BMHSummary(
                name="node-04",
                namespace="default",
                state="available",
                operational_status="OK",
                online=True,
            ),
            BMHSummary(
                name="node-05",
                namespace="default",
                state="provisioning",
                operational_status="OK",
                online=True,
            ),
        ]

    def test_no_filter(self, sample_bmh_list):
        """Test with no filters applied."""
        result = _apply_filters(
            sample_bmh_list,
            BMHStateFilter.ALL,
            BMHOperationalStatusFilter.ALL,
        )

        assert len(result) == 5

    def test_state_filter_provisioned(self, sample_bmh_list):
        """Test filtering by provisioned state."""
        result = _apply_filters(
            sample_bmh_list,
            BMHStateFilter.PROVISIONED,
            BMHOperationalStatusFilter.ALL,
        )

        assert len(result) == 1
        assert result[0].name == "node-01"

    def test_state_filter_available(self, sample_bmh_list):
        """Test filtering by available state."""
        result = _apply_filters(
            sample_bmh_list,
            BMHStateFilter.AVAILABLE,
            BMHOperationalStatusFilter.ALL,
        )

        assert len(result) == 1
        assert result[0].name == "node-04"

    def test_state_filter_error(self, sample_bmh_list):
        """Test filtering by error state."""
        result = _apply_filters(
            sample_bmh_list,
            BMHStateFilter.ERROR,
            BMHOperationalStatusFilter.ALL,
        )

        assert len(result) == 1
        assert result[0].name == "node-03"

    def test_status_filter_ok(self, sample_bmh_list):
        """Test filtering by OK operational status."""
        result = _apply_filters(
            sample_bmh_list,
            BMHStateFilter.ALL,
            BMHOperationalStatusFilter.OK,
        )

        assert len(result) == 3
        assert all(b.operational_status == "OK" for b in result)

    def test_status_filter_error(self, sample_bmh_list):
        """Test filtering by error operational status."""
        result = _apply_filters(
            sample_bmh_list,
            BMHStateFilter.ALL,
            BMHOperationalStatusFilter.ERROR,
        )

        assert len(result) == 1
        assert result[0].name == "node-03"

    def test_combined_filters(self, sample_bmh_list):
        """Test combining state and status filters."""
        result = _apply_filters(
            sample_bmh_list,
            BMHStateFilter.PROVISIONED,
            BMHOperationalStatusFilter.OK,
        )

        assert len(result) == 1
        assert result[0].name == "node-01"


class TestGenerateSummary:
    """Tests for _generate_summary function."""

    def test_generate_summary(self):
        """Test generating summary statistics."""
        bmh_list = [
            BMHSummary(
                name="node-01",
                namespace="default",
                state="provisioned",
                operational_status="OK",
                online=True,
            ),
            BMHSummary(
                name="node-02",
                namespace="default",
                state="provisioned",
                operational_status="OK",
                online=True,
            ),
            BMHSummary(
                name="node-03",
                namespace="default",
                state="inspecting",
                operational_status="discovered",
                online=True,
            ),
            BMHSummary(
                name="node-04",
                namespace="default",
                state="error",
                operational_status="error",
                online=False,
                error_message="Failed",
            ),
        ]

        summary = _generate_summary(bmh_list)

        assert summary.by_state["provisioned"] == 2
        assert summary.by_state["inspecting"] == 1
        assert summary.by_state["error"] == 1
        assert summary.by_operational_status["OK"] == 2
        assert summary.by_operational_status["discovered"] == 1
        assert summary.by_operational_status["error"] == 1
        assert summary.online_count == 3
        assert summary.error_count == 1
        assert summary.provisioned_count == 2
        assert summary.in_progress_count == 1  # inspecting

    def test_generate_summary_empty_list(self):
        """Test generating summary for empty list."""
        summary = _generate_summary([])

        assert summary.by_state == {}
        assert summary.by_operational_status == {}
        assert summary.online_count == 0
        assert summary.error_count == 0


class TestListBMH:
    """Tests for list_bmh function."""

    @pytest.fixture
    def mock_k8s_adapter(self):
        """Create a mock Kubernetes adapter."""
        adapter = MagicMock()
        adapter.list_custom_resources = AsyncMock(
            return_value=[
                SAMPLE_BMH_PROVISIONED,
                SAMPLE_BMH_INSPECTING,
                SAMPLE_BMH_ERROR,
                SAMPLE_BMH_AVAILABLE,
            ]
        )
        return adapter

    @pytest.mark.asyncio
    async def test_list_bmh_default(self, mock_k8s_adapter):
        """Test listing BMH with default parameters."""
        input_data = ListBMHInput()

        result = await list_bmh(mock_k8s_adapter, input_data)

        assert isinstance(result, ListBMHOutput)
        assert result.total_count == 4
        assert result.filtered_count == 4
        assert result.namespace == "default"
        assert len(result.bmh_list) == 4

    @pytest.mark.asyncio
    async def test_list_bmh_with_state_filter(self, mock_k8s_adapter):
        """Test listing BMH with state filter."""
        input_data = ListBMHInput(state_filter=BMHStateFilter.PROVISIONED)

        result = await list_bmh(mock_k8s_adapter, input_data)

        assert result.total_count == 4
        assert result.filtered_count == 1
        assert result.bmh_list[0].state == "provisioned"

    @pytest.mark.asyncio
    async def test_list_bmh_with_status_filter(self, mock_k8s_adapter):
        """Test listing BMH with operational status filter."""
        input_data = ListBMHInput(status_filter=BMHOperationalStatusFilter.OK)

        result = await list_bmh(mock_k8s_adapter, input_data)

        # SAMPLE_BMH_PROVISIONED and SAMPLE_BMH_AVAILABLE have OK status
        assert all(b.operational_status == "OK" for b in result.bmh_list)

    @pytest.mark.asyncio
    async def test_list_bmh_with_limit(self, mock_k8s_adapter):
        """Test listing BMH with limit."""
        input_data = ListBMHInput(limit=2)

        result = await list_bmh(mock_k8s_adapter, input_data)

        assert len(result.bmh_list) <= 2

    @pytest.mark.asyncio
    async def test_list_bmh_all_namespaces(self, mock_k8s_adapter):
        """Test listing BMH in all namespaces."""
        input_data = ListBMHInput(namespace="*")

        await list_bmh(mock_k8s_adapter, input_data)

        mock_k8s_adapter.list_custom_resources.assert_called_once()
        call_args = mock_k8s_adapter.list_custom_resources.call_args
        assert call_args.kwargs["namespace"] is None

    @pytest.mark.asyncio
    async def test_list_bmh_with_label_selector(self, mock_k8s_adapter):
        """Test listing BMH with label selector."""
        input_data = ListBMHInput(label_selector="env=prod")

        await list_bmh(mock_k8s_adapter, input_data)

        mock_k8s_adapter.list_custom_resources.assert_called_once()
        call_args = mock_k8s_adapter.list_custom_resources.call_args
        assert call_args.kwargs["label_selector"] == "env=prod"

    @pytest.mark.asyncio
    async def test_list_bmh_correct_api_group(self, mock_k8s_adapter):
        """Test that list_bmh uses correct API group."""
        input_data = ListBMHInput()

        await list_bmh(mock_k8s_adapter, input_data)

        mock_k8s_adapter.list_custom_resources.assert_called_once()
        call_args = mock_k8s_adapter.list_custom_resources.call_args
        assert call_args.kwargs["group"] == "metal3.io"
        assert call_args.kwargs["version"] == "v1alpha1"
        assert call_args.kwargs["plural"] == "baremetalhosts"

    @pytest.mark.asyncio
    async def test_list_bmh_summary_generated(self, mock_k8s_adapter):
        """Test that summary statistics are generated."""
        input_data = ListBMHInput()

        result = await list_bmh(mock_k8s_adapter, input_data)

        assert hasattr(result.summary, "by_state")
        assert hasattr(result.summary, "by_operational_status")
        assert hasattr(result.summary, "online_count")
        assert hasattr(result.summary, "error_count")

    @pytest.mark.asyncio
    async def test_list_bmh_empty_result(self):
        """Test listing BMH when none exist."""
        mock_adapter = MagicMock()
        mock_adapter.list_custom_resources = AsyncMock(return_value=[])

        input_data = ListBMHInput()

        result = await list_bmh(mock_adapter, input_data)

        assert result.total_count == 0
        assert result.filtered_count == 0
        assert result.bmh_list == []

    @pytest.mark.asyncio
    async def test_list_bmh_error_handling(self):
        """Test error handling when Kubernetes API fails."""
        mock_adapter = MagicMock()
        mock_adapter.list_custom_resources = AsyncMock(
            side_effect=Exception("API connection failed")
        )

        input_data = ListBMHInput()

        with pytest.raises(Exception) as exc_info:
            await list_bmh(mock_adapter, input_data)

        assert "BareMetalHosts" in str(exc_info.value) or "API" in str(exc_info.value)


class TestListBMHInput:
    """Tests for ListBMHInput validation."""

    def test_default_values(self):
        """Test default input values."""
        input_data = ListBMHInput()

        assert input_data.namespace == "default"
        assert input_data.state_filter == BMHStateFilter.ALL
        assert input_data.status_filter == BMHOperationalStatusFilter.ALL
        assert input_data.label_selector is None
        assert input_data.limit == 100

    def test_custom_namespace(self):
        """Test custom namespace input."""
        input_data = ListBMHInput(namespace="lab")

        assert input_data.namespace == "lab"

    def test_limit_validation(self):
        """Test limit validation."""
        # Valid limit
        input_data = ListBMHInput(limit=50)
        assert input_data.limit == 50

        # Invalid limit (too low)
        with pytest.raises(ValueError):
            ListBMHInput(limit=0)

        # Invalid limit (too high)
        with pytest.raises(ValueError):
            ListBMHInput(limit=501)


class TestBMHSummary:
    """Tests for BMHSummary model."""

    def test_required_fields(self):
        """Test that required fields are validated."""
        summary = BMHSummary(
            name="test-node",
            namespace="default",
            state="provisioned",
            operational_status="OK",
        )

        assert summary.name == "test-node"
        assert summary.namespace == "default"
        assert summary.state == "provisioned"

    def test_optional_fields_defaults(self):
        """Test optional fields have correct defaults."""
        summary = BMHSummary(
            name="test-node",
            namespace="default",
            state="available",
            operational_status="OK",
        )

        assert summary.bmc_address is None
        assert summary.online is False
        assert summary.consumer is None
        assert summary.error_message is None
        assert summary.age_seconds is None
        assert summary.hardware_vendor is None
        assert summary.hardware_model is None
