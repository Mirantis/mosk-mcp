"""Run OpenStack smoke tests for post-upgrade validation.

Tier 3 validation: Performs functional smoke tests to verify OpenStack
operations work correctly after upgrades or maintenance.

Smoke Test Types:
- vm_lifecycle: Create VM, wait for ACTIVE, verify boot, delete
- storage_operations: Create volume, attach to VM, detach, delete
- full_stack: Combined test of compute, storage, network (without ping)
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from mosk_mcp.observability.logging import get_logger


if TYPE_CHECKING:
    from mosk_mcp.adapters.openstack import OpenStackAdapter


logger = get_logger(__name__)


class SmokeTestType(str, Enum):
    """Types of smoke tests."""

    VM_LIFECYCLE = "vm_lifecycle"
    STORAGE_OPERATIONS = "storage_operations"
    FULL_STACK = "full_stack"


class SmokeTestStatus(str, Enum):
    """Smoke test result status."""

    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    ERROR = "error"


@dataclass
class SmokeTestStep:
    """Individual step in a smoke test."""

    name: str
    status: SmokeTestStatus
    duration_seconds: float = 0.0
    error_message: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class SmokeTestResult:
    """Result of a smoke test."""

    test_type: SmokeTestType
    status: SmokeTestStatus
    steps: list[SmokeTestStep] = field(default_factory=list)
    duration_seconds: float = 0.0
    error_message: str | None = None
    resources_created: list[str] = field(default_factory=list)
    resources_cleaned: list[str] = field(default_factory=list)
    resources_leaked: list[str] = field(default_factory=list)


class RunSmokeTestInput(BaseModel):
    """Input for run_smoke_test tool."""

    test_type: str = Field(
        default="vm_lifecycle",
        description=("Type of smoke test: vm_lifecycle, storage_operations, or full_stack"),
    )
    image_name: str | None = Field(
        default=None,
        description=(
            "Image name/ID to use for VM creation. If not provided, uses first available image."
        ),
    )
    flavor_name: str | None = Field(
        default=None,
        description=(
            "Flavor name/ID to use for VM creation. If not provided, "
            "uses smallest available flavor."
        ),
    )
    network_name: str | None = Field(
        default=None,
        description=("Network name/ID to use. If not provided, uses first available network."),
    )
    cleanup: bool = Field(
        default=True,
        description="Clean up created resources after test",
    )
    timeout_seconds: int = Field(
        default=300,
        description="Total test timeout in seconds",
        ge=60,
        le=900,
    )
    prefix: str = Field(
        default="mcp-smoke",
        description="Prefix for created resource names",
    )


class SmokeTestStepOutput(BaseModel):
    """Output for a single test step."""

    name: str = Field(description="Step name")
    status: str = Field(description="Step status (passed, failed, skipped, error)")
    duration_seconds: float = Field(default=0.0, description="Step duration")
    error_message: str | None = Field(default=None, description="Error if failed")
    details: dict[str, Any] = Field(default_factory=dict, description="Step details")


class RunSmokeTestOutput(BaseModel):
    """Output for run_smoke_test tool."""

    test_type: str = Field(description="Type of smoke test run")
    status: str = Field(description="Overall test status (passed, failed, error)")
    steps: list[SmokeTestStepOutput] = Field(description="Individual test steps")
    duration_seconds: float = Field(description="Total test duration")
    error_message: str | None = Field(default=None, description="Error if test failed")
    resources_created: list[str] = Field(
        default_factory=list, description="Resources created during test"
    )
    resources_cleaned: list[str] = Field(default_factory=list, description="Resources cleaned up")
    resources_leaked: list[str] = Field(
        default_factory=list, description="Resources that failed to clean up"
    )
    timestamp: str = Field(description="Test timestamp (ISO format)")
    recommendations: list[str] = Field(
        default_factory=list, description="Recommendations based on test results"
    )


# Test name prefix for resource identification
TEST_PREFIX = "mcp-smoke"


async def run_smoke_test(
    adapter: OpenStackAdapter,
    input_data: RunSmokeTestInput,
) -> RunSmokeTestOutput:
    """Run a smoke test to validate OpenStack functionality.

    Args:
        adapter: OpenStack adapter for API calls.
        input_data: Test configuration.

    Returns:
        Smoke test results.
    """
    start_time = datetime.now(UTC)

    # Parse test type
    try:
        test_type = SmokeTestType(input_data.test_type)
    except ValueError:
        return RunSmokeTestOutput(
            test_type=input_data.test_type,
            status=SmokeTestStatus.ERROR.value,
            steps=[],
            duration_seconds=0,
            error_message=f"Invalid test type: {input_data.test_type}",
            timestamp=start_time.isoformat(),
        )

    logger.info(
        "starting_smoke_test",
        test_type=test_type.value,
        timeout=input_data.timeout_seconds,
    )

    # Run the appropriate test
    try:
        result = await asyncio.wait_for(
            _run_test(adapter, test_type, input_data),
            timeout=input_data.timeout_seconds,
        )
    except TimeoutError:
        result = SmokeTestResult(
            test_type=test_type,
            status=SmokeTestStatus.ERROR,
            error_message=f"Test timed out after {input_data.timeout_seconds}s",
        )

    end_time = datetime.now(UTC)
    duration = (end_time - start_time).total_seconds()

    # Generate recommendations
    recommendations = _generate_recommendations(result)

    logger.info(
        "smoke_test_complete",
        test_type=test_type.value,
        status=result.status.value,
        duration=duration,
    )

    return RunSmokeTestOutput(
        test_type=test_type.value,
        status=result.status.value,
        steps=[
            SmokeTestStepOutput(
                name=s.name,
                status=s.status.value,
                duration_seconds=round(s.duration_seconds, 2),
                error_message=s.error_message,
                details=s.details,
            )
            for s in result.steps
        ],
        duration_seconds=round(duration, 2),
        error_message=result.error_message,
        resources_created=result.resources_created,
        resources_cleaned=result.resources_cleaned,
        resources_leaked=result.resources_leaked,
        timestamp=start_time.isoformat(),
        recommendations=recommendations,
    )


async def _run_test(
    adapter: OpenStackAdapter,
    test_type: SmokeTestType,
    input_data: RunSmokeTestInput,
) -> SmokeTestResult:
    """Run a specific smoke test.

    Args:
        adapter: OpenStack adapter.
        test_type: Type of test to run.
        input_data: Test configuration.

    Returns:
        Test result.
    """
    if test_type == SmokeTestType.VM_LIFECYCLE:
        return await _test_vm_lifecycle(adapter, input_data)
    elif test_type == SmokeTestType.STORAGE_OPERATIONS:
        return await _test_storage_operations(adapter, input_data)
    elif test_type == SmokeTestType.FULL_STACK:
        return await _test_full_stack(adapter, input_data)
    else:
        return SmokeTestResult(
            test_type=test_type,
            status=SmokeTestStatus.ERROR,
            error_message=f"Unknown test type: {test_type}",
        )


async def _test_vm_lifecycle(
    adapter: OpenStackAdapter,
    input_data: RunSmokeTestInput,
) -> SmokeTestResult:
    """Test VM lifecycle: create, verify, reboot, delete.

    Steps:
    1. Find suitable image and flavor
    2. Create VM
    3. Wait for ACTIVE state
    4. Verify console output (if available)
    5. Reboot VM
    6. Delete VM
    """
    result = SmokeTestResult(
        test_type=SmokeTestType.VM_LIFECYCLE,
        status=SmokeTestStatus.PASSED,
        steps=[],
    )

    test_id = uuid.uuid4().hex[:8]
    server_name = f"{input_data.prefix}-vm-{test_id}"
    server_id: str | None = None

    try:
        # Step 1: Find resources
        step_start = datetime.now(UTC)
        image_name, flavor_name, network_name = await _find_test_resources(adapter, input_data)
        step_duration = (datetime.now(UTC) - step_start).total_seconds()

        result.steps.append(
            SmokeTestStep(
                name="find_resources",
                status=SmokeTestStatus.PASSED,
                duration_seconds=step_duration,
                details={
                    "image": image_name,
                    "flavor": flavor_name,
                    "network": network_name,
                },
            )
        )

        # Step 2: Create VM
        step_start = datetime.now(UTC)
        server = await adapter.create_server(
            name=server_name,
            image=image_name,
            flavor=flavor_name,
            network=network_name,
            wait=True,
        )
        step_duration = (datetime.now(UTC) - step_start).total_seconds()

        if not server:
            result.steps.append(
                SmokeTestStep(
                    name="create_vm",
                    status=SmokeTestStatus.FAILED,
                    duration_seconds=step_duration,
                    error_message="Failed to create VM",
                )
            )
            result.status = SmokeTestStatus.FAILED
            result.error_message = "Failed to create VM"
            return result

        server_id = server.get("id")
        if not server_id:
            result.steps.append(
                SmokeTestStep(
                    name="create_vm",
                    status=SmokeTestStatus.FAILED,
                    duration_seconds=step_duration,
                    error_message="Server created but no ID returned",
                )
            )
            result.status = SmokeTestStatus.FAILED
            result.error_message = "Server created but no ID returned"
            return result
        result.resources_created.append(f"server:{server_name}")

        server_status = server.get("status", "UNKNOWN")
        result.steps.append(
            SmokeTestStep(
                name="create_vm",
                status=SmokeTestStatus.PASSED,
                duration_seconds=step_duration,
                details={
                    "server_id": server_id,
                    "server_name": server_name,
                    "status": server_status,
                },
            )
        )

        # Step 3: Verify VM is ACTIVE
        step_start = datetime.now(UTC)
        if server_status != "ACTIVE":
            result.steps.append(
                SmokeTestStep(
                    name="verify_vm_active",
                    status=SmokeTestStatus.FAILED,
                    duration_seconds=0,
                    error_message=f"VM not ACTIVE, status: {server_status}",
                )
            )
            result.status = SmokeTestStatus.FAILED
            result.error_message = f"VM not ACTIVE, status: {server_status}"
        else:
            step_duration = (datetime.now(UTC) - step_start).total_seconds()
            result.steps.append(
                SmokeTestStep(
                    name="verify_vm_active",
                    status=SmokeTestStatus.PASSED,
                    duration_seconds=step_duration,
                    details={"status": server_status},
                )
            )

        # Step 4: Check console output (optional, may not be available)
        step_start = datetime.now(UTC)
        console_output = await adapter.get_server_console_output(server_id, lines=10)
        step_duration = (datetime.now(UTC) - step_start).total_seconds()

        if console_output:
            result.steps.append(
                SmokeTestStep(
                    name="check_console",
                    status=SmokeTestStatus.PASSED,
                    duration_seconds=step_duration,
                    details={"console_lines": len(console_output.split("\n"))},
                )
            )
        else:
            result.steps.append(
                SmokeTestStep(
                    name="check_console",
                    status=SmokeTestStatus.SKIPPED,
                    duration_seconds=step_duration,
                    error_message="Console output not available",
                )
            )

        # Step 5: Reboot VM
        step_start = datetime.now(UTC)
        reboot_success = await adapter.reboot_server(server_id, hard=False, wait=True)
        step_duration = (datetime.now(UTC) - step_start).total_seconds()

        if reboot_success:
            result.steps.append(
                SmokeTestStep(
                    name="reboot_vm",
                    status=SmokeTestStatus.PASSED,
                    duration_seconds=step_duration,
                )
            )
        else:
            result.steps.append(
                SmokeTestStep(
                    name="reboot_vm",
                    status=SmokeTestStatus.FAILED,
                    duration_seconds=step_duration,
                    error_message="Failed to reboot VM",
                )
            )
            result.status = SmokeTestStatus.FAILED
            result.error_message = "Failed to reboot VM"

    except Exception as e:
        result.steps.append(
            SmokeTestStep(
                name="vm_lifecycle",
                status=SmokeTestStatus.ERROR,
                error_message=str(e),
            )
        )
        result.status = SmokeTestStatus.ERROR
        result.error_message = str(e)

    finally:
        # Cleanup
        if input_data.cleanup and server_id:
            step_start = datetime.now(UTC)
            try:
                delete_success = await adapter.delete_server(server_id, wait=True, force=True)
                step_duration = (datetime.now(UTC) - step_start).total_seconds()

                if delete_success:
                    result.resources_cleaned.append(f"server:{server_name}")
                    result.steps.append(
                        SmokeTestStep(
                            name="cleanup_vm",
                            status=SmokeTestStatus.PASSED,
                            duration_seconds=step_duration,
                        )
                    )
                else:
                    result.resources_leaked.append(f"server:{server_name}")
                    result.steps.append(
                        SmokeTestStep(
                            name="cleanup_vm",
                            status=SmokeTestStatus.FAILED,
                            duration_seconds=step_duration,
                            error_message="Failed to delete VM",
                        )
                    )
            except Exception as e:
                result.resources_leaked.append(f"server:{server_name}")
                result.steps.append(
                    SmokeTestStep(
                        name="cleanup_vm",
                        status=SmokeTestStatus.ERROR,
                        error_message=str(e),
                    )
                )

    return result


async def _test_storage_operations(
    adapter: OpenStackAdapter,
    input_data: RunSmokeTestInput,
) -> SmokeTestResult:
    """Test storage operations: create volume, attach, detach, delete.

    Steps:
    1. Create volume
    2. Wait for volume to be available
    3. Create a small VM (if needed for attach test)
    4. Attach volume to VM
    5. Detach volume from VM
    6. Delete volume
    7. Delete VM
    """
    result = SmokeTestResult(
        test_type=SmokeTestType.STORAGE_OPERATIONS,
        status=SmokeTestStatus.PASSED,
        steps=[],
    )

    test_id = uuid.uuid4().hex[:8]
    volume_name = f"{input_data.prefix}-vol-{test_id}"
    server_name = f"{input_data.prefix}-vm-{test_id}"
    volume_id: str | None = None
    server_id: str | None = None

    try:
        # Step 1: Create volume
        step_start = datetime.now(UTC)
        volume = await adapter.create_volume(name=volume_name, size_gb=1)
        step_duration = (datetime.now(UTC) - step_start).total_seconds()

        if not volume:
            result.steps.append(
                SmokeTestStep(
                    name="create_volume",
                    status=SmokeTestStatus.FAILED,
                    duration_seconds=step_duration,
                    error_message="Failed to create volume",
                )
            )
            result.status = SmokeTestStatus.FAILED
            result.error_message = "Failed to create volume"
            return result

        volume_id = volume.get("id")
        if not volume_id:
            result.steps.append(
                SmokeTestStep(
                    name="create_volume",
                    status=SmokeTestStatus.FAILED,
                    duration_seconds=step_duration,
                    error_message="Volume created but no ID returned",
                )
            )
            result.status = SmokeTestStatus.FAILED
            result.error_message = "Volume created but no ID returned"
            return result
        result.resources_created.append(f"volume:{volume_name}")

        result.steps.append(
            SmokeTestStep(
                name="create_volume",
                status=SmokeTestStatus.PASSED,
                duration_seconds=step_duration,
                details={
                    "volume_id": volume_id,
                    "size_gb": 1,
                    "status": volume.get("status"),
                },
            )
        )

        # Step 2: Wait for volume to be available
        step_start = datetime.now(UTC)
        volume_available = await _wait_for_volume_available(adapter, volume_id)
        step_duration = (datetime.now(UTC) - step_start).total_seconds()

        if not volume_available:
            result.steps.append(
                SmokeTestStep(
                    name="wait_volume_available",
                    status=SmokeTestStatus.FAILED,
                    duration_seconds=step_duration,
                    error_message="Volume did not become available",
                )
            )
            result.status = SmokeTestStatus.FAILED
            result.error_message = "Volume did not become available"
        else:
            result.steps.append(
                SmokeTestStep(
                    name="wait_volume_available",
                    status=SmokeTestStatus.PASSED,
                    duration_seconds=step_duration,
                )
            )

        # Step 3: Create VM for attach test
        step_start = datetime.now(UTC)
        image_name, flavor_name, network_name = await _find_test_resources(adapter, input_data)
        server = await adapter.create_server(
            name=server_name,
            image=image_name,
            flavor=flavor_name,
            network=network_name,
            wait=True,
        )
        step_duration = (datetime.now(UTC) - step_start).total_seconds()

        if not server:
            result.steps.append(
                SmokeTestStep(
                    name="create_vm_for_attach",
                    status=SmokeTestStatus.FAILED,
                    duration_seconds=step_duration,
                    error_message="Failed to create VM for attach test",
                )
            )
            result.status = SmokeTestStatus.FAILED
            result.error_message = "Failed to create VM for attach test"
        else:
            server_id = server.get("id")
            result.resources_created.append(f"server:{server_name}")
            result.steps.append(
                SmokeTestStep(
                    name="create_vm_for_attach",
                    status=SmokeTestStatus.PASSED,
                    duration_seconds=step_duration,
                    details={"server_id": server_id},
                )
            )

            # Step 4: Attach volume
            if volume_available and server_id:
                step_start = datetime.now(UTC)
                attach_success = await adapter.attach_volume(server=server_id, volume=volume_id)
                step_duration = (datetime.now(UTC) - step_start).total_seconds()

                if attach_success:
                    result.steps.append(
                        SmokeTestStep(
                            name="attach_volume",
                            status=SmokeTestStatus.PASSED,
                            duration_seconds=step_duration,
                        )
                    )

                    # Wait a moment for attachment
                    await asyncio.sleep(5)

                    # Step 5: Detach volume
                    step_start = datetime.now(UTC)
                    detach_success = await adapter.detach_volume(server=server_id, volume=volume_id)
                    step_duration = (datetime.now(UTC) - step_start).total_seconds()

                    if detach_success:
                        result.steps.append(
                            SmokeTestStep(
                                name="detach_volume",
                                status=SmokeTestStatus.PASSED,
                                duration_seconds=step_duration,
                            )
                        )
                    else:
                        result.steps.append(
                            SmokeTestStep(
                                name="detach_volume",
                                status=SmokeTestStatus.FAILED,
                                duration_seconds=step_duration,
                                error_message="Failed to detach volume",
                            )
                        )
                        result.status = SmokeTestStatus.FAILED
                else:
                    result.steps.append(
                        SmokeTestStep(
                            name="attach_volume",
                            status=SmokeTestStatus.FAILED,
                            duration_seconds=step_duration,
                            error_message="Failed to attach volume",
                        )
                    )
                    result.status = SmokeTestStatus.FAILED

    except Exception as e:
        result.steps.append(
            SmokeTestStep(
                name="storage_operations",
                status=SmokeTestStatus.ERROR,
                error_message=str(e),
            )
        )
        result.status = SmokeTestStatus.ERROR
        result.error_message = str(e)

    finally:
        # Cleanup
        if input_data.cleanup:
            # Delete server first
            if server_id:
                step_start = datetime.now(UTC)
                try:
                    delete_success = await adapter.delete_server(server_id, wait=True, force=True)
                    step_duration = (datetime.now(UTC) - step_start).total_seconds()
                    if delete_success:
                        result.resources_cleaned.append(f"server:{server_name}")
                        result.steps.append(
                            SmokeTestStep(
                                name="cleanup_vm",
                                status=SmokeTestStatus.PASSED,
                                duration_seconds=step_duration,
                            )
                        )
                    else:
                        result.resources_leaked.append(f"server:{server_name}")
                except Exception as e:
                    logger.warning(
                        "smoke_test_server_cleanup_failed",
                        server_name=server_name,
                        server_id=server_id,
                        error=str(e),
                        error_type=type(e).__name__,
                    )
                    result.resources_leaked.append(f"server:{server_name}")

            # Delete volume
            if volume_id:
                # Wait a moment for detachment to complete
                await asyncio.sleep(5)
                step_start = datetime.now(UTC)
                try:
                    delete_success = await adapter.delete_volume(volume_id, force=True)
                    step_duration = (datetime.now(UTC) - step_start).total_seconds()
                    if delete_success:
                        result.resources_cleaned.append(f"volume:{volume_name}")
                        result.steps.append(
                            SmokeTestStep(
                                name="cleanup_volume",
                                status=SmokeTestStatus.PASSED,
                                duration_seconds=step_duration,
                            )
                        )
                    else:
                        result.resources_leaked.append(f"volume:{volume_name}")
                except Exception as e:
                    logger.warning(
                        "smoke_test_volume_cleanup_failed",
                        volume_name=volume_name,
                        volume_id=volume_id,
                        error=str(e),
                        error_type=type(e).__name__,
                    )
                    result.resources_leaked.append(f"volume:{volume_name}")

    return result


async def _test_full_stack(
    adapter: OpenStackAdapter,
    input_data: RunSmokeTestInput,
) -> SmokeTestResult:
    """Full stack E2E test combining compute, storage, and network (no ping).

    Steps:
    1. Create keypair
    2. Create security group with SSH rule
    3. Create network and subnet
    4. Create volume
    5. Create VM with keypair, security group, network
    6. Attach volume to VM
    7. Verify VM is ACTIVE and has network IP
    8. Cleanup all resources
    """
    result = SmokeTestResult(
        test_type=SmokeTestType.FULL_STACK,
        status=SmokeTestStatus.PASSED,
        steps=[],
    )

    test_id = uuid.uuid4().hex[:8]
    keypair_name = f"{input_data.prefix}-key-{test_id}"
    sg_name = f"{input_data.prefix}-sg-{test_id}"
    network_name = f"{input_data.prefix}-net-{test_id}"
    subnet_name = f"{input_data.prefix}-subnet-{test_id}"
    volume_name = f"{input_data.prefix}-vol-{test_id}"
    server_name = f"{input_data.prefix}-vm-{test_id}"

    # Track created resources for cleanup
    keypair_created = False
    sg_created = False
    network_id: str | None = None
    subnet_id: str | None = None
    volume_id: str | None = None
    server_id: str | None = None

    try:
        # Step 1: Create keypair
        step_start = datetime.now(UTC)
        keypair = await adapter.create_keypair(keypair_name)
        step_duration = (datetime.now(UTC) - step_start).total_seconds()

        if keypair:
            keypair_created = True
            result.resources_created.append(f"keypair:{keypair_name}")
            result.steps.append(
                SmokeTestStep(
                    name="create_keypair",
                    status=SmokeTestStatus.PASSED,
                    duration_seconds=step_duration,
                )
            )
        else:
            result.steps.append(
                SmokeTestStep(
                    name="create_keypair",
                    status=SmokeTestStatus.FAILED,
                    duration_seconds=step_duration,
                    error_message="Failed to create keypair",
                )
            )

        # Step 2: Create security group
        step_start = datetime.now(UTC)
        sg = await adapter.create_security_group(
            sg_name, description="MCP smoke test security group"
        )
        step_duration = (datetime.now(UTC) - step_start).total_seconds()

        if sg:
            sg_created = True
            result.resources_created.append(f"security_group:{sg_name}")

            # Add SSH rule
            await adapter.add_security_group_rule(sg_name, protocol="tcp", port=22, ingress=True)
            # Add ICMP rule
            await adapter.add_security_group_rule(sg_name, protocol="icmp", ingress=True)

            result.steps.append(
                SmokeTestStep(
                    name="create_security_group",
                    status=SmokeTestStatus.PASSED,
                    duration_seconds=step_duration,
                    details={"rules_added": ["ssh", "icmp"]},
                )
            )
        else:
            result.steps.append(
                SmokeTestStep(
                    name="create_security_group",
                    status=SmokeTestStatus.FAILED,
                    duration_seconds=step_duration,
                    error_message="Failed to create security group",
                )
            )

        # Step 3: Create network and subnet
        step_start = datetime.now(UTC)
        network = await adapter.create_network(network_name)
        step_duration = (datetime.now(UTC) - step_start).total_seconds()

        if network:
            network_id = network.get("id")
            result.resources_created.append(f"network:{network_name}")

            # Create subnet (only if we have network_id)
            subnet = None
            if network_id:
                subnet = await adapter.create_subnet(
                    name=subnet_name,
                    network=network_id,
                    subnet_range="192.168.100.0/24",
                )
            if subnet:
                subnet_id = subnet.get("id")
                result.resources_created.append(f"subnet:{subnet_name}")

            result.steps.append(
                SmokeTestStep(
                    name="create_network",
                    status=SmokeTestStatus.PASSED,
                    duration_seconds=step_duration,
                    details={
                        "network_id": network_id,
                        "subnet_created": subnet_id is not None,
                    },
                )
            )
        else:
            result.steps.append(
                SmokeTestStep(
                    name="create_network",
                    status=SmokeTestStatus.FAILED,
                    duration_seconds=step_duration,
                    error_message="Failed to create network",
                )
            )

        # Step 4: Create volume
        step_start = datetime.now(UTC)
        volume = await adapter.create_volume(name=volume_name, size_gb=1)
        step_duration = (datetime.now(UTC) - step_start).total_seconds()

        if volume:
            volume_id = volume.get("id")
            result.resources_created.append(f"volume:{volume_name}")
            result.steps.append(
                SmokeTestStep(
                    name="create_volume",
                    status=SmokeTestStatus.PASSED,
                    duration_seconds=step_duration,
                    details={"volume_id": volume_id},
                )
            )
        else:
            result.steps.append(
                SmokeTestStep(
                    name="create_volume",
                    status=SmokeTestStatus.FAILED,
                    duration_seconds=step_duration,
                    error_message="Failed to create volume",
                )
            )

        # Step 5: Create VM
        step_start = datetime.now(UTC)
        image_name, flavor_name, _ = await _find_test_resources(adapter, input_data)

        # Use our created network or fall back to existing
        vm_network = network_id or input_data.network_name

        server = await adapter.create_server(
            name=server_name,
            image=image_name,
            flavor=flavor_name,
            network=vm_network,
            key_name=keypair_name if keypair_created else None,
            security_group=sg_name if sg_created else None,
            wait=True,
        )
        step_duration = (datetime.now(UTC) - step_start).total_seconds()

        if server:
            server_id = server.get("id")
            server_status = server.get("status", "UNKNOWN")
            result.resources_created.append(f"server:{server_name}")

            # Get network addresses
            addresses = server.get("addresses", {})
            ip_addresses = []
            for _net_name, addrs in addresses.items():
                if isinstance(addrs, list):
                    for addr in addrs:
                        if isinstance(addr, dict):
                            ip_addresses.append(addr.get("addr", ""))

            result.steps.append(
                SmokeTestStep(
                    name="create_vm",
                    status=SmokeTestStatus.PASSED,
                    duration_seconds=step_duration,
                    details={
                        "server_id": server_id,
                        "status": server_status,
                        "ip_addresses": ip_addresses,
                    },
                )
            )

            # Step 6: Attach volume (if both exist)
            if volume_id and server_id and server_status == "ACTIVE":
                # Wait for volume to be available
                await _wait_for_volume_available(adapter, volume_id)

                step_start = datetime.now(UTC)
                attach_success = await adapter.attach_volume(server=server_id, volume=volume_id)
                step_duration = (datetime.now(UTC) - step_start).total_seconds()

                if attach_success:
                    result.steps.append(
                        SmokeTestStep(
                            name="attach_volume",
                            status=SmokeTestStatus.PASSED,
                            duration_seconds=step_duration,
                        )
                    )
                else:
                    result.steps.append(
                        SmokeTestStep(
                            name="attach_volume",
                            status=SmokeTestStatus.FAILED,
                            duration_seconds=step_duration,
                            error_message="Failed to attach volume",
                        )
                    )

            # Step 7: Verify VM has network IP
            step_start = datetime.now(UTC)
            if ip_addresses:
                result.steps.append(
                    SmokeTestStep(
                        name="verify_network_ip",
                        status=SmokeTestStatus.PASSED,
                        duration_seconds=0,
                        details={"ip_addresses": ip_addresses},
                    )
                )
            else:
                result.steps.append(
                    SmokeTestStep(
                        name="verify_network_ip",
                        status=SmokeTestStatus.FAILED,
                        duration_seconds=0,
                        error_message="No IP addresses assigned",
                    )
                )
        else:
            result.steps.append(
                SmokeTestStep(
                    name="create_vm",
                    status=SmokeTestStatus.FAILED,
                    duration_seconds=step_duration,
                    error_message="Failed to create VM",
                )
            )
            result.status = SmokeTestStatus.FAILED
            result.error_message = "Failed to create VM"

    except Exception as e:
        result.steps.append(
            SmokeTestStep(
                name="full_stack",
                status=SmokeTestStatus.ERROR,
                error_message=str(e),
            )
        )
        result.status = SmokeTestStatus.ERROR
        result.error_message = str(e)

    finally:
        # Cleanup all resources in reverse order
        if input_data.cleanup:
            await _cleanup_full_stack(
                adapter,
                result,
                server_id=server_id,
                server_name=server_name,
                volume_id=volume_id,
                volume_name=volume_name,
                subnet_id=subnet_id,
                subnet_name=subnet_name,
                network_id=network_id,
                network_name=network_name,
                sg_name=sg_name if sg_created else None,
                keypair_name=keypair_name if keypair_created else None,
            )

    # Update overall status based on steps
    failed_steps = sum(1 for s in result.steps if s.status == SmokeTestStatus.FAILED)
    error_steps = sum(1 for s in result.steps if s.status == SmokeTestStatus.ERROR)

    if error_steps > 0:
        result.status = SmokeTestStatus.ERROR
    elif failed_steps > 0:
        result.status = SmokeTestStatus.FAILED

    return result


async def _cleanup_full_stack(
    adapter: OpenStackAdapter,
    result: SmokeTestResult,
    server_id: str | None,
    server_name: str,
    volume_id: str | None,
    volume_name: str,
    subnet_id: str | None,
    subnet_name: str,
    network_id: str | None,
    network_name: str,
    sg_name: str | None,
    keypair_name: str | None,
) -> None:
    """Clean up full stack test resources."""
    # 1. Delete server
    if server_id:
        try:
            if await adapter.delete_server(server_id, wait=True, force=True):
                result.resources_cleaned.append(f"server:{server_name}")
            else:
                result.resources_leaked.append(f"server:{server_name}")
        except Exception as e:
            logger.warning(
                "smoke_test_server_cleanup_failed",
                server_name=server_name,
                server_id=server_id,
                error=str(e),
                error_type=type(e).__name__,
            )
            result.resources_leaked.append(f"server:{server_name}")

    # 2. Delete volume
    if volume_id:
        await asyncio.sleep(5)  # Wait for detachment
        try:
            if await adapter.delete_volume(volume_id, force=True):
                result.resources_cleaned.append(f"volume:{volume_name}")
            else:
                result.resources_leaked.append(f"volume:{volume_name}")
        except Exception as e:
            logger.warning(
                "smoke_test_volume_cleanup_failed",
                volume_name=volume_name,
                volume_id=volume_id,
                error=str(e),
                error_type=type(e).__name__,
            )
            result.resources_leaked.append(f"volume:{volume_name}")

    # 3. Delete subnet
    if subnet_id:
        try:
            if await adapter.delete_subnet(subnet_id):
                result.resources_cleaned.append(f"subnet:{subnet_name}")
            else:
                result.resources_leaked.append(f"subnet:{subnet_name}")
        except Exception as e:
            logger.warning(
                "smoke_test_subnet_cleanup_failed",
                subnet_name=subnet_name,
                subnet_id=subnet_id,
                error=str(e),
                error_type=type(e).__name__,
            )
            result.resources_leaked.append(f"subnet:{subnet_name}")

    # 4. Delete network
    if network_id:
        try:
            if await adapter.delete_network(network_id):
                result.resources_cleaned.append(f"network:{network_name}")
            else:
                result.resources_leaked.append(f"network:{network_name}")
        except Exception as e:
            logger.warning(
                "smoke_test_network_cleanup_failed",
                network_name=network_name,
                network_id=network_id,
                error=str(e),
                error_type=type(e).__name__,
            )
            result.resources_leaked.append(f"network:{network_name}")

    # 5. Delete security group
    if sg_name:
        try:
            if await adapter.delete_security_group(sg_name):
                result.resources_cleaned.append(f"security_group:{sg_name}")
            else:
                result.resources_leaked.append(f"security_group:{sg_name}")
        except Exception as e:
            logger.warning(
                "smoke_test_security_group_cleanup_failed",
                sg_name=sg_name,
                error=str(e),
                error_type=type(e).__name__,
            )
            result.resources_leaked.append(f"security_group:{sg_name}")

    # 6. Delete keypair
    if keypair_name:
        try:
            if await adapter.delete_keypair(keypair_name):
                result.resources_cleaned.append(f"keypair:{keypair_name}")
            else:
                result.resources_leaked.append(f"keypair:{keypair_name}")
        except Exception as e:
            logger.warning(
                "smoke_test_keypair_cleanup_failed",
                keypair_name=keypair_name,
                error=str(e),
                error_type=type(e).__name__,
            )
            result.resources_leaked.append(f"keypair:{keypair_name}")


async def _find_test_resources(
    adapter: OpenStackAdapter,
    input_data: RunSmokeTestInput,
) -> tuple[str, str, str | None]:
    """Find suitable resources for testing.

    Args:
        adapter: OpenStack adapter.
        input_data: Test configuration.

    Returns:
        Tuple of (image_name, flavor_name, network_name).
    """
    # Find image
    image_name = input_data.image_name
    if not image_name:
        images = await adapter.list_images(limit=20)
        # Prefer cirros or small test images
        for img in images:
            name = img.get("Name", "").lower()
            if "cirros" in name or "test" in name:
                image_name = img.get("Name") or img.get("ID")
                break
        # Fall back to first active image
        if not image_name and images:
            for img in images:
                if img.get("Status", "").lower() == "active":
                    image_name = img.get("Name") or img.get("ID")
                    break
        if not image_name:
            raise ValueError("No suitable image found for testing")

    # Find flavor
    flavor_name = input_data.flavor_name
    if not flavor_name:
        flavors = await adapter.list_flavors()
        # Sort by RAM to get smallest
        sorted_flavors = sorted(flavors, key=lambda f: f.get("RAM", 999999))
        if sorted_flavors:
            flavor_name = sorted_flavors[0].get("Name") or sorted_flavors[0].get("ID")
        if not flavor_name:
            raise ValueError("No flavor found for testing")

    # Find network
    network_name = input_data.network_name
    if not network_name:
        networks = await adapter.list_networks(limit=10)
        # Prefer networks with 'internal' or 'private' in name
        for net in networks:
            name = net.get("Name", "").lower()
            if "internal" in name or "private" in name:
                network_name = net.get("Name") or net.get("ID")
                break
        # Fall back to first network
        if not network_name and networks:
            network_name = networks[0].get("Name") or networks[0].get("ID")

    return image_name, flavor_name, network_name


async def _wait_for_volume_available(
    adapter: OpenStackAdapter,
    volume_id: str,
    timeout: int = 60,
) -> bool:
    """Wait for volume to become available.

    Args:
        adapter: OpenStack adapter.
        volume_id: Volume ID.
        timeout: Timeout in seconds.

    Returns:
        True if volume became available.
    """
    start = datetime.now(UTC)
    while (datetime.now(UTC) - start).total_seconds() < timeout:
        volume = await adapter.get_volume(volume_id)
        if volume:
            status = volume.get("status", "").lower()
            if status == "available":
                return True
            elif status in ("error", "error_deleting"):
                return False
        await asyncio.sleep(3)
    return False


def _generate_recommendations(result: SmokeTestResult) -> list[str]:
    """Generate recommendations based on test results.

    Args:
        result: Smoke test result.

    Returns:
        List of recommendations.
    """
    recommendations = []

    if result.status == SmokeTestStatus.PASSED:
        recommendations.append(
            f"{result.test_type.value} test passed - OpenStack operations are functional"
        )
    elif result.status == SmokeTestStatus.FAILED:
        # Analyze failed steps
        for step in result.steps:
            if step.status == SmokeTestStatus.FAILED:
                if "create_vm" in step.name:
                    recommendations.append(
                        "VM creation failed - check Nova scheduler, hypervisor "
                        "availability, and quota limits"
                    )
                elif "volume" in step.name:
                    recommendations.append(
                        "Volume operation failed - check Cinder services and Ceph storage health"
                    )
                elif "network" in step.name:
                    recommendations.append(
                        "Network operation failed - check Neutron agents and OVS configuration"
                    )
                elif "attach" in step.name or "detach" in step.name:
                    recommendations.append(
                        "Volume attach/detach failed - check Nova-Cinder integration and libvirt"
                    )
    elif result.status == SmokeTestStatus.ERROR:
        recommendations.append(
            f"Test encountered an error: {result.error_message}. "
            "Check OpenStack service logs for details."
        )

    if result.resources_leaked:
        recommendations.append(
            f"Warning: {len(result.resources_leaked)} resources leaked during "
            f"test: {', '.join(result.resources_leaked)}"
        )

    return recommendations
