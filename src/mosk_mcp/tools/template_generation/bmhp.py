"""BareMetalHostProfile template generation tool.

This module provides the generate_bmhp tool for generating BareMetalHostProfile
custom resources for hardware configuration profiles in MOSK clusters.
"""

from __future__ import annotations

from typing import Any, ClassVar, Literal

from pydantic import BaseModel, Field, field_validator

from mosk_mcp.adapters.crd.baremetal import (
    BareMetalHostProfile,
    BareMetalHostProfileSpec,
    DiskSelector,
    HardwareProfile,
)
from mosk_mcp.adapters.crd.base import KubernetesMetadata
from mosk_mcp.observability.logging import get_logger
from mosk_mcp.tools.template_generation.base import (
    BaseTemplateGenerator,
    OutputFormat,
    TemplateOutput,
)


logger = get_logger(__name__)


class RaidConfig(BaseModel):
    """RAID configuration for storage devices.

    Attributes:
        level: RAID level (0, 1, 5, 6, 10).
        devices: List of device paths or selectors.
        spare_devices: Number of spare devices.
        chunk_size: Chunk size in KB for RAID5/6.
    """

    level: Literal[0, 1, 5, 6, 10] = Field(..., description="RAID level")
    devices: list[str] = Field(
        ...,
        description="Device paths or selectors (e.g., '/dev/sda', 'ssd:*')",
        min_length=1,
    )
    spare_devices: int = Field(
        default=0,
        description="Number of spare devices",
        ge=0,
    )
    chunk_size: int | None = Field(
        default=None,
        description="Chunk size in KB (for RAID5/6)",
        ge=64,
    )


class PartitionConfig(BaseModel):
    """Partition configuration.

    Attributes:
        name: Partition name/label.
        size: Partition size (e.g., '100G', '50%', 'remaining').
        filesystem: Filesystem type.
        mount_point: Mount point path.
        mount_options: Mount options.
    """

    name: str = Field(
        ...,
        description="Partition name/label",
        min_length=1,
        max_length=36,
    )
    size: str = Field(
        ...,
        description="Partition size: absolute (e.g., '100G'), percentage (e.g., '50%'), or 'remaining'",
    )
    filesystem: Literal["ext4", "xfs", "vfat", "swap", "none"] = Field(
        default="ext4",
        description="Filesystem type",
    )
    mount_point: str | None = Field(
        default=None,
        description="Mount point path (e.g., '/var/lib/docker')",
    )
    mount_options: list[str] = Field(
        default_factory=list,
        description="Mount options (e.g., ['noatime', 'nodiratime'])",
    )


class DiskConfig(BaseModel):
    """Disk configuration for partitioning.

    Attributes:
        selector: Disk selector (device type, size, etc.).
        wipe: Wipe disk before use.
        partitions: List of partition configurations.
    """

    selector: dict[str, Any] = Field(
        ...,
        description="Disk selector criteria (e.g., {'deviceType': 'ssd', 'minSizeGigabytes': 100})",
    )
    wipe: bool = Field(
        default=True,
        description="Wipe disk before partitioning",
    )
    partitions: list[PartitionConfig] = Field(
        default_factory=list,
        description="Partition configurations",
    )


class GenerateBMHPInput(BaseModel):
    """Input parameters for generating a BareMetalHostProfile CR.

    Attributes:
        profile_name: Profile name (used as resource name).
        role: Node role this profile is intended for.
        root_device_hints: Hints for selecting the root disk.
        kernel_parameters: Additional kernel boot parameters.
        grub_config: GRUB bootloader configuration.
        raid_config: Optional RAID configuration.
        disk_configs: Disk partitioning configurations.
        pre_deploy_script: Script to run before deployment.
        post_deploy_script: Script to run after deployment.
        namespace: Kubernetes namespace for the resource.
        labels: Additional labels to apply.
        annotations: Annotations to apply.
        output_format: Output format (yaml, json, or kubectl).
    """

    profile_name: str = Field(
        ...,
        description="Profile name, used as the resource name",
        min_length=1,
        max_length=63,
        pattern=r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$",
    )
    role: Literal["compute", "storage", "control", "gateway", "generic"] = Field(
        default="generic",
        description="Intended node role for this profile",
    )
    root_device_hints: dict[str, Any] | None = Field(
        default=None,
        description="Hints for selecting root disk (e.g., {'deviceType': 'ssd', 'minSizeGigabytes': 200})",
    )
    kernel_parameters: list[str] = Field(
        default_factory=list,
        description="Additional kernel boot parameters (e.g., ['hugepages=2048', 'isolcpus=1-3'])",
    )
    grub_config: dict[str, str] = Field(
        default_factory=dict,
        description="GRUB bootloader configuration",
    )
    raid_config: RaidConfig | None = Field(
        default=None,
        description="RAID configuration for storage",
    )
    disk_configs: list[DiskConfig] = Field(
        default_factory=list,
        description="Disk partitioning configurations",
    )
    pre_deploy_script: str | None = Field(
        default=None,
        description="Shell script to run before deployment",
    )
    post_deploy_script: str | None = Field(
        default=None,
        description="Shell script to run after deployment",
    )
    namespace: str = Field(
        default="default",
        description="Kubernetes namespace for the resource",
    )
    cluster_name: str = Field(
        ...,
        description="Cluster name for cluster.sigs.k8s.io/cluster-name label",
    )
    region: str = Field(
        default="region-one",
        description="Region for kaas.mirantis.com/region label",
    )
    labels: dict[str, str] = Field(
        default_factory=dict,
        description="Additional labels to apply",
    )
    annotations: dict[str, str] = Field(
        default_factory=dict,
        description="Annotations to apply",
    )
    output_format: OutputFormat = Field(
        default=OutputFormat.YAML,
        description="Output format: yaml, json, or kubectl",
    )

    @field_validator("kernel_parameters")
    @classmethod
    def validate_kernel_params(cls, v: list[str]) -> list[str]:
        """Validate kernel parameters format."""
        for param in v:
            if not param or param.startswith("-"):
                raise ValueError(f"Invalid kernel parameter: {param}")
        return v


class GenerateBMHPOutput(BaseModel):
    """Output from generate_bmhp tool.

    Attributes:
        template: Generated template output.
        role_recommendations: Recommendations based on the selected role.
        warnings: Any warnings generated during creation.
    """

    template: TemplateOutput = Field(..., description="Generated BMHp template")
    role_recommendations: str = Field(..., description="Recommendations for the selected role")
    warnings: list[str] = Field(default_factory=list, description="Warnings generated")


class BMHPGenerator(BaseTemplateGenerator[BareMetalHostProfile]):
    """Generator for BareMetalHostProfile custom resources.

    This generator creates BMHp CRs that define hardware configuration
    profiles for MOSK nodes, including disk layouts, kernel parameters,
    and deployment scripts.

    Example:
        generator = BMHPGenerator()
        input_params = GenerateBMHPInput(
            profile_name="compute-standard",
            role="compute",
            kernel_parameters=["hugepages=2048"],
        )
        output = generator.generate_bmhp(input_params)
    """

    # Role-specific default kernel parameters
    ROLE_KERNEL_PARAMS: ClassVar[dict[str, list[str]]] = {
        "compute": [
            "default_hugepagesz=1G",
            "hugepagesz=1G",
            "hugepages=0",
            "transparent_hugepage=never",
            "intel_iommu=on",
            "iommu=pt",
        ],
        "storage": [
            "elevator=noop",
            "transparent_hugepage=never",
        ],
        "control": [
            "transparent_hugepage=never",
        ],
        "gateway": [
            "transparent_hugepage=never",
            "intel_iommu=on",
            "iommu=pt",
        ],
        "generic": [],
    }

    # Role-specific root device recommendations
    ROLE_ROOT_HINTS: ClassVar[dict[str, dict[str, Any]]] = {
        "compute": {"deviceType": "ssd", "minSizeGigabytes": 200},
        "storage": {"deviceType": "ssd", "minSizeGigabytes": 100},
        "control": {"deviceType": "ssd", "minSizeGigabytes": 300},
        "gateway": {"deviceType": "ssd", "minSizeGigabytes": 200},
        "generic": {"minSizeGigabytes": 100},
    }

    def generate(self, **kwargs: Any) -> BareMetalHostProfile:
        """Generate a BareMetalHostProfile resource.

        Args:
            **kwargs: Parameters from GenerateBMHPInput.

        Returns:
            BareMetalHostProfile resource.
        """
        input_data = GenerateBMHPInput(**kwargs)
        return self._create_bmhp(input_data)

    def _create_bmhp(self, input_data: GenerateBMHPInput) -> BareMetalHostProfile:
        """Create a BareMetalHostProfile resource from input.

        Args:
            input_data: Validated input parameters.

        Returns:
            BareMetalHostProfile resource.
        """
        # Validate profile name
        self.validate_dns_label(input_data.profile_name, "profile_name")

        # Build labels based on real MOSK BMHp resources
        labels = self.build_standard_labels(
            cluster_name=input_data.cluster_name,
            region=input_data.region,
            additional=input_data.labels,
        )

        # Create metadata
        metadata = KubernetesMetadata(
            name=input_data.profile_name,
            namespace=input_data.namespace,
            labels=labels,
            annotations=input_data.annotations,
        )

        # Build hardware profile with root device hints
        hardware_profile = None
        root_hints = input_data.root_device_hints
        if root_hints:
            hardware_profile = HardwareProfile(
                root_device_hints=DiskSelector(
                    min_size_gigabytes=root_hints.get("minSizeGigabytes"),
                    max_size_gigabytes=root_hints.get("maxSizeGigabytes"),
                    device_type=root_hints.get("deviceType"),
                    model=root_hints.get("model"),
                    vendor=root_hints.get("vendor"),
                    rotational=root_hints.get("rotational"),
                    by_path=root_hints.get("byPath"),
                ),
            )

        # Build devices configuration from disk_configs
        devices: dict[str, Any] = {}
        if input_data.disk_configs:
            devices["disks"] = []
            for disk_cfg in input_data.disk_configs:
                disk_entry: dict[str, Any] = {
                    "selector": disk_cfg.selector,
                    "wipe": disk_cfg.wipe,
                }
                if disk_cfg.partitions:
                    disk_entry["partitions"] = [
                        {
                            "name": p.name,
                            "size": p.size,
                            "filesystem": p.filesystem,
                            **({"mountPoint": p.mount_point} if p.mount_point else {}),
                            **({"mountOptions": p.mount_options} if p.mount_options else {}),
                        }
                        for p in disk_cfg.partitions
                    ]
                devices["disks"].append(disk_entry)

        # Add RAID configuration if provided
        if input_data.raid_config:
            raid = input_data.raid_config
            devices["raid"] = {
                "level": raid.level,
                "devices": raid.devices,
                "spareDevices": raid.spare_devices,
            }
            if raid.chunk_size:
                devices["raid"]["chunkSize"] = raid.chunk_size

        # Create spec
        spec = BareMetalHostProfileSpec(
            hardware_profile=hardware_profile,
            kernel_parameters=input_data.kernel_parameters,
            grub_config=input_data.grub_config,
            pre_deploy_script=input_data.pre_deploy_script,
            post_deploy_script=input_data.post_deploy_script,
            devices=devices,
        )

        return BareMetalHostProfile(
            metadata=metadata,
            spec=spec,
        )

    def get_role_recommendations(self, role: str) -> str:
        """Get recommendations for a specific role.

        Args:
            role: Node role.

        Returns:
            Recommendations string.
        """
        recommendations: dict[str, str] = {
            "compute": """## Compute Node Profile Recommendations

- **CPU Isolation**: Consider using 'isolcpus' to reserve CPUs for VMs
- **Huge Pages**: Configure huge pages for better VM performance
- **IOMMU**: Enable for SR-IOV and device passthrough
- **Root Disk**: Use SSD with at least 200GB for OS and ephemeral storage
- **Partitions**: Consider separate partitions for:
  - /var/lib/nova (VM ephemeral disks)
  - /var/lib/libvirt (libvirt data)
""",
            "storage": """## Storage Node Profile Recommendations

- **I/O Scheduler**: Use 'noop' or 'none' for SSDs
- **Root Disk**: Keep root disk separate from Ceph OSDs
- **OSD Disks**: Leave OSD disks unpartitioned for Ceph
- **Journal/WAL**: Consider separate SSD/NVMe for Ceph journals
- **Network**: Ensure dedicated storage network with jumbo frames
""",
            "control": """## Control Plane Node Profile Recommendations

- **Root Disk**: Use SSD with at least 300GB for databases
- **Partitions**: Consider separate partitions for:
  - /var/lib/mysql (database storage)
  - /var/lib/rabbitmq (message queue data)
  - /var/log (log storage)
- **Memory**: Ensure sufficient RAM for control services
""",
            "gateway": """## Gateway Node Profile Recommendations

- **IOMMU**: Enable for network device passthrough
- **Root Disk**: Use SSD with at least 200GB
- **Network**: Ensure high-bandwidth NICs for traffic
- **SR-IOV**: Consider enabling for high-performance networking
""",
            "generic": """## Generic Profile Recommendations

This is a generic profile. Consider specifying a role for
role-specific recommendations and optimizations.
""",
        }

        return recommendations.get(role, recommendations["generic"])

    def generate_bmhp(self, input_data: GenerateBMHPInput) -> GenerateBMHPOutput:
        """Generate complete BMHp output with recommendations.

        Args:
            input_data: Input parameters for generation.

        Returns:
            Complete output with BMHp template and recommendations.
        """
        logger.info(
            "generating_bmhp",
            profile_name=input_data.profile_name,
            role=input_data.role,
        )

        warnings: list[str] = []

        # Check if role-specific kernel params should be added
        role_params = self.ROLE_KERNEL_PARAMS.get(input_data.role, [])
        if role_params and not input_data.kernel_parameters:
            warnings.append(
                f"Consider adding role-specific kernel parameters for '{input_data.role}' role: "
                f"{', '.join(role_params[:3])}..."
            )

        # Check if root device hints are provided
        if not input_data.root_device_hints:
            default_hints = self.ROLE_ROOT_HINTS.get(input_data.role, {})
            warnings.append(
                f"No root device hints provided. Recommended for '{input_data.role}': "
                f"{default_hints}"
            )

        # Generate the BMHp resource
        bmhp = self._create_bmhp(input_data)

        # Generate template output
        template = self.generate_template(bmhp, input_data.output_format)

        # Add warnings to template
        template.warnings = warnings

        # Get role recommendations
        recommendations = self.get_role_recommendations(input_data.role)

        logger.info(
            "generated_bmhp",
            profile_name=input_data.profile_name,
            namespace=input_data.namespace,
            warnings_count=len(warnings),
        )

        return GenerateBMHPOutput(
            template=template,
            role_recommendations=recommendations,
            warnings=warnings,
        )


# Singleton instance
_generator: BMHPGenerator | None = None


def get_bmhp_generator() -> BMHPGenerator:
    """Get the singleton BMHp generator instance.

    Returns:
        BMHPGenerator instance.
    """
    global _generator
    if _generator is None:
        _generator = BMHPGenerator()
    return _generator


async def generate_bmhp(
    profile_name: str,
    cluster_name: str,
    role: Literal["compute", "storage", "control", "gateway", "generic"] = "generic",
    region: str = "region-one",
    root_device_hints: dict[str, Any] | None = None,
    kernel_parameters: list[str] | None = None,
    grub_config: dict[str, str] | None = None,
    raid_config: dict[str, Any] | None = None,
    disk_configs: list[dict[str, Any]] | None = None,
    pre_deploy_script: str | None = None,
    post_deploy_script: str | None = None,
    namespace: str = "default",
    labels: dict[str, str] | None = None,
    annotations: dict[str, str] | None = None,
    output_format: OutputFormat = OutputFormat.YAML,
) -> GenerateBMHPOutput:
    """Generate a BareMetalHostProfile CR for hardware configuration.

    This tool generates a BareMetalHostProfile custom resource that defines
    hardware configuration profiles for MOSK nodes. Profiles can specify
    disk partitioning, RAID configuration, kernel parameters, and deployment
    scripts.

    BareMetalHostProfile (BMHp) resources are used to:
    - Define root disk selection criteria
    - Configure kernel boot parameters
    - Set up disk partitioning schemes
    - Configure RAID arrays
    - Run pre/post deployment scripts
    - Standardize hardware configuration across nodes

    Args:
        profile_name: Profile name (used as resource name). Must be a valid DNS label.
        role: Intended node role (compute, storage, control, gateway, generic).
            This affects recommendations but doesn't change the generated profile.
        root_device_hints: Hints for selecting the root disk. Example:
            {'deviceType': 'ssd', 'minSizeGigabytes': 200}
        kernel_parameters: Additional kernel boot parameters. Example:
            ['hugepages=2048', 'isolcpus=1-3', 'intel_iommu=on']
        grub_config: GRUB bootloader configuration key-value pairs.
        raid_config: RAID configuration dictionary with 'level', 'devices', etc.
        disk_configs: List of disk configurations with selectors and partitions.
        pre_deploy_script: Shell script to run before deployment.
        post_deploy_script: Shell script to run after deployment.
        namespace: Kubernetes namespace for the resource.
        labels: Additional labels for the resource.
        annotations: Annotations for the resource.
        output_format: Output format (yaml, json, or kubectl command).

    Returns:
        GenerateBMHPOutput containing:
        - template: The generated BareMetalHostProfile CR
        - role_recommendations: Role-specific recommendations
        - warnings: Any warnings about the configuration

    Example:
        >>> output = await generate_bmhp(
        ...     profile_name="compute-standard",
        ...     role="compute",
        ...     root_device_hints={"deviceType": "ssd", "minSizeGigabytes": 200},
        ...     kernel_parameters=["hugepages=2048", "intel_iommu=on"],
        ... )
        >>> print(output.template.content)
    """
    generator = get_bmhp_generator()

    # Convert raid_config dict to RaidConfig if provided
    raid: RaidConfig | None = None
    if raid_config:
        raid = RaidConfig(**raid_config)

    # Convert disk_configs dicts to DiskConfig objects
    disks: list[DiskConfig] = []
    if disk_configs:
        for dc in disk_configs:
            partitions = [PartitionConfig(**p) for p in dc.get("partitions", [])]
            disks.append(
                DiskConfig(
                    selector=dc["selector"],
                    wipe=dc.get("wipe", True),
                    partitions=partitions,
                )
            )

    input_data = GenerateBMHPInput(
        profile_name=profile_name,
        role=role,
        root_device_hints=root_device_hints,
        kernel_parameters=kernel_parameters or [],
        grub_config=grub_config or {},
        raid_config=raid,
        disk_configs=disks,
        pre_deploy_script=pre_deploy_script,
        post_deploy_script=post_deploy_script,
        namespace=namespace,
        cluster_name=cluster_name,
        region=region,
        labels=labels or {},
        annotations=annotations or {},
        output_format=output_format,
    )

    return generator.generate_bmhp(input_data)
