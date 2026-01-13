"""Role-Based Access Control (RBAC) enforcement for MOSK MCP Server.

This module provides RBAC enforcement including:
- Permission levels (READ, WRITE_NON_DESTRUCTIVE, WRITE_PRIVILEGED)
- Role to permissions mapping
- Decorators for tool permission enforcement
- Integration with UserContext
"""

from __future__ import annotations

import functools
from enum import Enum
from typing import TYPE_CHECKING, Any, ParamSpec, TypeVar

from mosk_mcp.auth.types import Permission, Role, UserContext
from mosk_mcp.core.exceptions import AuthorizationError
from mosk_mcp.observability.audit import AuditLevel, AuditLogger
from mosk_mcp.observability.logging import get_logger


if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


logger = get_logger(__name__)

P = ParamSpec("P")
R = TypeVar("R")


class ToolSafetyLevel(str, Enum):
    """Safety levels for tools.

    Attributes:
        READ_ONLY: Tools that only read data, no modifications.
        NON_DESTRUCTIVE: Tools that modify state but can be easily reversed.
        PRIVILEGED: Tools that perform destructive or high-impact operations.
    """

    READ_ONLY = "read_only"
    NON_DESTRUCTIVE = "non_destructive"
    PRIVILEGED = "privileged"

    def to_audit_level(self) -> AuditLevel:
        """Convert to corresponding audit level.

        Returns:
            Corresponding AuditLevel.
        """
        mapping = {
            ToolSafetyLevel.READ_ONLY: AuditLevel.READ,
            ToolSafetyLevel.NON_DESTRUCTIVE: AuditLevel.WRITE,
            ToolSafetyLevel.PRIVILEGED: AuditLevel.PRIVILEGED,
        }
        return mapping[self]


# Role to safety level permissions
ROLE_SAFETY_PERMISSIONS: dict[Role, set[ToolSafetyLevel]] = {
    Role.VIEWER: {
        ToolSafetyLevel.READ_ONLY,
    },
    Role.OPERATOR: {
        ToolSafetyLevel.READ_ONLY,
        ToolSafetyLevel.NON_DESTRUCTIVE,
    },
    Role.ADMINISTRATOR: {
        ToolSafetyLevel.READ_ONLY,
        ToolSafetyLevel.NON_DESTRUCTIVE,
        ToolSafetyLevel.PRIVILEGED,
    },
}


class RBACEnforcer:
    """RBAC enforcement for MOSK MCP Server.

    This class provides methods for checking permissions and enforcing
    access control based on user roles and tool safety levels.

    Attributes:
        _audit_logger: Optional audit logger for logging authorization events.

    Example:
        enforcer = RBACEnforcer(audit_logger)

        # Check permission
        if enforcer.can_execute(context, ToolSafetyLevel.PRIVILEGED):
            await perform_operation()

        # Or require permission (raises on failure)
        enforcer.require_permission(context, Permission.WRITE_MACHINES)
    """

    def __init__(self, audit_logger: AuditLogger | None = None) -> None:
        """Initialize the RBAC enforcer.

        Args:
            audit_logger: Optional audit logger for logging events.
        """
        self._audit_logger = audit_logger

    def can_execute_safety_level(
        self,
        context: UserContext,
        safety_level: ToolSafetyLevel,
    ) -> bool:
        """Check if user can execute tools at the given safety level.

        Args:
            context: User context.
            safety_level: Tool safety level.

        Returns:
            True if user has permission.
        """
        allowed_levels = ROLE_SAFETY_PERMISSIONS.get(context.role, set())
        return safety_level in allowed_levels

    def can_execute_permission(
        self,
        context: UserContext,
        permission: Permission,
    ) -> bool:
        """Check if user has a specific permission.

        Args:
            context: User context.
            permission: Required permission.

        Returns:
            True if user has permission.
        """
        return context.has_permission(permission)

    def require_safety_level(
        self,
        context: UserContext,
        safety_level: ToolSafetyLevel,
        tool_name: str | None = None,
    ) -> None:
        """Require user to have permission for a safety level.

        Args:
            context: User context.
            safety_level: Required safety level.
            tool_name: Optional tool name for error messages.

        Raises:
            AuthorizationError: If user lacks permission.
        """
        if not self.can_execute_safety_level(context, safety_level):
            tool_desc = f" for tool '{tool_name}'" if tool_name else ""
            raise AuthorizationError(
                message=f"Permission denied: {safety_level.value} access required{tool_desc}",
                required_permission=safety_level.value,
                user=context.username,
            )

    def require_permission(
        self,
        context: UserContext,
        permission: Permission,
        resource: str | None = None,
    ) -> None:
        """Require user to have a specific permission.

        Args:
            context: User context.
            permission: Required permission.
            resource: Optional resource for error messages.

        Raises:
            AuthorizationError: If user lacks permission.
        """
        if not self.can_execute_permission(context, permission):
            raise AuthorizationError(
                message=f"Permission denied: {permission.value} required",
                required_permission=permission.value,
                user=context.username,
                resource=resource,
            )

    def require_any_permission(
        self,
        context: UserContext,
        permissions: list[Permission],
        resource: str | None = None,
    ) -> None:
        """Require user to have any one of the specified permissions.

        Args:
            context: User context.
            permissions: List of permissions (any one is sufficient).
            resource: Optional resource for error messages.

        Raises:
            AuthorizationError: If user lacks all permissions.
        """
        if not context.has_any_permission(*permissions):
            perm_str = ", ".join(p.value for p in permissions)
            raise AuthorizationError(
                message=f"Permission denied: one of [{perm_str}] required",
                required_permission=perm_str,
                user=context.username,
                resource=resource,
            )

    def require_all_permissions(
        self,
        context: UserContext,
        permissions: list[Permission],
        resource: str | None = None,
    ) -> None:
        """Require user to have all specified permissions.

        Args:
            context: User context.
            permissions: List of required permissions.
            resource: Optional resource for error messages.

        Raises:
            AuthorizationError: If user lacks any permission.
        """
        if not context.has_all_permissions(*permissions):
            missing = [p for p in permissions if not context.has_permission(p)]
            perm_str = ", ".join(p.value for p in missing)
            raise AuthorizationError(
                message=f"Permission denied: missing [{perm_str}]",
                required_permission=perm_str,
                user=context.username,
                resource=resource,
            )

    def require_role(
        self,
        context: UserContext,
        minimum_role: Role,
    ) -> None:
        """Require user to have at least the specified role.

        Args:
            context: User context.
            minimum_role: Minimum required role.

        Raises:
            AuthorizationError: If user's role is insufficient.
        """
        # Role hierarchy: VIEWER < OPERATOR < ADMINISTRATOR
        role_hierarchy = {
            Role.VIEWER: 0,
            Role.OPERATOR: 1,
            Role.ADMINISTRATOR: 2,
        }

        user_level = role_hierarchy.get(context.role, -1)
        required_level = role_hierarchy.get(minimum_role, 999)

        if user_level < required_level:
            raise AuthorizationError(
                message=f"Permission denied: {minimum_role.value} role or higher required",
                required_permission=f"role:{minimum_role.value}",
                user=context.username,
            )

    async def log_authorization_failure(
        self,
        context: UserContext,
        action: str,
        required: str,
        resource_type: str | None = None,
        resource_name: str | None = None,
    ) -> None:
        """Log an authorization failure to the audit log.

        Args:
            context: User context.
            action: Action that was denied.
            required: Required permission.
            resource_type: Resource type.
            resource_name: Resource name.
        """
        if self._audit_logger:
            await self._audit_logger.log_authorization_denied(
                user=context,
                action=action,
                required_permission=required,
                resource_type=resource_type,
                resource_name=resource_name,
            )


# Singleton enforcer instance
_enforcer: RBACEnforcer | None = None


def get_enforcer(audit_logger: AuditLogger | None = None) -> RBACEnforcer:
    """Get the RBAC enforcer singleton.

    Args:
        audit_logger: Optional audit logger for initialization.

    Returns:
        RBACEnforcer instance.
    """
    global _enforcer
    if _enforcer is None:
        _enforcer = RBACEnforcer(audit_logger)
    return _enforcer


def set_enforcer(enforcer: RBACEnforcer) -> None:
    """Set the RBAC enforcer singleton.

    Args:
        enforcer: RBACEnforcer instance to use.
    """
    global _enforcer
    _enforcer = enforcer


# =========================================================================
# Decorators for Permission Enforcement
# =========================================================================


def require_safety_level(
    safety_level: ToolSafetyLevel,
) -> Callable[[Callable[P, Awaitable[R]]], Callable[P, Awaitable[R]]]:
    """Decorator to require a safety level for a tool function.

    The decorated function must accept a UserContext as its first argument
    or have a 'context' keyword argument.

    Args:
        safety_level: Required safety level.

    Returns:
        Decorator function.

    Example:
        @require_safety_level(ToolSafetyLevel.PRIVILEGED)
        async def delete_machine(context: UserContext, name: str) -> None:
            ...
    """

    def decorator(func: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
        @functools.wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            # Extract context from args or kwargs
            context = _extract_context(args, kwargs)
            if context is None:
                raise ValueError(f"Function {func.__name__} requires UserContext argument")

            enforcer = get_enforcer()
            enforcer.require_safety_level(context, safety_level, func.__name__)

            return await func(*args, **kwargs)

        return wrapper

    return decorator


def require_permission_decorator(
    permission: Permission,
) -> Callable[[Callable[P, Awaitable[R]]], Callable[P, Awaitable[R]]]:
    """Decorator to require a specific permission.

    The decorated function must accept a UserContext as its first argument
    or have a 'context' keyword argument.

    Args:
        permission: Required permission.

    Returns:
        Decorator function.

    Example:
        @require_permission_decorator(Permission.WRITE_MACHINES)
        async def create_machine(context: UserContext, spec: dict) -> dict:
            ...
    """

    def decorator(func: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
        @functools.wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            context = _extract_context(args, kwargs)
            if context is None:
                raise ValueError(f"Function {func.__name__} requires UserContext argument")

            enforcer = get_enforcer()
            enforcer.require_permission(context, permission)

            return await func(*args, **kwargs)

        return wrapper

    return decorator


def require_role_decorator(
    minimum_role: Role,
) -> Callable[[Callable[P, Awaitable[R]]], Callable[P, Awaitable[R]]]:
    """Decorator to require a minimum role.

    The decorated function must accept a UserContext as its first argument
    or have a 'context' keyword argument.

    Args:
        minimum_role: Minimum required role.

    Returns:
        Decorator function.

    Example:
        @require_role_decorator(Role.ADMINISTRATOR)
        async def admin_operation(context: UserContext) -> None:
            ...
    """

    def decorator(func: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
        @functools.wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            context = _extract_context(args, kwargs)
            if context is None:
                raise ValueError(f"Function {func.__name__} requires UserContext argument")

            enforcer = get_enforcer()
            enforcer.require_role(context, minimum_role)

            return await func(*args, **kwargs)

        return wrapper

    return decorator


def _extract_context(
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> UserContext | None:
    """Extract UserContext from function arguments.

    Args:
        args: Positional arguments.
        kwargs: Keyword arguments.

    Returns:
        UserContext if found, None otherwise.
    """
    # Check kwargs first - support both 'context' and 'user_context' parameter names
    for key in ("context", "user_context"):
        if key in kwargs:
            ctx = kwargs[key]
            if isinstance(ctx, UserContext):
                return ctx

    # Check first positional argument
    if args and isinstance(args[0], UserContext):
        return args[0]

    # Check all positional args
    for arg in args:
        if isinstance(arg, UserContext):
            return arg

    return None


# =========================================================================
# Authentication Enforcement Decorator
# =========================================================================


def require_authentication(
    func: Callable[P, Awaitable[R]] | None = None,
    *,
    allow_anonymous_for_read_only: bool = False,
) -> Callable[[Callable[P, Awaitable[R]]], Callable[P, Awaitable[R]]] | Callable[P, Awaitable[R]]:
    """Decorator to require authentication for a tool function.

    SECURITY: This decorator enforces that a valid UserContext is provided
    for privileged operations. For read-only operations, anonymous access
    may be allowed if allow_anonymous_for_read_only=True.

    The decorated function must have a 'context' keyword argument.

    Args:
        func: Function to decorate (used when decorator is applied without parens).
        allow_anonymous_for_read_only: If True, allow anonymous access for read-only ops.

    Returns:
        Decorated function that enforces authentication.

    Raises:
        AuthenticationError: If context is None and anonymous access is not allowed.

    Example:
        @require_authentication
        async def delete_machine(context: UserContext, name: str) -> None:
            ...

        @require_authentication(allow_anonymous_for_read_only=True)
        async def list_machines(context: UserContext | None = None) -> list:
            ...
    """
    from mosk_mcp.core.exceptions import AuthenticationError

    def decorator(fn: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
        @functools.wraps(fn)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            context = _extract_context(args, kwargs)

            if context is None:
                if allow_anonymous_for_read_only:
                    # Create a minimal read-only context for anonymous access
                    logger.warning(
                        "anonymous_access",
                        function=fn.__name__,
                        message="Allowing anonymous read-only access",
                    )
                else:
                    logger.error(
                        "authentication_required",
                        function=fn.__name__,
                        message="Authentication is required for this operation",
                    )
                    raise AuthenticationError(
                        message=f"Authentication required for {fn.__name__}. "
                        "Provide a valid UserContext.",
                        auth_method="none",
                    )

            return await fn(*args, **kwargs)

        return wrapper

    # Handle both @require_authentication and @require_authentication()
    if func is not None:
        return decorator(func)
    return decorator


def require_authenticated_context(
    safety_level: ToolSafetyLevel,
) -> Callable[[Callable[P, Awaitable[R]]], Callable[P, Awaitable[R]]]:
    """Combined decorator that requires authentication AND safety level.

    SECURITY: This is the recommended decorator for privileged tools.
    It enforces both authentication and authorization in a single decorator.

    Args:
        safety_level: Required safety level for the operation.

    Returns:
        Decorator function.

    Raises:
        AuthenticationError: If context is None.
        AuthorizationError: If user lacks required safety level.

    Example:
        @require_authenticated_context(ToolSafetyLevel.PRIVILEGED)
        async def delete_machine(context: UserContext, name: str) -> None:
            ...
    """
    from mosk_mcp.core.exceptions import AuthenticationError

    def decorator(func: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
        @functools.wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            context = _extract_context(args, kwargs)

            # SECURITY: Require authentication for all non-read-only operations
            if context is None:
                if safety_level == ToolSafetyLevel.READ_ONLY:
                    logger.debug(
                        "anonymous_read_access",
                        function=func.__name__,
                    )
                else:
                    logger.error(
                        "authentication_required",
                        function=func.__name__,
                        safety_level=safety_level.value,
                    )
                    raise AuthenticationError(
                        message=f"Authentication required for {func.__name__} "
                        f"(safety level: {safety_level.value})",
                        auth_method="none",
                    )
            else:
                # SECURITY: Enforce safety level authorization
                enforcer = get_enforcer()
                enforcer.require_safety_level(context, safety_level, func.__name__)

            return await func(*args, **kwargs)

        return wrapper

    return decorator


# =========================================================================
# Tool Definition Helper
# =========================================================================


class ToolDefinition:
    """Helper class for defining tools with RBAC metadata.

    This class provides a convenient way to define tools with their
    associated safety levels and permissions.

    Example:
        tool = ToolDefinition(
            name="delete_machine",
            safety_level=ToolSafetyLevel.PRIVILEGED,
            permissions=[Permission.WRITE_MACHINES],
            requires_crq=True,
            description="Delete a machine from the cluster",
        )

        if tool.can_execute(context):
            await tool.execute(context, machine_name="compute-01")
    """

    def __init__(
        self,
        name: str,
        safety_level: ToolSafetyLevel = ToolSafetyLevel.READ_ONLY,
        permissions: list[Permission] | None = None,
        requires_crq: bool = False,
        description: str = "",
    ) -> None:
        """Initialize tool definition.

        Args:
            name: Tool name.
            safety_level: Tool safety level.
            permissions: Required permissions (any one is sufficient).
            requires_crq: Whether tool requires a CRQ for execution.
            description: Tool description.
        """
        self.name = name
        self.safety_level = safety_level
        self.permissions = permissions or []
        self.requires_crq = requires_crq
        self.description = description

    def can_execute(self, context: UserContext) -> bool:
        """Check if user can execute this tool.

        Args:
            context: User context.

        Returns:
            True if user has permission.
        """
        enforcer = get_enforcer()

        # Check safety level
        if not enforcer.can_execute_safety_level(context, self.safety_level):
            return False

        # Check specific permissions if defined
        return not (self.permissions and not context.has_any_permission(*self.permissions))

    def require_execution(
        self,
        context: UserContext,
        crq_id: str | None = None,
    ) -> None:
        """Require permission to execute this tool.

        Args:
            context: User context.
            crq_id: Change request ID (required if requires_crq is True).

        Raises:
            AuthorizationError: If user lacks permission.
            ValidationError: If CRQ is required but not provided.
        """
        from mosk_mcp.core.exceptions import ValidationError

        enforcer = get_enforcer()

        # Check safety level
        enforcer.require_safety_level(context, self.safety_level, self.name)

        # Check specific permissions
        if self.permissions:
            enforcer.require_any_permission(context, self.permissions, self.name)

        # Check CRQ requirement
        if self.requires_crq and not crq_id:
            raise ValidationError(
                message=f"Tool '{self.name}' requires a valid CRQ ID",
                field="crq_id",
                constraint="required for privileged operations",
            )


# =========================================================================
# Tool Registry with RBAC
# =========================================================================


class ToolRegistry:
    """Registry for tools with RBAC enforcement.

    This registry maintains tool definitions and provides RBAC-aware
    tool discovery and execution.

    Example:
        registry = ToolRegistry()
        registry.register(ToolDefinition(
            name="list_machines",
            safety_level=ToolSafetyLevel.READ_ONLY,
        ))

        available_tools = registry.get_available_tools(context)
    """

    def __init__(self) -> None:
        """Initialize the tool registry."""
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, tool: ToolDefinition) -> None:
        """Register a tool.

        Args:
            tool: Tool definition.
        """
        self._tools[tool.name] = tool
        logger.debug("tool_registered", name=tool.name, safety_level=tool.safety_level.value)

    def get(self, name: str) -> ToolDefinition | None:
        """Get a tool by name.

        Args:
            name: Tool name.

        Returns:
            Tool definition or None.
        """
        return self._tools.get(name)

    def get_all(self) -> list[ToolDefinition]:
        """Get all registered tools.

        Returns:
            List of all tool definitions.
        """
        return list(self._tools.values())

    def get_available_tools(self, context: UserContext) -> list[ToolDefinition]:
        """Get tools available to a user.

        Args:
            context: User context.

        Returns:
            List of tools the user can execute.
        """
        return [tool for tool in self._tools.values() if tool.can_execute(context)]

    def get_tools_by_safety_level(
        self,
        safety_level: ToolSafetyLevel,
    ) -> list[ToolDefinition]:
        """Get tools at a specific safety level.

        Args:
            safety_level: Safety level to filter by.

        Returns:
            List of matching tools.
        """
        return [tool for tool in self._tools.values() if tool.safety_level == safety_level]

    def can_execute(
        self,
        tool_name: str,
        context: UserContext,
    ) -> bool:
        """Check if user can execute a tool.

        Args:
            tool_name: Tool name.
            context: User context.

        Returns:
            True if user can execute the tool.
        """
        tool = self._tools.get(tool_name)
        if tool is None:
            return False
        return tool.can_execute(context)
