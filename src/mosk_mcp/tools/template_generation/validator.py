"""Template validation tool.

This module provides the validate_template tool for validating generated
Kubernetes templates against schema and cluster state.
"""

from __future__ import annotations

import re
from typing import Any, ClassVar, Literal

import yaml
from pydantic import BaseModel, Field

from mosk_mcp.observability.logging import get_logger


logger = get_logger(__name__)


class ValidationIssue(BaseModel):
    """A single validation issue.

    Attributes:
        severity: Issue severity (error, warning, info).
        path: JSON path to the problematic field.
        message: Description of the issue.
        suggestion: Suggested fix.
    """

    severity: Literal["error", "warning", "info"] = Field(..., description="Issue severity")
    path: str = Field(..., description="Path to the problematic field")
    message: str = Field(..., description="Issue description")
    suggestion: str | None = Field(default=None, description="Suggested fix")


class ValidateTemplateInput(BaseModel):
    """Input parameters for validating a template.

    Attributes:
        template_yaml: YAML template string to validate.
        check_cluster_conflicts: Check for naming conflicts with existing resources.
        existing_resources: List of existing resource names (for conflict detection).
        strict_mode: Enable strict validation (warnings become errors).
    """

    template_yaml: str = Field(
        ...,
        description="YAML template string to validate",
        min_length=1,
    )
    check_cluster_conflicts: bool = Field(
        default=False,
        description="Check for naming conflicts with existing cluster resources",
    )
    existing_resources: list[str] | None = Field(
        default=None,
        description="List of existing resource names (kind/name format) for conflict detection",
    )
    strict_mode: bool = Field(
        default=False,
        description="Treat warnings as errors",
    )


class ValidateTemplateOutput(BaseModel):
    """Output from validate_template tool.

    Attributes:
        valid: Whether the template is valid (no errors).
        resource_kind: Detected resource kind.
        resource_name: Detected resource name.
        resource_namespace: Detected resource namespace.
        issues: List of validation issues.
        summary: Summary of validation results.
    """

    valid: bool = Field(..., description="Whether template is valid")
    resource_kind: str | None = Field(default=None, description="Detected resource kind")
    resource_name: str | None = Field(default=None, description="Detected resource name")
    resource_namespace: str | None = Field(default=None, description="Detected resource namespace")
    issues: list[ValidationIssue] = Field(default_factory=list, description="Validation issues")
    summary: str = Field(..., description="Validation summary")


class TemplateValidator:
    """Validator for Kubernetes custom resource templates.

    This validator checks templates for:
    - YAML syntax errors
    - Required Kubernetes fields
    - MOSK-specific schema compliance
    - Naming convention violations
    - Cluster state conflicts

    Example:
        validator = TemplateValidator()
        input_params = ValidateTemplateInput(
            template_yaml="apiVersion: kaas.mirantis.com/v1alpha1...",
            check_cluster_conflicts=True,
        )
        output = validator.validate(input_params)
    """

    # Regex patterns for validation
    DNS_SUBDOMAIN_PATTERN = re.compile(
        r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?(\.[a-z0-9]([-a-z0-9]*[a-z0-9])?)*$"
    )
    DNS_LABEL_PATTERN = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")
    LABEL_KEY_PATTERN = re.compile(
        r"^([a-z0-9]([-a-z0-9]*[a-z0-9])?\.)*[a-z0-9]([-a-z0-9]*[a-z0-9])?/?"
        r"[a-zA-Z0-9]([-_.a-zA-Z0-9]*[a-zA-Z0-9])?$"
    )
    MAC_ADDRESS_PATTERN = re.compile(r"^([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}$")
    IP_ADDRESS_PATTERN = re.compile(
        r"^(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}"
        r"(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)$"
    )

    # Known API versions and kinds
    KNOWN_RESOURCES: ClassVar[dict[str, dict[str, Any]]] = {
        "BareMetalHostInventory": {
            "apiVersion": "kaas.mirantis.com/v1alpha1",
            "required_spec_fields": ["bootMACAddress", "bmc"],
        },
        "BareMetalHostProfile": {
            "apiVersion": "metal3.io/v1alpha1",
            "required_spec_fields": [],
        },
        "Machine": {
            "apiVersion": "cluster.k8s.io/v1alpha1",
            "required_spec_fields": ["providerSpec"],
        },
        "IpamHost": {
            "apiVersion": "ipam.mirantis.com/v1alpha1",
            "required_spec_fields": ["l2Template", "networkAssignments"],
        },
        "L2Template": {
            "apiVersion": "ipam.mirantis.com/v1alpha1",
            "required_spec_fields": [],
        },
        "OpenStackDeployment": {
            "apiVersion": "lcm.mirantis.com/v1alpha1",
            "required_spec_fields": ["openStackVersion"],
        },
    }

    def validate(self, input_data: ValidateTemplateInput) -> ValidateTemplateOutput:
        """Validate a Kubernetes template.

        Args:
            input_data: Validation input parameters.

        Returns:
            Validation results.
        """
        logger.info("validating_template", strict_mode=input_data.strict_mode)

        issues: list[ValidationIssue] = []
        resource_kind: str | None = None
        resource_name: str | None = None
        resource_namespace: str | None = None

        # Parse YAML
        try:
            template = yaml.safe_load(input_data.template_yaml)
        except yaml.YAMLError as e:
            issues.append(
                ValidationIssue(
                    severity="error",
                    path="/",
                    message=f"Invalid YAML syntax: {e}",
                    suggestion="Check YAML formatting and indentation",
                )
            )
            return ValidateTemplateOutput(
                valid=False,
                issues=issues,
                summary="Template failed YAML parsing",
            )

        if not isinstance(template, dict):
            issues.append(
                ValidationIssue(
                    severity="error",
                    path="/",
                    message="Template must be a YAML mapping (dictionary)",
                    suggestion="Ensure template starts with key-value pairs",
                )
            )
            return ValidateTemplateOutput(
                valid=False,
                issues=issues,
                summary="Template structure invalid",
            )

        # Validate required Kubernetes fields
        issues.extend(self._validate_kubernetes_fields(template))

        # Extract resource info
        resource_kind = template.get("kind")
        metadata = template.get("metadata", {})
        resource_name = metadata.get("name")
        resource_namespace = metadata.get("namespace")

        # Validate metadata
        issues.extend(self._validate_metadata(metadata))

        # Validate resource-specific schema
        if resource_kind:
            issues.extend(self._validate_resource_schema(template, resource_kind))

        # Check for cluster conflicts
        if input_data.check_cluster_conflicts and input_data.existing_resources:
            issues.extend(
                self._check_cluster_conflicts(
                    resource_kind,
                    resource_name,
                    input_data.existing_resources,
                )
            )

        # Determine validity
        error_count = sum(1 for i in issues if i.severity == "error")
        warning_count = sum(1 for i in issues if i.severity == "warning")

        valid = error_count == 0
        if input_data.strict_mode:
            valid = valid and warning_count == 0

        # Build summary
        summary = self._build_summary(
            valid, error_count, warning_count, resource_kind, resource_name
        )

        logger.info(
            "validation_complete",
            valid=valid,
            errors=error_count,
            warnings=warning_count,
        )

        return ValidateTemplateOutput(
            valid=valid,
            resource_kind=resource_kind,
            resource_name=resource_name,
            resource_namespace=resource_namespace,
            issues=issues,
            summary=summary,
        )

    def _validate_kubernetes_fields(self, template: dict[str, Any]) -> list[ValidationIssue]:
        """Validate required Kubernetes fields.

        Args:
            template: Parsed template.

        Returns:
            List of validation issues.
        """
        issues: list[ValidationIssue] = []

        # Check apiVersion
        if "apiVersion" not in template:
            issues.append(
                ValidationIssue(
                    severity="error",
                    path="/apiVersion",
                    message="Missing required field 'apiVersion'",
                    suggestion="Add 'apiVersion' field (e.g., 'kaas.mirantis.com/v1alpha1')",
                )
            )
        elif not isinstance(template["apiVersion"], str):
            issues.append(
                ValidationIssue(
                    severity="error",
                    path="/apiVersion",
                    message="Field 'apiVersion' must be a string",
                )
            )

        # Check kind
        if "kind" not in template:
            issues.append(
                ValidationIssue(
                    severity="error",
                    path="/kind",
                    message="Missing required field 'kind'",
                    suggestion="Add 'kind' field (e.g., 'Machine', 'BareMetalHostProfile')",
                )
            )
        elif not isinstance(template["kind"], str):
            issues.append(
                ValidationIssue(
                    severity="error",
                    path="/kind",
                    message="Field 'kind' must be a string",
                )
            )
        else:
            kind = template["kind"]
            if kind not in self.KNOWN_RESOURCES:
                issues.append(
                    ValidationIssue(
                        severity="warning",
                        path="/kind",
                        message=f"Unknown resource kind '{kind}'",
                        suggestion=f"Known kinds: {', '.join(self.KNOWN_RESOURCES.keys())}",
                    )
                )

        # Check metadata
        if "metadata" not in template:
            issues.append(
                ValidationIssue(
                    severity="error",
                    path="/metadata",
                    message="Missing required field 'metadata'",
                    suggestion="Add 'metadata' section with 'name' field",
                )
            )
        elif not isinstance(template["metadata"], dict):
            issues.append(
                ValidationIssue(
                    severity="error",
                    path="/metadata",
                    message="Field 'metadata' must be an object",
                )
            )

        # Check spec (most MOSK resources require it)
        if "spec" not in template:
            issues.append(
                ValidationIssue(
                    severity="warning",
                    path="/spec",
                    message="Missing 'spec' field (required for most resources)",
                    suggestion="Add 'spec' section with resource configuration",
                )
            )

        return issues

    def _validate_metadata(self, metadata: dict[str, Any]) -> list[ValidationIssue]:
        """Validate metadata section.

        Args:
            metadata: Metadata dictionary.

        Returns:
            List of validation issues.
        """
        issues: list[ValidationIssue] = []

        # Check name
        if "name" not in metadata:
            issues.append(
                ValidationIssue(
                    severity="error",
                    path="/metadata/name",
                    message="Missing required field 'metadata.name'",
                    suggestion="Add a valid DNS label name",
                )
            )
        else:
            name = metadata["name"]
            if not isinstance(name, str):
                issues.append(
                    ValidationIssue(
                        severity="error",
                        path="/metadata/name",
                        message="Field 'metadata.name' must be a string",
                    )
                )
            elif len(name) > 63:
                issues.append(
                    ValidationIssue(
                        severity="error",
                        path="/metadata/name",
                        message=f"Name '{name}' exceeds 63 character limit",
                        suggestion="Use a shorter name",
                    )
                )
            elif not self.DNS_LABEL_PATTERN.match(name):
                issues.append(
                    ValidationIssue(
                        severity="error",
                        path="/metadata/name",
                        message=f"Name '{name}' is not a valid DNS label",
                        suggestion="Use lowercase alphanumeric characters and hyphens",
                    )
                )

        # Validate labels
        if "labels" in metadata:
            labels = metadata["labels"]
            if not isinstance(labels, dict):
                issues.append(
                    ValidationIssue(
                        severity="error",
                        path="/metadata/labels",
                        message="Field 'labels' must be an object",
                    )
                )
            else:
                for key, value in labels.items():
                    if not isinstance(key, str) or not isinstance(value, str):
                        issues.append(
                            ValidationIssue(
                                severity="error",
                                path=f"/metadata/labels/{key}",
                                message="Label keys and values must be strings",
                            )
                        )
                    elif len(value) > 63:
                        issues.append(
                            ValidationIssue(
                                severity="error",
                                path=f"/metadata/labels/{key}",
                                message="Label value exceeds 63 characters",
                                suggestion="Use a shorter label value",
                            )
                        )

        # Validate annotations
        if "annotations" in metadata:
            annotations = metadata["annotations"]
            if not isinstance(annotations, dict):
                issues.append(
                    ValidationIssue(
                        severity="error",
                        path="/metadata/annotations",
                        message="Field 'annotations' must be an object",
                    )
                )

        return issues

    def _validate_resource_schema(
        self, template: dict[str, Any], kind: str
    ) -> list[ValidationIssue]:
        """Validate resource-specific schema.

        Args:
            template: Parsed template.
            kind: Resource kind.

        Returns:
            List of validation issues.
        """
        issues: list[ValidationIssue] = []

        resource_info = self.KNOWN_RESOURCES.get(kind)
        if not resource_info:
            return issues

        # Check apiVersion matches
        expected_api_version = resource_info["apiVersion"]
        actual_api_version = template.get("apiVersion")
        if actual_api_version and actual_api_version != expected_api_version:
            issues.append(
                ValidationIssue(
                    severity="warning",
                    path="/apiVersion",
                    message=f"Unexpected apiVersion for {kind}",
                    suggestion=f"Expected '{expected_api_version}', got '{actual_api_version}'",
                )
            )

        # Check required spec fields
        spec = template.get("spec", {})
        for field in resource_info.get("required_spec_fields", []):
            if field not in spec:
                issues.append(
                    ValidationIssue(
                        severity="error",
                        path=f"/spec/{field}",
                        message=f"Missing required field 'spec.{field}' for {kind}",
                    )
                )

        # Kind-specific validations
        if kind == "BareMetalHostInventory":
            issues.extend(self._validate_bmhi_spec(spec))
        elif kind == "Machine":
            issues.extend(self._validate_machine_spec(spec, template.get("metadata", {})))
        elif kind == "IpamHost":
            issues.extend(self._validate_ipamhost_spec(spec))
        elif kind == "L2Template":
            issues.extend(self._validate_l2template_spec(spec))

        return issues

    def _validate_bmhi_spec(self, spec: dict[str, Any]) -> list[ValidationIssue]:
        """Validate BMHi-specific fields.

        Args:
            spec: Spec dictionary.

        Returns:
            List of validation issues.
        """
        issues: list[ValidationIssue] = []

        # Validate bootMACAddress
        mac = spec.get("bootMACAddress")
        if mac and not self.MAC_ADDRESS_PATTERN.match(mac):
            issues.append(
                ValidationIssue(
                    severity="error",
                    path="/spec/bootMACAddress",
                    message=f"Invalid MAC address format: {mac}",
                    suggestion="Use format: aa:bb:cc:dd:ee:ff",
                )
            )

        # Validate BMC
        bmc = spec.get("bmc", {})
        if bmc:
            if not bmc.get("address"):
                issues.append(
                    ValidationIssue(
                        severity="error",
                        path="/spec/bmc/address",
                        message="Missing BMC address",
                    )
                )
            if not bmc.get("credentialsName"):
                issues.append(
                    ValidationIssue(
                        severity="error",
                        path="/spec/bmc/credentialsName",
                        message="Missing BMC credentials secret name",
                    )
                )

        return issues

    def _validate_machine_spec(
        self, spec: dict[str, Any], metadata: dict[str, Any]
    ) -> list[ValidationIssue]:
        """Validate Machine-specific fields.

        Args:
            spec: Spec dictionary.
            metadata: Metadata dictionary.

        Returns:
            List of validation issues.
        """
        issues: list[ValidationIssue] = []

        # Check for role labels
        labels = metadata.get("labels", {})
        role_labels = [
            "openstack-compute-node",
            "openstack-control-plane",
            "openstack-gateway",
            "role",
        ]
        has_role = any(label in labels for label in role_labels)
        if not has_role:
            issues.append(
                ValidationIssue(
                    severity="warning",
                    path="/metadata/labels",
                    message="No role label detected",
                    suggestion="Add a role label (e.g., 'openstack-compute-node: enabled')",
                )
            )

        # Check providerSpec
        provider_spec = spec.get("providerSpec", {}).get("value", {})
        if not provider_spec.get("bareMetalHostProfile"):
            issues.append(
                ValidationIssue(
                    severity="error",
                    path="/spec/providerSpec/value/bareMetalHostProfile",
                    message="Missing bareMetalHostProfile reference",
                )
            )

        return issues

    def _validate_ipamhost_spec(self, spec: dict[str, Any]) -> list[ValidationIssue]:
        """Validate IpamHost-specific fields.

        Args:
            spec: Spec dictionary.

        Returns:
            List of validation issues.
        """
        issues: list[ValidationIssue] = []

        # Validate network assignments
        assignments = spec.get("networkAssignments", [])
        if not assignments:
            issues.append(
                ValidationIssue(
                    severity="warning",
                    path="/spec/networkAssignments",
                    message="No network assignments defined",
                    suggestion="Add at least a management network assignment",
                )
            )

        seen_networks = set()
        for i, assignment in enumerate(assignments):
            network = assignment.get("network")
            if network in seen_networks:
                issues.append(
                    ValidationIssue(
                        severity="error",
                        path=f"/spec/networkAssignments/{i}/network",
                        message=f"Duplicate network '{network}'",
                    )
                )
            seen_networks.add(network)

            # Validate IP if specified
            address = assignment.get("address")
            if address and not self.IP_ADDRESS_PATTERN.match(address):
                issues.append(
                    ValidationIssue(
                        severity="error",
                        path=f"/spec/networkAssignments/{i}/address",
                        message=f"Invalid IP address: {address}",
                    )
                )

        return issues

    def _validate_l2template_spec(self, spec: dict[str, Any]) -> list[ValidationIssue]:
        """Validate L2Template-specific fields.

        Args:
            spec: Spec dictionary.

        Returns:
            List of validation issues.
        """
        issues: list[ValidationIssue] = []

        # Collect defined interface names
        defined_interfaces = set()

        for iface in spec.get("interfaces", []):
            defined_interfaces.add(iface.get("name"))

        for bond in spec.get("bonds", []):
            defined_interfaces.add(bond.get("name"))

        for vlan in spec.get("vlans", []):
            defined_interfaces.add(vlan.get("name"))

        for bridge in spec.get("bridges", []):
            defined_interfaces.add(bridge.get("name"))

        # Validate bond interface references
        for i, bond in enumerate(spec.get("bonds", [])):
            for j, iface in enumerate(bond.get("interfaces", [])):
                if iface not in defined_interfaces:
                    issues.append(
                        ValidationIssue(
                            severity="warning",
                            path=f"/spec/bonds/{i}/interfaces/{j}",
                            message=f"Bond references undefined interface '{iface}'",
                        )
                    )

        # Validate VLAN parent references
        for i, vlan in enumerate(spec.get("vlans", [])):
            parent = vlan.get("parent")
            if parent and parent not in defined_interfaces:
                issues.append(
                    ValidationIssue(
                        severity="warning",
                        path=f"/spec/vlans/{i}/parent",
                        message=f"VLAN references undefined parent '{parent}'",
                    )
                )

        return issues

    def _check_cluster_conflicts(
        self,
        kind: str | None,
        name: str | None,
        existing_resources: list[str],
    ) -> list[ValidationIssue]:
        """Check for naming conflicts with existing resources.

        Args:
            kind: Resource kind.
            name: Resource name.
            existing_resources: List of existing resources in kind/name format.

        Returns:
            List of validation issues.
        """
        issues: list[ValidationIssue] = []

        if kind and name:
            resource_ref = f"{kind}/{name}"
            if resource_ref in existing_resources:
                issues.append(
                    ValidationIssue(
                        severity="error",
                        path="/metadata/name",
                        message=f"Resource '{resource_ref}' already exists in cluster",
                        suggestion="Use a different name or delete existing resource first",
                    )
                )

        return issues

    def _build_summary(
        self,
        valid: bool,
        error_count: int,
        warning_count: int,
        kind: str | None,
        name: str | None,
    ) -> str:
        """Build validation summary.

        Args:
            valid: Whether template is valid.
            error_count: Number of errors.
            warning_count: Number of warnings.
            kind: Resource kind.
            name: Resource name.

        Returns:
            Summary string.
        """
        resource_desc = f"{kind}/{name}" if kind and name else "template"

        if valid and error_count == 0 and warning_count == 0:
            return f"Validation PASSED: {resource_desc} is valid with no issues"
        elif valid:
            return (
                f"Validation PASSED with warnings: {resource_desc} has {warning_count} warning(s)"
            )
        else:
            return (
                f"Validation FAILED: {resource_desc} has "
                f"{error_count} error(s) and {warning_count} warning(s)"
            )


# Singleton instance
_validator: TemplateValidator | None = None


def get_template_validator() -> TemplateValidator:
    """Get the singleton template validator instance.

    Returns:
        TemplateValidator instance.
    """
    global _validator
    if _validator is None:
        _validator = TemplateValidator()
    return _validator


async def validate_template(
    template_yaml: str,
    check_cluster_conflicts: bool = False,
    existing_resources: list[str] | None = None,
    strict_mode: bool = False,
) -> ValidateTemplateOutput:
    """Validate a Kubernetes CR template.

    This tool validates generated templates for:
    - YAML syntax correctness
    - Required Kubernetes fields (apiVersion, kind, metadata)
    - MOSK-specific schema compliance
    - Naming convention adherence
    - Cross-field consistency
    - Optionally: naming conflicts with existing cluster resources

    The validator understands these MOSK resource types:
    - BaremetalHostInventory
    - BareMetalHostProfile
    - Machine
    - IpamHost
    - L2Template
    - OpenStackDeployment

    Args:
        template_yaml: The YAML template string to validate.
        check_cluster_conflicts: If True, check for naming conflicts with
            existing resources (requires existing_resources list).
        existing_resources: List of existing resources in "Kind/name" format
            for conflict detection.
        strict_mode: If True, treat warnings as errors (stricter validation).

    Returns:
        ValidateTemplateOutput containing:
        - valid: Whether the template passed validation
        - resource_kind: Detected Kubernetes resource kind
        - resource_name: Detected resource name
        - resource_namespace: Detected namespace
        - issues: List of validation issues with severity and suggestions
        - summary: Human-readable validation summary

    Example:
        >>> template = '''
        ... apiVersion: kaas.mirantis.com/v1alpha1
        ... kind: Machine
        ... metadata:
        ...   name: compute-01
        ...   labels:
        ...     openstack-compute-node: enabled
        ... spec:
        ...   providerSpec:
        ...     value:
        ...       bareMetalHostProfile: compute-standard
        ... '''
        >>> output = await validate_template(template)
        >>> print(output.summary)
        'Validation PASSED: Machine/compute-01 is valid with no issues'

        >>> # With conflict checking
        >>> output = await validate_template(
        ...     template,
        ...     check_cluster_conflicts=True,
        ...     existing_resources=["Machine/compute-01"],
        ... )
        >>> print(output.valid)
        False
    """
    validator = get_template_validator()

    input_data = ValidateTemplateInput(
        template_yaml=template_yaml,
        check_cluster_conflicts=check_cluster_conflicts,
        existing_resources=existing_resources,
        strict_mode=strict_mode,
    )

    return validator.validate(input_data)
