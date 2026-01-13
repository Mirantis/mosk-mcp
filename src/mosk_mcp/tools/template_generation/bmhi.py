"""BareMetalHostInventory template generation tool.

This module provides the generate_bmhi tool for generating BareMetalHostInventory
custom resources for hardware discovery in MOSK clusters.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from mosk_mcp.adapters.crd.baremetal import (
    BareMetalHostInventory,
    BareMetalHostInventorySpec,
    BMCSpec,
)
from mosk_mcp.adapters.crd.base import KubernetesMetadata
from mosk_mcp.observability.logging import get_logger
from mosk_mcp.tools.template_generation.base import (
    BaseTemplateGenerator,
    OutputFormat,
    TemplateOutput,
)


logger = get_logger(__name__)


class GenerateBMHIInput(BaseModel):
    """Input parameters for generating a BareMetalHostInventory CR.

    Attributes:
        hostname: Server hostname (used as resource name).
        bmc_address: BMC address (plain IP or protocol-prefixed).
        boot_mode: Boot mode (UEFI or legacy).
        bmc_credentials_secret: Name of the Secret containing BMC credentials.
        boot_mac_address: MAC address of the primary boot interface.
        bmc_type: Type of BMC protocol to use.
        disable_tls_verify: Skip TLS certificate verification for Redfish.
        hardware_profile: Optional reference to a BareMetalHostProfile.
        online: Desired power state (True = powered on).
        namespace: Kubernetes namespace for the resource.
        labels: Additional labels to apply to the resource.
        annotations: Annotations to apply to the resource.
        output_format: Output format (yaml, json, or kubectl).
    """

    hostname: str = Field(
        ...,
        description="Server hostname, used as the resource name",
        min_length=1,
        max_length=63,
        pattern=r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$",
    )
    bmc_address: str = Field(
        ...,
        description="BMC address. Supports plain IP (e.g., '192.168.1.100') or protocol-prefixed (e.g., 'ipmi://192.168.1.100', 'redfish://server.local:443')",
        min_length=1,
    )
    boot_mode: Literal["UEFI", "legacy"] = Field(
        default="UEFI",
        description="Boot mode: UEFI (default) or legacy",
    )
    bmc_credentials_secret: str = Field(
        ...,
        description="Name of the Kubernetes Secret containing BMC username and password",
        min_length=1,
        max_length=253,
    )
    boot_mac_address: str = Field(
        ...,
        description="MAC address of the primary boot interface (format: aa:bb:cc:dd:ee:ff)",
        pattern=r"^([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}$",
    )
    bmc_type: Literal["ipmi", "redfish", "idrac", "ilo"] = Field(
        default="ipmi",
        description="BMC protocol type",
    )
    disable_tls_verify: bool = Field(
        default=False,
        description="Skip TLS certificate verification (for Redfish/iDRAC/iLO)",
    )
    hardware_profile: str | None = Field(
        default=None,
        description="Optional reference to a BareMetalHostProfile name",
    )
    online: bool = Field(
        default=True,
        description="Desired power state (True = powered on)",
    )
    namespace: str = Field(
        default="default",
        description="Kubernetes namespace for the resource",
    )
    labels: dict[str, str] = Field(
        default_factory=dict,
        description="Additional labels to apply to the resource",
    )
    annotations: dict[str, str] = Field(
        default_factory=dict,
        description="Annotations to apply to the resource",
    )
    output_format: OutputFormat = Field(
        default=OutputFormat.YAML,
        description="Output format: yaml, json, or kubectl",
    )


class GenerateBMHIOutput(BaseModel):
    """Output from generate_bmhi tool.

    Attributes:
        template: Generated template output.
        bmc_secret_template: Template for the BMC credentials Secret.
        instructions: Instructions for applying the resources.
    """

    template: TemplateOutput = Field(..., description="Generated BMHi template")
    bmc_secret_template: str = Field(..., description="Template for BMC credentials Secret")
    instructions: str = Field(..., description="Instructions for applying the resources")


class BMHIGenerator(BaseTemplateGenerator[BareMetalHostInventory]):
    """Generator for BareMetalHostInventory custom resources.

    This generator creates BMHi CRs for hardware discovery, including
    the associated BMC credentials Secret template.

    Example:
        generator = BMHIGenerator()
        input_params = GenerateBMHIInput(
            hostname="server-01",
            bmc_address="ipmi://192.168.1.100",
            bmc_credentials_secret="server-01-bmc-secret",
            boot_mac_address="aa:bb:cc:dd:ee:ff",
        )
        output = generator.generate_bmhi(input_params)
    """

    def generate(self, **kwargs: Any) -> BareMetalHostInventory:
        """Generate a BareMetalHostInventory resource.

        Args:
            **kwargs: Parameters from GenerateBMHIInput.

        Returns:
            BareMetalHostInventory resource.
        """
        input_data = GenerateBMHIInput(**kwargs)
        return self._create_bmhi(input_data)

    def _create_bmhi(self, input_data: GenerateBMHIInput) -> BareMetalHostInventory:
        """Create a BareMetalHostInventory resource from input.

        Args:
            input_data: Validated input parameters.

        Returns:
            BareMetalHostInventory resource.
        """
        # Validate inputs
        self.validate_dns_label(input_data.hostname, "hostname")
        self.validate_mac_address(input_data.boot_mac_address, "boot_mac_address")

        # BMC address - strip protocol prefixes if present
        # User may provide ipmi://IP, redfish://IP, etc. but we only need plain IP
        bmc_address = input_data.bmc_address
        for prefix in ("ipmi://", "redfish://", "ilo://", "idrac://", "https://", "http://"):
            if bmc_address.lower().startswith(prefix):
                bmc_address = bmc_address[len(prefix) :]
                break

        # Build labels with required MOSK labels
        labels = {
            "kaas.mirantis.com/baremetalhost-id": input_data.hostname,
            "kaas.mirantis.com/provider": "baremetal",
        }
        # Add any additional labels
        labels.update(input_data.labels)

        # Build annotations with required storage sort term
        annotations = {
            "inspect.metal3.io/hardwaredetails-storage-sort-term": "hctl ASC, wwn ASC, by_id ASC, name ASC",
        }
        # Add any additional annotations
        annotations.update(input_data.annotations)

        # Create metadata
        metadata = KubernetesMetadata(
            name=input_data.hostname,
            namespace=input_data.namespace,
            labels=labels,
            annotations=annotations,
        )

        # Create BMC spec
        bmc_spec = BMCSpec(
            address=bmc_address,
            bmh_credentials_name=input_data.bmc_credentials_secret,
            disable_certificate_verification=input_data.disable_tls_verify,
        )

        # Create BMHi spec
        spec = BareMetalHostInventorySpec(
            boot_mac_address=input_data.boot_mac_address.lower(),
            boot_mode=input_data.boot_mode,
            bmc=bmc_spec,
            hardware_profile=input_data.hardware_profile,
            online=input_data.online,
        )

        return BareMetalHostInventory(
            metadata=metadata,
            spec=spec,
        )

    def generate_bmc_secret_template(
        self,
        secret_name: str,
        namespace: str = "default",
    ) -> str:
        """Generate a template for the BMC credentials Secret.

        Args:
            secret_name: Name for the Secret.
            namespace: Kubernetes namespace.

        Returns:
            YAML template for the Secret with placeholders.
        """
        secret_template = {
            "apiVersion": "v1",
            "kind": "Secret",
            "metadata": {
                "name": secret_name,
                "namespace": namespace,
                "labels": {
                    "environment.metal3.io": "baremetal",
                    "kaas.mirantis.com/credentials": secret_name,
                },
            },
            "type": "Opaque",
            "stringData": {
                "username": "<BMC_USERNAME>",
                "password": "<BMC_PASSWORD>",
            },
        }

        return self._to_yaml(secret_template)

    def generate_bmhi(self, input_data: GenerateBMHIInput) -> GenerateBMHIOutput:
        """Generate complete BMHi output including Secret template.

        Args:
            input_data: Input parameters for generation.

        Returns:
            Complete output with BMHi template and Secret template.
        """
        logger.info(
            "generating_bmhi",
            hostname=input_data.hostname,
            bmc_type=input_data.bmc_type,
        )

        # Generate the BMHi resource
        bmhi = self._create_bmhi(input_data)

        # Generate template output
        template = self.generate_template(bmhi, input_data.output_format)

        # Generate BMC secret template
        secret_template = self.generate_bmc_secret_template(
            secret_name=input_data.bmc_credentials_secret,
            namespace=input_data.namespace,
        )

        # Build instructions
        instructions = self._build_instructions(input_data)

        logger.info(
            "generated_bmhi",
            hostname=input_data.hostname,
            namespace=input_data.namespace,
        )

        return GenerateBMHIOutput(
            template=template,
            bmc_secret_template=secret_template,
            instructions=instructions,
        )

    def _build_instructions(self, input_data: GenerateBMHIInput) -> str:
        """Build instructions for applying the resources.

        Args:
            input_data: Input parameters.

        Returns:
            Instructions string.
        """
        return f"""## BareMetalHostInventory Setup Instructions

1. **Create BMC Credentials Secret**:
   First, create the Secret containing BMC credentials. Replace placeholders with actual values:

   ```bash
   kubectl create secret generic {input_data.bmc_credentials_secret} \\
     --namespace={input_data.namespace} \\
     --from-literal=username=<BMC_USERNAME> \\
     --from-literal=password=<BMC_PASSWORD>
   ```

2. **Apply BareMetalHostInventory**:
   Apply the generated BMHi CR:

   ```bash
   kubectl apply -f {input_data.hostname}-bmhi.yaml
   ```

3. **Verify Hardware Discovery**:
   Check the BMHi status for hardware discovery:

   ```bash
   kubectl get baremetalhostinventory {input_data.hostname} -n {input_data.namespace}
   kubectl describe baremetalhostinventory {input_data.hostname} -n {input_data.namespace}
   ```

4. **Check Provisioning State**:
   Wait for the host to be discovered and registered:

   ```bash
   kubectl get bmhi {input_data.hostname} -n {input_data.namespace} -o jsonpath='{{.status.provisioningState}}'
   ```

### Important Notes:
- Ensure BMC is accessible from the cluster network
- Verify the boot MAC address matches the primary network interface
- For Redfish/iDRAC/iLO, you may need to disable TLS verification for self-signed certificates
"""


# Singleton instance for the generator
_generator: BMHIGenerator | None = None


def get_bmhi_generator() -> BMHIGenerator:
    """Get the singleton BMHi generator instance.

    Returns:
        BMHIGenerator instance.
    """
    global _generator
    if _generator is None:
        _generator = BMHIGenerator()
    return _generator


async def generate_bmhi(
    hostname: str,
    bmc_address: str,
    bmc_credentials_secret: str,
    boot_mac_address: str,
    boot_mode: Literal["UEFI", "legacy"] = "UEFI",
    bmc_type: Literal["ipmi", "redfish", "idrac", "ilo"] = "ipmi",
    disable_tls_verify: bool = False,
    hardware_profile: str | None = None,
    online: bool = True,
    namespace: str = "default",
    labels: dict[str, str] | None = None,
    annotations: dict[str, str] | None = None,
    output_format: OutputFormat = OutputFormat.YAML,
) -> GenerateBMHIOutput:
    """Generate a BareMetalHostInventory CR for hardware discovery.

    This tool generates a BareMetalHostInventory custom resource that registers
    a bare metal server for hardware discovery in MOSK. It also provides
    a template for the required BMC credentials Secret.

    The BareMetalHostInventory (BMHi) resource is used to:
    - Register physical servers with the cluster
    - Enable out-of-band management via BMC (IPMI/Redfish)
    - Trigger hardware discovery to identify disks, NICs, and other hardware
    - Prepare servers for provisioning as MOSK nodes

    Args:
        hostname: Server hostname (used as resource name). Must be a valid DNS label.
        bmc_address: BMC address. Accepts plain IP (e.g., '192.168.1.100') or
            protocol-prefixed (e.g., 'ipmi://192.168.1.100', 'redfish://server.local:443').
        bmc_credentials_secret: Name of the Secret containing BMC username/password.
        boot_mac_address: MAC address of the primary boot interface (aa:bb:cc:dd:ee:ff).
        boot_mode: Boot mode - UEFI (default) or legacy.
        bmc_type: BMC protocol type (ipmi, redfish, idrac, ilo).
        disable_tls_verify: Skip TLS verification for Redfish/iDRAC/iLO.
        hardware_profile: Optional reference to a BareMetalHostProfile.
        online: Desired power state (True = powered on).
        namespace: Kubernetes namespace for the resource.
        labels: Additional labels for the resource.
        annotations: Annotations for the resource.
        output_format: Output format (yaml, json, or kubectl command).

    Returns:
        GenerateBMHIOutput containing:
        - template: The generated BareMetalHostInventory CR
        - bmc_secret_template: Template for the BMC credentials Secret
        - instructions: Step-by-step instructions for applying the resources

    Example:
        >>> output = await generate_bmhi(
        ...     hostname="compute-01",
        ...     bmc_address="ipmi://192.168.1.100",
        ...     bmc_credentials_secret="compute-01-bmc-secret",
        ...     boot_mac_address="aa:bb:cc:dd:ee:ff",
        ... )
        >>> print(output.template.content)
    """
    generator = get_bmhi_generator()

    input_data = GenerateBMHIInput(
        hostname=hostname,
        bmc_address=bmc_address,
        boot_mode=boot_mode,
        bmc_credentials_secret=bmc_credentials_secret,
        boot_mac_address=boot_mac_address,
        bmc_type=bmc_type,
        disable_tls_verify=disable_tls_verify,
        hardware_profile=hardware_profile,
        online=online,
        namespace=namespace,
        labels=labels or {},
        annotations=annotations or {},
        output_format=output_format,
    )

    return generator.generate_bmhi(input_data)
