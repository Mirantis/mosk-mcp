"""Tests for BareMetalHostInventory template generation.

Tests cover:
- Input validation (DNS labels, MAC addresses)
- Template generation in different formats
- BMC secret template generation
- Protocol prefix stripping from BMC addresses
- Label and annotation building
"""

import pytest

from mosk_mcp.tools.template_generation.base import OutputFormat
from mosk_mcp.tools.template_generation.bmhi import (
    BMHIGenerator,
    GenerateBMHIInput,
    generate_bmhi,
    get_bmhi_generator,
)


class TestGenerateBMHIInput:
    """Tests for GenerateBMHIInput validation."""

    def test_valid_input_minimal(self) -> None:
        """Test valid minimal input."""
        input_data = GenerateBMHIInput(
            hostname="server-01",
            bmc_address="192.168.1.100",
            bmc_credentials_secret="server-01-bmc",
            boot_mac_address="aa:bb:cc:dd:ee:ff",
        )
        assert input_data.hostname == "server-01"
        assert input_data.bmc_type == "ipmi"  # default
        assert input_data.boot_mode == "UEFI"  # default
        assert input_data.online is True  # default

    def test_valid_input_full(self) -> None:
        """Test valid input with all options."""
        input_data = GenerateBMHIInput(
            hostname="compute-05",
            bmc_address="redfish://192.168.1.100:443",
            bmc_credentials_secret="compute-05-bmc",
            boot_mac_address="AA:BB:CC:DD:EE:FF",
            boot_mode="legacy",
            bmc_type="redfish",
            disable_tls_verify=True,
            hardware_profile="compute-standard",
            online=False,
            namespace="baremetal",
            labels={"environment": "production"},
            annotations={"description": "Main compute node"},
            output_format=OutputFormat.JSON,
        )
        assert input_data.hostname == "compute-05"
        assert input_data.bmc_type == "redfish"
        assert input_data.boot_mode == "legacy"
        assert input_data.disable_tls_verify is True

    def test_invalid_hostname_uppercase(self) -> None:
        """Test uppercase hostname is rejected."""
        with pytest.raises(ValueError, match="hostname"):
            GenerateBMHIInput(
                hostname="Server-01",  # uppercase
                bmc_address="192.168.1.100",
                bmc_credentials_secret="server-01-bmc",
                boot_mac_address="aa:bb:cc:dd:ee:ff",
            )

    def test_invalid_hostname_too_long(self) -> None:
        """Test hostname exceeding 63 chars is rejected."""
        with pytest.raises(ValueError):
            GenerateBMHIInput(
                hostname="a" * 64,
                bmc_address="192.168.1.100",
                bmc_credentials_secret="server-01-bmc",
                boot_mac_address="aa:bb:cc:dd:ee:ff",
            )

    def test_invalid_hostname_starts_with_dash(self) -> None:
        """Test hostname starting with dash is rejected."""
        with pytest.raises(ValueError):
            GenerateBMHIInput(
                hostname="-server-01",
                bmc_address="192.168.1.100",
                bmc_credentials_secret="server-01-bmc",
                boot_mac_address="aa:bb:cc:dd:ee:ff",
            )

    def test_invalid_mac_address_format(self) -> None:
        """Test invalid MAC address format is rejected."""
        with pytest.raises(ValueError, match="boot_mac_address"):
            GenerateBMHIInput(
                hostname="server-01",
                bmc_address="192.168.1.100",
                bmc_credentials_secret="server-01-bmc",
                boot_mac_address="invalid-mac",
            )

    def test_invalid_mac_address_dashes(self) -> None:
        """Test MAC address with dashes instead of colons is rejected."""
        with pytest.raises(ValueError):
            GenerateBMHIInput(
                hostname="server-01",
                bmc_address="192.168.1.100",
                bmc_credentials_secret="server-01-bmc",
                boot_mac_address="aa-bb-cc-dd-ee-ff",
            )


class TestBMHIGenerator:
    """Tests for BMHIGenerator."""

    @pytest.fixture
    def generator(self) -> BMHIGenerator:
        """Create a BMHIGenerator instance."""
        return BMHIGenerator()

    def test_generate_basic_bmhi(self, generator: BMHIGenerator) -> None:
        """Test basic BMHi generation."""
        input_data = GenerateBMHIInput(
            hostname="server-01",
            bmc_address="192.168.1.100",
            bmc_credentials_secret="server-01-bmc",
            boot_mac_address="aa:bb:cc:dd:ee:ff",
        )
        output = generator.generate_bmhi(input_data)

        assert output.template.resource_kind == "BareMetalHostInventory"
        assert output.template.resource_name == "server-01"
        assert output.template.resource_namespace == "default"
        assert "server-01" in output.template.content
        assert "aa:bb:cc:dd:ee:ff" in output.template.content

    def test_generate_strips_ipmi_prefix(self, generator: BMHIGenerator) -> None:
        """Test that ipmi:// prefix is stripped from BMC address."""
        input_data = GenerateBMHIInput(
            hostname="server-01",
            bmc_address="ipmi://192.168.1.100",
            bmc_credentials_secret="server-01-bmc",
            boot_mac_address="aa:bb:cc:dd:ee:ff",
        )
        output = generator.generate_bmhi(input_data)

        # The content should have the raw IP, not the prefixed version
        assert "192.168.1.100" in output.template.content
        # Should NOT contain the ipmi:// prefix in the generated template
        assert "ipmi://192.168.1.100" not in output.template.content

    def test_generate_strips_redfish_prefix(self, generator: BMHIGenerator) -> None:
        """Test that redfish:// prefix is stripped from BMC address."""
        input_data = GenerateBMHIInput(
            hostname="server-01",
            bmc_address="redfish://bmc.example.com:443",
            bmc_credentials_secret="server-01-bmc",
            boot_mac_address="aa:bb:cc:dd:ee:ff",
            bmc_type="redfish",
        )
        output = generator.generate_bmhi(input_data)

        assert "bmc.example.com:443" in output.template.content

    def test_generate_strips_https_prefix(self, generator: BMHIGenerator) -> None:
        """Test that https:// prefix is stripped from BMC address."""
        input_data = GenerateBMHIInput(
            hostname="server-01",
            bmc_address="https://192.168.1.100",
            bmc_credentials_secret="server-01-bmc",
            boot_mac_address="aa:bb:cc:dd:ee:ff",
        )
        output = generator.generate_bmhi(input_data)

        # Should contain the IP without prefix
        content_lines = output.template.content.split("\n")
        address_lines = [line for line in content_lines if "address:" in line]
        assert any("192.168.1.100" in line for line in address_lines)

    def test_generate_includes_required_labels(self, generator: BMHIGenerator) -> None:
        """Test that required MOSK labels are included."""
        input_data = GenerateBMHIInput(
            hostname="server-01",
            bmc_address="192.168.1.100",
            bmc_credentials_secret="server-01-bmc",
            boot_mac_address="aa:bb:cc:dd:ee:ff",
        )
        output = generator.generate_bmhi(input_data)

        assert "kaas.mirantis.com/baremetalhost-id" in output.template.content
        assert "kaas.mirantis.com/provider" in output.template.content

    def test_generate_includes_storage_sort_annotation(self, generator: BMHIGenerator) -> None:
        """Test that storage sort annotation is included."""
        input_data = GenerateBMHIInput(
            hostname="server-01",
            bmc_address="192.168.1.100",
            bmc_credentials_secret="server-01-bmc",
            boot_mac_address="aa:bb:cc:dd:ee:ff",
        )
        output = generator.generate_bmhi(input_data)

        assert "inspect.metal3.io/hardwaredetails-storage-sort-term" in output.template.content

    def test_generate_json_format(self, generator: BMHIGenerator) -> None:
        """Test JSON output format."""
        input_data = GenerateBMHIInput(
            hostname="server-01",
            bmc_address="192.168.1.100",
            bmc_credentials_secret="server-01-bmc",
            boot_mac_address="aa:bb:cc:dd:ee:ff",
            output_format=OutputFormat.JSON,
        )
        output = generator.generate_bmhi(input_data)

        assert output.template.format == OutputFormat.JSON
        # JSON should start with {
        assert output.template.content.strip().startswith("{")

    def test_generate_kubectl_format(self, generator: BMHIGenerator) -> None:
        """Test kubectl command output format."""
        input_data = GenerateBMHIInput(
            hostname="server-01",
            bmc_address="192.168.1.100",
            bmc_credentials_secret="server-01-bmc",
            boot_mac_address="aa:bb:cc:dd:ee:ff",
            output_format=OutputFormat.KUBECTL,
        )
        output = generator.generate_bmhi(input_data)

        assert output.template.format == OutputFormat.KUBECTL
        assert output.template.content.startswith("kubectl apply")

    def test_generate_bmc_secret_template(self, generator: BMHIGenerator) -> None:
        """Test BMC secret template generation."""
        input_data = GenerateBMHIInput(
            hostname="server-01",
            bmc_address="192.168.1.100",
            bmc_credentials_secret="server-01-bmc",
            boot_mac_address="aa:bb:cc:dd:ee:ff",
        )
        output = generator.generate_bmhi(input_data)

        # Check secret template
        assert "kind: Secret" in output.bmc_secret_template
        assert "server-01-bmc" in output.bmc_secret_template
        assert "<BMC_USERNAME>" in output.bmc_secret_template
        assert "<BMC_PASSWORD>" in output.bmc_secret_template

    def test_generate_instructions(self, generator: BMHIGenerator) -> None:
        """Test that instructions are included."""
        input_data = GenerateBMHIInput(
            hostname="server-01",
            bmc_address="192.168.1.100",
            bmc_credentials_secret="server-01-bmc",
            boot_mac_address="aa:bb:cc:dd:ee:ff",
        )
        output = generator.generate_bmhi(input_data)

        # Check instructions contain key steps
        assert "Create BMC Credentials Secret" in output.instructions
        assert "Apply BareMetalHostInventory" in output.instructions
        assert "Verify Hardware Discovery" in output.instructions

    def test_generate_with_hardware_profile(self, generator: BMHIGenerator) -> None:
        """Test generation with hardware profile reference."""
        input_data = GenerateBMHIInput(
            hostname="server-01",
            bmc_address="192.168.1.100",
            bmc_credentials_secret="server-01-bmc",
            boot_mac_address="aa:bb:cc:dd:ee:ff",
            hardware_profile="compute-standard",
        )
        output = generator.generate_bmhi(input_data)

        assert "compute-standard" in output.template.content

    def test_generate_with_custom_labels(self, generator: BMHIGenerator) -> None:
        """Test generation with custom labels."""
        input_data = GenerateBMHIInput(
            hostname="server-01",
            bmc_address="192.168.1.100",
            bmc_credentials_secret="server-01-bmc",
            boot_mac_address="aa:bb:cc:dd:ee:ff",
            labels={"environment": "production", "rack": "rack-1"},
        )
        output = generator.generate_bmhi(input_data)

        assert "environment: production" in output.template.content
        assert "rack: rack-1" in output.template.content

    def test_mac_address_normalized_to_lowercase(self, generator: BMHIGenerator) -> None:
        """Test that MAC address is normalized to lowercase."""
        input_data = GenerateBMHIInput(
            hostname="server-01",
            bmc_address="192.168.1.100",
            bmc_credentials_secret="server-01-bmc",
            boot_mac_address="AA:BB:CC:DD:EE:FF",
        )
        output = generator.generate_bmhi(input_data)

        # Should be lowercase in output
        assert "aa:bb:cc:dd:ee:ff" in output.template.content


class TestGetBMHIGenerator:
    """Tests for singleton generator."""

    def test_singleton_returns_same_instance(self) -> None:
        """Test that get_bmhi_generator returns the same instance."""
        gen1 = get_bmhi_generator()
        gen2 = get_bmhi_generator()
        assert gen1 is gen2


class TestGenerateBMHIAsync:
    """Tests for async generate_bmhi function."""

    @pytest.mark.asyncio
    async def test_generate_bmhi_async(self) -> None:
        """Test async generate_bmhi function."""
        output = await generate_bmhi(
            hostname="compute-01",
            bmc_address="192.168.1.100",
            bmc_credentials_secret="compute-01-bmc",
            boot_mac_address="aa:bb:cc:dd:ee:ff",
        )

        assert output.template.resource_kind == "BareMetalHostInventory"
        assert output.template.resource_name == "compute-01"

    @pytest.mark.asyncio
    async def test_generate_bmhi_with_options(self) -> None:
        """Test async generate_bmhi with all options."""
        output = await generate_bmhi(
            hostname="storage-01",
            bmc_address="ipmi://10.0.0.50",
            bmc_credentials_secret="storage-01-bmc",
            boot_mac_address="11:22:33:44:55:66",
            boot_mode="legacy",
            bmc_type="ipmi",
            disable_tls_verify=False,
            hardware_profile="storage-profile",
            online=True,
            namespace="metal",
            labels={"role": "storage"},
            output_format=OutputFormat.YAML,
        )

        assert output.template.resource_namespace == "metal"
        assert "role: storage" in output.template.content
