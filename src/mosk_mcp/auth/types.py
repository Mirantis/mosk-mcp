"""Authentication types for MOSK MCP Server.

This module provides core authentication types used throughout the system:
- Role: User roles for RBAC (VIEWER, OPERATOR, ADMINISTRATOR)
- Permission: Fine-grained permissions for access control
- UserContext: Authenticated user context with permissions
- ROLE_PERMISSIONS: Mapping of roles to their granted permissions
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import TYPE_CHECKING

from mosk_mcp.core.exceptions import AuthorizationError


if TYPE_CHECKING:
    from datetime import datetime


class Role(str, Enum):
    """User roles for role-based access control.

    Roles are hierarchical:
    - VIEWER: Read-only access
    - OPERATOR: Can perform non-destructive operations
    - ADMINISTRATOR: Full access including destructive operations
    """

    VIEWER = "viewer"
    OPERATOR = "operator"
    ADMINISTRATOR = "administrator"

    @classmethod
    def from_string(cls, value: str) -> Role:
        """Convert string to Role enum.

        Args:
            value: Role string value.

        Returns:
            Corresponding Role enum value.

        Raises:
            ValueError: If value is not a valid role.
        """
        try:
            return cls(value.lower())
        except ValueError:
            valid_roles = [r.value for r in cls]
            raise ValueError(f"Invalid role '{value}'. Must be one of: {valid_roles}") from None


class Permission(str, Enum):
    """Permissions for fine-grained access control.

    Permissions map to specific capabilities in the system.
    """

    # Read permissions
    READ_MACHINES = "read:machines"
    READ_OSDPL = "read:osdpl"
    READ_CEPH = "read:ceph"
    READ_LOGS = "read:logs"
    READ_HEALTH = "read:health"

    # Write permissions
    WRITE_MACHINES = "write:machines"
    WRITE_OSDPL = "write:osdpl"

    # Execute permissions
    EXECUTE_MAINTENANCE = "execute:maintenance"
    EXECUTE_CEPH_OPS = "execute:ceph_ops"

    # Admin permissions
    ADMIN_CLUSTER = "admin:cluster"
    ADMIN_USERS = "admin:users"


# Role to permissions mapping
ROLE_PERMISSIONS: dict[Role, set[Permission]] = {
    Role.VIEWER: {
        Permission.READ_MACHINES,
        Permission.READ_OSDPL,
        Permission.READ_CEPH,
        Permission.READ_LOGS,
        Permission.READ_HEALTH,
    },
    Role.OPERATOR: {
        Permission.READ_MACHINES,
        Permission.READ_OSDPL,
        Permission.READ_CEPH,
        Permission.READ_LOGS,
        Permission.READ_HEALTH,
        Permission.WRITE_MACHINES,
        Permission.WRITE_OSDPL,
        Permission.EXECUTE_MAINTENANCE,
        Permission.EXECUTE_CEPH_OPS,
    },
    Role.ADMINISTRATOR: {
        Permission.READ_MACHINES,
        Permission.READ_OSDPL,
        Permission.READ_CEPH,
        Permission.READ_LOGS,
        Permission.READ_HEALTH,
        Permission.WRITE_MACHINES,
        Permission.WRITE_OSDPL,
        Permission.EXECUTE_MAINTENANCE,
        Permission.EXECUTE_CEPH_OPS,
        Permission.ADMIN_CLUSTER,
        Permission.ADMIN_USERS,
    },
}


def _empty_metadata() -> MappingProxyType[str, str]:
    """Create an empty immutable metadata mapping."""
    return MappingProxyType({})


@dataclass(frozen=True)
class UserContext:
    """Represents an authenticated user context.

    This class holds information about the authenticated user and their
    permissions. It is immutable to prevent accidental modification.

    Attributes:
        user_id: Unique identifier for the user.
        username: Human-readable username.
        role: User's role for RBAC.
        permissions: Set of granted permissions.
        authenticated_at: When authentication occurred.
        auth_method: Method used for authentication.
        metadata: Additional user metadata (immutable MappingProxyType).

    Note:
        The metadata field uses MappingProxyType to ensure true immutability
        even though the dataclass is frozen. A regular dict would still be
        mutable in a frozen dataclass.
    """

    user_id: str
    username: str
    role: Role
    permissions: frozenset[Permission]
    authenticated_at: datetime
    auth_method: str = "oidc"
    metadata: MappingProxyType[str, str] = field(default_factory=_empty_metadata)

    def has_permission(self, permission: Permission) -> bool:
        """Check if user has a specific permission.

        Args:
            permission: The permission to check.

        Returns:
            True if user has the permission, False otherwise.
        """
        return permission in self.permissions

    def has_any_permission(self, *permissions: Permission) -> bool:
        """Check if user has any of the specified permissions.

        Args:
            *permissions: Permissions to check.

        Returns:
            True if user has at least one permission, False otherwise.
        """
        return bool(self.permissions & set(permissions))

    def has_all_permissions(self, *permissions: Permission) -> bool:
        """Check if user has all of the specified permissions.

        Args:
            *permissions: Permissions to check.

        Returns:
            True if user has all permissions, False otherwise.
        """
        return set(permissions).issubset(self.permissions)

    def require_permission(self, permission: Permission) -> None:
        """Require user to have a specific permission.

        Args:
            permission: The required permission.

        Raises:
            AuthorizationError: If user lacks the required permission.
        """
        if not self.has_permission(permission):
            raise AuthorizationError(
                message=f"Permission denied: {permission.value} required",
                required_permission=permission.value,
                user=self.username,
            )

    def to_dict(self) -> dict[str, str | list[str]]:
        """Convert to dictionary for serialization.

        Returns:
            Dictionary representation of user context.
        """
        return {
            "user_id": self.user_id,
            "username": self.username,
            "role": self.role.value,
            "permissions": [p.value for p in self.permissions],
            "authenticated_at": self.authenticated_at.isoformat(),
            "auth_method": self.auth_method,
        }
