"""Pytest fixtures for tools tests.

This module provides common fixtures for testing MCP tools
which require authentication context due to security enforcement.
"""

from datetime import UTC, datetime

import pytest

from mosk_mcp.auth.types import Permission, Role, UserContext


@pytest.fixture
def admin_context() -> UserContext:
    """Create an administrator user context for testing privileged operations.

    Tools like apply_ceph_operation require authentication and are decorated
    with @require_authenticated_context.

    This fixture provides a valid admin context for testing these operations.
    """
    return UserContext(
        user_id="test-admin-001",
        username="test-admin",
        role=Role.ADMINISTRATOR,
        permissions=frozenset(Permission),
        authenticated_at=datetime.now(UTC),
        auth_method="test",
    )


@pytest.fixture
def operator_context() -> UserContext:
    """Create an operator user context for testing non-destructive operations.

    Operators can perform non-destructive operations but NOT privileged
    operations.
    """
    return UserContext(
        user_id="test-operator-001",
        username="test-operator",
        role=Role.OPERATOR,
        permissions=frozenset(
            [
                Permission.READ_MACHINES,
                Permission.READ_OSDPL,
                Permission.READ_CEPH,
                Permission.READ_LOGS,
                Permission.READ_HEALTH,
                Permission.WRITE_MACHINES,
                Permission.WRITE_OSDPL,
                Permission.EXECUTE_MAINTENANCE,
                Permission.EXECUTE_CEPH_OPS,
            ]
        ),
        authenticated_at=datetime.now(UTC),
        auth_method="test",
    )


@pytest.fixture
def viewer_context() -> UserContext:
    """Create a viewer user context for testing read-only operations.

    Viewers should NOT be able to perform write or privileged operations.
    This fixture is useful for testing authorization failures.
    """
    return UserContext(
        user_id="test-viewer-001",
        username="test-viewer",
        role=Role.VIEWER,
        permissions=frozenset(
            [
                Permission.READ_MACHINES,
                Permission.READ_OSDPL,
                Permission.READ_CEPH,
                Permission.READ_LOGS,
                Permission.READ_HEALTH,
            ]
        ),
        authenticated_at=datetime.now(UTC),
        auth_method="test",
    )
