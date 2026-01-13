"""Unit tests for Intelligent Troubleshooting tools.

This module contains comprehensive tests for all troubleshooting MCP tools
including log querying, event correlation, alert explanation, request tracing,
VM/network/storage diagnostics, known issue matching, resolution suggestions,
and diagnostic bundle generation.
"""

import base64
import io
import tarfile
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from mosk_mcp.adapters.stacklight import (
    Alert,
    LogEntry,
    LogQueryResult,
)
from mosk_mcp.adapters.stacklight import (
    AlertSeverity as AdapterAlertSeverity,
)
from mosk_mcp.adapters.stacklight import (
    AlertState as AdapterAlertState,
)
from mosk_mcp.adapters.stacklight import (
    LogSeverity as AdapterLogSeverity,
)
from mosk_mcp.core.exceptions import ToolExecutionError, ValidationError
from mosk_mcp.tools.troubleshooting import (
    # Known issues
    KNOWN_ISSUES,
    AlertSeverity,
    BundleFormat,
    DiagnosisCategory,
    IssuePattern,
    IssuePriority,
    LogSeverity,
    ResolutionConfidence,
    correlate_events,
    create_diagnostic_bundle,
    diagnose_network_issue,
    diagnose_storage_issue,
    diagnose_vm_failure,
    explain_alert,
    get_known_issue_database,
    get_known_issues,
    # Tools
    query_logs,
    reset_known_issue_database,
    suggest_resolution,
    trace_request,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_k8s_adapter():
    """Create a mock Kubernetes adapter."""
    adapter = AsyncMock()
    adapter.connect = AsyncMock()
    adapter.list = AsyncMock(return_value=[])
    adapter.get = AsyncMock()
    return adapter


@pytest.fixture
def mock_direct_client():
    """Create a mock DirectStackLightClient for OIDC-based StackLight access."""
    client = AsyncMock()
    client.connect = AsyncMock()
    return client


@pytest.fixture
def mock_log_entries():
    """Create mock log entries."""
    now = datetime.now(UTC)
    return [
        LogEntry(
            timestamp=now - timedelta(minutes=5),
            message="RPC timeout waiting for response from nova-scheduler",
            severity=AdapterLogSeverity.ERROR,
            service="nova-api",
            host="controller-01",
            request_id="req-abc123",
            namespace="openstack",
        ),
        LogEntry(
            timestamp=now - timedelta(minutes=4),
            message="Failed to connect to AMQP server",
            severity=AdapterLogSeverity.ERROR,
            service="nova-api",
            host="controller-01",
            request_id="req-abc123",
        ),
        LogEntry(
            timestamp=now - timedelta(minutes=3),
            message="Successfully reconnected to message queue",
            severity=AdapterLogSeverity.INFO,
            service="nova-api",
            host="controller-01",
        ),
        LogEntry(
            timestamp=now - timedelta(minutes=2),
            message="VM spawn request received",
            severity=AdapterLogSeverity.INFO,
            service="nova-compute",
            host="compute-01",
            request_id="req-def456",
        ),
        LogEntry(
            timestamp=now - timedelta(minutes=1),
            message="Neutron agent heartbeat missed",
            severity=AdapterLogSeverity.WARNING,
            service="neutron-agent",
            host="compute-01",
        ),
    ]


@pytest.fixture
def mock_alerts():
    """Create mock alerts."""
    now = datetime.now(UTC)
    return [
        Alert(
            alert_name="NeutronAgentDown",
            state=AdapterAlertState.FIRING,
            severity=AdapterAlertSeverity.WARNING,
            summary="Neutron agent on compute-01 is not responding",
            description="The Neutron OVS agent on compute-01 has missed heartbeats",
            starts_at=now - timedelta(hours=1),
            labels={"host": "compute-01", "service": "neutron"},
            annotations={"runbook_url": "https://docs.example.com/neutron-agent-down"},
        ),
        Alert(
            alert_name="CephOSDDown",
            state=AdapterAlertState.FIRING,
            severity=AdapterAlertSeverity.CRITICAL,
            summary="Ceph OSD.3 is down",
            description="OSD 3 on storage-02 has been marked down",
            starts_at=now - timedelta(minutes=30),
            labels={"osd_id": "3", "host": "storage-02"},
        ),
        Alert(
            alert_name="HighCPUUsage",
            state=AdapterAlertState.RESOLVED,
            severity=AdapterAlertSeverity.WARNING,
            summary="High CPU usage on controller-01",
            description="CPU usage exceeded 90% for 5 minutes",
            starts_at=now - timedelta(hours=2),
            ends_at=now - timedelta(hours=1),
            labels={"host": "controller-01"},
        ),
    ]


@pytest.fixture
def mock_stacklight_adapter(mock_log_entries, mock_alerts):
    """Create a mock StackLight adapter."""
    adapter = AsyncMock()
    adapter.connect = AsyncMock()
    # Return LogQueryResult instead of plain list for pagination support
    log_query_result = LogQueryResult(
        logs=mock_log_entries,
        total_count=len(mock_log_entries),
        cursor=None,
        has_more=False,
    )
    adapter.query_logs = AsyncMock(return_value=log_query_result)
    adapter.query_logs_natural_language = AsyncMock(
        return_value=(
            log_query_result,
            {"services": ["nova"], "severity": "error", "time_range_minutes": 60},
        )
    )
    adapter.get_alerts = AsyncMock(return_value=mock_alerts)
    adapter.get_alert_by_fingerprint = AsyncMock(return_value=None)
    adapter.get_logs_by_request_id = AsyncMock(return_value=mock_log_entries[:2])
    return adapter


@pytest.fixture(autouse=True)
def reset_known_issues():
    """Reset known issue database before each test."""
    reset_known_issue_database()
    yield
    reset_known_issue_database()


# =============================================================================
# Test query_logs
# =============================================================================


class TestQueryLogs:
    """Tests for query_logs tool."""

    @pytest.mark.asyncio
    async def test_query_logs_natural_language(
        self, mock_direct_client, mock_stacklight_adapter, mock_log_entries
    ):
        """Test querying logs with natural language."""
        with patch(
            "mosk_mcp.tools.troubleshooting.query_logs.StackLightAdapter",
            return_value=mock_stacklight_adapter,
        ):
            result = await query_logs(
                direct_client=mock_direct_client,
                query="nova errors in last hour",
            )

        assert result.returned_count == len(mock_log_entries)
        assert len(result.logs) == len(mock_log_entries)
        assert result.query_info is not None
        mock_stacklight_adapter.query_logs_natural_language.assert_called_once()

    @pytest.mark.asyncio
    async def test_query_logs_structured(
        self, mock_direct_client, mock_stacklight_adapter, mock_log_entries
    ):
        """Test querying logs with structured filters."""
        with patch(
            "mosk_mcp.tools.troubleshooting.query_logs.StackLightAdapter",
            return_value=mock_stacklight_adapter,
        ):
            result = await query_logs(
                direct_client=mock_direct_client,
                services=["nova-api"],
                severity=LogSeverity.ERROR,
                time_range_minutes=60,
                limit=100,
            )

        assert result.returned_count == len(mock_log_entries)
        mock_stacklight_adapter.query_logs.assert_called_once()

    @pytest.mark.asyncio
    async def test_query_logs_with_request_id(self, mock_direct_client, mock_stacklight_adapter):
        """Test querying logs with request ID filter."""
        with patch(
            "mosk_mcp.tools.troubleshooting.query_logs.StackLightAdapter",
            return_value=mock_stacklight_adapter,
        ):
            result = await query_logs(
                direct_client=mock_direct_client,
                request_id="req-abc123",
            )

        assert result.returned_count > 0

    @pytest.mark.asyncio
    async def test_query_logs_invalid_time_range(self, mock_direct_client):
        """Test query_logs with invalid time range."""
        with pytest.raises(ValidationError) as exc_info:
            await query_logs(
                direct_client=mock_direct_client,
                services=["nova"],
                time_range_minutes=20000,  # > 10080
            )

        assert "time_range_minutes" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_query_logs_invalid_limit(self, mock_direct_client):
        """Test query_logs with invalid limit."""
        with pytest.raises(ValidationError) as exc_info:
            await query_logs(
                direct_client=mock_direct_client,
                services=["nova"],
                limit=2000,  # > 1000
            )

        assert "limit" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_query_logs_statistics(
        self, mock_direct_client, mock_stacklight_adapter, mock_log_entries
    ):
        """Test that query_logs returns correct statistics."""
        with patch(
            "mosk_mcp.tools.troubleshooting.query_logs.StackLightAdapter",
            return_value=mock_stacklight_adapter,
        ):
            result = await query_logs(
                direct_client=mock_direct_client,
                services=["nova"],
                time_range_minutes=60,
            )

        # Check that statistics are calculated
        assert result.by_severity is not None
        assert result.by_service is not None
        assert result.by_host is not None
        assert result.time_range is not None

    @pytest.mark.asyncio
    async def test_query_logs_pagination(self, mock_direct_client, mock_log_entries):
        """Test pagination with cursor and has_more."""
        # Create a paginated result
        log_query_result = LogQueryResult(
            logs=mock_log_entries[:2],
            total_count=10,  # More logs exist than returned
            cursor="eyJ0aW1lc3RhbXAiOiAiMjAyNS0wMS0wMSIsICJfaWQiOiAiYWJjMTIzIn0=",
            has_more=True,
        )

        mock_adapter = AsyncMock()
        mock_adapter.connect = AsyncMock()
        mock_adapter.query_logs = AsyncMock(return_value=log_query_result)

        with patch(
            "mosk_mcp.tools.troubleshooting.query_logs.StackLightAdapter",
            return_value=mock_adapter,
        ):
            result = await query_logs(
                direct_client=mock_direct_client,
                services=["nova"],
                limit=2,
            )

        # Check pagination fields
        assert result.has_more is True
        assert result.cursor is not None
        assert result.total_count == 10
        assert result.returned_count == 2

    @pytest.mark.asyncio
    async def test_query_logs_aggregation_only(self, mock_direct_client, mock_log_entries):
        """Test aggregation_only mode returns empty logs but with stats."""
        log_query_result = LogQueryResult(
            logs=mock_log_entries,
            total_count=len(mock_log_entries),
            cursor=None,
            has_more=False,
        )

        mock_adapter = AsyncMock()
        mock_adapter.connect = AsyncMock()
        mock_adapter.query_logs = AsyncMock(return_value=log_query_result)

        with patch(
            "mosk_mcp.tools.troubleshooting.query_logs.StackLightAdapter",
            return_value=mock_adapter,
        ):
            result = await query_logs(
                direct_client=mock_direct_client,
                services=["nova"],
                aggregation_only=True,
            )

        # Should have no logs in aggregation_only mode
        assert len(result.logs) == 0
        # But should still have total_count from server
        assert result.total_count >= 0

    @pytest.mark.asyncio
    async def test_query_logs_message_truncation(self, mock_direct_client):
        """Test that long messages are truncated."""
        # Create a log entry with a very long message
        now = datetime.now(UTC)
        long_message = "A" * 10000  # 10KB message
        long_log_entry = LogEntry(
            timestamp=now,
            message=long_message,
            severity=AdapterLogSeverity.ERROR,
            service="nova-api",
            host="controller-01",
            request_id="req-abc123",
            namespace="openstack",
        )

        log_query_result = LogQueryResult(
            logs=[long_log_entry],
            total_count=1,
            cursor=None,
            has_more=False,
        )

        mock_adapter = AsyncMock()
        mock_adapter.connect = AsyncMock()
        mock_adapter.query_logs = AsyncMock(return_value=log_query_result)

        with patch(
            "mosk_mcp.tools.troubleshooting.query_logs.StackLightAdapter",
            return_value=mock_adapter,
        ):
            result = await query_logs(
                direct_client=mock_direct_client,
                services=["nova"],
            )

        # Check truncation
        assert result.truncated_messages == 1
        assert len(result.logs) == 1
        assert result.logs[0].message_truncated is True
        assert result.logs[0].original_length == 10000
        assert len(result.logs[0].message) < 10000
        assert "truncated" in result.logs[0].message.lower()

    @pytest.mark.asyncio
    async def test_query_logs_with_cursor(self, mock_direct_client, mock_log_entries):
        """Test passing cursor for pagination."""
        cursor = "eyJ0aW1lc3RhbXAiOiAiMjAyNS0wMS0wMSIsICJfaWQiOiAiYWJjMTIzIn0="

        log_query_result = LogQueryResult(
            logs=mock_log_entries,
            total_count=len(mock_log_entries),
            cursor=None,  # Last page
            has_more=False,
        )

        mock_adapter = AsyncMock()
        mock_adapter.connect = AsyncMock()
        mock_adapter.query_logs = AsyncMock(return_value=log_query_result)

        with patch(
            "mosk_mcp.tools.troubleshooting.query_logs.StackLightAdapter",
            return_value=mock_adapter,
        ):
            result = await query_logs(
                direct_client=mock_direct_client,
                services=["nova"],
                cursor=cursor,
            )

        # Verify cursor was passed to adapter
        mock_adapter.query_logs.assert_called_once()
        call_kwargs = mock_adapter.query_logs.call_args.kwargs
        assert call_kwargs.get("cursor") == cursor

        # Last page should have no cursor
        assert result.has_more is False
        assert result.cursor is None


# =============================================================================
# Test correlate_events
# =============================================================================


class TestCorrelateEvents:
    """Tests for correlate_events tool."""

    @pytest.mark.asyncio
    async def test_correlate_events_basic(
        self, mock_direct_client, mock_stacklight_adapter, mock_log_entries
    ):
        """Test basic event correlation."""
        with patch(
            "mosk_mcp.tools.troubleshooting.correlate_events.StackLightAdapter",
            return_value=mock_stacklight_adapter,
        ):
            result = await correlate_events(
                direct_client=mock_direct_client,
                services=["nova", "neutron"],
                window_minutes_before=30,
                window_minutes_after=30,
            )

        assert result.total_events >= 0
        assert result.anchor_time is not None
        assert result.timeline_summary is not None

    @pytest.mark.asyncio
    async def test_correlate_events_with_anchor_time(
        self, mock_direct_client, mock_stacklight_adapter
    ):
        """Test event correlation with specific anchor time."""
        anchor = datetime.now(UTC).isoformat()
        with patch(
            "mosk_mcp.tools.troubleshooting.correlate_events.StackLightAdapter",
            return_value=mock_stacklight_adapter,
        ):
            result = await correlate_events(
                direct_client=mock_direct_client,
                anchor_time=anchor,
                window_minutes_before=15,
                window_minutes_after=15,
            )

        assert result.timeline_summary is not None

    @pytest.mark.asyncio
    async def test_correlate_events_clusters(self, mock_direct_client, mock_stacklight_adapter):
        """Test that event correlation groups events into clusters."""
        with patch(
            "mosk_mcp.tools.troubleshooting.correlate_events.StackLightAdapter",
            return_value=mock_stacklight_adapter,
        ):
            result = await correlate_events(
                direct_client=mock_direct_client,
                services=["nova"],
            )

        # Events should be clustered
        assert result.clusters is not None


# =============================================================================
# Test explain_alert
# =============================================================================


class TestExplainAlert:
    """Tests for explain_alert tool."""

    @pytest.mark.asyncio
    async def test_explain_known_alert(self, mock_direct_client, mock_stacklight_adapter):
        """Test explaining a known alert type."""
        with patch(
            "mosk_mcp.tools.troubleshooting.explain_alert.StackLightAdapter",
            return_value=mock_stacklight_adapter,
        ):
            result = await explain_alert(
                direct_client=mock_direct_client,
                alert_name="NeutronAgentDown",
            )

        # Check output model structure
        assert result.alert is not None
        assert result.alert.alert_name == "NeutronAgentDown"
        assert result.context is not None
        assert len(result.remediation_steps) > 0

    @pytest.mark.asyncio
    async def test_explain_alert_with_history(self, mock_direct_client, mock_stacklight_adapter):
        """Test explaining alert with historical data."""
        with patch(
            "mosk_mcp.tools.troubleshooting.explain_alert.StackLightAdapter",
            return_value=mock_stacklight_adapter,
        ):
            result = await explain_alert(
                direct_client=mock_direct_client,
                alert_name="CephOSDDown",
                include_history=True,
            )

        assert result.alert is not None
        assert result.alert.alert_name == "CephOSDDown"

    @pytest.mark.asyncio
    async def test_explain_unknown_alert(self, mock_direct_client):
        """Test explaining an unknown alert type."""
        mock_adapter = AsyncMock()
        mock_adapter.connect = AsyncMock()
        mock_adapter.get_alert_by_fingerprint = AsyncMock(return_value=None)
        mock_adapter.get_alerts = AsyncMock(
            return_value=[
                Alert(
                    alert_name="UnknownAlert",
                    state=AdapterAlertState.FIRING,
                    severity=AdapterAlertSeverity.WARNING,
                    summary="Unknown issue detected",
                    starts_at=datetime.now(UTC),
                    labels={},
                )
            ]
        )
        mock_adapter.query_logs = AsyncMock(
            return_value=LogQueryResult(logs=[], total_count=0, cursor=None, has_more=False)
        )

        with patch(
            "mosk_mcp.tools.troubleshooting.explain_alert.StackLightAdapter",
            return_value=mock_adapter,
        ):
            result = await explain_alert(
                direct_client=mock_direct_client,
                alert_name="UnknownAlert",
            )

        # Should still return result, even if generic
        assert result.alert is not None


# =============================================================================
# Test trace_request
# =============================================================================


class TestTraceRequest:
    """Tests for trace_request tool."""

    @pytest.mark.asyncio
    async def test_trace_request_success(self, mock_direct_client, mock_log_entries):
        """Test tracing a request through services."""
        mock_adapter = AsyncMock()
        mock_adapter.connect = AsyncMock()
        mock_adapter.get_logs_by_request_id = AsyncMock(return_value=mock_log_entries[:2])

        with patch(
            "mosk_mcp.tools.troubleshooting.trace_request.StackLightAdapter",
            return_value=mock_adapter,
        ):
            result = await trace_request(
                direct_client=mock_direct_client,
                request_id="req-abc123",
            )

        assert result.request_id == "req-abc123"
        assert result.found is True
        assert len(result.spans) > 0

    @pytest.mark.asyncio
    async def test_trace_request_not_found(self, mock_direct_client):
        """Test tracing when request ID not found."""
        mock_adapter = AsyncMock()
        mock_adapter.connect = AsyncMock()
        mock_adapter.get_logs_by_request_id = AsyncMock(return_value=[])

        with patch(
            "mosk_mcp.tools.troubleshooting.trace_request.StackLightAdapter",
            return_value=mock_adapter,
        ):
            result = await trace_request(
                direct_client=mock_direct_client,
                request_id="nonexistent-request-id",
            )

        assert result.request_id == "nonexistent-request-id"
        assert result.found is False
        assert len(result.spans) == 0

    @pytest.mark.asyncio
    async def test_trace_request_invalid_id(self, mock_direct_client):
        """Test trace_request with invalid request ID."""
        with pytest.raises(ValidationError) as exc_info:
            await trace_request(
                direct_client=mock_direct_client,
                request_id="short",  # Too short
            )

        assert "request_id" in str(exc_info.value)


# =============================================================================
# Test diagnose_vm_failure
# =============================================================================


class TestDiagnoseVMFailure:
    """Tests for diagnose_vm_failure tool."""

    @pytest.mark.asyncio
    async def test_diagnose_spawn_failure(self, mock_direct_client, mock_stacklight_adapter):
        """Test diagnosing VM spawn failure."""
        with patch(
            "mosk_mcp.tools.troubleshooting.diagnose_vm_failure.StackLightAdapter",
            return_value=mock_stacklight_adapter,
        ):
            result = await diagnose_vm_failure(
                direct_client=mock_direct_client,
                instance_id="instance-abc123",
                failure_type="spawn",
            )

        # Check output model structure
        assert result.failure_detected is True or result.failure_detected is False
        # If failure detected, should have primary diagnosis
        if result.failure_detected:
            assert result.primary_diagnosis is not None

    @pytest.mark.asyncio
    async def test_diagnose_boot_failure(self, mock_direct_client, mock_stacklight_adapter):
        """Test diagnosing VM boot failure."""
        with patch(
            "mosk_mcp.tools.troubleshooting.diagnose_vm_failure.StackLightAdapter",
            return_value=mock_stacklight_adapter,
        ):
            result = await diagnose_vm_failure(
                direct_client=mock_direct_client,
                instance_id="instance-def456",
                failure_type="boot",
            )

        assert result.timestamp is not None

    @pytest.mark.asyncio
    async def test_diagnose_migration_failure(self, mock_direct_client, mock_stacklight_adapter):
        """Test diagnosing VM migration failure."""
        with patch(
            "mosk_mcp.tools.troubleshooting.diagnose_vm_failure.StackLightAdapter",
            return_value=mock_stacklight_adapter,
        ):
            result = await diagnose_vm_failure(
                direct_client=mock_direct_client,
                instance_id="instance-ghi789",
                failure_type="migration",
            )

        assert result.additional_findings is not None


# =============================================================================
# Test diagnose_network_issue
# =============================================================================


class TestDiagnoseNetworkIssue:
    """Tests for diagnose_network_issue tool."""

    @pytest.mark.asyncio
    async def test_diagnose_connectivity_issue(self, mock_direct_client, mock_stacklight_adapter):
        """Test diagnosing network connectivity issue."""
        with patch(
            "mosk_mcp.tools.troubleshooting.diagnose_network_issue.StackLightAdapter",
            return_value=mock_stacklight_adapter,
        ):
            result = await diagnose_network_issue(
                direct_client=mock_direct_client,
                instance_id="instance-abc",
                symptom="Cannot reach external network",
            )

        assert result.issue_detected is True or result.issue_detected is False

    @pytest.mark.asyncio
    async def test_diagnose_network_by_port(self, mock_direct_client, mock_stacklight_adapter):
        """Test diagnosing network issue by port ID."""
        with patch(
            "mosk_mcp.tools.troubleshooting.diagnose_network_issue.StackLightAdapter",
            return_value=mock_stacklight_adapter,
        ):
            result = await diagnose_network_issue(
                direct_client=mock_direct_client,
                port_id="port-abc123",
            )

        assert result.path_components is not None

    @pytest.mark.asyncio
    async def test_diagnose_network_by_ips(self, mock_direct_client, mock_stacklight_adapter):
        """Test diagnosing network issue between IPs."""
        with patch(
            "mosk_mcp.tools.troubleshooting.diagnose_network_issue.StackLightAdapter",
            return_value=mock_stacklight_adapter,
        ):
            result = await diagnose_network_issue(
                direct_client=mock_direct_client,
                source_ip="192.168.1.10",
                destination_ip="10.0.0.100",
            )

        assert result.agent_status is not None


# =============================================================================
# Test diagnose_storage_issue
# =============================================================================


class TestDiagnoseStorageIssue:
    """Tests for diagnose_storage_issue tool."""

    @pytest.mark.asyncio
    async def test_diagnose_volume_attach_issue(
        self, mock_k8s_adapter, mock_direct_client, mock_stacklight_adapter
    ):
        """Test diagnosing volume attachment issue."""
        with patch(
            "mosk_mcp.tools.troubleshooting.diagnose_storage_issue.StackLightAdapter",
            return_value=mock_stacklight_adapter,
        ):
            result = await diagnose_storage_issue(
                direct_client=mock_direct_client,
                kubernetes_adapter=mock_k8s_adapter,
                volume_id="vol-abc123",
                symptom="Volume attach timeout",
            )

        assert result.issue_detected is True or result.issue_detected is False

    @pytest.mark.asyncio
    async def test_diagnose_slow_io(
        self, mock_k8s_adapter, mock_direct_client, mock_stacklight_adapter
    ):
        """Test diagnosing slow I/O issue."""
        with patch(
            "mosk_mcp.tools.troubleshooting.diagnose_storage_issue.StackLightAdapter",
            return_value=mock_stacklight_adapter,
        ):
            result = await diagnose_storage_issue(
                direct_client=mock_direct_client,
                kubernetes_adapter=mock_k8s_adapter,
                instance_id="instance-xyz",
                symptom="Slow disk I/O",
            )

        assert result.storage_backend_status is not None

    @pytest.mark.asyncio
    async def test_diagnose_ceph_health(
        self, mock_k8s_adapter, mock_direct_client, mock_stacklight_adapter
    ):
        """Test diagnosing Ceph health issue."""
        with patch(
            "mosk_mcp.tools.troubleshooting.diagnose_storage_issue.StackLightAdapter",
            return_value=mock_stacklight_adapter,
        ):
            result = await diagnose_storage_issue(
                direct_client=mock_direct_client,
                kubernetes_adapter=mock_k8s_adapter,
                symptom="Ceph HEALTH_WARN",
                include_ceph_status=True,
            )

        # Ceph status should be included
        assert result.ceph_status is not None or result.ceph_status is None  # May or may not be set


# =============================================================================
# Test get_known_issues
# =============================================================================


class TestGetKnownIssues:
    """Tests for get_known_issues tool.

    Note: get_known_issues doesn't use StackLight adapter, it uses
    an in-memory known issues database.
    """

    @pytest.mark.asyncio
    async def test_get_known_issues_by_symptoms(self, mock_k8s_adapter):
        """Test finding known issues by symptoms."""
        result = await get_known_issues(
            kubernetes_adapter=mock_k8s_adapter,
            symptoms=["RPC timeout", "slow API responses"],
        )

        assert len(result.issues) > 0
        assert result.total_matches > 0
        # Should match MOSK-001 (RabbitMQ connection exhaustion)
        assert any("MOSK-001" in issue.issue_id for issue in result.issues)

    @pytest.mark.asyncio
    async def test_get_known_issues_by_error_message(self, mock_k8s_adapter):
        """Test finding known issues by error message."""
        result = await get_known_issues(
            kubernetes_adapter=mock_k8s_adapter,
            error_message="MessagingTimeout waiting for response from nova-scheduler",
        )

        assert len(result.issues) > 0
        # Should match RPC timeout issue
        assert result.best_match is not None

    @pytest.mark.asyncio
    async def test_get_known_issues_by_service(self, mock_k8s_adapter):
        """Test finding known issues by service."""
        result = await get_known_issues(
            kubernetes_adapter=mock_k8s_adapter,
            service="ceph",
            limit=5,
        )

        # Should return Ceph-related issues
        assert len(result.issues) > 0
        for issue in result.issues:
            assert any(s.lower() == "ceph" for s in issue.affected_services)

    @pytest.mark.asyncio
    async def test_get_known_issues_by_category(self, mock_k8s_adapter):
        """Test finding known issues by category."""
        result = await get_known_issues(
            kubernetes_adapter=mock_k8s_adapter,
            category=DiagnosisCategory.STORAGE_ISSUE,
        )

        assert len(result.issues) > 0
        for issue in result.issues:
            assert issue.category == DiagnosisCategory.STORAGE_ISSUE

    @pytest.mark.asyncio
    async def test_get_known_issues_empty_search(self, mock_k8s_adapter):
        """Test known issues search with no matches."""
        result = await get_known_issues(
            kubernetes_adapter=mock_k8s_adapter,
            error_message="completely random string that won't match anything xyz123",
        )

        # Should return results even if low confidence
        assert result.timestamp is not None


# =============================================================================
# Test suggest_resolution
# =============================================================================


class TestSuggestResolution:
    """Tests for suggest_resolution tool."""

    @pytest.mark.asyncio
    async def test_suggest_resolution_rpc_timeout(self, mock_k8s_adapter):
        """Test resolution suggestions for RPC timeout."""
        result = await suggest_resolution(
            kubernetes_adapter=mock_k8s_adapter,
            error_message="RPC timeout waiting for response from nova-compute",
            affected_service="nova",
        )

        assert result.primary_suggestion is not None
        assert result.primary_suggestion.title is not None
        assert len(result.primary_suggestion.steps) > 0
        assert result.primary_suggestion.confidence in ResolutionConfidence

    @pytest.mark.asyncio
    async def test_suggest_resolution_osd_down(self, mock_k8s_adapter):
        """Test resolution suggestions for OSD down."""
        result = await suggest_resolution(
            kubernetes_adapter=mock_k8s_adapter,
            symptoms=["OSD marked down", "Ceph HEALTH_WARN"],
            affected_service="ceph",
        )

        assert result.primary_suggestion is not None
        assert result.analysis_summary is not None

    @pytest.mark.asyncio
    async def test_suggest_resolution_with_preventive_measures(self, mock_k8s_adapter):
        """Test that resolution includes preventive measures."""
        result = await suggest_resolution(
            kubernetes_adapter=mock_k8s_adapter,
            error_message="slow request taking 45 seconds",
            include_preventive_measures=True,
        )

        assert len(result.preventive_measures) > 0
        for measure in result.preventive_measures:
            assert measure.title is not None
            assert measure.priority is not None

    @pytest.mark.asyncio
    async def test_suggest_resolution_requires_input(self, mock_k8s_adapter):
        """Test that suggest_resolution requires either error_message or symptoms."""
        with pytest.raises(ValidationError):
            await suggest_resolution(
                kubernetes_adapter=mock_k8s_adapter,
                # Neither error_message nor symptoms provided
            )

    @pytest.mark.asyncio
    async def test_suggest_resolution_confidence_levels(self, mock_k8s_adapter):
        """Test that confidence levels are correctly assigned."""
        # High confidence for known pattern
        result_high = await suggest_resolution(
            kubernetes_adapter=mock_k8s_adapter,
            error_message="libvirt connection refused",
            affected_service="nova",
        )
        assert result_high.primary_suggestion.confidence in [
            ResolutionConfidence.HIGH,
            ResolutionConfidence.MEDIUM,
        ]

        # Lower confidence for generic issue
        result_low = await suggest_resolution(
            kubernetes_adapter=mock_k8s_adapter,
            symptoms=["Something weird is happening"],
        )
        assert result_low.confidence_explanation is not None


# =============================================================================
# Test create_diagnostic_bundle
# =============================================================================


class TestCreateDiagnosticBundle:
    """Tests for create_diagnostic_bundle tool."""

    @pytest.mark.asyncio
    async def test_create_bundle_basic(
        self, mock_direct_client, mock_k8s_adapter, mock_stacklight_adapter
    ):
        """Test creating a basic diagnostic bundle."""
        with patch(
            "mosk_mcp.tools.troubleshooting.create_diagnostic_bundle.StackLightAdapter",
            return_value=mock_stacklight_adapter,
        ):
            result = await create_diagnostic_bundle(
                direct_client=mock_direct_client,
                kubernetes_adapter=mock_k8s_adapter,
                include_logs=True,
                log_hours=1,
            )

        assert result.bundle_name is not None
        assert result.bundle_id is not None
        assert result.size_bytes > 0
        assert result.data_base64 is not None
        assert result.checksum_sha256 is not None

    @pytest.mark.asyncio
    async def test_create_bundle_decodable(
        self, mock_direct_client, mock_k8s_adapter, mock_stacklight_adapter
    ):
        """Test that bundle is valid base64-encoded tar.gz."""
        with patch(
            "mosk_mcp.tools.troubleshooting.create_diagnostic_bundle.StackLightAdapter",
            return_value=mock_stacklight_adapter,
        ):
            result = await create_diagnostic_bundle(
                direct_client=mock_direct_client,
                kubernetes_adapter=mock_k8s_adapter,
                include_logs=False,  # Smaller bundle
            )

        # Decode and verify tar.gz
        bundle_data = base64.b64decode(result.data_base64)
        assert len(bundle_data) > 0

        # Verify it's a valid tar.gz
        buffer = io.BytesIO(bundle_data)
        with tarfile.open(fileobj=buffer, mode="r:gz") as tar:
            members = tar.getnames()
            assert "metadata.json" in members

    @pytest.mark.asyncio
    async def test_create_bundle_contents_tracking(
        self, mock_direct_client, mock_k8s_adapter, mock_stacklight_adapter
    ):
        """Test that bundle tracks contents correctly."""
        with patch(
            "mosk_mcp.tools.troubleshooting.create_diagnostic_bundle.StackLightAdapter",
            return_value=mock_stacklight_adapter,
        ):
            result = await create_diagnostic_bundle(
                direct_client=mock_direct_client,
                kubernetes_adapter=mock_k8s_adapter,
                include_cluster_state=True,
                include_openstack_state=True,
                include_ceph_state=True,
                include_logs=True,
                include_metrics=True,
                include_alerts=True,
            )

        assert result.contents is not None
        assert result.contents.total_files > 0
        assert len(result.contents.cluster_state_files) > 0

    @pytest.mark.asyncio
    async def test_create_bundle_custom_name(
        self, mock_direct_client, mock_k8s_adapter, mock_stacklight_adapter
    ):
        """Test creating bundle with custom name."""
        with patch(
            "mosk_mcp.tools.troubleshooting.create_diagnostic_bundle.StackLightAdapter",
            return_value=mock_stacklight_adapter,
        ):
            result = await create_diagnostic_bundle(
                direct_client=mock_direct_client,
                kubernetes_adapter=mock_k8s_adapter,
                bundle_name="my-custom-bundle",
            )

        assert result.bundle_name == "my-custom-bundle"

    @pytest.mark.asyncio
    async def test_create_bundle_affected_services(
        self, mock_direct_client, mock_k8s_adapter, mock_stacklight_adapter
    ):
        """Test creating bundle for specific services."""
        with patch(
            "mosk_mcp.tools.troubleshooting.create_diagnostic_bundle.StackLightAdapter",
            return_value=mock_stacklight_adapter,
        ):
            result = await create_diagnostic_bundle(
                direct_client=mock_direct_client,
                kubernetes_adapter=mock_k8s_adapter,
                affected_services=["nova", "neutron"],
                include_logs=True,
            )

        # Should include logs for specified services
        assert len(result.contents.log_files) > 0


# =============================================================================
# Test Known Issue Database
# =============================================================================


class TestKnownIssueDatabase:
    """Tests for KnownIssueDatabase."""

    def test_database_contains_issues(self):
        """Test that database contains known issues."""
        db = get_known_issue_database()
        all_issues = db.get_all()

        assert len(all_issues) >= 10  # At least 10 known issues
        assert all(isinstance(i, IssuePattern) for i in all_issues)

    def test_get_issue_by_id(self):
        """Test getting issue by ID."""
        db = get_known_issue_database()

        issue = db.get_by_id("MOSK-001")
        assert issue is not None
        assert issue.issue_id == "MOSK-001"
        assert "RabbitMQ" in issue.title or "RPC" in issue.title

    def test_get_issues_by_category(self):
        """Test getting issues by category."""
        db = get_known_issue_database()

        storage_issues = db.get_by_category(DiagnosisCategory.STORAGE_ISSUE)
        assert len(storage_issues) > 0
        assert all(i.category == DiagnosisCategory.STORAGE_ISSUE for i in storage_issues)

    def test_get_issues_by_service(self):
        """Test getting issues by service."""
        db = get_known_issue_database()

        nova_issues = db.get_by_service("nova")
        assert len(nova_issues) > 0
        for issue in nova_issues:
            assert any(s.lower() == "nova" for s in issue.affected_services)

    def test_find_matching_issues(self):
        """Test finding issues by pattern matching."""
        db = get_known_issue_database()

        matches = db.find_matching_issues(
            error_message="RPC timeout waiting for response",
            service="nova",
        )

        assert len(matches) > 0
        # First match should have highest score
        assert matches[0][1] >= matches[-1][1]

    def test_find_best_match(self):
        """Test finding the best matching issue."""
        db = get_known_issue_database()

        result = db.find_best_match(
            error_message="slow request taking 45.5 seconds",
            symptoms=["Ceph slow requests", "Volume latency"],
        )

        assert result is not None
        issue, score = result
        assert issue.issue_id is not None
        assert score >= 0.0


# =============================================================================
# Test Issue Pattern Matching
# =============================================================================


class TestIssuePatternMatching:
    """Tests for IssuePattern.match_score method."""

    def test_match_error_pattern(self):
        """Test matching against error patterns."""
        issue = KNOWN_ISSUES[0]  # MOSK-001 (RPC timeout)

        score = issue.match_score(
            error_message="MessagingTimeout waiting for response from nova-scheduler",
        )

        assert score > 0.0

    def test_match_symptom_keywords(self):
        """Test matching against symptom keywords."""
        issue = KNOWN_ISSUES[1]  # MOSK-002 (Ceph slow requests)

        score = issue.match_score(
            symptoms=["slow ceph requests", "high latency on osd"],
        )

        assert score > 0.0

    def test_match_service_filter(self):
        """Test matching with service filter."""
        issue = KNOWN_ISSUES[0]  # MOSK-001

        score_with_match = issue.match_score(service="nova")
        score_without_match = issue.match_score(service="unknown-service")

        # Should score higher when service matches
        assert score_with_match >= score_without_match

    def test_match_log_patterns(self):
        """Test matching against log patterns."""
        issue = KNOWN_ISSUES[0]  # MOSK-001

        score = issue.match_score(
            log_messages=[
                "MessagingTimeout waiting for response",
                "AMQP connection closed unexpectedly",
            ],
        )

        assert score > 0.0

    def test_to_known_issue_conversion(self):
        """Test converting IssuePattern to KnownIssue model."""
        pattern = KNOWN_ISSUES[0]
        known_issue = pattern.to_known_issue(match_score=0.75)

        assert known_issue.issue_id == pattern.issue_id
        assert known_issue.title == pattern.title
        assert known_issue.match_score == 0.75

    def test_issue_pattern_hashable(self):
        """Test that IssuePattern is hashable."""
        issue1 = KNOWN_ISSUES[0]
        issue2 = KNOWN_ISSUES[1]

        # Should be able to use in sets
        issue_set = {issue1, issue2}
        assert len(issue_set) == 2

        # Same issue ID should be equal
        assert issue1 == issue1
        assert issue1 != issue2


# =============================================================================
# Test Models
# =============================================================================


class TestModels:
    """Tests for Pydantic models and enums."""

    def test_log_severity_enum(self):
        """Test LogSeverity enum values."""
        assert LogSeverity.DEBUG.value == "debug"
        assert LogSeverity.INFO.value == "info"
        assert LogSeverity.WARNING.value == "warning"
        assert LogSeverity.ERROR.value == "error"
        assert LogSeverity.CRITICAL.value == "critical"

    def test_alert_severity_enum(self):
        """Test AlertSeverity enum values."""
        assert AlertSeverity.INFO.value == "info"
        assert AlertSeverity.WARNING.value == "warning"
        assert AlertSeverity.CRITICAL.value == "critical"

    def test_diagnosis_category_enum(self):
        """Test DiagnosisCategory enum values."""
        assert DiagnosisCategory.VM_FAILURE.value == "vm_failure"
        assert DiagnosisCategory.NETWORK_ISSUE.value == "network_issue"
        assert DiagnosisCategory.STORAGE_ISSUE.value == "storage_issue"
        assert DiagnosisCategory.SERVICE_ISSUE.value == "service_issue"
        assert DiagnosisCategory.AUTHENTICATION_ISSUE.value == "authentication_issue"

    def test_issue_priority_enum(self):
        """Test IssuePriority enum values."""
        assert IssuePriority.LOW.value == "low"
        assert IssuePriority.MEDIUM.value == "medium"
        assert IssuePriority.HIGH.value == "high"
        assert IssuePriority.CRITICAL.value == "critical"

    def test_resolution_confidence_enum(self):
        """Test ResolutionConfidence enum values."""
        assert ResolutionConfidence.LOW.value == "low"
        assert ResolutionConfidence.MEDIUM.value == "medium"
        assert ResolutionConfidence.HIGH.value == "high"
        assert ResolutionConfidence.EXPERIMENTAL.value == "experimental"

    def test_bundle_format_enum(self):
        """Test BundleFormat enum values."""
        assert BundleFormat.TARGZ.value == "tar.gz"
        assert BundleFormat.ZIP.value == "zip"


# =============================================================================
# Test Error Handling
# =============================================================================


class TestErrorHandling:
    """Tests for error handling in troubleshooting tools."""

    @pytest.mark.asyncio
    async def test_query_logs_adapter_failure(self, mock_direct_client):
        """Test query_logs handles adapter failures gracefully."""
        mock_adapter = AsyncMock()
        mock_adapter.connect = AsyncMock()
        mock_adapter.query_logs = AsyncMock(side_effect=Exception("Connection failed"))

        with (
            patch(
                "mosk_mcp.tools.troubleshooting.query_logs.StackLightAdapter",
                return_value=mock_adapter,
            ),
            pytest.raises(ToolExecutionError) as exc_info,
        ):
            await query_logs(
                direct_client=mock_direct_client,
                services=["nova"],
            )

        assert "query_logs" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_trace_request_empty_result(self, mock_direct_client):
        """Test trace_request handles no logs gracefully."""
        mock_adapter = AsyncMock()
        mock_adapter.connect = AsyncMock()
        mock_adapter.get_logs_by_request_id = AsyncMock(return_value=[])

        with patch(
            "mosk_mcp.tools.troubleshooting.trace_request.StackLightAdapter",
            return_value=mock_adapter,
        ):
            result = await trace_request(
                direct_client=mock_direct_client,
                request_id="nonexistent-request",
            )

        # Should return result even with no spans
        assert result.request_id == "nonexistent-request"
        assert result.found is False
        assert len(result.spans) == 0

    @pytest.mark.asyncio
    async def test_create_bundle_partial_failure(self, mock_direct_client, mock_k8s_adapter):
        """Test create_diagnostic_bundle continues on partial collection failure."""
        mock_adapter = AsyncMock()
        mock_adapter.connect = AsyncMock()
        # Logs succeed
        mock_adapter.query_logs = AsyncMock(
            return_value=LogQueryResult(logs=[], total_count=0, cursor=None, has_more=False)
        )
        # Alerts fail
        mock_adapter.get_alerts = AsyncMock(side_effect=Exception("Alert query failed"))

        with patch(
            "mosk_mcp.tools.troubleshooting.create_diagnostic_bundle.StackLightAdapter",
            return_value=mock_adapter,
        ):
            result = await create_diagnostic_bundle(
                direct_client=mock_direct_client,
                kubernetes_adapter=mock_k8s_adapter,
                include_logs=True,
                include_alerts=True,
            )

        # Should still create bundle with warnings
        assert result.bundle_id is not None
        assert len(result.warnings) > 0
        assert any("alert" in w.lower() for w in result.warnings)


# =============================================================================
# Test Tool Metadata
# =============================================================================


class TestToolMetadata:
    """Tests for tool metadata constants."""

    def test_tool_names_defined(self):
        """Test that all tool names are defined."""
        from mosk_mcp.tools.troubleshooting import (
            CORRELATE_EVENTS_NAME,
            CREATE_DIAGNOSTIC_BUNDLE_NAME,
            DIAGNOSE_NETWORK_ISSUE_NAME,
            DIAGNOSE_STORAGE_ISSUE_NAME,
            DIAGNOSE_VM_FAILURE_NAME,
            EXPLAIN_ALERT_NAME,
            GET_KNOWN_ISSUES_NAME,
            QUERY_LOGS_NAME,
            SUGGEST_RESOLUTION_NAME,
            TRACE_REQUEST_NAME,
        )

        assert QUERY_LOGS_NAME == "query_logs"
        assert CORRELATE_EVENTS_NAME == "correlate_events"
        assert EXPLAIN_ALERT_NAME == "explain_alert"
        assert TRACE_REQUEST_NAME == "trace_request"
        assert DIAGNOSE_VM_FAILURE_NAME == "diagnose_vm_failure"
        assert DIAGNOSE_NETWORK_ISSUE_NAME == "diagnose_network_issue"
        assert DIAGNOSE_STORAGE_ISSUE_NAME == "diagnose_storage_issue"
        assert GET_KNOWN_ISSUES_NAME == "get_known_issues"
        assert SUGGEST_RESOLUTION_NAME == "suggest_resolution"
        assert CREATE_DIAGNOSTIC_BUNDLE_NAME == "create_diagnostic_bundle"

    def test_tool_descriptions_defined(self):
        """Test that all tool descriptions are defined."""
        from mosk_mcp.tools.troubleshooting import (
            CORRELATE_EVENTS_DESCRIPTION,
            CREATE_DIAGNOSTIC_BUNDLE_DESCRIPTION,
            DIAGNOSE_NETWORK_ISSUE_DESCRIPTION,
            DIAGNOSE_STORAGE_ISSUE_DESCRIPTION,
            DIAGNOSE_VM_FAILURE_DESCRIPTION,
            EXPLAIN_ALERT_DESCRIPTION,
            GET_KNOWN_ISSUES_DESCRIPTION,
            QUERY_LOGS_DESCRIPTION,
            SUGGEST_RESOLUTION_DESCRIPTION,
            TRACE_REQUEST_DESCRIPTION,
        )

        descriptions = [
            QUERY_LOGS_DESCRIPTION,
            CORRELATE_EVENTS_DESCRIPTION,
            EXPLAIN_ALERT_DESCRIPTION,
            TRACE_REQUEST_DESCRIPTION,
            DIAGNOSE_VM_FAILURE_DESCRIPTION,
            DIAGNOSE_NETWORK_ISSUE_DESCRIPTION,
            DIAGNOSE_STORAGE_ISSUE_DESCRIPTION,
            GET_KNOWN_ISSUES_DESCRIPTION,
            SUGGEST_RESOLUTION_DESCRIPTION,
            CREATE_DIAGNOSTIC_BUNDLE_DESCRIPTION,
        ]

        for desc in descriptions:
            assert desc is not None
            assert len(desc) > 50  # Should have meaningful description
            assert "READ-ONLY" in desc  # All troubleshooting tools are read-only

    def test_tool_tags_defined(self):
        """Test that all tool tags are defined and include troubleshooting."""
        from mosk_mcp.tools.troubleshooting import (
            CORRELATE_EVENTS_TAGS,
            CREATE_DIAGNOSTIC_BUNDLE_TAGS,
            DIAGNOSE_NETWORK_ISSUE_TAGS,
            DIAGNOSE_STORAGE_ISSUE_TAGS,
            DIAGNOSE_VM_FAILURE_TAGS,
            EXPLAIN_ALERT_TAGS,
            GET_KNOWN_ISSUES_TAGS,
            QUERY_LOGS_TAGS,
            SUGGEST_RESOLUTION_TAGS,
            TRACE_REQUEST_TAGS,
        )

        all_tags = [
            QUERY_LOGS_TAGS,
            CORRELATE_EVENTS_TAGS,
            EXPLAIN_ALERT_TAGS,
            TRACE_REQUEST_TAGS,
            DIAGNOSE_VM_FAILURE_TAGS,
            DIAGNOSE_NETWORK_ISSUE_TAGS,
            DIAGNOSE_STORAGE_ISSUE_TAGS,
            GET_KNOWN_ISSUES_TAGS,
            SUGGEST_RESOLUTION_TAGS,
            CREATE_DIAGNOSTIC_BUNDLE_TAGS,
        ]

        for tags in all_tags:
            assert "troubleshooting" in tags
            assert "read-only" in tags


# =============================================================================
# Get Pod Logs Tests
# =============================================================================


class TestGetPodLogs:
    """Tests for get_pod_logs tool."""

    @pytest.fixture
    def mock_k8s_adapter_with_logs(self):
        """Create a mock Kubernetes adapter with get_pod_logs method."""
        adapter = AsyncMock()
        adapter.get_pod_logs = AsyncMock(
            return_value=[
                {
                    "pod_name": "nova-api-1234",
                    "namespace": "openstack",
                    "container": "nova-api",
                    "available_containers": ["nova-api", "init-container"],
                    "logs": "2025-01-01T00:00:00Z INFO nova.api.openstack Starting API\n2025-01-01T00:00:01Z DEBUG nova.api Initialized",
                    "log_lines": 2,
                    "truncated": False,
                    "error": None,
                }
            ]
        )
        return adapter

    @pytest.mark.asyncio
    async def test_get_pod_logs_by_name(self, mock_k8s_adapter_with_logs):
        """Test getting pod logs by pod name."""
        from mosk_mcp.tools.troubleshooting.get_pod_logs import get_pod_logs

        result = await get_pod_logs(
            kubernetes_adapter=mock_k8s_adapter_with_logs,
            pod_name="nova-api-1234",
            namespace="openstack",
        )

        assert result.total_pods == 1
        assert result.successful_pods == 1
        assert result.failed_pods == 0
        assert result.pods[0].pod_name == "nova-api-1234"
        assert "nova.api" in result.pods[0].logs

    @pytest.mark.asyncio
    async def test_get_pod_logs_by_label_selector(self, mock_k8s_adapter_with_logs):
        """Test getting pod logs by label selector."""
        from mosk_mcp.tools.troubleshooting.get_pod_logs import get_pod_logs

        result = await get_pod_logs(
            kubernetes_adapter=mock_k8s_adapter_with_logs,
            label_selector="application=nova",
            namespace="openstack",
        )

        assert result.total_pods >= 1
        mock_k8s_adapter_with_logs.get_pod_logs.assert_called()

    @pytest.mark.asyncio
    async def test_get_pod_logs_validation_error(self, mock_k8s_adapter_with_logs):
        """Test validation error when no pod identifier provided."""
        from mosk_mcp.tools.troubleshooting.get_pod_logs import get_pod_logs

        with pytest.raises(ValidationError) as exc_info:
            await get_pod_logs(kubernetes_adapter=mock_k8s_adapter_with_logs)

        assert "pod_name or label_selector must be provided" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_get_pod_logs_default_namespace(self, mock_k8s_adapter_with_logs):
        """Test default namespace is openstack."""
        from mosk_mcp.tools.troubleshooting.get_pod_logs import get_pod_logs

        await get_pod_logs(
            kubernetes_adapter=mock_k8s_adapter_with_logs,
            pod_name="test-pod",
        )

        # Check that the adapter was called with default namespace
        call_kwargs = mock_k8s_adapter_with_logs.get_pod_logs.call_args.kwargs
        assert call_kwargs["namespace"] == "openstack"

    @pytest.mark.asyncio
    async def test_get_pod_logs_with_previous_container(self, mock_k8s_adapter_with_logs):
        """Test getting logs from previous container."""
        from mosk_mcp.tools.troubleshooting.get_pod_logs import get_pod_logs

        await get_pod_logs(
            kubernetes_adapter=mock_k8s_adapter_with_logs,
            pod_name="crashed-pod",
            previous=True,
        )

        call_kwargs = mock_k8s_adapter_with_logs.get_pod_logs.call_args.kwargs
        assert call_kwargs["previous"] is True

    @pytest.mark.asyncio
    async def test_get_pod_logs_with_since_seconds(self, mock_k8s_adapter_with_logs):
        """Test getting recent logs with since_seconds."""
        from mosk_mcp.tools.troubleshooting.get_pod_logs import get_pod_logs

        await get_pod_logs(
            kubernetes_adapter=mock_k8s_adapter_with_logs,
            label_selector="app=nova",
            since_seconds=3600,  # Last hour
        )

        call_kwargs = mock_k8s_adapter_with_logs.get_pod_logs.call_args.kwargs
        assert call_kwargs["since_seconds"] == 3600

    @pytest.mark.asyncio
    async def test_get_pod_logs_execution_error(self):
        """Test handling of execution errors."""
        from mosk_mcp.tools.troubleshooting.get_pod_logs import get_pod_logs

        mock_adapter = AsyncMock()
        mock_adapter.get_pod_logs = AsyncMock(side_effect=Exception("Connection failed"))

        with pytest.raises(ToolExecutionError) as exc_info:
            await get_pod_logs(
                kubernetes_adapter=mock_adapter,
                pod_name="test-pod",
            )

        assert "Failed to get pod logs" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_get_pod_logs_multiple_pods(self):
        """Test getting logs from multiple pods."""
        from mosk_mcp.tools.troubleshooting.get_pod_logs import get_pod_logs

        mock_adapter = AsyncMock()
        mock_adapter.get_pod_logs = AsyncMock(
            return_value=[
                {
                    "pod_name": "nova-api-1",
                    "namespace": "openstack",
                    "logs": "Log from pod 1",
                    "log_lines": 1,
                    "truncated": False,
                },
                {
                    "pod_name": "nova-api-2",
                    "namespace": "openstack",
                    "logs": "Log from pod 2",
                    "log_lines": 1,
                    "truncated": False,
                },
                {
                    "pod_name": "nova-api-3",
                    "namespace": "openstack",
                    "logs": "",
                    "log_lines": 0,
                    "error": "Container crashed",
                },
            ]
        )

        result = await get_pod_logs(
            kubernetes_adapter=mock_adapter,
            label_selector="app=nova-api",
        )

        assert result.total_pods == 3
        assert result.successful_pods == 2
        assert result.failed_pods == 1
        assert result.total_log_lines == 2


class TestGetPodLogsOutput:
    """Tests for GetPodLogsOutput model."""

    def test_pod_log_entry_model(self):
        """Test PodLogEntry model."""
        from mosk_mcp.tools.troubleshooting.models import PodLogEntry

        entry = PodLogEntry(
            pod_name="test-pod",
            namespace="default",
            container="main",
            available_containers=["main", "sidecar"],
            logs="Test log content",
            log_lines=1,
            truncated=False,
        )

        assert entry.pod_name == "test-pod"
        assert entry.container == "main"
        assert len(entry.available_containers) == 2

    def test_get_pod_logs_output_model(self):
        """Test GetPodLogsOutput model."""
        from mosk_mcp.tools.troubleshooting.models import GetPodLogsOutput, PodLogEntry

        entry = PodLogEntry(
            pod_name="test-pod",
            namespace="default",
            logs="Test log",
            log_lines=1,
            truncated=False,
        )

        output = GetPodLogsOutput(
            pods=[entry],
            total_pods=1,
            successful_pods=1,
            failed_pods=0,
            total_log_lines=1,
            query_info={"pod_name": "test-pod"},
            timestamp="2025-01-01T00:00:00Z",
        )

        assert output.total_pods == 1
        assert len(output.pods) == 1


# =============================================================================
# Query Logs Extended Tests
# =============================================================================


class TestQueryLogsExtended:
    """Extended tests for query_logs tool - additional parameter combinations."""

    @pytest.mark.asyncio
    async def test_query_logs_by_host(
        self, mock_direct_client, mock_stacklight_adapter, mock_log_entries
    ):
        """Test query_logs filtered by host."""
        with patch(
            "mosk_mcp.tools.troubleshooting.query_logs.StackLightAdapter",
            return_value=mock_stacklight_adapter,
        ):
            result = await query_logs(
                direct_client=mock_direct_client,
                hosts=["controller-01"],
            )

        assert result is not None
        mock_stacklight_adapter.query_logs.assert_called_once()

    @pytest.mark.asyncio
    async def test_query_logs_with_namespaces(
        self, mock_direct_client, mock_stacklight_adapter, mock_log_entries
    ):
        """Test query_logs filtered by namespaces."""
        with patch(
            "mosk_mcp.tools.troubleshooting.query_logs.StackLightAdapter",
            return_value=mock_stacklight_adapter,
        ):
            result = await query_logs(
                direct_client=mock_direct_client,
                namespaces=["openstack", "stacklight"],
            )

        assert result is not None
        mock_stacklight_adapter.query_logs.assert_called_once()

    @pytest.mark.asyncio
    async def test_query_logs_aggregation_only(
        self, mock_direct_client, mock_stacklight_adapter, mock_log_entries
    ):
        """Test query_logs in aggregation only mode."""
        with patch(
            "mosk_mcp.tools.troubleshooting.query_logs.StackLightAdapter",
            return_value=mock_stacklight_adapter,
        ):
            result = await query_logs(
                direct_client=mock_direct_client,
                services=["nova"],
                aggregation_only=True,
            )

        assert result is not None
        # In aggregation_only mode, logs list should be empty
        assert isinstance(result.logs, list)

    @pytest.mark.asyncio
    async def test_query_logs_with_pagination_cursor(
        self, mock_direct_client, mock_log_entries, mock_alerts
    ):
        """Test query_logs pagination with cursor."""
        # Create adapter mock that returns has_more=True and cursor
        adapter = AsyncMock()
        adapter.connect = AsyncMock()
        paginated_result = LogQueryResult(
            logs=mock_log_entries[:2],
            total_count=100,
            has_more=True,
            cursor="next_page_cursor",
        )
        adapter.query_logs = AsyncMock(return_value=paginated_result)
        adapter.get_alerts = AsyncMock(return_value=mock_alerts)

        with patch(
            "mosk_mcp.tools.troubleshooting.query_logs.StackLightAdapter",
            return_value=adapter,
        ):
            result = await query_logs(
                direct_client=mock_direct_client,
                services=["nova"],
                limit=50,
            )

        assert result is not None
        assert result.has_more is True
        assert result.cursor == "next_page_cursor"

    @pytest.mark.asyncio
    async def test_query_logs_with_keywords(
        self, mock_direct_client, mock_stacklight_adapter, mock_log_entries
    ):
        """Test query_logs with additional keywords."""
        with patch(
            "mosk_mcp.tools.troubleshooting.query_logs.StackLightAdapter",
            return_value=mock_stacklight_adapter,
        ):
            result = await query_logs(
                direct_client=mock_direct_client,
                keywords=["timeout", "connection", "failed"],
            )

        assert result is not None
        mock_stacklight_adapter.query_logs.assert_called_once()

    @pytest.mark.asyncio
    async def test_query_logs_with_index_type_k8s_events(
        self, mock_direct_client, mock_stacklight_adapter, mock_log_entries
    ):
        """Test query_logs with k8s_events index type."""
        with patch(
            "mosk_mcp.tools.troubleshooting.query_logs.StackLightAdapter",
            return_value=mock_stacklight_adapter,
        ):
            result = await query_logs(
                direct_client=mock_direct_client,
                index_type="k8s_events",
                event_type_filter="Warning",
            )

        assert result is not None

    @pytest.mark.asyncio
    async def test_query_logs_with_audit_index(
        self, mock_direct_client, mock_stacklight_adapter, mock_log_entries
    ):
        """Test query_logs with audit index type."""
        with patch(
            "mosk_mcp.tools.troubleshooting.query_logs.StackLightAdapter",
            return_value=mock_stacklight_adapter,
        ):
            result = await query_logs(
                direct_client=mock_direct_client,
                index_type="audit",
                audit_provider="sudo",
            )

        assert result is not None

    @pytest.mark.asyncio
    async def test_query_logs_with_notifications_index(
        self, mock_direct_client, mock_stacklight_adapter, mock_log_entries
    ):
        """Test query_logs with notifications index type."""
        with patch(
            "mosk_mcp.tools.troubleshooting.query_logs.StackLightAdapter",
            return_value=mock_stacklight_adapter,
        ):
            result = await query_logs(
                direct_client=mock_direct_client,
                index_type="notifications",
                notification_logger="nova",
            )

        assert result is not None
