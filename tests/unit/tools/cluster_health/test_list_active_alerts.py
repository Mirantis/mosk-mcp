"""Unit tests for list_active_alerts tool."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mosk_mcp.tools.cluster_health.list_active_alerts import (
    _classify_component,
    _severity_to_enum,
    list_active_alerts,
)
from mosk_mcp.tools.cluster_health.models import ListActiveAlertsInput
from mosk_mcp.tools.common.enums import AlertSeverity


class TestClassifyComponent:
    """Tests for _classify_component function."""

    def test_classify_kubernetes_alert(self) -> None:
        """Test classification of kubernetes alerts."""
        assert _classify_component("KubeNodeNotReady", {}) == "kubernetes"
        assert _classify_component("KubeletDown", {}) == "kubernetes"
        assert _classify_component("PodCrashLooping", {}) == "kubernetes"
        assert _classify_component("ContainerOOMKilled", {}) == "kubernetes"
        assert _classify_component("EtcdHighLatency", {}) == "kubernetes"

    def test_classify_openstack_alert(self) -> None:
        """Test classification of openstack alerts."""
        assert _classify_component("NovaComputeDown", {}) == "openstack"
        assert _classify_component("NeutronAgentDown", {}) == "openstack"
        assert _classify_component("KeystoneApiError", {}) == "openstack"
        assert _classify_component("GlanceServiceDown", {}) == "openstack"
        assert _classify_component("CinderVolumeError", {}) == "openstack"

    def test_classify_ceph_alert(self) -> None:
        """Test classification of ceph alerts."""
        assert _classify_component("CephOSDDown", {}) == "ceph"
        assert _classify_component("CephMonitorDown", {}) == "ceph"
        assert _classify_component("CephCapacityWarning", {}) == "ceph"
        assert _classify_component("StorageCapacityLow", {}) == "ceph"

    def test_classify_stacklight_alert(self) -> None:
        """Test classification of stacklight alerts."""
        assert _classify_component("PrometheusTargetDown", {}) == "stacklight"
        assert _classify_component("AlertmanagerDown", {}) == "stacklight"
        assert _classify_component("OpenSearchClusterRed", {}) == "stacklight"
        assert _classify_component("GrafanaError", {}) == "stacklight"

    def test_classify_from_labels(self) -> None:
        """Test classification from labels."""
        assert _classify_component("GenericAlert", {"service": "nova-api"}) == "openstack"
        assert _classify_component("GenericAlert", {"component": "kubelet"}) == "kubernetes"

    def test_classify_other(self) -> None:
        """Test classification of unknown alerts."""
        assert _classify_component("CustomAlert", {}) == "other"
        assert _classify_component("UnknownError", {"app": "myapp"}) == "other"


class TestSeverityToEnum:
    """Tests for _severity_to_enum function."""

    def test_critical_severity(self) -> None:
        """Test critical severity mapping."""
        assert _severity_to_enum("critical") == AlertSeverity.CRITICAL
        assert _severity_to_enum("CRITICAL") == AlertSeverity.CRITICAL

    def test_warning_severity(self) -> None:
        """Test warning severity mapping."""
        assert _severity_to_enum("warning") == AlertSeverity.WARNING
        assert _severity_to_enum("WARNING") == AlertSeverity.WARNING

    def test_info_severity(self) -> None:
        """Test info severity mapping."""
        assert _severity_to_enum("info") == AlertSeverity.INFO
        assert _severity_to_enum("INFO") == AlertSeverity.INFO

    def test_none_severity(self) -> None:
        """Test unknown severity mapping."""
        assert _severity_to_enum("unknown") == AlertSeverity.NONE
        assert _severity_to_enum("") == AlertSeverity.NONE


class TestListActiveAlerts:
    """Tests for list_active_alerts function."""

    @pytest.fixture
    def mock_direct_client(self) -> AsyncMock:
        """Create mock DirectStackLightClient."""
        return AsyncMock()

    @pytest.fixture
    def mock_alert(self) -> MagicMock:
        """Create a mock alert."""
        alert = MagicMock()
        alert.alert_name = "CephOSDDown"
        alert.severity = MagicMock(value="critical")
        alert.state = MagicMock(value="firing")
        alert.summary = "OSD 5 is down"
        alert.description = "OSD 5 on node1 is not responding"
        alert.labels = {"severity": "critical", "alertname": "CephOSDDown"}
        alert.annotations = {"summary": "OSD 5 is down"}
        alert.starts_at = datetime(2024, 1, 1, 12, 0, 0)
        alert.fingerprint = "abc123"
        return alert

    @pytest.mark.asyncio
    async def test_list_alerts_success(
        self, mock_direct_client: AsyncMock, mock_alert: MagicMock
    ) -> None:
        """Test successful alert listing."""
        mock_adapter = AsyncMock()
        mock_adapter.get_alerts.return_value = [mock_alert]

        with patch(
            "mosk_mcp.tools.cluster_health.list_active_alerts.StackLightAdapter",
            return_value=mock_adapter,
        ):
            input_data = ListActiveAlertsInput()
            result = await list_active_alerts(mock_direct_client, input_data)

        assert result.total_count == 1
        assert result.critical_count == 1
        assert len(result.alerts) == 1
        assert result.alerts[0].alert_name == "CephOSDDown"

    @pytest.mark.asyncio
    async def test_list_alerts_with_severity_filter(self, mock_direct_client: AsyncMock) -> None:
        """Test alert listing with severity filter."""
        critical_alert = MagicMock()
        critical_alert.alert_name = "CephOSDDown"
        critical_alert.severity = MagicMock(value="critical")
        critical_alert.state = MagicMock(value="firing")
        critical_alert.summary = "OSD down"
        critical_alert.description = "OSD is down"
        critical_alert.labels = {}
        critical_alert.annotations = {}
        critical_alert.starts_at = datetime.now()
        critical_alert.fingerprint = "abc"

        warning_alert = MagicMock()
        warning_alert.alert_name = "DiskSpaceWarning"
        warning_alert.severity = MagicMock(value="warning")
        warning_alert.state = MagicMock(value="firing")
        warning_alert.summary = "Disk space low"
        warning_alert.description = "Disk space is low"
        warning_alert.labels = {}
        warning_alert.annotations = {}
        warning_alert.starts_at = datetime.now()
        warning_alert.fingerprint = "def"

        mock_adapter = AsyncMock()
        mock_adapter.get_alerts.return_value = [critical_alert, warning_alert]

        with patch(
            "mosk_mcp.tools.cluster_health.list_active_alerts.StackLightAdapter",
            return_value=mock_adapter,
        ):
            input_data = ListActiveAlertsInput(severity_filter=AlertSeverity.CRITICAL)
            result = await list_active_alerts(mock_direct_client, input_data)

        assert result.total_count == 1
        assert result.critical_count == 1
        assert all(a.severity == AlertSeverity.CRITICAL for a in result.alerts)

    @pytest.mark.asyncio
    async def test_list_alerts_with_component_filter(self, mock_direct_client: AsyncMock) -> None:
        """Test alert listing with component filter."""
        ceph_alert = MagicMock()
        ceph_alert.alert_name = "CephOSDDown"
        ceph_alert.severity = MagicMock(value="critical")
        ceph_alert.state = MagicMock(value="firing")
        ceph_alert.summary = "OSD down"
        ceph_alert.description = "OSD is down"
        ceph_alert.labels = {}
        ceph_alert.annotations = {}
        ceph_alert.starts_at = datetime.now()
        ceph_alert.fingerprint = "abc"

        k8s_alert = MagicMock()
        k8s_alert.alert_name = "KubeNodeNotReady"
        k8s_alert.severity = MagicMock(value="warning")
        k8s_alert.state = MagicMock(value="firing")
        k8s_alert.summary = "Node not ready"
        k8s_alert.description = "Node is not ready"
        k8s_alert.labels = {}
        k8s_alert.annotations = {}
        k8s_alert.starts_at = datetime.now()
        k8s_alert.fingerprint = "def"

        mock_adapter = AsyncMock()
        mock_adapter.get_alerts.return_value = [ceph_alert, k8s_alert]

        with patch(
            "mosk_mcp.tools.cluster_health.list_active_alerts.StackLightAdapter",
            return_value=mock_adapter,
        ):
            input_data = ListActiveAlertsInput(component_filter="ceph")
            result = await list_active_alerts(mock_direct_client, input_data)

        assert result.total_count == 1
        assert all(a.component == "ceph" for a in result.alerts)

    @pytest.mark.asyncio
    async def test_list_alerts_empty(self, mock_direct_client: AsyncMock) -> None:
        """Test alert listing with no alerts."""
        mock_adapter = AsyncMock()
        mock_adapter.get_alerts.return_value = []

        with patch(
            "mosk_mcp.tools.cluster_health.list_active_alerts.StackLightAdapter",
            return_value=mock_adapter,
        ):
            input_data = ListActiveAlertsInput()
            result = await list_active_alerts(mock_direct_client, input_data)

        assert result.total_count == 0
        assert result.critical_count == 0
        assert result.warning_count == 0
        assert result.alerts == []

    @pytest.mark.asyncio
    async def test_list_alerts_with_limit(self, mock_direct_client: AsyncMock) -> None:
        """Test alert listing with limit."""
        alerts = []
        for i in range(10):
            alert = MagicMock()
            alert.alert_name = f"Alert{i}"
            alert.severity = MagicMock(value="warning")
            alert.state = MagicMock(value="firing")
            alert.summary = f"Alert {i}"
            alert.description = f"Description {i}"
            alert.labels = {}
            alert.annotations = {}
            alert.starts_at = datetime.now()
            alert.fingerprint = f"fp{i}"
            alerts.append(alert)

        mock_adapter = AsyncMock()
        mock_adapter.get_alerts.return_value = alerts

        with patch(
            "mosk_mcp.tools.cluster_health.list_active_alerts.StackLightAdapter",
            return_value=mock_adapter,
        ):
            input_data = ListActiveAlertsInput(limit=5)
            result = await list_active_alerts(mock_direct_client, input_data)

        assert len(result.alerts) == 5

    @pytest.mark.asyncio
    async def test_list_alerts_by_component_counts(self, mock_direct_client: AsyncMock) -> None:
        """Test alert counts by component."""
        ceph_alert = MagicMock()
        ceph_alert.alert_name = "CephOSDDown"
        ceph_alert.severity = MagicMock(value="warning")
        ceph_alert.state = MagicMock(value="firing")
        ceph_alert.summary = "OSD down"
        ceph_alert.description = ""
        ceph_alert.labels = {}
        ceph_alert.annotations = {}
        ceph_alert.starts_at = datetime.now()
        ceph_alert.fingerprint = "abc"

        k8s_alert = MagicMock()
        k8s_alert.alert_name = "KubeNodeNotReady"
        k8s_alert.severity = MagicMock(value="warning")
        k8s_alert.state = MagicMock(value="firing")
        k8s_alert.summary = "Node not ready"
        k8s_alert.description = ""
        k8s_alert.labels = {}
        k8s_alert.annotations = {}
        k8s_alert.starts_at = datetime.now()
        k8s_alert.fingerprint = "def"

        mock_adapter = AsyncMock()
        mock_adapter.get_alerts.return_value = [ceph_alert, k8s_alert]

        with patch(
            "mosk_mcp.tools.cluster_health.list_active_alerts.StackLightAdapter",
            return_value=mock_adapter,
        ):
            input_data = ListActiveAlertsInput()
            result = await list_active_alerts(mock_direct_client, input_data)

        assert result.by_component["ceph"] == 1
        assert result.by_component["kubernetes"] == 1

    @pytest.mark.asyncio
    async def test_list_alerts_most_critical(self, mock_direct_client: AsyncMock) -> None:
        """Test most critical alerts are returned."""
        alerts = []
        for i in range(3):
            alert = MagicMock()
            alert.alert_name = f"CriticalAlert{i}"
            alert.severity = MagicMock(value="critical")
            alert.state = MagicMock(value="firing")
            alert.summary = f"Critical issue {i}"
            alert.description = ""
            alert.labels = {}
            alert.annotations = {}
            alert.starts_at = datetime.now()
            alert.fingerprint = f"fp{i}"
            alerts.append(alert)

        mock_adapter = AsyncMock()
        mock_adapter.get_alerts.return_value = alerts

        with patch(
            "mosk_mcp.tools.cluster_health.list_active_alerts.StackLightAdapter",
            return_value=mock_adapter,
        ):
            input_data = ListActiveAlertsInput()
            result = await list_active_alerts(mock_direct_client, input_data)

        assert len(result.most_critical) == 3
        assert all("Critical issue" in s for s in result.most_critical)
