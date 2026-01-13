"""Unit tests for common tools utilities.

Tests for enums, error handling, and scoring utilities.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from mosk_mcp.core.exceptions import KubernetesError, ToolExecutionError


# ==========================
# Enum Tests
# ==========================
class TestAlertSeverity:
    """Tests for AlertSeverity enum."""

    def test_enum_values(self) -> None:
        """Test AlertSeverity enum values."""
        from mosk_mcp.tools.common.enums import AlertSeverity

        assert AlertSeverity.CRITICAL.value == "critical"
        assert AlertSeverity.WARNING.value == "warning"
        assert AlertSeverity.INFO.value == "info"
        assert AlertSeverity.NONE.value == "none"
        assert AlertSeverity.PAGE.value == "page"

    def test_comparison_operators(self) -> None:
        """Test AlertSeverity comparison operators."""
        from mosk_mcp.tools.common.enums import AlertSeverity

        assert AlertSeverity.PAGE > AlertSeverity.CRITICAL
        assert AlertSeverity.CRITICAL > AlertSeverity.WARNING
        assert AlertSeverity.WARNING > AlertSeverity.INFO
        assert AlertSeverity.INFO > AlertSeverity.NONE

    def test_comparison_equals(self) -> None:
        """Test AlertSeverity equality comparisons."""
        from mosk_mcp.tools.common.enums import AlertSeverity

        assert AlertSeverity.CRITICAL >= AlertSeverity.CRITICAL
        assert AlertSeverity.CRITICAL <= AlertSeverity.CRITICAL
        assert not (AlertSeverity.CRITICAL < AlertSeverity.CRITICAL)
        assert not (AlertSeverity.CRITICAL > AlertSeverity.CRITICAL)

    def test_is_at_least(self) -> None:
        """Test is_at_least method."""
        from mosk_mcp.tools.common.enums import AlertSeverity

        assert AlertSeverity.CRITICAL.is_at_least(AlertSeverity.WARNING)
        assert AlertSeverity.CRITICAL.is_at_least(AlertSeverity.CRITICAL)
        assert not AlertSeverity.WARNING.is_at_least(AlertSeverity.CRITICAL)

    def test_is_more_severe_than(self) -> None:
        """Test is_more_severe_than method."""
        from mosk_mcp.tools.common.enums import AlertSeverity

        assert AlertSeverity.CRITICAL.is_more_severe_than(AlertSeverity.WARNING)
        assert not AlertSeverity.CRITICAL.is_more_severe_than(AlertSeverity.CRITICAL)
        assert not AlertSeverity.WARNING.is_more_severe_than(AlertSeverity.CRITICAL)

    def test_comparison_with_different_type(self) -> None:
        """Test comparison with non-enum type returns NotImplemented."""
        from mosk_mcp.tools.common.enums import AlertSeverity

        result = AlertSeverity.CRITICAL.__lt__("string")
        assert result is NotImplemented


class TestAlertState:
    """Tests for AlertState enum."""

    def test_enum_values(self) -> None:
        """Test AlertState enum values."""
        from mosk_mcp.tools.common.enums import AlertState

        assert AlertState.FIRING.value == "firing"
        assert AlertState.PENDING.value == "pending"
        assert AlertState.RESOLVED.value == "resolved"


class TestHealthStatus:
    """Tests for HealthStatus enum."""

    def test_enum_values(self) -> None:
        """Test HealthStatus enum values."""
        from mosk_mcp.tools.common.enums import HealthStatus

        assert HealthStatus.HEALTHY.value == "healthy"
        assert HealthStatus.DEGRADED.value == "degraded"
        assert HealthStatus.UNHEALTHY.value == "unhealthy"
        assert HealthStatus.UNKNOWN.value == "unknown"


class TestHealthState:
    """Tests for HealthState enum."""

    def test_enum_values(self) -> None:
        """Test HealthState enum values."""
        from mosk_mcp.tools.common.enums import HealthState

        assert HealthState.HEALTHY.value == "healthy"
        assert HealthState.DEGRADED.value == "degraded"
        assert HealthState.WARNING.value == "warning"
        assert HealthState.CRITICAL.value == "critical"
        assert HealthState.UNKNOWN.value == "unknown"

    def test_comparison_operators(self) -> None:
        """Test HealthState comparison operators."""
        from mosk_mcp.tools.common.enums import HealthState

        assert HealthState.CRITICAL > HealthState.WARNING
        assert HealthState.WARNING > HealthState.DEGRADED
        assert HealthState.DEGRADED > HealthState.HEALTHY
        assert HealthState.HEALTHY > HealthState.UNKNOWN


class TestCapacityStatus:
    """Tests for CapacityStatus enum."""

    def test_enum_values(self) -> None:
        """Test CapacityStatus enum values."""
        from mosk_mcp.tools.common.enums import CapacityStatus

        assert CapacityStatus.NORMAL.value == "normal"
        assert CapacityStatus.WARNING.value == "warning"
        assert CapacityStatus.CRITICAL.value == "critical"
        assert CapacityStatus.EMERGENCY.value == "emergency"

    def test_comparison_operators(self) -> None:
        """Test CapacityStatus comparison operators."""
        from mosk_mcp.tools.common.enums import CapacityStatus

        assert CapacityStatus.EMERGENCY > CapacityStatus.CRITICAL
        assert CapacityStatus.CRITICAL > CapacityStatus.WARNING
        assert CapacityStatus.WARNING > CapacityStatus.NORMAL


class TestOperationPhase:
    """Tests for OperationPhase enum."""

    def test_enum_values(self) -> None:
        """Test OperationPhase enum values."""
        from mosk_mcp.tools.common.enums import OperationPhase

        assert OperationPhase.PENDING.value == "pending"
        assert OperationPhase.IN_PROGRESS.value == "in_progress"
        assert OperationPhase.COMPLETED.value == "completed"
        assert OperationPhase.FAILED.value == "failed"
        assert OperationPhase.CANCELLED.value == "cancelled"


class TestValidationStatus:
    """Tests for ValidationStatus enum."""

    def test_enum_values(self) -> None:
        """Test ValidationStatus enum values."""
        from mosk_mcp.tools.common.enums import ValidationStatus

        assert ValidationStatus.PASSED.value == "passed"
        assert ValidationStatus.PASSED_WITH_WARNINGS.value == "passed_with_warnings"
        assert ValidationStatus.FAILED.value == "failed"
        assert ValidationStatus.ERROR.value == "error"


class TestValidationLevel:
    """Tests for ValidationLevel enum."""

    def test_enum_values(self) -> None:
        """Test ValidationLevel enum values."""
        from mosk_mcp.tools.common.enums import ValidationLevel

        assert ValidationLevel.QUICK.value == "quick"
        assert ValidationLevel.STANDARD.value == "standard"
        assert ValidationLevel.COMPREHENSIVE.value == "comprehensive"


class TestLogSeverity:
    """Tests for LogSeverity enum."""

    def test_enum_values(self) -> None:
        """Test LogSeverity enum values."""
        from mosk_mcp.tools.common.enums import LogSeverity

        assert LogSeverity.DEBUG.value == "debug"
        assert LogSeverity.INFO.value == "info"
        assert LogSeverity.WARNING.value == "warning"
        assert LogSeverity.ERROR.value == "error"
        assert LogSeverity.CRITICAL.value == "critical"
        assert LogSeverity.UNKNOWN.value == "unknown"


class TestMigrationStatus:
    """Tests for MigrationStatus enum."""

    def test_enum_values(self) -> None:
        """Test MigrationStatus enum values."""
        from mosk_mcp.tools.common.enums import MigrationStatus

        assert MigrationStatus.QUEUED.value == "queued"
        assert MigrationStatus.PREPARING.value == "preparing"
        assert MigrationStatus.RUNNING.value == "running"
        assert MigrationStatus.COMPLETED.value == "completed"
        assert MigrationStatus.FAILED.value == "failed"
        assert MigrationStatus.ERROR.value == "error"


class TestCephHealthStatus:
    """Tests for CephHealthStatus enum."""

    def test_enum_values(self) -> None:
        """Test CephHealthStatus enum values."""
        from mosk_mcp.tools.common.enums import CephHealthStatus

        assert CephHealthStatus.HEALTH_OK.value == "HEALTH_OK"
        assert CephHealthStatus.HEALTH_WARN.value == "HEALTH_WARN"
        assert CephHealthStatus.HEALTH_ERR.value == "HEALTH_ERR"
        assert CephHealthStatus.UNKNOWN.value == "UNKNOWN"


class TestDeviceFlowStatus:
    """Tests for DeviceFlowStatus enum."""

    def test_enum_values(self) -> None:
        """Test DeviceFlowStatus enum values."""
        from mosk_mcp.tools.common.enums import DeviceFlowStatus

        assert DeviceFlowStatus.PENDING.value == "pending"
        assert DeviceFlowStatus.AWAITING_USER.value == "awaiting_user"
        assert DeviceFlowStatus.POLLING.value == "polling"
        assert DeviceFlowStatus.COMPLETED.value == "completed"
        assert DeviceFlowStatus.EXPIRED.value == "expired"
        assert DeviceFlowStatus.DENIED.value == "denied"
        assert DeviceFlowStatus.ERROR.value == "error"


# ==========================
# Error Handling Tests
# ==========================
class TestHandleException:
    """Tests for _handle_exception function."""

    def test_reraises_tool_execution_error(self) -> None:
        """Test that ToolExecutionError is re-raised as-is."""
        from mosk_mcp.tools.common.errors import _handle_exception

        original_error = ToolExecutionError(
            message="Original error",
            tool_name="test_tool",
            details={},
        )

        with pytest.raises(ToolExecutionError) as exc_info:
            _handle_exception(
                original_error,
                "test_tool",
                wrap_kubernetes_errors=True,
                log_errors=False,
                exc_logger=MagicMock(),
            )

        assert exc_info.value is original_error

    def test_reraises_kubernetes_error_when_wrapping(self) -> None:
        """Test that KubernetesError is re-raised when wrap_kubernetes_errors=True."""
        from mosk_mcp.tools.common.errors import _handle_exception

        original_error = KubernetesError(
            message="K8s error",
            operation="get",
            resource_kind="Pod",
        )

        with pytest.raises(KubernetesError) as exc_info:
            _handle_exception(
                original_error,
                "test_tool",
                wrap_kubernetes_errors=True,
                log_errors=False,
                exc_logger=MagicMock(),
            )

        assert exc_info.value is original_error

    def test_wraps_kubernetes_error_when_not_wrapping(self) -> None:
        """Test that KubernetesError is wrapped when wrap_kubernetes_errors=False."""
        from mosk_mcp.tools.common.errors import _handle_exception

        original_error = KubernetesError(
            message="K8s error",
            operation="get",
            resource_kind="Pod",
        )
        mock_logger = MagicMock()

        with pytest.raises(ToolExecutionError) as exc_info:
            _handle_exception(
                original_error,
                "test_tool",
                wrap_kubernetes_errors=False,
                log_errors=True,
                exc_logger=mock_logger,
            )

        assert "test_tool" in str(exc_info.value)
        mock_logger.error.assert_called_once()

    def test_wraps_general_exception(self) -> None:
        """Test that general exceptions are wrapped in ToolExecutionError."""
        from mosk_mcp.tools.common.errors import _handle_exception

        original_error = ValueError("Something went wrong")
        mock_logger = MagicMock()

        with pytest.raises(ToolExecutionError) as exc_info:
            _handle_exception(
                original_error,
                "test_tool",
                wrap_kubernetes_errors=True,
                log_errors=True,
                exc_logger=mock_logger,
            )

        assert "test_tool" in str(exc_info.value)
        assert "ValueError" in str(exc_info.value.details)


class TestWrapKubernetesError:
    """Tests for wrap_kubernetes_error function."""

    def test_wrap_with_all_params(self) -> None:
        """Test wrapping with all parameters."""
        from mosk_mcp.tools.common.errors import wrap_kubernetes_error

        original = RuntimeError("Connection failed")
        wrapped = wrap_kubernetes_error(
            original,
            operation="list",
            resource_kind="Machine",
            namespace="default",
            resource_name="machine-01",
        )

        assert isinstance(wrapped, KubernetesError)
        assert wrapped.operation == "list"
        assert wrapped.resource_kind == "Machine"
        assert wrapped.namespace == "default"
        assert wrapped.resource_name == "machine-01"
        assert "Connection failed" in str(wrapped)

    def test_wrap_minimal_params(self) -> None:
        """Test wrapping with minimal parameters."""
        from mosk_mcp.tools.common.errors import wrap_kubernetes_error

        original = RuntimeError("Error")
        wrapped = wrap_kubernetes_error(original, "get", "Pod")

        assert isinstance(wrapped, KubernetesError)
        assert wrapped.operation == "get"
        assert wrapped.resource_kind == "Pod"
        assert wrapped.namespace is None
        assert wrapped.resource_name is None


class TestToolHandler:
    """Tests for tool_handler decorator."""

    @pytest.mark.asyncio
    async def test_async_function_success(self) -> None:
        """Test decorator with successful async function."""
        from mosk_mcp.tools.common.errors import tool_handler

        @tool_handler("test_tool", log_start=False, log_complete=False)
        async def my_tool() -> str:
            return "success"

        result = await my_tool()
        assert result == "success"

    @pytest.mark.asyncio
    async def test_async_function_error(self) -> None:
        """Test decorator wraps errors in async function."""
        from mosk_mcp.tools.common.errors import tool_handler

        @tool_handler("test_tool", log_errors=False)
        async def my_tool() -> str:
            raise ValueError("Test error")

        with pytest.raises(ToolExecutionError) as exc_info:
            await my_tool()

        assert "test_tool" in str(exc_info.value)

    def test_sync_function_success(self) -> None:
        """Test decorator with successful sync function."""
        from mosk_mcp.tools.common.errors import tool_handler

        @tool_handler("test_tool", log_start=False, log_complete=False)
        def my_tool() -> str:
            return "success"

        result = my_tool()
        assert result == "success"

    def test_sync_function_error(self) -> None:
        """Test decorator wraps errors in sync function."""
        from mosk_mcp.tools.common.errors import tool_handler

        @tool_handler("test_tool", log_errors=False)
        def my_tool() -> str:
            raise ValueError("Test error")

        with pytest.raises(ToolExecutionError) as exc_info:
            my_tool()

        assert "test_tool" in str(exc_info.value)


# ==========================
# Scoring Tests
# ==========================
class TestScoreComponent:
    """Tests for ScoreComponent dataclass."""

    def test_create_component(self) -> None:
        """Test creating a ScoreComponent."""
        from mosk_mcp.tools.common.scoring import ScoreComponent

        component = ScoreComponent(
            name="test",
            weight=30,
            score=25.5,
            details={"info": "test"},
        )

        assert component.name == "test"
        assert component.weight == 30
        assert component.score == 25.5
        assert component.details == {"info": "test"}

    def test_normalized_score_within_bounds(self) -> None:
        """Test normalized_score when within bounds."""
        from mosk_mcp.tools.common.scoring import ScoreComponent

        component = ScoreComponent(name="test", weight=30, score=20)
        assert component.normalized_score == 20

    def test_normalized_score_clamped_high(self) -> None:
        """Test normalized_score when above weight."""
        from mosk_mcp.tools.common.scoring import ScoreComponent

        component = ScoreComponent(name="test", weight=30, score=50)
        assert component.normalized_score == 30

    def test_normalized_score_clamped_low(self) -> None:
        """Test normalized_score when below zero."""
        from mosk_mcp.tools.common.scoring import ScoreComponent

        component = ScoreComponent(name="test", weight=30, score=-10)
        assert component.normalized_score == 0


class TestScoreCalculator:
    """Tests for ScoreCalculator class."""

    def test_add_component(self) -> None:
        """Test adding a component."""
        from mosk_mcp.tools.common.scoring import ScoreCalculator

        calc = ScoreCalculator()
        result = calc.add_component("test", 30, 25)

        assert result is calc  # Fluent interface
        assert len(calc.components) == 1
        assert calc.components[0].name == "test"

    def test_calculate_simple(self) -> None:
        """Test simple score calculation."""
        from mosk_mcp.tools.common.scoring import ScoreCalculator

        score = ScoreCalculator().add_component("a", 50, 50).add_component("b", 50, 25).calculate()

        assert score == 75

    def test_calculate_clamped_to_100(self) -> None:
        """Test score is clamped to 100."""
        from mosk_mcp.tools.common.scoring import ScoreCalculator

        score = ScoreCalculator().add_component("a", 60, 60).add_component("b", 60, 60).calculate()

        assert score == 100

    def test_add_ratio_component(self) -> None:
        """Test add_ratio_component method."""
        from mosk_mcp.tools.common.scoring import ScoreCalculator

        calc = ScoreCalculator()
        calc.add_ratio_component("health", 50, numerator=8, denominator=10)

        assert len(calc.components) == 1
        assert calc.components[0].score == 40  # 8/10 * 50

    def test_add_ratio_component_zero_denominator(self) -> None:
        """Test add_ratio_component with zero denominator."""
        from mosk_mcp.tools.common.scoring import ScoreCalculator

        calc = ScoreCalculator()
        calc.add_ratio_component("health", 50, numerator=0, denominator=0)

        assert calc.components[0].score == 50  # Full score

    def test_add_threshold_component(self) -> None:
        """Test add_threshold_component method."""
        from mosk_mcp.tools.common.scoring import ScoreCalculator

        calc = ScoreCalculator()
        thresholds = [(70, 1.0), (85, 0.75), (95, 0.4), (100, 0.0)]
        calc.add_threshold_component("capacity", 20, value=60, thresholds=thresholds)

        # 60 < 70, so score = 1.0 * 20 = 20
        assert calc.components[0].score == 20

    def test_add_threshold_component_middle(self) -> None:
        """Test add_threshold_component hitting middle threshold."""
        from mosk_mcp.tools.common.scoring import ScoreCalculator

        calc = ScoreCalculator()
        thresholds = [(70, 1.0), (85, 0.75), (95, 0.4), (100, 0.0)]
        calc.add_threshold_component("capacity", 20, value=80, thresholds=thresholds)

        # 70 <= 80 < 85, so score = 0.75 * 20 = 15
        assert calc.components[0].score == 15

    def test_get_breakdown(self) -> None:
        """Test get_breakdown method."""
        from mosk_mcp.tools.common.scoring import ScoreCalculator

        calc = ScoreCalculator().add_component("a", 50, 40).add_component("b", 50, 30)

        breakdown = calc.get_breakdown()

        assert breakdown["total_weight"] == 100
        assert breakdown["total_score"] == 70
        assert len(breakdown["components"]) == 2


class TestScoreFunctions:
    """Tests for standalone scoring functions."""

    def test_calculate_ratio_score(self) -> None:
        """Test calculate_ratio_score function."""
        from mosk_mcp.tools.common.scoring import calculate_ratio_score

        assert calculate_ratio_score(8, 10, 100) == 80
        assert calculate_ratio_score(10, 10, 100) == 100
        assert calculate_ratio_score(0, 10, 100) == 0

    def test_calculate_ratio_score_zero_denominator(self) -> None:
        """Test calculate_ratio_score with zero denominator."""
        from mosk_mcp.tools.common.scoring import calculate_ratio_score

        assert calculate_ratio_score(0, 0, 100) == 100  # Full score

    def test_calculate_threshold_score(self) -> None:
        """Test calculate_threshold_score function."""
        from mosk_mcp.tools.common.scoring import calculate_threshold_score

        thresholds = [(70, 100), (85, 75), (95, 40)]

        assert calculate_threshold_score(50, thresholds) == 100  # 50 < 70
        assert calculate_threshold_score(80, thresholds) == 75  # 70 <= 80 < 85
        assert calculate_threshold_score(90, thresholds) == 40  # 85 <= 90 < 95

    def test_calculate_threshold_score_default(self) -> None:
        """Test calculate_threshold_score with no matching threshold."""
        from mosk_mcp.tools.common.scoring import calculate_threshold_score

        thresholds = [(70, 100), (85, 75)]

        assert calculate_threshold_score(90, thresholds, default_score=10) == 10
