"""Post-upgrade validation tools for MOSK MCP Server.

This package provides tools for validating deployments after upgrades
or maintenance operations.

Two validation types:
1. OpenStack Upgrade Validation (run_post_upgrade_validation):
   - Validates OpenStack services after OpenStack version upgrades
   - Tier 1: Infrastructure Health (Kubernetes, Ceph, OSDPL)
   - Tier 2: Service Availability (API probing via keystone-client)
   - Tier 3: Functional Smoke Tests (VM lifecycle, storage operations)

2. MOSK Platform Upgrade Validation (run_mosk_platform_validation):
   - Validates platform after MOSK release upgrades (Kubernetes, LCM)
   - Tier 1: Kubernetes Infrastructure (nodes, system pods, API)
   - Tier 2: Platform Services (LCM, StackLight, MetalLB, Calico)
   - Tier 3: OpenStack Health (if deployed)
"""

from __future__ import annotations

from mosk_mcp.tools.validation.check_service_availability import (
    CheckServiceAvailabilityInput,
    CheckServiceAvailabilityOutput,
    check_service_availability,
)
from mosk_mcp.tools.validation.run_mosk_platform_validation import (
    RunMoskPlatformValidationInput,
    RunMoskPlatformValidationOutput,
    run_mosk_platform_validation,
)
from mosk_mcp.tools.validation.run_post_upgrade_validation import (
    RunPostUpgradeValidationInput,
    RunPostUpgradeValidationOutput,
    run_post_upgrade_validation,
)
from mosk_mcp.tools.validation.run_smoke_test import (
    RunSmokeTestInput,
    RunSmokeTestOutput,
    SmokeTestType,
    run_smoke_test,
)


__all__ = [
    # Service Availability (Tier 2 for OpenStack)
    "CheckServiceAvailabilityInput",
    "CheckServiceAvailabilityOutput",
    # MOSK Platform Upgrade Validation
    "RunMoskPlatformValidationInput",
    "RunMoskPlatformValidationOutput",
    # OpenStack Upgrade Validation
    "RunPostUpgradeValidationInput",
    "RunPostUpgradeValidationOutput",
    # Smoke Tests (Tier 3 for OpenStack)
    "RunSmokeTestInput",
    "RunSmokeTestOutput",
    "SmokeTestType",
    "check_service_availability",
    "run_mosk_platform_validation",
    "run_post_upgrade_validation",
    "run_smoke_test",
]
