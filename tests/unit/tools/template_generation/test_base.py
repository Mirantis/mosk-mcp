"""Tests for base template generator utilities.

Tests cover:
- DNS subdomain validation
- DNS label validation
- MAC address validation
- IP address validation
- CIDR notation validation
- VLAN ID validation
- MTU validation
- Diff generation
- Label building utilities
"""

import pytest

from mosk_mcp.core.exceptions import ValidationError
from mosk_mcp.tools.template_generation.base import (
    BaseTemplateGenerator,
    DiffOutput,
    OutputFormat,
    TemplateOutput,
)


class ConcreteGenerator(BaseTemplateGenerator):
    """Concrete implementation for testing abstract base class."""

    def generate(self, **kwargs):
        """Required implementation for abstract method."""
        raise NotImplementedError("Test implementation")


class TestDNSSubdomainValidation:
    """Tests for DNS subdomain validation."""

    @pytest.fixture
    def generator(self) -> ConcreteGenerator:
        """Create a generator instance."""
        return ConcreteGenerator()

    def test_valid_simple_subdomain(self, generator: ConcreteGenerator) -> None:
        """Test valid simple subdomain name."""
        generator.validate_dns_subdomain("example")

    def test_valid_subdomain_with_dots(self, generator: ConcreteGenerator) -> None:
        """Test valid subdomain with dots."""
        generator.validate_dns_subdomain("my.example.name")

    def test_valid_subdomain_with_hyphens(self, generator: ConcreteGenerator) -> None:
        """Test valid subdomain with hyphens."""
        generator.validate_dns_subdomain("my-example-name")

    def test_valid_subdomain_complex(self, generator: ConcreteGenerator) -> None:
        """Test valid complex subdomain."""
        generator.validate_dns_subdomain("my-example.test-name.local")

    def test_empty_subdomain_rejected(self, generator: ConcreteGenerator) -> None:
        """Test empty subdomain is rejected."""
        with pytest.raises(ValidationError, match="cannot be empty"):
            generator.validate_dns_subdomain("")

    def test_subdomain_too_long(self, generator: ConcreteGenerator) -> None:
        """Test subdomain exceeding 253 chars is rejected."""
        with pytest.raises(ValidationError, match="253"):
            generator.validate_dns_subdomain("a" * 254)

    def test_subdomain_uppercase_rejected(self, generator: ConcreteGenerator) -> None:
        """Test uppercase subdomain is rejected."""
        with pytest.raises(ValidationError, match="DNS subdomain"):
            generator.validate_dns_subdomain("MyExample")

    def test_subdomain_starts_with_dash_rejected(self, generator: ConcreteGenerator) -> None:
        """Test subdomain starting with dash is rejected."""
        with pytest.raises(ValidationError):
            generator.validate_dns_subdomain("-example")

    def test_subdomain_ends_with_dash_rejected(self, generator: ConcreteGenerator) -> None:
        """Test subdomain ending with dash is rejected."""
        with pytest.raises(ValidationError):
            generator.validate_dns_subdomain("example-")

    def test_subdomain_underscore_rejected(self, generator: ConcreteGenerator) -> None:
        """Test subdomain with underscore is rejected."""
        with pytest.raises(ValidationError):
            generator.validate_dns_subdomain("my_example")


class TestDNSLabelValidation:
    """Tests for DNS label validation."""

    @pytest.fixture
    def generator(self) -> ConcreteGenerator:
        """Create a generator instance."""
        return ConcreteGenerator()

    def test_valid_simple_label(self, generator: ConcreteGenerator) -> None:
        """Test valid simple label."""
        generator.validate_dns_label("compute01")

    def test_valid_label_with_hyphens(self, generator: ConcreteGenerator) -> None:
        """Test valid label with hyphens."""
        generator.validate_dns_label("compute-01-east")

    def test_valid_label_single_char(self, generator: ConcreteGenerator) -> None:
        """Test valid single character label."""
        generator.validate_dns_label("a")

    def test_valid_label_max_length(self, generator: ConcreteGenerator) -> None:
        """Test valid label at max length (63 chars)."""
        generator.validate_dns_label("a" * 63)

    def test_empty_label_rejected(self, generator: ConcreteGenerator) -> None:
        """Test empty label is rejected."""
        with pytest.raises(ValidationError, match="cannot be empty"):
            generator.validate_dns_label("")

    def test_label_too_long(self, generator: ConcreteGenerator) -> None:
        """Test label exceeding 63 chars is rejected."""
        with pytest.raises(ValidationError, match="63"):
            generator.validate_dns_label("a" * 64)

    def test_label_with_dot_rejected(self, generator: ConcreteGenerator) -> None:
        """Test label with dot is rejected (dots are for subdomains)."""
        with pytest.raises(ValidationError, match="DNS label"):
            generator.validate_dns_label("my.label")

    def test_label_uppercase_rejected(self, generator: ConcreteGenerator) -> None:
        """Test uppercase label is rejected."""
        with pytest.raises(ValidationError):
            generator.validate_dns_label("MyLabel")

    def test_label_starts_with_dash_rejected(self, generator: ConcreteGenerator) -> None:
        """Test label starting with dash is rejected."""
        with pytest.raises(ValidationError):
            generator.validate_dns_label("-compute")

    def test_label_ends_with_dash_rejected(self, generator: ConcreteGenerator) -> None:
        """Test label ending with dash is rejected."""
        with pytest.raises(ValidationError):
            generator.validate_dns_label("compute-")


class TestMACAddressValidation:
    """Tests for MAC address validation."""

    @pytest.fixture
    def generator(self) -> ConcreteGenerator:
        """Create a generator instance."""
        return ConcreteGenerator()

    def test_valid_lowercase_mac(self, generator: ConcreteGenerator) -> None:
        """Test valid lowercase MAC address."""
        generator.validate_mac_address("aa:bb:cc:dd:ee:ff")

    def test_valid_uppercase_mac(self, generator: ConcreteGenerator) -> None:
        """Test valid uppercase MAC address."""
        generator.validate_mac_address("AA:BB:CC:DD:EE:FF")

    def test_valid_mixed_case_mac(self, generator: ConcreteGenerator) -> None:
        """Test valid mixed case MAC address."""
        generator.validate_mac_address("Aa:Bb:Cc:Dd:Ee:Ff")

    def test_valid_numeric_mac(self, generator: ConcreteGenerator) -> None:
        """Test valid numeric MAC address."""
        generator.validate_mac_address("00:11:22:33:44:55")

    def test_invalid_mac_dashes(self, generator: ConcreteGenerator) -> None:
        """Test MAC with dashes instead of colons is rejected."""
        with pytest.raises(ValidationError, match="MAC"):
            generator.validate_mac_address("aa-bb-cc-dd-ee-ff")

    def test_invalid_mac_too_short(self, generator: ConcreteGenerator) -> None:
        """Test MAC with too few octets is rejected."""
        with pytest.raises(ValidationError):
            generator.validate_mac_address("aa:bb:cc:dd:ee")

    def test_invalid_mac_too_long(self, generator: ConcreteGenerator) -> None:
        """Test MAC with too many octets is rejected."""
        with pytest.raises(ValidationError):
            generator.validate_mac_address("aa:bb:cc:dd:ee:ff:gg")

    def test_invalid_mac_bad_chars(self, generator: ConcreteGenerator) -> None:
        """Test MAC with invalid characters is rejected."""
        with pytest.raises(ValidationError):
            generator.validate_mac_address("gg:hh:ii:jj:kk:ll")


class TestIPAddressValidation:
    """Tests for IP address validation."""

    @pytest.fixture
    def generator(self) -> ConcreteGenerator:
        """Create a generator instance."""
        return ConcreteGenerator()

    def test_valid_ip(self, generator: ConcreteGenerator) -> None:
        """Test valid IP addresses."""
        generator.validate_ip_address("192.168.1.100")
        generator.validate_ip_address("10.0.0.1")
        generator.validate_ip_address("172.16.0.1")

    def test_valid_ip_edge_values(self, generator: ConcreteGenerator) -> None:
        """Test valid edge case IP addresses."""
        generator.validate_ip_address("0.0.0.0")  # noqa: S104
        generator.validate_ip_address("255.255.255.255")

    def test_invalid_ip_octet_too_high(self, generator: ConcreteGenerator) -> None:
        """Test IP with octet > 255 is rejected."""
        with pytest.raises(ValidationError, match="IPv4"):
            generator.validate_ip_address("192.168.1.256")

    def test_invalid_ip_too_few_octets(self, generator: ConcreteGenerator) -> None:
        """Test IP with too few octets is rejected."""
        with pytest.raises(ValidationError):
            generator.validate_ip_address("192.168.1")

    def test_invalid_ip_hostname(self, generator: ConcreteGenerator) -> None:
        """Test hostname is rejected as IP."""
        with pytest.raises(ValidationError):
            generator.validate_ip_address("server.local")


class TestCIDRValidation:
    """Tests for CIDR notation validation."""

    @pytest.fixture
    def generator(self) -> ConcreteGenerator:
        """Create a generator instance."""
        return ConcreteGenerator()

    def test_valid_cidr(self, generator: ConcreteGenerator) -> None:
        """Test valid CIDR notations."""
        generator.validate_cidr("10.0.0.0/8")
        generator.validate_cidr("192.168.1.0/24")
        generator.validate_cidr("172.16.0.0/16")

    def test_valid_cidr_edge_masks(self, generator: ConcreteGenerator) -> None:
        """Test valid edge case CIDR masks."""
        generator.validate_cidr("0.0.0.0/0")
        generator.validate_cidr("192.168.1.1/32")

    def test_invalid_cidr_no_mask(self, generator: ConcreteGenerator) -> None:
        """Test CIDR without mask is rejected."""
        with pytest.raises(ValidationError, match="CIDR"):
            generator.validate_cidr("192.168.1.0")

    def test_invalid_cidr_mask_too_high(self, generator: ConcreteGenerator) -> None:
        """Test CIDR with mask > 32 is rejected."""
        with pytest.raises(ValidationError):
            generator.validate_cidr("192.168.1.0/33")

    def test_invalid_cidr_bad_ip(self, generator: ConcreteGenerator) -> None:
        """Test CIDR with invalid IP is rejected."""
        with pytest.raises(ValidationError):
            generator.validate_cidr("300.168.1.0/24")


class TestVLANValidation:
    """Tests for VLAN ID validation."""

    @pytest.fixture
    def generator(self) -> ConcreteGenerator:
        """Create a generator instance."""
        return ConcreteGenerator()

    def test_valid_vlan(self, generator: ConcreteGenerator) -> None:
        """Test valid VLAN IDs."""
        generator.validate_vlan_id(1)
        generator.validate_vlan_id(100)
        generator.validate_vlan_id(4094)

    def test_invalid_vlan_zero(self, generator: ConcreteGenerator) -> None:
        """Test VLAN 0 is rejected."""
        with pytest.raises(ValidationError, match="1 and 4094"):
            generator.validate_vlan_id(0)

    def test_invalid_vlan_too_high(self, generator: ConcreteGenerator) -> None:
        """Test VLAN > 4094 is rejected."""
        with pytest.raises(ValidationError):
            generator.validate_vlan_id(4095)


class TestMTUValidation:
    """Tests for MTU validation."""

    @pytest.fixture
    def generator(self) -> ConcreteGenerator:
        """Create a generator instance."""
        return ConcreteGenerator()

    def test_valid_mtu(self, generator: ConcreteGenerator) -> None:
        """Test valid MTU values."""
        generator.validate_mtu(68)  # minimum
        generator.validate_mtu(1500)  # standard ethernet
        generator.validate_mtu(9000)  # jumbo frames
        generator.validate_mtu(65535)  # maximum

    def test_invalid_mtu_too_low(self, generator: ConcreteGenerator) -> None:
        """Test MTU below 68 is rejected."""
        with pytest.raises(ValidationError, match="68 and 65535"):
            generator.validate_mtu(67)

    def test_invalid_mtu_too_high(self, generator: ConcreteGenerator) -> None:
        """Test MTU above 65535 is rejected."""
        with pytest.raises(ValidationError):
            generator.validate_mtu(65536)


class TestDiffGeneration:
    """Tests for diff generation utility."""

    def test_no_changes_diff(self) -> None:
        """Test diff with no changes."""
        content = "key: value\nother: data\n"
        diff = BaseTemplateGenerator.generate_diff(content, content)

        assert diff.has_changes is False
        assert diff.diff_text == "No changes"
        assert len(diff.changes_summary) == 0

    def test_added_line_diff(self) -> None:
        """Test diff with added line."""
        before = "key: value\n"
        after = "key: value\nnew: line\n"
        diff = BaseTemplateGenerator.generate_diff(before, after)

        assert diff.has_changes is True
        assert "+" in diff.diff_text
        assert any("Added" in s for s in diff.changes_summary)

    def test_removed_line_diff(self) -> None:
        """Test diff with removed line."""
        before = "key: value\nold: line\n"
        after = "key: value\n"
        diff = BaseTemplateGenerator.generate_diff(before, after)

        assert diff.has_changes is True
        assert "-" in diff.diff_text
        assert any("Removed" in s for s in diff.changes_summary)

    def test_dict_input_diff(self) -> None:
        """Test diff with dict inputs."""
        before = {"key": "value", "count": 1}
        after = {"key": "value", "count": 2}
        diff = BaseTemplateGenerator.generate_diff(before, after)

        assert diff.has_changes is True

    def test_long_content_truncated(self) -> None:
        """Test that long content is truncated in output."""
        before = "x" * 3000
        after = "y" * 3000
        diff = BaseTemplateGenerator.generate_diff(before, after)

        # before/after should be truncated to 2000 chars + "..."
        assert len(diff.before) <= 2003
        assert len(diff.after) <= 2003
        assert diff.before.endswith("...")
        assert diff.after.endswith("...")


class TestLabelBuilding:
    """Tests for label building utilities."""

    def test_standard_labels(self) -> None:
        """Test standard label building."""
        labels = BaseTemplateGenerator.build_standard_labels(
            cluster_name="mos",
            region="us-east-1",
        )

        assert labels["cluster.sigs.k8s.io/cluster-name"] == "mos"
        assert labels["kaas.mirantis.com/region"] == "us-east-1"
        assert labels["kaas.mirantis.com/provider"] == "baremetal"

    def test_standard_labels_with_additional(self) -> None:
        """Test standard labels with additional custom labels."""
        labels = BaseTemplateGenerator.build_standard_labels(
            cluster_name="mos",
            additional={"environment": "production", "team": "platform"},
        )

        assert labels["cluster.sigs.k8s.io/cluster-name"] == "mos"
        assert labels["environment"] == "production"
        assert labels["team"] == "platform"

    def test_machine_role_labels_compute(self) -> None:
        """Test machine role labels for compute."""
        labels = BaseTemplateGenerator.build_machine_role_labels(role="compute")

        assert labels["kaas.mirantis.com/provider"] == "baremetal"
        assert labels["hostlabel.bm.kaas.mirantis.com/worker"] == "worker"
        assert "controlplane" not in str(labels)

    def test_machine_role_labels_control(self) -> None:
        """Test machine role labels for control plane."""
        labels = BaseTemplateGenerator.build_machine_role_labels(role="control")

        assert labels["cluster.sigs.k8s.io/control-plane"] == "controlplane"
        assert labels["hostlabel.bm.kaas.mirantis.com/controlplane"] == "controlplane"
        assert labels["hostlabel.bm.kaas.mirantis.com/worker"] == "worker"

    def test_machine_role_labels_with_additional(self) -> None:
        """Test machine role labels with additional custom labels."""
        labels = BaseTemplateGenerator.build_machine_role_labels(
            role="storage",
            additional={"ceph-osd": "enabled"},
        )

        assert labels["hostlabel.bm.kaas.mirantis.com/worker"] == "worker"
        assert labels["ceph-osd"] == "enabled"


class TestOutputFormat:
    """Tests for OutputFormat enum."""

    def test_output_format_values(self) -> None:
        """Test OutputFormat enum values."""
        assert OutputFormat.YAML.value == "yaml"
        assert OutputFormat.JSON.value == "json"
        assert OutputFormat.KUBECTL.value == "kubectl"

    def test_output_format_from_string(self) -> None:
        """Test creating OutputFormat from string."""
        assert OutputFormat("yaml") == OutputFormat.YAML
        assert OutputFormat("json") == OutputFormat.JSON
        assert OutputFormat("kubectl") == OutputFormat.KUBECTL


class TestTemplateOutput:
    """Tests for TemplateOutput model."""

    def test_template_output_minimal(self) -> None:
        """Test minimal TemplateOutput."""
        output = TemplateOutput(
            format=OutputFormat.YAML,
            content="apiVersion: v1\nkind: ConfigMap",
            resource_kind="ConfigMap",
            resource_name="test",
        )

        assert output.format == OutputFormat.YAML
        assert output.warnings == []
        assert output.metadata == {}

    def test_template_output_full(self) -> None:
        """Test full TemplateOutput."""
        output = TemplateOutput(
            format=OutputFormat.JSON,
            content='{"apiVersion": "v1"}',
            resource_kind="Machine",
            resource_name="compute-01",
            resource_namespace="default",
            warnings=["Using default profile"],
            metadata={"generated_at": "2024-01-01"},
        )

        assert output.resource_namespace == "default"
        assert len(output.warnings) == 1
        assert "generated_at" in output.metadata


class TestDiffOutput:
    """Tests for DiffOutput model."""

    def test_diff_output(self) -> None:
        """Test DiffOutput model."""
        output = DiffOutput(
            has_changes=True,
            diff_text="--- before\n+++ after\n-old\n+new",
            before="old",
            after="new",
            changes_summary=["Added: new", "Removed: old"],
        )

        assert output.has_changes is True
        assert len(output.changes_summary) == 2
