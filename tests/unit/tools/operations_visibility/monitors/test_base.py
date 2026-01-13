"""Unit tests for monitor base classes."""

from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from mosk_mcp.tools.operations_visibility.monitors.base import (
    BaseOperationMonitor,
    ProgressSnapshot,
)


class TestProgressSnapshot:
    """Tests for ProgressSnapshot model."""

    def test_required_fields(self):
        """Test required fields."""
        snapshot = ProgressSnapshot(
            timestamp="2025-01-01T00:00:00Z",
            progress_percent=50,
            phase="running",
            message="Test message",
        )

        assert snapshot.timestamp == "2025-01-01T00:00:00Z"
        assert snapshot.progress_percent == 50
        assert snapshot.phase == "running"
        assert snapshot.message == "Test message"
        assert snapshot.details == {}

    def test_with_details(self):
        """Test with details provided."""
        snapshot = ProgressSnapshot(
            timestamp="2025-01-01T00:00:00Z",
            progress_percent=75,
            phase="finalizing",
            message="Almost done",
            details={"key": "value", "count": 5},
        )

        assert snapshot.details == {"key": "value", "count": 5}

    def test_create_factory_method(self):
        """Test create() factory method."""
        snapshot = ProgressSnapshot.create(
            progress_percent=25,
            phase="starting",
            message="Operation started",
        )

        assert snapshot.progress_percent == 25
        assert snapshot.phase == "starting"
        assert snapshot.message == "Operation started"
        assert snapshot.details == {}
        # Verify timestamp is valid ISO format
        datetime.fromisoformat(snapshot.timestamp.replace("Z", "+00:00"))

    def test_create_with_details(self):
        """Test create() factory method with details."""
        snapshot = ProgressSnapshot.create(
            progress_percent=100,
            phase="complete",
            message="Done!",
            details={"items_processed": 10},
        )

        assert snapshot.progress_percent == 100
        assert snapshot.details == {"items_processed": 10}

    def test_progress_percent_bounds(self):
        """Test progress_percent validation bounds."""
        # Valid: -1 for error state
        snapshot = ProgressSnapshot(
            timestamp="2025-01-01T00:00:00Z",
            progress_percent=-1,
            phase="error",
            message="Failed",
        )
        assert snapshot.progress_percent == -1

        # Test lower bound (0 is valid)
        snapshot = ProgressSnapshot(
            timestamp="2025-01-01T00:00:00Z",
            progress_percent=0,
            phase="starting",
            message="Starting",
        )
        assert snapshot.progress_percent == 0

        # Test upper bound (100 is valid)
        snapshot = ProgressSnapshot(
            timestamp="2025-01-01T00:00:00Z",
            progress_percent=100,
            phase="complete",
            message="Done",
        )
        assert snapshot.progress_percent == 100

        # Test below minimum bound (-2 should fail)
        with pytest.raises(ValueError):
            ProgressSnapshot(
                timestamp="2025-01-01T00:00:00Z",
                progress_percent=-2,
                phase="error",
                message="Failed",
            )

        # Invalid: above 100
        with pytest.raises(ValueError):
            ProgressSnapshot(
                timestamp="2025-01-01T00:00:00Z",
                progress_percent=101,
                phase="complete",
                message="Done",
            )


class ConcreteMonitor(BaseOperationMonitor):
    """Concrete implementation of BaseOperationMonitor for testing."""

    def __init__(self, adapter, target, namespace):
        """Initialize the concrete monitor."""
        super().__init__(adapter, target, namespace)
        self._complete = False
        self._failed = False
        self._error_message: str | None = None
        self._progress_percent = 0
        self._phase = "not_started"
        self._message = "Not started"

    def set_complete(self) -> None:
        """Set operation as complete."""
        self._complete = True
        self._progress_percent = 100
        self._phase = "complete"
        self._message = "Operation completed"

    def set_failed(self, message: str) -> None:
        """Set operation as failed."""
        self._failed = True
        self._error_message = message
        self._progress_percent = -1
        self._phase = "error"
        self._message = message

    def set_progress(self, percent: int, phase: str, message: str) -> None:
        """Set progress state."""
        self._progress_percent = percent
        self._phase = phase
        self._message = message

    async def get_progress(self) -> ProgressSnapshot:
        """Get current progress snapshot."""
        return ProgressSnapshot.create(
            progress_percent=self._progress_percent,
            phase=self._phase,
            message=self._message,
            details={"target": self.target, "namespace": self.namespace},
        )

    def is_complete(self) -> bool:
        """Check if operation is complete."""
        return self._complete

    def has_failed(self) -> bool:
        """Check if operation has failed."""
        return self._failed

    def get_error_message(self) -> str | None:
        """Get error message if operation failed."""
        return self._error_message


class TestBaseOperationMonitor:
    """Tests for BaseOperationMonitor class."""

    @pytest.fixture
    def mock_adapter(self):
        """Create mock adapter."""
        return AsyncMock()

    @pytest.fixture
    def monitor(self, mock_adapter):
        """Create concrete monitor instance."""
        return ConcreteMonitor(mock_adapter, "test-target", "test-namespace")

    def test_initialization(self, monitor):
        """Test monitor initialization."""
        assert monitor.target == "test-target"
        assert monitor.namespace == "test-namespace"
        assert monitor.started_at is None
        assert monitor.completed_at is None
        assert monitor.last_snapshot is None

    def test_started_at_property(self, monitor):
        """Test started_at property getter."""
        assert monitor.started_at is None
        monitor.mark_started()
        assert monitor.started_at is not None

    def test_completed_at_property(self, monitor):
        """Test completed_at property getter."""
        assert monitor.completed_at is None
        monitor.mark_completed()
        assert monitor.completed_at is not None

    def test_last_snapshot_property(self, monitor):
        """Test last_snapshot property getter."""
        assert monitor.last_snapshot is None
        # Manually set _last_snapshot
        snapshot = ProgressSnapshot.create(50, "running", "Test")
        monitor._last_snapshot = snapshot
        assert monitor.last_snapshot == snapshot

    def test_mark_started(self, monitor):
        """Test mark_started() method."""
        assert monitor._started_at is None
        monitor.mark_started()
        assert monitor._started_at is not None
        # Verify valid ISO format
        datetime.fromisoformat(monitor._started_at.replace("Z", "+00:00"))

    def test_mark_started_only_once(self, monitor):
        """Test that mark_started() only sets timestamp once."""
        monitor.mark_started()
        first_timestamp = monitor._started_at
        monitor.mark_started()
        assert monitor._started_at == first_timestamp

    def test_mark_completed(self, monitor):
        """Test mark_completed() method."""
        assert monitor._completed_at is None
        monitor.mark_completed()
        assert monitor._completed_at is not None
        # Verify valid ISO format
        datetime.fromisoformat(monitor._completed_at.replace("Z", "+00:00"))

    def test_mark_completed_only_once(self, monitor):
        """Test that mark_completed() only sets timestamp once."""
        monitor.mark_completed()
        first_timestamp = monitor._completed_at
        monitor.mark_completed()
        assert monitor._completed_at == first_timestamp

    @pytest.mark.asyncio
    async def test_poll_marks_started(self, monitor):
        """Test that poll() marks the monitor as started."""
        assert monitor._started_at is None
        await monitor.poll()
        assert monitor._started_at is not None

    @pytest.mark.asyncio
    async def test_poll_updates_last_snapshot(self, monitor):
        """Test that poll() updates the last snapshot."""
        monitor.set_progress(50, "running", "In progress")
        snapshot = await monitor.poll()

        assert monitor.last_snapshot == snapshot
        assert snapshot.progress_percent == 50
        assert snapshot.phase == "running"
        assert snapshot.message == "In progress"

    @pytest.mark.asyncio
    async def test_poll_marks_completed_on_success(self, monitor):
        """Test that poll() marks completed when operation is complete."""
        monitor.set_complete()
        assert monitor._completed_at is None

        await monitor.poll()

        assert monitor._completed_at is not None
        assert monitor.is_complete() is True

    @pytest.mark.asyncio
    async def test_poll_marks_completed_on_failure(self, monitor):
        """Test that poll() marks completed when operation has failed."""
        monitor.set_failed("Something went wrong")
        assert monitor._completed_at is None

        await monitor.poll()

        assert monitor._completed_at is not None
        assert monitor.has_failed() is True
        assert monitor.get_error_message() == "Something went wrong"

    @pytest.mark.asyncio
    async def test_poll_returns_progress_snapshot(self, monitor):
        """Test that poll() returns a ProgressSnapshot."""
        monitor.set_progress(75, "finalizing", "Almost done")

        snapshot = await monitor.poll()

        assert isinstance(snapshot, ProgressSnapshot)
        assert snapshot.progress_percent == 75
        assert snapshot.phase == "finalizing"
        assert snapshot.message == "Almost done"

    @pytest.mark.asyncio
    async def test_poll_includes_details(self, monitor):
        """Test that poll() includes details from get_progress()."""
        snapshot = await monitor.poll()

        assert snapshot.details == {
            "target": "test-target",
            "namespace": "test-namespace",
        }

    @pytest.mark.asyncio
    async def test_multiple_polls(self, monitor):
        """Test multiple poll() calls update state correctly."""
        # First poll - 25%
        monitor.set_progress(25, "starting", "Step 1")
        snapshot1 = await monitor.poll()
        assert snapshot1.progress_percent == 25

        # Second poll - 50%
        monitor.set_progress(50, "running", "Step 2")
        snapshot2 = await monitor.poll()
        assert snapshot2.progress_percent == 50
        assert monitor.last_snapshot == snapshot2

        # Third poll - complete
        monitor.set_complete()
        snapshot3 = await monitor.poll()
        assert snapshot3.progress_percent == 100
        assert monitor.is_complete() is True
        assert monitor._completed_at is not None

    def test_concrete_monitor_is_complete(self, monitor):
        """Test concrete monitor is_complete() method."""
        assert monitor.is_complete() is False
        monitor.set_complete()
        assert monitor.is_complete() is True

    def test_concrete_monitor_has_failed(self, monitor):
        """Test concrete monitor has_failed() method."""
        assert monitor.has_failed() is False
        monitor.set_failed("Error")
        assert monitor.has_failed() is True

    def test_concrete_monitor_get_error_message(self, monitor):
        """Test concrete monitor get_error_message() method."""
        assert monitor.get_error_message() is None
        monitor.set_failed("Test error message")
        assert monitor.get_error_message() == "Test error message"
