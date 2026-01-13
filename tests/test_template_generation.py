"""Comprehensive tests for template generation tools.

This module tests all template generation tools in the MOSK MCP Server:
- generate_bmhi (BareMetalHostInventory)
- generate_bmhp (BareMetalHostProfile)
- generate_machine (Machine)
- generate_l2template (L2Template)
- generate_osdpl_patch (OSDPL JSON patch)
- validate_template (Template validation)
"""

import pytest
import yaml

from mosk_mcp.tools.template_generation import (
    OutputFormat,
    generate_bmhi,
    generate_bmhp,
    generate_l2template,
    generate_machine,
    generate_osdpl_patch,
    validate_template,
)
from mosk_mcp.tools.template_generation.base import BaseTemplateGenerator, DiffOutput
from mosk_mcp.tools.template_generation.node_templates import generate_node_templates


# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def sample_l2_config():
    """Sample L2 configuration for L2Template tests ."""
    return {
        "cluster_name": "mos",
        "if_mapping": ["enp9s0f0", "enp9s0f1"],
        "subnets": [
            {"name": "pxe"},
            {"name": "lcm"},
        ],
        "np_template": """version: 2
ethernets:
  {{nic 0}}:
    dhcp4: false
    match:
      macaddress: {{mac 0}}
    set-name: {{nic 0}}
  {{nic 1}}:
    dhcp4: false
    match:
      macaddress: {{mac 1}}
    set-name: {{nic 1}}
bonds:
  bond0:
    interfaces:
      - {{nic 0}}
      - {{nic 1}}
    parameters:
      mode: 802.3ad
bridges:
  k8s-pxe:
    interfaces:
      - bond0
    addresses:
      - {{ip "k8s-pxe:pxe"}}
""",
    }


@pytest.fixture
def sample_l2_topology_config():
    """Sample L2 configuration using high-level topology."""
    return {
        "cluster_name": "mos",
        "if_mapping": ["enp9s0f0", "enp9s0f1", "eno1"],
        "subnets": [
            {"name": "pxe"},
            {"name": "lcm"},
        ],
        "topology": {
            "nic_count": 3,
            "bonds": [
                {"name": "bond0", "nic_indices": [0, 1], "mode": "802.3ad"},
            ],
            "vlans": [
                {"name": "vlan1722", "id": 1722, "parent": "bond0"},
            ],
            "bridges": [
                {"name": "k8s-pxe", "interfaces": ["bond0"], "subnet": "pxe", "is_gateway": True},
                {"name": "k8s-lcm", "interfaces": ["vlan1722"], "subnet": "lcm"},
            ],
        },
    }


# =============================================================================
# Test Base Template Generator
# =============================================================================


class TestBaseTemplateGenerator:
    """Tests for BaseTemplateGenerator utilities."""

    def test_validate_dns_label_valid(self):
        """Test valid DNS label validation."""
        generator = _create_dummy_generator()
        # Should not raise
        generator.validate_dns_label("valid-name", "test")
        generator.validate_dns_label("name123", "test")
        generator.validate_dns_label("a", "test")

    def test_validate_dns_label_invalid(self):
        """Test invalid DNS label validation."""
        generator = _create_dummy_generator()
        from mosk_mcp.core.exceptions import ValidationError

        with pytest.raises(ValidationError):
            generator.validate_dns_label("Invalid-Name", "test")  # uppercase

        with pytest.raises(ValidationError):
            generator.validate_dns_label("-invalid", "test")  # starts with hyphen

        with pytest.raises(ValidationError):
            generator.validate_dns_label("", "test")  # empty

        with pytest.raises(ValidationError):
            generator.validate_dns_label("a" * 64, "test")  # too long

    def test_validate_mac_address_valid(self):
        """Test valid MAC address validation."""
        generator = _create_dummy_generator()
        generator.validate_mac_address("aa:bb:cc:dd:ee:ff", "test")
        generator.validate_mac_address("00:11:22:33:44:55", "test")
        generator.validate_mac_address("AA:BB:CC:DD:EE:FF", "test")

    def test_validate_mac_address_invalid(self):
        """Test invalid MAC address validation."""
        generator = _create_dummy_generator()
        from mosk_mcp.core.exceptions import ValidationError

        with pytest.raises(ValidationError):
            generator.validate_mac_address("invalid", "test")

        with pytest.raises(ValidationError):
            generator.validate_mac_address("aa:bb:cc:dd:ee", "test")  # too short

    def test_validate_ip_address_valid(self):
        """Test valid IP address validation."""
        generator = _create_dummy_generator()
        generator.validate_ip_address("192.168.1.1", "test")
        generator.validate_ip_address("10.0.0.1", "test")
        generator.validate_ip_address("255.255.255.255", "test")

    def test_validate_ip_address_invalid(self):
        """Test invalid IP address validation."""
        generator = _create_dummy_generator()
        from mosk_mcp.core.exceptions import ValidationError

        with pytest.raises(ValidationError):
            generator.validate_ip_address("invalid", "test")

        with pytest.raises(ValidationError):
            generator.validate_ip_address("256.1.1.1", "test")  # out of range

    def test_generate_diff_with_changes(self):
        """Test diff generation with changes."""
        before = {"key": "old_value"}
        after = {"key": "new_value"}

        diff = BaseTemplateGenerator.generate_diff(before, after)

        assert isinstance(diff, DiffOutput)
        assert diff.has_changes is True
        assert "old_value" in diff.diff_text
        assert "new_value" in diff.diff_text

    def test_generate_diff_no_changes(self):
        """Test diff generation without changes."""
        data = {"key": "value"}

        diff = BaseTemplateGenerator.generate_diff(data, data)

        assert diff.has_changes is False
        assert diff.diff_text == "No changes"

    def test_build_machine_role_labels(self):
        """Test machine metadata labels generation.

        Note: build_machine_role_labels returns METADATA labels, not nodeLabels.
        nodeLabels (openstack-compute-node, role, etc.) are in providerSpec.value.nodeLabels.
        """
        # Compute nodes get worker label
        labels = BaseTemplateGenerator.build_machine_role_labels("compute")
        assert labels["kaas.mirantis.com/provider"] == "baremetal"
        assert labels["hostlabel.bm.kaas.mirantis.com/worker"] == "worker"

        # Control nodes get control-plane labels
        labels = BaseTemplateGenerator.build_machine_role_labels("control")
        assert labels["kaas.mirantis.com/provider"] == "baremetal"
        assert labels["cluster.sigs.k8s.io/control-plane"] == "controlplane"
        assert labels["hostlabel.bm.kaas.mirantis.com/controlplane"] == "controlplane"
        assert labels["hostlabel.bm.kaas.mirantis.com/worker"] == "worker"

        # Storage nodes get worker label
        labels = BaseTemplateGenerator.build_machine_role_labels("storage")
        assert labels["kaas.mirantis.com/provider"] == "baremetal"
        assert labels["hostlabel.bm.kaas.mirantis.com/worker"] == "worker"


# =============================================================================
# Test generate_bmhi
# =============================================================================


class TestGenerateBMHI:
    """Tests for generate_bmhi tool."""

    @pytest.mark.asyncio
    async def test_generate_bmhi_basic(self):
        """Test basic BMHi generation."""
        output = await generate_bmhi(
            hostname="server-01",
            bmc_address="ipmi://192.168.1.100",
            bmc_credentials_secret="server-01-bmc-secret",
            boot_mac_address="aa:bb:cc:dd:ee:ff",
        )

        assert output.template.resource_kind == "BareMetalHostInventory"
        assert output.template.resource_name == "server-01"
        assert output.template.format == OutputFormat.YAML

        # Verify YAML content
        parsed = yaml.safe_load(output.template.content)
        assert parsed["apiVersion"] == "kaas.mirantis.com/v1alpha1"
        assert parsed["kind"] == "BareMetalHostInventory"
        assert parsed["spec"]["bootMACAddress"] == "aa:bb:cc:dd:ee:ff"

    @pytest.mark.asyncio
    async def test_generate_bmhi_json_format(self):
        """Test BMHi generation with JSON output."""
        output = await generate_bmhi(
            hostname="server-01",
            bmc_address="ipmi://192.168.1.100",
            bmc_credentials_secret="server-01-bmc-secret",
            boot_mac_address="aa:bb:cc:dd:ee:ff",
            output_format=OutputFormat.JSON,
        )

        assert output.template.format == OutputFormat.JSON
        import json

        parsed = json.loads(output.template.content)
        assert parsed["kind"] == "BareMetalHostInventory"

    @pytest.mark.asyncio
    async def test_generate_bmhi_kubectl_format(self):
        """Test BMHi generation with kubectl command output."""
        output = await generate_bmhi(
            hostname="server-01",
            bmc_address="ipmi://192.168.1.100",
            bmc_credentials_secret="server-01-bmc-secret",
            boot_mac_address="aa:bb:cc:dd:ee:ff",
            output_format=OutputFormat.KUBECTL,
        )

        assert output.template.format == OutputFormat.KUBECTL
        assert "kubectl apply -f -" in output.template.content

    @pytest.mark.asyncio
    async def test_generate_bmhi_with_labels(self):
        """Test BMHi generation with custom labels."""
        output = await generate_bmhi(
            hostname="server-01",
            bmc_address="ipmi://192.168.1.100",
            bmc_credentials_secret="server-01-bmc-secret",
            boot_mac_address="aa:bb:cc:dd:ee:ff",
            labels={"custom-label": "custom-value"},
        )

        parsed = yaml.safe_load(output.template.content)
        assert parsed["metadata"]["labels"]["custom-label"] == "custom-value"

    @pytest.mark.asyncio
    async def test_generate_bmhi_includes_secret_template(self):
        """Test that BMHi output includes Secret template."""
        output = await generate_bmhi(
            hostname="server-01",
            bmc_address="ipmi://192.168.1.100",
            bmc_credentials_secret="server-01-bmc-secret",
            boot_mac_address="aa:bb:cc:dd:ee:ff",
        )

        assert output.bmc_secret_template is not None
        assert "server-01-bmc-secret" in output.bmc_secret_template
        assert "<BMC_USERNAME>" in output.bmc_secret_template

    @pytest.mark.asyncio
    async def test_generate_bmhi_invalid_mac(self):
        """Test BMHi generation with invalid MAC address."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            await generate_bmhi(
                hostname="server-01",
                bmc_address="ipmi://192.168.1.100",
                bmc_credentials_secret="server-01-bmc-secret",
                boot_mac_address="invalid-mac",
            )


# =============================================================================
# Test generate_bmhp
# =============================================================================


class TestGenerateBMHP:
    """Tests for generate_bmhp tool."""

    @pytest.mark.asyncio
    async def test_generate_bmhp_basic(self):
        """Test basic BMHp generation."""
        output = await generate_bmhp(
            profile_name="compute-standard",
            cluster_name="mos",
            role="compute",
        )

        assert output.template.resource_kind == "BareMetalHostProfile"
        assert output.template.resource_name == "compute-standard"

        parsed = yaml.safe_load(output.template.content)
        assert parsed["apiVersion"] == "metal3.io/v1alpha1"
        assert parsed["kind"] == "BareMetalHostProfile"
        # Verify required labels
        assert parsed["metadata"]["labels"]["cluster.sigs.k8s.io/cluster-name"] == "mos"
        assert parsed["metadata"]["labels"]["kaas.mirantis.com/provider"] == "baremetal"

    @pytest.mark.asyncio
    async def test_generate_bmhp_with_kernel_params(self):
        """Test BMHp generation with kernel parameters."""
        output = await generate_bmhp(
            profile_name="compute-optimized",
            cluster_name="mos",
            role="compute",
            kernel_parameters=["hugepages=2048", "intel_iommu=on"],
        )

        parsed = yaml.safe_load(output.template.content)
        assert "hugepages=2048" in parsed["spec"].get("kernelParameters", [])

    @pytest.mark.asyncio
    async def test_generate_bmhp_with_root_hints(self):
        """Test BMHp generation with root device hints."""
        output = await generate_bmhp(
            profile_name="storage-profile",
            cluster_name="mos",
            role="storage",
            root_device_hints={"deviceType": "ssd", "minSizeGigabytes": 200},
        )

        parsed = yaml.safe_load(output.template.content)
        hardware_profile = parsed["spec"].get("hardwareProfile", {})
        assert hardware_profile is not None

    @pytest.mark.asyncio
    async def test_generate_bmhp_includes_recommendations(self):
        """Test that BMHp output includes role recommendations."""
        output = await generate_bmhp(
            profile_name="compute-standard",
            cluster_name="mos",
            role="compute",
        )

        assert "Compute Node Profile Recommendations" in output.role_recommendations

    @pytest.mark.asyncio
    async def test_generate_bmhp_warnings_for_missing_config(self):
        """Test BMHp generates warnings for missing recommended config."""
        output = await generate_bmhp(
            profile_name="generic-profile",
            cluster_name="mos",
            role="compute",  # Compute without kernel params
        )

        # Should have warning about missing kernel params
        assert len(output.warnings) > 0

    @pytest.mark.asyncio
    async def test_generate_bmhp_with_region(self):
        """Test BMHp generation with custom region."""
        output = await generate_bmhp(
            profile_name="compute-profile",
            cluster_name="mos",
            region="us-east-1",
            role="compute",
        )

        parsed = yaml.safe_load(output.template.content)
        assert parsed["metadata"]["labels"]["kaas.mirantis.com/region"] == "us-east-1"


# =============================================================================
# Test generate_machine
# =============================================================================


class TestGenerateMachine:
    """Tests for generate_machine tool.

    Note: Machine CRs have two types of labels:
    1. Metadata labels - cluster/provider info (kaas.mirantis.com/provider, etc.)
    2. nodeLabels - Kubernetes node labels in providerSpec.value.nodeLabels
       (openstack-compute-node, role, etc.)
    """

    @pytest.mark.asyncio
    async def test_generate_machine_compute(self):
        """Test Machine generation for compute role."""
        output = await generate_machine(
            name="compute-01",
            role="compute",
            bmhp_ref="compute-standard-profile",
        )

        assert output.template.resource_kind == "Machine"
        assert output.template.resource_name == "compute-01"

        parsed = yaml.safe_load(output.template.content)

        # Check metadata labels (cluster/provider info)
        labels = parsed["metadata"]["labels"]
        assert labels["kaas.mirantis.com/provider"] == "baremetal"
        assert labels["hostlabel.bm.kaas.mirantis.com/worker"] == "worker"
        assert labels["cluster.sigs.k8s.io/cluster-name"] == "mos"

        # Check nodeLabels in providerSpec (role-specific Kubernetes node labels)
        node_labels = parsed["spec"]["providerSpec"]["value"]["nodeLabels"]
        node_label_dict = {nl["key"]: nl["value"] for nl in node_labels}
        assert node_label_dict["openstack-compute-node"] == "enabled"
        assert node_label_dict["openvswitch"] == "enabled"

    @pytest.mark.asyncio
    async def test_generate_machine_control(self):
        """Test Machine generation for control role."""
        output = await generate_machine(
            name="control-01",
            role="control",
            bmhp_ref="control-profile",
        )

        parsed = yaml.safe_load(output.template.content)

        # Control plane metadata labels
        labels = parsed["metadata"]["labels"]
        assert labels["cluster.sigs.k8s.io/control-plane"] == "controlplane"
        assert labels["hostlabel.bm.kaas.mirantis.com/controlplane"] == "controlplane"

        # Control plane nodeLabels
        node_labels = parsed["spec"]["providerSpec"]["value"]["nodeLabels"]
        node_label_dict = {nl["key"]: nl["value"] for nl in node_labels}
        assert node_label_dict["openstack-control-plane"] == "enabled"

    @pytest.mark.asyncio
    async def test_generate_machine_storage(self):
        """Test Machine generation for storage role."""
        output = await generate_machine(
            name="storage-01",
            role="storage",
            bmhp_ref="storage-profile",
        )

        parsed = yaml.safe_load(output.template.content)

        # Storage metadata labels (just worker)
        labels = parsed["metadata"]["labels"]
        assert labels["kaas.mirantis.com/provider"] == "baremetal"
        assert labels["hostlabel.bm.kaas.mirantis.com/worker"] == "worker"

        # Storage nodeLabels
        node_labels = parsed["spec"]["providerSpec"]["value"]["nodeLabels"]
        node_label_dict = {nl["key"]: nl["value"] for nl in node_labels}
        assert node_label_dict["role"] == "ceph-osd-node"

    @pytest.mark.asyncio
    async def test_generate_machine_with_network_refs(self):
        """Test Machine generation with network references."""
        output = await generate_machine(
            name="compute-01",
            role="compute",
            bmhp_ref="compute-profile",
            l2_template_label="compute-l2",
        )

        parsed = yaml.safe_load(output.template.content)
        provider_spec = parsed["spec"]["providerSpec"]["value"]
        # Check hostSelector
        assert "hostSelector" in provider_spec
        assert (
            provider_spec["hostSelector"]["matchLabels"]["kaas.mirantis.com/baremetalhost-id"]
            == "compute-01"
        )
        # Check l2TemplateSelector
        assert "l2TemplateSelector" in provider_spec
        assert provider_spec["l2TemplateSelector"]["label"] == "compute-l2"

    @pytest.mark.asyncio
    async def test_generate_machine_includes_related_resources(self):
        """Test Machine output includes related resources list."""
        output = await generate_machine(
            name="compute-01",
            role="compute",
            bmhp_ref="compute-profile",
        )

        assert "BareMetalHostProfile/compute-profile" in output.related_resources
        assert "BareMetalHostInventory/compute-01" in output.related_resources

    @pytest.mark.asyncio
    async def test_generate_machine_with_custom_cluster(self):
        """Test Machine generation with custom cluster name and region."""
        output = await generate_machine(
            name="compute-01",
            role="compute",
            bmhp_ref="profile",
            cluster_name="prod-cluster",
            region="us-west-2",
        )

        parsed = yaml.safe_load(output.template.content)
        labels = parsed["metadata"]["labels"]
        assert labels["cluster.sigs.k8s.io/cluster-name"] == "prod-cluster"
        assert labels["kaas.mirantis.com/region"] == "us-west-2"


# =============================================================================
# Test generate_l2template
# =============================================================================


class TestGenerateL2Template:
    """Tests for generate_l2template tool ."""

    @pytest.mark.asyncio
    async def test_generate_l2template_basic(self, sample_l2_config):
        """Test basic L2Template generation with raw npTemplate."""
        output = await generate_l2template(
            name="compute-l2",
            **sample_l2_config,
        )

        assert output.template.resource_kind == "L2Template"
        assert output.template.resource_name == "compute-l2"

        # Verify YAML content
        parsed = yaml.safe_load(output.template.content)
        assert parsed["apiVersion"] == "ipam.mirantis.com/v1alpha1"
        assert parsed["kind"] == "L2Template"
        # Required cluster label
        assert "cluster.sigs.k8s.io/cluster-name" in parsed["metadata"]["labels"]

    @pytest.mark.asyncio
    async def test_generate_l2template_with_topology(self, sample_l2_topology_config):
        """Test L2Template generation with high-level topology."""
        output = await generate_l2template(
            name="compute-l2",
            **sample_l2_topology_config,
        )

        assert output.template.resource_kind == "L2Template"
        assert output.np_template_preview  # Should have generated npTemplate
        assert "bond0" in output.np_template_preview
        # VLAN may be truncated in preview (500 chars), check full content
        parsed = yaml.safe_load(output.template.content)
        assert "vlan1722" in parsed["spec"]["npTemplate"]

    @pytest.mark.asyncio
    async def test_generate_l2template_subnet_refs(self, sample_l2_config):
        """Test L2Template includes subnet references."""
        output = await generate_l2template(
            name="compute-l2",
            **sample_l2_config,
        )

        assert "pxe" in output.subnet_refs
        assert "lcm" in output.subnet_refs

    @pytest.mark.asyncio
    async def test_generate_l2template_warnings_for_undefined_bridge_interface(self):
        """Test L2Template generates warnings for undefined bridge interfaces."""
        output = await generate_l2template(
            name="bad-template",
            cluster_name="mos",
            if_mapping=["eth0", "eth1"],
            subnets=[{"name": "pxe"}],
            topology={
                "nic_count": 2,
                "bonds": [],
                "vlans": [],
                "bridges": [
                    {
                        "name": "k8s-pxe",
                        "interfaces": ["undefined-iface"],  # Not defined
                        "subnet": "pxe",
                    }
                ],
            },
        )

        # Should warn about undefined interface
        warning_found = any("undefined" in w.lower() for w in output.warnings)
        assert warning_found

    @pytest.mark.asyncio
    async def test_generate_l2template_warnings_for_missing_subnet(self):
        """Test L2Template warns about missing subnet in topology."""
        output = await generate_l2template(
            name="missing-subnet",
            cluster_name="mos",
            if_mapping=["eth0"],
            subnets=[{"name": "pxe"}],  # Only pxe, no lcm
            topology={
                "nic_count": 1,
                "bonds": [],
                "vlans": [],
                "bridges": [
                    {
                        "name": "k8s-lcm",
                        "interfaces": ["eth0"],  # Need at least one interface
                        "subnet": "lcm",  # Not in subnets list
                    }
                ],
            },
        )

        warning_found = any("lcm" in w and "subnet" in w.lower() for w in output.warnings)
        assert warning_found

    @pytest.mark.asyncio
    async def test_generate_l2template_auto_if_mapping(self):
        """Test L2Template with auto interface mapping."""
        output = await generate_l2template(
            name="auto-iface-template",
            cluster_name="mos",
            auto_if_mapping_prio=["eno", "ens", "enp"],  # Auto-discover by prefix
            subnets=[{"name": "pxe"}],
            np_template="version: 2\nethernets:\n  {{nic 0}}:\n    dhcp4: false\n",
        )

        assert output.template.resource_kind == "L2Template"
        # Verify auto_if_mapping_prio in spec
        parsed = yaml.safe_load(output.template.content)
        assert parsed["spec"]["autoIfMappingPrio"] == ["eno", "ens", "enp"]


# =============================================================================
# Test generate_osdpl_patch
# =============================================================================


class TestGenerateOSDPLPatch:
    """Tests for generate_osdpl_patch tool."""

    @pytest.mark.asyncio
    async def test_generate_osdpl_patch_basic(self):
        """Test basic OSDPL patch generation."""
        output = await generate_osdpl_patch(
            changes=[
                {
                    "path": "spec.services.nova.replicas",
                    "value": 5,
                    "description": "Scale Nova to 5 replicas",
                }
            ],
        )

        assert len(output.patch) > 0
        assert output.patch[0]["path"] == "/spec/services/nova/replicas"
        assert output.patch[0]["value"] == 5

    @pytest.mark.asyncio
    async def test_generate_osdpl_patch_command(self):
        """Test OSDPL patch generates kubectl command."""
        output = await generate_osdpl_patch(
            changes=[{"path": "spec.services.nova.replicas", "value": 5}],
            osdpl_name="openstack",
            namespace="openstack",
        )

        assert "kubectl patch osdpl openstack" in output.patch_command
        assert "-n openstack" in output.patch_command

    @pytest.mark.asyncio
    async def test_generate_osdpl_patch_diff(self):
        """Test OSDPL patch with diff preview."""
        current = {"spec": {"services": {"nova": {"replicas": 3}}}}

        output = await generate_osdpl_patch(
            changes=[{"path": "spec.services.nova.replicas", "value": 5}],
            current_osdpl=current,
            show_diff=True,
        )

        assert output.diff is not None
        assert output.diff.has_changes is True

    @pytest.mark.asyncio
    async def test_generate_osdpl_patch_warnings_for_critical(self):
        """Test OSDPL patch warns about critical changes."""
        output = await generate_osdpl_patch(
            changes=[{"path": "spec.openStackVersion", "value": "zed"}],
        )

        warning_found = any("version" in w.lower() for w in output.warnings)
        assert warning_found

    @pytest.mark.asyncio
    async def test_generate_osdpl_patch_changes_summary(self):
        """Test OSDPL patch includes changes summary."""
        output = await generate_osdpl_patch(
            changes=[
                {
                    "path": "spec.services.nova.replicas",
                    "value": 5,
                    "description": "Scale Nova API",
                }
            ],
        )

        assert len(output.changes_summary) > 0
        assert "Scale Nova API" in output.changes_summary[0]


# =============================================================================
# Test validate_template
# =============================================================================


class TestValidateTemplate:
    """Tests for validate_template tool."""

    @pytest.mark.asyncio
    async def test_validate_template_valid(self):
        """Test validation of a valid template."""
        template = """
apiVersion: kaas.mirantis.com/v1alpha1
kind: Machine
metadata:
  name: compute-01
  labels:
    openstack-compute-node: enabled
spec:
  providerSpec:
    value:
      bareMetalHostProfile: compute-profile
"""
        output = await validate_template(template_yaml=template)

        assert output.valid is True
        assert output.resource_kind == "Machine"
        assert output.resource_name == "compute-01"

    @pytest.mark.asyncio
    async def test_validate_template_invalid_yaml(self):
        """Test validation with invalid YAML."""
        template = "invalid: yaml: content:"

        output = await validate_template(template_yaml=template)

        assert output.valid is False
        error_found = any(i.severity == "error" for i in output.issues)
        assert error_found

    @pytest.mark.asyncio
    async def test_validate_template_missing_required_fields(self):
        """Test validation catches missing required fields."""
        template = """
apiVersion: kaas.mirantis.com/v1alpha1
# Missing kind
metadata:
  name: test
"""
        output = await validate_template(template_yaml=template)

        assert output.valid is False
        error_found = any(i.severity == "error" and "kind" in i.path for i in output.issues)
        assert error_found

    @pytest.mark.asyncio
    async def test_validate_template_invalid_name(self):
        """Test validation catches invalid resource names."""
        template = """
apiVersion: kaas.mirantis.com/v1alpha1
kind: Machine
metadata:
  name: Invalid-Name
spec: {}
"""
        output = await validate_template(template_yaml=template)

        assert output.valid is False
        error_found = any(i.severity == "error" and "name" in i.path for i in output.issues)
        assert error_found

    @pytest.mark.asyncio
    async def test_validate_template_cluster_conflicts(self):
        """Test validation with cluster conflict checking."""
        template = """
apiVersion: kaas.mirantis.com/v1alpha1
kind: Machine
metadata:
  name: compute-01
spec:
  providerSpec:
    value:
      bareMetalHostProfile: profile
"""
        output = await validate_template(
            template_yaml=template,
            check_cluster_conflicts=True,
            existing_resources=["Machine/compute-01"],
        )

        assert output.valid is False
        conflict_found = any("already exists" in i.message for i in output.issues)
        assert conflict_found

    @pytest.mark.asyncio
    async def test_validate_template_strict_mode(self):
        """Test validation strict mode (warnings become errors)."""
        template = """
apiVersion: kaas.mirantis.com/v1alpha1
kind: Machine
metadata:
  name: compute-01
spec: {}
"""
        # Normal mode - warnings don't fail
        output_normal = await validate_template(template_yaml=template, strict_mode=False)

        # Strict mode - warnings become errors
        output_strict = await validate_template(template_yaml=template, strict_mode=True)

        # strict_mode should make it invalid if there are warnings
        if any(i.severity == "warning" for i in output_normal.issues):
            assert output_strict.valid is False

    @pytest.mark.asyncio
    async def test_validate_template_bmhi_specific(self):
        """Test BMHi-specific validation."""
        template = """
apiVersion: kaas.mirantis.com/v1alpha1
kind: BareMetalHostInventory
metadata:
  name: server-01
spec:
  bootMACAddress: invalid-mac
  bmc:
    address: ipmi://192.168.1.1
"""
        output = await validate_template(template_yaml=template)

        assert output.valid is False
        mac_error = any("MAC" in i.message for i in output.issues)
        assert mac_error


# =============================================================================
# Test generate_node_templates
# =============================================================================


class TestGenerateNodeTemplates:
    """Tests for generate_node_templates tool (combined Secret/BMHi/Machine)."""

    @pytest.mark.asyncio
    async def test_generate_node_templates_basic(self):
        """Test basic node templates generation."""
        output = await generate_node_templates(
            node_name="compute-04",
            role="compute",
            namespace="default",
            cluster_name="mos",
            bmhp_name="compute-profile",
            l2_template_label="compute-l2",
            bmc_address="192.168.1.100",
            boot_mac_address="aa:bb:cc:dd:ee:ff",
        )

        # Check output structure
        assert "Secret" in output.resources_included
        assert "BareMetalHostInventory" in output.resources_included
        assert "Machine" in output.resources_included
        assert len(output.apply_order) == 3

        # Verify templates contain all three resources
        assert "kind: Secret" in output.templates
        assert "kind: BareMetalHostInventory" in output.templates
        assert "kind: Machine" in output.templates

    @pytest.mark.asyncio
    async def test_generate_node_templates_placeholders(self):
        """Test node templates with placeholders for missing values."""
        output = await generate_node_templates(
            mode="sample",  # Use sample mode to generate templates with placeholders
            role="compute",
            # No node_name, cluster_name, etc. - should use placeholders
        )

        # Should have placeholders to replace
        assert len(output.placeholders_to_replace) > 0
        # Template should contain placeholder markers
        assert "<" in output.templates  # Placeholders like <BMC_USERNAME>

    @pytest.mark.asyncio
    async def test_generate_node_templates_compute_role(self):
        """Test node templates for compute role."""
        output = await generate_node_templates(
            node_name="compute-01",
            role="compute",
            namespace="default",
            cluster_name="mos",
            bmhp_name="profile",
            l2_template_label="l2",
            bmc_address="192.168.1.1",
            boot_mac_address="aa:bb:cc:dd:ee:ff",
        )

        assert "Compute" in output.role_info or "compute" in output.role_info.lower()

        # Parse the Machine part
        docs = list(yaml.safe_load_all(output.templates))
        machine = next((d for d in docs if d and d.get("kind") == "Machine"), None)
        assert machine is not None

        # Check nodeLabels for compute
        node_labels = machine["spec"]["providerSpec"]["value"]["nodeLabels"]
        node_label_dict = {nl["key"]: nl["value"] for nl in node_labels}
        assert node_label_dict.get("openstack-compute-node") == "enabled"

    @pytest.mark.asyncio
    async def test_generate_node_templates_storage_role(self):
        """Test node templates for storage role."""
        output = await generate_node_templates(
            node_name="storage-01",
            role="storage",
            namespace="default",
            cluster_name="mos",
            bmhp_name="profile",
            l2_template_label="l2",
            bmc_address="192.168.1.1",
            boot_mac_address="aa:bb:cc:dd:ee:ff",
            rack_id="rack1",  # rack_id is mandatory for storage role
        )

        # Parse the Machine part
        docs = list(yaml.safe_load_all(output.templates))
        machine = next((d for d in docs if d and d.get("kind") == "Machine"), None)
        assert machine is not None

        # Check nodeLabels for storage
        node_labels = machine["spec"]["providerSpec"]["value"]["nodeLabels"]
        node_label_dict = {nl["key"]: nl["value"] for nl in node_labels}
        assert node_label_dict.get("role") == "ceph-osd-node"

    @pytest.mark.asyncio
    async def test_generate_node_templates_generic_role(self):
        """Test node templates for generic role (default)."""
        output = await generate_node_templates(
            node_name="node-01",
            role="generic",
            cluster_name="mos",
            bmhp_name="profile",
            bmc_address="192.168.1.1",
            boot_mac_address="aa:bb:cc:dd:ee:ff",
        )

        # Generic role should mention it's for customization
        assert "generic" in output.role_info.lower() or "custom" in output.role_info.lower()

    @pytest.mark.asyncio
    async def test_generate_node_templates_invalid_label_case(self):
        """Test that invalid label keys (wrong case) are rejected."""
        output = await generate_node_templates(
            node_name="compute-01",
            role="compute",
            namespace="default",
            cluster_name="mos",
            bmhp_name="profile",
            bmc_address="192.168.1.1",
            boot_mac_address="aa:bb:cc:dd:ee:ff",
            additional_node_labels=[{"key": "Node-type", "value": "sriov"}],  # Wrong case
        )

        assert output.status == "validation_error"
        assert "Node-type" in output.message
        assert "node-type" in output.message  # Should suggest correct key
        assert "case-sensitive" in output.message.lower()

    @pytest.mark.asyncio
    async def test_generate_node_templates_invalid_label_unknown(self):
        """Test that unknown label keys are rejected."""
        output = await generate_node_templates(
            node_name="compute-01",
            role="compute",
            namespace="default",
            cluster_name="mos",
            bmhp_name="profile",
            bmc_address="192.168.1.1",
            boot_mac_address="aa:bb:cc:dd:ee:ff",
            additional_node_labels=[{"key": "unknown-label", "value": "test"}],
        )

        assert output.status == "validation_error"
        assert "unknown-label" in output.message
        assert "Supported" in output.message

    @pytest.mark.asyncio
    async def test_generate_node_templates_invalid_label_value(self):
        """Test that invalid label values are rejected."""
        output = await generate_node_templates(
            node_name="compute-01",
            role="compute",
            namespace="default",
            cluster_name="mos",
            bmhp_name="profile",
            bmc_address="192.168.1.1",
            boot_mac_address="aa:bb:cc:dd:ee:ff",
            additional_node_labels=[
                {"key": "stacklight", "value": "disabled"}
            ],  # Should be "enabled"
        )

        assert output.status == "validation_error"
        assert "disabled" in output.message
        assert "enabled" in output.message

    @pytest.mark.asyncio
    async def test_generate_node_templates_valid_custom_label(self):
        """Test that valid custom labels are accepted."""
        output = await generate_node_templates(
            node_name="compute-01",
            role="compute",
            namespace="default",
            cluster_name="mos",
            bmhp_name="profile",
            bmc_address="192.168.1.1",
            boot_mac_address="aa:bb:cc:dd:ee:ff",
            additional_node_labels=[{"key": "node-type", "value": "sriov"}],
        )

        assert output.status == "success"
        assert "node-type" in output.templates
        assert "sriov" in output.templates


# =============================================================================
# Helper Functions
# =============================================================================


def _create_dummy_generator():
    """Create a dummy generator for testing base class methods."""
    from typing import Any

    from mosk_mcp.tools.template_generation.base import BaseTemplateGenerator

    class DummyGenerator(BaseTemplateGenerator):
        def generate(self, **kwargs: Any):
            pass

    return DummyGenerator()
