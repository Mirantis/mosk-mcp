"""Tests for the RBAC enforcement module.

This module tests the RBACEnforcer class, decorators, and ToolRegistry.
"""

from datetime import UTC, datetime

import pytest

from mosk_mcp.auth.rbac import (
    ROLE_SAFETY_PERMISSIONS,
    RBACEnforcer,
    ToolDefinition,
    ToolRegistry,
    ToolSafetyLevel,
    get_enforcer,
    require_permission_decorator,
    require_role_decorator,
    require_safety_level,
    set_enforcer,
)
from mosk_mcp.auth.types import Permission, Role, UserContext
from mosk_mcp.core.exceptions import AuthorizationError


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def viewer_context():
    """Create a viewer user context."""
    return UserContext(
        user_id="viewer-001",
        username="viewer",
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


@pytest.fixture
def operator_context():
    """Create an operator user context."""
    return UserContext(
        user_id="operator-001",
        username="operator",
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
def admin_context():
    """Create an admin user context."""
    return UserContext(
        user_id="admin-001",
        username="admin",
        role=Role.ADMINISTRATOR,
        permissions=frozenset(Permission),
        authenticated_at=datetime.now(UTC),
        auth_method="test",
    )


@pytest.fixture
def enforcer():
    """Create an RBAC enforcer."""
    return RBACEnforcer()


@pytest.fixture(autouse=True)
def reset_singleton():
    """Reset singleton enforcer between tests."""
    set_enforcer(RBACEnforcer())
    yield
    set_enforcer(RBACEnforcer())


# =============================================================================
# Safety Level Tests
# =============================================================================


class TestToolSafetyLevel:
    """Tests for ToolSafetyLevel enum."""

    def test_safety_level_values(self):
        """Test safety level enum values."""
        assert ToolSafetyLevel.READ_ONLY.value == "read_only"
        assert ToolSafetyLevel.NON_DESTRUCTIVE.value == "non_destructive"
        assert ToolSafetyLevel.PRIVILEGED.value == "privileged"

    def test_to_audit_level(self):
        """Test conversion to audit level."""
        from mosk_mcp.observability.audit import AuditLevel

        assert ToolSafetyLevel.READ_ONLY.to_audit_level() == AuditLevel.READ
        assert ToolSafetyLevel.NON_DESTRUCTIVE.to_audit_level() == AuditLevel.WRITE
        assert ToolSafetyLevel.PRIVILEGED.to_audit_level() == AuditLevel.PRIVILEGED


class TestRoleSafetyPermissions:
    """Tests for role to safety level mappings."""

    def test_viewer_safety_levels(self):
        """Test viewer can only use read-only tools."""
        assert ToolSafetyLevel.READ_ONLY in ROLE_SAFETY_PERMISSIONS[Role.VIEWER]
        assert ToolSafetyLevel.NON_DESTRUCTIVE not in ROLE_SAFETY_PERMISSIONS[Role.VIEWER]
        assert ToolSafetyLevel.PRIVILEGED not in ROLE_SAFETY_PERMISSIONS[Role.VIEWER]

    def test_operator_safety_levels(self):
        """Test operator can use read-only and non-destructive tools."""
        assert ToolSafetyLevel.READ_ONLY in ROLE_SAFETY_PERMISSIONS[Role.OPERATOR]
        assert ToolSafetyLevel.NON_DESTRUCTIVE in ROLE_SAFETY_PERMISSIONS[Role.OPERATOR]
        assert ToolSafetyLevel.PRIVILEGED not in ROLE_SAFETY_PERMISSIONS[Role.OPERATOR]

    def test_admin_safety_levels(self):
        """Test admin can use all tools."""
        assert ToolSafetyLevel.READ_ONLY in ROLE_SAFETY_PERMISSIONS[Role.ADMINISTRATOR]
        assert ToolSafetyLevel.NON_DESTRUCTIVE in ROLE_SAFETY_PERMISSIONS[Role.ADMINISTRATOR]
        assert ToolSafetyLevel.PRIVILEGED in ROLE_SAFETY_PERMISSIONS[Role.ADMINISTRATOR]


# =============================================================================
# RBACEnforcer Tests
# =============================================================================


class TestRBACEnforcer:
    """Tests for RBACEnforcer class."""

    def test_can_execute_safety_level_viewer(self, enforcer, viewer_context):
        """Test viewer safety level checks."""
        assert enforcer.can_execute_safety_level(viewer_context, ToolSafetyLevel.READ_ONLY)
        assert not enforcer.can_execute_safety_level(
            viewer_context, ToolSafetyLevel.NON_DESTRUCTIVE
        )
        assert not enforcer.can_execute_safety_level(viewer_context, ToolSafetyLevel.PRIVILEGED)

    def test_can_execute_safety_level_operator(self, enforcer, operator_context):
        """Test operator safety level checks."""
        assert enforcer.can_execute_safety_level(operator_context, ToolSafetyLevel.READ_ONLY)
        assert enforcer.can_execute_safety_level(operator_context, ToolSafetyLevel.NON_DESTRUCTIVE)
        assert not enforcer.can_execute_safety_level(operator_context, ToolSafetyLevel.PRIVILEGED)

    def test_can_execute_safety_level_admin(self, enforcer, admin_context):
        """Test admin safety level checks."""
        assert enforcer.can_execute_safety_level(admin_context, ToolSafetyLevel.READ_ONLY)
        assert enforcer.can_execute_safety_level(admin_context, ToolSafetyLevel.NON_DESTRUCTIVE)
        assert enforcer.can_execute_safety_level(admin_context, ToolSafetyLevel.PRIVILEGED)

    def test_can_execute_permission(self, enforcer, operator_context):
        """Test permission check."""
        assert enforcer.can_execute_permission(operator_context, Permission.READ_MACHINES)
        assert enforcer.can_execute_permission(operator_context, Permission.WRITE_MACHINES)
        assert not enforcer.can_execute_permission(operator_context, Permission.ADMIN_CLUSTER)

    def test_require_safety_level_success(self, enforcer, operator_context):
        """Test require_safety_level succeeds when permitted."""
        # Should not raise
        enforcer.require_safety_level(operator_context, ToolSafetyLevel.READ_ONLY)
        enforcer.require_safety_level(operator_context, ToolSafetyLevel.NON_DESTRUCTIVE)

    def test_require_safety_level_failure(self, enforcer, viewer_context):
        """Test require_safety_level raises when not permitted."""
        with pytest.raises(AuthorizationError) as exc_info:
            enforcer.require_safety_level(viewer_context, ToolSafetyLevel.NON_DESTRUCTIVE)

        assert "non_destructive" in str(exc_info.value).lower()

    def test_require_permission_success(self, enforcer, operator_context):
        """Test require_permission succeeds when permitted."""
        enforcer.require_permission(operator_context, Permission.WRITE_MACHINES)

    def test_require_permission_failure(self, enforcer, viewer_context):
        """Test require_permission raises when not permitted."""
        with pytest.raises(AuthorizationError) as exc_info:
            enforcer.require_permission(viewer_context, Permission.WRITE_MACHINES)

        assert "write:machines" in str(exc_info.value).lower()

    def test_require_any_permission_success(self, enforcer, operator_context):
        """Test require_any_permission succeeds with any matching permission."""
        enforcer.require_any_permission(
            operator_context,
            [Permission.ADMIN_CLUSTER, Permission.WRITE_MACHINES],
        )

    def test_require_any_permission_failure(self, enforcer, viewer_context):
        """Test require_any_permission raises when no permissions match."""
        with pytest.raises(AuthorizationError):
            enforcer.require_any_permission(
                viewer_context,
                [Permission.WRITE_MACHINES, Permission.ADMIN_CLUSTER],
            )

    def test_require_all_permissions_success(self, enforcer, operator_context):
        """Test require_all_permissions succeeds when all match."""
        enforcer.require_all_permissions(
            operator_context,
            [Permission.READ_MACHINES, Permission.WRITE_MACHINES],
        )

    def test_require_all_permissions_failure(self, enforcer, operator_context):
        """Test require_all_permissions raises when any missing."""
        with pytest.raises(AuthorizationError) as exc_info:
            enforcer.require_all_permissions(
                operator_context,
                [Permission.WRITE_MACHINES, Permission.ADMIN_CLUSTER],
            )

        assert "admin:cluster" in str(exc_info.value).lower()

    def test_require_role_success(self, enforcer, admin_context):
        """Test require_role succeeds with sufficient role."""
        enforcer.require_role(admin_context, Role.VIEWER)
        enforcer.require_role(admin_context, Role.OPERATOR)
        enforcer.require_role(admin_context, Role.ADMINISTRATOR)

    def test_require_role_failure(self, enforcer, viewer_context):
        """Test require_role fails with insufficient role."""
        with pytest.raises(AuthorizationError) as exc_info:
            enforcer.require_role(viewer_context, Role.OPERATOR)

        assert "operator" in str(exc_info.value).lower()


# =============================================================================
# Singleton Tests
# =============================================================================


class TestRBACEnforcerSingleton:
    """Tests for enforcer singleton management."""

    def test_get_enforcer(self):
        """Test getting singleton enforcer."""
        enforcer1 = get_enforcer()
        enforcer2 = get_enforcer()

        assert enforcer1 is enforcer2

    def test_set_enforcer(self):
        """Test setting singleton enforcer."""
        custom_enforcer = RBACEnforcer()
        set_enforcer(custom_enforcer)

        assert get_enforcer() is custom_enforcer


# =============================================================================
# Decorator Tests
# =============================================================================


class TestRBACDecorators:
    """Tests for RBAC decorators."""

    @pytest.mark.asyncio
    async def test_require_safety_level_decorator_success(self, operator_context):
        """Test safety level decorator allows permitted access."""

        @require_safety_level(ToolSafetyLevel.NON_DESTRUCTIVE)
        async def create_machine(context: UserContext, name: str) -> dict:
            return {"name": name, "created": True}

        result = await create_machine(operator_context, "compute-01")

        assert result["name"] == "compute-01"
        assert result["created"]

    @pytest.mark.asyncio
    async def test_require_safety_level_decorator_failure(self, viewer_context):
        """Test safety level decorator blocks unpermitted access."""

        @require_safety_level(ToolSafetyLevel.NON_DESTRUCTIVE)
        async def create_machine(context: UserContext, name: str) -> dict:
            return {"name": name}

        with pytest.raises(AuthorizationError):
            await create_machine(viewer_context, "compute-01")

    @pytest.mark.asyncio
    async def test_require_permission_decorator_success(self, operator_context):
        """Test permission decorator allows permitted access."""

        @require_permission_decorator(Permission.WRITE_MACHINES)
        async def update_machine(context: UserContext, name: str) -> dict:
            return {"name": name, "updated": True}

        result = await update_machine(operator_context, "compute-01")

        assert result["updated"]

    @pytest.mark.asyncio
    async def test_require_permission_decorator_failure(self, viewer_context):
        """Test permission decorator blocks unpermitted access."""

        @require_permission_decorator(Permission.WRITE_MACHINES)
        async def update_machine(context: UserContext, name: str) -> dict:
            return {"name": name}

        with pytest.raises(AuthorizationError):
            await update_machine(viewer_context, "compute-01")

    @pytest.mark.asyncio
    async def test_require_role_decorator_success(self, admin_context):
        """Test role decorator allows permitted access."""

        @require_role_decorator(Role.ADMINISTRATOR)
        async def admin_action(context: UserContext) -> str:
            return "admin action completed"

        result = await admin_action(admin_context)

        assert result == "admin action completed"

    @pytest.mark.asyncio
    async def test_require_role_decorator_failure(self, operator_context):
        """Test role decorator blocks unpermitted access."""

        @require_role_decorator(Role.ADMINISTRATOR)
        async def admin_action(context: UserContext) -> str:
            return "admin action completed"

        with pytest.raises(AuthorizationError):
            await admin_action(operator_context)

    @pytest.mark.asyncio
    async def test_decorator_with_context_kwarg(self, operator_context):
        """Test decorator works with context as keyword argument."""

        @require_safety_level(ToolSafetyLevel.READ_ONLY)
        async def list_items(*, context: UserContext, limit: int = 10) -> list:
            return list(range(limit))

        result = await list_items(context=operator_context, limit=5)

        assert result == [0, 1, 2, 3, 4]


# =============================================================================
# ToolDefinition Tests
# =============================================================================


class TestToolDefinition:
    """Tests for ToolDefinition class."""

    def test_create_tool_definition(self):
        """Test creating a tool definition."""
        tool = ToolDefinition(
            name="list_machines",
            safety_level=ToolSafetyLevel.READ_ONLY,
            permissions=[Permission.READ_MACHINES],
            description="List all machines",
        )

        assert tool.name == "list_machines"
        assert tool.safety_level == ToolSafetyLevel.READ_ONLY
        assert not tool.requires_crq

    def test_can_execute_viewer(self, viewer_context):
        """Test viewer can execute read-only tool."""
        tool = ToolDefinition(
            name="list_machines",
            safety_level=ToolSafetyLevel.READ_ONLY,
            permissions=[Permission.READ_MACHINES],
        )

        assert tool.can_execute(viewer_context)

    def test_cannot_execute_viewer_write(self, viewer_context):
        """Test viewer cannot execute write tool."""
        tool = ToolDefinition(
            name="create_machine",
            safety_level=ToolSafetyLevel.NON_DESTRUCTIVE,
            permissions=[Permission.WRITE_MACHINES],
        )

        assert not tool.can_execute(viewer_context)

    def test_can_execute_operator_write(self, operator_context):
        """Test operator can execute write tool."""
        tool = ToolDefinition(
            name="create_machine",
            safety_level=ToolSafetyLevel.NON_DESTRUCTIVE,
            permissions=[Permission.WRITE_MACHINES],
        )

        assert tool.can_execute(operator_context)

    def test_can_execute_admin_privileged(self, admin_context):
        """Test admin can execute privileged tool."""
        tool = ToolDefinition(
            name="delete_cluster",
            safety_level=ToolSafetyLevel.PRIVILEGED,
            permissions=[Permission.ADMIN_CLUSTER],
            requires_crq=True,
        )

        assert tool.can_execute(admin_context)

    def test_require_execution_success(self, operator_context):
        """Test require_execution succeeds when permitted."""
        tool = ToolDefinition(
            name="create_machine",
            safety_level=ToolSafetyLevel.NON_DESTRUCTIVE,
            permissions=[Permission.WRITE_MACHINES],
        )

        tool.require_execution(operator_context)

    def test_require_execution_failure(self, viewer_context):
        """Test require_execution fails when not permitted."""
        tool = ToolDefinition(
            name="create_machine",
            safety_level=ToolSafetyLevel.NON_DESTRUCTIVE,
            permissions=[Permission.WRITE_MACHINES],
        )

        with pytest.raises(AuthorizationError):
            tool.require_execution(viewer_context)

    def test_require_execution_crq_required(self, admin_context):
        """Test require_execution fails when CRQ required but not provided."""
        from mosk_mcp.core.exceptions import ValidationError

        tool = ToolDefinition(
            name="delete_cluster",
            safety_level=ToolSafetyLevel.PRIVILEGED,
            permissions=[Permission.ADMIN_CLUSTER],
            requires_crq=True,
        )

        with pytest.raises(ValidationError) as exc_info:
            tool.require_execution(admin_context)

        assert "crq" in str(exc_info.value).lower()

    def test_require_execution_with_crq(self, admin_context):
        """Test require_execution succeeds with CRQ."""
        tool = ToolDefinition(
            name="delete_cluster",
            safety_level=ToolSafetyLevel.PRIVILEGED,
            permissions=[Permission.ADMIN_CLUSTER],
            requires_crq=True,
        )

        tool.require_execution(admin_context, crq_id="CRQ123456789")


# =============================================================================
# ToolRegistry Tests
# =============================================================================


class TestToolRegistry:
    """Tests for ToolRegistry class."""

    def test_register_tool(self):
        """Test registering a tool."""
        registry = ToolRegistry()
        tool = ToolDefinition(name="list_machines")

        registry.register(tool)

        assert registry.get("list_machines") is tool

    def test_get_nonexistent_tool(self):
        """Test getting non-existent tool."""
        registry = ToolRegistry()

        assert registry.get("nonexistent") is None

    def test_get_all_tools(self):
        """Test getting all tools."""
        registry = ToolRegistry()
        registry.register(ToolDefinition(name="tool1"))
        registry.register(ToolDefinition(name="tool2"))

        tools = registry.get_all()

        assert len(tools) == 2

    def test_get_available_tools_viewer(self, viewer_context):
        """Test getting tools available to viewer."""
        registry = ToolRegistry()
        registry.register(
            ToolDefinition(
                name="list_machines",
                safety_level=ToolSafetyLevel.READ_ONLY,
            )
        )
        registry.register(
            ToolDefinition(
                name="create_machine",
                safety_level=ToolSafetyLevel.NON_DESTRUCTIVE,
            )
        )

        available = registry.get_available_tools(viewer_context)

        assert len(available) == 1
        assert available[0].name == "list_machines"

    def test_get_available_tools_operator(self, operator_context):
        """Test getting tools available to operator."""
        registry = ToolRegistry()
        registry.register(
            ToolDefinition(
                name="list_machines",
                safety_level=ToolSafetyLevel.READ_ONLY,
            )
        )
        registry.register(
            ToolDefinition(
                name="create_machine",
                safety_level=ToolSafetyLevel.NON_DESTRUCTIVE,
            )
        )
        registry.register(
            ToolDefinition(
                name="delete_cluster",
                safety_level=ToolSafetyLevel.PRIVILEGED,
            )
        )

        available = registry.get_available_tools(operator_context)

        assert len(available) == 2
        names = [t.name for t in available]
        assert "list_machines" in names
        assert "create_machine" in names
        assert "delete_cluster" not in names

    def test_get_tools_by_safety_level(self):
        """Test filtering tools by safety level."""
        registry = ToolRegistry()
        registry.register(
            ToolDefinition(
                name="read_tool",
                safety_level=ToolSafetyLevel.READ_ONLY,
            )
        )
        registry.register(
            ToolDefinition(
                name="write_tool",
                safety_level=ToolSafetyLevel.NON_DESTRUCTIVE,
            )
        )

        read_tools = registry.get_tools_by_safety_level(ToolSafetyLevel.READ_ONLY)

        assert len(read_tools) == 1
        assert read_tools[0].name == "read_tool"

    def test_can_execute(self, operator_context):
        """Test can_execute method."""
        registry = ToolRegistry()
        registry.register(
            ToolDefinition(
                name="list_machines",
                safety_level=ToolSafetyLevel.READ_ONLY,
            )
        )
        registry.register(
            ToolDefinition(
                name="admin_tool",
                safety_level=ToolSafetyLevel.PRIVILEGED,
            )
        )

        assert registry.can_execute("list_machines", operator_context)
        assert not registry.can_execute("admin_tool", operator_context)
        assert not registry.can_execute("nonexistent", operator_context)
