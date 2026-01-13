"""Tests for authentication module."""

from datetime import UTC, datetime

import pytest

from mosk_mcp.auth.types import (
    ROLE_PERMISSIONS,
    Permission,
    Role,
    UserContext,
)
from mosk_mcp.core.exceptions import AuthorizationError


class TestRole:
    """Tests for Role enum."""

    def test_role_values(self) -> None:
        """Test role enum values."""
        assert Role.VIEWER.value == "viewer"
        assert Role.OPERATOR.value == "operator"
        assert Role.ADMINISTRATOR.value == "administrator"

    def test_from_string(self) -> None:
        """Test role creation from string."""
        assert Role.from_string("viewer") == Role.VIEWER
        assert Role.from_string("OPERATOR") == Role.OPERATOR
        assert Role.from_string("Administrator") == Role.ADMINISTRATOR

    def test_from_string_invalid(self) -> None:
        """Test invalid role string."""
        with pytest.raises(ValueError, match="Invalid role"):
            Role.from_string("superuser")


class TestPermission:
    """Tests for Permission enum."""

    def test_permission_categories(self) -> None:
        """Test permission value formats."""
        # All permissions should follow pattern: action:resource
        for perm in Permission:
            parts = perm.value.split(":")
            assert len(parts) == 2
            assert parts[0] in ["read", "write", "execute", "admin"]


class TestRolePermissions:
    """Tests for role-permission mapping."""

    def test_viewer_has_read_only(self) -> None:
        """Test that viewer role has only read permissions."""
        viewer_perms = ROLE_PERMISSIONS[Role.VIEWER]

        for perm in viewer_perms:
            assert perm.value.startswith("read:")

    def test_operator_has_viewer_permissions(self) -> None:
        """Test that operator includes all viewer permissions."""
        viewer_perms = ROLE_PERMISSIONS[Role.VIEWER]
        operator_perms = ROLE_PERMISSIONS[Role.OPERATOR]

        assert viewer_perms.issubset(operator_perms)

    def test_admin_has_all_permissions(self) -> None:
        """Test that admin has all permissions."""
        admin_perms = ROLE_PERMISSIONS[Role.ADMINISTRATOR]
        operator_perms = ROLE_PERMISSIONS[Role.OPERATOR]

        assert operator_perms.issubset(admin_perms)
        assert Permission.ADMIN_CLUSTER in admin_perms
        assert Permission.ADMIN_USERS in admin_perms


class TestUserContext:
    """Tests for UserContext."""

    @pytest.fixture
    def context(self) -> UserContext:
        """Create a test user context."""
        return UserContext(
            user_id="test-001",
            username="test-user",
            role=Role.OPERATOR,
            permissions=frozenset(ROLE_PERMISSIONS[Role.OPERATOR]),
            authenticated_at=datetime.now(UTC),
            auth_method="oidc",
        )

    def test_has_permission(self, context: UserContext) -> None:
        """Test permission checking."""
        assert context.has_permission(Permission.READ_MACHINES) is True
        assert context.has_permission(Permission.ADMIN_CLUSTER) is False

    def test_has_any_permission(self, context: UserContext) -> None:
        """Test any-of permission checking."""
        assert (
            context.has_any_permission(
                Permission.READ_MACHINES,
                Permission.ADMIN_CLUSTER,
            )
            is True
        )

        assert (
            context.has_any_permission(
                Permission.ADMIN_CLUSTER,
                Permission.ADMIN_USERS,
            )
            is False
        )

    def test_has_all_permissions(self, context: UserContext) -> None:
        """Test all-of permission checking."""
        assert (
            context.has_all_permissions(
                Permission.READ_MACHINES,
                Permission.WRITE_MACHINES,
            )
            is True
        )

        assert (
            context.has_all_permissions(
                Permission.READ_MACHINES,
                Permission.ADMIN_CLUSTER,
            )
            is False
        )

    def test_require_permission_success(self, context: UserContext) -> None:
        """Test require_permission with valid permission."""
        # Should not raise
        context.require_permission(Permission.READ_MACHINES)

    def test_require_permission_failure(self, context: UserContext) -> None:
        """Test require_permission with missing permission."""
        with pytest.raises(AuthorizationError) as exc_info:
            context.require_permission(Permission.ADMIN_CLUSTER)

        assert "admin:cluster" in str(exc_info.value)
        assert exc_info.value.user == "test-user"

    def test_to_dict(self, context: UserContext) -> None:
        """Test conversion to dictionary."""
        result = context.to_dict()

        assert result["user_id"] == "test-001"
        assert result["username"] == "test-user"
        assert result["role"] == "operator"
        assert "read:machines" in result["permissions"]

    def test_immutability(self, context: UserContext) -> None:
        """Test that UserContext is immutable."""
        with pytest.raises(AttributeError):
            context.username = "new-user"  # type: ignore[misc]
