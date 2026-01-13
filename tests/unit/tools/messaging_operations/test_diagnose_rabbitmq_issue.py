"""Unit tests for diagnose_rabbitmq_issue tool."""

from unittest.mock import AsyncMock, patch

import pytest

from mosk_mcp.core.exceptions import ToolExecutionError
from mosk_mcp.tools.messaging_operations.diagnose_rabbitmq_issue import (
    CONNECTION_CRITICAL_THRESHOLD,
    CONNECTION_WARNING_THRESHOLD,
    MEMORY_CRITICAL_THRESHOLD,
    MEMORY_WARNING_THRESHOLD,
    STALE_QUEUE_THRESHOLD,
    _create_check,
    _generate_recommendations,
    diagnose_rabbitmq_issue,
)
from mosk_mcp.tools.messaging_operations.models import (
    GetRabbitMQConnectionsOutput,
    GetRabbitMQStatusOutput,
    ListRabbitMQQueuesOutput,
    RabbitMQHealthLevel,
    RabbitMQInstanceDiagnosis,
    RabbitMQNodeInfo,
)


class TestCreateCheck:
    """Tests for _create_check helper function."""

    def test_passed_check(self) -> None:
        """Test creating a passed check."""
        check = _create_check(
            name="test_check",
            passed=True,
            message="Test passed",
            severity="info",
        )
        assert check.check_name == "test_check"
        assert check.status == "pass"
        assert check.message == "Test passed"
        assert check.severity == "info"
        assert check.details == {}

    def test_failed_check_warning(self) -> None:
        """Test creating a failed check with warning severity."""
        check = _create_check(
            name="test_check",
            passed=False,
            message="Test warning",
            severity="warning",
        )
        assert check.status == "warn"
        assert check.severity == "warning"

    def test_failed_check_error(self) -> None:
        """Test creating a failed check with error severity."""
        check = _create_check(
            name="test_check",
            passed=False,
            message="Test error",
            severity="error",
        )
        assert check.status == "fail"
        assert check.severity == "error"

    def test_failed_check_critical(self) -> None:
        """Test creating a failed check with critical severity."""
        check = _create_check(
            name="test_check",
            passed=False,
            message="Test critical",
            severity="critical",
        )
        assert check.status == "fail"
        assert check.severity == "critical"

    def test_check_with_details(self) -> None:
        """Test creating a check with details."""
        details = {"key": "value", "count": 42}
        check = _create_check(
            name="test_check",
            passed=True,
            message="Test message",
            details=details,
        )
        assert check.details == details


class TestGenerateRecommendations:
    """Tests for _generate_recommendations function."""

    def test_healthy_recommendations(self) -> None:
        """Test recommendations for healthy cluster."""
        instances = [
            RabbitMQInstanceDiagnosis(
                instance="main",
                health=RabbitMQHealthLevel.HEALTHY,
            )
        ]
        result = _generate_recommendations(
            instances=instances,
            overall_health=RabbitMQHealthLevel.HEALTHY,
            critical_issues=[],
        )
        assert any("healthy" in r.lower() for r in result)

    def test_critical_recommendations(self) -> None:
        """Test recommendations for critical issues."""
        instances = [
            RabbitMQInstanceDiagnosis(
                instance="main",
                health=RabbitMQHealthLevel.CRITICAL,
            )
        ]
        result = _generate_recommendations(
            instances=instances,
            overall_health=RabbitMQHealthLevel.CRITICAL,
            critical_issues=["Critical issue"],
        )
        assert any("IMMEDIATE ACTION" in r for r in result)

    def test_known_issue_mosk_001(self) -> None:
        """Test recommendations for MOSK-001 known issue."""
        instances = [
            RabbitMQInstanceDiagnosis(
                instance="main",
                health=RabbitMQHealthLevel.CRITICAL,
                known_issue_matches=["MOSK-001"],
            )
        ]
        result = _generate_recommendations(
            instances=instances,
            overall_health=RabbitMQHealthLevel.CRITICAL,
            critical_issues=[],
        )
        assert any("MOSK-001" in r for r in result)

    def test_alarm_recommendations(self) -> None:
        """Test recommendations for alarm issues."""
        instances = [
            RabbitMQInstanceDiagnosis(
                instance="main",
                health=RabbitMQHealthLevel.CRITICAL,
            )
        ]
        result = _generate_recommendations(
            instances=instances,
            overall_health=RabbitMQHealthLevel.CRITICAL,
            critical_issues=["Memory alarm active"],
        )
        assert any("alarm" in r.lower() for r in result)

    def test_blocked_connections_recommendations(self) -> None:
        """Test recommendations for blocked connections."""
        instances = [
            RabbitMQInstanceDiagnosis(
                instance="main",
                health=RabbitMQHealthLevel.CRITICAL,
            )
        ]
        result = _generate_recommendations(
            instances=instances,
            overall_health=RabbitMQHealthLevel.CRITICAL,
            critical_issues=["Blocked connections detected"],
        )
        assert any("blocked" in r.lower() for r in result)

    def test_partition_recommendations(self) -> None:
        """Test recommendations for network partitions."""
        instances = [
            RabbitMQInstanceDiagnosis(
                instance="main",
                health=RabbitMQHealthLevel.CRITICAL,
            )
        ]
        result = _generate_recommendations(
            instances=instances,
            overall_health=RabbitMQHealthLevel.CRITICAL,
            critical_issues=["Network partition detected"],
        )
        assert any("partition" in r.lower() for r in result)


class TestDiagnoseRabbitMQIssue:
    """Tests for diagnose_rabbitmq_issue function."""

    @pytest.fixture
    def mock_kubernetes_adapter(self) -> AsyncMock:
        """Create mock Kubernetes adapter."""
        return AsyncMock()

    @pytest.fixture
    def healthy_status(self) -> GetRabbitMQStatusOutput:
        """Create healthy status output."""
        return GetRabbitMQStatusOutput(
            instance="main",
            cluster_name="rabbit@test",
            health=RabbitMQHealthLevel.HEALTHY,
            health_summary="Healthy",
            running_nodes=3,
            total_nodes=3,
            is_healthy=True,
            is_safe_for_operations=True,
            nodes=[
                RabbitMQNodeInfo(
                    name="rabbit@node1",
                    running=True,
                    memory_percent=50.0,
                )
            ],
            has_alarms=False,
            has_partitions=False,
        )

    @pytest.fixture
    def healthy_queues(self) -> ListRabbitMQQueuesOutput:
        """Create healthy queues output."""
        return ListRabbitMQQueuesOutput(
            instance="main",
            has_backlog=False,
            has_stale_queues=False,
            stale_queue_count=0,
        )

    @pytest.fixture
    def healthy_connections(self) -> GetRabbitMQConnectionsOutput:
        """Create healthy connections output."""
        return GetRabbitMQConnectionsOutput(
            instance="main",
            has_blocked_connections=False,
            connection_utilization_percent=30.0,
        )

    @pytest.mark.asyncio
    async def test_diagnose_healthy_main_instance(
        self,
        mock_kubernetes_adapter: AsyncMock,
        healthy_status: GetRabbitMQStatusOutput,
        healthy_queues: ListRabbitMQQueuesOutput,
        healthy_connections: GetRabbitMQConnectionsOutput,
    ) -> None:
        """Test diagnosing a healthy main instance."""
        with (
            patch(
                "mosk_mcp.tools.messaging_operations.diagnose_rabbitmq_issue.get_rabbitmq_status",
                return_value=healthy_status,
            ),
            patch(
                "mosk_mcp.tools.messaging_operations.diagnose_rabbitmq_issue.list_rabbitmq_queues",
                return_value=healthy_queues,
            ),
            patch(
                "mosk_mcp.tools.messaging_operations.diagnose_rabbitmq_issue.get_rabbitmq_connections",
                return_value=healthy_connections,
            ),
        ):
            result = await diagnose_rabbitmq_issue(
                mock_kubernetes_adapter,
                rabbitmq_instance="main",
            )

        assert result.is_healthy is True
        assert result.overall_health == RabbitMQHealthLevel.HEALTHY
        assert result.requires_immediate_action is False
        assert len(result.instances) == 1
        assert result.instances[0].instance == "main"

    @pytest.mark.asyncio
    async def test_diagnose_all_instances(
        self,
        mock_kubernetes_adapter: AsyncMock,
        healthy_status: GetRabbitMQStatusOutput,
        healthy_queues: ListRabbitMQQueuesOutput,
        healthy_connections: GetRabbitMQConnectionsOutput,
    ) -> None:
        """Test diagnosing all instances."""
        with (
            patch(
                "mosk_mcp.tools.messaging_operations.diagnose_rabbitmq_issue.get_rabbitmq_status",
                return_value=healthy_status,
            ),
            patch(
                "mosk_mcp.tools.messaging_operations.diagnose_rabbitmq_issue.list_rabbitmq_queues",
                return_value=healthy_queues,
            ),
            patch(
                "mosk_mcp.tools.messaging_operations.diagnose_rabbitmq_issue.get_rabbitmq_connections",
                return_value=healthy_connections,
            ),
        ):
            result = await diagnose_rabbitmq_issue(
                mock_kubernetes_adapter,
                rabbitmq_instance="all",
            )

        # Should diagnose both main and neutron
        assert len(result.instances) == 2
        instance_names = [inst.instance for inst in result.instances]
        assert "main" in instance_names
        assert "neutron" in instance_names

    @pytest.mark.asyncio
    async def test_diagnose_nodes_down(
        self,
        mock_kubernetes_adapter: AsyncMock,
        healthy_queues: ListRabbitMQQueuesOutput,
        healthy_connections: GetRabbitMQConnectionsOutput,
    ) -> None:
        """Test detecting nodes down."""
        unhealthy_status = GetRabbitMQStatusOutput(
            instance="main",
            cluster_name="rabbit@test",
            health=RabbitMQHealthLevel.CRITICAL,
            health_summary="Node down",
            running_nodes=2,
            total_nodes=3,
            is_healthy=False,
            is_safe_for_operations=False,
            nodes=[
                RabbitMQNodeInfo(
                    name="rabbit@node1",
                    running=True,
                    memory_percent=50.0,
                )
            ],
            has_alarms=False,
            has_partitions=False,
        )

        with (
            patch(
                "mosk_mcp.tools.messaging_operations.diagnose_rabbitmq_issue.get_rabbitmq_status",
                return_value=unhealthy_status,
            ),
            patch(
                "mosk_mcp.tools.messaging_operations.diagnose_rabbitmq_issue.list_rabbitmq_queues",
                return_value=healthy_queues,
            ),
            patch(
                "mosk_mcp.tools.messaging_operations.diagnose_rabbitmq_issue.get_rabbitmq_connections",
                return_value=healthy_connections,
            ),
        ):
            result = await diagnose_rabbitmq_issue(
                mock_kubernetes_adapter,
                rabbitmq_instance="main",
            )

        assert result.is_healthy is False
        assert any("node" in issue.lower() for issue in result.instances[0].issues_found)

    @pytest.mark.asyncio
    async def test_diagnose_memory_alarm(
        self,
        mock_kubernetes_adapter: AsyncMock,
        healthy_queues: ListRabbitMQQueuesOutput,
        healthy_connections: GetRabbitMQConnectionsOutput,
    ) -> None:
        """Test detecting memory alarm."""
        alarm_status = GetRabbitMQStatusOutput(
            instance="main",
            cluster_name="rabbit@test",
            health=RabbitMQHealthLevel.CRITICAL,
            health_summary="Memory alarm",
            running_nodes=3,
            total_nodes=3,
            is_healthy=False,
            is_safe_for_operations=False,
            nodes=[
                RabbitMQNodeInfo(
                    name="rabbit@node1",
                    running=True,
                    memory_percent=50.0,
                )
            ],
            has_alarms=True,
            alarms=["memory"],
            has_partitions=False,
        )

        with (
            patch(
                "mosk_mcp.tools.messaging_operations.diagnose_rabbitmq_issue.get_rabbitmq_status",
                return_value=alarm_status,
            ),
            patch(
                "mosk_mcp.tools.messaging_operations.diagnose_rabbitmq_issue.list_rabbitmq_queues",
                return_value=healthy_queues,
            ),
            patch(
                "mosk_mcp.tools.messaging_operations.diagnose_rabbitmq_issue.get_rabbitmq_connections",
                return_value=healthy_connections,
            ),
        ):
            result = await diagnose_rabbitmq_issue(
                mock_kubernetes_adapter,
                rabbitmq_instance="main",
            )

        assert result.is_healthy is False
        # Check for critical issues or warning about alarms
        all_issues = result.critical_issues + result.warnings
        assert any("alarm" in issue.lower() for issue in all_issues)

    @pytest.mark.asyncio
    async def test_diagnose_network_partition(
        self,
        mock_kubernetes_adapter: AsyncMock,
        healthy_queues: ListRabbitMQQueuesOutput,
        healthy_connections: GetRabbitMQConnectionsOutput,
    ) -> None:
        """Test detecting network partitions."""
        partition_status = GetRabbitMQStatusOutput(
            instance="main",
            cluster_name="rabbit@test",
            health=RabbitMQHealthLevel.CRITICAL,
            health_summary="Network partition",
            running_nodes=3,
            total_nodes=3,
            is_healthy=False,
            is_safe_for_operations=False,
            nodes=[
                RabbitMQNodeInfo(
                    name="rabbit@node1",
                    running=True,
                    memory_percent=50.0,
                )
            ],
            has_alarms=False,
            has_partitions=True,
            partitions=["rabbit@node2"],
        )

        with (
            patch(
                "mosk_mcp.tools.messaging_operations.diagnose_rabbitmq_issue.get_rabbitmq_status",
                return_value=partition_status,
            ),
            patch(
                "mosk_mcp.tools.messaging_operations.diagnose_rabbitmq_issue.list_rabbitmq_queues",
                return_value=healthy_queues,
            ),
            patch(
                "mosk_mcp.tools.messaging_operations.diagnose_rabbitmq_issue.get_rabbitmq_connections",
                return_value=healthy_connections,
            ),
        ):
            result = await diagnose_rabbitmq_issue(
                mock_kubernetes_adapter,
                rabbitmq_instance="main",
            )

        assert result.is_healthy is False
        all_issues = result.critical_issues + result.warnings
        assert any("partition" in issue.lower() for issue in all_issues)

    @pytest.mark.asyncio
    async def test_diagnose_high_memory(
        self,
        mock_kubernetes_adapter: AsyncMock,
        healthy_queues: ListRabbitMQQueuesOutput,
        healthy_connections: GetRabbitMQConnectionsOutput,
    ) -> None:
        """Test detecting high memory usage."""
        high_memory_status = GetRabbitMQStatusOutput(
            instance="main",
            cluster_name="rabbit@test",
            health=RabbitMQHealthLevel.WARNING,
            health_summary="High memory",
            running_nodes=3,
            total_nodes=3,
            is_healthy=True,
            is_safe_for_operations=True,
            nodes=[
                RabbitMQNodeInfo(
                    name="rabbit@node1",
                    running=True,
                    memory_percent=MEMORY_WARNING_THRESHOLD + 5,
                )
            ],
            has_alarms=False,
            has_partitions=False,
        )

        with (
            patch(
                "mosk_mcp.tools.messaging_operations.diagnose_rabbitmq_issue.get_rabbitmq_status",
                return_value=high_memory_status,
            ),
            patch(
                "mosk_mcp.tools.messaging_operations.diagnose_rabbitmq_issue.list_rabbitmq_queues",
                return_value=healthy_queues,
            ),
            patch(
                "mosk_mcp.tools.messaging_operations.diagnose_rabbitmq_issue.get_rabbitmq_connections",
                return_value=healthy_connections,
            ),
        ):
            result = await diagnose_rabbitmq_issue(
                mock_kubernetes_adapter,
                rabbitmq_instance="main",
            )

        assert result.overall_health == RabbitMQHealthLevel.WARNING
        all_issues = result.critical_issues + result.warnings + result.instances[0].issues_found
        assert any("memory" in issue.lower() for issue in all_issues)

    @pytest.mark.asyncio
    async def test_diagnose_critical_memory(
        self,
        mock_kubernetes_adapter: AsyncMock,
        healthy_queues: ListRabbitMQQueuesOutput,
        healthy_connections: GetRabbitMQConnectionsOutput,
    ) -> None:
        """Test detecting critical memory usage."""
        critical_memory_status = GetRabbitMQStatusOutput(
            instance="main",
            cluster_name="rabbit@test",
            health=RabbitMQHealthLevel.CRITICAL,
            health_summary="Critical memory",
            running_nodes=3,
            total_nodes=3,
            is_healthy=False,
            is_safe_for_operations=False,
            nodes=[
                RabbitMQNodeInfo(
                    name="rabbit@node1",
                    running=True,
                    memory_percent=MEMORY_CRITICAL_THRESHOLD + 5,
                )
            ],
            has_alarms=False,
            has_partitions=False,
        )

        with (
            patch(
                "mosk_mcp.tools.messaging_operations.diagnose_rabbitmq_issue.get_rabbitmq_status",
                return_value=critical_memory_status,
            ),
            patch(
                "mosk_mcp.tools.messaging_operations.diagnose_rabbitmq_issue.list_rabbitmq_queues",
                return_value=healthy_queues,
            ),
            patch(
                "mosk_mcp.tools.messaging_operations.diagnose_rabbitmq_issue.get_rabbitmq_connections",
                return_value=healthy_connections,
            ),
        ):
            result = await diagnose_rabbitmq_issue(
                mock_kubernetes_adapter,
                rabbitmq_instance="main",
            )

        # Memory critical should produce critical health level
        assert result.overall_health == RabbitMQHealthLevel.CRITICAL

    @pytest.mark.asyncio
    async def test_diagnose_queue_backlog(
        self,
        mock_kubernetes_adapter: AsyncMock,
        healthy_status: GetRabbitMQStatusOutput,
        healthy_connections: GetRabbitMQConnectionsOutput,
    ) -> None:
        """Test detecting queue backlog."""
        backlog_queues = ListRabbitMQQueuesOutput(
            instance="main",
            has_backlog=True,
            total_messages=5000,
            has_stale_queues=False,
            stale_queue_count=0,
        )

        with (
            patch(
                "mosk_mcp.tools.messaging_operations.diagnose_rabbitmq_issue.get_rabbitmq_status",
                return_value=healthy_status,
            ),
            patch(
                "mosk_mcp.tools.messaging_operations.diagnose_rabbitmq_issue.list_rabbitmq_queues",
                return_value=backlog_queues,
            ),
            patch(
                "mosk_mcp.tools.messaging_operations.diagnose_rabbitmq_issue.get_rabbitmq_connections",
                return_value=healthy_connections,
            ),
        ):
            result = await diagnose_rabbitmq_issue(
                mock_kubernetes_adapter,
                rabbitmq_instance="main",
            )

        all_issues = result.instances[0].issues_found
        assert any("backlog" in issue.lower() for issue in all_issues)

    @pytest.mark.asyncio
    async def test_diagnose_stale_queues(
        self,
        mock_kubernetes_adapter: AsyncMock,
        healthy_status: GetRabbitMQStatusOutput,
        healthy_connections: GetRabbitMQConnectionsOutput,
    ) -> None:
        """Test detecting stale queues."""
        stale_queues = ListRabbitMQQueuesOutput(
            instance="main",
            has_backlog=False,
            has_stale_queues=True,
            stale_queue_count=STALE_QUEUE_THRESHOLD + 5,
        )

        with (
            patch(
                "mosk_mcp.tools.messaging_operations.diagnose_rabbitmq_issue.get_rabbitmq_status",
                return_value=healthy_status,
            ),
            patch(
                "mosk_mcp.tools.messaging_operations.diagnose_rabbitmq_issue.list_rabbitmq_queues",
                return_value=stale_queues,
            ),
            patch(
                "mosk_mcp.tools.messaging_operations.diagnose_rabbitmq_issue.get_rabbitmq_connections",
                return_value=healthy_connections,
            ),
        ):
            result = await diagnose_rabbitmq_issue(
                mock_kubernetes_adapter,
                rabbitmq_instance="main",
            )

        all_issues = result.instances[0].issues_found
        assert any("stale" in issue.lower() for issue in all_issues)

    @pytest.mark.asyncio
    async def test_diagnose_blocked_connections(
        self,
        mock_kubernetes_adapter: AsyncMock,
        healthy_status: GetRabbitMQStatusOutput,
        healthy_queues: ListRabbitMQQueuesOutput,
    ) -> None:
        """Test detecting blocked connections."""
        blocked_connections = GetRabbitMQConnectionsOutput(
            instance="main",
            has_blocked_connections=True,
            blocked_connections=5,
            connection_utilization_percent=50.0,
        )

        with (
            patch(
                "mosk_mcp.tools.messaging_operations.diagnose_rabbitmq_issue.get_rabbitmq_status",
                return_value=healthy_status,
            ),
            patch(
                "mosk_mcp.tools.messaging_operations.diagnose_rabbitmq_issue.list_rabbitmq_queues",
                return_value=healthy_queues,
            ),
            patch(
                "mosk_mcp.tools.messaging_operations.diagnose_rabbitmq_issue.get_rabbitmq_connections",
                return_value=blocked_connections,
            ),
        ):
            result = await diagnose_rabbitmq_issue(
                mock_kubernetes_adapter,
                rabbitmq_instance="main",
            )

        all_issues = result.critical_issues + result.warnings + result.instances[0].issues_found
        assert any("blocked" in issue.lower() for issue in all_issues)

    @pytest.mark.asyncio
    async def test_diagnose_connection_pool_warning(
        self,
        mock_kubernetes_adapter: AsyncMock,
        healthy_status: GetRabbitMQStatusOutput,
        healthy_queues: ListRabbitMQQueuesOutput,
    ) -> None:
        """Test detecting elevated connection pool utilization."""
        elevated_connections = GetRabbitMQConnectionsOutput(
            instance="main",
            has_blocked_connections=False,
            connection_utilization_percent=CONNECTION_WARNING_THRESHOLD + 5,
        )

        with (
            patch(
                "mosk_mcp.tools.messaging_operations.diagnose_rabbitmq_issue.get_rabbitmq_status",
                return_value=healthy_status,
            ),
            patch(
                "mosk_mcp.tools.messaging_operations.diagnose_rabbitmq_issue.list_rabbitmq_queues",
                return_value=healthy_queues,
            ),
            patch(
                "mosk_mcp.tools.messaging_operations.diagnose_rabbitmq_issue.get_rabbitmq_connections",
                return_value=elevated_connections,
            ),
        ):
            result = await diagnose_rabbitmq_issue(
                mock_kubernetes_adapter,
                rabbitmq_instance="main",
            )

        all_issues = result.instances[0].issues_found
        assert any("connection pool" in issue.lower() for issue in all_issues)

    @pytest.mark.asyncio
    async def test_diagnose_connection_pool_critical(
        self,
        mock_kubernetes_adapter: AsyncMock,
        healthy_status: GetRabbitMQStatusOutput,
        healthy_queues: ListRabbitMQQueuesOutput,
    ) -> None:
        """Test detecting critical connection pool utilization."""
        critical_connections = GetRabbitMQConnectionsOutput(
            instance="main",
            has_blocked_connections=False,
            connection_utilization_percent=CONNECTION_CRITICAL_THRESHOLD + 5,
        )

        with (
            patch(
                "mosk_mcp.tools.messaging_operations.diagnose_rabbitmq_issue.get_rabbitmq_status",
                return_value=healthy_status,
            ),
            patch(
                "mosk_mcp.tools.messaging_operations.diagnose_rabbitmq_issue.list_rabbitmq_queues",
                return_value=healthy_queues,
            ),
            patch(
                "mosk_mcp.tools.messaging_operations.diagnose_rabbitmq_issue.get_rabbitmq_connections",
                return_value=critical_connections,
            ),
        ):
            result = await diagnose_rabbitmq_issue(
                mock_kubernetes_adapter,
                rabbitmq_instance="main",
            )

        all_issues = result.instances[0].issues_found
        assert any("exhaustion" in issue.lower() for issue in all_issues)

    @pytest.mark.asyncio
    async def test_diagnose_without_queue_analysis(
        self,
        mock_kubernetes_adapter: AsyncMock,
        healthy_status: GetRabbitMQStatusOutput,
        healthy_connections: GetRabbitMQConnectionsOutput,
    ) -> None:
        """Test diagnosing without queue analysis."""
        with (
            patch(
                "mosk_mcp.tools.messaging_operations.diagnose_rabbitmq_issue.get_rabbitmq_status",
                return_value=healthy_status,
            ),
            patch(
                "mosk_mcp.tools.messaging_operations.diagnose_rabbitmq_issue.list_rabbitmq_queues",
            ) as mock_queues,
            patch(
                "mosk_mcp.tools.messaging_operations.diagnose_rabbitmq_issue.get_rabbitmq_connections",
                return_value=healthy_connections,
            ),
        ):
            await diagnose_rabbitmq_issue(
                mock_kubernetes_adapter,
                rabbitmq_instance="main",
                include_queue_analysis=False,
            )

        mock_queues.assert_not_called()

    @pytest.mark.asyncio
    async def test_diagnose_without_connection_analysis(
        self,
        mock_kubernetes_adapter: AsyncMock,
        healthy_status: GetRabbitMQStatusOutput,
        healthy_queues: ListRabbitMQQueuesOutput,
    ) -> None:
        """Test diagnosing without connection analysis."""
        with (
            patch(
                "mosk_mcp.tools.messaging_operations.diagnose_rabbitmq_issue.get_rabbitmq_status",
                return_value=healthy_status,
            ),
            patch(
                "mosk_mcp.tools.messaging_operations.diagnose_rabbitmq_issue.list_rabbitmq_queues",
                return_value=healthy_queues,
            ),
            patch(
                "mosk_mcp.tools.messaging_operations.diagnose_rabbitmq_issue.get_rabbitmq_connections",
            ) as mock_conns,
        ):
            await diagnose_rabbitmq_issue(
                mock_kubernetes_adapter,
                rabbitmq_instance="main",
                include_connection_analysis=False,
            )

        mock_conns.assert_not_called()

    @pytest.mark.asyncio
    async def test_diagnose_status_error_handled(
        self,
        mock_kubernetes_adapter: AsyncMock,
        healthy_queues: ListRabbitMQQueuesOutput,
        healthy_connections: GetRabbitMQConnectionsOutput,
    ) -> None:
        """Test status error is handled gracefully."""
        with (
            patch(
                "mosk_mcp.tools.messaging_operations.diagnose_rabbitmq_issue.get_rabbitmq_status",
                side_effect=ToolExecutionError(
                    message="Failed to get status",
                    tool_name="get_rabbitmq_status",
                ),
            ),
            patch(
                "mosk_mcp.tools.messaging_operations.diagnose_rabbitmq_issue.list_rabbitmq_queues",
                return_value=healthy_queues,
            ),
            patch(
                "mosk_mcp.tools.messaging_operations.diagnose_rabbitmq_issue.get_rabbitmq_connections",
                return_value=healthy_connections,
            ),
        ):
            result = await diagnose_rabbitmq_issue(
                mock_kubernetes_adapter,
                rabbitmq_instance="main",
            )

        # Should complete but show error
        assert len(result.instances) == 1
        assert any("failed" in issue.lower() for issue in result.instances[0].issues_found)

    @pytest.mark.asyncio
    async def test_diagnose_queue_error_handled(
        self,
        mock_kubernetes_adapter: AsyncMock,
        healthy_status: GetRabbitMQStatusOutput,
        healthy_connections: GetRabbitMQConnectionsOutput,
    ) -> None:
        """Test queue analysis error is handled gracefully."""
        with (
            patch(
                "mosk_mcp.tools.messaging_operations.diagnose_rabbitmq_issue.get_rabbitmq_status",
                return_value=healthy_status,
            ),
            patch(
                "mosk_mcp.tools.messaging_operations.diagnose_rabbitmq_issue.list_rabbitmq_queues",
                side_effect=ToolExecutionError(
                    message="Queue query failed",
                    tool_name="list_rabbitmq_queues",
                ),
            ),
            patch(
                "mosk_mcp.tools.messaging_operations.diagnose_rabbitmq_issue.get_rabbitmq_connections",
                return_value=healthy_connections,
            ),
        ):
            result = await diagnose_rabbitmq_issue(
                mock_kubernetes_adapter,
                rabbitmq_instance="main",
            )

        # Should complete without raising
        assert len(result.instances) == 1

    @pytest.mark.asyncio
    async def test_diagnose_connection_error_handled(
        self,
        mock_kubernetes_adapter: AsyncMock,
        healthy_status: GetRabbitMQStatusOutput,
        healthy_queues: ListRabbitMQQueuesOutput,
    ) -> None:
        """Test connection analysis error is handled gracefully."""
        with (
            patch(
                "mosk_mcp.tools.messaging_operations.diagnose_rabbitmq_issue.get_rabbitmq_status",
                return_value=healthy_status,
            ),
            patch(
                "mosk_mcp.tools.messaging_operations.diagnose_rabbitmq_issue.list_rabbitmq_queues",
                return_value=healthy_queues,
            ),
            patch(
                "mosk_mcp.tools.messaging_operations.diagnose_rabbitmq_issue.get_rabbitmq_connections",
                side_effect=ToolExecutionError(
                    message="Connection query failed",
                    tool_name="get_rabbitmq_connections",
                ),
            ),
        ):
            result = await diagnose_rabbitmq_issue(
                mock_kubernetes_adapter,
                rabbitmq_instance="main",
            )

        # Should complete without raising
        assert len(result.instances) == 1

    @pytest.mark.asyncio
    async def test_diagnose_instance_failure_handled(
        self,
        mock_kubernetes_adapter: AsyncMock,
    ) -> None:
        """Test instance diagnosis failure is handled gracefully."""
        with (
            patch(
                "mosk_mcp.tools.messaging_operations.diagnose_rabbitmq_issue.get_rabbitmq_status",
                side_effect=ToolExecutionError(
                    message="Instance unreachable",
                    tool_name="get_rabbitmq_status",
                ),
            ),
            patch(
                "mosk_mcp.tools.messaging_operations.diagnose_rabbitmq_issue.list_rabbitmq_queues",
                side_effect=ToolExecutionError(
                    message="Instance unreachable",
                    tool_name="list_rabbitmq_queues",
                ),
            ),
            patch(
                "mosk_mcp.tools.messaging_operations.diagnose_rabbitmq_issue.get_rabbitmq_connections",
                side_effect=ToolExecutionError(
                    message="Instance unreachable",
                    tool_name="get_rabbitmq_connections",
                ),
            ),
        ):
            result = await diagnose_rabbitmq_issue(
                mock_kubernetes_adapter,
                rabbitmq_instance="all",
            )

        # Should return diagnosis for both instances even with failures
        assert len(result.instances) == 2

    @pytest.mark.asyncio
    async def test_diagnose_check_statistics(
        self,
        mock_kubernetes_adapter: AsyncMock,
        healthy_status: GetRabbitMQStatusOutput,
        healthy_queues: ListRabbitMQQueuesOutput,
        healthy_connections: GetRabbitMQConnectionsOutput,
    ) -> None:
        """Test that check statistics are calculated correctly."""
        with (
            patch(
                "mosk_mcp.tools.messaging_operations.diagnose_rabbitmq_issue.get_rabbitmq_status",
                return_value=healthy_status,
            ),
            patch(
                "mosk_mcp.tools.messaging_operations.diagnose_rabbitmq_issue.list_rabbitmq_queues",
                return_value=healthy_queues,
            ),
            patch(
                "mosk_mcp.tools.messaging_operations.diagnose_rabbitmq_issue.get_rabbitmq_connections",
                return_value=healthy_connections,
            ),
        ):
            result = await diagnose_rabbitmq_issue(
                mock_kubernetes_adapter,
                rabbitmq_instance="main",
            )

        # Should have check statistics
        assert result.total_checks > 0
        assert result.checks_passed >= 0
        assert result.checks_warned >= 0
        assert result.checks_failed >= 0
        assert (
            result.total_checks
            == result.checks_passed + result.checks_warned + result.checks_failed
        )
