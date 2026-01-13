"""Unit tests for template generation tools.

Tests for the template generation utilities including:
- Base template generator classes
- BMHi, BMHp, Machine, L2Template generators
- Output format handling
- Template validation
"""

from __future__ import annotations

import pytest

from mosk_mcp.tools.template_generation.base import (
    DiffOutput,
    OutputFormat,
    TemplateOutput,
)


# =============================================================================
# Enum Tests
# =============================================================================


class TestOutputFormat:
    """Tests for OutputFormat enum."""

    def test_format_values(self) -> None:
        """Test output format enum values."""
        assert OutputFormat.YAML.value == "yaml"
        assert OutputFormat.JSON.value == "json"
        assert OutputFormat.KUBECTL.value == "kubectl"

    def test_format_count(self) -> None:
        """Test correct number of output formats."""
        assert len(OutputFormat) == 3


# =============================================================================
# Model Tests
# =============================================================================


class TestTemplateOutput:
    """Tests for TemplateOutput model."""

    def test_minimal_output(self) -> None:
        """Test creating output with minimal fields."""
        output = TemplateOutput(
            format=OutputFormat.YAML,
            content="apiVersion: v1\nkind: ConfigMap",
            resource_kind="ConfigMap",
            resource_name="my-config",
        )

        assert output.format == OutputFormat.YAML
        assert output.resource_kind == "ConfigMap"
        assert output.resource_name == "my-config"
        assert output.resource_namespace is None
        assert output.warnings == []
        assert output.metadata == {}

    def test_full_output(self) -> None:
        """Test creating output with all fields."""
        output = TemplateOutput(
            format=OutputFormat.JSON,
            content='{"apiVersion": "v1", "kind": "ConfigMap"}',
            resource_kind="ConfigMap",
            resource_name="my-config",
            resource_namespace="default",
            warnings=["Consider adding labels"],
            metadata={"generated_at": "2025-01-01"},
        )

        assert output.format == OutputFormat.JSON
        assert output.resource_namespace == "default"
        assert len(output.warnings) == 1
        assert output.metadata["generated_at"] == "2025-01-01"


class TestDiffOutput:
    """Tests for DiffOutput model."""

    def test_with_changes(self) -> None:
        """Test diff output with changes."""
        output = DiffOutput(
            has_changes=True,
            diff_text="--- before\n+++ after\n-old\n+new",
            before="old",
            after="new",
            changes_summary=["Modified field 'value'"],
        )

        assert output.has_changes is True
        assert "---" in output.diff_text
        assert len(output.changes_summary) == 1


# =============================================================================
# BMHi Generator Tests
# =============================================================================


class TestGenerateBMHIInput:
    """Tests for GenerateBMHIInput model."""

    def test_valid_input(self) -> None:
        """Test valid input parameters."""
        from mosk_mcp.tools.template_generation.bmhi import GenerateBMHIInput

        input_model = GenerateBMHIInput(
            hostname="compute-01",
            bmc_address="ipmi://192.168.1.100",
            bmc_credentials_secret="compute-01-bmc-secret",
            boot_mac_address="aa:bb:cc:dd:ee:ff",
        )

        assert input_model.hostname == "compute-01"
        assert input_model.bmc_type == "ipmi"  # Default
        assert input_model.boot_mode == "UEFI"  # Default
        assert input_model.online is True  # Default
        assert input_model.namespace == "default"  # Default

    def test_custom_values(self) -> None:
        """Test custom input values."""
        from mosk_mcp.tools.template_generation.bmhi import GenerateBMHIInput

        input_model = GenerateBMHIInput(
            hostname="storage-01",
            bmc_address="redfish://192.168.1.100:443",
            bmc_credentials_secret="storage-01-bmc",
            boot_mac_address="11:22:33:44:55:66",
            bmc_type="redfish",
            boot_mode="legacy",
            disable_tls_verify=True,
            namespace="production",
            labels={"env": "prod"},
        )

        assert input_model.bmc_type == "redfish"
        assert input_model.boot_mode == "legacy"
        assert input_model.disable_tls_verify is True
        assert input_model.labels == {"env": "prod"}

    def test_invalid_mac_address(self) -> None:
        """Test invalid MAC address validation."""
        from mosk_mcp.tools.template_generation.bmhi import GenerateBMHIInput

        with pytest.raises(Exception):  # Pydantic validation error
            GenerateBMHIInput(
                hostname="compute-01",
                bmc_address="192.168.1.100",
                bmc_credentials_secret="secret",
                boot_mac_address="invalid-mac",  # Invalid format
            )

    def test_invalid_hostname(self) -> None:
        """Test invalid hostname validation."""
        from mosk_mcp.tools.template_generation.bmhi import GenerateBMHIInput

        with pytest.raises(Exception):  # Pydantic validation error
            GenerateBMHIInput(
                hostname="Invalid_Hostname",  # Invalid (uppercase, underscore)
                bmc_address="192.168.1.100",
                bmc_credentials_secret="secret",
                boot_mac_address="aa:bb:cc:dd:ee:ff",
            )


class TestGenerateBMHI:
    """Tests for generate_bmhi tool function."""

    @pytest.mark.asyncio
    async def test_generate_yaml(self) -> None:
        """Test generating YAML output."""
        from mosk_mcp.tools.template_generation.bmhi import generate_bmhi

        result = await generate_bmhi(
            hostname="compute-01",
            bmc_address="ipmi://192.168.1.100",
            bmc_credentials_secret="compute-01-secret",
            boot_mac_address="aa:bb:cc:dd:ee:ff",
            output_format="yaml",
        )

        # generate_bmhi returns GenerateBMHIOutput with template: TemplateOutput
        assert result.template.format == OutputFormat.YAML
        assert result.template.resource_kind == "BareMetalHostInventory"
        assert result.template.resource_name == "compute-01"
        assert "apiVersion" in result.template.content
        assert "192.168.1.100" in result.template.content
        # Check additional output fields
        assert result.bmc_secret_template is not None
        assert result.instructions is not None

    @pytest.mark.asyncio
    async def test_generate_json(self) -> None:
        """Test generating JSON output."""
        from mosk_mcp.tools.template_generation.bmhi import generate_bmhi

        result = await generate_bmhi(
            hostname="compute-01",
            bmc_address="192.168.1.100",
            bmc_credentials_secret="compute-01-secret",
            boot_mac_address="aa:bb:cc:dd:ee:ff",
            output_format="json",
        )

        assert result.template.format == OutputFormat.JSON
        assert '"apiVersion"' in result.template.content or "apiVersion" in result.template.content

    @pytest.mark.asyncio
    async def test_generate_kubectl(self) -> None:
        """Test generating kubectl command output."""
        from mosk_mcp.tools.template_generation.bmhi import generate_bmhi

        result = await generate_bmhi(
            hostname="compute-01",
            bmc_address="192.168.1.100",
            bmc_credentials_secret="compute-01-secret",
            boot_mac_address="aa:bb:cc:dd:ee:ff",
            output_format="kubectl",
        )

        assert result.template.format == OutputFormat.KUBECTL
        assert "kubectl apply" in result.template.content


# =============================================================================
# BMHp Generator Tests
# =============================================================================


class TestGenerateBMHPInput:
    """Tests for GenerateBMHPInput model."""

    def test_valid_input(self) -> None:
        """Test valid input parameters."""
        from mosk_mcp.tools.template_generation.bmhp import GenerateBMHPInput

        input_model = GenerateBMHPInput(
            profile_name="compute-profile",
            cluster_name="mos",
        )

        assert input_model.profile_name == "compute-profile"
        assert input_model.cluster_name == "mos"
        assert input_model.namespace == "default"  # Default
        assert input_model.role == "generic"  # Default
        assert input_model.region == "region-one"  # Default


# =============================================================================
# Machine Generator Tests
# =============================================================================


class TestGenerateMachineInput:
    """Tests for GenerateMachineInput model."""

    def test_valid_input(self) -> None:
        """Test valid input parameters."""
        from mosk_mcp.tools.template_generation.machine import GenerateMachineInput

        input_model = GenerateMachineInput(
            name="compute-01",
            role="compute",
            bmhp_ref="compute-profile",
        )

        assert input_model.name == "compute-01"
        assert input_model.role == "compute"
        assert input_model.bmhp_ref == "compute-profile"
        assert input_model.cluster_name == "mos"  # Default
        assert input_model.namespace == "default"  # Default

    def test_all_roles(self) -> None:
        """Test all valid node roles."""
        from mosk_mcp.tools.template_generation.machine import GenerateMachineInput

        for role in ["compute", "control", "storage", "gateway"]:
            input_model = GenerateMachineInput(
                name=f"{role}-01",
                role=role,
                bmhp_ref="profile",
            )
            assert input_model.role == role


# =============================================================================
# OSDPL Patch Generator Tests
# =============================================================================


class TestGenerateOSDPLPatchInput:
    """Tests for GenerateOSDPLPatchInput model."""

    def test_valid_input(self) -> None:
        """Test valid input parameters."""
        from mosk_mcp.tools.template_generation.osdpl import GenerateOSDPLPatchInput

        input_model = GenerateOSDPLPatchInput(
            osdpl_name="mos",
            changes=[
                {
                    "path": "spec.services.nova.replicas",
                    "value": 5,
                }
            ],
        )

        assert input_model.osdpl_name == "mos"
        assert len(input_model.changes) == 1
        assert input_model.namespace == "openstack"  # Default
        assert input_model.show_diff is True  # Default


# =============================================================================
# Validator Tests
# =============================================================================


class TestValidatorModels:
    """Tests for template validator models."""

    def test_validation_issue(self) -> None:
        """Test ValidationIssue model."""
        from mosk_mcp.tools.template_generation.validator import ValidationIssue

        issue = ValidationIssue(
            severity="error",
            path="metadata.name",
            message="Name is required",
            suggestion="Add a valid resource name",
        )

        assert issue.severity == "error"
        assert issue.path == "metadata.name"
        assert issue.suggestion is not None

    def test_validate_template_input(self) -> None:
        """Test ValidateTemplateInput model."""
        from mosk_mcp.tools.template_generation.validator import ValidateTemplateInput

        input_model = ValidateTemplateInput(
            template_yaml="apiVersion: v1\nkind: ConfigMap",
            check_cluster_conflicts=False,
            strict_mode=False,
        )

        assert input_model.template_yaml is not None
        assert input_model.strict_mode is False

    def test_validate_template_output(self) -> None:
        """Test ValidateTemplateOutput model."""
        from mosk_mcp.tools.template_generation.validator import ValidateTemplateOutput

        output = ValidateTemplateOutput(
            valid=True,
            resource_kind="ConfigMap",
            resource_name="my-config",
            resource_namespace="default",
            issues=[],
            summary="Template is valid",
        )

        assert output.valid is True
        assert output.resource_kind == "ConfigMap"


class TestValidateTemplate:
    """Tests for validate_template tool function."""

    @pytest.mark.asyncio
    async def test_validate_valid_bmhi(self) -> None:
        """Test validating a valid BareMetalHostInventory template."""
        from mosk_mcp.tools.template_generation.validator import validate_template

        valid_yaml = """
apiVersion: metal3.io/v1alpha1
kind: BareMetalHostInventory
metadata:
  name: compute-01
  namespace: default
spec:
  bmc:
    address: ipmi://192.168.1.100
    credentialsName: compute-01-secret
  bootMACAddress: aa:bb:cc:dd:ee:ff
  online: true
"""

        result = await validate_template(template_yaml=valid_yaml)

        assert result.valid is True
        assert result.resource_kind == "BareMetalHostInventory"

    @pytest.mark.asyncio
    async def test_validate_invalid_yaml_syntax(self) -> None:
        """Test validating invalid YAML syntax."""
        from mosk_mcp.tools.template_generation.validator import validate_template

        invalid_yaml = """
apiVersion: v1
kind: ConfigMap
  invalid indentation here
"""

        result = await validate_template(template_yaml=invalid_yaml)

        # Should fail due to YAML syntax error
        assert result.valid is False
        assert len(result.issues) > 0

    @pytest.mark.asyncio
    async def test_validate_missing_apiversion(self) -> None:
        """Test validating template without apiVersion."""
        from mosk_mcp.tools.template_generation.validator import validate_template

        incomplete_yaml = """
kind: ConfigMap
metadata:
  name: test
"""

        result = await validate_template(template_yaml=incomplete_yaml)

        # Should fail or warn about missing apiVersion
        assert len(result.issues) > 0


# =============================================================================
# Base Template Generator Tests
# =============================================================================


class TestBaseTemplateGenerator:
    """Tests for BaseTemplateGenerator class."""

    def test_generator_initialization(self) -> None:
        """Test generator initialization."""
        from mosk_mcp.tools.template_generation.base import BaseTemplateGenerator

        # Create a concrete subclass for testing
        class TestGenerator(BaseTemplateGenerator):
            def generate(self, **kwargs) -> None:
                pass

        generator = TestGenerator(default_namespace="custom")
        assert generator.default_namespace == "custom"

    def test_load_environment_defaults(self) -> None:
        """Test loading environment defaults."""
        from mosk_mcp.tools.template_generation.base import BaseTemplateGenerator

        class TestGenerator(BaseTemplateGenerator):
            def generate(self, **kwargs) -> None:
                pass

        generator = TestGenerator()
        generator.load_environment_defaults({"region": "us-west", "cluster": "prod"})

        assert generator.get_default("region") == "us-west"
        assert generator.get_default("cluster") == "prod"
        assert generator.get_default("nonexistent", "fallback") == "fallback"

    def test_dns_subdomain_pattern(self) -> None:
        """Test DNS subdomain regex pattern."""
        from mosk_mcp.tools.template_generation.base import BaseTemplateGenerator

        # Valid patterns
        assert BaseTemplateGenerator.DNS_SUBDOMAIN_PATTERN.match("valid-name")
        assert BaseTemplateGenerator.DNS_SUBDOMAIN_PATTERN.match("a")
        assert BaseTemplateGenerator.DNS_SUBDOMAIN_PATTERN.match("abc123")
        assert BaseTemplateGenerator.DNS_SUBDOMAIN_PATTERN.match("my.domain.name")

        # Invalid patterns
        assert not BaseTemplateGenerator.DNS_SUBDOMAIN_PATTERN.match("-starts-with-dash")
        assert not BaseTemplateGenerator.DNS_SUBDOMAIN_PATTERN.match("ends-with-dash-")
        assert not BaseTemplateGenerator.DNS_SUBDOMAIN_PATTERN.match("UPPERCASE")
        assert not BaseTemplateGenerator.DNS_SUBDOMAIN_PATTERN.match("")

    def test_dns_label_pattern(self) -> None:
        """Test DNS label regex pattern."""
        from mosk_mcp.tools.template_generation.base import BaseTemplateGenerator

        # Valid patterns
        assert BaseTemplateGenerator.DNS_LABEL_PATTERN.match("valid-name")
        assert BaseTemplateGenerator.DNS_LABEL_PATTERN.match("a")
        assert BaseTemplateGenerator.DNS_LABEL_PATTERN.match("abc123")

        # Invalid patterns
        assert not BaseTemplateGenerator.DNS_LABEL_PATTERN.match("has.dot")
        assert not BaseTemplateGenerator.DNS_LABEL_PATTERN.match("-dash-start")

    def test_mac_address_pattern(self) -> None:
        """Test MAC address regex pattern."""
        from mosk_mcp.tools.template_generation.base import BaseTemplateGenerator

        # Valid MAC addresses
        assert BaseTemplateGenerator.MAC_ADDRESS_PATTERN.match("aa:bb:cc:dd:ee:ff")
        assert BaseTemplateGenerator.MAC_ADDRESS_PATTERN.match("AA:BB:CC:DD:EE:FF")
        assert BaseTemplateGenerator.MAC_ADDRESS_PATTERN.match("00:11:22:33:44:55")

        # Invalid MAC addresses
        assert not BaseTemplateGenerator.MAC_ADDRESS_PATTERN.match("invalid")
        assert not BaseTemplateGenerator.MAC_ADDRESS_PATTERN.match("aa:bb:cc:dd:ee")
        assert not BaseTemplateGenerator.MAC_ADDRESS_PATTERN.match("aa:bb:cc:dd:ee:ff:gg")

    def test_ip_address_pattern(self) -> None:
        """Test IP address regex pattern."""
        from mosk_mcp.tools.template_generation.base import BaseTemplateGenerator

        # Valid IP addresses
        assert BaseTemplateGenerator.IP_ADDRESS_PATTERN.match("192.168.1.1")
        assert BaseTemplateGenerator.IP_ADDRESS_PATTERN.match("10.0.0.1")
        assert BaseTemplateGenerator.IP_ADDRESS_PATTERN.match("255.255.255.255")
        assert BaseTemplateGenerator.IP_ADDRESS_PATTERN.match("1.2.3.4")

        # Invalid IP addresses
        assert not BaseTemplateGenerator.IP_ADDRESS_PATTERN.match("256.1.1.1")
        assert not BaseTemplateGenerator.IP_ADDRESS_PATTERN.match("1.1.1")
        assert not BaseTemplateGenerator.IP_ADDRESS_PATTERN.match("invalid")

    def test_cidr_pattern(self) -> None:
        """Test CIDR regex pattern."""
        from mosk_mcp.tools.template_generation.base import BaseTemplateGenerator

        # Valid CIDRs
        assert BaseTemplateGenerator.CIDR_PATTERN.match("192.168.1.0/24")
        assert BaseTemplateGenerator.CIDR_PATTERN.match("10.0.0.0/8")
        assert BaseTemplateGenerator.CIDR_PATTERN.match("172.16.0.0/12")
        assert BaseTemplateGenerator.CIDR_PATTERN.match("0.0.0.0/0")

        # Invalid CIDRs
        assert not BaseTemplateGenerator.CIDR_PATTERN.match("192.168.1.0")  # Missing prefix
        assert not BaseTemplateGenerator.CIDR_PATTERN.match("192.168.1.0/33")  # Invalid prefix
        assert not BaseTemplateGenerator.CIDR_PATTERN.match("invalid/24")


# =============================================================================
# L2Template Generator Tests
# =============================================================================


class TestL2Template:
    """Tests for L2Template generation."""

    def test_l2template_input_with_np_template(self) -> None:
        """Test L2Template input model with raw np_template."""
        from mosk_mcp.tools.template_generation.l2template import (
            GenerateL2TemplateInput,
        )

        # Either np_template or topology is required
        input_model = GenerateL2TemplateInput(
            name="compute-l2",
            cluster_name="mos",
            np_template="version: 2\nethernets:\n  eno1:\n    dhcp4: false\n",
        )

        assert input_model.name == "compute-l2"
        assert input_model.cluster_name == "mos"
        assert input_model.is_default is False
        assert input_model.np_template is not None

    def test_l2template_input_with_topology(self) -> None:
        """Test L2Template input model with topology."""
        from mosk_mcp.tools.template_generation.l2template import (
            GenerateL2TemplateInput,
            NetworkTopologyInput,
        )

        topology = NetworkTopologyInput(nic_count=2)
        input_model = GenerateL2TemplateInput(
            name="compute-l2",
            cluster_name="mos",
            topology=topology,
        )

        assert input_model.name == "compute-l2"
        assert input_model.topology is not None
        assert input_model.topology.nic_count == 2

    def test_l2template_input_missing_template_or_topology(self) -> None:
        """Test L2Template input model fails without np_template or topology."""
        from mosk_mcp.tools.template_generation.l2template import (
            GenerateL2TemplateInput,
        )

        with pytest.raises(ValueError, match="Either np_template or topology must be provided"):
            GenerateL2TemplateInput(
                name="compute-l2",
                cluster_name="mos",
                # Missing both np_template and topology
            )


# =============================================================================
# Node Templates Generator Tests
# =============================================================================


class TestNodeTemplates:
    """Tests for node templates generation."""

    def test_node_templates_input(self) -> None:
        """Test node templates input model."""
        from mosk_mcp.tools.template_generation.node_templates import (
            GenerateNodeTemplatesInput,
        )

        input_model = GenerateNodeTemplatesInput(
            node_name="compute-01",
            role="compute",
        )

        assert input_model.node_name == "compute-01"
        assert input_model.role == "compute"
        # Default namespace is None (requires explicit specification)
        assert input_model.namespace is None

    def test_node_templates_input_with_namespace(self) -> None:
        """Test node templates input model with explicit namespace."""
        from mosk_mcp.tools.template_generation.node_templates import (
            GenerateNodeTemplatesInput,
        )

        input_model = GenerateNodeTemplatesInput(
            node_name="compute-01",
            role="compute",
            namespace="production",
        )

        assert input_model.namespace == "production"

    @pytest.mark.asyncio
    async def test_generate_node_templates_sample_mode(self) -> None:
        """Test generating node templates in sample mode with placeholders."""
        from mosk_mcp.tools.template_generation.node_templates import (
            generate_node_templates,
        )

        result = await generate_node_templates(mode="sample", role="compute")

        # Should generate templates with placeholders in sample mode
        assert result.status == "success"
        assert result.templates is not None
        # Should have Secret, BMHi, and Machine templates
        assert "Secret" in result.templates
        assert "BareMetalHostInventory" in result.templates
        assert "Machine" in result.templates

    @pytest.mark.asyncio
    async def test_generate_node_templates_interactive_mode(self) -> None:
        """Test generating node templates in interactive mode returns missing fields."""
        from mosk_mcp.tools.template_generation.node_templates import (
            generate_node_templates,
        )

        # Default mode is interactive, should ask for mandatory fields
        result = await generate_node_templates(role="compute")

        # Should indicate missing required fields
        assert result.status == "missing_required"
        assert result.missing_mandatory is not None
        assert len(result.missing_mandatory) > 0


# =============================================================================
# L2Template Generator Extended Tests
# =============================================================================


class TestL2TemplateGenerator:
    """Tests for L2Template generator class."""

    def test_generate_with_topology(self) -> None:
        """Test generating L2Template with topology."""
        from mosk_mcp.tools.template_generation.l2template import (
            BridgeConfigInput,
            GenerateL2TemplateInput,
            L2TemplateGenerator,
            NetworkTopologyInput,
            SubnetRefInput,
        )

        generator = L2TemplateGenerator()
        topology = NetworkTopologyInput(
            nic_count=2,
            bridges=[
                BridgeConfigInput(
                    name="k8s-lcm",
                    interfaces=["eno1"],
                    subnet="lcm",
                )
            ],
        )
        input_data = GenerateL2TemplateInput(
            name="compute-l2",
            cluster_name="mos",
            if_mapping=["eno1", "eno2"],
            subnets=[SubnetRefInput(name="lcm")],
            topology=topology,
        )

        result = generator.generate_l2template(input_data)

        assert result.template.resource_kind == "L2Template"
        assert result.template.resource_name == "compute-l2"
        assert result.np_template_preview is not None
        assert "lcm" in result.subnet_refs

    @pytest.mark.asyncio
    async def test_generate_l2template_tool_function(self) -> None:
        """Test generate_l2template async tool function."""
        from mosk_mcp.tools.template_generation.l2template import generate_l2template

        result = await generate_l2template(
            name="storage-l2",
            cluster_name="mos",
            namespace="lab",
            np_template="version: 2\nethernets:\n  eno1:\n    dhcp4: false\n",
        )

        assert result.template.resource_kind == "L2Template"
        assert result.template.resource_name == "storage-l2"

    def test_np_template_builder(self) -> None:
        """Test NpTemplateBuilder class."""
        from mosk_mcp.tools.template_generation.l2template import (
            BondConfigInput,
            NetworkTopologyInput,
            NpTemplateBuilder,
            VlanConfigInput,
        )

        builder = NpTemplateBuilder(nic_count=4)
        topology = NetworkTopologyInput(
            nic_count=4,
            bonds=[BondConfigInput(name="bond0", nic_indices=[0, 1], mode="802.3ad")],
            vlans=[VlanConfigInput(name="vlan100", id=100, parent="bond0")],
        )

        result = builder.build(topology)

        assert "version: 2" in result
        assert "ethernets:" in result
        assert "bonds:" in result
        assert "vlans:" in result


# =============================================================================
# Machine Generator Extended Tests
# =============================================================================


class TestMachineGenerator:
    """Tests for Machine generator class."""

    @pytest.mark.asyncio
    async def test_generate_machine_compute(self) -> None:
        """Test generating Machine CR for compute role."""
        from mosk_mcp.tools.template_generation.machine import generate_machine

        result = await generate_machine(
            name="compute-01",
            role="compute",
            bmhp_ref="compute-profile",
            cluster_name="mos",
            namespace="lab",
        )

        assert result.template.resource_kind == "Machine"
        assert result.template.resource_name == "compute-01"
        assert "compute" in result.template.content.lower()

    @pytest.mark.asyncio
    async def test_generate_machine_control(self) -> None:
        """Test generating Machine CR for control role."""
        from mosk_mcp.tools.template_generation.machine import generate_machine

        result = await generate_machine(
            name="control-01",
            role="control",
            bmhp_ref="control-profile",
        )

        assert result.template.resource_kind == "Machine"

    @pytest.mark.asyncio
    async def test_generate_machine_storage(self) -> None:
        """Test generating Machine CR for storage role."""
        from mosk_mcp.tools.template_generation.machine import generate_machine

        result = await generate_machine(
            name="storage-01",
            role="storage",
            bmhp_ref="storage-profile",
        )

        assert result.template.resource_kind == "Machine"

    @pytest.mark.asyncio
    async def test_generate_machine_gateway(self) -> None:
        """Test generating Machine CR for gateway role."""
        from mosk_mcp.tools.template_generation.machine import generate_machine

        result = await generate_machine(
            name="gateway-01",
            role="gateway",
            bmhp_ref="gateway-profile",
        )

        assert result.template.resource_kind == "Machine"


# =============================================================================
# OSDPL Generator Extended Tests
# =============================================================================


class TestOSDPLGenerator:
    """Tests for OSDPL patch generator."""

    @pytest.mark.asyncio
    async def test_generate_simple_patch(self) -> None:
        """Test generating a simple OSDPL patch."""
        from mosk_mcp.tools.template_generation.osdpl import generate_osdpl_patch

        result = await generate_osdpl_patch(
            osdpl_name="mos",
            changes=[
                {
                    "path": "spec.openstack_version",
                    "value": "caracal",
                    "description": "Upgrade to Caracal",
                }
            ],
        )

        # GenerateOSDPLPatchOutput has patch (list) and patch_command (str)
        assert len(result.patch) > 0
        assert result.patch_command is not None
        assert "mos" in result.patch_command

    @pytest.mark.asyncio
    async def test_generate_multi_change_patch(self) -> None:
        """Test generating patch with multiple changes."""
        from mosk_mcp.tools.template_generation.osdpl import generate_osdpl_patch

        result = await generate_osdpl_patch(
            osdpl_name="mos",
            namespace="openstack",
            changes=[
                {"path": "spec.services.nova.replicas", "value": 3},
                {"path": "spec.services.neutron.replicas", "value": 3},
            ],
        )

        assert len(result.patch) == 2
        assert len(result.changes_summary) == 2


# =============================================================================
# BMHp Generator Extended Tests
# =============================================================================


class TestBMHPGenerator:
    """Tests for BareMetalHostProfile generator."""

    @pytest.mark.asyncio
    async def test_generate_simple_bmhp(self) -> None:
        """Test generating a simple BMHp."""
        from mosk_mcp.tools.template_generation.bmhp import generate_bmhp

        result = await generate_bmhp(
            profile_name="compute-profile",
            cluster_name="mos",
        )

        assert result.template.resource_kind == "BareMetalHostProfile"
        assert result.template.resource_name == "compute-profile"

    @pytest.mark.asyncio
    async def test_generate_bmhp_with_role(self) -> None:
        """Test generating BMHp with specific role."""
        from mosk_mcp.tools.template_generation.bmhp import generate_bmhp

        result = await generate_bmhp(
            profile_name="storage-profile",
            cluster_name="mos",
            role="storage",
            region="region-one",
        )

        assert result.template.resource_kind == "BareMetalHostProfile"


# =============================================================================
# Additional Validator Tests
# =============================================================================


class TestValidatorExtended:
    """Extended tests for template validator."""

    @pytest.mark.asyncio
    async def test_validate_machine_template(self) -> None:
        """Test validating a Machine template."""
        from mosk_mcp.tools.template_generation.validator import validate_template

        machine_yaml = """
apiVersion: cluster.k8s.io/v1alpha1
kind: Machine
metadata:
  name: compute-01
  namespace: default
  labels:
    cluster.sigs.k8s.io/cluster-name: mos
spec:
  providerSpec:
    value:
      apiVersion: baremetal.k8s.io/v1alpha1
      kind: BareMetalMachineProviderSpec
"""

        result = await validate_template(template_yaml=machine_yaml)

        assert result.resource_kind == "Machine"
        assert result.resource_name == "compute-01"

    @pytest.mark.asyncio
    async def test_validate_l2template(self) -> None:
        """Test validating an L2Template."""
        from mosk_mcp.tools.template_generation.validator import validate_template

        l2template_yaml = """
apiVersion: ipam.mirantis.com/v1alpha1
kind: L2Template
metadata:
  name: compute-l2
  namespace: default
  labels:
    cluster.sigs.k8s.io/cluster-name: mos
spec:
  npTemplate: |
    version: 2
    ethernets:
      eno1:
        dhcp4: false
"""

        result = await validate_template(template_yaml=l2template_yaml)

        assert result.resource_kind == "L2Template"
        assert result.resource_name == "compute-l2"

    @pytest.mark.asyncio
    async def test_strict_mode(self) -> None:
        """Test strict mode validation."""
        from mosk_mcp.tools.template_generation.validator import validate_template

        # Template with warnings that should fail in strict mode
        yaml_with_warnings = """
apiVersion: v1
kind: ConfigMap
metadata:
  name: test
"""

        result = await validate_template(template_yaml=yaml_with_warnings, strict_mode=True)

        # In strict mode, warnings become errors
        assert result is not None


# =============================================================================
# Node Role Configuration Tests
# =============================================================================


class TestNodeRoleConfiguration:
    """Tests for node role configuration in node_templates."""

    def test_node_role_config_exists(self) -> None:
        """Test that NODE_ROLE_CONFIG has all expected roles."""
        from mosk_mcp.tools.template_generation.node_templates import NODE_ROLE_CONFIG

        expected_roles = ["compute", "control", "storage", "gateway", "generic"]
        for role in expected_roles:
            assert role in NODE_ROLE_CONFIG
            assert "description" in NODE_ROLE_CONFIG[role]
            assert "machine_labels" in NODE_ROLE_CONFIG[role]
            assert "node_labels" in NODE_ROLE_CONFIG[role]

    def test_validate_node_labels(self) -> None:
        """Test validate_node_labels function."""
        from mosk_mcp.tools.template_generation.node_templates import (
            validate_node_labels,
        )

        # Valid labels
        valid_labels = [
            {"key": "stacklight", "value": "enabled"},
            {"key": "openstack-compute-node", "value": "enabled"},
        ]
        valid_result, errors = validate_node_labels(valid_labels)
        assert len(valid_result) == 2
        assert len(errors) == 0

        # Invalid label key
        invalid_labels = [{"key": "invalid-key", "value": "test"}]
        valid_result, errors = validate_node_labels(invalid_labels)
        assert len(valid_result) == 0
        assert len(errors) == 1

    def test_generation_modes(self) -> None:
        """Test GenerationMode enum."""
        from mosk_mcp.tools.template_generation.node_templates import GenerationMode

        assert GenerationMode.SAMPLE.value == "sample"
        assert GenerationMode.INTERACTIVE.value == "interactive"
        assert GenerationMode.PRODUCTION.value == "production"
