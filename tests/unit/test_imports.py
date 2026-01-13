"""Import verification tests.

These tests ensure that all public modules and their exports are importable
and that the package structure is correct after refactoring.
"""

from datetime import UTC


class TestCoreImports:
    """Test core module imports."""

    def test_core_exceptions(self) -> None:
        """Test core exception imports."""
        from mosk_mcp.core import (
            AuthenticationError,
            AuthorizationError,
            ConfigurationError,
            KubernetesError,
            MoskConnectionError,
            MoskMCPError,
            RateLimitError,
            ResourceNotFoundError,
            ToolExecutionError,
            UnsupportedVersionError,
            ValidationError,
        )

        # Verify they are exception classes
        assert issubclass(MoskMCPError, Exception)
        assert issubclass(KubernetesError, MoskMCPError)
        assert issubclass(ToolExecutionError, MoskMCPError)
        assert issubclass(AuthenticationError, MoskMCPError)
        assert issubclass(AuthorizationError, MoskMCPError)
        assert issubclass(ConfigurationError, MoskMCPError)
        assert issubclass(MoskConnectionError, MoskMCPError)
        assert issubclass(RateLimitError, MoskMCPError)
        assert issubclass(ResourceNotFoundError, MoskMCPError)
        assert issubclass(UnsupportedVersionError, MoskMCPError)
        assert issubclass(ValidationError, MoskMCPError)

    def test_core_config(self) -> None:
        """Test core config imports."""
        from mosk_mcp.core import Settings, TransportType, get_settings

        assert Settings is not None
        assert TransportType is not None
        assert callable(get_settings)

    def test_core_server_context(self) -> None:
        """Test core server context imports."""
        from mosk_mcp.core import ServerContextConfig, SSOServerContext

        assert ServerContextConfig is not None
        assert SSOServerContext is not None

    def test_core_server(self) -> None:
        """Test core server imports (direct from server module)."""
        from mosk_mcp.core.server import create_mcp_server, handle_tool_error

        assert callable(create_mcp_server)
        assert callable(handle_tool_error)


class TestInfrastructureImports:
    """Test infrastructure module imports."""

    def test_ratelimit_imports(self) -> None:
        """Test rate limiting imports."""
        from mosk_mcp.infrastructure import (
            RateLimitConfig,
            RateLimiter,
            RateLimitExceeded,
            check_rate_limit,
            get_rate_limiter,
            set_rate_limiter,
        )

        assert RateLimitConfig is not None
        assert RateLimitExceeded is not None
        assert RateLimiter is not None
        assert callable(check_rate_limit)
        assert callable(get_rate_limiter)
        assert callable(set_rate_limiter)

    def test_ratelimit_exceeded_is_exception(self) -> None:
        """Test RateLimitExceeded is an exception."""
        from mosk_mcp.infrastructure import RateLimitExceeded

        assert issubclass(RateLimitExceeded, Exception)

    def test_shutdown_imports(self) -> None:
        """Test shutdown handler imports."""
        from mosk_mcp.infrastructure import (
            GracefulShutdownManager,
            ShutdownEvent,
            ShutdownHook,
            ShutdownState,
            get_shutdown_manager,
            register_shutdown_hook,
            set_shutdown_manager,
        )

        assert GracefulShutdownManager is not None
        assert ShutdownEvent is not None
        assert ShutdownHook is not None
        assert ShutdownState is not None
        assert callable(get_shutdown_manager)
        assert callable(register_shutdown_hook)
        assert callable(set_shutdown_manager)

    def test_version_checker_imports(self) -> None:
        """Test version checker imports."""
        from mosk_mcp.infrastructure import (
            MOSKVersionInfo,
            VersionCompatibility,
            get_cached_version_info,
            set_cached_version_info,
        )

        assert MOSKVersionInfo is not None
        assert VersionCompatibility is not None
        assert callable(get_cached_version_info)
        assert callable(set_cached_version_info)


class TestObservabilityImports:
    """Test observability module imports."""

    def test_logging_imports(self) -> None:
        """Test logging imports."""
        from mosk_mcp.observability import LoggingContext, get_logger, setup_logging

        assert callable(setup_logging)
        assert callable(get_logger)
        assert LoggingContext is not None

    def test_audit_imports(self) -> None:
        """Test audit imports."""
        from mosk_mcp.observability import (
            AuditCategory,
            AuditContext,
            AuditEvent,
            AuditLevel,
            AuditLogger,
            AuditStatus,
        )

        assert AuditCategory is not None
        assert AuditContext is not None
        assert AuditEvent is not None
        assert AuditLevel is not None
        assert AuditLogger is not None
        assert AuditStatus is not None

    def test_metrics_imports(self) -> None:
        """Test metrics imports."""
        from mosk_mcp.observability import (
            MetricsRegistry,
            SafetyLevel,
            ToolStatus,
            create_metrics_app,
            get_metrics_registry,
            init_metrics_registry,
            record_auth_failure,
            record_k8s_request,
            record_privileged_op,
            track_tool,
        )

        assert MetricsRegistry is not None
        assert SafetyLevel is not None
        assert ToolStatus is not None
        assert callable(create_metrics_app)
        assert callable(get_metrics_registry)
        assert callable(init_metrics_registry)
        assert callable(record_auth_failure)
        assert callable(record_k8s_request)
        assert callable(record_privileged_op)
        assert callable(track_tool)

    def test_health_imports(self) -> None:
        """Test health check imports."""
        from mosk_mcp.observability import (
            CheckResult,
            HealthChecker,
            HealthResponse,
            HealthStatus,
            create_health_app,
            get_health_checker,
            init_health_checker,
        )

        assert CheckResult is not None
        assert HealthChecker is not None
        assert HealthResponse is not None
        assert HealthStatus is not None
        assert callable(create_health_app)
        assert callable(get_health_checker)
        assert callable(init_health_checker)


class TestRegistrationImports:
    """Test registration module imports."""

    def test_models_imports(self) -> None:
        """Test registration models imports."""
        from mosk_mcp.registration import ServerHealthResult, ServerInfo

        assert ServerHealthResult is not None
        assert ServerInfo is not None

    def test_tool_registration_imports(self) -> None:
        """Test tool registration function imports."""
        from mosk_mcp.registration.tools import (
            register_auth_tools,
            register_ceph_operations_tools,
            register_cluster_health_tools,
            register_node_lifecycle_tools,
            register_operations_visibility_tools,
            register_template_generation_tools,
            register_troubleshooting_tools,
            register_validation_tools,
        )

        assert callable(register_auth_tools)
        assert callable(register_ceph_operations_tools)
        assert callable(register_cluster_health_tools)
        assert callable(register_node_lifecycle_tools)
        assert callable(register_operations_visibility_tools)
        assert callable(register_template_generation_tools)
        assert callable(register_troubleshooting_tools)
        assert callable(register_validation_tools)


class TestToolsCommonImports:
    """Test tools.common shared utilities imports."""

    def test_health_utilities(self) -> None:
        """Test health utilities imports."""
        from mosk_mcp.tools.common import (
            CAPACITY_CRITICAL_THRESHOLD,
            CAPACITY_EMERGENCY_THRESHOLD,
            CAPACITY_WARNING_THRESHOLD,
            capacity_status,
            score_to_health,
        )

        # Thresholds per PROJECT_TRACKER.md:
        # - Warning (70%): Alert operator, suggest capacity planning
        # - Critical (80%): Require immediate attention
        # - Emergency (85%): Trigger OSD nearfull, automated alerts
        assert CAPACITY_WARNING_THRESHOLD == 70.0
        assert CAPACITY_CRITICAL_THRESHOLD == 80.0
        assert CAPACITY_EMERGENCY_THRESHOLD == 85.0
        assert callable(score_to_health)
        assert callable(capacity_status)

    def test_score_to_health_function(self) -> None:
        """Test score_to_health returns correct values."""
        from mosk_mcp.tools.common import HealthStatus, score_to_health

        assert score_to_health(95) == HealthStatus.HEALTHY
        assert score_to_health(90) == HealthStatus.HEALTHY
        assert score_to_health(89) == HealthStatus.DEGRADED
        assert score_to_health(70) == HealthStatus.DEGRADED
        assert score_to_health(69) == HealthStatus.UNHEALTHY
        assert score_to_health(50) == HealthStatus.UNHEALTHY
        assert score_to_health(0) == HealthStatus.UNHEALTHY

    def test_capacity_status_function(self) -> None:
        """Test capacity_status returns correct values.

        Thresholds per PROJECT_TRACKER.md:
        - < 70%: OK
        - 70-80%: WARNING (alert operator, capacity planning)
        - 80-85%: CRITICAL (require immediate attention)
        - >= 85%: EMERGENCY (OSD nearfull, automated alerts)
        """
        from mosk_mcp.tools.common import capacity_status

        # OK: < 70%
        assert capacity_status(50.0) == "OK"
        assert capacity_status(69.9) == "OK"
        # WARNING: 70-80%
        assert capacity_status(70.0) == "WARNING"
        assert capacity_status(79.9) == "WARNING"
        # CRITICAL: 80-85%
        assert capacity_status(80.0) == "CRITICAL"
        assert capacity_status(84.9) == "CRITICAL"
        # EMERGENCY: >= 85%
        assert capacity_status(85.0) == "EMERGENCY"
        assert capacity_status(100.0) == "EMERGENCY"

    def test_kubernetes_utilities(self) -> None:
        """Test Kubernetes utilities imports."""
        from mosk_mcp.tools.common import (
            calculate_resource_age,
            format_age,
            format_bytes,
            parse_kubernetes_quantity,
        )

        assert callable(calculate_resource_age)
        assert callable(format_age)
        assert callable(format_bytes)
        assert callable(parse_kubernetes_quantity)

    def test_format_bytes_function(self) -> None:
        """Test format_bytes returns correct values."""
        from mosk_mcp.tools.common import format_bytes

        assert format_bytes(500) == "500 B"
        assert format_bytes(1024) == "1.00 KB"
        assert format_bytes(1048576) == "1.00 MB"
        assert format_bytes(1073741824) == "1.00 GB"

    def test_format_age_function(self) -> None:
        """Test format_age returns correct values."""
        from mosk_mcp.tools.common import format_age

        assert format_age(None) == "unknown"
        assert format_age(30) == "0m"
        assert format_age(90) == "1m"
        assert format_age(3700) == "1h 1m"
        assert format_age(86400) == "1d 0h"
        assert format_age(90000) == "1d 1h"

    def test_parse_kubernetes_quantity_function(self) -> None:
        """Test parse_kubernetes_quantity returns correct values."""
        from mosk_mcp.tools.common import parse_kubernetes_quantity

        assert parse_kubernetes_quantity(1000) == 1000
        assert parse_kubernetes_quantity("1Ki") == 1024
        assert parse_kubernetes_quantity("1Mi") == 1048576
        assert parse_kubernetes_quantity("1Gi") == 1073741824
        assert parse_kubernetes_quantity("500m") == 500
        assert parse_kubernetes_quantity("1k") == 1000
        assert parse_kubernetes_quantity("1M") == 1000000

    def test_calculate_resource_age_function(self) -> None:
        """Test calculate_resource_age with various inputs."""
        from datetime import datetime

        from mosk_mcp.tools.common import calculate_resource_age

        # Empty metadata
        assert calculate_resource_age({}) is None

        # Missing timestamp
        assert calculate_resource_age({"name": "test"}) is None

        # Valid ISO timestamp
        from datetime import timedelta

        now = datetime.now(UTC)
        one_hour_ago = now - timedelta(hours=1)
        metadata = {"creationTimestamp": one_hour_ago.isoformat()}
        age = calculate_resource_age(metadata)
        assert age is not None
        assert age > 0

    def test_audit_utilities(self) -> None:
        """Test audit utilities imports."""
        from mosk_mcp.tools.common import audit_tool_execution

        assert audit_tool_execution is not None
        # It's an async context manager
        import inspect

        assert inspect.isasyncgenfunction(
            audit_tool_execution.__wrapped__  # type: ignore[attr-defined]
        ) or hasattr(audit_tool_execution, "__aenter__")

    def test_error_handling_utilities(self) -> None:
        """Test error handling utilities imports."""
        from mosk_mcp.tools.common import tool_handler, wrap_kubernetes_error

        assert callable(tool_handler)
        assert callable(wrap_kubernetes_error)


class TestAdapterImports:
    """Test adapter module imports."""

    def test_kubernetes_adapter(self) -> None:
        """Test Kubernetes adapter import."""
        from mosk_mcp.adapters.kubernetes import KubernetesAdapter

        assert KubernetesAdapter is not None

    def test_ceph_adapter(self) -> None:
        """Test Ceph adapter import."""
        from mosk_mcp.adapters.ceph import CephAdapter, CephHealthStatus

        assert CephAdapter is not None
        assert CephHealthStatus is not None

    def test_openstack_adapter(self) -> None:
        """Test OpenStack adapter import."""
        from mosk_mcp.adapters.openstack import OpenStackAdapter

        assert OpenStackAdapter is not None

    def test_stacklight_adapter(self) -> None:
        """Test StackLight adapter imports."""
        from mosk_mcp.adapters.stacklight import (
            StackLightAdapter,
            StackLightManager,
        )

        assert StackLightAdapter is not None
        assert StackLightManager is not None


class TestAuthImports:
    """Test auth module imports."""

    def test_rbac_imports(self) -> None:
        """Test RBAC imports."""
        from mosk_mcp.auth.rbac import (
            Permission,
            RBACEnforcer,
            Role,
            ToolDefinition,
            ToolRegistry,
            ToolSafetyLevel,
            get_enforcer,
            require_authentication,
            require_safety_level,
            set_enforcer,
        )

        assert Permission is not None
        assert RBACEnforcer is not None
        assert Role is not None
        assert ToolDefinition is not None
        assert ToolRegistry is not None
        assert ToolSafetyLevel is not None
        assert callable(get_enforcer)
        assert callable(require_authentication)
        assert callable(require_safety_level)
        assert callable(set_enforcer)

    def test_session_imports(self) -> None:
        """Test session imports."""
        from mosk_mcp.auth.session import (
            ClusterOIDCInfo,
            SessionState,
            TokenBasedAuthAdapter,
            TokenResponse,
            UserSession,
            generate_cluster_kubeconfig,
        )

        assert ClusterOIDCInfo is not None
        assert SessionState is not None
        assert TokenBasedAuthAdapter is not None
        assert TokenResponse is not None
        assert UserSession is not None
        assert callable(generate_cluster_kubeconfig)

    def test_keycloak_imports(self) -> None:
        """Test Keycloak imports."""
        from mosk_mcp.auth.keycloak_client import (
            ClusterOIDCInfo,
            MCCEndpoints,
            TokenResponse,
            discover_mcc_endpoints,
            discover_stacklight_endpoints,
            exchange_token_for_audience,
            generate_cluster_kubeconfig,
            generate_token_kubeconfig,
            get_cluster_oidc_info,
        )

        assert ClusterOIDCInfo is not None
        assert MCCEndpoints is not None
        assert TokenResponse is not None
        assert callable(discover_mcc_endpoints)
        assert callable(discover_stacklight_endpoints)
        assert callable(exchange_token_for_audience)
        assert callable(generate_cluster_kubeconfig)
        assert callable(generate_token_kubeconfig)
        assert callable(get_cluster_oidc_info)

    def test_crq_imports(self) -> None:
        """Test CRQ imports."""
        from mosk_mcp.auth.crq import CRQValidator

        assert CRQValidator is not None


class TestNoCircularImports:
    """Test that there are no circular import issues."""

    def test_import_all_modules(self) -> None:
        """Test importing all main modules doesn't cause circular imports."""
        # Core
        import mosk_mcp.core
        import mosk_mcp.core.config
        import mosk_mcp.core.exceptions
        import mosk_mcp.core.server
        import mosk_mcp.core.server_context
        import mosk_mcp.core.validation

        # Infrastructure
        import mosk_mcp.infrastructure
        import mosk_mcp.infrastructure.ratelimit
        import mosk_mcp.infrastructure.shutdown
        import mosk_mcp.infrastructure.version_checker

        # Observability
        import mosk_mcp.observability
        import mosk_mcp.observability.audit
        import mosk_mcp.observability.health
        import mosk_mcp.observability.logging
        import mosk_mcp.observability.metrics

        # Registration
        import mosk_mcp.registration
        import mosk_mcp.registration.models

        # Tools common
        import mosk_mcp.tools.common
        import mosk_mcp.tools.common.audit
        import mosk_mcp.tools.common.errors
        import mosk_mcp.tools.common.health
        import mosk_mcp.tools.common.kubernetes

        # All imports succeeded - use variables to silence linter
        assert mosk_mcp.core is not None
        assert mosk_mcp.infrastructure is not None
        assert mosk_mcp.observability is not None
        assert mosk_mcp.registration is not None
        assert mosk_mcp.tools.common is not None

    def test_cross_module_imports(self) -> None:
        """Test common cross-module import patterns work."""
        # Pattern: tool imports from core and common
        # Pattern: adapters import from core
        from mosk_mcp.adapters.kubernetes import KubernetesAdapter
        from mosk_mcp.core.exceptions import ToolExecutionError

        # Pattern: registration imports from tools
        from mosk_mcp.registration.tools import register_cluster_health_tools
        from mosk_mcp.tools.common import score_to_health, tool_handler

        # Use variables to silence linter
        assert ToolExecutionError is not None
        assert tool_handler is not None
        assert score_to_health is not None
        assert register_cluster_health_tools is not None
        assert KubernetesAdapter is not None
