"""Base template generator classes and utilities.

This module provides the foundation for all template generators including:
- Abstract base class with common functionality
- Output format handling (YAML, JSON, kubectl command)
- Naming validation utilities
- Diff generation for patches
- Environment-specific defaults loading
"""

from __future__ import annotations

import difflib
import json
import re
from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, Generic, TypeVar

import yaml
from pydantic import BaseModel, ConfigDict, Field

from mosk_mcp.adapters.crd.base import KubernetesResource
from mosk_mcp.core.exceptions import ValidationError
from mosk_mcp.observability.logging import get_logger


logger = get_logger(__name__)


class OutputFormat(str, Enum):
    """Output format for generated templates.

    Attributes:
        YAML: YAML format (default, human-readable).
        JSON: JSON format (machine-readable).
        KUBECTL: kubectl apply command with inline YAML.
    """

    YAML = "yaml"
    JSON = "json"
    KUBECTL = "kubectl"


class TemplateOutput(BaseModel):
    """Output model for generated templates.

    Attributes:
        format: Output format used.
        content: The generated template content.
        resource_kind: Kubernetes resource kind.
        resource_name: Resource name.
        resource_namespace: Resource namespace (if applicable).
        warnings: Any warnings generated during template creation.
        metadata: Additional metadata about the generation.
    """

    model_config = ConfigDict(populate_by_name=True)

    format: OutputFormat = Field(..., description="Output format")
    content: str = Field(..., description="Generated template content")
    resource_kind: str = Field(..., description="Kubernetes resource kind")
    resource_name: str = Field(..., description="Resource name")
    resource_namespace: str | None = Field(None, description="Resource namespace")
    warnings: list[str] = Field(default_factory=list, description="Generation warnings")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Additional metadata")


class DiffOutput(BaseModel):
    """Output model for diff preview.

    Attributes:
        has_changes: Whether there are any changes.
        diff_text: Unified diff text.
        before: Original content (truncated).
        after: Modified content (truncated).
        changes_summary: Summary of changes made.
    """

    model_config = ConfigDict(populate_by_name=True)

    has_changes: bool = Field(..., description="Whether there are changes")
    diff_text: str = Field(..., description="Unified diff output")
    before: str = Field(..., description="Original content")
    after: str = Field(..., description="Modified content")
    changes_summary: list[str] = Field(default_factory=list, description="Summary of changes")


# Type variable for generic template generator
ResourceT = TypeVar("ResourceT", bound=KubernetesResource[Any, Any])


class BaseTemplateGenerator(ABC, Generic[ResourceT]):
    """Abstract base class for all template generators.

    This class provides common functionality for generating Kubernetes
    custom resource templates, including:
    - Name validation
    - Output format conversion
    - YAML/JSON serialization
    - kubectl command generation

    Subclasses must implement the `generate` method to create the
    specific resource type.

    Example:
        class MachineGenerator(BaseTemplateGenerator[Machine]):
            def generate(self, input: MachineInput) -> Machine:
                return Machine(...)

        generator = MachineGenerator()
        output = generator.generate_template(input, OutputFormat.YAML)
    """

    # Regex patterns for validation
    DNS_SUBDOMAIN_PATTERN = re.compile(
        r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?(\.[a-z0-9]([-a-z0-9]*[a-z0-9])?)*$"
    )
    DNS_LABEL_PATTERN = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")
    MAC_ADDRESS_PATTERN = re.compile(r"^([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}$")
    IP_ADDRESS_PATTERN = re.compile(
        r"^(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}"
        r"(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)$"
    )
    CIDR_PATTERN = re.compile(
        r"^(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}"
        r"(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)/(?:[0-9]|[1-2][0-9]|3[0-2])$"
    )

    def __init__(self, default_namespace: str = "default") -> None:
        """Initialize the template generator.

        Args:
            default_namespace: Default namespace for resources.
        """
        self.default_namespace = default_namespace
        self._environment_defaults: dict[str, Any] = {}

    def load_environment_defaults(self, config: dict[str, Any]) -> None:
        """Load environment-specific default values.

        Args:
            config: Configuration dictionary with defaults.
        """
        self._environment_defaults = config
        logger.debug(
            "loaded_environment_defaults",
            defaults_count=len(config),
        )

    def get_default(self, key: str, fallback: Any = None) -> Any:
        """Get an environment-specific default value.

        Args:
            key: Configuration key.
            fallback: Fallback value if key not found.

        Returns:
            Default value or fallback.
        """
        return self._environment_defaults.get(key, fallback)

    @abstractmethod
    def generate(self, **kwargs: Any) -> ResourceT:
        """Generate the Kubernetes resource.

        Subclasses must implement this method to create the specific
        resource type.

        Args:
            **kwargs: Resource-specific parameters.

        Returns:
            Generated Kubernetes resource.
        """
        ...

    def generate_template(
        self,
        resource: ResourceT,
        output_format: OutputFormat = OutputFormat.YAML,
    ) -> TemplateOutput:
        """Generate template output in the specified format.

        Args:
            resource: The Kubernetes resource to convert.
            output_format: Desired output format.

        Returns:
            TemplateOutput with the generated content.
        """
        # Get the YAML-friendly dictionary
        resource_dict = resource.to_yaml_dict()

        # Generate content based on format
        if output_format == OutputFormat.YAML:
            content = self._to_yaml(resource_dict)
        elif output_format == OutputFormat.JSON:
            content = self._to_json(resource_dict)
        elif output_format == OutputFormat.KUBECTL:
            content = self._to_kubectl_command(resource_dict)
        else:
            content = self._to_yaml(resource_dict)

        return TemplateOutput(
            format=output_format,
            content=content,
            resource_kind=resource.kind,
            resource_name=resource.metadata.name,
            resource_namespace=resource.metadata.namespace,
        )

    def _to_yaml(self, data: dict[str, Any]) -> str:
        """Convert dictionary to YAML string.

        Args:
            data: Dictionary to convert.

        Returns:
            YAML string.
        """
        return yaml.dump(
            data,
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
            width=120,
        )

    def _to_json(self, data: dict[str, Any]) -> str:
        """Convert dictionary to JSON string.

        Args:
            data: Dictionary to convert.

        Returns:
            JSON string.
        """
        return json.dumps(data, indent=2, ensure_ascii=False)

    def _to_kubectl_command(self, data: dict[str, Any]) -> str:
        """Convert dictionary to kubectl apply command.

        Args:
            data: Dictionary to convert.

        Returns:
            kubectl command with heredoc.
        """
        yaml_content = self._to_yaml(data)
        return f"kubectl apply -f - <<'EOF'\n{yaml_content}EOF"

    # =========================================================================
    # Validation Utilities
    # =========================================================================

    def validate_dns_subdomain(self, name: str, field_name: str = "name") -> None:
        """Validate a DNS subdomain name.

        DNS subdomain names must:
        - Be lowercase
        - Start with an alphanumeric character
        - Contain only alphanumeric characters, '-', or '.'
        - End with an alphanumeric character
        - Be at most 253 characters

        Args:
            name: Name to validate.
            field_name: Field name for error messages.

        Raises:
            ValidationError: If name is invalid.
        """
        if not name:
            raise ValidationError(
                message=f"Field '{field_name}' cannot be empty",
                field=field_name,
                constraint="non-empty string",
            )

        if len(name) > 253:
            raise ValidationError(
                message=f"Field '{field_name}' exceeds maximum length of 253 characters",
                field=field_name,
                value=name,
                constraint="max length 253",
            )

        if not self.DNS_SUBDOMAIN_PATTERN.match(name):
            raise ValidationError(
                message=(
                    f"Field '{field_name}' must be a valid DNS subdomain name: "
                    "lowercase alphanumeric characters, '-' or '.', "
                    "starting and ending with an alphanumeric character"
                ),
                field=field_name,
                value=name,
                constraint="DNS subdomain name",
            )

    def validate_dns_label(self, name: str, field_name: str = "name") -> None:
        """Validate a DNS label name.

        DNS labels must:
        - Be lowercase
        - Start with an alphanumeric character
        - Contain only alphanumeric characters or '-'
        - End with an alphanumeric character
        - Be at most 63 characters

        Args:
            name: Name to validate.
            field_name: Field name for error messages.

        Raises:
            ValidationError: If name is invalid.
        """
        if not name:
            raise ValidationError(
                message=f"Field '{field_name}' cannot be empty",
                field=field_name,
                constraint="non-empty string",
            )

        if len(name) > 63:
            raise ValidationError(
                message=f"Field '{field_name}' exceeds maximum length of 63 characters",
                field=field_name,
                value=name,
                constraint="max length 63",
            )

        if not self.DNS_LABEL_PATTERN.match(name):
            raise ValidationError(
                message=(
                    f"Field '{field_name}' must be a valid DNS label: "
                    "lowercase alphanumeric characters or '-', "
                    "starting and ending with an alphanumeric character"
                ),
                field=field_name,
                value=name,
                constraint="DNS label",
            )

    def validate_mac_address(self, mac: str, field_name: str = "mac_address") -> None:
        """Validate a MAC address.

        Args:
            mac: MAC address to validate.
            field_name: Field name for error messages.

        Raises:
            ValidationError: If MAC address is invalid.
        """
        if not self.MAC_ADDRESS_PATTERN.match(mac):
            raise ValidationError(
                message=f"Field '{field_name}' must be a valid MAC address (aa:bb:cc:dd:ee:ff)",
                field=field_name,
                value=mac,
                constraint="MAC address format",
            )

    def validate_ip_address(self, ip: str, field_name: str = "ip_address") -> None:
        """Validate an IPv4 address.

        Args:
            ip: IP address to validate.
            field_name: Field name for error messages.

        Raises:
            ValidationError: If IP address is invalid.
        """
        if not self.IP_ADDRESS_PATTERN.match(ip):
            raise ValidationError(
                message=f"Field '{field_name}' must be a valid IPv4 address",
                field=field_name,
                value=ip,
                constraint="IPv4 address format",
            )

    def validate_cidr(self, cidr: str, field_name: str = "cidr") -> None:
        """Validate a CIDR notation.

        Args:
            cidr: CIDR to validate.
            field_name: Field name for error messages.

        Raises:
            ValidationError: If CIDR is invalid.
        """
        if not self.CIDR_PATTERN.match(cidr):
            raise ValidationError(
                message=f"Field '{field_name}' must be a valid CIDR notation (e.g., 10.0.0.0/24)",
                field=field_name,
                value=cidr,
                constraint="CIDR notation",
            )

    def validate_vlan_id(self, vlan_id: int, field_name: str = "vlan_id") -> None:
        """Validate a VLAN ID.

        Args:
            vlan_id: VLAN ID to validate.
            field_name: Field name for error messages.

        Raises:
            ValidationError: If VLAN ID is invalid.
        """
        if vlan_id < 1 or vlan_id > 4094:
            raise ValidationError(
                message=f"Field '{field_name}' must be between 1 and 4094",
                field=field_name,
                value=vlan_id,
                constraint="1-4094",
            )

    def validate_mtu(self, mtu: int, field_name: str = "mtu") -> None:
        """Validate an MTU value.

        Args:
            mtu: MTU to validate.
            field_name: Field name for error messages.

        Raises:
            ValidationError: If MTU is invalid.
        """
        if mtu < 68 or mtu > 65535:
            raise ValidationError(
                message=f"Field '{field_name}' must be between 68 and 65535",
                field=field_name,
                value=mtu,
                constraint="68-65535",
            )

    # =========================================================================
    # Diff Utilities
    # =========================================================================

    @staticmethod
    def generate_diff(
        before: str | dict[str, Any],
        after: str | dict[str, Any],
        context_lines: int = 3,
    ) -> DiffOutput:
        """Generate a unified diff between two contents.

        Args:
            before: Original content (string or dict).
            after: Modified content (string or dict).
            context_lines: Number of context lines in diff.

        Returns:
            DiffOutput with the diff information.
        """
        # Convert dicts to YAML for comparison
        if isinstance(before, dict):
            before_str = yaml.dump(before, default_flow_style=False, sort_keys=True)
        else:
            before_str = before

        if isinstance(after, dict):
            after_str = yaml.dump(after, default_flow_style=False, sort_keys=True)
        else:
            after_str = after

        # Generate unified diff
        diff_lines = list(
            difflib.unified_diff(
                before_str.splitlines(keepends=True),
                after_str.splitlines(keepends=True),
                fromfile="before",
                tofile="after",
                n=context_lines,
            )
        )

        diff_text = "".join(diff_lines)
        has_changes = len(diff_lines) > 0

        # Generate changes summary
        changes_summary = []
        for line in diff_lines:
            if line.startswith("+") and not line.startswith("+++"):
                changes_summary.append(f"Added: {line[1:].strip()[:50]}")
            elif line.startswith("-") and not line.startswith("---"):
                changes_summary.append(f"Removed: {line[1:].strip()[:50]}")

        # Truncate before/after for output
        max_length = 2000
        before_truncated = (
            before_str[:max_length] + "..." if len(before_str) > max_length else before_str
        )
        after_truncated = (
            after_str[:max_length] + "..." if len(after_str) > max_length else after_str
        )

        return DiffOutput(
            has_changes=has_changes,
            diff_text=diff_text if has_changes else "No changes",
            before=before_truncated,
            after=after_truncated,
            changes_summary=changes_summary[:20],  # Limit to 20 changes
        )

    # =========================================================================
    # Label Utilities
    # =========================================================================

    @staticmethod
    def build_standard_labels(
        cluster_name: str,
        region: str = "region-one",
        additional: dict[str, str] | None = None,
    ) -> dict[str, str]:
        """Build standard MOSK resource labels.

        Based on real MOSK cluster resources (BMHp, L2Template, etc).

        Args:
            cluster_name: Cluster name for cluster.sigs.k8s.io/cluster-name label.
            region: Region for kaas.mirantis.com/region label.
            additional: Additional custom labels.

        Returns:
            Dictionary of labels.
        """
        labels: dict[str, str] = {
            "cluster.sigs.k8s.io/cluster-name": cluster_name,
            "kaas.mirantis.com/provider": "baremetal",
            "kaas.mirantis.com/region": region,
        }

        if additional:
            labels.update(additional)

        return labels

    @staticmethod
    def build_machine_role_labels(
        role: str,
        additional: dict[str, str] | None = None,
    ) -> dict[str, str]:
        """Build metadata labels for a machine based on its role.

        These are labels for the Machine CR metadata, NOT nodeLabels.
        Based on real MOSK cluster Machine CRs.

        Args:
            role: Machine role (compute, control, storage, gateway).
            additional: Additional labels.

        Returns:
            Dictionary of metadata labels.
        """
        labels: dict[str, str] = {
            "kaas.mirantis.com/provider": "baremetal",
        }

        role_lower = role.lower()
        if role_lower == "control":
            # Control plane nodes have additional labels
            labels["cluster.sigs.k8s.io/control-plane"] = "controlplane"
            labels["hostlabel.bm.kaas.mirantis.com/controlplane"] = "controlplane"
            labels["hostlabel.bm.kaas.mirantis.com/worker"] = "worker"
        else:
            # All other roles are workers
            labels["hostlabel.bm.kaas.mirantis.com/worker"] = "worker"

        if additional:
            labels.update(additional)

        return labels
