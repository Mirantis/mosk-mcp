"""Post-MOSK platform upgrade validation tool.

Runs a comprehensive validation suite to verify MOSK platform (Kubernetes,
LCM, system components) health after platform upgrades.

Validation Tiers:
- Tier 1: Kubernetes Infrastructure (nodes, system pods, API)
- Tier 2: Platform Services (LCM, StackLight, MetalLB, Calico)
- Tier 3: OpenStack Health (if deployed - OSDPL status, services)

This is different from run_post_upgrade_validation which validates
OpenStack services after OpenStack upgrades. This tool validates the
underlying platform after MOSK release upgrades.
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
    TierResult,
    TierResultOutput,
    ValidationLevel,
    ValidationStatus,
)


if TYPE_CHECKING:
    from mosk_mcp.adapters.kubernetes import KubernetesAdapter


logger = get_logger(__name__)


class RunMoskPlatformValidationInput(BaseModel):
    """Input for run_mosk_platform_validation tool."""

    level: str = Field(
        default="standard",
        description=(
            "Validation level: quick (Kubernetes only), "
            "standard (+ platform services), comprehensive (+ OpenStack)"
        ),
    )
    cluster_name: str | None = Field(
        default=None,
        description="Cluster name to validate. Auto-discovered if not provided.",
    )
    cluster_namespace: str = Field(
        default="lab",
        description="Namespace where Cluster CR exists (on MCC)",
    )
    openstack_namespace: str = Field(
        default="openstack",
        description="Namespace where OpenStack is deployed (on MOSK)",
    )
    timeout_seconds: int = Field(
        default=300,
        description="Total validation timeout in seconds",
        ge=60,
        le=900,
    )


class RunMoskPlatformValidationOutput(BaseModel):
    """Output for run_mosk_platform_validation tool."""

    overall_status: str = Field(description="Overall validation status")
    validation_level: str = Field(description="Validation level run")
    tiers_run: int = Field(description="Number of tiers executed")
    tiers_passed: int = Field(description="Number of tiers passed")
    tiers_failed: int = Field(description="Number of tiers failed")
    tier_results: list[TierResultOutput] = Field(description="Per-tier results")
    timestamp: str = Field(description="Validation timestamp (ISO format)")
    duration_seconds: float = Field(description="Total validation duration")
    cluster_name: str | None = Field(default=None, description="Cluster name validated")
    from_release: str | None = Field(default=None, description="MOSK release before upgrade")
    to_release: str | None = Field(default=None, description="MOSK release after upgrade (current)")
    kubernetes_version: str | None = Field(default=None, description="Current Kubernetes version")
    recommendations: list[str] = Field(
        default_factory=list,
        description="Recommendations based on validation results",
    )
    summary: str = Field(default="", description="Human-readable summary")


async def run_mosk_platform_validation(
    mcc_adapter: KubernetesAdapter,
    mosk_adapter: KubernetesAdapter,
    input_data: RunMoskPlatformValidationInput,
) -> RunMoskPlatformValidationOutput:
    """Run comprehensive post-MOSK platform upgrade validation.

    Validates the MOSK platform (Kubernetes, LCM, system components) after
    a platform upgrade. This is different from OpenStack upgrade validation.

    Executes validation tiers based on the specified level:
    - quick: Tier 1 only (Kubernetes infrastructure)
    - standard: Tier 1 + Tier 2 (+ platform services)
    - comprehensive: All tiers (+ OpenStack health if deployed)

    Args:
        mcc_adapter: Kubernetes adapter for MCC management cluster.
        mosk_adapter: Kubernetes adapter for MOSK child cluster.
        input_data: Validation configuration.

    Returns:
        Comprehensive validation results.
    """
    start_time = datetime.now(UTC)

    # Parse validation level
    try:
        level = ValidationLevel(input_data.level)
    except ValueError:
        return RunMoskPlatformValidationOutput(
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
        "starting_mosk_platform_validation",
        level=level.value,
        cluster_name=input_data.cluster_name,
        cluster_namespace=input_data.cluster_namespace,
    )

    # Get cluster info from MCC
    cluster_name = input_data.cluster_name
    from_release: str | None = None
    to_release: str | None = None
    kubernetes_version: str | None = None

    try:
        if cluster_name:
            cluster = await mcc_adapter.get_cluster(
                name=cluster_name,
                namespace=input_data.cluster_namespace,
            )
        else:
            # Auto-discover cluster
            clusters = await mcc_adapter.list_clusters(
                namespace=input_data.cluster_namespace,
            )
            # Filter for MOSK clusters (release starts with 'mosk-')
            mosk_clusters = [
                c
                for c in clusters
                if c.get("spec", {})
                .get("providerSpec", {})
                .get("value", {})
                .get("release", "")
                .startswith("mosk-")
            ]
            if mosk_clusters:
                cluster = mosk_clusters[0]
                cluster_name = cluster.get("metadata", {}).get("name")
            else:
                cluster = None

        if cluster:
            provider_spec = cluster.get("spec", {}).get("providerSpec", {}).get("value", {})
            to_release = provider_spec.get("release")

            # Try to get from_release from ClusterUpgradeStatus
            try:
                upgrade_statuses = await mcc_adapter.list_cluster_upgrade_statuses(
                    namespace=input_data.cluster_namespace,
                )
                # Filter for this cluster's upgrade statuses
                cluster_statuses = [
                    s
                    for s in upgrade_statuses
                    if any(
                        ref.get("kind") == "Cluster" and ref.get("name") == cluster_name
                        for ref in s.get("metadata", {}).get("ownerReferences", [])
                    )
                ]
                if cluster_statuses:
                    # Get the most recent one
                    cluster_statuses.sort(
                        key=lambda x: x.get("metadata", {}).get("creationTimestamp", ""),
                        reverse=True,
                    )
                    from_release = cluster_statuses[0].get("fromRelease")
            except Exception as e:
                logger.warning("failed_to_get_upgrade_status", error=str(e))

            logger.info(
                "cluster_info_discovered",
                cluster_name=cluster_name,
                from_release=from_release,
                to_release=to_release,
            )
    except Exception as e:
        logger.warning("cluster_discovery_failed", error=str(e))

    # Get Kubernetes version from MOSK cluster
    try:
        nodes = await mosk_adapter.list(kind="Node")
        if nodes:
            kubernetes_version = (
                nodes[0].get("status", {}).get("nodeInfo", {}).get("kubeletVersion")
            )
    except Exception as e:
        logger.warning("failed_to_get_k8s_version", error=str(e))

    # Run validation tiers
    tier_results: list[TierResult] = []

    try:
        # Tier 1: Kubernetes Infrastructure (always run)
        tier1_result = await asyncio.wait_for(
            _run_tier1_kubernetes_infrastructure(mosk_adapter),
            timeout=input_data.timeout_seconds // 3,
        )
        tier_results.append(tier1_result)

        # Tier 2: Platform Services (standard and comprehensive)
        if level in (ValidationLevel.STANDARD, ValidationLevel.COMPREHENSIVE):
            tier2_result = await asyncio.wait_for(
                _run_tier2_platform_services(mosk_adapter),
                timeout=input_data.timeout_seconds // 3,
            )
            tier_results.append(tier2_result)

        # Tier 3: OpenStack Health (comprehensive only)
        if level == ValidationLevel.COMPREHENSIVE:
            tier3_result = await asyncio.wait_for(
                _run_tier3_openstack_health(mosk_adapter, input_data.openstack_namespace),
                timeout=input_data.timeout_seconds // 3,
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
    recommendations = _generate_platform_recommendations(tier_results, level)

    # Generate summary
    summary = _generate_platform_summary(
        overall_status, tiers_run, tiers_passed, tiers_failed, level, to_release
    )

    end_time = datetime.now(UTC)
    duration = (end_time - start_time).total_seconds()

    logger.info(
        "mosk_platform_validation_complete",
        overall_status=overall_status.value,
        tiers_run=tiers_run,
        tiers_passed=tiers_passed,
        tiers_failed=tiers_failed,
        duration=duration,
    )

    return RunMoskPlatformValidationOutput(
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
        cluster_name=cluster_name,
        from_release=from_release,
        to_release=to_release,
        kubernetes_version=kubernetes_version,
        recommendations=recommendations,
        summary=summary,
    )


async def _run_tier1_kubernetes_infrastructure(
    mosk_adapter: KubernetesAdapter,
) -> TierResult:
    """Run Tier 1: Kubernetes Infrastructure checks.

    Validates:
    - All nodes Ready
    - System pods healthy (kube-system)
    - API server responsive
    - Ceph storage healthy
    """
    start = datetime.now(UTC)
    result = TierResult(
        tier=1,
        name="Kubernetes Infrastructure",
        status=ValidationStatus.PASSED,
        details={},
    )

    checks_passed = 0
    checks_failed = 0

    try:
        # Check Kubernetes health
        try:
            k8s_input = GetKubernetesHealthInput(
                include_node_details=True,
                include_system_pods=True,
            )
            k8s_health = await get_kubernetes_health(mosk_adapter, k8s_input)

            result.details["kubernetes"] = {
                "health": k8s_health.health.value,
                "score": k8s_health.score,
                "nodes_total": k8s_health.total_nodes,
                "nodes_ready": k8s_health.ready_nodes,
                "nodes_not_ready": k8s_health.not_ready_nodes,
                "api_server_healthy": k8s_health.api_server_healthy,
            }

            # Add node details if any are not ready
            if k8s_health.not_ready_nodes > 0 and k8s_health.nodes:
                not_ready_nodes = [n for n in k8s_health.nodes if not n.ready]
                result.details["kubernetes"]["not_ready_node_names"] = [
                    n.name for n in not_ready_nodes[:5]
                ]

            if k8s_health.health.value == "healthy":
                checks_passed += 1
            elif k8s_health.health.value == "degraded":
                checks_passed += 1  # Pass with warning
            else:
                checks_failed += 1
        except Exception as e:
            checks_failed += 1
            result.details["kubernetes"] = {"status": "error", "error": str(e)}

        # Check Ceph health
        try:
            ceph_input = GetCephHealthInput(
                include_osd_details=True,
                include_pool_details=False,
            )
            ceph_health = await get_ceph_health(mosk_adapter, ceph_input)

            result.details["ceph"] = {
                "health": ceph_health.health.value,
                "score": ceph_health.score,
                "ceph_health": ceph_health.ceph_health,  # Native HEALTH_OK/WARN/ERR
                "osds_total": ceph_health.osds_total,
                "osds_up": ceph_health.osds_up,
                "osds_in": ceph_health.osds_in,
            }

            if ceph_health.health.value == "healthy":
                checks_passed += 1
            elif ceph_health.health.value == "degraded":
                checks_passed += 1  # Pass with warning
            else:
                checks_failed += 1
        except Exception as e:
            logger.warning(
                "platform_validation_ceph_check_failed",
                error=str(e),
                error_type=type(e).__name__,
            )
            result.details["ceph"] = {"status": "skipped", "reason": "not_available"}
            result.checks_skipped += 1

        # Check critical system namespaces
        # Note: lcm namespace naming varies - try multiple possibilities
        try:
            system_namespaces = ["kube-system", "kaas"]
            # Try to find LCM namespace dynamically
            lcm_namespaces = ["lcm-system", "ceph-lcm-mirantis", "lcm"]
            namespace_health: dict[str, Any] = {}

            for ns in system_namespaces:
                try:
                    pods = await mosk_adapter.list(kind="Pod", namespace=ns)
                    total = len(pods)
                    running = sum(1 for p in pods if p.get("status", {}).get("phase") == "Running")
                    failed = sum(
                        1 for p in pods if p.get("status", {}).get("phase") in ("Failed", "Unknown")
                    )
                    namespace_health[ns] = {
                        "pods_total": total,
                        "pods_running": running,
                        "pods_failed": failed,
                        "healthy": failed == 0 and (running == total or running > 0),
                    }
                except Exception as e:
                    logger.debug(
                        "platform_validation_namespace_check_failed",
                        namespace=ns,
                        error=str(e),
                    )
                    namespace_health[ns] = {"status": "error"}

            # Check LCM namespace (try multiple names)
            lcm_found = False
            lcm_errors: list[str] = []
            for lcm_ns in lcm_namespaces:
                try:
                    pods = await mosk_adapter.list(kind="Pod", namespace=lcm_ns)
                    if pods:
                        total = len(pods)
                        running = sum(
                            1 for p in pods if p.get("status", {}).get("phase") == "Running"
                        )
                        namespace_health[f"lcm ({lcm_ns})"] = {
                            "pods_total": total,
                            "pods_running": running,
                            "healthy": running > 0,
                        }
                        lcm_found = True
                        break
                except Exception as e:
                    logger.debug(
                        "lcm_namespace_check_failed",
                        namespace=lcm_ns,
                        error=str(e),
                    )
                    lcm_errors.append(f"{lcm_ns}: {e}")
                    continue

            if not lcm_found:
                if lcm_errors and len(lcm_errors) == len(lcm_namespaces):
                    # All attempts failed with errors - API connectivity issue
                    namespace_health["lcm"] = {
                        "status": "query_failed",
                        "tried": lcm_namespaces,
                        "errors": lcm_errors,
                    }
                else:
                    namespace_health["lcm"] = {"status": "not_found", "tried": lcm_namespaces}

            result.details["system_namespaces"] = namespace_health

            # Check if core namespaces (kube-system, kaas) are healthy
            core_healthy = all(
                ns_info.get("healthy", False)
                for ns, ns_info in namespace_health.items()
                if ns in ("kube-system", "kaas")
                and isinstance(ns_info, dict)
                and "healthy" in ns_info
            )
            if core_healthy:
                checks_passed += 1
            else:
                checks_failed += 1

        except Exception as e:
            checks_failed += 1
            result.details["system_namespaces"] = {"status": "error", "error": str(e)}

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


async def _run_tier2_platform_services(
    mosk_adapter: KubernetesAdapter,
) -> TierResult:
    """Run Tier 2: Platform Services checks.

    Validates:
    - LCM components healthy
    - StackLight/monitoring operational
    - MetalLB ready
    - Calico networking functional
    """
    start = datetime.now(UTC)
    result = TierResult(
        tier=2,
        name="Platform Services",
        status=ValidationStatus.PASSED,
        details={},
    )

    checks_passed = 0
    checks_failed = 0

    try:
        # Check LCM system pods (try multiple namespace names)
        lcm_namespaces = ["lcm-system", "ceph-lcm-mirantis", "lcm"]
        lcm_found = False
        try:
            for lcm_ns in lcm_namespaces:
                try:
                    lcm_pods = await mosk_adapter.list(kind="Pod", namespace=lcm_ns)
                    if lcm_pods:
                        lcm_running = sum(
                            1 for p in lcm_pods if p.get("status", {}).get("phase") == "Running"
                        )
                        result.details["lcm"] = {
                            "namespace": lcm_ns,
                            "pods_total": len(lcm_pods),
                            "pods_running": lcm_running,
                            "healthy": lcm_running > 0,
                        }
                        lcm_found = True
                        if result.details["lcm"]["healthy"]:
                            checks_passed += 1
                        else:
                            checks_failed += 1
                        break
                except Exception:
                    continue

            if not lcm_found:
                result.details["lcm"] = {
                    "status": "not_found",
                    "tried": lcm_namespaces,
                }
                result.checks_skipped += 1
        except Exception as e:
            result.details["lcm"] = {"status": "error", "error": str(e)}
            checks_failed += 1

        # Check StackLight/monitoring
        try:
            stacklight_pods = await mosk_adapter.list(kind="Pod", namespace="stacklight")
            sl_running = sum(
                1 for p in stacklight_pods if p.get("status", {}).get("phase") == "Running"
            )
            result.details["stacklight"] = {
                "pods_total": len(stacklight_pods),
                "pods_running": sl_running,
                "healthy": sl_running > 0,
            }
            if result.details["stacklight"]["healthy"]:
                checks_passed += 1
            else:
                # StackLight may not be required
                result.checks_skipped += 1
                result.details["stacklight"]["note"] = "StackLight may be optional"
        except Exception as e:
            logger.debug(
                "platform_validation_stacklight_check_failed",
                error=str(e),
                error_type=type(e).__name__,
            )
            result.details["stacklight"] = {"status": "not_deployed"}
            result.checks_skipped += 1

        # Check MetalLB
        try:
            metallb_pods = await mosk_adapter.list(kind="Pod", namespace="metallb-system")
            mb_running = sum(
                1 for p in metallb_pods if p.get("status", {}).get("phase") == "Running"
            )
            result.details["metallb"] = {
                "pods_total": len(metallb_pods),
                "pods_running": mb_running,
                "healthy": mb_running == len(metallb_pods) and len(metallb_pods) > 0,
            }
            if result.details["metallb"]["healthy"]:
                checks_passed += 1
            else:
                result.checks_skipped += 1
        except Exception as e:
            logger.debug(
                "platform_validation_metallb_check_failed",
                error=str(e),
                error_type=type(e).__name__,
            )
            result.details["metallb"] = {"status": "not_deployed"}
            result.checks_skipped += 1

        # Check Calico
        try:
            calico_pods = await mosk_adapter.list(
                kind="Pod", namespace="kube-system", label_selector="k8s-app=calico-node"
            )
            calico_running = sum(
                1 for p in calico_pods if p.get("status", {}).get("phase") == "Running"
            )
            result.details["calico"] = {
                "pods_total": len(calico_pods),
                "pods_running": calico_running,
                "healthy": calico_running == len(calico_pods) and len(calico_pods) > 0,
            }
            if result.details["calico"]["healthy"]:
                checks_passed += 1
            else:
                checks_failed += 1
        except Exception as e:
            result.details["calico"] = {"status": "error", "error": str(e)}
            checks_failed += 1

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


async def _run_tier3_openstack_health(
    mosk_adapter: KubernetesAdapter,
    openstack_namespace: str,
) -> TierResult:
    """Run Tier 3: OpenStack Health checks.

    Validates:
    - OSDPL status healthy
    - OpenStack services responding
    """
    start = datetime.now(UTC)
    result = TierResult(
        tier=3,
        name="OpenStack Health",
        status=ValidationStatus.PASSED,
        details={},
    )

    checks_passed = 0
    checks_failed = 0

    try:
        # Auto-discover OSDPL
        osdpls = await mosk_adapter.list_openstack_deployments(namespace=openstack_namespace)

        if not osdpls:
            result.details["openstack"] = {
                "status": "not_deployed",
                "note": "No OpenStackDeployment found",
            }
            result.checks_skipped += 1
            result.status = ValidationStatus.PASSED_WITH_WARNINGS
            result.duration_seconds = (datetime.now(UTC) - start).total_seconds()
            return result

        osdpl = osdpls[0]
        osdpl_name = osdpl.get("metadata", {}).get("name")
        spec = osdpl.get("spec", {})
        openstack_version = spec.get("openstack_version", "unknown")

        # Check OpenStack health using existing tool
        try:
            os_input = GetOpenStackHealthInput(
                osdpl_name=osdpl_name,
                namespace=openstack_namespace,
                include_services=True,
                include_endpoints=False,
            )
            os_health = await get_openstack_health(mosk_adapter, os_input)

            result.details["openstack"] = {
                "osdpl_name": osdpl_name,
                "openstack_version": openstack_version,
                "control_plane_health": os_health.control_plane_health.value,
                "compute_health": os_health.compute_health.value,
                "control_plane_score": os_health.control_plane_score,
                "compute_score": os_health.compute_score,
                "osdpl_phase": os_health.osdpl_phase,
            }

            # Use control plane health as primary indicator
            if os_health.control_plane_health.value == "healthy":
                checks_passed += 1
            elif os_health.control_plane_health.value == "degraded":
                checks_passed += 1  # Pass with warning
            else:
                checks_failed += 1

        except Exception as e:
            checks_failed += 1
            result.details["openstack"] = {
                "osdpl_name": osdpl_name,
                "status": "error",
                "error": str(e),
            }

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


def _generate_platform_recommendations(
    tier_results: list[TierResult],
    level: ValidationLevel,
) -> list[str]:
    """Generate recommendations based on validation results."""
    recommendations = []

    for tier in tier_results:
        if tier.status == ValidationStatus.FAILED:
            if tier.tier == 1:
                recommendations.append(
                    f"CRITICAL: {tier.name} failed - "
                    "resolve Kubernetes infrastructure issues before proceeding"
                )
            elif tier.tier == 2:
                recommendations.append(
                    f"WARNING: {tier.name} failed - check LCM and platform component pods"
                )
            elif tier.tier == 3:
                recommendations.append(
                    f"WARNING: {tier.name} failed - "
                    "OpenStack services may be impacted by platform upgrade"
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
        recommendations.append(
            "All validation checks passed - MOSK platform upgrade completed successfully"
        )

    return recommendations


def _generate_platform_summary(
    status: ValidationStatus,
    tiers_run: int,
    tiers_passed: int,
    tiers_failed: int,
    level: ValidationLevel,
    release: str | None,
) -> str:
    """Generate human-readable summary."""
    release_info = f" to {release}" if release else ""

    if status == ValidationStatus.PASSED:
        return (
            f"MOSK platform validation PASSED ({level.value} level): "
            f"{tiers_passed}/{tiers_run} tiers passed. "
            f"Platform upgrade{release_info} completed successfully."
        )
    elif status == ValidationStatus.PASSED_WITH_WARNINGS:
        return (
            f"MOSK platform validation PASSED WITH WARNINGS ({level.value} level): "
            f"{tiers_passed}/{tiers_run} tiers passed. "
            f"Review warnings before production use."
        )
    elif status == ValidationStatus.FAILED:
        return (
            f"MOSK platform validation FAILED ({level.value} level): "
            f"{tiers_failed}/{tiers_run} tiers failed. "
            "Address issues before proceeding."
        )
    else:
        return (
            f"MOSK platform validation ERROR ({level.value} level): "
            "Validation encountered an error. Check logs for details."
        )
