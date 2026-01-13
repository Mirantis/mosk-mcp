"""Machine CRD models for MOSK node management.

This module provides Pydantic models for the Machine custom resource,
which represents a node in a MOSK cluster.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, field_validator

from mosk_mcp.adapters.crd.base import (
    KubernetesMetadata,
    KubernetesResource,
)


class MachinePhase(str, Enum):
    """Machine lifecycle phases."""

    PENDING = "Pending"
    PROVISIONING = "Provisioning"
    PROVISIONED = "Provisioned"
    RUNNING = "Running"
    DELETING = "Deleting"
    DELETED = "Deleted"
    FAILED = "Failed"
    UNKNOWN = "Unknown"


class BareMetalHostProfileRef(BaseModel):
    """Reference to a BareMetalHostProfile.

    This model handles both the object format (with name and namespace) and
    the legacy string format for backwards compatibility.

    Object format (current):
        bareMetalHostProfile:
          name: worker-nova-cmp-1
          namespace: lab

    String format (legacy):
        bareMetalHostProfile: worker-nova-cmp-1

    Attributes:
        name: Name of the BareMetalHostProfile.
        namespace: Namespace of the BareMetalHostProfile (optional).
    """

    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(
        ...,
        description="Name of the BareMetalHostProfile",
    )
    namespace: str | None = Field(
        default=None,
        description="Namespace of the BareMetalHostProfile (optional)",
    )

    @classmethod
    def from_value(
        cls, value: str | dict[str, Any] | BareMetalHostProfileRef | None
    ) -> BareMetalHostProfileRef:
        """Create a BareMetalHostProfileRef from various input formats.

        Args:
            value: Either a string (profile name), dict with name/namespace,
                   an existing BareMetalHostProfileRef, or None.

        Returns:
            BareMetalHostProfileRef instance.

        Raises:
            ValueError: If the input format is invalid (except None which returns empty).
        """
        # Handle None case - return empty profile ref
        if value is None:
            return cls(name="")
        if isinstance(value, BareMetalHostProfileRef):
            return value
        if isinstance(value, str):
            return cls(name=value)
        if isinstance(value, dict):
            name = value.get("name")
            if not name:
                raise ValueError("BareMetalHostProfile reference must have a 'name' field")
            return cls(name=name, namespace=value.get("namespace"))
        raise ValueError(f"Invalid BareMetalHostProfile reference type: {type(value).__name__}")

    def to_kubernetes(self) -> dict[str, Any]:
        """Convert to Kubernetes API format (object format).

        Returns:
            Dictionary with name and optionally namespace.
        """
        result: dict[str, Any] = {"name": self.name}
        if self.namespace:
            result["namespace"] = self.namespace
        return result

    def to_string(self) -> str:
        """Get the profile name as a string.

        Returns:
            Profile name, optionally with namespace prefix.
        """
        if self.namespace:
            return f"{self.namespace}/{self.name}"
        return self.name

    def __str__(self) -> str:
        """Return string representation."""
        return self.to_string()


# Type alias for accepting either string, object format, or None
# Note: Using Union for forward reference compatibility
BareMetalHostProfileRefInput = str | dict[str, Any] | BareMetalHostProfileRef | None


class NodeLabel(BaseModel):
    """Node label key-value pair.

    Attributes:
        key: Label key.
        value: Label value.
    """

    key: str = Field(..., description="Label key")
    value: str = Field(..., description="Label value")


class HostSelector(BaseModel):
    """Host selector for matching BareMetalHost resources.

    Attributes:
        match_labels: Labels that must match the BareMetalHost.
    """

    model_config = ConfigDict(populate_by_name=True)

    match_labels: dict[str, str] = Field(
        default_factory=dict,
        alias="matchLabels",
        description="Labels to match against BareMetalHost",
    )

    def to_kubernetes(self) -> dict[str, Any]:
        """Convert to Kubernetes API format."""
        return {"matchLabels": self.match_labels}


class L2TemplateSelector(BaseModel):
    """Selector for L2Template resources.

    Attributes:
        label: Label value to match L2Template (matches kaas.mirantis.com/l2-template-name).
    """

    model_config = ConfigDict(populate_by_name=True)

    label: str = Field(
        ...,
        description="L2Template label value to match",
    )

    def to_kubernetes(self) -> dict[str, Any]:
        """Convert to Kubernetes API format."""
        return {"label": self.label}


class MachineProviderSpec(BaseModel):
    """Provider-specific specification for bare metal machines.

    Attributes:
        api_version: API version for provider spec (baremetal.k8s.io/v1alpha1).
        kind: Kind for provider spec (BareMetalMachineProviderSpec).
        bare_metal_host_profile: Reference to BareMetalHostProfile.
            Accepts either a string (legacy) or object with name/namespace (current).
        host_selector: Selector to match BareMetalHost by labels.
        l2_template_selector: Selector to match L2Template.
        host_repositories: List of host repositories for package installation.
        public_keys: SSH public keys to install on the host.
        node_labels: Labels to apply to the Kubernetes node.
    """

    model_config = ConfigDict(populate_by_name=True)

    # Provider spec API version and kind
    provider_api_version: str = Field(
        default="baremetal.k8s.io/v1alpha1",
        alias="apiVersion",
        description="API version for the provider spec",
    )
    provider_kind: str = Field(
        default="BareMetalMachineProviderSpec",
        alias="kind",
        description="Kind for the provider spec",
    )

    bare_metal_host_profile: BareMetalHostProfileRef = Field(
        ...,
        alias="bareMetalHostProfile",
        description="Reference to BareMetalHostProfile to use",
    )
    host_selector: HostSelector | None = Field(
        default=None,
        alias="hostSelector",
        description="Selector to match BareMetalHost by labels",
    )
    l2_template_selector: L2TemplateSelector | None = Field(
        default=None,
        alias="l2TemplateSelector",
        description="Selector to match L2Template",
    )
    host_repositories: list[str] = Field(
        default_factory=list,
        alias="hostRepositories",
        description="List of repository names for package installation",
    )
    public_keys: list[str] = Field(
        default_factory=list,
        alias="publicKeys",
        description="SSH public keys to install",
    )
    node_labels: list[NodeLabel] = Field(
        default_factory=list,
        alias="nodeLabels",
        description="Labels to apply to the Kubernetes node",
    )

    @field_validator("bare_metal_host_profile", mode="before")
    @classmethod
    def parse_profile_ref(cls, v: BareMetalHostProfileRefInput) -> BareMetalHostProfileRef:
        """Parse bare_metal_host_profile from string or dict.

        Args:
            v: Input value (string, dict, or BareMetalHostProfileRef).

        Returns:
            BareMetalHostProfileRef instance.
        """
        return BareMetalHostProfileRef.from_value(v)

    @property
    def profile_name(self) -> str:
        """Get the profile name.

        Returns:
            Profile name string.
        """
        return self.bare_metal_host_profile.name

    @property
    def profile_namespace(self) -> str | None:
        """Get the profile namespace.

        Returns:
            Profile namespace or None.
        """
        return self.bare_metal_host_profile.namespace

    def to_kubernetes(self) -> dict[str, Any]:
        """Convert to Kubernetes API format.

        Uses the object format for bareMetalHostProfile.
        """
        result: dict[str, Any] = {
            "apiVersion": self.provider_api_version,
            "kind": self.provider_kind,
            "bareMetalHostProfile": self.bare_metal_host_profile.to_kubernetes(),
        }
        if self.host_selector:
            result["hostSelector"] = self.host_selector.to_kubernetes()
        if self.l2_template_selector:
            result["l2TemplateSelector"] = self.l2_template_selector.to_kubernetes()
        if self.host_repositories:
            result["hostRepositories"] = self.host_repositories
        if self.public_keys:
            result["publicKeys"] = self.public_keys
        if self.node_labels:
            result["nodeLabels"] = [{"key": nl.key, "value": nl.value} for nl in self.node_labels]
        return result

    def to_kubernetes_legacy(self) -> dict[str, Any]:
        """Convert to Kubernetes API format using legacy string format.

        Use this for clusters that expect the string format for
        bareMetalHostProfile.
        """
        result: dict[str, Any] = {
            "apiVersion": self.provider_api_version,
            "kind": self.provider_kind,
            "bareMetalHostProfile": self.bare_metal_host_profile.name,
        }
        if self.host_selector:
            result["hostSelector"] = self.host_selector.to_kubernetes()
        if self.l2_template_selector:
            result["l2TemplateSelector"] = self.l2_template_selector.to_kubernetes()
        if self.host_repositories:
            result["hostRepositories"] = self.host_repositories
        if self.public_keys:
            result["publicKeys"] = self.public_keys
        if self.node_labels:
            result["nodeLabels"] = [{"key": nl.key, "value": nl.value} for nl in self.node_labels]
        return result


class MachineSpec(BaseModel):
    """Specification for Machine resource.

    Attributes:
        provider_spec: Provider-specific configuration.
    """

    model_config = ConfigDict(populate_by_name=True)

    provider_spec: MachineProviderSpec = Field(
        ...,
        alias="providerSpec",
        description="Provider-specific machine configuration",
    )

    def to_kubernetes(self) -> dict[str, Any]:
        """Convert to Kubernetes API format."""
        return {
            "providerSpec": {
                "value": self.provider_spec.to_kubernetes(),
            },
        }


class MachineAddress(BaseModel):
    """Network address for a machine.

    Attributes:
        address: The address value.
        type: Type of address (e.g., InternalIP, ExternalIP, Hostname).
    """

    model_config = ConfigDict(populate_by_name=True)

    address: str
    type: str


class MachineStatus(BaseModel):
    """Status of Machine resource.

    Attributes:
        phase: Current machine phase.
        addresses: Network addresses of the machine.
        node_ref: Reference to the Kubernetes Node.
        provider_status: Provider-specific status information.
        error_reason: Reason for error if in Failed phase.
        error_message: Detailed error message.
        conditions: Status conditions.
    """

    model_config = ConfigDict(populate_by_name=True)

    phase: MachinePhase | None = Field(None, description="Current machine phase")
    addresses: list[MachineAddress] = Field(default_factory=list, description="Network addresses")
    node_ref: dict[str, str] | None = Field(
        None,
        alias="nodeRef",
        description="Reference to Kubernetes Node",
    )
    provider_status: dict[str, Any] | None = Field(
        None,
        alias="providerStatus",
        description="Provider-specific status",
    )
    error_reason: str | None = Field(
        None,
        alias="errorReason",
        description="Reason for error",
    )
    error_message: str | None = Field(
        None,
        alias="errorMessage",
        description="Detailed error message",
    )
    conditions: list[dict[str, Any]] = Field(default_factory=list, description="Status conditions")

    @property
    def internal_ip(self) -> str | None:
        """Get the internal IP address of the machine.

        Returns:
            Internal IP address or None if not found.
        """
        for addr in self.addresses:
            if addr.type == "InternalIP":
                return addr.address
        return None

    @property
    def hostname(self) -> str | None:
        """Get the hostname of the machine.

        Returns:
            Hostname or None if not found.
        """
        for addr in self.addresses:
            if addr.type == "Hostname":
                return addr.address
        return None


class Machine(KubernetesResource[MachineSpec, MachineStatus]):
    """Machine custom resource.

    Represents a node in a MOSK cluster, managing its lifecycle from
    provisioning through decommissioning.

    Example:
        machine = Machine(
            metadata=KubernetesMetadata(
                name="compute-01",
                namespace="default",
                labels={
                    "kaas.mirantis.com/provider": "baremetal",
                    "openstack-compute-node": "enabled",
                },
            ),
            spec=MachineSpec(
                provider_spec=MachineProviderSpec(
                    bare_metal_host_profile="compute-profile",
                ),
            ),
        )
    """

    API_VERSION: ClassVar[str] = "cluster.k8s.io/v1alpha1"
    KIND: ClassVar[str] = "Machine"
    PLURAL: ClassVar[str] = "machines"
    GROUP: ClassVar[str] = "cluster.k8s.io"

    api_version: str = Field(default="cluster.k8s.io/v1alpha1", alias="apiVersion")
    kind: str = Field(default="Machine")
    spec: MachineSpec
    status: MachineStatus | None = None

    # Standard labels for node roles
    LABEL_PROVIDER: ClassVar[str] = "kaas.mirantis.com/provider"
    LABEL_COMPUTE: ClassVar[str] = "openstack-compute-node"
    LABEL_CONTROL: ClassVar[str] = "openstack-control-plane"
    LABEL_GATEWAY: ClassVar[str] = "openstack-gateway"
    LABEL_STORAGE: ClassVar[str] = "role"

    def _has_node_label(self, key: str, value: str) -> bool:
        """Check if the machine has a specific nodeLabel in providerSpec.

        Args:
            key: Label key to check.
            value: Expected label value.

        Returns:
            True if the nodeLabel exists with the expected value.
        """
        for nl in self.spec.provider_spec.node_labels:
            if nl.key == key and nl.value == value:
                return True
        return False

    @property
    def is_compute(self) -> bool:
        """Check if this machine is a compute node.

        Checks both metadata.labels and spec.providerSpec.nodeLabels.
        """
        # Check metadata labels first
        if self.metadata.labels.get(self.LABEL_COMPUTE) == "enabled":
            return True
        # Check nodeLabels in providerSpec
        return self._has_node_label(self.LABEL_COMPUTE, "enabled")

    @property
    def is_control_plane(self) -> bool:
        """Check if this machine is a control plane node.

        Checks both metadata.labels and spec.providerSpec.nodeLabels.
        """
        if self.metadata.labels.get(self.LABEL_CONTROL) == "enabled":
            return True
        return self._has_node_label(self.LABEL_CONTROL, "enabled")

    @property
    def is_gateway(self) -> bool:
        """Check if this machine is a gateway node.

        Checks both metadata.labels and spec.providerSpec.nodeLabels.
        """
        if self.metadata.labels.get(self.LABEL_GATEWAY) == "enabled":
            return True
        return self._has_node_label(self.LABEL_GATEWAY, "enabled")

    @property
    def is_storage(self) -> bool:
        """Check if this machine is a storage node.

        Checks both metadata.labels and spec.providerSpec.nodeLabels.
        """
        if self.metadata.labels.get(self.LABEL_STORAGE) == "ceph-osd":
            return True
        return self._has_node_label(self.LABEL_STORAGE, "ceph-osd")

    @property
    def role(self) -> str:
        """Get the primary role of this machine.

        Returns:
            Machine role: 'compute', 'control', 'gateway', 'storage', or 'unknown'.
        """
        if self.is_control_plane:
            return "control"
        if self.is_compute:
            return "compute"
        if self.is_gateway:
            return "gateway"
        if self.is_storage:
            return "storage"
        return "unknown"

    @classmethod
    def create_compute(
        cls,
        name: str,
        namespace: str,
        profile: str,
        labels: dict[str, str] | None = None,
    ) -> Machine:
        """Create a compute node Machine.

        Args:
            name: Machine name.
            namespace: Kubernetes namespace.
            profile: BareMetalHostProfile name.
            labels: Additional labels to apply.

        Returns:
            Machine configured as compute node.
        """
        all_labels = {
            cls.LABEL_PROVIDER: "baremetal",
            cls.LABEL_COMPUTE: "enabled",
        }
        if labels:
            all_labels.update(labels)

        return cls(
            metadata=KubernetesMetadata(
                name=name,
                namespace=namespace,
                labels=all_labels,
            ),
            spec=MachineSpec(
                provider_spec=MachineProviderSpec(
                    bare_metal_host_profile=BareMetalHostProfileRef(name=profile),
                ),
            ),
        )

    @classmethod
    def create_control(
        cls,
        name: str,
        namespace: str,
        profile: str,
        labels: dict[str, str] | None = None,
    ) -> Machine:
        """Create a control plane Machine.

        Args:
            name: Machine name.
            namespace: Kubernetes namespace.
            profile: BareMetalHostProfile name.
            labels: Additional labels to apply.

        Returns:
            Machine configured as control plane node.
        """
        all_labels = {
            cls.LABEL_PROVIDER: "baremetal",
            cls.LABEL_CONTROL: "enabled",
        }
        if labels:
            all_labels.update(labels)

        return cls(
            metadata=KubernetesMetadata(
                name=name,
                namespace=namespace,
                labels=all_labels,
            ),
            spec=MachineSpec(
                provider_spec=MachineProviderSpec(
                    bare_metal_host_profile=BareMetalHostProfileRef(name=profile),
                ),
            ),
        )

    @classmethod
    def create_storage(
        cls,
        name: str,
        namespace: str,
        profile: str,
        labels: dict[str, str] | None = None,
    ) -> Machine:
        """Create a storage node Machine.

        Args:
            name: Machine name.
            namespace: Kubernetes namespace.
            profile: BareMetalHostProfile name.
            labels: Additional labels to apply.

        Returns:
            Machine configured as storage node.
        """
        all_labels = {
            cls.LABEL_PROVIDER: "baremetal",
            cls.LABEL_STORAGE: "ceph-osd",
        }
        if labels:
            all_labels.update(labels)

        return cls(
            metadata=KubernetesMetadata(
                name=name,
                namespace=namespace,
                labels=all_labels,
            ),
            spec=MachineSpec(
                provider_spec=MachineProviderSpec(
                    bare_metal_host_profile=BareMetalHostProfileRef(name=profile),
                ),
            ),
        )

    @classmethod
    def from_kubernetes(cls, data: dict[str, Any]) -> Machine:
        """Create from Kubernetes API response.

        Args:
            data: Resource dictionary from Kubernetes API.

        Returns:
            Machine instance.
        """
        spec_data = data.get("spec", {})
        provider_spec_data = spec_data.get("providerSpec", {}).get("value", {})

        # Handle bareMetalHostProfile which can be string or object
        # The field_validator in MachineProviderSpec handles both formats
        profile_ref = provider_spec_data.get("bareMetalHostProfile")
        if profile_ref is None:
            # Fallback to empty string when profile not specified
            profile_ref = ""

        # Parse nodeLabels - format: [{"key": "label-key", "value": "label-value"}, ...]
        raw_node_labels = provider_spec_data.get("nodeLabels", [])
        node_labels = [
            NodeLabel(key=nl.get("key", ""), value=nl.get("value", ""))
            for nl in raw_node_labels
            if isinstance(nl, dict) and "key" in nl
        ]

        # Parse hostSelector
        host_selector = None
        raw_host_selector = provider_spec_data.get("hostSelector")
        if raw_host_selector and isinstance(raw_host_selector, dict):
            host_selector = HostSelector(match_labels=raw_host_selector.get("matchLabels", {}))

        # Parse l2TemplateSelector
        l2_template_selector = None
        raw_l2_selector = provider_spec_data.get("l2TemplateSelector")
        if raw_l2_selector and isinstance(raw_l2_selector, dict):
            label = raw_l2_selector.get("label", "")
            if label:
                l2_template_selector = L2TemplateSelector(label=label)

        spec = MachineSpec(
            provider_spec=MachineProviderSpec(
                provider_api_version=provider_spec_data.get(
                    "apiVersion", "baremetal.k8s.io/v1alpha1"
                ),
                provider_kind=provider_spec_data.get("kind", "BareMetalMachineProviderSpec"),
                bare_metal_host_profile=profile_ref,
                host_selector=host_selector,
                l2_template_selector=l2_template_selector,
                host_repositories=provider_spec_data.get("hostRepositories", []),
                public_keys=provider_spec_data.get("publicKeys", []),
                node_labels=node_labels,
            ),
        )

        status = None
        if "status" in data:
            status_data = data["status"]
            addresses = [
                MachineAddress(
                    address=addr.get("address", ""),
                    type=addr.get("type", ""),
                )
                for addr in status_data.get("addresses", [])
            ]

            phase = None
            if "phase" in status_data:
                try:
                    phase = MachinePhase(status_data["phase"])
                except ValueError:
                    phase = MachinePhase.UNKNOWN

            status = MachineStatus(
                phase=phase,
                addresses=addresses,
                node_ref=status_data.get("nodeRef"),
                provider_status=status_data.get("providerStatus"),
                error_reason=status_data.get("errorReason"),
                error_message=status_data.get("errorMessage"),
                conditions=status_data.get("conditions", []),
            )

        return cls(
            metadata=KubernetesMetadata.from_kubernetes(data["metadata"]),
            spec=spec,
            status=status,
        )
