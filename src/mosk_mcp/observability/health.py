"""Health check endpoints for MOSK MCP Server.

This module provides Kubernetes-compatible health check endpoints:
- /health/live - Liveness probe (server is running)
- /health/ready - Readiness probe (dependencies connected)
- /health/startup - Startup probe (initialization complete)

These endpoints return JSON responses with status and details suitable
for Kubernetes health probes and monitoring systems.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from mosk_mcp.infrastructure.version_checker import get_cached_version_info
from mosk_mcp.observability.logging import get_logger


if TYPE_CHECKING:
    from mosk_mcp.adapters.kubernetes import KubernetesAdapter
    from mosk_mcp.core.config import Settings


logger = get_logger(__name__)


class HealthStatus(str, Enum):
    """Health status values."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


class CheckResult(BaseModel):
    """Result of a single health check component."""

    name: str = Field(..., description="Name of the check")
    status: HealthStatus = Field(..., description="Check status")
    message: str | None = Field(default=None, description="Status message")
    latency_ms: float | None = Field(default=None, description="Check latency in milliseconds")
    details: dict[str, Any] = Field(default_factory=dict, description="Additional details")


class HealthResponse(BaseModel):
    """Health check response model."""

    status: HealthStatus = Field(..., description="Overall health status")
    timestamp: str = Field(..., description="ISO 8601 timestamp")
    version: str = Field(..., description="Server version")
    mosk_version: str | None = Field(default=None, description="MOSK cluster version")
    mosk_version_supported: bool | None = Field(
        default=None, description="Whether MOSK version meets minimum requirements (25.1+)"
    )
    checks: list[CheckResult] = Field(default_factory=list, description="Individual checks")
    uptime_seconds: float = Field(..., description="Server uptime in seconds")
    warnings: list[str] = Field(
        default_factory=list, description="Version or compatibility warnings"
    )


class StartupResponse(BaseModel):
    """Startup probe response model."""

    status: HealthStatus = Field(..., description="Startup status")
    timestamp: str = Field(..., description="ISO 8601 timestamp")
    initialized: bool = Field(..., description="Whether initialization is complete")
    initialization_time_seconds: float | None = Field(None, description="Time taken to initialize")
    message: str | None = Field(None, description="Status message")


@dataclass
class HealthChecker:
    """Health check manager for the MCP server.

    This class manages health check state and provides methods for
    checking server health at various levels.

    Attributes:
        settings: Application settings.
        start_time: Server start time.
        initialization_complete: Whether initialization is done.
        initialization_time: Time taken to initialize.
        _k8s_adapter: Optional Kubernetes adapter for connectivity checks.
    """

    settings: Settings
    start_time: float = field(default_factory=time.time)
    initialization_complete: bool = False
    initialization_time: float | None = None
    _k8s_adapter: KubernetesAdapter | None = None
    _last_k8s_check: float = 0.0
    _last_k8s_status: bool = False
    _k8s_check_cache_ttl: float = 2.0  # Cache K8s check results for 2 seconds (reduced from 5)
    _k8s_check_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _last_k8s_latency_ms: float = 0.0

    def mark_initialized(self) -> None:
        """Mark the server as fully initialized."""
        if not self.initialization_complete:
            self.initialization_time = time.time() - self.start_time
            self.initialization_complete = True
            logger.info(
                "server_initialized",
                initialization_time_seconds=self.initialization_time,
            )

    def set_kubernetes_adapter(self, adapter: KubernetesAdapter) -> None:
        """Set the Kubernetes adapter for health checks.

        Args:
            adapter: Initialized Kubernetes adapter.
        """
        self._k8s_adapter = adapter

    @property
    def uptime_seconds(self) -> float:
        """Get server uptime in seconds."""
        return time.time() - self.start_time

    async def check_liveness(self) -> HealthResponse:
        """Check server liveness.

        Liveness checks verify the server process is running and responsive.
        This is a lightweight check that should always succeed if the server
        is processing requests.

        Returns:
            HealthResponse indicating server is alive.
        """
        checks = [
            CheckResult(
                name="process",
                status=HealthStatus.HEALTHY,
                message="Server process is running",
            )
        ]

        # Get MOSK version info if available
        version_info = get_cached_version_info()
        mosk_version = version_info.version_string if version_info else None
        mosk_version_supported = version_info.is_compatible if version_info else None
        warnings = version_info.warnings if version_info else []

        return HealthResponse(
            status=HealthStatus.HEALTHY,
            timestamp=datetime.now(UTC).isoformat(),
            version=self.settings.app_version,
            mosk_version=mosk_version,
            mosk_version_supported=mosk_version_supported,
            checks=checks,
            uptime_seconds=self.uptime_seconds,
            warnings=warnings,
        )

    async def check_readiness(self) -> HealthResponse:
        """Check server readiness.

        Readiness checks verify the server can handle requests by checking
        connectivity to required dependencies (e.g., Kubernetes API).

        During shutdown (draining), this returns UNHEALTHY to remove the
        server from load balancer rotation.

        Returns:
            HealthResponse with dependency check results.
        """
        from mosk_mcp.infrastructure.shutdown import get_shutdown_manager

        checks: list[CheckResult] = []
        overall_status = HealthStatus.HEALTHY

        # Get MOSK version info if available
        version_info = get_cached_version_info()
        mosk_version = version_info.version_string if version_info else None
        mosk_version_supported = version_info.is_compatible if version_info else None
        warnings = version_info.warnings if version_info else []

        # Check if server is shutting down (draining)
        shutdown_manager = get_shutdown_manager()
        if shutdown_manager.is_shutting_down:
            checks.append(
                CheckResult(
                    name="shutdown",
                    status=HealthStatus.UNHEALTHY,
                    message=f"Server is {shutdown_manager.state.value}",
                    details={
                        "state": shutdown_manager.state.value,
                        "active_requests": shutdown_manager.active_requests,
                    },
                )
            )
            return HealthResponse(
                status=HealthStatus.UNHEALTHY,
                timestamp=datetime.now(UTC).isoformat(),
                version=self.settings.app_version,
                mosk_version=mosk_version,
                mosk_version_supported=mosk_version_supported,
                checks=checks,
                uptime_seconds=self.uptime_seconds,
                warnings=warnings,
            )

        # Check Kubernetes connectivity if enabled
        if self.settings.health_check_k8s_enabled:
            k8s_check = await self._check_kubernetes()
            checks.append(k8s_check)
            if k8s_check.status == HealthStatus.UNHEALTHY:
                overall_status = HealthStatus.UNHEALTHY
            elif (
                k8s_check.status == HealthStatus.DEGRADED and overall_status == HealthStatus.HEALTHY
            ):
                overall_status = HealthStatus.DEGRADED

        # Add basic process check
        checks.append(
            CheckResult(
                name="process",
                status=HealthStatus.HEALTHY,
                message="Server process is running",
            )
        )

        return HealthResponse(
            status=overall_status,
            timestamp=datetime.now(UTC).isoformat(),
            version=self.settings.app_version,
            mosk_version=mosk_version,
            mosk_version_supported=mosk_version_supported,
            checks=checks,
            uptime_seconds=self.uptime_seconds,
            warnings=warnings,
        )

    async def check_startup(self) -> StartupResponse:
        """Check server startup status.

        Startup checks verify the server has completed initialization.
        This is used by Kubernetes to determine when to start sending
        traffic to the server.

        Returns:
            StartupResponse with initialization status.
        """
        if self.initialization_complete:
            return StartupResponse(
                status=HealthStatus.HEALTHY,
                timestamp=datetime.now(UTC).isoformat(),
                initialized=True,
                initialization_time_seconds=self.initialization_time,
                message="Server initialization complete",
            )
        else:
            return StartupResponse(
                status=HealthStatus.UNHEALTHY,
                timestamp=datetime.now(UTC).isoformat(),
                initialized=False,
                initialization_time_seconds=None,
                message="Server is still initializing",
            )

    async def _check_kubernetes(self) -> CheckResult:
        """Check Kubernetes API connectivity.

        Uses caching with proper locking to avoid race conditions and
        overwhelming the API server with health checks.

        Returns:
            CheckResult for Kubernetes connectivity.
        """
        # Use lock to prevent race conditions on cache check and update
        async with self._k8s_check_lock:
            # Check cache (inside lock to prevent races)
            now = time.time()
            if now - self._last_k8s_check < self._k8s_check_cache_ttl:
                return CheckResult(
                    name="kubernetes",
                    status=HealthStatus.HEALTHY
                    if self._last_k8s_status
                    else HealthStatus.UNHEALTHY,
                    message="Cached check result",
                    latency_ms=self._last_k8s_latency_ms,
                    details={"cached": True, "cache_age_ms": (now - self._last_k8s_check) * 1000},
                )

            if self._k8s_adapter is None:
                return CheckResult(
                    name="kubernetes",
                    status=HealthStatus.DEGRADED,
                    message="Kubernetes adapter not configured",
                    details={"reason": "no_adapter"},
                )

            start = time.time()
            try:
                # Try to connect and get API version
                async with asyncio.timeout(self.settings.health_check_timeout_seconds):
                    connected = await self._k8s_adapter.check_connectivity()

                latency_ms = (time.time() - start) * 1000
                self._last_k8s_check = time.time()  # Update after check completes
                self._last_k8s_status = connected
                self._last_k8s_latency_ms = latency_ms

                if connected:
                    return CheckResult(
                        name="kubernetes",
                        status=HealthStatus.HEALTHY,
                        message="Connected to Kubernetes API",
                        latency_ms=latency_ms,
                    )
                else:
                    return CheckResult(
                        name="kubernetes",
                        status=HealthStatus.UNHEALTHY,
                        message="Cannot connect to Kubernetes API",
                        latency_ms=latency_ms,
                    )

            except TimeoutError:
                latency_ms = (time.time() - start) * 1000
                self._last_k8s_check = time.time()
                self._last_k8s_status = False
                self._last_k8s_latency_ms = latency_ms
                logger.warning(
                    "kubernetes_health_check_timeout",
                    timeout_seconds=self.settings.health_check_timeout_seconds,
                )
                return CheckResult(
                    name="kubernetes",
                    status=HealthStatus.UNHEALTHY,
                    message=f"Kubernetes API check timed out after {self.settings.health_check_timeout_seconds}s",
                    latency_ms=latency_ms,
                    details={"timeout": True},
                )

            except Exception as e:
                latency_ms = (time.time() - start) * 1000
                self._last_k8s_check = time.time()
                self._last_k8s_status = False
                self._last_k8s_latency_ms = latency_ms
                logger.warning(
                    "kubernetes_health_check_failed",
                    error=str(e),
                    error_type=type(e).__name__,
                )
                return CheckResult(
                    name="kubernetes",
                    status=HealthStatus.UNHEALTHY,
                    message=f"Kubernetes API check failed: {e}",
                    latency_ms=latency_ms,
                    details={"error": str(e)},
                )


# Global health checker instance (initialized by server)
_health_checker: HealthChecker | None = None


def get_health_checker() -> HealthChecker | None:
    """Get the global health checker instance.

    Returns:
        The health checker instance, or None if not initialized.
    """
    return _health_checker


def init_health_checker(settings: Settings) -> HealthChecker:
    """Initialize the global health checker.

    Args:
        settings: Application settings.

    Returns:
        Initialized health checker.
    """
    global _health_checker
    _health_checker = HealthChecker(settings=settings)
    logger.info("health_checker_initialized")
    return _health_checker


def create_health_app(health_checker: HealthChecker) -> Any:
    """Create a Starlette application for health endpoints.

    This creates a separate ASGI application that serves health check
    endpoints independently of the MCP server. This ensures health
    checks remain responsive even if the MCP server is under load.

    Args:
        health_checker: The health checker instance.

    Returns:
        Starlette application with health endpoints.
    """
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    async def liveness(request: Any) -> JSONResponse:
        """Handle liveness probe requests."""
        result = await health_checker.check_liveness()
        status_code = 200 if result.status == HealthStatus.HEALTHY else 503
        return JSONResponse(result.model_dump(), status_code=status_code)

    async def readiness(request: Any) -> JSONResponse:
        """Handle readiness probe requests."""
        result = await health_checker.check_readiness()
        status_code = 200 if result.status == HealthStatus.HEALTHY else 503
        return JSONResponse(result.model_dump(), status_code=status_code)

    async def startup(request: Any) -> JSONResponse:
        """Handle startup probe requests."""
        result = await health_checker.check_startup()
        status_code = 200 if result.status == HealthStatus.HEALTHY else 503
        return JSONResponse(result.model_dump(), status_code=status_code)

    async def health_root(request: Any) -> JSONResponse:
        """Handle root health endpoint - returns readiness status."""
        result = await health_checker.check_readiness()
        status_code = 200 if result.status == HealthStatus.HEALTHY else 503
        return JSONResponse(result.model_dump(), status_code=status_code)

    routes = [
        Route("/", health_root),
        Route("/health", health_root),
        Route("/health/live", liveness),
        Route("/health/ready", readiness),
        Route("/health/startup", startup),
    ]

    app = Starlette(routes=routes)
    return app
