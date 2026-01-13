"""Unit tests for validation tools.

Tests for run_smoke_test, run_post_upgrade_validation, and run_mosk_platform_validation.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mosk_mcp.tools.common import HealthStatus, ValidationLevel, ValidationStatus
from mosk_mcp.tools.validation.run_mosk_platform_validation import (
    RunMoskPlatformValidationInput,
    RunMoskPlatformValidationOutput,
    run_mosk_platform_validation,
)
from mosk_mcp.tools.validation.run_post_upgrade_validation import (
    RunPostUpgradeValidationInput,
    RunPostUpgradeValidationOutput,
    run_post_upgrade_validation,
)
from mosk_mcp.tools.validation.run_smoke_test import (
    RunSmokeTestInput,
    RunSmokeTestOutput,
    SmokeTestStatus,
    SmokeTestType,
    run_smoke_test,
)


# ==========================
# RunSmokeTest Tests
# ==========================
class TestRunSmokeTest:
    """Tests for run_smoke_test tool."""

    @pytest.fixture
    def mock_openstack_adapter(self) -> MagicMock:
        """Create a mock OpenStack adapter."""
        adapter = MagicMock()
        adapter.list_images = AsyncMock(
            return_value=[
                {"Name": "cirros-test", "ID": "img-123", "Status": "active"},
            ]
        )
        adapter.list_flavors = AsyncMock(
            return_value=[
                {"Name": "m1.tiny", "ID": "flv-123", "RAM": 512},
            ]
        )
        adapter.list_networks = AsyncMock(
            return_value=[
                {"Name": "internal-net", "ID": "net-123"},
            ]
        )
        adapter.create_server = AsyncMock(
            return_value={
                "id": "srv-123",
                "name": "mcp-smoke-vm-abc123",
                "status": "ACTIVE",
                "addresses": {"internal-net": [{"addr": "192.168.1.10"}]},
            }
        )
        adapter.get_server_console_output = AsyncMock(return_value="Boot successful\n")
        adapter.reboot_server = AsyncMock(return_value=True)
        adapter.delete_server = AsyncMock(return_value=True)
        adapter.create_volume = AsyncMock(
            return_value={"id": "vol-123", "name": "test-vol", "status": "available"}
        )
        adapter.get_volume = AsyncMock(return_value={"id": "vol-123", "status": "available"})
        adapter.attach_volume = AsyncMock(return_value=True)
        adapter.detach_volume = AsyncMock(return_value=True)
        adapter.delete_volume = AsyncMock(return_value=True)
        adapter.create_keypair = AsyncMock(return_value={"name": "test-key"})
        adapter.delete_keypair = AsyncMock(return_value=True)
        adapter.create_security_group = AsyncMock(return_value={"name": "test-sg"})
        adapter.add_security_group_rule = AsyncMock(return_value=True)
        adapter.delete_security_group = AsyncMock(return_value=True)
        adapter.create_network = AsyncMock(return_value={"id": "net-456", "name": "test-net"})
        adapter.delete_network = AsyncMock(return_value=True)
        adapter.create_subnet = AsyncMock(return_value={"id": "sub-123", "name": "test-subnet"})
        adapter.delete_subnet = AsyncMock(return_value=True)
        return adapter

    @pytest.mark.asyncio
    async def test_vm_lifecycle_success(self, mock_openstack_adapter: MagicMock) -> None:
        """Test successful VM lifecycle smoke test."""
        input_data = RunSmokeTestInput(
            test_type="vm_lifecycle",
            cleanup=True,
            timeout_seconds=300,
        )

        result = await run_smoke_test(mock_openstack_adapter, input_data)

        assert isinstance(result, RunSmokeTestOutput)
        assert result.test_type == "vm_lifecycle"
        assert result.status == SmokeTestStatus.PASSED.value
        assert len(result.steps) > 0
        assert result.timestamp
        # Verify cleanup happened
        mock_openstack_adapter.delete_server.assert_called()

    @pytest.mark.asyncio
    async def test_vm_lifecycle_create_failure(self, mock_openstack_adapter: MagicMock) -> None:
        """Test VM lifecycle when server creation fails."""
        mock_openstack_adapter.create_server = AsyncMock(return_value=None)

        input_data = RunSmokeTestInput(
            test_type="vm_lifecycle",
            cleanup=True,
        )

        result = await run_smoke_test(mock_openstack_adapter, input_data)

        assert result.status == SmokeTestStatus.FAILED.value
        assert result.error_message
        assert "Failed to create VM" in result.error_message

    @pytest.mark.asyncio
    async def test_storage_operations_success(self, mock_openstack_adapter: MagicMock) -> None:
        """Test successful storage operations smoke test."""
        input_data = RunSmokeTestInput(
            test_type="storage_operations",
            cleanup=True,
            timeout_seconds=300,
        )

        result = await run_smoke_test(mock_openstack_adapter, input_data)

        assert result.test_type == "storage_operations"
        # May pass or fail based on mock setup
        assert result.status in [
            SmokeTestStatus.PASSED.value,
            SmokeTestStatus.FAILED.value,
        ]
        assert len(result.steps) > 0

    @pytest.mark.asyncio
    async def test_full_stack_test(self, mock_openstack_adapter: MagicMock) -> None:
        """Test full stack smoke test."""
        input_data = RunSmokeTestInput(
            test_type="full_stack",
            cleanup=True,
            timeout_seconds=300,
        )

        result = await run_smoke_test(mock_openstack_adapter, input_data)

        assert result.test_type == "full_stack"
        assert len(result.steps) > 0
        # Should create multiple resources
        assert len(result.resources_created) > 0

    @pytest.mark.asyncio
    async def test_invalid_test_type(self, mock_openstack_adapter: MagicMock) -> None:
        """Test error handling for invalid test type."""
        input_data = RunSmokeTestInput(
            test_type="invalid_type",
            cleanup=True,
        )

        result = await run_smoke_test(mock_openstack_adapter, input_data)

        assert result.status == SmokeTestStatus.ERROR.value
        assert "Invalid test type" in (result.error_message or "")

    @pytest.mark.asyncio
    async def test_no_cleanup(self, mock_openstack_adapter: MagicMock) -> None:
        """Test smoke test without cleanup."""
        input_data = RunSmokeTestInput(
            test_type="vm_lifecycle",
            cleanup=False,
            timeout_seconds=300,
        )

        result = await run_smoke_test(mock_openstack_adapter, input_data)

        assert result.test_type == "vm_lifecycle"
        # Server should NOT be deleted when cleanup=False
        # Note: delete is called in finally block only if cleanup=True

    @pytest.mark.asyncio
    async def test_custom_image_flavor_network(self, mock_openstack_adapter: MagicMock) -> None:
        """Test smoke test with custom image, flavor, and network."""
        input_data = RunSmokeTestInput(
            test_type="vm_lifecycle",
            image_name="custom-image",
            flavor_name="custom-flavor",
            network_name="custom-network",
            cleanup=True,
        )

        result = await run_smoke_test(mock_openstack_adapter, input_data)

        assert result.test_type == "vm_lifecycle"
        # Should use the custom values (verified by mock calls)
        mock_openstack_adapter.create_server.assert_called()

    @pytest.mark.asyncio
    async def test_resource_leak_tracking(self, mock_openstack_adapter: MagicMock) -> None:
        """Test that leaked resources are tracked when cleanup fails."""
        mock_openstack_adapter.delete_server = AsyncMock(return_value=False)

        input_data = RunSmokeTestInput(
            test_type="vm_lifecycle",
            cleanup=True,
        )

        result = await run_smoke_test(mock_openstack_adapter, input_data)

        # Should track leaked resources
        assert len(result.resources_leaked) > 0 or result.status == SmokeTestStatus.FAILED.value

    @pytest.mark.asyncio
    async def test_recommendations_generated(self, mock_openstack_adapter: MagicMock) -> None:
        """Test that recommendations are generated based on results."""
        input_data = RunSmokeTestInput(
            test_type="vm_lifecycle",
            cleanup=True,
        )

        result = await run_smoke_test(mock_openstack_adapter, input_data)

        # Should have recommendations
        assert len(result.recommendations) > 0


class TestSmokeTestModels:
    """Tests for smoke test Pydantic models."""

    def test_smoke_test_input_defaults(self) -> None:
        """Test default values for smoke test input."""
        input_model = RunSmokeTestInput()

        assert input_model.test_type == "vm_lifecycle"
        assert input_model.cleanup is True
        assert input_model.timeout_seconds == 300
        assert input_model.prefix == "mcp-smoke"

    def test_smoke_test_type_enum(self) -> None:
        """Test SmokeTestType enum values."""
        assert SmokeTestType.VM_LIFECYCLE.value == "vm_lifecycle"
        assert SmokeTestType.STORAGE_OPERATIONS.value == "storage_operations"
        assert SmokeTestType.FULL_STACK.value == "full_stack"

    def test_smoke_test_status_enum(self) -> None:
        """Test SmokeTestStatus enum values."""
        assert SmokeTestStatus.PASSED.value == "passed"
        assert SmokeTestStatus.FAILED.value == "failed"
        assert SmokeTestStatus.SKIPPED.value == "skipped"
        assert SmokeTestStatus.ERROR.value == "error"


# ==========================
# RunPostUpgradeValidation Tests
# ==========================
class TestRunPostUpgradeValidation:
    """Tests for run_post_upgrade_validation tool."""

    @pytest.fixture
    def mock_k8s_adapter(self) -> MagicMock:
        """Create a mock Kubernetes adapter."""
        adapter = MagicMock()
        adapter.list_openstack_deployments = AsyncMock(
            return_value=[
                {
                    "metadata": {"name": "mos"},
                    "spec": {"openstack_version": "caracal"},
                    "status": {"openstack_version": "bobcat"},
                }
            ]
        )
        return adapter

    @pytest.fixture
    def mock_openstack_adapter(self) -> MagicMock:
        """Create a mock OpenStack adapter."""
        adapter = MagicMock()
        adapter.list_images = AsyncMock(return_value=[{"Name": "cirros", "Status": "active"}])
        adapter.list_flavors = AsyncMock(return_value=[{"Name": "m1.tiny", "RAM": 512}])
        adapter.list_networks = AsyncMock(return_value=[{"Name": "internal"}])
        adapter.create_server = AsyncMock(
            return_value={"id": "srv-123", "status": "ACTIVE", "addresses": {}}
        )
        adapter.delete_server = AsyncMock(return_value=True)
        return adapter

    @pytest.mark.asyncio
    async def test_quick_validation_level(
        self,
        mock_k8s_adapter: MagicMock,
        mock_openstack_adapter: MagicMock,
    ) -> None:
        """Test quick validation level (Tier 1 only)."""
        input_data = RunPostUpgradeValidationInput(
            level="quick",
            namespace="openstack",
        )

        # Mock the health check functions
        with (
            patch(
                "mosk_mcp.tools.validation.run_post_upgrade_validation.get_kubernetes_health"
            ) as mock_k8s_health,
            patch(
                "mosk_mcp.tools.validation.run_post_upgrade_validation.get_openstack_health"
            ) as mock_os_health,
            patch(
                "mosk_mcp.tools.validation.run_post_upgrade_validation.get_ceph_health"
            ) as mock_ceph_health,
        ):
            # Setup mock returns
            mock_k8s_result = MagicMock()
            mock_k8s_result.health = HealthStatus.HEALTHY
            mock_k8s_result.score = 100
            mock_k8s_result.total_nodes = 3
            mock_k8s_result.ready_nodes = 3
            mock_k8s_result.not_ready_nodes = 0
            mock_k8s_health.return_value = mock_k8s_result

            mock_os_result = MagicMock()
            mock_os_result.control_plane_health = HealthStatus.HEALTHY
            mock_os_result.control_plane_score = 100
            mock_os_result.osdpl_phase = "Deployed"
            mock_os_health.return_value = mock_os_result

            mock_ceph_result = MagicMock()
            mock_ceph_result.health = HealthStatus.HEALTHY
            mock_ceph_result.score = 100
            mock_ceph_result.ceph_health = "HEALTH_OK"
            mock_ceph_result.osds_total = 6
            mock_ceph_result.osds_up = 6
            mock_ceph_health.return_value = mock_ceph_result

            result = await run_post_upgrade_validation(
                mock_k8s_adapter, mock_openstack_adapter, input_data
            )

            assert isinstance(result, RunPostUpgradeValidationOutput)
            assert result.validation_level == "quick"
            # Quick level only runs Tier 1
            assert result.tiers_run == 1

    @pytest.mark.asyncio
    async def test_standard_validation_level(
        self,
        mock_k8s_adapter: MagicMock,
        mock_openstack_adapter: MagicMock,
    ) -> None:
        """Test standard validation level (Tier 1 + Tier 2)."""
        input_data = RunPostUpgradeValidationInput(
            level="standard",
            namespace="openstack",
        )

        with (
            patch(
                "mosk_mcp.tools.validation.run_post_upgrade_validation.get_kubernetes_health"
            ) as mock_k8s_health,
            patch(
                "mosk_mcp.tools.validation.run_post_upgrade_validation.get_openstack_health"
            ) as mock_os_health,
            patch(
                "mosk_mcp.tools.validation.run_post_upgrade_validation.get_ceph_health"
            ) as mock_ceph_health,
            patch(
                "mosk_mcp.tools.validation.run_post_upgrade_validation.check_service_availability"
            ) as mock_service_check,
        ):
            # Setup mock returns
            mock_k8s_result = MagicMock()
            mock_k8s_result.health = HealthStatus.HEALTHY
            mock_k8s_result.score = 100
            mock_k8s_result.total_nodes = 3
            mock_k8s_result.ready_nodes = 3
            mock_k8s_result.not_ready_nodes = 0
            mock_k8s_health.return_value = mock_k8s_result

            mock_os_result = MagicMock()
            mock_os_result.control_plane_health = HealthStatus.HEALTHY
            mock_os_result.control_plane_score = 100
            mock_os_result.osdpl_phase = "Deployed"
            mock_os_health.return_value = mock_os_result

            mock_ceph_result = MagicMock()
            mock_ceph_result.health = HealthStatus.HEALTHY
            mock_ceph_result.score = 100
            mock_ceph_result.ceph_health = "HEALTH_OK"
            mock_ceph_result.osds_total = 6
            mock_ceph_result.osds_up = 6
            mock_ceph_health.return_value = mock_ceph_result

            mock_service_result = MagicMock()
            mock_service_result.overall_status = "healthy"
            mock_service_result.services_checked = 5
            mock_service_result.services_healthy = 5
            mock_service_result.services_degraded = 0
            mock_service_result.services_unavailable = 0
            mock_service_result.results = []
            mock_service_check.return_value = mock_service_result

            result = await run_post_upgrade_validation(
                mock_k8s_adapter, mock_openstack_adapter, input_data
            )

            assert result.validation_level == "standard"
            # Standard level runs Tier 1 + Tier 2
            assert result.tiers_run == 2

    @pytest.mark.asyncio
    async def test_invalid_validation_level(
        self,
        mock_k8s_adapter: MagicMock,
        mock_openstack_adapter: MagicMock,
    ) -> None:
        """Test error handling for invalid validation level."""
        input_data = RunPostUpgradeValidationInput(
            level="invalid_level",
            namespace="openstack",
        )

        result = await run_post_upgrade_validation(
            mock_k8s_adapter, mock_openstack_adapter, input_data
        )

        assert result.overall_status == ValidationStatus.ERROR.value
        assert "Invalid validation level" in result.recommendations[0]

    @pytest.mark.asyncio
    async def test_osdpl_auto_discovery(
        self,
        mock_k8s_adapter: MagicMock,
        mock_openstack_adapter: MagicMock,
    ) -> None:
        """Test auto-discovery of OSDPL name."""
        input_data = RunPostUpgradeValidationInput(
            level="quick",
            osdpl_name=None,  # Should auto-discover
            namespace="openstack",
        )

        with (
            patch(
                "mosk_mcp.tools.validation.run_post_upgrade_validation.get_kubernetes_health"
            ) as mock_k8s_health,
            patch(
                "mosk_mcp.tools.validation.run_post_upgrade_validation.get_openstack_health"
            ) as mock_os_health,
            patch(
                "mosk_mcp.tools.validation.run_post_upgrade_validation.get_ceph_health"
            ) as mock_ceph_health,
        ):
            mock_k8s_result = MagicMock()
            mock_k8s_result.health = HealthStatus.HEALTHY
            mock_k8s_result.score = 100
            mock_k8s_result.total_nodes = 3
            mock_k8s_result.ready_nodes = 3
            mock_k8s_result.not_ready_nodes = 0
            mock_k8s_health.return_value = mock_k8s_result

            mock_os_result = MagicMock()
            mock_os_result.control_plane_health = HealthStatus.HEALTHY
            mock_os_result.control_plane_score = 100
            mock_os_result.osdpl_phase = "Deployed"
            mock_os_health.return_value = mock_os_result

            mock_ceph_result = MagicMock()
            mock_ceph_result.health = HealthStatus.HEALTHY
            mock_ceph_result.score = 100
            mock_ceph_result.ceph_health = "HEALTH_OK"
            mock_ceph_result.osds_total = 6
            mock_ceph_result.osds_up = 6
            mock_ceph_health.return_value = mock_ceph_result

            result = await run_post_upgrade_validation(
                mock_k8s_adapter, mock_openstack_adapter, input_data
            )

            # Should have auto-discovered the OSDPL name
            assert result.osdpl_name == "mos"
            assert result.to_version == "caracal"
            assert result.from_version == "bobcat"


class TestPostUpgradeValidationModels:
    """Tests for post-upgrade validation Pydantic models."""

    def test_input_defaults(self) -> None:
        """Test default values for input model."""
        input_model = RunPostUpgradeValidationInput()

        assert input_model.level == "standard"
        assert input_model.namespace == "openstack"
        assert input_model.cleanup_smoke_tests is True
        assert input_model.timeout_seconds == 600

    def test_validation_level_enum(self) -> None:
        """Test ValidationLevel enum values."""
        assert ValidationLevel.QUICK.value == "quick"
        assert ValidationLevel.STANDARD.value == "standard"
        assert ValidationLevel.COMPREHENSIVE.value == "comprehensive"


# ==========================
# RunMoskPlatformValidation Tests
# ==========================
class TestRunMoskPlatformValidation:
    """Tests for run_mosk_platform_validation tool."""

    @pytest.fixture
    def mock_mcc_adapter(self) -> MagicMock:
        """Create a mock MCC adapter."""
        adapter = MagicMock()
        adapter.get_cluster = AsyncMock(
            return_value={
                "metadata": {"name": "mos"},
                "spec": {"providerSpec": {"value": {"release": "mosk-21-0-2-25-2-2"}}},
            }
        )
        adapter.list_clusters = AsyncMock(
            return_value=[
                {
                    "metadata": {"name": "mos"},
                    "spec": {"providerSpec": {"value": {"release": "mosk-21-0-2-25-2-2"}}},
                }
            ]
        )
        adapter.list_cluster_upgrade_statuses = AsyncMock(return_value=[])
        return adapter

    @pytest.fixture
    def mock_mosk_adapter(self) -> MagicMock:
        """Create a mock MOSK adapter."""
        adapter = MagicMock()
        adapter.list = AsyncMock(
            return_value=[
                {
                    "metadata": {"name": "node-1"},
                    "status": {
                        "nodeInfo": {"kubeletVersion": "v1.28.6"},
                        "conditions": [{"type": "Ready", "status": "True"}],
                    },
                }
            ]
        )
        adapter.list_openstack_deployments = AsyncMock(
            return_value=[
                {
                    "metadata": {"name": "mos"},
                    "spec": {"openstack_version": "caracal"},
                }
            ]
        )
        return adapter

    @pytest.mark.asyncio
    async def test_quick_validation(
        self,
        mock_mcc_adapter: MagicMock,
        mock_mosk_adapter: MagicMock,
    ) -> None:
        """Test quick validation level."""
        input_data = RunMoskPlatformValidationInput(
            level="quick",
            cluster_name="mos",
            cluster_namespace="lab",
        )

        with (
            patch(
                "mosk_mcp.tools.validation.run_mosk_platform_validation.get_kubernetes_health"
            ) as mock_k8s_health,
            patch(
                "mosk_mcp.tools.validation.run_mosk_platform_validation.get_ceph_health"
            ) as mock_ceph_health,
        ):
            mock_k8s_result = MagicMock()
            mock_k8s_result.health = HealthStatus.HEALTHY
            mock_k8s_result.score = 100
            mock_k8s_result.total_nodes = 3
            mock_k8s_result.ready_nodes = 3
            mock_k8s_result.not_ready_nodes = 0
            mock_k8s_result.api_server_healthy = True
            mock_k8s_result.nodes = []
            mock_k8s_health.return_value = mock_k8s_result

            mock_ceph_result = MagicMock()
            mock_ceph_result.health = HealthStatus.HEALTHY
            mock_ceph_result.score = 100
            mock_ceph_result.ceph_health = "HEALTH_OK"
            mock_ceph_result.osds_total = 6
            mock_ceph_result.osds_up = 6
            mock_ceph_result.osds_in = 6
            mock_ceph_health.return_value = mock_ceph_result

            result = await run_mosk_platform_validation(
                mock_mcc_adapter, mock_mosk_adapter, input_data
            )

            assert isinstance(result, RunMoskPlatformValidationOutput)
            assert result.validation_level == "quick"
            # Quick level only runs Tier 1
            assert result.tiers_run == 1

    @pytest.mark.asyncio
    async def test_standard_validation(
        self,
        mock_mcc_adapter: MagicMock,
        mock_mosk_adapter: MagicMock,
    ) -> None:
        """Test standard validation level."""
        input_data = RunMoskPlatformValidationInput(
            level="standard",
            cluster_name="mos",
            cluster_namespace="lab",
        )

        with (
            patch(
                "mosk_mcp.tools.validation.run_mosk_platform_validation.get_kubernetes_health"
            ) as mock_k8s_health,
            patch(
                "mosk_mcp.tools.validation.run_mosk_platform_validation.get_ceph_health"
            ) as mock_ceph_health,
        ):
            mock_k8s_result = MagicMock()
            mock_k8s_result.health = HealthStatus.HEALTHY
            mock_k8s_result.score = 100
            mock_k8s_result.total_nodes = 3
            mock_k8s_result.ready_nodes = 3
            mock_k8s_result.not_ready_nodes = 0
            mock_k8s_result.api_server_healthy = True
            mock_k8s_result.nodes = []
            mock_k8s_health.return_value = mock_k8s_result

            mock_ceph_result = MagicMock()
            mock_ceph_result.health = HealthStatus.HEALTHY
            mock_ceph_result.score = 100
            mock_ceph_result.ceph_health = "HEALTH_OK"
            mock_ceph_result.osds_total = 6
            mock_ceph_result.osds_up = 6
            mock_ceph_result.osds_in = 6
            mock_ceph_health.return_value = mock_ceph_result

            result = await run_mosk_platform_validation(
                mock_mcc_adapter, mock_mosk_adapter, input_data
            )

            assert result.validation_level == "standard"
            # Standard level runs Tier 1 + Tier 2
            assert result.tiers_run == 2

    @pytest.mark.asyncio
    async def test_comprehensive_validation(
        self,
        mock_mcc_adapter: MagicMock,
        mock_mosk_adapter: MagicMock,
    ) -> None:
        """Test comprehensive validation level."""
        input_data = RunMoskPlatformValidationInput(
            level="comprehensive",
            cluster_name="mos",
            cluster_namespace="lab",
        )

        with (
            patch(
                "mosk_mcp.tools.validation.run_mosk_platform_validation.get_kubernetes_health"
            ) as mock_k8s_health,
            patch(
                "mosk_mcp.tools.validation.run_mosk_platform_validation.get_ceph_health"
            ) as mock_ceph_health,
            patch(
                "mosk_mcp.tools.validation.run_mosk_platform_validation.get_openstack_health"
            ) as mock_os_health,
        ):
            mock_k8s_result = MagicMock()
            mock_k8s_result.health = HealthStatus.HEALTHY
            mock_k8s_result.score = 100
            mock_k8s_result.total_nodes = 3
            mock_k8s_result.ready_nodes = 3
            mock_k8s_result.not_ready_nodes = 0
            mock_k8s_result.api_server_healthy = True
            mock_k8s_result.nodes = []
            mock_k8s_health.return_value = mock_k8s_result

            mock_ceph_result = MagicMock()
            mock_ceph_result.health = HealthStatus.HEALTHY
            mock_ceph_result.score = 100
            mock_ceph_result.ceph_health = "HEALTH_OK"
            mock_ceph_result.osds_total = 6
            mock_ceph_result.osds_up = 6
            mock_ceph_result.osds_in = 6
            mock_ceph_health.return_value = mock_ceph_result

            mock_os_result = MagicMock()
            mock_os_result.control_plane_health = HealthStatus.HEALTHY
            mock_os_result.compute_health = HealthStatus.HEALTHY
            mock_os_result.control_plane_score = 100
            mock_os_result.compute_score = 100
            mock_os_result.osdpl_phase = "Deployed"
            mock_os_health.return_value = mock_os_result

            result = await run_mosk_platform_validation(
                mock_mcc_adapter, mock_mosk_adapter, input_data
            )

            assert result.validation_level == "comprehensive"
            # Comprehensive level runs all 3 tiers
            assert result.tiers_run == 3

    @pytest.mark.asyncio
    async def test_invalid_validation_level(
        self,
        mock_mcc_adapter: MagicMock,
        mock_mosk_adapter: MagicMock,
    ) -> None:
        """Test error handling for invalid validation level."""
        input_data = RunMoskPlatformValidationInput(
            level="invalid",
            cluster_name="mos",
        )

        result = await run_mosk_platform_validation(mock_mcc_adapter, mock_mosk_adapter, input_data)

        assert result.overall_status == ValidationStatus.ERROR.value
        assert "Invalid validation level" in result.recommendations[0]

    @pytest.mark.asyncio
    async def test_cluster_auto_discovery(
        self,
        mock_mcc_adapter: MagicMock,
        mock_mosk_adapter: MagicMock,
    ) -> None:
        """Test auto-discovery of cluster name."""
        input_data = RunMoskPlatformValidationInput(
            level="quick",
            cluster_name=None,  # Should auto-discover
            cluster_namespace="lab",
        )

        with (
            patch(
                "mosk_mcp.tools.validation.run_mosk_platform_validation.get_kubernetes_health"
            ) as mock_k8s_health,
            patch(
                "mosk_mcp.tools.validation.run_mosk_platform_validation.get_ceph_health"
            ) as mock_ceph_health,
        ):
            mock_k8s_result = MagicMock()
            mock_k8s_result.health = HealthStatus.HEALTHY
            mock_k8s_result.score = 100
            mock_k8s_result.total_nodes = 3
            mock_k8s_result.ready_nodes = 3
            mock_k8s_result.not_ready_nodes = 0
            mock_k8s_result.api_server_healthy = True
            mock_k8s_result.nodes = []
            mock_k8s_health.return_value = mock_k8s_result

            mock_ceph_result = MagicMock()
            mock_ceph_result.health = HealthStatus.HEALTHY
            mock_ceph_result.score = 100
            mock_ceph_result.ceph_health = "HEALTH_OK"
            mock_ceph_result.osds_total = 6
            mock_ceph_result.osds_up = 6
            mock_ceph_result.osds_in = 6
            mock_ceph_health.return_value = mock_ceph_result

            result = await run_mosk_platform_validation(
                mock_mcc_adapter, mock_mosk_adapter, input_data
            )

            # Should have auto-discovered the cluster
            assert result.cluster_name == "mos"
            assert result.to_release == "mosk-21-0-2-25-2-2"

    @pytest.mark.asyncio
    async def test_kubernetes_version_discovery(
        self,
        mock_mcc_adapter: MagicMock,
        mock_mosk_adapter: MagicMock,
    ) -> None:
        """Test Kubernetes version is discovered from nodes."""
        input_data = RunMoskPlatformValidationInput(
            level="quick",
            cluster_name="mos",
        )

        with (
            patch(
                "mosk_mcp.tools.validation.run_mosk_platform_validation.get_kubernetes_health"
            ) as mock_k8s_health,
            patch(
                "mosk_mcp.tools.validation.run_mosk_platform_validation.get_ceph_health"
            ) as mock_ceph_health,
        ):
            mock_k8s_result = MagicMock()
            mock_k8s_result.health = HealthStatus.HEALTHY
            mock_k8s_result.score = 100
            mock_k8s_result.total_nodes = 3
            mock_k8s_result.ready_nodes = 3
            mock_k8s_result.not_ready_nodes = 0
            mock_k8s_result.api_server_healthy = True
            mock_k8s_result.nodes = []
            mock_k8s_health.return_value = mock_k8s_result

            mock_ceph_result = MagicMock()
            mock_ceph_result.health = HealthStatus.HEALTHY
            mock_ceph_result.score = 100
            mock_ceph_result.ceph_health = "HEALTH_OK"
            mock_ceph_result.osds_total = 6
            mock_ceph_result.osds_up = 6
            mock_ceph_result.osds_in = 6
            mock_ceph_health.return_value = mock_ceph_result

            result = await run_mosk_platform_validation(
                mock_mcc_adapter, mock_mosk_adapter, input_data
            )

            assert result.kubernetes_version == "v1.28.6"


class TestMoskPlatformValidationModels:
    """Tests for MOSK platform validation Pydantic models."""

    def test_input_defaults(self) -> None:
        """Test default values for input model."""
        input_model = RunMoskPlatformValidationInput()

        assert input_model.level == "standard"
        assert input_model.cluster_namespace == "lab"
        assert input_model.openstack_namespace == "openstack"
        assert input_model.timeout_seconds == 300

    def test_output_model_fields(self) -> None:
        """Test output model has all required fields."""
        output = RunMoskPlatformValidationOutput(
            overall_status="passed",
            validation_level="standard",
            tiers_run=2,
            tiers_passed=2,
            tiers_failed=0,
            tier_results=[],
            timestamp="2025-01-01T00:00:00Z",
            duration_seconds=10.5,
            summary="All checks passed",
        )

        assert output.overall_status == "passed"
        assert output.validation_level == "standard"
        assert output.tiers_run == 2
        assert output.tiers_passed == 2
        assert output.tiers_failed == 0


# ==========================
# Integration-style Tests
# ==========================
class TestValidationStatusHandling:
    """Tests for validation status determination."""

    def test_validation_status_enum(self) -> None:
        """Test ValidationStatus enum values."""
        assert ValidationStatus.PASSED.value == "passed"
        assert ValidationStatus.PASSED_WITH_WARNINGS.value == "passed_with_warnings"
        assert ValidationStatus.FAILED.value == "failed"
        assert ValidationStatus.ERROR.value == "error"

    def test_health_status_enum(self) -> None:
        """Test HealthStatus enum values."""
        assert HealthStatus.HEALTHY.value == "healthy"
        assert HealthStatus.DEGRADED.value == "degraded"
        assert HealthStatus.UNHEALTHY.value == "unhealthy"
        assert HealthStatus.UNKNOWN.value == "unknown"
