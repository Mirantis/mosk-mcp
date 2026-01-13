"""Apply machine tool for MOSK MCP Server.

This module provides the apply_machine tool for applying Machine CRs
to the cluster with CRQ validation for privileged operations.

Safety Level: PRIVILEGED (requires CRQ validation)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import yaml
from pydantic import BaseModel, Field

from mosk_mcp.auth.crq import get_crq_validator
from mosk_mcp.auth.rbac import ToolSafetyLevel, require_authenticated_context
from mosk_mcp.core.exceptions import KubernetesError, ResourceNotFoundError, ValidationError
from mosk_mcp.observability.audit import AuditLevel
from mosk_mcp.observability.logging import get_logger
from mosk_mcp.tools.common import audit_tool_execution


if TYPE_CHECKING:
    from mosk_mcp.adapters.kubernetes import KubernetesAdapter
    from mosk_mcp.auth.types import UserContext
    from mosk_mcp.observability.audit import AuditLogger


logger = get_logger(__name__)

# Tool metadata
TOOL_NAME = "apply_machine"
TOOL_SAFETY_LEVEL = ToolSafetyLevel.PRIVILEGED
TOOL_DESCRIPTION = (
    "Apply a Machine CR to the cluster to provision a new node. "
    "This is a privileged operation requiring CRQ validation."
)


class ApplyMachineInput(BaseModel):
    """Input parameters for apply_machine tool.

    Attributes:
        machine_yaml: YAML string containing the Machine CR.
        machine_dict: Alternative: Machine CR as dictionary.
        crq_number: Change Request number (required for non-dry-run).
        namespace: Override namespace for the Machine CR.
        dry_run: Validate without applying to cluster.
        validate_prerequisites: Check that referenced resources exist.
        server_side_apply: Use server-side apply instead of create/update.
    """

    machine_yaml: str | None = Field(
        default=None,
        description="YAML string containing the Machine CR",
    )
    machine_dict: dict[str, Any] | None = Field(
        default=None,
        description="Machine CR as dictionary (alternative to YAML)",
    )
    crq_number: str | None = Field(
        default=None,
        description="Change Request number (CRQxxxxxxxxx format)",
        pattern=r"^CRQ\d{9}$",
    )
    namespace: str | None = Field(
        default=None,
        description="Override namespace (uses value from YAML if not specified)",
    )
    dry_run: bool = Field(
        default=True,
        description="Validate without applying to cluster",
    )
    validate_prerequisites: bool = Field(
        default=True,
        description="Check that referenced resources (BMHi, BMHp) exist",
    )
    server_side_apply: bool = Field(
        default=False,
        description="Use server-side apply instead of create/update",
    )


class ApplyMachineOutput(BaseModel):
    """Output from apply_machine tool.

    Attributes:
        name: Machine name.
        namespace: Machine namespace.
        applied: Whether the Machine was applied to the cluster.
        created: Whether this was a new creation (vs update).
        dry_run: Whether this was a dry run.
        crq_validated: Whether CRQ validation passed.
        prerequisites_valid: Whether all prerequisites exist.
        prerequisite_issues: List of prerequisite issues found.
        machine_spec: Applied Machine spec.
        message: Result message.
        warnings: Any warnings generated.
        next_steps: Suggested next steps.
    """

    name: str = Field(..., description="Machine name")
    namespace: str = Field(..., description="Machine namespace")
    applied: bool = Field(..., description="Whether Machine was applied")
    created: bool = Field(default=False, description="Whether this was a creation")
    dry_run: bool = Field(..., description="Whether this was a dry run")
    crq_validated: bool = Field(..., description="Whether CRQ validation passed")
    prerequisites_valid: bool = Field(default=True, description="Whether prerequisites exist")
    prerequisite_issues: list[str] = Field(default_factory=list, description="Prerequisite issues")
    machine_spec: dict[str, Any] = Field(default_factory=dict, description="Applied Machine spec")
    message: str = Field(..., description="Result message")
    warnings: list[str] = Field(default_factory=list, description="Warnings generated")
    next_steps: list[str] = Field(default_factory=list, description="Suggested next steps")


def _parse_machine_yaml(yaml_str: str) -> dict[str, Any]:
    """Parse Machine YAML string to dictionary.

    Args:
        yaml_str: YAML string.

    Returns:
        Parsed dictionary.

    Raises:
        ValidationError: If YAML is invalid.
    """
    try:
        data = yaml.safe_load(yaml_str)
        if not isinstance(data, dict):
            raise ValidationError(
                "Machine YAML must be a dictionary",
                field="machine_yaml",
            )
        return data
    except yaml.YAMLError as e:
        raise ValidationError(
            f"Invalid YAML: {e}",
            field="machine_yaml",
        ) from e


def _validate_machine_structure(machine: dict[str, Any]) -> list[str]:
    """Validate Machine CR structure.

    Args:
        machine: Machine dictionary.

    Returns:
        List of validation warnings.
    """
    warnings = []

    # Check required fields
    if "apiVersion" not in machine:
        warnings.append("Missing apiVersion field")

    if "kind" not in machine:
        warnings.append("Missing kind field")
    elif machine["kind"] != "Machine":
        warnings.append(f"Kind should be 'Machine', got '{machine['kind']}'")

    metadata = machine.get("metadata", {})
    if not metadata.get("name"):
        warnings.append("Missing metadata.name")

    spec = machine.get("spec", {})
    provider_spec = spec.get("providerSpec", {}).get("value", {})

    if not provider_spec.get("bareMetalHostProfile"):
        warnings.append("Missing spec.providerSpec.value.bareMetalHostProfile")

    return warnings


async def _validate_prerequisites(
    k8s_adapter: KubernetesAdapter,
    machine: dict[str, Any],
    namespace: str,
) -> tuple[bool, list[str]]:
    """Validate that Machine prerequisites exist.

    Args:
        k8s_adapter: Kubernetes adapter.
        machine: Machine dictionary.
        namespace: Target namespace.

    Returns:
        Tuple of (all_valid, issues).
    """
    issues = []
    metadata = machine.get("metadata", {})
    spec = machine.get("spec", {})
    provider_spec = spec.get("providerSpec", {}).get("value", {})
    labels = metadata.get("labels", {})

    machine_name = metadata.get("name", "unknown")

    # Check BareMetalHostProfile
    bmhp_name = provider_spec.get("bareMetalHostProfile")
    if bmhp_name:
        # Handle both string and object format for bareMetalHostProfile reference
        if isinstance(bmhp_name, dict):
            bmhp_name = bmhp_name.get("name", "")
            if not bmhp_name:
                issues.append("bareMetalHostProfile object is missing required 'name' field")
                return False, issues
        try:
            await k8s_adapter.get_custom_resource(
                group="metal3.io",
                version="v1alpha1",
                plural="baremetalhostprofiles",
                name=bmhp_name,
                namespace=namespace,
            )
        except ResourceNotFoundError:
            issues.append(f"BareMetalHostProfile '{bmhp_name}' not found in namespace {namespace}")
        except Exception as e:
            logger.warning("bmhp_check_failed", name=bmhp_name, error=str(e))
            issues.append(f"Failed to check BareMetalHostProfile '{bmhp_name}': {e}")

    # Check BareMetalHostInventory (should match machine name)
    try:
        await k8s_adapter.get_custom_resource(
            group="kaas.mirantis.com",
            version="v1alpha1",
            plural="baremetalhostinventories",
            name=machine_name,
            namespace=namespace,
        )
    except ResourceNotFoundError:
        issues.append(f"BareMetalHostInventory '{machine_name}' not found in namespace {namespace}")
    except Exception as e:
        logger.warning("bmhi_check_failed", name=machine_name, error=str(e))
        issues.append(f"Failed to check BareMetalHostInventory '{machine_name}': {e}")

    # Check L2Template if referenced
    l2_template_ref = labels.get("kaas.mirantis.com/l2-template")
    if l2_template_ref:
        try:
            await k8s_adapter.get_custom_resource(
                group="ipam.mirantis.com",
                version="v1alpha1",
                plural="l2templates",
                name=l2_template_ref,
                namespace=namespace,
            )
        except ResourceNotFoundError:
            issues.append(f"L2Template '{l2_template_ref}' not found in namespace {namespace}")
        except Exception as e:
            logger.warning("l2template_check_failed", name=l2_template_ref, error=str(e))
            issues.append(f"Failed to check L2Template '{l2_template_ref}': {e}")

    return len(issues) == 0, issues


@dataclass
class MachineExistsResult:
    """Result of checking if a machine exists.

    Distinguishes between "machine doesn't exist" and "query failed",
    which is critical for preventing duplicate machine creation when
    the Kubernetes API is temporarily unavailable.
    """

    exists: bool
    """True if machine exists, False if not found."""

    query_succeeded: bool
    """True if the query completed successfully, False if it failed."""

    error: str | None = None
    """Error message if query failed, None otherwise."""


async def _check_machine_exists(
    k8s_adapter: KubernetesAdapter,
    name: str,
    namespace: str,
) -> MachineExistsResult:
    """Check if a Machine CR already exists.

    Args:
        k8s_adapter: Kubernetes adapter.
        name: Machine name.
        namespace: Machine namespace.

    Returns:
        MachineExistsResult with exists status and query success indicator.
        This allows the caller to distinguish between "machine doesn't exist"
        and "couldn't check if machine exists due to API failure".
    """
    try:
        await k8s_adapter.get_machine(name=name, namespace=namespace)
        return MachineExistsResult(exists=True, query_succeeded=True)
    except ResourceNotFoundError:
        return MachineExistsResult(exists=False, query_succeeded=True)
    except Exception as e:
        error_msg = f"{type(e).__name__}: {e}"
        logger.warning(
            "machine_existence_check_failed",
            name=name,
            namespace=namespace,
            error=error_msg,
        )
        return MachineExistsResult(exists=False, query_succeeded=False, error=error_msg)


@require_authenticated_context(ToolSafetyLevel.PRIVILEGED)
async def apply_machine(
    k8s_adapter: KubernetesAdapter,
    input_data: ApplyMachineInput,
    context: UserContext | None = None,
    audit_logger: AuditLogger | None = None,
) -> ApplyMachineOutput:
    """Apply a Machine CR to the cluster.

    SECURITY: This is a PRIVILEGED operation requiring authentication.
    The @require_authenticated_context decorator enforces that a valid
    UserContext with ADMINISTRATOR role is provided.

    This operation creates or updates a Machine CR in the MOSK cluster.
    It requires CRQ validation for non-dry-run operations.

    The tool:
    1. Validates the Machine CR structure
    2. Validates CRQ number (if not dry-run)
    3. Optionally validates that prerequisites exist (BMHi, BMHp, IpamHost)
    4. Creates or updates the Machine CR

    Args:
        k8s_adapter: Kubernetes adapter for API operations.
        input_data: Input parameters including Machine spec and CRQ.
        context: User context for RBAC (optional).
        audit_logger: Audit logger for tracking operations (optional).

    Returns:
        ApplyMachineOutput with operation results.

    Raises:
        ValidationError: If input validation fails or CRQ is invalid.
        KubernetesError: If Kubernetes API calls fail.

    Example:
        >>> async with KubernetesAdapter() as k8s:
        ...     result = await apply_machine(
        ...         k8s,
        ...         ApplyMachineInput(
        ...             machine_yaml=machine_yaml_str,
        ...             crq_number="CRQ123456789",
        ...             dry_run=False,
        ...         ),
        ...     )
        ...     print(f"Applied machine: {result.name}")
    """
    logger.info(
        "applying_machine",
        dry_run=input_data.dry_run,
        has_crq=input_data.crq_number is not None,
    )

    warnings: list[str] = []
    prerequisite_issues: list[str] = []

    # Parse Machine CR
    machine: dict[str, Any] = {}
    if input_data.machine_yaml:
        machine = _parse_machine_yaml(input_data.machine_yaml)
    elif input_data.machine_dict:
        machine = input_data.machine_dict
    else:
        raise ValidationError(
            "Either machine_yaml or machine_dict must be provided",
            field="machine_yaml",
        )

    # Extract metadata
    metadata = machine.get("metadata", {})
    machine_name = metadata.get("name")
    if not machine_name:
        raise ValidationError(
            "Machine name is required in metadata.name",
            field="metadata.name",
        )

    # Determine namespace
    namespace = input_data.namespace or metadata.get("namespace") or "default"

    # Update namespace in machine if overridden
    if "metadata" not in machine:
        machine["metadata"] = {}
    machine["metadata"]["namespace"] = namespace

    async with audit_tool_execution(
        TOOL_NAME,
        audit_logger,
        context,
        AuditLevel.PRIVILEGED,
        {
            "resource_type": "Machine",
            "resource_name": machine_name,
            "resource_namespace": namespace,
            "crq_id": input_data.crq_number,
            "dry_run": input_data.dry_run,
            "validate_prerequisites": input_data.validate_prerequisites,
        },
    ) as audit_details:
        try:
            # Validate Machine structure
            structure_warnings = _validate_machine_structure(machine)
            warnings.extend(structure_warnings)

            # Validate CRQ for non-dry-run operations
            crq_validated = True
            if not input_data.dry_run:
                if not input_data.crq_number:
                    raise ValidationError(
                        "CRQ number is required for non-dry-run apply_machine",
                        field="crq_number",
                        constraint="CRQ required for privileged operations",
                    )

                crq_validator = get_crq_validator(allow_format_only=True)
                crq_result = crq_validator.validate(input_data.crq_number)

                if not crq_result.is_valid:
                    raise ValidationError(
                        f"CRQ validation failed: {crq_result.message}",
                        field="crq_number",
                        value=input_data.crq_number,
                        details={"crq_status": crq_result.status.value},
                    )

                crq_validated = True
                # Add note about CRQ validation
                if not crq_result.verified_with_itsm:
                    warnings.append("CRQ format validated but ITSM verification not enabled")

                # Add CRQ annotation to Machine
                if "annotations" not in machine["metadata"]:
                    machine["metadata"]["annotations"] = {}
                machine["metadata"]["annotations"]["mosk-mcp/crq"] = input_data.crq_number
                machine["metadata"]["annotations"]["mosk-mcp/applied-at"] = datetime.now(
                    UTC
                ).isoformat()
                machine["metadata"]["annotations"]["mosk-mcp/applied-by"] = (
                    context.username if context else "unknown"
                )

            # Validate prerequisites
            prerequisites_valid = True
            if input_data.validate_prerequisites:
                prerequisites_valid, prerequisite_issues = await _validate_prerequisites(
                    k8s_adapter,
                    machine,
                    namespace,
                )

                if not prerequisites_valid and not input_data.dry_run:
                    raise ValidationError(
                        "Machine prerequisites not met",
                        field="prerequisites",
                        details={"issues": prerequisite_issues},
                    )

            # Check if machine already exists
            existence_result = await _check_machine_exists(
                k8s_adapter,
                machine_name,
                namespace,
            )

            # If the query failed, we cannot safely proceed - the machine might exist
            # and we could create a duplicate, causing cluster issues
            if not existence_result.query_succeeded:
                raise KubernetesError(
                    f"Cannot verify if machine '{machine_name}' exists due to API error. "
                    "Please ensure the Kubernetes API is accessible and try again.",
                    details={
                        "machine_name": machine_name,
                        "namespace": namespace,
                        "error": existence_result.error,
                        "reason": "Machine existence check failed - cannot safely proceed",
                    },
                )

            applied = False
            created = False

            # We only support creating new machines, not updating existing ones
            if existence_result.exists:
                raise ValidationError(
                    f"Machine '{machine_name}' already exists in namespace {namespace}. "
                    "Updating existing machines is not supported. Only creating new machines is allowed.",
                    field="machine_name",
                    value=machine_name,
                    details={"namespace": namespace, "operation": "update_not_supported"},
                )

            if not input_data.dry_run:
                # Create new machine
                await k8s_adapter.create_custom_resource(
                    group="cluster.k8s.io",
                    version="v1alpha1",
                    plural="machines",
                    namespace=namespace,
                    resource=machine,
                )
                applied = True
                created = True
                logger.info(
                    "machine_created",
                    name=machine_name,
                    namespace=namespace,
                )

            # Generate message
            if input_data.dry_run:
                message = (
                    f"Dry run: Machine '{machine_name}' would be created in namespace {namespace}"
                )
            else:
                message = f"Machine '{machine_name}' created in namespace {namespace}"

            # Generate next steps
            next_steps = []
            if input_data.dry_run:
                next_steps.append("Run with dry_run=False to apply the Machine")
                if prerequisite_issues:
                    next_steps.append("Resolve prerequisite issues before applying")
            else:
                next_steps.append(
                    f"Monitor machine status: kubectl get machine {machine_name} -n {namespace} -w"
                )
                next_steps.append(
                    f"Check machine details: kubectl describe machine {machine_name} -n {namespace}"
                )
                next_steps.append("Verify node joins cluster: kubectl get nodes -w")

            output = ApplyMachineOutput(
                name=machine_name,
                namespace=namespace,
                applied=applied,
                created=created,
                dry_run=input_data.dry_run,
                crq_validated=crq_validated,
                prerequisites_valid=prerequisites_valid,
                prerequisite_issues=prerequisite_issues,
                machine_spec=machine.get("spec", {}),
                message=message,
                warnings=warnings,
                next_steps=next_steps,
            )

            # Update audit details
            audit_details["applied"] = applied
            audit_details["created"] = created

            return output

        except ValidationError:
            raise

        except Exception as e:
            logger.error(
                "apply_machine_failed",
                name=machine_name,
                error=str(e),
            )

            if isinstance(e, (KubernetesError, ValidationError)):
                raise
            raise KubernetesError(
                f"Failed to apply machine: {e}",
                operation="apply",
                resource_kind="Machine",
                resource_name=machine_name,
                namespace=namespace,
            ) from e
