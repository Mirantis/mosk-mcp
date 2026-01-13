"""Tests for the audit logging module.

This module tests the AuditLogger class and related functionality.
"""

import json
from datetime import UTC, datetime

import pytest

from mosk_mcp.auth.types import Permission, Role, UserContext
from mosk_mcp.core.config import Environment, LogFormat, Settings
from mosk_mcp.observability.audit import (
    AuditCategory,
    AuditContext,
    AuditEvent,
    AuditLevel,
    AuditLogger,
    AuditStatus,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def audit_log_path(tmp_path):
    """Create a temporary audit log path."""
    return tmp_path / "audit.log"


@pytest.fixture
def settings(audit_log_path):
    """Create test settings with audit logging enabled.

    Note: environment=DEVELOPMENT allows auth_enabled=False
    and doesn't require MCC URL.
    log_to_stderr_only=False enables file writes for testing.
    """
    return Settings(
        audit_log_path=audit_log_path,
        audit_enabled=True,
        auth_enabled=False,
        otel_enabled=False,
        log_format=LogFormat.CONSOLE,
        environment=Environment.DEVELOPMENT,
        log_to_stderr_only=False,  # Enable file writes for testing
    )


@pytest.fixture
def disabled_settings(audit_log_path):
    """Create test settings with audit logging disabled.

    Note: environment=DEVELOPMENT allows auth_enabled=False
    and doesn't require MCC URL.
    """
    return Settings(
        audit_log_path=audit_log_path,
        audit_enabled=False,
        auth_enabled=False,
        otel_enabled=False,
        log_format=LogFormat.CONSOLE,
        environment=Environment.DEVELOPMENT,
    )


@pytest.fixture
def audit_logger(settings):
    """Create an audit logger for testing."""
    return AuditLogger(settings)


@pytest.fixture
def user_context():
    """Create a test user context."""
    return UserContext(
        user_id="test-user-123",
        username="testuser",
        role=Role.OPERATOR,
        permissions=frozenset([Permission.READ_MACHINES, Permission.WRITE_MACHINES]),
        authenticated_at=datetime.now(UTC),
        auth_method="test",
    )


# =============================================================================
# AuditEvent Tests
# =============================================================================


class TestAuditEvent:
    """Tests for AuditEvent dataclass."""

    def test_create_event(self):
        """Test creating an audit event."""
        event = AuditEvent(
            event_id="test-123",
            timestamp=datetime.now(UTC),
            category=AuditCategory.TOOL_EXECUTION,
            level=AuditLevel.READ,
            status=AuditStatus.SUCCESS,
            user_id="user-1",
            username="testuser",
            action="list_machines",
        )

        assert event.event_id == "test-123"
        assert event.category == AuditCategory.TOOL_EXECUTION
        assert event.level == AuditLevel.READ
        assert event.status == AuditStatus.SUCCESS

    def test_event_to_dict(self):
        """Test converting event to dictionary."""
        now = datetime.now(UTC)
        event = AuditEvent(
            event_id="test-123",
            timestamp=now,
            category=AuditCategory.TOOL_EXECUTION,
            level=AuditLevel.WRITE,
            status=AuditStatus.SUCCESS,
            user_id="user-1",
            username="testuser",
            action="create_machine",
            resource_type="Machine",
            resource_name="compute-01",
            crq_id="CRQ123456789",
        )

        result = event.to_dict()

        assert result["event_id"] == "test-123"
        assert result["timestamp"] == now.isoformat()
        assert result["category"] == "tool_execution"
        assert result["level"] == "write"
        assert result["status"] == "success"
        assert result["resource_type"] == "Machine"
        assert result["crq_id"] == "CRQ123456789"

    def test_event_to_json(self):
        """Test converting event to JSON."""
        event = AuditEvent(
            event_id="test-123",
            timestamp=datetime.now(UTC),
            category=AuditCategory.AUTHENTICATION,
            level=AuditLevel.READ,
            status=AuditStatus.SUCCESS,
            user_id="user-1",
            username="testuser",
            action="login",
        )

        json_str = event.to_json()
        parsed = json.loads(json_str)

        assert parsed["event_id"] == "test-123"
        assert parsed["category"] == "authentication"

    def test_event_from_dict(self):
        """Test creating event from dictionary."""
        data = {
            "event_id": "test-456",
            "timestamp": "2024-01-15T10:30:00+00:00",
            "category": "resource_access",
            "level": "read",
            "status": "success",
            "user_id": "user-2",
            "username": "admin",
            "action": "get_osdpl",
        }

        event = AuditEvent.from_dict(data)

        assert event.event_id == "test-456"
        assert event.category == AuditCategory.RESOURCE_ACCESS
        assert event.level == AuditLevel.READ
        assert event.status == AuditStatus.SUCCESS


# =============================================================================
# AuditLogger Tests
# =============================================================================


class TestAuditLogger:
    """Tests for AuditLogger class."""

    def test_create_logger_from_settings(self, settings):
        """Test creating logger from settings."""
        logger = AuditLogger.from_settings(settings)

        assert logger.enabled
        assert logger.audit_log_path == settings.audit_log_path

    def test_logger_disabled(self, disabled_settings):
        """Test logger when disabled."""
        logger = AuditLogger(disabled_settings)

        assert not logger.enabled

    @pytest.mark.asyncio
    async def test_log_event(self, audit_logger, audit_log_path):
        """Test logging an event."""
        event_id = await audit_logger.log(
            category=AuditCategory.TOOL_EXECUTION,
            level=AuditLevel.READ,
            status=AuditStatus.SUCCESS,
            user_id="test-user",
            username="testuser",
            action="list_machines",
        )

        assert event_id is not None

        # Verify file was written
        assert audit_log_path.exists()
        content = audit_log_path.read_text()
        assert "list_machines" in content

    @pytest.mark.asyncio
    async def test_log_from_context(self, audit_logger, user_context):
        """Test logging from user context."""
        event_id = await audit_logger.log_from_context(
            context=user_context,
            category=AuditCategory.TOOL_EXECUTION,
            level=AuditLevel.READ,
            status=AuditStatus.SUCCESS,
            action="get_machine",
        )

        assert event_id is not None

    @pytest.mark.asyncio
    async def test_log_tool_start(self, audit_logger, user_context):
        """Test logging tool start."""
        event_id = await audit_logger.log_tool_start(
            user=user_context,
            tool_name="create_machine",
            parameters={"name": "compute-01", "profile": "standard"},
            level=AuditLevel.WRITE,
        )

        assert event_id is not None
        assert event_id in audit_logger._event_cache

    @pytest.mark.asyncio
    async def test_log_tool_success(self, audit_logger, user_context):
        """Test logging tool success."""
        event_id = await audit_logger.log_tool_start(
            user=user_context,
            tool_name="list_machines",
        )

        await audit_logger.log_tool_success(
            event_id=event_id,
            duration_ms=150.5,
            result_summary={"count": 10},
        )

        # Event should still be in cache
        assert event_id in audit_logger._event_cache

    @pytest.mark.asyncio
    async def test_log_tool_failure(self, audit_logger, user_context):
        """Test logging tool failure."""
        event_id = await audit_logger.log_tool_start(
            user=user_context,
            tool_name="delete_machine",
            level=AuditLevel.PRIVILEGED,
        )

        await audit_logger.log_tool_failure(
            event_id=event_id,
            error_message="Permission denied",
            duration_ms=50.0,
            error_details={"reason": "insufficient_permissions"},
        )

    @pytest.mark.asyncio
    async def test_log_authentication_success(self, audit_logger):
        """Test logging authentication success."""
        event_id = await audit_logger.log_authentication(
            success=True,
            user_id="user-123",
            username="admin",
            auth_method="api_key",
        )

        assert event_id is not None

    @pytest.mark.asyncio
    async def test_log_authentication_failure(self, audit_logger):
        """Test logging authentication failure."""
        event_id = await audit_logger.log_authentication(
            success=False,
            auth_method="api_key",
            error_message="Invalid API key",
            ip_address="192.168.1.100",
        )

        assert event_id is not None

    @pytest.mark.asyncio
    async def test_log_authorization_denied(self, audit_logger, user_context):
        """Test logging authorization denial."""
        event_id = await audit_logger.log_authorization_denied(
            user=user_context,
            action="delete_osdpl",
            required_permission="admin:cluster",
            resource_type="OpenStackDeployment",
            resource_name="openstack",
        )

        assert event_id is not None

    @pytest.mark.asyncio
    async def test_log_resource_access(self, audit_logger, user_context):
        """Test logging resource access."""
        event_id = await audit_logger.log_resource_access(
            user=user_context,
            operation="list",
            resource_type="Machine",
            resource_name="*",
            namespace="default",
        )

        assert event_id is not None

    @pytest.mark.asyncio
    async def test_log_resource_modification(self, audit_logger, user_context):
        """Test logging resource modification."""
        event_id = await audit_logger.log_resource_modification(
            user=user_context,
            operation="create",
            resource_type="Machine",
            resource_name="compute-01",
            namespace="default",
            crq_id="CRQ123456789",
            changes={"spec": {"profile": "standard"}},
        )

        assert event_id is not None


# =============================================================================
# Query Interface Tests
# =============================================================================


class TestAuditLoggerQuery:
    """Tests for audit log query interface."""

    @pytest.mark.asyncio
    async def test_query_events(self, audit_logger, user_context):
        """Test querying events."""
        # Log some events
        await audit_logger.log_tool_start(
            user=user_context,
            tool_name="list_machines",
        )
        await audit_logger.log_tool_start(
            user=user_context,
            tool_name="get_osdpl",
        )

        # Query events
        events = await audit_logger.query_events(limit=10)

        assert len(events) >= 2

    @pytest.mark.asyncio
    async def test_query_events_with_filter(self, audit_logger, user_context):
        """Test querying events with filters."""
        # Log events with different categories
        await audit_logger.log_authentication(
            success=True,
            user_id="user-1",
            username="admin",
            auth_method="api_key",
        )
        await audit_logger.log_tool_start(
            user=user_context,
            tool_name="list_machines",
        )

        # Query only authentication events
        events = await audit_logger.query_events(
            category=AuditCategory.AUTHENTICATION,
            limit=10,
        )

        assert all(e.category == AuditCategory.AUTHENTICATION for e in events)

    @pytest.mark.asyncio
    async def test_get_recent_events(self, audit_logger, user_context):
        """Test getting recent events."""
        # Log several events
        for i in range(5):
            await audit_logger.log_tool_start(
                user=user_context,
                tool_name=f"tool_{i}",
            )

        events = await audit_logger.get_recent_events(count=3)

        assert len(events) == 3

    @pytest.mark.asyncio
    async def test_query_nonexistent_file(self, tmp_path):
        """Test querying when log file doesn't exist."""
        settings = Settings(
            audit_log_path=tmp_path / "nonexistent.log",
            audit_enabled=True,
            auth_enabled=False,
            otel_enabled=False,
            log_format=LogFormat.CONSOLE,
            environment=Environment.DEVELOPMENT,
        )
        logger = AuditLogger(settings)

        events = await logger.query_events()

        assert events == []


# =============================================================================
# Sanitization Tests
# =============================================================================


class TestAuditLoggerSanitization:
    """Tests for parameter sanitization."""

    def test_sanitize_sensitive_parameters(self, audit_logger):
        """Test that sensitive parameters are redacted."""
        params = {
            "name": "test",
            "password": "secret123",
            "api_key": "key-abc-123",
            "data": {"token": "xyz789"},
        }

        sanitized = audit_logger._sanitize_parameters(params)

        assert sanitized["name"] == "test"
        assert sanitized["password"] == "[REDACTED]"
        assert sanitized["api_key"] == "[REDACTED]"
        assert sanitized["data"]["token"] == "[REDACTED]"

    def test_sanitize_long_values(self, audit_logger):
        """Test that long values are truncated."""
        long_value = "x" * 2000
        params = {"data": long_value}

        sanitized = audit_logger._sanitize_parameters(params)

        assert len(sanitized["data"]) < len(long_value)
        assert "[truncated]" in sanitized["data"]


# =============================================================================
# AuditContext Tests
# =============================================================================


class TestAuditContext:
    """Tests for AuditContext context manager."""

    @pytest.mark.asyncio
    async def test_audit_context_success(self, audit_logger, user_context):
        """Test audit context for successful operation."""
        async with AuditContext(
            audit_logger=audit_logger,
            user=user_context,
            tool_name="list_machines",
        ) as ctx:
            ctx.set_result({"count": 5})
            # Simulate operation
            [{"name": f"machine-{i}"} for i in range(5)]

        # No exception means success was logged

    @pytest.mark.asyncio
    async def test_audit_context_failure(self, audit_logger, user_context):
        """Test audit context for failed operation."""
        with pytest.raises(ValueError):
            async with AuditContext(
                audit_logger=audit_logger,
                user=user_context,
                tool_name="failing_tool",
            ):
                raise ValueError("Operation failed")

        # Failure should have been logged

    @pytest.mark.asyncio
    async def test_audit_context_with_crq(self, audit_logger, user_context):
        """Test audit context with CRQ."""
        async with AuditContext(
            audit_logger=audit_logger,
            user=user_context,
            tool_name="delete_machine",
            level=AuditLevel.PRIVILEGED,
            crq_id="CRQ123456789",
        ):
            pass


# =============================================================================
# Event Cache Tests
# =============================================================================


class TestAuditLoggerCache:
    """Tests for event cache management."""

    @pytest.mark.asyncio
    async def test_clear_event_cache(self, audit_logger, user_context):
        """Test clearing the event cache."""
        # Add some events to cache
        await audit_logger.log_tool_start(
            user=user_context,
            tool_name="tool1",
        )
        await audit_logger.log_tool_start(
            user=user_context,
            tool_name="tool2",
        )

        assert len(audit_logger._event_cache) >= 2

        audit_logger.clear_event_cache()

        assert len(audit_logger._event_cache) == 0
        assert len(audit_logger._event_order) == 0

    @pytest.mark.asyncio
    async def test_cache_eviction(self, settings, user_context):
        """Test automatic cache eviction when max size is exceeded."""
        # Create logger with small cache size
        small_cache_logger = AuditLogger(settings, max_cache_size=10)

        # Add more events than cache can hold
        for i in range(15):
            await small_cache_logger.log_tool_start(
                user=user_context,
                tool_name=f"tool_{i}",
            )

        # Cache should not exceed max size (plus 10% eviction buffer)
        assert small_cache_logger.cache_size <= 10

    @pytest.mark.asyncio
    async def test_cache_preserves_recent_events(self, settings, user_context):
        """Test that cache eviction preserves the most recent events."""
        # Create logger with small cache size
        small_cache_logger = AuditLogger(settings, max_cache_size=5)

        # Log events and track their IDs
        event_ids = []
        for i in range(10):
            event_id = await small_cache_logger.log_tool_start(
                user=user_context,
                tool_name=f"tool_{i}",
            )
            event_ids.append(event_id)

        # The most recent events should still be in cache
        # (last 5 minus any eviction buffer)
        cached_count = 0
        for event_id in event_ids[-5:]:
            if event_id in small_cache_logger._event_cache:
                cached_count += 1

        # At least some recent events should be in cache
        assert cached_count >= 2

    def test_max_cache_size_property(self, settings):
        """Test max_cache_size property."""
        logger = AuditLogger(settings, max_cache_size=500)
        assert logger.max_cache_size == 500

    def test_default_max_cache_size(self, settings):
        """Test default max cache size."""
        logger = AuditLogger(settings)
        assert logger.max_cache_size == AuditLogger.DEFAULT_MAX_CACHE_SIZE

    def test_cache_size_property(self, settings):
        """Test cache_size property."""
        logger = AuditLogger(settings)
        assert logger.cache_size == 0

    @pytest.mark.asyncio
    async def test_event_update_does_not_duplicate(self, audit_logger, user_context):
        """Test that updating an event doesn't add a duplicate to the cache."""
        event_id = await audit_logger.log_tool_start(
            user=user_context,
            tool_name="test_tool",
        )

        initial_cache_size = audit_logger.cache_size

        # Log success for the same event
        await audit_logger.log_tool_success(event_id=event_id)

        # Cache size should be the same (event updated, not duplicated)
        assert audit_logger.cache_size == initial_cache_size


# =============================================================================
# Log Rotation Tests
# =============================================================================


class TestAuditLogRotator:
    """Tests for AuditLogRotator class."""

    @pytest.fixture
    def rotator(self, tmp_path):
        """Create a test rotator."""
        from mosk_mcp.observability.audit import AuditLogRotator

        log_path = tmp_path / "audit.log"
        return AuditLogRotator(
            log_path=log_path,
            max_size_mb=1,  # 1MB for easy testing
            backup_count=3,
            rotation_when="midnight",
            compress=False,  # Disable compression for easier testing
        )

    def test_rotator_initialization(self, rotator):
        """Test rotator initialization."""
        assert rotator.max_size_bytes == 1 * 1024 * 1024
        assert rotator.backup_count == 3
        assert rotator.rotation_when == "midnight"
        assert rotator.compress is False

    def test_should_rotate_no_file(self, rotator):
        """Test should_rotate returns False when file doesn't exist."""
        assert rotator.should_rotate() is False

    def test_should_rotate_by_size_small_file(self, rotator):
        """Test should_rotate returns False for small files."""
        # Create a small file
        rotator.log_path.parent.mkdir(parents=True, exist_ok=True)
        rotator.log_path.write_text("small content\n")

        assert rotator._should_rotate_by_size() is False

    def test_should_rotate_by_size_large_file(self, tmp_path):
        """Test should_rotate returns True for files exceeding max size."""
        from mosk_mcp.observability.audit import AuditLogRotator

        log_path = tmp_path / "audit.log"
        # Create rotator with tiny size limit
        rotator = AuditLogRotator(
            log_path=log_path,
            max_size_mb=1,  # Will be interpreted as 1MB, but we'll use bytes directly
            backup_count=3,
        )
        # Override to 100 bytes for testing
        rotator.max_size_bytes = 100

        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("x" * 200)  # 200 bytes > 100 bytes limit

        assert rotator._should_rotate_by_size() is True

    @pytest.mark.asyncio
    async def test_rotate_creates_backup(self, rotator):
        """Test rotation creates backup file."""
        # Create log file with content
        rotator.log_path.parent.mkdir(parents=True, exist_ok=True)
        rotator.log_path.write_text("test log content\n")

        # Perform rotation
        result = await rotator.rotate()

        assert result is True
        # Original file should be gone (moved to backup)
        assert not rotator.log_path.exists()
        # Backup should exist
        backup_files = rotator.get_backup_files()
        assert len(backup_files) == 1

    @pytest.mark.asyncio
    async def test_rotate_compresses_backup(self, tmp_path):
        """Test rotation compresses backup when enabled."""
        from mosk_mcp.observability.audit import AuditLogRotator

        log_path = tmp_path / "audit.log"
        rotator = AuditLogRotator(
            log_path=log_path,
            max_size_mb=1,
            backup_count=3,
            compress=True,
        )

        # Create log file with content
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("test log content for compression\n" * 100)

        # Perform rotation
        result = await rotator.rotate()

        assert result is True
        # Backup should be compressed
        backup_files = rotator.get_backup_files()
        assert len(backup_files) == 1
        assert backup_files[0].suffix == ".gz"

    @pytest.mark.asyncio
    async def test_rotate_cleanup_old_backups(self, tmp_path):
        """Test rotation removes old backups exceeding backup_count."""
        import time

        from mosk_mcp.observability.audit import AuditLogRotator

        log_path = tmp_path / "audit.log"
        rotator = AuditLogRotator(
            log_path=log_path,
            max_size_mb=1,
            backup_count=2,  # Keep only 2 backups
            compress=False,
        )

        log_path.parent.mkdir(parents=True, exist_ok=True)

        # Create multiple rotations
        for i in range(4):
            log_path.write_text(f"log content {i}\n")
            await rotator.rotate()
            time.sleep(0.01)  # Small delay to ensure unique timestamps

        # Should only have 2 backups
        backup_files = rotator.get_backup_files()
        assert len(backup_files) <= 2

    def test_get_backup_files_empty(self, rotator):
        """Test get_backup_files returns empty list when no backups."""
        # Ensure directory exists but is empty
        rotator.log_path.parent.mkdir(parents=True, exist_ok=True)
        assert rotator.get_backup_files() == []

    @pytest.mark.asyncio
    async def test_rotate_no_file(self, rotator):
        """Test rotation returns False when no file to rotate."""
        result = await rotator.rotate()
        assert result is False


class TestAuditLoggerRotation:
    """Tests for AuditLogger log rotation integration."""

    @pytest.fixture
    def rotation_settings(self, tmp_path):
        """Create settings with rotation enabled."""
        return Settings(
            audit_log_path=tmp_path / "audit.log",
            audit_enabled=True,
            audit_rotation_enabled=True,
            audit_max_size_mb=1,
            audit_backup_count=3,
            audit_rotation_when="midnight",
            auth_enabled=False,
            otel_enabled=False,
            log_format=LogFormat.CONSOLE,
            environment=Environment.DEVELOPMENT,
            log_to_stderr_only=False,  # Enable file writes for testing
        )

    @pytest.fixture
    def no_rotation_settings(self, tmp_path):
        """Create settings with rotation disabled."""
        return Settings(
            audit_log_path=tmp_path / "audit.log",
            audit_enabled=True,
            audit_rotation_enabled=False,
            auth_enabled=False,
            otel_enabled=False,
            log_format=LogFormat.CONSOLE,
            environment=Environment.DEVELOPMENT,
        )

    def test_rotation_enabled_property(self, rotation_settings):
        """Test rotation_enabled property."""
        logger = AuditLogger(rotation_settings)
        assert logger.rotation_enabled is True
        assert logger.rotator is not None

    def test_rotation_disabled_property(self, no_rotation_settings):
        """Test rotation disabled when configured."""
        logger = AuditLogger(no_rotation_settings)
        assert logger.rotation_enabled is False
        assert logger.rotator is None

    @pytest.mark.asyncio
    async def test_manual_rotate_logs(self, rotation_settings, user_context):
        """Test manual log rotation."""
        logger = AuditLogger(rotation_settings)

        # Write some events
        for i in range(5):
            await logger.log_tool_start(
                user=user_context,
                tool_name=f"tool_{i}",
            )

        # Manually rotate
        result = await logger.rotate_logs()

        assert result is True
        # Backup should exist
        backups = logger.get_backup_files()
        assert len(backups) == 1

    @pytest.mark.asyncio
    async def test_rotate_logs_disabled(self, no_rotation_settings):
        """Test rotate_logs returns False when rotation is disabled."""
        logger = AuditLogger(no_rotation_settings)
        result = await logger.rotate_logs()
        assert result is False

    def test_get_backup_files_no_rotator(self, no_rotation_settings):
        """Test get_backup_files returns empty when rotation disabled."""
        logger = AuditLogger(no_rotation_settings)
        assert logger.get_backup_files() == []

    @pytest.mark.asyncio
    async def test_auto_rotation_on_write(self, tmp_path, user_context):
        """Test automatic rotation when size limit exceeded."""
        log_path = tmp_path / "audit.log"
        settings = Settings(
            audit_log_path=log_path,
            audit_enabled=True,
            audit_rotation_enabled=True,
            audit_max_size_mb=1,  # Will be adjusted below
            audit_backup_count=3,
            auth_enabled=False,
            otel_enabled=False,
            log_format=LogFormat.CONSOLE,
            environment=Environment.DEVELOPMENT,
            log_to_stderr_only=False,  # Enable file writes for testing
        )

        logger = AuditLogger(settings)
        # Adjust rotator max size to a small value for testing
        if logger.rotator:
            logger.rotator.max_size_bytes = 500  # 500 bytes

        # Write enough events to exceed limit
        for i in range(20):
            await logger.log_tool_start(
                user=user_context,
                tool_name=f"tool_with_long_name_to_increase_size_{i}",
                parameters={"key": "value" * 10},
            )

        # Should have rotated at least once
        backups = logger.get_backup_files()
        assert len(backups) >= 1
