"""Post-upgrade validation tools registration for MOSK MCP Server.

This module registers post-upgrade validation tools with the MCP server:
- check_service_availability: Check OpenStack service availability
- run_smoke_test: Run OpenStack smoke tests
- run_post_upgrade_validation: Comprehensive post-upgrade validation
- run_mosk_platform_validation: MOSK platform upgrade validation
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from pydantic import Field

from mosk_mcp.observability.logging import get_logger
from mosk_mcp.registration.utils import create_adapter_getters, with_logging_context
from mosk_mcp.tools.validation import (
    CheckServiceAvailabilityInput,
    RunMoskPlatformValidationInput,
    RunPostUpgradeValidationInput,
    RunSmokeTestInput,
    check_service_availability,
    run_mosk_platform_validation,
    run_post_upgrade_validation,
    run_smoke_test,
)


if TYPE_CHECKING:
    from collections.abc import Callable

    from fastmcp import FastMCP

    from mosk_mcp.core.config import Settings
    from mosk_mcp.core.server_context import SSOServerContext


logger = get_logger(__name__)


def register_validation_tools(
    mcp: FastMCP, settings: Settings, context_getter: Callable[[], SSOServerContext | None]
) -> None:
    """Register post-upgrade validation tools with the MCP server.

    These tools provide comprehensive validation capabilities including
    service availability checks, functional smoke tests, and unified
    post-upgrade validation.

    All tools are READ_ONLY safety level (smoke tests create/cleanup resources).

    Args:
        mcp: FastMCP server instance.
        settings: Application settings.
        context_getter: Function that returns the current global SSOServerContext.
    """

    get_mosk, get_mcc = create_adapter_getters(context_getter)

    # =========================================================================
    # Service Availability Check Tool
    # =========================================================================

    # check_service_availability - Check OpenStack service availability
    @mcp.tool(
        name="check_service_availability",
        description=(
            "Check OpenStack service availability by probing APIs. Verifies services "
            "are responding after upgrades or maintenance. Tests keystone, nova, neutron, "
            "glance, cinder, heat. Checks agent status. Read-only operation."
        ),
    )
    async def _check_service_availability(
        services: list[str] | None = Field(
            default=None,
            description="Services to check (default: all core services)",
        ),
        include_agents: bool = Field(
            default=True, description="Check service agents (nova-compute, neutron agents)"
        ),
        timeout_seconds: int = Field(
            default=30, description="Timeout per service check", ge=5, le=120
        ),
    ) -> dict[str, Any]:
        """Check OpenStack service availability."""
        async with with_logging_context("check_service_availability"):
            k8s = await get_mosk()  # MOSK: OpenStack services

            # Get OpenStack adapter
            from mosk_mcp.adapters.openstack import OpenStackAdapter

            os_adapter = OpenStackAdapter(k8s)
            await os_adapter.connect()  # Connect to find keystone-client pod

            try:
                input_data = CheckServiceAvailabilityInput(
                    services=services,
                    include_agents=include_agents,
                    timeout_seconds=timeout_seconds,
                )
                result = await check_service_availability(os_adapter, input_data)
                return result.model_dump()
            finally:
                await os_adapter.disconnect()

    # =========================================================================
    # Smoke Test Tool
    # =========================================================================

    # run_smoke_test - Run OpenStack smoke tests
    @mcp.tool(
        name="run_smoke_test",
        description=(
            "Run OpenStack smoke tests to verify functionality after upgrades. "
            "Tests: vm_lifecycle (create/boot/reboot/delete VM), "
            "storage_operations (create/attach/detach/delete volume), "
            "full_stack (compute+storage+network without ping). "
            "Creates and cleans up test resources."
        ),
    )
    async def _run_smoke_test(
        test_type: Literal["vm_lifecycle", "storage_operations", "full_stack"] = Field(
            default="vm_lifecycle",
            description="Type of smoke test to run",
        ),
        image_name: str | None = Field(
            default=None, description="Image name for VM (auto-discovered if not provided)"
        ),
        flavor_name: str | None = Field(
            default=None, description="Flavor name for VM (auto-discovered if not provided)"
        ),
        network_name: str | None = Field(
            default=None, description="Network name for VM (auto-discovered if not provided)"
        ),
        cleanup: bool = Field(default=True, description="Clean up created resources after test"),
        timeout_seconds: int = Field(default=300, description="Total test timeout", ge=60, le=900),
        prefix: str = Field(default="mcp-smoke", description="Prefix for created resource names"),
    ) -> dict[str, Any]:
        """Run OpenStack smoke tests."""
        async with with_logging_context("run_smoke_test"):
            k8s = await get_mosk()  # MOSK: OpenStack smoke tests

            # Get OpenStack adapter
            from mosk_mcp.adapters.openstack import OpenStackAdapter

            os_adapter = OpenStackAdapter(k8s)
            await os_adapter.connect()  # Connect to find keystone-client pod

            try:
                input_data = RunSmokeTestInput(
                    test_type=test_type,
                    image_name=image_name,
                    flavor_name=flavor_name,
                    network_name=network_name,
                    cleanup=cleanup,
                    timeout_seconds=timeout_seconds,
                    prefix=prefix,
                )
                result = await run_smoke_test(os_adapter, input_data)
                return result.model_dump()
            finally:
                await os_adapter.disconnect()

    # =========================================================================
    # Unified Post-Upgrade Validation Tool
    # =========================================================================

    # run_post_upgrade_validation - Comprehensive post-upgrade validation
    @mcp.tool(
        name="run_post_upgrade_validation",
        description=(
            "Run comprehensive post-upgrade validation. Levels: "
            "quick (infrastructure health only), "
            "standard (+ API probing), "
            "comprehensive (+ smoke tests). "
            "Checks Kubernetes, OpenStack, and Ceph health. Returns pass/fail with recommendations."
        ),
    )
    async def _run_post_upgrade_validation(
        level: Literal["quick", "standard", "comprehensive"] = Field(
            default="standard",
            description="Validation level",
        ),
        osdpl_name: str | None = Field(
            default=None, description="OSDPL name (auto-discovered if not provided)"
        ),
        namespace: str = Field(default="openstack", description="OpenStack namespace"),
        include_smoke_tests: list[str] | None = Field(
            default=None,
            description="Smoke tests for comprehensive level (default: vm_lifecycle)",
        ),
        smoke_test_image: str | None = Field(default=None, description="Image for smoke tests"),
        smoke_test_flavor: str | None = Field(default=None, description="Flavor for smoke tests"),
        smoke_test_network: str | None = Field(default=None, description="Network for smoke tests"),
        cleanup_smoke_tests: bool = Field(
            default=True, description="Clean up smoke test resources"
        ),
        timeout_seconds: int = Field(
            default=600, description="Total validation timeout", ge=60, le=1800
        ),
    ) -> dict[str, Any]:
        """Run unified post-upgrade validation."""
        async with with_logging_context("run_post_upgrade_validation"):
            k8s = await get_mosk()  # MOSK: Post-upgrade validation

            # Get OpenStack adapter
            from mosk_mcp.adapters.openstack import OpenStackAdapter

            os_adapter = OpenStackAdapter(k8s)
            await os_adapter.connect()  # Connect to find keystone-client pod

            try:
                input_data = RunPostUpgradeValidationInput(
                    level=level,
                    osdpl_name=osdpl_name,
                    namespace=namespace,
                    include_smoke_tests=include_smoke_tests,
                    smoke_test_image=smoke_test_image,
                    smoke_test_flavor=smoke_test_flavor,
                    smoke_test_network=smoke_test_network,
                    cleanup_smoke_tests=cleanup_smoke_tests,
                    timeout_seconds=timeout_seconds,
                )
                result = await run_post_upgrade_validation(k8s, os_adapter, input_data)
                return result.model_dump()
            finally:
                await os_adapter.disconnect()

    # =========================================================================
    # MOSK Platform Upgrade Validation Tool
    # =========================================================================

    # run_mosk_platform_validation - MOSK platform upgrade validation
    @mcp.tool(
        name="run_mosk_platform_validation",
        description=(
            "Run post-MOSK platform upgrade validation. Levels: "
            "quick (Kubernetes infrastructure only), "
            "standard (+ platform services), "
            "comprehensive (+ OpenStack health). "
            "Use this after MOSK release upgrades (Kubernetes/LCM layer), not OpenStack upgrades."
        ),
    )
    async def _run_mosk_platform_validation(
        level: Literal["quick", "standard", "comprehensive"] = Field(
            default="standard",
            description="Validation level",
        ),
        cluster_name: str | None = Field(
            default=None, description="Cluster name on MCC (auto-discovered if not provided)"
        ),
        cluster_namespace: str = Field(
            default="lab", description="Namespace where Cluster CR exists on MCC"
        ),
        openstack_namespace: str = Field(
            default="openstack", description="OpenStack namespace on MOSK"
        ),
        timeout_seconds: int = Field(
            default=300, description="Total validation timeout", ge=60, le=900
        ),
    ) -> dict[str, Any]:
        """Run post-MOSK platform upgrade validation."""
        async with with_logging_context("run_mosk_platform_validation"):
            mcc_adapter = await get_mcc()  # MCC: Cluster CR
            mosk_adapter = await get_mosk()  # MOSK: K8s and OpenStack

            input_data = RunMoskPlatformValidationInput(
                level=level,
                cluster_name=cluster_name,
                cluster_namespace=cluster_namespace,
                openstack_namespace=openstack_namespace,
                timeout_seconds=timeout_seconds,
            )
            result = await run_mosk_platform_validation(mcc_adapter, mosk_adapter, input_data)
            return result.model_dump()

    logger.debug("validation_tools_registered", count=4)
