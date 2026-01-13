"""BareMetalHost-related CRD models.

This module provides Pydantic models for:
- BareMetalHostInventory (BMHi): Represents discovered hardware
- BareMetalHostProfile (BMHp): Defines hardware configuration profiles
"""

from __future__ import annotations

from enum import Enum
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

from mosk_mcp.adapters.crd.base import (
    KubernetesMetadata,
    KubernetesResource,
)


class BMCType(str, Enum):
    """Supported BMC types for bare metal management."""

    IPMI = "ipmi"
    REDFISH = "redfish"
    IDRAC = "idrac"
    ILO = "ilo"


class BMCSpec(BaseModel):
    """BMC (Baseboard Management Controller) configuration.

    Attributes:
        address: BMC address. Supports formats:
            - Plain IP: '192.168.1.100' or 'host:port'
            - IPMI: 'ipmi://host:port'
            - Redfish: 'redfish://host/redfish/v1/Systems/...'
        bmh_credentials_name: Name of the Secret containing BMC credentials.
        disable_certificate_verification: Skip TLS cert verification.
    """

    model_config = ConfigDict(populate_by_name=True)

    address: str = Field(
        ...,
        description="BMC address (plain IP like '192.168.1.100' or with protocol like 'ipmi://...')",
    )
    bmh_credentials_name: str = Field(
        ...,
        alias="bmhCredentialsName",
        description="Name of the Secret containing BMC username and password",
    )
    disable_certificate_verification: bool = Field(
        False,
        alias="disableCertificateVerification",
        description="Skip TLS certificate verification for Redfish connections",
    )

    def to_kubernetes(self) -> dict[str, Any]:
        """Convert to Kubernetes API format."""
        result: dict[str, Any] = {
            "address": self.address,
            "bmhCredentialsName": self.bmh_credentials_name,
        }
        if self.disable_certificate_verification:
            result["disableCertificateVerification"] = True
        return result


class DiskSelector(BaseModel):
    """Selector for matching disks in hardware profiles.

    Attributes:
        min_size_gigabytes: Minimum disk size in GB.
        max_size_gigabytes: Maximum disk size in GB.
        model: Disk model string match.
        vendor: Disk vendor string match.
        rotational: True for HDD, False for SSD.
        device_type: Type of device (ssd, hdd, nvme).
        by_path: Match by disk device path.
    """

    model_config = ConfigDict(populate_by_name=True)

    min_size_gigabytes: int | None = Field(None, alias="minSizeGigabytes")
    max_size_gigabytes: int | None = Field(None, alias="maxSizeGigabytes")
    model: str | None = None
    vendor: str | None = None
    rotational: bool | None = None
    device_type: Literal["ssd", "hdd", "nvme"] | None = Field(None, alias="deviceType")
    by_path: str | None = Field(None, alias="byPath")

    def to_kubernetes(self) -> dict[str, Any]:
        """Convert to Kubernetes API format."""
        result: dict[str, Any] = {}
        if self.min_size_gigabytes is not None:
            result["minSizeGigabytes"] = self.min_size_gigabytes
        if self.max_size_gigabytes is not None:
            result["maxSizeGigabytes"] = self.max_size_gigabytes
        if self.model is not None:
            result["model"] = self.model
        if self.vendor is not None:
            result["vendor"] = self.vendor
        if self.rotational is not None:
            result["rotational"] = self.rotational
        if self.device_type is not None:
            result["deviceType"] = self.device_type
        if self.by_path is not None:
            result["byPath"] = self.by_path
        return result


class NICBondingSpec(BaseModel):
    """Network interface bonding configuration.

    Attributes:
        mode: Bonding mode (e.g., '802.3ad', 'active-backup').
        interfaces: List of interface names to bond.
        mtu: MTU for the bond interface.
    """

    model_config = ConfigDict(populate_by_name=True)

    mode: str = Field(default="802.3ad", description="Bonding mode")
    interfaces: list[str] = Field(default_factory=list, description="Interfaces to include in bond")
    mtu: int | None = Field(None, description="MTU for the bond interface")

    def to_kubernetes(self) -> dict[str, Any]:
        """Convert to Kubernetes API format."""
        result: dict[str, Any] = {
            "mode": self.mode,
            "interfaces": self.interfaces,
        }
        if self.mtu is not None:
            result["mtu"] = self.mtu
        return result


class HardwareProfile(BaseModel):
    """Hardware profile specification for disk and device selection.

    Attributes:
        root_device_hints: Hints for selecting the root disk.
        grub_config: GRUB bootloader configuration.
        kernel_parameters: Additional kernel boot parameters.
    """

    model_config = ConfigDict(populate_by_name=True)

    root_device_hints: DiskSelector | None = Field(None, alias="rootDeviceHints")
    grub_config: dict[str, str] = Field(default_factory=dict, alias="grubConfig")
    kernel_parameters: list[str] = Field(default_factory=list, alias="kernelParameters")

    def to_kubernetes(self) -> dict[str, Any]:
        """Convert to Kubernetes API format."""
        result: dict[str, Any] = {}
        if self.root_device_hints is not None:
            result["rootDeviceHints"] = self.root_device_hints.to_kubernetes()
        if self.grub_config:
            result["grubConfig"] = self.grub_config
        if self.kernel_parameters:
            result["kernelParameters"] = self.kernel_parameters
        return result


class BareMetalHostInventorySpec(BaseModel):
    """Specification for BareMetalHostInventory resource.

    Attributes:
        boot_mac_address: MAC address for PXE boot.
        boot_mode: Boot mode (UEFI or legacy).
        bmc: BMC configuration for out-of-band management.
        hardware_profile: Reference to a BareMetalHostProfile.
        online: Desired power state.
        consumer_ref: Reference to consuming resource (e.g., Machine).
        external_id: External system identifier.
    """

    model_config = ConfigDict(populate_by_name=True)

    boot_mac_address: str = Field(
        ...,
        alias="bootMACAddress",
        description="MAC address of the primary boot interface",
    )
    boot_mode: Literal["UEFI", "legacy"] = Field(
        default="UEFI",
        alias="bootMode",
        description="Boot mode: UEFI (default) or legacy",
    )
    bmc: BMCSpec = Field(..., description="BMC configuration")
    hardware_profile: str | None = Field(
        None,
        alias="hardwareProfile",
        description="Reference to BareMetalHostProfile",
    )
    online: bool = Field(True, description="Desired power state")
    consumer_ref: dict[str, str] | None = Field(
        None,
        alias="consumerRef",
        description="Reference to resource consuming this host",
    )
    external_id: str | None = Field(
        None,
        alias="externalID",
        description="External system identifier",
    )

    def to_kubernetes(self) -> dict[str, Any]:
        """Convert to Kubernetes API format."""
        result: dict[str, Any] = {
            "bootMACAddress": self.boot_mac_address,
            "bootMode": self.boot_mode,
            "bmc": self.bmc.to_kubernetes(),
            "online": self.online,
        }
        if self.hardware_profile is not None:
            result["hardwareProfile"] = self.hardware_profile
        if self.consumer_ref is not None:
            result["consumerRef"] = self.consumer_ref
        if self.external_id is not None:
            result["externalID"] = self.external_id
        return result


class BareMetalHostInventoryStatus(BaseModel):
    """Status of BareMetalHostInventory resource.

    Attributes:
        provisioning_state: Current provisioning state.
        powered_on: Current power state.
        hardware_details: Discovered hardware information.
        error_message: Error message if in error state.
        error_type: Type of error.
    """

    model_config = ConfigDict(populate_by_name=True)

    provisioning_state: str | None = Field(
        None,
        alias="provisioningState",
        description="Current provisioning state",
    )
    powered_on: bool | None = Field(
        None,
        alias="poweredOn",
        description="Current power state",
    )
    hardware_details: dict[str, Any] | None = Field(
        None,
        alias="hardwareDetails",
        description="Discovered hardware information",
    )
    error_message: str | None = Field(
        None,
        alias="errorMessage",
        description="Error message if in error state",
    )
    error_type: str | None = Field(
        None,
        alias="errorType",
        description="Type of error",
    )


class BareMetalHostInventory(
    KubernetesResource[BareMetalHostInventorySpec, BareMetalHostInventoryStatus]
):
    """BareMetalHostInventory custom resource.

    Represents a discovered bare metal server with its BMC configuration
    and hardware details.

    Example:
        bmhi = BareMetalHostInventory(
            metadata=KubernetesMetadata(name="server-01", namespace="default"),
            spec=BareMetalHostInventorySpec(
                boot_mac_address="aa:bb:cc:dd:ee:ff",
                bmc=BMCSpec(
                    address="ipmi://192.168.1.100",
                    credentials_name="server-01-bmc-secret",
                ),
            ),
        )
    """

    API_VERSION: ClassVar[str] = "kaas.mirantis.com/v1alpha1"
    KIND: ClassVar[str] = "BareMetalHostInventory"
    PLURAL: ClassVar[str] = "baremetalhostinventories"
    GROUP: ClassVar[str] = "kaas.mirantis.com"

    api_version: str = Field(default="kaas.mirantis.com/v1alpha1", alias="apiVersion")
    kind: str = Field(default="BareMetalHostInventory")
    spec: BareMetalHostInventorySpec
    status: BareMetalHostInventoryStatus | None = None

    @classmethod
    def from_kubernetes(cls, data: dict[str, Any]) -> BareMetalHostInventory:
        """Create from Kubernetes API response.

        Args:
            data: Resource dictionary from Kubernetes API.

        Returns:
            BareMetalHostInventory instance.
        """
        spec_data = data.get("spec", {})
        bmc_data = spec_data.get("bmc", {})

        spec = BareMetalHostInventorySpec(
            boot_mac_address=spec_data.get("bootMACAddress", ""),
            boot_mode=spec_data.get("bootMode", "UEFI"),
            bmc=BMCSpec(
                address=bmc_data.get("address", ""),
                bmh_credentials_name=bmc_data.get("bmhCredentialsName", ""),
                disable_certificate_verification=bmc_data.get(
                    "disableCertificateVerification", False
                ),
            ),
            hardware_profile=spec_data.get("hardwareProfile"),
            online=spec_data.get("online", True),
            consumer_ref=spec_data.get("consumerRef"),
            external_id=spec_data.get("externalID"),
        )

        status = None
        if "status" in data:
            status_data = data["status"]
            status = BareMetalHostInventoryStatus(
                provisioning_state=status_data.get("provisioningState"),
                powered_on=status_data.get("poweredOn"),
                hardware_details=status_data.get("hardwareDetails"),
                error_message=status_data.get("errorMessage"),
                error_type=status_data.get("errorType"),
            )

        return cls(
            metadata=KubernetesMetadata.from_kubernetes(data["metadata"]),
            spec=spec,
            status=status,
        )


class BareMetalHostProfileSpec(BaseModel):
    """Specification for BareMetalHostProfile resource.

    Attributes:
        hardware_profile: Hardware profile configuration.
        grub_config: GRUB bootloader configuration.
        kernel_parameters: Additional kernel boot parameters.
        pre_deploy_script: Script to run before deployment.
        post_deploy_script: Script to run after deployment.
        devices: Device configuration (disks, NICs).
    """

    model_config = ConfigDict(populate_by_name=True)

    hardware_profile: HardwareProfile | None = Field(
        None,
        alias="hardwareProfile",
        description="Hardware profile configuration",
    )
    grub_config: dict[str, str] = Field(
        default_factory=dict,
        alias="grubConfig",
        description="GRUB bootloader configuration",
    )
    kernel_parameters: list[str] = Field(
        default_factory=list,
        alias="kernelParameters",
        description="Additional kernel boot parameters",
    )
    pre_deploy_script: str | None = Field(
        None,
        alias="preDeployScript",
        description="Script to run before deployment",
    )
    post_deploy_script: str | None = Field(
        None,
        alias="postDeployScript",
        description="Script to run after deployment",
    )
    devices: dict[str, Any] = Field(
        default_factory=dict,
        description="Device configuration (disks, NICs)",
    )

    def to_kubernetes(self) -> dict[str, Any]:
        """Convert to Kubernetes API format."""
        result: dict[str, Any] = {}
        if self.hardware_profile is not None:
            result["hardwareProfile"] = self.hardware_profile.to_kubernetes()
        if self.grub_config:
            result["grubConfig"] = self.grub_config
        if self.kernel_parameters:
            result["kernelParameters"] = self.kernel_parameters
        if self.pre_deploy_script is not None:
            result["preDeployScript"] = self.pre_deploy_script
        if self.post_deploy_script is not None:
            result["postDeployScript"] = self.post_deploy_script
        if self.devices:
            result["devices"] = self.devices
        return result


class BareMetalHostProfile(KubernetesResource[BareMetalHostProfileSpec, None]):
    """BareMetalHostProfile custom resource.

    Defines a reusable hardware configuration profile that can be applied
    to multiple BareMetalHostInventory resources.

    Example:
        profile = BareMetalHostProfile(
            metadata=KubernetesMetadata(name="compute-profile", namespace="default"),
            spec=BareMetalHostProfileSpec(
                kernel_parameters=["hugepages=2048", "isolcpus=1-3"],
            ),
        )
    """

    API_VERSION: ClassVar[str] = "metal3.io/v1alpha1"
    KIND: ClassVar[str] = "BareMetalHostProfile"
    PLURAL: ClassVar[str] = "baremetalhostprofiles"
    GROUP: ClassVar[str] = "metal3.io"

    api_version: str = Field(default="metal3.io/v1alpha1", alias="apiVersion")
    kind: str = Field(default="BareMetalHostProfile")
    spec: BareMetalHostProfileSpec
    status: None = None

    @classmethod
    def from_kubernetes(cls, data: dict[str, Any]) -> BareMetalHostProfile:
        """Create from Kubernetes API response.

        Args:
            data: Resource dictionary from Kubernetes API.

        Returns:
            BareMetalHostProfile instance.
        """
        spec_data = data.get("spec", {})

        hardware_profile = None
        if "hardwareProfile" in spec_data:
            hp_data = spec_data["hardwareProfile"]
            root_hints = None
            if "rootDeviceHints" in hp_data:
                rdh = hp_data["rootDeviceHints"]
                root_hints = DiskSelector(
                    min_size_gigabytes=rdh.get("minSizeGigabytes"),
                    max_size_gigabytes=rdh.get("maxSizeGigabytes"),
                    model=rdh.get("model"),
                    vendor=rdh.get("vendor"),
                    rotational=rdh.get("rotational"),
                    device_type=rdh.get("deviceType"),
                    by_path=rdh.get("byPath"),
                )
            hardware_profile = HardwareProfile(
                root_device_hints=root_hints,
                grub_config=hp_data.get("grubConfig", {}),
                kernel_parameters=hp_data.get("kernelParameters", []),
            )

        spec = BareMetalHostProfileSpec(
            hardware_profile=hardware_profile,
            grub_config=spec_data.get("grubConfig", {}),
            kernel_parameters=spec_data.get("kernelParameters", []),
            pre_deploy_script=spec_data.get("preDeployScript"),
            post_deploy_script=spec_data.get("postDeployScript"),
            devices=spec_data.get("devices", {}),
        )

        return cls(
            metadata=KubernetesMetadata.from_kubernetes(data["metadata"]),
            spec=spec,
        )
