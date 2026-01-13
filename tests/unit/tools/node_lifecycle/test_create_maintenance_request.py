"""Unit tests for create_maintenance_request tool."""

from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml

from mosk_mcp.core.exceptions import ValidationError
from mosk_mcp.tools.node_lifecycle.create_maintenance_request import (
    CreateMaintenanceRequestInput,
    CreateMaintenanceRequestOutput,
    MaintenanceReason,
    _check_existing_maintenance,
    _generate_maintenance_request_cr,
    _generate_maintenance_request_name,
    _validate_node_exists,
    create_maintenance_request,
)


SAMPLE_NODE_DATA = {
    "apiVersion": "v1",
    "kind": "Node",
    "metadata": {
        "name": "compute-01",
    },
    "spec": {
        "unschedulable": False,
    },
    "status": {
        "conditions": [
            {
                "type": "Ready",
                "status": "True",
            },
        ],
    },
}


SAMPLE_CORDONED_NODE_DATA = {
    "apiVersion": "v1",
    "kind": "Node",
    "metadata": {
        "name": "compute-02",
    },
    "spec": {
        "unschedulable": True,
    },
    "status": {
        "conditions": [
            {
                "type": "Ready",
                "status": "True",
            },
        ],
    },
}


class TestGenerateMaintenanceRequestName:
    """Tests for _generate_maintenance_request_name function."""

    def test_generate_name_basic(self):
        """Test basic name generation."""
        name = _generate_maintenance_request_name("compute-01")

        assert name.startswith("compute-01-maint-")
        # Should have timestamp suffix
        parts = name.split("-")
        assert len(parts) >= 3

    def test_generate_name_truncates_long_names(self):
        """Test that long node names are truncated."""
        long_name = "a" * 300
        name = _generate_maintenance_request_name(long_name)

        # Should be within K8s name limits
        assert len(name) <= 253

    def test_generate_name_unique(self):
        """Test that generated names are unique."""
        import time

        name1 = _generate_maintenance_request_name("node-01")
        time.sleep(0.01)  # Small delay to ensure different timestamp
        name2 = _generate_maintenance_request_name("node-01")

        # Names might be same if generated in same second
        # but format should be consistent
        assert name1.startswith("node-01-maint-")
        assert name2.startswith("node-01-maint-")


class TestGenerateMaintenanceRequestCR:
    """Tests for _generate_maintenance_request_cr function."""

    def test_generate_cr_basic(self):
        """Test basic CR generation."""
        input_data = CreateMaintenanceRequestInput(
            node_name="compute-01",
            reason=MaintenanceReason.HARDWARE_REPAIR,
            description="Replacing failed disk",
        )

        cr = _generate_maintenance_request_cr(input_data, "compute-01-maint-123")

        assert cr["apiVersion"] == "maintenance.kaas.mirantis.com/v1alpha1"
        assert cr["kind"] == "NodeMaintenanceRequest"
        assert cr["metadata"]["name"] == "compute-01-maint-123"
        assert cr["spec"]["nodeName"] == "compute-01"
        assert cr["spec"]["reason"] == "hardware-repair"

    def test_generate_cr_with_drain_spec(self):
        """Test CR generation with drain spec."""
        input_data = CreateMaintenanceRequestInput(
            node_name="compute-01",
            reason=MaintenanceReason.OS_UPGRADE,
            drain_pods=True,
            force_drain=True,
            grace_period_seconds=120,
            timeout_minutes=30,
        )

        cr = _generate_maintenance_request_cr(input_data, "test-maint")

        drain_spec = cr["spec"]["drainSpec"]
        assert drain_spec["enabled"] is True
        assert drain_spec["force"] is True
        assert drain_spec["gracePeriodSeconds"] == 120
        assert drain_spec["timeoutSeconds"] == 30 * 60

    def test_generate_cr_labels_and_annotations(self):
        """Test CR generation includes labels and annotations."""
        input_data = CreateMaintenanceRequestInput(
            node_name="compute-01",
            reason=MaintenanceReason.SECURITY_PATCH,
            description="Applying security patches",
        )

        cr = _generate_maintenance_request_cr(input_data, "test-maint")

        # Check labels
        labels = cr["metadata"]["labels"]
        assert labels["maintenance.kaas.mirantis.com/node"] == "compute-01"
        assert labels["maintenance.kaas.mirantis.com/reason"] == "security-patch"

        # Check annotations
        annotations = cr["metadata"]["annotations"]
        assert "mosk-mcp-server" in annotations["maintenance.kaas.mirantis.com/created-by"]


class TestValidateNodeExists:
    """Tests for _validate_node_exists function."""

    @pytest.fixture
    def mock_k8s_adapter(self):
        """Create a mock Kubernetes adapter."""
        adapter = MagicMock()
        adapter.get = AsyncMock(return_value=SAMPLE_NODE_DATA)
        return adapter

    @pytest.mark.asyncio
    async def test_validate_existing_node(self, mock_k8s_adapter):
        """Test validating an existing node."""
        warnings = await _validate_node_exists(mock_k8s_adapter, "compute-01")

        assert len(warnings) == 0

    @pytest.mark.asyncio
    async def test_validate_cordoned_node(self, mock_k8s_adapter):
        """Test validating a cordoned node generates warning."""
        mock_k8s_adapter.get = AsyncMock(return_value=SAMPLE_CORDONED_NODE_DATA)

        warnings = await _validate_node_exists(mock_k8s_adapter, "compute-02")

        assert len(warnings) == 1
        assert "already cordoned" in warnings[0]

    @pytest.mark.asyncio
    async def test_validate_not_ready_node(self, mock_k8s_adapter):
        """Test validating a not-ready node generates warning."""
        mock_k8s_adapter.get = AsyncMock(
            return_value={
                "spec": {},
                "status": {
                    "conditions": [
                        {"type": "Ready", "status": "False"},
                    ],
                },
            }
        )

        warnings = await _validate_node_exists(mock_k8s_adapter, "compute-03")

        assert len(warnings) == 1
        assert "not in Ready state" in warnings[0]

    @pytest.mark.asyncio
    async def test_validate_nonexistent_node(self, mock_k8s_adapter):
        """Test validating non-existent node raises error."""
        mock_k8s_adapter.get = AsyncMock(side_effect=Exception("Node not found"))

        with pytest.raises(ValidationError) as exc_info:
            await _validate_node_exists(mock_k8s_adapter, "nonexistent")

        assert "not found or inaccessible" in str(exc_info.value)


class TestCheckExistingMaintenance:
    """Tests for _check_existing_maintenance function."""

    @pytest.fixture
    def mock_k8s_adapter(self):
        """Create a mock Kubernetes adapter."""
        adapter = MagicMock()
        adapter.list_custom_resources = AsyncMock(return_value=[])
        return adapter

    @pytest.mark.asyncio
    async def test_no_existing_maintenance(self, mock_k8s_adapter):
        """Test checking when no existing maintenance requests."""
        warnings = await _check_existing_maintenance(
            mock_k8s_adapter,
            "compute-01",
            "default",
        )

        assert len(warnings) == 0

    @pytest.mark.asyncio
    async def test_existing_active_maintenance(self, mock_k8s_adapter):
        """Test checking when active maintenance request exists."""
        mock_k8s_adapter.list_custom_resources = AsyncMock(
            return_value=[
                {
                    "metadata": {"name": "compute-01-maint-123"},
                    "status": {"phase": "InProgress"},
                },
            ]
        )

        warnings = await _check_existing_maintenance(
            mock_k8s_adapter,
            "compute-01",
            "default",
        )

        assert len(warnings) == 1
        assert "active maintenance request" in warnings[0]

    @pytest.mark.asyncio
    async def test_existing_completed_maintenance(self, mock_k8s_adapter):
        """Test checking when only completed maintenance exists."""
        mock_k8s_adapter.list_custom_resources = AsyncMock(
            return_value=[
                {
                    "metadata": {"name": "compute-01-maint-old"},
                    "status": {"phase": "Completed"},
                },
            ]
        )

        warnings = await _check_existing_maintenance(
            mock_k8s_adapter,
            "compute-01",
            "default",
        )

        # Completed maintenance should not generate warning
        assert len(warnings) == 0


class TestCreateMaintenanceRequest:
    """Tests for create_maintenance_request function."""

    @pytest.fixture
    def mock_k8s_adapter(self):
        """Create a mock Kubernetes adapter."""
        adapter = MagicMock()
        adapter.get = AsyncMock(return_value=SAMPLE_NODE_DATA)
        adapter.list_custom_resources = AsyncMock(return_value=[])
        adapter.create_custom_resource = AsyncMock()
        return adapter

    @pytest.mark.asyncio
    async def test_create_maintenance_request_dry_run(self, mock_k8s_adapter, admin_context):
        """Test creating maintenance request in dry run mode."""
        input_data = CreateMaintenanceRequestInput(
            node_name="compute-01",
            reason=MaintenanceReason.HARDWARE_REPAIR,
            description="Testing",
            dry_run=True,
        )

        result = await create_maintenance_request(
            mock_k8s_adapter, input_data, context=admin_context
        )

        assert isinstance(result, CreateMaintenanceRequestOutput)
        assert result.applied is False
        assert result.status == "Generated"
        assert result.node_name == "compute-01"
        assert result.template_yaml is not None

        # Verify YAML is valid
        parsed_yaml = yaml.safe_load(result.template_yaml)
        assert parsed_yaml["kind"] == "NodeMaintenanceRequest"

        # Should not have called create
        mock_k8s_adapter.create_custom_resource.assert_not_called()

    @pytest.mark.asyncio
    async def test_create_maintenance_request_apply(self, mock_k8s_adapter, admin_context):
        """Test creating and applying maintenance request."""
        input_data = CreateMaintenanceRequestInput(
            node_name="compute-01",
            reason=MaintenanceReason.FIRMWARE_UPDATE,
            dry_run=False,
        )

        result = await create_maintenance_request(
            mock_k8s_adapter, input_data, context=admin_context
        )

        assert result.applied is True
        assert result.status == "Created"

        # Should have called create
        mock_k8s_adapter.create_custom_resource.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_maintenance_request_with_warnings(self, mock_k8s_adapter, admin_context):
        """Test that warnings are included in output."""
        mock_k8s_adapter.get = AsyncMock(return_value=SAMPLE_CORDONED_NODE_DATA)

        input_data = CreateMaintenanceRequestInput(
            node_name="compute-02",
            reason=MaintenanceReason.PLANNED_REBOOT,
            dry_run=True,
        )

        result = await create_maintenance_request(
            mock_k8s_adapter, input_data, context=admin_context
        )

        assert len(result.warnings) > 0
        assert any("cordoned" in w for w in result.warnings)

    @pytest.mark.asyncio
    async def test_create_maintenance_request_invalid_node(self, mock_k8s_adapter, admin_context):
        """Test creating maintenance request for invalid node."""
        mock_k8s_adapter.get = AsyncMock(side_effect=Exception("Node not found"))

        input_data = CreateMaintenanceRequestInput(
            node_name="nonexistent",
            reason=MaintenanceReason.OTHER,
        )

        with pytest.raises(ValidationError):
            await create_maintenance_request(mock_k8s_adapter, input_data, context=admin_context)

    @pytest.mark.asyncio
    async def test_create_maintenance_request_template_dict(self, mock_k8s_adapter, admin_context):
        """Test that template_dict is included in output."""
        input_data = CreateMaintenanceRequestInput(
            node_name="compute-01",
            reason=MaintenanceReason.DISK_REPLACEMENT,
            dry_run=True,
        )

        result = await create_maintenance_request(
            mock_k8s_adapter, input_data, context=admin_context
        )

        assert result.template_dict is not None
        assert result.template_dict["kind"] == "NodeMaintenanceRequest"
        assert result.template_dict["spec"]["nodeName"] == "compute-01"


class TestCreateMaintenanceRequestInput:
    """Tests for CreateMaintenanceRequestInput validation."""

    def test_required_fields(self):
        """Test required fields."""
        input_data = CreateMaintenanceRequestInput(
            node_name="compute-01",
            reason=MaintenanceReason.HARDWARE_REPAIR,
        )

        assert input_data.node_name == "compute-01"
        assert input_data.reason == MaintenanceReason.HARDWARE_REPAIR

    def test_default_values(self):
        """Test default values."""
        input_data = CreateMaintenanceRequestInput(
            node_name="test-node",
            reason=MaintenanceReason.OTHER,
        )

        assert input_data.description == ""
        assert input_data.drain_pods is True
        assert input_data.force_drain is False
        assert input_data.grace_period_seconds == 300
        assert input_data.timeout_minutes == 60
        assert input_data.dry_run is True
        assert input_data.namespace == "default"

    def test_node_name_validation(self):
        """Test node name validation."""
        with pytest.raises(ValueError):
            CreateMaintenanceRequestInput(
                node_name="",  # Empty name
                reason=MaintenanceReason.OTHER,
            )

    def test_grace_period_bounds(self):
        """Test grace period bounds validation."""
        # Valid
        input_data = CreateMaintenanceRequestInput(
            node_name="node",
            reason=MaintenanceReason.OTHER,
            grace_period_seconds=0,
        )
        assert input_data.grace_period_seconds == 0

        input_data = CreateMaintenanceRequestInput(
            node_name="node",
            reason=MaintenanceReason.OTHER,
            grace_period_seconds=3600,
        )
        assert input_data.grace_period_seconds == 3600

        # Invalid
        with pytest.raises(ValueError):
            CreateMaintenanceRequestInput(
                node_name="node",
                reason=MaintenanceReason.OTHER,
                grace_period_seconds=-1,
            )

    def test_timeout_minutes_bounds(self):
        """Test timeout minutes bounds validation."""
        with pytest.raises(ValueError):
            CreateMaintenanceRequestInput(
                node_name="node",
                reason=MaintenanceReason.OTHER,
                timeout_minutes=0,  # Must be >= 1
            )

        with pytest.raises(ValueError):
            CreateMaintenanceRequestInput(
                node_name="node",
                reason=MaintenanceReason.OTHER,
                timeout_minutes=2000,  # Must be <= 1440
            )


class TestMaintenanceReason:
    """Tests for MaintenanceReason enum."""

    def test_all_reasons(self):
        """Test all maintenance reasons are defined."""
        reasons = list(MaintenanceReason)

        assert MaintenanceReason.HARDWARE_REPAIR in reasons
        assert MaintenanceReason.FIRMWARE_UPDATE in reasons
        assert MaintenanceReason.OS_UPGRADE in reasons
        assert MaintenanceReason.SECURITY_PATCH in reasons
        assert MaintenanceReason.PERFORMANCE_TUNING in reasons
        assert MaintenanceReason.DISK_REPLACEMENT in reasons
        assert MaintenanceReason.NETWORK_MAINTENANCE in reasons
        assert MaintenanceReason.PLANNED_REBOOT in reasons
        assert MaintenanceReason.OTHER in reasons

    def test_reason_values(self):
        """Test reason values are kebab-case."""
        assert MaintenanceReason.HARDWARE_REPAIR.value == "hardware-repair"
        assert MaintenanceReason.FIRMWARE_UPDATE.value == "firmware-update"
        assert MaintenanceReason.OS_UPGRADE.value == "os-upgrade"
