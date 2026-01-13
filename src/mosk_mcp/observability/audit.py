"""Audit logging for MOSK MCP Server.

This module provides comprehensive audit logging for all operations,
including:
- Structured audit events with full context
- Async file writing for audit trails
- Log rotation support (size-based and time-based)
- Integration with structured logging
- Query interface for audit history
- CRQ validation support
"""

from __future__ import annotations

import asyncio
import gzip
import json
import shutil
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

import aiofiles
import aiofiles.os

from mosk_mcp.observability.logging import get_logger


if TYPE_CHECKING:
    from mosk_mcp.auth.types import UserContext
    from mosk_mcp.core.config import Settings


logger = get_logger(__name__)


class AuditLogRotator:
    """Handles audit log rotation with size-based and time-based strategies.

    This class provides log rotation functionality to prevent audit logs
    from growing unbounded. Supports:
    - Size-based rotation: Rotate when file exceeds max_size_mb
    - Time-based rotation: Rotate at specified intervals (hourly, daily, midnight)
    - Compression: Rotated files are gzipped to save space
    - Backup management: Keeps configurable number of backup files

    Attributes:
        log_path: Path to the audit log file.
        max_size_bytes: Maximum log size before rotation.
        backup_count: Number of backup files to keep.
        rotation_when: When to rotate (size, midnight, hourly, daily).
        compress: Whether to compress rotated files.
    """

    def __init__(
        self,
        log_path: Path,
        max_size_mb: int = 100,
        backup_count: int = 10,
        rotation_when: str = "midnight",
        compress: bool = True,
    ) -> None:
        """Initialize the log rotator.

        Args:
            log_path: Path to the audit log file.
            max_size_mb: Maximum log file size in megabytes.
            backup_count: Number of backup files to retain.
            rotation_when: Rotation trigger (size, midnight, h, d).
            compress: Whether to gzip rotated files.
        """
        self.log_path = log_path
        self.max_size_bytes = max_size_mb * 1024 * 1024
        self.backup_count = backup_count
        self.rotation_when = rotation_when.lower()
        self.compress = compress
        self._last_rotation_time: datetime | None = None
        self._rotation_lock = asyncio.Lock()

    def _get_rotation_suffix(self) -> str:
        """Generate a timestamp suffix for rotated files.

        Returns:
            Timestamp string for the backup filename.
        """
        now = datetime.now(UTC)
        return now.strftime("%Y%m%d-%H%M%S")

    def _should_rotate_by_size(self) -> bool:
        """Check if rotation is needed based on file size.

        Returns:
            True if the log file exceeds max_size_bytes.
        """
        if not self.log_path.exists():
            return False
        try:
            return self.log_path.stat().st_size >= self.max_size_bytes
        except OSError:
            return False

    def _should_rotate_by_time(self) -> bool:
        """Check if rotation is needed based on time.

        Returns:
            True if enough time has passed since last rotation.
        """
        now = datetime.now(UTC)

        if self._last_rotation_time is None:
            # First check - see if file exists and is old enough
            if not self.log_path.exists():
                return False
            try:
                mtime = datetime.fromtimestamp(self.log_path.stat().st_mtime, tz=UTC)
                self._last_rotation_time = mtime
            except OSError:
                return False

        if self.rotation_when == "midnight":
            # Rotate if we've crossed midnight since last rotation
            return now.date() > self._last_rotation_time.date()
        elif self.rotation_when == "h":
            # Rotate hourly
            return (now - self._last_rotation_time).total_seconds() >= 3600
        elif self.rotation_when == "d":
            # Rotate daily
            return (now - self._last_rotation_time).total_seconds() >= 86400
        return False

    def should_rotate(self) -> bool:
        """Check if log rotation is needed.

        Returns:
            True if rotation should be performed.
        """
        return self._should_rotate_by_size() or self._should_rotate_by_time()

    async def rotate(self) -> bool:
        """Perform log rotation.

        Rotates the current log file, compresses it if configured,
        and removes old backup files exceeding backup_count.

        Returns:
            True if rotation was successful, False otherwise.
        """
        async with self._rotation_lock:
            if not self.log_path.exists():
                return False

            try:
                # Generate backup filename
                suffix = self._get_rotation_suffix()
                backup_name = f"{self.log_path.stem}.{suffix}{self.log_path.suffix}"
                backup_path = self.log_path.parent / backup_name

                # Move current log to backup
                await asyncio.to_thread(shutil.move, str(self.log_path), str(backup_path))

                # Compress if configured
                if self.compress:
                    gz_path = backup_path.with_suffix(backup_path.suffix + ".gz")
                    await self._compress_file(backup_path, gz_path)
                    # Remove uncompressed backup
                    await aiofiles.os.remove(backup_path)
                    backup_path = gz_path

                # Update last rotation time
                self._last_rotation_time = datetime.now(UTC)

                # Cleanup old backups
                await self._cleanup_old_backups()

                logger.info(
                    "audit_log_rotated",
                    backup_path=str(backup_path),
                    compressed=self.compress,
                )
                return True

            except Exception as e:
                logger.error(
                    "audit_log_rotation_failed",
                    error=str(e),
                    log_path=str(self.log_path),
                )
                return False

    async def _compress_file(self, src: Path, dst: Path) -> None:
        """Compress a file using gzip.

        Args:
            src: Source file path.
            dst: Destination gzip file path.
        """

        def _do_compress() -> None:
            with src.open("rb") as f_in, gzip.open(dst, "wb", compresslevel=6) as f_out:
                shutil.copyfileobj(f_in, f_out)

        await asyncio.to_thread(_do_compress)

    async def _cleanup_old_backups(self) -> None:
        """Remove old backup files exceeding backup_count."""
        if self.backup_count <= 0:
            return

        try:
            # Find all backup files
            pattern = f"{self.log_path.stem}.*{self.log_path.suffix}*"
            backup_files = sorted(
                self.log_path.parent.glob(pattern),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )

            # Remove files exceeding backup_count
            files_to_remove = backup_files[self.backup_count :]
            for backup_file in files_to_remove:
                try:
                    await aiofiles.os.remove(backup_file)
                    logger.debug(
                        "audit_backup_removed",
                        path=str(backup_file),
                    )
                except OSError as e:
                    logger.warning(
                        "audit_backup_removal_failed",
                        path=str(backup_file),
                        error=str(e),
                    )

        except Exception as e:
            logger.warning(
                "audit_backup_cleanup_failed",
                error=str(e),
            )

    def get_backup_files(self) -> list[Path]:
        """Get list of backup files.

        Returns:
            List of backup file paths sorted by modification time.
        """
        if not self.log_path.parent.exists():
            return []

        pattern = f"{self.log_path.stem}.*{self.log_path.suffix}*"
        return sorted(
            self.log_path.parent.glob(pattern),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )


class AuditLevel(str, Enum):
    """Audit level classification for operations.

    Attributes:
        READ: Read-only operations that don't modify state.
        WRITE: Operations that modify state but are non-destructive.
        PRIVILEGED: Destructive or high-impact operations requiring CRQ.
    """

    READ = "read"
    WRITE = "write"
    PRIVILEGED = "privileged"


class AuditStatus(str, Enum):
    """Status of an audited operation.

    Attributes:
        STARTED: Operation has started.
        SUCCESS: Operation completed successfully.
        FAILURE: Operation failed.
        DENIED: Operation was denied (authorization).
        CANCELLED: Operation was cancelled.
    """

    STARTED = "started"
    SUCCESS = "success"
    FAILURE = "failure"
    DENIED = "denied"
    CANCELLED = "cancelled"


class AuditCategory(str, Enum):
    """Category of audited operations.

    Attributes:
        AUTHENTICATION: Authentication events.
        AUTHORIZATION: Authorization checks.
        TOOL_EXECUTION: Tool invocation events.
        RESOURCE_ACCESS: Kubernetes resource access.
        RESOURCE_MODIFICATION: Kubernetes resource changes.
        CONFIGURATION: Configuration changes.
        SYSTEM: System-level events.
    """

    AUTHENTICATION = "authentication"
    AUTHORIZATION = "authorization"
    TOOL_EXECUTION = "tool_execution"
    RESOURCE_ACCESS = "resource_access"
    RESOURCE_MODIFICATION = "resource_modification"
    CONFIGURATION = "configuration"
    SYSTEM = "system"


@dataclass
class AuditEvent:
    """Represents an audit event.

    Attributes:
        event_id: Unique identifier for this event.
        timestamp: When the event occurred.
        category: Event category.
        level: Audit level.
        status: Event status.
        user_id: ID of the user who triggered the event.
        username: Username of the user.
        action: Action being performed.
        resource_type: Type of resource being accessed/modified.
        resource_name: Name of the resource.
        resource_namespace: Namespace of the resource.
        details: Additional event details.
        crq_id: Change request ID for privileged operations.
        error_message: Error message if operation failed.
        duration_ms: Duration in milliseconds (for completed events).
        request_id: Correlation ID for request tracing.
        tool_name: Name of the tool if tool execution.
        ip_address: Client IP address if available.
    """

    event_id: str
    timestamp: datetime
    category: AuditCategory
    level: AuditLevel
    status: AuditStatus
    user_id: str
    username: str
    action: str
    resource_type: str | None = None
    resource_name: str | None = None
    resource_namespace: str | None = None
    details: dict[str, Any] = field(default_factory=dict)
    crq_id: str | None = None
    error_message: str | None = None
    duration_ms: float | None = None
    request_id: str | None = None
    tool_name: str | None = None
    ip_address: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization.

        Returns:
            Dictionary representation of the event.
        """
        result: dict[str, Any] = {
            "event_id": self.event_id,
            "timestamp": self.timestamp.isoformat(),
            "category": self.category.value,
            "level": self.level.value,
            "status": self.status.value,
            "user_id": self.user_id,
            "username": self.username,
            "action": self.action,
        }

        if self.resource_type:
            result["resource_type"] = self.resource_type
        if self.resource_name:
            result["resource_name"] = self.resource_name
        if self.resource_namespace:
            result["resource_namespace"] = self.resource_namespace
        if self.details:
            result["details"] = self.details
        if self.crq_id:
            result["crq_id"] = self.crq_id
        if self.error_message:
            result["error_message"] = self.error_message
        if self.duration_ms is not None:
            result["duration_ms"] = self.duration_ms
        if self.request_id:
            result["request_id"] = self.request_id
        if self.tool_name:
            result["tool_name"] = self.tool_name
        if self.ip_address:
            result["ip_address"] = self.ip_address

        return result

    def to_json(self) -> str:
        """Convert to JSON string.

        Returns:
            JSON representation of the event.
        """
        return json.dumps(self.to_dict())

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AuditEvent:
        """Create from dictionary.

        Args:
            data: Dictionary with event data.

        Returns:
            AuditEvent instance.
        """
        return cls(
            event_id=data["event_id"],
            timestamp=datetime.fromisoformat(data["timestamp"]),
            category=AuditCategory(data["category"]),
            level=AuditLevel(data["level"]),
            status=AuditStatus(data["status"]),
            user_id=data["user_id"],
            username=data["username"],
            action=data["action"],
            resource_type=data.get("resource_type"),
            resource_name=data.get("resource_name"),
            resource_namespace=data.get("resource_namespace"),
            details=data.get("details", {}),
            crq_id=data.get("crq_id"),
            error_message=data.get("error_message"),
            duration_ms=data.get("duration_ms"),
            request_id=data.get("request_id"),
            tool_name=data.get("tool_name"),
            ip_address=data.get("ip_address"),
        )


class AuditLogger:
    """Audit logger with structured logging and file output.

    This logger provides:
    - Structured logging via structlog
    - Async file writes to audit log
    - Automatic log rotation (size-based and time-based)
    - Event tracking and correlation with bounded cache
    - Query interface for audit history

    The event cache is bounded to prevent memory growth. When the cache
    exceeds max_cache_size, the oldest events are automatically evicted.

    Attributes:
        _audit_log_path: Path to audit log file.
        _enabled: Whether audit logging is enabled.
        _write_lock: Async lock for file writes.
        _event_cache: Cache of recent events for tracking (bounded).
        _max_cache_size: Maximum number of events to keep in cache.
        _rotator: Log rotator for automatic rotation.
        _rotation_enabled: Whether log rotation is enabled.

    Example:
        audit = AuditLogger(settings)

        event_id = await audit.log_tool_start(
            user=context,
            tool_name="list_machines",
            parameters={"namespace": "default"},
        )

        await audit.log_tool_success(event_id, result_count=10)
    """

    # Default maximum cache size
    DEFAULT_MAX_CACHE_SIZE = 1000

    def __init__(
        self,
        settings: Settings | None = None,
        max_cache_size: int | None = None,
    ) -> None:
        """Initialize the audit logger.

        Args:
            settings: Application settings. If None, uses defaults.
            max_cache_size: Maximum number of events to keep in cache.
                           Defaults to DEFAULT_MAX_CACHE_SIZE (1000).
        """
        if settings:
            self._audit_log_path = settings.audit_log_path
            self._enabled = settings.audit_enabled
            self._rotation_enabled = getattr(settings, "audit_rotation_enabled", True)
            max_size_mb = getattr(settings, "audit_max_size_mb", 100)
            backup_count = getattr(settings, "audit_backup_count", 10)
            rotation_when = getattr(settings, "audit_rotation_when", "midnight")
            # Docker-friendly mode: skip file writes, log to stderr only
            self._stderr_only = getattr(settings, "log_to_stderr_only", True)
        else:
            self._audit_log_path = Path("/var/log/mosk-mcp/audit.log")
            self._enabled = True
            self._rotation_enabled = True
            max_size_mb = 100
            backup_count = 10
            rotation_when = "midnight"
            self._stderr_only = True  # Default to stderr-only for Docker

        self._write_lock = asyncio.Lock()
        self._event_cache: dict[str, AuditEvent] = {}
        self._max_cache_size = max_cache_size or self.DEFAULT_MAX_CACHE_SIZE
        # Track insertion order for LRU eviction
        self._event_order: list[str] = []

        # Initialize log rotator
        self._rotator: AuditLogRotator | None = None
        if self._rotation_enabled:
            self._rotator = AuditLogRotator(
                log_path=self._audit_log_path,
                max_size_mb=max_size_mb,
                backup_count=backup_count,
                rotation_when=rotation_when,
                compress=True,
            )

    @classmethod
    def from_settings(cls, settings: Settings) -> AuditLogger:
        """Create audit logger from settings.

        Args:
            settings: Application settings.

        Returns:
            Configured AuditLogger instance.
        """
        return cls(settings)

    @property
    def enabled(self) -> bool:
        """Check if audit logging is enabled.

        Returns:
            True if enabled.
        """
        return self._enabled

    @property
    def audit_log_path(self) -> Path:
        """Get the audit log file path.

        Returns:
            Path to audit log file.
        """
        return self._audit_log_path

    @property
    def cache_size(self) -> int:
        """Get the current size of the event cache.

        Returns:
            Number of events in the cache.
        """
        return len(self._event_cache)

    @property
    def max_cache_size(self) -> int:
        """Get the maximum cache size.

        Returns:
            Maximum number of events to keep in cache.
        """
        return self._max_cache_size

    @property
    def rotation_enabled(self) -> bool:
        """Check if log rotation is enabled.

        Returns:
            True if rotation is enabled.
        """
        return self._rotation_enabled

    @property
    def rotator(self) -> AuditLogRotator | None:
        """Get the log rotator instance.

        Returns:
            AuditLogRotator instance or None if disabled.
        """
        return self._rotator

    @property
    def stderr_only(self) -> bool:
        """Check if logging to stderr only (Docker-friendly mode).

        When True, file writes are skipped and all logs go to structlog
        which outputs to stderr, making them visible via `docker logs`.

        Returns:
            True if in stderr-only mode.
        """
        return self._stderr_only

    def _generate_event_id(self) -> str:
        """Generate a unique event ID.

        Returns:
            UUID string.
        """
        return str(uuid.uuid4())

    def _evict_old_events(self) -> None:
        """Evict oldest events if cache exceeds max size.

        This method removes the oldest events from the cache to keep
        the cache size bounded. Uses FIFO eviction strategy.
        """
        if len(self._event_cache) <= self._max_cache_size:
            return

        # Calculate how many to evict (evict 10% extra to reduce frequency)
        evict_count = len(self._event_cache) - self._max_cache_size + (self._max_cache_size // 10)
        evict_count = max(1, evict_count)

        # Evict oldest events
        events_to_evict = self._event_order[:evict_count]
        for event_id in events_to_evict:
            self._event_cache.pop(event_id, None)
        self._event_order = self._event_order[evict_count:]

        logger.debug(
            "audit_cache_evicted",
            evicted_count=evict_count,
            cache_size=len(self._event_cache),
        )

    def _add_to_cache(self, event_id: str, event: AuditEvent) -> None:
        """Add an event to the cache with automatic eviction.

        Args:
            event_id: The event ID.
            event: The audit event to cache.
        """
        # If event already exists, update it without changing order
        if event_id in self._event_cache:
            self._event_cache[event_id] = event
            return

        # Add new event
        self._event_cache[event_id] = event
        self._event_order.append(event_id)

        # Evict old events if cache is too large
        self._evict_old_events()

    async def _ensure_log_directory(self) -> None:
        """Ensure the audit log directory exists."""
        try:
            await aiofiles.os.makedirs(
                self._audit_log_path.parent,
                exist_ok=True,
            )
        except Exception as e:
            logger.warning(
                "audit_directory_creation_failed",
                path=str(self._audit_log_path.parent),
                error=str(e),
            )

    async def _check_and_rotate(self) -> None:
        """Check if log rotation is needed and perform it.

        This method is called before each write to ensure logs
        don't exceed size limits or time boundaries.
        """
        if self._rotator is None:
            return

        if self._rotator.should_rotate():
            await self._rotator.rotate()

    async def _write_to_file(self, event: AuditEvent) -> None:
        """Write an audit event to the log file.

        Args:
            event: The audit event to write.
        """
        if not self._enabled:
            return

        # Skip file writes in stderr-only mode (Docker-friendly)
        # All logs go to structlog which outputs to stderr
        if self._stderr_only:
            return

        try:
            await self._ensure_log_directory()

            # Check for rotation before writing
            await self._check_and_rotate()

            async with (
                self._write_lock,
                aiofiles.open(
                    self._audit_log_path,
                    mode="a",
                    encoding="utf-8",
                ) as f,
            ):
                await f.write(event.to_json() + "\n")

        except Exception as e:
            logger.error(
                "audit_write_failed",
                event_id=event.event_id,
                error=str(e),
            )

    def _log_to_structlog(self, event: AuditEvent) -> None:
        """Log an audit event to structlog.

        Args:
            event: The audit event to log.
        """
        log_data = event.to_dict()
        log_data.pop("timestamp", None)  # structlog adds its own timestamp

        if event.status == AuditStatus.FAILURE:
            logger.warning("audit_event", **log_data)
        elif event.status == AuditStatus.DENIED:
            logger.warning("audit_event_denied", **log_data)
        else:
            logger.info("audit_event", **log_data)

    async def log(
        self,
        category: AuditCategory,
        level: AuditLevel,
        status: AuditStatus,
        user_id: str,
        username: str,
        action: str,
        resource_type: str | None = None,
        resource_name: str | None = None,
        resource_namespace: str | None = None,
        details: dict[str, Any] | None = None,
        crq_id: str | None = None,
        error_message: str | None = None,
        duration_ms: float | None = None,
        request_id: str | None = None,
        tool_name: str | None = None,
        ip_address: str | None = None,
        event_id: str | None = None,
    ) -> str:
        """Log an audit event.

        Args:
            category: Event category.
            level: Audit level.
            status: Event status.
            user_id: User ID.
            username: Username.
            action: Action being performed.
            resource_type: Type of resource.
            resource_name: Resource name.
            resource_namespace: Resource namespace.
            details: Additional details.
            crq_id: Change request ID.
            error_message: Error message if failed.
            duration_ms: Duration in milliseconds.
            request_id: Request correlation ID.
            tool_name: Tool name.
            ip_address: Client IP.
            event_id: Existing event ID (for updates).

        Returns:
            Event ID.
        """
        eid = event_id or self._generate_event_id()

        event = AuditEvent(
            event_id=eid,
            timestamp=datetime.now(UTC),
            category=category,
            level=level,
            status=status,
            user_id=user_id,
            username=username,
            action=action,
            resource_type=resource_type,
            resource_name=resource_name,
            resource_namespace=resource_namespace,
            details=details or {},
            crq_id=crq_id,
            error_message=error_message,
            duration_ms=duration_ms,
            request_id=request_id,
            tool_name=tool_name,
            ip_address=ip_address,
        )

        # Cache the event for later updates (with automatic eviction)
        self._add_to_cache(eid, event)

        # Log to structlog (always)
        self._log_to_structlog(event)

        # Write to file (async)
        if self._enabled:
            await self._write_to_file(event)

        return eid

    async def log_from_context(
        self,
        context: UserContext,
        category: AuditCategory,
        level: AuditLevel,
        status: AuditStatus,
        action: str,
        **kwargs: Any,
    ) -> str:
        """Log an audit event using UserContext.

        Args:
            context: User context.
            category: Event category.
            level: Audit level.
            status: Event status.
            action: Action being performed.
            **kwargs: Additional event parameters.

        Returns:
            Event ID.
        """
        return await self.log(
            category=category,
            level=level,
            status=status,
            user_id=context.user_id,
            username=context.username,
            action=action,
            **kwargs,
        )

    # =========================================================================
    # Convenience Methods for Common Events
    # =========================================================================

    async def log_tool_start(
        self,
        user: UserContext,
        tool_name: str,
        parameters: dict[str, Any] | None = None,
        level: AuditLevel = AuditLevel.READ,
        crq_id: str | None = None,
        request_id: str | None = None,
    ) -> str:
        """Log the start of a tool execution.

        Args:
            user: User context.
            tool_name: Name of the tool.
            parameters: Tool parameters (sanitized).
            level: Audit level.
            crq_id: Change request ID.
            request_id: Request correlation ID.

        Returns:
            Event ID for tracking.
        """
        return await self.log(
            category=AuditCategory.TOOL_EXECUTION,
            level=level,
            status=AuditStatus.STARTED,
            user_id=user.user_id,
            username=user.username,
            action=f"tool:{tool_name}",
            tool_name=tool_name,
            details={"parameters": self._sanitize_parameters(parameters or {})},
            crq_id=crq_id,
            request_id=request_id,
        )

    async def log_tool_success(
        self,
        event_id: str,
        duration_ms: float | None = None,
        result_summary: dict[str, Any] | None = None,
    ) -> None:
        """Log successful completion of a tool execution.

        Args:
            event_id: Event ID from log_tool_start.
            duration_ms: Execution duration.
            result_summary: Summary of results.
        """
        original = self._event_cache.get(event_id)
        if not original:
            logger.warning("audit_event_not_found", event_id=event_id)
            return

        await self.log(
            category=original.category,
            level=original.level,
            status=AuditStatus.SUCCESS,
            user_id=original.user_id,
            username=original.username,
            action=original.action,
            tool_name=original.tool_name,
            details={**original.details, "result": result_summary or {}},
            crq_id=original.crq_id,
            duration_ms=duration_ms,
            request_id=original.request_id,
            event_id=event_id,
        )

    async def log_tool_failure(
        self,
        event_id: str,
        error_message: str,
        duration_ms: float | None = None,
        error_details: dict[str, Any] | None = None,
    ) -> None:
        """Log failed tool execution.

        Args:
            event_id: Event ID from log_tool_start.
            error_message: Error message.
            duration_ms: Execution duration.
            error_details: Additional error details.
        """
        original = self._event_cache.get(event_id)
        if not original:
            logger.warning("audit_event_not_found", event_id=event_id)
            return

        await self.log(
            category=original.category,
            level=original.level,
            status=AuditStatus.FAILURE,
            user_id=original.user_id,
            username=original.username,
            action=original.action,
            tool_name=original.tool_name,
            details={**original.details, "error_details": error_details or {}},
            crq_id=original.crq_id,
            error_message=error_message,
            duration_ms=duration_ms,
            request_id=original.request_id,
            event_id=event_id,
        )

    async def log_authentication(
        self,
        success: bool,
        user_id: str | None = None,
        username: str | None = None,
        auth_method: str = "unknown",
        error_message: str | None = None,
        ip_address: str | None = None,
    ) -> str:
        """Log an authentication event.

        Args:
            success: Whether authentication succeeded.
            user_id: User ID if known.
            username: Username if known.
            auth_method: Authentication method used.
            error_message: Error message if failed.
            ip_address: Client IP address.

        Returns:
            Event ID.
        """
        return await self.log(
            category=AuditCategory.AUTHENTICATION,
            level=AuditLevel.READ,
            status=AuditStatus.SUCCESS if success else AuditStatus.FAILURE,
            user_id=user_id or "unknown",
            username=username or "unknown",
            action=f"auth:{auth_method}",
            error_message=error_message,
            ip_address=ip_address,
            details={"auth_method": auth_method},
        )

    async def log_authorization_denied(
        self,
        user: UserContext,
        action: str,
        required_permission: str,
        resource_type: str | None = None,
        resource_name: str | None = None,
    ) -> str:
        """Log an authorization denial.

        Args:
            user: User context.
            action: Action that was denied.
            required_permission: Permission that was required.
            resource_type: Resource type.
            resource_name: Resource name.

        Returns:
            Event ID.
        """
        return await self.log(
            category=AuditCategory.AUTHORIZATION,
            level=AuditLevel.READ,
            status=AuditStatus.DENIED,
            user_id=user.user_id,
            username=user.username,
            action=action,
            resource_type=resource_type,
            resource_name=resource_name,
            details={
                "required_permission": required_permission,
                "user_role": user.role.value,
                "user_permissions": [p.value for p in user.permissions],
            },
        )

    async def log_resource_access(
        self,
        user: UserContext,
        operation: str,
        resource_type: str,
        resource_name: str,
        namespace: str | None = None,
        success: bool = True,
        error_message: str | None = None,
    ) -> str:
        """Log resource access.

        Args:
            user: User context.
            operation: Operation (get, list, watch).
            resource_type: Resource type.
            resource_name: Resource name.
            namespace: Resource namespace.
            success: Whether operation succeeded.
            error_message: Error message if failed.

        Returns:
            Event ID.
        """
        return await self.log(
            category=AuditCategory.RESOURCE_ACCESS,
            level=AuditLevel.READ,
            status=AuditStatus.SUCCESS if success else AuditStatus.FAILURE,
            user_id=user.user_id,
            username=user.username,
            action=f"resource:{operation}",
            resource_type=resource_type,
            resource_name=resource_name,
            resource_namespace=namespace,
            error_message=error_message,
        )

    async def log_resource_modification(
        self,
        user: UserContext,
        operation: str,
        resource_type: str,
        resource_name: str,
        namespace: str | None = None,
        crq_id: str | None = None,
        success: bool = True,
        error_message: str | None = None,
        changes: dict[str, Any] | None = None,
    ) -> str:
        """Log resource modification.

        Args:
            user: User context.
            operation: Operation (create, patch, delete).
            resource_type: Resource type.
            resource_name: Resource name.
            namespace: Resource namespace.
            crq_id: Change request ID.
            success: Whether operation succeeded.
            error_message: Error message if failed.
            changes: Summary of changes made.

        Returns:
            Event ID.
        """
        level = AuditLevel.PRIVILEGED if operation == "delete" else AuditLevel.WRITE

        return await self.log(
            category=AuditCategory.RESOURCE_MODIFICATION,
            level=level,
            status=AuditStatus.SUCCESS if success else AuditStatus.FAILURE,
            user_id=user.user_id,
            username=user.username,
            action=f"resource:{operation}",
            resource_type=resource_type,
            resource_name=resource_name,
            resource_namespace=namespace,
            crq_id=crq_id,
            error_message=error_message,
            details={"changes": changes} if changes else {},
        )

    # =========================================================================
    # Query Interface
    # =========================================================================

    async def query_events(
        self,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        user_id: str | None = None,
        category: AuditCategory | None = None,
        level: AuditLevel | None = None,
        status: AuditStatus | None = None,
        resource_type: str | None = None,
        limit: int = 100,
    ) -> list[AuditEvent]:
        """Query audit events from the log file.

        Args:
            start_time: Start of time range.
            end_time: End of time range.
            user_id: Filter by user ID.
            category: Filter by category.
            level: Filter by level.
            status: Filter by status.
            resource_type: Filter by resource type.
            limit: Maximum number of events to return.

        Returns:
            List of matching audit events.
        """
        events: list[AuditEvent] = []

        if not self._audit_log_path.exists():
            return events

        try:
            async with aiofiles.open(
                self._audit_log_path,
                encoding="utf-8",
            ) as f:
                async for raw_line in f:
                    line = raw_line.strip()
                    if not line:
                        continue

                    try:
                        data = json.loads(line)
                        event = AuditEvent.from_dict(data)

                        # Apply filters
                        if start_time and event.timestamp < start_time:
                            continue
                        if end_time and event.timestamp > end_time:
                            continue
                        if user_id and event.user_id != user_id:
                            continue
                        if category and event.category != category:
                            continue
                        if level and event.level != level:
                            continue
                        if status and event.status != status:
                            continue
                        if resource_type and event.resource_type != resource_type:
                            continue

                        events.append(event)

                        if len(events) >= limit:
                            break

                    except (json.JSONDecodeError, KeyError, ValueError) as e:
                        logger.warning(
                            "audit_parse_error",
                            line=line[:100],
                            error=str(e),
                        )
                        continue

        except Exception as e:
            logger.error("audit_query_failed", error=str(e))

        return events

    async def get_recent_events(
        self,
        count: int = 50,
    ) -> list[AuditEvent]:
        """Get the most recent audit events.

        Args:
            count: Number of events to return.

        Returns:
            List of recent audit events.
        """
        # For efficiency, read from end of file
        events: list[AuditEvent] = []

        if not self._audit_log_path.exists():
            return events

        try:
            async with aiofiles.open(
                self._audit_log_path,
                encoding="utf-8",
            ) as f:
                # Read all lines and take last N
                # For very large files, this should be optimized
                raw_lines = await f.readlines()
                lines = [ln.strip() for ln in raw_lines if ln.strip()]
                lines = lines[-count:]

                for line in lines:
                    try:
                        data = json.loads(line)
                        events.append(AuditEvent.from_dict(data))
                    except (json.JSONDecodeError, KeyError, ValueError) as e:
                        logger.warning(
                            "audit_parse_error",
                            line=line[:100],
                            error=str(e),
                        )
                        continue

        except Exception as e:
            logger.error("audit_recent_query_failed", error=str(e))

        return events

    # =========================================================================
    # Helpers
    # =========================================================================

    def _sanitize_parameters(self, params: dict[str, Any]) -> dict[str, Any]:
        """Sanitize parameters to remove sensitive data.

        Args:
            params: Parameters to sanitize.

        Returns:
            Sanitized parameters.
        """
        sensitive_keys = {
            "password",
            "secret",
            "token",
            "api_key",
            "apikey",
            "auth",
            "credential",
            "credentials",
            "private_key",
            "privatekey",
        }

        sanitized: dict[str, Any] = {}
        for key, value in params.items():
            key_lower = key.lower()
            if any(s in key_lower for s in sensitive_keys):
                sanitized[key] = "[REDACTED]"
            elif isinstance(value, dict):
                sanitized[key] = self._sanitize_parameters(value)
            elif isinstance(value, str) and len(value) > 1000:
                sanitized[key] = f"{value[:100]}...[truncated]"
            else:
                sanitized[key] = value

        return sanitized

    def clear_event_cache(self) -> None:
        """Clear the event cache.

        This method clears all cached events. Note that automatic eviction
        is now enabled, so manual clearing is typically not necessary.
        """
        self._event_cache.clear()
        self._event_order.clear()

    async def rotate_logs(self) -> bool:
        """Manually trigger log rotation.

        Forces log rotation regardless of size or time thresholds.
        Useful for administrative tasks or before maintenance.

        Returns:
            True if rotation was successful, False otherwise.
        """
        if self._rotator is None:
            logger.warning("audit_rotation_disabled")
            return False

        return await self._rotator.rotate()

    def get_backup_files(self) -> list[Path]:
        """Get list of backup audit log files.

        Returns:
            List of backup file paths sorted by modification time (newest first).
        """
        if self._rotator is None:
            return []
        return self._rotator.get_backup_files()


class AuditContext:
    """Context manager for tracking operation duration in audit logs.

    Example:
        async with AuditContext(audit, user, "list_machines") as ctx:
            result = await list_machines()
            ctx.set_result({"count": len(result)})
    """

    def __init__(
        self,
        audit_logger: AuditLogger,
        user: UserContext,
        tool_name: str,
        parameters: dict[str, Any] | None = None,
        level: AuditLevel = AuditLevel.READ,
        crq_id: str | None = None,
        request_id: str | None = None,
    ) -> None:
        """Initialize audit context.

        Args:
            audit_logger: Audit logger instance.
            user: User context.
            tool_name: Tool name.
            parameters: Tool parameters.
            level: Audit level.
            crq_id: Change request ID.
            request_id: Request correlation ID.
        """
        self._audit = audit_logger
        self._user = user
        self._tool_name = tool_name
        self._parameters = parameters
        self._level = level
        self._crq_id = crq_id
        self._request_id = request_id
        self._event_id: str | None = None
        self._start_time: float | None = None
        self._result_summary: dict[str, Any] | None = None

    def set_result(self, summary: dict[str, Any]) -> None:
        """Set the result summary for successful completion.

        Args:
            summary: Result summary to include in audit log.
        """
        self._result_summary = summary

    async def __aenter__(self) -> AuditContext:
        """Enter the context and log start."""
        import time

        self._start_time = time.monotonic()
        self._event_id = await self._audit.log_tool_start(
            user=self._user,
            tool_name=self._tool_name,
            parameters=self._parameters,
            level=self._level,
            crq_id=self._crq_id,
            request_id=self._request_id,
        )
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        """Exit the context and log completion."""
        import time

        if self._event_id is None or self._start_time is None:
            return

        duration_ms = (time.monotonic() - self._start_time) * 1000

        if exc_val is None:
            await self._audit.log_tool_success(
                event_id=self._event_id,
                duration_ms=duration_ms,
                result_summary=self._result_summary,
            )
        else:
            await self._audit.log_tool_failure(
                event_id=self._event_id,
                error_message=str(exc_val),
                duration_ms=duration_ms,
                error_details={"error_type": exc_type.__name__ if exc_type else None},
            )
