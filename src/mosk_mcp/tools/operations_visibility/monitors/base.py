"""Base class for operation monitors.

This module provides the abstract base class that all operation monitors
must implement. Each monitor tracks progress of a specific operation type.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field


if TYPE_CHECKING:
    from mosk_mcp.adapters.kubernetes import KubernetesAdapter


class ProgressSnapshot(BaseModel):
    """A point-in-time snapshot of operation progress.

    Attributes:
        timestamp: ISO format timestamp when snapshot was taken.
        progress_percent: Overall progress percentage (0-100, -1 for error).
        phase: Current phase name of the operation.
        message: Human-readable status message.
        details: Operation-specific details dictionary.
    """

    timestamp: str = Field(..., description="ISO format timestamp")
    progress_percent: int = Field(..., description="Progress percentage", ge=-1, le=100)
    phase: str = Field(..., description="Current phase name")
    message: str = Field(..., description="Human-readable status message")
    details: dict[str, Any] = Field(default_factory=dict, description="Operation-specific details")

    @classmethod
    def create(
        cls,
        progress_percent: int,
        phase: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> ProgressSnapshot:
        """Factory method to create a snapshot with current timestamp.

        Args:
            progress_percent: Progress percentage (0-100).
            phase: Current phase name.
            message: Human-readable status.
            details: Optional operation-specific details.

        Returns:
            New ProgressSnapshot instance.
        """
        return cls(
            timestamp=datetime.now(UTC).isoformat(),
            progress_percent=progress_percent,
            phase=phase,
            message=message,
            details=details or {},
        )


class BaseOperationMonitor(ABC):
    """Abstract base class for operation monitors.

    Each monitor tracks progress of a specific operation type (node add,
    upgrade, etc.) and provides a consistent interface for the polling loop.

    Attributes:
        target: The resource name being monitored.
        namespace: The Kubernetes namespace.
        _last_snapshot: Most recent progress snapshot.
        _started_at: When monitoring started.
        _completed_at: When operation completed (if done).
    """

    def __init__(
        self,
        adapter: KubernetesAdapter,
        target: str,
        namespace: str,
    ) -> None:
        """Initialize the monitor.

        Args:
            adapter: Kubernetes adapter for API calls.
            target: Resource name to monitor.
            namespace: Kubernetes namespace.
        """
        self.adapter = adapter
        self.target = target
        self.namespace = namespace
        self._last_snapshot: ProgressSnapshot | None = None
        self._started_at: str | None = None
        self._completed_at: str | None = None

    @property
    def started_at(self) -> str | None:
        """When monitoring started."""
        return self._started_at

    @property
    def completed_at(self) -> str | None:
        """When operation completed."""
        return self._completed_at

    @property
    def last_snapshot(self) -> ProgressSnapshot | None:
        """Most recent progress snapshot."""
        return self._last_snapshot

    def mark_started(self) -> None:
        """Mark the monitoring as started."""
        if self._started_at is None:
            self._started_at = datetime.now(UTC).isoformat()

    def mark_completed(self) -> None:
        """Mark the operation as completed."""
        if self._completed_at is None:
            self._completed_at = datetime.now(UTC).isoformat()

    @abstractmethod
    async def get_progress(self) -> ProgressSnapshot:
        """Get current progress snapshot.

        This method should query the cluster and return the current
        state of the operation as a ProgressSnapshot.

        Returns:
            Current progress snapshot.
        """
        pass

    @abstractmethod
    def is_complete(self) -> bool:
        """Check if operation is complete.

        Returns:
            True if operation has completed successfully.
        """
        pass

    @abstractmethod
    def has_failed(self) -> bool:
        """Check if operation has failed.

        Returns:
            True if operation has failed.
        """
        pass

    @abstractmethod
    def get_error_message(self) -> str | None:
        """Get error message if operation failed.

        Returns:
            Error message string, or None if no error.
        """
        pass

    async def poll(self) -> ProgressSnapshot:
        """Poll for current progress and update internal state.

        This method calls get_progress(), updates the last snapshot,
        and marks completion if the operation is done.

        Returns:
            Current progress snapshot.
        """
        self.mark_started()
        snapshot = await self.get_progress()
        self._last_snapshot = snapshot

        if self.is_complete() or self.has_failed():
            self.mark_completed()

        return snapshot
