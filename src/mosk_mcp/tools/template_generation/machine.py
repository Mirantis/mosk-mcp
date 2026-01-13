"""Machine template generation tool.

This module provides the generate_machine tool for generating Machine
custom resources for adding nodes to MOSK clusters.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from mosk_mcp.adapters.crd.base import KubernetesMetadata
from mosk_mcp.adapters.crd.machine import (
    BareMetalHostProfileRef,
    HostSelector,
    L2TemplateSelector,
    Machine,
    MachineProviderSpec,
    MachineSpec,
    NodeLabel,
)
from mosk_mcp.observability.logging import get_logger
from mosk_mcp.tools.template_generation.base import (
    BaseTemplateGenerator,
    OutputFormat,
    TemplateOutput,
)


logger = get_logger(__name__)


class GenerateMachineInput(BaseModel):
    """Input parameters for generating a Machine CR.

    Attributes:
        name: Machine name (typically matches the hostname).
        role: Node role in the cluster.
        bmhp_ref: Reference to the BareMetalHostProfile to use.
        bmhp_namespace: Namespace of the BareMetalHostProfile.
        host_id: Host ID for hostSelector (usually same as name).
        l2_template_label: Label value for l2TemplateSelector.
        cluster_name: Name of the cluster (for cluster.sigs.k8s.io/cluster-name label).
        region: Region label for kaas.mirantis.com/region.
        node_labels: Labels to apply to the Kubernetes node after provisioning.
        host_repositories: List of repository names for package installation.
        public_keys: SSH public keys to install on the host.
        additional_labels: Additional labels to apply beyond role labels.
        annotations: Annotations to apply.
        namespace: Kubernetes namespace for the resource.
        output_format: Output format (yaml, json, or kubectl).
    """

    name: str = Field(
        ...,
        description="Machine name (typically the hostname)",
        min_length=1,
        max_length=63,
        pattern=r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$",
    )
    role: Literal["compute", "control", "storage", "gateway"] = Field(
        ...,
        description="Node role in the cluster",
    )
    bmhp_ref: str = Field(
        ...,
        description="Reference to BareMetalHostProfile name",
        min_length=1,
        max_length=253,
    )
    bmhp_namespace: str | None = Field(
        default=None,
        description="Namespace of the BareMetalHostProfile (defaults to same as Machine)",
    )
    host_id: str | None = Field(
        default=None,
        description="Host ID for hostSelector (defaults to Machine name). Used to match BareMetalHostInventory.",
    )
    l2_template_label: str | None = Field(
        default=None,
        description="Label value for L2Template selector (e.g., 'mosl2template62u')",
    )
    cluster_name: str = Field(
        default="mos",
        description="Cluster name for cluster.sigs.k8s.io/cluster-name label",
    )
    region: str = Field(
        default="region-one",
        description="Region label value for kaas.mirantis.com/region",
    )
    node_labels: list[dict[str, str]] = Field(
        default_factory=list,
        description="Labels to apply to the K8s node. Format: [{'key': 'label-key', 'value': 'label-value'}]",
    )
    host_repositories: list[str] = Field(
        default_factory=list,
        description="List of repository names for package installation",
    )
    public_keys: list[str] = Field(
        default_factory=list,
        description="SSH public keys to install on the host",
    )
    additional_labels: dict[str, str] = Field(
        default_factory=dict,
        description="Additional labels to apply beyond role labels",
    )
    annotations: dict[str, str] = Field(
        default_factory=dict,
        description="Annotations to apply to the resource",
    )
    namespace: str = Field(
        default="default",
        description="Kubernetes namespace for the resource",
    )
    output_format: OutputFormat = Field(
        default=OutputFormat.YAML,
        description="Output format: yaml, json, or kubectl",
    )


class GenerateMachineOutput(BaseModel):
    """Output from generate_machine tool.

    Attributes:
        template: Generated template output.
        role_labels: Labels applied based on the role.
        related_resources: List of related resources that should exist.
        next_steps: Suggested next steps after creating the machine.
    """

    template: TemplateOutput = Field(..., description="Generated Machine template")
    role_labels: dict[str, str] = Field(..., description="Labels applied based on role")
    related_resources: list[str] = Field(
        default_factory=list,
        description="Related resources that should exist",
    )
    next_steps: str = Field(..., description="Suggested next steps")


class MachineGenerator(BaseTemplateGenerator[Machine]):
    """Generator for Machine custom resources.

    This generator creates Machine CRs for adding nodes to MOSK clusters,
    with proper role labels and network configuration references.

    Example:
        generator = MachineGenerator()
        input_params = GenerateMachineInput(
            name="compute-01",
            role="compute",
            bmhp_ref="compute-profile",
        )
        output = generator.generate_machine(input_params)
    """

    def generate(self, **kwargs: Any) -> Machine:
        """Generate a Machine resource.

        Args:
            **kwargs: Parameters from GenerateMachineInput.

        Returns:
            Machine resource.
        """
        input_data = GenerateMachineInput(**kwargs)
        return self._create_machine(input_data)

    def _create_machine(self, input_data: GenerateMachineInput) -> Machine:
        """Create a Machine resource from input.

        Args:
            input_data: Validated input parameters.

        Returns:
            Machine resource.
        """
        # Validate machine name
        self.validate_dns_label(input_data.name, "name")

        # Build role-specific labels with required MOSK labels
        role_labels = self.build_machine_role_labels(
            role=input_data.role,
            additional=input_data.additional_labels,
        )

        # Add required MOSK labels
        role_labels["cluster.sigs.k8s.io/cluster-name"] = input_data.cluster_name
        role_labels["kaas.mirantis.com/region"] = input_data.region

        # Create metadata
        metadata = KubernetesMetadata(
            name=input_data.name,
            namespace=input_data.namespace,
            labels=role_labels,
            annotations=input_data.annotations,
        )

        # Create hostSelector - uses host_id or defaults to machine name
        host_id = input_data.host_id or input_data.name
        host_selector = HostSelector(match_labels={"kaas.mirantis.com/baremetalhost-id": host_id})

        # Create l2TemplateSelector if provided
        l2_template_selector = None
        if input_data.l2_template_label:
            l2_template_selector = L2TemplateSelector(label=input_data.l2_template_label)

        # Build node labels from input
        node_labels = []
        for nl in input_data.node_labels:
            if "key" in nl and "value" in nl:
                node_labels.append(NodeLabel(key=nl["key"], value=nl["value"]))

        # Add role-specific node labels (these go on the K8s node, not Machine metadata)
        # Based on real MOSK cluster Machine CRs
        if input_data.role == "compute":
            node_labels.append(NodeLabel(key="openstack-compute-node", value="enabled"))
            node_labels.append(NodeLabel(key="openvswitch", value="enabled"))
        elif input_data.role == "control":
            node_labels.append(NodeLabel(key="openstack-control-plane", value="enabled"))
            node_labels.append(NodeLabel(key="openstack-gateway", value="enabled"))
            node_labels.append(NodeLabel(key="openstack-compute-node", value="enabled"))
            node_labels.append(NodeLabel(key="openvswitch", value="enabled"))
            node_labels.append(NodeLabel(key="stacklight", value="enabled"))
        elif input_data.role == "storage":
            node_labels.append(NodeLabel(key="role", value="ceph-osd-node"))
        elif input_data.role == "gateway":
            node_labels.append(NodeLabel(key="openstack-gateway", value="enabled"))
            node_labels.append(NodeLabel(key="openvswitch", value="enabled"))

        # Create provider spec with proper structure
        bmhp_namespace = input_data.bmhp_namespace or input_data.namespace
        provider_spec = MachineProviderSpec(
            bare_metal_host_profile=BareMetalHostProfileRef(
                name=input_data.bmhp_ref, namespace=bmhp_namespace
            ),
            host_selector=host_selector,
            l2_template_selector=l2_template_selector,
            host_repositories=input_data.host_repositories,
            public_keys=input_data.public_keys,
            node_labels=node_labels,
        )

        # Create machine spec
        spec = MachineSpec(provider_spec=provider_spec)

        return Machine(
            metadata=metadata,
            spec=spec,
        )

    def _get_related_resources(self, input_data: GenerateMachineInput) -> list[str]:
        """Get list of related resources that should exist.

        Args:
            input_data: Input parameters.

        Returns:
            List of related resource references.
        """
        resources = [
            f"BareMetalHostProfile/{input_data.bmhp_ref}",
            f"BareMetalHostInventory/{input_data.host_id or input_data.name}",
        ]

        if input_data.l2_template_label:
            resources.append(f"L2Template/{input_data.l2_template_label}")

        return resources

    def _get_next_steps(self, input_data: GenerateMachineInput) -> str:
        """Get next steps after machine creation.

        Args:
            input_data: Input parameters.

        Returns:
            Next steps instructions.
        """
        role_specific: dict[str, str] = {
            "compute": """
4. **Configure Nova Compute**:
   After the machine is provisioned, verify Nova compute is registered:
   ```bash
   openstack compute service list --host {name}
   ```

5. **Enable Compute Host**:
   If the compute service is disabled:
   ```bash
   openstack compute service set --enable {name} nova-compute
   ```
""",
            "control": """
4. **Verify Control Plane**:
   After provisioning, verify control plane services:
   ```bash
   kubectl get pods -n openstack -l application=keystone
   kubectl get pods -n openstack -l application=nova-api
   ```

5. **Check Cluster Membership**:
   Verify the node joined the control plane:
   ```bash
   kubectl get nodes
   ```
""",
            "storage": """
4. **Verify Ceph OSD**:
   After provisioning, check if OSDs were created:
   ```bash
   kubectl exec -n rook-ceph deploy/rook-ceph-tools -- ceph osd tree
   ```

5. **Check OSD Status**:
   Verify OSD health:
   ```bash
   kubectl exec -n rook-ceph deploy/rook-ceph-tools -- ceph osd status
   ```
""",
            "gateway": """
4. **Verify Network Agent**:
   After provisioning, check network agent status:
   ```bash
   openstack network agent list --host {name}
   ```

5. **Enable Network Agent**:
   If agents are disabled:
   ```bash
   openstack network agent set --enable <agent-id>
   ```
""",
        }

        host_id = input_data.host_id or input_data.name
        base_steps = f"""## Next Steps for Machine '{input_data.name}'

1. **Verify Prerequisites**:
   Ensure the following resources exist:
   - BareMetalHostProfile: {input_data.bmhp_ref}
   - BareMetalHostInventory: {host_id}
   {f"- L2Template: {input_data.l2_template_label}" if input_data.l2_template_label else ""}

2. **Apply the Machine CR**:
   ```bash
   kubectl apply -f {input_data.name}-machine.yaml
   ```

3. **Monitor Provisioning**:
   Watch the machine status:
   ```bash
   kubectl get machine {input_data.name} -n {input_data.namespace} -w
   kubectl describe machine {input_data.name} -n {input_data.namespace}
   ```
"""

        role_steps = role_specific.get(input_data.role, "").format(name=input_data.name)

        return base_steps + role_steps

    def generate_machine(self, input_data: GenerateMachineInput) -> GenerateMachineOutput:
        """Generate complete Machine output with related resources info.

        Args:
            input_data: Input parameters for generation.

        Returns:
            Complete output with Machine template and guidance.
        """
        logger.info(
            "generating_machine",
            name=input_data.name,
            role=input_data.role,
        )

        # Generate the Machine resource
        machine = self._create_machine(input_data)

        # Generate template output
        template = self.generate_template(machine, input_data.output_format)

        # Get role labels
        role_labels = self.build_machine_role_labels(input_data.role)

        # Get related resources
        related = self._get_related_resources(input_data)

        # Get next steps
        next_steps = self._get_next_steps(input_data)

        logger.info(
            "generated_machine",
            name=input_data.name,
            namespace=input_data.namespace,
            role=input_data.role,
        )

        return GenerateMachineOutput(
            template=template,
            role_labels=role_labels,
            related_resources=related,
            next_steps=next_steps,
        )


# Singleton instance
_generator: MachineGenerator | None = None


def get_machine_generator() -> MachineGenerator:
    """Get the singleton Machine generator instance.

    Returns:
        MachineGenerator instance.
    """
    global _generator
    if _generator is None:
        _generator = MachineGenerator()
    return _generator


async def generate_machine(
    name: str,
    role: Literal["compute", "control", "storage", "gateway"],
    bmhp_ref: str,
    bmhp_namespace: str | None = None,
    host_id: str | None = None,
    l2_template_label: str | None = None,
    cluster_name: str = "mos",
    region: str = "region-one",
    node_labels: list[dict[str, str]] | None = None,
    host_repositories: list[str] | None = None,
    public_keys: list[str] | None = None,
    additional_labels: dict[str, str] | None = None,
    annotations: dict[str, str] | None = None,
    namespace: str = "default",
    output_format: OutputFormat = OutputFormat.YAML,
) -> GenerateMachineOutput:
    """Generate a Machine CR for adding a node to MOSK cluster.

    This tool generates a Machine custom resource that represents a node
    in a MOSK cluster. The Machine CR triggers provisioning of the physical
    server through the bare metal provider.

    The Machine resource is used to:
    - Define the node's role in the cluster (compute, control, storage, gateway)
    - Reference the BareMetalHostProfile for hardware configuration
    - Link to L2Template for network interface layout
    - Select the BareMetalHost via hostSelector
    - Configure SSH access and package repositories

    Prerequisites before applying a Machine CR:
    - BareMetalHostInventory with matching host_id must exist
    - BareMetalHostProfile referenced by bmhp_ref must exist
    - L2Template for network configuration (if l2_template_label is specified)

    Args:
        name: Machine name. Must be a valid DNS label.
        role: Node role in the cluster:
            - compute: OpenStack compute/hypervisor node
            - control: OpenStack control plane node
            - storage: Ceph storage node (OSD)
            - gateway: Network gateway node
        bmhp_ref: Name of the BareMetalHostProfile to use for provisioning.
        bmhp_namespace: Namespace of the BareMetalHostProfile (defaults to same as Machine).
        host_id: Host ID for hostSelector to match BMHi (defaults to Machine name).
        l2_template_label: Label value for L2Template selector (e.g., 'mosl2template62u').
        cluster_name: Cluster name for cluster.sigs.k8s.io/cluster-name label.
        region: Region label value for kaas.mirantis.com/region.
        node_labels: Labels to apply to K8s node after provisioning.
            Format: [{'key': 'label-key', 'value': 'label-value'}]
        host_repositories: List of repository names for package installation.
        public_keys: SSH public keys to install for access.
        additional_labels: Extra labels beyond the standard role labels.
        annotations: Annotations to add to the resource.
        namespace: Kubernetes namespace for the resource.
        output_format: Output format (yaml, json, or kubectl command).

    Returns:
        GenerateMachineOutput containing:
        - template: The generated Machine CR
        - role_labels: Labels applied based on the role
        - related_resources: Resources that should exist before applying
        - next_steps: Instructions for applying and verifying the machine

    Example:
        >>> output = await generate_machine(
        ...     name="compute-01",
        ...     role="compute",
        ...     bmhp_ref="worker-nova-cmp-1",
        ...     l2_template_label="mosl2template62u",
        ...     node_labels=[{"key": "node-type", "value": "sriov"}],
        ... )
        >>> print(output.template.content)
    """
    generator = get_machine_generator()

    input_data = GenerateMachineInput(
        name=name,
        role=role,
        bmhp_ref=bmhp_ref,
        bmhp_namespace=bmhp_namespace,
        host_id=host_id,
        l2_template_label=l2_template_label,
        cluster_name=cluster_name,
        region=region,
        node_labels=node_labels or [],
        host_repositories=host_repositories or [],
        public_keys=public_keys or [],
        additional_labels=additional_labels or {},
        annotations=annotations or {},
        namespace=namespace,
        output_format=output_format,
    )

    return generator.generate_machine(input_data)
