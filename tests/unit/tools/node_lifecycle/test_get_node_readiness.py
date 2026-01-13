"""Unit tests for get_node_readiness tool."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from mosk_mcp.core.exceptions import ResourceNotFoundError
from mosk_mcp.tools.node_lifecycle.get_node_readiness import (
    CheckSeverity,
    GetNodeReadinessInput,
    GetNodeReadinessOutput,
    NodeConditionStatus,
    ReadinessCheck,
    ReadinessCheckType,
    _check_ceph_health,
    _check_machine_status,
    _check_node_conditions,
    _check_openstack_services,
    _check_pending_pods,
    get_node_readiness,
)


SAMPLE_MACHINE_DATA = {
    "apiVersion": "kaas.mirantis.com/v1alpha1",
    "kind": "Machine",
    "metadata": {
        "name": "compute-01",
        "namespace": "default",
    },
    "status": {
        "phase": "Running",
        "nodeRef": {"name": "compute-01"},
    },
}


SAMPLE_NODE_DATA = {
    "apiVersion": "v1",
    "kind": "Node",
    "metadata": {
        "name": "compute-01",
    },
    "status": {
        "conditions": [
            {
                "type": "Ready",
                "status": "True",
                "reason": "KubeletReady",
                "message": "kubelet is posting ready status",
            },
            {
                "type": "MemoryPressure",
                "status": "False",
                "reason": "KubeletHasSufficientMemory",
            },
            {
                "type": "DiskPressure",
                "status": "False",
                "reason": "KubeletHasNoDiskPressure",
            },
            {
                "type": "PIDPressure",
                "status": "False",
                "reason": "KubeletHasSufficientPID",
            },
        ],
    },
}


SAMPLE_UNHEALTHY_NODE_DATA = {
    "apiVersion": "v1",
    "kind": "Node",
    "metadata": {
        "name": "compute-02",
    },
    "status": {
        "conditions": [
            {
                "type": "Ready",
                "status": "False",
                "reason": "KubeletNotReady",
                "message": "Node not responding",
            },
            {
                "type": "MemoryPressure",
                "status": "True",
                "reason": "KubeletHasInsufficientMemory",
            },
        ],
    },
}


class TestCheckNodeConditions:
    """Tests for _check_node_conditions function."""

    @pytest.fixture
    def mock_k8s_adapter(self):
        """Create a mock Kubernetes adapter."""
        adapter = MagicMock()
        adapter.get = AsyncMock(return_value=SAMPLE_NODE_DATA)
        return adapter

    @pytest.mark.asyncio
    async def test_check_healthy_node_conditions(self, mock_k8s_adapter):
        """Test checking conditions on a healthy node."""
        conditions, checks = await _check_node_conditions(
            mock_k8s_adapter,
            "compute-01",
        )

        # Should have conditions
        assert len(conditions) > 0
        assert all(isinstance(c, NodeConditionStatus) for c in conditions)

        # Ready condition should be healthy
        ready_condition = next(c for c in conditions if c.type == "Ready")
        assert ready_condition.status == "True"
        assert ready_condition.is_healthy is True

        # Checks should all pass for healthy node
        assert all(c.passed for c in checks)

    @pytest.mark.asyncio
    async def test_check_unhealthy_node_conditions(self, mock_k8s_adapter):
        """Test checking conditions on an unhealthy node."""
        mock_k8s_adapter.get = AsyncMock(return_value=SAMPLE_UNHEALTHY_NODE_DATA)

        conditions, checks = await _check_node_conditions(
            mock_k8s_adapter,
            "compute-02",
        )

        # Ready condition should be unhealthy
        ready_condition = next(c for c in conditions if c.type == "Ready")
        assert ready_condition.status == "False"
        assert ready_condition.is_healthy is False

        # Should have failing checks
        failed_checks = [c for c in checks if not c.passed]
        assert len(failed_checks) > 0

    @pytest.mark.asyncio
    async def test_check_node_not_found(self, mock_k8s_adapter):
        """Test checking conditions when node doesn't exist."""
        mock_k8s_adapter.get = AsyncMock(
            side_effect=ResourceNotFoundError(
                "Node not found",
                resource_type="Node",
                resource_id="nonexistent",
            )
        )

        conditions, checks = await _check_node_conditions(
            mock_k8s_adapter,
            "nonexistent",
        )

        # Should have no conditions
        assert len(conditions) == 0

        # Should have a failing check
        assert len(checks) == 1
        assert checks[0].severity == CheckSeverity.FAIL
        assert not checks[0].passed


class TestCheckPendingPods:
    """Tests for _check_pending_pods function."""

    @pytest.fixture
    def mock_k8s_adapter(self):
        """Create a mock Kubernetes adapter."""
        adapter = MagicMock()
        adapter.list = AsyncMock(return_value=[])
        return adapter

    @pytest.mark.asyncio
    async def test_check_no_pods(self, mock_k8s_adapter):
        """Test checking node with no pods."""
        checks = await _check_pending_pods(
            mock_k8s_adapter,
            "compute-01",
            ReadinessCheckType.GENERAL,
        )

        # Should have pod summary check
        assert len(checks) > 0
        summary_check = next(c for c in checks if c.name == "pod_summary")
        assert summary_check.passed is True

    @pytest.mark.asyncio
    async def test_check_running_pods(self, mock_k8s_adapter):
        """Test checking node with running pods."""
        mock_k8s_adapter.list = AsyncMock(
            return_value=[
                {
                    "metadata": {"name": "pod-1", "namespace": "default"},
                    "status": {"phase": "Running"},
                },
                {
                    "metadata": {"name": "pod-2", "namespace": "default"},
                    "status": {"phase": "Running"},
                },
            ]
        )

        checks = await _check_pending_pods(
            mock_k8s_adapter,
            "compute-01",
            ReadinessCheckType.GENERAL,
        )

        summary_check = next(c for c in checks if c.name == "pod_summary")
        assert summary_check.details["running"] == 2

    @pytest.mark.asyncio
    async def test_check_pending_pods_warning(self, mock_k8s_adapter):
        """Test checking node with pending pods generates warning."""
        mock_k8s_adapter.list = AsyncMock(
            return_value=[
                {
                    "metadata": {"name": "pod-1", "namespace": "default"},
                    "status": {"phase": "Pending"},
                },
            ]
        )

        checks = await _check_pending_pods(
            mock_k8s_adapter,
            "compute-01",
            ReadinessCheckType.GENERAL,
        )

        pending_check = next((c for c in checks if c.name == "pending_pods"), None)
        assert pending_check is not None
        assert pending_check.severity == CheckSeverity.WARNING

    @pytest.mark.asyncio
    async def test_check_critical_pods_for_drain(self, mock_k8s_adapter):
        """Test that critical pods are reported for drain operations."""
        mock_k8s_adapter.list = AsyncMock(
            return_value=[
                {
                    "metadata": {"name": "kube-proxy-xxx", "namespace": "kube-system"},
                    "status": {"phase": "Running"},
                },
            ]
        )

        checks = await _check_pending_pods(
            mock_k8s_adapter,
            "compute-01",
            ReadinessCheckType.DRAIN,
        )

        critical_check = next((c for c in checks if c.name == "critical_pods"), None)
        assert critical_check is not None
        assert critical_check.details["critical_count"] == 1


class TestCheckMachineStatus:
    """Tests for _check_machine_status function."""

    @pytest.fixture
    def mock_k8s_adapter(self):
        """Create a mock Kubernetes adapter."""
        adapter = MagicMock()
        adapter.get_machine = AsyncMock(return_value=SAMPLE_MACHINE_DATA)
        return adapter

    @pytest.mark.asyncio
    async def test_check_running_machine(self, mock_k8s_adapter):
        """Test checking a running machine."""
        node_name, checks = await _check_machine_status(
            mock_k8s_adapter,
            "compute-01",
            "default",
        )

        assert node_name == "compute-01"

        # Phase check should pass
        phase_check = next(c for c in checks if c.name == "machine_phase")
        assert phase_check.passed is True

        # Node ref check should pass
        node_ref_check = next(c for c in checks if c.name == "machine_node_ref")
        assert node_ref_check.passed is True

    @pytest.mark.asyncio
    async def test_check_failed_machine(self, mock_k8s_adapter):
        """Test checking a failed machine."""
        mock_k8s_adapter.get_machine = AsyncMock(
            return_value={
                "metadata": {"name": "compute-02"},
                "status": {
                    "phase": "Failed",
                    "errorReason": "ProvisioningFailed",
                    "errorMessage": "Hardware error",
                },
            }
        )

        _node_name, checks = await _check_machine_status(
            mock_k8s_adapter,
            "compute-02",
            "default",
        )

        # Should have error check
        error_check = next((c for c in checks if c.name == "machine_errors"), None)
        assert error_check is not None
        assert error_check.severity == CheckSeverity.FAIL

    @pytest.mark.asyncio
    async def test_check_machine_not_found(self, mock_k8s_adapter):
        """Test checking non-existent machine."""
        mock_k8s_adapter.get_machine = AsyncMock(
            side_effect=ResourceNotFoundError(
                "Machine not found",
                resource_type="Machine",
                resource_id="nonexistent",
            )
        )

        node_name, checks = await _check_machine_status(
            mock_k8s_adapter,
            "nonexistent",
            "default",
        )

        assert node_name is None
        assert len(checks) == 1
        assert checks[0].name == "machine_exists"
        assert not checks[0].passed


class TestCheckCephHealth:
    """Tests for _check_ceph_health function."""

    @pytest.fixture
    def mock_k8s_adapter(self):
        """Create a mock Kubernetes adapter."""
        adapter = MagicMock()
        adapter.list = AsyncMock(return_value=[])
        return adapter

    @pytest.mark.asyncio
    async def test_check_no_osds(self, mock_k8s_adapter):
        """Test checking node with no Ceph OSDs."""
        checks = await _check_ceph_health(mock_k8s_adapter, "compute-01")

        assert len(checks) == 1
        assert checks[0].name == "ceph_osds_on_node"
        assert checks[0].passed is True
        assert checks[0].details["osd_count"] == 0

    @pytest.mark.asyncio
    async def test_check_with_osds(self, mock_k8s_adapter):
        """Test checking node with Ceph OSDs."""
        mock_k8s_adapter.list = AsyncMock(
            return_value=[
                {"metadata": {"name": "rook-ceph-osd-0"}},
                {"metadata": {"name": "rook-ceph-osd-1"}},
            ]
        )

        checks = await _check_ceph_health(mock_k8s_adapter, "storage-01")

        assert len(checks) == 1
        assert checks[0].severity == CheckSeverity.INFO
        assert checks[0].details["osd_count"] == 2


class TestCheckOpenStackServices:
    """Tests for _check_openstack_services function."""

    @pytest.fixture
    def mock_k8s_adapter(self):
        """Create a mock Kubernetes adapter."""
        adapter = MagicMock()
        adapter.list = AsyncMock(return_value=[])
        return adapter

    @pytest.mark.asyncio
    async def test_check_no_openstack_pods(self, mock_k8s_adapter):
        """Test checking node with no OpenStack pods."""
        checks = await _check_openstack_services(mock_k8s_adapter, "compute-01")

        summary_check = next(c for c in checks if c.name == "openstack_pods_summary")
        assert summary_check.passed is True
        assert summary_check.details["total_openstack_pods"] == 0

    @pytest.mark.asyncio
    async def test_check_with_nova_compute(self, mock_k8s_adapter):
        """Test checking node with nova-compute pod."""
        mock_k8s_adapter.list = AsyncMock(
            return_value=[
                {"metadata": {"name": "nova-compute-default-xxx", "namespace": "openstack"}},
            ]
        )

        checks = await _check_openstack_services(mock_k8s_adapter, "compute-01")

        nova_check = next((c for c in checks if c.name == "nova_compute_on_node"), None)
        assert nova_check is not None
        assert nova_check.severity == CheckSeverity.INFO

    @pytest.mark.asyncio
    async def test_check_with_neutron_agents(self, mock_k8s_adapter):
        """Test checking node with Neutron agents."""
        mock_k8s_adapter.list = AsyncMock(
            return_value=[
                {"metadata": {"name": "neutron-l3-agent-xxx", "namespace": "openstack"}},
                {"metadata": {"name": "neutron-dhcp-agent-xxx", "namespace": "openstack"}},
            ]
        )

        checks = await _check_openstack_services(mock_k8s_adapter, "network-01")

        neutron_check = next((c for c in checks if c.name == "neutron_agents_on_node"), None)
        assert neutron_check is not None
        assert neutron_check.details["network_pod_count"] == 2


class TestGetNodeReadiness:
    """Tests for get_node_readiness function."""

    @pytest.fixture
    def mock_k8s_adapter(self):
        """Create a mock Kubernetes adapter."""
        adapter = MagicMock()
        adapter.get_machine = AsyncMock(return_value=SAMPLE_MACHINE_DATA)
        adapter.get = AsyncMock(return_value=SAMPLE_NODE_DATA)
        adapter.list = AsyncMock(return_value=[])
        # Mock auto-discovery to return None (use default namespace)
        adapter.get_mosk_machines_namespace = AsyncMock(return_value=None)
        return adapter

    @pytest.mark.asyncio
    async def test_get_node_readiness_healthy(self, mock_k8s_adapter):
        """Test getting readiness of a healthy node."""
        input_data = GetNodeReadinessInput(
            name="compute-01",
            namespace="default",
            check_type=ReadinessCheckType.GENERAL,
        )

        result = await get_node_readiness(mock_k8s_adapter, input_data)

        assert isinstance(result, GetNodeReadinessOutput)
        assert result.name == "compute-01"
        assert result.node_name == "compute-01"
        assert result.is_ready is True
        assert result.ready_for_operation is True
        assert len(result.blocking_issues) == 0

    @pytest.mark.asyncio
    async def test_get_node_readiness_with_blocking_issues(self, mock_k8s_adapter):
        """Test getting readiness when there are blocking issues."""
        mock_k8s_adapter.get = AsyncMock(return_value=SAMPLE_UNHEALTHY_NODE_DATA)

        input_data = GetNodeReadinessInput(
            name="compute-02",
            check_type=ReadinessCheckType.MAINTENANCE,
        )

        result = await get_node_readiness(mock_k8s_adapter, input_data)

        assert result.is_ready is False
        assert result.ready_for_operation is False
        assert len(result.blocking_issues) > 0

    @pytest.mark.asyncio
    async def test_get_node_readiness_without_ceph_check(self, mock_k8s_adapter):
        """Test getting readiness without Ceph checks."""
        input_data = GetNodeReadinessInput(
            name="compute-01",
            check_ceph=False,
            check_openstack=False,
        )

        result = await get_node_readiness(mock_k8s_adapter, input_data)

        # Should not include Ceph checks
        ceph_checks = [c for c in result.checks if "ceph" in c.name.lower()]
        assert len(ceph_checks) == 0

    @pytest.mark.asyncio
    async def test_get_node_readiness_summary(self, mock_k8s_adapter):
        """Test that summary is generated correctly."""
        input_data = GetNodeReadinessInput(
            name="compute-01",
        )

        result = await get_node_readiness(mock_k8s_adapter, input_data)

        assert "passed" in result.summary
        assert "failed" in result.summary
        assert "warnings" in result.summary
        assert "total" in result.summary

    @pytest.mark.asyncio
    async def test_get_node_readiness_recommendations(self, mock_k8s_adapter):
        """Test that recommendations are generated for maintenance."""
        input_data = GetNodeReadinessInput(
            name="compute-01",
            check_type=ReadinessCheckType.MAINTENANCE,
        )

        result = await get_node_readiness(mock_k8s_adapter, input_data)

        assert len(result.recommendations) > 0
        assert any("maintenance" in r.lower() for r in result.recommendations)


class TestGetNodeReadinessInput:
    """Tests for GetNodeReadinessInput validation."""

    def test_required_name(self):
        """Test that name is required."""
        with pytest.raises(ValueError):
            GetNodeReadinessInput(name="")

    def test_default_values(self):
        """Test default values."""
        input_data = GetNodeReadinessInput(name="test-node")

        assert input_data.namespace == "default"
        assert input_data.check_type == ReadinessCheckType.GENERAL
        assert input_data.check_ceph is True
        assert input_data.check_openstack is True

    def test_custom_check_type(self):
        """Test custom check type."""
        input_data = GetNodeReadinessInput(
            name="test-node",
            check_type=ReadinessCheckType.DRAIN,
        )

        assert input_data.check_type == ReadinessCheckType.DRAIN


class TestNodeConditionStatus:
    """Tests for NodeConditionStatus model."""

    def test_condition_fields(self):
        """Test condition model fields."""
        condition = NodeConditionStatus(
            type="Ready",
            status="True",
            reason="KubeletReady",
            message="kubelet is ready",
            is_healthy=True,
        )

        assert condition.type == "Ready"
        assert condition.status == "True"
        assert condition.is_healthy is True

    def test_condition_optional_fields(self):
        """Test condition with optional fields."""
        condition = NodeConditionStatus(
            type="Custom",
            status="Unknown",
            is_healthy=False,
        )

        assert condition.reason is None
        assert condition.message is None


class TestReadinessCheck:
    """Tests for ReadinessCheck model."""

    def test_check_fields(self):
        """Test check model fields."""
        check = ReadinessCheck(
            name="test_check",
            description="Test check description",
            severity=CheckSeverity.PASS,
            passed=True,
            message="Check passed",
        )

        assert check.name == "test_check"
        assert check.severity == CheckSeverity.PASS
        assert check.passed is True

    def test_check_with_remediation(self):
        """Test check with remediation."""
        check = ReadinessCheck(
            name="failed_check",
            description="A failing check",
            severity=CheckSeverity.FAIL,
            passed=False,
            message="Check failed",
            remediation="Do this to fix it",
        )

        assert check.remediation == "Do this to fix it"

    def test_check_default_details(self):
        """Test check default details."""
        check = ReadinessCheck(
            name="check",
            description="desc",
            severity=CheckSeverity.PASS,
            passed=True,
            message="ok",
        )

        assert check.details == {}
        assert check.remediation is None
