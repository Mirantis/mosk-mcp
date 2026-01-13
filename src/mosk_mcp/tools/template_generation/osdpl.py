"""OSDPL patch template generation tool.

This module provides the generate_osdpl_patch tool for generating
JSON patches for OpenStackDeployment resources with diff preview.
"""

from __future__ import annotations

import copy
import json
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

from mosk_mcp.observability.logging import get_logger
from mosk_mcp.tools.template_generation.base import (
    BaseTemplateGenerator,
    DiffOutput,
    OutputFormat,
)


logger = get_logger(__name__)


class PatchOperation(BaseModel):
    """A single JSON patch operation.

    Attributes:
        op: Operation type (add, remove, replace, move, copy, test).
        path: JSON Pointer path to the target location.
        value: Value for add/replace operations.
        from_path: Source path for move/copy operations.
    """

    model_config = ConfigDict(populate_by_name=True)

    op: Literal["add", "remove", "replace", "move", "copy", "test"] = Field(
        ..., description="Patch operation type"
    )
    path: str = Field(
        ...,
        description="JSON Pointer path (e.g., '/spec/services/nova/replicas')",
    )
    value: Any = Field(
        default=None,
        description="Value for add/replace operations",
    )
    from_path: str | None = Field(
        default=None,
        alias="from",
        description="Source path for move/copy operations",
    )


class OSDPLChange(BaseModel):
    """A high-level change to apply to OSDPL.

    Attributes:
        path: Dot-notation path to the field (e.g., 'spec.services.nova.replicas').
        value: New value to set.
        description: Human-readable description of the change.
    """

    path: str = Field(
        ...,
        description="Dot-notation path (e.g., 'spec.services.nova.replicas')",
    )
    value: Any = Field(
        ...,
        description="New value to set at the path",
    )
    description: str | None = Field(
        default=None,
        description="Human-readable description of the change",
    )


class GenerateOSDPLPatchInput(BaseModel):
    """Input parameters for generating an OSDPL patch.

    Attributes:
        changes: List of changes to apply.
        current_osdpl: Current OSDPL spec (for diff generation).
        osdpl_name: Name of the OSDPL resource to patch.
        namespace: Namespace of the OSDPL resource.
        show_diff: Generate a diff preview.
        output_format: Output format for the patch.
    """

    changes: list[OSDPLChange] = Field(
        ...,
        description="List of changes to apply to the OSDPL",
        min_length=1,
    )
    current_osdpl: dict[str, Any] | None = Field(
        default=None,
        description="Current OSDPL spec for diff generation (optional)",
    )
    osdpl_name: str = Field(
        ...,
        description="Name of the OSDPL resource (e.g., 'mos', 'openstack'). Required.",
    )
    namespace: str = Field(
        default="openstack",
        description="Kubernetes namespace where OSDPL is deployed",
    )
    show_diff: bool = Field(
        default=True,
        description="Generate a before/after diff preview",
    )
    output_format: OutputFormat = Field(
        default=OutputFormat.JSON,
        description="Output format for the patch",
    )


class GenerateOSDPLPatchOutput(BaseModel):
    """Output from generate_osdpl_patch tool.

    Attributes:
        patch: JSON patch array.
        patch_command: kubectl patch command.
        diff: Diff preview (if show_diff was True).
        changes_summary: Summary of changes to be applied.
        warnings: Any warnings about the changes.
    """

    patch: list[dict[str, Any]] = Field(..., description="JSON Patch operations")
    patch_command: str = Field(..., description="kubectl patch command to apply the changes")
    diff: DiffOutput | None = Field(default=None, description="Diff preview")
    changes_summary: list[str] = Field(default_factory=list, description="Summary of changes")
    warnings: list[str] = Field(default_factory=list, description="Warnings about the changes")


class OSDPLPatchGenerator(BaseTemplateGenerator[Any]):
    """Generator for OSDPL JSON patches.

    This generator creates JSON Patch documents for modifying
    OpenStackDeployment resources, with optional diff preview.

    Example:
        generator = OSDPLPatchGenerator()
        input_params = GenerateOSDPLPatchInput(
            changes=[
                OSDPLChange(
                    path="spec.services.nova.replicas",
                    value=5,
                    description="Scale Nova API to 5 replicas",
                ),
            ],
        )
        output = generator.generate_osdpl_patch(input_params)
    """

    # Known OSDPL paths and their types for validation
    KNOWN_PATHS: ClassVar[dict[str, dict[str, Any]]] = {
        "spec.openStackVersion": {
            "type": "string",
            "description": "OpenStack version (e.g., 'yoga', 'zed', 'antelope')",
            "critical": True,
        },
        "spec.preset": {
            "type": "string",
            "values": ["compute", "compute-tf", "core", "core-ceph", "full"],
            "description": "Deployment preset",
        },
        "spec.size": {
            "type": "string",
            "values": ["tiny", "small", "medium", "large", "xlarge"],
            "description": "Deployment size",
        },
        "spec.publicDomainName": {
            "type": "string",
            "description": "Public domain for OpenStack endpoints",
        },
        "spec.features.ssl.public_endpoints": {
            "type": "boolean",
            "description": "Enable SSL for public endpoints",
        },
    }

    # Service-specific paths
    SERVICES: ClassVar[list[str]] = [
        "keystone",
        "glance",
        "nova",
        "neutron",
        "cinder",
        "heat",
        "horizon",
        "octavia",
        "manila",
        "barbican",
    ]

    def generate(self, **kwargs: Any) -> Any:
        """Not used for patch generation."""
        raise NotImplementedError("Use generate_osdpl_patch instead")

    def _dot_to_json_pointer(self, dot_path: str) -> str:
        """Convert dot notation to JSON Pointer.

        Args:
            dot_path: Dot-notation path (e.g., 'spec.services.nova.replicas').

        Returns:
            JSON Pointer path (e.g., '/spec/services/nova/replicas').
        """
        # Handle array indices in dot notation (e.g., items.0.name)
        parts = dot_path.split(".")
        return "/" + "/".join(parts)

    def _json_pointer_to_dot(self, pointer: str) -> str:
        """Convert JSON Pointer to dot notation.

        Args:
            pointer: JSON Pointer path.

        Returns:
            Dot-notation path.
        """
        if pointer.startswith("/"):
            pointer = pointer[1:]
        return pointer.replace("/", ".")

    def _get_value_at_path(self, obj: dict[str, Any], dot_path: str) -> Any:
        """Get value at a dot-notation path.

        Args:
            obj: Dictionary to traverse.
            dot_path: Dot-notation path.

        Returns:
            Value at the path, or None if not found.
        """
        parts = dot_path.split(".")
        current = obj

        for part in parts:
            if isinstance(current, dict) and part in current:
                current = current[part]
            elif isinstance(current, list):
                try:
                    idx = int(part)
                    current = current[idx]
                except (ValueError, IndexError):
                    return None
            else:
                return None

        return current

    def _set_value_at_path(self, obj: dict[str, Any], dot_path: str, value: Any) -> dict[str, Any]:
        """Set value at a dot-notation path.

        Args:
            obj: Dictionary to modify (not mutated, returns new dict).
            dot_path: Dot-notation path.
            value: Value to set.

        Returns:
            New dictionary with value set.
        """
        result = copy.deepcopy(obj)
        parts = dot_path.split(".")
        current = result

        for i, part in enumerate(parts[:-1]):
            if part not in current:
                # Create intermediate objects
                next_part = parts[i + 1]
                try:
                    int(next_part)
                    current[part] = []
                except ValueError:
                    current[part] = {}
            current = current[part]

        current[parts[-1]] = value
        return result

    def _build_patch_operations(
        self, changes: list[OSDPLChange], current: dict[str, Any] | None
    ) -> list[dict[str, Any]]:
        """Build JSON Patch operations from changes.

        Args:
            changes: List of changes.
            current: Current OSDPL (optional, for determining add vs replace).

        Returns:
            List of JSON Patch operations.
        """
        operations: list[dict[str, Any]] = []

        for change in changes:
            pointer = self._dot_to_json_pointer(change.path)

            # Determine if this is an add or replace
            op = "replace"
            if current:
                existing = self._get_value_at_path(current, change.path)
                if existing is None:
                    op = "add"

            operation: dict[str, Any] = {
                "op": op,
                "path": pointer,
                "value": change.value,
            }
            operations.append(operation)

        return operations

    def _validate_changes(self, changes: list[OSDPLChange]) -> list[str]:
        """Validate changes and generate warnings.

        Args:
            changes: List of changes to validate.

        Returns:
            List of warnings.
        """
        warnings: list[str] = []

        for change in changes:
            # Check if this is a known path
            path_info = self.KNOWN_PATHS.get(change.path)
            if path_info:
                # Check if it's a critical path
                if path_info.get("critical"):
                    warnings.append(
                        f"CRITICAL: Changing '{change.path}' can cause service disruption"
                    )

                # Validate allowed values
                allowed_values = path_info.get("values")
                if allowed_values and change.value not in allowed_values:
                    warnings.append(
                        f"Value '{change.value}' for '{change.path}' is not in "
                        f"known values: {allowed_values}"
                    )

            # Check for version changes
            if "openStackVersion" in change.path:
                warnings.append(
                    "OpenStack version change detected. This will trigger a cluster upgrade. "
                    "Ensure you have reviewed the upgrade documentation and have a backup."
                )

            # Check for service enable/disable
            if change.path.endswith(".enabled") and change.value is False:
                warnings.append(
                    f"Disabling service at '{change.path}'. "
                    "Ensure no workloads depend on this service."
                )

            # Check for replica changes
            if "replicas" in change.path and isinstance(change.value, int):
                if change.value == 0:
                    warnings.append(
                        f"Setting replicas to 0 at '{change.path}'. This will stop the service."
                    )
                elif change.value == 1:
                    warnings.append(
                        f"Setting replicas to 1 at '{change.path}'. "
                        "Consider at least 2 replicas for HA."
                    )

        return warnings

    def _generate_kubectl_command(
        self,
        patch: list[dict[str, Any]],
        osdpl_name: str,
        namespace: str,
    ) -> str:
        """Generate kubectl patch command.

        Args:
            patch: JSON Patch operations.
            osdpl_name: OSDPL resource name.
            namespace: Namespace.

        Returns:
            kubectl command string.
        """
        patch_json = json.dumps(patch, indent=2)

        return f"""kubectl patch osdpl {osdpl_name} -n {namespace} --type=json -p '{patch_json}'

# Or using a file:
# 1. Save the patch to a file:
cat > osdpl-patch.json << 'EOF'
{patch_json}
EOF

# 2. Apply the patch:
kubectl patch osdpl {osdpl_name} -n {namespace} --type=json --patch-file=osdpl-patch.json"""

    def generate_osdpl_patch(self, input_data: GenerateOSDPLPatchInput) -> GenerateOSDPLPatchOutput:
        """Generate OSDPL patch with optional diff preview.

        Args:
            input_data: Input parameters for generation.

        Returns:
            Complete output with patch and diff.
        """
        logger.info(
            "generating_osdpl_patch",
            changes_count=len(input_data.changes),
            osdpl_name=input_data.osdpl_name,
        )

        # Validate changes and collect warnings
        warnings = self._validate_changes(input_data.changes)

        # Build patch operations
        patch_ops = self._build_patch_operations(input_data.changes, input_data.current_osdpl)

        # Build changes summary
        changes_summary = []
        for change in input_data.changes:
            desc = change.description or f"Set {change.path}"
            changes_summary.append(f"- {desc}: {change.value}")

        # Generate diff if requested and current OSDPL provided
        diff: DiffOutput | None = None
        if input_data.show_diff and input_data.current_osdpl:
            # Build the modified OSDPL
            modified = copy.deepcopy(input_data.current_osdpl)
            for change in input_data.changes:
                modified = self._set_value_at_path(modified, change.path, change.value)

            # Generate diff
            diff = self.generate_diff(
                input_data.current_osdpl,
                modified,
                context_lines=3,
            )

        # Generate kubectl command
        patch_command = self._generate_kubectl_command(
            patch_ops, input_data.osdpl_name, input_data.namespace
        )

        logger.info(
            "generated_osdpl_patch",
            osdpl_name=input_data.osdpl_name,
            operations_count=len(patch_ops),
            warnings_count=len(warnings),
        )

        return GenerateOSDPLPatchOutput(
            patch=patch_ops,
            patch_command=patch_command,
            diff=diff,
            changes_summary=changes_summary,
            warnings=warnings,
        )


# Singleton instance
_generator: OSDPLPatchGenerator | None = None


def get_osdpl_patch_generator() -> OSDPLPatchGenerator:
    """Get the singleton OSDPL patch generator instance.

    Returns:
        OSDPLPatchGenerator instance.
    """
    global _generator
    if _generator is None:
        _generator = OSDPLPatchGenerator()
    return _generator


async def generate_osdpl_patch(
    changes: list[dict[str, Any]],
    current_osdpl: dict[str, Any] | None = None,
    osdpl_name: str = "openstack",
    namespace: str = "openstack",
    show_diff: bool = True,
    output_format: OutputFormat = OutputFormat.JSON,
) -> GenerateOSDPLPatchOutput:
    """Generate a JSON patch for modifying an OpenStackDeployment resource.

    This tool generates a JSON Patch document for making changes to an
    OpenStackDeployment (OSDPL) resource. It supports diff preview when
    the current OSDPL spec is provided.

    JSON Patch (RFC 6902) is a format for describing changes to a JSON
    document. It's used with `kubectl patch --type=json`.

    Common OSDPL paths that can be modified:
    - spec.openStackVersion: OpenStack version (triggers upgrade)
    - spec.preset: Deployment preset (compute, core, full, etc.)
    - spec.size: Deployment size (tiny, small, medium, large, xlarge)
    - spec.publicDomainName: Public domain for endpoints
    - spec.features.ssl.public_endpoints: Enable SSL
    - spec.services.<service>.enabled: Enable/disable a service
    - spec.services.<service>.replicas: Service replica count
    - spec.services.<service>.config.*: Service-specific config

    Args:
        changes: List of changes to apply. Each change dict should have:
            - path: Dot-notation path (e.g., 'spec.services.nova.replicas')
            - value: New value to set
            - description: Optional description of the change
        current_osdpl: Current OSDPL spec for diff generation. If provided,
            enables diff preview showing before/after changes.
        osdpl_name: Name of the OSDPL resource to patch.
        namespace: Namespace of the OSDPL resource.
        show_diff: Generate a before/after diff preview (requires current_osdpl).
        output_format: Output format for the patch (json recommended).

    Returns:
        GenerateOSDPLPatchOutput containing:
        - patch: JSON Patch operations array
        - patch_command: kubectl command to apply the patch
        - diff: Diff preview (if show_diff and current_osdpl provided)
        - changes_summary: Human-readable summary of changes
        - warnings: Warnings about potentially dangerous changes

    Example:
        >>> # Scale Nova API to 5 replicas
        >>> output = await generate_osdpl_patch(
        ...     changes=[
        ...         {
        ...             "path": "spec.services.nova.replicas",
        ...             "value": 5,
        ...             "description": "Scale Nova API to 5 replicas",
        ...         },
        ...     ],
        ...     osdpl_name="openstack",
        ...     namespace="openstack",
        ... )
        >>> print(output.patch_command)

        >>> # Enable a service
        >>> output = await generate_osdpl_patch(
        ...     changes=[
        ...         {
        ...             "path": "spec.services.octavia.enabled",
        ...             "value": True,
        ...             "description": "Enable Octavia load balancer",
        ...         },
        ...     ],
        ... )
    """
    generator = get_osdpl_patch_generator()

    # Convert change dicts to OSDPLChange objects
    change_objs = [OSDPLChange(**c) for c in changes]

    input_data = GenerateOSDPLPatchInput(
        changes=change_objs,
        current_osdpl=current_osdpl,
        osdpl_name=osdpl_name,
        namespace=namespace,
        show_diff=show_diff,
        output_format=output_format,
    )

    return generator.generate_osdpl_patch(input_data)
