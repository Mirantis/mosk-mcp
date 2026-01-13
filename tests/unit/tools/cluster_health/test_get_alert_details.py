"""Unit tests for get_alert_details tool."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mosk_mcp.core.exceptions import ResourceNotFoundError, ToolExecutionError
from mosk_mcp.tools.cluster_health.get_alert_details import (
    RELATED_ALERTS,
    RUNBOOKS,
    SUGGESTED_ACTIONS,
    _query_alert_from_alertmanager,
    _query_alert_history,
    get_alert_details,
)
from mosk_mcp.tools.cluster_health.models import AlertHistoryEntry
from mosk_mcp.tools.common.enums import AlertSeverity, AlertState


class TestRunbooksAndSuggestedActions:
    """Tests for runbooks and suggested actions constants."""

    def test_runbooks_exist_for_common_alerts(self) -> None:
        """Test that runbooks are defined for common alerts."""
        assert "KubeNodeNotReady" in RUNBOOKS
        assert "CephOSDDown" in RUNBOOKS
        assert "NovaComputeDown" in RUNBOOKS

    def test_runbook_urls_are_valid(self) -> None:
        """Test that runbook URLs are valid."""
        for url in RUNBOOKS.values():
            assert url.startswith("https://")
            assert "mirantis.com" in url

    def test_suggested_actions_exist_for_common_alerts(self) -> None:
        """Test that suggested actions are defined for common alerts."""
        assert "KubeNodeNotReady" in SUGGESTED_ACTIONS
        assert "CephOSDDown" in SUGGESTED_ACTIONS
        assert "NovaComputeDown" in SUGGESTED_ACTIONS
        assert "CephCapacityWarning" in SUGGESTED_ACTIONS
        assert "KubePodCrashLooping" in SUGGESTED_ACTIONS

    def test_suggested_actions_are_lists(self) -> None:
        """Test that suggested actions are lists of strings."""
        for actions in SUGGESTED_ACTIONS.values():
            assert isinstance(actions, list)
            assert all(isinstance(action, str) for action in actions)
            assert len(actions) >= 1

    def test_related_alerts_exist_for_common_alerts(self) -> None:
        """Test that related alerts are defined."""
        assert "KubeNodeNotReady" in RELATED_ALERTS
        assert "CephOSDDown" in RELATED_ALERTS


class TestQueryAlertFromAlertmanager:
    """Tests for _query_alert_from_alertmanager function."""

    @pytest.fixture
    def mock_direct_client(self) -> AsyncMock:
        """Create mock DirectStackLightClient."""
        return AsyncMock()

    @pytest.fixture
    def mock_alert(self) -> MagicMock:
        """Create mock alert."""
        alert = MagicMock()
        alert.to_dict.return_value = {
            "labels": {"alertname": "CephOSDDown", "severity": "critical"},
            "annotations": {"summary": "OSD is down"},
            "fingerprint": "abc123",
            "status": {"state": "firing"},
        }
        return alert

    @pytest.mark.asyncio
    async def test_query_alert_found(
        self, mock_direct_client: AsyncMock, mock_alert: MagicMock
    ) -> None:
        """Test successful alert query."""
        with patch(
            "mosk_mcp.tools.cluster_health.get_alert_details.StackLightAdapter"
        ) as MockAdapter:
            mock_adapter = AsyncMock()
            mock_adapter.get_alerts.return_value = [mock_alert]
            MockAdapter.return_value = mock_adapter

            result = await _query_alert_from_alertmanager(mock_direct_client, "CephOSDDown")

        assert result is not None
        assert result["labels"]["alertname"] == "CephOSDDown"

    @pytest.mark.asyncio
    async def test_query_alert_not_found(self, mock_direct_client: AsyncMock) -> None:
        """Test alert not found."""
        with patch(
            "mosk_mcp.tools.cluster_health.get_alert_details.StackLightAdapter"
        ) as MockAdapter:
            mock_adapter = AsyncMock()
            mock_adapter.get_alerts.return_value = []
            MockAdapter.return_value = mock_adapter

            result = await _query_alert_from_alertmanager(mock_direct_client, "NonExistent")

        assert result is None

    @pytest.mark.asyncio
    async def test_query_alert_with_fingerprint(
        self, mock_direct_client: AsyncMock, mock_alert: MagicMock
    ) -> None:
        """Test query with fingerprint match."""
        with patch(
            "mosk_mcp.tools.cluster_health.get_alert_details.StackLightAdapter"
        ) as MockAdapter:
            mock_adapter = AsyncMock()
            mock_adapter.get_alerts.return_value = [mock_alert]
            MockAdapter.return_value = mock_adapter

            result = await _query_alert_from_alertmanager(
                mock_direct_client, "CephOSDDown", fingerprint="abc123"
            )

        assert result is not None
        assert result["fingerprint"] == "abc123"

    @pytest.mark.asyncio
    async def test_query_alert_with_wrong_fingerprint(
        self, mock_direct_client: AsyncMock, mock_alert: MagicMock
    ) -> None:
        """Test query with wrong fingerprint."""
        with patch(
            "mosk_mcp.tools.cluster_health.get_alert_details.StackLightAdapter"
        ) as MockAdapter:
            mock_adapter = AsyncMock()
            mock_adapter.get_alerts.return_value = [mock_alert]
            MockAdapter.return_value = mock_adapter

            result = await _query_alert_from_alertmanager(
                mock_direct_client, "CephOSDDown", fingerprint="wrong123"
            )

        assert result is None


class TestQueryAlertHistory:
    """Tests for _query_alert_history function."""

    @pytest.fixture
    def mock_direct_client(self) -> AsyncMock:
        """Create mock DirectStackLightClient."""
        return AsyncMock()

    @pytest.mark.asyncio
    async def test_query_history_success(self, mock_direct_client: AsyncMock) -> None:
        """Test successful history query."""
        sample1 = MagicMock()
        sample1.timestamp = datetime.now(UTC)
        sample1.value = 1.0

        sample2 = MagicMock()
        sample2.timestamp = datetime.now(UTC) - timedelta(hours=1)
        sample2.value = 0.0

        with patch(
            "mosk_mcp.tools.cluster_health.get_alert_details.StackLightAdapter"
        ) as MockAdapter:
            mock_adapter = AsyncMock()
            mock_adapter.query_prometheus_raw.return_value = [sample1, sample2]
            MockAdapter.return_value = mock_adapter

            result = await _query_alert_history(mock_direct_client, "CephOSDDown")

        assert len(result) == 2
        assert result[0].state == AlertState.FIRING
        assert result[1].state == AlertState.RESOLVED

    @pytest.mark.asyncio
    async def test_query_history_empty(self, mock_direct_client: AsyncMock) -> None:
        """Test history query with no results."""
        with patch(
            "mosk_mcp.tools.cluster_health.get_alert_details.StackLightAdapter"
        ) as MockAdapter:
            mock_adapter = AsyncMock()
            mock_adapter.query_prometheus_raw.return_value = []
            MockAdapter.return_value = mock_adapter

            result = await _query_alert_history(mock_direct_client, "CephOSDDown")

        assert result == []

    @pytest.mark.asyncio
    async def test_query_history_error_handled(self, mock_direct_client: AsyncMock) -> None:
        """Test history query error is handled gracefully."""
        with patch(
            "mosk_mcp.tools.cluster_health.get_alert_details.StackLightAdapter"
        ) as MockAdapter:
            mock_adapter = AsyncMock()
            mock_adapter.query_prometheus_raw.side_effect = Exception("Query failed")
            MockAdapter.return_value = mock_adapter

            result = await _query_alert_history(mock_direct_client, "CephOSDDown")

        # Should return empty list on error, not raise
        assert result == []

    @pytest.mark.asyncio
    async def test_query_history_custom_hours(self, mock_direct_client: AsyncMock) -> None:
        """Test history query with custom hours."""
        with patch(
            "mosk_mcp.tools.cluster_health.get_alert_details.StackLightAdapter"
        ) as MockAdapter:
            mock_adapter = AsyncMock()
            mock_adapter.query_prometheus_raw.return_value = []
            MockAdapter.return_value = mock_adapter

            await _query_alert_history(mock_direct_client, "CephOSDDown", hours=48)

            mock_adapter.query_prometheus_raw.assert_called_once()
            call_kwargs = mock_adapter.query_prometheus_raw.call_args.kwargs
            assert call_kwargs["time_range_minutes"] == 48 * 60


class TestGetAlertDetails:
    """Tests for get_alert_details function."""

    @pytest.fixture
    def mock_direct_client(self) -> AsyncMock:
        """Create mock DirectStackLightClient."""
        return AsyncMock()

    @pytest.fixture
    def mock_alert_data(self) -> dict:
        """Create mock alert data."""
        return {
            "labels": {"alertname": "CephOSDDown", "severity": "critical"},
            "annotations": {
                "summary": "OSD 5 is down",
                "description": "OSD 5 on node1 not responding",
            },
            "status": {"state": "firing", "silencedBy": []},
            "startsAt": datetime.now(UTC).isoformat(),
            "fingerprint": "abc123",
        }

    @pytest.mark.asyncio
    async def test_get_alert_details_success(
        self, mock_direct_client: AsyncMock, mock_alert_data: dict
    ) -> None:
        """Test successful alert details retrieval."""
        with patch(
            "mosk_mcp.tools.cluster_health.get_alert_details._query_alert_from_alertmanager",
            return_value=mock_alert_data,
        ):
            result = await get_alert_details(mock_direct_client, "CephOSDDown")

        assert result.alert_name == "CephOSDDown"
        assert result.severity == AlertSeverity.CRITICAL
        assert result.state == AlertState.FIRING
        assert result.summary == "OSD 5 is down"
        assert result.description == "OSD 5 on node1 not responding"

    @pytest.mark.asyncio
    async def test_get_alert_details_not_found(self, mock_direct_client: AsyncMock) -> None:
        """Test alert not found error."""
        with patch(
            "mosk_mcp.tools.cluster_health.get_alert_details._query_alert_from_alertmanager",
            return_value=None,
        ):
            with pytest.raises(ResourceNotFoundError) as exc_info:
                await get_alert_details(mock_direct_client, "NonExistent")

        assert "NonExistent" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_get_alert_details_with_fingerprint(
        self, mock_direct_client: AsyncMock, mock_alert_data: dict
    ) -> None:
        """Test alert details with fingerprint."""
        with patch(
            "mosk_mcp.tools.cluster_health.get_alert_details._query_alert_from_alertmanager",
            return_value=mock_alert_data,
        ) as mock_query:
            result = await get_alert_details(
                mock_direct_client, "CephOSDDown", fingerprint="abc123"
            )

        mock_query.assert_called_once_with(mock_direct_client, "CephOSDDown", "abc123")
        assert result.alert_name == "CephOSDDown"

    @pytest.mark.asyncio
    async def test_get_alert_details_with_history(
        self, mock_direct_client: AsyncMock, mock_alert_data: dict
    ) -> None:
        """Test alert details with history."""
        mock_history = [
            AlertHistoryEntry(
                timestamp=datetime.now(UTC).isoformat(),
                state=AlertState.FIRING,
                value=1.0,
            )
        ]

        with (
            patch(
                "mosk_mcp.tools.cluster_health.get_alert_details._query_alert_from_alertmanager",
                return_value=mock_alert_data,
            ),
            patch(
                "mosk_mcp.tools.cluster_health.get_alert_details._query_alert_history",
                return_value=mock_history,
            ),
        ):
            result = await get_alert_details(
                mock_direct_client, "CephOSDDown", include_history=True
            )

        assert len(result.history) == 1

    @pytest.mark.asyncio
    async def test_get_alert_details_warning_severity(self, mock_direct_client: AsyncMock) -> None:
        """Test warning severity mapping."""
        alert_data = {
            "labels": {"alertname": "DiskSpaceLow", "severity": "warning"},
            "annotations": {"summary": "Disk space low"},
            "status": {"state": "firing", "silencedBy": []},
            "startsAt": datetime.now(UTC).isoformat(),
        }

        with patch(
            "mosk_mcp.tools.cluster_health.get_alert_details._query_alert_from_alertmanager",
            return_value=alert_data,
        ):
            result = await get_alert_details(mock_direct_client, "DiskSpaceLow")

        assert result.severity == AlertSeverity.WARNING

    @pytest.mark.asyncio
    async def test_get_alert_details_info_severity(self, mock_direct_client: AsyncMock) -> None:
        """Test info severity mapping."""
        alert_data = {
            "labels": {"alertname": "InfoAlert", "severity": "info"},
            "annotations": {"summary": "Info"},
            "status": {"state": "firing", "silencedBy": []},
            "startsAt": datetime.now(UTC).isoformat(),
        }

        with patch(
            "mosk_mcp.tools.cluster_health.get_alert_details._query_alert_from_alertmanager",
            return_value=alert_data,
        ):
            result = await get_alert_details(mock_direct_client, "InfoAlert")

        assert result.severity == AlertSeverity.INFO

    @pytest.mark.asyncio
    async def test_get_alert_details_silenced(self, mock_direct_client: AsyncMock) -> None:
        """Test silenced alert."""
        alert_data = {
            "labels": {"alertname": "CephOSDDown", "severity": "critical"},
            "annotations": {"summary": "OSD down"},
            "status": {"state": "firing", "silencedBy": ["silence123"]},
            "startsAt": datetime.now(UTC).isoformat(),
        }

        with patch(
            "mosk_mcp.tools.cluster_health.get_alert_details._query_alert_from_alertmanager",
            return_value=alert_data,
        ):
            result = await get_alert_details(mock_direct_client, "CephOSDDown")

        assert result.is_silenced is True
        assert result.silence_id == "silence123"
        assert result.silence_ends_at is not None

    @pytest.mark.asyncio
    async def test_get_alert_details_with_context(
        self, mock_direct_client: AsyncMock, mock_alert_data: dict
    ) -> None:
        """Test alert details include context."""
        with patch(
            "mosk_mcp.tools.cluster_health.get_alert_details._query_alert_from_alertmanager",
            return_value=mock_alert_data,
        ):
            result = await get_alert_details(mock_direct_client, "CephOSDDown")

        assert result.context is not None
        assert result.context.runbook_url is not None
        assert len(result.context.suggested_actions) > 0
        assert len(result.context.related_alerts) > 0

    @pytest.mark.asyncio
    async def test_get_alert_details_default_actions(self, mock_direct_client: AsyncMock) -> None:
        """Test default actions for unknown alert."""
        alert_data = {
            "labels": {"alertname": "CustomAlert", "severity": "warning"},
            "annotations": {"summary": "Custom"},
            "status": {"state": "firing", "silencedBy": []},
            "startsAt": datetime.now(UTC).isoformat(),
        }

        with patch(
            "mosk_mcp.tools.cluster_health.get_alert_details._query_alert_from_alertmanager",
            return_value=alert_data,
        ):
            result = await get_alert_details(mock_direct_client, "CustomAlert")

        assert result.context is not None
        assert len(result.context.suggested_actions) > 0
        # Default actions should include generic advice
        assert any("logs" in action.lower() for action in result.context.suggested_actions)

    @pytest.mark.asyncio
    async def test_get_alert_details_duration_calculation(
        self, mock_direct_client: AsyncMock
    ) -> None:
        """Test duration calculation."""
        start_time = datetime.now(UTC) - timedelta(hours=2)
        alert_data = {
            "labels": {"alertname": "CephOSDDown", "severity": "critical"},
            "annotations": {"summary": "OSD down"},
            "status": {"state": "firing", "silencedBy": []},
            "startsAt": start_time.isoformat(),
        }

        with patch(
            "mosk_mcp.tools.cluster_health.get_alert_details._query_alert_from_alertmanager",
            return_value=alert_data,
        ):
            result = await get_alert_details(mock_direct_client, "CephOSDDown")

        # Duration should be approximately 2 hours
        assert result.duration_seconds > 7000  # > 2 hours in seconds
        assert result.duration_seconds < 8000

    @pytest.mark.asyncio
    async def test_get_alert_details_invalid_start_time(
        self, mock_direct_client: AsyncMock
    ) -> None:
        """Test handling of invalid start time."""
        alert_data = {
            "labels": {"alertname": "CephOSDDown", "severity": "critical"},
            "annotations": {"summary": "OSD down"},
            "status": {"state": "firing", "silencedBy": []},
            "startsAt": "invalid-time",
        }

        with patch(
            "mosk_mcp.tools.cluster_health.get_alert_details._query_alert_from_alertmanager",
            return_value=alert_data,
        ):
            result = await get_alert_details(mock_direct_client, "CephOSDDown")

        # Should default to 0 on parse error
        assert result.duration_seconds == 0

    @pytest.mark.asyncio
    async def test_get_alert_details_pending_state(self, mock_direct_client: AsyncMock) -> None:
        """Test pending alert state."""
        alert_data = {
            "labels": {"alertname": "CephOSDDown", "severity": "critical"},
            "annotations": {"summary": "OSD down"},
            "status": {"state": "pending", "silencedBy": []},
            "startsAt": datetime.now(UTC).isoformat(),
        }

        with patch(
            "mosk_mcp.tools.cluster_health.get_alert_details._query_alert_from_alertmanager",
            return_value=alert_data,
        ):
            result = await get_alert_details(mock_direct_client, "CephOSDDown")

        assert result.state == AlertState.PENDING

    @pytest.mark.asyncio
    async def test_get_alert_details_error_handling(self, mock_direct_client: AsyncMock) -> None:
        """Test error handling."""
        with patch(
            "mosk_mcp.tools.cluster_health.get_alert_details._query_alert_from_alertmanager",
            side_effect=Exception("Connection failed"),
        ):
            with pytest.raises(ToolExecutionError) as exc_info:
                await get_alert_details(mock_direct_client, "CephOSDDown")

        assert "Failed to get alert details" in str(exc_info.value)
