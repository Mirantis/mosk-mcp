"""StackLight Manager for dual-cluster monitoring.

This module provides the StackLightManager class that orchestrates two
independent StackLightAdapter instances for MCC (management) and MOSK
(workload) clusters.

Architecture:
    StackLightManager
    ├── MCC StackLightAdapter
    │   └── DirectStackLightClient (OIDC/SSO)
    └── MOSK StackLightAdapter
        └── DirectStackLightClient (OIDC/SSO)

The manager uses Keycloak OIDC/SSO authentication to access StackLight
services via their IAM Proxy endpoints. This provides:
- User-scoped access (respects IAM RBAC permissions)
- No dependency on admin kubeconfig
- Direct HTTP calls for better performance

Features:
- Dual cluster alert, log, and metric queries
- Graceful degradation when one cluster is unavailable
- Alert deduplication across clusters
- Unified health status aggregation
- Concurrent query execution for performance

Example:
    # Authentication is handled via Device Flow (login_secure tool)
    # After authentication, use the session to get StackLight clients

    mcc_client = await session.get_stacklight_client()
    mosk_client = await mosk_session.get_stacklight_client()

    async with mcc_client, mosk_client:
        manager = StackLightManager(mcc_client, mosk_client)
        async with manager:
            # Get alerts from both clusters
            result = await manager.get_combined_alerts()
            print(f"Found {result.total_unique} unique alerts")

            # Check health
            health = await manager.check_health()
            print(f"Overall status: {health.overall_status}")
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal

from mosk_mcp.adapters.stacklight.core import (
    AlertSeverity,
    AlertState,
    DirectStackLightClient,
    LogEntry,
    MetricSample,
    StackLightAdapter,
)
from mosk_mcp.adapters.stacklight.response_models import (
    AlertQueryResult,
    ClusterStackLightHealth,
    CombinedAlertResult,
    CombinedLogResult,
    CombinedMetricResult,
    ComponentHealth,
    ComponentHealthStatus,
    LogQueryResult,
    ManagerHealthStatus,
    MetricQueryResult,
    StackLightManagerHealth,
    deduplicate_alerts,
    empty_alert_result,
    empty_log_result,
    empty_metric_result,
)
from mosk_mcp.observability.logging import get_logger


if TYPE_CHECKING:
    from mosk_mcp.core.config import Settings

logger = get_logger(__name__)

# Type alias for cluster types
ClusterType = Literal["mcc", "mosk"]


@dataclass
class ManagerState:
    """Internal state for StackLightManager.

    Attributes:
        connected: Whether the manager is connected.
        mcc_healthy: MCC adapter last known health state.
        mosk_healthy: MOSK adapter last known health state.
        last_health_check: Timestamp of last health check.
    """

    connected: bool = False
    mcc_healthy: bool = False
    mosk_healthy: bool = False
    last_health_check: datetime | None = None


class StackLightManager:
    """Manager for dual-cluster StackLight deployments using OIDC/SSO.

    Orchestrates two independent StackLightAdapter instances for the
    MCC (management) and MOSK (workload) clusters, providing:
    - Unified query interface across both clusters
    - Graceful degradation when one cluster is unavailable
    - Alert deduplication across clusters
    - Aggregated health status
    - User-scoped access via Keycloak OIDC tokens

    The manager does NOT cache query results - it delegates to adapters
    which handle their own caching and connection management.

    Attributes:
        mcc: StackLightAdapter for MCC cluster.
        mosk: StackLightAdapter for MOSK cluster.

    Example:
        from mosk_mcp.adapters.stacklight import DirectStackLightClient

        # Create direct clients with OIDC auth
        mcc_client = DirectStackLightClient(auth_provider, prometheus_url=..., alertmanager_url=...)
        mosk_client = DirectStackLightClient(auth_provider, prometheus_url=..., alertmanager_url=...)

        async with mcc_client, mosk_client:
            manager = StackLightManager(mcc_client, mosk_client)
            await manager.connect()

            try:
                alerts = await manager.get_combined_alerts()
            finally:
                await manager.disconnect()
    """

    def __init__(
        self,
        mcc_direct_client: DirectStackLightClient,
        mosk_direct_client: DirectStackLightClient,
        settings: Settings | None = None,
        query_timeout: int = 30,
    ) -> None:
        """Initialize the StackLight manager with OIDC/SSO clients.

        Args:
            mcc_direct_client: DirectStackLightClient for MCC cluster (required).
            mosk_direct_client: DirectStackLightClient for MOSK cluster (required).
            settings: Optional application settings.
            query_timeout: Query timeout in seconds (default 30).
        """
        # Create adapters with DirectStackLightClient instances
        self._mcc = StackLightAdapter(
            direct_client=mcc_direct_client,
            cluster_type="mcc",
            query_timeout=query_timeout,
        )
        self._mosk = StackLightAdapter(
            direct_client=mosk_direct_client,
            cluster_type="mosk",
            query_timeout=query_timeout,
        )
        self._settings = settings
        self._query_timeout = query_timeout
        self._state = ManagerState()
        self._lock = asyncio.Lock()

        logger.debug("stacklight_manager_initialized")

    @property
    def mcc(self) -> StackLightAdapter:
        """Get the MCC cluster StackLight adapter."""
        return self._mcc

    @property
    def mosk(self) -> StackLightAdapter:
        """Get the MOSK cluster StackLight adapter."""
        return self._mosk

    @property
    def is_connected(self) -> bool:
        """Check if manager is connected."""
        return self._state.connected

    # =========================================================================
    # Connection Lifecycle
    # =========================================================================

    async def connect(self) -> None:
        """Connect to both StackLight deployments.

        Connects to MCC and MOSK StackLight adapters. Failures are logged
        but do not prevent connection - the manager operates in degraded
        mode if one cluster is unavailable.

        The manager is considered connected if at least one adapter connects.
        """
        async with self._lock:
            if self._state.connected:
                return

            logger.info("stacklight_manager_connecting")

            # Connect both adapters concurrently
            mcc_result = await self._safe_connect(self._mcc, "mcc")
            mosk_result = await self._safe_connect(self._mosk, "mosk")

            self._state.mcc_healthy = mcc_result
            self._state.mosk_healthy = mosk_result
            # P1 FIX: Only mark connected if at least one cluster is available
            # Previously always marked connected which hid total system failure
            self._state.connected = mcc_result or mosk_result
            self._state.last_health_check = datetime.now(UTC)

            if not mcc_result and not mosk_result:
                logger.error(
                    "stacklight_manager_both_clusters_unavailable",
                    message="StackLight is completely unavailable - neither MCC nor MOSK could connect",
                )
            elif not mcc_result:
                logger.warning("stacklight_manager_mcc_unavailable")
            elif not mosk_result:
                logger.warning("stacklight_manager_mosk_unavailable")
            else:
                logger.info("stacklight_manager_connected")

    async def _safe_connect(
        self,
        adapter: StackLightAdapter,
        cluster_type: str,
    ) -> bool:
        """Safely connect to an adapter, catching errors.

        Args:
            adapter: StackLight adapter to connect.
            cluster_type: Cluster type for logging.

        Returns:
            True if connected successfully, False otherwise.
        """
        try:
            await adapter.connect()
            logger.debug(
                "stacklight_adapter_connected",
                cluster_type=cluster_type,
            )
            return True
        except Exception as e:
            logger.warning(
                "stacklight_adapter_connect_failed",
                cluster_type=cluster_type,
                error=str(e),
            )
            return False

    async def disconnect(self) -> None:
        """Disconnect from both StackLight deployments."""
        async with self._lock:
            if not self._state.connected:
                return

            logger.info("stacklight_manager_disconnecting")

            # Disconnect both adapters
            try:
                await self._mcc.disconnect()
            except Exception as e:
                logger.warning(
                    "mcc_disconnect_error",
                    error=str(e),
                    error_type=type(e).__name__,
                )

            try:
                await self._mosk.disconnect()
            except Exception as e:
                logger.warning(
                    "mosk_disconnect_error",
                    error=str(e),
                    error_type=type(e).__name__,
                )

            self._state.connected = False
            self._state.mcc_healthy = False
            self._state.mosk_healthy = False
            logger.info("stacklight_manager_disconnected")

    async def __aenter__(self) -> StackLightManager:
        """Async context manager entry."""
        await self.connect()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        """Async context manager exit."""
        await self.disconnect()

    # =========================================================================
    # Alert Queries
    # =========================================================================

    async def get_mcc_alerts(
        self,
        state: AlertState | None = None,
        severity: AlertSeverity | None = None,
        labels: dict[str, str] | None = None,
        limit: int = 100,
    ) -> AlertQueryResult:
        """Get alerts from MCC cluster StackLight.

        Args:
            state: Filter by alert state.
            severity: Filter by severity.
            labels: Filter by label values.
            limit: Maximum alerts to return.

        Returns:
            AlertQueryResult with MCC alerts.
        """
        return await self._query_alerts_single(
            adapter=self._mcc,
            cluster_type="mcc",
            state=state,
            severity=severity,
            labels=labels,
            limit=limit,
        )

    async def get_mosk_alerts(
        self,
        state: AlertState | None = None,
        severity: AlertSeverity | None = None,
        labels: dict[str, str] | None = None,
        limit: int = 100,
    ) -> AlertQueryResult:
        """Get alerts from MOSK cluster StackLight.

        Args:
            state: Filter by alert state.
            severity: Filter by severity.
            labels: Filter by label values.
            limit: Maximum alerts to return.

        Returns:
            AlertQueryResult with MOSK alerts.
        """
        return await self._query_alerts_single(
            adapter=self._mosk,
            cluster_type="mosk",
            state=state,
            severity=severity,
            labels=labels,
            limit=limit,
        )

    async def get_combined_alerts(
        self,
        state: AlertState | None = None,
        severity: AlertSeverity | None = None,
        labels: dict[str, str] | None = None,
        limit: int = 200,
        deduplicate: bool = True,
        prefer_cluster: ClusterType | None = None,
    ) -> CombinedAlertResult:
        """Get alerts from both clusters with optional deduplication.

        Queries both MCC and MOSK clusters concurrently, combining results.
        Gracefully handles partial failures - returns available data even
        if one cluster is unavailable.

        Args:
            state: Filter by alert state.
            severity: Filter by severity.
            labels: Filter by label values.
            limit: Maximum alerts to return per cluster.
            deduplicate: Remove duplicate alerts across clusters.
            prefer_cluster: When deduplicating, prefer alerts from this cluster.

        Returns:
            CombinedAlertResult with alerts from both clusters.
        """
        start_time = time.monotonic()

        # Query both clusters concurrently
        mcc_task = self._query_alerts_single(
            adapter=self._mcc,
            cluster_type="mcc",
            state=state,
            severity=severity,
            labels=labels,
            limit=limit,
        )
        mosk_task = self._query_alerts_single(
            adapter=self._mosk,
            cluster_type="mosk",
            state=state,
            severity=severity,
            labels=labels,
            limit=limit,
        )

        mcc_result, mosk_result = await asyncio.gather(mcc_task, mosk_task, return_exceptions=False)

        # Combine alerts
        all_alerts = mcc_result.alerts + mosk_result.alerts

        # Deduplicate if requested
        if deduplicate and all_alerts:
            combined_alerts = deduplicate_alerts(all_alerts, prefer_cluster)
        else:
            combined_alerts = all_alerts

        # Calculate totals
        total_firing = sum(1 for a in combined_alerts if a.get("state") == "firing")
        total_critical = sum(1 for a in combined_alerts if a.get("severity") == "critical")

        overall_success = mcc_result.success or mosk_result.success
        degraded = mcc_result.success != mosk_result.success

        duration_ms = (time.monotonic() - start_time) * 1000

        logger.info(
            "combined_alerts_retrieved",
            mcc_count=mcc_result.count,
            mosk_count=mosk_result.count,
            combined_count=len(combined_alerts),
            deduplicated=deduplicate,
            duration_ms=round(duration_ms, 2),
        )

        return CombinedAlertResult(
            mcc=mcc_result,
            mosk=mosk_result,
            combined_alerts=combined_alerts,
            total_unique=len(combined_alerts),
            total_firing=total_firing,
            total_critical=total_critical,
            timestamp=datetime.now(UTC),
            overall_success=overall_success,
            degraded=degraded,
        )

    async def _query_alerts_single(
        self,
        adapter: StackLightAdapter,
        cluster_type: ClusterType,
        state: AlertState | None,
        severity: AlertSeverity | None,
        labels: dict[str, str] | None,
        limit: int,
    ) -> AlertQueryResult:
        """Query alerts from a single cluster with error handling.

        Args:
            adapter: StackLight adapter to query.
            cluster_type: Cluster type for response.
            state: Filter by alert state.
            severity: Filter by severity.
            labels: Filter by label values.
            limit: Maximum alerts.

        Returns:
            AlertQueryResult with alerts or error info.
        """
        start_time = time.monotonic()

        try:
            alerts = await adapter.get_alerts(
                state=state,
                severity=severity,
                labels=labels,
                limit=limit,
            )

            # Convert to dicts for response
            alerts_dicts = [a.to_dict() for a in alerts]

            # Count by state and severity
            firing_count = sum(1 for a in alerts if a.state == AlertState.FIRING)
            warning_count = sum(1 for a in alerts if a.severity == AlertSeverity.WARNING)
            critical_count = sum(1 for a in alerts if a.severity == AlertSeverity.CRITICAL)

            duration_ms = (time.monotonic() - start_time) * 1000

            return AlertQueryResult(
                cluster_type=cluster_type,
                success=True,
                count=len(alerts),
                query_duration_ms=round(duration_ms, 2),
                alerts=alerts_dicts,
                firing_count=firing_count,
                warning_count=warning_count,
                critical_count=critical_count,
            )

        except Exception as e:
            duration_ms = (time.monotonic() - start_time) * 1000
            error_msg = str(e)

            logger.warning(
                "alert_query_failed",
                cluster_type=cluster_type,
                error=error_msg,
                duration_ms=round(duration_ms, 2),
            )

            return empty_alert_result(cluster_type, error_msg)

    # =========================================================================
    # Metric Queries
    # =========================================================================

    async def query_metrics(
        self,
        cluster_type: ClusterType,
        metric_name: str,
        labels: dict[str, str] | None = None,
        time_range_minutes: int = 60,
        step_seconds: int = 60,
    ) -> list[MetricSample]:
        """Query metrics from a specific cluster.

        Args:
            cluster_type: Which cluster to query (mcc or mosk).
            metric_name: Metric name or PromQL query.
            labels: Label filters.
            time_range_minutes: Time range for query.
            step_seconds: Query resolution step.

        Returns:
            List of MetricSample objects.
        """
        adapter = self._mcc if cluster_type == "mcc" else self._mosk

        try:
            return await adapter.query_metrics(
                metric_name=metric_name,
                labels=labels,
                time_range_minutes=time_range_minutes,
                step_seconds=step_seconds,
            )
        except Exception as e:
            # P1 FIX: Log error level (not warning) when query fails
            # Previously returned empty list silently, hiding failures
            logger.error(
                "metric_query_failed",
                cluster_type=cluster_type,
                metric=metric_name,
                error=str(e),
                message="Returning empty result due to query failure - check cluster connectivity",
            )
            return []

    async def query_metrics_all_clusters(
        self,
        metric_name: str,
        labels: dict[str, str] | None = None,
        time_range_minutes: int = 60,
        step_seconds: int = 60,
    ) -> CombinedMetricResult:
        """Query metrics from both clusters.

        Args:
            metric_name: Metric name or PromQL query.
            labels: Label filters.
            time_range_minutes: Time range for query.
            step_seconds: Query resolution step.

        Returns:
            CombinedMetricResult with samples from both clusters.
        """
        start_time = time.monotonic()

        # Query both clusters concurrently
        mcc_task = self._query_metrics_single(
            adapter=self._mcc,
            cluster_type="mcc",
            metric_name=metric_name,
            labels=labels,
            time_range_minutes=time_range_minutes,
            step_seconds=step_seconds,
        )
        mosk_task = self._query_metrics_single(
            adapter=self._mosk,
            cluster_type="mosk",
            metric_name=metric_name,
            labels=labels,
            time_range_minutes=time_range_minutes,
            step_seconds=step_seconds,
        )

        mcc_result, mosk_result = await asyncio.gather(mcc_task, mosk_task, return_exceptions=False)

        # Combine samples
        combined_samples = mcc_result.samples + mosk_result.samples

        overall_success = mcc_result.success or mosk_result.success
        degraded = mcc_result.success != mosk_result.success

        duration_ms = (time.monotonic() - start_time) * 1000

        logger.info(
            "combined_metrics_retrieved",
            metric=metric_name,
            mcc_count=mcc_result.count,
            mosk_count=mosk_result.count,
            duration_ms=round(duration_ms, 2),
        )

        return CombinedMetricResult(
            mcc=mcc_result,
            mosk=mosk_result,
            combined_samples=combined_samples,
            total_samples=len(combined_samples),
            timestamp=datetime.now(UTC),
            overall_success=overall_success,
            degraded=degraded,
        )

    async def _query_metrics_single(
        self,
        adapter: StackLightAdapter,
        cluster_type: ClusterType,
        metric_name: str,
        labels: dict[str, str] | None,
        time_range_minutes: int,
        step_seconds: int,
    ) -> MetricQueryResult:
        """Query metrics from a single cluster with error handling."""
        start_time = time.monotonic()

        try:
            samples = await adapter.query_metrics(
                metric_name=metric_name,
                labels=labels,
                time_range_minutes=time_range_minutes,
                step_seconds=step_seconds,
            )

            # Convert to dicts
            samples_dicts = [s.to_dict() for s in samples]

            # Get unique metric names
            metric_names = list({s.metric_name for s in samples})

            duration_ms = (time.monotonic() - start_time) * 1000

            return MetricQueryResult(
                cluster_type=cluster_type,
                success=True,
                count=len(samples),
                query_duration_ms=round(duration_ms, 2),
                samples=samples_dicts,
                metric_names=metric_names,
            )

        except Exception as e:
            duration_ms = (time.monotonic() - start_time) * 1000
            error_msg = str(e)

            logger.warning(
                "metric_query_failed",
                cluster_type=cluster_type,
                metric=metric_name,
                error=error_msg,
            )

            return empty_metric_result(cluster_type, error_msg)

    # =========================================================================
    # Log Queries
    # =========================================================================

    async def query_logs(
        self,
        cluster_type: ClusterType,
        services: list[str] | None = None,
        severity: str | None = None,
        hosts: list[str] | None = None,
        time_range_minutes: int = 60,
        keywords: list[str] | None = None,
        limit: int = 100,
    ) -> list[LogEntry]:
        """Query logs from a specific cluster.

        Args:
            cluster_type: Which cluster to query (mcc or mosk).
            services: Filter by service names.
            severity: Minimum severity level.
            hosts: Filter by host names.
            time_range_minutes: Time range to query.
            keywords: Additional keywords to search.
            limit: Maximum logs to return.

        Returns:
            List of LogEntry objects.
        """
        adapter = self._mcc if cluster_type == "mcc" else self._mosk

        try:
            result = await adapter.query_logs(
                services=services,
                severity=severity,
                hosts=hosts,
                time_range_minutes=time_range_minutes,
                keywords=keywords,
                limit=limit,
            )
            # adapter.query_logs returns LogQueryResult, extract the logs list
            return result.logs
        except Exception as e:
            # P1 FIX: Log error level (not warning) when query fails
            # Previously returned empty list silently, hiding failures
            logger.error(
                "log_query_failed",
                cluster_type=cluster_type,
                services=services,
                error=str(e),
                message="Returning empty result due to query failure - check cluster connectivity",
            )
            return []

    async def query_logs_all_clusters(
        self,
        services: list[str] | None = None,
        severity: str | None = None,
        hosts: list[str] | None = None,
        time_range_minutes: int = 60,
        keywords: list[str] | None = None,
        limit: int = 100,
    ) -> CombinedLogResult:
        """Query logs from both clusters.

        Args:
            services: Filter by service names.
            severity: Minimum severity level.
            hosts: Filter by host names.
            time_range_minutes: Time range to query.
            keywords: Additional keywords to search.
            limit: Maximum logs per cluster.

        Returns:
            CombinedLogResult with logs from both clusters.
        """
        start_time = time.monotonic()

        # Query both clusters concurrently
        mcc_task = self._query_logs_single(
            adapter=self._mcc,
            cluster_type="mcc",
            services=services,
            severity=severity,
            hosts=hosts,
            time_range_minutes=time_range_minutes,
            keywords=keywords,
            limit=limit,
        )
        mosk_task = self._query_logs_single(
            adapter=self._mosk,
            cluster_type="mosk",
            services=services,
            severity=severity,
            hosts=hosts,
            time_range_minutes=time_range_minutes,
            keywords=keywords,
            limit=limit,
        )

        mcc_result, mosk_result = await asyncio.gather(mcc_task, mosk_task, return_exceptions=False)

        # Combine and sort logs by timestamp
        combined_logs = mcc_result.logs + mosk_result.logs
        combined_logs.sort(key=lambda x: x.get("timestamp", ""), reverse=True)

        overall_success = mcc_result.success or mosk_result.success
        degraded = mcc_result.success != mosk_result.success

        duration_ms = (time.monotonic() - start_time) * 1000

        logger.info(
            "combined_logs_retrieved",
            mcc_count=mcc_result.count,
            mosk_count=mosk_result.count,
            duration_ms=round(duration_ms, 2),
        )

        return CombinedLogResult(
            mcc=mcc_result,
            mosk=mosk_result,
            combined_logs=combined_logs,
            total_logs=len(combined_logs),
            timestamp=datetime.now(UTC),
            overall_success=overall_success,
            degraded=degraded,
        )

    async def _query_logs_single(
        self,
        adapter: StackLightAdapter,
        cluster_type: ClusterType,
        services: list[str] | None,
        severity: str | None,
        hosts: list[str] | None,
        time_range_minutes: int,
        keywords: list[str] | None,
        limit: int,
    ) -> LogQueryResult:
        """Query logs from a single cluster with error handling."""
        start_time = time.monotonic()

        try:
            log_result = await adapter.query_logs(
                services=services,
                severity=severity,
                hosts=hosts,
                time_range_minutes=time_range_minutes,
                keywords=keywords,
                limit=limit,
            )

            # Convert to dicts - access the .logs attribute from LogQueryResult
            log_entries = log_result.logs
            logs_dicts = [log.to_dict() for log in log_entries]

            # Get unique services
            unique_services = list({log.service for log in log_entries if log.service})

            # Count by severity
            error_count = sum(
                1 for log in log_entries if log.severity.value in ("error", "critical")
            )
            warning_count = sum(1 for log in log_entries if log.severity.value == "warning")

            duration_ms = (time.monotonic() - start_time) * 1000

            return LogQueryResult(
                cluster_type=cluster_type,
                success=True,
                count=log_result.returned_count,
                query_duration_ms=round(duration_ms, 2),
                logs=logs_dicts,
                services=unique_services,
                error_count=error_count,
                warning_count=warning_count,
            )

        except Exception as e:
            duration_ms = (time.monotonic() - start_time) * 1000
            error_msg = str(e)

            logger.warning(
                "log_query_failed",
                cluster_type=cluster_type,
                error=error_msg,
            )

            return empty_log_result(cluster_type, error_msg)

    # =========================================================================
    # Health Check
    # =========================================================================

    async def check_health(self) -> StackLightManagerHealth:
        """Check health of both StackLight deployments.

        Performs health checks on MCC and MOSK clusters concurrently,
        aggregating results into an overall health status.

        Returns:
            StackLightManagerHealth with overall and per-cluster status.
        """
        start_time = time.monotonic()

        # Check both clusters concurrently
        mcc_task = self._check_cluster_health(self._mcc, "mcc")
        mosk_task = self._check_cluster_health(self._mosk, "mosk")

        mcc_health, mosk_health = await asyncio.gather(mcc_task, mosk_task, return_exceptions=False)

        # Determine overall status
        if (
            mcc_health.status == ManagerHealthStatus.HEALTHY
            and mosk_health.status == ManagerHealthStatus.HEALTHY
        ):
            overall = ManagerHealthStatus.HEALTHY
        elif (
            mcc_health.status == ManagerHealthStatus.UNAVAILABLE
            and mosk_health.status == ManagerHealthStatus.UNAVAILABLE
        ):
            overall = ManagerHealthStatus.UNAVAILABLE
        else:
            overall = ManagerHealthStatus.DEGRADED

        # Generate recommendations
        recommendations = []
        if mcc_health.status != ManagerHealthStatus.HEALTHY:
            recommendations.append(
                f"MCC StackLight is {mcc_health.status.value}: {mcc_health.message}"
            )
        if mosk_health.status != ManagerHealthStatus.HEALTHY:
            recommendations.append(
                f"MOSK StackLight is {mosk_health.status.value}: {mosk_health.message}"
            )

        # Update internal state
        self._state.mcc_healthy = mcc_health.status == ManagerHealthStatus.HEALTHY
        self._state.mosk_healthy = mosk_health.status == ManagerHealthStatus.HEALTHY
        self._state.last_health_check = datetime.now(UTC)

        duration_ms = (time.monotonic() - start_time) * 1000

        logger.info(
            "health_check_completed",
            overall=overall.value,
            mcc=mcc_health.status.value,
            mosk=mosk_health.status.value,
            duration_ms=round(duration_ms, 2),
        )

        return StackLightManagerHealth(
            overall_status=overall,
            mcc=mcc_health,
            mosk=mosk_health,
            timestamp=datetime.now(UTC),
            recommendations=recommendations,
        )

    async def _check_cluster_health(
        self,
        adapter: StackLightAdapter,
        cluster_type: ClusterType,
    ) -> ClusterStackLightHealth:
        """Check health of a single cluster's StackLight deployment.

        Probes Prometheus, Alertmanager, and OpenSearch availability.

        Args:
            adapter: StackLight adapter to check.
            cluster_type: Cluster type for response.

        Returns:
            ClusterStackLightHealth with component status.
        """
        components: dict[str, ComponentHealth] = {}
        issues = []

        # Check Prometheus (via simple query)
        prom_health = await self._check_prometheus_health(adapter)
        components["prometheus"] = prom_health
        if prom_health.status != ComponentHealthStatus.HEALTHY:
            issues.append(f"prometheus: {prom_health.error_message or 'unavailable'}")

        # Check Alertmanager (via alert query)
        am_health = await self._check_alertmanager_health(adapter)
        components["alertmanager"] = am_health
        if am_health.status != ComponentHealthStatus.HEALTHY:
            issues.append(f"alertmanager: {am_health.error_message or 'unavailable'}")

        # Determine overall cluster health
        healthy_count = sum(
            1 for c in components.values() if c.status == ComponentHealthStatus.HEALTHY
        )

        if healthy_count == len(components):
            status = ManagerHealthStatus.HEALTHY
            message = "All components healthy"
        elif healthy_count > 0:
            status = ManagerHealthStatus.DEGRADED
            message = f"Issues: {'; '.join(issues)}"
        else:
            status = ManagerHealthStatus.UNAVAILABLE
            message = "All components unavailable"

        return ClusterStackLightHealth(
            cluster_type=cluster_type,
            status=status,
            components=components,
            last_check=datetime.now(UTC),
            message=message,
        )

    async def _check_prometheus_health(
        self,
        adapter: StackLightAdapter,
    ) -> ComponentHealth:
        """Check Prometheus health via simple query."""
        start_time = time.monotonic()

        try:
            # Simple query to check Prometheus is responding
            _samples, success = await adapter._query_prometheus_instant("up")
            duration_ms = (time.monotonic() - start_time) * 1000

            if success:
                return ComponentHealth(
                    component="prometheus",
                    status=ComponentHealthStatus.HEALTHY,
                    latency_ms=round(duration_ms, 2),
                )
            else:
                return ComponentHealth(
                    component="prometheus",
                    status=ComponentHealthStatus.UNAVAILABLE,
                    error_message="Query returned no results",
                    latency_ms=round(duration_ms, 2),
                )

        except Exception as e:
            duration_ms = (time.monotonic() - start_time) * 1000
            return ComponentHealth(
                component="prometheus",
                status=ComponentHealthStatus.UNAVAILABLE,
                error_message=str(e),
                latency_ms=round(duration_ms, 2),
            )

    async def _check_alertmanager_health(
        self,
        adapter: StackLightAdapter,
    ) -> ComponentHealth:
        """Check Alertmanager health via alert query."""
        start_time = time.monotonic()

        try:
            # Query alerts to check Alertmanager is responding
            # get_alerts returns a list of Alert objects; if it succeeds, Alertmanager is healthy
            await adapter.get_alerts(limit=1)
            duration_ms = (time.monotonic() - start_time) * 1000

            # Success is determined by whether we got a response without exception
            return ComponentHealth(
                component="alertmanager",
                status=ComponentHealthStatus.HEALTHY,
                latency_ms=round(duration_ms, 2),
            )

        except Exception as e:
            duration_ms = (time.monotonic() - start_time) * 1000
            return ComponentHealth(
                component="alertmanager",
                status=ComponentHealthStatus.UNAVAILABLE,
                error_message=str(e),
                latency_ms=round(duration_ms, 2),
            )


# =============================================================================
# Singleton Management
# =============================================================================

_stacklight_manager: StackLightManager | None = None


async def get_stacklight_manager(
    mcc_direct_client: DirectStackLightClient,
    mosk_direct_client: DirectStackLightClient,
    settings: Settings | None = None,
) -> StackLightManager:
    """Get or create the StackLight manager singleton.

    Creates a new StackLightManager if one doesn't exist, or returns
    the existing instance. The manager is connected automatically.

    Args:
        mcc_direct_client: DirectStackLightClient for MCC cluster (required).
        mosk_direct_client: DirectStackLightClient for MOSK cluster (required).
        settings: Optional application settings.

    Returns:
        Connected StackLightManager instance.
    """
    global _stacklight_manager

    if _stacklight_manager is None:
        _stacklight_manager = StackLightManager(
            mcc_direct_client=mcc_direct_client,
            mosk_direct_client=mosk_direct_client,
            settings=settings,
        )
        await _stacklight_manager.connect()

    return _stacklight_manager


def reset_stacklight_manager() -> None:
    """Reset the StackLight manager singleton (for testing)."""
    global _stacklight_manager
    _stacklight_manager = None
