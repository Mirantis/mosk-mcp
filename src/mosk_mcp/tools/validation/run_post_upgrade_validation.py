"""Unified post-upgrade validation tool for MOSK.

Runs a comprehensive validation suite to verify OpenStack deployment
health and functionality after upgrades or maintenance operations.

Validation Tiers:
- Tier 1: Infrastructure Health (Kubernetes, Ceph, OpenStack status)
- Tier 2: Service Availability (API endpoint probing)
- Tier 3: Functional Smoke Tests (VM lifecycle, storage operations)
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from mosk_mcp.observability.logging import get_logger
from mosk_mcp.tools.cluster_health.get_ceph_health import (
    GetCephHealthInput,
    get_ceph_health,
)
from mosk_mcp.tools.cluster_health.get_kubernetes_health import (
    GetKubernetesHealthInput,
    get_kubernetes_health,
)
from mosk_mcp.tools.cluster_health.get_openstack_health import (
    GetOpenStackHealthInput,
    get_openstack_health,
)
from mosk_mcp.tools.common import (
    HealthStatus,
    TierResult,
    TierResultOutput,
    ValidationLevel,
    ValidationStatus,
)
from mosk_mcp.tools.validation.check_service_availability import (
    CheckServiceAvailabilityInput,
    check_service_availability,
)
from mosk_mcp.tools.validation.run_smoke_test import (
    RunSmokeTestInput,
    SmokeTestStatus,
    SmokeTestType,
    run_smoke_test,
)


if TYPE_CHECKING:
    from mosk_mcp.adapters.kubernetes import KubernetesAdapter
    from mosk_mcp.adapters.openstack import OpenStackAdapter


logger = get_logger(__name__)


class RunPostUpgradeValidationInput(BaseModel):
    """Input for run_post_upgrade_validation tool."""

    level: str = Field(
        default="standard",
        description=(
            "Validation level: quick (infrastructure only), "
            "standard (+ API probing), comprehensive (+ smoke tests)"
        ),
    )
    osdpl_name: str | None = Field(
        default=None,
        description="OpenStackDeployment name. Auto-discovered if not provided.",
    )
    namespace: str = Field(
        default="openstack",
        description="Kubernetes namespace where OpenStack is deployed",
    )
    include_smoke_tests: list[str] | None = Field(
        default=None,
        description=(
            "Smoke tests to run (for comprehensive level): "
            "vm_lifecycle, storage_operations, full_stack. "
            "If not specified, runs vm_lifecycle only."
        ),
    )
    smoke_test_image: str | None = Field(
        default=None,
        description="Image name for smoke tests (auto-discovered if not provided)",
    )
    smoke_test_flavor: str | None = Field(
        default=None,
        description="Flavor name for smoke tests (auto-discovered if not provided)",
    )
    smoke_test_network: str | None = Field(
        default=None,
        description="Network name for smoke tests (auto-discovered if not provided)",
    )
    cleanup_smoke_tests: bool = Field(
        default=True,
        description="Clean up resources created during smoke tests",
    )
    timeout_seconds: int = Field(
        default=600,
        description="Total validation timeout in seconds",
        ge=60,
        le=1800,
    )


class RunPostUpgradeValidationOutput(BaseModel):
    """Output for run_post_upgrade_validation tool."""

    overall_status: str = Field(description="Overall validation status")
    validation_level: str = Field(description="Validation level run")
    tiers_run: int = Field(description="Number of tiers executed")
    tiers_passed: int = Field(description="Number of tiers passed")
    tiers_failed: int = Field(description="Number of tiers failed")
    tier_results: list[TierResultOutput] = Field(description="Per-tier results")
    timestamp: str = Field(description="Validation timestamp (ISO format)")
    duration_seconds: float = Field(description="Total validation duration")
    osdpl_name: str | None = Field(default=None, description="OSDPL name validated")
    from_version: str | None = Field(default=None, description="OpenStack version before upgrade")
    to_version: str | None = Field(default=None, description="OpenStack version after upgrade")
    recommendations: list[str] = Field(
        default_factory=list,
        description="Recommendations based on validation results",
    )
    summary: str = Field(default="", description="Human-readable summary")


async def run_post_upgrade_validation(
    k8s_adapter: KubernetesAdapter,
    openstack_adapter: OpenStackAdapter,
    input_data: RunPostUpgradeValidationInput,
) -> RunPostUpgradeValidationOutput:
    """Run comprehensive post-upgrade validation.

    Executes validation tiers based on the specified level:
    - quick: Tier 1 only (infrastructure health)
    - standard: Tier 1 + Tier 2 (+ service availability)
    - comprehensive: All tiers (+ functional smoke tests)

    Args:
        k8s_adapter: Kubernetes adapter for cluster access.
        openstack_adapter: OpenStack adapter for API calls.
        input_data: Validation configuration.

    Returns:
        Comprehensive validation results.
    """
    start_time = datetime.now(UTC)

    # Parse validation level
    try:
        level = ValidationLevel(input_data.level)
    except ValueError:
        return RunPostUpgradeValidationOutput(
            overall_status=ValidationStatus.ERROR.value,
            validation_level=input_data.level,
            tiers_run=0,
            tiers_passed=0,
            tiers_failed=0,
            tier_results=[],
            timestamp=start_time.isoformat(),
            duration_seconds=0,
            recommendations=[f"Invalid validation level: {input_data.level}"],
            summary=f"Validation failed: invalid level '{input_data.level}'",
        )

    logger.info(
        "starting_post_upgrade_validation",
        level=level.value,
        osdpl_name=input_data.osdpl_name,
        namespace=input_data.namespace,
    )

    # Auto-discover OSDPL if not provided
    osdpl_name = input_data.osdpl_name
    from_version: str | None = None
    to_version: str | None = None

    if not osdpl_name:
        try:
            osdpls = await k8s_adapter.list_openstack_deployments(namespace=input_data.namespace)
            if osdpls:
                osdpl_name = osdpls[0].get("metadata", {}).get("name")
                spec = osdpls[0].get("spec", {})
                status = osdpls[0].get("status", {})
                # Note: field name uses snake_case in the CRD
                to_version = spec.get("openstack_version")
                from_version = status.get("openstack_version", to_version)
                logger.info(
                    "auto_discovered_osdpl",
                    osdpl_name=osdpl_name,
                    from_version=from_version,
                    to_version=to_version,
                )
        except Exception as e:
            logger.warning("osdpl_auto_discovery_failed", error=str(e))

    # Run validation tiers
    tier_results: list[TierResult] = []

    try:
        # Tier 1: Infrastructure Health (always run)
        tier1_result = await asyncio.wait_for(
            _run_tier1_infrastructure(k8s_adapter, osdpl_name, input_data.namespace),
            timeout=input_data.timeout_seconds // 3,
        )
        tier_results.append(tier1_result)

        # Tier 2: Service Availability (standard and comprehensive)
        if level in (ValidationLevel.STANDARD, ValidationLevel.COMPREHENSIVE):
            tier2_result = await asyncio.wait_for(
                _run_tier2_service_availability(openstack_adapter),
                timeout=input_data.timeout_seconds // 3,
            )
            tier_results.append(tier2_result)

        # Tier 3: Functional Smoke Tests (comprehensive only)
        if level == ValidationLevel.COMPREHENSIVE:
            tier3_result = await asyncio.wait_for(
                _run_tier3_smoke_tests(openstack_adapter, input_data),
                timeout=input_data.timeout_seconds // 2,
            )
            tier_results.append(tier3_result)

    except TimeoutError:
        tier_results.append(
            TierResult(
                tier=len(tier_results) + 1,
                name="timeout",
                status=ValidationStatus.ERROR,
                error_message=f"Validation timed out after {input_data.timeout_seconds}s",
            )
        )

    # Calculate summary
    tiers_run = len(tier_results)
    tiers_passed = sum(
        1
        for t in tier_results
        if t.status in (ValidationStatus.PASSED, ValidationStatus.PASSED_WITH_WARNINGS)
    )
    tiers_failed = sum(
        1 for t in tier_results if t.status in (ValidationStatus.FAILED, ValidationStatus.ERROR)
    )

    # Determine overall status
    if tiers_failed > 0:
        overall_status = ValidationStatus.FAILED
    elif any(t.status == ValidationStatus.PASSED_WITH_WARNINGS for t in tier_results):
        overall_status = ValidationStatus.PASSED_WITH_WARNINGS
    else:
        overall_status = ValidationStatus.PASSED

    # Generate recommendations
    recommendations = _generate_recommendations(tier_results, level)

    # Generate summary
    summary = _generate_summary(overall_status, tiers_run, tiers_passed, tiers_failed, level)

    end_time = datetime.now(UTC)
    duration = (end_time - start_time).total_seconds()

    logger.info(
        "post_upgrade_validation_complete",
        overall_status=overall_status.value,
        tiers_run=tiers_run,
        tiers_passed=tiers_passed,
        tiers_failed=tiers_failed,
        duration=duration,
    )

    return RunPostUpgradeValidationOutput(
        overall_status=overall_status.value,
        validation_level=level.value,
        tiers_run=tiers_run,
        tiers_passed=tiers_passed,
        tiers_failed=tiers_failed,
        tier_results=[
            TierResultOutput(
                tier=t.tier,
                name=t.name,
                status=t.status.value,
                checks_passed=t.checks_passed,
                checks_failed=t.checks_failed,
                checks_skipped=t.checks_skipped,
                duration_seconds=round(t.duration_seconds, 2),
                details=t.details,
                error_message=t.error_message,
            )
            for t in tier_results
        ],
        timestamp=start_time.isoformat(),
        duration_seconds=round(duration, 2),
        osdpl_name=osdpl_name,
        from_version=from_version,
        to_version=to_version,
        recommendations=recommendations,
        summary=summary,
    )


async def _run_tier1_infrastructure(
    k8s_adapter: KubernetesAdapter,
    osdpl_name: str | None,
    namespace: str,
) -> TierResult:
    """Run Tier 1: Infrastructure Health checks.

    Uses existing cluster health tools to check:
    - Kubernetes cluster health
    - OpenStack deployment status
    - Ceph storage health (if applicable)
    """
    start = datetime.now(UTC)
    result = TierResult(
        tier=1,
        name="Infrastructure Health",
        status=ValidationStatus.PASSED,
        details={},
    )

    checks_passed = 0
    checks_failed = 0

    try:
        # Check Kubernetes health using existing tool
        try:
            k8s_input = GetKubernetesHealthInput(
                include_node_details=True,
                include_system_pods=False,  # Keep it quick
            )
            k8s_health = await get_kubernetes_health(k8s_adapter, k8s_input)

            result.details["kubernetes"] = {
                "health_status": k8s_health.health.value,
                "health_score": k8s_health.score,
                "nodes_total": k8s_health.total_nodes,
                "nodes_ready": k8s_health.ready_nodes,
                "nodes_not_ready": k8s_health.not_ready_nodes,
            }

            if k8s_health.health == HealthStatus.HEALTHY:
                checks_passed += 1
            elif k8s_health.health == HealthStatus.DEGRADED:
                checks_passed += 1  # Still passing but with warning
            else:
                checks_failed += 1
        except Exception as e:
            checks_failed += 1
            result.details["kubernetes"] = {"status": "error", "error": str(e)}

        # Check OpenStack deployment status using existing tool
        if osdpl_name:
            try:
                os_input = GetOpenStackHealthInput(
                    osdpl_name=osdpl_name,
                    namespace=namespace,
                    include_services=True,
                    include_endpoints=False,  # Keep it quick
                )
                os_health = await get_openstack_health(k8s_adapter, os_input)

                result.details["openstack"] = {
                    "osdpl_name": osdpl_name,
                    "health_status": os_health.control_plane_health.value,
                    "health_score": os_health.control_plane_score,
                    "osdpl_phase": os_health.osdpl_phase,
                }

                if os_health.control_plane_health == HealthStatus.HEALTHY:
                    checks_passed += 1
                elif os_health.control_plane_health == HealthStatus.DEGRADED:
                    checks_passed += 1  # Still passing but with warning
                else:
                    checks_failed += 1
            except Exception as e:
                checks_failed += 1
                result.details["openstack"] = {"status": "error", "error": str(e)}
        else:
            result.details["openstack"] = {"status": "skipped", "reason": "no_osdpl"}
            result.checks_skipped += 1

        # Check Ceph health using existing tool
        try:
            ceph_input = GetCephHealthInput(
                include_osd_details=False,
                include_pool_details=False,
            )
            ceph_health = await get_ceph_health(k8s_adapter, ceph_input)

            result.details["ceph"] = {
                "health_status": ceph_health.health.value,
                "health_score": ceph_health.score,
                "ceph_status": ceph_health.ceph_health,
                "osds_total": ceph_health.osds_total,
                "osds_up": ceph_health.osds_up,
            }

            if ceph_health.health == HealthStatus.HEALTHY:
                checks_passed += 1
            elif ceph_health.health == HealthStatus.DEGRADED:
                checks_passed += 1  # Still passing but with warning
            else:
                checks_failed += 1
        except Exception as e:
            logger.warning(
                "post_upgrade_validation_ceph_check_failed",
                error=str(e),
                error_type=type(e).__name__,
            )
            result.details["ceph"] = {"status": "skipped", "reason": "not_available"}
            result.checks_skipped += 1

        # Determine tier status
        result.checks_passed = checks_passed
        result.checks_failed = checks_failed

        if checks_failed > 0:
            result.status = ValidationStatus.FAILED
        elif result.checks_skipped > 0:
            result.status = ValidationStatus.PASSED_WITH_WARNINGS

    except Exception as e:
        result.status = ValidationStatus.ERROR
        result.error_message = str(e)

    result.duration_seconds = (datetime.now(UTC) - start).total_seconds()
    return result


async def _run_tier2_service_availability(
    openstack_adapter: OpenStackAdapter,
) -> TierResult:
    """Run Tier 2: Service Availability checks.

    Probes OpenStack APIs to verify services are responding.
    """
    start = datetime.now(UTC)
    result = TierResult(
        tier=2,
        name="Service Availability",
        status=ValidationStatus.PASSED,
        details={},
    )

    try:
        check_input = CheckServiceAvailabilityInput(
            services=None,  # Check all core services
            include_agents=True,
            timeout_seconds=30,
        )
        check_result = await check_service_availability(openstack_adapter, check_input)

        result.checks_passed = check_result.services_healthy
        result.checks_failed = check_result.services_unavailable
        result.checks_skipped = 0

        result.details = {
            "overall_status": check_result.overall_status,
            "services_checked": check_result.services_checked,
            "services_healthy": check_result.services_healthy,
            "services_degraded": check_result.services_degraded,
            "services_unavailable": check_result.services_unavailable,
            "service_results": {
                s.service_name: {
                    "status": s.status,
                    "response_time_ms": s.response_time_ms,
                    "agents_up": s.agents_up,
                    "agent_count": s.agent_count,
                }
                for s in check_result.results
            },
        }

        if check_result.services_unavailable > 0:
            result.status = ValidationStatus.FAILED
            result.error_message = f"{check_result.services_unavailable} services unavailable"
        elif check_result.services_degraded > 0:
            result.status = ValidationStatus.PASSED_WITH_WARNINGS
            result.error_message = f"{check_result.services_degraded} services degraded"

    except Exception as e:
        result.status = ValidationStatus.ERROR
        result.error_message = str(e)

    result.duration_seconds = (datetime.now(UTC) - start).total_seconds()
    return result


async def _run_tier3_smoke_tests(
    openstack_adapter: OpenStackAdapter,
    input_data: RunPostUpgradeValidationInput,
) -> TierResult:
    """Run Tier 3: Functional Smoke Tests.

    Executes smoke tests to verify OpenStack operations work correctly.
    """
    start = datetime.now(UTC)
    result = TierResult(
        tier=3,
        name="Functional Smoke Tests",
        status=ValidationStatus.PASSED,
        details={},
    )

    # Determine which tests to run
    tests_to_run = input_data.include_smoke_tests or ["vm_lifecycle"]

    try:
        test_results: dict[str, dict[str, Any]] = {}

        for test_name in tests_to_run:
            try:
                test_type = SmokeTestType(test_name)
            except ValueError:
                result.checks_skipped += 1
                test_results[test_name] = {
                    "status": "skipped",
                    "error": f"Unknown test type: {test_name}",
                }
                continue

            test_input = RunSmokeTestInput(
                test_type=test_type.value,
                image_name=input_data.smoke_test_image,
                flavor_name=input_data.smoke_test_flavor,
                network_name=input_data.smoke_test_network,
                cleanup=input_data.cleanup_smoke_tests,
                timeout_seconds=180,  # Per-test timeout
            )

            test_result = await run_smoke_test(openstack_adapter, test_input)

            test_results[test_name] = {
                "status": test_result.status,
                "duration_seconds": test_result.duration_seconds,
                "steps_passed": sum(
                    1 for s in test_result.steps if s.status == SmokeTestStatus.PASSED.value
                ),
                "steps_failed": sum(
                    1 for s in test_result.steps if s.status == SmokeTestStatus.FAILED.value
                ),
                "resources_leaked": test_result.resources_leaked,
            }

            if test_result.status == SmokeTestStatus.PASSED.value:
                result.checks_passed += 1
            elif test_result.status == SmokeTestStatus.FAILED.value:
                result.checks_failed += 1
            else:
                result.checks_skipped += 1

        result.details = {
            "tests_run": tests_to_run,
            "test_results": test_results,
        }

        if result.checks_failed > 0:
            result.status = ValidationStatus.FAILED
            result.error_message = f"{result.checks_failed} smoke tests failed"
        elif result.checks_skipped > 0:
            result.status = ValidationStatus.PASSED_WITH_WARNINGS

    except Exception as e:
        result.status = ValidationStatus.ERROR
        result.error_message = str(e)

    result.duration_seconds = (datetime.now(UTC) - start).total_seconds()
    return result


def _generate_recommendations(
    tier_results: list[TierResult],
    level: ValidationLevel,
) -> list[str]:
    """Generate recommendations based on validation results.

    Args:
        tier_results: Results from each tier.
        level: Validation level run.

    Returns:
        List of recommendations.
    """
    recommendations = []

    for tier in tier_results:
        if tier.status == ValidationStatus.FAILED:
            if tier.tier == 1:
                recommendations.append(
                    f"CRITICAL: {tier.name} failed - "
                    "resolve infrastructure issues before proceeding"
                )
            elif tier.tier == 2:
                recommendations.append(
                    f"WARNING: {tier.name} failed - check OpenStack service pods and logs"
                )
            elif tier.tier == 3:
                recommendations.append(
                    f"WARNING: {tier.name} failed - functional operations may be impaired"
                )

        elif tier.status == ValidationStatus.PASSED_WITH_WARNINGS:
            recommendations.append(
                f"INFO: {tier.name} passed with warnings - review details for potential issues"
            )

    # Level-specific recommendations
    if level == ValidationLevel.QUICK:
        recommendations.append(
            "Consider running 'standard' or 'comprehensive' validation "
            "for complete post-upgrade verification"
        )

    if not recommendations:
        recommendations.append("All validation checks passed - upgrade completed successfully")

    return recommendations


def _generate_summary(
    status: ValidationStatus,
    tiers_run: int,
    tiers_passed: int,
    tiers_failed: int,
    level: ValidationLevel,
) -> str:
    """Generate human-readable summary.

    Args:
        status: Overall validation status.
        tiers_run: Number of tiers run.
        tiers_passed: Number of tiers passed.
        tiers_failed: Number of tiers failed.
        level: Validation level.

    Returns:
        Summary string.
    """
    if status == ValidationStatus.PASSED:
        return (
            f"Post-upgrade validation PASSED ({level.value} level): "
            f"{tiers_passed}/{tiers_run} tiers passed. "
            "OpenStack deployment is healthy and operational."
        )
    elif status == ValidationStatus.PASSED_WITH_WARNINGS:
        return (
            f"Post-upgrade validation PASSED WITH WARNINGS ({level.value} level): "
            f"{tiers_passed}/{tiers_run} tiers passed. "
            "Review warnings before production use."
        )
    elif status == ValidationStatus.FAILED:
        return (
            f"Post-upgrade validation FAILED ({level.value} level): "
            f"{tiers_failed}/{tiers_run} tiers failed. "
            "Address issues before using the deployment."
        )
    else:
        return (
            f"Post-upgrade validation ERROR ({level.value} level): "
            "Validation encountered an error. Check logs for details."
        )
